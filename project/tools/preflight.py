#!/usr/bin/env python3
"""開始壓測前的確認：PG、Redis 沒有額外連線，也沒有殘留行程，名額夠用。

  python3 tools/preflight.py                   # 只檢查，列出占用者
  python3 tools/preflight.py --close           # 斷開 PG／Redis 的其他連線，等幾秒再確認沒有重連
  python3 tools/preflight.py --concurrency 200 # 以 200 併發檢查名額（預設 500，最重的一格）

不會砍掉任何行程：殘留的實驗行程或網站只列出來，要使用者自己停（同 run_matrix.sh，T7 #3）。
結束碼：0 全部通過；1 仍有額外連線、殘留行程或名額不足；2 連不上 PG 或 Redis。
設計見 docs/design/preflight.md。
"""
import argparse
import resource
import subprocess
import sys
import time

# 會占連線或製造負載的程式：實驗本身的四個，加上網站（連線池斷開後會立刻重連）
PROCS = r"load\.py|flush_worker|pgbench|crash_test\.py|web/server\.py"

PG_OTHERS = """
SELECT pid, usename, coalesce(datname, ''), coalesce(application_name, ''),
       coalesce(client_addr::text, 'local'), coalesce(state, '')
FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()
ORDER BY pid
"""


def psql(db, sql):
    return subprocess.run(["psql", "-X", "-At", "-d", db, "-c", sql],
                          check=True, capture_output=True, text=True).stdout.strip()


def pg_others(db):
    return [line.split("|") for line in psql(db, PG_OTHERS).splitlines() if line]


def redis_others(r):
    me = r.client_id()
    return [c for c in r.client_list() if int(c["id"]) != me]


def leftover_procs():
    out = subprocess.run(["pgrep", "-af", PROCS], capture_output=True, text=True).stdout
    return out.splitlines()


def show(ok, msg, details=()):
    print(f"[{'OK' if ok else 'NG'}] {msg}")
    for d in details:
        print(f"       {d}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="q01")
    ap.add_argument("--redis-host", default="localhost")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--concurrency", type=int, default=500, help="要檢查名額的併發數")
    ap.add_argument("--close", action="store_true", help="斷開 PG 與 Redis 的其他 client 連線")
    ap.add_argument("--settle", type=float, default=2.0, help="斷開後等幾秒再確認沒有重連")
    args = ap.parse_args()

    try:
        pg_slots = int(psql(args.db, "SHOW max_connections"))
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"[NG] 連不上 PostgreSQL（{args.db}）：{getattr(e, 'stderr', e)}".strip())
        return 2
    try:
        import redis
        r = redis.Redis(host=args.redis_host, port=args.redis_port, socket_timeout=3)
        redis_slots = int(r.config_get("maxclients")["maxclients"])
    except Exception as e:
        print(f"[NG] 連不上 Redis（{args.redis_host}:{args.redis_port}）：{e}")
        return 2

    if args.close:
        others = pg_others(args.db)
        if others:
            psql(args.db, "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                          "WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()")
        rothers = redis_others(r)
        for c in rothers:
            r.client_kill_filter(_id=c["id"])
        print(f"--close：斷開 PG {len(others)} 條、Redis {len(rothers)} 條其他連線，"
              f"等 {args.settle:g} 秒確認沒有重連")
        time.sleep(args.settle)

    ok = True
    procs = leftover_procs()
    ok &= show(not procs, f"殘留的實驗行程或網站：{len(procs)} 個"
               + ("（請自己停掉，本程式不砍行程）" if procs else ""), procs)

    others = pg_others(args.db)
    ok &= show(not others, f"PostgreSQL 其他 client 連線：{len(others)} 條"
               + ("（斷開後又連回來，請停掉那個程式）" if others and args.close else ""),
               [f"pid={o[0]} user={o[1]} db={o[2] or '-'} app={o[3] or '-'} from={o[4]} state={o[5] or '-'}"
                for o in others])

    rothers = redis_others(r)
    ok &= show(not rothers, f"Redis 其他 client 連線：{len(rothers)} 條"
               + ("（斷開後又連回來，請停掉那個程式）" if rothers and args.close else ""),
               [f"id={c['id']} addr={c['addr']} name={c['name'] or '-'} cmd={c['cmd']} age={c['age']}s"
                for c in rothers])

    c = args.concurrency
    # 取樣器多 1 條；不夠時 run_cell.py 會自動關掉取樣器（D21），所以只算提醒、不算失敗
    ok &= show(c <= pg_slots, f"PostgreSQL 名額：需要 {c}（取樣器另 1 條），max_connections={pg_slots}"
               + ("；取樣器會自動關閉（D21）" if c + 1 > pg_slots >= c else ""))
    ok &= show(c <= redis_slots, f"Redis 名額：需要 {c}，maxclients={redis_slots}")
    fds = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    need_fds = c + 256  # 連線本身，加上 pgbench 每個執行緒的 log 檔、Python 與函式庫自己的檔案
    ok &= show(fds == resource.RLIM_INFINITY or fds >= need_fds,
               f"檔案描述元上限（ulimit -n）：{fds}，至少需要 {need_fds}")

    print("全部通過，可以開始壓測。" if ok else "有項目沒通過，請處理後再跑。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
