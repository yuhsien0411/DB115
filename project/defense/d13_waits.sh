#!/usr/bin/env bash
# 防13 補充：為什麼拿掉 FOR UPDATE 反而變慢？兩種下單交易各跑 8 秒，第 3 秒起每 0.5 秒取樣一次 pg_stat_activity（共 5 次）
# 注意：這是取樣次數，不是等待時間。
cd "$(dirname "$0")"
for f in d13_order_tx_nolock.sql ../pg/order_tx.sql; do
  psql -X -q -d q01_defense -c "VACUUM products"
  pgbench -n -c 50 -j 8 -T 8 -f $f q01_defense > /tmp/d13w.$$ 2>&1 & sleep 3
  echo "== $f"
  for i in 1 2 3 4 5; do
    psql -X -At -d postgres -c "select coalesce(wait_event_type,'CPU')||':'||coalesce(wait_event,'-') from pg_stat_activity where datname='q01_defense' and state='active'"
    sleep 0.5
  done | sort | uniq -c | sort -rn | head -6
  wait; grep -E "^tps" /tmp/d13w.$$; rm -f /tmp/d13w.$$
done
