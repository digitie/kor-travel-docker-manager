#!/usr/bin/env bash
# 프로젝트 하나를 공용 Dagster plane으로 옮기거나 되돌린다(ADR-54, platform-topology.md §7 전환 runbook) — n150, root.
#
#   scripts/dagster-shared-cutover.sh <target> forward  <merge-sha>    # 그 target의 전환 PR을 설치한 **직후**
#   scripts/dagster-shared-cutover.sh <target> rollback <release-sha>  # 그 target이 `own`인 release를 설치한 뒤
#   scripts/dagster-shared-cutover.sh <target> resume   <merge-sha> <state-dir>
#       # forward가 펜스 **뒤** 스위치·검증에서 실패했을 때(예: pinned 재구축이 slot을 멈춘 뒤 실패해 Map·PinVi가 내려감).
#       # 펜스가 그대로인지(옛 daemon 정지, 펜스 뒤 옛 DB에 tick·run 없음) 확인하고 스위치(pinned 재구축은 전환된
#       # release 아래 멱등)부터 plane 재생성·검증·옛 컨테이너 제거·첫 tick까지 다시 간다. forward의 실패 메시지가 이
#       # 명령을 그대로 찍는다.
#
# weather-cutover.sh(첫 전환의 기록)를 일반화했다. target의 서비스·location·옛 DB·소비자는 이름을 적지 않고
# **설치본에서 파생**한다: code-server는 target의 `services` 중 `dagster code-server start`(또는 옛
# `dagster api grpc`)를 실행하는 것, 옛 webserver·
# daemon은 그 code-server에 기대며 `dagster-webserver`/`dagster-daemon`을 실행하는 것(공용 workspace를 붙인 것 제외),
# gateway는 그 둘에 기대는 것, location은 code-server의 `--location-name` 또는 `-m`, 옛 DB는 code-server의 옛 instance가
# 붙은 database(`current_database()`), 소비자는 targets의 `dagster.consumers`다.
#
# 스위치 방식은 target이 **pinned Map·PinVi pair**에 드는가로 갈린다(Manager의 `runtime_topology` 상수: map·pinvi).
#   - pinned: 설치본의 `scripts/run-pinned-rebuild-once`(이 스크립트가 쥔 lock G를 넘긴다). 전환 뒤 slot 서비스
#     key가 바뀌므로 same-pair converge가 아니라 **full 경로**다 — Map도 잠깐 내려가고, migration(멱등)이 다시 돌고,
#     compose-built 이미지 넷을 다시 만든다. code-server·API는 generation 이미지로 재생성·검증된다. 직접 compose는
#     generation 이미지를 벗어나므로 쓰지 않는다.
#   - compose: 설치본 compose로 `up -d --no-deps <code-server> <소비자>`.
# 어느 쪽이든 순서는 같다: fence(옛 daemon → webserver → gateway 정지, #447의 "retired 컨테이너 running" 거부를 푼다)
# → 옛 DB in-flight run 정리 → code-server·소비자 재생성 → 공용 daemon·webserver 재생성(그제야 workspace에 target이
# 든다) → 검증 → 옛 컨테이너 제거. 이중 발화 창은 없다(fence 전에는 공용 plane이 target을 싣지 않고, 공용 plane이
# 실은 뒤에는 옛 daemon이 멈춰 있다). fence와 plane 재생성 사이의 cron 슬롯은 건너뛴다.
#
# 운영: systemd-run으로(세션이 끊겨도 계속):
#   systemd-run --unit=dagster-cutover-<target> --collect /opt/kor-travel-docker-manager/scripts/dagster-shared-cutover.sh <target> forward <sha>
#   형제 프로젝트(transport)는 env 파일을 systemd-run으로 넘긴다(셸 env는 unit에 가지 않는다):
#   systemd-run --unit=dagster-cutover-transport --collect -E EXTERNAL_ENV_FILE=<file> /opt/kor-travel-docker-manager/scripts/dagster-shared-cutover.sh transport forward <sha>
# stdin이 tty면 거부한다(tmux 안이면 FORCE_TTY=1). 설치와 forward는 이어서 돌고, 그 사이 아무도 공용 plane을
# 재시작하지 않는다(재시작하면 새 workspace로 target을 싣는데 옛 daemon은 아직 tick한다 — 스크립트가 확인한다).
#
# 환경: EXTERNAL_ENV_FILE(형제 프로젝트 target에만, 필수 — 위), FIRST_TICK_TIMEOUT_S(기본 4500), SAMPLE_EVERY_S(기본 30), ROLLBACK_RELEASE(forward 실패 때 찍을 되돌릴
# release — 전환 직전 설치본의 sha. 없으면 "전환 직전 설치본"이라고만 찍는다), REQUIRE_MAP_IDLE(1이면 pinned
# 재구축이 끊을 pair 상대편 — PinVi 전환이면 Map — 의 진행 중 run이 있을 때 멈춘다. 기본은 세어 알리기만: 소유자가
# 그 손실을 받아들였다).
#
# 공용 plane은 이미 다른 테넌트(weather 등)가 tick한다 — tick은 **이 target의 것만** 센다. 이 target의 instigator
# selector id를 옛 DB에서 모아(`instigators`·`job_ticks`) `dagster_shared.job_ticks.selector_id`로 좁힌다. selector id는
# location·repository·이름에서 결정되므로 두 instance에서 같다. run은 `dagster/code_location` tag로 좁힌다.
#
# 형제 프로젝트(`external_project`, transport): compose가 Manager 밖(그 프로젝트의 working_dir)에 있다. 그 target의
# code-server·옛 서비스는 그 compose project에 있고, 공용 plane은 Manager project에 있다. 그래서
#   - 그 프로젝트의 compose 파일은 **실행 중 code-server 컨테이너의 compose label**(project·working_dir·config_files)에서
#     읽고 targets의 `external_project`와 대조한다. env 파일은 label이 말하지 못하므로(배포 스크립트의 임시 파일이다)
#     `EXTERNAL_ENV_FILE`(소유자만 읽는 일반 파일, 필수)로 받는다 — transport는
#     `DEPLOY_MODE=prepare-shared-dagster-cutover`가 만드는 `.env.server14.shared-dagster-cutover`다(이미지 tag 포함).
#   - 모양은 그 렌더(`--profile legacy-dagster`)에서 같은 규칙으로 파생하고 targets의 `dagster.external` 선언과 대조한다
#     (어긋나면 펜스 전에 멈춘다). code-server가 쓸 이미지가 호스트에 있어야 한다(전환은 빌드·pull하지 않는다).
#   - 스위치는 compose 경로(`up -d --no-deps --no-build <code-server>`)이고, 소비자는 그 저장소가 따로 배포한다.
#
# 원칙: 비밀을 출력하지 않는다(compose config는 python이 읽고 이름·개수만 꺼낸다). 모든 단계는 실패하면 멈추고 무엇이
# 실패했는지 말한다. 펜스 뒤 실패는 code-server가 아직 옛 instance면 옛 서비스를 되살리고, 아니면 되돌리기 명령을
# 찍는다. 상태를 바꾸는 명령은 lock G 아래에서만 돈다.
set -Eeuo pipefail
umask 077

TARGET="${1:-}"; MODE="${2:-}"; EXPECT="${3:-}"; RESUME_FROM="${4:-}"
[[ "$TARGET" =~ ^[a-z][a-z0-9-]*$ && ( "$MODE" == forward || "$MODE" == rollback || "$MODE" == resume ) \
   && "$EXPECT" =~ ^[0-9a-f]{7,40}$ && ( "$MODE" != resume || -d "$RESUME_FROM" ) ]] || {
  echo "usage: $0 <target> forward <merge-sha> | <target> rollback <release-sha> | <target> resume <merge-sha> <state-dir>" >&2; exit 64; }
[[ "$(id -u)" == 0 ]] || { echo "run as root" >&2; exit 64; }
if [[ -t 0 && "${FORCE_TTY:-0}" != 1 ]]; then
  echo "refusing to run on a terminal: a dropped session would stop between fence and switch." >&2
  echo "run: systemd-run --unit=dagster-cutover-$TARGET --collect${EXTERNAL_ENV_FILE:+ -E EXTERNAL_ENV_FILE=$EXTERNAL_ENV_FILE} $0 $TARGET $MODE $EXPECT${RESUME_FROM:+ $RESUME_FROM}   (inside tmux: FORCE_TTY=1)" >&2
  exit 64
fi

ROOT=/opt/kor-travel-docker-manager
PROJECT=kor-travel-docker-manager
LOCK_DIR=/run/lock/kor-travel-docker-manager
LOCK="$LOCK_DIR/global-mutation.lock"
PG=kor-travel-shared-postgres
NEW_DB=dagster_shared
SHARED_ROLE=kor_travel_dagster_shared_app
PINNED_TARGETS=" map pinvi "   # Manager `runtime_topology`의 pinned pair(MAP_TARGET·PINVI_TARGET)
FIRST_TICK_TIMEOUT_S="${FIRST_TICK_TIMEOUT_S:-4500}"
SAMPLE_EVERY_S="${SAMPLE_EVERY_S:-30}"
ROLLBACK_RELEASE="${ROLLBACK_RELEASE:-}"
REQUIRE_MAP_IDLE="${REQUIRE_MAP_IDLE:-0}"
EXTERNAL_ENV_FILE="${EXTERNAL_ENV_FILE:-}"
# 이 target의 서비스(code-server·옛 서비스·소비자)가 사는 compose project. Manager 자신의 target이면 Manager project이고,
# 형제 프로젝트면 아래 external 단계가 그 project·working_dir·compose 파일로 바꾼다.
TPROJECT="$PROJECT"; TWD=""; TFILES=()
STATE=/root/dagster-cutover-$TARGET-$MODE-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$STATE"

