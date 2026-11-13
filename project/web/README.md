# 拾物商店｜本機前台與後台

Python 同源提供 HTML／CSS／JavaScript 與 API，沿用既有 asyncpg／redis，不需新增套件。前台給顧客購物，後台給營運與研究者查閱；這是本機展示，以展示顧客選單代替登入，未串接金流。後台權杖不是正式帳號權限系統。

## 資料與資料庫帳號

D37 起網站**直接使用 `q01` 的 public 表**，和正式壓測是同一份 customers／products／orders／order_items，下單也呼叫壓測同一個 `place_order()`。舊的 `q01_web_demo`、`web/schema.sql` 與 `--init-demo` 已移除。

- 網站另外只用到 `order_requests`（冪等紀錄）、`cart_items`（購物車，D38）和 `view_flush_log`（Redis 模式回寫），都在 [project/schema.sql](../schema.sql)。
- **跑正式壓測前一定要停掉網站。** `run_matrix.sh` 開跑前的 `tools/preflight.py` 看到 `web/server.py` 在跑，或 PG／Redis 有其他連線，就會拒絕開始。
- 重跑 `project/schema.sql` 會清掉 `pending` 訂單（網站與下單實驗都是這個狀態）、`order_requests` 與所有購物車，庫存回到 100、瀏覽數歸零；重跑後要再執行 `setup_web_role.sh`。

從 `/workspace` 執行，網站先保持停止：

```bash
bash project/security/setup_web_role.sh    # 建立／更新 q01_web 與授權（可重跑）
bash project/security/verify_web_role.sh   # 31 項：該做的做得到、不該做的做不到
```

商品 price、訂單 amount、明細 unit_price 使用 PostgreSQL `numeric(10,2)`（D36，與 seed 相同）。API 以兩位小數字串回傳（如 `"1234.50"`），畫面格式為 `NT$ 1,234.50`。D16 期間四捨五入造成的歷史訂單 #1、#5 對帳差額，已於 D36 依 T456 紀錄的原始值還原。

執行期連線池與 advisory lock 預設皆為 `host=127.0.0.1 user=q01_web database=q01`，密碼交由 asyncpg 自動取得，不寫入專案。可用 `--db-user`、`--db-host`、`--db` 指定連線；驗收使用預設 q01_web。連線或權限失敗會提示先執行 setup_web_role.sh。`q01_web` 讀不到顧客的 email、城市等欄位，前台顧客選單只列前 10 位。

## 前台

```bash
python3 project/web/server.py
# 可選：--port 8000 --db q01 --db-user q01_web --db-host 127.0.0.1
```

網址：`http://127.0.0.1:8000/`。Dev Container 轉送 8000 後開啟該連接埠即可。同一程序也提供後台。

- 商品：搜尋、分類、分頁與詳情；詳情成功開啟時計入一次瀏覽，列表與加購物車不計數。
- 供貨狀態（D30）：商品卡與詳情顯示「有貨 · 剩 N 件」（庫存 >10）或醒目色「剩少量 · 剩 N 件」（1–10），0 為「已售完」並停用加入按鈕。API 回傳整數 stock，數字是讀取當下的值，庫存以結帳時為準；前台仍不回傳瀏覽數、營收、計數模式或實驗資料。
- 購物車（D38）：存在 PostgreSQL 的 `cart_items`，每位顧客一台；換分頁、換瀏覽器或重新整理都還在，切換頁首的顧客就是切換購物車。加入、＋／−、輸入數量、移除都呼叫 `POST /api/store/cart`（設定絕對數量，0＝移除）；開啟購物車時以 `GET /api/store/cart` 取回現價、庫存與加入時價格（唯讀、不計瀏覽數），價格與加入時不同時顯示「加入時 NT$ X → 目前 NT$ Y」，載入失敗則提示仍為上次內容。數量上限為目前庫存（每項最多 10,000），+ 按鈕、輸入框 max 與手動輸入一併受限；數量超過目前庫存或已售完時顯示提示並停用下單。加入購物車不扣庫存、也不預留。
- 頁首右側選擇「目前顧客」；我的訂單與明細查詢必須帶 customer_id，明細不屬於該顧客時回 404。此選單可切換展示身份，並非真實身份驗證。
- 結帳：送出購物車畫面的預估總額 `expected_total`；伺服器在同一交易鎖住商品後以現價計算金額，與預估總額不符時回 409、不建立訂單，前端隨即以現價／庫存刷新購物車並列出變動，由使用者確認後再決定是否重新下單（新的 request_id）。相符時在同一交易呼叫 `place_order()` 新增訂單（`pending`）／明細並扣庫存，再寫一筆 `order_requests`，並刪除購物車中這次下單的商品（其他分頁新加入的商品留在車上）；任何一步失敗都整筆回滾，購物車不變。庫存不足整筆回滾，409 回應「商品名稱 庫存不足（目前剩 N 件），本次未建立訂單。」（D30）。相同 request_id 與內容重送只成立一單。
- 503 或斷線時保留原請求（只有這一項存在瀏覽器 localStorage `q01-checkout`），購物車畫面固定為送出當下的內容並鎖住顧客選單，以同一識別碼重試；成功後重新載入購物車。D38 前的 localStorage 購物車 `q01-cart` 啟動時刪除。

前台沒有後台連結；只呼叫 `/api/store/*`。

## 後台

