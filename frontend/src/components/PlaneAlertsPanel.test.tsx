import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';

import { renderWithQueryClient } from '@/test-utils';
import { ApiError, type PlaneAlertsResponse } from '@/lib/api';
import PlaneAlertsPanel from './PlaneAlertsPanel';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return { ...actual, apiJson: vi.fn() };
});

import { apiJson } from '@/lib/api';

const mockedApiJson = vi.mocked(apiJson);

const BASE: PlaneAlertsResponse = {
  schema: 'kor-travel-docker-manager.plane-alerts.v1',
  host: 'test-host',
  plane_expected: true,
  enabled: true,
  config_error: null,
  interval_seconds: 120,
  evaluated_at: '2026-11-24T00:00:00Z',
  evaluation_age_seconds: 30,
  stale: false,
  evaluation_error: null,
  summary: { state: 'ok', text: '경보가 없습니다.', critical: 0, warning: 0 },
  alerts: [],
  recent_resolved: [],
  checks: [],
  locations: [],
  dagster_fds: [],
  notifiers: [{ name: 'webhook', enabled: false, configured: false, url_env: 'KTDM_ALERT_WEBHOOK_URL', last_error: null, last_sent_at: null }],
  diagnostics: [],
};

afterEach(() => {
  cleanup();
  mockedApiJson.mockReset();
});

describe('PlaneAlertsPanel', () => {
  it('경보가 없으면 상태 영역과 발송 꺼짐을 보인다', async () => {
    mockedApiJson.mockResolvedValue(BASE);
    renderWithQueryClient(<PlaneAlertsPanel />);
    expect(await screen.findByText('경보가 없습니다.')).toBeInTheDocument();
    expect(screen.getByTestId('plane-alerts-summary')).toHaveAttribute('role', 'status');
    expect(screen.getByTestId('plane-alerts-delivery')).toHaveTextContent('외부 발송은 꺼져 있습니다');
    expect(mockedApiJson).toHaveBeenCalledWith('/api/v1/plane-alerts', expect.anything());
  });

  it('진행 중인 경보를 심각도와 함께 보인다', async () => {
    mockedApiJson.mockResolvedValue({
      ...BASE,
      summary: { state: 'firing', text: '경보 1건(심각 0, 주의 1)', critical: 0, warning: 1 },
      alerts: [{
        key: 'dagster_fd_usage:c', rule: 'dagster_fd_usage', rule_label: 'Dagster 열린 fd', subject: 'c',
        severity: 'warning', title: 'c: grpc 프로세스(pid 12) 열린 fd 600/1024(59%)', detail: '누수 의심',
        evidence: {}, since: '2026-11-24T00:00:00Z', last_seen: null, resolved_at: null,
      }],
    } satisfies PlaneAlertsResponse);
    renderWithQueryClient(<PlaneAlertsPanel />);
    const list = await screen.findByRole('list', { name: '진행 중인 수집 경보' });
    expect(list).toHaveTextContent('주의');
    expect(list).toHaveTextContent('열린 fd 600/1024(59%)');
    expect(screen.getByTestId('plane-alerts-summary')).toHaveAttribute('role', 'alert');
  });

  it('공항 통계 사전집계 경보와 "해당 없음" 점검을 보인다', async () => {
    mockedApiJson.mockResolvedValue({
      ...BASE,
      summary: { state: 'firing', text: '경보 1건(심각 1, 주의 0)', critical: 1, warning: 0 },
      alerts: [{
        key: 'analytics_refresh_lag:kor-travel-transport/airport_collection_job', rule: 'analytics_refresh_lag',
        rule_label: '공항 통계 사전집계 갱신', subject: 'kor-travel-transport/airport_collection_job',
        severity: 'critical', title: '공항 통계 사전집계 갱신 멈춤: airport_collection_job 최근 2회 연속(stale)',
        detail: '대시보드가 원본 계산으로 떨어졌습니다.', evidence: { statuses: ['stale', 'stale'] },
        since: '2026-11-24T05:00:00Z', last_seen: null, resolved_at: null,
      }],
      checks: [{ rule: 'analytics_refresh_lag', label: '공항 통계 사전집계 갱신', state: 'not_applicable',
        detail: '최근 2시간 안에 끝난 kor-travel-transport/airport_collection_job run이 없습니다' }],
    } satisfies PlaneAlertsResponse);
    renderWithQueryClient(<PlaneAlertsPanel />);
    const list = await screen.findByRole('list', { name: '진행 중인 수집 경보' });
    expect(list).toHaveTextContent('심각');
    expect(list).toHaveTextContent('공항 통계 사전집계 갱신 멈춤');
    expect(list).toHaveTextContent('공항 통계 사전집계 갱신 ·');
    expect(screen.getByRole('list', { name: '수집 감시 점검 항목', hidden: true }))
      .toHaveTextContent('공항 통계 사전집계 갱신 · 해당 없음');
  });

  it('Telegram 발송 규칙과 chat 미설정을 보인다', async () => {
    const telegram = {
      name: 'telegram', enabled: true, configured: true,
      url_env: 'PINVI_TELEGRAM_BOT_TOKEN_DEFAULT · KTDM_ALERT_TELEGRAM_CHAT_ID', last_error: null, last_sent_at: null,
      policy: '위험 즉시 · 주의 매일 09시(KST) 요약 · 같은 경보 360분 안 반복 억제', suppressed: 0,
    };
    mockedApiJson.mockResolvedValue({ ...BASE, notifiers: [BASE.notifiers[0], telegram] });
    renderWithQueryClient(<PlaneAlertsPanel />);
    expect(await screen.findByTestId('plane-alerts-delivery')).toHaveTextContent(
      '외부 발송: Telegram(위험 즉시 · 주의 매일 09시(KST) 요약 · 같은 경보 360분 안 반복 억제)');
    cleanup();
    mockedApiJson.mockResolvedValue({ ...BASE, notifiers: [BASE.notifiers[0], { ...telegram, enabled: false, configured: false }] });
    renderWithQueryClient(<PlaneAlertsPanel />);
    expect(await screen.findByTestId('plane-alerts-delivery')).toHaveTextContent(
      'Telegram은 받는 chat(KTDM_ALERT_TELEGRAM_CHAT_ID)이 비어 있어 보내지 않습니다');
  });

  it('감시자가 멈추면 그 사실을 보인다', async () => {
    mockedApiJson.mockResolvedValue({
      ...BASE, stale: true,
      summary: { state: 'unknown', text: '감시가 30분 동안 평가하지 않았습니다.', critical: 0, warning: 0 },
    });
    renderWithQueryClient(<PlaneAlertsPanel />);
    expect(await screen.findByText(/감시자가 평가를 멈춘 것 같습니다/)).toBeInTheDocument();
  });

  it('조회 실패는 패널 안에 보인다(조용히 사라지지 않는다)', async () => {
    mockedApiJson.mockRejectedValue(new ApiError(500, 'boom', null));
    renderWithQueryClient(<PlaneAlertsPanel />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});
