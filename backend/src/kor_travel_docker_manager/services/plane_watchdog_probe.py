"""수집 감시의 측정(probe) — 판정하지 않는다.

- Dagster storage(공용 PostgreSQL의 `dagster_shared`)를 `docker exec … psql`로 **읽기 전용
  트랜잭션**(`BEGIN READ ONLY`, statement·lock timeout)에서 질의한다. 비밀번호·DSN을 읽지 않는다 —
  컨테이너 안 local socket과 `POSTGRES_USER`만 쓴다(백업 primitive와 같은 경로).
- 호스트의 디스크(statvfs), IO 압력(PSI), Dagster 컨테이너 프로세스의 열린 fd 수와 nofile 한도
  (`/proc`, Manager는 root로 돈다)를 읽는다.

질의는 모두 Dagster 인덱스를 탄다(2026-11-24 두 호스트 실측, `docs/plane-watchdog.md`):
instigator별 마지막 tick은 `idx_tick_selector_timestamp` 역방향 1행, 종료 run 증분은
`idx_run_range(status, update_timestamp)`, run의 location·schedule tag는 `idx_run_tags_run_idx`,
schedule tag 역조회는 `idx_run_tags(key, value)`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services import standalone_backup

Runner = Callable[..., subprocess.CompletedProcess]

_APPLICATION_NAME = "ktdm-plane-watchdog"
_DISCOVERY_TTL_SECONDS = 600.0
_DOCKER_TIMEOUT_SECONDS = 20


class ProbeError(Exception):
    """측정 실패. 메시지는 화면·로그로 가므로 비밀을 담지 않는다."""


@dataclass(frozen=True)
class Instigator:
    selector: str
    type: str  # SCHEDULE | SENSOR
    status: str  # RUNNING | STOPPED | DECLARED_IN_CODE
    name: str
    location: str
    cron: str | None
    min_interval: float | None
    last_tick: float | None  # epoch 초(UTC)
    #: 최근 tick(최신 먼저, 최대 5개): (epoch 초, status, 오류 메시지 요약)
    recent_ticks: tuple[tuple[float, str, str], ...] = ()


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    job: str
    location: str
    schedule: str | None
    status: str
    created: float
    updated: float
    never_started: bool


@dataclass(frozen=True)
class TaggedRun:
    """종료 run 하나와 그 run에 붙은 태그 하나의 값(없으면 ``None``)."""

    record: RunRecord
    tag: str | None
    #: 재시도로 만든 run(`dagster/parent_run_id` 또는 `kortravelcommon/infra_retry_parent`)이면 부모 run id.
    #: 재시도 run은 부모의 비-`dagster/` 태그를 물려받으므로 그 태그가 자기 판정인지 알 수 없다.
    retry_of: str | None = None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def instigator_from_row(row: Mapping[str, Any]) -> Instigator:
    ticks: list[tuple[float, str, str]] = []
    for tick in row.get("recent_ticks") or []:
        at = _number(tick.get("at")) if isinstance(tick, Mapping) else None
        if at is not None:
            ticks.append((at, str(tick.get("status") or ""), str(tick.get("error") or "")))
    ticks.sort(key=lambda item: item[0], reverse=True)
    last_tick = _number(row.get("last_tick"))
    if last_tick is None and ticks:
        last_tick = ticks[0][0]
    return Instigator(
        selector=str(row.get("selector") or ""),
        type=str(row.get("type") or ""),
        status=str(row.get("status") or ""),
        name=str(row.get("name") or ""),
        location=str(row.get("location") or ""),
        cron=row.get("cron") if isinstance(row.get("cron"), str) else None,
        min_interval=_number(row.get("min_interval")),
        last_tick=last_tick,
        recent_ticks=tuple(ticks),
    )


def run_from_row(row: Mapping[str, Any]) -> RunRecord:
    return RunRecord(
        run_id=str(row["run_id"]),
        job=str(row.get("job") or ""),
        location=str(row.get("location") or ""),
        schedule=row.get("schedule") if isinstance(row.get("schedule"), str) else None,
        status=str(row.get("status") or ""),
        created=float(row["created"]),
        updated=float(row["updated"]),
        never_started=bool(row.get("never_started")),
    )


# ---------------------------------------------------------------------------
# storage 질의
# ---------------------------------------------------------------------------

_INSTIGATORS_SQL = """
SELECT coalesce(jsonb_agg(x), '[]'::jsonb) FROM (
  SELECT i.selector_id AS selector,
         i.instigator_type AS type,
         i.status,
         b.body -> 'origin' ->> 'job_name' AS name,
         b.body -> 'origin' -> 'external_repository_origin'
                -> 'repository_location_origin' ->> 'location_name' AS location,
         b.body -> 'job_specific_data' ->> 'cron_schedule' AS cron,
         b.body -> 'job_specific_data' ->> 'min_interval' AS min_interval,
         (SELECT coalesce(jsonb_agg(jsonb_build_object(
                    'at', extract(epoch FROM t."timestamp"),
                    'status', t.status,
                    'error', left(concat_ws(' | ',
                        t.tick_body::json -> 'error' ->> 'message',
                        t.tick_body::json -> 'error' -> 'cause' ->> 'message'), 400))
                  ORDER BY t."timestamp" DESC), '[]'::jsonb)
            FROM (SELECT j."timestamp", j.status, j.tick_body FROM job_ticks j
                  WHERE j.selector_id = i.selector_id
                  ORDER BY j."timestamp" DESC LIMIT 5) t) AS recent_ticks
  FROM instigators i
  CROSS JOIN LATERAL (SELECT i.instigator_body::json AS body) b
) x;
"""

# Dagster는 timestamp without time zone에 UTC를 쓴다. extract(epoch)은 그 명목값을 UTC로 센다.
_RUN_COLUMNS = """
  r.run_id, r.pipeline_name AS job, r.status,
  extract(epoch FROM r.create_timestamp) AS created,
  extract(epoch FROM r.update_timestamp) AS updated,
  (r.start_time IS NULL) AS never_started,
  (SELECT t.value FROM run_tags t WHERE t.run_id = r.run_id
     AND t.key = 'dagster/code_location' LIMIT 1) AS location,
  (SELECT t.value FROM run_tags t WHERE t.run_id = r.run_id
     AND t.key = 'dagster/schedule_name' LIMIT 1) AS schedule
