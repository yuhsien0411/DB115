#!/usr/bin/env bash
# 以 q01_web 實際連線，逐項驗證「該能做的做得到、不該做的做不到」。設計見 docs/design/least_privilege.md
# 每項都包在 BEGIN … ROLLBACK 裡，不會改到資料。全部符合預期時結束碼 0。
set -uo pipefail
pass=0; fail=0
# ~/.pgpass 只存 q01 的密碼；測試其他資料庫時也要用同一組，才能證明「連得上也碰不到」
export PGPASSWORD="$(grep -F "127.0.0.1:5432:q01:q01_web:" "$HOME/.pgpass" | head -1 | cut -d: -f5-)"

run() {  # run <db> <sql>：以 q01_web 經 TCP 連線執行；-w 確保不會停下來等密碼
    local tx=(-c "BEGIN" -c "$2" -c "ROLLBACK")
    [ "${NO_TX:-}" = 1 ] && tx=(-c "$2")  # CREATE DATABASE 等指令不能放在交易區塊裡
    psql -X -At -w -v ON_ERROR_STOP=1 "host=127.0.0.1 port=5432 dbname=$1 user=q01_web" \
        "${tx[@]}" 2>&1
}
check() {  # check <ok|deny> <說明> <db> <sql>
    local want=$1 label=$2 out rc
    out="$(run "$3" "$4")"; rc=$?
    if { [ "$want" = ok ] && [ $rc -eq 0 ]; } ||
       { [ "$want" = deny ] && [ $rc -ne 0 ] && grep -qiE "permission denied|must be owner|not allowed" <<<"$out"; }; then
        pass=$((pass+1)); printf '  ✓ %-4s %s\n' "$want" "$label"
    else
        fail=$((fail+1)); printf '  ✗ %-4s %s\n      → %s\n' "$want" "$label" "$(head -1 <<<"$out")"
    fi
}
S=public  # D37 起網站與壓測共用 public

echo "網站需要的操作（應成功）"
check ok   "讀商品"                    q01 "SELECT count(*) FROM $S.products"
check ok   "讀顧客編號與姓名"          q01 "SELECT customer_id, name FROM $S.customers LIMIT 1"
check ok   "瀏覽數 +1"                 q01 "UPDATE $S.products SET view_count = view_count + 1 WHERE product_id = 1"
check ok   "鎖商品列"                  q01 "SELECT 1 FROM $S.products WHERE product_id = 1 FOR UPDATE"
check ok   "以 place_order() 下單"     q01 "SELECT $S.place_order(1, ARRAY[1], ARRAY[1])"
check ok   "寫冪等紀錄"                q01 "INSERT INTO $S.order_requests SELECT gen_random_uuid(), $S.place_order(1, ARRAY[2], ARRAY[1])"
check ok   "寫回寫紀錄"                q01 "INSERT INTO $S.view_flush_log(run_id, product_id, snapshot) VALUES ('verify', 1, 0)"
check ok   "advisory lock"             q01 "SELECT pg_advisory_xact_lock(1)"
check ok   "加入購物車"                q01 "INSERT INTO $S.cart_items(customer_id, product_id, quantity, added_price) VALUES (1, 1, 1, 0)"
check ok   "改購物車數量"              q01 "UPDATE $S.cart_items SET quantity = quantity WHERE customer_id = 1"
check ok   "移除購物車商品"            q01 "DELETE FROM $S.cart_items WHERE customer_id = 1"

echo "不該有的權限（應被拒）"
check deny "讀顧客 email"              q01 "SELECT email FROM $S.customers LIMIT 1"
check deny "改價格"                    q01 "UPDATE $S.products SET price = price WHERE product_id = 1"
check deny "刪訂單"                    q01 "DELETE FROM $S.orders"
check deny "改訂單金額"                q01 "UPDATE $S.orders SET amount = amount"
check deny "改冪等紀錄"                q01 "UPDATE $S.order_requests SET order_id = order_id"
check deny "刪冪等紀錄"                q01 "DELETE FROM $S.order_requests"
check deny "TRUNCATE 明細"             q01 "TRUNCATE $S.order_items"
check deny "改購物車加入價"            q01 "UPDATE $S.cart_items SET added_price = 0"
check deny "改購物車所屬顧客"          q01 "UPDATE $S.cart_items SET customer_id = customer_id"
check deny "TRUNCATE 購物車"           q01 "TRUNCATE $S.cart_items"
check deny "DROP 商品表"               q01 "DROP TABLE $S.products"
check deny "在 public 建表"            q01 "CREATE TABLE public.x (i int)"
check deny "建暫存表"                  q01 "CREATE TEMP TABLE x (i int)"
check deny "改 place_order()"          q01 "CREATE OR REPLACE FUNCTION public.place_order(int, int[], int[]) RETURNS int LANGUAGE sql AS 'SELECT 1'"
check deny "讀課程 shop 的訂單"        shop "SELECT count(*) FROM public.orders"
check deny "DROP 課程 shop 的表"       shop "DROP TABLE public.order_items"
NO_TX=1 check deny "建立資料庫"         q01 "CREATE DATABASE x"
check deny "建立帳號"                  q01 "CREATE ROLE x"
check deny "讀伺服器檔案"              q01 "SELECT pg_read_file('/etc/passwd')"
check deny "提權成 superuser"          q01 "ALTER ROLE q01_web SUPERUSER"

echo "結果：$pass 項符合預期，$fail 項不符"
[ $fail -eq 0 ]
