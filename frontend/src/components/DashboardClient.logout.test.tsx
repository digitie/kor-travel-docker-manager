import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

vi.mock('next/dynamic', () => ({ default: () => () => null }));
vi.mock('./layout/AppShell', () => ({
  default: ({ children, onLogout, isLoggingOut }: { children: ReactNode; onLogout: () => void; isLoggingOut: boolean }) =>
    <div><button disabled={isLoggingOut} onClick={onLogout}>로그아웃</button>{children}</div>,
}));

import DashboardClient from './DashboardClient';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json' },
});
class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() { Socket.instances.push(this); }
  open() { this.readyState = 1; this.onopen?.(); }
  close() { this.readyState = 3; this.onclose?.({ code: 1000 }); }
}
let client: QueryClient;
let logout: ReturnType<typeof deferred<Response>>;
let logoutQueue: ReturnType<typeof deferred<Response>>[];
let authNext: ReturnType<typeof deferred<Response>> | null;
let pendingContainers: boolean;
let authenticated: boolean;
let aborted: boolean;
let calls: string[];
let steps: string[];
beforeEach(() => {
  Socket.instances = [];
  logout = deferred<Response>();
  logoutQueue = [];
  authNext = null;
  pendingContainers = false;
  authenticated = true;
  aborted = false;
  calls = [];
  steps = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
  vi.stubGlobal('WebSocket', Socket);
  vi.stubGlobal('fetch', vi.fn((address: string, init?: RequestInit) => {
    const path = new URL(address).pathname;
    calls.push(path);
    if (path === '/api/v1/auth/me') return authNext?.promise ?? Promise.resolve(json({
      authenticated, username: 'test-user', expires_at: '2099-01-01T00:00:00Z',
    }));
    if (path === '/api/v1/auth/login') { authenticated = true; return Promise.resolve(json({ ok: true })); }
    if (path === '/api/v1/auth/logout') { steps.push('logout_post'); return (logoutQueue.shift() ?? logout).promise; }
    if (path === '/api/v1/containers') {
      if (!pendingContainers) return Promise.resolve(json([]));
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          aborted = true; steps.push('containers_abort');
          reject(new DOMException('취소', 'AbortError'));
        }, { once: true });
      });
    }
    if (path === '/api/v1/targets') return Promise.resolve(json([]));
    if (path === '/api/v1/system/disk-usage') return Promise.resolve(json({ state: 'unknown', summary: { text: '확인 불가', detail: '합성 관측' } }));
    if (path === '/health') return Promise.resolve(json({ status: 'healthy' }));
    throw new Error('예상하지 않은 합성 요청');
  }));
});
afterEach(() => { cleanup(); client.clear(); vi.unstubAllGlobals(); });

async function mount(openSocket = true) {
  render(<QueryClientProvider client={client}><DashboardClient /></QueryClientProvider>);
  await screen.findByRole('button', { name: '로그아웃' });
  await waitFor(() => expect(Socket.instances).toHaveLength(1));
  if (openSocket) await act(async () => { Socket.instances[0].open(); });
}
const containerCalls = () => calls.filter(path => path === '/api/v1/containers').length;

it('로그아웃 요청과 인증 재조회 중 폴링·WS를 멈추고 성공 뒤 로그인 화면에 남는다', async () => {
  await mount();
  authNext = deferred<Response>();
  fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => expect(steps).toContain('logout_post'));
  const before = containerCalls();
  await act(async () => { await client.invalidateQueries({ queryKey: ['containers'] }); });
  expect(containerCalls()).toBe(before);
  expect(Socket.instances[0].readyState).toBe(3);
  await act(async () => { logout.resolve(json({ ok: true })); });
  await act(async () => { await client.invalidateQueries({ queryKey: ['containers'] }); });
  expect(containerCalls()).toBe(before);
  expect(Socket.instances).toHaveLength(1);
  await act(async () => { authNext!.resolve(json({ detail: '미인증' }, 401)); });
  await screen.findByLabelText('비밀번호');
  expect(screen.queryByRole('button', { name: '로그아웃' })).not.toBeInTheDocument();
  expect(Socket.instances).toHaveLength(1);
});

