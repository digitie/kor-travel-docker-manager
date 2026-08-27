'use client';

import { FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { Copy, EyeOff, KeyRound, RefreshCw, Trash2, X } from 'lucide-react';
import {
  LoginAuditEvent,
  PublicApiKeyCreateResponse,
  PublicApiKeySummary,
  apiJson,
  deleteJson,
  postJson,
} from '@/lib/api';

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

export default function AdminSettingsPanel({ onClose }: { onClose: () => void }) {
  const [keyState, setKeyState] = useState<PublicKeyState>({
    busy: false,
    generatedKey: null,
    label: '',
    message: null,
    keys: null,
  });
  const [auditState, setAuditState] = useState<AuditState>({ events: null, message: null });
  const dialogRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    void loadPublicKeys();
    void loadAuditEvents();
  }, [loadAuditEvents, loadPublicKeys]);

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
      className="ops-modal max-w-5xl flex flex-col outline-hidden"
      ref={dialogRef}
      role="dialog"
      tabIndex={-1}
    >
      <div className="ops-modal__header">
        <div>
          <p className="text-xs text-secondary font-semibold tracking-[0.05em] uppercase">
            Admin Settings
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
      </div>
    </div>
  );
}

function eventTitle(event: LoginAuditEvent): string {
  if (event.event_type === 'logout') return `로그아웃 · ${event.attempted_username ?? '-'}`;
  if (event.event_type === 'api_key') return `API 키 · ${event.reason ?? '-'}`;
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
