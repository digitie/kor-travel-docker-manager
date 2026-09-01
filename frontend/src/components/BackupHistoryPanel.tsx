'use client';

import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, X } from 'lucide-react';
import {
  BackupJob,
  BackupListResponse,
  LatestBackupJobResponse,
  OffboxSyncStatusResponse,
  StandaloneBackupManifest,
  apiJson,
  postJson,
} from '@/lib/api';
import { HumanError, humanizeError } from '@/lib/errors';
import CopyableCommand from './CopyableCommand';
import InlineError from './InlineError';

const ROLE_OPTIONS: Array<{ value: StandaloneBackupManifest['role'] | 'all'; label: string }> = [
  { value: 'all', label: '전체' },
  { value: 'geo', label: 'geo' },
  { value: 'geo_dagster', label: 'geo_dagster' },
  { value: 'concierge', label: 'concierge' },
  { value: 'map_application', label: 'map_application' },
  { value: 'map_dagster', label: 'map_dagster' },
  { value: 'pinvi', label: 'pinvi' },
];

// scripts/run-standalone-backup.sh가 확정한 cron 주기(하루 1회). 나머지 role은 그
// wrapper의 대상이 아니므로 배지를 달지 않는다 — 없는 기대치로 경고를 만들지 않는다.
const EXPECTED_INTERVAL_HOURS: Partial<Record<StandaloneBackupManifest['role'], number>> = {
  geo_dagster: 24,
  concierge: 24,
  pinvi: 24,
};
const FRESHNESS_WARN_MULTIPLIER = 1.25;

const BACKUP_TIMEOUT_SECONDS = 14_400;

/** 누르기 전에 알아야 할 유일한 숫자. role마다 규모가 달라 문구도 달라야 한다 —
 * pinvi 백업을 확인하는데 geo 경고가 뜨면 그 경고를 읽지 않게 된다. */
function durationWarning(role: StandaloneBackupManifest['role']): string {
  const tail = '브라우저를 닫아도 진행됩니다. 상한은 4시간입니다.';
  if (role === 'geo') {
    return `geo는 수 시간이 걸릴 수 있습니다(실측 879초~22분). ${tail}`;
  }
  return `${role} 백업을 시작합니다. ${tail}`;
}

function formatElapsed(startedAtUnix: number, nowUnix: number): string {
  const seconds = Math.max(0, nowUnix - startedAtUnix);
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return `${seconds}초`;
  const hours = Math.floor(minutes / 60);
  return hours < 1 ? `${minutes}분` : `${hours}시간 ${minutes % 60}분`;
}

