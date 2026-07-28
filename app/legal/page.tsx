import type { Metadata } from "next";
import Link from "next/link";
import LegalFooter from "../legal-footer";

export const metadata: Metadata = {
  title: "使用、隱私與授權聲明｜台V Pulse",
  description: "台V Pulse 的資料來源、保存範圍、活動狀態判斷及非商用授權說明。",
};

export default function LegalPage() {
  return (
    <main className="app-shell legal-shell">
      <header className="legal-page-header"><Link className="button ghost" href="/">← 返回監測首頁</Link><div className="brand-block compact"><div className="brand-mark" aria-hidden="true">V</div><div><p className="eyebrow">LOCAL USE & PRIVACY</p><h1>使用、隱私與授權聲明</h1></div></div><span /></header>

      <section className="panel legal-document">
        <div><p className="section-kicker">ABOUT</p><h2>工具定位</h2><p>台V Pulse 是第三方製作、在使用者電腦本機執行的資料整理工具，並非 YouTube 或 Google 的官方產品，也未獲其背書。使用者須自行申請及保管 YouTube Data API Key。</p></div>
        <div><p className="section-kicker">DATA SOURCES</p><h2>資料來源與保存</h2><p>公開頻道、影片及直播資料來自 YouTube Data API。這類未經頻道主 OAuth 授權的公開 API 快照最多保存 30 天，期限到達後會由本機排程刪除。YouTube Studio 匯入與手動補充資料由使用者主動提供，可依設定保留一個月至十年。</p><p>資料與 API Key 預設只保存在使用者自己的電腦，不會由本專案作者集中收集。移除頻道會一併刪除該頻道的本機監測資料；也可以停止程式後刪除本機資料庫，清除全部內容。</p></div>
        <div><p className="section-kicker">AUTOMATED LABELS</p><h2>休止與疑似畢業標示</h2><p>「休止中」與「疑似已畢業」可能依頻道名稱、簡介、最後公開活動及平常更新節奏自動產生，只是待查證提示，不代表頻道主的正式公告。公開引用或散布前應回到頻道公告人工確認；使用者可在頻道詳細頁修正或恢復自動判斷。</p></div>
        <div><p className="section-kicker">PRIVACY & SECURITY</p><h2>隱私與安全責任</h2><p>請勿匯入與分析無關的敏感資料，也不要提交 `.env`、API Key、本機資料庫或 YouTube Studio 匯出檔。長期保存私人 Analytics 前應確認電腦帳號、磁碟與備份位置受到妥善保護。</p></div>
        <div><p className="section-kicker">PLATFORM TERMS</p><h2>平台條款</h2><p>使用本工具即代表使用者同意遵守 <a href="https://www.youtube.com/t/terms" target="_blank" rel="noreferrer">YouTube 服務條款</a>、<a href="https://developers.google.com/youtube/terms/developer-policies" target="_blank" rel="noreferrer">YouTube API 開發者政策</a>及 <a href="https://policies.google.com/privacy" target="_blank" rel="noreferrer">Google 隱私權政策</a>。介面上的確認視窗不會取代 YouTube 額外授權或合規審核。</p></div>
        <div><p className="section-kicker">LICENSE</p><h2>非商用軟體授權</h2><p>原始碼依 <a href="https://polyformproject.org/licenses/noncommercial/1.0.0" target="_blank" rel="noreferrer">PolyForm Noncommercial License 1.0.0</a> 提供。允許非商業使用、修改及散布；所有原始版本、修改版本、宣傳頁與再散布套件都必須附上完整授權條款與 Required Notice。未取得著作權人另行書面許可，不得將本程式或修改版本用於商業用途。</p><p>本軟體按現狀提供，不保證資料完整、分類正確、持續可用或符合任何特定用途。</p></div>
      </section>

      <LegalFooter context="使用、隱私與授權聲明" note="最後更新：2026-07-28" />
    </main>
  );
}
