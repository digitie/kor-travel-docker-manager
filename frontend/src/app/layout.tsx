import type { Metadata } from "next";
import "./globals.css";
import Providers from "./providers";

export const metadata: Metadata = {
  title: "Docker Manager UI",
  description: "Docker 인프라 컨테이너 관리와 메트릭 운영 대시보드",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className="font-sans antialiased bg-page text-strong min-h-screen">
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  );
}
