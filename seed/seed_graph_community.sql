-- =====================================================================
--  seed_graph_community.sql   （對照題 5 專用：推薦查詢 / 圖模型）
--  建立：2026-09-02   已在 PostgreSQL 16.13 實測，下方數字為實際輸出
-- ---------------------------------------------------------------------
--  為什麼需要這個檔案
--    seed_shop_1m.sql 的顧客與商品是「均勻隨機配對」，
--    深度 2 就已連通全部 1000 種商品，深度 2/3/4 結果完全相同（見 D71）。
--    題 5 的核心是「深度 2、3、4 各測一次」，用那份資料做不出任何差異。
--
--  ⚠ 這份資料集的關鍵設計：橋接顧客只連到【相鄰】社群
--    第一版寫成「10% 的邊連到隨機商品」，實測結果：深度 2 仍然連通全部 1000 種商品。
--    少量【均勻隨機】的長程邊就足以把圖的直徑壓垮 —— 這就是小世界現象。
--    改成「橋接顧客只買相鄰社群」之後，深度才真的有差。
--    這件事本身值得在 W13（牆二：形狀）講一次。
--
--  規模：顧客 5,000、商品 1,000、購買關係 100,000 條邊（滿足題 5 下限）
--  結構：1000 種商品分成 20 個社群（每群 50 種），排成一個環；
--        顧客歸屬一個社群，其中 10% 是橋接顧客，一半的購買落在相鄰社群。
-- =====================================================================

SELECT setseed(0.42);   -- 與 seed_shop_1m.sql 相同的可重現性做法

DROP TABLE IF EXISTS purchases;
CREATE TABLE purchases(
    customer_id int NOT NULL,
    product_id  int NOT NULL
);

INSERT INTO purchases(customer_id, product_id)
SELECT c,
       CASE WHEN is_bridge AND random() < 0.5
            THEN (((cm + 1) % 20) * 50) + (random()*49)::int + 1   -- 相鄰社群
            ELSE (  cm             * 50) + (random()*49)::int + 1  -- 自己的社群
       END
FROM (
    SELECT c,
           c % 20 AS cm,
           (c % 100) < 10 AS is_bridge          -- 10% 的顧客是橋接顧客
    FROM (SELECT (random()*4999)::int + 1 AS c
          FROM generate_series(1,100000)) z
) w;

CREATE INDEX idx_purchases_product  ON purchases(product_id);
CREATE INDEX idx_purchases_customer ON purchases(customer_id);
ANALYZE purchases;

-- =====================================================================
--  【驗證查詢】從商品 1 出發，各深度可達多少種商品
-- ---------------------------------------------------------------------
--  把下面的 4 換成 1 / 2 / 3 / 4 各跑一次。
--
--  PostgreSQL 16.13 實測（本機，setseed(0.42)）：
--
--      深度 | 可達商品數 | 執行時間（三次中位數）
--        1  |     88     |    6.2 ms
--        2  |    150     |  127.8 ms
--        3  |    200     |  346.6 ms
--        4  |    250     |  612.4 ms
--
--  ★ 三件事請注意：
--    ① 深度 2/3/4 的【結果】不同（150 / 200 / 250）—— 這正是 seed_shop_1m 做不到的
--    ② 深度 2/3/4 的【成本】以超線性成長（128 → 347 → 612 ms）
--       這是題 5 要學生解釋的核心：成本長在【每一跳】上
--    ③ 你的時間數字不會跟這裡一樣（機器不同），但【形狀】應該一樣
--
--  對照組（均勻隨機配對，即 seed_shop_1m 的分布）實測：
--      深度 1 = 673、深度 2 = 1000、深度 3 = 1000、深度 4 = 1000  ← 完全沒有差異
-- =====================================================================

-- WITH RECURSIVE r(prod, depth) AS (
--     SELECT 1, 0
--     UNION
--     SELECT p2.product_id, r.depth + 1
--     FROM r
--     JOIN purchases p1 ON p1.product_id  = r.prod
--     JOIN purchases p2 ON p2.customer_id = p1.customer_id
--     WHERE r.depth < 4                      -- ← 改這個數字
-- )
-- SELECT count(DISTINCT prod) FROM r;
