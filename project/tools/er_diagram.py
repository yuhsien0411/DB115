import sys
W, HDR, ROW = 250, 36, 26
GAP = 90
X = [20, 20 + W + GAP, 20 + 2 * (W + GAP), 20 + 3 * (W + GAP)]
Y0 = 80
FONT = "'Noto Sans TC','Noto Sans CJK TC','Microsoft JhengHei','PingFang TC',sans-serif"

# (key, 中文名, 欄位[(標記, 名稱, 型別, 註記)])
T = {
 "customers": ("customers｜顧客", [("PK","customer_id","int",""),("","name","text",""),("","email","text",""),("","city","text",""),("","signup_date","date","")]),
 "orders": ("orders｜訂單", [("PK","order_id","int",""),("FK","customer_id","int",""),("","order_date","date",""),("","status","text",""),("","amount","numeric(10,2)","※")]),
 "order_items": ("order_items｜訂單明細", [("PK,FK","order_id","int",""),("PK","line_no","int",""),("FK","product_id","int",""),("","quantity","int",""),("","unit_price","numeric(10,2)","")]),
 "products": ("products｜商品", [("PK","product_id","int",""),("","name","text",""),("","category","text",""),("","price","numeric(10,2)",""),("","stock","int",""),("","view_count","bigint","★")]),
 "view_flush_log": ("view_flush_log｜回寫紀錄 ★", [("PK","flush_id","bigserial",""),("","run_id","text",""),("","flushed_at","timestamptz",""),("FK","product_id","int",""),("","snapshot","bigint","")]),
 "order_requests": ("order_requests｜結帳冪等 ★", [("PK","request_id","uuid",""),("FK,UQ","order_id","int","")]),
 "cart_items": ("cart_items｜購物車 ★", [("PK,FK","customer_id","int",""),("PK,FK","product_id","int",""),("","quantity","int",""),("","added_price","numeric(10,2)",""),("","added_at","timestamptz","")]),
}
Y2 = Y0 + HDR + 6 * ROW + 72  # 第二排
POS = {"customers":(X[0],Y0),"orders":(X[1],Y0),"order_items":(X[2],Y0),"products":(X[3],Y0),
       "order_requests":(X[1],Y2),"cart_items":(X[2],Y2),"view_flush_log":(X[3],Y2)}
NEW = {"view_flush_log", "order_requests", "cart_items"}

out = []
def a(s): out.append(s)
def h(k): return HDR + len(T[k][1]) * ROW

def box(k):
    x, y = POS[k]; title, cols = T[k]; hh = h(k)
    stroke = "#b45309" if k in NEW else "#134e4a"
    head = "#b45309" if k in NEW else "#0f766e"
    a(f'<rect x="{x}" y="{y}" width="{W}" height="{hh}" rx="6" fill="#ffffff" stroke="{stroke}" stroke-width="1.6"/>')
    a(f'<path d="M{x} {y+HDR} V{y+6} a6 6 0 0 1 6 -6 H{x+W-6} a6 6 0 0 1 6 6 V{y+HDR} Z" fill="{head}"/>')
    a(f'<text x="{x+W/2}" y="{y+24}" text-anchor="middle" font-size="15" font-weight="700" fill="#ffffff">{title}</text>')
    for i, (tag, name, typ, note) in enumerate(cols):
        ry = y + HDR + i * ROW
        if i: a(f'<line x1="{x}" y1="{ry}" x2="{x+W}" y2="{ry}" stroke="#e2e8f0"/>')
        if note == "★": a(f'<rect x="{x+1}" y="{ry}" width="{W-2}" height="{ROW}" fill="#fef3c7"/>')
        if note == "※": a(f'<rect x="{x+1}" y="{ry}" width="{W-2}" height="{ROW}" fill="#ede9fe"/>')
        ty = ry + 18
        a(f'<text x="{x+10}" y="{ty}" font-size="11" font-weight="700" fill="#b91c1c">{tag}</text>')
        bold = ' font-weight="700"' if "PK" in tag else ""
        u = ' text-decoration="underline"' if "PK" in tag else ""
        a(f'<text x="{x+52}" y="{ty}" font-size="13" fill="#0f172a"{bold}{u}>{name}{(" "+note) if note else ""}</text>')
        a(f'<text x="{x+W-10}" y="{ty}" text-anchor="end" font-size="12" fill="#64748b">{typ}</text>')

def one(px, py, d):  # d: direction pointing away from the box into the line (+1 right / -1 left / 'down')
    if d == "down":
        for off in (10, 16): a(f'<line x1="{px-8}" y1="{py+off}" x2="{px+8}" y2="{py+off}" stroke="#334155" stroke-width="1.6"/>')
    else:
        for off in (10, 16): a(f'<line x1="{px+d*off}" y1="{py-8}" x2="{px+d*off}" y2="{py+8}" stroke="#334155" stroke-width="1.6"/>')

