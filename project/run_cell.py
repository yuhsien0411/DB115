#!/usr/bin/env python3
"""執行實驗矩陣的一格：暖機 → 正式期 → 計數對帳 → append 一列到批次資料夾的 runs.csv（預設 results/<今天日期>/，D39）。

  --backend pg     --tool pgbench   PG，用 pgbench 腳本（課程指定工具，主實驗）
  --backend pg     --tool loader    PG，用統一負載器（與 Redis 同一個工具，交叉驗證）
  --backend redis                   Redis INCR ＋ 快照回寫 worker（一律用統一負載器）

PG 模式會在正式期取樣鎖等待。
結束碼：0 對帳一致且有效；1 對帳不一致；2 run 作廢（worker abort、負載器遲到等）。
不論結果為何都會寫入那一列，原因寫在 notes。
介面規格見 docs/interfaces.md；設計見 docs/design/run_cell.md。
"""
import argparse
import csv
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time


from tools.terminal_progress import Spinner

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def psql(db, sql):
    return subprocess.run(["psql", "-X", "-At", "-d", db, "-c", sql],
                          check=True, capture_output=True, text=True).stdout.strip()


def per_product(db):
    rows = psql(db, "SELECT product_id, view_count FROM products ORDER BY product_id")
    return {int(a): int(b) for a, b in (line.split("|") for line in rows.splitlines())}


CHILDREN = []  # 所有啟動過的子行程；finally 時以行程群組為單位清掉


def spawn(cmd, **kw):
    # 新 session：負載器 multiprocessing 的子行程會在同一個行程群組，可以一起清掉
    p = subprocess.Popen(cmd, start_new_session=True, **kw)
    CHILDREN.append(p)
    return p


def kill_all():
    for p in CHILDREN:
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()


OTHERS_SQL = """
SELECT pid, usename, datname, coalesce(application_name, ''), coalesce(client_addr::text, 'local'), state
FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()
"""


def preflight_connections(args, notes):
    """PG 模式開跑前檢查連線名額（max_connections 是全伺服器共用的）。

    需要：併發數 ＋ 取樣器 1 條。取樣器在名額不夠時自動關閉（--sampler auto）。
    名額仍不夠時：預設列出占用者並中止；加 --close-others 才會斷開其他 client 連線。
    """
    slots = int(psql(args.db, "SHOW max_connections"))
    if args.sampler == "auto":
        args.sampler_on = args.concurrency + 1 <= slots
    else:
        args.sampler_on = args.sampler == "on"
    if not args.sampler_on:
        notes.append("sampler=off" + (" (connection limit)" if args.sampler == "auto" else ""))
    need = args.concurrency + (1 if args.sampler_on else 0)

    others = [line.split("|") for line in psql(args.db, OTHERS_SQL).splitlines() if line]
    if need + len(others) <= slots:
        return
    listing = "\n".join(f"  pid={o[0]} user={o[1]} db={o[2]} app={o[3] or '-'} from={o[4]} state={o[5]}"
                        for o in others)
    if not args.close_others:
        sys.exit(f"連線名額不足：需要 {need} 條，上限 {slots}，目前其他連線 {len(others)} 條：\n{listing}\n"
                 "請先關閉它們（網站、psql、資料庫瀏覽工具等），或加 --close-others 由本程式斷開。")
    print(f"--close-others：斷開 {len(others)} 條其他連線：\n{listing}", file=sys.stderr)
    psql(args.db, "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()")
    time.sleep(0.5)
    left = [line for line in psql(args.db, OTHERS_SQL).splitlines() if line]
    if need + len(left) > slots:  # 被斷開的程式立刻重連（例如網站的連線池）
        sys.exit(f"斷開後仍有 {len(left)} 條連線重新連上，請先停止那些程式：\n" + "\n".join(left))
    notes.append(f"closed_others={len(others)}")


def start_sampler(db, out, every):
    return spawn([PY, os.path.join(ROOT, "pg", "sample_waits.py"),
                  "--db", db, "--out", out, "--every", str(every)], stdout=subprocess.DEVNULL)


def stop_sampler(p, out):
    p.send_signal(signal.SIGTERM)
    p.wait()
    with open(os.path.join(out, "waits_summary.json")) as f:
        return json.load(f)


def run_tracked(cmd, *, progress_label, **kw):
    """同 subprocess.run，但登記在 CHILDREN，被中斷時一樣會被清掉。"""
    p = spawn(cmd, **kw)
    with Spinner(progress_label):
        p.wait()
    return p.returncode


