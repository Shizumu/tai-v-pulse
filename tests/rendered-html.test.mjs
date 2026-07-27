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
  assert.match(html, /href="\/insights"[^>]*>內容環境/);
  assert.match(html, /分類/);
  assert.match(html, /最近更新/);
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

test("starter preview is fully removed", async () => {
  const [page, layout, dashboard, insights, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/insights/insights-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  assert.doesNotMatch(page, /_sites-preview|SkeletonPreview|codex-preview/);
  assert.match(layout, /lang="zh-Hant"/);
  assert.match(dashboard, /查看詳細資料/);
  assert.match(dashboard, /訂閱趨勢/);
  assert.match(insights, /同級表現基準/);
  assert.match(insights, /直播時段熱圖/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
});
