-- 防04 延伸：統計資訊過期時 estimated 與 actual 差多少（跑在 q01_defense）
-- 情境：schema.sql 重跑後 pending 訂單是 0，之後網站／壓測下單產生大量 pending，但 autoanalyze 還沒觸發
-- （orders 100 萬列，autoanalyze 門檻約 50 + 10% × 100 萬 ≈ 10 萬筆變動，5,000 筆不會觸發）
UPDATE products SET stock = 1000000;
SELECT count(place_order((random()*49999)::int + 1, ARRAY[(random()*999)::int + 1], ARRAY[1]))
FROM generate_series(1, 5000);
\echo '== ANALYZE 之前'
EXPLAIN (ANALYZE) SELECT i.product_id, sum(i.quantity)::bigint FROM orders o JOIN order_items i USING (order_id)
WHERE o.status = 'pending' GROUP BY 1;
ANALYZE orders;
\echo '== ANALYZE 之後'
EXPLAIN (ANALYZE) SELECT i.product_id, sum(i.quantity)::bigint FROM orders o JOIN order_items i USING (order_id)
WHERE o.status = 'pending' GROUP BY 1;
