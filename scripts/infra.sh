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
  scripts/infra.sh up [all|tripmate|kraddr-geo|rustfs|tripmate-postgres|kraddr-geo-postgres]
  scripts/infra.sh stop [all|tripmate|kraddr-geo|rustfs|tripmate-postgres|kraddr-geo-postgres]
  scripts/infra.sh restart [all|tripmate|kraddr-geo|rustfs|tripmate-postgres|kraddr-geo-postgres]
  scripts/infra.sh status [target]
  scripts/infra.sh logs [target]
  scripts/infra.sh config

기본 target은 all이다. python-kraddr-geo에서 필요한 인프라는
`scripts/infra.sh up kraddr-geo`로 기동한다.
EOF
}

services_for_target() {
  case "${1:-all}" in
    all)
      printf '%s\n' postgres kraddr-geo-postgres rustfs rustfs-init
      ;;
    tripmate)
      printf '%s\n' postgres rustfs rustfs-init
      ;;
    kraddr-geo|python-kraddr-geo)
      printf '%s\n' kraddr-geo-postgres rustfs rustfs-init
      ;;
    rustfs)
      printf '%s\n' rustfs rustfs-init
      ;;
    krtour-map|python-krtour-map)
      printf '%s\n' postgres rustfs rustfs-init
      ;;
    postgres|tripmate-postgres)
      printf '%s\n' postgres
      ;;
    kraddr-postgres|kraddr-geo-postgres|kraddr-geo-postgresql)
      printf '%s\n' kraddr-geo-postgres
      ;;
    *)
      echo "unknown target: $1" >&2
      exit 2
      ;;
  esac
}

without_init_services() {
  local service
  for service in "$@"; do
    [[ "$service" == "rustfs-init" ]] || printf '%s\n' "$service"
  done
}

contains_init() {
  local service
  for service in "$@"; do
    [[ "$service" == "rustfs-init" ]] && return 0
  done
  return 1
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

  local target="${1:-all}"
  local services=()
  local runtime_services=()
  mapfile -t services < <(services_for_target "$target")
  mapfile -t runtime_services < <(without_init_services "${services[@]}")

  case "$command" in
    up)
      compose_cmd up -d "${services[@]}"
      ;;
    stop)
      compose_cmd stop "${runtime_services[@]}"
      ;;
    restart)
      compose_cmd restart "${runtime_services[@]}"
      if contains_init "${services[@]}"; then
        compose_cmd up -d rustfs-init
      fi
      ;;
    status|ps)
      compose_cmd ps "${services[@]}"
      ;;
    logs)
      compose_cmd logs -f --tail=100 "${runtime_services[@]}"
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
