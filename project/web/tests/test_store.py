"""真實 PostgreSQL 整合驗證（D37：網站直接用 public）。
管理帳號 student 只在 public 準備／清理獨立 fixture（商品與顧客編號 700000 以上），
網站功能、HTTP handler、advisory lock、Redis 回寫皆以 q01_web 執行。
不建立 schema、不改授權；原有 1,000 件商品必須維持原狀。測試必須在網站與壓測都停止時執行。
"""
import asyncio
from decimal import Decimal
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import secrets
import threading
import unittest
import uuid

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('web_server', ROOT / 'server.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
S = 'public'


class CheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = await asyncpg.connect(database='q01', user='student', host='/var/run/postgresql')
        self.store = mod.Store()
        self.ids = []
        self.customers = []
        self.addAsyncCleanup(self.cleanup)
        self.baseline = await self.fingerprint()
        await self.store.start()  # 同時防止和正常展示並行；此連線已是 q01_web。
        self.tag = 'web_test_' + uuid.uuid4().hex
        # 使用獨立的商品／顧客與分類，不改動原有商品的庫存、價格與瀏覽數。
        for i in range(2):
            while True:
                pid = secrets.randbelow(200000) + 700000
                if not await self.conn.fetchval(f'SELECT 1 FROM {S}.products WHERE product_id=$1', pid):
                    break
            await self.conn.execute(f'INSERT INTO {S}.products VALUES($1,$2,$3,$4,100,0)', pid, f'{self.tag}_{i}', self.tag, Decimal('13.45') + i)
            self.ids.append(pid)
            await self.conn.execute(f"INSERT INTO {S}.customers VALUES($1,$2,$3,'測試',current_date)", pid, f'{self.tag}_{i}', f'{self.tag}_{i}@example.invalid')
            self.customers.append(pid)
        self.customer = self.customers[0]
        self.prices = {pid: Decimal('13.45') + i for i, pid in enumerate(self.ids)}

    async def cleanup(self):
        await self.store.close()
        if self.store.r and hasattr(self.store, 'keys'):
            import redis.asyncio as redis
            r = redis.Redis()
            try:
                # run 已結束，只刪本測試產生的 q01:web_* keys。
                await r.delete(*self.store.keys)
            finally:
                await r.aclose()
        if self.customers:
            async with self.conn.transaction():
                await self.conn.execute(f'DELETE FROM {S}.cart_items WHERE customer_id=ANY($1::int[]) OR product_id=ANY($2::int[])', self.customers, self.ids)
                await self.conn.execute(f'DELETE FROM {S}.order_requests WHERE order_id IN (SELECT order_id FROM {S}.orders WHERE customer_id=ANY($1::int[]))', self.customers)
                await self.conn.execute(f'DELETE FROM {S}.order_items WHERE order_id IN (SELECT order_id FROM {S}.orders WHERE customer_id=ANY($1::int[]))', self.customers)
                await self.conn.execute(f'DELETE FROM {S}.orders WHERE customer_id=ANY($1::int[])', self.customers)
                await self.conn.execute(f'DELETE FROM {S}.view_flush_log WHERE product_id=ANY($1::int[])', self.ids)
                await self.conn.execute(f'DELETE FROM {S}.products WHERE product_id=ANY($1::int[])', self.ids)
                await self.conn.execute(f'DELETE FROM {S}.customers WHERE customer_id=ANY($1::int[])', self.customers)
        after = await self.fingerprint()
        await self.conn.close()
        self.assertEqual(self.baseline, after, '測試不得修改原有商品，也不得留下訂單或購物車')

    async def fingerprint(self):
        # 原有商品整列內容＋訂單總筆數：fixture 清乾淨後必須和開始時一模一樣
        return await self.conn.fetchrow(f"""SELECT
            (SELECT md5(string_agg(row_to_json(p)::text,'' ORDER BY product_id)) FROM {S}.products p WHERE product_id <= 1000),
            (SELECT max(order_id) FROM {S}.orders WHERE status <> 'pending'),
            (SELECT count(*) FROM {S}.orders WHERE status = 'pending'),
            (SELECT count(*) FROM {S}.order_requests),
            (SELECT count(*) FROM {S}.cart_items)""")

    def payload(self, items=None, token=None, customer=None, expected=None):
        items = items or [{'product_id': self.ids[0], 'quantity': 1}]
        if expected is None:
            # 依 fixture 原價算購物車預估總額；數量無效時給 0，交由伺服器拒絕。
            ok = all(type(i.get('quantity')) is int for i in items)
            expected = sum((self.prices.get(i['product_id'], Decimal(0)) * i['quantity'] for i in items), Decimal(0)) if ok else Decimal(0)
            expected = f'{expected:.2f}'
        return {'request_id': token or str(uuid.uuid4()), 'customer_id': customer or self.customer,
                'items': items, 'expected_total': expected}

    async def test_checkout_snapshots_decimal_price_and_deducts_stock(self):
        pid = self.ids[0]
        data = self.payload([{'product_id': pid, 'quantity': 2, 'price': 1}])
        data['amount'] = 1
        order = await self.store.checkout(data)
        product = await self.store.pool.fetchrow(f'SELECT * FROM {S}.products WHERE product_id=$1', pid)
        self.assertEqual(product['stock'], 98)
        self.assertEqual(order['amount'], Decimal('26.90'))
        self.assertIs(type(order['amount']), Decimal)
        self.assertIs(type(product['price']), Decimal)
        await self.conn.execute(f'UPDATE {S}.products SET price=price+10 WHERE product_id=$1', pid)
        detail = await self.store.order(order['order_id'])
        self.assertEqual(detail['items'][0]['unit_price'], Decimal('13.45'))
        self.assertEqual(json.loads(json.dumps(detail, default=mod.serial))['items'][0]['unit_price'], '13.45')

    async def test_concurrent_duplicate_request_creates_one_order(self):
        body = self.payload()
        orders = await asyncio.gather(*(self.store.checkout(body) for _ in range(5)))
        self.assertEqual(len({r['order_id'] for r in orders}), 1)
        self.assertEqual(await self.store.pool.fetchval(f'SELECT count(*) FROM {S}.orders WHERE customer_id=$1', self.customer), 1)
        self.assertEqual(await self.store.pool.fetchval(f'SELECT stock FROM {S}.products WHERE product_id=$1', self.ids[0]), 99)
        for different in (self.payload([{'product_id': self.ids[0], 'quantity': 2}], body['request_id']),
                          self.payload(token=body['request_id'], customer=self.customers[1]),
                          self.payload(token=body['request_id'], expected='99.00')):
            with self.assertRaises(mod.Problem) as err:
                await self.store.checkout(different)
            self.assertEqual(err.exception.status, 409)

    async def test_price_change_rejects_order_until_user_reconfirms(self):
        await self.conn.execute(f'UPDATE {S}.products SET price=20 WHERE product_id=$1', self.ids[0])
        stale = self.payload([{'product_id': self.ids[0], 'quantity': 2}])
        self.assertEqual(stale['expected_total'], '26.90')
        with self.assertRaises(mod.Problem) as err:
            await self.store.checkout(stale)
        self.assertEqual(err.exception.status, 409)
        self.assertEqual(err.exception.message, '商品價格已變動：購物車預估 NT$ 26.90，目前 NT$ 40.00。本次未建立訂單，請確認購物車後重新下單。')
        self.assertEqual(await self.store.pool.fetchval(f'SELECT count(*) FROM {S}.orders WHERE customer_id=$1', self.customer), 0)
        self.assertEqual(await self.store.pool.fetchval(f'SELECT stock FROM {S}.products WHERE product_id=$1', self.ids[0]), 100)
        # 使用者看過現價後以新的 request_id 重新下單才成立。
        order = await self.store.checkout(self.payload([{'product_id': self.ids[0], 'quantity': 2}], expected='40'))
        self.assertEqual(order['amount'], Decimal('40.00'))
        # 已成立的舊請求重送（舊版前端可能不帶 expected_total）仍回原訂單；新訂單缺欄位或格式錯誤則拒絕。
        legacy = {k: v for k, v in self.payload([{'product_id': self.ids[0], 'quantity': 2}], str(order['request_id'])).items() if k != 'expected_total'}
        self.assertEqual((await self.store.checkout(legacy))['order_id'], order['order_id'])
        fresh = {k: v for k, v in self.payload().items() if k != 'expected_total'}
        for bad, status in [(fresh, 400), (self.payload(expected=20), 400), (self.payload(expected='1.234'), 400), (self.payload(expected='-1'), 400)]:
            with self.assertRaises(mod.Problem) as err:
                await self.store.checkout(bad)
            self.assertEqual(err.exception.status, status)
        self.assertEqual(await self.store.pool.fetchval(f'SELECT count(*) FROM {S}.orders WHERE customer_id=$1', self.customer), 1)

    async def test_competing_checkouts_do_not_oversell(self):
        await self.store.pool.execute(f'UPDATE {S}.products SET stock=1 WHERE product_id=$1', self.ids[0])
        results = await asyncio.gather(*(self.store.checkout(self.payload()) for _ in range(10)), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual(sum(isinstance(r, mod.Problem) and r.status == 409 for r in results), 9)
        self.assertEqual(await self.store.pool.fetchval(f'SELECT stock FROM {S}.products WHERE product_id=$1', self.ids[0]), 0)

    async def test_insufficient_stock_rolls_back_entire_cart(self):
        await self.store.pool.execute(f'UPDATE {S}.products SET stock=0 WHERE product_id=$1', self.ids[1])
        with self.assertRaises(mod.Problem) as err:
            await self.store.checkout(self.payload([{'product_id': p, 'quantity': 1} for p in self.ids]))
        self.assertEqual(err.exception.status, 409)
        self.assertEqual(err.exception.message, f'{self.tag}_1 庫存不足（目前剩 0 件），本次未建立訂單。')
        self.assertEqual(await self.store.pool.fetchval(f'SELECT stock FROM {S}.products WHERE product_id=$1', self.ids[1]), 0)
        self.assertEqual(await self.store.pool.fetchval(f'SELECT stock FROM {S}.products WHERE product_id=$1', self.ids[0]), 100)
        self.assertEqual(await self.store.pool.fetchval(f'SELECT count(*) FROM {S}.orders WHERE customer_id=$1', self.customer), 0)

    async def test_missing_product_and_invalid_quantities_rejected(self):
        for qty in [0, -1, True, 1.5, None, 10001]:
            with self.assertRaises(mod.Problem):
                await self.store.checkout(self.payload([{'product_id': self.ids[0], 'quantity': qty}]))
        missing = await self.store.pool.fetchval(f'SELECT g FROM generate_series(900000,999999) g WHERE NOT EXISTS(SELECT 1 FROM {S}.products WHERE product_id=g) LIMIT 1')
        with self.assertRaises(mod.Problem):
            await self.store.checkout(self.payload([{'product_id': missing, 'quantity': 1}]))

    async def test_product_read_does_not_count_view(self):
        await self.store.products({'q': [str(self.ids[0])]})
        self.assertEqual(await self.store.pool.fetchval(f'SELECT view_count FROM {S}.products WHERE product_id=$1', self.ids[0]), 0)
        await self.store.view(self.ids[0])
        self.assertEqual(await self.store.pool.fetchval(f'SELECT view_count FROM {S}.products WHERE product_id=$1', self.ids[0]), 1)

    async def test_cart_lookup_returns_current_price_without_counting_view(self):
        await self.conn.execute(f'UPDATE {S}.products SET price=price+1 WHERE product_id=$1', self.ids[0])
        price = await self.store.pool.fetchval(f'SELECT price FROM {S}.products WHERE product_id=$1', self.ids[0])
        ids = f'{self.ids[0]},{self.ids[1]},{self.ids[0]}'
        result = await self.store.api('GET', '/api/store/products/lookup', {'ids': [ids]}, {})
        self.assertEqual([p['product_id'] for p in result['items']], sorted(self.ids[:2]))
        self.assertEqual(next(p for p in result['items'] if p['product_id'] == self.ids[0])['price'], price)
        self.assertEqual(set(result['items'][0]), {'product_id', 'name', 'category', 'price', 'availability', 'stock'})
        self.assertEqual(await self.store.pool.fetchval(f'SELECT sum(view_count) FROM {S}.products WHERE product_id=ANY($1::int[])', self.ids[:2]), 0)
        self.assertEqual((await self.store.api('GET', '/api/store/products/lookup', {}, {}))['items'], [])
        for bad in ['abc', '1,,2', '-1', ','.join(str(i) for i in range(1, 102))]:
            with self.assertRaises(mod.Problem) as err:
                await self.store.api('GET', '/api/store/products/lookup', {'ids': [bad]}, {})
            self.assertEqual(err.exception.status, 400)
        with self.assertRaises(mod.Problem) as err:
            await self.store.api('GET', '/api/admin/products/lookup', {'ids': [str(self.ids[0])]}, {}, admin=True)
        self.assertEqual(err.exception.status, 404)

    async def cart_set(self, pid, qty, customer=None):
        return await self.store.api('POST', '/api/store/cart', {},
                                    {'customer_id': customer or self.customer, 'product_id': pid, 'quantity': qty})

    async def test_cart_persists_in_postgres_with_absolute_quantity(self):
        # D38：購物車存在 cart_items；設定的是絕對數量，重送結果相同；0 表示移除；加入不扣庫存、不計瀏覽數
        a, b = self.ids
        cart = await self.cart_set(a, 2)
        self.assertEqual([(i['product_id'], i['quantity']) for i in cart['items']], [(a, 2)])
        self.assertEqual(cart['total'], Decimal('26.90'))
        await self.cart_set(a, 2)
        cart = await self.cart_set(a, 3)
        self.assertEqual(cart['items'][0]['quantity'], 3)
        await self.cart_set(b, 1)
        got = await self.store.api('GET', '/api/store/cart', {'customer_id': [str(self.customer)]}, {})
        self.assertEqual(sorted((i['product_id'], i['quantity']) for i in got['items']), sorted([(a, 3), (b, 1)]))
        self.assertEqual(set(got['items'][0]), {'product_id', 'name', 'category', 'price', 'availability', 'stock', 'quantity', 'added_price'})
        # 另一位顧客的購物車是空的（以顧客區分）
        self.assertEqual((await self.store.api('GET', '/api/store/cart', {'customer_id': [str(self.customers[1])]}, {}))['items'], [])
        cart = await self.cart_set(b, 0)
        self.assertEqual([i['product_id'] for i in cart['items']], [a])
        await self.cart_set(b, 0)  # 移除不存在的項目也成功
        self.assertEqual(await self.conn.fetchval(f'SELECT stock FROM {S}.products WHERE product_id=$1', a), 100)
        self.assertEqual(await self.conn.fetchval(f'SELECT view_count FROM {S}.products WHERE product_id=$1', a), 0)
        self.assertEqual(await self.conn.fetchval(f'SELECT quantity FROM {S}.cart_items WHERE customer_id=$1 AND product_id=$2', self.customer, a), 3)

    async def test_cart_keeps_added_price_after_price_change(self):
        a = self.ids[0]
        await self.cart_set(a, 1)
        await self.conn.execute(f'UPDATE {S}.products SET price=10 WHERE product_id=$1', a)
        cart = await self.cart_set(a, 2)  # 改數量不會改 added_price
        item = cart['items'][0]
        self.assertEqual((item['added_price'], item['price'], cart['total']), (Decimal('13.45'), Decimal('10.00'), Decimal('20.00')))
        body = json.loads(json.dumps(cart, default=mod.serial))
        self.assertEqual((body['items'][0]['added_price'], body['total']), ('13.45', '20.00'))
        with self.assertRaises(asyncpg.InsufficientPrivilegeError):
            await self.store.pool.execute(f'UPDATE {S}.cart_items SET added_price=0 WHERE customer_id=$1', self.customer)

    async def test_cart_rejects_invalid_input(self):
        a = self.ids[0]
        await self.conn.execute(f'UPDATE {S}.products SET stock=2 WHERE product_id=$1', a)
        with self.assertRaises(mod.Problem) as err:
            await self.cart_set(a, 3)
        self.assertEqual((err.exception.status, err.exception.message), (409, f'{self.tag}_0 庫存不足（目前剩 2 件），無法放入 3 件。'))
        missing = await self.conn.fetchval(f'SELECT g FROM generate_series(900000,999999) g WHERE NOT EXISTS(SELECT 1 FROM {S}.products WHERE product_id=g) LIMIT 1')
        no_customer = await self.conn.fetchval(f'SELECT g FROM generate_series(900000,999999) g WHERE NOT EXISTS(SELECT 1 FROM {S}.customers WHERE customer_id=g) LIMIT 1')
        for args, status in [((missing, 1), 404), ((a, 1, no_customer), 400), ((a, -1), 400), ((a, 1.5), 400),
                             ((a, True), 400), ((a, 10001), 400), (('x', 1), 400)]:
            with self.assertRaises(mod.Problem) as err:
                await self.cart_set(*args)
            self.assertEqual(err.exception.status, status, args)
        with self.assertRaises(mod.Problem) as err:
            await self.store.api('GET', '/api/store/cart', {}, {})
        self.assertEqual(err.exception.status, 400)
        with self.assertRaises(mod.Problem) as err:
            await self.store.api('GET', '/api/admin/cart', {'customer_id': [str(self.customer)]}, {}, admin=True)
        self.assertEqual(err.exception.status, 404)
        self.assertEqual(await self.conn.fetchval(f'SELECT count(*) FROM {S}.cart_items WHERE customer_id=$1', self.customer), 0)

    async def test_checkout_clears_only_ordered_items_in_same_transaction(self):
        a, b = self.ids
        await self.cart_set(a, 2)
        await self.cart_set(b, 1)
        await self.cart_set(a, 1, self.customers[1])  # 別人的購物車不受影響
        body = self.payload([{'product_id': a, 'quantity': 2}])
        order = await self.store.checkout(body)
        left = await self.conn.fetch(f'SELECT customer_id, product_id FROM {S}.cart_items WHERE customer_id=ANY($1::int[]) ORDER BY 1, 2', self.customers)
        self.assertEqual([tuple(r) for r in left], sorted([(self.customer, b), (self.customers[1], a)]))
        # 重送同一請求回同一筆訂單，不會再刪購物車
        await self.cart_set(a, 1)
        self.assertEqual((await self.store.checkout(body))['order_id'], order['order_id'])
        self.assertEqual(await self.conn.fetchval(f'SELECT count(*) FROM {S}.cart_items WHERE customer_id=$1', self.customer), 2)

    async def test_failed_checkout_keeps_cart(self):
        a, b = self.ids
        await self.cart_set(a, 1)
        await self.cart_set(b, 1)
        await self.conn.execute(f'UPDATE {S}.products SET stock=0 WHERE product_id=$1', b)
        await self.conn.execute(f'UPDATE {S}.products SET price=99 WHERE product_id=$1', a)
        for items in ([{'product_id': p, 'quantity': 1} for p in self.ids], [{'product_id': a, 'quantity': 1}]):
            with self.assertRaises(mod.Problem) as err:
                await self.store.checkout(self.payload(items))
            self.assertEqual(err.exception.status, 409)
        self.assertEqual(await self.conn.fetchval(f'SELECT count(*) FROM {S}.cart_items WHERE customer_id=$1', self.customer), 2)
        self.assertEqual(await self.conn.fetchval(f'SELECT count(*) FROM {S}.orders WHERE customer_id=$1', self.customer), 0)

    async def test_only_one_site_process_at_a_time(self):
        other = mod.Store()
        try:
            with self.assertRaises(RuntimeError):
                await other.start()
        finally:
            await other.close()

    async def test_runtime_role_and_price_update_denied(self):
        self.assertEqual(await self.store.pool.fetchval('SELECT current_user'), 'q01_web')
        self.assertEqual(await self.store.owner.fetchval('SELECT current_user'), 'q01_web')
        with self.assertRaises(asyncpg.InsufficientPrivilegeError):
            await self.store.pool.execute(f'UPDATE {S}.products SET price=price WHERE product_id=$1', self.ids[0])

    async def test_store_api_excludes_admin_fields_and_scopes_orders(self):
        def no_admin(value):
            if isinstance(value, dict):
                self.assertFalse({'view_count', 'live_count', 'revenue', 'counter', 'run_id', 'pg_views', 'live_views', 'sold'} & value.keys())
                for child in value.values():
                    no_admin(child)
            elif isinstance(value, list):
                for child in value:
                    no_admin(child)
        for stock, availability in [(11, '有貨'), (10, '剩少量'), (1, '剩少量'), (0, '已售完')]:
            await self.store.pool.execute(f'UPDATE {S}.products SET stock=$1 WHERE product_id=$2', stock, self.ids[0])
            result = await self.store.api('GET', '/api/store/products', {'q': [str(self.ids[0])]}, {})
            self.assertEqual(set(result['items'][0]), {'product_id', 'name', 'category', 'price', 'availability', 'stock'})
            self.assertEqual(result['items'][0]['availability'], availability)
            self.assertEqual(result['items'][0]['stock'], stock)
            self.assertIs(type(result['items'][0]['stock']), int)
            no_admin(result)
        no_admin(await self.store.api('GET', '/api/store/bootstrap', {}, {}))
        detail = await self.store.api('POST', f'/api/store/products/{self.ids[0]}/view', {}, {})
        self.assertEqual(set(detail), {'product_id', 'name', 'category', 'price', 'availability', 'stock'})
        self.assertEqual(detail['stock'], 0)
        no_admin(detail)
        await self.store.pool.execute(f'UPDATE {S}.products SET stock=100 WHERE product_id=$1', self.ids[0])
        own = await self.store.api('POST', '/api/store/orders', {}, self.payload())
        other = await self.store.checkout(self.payload(customer=self.customers[1]))
        no_admin(own)
        rows = await self.store.api('GET', '/api/store/orders', {'customer_id': [str(self.customer)]}, {})
        self.assertEqual([r['order_id'] for r in rows['items']], [own['order_id']])
        no_admin(rows)
        no_admin(await self.store.api('GET', f"/api/store/orders/{own['order_id']}", {'customer_id': [str(self.customer)]}, {}))
        for path, q, status in [('/api/store/orders', {}, 400),
                                (f"/api/store/orders/{own['order_id']}", {}, 400),
                                (f"/api/store/orders/{other['order_id']}", {'customer_id': [str(self.customer)]}, 404)]:
            with self.assertRaises(mod.Problem) as err:
                await self.store.api('GET', path, q, {})
            self.assertEqual(err.exception.status, status)
        for path in ('bootstrap', 'products', 'orders', f"orders/{own['order_id']}", 'dashboard', 'experiments'):
            with self.assertRaises(mod.Problem) as err:
                await self.store.api('GET', '/api/' + path, {}, {})
            self.assertEqual(err.exception.status, 404)

    async def test_admin_inventory_filters_sort_and_sold(self):
        await self.store.checkout(self.payload())
        await self.store.pool.execute(f'UPDATE {S}.products SET stock=3 WHERE product_id=$1', self.ids[1])
        q = {'category': [self.tag], 'sort': ['stock_asc']}
        result = await self.store.api('GET', '/api/admin/products', q, {}, admin=True)
        self.assertEqual([p['stock'] for p in result['items']], [3, 99])
        self.assertEqual(result['items'][1]['sold'], 1)
        result = await self.store.products({**q, 'availability': ['low']}, admin=True)
        self.assertEqual([p['product_id'] for p in result['items']], [self.ids[1]])
        result = await self.store.products({**q, 'sort': ['stock_desc']}, admin=True)
        self.assertEqual([p['stock'] for p in result['items']], [99, 3])
        for status, count in [('in_stock', 1), ('out', 0)]:
            self.assertEqual((await self.store.products({**q, 'availability': [status]}, admin=True))['total'], count)
        with self.assertRaises(mod.Problem):
            await self.store.products({'sort': ['stock;DELETE']}, admin=True)

    async def test_http_token_cookie_and_removed_routes(self):
        loop = asyncio.get_running_loop()
        token = 'test_' + uuid.uuid4().hex
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), mod.handler(self.store, loop, token))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port = httpd.server_address[1]
        def request(path, method='GET', data=None, headers=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                conn.request(method, path, body=data, headers=headers or {})
                r = conn.getresponse()
                return r.status, dict(r.getheaders()), r.read()
            finally:
                conn.close()
        async def get(path, **kwargs):
            return await asyncio.to_thread(request, path, **kwargs)
        try:
            for path in ('/api/admin/bootstrap', '/api/admin/products', '/api/admin/orders', '/api/admin/orders/1', '/api/admin/dashboard', '/api/admin/experiments', '/api/admin/unknown'):
                for method in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'):
                    self.assertEqual((await get(path, method=method))[0], 401)
            for path in ('/admin/', '/admin/admin.js'):
                status, _, body = await get(path)
                self.assertEqual(status, 401)
                self.assertIn('需要後台權杖', body.decode())
                self.assertNotIn('<script', body.decode())
            for value in ('q01_admin=wrong', 'bad@key=wrong'):
                self.assertEqual((await get('/api/admin/dashboard', headers={'Cookie': value}))[0], 401)
            self.assertEqual((await get('/admin/?token=%E6%B8%AC%E8%A9%A6'))[0], 401)
            status, headers, _ = await get('/admin/?token=' + token)
            self.assertEqual(status, 303)
            self.assertEqual(headers['Location'], '/admin/')
            for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/'):
                self.assertIn(flag, headers['Set-Cookie'])
            cookie = {'Cookie': headers['Set-Cookie'].split(';')[0]}
            for path in ('/admin/', '/admin/admin.js', '/api/admin/bootstrap', '/api/admin/products', '/api/admin/orders', '/api/admin/dashboard', '/api/admin/experiments'):
                self.assertEqual((await get(path, headers=cookie))[0], 200)
            status, _, body = await get('/')
            self.assertEqual(status, 200)
            self.assertNotIn('/admin', body.decode())
            self.assertNotIn('營運總覽', body.decode())
            self.assertIn('current-customer', body.decode())
            for path in ('/api/dashboard', '/api/products', '/api/orders', '/api/experiments', '/app.js', '/style.css', '/index.html'):
                self.assertEqual((await get(path))[0], 404)
            status, _, body = await get('/api/store/products?q=' + str(self.ids[0]))
            self.assertEqual(status, 200)
            self.assertEqual(set(json.loads(body)['items'][0]), {'product_id', 'name', 'category', 'price', 'availability', 'stock'})
            body = json.dumps(self.payload())
            self.assertEqual((await get('/api/store/orders', method='POST', data=body, headers={'Content-Type': 'application/json', 'Origin': 'http://elsewhere.invalid'}))[0], 403)
            self.assertEqual((await get('/api/store/orders', method='POST', data=body, headers={'Content-Type': 'text/plain'}))[0], 415)
            self.assertEqual((await get('/api/store/orders', method='POST', data='[]', headers={'Content-Type': 'application/json'}))[0], 400)
            self.assertEqual((await get('/api/store/orders', method='POST', data=body, headers={'Content-Type': 'application/json'}))[0], 200)
            await self.store.pool.execute(f'UPDATE {S}.products SET stock=3 WHERE product_id=$1', self.ids[0])
            rejected = json.dumps(self.payload([{'product_id': self.ids[0], 'quantity': 4}]))
            status, _, response = await get('/api/store/orders', method='POST', data=rejected, headers={'Content-Type': 'application/json'})
            self.assertEqual(status, 409)
            self.assertEqual(json.loads(response)['error'], f'{self.tag}_0 庫存不足（目前剩 3 件），本次未建立訂單。')
            self.assertEqual(await self.store.pool.fetchval(f'SELECT stock FROM {S}.products WHERE product_id=$1', self.ids[0]), 3)
            self.assertEqual(await self.store.pool.fetchval(f'SELECT count(*) FROM {S}.orders WHERE customer_id=$1', self.customer), 1)
        finally:
            await asyncio.to_thread(httpd.shutdown)
            await asyncio.to_thread(httpd.server_close)
            thread.join()

    async def test_redis_flush_and_missing_key_stop(self):
        await self.store.close()
        self.store = mod.Store(counter='redis')
        await self.store.start()
        first, second = self.ids
        self.assertEqual((await self.store.view(first))['live_count'], 1)
        self.assertEqual(await self.store.pool.fetchval(f'SELECT view_count FROM {S}.products WHERE product_id=$1', first), 0)
        # Redis 回應也必須經過前台白名單投影。
        projected = await self.store.api('POST', f'/api/store/products/{first}/view', {}, {})
        self.assertEqual(set(projected), {'product_id', 'name', 'category', 'price', 'availability', 'stock'})
        await self.store.flush()
        self.assertEqual(await self.store.pool.fetchval(f'SELECT view_count FROM {S}.products WHERE product_id=$1', first), 2)
        self.assertEqual(await self.store.pool.fetchval(f'SELECT snapshot FROM {S}.view_flush_log WHERE run_id=$1 AND product_id=$2 ORDER BY flush_id DESC LIMIT 1', self.store.run_id, first), 2)
        await self.store.r.delete(f'q01:{self.store.run_id}:views:{second}')
        with self.assertRaises(mod.Problem) as err:
            await self.store.view(second)
        self.assertEqual(err.exception.status, 503)
        self.assertIsNone(await self.store.r.get(f'q01:{self.store.run_id}:views:{second}'))
        self.assertIsNotNone(self.store.invalid)


if __name__ == '__main__':
    unittest.main()
