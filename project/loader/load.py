#!/usr/bin/env python3
"""統一負載器：用同一套閉迴路邏輯打 PG（UPDATE +1）或 Redis（INCR），記錄逐筆延遲。

每個 client 一次只有一個在途請求、不用 pipeline，與 pgbench 預設行為對齊。
介面規格見 docs/interfaces.md；設計見 docs/design/loader.md。
"""
import argparse
import array
import asyncio
import json
import math
import multiprocessing as mp
import os
import random
import signal
import time

N_PRODUCTS = 1000


def pick_product(rng, scenario):
    if scenario == "hot80":
        return rng.randint(1, 5) if rng.random() < 0.8 else rng.randint(6, N_PRODUCTS)
    if scenario == "hot5":
        return rng.randint(1, 5)
    return rng.randint(1, N_PRODUCTS)  # uniform1000


async def run_worker(args, worker_idx, n_clients, t_start, t_measure, t_end):
    """一個行程內跑 n_clients 個 client，回傳本行程的統計。"""
    if args.backend == "pg":
        import asyncpg
        conns = await asyncio.gather(*(asyncpg.connect(database=args.pg_db)
                                       for _ in range(n_clients)))
        sql = "UPDATE products SET view_count = view_count + 1 WHERE product_id = $1"

        async def do(i, pid):
            await conns[i].execute(sql, pid)
    else:
        import redis.asyncio as aioredis
        pool = aioredis.ConnectionPool(host=args.redis_host, port=args.redis_port,
                                       max_connections=n_clients)
        r = aioredis.Redis(connection_pool=pool)
        prefix = f"q01:{args.run_id}:views:"
        # 同時發出 n_clients 個 PING，讓連線池在開始前就建好所有連線
        await asyncio.gather(*(r.ping() for _ in range(n_clients)))

        async def do(i, pid):
            await r.incr(prefix + str(pid))

    lat_us = array.array("I")          # 正式期每筆成功請求的延遲（微秒）
    # --ack-log：每筆成功請求「收到回應」的時刻，存成距 t_start 的微秒數（判準 3，D41）
    ack_us = array.array("Q") if args.ack_log else None
    # 同時記錄失敗／逾時請求的「送出」時刻：中斷時在途、且在快照之前送出的，才可能已被回寫
    fail_us = array.array("Q") if args.ack_log else None
    sent = [0] * (N_PRODUCTS + 1)      # 全期（含暖機）每商品成功次數，用於對帳
    # 成功、錯誤、未知都分暖機期與正式期，正式期的欄位才和延遲樣本同範圍
    stats = {"ok_warmup": 0, "ok": 0, "err_warmup": 0, "err": 0,
             "unknown_warmup": 0, "unknown": 0, "errors": {}}

    async def client(i):
        rng = random.Random(args.seed * 100003 + worker_idx * 1009 + i)
        while True:
            now = time.time()
            if now >= t_end:
                return
            pid = pick_product(rng, args.scenario)
            phase = "" if now >= t_measure else "_warmup"  # 依請求送出時間歸入暖機或正式期
            t0 = time.perf_counter()
            sent_us = max(0, int((now - t_start) * 1e6))
            try:
                await asyncio.wait_for(do(i, pid), args.timeout)
            except asyncio.TimeoutError:
                # 逾時：伺服器可能已執行但客戶端沒收到，不能算成功也不能算失敗
                stats["unknown" + phase] += 1
                if fail_us is not None:
                    fail_us.append(sent_us)
                if args.stop_on_error:
                    return
                continue
            except Exception as e:  # noqa: BLE001 - 連線中斷等都要計數，不重送
                stats["err" + phase] += 1
                k = type(e).__name__
                stats["errors"][k] = stats["errors"].get(k, 0) + 1
                if fail_us is not None:
                    fail_us.append(sent_us)
                if args.stop_on_error:
                    return
                await asyncio.sleep(0.05)
                continue
            dt = time.perf_counter() - t0
            if ack_us is not None:
                ack_us.append(max(0, int((time.time() - t_start) * 1e6)))
            sent[pid] += 1
            stats["ok" + phase] += 1
            if not phase:
                lat_us.append(min(int(dt * 1e6), 2**32 - 1))

    # 連線都建好後，所有行程等到同一個絕對時間點才開始；趕不上就記下遲到秒數，該輪判為無效
    stats["late_start_s"] = round(max(0.0, time.time() - t_start), 3)
    await asyncio.sleep(max(0.0, t_start - time.time()))
    await asyncio.gather(*(client(i) for i in range(n_clients)))

    if args.backend == "pg":
        for c in conns:
            await c.close()
    else:
        await r.aclose()
        await pool.aclose()
    return lat_us, ack_us, fail_us, sent, stats


