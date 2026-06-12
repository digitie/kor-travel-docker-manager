#!/usr/bin/env sh
set -eu

log() {
  printf '[geo-check] %s\n' "$*"
}

POSTGRES_USER="${POSTGRES_USER:-addr}"
POSTGRES_DB="${POSTGRES_DB:-kor_travel_geo}"
SOURCE_DIR="${KOR_TRAVEL_GEO_SOURCE_DIR:-/data/juso}"
STRICT="${KOR_TRAVEL_GEO_STRICT_SOURCE_CHECK:-1}"

i=0
while [ "$i" -lt "${POSTGRES_WAIT_RETRIES:-60}" ]; do
  if pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 2
done

if [ "$i" -ge "${POSTGRES_WAIT_RETRIES:-60}" ]; then
  echo "postgres did not become ready in time" >&2
  exit 1
fi

if [ ! -d "$SOURCE_DIR" ]; then
  echo "source directory is not mounted: $SOURCE_DIR" >&2
  exit 1
fi

source_file_count="$(find "$SOURCE_DIR" -maxdepth 3 -type f 2>/dev/null | wc -l | tr -d ' ')"
log "source file count under $SOURCE_DIR: $source_file_count"
if [ "$source_file_count" = "0" ]; then
  echo "source directory is empty: $SOURCE_DIR" >&2
  exit 1
fi

table_count() {
  table="$1"
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
    "SELECT CASE WHEN to_regclass('$table') IS NULL THEN -1 ELSE (SELECT count(*) FROM $table) END"
}

missing=0
for table in load_manifest tl_juso_text mv_geocode_target; do
  count="$(table_count "$table" | tr -d '[:space:]')"
  case "$count" in
    -1)
      log "$table: missing"
      missing=1
      ;;
    0)
      log "$table: empty"
      missing=1
      ;;
    *)
      log "$table: $count rows"
      ;;
  esac
done

if [ "$missing" = "1" ]; then
  cat >&2 <<'EOF'
Kor Travel Geo source DB is not fully loaded.
Run the full-load or restore flow from kor-travel-geo, for example:
  PLAN_ONLY=1 bash scripts/fullload_test.sh
  bash scripts/fullload_test.sh
or restore the T-027 backup before using the geo target.
Set KOR_TRAVEL_GEO_STRICT_SOURCE_CHECK=0 only when an empty DB is intentional.
EOF
  if [ "$STRICT" = "1" ]; then
    exit 1
  fi
fi

log "geo source verification complete"
