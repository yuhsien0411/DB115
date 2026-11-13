#!/usr/bin/env bash
# 建立／更新網站低權限帳號 q01_web，並把密碼寫進 ~/.pgpass（不進專案）。設計見 docs/design/least_privilege.md
# 用法：bash project/security/setup_web_role.sh          （密碼已存在就沿用，否則隨機產生）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PGPASS="$HOME/.pgpass"
KEY="127.0.0.1:5432:q01:q01_web:"

touch "$PGPASS"
chmod 600 "$PGPASS"  # libpq 與 asyncpg 都會拒絕使用權限過寬的 .pgpass
PW="$(grep -F "$KEY" "$PGPASS" | head -1 | cut -d: -f5- || true)"
if [ -z "$PW" ]; then
    PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    echo "$KEY$PW" >> "$PGPASS"
fi

# 以 student（superuser）執行授權；密碼從 stdin 設成 psql 變數，不出現在指令列參數（ps 看不到）
{ printf "\\\\set pw '%s'\n" "$PW"; cat "$HERE/web_role.sql"; } \
    | psql -X -q -v ON_ERROR_STOP=1 -d q01 -f -
echo "q01_web 已就緒。連線：host=127.0.0.1 user=q01_web dbname=q01（密碼在 ~/.pgpass）"
