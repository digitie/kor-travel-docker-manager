'use client';

import React, { useState, useEffect } from 'react';
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
  X
} from 'lucide-react';

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
  config?: {
    ports: string[];
    env: Record<string, string>;
    volumes: string[];
    networks: string[];
  };
}

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://127.0.0.1:9100';

export default function Dashboard() {
  const queryClient = useQueryClient();
  const [selectedContainerId, setSelectedContainerId] = useState<string | null>(null);
  const [logContent, setLogContent] = useState<string>('');
  const [isLoadingLogs, setIsLoadingLogs] = useState<boolean>(false);

  // Configuration Modal States
  const [isConfigModalOpen, setIsConfigModalOpen] = useState<boolean>(false);
  const [configTargetContainer, setConfigTargetContainer] = useState<ContainerStatus | null>(null);
  const [inputPortsList, setInputPortsList] = useState<string[]>([]);
  const [inputEnvDict, setInputEnvDict] = useState<Record<string, string>>({});
  const [inputVolumesList, setInputVolumesList] = useState<string[]>([]);
  const [inputNetworksList, setInputNetworksList] = useState<string[]>([]);

  // Action mutation (start/stop/restart)
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

  // Config Update mutation
  const configMutation = useMutation({
    mutationFn: async ({ id, ports, env, volumes, networks }: { id: string; ports: string[]; env: Record<string, string>; volumes: string[]; networks: string[] }) => {
      const res = await fetch(`${BACKEND_URL}/api/containers/${id}/config`, {
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

  // Config Reset mutation
  const resetMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${BACKEND_URL}/api/containers/${id}/reset`, {
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
      networks: inputNetworksList
    });
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
              백엔드 서버가 {BACKEND_URL} 에서 실행 중인지 확인해 주세요. (WSL 환경 확인)
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
                const isPG = container.role === 'postgresql' || container.id.includes('postgresql');
                const Icon = isPG ? Database : FolderGit2;
                const title = container.display_name || (isPG ? 'PostgreSQL (PostGIS)' : 'RustFS Store');
                
                const isActionPending = actionMutation.isPending && actionMutation.variables?.id === container.id;
                const isConfigPending = configMutation.isPending && configMutation.variables?.id === container.id;
                const isResetPending = resetMutation.isPending && resetMutation.variables === container.id;
                const isContainerLoading = isActionPending || isConfigPending || isResetPending;

                return (
                  <div 
                    key={container.id}
                    className={`border rounded-2xl p-6 transition-all duration-300 hover:translate-y-[-2px] hover:shadow-glow/20 flex flex-col justify-between h-[280px] ${config.bgClass} bg-card/30 backdrop-blur-sm relative overflow-hidden`}
                  >
                    {isContainerLoading && (
                      <div className="absolute inset-0 bg-slate-950/70 backdrop-blur-[2px] flex flex-col items-center justify-center z-20 gap-3 transition-all duration-300">
                        <RefreshCw className="w-8 h-8 text-primary animate-spin" />
                        <span className="text-xs text-slate-300 font-semibold tracking-wide">
                          {isActionPending ? '컨테이너 제어 중...' : '컨테이너 재생성 중...'}
                        </span>
                      </div>
                    )}
                    <div>
                      {/* Service Header */}
                      <div className="flex justify-between items-start">
                        <div className="flex items-center gap-3">
                          <div className="p-3 bg-slate-900 border border-slate-800 rounded-xl">
                            <Icon className="w-6 h-6 text-primary" />
                          </div>
                          <div>
                            <h3 className="font-bold text-slate-200 text-base">{title}</h3>
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
                            {container.ports.length > 0 ? container.ports.join(', ') : (container.expected_ports || []).join(', ') || 'Exposed internally only'}
                          </span>
                        </div>
                        <div className="flex justify-between py-1.5 border-b border-border/40">
                          <span className="text-slate-500">도커 이미지</span>
                          <span className="text-slate-400 font-mono truncate max-w-[150px]">
                            {container.image || (isPG ? 'postgis/postgis:16-3.5' : 'rustfs/rustfs:latest')}
                          </span>
                        </div>
                        <div className="flex justify-between py-1.5 border-b border-border/40">
                          <span className="text-slate-500">접속 정보</span>
                          <span className="text-slate-400 font-mono truncate max-w-[180px]">
                            {container.connection || '-'}
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

                      <button
                        onClick={() => openConfigModal(container)}
                        className="flex-none flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded-xl p-2.5 text-xs font-semibold transition-all"
                        title="설정 변경"
                      >
                        <Settings className="w-4 h-4" />
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

      {/* Config Edit Glassmorphic Modal */}
      {isConfigModalOpen && configTargetContainer && (
        <div className="fixed inset-0 bg-slate-950/60 backdrop-blur-md flex items-center justify-center z-50 p-4 transition-all duration-300">
          <div className="bg-slate-900/95 border border-slate-800/80 rounded-2xl w-full max-w-lg p-6 shadow-2xl backdrop-blur-lg relative overflow-hidden flex flex-col max-h-[90vh]">
            <div className="absolute top-[-20%] left-[-10%] w-[40%] h-[40%] bg-primary/10 rounded-full blur-[80px] pointer-events-none" />
            
            {/* Modal Header */}
            <div className="flex justify-between items-center pb-4 border-b border-border/80 z-10">
              <h3 className="text-lg font-bold flex items-center gap-2 text-slate-200">
                <Settings className="w-5 h-5 text-primary animate-spin-slow" />
                <span>컨테이너 설정 변경</span>
              </h3>
              <button 
                onClick={() => setIsConfigModalOpen(false)}
                className="text-slate-400 hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-800/50 transition-all"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Modal Body / Form */}
            <form onSubmit={handleConfigSubmit} className="flex-grow overflow-y-auto pr-2 mt-4 space-y-5 z-10 select-text">
              <div className="p-4 bg-slate-950/40 border border-slate-800 rounded-xl text-xs text-slate-400 leading-relaxed">
                <p className="font-semibold text-slate-300 mb-1">{configTargetContainer.display_name} 설정</p>
                <p>docker-compose.yml 파일 내 설정을 변경합니다. 변경 후 컨테이너가 중지/삭제된 뒤 재생성됩니다.</p>
              </div>

              {/* Ports section */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider">포트 매핑 (host:container)</h4>
                  <button
                    type="button"
                    onClick={() => setInputPortsList(prev => [...prev, ''])}
                    className="text-[10px] text-primary hover:underline font-semibold"
                  >
                    + 추가
                  </button>
                </div>
                <div className="space-y-2">
                  {inputPortsList.map((port, idx) => (
                    <div key={idx} className="flex gap-2 items-center">
                      <input
                        type="text"
                        value={port}
                        onChange={(e) => {
                          const next = [...inputPortsList];
                          next[idx] = e.target.value;
                          setInputPortsList(next);
                        }}
                        placeholder="e.g. 55432:5432"
                        className="bg-slate-950 border border-slate-800 focus:border-primary focus:ring-1 focus:ring-primary rounded-xl px-4 py-2 text-xs text-slate-200 outline-none flex-grow font-mono"
                        required
                      />
                      <button
                        type="button"
                        onClick={() => setInputPortsList(prev => prev.filter((_, i) => i !== idx))}
                        className="text-rose-500 hover:text-rose-400 p-1.5"
                      >
                        <X className="w-4.5 h-4.5" />
                      </button>
                    </div>
                  ))}
                  {inputPortsList.length === 0 && (
                    <p className="text-xs text-slate-600">포트 바인딩 설정이 없습니다.</p>
                  )}
                </div>
              </div>

              {/* Volumes section */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider">볼륨 마운트 (host:container:mode)</h4>
                  <button
                    type="button"
                    onClick={() => setInputVolumesList(prev => [...prev, ''])}
                    className="text-[10px] text-primary hover:underline font-semibold"
                  >
                    + 추가
                  </button>
                </div>
                <div className="space-y-2">
                  {inputVolumesList.map((vol, idx) => (
                    <div key={idx} className="flex gap-2 items-center">
                      <input
                        type="text"
                        value={vol}
                        onChange={(e) => {
                          const next = [...inputVolumesList];
                          next[idx] = e.target.value;
                          setInputVolumesList(next);
                        }}
                        placeholder="e.g. tripmate-pgdata:/var/lib/postgresql/data"
                        className="bg-slate-950 border border-slate-800 focus:border-primary focus:ring-1 focus:ring-primary rounded-xl px-4 py-2 text-xs text-slate-200 outline-none flex-grow font-mono"
                        required
                      />
                      <button
                        type="button"
                        onClick={() => setInputVolumesList(prev => prev.filter((_, i) => i !== idx))}
                        className="text-rose-500 hover:text-rose-400 p-1.5"
                      >
                        <X className="w-4.5 h-4.5" />
                      </button>
                    </div>
                  ))}
                  {inputVolumesList.length === 0 && (
                    <p className="text-xs text-slate-600">볼륨 바인딩 설정이 없습니다.</p>
                  )}
                </div>
              </div>

              {/* Networks section */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider">네트워크 (default, etc.)</h4>
                  <button
                    type="button"
                    onClick={() => setInputNetworksList(prev => [...prev, ''])}
                    className="text-[10px] text-primary hover:underline font-semibold"
                  >
                    + 추가
                  </button>
                </div>
                <div className="space-y-2">
                  {inputNetworksList.map((net, idx) => (
                    <div key={idx} className="flex gap-2 items-center">
                      <input
                        type="text"
                        value={net}
                        onChange={(e) => {
                          const next = [...inputNetworksList];
                          next[idx] = e.target.value;
                          setInputNetworksList(next);
                        }}
                        placeholder="e.g. default"
                        className="bg-slate-950 border border-slate-800 focus:border-primary focus:ring-1 focus:ring-primary rounded-xl px-4 py-2 text-xs text-slate-200 outline-none flex-grow font-mono"
                        required
                      />
                      <button
                        type="button"
                        onClick={() => setInputNetworksList(prev => prev.filter((_, i) => i !== idx))}
                        className="text-rose-500 hover:text-rose-400 p-1.5"
                      >
                        <X className="w-4.5 h-4.5" />
                      </button>
                    </div>
                  ))}
                  {inputNetworksList.length === 0 && (
                    <p className="text-xs text-slate-600">네트워크 설정이 없습니다.</p>
                  )}
                </div>
              </div>

              {/* Env Variables section */}
              {Object.keys(inputEnvDict).length > 0 && (
                <div className="space-y-3">
                  <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider">컨테이너 환경 변수</h4>
                  <div className="grid grid-cols-1 gap-4">
                    {Object.entries(inputEnvDict).map(([key, val]) => (
                      <div key={key} className="flex flex-col gap-1.5">
                        <label className="text-xs text-slate-500 font-mono">{key}</label>
                        <input
                          type="text"
                          value={val}
                          onChange={(e) => setInputEnvDict(prev => ({ ...prev, [key]: e.target.value }))}
                          className="bg-slate-950 border border-slate-800 focus:border-primary focus:ring-1 focus:ring-primary rounded-xl px-4 py-2.5 text-sm text-slate-200 outline-none w-full transition-all font-mono"
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
                  className="bg-slate-850 hover:bg-slate-700 disabled:opacity-40 text-rose-400 border border-slate-800 rounded-xl py-3 text-sm font-semibold transition-all flex-1"
                >
                  {resetMutation.isPending ? '원복 중...' : '기본값 원복'}
                </button>
                
                <button
                  type="submit"
                  disabled={configMutation.isPending || resetMutation.isPending}
                  className="bg-primary hover:bg-primary/95 disabled:opacity-50 text-white rounded-xl py-3 text-sm font-semibold transition-all flex-1 shadow-glow flex items-center justify-center gap-2"
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
