#!/usr/bin/env sh
#
# kor-travel-geo **전용** database 복구 스크립트.
#
# ADR-37(2026-08-17): PostgreSQL은 프로젝트마다 전용 instance를 쓴다. 이 스크립트는
# `kor-travel-geo-postgres`(:12500) 안의 geo database만 다룬다.
#
# ⚠️ 여기에 다른 프로젝트의 role/database를 **다시 넣지 마라.** 예전에는 이 스크립트가
# 한 cluster 안에 `pinvi` role과 `pinvi`·`kor_travel_concierge`·`krtour_map` database를
# 만들고 owner/grant를 재적용했다. 그 구조 때문에 Map의 principal 경계가 복구 실행마다
# 무음으로 풀렸고(ADR-35), 실제로 Map credential로 33GB `kor_travel_geo`에 접속됐다.
# 각 프로젝트의 provisioning은 자기 instance에서 한다.
#
# ⚠️ 모든 psql/createdb 호출에 `-p`를 준다. 이 컨테이너의 PostgreSQL은 5432가 아니라
# `KOR_TRAVEL_GEO_DB_PORT`(기본 12500)를 듣는다 — 생략하면 연결이 조용히 실패한다.
set -eu

PGPORT_EFFECTIVE="${KOR_TRAVEL_GEO_DB_PORT:-12500}"
PSQL_HOST="127.0.0.1"

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
    if pg_isready -h "$PSQL_HOST" -p "$PGPORT_EFFECTIVE" -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
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
  psql -h "$PSQL_HOST" -p "$PGPORT_EFFECTIVE" -U "$POSTGRES_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname = '$(sql_literal "$1")'" | grep -q 1
}

db_exists() {
  psql -h "$PSQL_HOST" -p "$PGPORT_EFFECTIVE" -U "$POSTGRES_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$(sql_literal "$1")'" | grep -q 1
}

ensure_role() {
  role="$1"
  password="$2"
  require_identifier "$role"
  escaped_password="$(sql_literal "$password")"

  if role_exists "$role"; then
    log "role exists: $role; refreshing password"
    psql -v ON_ERROR_STOP=1 -h "$PSQL_HOST" -p "$PGPORT_EFFECTIVE" -U "$POSTGRES_USER" -d postgres \
      -c "ALTER ROLE $role LOGIN PASSWORD '$escaped_password'"
  else
    log "creating role: $role"
    psql -v ON_ERROR_STOP=1 -h "$PSQL_HOST" -p "$PGPORT_EFFECTIVE" -U "$POSTGRES_USER" -d postgres \
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
    createdb -h "$PSQL_HOST" -p "$PGPORT_EFFECTIVE" -U "$POSTGRES_USER" -O "$owner" "$db"
  fi

  psql -v ON_ERROR_STOP=1 -h "$PSQL_HOST" -p "$PGPORT_EFFECTIVE" -U "$POSTGRES_USER" -d postgres \
    -c "ALTER DATABASE $db OWNER TO $owner"
}

ensure_postgis_db() {
  db="$1"
  owner="$2"
  require_identifier "$db"
  require_identifier "$owner"

  log "ensuring extensions and grants: $db"
  psql -v ON_ERROR_STOP=1 -h "$PSQL_HOST" -p "$PGPORT_EFFECTIVE" -U "$POSTGRES_USER" -d "$db" <<SQL
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
GRANT ALL PRIVILEGES ON SCHEMA public TO $owner;
SQL
}

POSTGRES_USER="${POSTGRES_USER:-addr}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-addr}"
POSTGRES_DB="${POSTGRES_DB:-kor_travel_geo}"
# ADR-37 — pinvi/concierge role·database는 여기서 만들지 않는다.
# 각자 전용 instance(12600/12800)에서 자기 provisioning을 한다.

require_identifier "$POSTGRES_USER"
require_identifier "$POSTGRES_DB"

wait_for_postgres

ensure_database "$POSTGRES_DB" "$POSTGRES_USER"
ensure_postgis_db "$POSTGRES_DB" "$POSTGRES_USER"

log "database recovery complete"
