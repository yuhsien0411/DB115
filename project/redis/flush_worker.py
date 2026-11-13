#!/usr/bin/env python3
"""方案 B 的快照回寫 worker。

  init  --run-id R              從 PG 的 view_count 初始化 Redis key（key 已存在就拒絕）
  run   --run-id R --interval T 每 T 秒 MGET 全部 key，以 GREATEST 寫回 PG；收到 SIGTERM 時做最後一次回寫再結束

不變式與設計見 docs/design/flush_worker.md。
結束碼：0 正常；2 init 時 run 已存在；3 偵測到 key 遺失、計數倒退或 Redis 重啟，run 作廢。
"""
import argparse
import asyncio
import json
import signal
import sys
import time

import asyncpg
import redis.asyncio as aioredis

N_PRODUCTS = 1000
PIDS = list(range(1, N_PRODUCTS + 1))

FLUSH_SQL = """
WITH s AS (
    SELECT * FROM unnest($1::int[], $2::bigint[]) AS s(product_id, snapshot)
), upd AS (
    UPDATE products p
    SET view_count = GREATEST(p.view_count, s.snapshot)
    FROM s
    WHERE p.product_id = s.product_id AND s.snapshot > p.view_count
    RETURNING p.product_id, p.view_count
)
INSERT INTO view_flush_log (run_id, product_id, snapshot)
SELECT $3, product_id, view_count FROM upd
"""


def keys(run_id):
    return [f"q01:{run_id}:views:{pid}" for pid in PIDS]


def log(fp, **event):
    event["ts"] = round(time.time(), 6)
    line = json.dumps(event, ensure_ascii=False)
    print(line, flush=True)
    if fp:
        fp.write(line + "\n")
        fp.flush()


async def cmd_init(args):
    pg = await asyncpg.connect(database=args.pg_db)
    rows = await pg.fetch("SELECT product_id, view_count FROM products ORDER BY product_id")
    await pg.close()
    r = aioredis.Redis(host=args.redis_host, port=args.redis_port)
    meta = f"q01:{args.run_id}:meta"
    # HSETNX 是原子的：同一個 run_id 只有第一個 init 能拿到，避免兩個 init 互相覆蓋
    if not await r.hsetnx(meta, "status", "initializing") or await r.exists(*keys(args.run_id)):
        print(f"run {args.run_id} 已存在，拒絕覆蓋（不變式 1）", file=sys.stderr)
        await r.aclose()
        return 2
    pipe = r.pipeline(transaction=True)
    for row in rows:
        pipe.set(f"q01:{args.run_id}:views:{row['product_id']}", row["view_count"])
    pipe.hset(meta, mapping={
        "started_at": time.time(), "baseline_sum": sum(r_["view_count"] for r_ in rows),
        "redis_run_id": (await r.info("server"))["run_id"], "status": "initialized"})
    await pipe.execute()
    await r.aclose()
    print(json.dumps({"event": "init", "run_id": args.run_id, "products": len(rows),
                      "baseline_sum": sum(r_["view_count"] for r_ in rows)}))
    return 0


async def cmd_run(args):
    fp = open(args.log, "a") if args.log else None
    pg = await asyncpg.connect(database=args.pg_db)
    r = aioredis.Redis(host=args.redis_host, port=args.redis_port,
                       socket_timeout=args.interval, socket_connect_timeout=1)
    ks = keys(args.run_id)
    # 比較基準從 PG 現值開始：init 時 key = PG 值，且此 run 只有本 worker 會寫 PG，所以快照不可能低於它
    rows = await pg.fetch("SELECT view_count FROM products ORDER BY product_id")
    last = [row["view_count"] for row in rows]
    # Redis 每次啟動都會產生新的 run_id；不一致代表 Redis 重啟過，INCR 可能已從 0 重建 key
    redis_run_id = (await r.hgetall(f"q01:{args.run_id}:meta")).get(b"redis_run_id", b"").decode()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async def flush_once(final=False):
        t0 = time.perf_counter()
        try:
            snap_ts = time.time()
            vals = await r.mget(ks)
            now_run_id = (await r.info("server"))["run_id"]
        except (aioredis.ConnectionError, aioredis.TimeoutError, OSError) as e:
            log(fp, event="redis_unreachable", error=type(e).__name__, final=final)
            return 0
        if now_run_id != redis_run_id:
            log(fp, event="abort", reason="redis_restarted",
                expected=redis_run_id, got=now_run_id)
            return 3
        if any(v is None for v in vals):
            missing = sum(v is None for v in vals)
            log(fp, event="abort", reason="key_missing", missing=missing)
            return 3
        snap = [int(v) for v in vals]
        back = [i + 1 for i in range(N_PRODUCTS) if snap[i] < last[i]]
        if back:
            log(fp, event="abort", reason="counter_went_back", products=back[:10])
            return 3
        status = await pg.execute(FLUSH_SQL, PIDS, snap, args.run_id)  # 單一交易
        last[:] = snap
        log(fp, event="flush", final=final, changed=int(status.split()[-1]),
            redis_sum=sum(snap), snap_ts=round(snap_ts, 6),
            ms=round((time.perf_counter() - t0) * 1000, 2))
        return 0

    log(fp, event="worker_start", run_id=args.run_id, interval_s=args.interval)
    code = 0
    next_t = time.monotonic() + args.interval
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), max(0.0, next_t - time.monotonic()))
            break
        except asyncio.TimeoutError:
            pass
        next_t += args.interval  # 固定節拍，不因回寫耗時漂移
        code = await flush_once()
        if code:
            break
    if code == 0:
        code = await flush_once(final=True)
    log(fp, event="worker_stop", code=code)
    await pg.close()
    await r.aclose()
    if fp:
        fp.close()
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["init", "run"])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--interval", type=float, default=5, help="回寫間隔 T（秒）")
    ap.add_argument("--log", help="事件記錄 JSONL 路徑")
    ap.add_argument("--pg-db", default="q01")
    ap.add_argument("--redis-host", default="localhost")
    ap.add_argument("--redis-port", type=int, default=6379)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(cmd_init(args) if args.cmd == "init" else cmd_run(args)))


if __name__ == "__main__":
    main()
