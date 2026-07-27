"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

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
  match_term: string | null;
  match_field: string | null;
  match_excerpt: string | null;
  updated_at: string | null;
};

type LiveVideo = {
  video_id: string;
  title: string;
  channel_title: string;
  thumbnail_url: string | null;
  live_state: "live" | "upcoming";
  current_concurrent: number | null;
  scheduled_start: string | null;
  actual_start: string | null;
  updated_at: string | null;
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
  excluded: boolean;
  already_added: boolean;
};

type CollectionSettings = {
  min_subscribers: number;
  live_poll_seconds: number;
  channel_refresh_hours: number;
  upload_scan_hours: number;
  retention_days: number;
  discovery_terms: string[];
};

type ChannelSnapshot = {
  captured_at: string;
  subscriber_count: number | null;
  view_count: number | null;
  video_count: number | null;
};

type DetailedVideo = {
  video_id: string;
  title: string;
  thumbnail_url: string | null;
  published_at: string | null;
  duration_seconds: number | null;
  view_count: number | null;
  like_count: number | null;
  comment_count: number | null;
  live_state: "video" | "live" | "upcoming" | "completed";
  current_concurrent: number | null;
  peak_concurrent: number | null;
  concurrency_samples: number;
  scheduled_start: string | null;
  actual_start: string | null;
  actual_end: string | null;
  updated_at: string | null;
};

type ChannelDetail = {
  channel: Channel & {
    description: string;
    keywords: string;
    country: string | null;
    hidden_subscriber_count: number;
    discovery_status: string;
    created_at: string;
    last_stats_at: string | null;
    last_upload_scan_at: string | null;
  };
  snapshots: ChannelSnapshot[];
  videos: DetailedVideo[];
  peak_concurrent: number | null;
  concurrency_sample_count: number;
};

type Summary = {
  api_key_configured: boolean;
  collector_running: boolean;
  current_job: string | null;
  last_error: string | null;
  eligible_channels: number;
  review_channels: number;
  excluded_channels: number;
  live_count: number;
  upcoming_count: number;
  sample_count: number;
  quota_general: number;
  quota_search: number;
  quota_general_limit: number;
  quota_search_limit: number;
  retention_days: number;
  settings: CollectionSettings;
  categories: { category: string; channel_count: number }[];
  channels: Channel[];
  live_videos: LiveVideo[];
};

const DEFAULT_SETTINGS: CollectionSettings = {
  min_subscribers: 1000,
  live_poll_seconds: 60,
  channel_refresh_hours: 6,
  upload_scan_hours: 4,
  retention_days: 30,
  discovery_terms: ["台V", "台灣VTuber", "台灣 VTuber", "Taiwan VTuber"],
};

const EMPTY_SUMMARY: Summary = {
  api_key_configured: false,
  collector_running: false,
  current_job: null,
  last_error: null,
  eligible_channels: 0,
  review_channels: 0,
  excluded_channels: 0,
  live_count: 0,
  upcoming_count: 0,
  sample_count: 0,
  quota_general: 0,
  quota_search: 0,
  quota_general_limit: 10000,
  quota_search_limit: 100,
  retention_days: 30,
  settings: DEFAULT_SETTINGS,
  categories: [],
  channels: [],
  live_videos: [],
};

function number(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function fullNumber(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("zh-TW").format(value);
}

function time(value: string | null) {
  if (!value) return "時間未定";
  return new Intl.DateTimeFormat("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Taipei",
  }).format(new Date(value));
}

function ago(value: string | null) {
  if (!value) return "尚未更新";
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "剛剛";
  if (minutes < 60) return `${minutes} 分鐘前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小時前`;
  return `${Math.floor(minutes / 1440)} 天前`;
}

function duration(value: number | null) {
  if (value === null) return "—";
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return hours ? `${hours} 小時 ${minutes} 分` : `${minutes} 分`;
}

function videoType(video: DetailedVideo) {
  if (video.live_state === "live") return "直播中";
  if (video.live_state === "upcoming") return "待直播";
  if (video.live_state === "completed") return "直播存檔";
  return "影片";
}

