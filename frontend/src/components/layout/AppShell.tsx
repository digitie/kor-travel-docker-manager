'use client';

import { AdminShell, formatAdminTitle } from '@kor-travel/ui/admin-shell';
import { AppMenu, type AppMenuGroup, type AppMenuItem } from '@kor-travel/ui/app-menu';
import { Button } from '@kor-travel/ui/button';
import {
  Activity,
  Command,
  Database,
  GitCompare,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Pin,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';

/** 공통 AdminShell의 rail/drawer 전환 기준(lg, 1024px)과 같은 값이다. 기존 export 호환용 이름이다. */
export const DRAWER_MEDIA_QUERY = '(max-width: 63.999rem)';

/** 기존 Manager rail이 "1"/"0"으로 저장해 온 키를 그대로 쓴다(공통 셸이 형식을 보존한다). */
export const SIDEBAR_COLLAPSED_KEY = 'kor-travel-docker-manager:sidebar-collapsed';
/** 상단 바와 문서 제목의 영역 이름. 페이지 이름은 활성 메뉴에서 파생한다. */
export const APP_AREA = 'Docker Manager';
const MAIN_CONTENT_ID = 'main-content';
const HOME_HREF = '/';

type AppShellProps = {
  /** 활성 메뉴가 페이지가 아닐 때(앵커 등) 쓸 페이지 이름. 기본은 홈 메뉴 라벨이다. */
  title?: string;
  /** 상단 바 제목 옆의 작은 설명. */
  description?: string;
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

function toMenuGroups(groups: NavGroup[]): AppMenuGroup[] {
  return groups.map((group, index) => ({
    id: String(index),
    label: group.label,
    items: group.items.map((item): AppMenuItem => {
      const base = {
        id: item.href ?? item.label,
        label: item.label,
        icon: <item.icon aria-hidden="true" size={16} />,
        hint: item.hint,
        disabled: item.disabled,
      };
      return item.onClick ? { ...base, onSelect: item.onClick } : { ...base, href: item.href ?? HOME_HREF };
    }),
  }));
}

function BrandMark({ collapsed }: { collapsed: boolean }) {
  return (
    <a aria-label={`${APP_AREA} 홈`} className="manager-brand" href={HOME_HREF} title={APP_AREA}>
      {collapsed ? <span aria-hidden="true">DM</span> : APP_AREA}
    </a>
  );
}

function LogoutButton({ iconOnly, disabled, onLogout }: { iconOnly: boolean; disabled: boolean; onLogout: () => void }) {
  return (
    <Button
      aria-label={iconOnly ? '로그아웃' : undefined}
      className={iconOnly ? undefined : 'w-full justify-start'}
      disabled={disabled}
      onClick={onLogout}
      size={iconOnly ? 'icon-lg' : 'lg'}
      title={iconOnly ? '로그아웃' : undefined}
      type="button"
      variant="ghost"
    >
      <LogOut aria-hidden="true" size={16} />
      {iconOnly ? null : <span>로그아웃</span>}
    </Button>
  );
}

export default function AppShell({
  title,
  description,
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
  const [activeHref, setActiveHref] = useState(HOME_HREF);

  useEffect(() => {
    const updateActiveHref = () => setActiveHref(window.location.hash || HOME_HREF);
    updateActiveHref();
    window.addEventListener('hashchange', updateActiveHref);
    return () => window.removeEventListener('hashchange', updateActiveHref);
  }, []);

  const navGroups: NavGroup[] = [
    { items: [{ label: '대시보드', icon: LayoutDashboard, href: HOME_HREF }] },
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
      items: [{ label: '인증 설정', icon: KeyRound, onClick: onOpenAdminSettings }],
    },
  ];

  // 페이지 이름은 nav 정본에서 파생한다. 같은 문서 안 앵커(#…)는 페이지가 아니므로 홈 라벨을 쓴다.
  const homeLabel = navGroups.flatMap((group) => group.items).find((item) => item.href === HOME_HREF)?.label;
  const pageTitle = title ?? homeLabel ?? '';

  useEffect(() => {
    const previous = document.title;
    document.title = formatAdminTitle(APP_AREA, pageTitle);
    return () => { document.title = previous; };
  }, [pageTitle]);

  return (
    <AdminShell
      actions={actions}
      area={APP_AREA}
      brand={({ collapsed }) => <BrandMark collapsed={collapsed} />}
      collapsedStorageKey={SIDEBAR_COLLAPSED_KEY}
      description={description}
      mainClassName="manager-main"
      mainId={MAIN_CONTENT_ID}
      menu={<AppMenu activeItemId={activeHref} groups={toMenuGroups(navGroups)} label="관리" />}
      navigationKey={activeHref}
      railFooter={({ collapsed }) => (
        <LogoutButton disabled={isLoggingOut} iconOnly={collapsed} onLogout={onLogout} />
      )}
      title={pageTitle}
    >
      {children}
    </AdminShell>
  );
}
