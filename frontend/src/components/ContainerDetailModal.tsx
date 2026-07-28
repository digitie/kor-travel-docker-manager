'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Boxes, HardDrive, Network, HeartPulse, KeyRound, X, Hammer } from 'lucide-react';
import { ContainerInspect, apiJson, postJson } from '@/lib/api';

type TabId = 'overview' | 'mounts' | 'networks' | 'health' | 'env';

const TABS: Array<{ id: TabId; label: string; Icon: typeof Boxes }> = [
  { id: 'overview', label: '개요', Icon: Boxes },
  { id: 'mounts', label: 'Mounts', Icon: HardDrive },
  { id: 'networks', label: 'Networks', Icon: Network },
  { id: 'health', label: 'Healthcheck', Icon: HeartPulse },
  { id: 'env', label: 'Env', Icon: KeyRound },
];

// 개발 빌드에서만 노출한다. 운영 빌드(NODE_ENV=production)에서는 번들에서 분기 자체가 죽고,
// 서버도 production mutation 차단으로 거절하므로 이중으로 막힌다.
const IS_DEV = process.env.NODE_ENV !== 'production';

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-3 py-1.5 border-b border-line/50 last:border-b-0">
      <dt className="text-secondary text-[11px] uppercase tracking-[0.04em] pt-0.5">{label}</dt>
      <dd className="text-ink break-all">{value ?? <span className="text-secondary">—</span>}</dd>
    </div>
  );
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return <p className="text-secondary py-6 text-center">{children}</p>;
}

