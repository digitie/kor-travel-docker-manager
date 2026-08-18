#!/usr/bin/env sh
# issue #148/#177: 전용 PostgreSQL 인스턴스의 standalone 백업 wrapper.
# geo application DB role은 kor-travel-geo 앱 레벨 스케줄 백업이 정본이므로
# cron/systemd timer에 넣지 않는다. geo_dagster metadata DB는 별도 백업 대상으로 남긴다.
# cron/systemd timer에서 나머지 role별로 부른다:
#   15 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh geo_dagster     4 >>~/backups/geo_dagster.log 2>&1
#   30 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh concierge       7 >>~/backups/concierge.log 2>&1
#   45 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh map_application 7 >>~/backups/map_application.log 2>&1
#   50 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh map_dagster      7 >>~/backups/map_dagster.log 2>&1
#   55 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh pinvi            7 >>~/backups/pinvi.log 2>&1
set -eu

ROLE="${1:?usage: run-standalone-backup.sh <role> <keep>}"
KEEP="${2:?usage: run-standalone-backup.sh <role> <keep>}"
KTDCTL="${KTDCTL:-backend/ktd_venv/bin/ktdctl}"

log() {
  printf '[%s] [standalone-backup:%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ROLE" "$*"
}

log "creating backup"
"$KTDCTL" db-backup create "$ROLE"
log "gc (keep=$KEEP)"
"$KTDCTL" db-backup gc "$ROLE" --keep "$KEEP"
log "done"
