#!/usr/bin/env python3
"""防07：view_flush_log(run_id, flushed_at) 這個索引讓什麼變快、讓什麼變慢（跑在 q01_defense）。
兩張一樣的表、一樣先灌入正式資料的 39 萬列：A 只有主鍵，B 主鍵 + 複合索引（＝現行設計）。
寫入：模擬回寫 worker，每次回寫插 1,000 列（每件商品一列），A、B 交替各 300 次。
讀取：依 run_id 取一輪的回寫時間軸（crash_test／對帳的查法），各 200 次。"""
import asyncio, statistics, time, asyncpg

async def main():
    c = await asyncpg.connect(database='q01_defense')
    await c.execute("""
      DROP TABLE IF EXISTS vfl_a, vfl_b;
      CREATE TABLE vfl_a (flush_id bigserial PRIMARY KEY, run_id text NOT NULL,
                          flushed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                          product_id int NOT NULL, snapshot bigint NOT NULL);
      CREATE TABLE vfl_b (LIKE vfl_a INCLUDING ALL);
      CREATE INDEX ON vfl_b (run_id, flushed_at);
      INSERT INTO vfl_a (run_id, flushed_at, product_id, snapshot) SELECT run_id, flushed_at, product_id, snapshot FROM view_flush_log;
      INSERT INTO vfl_b (run_id, flushed_at, product_id, snapshot) SELECT run_id, flushed_at, product_id, snapshot FROM view_flush_log;
""")
    for t in ('vfl_a', 'vfl_b'):
        await c.execute(f'VACUUM ANALYZE {t}')
    ins = "INSERT INTO {t}(run_id, product_id, snapshot) SELECT $1, g, g FROM generate_series(1, 1000) g"
    w = {'vfl_a': [], 'vfl_b': []}
    for i in range(300):
        for t in (('vfl_a', 'vfl_b') if i % 2 == 0 else ('vfl_b', 'vfl_a')):
            s = time.perf_counter(); await c.execute(ins.format(t=t), f'defense07_{i // 20}')
            w[t].append((time.perf_counter() - s) * 1000)
    q = "SELECT flushed_at, product_id, snapshot FROM {t} WHERE run_id = $1 ORDER BY flushed_at"
    r = {'vfl_a': [], 'vfl_b': []}
    for _ in range(200):
        for t in r:
            s = time.perf_counter(); await c.fetch(q.format(t=t), 'redis_hot80_c200_r1_20261106161005')
            r[t].append((time.perf_counter() - s) * 1000)
    sizes = await c.fetch("""SELECT relname, pg_size_pretty(pg_relation_size(oid)) AS heap,
                                    pg_size_pretty(pg_indexes_size(oid)) AS idx
                             FROM pg_class WHERE relname IN ('vfl_a','vfl_b') ORDER BY 1""")
    names = {'vfl_a': 'A 只有主鍵', 'vfl_b': 'B 主鍵＋(run_id, flushed_at)'}
    print('寫入：每次回寫插 1,000 列，各 300 次（ms）')
    for t in w:
        print(f'  {names[t]:28s} 中位數 {statistics.median(w[t]):6.2f}  p95 {sorted(w[t])[284]:6.2f}')
    print('讀取：取一輪 19,000 列回寫時間軸，各 200 次（ms）')
    for t in r:
        print(f'  {names[t]:28s} 中位數 {statistics.median(r[t]):6.2f}  p95 {sorted(r[t])[189]:6.2f}')
    print('大小（灌完 39 萬＋30 萬列後）')
    for s in sizes:
        print(f"  {names[s['relname']]:28s} 表 {s['heap']}  全部索引 {s['idx']}")
    for t in ('vfl_a', 'vfl_b'):
        plan = await c.fetch('EXPLAIN ' + q.format(t=t).replace('$1', "'redis_hot80_c200_r1_20261106161005'"))
        print(f'  {names[t]} 計畫：{plan[0][0].strip()}' + (f' / {plan[1][0].strip()}' if len(plan) > 1 else ''))
    await c.close()

asyncio.run(main())
