import type { Page, Response } from '@playwright/test';

import type { PlaneAlertCheck, PlaneAlertsResponse } from '../src/lib/api';
import { expect, login, logout, noHorizontalOverflow, test } from './live-fixtures';

// 운영 Manager 대시보드 읽기 전용 live 시험(n150, scripts/n150-live-e2e.sh). 화면이 운영 상태 API가 실제로
// 낸 값을 그대로 보이는지 대조한다 — 고정 fixture가 아니라 그 순간의 운영 응답이 기대값이다.

const CHECK_STATE_TEXT: Record<PlaneAlertCheck['state'], string> = {
  ok: '정상', firing: '경보', unavailable: '확인 불가', disabled: '꺼짐', not_applicable: '해당 없음',
};
const EXPECT_HOST = process.env.KTDM_LIVE_EXPECT_HOST || 'n150';

function isPath(response: Response, path: string) {
  return new URL(response.url()).pathname === path && response.request().method() === 'GET';
}

/** 로그인한 뒤 대시보드가 처음 받은 수집 경보 응답을 돌려준다. */
async function loginWithPlaneAlerts(page: Page): Promise<PlaneAlertsResponse> {
  const first = page.waitForResponse((r) => isPath(r, '/api/v1/plane-alerts'));
  await login(page);
  const response = await first;
  expect(response.status(), '수집 경보 조회').toBe(200);
  return (await response.json()) as PlaneAlertsResponse;
}

test('로그인 → 대시보드(서비스 원장·상태 WebSocket) → 로그아웃 전환', async ({ page }, testInfo) => {
  const statusSockets: string[] = [];
  page.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname === '/api/v1/ws/status') statusSockets.push(socket.url());
  });
  const containers = page.waitForResponse((r) => isPath(r, '/api/v1/containers'));
  await login(page);
  const response = await containers;
  expect(response.status(), '컨테이너 조회').toBe(200);
  const items = (await response.json()) as { name: string }[];
  expect(items.length, '운영 Manager가 컨테이너를 하나도 보고하지 않습니다').toBeGreaterThan(0);

  const ledger = page.getByRole('table', { name: '서비스 원장' });
  await expect(ledger.locator('tbody tr').first()).toBeVisible();
  // 원장은 묶음·정렬을 거치므로 응답의 첫 항목이 아니라 응답 이름 가운데 하나가 보이는지 본다.
  const names = items.map((item) => item.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  await expect(ledger).toContainText(new RegExp(names.join('|')));

  // 상태 WebSocket이 붙고, 재연결을 되풀이하지 않는다(배포 뒤 반복적으로 깨졌던 항목).
  await expect.poll(() => statusSockets.length, { timeout: 20_000 }).toBeGreaterThan(0);
  await page.waitForTimeout(12_000);
  expect(statusSockets.length, '상태 WebSocket 재연결 반복').toBeLessThanOrEqual(2);
  await noHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`dashboard-${testInfo.project.name}.png`) });

  await logout(page);
  // 회수한 세션으로는 다시 들어가지 못한다.
  await page.reload();
  await expect(page.getByRole('button', { name: '로그인', exact: true })).toBeVisible();
  await expect(page.getByRole('table', { name: '서비스 원장' })).toHaveCount(0);
});

