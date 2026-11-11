import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import AppShell from './AppShell';

afterEach(() => { cleanup(); localStorage.clear(); });
it('한 화면의 메뉴 접힘이 다른 AppShell 인스턴스에 전파되지 않는다', async () => {
  const user = userEvent.setup();
  const props = { title: '운영', isLoggingOut: false, onLogout: vi.fn(), onOpenAdminSettings: vi.fn(),
    onOpenBackupHistory: vi.fn(), onOpenCommandPalette: vi.fn(), onOpenRuntimePins: vi.fn(), onOpenSourceStatus: vi.fn() };
  render(<><AppShell {...props}>첫 화면</AppShell><AppShell {...props}>둘째 화면</AppShell></>);
  const rails = screen.getAllByRole('complementary', { name: '주요 메뉴' });
  await user.click(within(rails[0]).getByRole('button', { name: '좌측 메뉴 접기' }));
  expect(rails[0].closest('.app-shell')).toHaveAttribute('data-sidebar-collapsed', 'true');
  expect(rails[1].closest('.app-shell')).toHaveAttribute('data-sidebar-collapsed', 'false');
  expect(rails[0].querySelector('[data-slot="app-menu"]')).toBeInTheDocument();
});
