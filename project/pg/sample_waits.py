#!/usr/bin/env python3
"""壓測期間每隔 --every 秒取樣一次 pg_stat_activity，直到收到 SIGTERM／SIGINT。

輸出：
  <out>/waits.csv          ts, wait_event_type, wait_event, count（每次取樣、每種等待一列；沒在等待記為 'none'）
  <out>/waits_summary.json 摘要，兩個比例的定義寫在檔內

這是取樣，不是累計等待時間。設計見 docs/design/pg_line.md。
"""
import argparse
import asyncio
import csv
import json
import os
import signal
import time

import asyncpg

SQL = """
SELECT coalesce(wait_event_type, 'none') AS t, coalesce(wait_event, 'none') AS e, count(*) AS n
FROM pg_stat_activity
WHERE datname = $1 AND state = 'active' AND backend_type = 'client backend'
  AND pid <> pg_backend_pid()
GROUP BY 1, 2
"""
SQL_CONNS = """
SELECT count(*) FROM pg_stat_activity
WHERE datname = $1 AND backend_type = 'client backend' AND pid <> pg_backend_pid()
"""
LOCKISH = {"Lock"}  # Lock 類包含 transactionid、tuple 等行鎖等待


async def main_async(args):
    os.makedirs(args.out, exist_ok=True)
    conn = await asyncpg.connect(database=args.db)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    samples = 0
    samples_with_lock = 0
    active_total = 0
    lock_total = 0
    by_event = {}
    max_conns = 0
    with open(os.path.join(args.out, "waits.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "wait_event_type", "wait_event", "count"])
        next_t = t_first = t_last = time.monotonic()
        while not stop.is_set():
            ts = round(time.time(), 3)
            t_last = time.monotonic()
            rows = await conn.fetch(SQL, args.db)
            max_conns = max(max_conns, await conn.fetchval(SQL_CONNS, args.db))
            samples += 1
            n_active = sum(r["n"] for r in rows)
            n_lock = sum(r["n"] for r in rows if r["t"] in LOCKISH)
            active_total += n_active
            lock_total += n_lock
            samples_with_lock += n_lock > 0
            for r in rows:
                w.writerow([ts, r["t"], r["e"], r["n"]])
                k = f"{r['t']}:{r['e']}"
                by_event[k] = by_event.get(k, 0) + r["n"]
            f.flush()
            next_t += args.every
            try:
                await asyncio.wait_for(stop.wait(), max(0.0, next_t - time.monotonic()))
            except asyncio.TimeoutError:
                pass
    elapsed = t_last - t_first  # 第一次到最後一次取樣
    await conn.close()

    summary = {
        "samples": samples,
        "every_s": args.every,
        # 實際平均間隔：查詢比間隔還慢時會落後，取樣次數就少於 elapsed ÷ every
        "actual_every_s": round(elapsed / (samples - 1), 4) if samples > 1 else None,
        "share_samples_with_lock": round(samples_with_lock / samples, 4) if samples else None,
        "share_active_backends_on_lock": round(lock_total / active_total, 4) if active_total else None,
        "max_client_connections_seen": max_conns,
        "by_event_backend_samples": dict(sorted(by_event.items(), key=lambda kv: -kv[1])),
        "definitions": {
            "share_samples_with_lock": "至少有一個 active 連線在等 Lock 類事件的取樣次數 ÷ 總取樣次數",
            "share_active_backends_on_lock": "所有取樣中，等 Lock 類事件的連線數總和 ÷ active 連線數總和（提案判準 2 採用此項）",
        },
    }
    with open(os.path.join(args.out, "waits_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", default="q01")
    ap.add_argument("--every", type=float, default=0.1, help="取樣間隔秒數；提案判準 2 規定 100 ms（D41）")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