it('이미 진행 중인 containers fetch의 AbortSignal을 POST보다 먼저 취소한다', async () => {
  pendingContainers = true;
  await mount(false);
  await waitFor(() => expect(containerCalls()).toBe(1));
  fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => expect(steps).toContain('logout_post'));
  expect(aborted).toBe(true);
  expect(steps.indexOf('containers_abort')).toBeLessThan(steps.indexOf('logout_post'));
  authNext = deferred<Response>();
  await act(async () => { logout.resolve(json({ ok: true })); });
  await act(async () => { authNext!.resolve(json({ detail: '미인증' }, 401)); });
});

it('로그아웃 실패 뒤 실제 세션이 살아 있으면 읽기·WS와 재시도 버튼을 복구한다', async () => {
  await mount();
  fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => expect(steps).toContain('logout_post'));
  await act(async () => { logout.resolve(json({ detail: '합성 실패' }, 503)); });
  await waitFor(() => expect(Socket.instances).toHaveLength(2));
  expect(screen.getByRole('button', { name: '로그아웃' })).toBeEnabled();
  const recovered = containerCalls();
  await act(async () => { await client.invalidateQueries({ queryKey: ['containers'] }); });
  expect(containerCalls()).toBeGreaterThan(recovered);
  await act(async () => { Socket.instances[1].open(); });
  expect(calls.filter(path => path === '/api/v1/auth/logout')).toHaveLength(1);
});

it('인증 재확인도 실패하면 이전 세션 캐시로 읽기를 재개하지 않고 로그아웃 재시도는 허용한다', async () => {
  await mount();
  authNext = deferred<Response>();
  fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => expect(steps).toContain('logout_post'));
  await act(async () => { logout.resolve(json({ ok: true })); });
  await act(async () => { authNext!.resolve(json({ detail: '합성 재확인 실패' }, 503)); });
  await waitFor(() => expect(screen.getByRole('button', { name: '로그아웃' })).toBeEnabled());
  const before = containerCalls();
  await act(async () => { await client.invalidateQueries({ queryKey: ['containers'] }); });
  expect(containerCalls()).toBe(before);
  expect(Socket.instances).toHaveLength(1);
  expect(Socket.instances[0].readyState).toBe(3);
  expect(await screen.findByText(/^인증 상태 확인 실패/)).toBeInTheDocument();
  const retry = deferred<Response>();
  logoutQueue = [retry];
  authNext = deferred<Response>();
  fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => expect(steps).toHaveLength(2));
  await act(async () => { await client.invalidateQueries({ queryKey: ['containers'] }); });
  expect(containerCalls()).toBe(before);
  await act(async () => { retry.resolve(json({ ok: true })); });
  await act(async () => { authNext!.resolve(json({ detail: '미인증' }, 401)); });
  await screen.findByLabelText('비밀번호');
  expect(Socket.instances).toHaveLength(1);
});

it('로그아웃 재확인 장애 뒤 로그인 화면으로 전환됐다가 새 로그인하면 관측 차단을 해제한다', async () => {
  await mount();
  authNext = deferred<Response>();
  fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => expect(steps).toContain('logout_post'));
  await act(async () => { logout.resolve(json({ ok: true })); });
  await act(async () => { authNext!.resolve(json({ detail: '합성 재확인 실패' }, 503)); });
  await screen.findByText(/^인증 상태 확인 실패/);
  authNext = null;
  authenticated = false;
  await act(async () => { await client.invalidateQueries({ queryKey: ['auth-me'] }); });
  await screen.findByLabelText('비밀번호');
  const before = containerCalls();
  fireEvent.change(screen.getByLabelText('아이디'), { target: { value: 'test-user' } });
  fireEvent.change(screen.getByLabelText('비밀번호'), { target: { value: 'fixture-only' } });
  fireEvent.click(screen.getByRole('button', { name: '로그인' }));
  await screen.findByRole('button', { name: '로그아웃' });
  await waitFor(() => expect(Socket.instances).toHaveLength(2));
  await waitFor(() => expect(containerCalls()).toBeGreaterThan(before));
});