與前台共用上述啟動程序。啟動時讀取 `Q01_ADMIN_TOKEN`；未設定則隨機產生。終端機印出完整入口：

```text
http://127.0.0.1:8000/admin/?token=<啟動時印出的權杖>
```

第一次開啟設為 HttpOnly、SameSite=Strict、Path=/ 的 cookie，303 轉址至 `http://127.0.0.1:8000/admin/`，移除網址權杖。HTTP 存取紀錄略去 query。無有效 cookie 時頁面顯示「需要後台權杖」，不載入腳本／資料；所有 `/api/admin/*` 回 401。權杖保存在本機啟動環境與瀏覽器 cookie，不放在前台程式。重啟後若使用隨機權杖，須重新用終端機網址登入。

後台提供：

- 營運總覽：商品數、訂單數、營收（含 seed 的 100 萬筆歷史訂單）、瀏覽數、熱門商品。
- 庫存：價格（兩位小數）、確切庫存、已售數量（只算 `pending` 訂單，也就是庫存重設後成立的；和「庫存＋已售＝100」對帳）；分類／有貨／低庫存／售完篩選，庫存升冪／降冪排序，每頁 12 件。本版唯讀。
- 訂單管理：最近 100 筆全部顧客訂單與明細；狀態依 `orders.status` 顯示（pending 已成立、completed 已完成、shipped 已出貨、cancelled 已取消），日期為 `order_date`。
- 計數與回寫：PG／Redis 模式、PG 與 Redis 計數及差額、最後回寫、間隔與 run_id。
- 對照實驗：預設顯示正式量測，可由資料來源下拉選單切回煙霧測試；唯讀正式／煙霧 CSV，保留來源、無效輪次與原 notes；沒有正式資料即顯示尚無資料。
- 交付檢查：沿用既有課程交付核對。

營運總覽、庫存、訂單管理與計數與回寫可在「重新整理」旁切換「自動更新」，每 2 秒更新並顯示最後成功時間，偏好保存在瀏覽器；背景分頁、訂單明細開啟、庫存篩選輸入中或上次更新未完成時會暫停，失敗保留原內容並顯示「更新失敗」。

舊 `/api/bootstrap`、`/api/products`、`/api/orders`、`/api/dashboard`、`/api/experiments` 與舊靜態路徑已移除。完整 HTTP 格式見 [interfaces.md §7](../docs/interfaces.md)。

## Redis 展示與停止

正常 Ctrl+C 停止舊網站後切換：

```bash
python3 project/web/server.py --counter redis --interval 5
```

`--interval` 為 1 或 5 秒。同一時間只容許一個網站行程（advisory lock `q01_web`）；正常 SIGINT／SIGTERM 等待在途請求並最後回寫。Redis 異常即停止本輪計數，不自動切回 PG。

run_id=`web_<uuid>`，key=`q01:web_<uuid>:views:<product_id>`，啟動時由 public.products 的 view_count 初始化，回寫到同一欄（與正式實驗 B 相同），無 TTL，有效 run 不清零／刪 key。原子檢查 key 後才 INCR，與正式負載器不同，展示 API 延遲不可當微基準。圖形工具請連 localhost:6379、DB 0；run_id 由後台計數頁或已驗證的 `/api/admin/dashboard` 查閱。不得停掉共用 Redis。

## 測試與稽核

網站停止時，從 `/workspace` 執行：

D37（2026-11-05）改用 public 後：整合測試 14/14、`check_cli.py` 全部 PASS、權限驗證 25/25、`audit.sql` 7 項 PASS；`web_rush.py` 20 位顧客 8 秒搶購對帳 3 項 PASS 後已 `--restore`。這次環境沒有 Node，`frontend.js` 未重跑。

```bash
python3 -m unittest discover -s project/web/tests -v
python3 project/web/tests/check_cli.py
# 若環境已有 Node，可執行純 JS 檢查；無需 npm 套件。
node project/web/tests/frontend.js
bash project/security/verify_web_role.sh
psql -X -v ON_ERROR_STOP=1 -d q01 -f project/web/audit.sql
```

19 項 Python 測試以 q01_web 執行網站功能、HTTP、購物車、結帳與 Redis 回寫，並確認連線池／鎖帳號及改價格被拒。student 只在 public 準備與刪除獨立的測試商品／顧客（編號 700000 以上）及其訂單；不建立 schema、不改授權。測試會消耗 `q01_order_id_seq`，清理後不重設序列（訂單編號有缺號是正常的）。Redis 僅刪除該測試已結束 run 的 keys。每項結束時核對原有 1,000 件商品的整列指紋、seed 訂單與 `pending` 訂單數都和開始時相同。網站與壓測都要停止時才能跑。

frontend.js 使用簡化 DOM 驗證兩個前台頁、六個後台頁、兩位小數金額格式、顧客切換與結果不明時原請求重試。當前容器無 Node／瀏覽器，本次以工具內建 V8 執行相同函式；紀錄在 `tests/frontend-v8.log`。瀏覽器排版與實際點擊仍待 PM 人工驗收。這些測試不是正式效能或超賣比較實驗。

[功能稽核操作單](../docs/audit.md)／[唯讀 SQL](audit.sql)／[T4 回報](../docs/tasks/codex_T4_report.md)／[T5 回報](../docs/tasks/codex_T5_report.md)／[T6 回報](../docs/tasks/codex_T6_report.md)。
