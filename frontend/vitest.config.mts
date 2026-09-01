import path from 'node:path';

import { defineConfig } from 'vitest/config';

// 순수 로직 단위 테스트 전용이다. 컴포넌트 렌더 테스트가 필요해지면 그때
// environment/jsdom을 도입한다 — 지금 넣으면 의존성만 늘어난다.
export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
});
