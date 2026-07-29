"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import LegalFooter from "../legal-footer";
import SiteHeader from "../site-header";

const API_BASE = process.env.NEXT_PUBLIC_TRACKER_API ?? "http://127.0.0.1:8787";

type DiscoveryBatch = {
  id: number;
  started_at: string;
  completed_at: string | null;
  status: string;
  discovery_terms: string[];
  candidate_count: number;
  eligible_count: number;
  below_threshold_count: number;
  review_count: number;
  excluded_count: number;
  rejected_count: number;
};

type Candidate = {
  id: number;
  batch_id: number;
  channel_id: string;
  title: string;
  handle: string | null;
  thumbnail_url: string | null;
  subscriber_count: number | null;
  hidden_subscriber_count: number;
  search_terms: string[];
  match_term: string | null;
  match_field: string | null;
  match_excerpt: string | null;
  validation_status: string;
  unlisted_reason: string;
  handling_status: string;
  handling_note: string;
  current_status: string;
  discovered_at: string;
  handled_at: string | null;
};

type CandidatePayload = {
  candidates: Candidate[];
  batches: DiscoveryBatch[];
  validation_counts: Record<string, number>;
  result_count: number;
  quota_note: string;
  error?: string;
};

const VALIDATION_LABELS: Record<string, string> = {
  pending: "驗證中",
  eligible: "符合規則",
  below_threshold: "未達門檻",
  review: "訂閱隱藏／待審",
  rejected: "無台 V 自述證據",
  excluded: "黑名單",
  unavailable: "資料未回傳",
};

const HANDLING_LABELS: Record<string, string> = {
  pending: "待人工處理",
  deferred: "稍後處理",
  included: "目前已收錄",
  excluded: "目前已排除",
  auto_included: "已自動收錄",
  manual_approved: "已人工收錄",
  manual_excluded: "已人工排除",
};

function number(value: number | null) {
  if (value === null) return "—";
  return new Intl.NumberFormat("zh-TW").format(value);
}

function dateTime(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-TW", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Taipei",
  }).format(new Date(value));
}