def run_pgbench(args, out, notes):
    script = os.path.join(ROOT, "pg", f"pg_{args.scenario}.sql")
    jobs = str(min(args.concurrency, args.pgbench_jobs))
    base = ["pgbench", "-n", "-c", str(args.concurrency), "-j", jobs, "-f", script, args.db]
    if args.warmup > 0:
        rc = run_tracked(base[:1] + ["-T", str(int(args.warmup)), f"--random-seed={args.seed}"]
                         + base[1:], progress_label=f"PG 暖機（{args.warmup:g} 秒）",
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc != 0:
            raise RuntimeError(f"pgbench 暖機失敗（exit={rc}）")
    before = per_product(args.db)
    sampler = start_sampler(args.db, out, args.sample_every) if args.sampler_on else None
    with open(os.path.join(out, "pgbench_stdout.txt"), "w") as so, \
         open(os.path.join(out, "pgbench_stderr.txt"), "w") as se:
        rc = run_tracked(base[:1] + ["-T", str(int(args.duration)), "-l",
                                     "--log-prefix", os.path.join(out, "pgbench_log"),
                                     f"--random-seed={args.seed + 1}"] + base[1:],
                         progress_label=f"PG 正式測試（{args.duration:g} 秒）",
                         stdout=so, stderr=se)
    waits = stop_sampler(sampler, out) if sampler else {}
    after = per_product(args.db)
    text = open(os.path.join(out, "pgbench_stdout.txt")).read()
    if rc != 0:
        notes.append(f"pgbench exit={rc}")
    m = re.search(r"number of transactions actually processed: (\d+)", text)
    ok_ops = int(m.group(1)) if m else 0
    m = re.search(r"number of failed transactions: (\d+)", text)
    err_ops = int(m.group(1)) if m else 0
    p95 = json.loads(subprocess.run(
        [PY, os.path.join(ROOT, "pg", "p95_pgbench.py"), os.path.join(out, "pgbench_log.*")],
        check=True, capture_output=True, text=True).stdout)
    delta = {k: after[k] - before[k] for k in after}
    total = sum(delta.values())
    if total != ok_ops:
        notes.append(f"COUNT MISMATCH: pgbench ok={ok_ops} vs PG delta={total}")
    return {
        "successful_ops": ok_ops, "error_ops": err_ops, "unknown_ops": 0,
        "success_ops_per_s": round(ok_ops / args.duration, 1),
        "p95_ms": p95["p95_ms"], "latency_samples": p95["successful_samples"],
        "hot_share_actual": round(sum(delta[i] for i in range(1, 6)) / total, 4) if total else "",
        **({"pg_connections_actual": waits["max_client_connections_seen"],
            "lock_share": waits["share_active_backends_on_lock"],
            "sample_every": waits["every_s"], "sample_actual": waits.get("actual_every_s"),
            "samples": waits["samples"]} if waits else {}),
        "reconciled": total == ok_ops,
    }


def run_loader(args, out, notes, watch=None):
    """watch：回寫 worker。它提早結束（run 作廢）時，立刻停止負載並保留證據。"""
    t_start = time.time() + 2 + args.concurrency / 200
    t_measure = t_start + args.warmup
    loader = spawn(
        [PY, os.path.join(ROOT, "loader", "load.py"), "--backend", args.backend,
         "--scenario", args.scenario, "--concurrency", str(args.concurrency),
         "--warmup", str(args.warmup), "--duration", str(args.duration),
         "--seed", str(args.seed), "--run-id", args.run_id, "--out", out,
         "--start-at", str(t_start), "--pg-db", args.db],
        stdout=subprocess.DEVNULL)
    sampler = None
    if args.backend == "pg" and args.sampler_on:
        time.sleep(max(0.0, t_measure - time.time()))
        sampler = start_sampler(args.db, out, args.sample_every)
        time.sleep(max(0.0, t_measure + args.duration - time.time()))
        waits = stop_sampler(sampler, out)
    while loader.poll() is None:
        if watch is not None and watch.poll() is not None:
            os.killpg(loader.pid, signal.SIGKILL)
            loader.wait()
            notes.append(f"RUN VOID: flush worker exited early (exit={watch.returncode}), load stopped")
            return {"void": True, "reconciled": False}
        time.sleep(0.2)
    if loader.returncode != 0:
        notes.append(f"loader exit={loader.returncode}")
    with open(os.path.join(out, "loader_summary.json")) as f:
        s = json.load(f)
    if not s.get("valid", True):
        notes.append(f"RUN VOID: loader late_start_s={s.get('late_start_s')}")
    r = {k: s[k] for k in ("successful_ops", "error_ops", "unknown_ops", "success_ops_per_s",
                           "p95_ms", "latency_samples", "hot_share_actual")}
    r["total_ops"] = s["successful_ops_total"]
    r["void"] = not s.get("valid", True)
    if sampler:
        r["pg_connections_actual"] = waits["max_client_connections_seen"]
        r["lock_share"] = waits["share_active_backends_on_lock"]
        r["sample_every"], r["samples"] = waits["every_s"], waits["samples"]
        r["sample_actual"] = waits.get("actual_every_s")
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["pg", "redis"], required=True)
    ap.add_argument("--tool", choices=["pgbench", "loader"], default="pgbench",
                    help="PG 的負載工具；redis 模式忽略")
    ap.add_argument("--scenario", choices=["hot80", "uniform1000", "hot5"], required=True)
    ap.add_argument("--concurrency", type=int, required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--warmup", type=float, default=30)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--interval", type=float, default=5, help="Redis 回寫間隔 T（秒）")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--db", default="q01")
    ap.add_argument("--pgbench-jobs", type=int, default=16)
    # D39：正式結果依批次放在 results/<YYYYMMDD>/；run_matrix.sh 會傳入整批共用的資料夾
    ap.add_argument("--results", default=os.path.join(ROOT, "results", time.strftime("%Y%m%d")),
                    help="結果資料夾（預設 results/<今天日期>）；runs.csv 與每輪目錄都寫在這裡")
    ap.add_argument("--note", default="", help="寫進 CSV notes 的自由備註")
    ap.add_argument("--sampler", choices=["auto", "on", "off"], default="auto",
                    help="PG 鎖等待取樣器；auto＝連線名額夠才開（500 併發時會關）")
    ap.add_argument("--sample-every", type=float, default=0.1,
                    help="鎖等待取樣間隔（秒）；提案判準 2 規定每 100 ms 一次（D41）")
    ap.add_argument("--close-others", action="store_true",
                    help="名額不足時斷開其他 client 連線（網站、psql 等）；預設只列出並中止")
    args = ap.parse_args()
    args.sampler_on = False
    if args.backend == "pg" and args.tool == "pgbench":
        # pgbench -T 只接受整數秒；讓實際執行時間、CSV 與吞吐分母一致
        for name in ("warmup", "duration"):
            v = getattr(args, name)
            if v != int(v) or (name == "duration" and v < 1):
                ap.error(f"pgbench 模式的 --{name} 必須是正整數秒")

    tag = args.tag = {"pg": "pg" if args.tool == "pgbench" else "pgl", "redis": "redis"}[args.backend]
    args.seed = args.seed if args.seed is not None else args.round * 1000 + args.concurrency
    args.run_id = (f"{tag}_{args.scenario}_c{args.concurrency}_r{args.round}"
                   f"_{time.strftime('%Y%m%d%H%M%S')}")
    notes = [args.note] if args.note else []
    if args.backend == "pg":
        preflight_connections(args, notes)  # 名額不足就在建目錄前中止，不留空的結果目錄
    out = os.path.join(args.results, args.run_id)
    os.makedirs(out)  # 會連同批次資料夾一起建立
    # SIGTERM／SIGHUP 也改走 SystemExit，finally 才能清掉子行程（SIGKILL 攔不到，見文件）
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda signum, frame: sys.exit(128 + signum))
    try:
        r, flush_ms, backlog = run_mode(args, out, notes)
    finally:
        kill_all()
    write_row(args, out, notes, r, flush_ms, backlog)


