#!/usr/bin/env bash
# 防12：斷電的那一瞬間（跑在 q01_defense；第二次會讓整個 PostgreSQL 強制中止再開機）
# 第一次：只砍掉那個連線（pg_terminate_backend）。第二次：pg_ctlcluster stop -m immediate（＝拔電源）。
# 兩次都是：商品 1 「扣了庫存、還沒寫訂單、沒有 COMMIT」。另外商品 3 先 COMMIT 扣 5 件，當作「已提交」的對照。
set -uo pipefail
DB=q01_defense; LOG=/var/log/postgresql/postgresql-16-main.log
q() { psql -X -At -d $DB -c "$1"; }
logmark() { sudo wc -l "$LOG" | cut -d' ' -f1; }
hold_tx() {  # 開一條連線：BEGIN → 扣商品 1 庫存 → 停住（不 COMMIT）
  psql -X -q -d $DB -c "BEGIN" -c "UPDATE products SET stock = stock - 1 WHERE product_id = 1" \
       -c "SELECT pg_sleep(60)" -c "COMMIT" >/dev/null 2>&1 &
  for _ in $(seq 1 50); do
    PID=$(q "SELECT pid FROM pg_stat_activity WHERE datname='$DB' AND query LIKE 'SELECT pg_sleep%' AND state <> 'idle'")
    [ -n "$PID" ] && break; sleep 0.1
  done
}
echo "== 開始前   商品1 庫存 = $(q 'SELECT stock FROM products WHERE product_id=1')   商品3 庫存 = $(q 'SELECT stock FROM products WHERE product_id=3')"

echo; echo "######## 第一次（對照組）：只砍掉那條連線"
hold_tx
echo "未提交交易的 pid = $PID；從另一條連線看到的商品1 庫存 = $(q 'SELECT stock FROM products WHERE product_id=1')（MVCC：看不到未提交的改動）"
M=$(logmark)
q "SELECT pg_terminate_backend($PID)" >/dev/null; sleep 1
echo "砍掉後  商品1 庫存 = $(q 'SELECT stock FROM products WHERE product_id=1')"
echo "-- 這段時間的 log："; sudo sed -n "$((M+1)),\$p" "$LOG"

echo; echo "######## 第二次（真的斷電）：pg_ctlcluster 16 main stop -m immediate"
q "UPDATE products SET stock = stock - 5 WHERE product_id = 3" >/dev/null   # 已提交的對照
echo "斷電前：商品3 已 COMMIT 扣 5 件 → $(q 'SELECT stock FROM products WHERE product_id=3')"
hold_tx
echo "未提交交易的 pid = $PID（商品1 扣了 1 件，沒有 COMMIT）"
M=$(logmark)
sudo pg_ctlcluster 16 main stop -m immediate
echo "（已強制中止）"; sleep 1
sudo pg_ctlcluster 16 main start
for _ in $(seq 1 50); do pg_isready -q && break; sleep 0.2; done
echo "重開後  商品1 庫存 = $(q 'SELECT stock FROM products WHERE product_id=1')   商品3 庫存 = $(q 'SELECT stock FROM products WHERE product_id=3')"
echo "-- 這段時間的 log："; sudo sed -n "$((M+1)),\$p" "$LOG"
wait 2>/dev/null
