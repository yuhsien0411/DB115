#!/usr/bin/env python3
"""防10：後台商品頁在 READ COMMITTED 下的「讀偏斜」（跑在 q01_defense）。
server.py 的後台商品頁先查 products（拿 stock），再另查一次 pending 訂單算「已售」。
不變式：每件商品 stock + 已售 = 庫存重設值（q01_defense 在 d04 設為 1,000,000）。
用兩條連線固定先後順序，讓一筆訂單剛好在兩條 SQL 之間提交，看畫面會算出什麼。"""
import asyncio, asyncpg

PID = 1
STOCK_Q = 'SELECT stock FROM products WHERE product_id = $1'
SOLD_Q = """SELECT coalesce(sum(i.quantity), 0) FROM orders o JOIN order_items i USING (order_id)
            WHERE o.status = 'pending' AND i.product_id = $1"""

async def trial(iso):
    admin = await asyncpg.connect(database='q01_defense')
    buyer = await asyncpg.connect(database='q01_defense')
    tx = admin.transaction(isolation=iso); await tx.start()
    stock = await admin.fetchval(STOCK_Q, PID)                         # 後台第 1 條 SQL
    oid = await buyer.fetchval('SELECT place_order(1, ARRAY[$1::int], ARRAY[3])', PID)  # 另一位顧客下單 3 件並提交
    sold = await admin.fetchval(SOLD_Q, PID)                           # 後台第 2 條 SQL
    await tx.commit()
    truth = await buyer.fetchval(f'SELECT stock + ({SOLD_Q}) FROM products WHERE product_id = $1', PID)
    print(f'{iso:16s} 畫面 stock={stock:,}  已售={sold:,}  stock+已售={stock + sold:,}  '
          f'（實際不變式 {truth:,}；中間成立訂單 #{oid}）→ {"✗ 對不上" if stock + sold != truth else "✓ 一致"}')
    await admin.close(); await buyer.close()

async def main():
    await trial('read_committed')
    await trial('repeatable_read')

asyncio.run(main())
