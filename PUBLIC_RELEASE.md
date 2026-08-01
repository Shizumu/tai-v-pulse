# 台V Pulse 0.9.0 Windows 對外測試版

本版提供單檔 Windows 安裝程式 `tai-v-pulse-0.9.0-setup.exe`。程式仍只在使用者自己的電腦執行；YouTube API Key、OAuth 設定／token、本機 SQLite 資料庫及私人 Analytics 不會傳給專案作者。

## 使用前準備

- Windows 10 22H2 或 Windows 11（含 .NET Framework 4.8 與 Windows PowerShell 5.1）
- Node.js 22.13.0 以上
- Python 3.11 以上
- 使用者自行申請的 YouTube Data API v3 Key
- 若要直接同步自己的私人 Analytics：自行建立的 Google「桌面應用程式」OAuth JSON，且同一 Google Cloud 專案已啟用 YouTube Analytics API
- 第一次安裝執行環境、Python 時區資料與 npm 套件時可連上網路

## 安裝與啟動

1. 核對安裝程式旁 `.sha256` 檔中的 SHA-256，再雙擊 `tai-v-pulse-0.9.0-setup.exe`。
2. 閱讀並勾選非商用授權及本機資料說明，按「安裝並啟動」。程式會安裝到目前使用者的 `%LOCALAPPDATA%\Programs\TaiVPulse`，不要求管理員權限。
3. 啟動器會檢查 Node.js、Python、Python 時區資料、npm 前端套件、資料服務與網頁服務。若缺少 Node.js 或 Python，會提供 WinGet 自動安裝、開啟官方下載頁或取消三種選項；若 Windows 缺少 IANA 時區資料，會把固定版本的 `tzdata` 安裝到台V Pulse 自己的 `work/python-packages`。
4. 第一次缺少 API Key 時會建立並開啟 `.env`；將自己的 Key 填入 `YOUTUBE_API_KEY=`，儲存後回到啟動器再按一次「啟動」。
5. 之後可從桌面或開始功能表的「台V Pulse」啟動、停止、開啟網頁、編輯 Key、查看「後台動態」、匯出診斷報告或解除安裝。服務就緒時會立即以 Windows 預設瀏覽器開啟本機網頁；不需要關閉重開啟動器，「停止」與執行狀態會直接恢復可用並持續更新。啟動期間仍可手動按「開啟網頁」或「編輯 API Key」。
6. 若要直接同步自己的頻道，在「頻道工作區」匯入自備的 OAuth JSON，按「連結我的 YouTube 頻道」，再於 Google 官方頁面確認 YouTube 與 Analytics 兩項唯讀權限。Studio 檔案匯入仍保留為進階備援。

直接執行新版安裝程式即可覆蓋升級；安裝器會保留 `.env`、`work/`、SQLite 與本機分析資料，清除新版已淘汰的程式檔，並以安裝包內版本化的最新版圖示重建桌面與開始功能表捷徑，避免沿用舊圖示快取。解除安裝時可選擇把資料移至 `%LOCALAPPDATA%\TaiVPulse\PreservedData-時間`，或在第二次確認後永久刪除所有台V Pulse 本機資料。

自動安裝只會要求 Windows Package Manager 安裝 `OpenJS.NodeJS.LTS` 與 `Python.Python.3.13`；如果 WinGet 不存在或安裝失敗，程式只會開啟 Node.js 與 Python 官方下載頁，不會從其他網站下載執行檔。

發佈包內的 PowerShell 腳本固定使用帶 BOM 的 UTF-8，確保 Windows PowerShell 5.1 在繁體中文系統上能正確解析中文訊息。

`.env` 允許 `YOUTUBE_API_KEY = 值`、引號及 `export` 寫法；若出現多個同名設定，會採用最後一行。需要只檢查設定是否可辨識時，可執行 `.\start-local.ps1 -CheckEnv`，檢查結果不會顯示金鑰內容。

EXE 啟動器只針對隨附且安裝在同一資料夾的本機腳本使用 `ExecutionPolicy Bypass`。原始碼測試包的 `.cmd` 入口仍可使用。若要自行從 PowerShell 執行，也可在目前視窗使用：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

這只影響目前 PowerShell 視窗。

## 0.9.0 重點

