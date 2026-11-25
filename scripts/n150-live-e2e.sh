#!/usr/bin/env bash
# 운영 Manager 대시보드 live e2e 러너(n150). 절차·자격 파일 작성법은 docs/live-e2e.md가 정본이다.
#
# - 자격(아이디·비밀번호·대상 URL)은 root 소유 0600 파일에서만 읽는다. 값은 출력하지 않고,
#   Playwright 컨테이너에는 환경변수 이름만 넘긴다(docker argv에 값이 실리지 않는다).
# - 읽기 전용 live 설정(frontend/playwright.live.config.ts)만 돌린다. 쓰기 스펙은
#   KTDM_LIVE_E2E_WRITE=1 없이는 수집조차 되지 않는다. Manager 세션은 컨테이너를 멈추고 지울 수
#   있는 관리자 권한이라, 읽기 전용 시험은 로그인·로그아웃 밖의 변경 요청을 브라우저에서 막는다.
# - n150 test gate(~/gate-slot.sh)를 거쳐 docker를 띄운다.
if [[ $- == *x* ]]; then
  echo "error: bash -x(xtrace)로 실행하지 마세요 — 자격 값이 터미널에 찍힐 수 있습니다" >&2
  exit 2
fi
set -euo pipefail
umask 077

usage() {
  cat <<'USAGE'
사용법:
  scripts/n150-live-e2e.sh [--list] [--project desktop|mobile] [--grep 패턴] [--grep-invert 패턴] [--max-failures N]

환경변수(모두 선택, 값은 비밀이 아니다):
  KTDM_LIVE_E2E_CREDENTIALS  자격 파일 경로(기본 /root/.ktdm-e2e.env, root 소유 0600)
  KTDM_LIVE_E2E_GATE         test gate 실행 파일(기본 ~/gate-slot.sh)
  KTDM_LIVE_E2E_IMAGE        Playwright 이미지(기본 frontend/package.json 버전의 공식 noble 이미지)
  KTDM_LIVE_E2E_SKIP_NPM_CI  1이면 named volume의 node_modules를 재사용한다
  KTDM_LIVE_E2E_WRITE        1이면 쓰기 스펙(*.write.live.spec.ts)도 수집한다(기본 제외)
  KTDM_LIVE_EXPECT_HOST      수집 경보 패널이 보고해야 할 plane host(기본 n150)

자격 파일에 허용하는 키(모두 필수): KTDM_LIVE_WEB_URL(대시보드 공개 https origin — prod 번들의
backend 주소·CORS·cookie가 그 origin 기준이라 loopback으로는 로그인하지 못한다),
KTDM_LIVE_USERNAME, KTDM_LIVE_PASSWORD.
USAGE
}

die() {
  echo "error: $*" >&2
  exit 2
}

