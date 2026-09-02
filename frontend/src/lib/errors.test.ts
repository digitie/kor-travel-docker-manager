import { describe, expect, it } from 'vitest';

import { ApiError } from './api';
import { humanizeError } from './errors';

/** admin.py/auth.py의 bare-string HTTPException(detail="TOKEN")을 구조화된
 * { code, message } 봉투로 바꾼 수정의 프런트 쪽 짝이다.
 *
 * parseErrorBody(api.ts)는 detail이 객체일 때만 code를 뽑아내므로, 여기서 만드는
 * ApiError는 실제 백엔드가 보내는 형태(JSON 문자열 본문의 detail 객체)를 그대로
 * 흉내 낸다 — CODE_MESSAGES 조회가 raw 토큰이 아니라 실제로 이 두 신규 키에서
 * 사람이 읽을 수 있는 문구를 돌려주는지 확인한다. */
function apiErrorWithCode(status: number, code: string, message: string): ApiError {
  const raw = JSON.stringify({ detail: { code, message } });
  return new ApiError(status, raw);
}

describe('humanizeError — CODE_MESSAGES 신규 항목', () => {
  it('PUBLIC_API_KEY_NOT_FOUND은 raw 토큰이 아니라 사람이 읽을 수 있는 문구를 돌려준다', () => {
    const error = apiErrorWithCode(404, 'PUBLIC_API_KEY_NOT_FOUND', '해당 공개 API 키를 찾을 수 없습니다.');

    const result = humanizeError(error, '키 삭제');

    expect(result.title).not.toContain('PUBLIC_API_KEY_NOT_FOUND');
    expect(result.title).toContain('그 공개 API 키가 이미 없습니다.');
    expect(result.hint).not.toBe('');
  });

  it('RATE_LIMITED은 raw 토큰이 아니라 사람이 읽을 수 있는 문구를 돌려준다', () => {
    const error = apiErrorWithCode(429, 'RATE_LIMITED', '로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.');

    const result = humanizeError(error, '로그인');

    expect(result.title).not.toContain('RATE_LIMITED');
    expect(result.title).toContain('요청이 너무 많아 잠시 차단됐습니다.');
    expect(result.hint).not.toBe('');
  });
});

describe('humanizeError — INVALID_CREDENTIALS 문맥 충돌 회귀', () => {
  /** 같은 코드가 두 폼에서 나온다: 비밀번호 변경("현재 비밀번호" 입력칸이 있음)과
   * 일반 로그인(그런 칸이 없음). byCode 매핑이 서버 메시지보다 우선이므로, 문구가
   * "현재 비밀번호"에 고정돼 있으면 로그인 실패에서도 그 문구가 뜬다 — 로그인 폼에는
   * "현재 비밀번호"라는 개념 자체가 없으므로 이는 사용자에게 혼란을 준다. */
  it('로그인 실패(서버 메시지는 "아이디 또는 비밀번호")에도 "현재 비밀번호" 문구를 보여주지 않는다', () => {
    const error = apiErrorWithCode(401, 'INVALID_CREDENTIALS', '아이디 또는 비밀번호가 올바르지 않습니다.');

    const result = humanizeError(error, '로그인');

    expect(result.title).not.toContain('현재 비밀번호');
    expect(result.title).toContain('비밀번호가 일치하지 않습니다.');
  });

  it('비밀번호 변경 실패에서도 여전히 사람이 읽을 수 있는 문구를 돌려준다', () => {
    const error = apiErrorWithCode(401, 'INVALID_CREDENTIALS', '현재 비밀번호가 일치하지 않습니다.');

    const result = humanizeError(error, '비밀번호 변경');

    expect(result.title).not.toContain('INVALID_CREDENTIALS');
    expect(result.title).toContain('비밀번호가 일치하지 않습니다.');
  });
});