def run_mode(args, out, notes):
    flush_ms = backlog = ""

    if args.backend == "pg" and args.tool == "pgbench":
        r = run_pgbench(args, out, notes)
    elif args.backend == "pg":
        b0 = int(psql(args.db, "SELECT sum(view_count) FROM products"))
        with Spinner(f"PG 負載測試（暖機 {args.warmup:g} 秒／正式 {args.duration:g} 秒）"):
            r = run_loader(args, out, notes)
        r["reconciled"] = int(psql(args.db, "SELECT sum(view_count) FROM products")) - b0 == r["total_ops"]
    else:
        b0 = int(psql(args.db, "SELECT sum(view_count) FROM products"))
        flush_log = os.path.join(out, "flush.jsonl")
        subprocess.run([PY, os.path.join(ROOT, "redis", "flush_worker.py"), "init",
                        "--run-id", args.run_id, "--pg-db", args.db],
                       check=True, stdout=subprocess.DEVNULL)
        worker = spawn([PY, os.path.join(ROOT, "redis", "flush_worker.py"), "run",
                        "--run-id", args.run_id, "--interval", str(args.interval),
                        "--log", flush_log, "--pg-db", args.db],
                       stdout=subprocess.DEVNULL)
        with Spinner(f"Redis 負載測試（暖機 {args.warmup:g} 秒／正式 {args.duration:g} 秒）"):
            r = run_loader(args, out, notes, watch=worker)
        if worker.poll() is None:
            worker.send_signal(signal.SIGTERM)  # 觸發最後一次回寫
        with Spinner("Redis 最後回寫與對帳"):
            worker_rc = worker.wait()
        if worker_rc != 0:
            r["void"] = True
            if not any("RUN VOID" in n for n in notes):
                notes.append(f"RUN VOID: flush worker exit={worker.returncode}")
        if r.get("total_ops") is None:  # 負載已被停止：保留證據，不做對帳
            return r, flush_ms, backlog
        events = [json.loads(line) for line in open(flush_log)]
        flushes = [e for e in events if e["event"] == "flush"]
        flush_ms = round(statistics.median(e["ms"] for e in flushes), 2) if flushes else ""
        # 相鄰快照增量的最大值（含 init 期初 → 第一次回寫），定義見 docs/design/run_cell.md
        sums = [b0] + [e["redis_sum"] for e in flushes]
        backlog = max(b - a for a, b in zip(sums, sums[1:])) if flushes else ""
        p = int(psql(args.db, "SELECT sum(view_count) FROM products")) - b0
        r["reconciled"] = p == r["total_ops"]
        if not r["reconciled"]:
            notes.append(f"COUNT MISMATCH: acked={r['total_ops']} vs PG delta={p}")
    if args.backend != "pg" or args.tool != "pgbench":
        if not r["reconciled"] and not any("MISMATCH" in n for n in notes):
            notes.append("COUNT MISMATCH")
    return r, flush_ms, backlog


