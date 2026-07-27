"use client";

import { useCallback, useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";
import Link from "next/link";
import SiteHeader from "../site-header";

const API_BASE = process.env.NEXT_PUBLIC_TRACKER_API ?? "http://127.0.0.1:8787";

type Channel = {
  channel_id: string;
  title: string;
  handle: string | null;
  thumbnail_url: string | null;
  subscriber_count: number | null;
  view_count: number | null;
  video_count: number | null;
  category: string;
  organization_name: string;
  manual_tags: string[];
};

type Summary = {
  channels: Channel[];
  search_quota_available: boolean;
  settings: { min_subscribers: number };
};

type ChannelCandidate = {
  channel_id: string;
  title: string;
  handle: string | null;
  thumbnail_url: string | null;
  subscriber_count: number | null;
  hidden_subscriber_count: boolean;
  description: string;
  meets_threshold: boolean;
  already_added: boolean;
};

type ImportBatch = {
  id: number;
  filename: string;
  imported_at: string;
  report_count: number;
  row_count: number;
  inserted_count: number;
  duplicate_count: number;
  conflict_count: number;
  date_start: string | null;
  date_end: string | null;
};

type AnalyticsRow = {
  id: number;
  batch_id: number;
  report_name: string;
  event_date: string | null;
  video_id: string | null;
  video_title: string | null;
  views: number | null;
  watch_time_hours: number | null;
  impressions: number | null;
  impressions_ctr: number | null;
  conflict_status: number;
};

type ManualMetric = {
  id: number;
  metric_date: string;
  video_id: string;
  metric_name: string;
  metric_value: number;
  note: string;
};

type CreatorData = {
  owned_channel_id: string | null;
  channel: Channel | null;
  public: null | {
    peak_concurrent: number | null;
    concurrency_sample_count: number;
    snapshots: unknown[];
    videos: unknown[];
  };
  imports: ImportBatch[];
  imported_overview: Record<string, number | null>;
  overview_sources: Record<string, number>;
  recent_rows: AnalyticsRow[];
  manual_metrics: ManualMetric[];
};

type Preview = {
  filename: string;
  report_count: number;
  row_count: number;
  recognized_metrics: string[];
  date_start: string | null;
  date_end: string | null;
  reports: { report_name: string; row_count: number; recognized_metrics: string[] }[];
};

type PendingUpload = {
  filename: string;
  size: number;
  content_base64: string;
  preview: Preview | null;
  error: string | null;
};

const METRIC_LABELS: Record<string, string> = {
  views: "觀看次數",
  watch_time_hours: "觀看時間（小時）",
  average_view_duration_seconds: "平均觀看時間（秒）",
  average_percentage_viewed: "平均觀看百分比",
  impressions: "曝光次數",
  impressions_ctr: "曝光點閱率",
  subscribers_net: "訂閱淨變化",
  subscribers_gained: "新增訂閱",
  subscribers_lost: "流失訂閱",
  likes: "喜歡次數",
  comments: "留言數",
  shares: "分享次數",
  unique_viewers: "不重複觀眾",
  returning_viewers: "回訪觀眾",
  estimated_revenue: "預估收益",
};

function compact(value: number | null | undefined, digits = 1) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: digits }).format(value);
}

function exact(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-TW", { maximumFractionDigits: digits }).format(value);
}

function dateTime(value: string | null | undefined) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Taipei" }).format(new Date(value));
}

async function filePayload(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let index = 0; index < bytes.length; index += 32768) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 32768));
  }
  return btoa(binary);
}

