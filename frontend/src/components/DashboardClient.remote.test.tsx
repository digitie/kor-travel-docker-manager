import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';

const fixture = vi.hoisted(() => ({
  mutate: vi.fn(),
  client: { invalidateQueries: vi.fn() },
  containers: [{ id: 'remote-api', name: 'remote-api', display_name: '원격 API', status: 'up',
    state: 'Remote HTTP observation', ports: [], remote: true }],
}));
vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => fixture.client,
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === 'auth-me' ? { authenticated: true, username: 'tester' }
      : queryKey[0] === 'containers' ? fixture.containers
      : queryKey[0] === 'targets' ? [] : undefined,
    isLoading: false, refetch: vi.fn(),
  }),
  useMutation: () => ({ mutate: fixture.mutate, isPending: false }),
}));
vi.mock('next/dynamic', () => ({ default: () => () => null }));
vi.mock('./layout/AppShell', () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock('@/lib/api', async (original) => ({
  ...await original<typeof import('@/lib/api')>(), setUnauthorizedHandler: vi.fn(),
}));

import DashboardClient from './DashboardClient';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fixture.mutate.mockClear();
});

it('원격 HTTP 응답을 Docker 실행으로 표시하거나 로컬 제어·상세 도구를 열지 않는다', () => {
  class Socket {
    close() {}
  }
  vi.stubGlobal('WebSocket', Socket);
  render(<DashboardClient />);
  const row = screen.getByText('원격 API').closest('tr')!;
  expect(within(row).getByText('원격 응답 정상')).toBeInTheDocument();
  for (const button of within(row).getAllByRole('button')) {
    expect(button).toBeDisabled();
    fireEvent.click(button);
  }
  expect(fixture.mutate).not.toHaveBeenCalled();
});
