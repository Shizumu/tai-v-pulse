# 台V Pulse 0.11.0 Windows 對外測試版

本版提供單檔 Windows 安裝程式 `tai-v-pulse-0.11.0-setup.exe`。程式仍只在使用者自己的電腦執行；YouTube API Key、OAuth 設定／token、本機 SQLite 資料庫及私人 Analytics 不會傳給專案作者。

## 使用前準備

- Windows 10 22H2 或 Windows 11（含 .NET Framework 4.8 與 Windows PowerShell 5.1）
- Node.js 22.13.0 以上
- Python 3.11 以上
- 使用者自行申請的 YouTube Data API v3 Key
- 若要直接同步自己的私人 Analytics：自行建立的 Google「桌面應用程式」OAuth JSON，且同一 Google Cloud 專案已啟用 YouTube Analytics API
- 第一次安裝執行環境、Python 時區資料與 npm 套件時可連上網路

## 安裝與啟動

1. 核對安裝程式旁 `.sha256` 檔中的 SHA-256，再雙擊 `tai-v-pulse-0.11.0-setup.exe`。已安裝 0.10.0 以上的電腦也可由啟動器按「檢查更新」；0.9.0 與更早版本沒有內建更新器，必須手動完成一次覆蓋安裝。
2. 閱讀並勾選非商用授權及本機資料說明，按「安裝並啟動」。程式會安裝到目前使用者的 `%LOCALAPPDATA%\Programs\TaiVPulse`，不要求管理員權限。
3. 啟動器會檢查 Node.js、Python、Python 時區資料、npm 前端套件、資料服務與網頁服務。若缺少 Node.js 或 Python，會提供 WinGet 自動安裝、開啟官方下載頁或取消三種選項；若 Windows 缺少 IANA 時區資料，會把固定版本的 `tzdata` 安裝到台V Pulse 自己的 `work/python-packages`。
4. 第一次缺少 API Key 時會建立並開啟 `.env`；將自己的 Key 填入 `YOUTUBE_API_KEY=`，儲存後回到啟動器再按一次「啟動」。
5. 之後可從桌面或開始功能表的「台V Pulse」啟動、停止、開啟網頁、編輯 Key、檢查更新、查看「後台動態」、匯出診斷報告或解除安裝。服務就緒時會立即以 Windows 預設瀏覽器開啟本機網頁；不需要關閉重開啟動器，「停止」與執行狀態會直接恢復可用並持續更新。啟動期間仍可手動按「開啟網頁」或「編輯 Key」。
6. 若要直接同步自己的頻道，在「頻道工作區」匯入自備的 OAuth JSON，按「連結我的 YouTube 頻道」，再於 Google 官方頁面確認 YouTube 與 Analytics 兩項唯讀權限。Studio 檔案匯入仍保留為進階備援。

直接執行新版安裝程式即可覆蓋升級；安裝器會保留 `.env`、`work/`、SQLite 與本機分析資料，清除新版已淘汰的程式檔，並以安裝包內版本化的最新版圖示重建桌面與開始功能表捷徑，避免沿用舊圖示快取。解除安裝時可選擇把資料移至 `%LOCALAPPDATA%\TaiVPulse\PreservedData-時間`，或在第二次確認後永久刪除所有台V Pulse 本機資料。

從 0.10.0 起，啟動器每天至多自動向 `Shizumu/tai-v-pulse` 的正式 GitHub Releases 檢查一次，也可按「檢查更新」立即確認。檢查請求只會帶入台V Pulse 版本與一般 HTTP 資訊，不會上傳 API Key、資料庫、OAuth、Studio 或監測內容。發現新版時會顯示版本與更新內容，只有使用者確認才會下載；下載檔必須符合預期的 GitHub HTTPS 位置、Release 檔案大小及 SHA-256。若正在執行背景資料工作，啟動器會要求稍後再更新。驗證完成後由暫存的獨立更新程序等待舊啟動器關閉，再沿用上述覆蓋升級流程並重新啟動新版。

