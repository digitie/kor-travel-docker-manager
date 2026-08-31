'use client';

import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, HelpCircle, RefreshCw, X } from 'lucide-react';
import {
  RuntimePinRequestSummary,
  RuntimePinsResponse,
  apiJson,
  deleteJson,
  postJson,
} from '@/lib/api';
import { HumanError, humanizeError } from '@/lib/errors';
import CopyableCommand from './CopyableCommand';
import InlineError from './InlineError';

const ROLE_LABELS: Record<string, string> = {
  map: '지도 (kor-travel-map)',
  pinvi: 'PinVi',
};

// 이 명령은 `summary.next_action`이 뜨는 상황 — 즉 **현재 세트가 재시도 금지 상태**일 때만
// 보인다. 그 상태에서 registry는 단일 role 회전을 거부하므로(`pin rotate`는 exit 2),
// 여기에 `pin rotate`를 두면 반드시 실패하는 명령을 쥐여 주는 셈이 된다.
const ROTATE_PAIR_COMMAND =
  'sudo -n backend/.venv/bin/ktdctl pin rotate-pair --map-revision <커밋 SHA> ' +
  '--pinvi-revision <커밋 SHA> --reason "..." --confirm';

const CLEAR_FORCE_COMMAND =
  'sudo -n backend/.venv/bin/ktdctl pin clear-pending --force --confirm';

function applyCommand(revision: string | undefined): string {
  // revision을 적어 넣는다. 적지 않으면 CLI가 거부하며, 그 강제 자체가 "무엇을
  // 고정하는지 손으로 확인한다"는 계약이다.
  return `sudo -n backend/.venv/bin/ktdctl pin apply-pending --expect-revision ${
    revision ?? '<커밋 SHA>'
  } --confirm`;
}

const REVISION_PATTERN = /^[0-9a-f]{40}$/;

const MAX_REASON_LENGTH = 500;

function clearPendingCommand(requestId: string): string {
  return `sudo -n backend/.venv/bin/ktdctl pin clear-pending --request-id ${requestId} --confirm`;
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '알 수 없음';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('ko-KR');
}

function short(digest: string): string {
  return digest.length > 14 ? `${digest.slice(0, 12)}…` : digest;
}

function PendingRequestCard({
  request,
  commitUrl,
  onCancel,
  cancelPending,
}: {
  request: RuntimePinRequestSummary;
  commitUrl: string | null;
  onCancel: () => void;
  cancelPending: boolean;
}) {
  if (request.status === 'unreadable') {
    return (
      <section className="rounded-card border border-danger p-4">
        <h3 className="text-sm font-semibold text-strong">대기 중인 요청을 읽지 못했습니다</h3>
        <p className="text-xs text-secondary mt-1">
          {request.detail ?? '요청 파일이 손상됐거나 권한이 맞지 않습니다.'}
        </p>
        {/* 읽지 못하면 id를 알 수 없어 화면에서는 취소할 수 없다. 그 상태에서는 새
            요청도 받지 못하므로, 파일을 지우는 명령을 그대로 준다. */}
        <p className="text-xs text-secondary mt-2">
          이 상태에서는 새 요청도 받을 수 없습니다. SSH에서 아래 명령으로 손상된 파일을
          지운 뒤 다시 요청하세요.
        </p>
        <CopyableCommand command={CLEAR_FORCE_COMMAND} />
      </section>
    );
  }

  const stale = request.status === 'stale';
  return (
    <section className={`rounded-card border p-4 ${stale ? 'border-danger' : 'border-line'}`}>
      <h3 className="text-sm font-semibold text-strong">
        {stale ? '무효가 된 변경 요청' : '적용 대기 중인 변경 요청'}
      </h3>
      <p className="text-xs text-secondary mt-1">
        {stale
          ? '이 요청이 기록된 뒤 고정 값이 바뀌어, 이대로는 적용되지 않습니다. 취소하고 다시 요청하세요.'
          : '아직 아무것도 바뀌지 않았습니다. 아래 명령을 SSH에서 실행해야 실제로 적용됩니다.'}
      </p>
      <dl className="grid grid-cols-[7rem_1fr] gap-x-4 gap-y-1 text-xs text-secondary mt-3">
        <dt>대상</dt>
        <dd className="text-strong">{ROLE_LABELS[request.role ?? ''] ?? request.role}</dd>
        <dt>요청한 커밋</dt>
        <dd className="break-all">
          {commitUrl ? (
            <a className="underline" href={commitUrl} rel="noreferrer noopener" target="_blank">
              {short(request.revision ?? '')}
            </a>
          ) : (
            short(request.revision ?? '')
          )}
        </dd>
        <dt>요청자</dt>
        <dd>
          {request.requested_by} · {formatTimestamp(request.requested_at)}
        </dd>
        <dt>사유</dt>
        <dd className="break-all">{request.reason}</dd>
        <dt>적용 후 세트</dt>
        <dd className="break-all">{short(request.prospective_pinset_sha256 ?? '')}</dd>
      </dl>
      <CopyableCommand
        command={
          stale
            ? clearPendingCommand(request.request_id ?? '<id>')
            : applyCommand(request.revision)
        }
      />
      <button
        className="ops-button mt-3"
        disabled={cancelPending}
        onClick={onCancel}
        type="button"
      >
        요청 취소
      </button>
    </section>
  );
}

