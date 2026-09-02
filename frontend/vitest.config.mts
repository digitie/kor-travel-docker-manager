import path from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// 순수 로직 단위 테스트(`*.test.ts`)와 컴포넌트 렌더 테스트(`*.test.tsx`)를 함께 다룬다.
// jsdom은 DOM이 없는 순수 로직 코드에도 상위 호환 환경이라 기존 `*.test.ts` 스위트는
// `environment: 'node'`였을 때와 동일하게 통과한다 — 컴포넌트 테스트를 위해 도입하면서
// 별도 environment 분기를 두지 않았다.
//
// `@vitejs/plugin-react`가 필요하다: 이 프로젝트의 Vite(8.x)는 SSR 모듈 변환에서 JSX
// 인식을 파일 확장자만으로 하지 않아서, 이 플러그인 없이는 `.tsx` 테스트 파일의 JSX가
// "Unexpected JSX expression" 파싱 에러로 죽는다 — Next.js 자체는 별도 컴파일러(SWC)를
// 쓰므로 이 프로젝트에는 원래 없던 의존성이다.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  test: {
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    environment: 'jsdom',
    setupFiles: ['./src/vitest-setup.ts'],
  },
});
