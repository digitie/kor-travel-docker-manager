'use client';

import { useState } from 'react';
import { Copy, Terminal } from 'lucide-react';

/** SSH에서 실행할 명령을 그대로 복사할 수 있게 보여 준다.
 *
 * 이 관리도구에는 의도적으로 CLI 전용으로 남긴 작업이 여럿 있다(파괴적 재구축,
 * secret 회전, 백업 정리 등). 비전문 관리자에게는 "SSH에서 뭘 치라는 건지"가 그
 * 자체로 장벽이므로, CLI 전용 결정마다 실행할 명령 원형을 화면에 둔다. 이것이
 * CLI-전용 정책과 사용성을 동시에 만족하는 최저비용 수단이다. */
export default function CopyableCommand({
  command,
  label,
  hint,
}: {
  command: string;
  /** 이 명령이 무엇을 하는지 한 줄. 생략하면 명령만 보여 준다. */
  label?: string;
  hint?: string;
}) {
  const [copied, setCopied] = useState(false);

  return (
    <div className="rounded-card border border-line p-3">
      {label ? (
        <p className="text-xs font-semibold text-strong flex items-center gap-1.5">
          <Terminal className="w-3.5 h-3.5" />
          {label}
        </p>
      ) : null}
      {hint ? <p className="text-xs text-secondary mt-1">{hint}</p> : null}
      <div className="flex items-start gap-2 mt-2">
        <code className="flex-1 text-xs bg-subtle rounded-card px-3 py-2 break-all">{command}</code>
        <button
          className="ops-button shrink-0"
          onClick={() => {
            // clipboard는 비보안 컨텍스트에서 없을 수 있다. 실패해도 화면은 명령을
            // 그대로 보여 주므로 손으로 옮겨 적을 수 있다 — 조용히 무시한다.
            void navigator.clipboard?.writeText(command).then(
              () => {
                setCopied(true);
                window.setTimeout(() => setCopied(false), 2000);
              },
              () => undefined
            );
          }}
          type="button"
        >
          <Copy className="w-4 h-4" />
          {copied ? '복사됨' : '복사'}
        </button>
      </div>
    </div>
  );
}
