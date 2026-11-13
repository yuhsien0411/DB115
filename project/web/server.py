#!/usr/bin/env python3
"""本機電商網站與儀表板：直接使用 q01 的 public 表，下單呼叫與壓測相同的 place_order()（D37），購物車存在 cart_items（D38）。
正式壓測仍使用既有 CLI；壓測前 tools/preflight.py 會要求先停掉本網站。"""
import argparse
import asyncio
import csv
import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hmac
from http.cookies import CookieError, SimpleCookie
import os
import secrets
from pathlib import Path
import re
import signal
import threading
import uuid
from urllib.parse import parse_qs, urlsplit

import asyncpg

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT.parent / 'results'


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def serial(value):
    if isinstance(value, (datetime.date, datetime.datetime, uuid.UUID)):
        return str(value)
    if isinstance(value, Decimal):
        # 金額 numeric(10,2)：輸出成字串（如 "1234.50"），避免轉 float 失去精度；前端以 Number() 顯示
        return f'{value:.2f}'
    raise TypeError(type(value).__name__)


def integer(value, low=1, high=1000000):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise Problem(400, '請輸入有效的正整數。')
    result = int(value)
    if not low <= result <= high:
        raise Problem(400, f'數值須介於 {low} 與 {high}。')
    return result


LOCK_NAME = 'q01_web'   # 同一時間只允許一個網站行程（advisory lock 的鍵）
CUSTOMER_PICKER = 10    # 前台顧客選單只列前 10 位（public 有 5 萬位）


