import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { renderWithQueryClient } from '@/test-utils';
import {
  ApiError,
  AdminPasswordPreflight,
  LoginAuditEvent,
  PublicApiKeySummary,
} from '@/lib/api';
import AdminSettingsPanel from './AdminSettingsPanel';

// 이 컴포넌트는 react-query를 쓰지 않고 apiJson/postJson/deleteJson을 직접 호출해
// useState를 굴린다. `postJson`/`deleteJson`은 `api.ts` 내부에서 자기 모듈 스코프의
// `apiJson`을 직접 참조하므로(ContainerDetailModal 테스트와 동일한 이유), export 객체의
// `apiJson`만 갈아 끼워서는 `postJson`/`deleteJson` 호출까지 가로챌 수 없다 — 셋 다
// 독립적으로 mock한다.
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    apiJson: vi.fn(),
    postJson: vi.fn(),
    deleteJson: vi.fn(),
  };
});

import { apiJson, postJson } from '@/lib/api';

const mockedApiJson = vi.mocked(apiJson);
const mockedPostJson = vi.mocked(postJson);

const PREFLIGHT_OK: AdminPasswordPreflight = {
  verdict: 'no_journal',
  detail: '',
  requires_acknowledgement: false,
  blocking: false,
  check_command: '',
};

const KEY_ITEM: PublicApiKeySummary = {
  public_api_key_id: 'k1',
  label: '테스트 키',
  key_hint: 'ab12',
  state: 'active',
  created_at: '2026-08-01T00:00:00Z',
};

/** 공개 키/로그인 기록/비밀번호 preflight 세 GET을 각자 지정된 결과로 흘려보내는
 * 기본 라우터. 개별 테스트가 특정 경로만 override한다. */
function routeApiJson(overrides: {
  publicKeys?: () => Promise<PublicApiKeySummary[]>;
  auditEvents?: () => Promise<LoginAuditEvent[]>;
  preflight?: () => Promise<AdminPasswordPreflight>;
}) {
  mockedApiJson.mockImplementation((path: unknown) => {
    const p = String(path);
    if (p === '/api/v1/admin/public-api-keys') {
      return (overrides.publicKeys ?? (() => Promise.resolve([])))();
    }
    if (p.startsWith('/api/v1/admin/login-audit-events')) {
      return (overrides.auditEvents ?? (() => Promise.resolve([])))();
    }
    if (p === '/api/v1/admin/password/preflight') {
      return (overrides.preflight ?? (() => Promise.resolve(PREFLIGHT_OK)))();
    }
    return Promise.reject(new Error(`unexpected path: ${p}`));
  });
}

describe('AdminSettingsPanel — 오류 표시 분기', () => {
  afterEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it('공개 API 키 목록 새로고침이 성공하면 이전 실패 메시지를 지운다 (loadPublicKeys 시작 시 상태 초기화)', async () => {
    let publicKeysCall = 0;
    routeApiJson({
      publicKeys: () => {
        publicKeysCall += 1;
        if (publicKeysCall === 1) {
          return Promise.reject(
            new ApiError(500, JSON.stringify({ detail: '키 목록을 불러오지 못했습니다.' }), null)
          );
        }
        return Promise.resolve([KEY_ITEM]);
      },
    });
    const user = userEvent.setup();

    renderWithQueryClient(<AdminSettingsPanel onClose={() => {}} />);

    // 최초 로드가 실패했다는 것부터 확인한다 — 이게 없으면 아래 "사라짐" 단언이
    // 애초에 아무것도 검증하지 않는 헛단언이 된다.
    await screen.findByText('공개 API 키 목록 조회 실패 — 키 목록을 불러오지 못했습니다.');

    const refreshButtons = screen.getAllByRole('button', { name: '새로고침' });
    await user.click(refreshButtons[0]);

    // GM 감사 노트가 지적했던 버그: 새로고침이 성공한 뒤에도 이전 오류 문구가
    // 화면에 그대로 남아 있던 문제. 현재 코드는 `loadPublicKeys` 시작 시
    // `patchKeyState({ message: null, error: null })`로 그 즉시 지운다.
    await waitFor(() => {
      expect(
        screen.queryByText('공개 API 키 목록 조회 실패 — 키 목록을 불러오지 못했습니다.')
      ).not.toBeInTheDocument();
    });
    expect(await screen.findByText('테스트 키')).toBeInTheDocument();
  });

  it('로그인 기록 조회가 실패하면 그 섹션에 InlineError가 뜬다', async () => {
    routeApiJson({
      auditEvents: () =>
        Promise.reject(
          new ApiError(503, JSON.stringify({ detail: '감사 로그 저장소에 연결할 수 없습니다.' }), null)
        ),
    });

    renderWithQueryClient(<AdminSettingsPanel onClose={() => {}} />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('로그인 기록 조회 실패 — 감사 로그 저장소에 연결할 수 없습니다.');
  });

  it('비밀번호 변경이 실패하면 (HumanError 객체가 아니라) title+hint 문자열이 렌더된다', async () => {
    routeApiJson({});
    mockedPostJson.mockRejectedValue(new Error('일시적인 오류입니다.'));
    const user = userEvent.setup();

    renderWithQueryClient(<AdminSettingsPanel onClose={() => {}} />);

    // preflight 로드를 기다린다 — 로드 전에는 acknowledged 계산이 preflight=null 기준이라
    // (needsAcknowledgement가 항상 false) 버튼이 우연히 활성화될 수 있어 이 테스트의
    // 전제(preflight가 실제로 반영된 상태)를 흐린다.
    await waitFor(() => expect(mockedApiJson).toHaveBeenCalledWith('/api/v1/admin/password/preflight'));

    await user.type(screen.getByLabelText('현재 비밀번호'), 'old-password-1');
    await user.type(screen.getByLabelText(/새 비밀번호 \(\d+자 이상\)/), 'new-password-123');
    await user.type(screen.getByLabelText('새 비밀번호 확인'), 'new-password-123');

    const submit = screen.getByRole('button', { name: '비밀번호 변경' });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);

    const message = await screen.findByText(
      '비밀번호 변경 실패 관리도구가 서버에 연결하지 못했을 수 있습니다. 네트워크와 백엔드 상태를 확인하세요.'
    );
    expect(message.tagName).toBe('P');
    // 다른 두 섹션(공개 API 키/로그인 기록)의 오류는 HumanError 객체를 InlineError로
    // 렌더해 role="alert"가 붙는다. 비밀번호 섹션은 그 경로를 타지 않는다는 것을
    // "이 화면에 alert가 하나도 없다"로 확인한다.
    expect(screen.queryAllByRole('alert')).toHaveLength(0);
  });
});