export default function CandidateReview() {
  const [payload, setPayload] = useState<CandidatePayload | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [actingId, setActingId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [batchId, setBatchId] = useState("all");
  const [validationStatus, setValidationStatus] = useState("all");
  const [handlingStatus, setHandlingStatus] = useState("all");
  const [sort, setSort] = useState("discovered_at");
  const [direction, setDirection] = useState("desc");

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    const search = new URLSearchParams({
      query,
      validation_status: validationStatus,
      handling_status: handlingStatus,
      sort,
      direction,
    });
    if (batchId !== "all") search.set("batch_id", batchId);
    try {
      const response = await fetch(`${API_BASE}/api/candidates?${search.toString()}`, {
        cache: "no-store",
      });
      const next = await response.json() as CandidatePayload;
      if (!response.ok) throw new Error(
        response.status === 404
          ? "候選審核 API 尚未載入，請重新啟動台V Pulse 本機服務。"
          : next.error ?? "無法讀取候選資料",
      );
      setPayload(next);
      setConnected(true);
    } catch (error) {
      setConnected(false);
      if (!quiet) setMessage(error instanceof Error ? error.message : "無法連線本機資料服務");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [batchId, direction, handlingStatus, query, sort, validationStatus]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(true), 30_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const act = async (candidate: Candidate, action: "approve" | "exclude" | "defer") => {
    if (action === "approve" && !window.confirm(
      `人工確認收錄「${candidate.title}」嗎？\n\n這會覆寫這次自動規則的未收錄結果，並把頻道加入公開監測。`,
    )) return;
    if (action === "exclude" && !window.confirm(
      `確定排除「${candidate.title}」嗎？\n\n若已收錄，本機影片、統計與同接資料會刪除；頻道會加入黑名單，後續探索不會自動加回。`,
    )) return;
    setActingId(candidate.id);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/api/candidates/${candidate.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const result = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(result.error ?? "無法處理候選");
      setMessage(result.message ?? "候選狀態已更新");
      await refresh(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法處理候選");
    } finally {
      setActingId(null);
    }
  };

  const candidates = payload?.candidates ?? [];

  return (
    <main className="app-shell candidate-review-shell">
      <SiteHeader
        active="monitor"
        eyebrow="DISCOVERY REVIEW"
        title="候選審核"
        connected={connected}
        actions={<Link className="button ghost" href="/">返回監測首頁</Link>}
      />

      {!connected && !loading && <section className="notice warning"><span className="notice-icon">!</span><div><strong>等待本機資料服務</strong><p>啟動台V Pulse 後，這頁會直接讀取 SQLite 中已保存的探索批次。</p></div></section>}
      {message && <p className="inline-message" role="status">{message}</p>}

      <section className="panel candidate-review-intro">
        <div>
          <p className="section-kicker">WHAT COUNTS AS A CANDIDATE</p>
          <h2>搜尋結果先成為候選，通過規則後才是已收錄頻道</h2>
          <p>候選是每次 YouTube 搜尋結果依 Channel ID 去重後的頻道。系統再用頻道名稱、說明與關鍵字驗證台 V 自述，並檢查公開訂閱數；候選數不等於收錄數。</p>
        </div>
        <ul>
          <li><strong>大小寫相同：</strong>台V、台v、VTuber、vtuber 都以不分大小寫方式比對。</li>
          <li><strong>空白正規化：</strong>台V、台 V、台 v，以及台灣VTuber、台灣 VTuber 視為同類寫法。</li>
          <li><strong>不耗搜尋配額：</strong>{payload?.quota_note ?? "本頁只查本機資料，不重新搜尋 YouTube。"}</li>
        </ul>
      </section>

      <section className="panel candidate-filter-panel">
        <div className="panel-heading"><div><p className="section-kicker">LOCAL REVIEW QUEUE</p><h2>搜尋、篩選與排序</h2></div><span>{loading ? "讀取中…" : `${payload?.result_count ?? 0} 筆結果`}</span></div>
        <div className="candidate-filter-grid">
          <label className="candidate-query"><span>搜尋候選</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="頻道名稱、ID、@handle、搜尋詞或原因" /></label>
          <label><span>探索批次</span><select value={batchId} onChange={(event) => setBatchId(event.target.value)}><option value="all">全部批次</option>{(payload?.batches ?? []).map((batch) => <option value={batch.id} key={batch.id}>#{batch.id} · {dateTime(batch.started_at)} · {batch.candidate_count} 候選</option>)}</select></label>
          <label><span>規則結果</span><select value={validationStatus} onChange={(event) => setValidationStatus(event.target.value)}><option value="all">全部結果</option>{Object.entries(VALIDATION_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label><span>目前處理狀態</span><select value={handlingStatus} onChange={(event) => setHandlingStatus(event.target.value)}><option value="all">全部狀態</option><option value="pending">待人工處理</option><option value="deferred">稍後處理</option><option value="included">目前已收錄</option><option value="excluded">目前已排除</option></select></label>
          <label><span>排序欄位</span><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="discovered_at">探索時間</option><option value="subscriber_count">訂閱數</option><option value="title">頻道名稱</option><option value="validation_status">規則結果</option><option value="batch">探索批次</option></select></label>
          <label><span>方向</span><select value={direction} onChange={(event) => setDirection(event.target.value)}><option value="desc">由新到舊／由大到小</option><option value="asc">由舊到新／由小到大</option></select></label>
        </div>
      </section>

      <section className="panel candidate-review-table-panel">
        <div className="panel-heading"><div><p className="section-kicker">CANDIDATE RECORDS</p><h2>探索候選紀錄</h2></div><span>最多顯示 2,000 筆篩選結果</span></div>
        <div className="table-wrap">
          <table className="candidate-review-table">
            <thead><tr><th>頻道</th><th>訂閱</th><th>命中搜尋詞</th><th>命中欄位與證據</th><th>探索時間</th><th>未收錄原因／規則結果</th><th>處理狀態</th><th>操作</th></tr></thead>
            <tbody>
              {!loading && candidates.length === 0 && <tr><td colSpan={8} className="table-empty">目前沒有符合篩選條件的候選。完成一次「探索台 V 頻道」後，去重候選會保存在這裡。</td></tr>}
              {candidates.map((candidate) => <tr key={candidate.id}>
                <td><div className="candidate-channel-cell">{candidate.thumbnail_url ? <img src={candidate.thumbnail_url} alt="" /> : <span>V</span>}<div><a href={`https://www.youtube.com/channel/${candidate.channel_id}`} target="_blank" rel="noreferrer"><strong>{candidate.title}</strong> ↗</a><small>{candidate.handle ?? "無公開帳號"} · {candidate.channel_id}</small></div></div></td>
                <td>{candidate.hidden_subscriber_count ? <span className="state-badge">未公開</span> : number(candidate.subscriber_count)}</td>
                <td><div className="candidate-term-list">{candidate.search_terms.map((term) => <span key={term}>{term}</span>)}</div></td>
                <td>{candidate.match_field ? <><strong>{candidate.match_field} · {candidate.match_term}</strong><small className="candidate-evidence">{candidate.match_excerpt}</small></> : <span className="muted-cell">YouTube Search API 不提供命中欄位；本機驗證未找到自述證據</span>}</td>
                <td><strong>批次 #{candidate.batch_id}</strong><small className="table-subline">{dateTime(candidate.discovered_at)}</small></td>
                <td><span className={`validation-badge ${candidate.validation_status}`}>{VALIDATION_LABELS[candidate.validation_status] ?? candidate.validation_status}</span><small className="candidate-reason">{candidate.unlisted_reason || "已符合自動收錄規則"}</small></td>
                <td><strong>{HANDLING_LABELS[candidate.current_status] ?? HANDLING_LABELS[candidate.handling_status] ?? candidate.current_status}</strong>{candidate.handling_note && <small className="table-subline">{candidate.handling_note}</small>}</td>
                <td><div className="candidate-row-actions">
                  {candidate.current_status !== "included" && candidate.current_status !== "excluded" && <button type="button" className="text-button" disabled={actingId === candidate.id} onClick={() => void act(candidate, "approve")}>確認收錄</button>}
                  {candidate.current_status === "pending" && <button type="button" className="text-button" disabled={actingId === candidate.id} onClick={() => void act(candidate, "defer")}>稍後處理</button>}
                  {candidate.current_status !== "excluded" && <button type="button" className="danger-button" disabled={actingId === candidate.id} onClick={() => void act(candidate, "exclude")}>排除</button>}
                </div></td>
              </tr>)}
            </tbody>
          </table>
        </div>
      </section>

      <LegalFooter context="候選審核" note="只讀取本機探索紀錄，不增加 YouTube Search API 配額" />
    </main>
  );
}
