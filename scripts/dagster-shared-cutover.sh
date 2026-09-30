#!/usr/bin/env bash
# weather를 공용 Dagster plane으로 옮기는 창(ADR-54, platform-topology.md §7 전환 runbook) — n150에서 root로.
#
#   weather-cutover.sh forward  <merge-sha>   # 전환 PR을 설치한 **직후**: (a)~(e)
#   weather-cutover.sh rollback <release-sha> # weather가 `own`인 release(예: 525ee5e)를 설치한 뒤: (f)
#
# 환경: FIRST_TICK_TIMEOUT_S(기본 4500 — 첫 tick을 기다리는 상한), SAMPLE_EVERY_S(기본 30).
#
# **SSH 세션에 매달아 돌리지 않는다.** 세션이 끊기면 펜스와 스위치 사이에서 멈춘다. systemd-run으로 돌린다:
#   systemd-run --unit=weather-cutover --collect /root/weather-cutover.sh forward <merge-sha>
#   journalctl -fu weather-cutover
# stdin이 tty면 거부한다(tmux 안이면 FORCE_TTY=1). HUP·INT·TERM은 실패로 다뤄 아래 복구를 탄다.
#
# **설치와 forward는 이어서 돈다.** plane은 workspace를 설치본 symlink(`/opt/kor-travel-docker-manager`)를 거쳐
# 붙인다. 전환 release를 설치한 뒤 plane 컨테이너가 한 번이라도 다시 시작되면(crash 재시작, dockerd 재시작,
# 대시보드의 restart) 새 workspace를 읽어 weather를 로드하는데, 옛 weather daemon은 아직 tick한다 — 이중 발화.
# 그래서 설치와 이 스크립트 사이에 아무도 plane을 재시작하지 않는다. 스크립트는 (a)와 펜스 직전에 그것을 확인한다
# (plane이 붙인 workspace에 weather가 없고, `dagster_shared`에 tick·run이 0).
#
# 전제(소유자): 에지의 `weather-dagster.digitie.mywire.org` → `https://dagster.digitie.mywire.org` redirect. 배포된
# weather-web의 브라우저 번들은 `NEXT_PUBLIC_DAGSTER_URL`을 빌드 때 구워 옛 host를 링크한다(env 변경은 SSR·다음
# 빌드에만 먹는다 — weather-web 빌드 인자 추가는 TODO).
#
# 원칙: 비밀을 출력하지 않는다(compose config를 찍지 않고, DB에서는 개수·이름·상태만 읽는다). 모든 단계는 실패하면
# 그 자리에서 멈추고 무엇이 실패했는지 말한다 — 펜스 뒤의 실패는 weather가 scheduler 없이 남지 않게 옛 서비스를
# 되살리거나(code-server 재생성 전) 정확한 복구 명령을 찍는다(그 뒤). 상태를 바꾸는 명령은 lock G(Manager의 host
# 변경 lock, ADR-51 C) 아래에서만 돈다. prod `ensure`는 거부되므로 설치본의 compose를 직접 부른다.
set -Eeuo pipefail
umask 077

MODE="${1:-}"; EXPECT="${2:-}"
[[ "$MODE" == forward || "$MODE" == rollback ]] && [[ "$EXPECT" =~ ^[0-9a-f]{7,40}$ ]] || {
  echo "usage: $0 forward <merge-sha> | rollback <release-sha>" >&2; exit 64; }
[[ "$(id -u)" == 0 ]] || { echo "run as root" >&2; exit 64; }
if [[ -t 0 && "${FORCE_TTY:-0}" != 1 ]]; then
  echo "refusing to run on a terminal: a dropped session would stop between fence and switch." >&2
  echo "run: systemd-run --unit=weather-cutover --collect $0 $MODE $EXPECT   (inside tmux: FORCE_TTY=1)" >&2
  exit 64
fi

ROOT=/opt/kor-travel-docker-manager
PROJECT=kor-travel-docker-manager
LOCK_DIR=/run/lock/kor-travel-docker-manager
LOCK="$LOCK_DIR/global-mutation.lock"
PG=kor-travel-shared-postgres
OLD_DB=kor_travel_weather_dagster
NEW_DB=dagster_shared
SHARED_ROLE=kor_travel_dagster_shared_app
LOCATION=kortravelweather_dagster.definitions
DECLARED_RUNNING=17
PRE_FLIP_WORKSPACE_DIGEST=b2311ec335e60898   # `load_from: []` — 전환 전 설치본(525ee5e, 6f30fd5와 같은 파일)의 workspace
ROLLBACK_RELEASE=525ee5e   # 전환 직전 설치본 — 6f30fd5로 되돌리면 #447(파생 legacy)이 빠진다
FIRST_TICK_TIMEOUT_S="${FIRST_TICK_TIMEOUT_S:-4500}"
SAMPLE_EVERY_S="${SAMPLE_EVERY_S:-30}"
STATE=/root/weather-cutover-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$STATE"

