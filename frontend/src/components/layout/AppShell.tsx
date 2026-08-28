'use client';

import {
  Command,
  Database,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  Pin,
  ServerCog,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

/** kor-travel-geo-ui의 AppShell과 동일한 breakpoint에서 사이드바가 drawer로 전환된다. */
export const DRAWER_MEDIA_QUERY = '(max-width: 61.25rem)';

function useIsDrawerLayout(): boolean {
  const [isDrawer, setIsDrawer] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(DRAWER_MEDIA_QUERY);
    const sync = () => setIsDrawer(query.matches);
    sync();
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);
  return isDrawer;
}

type AppShellProps = {
  children: React.ReactNode;
  isLoggingOut: boolean;
  onLogout: () => void;
  onOpenAdminSettings: () => void;
  onOpenBackupHistory: () => void;
  onOpenCommandPalette: () => void;
  onOpenRuntimePins: () => void;
};

export default function AppShell({
  children,
  isLoggingOut,
  onLogout,
  onOpenAdminSettings,
  onOpenBackupHistory,
  onOpenCommandPalette,
  onOpenRuntimePins,
}: AppShellProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const isDrawerLayout = useIsDrawerLayout();
  const sidebarRef = useRef<HTMLElement>(null);
  const menuToggleRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const closeMenu = useCallback(() => setMenuOpen(false), []);

  // 닫힌 drawer는 transform으로만 화면 밖에 있으므로 inert 없이는 탭 순서와 접근성 트리에
  // 그대로 남는다. 데스크톱 사이드바(같은 엘리먼트, menuOpen=false)에는 절대 적용하지 않는다.
  // drawer가 열려 있는 동안에는 반대로 <main>을 inert 처리한다 — 이 컴포넌트는 별도의
  // Tab 트랩을 구현하지 않으므로, inert가 없으면 aria-hidden만으로는 포커스가 뒤에 가려진
  // 콘텐츠로 계속 넘어간다(aria-hidden은 tab order를 바꾸지 않는다).
  useEffect(() => {
    sidebarRef.current?.toggleAttribute('inert', isDrawerLayout && !menuOpen);
    mainRef.current?.toggleAttribute('inert', isDrawerLayout && menuOpen);
  }, [isDrawerLayout, menuOpen]);

  // drawer breakpoint를 넘어 데스크톱 폭으로 커지면 열린 상태로 남은 drawer가 레이아웃을
  // 깨뜨리므로 강제로 닫는다. 이때 포커스가 사이드바 안에 있었다면(예: 닫기 버튼) 그 엘리먼트가
  // 데스크톱 규칙에서 display:none으로 사라져 포커스가 <body>로 유실되므로 <main>으로 되돌린다.
  useEffect(() => {
    if (isDrawerLayout) return;
    setMenuOpen((wasOpen) => {
      if (wasOpen && sidebarRef.current?.contains(document.activeElement)) {
        queueMicrotask(() => mainRef.current?.focus());
      }
      return false;
    });
  }, [isDrawerLayout]);

  useEffect(() => {
    if (!menuOpen) return;
    closeButtonRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        closeMenu();
        menuToggleRef.current?.focus();
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [menuOpen, closeMenu]);

  // drawer가 열려 있는 동안 배경 스크롤이 터치로 이어지지 않도록 잠근다.
  useEffect(() => {
    if (!menuOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [menuOpen]);

  function runAndCloseMenu(action: () => void) {
    action();
    closeMenu();
  }

  return (
    <div className="app-shell select-none" data-menu-open={menuOpen}>
      <header className="mobile-topbar">
        <button
          aria-controls="app-sidebar"
          aria-expanded={menuOpen}
          aria-label={menuOpen ? '메뉴 닫기' : '메뉴 열기'}
          className="mobile-menu-toggle"
          onClick={() => setMenuOpen((open) => !open)}
          ref={menuToggleRef}
          type="button"
        >
          {menuOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
        <strong className="mobile-topbar-title">Kor Travel Docker Manager</strong>
      </header>
      <button
        aria-label="메뉴 닫기"
        className="sidebar-backdrop"
        onClick={closeMenu}
        tabIndex={menuOpen ? 0 : -1}
        type="button"
      />
      <aside
        aria-label={menuOpen ? '내비게이션 메뉴' : undefined}
        aria-modal={menuOpen ? true : undefined}
        className="sidebar"
        id="app-sidebar"
        ref={sidebarRef}
        role={menuOpen ? 'dialog' : undefined}
      >
        <button
          aria-label="메뉴 닫기"
          className="sidebar-close"
          onClick={closeMenu}
          ref={closeButtonRef}
          type="button"
        >
          <X size={18} />
        </button>
        <div className="brand select-text">
          <span aria-hidden="true" className="brand-mark">
            <ServerCog size={18} />
          </span>
          <div className="brand-copy">
            <strong>Kor Travel</strong>
            <span>infrastructure control</span>
          </div>
        </div>
        <nav aria-label="관리" className="nav-group">
          <p className="nav-title">관리</p>
          <a aria-current="page" className="nav-link" href="/">
            <LayoutDashboard size={17} />
            대시보드
          </a>
        </nav>
        <div className="sidebar-footer">
          <button
            className="nav-link nav-button"
            onClick={() => runAndCloseMenu(onOpenCommandPalette)}
            type="button"
          >
            <Command size={17} />
            빠른 명령
            <kbd className="nav-link__hint">⌘K</kbd>
          </button>
          <button
            className="nav-link nav-button"
            onClick={() => runAndCloseMenu(onOpenAdminSettings)}
            type="button"
          >
            <KeyRound size={17} />
            인증 설정
          </button>
          <button
            className="nav-link nav-button"
            onClick={() => runAndCloseMenu(onOpenBackupHistory)}
            type="button"
          >
            <Database size={17} />
            백업 이력
          </button>
          <button
            className="nav-link nav-button"
            onClick={() => runAndCloseMenu(onOpenRuntimePins)}
            type="button"
          >
            <Pin size={17} />
            배포 버전 고정
          </button>
          <button
            className="nav-link nav-button"
            disabled={isLoggingOut}
            onClick={onLogout}
            type="button"
          >
            <LogOut size={17} />
            로그아웃
          </button>
        </div>
      </aside>
      <main aria-hidden={menuOpen || undefined} className="content select-text" ref={mainRef} tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