自動安裝只會要求 Windows Package Manager 安裝 `OpenJS.NodeJS.LTS` 與 `Python.Python.3.13`；如果 WinGet 不存在或安裝失敗，程式只會開啟 Node.js 與 Python 官方下載頁，不會從其他網站下載執行檔。

發佈包內的 PowerShell 腳本固定使用帶 BOM 的 UTF-8，確保 Windows PowerShell 5.1 在繁體中文系統上能正確解析中文訊息。

`.env` 允許 `YOUTUBE_API_KEY = 值`、引號及 `export` 寫法；若出現多個同名設定，會採用最後一行。需要只檢查設定是否可辨識時，可執行 `.\start-local.ps1 -CheckEnv`，檢查結果不會顯示金鑰內容。

EXE 啟動器只針對隨附且安裝在同一資料夾的本機腳本使用 `ExecutionPolicy Bypass`。原始碼測試包的 `.cmd` 入口仍可使用。若要自行從 PowerShell 執行，也可在目前視窗使用：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

這只影響目前 PowerShell 視窗。

## 0.11.0 重點

- 市場排行將「同接／訂閱」改為「直播持續動員」：只納入至少 20 個樣本且涵蓋 70% 直播時長的場次，以時間加權平均同接／訂閱計算，顯示每百位訂閱可持續留下幾位直播觀眾；峰值仍保留為輔助。
- 最近 X 天固定頻道趨勢新增公開黏著度與直播持續動員兩條歷史線，並移除只會累積的內容總數。公開黏著度以觀看倍數呈現，避免與直播指標使用相同百分比造成混淆。
- 同級高效率內容會獨立置頂顯示自己的最佳內容與同級名次，不會影響同級統計；直播列同時顯示平均、峰值與取樣覆蓋率。
- 監測首頁主卡改顯示最近更新狀態與台北時間；已收錄頻道表新增最近 30 日平均同接、完整取樣場次，以及每週直播、一般影片與 Shorts 頻率。
- 0.10.0 以上可透過內建更新器遠端覆蓋升級；更新仍會保留 `.env`、`work/`、SQLite、OAuth、Studio、個人設定與既有 `node_modules`。

## 0.10.1 重點

- 修正直播或預告轉為私人、遭刪除或無法由公開 YouTube API 取得後，仍永久留在「直播雷達」並持續高頻輪詢。新版會在成功回應中辨識缺少的影片 ID，移出雷達並停止高頻輪詢。
- 清除的只有「目前可公開存取」與舊的目前同接狀態；既有公開 metadata、觀看快照與歷史同接仍保留。若影片之後重新公開並再次被掃描到，會恢復正常狀態。
- 0.10.0 可透過內建更新器遠端覆蓋升級；更新仍會保留 `.env`、`work/`、SQLite、OAuth、Studio、個人設定與既有 `node_modules`。

## 0.10.0 重點

- Windows 啟動器新增非強制的更新檢查、一鍵下載、GitHub Release 來源限制、檔案大小與 SHA-256 驗證，以及避開背景資料工作的更新確認。
- 新增獨立 `TaiVPulseUpdater.exe`，從暫存位置等待舊啟動器關閉，再執行既有覆蓋升級並驗證安裝後版本，避免執行中的 EXE 自行覆寫。
- 正式 GitHub repository 可在推送 `vX.Y.Z` 標籤後，由 GitHub Actions 自動執行測試、公開白名單打包、Windows 安裝程式建置、校驗檔核對及 GitHub Release 發布。
- 修正 0.9.0 匯出的公開監測資料若含舊影片 `tags=null`，會被同版本匯入預覽錯誤拒絕；既有 ZIP 不需重做，更新目的端後即可直接匯入。

## 對外版限制

