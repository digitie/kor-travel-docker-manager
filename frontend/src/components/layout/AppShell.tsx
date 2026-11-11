'use client';

import { Button } from '@kor-travel/ui/button';

import {
  Activity,
  Command,
  Database,
  GitCompare,
  KeyRound,
  LayoutDashboard,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Pin,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import Link from 'next/link';
import { AppMenu } from '@kor-travel/ui/app-menu';
import { createStore } from 'zustand/vanilla';
import { useStore } from 'zustand';
import { useCallback, useEffect, useState, type ReactNode } from 'react';

/** 최신 Map admin rail과 같은 1024px 기준을 유지한다. 기존 export 호환용 이름이다. */
export const DRAWER_MEDIA_QUERY = '(max-width: 63.999rem)';

const SIDEBAR_COLLAPSED_KEY = 'kor-travel-docker-manager:sidebar-collapsed';
const MAIN_CONTENT_ID = 'main-content';

type AppShellProps = {
  title: string;
  description?: string;
  section?: string;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  isLoggingOut: boolean;
  onLogout: () => void;
  onOpenAdminSettings: () => void;
  onOpenBackupHistory: () => void;
  onOpenCommandPalette: () => void;
  onOpenRuntimePins: () => void;
  onOpenSourceStatus: () => void;
};

type NavItem = {
  label: string;
  icon: LucideIcon;
  href?: string;
  onClick?: () => void;
  hint?: string;
  disabled?: boolean;
};

type NavGroup = {
  label?: string;
  items: NavItem[];
};

const railRowClass = 'nav-link';

export default function AppShell({
  title,
  description,
  section = '개요',
  meta,
  actions,
  children,
  isLoggingOut,
  onLogout,
  onOpenAdminSettings,
  onOpenBackupHistory,
  onOpenCommandPalette,
  onOpenRuntimePins,
  onOpenSourceStatus,
}: AppShellProps) {
  const [uiStore] = useState(() => createStore<{ collapsed: boolean }>(() => ({ collapsed: false })));
  const sidebarCollapsed = useStore(uiStore, (state) => state.collapsed);
  const setSidebarCollapsed = useCallback((value: boolean | ((current: boolean) => boolean)) =>
    uiStore.setState((state) => ({ collapsed: typeof value === 'function' ? value(state.collapsed) : value })), [uiStore]);
  const [activeHref, setActiveHref] = useState('/');

  useEffect(() => {
    try {
      setSidebarCollapsed(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1');
    } catch {
      // 저장소 접근이 막힌 브라우저에서는 펼친 rail을 기본값으로 사용한다.
    }
  }, [setSidebarCollapsed]);

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, sidebarCollapsed ? '1' : '0');
    } catch {
      // 저장소가 없어도 메뉴 사용 자체는 계속 가능하다.
    }
  }, [sidebarCollapsed]);

  useEffect(() => {
    const updateActiveHref = () => {
      setActiveHref(window.location.hash || '/');
    };
    updateActiveHref();
    window.addEventListener('hashchange', updateActiveHref);
    return () => window.removeEventListener('hashchange', updateActiveHref);
  }, []);

  const navGroups: NavGroup[] = [
    {
      label: undefined,
      items: [{ label: '대시보드', icon: LayoutDashboard, href: '/' }],
    },
    {
      label: '서비스 관리',
      items: [
        { label: '서비스 원장', icon: Database, href: '#service-ledger' },
        { label: '앱별 상태', icon: Activity, href: '#service-groups' },
      ],
    },
    {
      label: '운영 도구',
      items: [
        { label: '빠른 명령', icon: Command, onClick: onOpenCommandPalette, hint: '⌘K' },
        { label: '백업 이력', icon: Database, onClick: onOpenBackupHistory },
        { label: '배포 버전 고정', icon: Pin, onClick: onOpenRuntimePins },
        { label: '배포 상태 확인', icon: GitCompare, onClick: onOpenSourceStatus },
      ],
    },
    {
      label: '시스템',
      items: [
        { label: '인증 설정', icon: KeyRound, onClick: onOpenAdminSettings },
        { label: '로그아웃', icon: LogOut, onClick: onLogout, disabled: isLoggingOut },
      ],
    },
  ];

  return (
    <div
      className="app-shell"
      data-sidebar-collapsed={sidebarCollapsed ? 'true' : 'false'}
    >
      <a className="skip-link" href={`#${MAIN_CONTENT_ID}`}>
        본문으로 건너뛰기
      </a>

      <aside
        aria-label="주요 메뉴"
        className="sidebar"
        data-slot="admin-shell-rail"
        id="app-sidebar"
      >
        <div className="brand">
          <Link aria-label="Docker Manager UI 홈" className="brand-wordmark" href="/">
            <span className="brand-name">Docker Manager UI</span>
            <span className="brand-subtitle">admin</span>
            <span aria-hidden="true" className="brand-short">dmu</span>
          </Link>
          <div className="brand-actions">
            <Button variant="ghost"
              aria-label={sidebarCollapsed ? '좌측 메뉴 펼치기' : '좌측 메뉴 접기'}
              className="sidebar-collapse-toggle"
              onClick={() => setSidebarCollapsed((current) => !current)}
              title={sidebarCollapsed ? '좌측 메뉴 펼치기' : '좌측 메뉴 접기'}
              type="button"
            >
              {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </Button>
            <Button variant="ghost"
              aria-label="로그아웃"
              className="mobile-logout-button"
              disabled={isLoggingOut}
              onClick={onLogout}
              title="로그아웃"
              type="button"
            >
              <LogOut size={16} />
            </Button>
          </div>
        </div>

        <AppMenu label="관리" activeItemId={activeHref} groups={navGroups.map((group, index) => ({
          id: String(index), label: group.label,
          items: group.items.map((item) => ({
            id: item.href ?? item.label, label: item.label, icon: <item.icon size={16} />,
            hint: item.hint, disabled: item.disabled,
            ...(item.onClick ? { onSelect: item.onClick } : { href: item.href ?? '/' }),
          })),
        }))} />

        <div className="sidebar-footer">
          <Button variant="ghost"
            aria-label={sidebarCollapsed ? '로그아웃' : undefined}
            className={`${railRowClass} nav-button`}
            disabled={isLoggingOut}
            onClick={onLogout}
            title={sidebarCollapsed ? '로그아웃' : undefined}
            type="button"
          >
            <LogOut aria-hidden="true" size={16} />
            <span className="nav-label">로그아웃</span>
          </Button>
        </div>
      </aside>

      <div className="app-shell__workspace">
        <header className="page-head" data-slot="admin-shell-header">
          <div className="page-head__inner">
            {section ? <p className="ops-eyebrow">{section}</p> : null}
            <div className="page-head__row">
              <div className="page-title">
                <h1 className="ops-title">{title}</h1>
              </div>
              {actions ? <div className="page-head__actions">{actions}</div> : null}
            </div>
            {meta ? <div className="page-head__meta">{meta}</div> : null}
            {description ? <p className="page-head__description">{description}</p> : null}
          </div>
        </header>
        <main
          aria-label="Docker Manager UI 대시보드 본문"
          className="content focus-visible:outline-0"
          data-slot="admin-shell-main"
          id={MAIN_CONTENT_ID}
          tabIndex={-1}
        >
          {children}
        </main>
      </div>
    </div>
  );
}
