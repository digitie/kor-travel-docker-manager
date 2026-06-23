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
    <main className="min-h-screen bg-page text-ink flex items-center justify-center px-6 py-10">
      <section className="w-full max-w-md bg-card border border-line rounded-card shadow-card p-6">
        <div className="flex items-center gap-3 pb-5 border-b border-line">
          <div className="p-2 bg-brand-tint text-brand border border-line rounded-card">
            <LockKeyhole className="w-6 h-6" />
          </div>
          <div>
            <p className="text-xs text-secondary font-semibold tracking-[0.05em] uppercase">
              Kor Travel Docker Manager
            </p>
            <h1 className="text-xl font-semibold text-strong mt-1">관리자 로그인</h1>
          </div>
        </div>

        <form aria-busy={busy} className="pt-5 space-y-4" onSubmit={submit}>
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-secondary" htmlFor="admin-username">
              아이디
            </label>
            <input
              aria-describedby={error ? 'login-error' : undefined}
              aria-invalid={Boolean(error)}
              autoComplete="username"
              className="w-full bg-subtle border border-line rounded-card min-h-[44px] px-3 text-sm text-strong outline-hidden focus-visible:outline-2 focus-visible:outline-brand"
              disabled={busy}
              id="admin-username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-secondary" htmlFor="admin-password">
              비밀번호
            </label>
            <input
              aria-describedby={error ? 'login-error' : undefined}
              aria-invalid={Boolean(error)}
              autoComplete="current-password"
              autoFocus
              className="w-full bg-subtle border border-line rounded-card min-h-[44px] px-3 text-sm text-strong outline-hidden focus-visible:outline-2 focus-visible:outline-brand"
              disabled={busy}
              id="admin-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <button
            className="w-full inline-flex items-center justify-center gap-2 min-h-[44px] bg-brand text-white rounded-card px-4 text-sm font-semibold disabled:opacity-60"
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
      </section>
    </main>
  );
}
