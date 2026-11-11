import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e', workers: 1, fullyParallel: false,
  outputDir: '../.git/ui-e2e', reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:12905', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile', use: { browserName: 'chromium', viewport: { width: 390, height: 844 } } },
  ],
  webServer: { command: 'npm run start -- --hostname 127.0.0.1', wait: { stdout: /Ready/ }, timeout: 30_000, gracefulShutdown: { signal: 'SIGTERM', timeout: 1000 } },
});
