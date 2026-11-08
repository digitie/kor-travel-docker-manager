import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';

const fixture = vi.hoisted(() => ({
  mutate: vi.fn(),
  client: { invalidateQueries: vi.fn() },
  containers: [{ id: 'remote-api', name: 'remote-api', display_name: '원격 API', status: 'up',
    state: 'Remote HTTP observation', ports: [], remote: true }] as ContainerStatus[],
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
import type { ContainerStatus } from '@/lib/containerPresentation';

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
  expect(screen.queryByText('원격 API')).not.toBeInTheDocument();
  expect(fixture.mutate).not.toHaveBeenCalled();
});


it('중지·미생성은 숨기고 실제 health·관측 실패를 오류 집계에 남긴다', () => {
  fixture.containers = [
    { id: 'worker', name: 'worker', display_name: '수집기', status: 'running', health: 'unhealthy',
      state: 'running', ports: [], observed_only: true },
    { id: 'failed', name: 'failed', display_name: '조회 실패', status: 'error',
      state: 'error', ports: [], observed_only: true, inventory_complete: false },
    { id: 'old', name: 'old', display_name: '옛 릴리스', status: 'exited', state: 'exited', ports: [] },
    { id: 'unused', name: 'unused', display_name: '미사용 선언', status: 'not_created', state: 'not_created', ports: [] },
  ];
  vi.stubGlobal('WebSocket', class { close() {} });
  render(<DashboardClient />);
  expect(screen.queryByText('옛 릴리스')).not.toBeInTheDocument();
  expect(screen.queryByText('미사용 선언')).not.toBeInTheDocument();
  const errorStat = screen.getByText('상태·관측 오류').closest('.ops-stat')!;
  expect(within(errorStat as HTMLElement).getByText('2')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('일부 Docker 상태를 읽지 못했습니다');
  const failedRow = screen.getByText('조회 실패').closest('tr')!;
  expect(within(failedRow).getByRole('button', { name: '시작' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: '중지 항목 포함' }));
  expect(screen.getByText('옛 릴리스')).toBeInTheDocument();
  expect(screen.queryByText('미사용 선언')).not.toBeInTheDocument();
});
