-- 防03：改價格之後，歷史訂單金額會不會跟著變？（跑在 q01_defense，最後 ROLLBACK）
\set oid 1000407
BEGIN;
-- 改價前：訂單存的金額、明細存的成交價、以及「若用 join 現價去算」會得到的金額
SELECT o.order_id, o.amount AS 存下的金額,
       sum(i.quantity * i.unit_price) AS 用成交價加總,
       sum(i.quantity * p.price)      AS 用現價加總_錯誤做法
FROM orders o JOIN order_items i USING (order_id) JOIN products p USING (product_id)
WHERE o.order_id = :oid GROUP BY 1,2;
SELECT i.line_no, i.product_id, i.quantity, i.unit_price AS 成交價, p.price AS 現價
FROM order_items i JOIN products p USING (product_id) WHERE i.order_id = :oid ORDER BY 1;
-- 把這張訂單裡的所有商品漲價一倍
UPDATE products SET price = price * 2
WHERE product_id IN (SELECT product_id FROM order_items WHERE order_id = :oid);
-- 改價後：再查一次
SELECT o.order_id, o.amount AS 存下的金額,
       sum(i.quantity * i.unit_price) AS 用成交價加總,
       sum(i.quantity * p.price)      AS 用現價加總_錯誤做法
FROM orders o JOIN order_items i USING (order_id) JOIN products p USING (product_id)
WHERE o.order_id = :oid GROUP BY 1,2;
ROLLBACK;