- 監測首頁可匯出／匯入版本化的公開監測資料包；匯入前會驗證 manifest、SHA-256、筆數與資料關聯，再由使用者選擇合併或取代公開資料。資料包不包含 API Key、OAuth、Studio、手動補值、工作區設定或候選紀錄，也不應以整個 `work/` 或 SQLite 代替。
- 內容環境新增可點擊的直播時段熱圖、統計範圍說明、混合主題與人工分類／遊戲名稱修正；這些分類屬於台V Pulse 的可檢查規則，不是 YouTube Analytics 官方分類。
- 趨勢圖表新增 7～365 天與自訂期間、比較群組／組織範圍、四項市場排行、熱門內容主題及固定頻道比較；頻道工作區改為清楚區分個人、團隊、公開監測與私人 OAuth／Studio 資料。
- 全頻道開台加強掃描改為台北時間 `00:05`、`01:05`、`08:05`、`12:05`、`15:05`、`18:05`～`23:05`，降低冷清時段的 API 消耗；已知預告／直播仍依設定輪詢同接，完整上傳掃描仍每 4 小時執行。
- 上傳播放清單失效時會略過該頻道並保留既有資料，不再讓單一 404 中止整批更新；監測首頁會顯示可理解的警示，技術識別資訊只留在本機診斷紀錄。

## 對外版限制

- 執行模式固定預設為 `public`。
- YouTube 公開 API 快照最多保存 30 天。
- Studio 匯入及手動補值只應用於使用者自己或獲授權管理的頻道。
- OAuth 直接連線只申請 `youtube.readonly` 與 `yt-analytics.readonly`；不能上傳、刪除或修改頻道內容，也不讀取收益。同步最多取最近 365 天，不包含曝光、曝光點閱率或回訪觀眾。
- 每位使用者必須使用自己的 Google OAuth 桌面應用程式。若 OAuth 同意畫面仍為測試狀態，Google 的 refresh token 通常會在 7 天後失效。
- 關機、睡眠、斷網或程式停止期間的即時同接無法回填。
- 分時加強掃描以台北時間固定執行；冷清時段仍可能遇到未預告且兩次掃描之間直接開播的頻道，無法保證零漏接。
- 安裝程式未經程式碼簽章，Windows SmartScreen 可能顯示未知發行者；發布時必須同時提供 SHA-256。
- 尚未在另一台乾淨電腦完成 Node.js／Python 自動安裝、長時間運作、升級及備份還原的整條實機驗收。

## 錯誤代碼與診斷報告

- `TVP-I…`：安裝或更新失敗。
- `TVP-E201`：Node.js 或 Python 尚未安裝完成。
- `TVP-E301`：YouTube API Key 尚未設定。
- `TVP-E401`／`TVP-E402`：npm 或前端套件安裝失敗。
- `TVP-E403`：Python 時區資料安裝失敗。
- `TVP-E501`／`TVP-E502`：本機資料服務或網頁服務啟動失敗。
- `TVP-D001`：診斷報告建立失敗。

啟動器的「匯出診斷報告」會在桌面建立 `TaiVPulse-diagnostics-日期時間.zip`。內容只取最近的啟動、前端、後端與安裝 LOG，並遮蔽 API Key、Windows 使用者名稱及電腦名稱；不會收錄 `.env`、OAuth JSON／token、SQLite、`work/` 內其他資料或 Studio 原始檔。回報問題時請同時提供畫面上的錯誤代碼與這個 ZIP。

OAuth 常見問題可直接在「頻道工作區 → 連線或同步遇到問題？」查看：403 測試使用者、Analytics API 未啟用、測試 token 七天後失效、scope 不完整、錯誤 OAuth JSON、Google 尚無資料、配額與網路問題均附繁中處理步驟。按「立即同步」後會維持「同步中…」並自動等待結果，不需要重複點擊；Google 英文原文只放在可展開的技術細節。

## 安全與授權

- 不要將 `.env`、API Key、OAuth JSON／token、`work/`、SQLite 資料庫或 Studio 原始匯出檔傳給他人。
- OAuth 設定與 token 使用目前 Windows 使用者的 DPAPI 加密後保存在 `work/oauth/`；中斷連線會嘗試向 Google 撤銷授權，再刪除本機 token 與直接同步資料。DPAPI 密文不可視為可攜式備份。
- 本機 API 只允許台V Pulse 自己的 `127.0.0.1:3000`／`localhost:3000` 網頁跨來源存取；請勿修改為對所有網站開放。
- 本套件依 PolyForm Noncommercial License 1.0.0 提供，只允許非商業用途。
- 分享或修改時必須一併保留 `LICENSE`、`NOTICE` 及必要署名。

完整功能、資料處理及授權說明請閱讀 `README.md`、`LICENSE` 與 `NOTICE`。
