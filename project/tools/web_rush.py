"""對展示網站發動搶購（D32）：很多位虛擬顧客同時瀏覽、下單，開著後台看數字變化。

只打 /api/store/*。D37 起網站直接寫 q01 的 public（和壓測同一份表），但不計入正式統計，數字不能拿來寫報告；
跑完要做正式壓測前，先 --restore 並停掉網站。
流量與正式實驗相同是 hot80：80% 打商品 1–5，其餘打 6–1000。熱門商品庫存約 100 件，
幾秒內就會賣完，之後含熱門商品的訂單會收到 409「庫存不足」。結束時用 SQL 對帳，確認沒有超賣。

    python3 tools/web_rush.py                     # 100 位顧客、60 秒
    python3 tools/web_rush.py --users 200 --seconds 30 --order-rate 0.2
    python3 tools/web_rush.py --restore           # 刪掉搶購訂單、把庫存加回去（瀏覽數不還原）

搶購訂單的 request_id（存在 order_requests）都以 f1a5f1a5- 開頭，--restore 只動這些訂單。
"""
import argparse
import collections
from decimal import Decimal
import json
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

S = "public"
PREFIX = 0xF1A5F1A5          # request_id 的前 32 位元，用來辨認搶購訂單
LIKE = "f1a5f1a5-%"
HOT = range(1, 6)
RESET, BOLD, RED, GREEN, GRAY = "\033[0m", "\033[1m", "\033[31m", "\033[32m", "\033[90m"


def psql(sql, db="q01"):
    # student 經 unix socket，與 run_cell.py 相同；網站帳號 q01_web 沒有刪訂單的權限
    res = subprocess.run(["psql", "-X", "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-d", db, "-c", sql],
                         capture_output=True, text=True, timeout=10)
    if res.returncode:
        raise RuntimeError(res.stderr.strip())
    return [line.split("|") for line in res.stdout.splitlines() if line]


def pick():
    return random.choice(HOT) if random.random() < 0.8 else random.randint(6, 1000)


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.count = collections.Counter()
        self.lat = collections.defaultdict(list)       # 全程延遲（ms），算 p95
        self.window = collections.defaultdict(list)    # 最近一秒

    def add(self, kind, ms=None):
        with self.lock:
            self.count[kind] += 1
            if ms is not None:
                self.lat[kind].append(ms)
                self.window[kind].append(ms)

    def take_window(self):
        with self.lock:
            w, self.window = self.window, collections.defaultdict(list)
        return w


