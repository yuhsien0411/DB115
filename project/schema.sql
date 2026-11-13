-- 題01：專題專用資料庫 q01（由課程 shop 複製，不動 shop 本身）
-- 建立：createdb -T shop q01 && psql -X -v ON_ERROR_STOP=1 -d q01 -f project/schema.sql
-- 重跑會把計數歸零、清掉下單實驗與網站的訂單、庫存還原為 100
-- 前四張表沿用 seed_shop_1m.sql（customers 5 萬、products 1,000、orders 100 萬、order_items 約 200 萬）。
-- 網站（web/server.py）也直接用這裡的 public 表，並呼叫同一個 place_order()（D37）；購物車存在 cart_items（D38）。

-- 可重跑：清掉下單實驗與網站新增的訂單（status = 'pending'，seed 沒有這個狀態），庫存還原為 seed 的 100
-- 購物車也一併清空並重建；先 DROP 再 CREATE，重跑種子檔後指向四張表的外鍵才會回來（講評 p.28）
DROP TABLE IF EXISTS cart_items;
DROP TABLE IF EXISTS order_requests;
DELETE FROM order_items i USING orders o WHERE i.order_id = o.order_id AND o.status = 'pending';
DELETE FROM orders WHERE status = 'pending';
UPDATE products SET stock = 100;

-- 金額沿用 seed 的 numeric(10,2)（兩位小數），不另外轉型（D36）

-- 可重跑：先移除本檔新增的結構再重建（計數歸零）
DROP TABLE IF EXISTS view_flush_log;
ALTER TABLE products DROP COLUMN IF EXISTS view_count;

-- 對照 A：PostgreSQL 直接計數；對照 B：Redis 快照回寫的正本也存在這裡
ALTER TABLE products ADD COLUMN view_count bigint NOT NULL DEFAULT 0 CHECK (view_count >= 0);

-- 對照 B：每次回寫的快照紀錄，用來畫故障時間軸與對帳
-- 回寫採「絕對累計快照」：view_count = GREATEST(view_count, snapshot)
-- 同一快照重送不會重複加，舊快照晚到也不會讓計數倒退
CREATE TABLE view_flush_log (
    flush_id    bigserial   PRIMARY KEY,
    run_id      text        NOT NULL,
    flushed_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    product_id  int         NOT NULL REFERENCES products(product_id),
    snapshot    bigint      NOT NULL CHECK (snapshot >= 0)
);
CREATE INDEX ON view_flush_log (run_id, flushed_at);

ANALYZE products;

-- 下單交易（補充實驗：計數 UPDATE 與扣庫存搶同一列的行鎖）
-- 新訂單編號從 seed 最大值之後開始，用序列產生，避免併發衝突
DROP FUNCTION IF EXISTS place_order(int, int[], int[]);
DROP SEQUENCE IF EXISTS q01_order_id_seq;
CREATE SEQUENCE q01_order_id_seq;
SELECT setval('q01_order_id_seq', (SELECT max(order_id) FROM orders));

-- 一個交易內：檢查參數 → 合併重複商品 → 鎖定商品列（依 product_id 排序以避免死結）→ 檢查庫存 → 寫 orders、order_items → 扣庫存
-- 任一商品不存在或庫存不足就整筆不成立，回傳 NULL（不丟錯，pgbench 才不會中斷該 client）
CREATE FUNCTION place_order(p_customer int, p_products int[], p_qty int[])
RETURNS int LANGUAGE plpgsql AS $$
DECLARE
    v_pids int[];
    v_qtys int[];
    v_locked int;
    v_order int;
BEGIN
    IF coalesce(cardinality(p_products), 0) = 0
       OR cardinality(p_products) <> coalesce(cardinality(p_qty), -1)
       OR EXISTS (SELECT 1 FROM unnest(p_qty) AS q WHERE q IS NULL OR q <= 0) THEN
        RAISE EXCEPTION 'place_order: 商品與數量陣列須等長、非空，數量須為正整數';
    END IF;

    SELECT array_agg(pid ORDER BY pid), array_agg(qty ORDER BY pid) INTO v_pids, v_qtys
    FROM (SELECT pid, sum(qty)::int AS qty
          FROM unnest(p_products, p_qty) AS x(pid, qty) GROUP BY pid) g;

    -- 先無條件鎖住所有要求的商品列；之後的檢查與寫入都讀同一份已鎖定的資料
    SELECT count(*) INTO v_locked
    FROM (SELECT 1 FROM products WHERE product_id = ANY (v_pids)
          ORDER BY product_id FOR UPDATE) l;
    IF v_locked <> cardinality(v_pids) THEN
        RETURN NULL;  -- 有商品不存在
    END IF;
    IF EXISTS (SELECT 1 FROM products p JOIN unnest(v_pids, v_qtys) AS x(pid, qty)
               ON p.product_id = x.pid WHERE p.stock < x.qty) THEN
        RETURN NULL;  -- 庫存不足
    END IF;

    v_order := nextval('q01_order_id_seq');
    INSERT INTO orders (order_id, customer_id, order_date, status, amount)
    SELECT v_order, p_customer, current_date, 'pending', sum(p.price * x.qty)
    FROM unnest(v_pids, v_qtys) AS x(pid, qty) JOIN products p ON p.product_id = x.pid;

    INSERT INTO order_items (order_id, line_no, product_id, quantity, unit_price)
    SELECT v_order, x.ord, x.pid, x.qty, p.price
    FROM unnest(v_pids, v_qtys) WITH ORDINALITY AS x(pid, qty, ord)
    JOIN products p ON p.product_id = x.pid;

    UPDATE products p SET stock = p.stock - x.qty
    FROM unnest(v_pids, v_qtys) AS x(pid, qty)
    WHERE p.product_id = x.pid;
    RETURN v_order;
END $$;

-- 網站結帳的冪等紀錄（D37）：同一個 request_id 重送只會對到同一筆訂單。
-- 另開一張表，不在 orders 加欄位：place_order() 與 seed 的 orders 都不必改，網站帳號對 orders 也維持只能新增。
-- request_id → order_id；order_id → request_id（兩者都是候選鍵，BCNF）
CREATE TABLE order_requests (
    request_id  uuid PRIMARY KEY,
    order_id    int  NOT NULL UNIQUE REFERENCES orders(order_id)
);

-- 購物車（D38）：取代原本存在瀏覽器 localStorage 的購物車（說明書 11/5 版：購物車放 PostgreSQL 或 Redis）。
-- 一位顧客對一件商品只有一列；加入購物車不扣庫存，結帳成功時在同一個交易刪掉已下單的商品。
-- added_price 是加入當下的價格：之後調價也不跟著改，從現價算不出來，是另一件事實，不是反正規化。
-- customer_id, product_id → quantity, added_price, added_at   （唯一的候選鍵是主鍵，BCNF）
CREATE TABLE cart_items (
    customer_id  int           NOT NULL REFERENCES customers(customer_id),
    product_id   int           NOT NULL REFERENCES products(product_id),
    quantity     int           NOT NULL CHECK (quantity BETWEEN 1 AND 10000),
    added_price  numeric(10,2) NOT NULL CHECK (added_price >= 0),
    added_at     timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (customer_id, product_id)
);