def write_row(args, out, notes, r, flush_ms, backlog):
    tag = args.tag
    if "lock_share" in r:
        notes.append(f"lock_share={r['lock_share']}")
        notes.append(f"sample_every={r['sample_every']:g}s (actual {r['sample_actual']}s); samples={r['samples']}")
    notes.append(f"tool={'pgbench' if tag == 'pg' else 'loader'}")

    row = {
        "run_id": args.run_id, "scenario": args.scenario, "backend": tag,
        "concurrency": args.concurrency, "round": args.round, "seed": args.seed,
        "products": psql(args.db, "SELECT count(*) FROM products"),
        "hot_share_actual": r.get("hot_share_actual", ""),
        "flush_interval_s": args.interval if args.backend == "redis" else "",
        "warmup_s": args.warmup, "measured_s": args.duration,
        "successful_ops": r.get("successful_ops", ""), "error_ops": r.get("error_ops", ""),
        "unknown_ops": r.get("unknown_ops", ""), "success_ops_per_s": r.get("success_ops_per_s", ""),
        "p95_ms": r.get("p95_ms", ""), "latency_samples": r.get("latency_samples", ""),
        "pg_connections_actual": r.get("pg_connections_actual", ""),
        "worker_flush_ms": flush_ms, "worker_backlog": backlog,
        "raw_log_path": os.path.relpath(out, ROOT),
        "summary_path": os.path.relpath(os.path.join(out, "cell_summary.json"), ROOT),
        "notes": "; ".join(notes),
    }
    with open(os.path.join(out, "cell_summary.json"), "w") as f:
        json.dump(row, f, ensure_ascii=False, indent=1)
    with open(os.path.join(ROOT, "results", "runs_template.csv")) as f:
        fields = next(csv.reader(f))
    path = os.path.join(args.results, "runs.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)
    print(json.dumps(row, ensure_ascii=False))
    raise SystemExit(2 if r.get("void") else 0 if r["reconciled"] else 1)


if __name__ == "__main__":
    main()
