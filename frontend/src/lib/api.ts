export const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:12901';

export class ApiError extends Error {
  status: number;

  /** 백엔드가 보낸 `detail`의 구조화된 코드. FastAPI가 문자열 detail을 보내면 null이다. */
  code: string | null;

  /** 사람이 읽을 수 있는 서버 메시지. 없으면 null이고, 그때는 `message`(원문)를 쓴다. */
  serverMessage: string | null;

  /** 응답 본문 원문. "자세히" 접기에서 그대로 보여 준다. */
  raw: string;

  /** GM-16: 이 요청의 상관관계 ID(`X-Request-ID` 응답 헤더). 서버 로그·감사
   * 행과 조인하는 키라, 운영자가 이슈에 붙여넣을 수 있게 화면에도 남긴다.
   * 헤더가 없으면(예: 네트워크 자체가 끊겨 응답을 못 받은 경우) null. */
  requestId: string | null;

  constructor(status: number, raw: string, requestId: string | null = null) {
    const parsed = parseErrorBody(raw);
    super(parsed.serverMessage || raw || `${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = parsed.code;
    this.serverMessage = parsed.serverMessage;
    this.raw = raw;
    this.requestId = requestId;
  }
}

/** FastAPI의 `{"detail": ...}` 본문에서 코드와 메시지를 뽑는다.
 *
 * detail은 문자열일 때도, `{code, message, ...}` 객체일 때도 있다
 * (`api/routes.py`의 candidate/post-mutation 계약 오류가 후자다). 어느 쪽이든
 * 실패하면 원문을 그대로 쓰고 예외를 던지지 않는다 — 오류 표시 경로가 다시
 * 오류를 내면 안 된다. */
function parseErrorBody(raw: string): { code: string | null; serverMessage: string | null } {
  if (!raw) return { code: null, serverMessage: null };
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return { code: null, serverMessage: null };
  }
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === 'string') return { code: null, serverMessage: detail };
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>;
    const code = typeof record.code === 'string' ? record.code : null;
    const message = typeof record.message === 'string' ? record.message : null;
    return { code, serverMessage: message };
  }
  return { code: null, serverMessage: null };
}

export type AuthMe = {
  authenticated: boolean;
  username: string;
  expires_at: string;
};

export type ContainerMetricSnapshot = {
  timestamp: string;
  cpu_pct: number;
  mem_pct: number;
  mem_usage: number;
  mem_limit: number;
  io_read: number;
  io_write: number;
  io_read_total?: number;
  io_write_total?: number;
  network_rx_bytes?: number;
  network_tx_bytes?: number;
  network_rx_packets?: number;
  network_tx_packets?: number;
  network_rx_errors?: number;
  network_tx_errors?: number;
  pids_current?: number | null;
  pids_limit?: number | null;
  stats_available?: boolean;
};

/** `GET /api/v1/containers/{id}/inspect` 응답. env는 백엔드에서 이미 redact된 값이다. */
export type ContainerInspect = {
  id: string;
  docker_id?: string | null;
  name: string;
  display_name?: string | null;
  role?: string | null;
  image?: string | null;
  image_id?: string | null;
  image_tags?: string[] | null;
  created?: string | null;
  status?: string | null;
  restart_count?: number | null;
  metrics?: ContainerMetricSnapshot | null;
  state?: {
    status?: string | null;
    running?: boolean | null;
    paused?: boolean | null;
    restarting?: boolean | null;
    oom_killed?: boolean | null;
    dead?: boolean | null;
    exit_code?: number | null;
    error?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    health?: {
      Status?: string | null;
      FailingStreak?: number | null;
      Log?: Array<{
        Start?: string | null;
        End?: string | null;
        ExitCode?: number | null;
        Output?: string | null;
      }> | null;
    } | null;
  } | null;
  config?: {
    hostname?: string | null;
    env?: string[] | null;
    cmd?: string[] | null;
    entrypoint?: string[] | null;
    labels?: Record<string, string> | null;
    working_dir?: string | null;
  } | null;
  host_config?: {
    restart_policy?: { Name?: string | null; MaximumRetryCount?: number | null } | null;
    network_mode?: string | null;
    port_bindings?: Record<string, unknown> | null;
    binds?: string[] | null;
  } | null;
  mounts?: Array<{
    type?: string | null;
    name?: string | null;
    source?: string | null;
    destination?: string | null;
    mode?: string | null;
    rw?: boolean | null;
  }> | null;
  network?: {
    ports?: Record<string, unknown> | null;
    networks?: Record<
      string,
      {
        network_id?: string | null;
        ip_address?: string | null;
        gateway?: string | null;
        mac_address?: string | null;
        aliases?: string[] | null;
      }
    > | null;
  } | null;
};

export type PublicApiKeySummary = {
  public_api_key_id: string;
  label?: string | null;
  key_hint: string;
  state: 'active' | 'revoked';
  created_at: string;
  created_by?: string | null;
  revoked_at?: string | null;
  revoked_by?: string | null;
};

export type PublicApiKeyCreateResponse = {
  key: string;
  item: PublicApiKeySummary;
};

export type LoginAuditEvent = {
  audit_event_id: string;
  occurred_at: string;
  event_type: string;
  outcome: 'succeeded' | 'failed' | 'denied' | string;
  attempted_username?: string | null;
  reason?: string | null;
  next_path?: string | null;
  client_ip_hash?: string | null;
  user_agent_hash?: string | null;
  origin?: string | null;
  request_path?: string | null;
  session_id_hash?: string | null;
  detail?: Record<string, unknown>;
};

/** `ktdctl db-backup create`가 생성하는 standalone DB backup manifest 항목
 * (issue #177). `GET /api/v1/backups` 응답 요소. */
export type StandaloneBackupManifest = {
  role: 'geo' | 'geo_dagster' | 'concierge' | 'map_application' | 'map_dagster' | 'pinvi';
  created_at_unix: number;
  duration_sec: number;
  sha256: string;
  byte_size: number;
  backup_filename: string;
  instance: string;
  db_size_bytes: number;
  toc_entry_count: number;
  alembic_head: string | null;
};

/** GM-13: manifest 파일 하나가 손상·형식 위반·role 불일치여도 `GET /api/v1/backups`
 * 전체를 409로 지우지 않고, 그 항목만 이 형태로 성공한 항목들 사이에 끼워 보여준다. */
export type UnreadableBackupEntry = {
  state: 'unreadable';
  filename: string;
  reason: string;
};

/** `GET /api/v1/backups` 응답. 생성/보존 정리는 `ktdctl db-backup` CLI 전용이라
 * 이 API는 mutation을 노출하지 않는 읽기 전용 목록이다. */
export type BackupListResponse = {
  backups: (StandaloneBackupManifest | UnreadableBackupEntry)[];
};

/** `POST /api/v1/backups/{role}`의 202 응답이자 `GET .../jobs/{id}` 폴링 응답.
 *
 * 이 기록은 **권위가 아니다** — 프로세스가 죽으면 사라진다. 무엇이 실제로 남았는지는
 * `GET /api/v1/backups`가 읽는 디스크의 manifest가 말한다. */
export type BackupJob = {
  job_id: string;
  kind: 'db_backup_create';
  key: StandaloneBackupManifest['role'];
  state: 'running' | 'succeeded' | 'failed';
  started_at_unix: number;
  finished_at_unix: number | null;
  result: StandaloneBackupManifest | null;
  error: string | null;
  /** GM-14: 백업 자체는 시작됐지만(그래서 여기까지 온다) 그 사실을 남기는
   * 감사 로그 기록이 실패했을 때만 `POST /backups/{role}`의 202 응답에 붙는다
   * — `GET .../jobs/{id}` 폴링 응답에는 없다(그 시점엔 이미 지나간 일이다). */
  audit_warning?: string;
};

export type LatestBackupJobResponse = { job: BackupJob | null };

/** `ktdctl offbox-sync run`이 남기는 상태(GM-08) — 백업과 pin registry 보존본을
 * 설정된 원격 호스트로 옮기고 원격 `sha256sum -c`로 재검증한 마지막 결과다.
 * 실행 트리거는 CLI 전용이라 `GET /api/v1/backups/offbox-sync-status`는 읽기 전용. */
export type OffboxSyncTargetStatus = {
  label: string;
  synced: boolean;
  verified: boolean;
  detail: string;
};

export type OffboxSyncStatus = {
  destination_host: string;
  started_at_unix: number;
  duration_sec: number;
  targets: OffboxSyncTargetStatus[];
  all_verified: boolean;
};

export type OffboxSyncStatusResponse = {
  status: OffboxSyncStatus | null;
  // false: 아무도 KTDM_OFFBOX_HOST를 설정하지 않았다(의도적 미사용, 문제 아님).
  // true + status null: env는 있지만 offbox-sync run을 아직 실행한 적이 없다 —
  // 이 둘을 구분하지 않으면 "설정하고 방치"와 "애초에 안 씀"이 화면에서 똑같이 보인다.
  configured: boolean;
};

/** `GET /api/v1/admin/password/preflight`.
 *
 * `unfinished_journal`은 **우회 경로가 없는** 거부다(증명된 사실이고, 증명됐다는 것은
 * 재개가 실제로 걸려 있다는 뜻이다). `unverifiable`/`unknown`은 backend가 root의 0700
 * 디렉터리를 읽지 못한 상태이며, "못 봤다"를 "안전"으로 읽지 않으려고 명시 승인을
 * 요구한다. */
export type AdminPasswordPreflight = {
  verdict: 'not_rebuildable' | 'no_journal' | 'unfinished_journal' | 'unverifiable' | 'unknown';
  detail: string;
  requires_acknowledgement: boolean;
  blocking: boolean;
  /** 서버가 실제 경로로 만들어 준 확인 명령. 화면이 placeholder를 넣으면 붙여넣었을 때
   * 없는 경로를 조회해 "journal 없음 = 안전"으로 오독하게 된다. */
  check_command: string;
};

export type HumanVerdict = {
  level: 'ok' | 'action_required' | 'unverified';
  text: string;
  next_action: string;
};

export type SourceStatusRow = {
  id?: string;
  role?: string;
  label?: string;
  title?: string;
  state: string;
  detail?: string | null;
  revision?: string | null;
  pinned_revision?: string | null;
  head_revision?: string | null;
  image_id?: string | null;
  scope?: string;
  human: HumanVerdict;
};

export type SourceStatusEnvironment = SourceStatusRow & {
  required_count: number;
  missing: string[];
  injected_at_rebuild: string[];
  documented_but_unused: string[];
};

/** `GET /api/v1/source-status` 응답. 관측 전용이라 mutation이 없다. 각 행은
 * "최신 상태입니다 / 업데이트가 필요합니다 / 확인할 수 없습니다"로 번역돼 온다. */
export type SourceStatusResponse = {
  schema: string;
  collected_at: string;
  cached: boolean;
  cache_ttl_seconds?: number;
  manager: SourceStatusRow & { manifest?: Record<string, unknown> | null };
  checkouts: SourceStatusRow[];
  running_images: SourceStatusRow[];
  contracts: SourceStatusRow[];
  environment: SourceStatusEnvironment;
  summary: HumanVerdict;
};

/** `GET /api/v1/deployment-readiness`의 항목 하나.
 *
 * `state`는 재구축을 기준으로 읽는다: `missing`은 지금 실행하면 확실히 실패하는
 * 결손, `warn`은 막지는 않지만 사람이 봐야 하는 것, `unknown`은 판단 근거가 없다는
 * 뜻이다(추측한 값을 보여 주지 않는다). */
export type ReadinessCheck = {
  id: string;
  state: 'ok' | 'warn' | 'unknown' | 'missing';
  label_ko: string;
  detail: string;
  source: 'project_root' | 'sibling_checkout' | 'docker_cli' | 'none';
  evidence: Record<string, unknown>;
};

/** 검사하지 않기로 **결정한** 항목. 화면에서 숨기면 "전부 확인됨"으로 읽히므로
 * 이유와 함께 그대로 보여 준다. */
export type UnavailableReadinessCheck = {
  id: string;
  label_ko: string;
  reason: string;
};

/** `GET /api/v1/deployment-readiness` 응답. 관측 전용이며 무엇도 pull하지 않는다.
 * 서비스는 예외를 던지지 않으므로 호스트를 읽지 못하면 `unknown` 행으로 떨어진다. */
export type DeploymentReadinessResponse = {
  schema: string;
  generated_at: string;
  cached: boolean;
  cache_age_seconds: number;
  stale?: boolean;
  summary: {
    state: 'ok' | 'blocked' | 'unverified';
    blocking_count: number;
    warn_count: number;
    unknown_count: number;
    text: string;
  };
  checks: ReadinessCheck[];
  unavailable_checks: UnavailableReadinessCheck[];
};

export type RebuildFinding = {
  code: string;
  text: string;
  next_action?: string;
};

/** `GET /api/v1/pinned-rebuild/preflight`.
 *
 * **실행 버튼이 아니다.** `rebuild-pinned`는 root를 요구하고 3개 DB를 파기하므로,
 * 화면은 "지금 눌러도 되는가"만 판정하고 실행은 SSH에 남긴다. `can_start`가 true여도
 * 화면이 실행하지 않는다 — payload가 주는 것은 명령 문자열뿐이다. */
export type PinnedRebuildPreflight = {
  schema: string;
  collected_at: string;
  can_start: boolean;
  pinset_sha256: string | null;
  blockers: RebuildFinding[];
  warnings: RebuildFinding[];
  unverified: RebuildFinding[];
  command: string;
  /** `attention`은 차단은 아니지만 초록불도 아니다 — 읽고 나서 실행해야 한다
   * (예: 미종결 journal이 있어 새로 시작하지 않고 재개한다). */
  summary: { state: 'ok' | 'attention' | 'blocked' | 'unverified'; text: string };
};

export type DiskUsageRow = {
  type: string;
  label_ko: string;
  total_count?: string | null;
  active_count?: string | null;
  size_bytes: number | null;
  size_text: string | null;
  reclaimable_bytes: number | null;
  reclaimable_text: string | null;
};

/** `GET /api/v1/system/disk-usage` 응답. 관측 전용 — 정리(prune)는 파괴적이라
 * CLI에만 있고 이 응답은 실행할 명령만 알려 준다. */
export type DiskUsageResponse = {
  schema: string;
  collected_at: string;
  cached: boolean;
  state: 'ok' | 'warn' | 'unknown';
  rows: DiskUsageRow[];
  reclaimable_bytes: number | null;
  summary: {
    state: 'ok' | 'warn' | 'unknown';
    text: string;
    detail: string;
    next_action: string;
  };
};

export type RuntimePinSource = {
  role: 'map' | 'pinvi';
  url: string;
  revision: string;
};

/** terminal(재시도 금지) 판정을 받은 candidate pinset. `phase`가 있으면 그 상태의
 * 재개만 막고, 없으면 그 pinset의 모든 실행을 막는다. */
export type BlockedPinset = {
  pinset_sha256: string;
  map_revision: string;
  pinvi_revision: string;
  reason: string;
  blocked_at: string;
  phase?: string | null;
};

export type RuntimePinRotation = {
  pinset_sha256: string;
  rotated_at: string;
  rotated_by: string;
  reason: string;
  supersedes_pinset_sha256?: string | null;
};

/** UI가 기록한 회전 **요청**. 적용은 SSH의 `ktdctl pin apply-pending --expect-revision
 * <40-hex> --confirm`이며,
 * API 프로세스는 registry(root 0600)를 쓸 수 없다. `stale`은 요청 이후 pin이 바뀌어
 * 이 요청으로는 적용되지 않는 상태, `unreadable`은 요청 파일 자체를 읽지 못한 상태다. */
export type RuntimePinRequestSummary = {
  status: 'pending' | 'stale' | 'unreadable';
  detail?: string | null;
  request_id?: string;
  role?: 'map' | 'pinvi';
  revision?: string;
  reason?: string;
  requested_by?: string;
  requested_at?: string;
  base_pinset_sha256?: string;
  prospective_pinset_sha256?: string;
};

/** `GET /api/v1/runtime-pins` 응답. 회전 적용은 root 전용 `ktdctl pin`이므로 이 API는
 * 읽기와 **요청 기록**만 한다. registry를 읽을 수 없으면 값을 추측하지 않고 `unknown`이다. */
export type RuntimePinsResponse = {
  status: 'ok' | 'stale' | 'degraded' | 'unknown';
  source: string | null;
  detail?: string | null;
  published_at?: string | null;
  pins: {
    release_version: number;
    pinset_sha256: string;
    sources: RuntimePinSource[];
    rotated_at: string;
    rotated_by: string;
    reason: string;
  } | null;
  pending_request?: RuntimePinRequestSummary | null;
  lifecycle?: {
    current_pinset_is_blocked: boolean;
    current_pinset_has_phase_scoped_block?: boolean;
    blocked_pinsets: BlockedPinset[];
    history: RuntimePinRotation[];
  };
  summary?: {
    state: 'ok' | 'action_required' | 'unverified';
    text: string;
    next_action: string;
  };
};

type ApiRequestInit = RequestInit & {
  redirectOnUnauthorized?: boolean;
};

let unauthorizedHandler: (() => void) | null = null;

/**
 * 401 응답 시 호출될 핸들러를 등록한다. 등록되면 전체 페이지 하드 리로드 대신 이 핸들러가
 * 호출되어 SPA 내에서 인증 상태를 갱신(예: auth-me 쿼리 무효화 → LoginScreen 전환)할 수 있다.
 * 등록 해제하려면 null 을 전달한다.
 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export function apiUrl(path: string): string {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return `${BACKEND_URL}${normalized}`;
}

export function apiWsUrl(path: string): string {
  return apiUrl(path).replace(/^http/, 'ws');
}

// C7 WebSocket 종료 코드 계약 — backend api/websocket.py 및 docs/docker-management.md 참조.
export const WS_CLOSE_AUTH_REQUIRED = 4401;
export const WS_CLOSE_INVALID_CONTAINER = 4000;
export const WS_CLOSE_TRY_AGAIN_LATER = 1013;

/** WebSocket 4401을 REST 401과 동일한 인증 갱신 경로로 흘려보낸다. */
export function notifyUnauthorized(): void {
  handleUnauthorized();
}

export async function apiFetch(path: string, init?: ApiRequestInit): Promise<Response> {
  const { redirectOnUnauthorized = true, ...fetchInit } = init ?? {};
  const response = await fetch(apiUrl(path), {
    ...fetchInit,
    credentials: 'include',
    headers: {
      ...(fetchInit.body ? { 'content-type': 'application/json' } : {}),
      ...(fetchInit.headers ?? {}),
    },
  });
  if (response.status === 401 && redirectOnUnauthorized) {
    handleUnauthorized();
  }
  return response;
}

export async function apiJson<T>(path: string, init?: ApiRequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(
      response.status,
      text || `${response.status} ${response.statusText}`,
      response.headers.get('x-request-id')
    );
  }
  return (await response.json()) as T;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return apiJson<T>(path, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function deleteJson<T>(path: string): Promise<T> {
  return apiJson<T>(path, { method: 'DELETE' });
}

function handleUnauthorized() {
  if (unauthorizedHandler) {
    unauthorizedHandler();
    return;
  }
  // 핸들러 미등록(SSR/마운트 이전) 시 폴백: 루트로 이동/새로고침해 로그인 화면을 띄운다.
  if (typeof window === 'undefined') return;
  if (window.location.pathname !== '/') {
    window.location.assign('/');
  } else {
    window.location.reload();
  }
}
