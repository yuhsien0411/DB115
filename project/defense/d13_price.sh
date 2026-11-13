#!/usr/bin/env bash
# 防13：正確性的價格（跑在 q01_defense；每格 20 秒、50 併發、暖機 5 秒；屬煙霧量測，不是正式矩陣）
# (a) 防超賣的行鎖：place_order（FOR UPDATE） vs place_order_nolock（拿掉 FOR UPDATE）—— 下單交易，hot80 商品分布
# (b) 持久性：synchronous_commit on vs off —— 瀏覽計數 UPDATE +1，uniform1000 與 hot80
set -euo pipefail
cd "$(dirname "$0")"
DB=q01_defense; OUT=out/d13_tmp; mkdir -p $OUT
psql -X -q -d $DB -c "UPDATE products SET stock = 100000000" -c "VACUUM ANALYZE products"
cell() {  # cell <名稱> <腳本> [PGOPTIONS]
  rm -f $OUT/pgbench_log.*
  PGOPTIONS="${3:-}" pgbench -n -c 50 -j 8 -T 25 -f "$2" -l --log-prefix=$OUT/pgbench_log $DB > $OUT/stdout.txt 2>&1
  python3 - "$1" $OUT <<'PY'
import glob, sys
name, d = sys.argv[1], sys.argv[2]
rows = [l.split() for f in glob.glob(d + '/pgbench_log.*') for l in open(f)]
t0 = min(int(r[4]) for r in rows)
lat = sorted(int(r[2]) / 1000 for r in rows if int(r[4]) >= t0 + 5)   # 去掉前 5 秒暖機
dur = max(int(r[4]) for r in rows) - (t0 + 5) + 1
print(f'  {name:44s} {len(lat)/dur:9,.0f} tx/s   p50 {lat[len(lat)//2]:7.2f} ms   p95 {lat[int(len(lat)*0.95)]:7.2f} ms')
PY
}
echo "(a) 下單交易 order_tx（hot80，1–3 品項），防超賣的行鎖"
cell "place_order        （有 FOR UPDATE，正本）" ../pg/order_tx.sql
cell "place_order_nolock （拿掉 FOR UPDATE，會超賣）" d13_order_tx_nolock.sql
echo "(b) 瀏覽計數 UPDATE +1，持久性"
cell "uniform1000  synchronous_commit=on （預設）" ../pg/pg_uniform1000.sql
cell "uniform1000  synchronous_commit=off" ../pg/pg_uniform1000.sql "-c synchronous_commit=off"
cell "hot80        synchronous_commit=on （預設）" ../pg/pg_hot80.sql
cell "hot80        synchronous_commit=off" ../pg/pg_hot80.sql "-c synchronous_commit=off"
rm -rf $OUT
