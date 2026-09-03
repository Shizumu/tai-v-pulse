# 台V Pulse 目前狀態

## `0.12.0` 新手導覽與自動參考頻道組

- 首次同意使用聲明後會開啟六段新手導覽，依序說明頻道工作區、監測首頁、內容環境、趨勢圖表與四個常用指標。導覽可略過，頁首「使用教學」可隨時重新開啟；前往實際頁面後，閱讀位置與操作狀態不會被導覽強制重設。
- 頻道工作區選定自己的頻道後，新增「為你挑選的參考頻道」。系統以最近 90 日已收錄的公開影片，先比對直播／一般影片／Shorts 形式與既有規則主題，再按目前公開訂閱分成低於 0.5 倍、0.5～2 倍同級及高於 2 倍三組；每組預選最多 2 個，資料足夠時合計 3～6 個，缺少可用頻道的組別會保留並說明。
- 每個候選顯示訂閱比例、近期內容量、共同主要形式／主題與資料不足提示；雙方各至少需要 3 項近期公開內容才會宣稱內容定位相近。休止中、疑似已畢業與已確認畢業頻道不列入推薦。
- 推薦名單可逐一替換，並可直接帶入內容環境或趨勢圖表。兩頁的指定頻道上限同步調整為 6 個；趨勢頁會同時帶入指定群組與固定比較線，推薦組也會保存成本機比較組合。
- 這項推薦只代表公開內容定位與訂閱規模。YouTube Data API 不提供跨頻道觀眾重疊，因此介面不會把它描述成相同客群；也不會從單次流量變化推論因果。
- 驗證完成：collector 47 項、rendered HTML 7 項、TypeScript、lint 與 vinext production build 均通過；使用現有本機公開資料讀回三組共 6 個推薦候選，工作區頁面及推薦 API 均回傳 200。

## `0.12.0` Taiwan VTuber Data 外部名錄同步

