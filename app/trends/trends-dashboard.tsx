"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import SiteHeader from "../site-header";

const API_BASE = process.env.NEXT_PUBLIC_TRACKER_API ?? "http://127.0.0.1:8787";

type Channel = {
  channel_id: string;
  title: string;
  thumbnail_url: string | null;
  subscriber_count: number | null;
  category: string;
};

type Summary = {
  channels: Channel[];
  categories: { category: string; channel_count: number }[];
  settings: { min_subscribers: number };
  owned_channel_id: string | null;
  owned_channel: Channel | null;
};

type Delta = {
  period_days: number;
  current: number | null;
  previous: number | null;
  change: number | null;
  percent_change: number | null;
  basis_at: string | null;
  ready: boolean;
};

type TrendChannel = Channel & {
  organization_name: string;
  activity_status: string;
  is_reference: boolean;
  recent_items: number;
  previous_items: number;
  median_views: number | null;
  median_peak_concurrent: number | null;
  stickiness: number | null;
  ccv_rate: number | null;
  subscriber_delta_7: Delta;
  subscriber_delta_30: Delta;
  view_delta_30: Delta;
  median_views_delta: Delta;
  stickiness_delta: Delta;
  ccv_rate_delta: Delta;
};

type RankedVideo = {
  video_id: string;
  channel_id: string;
  title: string;
  channel_title: string;
  thumbnail_url: string | null;
  view_count: number | null;
  view_rate: number | null;
  format_type: string;
  content_type: string;
  attributes: string[];
};

type Trends = {
  generated_at: string;
  reference: TrendChannel | null;
  overview: {
    peer_channels: number;
    active_channels: number;
    median_subscribers: number | null;
    median_views: number | null;
    median_stickiness: number | null;
  };
  rankings: {
    subscribers: TrendChannel[];
    growth_7: TrendChannel[];
    growth_30: TrendChannel[];
    median_views: TrendChannel[];
    stickiness: TrendChannel[];
    ccv_rate: TrendChannel[];
    top_videos: RankedVideo[];
    organizations: {
      organization_name: string;
      members: number;
      subscriber_count: number;
      median_views_total: number;
      median_stickiness: number | null;
    }[];
  };
  series: {
    channel_id: string;
    title: string;
    points: { date: string; subscriber_count: number | null; view_count: number | null }[];
  }[];
  peer_series: { date: string; subscriber_count: number | null }[];
  readiness: {
    oldest_snapshot_at: string | null;
    collected_days: number;
    week_ready: boolean;
    month_ready: boolean;
  };
  private_metrics: Record<string, number | null> | null;
};

const TIERS: Record<string, [number, number, string]> = {
  "1k-5k": [1000, 5000, "1,000～5,000"],
  "5k-10k": [5000, 10000, "5,000～10,000"],
  "10k-50k": [10000, 50000, "1 萬～5 萬"],
  "50k-100k": [50000, 100000, "5 萬～10 萬"],
  "100k+": [100000, 100000000, "10 萬以上"],
};

const COLORS = ["#2ca981", "#ce6f93", "#4c8ecb", "#8b72ca", "#c18a2d"];

function compact(value: number | null | undefined, digits = 1) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: digits }).format(value);
}

function exact(value: number | null | undefined, digits = 1) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-TW", { maximumFractionDigits: digits }).format(value);
}