# compose 서비스 이름(정본 compose). 컨테이너는 라벨로 찾는다 — 이름을 외우지 않는다.
CODE=kor-travel-weather-dagster-code-server
WEB=kor-travel-weather-web
OLD_DAEMON=kor-travel-weather-dagster-daemon
OLD_WEBSERVER=kor-travel-weather-dagster-webserver
OLD_GATEWAY=kor-travel-weather-dagster-gateway
DAEMON=kor-travel-dagster-daemon
WEBSERVER=kor-travel-dagster-webserver
GATEWAY=kor-travel-dagster-gateway

PHASE="init"
FENCED=0          # 옛 서비스를 하나라도 멈췄다
say() { printf '[%s] %s: %s\n' "$(date -u +%H:%M:%SZ)" "$PHASE" "$*"; }
count_lines() { awk 'NF { n++ } END { print n + 0 }' "$@"; }  # grep -c와 달리 0개에도 성공한다

compose() { docker compose -p "$PROJECT" --project-directory "$ROOT" -f "$ROOT/docker-compose.yml" --env-file "$ROOT/.env" "$@"; }
cid() {  # compose 서비스의 컨테이너 id(멈춘 것 포함, `compose run` 일회성 제외), 없으면 빈 문자열, 둘 이상이면 실패
  local ids n
  ids="$(docker ps -aq --no-trunc --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=$1" --filter "label=com.docker.compose.oneoff=False")"
  n="$(count_lines <<<"$ids")"
  (( n <= 1 )) || fail "$n containers match compose service $1 — refusing to guess which one is live"
  printf '%s\n' "$ids"
}
health() { local c; c="$(cid "$1")"; [[ -n "$c" ]] && docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c" || echo absent; }
running() { local c; c="$(cid "$1")"; [[ -n "$c" ]] && [[ "$(docker inspect -f '{{.State.Running}}' "$c")" == true ]]; }
env_has() { local c; c="$(cid "$1")"; [[ -n "$c" ]] && docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" | grep -q "^$2="; }
env_value() { docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$(cid "$1")" | sed -n "s/^$2=//p"; }  # 비밀이 아닌 키에만 쓴다
sql() {  # sql <db> <statement> — 공용 instance의 admin으로 socket 접속, 결과만(값에 비밀 없음)
  docker exec -u postgres "$PG" sh -c 'psql -X -qAt -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 11000 -U "$POSTGRES_USER" -d "$0" -c "$1"' "$1" "$2"; }

# 펜스 뒤 실패의 복구(적대 리뷰 H3). code-server를 재생성하기 전이면 옛 instance가 그대로다 — plane이 weather를
# 로드하지 않았다면 옛 서비스를 다시 켠다. 그 뒤면 정확한 되돌리기 명령을 찍는다.
code_moved() {  # weather code-server가 공용 instance로 넘어갔는가 — 플래그가 아니라 실제 상태로 본다
  local before now
  before="$(awk -v s="$CODE" '$1==s{print $2}' "$STATE/ids-before.txt" 2>/dev/null || true)"
  now="$(docker ps -aq --no-trunc --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=$CODE" --filter "label=com.docker.compose.oneoff=False" 2>/dev/null | head -1 || true)"
  [[ -z "$before" || -z "$now" || "$now" != "$before" ]] && return 0
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$now" 2>/dev/null | grep -q '^KOR_TRAVEL_DAGSTER_SHARED_PG_URL='
}
recover() {
  (( FENCED )) || return 0
  if ! code_moved; then
    if plane_workspace_clean quiet; then
      echo "recover: code-server not recreated and the plane does not list weather — restarting the old weather services" >&2
      for s in "$OLD_WEBSERVER" "$OLD_DAEMON" "$OLD_GATEWAY"; do
        c="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$s" \
          --filter "label=com.docker.compose.oneoff=False" 2>/dev/null | head -1 || true)"
        if [[ -n "$c" ]] && docker start "$c" >/dev/null 2>&1; then echo "recover: started $s" >&2; else echo "recover: COULD NOT start $s" >&2; fi
      done
      echo "recover: the old weather instance is scheduling again; the shared plane has no weather. Investigate, then retry forward." >&2
    else
      echo "recover: the shared plane already lists weather — NOT restarting the old daemon (double fire)." >&2
      echo "recover: install $ROLLBACK_RELEASE, then run: $0 rollback $ROLLBACK_RELEASE" >&2
    fi
  else
    echo "recover: weather's code-server was already moved. To go back: install $ROLLBACK_RELEASE, then run: $0 rollback $ROLLBACK_RELEASE" >&2
  fi
}
IN_FAIL=0
fail() {
  # 명령 치환(subshell) 안에서는 이유만 찍고 끝낸다 — 복구는 바깥 셸에서 한 번만 돈다(바깥의 ERR가 이어 받는다).
  if (( BASH_SUBSHELL > 0 )); then
    printf '[%s] %s: FAILED — %s\n' "$(date -u +%H:%M:%SZ)" "$PHASE" "$*" >&2
    exit 1
  fi
  (( IN_FAIL )) && exit 1
  IN_FAIL=1
  trap - ERR HUP INT TERM
  printf '[%s] %s: FAILED — %s (state: %s)\n' "$(date -u +%H:%M:%SZ)" "$PHASE" "$*" "$STATE" >&2
  recover || true
  exit 1
}
# subshell의 ERR는 무시한다 — 실패한 치환은 바깥 명령을 실패시키고 거기서 한 번 잡힌다(중복 출력 없음).
trap '(( BASH_SUBSHELL > 0 )) || fail "a command failed at line $LINENO (see the message above, if any)"' ERR
trap 'fail "interrupted by a signal"' HUP INT TERM

wait_health() {  # wait_health <service> <seconds>
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
container_graphql() {  # container_graphql <service> <port> — stdin의 query를 그 컨테이너 안에서(loopback) 묻는다
  docker exec -i "$(cid "$1")" python -I -c '
import json, sys, urllib.request as u
q = sys.stdin.read()
r = u.urlopen(u.Request("http://127.0.0.1:%s/graphql" % sys.argv[1], data=json.dumps({"query": q}).encode(),
    headers={"Content-Type": "application/json"}), timeout=20)
print(json.dumps(json.load(r)))' "$2"; }
graphql() { container_graphql "$WEBSERVER" 11002 <<<"$1"; }
old_graphql() { container_graphql "$OLD_WEBSERVER" 14107 <<<"$1"; }
SCHEDULES_Q='{ repositoryOrError(repositorySelector:{repositoryLocationName:"'"$LOCATION"'", repositoryName:"__repository__"}) { __typename ... on Repository { schedules { name scheduleState { status } } } } }'
running_schedules() {  # stdin: GraphQL 응답 → RUNNING schedule 이름(정렬)
  python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]["repositoryOrError"]
if d["__typename"] != "Repository": sys.exit("repository not loaded: %s" % d["__typename"])
print("\n".join(sorted(s["name"] for s in d["schedules"] if s["scheduleState"]["status"] == "RUNNING")))'; }
installed_rev() { basename "$(readlink -f "$ROOT")" | sed -n 's/^ktdm-release-\([0-9a-f]\{40\}\)$/\1/p'; }
installed_plane() {  # 설치본 compose가 말하는 weather code-server의 plane — shared|own. 값은 찍지 않는다.
  compose config --format json 2>/dev/null | python3 -c '
import json, sys
s = json.load(sys.stdin)["services"]["'"$CODE"'"]
print("shared" if "KOR_TRAVEL_DAGSTER_SHARED_PG_URL" in (s.get("environment") or {}) else "own")'; }
same_image() {  # same_image <service> — 도는 컨테이너의 이미지 id = 설치본 compose가 가리키는 이미지 이름의 id
  local name want have
  name="$(compose config --format json 2>/dev/null | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"][sys.argv[1]]["image"])' "$1")" || return 1
  want="$(docker image inspect -f '{{.Id}}' "$name" 2>/dev/null)" || return 1
  have="$(docker inspect -f '{{.Image}}' "$(cid "$1")")" || return 1
  [[ -n "$want" && "$want" == "$have" ]]
}
installed_workspace_has_weather() { grep -q "location_name: $LOCATION" "$ROOT/config/dagster-shared/workspace.yaml"; }

# plane 컨테이너 **안에 붙은** workspace가 아직 설치 전 것인가(적대 리뷰 H1). 두 컨테이너 모두: weather가 없고, 내용
# sha16이 그 컨테이너가 만들어질 때의 digest env와 같고, 설치 전 빈 workspace의 digest와 같다. 그리고
# `dagster_shared`에 tick·run이 하나도 없다(weather가 아직 한 번도 거기서 돌지 않았다).
plane_workspace_clean() {
  local quiet="${1:-}" s c got env
  for s in "$DAEMON" "$WEBSERVER"; do
    c="$(cid "$s")"; [[ -n "$c" ]] || { [[ -n "$quiet" ]] || echo "no container for $s" >&2; return 1; }
    got="$(docker exec "$c" python -I -c '
import hashlib, os, sys
b = open(os.path.join(os.environ["DAGSTER_HOME"], "workspace.yaml"), "rb").read()
print(hashlib.sha256(b).hexdigest()[:16], "weather" if sys.argv[1].encode() in b else "clean")' "$LOCATION" 2>/dev/null)" || return 1
    env="$(env_value "$s" KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST)"
    if [[ "$got" != "$PRE_FLIP_WORKSPACE_DIGEST clean" || "$env" != "$PRE_FLIP_WORKSPACE_DIGEST" ]]; then
      [[ -n "$quiet" ]] || echo "$s mounts workspace [$got] with digest env [$env]; expected [$PRE_FLIP_WORKSPACE_DIGEST clean]" >&2
      return 1
    fi
  done
  # weather 범위로 센다(재리뷰 MED-5): weather location tag를 단 run은 0이어야 하고, tick은 location을 싣지 않으므로
  # 시작 때 잰 기준선(전체 tick 수)에서 늘지 않아야 한다 — 다른 테넌트가 없는 지금은 weather의 tick이 곧 증가분이다.
  local weather_runs ticks
  weather_runs="$(sql "$NEW_DB" "$WEATHER_RUNS_SQL")" || return 1
  ticks="$(sql "$NEW_DB" "SELECT count(*) FROM job_ticks")" || return 1
  if [[ "$weather_runs" != 0 || "$ticks" != "$SHARED_TICKS_BASELINE" ]]; then
    [[ -n "$quiet" ]] || echo "$NEW_DB has $weather_runs weather runs and $ticks ticks (baseline $SHARED_TICKS_BASELINE)" >&2
    return 1
  fi
}
WEATHER_RUNS_SQL="SELECT count(*) FROM runs r JOIN run_tags t ON t.run_id = r.run_id WHERE t.key = 'dagster/code_location' AND t.value = '$LOCATION'"
WEATHER_LIVE_RUNS_SQL="$WEATHER_RUNS_SQL AND r.status IN ('QUEUED','NOT_STARTED','STARTING','STARTED','CANCELING')"
SHARED_TICKS_BASELINE="unset"

# ── lock G ────────────────────────────────────────────────────────────────
PHASE="lock"
[[ ! -L "$LOCK_DIR" ]] || fail "lock directory is a symlink"
install -d -o root -g root -m 0700 "$LOCK_DIR"
exec 9>>"$LOCK"
[[ "$(stat -c '%u:%a:%h' "$LOCK")" == "0:600:1" ]] || chmod 0600 "$LOCK"
[[ "$(stat -c '%u:%a:%h' "$LOCK")" == "0:600:1" ]] || fail "global mutation lock is unsafe"
flock -n 9 || fail "another manager mutation holds lock G"
say "holding lock G; state dir $STATE"

# ═════════════════════════════════════════════════════════════════════════
if [[ "$MODE" == rollback ]]; then
  # (f) 되돌리기 — 순서가 계약이다: 공용 plane에서 weather를 **먼저** 내리고, 그 다음 옛 서비스를 띄운다.
  PHASE="f-precheck"
  rev="$(installed_rev)"; [[ -n "$rev" && "$rev" == "$EXPECT"* ]] || fail "installed release is ${rev:-unknown}, expected $EXPECT"
  [[ "$(installed_plane)" == own ]] || fail "the installed release still has weather on the shared plane — install the rollback release first"
  ! installed_workspace_has_weather || fail "the installed shared workspace still lists $LOCATION"

  # 공용 plane의 weather run을 끝내는 원문 — 되돌리기와(plane이 고장 나 건너뛰었다면) 나중의 수동 실행이 같은 것을 쓴다.
  cat > "$STATE/cancel-shared-weather-runs.py" <<'PY'
import sys, time
from sqlalchemy import text
from dagster import DagsterInstance
from dagster._core.storage.dagster_run import DagsterRunStatus, RunsFilter
location, database = sys.argv[1], sys.argv[2]
live = [DagsterRunStatus.QUEUED, DagsterRunStatus.NOT_STARTED, DagsterRunStatus.STARTING,
        DagsterRunStatus.STARTED, DagsterRunStatus.CANCELING]
with DagsterInstance.get() as instance:
    with instance.run_storage.connect() as conn:
        current = conn.execute(text("SELECT current_database()")).scalar()
    assert current == database, "refusing: this instance is %s, not %s" % (current, database)
    runs = instance.get_runs(filters=RunsFilter(statuses=live, tags={"dagster/code_location": location}))
    for run in runs:
        if run.status in (DagsterRunStatus.STARTED, DagsterRunStatus.STARTING):
            try:
                instance.run_launcher.terminate(run.run_id)
            except Exception as exc:  # noqa: BLE001 - 종료 요청은 best effort, 아래 report가 상태를 닫는다
                print("terminate %s: %s" % (run.run_id[:8], type(exc).__name__))
    time.sleep(5)
    for run in instance.get_runs(filters=RunsFilter(statuses=live, tags={"dagster/code_location": location})):
        instance.report_run_canceled(run, message="weather rolled back to its own Dagster instance")
    print("canceled %d weather runs in %s" % (len(runs), current))
PY

  # 최종 점검 MED: `if running`은 조건 안이라 cid 실패(둘 이상 일치)·docker 오류가 "안 돈다"로 읽혀 plane을 건너뛰고
  # 옛 daemon을 켤 수 있었다(이중 발화). 컨테이너는 최상위에서 풀어 ERR trap을 받게 하고, 상태는 명시적으로 가른다.
  daemon_cid="$(cid "$DAEMON")"
  if [[ -n "$daemon_cid" ]]; then
    daemon_state="$(docker inspect -f '{{.State.Running}}' "$daemon_cid")"
  else
    daemon_state=absent
  fi
  case "$daemon_state" in
    true|false|absent) ;;
    *) fail "cannot tell whether the shared daemon is running (state '$daemon_state')" ;;
  esac
  if [[ "$daemon_state" == true ]]; then
    # f-plane: 공용 plane에서 weather를 **먼저** 내린다 — 공용 daemon이 더는 weather를 tick·dequeue하지 않는다.
    PHASE="f-plane"
    say "recreating the shared daemon and webserver on a workspace without weather"
    compose up -d --no-deps "$DAEMON" "$WEBSERVER" >/dev/null
    wait_health "$WEBSERVER" 300
    wait_health "$DAEMON" 300
    locations="$(graphql '{ workspaceOrError { __typename ... on Workspace { locationEntries { name } } } }')" \
      || fail "cannot ask the shared webserver for its workspace"
    grep -q '"Workspace"' <<<"$locations" || fail "the shared webserver did not return a workspace"
    ! grep -q "\"$LOCATION\"" <<<"$locations" || fail "the shared webserver still lists $LOCATION"
    say "weather is out of the shared workspace"

    # f-cancel: 그 다음 공용 plane에 남은 weather run을 끝낸다(적대 리뷰 M2) — plane 컨테이너 안에서,
    # `dagster/code_location`으로 걸러. 공용 daemon이 weather를 더 보지 않으므로 새 weather run이 생기지 않는다.
    # STARTED는 launcher로 종료를 요청한다(run의 origin, 아직 공용 instance를 보는 code-server로 간다).
    PHASE="f-cancel"
    docker exec -i "$(cid "$DAEMON")" python -I - "$LOCATION" "$NEW_DB" < "$STATE/cancel-shared-weather-runs.py" | sed 's/^/  /'
    live="$(sql "$NEW_DB" "$WEATHER_LIVE_RUNS_SQL")"
    [[ "$live" == 0 ]] || fail "$live weather runs are still live in $NEW_DB"
    say "0 live weather runs in $NEW_DB"
  else
    # 재리뷰 MED-4: 공용 daemon이 돌지 않으면 이중 발화할 것이 없다 — plane 단계를 건너뛰고 옛 서비스로 간다.
    PHASE="f-plane"
    say "the shared daemon is not running — SKIPPING the plane recreate and the shared weather-run cancel"
    say "  later, once the plane is repaired and BEFORE it starts again with weather: it will read the installed"
    say "  workspace (no weather). Then cancel leftover weather runs with:"
    say "  docker exec -i <kor-travel-dagster-daemon> python -I - $LOCATION $NEW_DB < $STATE/cancel-shared-weather-runs.py"
  fi
  PHASE="f-old"
  compose up -d --no-deps "$CODE" >/dev/null
  wait_health "$CODE" 420
  env_has "$CODE" KOR_TRAVEL_DAGSTER_SHARED_PG_URL && fail "weather code-server still has the shared metadata URL"
  compose up -d --no-deps "$OLD_WEBSERVER" >/dev/null
  wait_health "$OLD_WEBSERVER" 300
  compose up -d --no-deps "$OLD_DAEMON" "$OLD_GATEWAY" "$WEB" >/dev/null
  wait_health "$OLD_DAEMON" 300
  running "$OLD_GATEWAY" || fail "old gateway is not running"
  running "$WEB" || fail "weather-web is not running"
  n="$(old_graphql "$SCHEDULES_Q" | running_schedules | count_lines)"
  [[ "$n" == "$DECLARED_RUNNING" ]] || fail "old weather instance has $n RUNNING schedules, expected $DECLARED_RUNNING"
  say "old weather instance: $n RUNNING schedules"
  PHASE="done"
  say "rollback complete. Catch-up: the old daemon may launch up to max_catchup_runs (Dagster default 5) missed runs per"
  say "  schedule for the slots since the fence — watch the old instance's queue. Owner: restore the weather-dagster edge upstream."
  exit 0
