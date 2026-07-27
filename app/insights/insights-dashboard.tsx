"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useCallback, useEffect, useMemo, useRef, useState, type FocusEvent, type PointerEvent as ReactPointerEvent } from "react";
import SiteHeader from "../site-header";

const API_BASE = process.env.NEXT_PUBLIC_TRACKER_API ?? "http://127.0.0.1:8787";

type Channel = {
  channel_id: string;
  title: string;
  handle: string | null;
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

type MetricValue = { median: number | null; p75: number | null };

type ChannelMetrics = {
  channel_id: string;
  title: string;
  thumbnail_url: string | null;
  category: string;
  subscriber_count: number | null;
  items: number;
  streams: number;
  weekly_frequency: number;
  average_duration_seconds: number | null;
  median_views: number | null;
  median_view_rate: number | null;
  median_peak_concurrent: number | null;
  median_ccv_rate: number | null;
  subscriber_growth: number | null;
  view_growth: number | null;
  snapshot_count: number;
};

type RankedVideo = {
  video_id: string;
  title: string;
  thumbnail_url: string | null;
  channel_id: string;
  channel_title: string;
  subscriber_count: number | null;
  view_count: number | null;
  view_rate: number | null;
  peak_concurrent: number | null;
  ccv_rate: number | null;
  content_type: string;
  attributes: string[];
  format_type: string;
  published_at: string | null;
};

type ContentBreakdown = {
  content_type: string;
  description: string;
  items: number;
  share: number;
  streams: number;
  median_views: number | null;
  median_view_rate: number | null;
  median_peak_concurrent: number | null;
  average_duration_seconds: number | null;
  representative_videos: RankedVideo[];
  is_attribute?: boolean;
};

type LandscapeFormat = "主要內容" | "直播" | "影片" | "Shorts" | "全部";

type Insights = {
  generated_at: string;
  period_days: number;
  filters: { min_subscribers: number; max_subscribers: number; category: string; include_graduated: boolean; channel_ids: string[] };
  overview: {
    channels: number;
    active_channels: number;
    total_subscribers: number;
    total_channel_views: number;
    recent_items: number;
    recent_streams: number;
    recent_shorts: number;
    live_hours: number;
    subscriber_growth: number;
    view_growth: number;
  };
  benchmarks: Record<string, MetricValue>;
  reference: ChannelMetrics | null;
  content_breakdown: ContentBreakdown[];
  content_landscapes: Record<LandscapeFormat, {
    items: number;
    content_breakdown: ContentBreakdown[];
    collaboration: ContentBreakdown;
  }>;
  format_breakdown: { format_type: string; items: number }[];
  schedule: { count: number; median_peak: number | null }[][];
  top_videos: RankedVideo[];
  top_videos_by_format: Record<"綜合" | "影片" | "直播" | "Shorts", RankedVideo[]>;
  top_channels: ChannelMetrics[];
  keywords: { keyword: string; count: number }[];
  coverage: {
    growth_channels: number;
    growth_percent: number;
    classified_items: number;
    classification_percent: number;
  };
};

const TIERS: Record<string, [number, number, string]> = {
  "1k-5k": [1000, 5000, "1,000～5,000"],
  "5k-10k": [5000, 10000, "5,000～10,000"],
  "10k-50k": [10000, 50000, "1 萬～5 萬"],
  "50k-100k": [50000, 100000, "5 萬～10 萬"],
  "100k+": [100000, 100000000, "10 萬以上"],
};

const DAYS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"];
const BLOCKS = ["00–04", "04–08", "08–12", "12–16", "16–20", "20–24"];

function compact(value: number | null | undefined, digits = 1) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: digits }).format(value);
}

function exact(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 1 }).format(value);
}

function percent(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(value < 10 ? 1 : 0)}%`;
}

function hours(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${(value / 3600).toFixed(1)} 小時`;
}

