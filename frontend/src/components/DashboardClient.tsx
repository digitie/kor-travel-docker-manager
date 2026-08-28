'use client';

import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Database,
  FolderGit2,
  Play,
  Square,
  RotateCw,
  Terminal,
  Activity,
  RefreshCw,
  ShieldAlert,
  Settings,
  X,
  Radio,
  Cpu,
  HardDrive,
  BarChart3,
  Gauge,
  ServerCog,
  Boxes
} from 'lucide-react';
import dynamic from 'next/dynamic';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import AdminSettingsPanel from './AdminSettingsPanel';
import BackupHistoryPanel from './BackupHistoryPanel';
import RuntimePinPanel from './RuntimePinPanel';
import SourceStatusPanel from './SourceStatusPanel';
import ContainerDetailModal from './ContainerDetailModal';
import LoginScreen from './LoginScreen';
import ToastStack, { ToastItem, errorToast, successToast } from './Toast';
import AppShell from './layout/AppShell';
import { humanizeError } from '@/lib/errors';
import {
  ApiError,
  DiskUsageResponse,
  AuthMe,
  BACKEND_URL,
  WS_CLOSE_AUTH_REQUIRED,
  WS_CLOSE_INVALID_CONTAINER,
  WS_CLOSE_TRY_AGAIN_LATER,
  apiJson,
  apiWsUrl,
  notifyUnauthorized,
  setUnauthorizedHandler,
} from '@/lib/api';
import { diffEnv, diffList } from '@/lib/configDiff';
import {
  validateEnvEntry,
  validateNetworkName,
  validatePortMapping,
} from '@/lib/configValidation';

// 향후 스키마 정의 및 폼 검증 확장을 위해 사전 import
const _unusedForm = typeof useForm !== 'undefined';
const _unusedZod = typeof z !== 'undefined';

// Dynamic Import for Recharts to resolve 'Heavy library loaded eagerly' warning
const ResponsiveContainer = dynamic(() => import('recharts').then(mod => mod.ResponsiveContainer), { ssr: false });
const LineChart = dynamic(() => import('recharts').then(mod => mod.LineChart), { ssr: false });
const Line = dynamic(() => import('recharts').then(mod => mod.Line), { ssr: false });
const XAxis = dynamic(() => import('recharts').then(mod => mod.XAxis), { ssr: false });
const YAxis = dynamic(() => import('recharts').then(mod => mod.YAxis), { ssr: false });
const CartesianGrid = dynamic(() => import('recharts').then(mod => mod.CartesianGrid), { ssr: false });
const RechartsTooltip = dynamic(() => import('recharts').then(mod => mod.Tooltip), { ssr: false });
const Legend = dynamic(() => import('recharts').then(mod => mod.Legend), { ssr: false });

interface ContainerStatus {
  id: string;
  name: string;
  display_name?: string;
  role?: string;
  connection?: string;
  public_url?: string;
  expected_ports?: string[];
  image?: string;
  status: string;
  state: string;
  ports: string[];
  metrics?: {
    timestamp: string;
    cpu_pct: number;
    mem_pct: number;
    mem_usage: number;
    mem_limit: number;
    io_read: number;
    io_write: number;
  };
  config?: {
    ports: string[];
    env: Record<string, string>;
    volumes: string[];
    networks: string[];
    /** 배포 계약이 값을 고정한 환경변수 이름. 서버가 저장 시점에 변경을 거부하므로
     * 편집기도 처음부터 잠근다 — 눌러 본 뒤에야 거부를 알게 하지 않는다. */
    locked_env?: string[];
  };
}

interface MetricHistoryPoint {
  timestamp: string;
  cpu_pct: number;
  mem_pct: number;
  io_read: number;
  io_write: number;
}

