'use client';

import { FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { Copy, EyeOff, KeyRound, RefreshCw, Trash2, X } from 'lucide-react';
import {
  AdminPasswordPreflight,
  LoginAuditEvent,
  PublicApiKeyCreateResponse,
  PublicApiKeySummary,
  apiJson,
  deleteJson,
  postJson,
} from '@/lib/api';
import { humanizeError } from '@/lib/errors';
import CopyableCommand from './CopyableCommand';

const MIN_PASSWORD_LENGTH = 12;
/** 서버가 이 문구를 요구하지는 않는다. 화면이 "무심코 진행"을 막는 마찰이다. */
const ACKNOWLEDGEMENT_PHRASE = '재구축 무효화 동의';


type PublicKeyState = {
  busy: boolean;
  generatedKey: string | null;
  label: string;
  message: string | null;
  keys: PublicApiKeySummary[] | null;
};

type AuditState = {
  events: LoginAuditEvent[] | null;
  message: string | null;
};

type PasswordState = {
  busy: boolean;
  current: string;
  next: string;
  confirm: string;
  typedAcknowledgement: string;
  message: string | null;
  error: string | null;
  preflight: AdminPasswordPreflight | null;
};

export default function AdminSettingsPanel({ onClose }: { onClose: () => void }) {
  const [keyState, setKeyState] = useState<PublicKeyState>({
    busy: false,
    generatedKey: null,
    label: '',
    message: null,
    keys: null,
  });
  const [auditState, setAuditState] = useState<AuditState>({ events: null, message: null });
  const [passwordState, setPasswordState] = useState<PasswordState>({
    busy: false,
    current: '',
    next: '',
    confirm: '',
    typedAcknowledgement: '',
    message: null,
    error: null,
    preflight: null,
  });
  const dialogRef = useRef<HTMLDivElement>(null);

  const patchPasswordState = useCallback((patch: Partial<PasswordState>) => {
    setPasswordState((current) => ({ ...current, ...patch }));
  }, []);

  const patchKeyState = useCallback((patch: Partial<PublicKeyState>) => {
    setKeyState((current) => ({ ...current, ...patch }));
  }, []);

  const loadPublicKeys = useCallback(async () => {
    patchKeyState({ message: null });
    try {
      patchKeyState({
        keys: await apiJson<PublicApiKeySummary[]>('/api/v1/admin/public-api-keys'),
      });
    } catch (error) {
      patchKeyState({ message: error instanceof Error ? error.message : String(error) });
    }
  }, [patchKeyState]);

  const loadAuditEvents = useCallback(async () => {
    setAuditState((current) => ({ ...current, message: null }));
    try {
      const events = await apiJson<LoginAuditEvent[]>('/api/v1/admin/login-audit-events?limit=80');
      setAuditState({ events, message: null });
    } catch (error) {
      setAuditState((current) => ({
        ...current,
        message: error instanceof Error ? error.message : String(error),
      }));
    }
  }, []);

  // 폼을 그리기 전에 가드 상태를 먼저 읽는다 — 눌러 본 뒤에야 거부를 알게 하지 않는다.
  const loadPasswordPreflight = useCallback(async () => {
    try {
      patchPasswordState({
        preflight: await apiJson<AdminPasswordPreflight>(
          '/api/v1/admin/password/preflight'
        ),
      });
    } catch (error) {
      patchPasswordState({ error: humanizeError(error, '재구축 상태 확인').title });
    }
  }, [patchPasswordState]);

  useEffect(() => {
    void loadPublicKeys();
    void loadAuditEvents();
    void loadPasswordPreflight();
  }, [loadAuditEvents, loadPublicKeys, loadPasswordPreflight]);

  const preflight = passwordState.preflight;
  const journalBlocks = preflight?.verdict === 'unfinished_journal';
  const needsAcknowledgement = preflight?.requires_acknowledgement === true;
  const acknowledged =
    !needsAcknowledgement || passwordState.typedAcknowledgement === ACKNOWLEDGEMENT_PHRASE;
  const passwordReady =
    !journalBlocks &&
    acknowledged &&
    passwordState.next.length >= MIN_PASSWORD_LENGTH &&
    passwordState.next === passwordState.confirm &&
    passwordState.next !== passwordState.current &&
    passwordState.current.length > 0;

  const changePassword = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      if (!passwordReady || passwordState.busy) return;
      patchPasswordState({ busy: true, message: null, error: null });
      try {
        await postJson<{ ok: true; guard: string }>('/api/v1/admin/password', {
          current_password: passwordState.current,
          new_password: passwordState.next,
          acknowledge_pinned_rebuild_invalidation: needsAcknowledgement,
        });
        patchPasswordState({
          busy: false,
          current: '',
          next: '',
          confirm: '',
          typedAcknowledgement: '',
          message:
            '비밀번호를 변경했습니다. 다음 로그인부터 새 비밀번호를 사용합니다. ' +
            '세션 검증은 비밀번호 해시를 보지 않으므로 이미 열려 있던 세션은 ' +
            '내 것도 남의 것도 그대로 유지됩니다 — 유출이 의심되어 바꾸는 경우라면 ' +
            '기존 세션을 따로 끊어야 합니다.',
        });
        // 감사 행이 변경의 눈에 보이는 증거다.
        await loadAuditEvents();
        await loadPasswordPreflight();
      } catch (error) {
        const humanized = humanizeError(error, '비밀번호 변경');
        patchPasswordState({ busy: false, error: `${humanized.title} ${humanized.hint}` });
      }
    },
    [
      loadAuditEvents,
      loadPasswordPreflight,
      needsAcknowledgement,
      passwordReady,
      passwordState,
      patchPasswordState,
    ]
  );

  // 모달 접근성: 열릴 때 초기 포커스를 패널로 옮기고, Escape 키로 닫는다.
  useEffect(() => {
    dialogRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  async function createKey(event: FormEvent) {
    event.preventDefault();
    patchKeyState({ busy: true, generatedKey: null, message: null });
    try {
      const result = await postJson<PublicApiKeyCreateResponse>('/api/v1/admin/public-api-keys', {
        label: keyState.label.trim() || null,
      });
      setKeyState((current) => ({
        ...current,
        busy: false,
        generatedKey: result.key,
        label: '',
        message: '공개 API 키를 생성했습니다.',
        keys: [result.item, ...(current.keys ?? [])],
      }));
      await loadAuditEvents();
    } catch (error) {
      patchKeyState({
        busy: false,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function revokeKey(publicApiKeyId: string) {
    patchKeyState({ busy: true, message: null });
    try {
      const result = await deleteJson<PublicApiKeySummary>(
        `/api/v1/admin/public-api-keys/${publicApiKeyId}`
      );
      setKeyState((current) => ({
        ...current,
        busy: false,
        message: '공개 API 키를 폐기했습니다.',
        keys: (current.keys ?? []).map((item) =>
          item.public_api_key_id === result.public_api_key_id ? result : item
        ),
      }));
      await loadAuditEvents();
    } catch (error) {
      patchKeyState({
        busy: false,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function copyGeneratedKey() {
    if (!keyState.generatedKey) return;
    const keyInput = document.getElementById('generated-public-key') as HTMLInputElement | null;
    try {
      if (!navigator.clipboard || !window.isSecureContext) {
        throw new Error('Clipboard API is unavailable');
      }
      await navigator.clipboard.writeText(keyState.generatedKey);
      patchKeyState({ message: '생성된 키를 복사했습니다.' });
    } catch {
      keyInput?.focus();
      keyInput?.select();
      const copied = document.execCommand?.('copy') ?? false;
      patchKeyState({
        message: copied
          ? '생성된 키를 복사했습니다.'
          : '생성된 키를 선택했습니다. 직접 복사해 주세요.',
      });
    }
  }

  return (
    <div
      aria-labelledby="admin-settings-title"
      aria-modal="true"
      className="ops-modal max-w-5xl flex flex-col focus-visible:outline-0"
      ref={dialogRef}
      role="dialog"
      tabIndex={-1}
    >
      <div className="ops-modal__header">
        <div>
          <p className="text-xs text-secondary font-semibold">
            시스템 설정
          </p>
          <h2 className="text-lg font-semibold text-strong mt-1" id="admin-settings-title">
            인증 및 공개 API 키
          </h2>
        </div>
        <button
          className="ops-icon-button"
          onClick={onClose}
          type="button"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="overflow-y-auto p-6 grid grid-cols-1 lg:grid-cols-2 gap-5">
        <section className="border-t border-line pt-4 lg:border-t-0 lg:border-r lg:pr-5 lg:pt-0">
          <div className="flex items-center justify-between gap-3 mb-4">
            <div>
              <h3 className="ops-section-title">공개 API 키</h3>
              <p className="ops-section-copy">
                생성된 키는 DB에 hash와 hint로 저장되며, 평문은 생성 직후 한 번만 표시됩니다.
              </p>
            </div>
            <KeyRound className="w-5 h-5 text-brand shrink-0" />
          </div>
          <form className="space-y-3" onSubmit={createKey}>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-secondary" htmlFor="public-key-label">
                키 이름
              </label>
              <input
                className="ops-input"
                id="public-key-label"
                maxLength={80}
                placeholder="운영 콘솔, 테스트 클라이언트"
                value={keyState.label}
                onChange={(event) => patchKeyState({ label: event.target.value })}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                className="ops-button ops-button--primary"
                disabled={keyState.busy}
                type="submit"
              >
                <KeyRound className="w-4 h-4" />
                랜덤 키 생성
              </button>
              <button
                className="ops-button"
                disabled={keyState.busy}
                onClick={() => void loadPublicKeys()}
                type="button"
              >
                <RefreshCw className="w-4 h-4" />
                새로고침
              </button>
            </div>
          </form>

          {keyState.generatedKey ? (
            <div className="mt-4 bg-subtle border border-line rounded-card p-3 space-y-2">
              <label
                className="text-xs font-semibold text-secondary"
                htmlFor="generated-public-key"
              >
                생성된 키
              </label>
              <div className="flex gap-2">
                <input
                  className="ops-input flex-1 font-mono"
                  id="generated-public-key"
                  readOnly
                  value={keyState.generatedKey}
                  onFocus={(event) => event.currentTarget.select()}
                />
                <button
                  aria-label="생성된 키 복사"
                  className="ops-icon-button"
                  onClick={() => void copyGeneratedKey()}
                  type="button"
                >
                  <Copy className="w-4 h-4" />
                </button>
                <button
                  aria-label="생성된 키 화면에서 지우기"
                  className="ops-icon-button"
                  onClick={() => patchKeyState({ generatedKey: null })}
                  type="button"
                >
                  <EyeOff className="w-4 h-4" />
                </button>
              </div>
              <p className="text-xs text-secondary">
                이 키는 지금 한 번만 표시됩니다. 복사한 뒤 “지우기”로 화면에서 제거할 수 있습니다.
              </p>
            </div>
          ) : null}

          <div className="mt-4 space-y-2">
            {keyState.keys === null ? (
              <p className="text-sm text-secondary">공개 API 키 목록을 불러오는 중입니다.</p>
            ) : keyState.keys.length === 0 ? (
              <p className="text-sm text-secondary">등록된 공개 API 키가 없습니다.</p>
            ) : (
              keyState.keys.map((item) => (
                <div
                  className="flex items-center justify-between gap-3 border border-line rounded-card px-3 py-2"
                  key={item.public_api_key_id}
                >
                  <div className="min-w-0">
                    <strong className="block text-sm text-strong truncate">
                      {item.label ?? '이름 없음'}
                    </strong>
                    <span className="block text-xs text-secondary font-mono">
                      {item.state === 'active' ? '활성' : '폐기됨'} · ····{item.key_hint}
                    </span>
                  </div>
                  {item.state === 'active' ? (
                    <button
                      className="ops-icon-button text-danger border-danger/40"
                      disabled={keyState.busy}
                      onClick={() => void revokeKey(item.public_api_key_id)}
                      type="button"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  ) : null}
                </div>
              ))
            )}
          </div>
          {keyState.message ? <p className="mt-3 text-sm text-secondary">{keyState.message}</p> : null}
        </section>

        <section className="border-t border-line pt-4 lg:border-t-0 lg:pl-1 lg:pt-0">
          <div className="flex items-center justify-between gap-3 mb-4">
            <div>
              <h3 className="ops-section-title">로그인 기록</h3>
              <p className="ops-section-copy">
                로그인 시도, 성공, 실패, 로그아웃과 key 관리 이벤트를 최신순으로 표시합니다.
              </p>
            </div>
            <button
              className="ops-button"
              onClick={() => void loadAuditEvents()}
              type="button"
            >
              <RefreshCw className="w-4 h-4" />
              새로고침
            </button>
          </div>
          <div className="space-y-2">
            {auditState.events === null ? (
              <p className="text-sm text-secondary">기록을 불러오는 중입니다.</p>
            ) : auditState.events.length === 0 ? (
              <p className="text-sm text-secondary">저장된 기록이 없습니다.</p>
            ) : (
              auditState.events.map((event) => (
                <div
                  className="border border-line rounded-card px-3 py-2"
                  key={event.audit_event_id}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <strong className="block text-sm text-strong">
                        {eventTitle(event)}
                      </strong>
                      <span className="block text-xs text-secondary mt-1 font-mono break-all">
                        {eventDetail(event)}
                      </span>
                    </div>
                    <span
                      className={`text-xs font-semibold shrink-0 ${
                        event.outcome === 'succeeded' ? 'text-ok' : 'text-danger'
                      }`}
                    >
                      {outcomeLabel(event.outcome)}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
          {auditState.message ? (
            <p className="mt-3 text-sm text-danger">{auditState.message}</p>
          ) : null}
        </section>

        <section className="border-t border-line pt-4 lg:col-span-2">
          <div className="flex items-center justify-between gap-3 mb-4">
            <div>
              <h3 className="ops-section-title">관리자 비밀번호 변경</h3>
              <p className="ops-section-copy">
                `.env`의 비밀번호 해시 한 줄만 바뀝니다. 재기동 없이 즉시 적용되고, 진행
                중인 세션은 끊기지 않습니다.
              </p>
            </div>
          </div>

          {journalBlocks ? (
            <div className="rounded-card border border-danger p-3 mb-3" role="alert">
              <p className="text-sm font-semibold text-danger">
                지금은 비밀번호를 바꿀 수 없습니다.
              </p>
              <p className="text-xs text-secondary mt-1">{preflight?.detail}</p>
              <p className="text-xs text-secondary mt-1">
                재구축이 끝나거나 정리된 뒤에 다시 시도하세요. 이 판정은 우회할 수 없습니다.
              </p>
              {/* 실제로 조치해야 하는 쪽에 확인 경로가 없으면 "기다리라"는 말만 남는다. */}
              {preflight?.check_command ? (
                <CopyableCommand command={preflight.check_command} />
              ) : null}
              <button
                className="ops-button mt-2"
                onClick={() => void loadPasswordPreflight()}
                type="button"
              >
                다시 확인
              </button>
            </div>
          ) : null}

          {needsAcknowledgement ? (
            <div className="rounded-card border border-line p-3 mb-3">
              <p className="text-sm font-semibold text-strong">
                진행 중인 재구축이 있는지 확인할 수 없습니다.
              </p>
              <p className="text-xs text-secondary mt-1">
                {preflight?.detail} 진행 중인 재구축이 있다면 이 변경으로 그 재구축의 재개가
                영구 차단됩니다. SSH에서 아래를 확인한 뒤 진행하세요.
              </p>
              {preflight?.check_command ? (
                <CopyableCommand command={preflight.check_command} />
              ) : null}
              <label className="block text-xs text-secondary mt-3">
                확인했다면 <span className="font-semibold">{ACKNOWLEDGEMENT_PHRASE}</span>를
                그대로 입력하세요.
                <input
                  className="ops-input mt-1"
                  onChange={(event) =>
                    patchPasswordState({ typedAcknowledgement: event.target.value })
                  }
                  value={passwordState.typedAcknowledgement}
                />
              </label>
            </div>
          ) : null}

          <form className="space-y-3 max-w-md" onSubmit={changePassword}>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-secondary" htmlFor="current-password">
                현재 비밀번호
              </label>
              <input
                autoComplete="current-password"
                className="ops-input"
                disabled={journalBlocks}
                id="current-password"
                onChange={(event) => patchPasswordState({ current: event.target.value })}
                type="password"
                value={passwordState.current}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-secondary" htmlFor="new-password">
                새 비밀번호 ({MIN_PASSWORD_LENGTH}자 이상)
              </label>
              <input
                autoComplete="new-password"
                className="ops-input"
                disabled={journalBlocks}
                id="new-password"
                onChange={(event) => patchPasswordState({ next: event.target.value })}
                type="password"
                value={passwordState.next}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-secondary" htmlFor="confirm-password">
                새 비밀번호 확인
              </label>
              <input
                autoComplete="new-password"
                className="ops-input"
                disabled={journalBlocks}
                id="confirm-password"
                onChange={(event) => patchPasswordState({ confirm: event.target.value })}
                type="password"
                value={passwordState.confirm}
              />
            </div>
            {passwordState.next && passwordState.next !== passwordState.confirm ? (
              <p className="text-xs text-danger">새 비밀번호가 서로 다릅니다.</p>
            ) : null}
            <button
              className="ops-button ops-button--primary"
              disabled={!passwordReady || passwordState.busy}
              type="submit"
            >
              {passwordState.busy ? '변경 중...' : '비밀번호 변경'}
            </button>
          </form>

          {passwordState.message ? (
            <p className="mt-3 text-sm text-ok">{passwordState.message}</p>
          ) : null}
          {passwordState.error ? (
            <p className="mt-3 text-sm text-danger">{passwordState.error}</p>
          ) : null}
        </section>
      </div>
    </div>
  );
}

function eventTitle(event: LoginAuditEvent): string {
  if (event.event_type === 'logout') return `로그아웃 · ${event.attempted_username ?? '-'}`;
  if (event.event_type === 'api_key') return `API 키 · ${event.reason ?? '-'}`;
  // 비밀번호 변경 감사 행이 "로그인"으로 표시되면 설정 오류(env_not_writable 등)가
  // 실패한 로그인 시도처럼 읽혀 보안 사고로 오인된다.
  if (event.event_type === 'admin_password') {
    return `비밀번호 변경 · ${event.attempted_username ?? '-'}`;
  }
  if (event.event_type === 'runtime_pin') return `고정 버전 · ${event.reason ?? '-'}`;
  if (event.event_type === 'backup') return `백업 · ${event.reason ?? '-'}`;
  return `로그인 · ${event.attempted_username ?? '-'}`;
}

function eventDetail(event: LoginAuditEvent): string {
  const ip = event.client_ip_hash ? `ip:${event.client_ip_hash.slice(0, 10)}` : 'ip:-';
  const ua = event.user_agent_hash ? `ua:${event.user_agent_hash.slice(0, 10)}` : 'ua:-';
  return `${event.occurred_at} · ${event.reason ?? '-'} · ${ip} · ${ua}`;
}

function outcomeLabel(outcome: LoginAuditEvent['outcome']): string {
  if (outcome === 'succeeded') return '성공';
  if (outcome === 'denied') return '거부';
  if (outcome === 'failed') return '실패';
  return outcome;
}
