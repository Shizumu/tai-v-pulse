import type { Metadata } from "next";
import TrendsDashboard from "./trends-dashboard";

export const metadata: Metadata = {
  title: "趨勢圖表｜台V Pulse",
  description: "查看台灣 VTuber 的訂閱成長、觀看中位數、公開觀看黏著度與頻道比較圖表。",
};

export default function TrendsPage() {
  return <TrendsDashboard />;
}
