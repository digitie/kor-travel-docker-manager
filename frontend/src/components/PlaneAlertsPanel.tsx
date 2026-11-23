'use client';

import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, BellRing, CheckCircle2, HelpCircle } from 'lucide-react';
import type { ReactNode } from 'react';

import { type PlaneAlert, type PlaneAlertCheck, type PlaneAlertsResponse, apiJson } from '@/lib/api';
import { humanizeError } from '@/lib/errors';
import InlineError from './InlineError';

/** 공용 Dagster plane 수집 경보 패널(대시보드 본문).
 *
 * 감시는 Manager backend가 Dagster 밖에서 돌리고, 이 패널은 마지막 평가 결과를 읽기만 한다.
 * 감사(2026-11-23)는 PinVi·concierge run 전부 실패와 특보 이벤트 100% 실패를 며칠 동안 아무도
 * 몰랐다고 적었다 — 그래서 대화상자 뒤가 아니라 대시보드 본문에 항상 보인다. */

const SEVERITY_TEXT: Record<PlaneAlert['severity'], string> = { critical: '심각', warning: '주의' };

const CHECK_STATE_TEXT: Record<PlaneAlertCheck['state'], string> = {
  ok: '정상',
  firing: '경보',
  unavailable: '확인 불가',
  disabled: '꺼짐',
  not_applicable: '해당 없음',
};

function formatKst(value: string | null | undefined): string {
  if (!value) return '알 수 없음';
  return new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' });
}

function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '';
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))}초 전`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}분 전`;
  return `${Math.round(seconds / 3600)}시간 전`;
}

function AlertRow({ alert }: { alert: PlaneAlert }) {
  const critical = alert.severity === 'critical';
  return (
    <li
      className={`rounded-card border p-3 ${critical ? 'border-danger' : 'border-warn'}`}
      data-severity={alert.severity}
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className={`w-4 h-4 shrink-0 mt-0.5 ${critical ? 'text-danger' : 'text-warn'}`} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-strong break-words">
            <span className={`ops-status-badge mr-2 ${critical ? 'text-danger' : 'text-warn'}`}>
              {SEVERITY_TEXT[alert.severity] ?? alert.severity}
            </span>
            {alert.title}
          </p>
          <p className="text-xs text-secondary mt-1 break-words">{alert.detail}</p>
          <p className="text-xs text-secondary mt-1">
            {alert.rule_label} · {formatKst(alert.since)}부터
          </p>
        </div>
      </div>
    </li>
  );
}

function SummaryIcon({ state }: { state: PlaneAlertsResponse['summary']['state'] | undefined }) {
  if (state === 'firing' || state === 'error') {
    return <AlertTriangle className="w-5 h-5 shrink-0 text-danger" />;
  }
  if (state === 'ok') return <CheckCircle2 className="w-5 h-5 shrink-0 text-ok" />;
  return <HelpCircle className="w-5 h-5 shrink-0 text-secondary" />;
}

function Summary({ data }: { data: PlaneAlertsResponse | undefined }) {
  const state = data?.summary?.state;
  const loud = state === 'firing' || state === 'error';
  return (
    <div
      className={`flex items-start gap-3 rounded-card border p-3 ${loud ? 'border-danger' : 'border-line'}`}
      data-testid="plane-alerts-summary"
      role={loud ? 'alert' : 'status'}
    >
      <SummaryIcon state={state} />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-strong">{data?.summary?.text ?? '감시 상태를 알 수 없습니다.'}</p>
        {data?.stale ? (
          <p className="text-xs text-danger mt-1">
            감시자가 평가를 멈춘 것 같습니다 — Manager backend 로그(journald)를 확인하세요.
          </p>
        ) : null}
        {data?.evaluation_error ? (
          <p className="text-xs text-danger mt-1 break-all">평가 오류: {data.evaluation_error}</p>
        ) : null}
      </div>
    </div>
  );
}

function deliveryText(notifiers: PlaneAlertsResponse['notifiers']): string {
  const delivering = notifiers.filter((notifier) => notifier.enabled);
  if (delivering.length === 0) {
    return '외부 발송은 꺼져 있습니다 — 이 화면, 상태 API, Manager 로그로만 보입니다(발송 채널은 소유자 결정).';
  }
  const failures = delivering.map((notifier) => notifier.last_error).filter(Boolean);
  const names = delivering.map((notifier) => notifier.name).join(', ');
  return failures.length > 0 ? `외부 발송: ${names} (최근 실패: ${failures.join(', ')})` : `외부 발송: ${names}`;
}

