'use client';

import React, { useState } from 'react';
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
  ShieldAlert 
} from 'lucide-react';

interface ContainerStatus {
  id: string;
  name: string;
  status: string;
  state: string;
  ports: string[];
}

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://127.0.0.1:8000';

export default function Dashboard() {
  const queryClient = useQueryClient();
  const [selectedContainerId, setSelectedContainerId] = useState<string | null>(null);
  const [logContent, setLogContent] = useState<string>('');
  const [isLoadingLogs, setIsLoadingLogs] = useState<boolean>(false);

  // Fetch all container statuses
  const { data: containers = [], isLoading, isRefetching, error } = useQuery<ContainerStatus[]>({
    queryKey: ['containers'],
    queryFn: async () => {
      const res = await fetch(`${BACKEND_URL}/api/containers`);
      if (!res.ok) {
        throw new Error('Failed to fetch containers status.');
      }
      return res.json();
    },
    refetchInterval: 5000, // Refetch every 5 seconds
  });

  // Action mutation (start/stop/restart)
  const actionMutation = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: string }) => {
      const res = await fetch(`${BACKEND_URL}/api/containers/${id}/action`, {
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

  // Fetch logs for a specific container
  const fetchLogs = async (id: string) => {
    setIsLoadingLogs(true);
    setSelectedContainerId(id);
    try {
      const res = await fetch(`${BACKEND_URL}/api/containers/${id}/logs`);
      if (!res.ok) throw new Error('Failed to fetch logs');
      const data = await res.json();
      setLogContent(data.logs || 'No logs available.');
    } catch (err: any) {
      setLogContent(`Error: ${err.message}`);
    } finally {
      setIsLoadingLogs(false);
    }
  };

  const handleAction = (id: string, action: string) => {
    actionMutation.mutate({ id, action });
  };

  // Helper to determine status color and animations
  const getStatusConfig = (status: string) => {
    const s = status.toLowerCase();
    if (s === 'running') {
      return {
        dotClass: 'bg-emerald-500 shadow-glow-success animate-pulse',
        textClass: 'text-emerald-400 font-semibold',
        bgClass: 'border-emerald-500/30 bg-emerald-950/10'
      };
    } else if (s === 'exited' || s === 'offline') {
      return {
        dotClass: 'bg-rose-500 shadow-glow-error',
        textClass: 'text-rose-400 font-semibold',
        bgClass: 'border-rose-500/20 bg-rose-950/10'
      };
    } else if (s.includes('starting') || s.includes('restarting')) {
      return {
        dotClass: 'bg-amber-500 animate-ping',
        textClass: 'text-amber-400 font-semibold',
        bgClass: 'border-amber-500/20 bg-amber-950/10'
      };
    } else {
      return {
        dotClass: 'bg-slate-500',
        textClass: 'text-slate-400 font-semibold',
        bgClass: 'border-slate-800 bg-slate-900/50'
      };
    }
  };

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col p-6 md:p-12 relative overflow-hidden">
      {/* Dynamic background decoration */}
      <div className="absolute top-[-20%] left-[-10%] w-[50%] h-[50%] bg-primary/10 rounded-full blur-[120px] pointer-events-none" />
      <div className="absolute bottom-[-20%] right-[-10%] w-[50%] h-[50%] bg-violet-600/5 rounded-full blur-[120px] pointer-events-none" />

      {/* Header */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-10 pb-6 border-b border-border z-10">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-foreground via-slate-200 to-primary/80">
            TripMate Infrastructure Manager
          </h1>
          <p className="text-slate-400 mt-2 text-sm max-w-xl">
            TripMate 통합 서비스 구동을 위한 PostgreSQL 및 RustFS Docker 컨테이너의 실시간 상태 대시보드입니다.
          </p>
        </div>
        
        <div className="flex items-center gap-3 bg-card border border-border px-4 py-2 rounded-xl text-xs text-slate-400">
          <Activity className={`w-4 h-4 ${isRefetching ? 'text-primary animate-spin' : 'text-slate-400'}`} />
          <span>5초마다 자동 갱신</span>
          {isRefetching && <span className="text-primary font-medium">동기화 중...</span>}
        </div>
      </header>

      {/* API Connection Error Alert */}
      {error && (
        <div className="mb-8 p-4 bg-rose-950/20 border border-rose-500/30 rounded-xl flex items-start gap-3 text-rose-300 text-sm z-10">
          <ShieldAlert className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold text-rose-200">백엔드 서버와 통신할 수 없습니다.</p>
            <p className="mt-1 opacity-80">
              `poetry run uvicorn src.tripmate_manager.main:app` 명령어를 통해 백엔드 서버를 8000포트에서 실행해 주세요.
            </p>
          </div>
        </div>
      )}

      {/* Main Grid Layout */}
      <main className="grid grid-cols-1 lg:grid-cols-3 gap-8 z-10 flex-grow items-start">
        {/* Left/Middle Column: Services Status List */}
        <div className="lg:col-span-2 flex flex-col gap-6">
          <h2 className="text-lg font-bold tracking-tight flex items-center gap-2 text-slate-300">
            <Activity className="w-5 h-5 text-primary" />
            인프라 컨테이너 상태
          </h2>

          {isLoading ? (
            <div className="flex flex-col items-center justify-center p-20 bg-card/40 border border-border rounded-2xl">
              <RefreshCw className="w-8 h-8 text-primary animate-spin mb-4" />
              <p className="text-slate-400 text-sm">컨테이너 상태를 불러오는 중입니다...</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {containers.map((container) => {
                const config = getStatusConfig(container.status);
                const isPG = container.id === 'postgresql';
                const Icon = isPG ? Database : FolderGit2;

                return (
                  <div 
                    key={container.id}
                    className={`border rounded-2xl p-6 transition-all duration-300 hover:translate-y-[-2px] hover:shadow-glow/20 flex flex-col justify-between h-[280px] ${config.bgClass} bg-card/30 backdrop-blur-sm`}
                  >
                    <div>
                      {/* Service Header */}
                      <div className="flex justify-between items-start">
                        <div className="flex items-center gap-3">
                          <div className="p-3 bg-slate-900 border border-slate-800 rounded-xl">
                            <Icon className="w-6 h-6 text-primary" />
                          </div>
                          <div>
                            <h3 className="font-bold text-slate-200 text-base">{container.id === 'postgresql' ? 'PostgreSQL (PostGIS)' : 'RustFS Store'}</h3>
                            <p className="text-slate-500 text-xs mt-0.5">{container.name}</p>
                          </div>
                        </div>
                        
                        <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-slate-950/80 border border-slate-800/80 text-xs">
                          <span className={`w-2 h-2 rounded-full ${config.dotClass}`} />
                          <span className={config.textClass}>{container.status.toUpperCase()}</span>
                        </div>
                      </div>

                      {/* Ports & Bindings info */}
                      <div className="mt-6 space-y-2 text-xs">
                        <div className="flex justify-between py-1.5 border-b border-border/40">
                          <span className="text-slate-500">포트 바인딩</span>
                          <span className="text-slate-300 font-mono">
                            {container.ports.length > 0 ? container.ports.join(', ') : 'Exposed internally only'}
                          </span>
                        </div>
                        <div className="flex justify-between py-1.5 border-b border-border/40">
                          <span className="text-slate-500">도커 이미지</span>
                          <span className="text-slate-400 font-mono truncate max-w-[150px]">
                            {isPG ? 'postgis/postgis:16-3.5-alpine' : 'rustfs/rustfs:latest'}
                          </span>
                        </div>
                      </div>
                    </div>

                    {/* Action controls */}
                    <div className="mt-6 flex gap-2 pt-4 border-t border-border/40">
                      <button
                        onClick={() => handleAction(container.id, 'start')}
                        disabled={actionMutation.isPending || container.status === 'running'}
                        className="flex-1 flex items-center justify-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:hover:bg-emerald-600 text-white rounded-xl py-2.5 px-3 text-xs font-semibold transition-all"
                        title="컨테이너 시작"
                      >
                        <Play className="w-3.5 h-3.5" />
                        Start
                      </button>

                      <button
                        onClick={() => handleAction(container.id, 'stop')}
                        disabled={actionMutation.isPending || container.status !== 'running'}
                        className="flex-1 flex items-center justify-center gap-2 bg-rose-600 hover:bg-rose-500 disabled:opacity-40 disabled:hover:bg-rose-600 text-white rounded-xl py-2.5 px-3 text-xs font-semibold transition-all"
                        title="컨테이너 정지"
                      >
                        <Square className="w-3.5 h-3.5" />
                        Stop
                      </button>

                      <button
                        onClick={() => handleAction(container.id, 'restart')}
                        disabled={actionMutation.isPending || container.status !== 'running'}
                        className="flex-none flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-300 border border-slate-700 rounded-xl p-2.5 text-xs font-semibold transition-all"
                        title="컨테이너 재시작"
                      >
                        <RotateCw className="w-4 h-4" />
                      </button>

                      <button
                        onClick={() => fetchLogs(container.id)}
                        className="flex-none flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded-xl p-2.5 text-xs font-semibold transition-all"
                        title="로그 보기"
                      >
                        <Terminal className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right Column: Terminal Logs Console */}
        <div className="flex flex-col gap-6">
          <h2 className="text-lg font-bold tracking-tight flex items-center justify-between text-slate-300">
            <span className="flex items-center gap-2">
              <Terminal className="w-5 h-5 text-primary" />
              실시간 콘솔 로그
            </span>
            {selectedContainerId && (
              <button 
                onClick={() => fetchLogs(selectedContainerId)}
                disabled={isLoadingLogs}
                className="text-xs text-primary hover:text-primary/80 flex items-center gap-1 font-semibold transition-colors"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isLoadingLogs ? 'animate-spin' : ''}`} />
                새로고침
              </button>
            )}
          </h2>

          <div className="bg-slate-950 border border-border rounded-2xl h-[450px] p-4 font-mono text-xs flex flex-col justify-between overflow-hidden shadow-2xl relative">
            <div className="absolute top-3 right-3 flex items-center gap-1.5 bg-slate-900 border border-slate-800 px-3 py-1 rounded-md text-[10px] text-slate-500 font-semibold uppercase tracking-wider">
              {selectedContainerId ? `${selectedContainerId} logs` : 'no container selected'}
            </div>

            {selectedContainerId ? (
              <pre className="flex-grow overflow-y-auto overflow-x-auto text-slate-300 leading-relaxed text-left h-full pr-2 mt-4 whitespace-pre-wrap select-text">
                {isLoadingLogs ? (
                  <div className="flex items-center justify-center h-full text-slate-500">
                    <RefreshCw className="w-4 h-4 animate-spin mr-2" />
                    로그를 불러오는 중...
                  </div>
                ) : (
                  logContent
                )}
              </pre>
            ) : (
              <div className="flex flex-col items-center justify-center flex-grow text-slate-600 gap-3">
                <Terminal className="w-10 h-10 text-slate-800" />
                <p className="text-center max-w-[200px] text-xs">
                  컨테이너 카드 하단의 로그 아이콘(<Terminal className="w-3 h-3 inline mx-0.5" />)을 클릭해 콘솔 로그를 확인하세요.
                </p>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
