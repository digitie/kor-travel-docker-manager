"use client";

import { AppErrorPanel } from "@/components/layout/AppErrorPanel";
import "./globals.css";

export default function GlobalError({
  error,
  reset
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="ko">
      <body className="font-sans antialiased bg-page text-strong min-h-screen">
        <AppErrorPanel error={error} reset={reset} standalone />
      </body>
    </html>
  );
}
