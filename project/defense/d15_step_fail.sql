-- 防15：網站結帳的相鄰兩步——place_order() 成功（扣庫存、寫訂單）→ 寫冪等紀錄 order_requests 失敗（跑在 q01_defense）
-- 失敗用「request_id 重複」製造：先放一筆同 uuid 的紀錄，讓第二步撞主鍵。
\set ON_ERROR_STOP 0
\set rid '\'11111111-1111-1111-1111-111111111111\''
DELETE FROM order_requests WHERE request_id = :rid;
INSERT INTO order_requests SELECT :rid, max(order_id) FROM orders WHERE status = 'pending';
SELECT stock AS 前_商品7庫存, (SELECT count(*) FROM orders WHERE status='pending') AS 前_pending訂單數,
       (SELECT last_value FROM q01_order_id_seq) AS 前_序列值 FROM products WHERE product_id = 7;
BEGIN;
SELECT place_order(1, ARRAY[7], ARRAY[2]) AS 第一步_新訂單編號;
SELECT stock AS 交易內_商品7庫存 FROM products WHERE product_id = 7;
INSERT INTO order_requests VALUES (:rid, currval('q01_order_id_seq'));   -- 第二步：撞主鍵失敗
COMMIT;   -- 交易已失敗，COMMIT 會變成 ROLLBACK
SELECT stock AS 後_商品7庫存, (SELECT count(*) FROM orders WHERE status='pending') AS 後_pending訂單數,
       (SELECT last_value FROM q01_order_id_seq) AS 後_序列值 FROM products WHERE product_id = 7;
DELETE FROM order_requests WHERE request_id = :rid;
