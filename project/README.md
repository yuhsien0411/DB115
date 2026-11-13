# 題01｜熱門商品即時計數（統一專案）

PostgreSQL `UPDATE ... view_count + 1` ⟷ Redis `INCR` ＋ 每 T 秒快照回寫。
本資料夾取代早期的 `claude_project/` 與 `codex_project/`（甲方已於 2026-10-24 刪除封存資料夾，D35）。

## 已定案（甲方 2026-10-23 拍板）

| 項目 | 決定 |
|---|---|
| PM | Claude：對接甲方、拆任務、整合、報告，並實作全部程式（D7） |
| Codex | 原核心實驗依 D7 做審查；2026-10-24 甲方指示開始前端，授權實作本機展示與必要 API（D15） |
| 回寫設計 | 絕對累計快照 ＋ `GREATEST`，不用提案中的 delta 回寫；期末報告說明偏離提案的原因 |
| 資料隔離 | 專用資料庫 `q01`（由 `shop` 複製），不動課程的 `shop` |
| 金額型別 | 價格、訂單金額、成交單價沿用 seed 的 `numeric(10,2)`（兩位小數）；D36 撤銷 D16 的整數元 |
| 實驗矩陣 | 依已交提案：2 方案 × 併發 50／200／500 × hot80／uniform1000；暖機 30 秒，正式 60 秒 × 3 次取中位數。hot5 為補充組 |
| 遺失視窗 | T = 1、5 秒，壓測中強制停掉 Redis |
| 量測數據 | AI 只做煙霧測試（短時間、低併發）；正式數據由組員親自跑，並保留原始 log |

### 快照回寫的不變式（Codex 提出）

1. Redis key `q01:<run_id>:views:<product_id>` 在每個 run 開始時，從 PG 的 `view_count` 初始化，之後只會 `INCR`，不清零、不設 TTL、不 `DEL`。
2. 單一 worker 每 T 秒 `MGET` 全部 key，以 `UPDATE ... SET view_count = GREATEST(view_count, snapshot)` 寫回，並寫入 `view_flush_log`。
3. 如果 Redis key 遺失或重啟後回到舊值，該 run 就作廢：停止流量、保留證據，再從 PG 重新初始化一個新 run。舊 worker 必須先停止。
4. 同一個 run 只能用一種寫入模式，A 與 B 不可同時寫同一組商品。

## 目錄與負責人

| 路徑 | 內容 | 負責 | 審查 |
|---|---|---|---|
| `schema.sql` | `q01` 的增量 DDL（可重跑） | Claude | Codex |
| `run_cell.py` | 跑矩陣的一格，並自動對帳 | Claude | Codex |
| `run_matrix.sh` | 依序跑完整矩陣、故障實驗與摘要 | Claude | Codex |
| `tools/summarize.py` | 從結果檔產生報告用的表格 | Claude | Codex |
| `tools/preflight.py` | 開跑前確認額外連線、殘留行程與名額 | Claude | Codex |
| `pg/` | pgbench 腳本、p95 工具、鎖等待取樣、下單交易、PG 矩陣執行腳本 | Claude | Codex |
| `redis/` | 回寫 worker、故障實驗腳本 | Claude | **Codex** |
| `loader/` | 統一負載器（同一套邏輯打 PG 或 Redis，逐筆延遲） | Claude | Codex |
| `results/<YYYYMMDD>/` | 一批正式量測（開跑當天日期，D39）：每輪目錄 `<run_id>/`、`runs.csv`、`failures.csv`、`matrix_*.log`、`summary.md`。第一批在 `results/20261024/` | 組員 | — |
| `results/runs_template.csv`、`failure_template.csv` | 結果欄位格式（兩條線共用，不可擅改） | — | — |
| `security/` | 網站低權限帳號 `q01_web` 的建立與驗證（D18） | Claude | Codex |
| `題01_專題提案.docx`、`.pdf` | 提案定稿（D34）；`排版前備份/` 為排版前的版本 | 甲方 | — |
| `docs/` | 設計書、來源 | Claude | Codex |
| `web/` | 本機電商前端、API（public 表，D37）與功能驗證 | Codex（D15） | 待瀏覽器驗收 |

規則：
- 只改自己負責的路徑；要改別人的檔案或共用格式，先告訴 PM。
- **所有設計與介面都要有 md 文件**（甲方要求，方便後續追蹤）：
  - 新增或修改介面（CLI 參數、檔案格式、Redis key、資料表、CSV 欄位）時，同一次交付要更新 [`docs/interfaces.md`](docs/interfaces.md)。
  - 設計決定與其原因，記在 [`docs/decisions.md`](docs/decisions.md)，依時間追加，不刪除舊紀錄。
  - 每個模組的設計寫在 `docs/design/<模組>.md`。
  - 派給 Codex 的任務單與它的回報都留在 `docs/tasks/`。

## 環境

```bash
createdb -T shop q01                                     # 已建立；重建前先 dropdb q01
psql -X -v ON_ERROR_STOP=1 -d q01 -f project/schema.sql  # 會把計數歸零
pip install --user --break-system-packages -r project/requirements.txt
```