def many(px, py, d, optional):
    # crow's foot touching the box at (px,py); d = direction from box into the line
    if d == "up":
        for dx in (-9, 0, 9): a(f'<line x1="{px}" y1="{py-14}" x2="{px+dx}" y2="{py}" stroke="#334155" stroke-width="1.6"/>')
        if optional: a(f'<circle cx="{px}" cy="{py-21}" r="5" fill="#fff" stroke="#334155" stroke-width="1.6"/>')
        else: a(f'<line x1="{px-8}" y1="{py-20}" x2="{px+8}" y2="{py-20}" stroke="#334155" stroke-width="1.6"/>')
        return
    for dy in (-9, 0, 9): a(f'<line x1="{px+d*14}" y1="{py}" x2="{px}" y2="{py+dy}" stroke="#334155" stroke-width="1.6"/>')
    if optional: a(f'<circle cx="{px+d*21}" cy="{py}" r="5" fill="#fff" stroke="#334155" stroke-width="1.6"/>')
    else: a(f'<line x1="{px+d*20}" y1="{py-8}" x2="{px+d*20}" y2="{py+8}" stroke="#334155" stroke-width="1.6"/>')

def zero_one(px, py):  # 線從上方接到方框頂端：o| 零或一
    a(f'<line x1="{px-8}" y1="{py-10}" x2="{px+8}" y2="{py-10}" stroke="#334155" stroke-width="1.6"/>')
    a(f'<circle cx="{px}" cy="{py-19}" r="5" fill="#fff" stroke="#334155" stroke-width="1.6"/>')

def many_below(px, py, optional):  # 方框底端 (px,py) 往下接出的線：鴉爪腳朝方框
    for dx in (-9, 0, 9): a(f'<line x1="{px}" y1="{py+14}" x2="{px+dx}" y2="{py}" stroke="#334155" stroke-width="1.6"/>')
    if optional: a(f'<circle cx="{px}" cy="{py+21}" r="5" fill="#fff" stroke="#334155" stroke-width="1.6"/>')
    else: a(f'<line x1="{px-8}" y1="{py+20}" x2="{px+8}" y2="{py+20}" stroke="#334155" stroke-width="1.6"/>')

