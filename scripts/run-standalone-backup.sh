#!/usr/bin/env sh
# issue #148/#177: 전용 PostgreSQL 인스턴스의 standalone 백업 wrapper.
# geo application DB role은 kor-travel-geo 앱 레벨 스케줄 백업이 정본이므로
# cron/systemd timer에 넣지 않는다. geo_dagster metadata DB는 별도 백업 대상으로 남긴다.
# cron/systemd timer에서는 H49가 승인한 세 role만 부른다. Map application/Dagster와
# geo application은 각각 #148 정책·geo 앱 백업과 중복되므로 이 wrapper의 주기 대상이 아니다.
# 아래 `>>` append 로그의 로테이션은 trusted installer가 .env의 KTDM_BACKUP_ROOT로
# /etc/logrotate.d/kor-travel-docker-manager를 렌더링해 설치한다(GM-03). 로테이션을
# 원하면 KTDM_BACKUP_ROOT를 **crontab 라인뿐 아니라 .env에도** 선언해야 한다 —
# installer는 .env만 읽는다. 공유 백업 디렉터리(chgrp+2770)를 쓰면 .env에
# KTDM_BACKUP_SHARED_GROUP도 선언해야 logrotate가 group-writable 부모를 거부하지 않는다.
# 다음 줄을 crontab에 한 번 넣어 host timezone과 무관하게 UTC로 고정한다:
#   CRON_TZ=UTC
#   15 3 * * * KTDM_BACKUP_ROOT=/absolute/backup/root /absolute/path/to/kor-travel-docker-manager/scripts/run-standalone-backup.sh geo_dagster 4 >>/absolute/backup/root/geo_dagster.log 2>&1
#   30 3 * * * KTDM_BACKUP_ROOT=/absolute/backup/root /absolute/path/to/kor-travel-docker-manager/scripts/run-standalone-backup.sh concierge 7 >>/absolute/backup/root/concierge.log 2>&1
#   55 3 * * * KTDM_BACKUP_ROOT=/absolute/backup/root /absolute/path/to/kor-travel-docker-manager/scripts/run-standalone-backup.sh pinvi 7 >>/absolute/backup/root/pinvi.log 2>&1
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