pw_args=()
list_only=0
while (($#)); do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --list) pw_args+=(--list); list_only=1; shift ;;
    --project)
      (($# >= 2)) || die "$1에는 값이 필요합니다"
      [[ "$2" == desktop || "$2" == mobile ]] || die "--project는 desktop 또는 mobile입니다"
      pw_args+=("$1" "$2"); shift 2 ;;
    --project=desktop | --project=mobile) pw_args+=("$1"); shift ;;
    --grep | -g | --grep-invert | --max-failures)
      (($# >= 2)) || die "$1에는 값이 필요합니다"
      pw_args+=("$1" "$2"); shift 2 ;;
    --grep=* | --grep-invert=* | --max-failures=*) pw_args+=("$1"); shift ;;
    *) die "허용하지 않는 인자입니다: $1 (설정 파일·reporter·workers는 바꿀 수 없습니다 — --help 참고)" ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
frontend_dir="$repo_root/frontend"
[[ -f "$frontend_dir/playwright.live.config.ts" ]] || die "frontend/playwright.live.config.ts가 없습니다: $repo_root"

cred_file="${KTDM_LIVE_E2E_CREDENTIALS:-/root/.ktdm-e2e.env}"
[[ "$cred_file" == /* ]] || die "KTDM_LIVE_E2E_CREDENTIALS는 절대 경로여야 합니다"
gate="${KTDM_LIVE_E2E_GATE:-$HOME/gate-slot.sh}"
[[ -x "$gate" ]] || die "test gate 실행 파일이 없습니다: $gate (n150의 ~/gate-slot.sh를 거쳐야 합니다)"
docker_bin="$(command -v docker || true)"
[[ -n "$docker_bin" ]] || die "docker가 없습니다"

write_opt_in="${KTDM_LIVE_E2E_WRITE:-0}"
[[ "$write_opt_in" == 0 || "$write_opt_in" == 1 ]] || die "KTDM_LIVE_E2E_WRITE는 0 또는 1이어야 합니다"
skip_npm_ci="${KTDM_LIVE_E2E_SKIP_NPM_CI:-0}"
[[ "$skip_npm_ci" == 0 || "$skip_npm_ci" == 1 ]] || die "KTDM_LIVE_E2E_SKIP_NPM_CI는 0 또는 1이어야 합니다"
expect_host="${KTDM_LIVE_EXPECT_HOST:-n150}"
[[ "$expect_host" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || die "KTDM_LIVE_EXPECT_HOST는 소문자 host 이름이어야 합니다"

# root 권한으로 자격 파일을 검사한 뒤 내용을 stdout으로만 낸다. 파일은 root 소유 일반 파일(심볼릭
# 링크 아님)이고 mode가 0600 또는 0400이어야 하며, 담은 디렉터리도 root 소유에 group·other 쓰기가
# 없어야 한다. 실패 사유에는 uid·mode만 싣고 내용은 싣지 않는다.
root_reader='f=$1
d=$(dirname -- "$f")
if [ -L "$f" ] || [ ! -f "$f" ]; then
  echo "error: 자격 파일이 없거나 일반 파일이 아닙니다: $f" >&2; exit 3
fi
fm=$(stat -c "%u %a" -- "$f") || exit 3
dm=$(stat -c "%u %a" -- "$d") || exit 3
case "$fm" in
  "0 600" | "0 400") ;;
  *) echo "error: 자격 파일은 root 소유·0600이어야 합니다(현재 uid mode: $fm): $f" >&2; exit 4 ;;
esac
case "$dm" in
  "0 "*) ;;
  *) echo "error: 자격 파일 디렉터리는 root 소유여야 합니다(현재 uid mode: $dm): $d" >&2; exit 4 ;;
esac
if [ $(( 0${dm#* } & 022 )) -ne 0 ]; then
  echo "error: 자격 파일 디렉터리에 group·other 쓰기 권한이 있습니다(mode ${dm#* }): $d" >&2; exit 4
fi
exec cat -- "$f"'

if ((EUID == 0)); then
  reader=(/bin/sh -c "$root_reader" live-e2e-credentials "$cred_file")
else
  sudo -n true 2>/dev/null || die "자격 파일은 root로 읽습니다 — 먼저 'sudo -v'로 sudo 인증을 갱신하세요"
  reader=(sudo -n -- /bin/sh -c "$root_reader" live-e2e-credentials "$cred_file")
fi
credentials="$("${reader[@]}")" || exit $?

declare -A cred=()
lineno=0
while IFS= read -r line || [[ -n "$line" ]]; do
  lineno=$((lineno + 1))
  line="${line%$'\r'}"
  [[ "$line" =~ ^[[:space:]]*(#.*)?$ ]] && continue
  [[ "$line" =~ ^[[:space:]]*export[[:space:]]+(.*)$ ]] && line="${BASH_REMATCH[1]}"
  [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || die "자격 파일 ${lineno}행이 KEY=VALUE 형식이 아닙니다"
  key="${BASH_REMATCH[1]}"
  value="${BASH_REMATCH[2]}"
  if ((${#value} >= 2)) && [[ ("${value:0:1}" == '"' && "${value: -1}" == '"') || ("${value:0:1}" == "'" && "${value: -1}" == "'") ]]; then
    value="${value:1:${#value}-2}"
  fi
  case "$key" in
    KTDM_LIVE_WEB_URL | KTDM_LIVE_USERNAME | KTDM_LIVE_PASSWORD) ;;
    *) die "자격 파일 ${lineno}행의 키 ${key}는 허용하지 않습니다(KTDM_LIVE_WEB_URL·KTDM_LIVE_USERNAME·KTDM_LIVE_PASSWORD)" ;;
  esac
  [[ -z "${cred[$key]+set}" ]] || die "자격 파일에 ${key}가 두 번 있습니다"
  cred[$key]="$value"
done < <(printf '%s\n' "$credentials")
credentials=""

for key in KTDM_LIVE_WEB_URL KTDM_LIVE_USERNAME KTDM_LIVE_PASSWORD; do
  [[ -n "${cred[$key]:-}" ]] || die "자격 파일에 ${key}가 없습니다"
done
web_url="${cred[KTDM_LIVE_WEB_URL]%/}"
[[ "$web_url" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || die "KTDM_LIVE_WEB_URL은 scheme://host[:port] 형식이어야 합니다(경로 없이)"

pw_version="$(sed -n 's/.*"@playwright\/test": *"\([0-9][0-9.]*\)".*/\1/p' "$frontend_dir/package.json" | head -n 1)"
[[ -n "$pw_version" ]] || die "frontend/package.json에서 @playwright/test 정확한 버전을 찾지 못했습니다"
image="${KTDM_LIVE_E2E_IMAGE:-mcr.microsoft.com/playwright:v${pw_version}-noble}"
[[ "$image" == mcr.microsoft.com/playwright:* || "$image" == mcr.microsoft.com/playwright@* ]] \
  || die "KTDM_LIVE_E2E_IMAGE는 공식 mcr.microsoft.com/playwright 이미지여야 합니다"

# frontend/package.json engines·.npmrc engine-strict와 같은 Node/npm. 공식 Playwright 이미지의 Node는
# 패치 버전이 달라 engine-strict npm ci가 실패하므로, 컨테이너 안에서 nodejs.org 공식 tarball을 아래
# SHA-256(SHASUMS256.txt 값)으로 검증해 쓴다.
node_version="26.13.1"
npm_version="12.0.1"
node_sha256_x64="518674de2b621728a5e4486f5fb3b7d2900bb3cc4aa97969edaa463db99f9778"
node_sha256_arm64="80a330b35415382878df18d41afbd106ea89bfb3be0087d6608e289a14c5cc7d"
if [[ -f "$repo_root/.nvmrc" && "$(tr -d '[:space:]' <"$repo_root/.nvmrc")" != "$node_version" ]]; then
  die "러너 Node $node_version이 .nvmrc와 다릅니다 — 고정 tarball과 SHA-256을 함께 갱신하세요"
fi

# 컨테이너 안 스크립트. 자격 값은 환경변수로만 들어오고, 출력은 LIVE_E2E_REDACT에 적힌 변수의 값을
# ***로 가린 뒤 터미널에 낸다.
inner=$(cat <<'INNER'
set -euo pipefail
case "$(uname -m)" in
  x86_64) node_arch=x64; node_sha="$LIVE_E2E_NODE_SHA256_X64" ;;
  aarch64) node_arch=arm64; node_sha="$LIVE_E2E_NODE_SHA256_ARM64" ;;
  *) echo "error: $(uname -m)용 고정 Node tarball이 없습니다" >&2; exit 1 ;;
esac
node_dist="node-v${LIVE_E2E_NODE_VERSION}-linux-${node_arch}"
node_cache=/tmp/.npm/_live-e2e-node
node_tarball="$node_cache/${node_dist}.tar.gz"
mkdir -p "$node_cache"
if [[ ! -f "$node_tarball" ]] || ! printf '%s  %s\n' "$node_sha" "$node_tarball" | sha256sum -c --status; then
  node_part="$node_cache/.${node_dist}.part"
  node -e 'fetch(process.argv[1]).then(async (r) => { if (!r.ok) throw new Error("HTTP " + r.status); require("fs").writeFileSync(process.argv[2], Buffer.from(await r.arrayBuffer())); }).catch((e) => { console.error(e.message); process.exit(1); })' \
    "https://nodejs.org/dist/v${LIVE_E2E_NODE_VERSION}/${node_dist}.tar.gz" "$node_part"
  printf '%s  %s\n' "$node_sha" "$node_part" | sha256sum -c --status \
    || { echo "error: ${node_dist}.tar.gz SHA-256 불일치" >&2; exit 1; }
  mv -f "$node_part" "$node_tarball"
fi
mkdir -p /tmp/live-e2e-node
tar -xzf "$node_tarball" -C /tmp/live-e2e-node --strip-components=1 --no-same-owner
export PATH="/tmp/live-e2e-node/bin:$PATH"
hash -r
[[ "$(node --version)" == "v${LIVE_E2E_NODE_VERSION}" ]] || { echo "error: Node 버전 불일치" >&2; exit 1; }
npm install --global --no-audit --no-fund "npm@${LIVE_E2E_NPM_VERSION}" >/dev/null
hash -r
[[ "$(npm --version)" == "$LIVE_E2E_NPM_VERSION" ]] || { echo "error: npm 버전 불일치" >&2; exit 1; }
if [[ "$LIVE_E2E_SKIP_NPM_CI" != 1 ]]; then
  npm ci --no-audit --no-fund --engine-strict --ignore-scripts >/dev/null
fi

redact_js=$(cat <<'JS'
const names = (process.env.LIVE_E2E_REDACT || '').split(',').filter(Boolean);
const secrets = [];
for (const name of names) {
  const value = process.env[name];
  if (value && value.length >= 3) secrets.push(value, encodeURIComponent(value), JSON.stringify(value).slice(1, -1));
}
secrets.sort((a, b) => b.length - a.length);
const redact = (text) => secrets.reduce((acc, secret) => acc.split(secret).join('***'), text);
if (process.argv[1] === 'filter') {
  const rl = require('readline').createInterface({ input: process.stdin });
  rl.on('line', (line) => process.stdout.write(redact(line) + '\n'));
} else {
  const fs = require('fs');
  const [file, startMs] = process.argv.slice(2);
  let report;
  try { report = JSON.parse(fs.readFileSync(file, 'utf8')); } catch {
    console.log('live_e2e_summary: JSON 보고서가 없습니다(Playwright가 시작 전에 멈췄습니다)');
    process.exit(0);
  }
  const stats = report.stats || {};
  if (stats.startTime && Date.parse(stats.startTime) < Number(startMs) - 60_000) {
    console.log('live_e2e_summary: JSON 보고서가 이번 실행의 것이 아닙니다');
    process.exit(0);
  }
  const bad = [];
  const walk = (suite, trail) => {
    for (const child of suite.suites || []) walk(child, [...trail, child.title]);
    for (const spec of suite.specs || []) {
      for (const t of spec.tests || []) {
        if (t.status !== 'unexpected' && t.status !== 'flaky') continue;
        const last = (t.results || []).at(-1);
        const message = ((last && last.error && last.error.message) || '').replace(/\x1b\[[0-9;]*m/g, '').split('\n')[0];
        bad.push(`  - [${t.status}] ${t.projectName ? t.projectName + ' · ' : ''}${spec.file}:${spec.line} ${[...trail, spec.title].filter(Boolean).join(' › ')}${message ? ' — ' + redact(message).slice(0, 300) : ''}`);
      }
    }
  };
  for (const suite of report.suites || []) walk(suite, []);
  console.log(`live_e2e_summary 통과=${stats.expected ?? 0} 실패=${stats.unexpected ?? 0} flaky=${stats.flaky ?? 0} 건너뜀=${stats.skipped ?? 0} 소요=${Math.round((stats.duration || 0) / 1000)}초`);
  for (const line of bad) console.log(line);
}
JS
)

mkdir -p "$LIVE_E2E_RESULTS_PARENT"
chmod 700 "$LIVE_E2E_RESULTS_PARENT"
start_ms=$(($(date +%s) * 1000))
status=0
npx playwright test --config "$LIVE_E2E_CONFIG" "$@" 2>&1 | node -e "$redact_js" filter || status=$?
if [[ "$LIVE_E2E_LIST_ONLY" != 1 ]]; then
  node -e "$redact_js" summary "$LIVE_E2E_REPORT" "$start_ms"
fi
chown -R "$LIVE_E2E_OWNER" "$LIVE_E2E_RESULTS_PARENT" 2>/dev/null || true
exit "$status"
INNER
)

export KTDM_LIVE_E2E=1
export KTDM_LIVE_WEB_URL="$web_url"
export KTDM_LIVE_USERNAME="${cred[KTDM_LIVE_USERNAME]}"
export KTDM_LIVE_PASSWORD="${cred[KTDM_LIVE_PASSWORD]}"
export KTDM_LIVE_EXPECT_HOST="$expect_host"
cred=()

docker_args=(
  run --rm --init --ipc=host --network host
  --env KTDM_LIVE_E2E --env KTDM_LIVE_WEB_URL --env KTDM_LIVE_USERNAME --env KTDM_LIVE_PASSWORD
  --env KTDM_LIVE_EXPECT_HOST
  -e LIVE_E2E_REDACT=KTDM_LIVE_PASSWORD
  -e LIVE_E2E_CONFIG=playwright.live.config.ts
  -e LIVE_E2E_RESULTS_PARENT=/work/test-results
  -e LIVE_E2E_REPORT=/work/test-results/live/report.json
  -e "LIVE_E2E_LIST_ONLY=$list_only"
  -e "LIVE_E2E_OWNER=$(id -u):$(id -g)"
  -e "LIVE_E2E_SKIP_NPM_CI=$skip_npm_ci"
  -e "LIVE_E2E_NODE_VERSION=$node_version"
  -e "LIVE_E2E_NPM_VERSION=$npm_version"
  -e "LIVE_E2E_NODE_SHA256_X64=$node_sha256_x64"
  -e "LIVE_E2E_NODE_SHA256_ARM64=$node_sha256_arm64"
  -e HOME=/tmp/live-e2e-home
  -e npm_config_cache=/tmp/.npm
  -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
  -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
)
if [[ "$write_opt_in" == 1 ]]; then
  export KTDM_LIVE_E2E_WRITE=1
  docker_args+=(--env KTDM_LIVE_E2E_WRITE)
fi
# 컨테이너에는 frontend/만 싣는다. backend·config·배포 트리의 .env는 root 컨테이너에 보이지 않는다.
docker_args+=(
  -v "$frontend_dir:/work"
  -v ktdm-live-e2e-node-modules:/work/node_modules
  -v ktdm-live-e2e-npm-cache:/tmp/.npm
  -w /work
  "$image"
  bash -c "$inner" live-e2e
)
if ((${#pw_args[@]})); then
  docker_args+=("${pw_args[@]}")
fi

echo "live_e2e target=manager config=frontend/playwright.live.config.ts image=$image expect_host=$expect_host"
echo "live_e2e credentials=$cred_file (값은 출력하지 않습니다) write_specs=$([[ $write_opt_in == 1 ]] && echo 포함 || echo 제외)"
exec "$gate" "$docker_bin" "${docker_args[@]}"
