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

// transport 공항 통계 사전집계 갱신 지연(transport #283 run 태그 `kortravel/analytics-refresh`). odroid는 delayed
// 2회 연속(주의), n150은 그 job이 없어 "해당 없음"이다.
const analyticsAlert = {
  key: 'analytics_refresh_lag:kor-travel-transport/airport_collection_job', rule: 'analytics_refresh_lag',
  rule_label: '공항 통계 사전집계 갱신', subject: 'kor-travel-transport/airport_collection_job', severity: 'warning',
  title: '공항 통계 사전집계 갱신 지연: airport_collection_job 최근 2회 연속(delayed)',
  detail: '사전집계에 반영되지 않은 가장 오래된 적재가 15분을 넘었습니다. 대시보드는 아직 직전 사전집계를 냅니다. '
    + '공항 수집 run 자체는 SUCCESS일 수 있습니다 — 수집은 되는데 대시보드 묶음 갱신이 밀린 것입니다.',
  evidence: { statuses: ['delayed', 'delayed'], streak: 2 },
  since: '2026-11-24T05:10:00Z', last_seen: '2026-11-24T05:12:00Z', resolved_at: null,
};

test('수집 경보 패널: 공항 통계 사전집계 갱신 지연은 주의로, transport가 없는 plane은 해당 없음으로 보인다', async ({ page }, testInfo) => {
  let payload: unknown = {
    ...quietPlaneAlerts, host: 'odroid',
    summary: { state: 'firing', text: '경보 1건(심각 0, 주의 1)', critical: 0, warning: 1 },
    alerts: [analyticsAlert],
    checks: [{ rule: 'analytics_refresh_lag', label: '공항 통계 사전집계 갱신', state: 'firing',
      detail: '최근 판정 run delayed · 2026-11-24T05:12:00Z' }],
  };
  const backend = await mockBackend(page, testInfo, { get planeAlerts() { return payload; } });
  await login(page);

  const panel = page.getByRole('region', { name: '수집 경보' });
  await expect(panel).toContainText('공항 통계 사전집계 갱신 지연');
  const item = panel.getByRole('list', { name: '진행 중인 수집 경보' }).getByRole('listitem');
  await expect(item).toHaveCount(1);
  await expect(item).toHaveAttribute('data-severity', 'warning');
  await expect(item).toContainText('주의');
  await expect(item).toContainText('최근 2회 연속(delayed)');
  await expect(item).toContainText('SUCCESS일 수 있습니다');
  await expect(item).toContainText('공항 통계 사전집계 갱신 ·');
  await panel.getByText('점검 항목 1개').click();
  await expect(panel.getByRole('list', { name: '수집 감시 점검 항목' })).toContainText('공항 통계 사전집계 갱신 · 경보');
  await page.screenshot({ path: testInfo.outputPath(`plane-alerts-analytics-${testInfo.project.name}.png`), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);

  // n150: 공항 job이 없다 — 경보 없이 "해당 없음"(정상으로도, 확인 불가로도 읽히지 않는다).
  payload = {
    ...quietPlaneAlerts, host: 'n150',
    checks: [{ rule: 'analytics_refresh_lag', label: '공항 통계 사전집계 갱신', state: 'not_applicable',
      detail: '최근 2시간 안에 끝난 kor-travel-transport/airport_collection_job run이 없습니다' }],
  };
  await page.reload();
  await expect(panel.getByTestId('plane-alerts-summary')).toContainText('경보가 없습니다.');
  await expect(panel.getByRole('list', { name: '진행 중인 수집 경보' })).toHaveCount(0);
  await panel.getByText('점검 항목 1개').click();
  await expect(panel.getByRole('list', { name: '수집 감시 점검 항목' }))
    .toContainText('공항 통계 사전집계 갱신 · 해당 없음 — 최근 2시간 안에 끝난 kor-travel-transport/airport_collection_job run이 없습니다');
  expect(backend.errors).toEqual([]);
});

// 공용 Dagster role 연결 수(소유자 결정 2026-11-25 — 한도 60, 80% 주의·95% 위험, 최근 3회 연속). 문구는 backend
// `evaluate_role_connections`·`_role_connections_round`가 내는 모양 그대로다.
const roleConnectionAlert = {
  key: 'dagster_role_connections:kor_travel_dagster_shared_app', rule: 'dagster_role_connections',
  rule_label: '공용 Dagster role 연결 수', subject: 'kor_travel_dagster_shared_app', severity: 'critical',
  title: '공용 Dagster role kor_travel_dagster_shared_app 연결 58/60(97%) — 최근 3회 평가 연속 95% 이상',
  detail: '한도 60. 한도에 닿으면 PostgreSQL이 이 role의 새 접속을 `too many connections for role`로 거절하고, 그때 접속하던 쪽'
    + '(run worker면 그 run)이 실패합니다. daemon pool(`KOR_TRAVEL_DAGSTER_DAEMON_POOL_*`)을 줄이거나 한도'
    + '(`KTDM_DAGSTER_SHARED_ROLE_CONNECTION_LIMIT`)를 올리기 전에 docs/daemon-pool-adoption.md의 접속 예산을 다시 셉니다. '
    + 'instance 전체 client 연결 81/97.',
  evidence: { connections: 58, limit: 60, ratio: 0.967, recent_ratios: [0.95, 0.967, 0.967], client_connections: 81, capacity: 97 },
  since: '2026-11-25T03:06:00Z', last_seen: '2026-11-25T03:06:00Z', resolved_at: null,
};

test('수집 경보 패널: 공용 Dagster role 연결 수가 한도에 다가가면 위험으로, 평소에는 비율을 점검 항목에 보인다', async ({ page }, testInfo) => {
  let payload: unknown = {
    ...quietPlaneAlerts, host: 'n150',
    summary: { state: 'firing', text: '경보 1건(심각 1, 주의 0)', critical: 1, warning: 0 },
    alerts: [roleConnectionAlert],
    checks: [{ rule: 'dagster_role_connections', label: '공용 Dagster role 연결 수', state: 'firing',
      detail: '58/60(97%) · instance 81/97' }],
  };
  const backend = await mockBackend(page, testInfo, { get planeAlerts() { return payload; } });
  await login(page);

  const panel = page.getByRole('region', { name: '수집 경보' });
  await expect(panel.getByTestId('plane-alerts-summary')).toHaveAttribute('role', 'alert');
  const item = panel.getByRole('list', { name: '진행 중인 수집 경보' }).getByRole('listitem');
  await expect(item).toHaveCount(1);
  await expect(item).toHaveAttribute('data-severity', 'critical');
  await expect(item).toContainText('연결 58/60(97%) — 최근 3회 평가 연속 95% 이상');
  await expect(item).toContainText('too many connections for role');
  await expect(item).toContainText('KTDM_DAGSTER_SHARED_ROLE_CONNECTION_LIMIT');
  await panel.getByText('점검 항목 1개').click();
  await expect(panel.getByRole('list', { name: '수집 감시 점검 항목' }))
    .toContainText('공용 Dagster role 연결 수 · 경보 — 58/60(97%) · instance 81/97');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath(`plane-alerts-role-connections-${testInfo.project.name}.png`), fullPage: true });

  // 경보 없이 비율이 점검 항목에 보이고, 주의선을 한 번 넘은 것(50/60)은 연속 관측을 기다린다고 적힌다.
  payload = {
    ...quietPlaneAlerts, host: 'n150',
    checks: [{ rule: 'dagster_role_connections', label: '공용 Dagster role 연결 수', state: 'ok',
      detail: '50/60(83%) · instance 73/97 · 주의선 넘음 1/3회 연속' }],
  };
  await page.reload();
  await expect(panel.getByTestId('plane-alerts-summary')).toContainText('경보가 없습니다.');
  await panel.getByText('점검 항목 1개').click();
  await expect(panel.getByRole('list', { name: '수집 감시 점검 항목' }))
    .toContainText('공용 Dagster role 연결 수 · 정상 — 50/60(83%) · instance 73/97 · 주의선 넘음 1/3회 연속');
  expect(backend.errors).toEqual([]);
});