fi

# ═════════════════════════════════════════════════════════════════════════
# (a) prechecks
PHASE="a-precheck"
rev="$(installed_rev)"; [[ -n "$rev" && "$rev" == "$EXPECT"* ]] || fail "installed release is ${rev:-unknown}, expected $EXPECT"
say "installed release $rev"
[[ "$(installed_plane)" == shared ]] || fail "the installed compose does not put weather on the shared plane"
installed_workspace_has_weather || fail "the installed shared workspace does not list $LOCATION"
for s in "$DAEMON" "$WEBSERVER" "$GATEWAY"; do [[ "$(health "$s")" == healthy ]] || fail "$s is $(health "$s")"; done
SHARED_TICKS_BASELINE="$(sql "$NEW_DB" "SELECT count(*) FROM job_ticks")"
echo "$SHARED_TICKS_BASELINE" > "$STATE/shared_ticks_baseline"
plane_workspace_clean || fail "the running plane already loaded the new workspace or ran weather — double-fire risk (was it restarted since install?)"
say "the running plane still mounts the pre-flip empty workspace; $NEW_DB has 0 weather runs (tick baseline $SHARED_TICKS_BASELINE)"
{ running "$OLD_DAEMON" && running "$OLD_WEBSERVER"; } || fail "the old weather daemon/webserver are not both running (already fenced?)"
[[ "$(health "$CODE")" == healthy ]] || fail "weather code-server is $(health "$CODE")"
env_has "$CODE" KOR_TRAVEL_DAGSTER_SHARED_PG_URL && fail "the running weather code-server already has the shared metadata URL — it is not on the old instance"
docker exec "$(cid "$WEB")" grep -q WeatherDagsterOverview /app/.next/server/app/api/dagster/graphql/route.js \
  || fail "the deployed weather-web proxy is not the scoped one (weather PR #65) — it would forward raw GraphQL to the shared webserver"