it.each([503, 200])('새 로그인 뒤 인증 재확인이 %s이면 읽기 차단을 성공으로 해제하지 않는다', async (status) => {
  await mount();
  authNext = deferred<Response>();
  fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => expect(steps).toContain('logout_post'));
  await act(async () => { logout.resolve(json({ ok: true })); });
  await act(async () => { authNext!.resolve(json({ detail: '합성 재확인 실패' }, 503)); });
  await screen.findByText(/^인증 상태 확인 실패/);
  authNext = null;
  authenticated = false;
  await act(async () => { await client.invalidateQueries({ queryKey: ['auth-me'] }); });
  await screen.findByLabelText('비밀번호');
  const before = containerCalls();
  authNext = deferred<Response>();
  fireEvent.change(screen.getByLabelText('아이디'), { target: { value: 'test-user' } });
  fireEvent.change(screen.getByLabelText('비밀번호'), { target: { value: 'fixture-only' } });
  fireEvent.click(screen.getByRole('button', { name: '로그인' }));
  await waitFor(() => expect(calls.filter(path => path === '/api/v1/auth/me')).toHaveLength(4));
  await act(async () => { authNext!.resolve(json({ authenticated: false, detail: '합성 미확인' }, status)); });
  await waitFor(() => expect(screen.getByRole('button', { name: '로그인' })).toBeEnabled());
  // 이후 별도 인증 캐시 갱신이 오더라도 실패한 onLogin이 latch를 미리 풀면 안 된다.
  authNext = null;
  await act(async () => { await client.invalidateQueries({ queryKey: ['auth-me'] }); });
  await screen.findByRole('button', { name: '로그아웃' });
  await act(async () => { await client.invalidateQueries({ queryKey: ['containers'] }); });
  expect(Socket.instances).toHaveLength(1);
  expect(containerCalls()).toBe(before);
});


it('버튼 뒤 빠른 명령 재진입을 막고 인증 재확인이 끝난 뒤 의도한 재시도만 허용한다', async () => {
  const retry = deferred<Response>();
  logoutQueue = [logout, retry];
  await mount();
  authNext = deferred<Response>();
  fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => expect(steps).toHaveLength(1));
  const before = containerCalls();
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  const palette = await screen.findByRole('dialog', { name: '빠른 명령' });
  fireEvent.click(within(palette).getByText('로그아웃'));
  await act(async () => {});
  expect(steps).toHaveLength(1);
  await act(async () => { logout.resolve(json({ detail: '합성 첫 실패' }, 503)); });
  await waitFor(() => expect(calls.filter(path => path === '/api/v1/auth/me')).toHaveLength(2));
  // POST가 끝나도 인증 재확인 중에는 다른 진입점이 guard를 풀지 않는다.
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  fireEvent.click(within(await screen.findByRole('dialog', { name: '빠른 명령' })).getByText('로그아웃'));
  await act(async () => { await client.invalidateQueries({ queryKey: ['containers'] }); });
  expect(steps).toHaveLength(1);
  expect(containerCalls()).toBe(before);
  expect(Socket.instances).toHaveLength(1);
  await act(async () => { authNext!.resolve(json({ authenticated: true, username: 'test-user', expires_at: '2099-01-01T00:00:00Z' })); });
  await waitFor(() => expect(screen.getByRole('button', { name: '로그아웃' })).toBeEnabled());
  await waitFor(() => expect(Socket.instances).toHaveLength(2));
  authNext = deferred<Response>();
  fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => expect(steps).toHaveLength(2));
  const retryReads = containerCalls();
  await act(async () => { retry.resolve(json({ ok: true })); });
  await act(async () => { await client.invalidateQueries({ queryKey: ['containers'] }); });
  expect(containerCalls()).toBe(retryReads);
  expect(Socket.instances).toHaveLength(2);
  await act(async () => { authNext!.resolve(json({ detail: '미인증' }, 401)); });
  await screen.findByLabelText('비밀번호');
});

it('같은 렌더 프레임에서 빠른 명령을 두 번 실행해도 로그아웃은 한 번만 보낸다', async () => {
  await mount();
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  const command = within(await screen.findByRole('dialog', { name: '빠른 명령' })).getByText('로그아웃');
  act(() => {
    fireEvent.click(command);
    fireEvent.click(command);
  });
  await waitFor(() => expect(steps).toContain('logout_post'));
  expect(steps).toHaveLength(1);
  authenticated = false;
  await act(async () => { logout.resolve(json({ ok: true })); });
  await screen.findByLabelText('비밀번호');
  expect(Socket.instances).toHaveLength(1);
});
