import type { Metadata } from "next";
import CreatorDashboard from "./creator-dashboard";

export const metadata: Metadata = {
  title: "我的頻道｜台V Pulse",
  description: "把公開監測資料、YouTube Studio 匯出報表與手動補充資料整理在同一個本機頁面。",
};

export default function CreatorPage() {
  return <CreatorDashboard />;
}
