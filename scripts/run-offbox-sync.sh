#!/usr/bin/env sh
# GM-08: 백업과 pin registry 보존본의 off-box 동기화 wrapper.
# `ktdctl offbox-sync run`은 pin registry 파일이 root 0600이라 root 실행이 필요하다
# (`db-backup create/gc`와 달리 root crontab 또는 sudo로 걸어야 한다).
# 목적지(KTDM_OFFBOX_HOST/USER/REMOTE_ROOT/SSH_KEY/PORT)는 환경마다 다르므로 이
# 저장소가 기본값을 강제하지 않는다 — crontab 라인에서 명시하거나 root의 .env에 둔다.
# 로그 로테이션은 KTDM_BACKUP_ROOT의 다른 role 로그와 같은 방식으로 직접 설정한다
# (trusted installer의 logrotate 렌더링은 GM-03의 백업 role 로그만 다룬다 — 이
# wrapper 로그는 별도로 등록해야 한다).
# 다음 줄을 **root** crontab에 한 번 넣어 host timezone과 무관하게 UTC로 고정한다:
#   CRON_TZ=UTC
#   45 4 * * * KTDM_OFFBOX_HOST=backup-vault.example KTDM_OFFBOX_USER=ktdm-sync \
#     KTDM_OFFBOX_REMOTE_ROOT=/srv/ktdm-offbox KTDM_OFFBOX_SSH_KEY=/etc/ktdm/offbox-sync-key \
#     KTDM_BACKUP_ROOT=/absolute/backup/root \
#     /absolute/path/to/kor-travel-docker-manager/scripts/run-offbox-sync.sh \
#     >>/absolute/backup/root/offbox-sync.log 2>&1
# 03:15/03:30/03:55에 role 백업 생성 cron이 돈다 — 그 role의 rsync 전송이 겹치면
# `_role_lock`이 "another rehearsal/backup is already running"으로 거부한다.
# 04:xx 이후처럼 그 창을 피해서 걸 것.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
KTDCTL="${KTDCTL:-$PROJECT_ROOT/backend/.venv/bin/ktdctl}"

if [ ! -x "$KTDCTL" ] && [ -x "$PROJECT_ROOT/backend/ktd_venv/bin/ktdctl" ]; then
  KTDCTL="$PROJECT_ROOT/backend/ktd_venv/bin/ktdctl"
fi
[ -x "$KTDCTL" ] || {
  printf 'ktdctl is not executable: %s\n' "$KTDCTL" >&2
  exit 2
}

: "${KTDM_OFFBOX_HOST:?KTDM_OFFBOX_HOST must be set — off-box sync destination is not configured}"

log() {
  printf '[%s] [offbox-sync] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

log "syncing to $KTDM_OFFBOX_HOST"
"$KTDCTL" offbox-sync run --json
log "done"
