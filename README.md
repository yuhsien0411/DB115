# 資料庫管理系統（115-1）｜課程環境

**環境包版本：2026-09-10b**（若你手上的 README 沒有這一行，你拿到的是舊版，請重新下載）

這個資料夾會在你自己的電腦上，一次建好整學期要用的三個資料庫：

| | 版本 | 為什麼是這個版本 |
|---|---|---|
| PostgreSQL | 16 | 課程投影片與實作單的每一段 `EXPLAIN` 輸出都是在 16 上量的 |
| Redis | 7 | Ubuntu 24.04 隨附 |
| MongoDB | 8.0 | 與 MongoDB Atlas 免費叢集相同 |

**最重要的一件事**：這個環境的設定是**鎖死**的。所以實作單裡每一句「你應該看到什麼」，
在你的電腦上、在同學的電腦上、在老師的電腦上，會長得**一模一樣**。
不是「差不多」，是一樣。

---

## 一、先裝三個東西（只做一次）

1. **Docker Desktop** — <https://www.docker.com/products/docker-desktop/>
   * Windows 使用者：安裝程式會要你啟用 WSL2，照著做，中間要重開機一次。
2. **Visual Studio Code** — <https://code.visualstudio.com/>
3. **Dev Containers 擴充套件** — 在 VS Code 左側的方塊圖示裡搜尋 `Dev Containers`（發行者是 Microsoft），按安裝。

### ⚠ 一個一定要改的設定

打開 Docker Desktop → Settings → Resources，把 **Memory 調到至少 4 GB**。

原因：專題有幾題要跑 **500 個並行連線**，記憶體不夠會失敗。
（順帶一提，這正是雲端免費方案做不到的事——那邊上限只有 104 條。）

---

## 二、開起來（三步）

1. **解壓縮**，會得到一個叫 **`db115`** 的資料夾。
   * **macOS**：點兩下 zip 即可。
   * **Windows**：右鍵 → **全部解壓縮**，把目的地改成 **`C:\`**（這樣會得到 `C:\db115`）。
   * **請把 `db115` 放在路徑沒有中文、沒有空白的地方**。
     Windows 特別不要放桌面或「文件」——那兩處常被 OneDrive 同步，會和 Docker 打架。
2. 用 VS Code 打開 **`db115` 這個資料夾本身**（File → Open Folder）。

   > ### ⚠ 這一步最容易錯，先確認一件事
   > 打開之後，看 VS Code 左側的檔案列表。
   > **`.devcontainer` 必須直接出現在最上層**，像這樣：
   >
   > ```
   > DB115
   >   > .devcontainer      ← 一定要在這一層看得到
   >   > seed
   >     check.sh
   >     README.md
   > ```
   >
   > **如果 `.devcontainer` 藏在再下一層的資料夾裡，代表你開錯層了。**
   > 重新 Open Folder，選到「直接含有 `.devcontainer` 的那一層」。

3. 右下角會跳出一個提示問你要不要「Reopen in Container」，按下去。
   * 沒跳出來的話：按 `F1`，輸入 `Dev Containers: Reopen in Container`，按 Enter。

   > ### ⚠ 如果跳出來的是「Add Dev Container Configuration Files」
   > 那個視窗會問你 *Where would you like to create your container configuration?*
   >
   > **按 `Esc` 離開，不要選任何一項。**
   > 它的意思是「我找不到設定檔，要不要幫你新建一個」——選下去會建出一個空的設定，把課程的設定蓋掉。
   > **看到它就代表你開錯資料夾層級了**，回到第 2 步重開。

### 第一次會跑很久，不要中斷它

第一次建立會做兩件事：

* 下載並安裝三個資料庫 —— 約 **500 MB**，看你的網路，**3 到 10 分鐘**
* 產生課程的種子資料（100 萬列訂單）—— 約 **1 到 3 分鐘**

**中間看起來像沒反應是正常的。** 中斷它會留下一個半殘的環境，比重跑一次麻煩得多。

**第二次之後就只要幾秒。**

---

## 三、跑完一定要做這件事

容器開好之後，在 VS Code 下方的終端機打：

```bash
bash check.sh
```

它會檢查 18 項：三個資料庫的版本、六個決定 `EXPLAIN` 形狀的設定、種子資料的列數、讀寫是否正常。

**看到「全部 18 項通過」才算好了。**

只要有一項失敗，**先解決它，不要往下做實作單**——環境不對的話，
你會在實作單的第三步卡住，而且完全不知道為什麼。
失敗時把整段輸出貼到課程 Line 群組，那段輸出本身就說明了問題在哪。

---

## 四、連線資訊

三個資料庫都在 `localhost`，帳號密碼都是 `student`。

| | 指令 | 主機 | 連接埠 | 帳號 / 密碼 |
|---|---|---|---|---|
| PostgreSQL | `psql -d shop` | localhost | 5432 | student / student |
| Redis | `redis-cli` | localhost | 6379 | 不需要 |
| MongoDB | `mongosh` | localhost | 27017 | 不需要 |

**三個已經建好的 PostgreSQL 資料庫**：

| 資料庫 | 內容 | 什麼時候用 |
|---|---|---|
| `shop` | 100 萬列訂單（`customers` / `products` / `orders` / `order_items`） | 實作單 1、2、3；講授 W8–W11 |
| `graph` | 購買關係圖（10 萬條邊） | 對照題 5、實作單 5 的 PostgreSQL 那一半 |
| `norm` | 正規化練習的四張表 | 講授 W5–W6 |

> **需要兩個連線的時候**（實作單 3 的交易實驗），在 VS Code 裡開兩個終端機就是兩個連線。
> 這一點雲端的網頁編輯器做不到。

---

## 五、三條要記住的事

1. **不要刪掉這個容器。** 你的資料在容器裡，不在這個資料夾裡。
   真的刪了也不會怎樣——重新 Reopen in Container 會全部重建，只是要再等幾分鐘。
2. **實作單 2 的步驟 3 會改掉種子資料。** 那是故意的。
   想復原就重跑一次 `psql -q -d shop -f seed/seed_shop_1m.sql`（亂數種子是固定的，結果會完全一樣）。
3. **專題報告要寫明你的環境**（機器、記憶體、Docker 配額）。
   全班的**設定**一樣，但**機器**不一樣，所以時間數字本來就會不同——寫明環境不扣分，假裝沒這回事才扣分。

---

## 六、常見狀況

| 症狀 | 原因與處理 |
|---|---|
| 按了 Reopen in Container 之後一直轉 | 正常，第一次要 3–10 分鐘。看 VS Code 右下角的進度，不要按取消 |
| `psql: command not found` | 你還沒進到容器裡。看 VS Code 左下角，要顯示「Dev Container: 資料庫管理系統 115-1」 |
| 檢查環境有幾項失敗 | 按 `F1` → `Dev Containers: Rebuild Container` 重建一次，再跑一次檢查 |
| 電腦重開機之後連不上 | 打開 Docker Desktop，再用 VS Code 開這個資料夾即可 |
| 500 個連線跑不起來 | Docker Desktop 的 Memory 沒調到 4 GB（見第一節） |
