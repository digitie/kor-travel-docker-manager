import type { HumanError } from './errors';

export type ToastItem = {
  id: number;
  tone: 'success' | 'error';
  title: string;
  hint?: string;
  /** 실패 원문. 접어 두고 필요할 때만 편다. */
  raw?: string;
};

let nextToastId = 1;

export function successToast(title: string, hint?: string): ToastItem {
  return { id: nextToastId++, tone: 'success', title, hint };
}

export function errorToast(error: HumanError): ToastItem {
  return {
    id: nextToastId++,
    tone: 'error',
    title: error.title,
    hint: error.hint,
    raw: error.raw,
  };
}
