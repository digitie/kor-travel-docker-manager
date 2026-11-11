import { defineConfig } from '@playwright/test';

const port = Number(process.env.E2E_UI_PORT ?? '12905');
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('격리 E2E 포트가 올바르지 않습니다.');
const baseURL = 'http://127.0.0.1:' + port;

export default defineConfig({
  testDir: './e2e', workers: 1, fullyParallel: false,
  // n150의 첫 브라우저/번들 로딩을 포함하되 실제 UI 단언은 그대로 유지한다.
  timeout: 90_000, expect: { timeout: 15_000 },
  outputDir: '../.git/ui-e2e', reporter: 'list',
  use: { baseURL, trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile', use: { browserName: 'chromium', viewport: { width: 390, height: 844 } } },
  ],
  webServer: { command: 'node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port ' + port,
    url: baseURL, reuseExistingServer: false, timeout: 60_000, gracefulShutdown: { signal: 'SIGTERM', timeout: 1000 } },
});
