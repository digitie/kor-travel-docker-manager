import { expect, type Page, type TestInfo } from '@playwright/test';

// 운영 Docker·인증 서버를 호출하지 않는 합성 백엔드. 격리 서버의 정적 파일만 실제로 읽는다.
export const demoContainer = { id: 'demo', name: 'demo-service', display_name: '검증 서비스', role: 'api',
  status: 'running', state: 'running', ports: ['12990:9000'],
  config: { ports: ['12990:9000', '12991:9001'], volumes: [], networks: ['default'], env: {} } };

export type MockBackend = { actions: () => number; errors: string[] };

export async function mockBackend(page: Page, testInfo: TestInfo): Promise<MockBackend> {
  let authenticated = false;
  let actions = 0;
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const origin = String(testInfo.project.use.baseURL);
  await page.route('**/*', async (route) => {
    if (new URL(route.request().url()).origin === origin) await route.continue();
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
    else if (path.endsWith('/containers')) body = [demoContainer];
    else if (path.endsWith('/targets')) body = [];
    else if (path.endsWith('/inspect')) body = { ...demoContainer, restart_count: 0, state: { status: 'running', running: true },
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
      'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    } });
  });
  return { actions: () => actions, errors };
}

export async function login(page: Page) {
  await page.goto('/');
  await page.getByLabel('비밀번호', { exact: true }).fill('example-test-value');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await expect(page.getByRole('table', { name: '서비스 원장' })).toContainText('demo-service');
}

/** 좁은 폭에서는 주 메뉴가 drawer 안에 있다. 열린 drawer(dialog)를 돌려준다. */
export async function openMenu(page: Page) {
  const menuButton = page.locator('[data-slot="admin-shell-menu-button"]');
  if (!(await menuButton.isVisible())) return page.locator('[data-slot="admin-shell-rail"]');
  await menuButton.click();
  const drawer = page.getByRole('dialog', { name: '메뉴' });
  await expect(drawer).toBeVisible();
  return drawer;
}
