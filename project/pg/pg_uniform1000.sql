\set pid random(1,1000)
UPDATE products
SET view_count = view_count + 1
WHERE product_id = :pid;
