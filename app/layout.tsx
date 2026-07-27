import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "台V Pulse",
  description: "私人使用的台灣 VTuber YouTube 數據監測工具。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
