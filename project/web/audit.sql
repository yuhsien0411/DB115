\set ON_ERROR_STOP on
\pset pager off
-- 唯讀對帳：不修改商品、訂單、Redis 或正式實驗結果。
-- D37 起網站直接用 public。對帳範圍是庫存重設（schema.sql）後成立的訂單，也就是 status = 'pending'
-- （網站與下單實驗都經 place_order() 成立；seed 的 100 萬筆歷史訂單沒有這個狀態，不在範圍內）。
-- 庫存檢查適用目前規則：重設時每件 100、之後無補貨／取消／手動改庫存。
-- psql 變數代替暫存 view（READ ONLY 交易不能建物件）
\set recent '(SELECT * FROM public.orders WHERE status = ''pending'')'
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT clock_timestamp() AS audited_at,
       (SELECT count(*) FROM public.products) AS products,
       (SELECT count(*) FROM :recent) AS pending_orders,
       (SELECT count(*) FROM public.order_requests) AS web_orders,
       (SELECT count(*) FROM public.cart_items) AS cart_lines,  -- 購物車（D38）：加入購物車不扣庫存，不影響下方庫存對帳
       (SELECT count(*) FROM public.order_items i JOIN :recent USING (order_id)) AS pending_items;

WITH bad_totals AS (
    SELECT o.order_id
    FROM :recent o
    LEFT JOIN public.order_items i USING (order_id)
    GROUP BY o.order_id, o.amount
    HAVING count(i.line_no) = 0
        OR o.amount <> coalesce(sum(i.quantity * i.unit_price), 0)
), sold AS (
    SELECT i.product_id, sum(i.quantity) AS n
    FROM :recent o JOIN public.order_items i USING (order_id) GROUP BY 1
), bad_stock AS (
    SELECT p.product_id
    FROM public.products p LEFT JOIN sold s USING (product_id)
    WHERE p.stock + coalesce(s.n, 0) <> 100
), checks AS (
    SELECT '01 商品非負值' AS check_name, count(*) AS problem_count
    FROM public.products WHERE stock < 0 OR price < 0 OR view_count < 0
    UNION ALL
    SELECT '02 訂單總額與明細一致且非空', count(*) FROM bad_totals
    UNION ALL
    SELECT '03 庫存加已售數量等於初始100', count(*) FROM bad_stock
    UNION ALL
    SELECT '04 冪等紀錄都對到pending訂單', count(*)
    FROM public.order_requests r LEFT JOIN :recent o USING (order_id)
    WHERE o.order_id IS NULL
    UNION ALL
    SELECT '05 明細均有商品', count(*)
    FROM :recent o JOIN public.order_items i USING (order_id)
    LEFT JOIN public.products p USING (product_id)
    WHERE p.product_id IS NULL
    UNION ALL
    SELECT '06 訂單均有顧客', count(*)
    FROM :recent o LEFT JOIN public.customers c USING (customer_id)
    WHERE c.customer_id IS NULL
    UNION ALL
    SELECT '07 回寫快照未超過PG已保存值', count(*)
    FROM public.view_flush_log l JOIN public.products p USING (product_id)
    WHERE l.run_id LIKE 'web\_%' AND l.snapshot > p.view_count
)
SELECT check_name, problem_count,
       CASE WHEN problem_count = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM checks ORDER BY check_name;

-- 最近五張訂單的金額對帳，讓稽核者可與畫面逐筆核對。
SELECT o.order_id, o.customer_id, o.order_date, o.amount, r.request_id IS NOT NULL AS from_web,
       count(i.line_no) AS lines, sum(i.quantity) AS total_quantity,
       sum(i.quantity * i.unit_price) AS calculated_amount
FROM :recent o
LEFT JOIN public.order_requests r USING (order_id)
LEFT JOIN public.order_items i USING (order_id)
GROUP BY o.order_id, o.customer_id, o.order_date, o.amount, r.request_id
ORDER BY o.order_id DESC LIMIT 5;

ROLLBACK;