def p95(values):
    if not values:
        return 0.0
    s = sorted(values)
    return s[max(0, -(-len(s) * 95 // 100) - 1)]   # nearest-rank，與 summarize.py 相同


def post(url, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        e.read()
        status = e.code
    return status, (time.perf_counter() - t0) * 1000


def order(args, customers, stats):
    items = [{"product_id": pick(), "quantity": random.randint(1, 2)}
             for _ in range(random.randint(1, 2))]
    rid = str(uuid.UUID(int=(PREFIX << 96) | random.getrandbits(96)))
    try:
        # 下單必須帶購物車預估總額（現價計算）；先以唯讀查詢取現價，不計瀏覽數
        ids = ",".join(str(i["product_id"]) for i in items)
        with urllib.request.urlopen(f"{args.url}/api/store/products/lookup?ids={ids}", timeout=args.timeout) as r:
            prices = {p["product_id"]: Decimal(p["price"]) for p in json.loads(r.read())["items"]}
    except Exception:
        stats.add("order_err")
        return
    expected = sum((prices.get(i["product_id"], Decimal(0)) * i["quantity"] for i in items), Decimal(0))
    body = {"request_id": rid, "customer_id": random.choice(customers), "items": items,
            "expected_total": f"{expected:.2f}"}
    try:
        status, ms = post(f"{args.url}/api/store/orders", body, args.timeout)
        kind = {200: "order_ok", 409: "order_soldout"}.get(status, "order_err")
        stats.add(kind, ms if kind == "order_ok" else None)
    except Exception:
        stats.add("order_unknown")   # 逾時或斷線：可能已經成立，結束時以 SQL 為準


def shopper(args, customers, stats, stop):
    while not stop.is_set():
        try:
            status, ms = post(f"{args.url}/api/store/products/{pick()}/view", {}, args.timeout)
            stats.add("view_ok" if status == 200 else "view_err", ms)
        except Exception:
            stats.add("view_err")
        if random.random() < args.order_rate:
            order(args, customers, stats)
        if args.think:
            time.sleep(random.uniform(0, 2 * args.think))


def snapshot():
    stock, total = psql(f"SELECT coalesce(sum(stock),0), (SELECT coalesce(max(order_id),0) FROM {S}.orders) FROM {S}.products")[0]
    return int(stock), int(total)


def hot_stock():
    return {int(p): (n, int(s)) for p, n, s in
            psql(f"SELECT product_id,name,stock FROM {S}.products WHERE product_id<=5 ORDER BY 1")}


def draw(args, stats, start_stock, elapsed, prev, hot):
    w = stats.take_window()
    c = stats.count
    rate = lambda k: c[k] - prev.get(k, 0)
    out = [f"{BOLD}拾物商店 搶購中{RESET}  {elapsed:4.0f} / {args.seconds:g} 秒"
           f"  {args.users} 位顧客同時下手  {GRAY}（展示用，不計入正式統計）{RESET}", ""]
    out.append(f"  瀏覽      每秒 {rate('view_ok'):>6,}   累計 {c['view_ok']:>9,}"
               f"   p95 {p95(w['view_ok']):>7.0f} ms")
    out.append(f"  {GREEN}成功下單{RESET}  每秒 {rate('order_ok'):>6,}   累計 {c['order_ok']:>9,}"
               f"   p95 {p95(w['order_ok']):>7.0f} ms")
    out.append(f"  {RED}售完被拒{RESET}  每秒 {rate('order_soldout'):>6,}   累計 {c['order_soldout']:>9,}")
    bad = c["view_err"] + c["order_err"] + c["order_unknown"]
    out.append(f"  錯誤／逾時          累計 {bad:>9,}")
    out += ["", f"  {BOLD}熱門商品剩餘庫存{RESET}"]
    for pid, (name, s) in hot.items():
        full = max(start_stock.get(pid, s), 1)
        n = round(s / full * 30)
        color = RED if s == 0 else GREEN
        tag = f"{RED}已售完{RESET}" if s == 0 else f"剩 {s}"
        out.append(f"  #{pid} {name:<10} {color}{'█' * n}{RESET}{'·' * (30 - n)} {tag}")
    sys.stdout.write("\033[H\033[J" + "\n".join(out) + "\n")
    sys.stdout.flush()
    return dict(c)


def rush(args):
    try:
        with urllib.request.urlopen(f"{args.url}/api/store/bootstrap", timeout=5) as r:
            customers = [x["customer_id"] for x in json.load(r)["customers"]]
    except Exception as e:
        sys.exit(f"連不到網站 {args.url}（{type(e).__name__}）。請先啟動 web/server.py。")
    stock0, max_order0 = snapshot()
    start_hot = {p: s for p, (_, s) in hot_stock().items()}
    stats, stop = Stats(), threading.Event()
    threads = [threading.Thread(target=shopper, args=(args, customers, stats, stop), daemon=True)
               for _ in range(args.users)]
    tty = sys.stdout.isatty()
    if tty:
        print("\033[?25l", end="")
    t0 = time.time()
    for t in threads:
        t.start()
    prev = {}
    try:
        while (elapsed := time.time() - t0) < args.seconds:
            time.sleep(1)
            hot = hot_stock()
            if tty:
                prev = draw(args, stats, start_hot, elapsed, prev, hot)
            elif int(elapsed) % 5 == 0:
                print(f"{elapsed:4.0f}s {dict(stats.count)} 熱門庫存 {[s for _, s in hot.values()]}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if tty:
            print("\033[?25h", end="")
    print("\n等在途請求結束…", flush=True)
    for t in threads:
        t.join(args.timeout + 5)
    report(stats, stock0, max_order0, time.time() - t0)


def report(stats, stock0, max_order0, seconds):
    c = stats.count
    stock1, _ = snapshot()
    db_rush, db_qty, other_qty, neg = (int(x) for x in psql(f"""
        SELECT (SELECT count(*) FROM {S}.order_requests WHERE order_id>{max_order0} AND request_id::text LIKE '{LIKE}'),
          (SELECT coalesce(sum(i.quantity),0) FROM {S}.order_items i JOIN {S}.order_requests r USING(order_id)
             WHERE i.order_id>{max_order0} AND r.request_id::text LIKE '{LIKE}'),
          (SELECT coalesce(sum(i.quantity),0) FROM {S}.order_items i LEFT JOIN {S}.order_requests r USING(order_id)
             WHERE i.order_id>{max_order0} AND coalesce(r.request_id::text, '') NOT LIKE '{LIKE}'),
          (SELECT count(*) FROM {S}.products WHERE stock<0)""")[0])
    print(f"\n{BOLD}搶購結束{RESET}（{seconds:.0f} 秒）")
    print(f"  瀏覽成功 {c['view_ok']:,}（p95 {p95(stats.lat['view_ok']):.0f} ms），錯誤 {c['view_err']:,}")
    print(f"  下單：成功 {c['order_ok']:,}（p95 {p95(stats.lat['order_ok']):.0f} ms），售完被拒 {c['order_soldout']:,}，"
          f"錯誤 {c['order_err']:,}，逾時未知 {c['order_unknown']:,}")
    print(f"\n{BOLD}SQL 對帳{RESET}")
    checks = [
        (c["order_ok"] <= db_rush <= c["order_ok"] + c["order_unknown"],
         f"資料庫的搶購訂單 {db_rush:,} 筆，介於成功回應 {c['order_ok']:,} 與 成功＋逾時 {c['order_ok'] + c['order_unknown']:,} 之間"),
        (stock0 - stock1 == db_qty + other_qty,
         f"庫存總共減少 {stock0 - stock1:,} 件 ＝ 搶購訂單 {db_qty:,} 件 ＋ 期間其他訂單 {other_qty:,} 件"),
        (neg == 0, f"庫存為負的商品 {neg} 件（沒有超賣）"),
    ]
    for ok, text in checks:
        print(f"  {GREEN + 'PASS' if ok else RED + 'FAIL'}{RESET}  {text}")
    print(f"\n要把搶購訂單與庫存恢復原狀：python3 tools/web_rush.py --restore")
    sys.exit(0 if all(ok for ok, _ in checks) else 1)


def restore():
    n, qty = (int(x) for x in psql(f"""
        SELECT count(DISTINCT o.order_id), coalesce(sum(i.quantity),0)
        FROM {S}.order_requests o LEFT JOIN {S}.order_items i USING(order_id)
        WHERE o.request_id::text LIKE '{LIKE}'""")[0])
    if not n:
        print("沒有搶購訂單，不需要恢復。")
        return
    # 一個交易：先把數量加回庫存，再刪明細與訂單
    res = subprocess.run(["psql", "-X", "-q", "-1", "-v", "ON_ERROR_STOP=1", "-d", "q01"], text=True,
                         capture_output=True, input=f"""
        CREATE TEMP TABLE rush ON COMMIT DROP AS
          SELECT order_id FROM {S}.order_requests WHERE request_id::text LIKE '{LIKE}';
        UPDATE {S}.products p SET stock = p.stock + q.n
          FROM (SELECT product_id, sum(quantity) AS n FROM {S}.order_items
                WHERE order_id IN (SELECT order_id FROM rush) GROUP BY product_id) q
          WHERE p.product_id = q.product_id;
        DELETE FROM {S}.order_requests WHERE order_id IN (SELECT order_id FROM rush);
        DELETE FROM {S}.order_items WHERE order_id IN (SELECT order_id FROM rush);
        DELETE FROM {S}.orders WHERE order_id IN (SELECT order_id FROM rush);
    """)
    if res.returncode:
        sys.exit(f"恢復失敗，資料沒有變動：{res.stderr.strip()}")
    print(f"已刪除搶購訂單 {n:,} 筆，庫存加回 {qty:,} 件。瀏覽數沒有還原；其他訂單不受影響。")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--users", type=int, default=100, help="同時搶購的虛擬顧客數")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--order-rate", type=float, default=0.1, help="每次瀏覽後下單的機率")
    ap.add_argument("--think", type=float, default=0, help="每位顧客兩次動作間的平均停頓（秒）")
    ap.add_argument("--timeout", type=float, default=35, help="單一請求逾時（網站本身最多等 30 秒）")
    ap.add_argument("--restore", action="store_true", help="刪掉搶購訂單、把庫存加回去")
    args = ap.parse_args()
    restore() if args.restore else rush(args)


if __name__ == "__main__":
    main()
