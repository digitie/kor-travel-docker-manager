import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { renderWithQueryClient } from '@/test-utils';
import { RuntimePinsResponse } from '@/lib/api';
import RuntimePinPanel from './RuntimePinPanel';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return { ...actual, apiJson: vi.fn(), postJson: vi.fn(), deleteJson: vi.fn() };
});

import { apiJson, postJson } from '@/lib/api';

const mockedApiJson = vi.mocked(apiJson);
const PIN_VERIFY = 'sudo -n backend/.venv/bin/ktdctl pin verify';

const BLOCKED_CURRENT: RuntimePinsResponse = {
  status: 'ok',
  source: 'published_copy',
  pins: {
    release_version: 5,
    pinset_sha256: 'a'.repeat(64),
    sources: [
      { role: 'map', url: 'https://github.com/digitie/kor-travel-map.git', revision: 'b'.repeat(40) },
      { role: 'pinvi', url: 'https://github.com/digitie/pinvi.git', revision: 'c'.repeat(40) },
    ],
    rotated_at: '2026-09-26T00:00:00Z',
    rotated_by: 'root',
    reason: 'test',
  },
  pending_request: null,
  lifecycle: { current_pinset_is_blocked: true, blocked_pinsets: [], history: [] },
  summary: {
    state: 'action_required',
    text: '현재 고정된 세트에 legacy terminal 기록이 있습니다. root 검증으로 확인해야 합니다.',
    next_action: PIN_VERIFY,
  },
};

describe('RuntimePinPanel — 재시도 금지 기록이 있는 현재 세트', () => {
  afterEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it('서버가 준 next_action(pin verify)을 보이고 회전을 "해야 한다"고 말하지 않는다 (ADR-51: 경고일 뿐)', async () => {
    mockedApiJson.mockImplementation((path: unknown) =>
      String(path) === '/api/v1/runtime-pins'
        ? Promise.resolve(BLOCKED_CURRENT)
        : Promise.reject(new Error(`unexpected path: ${String(path)}`))
    );

    renderWithQueryClient(<RuntimePinPanel onClose={() => undefined} />);

    expect(await screen.findByText(PIN_VERIFY)).toBeTruthy();
    expect(screen.queryByText(/새 버전으로 회전해야 합니다/)).toBeNull();

    const user = userEvent.setup();
    const role = screen.getByLabelText('대상');
    expect(role).toHaveValue('map');
    await user.selectOptions(role, 'pinvi');
    expect(screen.getByLabelText('대상')).toBe(role);
    expect(role).toHaveValue('pinvi');

    const revision = screen.getByLabelText('고정할 커밋 SHA');
    await user.type(revision, 'a'.repeat(40));
    expect(screen.getByLabelText('고정할 커밋 SHA')).toBe(revision);
    expect(revision).toHaveFocus();
    const reason = screen.getByLabelText('변경 사유');
    await user.type(reason, '검증 사유');
    expect(screen.getByLabelText('변경 사유')).toBe(reason);
    expect(reason).toHaveFocus();
    await user.clear(reason);
    expect(reason).toHaveValue('');
    expect(postJson).not.toHaveBeenCalled();
  });
});
