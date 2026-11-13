#!/usr/bin/env bash
# 防05：數每一個網頁請求實際發出幾條 SQL（只打 GET，不改資料）
# 做法：暫時讓 q01_web 在 q01 記錄所有 SQL（log_statement=all），每打一個 API 就數 log 新增了幾行 statement，最後還原。
set -euo pipefail
LOG=/var/log/postgresql/postgresql-16-main.log
PORT=8765; TOKEN=defense05
psql -X -q -d q01 -c "ALTER ROLE q01_web IN DATABASE q01 SET log_statement = 'all'"
trap 'kill $SRV 2>/dev/null || true; wait $SRV 2>/dev/null || true; psql -X -q -d q01 -c "ALTER ROLE q01_web IN DATABASE q01 RESET log_statement"; echo "(已還原 log_statement)"' EXIT
Q01_ADMIN_TOKEN=$TOKEN python3 web/server.py --port $PORT > /dev/null 2>&1 & SRV=$!
for i in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 0.2; done
count() {  # $1 說明  $2 路徑
  local before after
  before=$(sudo wc -l "$LOG" | cut -d" " -f1); curl -s -b "q01_admin=$TOKEN" -o /dev/null -w "%{http_code} %{time_total}s" "http://127.0.0.1:$PORT$2"; sleep 0.3
  after=$(sudo wc -l "$LOG" | cut -d" " -f1)
  local n; n=$(sudo sed -n "$((before+1)),${after}p" "$LOG" | grep -c "q01_web@q01 LOG:  \(statement\|execute\)" || true)
  printf "  ← %-38s %s 條 SQL\n" "$1" "$n"
  sudo sed -n "$((before+1)),${after}p" "$LOG" | grep "q01_web@q01 LOG:  \(statement\|execute\)" | sed 's/.*LOG:  /      /' | cut -c1-150
}
count "前台商品列表（一頁 12 件）"        "/api/store/products?page=1"
count "前台我的訂單（顧客 1）"            "/api/store/orders?customer_id=1"
count "前台訂單明細"                      "/api/store/orders/1000407?customer_id=$(psql -X -At -d q01 -c 'select customer_id from orders where order_id=1000407')"
count "前台購物車（顧客 1）"              "/api/store/cart?customer_id=1"
count "後台商品列表（一頁 12 件含已售）"  "/api/admin/products?page=1"
count "後台儀表板"                        "/api/admin/dashboard"
