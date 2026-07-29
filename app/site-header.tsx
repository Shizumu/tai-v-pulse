"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useState, type ReactNode } from "react";

type PageKey = "monitor" | "insights" | "trends" | "creator";

type SiteHeaderProps = {
  active: PageKey;
  eyebrow: string;
  title: string;
  connected: boolean;
  statusText?: string;
  actions?: ReactNode;
  compact?: boolean;
};

const NAV_ITEMS: { key: PageKey; href: string; label: string }[] = [
  { key: "monitor", href: "/", label: "監測首頁" },
  { key: "insights", href: "/insights", label: "內容環境" },
  { key: "trends", href: "/trends", label: "趨勢圖表" },
  { key: "creator", href: "/creator", label: "頻道工作區" },
];

const THEMES = [
  ["mint", "薄荷綠"],
  ["pink", "櫻花粉"],
  ["blue", "天空藍"],
  ["purple", "薰衣草紫"],
  ["yellow", "奶油黃"],
] as const;

export default function SiteHeader({
  active,
  eyebrow,
  title,
  connected,
  statusText,
  actions,
  compact = false,
}: SiteHeaderProps) {
  const [theme, setTheme] = useState("mint");

  useEffect(() => {
    const saved = window.localStorage.getItem("tai-v-pulse-theme") ?? "mint";
    if (THEMES.some(([value]) => value === saved)) setTheme(saved);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("tai-v-pulse-theme", theme);
  }, [theme]);

  return (
    <header className="topbar global-topbar">
      <div className={`brand-block${compact ? " compact" : ""}`}>
        <div className="brand-mark" aria-hidden="true">V</div>
        <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1></div>
      </div>
      <nav className="page-nav" aria-label="主要頁面">
        {NAV_ITEMS.map((item) => (
          <a className={active === item.key ? "active" : undefined} href={item.href} aria-current={active === item.key ? "page" : undefined} key={item.key}>{item.label}</a>
        ))}
      </nav>
      <div className="status-cluster">
        <label className="theme-picker"><span>外觀</span><select aria-label="頁面顏色主題" value={theme} onChange={(event) => setTheme(event.target.value)}>{THEMES.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <span className={`connection ${connected ? "online" : "offline"}`}><i />{statusText ?? (connected ? "本機資料已連線" : "等待本機服務")}</span>
        {actions}
      </div>
    </header>
  );
}
