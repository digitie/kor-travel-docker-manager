#!/usr/bin/env sh
# issue #148/#177: 전용 PostgreSQL 인스턴스의 standalone 백업 wrapper.
# geo application DB role은 kor-travel-geo 앱 레벨 스케줄 백업이 정본이므로
# cron/systemd timer에 넣지 않는다. geo_dagster metadata DB는 별도 백업 대상으로 남긴다.
# cron/systemd timer에서는 H49가 승인한 세 role만 부른다. Map application/Dagster와
# geo application은 각각 #148 정책·geo 앱 백업과 중복되므로 이 wrapper의 주기 대상이 아니다.
# 다음 줄을 crontab에 한 번 넣어 host timezone과 무관하게 UTC로 고정한다:
#   CRON_TZ=UTC
#   15 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh geo_dagster     4 >>~/backups/geo_dagster.log 2>&1
#   30 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh concierge       7 >>~/backups/concierge.log 2>&1
#   55 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh pinvi            7 >>~/backups/pinvi.log 2>&1
set -eu

ROLE="${1:?usage: run-standalone-backup.sh <role> <keep>}"
KEEP="${2:?usage: run-standalone-backup.sh <role> <keep>}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
KTDCTL="${KTDCTL:-$PROJECT_ROOT/backend/.venv/bin/ktdctl}"

case "$ROLE" in
  geo_dagster|concierge|pinvi) ;;
  *)
    printf 'periodic standalone backup is not enabled for role: %s\n' "$ROLE" >&2
    exit 2
    ;;
esac

BACKUP_ROOT="${KTDM_BACKUP_ROOT:?KTDM_BACKUP_ROOT must be set to an absolute path}"
case "$BACKUP_ROOT" in
  /*) ;;
  *)
    printf 'KTDM_BACKUP_ROOT must be absolute\n' >&2
    exit 2
    ;;
esac

if [ ! -x "$KTDCTL" ] && [ -x "$PROJECT_ROOT/backend/ktd_venv/bin/ktdctl" ]; then
  KTDCTL="$PROJECT_ROOT/backend/ktd_venv/bin/ktdctl"
fi
[ -x "$KTDCTL" ] || {
  printf 'ktdctl is not executable: %s\n' "$KTDCTL" >&2
  exit 2
}

log() {
  printf '[%s] [standalone-backup:%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ROLE" "$*"
}

log "creating backup"
"$KTDCTL" db-backup create "$ROLE"
log "gc (keep=$KEEP)"
"$KTDCTL" db-backup gc "$ROLE" --keep "$KEEP"
log "done"
