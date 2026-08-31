'use client';

import { FormEvent, useState } from 'react';
import { LoaderCircle, LogIn } from 'lucide-react';
import { ApiError, postJson } from '@/lib/api';

export default function LoginScreen({ onLogin }: { onLogin: () => Promise<void> }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
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
    <section className="ops-auth-shell" aria-labelledby="login-title">
      <div className="ops-auth-content">
        <div className="ops-auth-brand">
          <p className="ops-auth-product">Docker Manager UI</p>
          <h1 id="login-title">관리자 로그인</h1>
          <p className="ops-auth-description">Docker 인프라 운영 콘솔에 로그인하세요.</p>
        </div>
        <form aria-busy={busy} className="ops-auth-form" onSubmit={submit}>
          <div className="ops-field">
            <label className="ops-form-label" htmlFor="admin-username">
              아이디
            </label>
            <input
              autoComplete="username"
              className="ops-input"
              disabled={busy}
              id="admin-username"
              value={username}
              aria-describedby="login-error"
              aria-invalid={error ? true : undefined}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>
          <div className="ops-field">
            <label className="ops-form-label" htmlFor="admin-password">
              비밀번호
            </label>
            <input
              autoComplete="current-password"
              className="ops-input"
              disabled={busy}
              id="admin-password"
              type="password"
              value={password}
              aria-describedby="login-error"
              aria-invalid={error ? true : undefined}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <button
            className="ops-button ops-button--primary ops-auth-submit"
            disabled={busy}
            type="submit"
          >
            {busy ? <LoaderCircle className="animate-spin" size={17} /> : <LogIn size={17} />}
            {busy ? '로그인 중…' : '로그인'}
          </button>
          {/* 실패 메시지를 AT에 확실히 알리기 위해 live region을 항상 마운트해 둔다. */}
          <p aria-live="assertive" className="ops-auth-error" id="login-error" role="alert">
            {error}
          </p>
        </form>
        <p className="ops-auth-footer">Docker Manager UI · 운영 관리자 전용</p>
      </div>
    </section>
  );
}
