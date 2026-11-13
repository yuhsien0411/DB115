#!/usr/bin/env bash
# 依序跑完整實驗矩陣（2 方案 × 併發 × 流量 × 輪次），再跑遺失視窗實驗，最後產生摘要。
# 每格呼叫 run_cell.py；一格失敗會記在 log 並繼續下一格。設計見 docs/design/run_matrix.md。
#
#   bash run_matrix.sh                      # 主矩陣 36 格＋故障實驗 T=1、5 各 3 次（約 1 小時 10 分）
#   bash run_matrix.sh --dry-run            # 只印出會跑的指令
#   bash run_matrix.sh --hot5               # 加跑補充組 hot5
#   bash run_matrix.sh --rounds "1" --concurrency "200" --no-crash   # 只跑部分
#   bash run_matrix.sh --close-others       # 傳給 run_cell.py（PG 名額不足時斷開其他連線）
#   bash run_matrix.sh --smoke --rounds 1 --concurrency 20   # 煙霧測試：暖機 1 秒、正式 3 秒，寫到 results/_smoke
#   bash run_matrix.sh --out results/20261106 --no-crash ...   # 指定批次資料夾（預設 results/<開跑當天日期>，D39）
set -u
cd "$(dirname "$0")"

ROUNDS="1 2 3"
CONCURRENCY="50 200 500"
SCENARIOS="hot80 uniform1000"
BACKENDS="pg redis"
INTERVAL=5
CRASH_T="1 5"
CRASH_REPEAT=3
OUT=""
DRY=0
CLOSE=""
SMOKE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rounds) ROUNDS="$2"; shift ;;
    --concurrency) CONCURRENCY="$2"; shift ;;
    --scenarios) SCENARIOS="$2"; shift ;;
    --backends) BACKENDS="$2"; shift ;;
    --interval) INTERVAL="$2"; shift ;;
    --hot5) SCENARIOS="$SCENARIOS hot5" ;;
    --crash-t) CRASH_T="$2"; shift ;;
    --crash-repeat) CRASH_REPEAT="$2"; shift ;;
    --no-crash) CRASH_T="" ;;
    --close-others) CLOSE="--close-others" ;;
    --dry-run) DRY=1 ;;
    --smoke) SMOKE=1 ;;
    --out) OUT="$2"; shift ;;
    *) echo "未知參數：$1" >&2; exit 64 ;;
  esac
  shift
done

# 正式結果依批次放在 results/<開跑當天日期>/（D39），跨過午夜也留在同一個資料夾
[ -z "$OUT" ] && OUT="results/$(date +%Y%m%d)"
CELL=(--results "$OUT")     # 傳給每一格 run_cell.py 的共同參數
CRASH=(--out-root "$OUT" --csv "$OUT/failures.csv")
SUM=(--interval "$INTERVAL")
if [ "$SMOKE" = 1 ]; then
  OUT=results/_smoke  # 煙霧測試固定放這裡，不分日期
  CELL=(--warmup 1 --duration 3 --results "$OUT" --note smoke)
  CRASH=(--kill-after 2 --concurrency 20 --out-root "$OUT" --csv "$OUT/failures.csv")
  SUM+=(--duration any)
  CRASH_REPEAT=1
fi
SUM+=(--runs "$OUT/runs.csv" --failures "$OUT/failures.csv" --out "$OUT/summary.md")
[ "$DRY" = 1 ] || mkdir -p "$OUT"  # --dry-run 不建資料夾，以免留下空批次
LOG="$OUT/matrix_$(date +%Y%m%d%H%M%S).log"
FAILED=0
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# 以實際執行的指令數計算；每個 crash_test.py 呼叫（每個 T 的每一次）算一項。
word_count() { local words=( $1 ); echo "${#words[@]}"; }
TOTAL=$(( $(word_count "$ROUNDS") * $(word_count "$CONCURRENCY") * $(word_count "$SCENARIOS") * $(word_count "$BACKENDS") + $(word_count "$CRASH_T") * CRASH_REPEAT ))
CURRENT=0