- 監測首頁新增 Taiwan VTuber Data 名錄同步。系統只解析 `TaiwanVtuberTrackingData` repository 的 `TW_VTUBER_TRACK_LIST.csv`，限定 `Taiwanese VTubers` 區段、`Nationality=TW`、`Activity=Active`、無畢業日期且具有有效 YouTube Channel ID 的個人頻道；團體官方、非台灣、已停止活動、無 YouTube 或無效 ID 會先略過。
- 外部名錄只提供頻道身分與 Channel ID。名稱、訂閱、觀看、影片及直播統計均以使用者自己的 YouTube Data API Key 透過 `channels.list` 每 50 個批次重新核對，不匯入外站統計。
- 公開訂閱達現行門檻者直接收錄；未公開、未達門檻、本機黑名單及 YouTube 未回傳分別保存結果。已收錄頻道若後來低於門檻或隱藏訂閱數會保留既有狀態，外部名錄移除項目也不會刪除本機歷史。
- 新增 `external_directory_imports` 與 `external_directory_entries`，保存來源網址、可取得的 Git commit、CSV SHA-256、Unlicense、同步時間、逐頻道結果與統計；名錄的團體或活動欄位只作匯入稽核，不覆蓋本機人工組織、標籤或活動狀態。
- 新增背景 API `/api/external-directory/import`、進度／結果摘要、桌面與行動版介面，以及 CSV 邊界和直接收錄／門檻／黑名單／來源證據測試。
- 2026-09-03 已在實際本機資料執行首次同步：來源 CSV 4,594 列中辨識 2,594 個台灣個人頻道，篩出 2,127 個活動中且有有效 YouTube Channel ID 的項目；YouTube 回傳 2,113 個，其中 1,308 個達門檻（新增 1,188、更新既有 120）、805 個未達門檻、14 個未回傳。同步後本機共有 1,340 個符合門檻的頻道。
- 監測首頁 API 改為回傳全部符合門檻的頻道；介面搜尋與篩選套用完整資料集，表格先渲染 200 個並可逐批再顯示 200 個，避免大量名單只停在舊的 500 筆上限或一次建立過大的畫面。
- `npm run dev`／`build`／`start` 改由跨平台 Node 啟動器設定 Wrangler LOG 路徑，Windows PowerShell 可直接執行；公開白名單同步包含該啟動器。
- 驗證完成：collector 46 項、rendered HTML 7 項、TypeScript、vinext production build、SQLite `integrity_check` 與外鍵檢查均通過；lint 為 0 errors，變更頁面只有 4 個既有 `<img>` 警告。已以實際 API 讀回 1,340／1,340 個頻道，並完成桌面與 390 × 844 行動版視覺檢查。
- PR [#10](https://github.com/Shizumu/tai-v-pulse/pull/10) 已 squash merge 至 `main` commit `598beefa366093925c6a5865b2b989eca91e4639`。標籤 `v0.12.0` 的 [GitHub Actions run 33784175100](https://github.com/Shizumu/tai-v-pulse/actions/runs/33784175100) 全部通過，並建立非草稿、非預發佈的正式 [GitHub Release](https://github.com/Shizumu/tai-v-pulse/releases/tag/v0.12.0)。
- 已重新下載 GitHub Release 資產驗證：公開 ZIP 332,919 bytes、SHA-256 `265e93ef858c22d1c4ae9a5e3eb32c9598d4c05b88cf91ed0382cae2fe5e0bb6`；Windows 安裝程式 429,056 bytes、SHA-256 `2a6630d1764a21586e23e63a542b7b58b6aed1dc4248e0e1953957e5af8f9a94`，檔案版本 `0.12.0.0`／產品版本 `0.12.0`。兩者均與 sidecar 相符；ZIP 共 57 個檔案，manifest 的 56 條項目與其餘 payload 完全吻合，必要的新功能檔案均存在，`.env.example` 的 API Key 為空白，未發現 `.env`、`work/`、SQLite／DB、Studio CSV／TSV、相依套件、LOG、outputs、Git metadata 或 API Key 格式內容。GitHub `releases/latest` 已回傳 `v0.12.0` 與同一組資產資訊，因此 0.10.0 以上啟動器可偵測此版。

更新日期：2026-09-04
版本：`0.12.0`
狀態：`0.12.0` 收錄 Taiwan VTuber Data 名錄同步、新手導覽與自動參考頻道組；功能、實際本機資料、白名單套件與 Windows 安裝程式依本文件列出的範圍驗證。0.10.0 以上可由內建更新器遠端覆蓋升級；0.9.0 與更早版本仍須先手動安裝最新版一次。

## `0.11.0` 公開指標與決策介面調整

- 新增共用同接品質計算：依實際樣本間隔做時間加權平均，單段最多只承認兩個輪詢週期，避免把停機或延遲空白誤算成持續觀眾；至少 20 個樣本且涵蓋 70% 直播時長才標記為完整取樣。
- 市場排行把「同接／訂閱」改為「直播持續動員」，公式為最近 30 日完整取樣直播的平均同接中位數／目前訂閱數，介面顯示「每百位訂閱幾人」；公開觀看黏著度改顯示觀看倍數，兩者不再使用相同百分比外觀。
- 固定頻道歷史加入各日往前 30 天的公開黏著度及直播持續動員；累積內容總數折線已移除。固定比較表同時顯示平均同接、峰值與完整取樣場次。
- 同級高效率內容將基準頻道自己的最佳內容另外置頂並顯示同級名次，不納入同級中位數或占用同級榜單；直播排行使用平均同接／訂閱，仍保留峰值與覆蓋率。實際本機資料已確認基準頻道 Shorts 出現於此區。
- 監測首頁主卡改顯示最近更新狀態與台北時間；同接資料點保留在唯讀系統資訊。已收錄頻道表新增最近 30 日平均同接、完整取樣場次、每週直播、一般影片及 Shorts 頻率與排序。
- 驗證完成：collector 44 項、rendered HTML 7 項、TypeScript、production build、Windows 啟動與 UTF-8 編碼均通過；lint 為 0 errors，僅保留 205 個既有 `<img>` 警告。使用 152 個本機公開頻道驗證 API payload 與三個頁面，並完成桌面及 390 × 844 行動版互動檢查。
- PR [#8](https://github.com/Shizumu/tai-v-pulse/pull/8) 已 squash merge 至 `main` commit `858a58137850cad87d0636c23570b31a671cefd6`。標籤 `v0.11.0` 的 [GitHub Actions run 30846570600](https://github.com/Shizumu/tai-v-pulse/actions/runs/30846570600) 全部通過，並建立非草稿、非預發佈的正式 [GitHub Release](https://github.com/Shizumu/tai-v-pulse/releases/tag/v0.11.0)。
- 已重新下載 GitHub Release 資產驗證：公開 ZIP 313,848 bytes、SHA-256 `6f1dd3d1553352a52fd029dab83a1c033c9ef9a21d38f58798d0e10a9b48e24b`；Windows 安裝程式 410,112 bytes、SHA-256 `d3f94152be9f76d455233b4810e2a884d0e4457654491ac2b9744e44a3fd88fc`，檔案版本 `0.11.0.0`／產品版本 `0.11.0`。兩者均與 sidecar 相符；ZIP 的 53 條 manifest 與 53 個受管理檔案完全吻合，`.env.example` 的 API Key 為空白，未發現 `.env`、`work/`、SQLite、相依套件、outputs 或 Git metadata。GitHub `releases/latest` 已回傳 `v0.11.0` 與同一組安裝程式大小及 SHA-256，因此 0.10.0 以上啟動器可偵測此版。

## `0.10.1` 直播雷達不可公開影片修正

- `refresh_videos()` 現在會在 YouTube `videos.list` 整批成功後，比對要求與實際回傳的影片 ID。缺少且資料庫原本為 `live`／`upcoming` 的影片會改為 `unavailable`，清空 `current_concurrent`，因此立刻退出直播雷達與每分鐘輪詢。
- 不會刪除影片列、觀看快照或 `concurrency_samples`；頻道詳細資料明確顯示「目前無法公開存取」，內容格式仍保留為直播。若影片重新公開並於加強掃描或上傳掃描再次回傳，`upsert_video()` 會恢復實際狀態。
- 新增回歸測試覆蓋私人影片從雷達移除、舊同接清空、歷史樣本保留及重新公開恢復。`python -m unittest tests.test_collector` 43 項、TypeScript、rendered HTML 7 項、production build、Windows 啟動與 UTF-8 檢查均通過；lint 為 0 errors、204 個既有 `<img>` 警告，多數來自 `work/` 歷史驗證副本且不進公開包。
- 版本升為 `0.10.1`。0.10.0 啟動器可由正式 GitHub Release 偵測並下載 `tai-v-pulse-0.10.1-setup.exe`；更新保留 `.env`、`work/`、SQLite、OAuth、Studio、個人設定與既有 `node_modules`。
- PR [#6](https://github.com/Shizumu/tai-v-pulse/pull/6) 已 squash merge 至 `main` commit `fd2a222c865b6a0f888cbfadd8989be08ae8a123`。標籤 `v0.10.1` 的 [GitHub Actions run 30800886589](https://github.com/Shizumu/tai-v-pulse/actions/runs/30800886589) 全部通過，並建立正式 [GitHub Release](https://github.com/Shizumu/tai-v-pulse/releases/tag/v0.10.1)。
- 已重新下載 GitHub Release 資產驗證：公開 ZIP 308,864 bytes、SHA-256 `67c615b6af312434c405801b3c77c3cba1e2687287a90d0bd4d88d7e350b386e`；Windows 安裝程式 404,992 bytes、SHA-256 `3776e4578b3e740ab9dcb8c8ba551e9e211e3b7df8611be9c27375d0d76e5349`，檔案版本 `0.10.1.0`／產品版本 `0.10.1`。兩者均與 sidecar 相符；ZIP 的 53 條 manifest 與 53 個受管理檔案完全吻合，`.env.example` 的 API Key 為空白，未發現 `.env`、`work/`、SQLite、相依套件、輸出或 Git metadata，且包內含本次後端與 UI 修正。安裝程式尚未 Authenticode 簽章；另一台實體 Windows 電腦的 0.10.0 → 0.10.1 更新與資料保留仍待實機驗收。

## `0.10.0` 更新與資料搬遷修正

- `collector/public_transfer.py` 現在接受 SQLite schema 原本就允許的舊影片 `tags=null`。使用 2026-08-01 實際由 0.9.0 匯出的 2.28 MB 公開監測 ZIP 重跑，預覽已通過，辨識 152 個頻道與 1,756 支影片；原始 ZIP 未修改，目的端更新後可直接匯入。
- Windows 啟動器新增「檢查更新」。每天最多自動查詢一次 `Shizumu/tai-v-pulse` 正式 GitHub Release，也可手動檢查；只接受非草稿、非預發佈的語意版本，以及符合預期檔名與 GitHub HTTPS Release 路徑的安裝程式。
- 下載前由使用者確認版本與 Release 說明；下載後核對 GitHub Release API 的資產大小及 SHA-256，失敗不執行。背景資料工作仍在執行時會要求稍後更新，不中斷正在進行的匯入或同步。
- 新增 `TaiVPulseUpdater.exe`。啟動器把它複製到獨立暫存目錄後關閉；更新器等待舊程序結束，再以 `--silent --no-launch` 執行既有安裝器、驗證安裝後版本並重新啟動。`.env`、`work/`、SQLite、OAuth、Studio、個人設定與 `node_modules` 沿用既有覆蓋升級保留規則。
- `.github/workflows/release.yml` 只在 `vX.Y.Z` 標籤推送時執行，先確認標籤與 `package.json` 相符，再跑 collector、TypeScript、lint、production build、rendered HTML 與 Windows 測試；通過後呼叫正式白名單 ZIP／EXE 腳本、核對 sidecar 並建立 GitHub Release。
- `scripts/package-public.ps1` 白名單新增 `.github` workflow 與 YAML 絕對路徑檢查；`scripts/build-windows-installer.ps1` 會編譯、封裝並驗證更新輔助程式。`export-diagnostics.ps1` 僅增加最近 updater LOG，仍套用遮蔽且不收錄私人資料。
- 自動更新目前沒有 Authenticode 或專案自己的離線簽章；SHA-256 與 HTTPS 可驗證本次下載和 GitHub Release 一致，但不能取代發行者簽章。因此本版不強制靜默更新，仍由使用者確認。
- 0.9.0 及更早版本沒有更新檢查程式碼，無法被遠端補上；首次必須手動覆蓋安裝 0.10.0 或更新版本。0.10.0 與 0.10.1 的真實標籤工作流程及正式 Release 已驗證；跨版本下載／程序交接與另一台 Windows 實機更新仍需使用者端驗收。
- 正式公開 ZIP：`outputs/tai-v-pulse-0.10.0-public.zip`；307,430 bytes；SHA-256 `f580d473154e375f974bf975e4de59b4713535f1a93c2aa34efb2035211d3cbf`。ZIP 共 54 個檔案，包含 release workflow、更新器原始碼與搬遷修正；未發現 `.env`、`work/`、SQLite／DB、OAuth、Studio 檔、依賴、LOG、EXE 或 Git metadata，`.env.example` 的 API Key 為空白，所有 PowerShell 腳本含 UTF-8 BOM。
- 正式 Windows 安裝程式：`outputs/tai-v-pulse-0.10.0-setup.exe`；403,968 bytes；SHA-256 `b3fb451b05d2974188f134f006155dd2646f903aa730780f492b510124a7d2a1`；檔案版本 `0.10.0.0`、產品版本 `0.10.0`。隔離安裝後 `TaiVPulse.exe` 與 `TaiVPulseUpdater.exe` 均為 `0.10.0`，沒有產生 `.env` 或 SQLite；建置流程另已驗證覆蓋升級保留 `.env`、SQLite、`node_modules` 並清除淘汰程式檔。
- 測試：`python -m unittest tests.test_collector` 42 項通過；實際 0.9.0 公開 ZIP 的預覽、合併、取代均成功並保留 7 筆 `tags=null`；TypeScript 通過；rendered HTML 7 項通過；Windows 啟動／更新結構及 UTF-8 測試通過；production build 通過六個 route；lint 0 errors、64 個既有 `<img>` 警告。該輪初次記錄時只解析 workflow YAML；後續 0.10.0 與 0.10.1 的 GitHub 標籤工作流程均已成功執行。

## `0.9.0` 正式發佈結果

- 全頻道加強掃描改為台北時間 `00:05`、`01:05`、`08:05`、`12:05`、`15:05`、`18:05`～`23:05`，每日由 72 次降為 11 次。每 4 小時完整上傳掃描、已知預告／直播的同接輪詢、一般配額安全線及「我的頻道」優先均維持不變。
- 正式公開 ZIP：`outputs/tai-v-pulse-0.9.0-public.zip`；SHA-256 `73431e447d8eed98c778b660a2f0861b0e9a4acd3c086ff7389092016d8439b8`。
- 正式 Windows 安裝程式：`outputs/tai-v-pulse-0.9.0-setup.exe`；SHA-256 `43e415bb7c66bf9c68e8d4d615501687b7d4046aab03b4f5116375e169b8b2d0`；檔案版本 `0.9.0.0`、產品版本 `0.9.0`。
- 公開原始碼 commit `630a56c` 已推送至 `codex/tai-v-pulse-0.9.0`，GitHub 草稿 PR #3 已建立；`main` 尚未合併或改寫。
- ZIP 有 52 個 entry（51 個 manifest 管理項目加 manifest 本身），版本為 `0.9.0`、`TAI_V_PULSE_EDITION=public`、`YOUTUBE_API_KEY=` 空白；獨立檢查未發現 `.env`、`work/`、SQLite／DB、OAuth JSON／token、Studio CSV／TSV、相依套件、LOG、建置快取或 Git metadata。
- 安裝程式建置流程已驗證解壓內容、覆蓋升級保留 `.env`／SQLite／`node_modules`、清除淘汰程式檔、捷徑圖示、PowerShell 5.1 parser 與啟動器版本資訊；此隔離安裝驗證不等於另一台乾淨電腦的實機驗收。

## `0.9.0` 累積完成內容

- 頻道工作區最上層新增「個人／團隊」分段。個人模式先顯示目前選取頻道的身分、YouTube 入口、公開訂閱、總觀看、影片數與歷史最高同接；團隊模式顯示公開合計、成員卡與比較表，即使尚無或僅有一個頻道也保留加入入口。
- 個人／團隊切換只控制上方摘要，不會改寫目前選取頻道、搜尋、Analytics 篩選或下方進階區的 React 狀態；新增／搜尋管理頻道與 OAuth 連線都移到摘要之後。團隊合計只取現有公開欄位，任何成員缺少該公開值時總計顯示 `—`，不以 `0` 補齊。
- OAuth 未連線時預設只顯示私人分析精簡入口；已連線時顯示連線頻道、資料日期、最近同步、每日自動同步、立即同步與管理控制。OAuth JSON、權限與本機加密說明、疑難排解、中斷連線及刪除設定只在管理區顯示；同步或授權錯誤會顯示「需要處理」並自動展開管理區。
- 團隊摘要明示只使用公開監測統計，OAuth、Studio 與手動補值仍依目前選取頻道分開呈現，不會加入團隊公開合計或其他頻道比較。本組只重排既有前端狀態與資料呈現，沒有新增後端欄位、資料表或第二套狀態來源。
- 內容環境頁尾改為「看懂內容環境」，以使用者任務說明先選基準頻道，再從內容主題、觀看／訂閱比與直播時段理解自己的位置；移除頁尾「至少 30 天／兩筆快照」等統一技術門檻。
- 訂閱與觀看成長在沒有期間起點資料時顯示 `—` 與「累積後顯示」，有部分頻道可比較時明列實際涵蓋頻道數；同級基準表把相同提示放在成長指標列。內容主題卡的觀看、觀看／訂閱與直播同接缺值也各自說明缺少的資料來源，不以 `0` 代替。
- 趨勢圖表觀察期間預設最近 7 天，提供 7、14、30、90、365 天快速選項與 7～365 天自訂輸入；介面明確區分此期間只控制歷史折線，熱門內容、觀看中位數、公開黏著度與比較摘要固定使用最近 30 天。
- 市場排行移除 7／30 日成長競逐，只保留訂閱數、觀看中位數、公開黏著度及同接／訂閱；每個頁籤改顯示各自的資料範圍、公式、缺值條件及內容形式篩選影響。指定頻道的折線與 30 日變化比較仍保留，後端歷史快照與成長 payload 也未停止蒐集或刪除。
- 「近 30 天熱門內容」新增獨立主題篩選，支援全部、遊戲、雜談、歌回、ASMR、音樂作品、紀念／重大活動與其他，亦能命中「歌回 + 雜談」等混合主題；該參數只篩熱門內容，不改動頻道趨勢、排行或摘要。
- 組織／團體表現新增「同級範圍／全部已收錄」切換；全部模式忽略訂閱級距，但仍服從頻道分類與已確認畢業篩選。介面顯示目前範圍、已填／範圍頻道數及未填組織名稱不納入統計的提示；組織用的擴大資料集不會混入同級排行、熱門內容或快照成熟度。
- YouTube 回覆 `playlistNotFound` 404 時，排程只略過該頻道並繼續處理同批其他頻道；既有影片與歷史資料不會被刪除，失效頻道會記錄本次掃描時間以免持續壟斷待處理佇列。
- 監測首頁改顯示不含原始 JSON 的繁中警示，說明已略過的頻道、既有資料仍保留及後續重試方式；本機 `api-error.log` 路徑則保留 Channel ID、播放清單 ID 與完整技術錯誤供診斷。
- 監測首頁新增首次使用引導，先說明工具用來理解自己的位置、同級內容與直播環境，並以「指定收錄頻道」作為主要下一步；尚未收錄頻道時才顯示，不移除進階功能。
- 首頁總覽卡補上各數字回答的問題；同接樣本尚未累積時顯示「資料累積中」，不以 `0` 偽裝。現正直播卡右上固定顯示台北時間 `HH:mm`，每分鐘局部更新，不觸發整頁或資料重新整理。
- 高頻的「指定 VTuber 搜尋」已移到總覽卡後、直播雷達與收錄規則前；「公開監測資料搬遷」移到已確認頻道列表後的頁尾進階區，預設收合。
- 公開資料搬遷在已選擇匯入檔、正在預覽／匯入或發生錯誤時會保持展開；首頁背景摘要更新不會重設搜尋、搬遷展開狀態或使用者正在處理的檔案。
- 趨勢圖表比較建立器已重排：桌面第一列等寬顯示期間、比較群組、頻道分類、內容形式，畢業頻道核取條件緊貼比較群組；第二列由基準頻道與「加入固定比較頻道（最多 6 個）」兩個各跨兩欄的長選單組成。
- 固定比較線的已選頻道另列在建立器下方，儲存／載入固定比較組合獨立為精簡工具列；自訂級距、固定量級及指定頻道群組仍收在比較群組欄內，沒有改動既有 API 參數、資料範圍或排行邏輯。
- 監測首頁新增「公開監測資料搬遷」：來源端可匯出已收錄頻道、公開頻道快照、影片、影片快照、直播同接樣本與分類／遊戲名稱人工修正，並分別選擇是否包含黑名單及收錄來源證據。
- 搬遷資料包採固定 `format_version=1` 的 ZIP；`manifest.json` 記錄建立時間、來源版本、匯出選項、每個 JSONL 檔的欄位白名單、筆數、位元組數及 SHA-256。匯入拒絕額外檔案／欄位、不安全路徑、格式版本不相容、雜湊或筆數不符，以及找不到父頻道／影片的孤立資料。
- 匯入前會預覽版本、建立時間、來源程式版本、頻道／影片／頻道快照／影片快照／同接／人工修正／黑名單筆數，以及五項完整性檢查；未通過不會寫入 SQLite。
- 「合併」以頻道／影片 ID 與更新時間保留較新記錄，歷史資料依父項目 ID 加擷取時間去重；「取代本機公開監測資料」會清除公開歷史與已收錄公開頻道後寫入資料包，並要求額外勾選確認。
- 取代流程會先找出工作區、OAuth、Studio、手動補值等私人表仍引用的頻道；這些頻道只轉為 `owned` 基礎列，不刪除私人表、工作區選擇或個人設定。資料包未包含黑名單時保留目的端黑名單；匯入的已收錄 Channel ID 會解除目的端同 ID 的舊黑名單狀態。
- 公開搬遷白名單不包含 `.env`、API Key、OAuth JSON／token、`work/oauth`、私人 OAuth Analytics、Studio 匯入、手動補值、工作區選擇、個人設定或候選審核紀錄；匯入後仍須使用目的電腦自己的 API Key 繼續更新。
- 監測首頁的「收錄規則」改為在原卡片內直接展開表單；儲存後摘要立即更新，取消會還原草稿，並以「可修改設定」與「系統資訊（唯讀）」分區呈現。
- 頻道工作區的「期間觀看最高內容」改顯示縮圖、影片標題、頻道名稱、發布／直播日期、觀看、觀看時數與訂閱淨變化；標題或整列可在新分頁開啟 YouTube，video ID 僅作資料識別。OAuth 同步會以使用者自己的唯讀授權批次取得影片 metadata，並保存在私人 OAuth 資料表，不混入公開影片資料。
- 趨勢圖表的市場排行比較跳卡改用頁面最上層 portal 與固定定位，依視窗可用空間自動向上或向下展開；支援滑鼠、焦點、Enter／空白鍵、行動版點擊、Esc、外部點擊及明確關閉按鈕，背景資料更新不會主動重設已開啟的列。
- 「包含已確認畢業頻道」的方框與文字已合併為同一個可點擊 label，並放在「比較群組」欄位正下方；桌面與 390 px 行動版均維持緊湊對齊。
- 頻道工作區新增自備 Google「桌面應用程式」OAuth JSON 的直接連線：系統瀏覽器授權、一次性 state、PKCE、`youtube.readonly` 與 `yt-analytics.readonly`，不申請修改頻道或收益權限。
- OAuth 用戶端設定與 token 分開用 Windows DPAPI 加密，保存於 SQLite 同層的 `oauth/`；前端及 API 狀態不回傳 client secret、access token 或 refresh token。
- Google 授權後以 `channels.list(mine=true)` 明確取得自己的頻道並加入工作區，立即同步整體、每日及內容 Analytics；之後每 24 小時背景更新，也保留立即同步入口。
- OAuth 同步資料使用獨立的 summary／daily／video SQLite 表，與公開監測、Studio 匯入及手動補值保持來源分離；同步最多最近 365 天，且服從既有私人資料保留設定。
- 中斷連線會嘗試向 Google 撤銷 refresh token，再刪除本機 token 與 OAuth 同步資料；OAuth 用戶端設定可另行刪除。
- 本機 API 的 CORS 由 `*` 收緊為 `http://127.0.0.1:3000` 與 `http://localhost:3000`，其他瀏覽器 Origin 直接回覆 403；不帶 Origin 的本機命令列／健康檢查仍可使用。
- 頻道工作區主流程顯示 OAuth 連線、安全說明、同步狀態、私人摘要、最近每日資料與期間熱門內容；既有 Studio 匯入、完整內容瀏覽器及手動補值收進「進階備援」並完整保留。
- OAuth 連線區新增內建疑難排解，涵蓋 403 測試使用者、Analytics API 未啟用、測試 token 七天後失效、scope、錯誤 JSON、無資料、配額及網路問題；常見 Google 英文錯誤會轉成繁中原因、操作步驟與設定連結，原文只放在技術細節。
- 「立即同步」會持續顯示「同步中…」並每兩秒讀取實際背景狀態，完成後顯示資料日期，失敗則保留可操作錯誤；OAuth 同步不再被公開監測 API Key 的啟動前置條件誤擋。
- 新增 SQLite `discovery_batches` 與 `discovery_candidates`，每次探索保存去重後候選、搜尋詞、規則證據、未收錄原因及人工處理狀態。
- 新增 `/candidates` 候選審核頁與本機 API，可依批次搜尋、篩選、排序、開啟頻道、人工收錄、稍後處理或排除；讀取既有候選不使用 YouTube Search API 配額。
- 候選頁與 README 區分「搜尋候選」和「確認收錄」，並說明台 V 自述比對邊界。
- Studio 繁中內容報表已支援影片發布時間／長度、互動觀看及主要觀眾指標；含總計列時不會和影片明細重複加總。
- 新增 `/api/creator/analytics`，以完整的最新解析內容為資料範圍，支援日期、內容／遊戲文字、來源報表、明細／總計、內容格式、內容主題、核對狀態、排序方向及分頁；缺值不會被當成 0，排序時固定放在最後。
- 頻道工作區的「已解析內容資料」可直接點擊所有可見欄位排序，並以篩選控制列切換日期範圍、類型、主題、報表、狀態及每頁筆數；桌面使用表格內橫向捲動，行動版維持單欄控制與頁面不橫向溢出。
- Studio 匯入區已明列 CSV／TSV／ZIP、可辨識欄位、8 MB 單檔上限、ZIP 最多 20 份報表、原始檔不保存，以及目前不支援 XLSX。
- 全站頁尾顯示「靜靜子Shizumum Ch. 製作」。
- 直播時段熱圖新增統計日期、實際涵蓋頻道、可用直播場次與最集中時段結論；每格保存對應直播清單，點擊後可查看頻道、標題、台北時間、最高同接與 YouTube 連結。
- 內容分類改以人工確認、標題、YouTube 類別、影片標籤、說明文字為順序；早安台／朝活／早安配信固定先視為雜談，明寫歌回或辨識到遊戲名稱時由明確標題主題優先，避免沿用 SEO 標籤造成誤判。
- 支援「歌回 + 雜談」等混合主題；內容卡片以組合名稱呈現，每筆內容在總數與占比只計一次。
- 遊戲辨識加入 YouTube Gaming 類別、內建常見遊戲別名與既有人工確認的遊戲名稱；代表內容顯示分類來源／證據及觀看／訂閱比排序規則。
- 內容代表項目可人工複選主題及確認遊戲名稱；修正另存 `video_classification_overrides`，後續掃描不會覆蓋，亦可改回自動分類。
- Windows 啟動器不再於啟動腳本結束後無期限等待長時間服務保留的輸出管線；服務啟動完成後可直接按「停止」，執行狀態也會在同一個啟動器視窗繼續更新。
- Windows 安裝包會帶入版本化的最新版 ICO，桌面、開始功能表與解除安裝項目皆明確使用該圖示，覆蓋安裝時會重新建立捷徑。
- 版本號已由 `0.8.3` 更新為 `0.8.4`；已依白名單流程產生正式 `tai-v-pulse-0.8.4-public.zip`、`tai-v-pulse-0.8.4-setup.exe` 及各自的 SHA-256。

## 修改過的重要檔案

- `app/dashboard.tsx`、`app/globals.css`：首次使用引導、總覽指標短句、台北時間時鐘、指定頻道搜尋排序，以及頁尾預設收合且可強制保持展開的公開資料搬遷區。
- `collector/server.py`、`app/dashboard.tsx`：辨識上傳播放清單 404 原因、逐頻道略過、繁中介面警示及本機技術診斷紀錄。
- `tests/test_collector.py`：失效頻道既有資料保留、同批正常頻道仍更新、掃描游標推進，以及介面／診斷訊息分流的回歸測試。
- `app/trends/trends-dashboard.tsx`、`app/globals.css`：比較建立器兩列欄位、7～365 天圖表期間、期間語意提示、四項市場排行與動態說明、熱門內容主題及組織範圍控制，以及桌面／窄版退化樣式。
- `app/insights/insights-dashboard.tsx`、`app/globals.css`：頁尾閱讀引導、成長涵蓋範圍及觀看／訂閱／同接缺值的就地提示。
- `collector/server.py`：趨勢 API 的 7 天預設、熱門內容主題過濾、獨立組織範圍與填寫完整度；保留既有成長資料但不讓全部組織資料污染同級排行、熱門內容或快照成熟度。
- `tests/rendered-html.test.mjs`：首頁引導、台北時間、區塊順序、搬遷預設收合與保持展開條件，以及趨勢建立器欄位／跨欄樣式的 server-render／原始碼回歸檢查。
- `collector/public_transfer.py`：公開監測 ZIP schema、逐表逐欄白名單、JSONL／SHA-256、預覽完整性驗證、合併去重，以及保留私人引用的取代交易。
- `collector/server.py`：公開資料匯出、匯入預覽與匯入 API，ZIP 二進位回應／上傳大小限制，並以現有資料庫鎖與背景工作鎖隔離寫入。
- `app/dashboard.tsx`、`app/globals.css`：搬遷操作區、選用黑名單／證據、檔案預覽、筆數與完整性結果、合併／取代影響及響應式樣式。
- `tests/test_collector.py`、`tests/rendered-html.test.mjs`：私人來源不外洩、可選資料、取代保留私人引用、合併不重複、竄改拒絕及首頁入口回歸。
- `app/dashboard.tsx`、`app/creator/creator-dashboard.tsx`、`app/trends/trends-dashboard.tsx`、`app/globals.css`：收錄規則就地編輯、期間熱門內容卡列、排行 portal 跳卡、比較群組核取條件，以及桌面／行動版樣式。
- `collector/oauth.py`、`collector/server.py`：以 OAuth 唯讀授權批次取得熱門影片 metadata，保存於私人 OAuth 內容表，並於工作區 payload 提供標題、縮圖及發布／直播日期與缺值 fallback。
- `tests/test_collector.py`、`tests/rendered-html.test.mjs`：OAuth metadata 來源隔離、四項 UI 結構、互動入口及缺值處理的回歸檢查。
- `collector/oauth.py`：DPAPI 密文儲存、OAuth state／PKCE、token 交換／更新／撤銷、Google 錯誤分類，以及 YouTube／Analytics 唯讀要求。
- `collector/server.py`：OAuth 資料表、連線／同步／刪除 API、同步工作狀態、繁中解法 payload、每日排程、Analytics payload 與 CORS 限制；另保留候選、公開監測及 Studio 路徑。
- `app/candidates/`、`app/dashboard.tsx`、`app/globals.css`：候選審核頁、入口與版面。
- `app/creator/creator-dashboard.tsx`、`app/globals.css`：個人／團隊上層摘要、公開團隊合計與缺值處理、管理頻道入口後移、OAuth 精簡摘要與按需管理、錯誤自動展開、同步輪詢、私人同步資料、響應式版面、進階備援，以及解析內容中的分類證據／遊戲名稱顯示。
- `collector/server.py`、`app/insights/insights-dashboard.tsx`、`app/globals.css`：分類證據與遊戲別名、人工修正保存、混合主題、熱圖摘要／明細 API、互動清單與響應式版面。
- `app/legal-footer.tsx`：全站製作者署名。
- `tests/test_collector.py`、`tests/rendered-html.test.mjs`：DPAPI 密文、PKCE／唯讀 scope、CORS、OAuth 同步來源隔離、分類優先度、混合主題不重複、人工修正沿用、熱圖明細，以及既有候選／Studio／渲染測試。
- `windows/TaiVPulseLauncher.cs`、`windows/TaiVPulseInstaller.cs`、`scripts/build-windows-installer.ps1`、`tests/windows-startup.test.ps1`：啟動輸出管線有限等待、最新版捷徑圖示封裝／指定及回歸檢查。
- `README.md`：既有 OAuth、資料與分享說明，以及本次熱圖操作、混合主題、分類優先度與人工修正方式。
- `CHANGELOG.md`、`docs/CURRENT_STATE.md`：未發佈功能紀錄、實際測試範圍、限制與交接狀態。

## 測試結果

### `0.9.0` 正式整合與產物驗證

- `python -m unittest tests.test_collector`：42 項通過。
- `npx tsc --noEmit`：通過。
- `node --test tests/rendered-html.test.mjs`：7 項通過。
- `npm run lint`：0 個錯誤、63 個既有 `<img>` 最佳化警告；警告包含現行外部縮圖與 `work/` 歷史驗證副本。
- PowerShell 設定獨立 `WRANGLER_LOG_PATH` 後執行 `npx vinext build`：通過；辨識 `/`、`/candidates`、`/creator`、`/insights`、`/legal`、`/trends` 六個 route。
- Windows PowerShell 5.1 UTF-8 編碼測試、啟動／相依／解除安裝入口測試：通過。
- `scripts/package-public.ps1` 與 `scripts/build-windows-installer.ps1`：通過；SHA-256 已獨立重算並與 sidecar 一致。
- 未以真實 API Key、OAuth JSON／token、Studio 原始檔或既有私人 SQLite 執行測試。

### 本次頻道工作區資訊層級實際執行

- `npx tsc --noEmit`：通過。
- `npx eslint app/creator/creator-dashboard.tsx`：0 個錯誤、5 個既有頻道／內容縮圖 `<img>` 最佳化警告；本組沒有新增圖片來源。
- `node --test tests/rendered-html.test.mjs`：7 項通過；新增檢查個人／團隊分段、模式切換狀態保留說明、團隊公開資料界線、摘要／管理區順序、OAuth 按需展開與私人資料不進團隊合計。
- PowerShell 設定獨立 `WRANGLER_LOG_PATH` 後執行 `npx vinext build`：production build 通過；測試 log 已清除。
- 本組沒有修改 API 或 SQLite 結構，也沒有使用真實 OAuth JSON、token 或私人頻道資料。錯誤自動展開由載入／同步 payload 的狀態分支與原始碼回歸檢查覆蓋。

### 本輪完成項目的最終整合檢查

- `python -m unittest tests.test_collector`：42 項通過。
- `npx tsc --noEmit`：通過。
- `npm run lint`：0 個錯誤、50 個既有 `<img>` 最佳化警告；警告包含現行頁面與 `work/` 歷史驗證副本。
- `node --test tests/rendered-html.test.mjs`：7 項通過。
- `npx vinext build`：通過；可辨識 `/`、`/candidates`、`/creator`、`/insights`、`/legal` 與 `/trends` 六個 app route。
- 先前該輪只完成整合檢查，當時未打包、升版或操作 GitHub；本次已由上方 `0.9.0` 正式發佈結果取代。

### 本次內容環境文案與缺值提示實際執行

- `npx tsc --noEmit`：通過。
- `npx eslint app/insights/insights-dashboard.tsx tests/rendered-html.test.mjs`：0 個錯誤、3 個既有內容縮圖 `<img>` 最佳化警告；本次沒有新增圖片。
- `node --test tests/rendered-html.test.mjs`：7 項通過；新增檢查「看懂內容環境」完整文案、成長與同接缺值就地提示，並確認舊的「至少 30 天／至少兩筆快照」頁尾門檻已移除。
- 本組只調整現有 payload 的呈現與缺值判讀，未新增 API 或資料欄位；production build 會在完成後續頻道工作區同一輪 UI 後統一重跑。

### 本次趨勢頁剩餘功能實際執行

- `python -m unittest tests.test_collector`：42 項通過；新增驗證 7 天最低／預設語意、混合主題熱門內容篩選、全部組織可納入級距外頻道、同級組織只取目前範圍，以及擴大組織資料不會進入同級熱門內容。
- `npx tsc --noEmit`：通過。
- `npx eslint app/trends/trends-dashboard.tsx tests/rendered-html.test.mjs`：0 個錯誤、1 個既有熱門內容縮圖 `<img>` 最佳化警告；本次沒有新增圖片來源。
- PowerShell 設定獨立 `WRANGLER_LOG_PATH` 後執行 `npx vinext build`：production build 通過。
- `node --test tests/rendered-html.test.mjs`：7 項通過；涵蓋 7 天預設、14／365 天與自訂入口、圖表／30 日統計分流文案、四項排行、完整主題選項、組織範圍參數，以及固定比較表仍保留 30 日訂閱變化。
- 本機瀏覽器確認 7 天為預設、7／14／30／90／365 天與自訂入口可見，且舊 API payload 缺少新增組織完整度欄位時不會讓整頁崩潰。因既有本機服務佔用正式 3000／8787 埠，為避免中斷使用者服務，本次停止以臨時 SQLite 進行完整的主題／組織切換瀏覽器驗收；相關資料隔離與結果由臨時 SQLite 單元測試覆蓋。

### 本次失效上傳播放清單容錯實際執行

- `python -m unittest tests.test_collector`：41 項通過；新增覆蓋單一 `playlistNotFound` 404 不終止整批、既有影片保留、正常頻道新影片仍寫入、失效頻道掃描時間推進，以及繁中警示不暴露原始 JSON、診斷紀錄保留 Channel／Playlist ID 與技術原因。
- `npx tsc --noEmit`：通過；監測首頁摘要 payload 已加入可缺省的警示欄位並優先顯示友善警示。
- `node --test tests/rendered-html.test.mjs`：7 項通過。
- 本次未用真實失效頻道呼叫 YouTube API；錯誤原因解析與批次續跑使用假的 404 回應及臨時 SQLite 驗證，沒有讀取既有 `work/` 或私人資料。

### 本次監測首頁資訊層級實際執行

- `npx tsc --noEmit`：通過。
- `npx eslint app/dashboard.tsx tests/rendered-html.test.mjs`：0 個錯誤、4 個既有 `<img>` 最佳化警告；本次沒有新增圖片來源。
- PowerShell 設定獨立的 `WRANGLER_LOG_PATH` 後執行 `npx vinext build`：production build 通過。
- `node --test tests/rendered-html.test.mjs`：7 項通過；新增檢查首頁首次引導與台北時間可 server-render、指定搜尋位於總覽後、搬遷位於已確認頻道後且初始內容不展開，原始碼仍保留完整匯入／匯出操作與強制展開條件。
- 臨時空白 SQLite 的本機瀏覽器檢查：1280 px 下指定搜尋位於總覽後、直播雷達前，搬遷位於已確認頻道後；台北時間在 effect 執行後顯示 `HH:mm`，搬遷可展開及再收合。390 × 844 下首次引導、搜尋與搬遷維持單欄，頁面 `scrollWidth` 未超過 viewport。
- 本次沒有用實際 ZIP 操作檔案選擇、預覽或匯入，因此「選檔／預覽／錯誤時強制保持展開」由 React 狀態條件與回歸檢查覆蓋，未做真實檔案互動；沒有讀取既有 `work/`、SQLite 或私人資料。

### 本次趨勢頁純 UI 調整實際執行

- `npx tsc --noEmit`：通過。
- `npx eslint app/trends/trends-dashboard.tsx tests/rendered-html.test.mjs`：0 個錯誤、1 個既有熱門內容縮圖 `<img>` 最佳化警告；本次沒有新增圖片。
- PowerShell 設定獨立的 `WRANGLER_LOG_PATH` 後執行 `npx vinext build`：production build 通過。
- `node --test tests/rendered-html.test.mjs`：7 項通過；新增檢查固定比較頻道與儲存工具列可 server-render、舊的散落 footer 已移除，桌面四欄及第二列兩欄跨距都有樣式回歸。
- 臨時空白 SQLite 的本機瀏覽器檢查：1280 px 下第一列四欄皆為 175 px，第二列兩個長選單皆為 359 px，畢業頻道條件位於比較群組欄內；390 × 844 下六個主控制依序單欄顯示，頁面 `scrollWidth` 未超過 viewport。測試只驗證 UI 排列與響應式版面，沒有更動或驗證趨勢資料語意。

### 本次公開監測資料搬遷實際執行

- `python -m unittest tests.test_collector`：40 項通過；新增測試覆蓋公開 ZIP 精確檔案白名單、不含私人 sentinel、黑名單／來源證據選項、版本與筆數預覽、取代時保留工作區與手動補值、同一包重複合併不增加歷史列，以及等長竄改觸發 SHA-256 拒絕。
- `npx tsc --noEmit`：通過。
- `npm run lint`：0 個錯誤、50 個既有 `<img>` 最佳化警告；警告包含 repository 現行圖片與 `work/` 歷史副本，本次搬遷介面沒有新增圖片。
- PowerShell 設定 `WRANGLER_LOG_PATH` 後執行 `npx vinext build`：production build 通過。
- `node --test tests/rendered-html.test.mjs`：7 項通過；首頁輸出包含搬遷入口、合併／取代、私人排除界線與目的端 API Key 提示。
- 本機瀏覽器版面檢查：一般桌面寬度為雙欄；390 × 844 窄版覆寫下改為單欄，頁面 `scrollWidth` 未超過 viewport，匯出邊界、合併及取代說明均可見。這次瀏覽器只驗證空白測試頁的結構與響應式版面，沒有以現有 `work/` 或私人資料操作匯出／匯入。

先前公開搬遷功能完成時尚未重新打包或升版；本次已納入 `0.9.0`。另一台實體 Windows 電腦、接近 256 MB 上限的實際資料包或執行中的真實排程端到端搬遷驗收仍未完成。

### 本次四項 UI 修正實際執行

- `python -m unittest tests.test_collector`：37 項通過；包含 OAuth 期間熱門內容 metadata payload、私人表保存與不寫入公開 `videos` 表的檢查。
- `npx tsc --noEmit`：通過。
- `npm run lint`：0 個錯誤、50 個 `<img>` 最佳化警告；警告包含 repository 既有與 `work/` 歷史副本，本次縮圖沿用現有外部圖片做法。
- PowerShell 設定 `WRANGLER_LOG_PATH` 後執行 `npx vinext build`：production build 通過。
- `node --test tests/rendered-html.test.mjs`：7 項通過。
- `npm test` 在 Windows PowerShell 會先被 `package.json` 既有的 POSIX 環境變數語法擋下，尚未進入建置；本次已用上列等價命令分別完成 build 與 HTML 測試，未為這四項 UI 修正擴改建置腳本。
- 實際本機瀏覽器驗證：收錄規則的展開、取消與儲存；市場排行前兩列跳卡完整顯示、方向計算、最上層 portal、滑鼠／鍵盤開啟、Esc 與關閉；點擊核取方塊文字可切換；390 × 844 下頁面無橫向溢出、跳卡完整、篩選條件維持在比較群組下方。
- 真實 Google 帳戶的 OAuth metadata 同步未在本次重新執行；標題、縮圖、日期 payload 與來源隔離由假的 Google 回應及臨時 SQLite 單元測試覆蓋，未使用或寫入私人憑證／頻道資料。

先前四項 UI 修正完成時尚未重新打包；本次已納入 `0.9.0`，既有 `0.8.4` 仍不包含這些修正。

### 本次熱圖與分類功能實際執行

- `python -m unittest tests.test_collector`：37 項通過。
- TypeScript 型別檢查：通過。
- ESLint：0 個錯誤；只有既有 `<img>` 最佳化警告。
- vinext production build：通過。
- `node --test tests/rendered-html.test.mjs`：7 項通過。
- OAuth 測試使用臨時 DPAPI 密文與假的 Google 回應，未寫入真實 client ID、token 或私人頻道資料。

本次沒有重建 ZIP／Windows 安裝程式，也沒有以實際頻道資料完成桌面或行動版的熱圖點擊、YouTube 連結及人工修正互動驗收。

### 既有 `0.8.4` 產物的歷史驗證（本次由 `0.9.0` 取代）

- Windows PowerShell UTF-8 編碼測試：通過。
- Windows 啟動、相依與解除安裝入口測試：通過。
- 啟動器有限等待與捷徑圖示來源回歸檢查：通過。
- 正式 `0.8.4` Windows 安裝程式建置、兩次靜默安裝／覆蓋升級、版本化 ICO 內容雜湊及啟動器檔案版本檢查：通過；安裝程式版本為 `0.8.4.0`。
- 實際本機 API 與桌面瀏覽器排序互動：通過。
- 390 × 844 行動版瀏覽器檢查：控制列為單欄、搜尋／清除／翻頁按鈕可見、頁面無橫向溢出，寬表只在表格容器內捲動。
- 對外 ZIP 白名單檢查：51 個項目，包含 `collector/oauth.py`，不含 `.env`、`work/`、OAuth `.dat`、SQLite、Studio CSV／TSV、依賴、LOG 或 Git metadata。
- 正式 ZIP SHA-256：`6e226dc9787d471168f9140427c555f800d4203c903dab7c937f5f8e0d7530f1`；正式 Windows 安裝程式 SHA-256：`2420a53670fe5cd67e0ddc460d15e66d2e2f9e2c694e0ecaae658e73df6178a0`，均與隨附校驗檔一致。

## 尚未解決事項

- 公開搬遷目前只接受 `format_version=1`，單次上傳上限 256 MB、解壓後上限 1 GB；尚未用接近上限的大型真實同接資料驗證瀏覽器記憶體、匯出耗時與匯入交易時間。
- 尚未在兩台實體 Windows 電腦完成來源匯出、檔案傳遞、目的端預覽、合併、取代、重啟與後續 API 更新的完整驗收；目前證據為臨時 SQLite、自動測試、型別、lint、production build 與 server-render。
- 合併模式對相同頻道／影片以 `updated_at` 判斷較新版本；同一父項目與相同 `captured_at` 的歷史列視為同一資料點並保留目的端既有列。若未來需要對相同時間但數值不同的資料做衝突檢視，需另設明確衝突模型，不能靜默猜測來源優先序。
- 既有 `0.8.3`、`0.8.4`、`0.9.0` 與 `outputs/pending-version-20260729-oauth-help-final/` 內候選包是歷史產物；目前正式分享檔是 GitHub Release 的 `tai-v-pulse-0.12.0-setup.exe` 及同名 `.sha256`。
- 尚未完成真實頻道 Analytics 成功同步、24 小時續期、撤銷及重新連結的完整端到端驗收；自動測試仍只使用臨時 DPAPI 密文與假的 Google 回應，沒有把私人憑證或頻道資料放進 repository。
- Google OAuth 同意畫面若維持測試狀態，refresh token 通常 7 天後失效；長期個人使用需在 Google Cloud 設定正式發布。未驗證應用程式可能仍顯示 Google 警告。
- 目前直接同步使用 YouTube Analytics Targeted Queries API，未納入曝光、曝光點閱率、回訪觀眾、收益及 Reporting API 報表；這些欄位可繼續由 Studio 進階備援匯入。
- Studio 匯入目前不接受 XLSX；額外圖表與工作表不會匯入。CSV／TSV／ZIP 實際可用指標仍取決於 Studio 匯出內容。
- 內容主題與格式仍是可檢查的規則分類；內建遊戲別名不可能涵蓋所有作品，證據不足時仍保留「其他／未判斷」，由使用者人工確認，不猜測內容。
- 尚未用既有私人資料庫驗證 `0.9.0` 的分類器版本遷移，也未在真實瀏覽器完成熱圖明細與人工修正的桌面／行動版互動驗收；目前證據是臨時 SQLite 單元測試、型別、渲染與 production build。
- 尚未在另一台乾淨 Windows 電腦完成 `0.12.0` 正式包的首次安裝、執行環境自動安裝、啟動、長時間運作及資料保留實機驗收，也尚未實機完成 0.10.x／0.11.0 → 0.12.0 的內建更新流程。
- `0.12.0` Windows 安裝程式仍未進行 Authenticode 程式碼簽章，SmartScreen 可能顯示「未知發行者」；SHA-256 只能驗證檔案一致，不能取代簽章。

## 下一步建議

- 先用兩份不含憑證、且已備妥可回復環境的實際公開監測資料，分別驗證合併與取代；確認頻道／影片／快照／同接／人工分類筆數、私人工作區與 Analytics 未變，以及目的電腦設定自己的 API Key 後可繼續更新。
- 另以大量同接樣本測試 256 MB 上限附近的匯出、瀏覽器上傳、預覽與 SQLite 交易耗時；若實際資料接近上限，再依量測決定是否改成串流上傳，不先提高安全限制。
- 先在可安全復原的測試環境驗證既有本機資料啟動遷移、熱圖統計／明細、分類證據、人工修正保存與重新啟動後沿用；再以桌面及行動版瀏覽器確認鍵盤、觸控與版面。
- 對外只提供 `0.12.0` 正式 GitHub Release 的安裝程式與同名 SHA-256；更早版本保留為歷史產物，不再宣稱包含本次調整。
- 由使用者在 Google Cloud 建立自己的桌面 OAuth 用戶端，以實際頻道依序驗證首次授權、立即同步、24 小時續期、Google 撤銷與重新連結；驗收時不要分享 JSON 或 token。
- 在乾淨 Windows 電腦驗證 `0.12.0` 正式包的首次安裝、缺少執行環境時的引導、啟動及資料保留，並從既有 0.10.x／0.11.0 實際執行內建更新到 0.12.0。
