import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { renderWithQueryClient } from '@/test-utils';
import { ApiError, ContainerInspect } from '@/lib/api';
import ContainerDetailModal from './ContainerDetailModal';

// `postJson`은 `apiJson`을 내부적으로 호출하지만, `api.ts` 안에서 자기 모듈 스코프의
// 함수를 직접 참조한다 — export 객체의 `apiJson` 프로퍼티를 갈아 끼워도 `postJson`
// 내부 호출까지 따라오지 않는다. 그래서 조회(`apiJson`)와 실행(`postJson`) 두 진입점을
// 각각 독립된 mock으로 둔다.
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    apiJson: vi.fn(),
    postJson: vi.fn(),
  };
});

import { apiJson, postJson } from '@/lib/api';

const mockedApiJson = vi.mocked(apiJson);
const mockedPostJson = vi.mocked(postJson);

const INSPECT_OK: ContainerInspect = {
  id: 'c1',
  name: 'geo-api-1',
  status: 'running',
  restart_count: 0,
  state: { status: 'running', running: true },
};

describe('ContainerDetailModal — 오류 표시 분기', () => {
  afterEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it('컨테이너 상세 조회(useQuery)가 실패하면 InlineError가 렌더된다', async () => {
    mockedApiJson.mockRejectedValue(
      new ApiError(404, JSON.stringify({ detail: '컨테이너를 찾을 수 없습니다.' }), null)
    );

    renderWithQueryClient(
      <ContainerDetailModal
        containerId="c1"
        containerLabel="geo-api-1"
        targetId={null}
        onClose={() => {}}
      />
    );

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('컨테이너 상세 정보 조회 실패 — 컨테이너를 찾을 수 없습니다.');
  });

  it('ensure 실행(postJson, react-query 밖의 imperative 상태)이 실패하면 로컬 에러 메시지가 렌더된다', async () => {
    const user = userEvent.setup();
    mockedApiJson.mockResolvedValue(INSPECT_OK);
    mockedPostJson.mockRejectedValue(new Error('target 서비스가 응답하지 않습니다.'));
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    renderWithQueryClient(
      <ContainerDetailModal
        containerId="c1"
        containerLabel="geo-api-1"
        targetId="geo"
        targetServices={['geo-api', 'geo-db']}
        onClose={() => {}}
      />
    );

    const ensureButton = await screen.findByRole('button', {
      name: /geo ensure --build/,
    });
    await user.click(ensureButton);

    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => {
      expect(mockedPostJson).toHaveBeenCalledWith('/api/v1/targets/geo/ensure', {
        build: true,
        recreate: false,
      });
    });

    // 이 오류는 useQuery의 InlineError 경로가 아니라 컴포넌트 로컬 state
    // (`ensureState`/`ensureMessage`)로 표시된다 — HumanError 객체가 아니라
    // `e.message` 원문 문자열이 그대로 보인다.
    const message = await screen.findByText('target 서비스가 응답하지 않습니다.');
    expect(message).toBeInTheDocument();
  });
});
