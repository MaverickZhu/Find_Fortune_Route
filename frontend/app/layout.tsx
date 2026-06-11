import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Find Fortune Route",
  description: "A-share strategy research and decision support dashboard",
  icons: {
    icon: "/favicon.svg"
  }
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
