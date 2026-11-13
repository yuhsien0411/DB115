#!/usr/bin/env python3
"""防11：最後一件商品被 200 人同時搶（跑在 q01_defense）。
每一回合：把商品 2 的庫存設成 1，200 條連線同時呼叫一次下單（各買 1 件），數成立了幾張訂單、最後庫存剩多少。
place_order（正本，有 FOR UPDATE） vs place_order_nolock（只拿掉 FOR UPDATE，其餘一字不改），各 5 回合。"""
import asyncio, asyncpg

PID, N, ROUNDS = 2, 200, 5

async def one_round(conns, fn):
    await conns[0].execute('UPDATE products SET stock = 1 WHERE product_id = $1', PID)
    gate = asyncio.Event()
    async def buy(c, cust):
        await gate.wait()
        return await c.fetchval(f'SELECT {fn}($1, ARRAY[$2::int], ARRAY[1])', cust, PID)
    tasks = [asyncio.create_task(buy(c, i + 1)) for i, c in enumerate(conns)]
    await asyncio.sleep(0.2); gate.set()
    oids = [o for o in await asyncio.gather(*tasks) if o is not None]
    stock = await conns[0].fetchval('SELECT stock FROM products WHERE product_id = $1', PID)
    return len(oids), stock

async def main():
    conns = await asyncio.gather(*(asyncpg.connect(database='q01_defense') for _ in range(N)))
    for fn in ('place_order', 'place_order_nolock'):
        res = [await one_round(conns, fn) for _ in range(ROUNDS)]
        print(f'{fn:20s} 庫存 1、{N} 人同時買：' +
              '、'.join(f'成立 {n} 張／剩 {s}' for n, s in res))
    await conns[0].execute('UPDATE products SET stock = 1000000 WHERE product_id = $1', PID)
    await asyncio.gather(*(c.close() for c in conns))

asyncio.run(main())
