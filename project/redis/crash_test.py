#!/usr/bin/env python3
"""遺失視窗實驗：壓測途中 SIGKILL 一個不持久化的私有 Redis，計算少算的次數。

使用獨立 Redis（預設 port 6380，--save "" --appendonly no），不動課程的 6379。
在某次回寫完成後經過 phase×T 秒才中斷，讓不同 T 的中斷相位一致。
結果 append 到批次資料夾的 failures.csv（預設 results/<今天日期>/，D39；欄位見 results/failure_template.csv）。
設計見 docs/design/crash_test.md。
"""
import argparse
import array
import csv
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tools.terminal_progress import Spinner


def pg_sum(db):
    out = subprocess.run(["psql", "-X", "-At", "-d", db, "-c",
                          "SELECT sum(view_count) FROM products"],
                         check=True, capture_output=True, text=True).stdout
    return int(out.strip())


def read_events(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def ping(port):
    return subprocess.run(["redis-cli", "-p", str(port), "ping"],
                          capture_output=True, text=True).stdout.strip() == "PONG"


def start_private_redis(port, data_dir, log_path):
    """啟動私有 Redis，並確認回應的確實是這個子行程（避免連到既有服務，最後誤殺它）。"""
    if ping(port):
        sys.exit(f"port {port} 已有 Redis 在跑，拒絕執行；請用 --port 換一個")
    rs = subprocess.Popen(["redis-server", "--port", str(port), "--save", "",
                           "--appendonly", "no", "--dir", data_dir],
                          stdout=open(log_path, "a"))
    for _ in range(100):
        if rs.poll() is not None:
            sys.exit(f"私有 Redis 啟動失敗（exit={rs.returncode}），見 {log_path}")
        if ping(port):
            break
        time.sleep(0.05)
    info = subprocess.run(["redis-cli", "-p", str(port), "info", "server"],
                          capture_output=True, text=True).stdout
    pid = next((int(line.split(":")[1]) for line in info.splitlines()
                if line.startswith("process_id:")), None)
    if pid != rs.pid:
        rs.kill()
        sys.exit(f"port {port} 回應的 Redis（pid={pid}）不是本次啟動的（pid={rs.pid}），中止")
    return rs


def count_times(out, pattern, t_start, t, after):
    """負載器逐筆時刻檔（uint64 微秒，距 t_start）中，晚於（after）或不晚於 t 的筆數；沒有檔案回 None。"""
    cut = (t - t_start) * 1e6
    files = sorted(glob.glob(os.path.join(out, pattern)))
    if not files:
        return None
    n = 0
    for path in files:
        a = array.array("Q")
        with open(path, "rb") as f:
            a.frombytes(f.read())
        n += sum(1 for x in a if (x > cut) == after)
    return n


def wait_for_flush_after(path, t, timeout):
    """等到快照時間在 t 之後的一次成功回寫，回傳該事件。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in reversed(read_events(path)):
            if e["event"] == "flush" and e["snap_ts"] > t:
                return e
        time.sleep(0.02)
    raise TimeoutError("等不到回寫事件")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, required=True, help="回寫間隔 T（秒）")
    ap.add_argument("--repeat", type=int, default=1, help="第幾次重複（寫入 CSV）")
    ap.add_argument("--concurrency", type=int, default=200)
    ap.add_argument("--scenario", default="hot80")
    ap.add_argument("--kill-after", type=float, default=10,
                    help="負載開始後至少經過幾秒才準備中斷")
    ap.add_argument("--phase", type=float, default=0.8,
                    help="在某次回寫完成後經過 phase×T 秒中斷")
    ap.add_argument("--port", type=int, default=6380)
    ap.add_argument("--pg-db", default="q01")
    ap.add_argument("--procs", type=int, default=8)
    # D39：預設寫到 results/<今天日期>/；補跑舊批次時用 --batch 20261024（或分別指定 --out-root／--csv）
    ap.add_argument("--batch", default=time.strftime("%Y%m%d"), help="批次資料夾名稱（results/<batch>/），預設今天日期")
    ap.add_argument("--out-root", help="每次結果目錄的上層，預設 results/<batch>")
    ap.add_argument("--csv", help="append 的 CSV，預設 results/<batch>/failures.csv")
    args = ap.parse_args()
    args.out_root = args.out_root or os.path.join(ROOT, "results", args.batch)
    args.csv = args.csv or os.path.join(args.out_root, "failures.csv")

    run_id = (f"crash_{args.scenario}_c{args.concurrency}_T{args.interval:g}"
              f"_r{args.repeat}_{time.strftime('%Y%m%d%H%M%S')}")
    out = os.path.join(args.out_root, run_id)
    os.makedirs(out)
    flush_log = os.path.join(out, "flush.jsonl")
    rconn = ["--redis-port", str(args.port)]

    # 1. 私有、不持久化的 Redis
    redis_dir = os.path.join(out, "redis_data")
    os.makedirs(redis_dir)
    redis_log = os.path.join(out, "redis_server.log")
    rs = start_private_redis(args.port, redis_dir, redis_log)
    persistence = subprocess.run(
        ["redis-cli", "-p", str(args.port), "config", "get", "save"],
        capture_output=True, text=True).stdout.split("\n")[1:2]
    persistence = f"save='{persistence[0] if persistence else ''}', appendonly=no"

    # 2. 期初、初始化、啟動 worker 與負載器
    b0 = pg_sum(args.pg_db)
    py = sys.executable
    subprocess.run([py, os.path.join(HERE, "flush_worker.py"), "init", "--run-id", run_id,
                    "--pg-db", args.pg_db, *rconn], check=True, stdout=subprocess.DEVNULL)
    worker = subprocess.Popen([py, os.path.join(HERE, "flush_worker.py"), "run",
                               "--run-id", run_id, "--interval", str(args.interval),
                               "--log", flush_log, "--pg-db", args.pg_db, *rconn],
                              stdout=subprocess.DEVNULL)
    t_load = time.time()
    loader = subprocess.Popen([py, os.path.join(ROOT, "loader", "load.py"),
                               "--backend", "redis", "--scenario", args.scenario,
                               "--concurrency", str(args.concurrency), "--warmup", "0",
                               "--duration", str(args.kill_after + 10 * args.interval + 60),
                               "--procs", str(args.procs), "--stop-on-error",
                               "--timeout", "2", "--ack-log", "--run-id", run_id, "--out", out, *rconn],
                              stdout=subprocess.DEVNULL)

    # 3. 對齊相位後中斷：以某次回寫的「快照取得時間」為錨點
    with Spinner(f"故障實驗 T={args.interval:g} 秒：等待負載與中斷相位"):
        time.sleep(2 + args.concurrency / 200 + args.kill_after)  # 扣掉負載器建立連線的時間
        anchor = wait_for_flush_after(flush_log, time.time(), 3 * args.interval + 5)
        time.sleep(max(0.0, anchor["snap_ts"] + args.phase * args.interval - time.time()))
    crash_at = time.time()
    rs.kill()  # SIGKILL，模擬當機，不是正常 SHUTDOWN
    rs.wait()

    # 4. 收尾：負載器遇錯即停；worker 做最後回寫（Redis 已死，會失敗）後結束
    with Spinner("故障實驗：等待負載停止與回寫收尾"):
        loader.wait()
        worker.terminate()
        worker.wait()
    with open(os.path.join(out, "loader_summary.json")) as f:
        ls = json.load(f)
    # 有寫進 log 的 flush 都已 commit；P 涵蓋到快照時間最晚的那一批（它可能在中斷後才 commit）
    flushes = [e for e in read_events(flush_log) if e["event"] == "flush"]
    last = max(flushes, key=lambda e: e["snap_ts"], default=None)
    n = ls["successful_ops_total"]
    p = pg_sum(args.pg_db) - b0
    gap = n - p
    # 中斷時在途的請求（failed＋unknown）可能已執行並被回寫，算進 P 卻沒算進 N，
    # 所以「已確認卻遺失」的真值介於 gap 與 gap＋在途數之間（D13）。
    # 有逐筆紀錄時（D41）只加「在最後一批快照取得之前就送出」的在途請求：之後才送出的不可能在快照裡
    in_flight = ls["error_ops"] + ls["unknown_ops"]
    early = (count_times(out, "fail_sent_us.*.bin", ls["t_start"], last["snap_ts"], after=False)
             if last else None)
    gap_upper = gap + (in_flight if early is None else early)
    load_s = crash_at - ls["t_measure_start"]
    throughput = n / load_s if load_s > 0 else 0
    ref = throughput * args.interval  # 舊門檻（D34 前），只留作參考
    # 提案判準 3（D34、D41）：上界＝最後一批成功寫入 PG 的快照「取得之後」才確認成功的請求數。
    # snap_ts 在發出 MGET 前記錄，所以早於 snap_ts 確認的請求一定已在該批快照內。
    bound = count_times(out, "ack_us.*.bin", ls["t_start"], last["snap_ts"], after=True) if last else None
    if bound is None:
        verdict = "VOID(no ack log)" if last else "VOID(no flush)"
    else:
        verdict = "PASS" if gap_upper <= bound else "FAIL" if gap > bound else "UNCERTAIN"
    if not ls.get("valid", True):
        verdict = f"VOID(loader late_start_s={ls.get('late_start_s')})"
    # 實際相位：中斷時刻距最後一次「中斷前取得」的快照；回寫太慢而跨過下一次快照時會偏離目標
    before = [e["snap_ts"] for e in flushes if e["snap_ts"] <= crash_at]
    actual_phase = round((crash_at - max(before)) / args.interval, 3) if before else None
    if actual_phase is None or abs(actual_phase - args.phase) > 0.1:
        verdict = f"VOID(phase={actual_phase}, target={args.phase})"

    # 5. 重啟同一個私有 Redis，確認沒有可恢復的計數（不持久化）
    rs2 = start_private_redis(args.port, redis_dir, redis_log)
    recovered = subprocess.run(["redis-cli", "-p", str(args.port), "dbsize"],
                               capture_output=True, text=True).stdout.strip()
    rs2.kill()  # 只關閉自己啟動、且已核對 pid 的實例
    rs2.wait()

    row = {
        "run_id": run_id, "repeat": args.repeat, "failed_component": "redis(SIGKILL)",
        "redis_persistence": persistence, "flush_interval_s": args.interval,
        "last_flush_commit_at": last["ts"] if last else "", "crash_at": round(crash_at, 6),
        "actual_exposure_s": round(crash_at - last["snap_ts"], 3) if last else "",
        "baseline_B0": b0, "acknowledged_N": n, "failed_ops": ls["error_ops"],
        "unknown_ops": ls["unknown_ops"], "pg_increment_P": p, "pg_gap_N_minus_P": gap,
        "redis_recovered_count": f"dbsize={recovered}", "pg_increment_after_recovery": p,
        "net_gap_after_recovery": gap, "raw_log_path": os.path.relpath(out, ROOT),
        "notes": (f"bound=acks_after_last_snapshot={bound}; "
                  f"throughput={throughput:.1f}/s over {load_s:.2f}s; ref throughput*T={ref:.0f}; "
                  f"acked_lost in [{gap}, {gap_upper}] "
                  f"(in_flight={in_flight}, sent_before_snapshot={early}); phase={actual_phase}; exposure from snap_ts; "
                  f"criterion3={verdict}"),
    }
    with open(os.path.join(ROOT, "results", "failure_template.csv")) as f:
        fields = next(csv.reader(f))
    new = not os.path.exists(args.csv)
    with open(args.csv, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)
    with open(os.path.join(out, "crash_summary.json"), "w") as f:
        json.dump(row, f, ensure_ascii=False, indent=1)
    print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
