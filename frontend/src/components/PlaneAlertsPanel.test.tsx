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
