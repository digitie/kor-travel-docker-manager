'use client';

import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Database, RefreshCw, X } from 'lucide-react';
import { BackupListResponse, StandaloneBackupManifest, apiJson } from '@/lib/api';

const ROLE_OPTIONS: Array<{ value: StandaloneBackupManifest['role'] | 'all'; label: string }> = [
  { value: 'all', label: '전체' },
  { value: 'map_application', label: 'map_application' },
  { value: 'map_dagster', label: 'map_dagster' },
  { value: 'pinvi', label: 'pinvi' },
];

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
  const dialogRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    dialogRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const backups = data?.backups ?? [];

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
            생성·복구·GC는 `ktdctl db-backup` CLI 전용입니다. 이 화면은 조회만 지원합니다.
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

        {isLoading ? (
          <p className="text-sm text-secondary">백업 이력을 불러오는 중입니다.</p>
        ) : error ? (
          <p className="text-sm text-danger">
            {error instanceof Error ? error.message : String(error)}
          </p>
        ) : backups.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-12 text-secondary">
            <Database className="w-8 h-8" />
            <p className="text-sm">저장된 백업이 없습니다.</p>
          </div>
        ) : (
          <div className="border-t border-line pt-2">
            <table className="ops-archive-table w-full table-fixed text-sm">
              <thead className="bg-subtle text-xs text-secondary uppercase tracking-[0.05em]">
                <tr>
                  <th className="text-left py-2 px-3 font-semibold break-all">생성 시각</th>
                  <th className="text-left py-2 px-3 font-semibold break-all">역할</th>
                  <th className="text-left py-2 px-3 font-semibold break-all">스키마 리비전</th>
                  <th className="text-left py-2 px-3 font-semibold break-all">크기</th>
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
                    <td data-label="스키마 리비전" className="py-2 px-3 text-ink font-mono">{backup.schema_revision}</td>
                    <td data-label="크기" className="py-2 px-3 text-ink break-all">
                      {formatBytes(backup.byte_size)}
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

        {data?.warnings && data.warnings.length > 0 ? (
          <div className="mt-4 space-y-1">
            {data.warnings.map((warning, index) => (
              <p className="text-xs text-danger" key={index}>
                {warning}
              </p>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
