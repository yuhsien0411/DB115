# tools

- `er_diagram.py`：產生 ER 圖 SVG。最初複製自已刪除的 `claude_project/tools`，金額型別為 numeric(10,2)（D36 撤銷 D16 的 int）；2026-11-06 補上網站用的 `order_requests`（D37）與 `cart_items`（D38），期末報告用這一版（提案定稿裡的 ER 圖沒有這兩張表）。

  ```bash
  python3 tools/er_diagram.py er_diagram.svg
  rsvg-convert -z 2 er_diagram.svg -o er_diagram.png
  ```

  `view_flush_log` 已改為實作的快照版（`run_id`、`snapshot`，D2、D34）。

- `build_proposal.py`：由 `proposal/proposal_題01.md`（正文）、`proposal/er_diagram.png`、`proposal/schema_proposal.sql`（附錄）產生提案 Word 與 PDF（D34）。會檢查正文只占第 1 頁、附錄從第 2 頁開始；在 Word 手改後重跑會覆蓋 docx。**輸入的 `proposal/` 已由甲方刪除（D35），目前無法重跑**；定稿以 `project/題01_專題提案.docx`、`.pdf` 為準。

  ```bash
  python3 tools/build_proposal.py   # 輸出 題01_專題提案.docx、題01-C_專題提案.pdf
  ```

- `summarize.py`：從一個批次（`results/<YYYYMMDD>/`，D39）的 `runs.csv`、`failures.csv` 產生報告用的四張表（D23）；不給 `--dir` 時用最新一批。設計見 [../docs/design/summarize.md](../docs/design/summarize.md)。

  ```bash
  python3 tools/summarize.py --dir results/20261024 --out results/20261024/summary.md
  python3 tools/summarize.py --runs results/_smoke/runs.csv --failures results/_smoke/failures.csv   # 煙霧測試
  ```

- `preflight.py`：開始壓測前確認 PG、Redis 沒有額外連線、沒有殘留行程，名額夠用（D26）。`run_matrix.sh` 開頭會自動執行。設計見 [../docs/design/preflight.md](../docs/design/preflight.md)。

  ```bash
  python3 tools/preflight.py                    # 只檢查
  python3 tools/preflight.py --close            # 斷開其他連線後再確認
  bash tools/test_preflight.sh                  # 自我測試（會斷開其他連線）
  ```

- `watch_live.py`、`watch_demo.sh`：觀察用，**不計入正式統計**（D31）。一個終端機開即時畫面，另一個跑短壓測；結果只寫到 `results/_watch/`，不進 `summary.md` 和網站儀表板。

  ```bash
  python3 tools/watch_live.py                   # 終端機 1：每秒畫一次連線狀態、計數速率、Redis 指令數
  bash tools/watch_demo.sh                      # 終端機 2：PG hot80 → PG uniform1000 → Redis hot80，200 併發各 30 秒
  ```

  畫面數字是取樣，跑的時間也比正式短（暖機 5 秒、量測 30 秒），不能拿來寫報告。

- `web_rush.py`（下單前以 `/api/store/products/lookup` 取現價算 `expected_total`）：對展示網站發動搶購，開著後台看數字變化，**不計入正式統計**（D32）。100 位虛擬顧客以 hot80 分布同時瀏覽、下單，終端機顯示每秒瀏覽／成交／售完被拒與熱門商品剩餘庫存；結束時用 SQL 對帳（訂單數、庫存減少量、沒有負庫存）。搶購訂單的 `request_id` 以 `f1a5f1a5-` 開頭，`--restore` 只刪這些訂單並把庫存加回去，瀏覽數不還原。

  ```bash
  python3 tools/web_rush.py                     # 先啟動 web/server.py，後台開「自動更新」
  python3 tools/web_rush.py --users 200 --seconds 30 --order-rate 0.2
  python3 tools/web_rush.py --restore           # 結束後恢復展示資料
  ```
