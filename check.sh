#!/usr/bin/env bash
# =====================================================================
#  環境自我檢查｜資料庫管理系統（115-1）
#  （檔名用英文，是為了避免 Windows 解壓縮時把中文檔名弄壞）
#
#  把這支跑出來的整段輸出，跟課程公告的「標準輸出」逐行比對。
#  只要有一行對不上，就代表你的環境沒建好 —— 先解決它，不要往下做實作單。
# =====================================================================
ok=0; bad=0
chk () {  # chk 說明 實際值 期望值
  if [ "$2" = "$3" ]; then printf "  [ OK ] %-34s %s\n" "$1" "$2"; ok=$((ok+1))
  else printf "  [失敗] %-34s 得到 %s，應為 %s\n" "$1" "${2:-（沒有回應）}" "$3"; bad=$((bad+1)); fi
}

echo "=============================================="
echo " 一、三個資料庫的版本"
echo "=============================================="
chk "PostgreSQL 主版本" "$(psql -qtAX -d shop -c 'SHOW server_version' 2>/dev/null | cut -d. -f1)" "16"
chk "Redis 主版本"      "$(redis-cli INFO server 2>/dev/null | sed -n 's/^redis_version:\([0-9]*\).*/\1/p' | tr -d '\r')" "7"
chk "MongoDB 主版本"    "$(mongosh --quiet --eval 'db.version()' 2>/dev/null | tr -d '\r' | cut -d. -f1,2)" "8.0"

echo
echo "=============================================="
echo " 二、決定 EXPLAIN 形狀的設定（對不上，實作單就會對不上）"
echo "=============================================="
for kv in "random_page_cost 4" "max_parallel_workers_per_gather 2" "shared_buffers 128MB" "work_mem 4MB" "effective_cache_size 4GB" "max_connections 500"; do
  k=${kv% *}; v=${kv#* }
  chk "$k" "$(psql -qtAX -d shop -c "SHOW $k" 2>/dev/null)" "$v"
done

echo
echo "=============================================="
echo " 三、種子資料的列數（全班必須完全一樣）"
echo "=============================================="
chk "shop.customers"   "$(psql -qtAX -d shop -c 'SELECT count(*) FROM customers'   2>/dev/null)" "50000"
chk "shop.products"    "$(psql -qtAX -d shop -c 'SELECT count(*) FROM products'    2>/dev/null)" "1000"
chk "shop.orders"      "$(psql -qtAX -d shop -c 'SELECT count(*) FROM orders'      2>/dev/null)" "1000000"
chk "shop.order_items" "$(psql -qtAX -d shop -c 'SELECT count(*) FROM order_items' 2>/dev/null)" "2000000"
chk "orders 之中 completed" "$(psql -qtAX -d shop -c "SELECT count(*) FROM orders WHERE status = 'completed'" 2>/dev/null)" "899754"
chk "orders 之中 cancelled" "$(psql -qtAX -d shop -c "SELECT count(*) FROM orders WHERE status = 'cancelled'" 2>/dev/null)" "30005"
chk "graph.edges"          "$(psql -qtAX -d graph -c "SELECT count(*) FROM purchases" 2>/dev/null)" "100000"

echo
echo "=============================================="
echo " 四、兩件實作單一定會用到的事"
echo "=============================================="
chk "Redis 寫得進去讀得出來" "$(redis-cli SET coursecheck hello >/dev/null 2>&1; redis-cli GET coursecheck 2>/dev/null; redis-cli DEL coursecheck >/dev/null 2>&1)" "hello"
chk "MongoDB 寫得進去讀得出來" "$(mongosh --quiet mongodb://127.0.0.1:27017/coursecheck --eval 'db.c.drop(); db.c.insertOne({a:1}); print(db.c.countDocuments({})); db.c.drop()' 2>/dev/null | head -1 | tr -d '\r')" "1"

echo
echo "=============================================="
if [ $bad -eq 0 ]; then
  echo " 全部 $ok 項通過。環境沒問題，可以開始做實作單。"
else
  echo " $bad 項失敗（$ok 項通過）。"
  echo
  echo " 怎麼辦："
  echo "   1. 先在 VS Code 按 F1 → Dev Containers: Rebuild Container，重建一次。"
  echo "   2. 重建後再跑一次這支。還是失敗的話，把上面整段輸出貼到討論區。"
  echo "   3. 不要跳過這一步直接做實作單 —— 環境不對，你會在第三步卡住而且不知道為什麼。"
fi
echo "=============================================="
exit $bad
