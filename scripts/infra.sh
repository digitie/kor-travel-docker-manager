#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${TRIPMATE_MANAGER_COMPOSE_FILE:-$ROOT_DIR/docker-compose.yml}"
ENV_FILE="${TRIPMATE_MANAGER_ENV_FILE:-$ROOT_DIR/.env}"

compose_cmd() {
  if [[ -f "$ENV_FILE" ]]; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
  else
    docker compose -f "$COMPOSE_FILE" "$@"
  fi
}

usage() {
  cat <<'EOF'
Usage:
  scripts/infra.sh [db|storage|geo|map|ai|main] [--build] [--recreate]
  scripts/infra.sh up [target] [--build] [--recreate]
  scripts/infra.sh ensure [target] [--build] [--recreate]
  scripts/infra.sh stop [target]
  scripts/infra.sh restart [target]
  scripts/infra.sh status [target]
  scripts/infra.sh logs [target] [--follow]
  scripts/infra.sh config

기본 target은 all이다. target은 db, storage, geo, map, ai, main 순서로 의존성을 확장한다.
새 작업에서는 Python CLI `tmctl <alias>` 사용을 권장한다.
EOF
}

services_for_target() {
  case "${1:-all}" in
    db|postgresql|postgres|shared-postgresql|kraddr-postgres|kraddr-geo-postgres|kraddr-geo-postgresql)
      printf '%s\n' kraddr-geo-postgres
      ;;
    storage|rustfs|s3|object-storage)
      printf '%s\n' kraddr-geo-postgres
      printf '%s\n' rustfs
      ;;
    all|geo|kraddr-geo|python-kraddr-geo|map|krtour-map|python-krtour-map|ai|tripmate-agent|agent|main|tripmate|tripmate-api|tripmate-web)
      printf '%s\n' kraddr-geo-postgres
      printf '%s\n' rustfs
      printf '%s\n' kraddr-geo-api
      printf '%s\n' kraddr-geo-ui
      ;;
    *)
      echo "unknown target: $1" >&2
      exit 2
      ;;
  esac
}

target_rank() {
  case "${1:-all}" in
    db|postgresql|postgres|shared-postgresql|kraddr-postgres|kraddr-geo-postgres|kraddr-geo-postgresql)
      echo 1
      ;;
    storage|rustfs|s3|object-storage)
      echo 2
      ;;
    geo|kraddr-geo|python-kraddr-geo)
      echo 3
      ;;
    map|krtour-map|python-krtour-map)
      echo 4
      ;;
    ai|tripmate-agent|agent)
      echo 5
      ;;
    main|tripmate|tripmate-api|tripmate-web|all)
      echo 6
      ;;
    *)
      echo 0
      ;;
  esac
}

run_init_steps() {
  local target="$1"
  local rank
  rank="$(target_rank "$target")"

  if [[ "$rank" -ge 1 ]]; then
    compose_cmd exec -T kraddr-geo-postgres sh /opt/tripmate-manager/ensure-kraddr-geo-db.sh
  fi
  if [[ "$rank" -ge 2 ]]; then
    compose_cmd run --rm rustfs-init
  fi
  if [[ "$rank" -ge 3 ]]; then
    compose_cmd exec -T kraddr-geo-postgres sh /opt/tripmate-manager/verify-kraddr-geo-source.sh
  fi
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found" >&2
    exit 127
  fi
}

main() {
  require_docker
  local command="${1:-}"
  [[ -n "$command" ]] || { usage; exit 2; }
  shift || true

  case "$command" in
    db|storage|geo|map|ai|main)
      set -- "$command" "$@"
      command="ensure"
      ;;
  esac

  local target="${1:-all}"
  if [[ "$target" == --* ]]; then
    target="all"
  else
    shift || true
  fi

  local compose_args=()
  local follow_logs=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --build)
        compose_args+=(--build)
        ;;
      --recreate)
        compose_args+=(--force-recreate)
        ;;
      --follow|-f)
        follow_logs=true
        ;;
      *)
        echo "unknown option: $1" >&2
        usage
        exit 2
        ;;
    esac
    shift
  done

  local services=()
  mapfile -t services < <(services_for_target "$target")

  case "$command" in
    up|ensure)
      compose_cmd up -d "${compose_args[@]}" "${services[@]}"
      run_init_steps "$target"
      ;;
    stop)
      compose_cmd stop "${services[@]}"
      ;;
    restart)
      compose_cmd restart "${services[@]}"
      run_init_steps "$target"
      ;;
    status|ps)
      compose_cmd ps "${services[@]}"
      ;;
    logs)
      if [[ "$follow_logs" == true ]]; then
        compose_cmd logs -f --tail=100 "${services[@]}"
      else
        compose_cmd logs --tail=100 "${services[@]}"
      fi
      ;;
    config)
      compose_cmd config
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      echo "unknown command: $command" >&2
      usage
      exit 2
      ;;
  esac
}

main "$@"
