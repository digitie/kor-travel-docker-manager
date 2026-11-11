import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQueryClient } from '@/test-utils';
import { apiJson, postJson, type BackupJob } from '@/lib/api';
import BackupHistoryPanel from './BackupHistoryPanel';

vi.mock('@/lib/api', async (original) => ({ ...await original<typeof import('@/lib/api')>(), apiJson: vi.fn(), postJson: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it('승인 뒤 서버가 반환한 진행 작업을 캐시에 연결하고 완료로 표시하지 않는다', async () => {
  const job: BackupJob = { job_id: 'new-job', kind: 'db_backup_create', key: 'geo', state: 'running',
    started_at_unix: Date.now() / 1000, finished_at_unix: null, result: null, error: null };
  vi.mocked(apiJson).mockImplementation(async (path) => {
    if (path.includes('/offbox-sync-status')) return { configured: false, status: null } as never;
    if (path.endsWith('/jobs/new-job')) return new Promise(() => {});
    if (path.endsWith('/jobs')) return { job: null } as never;
    return { roles: ['geo'], backups: [] } as never;
  });
  vi.mocked(postJson).mockResolvedValue(job);
  const { queryClient } = renderWithQueryClient(<BackupHistoryPanel onClose={() => {}} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'geo' }));
  const create = screen.getByRole('button', { name: '백업 생성' });
  await waitFor(() => expect(create).toBeEnabled()); await user.click(create);
  expect(postJson).not.toHaveBeenCalled();
  await user.click(await screen.findByRole('button', { name: '계속' }));
  await waitFor(() => expect(queryClient.getQueryData(['backup-job', 'geo', 'new-job'])).toEqual(job));
  expect(screen.getByText(/백업이 진행 중입니다/)).toBeInTheDocument();
  expect(screen.queryByText(/백업이 끝났습니다/)).not.toBeInTheDocument();
});
