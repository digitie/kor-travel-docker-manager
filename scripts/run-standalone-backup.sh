#!/usr/bin/env sh
# issue #148/#177: PostgreSQL database별 standalone 백업 wrapper. geo_dagster·concierge·
# pinvi·transport·transport_dagster는 공용 instance(kor-travel-shared-postgres)에 있다.
# geo application DB role은 kor-travel-geo 앱 레벨 스케줄 백업이 정본이므로
# cron/systemd timer에 넣지 않는다. geo_dagster metadata DB는 별도 백업 대상으로 남긴다.
# cron/systemd timer에서는 H49가 승인한 세 role과, 2026-09-28 오너 결정으로 transport
# 저장소의 자체 cron을 대신하는 transport 둘만 부른다. Map application/Dagster와
# geo application은 각각 #148 정책·geo 앱 백업과 중복되므로 이 wrapper의 주기 대상이 아니다.
# transport application DB(약 13 GB, dump 약 1 GB·약 9분)는 3일마다 02:15 KST(17:15 UTC)에
# 뜬다 — transport 자신의 Dagster job(매시 :00, 03:00·03:30 KST reference, ferry :45)을 피한 자리다.
# 디스크 여유는 ktdctl이 role마다 시작 전에 확인하고, 모자라면 시작하지 않고 exit 2다.
# 아래 `>>` append 로그의 로테이션은 trusted installer가 .env의 KTDM_BACKUP_ROOT로
# /etc/logrotate.d/kor-travel-docker-manager를 렌더링해 설치한다(GM-03). 로테이션을
# 원하면 KTDM_BACKUP_ROOT를 **crontab 라인뿐 아니라 .env에도** 선언해야 한다 —
# installer는 .env만 읽는다. **.env에 그 키가 없으면 로테이션이 없고**(installer가 경고 한
# 줄만 남긴다), backend(UI)도 cron과 다른 root(backend 계정의 `~/backups`)를 읽어 신선도
# 배지가 cron dump를 보지 못한다 — 2026-09-28 n150이 그 상태다(docs/docker-management.md
# "PostgreSQL 백업" 참고). 공유 백업 디렉터리(chgrp+2770)를 쓰면 KTDM_BACKUP_SHARED_GROUP을
# **두 곳에** 선언한다: .env(backend, 그리고 logrotate가 group-writable 부모를 거부하지 않게)와
# 이 crontab(CRON_TZ=UTC 아래 환경 줄 `KTDM_BACKUP_SHARED_GROUP=<group>` — ktdctl은 .env를 읽지
# 않는다). backend와 cron이 다른 계정으로 같은 root를 쓰면(n150) 공유 그룹 없이는 UI 생성이
# 409로 거절된다(cron의 role 디렉터리를 망가뜨리지 않게). UI로도 만들 때만 공유 그룹을 한다.
# crontab이 가리키는 체크아웃은 설치본(/opt)과 별개다 — 설치 뒤 backend/src·
# config/docker-targets.yml·이 파일을 함께 맞춘다(docs/docker-management.md "PostgreSQL 백업").
# 다음 줄을 crontab에 한 번 넣어 host timezone과 무관하게 UTC로 고정한다:
#   CRON_TZ=UTC
#   15 3 * * * KTDM_BACKUP_ROOT=/absolute/backup/root /absolute/path/to/kor-travel-docker-manager/scripts/run-standalone-backup.sh geo_dagster 4 >>/absolute/backup/root/geo_dagster.log 2>&1
#   30 3 * * * KTDM_BACKUP_ROOT=/absolute/backup/root /absolute/path/to/kor-travel-docker-manager/scripts/run-standalone-backup.sh concierge 7 >>/absolute/backup/root/concierge.log 2>&1
#   55 3 * * * KTDM_BACKUP_ROOT=/absolute/backup/root /absolute/path/to/kor-travel-docker-manager/scripts/run-standalone-backup.sh pinvi 7 >>/absolute/backup/root/pinvi.log 2>&1
#   50 16 * * * KTDM_BACKUP_ROOT=/absolute/backup/root /absolute/path/to/kor-travel-docker-manager/scripts/run-standalone-backup.sh transport_dagster 7 >>/absolute/backup/root/transport_dagster.log 2>&1
#   15 17 */3 * * KTDM_BACKUP_ROOT=/absolute/backup/root /absolute/path/to/kor-travel-docker-manager/scripts/run-standalone-backup.sh transport 3 >>/absolute/backup/root/transport.log 2>&1
set -eu

ROLE="${1:?usage: run-standalone-backup.sh <role> <keep>}"
KEEP="${2:?usage: run-standalone-backup.sh <role> <keep>}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
KTDCTL="${KTDCTL:-$PROJECT_ROOT/backend/.venv/bin/ktdctl}"

case "$ROLE" in
  geo_dagster|concierge|pinvi|transport|transport_dagster) ;;
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
