#!/usr/bin/env bash
# 防20：Redis 的 ~10 萬 ops/s 是 Redis 的上限，還是負載器（Python）的上限？
# 用一個私有、不持久化的 Redis（port 6390，跑完就關），不碰課程 Redis 與正式結果。屬煙霧量測。
#   (1) 同一個負載器（loader/load.py，8 個行程、200 併發、hot80）跑 20 秒，期間取樣 redis-server 與負載器的 CPU%
#   (2) redis-benchmark（C 寫的，多執行緒）直接打 INCR，看 Redis 本身能到多少
set -uo pipefail
cd "$(dirname "$0")/.."
PORT=6390; TMP=$(mktemp -d)
redis-server --port $PORT --save "" --appendonly no --daemonize yes --pidfile $TMP/r.pid --logfile $TMP/r.log
sleep 0.5; RPID=$(cat $TMP/r.pid)
trap 'redis-cli -p $PORT shutdown nosave >/dev/null 2>&1; rm -rf $TMP' EXIT
redis-cli -p $PORT eval "for i=1,1000 do redis.call('SET','q01:d20:views:'..i,0) end" 0 >/dev/null
echo "== (1) 專案負載器 → Redis（200 併發、hot80、暖機 5 秒、正式 15 秒）"
python3 loader/load.py --backend redis --scenario hot80 --concurrency 200 --warmup 5 --duration 15 \
        --run-id d20 --out $TMP/load --redis-port $PORT > $TMP/load.json 2>/dev/null &
LPID=$!; sleep 9
echo "   第 9 秒起取樣 5 秒（top，%CPU 以單核為 100%）："
top -b -d 5 -n 2 | awk -v r=$RPID 'NR>1 && /^top -/{blk++} blk==1 && ($1==r || $12 ~ /python3/) {print}' > $TMP/top.txt
awk -v r=$RPID '$1==r {printf "   redis-server      %%CPU = %s\n", $9}' $TMP/top.txt
awk -v r=$RPID '$1!=r {s+=$9; n++} END {printf "   負載器 python3 ×%d 合計 %%CPU = %.0f（平均每個 %.0f）\n", n, s, s/n}' $TMP/top.txt
wait $LPID
python3 -c "import json,sys; d=json.load(open('$TMP/load.json')); print(f\"   負載器結果：{d['success_ops_per_s']} ops/s, p95 {d.get('p95_ms','?')} ms\")" 2>/dev/null || tail -c 400 $TMP/load.json
echo "== (2) redis-benchmark 直接打 INCR（200 連線、不 pipeline，與負載器一樣一次一個請求）"
redis-benchmark -p $PORT -c 200 -n 2000000 --threads 4 -q -t incr | tr '\r' '\n' | grep 'requests per second'