say "weather-web runs the scoped Dagster proxy"
old_graphql "$SCHEDULES_Q" | running_schedules > "$STATE/old-running.txt" || fail "cannot read the old RUNNING schedules"
n_old="$(count_lines "$STATE/old-running.txt")"
[[ "$n_old" == "$DECLARED_RUNNING" ]] || fail "old RUNNING schedule count is $n_old, declared $DECLARED_RUNNING"
say "old instance: $DECLARED_RUNNING RUNNING schedules"
# 재생성이 코드를 바꾸지 않는다 — 도는 weather code-server·web의 이미지가 compose가 가리키는 tag의 이미지와 같다.
for s in "$CODE" "$WEB"; do same_image "$s" || fail "$s runs an image other than its compose tag — recreating would change its code"; done
say "weather code-server and web run their compose tags' images"
for s in "$CODE" "$WEB" "$DAEMON" "$WEBSERVER"; do c="$(cid "$s")"; echo "$s $c"; done > "$STATE/ids-before.txt"

# (b) drain report — weather의 외부 run은 늘 떠 있다. 소유자가 in-flight 손실을 받아들였다 — 펜스 뒤 옛 DB에서 끝낸다.
PHASE="b-drain"
sql "$OLD_DB" "SELECT status, count(*) FROM runs WHERE status IN ('QUEUED','NOT_STARTED','STARTING','STARTED','CANCELING') GROUP BY 1 ORDER BY 1" \
  | tee "$STATE/drain-before.txt" | sed 's/^/  in flight: /'

