#!/usr/bin/env bash
# 프론트엔드 빌드 툴체인 무결성 검사 (배포 preflight).
#
# 배경: 운영 호스트에서 frontend/node_modules 가 부분 설치 상태로 남아 있는 것을 발견했다
# (최상위 패키지는 있는데 node_modules/.bin 이 비어 `next: not found`). 이 상태에서는
# `npm run build` 가 실패하는데, 그때는 이미 배포 절차가 시작된 뒤라 실행 중인 서버를
# 내렸다면 복구할 빌드 산출물이 없다.
#
# 그래서 **서버를 건드리기 전에** 이 스크립트로 먼저 걸러 낸다. rsync 는 node_modules 를
# 동기화하지 않으므로(대상 호스트에서 직접 설치) 이 검사는 배포 호스트에서 실행해야 한다.
#
# 사용:
#   scripts/verify-frontend-toolchain.sh            # 검사만
#   scripts/verify-frontend-toolchain.sh --fix      # 깨져 있으면 npm ci 로 복구 후 재검사
#
# 종료 코드: 0 정상 / 1 툴체인 손상(또는 복구 실패)

set -euo pipefail

FIX=0
[ "${1:-}" = "--fix" ] && FIX=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/../frontend" && pwd)"
cd "$FRONTEND_DIR"

fail() { echo "❌ $*" >&2; }
ok() { echo "✅ $*"; }

problems=0

check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    ok "$label"
  else
    fail "$label"
    problems=$((problems + 1))
  fi
}

run_checks() {
  problems=0
  check "package.json 존재" test -f package.json
  check "package-lock.json 존재" test -f package-lock.json
  check "node_modules 디렉터리 존재" test -d node_modules
  # 핵심 판정: 부분 설치는 최상위 패키지가 있어도 .bin 이 비어 있다.
  check "node_modules/.bin/next 실행 가능" test -x node_modules/.bin/next
  check "next 패키지 존재" test -d node_modules/next
  check "빌드에 필요한 typescript 존재" test -d node_modules/typescript
  return 0
}

echo "== frontend 툴체인 검사: $FRONTEND_DIR =="
run_checks

if [ "$problems" -eq 0 ]; then
  echo
  ok "툴체인 정상 — 빌드를 진행해도 된다."
  exit 0
fi

echo
fail "툴체인 손상 항목 ${problems}건."

if [ "$FIX" -ne 1 ]; then
  cat >&2 <<'MSG'

실행 중인 서버를 내리기 전에 먼저 복구할 것:
  cd frontend && npm ci

또는 이 스크립트를 --fix 로 다시 실행한다:
  scripts/verify-frontend-toolchain.sh --fix
MSG
  exit 1
fi

echo
echo "== --fix: npm ci 로 복구 시도 =="
if ! npm ci --no-audit --no-fund; then
  fail "npm ci 실패 — 수동 확인이 필요하다. 실행 중인 서버는 내리지 말 것."
  exit 1
fi

echo
echo "== 복구 후 재검사 =="
run_checks
if [ "$problems" -ne 0 ]; then
  fail "복구 후에도 손상 항목 ${problems}건 — 실행 중인 서버는 내리지 말 것."
  exit 1
fi

echo
ok "복구 완료 — 빌드를 진행해도 된다."
