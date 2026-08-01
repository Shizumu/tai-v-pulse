import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render(pathname = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${pathname}`, {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the Taiwan VTuber dashboard", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<title>台V Pulse｜私人數據監測台<\/title>/i);
  assert.match(html, /台V Pulse/);
  assert.match(html, /探索台 V 頻道/);
  assert.match(html, /收錄規則/);
  assert.match(html, /指定 VTuber 搜尋/);
  assert.match(html, /先收錄一個頻道，開始看懂自己的位置/);
  assert.match(html, /台北時間/);
  assert.match(html, /公開監測資料搬遷/);
  assert.doesNotMatch(html, /匯出公開監測 ZIP/);
  assert.match(html, /href="\/insights"[^>]*>內容環境/);
  assert.match(html, /href="\/trends"[^>]*>趨勢圖表/);
  assert.match(html, /href="\/creator"[^>]*>頻道工作區/);
  assert.match(html, /分類/);
  assert.match(html, /最近更新/);
  assert.match(html, /公開快照保留/);
  assert.match(html, /我的頻道資料/);
  assert.match(html, /使用、隱私與授權聲明/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape/);
});

test("server-renders the content landscape page", async () => {
  const response = await render("/insights");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<title>內容環境｜台V Pulse<\/title>/i);
  assert.match(html, /選擇要觀察的頻道環境/);
  assert.match(html, /參考頻道的 0.5～2 倍/);
});

test("server-renders the private creator page", async () => {
  const response = await render("/creator");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<title>頻道工作區｜台V Pulse<\/title>/i);
  assert.match(html, /PRIVATE CREATOR ANALYTICS/);
  assert.match(html, /私人 Analytics 依管理頻道分開保存/);
  assert.match(html, /href="\/"[^>]*>監測首頁/);
  assert.match(html, /href="\/insights"[^>]*>內容環境/);
});

test("server-renders the candidate review page", async () => {
  const response = await render("/candidates");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<title>候選審核｜台V Pulse<\/title>/i);
  assert.match(html, /搜尋結果先成為候選/);
  assert.match(html, /不耗搜尋配額/);
  assert.match(html, /大小寫相同/);
  assert.match(html, /href="\/creator"[^>]*>頻道工作區/);
});

test("server-renders the local trend dashboard", async () => {
  const response = await render("/trends");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<title>趨勢圖表｜台V Pulse<\/title>/i);
  assert.match(html, /MARKET TRENDS/);
  assert.match(html, /以我的頻道建立比較/);
  assert.match(html, /包含已確認畢業頻道/);
  assert.match(html, /加入固定比較頻道（最多 5 個）/);
  assert.match(html, /儲存固定比較組合/);
  assert.match(html, /圖表觀察期間/);
  assert.match(html, /自訂天數/);
  assert.match(html, /只調整歷史折線/);
});

test("server-renders the local legal and license notice", async () => {
  const response = await render("/legal");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<title>使用、隱私與授權聲明｜台V Pulse<\/title>/i);
  assert.match(html, /公開 API 快照最多保存 30 天/);
  assert.match(html, /PolyForm Noncommercial License 1.0.0/);
  assert.match(html, /休止與疑似畢業標示/);
});

test("starter preview is fully removed", async () => {
  const [page, layout, dashboard, insights, trends, styles, creator, candidates, legalConsent, legalFooter, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/insights/insights-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/trends/trends-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/creator/creator-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/candidates/candidate-review.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/legal-consent.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/legal-footer.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  assert.doesNotMatch(page, /_sites-preview|SkeletonPreview|codex-preview/);
  assert.match(layout, /lang="zh-Hant"/);
  assert.match(dashboard, /查看詳細資料/);
  assert.match(dashboard, /訂閱趨勢/);
  assert.match(dashboard, /疑似已畢業/);
  assert.match(dashboard, /頻道資料 →/);
  assert.match(dashboard, /公開 API 快照保留/);
  assert.match(dashboard, /我的頻道匯入資料保留/);
  assert.match(dashboard, /可修改設定摘要/);
  assert.match(dashboard, /系統資訊（唯讀）/);
  assert.match(dashboard, /inline-settings-form/);
  assert.match(dashboard, /取消編輯/);
  assert.doesNotMatch(dashboard, /className="panel settings-panel"/);
  assert.match(dashboard, /手動新增待更新/);
  assert.match(dashboard, /分時開台偵測/);
  assert.match(dashboard, /enhanced_live_scan_times\.join\("、"\)/);
  assert.match(dashboard, /18:05～01:05 每小時輕量檢查一次/);
  assert.match(dashboard, /大小寫與空白差異會自動辨識/);
  assert.match(dashboard, /查看候選審核與未收錄原因/);
  assert.match(dashboard, /設定.*的頻道資料/);
  assert.match(dashboard, /公開監測資料搬遷/);
  assert.match(dashboard, /publicTransferMustStayOpen/);
  assert.match(dashboard, /aria-expanded={publicTransferExpanded}/);
  assert.match(dashboard, /data\.eligible_channels === 0/);
  assert.match(dashboard, /指定收錄頻道/);
  assert.match(dashboard, /setInterval\(updateClock, 60000\)/);
  assert.match(dashboard, /匯出公開監測 ZIP/);
  assert.match(dashboard, /合併（建議）/);
  assert.match(dashboard, /取代本機公開監測資料/);
  assert.match(dashboard, /絕不包含/);
  assert.match(dashboard, /後續更新仍須在這台電腦自行設定 YouTube API Key/);
  assert.ok(dashboard.indexOf('id="specific-channel-search"') < dashboard.indexOf('className="content-grid"'), "指定頻道搜尋應位於總覽後、直播雷達與探索設定前");
  assert.ok(dashboard.indexOf("public-transfer-panel") > dashboard.indexOf('className="panel channel-panel"'), "公開資料搬遷應位於已確認頻道之後");
  assert.match(insights, /同級表現基準/);
  assert.match(insights, /直播時段熱圖/);
  assert.match(insights, /統計日期/);
  assert.match(insights, /點擊有數字的格子可查看頻道/);
  assert.match(insights, /混合主題只算一筆內容/);
  assert.match(insights, /分類依據/);
  assert.match(insights, /代表內容排序/);
  assert.match(insights, /儲存並沿用/);
  assert.match(insights, /代表內容/);
  assert.match(insights, /一般影片/);
  assert.match(insights, /背景更新中，閱讀位置會保留/);
  assert.match(insights, /看懂內容環境/);
  assert.match(insights, /先選擇一個基準頻道，觀察訂閱規模相近的頻道在做什麼/);
  assert.match(insights, /趨勢與成長比較會隨資料累積而更可靠/);
  assert.match(insights, /尚未累積到.*天前的頻道資料/);
  assert.match(insights, /尚無直播同接樣本/);
  assert.doesNotMatch(insights, /建議至少累積 30 天/);
  assert.doesNotMatch(insights, /至少兩筆快照/);
  assert.match(trends, /30 天摘要與變化正在累積/);
  assert.match(trends, /公開觀看黏著度/);
  assert.match(trends, /固定比較線/);
  assert.match(trends, /className="comparison-control"/);
  assert.match(trends, /className="saved-comparison-toolbar"/);
  assert.match(trends, /加入固定比較頻道（最多 5 個）/);
  assert.doesNotMatch(trends, /className="comparison-builder-footer"/);
  assert.match(styles, /\.trend-control-grid \{ display: grid; grid-template-columns: repeat\(4, minmax\(120px, 1fr\)\)/);
  assert.match(styles, /\.trend-control-grid \.reference-control, \.trend-control-grid \.comparison-control \{ grid-column: span 2; \}/);
  assert.match(trends, /觀看成長/);
  assert.match(trends, /固定頻道指標比較/);
  assert.match(trends, /createPortal/);
  assert.match(trends, /data-placement/);
  assert.match(trends, /event\.key === "Escape"/);
  assert.match(trends, /關閉比較跳卡/);
  assert.match(trends, /trend-filter-stack[^\n]*比較群組[^\n]*graduated-toggle[^\n]*包含已確認畢業頻道/);
  assert.match(trends, /\["subscribers", "median_views", "stickiness", "ccv_rate"\]/);
  assert.match(trends, /TOPIC_OPTIONS = \["全部", "遊戲", "雜談", "歌回", "ASMR", "音樂作品", "紀念／重大活動", "其他"\]/);
  assert.match(trends, /video\.content_type\.split\("\+"\)\.some\(\(part\) => part\.trim\(\) === topic\)/);
  assert.match(trends, /visibleTopVideos = trends\?\.rankings\.top_videos\.filter/);
  assert.match(trends, /visibleTopVideos\.slice\(0, 8\)/);
  assert.match(trends, /organization_scope/);
  assert.match(trends, /periodMode/);
  assert.match(trends, /min=\{7\} max=\{365\}/);
  assert.match(trends, /30日訂閱變化/);
  assert.match(creator, /YouTube Studio/);
  assert.match(creator, /直接連結我的 YouTube 頻道/);
  assert.match(creator, /Windows 電腦以 DPAPI 加密保存/);
  assert.match(creator, /進階備援：Studio 匯入與手動補值/);
  assert.match(creator, /Google Analytics API 唯讀同步/);
  assert.match(creator, /連線或同步遇到問題/);
  assert.match(creator, /Analytics API 尚未啟用/);
  assert.match(creator, /按立即同步像沒反應/);
  assert.match(creator, /每兩秒確認狀態/);
  assert.match(creator, /原始檔不會保存/);
  assert.match(creator, /搜尋並加入管理頻道/);
  assert.match(creator, /未達一般收錄門檻也能加入工作區/);
  assert.match(creator, /管理頻道與團隊比較/);
  assert.match(creator, /workspaceMode === "personal"/);
  assert.match(creator, /workspaceMode === "team"/);
  assert.match(creator, /aria-pressed=\{workspaceMode === "personal"\}/);
  assert.match(creator, /模式只切換上方公開摘要；目前選取頻道、篩選條件與下方展開內容都會保留/);
  assert.match(creator, /全部為公開監測統計/);
  assert.match(creator, /私人 OAuth、Studio 與手動補值仍各自綁定選取頻道/);
  assert.match(creator, /oauthManagerOpen &&/);
  assert.match(creator, /aria-expanded=\{oauthManagerOpen\}/);
  assert.match(creator, /私人資料只屬於這個頻道，不會加入團隊公開合計或其他頻道比較/);
  assert.ok(creator.indexOf("WORKSPACE VIEW") < creator.indexOf("ADD MANAGED CHANNEL"));
  assert.ok(creator.indexOf("ADD MANAGED CHANNEL") < creator.indexOf("PRIVATE ANALYTICS CONNECTION"));
  assert.match(creator, /已自動沿用監測首頁資料/);
  assert.match(creator, /尚未納入摘要的欄位/);
  assert.match(creator, /互動觀看次數/);
  assert.match(creator, /已解析內容資料/);
  assert.match(creator, /搜尋內容或遊戲名稱/);
  assert.match(creator, /缺值不當成 0/);
  assert.match(creator, /目前不支援 XLSX/);
  assert.match(creator, /ZIP 最多解析 20 份/);
  assert.match(creator, /期間觀看最高內容/);
  assert.match(creator, /oauth-video-row/);
  assert.match(creator, /發布／直播日期/);
  assert.match(creator, /row\.content_date/);
  assert.match(creator, /在新分頁開啟/);
  assert.doesNotMatch(creator, /row\.title \?\? row\.video_id/);
  assert.match(legalFooter, /靜靜子Shizumum Ch\. 製作/);
  assert.match(candidates, /候選數不等於收錄數/);
  assert.match(candidates, /YouTube Search API 不提供命中欄位/);
  assert.match(legalConsent, /同意並開始使用/);
  assert.match(packageJson, /PolyForm-Noncommercial-1.0.0/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
});