def worker_main(args, worker_idx, n_clients, t_start, t_measure, t_end, out_dir):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    lat_us, ack_us, fail_us, sent, stats = asyncio.run(
        run_worker(args, worker_idx, n_clients, t_start, t_measure, t_end))
    with open(os.path.join(out_dir, f"lat_us.{worker_idx}.bin"), "wb") as f:
        lat_us.tofile(f)
    if ack_us is not None:
        with open(os.path.join(out_dir, f"ack_us.{worker_idx}.bin"), "wb") as f:
            ack_us.tofile(f)
        with open(os.path.join(out_dir, f"fail_sent_us.{worker_idx}.bin"), "wb") as f:
            fail_us.tofile(f)
    with open(os.path.join(out_dir, f"part.{worker_idx}.json"), "w") as f:
        json.dump({"sent": sent, **stats}, f)


def p95_nearest_rank(values):
    if not values:
        return None
    values = sorted(values)
    return values[math.ceil(0.95 * len(values)) - 1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", choices=["pg", "redis"], required=True)
    ap.add_argument("--scenario", choices=["hot80", "uniform1000", "hot5"], required=True)
    ap.add_argument("--concurrency", type=int, required=True)
    ap.add_argument("--warmup", type=float, default=30)
    ap.add_argument("--duration", type=float, default=60, help="正式期秒數")
    ap.add_argument("--procs", type=int, default=min(8, os.cpu_count() or 1),
                    help="負載行程數（避免單一 Python 行程成為瓶頸）")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", required=True, help="結果目錄")
    ap.add_argument("--timeout", type=float, default=10)
    ap.add_argument("--stop-on-error", action="store_true",
                    help="client 遇錯即停，不重試（故障實驗用）")
    ap.add_argument("--ack-log", action="store_true",
                    help="逐筆記錄成功回應的時刻到 ack_us.<i>.bin、失敗／逾時請求的送出時刻到 fail_sent_us.<i>.bin"
                         "（uint64 微秒，距 t_start；故障實驗用，D41）")
    ap.add_argument("--start-at", type=float,
                    help="開始送請求的 epoch 秒（由執行器指定，用來對齊取樣）；預設為現在＋建立連線的時間")
    ap.add_argument("--pg-db", default="q01")
    ap.add_argument("--redis-host", default="localhost")
    ap.add_argument("--redis-port", type=int, default=6379)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    procs = max(1, min(args.procs, args.concurrency))
    shares = [args.concurrency // procs + (1 if i < args.concurrency % procs else 0)
              for i in range(procs)]
    t_start = args.start_at or time.time() + 2 + args.concurrency / 200  # 預留建立連線的時間
    t_measure = t_start + args.warmup
    t_end = t_measure + args.duration

    ctx = mp.get_context("spawn")
    ps = [ctx.Process(target=worker_main,
                      args=(args, i, n, t_start, t_measure, t_end, args.out))
          for i, n in enumerate(shares)]
    for p in ps:
        p.start()
    for p in ps:
        p.join()
    bad = [p.exitcode for p in ps if p.exitcode != 0]

    lat = array.array("I")
    sent = [0] * (N_PRODUCTS + 1)
    counters = ("ok_warmup", "ok", "err_warmup", "err", "unknown_warmup", "unknown")
    tot = {k: 0 for k in counters}
    tot["errors"] = {}
    late = 0.0
    for i in range(procs):
        with open(os.path.join(args.out, f"lat_us.{i}.bin"), "rb") as f:
            lat.frombytes(f.read())
        with open(os.path.join(args.out, f"part.{i}.json")) as f:
            part = json.load(f)
        for pid, c in enumerate(part["sent"]):
            sent[pid] += c
        for k in counters:
            tot[k] += part[k]
        late = max(late, part["late_start_s"])
        for k, v in part["errors"].items():
            tot["errors"][k] = tot["errors"].get(k, 0) + v

    total_ok = sum(sent)
    hot = sum(sent[1:6])
    p95 = p95_nearest_rank(lat)
    summary = {
        "run_id": args.run_id, "backend": args.backend, "scenario": args.scenario,
        "concurrency": args.concurrency, "procs": procs, "seed": args.seed,
        "warmup_s": args.warmup, "measured_s": args.duration,
        "t_start": t_start, "t_measure_start": t_measure, "t_measure_end": t_end,
        "ack_log": bool(args.ack_log),
        "successful_ops": tot["ok"], "successful_ops_warmup": tot["ok_warmup"],
        "successful_ops_total": total_ok,
        "error_ops": tot["err"], "unknown_ops": tot["unknown"],
        "error_ops_warmup": tot["err_warmup"], "unknown_ops_warmup": tot["unknown_warmup"],
        "errors": tot["errors"],
        "success_ops_per_s": round(tot["ok"] / args.duration, 1),
        "p95_ms": None if p95 is None else round(p95 / 1000, 3),
        "latency_samples": len(lat),
        "hot_share_actual": round(hot / total_ok, 4) if total_ok else None,
        "worker_exitcodes_nonzero": bad,
        "late_start_s": late,
        "valid": not bad and late == 0,
    }
    with open(os.path.join(args.out, "sent_per_product.json"), "w") as f:
        json.dump(sent, f)
    with open(os.path.join(args.out, "loader_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))
    raise SystemExit(0 if summary["valid"] else 1)


if __name__ == "__main__":
    main()
