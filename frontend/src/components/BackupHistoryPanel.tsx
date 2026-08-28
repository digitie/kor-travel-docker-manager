'use client';

import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, X } from 'lucide-react';
import {
  BackupJob,
  BackupListResponse,
  LatestBackupJobResponse,
  StandaloneBackupManifest,
  apiJson,
  postJson,
} from '@/lib/api';
import { HumanError, humanizeError } from '@/lib/errors';

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

/** geo 실측 소요. 누르기 전에 알아야 할 유일한 숫자다. */
const DURATION_WARNING =
  'geo는 수 시간이 걸릴 수 있습니다(실측 879초~22분, 상한 4시간). ' +
  '브라우저를 닫아도 진행됩니다.';

function formatElapsed(startedAtUnix: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000) - startedAtUnix);
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
    if (!jobId && latestJob?.job?.state === 'running') setJobId(latestJob.job.job_id);
  }, [jobId, latestJob]);

  const { data: job } = useQuery<BackupJob>({
    queryKey: ['backup-job', role, jobId],
    queryFn: () => apiJson<BackupJob>(`/api/v1/backups/${role}/jobs/${jobId}`),
    enabled: role !== 'all' && jobId !== null,
    // 진행 중일 때만 폴링한다. 끝난 job을 계속 두드릴 이유가 없다.
    refetchInterval: (query) => (query.state.data?.state === 'running' ? 5000 : false),
    retry: false,
  });

  const finishedJobId = job?.state === 'succeeded' ? job.job_id : null;
  useEffect(() => {
    // 성공하면 목록을 다시 읽는다 — 실제로 남은 것은 manifest가 말한다.
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
  const running = job?.state === 'running';

  // role별 최신 백업 시각. 기대 주기가 있는 role만 신선도를 판정한다.
  const freshness = ROLE_OPTIONS.filter((option) => option.value !== 'all')
    .map((option) => {
      const value = option.value as StandaloneBackupManifest['role'];
      const expected = EXPECTED_INTERVAL_HOURS[value];
      if (expected === undefined) return null;
      const newest = backups
        .filter((backup) => backup.role === value)
        .reduce<number | null>(
          (latest, backup) => Math.max(latest ?? 0, backup.created_at_unix),
          null
        );
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
      className="ops-modal max-w-5xl flex flex-col outline-hidden"
      ref={dialogRef}
      role="dialog"
      tabIndex={-1}
    >
      <div className="ops-modal__header">
        <div>
          <p className="text-xs text-secondary font-semibold tracking-[0.05em] uppercase">
            Backup History
          </p>
          <h2 className="text-lg font-semibold text-strong mt-1" id="backup-history-title">
            DB 백업 이력 (읽기 전용)
          </h2>
          <p className="text-xs text-secondary mt-1">
            생성은 이 화면에서 할 수 있습니다. 정리(GC)와 복원은 CLI 전용이며,
            <strong> 복원은 아직 구현돼 있지 않습니다</strong> — 백업이 있다는 것과
            복원할 수 있다는 것은 다릅니다.
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
                if (!confirm(`${role} 백업을 시작합니다.\n\n${DURATION_WARNING}`)) return;
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

        {job ? (
          <div
            aria-live="polite"
            className={`rounded-card border p-3 mb-4 text-xs ${
              job.state === 'failed' ? 'border-danger' : 'border-line'
            }`}
          >
            {job.state === 'running' ? (
              <p className="text-strong">
                {job.key} 백업이 진행 중입니다 · {formatElapsed(job.started_at_unix)} 경과.
                이 화면을 닫아도 계속됩니다.
              </p>
            ) : job.state === 'succeeded' ? (
              <p className="text-strong">
                {job.key} 백업이 끝났습니다
                {job.result ? ` — ${job.result.backup_filename}` : ''}.
              </p>
            ) : (
              <>
                <p className="text-danger font-semibold">{job.key} 백업이 실패했습니다.</p>
                <p className="text-secondary mt-1 break-all">{job.error}</p>
              </>
            )}
          </div>
        ) : null}

        {jobError ? (
          <div className="rounded-card border border-danger p-3 mb-4" role="alert">
            <p className="text-sm font-semibold text-danger">{jobError.title}</p>
            <p className="text-xs text-secondary mt-1">{jobError.hint}</p>
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
              <thead className="bg-subtle text-xs text-secondary uppercase tracking-[0.05em]">
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