# (c) fence — 바로 앞에서 plane이 아직 설치 전 workspace인지 다시 본다. 옛 daemon 먼저, 그 다음 webserver·gateway.
PHASE="c-fence"
plane_workspace_clean || fail "the plane changed since the precheck — double-fire risk; not fencing"
FENCED=1
for s in "$OLD_DAEMON" "$OLD_WEBSERVER" "$OLD_GATEWAY"; do
  c="$(cid "$s")"; [[ -n "$c" ]] || fail "no container for $s"
  docker stop -t 60 "$c" >/dev/null
  running "$s" && fail "$s is still running"
  say "stopped $s"
done
FENCE_TS="$(sql "$OLD_DB" "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')")"
echo "$FENCE_TS" > "$STATE/fence_ts"
say "fence_ts=$FENCE_TS (UTC)"
# 옛 DB의 in-flight run을 Dagster 자신의 API로 끝낸다 — code-server는 아직 옛 instance를 본다(재생성 전).
# 그것이 옛 DB인지 **연결한 database 이름으로** 확인한 뒤에만 건드린다(적대 리뷰 H2). STARTED는 먼저 launcher로
# 종료를 요청한다(code-server가 run worker의 부모라 닿는다).
docker exec -i "$(cid "$CODE")" python -I - "$OLD_DB" <<'PY' | tee "$STATE/drain-canceled.txt" | sed 's/^/  /'
import sys, time
from sqlalchemy import text
from dagster import DagsterInstance
from dagster._core.storage.dagster_run import DagsterRunStatus, RunsFilter
database = sys.argv[1]
live = [DagsterRunStatus.QUEUED, DagsterRunStatus.NOT_STARTED, DagsterRunStatus.STARTING,
        DagsterRunStatus.STARTED, DagsterRunStatus.CANCELING]
