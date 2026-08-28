'use client';

import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, HelpCircle, RefreshCw, X } from 'lucide-react';
import { RuntimePinsResponse, apiJson } from '@/lib/api';
import CopyableCommand from './CopyableCommand';

const ROLE_LABELS: Record<string, string> = {
  map: '지도 (kor-travel-map)',
  pinvi: 'PinVi',
};

const ROTATE_COMMAND =
  'sudo -n backend/.venv/bin/ktdctl pin rotate --role <map|pinvi> --revision <커밋 SHA> --reason "..." --confirm';

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '알 수 없음';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('ko-KR');
}

function short(digest: string): string {
  return digest.length > 14 ? `${digest.slice(0, 12)}…` : digest;
}

export default function RuntimePinPanel({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);

  const { data, isLoading, isFetching, error, refetch } = useQuery<RuntimePinsResponse>({
    queryKey: ['runtime-pins'],
    queryFn: () => apiJson<RuntimePinsResponse>('/api/v1/runtime-pins'),
    retry: false,
  });

  useEffect(() => {
    dialogRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const summary = data?.summary;
  const lifecycle = data?.lifecycle;
  const pins = data?.pins;
  const unknown = data?.status === 'unknown';
  // stale/degraded는 값은 있으나 권위 있는 값이 아니다 — 정상으로 보여주지 않는다.
  const unverified = data?.status === 'stale' || data?.status === 'degraded';

  return (
    <div
      aria-labelledby="runtime-pin-title"
      aria-modal="true"
      className="ops-modal max-w-4xl flex flex-col outline-hidden"
      ref={dialogRef}
      role="dialog"
      tabIndex={-1}
    >
      <div className="ops-modal__header">
        <div>
          <p className="text-xs text-secondary font-semibold tracking-[0.05em] uppercase">
            Deployment Pins
          </p>
          <h2 className="text-lg font-semibold text-strong mt-1" id="runtime-pin-title">
            고정된 배포 버전 (읽기 전용)
          </h2>
          <p className="text-xs text-secondary mt-1">
            지도와 PinVi를 어느 시점 코드로 재구축할지 고정해 둔 값입니다. 변경은 SSH에서
            `ktdctl pin rotate`로만 가능합니다.
          </p>
        </div>
        <button className="ops-icon-button" onClick={onClose} type="button">
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="overflow-y-auto p-6 space-y-6">
        <div className="flex justify-end">
          <button
            className="ops-button"
            disabled={isFetching}
            onClick={() => void refetch()}
            type="button"
          >
            <RefreshCw className={`w-4 h-4 ${isFetching ? 'animate-spin' : ''}`} />
            새로고침
          </button>
        </div>

        {isLoading ? (
          <p className="text-sm text-secondary">고정 정보를 불러오는 중입니다.</p>
        ) : error ? (
          <p className="text-sm text-danger">
            고정 정보를 불러오지 못했습니다.{' '}
            {error instanceof Error ? error.message : String(error)}
          </p>
        ) : (
          <>
            <section
              className={`rounded-card border p-4 ${
                summary?.state === 'action_required' ? 'border-danger' : 'border-line'
              }`}
            >
              <div className="flex items-start gap-3">
                {unknown || unverified ? (
                  <HelpCircle className="w-5 h-5 text-secondary shrink-0 mt-0.5" />
                ) : summary?.state === 'action_required' ? (
                  <AlertTriangle className="w-5 h-5 text-danger shrink-0 mt-0.5" />
                ) : (
                  <CheckCircle2 className="w-5 h-5 text-ok shrink-0 mt-0.5" />
                )}
                <div className="flex-1">
                  <p className="text-sm text-strong font-semibold">
                    {unknown
                      ? '고정 정보를 확인할 수 없습니다.'
                      : (summary?.text ?? '고정 정보가 등록돼 있습니다.')}
                  </p>
                  {unknown || unverified ? (
                    <p className="text-xs text-secondary mt-1">
                      {unknown
                        ? '이 호스트에 pin registry가 아직 준비되지 않았거나 관리도구가 읽을 수 없습니다. SSH에서 아래 명령으로 초기화하세요.'
                        : '아래 값은 참고용입니다. 권위 있는 값인지 SSH에서 확인하세요.'}
                      {data?.detail ? ` (${data.detail})` : ''}
                    </p>
                  ) : null}
                  {unknown ? (
                    <CopyableCommand command="sudo -n backend/.venv/bin/ktdctl pin init --confirm" />
                  ) : unverified ? (
                    <CopyableCommand command="sudo -n backend/.venv/bin/ktdctl pin verify" />
                  ) : summary?.next_action ? (
                    <>
                      <p className="text-xs text-secondary mt-1">
                        아래 명령을 SSH에서 실행해 새 버전으로 회전해야 합니다.
                      </p>
                      <CopyableCommand command={ROTATE_COMMAND} />
                    </>
                  ) : null}
                </div>
              </div>
            </section>

            {pins ? (
              <section>
                <h3 className="text-sm font-semibold text-strong mb-2">현재 고정 버전</h3>
                <table className="ops-archive-table w-full table-fixed text-sm">
                  <thead className="bg-subtle text-xs text-secondary uppercase tracking-[0.05em]">
                    <tr>
                      <th className="text-left py-2 px-3 font-semibold">대상</th>
                      <th className="text-left py-2 px-3 font-semibold">고정된 커밋</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pins.sources.map((source) => (
                      <tr key={source.role}>
                        <td className="py-2 px-3">{ROLE_LABELS[source.role] ?? source.role}</td>
                        <td className="py-2 px-3 break-all">
                          <a
                            className="underline"
                            href={`${source.url.replace(/\.git$/, '')}/commit/${source.revision}`}
                            rel="noreferrer noopener"
                            target="_blank"
                          >
                            {short(source.revision)}
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-secondary mt-3">
                  <dt>세트 식별자</dt>
                  <dd className="break-all">{short(pins.pinset_sha256)}</dd>
                  <dt>마지막 변경</dt>
                  <dd>
                    {formatTimestamp(pins.rotated_at)} · {pins.rotated_by}
                  </dd>
                  <dt>변경 사유</dt>
                  <dd className="break-all">{pins.reason}</dd>
                </dl>
              </section>
            ) : null}

            {lifecycle && lifecycle.blocked_pinsets.length > 0 ? (
              <section>
                <h3 className="text-sm font-semibold text-strong mb-1">
                  재시도가 금지된 버전 세트
                </h3>
                <p className="text-xs text-secondary mb-2">
                  과거에 재구축이 실패로 종료된 조합입니다. 관리도구가 이 조합의 재실행을
                  자동으로 거부합니다.
                </p>
                <ul className="space-y-2">
                  {lifecycle.blocked_pinsets.map((entry) => (
                    <li className="rounded-card border border-line p-3" key={entry.pinset_sha256}>
                      <p className="text-xs font-semibold text-strong break-all">
                        {short(entry.pinset_sha256)}
                        {entry.pinset_sha256 === pins?.pinset_sha256 ? ' · 현재 고정된 세트' : ''}
                        {entry.phase ? ` · ${entry.phase} 단계 한정` : ''}
                      </p>
                      <p className="text-xs text-secondary mt-1">{entry.reason}</p>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {lifecycle && lifecycle.history.length > 0 ? (
              <section>
                <h3 className="text-sm font-semibold text-strong mb-2">변경 이력</h3>
                <ul className="space-y-1 text-xs text-secondary">
                  {[...lifecycle.history].reverse().map((entry) => (
                    <li className="break-all" key={`${entry.pinset_sha256}-${entry.rotated_at}`}>
                      {formatTimestamp(entry.rotated_at)} · {entry.rotated_by} ·{' '}
                      {short(entry.pinset_sha256)} — {entry.reason}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
