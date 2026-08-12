"use client";

import { ArrowLeft, RefreshCw } from "lucide-react";
import { useEffect, useMemo } from "react";
import {
  errorRecoveryMessage,
  errorReloadStorageKey,
  isLikelyRecoverableNextRuntimeError
} from "@/lib/error-recovery";

type AppErrorPanelProps = {
  error: Error & { digest?: string };
  reset?: () => void;
  standalone?: boolean;
};

function goBack() {
  if (typeof window === "undefined") {
    return;
  }
  if (window.history.length > 1) {
    window.history.back();
    return;
  }
  window.location.assign("/");
}

export function AppErrorPanel({ error, reset, standalone = false }: AppErrorPanelProps) {
  const recoverable = useMemo(() => isLikelyRecoverableNextRuntimeError(error), [error]);
  const details = useMemo(() => errorRecoveryMessage(error), [error]);

  useEffect(() => {
    if (!recoverable || typeof window === "undefined") {
      return;
    }

    const key = errorReloadStorageKey(window.location.pathname);
    if (window.sessionStorage.getItem(key) === "1") {
      return;
    }

    window.sessionStorage.setItem(key, "1");
    window.location.reload();
  }, [recoverable]);

  const retry = () => {
    if (typeof window !== "undefined") {
      window.sessionStorage.removeItem(errorReloadStorageKey(window.location.pathname));
    }
    if (reset) {
      reset();
      return;
    }
    if (typeof window !== "undefined") {
      window.location.reload();
    }
  };

  const buttonBase = "ops-button select-none";

  return (
    <section
      role="alert"
      className={standalone ? "ops-error-shell" : "w-full min-h-[70vh] flex items-center justify-center p-6"}
    >
      <div className="ops-error-card w-full max-w-2xl flex flex-col gap-3.5 p-6">
        <p className="ops-eyebrow">UI runtime error</p>
        <h1 className="font-display text-strong text-2xl font-semibold leading-tight tracking-tight">
          페이지를 다시 불러오지 못했습니다
        </h1>
        <p className="text-secondary text-sm leading-relaxed">
          {recoverable
            ? "현재 탭의 화면 런타임 상태가 서버와 맞지 않아 새로고침이 필요합니다."
            : "현재 탭의 UI 상태가 서버와 맞지 않거나, 화면 렌더링 중 오류가 발생했습니다."}
        </p>
        <div className="flex flex-wrap items-center gap-2.5 mt-1">
          <button
            type="button"
            onClick={retry}
            className={`${buttonBase} ops-button--primary`}
          >
            <RefreshCw className="w-4 h-4" />
            다시 시도
          </button>
          <button
            type="button"
            onClick={goBack}
            className={buttonBase}
          >
            <ArrowLeft className="w-4 h-4" />
            이전 화면
          </button>
        </div>
        <details className="border-t border-line pt-3 mt-1">
          <summary className="text-secondary text-xs cursor-pointer">오류 정보</summary>
          <pre className="bg-subtle border border-line rounded-card text-ink text-xs leading-relaxed mt-2.5 max-h-44 overflow-auto p-2.5 whitespace-pre-wrap font-mono">
            {details || "no details"}
          </pre>
        </details>
      </div>
    </section>
  );
}
