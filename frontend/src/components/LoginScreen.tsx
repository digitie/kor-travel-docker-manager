'use client';

import { FormEvent, useState } from 'react';
import { LockKeyhole, LogIn } from 'lucide-react';
import { ApiError, postJson } from '@/lib/api';

export default function LoginScreen({ onLogin }: { onLogin: () => Promise<void> }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await postJson('/api/v1/auth/login', { username, password, next: '/' });
      await onLogin();
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setError('로그인 환경변수가 설정되지 않았습니다.');
      } else if (err instanceof ApiError && err.status === 429) {
        setError('로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.');
      } else if (err instanceof ApiError && err.status === 403) {
        setError('허용되지 않은 요청입니다. 대시보드 주소를 확인하세요.');
      } else {
        setError('아이디 또는 비밀번호가 올바르지 않습니다.');
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="ops-auth-shell">
      <section className="ops-auth-frame" aria-labelledby="login-title">
        <div className="ops-auth-intro">
          <div className="ops-brand__mark" aria-hidden="true">KT</div>
          <p className="ops-eyebrow mt-8 text-graphite-ink">Kor Travel Docker Manager</p>
          <h1>운영 인프라를<br />안전하게 제어합니다.</h1>
          <p>컨테이너 상태, 로그, 백업 이력과 인증 설정은 승인된 관리자 세션에서만 확인할 수 있습니다.</p>
        </div>
        <div className="ops-auth-card">
          <div className="flex items-center gap-3 pb-5 border-b border-line">
            <div className="p-2 bg-brand-tint text-brand border border-line rounded-card">
              <LockKeyhole className="w-5 h-5" />
            </div>
            <div>
              <p className="ops-eyebrow">관리자 세션</p>
              <h2 className="ops-section-title mt-1" id="login-title">로그인</h2>
            </div>
          </div>

        <form aria-busy={busy} className="pt-5 space-y-4" onSubmit={submit}>
          <div className="space-y-1.5">
            <label className="ops-form-label" htmlFor="admin-username">
              아이디
            </label>
            <input
              aria-describedby={error ? 'login-error' : undefined}
              aria-invalid={Boolean(error)}
              autoComplete="username"
              className="ops-input"
              disabled={busy}
              id="admin-username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <label className="ops-form-label" htmlFor="admin-password">
              비밀번호
            </label>
            <input
              aria-describedby={error ? 'login-error' : undefined}
              aria-invalid={Boolean(error)}
              autoComplete="current-password"
              autoFocus
              className="ops-input"
              disabled={busy}
              id="admin-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <button
            className="ops-button ops-button--primary w-full"
            disabled={busy}
            type="submit"
          >
            <LogIn className="w-4 h-4" />
            로그인
          </button>
          {error ? (
            <p
              aria-live="polite"
              className="text-sm text-danger bg-danger/5 border border-danger/30 rounded-card px-3 py-2"
              id="login-error"
              role="alert"
            >
              {error}
            </p>
          ) : null}
        </form>
        </div>
      </section>
    </main>
  );
}
