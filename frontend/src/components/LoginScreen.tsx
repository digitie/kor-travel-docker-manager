'use client';

import { useState } from 'react';
import { LoginForm, type LoginSubmission } from '@kor-travel/ui/login-form';
import { ApiError, postJson } from '@/lib/api';

export default function LoginScreen({ onLogin }: { onLogin: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit({ credentials }: LoginSubmission) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await postJson('/api/v1/auth/login', { ...credentials, next: '/' });
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

  return <section className="ops-auth-shell" aria-label="관리자 로그인">
    <div className="ops-auth-content">
      <LoginForm brand="Docker Manager UI" description="Docker 인프라 운영 콘솔에 로그인하세요."
        defaultUsername="admin" pending={busy} error={error} onClearError={() => setError(null)}
        onSubmit={submit} footer="Docker Manager UI · 운영 관리자 전용" />
    </div>
  </section>;
}