PHASE="init"
FENCED=0
SWITCH_STARTED=0
SEL_IN=""   # 이 target의 selector id SQL 목록 — ('…','…')
say() { printf '[%s] %s/%s: %s\n' "$(date -u +%H:%M:%SZ)" "$TARGET" "$PHASE" "$*"; }
count_lines() { awk 'NF { n++ } END { print n + 0 }' "$@"; }
compose() { docker compose -p "$PROJECT" --project-directory "$ROOT" -f "$ROOT/docker-compose.yml" --env-file "$ROOT/.env" "$@"; }
tcompose() {  # 이 target의 compose — Manager target이면 Manager compose, 형제 프로젝트면 그 project의 파일·env
  if [[ -z "$TWD" ]]; then compose "$@"; return; fi
  local f args=()
  for f in "${TFILES[@]}"; do args+=(-f "$f"); done
  docker compose -p "$TPROJECT" --project-directory "$TWD" "${args[@]}" --env-file "$EXTERNAL_ENV_FILE" "$@"
}
tup() {  # target 서비스 재생성(의존 없이). 형제 프로젝트는 빌드·pull하지 않는다 — 이미지는 그 저장소의 준비 단계가 만든다.
  if [[ -n "$TWD" ]]; then tcompose up -d --no-deps --no-build "$@"; else compose up -d --no-deps "$@"; fi
}
project_of() {  # 서비스가 사는 compose project — 공용 plane 서비스는 Manager project, 나머지는 target의 project
  case " $DAEMON $WEBSERVER $GATEWAY " in *" $1 "*) echo "$PROJECT" ;; *) echo "$TPROJECT" ;; esac
}
cid() {  # compose 서비스의 컨테이너 id(`compose run` 일회성 제외). 없으면 빈 문자열, 둘 이상이면 실패
  local ids n
  ids="$(docker ps -aq --no-trunc --filter "label=com.docker.compose.project=$(project_of "$1")" \
    --filter "label=com.docker.compose.service=$1" --filter "label=com.docker.compose.oneoff=False")"
  n="$(count_lines <<<"$ids")"
  (( n <= 1 )) || fail "$n containers match compose service $1 — refusing to guess which one is live"
  printf '%s\n' "$ids"
}
health() { local c; c="$(cid "$1")"; [[ -n "$c" ]] && docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c" || echo absent; }
running() { local c; c="$(cid "$1")"; [[ -n "$c" ]] && [[ "$(docker inspect -f '{{.State.Running}}' "$c")" == true ]]; }
env_has() { local c; c="$(cid "$1")"; [[ -n "$c" ]] && docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" | grep -q "^$2="; }
env_value() { docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$(cid "$1")" | sed -n "s/^$2=//p"; }  # 비밀이 아닌 키에만
sql() { docker exec -u postgres "$PG" sh -c 'psql -X -qAt -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 11000 -U "$POSTGRES_USER" -d "$0" -c "$1"' "$1" "$2"; }
installed_rev() { basename "$(readlink -f "$ROOT")" | sed -n 's/^ktdm-release-\([0-9a-f]\{40\}\)$/\1/p'; }

# ── 파생: 설치본 compose(legacy-dagster profile까지 렌더)·targets에서 이 target의 family ──────────────────
# 형제 프로젝트 target이면 family는 그 프로젝트의 렌더(두 번째 입력)에서, 공용 plane은 Manager 렌더(stdin)에서 찾는다.
derive() {
  local t="${1:-$TARGET}"
  if [[ "$t" == "$TARGET" && -n "$TWD" ]]; then
    compose --profile legacy-dagster config --format json 2>/dev/null \
      | python3 -I -c "$DERIVE_PY" "$t" "$ROOT/config/docker-targets.yml" \
          <(tcompose --profile legacy-dagster config --format json 2>/dev/null)
  else
    compose --profile legacy-dagster config --format json 2>/dev/null \
      | python3 -I -c "$DERIVE_PY" "$t" "$ROOT/config/docker-targets.yml" -
  fi
}
DERIVE_PY='
import json, shlex, sys
target, targets_path, target_compose = sys.argv[1], sys.argv[2], sys.argv[3]
import re
compose = json.load(sys.stdin)
services = compose["services"]
# 이 target의 서비스가 사는 렌더 — Manager target이면 같은 것, 형제 프로젝트면 그 프로젝트의 것.
tservices = services if target_compose == "-" else json.load(open(target_compose, encoding="utf-8"))["services"]
# targets YAML은 PyYAML 없이 읽을 수 없을 수 있다 — docker compose가 아니라 Manager 설치본의 python에 기대지 않으려고
# 최소 파서 대신 yaml을 시도하고, 없으면 실패한다.
try:
    import yaml
except ImportError:
    sys.exit("python3-yaml is required to read docker-targets.yml")
spec = yaml.safe_load(open(targets_path, encoding="utf-8"))["targets"][target]
block = spec.get("dagster") or {}
def words(s):
    out = []
    for key in ("command", "entrypoint"):
        v = s.get(key)
        out += v if isinstance(v, list) else (shlex.split(v) if isinstance(v, str) else [])
    return out
def runs(s, program): return program in " ".join(words(s))
def deps(s): return set((s.get("depends_on") or {}).keys())
def flag(argv, *names):
    for i, w in enumerate(argv[:-1]):
        if w in names: return argv[i + 1]
    return None
def shared_workspace(s):
    return any("config/dagster-shared/workspace.yaml" in str(v.get("source", v) if isinstance(v, dict) else v) for v in s.get("volumes") or [])
def code_server(s):
    argv = words(s)
    return any(w.rsplit("/", 1)[-1] == "dagster" and tuple(argv[i + 1:i + 3]) in (("code-server", "start"), ("api", "grpc"))
               for i, w in enumerate(argv))
external = block.get("external") if spec.get("external_project") else None
if spec.get("external_project") and not isinstance(external, dict):
    sys.exit("targets.%s.dagster.external is not declared" % target)
if spec.get("external_project") and target_compose == "-":
    sys.exit("internal: no compose rendering for the sibling project %s" % target)
# Manager target은 그 target의 `services` 안에서, 형제 프로젝트는 그 프로젝트의 렌더 전체에서 찾는다.
candidates = list(tservices) if external is not None else [n for n in spec.get("services") or [] if n in tservices]
codes = [n for n in candidates if code_server(tservices[n])]
if len(codes) != 1: sys.exit("expected one code-server in %s, got %s" % (target, codes))
code = codes[0]
dependents = {n: s for n, s in tservices.items() if code in deps(s) and not shared_workspace(s)}
web = [n for n, s in dependents.items() if runs(s, "dagster-webserver")]
dae = [n for n, s in dependents.items() if runs(s, "dagster-daemon")]
if len(web) != 1 or len(dae) != 1: sys.exit("expected one old webserver and daemon, got %s %s" % (web, dae))
gws = sorted(n for n, s in tservices.items() if n not in (web[0], dae[0]) and deps(s) & {web[0], dae[0]} and not shared_workspace(s))
argv = words(tservices[code])
location = flag(argv, "--location-name", "-l") or flag(argv, "-m", "--module-name")
webport = flag(words(tservices[web[0]]), "-p", "--port")
consumers = sorted(block.get("consumers") or {})
if external is not None:
    # 선언과 그 프로젝트의 실제 렌더가 같아야 한다 — Manager의 workspace·상한·펜스 검사는 선언을 싣는다.
    port = flag(argv, "-p", "--port") or ""
    derived = {"code_server": code, "webserver": web[0], "daemon": dae[0], "gateways": gws,
               "location_name": location, "port": int(port) if port.isdigit() else port}
    declared = {key: (sorted(external.get(key) or []) if key == "gateways" else external.get(key)) for key in derived}
    differ = sorted(key for key in derived if derived[key] != declared[key])
    if differ:
        sys.exit("the %s compose differs from targets.%s.dagster.external in %s: rendered %s, declared %s"
                 % (target, target, differ, {k: derived[k] for k in differ}, {k: declared[k] for k in differ}))
    if flag(argv, "-h", "--host") != "127.0.0.1":
        sys.exit("the %s code-server does not listen on 127.0.0.1 only" % target)
    named = sorted(n for n in [code, web[0], dae[0], *gws] if tservices[n].get("container_name"))
    if named:
        sys.exit("the %s Dagster services set container_name (Manager expects the compose default names): %s" % (target, named))
    # 공용 plane 모양(공용 URL을 받은 code-server)이면 Manager의 공용 plane code-server와 같은 규칙을 지킨다 — 이
    # 저장소의 규칙 테스트가 그 compose를 볼 수 없으므로 여기서 효과로 본다: `code-server start`(reload가 정의를 다시
    # 읽는다), Manager의 공용 probe(`x-dagster-code-server-probe`) 원문 그대로 exec 형식, 같은 proxy heartbeat, init.
    tcode = tservices[code]
    if "KOR_TRAVEL_DAGSTER_SHARED_PG_URL" in (tcode.get("environment") or {}):
        if not any(argv[i:i + 2] == ["code-server", "start"] for i in range(len(argv))):
            sys.exit("the %s code-server on the shared plane does not run `dagster code-server start`" % target)
        proxies = [s for s in services.values() if any(words(s)[i:i + 2] == ["code-server", "start"] for i in range(len(words(s))))]
        probes = {str(((s.get("healthcheck") or {}).get("test") or [None] * 5)[4]) for s in proxies}
        ttls = {str((s.get("environment") or {}).get("DAGSTER_GRPC_PROXY_HEARTBEAT_TTL_SECONDS")) for s in proxies}
        if len(probes) != 1 or len(ttls) != 1:
            sys.exit("the Manager shared code-servers do not share one probe and heartbeat (%d, %d)" % (len(probes), len(ttls)))
        test = [str(w) for w in ((tcode.get("healthcheck") or {}).get("test") or [])]
        if test[:4] != ["CMD", "python", "-I", "-c"] or len(test) != 6 or test[4] != next(iter(probes)) or test[5] != port:
            sys.exit("the %s code-server healthcheck is not the shared probe (exec form, Manager x-dagster-code-server-probe, port %s)" % (target, port))
        if str((tcode.get("environment") or {}).get("DAGSTER_GRPC_PROXY_HEARTBEAT_TTL_SECONDS")) != next(iter(ttls)):
            sys.exit("the %s code-server DAGSTER_GRPC_PROXY_HEARTBEAT_TTL_SECONDS differs from the shared code-servers (%s)" % (target, next(iter(ttls))))
        if tcode.get("init") is not True:
            sys.exit("the %s code-server does not run under init (init: true)" % target)
    print("CODE_IMAGE=%s" % (tcode.get("image") or ""))
def plane_webserver_port():
    plane_web = [n for n, s in services.items() if not s.get("profiles") and shared_workspace(s) and runs(s, "dagster-webserver")]
    if len(plane_web) != 1: sys.exit("expected one shared webserver, got %s" % plane_web)
    port = flag(words(services[plane_web[0]]), "-p", "--port")
    if not (port or "").isdigit(): sys.exit("shared webserver port is not a literal: %s" % port)
    return port
INTERNAL_BASE = "http://127.0.0.1:%s" % plane_webserver_port()
def expected_internal(kind):
    # 종류는 internal 또는 internal/<path> — 값은 공용 webserver의 loopback 뒤에 그 경로를 붙인 것(계약과 같다).
    base, _, path = str(kind).partition("/")
    return INTERNAL_BASE + ("/" + path if path else "")
internal = sorted("%s %s %s" % (svc, var, expected_internal(kind)) for svc, vs in (block.get("consumers") or {}).items() for var, kind in vs.items() if str(kind).split("/")[0] == "internal")
print("CODE=%s" % code)
print("OLD_WEBSERVER=%s" % web[0])
print("OLD_DAEMON=%s" % dae[0])
print("OLD_GATEWAYS=%s" % " ".join(gws))
print("LOCATION=%s" % location)
print("OLD_WEBSERVER_PORT=%s" % webport)
print("CONSUMERS=%s" % " ".join(consumers))
print("INTERNAL_CONSUMERS=%s" % ";".join(internal))
print("PLANE_WEBSERVER_PORT=%s" % plane_webserver_port())
print("CONTROL_PLANE=%s" % block.get("control_plane"))
print("CODE_HAS_SHARED_URL=%s" % ("yes" if "KOR_TRAVEL_DAGSTER_SHARED_PG_URL" in (tservices[code].get("environment") or {}) else "no"))
print("RUNTIME_SERVICES=%s" % " ".join(spec.get("runtime_services") or spec.get("services") or []))
# 공용 plane — 파생 workspace를 붙인 활성 webserver·daemon, 그리고 그 webserver에 기대는 활성 gateway(이름을 적지 않는다).
active = {n: s for n, s in services.items() if not s.get("profiles")}
plane = {n: s for n, s in active.items() if shared_workspace(s)}
pd = [n for n, s in plane.items() if runs(s, "dagster-daemon")]
pw = [n for n, s in plane.items() if runs(s, "dagster-webserver")]
if len(pd) != 1 or len(pw) != 1: sys.exit("expected one shared daemon and webserver, got %s %s" % (pd, pw))
pg = sorted(n for n, s in active.items() if n not in plane and pw[0] in deps(s))
if len(pg) != 1: sys.exit("expected one shared gateway, got %s" % pg)
print("PLANE_DAEMON=%s" % pd[0])
print("PLANE_WEBSERVER=%s" % pw[0])
print("PLANE_GATEWAY=%s" % pg[0])
# 공용 instance의 role·DB를 만드는 one-shot — 공용 role 사용자 env를 싣고 CONNECTION LIMIT을 거는 활성 서비스.
dbi = [n for n, s in active.items() if "KOR_TRAVEL_DAGSTER_SHARED_APP_USER" in (s.get("environment") or {})
       and "CONNECTION LIMIT" in " ".join(words(s))]
if len(dbi) != 1: sys.exit("expected one shared db-init one-shot, got %s" % dbi)
limits = sorted(set(re.findall(r"CONNECTION LIMIT ([0-9]+)", " ".join(words(active[dbi[0]])))))
if len(limits) != 1: sys.exit("the shared db-init sets no single CONNECTION LIMIT: %s" % limits)
print("PLANE_DB_INIT=%s" % dbi[0])
print("PLANE_ROLE_CONNECTION_LIMIT=%s" % limits[0])
'
fact() { sed -n "s/^$1=//p" <<<"$2"; }

# 공용 plane 서비스 — derive()가 설치본 compose에서 모양으로 찾는다(아래 derive 단계에서 채운다).
DAEMON=""; WEBSERVER=""; GATEWAY=""

# ── 실패와 복구 ───────────────────────────────────────────────────────────
code_moved() {  # code-server가 공용 instance로 넘어갔는가 — 실제 상태로 본다
  local before now
  before="$(awk -v s="$CODE" '$1==s{print $2}' "$STATE/ids-before.txt" 2>/dev/null || true)"
  now="$(docker ps -aq --no-trunc --filter "label=com.docker.compose.project=$TPROJECT" \
    --filter "label=com.docker.compose.service=$CODE" --filter "label=com.docker.compose.oneoff=False" 2>/dev/null | head -1 || true)"
  [[ -z "$before" || -z "$now" || "$now" != "$before" ]] && return 0
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$now" 2>/dev/null | grep -q '^KOR_TRAVEL_DAGSTER_SHARED_PG_URL='
}
rollback_hint() {
  echo "recover: install ${ROLLBACK_RELEASE:-the release that was installed before $EXPECT}, then run: $0 $TARGET rollback ${ROLLBACK_RELEASE:-<that sha>}" >&2
}
resume_hint() {
  echo "recover: to finish the cutover instead (same installed release $EXPECT; the pinned rebuild is idempotent under it):" >&2
  echo "  systemd-run --unit=dagster-cutover-$TARGET-resume --collect${EXTERNAL_ENV_FILE:+ -E EXTERNAL_ENV_FILE=$EXTERNAL_ENV_FILE} $0 $TARGET resume $EXPECT $STATE" >&2
  echo "  it re-checks the fence, reruns the switch$([[ "$SWITCH" == pinned ]] && echo " (scripts/run-pinned-rebuild-once $(installed_rev) <new outdir>)"), recreates the plane" >&2
  echo "  (compose up -d --no-deps ${DAEMON:-<shared daemon>} ${WEBSERVER:-<shared webserver>}), verifies the location and instigator parity, removes the old containers" >&2
}
pair_down_report() {  # pinned 재구축은 실패 때 slot 전부를 멈춘다(compose_service의 오류 처리) — 무엇이 내려갔는지 말한다
  local t f s down=""
  for t in $PINNED_TARGETS; do
    f="$(derive "$t" 2>/dev/null)" || { echo "recover: cannot derive $t's services" >&2; continue; }
    for s in $(fact RUNTIME_SERVICES "$f"); do running "$s" 2>/dev/null || down+=" $t:$s($(health "$s" 2>/dev/null))"; done
  done
  echo "recover: not running after the failed pinned rebuild:${down:- none}" >&2
}
recover() {
  (( FENCED )) || return 0
  if (( SWITCH_STARTED )) && [[ "$SWITCH" == pinned ]]; then pair_down_report; fi
  if ! code_moved; then
    if ! running "$CODE" 2>/dev/null; then
      echo "recover: $CODE is not running — NOT restarting the old daemon (it would schedule against a missing code-server)." >&2
      resume_hint
      rollback_hint
    elif plane_clean quiet; then
      echo "recover: $CODE is still on the old instance and the plane does not list $LOCATION — restarting the old services" >&2
      for s in "$OLD_WEBSERVER" "$OLD_DAEMON" $OLD_GATEWAYS; do
        c="$(docker ps -aq --filter "label=com.docker.compose.project=$TPROJECT" --filter "label=com.docker.compose.service=$s" \
          --filter "label=com.docker.compose.oneoff=False" 2>/dev/null | head -1 || true)"
        if [[ -n "$c" ]] && docker start "$c" >/dev/null 2>&1; then echo "recover: started $s" >&2; else echo "recover: COULD NOT start $s" >&2; fi
      done
      echo "recover: the old $TARGET instance is scheduling again. Investigate, then retry forward." >&2
    else
      echo "recover: the shared plane already lists or ticked $LOCATION — NOT restarting the old daemon (double fire)." >&2
      resume_hint
      rollback_hint
    fi
  else
    echo "recover: $CODE was already moved to the shared instance." >&2
    resume_hint
    rollback_hint
  fi
}
IN_FAIL=0
fail() {
  if (( BASH_SUBSHELL > 0 )); then printf '[%s] %s/%s: FAILED — %s\n' "$(date -u +%H:%M:%SZ)" "$TARGET" "$PHASE" "$*" >&2; exit 1; fi
  (( IN_FAIL )) && exit 1
  IN_FAIL=1
  trap - ERR HUP INT TERM
  printf '[%s] %s/%s: FAILED — %s (state: %s)\n' "$(date -u +%H:%M:%SZ)" "$TARGET" "$PHASE" "$*" "$STATE" >&2
  recover || true
  exit 1
}
trap '(( BASH_SUBSHELL > 0 )) || fail "a command failed at line $LINENO (see the message above, if any)"' ERR
trap 'fail "interrupted by a signal"' HUP INT TERM

wait_health() {
  local deadline=$(( $(date +%s) + $2 )) s
  while :; do
    s="$(health "$1")"
    [[ "$s" == healthy ]] && { say "$1 healthy"; return 0; }
    [[ "$s" == unhealthy || "$s" == exited || "$s" == dead || "$s" == absent ]] && break
    (( $(date +%s) < deadline )) || break
    sleep 5
  done
  local c; c="$(cid "$1")"
  [[ -n "$c" ]] && docker inspect -f '{{range .State.Health.Log}}{{.Output}}{{end}}' "$c" 2>/dev/null | tail -c 600 >&2 || true
  fail "$1 is $s (waited $2 s)"
}
container_graphql() {
  docker exec -i "$(cid "$1")" python -I -c '
import json, sys, urllib.request as u
q = sys.stdin.read()
r = u.urlopen(u.Request("http://127.0.0.1:%s/graphql" % sys.argv[1], data=json.dumps({"query": q}).encode(),
    headers={"Content-Type": "application/json"}), timeout=20)
print(json.dumps(json.load(r)))' "$2"; }
graphql() { container_graphql "$WEBSERVER" "$PLANE_PORT" <<<"$1"; }
old_graphql() { container_graphql "$OLD_WEBSERVER" "$OLD_WEBSERVER_PORT" <<<"$1"; }
instigators_q() { printf '%s' '{ repositoryOrError(repositorySelector:{repositoryLocationName:"'"$LOCATION"'", repositoryName:"__repository__"}) { __typename ... on Repository { schedules { name defaultStatus scheduleState { status } } sensors { name defaultStatus sensorState { status } } } } }'; }
# stdin: GraphQL(위 query) → 상태가 코드의 `default_status`와 다른 schedule·sensor("이름 상태≠기본값"). 공용 instance는
# 새로 시작하므로(D1) 상태 행이 없고 코드의 기본값으로 돈다 — 옛 instance에서 손으로 멈춘(또는 켠) instigator는 전환 뒤
# 기본값으로 돌아가 쏘고(이중 정책), 동등 검증(e-verify)은 펜스 **뒤**에야 실패한다. 상태는 옮기지 않는다(D4) — 펜스 전에
# 멈추고 이름을 말한다. 코드의 기본값을 바꾸거나 옛 instance에서 상태를 기본값으로 되돌린 뒤 다시 한다.
dagster_versions_match() {  # $1: 이미지. 그 이미지의 dagster 버전 = 공용 webserver의 버전이면 0(둘을 말한다)
  local image_version host_version
  image_version="$(docker run --rm --network none --entrypoint python "$1" -I -c 'import importlib.metadata as m; print(m.version("dagster"))' 2>/dev/null)" || return 1
  host_version="$(graphql '{ version }' | python3 -c 'import json, sys; print(json.load(sys.stdin)["data"]["version"])')" || return 1
  say "dagster: $1 has ${image_version:-?}, the shared plane runs ${host_version:-?}"
  [[ -n "$image_version" && "$image_version" == "$host_version" ]]
}
instigators_off_their_code_default() {
  python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]["repositoryOrError"]
if d["__typename"] != "Repository": sys.exit("repository not loaded: %s" % d["__typename"])
rows = [("schedule:" + s["name"], s["scheduleState"]["status"], s.get("defaultStatus")) for s in d["schedules"]]
rows += [("sensor:" + s["name"], s["sensorState"]["status"], s.get("defaultStatus")) for s in d["sensors"]]
for name, status, default in sorted(rows):
    if default is None: sys.exit("the webserver reports no defaultStatus for %s" % name)
    if status != default: print("%s %s!=%s" % (name, status, default))'; }
running_instigators() {  # stdin: GraphQL → RUNNING schedule·sensor 이름(정렬, "schedule:"·"sensor:" 접두)
  python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]["repositoryOrError"]
if d["__typename"] != "Repository": sys.exit("repository not loaded: %s" % d["__typename"])
names = ["schedule:" + s["name"] for s in d["schedules"] if s["scheduleState"]["status"] == "RUNNING"]
names += ["sensor:" + s["name"] for s in d["sensors"] if s["sensorState"]["status"] == "RUNNING"]
print("\n".join(sorted(names)))'; }
installed_workspace_lists() { grep -q "location_name: $LOCATION" "$ROOT/config/dagster-shared/workspace.yaml"; }
TARGET_RUNS_SQL() { printf "SELECT count(*) FROM runs r JOIN run_tags t ON t.run_id = r.run_id WHERE t.key = 'dagster/code_location' AND t.value = '%s'%s" "$LOCATION" "${1:-}"; }
LIVE="('QUEUED','NOT_STARTED','STARTING','STARTED','CANCELING')"
TARGET_TICKS_SQL() {  # 이 target의 tick만 — 공용 plane에는 다른 테넌트의 tick이 섞인다(적대 리뷰 H1)
  [[ -n "$SEL_IN" ]] || fail "internal: selector ids not loaded"
  printf "SELECT count(*) FROM job_ticks WHERE selector_id IN %s%s" "$SEL_IN" "${1:-}"
}
load_selectors() {  # 옛 DB의 이 target instigator selector id(상태 행 + tick 기록) → SEL_IN, $STATE/selectors.txt
  sql "$OLD_DB" "SELECT selector_id FROM instigators WHERE selector_id IS NOT NULL UNION SELECT DISTINCT selector_id FROM job_ticks WHERE selector_id IS NOT NULL ORDER BY 1" > "$STATE/selectors.txt" \
    || fail "cannot read $TARGET's instigator selector ids from $OLD_DB"
  set_selectors "$STATE/selectors.txt"
}
set_selectors() {
  local id list="" n=0
  while read -r id; do
    [[ -n "$id" ]] || continue
    [[ "$id" =~ ^[0-9a-f]{16,64}$ ]] || fail "unexpected selector id shape in $1"
    list+="${list:+,}'$id'"; n=$(( n + 1 ))
  done < "$1"
  (( n > 0 )) || fail "no instigator selector ids for $TARGET in $1 — cannot scope tick counts"
  SEL_IN="($list)"
  say "$n instigator selector ids scope $TARGET's ticks"
}
SHARED_TICKS_BASELINE="unset"
plane_clean() {  # 도는 공용 plane이 아직 이 target을 싣지 않았다(붙인 workspace에 없고 digest env와 같으며, 공용 DB에 흔적이 없다)
  local quiet="${1:-}" s c got env
  for s in "$DAEMON" "$WEBSERVER"; do
    c="$(cid "$s")"; [[ -n "$c" ]] || { [[ -n "$quiet" ]] || echo "no container for $s" >&2; return 1; }
    got="$(docker exec "$c" python -I -c '
import hashlib, os, sys
b = open(os.path.join(os.environ["DAGSTER_HOME"], "workspace.yaml"), "rb").read()
print(hashlib.sha256(b).hexdigest()[:16], "listed" if ("location_name: " + sys.argv[1]).encode() in b else "clean")' "$LOCATION" 2>/dev/null)" || return 1
    env="$(env_value "$s" KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST)"
    if [[ "$got" != "$env clean" ]]; then
      [[ -n "$quiet" ]] || echo "$s mounts workspace [$got] but was created with digest [$env] (expected [<digest> clean])" >&2
      return 1
    fi
  done
  local runs ticks
  runs="$(sql "$NEW_DB" "$(TARGET_RUNS_SQL)")" || return 1
  ticks="$(sql "$NEW_DB" "$(TARGET_TICKS_SQL)")" || return 1
  if [[ "$runs" != 0 || ( "$SHARED_TICKS_BASELINE" != unset && "$ticks" != "$SHARED_TICKS_BASELINE" ) ]]; then
    [[ -n "$quiet" ]] || echo "$NEW_DB has $runs $TARGET runs and $ticks $TARGET ticks (baseline $SHARED_TICKS_BASELINE)" >&2
    return 1
  fi
}
# 옛 instance의 in-flight run을 Dagster 자신의 API로 끝낸다 — database 이름으로 대상을 확인한 뒤에만.
cancel_runs_py() { cat <<'PY'
import sys, time
from sqlalchemy import text
from dagster import DagsterInstance
from dagster._core.storage.dagster_run import DagsterRunStatus, RunsFilter
database, location = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
live = [DagsterRunStatus.QUEUED, DagsterRunStatus.NOT_STARTED, DagsterRunStatus.STARTING,
        DagsterRunStatus.STARTED, DagsterRunStatus.CANCELING]
tags = {"dagster/code_location": location} if location else None
with DagsterInstance.get() as instance:
    with instance.run_storage.connect() as conn:
        current = conn.execute(text("SELECT current_database()")).scalar()
    assert current == database, "refusing: this instance is %s, not %s" % (current, database)
    runs = instance.get_runs(filters=RunsFilter(statuses=live, tags=tags))
    for run in runs:
        if run.status in (DagsterRunStatus.STARTED, DagsterRunStatus.STARTING):
            try:
                instance.run_launcher.terminate(run.run_id)
            except Exception as exc:  # noqa: BLE001 - best effort; report_run_canceled closes the state
                print("terminate %s: %s" % (run.run_id[:8], type(exc).__name__))
    time.sleep(5)
    for run in instance.get_runs(filters=RunsFilter(statuses=live, tags=tags)):
        instance.report_run_canceled(run, message="moved between Dagster control planes (ADR-54 cutover)")
    print("canceled %d runs in %s" % (len(runs), current))
PY
}
current_db_of() {  # 그 컨테이너의 Dagster instance가 붙은 database
  docker exec -i "$(cid "$1")" python -I - <<'PY'
from sqlalchemy import text
from dagster import DagsterInstance
with DagsterInstance.get() as instance, instance.run_storage.connect() as conn:
    print(conn.execute(text("SELECT current_database()")).scalar())
PY
}
pinned_rebuild() {  # 설치본 launcher — 이 스크립트가 쥔 lock G(fd 9)를 넘긴다(launcher가 그 fd를 검증·재획득한다)
  local rev out
  rev="$(installed_rev)"; out="$STATE/pinned-rebuild"
  say "running the pinned Map+PinVi rebuild (full path after a flip: Map and PinVi restart, images rebuild) — output $out"
  SWITCH_STARTED=1
  if ! KTDM_PINNED_REBUILD_GLOBAL_LOCK_FD=9 "$ROOT/scripts/run-pinned-rebuild-once" "$rev" "$out" >"$STATE/pinned-rebuild.log" 2>&1; then
    tail -n 40 "$STATE/pinned-rebuild.log" >&2
    if [[ "$MODE" == rollback ]]; then
      pair_down_report
      echo "recover: the rollback release is installed; rerun the same rollback once the cause is fixed: $0 $TARGET rollback $EXPECT" >&2
    fi
    fail "pinned rebuild failed (full log $STATE/pinned-rebuild.log)"
  fi
  say "pinned rebuild finished"
}
pair_runs_report() {  # 전체 경로는 pair 상대편(PinVi 전환이면 Map)의 진행 중 run을 끊는다 — 세어 알린다(적대 리뷰 M2)
  [[ "$SWITCH" == pinned ]] || return 0
  local t f code loc db n total=0
  for t in $PINNED_TARGETS; do
    [[ "$t" != "$TARGET" ]] || continue
    f="$(derive "$t")" || fail "cannot derive $t's Dagster family"
    code="$(fact CODE "$f")"; loc="$(fact LOCATION "$f")"
    running "$code" || { say "WARNING: $t's $code is not running — cannot count its in-flight runs"; continue; }
    db="$(current_db_of "$code" | tail -1)"
    [[ "$db" =~ ^[a-z_][a-z0-9_]*$ ]] || fail "cannot tell $t's Dagster database ($db)"
    if [[ "$db" == "$NEW_DB" ]]; then
      n="$(sql "$NEW_DB" "SELECT count(*) FROM runs r JOIN run_tags t ON t.run_id = r.run_id WHERE t.key = 'dagster/code_location' AND t.value = '$loc' AND r.status IN $LIVE")"
    else
      n="$(sql "$db" "SELECT count(*) FROM runs WHERE status IN $LIVE")"
    fi
    say "the pinned rebuild restarts $t: $n in-flight $t runs in $db will be interrupted"
    (( n == 0 )) || [[ "$db" == "$NEW_DB" ]] || sql "$db" "SELECT status, count(*) FROM runs WHERE status IN $LIVE GROUP BY 1 ORDER BY 1" | sed "s/^/  $t in flight: /"
    total=$(( total + n ))
  done
  if (( total > 0 )) && [[ "$REQUIRE_MAP_IDLE" == 1 ]]; then fail "REQUIRE_MAP_IDLE=1 and $total in-flight runs would be interrupted"; fi
}
consumer_scope_check() {  # 소비자가 공용 webserver에 이 location만 묻는가 — 앱의 일이라 target마다 다르다(파생 불가)
  case "$TARGET" in
    weather) docker exec "$(cid kor-travel-weather-web)" grep -q WeatherDagsterOverview /app/.next/server/app/api/dagster/graphql/route.js ;;
    pinvi) [[ "$(docker exec "$(cid pinvi-api)" python -c 'from app.core.config import get_settings as g; print(g().pinvi_dagster_location_name)' 2>/dev/null | tail -1)" == "$LOCATION" ]] ;;
    # geo #569: summary·run 조회·launch가 `dagster_repository_location_name`으로 좁혀진다. run 상세는
    # `repositoryOrigin`으로 소유를 확인한다(`Run.tags`는 `.dagster/*`를 숨긴다).
    geo) [[ "$(docker exec "$(cid kor-travel-geo-api)" python -c 'from kortravelgeo.settings import Settings as S; print(S().dagster_repository_location_name)' 2>/dev/null | tail -1)" == "$LOCATION" ]] ;;
    # Map #1289: summary·pipeline·schedule 명령·writer drain이 `dagster_repository_location_name`으로 좁혀진다.
    map) [[ "$(docker exec "$(cid kor-travel-map-api)" python -c 'from kortravelmap.api.settings import ApiSettings as S; print(S().dagster_repository_location_name)' 2>/dev/null | tail -1)" == "$LOCATION" ]] ;;
    # transport의 소비자는 다른 compose project(`kor-travel-transport-admin`)의 운영 UI다. 지금 떠 있는 것은 옛 전용
    # webserver(14004)를 부르므로 공용 webserver의 다른 테넌트를 보지 못한다. 공용 webserver를 부르는 release는
    # 이름 붙은 query만 이 location으로 좁혀 보낸다(transport `lib/dagster-scope.ts`) — 전환 **뒤에** 배포한다.
    transport) say "transport's consumer (kor-travel-transport-admin) is deployed after the cutover with location-scoped queries (transport runbook)"; return 0 ;;
    *) say "WARNING: no consumer-scoping probe for $TARGET — check by hand (runbook step 4) that its consumers scope to $LOCATION"; return 0 ;;
  esac
}