- 執行模式固定預設為 `public`。
- YouTube 公開 API 快照最多保存 30 天。
- Studio 匯入及手動補值只應用於使用者自己或獲授權管理的頻道。
- OAuth 直接連線只申請 `youtube.readonly` 與 `yt-analytics.readonly`；不能上傳、刪除或修改頻道內容，也不讀取收益。同步最多取最近 365 天，不包含曝光、曝光點閱率或回訪觀眾。
- 每位使用者必須使用自己的 Google OAuth 桌面應用程式。若 OAuth 同意畫面仍為測試狀態，Google 的 refresh token 通常會在 7 天後失效。
- 關機、睡眠、斷網或程式停止期間的即時同接無法回填。
- 分時加強掃描以台北時間固定執行；冷清時段仍可能遇到未預告且兩次掃描之間直接開播的頻道，無法保證零漏接。
- 安裝程式未經程式碼簽章，Windows SmartScreen 可能顯示未知發行者；發布時必須同時提供 SHA-256。
- 自動更新目前信任 GitHub HTTPS 與 Release API 提供的 SHA-256，尚未加入 Authenticode 或專案自己的離線簽章；因此仍會先顯示版本並要求使用者確認，不做強制靜默更新。
- 尚未在另一台乾淨電腦完成 Node.js／Python 自動安裝、長時間運作、升級及備份還原的整條實機驗收。

## 錯誤代碼與診斷報告

- `TVP-I…`：安裝或更新失敗。
- `TVP-E201`：Node.js 或 Python 尚未安裝完成。
- `TVP-E301`：YouTube API Key 尚未設定。
- `TVP-E401`／`TVP-E402`：npm 或前端套件安裝失敗。
- `TVP-E403`：Python 時區資料安裝失敗。
- `TVP-E501`／`TVP-E502`：本機資料服務或網頁服務啟動失敗。
- `TVP-E801`：無法取得或解析正式 GitHub Release 更新資訊。
- `TVP-E802`：更新下載、檔案大小、SHA-256 或獨立更新程序驗證失敗。
- `TVP-D001`：診斷報告建立失敗。

啟動器的「匯出診斷報告」會在桌面建立 `TaiVPulse-diagnostics-日期時間.zip`。內容只取最近的啟動、前端、後端、安裝與更新 LOG，並遮蔽 API Key、Windows 使用者名稱及電腦名稱；不會收錄 `.env`、OAuth JSON／token、SQLite、`work/` 內其他資料或 Studio 原始檔。回報問題時請同時提供畫面上的錯誤代碼與這個 ZIP。

OAuth 常見問題可直接在「頻道工作區 → 連線或同步遇到問題？」查看：403 測試使用者、Analytics API 未啟用、測試 token 七天後失效、scope 不完整、錯誤 OAuth JSON、Google 尚無資料、配額與網路問題均附繁中處理步驟。按「立即同步」後會維持「同步中…」並自動等待結果，不需要重複點擊；Google 英文原文只放在可展開的技術細節。

## 安全與授權

- 不要將 `.env`、API Key、OAuth JSON／token、`work/`、SQLite 資料庫或 Studio 原始匯出檔傳給他人。
- OAuth 設定與 token 使用目前 Windows 使用者的 DPAPI 加密後保存在 `work/oauth/`；中斷連線會嘗試向 Google 撤銷授權，再刪除本機 token 與直接同步資料。DPAPI 密文不可視為可攜式備份。
- 本機 API 只允許台V Pulse 自己的 `127.0.0.1:3000`／`localhost:3000` 網頁跨來源存取；請勿修改為對所有網站開放。
- 本套件依 PolyForm Noncommercial License 1.0.0 提供，只允許非商業用途。
- 分享或修改時必須一併保留 `LICENSE`、`NOTICE` 及必要署名。

完整功能、資料處理及授權說明請閱讀 `README.md`、`LICENSE` 與 `NOTICE`。
