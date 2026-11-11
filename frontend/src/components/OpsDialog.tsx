'use client';

import { useState, type ComponentProps, type ReactNode } from 'react';
import { Dialog, DialogContent, DialogTitle } from '@kor-travel/ui/primitives';

/** 운영 패널의 상태만 연결하고 포커스·Escape·portal은 공통 Radix에 맡긴다. */
export default function OpsDialog({ onClose, children, className, onCloseAutoFocus, ...props }: ComponentProps<typeof DialogContent> & {
  onClose: () => void; children: ReactNode;
}) {
  // 조건부로 여는 패널에는 Radix Trigger가 없으므로 실제 호출 버튼을 보존한다.
  const [returnFocus] = useState(() => typeof document !== 'undefined' && document.activeElement instanceof HTMLElement ? document.activeElement : null);
  const sizeClass = className?.match(/\bmax-w-([a-z0-9]+)\b/)?.[0];
  const panelClass = `${className?.replace(/\brelative\b/g, 'fixed') ?? ''} w-[calc(100%-2rem)] sm:w-full${sizeClass ? ` sm:${sizeClass}` : ''}`;
  return <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
    <DialogContent showCloseButton={false} {...props} aria-describedby={undefined}
      className={panelClass}
      onCloseAutoFocus={(event) => {
        onCloseAutoFocus?.(event);
        if (!event.defaultPrevented && returnFocus?.isConnected) { event.preventDefault(); returnFocus.focus(); }
      }}>
      <DialogTitle className="sr-only">{props['aria-label'] ?? '운영 도구'}</DialogTitle>
      {children}
    </DialogContent>
  </Dialog>;
}
