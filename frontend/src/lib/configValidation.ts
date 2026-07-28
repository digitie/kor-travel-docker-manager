// 백엔드 kor_travel_docker_manager.services.docker_service의 검증 규칙을 그대로
// 반영한다(제출 전 즉시 피드백용). 서버가 최종 게이트이므로 여기서 어긋나도 보안
// 문제는 아니며, 최악의 경우 UX 품질만 낮아진다.

const SENSITIVE_KEY_PARTS = [
  'PASSWORD',
  'PASSWD',
  'SECRET',
  'TOKEN',
  'ACCESS_KEY',
  'PRIVATE_KEY',
  'API_KEY',
  'APIKEY',
  'CREDENTIAL',
];

function isSensitiveEnvKey(key: string): boolean {
  const upper = key.toUpperCase();
  return SENSITIVE_KEY_PARTS.some((part) => upper.includes(part));
}

const ENV_VAR_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
const INTERPOLATED_VALUE_RE = /^\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}$/;
const INTERPOLATED_VALUE_PARTS_RE = /^\$\{[A-Za-z_][A-Za-z0-9_]*(?::-([^}]*))?\}$/;
const URL_USERINFO_RE = /[a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^:/?#@\s]+:([^@/?#\s]+)@/g;

function interpolatedDefault(value: string): string | undefined {
  const match = INTERPOLATED_VALUE_PARTS_RE.exec(value);
  return match ? match[1] : undefined;
}

// ⚠️ `${...}` 블록 전체를 지우고 남는 부분만 스캔하지 않는다. 그러면
// `${FAKE_NAME:-literal-secret}`처럼 지어낸 이름으로 감싸기만 해도 스캔을 통째로
// 우회할 수 있다(적대적 리뷰에서 재현됨 — 백엔드 docker_service.py 참조). password
// 토큰 자체가 `${VAR}` 보간인 경우만 안전하다고 본다.
function hasLiteralUrlCredential(value: string): boolean {
  URL_USERINFO_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = URL_USERINFO_RE.exec(value)) !== null) {
    if (!INTERPOLATED_VALUE_RE.test(match[1])) return true;
  }
  return false;
}

/**
 * env key/value를 검증한다. `baselineValue`는 이 key의 저장된(현재 compose) 값이다.
 *
 * - baseline이 이미 `${...}` 보간이었다면 새 값도 보간이어야 한다(비밀 참조를 리터럴로
 *   되돌리는 것만 막는다 — `KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED=true`처럼 이름에
 *   API_KEY가 들어가도 원래부터 리터럴 불리언이면 자유롭다). sensitive key이면 한 겹 더:
 *   여전히 보간 형태여도 `:-default` 리터럴 자체가 baseline과 달라졌으면 거부한다 —
 *   그렇지 않으면 `${REAL_VAR:-old}` → `${FAKE_VAR:-new-secret}`처럼 지어낸 변수명으로
 *   감싸기만 해도 "여전히 보간 형태"라는 이유로 통과한다(URL이 아닌 단일 리터럴 비밀은
 *   아래 credential 스캔의 대상이 아니라서 이 겹이 없으면 그대로 뚫린다). default를
 *   완전히 없애는 것은 git에 새 리터럴을 남기지 않으므로 허용한다.
 * - baseline을 모르면(신규 key) key 이름 휴리스틱으로 방어한다.
 * - 값이 baseline과 완전히 같지 않다면, key 이름과 무관하게 literal 접속 자격증명이
 *   있으면 거부한다. "보간으로 감싸면 통과"가 아니다 — 지어낸 변수명으로 감싼
 *   `${FAKE_NAME:-literal-secret}`도 값이 바뀌었으면 거부된다.
 */
