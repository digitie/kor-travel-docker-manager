import type { Page } from '@playwright/test';

/** live 시험과 사전 로그인이 함께 쓰는 자격 접근(값은 메시지에 싣지 않는다). */
export function liveEnv(name: 'KTDM_LIVE_USERNAME' | 'KTDM_LIVE_PASSWORD'): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name}가 없습니다`);
  return value;
}

/** 로그인이 실패하면 입력값이 남은 화면이 실패 산출물(접근성 스냅샷)에 담긴다 — 비밀번호 칸을 비운다. */
export async function clearPasswordFields(page: Page) {
  await page.evaluate(() => document.querySelectorAll<HTMLInputElement>('input[type=password]')
    .forEach((input) => { input.value = ''; })).catch(() => undefined);
}
