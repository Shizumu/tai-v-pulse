"use client";

import { useCallback, useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";
import Link from "next/link";
import LegalFooter from "../legal-footer";
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
  activity_status?: string;
  last_stats_at?: string | null;
};

type WorkspaceChannel = Channel & {
  subscriber_count_delta_30d: number | null;
  view_count_delta_30d: number | null;
  video_count_delta_30d: number | null;
  snapshot_count_30d: number;
  month_ready: boolean;
  recent_content_count_30d: number;
  peak_concurrent: number | null;
  concurrency_sample_count: number;
  added_at: string;
};

type PublicSnapshot = {
  captured_at: string;
  subscriber_count: number | null;
  view_count: number | null;
  video_count: number | null;
};

type PublicVideo = {
  video_id: string;
  title: string;
  thumbnail_url: string | null;
  published_at: string | null;
  scheduled_start: string | null;
  live_state: string;
  view_count: number | null;
  peak_concurrent: number | null;
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
  published_at: string | null;
  duration_seconds: number | null;
  row_kind: string;
  video_id: string | null;
  video_title: string | null;
  views: number | null;
  engaged_views: number | null;
  watch_time_hours: number | null;
  average_view_duration_seconds: number | null;
  average_percentage_viewed: number | null;
  impressions: number | null;
  impressions_ctr: number | null;
  likes: number | null;
  comments: number | null;
  unique_viewers: number | null;
  returning_viewers: number | null;
  conflict_status: number;
  display_date: string | null;
  date_source: string;
  content_format: string;
  content_topic: string;
  classification_source: string;
  classification_evidence: string;
  game_name: string;
};

type AnalyticsPayload = {
  channel_id: string | null;
  rows: AnalyticsRow[];
  result_count: number;
  available_count: number;
  page: number;
  page_size: number;
  page_count: number;
  reports: string[];
  content_formats: string[];
  content_topics: string[];
  error?: string;
};

type AnalyticsSort =
  | "date"
  | "content"
  | "report_name"
  | "content_format"
  | "content_topic"
  | "views"
  | "engaged_views"
  | "watch_time_hours"
  | "average_view_duration_seconds"
  | "average_percentage_viewed"
  | "impressions"
  | "impressions_ctr"
  | "unique_viewers"
  | "returning_viewers"
  | "likes"
  | "comments"
  | "conflict_status";

type ManualMetric = {
  id: number;
  metric_date: string;
  video_id: string;
  metric_name: string;
  metric_value: number;
  note: string;
};

type OAuthMetrics = Partial<Record<
  "views" | "engaged_views" | "watch_time_hours" | "average_view_duration_seconds" |
  "average_percentage_viewed" | "subscribers_net" | "subscribers_gained" |
  "subscribers_lost" | "likes" | "comments" | "shares" | "unique_viewers",
  number
>>;

type OAuthData = {
  status: {
    configured: boolean;
    authorized: boolean;
    connected: boolean;
    channel: { channel_id: string; title: string; thumbnail_url: string | null } | null;
    connected_at: string | null;
    last_sync_at: string | null;
    last_data_date: string | null;
    last_error: string;
    last_error_help: {
      code: string;
      title: string;
      message: string;
      steps: string[];
      help_url: string | null;
      help_label: string | null;
    } | null;
    syncing: boolean;
    sync_started_at: string | null;
    storage: string;
  };
  summary: { date_start: string; date_end: string; synced_at: string; metrics: OAuthMetrics } | null;
  daily: { event_date: string; synced_at: string; metrics: OAuthMetrics }[];
  videos: { video_id: string; title: string | null; thumbnail_url: string | null; published_at: string | null; live_at: string | null; content_date: string | null; synced_at: string; metrics: OAuthMetrics }[];
};

type CreatorData = {
  owned_channel_id: string | null;
  workspace_channels: WorkspaceChannel[];
  channel: Channel | null;
  public: null | {
    peak_concurrent: number | null;
    concurrency_sample_count: number;
    snapshots: PublicSnapshot[];
    videos: PublicVideo[];
  };
  oauth: OAuthData;
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
  recognized_dimensions: string[];
  recognized_column_count: number;
  ignored_headers: string[];
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
  engaged_views: "互動觀看次數",
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

const ANALYTICS_SORT_OPTIONS: { value: AnalyticsSort; label: string }[] = [
  { value: "date", label: "資料日期／發布日" },
  { value: "content", label: "內容標題" },
  { value: "content_topic", label: "內容主題" },
  { value: "content_format", label: "內容格式" },
  { value: "report_name", label: "來源報表" },
  { value: "views", label: "觀看" },
  { value: "engaged_views", label: "互動觀看" },
  { value: "watch_time_hours", label: "觀看時數" },
  { value: "average_view_duration_seconds", label: "平均觀看" },
  { value: "average_percentage_viewed", label: "觀看比例" },
  { value: "impressions", label: "曝光" },
  { value: "impressions_ctr", label: "點閱率" },
  { value: "unique_viewers", label: "不重複觀眾" },
  { value: "returning_viewers", label: "回訪觀眾" },
  { value: "likes", label: "喜歡" },
  { value: "comments", label: "留言" },
  { value: "conflict_status", label: "核對狀態" },
];

function SortHeader({
  field,
  label,
  sort,
  direction,
  onSort,
}: {
  field: AnalyticsSort;
  label: string;
  sort: AnalyticsSort;
  direction: "asc" | "desc";
  onSort: (field: AnalyticsSort) => void;
}) {
  const active = sort === field;
  return (
    <th aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}>
      <button className="analytics-sort-button" type="button" onClick={() => onSort(field)}>
        {label}<span aria-hidden="true">{active ? (direction === "asc" ? "↑" : "↓") : "↕"}</span>
      </button>
    </th>
  );
}

function compact(value: number | null | undefined, digits = 1) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: digits }).format(value);
}

function exact(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-TW", { maximumFractionDigits: digits }).format(value);
}

