'use client';

import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, HelpCircle, RefreshCw, X } from 'lucide-react';
import {
  DeploymentReadinessResponse,
  HumanVerdict,
  ReadinessCheck,
  RuntimePinsResponse,
  SourceStatusResponse,
  SourceStatusRow,
  apiJson,
} from '@/lib/api';
import { buildGithubCommitUrl, buildGithubCompareUrl, shortRevision } from '@/lib/github';
import CopyableCommand from './CopyableCommand';

function VerdictIcon({ level }: { level: HumanVerdict['level'] }) {
  if (level === 'action_required') {
    return <AlertTriangle className="w-4 h-4 text-danger shrink-0 mt-0.5" />;
  }
  if (level === 'unverified') {
    return <HelpCircle className="w-4 h-4 text-secondary shrink-0 mt-0.5" />;
  }
  return <CheckCircle2 className="w-4 h-4 text-ok shrink-0 mt-0.5" />;
}

/** 사전 점검 항목의 `state`를 이 패널의 기존 3단계 어휘로 옮긴다.
 *
 * `warn`을 `action_required`로 올리지 않는다 — 막지 않는 항목을 빨갛게 칠하면 진짜
 * 차단 항목이 묻힌다. */
const READINESS_LEVEL: Record<ReadinessCheck['state'], HumanVerdict['level']> = {
  ok: 'ok',
  warn: 'unverified',
  unknown: 'unverified',
  missing: 'action_required',
};

const READINESS_STATE_TEXT: Record<ReadinessCheck['state'], string> = {
  ok: '문제 없습니다',
  warn: '확인이 필요합니다',
  unknown: '확인할 수 없습니다',
  missing: '지금 재구축하면 실패합니다',
};

function Row({
  label,
  row,
  extra,
}: {
  label: string;
  row: SourceStatusRow;
  extra?: React.ReactNode;
}) {
  return (
    <li className="rounded-card border border-line p-3">
      <div className="flex items-start gap-2">
        <VerdictIcon level={row.human.level} />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-strong font-semibold">{label}</p>
          <p className="text-xs text-secondary mt-0.5">{row.human.text}</p>
          {extra}
          {row.human.next_action ? (
            <p className="text-xs text-secondary mt-1.5 font-mono break-all">
              다음: {row.human.next_action}
            </p>
          ) : null}
        </div>
      </div>
    </li>
  );
}