with DagsterInstance.get() as instance:
    with instance.run_storage.connect() as conn:
        current = conn.execute(text("SELECT current_database()")).scalar()
    assert current == database, "refusing: this instance is %s, not %s" % (current, database)
    runs = instance.get_runs(filters=RunsFilter(statuses=live))
    for run in runs:
        if run.status in (DagsterRunStatus.STARTED, DagsterRunStatus.STARTING):
            try:
                instance.run_launcher.terminate(run.run_id)
            except Exception as exc:  # noqa: BLE001 - 종료 요청은 best effort, 아래 report가 상태를 닫는다
                print("terminate %s: %s" % (run.run_id[:8], type(exc).__name__))
    time.sleep(5)
    for run in instance.get_runs(filters=RunsFilter(statuses=live)):
        instance.report_run_canceled(
            run, message="weather moved to the shared Dagster plane (ADR-54); in-flight run abandoned by owner decision")
    print("canceled %d runs in %s" % (len(runs), current))
PY
left="$(sql "$OLD_DB" "SELECT count(*) FROM runs WHERE status IN ('QUEUED','NOT_STARTED','STARTING','STARTED','CANCELING')")"
[[ "$left" == 0 ]] || fail "$left runs still in flight in $OLD_DB"

# (d) switch — digest가 재생성을 건다. 재생성됐는지 id로 확인한다. fence와 switch 사이의 cron 슬롯은 건너뛴다(공용
# daemon은 weather schedule을 처음 보므로 과거 슬롯을 따라잡지 않는다 — runbook에 적었다).
PHASE="d-switch"
SWITCH_TS="$(sql "$NEW_DB" "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')")"
echo "$SWITCH_TS" > "$STATE/switch_ts"
compose up -d --no-deps "$CODE" "$WEB" >/dev/null
wait_health "$CODE" 420
running "$WEB" || fail "weather-web is not running"
compose up -d --no-deps "$DAEMON" "$WEBSERVER" >/dev/null
for s in "$CODE" "$WEB" "$DAEMON" "$WEBSERVER"; do
  before="$(awk -v s="$s" '$1==s{print $2}' "$STATE/ids-before.txt")"; now="$(cid "$s")"
  [[ -n "$now" && "$now" != "$before"* && "$before" != "$now"* ]] || fail "$s was not recreated (container id unchanged)"
  say "$s recreated"
