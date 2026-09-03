"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

const GUIDE_KEY = "tai-v-pulse-product-guide-v1";
const GUIDE_STEP_KEY = "tai-v-pulse-product-guide-step-v1";
const CONSENT_KEY = "tai-v-pulse-legal-consent-v1";
const IS_PERSONAL_EDITION = process.env.NEXT_PUBLIC_TAI_V_PULSE_EDITION === "personal";

type GuideStep = {
  eyebrow: string;
  title: string;
  description: string;
  points: string[];
  href?: string;
  action?: string;
  metrics?: { term: string; meaning: string }[];
};

const GUIDE_STEPS: GuideStep[] = [
  {
    eyebrow: "WELCOME",
    title: "用四個步驟看懂自己的頻道位置",
    description: "這份導覽會帶你認識四個固定入口。你可以隨時略過，之後從頁首的「使用教學」重新開啟。",
    points: ["先在頻道工作區選擇自己的頻道", "再從內容環境找出同級內容機會", "最後用趨勢圖表持續觀察變化"],
  },
  {
    eyebrow: "STEP 1 · CREATOR",
    title: "頻道工作區：先選擇你要經營的頻道",
    description: "工作區把公開監測、你授權的私人 Analytics、Studio 匯入與手動補值分開保存，避免不同來源被混在一起。",
    points: ["加入自己或團隊管理的頻道", "查看公開概況與私人 Analytics", "建立系統推薦的 3～6 個參考頻道"],
    href: "/creator",
    action: "前往頻道工作區",
  },
  {
    eyebrow: "STEP 2 · MONITOR",
    title: "監測首頁：確認現在正在發生什麼",
    description: "首頁用來查看已收錄頻道、直播排程、資料更新狀態與 API 配額，也能指定新增頻道。",
    points: ["直播雷達顯示目前直播與已知預告", "資料更新狀態說明最近一次收集結果", "背景更新只換資料，不會移動閱讀位置"],
    href: "/",
    action: "前往監測首頁",
  },
  {
    eyebrow: "STEP 3 · LANDSCAPE",
    title: "內容環境：看同量級頻道在做什麼",
    description: "選擇基準頻道、期間和內容形式後，比較同級中位數、常見主題、發布節奏與高效率內容。",
    points: ["基準頻道不納入同級中位數", "直播、一般影片與 Shorts 分開比較", "內容分類是可檢查的規則結果，不是官方 Analytics 分類"],
    href: "/insights",
    action: "前往內容環境",
  },
  {
    eyebrow: "STEP 4 · TRENDS",
    title: "趨勢圖表：追蹤變化是否持續",
    description: "固定比較幾個頻道，觀察訂閱、公開觀看、內容表現與直播動員的歷史走勢。",
    points: ["沒有足夠期間起點時會顯示資料累積中", "市場排行與固定比較可以使用不同群組", "偶發爆量適合當線索，需要後續資料確認"],
    href: "/trends",
    action: "前往趨勢圖表",
  },
  {
    eyebrow: "METRIC GUIDE",
    title: "常用指標怎麼看",
    description: "每個數字都有適用範圍。缺少資料時顯示「—」或「資料累積中」，不會用 0 代替。",
    points: [],
    metrics: [
      { term: "觀看中位數", meaning: "最近期間內容觀看的中間值，較不容易被單一爆量內容影響。" },
      { term: "公開觀看黏著度", meaning: "最近 30 日內容觀看中位數 ÷ 目前訂閱數；不是回訪觀眾或官方留存率。" },
      { term: "直播持續動員", meaning: "完整取樣直播的平均同接中位數 ÷ 訂閱數，以每百位訂閱可持續留下幾位觀眾呈現。" },
      { term: "同級", meaning: "預設為基準頻道訂閱數的 0.5～2 倍；比較時仍要留意內容形式與主題。" },
    ],
  },
];