"""

_RUNS_SINCE_SQL = f"""
SELECT coalesce(jsonb_agg(x), '[]'::jsonb) FROM (
  SELECT {_RUN_COLUMNS}
  FROM runs r
  WHERE r.status IN ('SUCCESS', 'FAILURE', 'CANCELED')
    AND r.update_timestamp >= (to_timestamp(:'since'::double precision) AT TIME ZONE 'UTC')
) x;
"""

# 긴 주기 schedule(창이 증분 feed보다 긴 것)만, 6시간에 한 번 읽는다. schedule tag의 가장 최근 run으로
# 그 schedule의 job을 찾고(`idx_run_tags(key, value)`), 그 job의 같은 location 최근 종료 run을 읽는다
# (`idx_runs_by_job(pipeline_name, id)` 역방향). 수동·요청 실행의 성공도 성공으로 센다 — schedule tag가
# 붙은 run만 보면 11-16 수동 성공이 있는 MCST job을 "3주기 성공 없음"으로 잘못 울린다(2026-11-24 실측).
_SCHEDULE_RUNS_SQL = f"""
SELECT coalesce(jsonb_agg(x), '[]'::jsonb) FROM (
  SELECT s.location AS want_location, s.schedule AS want_schedule, picked.*
  FROM json_to_recordset(:'schedules'::json) AS s(location text, schedule text)
  CROSS JOIN LATERAL (
    SELECT c.pipeline_name FROM (
      SELECT r0.pipeline_name,
             (SELECT l.value FROM run_tags l WHERE l.run_id = r0.run_id
                AND l.key = 'dagster/code_location' LIMIT 1) AS location
      FROM run_tags st JOIN runs r0 ON r0.run_id = st.run_id
      WHERE st.key = 'dagster/schedule_name' AND st.value = s.schedule
      ORDER BY r0.id DESC
      LIMIT 20
    ) c
    WHERE c.location = s.location
    LIMIT 1
  ) j
  CROSS JOIN LATERAL (
    SELECT c.* FROM (
      SELECT {_RUN_COLUMNS}
      FROM runs r
      WHERE r.pipeline_name = j.pipeline_name
        AND r.status IN ('SUCCESS', 'FAILURE', 'CANCELED')
      ORDER BY r.id DESC
      LIMIT 30
    ) c
    WHERE c.location = s.location
    LIMIT :limit
  ) picked
) x;
"""
# location 거르기를 EXISTS 대신 바깥에서 한다. odroid에서 EXISTS가 location tag 1.5만 행의 semi-join으로
# 풀려 22.6초가 걸렸다(2026-11-24 EXPLAIN, load 23). 위 모양은 job 인덱스 역방향 30행만 읽는다.

# job 하나의 최근 종료 run과 그 run의 태그 하나(매 평가). 위와 같은 모양 — job의 최근 종료 run 30행을 읽고
# location은 바깥에서 거른다. `since`(판정 창의 시작) 하한이 있어 job이 며칠 멈춰도 읽는 범위가 창 안의 종료 run으로
# 묶인다. 2026-11-24 실측(창 2시간): odroid `idx_run_range` 81행 → 12행 51 ms, n150(그 job이 10-14 이후 없음)
# `idx_run_range` 0행 0.7 ms. 하한이 없을 때는 odroid가 `runs_pkey` 역방향으로 job이 멈춘 뒤 쌓인 run을 전부 거슬렀다.
_TAGGED_JOB_RUNS_SQL = f"""
SELECT coalesce(jsonb_agg(x ORDER BY x.created DESC), '[]'::jsonb) FROM (
  SELECT c.* FROM (
    SELECT {_RUN_COLUMNS},
      (SELECT t.value FROM run_tags t WHERE t.run_id = r.run_id
         AND t.key = :'tag_key' LIMIT 1) AS tag_value,
      (SELECT t.value FROM run_tags t WHERE t.run_id = r.run_id
         AND t.key IN ('dagster/parent_run_id', 'kortravelcommon/infra_retry_parent') LIMIT 1) AS retry_of
    FROM runs r
    WHERE r.pipeline_name = :'job'
      AND r.status IN ('SUCCESS', 'FAILURE', 'CANCELED')
      AND r.update_timestamp >= (to_timestamp(:'since'::double precision) AT TIME ZONE 'UTC')
    ORDER BY r.id DESC
    LIMIT 30
  ) c
  WHERE c.location = :'location'
  LIMIT :limit
) x;
"""


# location 하나의 실행 중 run 수(자동 재시작 직전 1회). `idx_run_range(status, …)`의 status 앞부분 — 실행 중 run은 몇 개뿐이다.
_RUNNING_RUNS_SQL = """
SELECT jsonb_build_object('running', count(*)) FROM runs r
WHERE r.status IN ('STARTING', 'STARTED', 'CANCELING')
  AND EXISTS (SELECT 1 FROM run_tags t WHERE t.run_id = r.run_id
                AND t.key = 'dagster/code_location' AND t.value = :'location');
