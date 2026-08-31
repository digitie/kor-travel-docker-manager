'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Boxes, HardDrive, Network, HeartPulse, KeyRound, X, Hammer } from 'lucide-react';
import { ContainerInspect, apiJson, postJson } from '@/lib/api';

type TabId = 'overview' | 'mounts' | 'networks' | 'health' | 'env';

const TABS: Array<{ id: TabId; label: string; Icon: typeof Boxes }> = [
  { id: 'overview', label: '개요', Icon: Boxes },
  { id: 'mounts', label: '마운트', Icon: HardDrive },
  { id: 'networks', label: '네트워크', Icon: Network },
  { id: 'health', label: '상태 검사', Icon: HeartPulse },
  { id: 'env', label: '환경 변수', Icon: KeyRound },
];

// 개발 빌드에서만 노출한다. 운영 빌드에서는 Next.js가 이 분기를 번들에서 제거한다.
//
// ⚠️ 이것이 **유일한** 방어선이다. 서버는 이 호출을 production이라고 막지 않는다 —
// `assert_manager_mutation_allowed`는 환경 선언의 정합성만 검증하고 문자열을 돌려주며,
// `assert_c6c_mutation_allowed`는 대상 service가 C6c runtime(Map 4종·pinvi-api)과 겹치지
// 않으면 그냥 반환한다. 즉 db·storage·gra·cadv·prom·geo·conc target은 production에서도
// 통과한다. `npm run dev`로 띄운 프론트를 운영 백엔드에 붙이면 NODE_ENV는 development라
// 버튼이 보이고 실제로 실행된다. 서버측 차단은 후속 과제로 분리했다(T-044).
// 그래서 아래 실행 경로에 실제 영향 범위를 밝힌 확인 절차를 둔다.
const IS_DEV = process.env.NODE_ENV !== 'production';

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(5.5rem,auto)_minmax(0,1fr)] gap-3 py-1.5 border-b border-line/50 last:border-b-0">
      <dt className="text-secondary text-xs font-medium pt-0.5">{label}</dt>
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
  targetServices,
  onClose,
}: {
  containerId: string;
  containerLabel: string;
  targetId?: string | null;
  /** target의 depends_on까지 펼친 실제 재생성 대상. 영향 범위를 사용자에게 보여 준다. */
  targetServices?: string[] | null;
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

  // 열릴 때 한 번만 포커스를 옮긴다. `[onClose]`에 묶으면 부모가 WS broadcast(2초)마다
  // 리렌더할 때 effect가 재실행돼 사용자가 어디에 있든 2초마다 닫기 버튼으로 포커스를
  // 빼앗는다. 마우스 검증으로는 드러나지 않는 키보드/스크린리더 결함이다.
  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  // Esc 핸들러는 ref로 최신 onClose를 읽어 리스너를 한 번만 등록한다.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCloseRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const mounts = useMemo(() => data?.mounts ?? [], [data]);
  const networks = useMemo(
    () => Object.entries(data?.network?.networks ?? {}),
    [data]
  );
  const env = useMemo(() => data?.config?.env ?? [], [data]);
  const health = data?.state?.health ?? null;

  const runEnsure = async () => {
    if (!targetId) return;
    // ensure는 target 하나가 아니라 depends_on 폐포 전체를 재생성한다(`pinvi`면 현재 21개 서비스).
    // db가 포함되면 스키마·권한 복구 스크립트까지 실행된다. 버튼 라벨만 보면 이 범위가
    // 드러나지 않으므로, 실제 대상을 세어 보여 주고 확인을 받는다.
    const services = targetServices ?? [];
    const scope = services.length ? `\n\n대상 ${services.length}개: ${services.join(', ')}` : '';
    const dbWarning = services.some((s) => /postgres/i.test(s))
      ? '\n\n⚠️ PostgreSQL이 포함됩니다. 재생성과 함께 스키마·권한 복구 스크립트가 실행됩니다.'
      : '';
    if (
      !window.confirm(
        `${targetId} target을 docker compose up -d --build 로 재생성합니다.${scope}${dbWarning}\n\n계속할까요?`
      )
    ) {
      return;
    }
    setEnsureState('running');
    setEnsureMessage('');
    try {
      await postJson(`/api/v1/targets/${targetId}/ensure`, { build: true, recreate: false });
      setEnsureState('done');
      setEnsureMessage(`${targetId} target(${services.length || '?'}개 서비스)을 재생성했습니다.`);
    } catch (e) {
      setEnsureState('error');
      setEnsureMessage(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="ops-modal-backdrop">
      <div
        aria-label={`${containerLabel} 상세 정보`}
        aria-modal="true"
        role="dialog"
        className="ops-modal max-w-3xl relative flex flex-col"
      >
        <div className="ops-modal__header shrink-0">
          <h3 className="text-sm font-semibold flex items-center gap-2 text-strong">
            <Boxes className="w-4 h-4 text-brand" />
            <span className="break-all">{containerLabel}</span>
          </h3>
          <button
            ref={closeRef}
            type="button"
            aria-label="닫기"
            onClick={onClose}
            className="ops-icon-button"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 탭: 좁은 화면에서는 가로 스크롤되어 본문과 겹치지 않는다. */}
        <div
          role="tablist"
          aria-label="상세 정보 분류"
          className="grid grid-cols-2 sm:flex gap-1 px-6 pt-3 border-b border-line shrink-0"
        >
          {TABS.map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`container-detail-tab-${id}`}
              aria-selected={tab === id}
              // 패널은 DOM에 하나뿐이고 id가 활성 탭을 따라간다. 탭마다 자기 id를 가리키면
              // 비활성 4개는 존재하지 않는 IDREF가 된다. 고정 id 하나를 함께 가리킨다.
              aria-controls="container-detail-panel"
              tabIndex={tab === id ? 0 : -1}
              onKeyDown={(e) => {
                const idx = TABS.findIndex((t) => t.id === tab);
                const move =
                  e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
                if (!move && e.key !== 'Home' && e.key !== 'End') return;
                e.preventDefault();
                const next =
                  e.key === 'Home'
                    ? 0
                    : e.key === 'End'
                      ? TABS.length - 1
                      : (idx + move + TABS.length) % TABS.length;
                setTab(TABS[next].id);
                document.getElementById(`container-detail-tab-${TABS[next].id}`)?.focus();
              }}
              onClick={() => setTab(id)}
              className={`flex items-center justify-center gap-1.5 px-3 py-2 text-xs whitespace-nowrap border-b-2 transition-[color,background-color,border-color] ${
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
          id="container-detail-panel"
          aria-labelledby={`container-detail-tab-${tab}`}
          // 이 div가 스크롤 컨테이너인데 내부에 포커스 가능한 요소가 없다. tabIndex가 없으면
          // 키보드만 쓰는 사용자가 패널에 포커스를 못 줘서 긴 env 목록을 스크롤할 수 없다.
          tabIndex={0}
          className="flex-grow overflow-y-auto px-6 py-4 text-xs select-text scrollbar-thin focus-visible:outline-2 focus-visible:outline-brand"
        >
          {isLoading && <EmptyState>불러오는 중…</EmptyState>}
          {error && (
            <div className="py-6 text-center space-y-1">
              <p className="text-danger">상세 정보를 불러오지 못했습니다.</p>
              {/* 백엔드는 FastAPI detail JSON을 그대로 돌려준다. 원문을 그대로 찍으면
                  사용자에게 의미 없는 문자열이 보이므로 보조 설명으로만 둔다. */}
              <p className="text-secondary">
                컨테이너가 실행 중이 아니거나 Docker 데몬에 연결할 수 없습니다.
              </p>
            </div>
          )}

          {data && tab === 'overview' && (
            <dl>
              <Row label="이미지" value={data.image} />
              <Row label="컨테이너" value={data.name} />
              <Row label="Docker ID" value={data.docker_id?.slice(0, 20)} />
              <Row label="상태" value={data.state?.status} />
              <Row label="시작" value={data.state?.started_at} />
              {/* Docker는 running 중에도 ExitCode 0을 보고한다. 그대로 찍으면 '정상 종료'로 읽힌다. */}
              <Row
                label="종료 코드"
                value={data.state?.running === false ? data.state?.exit_code : undefined}
              />
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
              <div>
                <table className="w-full table-fixed text-left">
                  <thead className="text-secondary text-xs font-medium">
                    <tr>
                      <th className="py-1.5 pr-3 font-medium break-all">type</th>
                      <th className="py-1.5 pr-3 font-medium break-all">source</th>
                      <th className="py-1.5 pr-3 font-medium break-all">destination</th>
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
                    <p className="text-secondary text-xs font-medium">최근 검사</p>
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
                  비밀 성격의 key와 접속 문자열의 비밀번호 구간은 서버에서 가려서 전달합니다
                  (<code>&lt;redacted&gt;</code>). 다만 이는 이름·패턴 기반이라 완전하지 않을 수
                  있으니, 화면 공유나 스크린샷 전에 값을 확인하세요.
                </p>
                <ul className="space-y-1">
                  {env.map((pair, i) => {
                    const idx = pair.indexOf('=');
                    const key = idx === -1 ? pair : pair.slice(0, idx);
                    const value = idx === -1 ? '' : pair.slice(idx + 1);
                    return (
                      <li key={`${key}-${i}`} className="grid grid-cols-[minmax(5.5rem,auto)_minmax(0,1fr)] gap-3 border-b border-line/50 py-1">
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
              className="bg-card hover:bg-subtle text-ink border border-line rounded-card min-h-[36px] px-3 text-xs inline-flex items-center gap-1.5 transition-[color,background-color,border-color] disabled:text-secondary"
              title={`${targetId} target의 depends_on 폐포 전체를 docker compose up -d --build로 재생성한다`}
            >
              <Hammer className="w-3.5 h-3.5" />
              {ensureState === 'running'
                ? '실행 중…'
                : `${targetId} ensure --build${
                    targetServices?.length ? ` (${targetServices.length}개 서비스)` : ''
                  }`}
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