function signed(value: number | null | undefined) {
  if (value === null || value === undefined) return "資料累積中";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${new Intl.NumberFormat("zh-TW").format(value)}`;
}

function dateTime(value: string | null | undefined) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Taipei" }).format(new Date(value));
}

function openYouTubeVideo(videoId: string) {
  window.open(`https://www.youtube.com/watch?v=${videoId}`, "_blank", "noopener,noreferrer");
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
  const [classificationChannel, setClassificationChannel] = useState<ChannelCandidate | null>(null);
  const [classificationCategory, setClassificationCategory] = useState("未分類");
  const [classificationOrganization, setClassificationOrganization] = useState("");
  const [classificationTags, setClassificationTags] = useState("");
  const [classificationActivity, setClassificationActivity] = useState("自動判斷");
  const [analytics, setAnalytics] = useState<AnalyticsPayload | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);
  const [analyticsRevision, setAnalyticsRevision] = useState(0);
  const [analyticsQueryDraft, setAnalyticsQueryDraft] = useState("");
  const [analyticsQuery, setAnalyticsQuery] = useState("");
  const [analyticsDateStart, setAnalyticsDateStart] = useState("");
  const [analyticsDateEnd, setAnalyticsDateEnd] = useState("");
  const [analyticsReport, setAnalyticsReport] = useState("all");
  const [analyticsRowKind, setAnalyticsRowKind] = useState("detail");
  const [analyticsStatus, setAnalyticsStatus] = useState("all");
  const [analyticsFormat, setAnalyticsFormat] = useState("all");
  const [analyticsTopic, setAnalyticsTopic] = useState("all");
  const [analyticsSort, setAnalyticsSort] = useState<AnalyticsSort>("date");
  const [analyticsDirection, setAnalyticsDirection] = useState<"asc" | "desc">("desc");
  const [analyticsPage, setAnalyticsPage] = useState(1);
  const [analyticsPageSize, setAnalyticsPageSize] = useState(50);
  const [workspaceMode, setWorkspaceMode] = useState<"personal" | "team">("personal");
  const [oauthManagerOpen, setOauthManagerOpen] = useState(false);
  const [oauthBusy, setOauthBusy] = useState(false);
  const [oauthSyncing, setOauthSyncing] = useState(false);

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
      if (creatorPayload.oauth.status.last_error || creatorPayload.oauth.status.last_error_help) {
        setOauthManagerOpen(true);
      }
      setConnected(true);
      setAnalyticsRevision((current) => current + 1);
    } catch (error) {
      setConnected(false);
      setMessage(error instanceof Error ? error.message : "無法載入頻道工作區");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 30000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  useEffect(() => {
    if (!oauthSyncing) return;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/creator`, { cache: "no-store" });
        if (!response.ok) throw new Error("本機資料服務沒有回應");
        const payload = await response.json() as CreatorData;
        if (stopped) return;
        setCreator(payload);
        if (payload.oauth.status.last_error || payload.oauth.status.last_error_help) {
          setOauthManagerOpen(true);
        }
        if (payload.oauth.status.syncing) {
          timer = window.setTimeout(() => void poll(), 2000);
          return;
        }
        setOauthSyncing(false);
        setAnalyticsRevision((current) => current + 1);
        if (payload.oauth.status.last_error_help) {
          setMessage(`${payload.oauth.status.last_error_help.title}：${payload.oauth.status.last_error_help.message}`);
        } else if (payload.oauth.status.last_sync_at) {
          setMessage(`YouTube Analytics 同步完成，資料更新至 ${payload.oauth.status.last_data_date ?? "目前可取得日期"}。`);
        } else {
          setMessage("同步工作已結束，但 Google 尚未回傳可用資料；請查看下方疑難排解。");
        }
      } catch (error) {
        if (stopped) return;
        setMessage(error instanceof Error ? error.message : "無法確認同步狀態");
        timer = window.setTimeout(() => void poll(), 3000);
      }
    };
    timer = window.setTimeout(() => void poll(), 500);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [oauthSyncing]);

  useEffect(() => {
    const receiveOAuthCompletion = (event: MessageEvent) => {
      if (event.origin === API_BASE && event.data === "tai-v-pulse-oauth-complete") {
        void refresh();
      }
    };
    window.addEventListener("message", receiveOAuthCompletion);
    return () => window.removeEventListener("message", receiveOAuthCompletion);
  }, [refresh]);

  useEffect(() => {
    const channelId = creator?.owned_channel_id;
    if (!channelId) {
      return;
    }
    const controller = new AbortController();
    const parameters = new URLSearchParams({
      channel_id: channelId,
      query: analyticsQuery,
      date_start: analyticsDateStart,
      date_end: analyticsDateEnd,
      report_name: analyticsReport,
      row_kind: analyticsRowKind,
      status: analyticsStatus,
      content_format: analyticsFormat,
      content_topic: analyticsTopic,
      sort: analyticsSort,
      direction: analyticsDirection,
      page: String(analyticsPage),
      page_size: String(analyticsPageSize),
    });
    const request = window.setTimeout(() => {
      setAnalyticsLoading(true);
      setAnalyticsError(null);
      void fetch(`${API_BASE}/api/creator/analytics?${parameters.toString()}`, {
        cache: "no-store",
        signal: controller.signal,
      }).then(async (response) => {
        const payload = await response.json() as AnalyticsPayload;
        if (!response.ok) throw new Error(payload.error ?? "無法讀取已解析內容資料");
        setAnalytics(payload);
        if (payload.page !== analyticsPage) setAnalyticsPage(payload.page);
      }).catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setAnalyticsError(error instanceof Error ? error.message : "無法讀取已解析內容資料");
      }).finally(() => {
        if (!controller.signal.aborted) setAnalyticsLoading(false);
      });
    }, 0);
    return () => {
      window.clearTimeout(request);
      controller.abort();
    };
  }, [
    creator?.owned_channel_id,
    analyticsQuery,
    analyticsDateStart,
    analyticsDateEnd,
    analyticsReport,
    analyticsRowKind,
    analyticsStatus,
    analyticsFormat,
    analyticsTopic,
    analyticsSort,
    analyticsDirection,
    analyticsPage,
    analyticsPageSize,
    analyticsRevision,
  ]);

  const changeAnalyticsSort = (field: AnalyticsSort) => {
    setAnalyticsPage(1);
    if (analyticsSort === field) {
      setAnalyticsDirection((current) => current === "asc" ? "desc" : "asc");
      return;
    }
    setAnalyticsSort(field);
    setAnalyticsDirection("desc");
  };

  const searchAnalytics = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAnalyticsPage(1);
    setAnalyticsQuery(analyticsQueryDraft.trim());
  };

  const confirmChannelChange = () => pending.length === 0 || window.confirm(
    "目前有尚未匯入的檔案預覽。切換頻道會清除這些待匯入檔案，確定繼續嗎？",
  );

  const clearChannelDrafts = () => {
    setPending([]);
    setManualValue("");
    setManualVideoId("");
    setManualNote("");
  };

  const saveOwnedChannel = async () => {
    if (!channelChoice) return;
    if (!confirmChannelChange()) return;
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
      setChannelChoice("");
      clearChannelDrafts();
      setMessage(payload.message ?? "管理頻道已加入工作區");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法設定頻道");
    } finally {
      setSavingChannel(false);
    }
  };

  const selectWorkspaceChannel = async (channelId: string) => {
    if (creator?.owned_channel_id === channelId) return;
    if (!confirmChannelChange()) return;
    setSavingChannel(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/creator`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel_id: channelId }),
      });
      const payload = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法切換管理頻道");
      setMessage(payload.message ?? "已切換管理頻道");
      clearChannelDrafts();
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法切換管理頻道");
    } finally {
      setSavingChannel(false);
    }
  };

  const removeWorkspaceChannel = async (channel: WorkspaceChannel) => {
    if (channel.channel_id === creator?.owned_channel_id && !confirmChannelChange()) return;
    if (!window.confirm(`將「${channel.title}」移出這個工作區嗎？\n\n公開監測、Studio 匯入與手動補充資料都會保留，之後可重新加入。`)) return;
    setSavingChannel(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/creator/channels/${encodeURIComponent(channel.channel_id)}`, { method: "DELETE" });
      const payload = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法移出工作區");
      setMessage(payload.message ?? "已移出工作區");
      if (channel.channel_id === creator?.owned_channel_id) clearChannelDrafts();
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法移出工作區");
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
    if (!confirmChannelChange()) return;
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
      if (!response.ok) throw new Error(payload.error ?? "無法加入頻道工作區");
      setMessage(payload.message ?? "管理頻道已加入工作區");
      setChannelCandidates([]);
      setChannelQuery("");
      clearChannelDrafts();
      if (!candidate.already_added) {
        setClassificationChannel(candidate);
        setClassificationCategory("未分類");
        setClassificationOrganization("");
        setClassificationTags("");
        setClassificationActivity("自動判斷");
      }
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法加入頻道工作區");
    } finally {
      setAddingChannelId(null);
    }
  };

  const saveChannelClassification = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!classificationChannel) return;
    setAddingChannelId(classificationChannel.channel_id);
    try {
      const response = await fetch(`${API_BASE}/api/channels/${encodeURIComponent(classificationChannel.channel_id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category: classificationCategory,
          organization_name: classificationOrganization,
          manual_tags: classificationTags.split(/[、,，#\n]/).map((tag) => tag.trim()).filter(Boolean),
          activity_status: classificationActivity,
        }),
      });
      const payload = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法儲存分類");
      setClassificationChannel(null);
      setMessage(payload.message ?? "頻道分類已儲存");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法儲存分類");
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
          body: JSON.stringify({ filename: item.filename, content_base64: item.content_base64, channel_id: creator?.owned_channel_id }),
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
        body: JSON.stringify({ channel_id: creator?.owned_channel_id, metric_date: manualDate, metric_name: manualName, metric_value: manualValue, video_id: manualVideoId, note: manualNote }),
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

  const configureOAuth = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 128 * 1024) {
      setMessage("OAuth JSON 不可超過 128 KB");
      return;
    }
    setOauthBusy(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/creator/oauth/client`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content_base64: await filePayload(file) }),
      });
      const payload = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法保存 OAuth 設定");
      setMessage(payload.message ?? "OAuth 設定已保存");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法保存 OAuth 設定");
    } finally {
      setOauthBusy(false);
    }
  };

  const connectOAuth = () => {
    window.open(`${API_BASE}/api/creator/oauth/start`, "_blank", "popup,width=720,height=760");
    setMessage("已開啟 Google 授權頁；完成後這裡會自動更新，或可按右上角「更新資料」。");
  };

  const syncOAuth = async () => {
    setOauthSyncing(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/creator/oauth/sync`, { method: "POST" });
      const payload = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法啟動 Analytics 同步");
      setMessage("正在背景同步 YouTube Analytics；完成或失敗後會自動顯示結果。");
    } catch (error) {
      setOauthSyncing(false);
      setMessage(error instanceof Error ? error.message : "無法啟動 Analytics 同步");
    }
  };

  const disconnectOAuth = async () => {
    if (!window.confirm("中斷 YouTube 連線嗎？\n\n台V Pulse 會向 Google 撤銷授權，並刪除本機 token 與直接同步的 Analytics 資料；公開監測與 Studio 匯入不受影響。")) return;
    setOauthBusy(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/creator/oauth`, { method: "DELETE" });
      const payload = await response.json() as { message?: string; warning?: string | null; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法中斷連線");
      setMessage(payload.warning ? `${payload.message}；${payload.warning}` : payload.message ?? "已中斷連線");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法中斷連線");
    } finally {
      setOauthBusy(false);
    }
  };

  const deleteOAuthClient = async () => {
    if (!window.confirm("刪除這台電腦上的 OAuth 用戶端設定嗎？")) return;
    setOauthBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/creator/oauth/client`, { method: "DELETE" });
      const payload = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法刪除 OAuth 設定");
      setMessage(payload.message ?? "OAuth 設定已刪除");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法刪除 OAuth 設定");
    } finally {
      setOauthBusy(false);
    }
  };

  const overviewCards = useMemo(() => [
    ["views", "匯入期間觀看", (value: number) => compact(value)],
    ["engaged_views", "互動觀看", (value: number) => compact(value)],
    ["watch_time_hours", "觀看時間", (value: number) => `${exact(value, 1)} 小時`],
    ["impressions", "曝光次數", (value: number) => compact(value)],
    ["impressions_ctr", "曝光點閱率", (value: number) => `${exact(value, 2)}%`],
    ["subscribers_net", "訂閱淨變化", (value: number) => exact(value)],
    ["average_view_duration_seconds", "平均觀看時間", (value: number) => `${exact(value)} 秒`],
    ["average_percentage_viewed", "平均觀看比例", (value: number) => `${exact(value, 2)}%`],
    ["unique_viewers", "不重複觀眾", (value: number) => compact(value)],
    ["returning_viewers", "回訪觀眾", (value: number) => compact(value)],
    ["likes", "喜歡次數", (value: number) => compact(value)],
    ["comments", "留言數", (value: number) => compact(value)],
    ["shares", "分享次數", (value: number) => compact(value)],
  ] as const, []);

  const workspaceIds = useMemo(
    () => new Set((creator?.workspace_channels ?? []).map((channel) => channel.channel_id)),
    [creator?.workspace_channels],
  );
  const availableChannels = useMemo(
    () => (summary?.channels ?? []).filter((channel) => !workspaceIds.has(channel.channel_id)),
    [summary?.channels, workspaceIds],
  );
  const workspaceTotals = useMemo(() => {
    const channels = creator?.workspace_channels ?? [];
    const totals = channels.reduce((current, channel) => ({
      subscribers: current.subscribers + Number(channel.subscriber_count ?? 0),
      views: current.views + Number(channel.view_count ?? 0),
      videos: current.videos + Number(channel.video_count ?? 0),
      subscriberDelta: current.subscriberDelta + Number(channel.subscriber_count_delta_30d ?? 0),
    }), { subscribers: 0, views: 0, videos: 0, subscriberDelta: 0 });
    return {
      subscribers: channels.length > 0 && channels.every((channel) => channel.subscriber_count !== null) ? totals.subscribers : null,
      views: channels.length > 0 && channels.every((channel) => channel.view_count !== null) ? totals.views : null,
      videos: channels.length > 0 && channels.every((channel) => channel.video_count !== null) ? totals.videos : null,
      subscriberDelta: channels.length > 0 && channels.every((channel) => channel.month_ready) ? totals.subscriberDelta : null,
    };
  }, [creator?.workspace_channels]);
  const publicDelta = useMemo(() => {
    const snapshots = creator?.public?.snapshots ?? [];
    if (snapshots.length < 2) return null;
    const first = snapshots[0];
    const last = snapshots[snapshots.length - 1];
    return {
      subscribers: first.subscriber_count === null || last.subscriber_count === null ? null : last.subscriber_count - first.subscriber_count,
      views: first.view_count === null || last.view_count === null ? null : last.view_count - first.view_count,
      videos: first.video_count === null || last.video_count === null ? null : last.video_count - first.video_count,
      start: first.captured_at,
      end: last.captured_at,
    };
  }, [creator?.public?.snapshots]);

  return (
    <main className="app-shell creator-shell">
      <SiteHeader active="creator" eyebrow="PRIVATE CREATOR ANALYTICS" title="頻道工作區" connected={connected} actions={<button className="button ghost" type="button" onClick={() => void refresh()} disabled={loading}>更新資料</button>} />

      {!connected && <section className="notice warning"><span className="notice-icon">!</span><div><strong>等待本機資料服務</strong><p>啟動台V Pulse 後，這頁會讀取既有公開監測資料與你的私人匯入資料。</p></div></section>}
      {message && <section className="inline-message">{message}</section>}
      {loading && <section className="panel creator-placeholder">正在整理你的頻道資料…</section>}

      {!loading && creator && <section className="panel workspace-mode-panel">
        <div><p className="section-kicker">WORKSPACE VIEW</p><h2>先看個人頻道，或切到團隊總覽</h2><p>模式只切換上方公開摘要；目前選取頻道、篩選條件與下方展開內容都會保留。</p></div>
        <div className="workspace-mode-switch" role="group" aria-label="工作區檢視模式">
          <button className={workspaceMode === "personal" ? "active" : ""} type="button" aria-pressed={workspaceMode === "personal"} onClick={() => setWorkspaceMode("personal")}><strong>個人</strong><span>目前頻道資料</span></button>
          <button className={workspaceMode === "team" ? "active" : ""} type="button" aria-pressed={workspaceMode === "team"} onClick={() => setWorkspaceMode("team")}><strong>團隊</strong><span>{creator.workspace_channels.length} 個管理頻道</span></button>
        </div>
      </section>}

      {!loading && creator && workspaceMode === "personal" && (creator.channel ? <>
        <section className="panel creator-profile">
          <div className="creator-identity">{creator.channel.thumbnail_url ? <img src={creator.channel.thumbnail_url} alt="" /> : <span>V</span>}<div><p className="section-kicker">{creator.channel.category}{creator.channel.organization_name ? ` · ${creator.channel.organization_name}` : ""}</p><h2>{creator.channel.title}</h2><p>{creator.channel.handle ?? creator.channel.channel_id}</p><div className="tag-row">{creator.channel.manual_tags.map((tag) => <i key={tag}>#{tag}</i>)}</div></div></div>
          <a className="button external-button" href={creator.channel.handle ? `https://www.youtube.com/${creator.channel.handle}` : `https://www.youtube.com/channel/${creator.channel.channel_id}`} target="_blank" rel="noreferrer">開啟 YouTube ↗</a>
        </section>
        <section className="creator-public-grid">
          <article className="insight-hero primary"><span>公開訂閱</span><strong>{compact(creator.channel.subscriber_count)}</strong><p>監測首頁 · {dateTime(creator.channel.last_stats_at)}</p></article>
          <article className="insight-hero"><span>頻道總觀看</span><strong>{compact(creator.channel.view_count)}</strong><p>公開累積值</p></article>
          <article className="insight-hero"><span>公開影片數</span><strong>{compact(creator.channel.video_count)}</strong><p>目前 YouTube API 統計</p></article>
          <article className="insight-hero"><span>歷史最高同接</span><strong>{compact(creator.public?.peak_concurrent)}</strong><p>{exact(creator.public?.concurrency_sample_count)} 個同接資料點</p></article>
        </section>
      </> : <section className="panel workspace-mode-empty"><strong>尚未選定個人頻道</strong><p>從下方管理區加入監測中的頻道，或使用私人 Analytics 連線自動辨識自己的頻道。</p><button className="button primary" type="button" onClick={() => document.getElementById("creator-channel-management")?.scrollIntoView({ behavior: "smooth", block: "start" })}>前往加入頻道</button></section>)}

      {!loading && creator && workspaceMode === "team" && <section className="panel workspace-overview-panel">
        <div className="panel-heading"><div><p className="section-kicker">MANAGED CHANNELS</p><h2>管理頻道與團隊比較</h2></div><span>{creator.workspace_channels.length} 個頻道 · 全部為公開監測統計</span></div>
        {creator.workspace_channels.length > 0 && <><div className="workspace-total-grid"><article><span>合計訂閱</span><strong>{compact(workspaceTotals.subscribers)}</strong><small>30天 {signed(workspaceTotals.subscriberDelta)}</small></article><article><span>合計觀看</span><strong>{compact(workspaceTotals.views)}</strong><small>公開累積值</small></article><article><span>合計影片</span><strong>{compact(workspaceTotals.videos)}</strong><small>包含直播存檔</small></article></div>
        <div className="workspace-channel-grid">{creator.workspace_channels.map((channel) => <article className={channel.channel_id === creator.owned_channel_id ? "active" : ""} key={channel.channel_id}><button className="workspace-channel-main" type="button" onClick={() => void selectWorkspaceChannel(channel.channel_id)} disabled={savingChannel}>{channel.thumbnail_url ? <img src={channel.thumbnail_url} alt="" /> : <span className="workspace-avatar">V</span>}<span><b>{channel.title}</b><small>{channel.organization_name || channel.category} · {channel.activity_status ?? "狀態不明"}</small></span>{channel.channel_id === creator.owned_channel_id && <i>目前查看</i>}</button><dl><div><dt>訂閱</dt><dd>{compact(channel.subscriber_count)}</dd></div><div><dt>30天變化</dt><dd>{signed(channel.subscriber_count_delta_30d)}</dd></div><div><dt>近30天內容</dt><dd>{exact(channel.recent_content_count_30d)}</dd></div><div><dt>最高同接</dt><dd>{compact(channel.peak_concurrent)}</dd></div></dl><button className="workspace-remove" type="button" onClick={() => void removeWorkspaceChannel(channel)} disabled={savingChannel}>移出工作區</button></article>)}</div>
        <div className="table-wrap workspace-comparison-table"><table><thead><tr><th>頻道</th><th>訂閱</th><th>30天訂閱</th><th>總觀看</th><th>30天觀看</th><th>近30天內容</th><th>最高同接</th><th>資料點</th></tr></thead><tbody>{creator.workspace_channels.map((channel) => <tr key={channel.channel_id}><td><strong>{channel.title}</strong><small className="table-subline">{channel.organization_name || channel.category}</small></td><td>{exact(channel.subscriber_count)}</td><td>{signed(channel.subscriber_count_delta_30d)}</td><td>{compact(channel.view_count)}</td><td>{signed(channel.view_count_delta_30d)}</td><td>{exact(channel.recent_content_count_30d)}</td><td>{exact(channel.peak_concurrent)}</td><td>{exact(channel.concurrency_sample_count)}</td></tr>)}</tbody></table></div></>}
        <div className="workspace-team-add"><div><strong>{creator.workspace_channels.length === 0 ? "先加入第一個管理頻道" : "繼續擴充團隊"}</strong><p>新增只沿用公開監測資料；私人 OAuth、Studio 與手動補值仍各自綁定選取頻道。</p></div><button className="button primary" type="button" onClick={() => document.getElementById("creator-channel-management")?.scrollIntoView({ behavior: "smooth", block: "start" })}>加入管理頻道</button></div>
      </section>}

      {!loading && summary && (creator?.workspace_channels.length ?? 0) === 0 && <section className="panel creator-onboarding">
        <div className="onboarding-heading"><p className="section-kicker">START HERE</p><h2>先連結自己的頻道，或加入團隊管理頻道</h2><p>個人可用下方 OAuth 唯讀連線自動辨識自己的頻道；企業 STAFF 仍可加入多位藝人。工作區沿用監測首頁同一筆公開紀錄，私人來源各自分開保存。</p></div>
        <ol><li><strong>1</strong><span>連結自己或加入管理頻道</span></li><li><strong>2</strong><span>立即沿用公開監測與歷史快照</span></li><li><strong>3</strong><span>每日同步自己的私人 Analytics</span></li></ol>
        <div className="data-flow"><span>公開監測資料</span><b>＋</b><span>OAuth 私人 Analytics</span><b>→</b><strong>團隊比較與個別分析</strong></div>
      </section>}

      {!loading && summary && <section className="panel creator-channel-picker" id="creator-channel-management">
        <div><p className="section-kicker">ADD MANAGED CHANNEL</p><h2>從監測首頁加入管理頻道</h2><p>已經收錄的頻道會直接沿用現有數據，不會複製或重新消耗 API；加入後可在上方卡片切換。</p></div>
        <label><span>尚未加入工作區的頻道</span><select value={channelChoice} onChange={(event) => setChannelChoice(event.target.value)}><option value="">請選擇頻道</option>{availableChannels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.title}｜{compact(channel.subscriber_count)} 訂閱</option>)}</select></label>
        <button className="button primary" type="button" onClick={() => void saveOwnedChannel()} disabled={!channelChoice || savingChannel}>{savingChannel ? "加入中…" : "加入工作區"}</button>
      </section>}

      {!loading && summary && <section className="panel creator-channel-search">
        <div className="panel-heading"><div><p className="section-kicker">DIRECT LOOKUP</p><h2>搜尋並加入管理頻道</h2></div><span>{summary.search_quota_available ? "名稱、網址、@handle 或 Channel ID" : "搜尋配額已滿：請使用網址、@handle 或 Channel ID"}</span></div>
        <form className="specific-form" onSubmit={(event) => void searchOwnedChannel(event)}><label><span>藝人或自己的 YouTube 頻道</span><input value={channelQuery} onChange={(event) => setChannelQuery(event.target.value)} placeholder="貼上頻道網址、@handle、Channel ID 或名稱" /></label><button className="button primary" type="submit" disabled={!connected || searchingChannel || !channelQuery.trim()}>{searchingChannel ? "搜尋中…" : "搜尋頻道"}</button></form>
        {channelCandidates.length > 0 && <div className="candidate-list">{channelCandidates.map((candidate) => <article className="candidate-card" key={candidate.channel_id}>{candidate.thumbnail_url ? <img src={candidate.thumbnail_url} alt="" /> : <span className="candidate-avatar">V</span>}<div className="candidate-copy"><div className="candidate-title"><strong>{candidate.title}</strong><span>{candidate.hidden_subscriber_count ? "訂閱未公開" : `${compact(candidate.subscriber_count)} 訂閱`}</span></div><small>{candidate.handle ?? candidate.channel_id}</small><p>{candidate.description || "這個頻道沒有公開說明。"}</p>{!candidate.meets_threshold && <p className="owned-exception-note">未達一般收錄門檻也能加入工作區，但不會納入台 V 整體比較。</p>}</div><button className="button candidate-action" type="button" onClick={() => void chooseOwnedCandidate(candidate)} disabled={addingChannelId === candidate.channel_id || workspaceIds.has(candidate.channel_id)}>{addingChannelId === candidate.channel_id ? "設定中…" : workspaceIds.has(candidate.channel_id) ? "已在工作區" : candidate.already_added ? "加入工作區" : "收錄並加入"}</button></article>)}</div>}
        <p className="panel-footnote">你也可以回到 <Link href="/">監測首頁</Link> 先確認公開資料。URL、@handle 與 Channel ID 不使用搜尋配額。</p>
      </section>}

      {!loading && creator && <section className={`panel oauth-connect-panel${creator.oauth.status.last_error ? " has-error" : ""}`}>
        <div className="panel-heading"><div><p className="section-kicker">PRIVATE ANALYTICS CONNECTION</p><h2>私人 Analytics 連線</h2></div><span>{oauthSyncing || creator.oauth.status.syncing ? "同步中…" : creator.oauth.status.connected ? "已連線 · 每日自動同步" : creator.oauth.status.configured ? "等待 Google 授權" : "尚未設定"}</span></div>
        <div className="oauth-connection-summary">
          <div className="oauth-channel"><span className={creator.oauth.status.last_error ? "error-badge" : creator.oauth.status.connected ? "state-badge" : "muted-badge"}>{creator.oauth.status.last_error ? "需要處理" : creator.oauth.status.connected ? "已連線" : "未連線"}</span><div><strong>{creator.oauth.status.channel?.title ?? "直接連結我的 YouTube 頻道"}</strong><p>{creator.oauth.status.connected ? `${creator.oauth.status.last_data_date ? `資料到 ${creator.oauth.status.last_data_date}` : "資料累積中"} · 每日自動同步${creator.oauth.status.last_sync_at ? ` · 最近同步 ${dateTime(creator.oauth.status.last_sync_at)}` : ""}` : creator.oauth.status.configured ? "OAuth JSON 已加密保存，可繼續 Google 唯讀授權。" : "設定自己的 OAuth JSON 後，才會讀取私人 Analytics。"}</p></div></div>
          <div className="oauth-action-buttons">{creator.oauth.status.connected && <button className="button primary" type="button" onClick={() => void syncOAuth()} disabled={oauthBusy || oauthSyncing || creator.oauth.status.syncing}>{oauthSyncing || creator.oauth.status.syncing ? "同步中…" : "立即同步"}</button>}{creator.oauth.status.configured && !creator.oauth.status.connected && <button className="button primary" type="button" onClick={connectOAuth} disabled={oauthBusy}>繼續 Google 授權</button>}<button className="button ghost" type="button" aria-expanded={oauthManagerOpen} aria-controls="oauth-connection-management" onClick={() => setOauthManagerOpen((current) => !current)}>{oauthManagerOpen ? "收合連線管理" : creator.oauth.status.configured ? "管理連線" : "設定私人分析連線"}</button></div>
        </div>
        {oauthManagerOpen && <div className="oauth-management" id="oauth-connection-management">
          <div className="oauth-security-note"><strong>只讀取，不代替你操作頻道</strong><p>只申請 YouTube 帳戶唯讀與 Analytics 唯讀權限；不能上傳、刪除、修改影片，也不讀取收益。OAuth 設定與 token 只在這台 Windows 電腦以 DPAPI 加密保存。</p></div>
          {!creator.oauth.status.configured && <div className="oauth-setup-grid"><div><strong>1. 建立自己的桌面應用程式 OAuth</strong><p>在 Google Cloud 啟用 YouTube Data API v3 與 YouTube Analytics API，建立「桌面應用程式」OAuth 用戶端，再下載 JSON。</p><a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">開啟 Google Cloud 憑證頁 ↗</a></div><label className="oauth-file-button"><span>2. 匯入 OAuth JSON</span><input type="file" accept=".json,application/json" onChange={(event) => void configureOAuth(event)} disabled={oauthBusy} /><strong>{oauthBusy ? "加密保存中…" : "選擇 JSON 檔"}</strong></label></div>}
          {creator.oauth.status.configured && !creator.oauth.status.connected && <div className="oauth-actions"><div><strong>OAuth 設定已加密保存</strong><p>下一步會在 Google 官方頁面登入並確認兩項唯讀權限。完成後 Google 將導回本機服務。</p></div><div className="oauth-action-buttons"><button className="button primary" type="button" onClick={connectOAuth} disabled={oauthBusy}>連結我的 YouTube 頻道</button><button className="button ghost" type="button" onClick={() => void deleteOAuthClient()} disabled={oauthBusy}>刪除設定</button></div></div>}
          {creator.oauth.status.connected && <div className="oauth-actions"><div><strong>已連結 {creator.oauth.status.channel?.title}</strong><p>私人資料只屬於這個頻道，不會加入團隊公開合計或其他頻道比較。</p></div><button className="danger-button" type="button" onClick={() => void disconnectOAuth()} disabled={oauthBusy || oauthSyncing || creator.oauth.status.syncing}>中斷並刪除同步資料</button></div>}
          {creator.oauth.status.last_error_help && <section className="oauth-error" role="status"><strong>{creator.oauth.status.last_error_help.title}</strong><p>{creator.oauth.status.last_error_help.message}</p><ol>{creator.oauth.status.last_error_help.steps.map((step) => <li key={step}>{step}</li>)}</ol>{creator.oauth.status.last_error_help.help_url && creator.oauth.status.last_error_help.help_label && <a href={creator.oauth.status.last_error_help.help_url} target="_blank" rel="noreferrer">{creator.oauth.status.last_error_help.help_label} ↗</a>}<details><summary>查看 Google 技術細節</summary><code>{creator.oauth.status.last_error}</code></details></section>}
          <details className="oauth-troubleshooting"><summary><span><strong>連線或同步遇到問題？</strong><small>403、API 未啟用、七天後失效、沒資料與按鈕狀態</small></span><i>查看解法</i></summary><div className="oauth-troubleshooting-grid"><article><strong>403：應用程式未完成驗證</strong><p>在建立 OAuth JSON 的同一個專案，進入 Google Auth Platform「目標對象」，把目前登入的完整 Google 帳號加入測試使用者。專案擁有者也不一定會自動加入。</p><a href="https://console.cloud.google.com/auth/audience" target="_blank" rel="noreferrer">設定測試使用者 ↗</a></article><article><strong>Analytics API 尚未啟用</strong><p>OAuth 成功不代表 Analytics API 已啟用。確認同一專案同時啟用 YouTube Data API v3 與 YouTube Analytics API，等待 1～5 分鐘再同步。</p><a href="https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com" target="_blank" rel="noreferrer">啟用 Analytics API ↗</a></article><article><strong>七天後突然失效</strong><p>Google OAuth 若仍是「測試」發布狀態，refresh token 通常七天後失效。重新連線可暫時恢復；長期每日同步需將發布狀態改為正式環境。</p></article><article><strong>已連線但沒有資料</strong><p>先等待目前同步完成；Google 尚未產生可用報表列、頻道較新或期間沒有活動時可能仍顯示「—」。這不是 0，也不會用猜測值補上。</p></article><article><strong>匯入了錯誤專案的 JSON</strong><p>先中斷連線，再刪除 OAuth 設定，改匯入已啟用兩個 YouTube API 的正確桌面應用程式 JSON。不要編輯 JSON 或把它傳給別人。</p></article><article><strong>按立即同步像沒反應</strong><p>按下後會持續顯示「同步中…」並每兩秒確認狀態；完成後顯示資料日期，失敗則顯示繁中原因、操作步驟與可展開的技術原文。</p></article></div></details>
          <p className="panel-footnote">若 Google OAuth 同意畫面仍為「測試」狀態，refresh token 通常會在 7 天後失效；個人使用請在 Google Cloud 將應用程式發布到正式環境。不要把下載的 OAuth JSON 分享或放進 Git。</p>
        </div>}
      </section>}

      {creator?.channel && <>
        <section className="panel public-monitor-panel">
          <div className="panel-heading"><div><p className="section-kicker">PUBLIC MONITOR SYNC</p><h2>已自動沿用監測首頁資料</h2></div><span>{creator.public?.snapshots.length ?? 0} 個頻道快照 · {creator.public?.videos.length ?? 0} 筆內容</span></div>
          <div className="public-source-note"><strong>不另存一份副本</strong><p>這裡直接讀取監測首頁的同一筆頻道、影片與同接紀錄；監測更新後，工作區會同步顯示。私人 Analytics 會另以 OAuth、Studio 匯入或手動補值標示來源。</p></div>
          <div className="public-delta-grid"><article><span>快照期間訂閱</span><strong>{publicDelta ? signed(publicDelta.subscribers) : "資料累積中"}</strong><small>{publicDelta ? `${dateTime(publicDelta.start)} 起` : "至少需要兩個快照"}</small></article><article><span>快照期間觀看</span><strong>{publicDelta ? signed(publicDelta.views) : "資料累積中"}</strong><small>公開觀看累積差</small></article><article><span>快照期間內容</span><strong>{publicDelta ? signed(publicDelta.videos) : "資料累積中"}</strong><small>公開影片數差</small></article></div>
          <div className="table-wrap public-snapshot-table"><table><thead><tr><th>監測時間</th><th>訂閱</th><th>總觀看</th><th>影片數</th></tr></thead><tbody>{(creator.public?.snapshots ?? []).length === 0 ? <tr><td colSpan={4} className="table-empty">目前只有最新頻道統計，後續監測會逐步累積歷史。</td></tr> : (creator.public?.snapshots ?? []).slice(-12).reverse().map((snapshot) => <tr key={snapshot.captured_at}><td>{dateTime(snapshot.captured_at)}</td><td>{exact(snapshot.subscriber_count)}</td><td>{exact(snapshot.view_count)}</td><td>{exact(snapshot.video_count)}</td></tr>)}</tbody></table></div>
        </section>

        <section className="panel public-content-panel">
          <div className="panel-heading"><div><p className="section-kicker">MONITORED CONTENT</p><h2>監測首頁已抓到的近期內容</h2></div><span>顯示最近 8 筆</span></div>
          <div className="public-content-grid">{(creator.public?.videos ?? []).length === 0 ? <div className="table-empty">尚未掃描到影片或直播；完成最新上傳掃描後會自動出現。</div> : (creator.public?.videos ?? []).slice(0, 8).map((video) => <article key={video.video_id}>{video.thumbnail_url ? <img src={video.thumbnail_url} alt="" /> : <span className="content-thumb-fallback">V</span>}<div><strong>{video.title}</strong><p>{video.live_state === "completed" ? "直播存檔" : video.live_state === "live" ? "直播中" : video.live_state === "upcoming" ? "預定直播" : "影片"} · {compact(video.view_count)} 觀看{video.peak_concurrent === null ? "" : ` · 最高同接 ${exact(video.peak_concurrent)}`}</p></div><a href={`https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer">開啟 ↗</a></article>)}</div>
        </section>

        {creator.oauth.status.connected && creator.oauth.status.channel?.channel_id === creator.channel.channel_id && <section className="panel oauth-analytics-panel">
          <div className="panel-heading"><div><p className="section-kicker">PRIVATE ANALYTICS SYNC</p><h2>Google 唯讀同步摘要</h2></div><span>{creator.oauth.summary ? `${creator.oauth.summary.date_start}～${creator.oauth.summary.date_end}` : "資料累積中"}</span></div>
          <p className="analytics-classification-note">這區只顯示 OAuth 直接取得的私人 Analytics，與公開監測、Studio 匯入及手動補值分開保存。最多同步最近 365 天，並受「私人資料保存」設定限制；Google 尚未提供或不相容的欄位顯示「—」。</p>
          <div className="creator-private-grid oauth-summary-grid">{overviewCards.filter(([key]) => !["impressions", "impressions_ctr", "returning_viewers"].includes(key)).map(([key, label, formatter]) => <article key={key}><span>{label}</span><strong>{creator.oauth.summary?.metrics[key as keyof OAuthMetrics] === undefined ? "—" : formatter(creator.oauth.summary.metrics[key as keyof OAuthMetrics]!)}</strong><p>Google Analytics API 唯讀同步</p></article>)}</div>
          <div className="oauth-data-columns">
            <article><div className="panel-heading"><div><h3>最近每日資料</h3></div><span>最新 14 天</span></div><div className="table-wrap"><table><thead><tr><th>日期</th><th>觀看</th><th>觀看時數</th><th>訂閱淨變化</th><th>不重複觀眾</th></tr></thead><tbody>{creator.oauth.daily.length === 0 ? <tr><td colSpan={5} className="table-empty">尚未取得每日 Analytics。</td></tr> : creator.oauth.daily.slice(0, 14).map((row) => <tr key={row.event_date}><td>{row.event_date}</td><td>{exact(row.metrics.views)}</td><td>{exact(row.metrics.watch_time_hours, 1)}</td><td>{signed(row.metrics.subscribers_net)}</td><td>{exact(row.metrics.unique_viewers)}</td></tr>)}</tbody></table></div></article>
            <article><div className="panel-heading"><div><h3>期間觀看最高內容</h3></div><span>前 10 筆</span></div><div className="table-wrap oauth-video-table"><table><thead><tr><th>內容與發布日期</th><th>觀看</th><th>觀看時數</th><th>訂閱淨變化</th></tr></thead><tbody>{creator.oauth.videos.length === 0 ? <tr><td colSpan={4} className="table-empty">尚未取得內容 Analytics。</td></tr> : creator.oauth.videos.slice(0, 10).map((row) => <tr className="oauth-video-row" key={row.video_id} tabIndex={0} aria-label={`在新分頁開啟 ${row.title ?? "這支影片"}`} onClick={(event) => { if ((event.target as HTMLElement).closest?.("a")) return; openYouTubeVideo(row.video_id); }} onKeyDown={(event) => { if (event.target !== event.currentTarget || !["Enter", " "].includes(event.key)) return; event.preventDefault(); openYouTubeVideo(row.video_id); }}><td><a className="oauth-video-link" href={`https://www.youtube.com/watch?v=${row.video_id}`} target="_blank" rel="noreferrer">{row.thumbnail_url ? <img src={row.thumbnail_url} alt="" /> : <span className="oauth-video-thumb-fallback">V</span>}<span><strong>{row.title ?? "影片標題尚未取得"}</strong><small>{creator.channel?.title ?? creator.oauth.status.channel?.title ?? "頻道名稱尚未取得"}</small><small>發布／直播日期：{dateTime(row.content_date)}</small></span></a></td><td>{exact(row.metrics.views)}</td><td>{exact(row.metrics.watch_time_hours, 1)}</td><td>{signed(row.metrics.subscribers_net)}</td></tr>)}</tbody></table></div></article>
          </div>
        </section>}

        <details className="creator-advanced-tools">
          <summary><span><strong>進階備援：Studio 匯入與手動補值</strong><small>直接連線未包含的報表欄位，或 OAuth 暫時不可用時再使用</small></span><i>展開</i></summary>
        <section className="creator-two-column">
          <article className="panel creator-import-panel">
            <div className="panel-heading"><div><p className="section-kicker">YOUTUBE STUDIO IMPORT</p><h2>匯入私人 Analytics</h2></div><span>目前歸入：{creator.channel.title}</span></div>
            <div className="import-dropzone"><input type="file" accept=".csv,.tsv,.zip,text/csv,text/tab-separated-values,application/zip" multiple onChange={(event) => void prepareFiles(event)} disabled={preparing || importing} /><strong>{preparing ? "正在檢查報表…" : "選擇 YouTube Studio 匯出檔"}</strong><p>可匯入 CSV、TSV，或內含 CSV／TSV 的 ZIP；目前不支援 XLSX。先預覽欄位與列數，確認後才寫入。</p></div>
            {pending.length > 0 && <div className="upload-preview-list">{pending.map((item) => <article className={item.error ? "invalid" : ""} key={item.filename}><div><strong>{item.filename}</strong><span>{(item.size / 1024).toFixed(1)} KB</span></div>{item.error ? <p>{item.error}</p> : item.preview && <><p>{item.preview.report_count} 份報表 · {item.preview.row_count} 列 · 已使用 {item.preview.recognized_column_count} 個欄位（{item.preview.recognized_metrics.map((metric) => METRIC_LABELS[metric] ?? metric).join("、")}） · {item.preview.date_start ? `${item.preview.date_start}～${item.preview.date_end}` : "內容報表未含每日日期欄"}</p>{item.preview.ignored_headers.length > 0 && <small className="ignored-columns-note">尚未納入摘要的欄位：{item.preview.ignored_headers.slice(0, 8).join("、")}{item.preview.ignored_headers.length > 8 ? `，另 ${item.preview.ignored_headers.length - 8} 欄` : ""}</small>}</>}</article>)}</div>}
            {pending.some((item) => item.preview) && <div className="import-actions"><button className="button ghost" type="button" onClick={() => setPending([])} disabled={importing}>取消</button><button className="button primary" type="button" onClick={() => void importFiles()} disabled={importing}>{importing ? "匯入中…" : "確認匯入"}</button></div>}
            <div className="import-support-note"><strong>可解析的資料</strong><p>日期、影片 ID、影片標題、發布時間、長度，以及觀看、互動觀看、觀看時數、平均觀看、觀看比例、曝光、點閱率、訂閱增減、喜歡、留言、分享、不重複觀眾、回訪觀眾與預估收益。實際結果以匯出檔包含的欄位為準。</p><small>每個檔案上限 8 MB；ZIP 最多解析 20 份 CSV／TSV。原始檔不會保存，只留下解析後數值、來源批次與檔案雜湊。</small></div>
            <p className="panel-footnote">YouTube Studio「進階模式」可匯出目前報表；單次介面匯出最多 500 列。XLSX 內的圖表或額外工作表目前不會匯入。<a href="https://support.google.com/youtube/answer/9717005?hl=zh-Hant" target="_blank" rel="noreferrer">查看官方說明 ↗</a></p>
          </article>

          <aside className="panel creator-privacy-panel">
            <div className="panel-heading"><div><p className="section-kicker">LOCAL & PRIVATE</p><h2>資料怎麼合併</h2></div></div>
            <dl className="rules-list"><div><dt>相同檔案</dt><dd>整份略過</dd></div><div><dt>相同資料列</dt><dd>不重複寫入</dd></div><div><dt>同鍵不同值</dt><dd>標示衝突，最新批次顯示</dd></div><div><dt>手動補值</dt><dd>獨立保存，不覆蓋原始列</dd></div><div><dt>原始檔</dt><dd>解析後立即丟棄</dd></div></dl>
            <p className="creator-private-note">收益等敏感欄位只有在匯出檔包含時才會保存，並且綁定目前選取的藝人頻道；整個 work 資料夾不會上傳 GitHub。</p>
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
          <div className="panel-heading"><div><p className="section-kicker">PARSED PRIVATE CONTENT</p><h2>已解析內容資料</h2></div><span>{analyticsLoading ? "背景更新中…" : analytics ? `${analytics.result_count} / ${analytics.available_count} 筆` : "讀取中…"}</span></div>
          <div className="analytics-filter-grid">
            <form className="analytics-query" onSubmit={searchAnalytics}><label><span>搜尋內容或遊戲名稱</span><input value={analyticsQueryDraft} onChange={(event) => setAnalyticsQueryDraft(event.target.value)} placeholder="影片標題、影片 ID 或報表名稱" /></label><button className="button ghost" type="submit">搜尋</button></form>
            <label><span>開始日期</span><input type="date" value={analyticsDateStart} onChange={(event) => { setAnalyticsPage(1); setAnalyticsDateStart(event.target.value); }} /></label>
            <label><span>結束日期</span><input type="date" value={analyticsDateEnd} onChange={(event) => { setAnalyticsPage(1); setAnalyticsDateEnd(event.target.value); }} /></label>
            <label><span>來源報表</span><select value={analyticsReport} onChange={(event) => { setAnalyticsPage(1); setAnalyticsReport(event.target.value); }}><option value="all">全部報表</option>{(analytics?.reports ?? []).map((report) => <option value={report} key={report}>{report}</option>)}</select></label>
            <label><span>資料列</span><select value={analyticsRowKind} onChange={(event) => { setAnalyticsPage(1); setAnalyticsRowKind(event.target.value); }}><option value="detail">內容明細</option><option value="total">報表總計</option><option value="all">全部</option></select></label>
            <label><span>內容格式</span><select value={analyticsFormat} onChange={(event) => { setAnalyticsPage(1); setAnalyticsFormat(event.target.value); }}><option value="all">全部格式</option>{(analytics?.content_formats ?? []).map((format) => <option value={format} key={format}>{format}</option>)}</select></label>
            <label><span>內容主題</span><select value={analyticsTopic} onChange={(event) => { setAnalyticsPage(1); setAnalyticsTopic(event.target.value); }}><option value="all">全部主題</option>{(analytics?.content_topics ?? []).map((topic) => <option value={topic} key={topic}>{topic}</option>)}</select></label>
            <label><span>核對狀態</span><select value={analyticsStatus} onChange={(event) => { setAnalyticsPage(1); setAnalyticsStatus(event.target.value); }}><option value="all">全部狀態</option><option value="normal">正常</option><option value="conflict">待核對</option></select></label>
            <label><span>排序欄位</span><select value={analyticsSort} onChange={(event) => { setAnalyticsPage(1); setAnalyticsSort(event.target.value as AnalyticsSort); }}>{ANALYTICS_SORT_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>
            <label><span>方向</span><select value={analyticsDirection} onChange={(event) => { setAnalyticsPage(1); setAnalyticsDirection(event.target.value as "asc" | "desc"); }}><option value="desc">新到舊／大到小</option><option value="asc">舊到新／小到大</option></select></label>
            <label><span>每頁</span><select value={analyticsPageSize} onChange={(event) => { setAnalyticsPage(1); setAnalyticsPageSize(Number(event.target.value)); }}><option value={25}>25 筆</option><option value={50}>50 筆</option><option value={100}>100 筆</option></select></label>
            <button className="button ghost analytics-clear" type="button" onClick={() => { setAnalyticsQueryDraft(""); setAnalyticsQuery(""); setAnalyticsDateStart(""); setAnalyticsDateEnd(""); setAnalyticsReport("all"); setAnalyticsRowKind("detail"); setAnalyticsStatus("all"); setAnalyticsFormat("all"); setAnalyticsTopic("all"); setAnalyticsSort("date"); setAnalyticsDirection("desc"); setAnalyticsPage(1); }}>清除篩選</button>
          </div>
          <p className="analytics-classification-note">日期可能來自每日資料日期或影片發布日，列內會標示來源。內容主題是台V Pulse 的可檢查規則分類，不是 YouTube Analytics 官方分類；特定遊戲請用標題搜尋，無足夠證據時顯示「未分類／未判斷」。缺值不當成 0，排序時固定放在最後。</p>
          {analyticsError && <p className="inline-message" role="status">{analyticsError}</p>}
          <div className="table-wrap analytics-table-wrap"><table className="analytics-table"><thead><tr>
            <SortHeader field="date" label="資料日期／發布日" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="content" label="內容" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="content_topic" label="內容主題" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="content_format" label="內容格式" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="report_name" label="報表" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="views" label="觀看" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="engaged_views" label="互動觀看" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="watch_time_hours" label="觀看時數" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="average_view_duration_seconds" label="平均觀看" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="average_percentage_viewed" label="觀看比例" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="impressions" label="曝光" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="impressions_ctr" label="點閱率" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="unique_viewers" label="不重複觀眾" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="returning_viewers" label="回訪觀眾" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="likes" label="喜歡" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="comments" label="留言" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
            <SortHeader field="conflict_status" label="狀態" sort={analyticsSort} direction={analyticsDirection} onSort={changeAnalyticsSort} />
          </tr></thead><tbody>{!analytics || analytics.rows.length === 0 ? <tr><td colSpan={17} className="table-empty">{analyticsLoading ? "正在讀取解析資料…" : "目前沒有符合篩選條件的資料。"}</td></tr> : analytics.rows.map((row) => <tr key={row.id}><td><strong>{row.display_date ?? (row.row_kind === "total" ? "報表總計" : "無日期")}</strong><small className="table-subline">{row.date_source}</small></td><td><strong>{row.video_title ?? row.video_id ?? "整體報表"}</strong>{row.video_title && row.video_id && <small className="table-subline">{row.video_id}</small>}</td><td><strong>{row.content_topic}</strong><small className="table-subline">{row.classification_source}{row.game_name ? ` · ${row.game_name}` : ""}{row.classification_evidence ? ` · ${row.classification_evidence}` : ""}</small></td><td>{row.content_format}</td><td>{row.report_name}</td><td>{exact(row.views)}</td><td>{exact(row.engaged_views)}</td><td>{exact(row.watch_time_hours, 1)}</td><td>{row.average_view_duration_seconds === null ? "—" : `${exact(row.average_view_duration_seconds)} 秒`}</td><td>{row.average_percentage_viewed === null ? "—" : `${exact(row.average_percentage_viewed, 2)}%`}</td><td>{exact(row.impressions)}</td><td>{row.impressions_ctr === null ? "—" : `${exact(row.impressions_ctr, 2)}%`}</td><td>{exact(row.unique_viewers)}</td><td>{exact(row.returning_viewers)}</td><td>{exact(row.likes)}</td><td>{exact(row.comments)}</td><td>{row.conflict_status ? <span className="conflict-badge">待核對</span> : <span className="state-badge">正常</span>}</td></tr>)}</tbody></table></div>
          <div className="analytics-pagination"><span>{analytics?.result_count ? `第 ${analytics.page} / ${analytics.page_count} 頁` : "0 筆結果"}</span><div><button className="button ghost" type="button" onClick={() => setAnalyticsPage((current) => Math.max(1, current - 1))} disabled={!analytics || analytics.page <= 1 || analyticsLoading}>上一頁</button><button className="button ghost" type="button" onClick={() => setAnalyticsPage((current) => Math.min(analytics?.page_count ?? current, current + 1))} disabled={!analytics || analytics.page >= analytics.page_count || analyticsLoading}>下一頁</button></div></div>
        </section>

        <section className="panel manual-history-panel">
          <div className="panel-heading"><div><p className="section-kicker">MANUAL HISTORY</p><h2>手動補充紀錄</h2></div><span>{creator.manual_metrics.length} 筆</span></div>
          <div className="table-wrap"><table><thead><tr><th>日期</th><th>指標</th><th>數值</th><th>影片</th><th>備註</th><th>操作</th></tr></thead><tbody>{creator.manual_metrics.length === 0 ? <tr><td colSpan={6} className="table-empty">目前沒有手動補充資料。</td></tr> : creator.manual_metrics.map((item) => <tr key={item.id}><td>{item.metric_date}</td><td>{METRIC_LABELS[item.metric_name] ?? item.metric_name}</td><td>{exact(item.metric_value, 2)}</td><td>{item.video_id || "整體"}</td><td>{item.note || "—"}</td><td><button className="danger-button" type="button" onClick={() => void removeManual(item)}>刪除</button></td></tr>)}</tbody></table></div>
        </section>
        </details>
      </>}

      {classificationChannel && <div className="modal-backdrop" role="presentation"><section className="confirmation-dialog classification-dialog" role="dialog" aria-modal="true" aria-labelledby="creator-classification-title"><p className="section-kicker">CHANNEL CLASSIFICATION</p><h2 id="creator-classification-title">設定 {classificationChannel.title} 的頻道資料</h2><p>新頻道已加入工作區。先補上勢別、所屬與標籤，之後團隊比較就能正確分組；這些本機欄位不會寫回 YouTube。</p><form onSubmit={(event) => void saveChannelClassification(event)}><div className="classification-grid"><label><span>勢別分類</span><select value={classificationCategory} onChange={(event) => setClassificationCategory(event.target.value)}><option>未分類</option><option>個人勢</option><option>企業勢</option><option>團體勢</option><option>其他</option></select></label><label><span>活動狀態</span><select value={classificationActivity} onChange={(event) => setClassificationActivity(event.target.value)}><option>自動判斷</option><option>活動中</option><option>休止中</option><option>疑似已畢業</option><option>已確認畢業</option><option>狀態不明</option></select></label><label><span>所屬企業／團體</span><input value={classificationOrganization} onChange={(event) => setClassificationOrganization(event.target.value)} placeholder="例如：子午計畫" maxLength={80} /></label><label><span>自訂標籤</span><input value={classificationTags} onChange={(event) => setClassificationTags(event.target.value)} placeholder="例如：歌勢、遊戲、同期生" /></label></div><div className="dialog-actions"><button className="button ghost" type="button" onClick={() => setClassificationChannel(null)}>稍後分類</button><button className="button primary" type="submit" disabled={addingChannelId === classificationChannel.channel_id}>{addingChannelId === classificationChannel.channel_id ? "儲存中…" : "儲存分類"}</button></div></form></section></div>}

      <LegalFooter context="頻道工作區" note="私人 Analytics 依管理頻道分開保存，且只留在你的電腦" />
    </main>
  );
}