export function validateEnvEntry(
  key: string,
  value: string,
  baselineValue: string | undefined
): string | null {
  if (!ENV_VAR_NAME_RE.test(key)) {
    return `환경변수 이름이 올바르지 않습니다: '${key}'`;
  }
  // 보간 판정은 앞뒤 공백을 무시한다(터미널/.env 복붙 시 흔한 공백으로 인한 오해 방지).
  const trimmedValue = value.trim();
  if (baselineValue !== undefined) {
    const trimmedBaseline = baselineValue.trim();
    if (INTERPOLATED_VALUE_RE.test(trimmedBaseline)) {
      if (!INTERPOLATED_VALUE_RE.test(trimmedValue)) {
        return `'${key}'는 원래 '\${...}' 참조였습니다. 리터럴로 바꾸면 docker-compose.yml(git 추적 파일)에 실제 값이 그대로 저장됩니다.`;
      }
      if (isSensitiveEnvKey(key)) {
        const newDefault = interpolatedDefault(trimmedValue);
        const baselineDefault = interpolatedDefault(trimmedBaseline);
        if (newDefault && newDefault !== baselineDefault) {
          return `'${key}'는 비밀 성격 값입니다. 참조하는 변수 이름을 바꾸거나 새 기본값을 지정해도, 그 기본값이 docker-compose.yml(git 추적 파일)에 그대로 저장됩니다.`;
        }
      }
    }
  } else if (isSensitiveEnvKey(key) && !INTERPOLATED_VALUE_RE.test(trimmedValue)) {
    return `'${key}'는 비밀 성격 값으로 보입니다. .env에 정의하고 '\${${key}}' 형태로 참조하세요.`;
  }
  if (value !== baselineValue && hasLiteralUrlCredential(value)) {
    return `'${key}' 값에 접속 문자열 형태의 자격증명이 그대로 포함되어 있습니다.`;
  }
  return null;
}

const PORT_TOKEN = '(?:\\d{1,5}(?:-\\d{1,5})?|\\$\\{[^{}]+\\})';
const PORT_IP = '(?:\\d{1,3}(?:\\.\\d{1,3}){3}|\\$\\{[^{}]+\\})';
const PORT_PROTO = '(?:/(?:tcp|udp))?';
const PORT_PATTERNS = [
  new RegExp(`^(?<container>${PORT_TOKEN})${PORT_PROTO}$`),
  new RegExp(`^(?<host>${PORT_TOKEN}):(?<container>${PORT_TOKEN})${PORT_PROTO}$`),
  new RegExp(`^(?<ip>${PORT_IP}):(?<host>${PORT_TOKEN}):(?<container>${PORT_TOKEN})${PORT_PROTO}$`),
];

function validatePortToken(token: string): string | null {
  if (token.startsWith('${')) return null;
  const parts = token.split('-');
  const values: number[] = [];
  for (const part of parts) {
    if (!/^\d+$/.test(part)) return '포트 매핑 형식이 올바르지 않습니다.';
    const value = Number(part);
    if (value < 1 || value > 65535) return '포트 번호는 1~65535 범위여야 합니다.';
    values.push(value);
  }
  if (values.length === 2 && values[0] > values[1]) {
    return '포트 범위의 시작이 끝보다 큽니다.';
  }
  return null;
}

export function validatePortMapping(raw: string): string | null {
  const entry = raw.trim();
  if (!entry) return '포트 매핑 값이 비어 있습니다.';
  for (const pattern of PORT_PATTERNS) {
    const match = entry.match(pattern);
    if (match?.groups) {
      const { host, container } = match.groups;
      if (host) {
        const err = validatePortToken(host);
        if (err) return err;
      }
      return validatePortToken(container);
    }
  }
  return `포트 매핑 형식이 올바르지 않습니다: '${raw}' (예: '5432:5432')`;
}

const NETWORK_NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/;

export function validateNetworkName(raw: string): string | null {
  const entry = raw.trim();
  if (!entry) return '네트워크 이름이 비어 있습니다.';
  if (entry !== raw) return '네트워크 이름 앞뒤에 공백이 있습니다.';
  if (!NETWORK_NAME_RE.test(entry)) {
    return `네트워크 이름 형식이 올바르지 않습니다: '${raw}'`;
  }
  return null;
}