function percent(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(Math.abs(value) < 10 ? 1 : 0)}%`;
}

function DeltaBadge({ delta, label, collectedDays }: { delta: Delta; label: string; collectedDays: number }) {
  if (!delta.ready || delta.change === null) {
    return <button className="delta-badge pending" type="button" aria-label={`${label}歷史資料累積中`}><span>◷</span><i><strong>資料累積中</strong><small>已累積 {collectedDays.toFixed(1)}／{delta.period_days} 天</small><small>累積完成後自動顯示漲跌</small></i></button>;
  }
  const state = delta.change > 0 ? "up" : delta.change < 0 ? "down" : "flat";
  const arrow = state === "up" ? "↑" : state === "down" ? "↓" : "→";
  return <button className={`delta-badge ${state}`} type="button" aria-label={`${label}${state === "up" ? "上升" : state === "down" ? "下降" : "持平"}`}><span>{arrow}</span><i><strong>與 {delta.period_days} 天前比較</strong><small>目前：{exact(delta.current)}</small><small>先前：{exact(delta.previous)}</small><small>變化：{delta.change > 0 ? "+" : ""}{exact(delta.change)}（{delta.percent_change !== null && delta.percent_change > 0 ? "+" : ""}{percent(delta.percent_change)}）</small>{delta.basis_at && <small>基準：{new Date(delta.basis_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</small>}</i></button>;
}

function SubscriberChart({ series, peerSeries }: { series: Trends["series"]; peerSeries: Trends["peer_series"] }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const width = Math.max(320, canvas.parentElement?.clientWidth ?? 760);
      const height = 300;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.scale(ratio, ratio);
      context.clearRect(0, 0, width, height);
      const dates = [...new Set([...peerSeries.map((point) => point.date), ...series.flatMap((row) => row.points.map((point) => point.date))])].sort();
      const values = [
        ...peerSeries.map((point) => point.subscriber_count),
        ...series.flatMap((row) => row.points.map((point) => point.subscriber_count)),
      ].filter((value): value is number => value !== null);
      if (dates.length === 0 || values.length === 0) {
        context.fillStyle = "#6d7973";
        context.font = "14px system-ui";
        context.fillText("歷史快照累積後會顯示折線", 22, 42);
        return;
      }
      const padding = { left: 62, right: 18, top: 22, bottom: 38 };
      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = Math.max(1, max - min);
      const x = (date: string) => padding.left + (dates.indexOf(date) / Math.max(1, dates.length - 1)) * (width - padding.left - padding.right);
      const y = (value: number) => padding.top + (1 - (value - min) / span) * (height - padding.top - padding.bottom);
      context.strokeStyle = "rgba(111,126,118,.22)";
      context.lineWidth = 1;
      for (let line = 0; line <= 4; line += 1) {
        const lineY = padding.top + line / 4 * (height - padding.top - padding.bottom);
        context.beginPath(); context.moveTo(padding.left, lineY); context.lineTo(width - padding.right, lineY); context.stroke();
        context.fillStyle = "#6d7973"; context.font = "11px system-ui";
        context.fillText(compact(max - span * line / 4), 8, lineY + 4);
      }
      const drawLine = (points: { date: string; subscriber_count: number | null }[], color: string, dashed = false) => {
        context.strokeStyle = color; context.lineWidth = dashed ? 2 : 2.8; context.setLineDash(dashed ? [6, 5] : []);
        context.beginPath(); let started = false;
        for (const point of points) {
          if (point.subscriber_count === null) continue;
          if (!started) { context.moveTo(x(point.date), y(point.subscriber_count)); started = true; }
          else context.lineTo(x(point.date), y(point.subscriber_count));
        }
        context.stroke(); context.setLineDash([]);
      };
      drawLine(peerSeries, "#8f9994", true);
      series.forEach((row, index) => drawLine(row.points, COLORS[index % COLORS.length]));
      context.fillStyle = "#6d7973"; context.font = "11px system-ui";
      context.fillText(dates[0], padding.left, height - 12);
      if (dates.length > 1) context.fillText(dates[dates.length - 1], width - padding.right - 72, height - 12);
    };
    draw();
    const observer = new ResizeObserver(draw);
    if (canvas.parentElement) observer.observe(canvas.parentElement);
    return () => observer.disconnect();
  }, [peerSeries, series]);
  return <canvas ref={canvasRef} role="img" aria-label="頻道訂閱歷史折線圖" />;
}

export default function TrendsDashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [trends, setTrends] = useState<Trends | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);
  const [mode, setMode] = useState("all");
  const [tier, setTier] = useState("5k-10k");
  const [customMin, setCustomMin] = useState(1000);
  const [customMax, setCustomMax] = useState(5000);
  const [category, setCategory] = useState("全部");
  const [referenceId, setReferenceId] = useState("");
  const [cohortIds, setCohortIds] = useState<string[]>([]);
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [formatType, setFormatType] = useState("主要內容");
  const [includeGraduated, setIncludeGraduated] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [ranking, setRanking] = useState<"growth_30" | "growth_7" | "subscribers" | "median_views" | "stickiness" | "ccv_rate">("growth_30");
  const [savedGroups, setSavedGroups] = useState<{ name: string; ids: string[] }[]>([]);
  const [groupName, setGroupName] = useState("");
  const ownedDefaultApplied = useRef(false);
  const trendsRef = useRef<Trends | null>(null);

  const loadSummary = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/summary`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setSummary((await response.json()) as Summary);
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    void loadSummary();
    const timer = window.setInterval(() => void loadSummary(), 30000);
    try {
      const saved = JSON.parse(window.localStorage.getItem("tai-v-pulse-comparison-groups") ?? "[]") as { name: string; ids: string[] }[];
      if (Array.isArray(saved)) setSavedGroups(saved);
    } catch { setSavedGroups([]); }
    return () => window.clearInterval(timer);
  }, [loadSummary]);

  useEffect(() => {
    const timer = window.setInterval(() => setRefreshKey((value) => value + 1), 300000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    trendsRef.current = trends;
  }, [trends]);

  useEffect(() => {
    if (!ownedDefaultApplied.current && summary?.owned_channel_id) {
      ownedDefaultApplied.current = true;
      setReferenceId(summary.owned_channel_id);
      setMode("relative");
    }
  }, [summary?.owned_channel_id]);

  const reference = summary?.channels.find((channel) => channel.channel_id === referenceId)
    ?? (summary?.owned_channel?.channel_id === referenceId ? summary.owned_channel : null);
  const summaryReady = Boolean(summary);
  const referenceAvailable = Boolean(reference);
  const referenceSubscribers = reference?.subscriber_count ?? null;
  const range = useMemo(() => {
    if (mode === "relative" && referenceSubscribers) return [Math.max(1, Math.floor(referenceSubscribers * .5)), Math.ceil(referenceSubscribers * 2)] as const;
    if (mode === "tier") return [TIERS[tier][0], TIERS[tier][1]] as const;
    if (mode === "range") return [Math.max(0, customMin), Math.max(customMin, customMax)] as const;
    return [summary?.settings.min_subscribers ?? 0, 100000000] as const;
  }, [customMax, customMin, mode, referenceSubscribers, summary?.settings.min_subscribers, tier]);
  const cohortKey = cohortIds.join(",");
  const comparisonKey = comparisonIds.join(",");

  useEffect(() => {
    if (!summaryReady || (mode === "relative" && !referenceAvailable) || (mode === "channels" && !cohortKey)) {
      setTrends(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const params = new URLSearchParams({ days: String(days), min_subscribers: String(range[0]), max_subscribers: String(range[1]), category, format_type: formatType, include_graduated: String(includeGraduated) });
    if (referenceId) params.set("reference_channel_id", referenceId);
    if (mode === "channels") params.set("channel_ids", cohortKey);
    if (comparisonKey) params.set("comparison_ids", comparisonKey);
    setLoading(trendsRef.current === null); setError(null);
    fetch(`${API_BASE}/api/trends?${params}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const payload = await response.json() as Trends & { error?: string };
        if (!response.ok) throw new Error(payload.error ?? "無法載入趨勢資料");
        setTrends(payload);
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "無法載入趨勢資料");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [category, cohortKey, comparisonKey, days, formatType, includeGraduated, mode, range, referenceAvailable, referenceId, refreshKey, summaryReady]);

  function addId(setter: (value: string[] | ((current: string[]) => string[])) => void, id: string) {
    if (!id || id === referenceId) return;
    setter((current: string[]) => [...new Set([...current, id])].slice(0, 5));
  }

  function saveGroup() {
    const name = groupName.trim();
    if (!name || comparisonIds.length === 0) return;
    const next = [...savedGroups.filter((group) => group.name !== name), { name, ids: comparisonIds }];
    setSavedGroups(next); setGroupName("");
    window.localStorage.setItem("tai-v-pulse-comparison-groups", JSON.stringify(next));
  }

  const rankingRows = trends?.rankings[ranking] ?? [];
  const rankingMetric = (row: TrendChannel) => ranking === "subscribers" ? row.subscriber_count : ranking === "median_views" ? row.median_views : ranking === "stickiness" ? row.stickiness : ranking === "ccv_rate" ? row.ccv_rate : ranking === "growth_7" ? row.subscriber_delta_7.percent_change : row.subscriber_delta_30.percent_change;
  const rankingDelta = (row: TrendChannel) => ranking === "growth_7" ? row.subscriber_delta_7 : ranking === "growth_30" || ranking === "subscribers" ? row.subscriber_delta_30 : ranking === "median_views" ? row.median_views_delta : ranking === "stickiness" ? row.stickiness_delta : row.ccv_rate_delta;
  const metricLabel = ranking === "subscribers" ? "訂閱數" : ranking === "median_views" ? "觀看中位數" : ranking === "stickiness" ? "公開觀看黏著度" : ranking === "ccv_rate" ? "同接／訂閱比" : ranking === "growth_7" ? "7 日訂閱成長" : "30 日訂閱成長";
  const metricFormatter = (value: number | null) => ["stickiness", "ccv_rate", "growth_7", "growth_30"].includes(ranking) ? percent(value) : compact(value);
  const maxMetric = Math.max(1, ...rankingRows.slice(0, 15).map((row) => Math.max(0, Number(rankingMetric(row) ?? 0))));

  return <main className="app-shell trends-shell">
    <SiteHeader active="trends" eyebrow="MARKET TRENDS" title="趨勢圖表" connected={connected} statusText={trends ? `本機快照更新 ${new Date(trends.generated_at).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Taipei" })}` : undefined} />
    {!connected && <section className="notice warning"><span className="notice-icon">!</span><div><strong>資料服務尚未啟動</strong><p>啟動台V Pulse 後，圖表會自動讀取本機快照。</p></div></section>}
    {error && <section className="inline-message">{error}</section>}

    <section className="panel trend-controls">
      <div className="control-intro"><p className="section-kicker">COMPARISON BUILDER</p><h2>以我的頻道建立比較</h2><p>基準頻道不納入同級中位數；公開資料可公平比較，Studio 私人指標只顯示在自己的區塊。</p></div>
      <div className="trend-control-grid">
        <label><span>期間</span><select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={7}>7 天</option><option value={30}>30 天</option><option value={90}>90 天</option><option value={365}>1 年</option></select></label>
        <label><span>比較群組</span><select value={mode} onChange={(event) => setMode(event.target.value)}><option value="all">全部頻道</option><option value="relative">基準頻道 0.5～2 倍</option><option value="tier">固定量級</option><option value="range">自訂量級</option><option value="channels">指定頻道群組</option></select></label>
        {mode === "tier" && <label><span>訂閱級距</span><select value={tier} onChange={(event) => setTier(event.target.value)}>{Object.entries(TIERS).map(([key, value]) => <option value={key} key={key}>{value[2]}</option>)}</select></label>}
        {mode === "range" && <div className="range-controls"><label><span>最低訂閱</span><input type="number" min={0} value={customMin} onChange={(event) => setCustomMin(Number(event.target.value))} /></label><label><span>最高訂閱</span><input type="number" min={customMin} value={customMax} onChange={(event) => setCustomMax(Number(event.target.value))} /></label></div>}
        {mode === "channels" && <label><span>加入群組</span><select value="" onChange={(event) => addId(setCohortIds, event.target.value)}><option value="">選擇頻道…</option>{summary?.channels.filter((channel) => channel.channel_id !== referenceId && !cohortIds.includes(channel.channel_id)).map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.title}</option>)}</select></label>}
        <label><span>頻道分類</span><select value={category} onChange={(event) => setCategory(event.target.value)}><option>全部</option>{summary?.categories.map((item) => <option value={item.category} key={item.category}>{item.category}</option>)}</select></label>
        <label><span>內容形式</span><select value={formatType} onChange={(event) => setFormatType(event.target.value)}><option>主要內容</option><option>直播</option><option value="影片">一般影片</option><option>Shorts</option><option>全部</option></select></label>
        <label className="reference-control"><span>基準頻道</span><select value={referenceId} onChange={(event) => setReferenceId(event.target.value)}><option value="">不設定基準</option>{summary?.owned_channel && !summary.channels.some((channel) => channel.channel_id === summary.owned_channel?.channel_id) && <option value={summary.owned_channel.channel_id}>我的頻道：{summary.owned_channel.title}</option>}{summary?.channels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.title}｜{compact(channel.subscriber_count)}</option>)}</select></label>
        <label><span>固定比較線（最多 5 個）</span><select value="" onChange={(event) => addId(setComparisonIds, event.target.value)}><option value="">加入頻道…</option>{summary?.channels.filter((channel) => channel.channel_id !== referenceId && !comparisonIds.includes(channel.channel_id)).map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.title}</option>)}</select></label>
        <label className="graduated-toggle"><input type="checkbox" checked={includeGraduated} onChange={(event) => setIncludeGraduated(event.target.checked)} /><span>包含已確認畢業頻道</span></label>
      </div>
      {mode === "channels" && <div className="cohort-channel-chips"><span>指定群組</span><div className="selected-channel-chips">{cohortIds.map((id) => <button type="button" onClick={() => setCohortIds((current) => current.filter((value) => value !== id))} key={id}>{summary?.channels.find((channel) => channel.channel_id === id)?.title ?? id}<span>×</span></button>)}</div></div>}
      <div className="comparison-builder-footer"><div><span>圖表固定比較線</span><div className="selected-channel-chips">{comparisonIds.map((id) => <button type="button" onClick={() => setComparisonIds((current) => current.filter((value) => value !== id))} key={id}>{summary?.channels.find((channel) => channel.channel_id === id)?.title ?? id}<span>×</span></button>)}</div></div><div className="save-group-row"><input value={groupName} onChange={(event) => setGroupName(event.target.value)} placeholder="儲存比較組合" /><button type="button" onClick={saveGroup} disabled={!groupName.trim() || comparisonIds.length === 0}>儲存</button>{savedGroups.length > 0 && <select value="" onChange={(event) => { const group = savedGroups.find((item) => item.name === event.target.value); if (group) setComparisonIds(group.ids.slice(0, 5)); }}><option value="">載入組合…</option>{savedGroups.map((group) => <option value={group.name} key={group.name}>{group.name}</option>)}</select>}</div></div>
      <div className="cohort-summary"><span>{referenceId === summary?.owned_channel_id ? "以我的頻道為基準" : "目前比較"}</span><strong>{mode === "relative" && reference ? `${reference.title} 的 0.5～2 倍` : mode === "tier" ? TIERS[tier][2] : mode === "range" ? `${exact(range[0])}～${exact(range[1])}` : mode === "channels" ? `${cohortIds.length} 個指定頻道` : "全部已收錄頻道"}</strong><small>{mode === "channels" ? "指定群組" : `${exact(range[0])}～${range[1] >= 100000000 ? "不限上限" : exact(range[1])} 訂閱`}</small></div>
    </section>

    {loading && !trends && <section className="panel insight-placeholder">正在整理趨勢資料…</section>}
    {!loading && mode === "channels" && cohortIds.length === 0 && <section className="panel insight-placeholder"><div><strong>先加入要比較的頻道</strong><p>可指定最多 5 個頻道形成自訂比較群組。</p></div></section>}
    {trends && <>
      {!trends.readiness.month_ready && <section className="notice trend-readiness"><span className="notice-icon">◷</span><div><strong>30 天趨勢正在累積</strong><p>目前已累積 {trends.readiness.collected_days.toFixed(1)} 天；訂閱排行與目前觀看表現可先使用，週／月漲跌會在資料成熟後自動解鎖。</p></div></section>}
      <section className="trend-overview-grid">
        <article className="insight-hero primary"><span>同級頻道</span><strong>{trends.overview.peer_channels}</strong><p>{trends.overview.active_channels} 個近 30 天有內容</p></article>
        <article className="insight-hero"><span>同級訂閱中位數</span><strong>{compact(trends.overview.median_subscribers)}</strong><p>不包含基準頻道</p></article>
        <article className="insight-hero"><span>同級觀看中位數</span><strong>{compact(trends.overview.median_views)}</strong><p>{formatType === "主要內容" ? "不包含 Shorts" : formatType}</p></article>
        <article className="insight-hero"><span>同級公開黏著度</span><strong>{percent(trends.overview.median_stickiness)}</strong><p>觀看中位數／訂閱數</p></article>
        {trends.reference && <article className="insight-hero owned-trend-card"><span>我的公開訂閱</span><div className="metric-with-delta"><strong>{compact(trends.reference.subscriber_count)}</strong><DeltaBadge delta={trends.reference.subscriber_delta_30} label="訂閱數" collectedDays={trends.readiness.collected_days} /></div><p>{trends.reference.title}</p></article>}
        {trends.reference && <article className="insight-hero"><span>我的公開黏著度</span><div className="metric-with-delta"><strong>{percent(trends.reference.stickiness)}</strong><DeltaBadge delta={trends.reference.stickiness_delta} label="公開觀看黏著度" collectedDays={trends.readiness.collected_days} /></div><p>不是 Studio 回訪觀眾</p></article>}
      </section>

      <section className="panel trend-chart-panel">
        <div className="panel-heading"><div><p className="section-kicker">SUBSCRIBER HISTORY</p><h2>訂閱趨勢比較</h2></div><span>實線為指定頻道 · 灰色虛線為同級中位數</span></div>
        <div className="trend-chart"><SubscriberChart series={trends.series} peerSeries={trends.peer_series} /></div>
        <div className="chart-legend"><span><i className="peer" />同級中位數</span>{trends.series.map((row, index) => <span key={row.channel_id}><i style={{ background: COLORS[index % COLORS.length] }} />{row.title}</span>)}</div>
      </section>

      <section className="panel ranking-panel">
        <div className="panel-heading efficiency-heading"><div><p className="section-kicker">MARKET RANKINGS</p><h2>市場排行</h2></div><div className="format-tabs ranking-tabs">{(["growth_30", "growth_7", "subscribers", "median_views", "stickiness", "ccv_rate"] as const).map((key) => <button className={ranking === key ? "active" : ""} type="button" onClick={() => setRanking(key)} key={key}>{key === "growth_30" ? "30日成長" : key === "growth_7" ? "7日成長" : key === "subscribers" ? "訂閱數" : key === "median_views" ? "觀看中位數" : key === "stickiness" ? "公開黏著度" : "同接／訂閱"}</button>)}</div></div>
        <p className="efficiency-explainer">公開觀看黏著度＝最近 30 日觀看中位數／目前訂閱數；不是 YouTube Studio 的回訪觀眾或留存率。</p>
        <div className="ranking-layout"><div className="trend-bars">{rankingRows.slice(0, 15).map((row, index) => { const value = rankingMetric(row); return <div className={`trend-bar-row${row.is_reference ? " reference" : ""}`} key={row.channel_id}><span>{index + 1}</span><div><strong>{row.title}{row.is_reference ? "（我的頻道）" : ""}</strong><small>{compact(row.subscriber_count)} 訂閱 · {row.recent_items} 項內容</small></div><div className="trend-bar-track"><i style={{ width: `${Math.max(2, Math.max(0, Number(value ?? 0)) / maxMetric * 100)}%` }} /></div><b>{metricFormatter(value)}</b><DeltaBadge delta={rankingDelta(row)} label={metricLabel} collectedDays={trends.readiness.collected_days} /></div>; })}</div></div>
      </section>

      <section className="insight-two-column trend-secondary-grid">
        <article className="panel"><div className="panel-heading"><div><p className="section-kicker">POPULAR CONTENT</p><h2>近 30 天熱門內容</h2></div><span>{formatType === "主要內容" ? "直播＋一般影片" : formatType}</span></div><div className="top-content-list">{trends.rankings.top_videos.slice(0, 8).map((video, index) => <a className="top-content-row" href={`https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer" key={video.video_id}><span className="rank">{index + 1}</span>{video.thumbnail_url ? <img src={video.thumbnail_url} alt="" /> : <span className="top-thumb-fallback">V</span>}<div><strong>{video.title}</strong><p>{video.channel_title} · {video.content_type} · {video.format_type}{video.attributes.includes("聯動") ? " · 聯動" : ""}</p></div><div className="top-content-metric"><strong>{compact(video.view_count)}</strong><span>{percent(video.view_rate)} 觀看／訂閱</span></div></a>)}</div></article>
        <aside className="panel"><div className="panel-heading"><div><p className="section-kicker">ORGANIZATION VIEW</p><h2>組織／團體表現</h2></div><span>成員近期觀看中位數加總</span></div><div className="organization-bars">{trends.rankings.organizations.length === 0 ? <div className="insight-placeholder embedded">為更多頻道填入所屬組織後會顯示比較。</div> : trends.rankings.organizations.slice(0, 12).map((organization, index) => <div key={organization.organization_name}><span>{index + 1}</span><strong>{organization.organization_name}</strong><i><b style={{ width: `${organization.median_views_total / Math.max(1, trends.rankings.organizations[0].median_views_total) * 100}%` }} /></i><em>{compact(organization.median_views_total)} · {organization.members} 個頻道</em></div>)}</div></aside>
      </section>

      {trends.private_metrics && Object.values(trends.private_metrics).some((value) => value !== null) && <section className="panel private-trend-panel"><div className="panel-heading"><div><p className="section-kicker">PRIVATE STUDIO OVERLAY</p><h2>我的 Studio 私人指標</h2></div><span>只顯示自己的資料，不與公開頻道硬比</span></div><div className="private-metric-strip"><div><span>觀看</span><strong>{compact(trends.private_metrics.views)}</strong></div><div><span>觀看時數</span><strong>{exact(trends.private_metrics.watch_time_hours)}</strong></div><div><span>曝光</span><strong>{compact(trends.private_metrics.impressions)}</strong></div><div><span>點閱率</span><strong>{percent(trends.private_metrics.impressions_ctr)}</strong></div><div><span>回訪觀眾</span><strong>{compact(trends.private_metrics.returning_viewers)}</strong></div></div></section>}
    </>}
    <footer><span>台V Pulse · 趨勢圖表</span><span>圖表使用本機快照，不增加 YouTube API 配額</span></footer>
  </main>;
}
