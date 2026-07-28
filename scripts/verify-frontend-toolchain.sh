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
#   scripts/verify-frontend-toolchain.sh            # 검사만 (비파괴)
#   scripts/verify-frontend-toolchain.sh --fix      # 깨져 있으면 npm ci 로 복구 후 재검사
#
# ⚠️ --fix 는 파괴적이다. `npm ci` 는 기존 node_modules 를 **먼저 지우고** 설치한다.
#    `next start` 는 route 번들과 그 require 를 요청 시점에 lazy 로 해석하므로, 실행 중인
#    서버가 있는 상태에서 --fix 를 돌리면 그 서버의 의존성 트리가 사라진다. 설치가 실패하면
#    실행 중인 서버와 복구 경로가 동시에 없어진다.
#    → --fix 는 **서버를 내린 뒤**, 또는 아직 서비스 중이 아닌 호스트에서만 쓴다.
#      검사만 할 때는 인자 없이 실행한다(비파괴).
#
# 종료 코드: 0 정상 / 1 툴체인 손상(또는 복구 실패) / 2 잘못된 사용법

set -euo pipefail

FIX=0
for arg in "$@"; do
  case "$arg" in
    --fix) FIX=1 ;;
    *)
      echo "사용법: $(basename "$0") [--fix]" >&2
      echo "  알 수 없는 인자: $arg" >&2
      exit 2
      ;;
  esac
done

# 심볼릭 링크로 호출돼도 저장소의 frontend/ 를 보도록 실제 경로를 먼저 푼다.
# (풀지 않으면 링크 옆에 있는 무관한 frontend/ 를 검사하고 0을 반환할 수 있다.)
_self="${BASH_SOURCE[0]}"
if command -v readlink >/dev/null 2>&1 && readlink -f "$_self" >/dev/null 2>&1; then
  _self="$(readlink -f "$_self")"
fi
SCRIPT_DIR="$(cd "$(dirname "$_self")" && pwd)"
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
  # 빠른 사전 판정: 부분 설치는 최상위 패키지가 있어도 .bin 이 비어 있다.
  check "node_modules/.bin/next 실행 가능" test -x node_modules/.bin/next
  # 결정적 판정: 개별 경로를 찍어 보는 방식으로는 부분 설치를 못 잡는다. 실제로
  # next/typescript 는 있는데 react·react-dom·tailwindcss 가 없는 트리가 위 검사들을
  # 모두 통과한다(적대적 리뷰에서 재현). npm ls 는 최상위 의존성의 누락·불일치를
  # 네트워크 없이 종료 코드로 알려 주므로 이 검사가 진짜 게이트다.
  check "의존성 트리 정합(npm ls --depth=0)" npm ls --depth=0
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