test('수집 경보 패널: Telegram 발송 규칙과 최근 실패를 숨기지 않는다', async ({ page }, testInfo) => {
  const telegram = {
    name: 'telegram', enabled: true, configured: true,
    url_env: 'PINVI_TELEGRAM_BOT_TOKEN_DEFAULT · KTDM_ALERT_TELEGRAM_CHAT_ID', last_error: 'HTTP 429',
    last_sent_at: '2026-11-24T01:00:00Z',
    policy: '위험 즉시 · 주의 매일 09시(KST) 요약 · 같은 경보 360분 안 반복 억제', suppressed: 2,
  };
  const backend = await mockBackend(page, testInfo, {
    planeAlerts: { ...quietPlaneAlerts, notifiers: [...quietPlaneAlerts.notifiers, telegram] },
  });
  await login(page);
  const delivery = page.getByRole('region', { name: '수집 경보' }).getByTestId('plane-alerts-delivery');
  await expect(delivery).toContainText('외부 발송: Telegram(위험 즉시 · 주의 매일 09시(KST) 요약');
  await expect(delivery).toContainText('최근 실패 HTTP 429');
  // 토큰 값은 화면에 없다 — 변수 이름도 발송 규칙 문구에 싣지 않는다.
  await expect(delivery).not.toContainText('PINVI_TELEGRAM_BOT_TOKEN_DEFAULT');
  expect(backend.errors).toEqual([]);
});