# ── 앱 쪽 drain 게이트(target마다 다르다 — 앱의 일이라 파생 불가) ──────────────────────
# Map: 공용 instance의 run storage는 새로 시작한다. Map DB의 active operation(`ops.import_jobs`의 queued·running)이
# 옛 instance에만 있는 Dagster run을 가리키면 새 instance의 reconcile sensor가 그 run을 영영 찾지 못한다(Map runbook
# docker-app.md "공유 Dagster plane으로 옮기기 전의 drain"). 그래서 펜스 전과, 펜스·취소 뒤 스위치 전에 0이어야 한다.
# 큐에만 있고 run이 없는 요청(`dagster_run_id` 없음)은 새 instance의 queue sensor가 DB 상태로 이어받으므로 세지 않는다.
app_active_operations() {  # stdout: 남은 active operation 수(0이면 통과). 게이트가 없는 target은 0.
  case "$TARGET" in
    map)
      local db
      db="$(sed -n 's/^KOR_TRAVEL_MAP_POSTGRES_DB=//p' "$ROOT/.env" | tail -1 | tr -d "\"'")"
      [[ "$db" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "cannot tell the Map application database" >&2; return 1; }
      sql "$db" "SELECT count(*) FROM ops.import_jobs WHERE status IN ('queued','running') AND dagster_run_id IS NOT NULL AND quarantined_at IS NULL AND kind IN ('provider_feature_load_run','feature_update_request')"
      ;;
    *) echo 0 ;;
  esac
}
# C7 게이트(D2)의 Dagster 자격증명 파일 — Map의 C7만 공용 gateway를 부른다(Map #1290, Manager ADR-54 개정).
C7_AUTH_FILE=/root/.d2-dagster-basic-auth
write_c7_auth_file() {  # gateway의 user:password 한 줄, root 0600, symlink 아님. 값은 출력하지 않는다.
  # 끝난 전환을 실패로 만들지 않는다(리뷰 L1) — 문제가 있으면 경고와 손으로 할 단계를 찍고 1을 돌려준다.
  local gw user tmp
  c7_manual() {
    say "WARNING: $1 — C7 auth file NOT written. By hand (root, values not echoed): printf '%s:' <KOR_TRAVEL_DAGSTER_UI_USER> > $C7_AUTH_FILE;"
    say "  docker exec <$GATEWAY> cat /run/secrets/kor-travel-dagster-ui-password >> $C7_AUTH_FILE; chmod 0600 $C7_AUTH_FILE"
    say "  (C7 accepts only [\\x21-\\x39\\x3b-\\x7e]+:[\\x21-\\x7e]+ — no spaces or non-ASCII in the password)"
  }
  gw="$(cid "$GATEWAY")"; [[ -n "$gw" ]] || { c7_manual "no gateway container"; return 1; }
  user="$(env_value "$GATEWAY" DAGSTER_UI_USER)"
  [[ "$user" =~ ^[A-Za-z0-9._@-]+$ ]] || { c7_manual "gateway user is not a plain name"; return 1; }
  [[ ! -L "$C7_AUTH_FILE" ]] || { c7_manual "$C7_AUTH_FILE is a symlink"; return 1; }
  tmp="$(mktemp /root/.d2-dagster-basic-auth.XXXXXX)" || { c7_manual "cannot create a temp file"; return 1; }
  if ! { printf '%s:' "$user"; docker exec "$gw" cat /run/secrets/kor-travel-dagster-ui-password; } > "$tmp"; then
    rm -f "$tmp"; c7_manual "cannot read the gateway credential"; return 1
  fi
  # C7(run-c7-prod-live-e2e.sh validate_dagster_basic_auth_file)가 읽는 모양만 쓴다 — 끝 개행 하나는 허용.
  if ! python3 -I - "$tmp" <<'PY'
import re, sys
credential = open(sys.argv[1], "rb").read().removesuffix(b"\n")
sys.exit(0 if re.fullmatch(rb"[\x21-\x39\x3b-\x7e]+:[\x21-\x7e]+", credential) else 1)
PY
  then
    rm -f "$tmp"; c7_manual "the gateway credential is not in the form C7 accepts"; return 1
  fi
  chown 0:0 "$tmp"; chmod 0600 "$tmp"; mv -f "$tmp" "$C7_AUTH_FILE"
  # 효과로 확인: Origin·Sec-Fetch-Site 없는 인증 POST가 gateway를 지나 공용 webserver에 닿는다(값은 출력하지 않는다).
  local port; port="$(env_value "$GATEWAY" DAGSTER_GATEWAY_PORT)"
  if ! python3 - "$C7_AUTH_FILE" "${port:-11001}" <<'PY'
import base64, json, sys, urllib.request
cred = open(sys.argv[1], "rb").read().strip()
req = urllib.request.Request("http://127.0.0.1:%s/graphql" % sys.argv[2],
    data=json.dumps({"query": "{ version }"}).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(cred).decode()})
