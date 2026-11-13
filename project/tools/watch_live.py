"""即時觀察壓測時 PG 連線在做什麼、每秒成功幾筆（D31）。只讀，不寫任何結果檔。

每秒查一次 pg_stat_activity、products 計數總和與 Redis INFO，畫在終端機上。
這是給人看的取樣畫面，不是量測：數字不進 runs.csv，也不能拿來寫報告。

    python3 tools/watch_live.py            # 另開一個終端機跑 tools/watch_demo.sh
    python3 tools/watch_live.py --every 0.5
"""
import argparse
import collections
import shutil
import subprocess
import sys
import time

SQL = """
WITH a AS (
  SELECT state, wait_event_type, wait_event FROM pg_stat_activity
  WHERE datname = current_database() AND backend_type = 'client backend'
    AND pid <> pg_backend_pid()
)
SELECT count(*),
  count(*) FILTER (WHERE wait_event_type = 'Lock' AND wait_event = 'transactionid'),
  count(*) FILTER (WHERE wait_event_type = 'Lock' AND wait_event = 'tuple'),
  count(*) FILTER (WHERE wait_event_type = 'Lock' AND wait_event NOT IN ('transactionid', 'tuple')),
  count(*) FILTER (WHERE state = 'active' AND wait_event_type IS NOT NULL AND wait_event_type <> 'Lock'),
  count(*) FILTER (WHERE state = 'active' AND wait_event_type IS NULL),
  count(*) FILTER (WHERE state <> 'active'),
  (SELECT sum(view_count) FROM products),
  (SELECT sum(view_count) FROM products WHERE product_id <= 5)
FROM a
"""
# 顯示順序與顏色；名稱對應 SQL 的第 2–7 欄
STATES = [("等列鎖 transactionid", "\033[31m"), ("等列鎖 tuple", "\033[35m"),
          ("等其他鎖", "\033[33m"), ("其他等待（IO／LWLock）", "\033[36m"),
          ("正在執行", "\033[32m"), ("閒置（等 client 送下一筆）", "\033[90m")]
RESET, BOLD = "\033[0m", "\033[1m"
SPARK = "▁▂▃▄▅▆▇█"


def psql(db, sql):
    return subprocess.run(["psql", "-X", "-At", "-F", "|", "-d", db, "-c", sql],
                          capture_output=True, text=True, timeout=5)


def spark(values, width):
    vals = list(values)[-width:]
    top = max(vals, default=0) or 1
    return "".join(SPARK[min(7, int(v / top * 7.999))] for v in vals)


def bar(n, total, width, color):
    filled = round(n / total * width) if total else 0
    return f"{color}{'█' * filled}{RESET}{'·' * (width - filled)}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="q01")
    ap.add_argument("--every", type=float, default=1.0, help="更新間隔（秒）")
    ap.add_argument("--redis-port", type=int, default=6379)
    args = ap.parse_args()

    try:
        import redis
        rds = redis.Redis(port=args.redis_port, socket_timeout=1)
    except ImportError:
        rds = None
    max_conn = psql(args.db, "SHOW max_connections").stdout.strip() or "?"
    pg_hist, rd_hist = collections.deque(maxlen=120), collections.deque(maxlen=120)
    prev = None
    print("\033[?25l", end="")  # 藏游標
    try:
        while True:
            t0 = time.time()
            width = max(20, min(50, shutil.get_terminal_size().columns - 40))
            out = [f"{BOLD}q01 即時觀察{RESET}  {time.strftime('%H:%M:%S')}"
                   f"  （取樣畫面，不計入正式統計；Ctrl-C 離開）", ""]

            res = psql(args.db, SQL)
            if res.returncode != 0:
                out.append(f"PG 查詢失敗：{res.stderr.strip()}")
            else:
                f = [int(x or 0) for x in res.stdout.strip().split("|")]
                total, states, vsum, vhot = f[0], f[1:7], f[7], f[8]
                rate = hot_rate = 0.0
                if prev:
                    dt = t0 - prev[0]
                    rate, hot_rate = (vsum - prev[1]) / dt, (vhot - prev[2]) / dt
                prev = (t0, vsum, vhot)
                pg_hist.append(max(rate, 0))
                lock = states[0] + states[1] + states[2]
                active = total - states[5]
                out.append(f"{BOLD}PostgreSQL{RESET}  client 連線 {total} / 上限 {max_conn}")
                for (name, color), n in zip(STATES, states):
                    out.append(f"  {bar(n, total, width, color)} {n:>4}  {name}")
                if active:
                    out.append(f"  → 忙碌的 {active} 條連線裡，{BOLD}{lock} 條（{lock / active:.0%}）"
                               f"卡在等鎖{RESET}")
                share = f"，其中熱門 5 件占 {hot_rate / rate:.0%}" if rate > 0 else ""
                out.append(f"  PG 計數每秒增加 {rate:>10,.0f}{share}")
                out.append(f"  {spark(pg_hist, width + 14)}")
                out.append("  （Redis 模式時 PG 只在每次回寫才跳一下）")

            out.append("")
            try:
                if rds is None:
                    raise RuntimeError("沒有安裝 redis 套件")
                info = rds.info()
                ops = info["instantaneous_ops_per_sec"]
                rd_hist.append(ops)
                out.append(f"{BOLD}Redis{RESET}  client 連線 {info['connected_clients']}"
                           f"  每秒指令 {ops:>10,}")
                out.append(f"  {spark(rd_hist, width + 14)}")
            except Exception as e:
                out.append(f"Redis 讀不到（{type(e).__name__}）")

            sys.stdout.write("\033[H\033[J" + "\n".join(out) + "\n")
            sys.stdout.flush()
            time.sleep(max(0, args.every - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        print("\033[?25h", end="")


if __name__ == "__main__":
    main()
