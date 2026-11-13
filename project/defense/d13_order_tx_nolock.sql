-- 下單交易：1–3 個品項，每項數量 1–2；商品依 hot80 分布挑選（重複商品由 place_order 合併）
-- 與 pg_hot80.sql 一起跑（pgbench -f pg_hot80.sql@8 -f order_tx.sql@2），觀察計數與扣庫存搶鎖
-- 庫存不足時 place_order 回傳 NULL、整筆不成立；跑前可用 UPDATE products SET stock = 1000000 避免賣光
\set customer random(1, 50000)
\set n random(1, 3)
\set s random(1, 100)
\if :s <= 80
  \set p1 random(1, 5)
\else
  \set p1 random(6, 1000)
\endif
\set s random(1, 100)
\if :s <= 80
  \set p2 random(1, 5)
\else
  \set p2 random(6, 1000)
\endif
\set s random(1, 100)
\if :s <= 80
  \set p3 random(1, 5)
\else
  \set p3 random(6, 1000)
\endif
\set q1 random(1, 2)
\set q2 random(1, 2)
\set q3 random(1, 2)
SELECT place_order_nolock(:customer, (ARRAY[:p1, :p2, :p3])[1:(:n)], (ARRAY[:q1, :q2, :q3])[1:(:n)]);
