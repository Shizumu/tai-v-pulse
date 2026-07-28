# 台V Pulse 目前狀態

更新日期：2026-07-28  
版本：`0.8.3`  
狀態：Windows 啟動器自動開頁、互動狀態與後台動態顯示已修正，0.8.3 安裝包已完成。

## 本次完成內容

- 啟動器在收到服務就緒訊息時立即以 Windows Shell 開啟 `http://127.0.0.1:3000`，不再等 PowerShell 完成最後收尾才嘗試開頁。
- 啟動期間不再把整個視窗切成等待游標；「開啟網頁」與「編輯 API Key」仍可操作，避免看起來像介面假死。
- 啟動器新增獨立「後台動態」，顯示探索頻道、直播同接更新、最新上傳掃描、頻道公開數據更新及手動頻道批次更新。
- `/api/summary` 保留目前工作開始時間，以及最近工作、完成時間與成功／失敗狀態，讓啟動器不會漏掉短時間完成的背景工作。
- 版本升為 `0.8.3`，沿用既有覆蓋升級、資料保留、解除安裝與安全白名單流程。

## 修改過的重要檔案

- `windows/TaiVPulseLauncher.cs`：可靠開頁、啟動期間互動與後台動態。
- `collector/server.py`、`app/dashboard.tsx`：背景工作狀態 payload 與 TypeScript type。
- `tests/test_collector.py`、`tests/windows-startup.test.ps1`：最近工作狀態及啟動器回歸測試。
- `package.json`、`package-lock.json`、`README.md`、`PUBLIC_RELEASE.md`、`docs/CURRENT_STATE.md`：0.8.3 版本、使用說明與本次收尾狀態。
- `outputs/tai-v-pulse-0.8.3-setup.exe`、`.sha256`：Windows 成品。

## 測試結果

- 後端 24 項單元測試通過。
- Windows 啟動、時區相依、Shell 開頁、服務就緒開頁、後台動態與非全窗等待游標回歸測試通過。
- TypeScript 型別檢查與 Windows 內建 .NET Framework C# 編譯器驗證通過。
- 安裝器建置驗證通過：安全白名單、靜默安裝、必要／禁止檔案、PowerShell parser 與版本檢查。
- 安裝程式 SHA-256：`6ca889f7258298971dcf578666317211794b5243f13a89f6998f1d5efb4f9f1c`。

## 尚未解決事項

- 尚待原本發生問題的電腦直接覆蓋安裝 0.8.3，驗收瀏覽器自動開啟、啟動器按鈕及後台動態。
- 首次補齊 Python 時區資料與 npm 套件仍需網路；安裝程式尚未使用 Authenticode 簽章。
- Repository 尚無 `HEAD` commit，所有檔案均為 untracked，Git 無法提供可靠的任務差異。

## 下一步建議

- 在原電腦直接執行 `outputs/tai-v-pulse-0.8.3-setup.exe`，不要先解除安裝；既有 `.env`、SQLite、`work/` 與 `node_modules` 會保留。若仍失敗，再匯出新的診斷 ZIP。