export default function Dashboard() {
  const [data, setData] = useState<Summary>(EMPTY_SUMMARY);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [specificQuery, setSpecificQuery] = useState("");
  const [candidates, setCandidates] = useState<ChannelCandidate[]>([]);
  const [searchingSpecific, setSearchingSpecific] = useState(false);
  const [actingChannelId, setActingChannelId] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState("全部");
  const [sortBy, setSortBy] = useState("subscribers");
  const [showSettings, setShowSettings] = useState(false);
  const [settingsDraft, setSettingsDraft] = useState<CollectionSettings>(DEFAULT_SETTINGS);
  const [termsDraft, setTermsDraft] = useState(DEFAULT_SETTINGS.discovery_terms.join("\n"));
  const [savingSettings, setSavingSettings] = useState(false);
  const [selectedChannelId, setSelectedChannelId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ChannelDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailCategory, setDetailCategory] = useState("");

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/summary`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = (await response.json()) as Summary;
      setData(payload);
      setConnected(true);
    } catch {
      setConnected(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const runDiscovery = async () => {
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/discover`, { method: "POST" });
      const payload = (await response.json()) as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法開始探索");
      setMessage(payload.message ?? "已開始搜尋候選頻道");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失敗");
    }
  };

  const searchSpecific = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = specificQuery.trim();
    if (!value) return;
    setMessage(null);
    setSearchingSpecific(true);
    try {
      const response = await fetch(`${API_BASE}/api/channel-search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: value }),
      });
      const payload = (await response.json()) as { candidates?: ChannelCandidate[]; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "搜尋失敗");
      setCandidates(payload.candidates ?? []);
      if ((payload.candidates ?? []).length === 0) setMessage("找不到符合的 YouTube 頻道");
    } catch (error) {
      setCandidates([]);
      setMessage(error instanceof Error ? error.message : "搜尋失敗");
    } finally {
      setSearchingSpecific(false);
    }
  };

  const addCandidate = async (channel: ChannelCandidate) => {
    setMessage(null);
    setActingChannelId(channel.channel_id);
    try {
      const response = await fetch(`${API_BASE}/api/channels`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel_id: channel.channel_id }),
      });
      const payload = (await response.json()) as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法收錄頻道");
      setCandidates((current) => current.map((item) => item.channel_id === channel.channel_id
        ? { ...item, already_added: true, excluded: false }
        : item));
      setMessage(payload.message ?? `已收錄 ${channel.title}`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法收錄頻道");
    } finally {
      setActingChannelId(null);
    }
  };

  const excludeChannel = async (channel: Channel) => {
    const confirmed = window.confirm(
      `確定排除「${channel.title}」嗎？\n\n此頻道在本機保存的影片、統計與同接資料會一併刪除，並加入黑名單，之後大範圍探索不會再自動加回。`,
    );
    if (!confirmed) return;
    setMessage(null);
    setActingChannelId(channel.channel_id);
    try {
      const response = await fetch(`${API_BASE}/api/channels/${encodeURIComponent(channel.channel_id)}`, { method: "DELETE" });
      const payload = (await response.json()) as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法排除頻道");
      setCandidates((current) => current.map((item) => item.channel_id === channel.channel_id
        ? { ...item, already_added: false, excluded: true }
        : item));
      setMessage(payload.message ?? `已排除 ${channel.title}`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法排除頻道");
    } finally {
      setActingChannelId(null);
    }
  };

  const updateCategory = async (channelId: string, category: string) => {
    setActingChannelId(channelId);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/channels/${encodeURIComponent(channelId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category }),
      });
      const payload = (await response.json()) as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法儲存分類");
      setData((current) => ({
        ...current,
        channels: current.channels.map((channel) => channel.channel_id === channelId
          ? { ...channel, category: category.trim() || "未分類" }
          : channel),
      }));
      setDetail((current) => current && current.channel.channel_id === channelId
        ? { ...current, channel: { ...current.channel, category: category.trim() || "未分類" } }
        : current);
      setMessage(payload.message ?? "頻道分類已儲存");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法儲存分類");
    } finally {
      setActingChannelId(null);
    }
  };

  const openSettings = () => {
    setSettingsDraft(data.settings);
    setTermsDraft(data.settings.discovery_terms.join("\n"));
    setShowSettings(true);
  };

  const saveSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSavingSettings(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...settingsDraft,
          discovery_terms: termsDraft.split(/[\n,]/).map((term) => term.trim()).filter(Boolean),
        }),
      });
      const payload = (await response.json()) as { settings?: CollectionSettings; message?: string; error?: string };
      if (!response.ok || !payload.settings) throw new Error(payload.error ?? "無法儲存規則");
      setData((current) => ({ ...current, settings: payload.settings!, retention_days: payload.settings!.retention_days }));
      setSettingsDraft(payload.settings);
      setTermsDraft(payload.settings.discovery_terms.join("\n"));
      setShowSettings(false);
      setMessage(payload.message ?? "規則已儲存");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法儲存規則");
    } finally {
      setSavingSettings(false);
    }
  };

  const openChannel = async (channelId: string) => {
    setSelectedChannelId(channelId);
    setDetail(null);
    setDetailLoading(true);
    setMessage(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
    try {
      const response = await fetch(`${API_BASE}/api/channels/${encodeURIComponent(channelId)}`, { cache: "no-store" });
      const payload = (await response.json()) as ChannelDetail & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "無法載入頻道資料");
      setDetail(payload);
      setDetailCategory(payload.channel.category);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法載入頻道資料");
    } finally {
      setDetailLoading(false);
    }
  };

  const categoryOptions = useMemo(() => {
    const values = new Set(["未分類", "個人勢", "企業勢", "團體勢", "其他"]);
    data.categories.forEach(({ category }) => values.add(category));
    return Array.from(values);
  }, [data.categories]);

  const visibleChannels = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("zh-TW");
    const channels = data.channels.filter((channel) => {
      if (categoryFilter !== "全部" && channel.category !== categoryFilter) return false;
      if (!normalized) return true;
      return `${channel.title} ${channel.handle ?? ""} ${channel.category} ${channel.match_excerpt ?? ""}`
        .toLocaleLowerCase("zh-TW")
        .includes(normalized);
    });
    return [...channels].sort((a, b) => {
      if (sortBy === "category") return a.category.localeCompare(b.category, "zh-Hant") || a.title.localeCompare(b.title, "zh-Hant");
      if (sortBy === "views") return (b.view_count ?? -1) - (a.view_count ?? -1);
      if (sortBy === "videos") return (b.video_count ?? -1) - (a.video_count ?? -1);
      if (sortBy === "name") return a.title.localeCompare(b.title, "zh-Hant");
      if (sortBy === "updated") return new Date(b.updated_at ?? 0).getTime() - new Date(a.updated_at ?? 0).getTime();
      return (b.subscriber_count ?? -1) - (a.subscriber_count ?? -1);
    });
  }, [categoryFilter, data.channels, query, sortBy]);

  const quotaPercent = Math.min(100, Math.round((data.quota_general / data.quota_general_limit) * 100));
  const trendSnapshots = detail?.snapshots.slice(-40) ?? [];
  const trendValues = trendSnapshots.map((snapshot) => snapshot.subscriber_count ?? 0);
  const trendMin = trendValues.length ? Math.min(...trendValues) : 0;
  const trendMax = trendValues.length ? Math.max(...trendValues) : 0;

  if (selectedChannelId) {
    return (
      <main className="app-shell detail-shell">
        <header className="detail-topbar">
          <button className="button ghost back-button" type="button" onClick={() => { setSelectedChannelId(null); setDetail(null); setMessage(null); }}>← 返回頻道列表</button>
          <div className="brand-block compact"><div className="brand-mark" aria-hidden="true">V</div><div><p className="eyebrow">CHANNEL INTELLIGENCE</p><h1>頻道詳細資料</h1></div></div>
        </header>
        {message && <section className="inline-message">{message}</section>}
        {detailLoading && <section className="panel detail-loading">正在整理頻道資料…</section>}
        {!detailLoading && !detail && <section className="panel detail-loading">找不到這個頻道，請返回列表重試。</section>}
        {detail && (
          <>
            <section className="panel detail-identity">
              <div className="detail-profile">
                {detail.channel.thumbnail_url ? <img src={detail.channel.thumbnail_url} alt="" /> : <span className="detail-avatar">V</span>}
                <div><p className="section-kicker">{detail.channel.category}</p><h2>{detail.channel.title}</h2><span>{detail.channel.handle ?? detail.channel.channel_id}</span></div>
              </div>
              <div className="detail-actions">
                <a className="button external-button" href={detail.channel.handle ? `https://www.youtube.com/${detail.channel.handle}` : `https://www.youtube.com/channel/${detail.channel.channel_id}`} target="_blank" rel="noreferrer">開啟 YouTube ↗</a>
              </div>
              <p className="detail-description">{detail.channel.description || "這個頻道沒有公開說明。"}</p>
              <form className="detail-category-form" onSubmit={(event) => { event.preventDefault(); void updateCategory(detail.channel.channel_id, detailCategory); }}>
                <label><span>自訂分類</span><input value={detailCategory} onChange={(event) => setDetailCategory(event.target.value)} list="detail-category-options" maxLength={40} /></label>
                <datalist id="detail-category-options">{categoryOptions.map((category) => <option value={category} key={category} />)}</datalist>
                <button className="button" type="submit" disabled={actingChannelId === detail.channel.channel_id}>{actingChannelId === detail.channel.channel_id ? "儲存中…" : "儲存分類"}</button>
              </form>
            </section>

            <section className="detail-metrics">
              <article className="metric-card"><span>訂閱者</span><strong>{number(detail.channel.subscriber_count)}</strong><p>{fullNumber(detail.channel.subscriber_count)} 人</p></article>
              <article className="metric-card"><span>頻道總觀看</span><strong>{number(detail.channel.view_count)}</strong><p>{fullNumber(detail.channel.view_count)} 次</p></article>
              <article className="metric-card"><span>影片數</span><strong>{number(detail.channel.video_count)}</strong><p>API 公開統計</p></article>
              <article className="metric-card"><span>歷史最高同接</span><strong>{number(detail.peak_concurrent)}</strong><p>{fullNumber(detail.concurrency_sample_count)} 個同接資料點</p></article>
            </section>

            <section className="detail-grid">
              <article className="panel trend-panel">
                <div className="panel-heading"><div><p className="section-kicker">SUBSCRIBER HISTORY</p><h2>訂閱趨勢</h2></div><span>{trendSnapshots.length} 個近期快照</span></div>
                {trendSnapshots.length < 2 ? (
                  <div className="detail-empty">至少完成兩次頻道統計更新後，這裡會顯示趨勢。</div>
                ) : (
                  <div className="trend-chart" aria-label="訂閱數趨勢圖">
                    {trendSnapshots.map((snapshot, index) => {
                      const value = snapshot.subscriber_count ?? trendMin;
                      const height = trendMax === trendMin ? 52 : 18 + ((value - trendMin) / (trendMax - trendMin)) * 82;
                      return <i key={`${snapshot.captured_at}-${index}`} style={{ height: `${height}%` }} title={`${time(snapshot.captured_at)}：${fullNumber(snapshot.subscriber_count)}`} />;
                    })}
                  </div>
                )}
                <div className="trend-legend"><span>{trendSnapshots[0] ? time(trendSnapshots[0].captured_at) : "—"}</span><strong>{fullNumber(trendMin)} → {fullNumber(trendMax)}</strong><span>{trendSnapshots.at(-1) ? time(trendSnapshots.at(-1)!.captured_at) : "—"}</span></div>
              </article>
              <aside className="panel detail-meta-panel">
                <div className="panel-heading"><div><p className="section-kicker">COLLECTION INFO</p><h2>收錄資訊</h2></div></div>
                <dl className="rules-list">
                  <div><dt>收錄依據</dt><dd>{detail.channel.match_term ?? "待確認"}</dd></div>
                  <div><dt>命中欄位</dt><dd>{detail.channel.match_field ?? "—"}</dd></div>
                  <div><dt>國家代碼</dt><dd>{detail.channel.country ?? "未公開"}</dd></div>
                  <div><dt>首次收錄</dt><dd>{time(detail.channel.created_at)}</dd></div>
                  <div><dt>頻道統計更新</dt><dd>{ago(detail.channel.last_stats_at)}</dd></div>
                  <div><dt>影片掃描</dt><dd>{ago(detail.channel.last_upload_scan_at)}</dd></div>
                </dl>
              </aside>
            </section>

            <section className="panel detail-videos-panel">
              <div className="panel-heading"><div><p className="section-kicker">VIDEO LIBRARY</p><h2>已蒐集影片與直播</h2></div><span>{detail.videos.length} 個項目</span></div>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>影片</th><th>類型</th><th>觀看</th><th>喜歡</th><th>留言</th><th>最高同接</th><th>長度</th><th>發布</th></tr></thead>
                  <tbody>
                    {detail.videos.length === 0 ? (
                      <tr><td colSpan={8} className="table-empty">完成一次最新上傳掃描後，影片會出現在這裡。</td></tr>
                    ) : detail.videos.map((video) => (
                      <tr key={video.video_id}>
                        <td><a className="video-name" href={`https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer">{video.thumbnail_url && <img src={video.thumbnail_url} alt="" />}<strong>{video.title}</strong></a></td>
                        <td><span className={`state-badge ${video.live_state}`}>{videoType(video)}</span></td>
                        <td>{number(video.view_count)}</td><td>{number(video.like_count)}</td><td>{number(video.comment_count)}</td><td>{number(video.peak_concurrent)}</td><td>{duration(video.duration_seconds)}</td><td>{time(video.published_at ?? video.scheduled_start)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
        <footer><span>台V Pulse · 頻道詳細資料</span><span>資料只保存在你的電腦</span></footer>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-block"><div className="brand-mark" aria-hidden="true">V</div><div><p className="eyebrow">LOCAL VTUBER INTELLIGENCE</p><h1>台V Pulse</h1></div></div>
        <div className="status-cluster">
          <span className={`connection ${connected ? "online" : "offline"}`}><i />{connected ? "本機服務已連線" : "等待本機服務"}</span>
          <a className="button insight-nav-button" href="/insights">內容環境</a>
          <button className="button ghost" onClick={() => void refresh()} disabled={loading}>重新整理</button>
          <button className="button primary" onClick={() => void runDiscovery()} disabled={!connected || Boolean(data.current_job)}>{data.current_job === "discover" ? "正在探索…" : "探索台 V 頻道"}</button>
        </div>
      </header>

      {!connected && <section className="notice warning"><span className="notice-icon">!</span><div><strong>資料服務尚未啟動</strong><p>執行 start-local.ps1 後，本頁會自動連線。介面可以先預覽，但不會呼叫 YouTube。</p></div></section>}
      {connected && !data.api_key_configured && <section className="notice"><span className="notice-icon">i</span><div><strong>還差一把 API Key</strong><p>在 .env 填入 YOUTUBE_API_KEY；請不要把 Key 貼到聊天或公開檔案。</p></div></section>}
      {(message || data.last_error) && <section className="inline-message">{message ?? data.last_error}</section>}

      <section className="hero-grid">
        <article className="hero-card live-hero"><div className="hero-heading"><span className="live-dot" />現正直播</div><strong>{data.live_count}</strong><p>每 {data.settings.live_poll_seconds} 秒批次更新同接</p><div className="mini-bars" aria-hidden="true">{[18, 33, 23, 51, 39, 72, 57, 86, 64, 94, 78, 100].map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div></article>
        <article className="metric-card"><span>已收錄頻道</span><strong>{number(data.eligible_channels)}</strong><p>自動驗證或手動指定</p></article>
        <article className="metric-card"><span>即將直播</span><strong>{number(data.upcoming_count)}</strong><p>由最新上傳與排程辨識</p></article>
        <article className="metric-card"><span>同接資料點</span><strong>{number(data.sample_count)}</strong><p>目前保留 {data.retention_days} 天</p></article>
        <article className="metric-card quota-card"><span>今日一般配額</span><strong>{number(data.quota_general)} <small>/ {number(data.quota_general_limit)}</small></strong><div className="quota-track"><i style={{ width: `${quotaPercent}%` }} /></div><p>{quotaPercent}% 已使用 · 搜尋 {data.quota_search}/{data.quota_search_limit}</p></article>
      </section>

      <section className="content-grid">
        <article className="panel live-panel">
          <div className="panel-heading"><div><p className="section-kicker">LIVE RADAR</p><h2>直播雷達</h2></div><span>{data.live_videos.length} 個項目</span></div>
          <div className="live-list">{data.live_videos.length === 0 ? <div className="empty-state"><span>◌</span><strong>目前沒有已知直播</strong><p>完成首次頻道探索和上傳掃描後，直播會出現在這裡。</p></div> : data.live_videos.map((video) => <a className="live-row" key={video.video_id} href={`https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer"><div className="thumb" style={video.thumbnail_url ? { backgroundImage: `url(${video.thumbnail_url})` } : undefined}><span>{video.live_state === "live" ? "LIVE" : "預定"}</span></div><div className="live-copy"><strong>{video.title}</strong><p>{video.channel_title}</p></div><div className="live-stat"><strong>{video.live_state === "live" ? number(video.current_concurrent) : time(video.scheduled_start)}</strong><span>{video.live_state === "live" ? "目前同接" : "預定開始"}</span></div></a>)}</div>
        </article>

        <aside className="panel rules-panel">
          <div className="panel-heading"><div><p className="section-kicker">COLLECTION RULES</p><h2>收錄規則</h2></div><button className="text-button" type="button" onClick={openSettings} disabled={!connected}>編輯</button></div>
          <dl className="rules-list">
            <div><dt>自述字樣</dt><dd>{data.settings.discovery_terms.join("、")}</dd></div>
            <div><dt>最低訂閱</dt><dd>{fullNumber(data.settings.min_subscribers)}</dd></div>
            <div><dt>同接頻率</dt><dd>{data.settings.live_poll_seconds} 秒／批次 50 支</dd></div>
            <div><dt>頻道更新</dt><dd>每 {data.settings.channel_refresh_hours} 小時</dd></div>
            <div><dt>原始保留</dt><dd>{data.settings.retention_days} 天</dd></div>
            <div><dt>手動排除</dt><dd>{data.excluded_channels} 個黑名單頻道</dd></div>
          </dl>
          <div className="rule-note"><strong>變更套用於後續收錄</strong><p>提高門檻不會自動刪除已收錄頻道；你可以從頻道列表自行排除。</p></div>
        </aside>
      </section>

      {showSettings && (
        <section className="panel settings-panel">
          <div className="panel-heading"><div><p className="section-kicker">RULE EDITOR</p><h2>編輯收錄與監控規則</h2></div><button className="text-button" type="button" onClick={() => setShowSettings(false)}>關閉</button></div>
          <form className="settings-form" onSubmit={(event) => void saveSettings(event)}>
            <label className="terms-field"><span>大範圍探索字樣</span><textarea value={termsDraft} onChange={(event) => setTermsDraft(event.target.value)} rows={5} /><small>每行一組，最多 12 組。候選頻道也必須在名稱、說明或關鍵字中出現其中一組。</small></label>
            <div className="settings-number-grid">
              <label><span>最低訂閱數</span><input type="number" min={1} max={10000000} value={settingsDraft.min_subscribers} onChange={(event) => setSettingsDraft({ ...settingsDraft, min_subscribers: Number(event.target.value) })} /></label>
              <label><span>同接更新秒數</span><input type="number" min={30} max={3600} value={settingsDraft.live_poll_seconds} onChange={(event) => setSettingsDraft({ ...settingsDraft, live_poll_seconds: Number(event.target.value) })} /></label>
              <label><span>頻道統計更新（小時）</span><input type="number" min={1} max={168} value={settingsDraft.channel_refresh_hours} onChange={(event) => setSettingsDraft({ ...settingsDraft, channel_refresh_hours: Number(event.target.value) })} /></label>
              <label><span>上傳掃描（小時）</span><input type="number" min={1} max={168} value={settingsDraft.upload_scan_hours} onChange={(event) => setSettingsDraft({ ...settingsDraft, upload_scan_hours: Number(event.target.value) })} /></label>
              <label><span>資料保留天數</span><input type="number" min={1} max={1095} value={settingsDraft.retention_days} onChange={(event) => setSettingsDraft({ ...settingsDraft, retention_days: Number(event.target.value) })} /></label>
            </div>
            <div className="settings-footer"><p>同接最低可設 30 秒；頻率越高，日常 API 請求會越多。</p><button className="button primary" type="submit" disabled={savingSettings}>{savingSettings ? "儲存中…" : "儲存規則"}</button></div>
          </form>
        </section>
      )}

      <section className="panel specific-search-panel">
        <div className="panel-heading specific-heading"><div><p className="section-kicker">DIRECT CHANNEL LOOKUP</p><h2>指定 VTuber 搜尋</h2></div><p>直接指定不要求自述字樣，但仍須公開訂閱數達 {fullNumber(data.settings.min_subscribers)}。</p></div>
        <form className="specific-form" onSubmit={(event) => void searchSpecific(event)}><label><span>名稱、@handle、Channel ID 或頻道網址</span><input value={specificQuery} onChange={(event) => setSpecificQuery(event.target.value)} placeholder="例如：杏仁ミル、@handle、UC..." aria-label="指定 VTuber 頻道" /></label><button className="button primary" type="submit" disabled={!connected || !specificQuery.trim() || searchingSpecific}>{searchingSpecific ? "搜尋中…" : "搜尋頻道"}</button></form>
        {candidates.length > 0 && <div className="candidate-list">{candidates.map((channel) => {
          const unavailable = channel.hidden_subscriber_count || !channel.meets_threshold;
          const status = channel.hidden_subscriber_count ? "訂閱數已隱藏" : channel.meets_threshold ? `${number(channel.subscriber_count)} 位訂閱者` : `${number(channel.subscriber_count)} 位訂閱者 · 未達門檻`;
          return <article className="candidate-card" key={channel.channel_id}>{channel.thumbnail_url ? <img src={channel.thumbnail_url} alt="" /> : <span className="candidate-avatar">V</span>}<div className="candidate-copy"><div className="candidate-title"><strong>{channel.title}</strong><span>{status}</span></div><small>{channel.handle ?? channel.channel_id}</small><p>{channel.description || "這個頻道沒有公開說明。"}</p></div><button className="button candidate-action" type="button" onClick={() => void addCandidate(channel)} disabled={unavailable || channel.already_added || actingChannelId === channel.channel_id}>{actingChannelId === channel.channel_id ? "處理中…" : channel.already_added ? "已收錄" : channel.excluded ? "重新收錄" : "收錄"}</button></article>;
        })}</div>}
      </section>

      <section className="panel channel-panel">
        <div className="panel-heading channel-heading">
          <div><p className="section-kicker">VERIFIED CHANNELS</p><h2>已確認頻道</h2></div>
          <div className="channel-controls">
            <label className="compact-select"><span>分類</span><select value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option>全部</option>{data.categories.map(({ category, channel_count }) => <option value={category} key={category}>{category}（{channel_count}）</option>)}</select></label>
            <label className="compact-select"><span>排序</span><select value={sortBy} onChange={(event) => setSortBy(event.target.value)}><option value="subscribers">訂閱數</option><option value="views">總觀看</option><option value="videos">影片數</option><option value="category">分類</option><option value="name">名稱</option><option value="updated">最近更新</option></select></label>
            <label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋頻道、分類或證據" aria-label="搜尋頻道" /></label>
          </div>
        </div>
        <datalist id="channel-category-options">{categoryOptions.map((category) => <option value={category} key={category} />)}</datalist>
        <div className="table-wrap"><table><thead><tr><th>頻道</th><th>分類</th><th>訂閱</th><th>總觀看</th><th>影片</th><th>收錄依據</th><th>更新</th><th>操作</th></tr></thead><tbody>
          {visibleChannels.length === 0 ? <tr><td colSpan={8} className="table-empty">沒有符合目前篩選的頻道。</td></tr> : visibleChannels.map((channel) => <tr key={channel.channel_id}>
            <td><button className="channel-link" type="button" onClick={() => void openChannel(channel.channel_id)}><span className="channel-name">{channel.thumbnail_url ? <img src={channel.thumbnail_url} alt="" /> : <span className="avatar-fallback">V</span>}<span><strong>{channel.title}</strong><small>{channel.handle ?? channel.channel_id}</small></span></span><span className="open-detail">查看詳細資料 →</span></button></td>
            <td><select className="category-select" value={channel.category} onChange={(event) => void updateCategory(channel.channel_id, event.target.value)} disabled={actingChannelId === channel.channel_id}>{categoryOptions.map((category) => <option value={category} key={category}>{category}</option>)}</select></td>
            <td>{number(channel.subscriber_count)}</td><td>{number(channel.view_count)}</td><td>{number(channel.video_count)}</td><td><span className="evidence">{channel.match_term ?? "待確認"}</span><small className="excerpt">{channel.match_excerpt ?? "—"}</small></td><td>{ago(channel.updated_at)}</td><td><button className="danger-button" type="button" onClick={() => void excludeChannel(channel)} disabled={actingChannelId === channel.channel_id}>{actingChannelId === channel.channel_id ? "處理中" : "排除"}</button></td>
          </tr>)}
        </tbody></table></div>
      </section>

      <footer><span>台V Pulse · 僅在你的電腦運作</span><span>資料來源：YouTube Data API · 非 YouTube 官方產品</span></footer>
    </main>
  );
}
