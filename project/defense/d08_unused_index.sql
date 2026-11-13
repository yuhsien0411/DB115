-- 防08：建了索引，planner 卻不用（唯讀，跑在 q01）
\timing on
\echo '======== 情況 1：選擇率太高 —— orders_pkey（order_id）'
\echo '-- (1a) 只拿 0.1%：用索引'
EXPLAIN ANALYZE SELECT sum(amount) FROM orders WHERE order_id <= 1000;
\echo '-- (1b) 拿 90%：同一個索引，planner 改走全表掃描'
EXPLAIN ANALYZE SELECT sum(amount) FROM orders WHERE order_id <= 900000;
\echo '-- (1c) 強迫它用索引，看看是不是真的比較慢'
SET enable_seqscan = off;
EXPLAIN ANALYZE SELECT sum(amount) FROM orders WHERE order_id <= 900000;
RESET enable_seqscan;
\echo '-- planner 依據的統計資訊：orders 有幾列、幾頁'
SELECT relname, reltuples::bigint, relpages FROM pg_class WHERE relname = 'orders';

\echo '======== 情況 2：不是選擇率，是「這個運算子用不了這個索引」—— web/audit.sql 第 07 項'
\echo '-- 資料庫定序'
SELECT datcollate FROM pg_database WHERE datname = current_database();
\echo '-- (2a) audit.sql 原寫法：LIKE 前綴。planner 估 1 列（非常有選擇性），卻仍全表掃描'
EXPLAIN ANALYZE SELECT count(*) FROM view_flush_log WHERE run_id LIKE 'web\_%';
\echo '-- (2b) 把全表掃描關掉：還是用不了索引（Disabled 或成本變天價），證明不是成本問題而是能力問題'
SET enable_seqscan = off;
EXPLAIN ANALYZE SELECT count(*) FROM view_flush_log WHERE run_id LIKE 'web\_%';
RESET enable_seqscan;
\echo '-- (2c) 改寫成範圍條件（語意相同）：索引就用得上了'
EXPLAIN ANALYZE SELECT count(*) FROM view_flush_log WHERE run_id >= 'web_' AND run_id < 'web`';
