#!/usr/bin/env bash
# 觀察用的短壓測（D31）：不計入正式統計，結果只寫到 results/_watch/。
# 搭配另一個終端機的 python3 tools/watch_live.py 看即時畫面。
#
#   bash tools/watch_demo.sh                  # PG hot80 → PG uniform1000 → Redis hot80，200 併發各 30 秒
#   bash tools/watch_demo.sh --concurrency 50 --duration 20
set -u
cd "$(dirname "$0")/.."

C=200
DUR=30
while [ $# -gt 0 ]; do
  case "$1" in
    --concurrency) C="$2"; shift ;;
    --duration) DUR="$2"; shift ;;
    *) echo "未知參數：$1" >&2; exit 64 ;;
  esac
  shift
done

OUT=results/_watch
mkdir -p "$OUT"
COMMON=(--concurrency "$C" --round 1 --warmup 5 --duration "$DUR" --results "$OUT" --note watch)

step() {
  echo
  echo "==== $1 ===="
  echo "     $2"
  sleep 3
}

step "PG／hot80（80% 流量打 5 件商品）" "預期：大部分連線卡在等列鎖 transactionid"
python3 run_cell.py --backend pg --scenario hot80 "${COMMON[@]}" >/dev/null

step "PG／uniform1000（流量平均分到 1000 件）" "預期：幾乎不等鎖，每秒筆數大幅上升"
python3 run_cell.py --backend pg --scenario uniform1000 "${COMMON[@]}" >/dev/null

step "Redis／hot80（同樣的熱點，改由 Redis 計數）" "預期：PG 沒有排隊，只在每 5 秒回寫時跳一下；Redis 每秒十萬級"
python3 run_cell.py --backend redis --scenario hot80 --interval 5 "${COMMON[@]}" >/dev/null

echo
echo "完成。結果在 $OUT/runs.csv（觀察用，不進任何正式批次 results/<日期>/）"
