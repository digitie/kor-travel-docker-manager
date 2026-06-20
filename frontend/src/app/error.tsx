"use client";

import { AppErrorPanel } from "@/components/layout/AppErrorPanel";

export default function Error({
  error,
  reset
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <AppErrorPanel error={error} reset={reset} />;
}
