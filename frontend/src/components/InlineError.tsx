'use client';

import { useState } from 'react';
import { HumanError } from '@/lib/errors';

/** 모달 안에서 실패를 그 자리에 보여 준다.
 *
 * 토스트 스택은 `DashboardClient`가 소유하므로 모달이 자기 실패를 화면 구석으로 보내면
 * 방금 누른 버튼 옆이 비어 보인다. **원문(`raw`)을 반드시 접어서 남긴다** — 그것이
 * 운영자가 이슈에 붙여넣을 값이고, 손으로 만든 title/hint만 남기면 서버가 말한 구체적인
 * 내용이 사라진다. */
export default function InlineError({ error }: { error: HumanError }) {
  const [showRaw, setShowRaw] = useState(false);
  return (
    <div className="rounded-card border border-danger p-3" role="alert">
      <p className="text-sm font-semibold text-danger">{error.title}</p>
      <p className="text-xs text-secondary mt-1">{error.hint}</p>
      {error.requestId ? (
        // GM-16: 서버 로그·감사 행과 조인하는 키다 — 스크린샷 하나로 추적할 수
        // 있도록 접지 않고 바로 보여준다.
        <p className="text-[11px] text-secondary mt-1 font-mono break-all">
          요청 ID: {error.requestId}
        </p>
      ) : null}
      {error.raw ? (
        <>
          <button
            className="text-xs text-secondary underline mt-2"
            onClick={() => setShowRaw((value) => !value)}
            type="button"
          >
            자세히
          </button>
          {showRaw ? (
            <pre className="text-[11px] bg-subtle rounded-card p-2 mt-2 overflow-x-auto whitespace-pre-wrap break-all">
              {error.raw}
            </pre>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
