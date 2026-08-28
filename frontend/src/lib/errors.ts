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
  RUNTIME_PINS_UNVERIFIED: {
    title: '현재 고정 값을 확인할 수 없어 요청을 받지 못했습니다.',
    hint: 'SSH에서 `ktdctl pin verify`를 실행해 공개 사본을 갱신한 뒤 다시 시도하세요.',
  },
  RUNTIME_PINS_MALFORMED: {
    title: '공개된 고정 값의 형식이 올바르지 않습니다.',
    hint: '요청은 기록되지 않았습니다. SSH에서 `ktdctl pin verify`로 registry 상태를 확인하세요.',
  },
  RUNTIME_PIN_UNCHANGED: {
    title: '이미 그 버전이 고정돼 있습니다.',
    hint: '바뀌는 것이 없어 요청을 기록하지 않았습니다. 다른 커밋 SHA를 입력하세요.',
  },
  RUNTIME_PIN_BLOCKED_TARGET: {
    title: '재시도가 영구 금지된 버전 조합입니다.',
    hint: '이 조합은 과거에 재구축이 실패로 종료됐습니다. 다른 커밋 SHA를 지정하세요.',
  },
  RUNTIME_PIN_REQUEST_EXISTS: {
    title: '이미 대기 중인 회전 요청이 있습니다.',
    hint: '먼저 그 요청을 적용하거나 취소한 뒤 새 요청을 남기세요. 기존 요청은 덮어쓰지 않습니다.',
  },
  RUNTIME_PIN_REQUEST_NOT_FOUND: {
    title: '그 요청이 이미 없습니다.',
    hint: '다른 사람이 적용했거나 취소했을 수 있습니다. 새로고침 후 확인하세요.',
  },
  RUNTIME_PIN_REQUEST_UNREADABLE: {
    title: '대기 중인 요청 파일을 읽지 못했습니다.',
    hint: 'SSH에서 `ktdctl pin show-pending`으로 상태를 확인하세요.',
  },
  RUNTIME_PIN_REQUEST_NOT_WRITABLE: {
    title: '요청을 저장하지 못했습니다.',
    hint: '백엔드 사용자가 요청 디렉터리에 쓸 수 있는지 운영자에게 확인하세요. 고정 값은 그대로입니다.',
  },
  RUNTIME_PIN_TERMINAL_REQUIRES_PAIR: {
    title: '지금은 한쪽만 회전할 수 없습니다.',
    hint:
      '고정된 세트가 재시도 금지 상태라 Map과 PinVi를 한 번에 회전해야 합니다. SSH에서 ' +
      '`ktdctl pin rotate-pair --map-revision <40-hex> --pinvi-revision <40-hex> ' +
      '--reason "..." --confirm`을 실행하세요.',
  },
  INVALID_CREDENTIALS: {
    title: '현재 비밀번호가 일치하지 않습니다.',
    hint: '다시 입력하세요. 5회 연속 실패하면 로그인 자체가 일시적으로 차단됩니다.',
  },
  AUTH_MISCONFIGURED: {
    title: '관리자 인증 설정이 완전하지 않습니다.',
    hint: '`.env`의 관리자 해시와 세션 비밀이 설정돼 있는지 SSH에서 확인하세요.',
  },
  NEW_PASSWORD_TOO_SHORT: {
    title: '새 비밀번호가 너무 짧습니다.',
    hint: '12자 이상이어야 합니다.',
  },
  NEW_PASSWORD_INVALID: {
    title: '새 비밀번호에 쓸 수 없는 문자가 있습니다.',
    hint: '줄바꿈이나 NUL을 포함할 수 없습니다.',
  },
  NEW_PASSWORD_UNCHANGED: {
    title: '새 비밀번호가 현재 비밀번호와 같습니다.',
    hint: '다른 값을 입력하세요.',
  },
  PINNED_REBUILD_JOURNAL_UNFINISHED: {
    title: '진행 중인 재구축이 있어 비밀번호를 바꿀 수 없습니다.',
    hint: '지금 바꾸면 그 재구축의 재개가 영구 차단됩니다. 재구축이 끝나거나 정리된 뒤에 다시 시도하세요.',
  },
  PINNED_REBUILD_JOURNAL_UNVERIFIABLE: {
    title: '진행 중인 재구축이 있는지 확인할 수 없습니다.',
    hint: 'SSH에서 확인한 뒤 명시 문구를 입력해야 진행할 수 있습니다.',
  },
  ENV_NOT_WRITABLE: {
    title: '`.env`를 이 프로세스가 쓸 수 없습니다.',
    hint: '권한을 완화하지 마세요. backend를 해당 소유자 권한으로 재기동하거나 SSH에서 해시를 직접 교체합니다.',
  },
  ENV_MODE_UNSAFE: {
    title: '`.env`의 권한이 안전하지 않습니다.',
    hint: '`0600`이어야 합니다. SSH에서 권한을 확인하세요.',
  },
  ENV_DUPLICATE_ASSIGNMENT: {
    title: '`.env`에 같은 키가 여러 번 있습니다.',
    hint: '어느 줄이 유효한지 모호하므로 SSH에서 손으로 정리한 뒤 다시 시도하세요.',
  },
  ENV_REWRITE_WOULD_CHANGE_OTHER_KEYS: {
    title: '다른 키까지 바뀔 수 있어 저장하지 않았습니다.',
    hint: '`.env`가 예상 밖의 형식입니다. 아무것도 바뀌지 않았습니다.',
  },
  BACKUP_JOB_NOT_FOUND: {
    title: '그 백업 작업 기록이 없습니다.',
    hint: '관리도구가 재기동되면 진행 기록은 사라집니다. 실제로 남은 백업은 아래 목록이 정본입니다.',
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

/** 어떤 예외든 사람이 읽을 수 있는 형태로 바꾼다. 절대 던지지 않는다.
 *
 * 우선순위가 중요하다: **코드 매핑 → 서버 메시지 → 상태 코드 문구**. 서버 메시지를
 * 상태 코드 문구보다 뒤에 두면, 백엔드가 정확히 써 놓은 한국어 안내(예: "현재 비밀번호가
 * 일치하지 않습니다")를 "로그인이 필요합니다. 다시 로그인하세요" 같은 엉뚱한 일반 문구가
 * 덮어 버린다. 그 상태에서 운영자는 멀쩡한 비밀번호로 다시 로그인하고 어리둥절해진다. */
export function humanizeError(error: unknown, action: string): HumanError {
  if (error instanceof ApiError) {
    const byCode = error.code ? CODE_MESSAGES[error.code] : undefined;
    if (byCode) {
      return { title: `${action} 실패 — ${byCode.title}`, hint: byCode.hint, raw: error.raw };
    }
    const byStatus = STATUS_MESSAGES[error.status];
    if (error.serverMessage) {
      // 서버가 구체적으로 말했다면 그것이 가장 정확하다. 상태 코드 문구는 보조로 붙인다.
      return {
        title: `${action} 실패 — ${error.serverMessage}`,
        hint: byStatus?.hint ?? '아래 원문을 확인하세요.',
        raw: error.raw,
      };
    }
    if (byStatus) {
      return {
        title: `${action} 실패 — ${byStatus.title}`,
        hint: byStatus.hint,
        raw: error.raw,
      };
    }
    return {
      title: `${action} 실패`,
      hint: '아래 원문을 확인하세요.',
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