done
[[ "$(env_value "$WEB" DAGSTER_UI_INTERNAL_URL)" == "http://127.0.0.1:11002" ]] || fail "weather-web does not point at the shared webserver"
env_has "$CODE" KOR_TRAVEL_DAGSTER_SHARED_PG_URL || fail "weather code-server did not get the shared metadata URL"

# (e) verify
PHASE="e-verify"
wait_health "$WEBSERVER" 420   # probe: workspace location 전부 RepositoryLocation + 버전 상한 + 붙인 파일 digest
wait_health "$DAEMON" 420      # probe: code-server SERVING + 붙인 파일 digest + liveness-check
graphql '{ workspaceOrError { __typename ... on Workspace { locationEntries { name locationOrLoadError { __typename } } } } }' \
  | python3 -c '
import json, sys
w = json.load(sys.stdin)["data"]["workspaceOrError"]
got = {e["name"]: (e["locationOrLoadError"] or {}).get("__typename") for e in w["locationEntries"]}
sys.exit(0 if got.get("'"$LOCATION"'") == "RepositoryLocation" else "weather location not loaded: %s" % got)' \
  || fail "the shared webserver did not load $LOCATION"
say "$LOCATION is RepositoryLocation on the shared webserver"
graphql "$SCHEDULES_Q" | running_schedules > "$STATE/new-running.txt" || fail "cannot read the shared RUNNING schedules"
diff -u "$STATE/old-running.txt" "$STATE/new-running.txt" >&2 || fail "instigator parity: RUNNING schedules differ between the old instance and $NEW_DB"
say "instigator parity: $(count_lines "$STATE/new-running.txt") RUNNING schedules, same names as before the fence"

