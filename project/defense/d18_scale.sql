-- 防18：資料量放大時第一個壞掉的地方（跑在 q01_defense）
-- 做法：把 orders 複製成 10 倍（1,000 萬列，顧客分布不變），量網站兩條「沒有索引可用、只能全表掃描」的查詢，
-- 1 倍 vs 10 倍；100 倍用線性外推（全表掃描的成本與列數成正比——這是推估，不是量到的）。
\timing on
DROP TABLE IF EXISTS orders_10x;
CREATE TABLE orders_10x AS
  SELECT o.order_id + k * 2000000 AS order_id, o.customer_id, o.order_date, o.status, o.amount
  FROM orders o, generate_series(0, 9) k;
ALTER TABLE orders_10x ADD PRIMARY KEY (order_id);
VACUUM ANALYZE orders_10x;
SELECT 'orders' AS 表, count(*) AS 列數, pg_size_pretty(pg_relation_size('orders')) FROM orders
UNION ALL SELECT 'orders_10x', count(*), pg_size_pretty(pg_relation_size('orders_10x')) FROM orders_10x;
\echo '== 前台「我的訂單」（server.py 第 370 行；orders.customer_id 沒有索引）'
EXPLAIN (ANALYZE, TIMING OFF) SELECT * FROM orders     WHERE customer_id = 1 ORDER BY order_id DESC LIMIT 100;
EXPLAIN (ANALYZE, TIMING OFF) SELECT * FROM orders_10x WHERE customer_id = 1 ORDER BY order_id DESC LIMIT 100;
\echo '== 後台儀表板 訂單數與營收（server.py 第 326 行；自動更新每 2 秒一次）'
EXPLAIN (ANALYZE, TIMING OFF) SELECT count(*), sum(amount) FROM orders;
EXPLAIN (ANALYZE, TIMING OFF) SELECT count(*), sum(amount) FROM orders_10x;
\echo '== 對照：如果替 customer_id 加索引（10 倍資料）'
CREATE INDEX orders_10x_customer_idx ON orders_10x (customer_id);
EXPLAIN (ANALYZE, TIMING OFF) SELECT * FROM orders_10x WHERE customer_id = 1 ORDER BY order_id DESC LIMIT 100;
DROP TABLE orders_10x;