PostgreSQL 16（`max_connections=500`）、Redis 7（RDB 開、AOF 關）、15 GB。主機 16 個邏輯核心，正式實驗時以 Windows 的 `%UserProfile%\.wslconfig`（`[wsl2]` 下 `processors=12`）把 WSL2 限定為 **12 核**，留 4 核給 Windows（D27）。開跑前先執行 `nproc` 確認是 12；改完設定要 Quit Docker Desktop → `wsl --shutdown` → 重開 Docker → Reopen in Container。

## 怎麼跑（組員）

煙霧測試結果在 `results/_smoke/`，**不是正式數據**。正式數據請組員自己跑，並在報告寫明機器規格。

```bash
cd project
# 開跑前確認：沒有額外連線、殘留行程，名額夠用（加 --close 代為斷開連線）
python3 tools/preflight.py
# 主矩陣的一格（每格約 1.5 分鐘）
# 結果預設寫到 results/<今天日期>/（D39）
python3 run_cell.py --backend pg    --scenario hot80       --concurrency 200 --round 1
python3 run_cell.py --backend pg    --scenario uniform1000 --concurrency 200 --round 1
python3 run_cell.py --backend redis --scenario hot80       --concurrency 200 --round 1 --interval 5
# 遺失視窗
python3 redis/crash_test.py --interval 5 --repeat 1

# 或一次跑完：主矩陣 36 格＋遺失視窗 T=1、5 各 3 次＋摘要（約 1 小時 10 分）
bash run_matrix.sh --dry-run   # 先看會跑哪些指令
bash run_matrix.sh --smoke --rounds 1 --concurrency 20   # 先用 1 分鐘確認流程能跑通（寫到 results/_smoke）
bash run_matrix.sh            # 寫到 results/<開跑當天日期>/，跨午夜也同一個資料夾；可用 --out results/<日期> 指定
python3 tools/summarize.py --dir results/20261106 --out results/20261106/summary.md   # 隨時可重建某一批的摘要（不給 --dir 就用最新一批）
```

互動終端機會顯示 `- \ | /` 旋轉圖示、等待階段與已用時間；整批執行另顯示 `[目前/總數]` 與完成百分比。轉存 stderr 或 `TERM=dumb` 時自動關閉動畫。原有指令不需加參數。

摘要的四張表對應題目要求：兩方案的吞吐與 p95、PG 鎖競爭代價（p95 熱點 − p95 均勻）、鎖等待取樣頻率、遺失視窗。算法與報告寫法見 [docs/design/summarize.md](docs/design/summarize.md)。

**補跑第一批的故障實驗（`results/20261024/` 的 T=1、5 只有第 3 次，D29）**：關掉網站後執行，約 1 分鐘。若改為整批重測（`bash run_matrix.sh` 本身就含故障實驗 T=1、5 各 3 次），可不補。

```bash
python3 tools/preflight.py --close
for t in 1 5; do for i in 1 2; do python3 redis/crash_test.py --interval $t --repeat $i --batch 20261024; done; done
python3 tools/summarize.py --dir results/20261024 --out results/20261024/summary.md   # 表 4 的有效次數應變成 3
```

想親眼看高併發的樣子（**不計入正式統計**）：終端機畫面用 `tools/watch_live.py`＋`tools/watch_demo.sh`（D31），網站搶購用 `tools/web_rush.py`（D32），用法見 [tools/README.md](tools/README.md)。

跑 PG 之前請先**停掉網站、關掉 psql 和資料庫瀏覽工具**，因為它們也會占 PG 的連線名額（上限 500）。名額不夠時程式會列出占用者並中止；加 `--close-others` 則由程式代為斷開。500 併發時，鎖等待取樣器會自動關閉（D21）。

各指令的完整參數見 [docs/interfaces.md](docs/interfaces.md)。

## 本機網頁展示與儀表板

2026-10-24 甲方追加指示開始實作（D15）。網站分成前台（顧客：逛商品、購物車、下單、我的訂單）和後台（營運：庫存、全部訂單、計數與回寫、實驗儀表板）（D17）。執行期以低權限帳號 `q01_web` 連線（D18）。D37 起網站直接使用 `q01` 的 public 表，下單呼叫和壓測同一個 `place_order()`；跑正式壓測前要先停掉網站（`tools/preflight.py` 會檢查）。前端不列入三條效能成功判準。

```bash
# 從 /workspace 執行
bash project/security/setup_web_role.sh     # 首次或重跑 schema.sql 後：建立 q01_web 與授權
python3 project/web/server.py               # 啟動網站
# 前台：http://127.0.0.1:8000/
# 後台：終端機印出的 http://127.0.0.1:8000/admin/?token=...
```

完整操作與 Redis 模式見 [web/README.md](web/README.md)；[課程要求與待辦核對](docs/frontend_scope.md)列出效能報告、超賣實驗等仍需完成的交付。已完成 API／資料庫功能驗證，實際瀏覽器排版與點擊驗收待執行。
