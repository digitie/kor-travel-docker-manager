import { test, expect } from '@playwright/test';

// 운영 Docker·인증 서버를 호출하지 않고 common UI의 실제 브라우저 계약을 검증한다.
test('공통 로그인·표·확인·모달·설정 행과 로그아웃', async ({ page }, testInfo) => {
  let authenticated = false;
  let actions = 0;
  const container = { id: 'demo', name: 'demo-service', display_name: '검증 서비스', role: 'api',
    status: 'running', state: 'running', ports: ['12990:9000'],
    config: { ports: ['12990:9000', '12991:9001'], volumes: [], networks: ['default'], env: {} } };
  const errors: string[] = []; page.on('pageerror', (error) => errors.push(error.message));
  // 격리 서버의 정적 파일만 실제로 읽고, 운영 UI/API 등 다른 주소는 접속하지 않는다.
  await page.route('**/*', async (route) => {
    if (new URL(route.request().url()).origin === String(testInfo.project.use.baseURL)) await route.continue();
    else await route.abort();
  });
  await page.routeWebSocket('**/api/v1/ws/**', () => {});
  await page.route('http://localhost:12901/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path.endsWith('/auth/login')) authenticated = true;
    if (path.endsWith('/auth/logout')) authenticated = false;
    if (path.endsWith('/action')) actions++;
    if (path.endsWith('/auth/me')) body = { authenticated, username: 'admin', expires_at: '' };
    else if (path.endsWith('/containers')) body = [container];
    else if (path.endsWith('/targets')) body = [];
    else if (path.endsWith('/inspect')) body = { ...container, restart_count: 0, state: { status: 'running', running: true },
      mounts: [{ type: 'bind', source: '/example', destination: '/data', rw: false }] };
    else if (path.endsWith('/backups/offbox-sync-status')) body = { configured: false, status: null };
    else if (path.endsWith('/backups')) body = { roles: ['geo'], backups: [{ role: 'geo', created_at_unix: 1_794_351_600,
      duration_sec: 2, sha256: 'a'.repeat(64), byte_size: 1024, backup_filename: 'demo-backup.dump', instance: 'demo',
      db_size_bytes: 1024, toc_entry_count: 2, alembic_head: null }] };
    else if (path.endsWith('/runtime-pins')) body = { status: 'ok', source: 'test', summary: { state: 'ok', text: '검증 상태', next_action: '' },
      pins: { release_version: 1, pinset_sha256: 'b'.repeat(64), sources: [{ role: 'map', url: 'https://example.com/map.git', revision: 'c'.repeat(40) }],
        rotated_at: '2026-11-11T00:00:00Z', rotated_by: 'tester', reason: '검증' }, lifecycle: { blocked_pinsets: [], history: [] } };
    else if (path.endsWith('/disk-usage')) body = { state: 'unknown', summary: { text: '미확인', detail: '검증용 상태', next_action: '' } };
    else if (path === '/health') body = { status: 'healthy' };
    await route.fulfill({ status: 200, json: body, headers: {
      'Access-Control-Allow-Origin': String(testInfo.project.use.baseURL), 'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    } });
  });
  await page.goto('/');
  await page.getByLabel('비밀번호', { exact: true }).fill('example-test-value');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  const table = page.getByRole('table', { name: '서비스 원장' });
  await expect(table).toContainText('demo-service');
  if (testInfo.project.name === 'mobile') {
    const rail = await page.getByRole('complementary', { name: '주요 메뉴' }).boundingBox();
    expect(rail?.height).toBeLessThan(160);
  }
  const stop = table.getByRole('button', { name: '중지', exact: true });
  await stop.click(); await expect(page.getByRole('alertdialog')).toContainText('검증 서비스');
  expect(actions).toBe(0); await page.getByRole('button', { name: '취소', exact: true }).click();
  await expect(stop).toBeFocused(); expect(actions).toBe(0);
  await stop.click(); await page.getByRole('button', { name: '계속', exact: true }).click();
  await expect.poll(() => actions).toBe(1);
  await table.getByTitle('inspect 상세(상태·리소스·mounts·networks·healthcheck·env) 보기').click();
  const detail = page.getByRole('dialog', { name: /상세 정보/ });
  await expect(detail).toBeVisible(); await detail.getByRole('tab', { name: '마운트' }).click();
  await expect(detail.getByRole('table', { name: '마운트' })).toContainText('/data');
  await page.keyboard.press('Escape'); await expect(detail).toHaveCount(0);
  await table.getByTitle('컨테이너 세부 설정 변경').click();
  const config = page.getByRole('dialog', { name: '컨테이너 설정 변경' });
  const port = config.getByLabel('포트 매핑 2', { exact: true });
  await port.fill('12992:9002'); await expect(port).toBeFocused();
  await config.getByLabel('포트 매핑 1 삭제', { exact: true }).click();
  await expect(config.getByLabel('포트 매핑 1', { exact: true })).toHaveValue('12992:9002');
  await page.screenshot({ path: testInfo.outputPath('common-ui.png'), fullPage: false });
  await page.keyboard.press('Escape'); await expect(config).toHaveCount(0);
  await page.getByRole('button', { name: '백업 이력', exact: true }).click();
  await expect(page.getByRole('table', { name: '백업 이력' })).toContainText('demo-backup.dump');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '배포 버전 고정', exact: true }).click();
  await expect(page.getByRole('table', { name: '현재 고정 버전' })).toContainText('cccc');
  await page.keyboard.press('Escape');
  await expect(page.locator('body')).toHaveCSS('font-family', /Pretendard/);
  await page.getByRole('button', { name: '빠른 명령', exact: true }).first().click();
  await page.getByRole('dialog', { name: '빠른 명령' }).getByRole('button', { name: /로그아웃/ }).click();
  await expect(page.getByRole('button', { name: '로그인', exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