function formatBytes(byteSize: number): string {
  if (byteSize < 1024) return `${byteSize} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = byteSize / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function formatTimestamp(createdAtUnix: number): string {
  return new Date(createdAtUnix * 1000).toLocaleString('ko-KR');
}

export default function BackupHistoryPanel({ onClose }: { onClose: () => void }) {
  const [role, setRole] = useState<StandaloneBackupManifest['role'] | 'all'>('all');
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobError, setJobError] = useState<HumanError | null>(null);
  // 경과 시간은 렌더 시점에 계산되므로, 무언가 주기적으로 리렌더하지 않으면 4시간짜리
  // 작업이 "3초 경과"에 멈춰 있다. 살아 있는지 알려 주는 유일한 숫자가 거짓이 된다.
  const [nowUnix, setNowUnix] = useState(() => Math.floor(Date.now() / 1000));
  const dialogRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data, isLoading, isFetching, error, refetch } = useQuery<BackupListResponse>({
    queryKey: ['backups', role],
    queryFn: () =>
      apiJson<BackupListResponse>(
        role === 'all' ? '/api/v1/backups' : `/api/v1/backups?role=${role}`
      ),
    // 400/409류 영구 에러도 재시도하면 isLoading이 ~7초 유지돼 "로딩 중"으로
    // 오해하게 만든다 — DashboardClient의 auth-me 쿼리와 같은 이유로 끈다.
    retry: false,
  });

  // 신선도 배지는 **필터되지 않은** 목록으로 계산한다. 필터된 결과로 계산하면 role
  // 하나를 고르는 순간 나머지 role이 전부 "백업 없음"으로 빨갛게 표시된다 — 어젯밤
  // cron이 정상적으로 받아 둔 백업을 없다고 말하는 셈이고, 그러면 운영자는 빨간 백업
  // 경고를 노이즈로 학습한다.
  const { data: allBackups } = useQuery<BackupListResponse>({
    queryKey: ['backups', 'all'],
    queryFn: () => apiJson<BackupListResponse>('/api/v1/backups'),
    retry: false,
  });

  // 트리거는 CLI 전용(`ktdctl offbox-sync run`)이다 — 이 쿼리는 마지막 결과만 읽는다.
  const { data: offboxSync } = useQuery<OffboxSyncStatusResponse>({
    queryKey: ['backups', 'offbox-sync-status'],
    queryFn: () => apiJson<OffboxSyncStatusResponse>('/api/v1/backups/offbox-sync-status'),
    retry: false,
  });

  // role을 바꾸면 이전 role의 job을 따라다니지 않는다.
  useEffect(() => {
    setJobId(null);
    setJobError(null);
  }, [role]);

  // 새로고침으로 job id를 잃어도 진행 중인 작업에 다시 붙는다.
  const { data: latestJob } = useQuery<LatestBackupJobResponse>({
    queryKey: ['backup-job-latest', role],
    queryFn: () => apiJson<LatestBackupJobResponse>(`/api/v1/backups/${role}/jobs`),
    enabled: role !== 'all',
    retry: false,
  });

  useEffect(() => {
    // `running`만 다시 붙이면 **끝난 작업이 화면에서 사라진다.** 이 패널은 "브라우저를
    // 닫아도 진행됩니다"라고 말하므로 운영자는 실제로 닫는다. 돌아왔을 때 실패한
    // 백업이 아무 흔적도 남기지 않으면 시작조차 안 한 것과 구별되지 않는다.
    if (!jobId && latestJob?.job) setJobId(latestJob.job.job_id);
  }, [jobId, latestJob]);

  const { data: job, error: jobQueryError } = useQuery<BackupJob>({
    queryKey: ['backup-job', role, jobId],
    queryFn: () => apiJson<BackupJob>(`/api/v1/backups/${role}/jobs/${jobId}`),
    enabled: role !== 'all' && jobId !== null,
    // 진행 중일 때만 폴링한다. 끝난 job을 계속 두드릴 이유가 없다.
    refetchInterval: (query) => (query.state.data?.state === 'running' ? 5000 : false),
    retry: false,
  });

  // 진행 중일 때만 1초 시계를 돌린다.
  useEffect(() => {
    if (job?.state !== 'running') return;
    const timer = window.setInterval(
      () => setNowUnix(Math.floor(Date.now() / 1000)),
      1000
    );
    return () => window.clearInterval(timer);
  }, [job?.state]);

  const finishedJobId = job && job.state !== 'running' ? job.job_id : null;
  useEffect(() => {
    // 끝나면(성공이든 실패든) 목록을 다시 읽는다 — 실제로 남은 것은 manifest가 말한다.
    if (finishedJobId) void queryClient.invalidateQueries({ queryKey: ['backups'] });
  }, [finishedJobId, queryClient]);

  const createBackup = useMutation({
    mutationFn: () =>
      postJson<BackupJob>(`/api/v1/backups/${role}`, {
        timeout_seconds: BACKUP_TIMEOUT_SECONDS,
      }),
    onSuccess: (started) => {
      setJobError(null);
      setJobId(started.job_id);
    },
    onError: (error) => setJobError(humanizeError(error, '백업 생성')),
  });

  useEffect(() => {
    dialogRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const backups = data?.backups ?? [];
  const everyBackup = allBackups?.backups ?? [];
  const running = job?.state === 'running';

  // role별 최신 백업 시각. 기대 주기가 있는 role만 신선도를 판정한다.
  const freshness = ROLE_OPTIONS.filter((option) => option.value !== 'all')
    .map((option) => {
      const value = option.value as StandaloneBackupManifest['role'];
      const expected = EXPECTED_INTERVAL_HOURS[value];
      if (expected === undefined) return null;
      const newest = everyBackup
        .filter((backup) => backup.role === value)
        .reduce<number | null>(
          (latest, backup) => Math.max(latest ?? 0, backup.created_at_unix),
          null
        );
      // 목록을 아직 못 읽었으면 "없다"가 아니라 판정하지 않는다.
      if (allBackups === undefined) return null;
      if (newest === null) return { role: value, hours: null, stale: true };
      const hours = (Date.now() / 1000 - newest) / 3600;
      return { role: value, hours, stale: hours > expected * FRESHNESS_WARN_MULTIPLIER };
    })
    .filter((row): row is { role: StandaloneBackupManifest['role']; hours: number | null; stale: boolean } =>
      row !== null
    );

  return (
    <div
      aria-labelledby="backup-history-title"
      aria-modal="true"
      className="ops-modal max-w-5xl flex flex-col focus-visible:outline-0"
      ref={dialogRef}
      role="dialog"
      tabIndex={-1}
    >
      <div className="ops-modal__header">
        <div>
          <p className="text-xs text-secondary font-semibold">
            백업 이력
          </p>
          <h2 className="text-lg font-semibold text-strong mt-1" id="backup-history-title">
            DB 백업
          </h2>
          <p className="text-xs text-secondary mt-1">
            생성은 이 화면에서 할 수 있습니다. 정리(GC)와 복원은 CLI 전용이며,
            <strong> 복원은 아직 구현돼 있지 않습니다</strong> — 백업이 있다는 것과
            복원할 수 있다는 것은 다릅니다. 아래 명령으로 그 차이를 미리 확인하세요.
          </p>
        </div>
        <button
          className="ops-icon-button"
          onClick={onClose}
          type="button"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="overflow-y-auto p-6">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div className="flex flex-wrap gap-2">
            {ROLE_OPTIONS.map((option) => (
              <button
                className={`inline-flex items-center gap-2 min-h-[36px] rounded-card px-3 text-xs font-semibold border ${
                  role === option.value
                    ? 'ops-button ops-button--primary'
                    : 'ops-button'
                }`}
                key={option.value}
                onClick={() => setRole(option.value)}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <button
              className="ops-button"
              disabled={role === 'all' || running || createBackup.isPending}
              onClick={() => {
                if (role === 'all') return;
                if (!confirm(durationWarning(role))) return;
                createBackup.mutate();
              }}
              title={role === 'all' ? '백업할 role을 하나 선택하세요' : undefined}
              type="button"
            >
              백업 생성
            </button>
            <button
              className="ops-button"
              disabled={isFetching}
              onClick={() => void refetch()}
              type="button"
            >
              <RefreshCw className={`w-4 h-4 ${isFetching ? 'animate-spin' : ''}`} />
              새로고침
            </button>
          </div>
        </div>

        {role !== 'all' ? (
          // 목록에 보인다고 복원할 수 있는 것이 아니다 — dump가 잘렸거나 digest가
          // 어긋났거나 live schema가 백업 시점과 달라도 이 표는 똑같이 보인다.
          <div className="mb-4">
            <CopyableCommand
              command={`sudo -n backend/.venv/bin/ktdctl db-backup restore-plan ${role}`}
              hint="이 백업으로 복원하면 무슨 일이 일어나는지 계산합니다(읽기 전용)."
            />
          </div>
        ) : null}

        {role === 'all' ? (
          // 이유를 hover title에만 두면 터치 기기와 마우스를 안 올리는 사람에게는
          // 기능이 그냥 고장 난 것으로 보인다.
          <p className="text-xs text-secondary mb-3">
            백업을 생성하려면 위에서 role을 하나 고르세요. 전체 보기에서는 진행 중인
            작업도 보이지 않습니다.
          </p>
        ) : null}

        {freshness.length > 0 ? (
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs mb-4">
            {freshness.map((row) => (
              <li className={row.stale ? 'text-danger' : 'text-secondary'} key={row.role}>
                <span className="font-mono">{row.role}</span>{' '}
                {row.hours === null
                  ? '백업 없음'
                  : `마지막 백업 ${Math.floor(row.hours)}시간 전`}
              </li>
            ))}
          </ul>
        ) : null}

        {/* 로컬 백업만으로는 호스트 디스크 유실에서 살아남지 못한다 — off-box
            동기화(GM-08) 상태를 같은 화면에서 보여야 "백업이 있다"는 착각이 안 생긴다.
            트리거는 CLI 전용이라 여기서는 마지막 결과만 읽는다. "설정 안 함"과
            "설정했지만 방치"를 구분해야 후자가 전자로 오해돼 방치되지 않는다. */}
        <p
          className={`text-xs mb-1 ${
            offboxSync?.status && !offboxSync.status.all_verified ? 'text-danger' : 'text-secondary'
          }`}
        >
          off-box 동기화:{' '}
          {offboxSync?.status
            ? `${offboxSync.status.destination_host} · ${
                offboxSync.status.all_verified ? '검증됨' : '일부 실패'
              } · ${formatTimestamp(offboxSync.status.started_at_unix)}`
            : offboxSync?.configured
              ? '설정됐지만 아직 실행한 적이 없습니다 (ktdctl offbox-sync run — 주기 자동화는 별도 설정 필요)'
              : '설정되지 않음 (선택 기능, KTDM_OFFBOX_HOST 등 env 필요)'}
        </p>
        {offboxSync?.status && !offboxSync.status.all_verified ? (
          <p className="text-xs text-danger mb-4">
            실패한 대상:{' '}
            {offboxSync.status.targets
              .filter((target) => !target.verified)
              .map((target) => target.label)
              .join(', ')}
          </p>
        ) : (
          <div className="mb-4" />
        )}

        {job ? (
          <div
            aria-live="polite"
            className={`rounded-card border p-3 mb-4 text-xs ${
              job.state === 'failed' ? 'border-danger' : 'border-line'
            }`}
          >
            {job.state === 'running' ? (
              <p className="text-strong">
                {job.key} 백업이 진행 중입니다 ·{' '}
                {formatElapsed(job.started_at_unix, nowUnix)} 경과. 이 화면을 닫아도
                계속됩니다.
              </p>
            ) : job.state === 'succeeded' ? (
              <p className="text-strong">
                {job.key} 백업이 끝났습니다
                {job.result ? ` — ${job.result.backup_filename}` : ''}.
              </p>
            ) : (
              <>
                <p className="text-danger font-semibold">
                  {job.key} 백업이 실패했습니다. 이 백업은 만들어지지 않았습니다.
                </p>
                {/* 원문은 영어 예외 문자열이라 그대로가 답은 아니지만, 운영자가 이슈에
                    붙여넣을 값이므로 버리지 않고 그대로 남긴다. */}
                <p className="text-secondary mt-1 break-all font-mono">{job.error}</p>
              </>
            )}
          </div>
        ) : null}

        {jobQueryError ? (
          <div className="mb-4">
            {/* 관리도구가 재기동되면 job 기록은 사라진다(프로세스 로컬). 그때 계속
                "진행 중"이라고 우기면 안 된다 — 기록을 잃었다고 말하고, 실제로 남은
                것은 아래 목록이 정본이라고 알려 준다. */}
            <InlineError error={humanizeError(jobQueryError, '백업 진행 상태 확인')} />
          </div>
        ) : null}

        {jobError ? (
          <div className="mb-4">
            <InlineError error={jobError} />
          </div>
        ) : null}

        {isLoading ? (
          <p className="text-sm text-secondary">백업 이력을 불러오는 중입니다.</p>
        ) : error ? (
          <p className="text-sm text-danger">
            {error instanceof Error ? error.message : String(error)}
          </p>
        ) : backups.length === 0 ? (
          <p className="text-sm text-secondary text-center py-12">저장된 백업이 없습니다.</p>
        ) : (
          <div className="border-t border-line pt-2">
            <table className="ops-archive-table w-full table-fixed text-sm">
              <thead className="bg-subtle text-xs text-secondary">
                <tr>
                  <th className="text-left py-2 px-3 font-semibold break-all">생성 시각</th>
                  <th className="text-left py-2 px-3 font-semibold break-all">역할</th>
                  <th className="text-left py-2 px-3 font-semibold break-all">크기</th>
                  <th className="text-left py-2 px-3 font-semibold break-all">alembic</th>
                  <th className="text-left py-2 px-3 font-semibold break-all">SHA-256</th>
                  <th className="text-left py-2 px-3 font-semibold break-all">파일명</th>
                </tr>
              </thead>
              <tbody>
                {backups.map((backup) => (
                  <tr
                    className="border-t border-line"
                    key={`${backup.role}-${backup.backup_filename}`}
                  >
                    <td data-label="생성 시각" className="py-2 px-3 text-ink break-all">
                      {formatTimestamp(backup.created_at_unix)}
                    </td>
                    <td data-label="역할" className="py-2 px-3 text-ink font-mono">{backup.role}</td>
                    <td data-label="크기" className="py-2 px-3 text-ink break-all">
                      {formatBytes(backup.byte_size)}
                    </td>
                    <td data-label="alembic" className="py-2 px-3 text-ink font-mono break-all">
                      {backup.alembic_head ?? '—'}
                    </td>
                    <td
                      className="py-2 px-3 text-secondary font-mono text-xs break-all"
                      data-label="SHA-256"
                      title={backup.sha256}
                    >
                      {backup.sha256.slice(0, 12)}…
                    </td>
                    <td
                      className="py-2 px-3 text-secondary font-mono text-xs break-all"
                      data-label="파일명"
                      title={backup.backup_filename}
                    >
                      {backup.backup_filename}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
