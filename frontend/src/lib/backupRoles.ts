/** GM-18: `GET /api/v1/backups`가 실어 보내는 role 목록에서 UI 옵션을 파생한다.
 * 이 함수를 거치지 않고 select/생성 버튼이 자체적으로 role을 하드코딩하면, 백엔드
 * 정본(standalone_backup.BACKUP_ROLES)에 새 role이 추가돼도 이 목록만 조용히
 * 구식으로 남는다. */
export function buildBackupRoleOptions(
  roles: readonly string[]
): Array<{ value: string; label: string }> {
  return [{ value: 'all', label: '전체' }, ...roles.map((value) => ({ value, label: value }))];
}
