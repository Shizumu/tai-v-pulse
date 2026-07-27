import type { Metadata } from "next";
import InsightsDashboard from "./insights-dashboard";

export const metadata: Metadata = {
  title: "內容環境｜台V Pulse",
  description: "比較同量級台灣 VTuber 的內容組合、發布頻率、觀看與直播表現。",
};

export default function InsightsPage() {
  return <InsightsDashboard />;
}