function Disclosure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details>
      <summary className="text-xs font-semibold text-secondary cursor-pointer">{label}</summary>
      {children}
    </details>
  );
}

function Details({ data }: { data: PlaneAlertsResponse | undefined }) {
  const checks = data?.checks ?? [];
  const captures = data?.diagnostics ?? [];
  const resolved = data?.recent_resolved ?? [];
  return (
    <>
      {checks.length > 0 ? (
        <Disclosure label={`점검 항목 ${checks.length}개`}>
          <ul aria-label="수집 감시 점검 항목" className="mt-2 space-y-1">
            {checks.map((check) => (
              <li className="text-xs text-secondary break-words" key={check.rule}>
                <span className="font-semibold text-strong">{check.label}</span>
                {` · ${CHECK_STATE_TEXT[check.state] ?? check.state}`}
                {check.detail ? ` — ${check.detail}` : ''}
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
      {captures.length > 0 ? (
        <Disclosure label={`정지 진단 파일 ${captures.length}건`}>
          <ul className="mt-2 space-y-1">
            {captures.map((capture) => (
              <li className="text-xs text-secondary font-mono break-all" key={`${capture.location}-${capture.captured_at}`}>
                {`${capture.location} · ${formatKst(capture.captured_at)} · ${capture.directory ?? capture.error ?? ''}`}
                {capture.py_spy ? ` · py-spy ${capture.py_spy}` : ''}
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
      {resolved.length > 0 ? (
        <Disclosure label={`최근 해소 ${resolved.length}건`}>
          <ul className="mt-2 space-y-1">
            {resolved.map((alert) => (
              <li className="text-xs text-secondary break-words" key={`${alert.key}-${alert.resolved_at}`}>
                {`${alert.title} · ${formatKst(alert.resolved_at)} 해소`}
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
    </>
  );
}

function Body({ data }: { data: PlaneAlertsResponse | undefined }) {
  const alerts = data?.alerts ?? [];
  return (
    <>
      <Summary data={data} />
      {alerts.length > 0 ? (
        <ul aria-label="진행 중인 수집 경보" className="space-y-2">
          {alerts.map((alert) => (
            <AlertRow alert={alert} key={alert.key} />
          ))}
        </ul>
      ) : null}
      <p className="text-xs text-secondary" data-testid="plane-alerts-delivery">
        {deliveryText(data?.notifiers ?? [])}
      </p>
      <Details data={data} />
    </>
  );
}

function evaluationText(data: PlaneAlertsResponse | undefined): string {
  if (!data?.evaluated_at) return '';
  return ` 마지막 평가 ${formatKst(data.evaluated_at)}(${formatAge(data.evaluation_age_seconds)}).`;
}

export default function PlaneAlertsPanel() {
  const { data, error, isLoading } = useQuery<PlaneAlertsResponse>({
    queryKey: ['plane-alerts'],
    queryFn: ({ signal }) => apiJson<PlaneAlertsResponse>('/api/v1/plane-alerts', { signal }),
    refetchInterval: 60_000,
    retry: false,
  });

  let body: ReactNode = <Body data={data} />;
  if (error) body = <InlineError error={humanizeError(error, '수집 경보 조회')} />;
  else if (isLoading) body = <p className="text-sm text-secondary">수집 경보를 불러오는 중입니다.</p>;

  return (
    <section aria-labelledby="plane-alerts-title" className="ops-ledger" id="plane-alerts">
      <div className="ops-ledger__header">
        <div>
          <h2 className="ops-section-title flex items-center gap-2" id="plane-alerts-title">
            <BellRing aria-hidden="true" size={16} />
            수집 경보
          </h2>
          <p className="ops-section-copy">
            공용 Dagster plane의 tick 정지, 주기 내 성공 없음, 연속 실패, launch 실패, 디스크·IO·fd를
            Dagster 밖(Manager)에서 {data?.interval_seconds ?? '?'}초마다 평가합니다.{evaluationText(data)}
          </p>
        </div>
      </div>
      <div className="p-6 space-y-4">{body}</div>
    </section>
  );
}
