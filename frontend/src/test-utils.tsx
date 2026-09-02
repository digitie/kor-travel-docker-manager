import { ReactElement } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, RenderOptions, RenderResult } from '@testing-library/react';

/** 매 렌더마다 새 `QueryClient`를 만든다.
 *
 * 테스트 간에 하나를 공유하면 앞 테스트가 채운 쿼리 캐시가 뒤 테스트에 새어 들어가
 * 서로 다른 mock 응답을 기대하는 테스트가 캐시된 이전 값을 보게 된다. `retry: false`는
 * 필수다 — 컴포넌트 쪽 `useQuery`가 `retry`를 지정하지 않으면 react-query 기본값(3회,
 * 지수 백오프)이 적용돼 오류 하나를 확인하는 테스트가 실제로 몇 초씩 걸리고, 그마저도
 * fake timer 없이는 대기해야 한다. */
function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
}

/** `@tanstack/react-query`의 `useQuery`를 쓰는 컴포넌트를 렌더한다.
 *
 * `QueryClientProvider` 조상이 없으면 `useQuery`가 즉시 던지므로, 그런 컴포넌트를
 * React Testing Library의 `render()`에 직접 넘기면 항상 실패한다. */
export function renderWithQueryClient(
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>
): RenderResult & { queryClient: QueryClient } {
  const queryClient = createTestQueryClient();
  const result = render(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
    ...options,
  });
  return { ...result, queryClient };
}