export default function SourceStatusPanel({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);

  const { data, isLoading, isFetching, error, refetch } = useQuery<SourceStatusResponse>({
    queryKey: ['source-status'],
    queryFn: () => apiJson<SourceStatusResponse>('/api/v1/source-status'),
    retry: false,
  });

  // 고정 pin의 저장소 URL은 pin 카드가 이미 알고 있다. compare 링크를 만들려면 그
  // URL이 필요하므로 같은 응답을 재사용한다(백엔드 추가 호출 없음).
  const { data: pins } = useQuery<RuntimePinsResponse>({
    queryKey: ['runtime-pins'],
    queryFn: () => apiJson<RuntimePinsResponse>('/api/v1/runtime-pins'),
    retry: false,
  });

  // 사전 점검은 별도 엔드포인트다. 실패해도 이 패널의 나머지는 그대로 보여야 하므로
  // 같은 쿼리에 묶지 않는다.
  const {
    data: readiness,
    isFetching: readinessFetching,
    error: readinessError,
    refetch: refetchReadiness,
  } = useQuery<DeploymentReadinessResponse>({
    queryKey: ['deployment-readiness'],
    queryFn: () => apiJson<DeploymentReadinessResponse>('/api/v1/deployment-readiness'),
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

  const remoteFor = (role: string | undefined): string | null =>
    pins?.pins?.sources.find((source) => source.role === role)?.url ?? null;

  return (
    <div
      aria-labelledby="source-status-title"
      aria-modal="true"
      className="ops-modal max-w-4xl flex flex-col outline-hidden"
      ref={dialogRef}
      role="dialog"
      tabIndex={-1}
    >
      <div className="ops-modal__header">
        <div>
          <p className="text-xs text-secondary font-semibold tracking-[0.05em] uppercase">
            Source Status
          </p>
          <h2 className="text-lg font-semibold text-strong mt-1" id="source-status-title">
            지금 뭐가 돌고 있나 (읽기 전용)
          </h2>
          <p className="text-xs text-secondary mt-1">
            재구축 사전 점검, 설치 기록, 작업 사본, 실행 중 이미지, 계약 일치 여부를
            관측만 합니다. 아무것도 바꾸지 않습니다.
          </p>
        </div>
        <button className="ops-icon-button" onClick={onClose} type="button">
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="overflow-y-auto p-6 space-y-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-secondary">
            {data?.cached ? '캐시된 결과입니다.' : '방금 관측한 결과입니다.'}
          </p>
          <button
            className="ops-button"
            disabled={isFetching || readinessFetching}
            onClick={() => {
              void refetch();
              void refetchReadiness();
            }}
            type="button"
          >
            <RefreshCw
              className={`w-4 h-4 ${isFetching || readinessFetching ? 'animate-spin' : ''}`}
            />
            새로고침
          </button>
        </div>

        <section>
          <h3 className="text-sm font-semibold text-strong mb-1">재구축 사전 점검</h3>
          <p className="text-xs text-secondary mb-2">
            지금 재구축을 실행하면 실패할 만한 결손이 있는지만 봅니다. 통과해도 성공을
            보장하지는 않습니다.
          </p>
          {readinessError ? (
            <p className="text-sm text-danger">
              사전 점검 결과를 불러오지 못했습니다.{' '}
              {readinessError instanceof Error
                ? readinessError.message
                : String(readinessError)}
            </p>
          ) : readiness ? (
            <>
              <div
                className={`rounded-card border p-3 ${
                  readiness.summary.state === 'blocked' ? 'border-danger' : 'border-line'
                }`}
              >
                <div className="flex items-start gap-2">
                  <VerdictIcon
                    level={
                      readiness.summary.state === 'blocked'
                        ? 'action_required'
                        : readiness.summary.state === 'unverified'
                          ? 'unverified'
                          : 'ok'
                    }
                  />
                  <p className="text-sm text-strong">{readiness.summary.text}</p>
                </div>
              </div>
              <ul className="space-y-2 mt-2">
                {readiness.checks.map((check) => (
                  <Row
                    key={check.id}
                    label={check.label_ko}
                    row={{
                      state: check.state,
                      human: {
                        level: READINESS_LEVEL[check.state],
                        text: READINESS_STATE_TEXT[check.state],
                        next_action: '',
                      },
                    }}
                    extra={<p className="text-xs text-secondary mt-1">{check.detail}</p>}
                  />
                ))}
              </ul>
              {readiness.unavailable_checks.length > 0 ? (
                <ul className="space-y-2 mt-2">
                  {readiness.unavailable_checks.map((entry) => (
                    <li className="rounded-card border border-line p-3" key={entry.id}>
                      <div className="flex items-start gap-2">
                        <HelpCircle className="w-4 h-4 text-secondary shrink-0 mt-0.5" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm text-strong font-semibold">
                            {entry.label_ko}
                          </p>
                          {/* 검사하지 않기로 결정한 항목을 숨기면 "전부 확인됨"으로
                              읽힌다. 이유까지 그대로 보여 준다. */}
                          <p className="text-xs text-secondary mt-0.5">
                            검사하지 않습니다 — {entry.reason}
                          </p>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-secondary">사전 점검을 수행하는 중입니다.</p>
          )}
        </section>

        {isLoading ? (
          <p className="text-sm text-secondary">배포 상태를 확인하는 중입니다.</p>
        ) : error ? (
          <p className="text-sm text-danger">
            배포 상태를 불러오지 못했습니다.{' '}
            {error instanceof Error ? error.message : String(error)}
          </p>
        ) : data ? (
          <>
            <section
              className={`rounded-card border p-4 ${
                data.summary.level === 'action_required' ? 'border-danger' : 'border-line'
              }`}
            >
              <div className="flex items-start gap-3">
                <VerdictIcon level={data.summary.level} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-strong">{data.summary.text}</p>
                  {data.summary.next_action ? (
                    <CopyableCommand
                      command={data.summary.next_action}
                      hint="SSH에서 실행해 자세히 확인할 수 있습니다."
                    />
                  ) : null}
                </div>
              </div>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-strong mb-2">관리도구 설치 기록</h3>
              <ul className="space-y-2">
                <Row
                  label="설치된 Manager 버전"
                  row={data.manager}
                  extra={
                    data.manager.revision ? (
                      <p className="text-xs text-secondary mt-1 font-mono break-all">
                        {shortRevision(data.manager.revision)}
                      </p>
                    ) : null
                  }
                />
              </ul>
            </section>

            {data.running_images.length > 0 && (
              <section>
                <h3 className="text-sm font-semibold text-strong mb-2">실행 중인 이미지</h3>
                <ul className="space-y-2">
                  {data.running_images.map((row) => {
                    const remote = remoteFor(row.role);
                    const compare =
                      remote && row.revision && row.pinned_revision
                        ? buildGithubCompareUrl(remote, row.revision, row.pinned_revision)
                        : null;
                    const commit =
                      remote && row.revision ? buildGithubCommitUrl(remote, row.revision) : null;
                    return (
                      <Row
                        key={row.role}
                        label={row.label ?? row.role ?? ''}
                        row={row}
                        extra={
                          row.revision ? (
                            <p className="text-xs text-secondary mt-1 break-all">
                              <span className="font-mono">{shortRevision(row.revision)}</span>
                              {compare ? (
                                <>
                                  {' · '}
                                  <a
                                    className="underline"
                                    href={compare}
                                    rel="noreferrer noopener"
                                    target="_blank"
                                  >
                                    고정 버전과 무엇이 다른지 보기
                                  </a>
                                </>
                              ) : commit ? (
                                <>
                                  {' · '}
                                  <a
                                    className="underline"
                                    href={commit}
                                    rel="noreferrer noopener"
                                    target="_blank"
                                  >
                                    커밋 보기
                                  </a>
                                </>
                              ) : null}
                            </p>
                          ) : null
                        }
                      />
                    );
                  })}
                </ul>
              </section>
            )}

            {data.checkouts.length > 0 && (
              <section>
                <h3 className="text-sm font-semibold text-strong mb-2">작업 사본</h3>
                <ul className="space-y-2">
                  {data.checkouts.map((row) => (
                    <Row
                      key={row.role}
                      label={row.label ?? row.role ?? ''}
                      row={row}
                      extra={
                        row.revision ? (
                          <p className="text-xs text-secondary mt-1 font-mono break-all">
                            {shortRevision(row.revision)}
                          </p>
                        ) : null
                      }
                    />
                  ))}
                </ul>
              </section>
            )}

            <section>
              <h3 className="text-sm font-semibold text-strong mb-2">계약 일치</h3>
              <ul className="space-y-2">
                {data.contracts.map((row) => (
                  <Row
                    key={row.id}
                    label={row.title ?? row.id ?? ''}
                    row={row}
                    extra={
                      row.scope === 'sibling_checkout' ? (
                        <p className="text-xs text-secondary mt-1">
                          작업 사본 기준입니다 — 고정된 소스 자체를 검증한 것이 아닙니다.
                        </p>
                      ) : null
                    }
                  />
                ))}
                <Row
                  label={data.environment.title ?? '환경 변수 완결성'}
                  row={data.environment}
                  extra={
                    <>
                      {data.environment.missing.length > 0 ? (
                        <p className="text-xs text-danger mt-1 break-all">
                          누락: {data.environment.missing.join(', ')}
                        </p>
                      ) : null}
                      {data.environment.injected_at_rebuild.length > 0 ? (
                        <p className="text-xs text-secondary mt-1">
                          재구축이 주입하는 값 {data.environment.injected_at_rebuild.length}개는
                          정상적으로 비어 있습니다.
                        </p>
                      ) : null}
                    </>
                  }
                />
              </ul>
            </section>
          </>
        ) : null}
      </div>
    </div>
  );
}
