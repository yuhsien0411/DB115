#!/usr/bin/env python3
"""防05：如果後台商品頁的「已售」寫成 N+1，代價是多少（唯讀，跑在 q01，用 student）。
批次版＝server.py 現在的寫法：一頁 12 件，一條 SQL 用 ANY($1) 一次算完。
N+1 版＝先拿到 12 件，再對每一件各查一次。各跑 200 次取中位數。"""
import asyncio, statistics, time, asyncpg

BATCH = """SELECT i.product_id, sum(i.quantity)::bigint FROM orders o JOIN order_items i USING (order_id)
           WHERE o.status = 'pending' AND i.product_id = ANY($1::int[]) GROUP BY 1"""
ONE = """SELECT coalesce(sum(i.quantity), 0)::bigint FROM orders o JOIN order_items i USING (order_id)
         WHERE o.status = 'pending' AND i.product_id = $1"""

async def main():
    c = await asyncpg.connect(database='q01')
    ids = [r['product_id'] for r in await c.fetch('SELECT product_id FROM products ORDER BY product_id LIMIT 12')]
    async def batch():
        await c.fetch(BATCH, ids)
    async def n_plus_1():
        for pid in ids:
            await c.fetchval(ONE, pid)
    for name, fn, trips in (('批次（現行）', batch, 1), ('N+1', n_plus_1, len(ids))):
        await fn()  # 暖機
        ts = []
        for _ in range(200):
            t = time.perf_counter(); await fn(); ts.append((time.perf_counter() - t) * 1000)
        print(f'{name:8s} 每頁往返 {trips:2d} 趟  中位數 {statistics.median(ts):7.2f} ms  p95 {sorted(ts)[189]:7.2f} ms')
    await c.close()

asyncio.run(main())
