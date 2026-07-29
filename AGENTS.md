# 台V Pulse 開發指引

本文件記錄適用於整個 repository 的長期規則。實作前另讀 `docs/CURRENT_STATE.md`，但以實際程式碼與測試為準。

## 專案目標

台V Pulse 是本機優先、供個人或小型 VTuber 團隊使用的台灣 VTuber YouTube 公開數據監測與分析工具。它收集頻道、影片、直播、訂閱、觀看與同接快照，並提供同量級內容環境、趨勢比較及自有頻道工作區。它不是 YouTube 官方產品，也不是雲端多租戶服務。

## 技術架構與主要套件

- 前端：Next.js 16 App Router、React 19、TypeScript 5、全域 CSS。
- 建置／執行：vinext、Vite 8、Cloudflare Vite plugin；Node.js 需求為 `>=22.13.0`。
- 本機資料服務：Python 3.11+，以標準函式庫實作 HTTP server、背景排程器與 YouTube Data API v3 client；Windows 所需的 IANA 時區資料固定使用 `requirements.txt` 的 `tzdata`，由 `start-local.ps1` 安裝至 `work/python-packages`。
- 資料庫：SQLite，預設為 `work/tai_v_pulse.sqlite3`；實際 schema 與相容性遷移集中在 `collector/server.py`。
- `drizzle-orm`、`db/`、`drizzle/` 與 `worker/` 目前是前端／Cloudflare scaffold，不是本機 SQLite 的資料來源真相。
- 本機埠：前端 `127.0.0.1:3000`，資料服務 `127.0.0.1:8787`；前端透過 `NEXT_PUBLIC_TRACKER_API` 存取資料服務。

## 重要目錄

- `app/`：Next.js 頁面、client dashboard、共用頁首、法律聲明與樣式。
- `app/dashboard.tsx`：監測首頁、探索、規則設定、已收錄頻道及詳細資料。
- `app/insights/`：內容環境與同量級分析。
- `app/trends/`：多指標趨勢、排行與固定頻道比較。
- `app/creator/`：多頻道工作區、Studio 匯入及手動補值。
- `collector/server.py`：SQLite schema、YouTube API、配額、排程、分類、分析與所有本機 API。
- `tests/`：Python collector 單元測試與前端 server-render 測試。
- `work/`：本機資料庫、PID、log 與執行期 Python 套件；不得提交或打包分享。
- `public/`：靜態資源。
- `windows/`：Windows 圖形化啟動器與單檔安裝程式的 C# 原始碼。
- `worker/`、`db/`、`drizzle/`、`.openai/`：尚未成為主要執行路徑的部署 scaffold。

## 開發與程式碼規範

- UI 使用繁體中文，保留「監測首頁、內容環境、趨勢圖表、頻道工作區」四個固定入口。
- 背景更新只能局部更新資料，不得整頁重新整理、重設捲動位置或關閉使用者正在看的內容。
- 互動元件需支援滑鼠、鍵盤焦點與合理的行動版退化；缺值顯示 `—` 或「資料累積中」，不得以 `0` 偽裝缺失資料。
- 公開資料、Studio 私人資料與手動補值必須保持來源可辨識；衝突要保留並標示，不得靜默覆蓋。
- 新增 API 或資料欄位時，同步更新 Python payload、TypeScript type、UI 缺值處理與測試。
- 延續現有簡單依賴策略；除非有明確需求，不要另建第二套後端、ORM schema 或狀態來源。
- 不要把一次性聊天內容、猜測或使用者私人頻道數據寫入文件或程式碼。

## 資料來源與處理原則