class Store:
    def __init__(self, db='q01', counter='pg', interval=5,
                 db_user='q01_web', db_host='127.0.0.1'):
        self.db, self.s, self.counter, self.interval = db, 'public', counter, interval
        self.connection = dict(database=db, user=db_user, host=db_host)
        self.run_id = 'web_' + uuid.uuid4().hex
        self.r = None
        self.task = None
        self.guard = asyncio.Lock()
        self.invalid = None
        self.last_flush = None

    async def start(self):
        self.pool = await asyncpg.create_pool(**self.connection, min_size=1, max_size=6,
                                               command_timeout=15)
        self.owner = await asyncpg.connect(**self.connection)
        locked = await self.owner.fetchval('SELECT pg_try_advisory_lock(hashtextextended($1,0))', LOCK_NAME)
        if not locked:
            await self.owner.close()
            await self.pool.close()
            raise RuntimeError('已有網站執行中，請先停止舊網站。')
        for table, decision in (('order_requests', 'D37'), ('cart_items', 'D38')):
            if not await self.owner.fetchval('SELECT to_regclass($1)', f'public.{table}'):
                await self.close()
                raise RuntimeError(f'缺少 public.{table}，請先套用 project/schema.sql 的 {table} 段落（{decision}）。')
        if self.counter == 'redis':
            import redis.asyncio as redis
            self.r = redis.Redis(host='localhost', port=6379, socket_timeout=2,
                                 socket_connect_timeout=2, decode_responses=True)
            self.redis_identity = (await self.r.info('server'))['run_id']
            rows = await self.pool.fetch(f'SELECT product_id,view_count FROM {self.s}.products ORDER BY product_id')
            self.ids = [x['product_id'] for x in rows]
            self.keys = [f'q01:{self.run_id}:views:{i}' for i in self.ids]
            self.last = [x['view_count'] for x in rows]
            await self.r.mset(dict(zip(self.keys, self.last)))
            self.task = asyncio.create_task(self.flush_loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.r:
            if not self.invalid:
                try:
                    await self.flush()
                except Exception:
                    pass
            await self.r.aclose()
        if hasattr(self, 'owner') and not self.owner.is_closed():
            await self.owner.close()
        if hasattr(self, 'pool'):
            await self.pool.close()

    async def flush_loop(self):
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.flush()
            except Exception as e:
                self.invalid = 'Redis 展示已停止計數，請保留現況並重啟展示。'
                print('展示回寫失敗：', type(e).__name__, flush=True)
                return

    async def flush(self):
        async with self.guard:
            if self.invalid:
                raise Problem(503, self.invalid)
            vals = await self.r.mget(self.keys)
            if ((await self.r.info('server'))['run_id'] != self.redis_identity
                    or any(x is None for x in vals)):
                raise Problem(503, 'Redis 重啟或計數 key 遺失。')
            snap = [int(x) for x in vals]
            if any(a < b for a, b in zip(snap, self.last)):
                raise Problem(503, 'Redis 計數倒退。')
            await self.pool.execute(f'''
                WITH s AS (SELECT * FROM unnest($1::int[], $2::bigint[]) AS t(pid,n)),
                u AS (UPDATE {self.s}.products p SET view_count=GREATEST(p.view_count,s.n)
                      FROM s WHERE p.product_id=s.pid AND s.n>p.view_count
                      RETURNING p.product_id,p.view_count)
                INSERT INTO {self.s}.view_flush_log(run_id,product_id,snapshot)
                SELECT $3,product_id,view_count FROM u''', self.ids, snap, self.run_id)
            self.last = snap
            self.last_flush = datetime.datetime.now(datetime.timezone.utc).isoformat()

    async def products(self, q, admin=False):
        page = integer(q.get('page', ['1'])[0], high=10000)
        search = q.get('q', [''])[0][:100]
        category = q.get('category', [''])[0][:50]
        where = "WHERE ($1='' OR name ILIKE '%'||$1||'%' OR product_id::text=$1) AND ($2='' OR category=$2)"
        if admin:
            status = q.get('availability', [''])[0]
            filters = {'': '', 'in_stock': ' AND stock>10', 'low': ' AND stock BETWEEN 1 AND 10',
                       'out': ' AND stock=0'}
            sort = q.get('sort', ['id'])[0]
            sorts = {'id': 'product_id', 'stock_asc': 'stock,product_id', 'stock_desc': 'stock DESC,product_id'}
            if status not in filters or sort not in sorts:
                raise Problem(400, '庫存篩選或排序無效。')
            where += filters[status]
            order_by = sorts[sort]
        else:
            order_by = 'product_id'
        rows = await self.pool.fetch(f'SELECT * FROM {self.s}.products p {where} ORDER BY {order_by} LIMIT 12 OFFSET $3', search, category, (page-1)*12)
        count = await self.pool.fetchval(f'SELECT count(*) FROM {self.s}.products {where}', search, category)
        if not admin:
            return {'items': [self.store_product(r) for r in rows], 'total': count, 'page': page}
        # 已售＝庫存重設（schema.sql）後成立的訂單，也就是 status='pending'；seed 的 100 萬筆歷史訂單不算，
        # 才能和「庫存＋已售＝100」對帳。order_items 沒有 product_id 索引，逐列子查詢會掃 200 萬列明細，
        # 所以先挑出 pending 訂單（少量）再經主鍵找明細，一次算完這一頁。
        sold = dict(await self.pool.fetch(f'''
            SELECT i.product_id, sum(i.quantity)::bigint FROM {self.s}.orders o
            JOIN {self.s}.order_items i USING (order_id)
            WHERE o.status = 'pending' AND i.product_id = ANY($1::int[]) GROUP BY 1''',
            [r['product_id'] for r in rows]))
        return {'items': [{**dict(r), 'sold': sold.get(r['product_id'], 0)} for r in rows],
                'total': count, 'page': page}

    async def lookup(self, q):
        # 購物車刷新現價／庫存用：唯讀，不計瀏覽數（不可改用 /view）。
        raw = q.get('ids', [''])[0]
        ids = sorted({integer(x) for x in raw.split(',')}) if raw else []
        if len(ids) > 100:
            raise Problem(400, '一次最多查詢 100 件商品。')
        rows = await self.pool.fetch(f'SELECT * FROM {self.s}.products WHERE product_id=ANY($1::int[]) ORDER BY product_id', ids)
        return {'items': [self.store_product(r) for r in rows]}

    @staticmethod
    def store_product(row):
        # 白名單投影：新增後台欄位時也不會意外進入前台。
        return {**{k: row[k] for k in ('product_id', 'name', 'category', 'price', 'stock')},
                'availability': '有貨' if row['stock'] > 10 else '剩少量' if row['stock'] else '已售完'}

    async def cart(self, customer, conn=None):
        # 購物車存在 cart_items（D38）：現價、庫存每次從 products 讀；added_price 是加入當下的價格。唯讀、不計瀏覽數。
        rows = await (conn or self.pool).fetch(f'''
            SELECT c.quantity, c.added_price, p.* FROM {self.s}.cart_items c
            JOIN {self.s}.products p USING (product_id)
            WHERE c.customer_id = $1 ORDER BY c.added_at, c.product_id''', customer)
        return {'customer_id': customer,
                'items': [{**self.store_product(r), 'quantity': r['quantity'], 'added_price': r['added_price']}
                          for r in rows],
                'total': sum((r['price'] * r['quantity'] for r in rows), Decimal(0))}

    async def set_cart(self, data):
        # 設定某件商品的「絕對數量」（重送同一請求結果相同）；0 表示移除。加入購物車不扣庫存。
        customer = integer(data.get('customer_id'))
        pid = integer(data.get('product_id'))
        qty = integer(data.get('quantity'), low=0, high=10000)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 同一位顧客的購物車修改依序進行；不同顧客互不影響
                await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', f'cart:{customer}')
                if qty == 0:
                    await conn.execute(f'DELETE FROM {self.s}.cart_items WHERE customer_id=$1 AND product_id=$2', customer, pid)
                    return await self.cart(customer, conn)
                if not await conn.fetchval(f'SELECT 1 FROM {self.s}.customers WHERE customer_id=$1', customer):
                    raise Problem(400, '請選擇有效顧客。')
                # 不鎖商品列：這裡的庫存只是上限提示，結帳時仍會鎖列重新檢查
                p = await conn.fetchrow(f'SELECT name, price, stock FROM {self.s}.products WHERE product_id=$1', pid)
                if not p:
                    raise Problem(404, '找不到商品。')
                if qty > p['stock']:
                    raise Problem(409, f"{p['name']} 庫存不足（目前剩 {p['stock']} 件），無法放入 {qty} 件。")
                # added_price 只在第一次加入時寫入；之後只改數量
                await conn.execute(f'''
                    INSERT INTO {self.s}.cart_items (customer_id, product_id, quantity, added_price)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (customer_id, product_id) DO UPDATE SET quantity = EXCLUDED.quantity''',
                    customer, pid, qty, p['price'])
                return await self.cart(customer, conn)

    async def view(self, pid):
        pid = integer(pid)
        if self.invalid:
            raise Problem(503, self.invalid)
        async with self.guard:
            if self.counter == 'pg':
                row = await self.pool.fetchrow(f'UPDATE {self.s}.products SET view_count=view_count+1 WHERE product_id=$1 RETURNING *', pid)
                if not row:
                    raise Problem(404, '找不到商品。')
                return dict(row)
            row = await self.pool.fetchrow(f'SELECT * FROM {self.s}.products WHERE product_id=$1', pid)
            if not row:
                raise Problem(404, '找不到商品。')
            try:
                if (await self.r.info('server'))['run_id'] != self.redis_identity:
                    raise ValueError('Redis restarted')
                # 僅供功能展示：原子檢查 key，避免 INCR 默默從零重建；非正式微基準。
                n = await self.r.eval("if redis.call('EXISTS',KEYS[1])==0 then return -1 else return redis.call('INCR',KEYS[1]) end", 1, f'q01:{self.run_id}:views:{pid}')
                if n < 0:
                    raise ValueError('Key missing')
                return {**dict(row), 'live_count': n}
            except Exception:
                self.invalid = 'Redis 連線或計數異常，本輪展示已停止計數。請重啟展示。'
                raise Problem(503, self.invalid)

    async def checkout(self, data):
        try:
            token = uuid.UUID(data.get('request_id', ''))
        except (ValueError, TypeError, AttributeError):
            raise Problem(400, '缺少有效的結帳識別碼。')
        customer = integer(data.get('customer_id'))
        raw = data.get('items')
        if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
            raise Problem(400, '購物車必須包含 1–100 個項目。')
        items = {}
        for item in raw:
            if not isinstance(item, dict):
                raise Problem(400, '購物車格式錯誤。')
            pid = integer(item.get('product_id'))
            qty = integer(item.get('quantity'), high=10000)
            items[pid] = integer(items.get(pid, 0) + qty, high=10000)
        # 使用者在購物車看到的預估總額（字串、最多兩位小數）；與現價算出的總額不符就不成立訂單。
        raw_total = data.get('expected_total')
        expected = None
        if raw_total is not None:
            if not isinstance(raw_total, str) or not re.fullmatch(r'\d{1,8}(\.\d{1,2})?', raw_total):
                raise Problem(400, '預估總額格式錯誤。')
            expected = Decimal(raw_total)
        async with self.pool.acquire() as conn:
            async with conn.transaction(isolation='read_committed'):
                await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', str(token))
                old = await conn.fetchrow(f'SELECT o.* FROM {self.s}.order_requests r JOIN {self.s}.orders o USING (order_id) WHERE r.request_id=$1', token)
                if old:
                    old_items = await conn.fetch(f'SELECT product_id,quantity FROM {self.s}.order_items WHERE order_id=$1', old['order_id'])
                    if (old['customer_id'] != customer or {x['product_id']:x['quantity'] for x in old_items} != items
                            or (expected is not None and old['amount'] != expected)):
                        raise Problem(409, '此結帳識別碼已用於另一筆內容，請重新確認購物車。')
                    oid = old['order_id']
                else:
                    # 重送已成立的舊請求可不帶 expected_total；新訂單一律必填。
                    if expected is None:
                        raise Problem(400, '缺少預估總額 expected_total。')
                    if not await conn.fetchval(f'SELECT 1 FROM {self.s}.customers WHERE customer_id=$1', customer):
                        raise Problem(400, '請選擇有效顧客。')
                    products = await conn.fetch(f'SELECT * FROM {self.s}.products WHERE product_id=ANY($1::int[]) ORDER BY product_id FOR UPDATE', list(items))
                    if len(products) != len(items):
                        raise Problem(409, '有商品不存在，請更新購物車。')
                    for p in products:
                        if p['stock'] < items[p['product_id']]:
                            raise Problem(409, f"{p['name']} 庫存不足（目前剩 {p['stock']} 件），本次未建立訂單。")
                    total = sum(p['price'] * items[p['product_id']] for p in products)
                    if total > Decimal('99999999.99'):
                        raise Problem(400, '訂單金額超過 numeric(10,2) 上限。')
                    # 商品已 FOR UPDATE 鎖住：比對通過後到寫入前，價格不會再變。
                    if total != expected:
                        raise Problem(409, f'商品價格已變動：購物車預估 NT$ {expected:,.2f}，目前 NT$ {total:,.2f}。本次未建立訂單，請確認購物車後重新下單。')
                    # 寫入交給壓測同一個 place_order()（schema.sql）：同一交易內再鎖一次已持有的列、
                    # 寫 orders／order_items、扣庫存。上面已鎖列並檢查過，正常不會回傳 NULL。
                    pids = [p['product_id'] for p in products]
                    oid = await conn.fetchval(f'SELECT {self.s}.place_order($1, $2::int[], $3::int[])',
                                              customer, pids, [items[p] for p in pids])
                    if oid is None:
                        raise Problem(409, '商品不存在或庫存不足，本次未建立訂單，請更新購物車。')
                    await conn.execute(f'INSERT INTO {self.s}.order_requests(request_id, order_id) VALUES($1, $2)', token, oid)
                    # 同一交易內清掉購物車裡已下單的商品（D38）：訂單成立與清購物車一起提交、一起回滾。
                    # 只刪這次下單的商品；結帳期間在別的分頁新加入的商品留在車上。
                    await conn.execute(f'DELETE FROM {self.s}.cart_items WHERE customer_id=$1 AND product_id=ANY($2::int[])', customer, pids)
        return await self.order(oid)

    async def order(self, oid, customer=None):
        row = await self.pool.fetchrow(f'SELECT o.*,r.request_id,c.name AS customer_name FROM {self.s}.orders o JOIN {self.s}.customers c USING(customer_id) LEFT JOIN {self.s}.order_requests r USING(order_id) WHERE order_id=$1 AND ($2::int IS NULL OR o.customer_id=$2)', integer(oid, high=2**31 - 1), customer)  # orders.order_id 是 int；新訂單從 1,000,001 起
        if not row:
            raise Problem(404, '找不到訂單。')
        items = await self.pool.fetch(f'SELECT i.*,p.name FROM {self.s}.order_items i JOIN {self.s}.products p USING(product_id) WHERE order_id=$1 ORDER BY line_no', row['order_id'])
        return {**dict(row), 'items': [dict(x) for x in items]}

    async def dashboard(self):
        row = dict(await self.pool.fetchrow(f'SELECT count(*) AS products,sum(stock) AS stock,sum(view_count)::bigint AS pg_views FROM {self.s}.products'))
        row.update(dict(await self.pool.fetchrow(f'SELECT count(*) AS orders,coalesce(sum(amount),0) AS revenue FROM {self.s}.orders')))
        rows = [dict(x) for x in await self.pool.fetch(f'SELECT product_id,name,category,view_count FROM {self.s}.products ORDER BY view_count DESC,product_id LIMIT 5')]
        row.update(counter=self.counter, interval=self.interval, last_flush=self.last_flush,
                   error=self.invalid, run_id=self.run_id, top=rows, live_views=None)
        if self.r and not self.invalid:
            try:
                vals = await self.r.mget(self.keys)
                if any(v is None for v in vals):
                    raise ValueError('Missing key')
                row['live_views'] = sum(int(v) for v in vals)
            except Exception:
                row['error'] = 'Redis 狀態無法讀取，請檢查服務。'
        return row

    async def api(self, method, path, q, data, admin=False):
        is_admin = path.startswith('/api/admin/')
        if is_admin and not admin:
            raise Problem(401, '需要後台權杖。')
        if not (is_admin or path.startswith('/api/store/')):
            raise Problem(404, '找不到此功能。')
        prefix = '/api/admin' if is_admin else '/api/store'
        route = path[len(prefix):]
        if method == 'GET' and route == '/bootstrap':
            cats = await self.pool.fetch(f'SELECT DISTINCT category FROM {self.s}.products ORDER BY category')
            # 只列前幾位當展示用的顧客選單；網站帳號只能讀 customer_id、name 兩欄（不含 email 等個資）
            customers = await self.pool.fetch(f'SELECT customer_id, name FROM {self.s}.customers ORDER BY customer_id LIMIT {CUSTOMER_PICKER}')
            result = {'categories': [r['category'] for r in cats], 'customers': [dict(r) for r in customers]}
            if is_admin:
                result['counter'] = self.counter
            return result
        if method == 'GET' and route == '/products':
            return await self.products(q, admin=is_admin)
        if not is_admin and method == 'GET' and route == '/products/lookup':
            return await self.lookup(q)
        if not is_admin and method == 'POST' and re.fullmatch(r'/products/\d+/view', route):
            return self.store_product(await self.view(route.split('/')[2]))
        if not is_admin and method == 'GET' and route == '/cart':
            return await self.cart(integer(q.get('customer_id', [None])[0]))
        if not is_admin and method == 'POST' and route == '/cart':
            return await self.set_cart(data)
        if not is_admin and method == 'POST' and route == '/orders':
            return await self.checkout(data)
        if method == 'GET' and route == '/orders':
            customer = None if is_admin else integer(q.get('customer_id', [None])[0])
            rows = await self.pool.fetch(f'SELECT o.*,c.name AS customer_name FROM {self.s}.orders o JOIN {self.s}.customers c USING(customer_id) WHERE ($1::int IS NULL OR customer_id=$1) ORDER BY order_id DESC LIMIT 100', customer)
            return {'items': [dict(r) for r in rows]}
        if method == 'GET' and re.fullmatch(r'/orders/\d+', route):
            customer = None if is_admin else integer(q.get('customer_id', [None])[0])
            return await self.order(route.split('/')[-1], customer)
        if is_admin and method == 'GET' and route == '/dashboard':
            return await self.dashboard()
        if is_admin and method == 'GET' and route == '/experiments':
            # 正式結果依批次放在 results/<YYYYMMDD>/（D39），新的在前；煙霧測試在 results/_smoke/
            result = {'runs': [], 'failures': [], 'batches': []}
            batches = sorted((p for p in RESULTS.iterdir() if p.is_dir() and re.fullmatch(r'\d{8}', p.name)),
                             key=lambda p: p.name, reverse=True) if RESULTS.is_dir() else []
            for prefix, kind, batch in [*((b, 'formal', b.name) for b in batches), (RESULTS / '_smoke', 'smoke', '')]:
                found = False
                for filename, target in [('runs.csv', 'runs'), ('failures.csv', 'failures')]:
                    p = prefix / filename
                    if p.is_file():
                        found = True
                        with p.open(newline='') as f:
                            for row in csv.DictReader(f):
                                result[target].append({**row, 'source': kind, 'batch': batch})
                if found and kind == 'formal':
                    result['batches'].append(batch)
            return result
        raise Problem(404, '找不到此功能。')


def handler(store, loop, admin_token):
    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, fmt, *args):
            # 權杖只在啟動資訊中顯示，避免帶 query 的存取紀錄再洩漏。
            print(f'HTTP {self.command} {urlsplit(self.path).path}', flush=True)

        def authorized(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
                value = cookie.get('q01_admin')
                return value is not None and hmac.compare_digest(value.value.encode(), admin_token.encode())
            except (CookieError, ValueError, TypeError):
                return False

        def send_body(self, status, body, content_type, headers=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def send_json(self, status, payload):
            self.send_body(status, json.dumps(payload, ensure_ascii=False, default=serial).encode(), 'application/json')

        def do_GET(self):
            url = urlsplit(self.path)
            path = url.path
            if path.startswith('/api/'):
                return self.dispatch()
            if path in ('/admin', '/admin/'):
                token = parse_qs(url.query).get('token', [None])[0]
                if token is not None and hmac.compare_digest(token.encode(), admin_token.encode()):
                    cookie = SimpleCookie()
                    cookie['q01_admin'] = admin_token
                    cookie['q01_admin']['path'] = '/'
                    cookie['q01_admin']['httponly'] = True
                    cookie['q01_admin']['samesite'] = 'Strict'
                    return self.send_body(303, b'', 'text/html',
                                          {'Set-Cookie': cookie.output(header='').strip(), 'Location': '/admin/'})
                if not self.authorized():
                    return self.send_body(401, '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>需要後台權杖</title><h1>需要後台權杖</h1><p>請使用網站啟動終端機提供的後台網址。</p></html>'.encode(), 'text/html')
            if path.startswith('/admin/') and not self.authorized():
                return self.send_json(401, {'error': '需要後台權杖。'})
            filenames = {'/': 'store/index.html', '/store/store.js': 'store/store.js',
                         '/admin': 'admin/index.html', '/admin/': 'admin/index.html',
                         '/admin/admin.js': 'admin/admin.js', '/common.css': 'common.css', '/common.js': 'common.js'}
            if path not in filenames:
                return self.send_json(404, {'error': '找不到頁面。'})
            name = filenames[path]
            content_type = {'html':'text/html', 'js':'text/javascript', 'css':'text/css'}[name.split('.')[-1]]
            self.send_body(200, (ROOT / 'static' / name).read_bytes(), content_type)

        def do_POST(self):
            self.dispatch()

        do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_POST

        def dispatch(self):
            try:
                url = urlsplit(self.path)
                admin = self.authorized()
                if url.path.startswith('/api/admin/') and not admin:
                    raise Problem(401, '需要後台權杖。')
                data = {}
                if self.command == 'POST':
                    origin = self.headers.get('Origin')
                    if origin and origin != 'http://' + self.headers.get('Host', ''):
                        raise Problem(403, '請從展示網站操作。')
                    if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        raise Problem(415, '請使用 JSON 格式。')
                    n = integer(self.headers.get('Content-Length', '0'), high=32768)
                    try:
                        data = json.loads(self.rfile.read(n))
                    except (ValueError, UnicodeDecodeError):
                        raise Problem(400, 'JSON 格式錯誤。')
                    if not isinstance(data, dict):
                        raise Problem(400, '請提供 JSON 物件。')
                url = urlsplit(self.path)
                future = asyncio.run_coroutine_threadsafe(store.api(self.command, url.path, parse_qs(url.query), data, admin=admin), loop)
                # 不在用戶端逾時後取消交易；相同 request_id 可安全查回已提交的訂單。
                result = future.result(timeout=30)
                self.send_json(200, result)
            except Problem as e:
                self.send_json(e.status, {'error': e.message})
            except Exception as e:
                print('API error:', type(e).__name__, flush=True)
                self.send_json(503, {'error': '服務暫時無法完成操作。若是結帳，請保留購物車並以同一識別碼重試。'})
    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', default='q01')
    ap.add_argument('--db-user', default='q01_web')
    ap.add_argument('--db-host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--counter', choices=['pg', 'redis'], default='pg')
    ap.add_argument('--interval', type=int, choices=[1, 5], default=5)
    args = ap.parse_args()
    loop = asyncio.new_event_loop()
    async def prepare():
        store = Store(args.db, counter=args.counter, interval=args.interval,
                      db_user=args.db_user, db_host=args.db_host)
        try:
            await store.start()
        except Exception:
            await store.close()
            raise
        return store
    try:
        store = loop.run_until_complete(prepare())
    except (asyncpg.PostgresError, OSError) as e:
        print(f'資料庫連線或權限失敗（{type(e).__name__}）。請先執行 bash project/security/setup_web_role.sh，並確認 --db-user、--db-host 與 PostgreSQL 狀態。', flush=True)
        loop.close()
        raise SystemExit(1)
    except RuntimeError as e:
        print(str(e), flush=True)
        loop.close()
        raise SystemExit(1)
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    class LocalServer(ThreadingHTTPServer):
        daemon_threads = False  # 關閉時等待在途請求，再做最後回寫。
    admin_token = os.environ.get('Q01_ADMIN_TOKEN') or secrets.token_urlsafe(32)
    try:
        server = LocalServer(('127.0.0.1', args.port), handler(store, loop, admin_token))
    except OSError:
        asyncio.run_coroutine_threadsafe(store.close(), loop).result(timeout=20)
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()
        print('網站無法綁定連接埠，請確認 --port 未被使用。', flush=True)
        raise SystemExit(1)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f'Q01 網站：http://127.0.0.1:{args.port} （{args.counter}，schema={store.s}）', flush=True)
    from urllib.parse import quote
    print(f'後台：http://127.0.0.1:{args.port}/admin/?token={quote(admin_token, safe="")}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        asyncio.run_coroutine_threadsafe(store.close(), loop).result(timeout=20)
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()


if __name__ == '__main__':
    main()
