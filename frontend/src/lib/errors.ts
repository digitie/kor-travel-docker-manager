import { ApiError } from './api';

/** 사용자에게 보여 줄 오류 표현.
 *
 * `title`은 무슨 일이 일어났는지, `hint`는 다음에 뭘 하면 되는지, `raw`는 접어 두는
 * 원문이다. 비전문 관리자가 브라우저 alert로 raw JSON을 마주하지 않게 하는 것이
 * 이 계층의 목적이고, 원문은 버리지 않고 접어 둔다. */
export type HumanError = {
  title: string;
  hint: string;
  raw: string;
};

/** 백엔드 계약 오류 코드 → 사람 말.
 *
 * 코드는 `services/c6c_deployment.py`의 계약 예외가 소유한다. 여기 없는 코드는
 * 서버 메시지를 그대로 쓰되 raw는 접어 둔다 — 매핑 누락이 표시 실패가 되면 안 된다. */
const CODE_MESSAGES: Record<string, { title: string; hint: string }> = {
  COMPOSE_CANDIDATE_PROTECTED_REFERENCE: {
    title: '변경하려는 값이 보호된 항목이라 적용하지 않았습니다.',
    hint: '컨테이너는 그대로입니다. 볼륨이나 이미지처럼 고정된 항목은 이 화면에서 바꿀 수 없습니다.',
  },
  COMPOSE_POST_MUTATION_CONTRACT_FAILURE: {
    title: '변경은 적용됐지만 이후 검증에서 문제가 발견됐습니다.',
    hint: '자동 복구를 시도했습니다. 아래 원문의 복구 결과를 확인하고, 상태가 이상하면 운영자에게 알리세요.',
  },
};

const STATUS_MESSAGES: Record<number, { title: string; hint: string }> = {
  401: {
    title: '로그인이 필요합니다.',
    hint: '세션이 만료됐습니다. 다시 로그인하세요.',
  },
  403: {
    title: '허용되지 않은 요청입니다.',
    hint: '대시보드 주소가 올바른지 확인하세요.',
  },
  404: {
    title: '대상을 찾을 수 없습니다.',
    hint: '컨테이너나 target이 이미 사라졌을 수 있습니다. 새로고침 후 다시 시도하세요.',
  },
  409: {
    title: '지금은 이 작업을 할 수 없는 상태입니다.',
    hint: '운영 환경에서 막혀 있거나 다른 작업이 진행 중일 수 있습니다. 아래 원문을 확인하세요.',
  },
  429: {
    title: '요청이 너무 많습니다.',
    hint: '잠시 후 다시 시도하세요.',
  },
  500: {
    title: '관리도구 내부에서 오류가 발생했습니다.',
    hint: '아래 원문을 운영자에게 전달하세요.',
  },
  503: {
    title: '관리도구가 아직 준비되지 않았습니다.',
    hint: '필요한 환경변수가 설정되지 않았을 수 있습니다.',
  },
};

/** 어떤 예외든 사람이 읽을 수 있는 형태로 바꾼다. 절대 던지지 않는다. */
export function humanizeError(error: unknown, action: string): HumanError {
  if (error instanceof ApiError) {
    const byCode = error.code ? CODE_MESSAGES[error.code] : undefined;
    if (byCode) {
      return { title: `${action} 실패 — ${byCode.title}`, hint: byCode.hint, raw: error.raw };
    }
    const byStatus = STATUS_MESSAGES[error.status];
    if (byStatus) {
      return {
        title: `${action} 실패 — ${byStatus.title}`,
        hint: error.serverMessage ? `${byStatus.hint}` : byStatus.hint,
        raw: error.raw,
      };
    }
    return {
      title: `${action} 실패`,
      hint: error.serverMessage ?? '아래 원문을 확인하세요.',
      raw: error.raw,
    };
  }
  const message = error instanceof Error ? error.message : String(error);
  return {
    title: `${action} 실패`,
    hint: '관리도구가 서버에 연결하지 못했을 수 있습니다. 네트워크와 백엔드 상태를 확인하세요.',
    raw: message,
  };
}