export default function RuntimePinPanel({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const [role, setRole] = useState<'map' | 'pinvi'>('map');
  const [revision, setRevision] = useState('');
  const [reason, setReason] = useState('');
  const [formError, setFormError] = useState<HumanError | null>(null);

  const { data, isLoading, isFetching, error, refetch } = useQuery<RuntimePinsResponse>({
    queryKey: ['runtime-pins'],
    queryFn: () => apiJson<RuntimePinsResponse>('/api/v1/runtime-pins'),
    retry: false,
  });

  const createRequest = useMutation({
    mutationFn: () =>
      postJson<unknown>('/api/v1/runtime-pins/requests', { role, revision, reason }),
    onSuccess: () => {
      setRevision('');
      setReason('');
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ['runtime-pins'] });
    },
    onError: (mutationError) => setFormError(humanizeError(mutationError, '변경 요청 기록')),
  });

  const cancelRequest = useMutation({
    mutationFn: (requestId: string) =>
      deleteJson<unknown>(`/api/v1/runtime-pins/requests/${requestId}`),
    onSuccess: () => setFormError(null),
    onError: (mutationError) => setFormError(humanizeError(mutationError, '요청 취소')),
    // 실패했을 때도 다시 읽는다. 404는 "이미 없다"는 뜻이므로, 그대로 두면 사라진
    // 요청의 카드가 계속 남아 취소 버튼과 적용 명령을 권한다.
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['runtime-pins'] }),
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

  const pending = data?.pending_request ?? null;
  const revisionValid = REVISION_PATTERN.test(revision);
  const reasonValid = reason.trim().length > 0;
  // 요청을 받을 수 없는 상태에서 입력칸을 보여 주면, 눌러 본 뒤에야 거부를 알게 된다.
  const canRequest = data?.status === 'ok' && !pending;
  // 요청의 커밋 링크는 registry가 소유한 canonical URL에서만 만든다 — 요청이 URL을
  // 정하지 못하게 하는 백엔드 규칙과 화면을 일치시킨다.
  const pendingRepository = pins?.sources.find((source) => source.role === pending?.role)?.url;
  const pendingCommitUrl =
    pendingRepository && pending?.revision
      ? `${pendingRepository.replace(/\.git$/, '')}/commit/${pending.revision}`
      : null;

  return (
    <div
      aria-labelledby="runtime-pin-title"
      aria-modal="true"
      className="ops-modal max-w-4xl flex flex-col focus-visible:outline-0"
      ref={dialogRef}
      role="dialog"
      tabIndex={-1}
    >
      <div className="ops-modal__header">
        <div>
          <p className="text-xs text-secondary font-semibold">
            배포 버전
          </p>
          <h2 className="text-lg font-semibold text-strong mt-1" id="runtime-pin-title">
            고정된 배포 버전
          </h2>
          <p className="text-xs text-secondary mt-1">
            지도와 PinVi를 어느 시점 코드로 재구축할지 고정해 둔 값입니다. 여기서는 변경
            요청만 기록되고, 실제 적용은 SSH에서 `ktdctl pin apply-pending --expect-revision`을 실행한
            뒤에 이뤄집니다.
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
                        아래 명령을 SSH에서 실행해 새 버전으로 회전해야 합니다. 지금
                        세트는 재시도 금지 상태라 <strong>Map과 PinVi를 한 번에</strong>{' '}
                        바꿔야 합니다 — 한쪽만 바꾸는 명령은 거부됩니다.
                      </p>
                      <CopyableCommand command={ROTATE_PAIR_COMMAND} />
                    </>
                  ) : null}
                </div>
              </div>
            </section>

            {formError ? <InlineError error={formError} /> : null}

            {pending ? (
              <PendingRequestCard
                cancelPending={cancelRequest.isPending}
                commitUrl={pendingCommitUrl}
                onCancel={() => cancelRequest.mutate(pending.request_id ?? '')}
                request={pending}
              />
            ) : null}

            {pins ? (
              <section>
                <h3 className="text-sm font-semibold text-strong mb-2">현재 고정 버전</h3>
                <table className="ops-archive-table w-full table-fixed text-sm">
                  <thead className="bg-subtle text-xs text-secondary">
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

            {!canRequest && !pending ? (
              // 폼이 그냥 사라지면 "왜 못 바꾸지"만 남는다. 무엇이 막고 있는지 말한다.
              <section className="rounded-card border border-line p-4">
                <h3 className="text-sm font-semibold text-strong">버전 변경 요청</h3>
                <p className="text-xs text-secondary mt-1">
                  지금은 변경 요청을 받을 수 없습니다. 현재 고정 값이 권위 있는 값으로
                  확인되지 않으면 어떤 값을 기준으로 바꾸는지 알 수 없기 때문입니다. 아래
                  명령으로 공개 사본을 갱신한 뒤 다시 시도하세요.
                </p>
                <CopyableCommand command="sudo -n backend/.venv/bin/ktdctl pin verify" />
              </section>
            ) : null}

            {canRequest ? (
              <section className="rounded-card border border-line p-4">
                <h3 className="text-sm font-semibold text-strong">버전 변경 요청</h3>
                <p className="text-xs text-secondary mt-1">
                  요청을 기록해도 아직 아무것도 바뀌지 않습니다. 적용은 운영자가 SSH에서
                  아래 명령을 실행할 때 이뤄지며, 그때 커밋 주소와 세트 식별자는 관리도구가
                  다시 계산합니다.
                </p>
                <CopyableCommand
                  command={applyCommand(revisionValid ? revision : undefined)}
                  hint="요청을 기록한 뒤 SSH에서 실행합니다."
                />
                <form
                  className="mt-3 space-y-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    createRequest.mutate();
                  }}
                >
                  <label className="block text-xs text-secondary">
                    대상
                    <select
                      className="ops-input mt-1 w-full"
                      onChange={(event) => setRole(event.target.value as 'map' | 'pinvi')}
                      value={role}
                    >
                      {(['map', 'pinvi'] as const).map((value) => (
                        <option key={value} value={value}>
                          {ROLE_LABELS[value]}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="block text-xs text-secondary">
                    고정할 커밋 SHA
                    <input
                      className="ops-input mt-1 w-full font-mono"
                      onChange={(event) => setRevision(event.target.value.trim())}
                      placeholder="40자리 소문자 16진수"
                      value={revision}
                    />
                  </label>
                  {revision && !revisionValid ? (
                    <p className="text-xs text-danger">
                      40자리 소문자 16진수 커밋 SHA여야 합니다.
                    </p>
                  ) : null}

                  <label className="block text-xs text-secondary">
                    변경 사유
                    <textarea
                      className="ops-input mt-1 w-full"
                      maxLength={MAX_REASON_LENGTH}
                      onChange={(event) => setReason(event.target.value)}
                      rows={2}
                      value={reason}
                    />
                  </label>
                  <p className="text-xs text-secondary">
                    {reason.length}/{MAX_REASON_LENGTH}자 · 사유는 공개 사본과 API 응답에 그대로
                    기록되므로 비밀번호나 토큰을 적지 마세요.
                  </p>

                  <button
                    className="ops-button"
                    disabled={!revisionValid || !reasonValid || createRequest.isPending}
                    type="submit"
                  >
                    변경 요청 기록
                  </button>
                </form>
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