test('수집 경보 패널: 점검 항목·발송 상태가 운영 응답과 같고, 감시가 살아 있다', async ({ page }, testInfo) => {
  const data = await loginWithPlaneAlerts(page);

  // 감시 자체가 살아 있어야 한다(평가 정지·설정 오류는 패널이 아무리 조용해도 실패다).
  expect(data.schema).toBe('kor-travel-docker-manager.plane-alerts.v1');
  expect(data.host, '수집 경보가 보고하는 plane host').toBe(EXPECT_HOST);
  expect(data.enabled, '수집 감시가 꺼져 있습니다').toBe(true);
  expect(data.config_error, '수집 감시 설정 오류').toBeFalsy();
  expect(data.evaluation_error, '수집 감시 평가 오류').toBeFalsy();
  expect(data.stale, '수집 감시가 평가를 멈췄습니다').toBe(false);

  const panel = page.getByRole('region', { name: '수집 경보' });
  await expect(panel).toBeVisible();
  const summary = panel.getByTestId('plane-alerts-summary');
  await expect(summary).toContainText(data.summary.text);
  await expect(summary).toHaveAttribute('role',
    data.summary.state === 'firing' || data.summary.state === 'error' ? 'alert' : 'status');
  const alertList = panel.getByRole('list', { name: '진행 중인 수집 경보' });
  if (data.alerts.length === 0) await expect(alertList).toHaveCount(0);
  else await expect(alertList.getByRole('listitem')).toHaveCount(data.alerts.length);

  // 점검 항목: 응답의 항목 전부가 "이름 · 상태 — 설명"으로 보인다.
  await panel.getByText(`점검 항목 ${data.checks.length}개`, { exact: true }).click();
  const checks = panel.getByRole('list', { name: '수집 감시 점검 항목' });
  await expect(checks.getByRole('listitem')).toHaveCount(data.checks.length);
  for (const check of data.checks) {
    await expect(checks).toContainText(
      `${check.label} · ${CHECK_STATE_TEXT[check.state]}${check.detail ? ` — ${check.detail}` : ''}`);
  }

  // 공용 Dagster role 연결 수는 실제로 측정돼 "N/60(비율)"로 보인다(소유자 결정 2026-11-25 — 한도 60).
  const roleCheck = data.checks.find((check) => check.rule === 'dagster_role_connections');
  expect(roleCheck, '공용 Dagster role 연결 수 점검이 없습니다').toBeDefined();
  expect(['ok', 'firing'], `role 연결 수를 측정하지 못했습니다: ${roleCheck?.detail}`).toContain(roleCheck?.state);
  expect(roleCheck?.detail).toMatch(/^\d+\/60\(\d+%\)/);
  await expect(checks).toContainText(/공용 Dagster role 연결 수 · (정상|경보) — \d+\/60\(\d+%\)/);

  // 공항 통계 사전집계 갱신: n150에는 공항 수집 job이 없어 "해당 없음"이다(정상·확인 불가가 아니다).
  const analytics = data.checks.find((check) => check.rule === 'analytics_refresh_lag');
  expect(analytics, '공항 통계 사전집계 갱신 점검이 없습니다').toBeDefined();
  if (EXPECT_HOST === 'n150') {
    expect(analytics?.state, `n150 공항 통계 사전집계 갱신: ${analytics?.detail}`).toBe('not_applicable');
    await expect(checks).toContainText('공항 통계 사전집계 갱신 · 해당 없음');
  }

  // 외부 발송: 켜진 채널이 없으면 꺼져 있다는 사실과 Telegram이 보내지 않는 까닭을 숨기지 않는다.
  const delivery = panel.getByTestId('plane-alerts-delivery');
  const enabled = data.notifiers.filter((notifier) => notifier.enabled);
  if (enabled.length === 0) {
    await expect(delivery).toContainText('외부 발송은 꺼져 있습니다');
    const telegram = data.notifiers.find((notifier) => notifier.name === 'telegram');
    expect(telegram, 'Telegram 발송 상태가 응답에 없습니다').toBeDefined();
    expect(telegram?.disabled_reason, 'Telegram이 꺼져 있는데 까닭이 없습니다').toBeTruthy();
    await expect(delivery).toContainText(`Telegram은 보내지 않습니다 — ${telegram?.disabled_reason}`);
  } else {
    await expect(delivery).toContainText('외부 발송: ');
    for (const notifier of enabled) {
      if (notifier.last_error) await expect(delivery).toContainText(`최근 실패 ${notifier.last_error}`);
    }
  }
  // 발송 문구에 토큰·chat 변수 이름을 싣지 않는다.
  await expect(delivery).not.toContainText('TOKEN');

  await noHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`plane-alerts-${testInfo.project.name}.png`), fullPage: true });
  await logout(page);
});

test('로그인하지 않은 브라우저는 대시보드·상태 API를 보지 못한다', async ({ page }) => {
  const me = page.waitForResponse((r) => isPath(r, '/api/v1/auth/me'));
  await page.goto('/');
  const meResponse = await me;
  await expect(page.getByRole('button', { name: '로그인', exact: true })).toBeVisible();
  await expect(page.getByRole('table', { name: '서비스 원장' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '수집 경보' })).toHaveCount(0);
  // 같은 backend의 보호 API를 cookie 없이 직접 부르면 거절된다.
  const backend = new URL(meResponse.url()).origin;
  for (const path of ['/api/v1/plane-alerts', '/api/v1/containers']) {
    const response = await page.request.get(`${backend}${path}`, { headers: { Origin: new URL(page.url()).origin } });
    expect([401, 403], `${path} 무인증 응답`).toContain(response.status());
  }
});
