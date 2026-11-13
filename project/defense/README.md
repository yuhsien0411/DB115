# 個人架構防禦的實驗腳本（D40）

回答稿：`pdf/防禦題庫_19題回答稿.md`。本資料夾的每支腳本對應一題，輸出存在 `out/`（AI 於 2026-11-12 跑的版本）。**錄影前請自己重跑**，以自己的輸出為準。

## 資料庫

- 唯讀查詢跑在 `q01`。
- 會改資料的實驗跑在 **`q01_defense`**，它由 `q01` 複製而來，不影響正式數據與網站。
- 重建方式：先確認 `q01` 沒有連線，再執行

  ```bash
  dropdb q01_defense; createdb -T q01 q01_defense
  ```

## 腳本一覽

| 題 | 腳本 | 跑在 | 會改資料嗎 |
|---|---|---|---|
| 01 | `psql -X -e -d q01 -f defense/d01_fd.sql` | q01 | 否 |
| 02 | `psql -X -e -d q01 -f defense/d02_amount.sql` | q01 | 否 |
| 03 | `psql -X -e -d q01_defense -f defense/d03_price.sql` | q01_defense | 否（最後 ROLLBACK） |
| 04 | `psql -X -e -d q01 -f defense/d04_candidates.sql`；`psql -X -e -d q01_defense -f defense/d04_stale_stats.sql` | 兩者 | 後者會：庫存設為 1,000,000，並新增 5,000 張 pending 訂單 |
| 05 | `bash defense/d05_count_sql.sh`；`python3 defense/d05_n1_cost.py` | q01 | 否。前者暫時開 q01_web 的 log_statement 並啟動網站（port 8765），只打 GET，結束時還原 |
| 06 | `bash security/verify_web_role.sh` | q01 | 否（每項都 ROLLBACK） |
| 07 | `python3 defense/d07_index_cost.py` | q01_defense | 建表 vfl_a、vfl_b |
| 08 | `psql -X -e -d q01 -f defense/d08_unused_index.sql` | q01 | 否 |
| 09 | `psql -X -e -d q01_defense -f defense/d09_column_order.sql` | q01_defense | 建表；**要先跑 07**（用到 vfl_a） |
| 10 | `python3 defense/d10_read_skew.py` | q01_defense | 下 2 張小訂單 |
| 11 | `bash defense/d11_make_nolock.sh`，再 `python3 defense/d11_oversell.py` | q01_defense | 建 place_order_nolock()；商品 2 會超賣 |
| 12 | `bash defense/d12_crash.sh` | q01_defense | **會讓整個 PostgreSQL 強制中止再開機**。先停網站、關掉 psql 與其他連線 |
| 13 | `bash defense/d13_price.sh`；`bash defense/d13_waits.sh` | q01_defense | 庫存設為 1 億、新增大量訂單；**要先跑 11**（用到 nolock） |
| 15 | `psql -X -e -d q01_defense -f defense/d15_step_fail.sql` | q01_defense | 否（失敗的交易回滾；序列會前進 1） |
| 16 | `bash defense/d16_c200.sh` | q01_defense | 計數增加 |
| 18 | `psql -X -e -d q01_defense -f defense/d18_scale.sql` | q01_defense | 暫建 1,000 萬列的 orders_10x，結束時刪除；約 15 秒 |
| 20 | `bash defense/d20_loader_bound.sh` | 私有 Redis（port 6390） | 否。跑完即關閉，不碰課程 Redis |

防 14、16、19 主要用正式數據 `results/20261106/summary.md`。防 17 是 MongoDB 組的題目，本組不適用。

以上指令都從 `project/` 執行。

## 注意

- 13、16、20 是煙霧量測：每格 20–25 秒、只跑一次，不是正式矩陣。報告與錄影時要這樣講。
- 跑 13、16、20 時不要同時跑正式矩陣。它們會占用 CPU 與 PG 連線。
