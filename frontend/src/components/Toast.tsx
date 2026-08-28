'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronDown, X } from 'lucide-react';
import { HumanError } from '@/lib/errors';

export type ToastItem = {
  id: number;
  tone: 'success' | 'error';
  title: string;
  hint?: string;
  /** 실패 원문. 접어 두고 필요할 때만 편다. */
  raw?: string;
};

let nextToastId = 1;

export function successToast(title: string, hint?: string): ToastItem {
  return { id: nextToastId++, tone: 'success', title, hint };
}

export function errorToast(error: HumanError): ToastItem {
  return {
    id: nextToastId++,
    tone: 'error',
    title: error.title,
    hint: error.hint,
    raw: error.raw,
  };
}

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    // 성공은 스스로 사라져도 되지만 실패는 사람이 읽고 닫아야 한다.
    if (item.tone !== 'success') return;
    const timer = window.setTimeout(onDismiss, 6000);
    return () => window.clearTimeout(timer);
  }, [item.tone, onDismiss]);

  return (
    <div
      className={`ops-card w-full max-w-md p-4 shadow-lg border ${
        item.tone === 'error' ? 'border-danger' : 'border-line'
      }`}
      role={item.tone === 'error' ? 'alert' : 'status'}
    >
      <div className="flex items-start gap-3">
        {item.tone === 'error' ? (
          <AlertTriangle className="w-5 h-5 text-danger shrink-0 mt-0.5" />
        ) : (
          <CheckCircle2 className="w-5 h-5 text-success shrink-0 mt-0.5" />
        )}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-strong">{item.title}</p>
          {item.hint ? <p className="text-xs text-secondary mt-1">{item.hint}</p> : null}
          {item.raw ? (
            <>
              <button
                className="text-xs text-secondary underline mt-2 inline-flex items-center gap-1"
                onClick={() => setShowRaw((value) => !value)}
                type="button"
              >
                <ChevronDown className={`w-3 h-3 ${showRaw ? 'rotate-180' : ''}`} />
                자세히
              </button>
              {showRaw ? (
                <pre className="text-[11px] bg-subtle rounded-card p-2 mt-2 overflow-x-auto whitespace-pre-wrap break-all">
                  {item.raw}
                </pre>
              ) : null}
            </>
          ) : null}
        </div>
        <button aria-label="닫기" className="ops-icon-button shrink-0" onClick={onDismiss} type="button">
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

export default function ToastStack({
  items,
  onDismiss,
}: {
  items: ToastItem[];
  onDismiss: (id: number) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 items-end pointer-events-none"
    >
      {items.map((item) => (
        <div className="pointer-events-auto w-full flex justify-end" key={item.id}>
          <ToastCard item={item} onDismiss={() => onDismiss(item.id)} />
        </div>
      ))}
    </div>
  );
}