// Byte Formatting Helper
function formatBytes(bytes: number | undefined, decimals = 1) {
  if (bytes === undefined || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

// Time Formatting Helper for Chart X-Axis
function formatTimestamp(timestampStr: string) {
  if (!timestampStr) return '';
  try {
    const parts = timestampStr.split(' ');
    if (parts.length === 2) {
      return parts[1];
    }
    const d = new Date(timestampStr.replace(' ', 'T') + 'Z');
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  } catch (e) {
    return timestampStr;
  }
}

// 상태 칸은 Docker의 원문(running/not_created…)을 그대로 보여 줬는데 같은 화면의 KPI는
// 한국어라 어휘가 이중이었다. 표시는 한국어로 통일하고 원문은 title 속성으로 남긴다.
const STATUS_LABELS: Record<string, string> = {
  running: '실행 중',
  exited: '중지됨',
  offline: '연결 끊김',
  paused: '일시정지',
  restarting: '재시작 중',
  starting: '시작 중',
  created: '생성됨',
  not_created: '미생성',
  'not created': '미생성',
  dead: '비정상 종료',
  error: '오류',
};

const statusLabel = (status: string): string =>
  STATUS_LABELS[(status || '').toLowerCase()] ?? status;

// role은 내부 식별자(map-api, metrics-exporter…)라 처음 보는 사람에게 의미가 없다.
const ROLE_LABELS: Record<string, string> = {
  postgresql: '데이터베이스',
  rustfs: '오브젝트 저장소',
  prometheus: '메트릭 수집',
  grafana: '메트릭 시각화',
  cadvisor: '컨테이너 메트릭',
  'metrics-exporter': '메트릭 노출',
};

// 분기 순서가 곧 우선순위다. 넓은 패턴(`api`, `ui`)을 먼저 두면 좁은 패턴이 도달하지
// 못한다 — 예: `geocoder-api`는 `-api`에, `geocoder-ui`는 `ui`에 먼저 걸려 "지오코더"가
// 영원히 나오지 않는다. 좁은 것부터 검사한다.
const roleLabel = (role: string | null | undefined): string => {
  const value = (role || '').toLowerCase();
  if (!value) return '-';
  if (ROLE_LABELS[value]) return ROLE_LABELS[value];
  // `map-postgresql`·`concierge-postgresql`처럼 접두사가 붙은 실제 role 값은 exact key와
  // 맞지 않는다(config/docker-targets.yml). 접미사로 판정한다.
  if (value.endsWith('postgresql') || value.endsWith('postgres')) return '데이터베이스';
  if (value.includes('geocoder')) return '지오코더';
  if (value.includes('dagster') || value.includes('scheduler')) return '워크플로';
  if (value.includes('mcp')) return 'MCP 서버';
  if (value.endsWith('-api') || value.includes('api')) return 'API 서버';
  if (value.includes('ui') || value.includes('web')) return '웹 UI';
  return role || '-';
};

const getStatusConfig = (status: string) => {
  const s = status.toLowerCase();
  if (s === 'running') {
    return {
      dotClass: 'bg-ok animate-pulse',
      textClass: 'text-ok font-semibold',
      rowClass: 'bg-card hover:bg-subtle'
    };
  } else if (s === 'exited' || s === 'offline') {
    return {
      dotClass: 'bg-danger',
      textClass: 'text-danger font-semibold',
      rowClass: 'bg-danger/5 hover:bg-subtle'
    };
  } else if (s.includes('starting') || s.includes('restarting') || s.includes('paused')) {
    return {
      dotClass: 'bg-warn animate-ping',
      textClass: 'text-warn font-semibold',
      rowClass: 'bg-card hover:bg-subtle'
    };
  } else {
    return {
      dotClass: 'bg-disabled',
      textClass: 'text-secondary font-semibold',
      rowClass: 'bg-card hover:bg-subtle'
    };
  }
};

const getContainerPresentation = (container: ContainerStatus) => {
  const role = container.role || '';
  const id = container.id || '';

  if (role === 'postgresql' || id.includes('postgresql')) {
    return { Icon: Database, displayName: container.display_name || 'PostgreSQL (PostGIS)' };
  }
  if (role === 'rustfs') {
    return { Icon: FolderGit2, displayName: container.display_name || 'RustFS Store' };
  }
  if (role.includes('geocoder')) {
    return { Icon: ServerCog, displayName: container.display_name || 'Kor Travel Geo' };
  }
  if (role.includes('mcp')) {
    return { Icon: Radio, displayName: container.display_name || 'MCP HTTP' };
  }
  if (role.includes('scheduler') || role.includes('dagster')) {
    return { Icon: Activity, displayName: container.display_name || 'Workflow' };
  }
  if (role.includes('concierge') || role.includes('map-api') || role.includes('pinvi-api')) {
    return { Icon: ServerCog, displayName: container.display_name || 'App API' };
  }
  if (role.includes('ui') || role.includes('web')) {
    return { Icon: Boxes, displayName: container.display_name || 'Web UI' };
  }
  if (role === 'prometheus') {
    return { Icon: Activity, displayName: container.display_name || 'Prometheus 메트릭 저장소' };
  }
  if (role === 'grafana') {
    return { Icon: BarChart3, displayName: container.display_name || 'Grafana 시각화 도구' };
  }
  if (role === 'metrics-exporter') {
    return { Icon: Gauge, displayName: container.display_name || 'cAdvisor Exporter' };
  }

  return { Icon: Boxes, displayName: container.display_name || container.name };
};

export default function DashboardClient() {
  const queryClient = useQueryClient();
  const [isAdminSettingsOpen, setIsAdminSettingsOpen] = useState<boolean>(false);
  const [isBackupHistoryOpen, setIsBackupHistoryOpen] = useState<boolean>(false);
  const [isRuntimePinsOpen, setIsRuntimePinsOpen] = useState<boolean>(false);
  const [isSourceStatusOpen, setIsSourceStatusOpen] = useState<boolean>(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  // 실패는 사람이 읽고 닫아야 하므로 쌓이되, 화면을 덮지 않게 최근 것만 남긴다.
  const pushToast = useCallback((item: ToastItem) => {
    setToasts((current) => [...current, item].slice(-3));
  }, []);
  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState<boolean>(false);
  const [commandQuery, setCommandQuery] = useState('');
  const commandInputRef = useRef<HTMLInputElement>(null);

  const {
    data: auth,
    isLoading: isAuthLoading,
    refetch: refetchAuth,
  } = useQuery<AuthMe>({
    queryKey: ['auth-me'],
    queryFn: async (): Promise<AuthMe> => {
      try {
        return await apiJson<AuthMe>('/api/v1/auth/me', { redirectOnUnauthorized: false });
      } catch (error) {
        // 미인증(401)은 오류가 아니라 유효한 상태로 취급한다. throw하면 react-query가 직전
        // 성공 데이터(authenticated:true)를 유지해 로그아웃/세션만료 후에도 대시보드가 남는다.
        if (error instanceof ApiError && error.status === 401) {
          return { authenticated: false, username: '', expires_at: '' };
        }
        throw error;
      }
    },
    retry: false,
    refetchInterval: 5 * 60_000, // 살아 있는 소켓만으로 세션이 무한 연장되지 않게 주기 재확인
    refetchOnWindowFocus: true,
    staleTime: 60_000,
  });
  const isAuthenticated = auth?.authenticated === true;

  // 백그라운드 요청이 401을 받으면 하드 리로드 대신 auth-me 쿼리를 무효화해 SPA 내에서
  // LoginScreen 으로 전환한다(logout 경로와 동작 일치, in-flight UI 상태 보존).
  useEffect(() => {
    setUnauthorizedHandler(() => {
      void queryClient.invalidateQueries({ queryKey: ['auth-me'] });
    });
    return () => setUnauthorizedHandler(null);
  }, [queryClient]);

  // WebSocket State - Default initialized directly to resolve 'State initialized from a mount effect'
  const [wsContainers, setWsContainers] = useState<ContainerStatus[] | null>(null);
  const [isWsConnected, setIsWsConnected] = useState<boolean>(false);
  // 서버가 1013(혼잡)으로 흘려보낸 상태. 폴백 폴링 주기를 늦추는 데만 쓴다.
  const [isWsShedding, setIsWsShedding] = useState<boolean>(false);
  // inspect 상세 패널 대상 컨테이너(null이면 닫힘).
  const [detailContainer, setDetailContainer] = useState<ContainerStatus | null>(null);
  // 안정된 identity로 넘긴다. 인라인 화살표를 주면 WS broadcast(2초)마다 prop이 바뀐다.
  const closeDetailModal = useCallback(() => setDetailContainer(null), []);

  // Modal States
  const [isConfigModalOpen, setIsConfigModalOpen] = useState<boolean>(false);
  const [configTargetContainer, setConfigTargetContainer] = useState<ContainerStatus | null>(null);
  const [inputPortsList, setInputPortsList] = useState<string[]>([]);
  const [inputEnvDict, setInputEnvDict] = useState<Record<string, string>>({});
  const [inputVolumesList, setInputVolumesList] = useState<string[]>([]);
  const [inputNetworksList, setInputNetworksList] = useState<string[]>([]);

  // Real-time Log Modal States
  const [isLogModalOpen, setIsLogModalOpen] = useState<boolean>(false);
  const [logContainerId, setLogContainerId] = useState<string | null>(null);
  const [liveLogs, setLiveLogs] = useState<string>('');
  const [isLogWsOpen, setIsLogWsOpen] = useState<boolean>(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Performance Chart Modal States
  const [isChartModalOpen, setIsChartModalOpen] = useState<boolean>(false);
  const [chartContainerId, setChartContainerId] = useState<string | null>(null);
  const [chartMetricType, setChartMetricType] = useState<'cpu' | 'memory' | 'io'>('cpu');
  const [chartHours, setChartHours] = useState<number>(1);

  // Real-time rolling metrics points from WebSocket (replaces state copy from queryChartData)
  const [wsMetricsPoints, setWsMetricsPoints] = useState<MetricHistoryPoint[]>([]);

  // Reset rolling metrics handled via event handlers direct triggers to solve react-doctor warnings

  // Refs to avoid stale closures in WebSocket handler
  const isChartModalOpenRef = useRef(isChartModalOpen);
  const chartContainerIdRef = useRef(chartContainerId);

  useEffect(() => {
    isChartModalOpenRef.current = isChartModalOpen;
  }, [isChartModalOpen]);

  useEffect(() => {
    chartContainerIdRef.current = chartContainerId;
  }, [chartContainerId]);

  // 모달 접근성: 열린 모달을 Escape 키로 닫는다(AdminSettings 모달은 자체 처리).
  useEffect(() => {
    if (!isLogModalOpen && !isChartModalOpen && !isConfigModalOpen && !isCommandPaletteOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      if (isCommandPaletteOpen) {
        setIsCommandPaletteOpen(false);
      } else if (isConfigModalOpen) {
        setIsConfigModalOpen(false);
      } else if (isChartModalOpen) {
        setIsChartModalOpen(false);
        setWsMetricsPoints([]);
      } else if (isLogModalOpen) {
        setIsLogModalOpen(false);
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [isLogModalOpen, isChartModalOpen, isConfigModalOpen, isCommandPaletteOpen]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setIsCommandPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    if (!isCommandPaletteOpen) {
      setCommandQuery('');
      return;
    }
    commandInputRef.current?.focus();
  }, [isCommandPaletteOpen]);

  // Fallback Polling (Query) - Versioned v1
  const { data: fallbackContainers = [], isLoading, error } = useQuery<ContainerStatus[]>({
    queryKey: ['containers'],
    queryFn: () => apiJson<ContainerStatus[]>('/api/v1/containers'),
    // 서버가 혼잡(1013)해서 WS를 흘려보낸 경우에는 폴백 폴링을 늦춘다. 5초 폴링은
    // 요청마다 전체 docker sweep을 돌아 WS 한 건보다 비싸므로, 그대로 두면 부하를
    // 덜어내려던 shed가 오히려 서버 부하를 올린다.
    refetchInterval: isWsShedding ? 30000 : 5000,
    enabled: isAuthenticated && !isWsConnected,
  });

  // target registry — 상세 패널의 `ensure --build`가 어떤 target에 속하고 실제로 몇 개
  // 서비스를 재생성하는지 알기 위해 쓴다. 정적 설정이라 한 번만 받는다. 모달이 열리기 전에
  // 미리 캐시해 두어야 패널이 열린 뒤 footer가 뒤늦게 튀어나오지 않는다.
  type TargetSummary = {
    id: string;
    display_name?: string;
    containers?: string[];
    resolved_services?: string[];
  };
  const { data: targets = [] } = useQuery<TargetSummary[]>({
    queryKey: ['targets'],
    queryFn: () => apiJson<TargetSummary[]>('/api/v1/targets'),
    enabled: isAuthenticated,
    staleTime: Infinity,
    refetchInterval: false,
  });

  // `containers`는 target이 "직접 소유한" 목록이 아니다 — depends_on까지 펼쳐진
  // 전이 폐포이므로 여러 target에서 공유 인프라가 반복된다. 따라서 첫 매치를 쓰면
  // `dependency_order`가 좁은 것부터 나열된다는 우연에 기대게 된다.
  // (`all` target은 현재 21개 서비스를 담는다 — 순서가 바뀌면 전체 스택 범위가 달라진다.)
  // 순서와 무관하게 **가장 좁은** target을 고른다.
  const detailTarget = useMemo(() => {
    if (!detailContainer) return null;
    const matches = targets.filter((t) => (t.containers ?? []).includes(detailContainer.id));
    if (matches.length === 0) return null;
    return matches.reduce((narrowest, t) =>
      (t.containers?.length ?? Infinity) < (narrowest.containers?.length ?? Infinity)
        ? t
        : narrowest
    );
  }, [targets, detailContainer]);

  // Active containers dataset (WS if available, fallback query otherwise)
  const displayContainers = wsContainers || fallbackContainers;

  // "디스크 참"은 비전문 관리자가 이 시스템을 죽이는 가장 그럴듯한 경로인데 어느
  // 화면에도 없었다. 원시 수치가 아니라 "정리 시 약 N GB 확보 가능"으로 보여 준다.
  const { data: diskUsage } = useQuery<DiskUsageResponse>({
    queryKey: ['disk-usage'],
    queryFn: () => apiJson<DiskUsageResponse>('/api/v1/system/disk-usage'),
    enabled: isAuthenticated,
    refetchInterval: 60_000,
    retry: false,
  });

  // 관리도구 자신의 상태. `/health`는 인증이 필요 없고 부작용도 없으므로 가볍게 폴링한다.
  const { data: healthData, isError: healthErrored } = useQuery<{ status?: string }>({
    queryKey: ['manager-health'],
    queryFn: () => apiJson<{ status?: string }>('/health', { redirectOnUnauthorized: false }),
    enabled: isAuthenticated,
    refetchInterval: 30_000,
    retry: false,
  });
  const managerHealth: 'healthy' | 'checking' | 'down' = healthErrored
    ? 'down'
    : healthData?.status === 'healthy'
      ? 'healthy'
      : 'checking';

  // 21개 컨테이너가 평면 테이블 하나라 "PinVi 쪽이 지금 정상인가"에 답하려면 행을 눈으로
  // 골라 세야 했다. 앱 단위로 묶어 한 줄 요약을 준다.
  //
  // `targets.containers`는 target이 직접 소유한 목록이 아니라 depends_on 전이 폐포라
  // 공용 인프라가 여러 target에 중복 등장한다. 그래서 컨테이너마다 **가장 좁은** target에
  // 한 번만 배정한다(`detailTarget`이 쓰는 것과 같은 규칙).
  const containerGroups = useMemo(() => {
    const groups = new Map<string, { label: string; containers: ContainerStatus[] }>();
    for (const container of displayContainers) {
      const matches = targets.filter((target) => (target.containers ?? []).includes(container.id));
      const narrowest = matches.length
        ? matches.reduce((current, candidate) =>
            (candidate.containers?.length ?? Infinity) < (current.containers?.length ?? Infinity)
              ? candidate
              : current
          )
        : null;
      const key = narrowest?.id ?? '__other__';
      const label = narrowest ? narrowest.display_name || narrowest.id : '기타';
      const bucket = groups.get(key) ?? { label, containers: [] };
      bucket.containers.push(container);
      groups.set(key, bucket);
    }
    return Array.from(groups.entries()).map(([id, group]) => {
      const running = group.containers.filter(
        (container: ContainerStatus) => (container.status || '').toLowerCase() === 'running'
      );
      return {
        id,
        label: group.label,
        containers: group.containers,
        runningCount: running.length,
        healthy: running.length === group.containers.length,
      };
    });
  }, [displayContainers, targets]);

  // 컨테이너 단위 제어만 있어서 "geo 전체 재시작"이 수동 N회 클릭이었다. 신규
  // 엔드포인트 없이 순차 호출로 처리한다 — 각 호출이 기존 C6c 락을 그대로 통과한다.
  const [bulkRestartingGroup, setBulkRestartingGroup] = useState<string | null>(null);
  const restartGroup = useCallback(
    async (group: { id: string; label: string; containers: ContainerStatus[] }) => {
      const running = group.containers.filter((c) => (c.status || '').toLowerCase() === 'running');
      if (running.length === 0) {
        pushToast(successToast(`${group.label}에 실행 중인 컨테이너가 없습니다.`));
        return;
      }
      const names = running.map((c) => c.display_name || c.name).join(', ');
      if (
        !window.confirm(
          `${group.label}의 실행 중인 컨테이너 ${running.length}개를 순서대로 재시작합니다.\n\n` +
            `대상: ${names}\n\n계속할까요?`
        )
      ) {
        return;
      }
      setBulkRestartingGroup(group.id);
      let failed = 0;
      for (const container of running) {
        try {
          await apiJson(`/api/v1/containers/${container.id}/action`, {
            method: 'POST',
            body: JSON.stringify({ action: 'restart' }),
          });
        } catch (error) {
          failed += 1;
          pushToast(
            errorToast(humanizeError(error, `${container.display_name || container.name} 재시작`))
          );
        }
      }
      setBulkRestartingGroup(null);
      queryClient.invalidateQueries({ queryKey: ['containers'] });
      if (failed === 0) {
        pushToast(
          successToast(`${group.label} 재시작 완료`, `${running.length}개 컨테이너를 재시작했습니다.`)
        );
      }
    },
    [pushToast, queryClient]
  );

  // KPI summary counts derived from the active container list
  const kpiCounts = useMemo(() => {
    const stoppedStatuses = new Set([
      'exited',
      'paused',
      'created',
      'dead',
      'not_created',
      'not created',
      'offline',
    ]);
    let running = 0;
    let stopped = 0;
    let errored = 0;
    for (const c of displayContainers) {
      const s = (c.status || '').toLowerCase();
      if (s === 'running') running += 1;
      else if (s === 'error') errored += 1;
      else if (stoppedStatuses.has(s)) stopped += 1;
    }
    return {
      total: displayContainers.length,
      running,
      stopped,
      error: errored,
    };
  }, [displayContainers]);

  // Status WebSockets connection setup - Versioned v1
  useEffect(() => {
    if (!isAuthenticated) return;

    let ws: WebSocket | undefined;
    let reconnectTimeout: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    let attempt = 0;
    let framesThisSocket = 0;
    let lastMessageAt = Date.now();
    // 최초 프레임은 서버가 docker sweep을 끝낸 뒤에야 온다. 그 지연을 watchdog으로 재면
    // 느리지만 정상인 서버에서 소켓을 영영 유지하지 못한다. 최초 프레임 전에는 넉넉한
    // grace를 주고, 그 뒤부터 broadcast 간격(2초) 기준으로 반개방을 감시한다.
    const FIRST_FRAME_GRACE_MS = 30000;
    const IDLE_LIMIT_MS = 8000;

    const detach = (socket: WebSocket) => {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
    };

    const connectWS = () => {
      if (cancelled) return;
      const wsUrl = apiWsUrl('/api/v1/ws/status');
      let socket: WebSocket;
      try {
        socket = new WebSocket(wsUrl);
      } catch (err) {
        // SecurityError/SyntaxError는 설정 오류이지 일시적 장애가 아니다. 재시도하지 않고
        // HTTP 폴백 폴링(isWsConnected=false)에 맡긴다.
        console.error('WebSocket construction failed for', wsUrl, err);
        return;
      }
      ws = socket;

      socket.onopen = () => {
        framesThisSocket = 0;
        lastMessageAt = Date.now();
        setIsWsShedding(false);
        setIsWsConnected(true);
      };

      socket.onmessage = (event) => {
        // backoff는 handshake 성립이 아니라 "실제로 동작하는 소켓"에서만 리셋한다.
        // onopen에서 리셋하면 서버가 항상 accept하는 계약 탓에 backoff가 영원히
        // attempt=0에 머문다. 첫 프레임만으로 리셋해도 부족하다 — ws_status는 accept
        // 직후 초기 상태를 한 번 보내므로, 크래시 루프 중인 서버도 매 연결마다 1프레임을
        // 흘려 초당 1회 재연결이 된다. 2프레임(초기 + broadcast 1회)을 받아야 리셋한다.
        framesThisSocket += 1;
        if (framesThisSocket >= 2) attempt = 0;
        lastMessageAt = Date.now();
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'status' && message.containers) {
            setWsContainers(message.containers);

            // Real-time sliding/rolling metrics chart update
            if (isChartModalOpenRef.current && chartContainerIdRef.current) {
              const target = message.containers.find((c: any) => c.id === chartContainerIdRef.current);
              if (target && target.metrics && target.metrics.timestamp) {
                const newMetric = target.metrics;
                setWsMetricsPoints((prev) => {
                  // Skip duplicates
                  if (prev.length > 0 && prev[prev.length - 1].timestamp === newMetric.timestamp) {
                    return prev;
                  }
                  const newPoint: MetricHistoryPoint = {
                    timestamp: newMetric.timestamp,
                    cpu_pct: newMetric.cpu_pct,
                    mem_pct: newMetric.mem_pct,
                    io_read: newMetric.io_read,
                    io_write: newMetric.io_write
                  };
                  const nextData = [...prev, newPoint];
                  // Keep up to 360 points (1 hour of 10s intervals)
                  if (nextData.length > 360) {
                    return nextData.slice(nextData.length - 360);
                  }
                  return nextData;
                });
              }
            }
          }
        } catch (err) {
          console.error('Error parsing status WS message:', err);
        }
      };

      socket.onclose = (event) => {
        setIsWsConnected(false);
        setWsContainers(null);
        // 미인증 전환/언마운트(cancelled) 후에는 재연결하지 않는다.
        if (cancelled) return;
        // 4401은 재시도로 회복되지 않는 종단 상태다. REST 401과 같은 경로로 인증 상태를
        // 갱신해 LoginScreen으로 전환하고 재연결을 멈춘다. 이 분기가 없으면 서버의
        // accept-then-close 수정이 관측되지 않는다.
        if (event.code === WS_CLOSE_AUTH_REQUIRED) {
          notifyUnauthorized();
          return;
        }
        // 1013은 서버 혼잡이다. 폴백 폴링을 늦춰 shed가 부하를 되레 키우지 않게 한다.
        setIsWsShedding(event.code === WS_CLOSE_TRY_AGAIN_LATER);
        const delay = Math.min(30000, 1000 * 2 ** attempt) * (0.5 + Math.random() * 0.5);
        attempt += 1;
        reconnectTimeout = setTimeout(connectWS, delay);
      };

      socket.onerror = (err) => {
        console.error('WebSocket error:', err);
        socket.close();
      };
    };

    connectWS();

    // 반개방 소켓 감시: 서버는 2초마다 상태를 보낸다. 프레임이 한 건도 오지 않은
    // 동안에는 초기 docker sweep 지연을 감안해 grace를 주고, 그 뒤로는 무프레임
    // 8초에 소켓을 닫아 재연결 경로와 폴백 폴링을 다시 켠다.
    const watchdog = setInterval(() => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const limit = framesThisSocket === 0 ? FIRST_FRAME_GRACE_MS : IDLE_LIMIT_MS;
      if (Date.now() - lastMessageAt > limit) ws.close();
    }, 3000);

    return () => {
      cancelled = true;
      clearInterval(watchdog);
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (ws) {
        // 정리 후 stale onclose가 4401 종단 분기를 오발동하지 않게 핸들러를 먼저 뗀다.
        detach(ws);
        ws.close();
      }
    };
  }, [isAuthenticated]);

  // Container Action Mutation - Versioned v1
  const actionMutation = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: string }) => {
      return apiJson(`/api/v1/containers/${id}/action`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['containers'] });
    },
    // onError가 없어서 start/stop/restart 실패가 완전히 무음이었다 — 버튼을 눌러도
    // 아무 일도 일어나지 않는 것처럼 보였고, production 하드스톱처럼 서버가 의도적으로
    // 거부하는 경우조차 이유를 알 수 없었다.
    onError: (err: unknown, variables) => {
      const container = displayContainers.find((item) => item.id === variables.id);
      const label = container?.display_name || container?.name || variables.id;
      const verb =
        variables.action === 'start' ? '시작' : variables.action === 'stop' ? '중지' : '재시작';
      pushToast(errorToast(humanizeError(err, `${label} ${verb}`)));
    },
  });

  // 컨테이너 하나를 멈추면 그것에 의존하는 서비스도 함께 영향을 받는다. 그 범위는
  // 이미 받아 둔 target 데이터로 계산할 수 있는데, 지금까지는 확인 없이 즉시 실행돼
  // 누르기 전에는 알 수 없었다. `ContainerDetailModal.runEnsure`가 쓰는 것과 같은
  // "영향 범위를 세어 보여 주고 확인받는" 패턴을 stop/restart에도 적용한다.
  const impactedTargetsFor = useCallback(
    (containerId: string) =>
      targets.filter((target) => (target.containers ?? []).includes(containerId)),
    [targets]
  );

  const handleAction = (id: string, action: string) => {
    if (action === 'stop' || action === 'restart') {
      const container = displayContainers.find((item) => item.id === id);
      const label = container?.display_name || container?.name || id;
      const impacted = impactedTargetsFor(id);
      const verb = action === 'stop' ? '중지' : '재시작';
      const scope = impacted.length
        ? `\n\n이 컨테이너는 다음 ${impacted.length}개 target에 포함됩니다: ` +
          `${impacted.map((target) => target.id).join(', ')}\n` +
          '해당 target을 쓰는 앱이 함께 영향을 받습니다.'
        : '';
      if (!window.confirm(`${label}을(를) ${verb}합니다.${scope}\n\n계속할까요?`)) {
        return;
      }
    }
    actionMutation.mutate({ id, action });
  };

  // Config Update mutation - Versioned v1
  const configMutation = useMutation({
    mutationFn: async ({ id, ports, env, volumes, networks }: { id: string; ports: string[]; env: Record<string, string>; volumes: string[]; networks: string[] }) => {
      return apiJson(`/api/v1/containers/${id}/config`, {
        method: 'POST',
        body: JSON.stringify({ ports, env, volumes, networks }),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['containers'] });
      setIsConfigModalOpen(false);
      pushToast(
        successToast(
          '설정을 반영했습니다.',
          '컨테이너가 새 설정으로 재생성되었습니다.'
        )
      );
    },
    onError: (err: unknown) => {
      pushToast(errorToast(humanizeError(err, '설정 변경')));
    },
  });

  // Config Reset mutation - Versioned v1
  const resetMutation = useMutation({
    mutationFn: async (id: string) => {
      return apiJson(`/api/v1/containers/${id}/reset`, {
        method: 'POST',
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['containers'] });
      setIsConfigModalOpen(false);
      pushToast(
        successToast(
          '설정을 기본값으로 되돌렸습니다.',
          '컨테이너가 기본 설정으로 재생성되었습니다.'
        )
      );
    },
    onError: (err: unknown) => {
      pushToast(errorToast(humanizeError(err, '설정 원복')));
    },
  });

  // Performance Metrics History Query - Versioned v1
  // 백엔드는 `hours`를 지원하는데 UI가 1로 고정하고 있어 "어제 밤에 뭐가 있었나"를
  // 볼 방법이 없었다. 이미 있는 파라미터를 노출만 한다.
  const { data: queryChartData = [], isLoading: isLoadingChart } = useQuery<MetricHistoryPoint[]>({
    queryKey: ['metrics-history', chartContainerId, chartMetricType, chartHours],
    queryFn: async () => {
      if (!chartContainerId) return [];
      return apiJson<MetricHistoryPoint[]>(
        `/api/v1/containers/${chartContainerId}/metrics?hours=${chartHours}`
      );
    },
    enabled: isAuthenticated && !!chartContainerId && isChartModalOpen,
  });

  // Derived combined chart data using useMemo (resolves react-doctor's 'no-derived-state')
  const combinedChartData = useMemo(() => {
    if (wsMetricsPoints.length === 0) return queryChartData;
    
    const merged = [...queryChartData];
    const existingTimestamps = new Set(queryChartData.map(d => d.timestamp));
    
    for (const pt of wsMetricsPoints) {
      if (!existingTimestamps.has(pt.timestamp)) {
        merged.push(pt);
        existingTimestamps.add(pt.timestamp);
      }
    }
    
    // Max 1 hour (360 points at 10s intervals)
    if (merged.length > 360) {
      return merged.slice(merged.length - 360);
    }
    return merged;
  }, [queryChartData, wsMetricsPoints]);

  // WebSocket live logs stream hook - Versioned v1
  useEffect(() => {
    if (!logContainerId || !isLogModalOpen) return;

    const wsUrl = apiWsUrl(`/api/v1/ws/logs/${logContainerId}`);
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch (err) {
      console.error('Log WebSocket construction failed for', wsUrl, err);
      setLiveLogs('\n[Error] 로그 스트림 주소가 올바르지 않습니다.\n');
      return;
    }

    setLiveLogs('--- 실시간 로그 스트리밍을 시작합니다 ---\n');
    setIsLogWsOpen(false);

    ws.onopen = () => setIsLogWsOpen(true);

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.log) {
          setLiveLogs((prev) => {
            const next = prev + message.log;
            const lines = next.split('\n');
            if (lines.length > 3000) {
              return lines.slice(lines.length - 3000).join('\n');
            }
            return next;
          });
        } else if (message.error) {
          setLiveLogs((prev) => prev + `\n[Error] ${message.error}\n`);
        }
      } catch (err) {
        console.error('Error parsing log WS payload:', err);
      }
    };

    ws.onerror = (err) => {
      console.error('Log WS error:', err);
      ws.close();
    };

    ws.onclose = (event) => {
      setIsLogWsOpen(false);
      // 로그 소켓도 종료 코드를 소비해 원인을 구분한다.
      if (event.code === WS_CLOSE_AUTH_REQUIRED) {
        setLiveLogs((prev) => prev + '\n--- 인증이 만료되어 로그 스트리밍이 종료되었습니다 ---\n');
        notifyUnauthorized();
        return;
      }
      if (event.code === WS_CLOSE_INVALID_CONTAINER) {
        setLiveLogs((prev) => prev + '\n--- 알 수 없는 컨테이너 ID입니다 ---\n');
        return;
      }
      if (event.code === WS_CLOSE_TRY_AGAIN_LATER) {
        setLiveLogs(
          (prev) => prev + '\n--- 서버가 혼잡합니다. 잠시 후 다시 열어 주세요 ---\n'
        );
        return;
      }
      setLiveLogs((prev) => prev + '\n--- 스트리밍 연결이 닫혔습니다 ---\n');
    };

    return () => {
      // 이전 컨테이너의 종료 배너가 새 컨테이너 버퍼에 섞이지 않게 핸들러를 먼저 뗀다.
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      ws.close();
    };
  }, [logContainerId, isLogModalOpen]);

  // Log Modal Auto-Scroll
  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [liveLogs]);

  const openConfigModal = (container: ContainerStatus) => {
    setConfigTargetContainer(container);
    setInputPortsList(container.config?.ports || []);
    setInputEnvDict(container.config?.env || {});
    setInputVolumesList(container.config?.volumes || []);
    setInputNetworksList(container.config?.networks || []);
    setIsConfigModalOpen(true);
  };

  const handleConfigSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!configTargetContainer || configValidation.hasBlockingIssue) return;
    configMutation.mutate({
      id: configTargetContainer.id,
      ports: inputPortsList,
      env: inputEnvDict,
      volumes: inputVolumesList,
      networks: inputNetworksList,
    });
  };

  // 저장 전 실시간 검증 + diff. 서버(docker_service.validate_container_config_update)와
  // 같은 규칙을 프론트에도 반영해 왕복 없이 즉시 피드백한다 — 서버가 최종 게이트이므로
  // 여기서 놓쳐도 보안 문제는 아니고 UX 품질만 낮아진다.
  const configValidation = useMemo(() => {
    const baseline = configTargetContainer?.config ?? {
      ports: [],
      env: {},
      volumes: [],
      networks: [],
    };
    const portErrors = inputPortsList.map(validatePortMapping);
    const networkErrors = inputNetworksList.map(validateNetworkName);
    const envErrors = Object.fromEntries(
      Object.entries(inputEnvDict).map(([key, value]) => [
        key,
        validateEnvEntry(key, value, baseline.env?.[key]),
      ])
    );
    const portsDiff = diffList(baseline.ports ?? [], inputPortsList);
    const networksDiff = diffList(baseline.networks ?? [], inputNetworksList);
    const envDiff = diffEnv(baseline.env ?? {}, inputEnvDict).filter(
      (row) => row.kind !== 'same'
    );
    // 계약이 값을 고정한 키. 입력칸을 잠그므로 여기서 바뀔 일이 없지만, 서버가
    // 거부하는 조건과 같은 판정을 화면에도 둬서 잠금이 뚫린 경우를 제출 전에 잡는다.
    const lockedEnv = new Set(baseline.locked_env ?? []);
    const lockedEnvChanged = envDiff
      .filter((row) => lockedEnv.has(row.key))
      .map((row) => row.key);
    // volumes는 서버에서 불변이다(compose_volume_graph_hash 비교로 첫 mutation 전에
    // 거부). 여기서 미리 감지해 제출 왕복 없이 안내한다.
    const volumesChanged = diffList(baseline.volumes ?? [], inputVolumesList).changed;

    const hasFieldError =
      portErrors.some(Boolean) ||
      networkErrors.some(Boolean) ||
      Object.values(envErrors).some(Boolean);

    return {
      portErrors,
      networkErrors,
      envErrors,
      portsDiff,
      networksDiff,
      envDiff,
      volumesChanged,
      lockedEnv,
      lockedEnvChanged,
      hasBlockingIssue: hasFieldError || volumesChanged || lockedEnvChanged.length > 0,
    };
  }, [configTargetContainer, inputPortsList, inputNetworksList, inputEnvDict, inputVolumesList]);

  const openLogModal = (id: string) => {
    setLiveLogs(''); // 이전 컨테이너 로그가 한 프레임 비치지 않게 먼저 비운다
    setLogContainerId(id);
    setIsLogModalOpen(true);
  };

  const openChartModal = (id: string, type: 'cpu' | 'memory' | 'io') => {
    setWsMetricsPoints([]); // 이벤트 핸들러에서 직접 초기화하여 derived-state 경고 방지
    setChartContainerId(id);
    setChartMetricType(type);
    setIsChartModalOpen(true);
  };

  const logoutMutation = useMutation({
    mutationFn: () => apiJson<{ ok: boolean }>('/api/v1/auth/logout', { method: 'POST' }),
    onSettled: async () => {
      setWsContainers(null);
      setIsWsConnected(false);
      setIsAdminSettingsOpen(false);
      queryClient.removeQueries({ queryKey: ['containers'] });
      await refetchAuth();
    },
  });

  const commandItems = [
    {
      id: 'settings',
      label: '인증 및 공개 API 키',
      hint: '설정',
      run: () => setIsAdminSettingsOpen(true),
    },
    {
      id: 'backups',
      label: 'DB 백업 이력 열기',
      hint: '조회',
      run: () => setIsBackupHistoryOpen(true),
    },
    {
      id: 'runtime-pins',
      label: '배포 버전 고정 상태 열기',
      hint: '조회',
      run: () => setIsRuntimePinsOpen(true),
    },
    {
      id: 'source-status',
      label: '배포 상태 확인 열기',
      hint: '조회',
      run: () => setIsSourceStatusOpen(true),
    },
    {
      id: 'refresh',
      label: '컨테이너 상태 새로고침',
      hint: '동기화',
      run: () => void queryClient.invalidateQueries({ queryKey: ['containers'] }),
    },
    {
      id: 'logout',
      label: '로그아웃',
      hint: '세션',
      run: () => logoutMutation.mutate(),
    },
  ].filter((item) => item.label.includes(commandQuery.trim()) || item.hint.includes(commandQuery.trim()));

  const runCommand = (run: () => void) => {
    setIsCommandPaletteOpen(false);
    run();
  };

  if (isAuthLoading) {
    return (
      <div className="min-h-screen bg-page text-ink flex items-center justify-center">
        <div className="flex items-center gap-2 text-sm text-secondary">
          <RefreshCw className="w-4 h-4 text-brand animate-spin" />
          <span>인증 상태를 확인하는 중입니다...</span>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <LoginScreen
        onLogin={async () => {
          await refetchAuth();
          queryClient.invalidateQueries({ queryKey: ['containers'] });
        }}
      />
    );
  }

  return (
    <AppShell
      isLoggingOut={logoutMutation.isPending}
      onLogout={() => logoutMutation.mutate()}
      onOpenAdminSettings={() => setIsAdminSettingsOpen(true)}
      onOpenBackupHistory={() => setIsBackupHistoryOpen(true)}
      onOpenCommandPalette={() => setIsCommandPaletteOpen(true)}
      onOpenRuntimePins={() => setIsRuntimePinsOpen(true)}
      onOpenSourceStatus={() => setIsSourceStatusOpen(true)}
    >
      <div className="page-head">
        <div className="page-title">
          <p className="ops-eyebrow">Kor Travel / infrastructure control</p>
          <h1 className="ops-title">인프라 서비스 컨트롤</h1>
        </div>
      </div>

      <section className="ops-overview" aria-labelledby="service-summary-title">
        <div className="ops-summary">
          <div className="ops-summary__header">
            <div>
              <h2 className="ops-section-title" id="service-summary-title">서비스 상태 요약</h2>
              <p className="ops-section-copy">현재 수집된 컨테이너 원장을 기준으로 집계합니다.</p>
            </div>
            <span className="ops-status-badge">
              <span className={`w-1.5 h-1.5 rounded-full ${isWsConnected ? 'bg-ok animate-pulse' : 'bg-warn'}`} />
              {isWsConnected ? '실시간 동기화' : 'HTTP 폴백'}
            </span>
          </div>
          <div className="ops-counts">
            <div className="ops-count"><span className="ops-count__label">전체</span><strong className="ops-count__value">{kpiCounts.total}</strong></div>
            <div className="ops-count ops-count--ok"><span className="ops-count__label">실행 중</span><strong className="ops-count__value">{kpiCounts.running}</strong></div>
            <div className="ops-count"><span className="ops-count__label">중지·미생성</span><strong className="ops-count__value">{kpiCounts.stopped}</strong></div>
            <div className="ops-count ops-count--danger"><span className="ops-count__label">오류</span><strong className="ops-count__value">{kpiCounts.error}</strong></div>
            {diskUsage ? (
              <div
                className={`ops-count ${diskUsage.state === 'warn' ? 'ops-count--danger' : ''}`}
                title={diskUsage.summary.detail}
              >
                <span className="ops-count__label">정리 가능 용량</span>
                <strong className="ops-count__value text-base">
                  {diskUsage.state === 'unknown'
                    ? '확인 불가'
                    : diskUsage.summary.text.replace('정리 시 약 ', '').replace(' 확보 가능', '')}
                </strong>
              </div>
            ) : null}
          </div>
          {diskUsage?.state === 'warn' ? (
            <p className="text-xs text-danger mt-2">
              {diskUsage.summary.detail} 정리는 SSH에서{' '}
              <code className="font-mono">{diskUsage.summary.next_action}</code>
            </p>
          ) : null}
        </div>
        <aside className="ops-signal" aria-label="동기화 상태">
          <div>
            <p className="ops-signal__label">observability signal</p>
            <p className="ops-signal__value">
              <span className={`w-2 h-2 rounded-full ${isWsConnected ? 'bg-ok animate-pulse' : 'bg-warn'}`} />
              {isWsConnected ? 'WebSocket 연결됨' : '폴백 폴링 중'}
            </p>
          </div>
          <p className="ops-signal__detail">{isWsConnected ? '상태와 차트가 수신 프레임에 맞춰 갱신됩니다.' : 'WebSocket 복구 전에는 HTTP 조회 결과를 표시합니다.'}</p>
          {/* "관리도구 자신이 정상인가"는 비전문 운영자의 첫 질문인데 답할 화면이
              없었다. /health는 이미 있었지만 어느 UI에도 표시되지 않았다. */}
          <div className="mt-3 pt-3 border-t border-line">
            <p className="ops-signal__label">manager health</p>
            <p className="ops-signal__value">
              <span
                className={`w-2 h-2 rounded-full ${
                  managerHealth === 'healthy'
                    ? 'bg-ok'
                    : managerHealth === 'checking'
                      ? 'bg-warn animate-pulse'
                      : 'bg-danger'
                }`}
              />
              {managerHealth === 'healthy'
                ? '관리도구 정상'
                : managerHealth === 'checking'
                  ? '관리도구 확인 중'
                  : '관리도구 응답 없음'}
            </p>
            <p className="ops-signal__detail">
              {managerHealth === 'healthy'
                ? '백엔드 API가 응답하고 있습니다.'
                : managerHealth === 'checking'
                  ? '백엔드 상태를 확인하는 중입니다.'
                  : '백엔드가 응답하지 않습니다. 아래 컨테이너 상태도 최신이 아닐 수 있습니다.'}
            </p>
          </div>
        </aside>
      </section>

      {error && !isWsConnected && (
        <section className="mb-4 flex items-start gap-3 border border-danger/30 bg-danger/5 p-4 text-sm rounded-panel" role="alert">
          <ShieldAlert className="w-5 h-5 shrink-0 text-danger" />
          <div>
            <p className="font-semibold text-danger">통신 연결 오류</p>
            <p className="mt-1 text-ink">백엔드 서버가 {BACKEND_URL}에서 실행 중인지와 Docker 엔진 상태를 확인해 주세요.</p>
          </div>
        </section>
      )}

      {containerGroups.length > 0 && (
        <section className="ops-ledger mb-4" aria-labelledby="service-groups-title">
          <div className="ops-ledger__header">
            <div>
              <h2 className="ops-section-title" id="service-groups-title">앱별 상태</h2>
              <p className="ops-section-copy">
                컨테이너를 앱 단위로 묶어 보여 줍니다. 재시작은 실행 중인 것만 순서대로 진행합니다.
              </p>
            </div>
          </div>
          <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
            {containerGroups.map((group) => (
              <div className="rounded-card border border-line p-3" key={group.id}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-strong truncate">{group.label}</p>
                    <p
                      className={`text-xs mt-0.5 ${group.healthy ? 'text-ok' : 'text-warn'}`}
                    >
                      {group.healthy
                        ? `모두 정상 (${group.containers.length}개)`
                        : `${group.containers.length - group.runningCount}개 중지됨 / 전체 ${group.containers.length}개`}
                    </p>
                  </div>
                  <button
                    className="ops-button shrink-0"
                    disabled={bulkRestartingGroup !== null || group.runningCount === 0}
                    onClick={() => void restartGroup(group)}
                    type="button"
                  >
                    <RotateCw
                      className={`w-4 h-4 ${bulkRestartingGroup === group.id ? 'animate-spin' : ''}`}
                    />
                    재시작
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="ops-ledger" aria-labelledby="service-ledger-title">
        <div className="ops-ledger__header">
          <div>
            <h2 className="ops-section-title" id="service-ledger-title">서비스 원장</h2>
            <p className="ops-section-copy">수치 버튼은 최근 1시간의 해당 메트릭 차트를 엽니다.</p>
          </div>
          <span className="ops-status-badge"><Activity className="w-3 h-3 text-brand" /> 현재 상태</span>
        </div>

        {isLoading && displayContainers.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-16">
            <RefreshCw className="mb-3 h-7 w-7 animate-spin text-brand" />
            <p className="text-sm text-secondary">컨테이너 상태를 분석하는 중입니다.</p>
          </div>
        ) : (
          <table className="ops-fleet-table text-left">
              <thead className="sticky top-0 z-10">
                <tr>
                  <th>상태</th><th>컨테이너</th><th>역할</th><th>포트</th><th className="text-center">CPU</th><th className="text-center">메모리</th><th className="text-center">I/O</th><th className="text-center">도구</th><th className="text-right">제어</th>
                </tr>
              </thead>
              <tbody className="text-xs md:text-sm">
                {displayContainers.map((container) => {
                  const statusCfg = getStatusConfig(container.status);
                  const { Icon, displayName } = getContainerPresentation(container);
                  
                  const isActionPending = actionMutation.isPending && actionMutation.variables?.id === container.id;
                  const isConfigPending = configMutation.isPending && configMutation.variables?.id === container.id;
                  const isResetPending = resetMutation.isPending && resetMutation.variables === container.id;
                  const isContainerLoading = isActionPending || isConfigPending || isResetPending;

                  const metrics = container.metrics || {
                    cpu_pct: 0.0,
                    mem_pct: 0.0,
                    mem_usage: 0,
                    mem_limit: 0,
                    io_read: 0,
                    io_write: 0
                  };

                  return (
                    <tr key={container.id}>
                      {/* Status Indicator */}
                      <td data-label="상태">
                        <div className="flex items-center gap-2.5">
                          <span className={`w-2 h-2 rounded-full ${statusCfg.dotClass}`} />
                          <span
                            className={`${statusCfg.textClass} text-xs md:text-sm tracking-[0.05em] font-bold`}
                            title={container.status}
                          >
                            {statusLabel(container.status)}
                          </span>
                        </div>
                      </td>

                      {/* Display & Container Name */}
                      <td data-label="컨테이너">
                        <div className="flex items-center gap-3">
                          <div className="p-2 bg-subtle border border-line rounded-card shrink-0">
                            <Icon className="w-5 h-5 text-brand" />
                          </div>
                          <div>
                            <div className="font-display font-semibold text-strong text-base">{displayName}</div>
                            <div className="text-secondary text-xs md:text-sm mt-0.5 font-mono tabular-nums">{container.name}</div>
                            {container.public_url && (
                              <a
                                href={container.public_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                title="운영(prod) 공개 주소"
                                className="block text-brand text-xs md:text-sm mt-0.5 font-mono underline hover:opacity-80 break-all"
                              >
                                {container.public_url.replace(/^https?:\/\//, '')}
                              </a>
                            )}
                          </div>
                        </div>
                      </td>

                      {/* Role */}
                      <td data-label="역할" className="text-ink text-xs md:text-sm">
                        <span title={container.role ?? undefined}>{roleLabel(container.role)}</span>
                      </td>

                      {/* Port Bindings */}
                      <td data-label="포트" className="font-mono tabular-nums text-strong text-xs md:text-sm break-all">
                        {container.ports.length > 0
                          ? container.ports.join(', ')
                          : (container.expected_ports || []).join(', ') || '내부 노출'
                        }
                      </td>

                      {/* CPU Metric (Interactive) */}
                      <td data-label="CPU" className="text-center">
                        <button 
                          type="button"
                          disabled={container.status !== 'running'}
                          onClick={() => container.status === 'running' && openChartModal(container.id, 'cpu')}
                          className="ops-metric"
                          title={container.status === 'running' ? '지난 1시간 CPU 사용 이력 보기' : ''}
                        >
                          <span className="flex items-center gap-1 font-mono tabular-nums font-bold text-xs md:text-sm">
                            <Cpu className="w-3.5 h-3.5 opacity-80" />
                            {container.status === 'running' ? `${metrics.cpu_pct.toFixed(1)}%` : '0.0%'}
                          </span>
                          <span className="text-[10px] md:text-xs text-secondary mt-0.5 font-semibold">차트</span>
                        </button>
                      </td>

                      {/* Memory Metric (Interactive) */}
                      <td data-label="메모리" className="text-center">
                        <button 
                          type="button"
                          disabled={container.status !== 'running'}
                          onClick={() => container.status === 'running' && openChartModal(container.id, 'memory')}
                          className="ops-metric"
                          title={container.status === 'running' ? '지난 1시간 메모리 사용 이력 보기' : ''}
                        >
                          <span className="flex items-center gap-1 font-mono tabular-nums font-bold text-xs md:text-sm">
                            <HardDrive className="w-3.5 h-3.5 opacity-80" />
                            {container.status === 'running' ? `${metrics.mem_pct.toFixed(1)}%` : '0.0%'}
                          </span>
                          <span className="text-[10px] md:text-xs text-secondary mt-0.5 uppercase tracking-[0.05em] font-bold font-mono tabular-nums">
                            {container.status === 'running' ? formatBytes(metrics.mem_usage) : '0 B'}
                          </span>
                        </button>
                      </td>

                      {/* I/O Metrics (Interactive) */}
                      <td data-label="I/O" className="text-center">
                        <button 
                          type="button"
                          disabled={container.status !== 'running'}
                          onClick={() => container.status === 'running' && openChartModal(container.id, 'io')}
                          className="ops-metric"
                          title={container.status === 'running' ? '지난 1시간 I/O 이력 보기' : ''}
                        >
                          <span className="font-mono tabular-nums text-xs md:text-sm font-semibold space-y-0.5 block">
                            <span className="block text-warn">R: {container.status === 'running' ? formatBytes(metrics.io_read) : '0 B'}</span>
                            <span className="block text-danger">W: {container.status === 'running' ? formatBytes(metrics.io_write) : '0 B'}</span>
                          </span>
                          <span className="text-[10px] md:text-xs text-secondary mt-0.5 font-semibold">차트</span>
                        </button>
                      </td>

                      {/* Terminal Log & Configuration */}
                      <td data-label="도구" className="text-center">
                        <div className="flex items-center justify-center gap-1.5">
                          <button
                            type="button"
                            onClick={() => openLogModal(container.id)}
                            className="ops-icon-button"
                            title="실시간 터미널 로그 스트리밍 모달 열기"
                          >
                            <Terminal className="w-4 h-4" />
                          </button>

                          {/* 컨테이너가 없거나 docker가 죽어 있으면 inspect가 500이다.
                              다른 지표 버튼과 같은 방식으로 미리 막는다. */}
                          <button
                            type="button"
                            disabled={
                              container.status === 'not_created' ||
                              container.status === 'offline'
                            }
                            onClick={() => setDetailContainer(container)}
                            className="ops-icon-button"
                            title={
                              container.status === 'not_created' ||
                              container.status === 'offline'
                                ? '컨테이너가 생성되지 않아 상세 정보를 볼 수 없습니다'
                                : 'inspect 상세(mounts·networks·healthcheck·env) 보기'
                            }
                          >
                            <Boxes className="w-4 h-4" />
                          </button>

                          <button
                            type="button"
                            onClick={() => openConfigModal(container)}
                            className="ops-icon-button"
                            title="컨테이너 세부 설정 변경"
                          >
                            <Settings className="w-4 h-4" />
                          </button>
                        </div>
                      </td>

                      {/* Controller Actions */}
                      <td data-label="제어" className="text-right">
                        <div className="inline-flex gap-1.5 items-center">
                          {isContainerLoading ? (
                            <div className="flex items-center gap-1.5 text-xs text-secondary font-semibold py-2 px-3">
                              <RefreshCw className="w-3.5 h-3.5 animate-spin text-brand" />
                              <span>처리 중</span>
                            </div>
                          ) : (
                            <>
                              <button
                                type="button"
                                onClick={() => handleAction(container.id, 'start')}
                                disabled={actionMutation.isPending || container.status === 'running'}
                                className="ops-button text-ok border-ok hover:border-ok hover:bg-ok hover:text-card disabled:hover:bg-card disabled:hover:text-ok"
                                title="컨테이너 가동"
                              >
                                <Play className="w-3 h-3" />
                                Start
                              </button>

                              <button
                                type="button"
                                onClick={() => handleAction(container.id, 'stop')}
                                disabled={actionMutation.isPending || container.status !== 'running'}
                                className="ops-button ops-button--danger text-danger border-danger disabled:hover:bg-card disabled:hover:text-danger"
                                title="컨테이너 정지"
                              >
                                <Square className="w-3 h-3" />
                                Stop
                              </button>

                              <button
                                type="button"
                                onClick={() => handleAction(container.id, 'restart')}
                                disabled={actionMutation.isPending || container.status !== 'running'}
                                className="ops-icon-button"
                                title="컨테이너 재부팅"
                              >
                                <RotateCw className="w-3.5 h-3.5" />
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
        )}
      </section>

      {isCommandPaletteOpen && (
        <div
          className="ops-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setIsCommandPaletteOpen(false);
          }}
        >
          <div aria-label="빠른 명령" aria-modal="true" className="ops-command-dialog" role="dialog">
            <input
              aria-label="명령 검색"
              className="ops-command-dialog__input"
              onChange={(event) => setCommandQuery(event.target.value)}
              placeholder="명령 검색"
              ref={commandInputRef}
              value={commandQuery}
            />
            <div className="ops-command-dialog__list" role="list">
              {commandItems.length ? commandItems.map((item) => (
                <button
                  className="ops-command-dialog__option"
                  key={item.id}
                  onClick={() => runCommand(item.run)}
                  type="button"
                >
                  <span>{item.label}</span>
                  <span className="font-mono text-[10px] text-secondary">{item.hint}</span>
                </button>
              )) : <p className="px-3 py-5 text-sm text-secondary">일치하는 명령이 없습니다.</p>}
            </div>
          </div>
        </div>
      )}

      {isAdminSettingsOpen && (
        <div className="ops-modal-backdrop select-text">
          <AdminSettingsPanel onClose={() => setIsAdminSettingsOpen(false)} />
        </div>
      )}

      {isBackupHistoryOpen && (
        <div className="ops-modal-backdrop select-text">
          <BackupHistoryPanel onClose={() => setIsBackupHistoryOpen(false)} />
        </div>
      )}

      {isRuntimePinsOpen && (
        <div className="ops-modal-backdrop select-text">
          <RuntimePinPanel onClose={() => setIsRuntimePinsOpen(false)} />
        </div>
      )}

      {isSourceStatusOpen && (
        <div className="ops-modal-backdrop select-text">
          <SourceStatusPanel onClose={() => setIsSourceStatusOpen(false)} />
        </div>
      )}

      {/* Live Log Terminal Modal */}
      {isLogModalOpen && logContainerId && (
        <div className="ops-modal-backdrop select-text">
          <div
            aria-label="실시간 콘솔 로그"
            aria-modal="true"
            role="dialog"
            className="ops-modal max-w-4xl p-6 flex flex-col h-[75vh] relative"
          >

            {/* Modal Header */}
            <div className="flex justify-between items-center pb-4 border-b border-line z-10 shrink-0">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-subtle border border-line rounded-card">
                  <Terminal className="w-5 h-5 text-brand" />
                </div>
                <div>
                  <h3 className="font-semibold text-strong text-base uppercase tracking-[0.05em]">실시간 콘솔 로그</h3>
                  <p className="text-xs text-secondary mt-0.5 font-light">컨테이너 ID: {logContainerId}</p>
                </div>
              </div>

              <button
                type="button"
                aria-label="닫기"
                autoFocus
                onClick={() => setIsLogModalOpen(false)}
                className="ops-icon-button"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Terminal View */}
            <div className="flex-grow bg-subtle border border-line rounded-card p-4 mt-4 font-mono text-xs overflow-y-auto leading-relaxed text-strong scrollbar-thin select-text">
              <pre className="whitespace-pre-wrap select-text pr-2 text-left">
                {liveLogs}
              </pre>
              <div ref={logEndRef} />
            </div>

            {/* Tip Footer */}
            <div className="pt-4 text-[10px] text-secondary shrink-0 z-10 flex justify-between items-center">
              <span className="font-light">* 최신 3,000줄의 로그가 메모리에 버퍼링되며 자동으로 아래로 스크롤됩니다.</span>
              <span className="flex items-center gap-1 font-bold uppercase tracking-[0.05em]">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${isLogWsOpen ? 'bg-ok animate-ping' : 'bg-warn'}`}
                />
                {isLogWsOpen ? 'WS 스트리밍 활성화됨' : 'WS 스트리밍 종료됨'}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Performance History Chart Modal */}
      {isChartModalOpen && chartContainerId && (
        <div className="ops-modal-backdrop">
          <div
            aria-label="실시간 성능 차트"
            aria-modal="true"
            role="dialog"
            className="ops-modal max-w-3xl p-6 flex flex-col relative"
          >

            {/* Modal Header */}
            <div className="flex justify-between items-center pb-4 border-b border-line z-10 shrink-0">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-subtle border border-line rounded-card">
                  <Activity className="w-5 h-5 text-brand" />
                </div>
                <div>
                  <h3 className="font-semibold text-strong text-base tracking-[0.05em]">
                    성능 이력 ({chartHours === 1 ? '최근 1시간' : `최근 ${chartHours}시간`})
                  </h3>
                  <p className="text-xs text-secondary mt-0.5 font-light font-mono">대상 컨테이너: {chartContainerId}</p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <label className="text-xs text-secondary" htmlFor="chart-hours">
                  기간
                </label>
                <select
                  className="ops-input min-h-[36px] py-1 text-xs"
                  id="chart-hours"
                  onChange={(event) => setChartHours(Number(event.target.value))}
                  value={chartHours}
                >
                  <option value={1}>1시간</option>
                  <option value={6}>6시간</option>
                  <option value={24}>24시간</option>
                  <option value={72}>3일</option>
                </select>
              </div>

              <button
                type="button"
                aria-label="닫기"
                autoFocus
                onClick={() => {
                  setIsChartModalOpen(false);
                  setWsMetricsPoints([]); // 이벤트 핸들러에서 직접 초기화하여 derived-state 경고 방지
                }}
                className="ops-icon-button"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Metric Tab Selector */}
            <div className="flex border-b border-line mt-5 shrink-0 z-10">
              <button
                type="button"
                onClick={() => setChartMetricType('cpu')}
                className={`py-2 px-4 text-xs font-bold tracking-[0.05em] uppercase transition-colors border-b-2 outline-hidden ${
                  chartMetricType === 'cpu'
                    ? 'border-brand text-brand'
                    : 'border-transparent text-secondary hover:text-strong'
                }`}
              >
                CPU 점유율 (%)
              </button>
              <button
                type="button"
                onClick={() => setChartMetricType('memory')}
                className={`py-2 px-4 text-xs font-bold tracking-[0.05em] uppercase transition-colors border-b-2 outline-hidden ${
                  chartMetricType === 'memory'
                    ? 'border-brand text-brand'
                    : 'border-transparent text-secondary hover:text-strong'
                }`}
              >
                메모리 점유율 (%)
              </button>
              <button
                type="button"
                onClick={() => setChartMetricType('io')}
                className={`py-2 px-4 text-xs font-bold tracking-[0.05em] uppercase transition-colors border-b-2 outline-hidden ${
                  chartMetricType === 'io'
                    ? 'border-brand text-brand'
                    : 'border-transparent text-secondary hover:text-strong'
                }`}
              >
                I/O Read / Write (Bytes)
              </button>
            </div>

            {/* Chart Container */}
            <div className="h-[300px] mt-6 w-full z-10 bg-subtle border border-line rounded-card p-4 flex items-center justify-center">
              {isLoadingChart && combinedChartData.length === 0 ? (
                <div className="flex items-center gap-2 text-ink text-xs">
                  <RefreshCw className="w-4 h-4 animate-spin text-brand" />
                  <span>차트 기록을 조회하고 있습니다...</span>
                </div>
              ) : combinedChartData.length === 0 ? (
                <div className="text-secondary text-xs py-20 font-light">
                  최근 수집된 메트릭 이력이 없습니다. (수집기는 10초 주기로 수집하며 한달 저장됩니다.)
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={combinedChartData}
                    margin={{ top: 5, right: 10, left: 10, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-line)" />
                    <XAxis
                      dataKey="timestamp"
                      tickFormatter={formatTimestamp}
                      stroke="var(--color-secondary)"
                      style={{ fontSize: 14, fontFamily: 'var(--font-mono)' }}
                      dy={5}
                    />
                    <YAxis
                      stroke="var(--color-secondary)"
                      style={{ fontSize: 14, fontFamily: 'var(--font-mono)' }}
                      dx={-5}
                      tickFormatter={(value) => {
                        if (chartMetricType === 'io') {
                          return formatBytes(value, 0);
                        }
                        return `${value}%`;
                      }}
                    />
                    <RechartsTooltip
                      contentStyle={{
                        backgroundColor: 'var(--color-card)',
                        border: '1px solid var(--color-line)',
                        borderRadius: 'var(--radius-card)',
                        fontSize: 14, // Increased fontSize to 14
                        fontFamily: 'var(--font-mono)',
                        color: 'var(--color-strong)'
                      }}
                      labelFormatter={(label) => `수집 시각: ${formatTimestamp(label as string)}`}
                      formatter={(value: any, name: any) => {
                        const formattedVal = chartMetricType === 'io' ? formatBytes(value as number) : `${Number(value).toFixed(1)}%`;
                        const labelName = name === 'cpu_pct' ? 'CPU 사용량' : name === 'mem_pct' ? '메모리 사용량' : name === 'io_read' ? 'Disk Read' : 'Disk Write';
                        return [formattedVal, labelName];
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: 14, marginTop: 10 }} />
                    
                    {chartMetricType === 'cpu' && (
                      <Line
                        type="monotone"
                        dataKey="cpu_pct"
                        stroke="var(--color-ok)"
                        strokeWidth={2}
                        dot={false}
                        activeDot={{ r: 4 }}
                        name="cpu_pct"
                      />
                    )}
                    
                    {chartMetricType === 'memory' && (
                      <Line
                        type="monotone"
                        dataKey="mem_pct"
                        stroke="var(--color-brand)"
                        strokeWidth={2}
                        dot={false}
                        activeDot={{ r: 4 }}
                        name="mem_pct"
                      />
                    )}
                    
                    {chartMetricType === 'io' && (
                      <>
                        <Line
                          type="monotone"
                          dataKey="io_read"
                          stroke="var(--color-warn)"
                          strokeWidth={1.5}
                          dot={false}
                          name="io_read"
                        />
                        <Line
                          type="monotone"
                          dataKey="io_write"
                          stroke="var(--color-danger)"
                          strokeWidth={1.5}
                          dot={false}
                          name="io_write"
                        />
                      </>
                    )}
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>

            {/* Note */}
            <p className="text-[10px] text-secondary mt-4 z-10 shrink-0 font-light">
              * 웹소켓 연결 상태에서 매 10초마다 새로운 메트릭 데이터가 이 차트에 실시간으로 추가되어 업데이트(롤링)됩니다.
            </p>
          </div>
        </div>
      )}

      {/* Config Edit Modal */}
      {isConfigModalOpen && configTargetContainer && (
        <div className="ops-modal-backdrop">
          <div
            aria-label="컨테이너 설정 변경"
            aria-modal="true"
            role="dialog"
            className="ops-modal max-w-lg p-6 relative flex flex-col"
          >

            {/* Modal Header */}
            <div className="flex justify-between items-center pb-4 border-b border-line z-10">
              <h3 className="text-sm font-semibold tracking-[0.05em] flex items-center gap-2 text-strong uppercase">
                <Settings className="w-4 h-4 text-brand" />
                <span>컨테이너 설정 변경</span>
              </h3>
              <button
                type="button"
                aria-label="닫기"
                autoFocus
                onClick={() => setIsConfigModalOpen(false)}
                className="ops-icon-button"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Modal Body / Form */}
            <form onSubmit={handleConfigSubmit} className="flex-grow overflow-y-auto pr-2 mt-4 space-y-5 z-10 select-text scrollbar-thin">
              <div className="p-4 bg-subtle border border-line rounded-card text-xs text-ink leading-relaxed">
                <p className="font-semibold text-strong mb-1 uppercase tracking-[0.05em]">{configTargetContainer.display_name} 설정</p>
                <p className="font-light">docker-compose.yml 파일 내 설정을 변경합니다. 변경 후 컨테이너가 중지/삭제된 뒤 재생성됩니다.</p>
              </div>

              {/* Ports section */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <h4 className="text-[10px] font-bold text-secondary uppercase tracking-[0.05em]">포트 매핑 (host:container)</h4>
                  <button
                    type="button"
                    onClick={() => setInputPortsList(prev => [...prev, ''])}
                    className="text-[10px] text-brand hover:underline font-bold uppercase tracking-[0.05em]"
                  >
                    + 추가
                  </button>
                </div>
                <div className="space-y-2">
                  {/* key에 값(port)을 넣으면 매 keystroke마다 key가 바뀌어 React가 input
                      DOM 노드를 파괴·재생성한다 — 실시간 검증 기능 자체가 타이핑할 때마다
                      포커스를 잃어 쓸 수 없게 된다(적대적 리뷰에서 발견). 행은 추가/삭제만
                      되고 재정렬되지 않으므로 index만으로 충분히 안정적인 key다. */}
                  {inputPortsList.map((port, idx) => (
                    <div key={`port-${idx}`}>
                      <div className="flex gap-2 items-center">
                        <input
                          type="text"
                          value={port}
                          onChange={(e) => {
                            const next = [...inputPortsList];
                            next[idx] = e.target.value;
                            setInputPortsList(next);
                          }}
                          placeholder="e.g. 5432:5432"
                          aria-invalid={!!configValidation.portErrors[idx]}
                          aria-describedby={
                            configValidation.portErrors[idx] ? `port-error-${idx}` : undefined
                          }
                          className={`bg-card border rounded-card min-h-[44px] px-4 py-2 text-xs text-strong outline-hidden focus-visible:outline-2 flex-grow font-mono ${
                            configValidation.portErrors[idx]
                              ? 'border-danger focus:border-danger focus-visible:outline-danger'
                              : 'border-line focus:border-brand focus-visible:outline-brand'
                          } focus:ring-0`}
                          aria-label={`포트 매핑 ${idx + 1}`} // Added aria-label for accessibility
                          required
                        />
                        <button
                          type="button"
                          onClick={() => setInputPortsList(prev => prev.filter((_, i) => i !== idx))}
                          className="text-danger hover:text-danger/80 p-1.5"
                          aria-label={`포트 매핑 ${idx + 1} 삭제`} // Added aria-label for accessibility
                        >
                          <X className="w-4.5 h-4.5" />
                        </button>
                      </div>
                      {configValidation.portErrors[idx] && (
                        <p id={`port-error-${idx}`} className="text-danger text-[11px] mt-1 pl-1">
                          {configValidation.portErrors[idx]}
                        </p>
                      )}
                    </div>
                  ))}
                  {inputPortsList.length === 0 && (
                    <p className="text-xs text-secondary font-light italic">포트 바인딩 설정이 없습니다.</p>
                  )}
                </div>
              </div>

              {/* Volumes section — 서버가 불변 계약으로 거부하므로 처음부터 읽기 전용이다.
                  편집을 허용한 뒤 저장 시점에 경고하면 사용자는 이미 값을 잃은 뒤다. */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <h4 className="text-[10px] font-bold text-secondary uppercase tracking-[0.05em]">
                    볼륨 마운트 (읽기 전용)
                  </h4>
                </div>
                <div className="space-y-2">
                  <p className="text-xs text-secondary">
                    볼륨은 이 화면에서 바꿀 수 없습니다. 데이터 위치가 바뀌면 기존 데이터를
                    잃을 수 있어 서버가 변경을 거부합니다.
                  </p>
                  {inputVolumesList.map((vol, idx) => (
                    <p
                      className="bg-subtle border border-line rounded-card px-4 py-2 text-xs text-secondary font-mono break-all"
                      key={`vol-${idx}`}
                    >
                      {vol}
                    </p>
                  ))}
                  {inputVolumesList.length === 0 && (
                    <p className="text-xs text-secondary font-light italic">볼륨 바인딩 설정이 없습니다.</p>
                  )}
                </div>
              </div>

              {/* Networks section */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <h4 className="text-[10px] font-bold text-secondary uppercase tracking-[0.05em]">네트워크 (default, etc.)</h4>
                  <button
                    type="button"
                    onClick={() => setInputNetworksList(prev => [...prev, ''])}
                    className="text-[10px] text-brand hover:underline font-bold uppercase tracking-[0.05em]"
                  >
                    + 추가
                  </button>
                </div>
                <div className="space-y-2">
                  {/* index-only key — 값을 key에 넣으면 keystroke마다 재마운트돼 포커스를 잃는다. */}
                  {inputNetworksList.map((net, idx) => (
                    <div key={`net-${idx}`}>
                      <div className="flex gap-2 items-center">
                        <input
                          type="text"
                          value={net}
                          onChange={(e) => {
                            const next = [...inputNetworksList];
                            next[idx] = e.target.value;
                            setInputNetworksList(next);
                          }}
                          placeholder="e.g. default"
                          aria-invalid={!!configValidation.networkErrors[idx]}
                          aria-describedby={
                            configValidation.networkErrors[idx] ? `network-error-${idx}` : undefined
                          }
                          className={`bg-card border rounded-card min-h-[44px] px-4 py-2 text-xs text-strong outline-hidden focus-visible:outline-2 flex-grow font-mono ${
                            configValidation.networkErrors[idx]
                              ? 'border-danger focus:border-danger focus-visible:outline-danger'
                              : 'border-line focus:border-brand focus-visible:outline-brand'
                          } focus:ring-0`}
                          aria-label={`네트워크 ${idx + 1}`} // Added aria-label for accessibility
                          required
                        />
                        <button
                          type="button"
                          onClick={() => setInputNetworksList(prev => prev.filter((_, i) => i !== idx))}
                          className="text-danger hover:text-danger/80 p-1.5"
                          aria-label={`네트워크 ${idx + 1} 삭제`} // Added aria-label for accessibility
                        >
                          <X className="w-4.5 h-4.5" />
                        </button>
                      </div>
                      {configValidation.networkErrors[idx] && (
                        <p id={`network-error-${idx}`} className="text-danger text-[11px] mt-1 pl-1">
                          {configValidation.networkErrors[idx]}
                        </p>
                      )}
                    </div>
                  ))}
                  {inputNetworksList.length === 0 && (
                    <p className="text-xs text-secondary font-light italic">네트워크 설정이 없습니다.</p>
                  )}
                </div>
              </div>

              {/* Env Variables section */}
              {Object.keys(inputEnvDict).length > 0 && (
                <div className="space-y-3">
                  <h4 className="text-[10px] font-bold text-secondary uppercase tracking-[0.05em]">환경 변수</h4>
                  <div className="grid grid-cols-1 gap-4">
                    {Object.entries(inputEnvDict).map(([key, val]) => {
                      const envError = configValidation.envErrors[key];
                      // 계약이 고정한 값은 서버가 저장 시점에 거부한다. 편집 가능하게
                      // 두면 "저장했는데 다음 재구축이 실패"하는 경로가 열린다.
                      const locked = configValidation.lockedEnv.has(key);
                      return (
                        <div key={key} className="flex flex-col gap-1.5">
                          <label className="text-xs text-secondary font-mono font-light" htmlFor={`env-input-${key}`}>
                            {key}
                            {locked ? <span className="ml-2 font-sans">· 읽기 전용</span> : null}
                          </label>
                          <input
                            id={`env-input-${key}`}
                            type="text"
                            value={val}
                            readOnly={locked}
                            onChange={(e) => setInputEnvDict(prev => ({ ...prev, [key]: e.target.value }))}
                            aria-invalid={!!envError}
                            aria-describedby={
                              envError
                                ? `env-error-${key}`
                                : locked
                                  ? `env-locked-${key}`
                                  : undefined
                            }
                            className={`bg-card border rounded-card min-h-[44px] px-4 py-2 text-xs outline-hidden focus-visible:outline-2 w-full transition-colors font-mono ${
                              locked ? 'text-secondary bg-subtle' : 'text-strong'
                            } ${
                              envError
                                ? 'border-danger focus:border-danger focus-visible:outline-danger'
                                : 'border-line focus:border-brand focus-visible:outline-brand'
                            } focus:ring-0`}
                            aria-label={`환경 변수 ${key}`} // Added aria-label for accessibility
                            required
                          />
                          {locked && (
                            <p id={`env-locked-${key}`} className="text-secondary text-[11px]">
                              배포 계약이 고정한 값입니다. 바꾸려면 계약 자체를 수정해야 하며,
                              여기서 바꾸면 다음 재구축이 거부됩니다.
                            </p>
                          )}
                          {envError && (
                            <p id={`env-error-${key}`} className="text-danger text-[11px]">{envError}</p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* 잠금이 뚫린 경우의 최종 안내. 정상 경로에서는 나타나지 않는다. */}
              {configValidation.lockedEnvChanged.length > 0 && (
                <p aria-live="polite" className="text-danger text-xs">
                  배포 계약이 고정한 환경변수가 바뀌었습니다:{' '}
                  {configValidation.lockedEnvChanged.join(', ')}. 저장할 수 없습니다.
                </p>
              )}

              {/* 변경 사항 미리보기: 제출 전 무엇이 바뀌는지 요약한다. */}
              {(configValidation.portsDiff.changed ||
                configValidation.networksDiff.changed ||
                configValidation.envDiff.length > 0) && (
                <div
                  aria-live="polite"
                  className="space-y-2 p-4 bg-subtle border border-line rounded-card text-xs"
                >
                  <h4 className="text-[10px] font-bold text-secondary uppercase tracking-[0.05em]">
                    변경 사항 미리보기
                  </h4>
                  {configValidation.portsDiff.changed && (
                    <div>
                      {configValidation.portsDiff.added.map((p) => (
                        <p key={`port-add-${p}`} className="text-ok font-mono">+ 포트 {p}</p>
                      ))}
                      {configValidation.portsDiff.removed.map((p) => (
                        <p key={`port-rm-${p}`} className="text-danger font-mono">- 포트 {p}</p>
                      ))}
                    </div>
                  )}
                  {configValidation.networksDiff.changed && (
                    <div>
                      {configValidation.networksDiff.added.map((n) => (
                        <p key={`net-add-${n}`} className="text-ok font-mono">+ 네트워크 {n}</p>
                      ))}
                      {configValidation.networksDiff.removed.map((n) => (
                        <p key={`net-rm-${n}`} className="text-danger font-mono">- 네트워크 {n}</p>
                      ))}
                    </div>
                  )}
                  {configValidation.envDiff.length > 0 && (
                    <div className="space-y-1">
                      {configValidation.envDiff.map((row) => (
                        <p key={`env-diff-${row.key}`} className="font-mono break-all">
                          <span className="text-secondary">{row.key}:</span>{' '}
                          <span className="text-danger">{row.before ?? '(없음)'}</span>
                          {' → '}
                          <span className="text-ok">{row.after ?? '(삭제)'}</span>
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Submit / Reset Actions */}
              <div className="flex gap-3 mt-6">
                <button
                  type="button"
                  onClick={() => {
                    if (confirm('정말로 설정을 기본값으로 원복하시겠습니까?')) {
                      resetMutation.mutate(configTargetContainer.id);
                    }
                  }}
                  disabled={resetMutation.isPending || configMutation.isPending}
                  className="ops-button ops-button--danger flex-1 text-danger border-danger disabled:opacity-40"
                >
                  {resetMutation.isPending ? '원복 중...' : '기본값 원복'}
                </button>

                <button
                  type="submit"
                  disabled={
                    configMutation.isPending ||
                    resetMutation.isPending ||
                    configValidation.hasBlockingIssue
                  }
                  title={
                    configValidation.hasBlockingIssue
                      ? '위에 표시된 오류를 먼저 해결하세요'
                      : undefined
                  }
                  className="ops-button ops-button--primary flex-1"
                >
                  {configMutation.isPending ? (
                    <>
                      <RefreshCw className="w-4 h-4 animate-spin" />
                      <span>적용 중...</span>
                    </>
                  ) : (
                    <span>적용 및 재생성</span>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {detailContainer && (
        <ContainerDetailModal
          containerId={detailContainer.id}
          containerLabel={detailContainer.display_name || detailContainer.name}
          targetId={detailTarget?.id ?? null}
          targetServices={detailTarget?.resolved_services ?? null}
          onClose={closeDetailModal}
        />
      )}

      <ToastStack items={toasts} onDismiss={dismissToast} />
    </AppShell>
  );
}
