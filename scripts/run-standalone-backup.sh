#!/usr/bin/env sh
# issue #148/#177: 네 전용 PostgreSQL 인스턴스(geo/concierge/map/pinvi) 공통 주기 백업.
# cron/systemd timer에서 role별로 부른다:
#   0 3 * * *  cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh geo            2 >>~/backups/geo.log 2>&1
#   15 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh geo_dagster     4 >>~/backups/geo_dagster.log 2>&1
#   30 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh concierge       7 >>~/backups/concierge.log 2>&1
#   45 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh map_application 7 >>~/backups/map_application.log 2>&1
#   50 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh map_dagster      7 >>~/backups/map_dagster.log 2>&1
#   55 3 * * * cd ~/kor-travel-docker-manager && scripts/run-standalone-backup.sh pinvi            7 >>~/backups/pinvi.log 2>&1
#
# geo는 33GB급이라 `--keep` 값을 낮게 둔다 — dump 세대를 map만큼 쌓을 디스크
# 여유가 없다(issue #177 실측: 남은 122G, 백업만으로 이미 7.8G 사용 중).
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