sys.exit(0 if urllib.request.urlopen(req, timeout=20).status == 200 else 1)
PY
  then
    say "WARNING: wrote $C7_AUTH_FILE but the gateway refused it as a non-browser POST — check the gateway release (ADR-54 amendment)"
    return 1
  fi
  say "wrote $C7_AUTH_FILE (root 0600) from the gateway credential; a non-browser authenticated POST passes the gateway"
}
c7_dagster_attestation() {  # Map C7의 canonical GraphQL URL과 sha256(scripts/lib/c7_prod_runtime.py와 같은 규칙)
  python3 - "$1" <<'PY'
import hashlib, sys
from urllib.parse import urlsplit, urlunsplit
raw = sys.argv[1].strip().rstrip("/")
p = urlsplit(raw)
assert p.scheme == "https" and p.hostname and not p.username and not p.query and not p.fragment, "not an https origin"
host = p.hostname.rstrip(".").lower()
origin = urlunsplit(("https", host + (":%d" % p.port if p.port else ""), "", "", ""))
path = p.path.rstrip("/")
path = path if path.endswith("/graphql") else path + "/graphql"
url = origin + path
print(url, hashlib.sha256(url.encode()).hexdigest())
PY
}

# ── lock G ────────────────────────────────────────────────────────────────
PHASE="lock"
[[ ! -L "$LOCK_DIR" ]] || fail "lock directory is a symlink"
install -d -o root -g root -m 0700 "$LOCK_DIR"
exec 9>>"$LOCK"
[[ "$(stat -c '%u:%a:%h' "$LOCK")" == "0:600:1" ]] || chmod 0600 "$LOCK"
[[ "$(stat -c '%u:%a:%h' "$LOCK")" == "0:600:1" ]] || fail "global mutation lock is unsafe"
flock -n 9 || fail "another manager mutation holds lock G"
say "holding lock G; state dir $STATE"

