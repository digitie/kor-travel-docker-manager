import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';

import { renderWithQueryClient } from '@/test-utils';
import type { ContainerInspect } from '@/lib/api';

// 이 파일 안에서 `./ContainerDetailModal`을 정적으로 import하지 않는다 — `IS_DEV =
// process.env.NODE_ENV !== 'production'`은 모듈이 처음 평가될 때 한 번만 고정되는
// 상수라, 이미 로드된 인스턴스에 나중에 `vi.stubEnv`를 걸어도 값이 바뀌지 않는다.
// Vitest는 테스트 파일마다 격리된 모듈 레지스트리를 쓰므로(`test.isolate` 기본값),
// 이 파일에서 env를 스텁한 뒤 동적 `import()`로 **이 파일 안에서 처음** 로드하면
// 그 시점의 값으로 `IS_DEV`가 고정된다.
//
// 이 파일에는 테스트를 하나만 둔다 — 두 번째로 `vi.resetModules()`까지 곁들이면
// `./ContainerDetailModal`이 다시 로드하는 `@tanstack/react-query`가 `test-utils`의
// `QueryClientProvider`가 이미 들고 있는 인스턴스와 다른 모듈 인스턴스가 되어
// `useQuery`가 "No QueryClient set" 오류로 죽는다. NODE_ENV=test(기본값)에서 패널이
// 보이는 것은 `ContainerDetailModal.test.tsx`의 ensure 액션 테스트가 이미 그 버튼을
// 찾아 클릭하는 것으로 증명한다.
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    apiJson: vi.fn(),
    postJson: vi.fn(),
  };
});

import { apiJson } from '@/lib/api';

const mockedApiJson = vi.mocked(apiJson);

const INSPECT_OK: ContainerInspect = {
  id: 'c1',
  name: 'geo-api-1',
  status: 'running',
  restart_count: 0,
  state: { status: 'running', running: true },
};

describe('ContainerDetailModal — production 환경의 개발자 전용 패널', () => {
  afterEach(() => {
    cleanup();
    vi.resetAllMocks();
    vi.unstubAllEnvs();
  });

  it('NODE_ENV=production이면 ensure 실행 패널이 렌더되지 않는다', async () => {
    mockedApiJson.mockResolvedValue(INSPECT_OK);
    vi.stubEnv('NODE_ENV', 'production');

    const { default: ContainerDetailModal } = await import('./ContainerDetailModal');

    renderWithQueryClient(
      <ContainerDetailModal
        containerId="c1"
        containerLabel="geo-api-1"
        targetId="geo"
        targetServices={['geo-api', 'geo-db']}
        onClose={() => {}}
      />
    );

    // 데이터가 로드될 때까지 기다린 뒤에도 개발자 전용 패널이 없어야 한다 — 아직
    // 로딩 중이라 안 보이는 것과 혼동하지 않도록 overview 탭 렌더까지 기다린다.
    await screen.findByText('geo-api-1', { selector: 'dd' });
    expect(screen.queryByRole('button', { name: /ensure --build/ })).not.toBeInTheDocument();
    expect(screen.queryByText('개발 빌드 전용')).not.toBeInTheDocument();
  });
});
