#!/usr/bin/env sh
set -eu

log() {
  printf '[db-init] %s\n' "$*"
}

require_identifier() {
  case "$1" in
    *[!A-Za-z0-9_]*|'')
      echo "invalid identifier: $1" >&2
      exit 2
      ;;
  esac
}

wait_for_postgres() {
  i=0
  while [ "$i" -lt "${POSTGRES_WAIT_RETRIES:-60}" ]; do
    if pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
      return 0
    fi
    i=$((i + 1))
    sleep 2
  done
  echo "postgres did not become ready in time" >&2
  return 1
}

sql_literal() {
  printf "%s" "$1" | sed "s/'/''/g"
}

role_exists() {
  psql -U "$POSTGRES_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname = '$(sql_literal "$1")'" | grep -q 1
}

db_exists() {
  psql -U "$POSTGRES_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$(sql_literal "$1")'" | grep -q 1
}

ensure_role() {
  role="$1"
  password="$2"
  require_identifier "$role"
  escaped_password="$(sql_literal "$password")"

  if role_exists "$role"; then
    log "role exists: $role; refreshing password"
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
      -c "ALTER ROLE $role LOGIN PASSWORD '$escaped_password'"
  else
    log "creating role: $role"
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
      -c "CREATE ROLE $role LOGIN PASSWORD '$escaped_password'"
  fi
}

ensure_database() {
  db="$1"
  owner="$2"
  require_identifier "$db"
  require_identifier "$owner"

  if db_exists "$db"; then
    log "database exists: $db"
  else
    log "creating database: $db owner=$owner"
    createdb -U "$POSTGRES_USER" -O "$owner" "$db"
  fi

  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
    -c "ALTER DATABASE $db OWNER TO $owner"
}

ensure_postgis_db() {
  db="$1"
  owner="$2"
  require_identifier "$db"
  require_identifier "$owner"

  log "ensuring extensions and grants: $db"
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db" <<SQL
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
GRANT ALL PRIVILEGES ON SCHEMA public TO $owner;
SQL
}

ensure_krtour_map_db() {
  db="$1"
  owner="$2"
  require_identifier "$db"
  require_identifier "$owner"

  log "ensuring krtour_map schemas/extensions"
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db" <<SQL
CREATE SCHEMA IF NOT EXISTS feature;
CREATE SCHEMA IF NOT EXISTS provider_sync;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS x_extension;
CREATE EXTENSION IF NOT EXISTS postgis SCHEMA x_extension;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA x_extension;
CREATE EXTENSION IF NOT EXISTS pgcrypto SCHEMA x_extension;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
ALTER DATABASE $db SET search_path = public, x_extension;
GRANT ALL PRIVILEGES ON SCHEMA public TO $owner;
GRANT ALL PRIVILEGES ON SCHEMA feature TO $owner;
GRANT ALL PRIVILEGES ON SCHEMA provider_sync TO $owner;
GRANT ALL PRIVILEGES ON SCHEMA ops TO $owner;
GRANT ALL PRIVILEGES ON SCHEMA x_extension TO $owner;
SQL
}

POSTGRES_USER="${POSTGRES_USER:-addr}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-addr}"
POSTGRES_DB="${POSTGRES_DB:-kor_travel_geo}"
TRIPMATE_POSTGRES_USER="${TRIPMATE_POSTGRES_USER:-tripmate}"
TRIPMATE_POSTGRES_PASSWORD="${TRIPMATE_POSTGRES_PASSWORD:-tripmate_dev_password}"
TRIPMATE_POSTGRES_DB="${TRIPMATE_POSTGRES_DB:-tripmate}"
KOR_TRAVEL_CONCIERGE_POSTGRES_DB="${KOR_TRAVEL_CONCIERGE_POSTGRES_DB:-kor_travel_concierge}"
KRTOUR_MAP_POSTGRES_USER="${KRTOUR_MAP_POSTGRES_USER:-krtour_map}"
KRTOUR_MAP_POSTGRES_PASSWORD="${KRTOUR_MAP_POSTGRES_PASSWORD:-krtour_map_dev_password}"
KRTOUR_MAP_POSTGRES_DB="${KRTOUR_MAP_POSTGRES_DB:-krtour_map}"
KRTOUR_MAP_DAGSTER_POSTGRES_DB="${KRTOUR_MAP_DAGSTER_POSTGRES_DB:-krtour_map_dagster}"

require_identifier "$POSTGRES_USER"
require_identifier "$POSTGRES_DB"

wait_for_postgres

ensure_role "$TRIPMATE_POSTGRES_USER" "$TRIPMATE_POSTGRES_PASSWORD"
ensure_role "$KRTOUR_MAP_POSTGRES_USER" "$KRTOUR_MAP_POSTGRES_PASSWORD"

ensure_database "$POSTGRES_DB" "$POSTGRES_USER"
ensure_database "$TRIPMATE_POSTGRES_DB" "$TRIPMATE_POSTGRES_USER"
ensure_database "$KOR_TRAVEL_CONCIERGE_POSTGRES_DB" "$POSTGRES_USER"
ensure_database "$KRTOUR_MAP_POSTGRES_DB" "$KRTOUR_MAP_POSTGRES_USER"
ensure_database "$KRTOUR_MAP_DAGSTER_POSTGRES_DB" "$KRTOUR_MAP_POSTGRES_USER"

ensure_postgis_db "$POSTGRES_DB" "$POSTGRES_USER"
ensure_postgis_db "$TRIPMATE_POSTGRES_DB" "$TRIPMATE_POSTGRES_USER"
ensure_postgis_db "$KOR_TRAVEL_CONCIERGE_POSTGRES_DB" "$POSTGRES_USER"
ensure_krtour_map_db "$KRTOUR_MAP_POSTGRES_DB" "$KRTOUR_MAP_POSTGRES_USER"

log "database recovery complete"