# 옛 weather daemon·webserver·gateway 컨테이너를 지운다(적대 리뷰 M1) — 멈춘 채 두면 대시보드의 start 한 번이 옛
# daemon을 되살려 이중 발화한다. 볼륨은 지우지 않는다. 되돌리기는 legacy release의 compose가 다시 만든다.
PHASE="e-retire"
for s in "$OLD_DAEMON" "$OLD_WEBSERVER" "$OLD_GATEWAY"; do
  c="$(cid "$s")"
  if [[ -n "$c" ]]; then
    running "$s" && fail "$s is running again — stop the old daemon now: active double fire"
    docker rm "$c" >/dev/null; say "removed the stopped $s container"
  fi
done

# 여기서부터는 읽기만 한다 — lock G를 놓는다(적대 리뷰 M4).
flock -u 9; exec 9>&-
PHASE="e-watch"
say "released lock G; waiting for the first weather tick in $NEW_DB (up to $FIRST_TICK_TIMEOUT_S s), sampling G3-a every $SAMPLE_EVERY_S s"
deadline=$(( $(date +%s) + FIRST_TICK_TIMEOUT_S )); max_conn=0
echo "utc,shared_role_connections,old_ticks_after_fence,old_runs_after_fence,new_ticks_after_switch" > "$STATE/g3a.csv"
while :; do
  conn="$(sql "$NEW_DB" "SELECT count(*) FROM pg_stat_activity WHERE usename = '$SHARED_ROLE'")"
  old_ticks="$(sql "$OLD_DB" "SELECT count(*) FROM job_ticks WHERE create_timestamp > '$FENCE_TS'")"
  old_runs="$(sql "$OLD_DB" "SELECT count(*) FROM runs WHERE create_timestamp > '$FENCE_TS'")"
  new_ticks="$(sql "$NEW_DB" "SELECT count(*) FROM job_ticks WHERE create_timestamp > '$SWITCH_TS'")"
  echo "$(date -u +%H:%M:%S),$conn,$old_ticks,$old_runs,$new_ticks" >> "$STATE/g3a.csv"
  (( conn > max_conn )) && max_conn=$conn
  [[ "$old_ticks" == 0 && "$old_runs" == 0 ]] || fail "double fire: $old_ticks ticks / $old_runs runs written to $OLD_DB after fence_ts"
  if (( new_ticks > 0 )); then say "first tick landed in $NEW_DB ($new_ticks so far)"; break; fi
  (( $(date +%s) < deadline )) || fail "no tick in $NEW_DB within $FIRST_TICK_TIMEOUT_S s"
  sleep "$SAMPLE_EVERY_S"
done
sql "$NEW_DB" "SELECT status, count(*) FROM job_ticks WHERE create_timestamp > '$SWITCH_TS' GROUP BY 1 ORDER BY 1" | sed 's/^/  tick status: /'
say "G3-a: peak $max_conn connections for $SHARED_ROLE so far (CONNECTION LIMIT 30) — samples in $STATE/g3a.csv"
say "old instance: 0 ticks and 0 runs written after fence_ts"

PHASE="done"
say "forward cutover verified. Next (runbook step 4, by hand): first run reaches SUCCESS and its events are in $NEW_DB;"
say "  the switch SQL on dagster/code_location is 0; weather-web shows only weather; keep sampling G3-a under load."
say "  PREREQUISITE (owner): the edge redirect weather-dagster.digitie.mywire.org -> https://dagster.digitie.mywire.org —"
say "  the deployed weather-web browser bundle still links the old host (NEXT_PUBLIC_DAGSTER_URL is baked at build time)."
