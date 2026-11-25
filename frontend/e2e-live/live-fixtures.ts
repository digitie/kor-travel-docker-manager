import { test as base, expect, type Page } from '@playwright/test';

import { clearPasswordFields, liveEnv } from './live-env';

/** 운영 Manager에 붙는 읽기 전용 live 시험의 공통 장치.
 *
 * Manager 로그인 세션은 컨테이너를 멈추고 다시 만들 수 있는 관리자 권한이다. 그래서 계정 권한이
 * 아니라 이 fixture가 운영을 지킨다: 로그인·로그아웃 밖의 변경 요청(GET·HEAD 밖)은 운영으로 보내지
 * 않고 브라우저에서 끊은 뒤, 시험 끝에 그런 요청이 하나라도 있었으면 실패시킨다. context 단위라 새 탭·
 * popup도 같은 규칙을 받고, service worker는 설정에서 막는다. */
const AUTH_PATHS = new Set(['/api/v1/auth/login', '/api/v1/auth/logout']);

export type LiveGuard = { blocked: string[]; errors: string[] };

export const test = base.extend<{ guard: LiveGuard }>({
  guard: [async ({ page, context }, use) => {
    const state: LiveGuard = { blocked: [], errors: [] };
    page.on('pageerror', (error) => state.errors.push(`${error.name}: ${error.message}`));
    await context.route('**/*', async (route) => {
      const request = route.request();
      const method = request.method();
      if (method === 'GET' || method === 'HEAD' || AUTH_PATHS.has(new URL(request.url()).pathname)) {
        await route.fallback();
        return;
      }
      state.blocked.push(`${method} ${new URL(request.url()).pathname}`);
      await route.abort('blockedbyclient');
    });
    await use(state);
    expect(state.blocked, '읽기 전용 live 시험이 운영 변경 요청을 보내려 했습니다').toEqual([]);
    expect(state.errors, '브라우저 런타임 오류').toEqual([]);
  }, { auto: true }],
});

export { expect, liveEnv };

/** UI로 로그인하고 서비스 원장이 보일 때까지 기다린다. */
export async function login(page: Page) {
  await page.goto('/');
  await page.getByLabel('아이디', { exact: true }).fill(liveEnv('KTDM_LIVE_USERNAME'));
  await page.getByLabel('비밀번호', { exact: true }).fill(liveEnv('KTDM_LIVE_PASSWORD'));
  const response = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/auth/login'
    && r.request().method() === 'POST');
  try {
    await page.getByRole('button', { name: '로그인', exact: true }).click();
    expect((await response).status(), '로그인 응답').toBe(200);
  } finally {
    await clearPasswordFields(page);
  }
  await expect(page.getByRole('table', { name: '서비스 원장' })).toBeVisible();
}

/** 좁은 폭에서는 주 메뉴가 drawer 안에 있다. 메뉴 영역(rail 또는 drawer)을 돌려준다. */
export async function openMenu(page: Page) {
  const menuButton = page.locator('[data-slot="admin-shell-menu-button"]');
  if (!(await menuButton.isVisible())) return page.locator('[data-slot="admin-shell-rail"]');
  await menuButton.click();
  const drawer = page.getByRole('dialog', { name: '메뉴' });
  await expect(drawer).toBeVisible();
  return drawer;
}

/** UI로 로그아웃해 세션을 서버에서 회수한다. 시험이 운영에 세션을 남기지 않게 끝마다 부른다. */
export async function logout(page: Page) {
  const response = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/auth/logout'
    && r.request().method() === 'POST');
  await (await openMenu(page)).getByRole('button', { name: '로그아웃', exact: true }).click();
  expect((await response).ok(), '로그아웃 응답').toBe(true);
  await expect(page.getByRole('button', { name: '로그인', exact: true })).toBeVisible();
}

export async function noHorizontalOverflow(page: Page) {
  await expect.poll(() => page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )).toBeLessThanOrEqual(1);
}
