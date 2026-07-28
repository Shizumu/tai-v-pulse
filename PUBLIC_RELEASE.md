# 台V Pulse 0.8.3 Windows 對外測試版

本版提供單檔 Windows 安裝程式 `tai-v-pulse-0.8.3-setup.exe`。程式仍只在使用者自己的電腦執行；YouTube API Key、本機 SQLite 資料庫及自行匯入的 Studio 資料不會傳給專案作者。

## 使用前準備

- Windows 10 22H2 或 Windows 11（含 .NET Framework 4.8 與 Windows PowerShell 5.1）
- Node.js 22.13.0 以上
- Python 3.11 以上
- 使用者自行申請的 YouTube Data API v3 Key
- 第一次安裝執行環境、Python 時區資料與 npm 套件時可連上網路

## 安裝與啟動

1. 核對安裝程式旁 `.sha256` 檔中的 SHA-256，再雙擊 `tai-v-pulse-0.8.3-setup.exe`。
2. 閱讀並勾選非商用授權及本機資料說明，按「安裝並啟動」。程式會安裝到目前使用者的 `%LOCALAPPDATA%\Programs\TaiVPulse`，不要求管理員權限。
3. 啟動器會檢查 Node.js、Python、Python 時區資料、npm 前端套件、資料服務與網頁服務。若缺少 Node.js 或 Python，會提供 WinGet 自動安裝、開啟官方下載頁或取消三種選項；若 Windows 缺少 IANA 時區資料，會把固定版本的 `tzdata` 安裝到台V Pulse 自己的 `work/python-packages`。
4. 第一次缺少 API Key 時會建立並開啟 `.env`；將自己的 Key 填入 `YOUTUBE_API_KEY=`，儲存後回到啟動器再按一次「啟動」。
5. 之後可從桌面或開始功能表的「台V Pulse」啟動、停止、開啟網頁、編輯 Key、查看「後台動態」、匯出診斷報告或解除安裝。服務就緒時會立即以 Windows 預設瀏覽器開啟本機網頁，啟動期間仍可手動按「開啟網頁」或「編輯 API Key」。

直接執行新版安裝程式即可覆蓋升級；安裝器會保留 `.env`、`work/`、SQLite 與本機分析資料，並清除新版已淘汰的程式檔。解除安裝時可選擇把資料移至 `%LOCALAPPDATA%\TaiVPulse\PreservedData-時間`，或在第二次確認後永久刪除所有台V Pulse 本機資料。

自動安裝只會要求 Windows Package Manager 安裝 `OpenJS.NodeJS.LTS` 與 `Python.Python.3.13`；如果 WinGet 不存在或安裝失敗，程式只會開啟 Node.js 與 Python 官方下載頁，不會從其他網站下載執行檔。

發佈包內的 PowerShell 腳本固定使用帶 BOM 的 UTF-8，確保 Windows PowerShell 5.1 在繁體中文系統上能正確解析中文訊息。

`.env` 允許 `YOUTUBE_API_KEY = 值`、引號及 `export` 寫法；若出現多個同名設定，會採用最後一行。需要只檢查設定是否可辨識時，可執行 `.\start-local.ps1 -CheckEnv`，檢查結果不會顯示金鑰內容。

EXE 啟動器只針對隨附且安裝在同一資料夾的本機腳本使用 `ExecutionPolicy Bypass`。原始碼測試包的 `.cmd` 入口仍可使用。若要自行從 PowerShell 執行，也可在目前視窗使用：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

這只影響目前 PowerShell 視窗。

## 對外版限制

- 執行模式固定預設為 `public`。
- YouTube 公開 API 快照最多保存 30 天。
- Studio 匯入及手動補值只應用於使用者自己或獲授權管理的頻道。
- 關機、睡眠、斷網或程式停止期間的即時同接無法回填。
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

啟動器的「匯出診斷報告」會在桌面建立 `TaiVPulse-diagnostics-日期時間.zip`。內容只取最近的啟動、前端、後端與安裝 LOG，並遮蔽 API Key、Windows 使用者名稱及電腦名稱；不會收錄 `.env`、SQLite、`work/` 內其他資料或 Studio 原始檔。回報問題時請同時提供畫面上的錯誤代碼與這個 ZIP。

## 安全與授權

- 不要將 `.env`、API Key、`work/`、SQLite 資料庫或 Studio 原始匯出檔傳給他人。
- 本套件依 PolyForm Noncommercial License 1.0.0 提供，只允許非商業用途。
- 分享或修改時必須一併保留 `LICENSE`、`NOTICE` 及必要署名。

完整功能、資料處理及授權說明請閱讀 `README.md`、`LICENSE` 與 `NOTICE`。
