import type { Page, Response } from '@playwright/test';

import type { PlaneAlertCheck, PlaneAlertsResponse } from '../src/lib/api';
import { expect, login, logout, noHorizontalOverflow, test } from './live-fixtures';

// 운영 Manager 대시보드 읽기 전용 live 시험(n150, scripts/n150-live-e2e.sh). 화면이 운영 상태 API가 실제로
// 낸 값을 그대로 보이는지 대조한다 — 고정 fixture가 아니라 그 순간의 운영 응답이 기대값이다.

const CHECK_STATE_TEXT: Record<PlaneAlertCheck['state'], string> = {
  ok: '정상', firing: '경보', unavailable: '확인 불가', disabled: '꺼짐', not_applicable: '해당 없음',
};
const EXPECT_HOST = process.env.KTDM_LIVE_EXPECT_HOST || 'n150';
// 배포 직후 수집 감시의 첫 평가(12시간 run 조회 포함)가 끝날 때까지 기다리는 상한.
const FIRST_EVALUATION_WAIT_MS = 300_000;

function isPath(response: Response, path: string) {
  return new URL(response.url()).pathname === path && response.request().method() === 'GET';
}

function checkLine(check: PlaneAlertCheck) {
  return `${check.label} · ${CHECK_STATE_TEXT[check.state]}${check.detail ? ` — ${check.detail}` : ''}`;
}

/** 로그인한 뒤 첫 평가가 끝난 수집 경보 응답을 돌려준다. 패널은 60초마다 다시 읽으므로, 이후 응답은
 * `latest()`로 따라간다(화면과 대조할 때 마지막 응답을 기준으로 한다). */
async function loginWithPlaneAlerts(page: Page) {
  const seen: { latest: PlaneAlertsResponse | null } = { latest: null };
  page.on('response', async (response) => {
    if (!isPath(response, '/api/v1/plane-alerts') || response.status() !== 200) return;
    try { seen.latest = (await response.json()) as PlaneAlertsResponse; } catch { /* 페이지 이동 중 본문 유실 */ }
  });
  const first = page.waitForResponse((r) => isPath(r, '/api/v1/plane-alerts'));
  await login(page);
  expect((await first).status(), '수집 경보 조회').toBe(200);
  const deadline = Date.now() + FIRST_EVALUATION_WAIT_MS;
  await expect.poll(() => seen.latest !== null).toBe(true);
  while (!seen.latest?.evaluated_at) {
    expect(Date.now() < deadline, `수집 감시가 ${FIRST_EVALUATION_WAIT_MS / 1000}초 안에 첫 평가를 마치지 않았습니다`).toBe(true);
    const next = page.waitForResponse((r) => isPath(r, '/api/v1/plane-alerts'));
    await page.waitForTimeout(15_000);
    await page.reload();
    await next;
    await expect(page.getByRole('table', { name: '서비스 원장' })).toBeVisible();
  }
  return (): PlaneAlertsResponse => {
    if (!seen.latest) throw new Error('수집 경보 응답이 없습니다');
    return seen.latest;
  };
}

test('로그인 → 대시보드(서비스 원장·상태 WebSocket) → 로그아웃 전환', async ({ page }, testInfo) => {
  type Socket = { frames: number; closed: boolean };
  const statusSockets: Socket[] = [];
  page.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname !== '/api/v1/ws/status') return;
    const entry: Socket = { frames: 0, closed: false };
    statusSockets.push(entry);
    socket.on('framereceived', () => { entry.frames += 1; });
    socket.on('close', () => { entry.closed = true; });
  });
  const containers = page.waitForResponse((r) => isPath(r, '/api/v1/containers'));
  await login(page);
  const response = await containers;
  expect(response.status(), '컨테이너 조회').toBe(200);
  const items = (await response.json()) as { name: string }[];
  expect(items.length, '운영 Manager가 컨테이너를 하나도 보고하지 않습니다').toBeGreaterThan(0);

  const ledger = page.getByRole('table', { name: '서비스 원장' });
  await expect(ledger.locator('tbody tr').first()).toBeVisible();
  // 원장은 묶음·정렬을 거치므로 응답의 첫 항목이 아니라 응답 이름 가운데 하나가 행으로 보이는지 본다.
  // 짧은 이름은 아무 글자에나 걸리므로 8자 이상만 쓴다.
  const names = items.map((item) => item.name).filter((name) => name.length >= 8);
  expect(names.length, '대조할 컨테이너 이름이 없습니다').toBeGreaterThan(0);
  await expect.poll(async () => {
    const rows = await ledger.locator('tbody tr').allInnerTexts();
    return rows.some((row) => names.some((name) => row.includes(name)));
  }).toBe(true);

  // 상태 WebSocket은 붙어서 실제로 프레임을 받고(서버는 2초마다 보낸다), 12초 동안 끊기거나 재연결을
  // 되풀이하지 않는다(배포 뒤 반복적으로 깨졌던 항목 — accept 직후 4401로 닫히는 경우도 여기서 걸린다).
  await expect.poll(() => statusSockets.length, { timeout: 20_000 }).toBeGreaterThan(0);
  await page.waitForTimeout(12_000);
  expect(statusSockets.length, '상태 WebSocket 재연결 반복').toBeLessThanOrEqual(2);
  const current = statusSockets[statusSockets.length - 1];
  expect(current.closed, '상태 WebSocket이 열린 채로 남지 않았습니다').toBe(false);
  expect(current.frames, '상태 WebSocket이 프레임을 받지 못했습니다').toBeGreaterThanOrEqual(2);
  await noHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`dashboard-${testInfo.project.name}.png`) });

  await logout(page);
  // 회수한 세션으로는 다시 들어가지 못한다.
  await page.reload();
  await expect(page.getByRole('button', { name: '로그인', exact: true })).toBeVisible();
  await expect(page.getByRole('table', { name: '서비스 원장' })).toHaveCount(0);
});