# 上一格若沒清乾淨，殘留的負載會汙染後面每一格，所以整批停下來（不代為砍掉不明行程）
check_leftovers() {
  [ "$DRY" = 1 ] && return 0
  local left
  left=$(pgrep -af 'load\.py|flush_worker|pgbench|crash_test\.py' | grep -v pgrep)
  if [ -n "$left" ]; then
    log "中止：有殘留的實驗行程，請確認後結束它們再重跑缺的格子："
    echo "$left" | tee -a "$LOG"
    exit 1
  fi
}

# 跑一個實驗指令；結束碼不為 0 只記錄、不中止
run() {
  if [ "$DRY" = 1 ]; then echo "$*"; return 0; fi
  check_leftovers
  CURRENT=$((CURRENT + 1))
  log "RUN [$CURRENT/$TOTAL] $*"
  "$@"; local rc=$?
  log "EXIT $rc [$CURRENT/$TOTAL] 完成 $((CURRENT * 100 / TOTAL))%  $*"
  [ "$rc" = 0 ] || FAILED=$((FAILED + 1))
}

vacuum() {
  # 每格前清掉熱門列累積的舊版本（D24）；失敗時仍照跑，但在該格 notes 標記
  if [ "$DRY" = 1 ]; then echo 'psql -X -q -d q01 -c "VACUUM (ANALYZE) products"'; return 0; fi
  psql -X -q -d q01 -c "VACUUM (ANALYZE) products" >>"$LOG" 2>&1 && return 0
  log "VACUUM 失敗，下一格標記 vacuum_failed"
  return 1
}

[ "$DRY" = 0 ] && check_leftovers
# 開跑前確認 PG／Redis 沒有額外連線、名額夠用（以最大併發檢查）；--close-others 時代為斷開
if [ "$DRY" = 0 ]; then
  MAXC=$(echo "$CONCURRENCY" | tr ' ' '\n' | sort -n | tail -1)
  python3 tools/preflight.py --concurrency "$MAXC" ${CLOSE:+--close} 2>&1 | tee -a "$LOG"
  if [ "${PIPESTATUS[0]}" != 0 ]; then
    log "中止：開跑前確認沒通過（見上）"
    exit 1
  fi
fi
[ "$DRY" = 0 ] && log "開始：rounds=[$ROUNDS] concurrency=[$CONCURRENCY] scenarios=[$SCENARIOS] backends=[$BACKENDS] T=$INTERVAL crash_T=[$CRASH_T]x$CRASH_REPEAT"

k=0
for r in $ROUNDS; do
  for c in $CONCURRENCY; do
    for s in $SCENARIOS; do
      # 每組交替先跑哪個方案，機器狀態的漂移才不會固定落在同一邊
      order=$BACKENDS
      [ $((k % 2)) = 1 ] && order=$(echo "$BACKENDS" | awk '{for (i = NF; i > 0; i--) printf "%s%s", $i, (i > 1 ? " " : "")}')
      k=$((k + 1))
      for b in $order; do
        note=()
        vacuum || note=(--note vacuum_failed)
        if [ "$b" = redis ]; then
          run python3 run_cell.py --backend redis --scenario "$s" --concurrency "$c" --round "$r" \
            --interval "$INTERVAL" ${CELL[@]+"${CELL[@]}"} ${note[@]+"${note[@]}"}
        else
          run python3 run_cell.py --backend pg --scenario "$s" --concurrency "$c" --round "$r" \
            ${CLOSE:+"$CLOSE"} ${CELL[@]+"${CELL[@]}"} ${note[@]+"${note[@]}"}
        fi
      done
    done
  done
done

# crash_test.py 的 --repeat 是「第幾次」（寫進 CSV），不是次數，所以要逐次呼叫
for t in $CRASH_T; do
  for i in $(seq 1 "$CRASH_REPEAT"); do
    run python3 redis/crash_test.py --interval "$t" --repeat "$i" ${CRASH[@]+"${CRASH[@]}"}
  done
done

[ "$DRY" = 1 ] && { echo "python3 tools/summarize.py ${SUM[*]}"; exit 0; }
log "結束：$FAILED 個指令結束碼不為 0（明細見 $LOG；那幾格是否可用看 runs.csv 的 notes）"
if python3 tools/summarize.py "${SUM[@]}"; then
  log "摘要：$OUT/summary.md"
else
  log "摘要產生失敗，$OUT/summary.md 不是這一批的結果"
  exit 3
fi
exit 0
