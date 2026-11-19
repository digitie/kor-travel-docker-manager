import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import AppShell, { APP_AREA, SIDEBAR_COLLAPSED_KEY } from './AppShell';

const props = () => ({ isLoggingOut: false, onLogout: vi.fn(), onOpenAdminSettings: vi.fn(),
  onOpenBackupHistory: vi.fn(), onOpenCommandPalette: vi.fn(), onOpenRuntimePins: vi.fn(), onOpenSourceStatus: vi.fn() });

afterEach(() => { cleanup(); localStorage.clear(); });

it('상단 바 한 줄에 "영역 - 페이지" 제목과 작은 설명을 두고 문서 제목도 맞춘다', () => {
  render(<AppShell {...props()} description="페이지 설명">본문</AppShell>);
  const heading = screen.getByRole('heading', { level: 1 });
  expect(heading).toHaveTextContent(`${APP_AREA} - 대시보드`);
  expect(heading.closest('[data-slot="admin-shell-header"]')).toHaveTextContent('페이지 설명');
  expect(document.title).toBe(`${APP_AREA} - 대시보드`);
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
});

it('기존 "1"/"0" 접힘 저장 형식을 이어 쓰고 접힌 rail에도 로그아웃을 남긴다', async () => {
  localStorage.setItem(SIDEBAR_COLLAPSED_KEY, '0');
  const user = userEvent.setup();
  const handlers = props();
  render(<AppShell {...handlers}>본문</AppShell>);
  const rail = document.querySelector<HTMLElement>('[data-slot="admin-shell-rail"]')!;
  await user.click(within(rail).getByRole('button', { name: '메뉴 접기' }));
  expect(localStorage.getItem(SIDEBAR_COLLAPSED_KEY)).toBe('1');
  expect(rail).toHaveAttribute('data-collapsed', 'true');
  await user.click(within(rail).getByRole('button', { name: '로그아웃' }));
  expect(handlers.onLogout).toHaveBeenCalledTimes(1);
  await user.click(within(rail).getByRole('button', { name: '백업 이력' }));
  expect(handlers.onOpenBackupHistory).toHaveBeenCalledTimes(1);
});
