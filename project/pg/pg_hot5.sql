-- 補充組：100% 流量只打 5 件，不可與 80/20 主組混稱。
\set pid random(1,5)
UPDATE products
SET view_count = view_count + 1
WHERE product_id = :pid;
