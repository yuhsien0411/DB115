-- 防01：資料只能「否證」FD，不能「證明」FD（唯讀，跑在 q01）
\timing on
-- (1) order_items：(order_id, line_no) → product_id, quantity, unit_price   主鍵保證，不必查
-- (2) 候選 FD：(order_id, product_id) → line_no   業務規則「同一張訂單同一商品只占一行」
--     place_order() 會先 GROUP BY 合併重複商品；但資料庫沒有 UNIQUE(order_id, product_id) 擋
SELECT count(*) AS 違反筆數_同單同商品多行
FROM (SELECT order_id, product_id FROM order_items
      GROUP BY 1,2 HAVING count(*) > 1) v;
-- (3) 候選 FD：product_id → unit_price   （「同一商品的成交價都一樣」）
SELECT count(*) AS 有多種成交價的商品數
FROM (SELECT product_id FROM order_items GROUP BY 1 HAVING count(DISTINCT unit_price) > 1) v;
-- (4) 舉一個反例給人看
SELECT product_id, count(DISTINCT unit_price) AS 不同成交價個數, min(unit_price), max(unit_price)
FROM order_items WHERE product_id = 1 GROUP BY 1;
SELECT product_id, price AS 現價 FROM products WHERE product_id = 1;
