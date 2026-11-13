#!/usr/bin/env bash
# preflight.py 的自我測試：故意開一條 PG 和一條 Redis 的額外連線，確認
#   1. 只檢查時會抓到兩條，結束碼 1
#   2. --close 會斷開它們，結束碼 0
#   3. 斷開後再檢查一次仍是 0
# 會斷開本機 PG／Redis 的所有其他 client 連線，跑之前先停網站、關 psql。
set -u
cd "$(dirname "$0")/.."

FAIL=0
check() {  # check <說明> <預期結束碼> <實際結束碼>
  if [ "$2" = "$3" ]; then echo "PASS  $1"; else echo "FAIL  $1（預期 $2，實際 $3）"; FAIL=1; fi
}

if ! python3 tools/preflight.py --close >/dev/null; then
  echo "起始狀態不乾淨（可能有殘留行程或網站），先處理再測：" >&2
  python3 tools/preflight.py >&2
  exit 2
fi

psql -X -q -d q01 -c "SELECT pg_sleep(60)" >/dev/null 2>&1 &
PG_PID=$!
redis-cli BLPOP q01:preflight_test 60 >/dev/null 2>&1 &
RD_PID=$!
trap 'kill $PG_PID $RD_PID 2>/dev/null' EXIT
sleep 1

out=$(python3 tools/preflight.py); rc=$?
check "有額外連線時結束碼 1" 1 "$rc"
echo "$out" | grep -q "PostgreSQL 其他 client 連線：1 條" && check "抓到 PG 的 1 條" 0 0 || check "抓到 PG 的 1 條" 0 1
echo "$out" | grep -q "Redis 其他 client 連線：1 條" && check "抓到 Redis 的 1 條" 0 0 || check "抓到 Redis 的 1 條" 0 1

python3 tools/preflight.py --close >/dev/null; rc=$?
check "--close 之後結束碼 0" 0 "$rc"

python3 tools/preflight.py >/dev/null; rc=$?
check "再檢查一次結束碼 0" 0 "$rc"

[ "$FAIL" = 0 ] && echo "全部通過" || echo "有測試失敗"
exit "$FAIL"