function signed(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${compact(value)}`;
}

export default function InsightsDashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);
  const [mode, setMode] = useState("all");
  const [tier, setTier] = useState("5k-10k");
  const [customMin, setCustomMin] = useState(1000);
  const [customMax, setCustomMax] = useState(5000);
  const [selectedChannelIds, setSelectedChannelIds] = useState<string[]>([]);
  const [savedGroups, setSavedGroups] = useState<{ name: string; ids: string[] }[]>([]);
  const [groupName, setGroupName] = useState("");
  const [category, setCategory] = useState("全部");
  const [referenceId, setReferenceId] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [includeGraduated, setIncludeGraduated] = useState(false);
  const [contentFormat, setContentFormat] = useState<"綜合" | "影片" | "直播" | "Shorts">("影片");
  const [landscapeFormat, setLandscapeFormat] = useState<LandscapeFormat>("主要內容");
  const [representativePopover, setRepresentativePopover] = useState<{
    item: ContentBreakdown;
    x: number;
    y: number;
    locked: boolean;
  } | null>(null);
  const insightsRef = useRef<Insights | null>(null);
  const ownedDefaultApplied = useRef(false);
  const closePopoverTimer = useRef<number | null>(null);

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
    return () => window.clearInterval(timer);
  }, [loadSummary]);

  useEffect(() => {
    const timer = window.setInterval(() => setRefreshKey((value) => value + 1), 300000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const saved = window.localStorage.getItem("tai-v-pulse-efficiency-format");
    if (saved && ["綜合", "影片", "直播", "Shorts"].includes(saved)) {
      setContentFormat(saved as "綜合" | "影片" | "直播" | "Shorts");
    }
    const savedLandscape = window.localStorage.getItem("tai-v-pulse-landscape-format");
    if (savedLandscape && ["主要內容", "直播", "影片", "Shorts", "全部"].includes(savedLandscape)) {
      setLandscapeFormat(savedLandscape as LandscapeFormat);
    }
    try {
      const groups = JSON.parse(window.localStorage.getItem("tai-v-pulse-comparison-groups") ?? "[]") as { name: string; ids: string[] }[];
      if (Array.isArray(groups)) setSavedGroups(groups.filter((group) => group.name && Array.isArray(group.ids)));
    } catch {
      setSavedGroups([]);
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem("tai-v-pulse-efficiency-format", contentFormat);
  }, [contentFormat]);

  useEffect(() => {
    window.localStorage.setItem("tai-v-pulse-landscape-format", landscapeFormat);
    setRepresentativePopover(null);
  }, [landscapeFormat]);

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
  const cohortRange = useMemo(() => {
    if (mode === "relative" && referenceSubscribers) {
      return [Math.max(1, Math.floor(referenceSubscribers * .5)), Math.ceil(referenceSubscribers * 2)] as const;
    }
    if (mode === "tier") return [TIERS[tier][0], TIERS[tier][1]] as const;
    if (mode === "range") return [Math.max(0, customMin), Math.max(customMin, customMax)] as const;
    return [summary?.settings.min_subscribers ?? 0, 100000000] as const;
  }, [customMax, customMin, mode, referenceSubscribers, summary?.settings.min_subscribers, tier]);

  const selectedChannels = selectedChannelIds
    .map((channelId) => summary?.channels.find((channel) => channel.channel_id === channelId))
    .filter((channel): channel is Channel => Boolean(channel));

  function addSelectedChannel(channelId: string) {
    if (!channelId || channelId === referenceId) return;
    setSelectedChannelIds((current) => [...new Set([...current, channelId])].slice(0, 5));
  }

  function saveComparisonGroup() {
    const name = groupName.trim();
    if (!name || selectedChannelIds.length === 0) return;
    const next = [...savedGroups.filter((group) => group.name !== name), { name, ids: selectedChannelIds }];
    setSavedGroups(next);
    window.localStorage.setItem("tai-v-pulse-comparison-groups", JSON.stringify(next));
    setGroupName("");
  }

  function popoverPosition(clientX: number, clientY: number) {
    const width = Math.min(430, window.innerWidth - 24);
    const height = 360;
    const x = clientX + 16 + width > window.innerWidth - 12
      ? Math.max(12, clientX - width - 16)
      : clientX + 16;
    const y = clientY + 14 + height > window.innerHeight - 12
      ? Math.max(12, clientY - height - 14)
      : clientY + 14;
    return { x, y };
  }

  function cancelPopoverClose() {
    if (closePopoverTimer.current !== null) {
      window.clearTimeout(closePopoverTimer.current);
      closePopoverTimer.current = null;
    }
  }

  function schedulePopoverClose() {
    cancelPopoverClose();
    closePopoverTimer.current = window.setTimeout(() => {
      setRepresentativePopover((current) => current?.locked ? current : null);
    }, 140);
  }

  function openPopover(item: ContentBreakdown, clientX: number, clientY: number, locked = false) {
    cancelPopoverClose();
    setRepresentativePopover({ item, ...popoverPosition(clientX, clientY), locked });
  }

  function handleCardPointer(event: ReactPointerEvent<HTMLElement>, item: ContentBreakdown) {
    if (event.pointerType !== "mouse") return;
    setRepresentativePopover((current) => {
      if (current?.locked) return current;
      return { item, ...popoverPosition(event.clientX, event.clientY), locked: false };
    });
  }

  function handleCardFocus(event: FocusEvent<HTMLElement>, item: ContentBreakdown) {
    const rect = event.currentTarget.getBoundingClientRect();
    openPopover(item, rect.right, rect.top + 24);
  }

  const selectedChannelKey = selectedChannelIds.join(",");

  useEffect(() => {
    if (!summaryReady || (mode === "relative" && !referenceAvailable) || (mode === "channels" && !selectedChannelKey)) {
      setInsights(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const params = new URLSearchParams({
      days: String(days),
      min_subscribers: String(cohortRange[0]),
      max_subscribers: String(cohortRange[1]),
      category,
      include_graduated: String(includeGraduated),
    });
    if (referenceId) params.set("reference_channel_id", referenceId);
    if (mode === "channels" && selectedChannelKey) params.set("channel_ids", selectedChannelKey);
    if (insightsRef.current) setRefreshing(true);
    else setLoading(true);
    setError(null);
    fetch(`${API_BASE}/api/insights?${params}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const payload = await response.json() as Insights & { error?: string };
        if (!response.ok) throw new Error(payload.error ?? "無法載入分析資料");
        insightsRef.current = payload;
        setInsights(payload);
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "無法載入分析資料");
      })
      .finally(() => { setLoading(false); setRefreshing(false); });
    return () => controller.abort();
  }, [category, cohortRange, days, includeGraduated, mode, referenceAvailable, referenceId, refreshKey, selectedChannelKey, summaryReady]);

  const cohortLabel = mode === "relative"
    ? reference ? `${reference.title} 的 0.5～2 倍訂閱` : "請選擇參考頻道"
    : mode === "tier" ? `${TIERS[tier][2]} 訂閱`
      : mode === "range" ? `${exact(cohortRange[0])}～${exact(cohortRange[1])} 訂閱`
        : mode === "channels" ? `${selectedChannelIds.length} 個指定頻道` : "全部已收錄頻道";
  const heatMax = Math.max(1, ...(insights?.schedule.flat().map((cell) => cell.count) ?? [1]));
  const activeLandscape = insights?.content_landscapes?.[landscapeFormat];
  const landscapeItems = activeLandscape
    ? [...activeLandscape.content_breakdown, ...(activeLandscape.collaboration.items ? [activeLandscape.collaboration] : [])]
    : insights?.content_breakdown ?? [];
  const contentMax = Math.max(1, ...(landscapeItems.map((item) => item.items) ?? [1]));
  const efficientVideos = insights?.top_videos_by_format?.[contentFormat] ?? [];

  const benchmarkRows = [
    ["weekly_frequency", "每週內容數", (value: number | null) => value === null ? "—" : `${value.toFixed(1)} 個`],
    ["average_duration_seconds", "平均內容長度", hours],
    ["median_views", "內容觀看中位數", compact],
    ["median_view_rate", "觀看／訂閱比", percent],
    ["median_peak_concurrent", "直播同接中位數", compact],
    ["median_ccv_rate", "同接／訂閱比", percent],
    ["subscriber_growth", `${days} 日訂閱成長`, signed],
    ["view_growth", `${days} 日觀看成長`, signed],
  ] as const;

  return (
    <main className="app-shell insights-shell">
      <SiteHeader
        active="insights"
        eyebrow="CONTENT LANDSCAPE"
        title="內容環境"
        connected={connected}
        statusText={refreshing ? "背景更新中，閱讀位置會保留" : insights ? `分析更新 ${new Intl.DateTimeFormat("zh-TW", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Taipei" }).format(new Date(insights.generated_at))}` : undefined}
        actions={<button className="button ghost" type="button" onClick={() => setRefreshKey((value) => value + 1)} disabled={!connected || loading || refreshing}>{refreshing ? "更新中…" : "更新分析"}</button>}
      />

      {!connected && <section className="notice warning"><span className="notice-icon">!</span><div><strong>資料服務尚未啟動</strong><p>啟動台V Pulse 後，這個頁面會自動讀取已收錄資料。</p></div></section>}
      {error && <section className="inline-message">{error}</section>}

      <section className="panel insight-controls">
        <div className="control-intro"><p className="section-kicker">COHORT BUILDER</p><h2>選擇要觀察的頻道環境</h2><p>用相同量級、分類與期間比較內容策略，避免被大型頻道的數字干擾。</p></div>
        <div className="control-grid">
          <label><span>期間</span><select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={7}>最近 7 天</option><option value={30}>最近 30 天</option><option value={90}>最近 90 天</option></select></label>
          <label><span>比較群組</span><select value={mode} onChange={(event) => setMode(event.target.value)}><option value="all">全體已收錄頻道</option><option value="relative">參考頻道的 0.5～2 倍</option><option value="tier">固定訂閱級距</option><option value="range">自訂訂閱範圍</option><option value="channels">指定頻道</option></select></label>
          {mode === "tier" && <label><span>訂閱級距</span><select value={tier} onChange={(event) => setTier(event.target.value)}>{Object.entries(TIERS).map(([value, item]) => <option value={value} key={value}>{item[2]}</option>)}</select></label>}
          {mode === "range" && <div className="range-controls"><label><span>最低訂閱</span><input type="number" min={0} value={customMin} onChange={(event) => setCustomMin(Number(event.target.value))} /></label><label><span>最高訂閱</span><input type="number" min={customMin} value={customMax} onChange={(event) => setCustomMax(Number(event.target.value))} /></label></div>}
          {mode === "channels" && <div className="channel-group-builder"><label><span>加入比較頻道（最多 5 個）</span><select value="" onChange={(event) => addSelectedChannel(event.target.value)}><option value="">選擇頻道…</option>{summary?.channels.filter((channel) => channel.channel_id !== referenceId && !selectedChannelIds.includes(channel.channel_id)).map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.title}｜{compact(channel.subscriber_count)}</option>)}</select></label><div className="selected-channel-chips">{selectedChannels.map((channel) => <button type="button" onClick={() => setSelectedChannelIds((current) => current.filter((id) => id !== channel.channel_id))} key={channel.channel_id}>{channel.title}<span>×</span></button>)}</div><div className="save-group-row"><input value={groupName} onChange={(event) => setGroupName(event.target.value)} placeholder="比較組合名稱" /><button type="button" onClick={saveComparisonGroup} disabled={!groupName.trim() || selectedChannelIds.length === 0}>儲存</button>{savedGroups.length > 0 && <select value="" onChange={(event) => { const group = savedGroups.find((item) => item.name === event.target.value); if (group) setSelectedChannelIds(group.ids.slice(0, 5)); }}><option value="">載入已存組合…</option>{savedGroups.map((group) => <option value={group.name} key={group.name}>{group.name}</option>)}</select>}</div></div>}
          <label><span>頻道分類</span><select value={category} onChange={(event) => setCategory(event.target.value)}><option>全部</option>{summary?.categories.map((item) => <option value={item.category} key={item.category}>{item.category}（{item.channel_count}）</option>)}</select></label>
          <label className="reference-control"><span>參考頻道（用於個別比較）</span><select value={referenceId} onChange={(event) => setReferenceId(event.target.value)}><option value="">不比較單一頻道</option>{summary?.owned_channel && !summary.channels.some((channel) => channel.channel_id === summary.owned_channel?.channel_id) && <option value={summary.owned_channel.channel_id}>我的頻道：{summary.owned_channel.title}｜{compact(summary.owned_channel.subscriber_count)} 訂閱</option>}{summary?.channels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.title}｜{compact(channel.subscriber_count)} 訂閱</option>)}</select></label>
          <label className="graduated-toggle"><input type="checkbox" checked={includeGraduated} onChange={(event) => setIncludeGraduated(event.target.checked)} /><span>包含已確認畢業頻道</span></label>
        </div>
        <div className="cohort-summary"><span>{summary?.owned_channel_id && referenceId === summary.owned_channel_id ? "以我的頻道為基準" : "目前群組"}</span><strong>{cohortLabel}</strong><small>{mode === "channels" ? "基準頻道不納入同級中位數" : `${exact(cohortRange[0])}～${cohortRange[1] >= 100000000 ? "不限上限" : exact(cohortRange[1])} 訂閱`}{includeGraduated ? " · 包含畢業頻道" : ""}</small></div>
      </section>

      {mode === "relative" && !reference && <section className="panel insight-placeholder"><strong>先選擇參考頻道</strong><p>系統會自動建立訂閱數為該頻道 0.5～2 倍的比較群組。</p></section>}
      {mode === "channels" && selectedChannelIds.length === 0 && <section className="panel insight-placeholder"><strong>先加入比較頻道</strong><p>最多可指定五個頻道，基準頻道會另外顯示而不影響群組中位數。</p></section>}
      {loading && <section className="panel insight-placeholder">正在整理內容環境…</section>}

      {!loading && insights && (
        <>
          <section className="insight-hero-grid">
            <article className="insight-hero primary"><span>群組頻道</span><strong>{insights.overview.channels}</strong><p>{insights.overview.active_channels} 個在期間內有新內容</p></article>
            <article className="insight-hero"><span>近期內容</span><strong>{compact(insights.overview.recent_items)}</strong><p>{insights.overview.recent_streams} 場直播 · {insights.overview.recent_shorts} 支 Shorts</p></article>
            <article className="insight-hero"><span>直播總時數</span><strong>{insights.overview.live_hours.toFixed(1)}</strong><p>最近 {days} 天已蒐集直播</p></article>
            <article className="insight-hero"><span>訂閱成長</span><strong>{signed(insights.overview.subscriber_growth)}</strong><p>{insights.coverage.growth_channels}/{insights.overview.channels} 個頻道具備足夠快照</p></article>
            <article className="insight-hero"><span>觀看成長</span><strong>{signed(insights.overview.view_growth)}</strong><p>頻道累積觀看差值</p></article>
            <article className="insight-hero"><span>群組累積觀看</span><strong>{compact(insights.overview.total_channel_views)}</strong><p>目前公開頻道觀看總和</p></article>
          </section>

          <section className="insight-two-column">
            <article className="panel benchmark-panel">
              <div className="panel-heading"><div><p className="section-kicker">PEER BENCHMARK</p><h2>同級表現基準</h2></div><span>{reference ? `比較：${reference.title}` : "選參考頻道即可加入個別比較"}</span></div>
              <div className="table-wrap"><table><thead><tr><th>指標</th>{reference && <th>{reference.title}</th>}<th>同級中位數</th><th>同級前 25%</th></tr></thead><tbody>{benchmarkRows.map(([key, label, formatter]) => <tr key={key}><td><strong>{label}</strong></td>{reference && <td className="reference-value">{formatter(insights.reference?.[key] as number | null)}</td>}<td>{formatter(insights.benchmarks[key]?.median ?? null)}</td><td>{formatter(insights.benchmarks[key]?.p75 ?? null)}</td></tr>)}</tbody></table></div>
              <p className="panel-footnote">觀看／訂閱比使用目前公開訂閱數計算，適合比較量級，不代表不重複觀眾。</p>
            </article>

            <aside className="panel format-panel">
              <div className="panel-heading"><div><p className="section-kicker">FORMAT MIX</p><h2>內容形式</h2></div></div>
              <div className="format-donuts">{insights.format_breakdown.map((item) => { const share = insights.overview.recent_items ? item.items / insights.overview.recent_items * 100 : 0; return <div className="format-stat" key={item.format_type}><span>{item.format_type}</span><strong>{item.items}</strong><div><i style={{ width: `${share}%` }} /></div><small>{share.toFixed(1)}%</small></div>; })}</div>
              <div className="coverage-card"><span>資料完整度</span><strong>{percent(insights.coverage.classification_percent)}</strong><p>{insights.coverage.classified_items} 個內容已辨識主題；「其他」可隨未來規則持續改善。</p></div>
            </aside>
          </section>

          <section className="panel content-landscape-panel">
            <div className="panel-heading efficiency-heading"><div><p className="section-kicker">CONTENT LANDSCAPE</p><h2>大家都在做什麼</h2></div><div className="format-tabs landscape-tabs" role="group" aria-label="內容環境形式">{(["主要內容", "直播", "影片", "Shorts", "全部"] as const).map((format) => <button className={landscapeFormat === format ? "active" : ""} type="button" onClick={() => setLandscapeFormat(format)} aria-pressed={landscapeFormat === format} key={format}>{format === "主要內容" ? "直播＋一般影片" : format === "影片" ? "一般影片" : format}</button>)}</div></div>
            <p className="efficiency-explainer">主題、影片形式與聯動屬性分開判斷；移動滑鼠時代表內容會跟隨游標，點一下可固定。現在顯示 {activeLandscape?.items ?? 0} 項{landscapeFormat === "主要內容" ? "非 Shorts 內容" : landscapeFormat}。</p>
            <div className="content-landscape-grid">{landscapeItems.length === 0 ? <div className="insight-placeholder embedded">目前期間內尚無內容資料。</div> : landscapeItems.map((item) => {
              const strong = item.median_view_rate !== null && item.median_view_rate >= (insights.benchmarks.median_view_rate?.p75 ?? Infinity);
              const opportunity = strong && item.share < 15;
              const expanded = representativePopover?.item.content_type === item.content_type;
              return <article className={`content-type-card${expanded ? " open" : ""}${item.is_attribute ? " attribute-card" : ""}`} key={`${item.content_type}-${item.is_attribute ? "attribute" : "topic"}`} onPointerEnter={(event) => handleCardPointer(event, item)} onPointerMove={(event) => handleCardPointer(event, item)} onPointerLeave={schedulePopoverClose} onFocus={(event) => handleCardFocus(event, item)} onBlur={schedulePopoverClose}><button className="content-card-trigger" type="button" aria-expanded={expanded} onClick={(event) => { const rect = event.currentTarget.getBoundingClientRect(); if (representativePopover?.locked && representativePopover.item.content_type === item.content_type) setRepresentativePopover(null); else openPopover(item, rect.right, rect.top + 30, true); }}><div className="content-type-heading"><strong>{item.content_type}</strong>{item.is_attribute ? <span className="attribute-label">附加標籤</span> : opportunity ? <span className="opportunity">低占比高表現</span> : strong ? <span>表現突出</span> : null}</div><p className="content-definition">{item.description}</p><div className="content-share-track"><i style={{ width: `${item.items / contentMax * 100}%` }} /></div><dl><div><dt>內容數</dt><dd>{item.items}（{item.share.toFixed(1)}%）</dd></div><div><dt>觀看中位數</dt><dd>{compact(item.median_views)}</dd></div><div><dt>觀看／訂閱</dt><dd>{percent(item.median_view_rate)}</dd></div><div><dt>直播同接中位數</dt><dd>{compact(item.median_peak_concurrent)}</dd></div></dl></button></article>;
            })}</div>
            {representativePopover && <div className={`content-representatives floating${representativePopover.locked ? " locked" : ""}`} style={{ left: representativePopover.x, top: representativePopover.y }} role="dialog" aria-label={`${representativePopover.item.content_type}代表內容`} onPointerEnter={cancelPopoverClose} onPointerLeave={schedulePopoverClose}><div><strong>{representativePopover.item.content_type}代表內容</strong><span>每個頻道最多一項</span>{representativePopover.locked && <button type="button" onClick={() => setRepresentativePopover(null)} aria-label="關閉代表內容">×</button>}</div>{representativePopover.item.representative_videos.length === 0 ? <p>目前沒有足夠資料。</p> : representativePopover.item.representative_videos.map((video, index) => <a href={`https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer" key={video.video_id}><b>{index + 1}</b>{video.thumbnail_url ? <img src={video.thumbnail_url} alt="" /> : <i>V</i>}<span><strong>{video.title}</strong><small>{video.channel_title} · <em>{video.format_type}</em>{video.attributes.includes("聯動") ? " · 聯動" : ""} · {compact(video.view_count)} 觀看 · {percent(video.view_rate)}</small></span></a>)}</div>}
          </section>

          <section className="insight-two-column schedule-layout">
            <article className="panel schedule-panel">
              <div className="panel-heading"><div><p className="section-kicker">LIVE SCHEDULE</p><h2>直播時段熱圖</h2></div><span>台北時間 · 顏色越深代表開台越集中</span></div>
              <div className="heatmap" role="img" aria-label="一週直播開台時段熱圖"><div className="heatmap-corner" />{BLOCKS.map((block) => <span className="heatmap-header" key={block}>{block}</span>)}{insights.schedule.map((row, day) => <div className="heatmap-row" key={DAYS[day]}><strong>{DAYS[day]}</strong>{row.map((cell, block) => <div className="heat-cell" key={block} style={{ backgroundColor: `color-mix(in srgb, var(--mint) ${8 + cell.count / heatMax * 82}%, transparent)` }} title={`${DAYS[day]} ${BLOCKS[block]}：${cell.count} 場，最高同接中位數 ${compact(cell.median_peak)}`}><span>{cell.count || ""}</span></div>)}</div>)}</div>
              <p className="panel-footnote">這表示同級頻道何時集中開台，不代表觀眾彼此重疊。</p>
            </article>

            <aside className="panel keyword-panel">
              <div className="panel-heading"><div><p className="section-kicker">RECENT SIGNALS</p><h2>近期標籤與主題</h2></div></div>
              <div className="keyword-cloud">{insights.keywords.length === 0 ? <p>後續影片掃描取得標籤後會顯示。</p> : insights.keywords.map((item, index) => <span key={item.keyword} style={{ fontSize: `${10 + Math.max(0, 7 - index) * .7}px` }}>{item.keyword}<small>{item.count}</small></span>)}</div>
              <p className="panel-footnote">來源為公開影片標籤與標題中的主題標籤，不會讀取私人 Analytics。</p>
            </aside>
          </section>

          <section className="insight-two-column leader-layout">
            <article className="panel top-content-panel">
              <div className="panel-heading efficiency-heading"><div><p className="section-kicker">CONTENT EXAMPLES</p><h2>同級高效率內容</h2></div><div className="format-tabs" role="group" aria-label="高效率內容形式">{(["影片", "直播", "Shorts", "綜合"] as const).map((format) => <button className={contentFormat === format ? "active" : ""} type="button" onClick={() => setContentFormat(format)} aria-pressed={contentFormat === format} key={format}>{format === "影片" ? "一般影片" : format}</button>)}</div></div>
              <p className="efficiency-explainer">{contentFormat === "直播" ? "直播依最高同接／訂閱比排序；沒有同接樣本的直播會排在後方。" : `${contentFormat === "綜合" ? "綜合內容" : contentFormat}依觀看／訂閱比排序。`} 每個頻道先取表現最好的一項，避免同一頻道占滿榜單。</p>
              <div className="top-content-list">{efficientVideos.length === 0 ? <div className="insight-placeholder embedded">目前期間內沒有{contentFormat === "綜合" ? "近期內容" : contentFormat}資料。</div> : efficientVideos.map((video, index) => <a href={`https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer" className="top-content-row" key={video.video_id}><span className="rank">{index + 1}</span>{video.thumbnail_url ? <img src={video.thumbnail_url} alt="" /> : <span className="top-thumb-fallback">V</span>}<div><strong>{video.title}</strong><p>{video.channel_title} · {video.content_type} · {video.format_type}</p></div><div className="top-content-metric"><strong>{contentFormat === "直播" ? percent(video.ccv_rate) : percent(video.view_rate)}</strong><span>{contentFormat === "直播" ? `${compact(video.peak_concurrent)} 最高同接` : `${compact(video.view_count)} 觀看`}</span></div></a>)}</div>
            </article>

            <aside className="panel top-channel-panel">
              <div className="panel-heading"><div><p className="section-kicker">PEER EXAMPLES</p><h2>同級頻道案例</h2></div><span>近期觀看效率</span></div>
              <div className="peer-list">{insights.top_channels.slice(0, 8).map((channel, index) => <div className="peer-row" key={channel.channel_id}><span>{index + 1}</span>{channel.thumbnail_url ? <img src={channel.thumbnail_url} alt="" /> : <i>V</i>}<div><strong>{channel.title}</strong><p>每週 {channel.weekly_frequency.toFixed(1)} 個 · {compact(channel.median_views)} 觀看中位數</p></div><b>{percent(channel.median_view_rate)}</b></div>)}</div>
            </aside>
          </section>

          <section className="analysis-note"><strong>如何閱讀這一頁</strong><p>先用參考頻道建立同量級群組，再看內容占比、觀看／訂閱比和開台時段。資料量少時不要急著下結論；建議至少累積 30 天，成長比較則需每個頻道至少兩筆快照。</p></section>
        </>
      )}

      <footer><span>台V Pulse · 內容環境</span><span>所有分析都在你的電腦完成</span></footer>
    </main>
  );
}
