'use client';

import React, { useState, useEffect, useRef, useMemo } from 'react';
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
  Boxes,
  KeyRound,
  LogOut
} from 'lucide-react';
import dynamic from 'next/dynamic';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import AdminSettingsPanel from './AdminSettingsPanel';
import LoginScreen from './LoginScreen';
import { AuthMe, BACKEND_URL, apiJson, apiWsUrl, setUnauthorizedHandler } from '@/lib/api';

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

  const {
    data: auth,
    isLoading: isAuthLoading,
    refetch: refetchAuth,
  } = useQuery<AuthMe>({
    queryKey: ['auth-me'],
    queryFn: () => apiJson<AuthMe>('/api/v1/auth/me', { redirectOnUnauthorized: false }),
    retry: false,
    refetchInterval: false,
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
  const logEndRef = useRef<HTMLDivElement>(null);

  // Performance Chart Modal States
  const [isChartModalOpen, setIsChartModalOpen] = useState<boolean>(false);
  const [chartContainerId, setChartContainerId] = useState<string | null>(null);
  const [chartMetricType, setChartMetricType] = useState<'cpu' | 'memory' | 'io'>('cpu');

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
    if (!isLogModalOpen && !isChartModalOpen && !isConfigModalOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      if (isConfigModalOpen) {
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
  }, [isLogModalOpen, isChartModalOpen, isConfigModalOpen]);

  // Fallback Polling (Query) - Versioned v1
  const { data: fallbackContainers = [], isLoading, error } = useQuery<ContainerStatus[]>({
    queryKey: ['containers'],
    queryFn: () => apiJson<ContainerStatus[]>('/api/v1/containers'),
    refetchInterval: 5000,
    enabled: isAuthenticated && !isWsConnected, // Only run polling if WebSocket is offline
  });

  // Active containers dataset (WS if available, fallback query otherwise)
  const displayContainers = wsContainers || fallbackContainers;

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

    let ws: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;

    const connectWS = () => {
      const wsUrl = apiWsUrl('/api/v1/ws/status');
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        setIsWsConnected(true);
      };

      ws.onmessage = (event) => {
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

      ws.onclose = () => {
        // Multi-setState is fine in standard handlers, but let's reset cleanly
        setIsWsConnected(false);
        setWsContainers(null);
        reconnectTimeout = setTimeout(connectWS, 3000); // Retry every 3 seconds
      };

      ws.onerror = (err) => {
        console.error('WebSocket error:', err);
        ws.close();
      };
    };

    connectWS();

    return () => {
      if (ws) ws.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
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
  });

  const handleAction = (id: string, action: string) => {
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
      alert('설정이 성공적으로 반영되었으며, 컨테이너가 재생성되었습니다.');
    },
    onError: (err: any) => {
      alert(`설정 변경 실패: ${err.message}`);
    }
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
      alert('설정이 기본값으로 원복되었으며, 컨테이너가 재생성되었습니다.');
    },
    onError: (err: any) => {
      alert(`설정 원복 실패: ${err.message}`);
    }
  });

  // 1-Hour Performance Metrics History Query - Versioned v1
  // Disabled conditional triggers / useEffect copies to resolve 'no-derived-state' and 'no-event-handler'
  const { data: queryChartData = [], isLoading: isLoadingChart } = useQuery<MetricHistoryPoint[]>({
    queryKey: ['metrics-history', chartContainerId, chartMetricType],
    queryFn: async () => {
      if (!chartContainerId) return [];
      return apiJson<MetricHistoryPoint[]>(
        `/api/v1/containers/${chartContainerId}/metrics?hours=1`
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
    const ws = new WebSocket(wsUrl);

    setLiveLogs('--- 실시간 로그 스트리밍을 시작합니다 ---\n');

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

    ws.onclose = () => {
      setLiveLogs((prev) => prev + '\n--- 스트리밍 연결이 닫혔습니다 ---\n');
    };

    return () => {
      if (ws) ws.close();
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
    if (!configTargetContainer) return;
    configMutation.mutate({
      id: configTargetContainer.id,
      ports: inputPortsList,
      env: inputEnvDict,
      volumes: inputVolumesList,
      networks: inputNetworksList,
    });
  };

  const openLogModal = (id: string) => {
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
    <div className="min-h-screen bg-page text-ink flex flex-col relative overflow-hidden select-none">
      {/* 4px Brand Accent Stripe Pinned to Top */}
      <div className="h-1 w-full bg-brand fixed top-0 left-0 z-50" />

      {/* Admin Top Bar - Compact header with border-b */}
      <header className="w-full bg-card border-b border-line mt-1 shadow-card">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 px-6 md:px-12 py-5">
          <div className="select-text min-w-0">
            <span className="inline-block text-[9px] font-semibold tracking-[0.05em] uppercase text-secondary border border-line px-2 py-0.5 bg-subtle rounded-md">
              PINVI SYSTEM INFRASTRUCTURE
            </span>
            <h1 className="text-xl md:text-2xl font-semibold tracking-tight text-strong mt-2">
              인프라 서비스 컨트롤 센터
            </h1>
            <p className="text-secondary text-sm font-sans mt-1 leading-relaxed">
              공용 데이터베이스, 오브젝트 스토리지, 지오코더, 관측 스택을 제어하고 모니터링하는 통합 인프라 관리 센터입니다.
            </p>
          </div>

          <div className="flex items-center gap-2 select-none shrink-0 self-start md:self-auto">
            <button
              type="button"
              onClick={() => setIsAdminSettingsOpen(true)}
              className="flex items-center gap-2 bg-card hover:bg-subtle border border-line px-3 py-2 rounded-card text-xs font-semibold text-ink"
            >
              <KeyRound className="w-4 h-4 text-brand" />
              인증 설정
            </button>
            <button
              type="button"
              onClick={() => logoutMutation.mutate()}
              disabled={logoutMutation.isPending}
              className="flex items-center gap-2 bg-card hover:bg-subtle border border-line px-3 py-2 rounded-card text-xs font-semibold text-ink disabled:opacity-60"
            >
              <LogOut className="w-4 h-4 text-secondary" />
              로그아웃
            </button>
            {/* WebSocket Status Indicator */}
            <div className="flex items-center gap-2 bg-subtle border border-line px-3 py-2 rounded-card text-[10px] tracking-[0.05em] uppercase font-semibold">
              {isWsConnected ? (
                <>
                  <span className="w-2 h-2 rounded-full bg-ok animate-pulse" />
                  <span className="text-ok">REALTIME WS SYNC</span>
                </>
              ) : (
                <>
                  <span className="w-2 h-2 rounded-full bg-warn" />
                  <span className="text-warn">HTTP FALLBACK POLLING</span>
                </>
              )}
            </div>
          </div>
        </div>
      </header>

      {/* Main Container Wrapper with Padding */}
      <div className="flex-grow w-full px-6 md:px-12 py-6 z-10 flex flex-col select-text">
        {/* KPI Summary Strip */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          {/* 전체 */}
          <div className="bg-card border border-line rounded-card shadow-card px-4 py-3.5 flex flex-col gap-1 min-h-[80px]">
            <span className="text-secondary text-xs font-semibold tracking-[0.05em] uppercase">전체</span>
            <span className="text-strong text-3xl font-mono tabular-nums font-semibold leading-none">
              {kpiCounts.total}
            </span>
          </div>

          {/* 실행 중 */}
          <div className="bg-card border border-line rounded-card shadow-card px-4 py-3.5 flex flex-col gap-1 min-h-[80px]">
            <span className="flex items-center gap-1.5 text-secondary text-xs font-semibold tracking-[0.05em] uppercase">
              <span className="w-1.5 h-1.5 rounded-full bg-ok" />
              실행 중
            </span>
            <span className="text-ok text-3xl font-mono tabular-nums font-semibold leading-none">
              {kpiCounts.running}
            </span>
          </div>

          {/* 중지·미생성 */}
          <div className="bg-card border border-line rounded-card shadow-card px-4 py-3.5 flex flex-col gap-1 min-h-[80px]">
            <span className="text-secondary text-xs font-semibold tracking-[0.05em] uppercase">중지·미생성</span>
            <span className="text-strong text-3xl font-mono tabular-nums font-semibold leading-none">
              {kpiCounts.stopped}
            </span>
          </div>

          {/* 오류 */}
          <div className="bg-card border border-line rounded-card shadow-card px-4 py-3.5 flex flex-col gap-1 min-h-[80px]">
            <span className="flex items-center gap-1.5 text-secondary text-xs font-semibold tracking-[0.05em] uppercase">
              <span className="w-1.5 h-1.5 rounded-full bg-danger" />
              오류
            </span>
            <span className="text-danger text-3xl font-mono tabular-nums font-semibold leading-none">
              {kpiCounts.error}
            </span>
          </div>
        </div>

        {/* API Connection Error Alert */}
        {error && !isWsConnected && (
          <div className="mb-8 p-4 bg-danger/5 border border-danger/30 rounded-card shadow-card flex items-start gap-3 text-danger text-sm z-10">
            <ShieldAlert className="w-5 h-5 text-danger shrink-0 mt-0.5" />
            <div>
              <p className="font-bold text-danger uppercase tracking-[0.05em] text-xs">통신 연결 오류</p>
              <p className="mt-1 text-ink font-light font-sans">
                백엔드 서버가 {BACKEND_URL} 에서 실행 중인지 확인해 주세요. (WSL 및 Docker 엔진 기동 점검)
              </p>
            </div>
          </div>
        )}

        {/* Main Table Layout */}
        <main className="flex-grow w-full overflow-hidden">
          <h2 className="text-sm font-semibold tracking-[0.05em] flex items-center gap-2 text-strong mb-6 uppercase">
            <Activity className="w-4 h-4 text-brand" />
            인프라 컨테이너 실시간 모니터링 테이블
          </h2>

          {isLoading && displayContainers.length === 0 ? (
            <div className="flex flex-col items-center justify-center p-20 bg-card border border-line rounded-card shadow-card">
              <RefreshCw className="w-8 h-8 text-brand animate-spin mb-4" />
              <p className="text-ink text-sm font-light">컨테이너 상태를 분석하는 중입니다...</p>
            </div>
          ) : (
            <div className="border border-line rounded-card bg-card overflow-x-auto shadow-card">
              <table className="w-full text-left border-collapse min-w-[1000px]">
                <thead className="sticky top-0 z-10">
                  <tr className="border-b border-line text-secondary font-semibold uppercase tracking-[0.05em] text-xs bg-subtle [&>th]:bg-subtle">
                    <th className="py-3 px-6 text-secondary text-xs font-semibold uppercase tracking-[0.05em]">상태</th>
                    <th className="py-3 px-6 text-secondary text-xs font-semibold uppercase tracking-[0.05em]">컨테이너 명칭</th>
                    <th className="py-3 px-6 text-secondary text-xs font-semibold uppercase tracking-[0.05em]">역할</th>
                    <th className="py-3 px-6 text-secondary text-xs font-semibold uppercase tracking-[0.05em]">포트 바인딩</th>
                    <th className="py-3 px-6 text-center text-secondary text-xs font-semibold uppercase tracking-[0.05em]">CPU 점유율</th>
                    <th className="py-3 px-6 text-center text-secondary text-xs font-semibold uppercase tracking-[0.05em]">메모리 사용량</th>
                    <th className="py-3 px-6 text-center text-secondary text-xs font-semibold uppercase tracking-[0.05em]">I/O 델타 (Read / Write)</th>
                    <th className="py-3 px-6 text-center text-secondary text-xs font-semibold uppercase tracking-[0.05em]">기능</th>
                    <th className="py-3 px-6 text-right text-secondary text-xs font-semibold uppercase tracking-[0.05em]">서비스 통제</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line text-xs md:text-sm">
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
                      <tr
                        key={container.id}
                        className={`transition-colors duration-150 ease-default group relative ${statusCfg.rowClass}`}
                      >
                        {/* Status Indicator */}
                        <td className="py-3 px-6">
                          <div className="flex items-center gap-2.5">
                            <span className={`w-2 h-2 rounded-full ${statusCfg.dotClass}`} />
                            <span className={`${statusCfg.textClass} text-xs md:text-sm uppercase tracking-[0.05em] font-bold`}>
                              {container.status}
                            </span>
                          </div>
                        </td>

                        {/* Display & Container Name */}
                        <td className="py-3 px-6">
                          <div className="flex items-center gap-3">
                            <div className="p-2 bg-subtle border border-line rounded-card shrink-0">
                              <Icon className="w-5 h-5 text-brand" />
                            </div>
                            <div>
                              <div className="font-sans font-semibold text-strong text-base uppercase">{displayName}</div>
                              <div className="text-secondary text-xs md:text-sm mt-0.5 font-mono tabular-nums font-light">{container.name}</div>
                              {container.public_url && (
                                <a
                                  href={container.public_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  title="운영(prod) 공개 주소"
                                  className="block text-brand text-xs md:text-sm mt-0.5 font-mono font-light underline hover:opacity-80 break-all"
                                >
                                  {container.public_url.replace(/^https?:\/\//, '')}
                                </a>
                              )}
                            </div>
                          </div>
                        </td>

                        {/* Role */}
                        <td className="py-3 px-6 text-ink font-light uppercase text-xs md:text-sm tracking-[0.05em]">
                          {container.role}
                        </td>

                        {/* Port Bindings */}
                        <td className="py-3 px-6 font-mono tabular-nums text-strong font-light text-xs md:text-sm">
                          {container.ports.length > 0
                            ? container.ports.join(', ')
                            : (container.expected_ports || []).join(', ') || 'Exposed internally'
                          }
                        </td>

                        {/* CPU Metric (Interactive) */}
                        <td className="py-3 px-6 text-center">
                          <button 
                            type="button"
                            disabled={container.status !== 'running'}
                            onClick={() => container.status === 'running' && openChartModal(container.id, 'cpu')}
                            className={`inline-flex flex-col items-center justify-center min-h-[44px] px-3 py-1.5 rounded-card border border-transparent select-none outline-hidden focus-visible:outline-2 focus-visible:outline-brand ${
                              container.status === 'running'
                                ? 'hover:border-line hover:bg-subtle cursor-pointer text-ink'
                                : 'text-secondary opacity-50 cursor-default'
                            }`}
                            title={container.status === 'running' ? '지난 1시간 CPU 사용 이력 보기' : ''}
                          >
                            <span className="flex items-center gap-1 font-mono tabular-nums font-bold text-xs md:text-sm">
                              <Cpu className="w-3.5 h-3.5 opacity-80" />
                              {container.status === 'running' ? `${metrics.cpu_pct.toFixed(1)}%` : '0.0%'}
                            </span>
                            <span className="text-[10px] md:text-xs text-secondary mt-0.5 uppercase tracking-[0.05em] font-bold">실시간 차트</span>
                          </button>
                        </td>

                        {/* Memory Metric (Interactive) */}
                        <td className="py-3 px-6 text-center">
                          <button 
                            type="button"
                            disabled={container.status !== 'running'}
                            onClick={() => container.status === 'running' && openChartModal(container.id, 'memory')}
                            className={`inline-flex flex-col items-center justify-center min-h-[44px] px-3 py-1.5 rounded-card border border-transparent select-none outline-hidden focus-visible:outline-2 focus-visible:outline-brand ${
                              container.status === 'running'
                                ? 'hover:border-line hover:bg-subtle cursor-pointer text-ink'
                                : 'text-secondary opacity-50 cursor-default'
                            }`}
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
                        <td className="py-3 px-6 text-center">
                          <button 
                            type="button"
                            disabled={container.status !== 'running'}
                            onClick={() => container.status === 'running' && openChartModal(container.id, 'io')}
                            className={`inline-flex flex-col items-center justify-center min-h-[44px] px-3 py-1.5 rounded-card border border-transparent select-none outline-hidden focus-visible:outline-2 focus-visible:outline-brand ${
                              container.status === 'running'
                                ? 'hover:border-line hover:bg-subtle cursor-pointer text-ink'
                                : 'text-secondary opacity-50 cursor-default'
                            }`}
                            title={container.status === 'running' ? '지난 1시간 I/O 이력 보기' : ''}
                          >
                            <span className="font-mono tabular-nums text-xs md:text-sm font-semibold space-y-0.5 block">
                              <span className="block text-warn">R: {container.status === 'running' ? formatBytes(metrics.io_read) : '0 B'}</span>
                              <span className="block text-danger">W: {container.status === 'running' ? formatBytes(metrics.io_write) : '0 B'}</span>
                            </span>
                            <span className="text-[10px] md:text-xs text-secondary mt-0.5 uppercase tracking-[0.05em] font-bold">실시간 차트</span>
                          </button>
                        </td>

                        {/* Terminal Log & Configuration */}
                        <td className="py-3 px-6 text-center">
                          <div className="flex items-center justify-center gap-1.5">
                            <button
                              type="button"
                              onClick={() => openLogModal(container.id)}
                              className="bg-card hover:bg-subtle text-ink border border-line rounded-card min-h-[44px] p-2 text-xs transition-all duration-150 ease-default"
                              title="실시간 터미널 로그 스트리밍 모달 열기"
                            >
                              <Terminal className="w-4 h-4" />
                            </button>

                            <button
                              type="button"
                              onClick={() => openConfigModal(container)}
                              className="bg-card hover:bg-subtle text-ink border border-line rounded-card min-h-[44px] p-2 text-xs transition-all duration-150 ease-default"
                              title="컨테이너 세부 설정 변경"
                            >
                              <Settings className="w-4 h-4" />
                            </button>
                          </div>
                        </td>

                        {/* Controller Actions */}
                        <td className="py-3 px-6 text-right">
                          <div className="inline-flex gap-1.5 items-center">
                            {isContainerLoading ? (
                              <div className="flex items-center gap-1.5 text-xs text-secondary font-bold tracking-[0.05em] uppercase py-2 px-3">
                                <RefreshCw className="w-3.5 h-3.5 animate-spin text-brand" />
                                <span>처리 중</span>
                              </div>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  onClick={() => handleAction(container.id, 'start')}
                                  disabled={actionMutation.isPending || container.status === 'running'}
                                  className="flex items-center gap-1.5 bg-card hover:bg-ok hover:text-white disabled:opacity-30 disabled:hover:bg-card disabled:hover:text-ok text-ok border border-ok rounded-card min-h-[44px] py-2 px-3 text-xs font-bold tracking-[0.05em] uppercase transition-all duration-150 ease-default"
                                  title="컨테이너 가동"
                                >
                                  <Play className="w-3 h-3" />
                                  Start
                                </button>

                                <button
                                  type="button"
                                  onClick={() => handleAction(container.id, 'stop')}
                                  disabled={actionMutation.isPending || container.status !== 'running'}
                                  className="flex items-center gap-1.5 bg-card hover:bg-danger hover:text-white disabled:opacity-30 disabled:hover:bg-card disabled:hover:text-danger text-danger border border-danger rounded-card min-h-[44px] py-2 px-3 text-xs font-bold tracking-[0.05em] uppercase transition-all duration-150 ease-default"
                                  title="컨테이너 정지"
                                >
                                  <Square className="w-3 h-3" />
                                  Stop
                                </button>

                                <button
                                  type="button"
                                  onClick={() => handleAction(container.id, 'restart')}
                                  disabled={actionMutation.isPending || container.status !== 'running'}
                                  className="bg-card hover:bg-subtle text-ink border border-line rounded-card min-h-[44px] p-2 text-xs transition-all duration-150 ease-default"
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
            </div>
          )}
        </main>
      </div>

      {isAdminSettingsOpen && (
        <div className="fixed inset-0 bg-strong/40 backdrop-blur-md flex items-center justify-center z-50 p-4 transition-all duration-300 select-text">
          <AdminSettingsPanel onClose={() => setIsAdminSettingsOpen(false)} />
        </div>
      )}

      {/* Live Log Terminal Modal */}
      {isLogModalOpen && logContainerId && (
        <div className="fixed inset-0 bg-strong/40 backdrop-blur-md flex items-center justify-center z-50 p-4 transition-all duration-300 select-text">
          <div
            aria-label="실시간 콘솔 로그"
            aria-modal="true"
            role="dialog"
            className="bg-card border border-line rounded-card w-full max-w-4xl p-6 shadow-modal flex flex-col h-[75vh] relative overflow-hidden"
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
                className="text-secondary hover:text-strong p-2 rounded-full hover:bg-elevated transition-all"
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
                <span className="w-1.5 h-1.5 rounded-full bg-ok animate-ping" />
                WS 스트리밍 활성화됨
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Performance History Chart Modal */}
      {isChartModalOpen && chartContainerId && (
        <div className="fixed inset-0 bg-strong/40 backdrop-blur-md flex items-center justify-center z-50 p-4 transition-all duration-300">
          <div
            aria-label="실시간 성능 차트"
            aria-modal="true"
            role="dialog"
            className="bg-card border border-line rounded-card w-full max-w-3xl p-6 shadow-modal flex flex-col relative overflow-hidden"
          >

            {/* Modal Header */}
            <div className="flex justify-between items-center pb-4 border-b border-line z-10 shrink-0">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-subtle border border-line rounded-card">
                  <Activity className="w-5 h-5 text-brand" />
                </div>
                <div>
                  <h3 className="font-semibold text-strong text-base uppercase tracking-[0.05em]">실시간 성능 롤링 차트 (1시간)</h3>
                  <p className="text-xs text-secondary mt-0.5 font-light font-mono">대상 컨테이너: {chartContainerId}</p>
                </div>
              </div>

              <button
                type="button"
                aria-label="닫기"
                autoFocus
                onClick={() => {
                  setIsChartModalOpen(false);
                  setWsMetricsPoints([]); // 이벤트 핸들러에서 직접 초기화하여 derived-state 경고 방지
                }}
                className="text-secondary hover:text-strong p-2 rounded-full hover:bg-elevated transition-all"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Metric Tab Selector */}
            <div className="flex border-b border-line mt-5 shrink-0 z-10">
              <button
                type="button"
                onClick={() => setChartMetricType('cpu')}
                className={`py-2 px-4 text-xs font-bold tracking-[0.05em] uppercase transition-all border-b-2 outline-hidden ${
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
                className={`py-2 px-4 text-xs font-bold tracking-[0.05em] uppercase transition-all border-b-2 outline-hidden ${
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
                className={`py-2 px-4 text-xs font-bold tracking-[0.05em] uppercase transition-all border-b-2 outline-hidden ${
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
                    <CartesianGrid strokeDasharray="3 3" stroke="#d8dee8" />
                    <XAxis
                      dataKey="timestamp"
                      tickFormatter={formatTimestamp}
                      stroke="#667085"
                      style={{ fontSize: 14, fontFamily: 'monospace' }} // Increased fontSize to 14 to resolve accessibility small text warning
                      dy={5}
                    />
                    <YAxis
                      stroke="#667085"
                      style={{ fontSize: 14, fontFamily: 'monospace' }} // Increased fontSize to 14 to resolve accessibility small text warning
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
                        backgroundColor: '#ffffff',
                        border: '1px solid #d8dee8',
                        borderRadius: '8px',
                        fontSize: 14, // Increased fontSize to 14
                        fontFamily: 'monospace',
                        color: '#172033'
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
                        stroke="#15803d"
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
                        stroke="#0f766e"
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
                          stroke="#b45309"
                          strokeWidth={1.5}
                          dot={false}
                          name="io_read"
                        />
                        <Line
                          type="monotone"
                          dataKey="io_write"
                          stroke="#b42318"
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
        <div className="fixed inset-0 bg-strong/40 backdrop-blur-md flex items-center justify-center z-50 p-4 transition-all duration-300">
          <div
            aria-label="컨테이너 설정 변경"
            aria-modal="true"
            role="dialog"
            className="bg-card border border-line rounded-card w-full max-w-lg p-6 shadow-modal relative overflow-hidden flex flex-col max-h-[90vh]"
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
                className="text-secondary hover:text-strong p-1.5 rounded-full hover:bg-elevated transition-all"
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
                  {inputPortsList.map((port, idx) => (
                    <div key={`port-${idx}-${port}`} className="flex gap-2 items-center">
                      <input
                        type="text"
                        value={port}
                        onChange={(e) => {
                          const next = [...inputPortsList];
                          next[idx] = e.target.value;
                          setInputPortsList(next);
                        }}
                        placeholder="e.g. 5432:5432"
                        className="bg-card border border-line focus:border-brand focus:ring-0 rounded-card min-h-[44px] px-4 py-2 text-xs text-strong outline-hidden focus-visible:outline-2 focus-visible:outline-brand flex-grow font-mono"
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
                  ))}
                  {inputPortsList.length === 0 && (
                    <p className="text-xs text-secondary font-light italic">포트 바인딩 설정이 없습니다.</p>
                  )}
                </div>
              </div>

              {/* Volumes section */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <h4 className="text-[10px] font-bold text-secondary uppercase tracking-[0.05em]">볼륨 마운트 (host:container:mode)</h4>
                  <button
                    type="button"
                    onClick={() => setInputVolumesList(prev => [...prev, ''])}
                    className="text-[10px] text-brand hover:underline font-bold uppercase tracking-[0.05em]"
                  >
                    + 추가
                  </button>
                </div>
                <div className="space-y-2">
                  {inputVolumesList.map((vol, idx) => (
                    <div key={`vol-${idx}-${vol}`} className="flex gap-2 items-center">
                      <input
                        type="text"
                        value={vol}
                        onChange={(e) => {
                          const next = [...inputVolumesList];
                          next[idx] = e.target.value;
                          setInputVolumesList(next);
                        }}
                        placeholder="e.g. ${KOR_TRAVEL_GEO_PGDATA:-/tmp/pgdata}:/var/lib/postgresql/data"
                        className="bg-card border border-line focus:border-brand focus:ring-0 rounded-card min-h-[44px] px-4 py-2 text-xs text-strong outline-hidden focus-visible:outline-2 focus-visible:outline-brand flex-grow font-mono"
                        aria-label={`볼륨 마운트 ${idx + 1}`} // Added aria-label for accessibility
                        required
                      />
                      <button
                        type="button"
                        onClick={() => setInputVolumesList(prev => prev.filter((_, i) => i !== idx))}
                        className="text-danger hover:text-danger/80 p-1.5"
                        aria-label={`볼륨 마운트 ${idx + 1} 삭제`} // Added aria-label for accessibility
                      >
                        <X className="w-4.5 h-4.5" />
                      </button>
                    </div>
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
                  {inputNetworksList.map((net, idx) => (
                    <div key={`net-${idx}-${net}`} className="flex gap-2 items-center">
                      <input
                        type="text"
                        value={net}
                        onChange={(e) => {
                          const next = [...inputNetworksList];
                          next[idx] = e.target.value;
                          setInputNetworksList(next);
                        }}
                        placeholder="e.g. default"
                        className="bg-card border border-line focus:border-brand focus:ring-0 rounded-card min-h-[44px] px-4 py-2 text-xs text-strong outline-hidden focus-visible:outline-2 focus-visible:outline-brand flex-grow font-mono"
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
                    {Object.entries(inputEnvDict).map(([key, val]) => (
                      <div key={key} className="flex flex-col gap-1.5">
                        <label className="text-xs text-secondary font-mono font-light" htmlFor={`env-input-${key}`}>
                          {key}
                        </label>
                        <input
                          id={`env-input-${key}`}
                          type="text"
                          value={val}
                          onChange={(e) => setInputEnvDict(prev => ({ ...prev, [key]: e.target.value }))}
                          className="bg-card border border-line focus:border-brand focus:ring-0 rounded-card min-h-[44px] px-4 py-2 text-xs text-strong outline-hidden focus-visible:outline-2 focus-visible:outline-brand w-full transition-all font-mono"
                          aria-label={`환경 변수 ${key}`} // Added aria-label for accessibility
                          required
                        />
                      </div>
                    ))}
                  </div>
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
                  className="bg-card hover:bg-danger hover:text-white disabled:opacity-40 text-danger border border-danger rounded-card min-h-[44px] py-3 text-xs font-bold tracking-[0.05em] uppercase transition-all duration-150 ease-default flex-1"
                >
                  {resetMutation.isPending ? '원복 중...' : '기본값 원복'}
                </button>

                <button
                  type="submit"
                  disabled={configMutation.isPending || resetMutation.isPending}
                  className="bg-brand hover:bg-brand-ink text-white disabled:opacity-50 rounded-card shadow-card min-h-[44px] py-3 text-xs font-bold tracking-[0.05em] uppercase transition-all duration-150 ease-default flex-1 flex items-center justify-center gap-2"
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
    </div>
  );
}
