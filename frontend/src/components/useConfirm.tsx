'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertDialog, AlertDialogContent, AlertDialogTitle, AlertDialogDescription,
  AlertDialogAction, AlertDialogCancel } from '@kor-travel/ui/primitives';

/** 동시 요청은 거부하고 패널이 사라지면 대기 중 조작도 취소한다. */
export function useConfirm(enabled = true) {
  const [message, setMessage] = useState<string | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const pending = useRef<((accepted: boolean) => void) | null>(null);
  const finish = useCallback((accepted: boolean) => {
    const resolve = pending.current;
    pending.current = null;
    setMessage(null);
    resolve?.(accepted);
  }, []);
  useEffect(() => () => { pending.current?.(false); pending.current = null; }, []);
  useEffect(() => {
    if (enabled) return () => finish(false);
  }, [enabled, finish]);
  const confirm = useCallback((text: string) => {
    if (!enabled || pending.current) return Promise.resolve(false);
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    return new Promise<boolean>((resolve) => { pending.current = resolve; setMessage(text); });
  }, [enabled]);
  const confirmation = <AlertDialog open={message !== null} onOpenChange={(open) => { if (!open) finish(false); }}>
    <AlertDialogContent onCloseAutoFocus={(event) => { if (returnFocus.current?.isConnected) { event.preventDefault(); returnFocus.current.focus(); } }}>
      <AlertDialogTitle>작업 확인</AlertDialogTitle>
      <AlertDialogDescription className="whitespace-pre-wrap">{message}</AlertDialogDescription>
      <div className="flex justify-end gap-2">
        <AlertDialogCancel onClick={() => finish(false)}>취소</AlertDialogCancel>
        <AlertDialogAction onClick={() => finish(true)}>계속</AlertDialogAction>
      </div>
    </AlertDialogContent>
  </AlertDialog>;
  return { confirm, confirmation };
}
