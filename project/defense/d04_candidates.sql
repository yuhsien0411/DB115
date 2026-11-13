-- 防04 前置：網站實際發出的查詢逐一量時間，挑最慢的（唯讀，跑在 q01）
\timing on
\echo '== A 前台「我的訂單」 GET /api/store/orders?customer_id=1 （server.py 第 370 行）'
EXPLAIN (ANALYZE, BUFFERS) SELECT o.*,c.name AS customer_name FROM orders o JOIN customers c USING(customer_id) WHERE (1::int IS NULL OR customer_id=1) ORDER BY order_id DESC LIMIT 100;
\echo '== B 後台儀表板 訂單數與營收（server.py 第 326 行，自動更新每 2 秒一次）'
EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) AS orders,coalesce(sum(amount),0) AS revenue FROM orders;
\echo '== C 後台商品頁「已售」（server.py 第 166 行）'
EXPLAIN (ANALYZE, BUFFERS) SELECT i.product_id, sum(i.quantity)::bigint FROM orders o JOIN order_items i USING (order_id) WHERE o.status = 'pending' AND i.product_id = ANY(ARRAY[1,2,3,4,5,6,7,8,9,10,11,12]) GROUP BY 1;
\echo '== D 前台商品搜尋 ILIKE'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM products p WHERE ('book'='' OR name ILIKE '%'||'book'||'%' OR product_id::text='book') AND (''='' OR category='') ORDER BY product_id LIMIT 12 OFFSET 0;
