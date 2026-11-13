-- =====================================================================
--  資料庫管理系統｜教學用種子資料集  seed_shop_1m.sql
--  用途：W8 索引 / W9 查詢處理與優化 / W10–W11 交易與並行
--  規模：orders 100 萬列、order_items 約 200 萬列、customers 5 萬列
--  特色：資料分布刻意設計，讓每個教學重點都「一定看得出來」
--  使用：psql "<你的連線字串>" -f seed_shop_1m.sql
--  提醒：Neon 免費方案每個 project 上限 0.5 GB。
--        本腳本載入後約 234 MB，加上下方示範索引約 262 MB —— 可安全放入。
--        若還要留空間給學生自己實驗，把下方 N_ORDERS 改成 300000。
--  說明：本檔已在 PostgreSQL 16 實測，下方每個教學點的執行計畫皆為實際輸出。
--  2026-09-02 複測紀錄：載入正常，列數與本檔記載完全一致
--    （completed 899754 / shipped 70241 / cancelled 30005），setseed(0.42) 可重現性成立。
--    另補測三個數字：三個索引的 B+ 樹高皆為 3；orders 表 57 MB、含索引 107 MB；
--    2 個索引使 30 萬列插入由 376 ms 變 3726 ms（約 10 倍）。
--    ⚠ 教學點 D 原寫法有誤，已更正（見下）。
--    ⚠ 本資料集【不適用】於圖／推薦類實驗，理由見檔尾【資料集限制】。
-- =====================================================================

\set N_CUSTOMERS 50000
\set N_ORDERS    1000000

\timing on

-- 固定亂數種子：確保每位學生跑出來的資料完全一致。
-- 這在非同步課很重要——你的講解影片裡的 EXPLAIN 輸出，學生才對得上。
SELECT setseed(0.42);

DROP TABLE IF EXISTS order_items, orders, customers, products CASCADE;

-- ---------------------------------------------------------------------
-- 1. customers：5 萬列。city 刻意做成高度傾斜（見下方教學點 C）
-- ---------------------------------------------------------------------
CREATE TABLE customers (
    customer_id  int PRIMARY KEY,
    name         text NOT NULL,
    email        text NOT NULL,
    city         text NOT NULL,
    signup_date  date NOT NULL
);

-- 注意兩個寫法上的坑（也是不錯的課堂陷阱題）：
--  1. 若在 CASE 的每個 WHEN 各寫一次 random()，每次都會重新取值，比例會跑掉。
--  2. 因此先在子查詢取一次 v；子查詢尾端的 OFFSET 0 是最佳化柵欄，
--     用來阻止 PostgreSQL 把子查詢展平後又把 random() 展開成多次計算。
INSERT INTO customers
SELECT
    g,
    'Customer ' || g,
    'user' || g || '@example.com',
    -- 60% 台北、20% 台中、15% 高雄、5% 花蓮 → 刻意的傾斜分布
    CASE
        WHEN v < 0.60 THEN '台北'
        WHEN v < 0.80 THEN '台中'
        WHEN v < 0.95 THEN '高雄'
        ELSE '花蓮'
    END,
    DATE '2021-01-01' + (v2 * 1400)::int
FROM (
    SELECT g, random() AS v, random() AS v2
    FROM generate_series(1, :N_CUSTOMERS) g
    OFFSET 0
) s;

-- ---------------------------------------------------------------------
-- 2. products：1 千列
-- ---------------------------------------------------------------------
CREATE TABLE products (
    product_id  int PRIMARY KEY,
    name        text NOT NULL,
    category    text NOT NULL,
    price       numeric(10,2) NOT NULL,
    stock       int NOT NULL          -- 給 W10 超賣實驗用
);

INSERT INTO products
SELECT
    g,
    'Product ' || g,
    (ARRAY['3C','服飾','food','書籍','家電'])[1 + (random() * 4)::int],
    (random() * 4900 + 100)::numeric(10,2),
    100                                -- 每項商品初始庫存 100
FROM generate_series(1, 1000) g;