- 只使用官方 YouTube Data API v3；不得爬取 YouTube 網頁。
- 自動探索只收錄名稱、說明或關鍵字自述為台 V，且達最低公開訂閱門檻的頻道；手動加入與自有工作區例外沿用現有規則。
- 搜尋配額與一般配額分開計算，保留安全比例；可批次的 channels、videos 與同接請求必須合併。
- 快照是歷史分析的依據。沒有足夠的 7／30 日資料時不得硬算漲跌；停機期間漏掉的即時同接不可回填。
- 內容主題是可檢查的規則分類，不是 YouTube Analytics 官方分類。直播、一般影片與 Shorts 必須分開；「聯動」是可與主題並存的附加屬性。
- 同級預設以基準頻道訂閱數的 0.5～2 倍定義，且基準頻道不納入同級中位數。
- 「公開觀看黏著度」固定定義為最近 30 日內容觀看中位數除以目前訂閱數，不得描述成回訪觀眾、留存率或不重複觀眾。
- 時間保存為可比較的 API/UTC 時間；介面顯示時轉為使用者時區。

## 安全、隱私與授權

- `YOUTUBE_API_KEY` 只可放在 `.env`；不得出現在聊天、原始碼、log、Git、測試 fixture 或發佈包。
- `.env`、`work/`、Studio 原始匯出檔、SQLite 資料庫與使用者私人分析資料不得提交或上傳。
- Studio 原始檔只在記憶體中解析；持久化僅保存結構化資料、來源批次與雜湊。
- `TAI_V_PULSE_EDITION=public` 的公開 API 快照保留上限為 30 天；作者 `personal` 版可由本人選擇長期或永久保存。不可把私人豁免帶入對外預設。
- 排除頻道會刪除其本機監測資料並加入黑名單；維持明確確認與可理解的後果提示。
- 專案使用 PolyForm Noncommercial License 1.0.0。修改或散布時保留 `LICENSE`、`NOTICE` 與必要署名；不得擅自改成允許商用。
- 每位使用者自行申請 API Key；任何分享或打包都不得夾帶作者的 Key 或資料庫。
- 對外測試包必須使用 `scripts/package-public.ps1` 的白名單流程，排除 `.env`、`work/`、SQLite、Studio 原始檔、相依套件、建置快取與 Git metadata。
- 對外 EXE 必須使用 `scripts/build-windows-installer.ps1`，沿用同一份白名單內容並驗證安裝後不含私人資料。
- 診斷報告不得包含 `.env`、API Key、SQLite 或 Studio 原始檔；新增 LOG 時同步檢查 `export-diagnostics.ps1` 的白名單與遮蔽規則。
- 對外包內的 PowerShell 腳本必須為 UTF-8 BOM，並以 Windows PowerShell 5.1 parser 驗證，避免繁體中文系統誤判編碼。

## 常用指令

```powershell
# 安裝
npm install

# 第一次啟動會建立 .env；填入 Key 後再次執行
.\start-local.ps1
.\start-local.ps1 -NoOpen
.\start-local.ps1 -CheckEnv
.\stop-local.ps1

# 依 package.json 版本產生對外原始碼測試包
.\scripts\package-public.ps1

# 產生 Windows 單檔安裝程式與 SHA-256
.\scripts\build-windows-installer.ps1

# 後端測試
python -m unittest tests.test_collector

# 前端檢查
npm run lint
npx tsc --noEmit
node --test tests/rendered-html.test.mjs
npm run build

# package.json 定義的完整前端測試（build + rendered HTML）
npm test
```

## 不得違反的既有設計決策

- 維持本機優先與單機 SQLite；未經明確決策不得改為必須登入的雲端服務。
- 工作區頻道沿用監測首頁同一筆公開資料，不建立重複的公開資料副本。
- Studio 私人指標只與自己的頻道／團隊工作區綁定，不可混入公開排行或其他頻道比較。
- 公開版與私人版的資料保留政策必須分離。
- 趨勢與內容比較必須保留格式篩選，避免 Shorts、一般影片與直播被無意混算。
- 同接輪詢預設批次處理；手動新增頻道使用持久化佇列，50 個自動刷新並保留立即刷新入口。
- 活動／畢業自動判定只是待人工確認的規則結果；人工狀態不得被排程覆寫。
- 已核准每位使用者安裝、免管理員權限的 Windows EXE；它仍維持本機服務架構，不得藉此改成雲端多租戶服務。
