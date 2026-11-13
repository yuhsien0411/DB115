"""啟動／正常停止的煙霧驗證；不發送商品瀏覽或下單，不修改 public 的資料。"""
import asyncio
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import asyncpg
import redis.asyncio as redis

ROOT = Path(__file__).resolve().parents[1]


def request(path, cookie=None):
    conn = http.client.HTTPConnection('127.0.0.1', 18080, timeout=5)
    try:
        conn.request('GET', path, headers={'Cookie': cookie} if cookie else {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


async def main():
    admin = await asyncpg.connect(database='q01', user='student', host='/var/run/postgresql')
    try:
        for mode in ('pg', 'redis'):
            # 確認沒有既有網站在跑（advisory lock q01_web），避免停止他人服務。
            locked = await admin.fetchval("SELECT pg_try_advisory_lock(hashtextextended('q01_web',0))")
            assert locked, '請先正常停止既有網站'
            await admin.execute("SELECT pg_advisory_unlock(hashtextextended('q01_web',0))")
            proc = subprocess.Popen([sys.executable, str(ROOT/'server.py'), '--port', '18080', '--counter', mode], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={k:v for k,v in os.environ.items() if k!='Q01_ADMIN_TOKEN'})
            run_id = None
            try:
                # 首兩行必須是前台與自動產生的後台入口。
                first = await asyncio.wait_for(asyncio.to_thread(proc.stdout.readline), 20)
                second = await asyncio.wait_for(asyncio.to_thread(proc.stdout.readline), 20)
                assert '18080' in first and '/admin/?token=' in second, (first, second)
                token = second.strip().split('token=', 1)[1]
                assert len(token) >= 32
                assert request('/')[0] == 200
                status, headers, _ = request('/admin/?token=' + token)
                assert status == 303
                cookie = headers['Set-Cookie'].split(';')[0]
                status, _, body = request('/api/admin/dashboard', cookie)
                assert status == 200
                data = json.loads(body)
                assert data['counter'] == mode
                run_id = data['run_id']
                # 以後端 PID 查證實際池／鎖連線皆使用低權限角色。
                roles = await admin.fetch("SELECT DISTINCT a.usename FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid WHERE l.locktype='advisory' AND l.granted AND a.datname='q01'")
                assert any(r['usename']=='q01_web' for r in roles)
            finally:
                proc.send_signal(signal.SIGTERM)
                output = await asyncio.to_thread(proc.communicate, timeout=25)
                assert proc.returncode == 0, output
                if mode == 'redis' and run_id:
                    r = redis.Redis()
                    try:
                        keys = [key async for key in r.scan_iter(match=f'q01:{run_id}:views:*')]
                        assert keys
                        await r.delete(*keys)  # 只清理此次已結束的測試 run。
                    finally:
                        await r.aclose()
            print(f'{mode}: 自動權杖、前後台 HTTP、q01_web advisory lock、SIGTERM 正常停止 PASS')
        failed = await asyncio.to_thread(subprocess.run, [sys.executable, str(ROOT/'server.py'), '--db-user', 'q01_web_missing_test'], capture_output=True, text=True, timeout=20)
        assert failed.returncode == 1 and 'setup_web_role.sh' in failed.stdout
        print('錯誤帳號：結束碼 1 且提示 setup_web_role.sh PASS')
        locked = await admin.fetchval("SELECT pg_try_advisory_lock(hashtextextended('q01_web',0))")
        assert locked
        print('所有測試程序已停止，網站 advisory lock 已釋放 PASS')
    finally:
        await admin.close()


if __name__ == '__main__':
    asyncio.run(main())
