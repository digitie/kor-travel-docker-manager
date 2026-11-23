import { test, expect } from '@playwright/test';
import { login, mockBackend, openMenu, quietPlaneAlerts } from './mock-backend';

// 감사(2026-11-23) 당시의 두 사건을 그대로 띄운 응답: odroid weather location 정지(11-19)와
// 특보 이벤트 3주기 성공 없음(11-18). 외부 발송은 꺼져 있다(소유자 결정 대기).
const firingPlaneAlerts = {
  ...quietPlaneAlerts,
  summary: { state: 'firing', text: '경보 2건(심각 2, 주의 0)', critical: 2, warning: 0 },
  alerts: [
    {
      key: 'location_tick_stall:kortravelweather_dagster.definitions', rule: 'location_tick_stall',
      rule_label: 'location tick 정지', subject: 'kortravelweather_dagster.definitions', severity: 'critical',
      title: 'kortravelweather_dagster.definitions: 34분째 schedule·sensor tick이 없습니다',
      detail: '활성 schedule·sensor 14개 중 어느 것도 tick을 남기지 않았습니다.',
      evidence: {}, since: '2026-11-19T17:50:00Z', last_seen: '2026-11-19T17:50:00Z', resolved_at: null,
    },
    {
      key: 'schedule_no_success:kortravelweather_dagster.definitions/regional_alert_events_every_five_minutes',
      rule: 'schedule_no_success', rule_label: 'schedule 주기 내 성공 없음',
      subject: 'kortravelweather_dagster.definitions/regional_alert_events_every_five_minutes', severity: 'critical',
      title: 'kma_regional_alert_events_job: 최근 3주기(45분) 동안 성공이 없습니다',
      detail: '창 안 종료 run 9건이 모두 실패·취소입니다.',
      evidence: {}, since: '2026-11-18T07:52:00Z', last_seen: '2026-11-18T07:52:00Z', resolved_at: null,
    },
  ],
  checks: [
    { rule: 'location_tick_stall', label: 'location tick 정지', state: 'firing', detail: '1건' },
    { rule: 'io_pressure', label: 'IO 압력(PSI)', state: 'unavailable', detail: '/proc/pressure/io가 없습니다(커널 PSI 비활성)' },
  ],
  diagnostics: [{ location: 'kortravelweather_dagster.definitions', captured_at: '2026-11-19T17:50:05Z',
    directory: '/state/plane-watchdog/diagnostics/20261119T175000Z-weather', py_spy: 'missing' }],
};

test('수집 경보 패널: 진행 중인 경보·점검 항목·발송 꺼짐을 대시보드 본문에 보인다', async ({ page }, testInfo) => {
  const backend = await mockBackend(page, testInfo, { planeAlerts: firingPlaneAlerts });
  await login(page);

  const panel = page.getByRole('region', { name: '수집 경보' });
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId('plane-alerts-summary')).toHaveAttribute('role', 'alert');
  await expect(panel.getByTestId('plane-alerts-summary')).toContainText('경보 2건(심각 2, 주의 0)');

  const list = panel.getByRole('list', { name: '진행 중인 수집 경보' });
  await expect(list.getByRole('listitem')).toHaveCount(2);
  await expect(list).toContainText('34분째 schedule·sensor tick이 없습니다');
  await expect(list).toContainText('최근 3주기(45분) 동안 성공이 없습니다');
  await expect(list.getByRole('listitem').first()).toHaveAttribute('data-severity', 'critical');
  await expect(list.getByRole('listitem').first()).toContainText('심각');

  // 발송 채널은 소유자 결정이다 — 꺼져 있다는 사실을 숨기지 않는다.
  await expect(panel.getByTestId('plane-alerts-delivery')).toContainText('외부 발송은 꺼져 있습니다');

  // 측정하지 못한 점검은 "확인 불가"로 보인다(정상으로 읽히지 않는다).
  await panel.getByText('점검 항목 2개').click();
  await expect(panel.getByRole('list', { name: '수집 감시 점검 항목' })).toContainText('IO 압력(PSI) · 확인 불가');
  await panel.getByText('정지 진단 파일 1건').click();
  await expect(panel).toContainText('py-spy missing');
  await page.screenshot({ path: testInfo.outputPath(`plane-alerts-${testInfo.project.name}.png`), fullPage: true });

  // 메뉴의 앵커로 패널에 간다.
  const menu = await openMenu(page);
  await menu.getByRole('link', { name: '수집 경보' }).click();
  await expect(page).toHaveURL(/#plane-alerts$/);
  await expect(panel).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
  expect(backend.errors).toEqual([]);
});

test('수집 경보 패널: 경보가 없으면 상태로, 감시자가 멈추면 그 사실을 보인다', async ({ page }, testInfo) => {
  const stale = {
    ...quietPlaneAlerts, stale: true, evaluation_age_seconds: 1800,
    summary: { state: 'unknown', text: '감시가 30분 동안 평가하지 않았습니다.', critical: 0, warning: 0 },
  };
  let payload: unknown = quietPlaneAlerts;
  await mockBackend(page, testInfo, { get planeAlerts() { return payload; } });
  await login(page);

  const panel = page.getByRole('region', { name: '수집 경보' });
  await expect(panel.getByTestId('plane-alerts-summary')).toHaveAttribute('role', 'status');
  await expect(panel.getByTestId('plane-alerts-summary')).toContainText('경보가 없습니다.');
  await expect(panel.getByRole('list', { name: '진행 중인 수집 경보' })).toHaveCount(0);

  payload = stale;
  await page.reload();
  await expect(panel.getByTestId('plane-alerts-summary')).toContainText('감시가 30분 동안 평가하지 않았습니다.');
  await expect(panel).toContainText('감시자가 평가를 멈춘 것 같습니다');
});
