// 설정 변경 모달에서 제출 전 "무엇이 바뀌는지"를 보여 주기 위한 순수 diff 계산.

export type ListDiff = { added: string[]; removed: string[]; changed: boolean };

export function diffList(before: string[], after: string[]): ListDiff {
  const added = after.filter((item) => !before.includes(item));
  const removed = before.filter((item) => !after.includes(item));
  return { added, removed, changed: added.length > 0 || removed.length > 0 };
}

export type EnvDiffRow = {
  key: string;
  before?: string;
  after?: string;
  kind: 'added' | 'removed' | 'changed' | 'same';
};

export function diffEnv(
  before: Record<string, string>,
  after: Record<string, string>
): EnvDiffRow[] {
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
  return keys.map((key): EnvDiffRow => {
    const b = before[key];
    const a = after[key];
    if (b === undefined) return { key, after: a, kind: 'added' };
    if (a === undefined) return { key, before: b, kind: 'removed' };
    if (a !== b) return { key, before: b, after: a, kind: 'changed' };
    return { key, before: b, after: a, kind: 'same' };
  });
}
