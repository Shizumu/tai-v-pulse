"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import LegalFooter from "../legal-footer";
import SiteHeader from "../site-header";

const API_BASE = process.env.NEXT_PUBLIC_TRACKER_API ?? "http://127.0.0.1:8787";

type Channel = {
  channel_id: string;
  title: string;
  thumbnail_url: string | null;
  subscriber_count: number | null;
  view_count?: number | null;
  video_count?: number | null;
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
  median_average_concurrent: number | null;
  median_peak_concurrent: number | null;
  stickiness: number | null;
  sustained_ccv_rate: number | null;
  ccv_rate: number | null;
  concurrency_covered_streams: number;
  concurrency_total_streams: number;
  subscriber_delta_7: Delta;
  subscriber_delta_30: Delta;
  view_delta_30: Delta;
  median_views_delta: Delta;
  stickiness_delta: Delta;
  sustained_ccv_rate_delta: Delta;
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
  comparison_channels: TrendChannel[];
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
    sustained_ccv_rate: TrendChannel[];
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
  organization_coverage?: {
    scope_channels: number;
    named_channels: number;
  };
  series: {
    channel_id: string;
    title: string;
    points: { date: string; subscriber_count: number | null; view_count: number | null; video_count: number | null; stickiness: number | null; sustained_ccv_rate: number | null }[];
  }[];
  peer_series: { date: string; subscriber_count: number | null; view_count: number | null; video_count: number | null; stickiness: number | null; sustained_ccv_rate: number | null }[];
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

const COLORS = ["#2ca981", "#ce6f93", "#4c8ecb", "#8b72ca", "#c18a2d", "#28747c", "#b44c3d"];
const PERIOD_OPTIONS = [7, 14, 30, 90, 365] as const;
const TOPIC_OPTIONS = ["全部", "遊戲", "雜談", "歌回", "ASMR", "音樂作品", "紀念／重大活動", "其他"] as const;

type HistoryField = "subscriber_count" | "view_count" | "stickiness" | "sustained_ccv_rate";
type ChartMetric = "subscribers" | "subscriber_growth" | "views" | "view_growth" | "stickiness" | "sustained_ccv_rate";

const CHART_METRICS: Record<ChartMetric, { label: string; field: HistoryField; growth: boolean; format: "number" | "multiple" | "per_hundred"; description: string }> = {
  subscribers: { label: "訂閱總數", field: "subscriber_count", growth: false, format: "number", description: "比較各頻道當下規模" },
  subscriber_growth: { label: "訂閱成長", field: "subscriber_count", growth: true, format: "number", description: "各頻道相對於圖表起點增加多少訂閱" },
  views: { label: "累積觀看", field: "view_count", growth: false, format: "number", description: "比較頻道公開累積觀看總數" },
  view_growth: { label: "觀看成長", field: "view_count", growth: true, format: "number", description: "各頻道相對於圖表起點增加多少公開觀看" },
  stickiness: { label: "公開黏著度", field: "stickiness", growth: false, format: "multiple", description: "各日往前 30 天內容觀看中位數／當日訂閱；以倍數呈現" },
  sustained_ccv_rate: { label: "直播持續動員", field: "sustained_ccv_rate", growth: false, format: "per_hundred", description: "各日往前 30 天完整取樣直播的平均同接中位數／當日訂閱" },
};

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

function multiple(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  const ratio = value / 100;
  return `${ratio.toFixed(ratio >= 10 ? 1 : 2)}×`;
}

function perHundred(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(value >= 10 ? 1 : 2)} 人／百訂閱`;
}

function matchesContentTopic(video: RankedVideo, topic: string) {
  if (topic === "全部") return true;
  return video.content_type.split("+").some((part) => part.trim() === topic);
}

function DeltaBadge({ delta, label, collectedDays }: { delta: Delta; label: string; collectedDays: number }) {
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const closeTimerRef = useRef<number | null>(null);
  const suppressFocusOpenRef = useRef(false);
  const popoverId = useId();
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{ top: number; left: number; placement: "up" | "down" } | null>(null);
  const ready = delta.ready && delta.change !== null;
  const state = !ready ? "pending" : delta.change! > 0 ? "up" : delta.change! < 0 ? "down" : "flat";
  const arrow = state === "pending" ? "◷" : state === "up" ? "↑" : state === "down" ? "↓" : "→";
  const stateLabel = state === "pending" ? "歷史資料累積中" : state === "up" ? "上升" : state === "down" ? "下降" : "持平";

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = null;
  }, []);

  const openPopover = useCallback(() => {
    clearCloseTimer();
    setOpen(true);
  }, [clearCloseTimer]);

  const closePopover = useCallback((restoreFocus = false) => {
    clearCloseTimer();
    setOpen(false);
    setPosition(null);
    if (restoreFocus) {
      suppressFocusOpenRef.current = true;
      window.requestAnimationFrame(() => {
        triggerRef.current?.focus();
        suppressFocusOpenRef.current = false;
      });
    }
  }, [clearCloseTimer]);

  const scheduleClose = useCallback(() => {
    clearCloseTimer();
    closeTimerRef.current = window.setTimeout(() => {
      const active = document.activeElement;
      if (triggerRef.current?.matches(":hover") || popoverRef.current?.matches(":hover") || triggerRef.current?.contains(active) || popoverRef.current?.contains(active)) return;
      setOpen(false);
      setPosition(null);
    }, 160);
  }, [clearCloseTimer]);

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const popover = popoverRef.current;
    if (!trigger || !popover) return;
    const triggerRect = trigger.getBoundingClientRect();
    const popoverWidth = popover.offsetWidth;
    const popoverHeight = popover.offsetHeight;
    const margin = 12;
    const gap = 9;
    const spaceAbove = triggerRect.top - margin - gap;
    const spaceBelow = window.innerHeight - triggerRect.bottom - margin - gap;
    const placement = spaceBelow >= popoverHeight || spaceBelow >= spaceAbove ? "down" : "up";
    const preferredTop = placement === "down" ? triggerRect.bottom + gap : triggerRect.top - popoverHeight - gap;
    const top = Math.min(Math.max(margin, preferredTop), Math.max(margin, window.innerHeight - popoverHeight - margin));
    const left = Math.min(Math.max(margin, triggerRect.right - popoverWidth), Math.max(margin, window.innerWidth - popoverWidth - margin));
    setPosition({ top, left, placement });
  }, []);

  useEffect(() => {
    if (!open) return;
    updatePosition();
    const frame = window.requestAnimationFrame(updatePosition);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closePopover(true);
    };
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node) || triggerRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      closePopover();
    };
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [closePopover, open, updatePosition]);

  useEffect(() => () => clearCloseTimer(), [clearCloseTimer]);

  return <>
    <button
      ref={triggerRef}
      className={`delta-badge ${state}`}
      type="button"
      aria-label={`${label}${stateLabel}`}
      aria-expanded={open}
      aria-controls={open ? popoverId : undefined}
      aria-haspopup="dialog"
      onMouseEnter={openPopover}
      onMouseLeave={scheduleClose}
      onFocus={() => { if (!suppressFocusOpenRef.current) openPopover(); }}
      onBlur={scheduleClose}
      onClick={openPopover}
    ><span>{arrow}</span></button>
    {open && typeof document !== "undefined" && createPortal(
      <div
        ref={popoverRef}
        id={popoverId}
        className="delta-popover"
        data-placement={position?.placement ?? "down"}
        role="dialog"
        aria-label={`${label}詳細比較`}
        style={position ? { top: position.top, left: position.left } : { top: 0, left: 0, visibility: "hidden" }}
        onMouseEnter={clearCloseTimer}
        onMouseLeave={scheduleClose}
        onFocusCapture={clearCloseTimer}
        onBlurCapture={scheduleClose}
      >
        <div className="delta-popover-heading"><strong>{ready ? `與 ${delta.period_days} 天前比較` : "資料累積中"}</strong><button type="button" onClick={() => closePopover(true)} aria-label="關閉比較跳卡">×</button></div>
        {ready ? <>
          <small>目前：{exact(delta.current)}</small>
          <small>先前：{exact(delta.previous)}</small>
          <small>變化：{delta.change! > 0 ? "+" : ""}{exact(delta.change)}（{delta.percent_change !== null && delta.percent_change > 0 ? "+" : ""}{percent(delta.percent_change)}）</small>
          {delta.basis_at && <small>基準：{new Date(delta.basis_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</small>}
        </> : <>
          <small>已累積 {collectedDays.toFixed(1)}／{delta.period_days} 天</small>
          <small>累積完成後自動顯示漲跌</small>
        </>}
      </div>,
      document.body,
    )}
  </>;
}

function ComparisonChart({ series, peerSeries, metric }: { series: Trends["series"]; peerSeries: Trends["peer_series"]; metric: ChartMetric }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const definition = CHART_METRICS[metric];
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
      const convert = <T extends Record<HistoryField, number | null>>(points: (T & { date: string })[]) => {
        const first = points.find((point) => point[definition.field] !== null)?.[definition.field] ?? null;
        return points.map((point) => ({
          date: point.date,
          value: point[definition.field] === null
            ? null
            : definition.growth && first !== null
              ? Number(point[definition.field]) - Number(first)
              : Number(point[definition.field]),
        }));
      };
      const peerPoints = convert(peerSeries);
      const channelSeries = series.map((row) => ({ ...row, values: convert(row.points) }));
      const dates = [...new Set([...peerSeries.map((point) => point.date), ...series.flatMap((row) => row.points.map((point) => point.date))])].sort();
      const values = [
        ...peerPoints.map((point) => point.value),
        ...channelSeries.flatMap((row) => row.values.map((point) => point.value)),
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
        const labelValue = max - span * line / 4;
        const axisLabel = definition.format === "multiple" ? multiple(labelValue) : definition.format === "per_hundred" ? perHundred(labelValue) : compact(labelValue);
        context.fillText(axisLabel, 8, lineY + 4);
      }
      const drawLine = (points: { date: string; value: number | null }[], color: string, dashed = false) => {
        context.strokeStyle = color; context.lineWidth = dashed ? 2 : 2.8; context.setLineDash(dashed ? [6, 5] : []);
        context.beginPath(); let started = false;
        for (const point of points) {
          if (point.value === null) continue;
          if (!started) { context.moveTo(x(point.date), y(point.value)); started = true; }
          else context.lineTo(x(point.date), y(point.value));
        }
        context.stroke(); context.setLineDash([]);
      };
      drawLine(peerPoints, "#8f9994", true);
      channelSeries.forEach((row, index) => drawLine(row.values, COLORS[index % COLORS.length]));
      context.fillStyle = "#6d7973"; context.font = "11px system-ui";
      context.fillText(dates[0], padding.left, height - 12);
      if (dates.length > 1) context.fillText(dates[dates.length - 1], width - padding.right - 72, height - 12);
    };
    draw();
    const observer = new ResizeObserver(draw);
    if (canvas.parentElement) observer.observe(canvas.parentElement);
    return () => observer.disconnect();
  }, [definition.field, definition.format, definition.growth, peerSeries, series]);
  return <canvas ref={canvasRef} role="img" aria-label={`頻道${definition.label}歷史折線圖`} />;
}

export default function TrendsDashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [trends, setTrends] = useState<Trends | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(7);
  const [periodMode, setPeriodMode] = useState("7");
  const [customDays, setCustomDays] = useState("30");
  const [mode, setMode] = useState("all");
  const [tier, setTier] = useState("5k-10k");
  const [customMin, setCustomMin] = useState(1000);
  const [customMax, setCustomMax] = useState(5000);
  const [category, setCategory] = useState("全部");
  const [referenceId, setReferenceId] = useState("");
  const [cohortIds, setCohortIds] = useState<string[]>([]);
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [formatType, setFormatType] = useState("主要內容");
  const [contentTopic, setContentTopic] = useState("全部");
  const [organizationScope, setOrganizationScope] = useState<"peer" | "all">("peer");
  const [includeGraduated, setIncludeGraduated] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [ranking, setRanking] = useState<"subscribers" | "median_views" | "stickiness" | "sustained_ccv_rate">("subscribers");
  const [chartMetric, setChartMetric] = useState<ChartMetric>("subscribers");
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
    if (ownedDefaultApplied.current || !summary) return;
    const parameters = new URLSearchParams(window.location.search);
    const requestedReference = parameters.get("reference")?.trim() ?? "";
    const allChannels = [...summary.channels, ...(summary.owned_channel ? [summary.owned_channel] : [])];
    const validIds = new Set(allChannels.map((channel) => channel.channel_id));
    const nextReference = validIds.has(requestedReference) ? requestedReference : summary.owned_channel_id ?? "";
    const requestedChannels = (parameters.get("channels") ?? "").split(",")
      .map((channelId) => channelId.trim())
      .filter((channelId, index, values) => channelId && channelId !== nextReference && validIds.has(channelId) && values.indexOf(channelId) === index)
      .slice(0, 6);
    ownedDefaultApplied.current = true;
    if (nextReference) setReferenceId(nextReference);
    if (requestedChannels.length > 0) {
      setCohortIds(requestedChannels);
      setComparisonIds(requestedChannels);
      setMode("channels");
    } else if (nextReference) {
      setMode("relative");
    }
  }, [summary]);

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
    const params = new URLSearchParams({ days: String(days), min_subscribers: String(range[0]), max_subscribers: String(range[1]), category, format_type: formatType, content_topic: contentTopic, organization_scope: organizationScope, include_graduated: String(includeGraduated) });
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
  }, [category, cohortKey, comparisonKey, contentTopic, days, formatType, includeGraduated, mode, organizationScope, range, referenceAvailable, referenceId, refreshKey, summaryReady]);

  function commitCustomDays() {
    const normalized = Math.max(7, Math.min(365, Number.parseInt(customDays, 10) || 7));
    setCustomDays(String(normalized));
    setDays(normalized);
  }

  function addId(setter: (value: string[] | ((current: string[]) => string[])) => void, id: string) {
    if (!id || id === referenceId) return;
    setter((current: string[]) => [...new Set([...current, id])].slice(0, 6));
  }

  function saveGroup() {
    const name = groupName.trim();
    if (!name || comparisonIds.length === 0) return;
    const next = [...savedGroups.filter((group) => group.name !== name), { name, ids: comparisonIds }];
    setSavedGroups(next); setGroupName("");
    window.localStorage.setItem("tai-v-pulse-comparison-groups", JSON.stringify(next));
  }

  const rankingRows = trends?.rankings[ranking] ?? [];
  const organizationCoverage = trends?.organization_coverage ?? {
    scope_channels: trends?.overview.peer_channels ?? 0,
    named_channels: 0,
  };
  const visibleTopVideos = trends?.rankings.top_videos.filter((video) => matchesContentTopic(video, contentTopic)) ?? [];
  const rankingMetric = (row: TrendChannel) => ranking === "subscribers" ? row.subscriber_count : ranking === "median_views" ? row.median_views : ranking === "stickiness" ? row.stickiness : row.sustained_ccv_rate;
  const rankingDelta = (row: TrendChannel) => ranking === "subscribers" ? row.subscriber_delta_30 : ranking === "median_views" ? row.median_views_delta : ranking === "stickiness" ? row.stickiness_delta : row.sustained_ccv_rate_delta;
  const metricLabel = ranking === "subscribers" ? "訂閱數" : ranking === "median_views" ? "觀看中位數" : ranking === "stickiness" ? "公開觀看黏著度" : "直播持續動員";
  const metricFormatter = (value: number | null) => ranking === "stickiness" ? multiple(value) : ranking === "sustained_ccv_rate" ? perHundred(value) : compact(value);
  const rankingExplanation = ranking === "subscribers"
    ? "依目前公開訂閱數排序；頻道隱藏訂閱數時顯示缺值並排在後方。這是頻道級指標，不受內容形式篩選影響。"
    : ranking === "median_views"
      ? `依最近 30 日${formatType === "全部" ? "全部內容" : `符合「${formatType}」的內容`}公開觀看中位數排序；沒有可用內容時顯示缺值。`
      : ranking === "stickiness"
        ? `公開觀看黏著度＝最近 30 日${formatType === "全部" ? "全部內容" : `符合「${formatType}」的內容`}觀看中位數／目前訂閱數；缺少內容或訂閱數時不計算，也不是 Studio 回訪觀眾或留存率。`
        : "直播持續動員＝最近 30 日完整取樣直播的平均同接中位數／目前訂閱數，以每百位訂閱可持續留下幾位觀眾呈現。每場至少需要 20 個樣本且涵蓋 70% 直播時長；此指標固定看直播，不受上方內容形式篩選影響。";
  const maxMetric = Math.max(1, ...rankingRows.slice(0, 15).map((row) => Math.max(0, Number(rankingMetric(row) ?? 0))));

  return <main className="app-shell trends-shell">
    <SiteHeader active="trends" eyebrow="MARKET TRENDS" title="趨勢圖表" connected={connected} statusText={trends ? `本機快照更新 ${new Date(trends.generated_at).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Taipei" })}` : undefined} />
    {!connected && <section className="notice warning"><span className="notice-icon">!</span><div><strong>資料服務尚未啟動</strong><p>啟動台V Pulse 後，圖表會自動讀取本機快照。</p></div></section>}
    {error && <section className="inline-message">{error}</section>}

    <section className="panel trend-controls">
      <div className="control-intro"><p className="section-kicker">COMPARISON BUILDER</p><h2>以我的頻道建立比較</h2><p>基準頻道不納入同級中位數；公開資料可公平比較，Studio 私人指標只顯示在自己的區塊。</p></div>
      <div className="trend-control-grid">
        <div className="trend-filter-stack period-filter-stack"><label><span>圖表觀察期間</span><select value={periodMode} onChange={(event) => { const value = event.target.value; setPeriodMode(value); if (value !== "custom") { const nextDays = Number(value); setDays(nextDays); setCustomDays(String(nextDays)); } }}>{PERIOD_OPTIONS.map((option) => <option value={option} key={option}>{option} 天</option>)}<option value="custom">自訂天數…</option></select></label>{periodMode === "custom" && <label className="custom-period-control"><span>自訂 7～365 天</span><input type="number" min={7} max={365} step={1} value={customDays} onChange={(event) => setCustomDays(event.target.value)} onBlur={commitCustomDays} onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} /></label>}<small>只調整歷史折線；熱門內容與摘要固定最近 30 天。</small></div>
        <div className="trend-filter-stack"><label><span>比較群組</span><select value={mode} onChange={(event) => setMode(event.target.value)}><option value="all">全部頻道</option><option value="relative">基準頻道 0.5～2 倍</option><option value="tier">固定量級</option><option value="range">自訂量級</option><option value="channels">指定頻道群組</option></select></label><label className="graduated-toggle"><input type="checkbox" checked={includeGraduated} onChange={(event) => setIncludeGraduated(event.target.checked)} /><span>包含已確認畢業頻道</span></label>{mode === "tier" && <label className="cohort-mode-control"><span>訂閱級距</span><select value={tier} onChange={(event) => setTier(event.target.value)}>{Object.entries(TIERS).map(([key, value]) => <option value={key} key={key}>{value[2]}</option>)}</select></label>}{mode === "range" && <div className="range-controls cohort-mode-control"><label><span>最低訂閱</span><input type="number" min={0} value={customMin} onChange={(event) => setCustomMin(Number(event.target.value))} /></label><label><span>最高訂閱</span><input type="number" min={customMin} value={customMax} onChange={(event) => setCustomMax(Number(event.target.value))} /></label></div>}{mode === "channels" && <label className="cohort-mode-control"><span>加入群組</span><select value="" onChange={(event) => addId(setCohortIds, event.target.value)}><option value="">選擇頻道…</option>{summary?.channels.filter((channel) => channel.channel_id !== referenceId && !cohortIds.includes(channel.channel_id)).map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.title}</option>)}</select></label>}</div>
        <label><span>頻道分類</span><select value={category} onChange={(event) => setCategory(event.target.value)}><option>全部</option>{summary?.categories.map((item) => <option value={item.category} key={item.category}>{item.category}</option>)}</select></label>
        <label><span>內容形式</span><select value={formatType} onChange={(event) => setFormatType(event.target.value)}><option>主要內容</option><option>直播</option><option value="影片">一般影片</option><option>Shorts</option><option>全部</option></select></label>
        <label className="reference-control"><span>基準頻道</span><select value={referenceId} onChange={(event) => setReferenceId(event.target.value)}><option value="">不設定基準</option>{summary?.owned_channel && !summary.channels.some((channel) => channel.channel_id === summary.owned_channel?.channel_id) && <option value={summary.owned_channel.channel_id}>我的頻道：{summary.owned_channel.title}</option>}{summary?.channels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.title}｜{compact(channel.subscriber_count)}</option>)}</select></label>
        <label className="comparison-control"><span>加入固定比較頻道（最多 6 個）</span><select value="" onChange={(event) => addId(setComparisonIds, event.target.value)}><option value="">加入頻道…</option>{summary?.channels.filter((channel) => channel.channel_id !== referenceId && !comparisonIds.includes(channel.channel_id)).map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.title}</option>)}</select></label>
      </div>
      <div className="cohort-summary"><span>{referenceId === summary?.owned_channel_id ? "以我的頻道為基準" : "目前比較"}</span><strong>{mode === "relative" && reference ? `${reference.title} 的 0.5～2 倍` : mode === "tier" ? TIERS[tier][2] : mode === "range" ? `${exact(range[0])}～${exact(range[1])}` : mode === "channels" ? `${cohortIds.length} 個指定頻道` : "全部已收錄頻道"}</strong><small>{mode === "channels" ? "指定群組" : `${exact(range[0])}～${range[1] >= 100000000 ? "不限上限" : exact(range[1])} 訂閱`}</small></div>
      {mode === "channels" && <div className="cohort-channel-chips"><span>指定群組</span><div className="selected-channel-chips">{cohortIds.map((id) => <button type="button" onClick={() => setCohortIds((current) => current.filter((value) => value !== id))} key={id}>{summary?.channels.find((channel) => channel.channel_id === id)?.title ?? id}<span>×</span></button>)}</div></div>}
      <div className="comparison-selection-strip"><span>圖表固定比較線</span><div className="selected-channel-chips">{comparisonIds.length === 0 ? <small>尚未加入額外頻道</small> : comparisonIds.map((id) => <button type="button" onClick={() => setComparisonIds((current) => current.filter((value) => value !== id))} key={id}>{summary?.channels.find((channel) => channel.channel_id === id)?.title ?? id}<span>×</span></button>)}</div></div>
      <div className="saved-comparison-toolbar"><span>儲存固定比較組合</span><div className="save-group-row"><input value={groupName} onChange={(event) => setGroupName(event.target.value)} placeholder="組合名稱" aria-label="固定比較組合名稱" /><button type="button" onClick={saveGroup} disabled={!groupName.trim() || comparisonIds.length === 0}>儲存</button>{savedGroups.length > 0 && <select value="" aria-label="載入固定比較組合" onChange={(event) => { const group = savedGroups.find((item) => item.name === event.target.value); if (group) setComparisonIds(group.ids.slice(0, 6)); }}><option value="">載入組合…</option>{savedGroups.map((group) => <option value={group.name} key={group.name}>{group.name}</option>)}</select>}</div></div>
    </section>

    {loading && !trends && <section className="panel insight-placeholder">正在整理趨勢資料…</section>}
    {!loading && mode === "channels" && cohortIds.length === 0 && <section className="panel insight-placeholder"><div><strong>先加入要比較的頻道</strong><p>可指定最多 6 個頻道形成自訂比較群組。</p></div></section>}
    {trends && <>
      {!trends.readiness.month_ready && <section className="notice trend-readiness"><span className="notice-icon">◷</span><div><strong>30 天摘要與變化正在累積</strong><p>目前已累積 {trends.readiness.collected_days.toFixed(1)} 天；圖表仍依上方 {days} 天期間顯示已有快照，固定 30 天的觀看摘要與月變化會在資料成熟後解鎖。</p></div></section>}
      <section className="trend-overview-grid">
        <article className="insight-hero primary"><span>同級頻道</span><strong>{trends.overview.peer_channels}</strong><p>{trends.overview.active_channels} 個近 30 天有內容</p></article>
        <article className="insight-hero"><span>同級訂閱中位數</span><strong>{compact(trends.overview.median_subscribers)}</strong><p>不包含基準頻道</p></article>
        <article className="insight-hero"><span>近 30 日觀看中位數</span><strong>{compact(trends.overview.median_views)}</strong><p>{formatType === "主要內容" ? "不包含 Shorts" : formatType}</p></article>
        <article className="insight-hero"><span>近 30 日公開黏著度</span><strong>{multiple(trends.overview.median_stickiness)}</strong><p>觀看中位數／訂閱數</p></article>
        {trends.reference && <article className="insight-hero owned-trend-card"><span>我的公開訂閱</span><div className="metric-with-delta"><strong>{compact(trends.reference.subscriber_count)}</strong><DeltaBadge delta={trends.reference.subscriber_delta_30} label="訂閱數" collectedDays={trends.readiness.collected_days} /></div><p>{trends.reference.title}</p></article>}
        {trends.reference && <article className="insight-hero"><span>我的公開黏著度</span><div className="metric-with-delta"><strong>{multiple(trends.reference.stickiness)}</strong><DeltaBadge delta={trends.reference.stickiness_delta} label="公開觀看黏著度" collectedDays={trends.readiness.collected_days} /></div><p>不是 Studio 回訪觀眾</p></article>}
      </section>

      <section className="panel trend-chart-panel">
        <div className="panel-heading"><div><p className="section-kicker">MULTI-METRIC HISTORY</p><h2>最近 {days} 天固定頻道趨勢</h2></div><span>此期間只套用歷史折線 · 實線為指定頻道 · 灰色虛線為同級中位數</span></div>
        <div className="chart-metric-toolbar"><div className="format-tabs chart-metric-tabs">{(Object.keys(CHART_METRICS) as ChartMetric[]).map((key) => <button type="button" className={chartMetric === key ? "active" : ""} onClick={() => setChartMetric(key)} key={key}>{CHART_METRICS[key].label}</button>)}</div><span>{CHART_METRICS[chartMetric].description}</span></div>
        <div className="trend-chart"><ComparisonChart series={trends.series} peerSeries={trends.peer_series} metric={chartMetric} /></div>
        <div className="chart-legend"><span><i className="peer" />同級中位數</span>{trends.series.map((row, index) => <span key={row.channel_id}><i style={{ background: COLORS[index % COLORS.length] }} />{row.title}</span>)}</div>
      </section>

      <section className="panel fixed-comparison-panel">
        <div className="panel-heading"><div><p className="section-kicker">CHANNEL SCORECARD</p><h2>固定頻道指標比較</h2></div><span>同一批頻道一次比較規模、成長、產量、觀看與直播表現</span></div>
        <div className="comparison-table-wrap"><table className="comparison-metric-table"><thead><tr><th>頻道</th><th>訂閱</th><th>30日訂閱變化</th><th>30日觀看變化</th><th>近30日內容</th><th>觀看中位數</th><th>公開黏著度</th><th>直播平均／峰值</th><th>直播持續動員</th></tr></thead><tbody>{trends.comparison_channels.map((row) => <tr className={row.is_reference ? "reference" : ""} key={row.channel_id}><td><strong>{row.title}</strong>{row.is_reference && <span>我的基準</span>}</td><td>{compact(row.subscriber_count)}</td><td>{row.subscriber_delta_30.ready ? <><b className={Number(row.subscriber_delta_30.change) > 0 ? "positive" : Number(row.subscriber_delta_30.change) < 0 ? "negative" : ""}>{Number(row.subscriber_delta_30.change) > 0 ? "+" : ""}{compact(row.subscriber_delta_30.change)}</b><small>{percent(row.subscriber_delta_30.percent_change)}</small></> : <small>資料累積中</small>}</td><td>{row.view_delta_30.ready ? <><b className={Number(row.view_delta_30.change) > 0 ? "positive" : Number(row.view_delta_30.change) < 0 ? "negative" : ""}>{Number(row.view_delta_30.change) > 0 ? "+" : ""}{compact(row.view_delta_30.change)}</b><small>{percent(row.view_delta_30.percent_change)}</small></> : <small>資料累積中</small>}</td><td>{exact(row.recent_items, 0)}</td><td>{compact(row.median_views)}</td><td>{multiple(row.stickiness)}</td><td><b>{compact(row.median_average_concurrent)} 平均</b><small>{compact(row.median_peak_concurrent)} 峰值 · {row.concurrency_covered_streams}/{row.concurrency_total_streams} 場完整</small></td><td>{perHundred(row.sustained_ccv_rate)}</td></tr>)}</tbody></table></div>
        {trends.comparison_channels.length < 2 && <p className="panel-footnote">在上方「固定比較線」再加入頻道，就能並排比較這些指標。</p>}
      </section>

      <section className="panel ranking-panel">
        <div className="panel-heading efficiency-heading"><div><p className="section-kicker">MARKET RANKINGS</p><h2>市場排行</h2></div><div className="format-tabs ranking-tabs">{(["subscribers", "median_views", "stickiness", "sustained_ccv_rate"] as const).map((key) => <button className={ranking === key ? "active" : ""} type="button" onClick={() => setRanking(key)} key={key}>{key === "subscribers" ? "訂閱數" : key === "median_views" ? "觀看中位數" : key === "stickiness" ? "公開黏著度" : "直播持續動員"}</button>)}</div></div>
        <p className="efficiency-explainer">{rankingExplanation}</p>
        <div className="ranking-layout"><div className="trend-bars">{rankingRows.slice(0, 15).map((row, index) => { const value = rankingMetric(row); return <div className={`trend-bar-row${row.is_reference ? " reference" : ""}`} key={row.channel_id}><span>{index + 1}</span><div><strong>{row.title}{row.is_reference ? "（我的頻道）" : ""}</strong><small>{compact(row.subscriber_count)} 訂閱 · {ranking === "sustained_ccv_rate" ? `${row.concurrency_covered_streams}/${row.concurrency_total_streams} 場完整取樣` : `${row.recent_items} 項內容`}</small></div><div className="trend-bar-track"><i style={{ width: `${Math.max(2, Math.max(0, Number(value ?? 0)) / maxMetric * 100)}%` }} /></div><b>{metricFormatter(value)}</b><DeltaBadge delta={rankingDelta(row)} label={metricLabel} collectedDays={trends.readiness.collected_days} /></div>; })}</div></div>
      </section>

      <section className="insight-two-column trend-secondary-grid">
        <article className="panel"><div className="panel-heading panel-heading-controls"><div><p className="section-kicker">POPULAR CONTENT</p><h2>近 30 天熱門內容</h2><span>{formatType === "主要內容" ? "直播＋一般影片" : formatType} · 主題篩選只影響本區</span></div><label><span>內容主題</span><select value={contentTopic} onChange={(event) => setContentTopic(event.target.value)}>{TOPIC_OPTIONS.map((topic) => <option value={topic} key={topic}>{topic}</option>)}</select></label></div><div className="top-content-list">{visibleTopVideos.length === 0 ? <div className="insight-placeholder embedded">最近 30 天沒有符合此形式與主題的內容。</div> : visibleTopVideos.slice(0, 8).map((video, index) => <a className="top-content-row" href={`https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer" key={video.video_id}><span className="rank">{index + 1}</span>{video.thumbnail_url ? <img src={video.thumbnail_url} alt="" /> : <span className="top-thumb-fallback">V</span>}<div><strong>{video.title}</strong><p>{video.channel_title} · {video.content_type} · {video.format_type}{video.attributes.includes("聯動") ? " · 聯動" : ""}</p></div><div className="top-content-metric"><strong>{compact(video.view_count)}</strong><span>{percent(video.view_rate)} 觀看／訂閱</span></div></a>)}</div></article>
        <aside className="panel"><div className="panel-heading organization-heading"><div><p className="section-kicker">ORGANIZATION VIEW</p><h2>組織／團體表現</h2><span>成員近 30 日觀看中位數加總</span></div><div className="format-tabs organization-scope-tabs"><button className={organizationScope === "peer" ? "active" : ""} type="button" onClick={() => setOrganizationScope("peer")}>同級範圍</button><button className={organizationScope === "all" ? "active" : ""} type="button" onClick={() => setOrganizationScope("all")}>全部已收錄</button></div></div><p className="organization-scope-note">{organizationScope === "peer" ? `使用目前 ${exact(range[0])}～${range[1] >= 100000000 ? "不限上限" : exact(range[1])} 訂閱範圍。` : "忽略訂閱級距，仍套用頻道分類與已畢業篩選。"} 已有 {organizationCoverage.named_channels}／{organizationCoverage.scope_channels} 個頻道填寫組織名稱；未填者不納入組織統計。</p><div className="organization-bars">{trends.rankings.organizations.length === 0 ? <div className="insight-placeholder embedded">為頻道填入所屬組織後會顯示比較。</div> : trends.rankings.organizations.slice(0, 12).map((organization, index) => <div key={organization.organization_name}><span>{index + 1}</span><strong>{organization.organization_name}</strong><i><b style={{ width: `${organization.median_views_total / Math.max(1, trends.rankings.organizations[0].median_views_total) * 100}%` }} /></i><em>{compact(organization.median_views_total)} · {organization.members} 個頻道</em></div>)}</div></aside>
      </section>

      {trends.private_metrics && Object.values(trends.private_metrics).some((value) => value !== null) && <section className="panel private-trend-panel"><div className="panel-heading"><div><p className="section-kicker">PRIVATE STUDIO OVERLAY</p><h2>我的 Studio 私人指標</h2></div><span>只顯示自己的資料，不與公開頻道硬比</span></div><div className="private-metric-strip"><div><span>觀看</span><strong>{compact(trends.private_metrics.views)}</strong></div><div><span>觀看時數</span><strong>{exact(trends.private_metrics.watch_time_hours)}</strong></div><div><span>曝光</span><strong>{compact(trends.private_metrics.impressions)}</strong></div><div><span>點閱率</span><strong>{percent(trends.private_metrics.impressions_ctr)}</strong></div><div><span>回訪觀眾</span><strong>{compact(trends.private_metrics.returning_viewers)}</strong></div></div></section>}
    </>}
    <LegalFooter context="趨勢圖表" note="圖表使用本機快照，不增加 YouTube API 配額" />
  </main>;
}
