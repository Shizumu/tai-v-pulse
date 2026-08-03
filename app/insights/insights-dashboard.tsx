"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useCallback, useEffect, useMemo, useRef, useState, type FocusEvent, type FormEvent, type PointerEvent as ReactPointerEvent } from "react";
import LegalFooter from "../legal-footer";
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
  median_average_concurrent: number | null;
  median_peak_concurrent: number | null;
  median_sustained_ccv_rate: number | null;
  median_ccv_rate: number | null;
  concurrency_covered_streams: number;
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
  average_concurrent: number | null;
  peak_concurrent: number | null;
  concurrency_sample_count: number;
  concurrency_coverage: number | null;
  concurrency_ready: boolean;
  sustained_ccv_rate: number | null;
  ccv_rate: number | null;
  content_type: string;
  topics: string[];
  attributes: string[];
  classification_source: string;
  classification_evidence: string;
  game_name: string;
  format_type: string;
  published_at: string | null;
  peer_rank?: number;
  comparison_count?: number;
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

type ScheduleStream = {
  video_id: string;
  title: string;
  channel_id: string;
  channel_title: string;
  started_at: string;
  peak_concurrent: number | null;
};

type ScheduleCell = { count: number; median_peak: number | null; streams: ScheduleStream[] };

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
  schedule: ScheduleCell[][];
  schedule_summary: {
    date_start: string;
    date_end: string;
    channel_count: number;
    stream_count: number;
    conclusion: string;
  };
  classification_guide: { priority: string; representative_ranking: string };
  top_videos: RankedVideo[];
  top_videos_by_format: Record<"綜合" | "影片" | "直播" | "Shorts", RankedVideo[]>;
  reference_top_videos_by_format: Record<"綜合" | "影片" | "直播" | "Shorts", RankedVideo | null>;
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
const TOPIC_OPTIONS = ["紀念／重大活動", "ASMR", "歌回", "音樂作品", "雜談", "遊戲"];

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

function perHundred(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(value >= 10 ? 1 : 2)} 人／百訂閱`;
}

function hours(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${(value / 3600).toFixed(1)} 小時`;
}

