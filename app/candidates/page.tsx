import type { Metadata } from "next";
import CandidateReview from "./candidate-review";

export const metadata: Metadata = {
  title: "候選審核｜台V Pulse",
  description: "查看每次台 V 探索批次的候選頻道、規則證據、未收錄原因與人工處理狀態。",
};

export default function CandidatesPage() {
  return <CandidateReview />;
}
