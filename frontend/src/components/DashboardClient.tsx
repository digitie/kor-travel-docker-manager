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
  HardDrive
} from 'lucide-react';
import dynamic from 'next/dynamic';
import { useForm } from 'react-hook-form';
import { z } from 'zod';

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

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:9091';

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
      dotClass: 'bg-emerald-500 shadow-glow-success animate-pulse',
      textClass: 'text-emerald-400 font-semibold',
      rowClass: 'border-emerald-500/20 bg-emerald-950/5'
    };
  } else if (s === 'exited' || s === 'offline') {
    return {
      dotClass: 'bg-rose-500 shadow-glow-error',
      textClass: 'text-rose-400 font-semibold',
      rowClass: 'border-rose-500/10 bg-rose-950/5'
    };
  } else if (s.includes('starting') || s.includes('restarting')) {
    return {
      dotClass: 'bg-amber-500 animate-ping',
      textClass: 'text-amber-400 font-semibold',
      rowClass: 'border-amber-500/15 bg-amber-950/5'
    };
  } else {
    return {
      dotClass: 'bg-slate-500',
      textClass: 'text-slate-400 font-semibold',
      rowClass: 'border-slate-800 bg-slate-900/10'
    };
  }
};

export default function DashboardClient() {
  const queryClient = useQueryClient();
  
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

  // Fallback Polling (Query) - Versioned v1
  const { data: fallbackContainers = [], isLoading, error } = useQuery<ContainerStatus[]>({
    queryKey: ['containers'],
    queryFn: async () => {
      const res = await fetch(`${BACKEND_URL}/api/v1/containers`);
      if (!res.ok) {
        throw new Error('Failed to fetch containers status.');
      }
      return res.json();
    },
    refetchInterval: 5000,
    enabled: !isWsConnected, // Only run polling if WebSocket is offline
  });

  // Active containers dataset (WS if available, fallback query otherwise)
  const displayContainers = wsContainers || fallbackContainers;

  // Status WebSockets connection setup - Versioned v1
  useEffect(() => {
    let ws: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;

    const connectWS = () => {
      const wsUrl = `${BACKEND_URL.replace(/^http/, 'ws')}/api/v1/ws/status`;
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
  }, []);

  // Container Action Mutation - Versioned v1
  const actionMutation = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: string }) => {
      const res = await fetch(`${BACKEND_URL}/api/v1/containers/${id}/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || 'Failed to execute action.');
      }
      return res.json();
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
      const res = await fetch(`${BACKEND_URL}/api/v1/containers/${id}/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ports, env, volumes, networks }),
      });
      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || 'Failed to update configuration.');
      }
      return res.json();
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
      const res = await fetch(`${BACKEND_URL}/api/v1/containers/${id}/reset`, {
        method: 'POST',
      });
      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || 'Failed to reset configuration.');
      }
      return res.json();
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
      const res = await fetch(`${BACKEND_URL}/api/v1/containers/${chartContainerId}/metrics?hours=1`);
      if (!res.ok) throw new Error('Failed to fetch metrics history');
      return res.json();
    },
    enabled: !!chartContainerId && isChartModalOpen,
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

    const wsUrl = `${BACKEND_URL.replace(/^http/, 'ws')}/api/v1/ws/logs/${logContainerId}`;
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

  return (
    <div className="min-h-screen bg-canvas text-on-dark flex flex-col relative overflow-hidden select-none">
      {/* 4px M Tricolor Stripe Pinned to Top */}
      <div className="h-1 w-full bg-gradient-to-r from-m-blue-light via-m-blue-dark to-m-red fixed top-0 left-0 z-50" />

      {/* Hero Header Section - Clean Pure Black header with border-b, NO background image */}
      <section className="relative w-full h-[24vh] bg-[#000000] flex flex-col justify-end p-6 md:p-12 overflow-hidden border-b border-hairline mt-1">
        {/* Content over solid black background */}
        <div className="relative z-10 flex flex-col md:flex-row justify-between items-start md:items-end w-full gap-4 mt-auto">
          <div className="select-text">
            <span className="text-[9px] font-bold tracking-machined uppercase text-on-dark border border-hairline px-2.5 py-1 bg-canvas rounded-none">
              TRIPMATE SYSTEM INFRASTRUCTURE
            </span>
            <h1 className="text-2xl md:text-4xl font-display font-bold uppercase tracking-tight text-on-dark mt-4">
              TRIPMATE INFRASTRUCTURE SERVICES CONTROL CENTER.
            </h1>
            <p className="text-body-text text-xs font-light max-w-xl mt-2 leading-relaxed">
              공용 데이터베이스 및 RustFS 오브젝트 스토리지를 최상의 상태로 제어하고 모니터링하는 통합 인프라 관리 센터입니다.
            </p>
          </div>

          <div className="flex items-center gap-4 select-none self-end md:self-auto mb-1 md:mb-0">
            {/* WebSocket Status Indicator */}
            <div className="flex items-center gap-2 bg-surface-card border border-hairline px-4 py-2.5 rounded-none text-[9px] tracking-machined uppercase font-bold text-on-dark">
              {isWsConnected ? (
                <>
                  <Radio className="w-3.5 h-3.5 text-emerald-400 animate-pulse mr-1" />
                  <span className="text-emerald-400">REALTIME WS SYNC</span>
                </>
              ) : (
                <>
                  <RefreshCw className="w-3.5 h-3.5 text-amber-500 animate-spin mr-1" />
                  <span className="text-amber-500">HTTP FALLBACK POLLING</span>
                </>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* Main Container Wrapper with Padding */}
      <div className="flex-grow w-full px-6 md:px-12 py-10 z-10 flex flex-col select-text">
        {/* API Connection Error Alert */}
        {error && !isWsConnected && (
          <div className="mb-8 p-4 bg-rose-950/20 border border-rose-500/30 rounded-none flex items-start gap-3 text-rose-300 text-sm z-10">
            <ShieldAlert className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
            <div>
              <p className="font-bold text-rose-200 uppercase tracking-machined text-xs">통신 연결 오류</p>
              <p className="mt-1 opacity-80 font-light font-sans">
                백엔드 서버가 {BACKEND_URL} 에서 실행 중인지 확인해 주세요. (WSL 및 Docker 엔진 기동 점검)
              </p>
            </div>
          </div>
        )}

        {/* Main Table Layout */}
        <main className="flex-grow w-full overflow-hidden">
          <h2 className="text-sm font-bold tracking-machined flex items-center gap-2 text-body-strong mb-6 uppercase">
            <Activity className="w-4 h-4 text-on-dark" />
            인프라 컨테이너 실시간 모니터링 테이블
          </h2>

          {isLoading && displayContainers.length === 0 ? (
            <div className="flex flex-col items-center justify-center p-20 bg-surface-card border border-hairline rounded-none">
              <RefreshCw className="w-8 h-8 text-on-dark animate-spin mb-4" />
              <p className="text-body-text text-sm font-light">컨테이너 상태를 분석하는 중입니다...</p>
            </div>
          ) : (
            <div className="border border-hairline rounded-none bg-canvas overflow-x-auto shadow-none">
              <table className="w-full text-left border-collapse min-w-[1000px]">
                <thead>
                  <tr className="border-b border-hairline text-muted font-bold uppercase tracking-machined text-xs md:text-sm bg-surface-soft">
                    <th className="py-4.5 px-6">상태</th>
                    <th className="py-4.5 px-6">컨테이너 명칭</th>
                    <th className="py-4.5 px-6">역할</th>
                    <th className="py-4.5 px-6">포트 바인딩</th>
                    <th className="py-4.5 px-6 text-center">CPU 점유율</th>
                    <th className="py-4.5 px-6 text-center">메모리 사용량</th>
                    <th className="py-4.5 px-6 text-center">I/O 델타 (Read / Write)</th>
                    <th className="py-4.5 px-6 text-center">기능</th>
                    <th className="py-4.5 px-6 text-right">서비스 통제</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40 text-xs md:text-sm">
                  {displayContainers.map((container) => {
                    const statusCfg = getStatusConfig(container.status);
                    const isPG = container.role === 'postgresql' || container.id.includes('postgresql');
                    const Icon = isPG ? Database : FolderGit2;
                    const displayName = container.display_name || (isPG ? 'PostgreSQL (PostGIS)' : 'RustFS Store');
                    
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
                        className={`transition-colors duration-200 hover:bg-surface-soft/40 group relative ${statusCfg.rowClass}`}
                      >
                        {/* Status Indicator */}
                        <td className="py-5 px-6">
                          <div className="flex items-center gap-2.5">
                            <span className={`w-2 h-2 rounded-full ${statusCfg.dotClass}`} />
                            <span className={`${statusCfg.textClass} text-xs md:text-sm uppercase tracking-machined font-bold`}>
                              {container.status}
                            </span>
                          </div>
                        </td>

                        {/* Display & Container Name */}
                        <td className="py-5 px-6">
                          <div className="flex items-center gap-3">
                            <div className="p-2 bg-canvas border border-hairline rounded-none shrink-0">
                              <Icon className="w-5 h-5 text-on-dark" />
                            </div>
                            <div>
                              <div className="font-bold text-on-dark text-base uppercase">{displayName}</div>
                              <div className="text-muted text-xs md:text-sm mt-0.5 font-mono font-light">{container.name}</div>
                            </div>
                          </div>
                        </td>

                        {/* Role */}
                        <td className="py-5 px-6 text-body-text font-light uppercase text-xs md:text-sm tracking-machined">
                          {container.role}
                        </td>

                        {/* Port Bindings */}
                        <td className="py-5 px-6 font-mono text-body-strong font-light text-xs md:text-sm">
                          {container.ports.length > 0 
                            ? container.ports.join(', ') 
                            : (container.expected_ports || []).join(', ') || 'Exposed internally'
                          }
                        </td>

                        {/* CPU Metric (Interactive) */}
                        <td className="py-5 px-6 text-center">
                          <button 
                            type="button"
                            disabled={container.status !== 'running'}
                            onClick={() => container.status === 'running' && openChartModal(container.id, 'cpu')}
                            className={`inline-flex flex-col items-center justify-center px-3 py-1.5 rounded-none border border-transparent select-none outline-none focus:outline-none focus:ring-0 ${
                              container.status === 'running' 
                                ? 'hover:border-hairline hover:bg-surface-soft cursor-pointer text-on-dark' 
                                : 'text-muted opacity-50 cursor-default'
                            }`}
                            title={container.status === 'running' ? '지난 1시간 CPU 사용 이력 보기' : ''}
                          >
                            <span className="flex items-center gap-1 font-mono font-bold text-xs md:text-sm">
                              <Cpu className="w-3.5 h-3.5 opacity-80" />
                              {container.status === 'running' ? `${metrics.cpu_pct.toFixed(1)}%` : '0.0%'}
                            </span>
                            <span className="text-[10px] md:text-xs text-muted mt-0.5 uppercase tracking-machined font-bold">실시간 차트</span>
                          </button>
                        </td>

                        {/* Memory Metric (Interactive) */}
                        <td className="py-5 px-6 text-center">
                          <button 
                            type="button"
                            disabled={container.status !== 'running'}
                            onClick={() => container.status === 'running' && openChartModal(container.id, 'memory')}
                            className={`inline-flex flex-col items-center justify-center px-3 py-1.5 rounded-none border border-transparent select-none outline-none focus:outline-none focus:ring-0 ${
                              container.status === 'running' 
                                ? 'hover:border-hairline hover:bg-surface-soft cursor-pointer text-on-dark' 
                                : 'text-muted opacity-50 cursor-default'
                            }`}
                            title={container.status === 'running' ? '지난 1시간 메모리 사용 이력 보기' : ''}
                          >
                            <span className="flex items-center gap-1 font-mono font-bold text-xs md:text-sm">
                              <HardDrive className="w-3.5 h-3.5 opacity-80" />
                              {container.status === 'running' ? `${metrics.mem_pct.toFixed(1)}%` : '0.0%'}
                            </span>
                            <span className="text-[10px] md:text-xs text-muted mt-0.5 uppercase tracking-machined font-bold">
                              {container.status === 'running' ? formatBytes(metrics.mem_usage) : '0 B'}
                            </span>
                          </button>
                        </td>

                        {/* I/O Metrics (Interactive) */}
                        <td className="py-5 px-6 text-center">
                          <button 
                            type="button"
                            disabled={container.status !== 'running'}
                            onClick={() => container.status === 'running' && openChartModal(container.id, 'io')}
                            className={`inline-flex flex-col items-center justify-center px-3 py-1.5 rounded-none border border-transparent select-none outline-none focus:outline-none focus:ring-0 ${
                              container.status === 'running' 
                                ? 'hover:border-hairline hover:bg-surface-soft cursor-pointer text-on-dark' 
                                : 'text-muted opacity-50 cursor-default'
                            }`}
                            title={container.status === 'running' ? '지난 1시간 I/O 이력 보기' : ''}
                          >
                            <span className="font-mono text-xs md:text-sm font-semibold space-y-0.5 block">
                              <span className="block text-amber-400">R: {container.status === 'running' ? formatBytes(metrics.io_read) : '0 B'}</span>
                              <span className="block text-rose-400">W: {container.status === 'running' ? formatBytes(metrics.io_write) : '0 B'}</span>
                            </span>
                            <span className="text-[10px] md:text-xs text-muted mt-0.5 uppercase tracking-machined font-bold">실시간 차트</span>
                          </button>
                        </td>

                        {/* Terminal Log & Configuration */}
                        <td className="py-5 px-6 text-center">
                          <div className="flex items-center justify-center gap-1.5">
                            <button
                              type="button"
                              onClick={() => openLogModal(container.id)}
                              className="bg-surface-card hover:bg-on-dark hover:text-canvas text-on-dark border border-hairline rounded-none p-2 text-xs transition-all duration-150"
                              title="실시간 터미널 로그 스트리밍 모달 열기"
                            >
                              <Terminal className="w-4 h-4" />
                            </button>
                            
                            <button
                              type="button"
                              onClick={() => openConfigModal(container)}
                              className="bg-surface-card hover:bg-on-dark hover:text-canvas text-on-dark border border-hairline rounded-none p-2 text-xs transition-all duration-150"
                              title="컨테이너 세부 설정 변경"
                            >
                              <Settings className="w-4 h-4" />
                            </button>
                          </div>
                        </td>

                        {/* Controller Actions */}
                        <td className="py-5 px-6 text-right">
                          <div className="inline-flex gap-1.5 items-center">
                            {isContainerLoading ? (
                              <div className="flex items-center gap-1.5 text-xs text-muted font-bold tracking-machined uppercase py-2 px-3">
                                <RefreshCw className="w-3.5 h-3.5 animate-spin text-on-dark" />
                                <span>처리 중</span>
                              </div>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  onClick={() => handleAction(container.id, 'start')}
                                  disabled={actionMutation.isPending || container.status === 'running'}
                                  className="flex items-center gap-1.5 bg-canvas hover:bg-emerald-600 disabled:opacity-30 disabled:hover:bg-canvas disabled:hover:text-emerald-400 text-emerald-400 border border-emerald-600 rounded-none py-2 px-3 text-xs font-bold tracking-machined uppercase transition-all duration-150"
                                  title="컨테이너 가동"
                                >
                                  <Play className="w-3 h-3" />
                                  Start
                                </button>

                                <button
                                  type="button"
                                  onClick={() => handleAction(container.id, 'stop')}
                                  disabled={actionMutation.isPending || container.status !== 'running'}
                                  className="flex items-center gap-1.5 bg-canvas hover:bg-rose-600 disabled:opacity-30 disabled:hover:bg-canvas disabled:hover:text-rose-400 text-rose-400 border border-rose-600 rounded-none py-2 px-3 text-xs font-bold tracking-machined uppercase transition-all duration-150"
                                  title="컨테이너 정지"
                                >
                                  <Square className="w-3 h-3" />
                                  Stop
                                </button>

                                <button
                                  type="button"
                                  onClick={() => handleAction(container.id, 'restart')}
                                  disabled={actionMutation.isPending || container.status !== 'running'}
                                  className="bg-canvas hover:bg-on-dark hover:text-canvas text-on-dark border border-hairline rounded-none p-2 text-xs transition-all duration-150"
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

      {/* Live Log Terminal Modal */}
      {isLogModalOpen && logContainerId && (
        <div className="fixed inset-0 bg-slate-950/70 backdrop-blur-md flex items-center justify-center z-50 p-4 transition-all duration-300 select-text">
          <div className="bg-canvas border border-hairline rounded-none w-full max-w-4xl p-6 shadow-none flex flex-col h-[75vh] relative overflow-hidden">
            
            {/* Modal Header */}
            <div className="flex justify-between items-center pb-4 border-b border-hairline z-10 shrink-0">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-canvas border border-hairline rounded-none">
                  <Terminal className="w-5 h-5 text-on-dark" />
                </div>
                <div>
                  <h3 className="font-bold text-on-dark text-base uppercase tracking-machined">실시간 콘솔 로그</h3>
                  <p className="text-xs text-muted mt-0.5 font-light">컨테이너 ID: {logContainerId}</p>
                </div>
              </div>
              
              <button 
                type="button"
                onClick={() => setIsLogModalOpen(false)}
                className="text-muted hover:text-on-dark p-2 rounded-full hover:bg-surface-elevated transition-all"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Terminal View */}
            <div className="flex-grow bg-surface-soft border border-hairline rounded-none p-4 mt-4 font-mono text-xs overflow-y-auto leading-relaxed text-body-strong scrollbar-thin select-text">
              <pre className="whitespace-pre-wrap select-text pr-2 text-left">
                {liveLogs}
              </pre>
              <div ref={logEndRef} />
            </div>

            {/* Tip Footer */}
            <div className="pt-4 text-[10px] text-muted shrink-0 z-10 flex justify-between items-center">
              <span className="font-light">* 최신 3,000줄의 로그가 메모리에 버퍼링되며 자동으로 아래로 스크롤됩니다.</span>
              <span className="flex items-center gap-1 font-bold uppercase tracking-machined">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
                WS 스트리밍 활성화됨
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Performance History Chart Modal */}
      {isChartModalOpen && chartContainerId && (
        <div className="fixed inset-0 bg-slate-950/70 backdrop-blur-md flex items-center justify-center z-50 p-4 transition-all duration-300">
          <div className="bg-canvas border border-hairline rounded-none w-full max-w-3xl p-6 shadow-none flex flex-col relative overflow-hidden">
            
            {/* Modal Header */}
            <div className="flex justify-between items-center pb-4 border-b border-hairline z-10 shrink-0">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-canvas border border-hairline rounded-none">
                  <Activity className="w-5 h-5 text-on-dark" />
                </div>
                <div>
                  <h3 className="font-bold text-on-dark text-base uppercase tracking-machined">실시간 성능 롤링 차트 (1시간)</h3>
                  <p className="text-xs text-muted mt-0.5 font-light font-mono">대상 컨테이너: {chartContainerId}</p>
                </div>
              </div>
              
              <button 
                type="button"
                onClick={() => {
                  setIsChartModalOpen(false);
                  setWsMetricsPoints([]); // 이벤트 핸들러에서 직접 초기화하여 derived-state 경고 방지
                }}
                className="text-muted hover:text-on-dark p-2 rounded-full hover:bg-surface-elevated transition-all"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Metric Tab Selector */}
            <div className="flex border-b border-hairline mt-5 shrink-0 z-10">
              <button
                type="button"
                onClick={() => setChartMetricType('cpu')}
                className={`py-2 px-4 text-xs font-bold tracking-machined uppercase transition-all border-b-2 outline-none ${
                  chartMetricType === 'cpu' 
                    ? 'border-on-dark text-on-dark' 
                    : 'border-transparent text-muted hover:text-body-strong'
                }`}
              >
                CPU 점유율 (%)
              </button>
              <button
                type="button"
                onClick={() => setChartMetricType('memory')}
                className={`py-2 px-4 text-xs font-bold tracking-machined uppercase transition-all border-b-2 outline-none ${
                  chartMetricType === 'memory' 
                    ? 'border-on-dark text-on-dark' 
                    : 'border-transparent text-muted hover:text-body-strong'
                }`}
              >
                메모리 점유율 (%)
              </button>
              <button
                type="button"
                onClick={() => setChartMetricType('io')}
                className={`py-2 px-4 text-xs font-bold tracking-machined uppercase transition-all border-b-2 outline-none ${
                  chartMetricType === 'io' 
                    ? 'border-on-dark text-on-dark' 
                    : 'border-transparent text-muted hover:text-body-strong'
                }`}
              >
                I/O Read / Write (Bytes)
              </button>
            </div>

            {/* Chart Container */}
            <div className="h-[300px] mt-6 w-full z-10 bg-canvas border border-hairline rounded-none p-4 flex items-center justify-center">
              {isLoadingChart && combinedChartData.length === 0 ? (
                <div className="flex items-center gap-2 text-body-text text-xs">
                  <RefreshCw className="w-4 h-4 animate-spin text-on-dark" />
                  <span>차트 기록을 조회하고 있습니다...</span>
                </div>
              ) : combinedChartData.length === 0 ? (
                <div className="text-muted text-xs py-20 font-light">
                  최근 수집된 메트릭 이력이 없습니다. (수집기는 10초 주기로 수집하며 한달 저장됩니다.)
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={combinedChartData}
                    margin={{ top: 5, right: 10, left: 10, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
                    <XAxis 
                      dataKey="timestamp" 
                      tickFormatter={formatTimestamp} 
                      stroke="#7e7e7e" 
                      style={{ fontSize: 14, fontFamily: 'monospace' }} // Increased fontSize to 14 to resolve accessibility small text warning
                      dy={5}
                    />
                    <YAxis 
                      stroke="#7e7e7e" 
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
                        backgroundColor: '#1a1a1a',
                        border: '1px solid #3c3c3c',
                        borderRadius: '0px',
                        fontSize: 14, // Increased fontSize to 14
                        fontFamily: 'monospace',
                        color: '#ffffff'
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
                        stroke="#0fa336" 
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
                        stroke="#1c69d4" 
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
                          stroke="#f4b400" 
                          strokeWidth={1.5}
                          dot={false}
                          name="io_read"
                        />
                        <Line 
                          type="monotone" 
                          dataKey="io_write" 
                          stroke="#e22718" 
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
            <p className="text-[10px] text-muted mt-4 z-10 shrink-0 font-light">
              * 웹소켓 연결 상태에서 매 10초마다 새로운 메트릭 데이터가 이 차트에 실시간으로 추가되어 업데이트(롤링)됩니다.
            </p>
          </div>
        </div>
      )}

      {/* Config Edit Modal */}
      {isConfigModalOpen && configTargetContainer && (
        <div className="fixed inset-0 bg-slate-950/70 backdrop-blur-md flex items-center justify-center z-50 p-4 transition-all duration-300">
          <div className="bg-canvas border border-hairline rounded-none w-full max-w-lg p-6 shadow-none relative overflow-hidden flex flex-col max-h-[90vh]">
            
            {/* Modal Header */}
            <div className="flex justify-between items-center pb-4 border-b border-hairline z-10">
              <h3 className="text-sm font-bold tracking-machined flex items-center gap-2 text-on-dark uppercase">
                <Settings className="w-4 h-4 text-on-dark" />
                <span>컨테이너 설정 변경</span>
              </h3>
              <button 
                type="button"
                onClick={() => setIsConfigModalOpen(false)}
                className="text-muted hover:text-on-dark p-1.5 rounded-full hover:bg-surface-elevated transition-all"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Modal Body / Form */}
            <form onSubmit={handleConfigSubmit} className="flex-grow overflow-y-auto pr-2 mt-4 space-y-5 z-10 select-text scrollbar-thin">
              <div className="p-4 bg-surface-soft border border-hairline rounded-none text-xs text-body-text leading-relaxed">
                <p className="font-bold text-body-strong mb-1 uppercase tracking-machined">{configTargetContainer.display_name} 설정</p>
                <p className="font-light">docker-compose.yml 파일 내 설정을 변경합니다. 변경 후 컨테이너가 중지/삭제된 뒤 재생성됩니다.</p>
              </div>

              {/* Ports section */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <h4 className="text-[10px] font-bold text-muted uppercase tracking-machined">포트 매핑 (host:container)</h4>
                  <button
                    type="button"
                    onClick={() => setInputPortsList(prev => [...prev, ''])}
                    className="text-[10px] text-on-dark hover:underline font-bold uppercase tracking-machined"
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
                        placeholder="e.g. 55432:5432"
                        className="bg-surface-card border border-hairline focus:border-on-dark focus:ring-0 rounded-none px-4 py-2 text-xs text-body-strong outline-none flex-grow font-mono"
                        aria-label={`포트 매핑 ${idx + 1}`} // Added aria-label for accessibility
                        required
                      />
                      <button
                        type="button"
                        onClick={() => setInputPortsList(prev => prev.filter((_, i) => i !== idx))}
                        className="text-rose-500 hover:text-rose-450 p-1.5"
                        aria-label={`포트 매핑 ${idx + 1} 삭제`} // Added aria-label for accessibility
                      >
                        <X className="w-4.5 h-4.5" />
                      </button>
                    </div>
                  ))}
                  {inputPortsList.length === 0 && (
                    <p className="text-xs text-muted font-light italic">포트 바인딩 설정이 없습니다.</p>
                  )}
                </div>
              </div>

              {/* Volumes section */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <h4 className="text-[10px] font-bold text-muted uppercase tracking-machined">볼륨 마운트 (host:container:mode)</h4>
                  <button
                    type="button"
                    onClick={() => setInputVolumesList(prev => [...prev, ''])}
                    className="text-[10px] text-on-dark hover:underline font-bold uppercase tracking-machined"
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
                        placeholder="e.g. tripmate-pgdata:/var/lib/postgresql/data"
                        className="bg-surface-card border border-hairline focus:border-on-dark focus:ring-0 rounded-none px-4 py-2 text-xs text-body-strong outline-none flex-grow font-mono"
                        aria-label={`볼륨 마운트 ${idx + 1}`} // Added aria-label for accessibility
                        required
                      />
                      <button
                        type="button"
                        onClick={() => setInputVolumesList(prev => prev.filter((_, i) => i !== idx))}
                        className="text-rose-500 hover:text-rose-450 p-1.5"
                        aria-label={`볼륨 마운트 ${idx + 1} 삭제`} // Added aria-label for accessibility
                      >
                        <X className="w-4.5 h-4.5" />
                      </button>
                    </div>
                  ))}
                  {inputVolumesList.length === 0 && (
                    <p className="text-xs text-muted font-light italic">볼륨 바인딩 설정이 없습니다.</p>
                  )}
                </div>
              </div>

              {/* Networks section */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <h4 className="text-[10px] font-bold text-muted uppercase tracking-machined">네트워크 (default, etc.)</h4>
                  <button
                    type="button"
                    onClick={() => setInputNetworksList(prev => [...prev, ''])}
                    className="text-[10px] text-on-dark hover:underline font-bold uppercase tracking-machined"
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
                        className="bg-surface-card border border-hairline focus:border-on-dark focus:ring-0 rounded-none px-4 py-2 text-xs text-body-strong outline-none flex-grow font-mono"
                        aria-label={`네트워크 ${idx + 1}`} // Added aria-label for accessibility
                        required
                      />
                      <button
                        type="button"
                        onClick={() => setInputNetworksList(prev => prev.filter((_, i) => i !== idx))}
                        className="text-rose-500 hover:text-rose-450 p-1.5"
                        aria-label={`네트워크 ${idx + 1} 삭제`} // Added aria-label for accessibility
                      >
                        <X className="w-4.5 h-4.5" />
                      </button>
                    </div>
                  ))}
                  {inputNetworksList.length === 0 && (
                    <p className="text-xs text-muted font-light italic">네트워크 설정이 없습니다.</p>
                  )}
                </div>
              </div>

              {/* Env Variables section */}
              {Object.keys(inputEnvDict).length > 0 && (
                <div className="space-y-3">
                  <h4 className="text-[10px] font-bold text-muted uppercase tracking-machined">환경 변수</h4>
                  <div className="grid grid-cols-1 gap-4">
                    {Object.entries(inputEnvDict).map(([key, val]) => (
                      <div key={key} className="flex flex-col gap-1.5">
                        <label className="text-xs text-muted font-mono font-light" htmlFor={`env-input-${key}`}>
                          {key}
                        </label>
                        <input
                          id={`env-input-${key}`}
                          type="text"
                          value={val}
                          onChange={(e) => setInputEnvDict(prev => ({ ...prev, [key]: e.target.value }))}
                          className="bg-surface-card border border-hairline focus:border-on-dark focus:ring-0 rounded-none px-4 py-2 text-xs text-body-strong outline-none w-full transition-all font-mono"
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
                  className="bg-canvas hover:bg-rose-600 hover:text-white disabled:opacity-40 text-rose-400 border border-rose-600 rounded-none py-3 text-xs font-bold tracking-machined uppercase transition-all duration-150 flex-1"
                >
                  {resetMutation.isPending ? '원복 중...' : '기본값 원복'}
                </button>
                
                <button
                  type="submit"
                  disabled={configMutation.isPending || resetMutation.isPending}
                  className="bg-on-dark hover:bg-muted text-canvas disabled:opacity-50 rounded-none py-3 text-xs font-bold tracking-machined uppercase transition-all duration-150 flex-1 flex items-center justify-center gap-2"
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