export default function ProductGuide() {
  const [open, setOpen] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const titleId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const finish = useCallback((status: "completed" | "skipped") => {
    window.localStorage.setItem(GUIDE_KEY, status);
    window.localStorage.removeItem(GUIDE_STEP_KEY);
    setOpen(false);
  }, []);

  useEffect(() => {
    const openGuide = (reset = false) => {
      const savedStep = Number(window.localStorage.getItem(GUIDE_STEP_KEY) ?? "0");
      setStepIndex(reset ? 0 : Math.max(0, Math.min(GUIDE_STEPS.length - 1, Number.isFinite(savedStep) ? savedStep : 0)));
      setOpen(true);
    };
    const mayOpen = () => IS_PERSONAL_EDITION || window.localStorage.getItem(CONSENT_KEY) === "accepted";
    const initial = window.setTimeout(() => {
      if (!window.localStorage.getItem(GUIDE_KEY) && mayOpen()) openGuide();
    }, 250);
    const launch = () => openGuide();
    const consentReady = () => {
      if (!window.localStorage.getItem(GUIDE_KEY)) openGuide();
    };
    window.addEventListener("tai-v-pulse-open-guide", launch);
    window.addEventListener("tai-v-pulse-guide-ready", consentReady);
    return () => {
      window.clearTimeout(initial);
      window.removeEventListener("tai-v-pulse-open-guide", launch);
      window.removeEventListener("tai-v-pulse-guide-ready", consentReady);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") finish("skipped");
      if (event.key === "Tab") {
        const focusable = dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], [tabindex="0"]');
        const first = focusable?.[0];
        const last = focusable?.[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, [finish, open]);

  const setStep = (next: number) => {
    const bounded = Math.max(0, Math.min(GUIDE_STEPS.length - 1, next));
    window.localStorage.setItem(GUIDE_STEP_KEY, String(bounded));
    setStepIndex(bounded);
    dialogRef.current?.scrollTo({ top: 0 });
  };

  const visit = (href: string) => {
    window.localStorage.setItem(GUIDE_KEY, "paused");
    window.localStorage.setItem(GUIDE_STEP_KEY, String(stepIndex));
    window.location.assign(href);
  };

  if (!open) return null;
  const step = GUIDE_STEPS[stepIndex];

  return (
    <div className="product-guide-backdrop" role="presentation">
      <section className="product-guide-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} ref={dialogRef}>
        <div className="product-guide-progress" aria-label={`導覽進度 ${stepIndex + 1}／${GUIDE_STEPS.length}`}>
          {GUIDE_STEPS.map((item, index) => <button className={index === stepIndex ? "active" : index < stepIndex ? "done" : ""} type="button" onClick={() => setStep(index)} aria-label={`前往第 ${index + 1} 步：${item.title}`} aria-current={index === stepIndex ? "step" : undefined} key={item.eyebrow}><i /></button>)}
        </div>
        <div className="product-guide-copy">
          <p className="section-kicker">{step.eyebrow}</p>
          <h2 id={titleId}>{step.title}</h2>
          <p>{step.description}</p>
          {step.points.length > 0 && <ul>{step.points.map((point) => <li key={point}><span aria-hidden="true">✓</span>{point}</li>)}</ul>}
          {step.metrics && <dl className="guide-metric-list">{step.metrics.map((metric) => <div key={metric.term}><dt>{metric.term}</dt><dd>{metric.meaning}</dd></div>)}</dl>}
          {step.href && step.action && <><button className="guide-page-link" type="button" onClick={() => visit(step.href!)}>{step.action}<span aria-hidden="true">→</span></button><p className="guide-visit-note">前往後即可操作頁面；從頁首「使用教學」可接著看。</p></>}
        </div>
        <div className="product-guide-actions">
          <button className="text-button" type="button" onClick={() => finish("skipped")}>稍後再看</button>
          <div>
            <button className="button ghost" type="button" onClick={() => setStep(stepIndex - 1)} disabled={stepIndex === 0}>上一步</button>
            {stepIndex < GUIDE_STEPS.length - 1
              ? <button className="button primary" type="button" onClick={() => setStep(stepIndex + 1)}>下一步</button>
              : <button className="button primary" type="button" onClick={() => finish("completed")}>完成教學</button>}
          </div>
        </div>
      </section>
    </div>
  );
}
