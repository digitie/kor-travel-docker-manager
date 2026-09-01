import { describe, expect, it } from 'vitest';

import { buildBackupRoleOptions } from './backupRoles';

describe('buildBackupRoleOptions', () => {
  it('빈 role 목록이면 전체 옵션만 남는다', () => {
    expect(buildBackupRoleOptions([])).toEqual([{ value: 'all', label: '전체' }]);
  });

  it('백엔드가 보낸 role을 그대로 옵션으로 파생한다 — 하드코딩된 목록이 아니다', () => {
    // GM-18 리뷰 반영: 하드코딩된 옛 ROLE_OPTIONS 배열로 되돌아가는 회귀가 있다면
    // 그 배열에 없는 이 canary 값이 여기서 사라져 테스트가 실패해야 한다.
    const out = buildBackupRoleOptions(['geo', 'zzz_new_backup_role']);

    expect(out).toEqual([
      { value: 'all', label: '전체' },
      { value: 'geo', label: 'geo' },
      { value: 'zzz_new_backup_role', label: 'zzz_new_backup_role' },
    ]);
  });

  it("'전체' 옵션은 항상 맨 앞에 오고 role 순서를 그대로 보존한다", () => {
    const out = buildBackupRoleOptions(['pinvi', 'geo']);
    expect(out.map((option) => option.value)).toEqual(['all', 'pinvi', 'geo']);
  });
});