PHASE="derive"
rev="$(installed_rev)"; [[ -n "$rev" && "$rev" == "$EXPECT"* ]] || fail "installed release is ${rev:-unknown}, expected $EXPECT"
# 형제 프로젝트면 그 compose project·working_dir를 targets에서, compose 파일을 실행 중 code-server의 label에서 읽는다.
external="$(python3 -I -c '
import sys, yaml
spec = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["targets"][sys.argv[2]]
project = spec.get("external_project") or {}
external = (spec.get("dagster") or {}).get("external") or {}
if project:
    print(project["project"], project["working_dir"], external.get("code_server", ""))
' "$ROOT/config/docker-targets.yml" "$TARGET")" || fail "cannot read targets.$TARGET from the installed release"
if [[ -n "$external" ]]; then
  read -r TPROJECT TWD ext_code <<<"$external"
  [[ "$TPROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ && "$TWD" == /* && -n "$ext_code" ]] \
    || fail "targets.$TARGET.external_project / dagster.external are incomplete"
  [[ -n "$EXTERNAL_ENV_FILE" ]] || fail "EXTERNAL_ENV_FILE is required for the sibling project $TARGET (its compose env file)"
  # 비밀이 든 파일이다 — 일반 파일이고 소유자만 읽는다(되돌리기 때는 그 프로젝트의 이전 release가 working_dir를
  # 다시 동기화하며 지울 수 있어 밖에 복사해 둔 것을 쓴다).
  [[ -f "$EXTERNAL_ENV_FILE" && ! -L "$EXTERNAL_ENV_FILE" && "$(stat -c '%a' "$EXTERNAL_ENV_FILE")" =~ ^[46]00$ ]] \
    || fail "EXTERNAL_ENV_FILE must be a regular file readable by its owner only (0600/0400)"
  ext_c="$(docker ps -aq --no-trunc --filter "label=com.docker.compose.project=$TPROJECT" \
    --filter "label=com.docker.compose.service=$ext_code" --filter "label=com.docker.compose.oneoff=False")"
  [[ "$(count_lines <<<"$ext_c")" == 1 ]] || fail "expected one $TPROJECT $ext_code container to read the compose files from"
  [[ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$ext_c")" == "$TWD" ]] \
    || fail "$ext_code was not created from $TWD"
  IFS=',' read -r -a TFILES <<<"$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$ext_c")"
  (( ${#TFILES[@]} > 0 )) || fail "$ext_code carries no compose config_files label"
  for f in "${TFILES[@]}"; do
    [[ "$f" == "$TWD"/* && -f "$f" && ! -L "$f" ]] || fail "compose file $f of $TPROJECT is not a regular file inside $TWD"
  done
  say "sibling project $TPROJECT in $TWD: compose files ${TFILES[*]##*/}, env file ${EXTERNAL_ENV_FILE##*/}"
fi
facts="$(derive)" || fail "cannot derive $TARGET's Dagster family from the installed release"
printf '%s\n' "$facts" > "$STATE/family.txt"
CODE="$(sed -n 's/^CODE=//p' <<<"$facts")"; OLD_WEBSERVER="$(sed -n 's/^OLD_WEBSERVER=//p' <<<"$facts")"
OLD_DAEMON="$(sed -n 's/^OLD_DAEMON=//p' <<<"$facts")"; OLD_GATEWAYS="$(sed -n 's/^OLD_GATEWAYS=//p' <<<"$facts")"
LOCATION="$(sed -n 's/^LOCATION=//p' <<<"$facts")"; OLD_WEBSERVER_PORT="$(sed -n 's/^OLD_WEBSERVER_PORT=//p' <<<"$facts")"
CONSUMERS="$(sed -n 's/^CONSUMERS=//p' <<<"$facts")"; INTERNAL_CONSUMERS="$(sed -n 's/^INTERNAL_CONSUMERS=//p' <<<"$facts")"
PLANE_PORT="$(sed -n 's/^PLANE_WEBSERVER_PORT=//p' <<<"$facts")"
PLANE_SWITCH="$(sed -n 's/^CONTROL_PLANE=//p' <<<"$facts")"; CODE_SHARED="$(sed -n 's/^CODE_HAS_SHARED_URL=//p' <<<"$facts")"
DAEMON="$(fact PLANE_DAEMON "$facts")"; WEBSERVER="$(fact PLANE_WEBSERVER "$facts")"; GATEWAY="$(fact PLANE_GATEWAY "$facts")"
[[ -n "$DAEMON" && -n "$WEBSERVER" && -n "$GATEWAY" ]] || fail "cannot derive the shared plane services"
[[ "$OLD_WEBSERVER_PORT" =~ ^[0-9]+$ ]] || fail "old webserver port is not a literal: $OLD_WEBSERVER_PORT"
[[ -n "$LOCATION" && -n "$CODE" ]] || fail "incomplete family"
SWITCH=compose; [[ "$PINNED_TARGETS" == *" $TARGET "* ]] && SWITCH=pinned
if [[ "$SWITCH" == pinned ]]; then
  # pinned 재구축은 rehearsal/rebuildable 호스트에서만 돈다(c6c_deployment) — 펜스 **전에** 확인한다.
  { grep -Eqx "KTDM_DEPLOYMENT_ENVIRONMENT=['\"]?rehearsal['\"]?" "$ROOT/.env"       && grep -Eqx "KTDM_DEPLOYMENT_LIFECYCLE=['\"]?rebuildable['\"]?" "$ROOT/.env"; }     || fail "the pinned rebuild would refuse: .env is not KTDM_DEPLOYMENT_ENVIRONMENT=rehearsal / KTDM_DEPLOYMENT_LIFECYCLE=rebuildable"
  [[ -x "$ROOT/scripts/run-pinned-rebuild-once" ]] || fail "the installed release has no scripts/run-pinned-rebuild-once"
fi
say "code-server $CODE, old $OLD_WEBSERVER (port $OLD_WEBSERVER_PORT) + $OLD_DAEMON${OLD_GATEWAYS:+ + $OLD_GATEWAYS}, location $LOCATION, consumers [$CONSUMERS], switch $SWITCH"
if [[ -n "$TWD" ]]; then
  # 형제 프로젝트의 code-server는 `--no-build`로 다시 만든다 — 그 이미지가 호스트에 있어야 한다(펜스 전에 본다).
  CODE_IMAGE="$(fact CODE_IMAGE "$facts")"
  [[ -n "$CODE_IMAGE" ]] && docker image inspect "$CODE_IMAGE" >/dev/null 2>&1 \
    || fail "the image $TPROJECT's $CODE would run (${CODE_IMAGE:-none}) is not on this host — build it with the sibling project's prepare step first"
  say "$CODE will run $CODE_IMAGE"
  # Manager 자신의 code-server는 Manager가 짓는 이미지라 호스트와 같은 dagster로 맞춰지지만, 형제 프로젝트의 이미지는 그
  # 저장소가 짓는다. 다른 버전이면 공용 webserver가 unhealthy가 되어(버전 상한) 전 테넌트가 함께 내려가고, 그것이 펜스
  # **뒤**의 wait_health에서야 드러난다. 그래서 펜스 전에 같은지 본다.
  dagster_versions_match "$CODE_IMAGE" || fail "the dagster version of $CODE_IMAGE differs from the shared plane's — pin the sibling project to the host version and prepare again"
fi

# ═════════════════════════════════════════════════════════════════════════
if [[ "$MODE" == rollback ]]; then
  PHASE="f-precheck"
  [[ "$PLANE_SWITCH" == own && "$CODE_SHARED" == no ]] || fail "the installed release still has $TARGET on the shared plane — install the rollback release first"
  ! installed_workspace_lists || fail "the installed shared workspace still lists $LOCATION"
  # 되돌리기도 같은 앱 게이트를 지난다: active operation이 공용 instance의 run을 가리키면 옛 reconcile sensor가 그 run을
  # 영영 찾지 못한다. 공용 plane이 아직 이 location을 싣는 동안(plane 재생성 전) 그쪽 reconcile이 끝내게 한다.
  active="$(app_active_operations)" || fail "cannot read $TARGET's active operations"
  [[ "$active" == 0 ]] || fail "$active active $TARGET operations point at runs of the shared instance — let them settle there first (runbook §7 Map drain)"
  pair_runs_report
  cancel_runs_py > "$STATE/cancel-runs.py"
  daemon_c="$(cid "$DAEMON")"
  daemon_state="absent"; [[ -z "$daemon_c" ]] || daemon_state="$(docker inspect -f '{{.State.Status}}' "$daemon_c")"
  if [[ "$daemon_state" == running ]]; then
    PHASE="f-plane"
    compose up -d --no-deps "$DAEMON" "$WEBSERVER" >/dev/null
    wait_health "$WEBSERVER" 300
    wait_health "$DAEMON" 300
    locations="$(graphql '{ workspaceOrError { __typename ... on Workspace { locationEntries { name } } } }')" || fail "cannot ask the shared webserver for its workspace"
    grep -q '"Workspace"' <<<"$locations" || fail "the shared webserver did not return a workspace"
    ! grep -q "\"$LOCATION\"" <<<"$locations" || fail "the shared webserver still lists $LOCATION"
    say "$LOCATION is out of the shared workspace"
    PHASE="f-cancel"
    docker exec -i "$(cid "$DAEMON")" python -I - "$NEW_DB" "$LOCATION" < "$STATE/cancel-runs.py" | sed 's/^/  /'
    live="$(sql "$NEW_DB" "$(TARGET_RUNS_SQL " AND r.status IN $LIVE")")"
    [[ "$live" == 0 ]] || fail "$live $TARGET runs are still live in $NEW_DB"
  else
    PHASE="f-plane"
    say "the shared daemon is $daemon_state — SKIPPING the plane recreate and the shared-run cancel. Once the plane is repaired:"
    say "  docker exec -i <kor-travel-dagster-daemon> python -I - $NEW_DB $LOCATION < $STATE/cancel-runs.py"
  fi
  PHASE="f-old"
  if [[ "$SWITCH" == pinned ]]; then
    pinned_rebuild   # own release: the old webserver·daemon are slot/companion services again
  else
    tup "$CODE" >/dev/null
    wait_health "$CODE" 420
    tup "$OLD_WEBSERVER" >/dev/null
    wait_health "$OLD_WEBSERVER" 300
    # shellcheck disable=SC2086 # 게이트웨이·소비자 목록은 공백으로 나뉜 서비스 이름이다
    tup "$OLD_DAEMON" $OLD_GATEWAYS $CONSUMERS >/dev/null
  fi
  wait_health "$CODE" 420
  env_has "$CODE" KOR_TRAVEL_DAGSTER_SHARED_PG_URL && fail "$CODE still has the shared metadata URL"
  wait_health "$OLD_WEBSERVER" 300
  wait_health "$OLD_DAEMON" 300
  n="$(old_graphql "$(instigators_q)" | running_instigators | count_lines)"
  (( n > 0 )) || fail "the old $TARGET instance shows no RUNNING instigators"
  say "old $TARGET instance: $n RUNNING instigators"
  PHASE="done"
  say "rollback complete. Catch-up: the old daemon may launch up to max_catchup_runs (default 5) missed runs per schedule."
  exit 0
fi

# ═════════════════════════════════════════════════════════════════════════
# 스위치부터 끝까지 — forward의 펜스 뒤와 resume이 같은 길을 간다.
switch_and_verify() {
  PHASE="d-switch"
  if [[ -s "$STATE/switch_ts" ]]; then
    SWITCH_TS="$(cat "$STATE/switch_ts")"
  else
    SWITCH_TS="$(sql "$NEW_DB" "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')")"
    echo "$SWITCH_TS" > "$STATE/switch_ts"
  fi
  if [[ "$SWITCH" == pinned ]]; then
    pinned_rebuild
  else
    SWITCH_STARTED=1
    # shellcheck disable=SC2086
    tup "$CODE" $CONSUMERS >/dev/null
  fi
  wait_health "$CODE" 600
  env_has "$CODE" KOR_TRAVEL_DAGSTER_SHARED_PG_URL || fail "$CODE did not get the shared metadata URL"
  local internal entry svc var want s before now
  IFS=';' read -r -a internal <<<"$INTERNAL_CONSUMERS"
  for entry in "${internal[@]}"; do
    [[ -n "$entry" ]] || continue
    # "<서비스> <env> <기대값>" — 기대값은 derive가 계약대로 만든다(공용 webserver loopback + 종류의 경로).
    read -r svc var want <<<"$entry"
    running "$svc" || fail "consumer $svc is not running"
    [[ "$(env_value "$svc" "$var")" == "$want" ]] || fail "$svc $var does not point at the shared webserver ($want)"
  done
  # plane: Manager의 plane을 아는 pinned 재구축(ADR-54 개정)은 smoke 전에 이미 plane을 맞췄다 — 그러면 여기는 검증이다.
  # 옛 release의 재구축이거나 compose 경로면 여기서 맞춘다. 판정은 실제 상태로: 두 컨테이너가 펜스 뒤에 새로 만들어졌고
  # 설치본 workspace의 digest를 싣고 있으면 다시 만들지 않는다(frozen render와 평범한 render의 config hash가 달라 `up`이
  # 한 번 더 재생성하는 것을 피한다).
  local want_digest plane_current=1
  want_digest="$(sha256sum "$ROOT/config/dagster-shared/workspace.yaml" | cut -c1-16)"
  for s in "$DAEMON" "$WEBSERVER"; do
    before="$(awk -v s="$s" '$1==s{print $2}' "$STATE/ids-before.txt")"; now="$(cid "$s")"
    if [[ -z "$now" || "$now" == "$before" ]] || ! running "$s" \
       || [[ "$(env_value "$s" KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST)" != "$want_digest" ]]; then
      plane_current=0
    fi
  done
  if (( plane_current )); then
    say "the plane was already converged (recreated after the fence with workspace digest $want_digest) — verifying only"
  else
    compose up -d --no-deps "$DAEMON" "$WEBSERVER" >/dev/null
  fi
  for s in "$CODE" $CONSUMERS "$DAEMON" "$WEBSERVER"; do
    before="$(awk -v s="$s" '$1==s{print $2}' "$STATE/ids-before.txt")"; now="$(cid "$s")"
    [[ -n "$now" && "$now" != "$before" ]] || fail "$s was not recreated (container id unchanged since before the fence)"
    say "$s recreated"
  done

  PHASE="e-verify"
  wait_health "$WEBSERVER" 420
  wait_health "$DAEMON" 420
  graphql '{ workspaceOrError { __typename ... on Workspace { locationEntries { name locationOrLoadError { __typename } } } } }' \
    | python3 -c '
import json, sys
w = json.load(sys.stdin)["data"]["workspaceOrError"]
got = {e["name"]: (e["locationOrLoadError"] or {}).get("__typename") for e in w["locationEntries"]}
sys.exit(0 if got.get(sys.argv[1]) == "RepositoryLocation" else "location not loaded: %s" % got)' "$LOCATION" \
    || fail "the shared webserver did not load $LOCATION"
  say "$LOCATION is RepositoryLocation on the shared webserver"
  graphql "$(instigators_q)" | running_instigators > "$STATE/new-running.txt" || fail "cannot read the shared RUNNING instigators"
  diff -u "$STATE/old-running.txt" "$STATE/new-running.txt" >&2 || fail "instigator parity: RUNNING schedules/sensors differ between the old instance and $NEW_DB"
  say "instigator parity: $(count_lines "$STATE/new-running.txt") RUNNING instigators, same names as before the fence"

  PHASE="e-retire"
  for s in "$OLD_DAEMON" "$OLD_WEBSERVER" $OLD_GATEWAYS; do
    c="$(cid "$s")"
    if [[ -n "$c" ]]; then
      running "$s" && fail "$s is running again — stop the old daemon now: active double fire"
      docker rm "$c" >/dev/null; say "removed the stopped $s container"
    fi
  done

  flock -u 9; exec 9>&-
  PHASE="e-watch"
  say "released lock G; waiting for the first $TARGET tick in $NEW_DB (up to $FIRST_TICK_TIMEOUT_S s)"
  local deadline max_conn=0 conn old_ticks old_runs new_ticks
  deadline=$(( $(date +%s) + FIRST_TICK_TIMEOUT_S ))
  echo "utc,shared_role_connections,old_ticks_after_fence,old_runs_after_fence,target_ticks_after_switch" > "$STATE/g3a.csv"
  while :; do
    conn="$(sql "$NEW_DB" "SELECT count(*) FROM pg_stat_activity WHERE usename = '$SHARED_ROLE'")"
    old_ticks="$(sql "$OLD_DB" "SELECT count(*) FROM job_ticks WHERE create_timestamp > '$FENCE_TS'")"
    old_runs="$(sql "$OLD_DB" "SELECT count(*) FROM runs WHERE create_timestamp > '$FENCE_TS'")"
    new_ticks="$(sql "$NEW_DB" "$(TARGET_TICKS_SQL " AND create_timestamp > '$SWITCH_TS'")")"
    echo "$(date -u +%H:%M:%S),$conn,$old_ticks,$old_runs,$new_ticks" >> "$STATE/g3a.csv"
    (( conn > max_conn )) && max_conn=$conn
    [[ "$old_ticks" == 0 && "$old_runs" == 0 ]] || fail "double fire: $old_ticks ticks / $old_runs runs written to $OLD_DB after fence_ts — stop the old daemon now"
    if (( new_ticks > 0 )); then say "first $TARGET tick after the switch landed in $NEW_DB ($new_ticks so far)"; break; fi
    (( $(date +%s) < deadline )) || fail "no $TARGET tick in $NEW_DB within $FIRST_TICK_TIMEOUT_S s"
    sleep "$SAMPLE_EVERY_S"
  done
  say "G3-a: peak $max_conn connections for $SHARED_ROLE so far (CONNECTION LIMIT $(fact PLANE_ROLE_CONNECTION_LIMIT "$facts")) — samples in $STATE/g3a.csv"
  PHASE="done"
  say "forward cutover verified. By hand (runbook step 4): the first $TARGET run reaches SUCCESS in $NEW_DB; the switch SQL on"
  say "  dagster/code_location is 0; $TARGET's API/UI show only $LOCATION; keep sampling G3-a under load."
  if [[ -n "$TWD" ]]; then
    say "sibling project $TPROJECT: only $CODE was recreated. Converge the rest with its own deploy (transport:"
    say "  scripts/deploy-server14.sh, then scripts/deploy-transport-admin-server14.sh for the location-scoped UI)."
  fi
  local shared_url
  shared_url="$(sed -n 's/^KTDM_PROD_URL_DAGSTER=//p' "$ROOT/.env" | tail -1 | tr -d "\"'")"
  say "edge (information, not a prerequisite): $TARGET's old public Dagster hostname (e.g. $TARGET-dagster.digitie.mywire.org) has"
  say "  no upstream any more — the owner chose no redirect. $TARGET's Dagster UI is now the shared one: ${shared_url:-<KTDM_PROD_URL_DAGSTER>}"
  if [[ "$TARGET" == map ]]; then
    write_c7_auth_file || true
    local attest
    attest="$(c7_dagster_attestation "${shared_url:-}")" || fail "cannot derive the C7 Dagster attestation from KTDM_PROD_URL_DAGSTER"
    say "C7 (D2): set these three in /root/.d2-live.env before the next chain run (repin flips the service and plane keys):"
    say "  E2E_DAGSTER_URL=${attest%% *}"
    say "  E2E_C7_EXPECTED_DAGSTER_ORIGIN_SHA256=${attest##* }"
    say "  E2E_DAGSTER_BASIC_AUTH_FILE=$C7_AUTH_FILE"
  fi
}

# ═════════════════════════════════════════════════════════════════════════
if [[ "$MODE" == resume ]]; then
  PHASE="r-precheck"
  [[ "$PLANE_SWITCH" == shared && "$CODE_SHARED" == yes ]] || fail "the installed release does not put $TARGET on the shared plane — resume needs the forward release"
  installed_workspace_lists || fail "the installed shared workspace does not list $LOCATION"
  for f in fence_ts old_db selectors.txt old-running.txt ids-before.txt tick_baseline; do
    [[ -s "$RESUME_FROM/$f" ]] || fail "$RESUME_FROM/$f is missing — not a forward state dir that got past the fence"
    cp -p "$RESUME_FROM/$f" "$STATE/$f"
  done
  [[ ! -s "$RESUME_FROM/switch_ts" ]] || cp -p "$RESUME_FROM/switch_ts" "$STATE/switch_ts"
  FENCE_TS="$(cat "$STATE/fence_ts")"; OLD_DB="$(cat "$STATE/old_db")"; SHARED_TICKS_BASELINE="$(cat "$STATE/tick_baseline")"
  [[ "$OLD_DB" =~ ^[a-z_][a-z0-9_]*$ && "$OLD_DB" != "$NEW_DB" ]] || fail "bad old database in $RESUME_FROM/old_db"
  [[ "$FENCE_TS" =~ ^[0-9-]+\ [0-9:.]+$ ]] || fail "bad fence_ts in $RESUME_FROM"
  set_selectors "$STATE/selectors.txt"
  say "resuming $RESUME_FROM: old database $OLD_DB, fence_ts $FENCE_TS"
  # 펜스가 그대로인가 — 옛 daemon·webserver가 멈춰 있고, 펜스 뒤 옛 DB에 아무것도 쓰이지 않았다.
  for s in "$OLD_DAEMON" "$OLD_WEBSERVER" $OLD_GATEWAYS; do
    ! running "$s" || fail "$s is running — the fence is broken; stop it before resuming (double fire)"
  done
  n="$(sql "$OLD_DB" "SELECT (SELECT count(*) FROM job_ticks WHERE create_timestamp > '$FENCE_TS') + (SELECT count(*) FROM runs WHERE create_timestamp > '$FENCE_TS')")"
  [[ "$n" == 0 ]] || fail "$n ticks/runs were written to $OLD_DB after fence_ts — the old instance ran again; investigate before resuming"
  left="$(sql "$OLD_DB" "SELECT count(*) FROM runs WHERE status IN $LIVE")"
  [[ "$left" == 0 ]] || fail "$left runs in flight in $OLD_DB"
  pair_runs_report
  FENCED=1
  switch_and_verify
  exit 0
fi

# ═════════════════════════════════════════════════════════════════════════
PHASE="a-precheck"
[[ "$PLANE_SWITCH" == shared && "$CODE_SHARED" == yes ]] || fail "the installed release does not put $TARGET on the shared plane"
installed_workspace_lists || fail "the installed shared workspace does not list $LOCATION"
for s in "$DAEMON" "$WEBSERVER" "$GATEWAY"; do [[ "$(health "$s")" == healthy ]] || fail "$s is $(health "$s")"; done
{ running "$OLD_DAEMON" && running "$OLD_WEBSERVER"; } || fail "the old daemon/webserver are not both running (already fenced? then use resume)"
[[ "$(health "$CODE")" == healthy ]] || fail "$CODE is $(health "$CODE")"
env_has "$CODE" KOR_TRAVEL_DAGSTER_SHARED_PG_URL && fail "the running $CODE already has the shared metadata URL"
OLD_DB="$(current_db_of "$CODE" | tail -1)"
[[ "$OLD_DB" =~ ^[a-z_][a-z0-9_]*$ && "$OLD_DB" != "$NEW_DB" ]] || fail "cannot tell $CODE's old database ($OLD_DB)"
echo "$OLD_DB" > "$STATE/old_db"
say "old instance database: $OLD_DB"
load_selectors
SHARED_TICKS_BASELINE="$(sql "$NEW_DB" "$(TARGET_TICKS_SQL)")"
echo "$SHARED_TICKS_BASELINE" > "$STATE/tick_baseline"
plane_clean || fail "the running plane already loaded the new workspace or ran $TARGET — double-fire risk (restarted since install?)"
say "the running plane does not carry $LOCATION yet ($TARGET tick baseline $SHARED_TICKS_BASELINE)"
consumer_scope_check || fail "a consumer of $TARGET is not scoped to $LOCATION — it would see or act on other tenants"
old_graphql "$(instigators_q)" > "$STATE/old-instigators.json" || fail "cannot read the old instigators"
running_instigators < "$STATE/old-instigators.json" > "$STATE/old-running.txt" || fail "cannot read the old RUNNING instigators"
drift="$(instigators_off_their_code_default < "$STATE/old-instigators.json")" || fail "cannot compare the old instigator states with their code defaults"
[[ -z "$drift" ]] || fail "instigators whose state on the old instance differs from default_status in code (the shared instance starts from the code default, states are not copied) — align them first: $(tr '\n' ' ' <<<"$drift")"
active="$(app_active_operations)" || fail "cannot read $TARGET's active operations"
[[ "$active" == 0 ]] || fail "$active active $TARGET operations still point at runs of the old instance — drain first (runbook §7 Map drain)"
(( $(count_lines "$STATE/old-running.txt") > 0 )) || fail "the old instance shows no RUNNING instigators"
say "old instance: $(count_lines "$STATE/old-running.txt") RUNNING instigators"
pair_runs_report
# G3-a(ADR-54): 설치본이 정한 공용 role 연결 상한을 live role에 건다 — db-init one-shot은 멱등 ALTER다(이미 맞으면 같은 값).
# 펜스 전이라 실패하면 아무것도 바뀌지 않은 채 멈춘다. lock G 아래다.
DB_INIT="$(fact PLANE_DB_INIT "$facts")"; WANT_LIMIT="$(fact PLANE_ROLE_CONNECTION_LIMIT "$facts")"
[[ -n "$DB_INIT" && "$WANT_LIMIT" =~ ^[0-9]+$ ]] || fail "cannot derive the shared db-init one-shot or its connection limit"
compose run -T --rm --no-deps "$DB_INIT" >"$STATE/db-init.log" 2>&1 || { tail -n 20 "$STATE/db-init.log" >&2; fail "$DB_INIT failed"; }
got_limit="$(sql postgres "SELECT rolconnlimit FROM pg_roles WHERE rolname = '$SHARED_ROLE'")"
[[ "$got_limit" == "$WANT_LIMIT" ]] || fail "$SHARED_ROLE CONNECTION LIMIT is ${got_limit:-?}, the installed release wants $WANT_LIMIT"
say "$SHARED_ROLE CONNECTION LIMIT $got_limit (applied by $DB_INIT)"
for s in "$CODE" $CONSUMERS "$DAEMON" "$WEBSERVER"; do c="$(cid "$s")"; echo "$s $c"; done > "$STATE/ids-before.txt"

PHASE="b-drain"
sql "$OLD_DB" "SELECT status, count(*) FROM runs WHERE status IN $LIVE GROUP BY 1 ORDER BY 1" | tee "$STATE/drain-before.txt" | sed 's/^/  in flight: /'

PHASE="c-fence"
plane_clean || fail "the plane changed since the precheck — double-fire risk; not fencing"
FENCED=1
for s in "$OLD_DAEMON" "$OLD_WEBSERVER" $OLD_GATEWAYS; do
  c="$(cid "$s")"; [[ -n "$c" ]] || fail "no container for $s"
  docker stop -t 60 "$c" >/dev/null
  running "$s" && fail "$s is still running"
  say "stopped $s"
done
FENCE_TS="$(sql "$OLD_DB" "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')")"
echo "$FENCE_TS" > "$STATE/fence_ts"; say "fence_ts=$FENCE_TS (UTC)"
[[ "$SWITCH" != pinned ]] || say "from here until the plane recreate $TARGET has no scheduler (the pinned rebuild takes about 15-40 min)"
cancel_runs_py > "$STATE/cancel-runs.py"
docker exec -i "$(cid "$CODE")" python -I - "$OLD_DB" < "$STATE/cancel-runs.py" | tee "$STATE/drain-canceled.txt" | sed 's/^/  /'
left="$(sql "$OLD_DB" "SELECT count(*) FROM runs WHERE status IN $LIVE")"
[[ "$left" == 0 ]] || fail "$left runs still in flight in $OLD_DB"
# 펜스와 취소 사이에 old instance가 띄운 run을 가리키는 active operation이 생겼으면 여기서 멈춘다 — 아직 아무것도
# 옮기지 않았으므로 recover가 옛 서비스를 되살리고, 옛 reconcile sensor가 그 상태를 DB에 반영한다.
active="$(app_active_operations)" || fail "cannot read $TARGET's active operations"
[[ "$active" == 0 ]] || fail "$active active $TARGET operations point at runs of the old instance after the fence — retry after the old instance settles them"

switch_and_verify
