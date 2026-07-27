import type { Metadata } from "next";
import Dashboard from "./dashboard";

export const metadata: Metadata = {
  title: "台V Pulse｜私人數據監測台",
  description: "在本機整理台灣 VTuber 頻道、直播同接與資料擷取狀態。",
};

export default function Home() {
  return <Dashboard />;
}
