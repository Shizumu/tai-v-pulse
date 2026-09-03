"use client";

import { useEffect, useState } from "react";

const CONSENT_KEY = "tai-v-pulse-legal-consent-v1";
const IS_PERSONAL_EDITION = process.env.NEXT_PUBLIC_TAI_V_PULSE_EDITION === "personal";

export default function LegalConsent() {
  const [ready, setReady] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setAccepted(window.localStorage.getItem(CONSENT_KEY) === "accepted");
      setReady(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  if (IS_PERSONAL_EDITION || !ready || accepted) return null;

  return (
    <div className="modal-backdrop legal-consent-backdrop" role="presentation">
      <section className="confirmation-dialog legal-consent-dialog" role="dialog" aria-modal="true" aria-labelledby="legal-consent-title">
        <p className="section-kicker">BEFORE YOU START</p>
        <h2 id="legal-consent-title">使用前請確認資料與授權聲明</h2>
        <p>台V Pulse 是在你的電腦執行的非官方工具。每位使用者必須使用自己的 API Key，並自行保護本機資料。</p>
        <ul>
          <li>YouTube 公開 API 快照最多保留 30 天；較長保存選項只適用於你經 OAuth 授權同步、自行匯入或手動補充的私人資料。</li>
          <li>系統顯示的休止或疑似畢業狀態只是規則初判，必須人工查證。</li>
          <li>程式採非商用授權；修改、分享或宣傳時必須一併保留授權條款及版權聲明。</li>
        </ul>
        <p className="legal-links"><a href="/legal" target="_blank">閱讀完整使用與隱私聲明</a><a href="https://www.youtube.com/t/terms" target="_blank" rel="noreferrer">YouTube 服務條款</a><a href="https://policies.google.com/privacy" target="_blank" rel="noreferrer">Google 隱私權政策</a></p>
        <label className="consent-check"><input type="checkbox" checked={checked} onChange={(event) => setChecked(event.target.checked)} /><span>我已閱讀並同意上述使用、資料保存與非商用授權條件</span></label>
        <div className="dialog-actions"><button className="button primary" type="button" disabled={!checked} onClick={() => { window.localStorage.setItem(CONSENT_KEY, "accepted"); setAccepted(true); window.dispatchEvent(new Event("tai-v-pulse-guide-ready")); }}>同意並開始使用</button></div>
      </section>
    </div>
  );
}
