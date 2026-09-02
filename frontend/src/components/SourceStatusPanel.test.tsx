import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';

import { renderWithQueryClient } from '@/test-utils';
import {
  ApiError,
  PinnedRebuildPreflight,
  RuntimePinsResponse,
  SourceStatusResponse,
} from '@/lib/api';
import SourceStatusPanel from './SourceStatusPanel';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    apiJson: vi.fn(),
  };
});

import { apiJson } from '@/lib/api';

const mockedApiJson = vi.mocked(apiJson);

const SOURCE_STATUS_OK: SourceStatusResponse = {
  schema: 'v1',
  collected_at: '2026-08-01T00:00:00Z',
  cached: false,
  manager: {
    state: 'ok',
    human: { level: 'ok', text: '설치 확인됨', next_action: '' },
  },
  checkouts: [],
  running_images: [],
  contracts: [],
  environment: {
    state: 'ok',
    human: { level: 'ok', text: '문제 없음', next_action: '' },
    required_count: 0,
    missing: [],
    injected_at_rebuild: [],
    documented_but_unused: [],
  },
  summary: { level: 'ok', text: '지금 뭐가 돌고 있는지 전부 정상입니다', next_action: '' },
};

const RUNTIME_PINS_OK: RuntimePinsResponse = {
  status: 'ok',
  source: 'test',
  pins: null,
};

const REBUILD_PREFLIGHT_OK: PinnedRebuildPreflight = {
  schema: 'v1',
  collected_at: '2026-08-01T00:00:00Z',
  can_start: true,
  pinset_sha256: 'deadbeef',
  blockers: [],
  warnings: [],
  unverified: [],
  command: 'pinvi-pair rebuild-pinned --confirm',
  summary: { state: 'ok', text: '지금 실행해도 됩니다' },
};

/** 세 섹션(사전 점검/실행/설치 기록) 중 배포 준비도(`deployment-readiness`)만 에러를
 * 내고 나머지는 성공하는 상황을 만든다. 이 패널의 4개 `useQuery`는 서로 독립이라
 * (설계 주석 참고) 하나가 실패해도 나머지는 그대로 그려져야 한다. */
function mockApiJsonPartialFailure() {
  mockedApiJson.mockImplementation((path: string) => {
    if (path.includes('/deployment-readiness')) {
      return Promise.reject(
        new ApiError(500, JSON.stringify({ detail: '점검 서비스에 연결하지 못했습니다.' }), null)
      );
    }
    if (path === '/api/v1/source-status') {
      return Promise.resolve(SOURCE_STATUS_OK) as ReturnType<typeof apiJson>;
    }
    if (path === '/api/v1/runtime-pins') {
      return Promise.resolve(RUNTIME_PINS_OK) as ReturnType<typeof apiJson>;
    }
    if (path.includes('/pinned-rebuild/preflight')) {
      return Promise.resolve(REBUILD_PREFLIGHT_OK) as ReturnType<typeof apiJson>;
    }
    return Promise.reject(new Error(`unexpected path: ${path}`));
  });
}

describe('SourceStatusPanel — 오류 표시 분기', () => {
  afterEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it('배포 준비도 조회만 실패해도 그 섹션에만 InlineError가 뜨고 다른 두 섹션은 정상 렌더된다', async () => {
    mockApiJsonPartialFailure();

    renderWithQueryClient(<SourceStatusPanel onClose={() => {}} />);

    // 실패한 섹션: 사전 점검. humanizeError가 만드는 제목을 그대로 확인한다 —
    // ApiError.serverMessage가 상태 코드 문구보다 우선해야 한다(errors.ts 우선순위 계약).
    const readinessError = await screen.findByText(
      '사전 점검 결과 조회 실패 — 점검 서비스에 연결하지 못했습니다.'
    );
    expect(readinessError).toBeInTheDocument();

    // 성공한 두 섹션은 그대로 그려진다.
    await waitFor(() => {
      expect(screen.getByText('지금 실행해도 됩니다')).toBeInTheDocument();
    });
    expect(screen.getByText('지금 뭐가 돌고 있는지 전부 정상입니다')).toBeInTheDocument();
    expect(screen.getByText('설치된 Manager 버전')).toBeInTheDocument();

    // 알림(role="alert")은 실패한 섹션 하나뿐이다 — 다른 두 섹션까지 오류로 오염되지 않았다.
    expect(screen.getAllByRole('alert')).toHaveLength(1);
  });

  it('사전 점검 섹션이 실패해도 재구축 실행 섹션의 blockers/command는 그대로 보인다', async () => {
    mockApiJsonPartialFailure();

    renderWithQueryClient(<SourceStatusPanel onClose={() => {}} />);

    await screen.findByText('사전 점검 결과 조회 실패 — 점검 서비스에 연결하지 못했습니다.');

    expect(screen.getByText('pinvi-pair rebuild-pinned --confirm')).toBeInTheDocument();
  });
});