export default function CreatorDashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [creator, setCreator] = useState<CreatorData | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [channelChoice, setChannelChoice] = useState("");
  const [savingChannel, setSavingChannel] = useState(false);
  const [channelQuery, setChannelQuery] = useState("");
  const [channelCandidates, setChannelCandidates] = useState<ChannelCandidate[]>([]);
  const [searchingChannel, setSearchingChannel] = useState(false);
  const [addingChannelId, setAddingChannelId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingUpload[]>([]);
  const [preparing, setPreparing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [manualDate, setManualDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [manualName, setManualName] = useState("views");
  const [manualValue, setManualValue] = useState("");
  const [manualVideoId, setManualVideoId] = useState("");
  const [manualNote, setManualNote] = useState("");
  const [savingManual, setSavingManual] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [summaryResponse, creatorResponse] = await Promise.all([
        fetch(`${API_BASE}/api/summary`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/creator`, { cache: "no-store" }),
      ]);
      if (!summaryResponse.ok || !creatorResponse.ok) throw new Error("本機資料服務沒有回應");
      const [summaryPayload, creatorPayload] = await Promise.all([
        summaryResponse.json() as Promise<Summary>,
        creatorResponse.json() as Promise<CreatorData>,
      ]);
      setSummary(summaryPayload);
      setCreator(creatorPayload);
      setChannelChoice((current) => creatorPayload.owned_channel_id
        ?? (summaryPayload.channels.some((channel) => channel.channel_id === current) ? current : ""));
      setConnected(true);
    } catch (error) {
      setConnected(false);
      setMessage(error instanceof Error ? error.message : "無法載入我的頻道");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 30000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  const saveOwnedChannel = async () => {
    if (!channelChoice) return;
    setSavingChannel(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/creator`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel_id: channelChoice }),
      });
      const payload = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法設定頻道");
      setMessage(payload.message ?? "我的頻道已設定");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法設定頻道");
    } finally {
      setSavingChannel(false);
    }
  };

  const searchOwnedChannel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const query = channelQuery.trim();
    if (!query) return;
    setSearchingChannel(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/channel-search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const payload = await response.json() as { candidates?: ChannelCandidate[]; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "搜尋失敗");
      setChannelCandidates(payload.candidates ?? []);
      if (!(payload.candidates ?? []).length) setMessage("找不到這個 YouTube 頻道");
    } catch (error) {
      setChannelCandidates([]);
      setMessage(error instanceof Error ? error.message : "搜尋失敗");
    } finally {
      setSearchingChannel(false);
    }
  };

  const chooseOwnedCandidate = async (candidate: ChannelCandidate) => {
    setAddingChannelId(candidate.channel_id);
    setMessage(null);
    try {
      const response = await fetch(
        `${API_BASE}${candidate.already_added ? "/api/creator" : "/api/creator/channel"}`,
        {
          method: candidate.already_added ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ channel_id: candidate.channel_id }),
        },
      );
      const payload = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法設定我的頻道");
      setMessage(payload.message ?? "我的頻道已設定");
      setChannelCandidates([]);
      setChannelQuery("");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法設定我的頻道");
    } finally {
      setAddingChannelId(null);
    }
  };

  const prepareFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []).slice(0, 5);
    event.target.value = "";
    if (!files.length) return;
    setPreparing(true);
    setMessage(null);
    const prepared: PendingUpload[] = [];
    for (const file of files) {
      if (file.size > 8 * 1024 * 1024) {
        prepared.push({ filename: file.name, size: file.size, content_base64: "", preview: null, error: "檔案超過 8 MB" });
        continue;
      }
      try {
        const content_base64 = await filePayload(file);
        const response = await fetch(`${API_BASE}/api/creator/import-preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: file.name, content_base64 }),
        });
        const payload = await response.json() as { preview?: Preview; error?: string };
        if (!response.ok || !payload.preview) throw new Error(payload.error ?? "無法辨識報表");
        prepared.push({ filename: file.name, size: file.size, content_base64, preview: payload.preview, error: null });
      } catch (error) {
        prepared.push({ filename: file.name, size: file.size, content_base64: "", preview: null, error: error instanceof Error ? error.message : "無法辨識報表" });
      }
    }
    setPending(prepared);
    setPreparing(false);
  };

  const importFiles = async () => {
    const valid = pending.filter((item) => item.preview && item.content_base64);
    if (!valid.length) return;
    setImporting(true);
    setMessage(null);
    try {
      const results: string[] = [];
      for (const item of valid) {
        const response = await fetch(`${API_BASE}/api/creator/import`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: item.filename, content_base64: item.content_base64 }),
        });
        const payload = await response.json() as { message?: string; error?: string };
        if (!response.ok) throw new Error(`${item.filename}：${payload.error ?? "匯入失敗"}`);
        results.push(`${item.filename}：${payload.message ?? "完成"}`);
      }
      setPending([]);
      setMessage(results.join("；"));
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "匯入失敗");
    } finally {
      setImporting(false);
    }
  };

  const rollbackImport = async (batch: ImportBatch) => {
    if (!window.confirm(`回復「${batch.filename}」這次匯入嗎？\n\n只會刪除這個匯入批次所新增的私人 Analytics 列，不會動到公開監測資料。`)) return;
    const response = await fetch(`${API_BASE}/api/creator/imports/${batch.id}`, { method: "DELETE" });
    const payload = await response.json() as { message?: string; error?: string };
    setMessage(response.ok ? payload.message ?? "匯入已回復" : payload.error ?? "無法回復匯入");
    if (response.ok) await refresh();
  };

  const saveManual = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSavingManual(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/creator/manual`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ metric_date: manualDate, metric_name: manualName, metric_value: manualValue, video_id: manualVideoId, note: manualNote }),
      });
      const payload = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法儲存補充資料");
      setManualValue("");
      setManualVideoId("");
      setManualNote("");
      setMessage(payload.message ?? "手動補充資料已儲存");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法儲存補充資料");
    } finally {
      setSavingManual(false);
    }
  };

  const removeManual = async (item: ManualMetric) => {
    if (!window.confirm(`刪除 ${item.metric_date} 的「${METRIC_LABELS[item.metric_name] ?? item.metric_name}」補充值嗎？`)) return;
    const response = await fetch(`${API_BASE}/api/creator/manual/${item.id}`, { method: "DELETE" });
    const payload = await response.json() as { message?: string; error?: string };
    setMessage(response.ok ? payload.message ?? "已刪除" : payload.error ?? "無法刪除");
    if (response.ok) await refresh();
  };

  const overviewCards = useMemo(() => [
    ["views", "匯入期間觀看", (value: number) => compact(value)],
    ["watch_time_hours", "觀看時間", (value: number) => `${exact(value, 1)} 小時`],
    ["impressions", "曝光次數", (value: number) => compact(value)],
    ["impressions_ctr", "曝光點閱率", (value: number) => `${exact(value, 2)}%`],
    ["subscribers_net", "訂閱淨變化", (value: number) => exact(value)],
    ["average_view_duration_seconds", "平均觀看時間", (value: number) => `${exact(value)} 秒`],
  ] as const, []);

  return (
    <main className="app-shell creator-shell">
      <SiteHeader active="creator" eyebrow="PRIVATE CREATOR ANALYTICS" title="我的頻道" connected={connected} actions={<button className="button ghost" type="button" onClick={() => void refresh()} disabled={loading}>更新資料</button>} />

      {!connected && <section className="notice warning"><span className="notice-icon">!</span><div><strong>等待本機資料服務</strong><p>啟動台V Pulse 後，這頁會讀取既有公開監測資料與你的私人匯入資料。</p></div></section>}
      {message && <section className="inline-message">{message}</section>}
      {loading && <section className="panel creator-placeholder">正在整理你的頻道資料…</section>}

      {!loading && summary && !creator?.owned_channel_id && <section className="panel creator-onboarding">
        <div className="onboarding-heading"><p className="section-kicker">START HERE</p><h2>先把自己的頻道接上公開監測資料</h2><p>「我的頻道」不會複製另一份公開資料，而是連結監測首頁的同一筆頻道紀錄，再加上只留在本機的 YouTube Studio Analytics。</p></div>
        <ol><li><strong>1</strong><span>搜尋或加入自己的頻道</span></li><li><strong>2</strong><span>設為我的頻道，立即沿用公開監測</span></li><li><strong>3</strong><span>需要時再匯入 Studio 補充私人數據</span></li></ol>
        <div className="data-flow"><span>公開監測資料</span><b>＋</b><span>本機 Studio 匯入</span><b>→</b><strong>我的頻道分析</strong></div>
      </section>}

      {!loading && summary && <section className="panel creator-channel-picker">
        <div><p className="section-kicker">OWNED CHANNEL</p><h2>{creator?.owned_channel_id ? "切換我的頻道" : "選擇已收錄的頻道"}</h2><p>如果已經在監測名單中可直接選擇；找不到時使用下方搜尋，不必先切換頁面。</p></div>
        <label><span>已收錄頻道</span><select value={channelChoice} onChange={(event) => setChannelChoice(event.target.value)}><option value="">請選擇你的頻道</option>{summary.channels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.title}｜{compact(channel.subscriber_count)} 訂閱</option>)}</select></label>
        <button className="button primary" type="button" onClick={() => void saveOwnedChannel()} disabled={!channelChoice || savingChannel}>{savingChannel ? "儲存中…" : creator?.owned_channel_id ? "切換我的頻道" : "設為我的頻道"}</button>
      </section>}

      {!loading && summary && <section className="panel creator-channel-search">
        <div className="panel-heading"><div><p className="section-kicker">DIRECT LOOKUP</p><h2>搜尋並設為我的頻道</h2></div><span>{summary.search_quota_available ? "名稱、網址、@handle 或 Channel ID" : "搜尋配額已滿：請使用網址、@handle 或 Channel ID"}</span></div>
        <form className="specific-form" onSubmit={(event) => void searchOwnedChannel(event)}><label><span>自己的 YouTube 頻道</span><input value={channelQuery} onChange={(event) => setChannelQuery(event.target.value)} placeholder="貼上頻道網址、@handle、Channel ID 或名稱" /></label><button className="button primary" type="submit" disabled={!connected || searchingChannel || !channelQuery.trim()}>{searchingChannel ? "搜尋中…" : "搜尋頻道"}</button></form>
        {channelCandidates.length > 0 && <div className="candidate-list">{channelCandidates.map((candidate) => <article className="candidate-card" key={candidate.channel_id}>{candidate.thumbnail_url ? <img src={candidate.thumbnail_url} alt="" /> : <span className="candidate-avatar">V</span>}<div className="candidate-copy"><div className="candidate-title"><strong>{candidate.title}</strong><span>{candidate.hidden_subscriber_count ? "訂閱未公開" : `${compact(candidate.subscriber_count)} 訂閱`}</span></div><small>{candidate.handle ?? candidate.channel_id}</small><p>{candidate.description || "這個頻道沒有公開說明。"}</p>{!candidate.meets_threshold && <p className="owned-exception-note">未達一般收錄門檻也可設為自己的頻道；不會納入台 V 整體比較。</p>}</div><button className="button candidate-action" type="button" onClick={() => void chooseOwnedCandidate(candidate)} disabled={addingChannelId === candidate.channel_id}>{addingChannelId === candidate.channel_id ? "設定中…" : candidate.already_added ? "設為我的頻道" : "加入並設為我的頻道"}</button></article>)}</div>}
        <p className="panel-footnote">你也可以回到 <Link href="/">監測首頁</Link> 先確認公開資料。URL、@handle 與 Channel ID 不使用搜尋配額。</p>
      </section>}

      {creator?.channel && <>
        <section className="panel creator-profile">
          <div className="creator-identity">{creator.channel.thumbnail_url ? <img src={creator.channel.thumbnail_url} alt="" /> : <span>V</span>}<div><p className="section-kicker">{creator.channel.category}{creator.channel.organization_name ? ` · ${creator.channel.organization_name}` : ""}</p><h2>{creator.channel.title}</h2><p>{creator.channel.handle ?? creator.channel.channel_id}</p><div className="tag-row">{creator.channel.manual_tags.map((tag) => <i key={tag}>#{tag}</i>)}</div></div></div>
          <a className="button external-button" href={creator.channel.handle ? `https://www.youtube.com/${creator.channel.handle}` : `https://www.youtube.com/channel/${creator.channel.channel_id}`} target="_blank" rel="noreferrer">開啟 YouTube ↗</a>
        </section>

        <section className="creator-public-grid">
          <article className="insight-hero primary"><span>公開訂閱</span><strong>{compact(creator.channel.subscriber_count)}</strong><p>沿用監測首頁的最新快照</p></article>
          <article className="insight-hero"><span>頻道總觀看</span><strong>{compact(creator.channel.view_count)}</strong><p>公開累積值</p></article>
          <article className="insight-hero"><span>公開影片數</span><strong>{compact(creator.channel.video_count)}</strong><p>目前 YouTube API 統計</p></article>
          <article className="insight-hero"><span>歷史最高同接</span><strong>{compact(creator.public?.peak_concurrent)}</strong><p>{exact(creator.public?.concurrency_sample_count)} 個同接資料點</p></article>
        </section>

        <section className="creator-two-column">
          <article className="panel creator-import-panel">
            <div className="panel-heading"><div><p className="section-kicker">YOUTUBE STUDIO IMPORT</p><h2>匯入私人 Analytics</h2></div><span>CSV、TSV 或 ZIP · 每次最多 5 個</span></div>
            <div className="import-dropzone"><input type="file" accept=".csv,.tsv,.zip,text/csv,text/tab-separated-values,application/zip" multiple onChange={(event) => void prepareFiles(event)} disabled={preparing || importing} /><strong>{preparing ? "正在檢查報表…" : "選擇 YouTube Studio 匯出檔"}</strong><p>先預覽欄位與列數，確認後才寫入。原始檔不會保存，只留下解析後數值與來源批次。</p></div>
            {pending.length > 0 && <div className="upload-preview-list">{pending.map((item) => <article className={item.error ? "invalid" : ""} key={item.filename}><div><strong>{item.filename}</strong><span>{(item.size / 1024).toFixed(1)} KB</span></div>{item.error ? <p>{item.error}</p> : item.preview && <p>{item.preview.report_count} 份報表 · {item.preview.row_count} 列 · {item.preview.recognized_metrics.map((metric) => METRIC_LABELS[metric] ?? metric).join("、")} · {item.preview.date_start ?? "無日期"}～{item.preview.date_end ?? "無日期"}</p>}</article>)}</div>}
            {pending.some((item) => item.preview) && <div className="import-actions"><button className="button ghost" type="button" onClick={() => setPending([])} disabled={importing}>取消</button><button className="button primary" type="button" onClick={() => void importFiles()} disabled={importing}>{importing ? "匯入中…" : "確認匯入"}</button></div>}
            <p className="panel-footnote">YouTube Studio「進階模式」可匯出目前報表；單次介面匯出最多 500 列。若下載為 ZIP，可以直接上傳。<a href="https://support.google.com/youtube/answer/9717005?hl=zh-Hant" target="_blank" rel="noreferrer">查看官方說明 ↗</a></p>
          </article>

          <aside className="panel creator-privacy-panel">
            <div className="panel-heading"><div><p className="section-kicker">LOCAL & PRIVATE</p><h2>資料怎麼合併</h2></div></div>
            <dl className="rules-list"><div><dt>相同檔案</dt><dd>整份略過</dd></div><div><dt>相同資料列</dt><dd>不重複寫入</dd></div><div><dt>同鍵不同值</dt><dd>標示衝突，最新批次顯示</dd></div><div><dt>手動補值</dt><dd>獨立保存，不覆蓋原始列</dd></div><div><dt>原始檔</dt><dd>解析後立即丟棄</dd></div></dl>
            <p className="creator-private-note">收益等敏感欄位只有在你匯出的檔案包含時才會保存；整個 work 資料夾不會上傳 GitHub。</p>
          </aside>
        </section>

        <section className="panel imported-overview-panel">
          <div className="panel-heading"><div><p className="section-kicker">PRIVATE OVERVIEW</p><h2>匯入資料摘要</h2></div><span>各指標使用最新一個包含該欄位的報表批次</span></div>
          <div className="creator-private-grid">{overviewCards.map(([key, label, formatter]) => <article key={key}><span>{label}</span><strong>{creator.imported_overview[key] === null || creator.imported_overview[key] === undefined ? "—" : formatter(creator.imported_overview[key]!)}</strong><p>{creator.overview_sources[key] ? `來源批次 #${creator.overview_sources[key]}` : "尚未匯入"}</p></article>)}</div>
          {creator.imported_overview.estimated_revenue !== null && creator.imported_overview.estimated_revenue !== undefined && <p className="revenue-note">已匯入預估收益：{exact(creator.imported_overview.estimated_revenue, 2)}。此欄位只顯示在「我的頻道」。</p>}
        </section>

        <section className="creator-two-column">
          <article className="panel import-history-panel">
            <div className="panel-heading"><div><p className="section-kicker">IMPORT HISTORY</p><h2>匯入紀錄</h2></div><span>{creator.imports.length} 個批次</span></div>
            <div className="table-wrap"><table><thead><tr><th>檔案</th><th>期間</th><th>結果</th><th>匯入時間</th><th>操作</th></tr></thead><tbody>{creator.imports.length === 0 ? <tr><td colSpan={5} className="table-empty">尚未匯入 YouTube Studio 報表。</td></tr> : creator.imports.map((batch) => <tr key={batch.id}><td><strong>{batch.filename}</strong><small className="table-subline">批次 #{batch.id} · {batch.report_count} 份報表</small></td><td>{batch.date_start ?? "—"}～{batch.date_end ?? "—"}</td><td>{batch.inserted_count} 新增 · {batch.duplicate_count} 重複{batch.conflict_count > 0 && <span className="conflict-badge">{batch.conflict_count} 衝突</span>}</td><td>{dateTime(batch.imported_at)}</td><td><button className="danger-button" type="button" onClick={() => void rollbackImport(batch)}>回復</button></td></tr>)}</tbody></table></div>
          </article>

          <aside className="panel manual-entry-panel">
            <div className="panel-heading"><div><p className="section-kicker">MANUAL SUPPLEMENT</p><h2>手動補漏</h2></div></div>
            <form className="manual-metric-form" onSubmit={(event) => void saveManual(event)}><label><span>日期</span><input type="date" value={manualDate} onChange={(event) => setManualDate(event.target.value)} required /></label><label><span>指標</span><select value={manualName} onChange={(event) => setManualName(event.target.value)}>{Object.entries(METRIC_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>數值</span><input type="number" step="any" value={manualValue} onChange={(event) => setManualValue(event.target.value)} required /></label><label><span>影片 ID（選填）</span><input value={manualVideoId} onChange={(event) => setManualVideoId(event.target.value)} maxLength={100} /></label><label className="manual-note"><span>備註</span><input value={manualNote} onChange={(event) => setManualNote(event.target.value)} maxLength={300} placeholder="說明數據來源或補值原因" /></label><button className="button primary" type="submit" disabled={savingManual}>{savingManual ? "儲存中…" : "儲存補充值"}</button></form>
          </aside>
        </section>

        <section className="panel recent-private-panel">
          <div className="panel-heading"><div><p className="section-kicker">RECENT PRIVATE ROWS</p><h2>最近解析資料</h2></div><span>{creator.recent_rows.length} 列預覽</span></div>
          <div className="table-wrap"><table><thead><tr><th>日期／內容</th><th>報表</th><th>觀看</th><th>觀看時數</th><th>曝光</th><th>點閱率</th><th>狀態</th></tr></thead><tbody>{creator.recent_rows.length === 0 ? <tr><td colSpan={7} className="table-empty">匯入後可在這裡核對解析結果。</td></tr> : creator.recent_rows.slice(0, 40).map((row) => <tr key={row.id}><td><strong>{row.event_date ?? "總計／無日期"}</strong><small className="table-subline">{row.video_title ?? row.video_id ?? "整體報表"}</small></td><td>{row.report_name}</td><td>{exact(row.views)}</td><td>{exact(row.watch_time_hours, 1)}</td><td>{exact(row.impressions)}</td><td>{row.impressions_ctr === null ? "—" : `${exact(row.impressions_ctr, 2)}%`}</td><td>{row.conflict_status ? <span className="conflict-badge">待核對</span> : <span className="state-badge">正常</span>}</td></tr>)}</tbody></table></div>
        </section>

        <section className="panel manual-history-panel">
          <div className="panel-heading"><div><p className="section-kicker">MANUAL HISTORY</p><h2>手動補充紀錄</h2></div><span>{creator.manual_metrics.length} 筆</span></div>
          <div className="table-wrap"><table><thead><tr><th>日期</th><th>指標</th><th>數值</th><th>影片</th><th>備註</th><th>操作</th></tr></thead><tbody>{creator.manual_metrics.length === 0 ? <tr><td colSpan={6} className="table-empty">目前沒有手動補充資料。</td></tr> : creator.manual_metrics.map((item) => <tr key={item.id}><td>{item.metric_date}</td><td>{METRIC_LABELS[item.metric_name] ?? item.metric_name}</td><td>{exact(item.metric_value, 2)}</td><td>{item.video_id || "整體"}</td><td>{item.note || "—"}</td><td><button className="danger-button" type="button" onClick={() => void removeManual(item)}>刪除</button></td></tr>)}</tbody></table></div>
        </section>
      </>}

      <footer><span>台V Pulse · 我的頻道</span><span>私人 Analytics 與匯入內容只保存在你的電腦</span></footer>
    </main>
  );
}
