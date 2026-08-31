'use client';

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
import { useEffect, useState, type ReactNode } from 'react';

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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeHref, setActiveHref] = useState('/');

  useEffect(() => {
    try {
      setSidebarCollapsed(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1');
    } catch {
      // 저장소 접근이 막힌 브라우저에서는 펼친 rail을 기본값으로 사용한다.
    }
  }, []);

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
          <a aria-label="Docker Manager UI 홈" className="brand-wordmark" href="/">
            <span className="brand-name">Docker Manager UI</span>
            <span className="brand-subtitle">admin</span>
            <span aria-hidden="true" className="brand-short">dmu</span>
          </a>
          <div className="brand-actions">
            <button
              aria-label={sidebarCollapsed ? '좌측 메뉴 펼치기' : '좌측 메뉴 접기'}
              className="sidebar-collapse-toggle"
              onClick={() => setSidebarCollapsed((current) => !current)}
              title={sidebarCollapsed ? '좌측 메뉴 펼치기' : '좌측 메뉴 접기'}
              type="button"
            >
              {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>
            <button
              aria-label="로그아웃"
              className="mobile-logout-button"
              disabled={isLoggingOut}
              onClick={onLogout}
              title="로그아웃"
              type="button"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>

        <nav aria-label="관리" className="rail-nav">
          {navGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              {group.label ? <div className="nav-title">{group.label}</div> : null}
              {group.items.map((item) => {
                const Icon = item.icon;
                const commonClassName = `${railRowClass}${item.disabled ? ' nav-link--disabled' : ''}`;

                if (item.onClick) {
                  return (
                    <button
                      aria-label={sidebarCollapsed ? item.label : undefined}
                      className={`${commonClassName} nav-button`}
                      disabled={item.disabled}
                      key={item.label}
                      onClick={item.onClick}
                      title={sidebarCollapsed ? item.label : undefined}
                      type="button"
                    >
                      <Icon aria-hidden="true" size={16} />
                      <span className="nav-label">{item.label}</span>
                      {item.hint ? <kbd className="nav-link__hint">{item.hint}</kbd> : null}
                    </button>
                  );
                }

                return (
                  <a
                    aria-current={item.href === activeHref ? 'page' : undefined}
                    aria-label={sidebarCollapsed ? item.label : undefined}
                    className={commonClassName}
                    href={item.href}
                    key={item.label}
                    title={sidebarCollapsed ? item.label : undefined}
                    onClick={() => setActiveHref(item.href ?? '/')}
                  >
                    <Icon aria-hidden="true" size={16} />
                    <span className="nav-label">{item.label}</span>
                  </a>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <button
            aria-label={sidebarCollapsed ? '로그아웃' : undefined}
            className={`${railRowClass} nav-button`}
            disabled={isLoggingOut}
            onClick={onLogout}
            title={sidebarCollapsed ? '로그아웃' : undefined}
            type="button"
          >
            <LogOut aria-hidden="true" size={16} />
            <span className="nav-label">로그아웃</span>
          </button>
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