test('수집 경보 패널: 점검 항목·발송 상태가 운영 응답과 같고, 감시가 살아 있다', async ({ page }, testInfo) => {
  test.setTimeout(FIRST_EVALUATION_WAIT_MS + 120_000);
  const latest = await loginWithPlaneAlerts(page);
  const data = latest();

  // 감시 자체가 살아 있어야 한다(평가 정지·설정 오류는 패널이 아무리 조용해도 실패다).
  expect(data.schema).toBe('kor-travel-docker-manager.plane-alerts.v1');
  expect(data.host, '수집 경보가 보고하는 plane host').toBe(EXPECT_HOST);
  expect(data.enabled, '수집 감시가 꺼져 있습니다').toBe(true);
  expect(data.config_error, '수집 감시 설정 오류').toBeFalsy();
  expect(data.evaluation_error, '수집 감시 평가 오류').toBeFalsy();
  expect(data.stale, '수집 감시가 평가를 멈췄습니다').toBe(false);

  const panel = page.getByRole('region', { name: '수집 경보' });
  await expect(panel).toBeVisible();
  // 화면은 60초마다 다시 읽으므로 화면과의 대조는 마지막 응답을 기준으로, 잠깐 어긋나면 다시 본다.
  await expect(async () => {
    const now = latest();
    const summary = panel.getByTestId('plane-alerts-summary');
    await expect(summary).toContainText(now.summary.text, { timeout: 2_000 });
    await expect(summary).toHaveAttribute('role',
      now.summary.state === 'firing' || now.summary.state === 'error' ? 'alert' : 'status', { timeout: 2_000 });
    const alertList = panel.getByRole('list', { name: '진행 중인 수집 경보' });
    if (now.alerts.length === 0) await expect(alertList).toHaveCount(0, { timeout: 2_000 });
    else await expect(alertList.getByRole('listitem')).toHaveCount(now.alerts.length, { timeout: 2_000 });
  }).toPass({ timeout: 30_000 });

  // 점검 항목: 응답의 항목 전부가 "이름 · 상태 — 설명"으로 보인다.
  await panel.getByText(`점검 항목 ${data.checks.length}개`, { exact: true }).click();
  const checks = panel.getByRole('list', { name: '수집 감시 점검 항목' });
  await expect(async () => {
    const now = latest();
    const text = (await checks.innerText()).replace(/\s+/g, ' ');
    for (const check of now.checks) expect(text).toContain(checkLine(check).replace(/\s+/g, ' '));
  }).toPass({ timeout: 30_000 });

  // 공용 Dagster role 연결 수는 실제로 측정돼 "N/한도(비율)"로 보인다. n150 한도는 60(소유자 결정 2026-11-25).
  const roleCheck = data.checks.find((check) => check.rule === 'dagster_role_connections');
  expect(roleCheck, '공용 Dagster role 연결 수 점검이 없습니다').toBeDefined();
  expect(['ok', 'firing'], `role 연결 수를 측정하지 못했습니다: ${roleCheck?.detail}`).toContain(roleCheck?.state);
  expect(roleCheck?.detail).toMatch(EXPECT_HOST === 'n150' ? /^\d+\/60\(\d+%\)/ : /^\d+\/\d+(\(\d+%\))?/);
  await expect(checks).toContainText(/공용 Dagster role 연결 수 · (정상|경보) — \d+\/\d+/);

  // 공항 통계 사전집계 갱신: n150에는 공항 수집 job이 없어 "해당 없음"이다(정상·확인 불가가 아니다).
  const analytics = data.checks.find((check) => check.rule === 'analytics_refresh_lag');
  expect(analytics, '공항 통계 사전집계 갱신 점검이 없습니다').toBeDefined();
  if (EXPECT_HOST === 'n150') {
    expect(analytics?.state, `n150 공항 통계 사전집계 갱신: ${analytics?.detail}`).toBe('not_applicable');
    await expect(checks).toContainText('공항 통계 사전집계 갱신 · 해당 없음');
  }

  // 외부 발송: 켜진 채널이 없으면 꺼져 있다는 사실과 Telegram이 보내지 않는 까닭을 숨기지 않는다.
  const delivery = panel.getByTestId('plane-alerts-delivery');
  await expect(async () => {
    const notifiers = latest().notifiers;
    const text = await delivery.innerText();
    const enabled = notifiers.filter((notifier) => notifier.enabled);
    if (enabled.length === 0) {
      expect(text).toContain('외부 발송은 꺼져 있습니다');
      const telegram = notifiers.find((notifier) => notifier.name === 'telegram');
      expect(telegram, 'Telegram 발송 상태가 응답에 없습니다').toBeDefined();
      expect(telegram?.disabled_reason, 'Telegram이 꺼져 있는데 까닭이 없습니다').toBeTruthy();
      expect(text).toContain(`Telegram은 보내지 않습니다 — ${telegram?.disabled_reason}`);
    } else {
      expect(text).toContain('외부 발송: ');
      for (const notifier of enabled) {
        if (notifier.last_error) expect(text).toContain(`최근 실패 ${notifier.last_error}`);
      }
    }
  }).toPass({ timeout: 30_000 });
  // 발송 문구에 Telegram bot 토큰 값 모양(숫자:문자열)이 보이지 않는다(변수 이름은 사유로 보일 수 있다).
  expect(await delivery.innerText()).not.toMatch(/\d{6,}:[A-Za-z0-9_-]{30,}/);

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