export default function ContainerDetailModal({
  containerId,
  containerLabel,
  targetId,
  onClose,
}: {
  containerId: string;
  containerLabel: string;
  targetId?: string | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<TabId>('overview');
  const [ensureState, setEnsureState] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [ensureMessage, setEnsureMessage] = useState<string>('');
  const closeRef = useRef<HTMLButtonElement>(null);

  const { data, isLoading, error } = useQuery<ContainerInspect>({
    queryKey: ['container-inspect', containerId],
    queryFn: () => apiJson<ContainerInspect>(`/api/v1/containers/${containerId}/inspect`),
    refetchInterval: 5000,
  });

  // Esc로 닫기 + 열릴 때 닫기 버튼으로 포커스 이동(모달 a11y 기존 패턴과 정렬).
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const mounts = useMemo(() => data?.mounts ?? [], [data]);
  const networks = useMemo(
    () => Object.entries(data?.network?.networks ?? {}),
    [data]
  );
  const env = useMemo(() => data?.config?.env ?? [], [data]);
  const health = data?.state?.health ?? null;

  const runEnsure = async () => {
    if (!targetId) return;
    setEnsureState('running');
    setEnsureMessage('');
    try {
      await postJson(`/api/v1/targets/${targetId}/ensure`, { build: true, recreate: false });
      setEnsureState('done');
      setEnsureMessage(`${targetId} target을 --build로 실행했습니다.`);
    } catch (e) {
      setEnsureState('error');
      setEnsureMessage(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="fixed inset-0 bg-strong/40 backdrop-blur-md flex items-center justify-center z-50 p-4">
      <div
        aria-label={`${containerLabel} 상세 정보`}
        aria-modal="true"
        role="dialog"
        className="bg-card border border-line rounded-card w-full max-w-3xl shadow-modal relative flex flex-col max-h-[90vh]"
      >
        <div className="flex justify-between items-center p-6 pb-4 border-b border-line shrink-0">
          <h3 className="text-sm font-semibold tracking-[0.05em] flex items-center gap-2 text-strong uppercase">
            <Boxes className="w-4 h-4 text-brand" />
            <span className="break-all">{containerLabel}</span>
          </h3>
          <button
            ref={closeRef}
            type="button"
            aria-label="닫기"
            onClick={onClose}
            className="text-secondary hover:text-strong p-1.5 rounded-full hover:bg-elevated transition-all shrink-0"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 탭: 좁은 화면에서는 가로 스크롤되어 본문과 겹치지 않는다. */}
        <div
          role="tablist"
          aria-label="상세 정보 분류"
          className="flex gap-1 px-6 pt-3 border-b border-line overflow-x-auto scrollbar-thin shrink-0"
        >
          {TABS.map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`container-detail-tab-${id}`}
              aria-selected={tab === id}
              aria-controls={`container-detail-panel-${id}`}
              onClick={() => setTab(id)}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs whitespace-nowrap border-b-2 transition-colors ${
                tab === id
                  ? 'border-brand text-strong font-semibold'
                  : 'border-transparent text-secondary hover:text-ink'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>

        <div
          role="tabpanel"
          id={`container-detail-panel-${tab}`}
          aria-labelledby={`container-detail-tab-${tab}`}
          className="flex-grow overflow-y-auto px-6 py-4 text-xs select-text scrollbar-thin"
        >
          {isLoading && <EmptyState>불러오는 중…</EmptyState>}
          {error && (
            <p className="py-6 text-center text-danger">
              상세 정보를 불러오지 못했습니다: {error instanceof Error ? error.message : String(error)}
            </p>
          )}

          {data && tab === 'overview' && (
            <dl>
              <Row label="이미지" value={data.image} />
              <Row label="컨테이너" value={data.name} />
              <Row label="Docker ID" value={data.docker_id?.slice(0, 20)} />
              <Row label="상태" value={data.state?.status} />
              <Row label="시작" value={data.state?.started_at} />
              <Row label="종료 코드" value={data.state?.exit_code ?? undefined} />
              <Row label="restart" value={data.host_config?.restart_policy?.Name} />
              <Row label="network mode" value={data.host_config?.network_mode} />
              <Row label="workdir" value={data.config?.working_dir} />
              <Row
                label="command"
                value={data.config?.cmd?.length ? data.config.cmd.join(' ') : undefined}
              />
            </dl>
          )}

          {data && tab === 'mounts' && (
            mounts.length === 0 ? (
              <EmptyState>마운트가 없습니다.</EmptyState>
            ) : (
              <div className="overflow-x-auto scrollbar-thin">
                <table className="w-full text-left min-w-[34rem]">
                  <thead className="text-secondary text-[11px] uppercase tracking-[0.04em]">
                    <tr>
                      <th className="py-1.5 pr-3 font-medium">type</th>
                      <th className="py-1.5 pr-3 font-medium">source</th>
                      <th className="py-1.5 pr-3 font-medium">destination</th>
                      <th className="py-1.5 font-medium">mode</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mounts.map((m, i) => (
                      <tr key={`${m.destination ?? 'mount'}-${i}`} className="border-t border-line/50">
                        <td className="py-1.5 pr-3">{m.type ?? '—'}</td>
                        <td className="py-1.5 pr-3 break-all">{m.name || m.source || '—'}</td>
                        <td className="py-1.5 pr-3 break-all">{m.destination ?? '—'}</td>
                        <td className="py-1.5">{m.rw === false ? 'ro' : m.mode || 'rw'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}

          {data && tab === 'networks' && (
            networks.length === 0 ? (
              <EmptyState>네트워크 정보가 없습니다(host 모드일 수 있습니다).</EmptyState>
            ) : (
              <div className="space-y-4">
                {networks.map(([name, n]) => (
                  <div key={name} className="border border-line rounded-card p-3">
                    <p className="text-strong font-semibold mb-2 break-all">{name}</p>
                    <dl>
                      <Row label="IP" value={n?.ip_address} />
                      <Row label="gateway" value={n?.gateway} />
                      <Row label="MAC" value={n?.mac_address} />
                      <Row
                        label="aliases"
                        value={n?.aliases?.length ? n.aliases.join(', ') : undefined}
                      />
                    </dl>
                  </div>
                ))}
              </div>
            )
          )}

          {data && tab === 'health' && (
            !health || !health.Status ? (
              <EmptyState>이 컨테이너에는 healthcheck가 정의되어 있지 않습니다.</EmptyState>
            ) : (
              <div className="space-y-3">
                <dl>
                  <Row label="상태" value={health.Status} />
                  <Row label="연속 실패" value={health.FailingStreak ?? 0} />
                </dl>
                {health.Log?.length ? (
                  <div className="space-y-2">
                    <p className="text-secondary text-[11px] uppercase tracking-[0.04em]">최근 검사</p>
                    {health.Log.slice(-5).reverse().map((entry, i) => (
                      <div key={`${entry.Start ?? i}`} className="border border-line rounded-card p-2">
                        <p className="text-secondary">
                          {entry.End ?? entry.Start ?? '—'} · exit {entry.ExitCode ?? '—'}
                        </p>
                        {entry.Output ? (
                          <pre className="mt-1 whitespace-pre-wrap break-all text-ink">
                            {entry.Output.trim().slice(0, 600)}
                          </pre>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            )
          )}

          {data && tab === 'env' && (
            env.length === 0 ? (
              <EmptyState>환경변수가 없습니다.</EmptyState>
            ) : (
              <div className="space-y-2">
                <p className="text-secondary leading-relaxed">
                  secret 성격 값은 서버에서 이미 가려진 상태로 전달됩니다. 원문은 노출되지 않습니다.
                </p>
                <ul className="space-y-1">
                  {env.map((pair, i) => {
                    const idx = pair.indexOf('=');
                    const key = idx === -1 ? pair : pair.slice(0, idx);
                    const value = idx === -1 ? '' : pair.slice(idx + 1);
                    return (
                      <li key={`${key}-${i}`} className="grid grid-cols-[minmax(9rem,auto)_1fr] gap-3 border-b border-line/50 py-1">
                        <span className="text-secondary break-all">{key}</span>
                        <span className="text-ink break-all">{value || '—'}</span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )
          )}
        </div>

        {IS_DEV && targetId ? (
          <div className="border-t border-line px-6 py-3 flex flex-wrap items-center gap-3 shrink-0">
            <button
              type="button"
              onClick={runEnsure}
              disabled={ensureState === 'running'}
              className="bg-card hover:bg-subtle text-ink border border-line rounded-card min-h-[36px] px-3 text-xs inline-flex items-center gap-1.5 transition-all disabled:opacity-50"
              title={`${targetId} target을 docker compose up -d --build로 실행`}
            >
              <Hammer className="w-3.5 h-3.5" />
              {ensureState === 'running' ? '실행 중…' : `${targetId} ensure --build`}
            </button>
            <span className="text-[11px] text-secondary">개발 빌드 전용</span>
            {ensureMessage ? (
              <span className={`text-[11px] ${ensureState === 'error' ? 'text-danger' : 'text-secondary'}`}>
                {ensureMessage}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