-- ---------------------------------------------------------------------
-- 3. orders：100 萬列
--    status 刻意做成 90 / 7 / 3 的極端分布 → 教學點 A
-- ---------------------------------------------------------------------
CREATE TABLE orders (
    order_id     int PRIMARY KEY,
    customer_id  int NOT NULL REFERENCES customers(customer_id),
    order_date   date NOT NULL,
    status       text NOT NULL,
    amount       numeric(10,2) NOT NULL
);

INSERT INTO orders
SELECT
    g,
    1 + (v2 * (:N_CUSTOMERS - 1))::int,
    DATE '2023-01-01' + (v3 * 900)::int,
    CASE
        WHEN v < 0.90 THEN 'completed'   -- 90%
        WHEN v < 0.97 THEN 'shipped'     --  7%
        ELSE 'cancelled'                 --  3%
    END,
    (v4 * 9900 + 100)::numeric(10,2)
FROM (
    SELECT g, random() AS v, random() AS v2, random() AS v3, random() AS v4
    FROM generate_series(1, :N_ORDERS) g
    OFFSET 0
) s;

-- ---------------------------------------------------------------------
-- 4. order_items：每張訂單 1–3 列，約 200 萬列
-- ---------------------------------------------------------------------
CREATE TABLE order_items (
    order_id    int NOT NULL REFERENCES orders(order_id),
    line_no     int NOT NULL,
    product_id  int NOT NULL REFERENCES products(product_id),
    quantity    int NOT NULL,
    unit_price  numeric(10,2) NOT NULL,
    PRIMARY KEY (order_id, line_no)
);

INSERT INTO order_items
SELECT
    o.order_id,
    ln.line_no,
    1 + (random() * 999)::int,
    1 + (random() * 3)::int,
    (random() * 4900 + 100)::numeric(10,2)
FROM orders o
CROSS JOIN LATERAL (
    SELECT generate_series(1, 1 + (random() * 2)::int) AS line_no
) ln;

ANALYZE;

