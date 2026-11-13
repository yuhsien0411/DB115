#!/usr/bin/env bash
# 防16／防19：200 併發、hot80 的瀏覽計數 UPDATE +1，synchronous_commit on vs off（跑在 q01_defense；每格 25 秒，前 5 秒不計；煙霧量測）
set -euo pipefail
cd "$(dirname "$0")"
DB=q01_defense; OUT=out/d16_tmp; mkdir -p $OUT
cell() {
  psql -X -q -d $DB -c "VACUUM products"
  rm -f $OUT/pgbench_log.*
  PGOPTIONS="${2:-}" pgbench -n -c 200 -j 12 -T 25 -f ../pg/pg_hot80.sql -l --log-prefix=$OUT/pgbench_log $DB > $OUT/stdout.txt 2>&1
  python3 - "$1" $OUT <<'PY'
import glob, sys
name, d = sys.argv[1], sys.argv[2]
rows = [l.split() for f in glob.glob(d + '/pgbench_log.*') for l in open(f)]
t0 = min(int(r[4]) for r in rows)
lat = sorted(int(r[2]) / 1000 for r in rows if int(r[4]) >= t0 + 5)
dur = max(int(r[4]) for r in rows) - (t0 + 5) + 1
print(f'  {name:36s} {len(lat)/dur:9,.0f} tx/s   p50 {lat[len(lat)//2]:7.2f} ms   p95 {lat[int(len(lat)*0.95)]:7.2f} ms')
PY
}
echo "hot80、200 併發"
cell "synchronous_commit=on （預設）"
cell "synchronous_commit=off" "-c synchronous_commit=off"
rm -rf $OUT