def hrel(left, right, y, label, many_side, optional, lcard, rcard):
    x1 = POS[left][0] + W; x2 = POS[right][0]
    a(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="#334155" stroke-width="1.6"/>')
    if many_side == "right": one(x1, y, 1); many(x2, y, -1, optional)
    else: many(x1, y, 1, optional); one(x2, y, -1)
    mid = (x1 + x2) / 2
    a(f'<text x="{mid}" y="{y-14}" text-anchor="middle" font-size="13" font-weight="700" fill="#0f172a">{label}</text>')
    a(f'<text x="{x1+6}" y="{y+24}" font-size="12" fill="#475569">{lcard}</text>')
    a(f'<text x="{x2-6}" y="{y+24}" text-anchor="end" font-size="12" fill="#475569">{rcard}</text>')

Wt = X[3] + W + 20
YB = Y2 + h("cart_items") + 40          # 購物車 → 顧客 的繞線高度
LY = YB + 50                             # 圖例
Ht = LY + 24 + 4 * 28 + 20
a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {Wt} {Ht}" width="{Wt}" height="{Ht}" font-family="{FONT}">')
a(f'<rect width="{Wt}" height="{Ht}" fill="#ffffff"/>')
a(f'<text x="20" y="34" font-size="20" font-weight="700" fill="#0f172a">題 1｜熱門商品即時計數 — ER 圖（PostgreSQL，正本）</text>')
a(f'<text x="20" y="58" font-size="12" fill="#475569">Crow&#39;s foot 表示法：|| 恰好一個　o&lt; 零到多個　|&lt; 一到多個。Redis 只存計數器 q01:&lt;run_id&gt;:views:&lt;product_id&gt;，不在本圖內；購物車存在 PostgreSQL（cart_items）。</text>')
for k in T: box(k)
ry = Y0 + HDR + ROW + ROW / 2  # 對齊第二列附近
hrel("customers", "orders", ry, "下單", "right", True, "1", "0..N")
hrel("orders", "order_items", Y0 + HDR + ROW / 2, "包含", "right", False, "1", "1..N")
# products(右) 1 —— order_items(左) 多
x1 = POS["order_items"][0] + W; x2 = POS["products"][0]; y = Y0 + HDR + 2 * ROW + ROW / 2
a(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="#334155" stroke-width="1.6"/>')
many(x1, y, 1, True); one(x2, y, -1)
a(f'<text x="{(x1+x2)/2}" y="{y-14}" text-anchor="middle" font-size="13" font-weight="700" fill="#0f172a">被購買</text>')
a(f'<text x="{x1+6}" y="{y+24}" font-size="12" fill="#475569">0..N</text>')
a(f'<text x="{x2-6}" y="{y+24}" text-anchor="end" font-size="12" fill="#475569">1</text>')
# products 1 —— view_flush_log 多（垂直）
px = POS["products"][0] + W / 2; py1 = Y0 + h("products"); py2 = POS["view_flush_log"][1]
a(f'<line x1="{px}" y1="{py1}" x2="{px}" y2="{py2}" stroke="#334155" stroke-width="1.6"/>')
one(px, py1, "down"); many(px, py2, "up", True)
a(f'<text x="{px+14}" y="{(py1+py2)/2+5}" font-size="13" font-weight="700" fill="#0f172a">回寫 Redis 計數</text>')
a(f'<text x="{px-12}" y="{py1+30}" text-anchor="end" font-size="12" fill="#475569">1</text>')
a(f'<text x="{px-12}" y="{py2-6}" text-anchor="end" font-size="12" fill="#475569">0..N</text>')
# orders 1 —— order_requests 0..1（垂直；order_id UNIQUE）
px = POS["orders"][0] + W / 2; py1 = Y0 + h("orders"); py2 = Y2
a(f'<line x1="{px}" y1="{py1}" x2="{px}" y2="{py2}" stroke="#334155" stroke-width="1.6"/>')
one(px, py1, "down"); zero_one(px, py2)
a(f'<text x="{px+14}" y="{(py1+py2)/2+5}" font-size="13" font-weight="700" fill="#0f172a">網站結帳識別碼</text>')
a(f'<text x="{px-12}" y="{py1+30}" text-anchor="end" font-size="12" fill="#475569">1</text>')
a(f'<text x="{px-12}" y="{py2-6}" text-anchor="end" font-size="12" fill="#475569">0..1</text>')
# products 1 —— cart_items 多（從購物車右側繞到商品左側下方）
x1 = POS["cart_items"][0] + W; yc = Y2 + HDR + ROW + ROW / 2; xm = POS["products"][0] - GAP / 2; yp = Y0 + h("products") - 26; x2 = POS["products"][0]
a(f'<polyline points="{x1},{yc} {xm},{yc} {xm},{yp} {x2},{yp}" fill="none" stroke="#334155" stroke-width="1.6"/>')
many(x1, yc, 1, True); one(x2, yp, -1)
a(f'<text x="{xm-6}" y="{(yc+yp)/2}" text-anchor="end" font-size="13" font-weight="700" fill="#0f172a">放入購物車</text>')
a(f'<text x="{x1+6}" y="{yc+24}" font-size="12" fill="#475569">0..N</text>')
a(f'<text x="{x2-6}" y="{yp-8}" text-anchor="end" font-size="12" fill="#475569">1</text>')
# customers 1 —— cart_items 多（從購物車底部繞到顧客底部）
cx = POS["cart_items"][0] + W / 2; cy = Y2 + h("cart_items"); ux = POS["customers"][0] + W / 2; uy = Y0 + h("customers")
a(f'<polyline points="{cx},{cy} {cx},{YB} {ux},{YB} {ux},{uy}" fill="none" stroke="#334155" stroke-width="1.6"/>')
many_below(cx, cy, True); one(ux, uy, "down")
a(f'<text x="{(cx+ux)/2}" y="{YB-8}" text-anchor="middle" font-size="13" font-weight="700" fill="#0f172a">擁有購物車（每位顧客一台）</text>')
a(f'<text x="{cx+12}" y="{cy+34}" font-size="12" fill="#475569">0..N</text>')
a(f'<text x="{ux+12}" y="{uy+30}" font-size="12" fill="#475569">1</text>')
# 圖例
lx, ly = 20, LY
a(f'<text x="{lx}" y="{ly}" font-size="14" font-weight="700" fill="#0f172a">圖例</text>')
items = [("#fef3c7","★ 本專題新增（view_count、view_flush_log；網站用的 order_requests〔D37〕、cart_items〔D38〕）"),("#ede9fe","※ 刻意保留的冗餘：orders.amount 為衍生值（Σ quantity × unit_price）；Redis 方案下 view_count ＝ 最新回寫快照；unit_price 是成交價、added_price 是加入購物車時的價格，都不是冗餘"),(None,"PK 主鍵（底線）　FK 外鍵　UQ 唯一　order_items 主鍵為 (order_id, line_no)；cart_items 主鍵為 (customer_id, product_id)")]
for i,(c,t) in enumerate(items):
    yy = ly + 24 + i * 28
    if c: a(f'<rect x="{lx}" y="{yy-14}" width="22" height="18" fill="{c}" stroke="#94a3b8"/>')
    a(f'<text x="{lx + (32 if c else 0)}" y="{yy}" font-size="13" fill="#334155">{t}</text>')
a(f'<text x="{lx}" y="{ly+24+3*28+4}" font-size="13" fill="#334155">設計取捨：view_count 與 stock 同在 products 一列 → 熱門商品的計數 UPDATE 會與下單扣庫存爭同一把行鎖（備案：拆成 product_stats）</text>')
a('</svg>')
open(sys.argv[1], "w", encoding="utf-8").write("\n".join(out))
