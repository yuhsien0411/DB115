-- 防02：orders.amount 是刻意多存的「明細加總」，誰在維持一致？（唯讀，跑在 q01）
\timing on
-- 系統自己建立的訂單（place_order 寫的，status = 'pending'）：amount 是否等於明細加總
SELECT o.status, count(*) AS 訂單數,
       count(*) FILTER (WHERE o.amount = s.line_sum) AS 相等,
       count(*) FILTER (WHERE o.amount <> s.line_sum) AS 不相等
FROM orders o
JOIN (SELECT order_id, sum(quantity * unit_price) AS line_sum FROM order_items GROUP BY 1) s USING (order_id)
GROUP BY o.status ORDER BY 1;
-- 種子訂單的 amount 是亂數（講評 p.27），舉一筆給人看
SELECT o.order_id, o.status, o.amount, sum(i.quantity * i.unit_price) AS 明細加總
FROM orders o JOIN order_items i USING (order_id)
WHERE o.order_id IN (1, (SELECT max(order_id) FROM orders WHERE status='pending'))
GROUP BY 1,2,3 ORDER BY 1;
-- view_count 的正本在哪裡、Redis 那份何時寫回：看最近一次正式 run 的回寫紀錄
SELECT run_id, count(DISTINCT flushed_at) AS 回寫次數, min(flushed_at), max(flushed_at)
FROM view_flush_log WHERE run_id = 'redis_hot80_c200_r1_20261106161005' GROUP BY 1;