"""


@dataclass(frozen=True)
class StorageTarget:
    container: str
    database: str


def storage_target() -> StorageTarget:
    """백업 role 표(`dagster_shared`)를 정본으로 쓴다 — 컨테이너·DB 이름을 여기 다시 적지 않는다."""

    env_name, default_container, database = standalone_backup._ROLE_CONFIG["dagster_shared"]
    container = (os.environ.get(env_name, "").strip() if env_name else "") or default_container
    return StorageTarget(container=container, database=database)


class StorageClient:
    """storage 질의 하나 = `docker exec -i … psql` 한 번. 읽기 전용 트랜잭션 안에서만 돈다."""

    def __init__(self, *, statement_timeout_seconds: int, runner: Runner = subprocess.run) -> None:
        self._timeout = int(statement_timeout_seconds)
        self._runner = runner
        self._discovered: tuple[float, str, int] | None = None
        self._lock = threading.Lock()

    def _discover(self, container: str) -> tuple[str, int]:
        with self._lock:
            cached = self._discovered
            now = time.monotonic()
            if cached is not None and now - cached[0] < _DISCOVERY_TTL_SECONDS:
                return cached[1], cached[2]
            try:
                admin = standalone_backup._discover_admin_role(container)
                port = standalone_backup._discover_port(container)
            except standalone_backup.StandaloneBackupError as exc:
                raise ProbeError(f"공용 PostgreSQL 컨테이너를 확인할 수 없습니다: {exc}") from exc
            self._discovered = (now, admin, port)
            return admin, port

    def query_json(self, sql: str, variables: Mapping[str, str] | None = None) -> Any:
        target = storage_target()
        admin, port = self._discover(target.container)
        script = (
            "BEGIN READ ONLY;\n"
            f"SET LOCAL statement_timeout = '{self._timeout}s';\n"
            "SET LOCAL lock_timeout = '2s';\n"
            f"{sql.strip()}\n"
            "COMMIT;\n"
        )
        argv = [
            "docker", "exec", "-i", "--user", "postgres",
            "--env", f"PGAPPNAME={_APPLICATION_NAME}",
            target.container,
            "psql", "--username", admin, "--port", str(port), "--dbname", target.database,
            "--no-psqlrc", "--quiet", "--tuples-only", "--no-align",
            "--set", "ON_ERROR_STOP=1",
        ]
        for name, value in (variables or {}).items():
            if not re.fullmatch(r"[a-z_]+", name):
                raise ProbeError("invalid query variable name")
            argv.extend(["--set", f"{name}={value}"])
        try:
            completed = self._runner(
                argv, input=script, capture_output=True, text=True, check=False,
                timeout=self._timeout + 15, cwd="/",
            )
        except subprocess.TimeoutExpired as exc:
            raise ProbeError("storage 질의가 시간 안에 끝나지 않았습니다.") from exc
        except OSError as exc:
            raise ProbeError(f"docker를 실행할 수 없습니다: {exc.strerror or exc}") from exc
        if completed.returncode != 0:
            tail = (completed.stderr or "").strip().splitlines()[-1:] or ["(stderr 없음)"]
            raise ProbeError(f"storage 질의 실패(exit {completed.returncode}): {tail[0][:300]}")
        # `--quiet`라 BEGIN·SET·COMMIT 태그는 나오지 않는다. 결과는 jsonb 한 값(한 줄)이다.
        text = (completed.stdout or "").strip()
        if not text:
            raise ProbeError("storage 질의가 결과를 돌려주지 않았습니다.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProbeError("storage 질의 결과를 해석할 수 없습니다.") from exc

    def instigators(self) -> list[Instigator]:
        rows = self.query_json(_INSTIGATORS_SQL)
        return _rows(rows, instigator_from_row)

    def runs_since(self, since_epoch: float) -> list[RunRecord]:
        rows = self.query_json(_RUNS_SINCE_SQL, {"since": f"{since_epoch:.3f}"})
        return _rows(rows, run_from_row)

    def schedule_runs(
        self, schedules: Sequence[tuple[str, str]], *, limit: int
    ) -> dict[tuple[str, str], list[RunRecord]]:
        """(location, schedule)마다 그 schedule의 job이 같은 location에서 남긴 최근 종료 run."""

        result: dict[tuple[str, str], list[RunRecord]] = {key: [] for key in schedules}
        if not schedules:
            return result
        payload = json.dumps([{"location": loc, "schedule": name} for loc, name in schedules])
        rows = self.query_json(_SCHEDULE_RUNS_SQL, {"schedules": payload, "limit": str(int(limit))})
        for row in _rows(rows, lambda row: row):
            key = (str(row.get("want_location")), str(row.get("want_schedule")))
            record = _rows([row], run_from_row)[0]
            if key in result and record.location == key[0]:
                result[key].append(record)
        return result

    def running_runs(self, location: str) -> int:
        """그 location의 실행 중(STARTING·STARTED·CANCELING) run 수."""

        result = self.query_json(_RUNNING_RUNS_SQL, {"location": location})
        if not isinstance(result, dict) or not isinstance(result.get("running"), int):
            raise ProbeError("실행 중 run 질의 결과의 모양이 예상과 다릅니다.")
        return int(result["running"])

    def tagged_job_runs(
        self, location: str, job: str, tag_key: str, *, since: float, limit: int
    ) -> list[TaggedRun]:
        """(location, job)의 ``since`` 뒤 갱신된 최근 종료 run(최신 먼저)과 각 run의 ``tag_key`` 태그 값."""

        rows = self.query_json(_TAGGED_JOB_RUNS_SQL, {
            "location": location, "job": job, "tag_key": tag_key, "since": f"{since:.3f}",
            "limit": str(int(limit))})

        def convert(row: Mapping[str, Any]) -> TaggedRun:
            tag = row.get("tag_value")
            parent = row.get("retry_of")
            return TaggedRun(record=run_from_row(row), tag=tag if isinstance(tag, str) and tag else None,
                             retry_of=parent if isinstance(parent, str) and parent else None)

        return [item for item in _rows(rows, convert) if item.record.location == location]


def _rows(rows: Any, convert: Callable[[Mapping[str, Any]], Any]) -> list[Any]:
    """질의 결과 행을 바꾼다. 모양이 틀리면 KeyError가 아니라 측정 실패(ProbeError)다."""

    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ProbeError("storage 질의 결과가 목록이 아닙니다.")
    try:
        return [convert(row) for row in rows]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ProbeError(f"storage 질의 결과의 모양이 예상과 다릅니다: {type(exc).__name__}") from exc


# ---------------------------------------------------------------------------
# 호스트 측정
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiskObservation:
    path: str
    total_bytes: int
    used_bytes: int
    available_bytes: int

    @property
    def used_percent(self) -> float:
        # df의 Capacity와 같은 식: used / (used + available). root 예약분은 분모에서 빠진다.
        denominator = self.used_bytes + self.available_bytes
        return 0.0 if denominator <= 0 else 100.0 * self.used_bytes / denominator


def read_disk(path: str) -> DiskObservation:
    stat = os.statvfs(path)
    total = stat.f_blocks * stat.f_frsize
    used = (stat.f_blocks - stat.f_bfree) * stat.f_frsize
    available = stat.f_bavail * stat.f_frsize
    return DiskObservation(path=path, total_bytes=total, used_bytes=used, available_bytes=available)


def _docker_text(arguments: list[str], runner: Runner) -> str | None:
    try:
        completed = runner(
            ["docker", *arguments], capture_output=True, text=True, check=False,
            timeout=_DOCKER_TIMEOUT_SECONDS, cwd="/",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or ""


def default_disk_paths(runner: Runner = subprocess.run) -> list[str]:
    """`/`, Docker root, 공용 PostgreSQL 데이터 mount. 같은 장치는 하나로 합친다."""

    candidates = ["/"]
    root = (_docker_text(["info", "--format", "{{.DockerRootDir}}"], runner) or "").strip()
    if root:
        candidates.append(root)
    mounts = _docker_text(
        ["inspect", "--format", "{{range .Mounts}}{{.Source}}\t{{.Destination}}\n{{end}}",
         storage_target().container],
        runner,
    )
    for line in (mounts or "").splitlines():
        source, _, destination = line.partition("\t")
        if source and destination.startswith("/var/lib/postgresql"):
            candidates.append(source)
    return candidates


def read_disks(paths: Sequence[str]) -> tuple[list[DiskObservation], list[str]]:
    observations: list[DiskObservation] = []
    errors: list[str] = []
    seen_devices: set[int] = set()
    for path in paths:
        try:
            device = os.stat(path).st_dev
            if device in seen_devices:
                continue
            observation = read_disk(path)
        except OSError as exc:
            errors.append(f"{path}: {exc.strerror or exc}")
            continue
        seen_devices.add(device)
        observations.append(observation)
    return observations, errors


def parse_pressure(text: str) -> dict[str, dict[str, float]]:
    """`/proc/pressure/*` 형식: `some avg10=1.00 avg60=… avg300=… total=…`."""

    result: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        kind, *pairs = line.split()
        values: dict[str, float] = {}
        for pair in pairs:
            key, _, raw = pair.partition("=")
            try:
                values[key] = float(raw)
            except ValueError:
                continue
        if kind in {"some", "full"}:
            result[kind] = values
    return result


def read_io_pressure(path: str = "/proc/pressure/io") -> dict[str, dict[str, float]] | None:
    """PSI가 꺼진 커널(파일 없음)이면 ``None``."""

    try:
        with open(path, encoding="ascii") as handle:
            return parse_pressure(handle.read())
    except FileNotFoundError:
        return None


@dataclass(frozen=True)
class ContainerFds:
    """Dagster 컨테이너 하나의 fd 관측. 판정은 **프로세스별 비율**의 최댓값으로 한다.

    n150 fd 고갈(2026-11 감사)은 code-server 안 `dagster api grpc` 프로세스 하나가 soft 한도 1024를 다
    쓴 사건이었다. 컨테이너 합계가 아니라 그 pid의 열린 fd / 그 pid의 soft 한도를 본다.
    """

    container: str
    open_fds: int  # 비율이 가장 큰 프로세스의 열린 fd 수
    nofile_soft_limit: int | None  # 그 프로세스의 soft 한도
    open_fds_total: int
    processes: int
    process_kind: str = "other"  # grpc | code-server | daemon | webserver | other
    pid: int | None = None
    locations: tuple[str, ...] = ()  # 명령줄의 `-m`·`--location-name` 값

    @property
    def ratio(self) -> float | None:
        if not self.nofile_soft_limit:
            return None
        return self.open_fds / self.nofile_soft_limit


def process_kind(args: str) -> str:
    if "dagster api grpc" in args:
        return "grpc"
    if "code-server start" in args:
        return "code-server"
    if "dagster-daemon" in args:
        return "daemon"
    if "dagster-webserver" in args or "dagster_webserver" in args:
        return "webserver"
    return "other"


def location_names(args: str) -> list[str]:
    """명령줄에서 location 이름(`-m <module>`, `--location-name <name>`)을 꺼낸다."""

    tokens = args.split()
    found = []
    for index, token in enumerate(tokens[:-1]):
        value = tokens[index + 1]
        if value.startswith("-"):
            continue
        # `python -m dagster api grpc`의 `-m dagster`는 location이 아니다 — dagster 하위 명령 뒤의 `-m`만 센다.
        if token == "--location-name" or (
                token == "-m" and any(word in {"code-server", "grpc"} for word in tokens[:index])):
            if value not in found:
                found.append(value)
    return found


def _soft_nofile(limits_text: str) -> int | None:
    for line in limits_text.splitlines():
        if line.startswith("Max open files"):
            fields = line[len("Max open files"):].split()
            if fields and fields[0].isdigit():
                return int(fields[0])
            return None
    return None


def dagster_containers(runner: Runner = subprocess.run) -> list[str]:
    output = _docker_text(["ps", "--filter", "name=dagster", "--format", "{{.Names}}"], runner)
    if output is None:
        raise ProbeError("docker ps를 실행할 수 없습니다.")
    return sorted(name.strip() for name in output.splitlines() if name.strip())


def container_processes(container: str, runner: Runner = subprocess.run) -> list[tuple[int, str]]:
    """`docker top`이 주는 **호스트** PID와 명령줄."""

    output = _docker_text(["top", container, "-eo", "pid,args"], runner)
    if output is None:
        return []
    processes: list[tuple[int, str]] = []
    for line in output.splitlines()[1:]:
        pid_text, _, args = line.strip().partition(" ")
        if pid_text.isdigit():
            processes.append((int(pid_text), args.strip()))
    return processes


def read_container_fds(
    container: str, *, runner: Runner = subprocess.run, proc_root: Path = Path("/proc")
) -> ContainerFds | None:
    """읽을 수 없으면(root가 아님 등) ``None``이다."""

    best: tuple[float, int, int | None, str, int] | None = None
    total = 0
    counted = 0
    locations: list[str] = []
    for pid, args in container_processes(container, runner):
        locations.extend(name for name in location_names(args) if name not in locations)
        try:
            count = len(os.listdir(proc_root / str(pid) / "fd"))
            soft = _soft_nofile((proc_root / str(pid) / "limits").read_text(encoding="ascii"))
        except OSError:
            continue
        counted += 1
        total += count
        # 한도를 모르는 프로세스는 비율 0으로 두되 fd 수로만 비교 대상이 된다.
        score = count / soft if soft else 0.0
        if best is None or (score, count) > (best[0], best[1]):
            best = (score, count, soft, process_kind(args), pid)
    if best is None:
        return None
    return ContainerFds(container=container, open_fds=best[1], nofile_soft_limit=best[2],
                        open_fds_total=total, processes=counted, process_kind=best[3], pid=best[4],
                        locations=tuple(locations))


def read_dagster_fds(
    *, runner: Runner = subprocess.run, proc_root: Path = Path("/proc")
) -> tuple[list[ContainerFds], list[str]]:
    observations: list[ContainerFds] = []
    unreadable: list[str] = []
    for container in dagster_containers(runner):
        observation = read_container_fds(container, runner=runner, proc_root=proc_root)
        if observation is None:
            unreadable.append(container)
        else:
            observations.append(observation)
    return observations, unreadable


__all__ = [
    "ContainerFds",
    "DiskObservation",
    "Instigator",
    "ProbeError",
    "RunRecord",
    "StorageClient",
    "TaggedRun",
    "container_processes",
    "dagster_containers",
    "default_disk_paths",
    "location_names",
    "process_kind",
    "instigator_from_row",
    "parse_pressure",
    "read_dagster_fds",
    "read_disks",
    "read_io_pressure",
    "run_from_row",
    "storage_target",
]
