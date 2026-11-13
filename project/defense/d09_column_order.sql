-- 防09：複合索引欄位順序對調，哪些查詢會變慢（跑在 q01_defense；用 d07 建好的 vfl_a 複製兩份）
-- 現行：(run_id, flushed_at)；對調：(flushed_at, run_id)。兩張表資料完全相同（約 69 萬列、43 輪）。
DROP TABLE IF EXISTS vfl_now, vfl_swap;
CREATE TABLE vfl_now  AS SELECT * FROM vfl_a;
CREATE TABLE vfl_swap AS SELECT * FROM vfl_a;
CREATE INDEX vfl_now_idx  ON vfl_now  (run_id, flushed_at);
CREATE INDEX vfl_swap_idx ON vfl_swap (flushed_at, run_id);
VACUUM ANALYZE vfl_now; VACUUM ANALYZE vfl_swap;
SELECT count(*) AS 列數, count(DISTINCT run_id) AS 輪數 FROM vfl_now;
\set R '\'redis_hot80_c200_r1_20261106161005\''
\echo '======== Q1 取某一輪的回寫時間軸（run_id = ? ORDER BY flushed_at）'
EXPLAIN ANALYZE SELECT * FROM vfl_now  WHERE run_id = :R ORDER BY flushed_at;
EXPLAIN ANALYZE SELECT * FROM vfl_swap WHERE run_id = :R ORDER BY flushed_at;
\echo '======== Q2 某一輪中斷前後 10 秒（run_id = ? AND flushed_at BETWEEN）'
EXPLAIN ANALYZE SELECT * FROM vfl_now  WHERE run_id = :R AND flushed_at BETWEEN '2026-11-06 16:11:00+00' AND '2026-11-06 16:11:10+00';
EXPLAIN ANALYZE SELECT * FROM vfl_swap WHERE run_id = :R AND flushed_at BETWEEN '2026-11-06 16:11:00+00' AND '2026-11-06 16:11:10+00';
\echo '======== Q3 不分輪次，最近一段時間的回寫（只用 flushed_at）'
EXPLAIN ANALYZE SELECT count(*) FROM vfl_now  WHERE flushed_at BETWEEN '2026-11-06 16:11:00+00' AND '2026-11-06 16:11:10+00';
EXPLAIN ANALYZE SELECT count(*) FROM vfl_swap WHERE flushed_at BETWEEN '2026-11-06 16:11:00+00' AND '2026-11-06 16:11:10+00';
