#!/usr/bin/env bash
# 防11：在 q01_defense 建一個「拿掉 FOR UPDATE」的 place_order_nolock()，正本 place_order() 不動
set -euo pipefail
DEF=$(psql -X -At -d q01_defense -c "SELECT pg_get_functiondef('place_order(int,int[],int[])'::regprocedure)")
NEW=$(sed -e 's/FUNCTION public.place_order(/FUNCTION public.place_order_nolock(/' \
          -e 's/          ORDER BY product_id FOR UPDATE) l;/          ORDER BY product_id) l;   -- ← 防11：FOR UPDATE 拿掉了/' <<<"$DEF")
grep -q "防11" <<<"$NEW" || { echo "替換失敗"; exit 1; }
psql -X -q -d q01_defense -c "DROP FUNCTION IF EXISTS place_order_nolock(int,int[],int[])"
psql -X -q -d q01_defense <<<"$NEW"
diff <(echo "$DEF") <(echo "$NEW") || true
