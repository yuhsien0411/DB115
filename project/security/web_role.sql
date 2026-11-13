-- 網站專用的低權限帳號 q01_web（最小權限原則，D18；對應防禦題庫 防 06）
-- 由 security/setup_web_role.sh 以 student 執行（需要 -v pw=...）；可重跑。
-- D37 起網站直接使用 public（與壓測同一份表與 place_order()），不再有 q01_web_demo。
-- 前提：public.order_requests、public.cart_items 已存在（project/schema.sql）。重跑 schema.sql 之後要重跑本檔。

-- 1. 帳號本身：可登入，但沒有任何系統層級的權力
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'q01_web') THEN
        CREATE ROLE q01_web LOGIN;
    END IF;
END $$;
ALTER ROLE q01_web NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 20 PASSWORD :'pw';

-- 2. 資料庫層級：q01 只開放 q01_web（與 superuser）連線，也拿掉 PUBLIC 的建暫存表權限
--    課程的 shop、graph、norm 不動：q01_web 仍可連線（PUBLIC 預設），但那裡的表沒有任何授權
REVOKE ALL ON DATABASE q01 FROM PUBLIC;
GRANT CONNECT ON DATABASE q01 TO q01_web;

-- 3. 先清掉之前給過的權限，再只給網站用得到的（重跑時結果一致）
--    舊的 q01_web_demo 已移除（D37）；若還在，也一併收回
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'q01_web_demo') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA q01_web_demo FROM q01_web';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA q01_web_demo FROM q01_web';
        EXECUTE 'REVOKE ALL ON SCHEMA q01_web_demo FROM q01_web';
    END IF;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM q01_web;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM q01_web;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM q01_web;
REVOKE ALL ON SCHEMA public FROM q01_web;
-- 前一版沒有給 public 欄位層級的權限；表層級 REVOKE 不會收回欄位權限，這裡逐欄收回
REVOKE ALL (customer_id, name, email, city, signup_date) ON public.customers FROM q01_web;
REVOKE ALL (customer_id, product_id, quantity, added_price, added_at) ON public.cart_items FROM q01_web;

GRANT USAGE ON SCHEMA public TO q01_web;  -- 只能使用，不能在裡面建物件（PG 15 起 PUBLIC 也沒有 CREATE）

-- 顧客：只能讀編號與姓名，讀不到 email、城市等個資
GRANT SELECT (customer_id, name) ON public.customers TO q01_web;
-- 欄位層級：只能改庫存與瀏覽數，改不了價格、名稱（FOR UPDATE 鎖列也靠這個 UPDATE 權限）
GRANT SELECT, UPDATE (stock, view_count) ON public.products TO q01_web;
-- 訂單與明細只能新增和讀取：不能改、不能刪，成交紀錄寫入後就固定
GRANT SELECT, INSERT ON public.orders, public.order_items TO q01_web;
GRANT SELECT, INSERT ON public.order_requests TO q01_web;
-- 購物車（D38）：加入、刪除、改數量；加入時的價格（added_price）與鍵欄位寫入後不能改
GRANT SELECT, INSERT, DELETE ON public.cart_items TO q01_web;
GRANT UPDATE (quantity) ON public.cart_items TO q01_web;
-- Redis 模式的回寫紀錄
GRANT SELECT, INSERT ON public.view_flush_log TO q01_web;
GRANT USAGE ON SEQUENCE public.view_flush_log_flush_id_seq TO q01_web;
-- 下單：呼叫壓測同一個 place_order()；它是 SECURITY INVOKER，以 q01_web 自己的權限執行，
-- 所以上面的表權限之外只需要序列與函數本身
GRANT USAGE ON SEQUENCE public.q01_order_id_seq TO q01_web;
GRANT EXECUTE ON FUNCTION public.place_order(int, int[], int[]) TO q01_web;