-- =====================================================================
--  以下為建議的課堂示範腳本（不會自動執行，請學生逐段自行嘗試）
-- =====================================================================
--
-- 【教學點 A】為什麼建了索引還是走 Seq Scan？（選擇率）
--   CREATE INDEX idx_orders_status ON orders(status);
--   ANALYZE orders;
--   EXPLAIN ANALYZE SELECT * FROM orders WHERE status = 'completed';
--     → 實測輸出：Seq Scan（rows=899754）。索引明明存在，planner 就是不用它。
--   EXPLAIN ANALYZE SELECT * FROM orders WHERE status = 'cancelled';
--     → 實測輸出：Bitmap Heap Scan（rows=30005）。改走索引了。
--   → 同一個索引、同一張表，只因為選擇率不同（90% vs 3%），planner 就換了策略。
--     這是「索引不是建了就會用」最有說服力的一次示範。
--   → 順帶可問：為什麼是 Bitmap Heap Scan 而不是單純的 Index Scan？
--     （因為 3 萬列分散在很多 page 上，先蒐集再依實體順序讀比較划算）
--
-- 【教學點 B】複合索引的最左前綴
--   CREATE INDEX idx_orders_cust_date ON orders(customer_id, order_date);
--   EXPLAIN ANALYZE SELECT * FROM orders WHERE customer_id = 42;
--     → 實測：Bitmap Heap Scan，用得到索引（customer_id 是最左欄）
--   EXPLAIN ANALYZE SELECT * FROM orders WHERE order_date = DATE '2024-05-01';
--     → 實測：Parallel Seq Scan，用不到（跳過了最左欄）
--   EXPLAIN ANALYZE SELECT * FROM orders WHERE customer_id = 42
--                     AND order_date > DATE '2024-01-01';                           -- 兩欄都用到
--
-- 【教學點 C】JOIN 演算法的切換（W9）
--   EXPLAIN ANALYZE
--     SELECT c.city, count(*) FROM orders o JOIN customers c USING (customer_id)
--     GROUP BY c.city;   -- 實測：Hash Join（把小的 customers 做成 hash table）
--   EXPLAIN ANALYZE
--     SELECT * FROM orders o JOIN customers c USING (customer_id)
--     WHERE o.order_id = 12345;  -- 實測：Nested Loop + Index Scan（只有一列）
--   → 讓學生自己找出「什麼時候 planner 會改用哪一種 join」。
--
-- 【教學點 D】統計資訊過期會怎樣（W9）
--   ⚠ 2026-09-02 更正：原本這裡寫的 DELETE 會失敗！
--     DELETE FROM orders WHERE status = 'cancelled';
--     → ERROR: update or delete on table "orders" violates foreign key constraint
--              "order_items_order_id_fkey" on table "order_items"
--     order_items 仍參照著這些訂單，所以刪不掉。改用 UPDATE 改變分布即可，
--     而且效果比 DELETE 更好（見下方 ★）。
--
--   UPDATE orders SET status = 'completed' WHERE status = 'cancelled';   -- 分布大變，但不告訴資料庫
--   EXPLAIN ANALYZE SELECT * FROM orders WHERE status = 'cancelled';
--     → 實測：估計 rows≈3 萬（舊統計），actual rows=0。兩組數字整個裂開。
--   ANALYZE orders;
--   EXPLAIN ANALYZE SELECT * FROM orders WHERE status = 'cancelled';
--     → 實測：計畫整個換掉，Bitmap Heap Scan → Index Scan，估計 rows=1。
--
--   ★ 這個版本多送一個教學點：未 ANALYZE 那次的輸出裡，
--     內層 Bitmap Index Scan 的 actual rows=30005，外層 Bitmap Heap Scan 的 actual rows=0。
--     索引裡還留著 3 萬筆指向舊版本的項目（MVCC，尚未 VACUUM），
--     回到 heap 做 recheck 才全部被剔除。這是 W11 的絕佳伏筆。
--
--   ⚠ 注意：估計列數是 ANALYZE 抽樣算出來的，每次執行都會略有不同
--     （實測三次分別為 29167 / 31033 / 31966）。講解時要說明這一點，
--     否則學生會以為自己做錯了。
--
-- 【教學點 E】W10 超賣實驗（需要兩個連線！）
--   Session 1:  BEGIN;
--               SELECT stock FROM products WHERE product_id = 1;      -- 讀到 100
--   Session 2:  BEGIN;
--               SELECT stock FROM products WHERE product_id = 1;      -- 也讀到 100
--   Session 1:  UPDATE products SET stock = 99 WHERE product_id = 1;  COMMIT;
--   Session 2:  UPDATE products SET stock = 99 WHERE product_id = 1;  COMMIT;
--   → 賣掉兩件，庫存只少一件：更新遺失（Lost Update）。
--   修法一：SELECT ... FOR UPDATE
--   修法二：SET TRANSACTION ISOLATION LEVEL SERIALIZABLE（觀察序列化失敗與重試）
--   修法三：UPDATE products SET stock = stock - 1 WHERE product_id = 1 AND stock > 0;
--   → 請學生比較三者的正確性與吞吐量代價。
-- =====================================================================

-- =====================================================================
--  【資料集限制】（2026-09-02 實測後補記）
-- =====================================================================
--  ⚠ 本資料集不適用於「圖查詢／推薦」類的實驗（實作單 5、對照題 5）。
--
--  原因：order_items 的 product_id 是均勻隨機指派的（1 + random()*999），
--  所以顧客–商品二部圖幾乎是完全圖：
--    · 單一商品的買家數 = 977（1000 位隨機買家中的去重值）
--    · 從任一商品出發，【深度 2 就已經連到全部 1000 種商品】
--  因此深度 2、3、4 的結果集完全相同，「深度成本」的實驗做不出任何差異。
--
--  這是分布問題不是規模問題——把資料放大十倍也不會改善。
--  圖類實驗請改用具有社群結構的資料集（主文件 §9.10 建議的 Olist、MovieLens），
--  或另外產生帶群聚性的購買關係。
--
--  （其餘教學點 A、B、C、D、E 均已複測通過。）
