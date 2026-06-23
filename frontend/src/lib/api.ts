export const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:12901';

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export type AuthMe = {
  authenticated: boolean;
  username: string;
  expires_at: string;
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
    throw new ApiError(response.status, text || `${response.status} ${response.statusText}`);
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