function signed(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${compact(value)}`;
}

function calendarDate(value: string) {
  const [year, month, day] = value.split("-");
  return year && month && day ? `${year}/${month}/${day}` : value;
}

function streamTime(value: string) {
  return new Intl.DateTimeFormat("zh-TW", {
    month: "numeric", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit",
    timeZone: "Asia/Taipei",
  }).format(new Date(value));
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
  const [selectedScheduleCell, setSelectedScheduleCell] = useState<{ day: number; block: number } | null>(null);
  const [classificationVideo, setClassificationVideo] = useState<RankedVideo | null>(null);
  const [classificationTopics, setClassificationTopics] = useState<string[]>([]);
  const [classificationGame, setClassificationGame] = useState("");
  const [classificationNote, setClassificationNote] = useState("");
  const [classificationSaving, setClassificationSaving] = useState(false);
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

  function openClassification(video: RankedVideo) {
    setRepresentativePopover(null);
    setClassificationVideo(video);
    setClassificationTopics(
      (video.topics?.length ? video.topics : video.content_type.split(" + "))
        .filter((topic) => TOPIC_OPTIONS.includes(topic)),
    );
    setClassificationGame(video.game_name ?? "");
    setClassificationNote("");
    setError(null);
  }

  function toggleClassificationTopic(topic: string) {
    setClassificationTopics((current) => current.includes(topic)
      ? current.filter((value) => value !== topic)
      : [...current, topic]);
  }

  async function saveClassification(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!classificationVideo) return;
    setClassificationSaving(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/content-classification`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_id: classificationVideo.video_id,
          topics: classificationTopics,
          game_name: classificationGame,
          note: classificationNote,
        }),
      });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法儲存內容分類");
      setClassificationVideo(null);
      setRefreshKey((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法儲存內容分類");
    } finally {
      setClassificationSaving(false);
    }
  }

  async function resetClassification() {
    if (!classificationVideo) return;
    setClassificationSaving(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/content-classification/${encodeURIComponent(classificationVideo.video_id)}`, {
        method: "DELETE",
      });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法改回自動分類");
      setClassificationVideo(null);
      setRefreshKey((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法改回自動分類");
    } finally {
      setClassificationSaving(false);
    }
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
  const selectedSchedule = selectedScheduleCell && insights
    ? insights.schedule[selectedScheduleCell.day]?.[selectedScheduleCell.block] ?? null
    : null;
  const activeLandscape = insights?.content_landscapes?.[landscapeFormat];
  const landscapeItems = activeLandscape
    ? [...activeLandscape.content_breakdown, ...(activeLandscape.collaboration.items ? [activeLandscape.collaboration] : [])]
    : insights?.content_breakdown ?? [];
  const contentMax = Math.max(1, ...(landscapeItems.map((item) => item.items) ?? [1]));
  const efficientVideos = insights?.top_videos_by_format?.[contentFormat] ?? [];
  const referenceEfficientVideo = insights?.reference_top_videos_by_format?.[contentFormat] ?? null;
  const growthCoverageText = insights && insights.coverage.growth_channels > 0
    ? `以 ${insights.coverage.growth_channels}/${insights.overview.channels} 個已有期間起點資料的頻道加總`
    : `尚未累積到 ${days} 天前的頻道資料；累積後顯示`;

  const benchmarkRows = [
    ["weekly_frequency", "每週內容數", (value: number | null) => value === null ? "—" : `${value.toFixed(1)} 個`],
    ["average_duration_seconds", "平均內容長度", hours],
    ["median_views", "內容觀看中位數", compact],
    ["median_view_rate", "觀看／訂閱比", percent],
    ["median_average_concurrent", "直播平均同接中位數", compact],
    ["median_peak_concurrent", "直播峰值同接中位數", compact],
    ["median_sustained_ccv_rate", "直播持續動員", perHundred],
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
            <article className="insight-hero"><span>訂閱成長</span><strong>{signed(insights.coverage.growth_channels > 0 ? insights.overview.subscriber_growth : null)}</strong><p>{growthCoverageText}</p></article>
            <article className="insight-hero"><span>觀看成長</span><strong>{signed(insights.coverage.growth_channels > 0 ? insights.overview.view_growth : null)}</strong><p>{growthCoverageText}</p></article>
            <article className="insight-hero"><span>群組累積觀看</span><strong>{compact(insights.overview.total_channel_views)}</strong><p>目前公開頻道觀看總和</p></article>
          </section>

          <section className="insight-two-column">
            <article className="panel benchmark-panel">
              <div className="panel-heading"><div><p className="section-kicker">PEER BENCHMARK</p><h2>同級表現基準</h2></div><span>{reference ? `比較：${reference.title}` : "選參考頻道即可加入個別比較"}</span></div>
              <div className="table-wrap"><table><thead><tr><th>指標</th>{reference && <th>{reference.title}</th>}<th>同級中位數</th><th>同級前 25%</th></tr></thead><tbody>{benchmarkRows.map(([key, label, formatter]) => <tr key={key}><td><strong>{label}</strong>{(key === "subscriber_growth" || key === "view_growth") && insights.coverage.growth_channels < insights.overview.channels && <small className="benchmark-metric-note">{growthCoverageText}</small>}</td>{reference && <td className="reference-value">{formatter(insights.reference?.[key] as number | null)}</td>}<td>{formatter(insights.benchmarks[key]?.median ?? null)}</td><td>{formatter(insights.benchmarks[key]?.p75 ?? null)}</td></tr>)}</tbody></table></div>
              <p className="panel-footnote">觀看／訂閱比使用目前公開訂閱數計算；直播持續動員是完整取樣場次的平均同接／訂閱，以每百位訂閱可持續留下幾位觀眾呈現。兩者都不是不重複觀眾或官方留存率。</p>
            </article>

            <aside className="panel format-panel">
              <div className="panel-heading"><div><p className="section-kicker">FORMAT MIX</p><h2>內容形式</h2></div></div>
              <div className="format-donuts">{insights.format_breakdown.map((item) => { const share = insights.overview.recent_items ? item.items / insights.overview.recent_items * 100 : 0; return <div className="format-stat" key={item.format_type}><span>{item.format_type}</span><strong>{item.items}</strong><div><i style={{ width: `${share}%` }} /></div><small>{share.toFixed(1)}%</small></div>; })}</div>
              <div className="coverage-card"><span>資料完整度</span><strong>{percent(insights.coverage.classification_percent)}</strong><p>{insights.coverage.classified_items} 個內容已辨識主題；「其他」可隨未來規則持續改善。</p></div>
            </aside>
          </section>

          <section className="panel content-landscape-panel">
            <div className="panel-heading efficiency-heading"><div><p className="section-kicker">CONTENT LANDSCAPE</p><h2>大家都在做什麼</h2></div><div className="format-tabs landscape-tabs" role="group" aria-label="內容環境形式">{(["主要內容", "直播", "影片", "Shorts", "全部"] as const).map((format) => <button className={landscapeFormat === format ? "active" : ""} type="button" onClick={() => setLandscapeFormat(format)} aria-pressed={landscapeFormat === format} key={format}>{format === "主要內容" ? "直播＋一般影片" : format === "影片" ? "一般影片" : format}</button>)}</div></div>
            <p className="efficiency-explainer">主題、影片形式與聯動屬性分開判斷；混合主題會以「歌回 + 雜談」呈現，但每筆內容在總數只計一次。現在顯示 {activeLandscape?.items ?? 0} 項{landscapeFormat === "主要內容" ? "非 Shorts 內容" : landscapeFormat}。</p>
            <p className="classification-guide"><strong>分類依據：</strong>{insights.classification_guide.priority}<br /><strong>代表內容排序：</strong>{insights.classification_guide.representative_ranking}</p>
            <div className="content-landscape-grid">{landscapeItems.length === 0 ? <div className="insight-placeholder embedded">目前期間內尚無內容資料。</div> : landscapeItems.map((item) => {
              const strong = item.median_view_rate !== null && item.median_view_rate >= (insights.benchmarks.median_view_rate?.p75 ?? Infinity);
              const opportunity = strong && item.share < 15;
              const expanded = representativePopover?.item.content_type === item.content_type;
              return <article className={`content-type-card${expanded ? " open" : ""}${item.is_attribute ? " attribute-card" : ""}`} key={`${item.content_type}-${item.is_attribute ? "attribute" : "topic"}`} onPointerEnter={(event) => handleCardPointer(event, item)} onPointerMove={(event) => handleCardPointer(event, item)} onPointerLeave={schedulePopoverClose} onFocus={(event) => handleCardFocus(event, item)} onBlur={schedulePopoverClose}><button className="content-card-trigger" type="button" aria-expanded={expanded} onClick={(event) => { const rect = event.currentTarget.getBoundingClientRect(); if (representativePopover?.locked && representativePopover.item.content_type === item.content_type) setRepresentativePopover(null); else openPopover(item, rect.right, rect.top + 30, true); }}><div className="content-type-heading"><strong>{item.content_type}</strong>{item.is_attribute ? <span className="attribute-label">附加標籤</span> : opportunity ? <span className="opportunity">低占比高表現</span> : strong ? <span>表現突出</span> : null}</div><p className="content-definition">{item.description}</p><div className="content-share-track"><i style={{ width: `${item.items / contentMax * 100}%` }} /></div><dl><div><dt>內容數</dt><dd>{item.items}（{item.share.toFixed(1)}%）</dd></div><div><dt>觀看中位數</dt><dd>{compact(item.median_views)}{item.median_views === null && <small className="metric-missing">尚無可用觀看資料</small>}</dd></div><div><dt>觀看／訂閱</dt><dd>{percent(item.median_view_rate)}{item.median_view_rate === null && <small className="metric-missing">缺少觀看或訂閱資料</small>}</dd></div><div><dt>直播同接中位數</dt><dd>{compact(item.median_peak_concurrent)}{item.median_peak_concurrent === null && <small className="metric-missing">尚無直播同接樣本</small>}</dd></div></dl></button></article>;
            })}</div>
            {representativePopover && <div className={`content-representatives floating${representativePopover.locked ? " locked" : ""}`} style={{ left: representativePopover.x, top: representativePopover.y }} role="dialog" aria-label={`${representativePopover.item.content_type}代表內容`} onPointerEnter={cancelPopoverClose} onPointerLeave={schedulePopoverClose}>
              <div className="representative-heading"><strong>{representativePopover.item.content_type}代表內容</strong><span>觀看／訂閱比排序 · 每頻道一項</span>{representativePopover.locked && <button type="button" onClick={() => setRepresentativePopover(null)} aria-label="關閉代表內容">×</button>}</div>
              {representativePopover.item.representative_videos.length === 0 ? <p>目前沒有足夠資料。</p> : representativePopover.item.representative_videos.map((video, index) => <div className="representative-row" key={video.video_id}>
                <a href={`https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer"><b>{index + 1}</b>{video.thumbnail_url ? <img src={video.thumbnail_url} alt="" /> : <i>V</i>}<span><strong>{video.title}</strong><small>{video.channel_title} · <em>{video.content_type}</em>{video.game_name ? ` · ${video.game_name}` : ""}{video.attributes.includes("聯動") ? " · 聯動" : ""} · {compact(video.view_count)} 觀看 · {percent(video.view_rate)}</small><small>依據：{video.classification_source}{video.classification_evidence ? ` · ${video.classification_evidence}` : ""}</small></span></a>
                <button type="button" onClick={() => openClassification(video)}>修正分類</button>
              </div>)}
            </div>}
          </section>

          <section className="insight-two-column schedule-layout">
            <article className="panel schedule-panel">
              <div className="panel-heading"><div><p className="section-kicker">LIVE SCHEDULE</p><h2>直播時段熱圖</h2></div><span>台北時間 · 顏色越深代表開台越集中</span></div>
              <div className="schedule-summary"><span>統計日期 <strong>{calendarDate(insights.schedule_summary.date_start)}～{calendarDate(insights.schedule_summary.date_end)}</strong></span><span>涵蓋頻道 <strong>{insights.schedule_summary.channel_count}</strong></span><span>直播場次 <strong>{insights.schedule_summary.stream_count}</strong></span></div>
              <p className="schedule-conclusion">{insights.schedule_summary.conclusion}</p>
              <div className="heatmap" role="grid" aria-label="一週直播開台時段熱圖"><div className="heatmap-corner" />{BLOCKS.map((block) => <span className="heatmap-header" key={block}>{block}</span>)}{insights.schedule.map((row, day) => <div className="heatmap-row" key={DAYS[day]}><strong>{DAYS[day]}</strong>{row.map((cell, block) => {
                const selected = selectedScheduleCell?.day === day && selectedScheduleCell.block === block;
                const label = `${DAYS[day]} ${BLOCKS[block]}：${cell.count} 場，最高同接中位數 ${compact(cell.median_peak)}`;
                return <button className={`heat-cell${selected ? " selected" : ""}`} type="button" key={block} style={{ backgroundColor: `color-mix(in srgb, var(--mint) ${8 + cell.count / heatMax * 82}%, transparent)` }} title={label} aria-label={`${label}；點擊查看直播明細`} aria-pressed={selected} disabled={cell.count === 0} onClick={() => setSelectedScheduleCell({ day, block })}><span>{cell.count || ""}</span></button>;
              })}</div>)}</div>
              {selectedScheduleCell && selectedSchedule && <section className="schedule-detail" aria-live="polite"><div><strong>{DAYS[selectedScheduleCell.day]} {BLOCKS[selectedScheduleCell.block]} 直播明細</strong><span>{selectedSchedule.count} 場 · 最高同接中位數 {compact(selectedSchedule.median_peak)}</span><button type="button" onClick={() => setSelectedScheduleCell(null)} aria-label="關閉直播明細">×</button></div><div className="schedule-stream-list">{selectedSchedule.streams.map((stream) => <a href={`https://www.youtube.com/watch?v=${stream.video_id}`} target="_blank" rel="noreferrer" key={stream.video_id}><span><strong>{stream.title}</strong><small>{stream.channel_title} · {streamTime(stream.started_at)}</small></span><b>{compact(stream.peak_concurrent)}<small>最高同接</small></b><i>開啟 YouTube ↗</i></a>)}</div></section>}
              <p className="panel-footnote">點擊有數字的格子可查看頻道、直播標題、開始時間、最高同接與 YouTube 連結。時段集中不代表觀眾彼此重疊。</p>
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
              <p className="efficiency-explainer">{contentFormat === "直播" ? "直播依完整取樣場次的平均同接／訂閱排序；至少需要 20 個樣本且涵蓋 70% 直播時長，並同時保留峰值供判讀。" : `${contentFormat === "綜合" ? "綜合內容" : contentFormat}依觀看／訂閱比排序。`} 每個同級頻道先取表現最好的一項；我的內容另外顯示，不影響同級排行與基準。</p>
              <div className="top-content-list">
                {referenceEfficientVideo && <a href={`https://www.youtube.com/watch?v=${referenceEfficientVideo.video_id}`} target="_blank" rel="noreferrer" className="top-content-row reference-content-row"><span className="rank">我的</span>{referenceEfficientVideo.thumbnail_url ? <img src={referenceEfficientVideo.thumbnail_url} alt="" /> : <span className="top-thumb-fallback">V</span>}<div><strong>{referenceEfficientVideo.title}</strong><p>{referenceEfficientVideo.channel_title} · {referenceEfficientVideo.content_type} · {referenceEfficientVideo.format_type}{referenceEfficientVideo.peer_rank ? ` · 同級第 ${referenceEfficientVideo.peer_rank}/${referenceEfficientVideo.comparison_count}` : ""}</p></div><div className="top-content-metric"><strong>{contentFormat === "直播" ? perHundred(referenceEfficientVideo.sustained_ccv_rate) : percent(referenceEfficientVideo.view_rate)}</strong><span>{contentFormat === "直播" ? `${compact(referenceEfficientVideo.average_concurrent)} 平均 · ${compact(referenceEfficientVideo.peak_concurrent)} 峰值` : `${compact(referenceEfficientVideo.view_count)} 觀看`}</span></div></a>}
                {efficientVideos.length === 0 ? <div className="insight-placeholder embedded">目前期間內沒有可比較的{contentFormat === "綜合" ? "近期內容" : contentFormat}資料。</div> : efficientVideos.map((video, index) => <a href={`https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer" className="top-content-row" key={video.video_id}><span className="rank">{index + 1}</span>{video.thumbnail_url ? <img src={video.thumbnail_url} alt="" /> : <span className="top-thumb-fallback">V</span>}<div><strong>{video.title}</strong><p>{video.channel_title} · {video.content_type} · {video.format_type}</p></div><div className="top-content-metric"><strong>{contentFormat === "直播" ? perHundred(video.sustained_ccv_rate) : percent(video.view_rate)}</strong><span>{contentFormat === "直播" ? `${compact(video.average_concurrent)} 平均 · ${compact(video.peak_concurrent)} 峰值 · ${video.concurrency_coverage === null ? "—" : `${video.concurrency_coverage.toFixed(0)}%`} 覆蓋` : `${compact(video.view_count)} 觀看`}</span></div></a>)}
              </div>
            </article>

            <aside className="panel top-channel-panel">
              <div className="panel-heading"><div><p className="section-kicker">PEER EXAMPLES</p><h2>同級頻道案例</h2></div><span>近期觀看效率</span></div>
              <div className="peer-list">{insights.top_channels.slice(0, 8).map((channel, index) => <div className="peer-row" key={channel.channel_id}><span>{index + 1}</span>{channel.thumbnail_url ? <img src={channel.thumbnail_url} alt="" /> : <i>V</i>}<div><strong>{channel.title}</strong><p>每週 {channel.weekly_frequency.toFixed(1)} 個 · {compact(channel.median_views)} 觀看中位數</p></div><b>{percent(channel.median_view_rate)}</b></div>)}</div>
            </aside>
          </section>

          <section className="analysis-note"><strong>看懂內容環境</strong><p>先選擇一個基準頻道，觀察訂閱規模相近的頻道在做什麼。從內容主題、觀看／訂閱比與直播時段找出自己的位置；趨勢與成長比較會隨資料累積而更可靠。</p></section>
        </>
      )}

      {classificationVideo && <div className="modal-backdrop" role="presentation"><section className="confirmation-dialog content-classification-dialog" role="dialog" aria-modal="true" aria-labelledby="content-classification-title"><p className="section-kicker">CONTENT CLASSIFICATION</p><h2 id="content-classification-title">修正內容分類</h2><p className="classification-current"><strong>{classificationVideo.title}</strong><span>目前依據：{classificationVideo.classification_source}{classificationVideo.classification_evidence ? ` · ${classificationVideo.classification_evidence}` : ""}</span></p><form onSubmit={(event) => void saveClassification(event)}><fieldset><legend>內容主題（可複選）</legend><div className="classification-topic-options">{TOPIC_OPTIONS.map((topic) => <label key={topic}><input type="checkbox" checked={classificationTopics.includes(topic)} onChange={() => toggleClassificationTopic(topic)} /><span>{topic}</span></label>)}</div><small>不選任何主題會保存為「其他」；混合主題只算一筆內容。</small></fieldset><label><span>遊戲名稱或常用別名</span><input value={classificationGame} onChange={(event) => setClassificationGame(event.target.value)} maxLength={120} placeholder="例如：Minecraft、麥塊" /><small>填寫後會同時標記為遊戲；相同字樣出現在後續標題時可沿用這次確認。</small></label><label><span>修正備註（選填）</span><input value={classificationNote} onChange={(event) => setClassificationNote(event.target.value)} maxLength={240} placeholder="例如：標題中的歌是背景音樂，主題仍為雜談" /></label><div className="dialog-actions">{classificationVideo.classification_source === "人工確認" && <button className="button ghost danger" type="button" onClick={() => void resetClassification()} disabled={classificationSaving}>改回自動分類</button>}<button className="button ghost" type="button" onClick={() => setClassificationVideo(null)} disabled={classificationSaving}>取消</button><button className="button primary" type="submit" disabled={classificationSaving}>{classificationSaving ? "儲存中…" : "儲存並沿用"}</button></div></form></section></div>}

      <LegalFooter context="內容環境" note="所有分析都在你的電腦完成" />
    </main>
  );
}
