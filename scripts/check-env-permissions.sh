#!/usr/bin/env sh
# issue #179: `.env`만 이름을 지목하면 `.env.bak.*`/`.env.backup-*` 같은 파생물이
# 검사를 빠져나간다. 원본 이름을 하드코딩하지 않고 `.env*` 전체를 훑되,
# tracked 예시 파일(`.env.example`)만 명시적으로 뺀다.
set -eu

TARGET_DIR="${1:-.}"
FIX="${2:-}"

log() {
  printf '[env-perm-check] %s\n' "$*"
}

violations=0
for f in "$TARGET_DIR"/.env*; do
  [ -e "$f" ] || continue
  base="$(basename "$f")"
  [ "$base" = ".env.example" ] && continue
  perm="$(stat -c '%a' "$f" 2>/dev/null || stat -f '%Lp' "$f")"
  if [ "$perm" != "600" ]; then
    if [ "$FIX" = "--fix" ]; then
      chmod 600 "$f"
      log "fixed $f (was $perm, now 600)"
    else
      log "not 600 ($perm): $f"
      violations=$((violations + 1))
    fi
  fi
done

if [ "$violations" -gt 0 ]; then
  log "$violations file(s) outside 600 permissions. Re-run with --fix to chmod 600, or fix manually."
  exit 1
fi

log "all .env* derivatives (excluding .env.example) are 600"
