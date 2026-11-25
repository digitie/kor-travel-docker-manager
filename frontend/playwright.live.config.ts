import { defineConfig } from '@playwright/test';

// 운영 Manager 대시보드 live 시험. 기본 주소·webServer·mock이 없고, 실행자가 명시한 공개 origin과
// 비공개 관리자 자격으로만 돈다(n150: scripts/n150-live-e2e.sh, docs/live-e2e.md). CI의 격리 회귀
// (`playwright.config.ts`, mock backend)와 섞지 않는다.
const baseURL = process.env.KTDM_LIVE_WEB_URL;
if (process.env.KTDM_LIVE_E2E !== '1' || !baseURL
  || !process.env.KTDM_LIVE_USERNAME || !process.env.KTDM_LIVE_PASSWORD) {
  throw new Error('live 실행에는 KTDM_LIVE_E2E=1, KTDM_LIVE_WEB_URL과 비공개 관리자 환경변수가 필요합니다.');
}
// 브라우저 프로세스에는 자격 값을 물려주지 않는다(시험 코드는 runner 프로세스의 env로 읽는다).
const browserEnv = Object.fromEntries(Object.entries(process.env)
  .filter((entry): entry is [string, string] => entry[1] !== undefined
    && entry[0] !== 'KTDM_LIVE_USERNAME' && entry[0] !== 'KTDM_LIVE_PASSWORD'));

export default defineConfig({
  testDir: './e2e-live',
  testMatch: '**/*.live.spec.ts',
  // 운영 상태를 바꾸는 시험은 `*.write.live.spec.ts`로 두고 KTDM_LIVE_E2E_WRITE=1일 때만 수집한다.
  testIgnore: process.env.KTDM_LIVE_E2E_WRITE === '1' ? [] : ['**/*.write.live.spec.ts'],
  // 자격이 틀리면 시험마다 로그인에 실패해 운영 로그인 잠금(10분 5회)을 건다. 먼저 한 번만 확인한다.
  globalSetup: './e2e-live/login-preflight.ts',
  workers: 1, fullyParallel: false, retries: 0,
  timeout: 90_000, expect: { timeout: 20_000 },
  outputDir: './test-results/live',
  reporter: [['list'], ['json', { outputFile: './test-results/live/report.json' }]],
  // trace는 입력값(비밀번호 포함)을 담을 수 있어 끈다. 실패 스크린샷만 남긴다.
  use: {
    baseURL, headless: true, trace: 'off', screenshot: 'only-on-failure', serviceWorkers: 'block',
    launchOptions: { env: browserEnv },
  },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile', use: { browserName: 'chromium', viewport: { width: 390, height: 844 } } },
  ],
});
