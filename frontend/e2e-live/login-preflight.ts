import { chromium, type FullConfig } from '@playwright/test';

import { clearPasswordFields, liveEnv } from './live-env';

// live 전체 실행 전에 자격을 브라우저로 한 번만 확인한다(backend 주소는 운영 번들이 정한다). 틀린 자격으로
// 시험을 다 돌리면 로그인 실패가 쌓여 운영 관리자 로그인이 10분씩 잠긴다(5회 한도). 여기서 실패하면 실행
// 전체를 멈춘다(실패 기록 1회). 성공하면 만든 세션을 바로 로그아웃으로 회수한다. 자격 값은 메시지에 싣지 않는다.
export default async function livePreflight(config: FullConfig) {
  const baseURL = config.projects[0]?.use.baseURL;
  if (!baseURL) throw new Error('KTDM_LIVE_WEB_URL이 없습니다');
  const browser = await chromium.launch({ env: config.projects[0]?.use.launchOptions?.env });
  try {
    const page = await browser.newPage({ baseURL, serviceWorkers: 'block' });
    await page.goto('/');
    await page.getByLabel('아이디', { exact: true }).fill(liveEnv('KTDM_LIVE_USERNAME'));
    await page.getByLabel('비밀번호', { exact: true }).fill(liveEnv('KTDM_LIVE_PASSWORD'));
    const responsePromise = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/auth/login'
      && r.request().method() === 'POST');
    await page.getByRole('button', { name: '로그인', exact: true }).click();
    const response = await responsePromise;
    await clearPasswordFields(page);
    const status = response.status();
    if (status !== 200) {
      const reason = status === 401 ? '자격 파일의 아이디·비밀번호가 운영과 다릅니다'
        : status === 429 ? '운영 로그인이 잠겨 있습니다(10분 뒤 다시)'
          : status === 403 ? 'KTDM_LIVE_WEB_URL이 backend의 허용 origin이 아닙니다'
            : '운영 로그인 응답이 예상과 다릅니다';
      throw new Error(`live 사전 로그인 실패(HTTP ${status}): ${reason} — 시험을 시작하지 않습니다`);
    }
    const logoutUrl = new URL('/api/v1/auth/logout', response.url()).toString();
    const logout = await page.evaluate(async (url) => (await fetch(url, { method: 'POST', credentials: 'include' })).status,
      logoutUrl);
    if (logout < 200 || logout >= 300) throw new Error(`live 사전 로그인 세션을 회수하지 못했습니다(HTTP ${logout})`);
  } finally {
    await browser.close();
  }
}
