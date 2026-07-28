import type { Metadata } from "next";
import CreatorDashboard from "./creator-dashboard";

export const metadata: Metadata = {
  title: "頻道工作區｜台V Pulse",
  description: "把多個管理頻道的公開監測、YouTube Studio 匯出報表與手動補充資料整理在同一個本機工作區。",
};

export default function CreatorPage() {
  return <CreatorDashboard />;
}
