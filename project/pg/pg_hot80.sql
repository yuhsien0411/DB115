-- 5 件占期望 80%，其餘 995 件占 20%；保存實際商品分布核對。
\set selector random(1,100)
\if :selector <= 80
  \set pid random(1,5)
\else
  \set pid random(6,1000)
\endif
UPDATE products
SET view_count = view_count + 1
WHERE product_id = :pid;