test('수집 경보 패널: code-server 자동 재시작 규칙·기록·보류 사유를 보인다', async ({ page }, testInfo) => {
  const autoRestart = {
    enabled: true, active_on_host: true, hosts: ['n150'],
    policy: 'grpc fd ≥ 90% 또는 launch 연속 실패 · 그 location 실행 중 run 0 · 컨테이너당 6시간 1회',
    recent: [
      { container: 'pinvi-dagster-code-server-latest', location: 'pinvi.etl.definitions', rule: 'dagster_fd_usage',
        reason: 'grpc 프로세스(pid 4242) 열린 fd 980/1024(96%) ≥ 90%', at: '2026-11-24T01:00:00Z',
        finished_at: '2026-11-24T01:02:00Z', success: true, error: null,
        diagnostics: { before: '/state/plane-watchdog/diagnostics/20261124T010000Z-fd-pinvi', after: null } },
      { container: 'kor-travel-concierge-dagster-code-server-latest', location: 'ktc.dagster.definitions',
        rule: 'launch_failure_streak', reason: 'run launch 5회 연속 실패', at: '2026-11-23T20:00:00Z',
        success: false, error: 'ManagerMutationActiveError: another Manager mutation is already active', diagnostics: {} },
    ],
    skipped: ['pinvi-dagster-code-server-latest: pinvi.etl.definitions에 실행 중 run 2개 — 재시작하지 않습니다'],
  };
  const backend = await mockBackend(page, testInfo, { planeAlerts: { ...quietPlaneAlerts, auto_restart: autoRestart } });
  await login(page);
  const panel = page.getByRole('region', { name: '수집 경보' });
  await expect(panel.getByTestId('plane-alerts-auto-restart')).toContainText('code-server 자동 재시작: grpc fd ≥ 90%');
  const records = panel.getByRole('list', { name: '자동 재시작 기록' }).getByRole('listitem');
  await expect(records).toHaveCount(2);
  await expect(records.first()).toContainText('pinvi-dagster-code-server-latest 재시작 완료');
  await expect(records.nth(1)).toHaveAttribute('data-success', 'false');
  await expect(records.nth(1)).toContainText('another Manager mutation is already active');
  await panel.getByText('자동 재시작 보류 1건').click();
  await expect(panel.getByRole('list', { name: '자동 재시작 보류 사유' })).toContainText('실행 중 run 2개');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath(`plane-alerts-auto-restart-${testInfo.project.name}.png`), fullPage: true });
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
