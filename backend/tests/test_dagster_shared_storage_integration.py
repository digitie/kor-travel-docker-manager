"""공용 Dagster storage 준비(db-init + storage migrate)를 **실제 이미지로** 격리 실행한다.

단위 테스트(`test_dagster_shared_config.py`)는 선언끼리의 일치만 본다. 선언을 읽어서는 알 수
없는 것이 있다:

- db-init이 고정된 공용 PostgreSQL 이미지 위에서 role·DB를 만들고 CONNECT를 소유 role에게만
  남기는가, 다시 돌려도 멱등인가.
- `should_autocreate_tables: false`인 instance 설정으로 빈 DB에 schema가 **생기는가** —
  `dagster instance migrate`만으로는 빈 DB에 table이 생기지 않는다. 그리고 table 소유자가
  공용 admin이 아니라 app role인가.
- Dagster가 공용 `dagster.yaml`을 받아들이는가(QueuedRunCoordinator, telemetry off). 거부되는
  설정은 stage 3의 webserver·daemon이 뜨지 못하게 만든다.
- location별 run 상한이 **실제로 무는가.** 파싱된 상한을 파일과 대조하는 것은 항진명제다 —
  `.dagster/repository`로 건 상한도 그대로 파싱되지만 queue daemon은 그 tag를 run 본문에서 보지
  못해 아무것도 막지 않았다(적대 리뷰 H1). 그래서 Dagster 자신의 `create_run`으로 run을 만들고
  queue daemon 자신의 dequeue 판정을 돌린다.
- 새 role이 PUBLIC CONNECT가 남은 **다른** DB에서 무엇을 할 수 있고 없는가(적대 리뷰 M4 —
  n150의 `pinvi`가 그런 DB다), 그리고 readiness 검사의 SQL이 그 DB를 고르는가.
- 해시 잠금 설치(`--require-hashes --no-deps`)로 이미지가 실제로 만들어지는가.

그래서 정본 compose의 세 서비스(공용 instance·db-init·migrate)를 그 실행 형태 그대로 가져오고,
호스트에 닿는 것(PGDATA bind·host network·컨테이너 이름·host 포트)만 뺀 격리 프로젝트로 돌린다.
공용 instance의 형태는 `test_shared_postgres_runtime_integration.py`의 fixture를 그대로 쓴다.
호스트 이미지는 정본 build context에서 **격리 태그로** 만들고 끝에 지운다 — 운영 태그(정본
compose의 내용 해시 tag)를 만들지 않는다. 기대값은 전부 정본 파일에서 읽는다. gate(`KTDM_REQUIRE_DOCKER_INTEGRATION`)는
`test_compose_readiness_integration.py`의 것을 그대로 쓴다(0은 skip, 1은 Docker를 못 쓰면 실패).
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_compose_readiness_integration import _unavailable_docker_fixture
from test_shared_postgres_runtime_integration import (
    _ISOLATED_PORT,
    _fixture_service,
    _inspect,
    _psql,
    _remove_project_residue,
    _run,
    _wait_healthy,
)

from kor_travel_docker_manager.services.postgres_connect_posture import PUBLIC_CONNECT_SQL
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_SHARED_POSTGRES = "kor-travel-shared-postgres"
_DB_INIT = "kor-travel-shared-db-init-dagster"
_MIGRATE = "kor-travel-dagster-storage-migrate"
_PROJECT_PREFIX = "ktdm-dagstershared-"
#: 이미지 빌드(pip 해시 설치)와 한 번의 one-shot 실행 상한. n150 부하에서 넉넉히.
_BUILD_TIMEOUT = 1800
_RUN_TIMEOUT = 600

#: db-init·migrate에서 옮기는 실행 형태. 컨테이너 이름·network·build는 옮기지 않는다.
_CARRIED_KEYS = ("image", "command", "environment", "secrets", "depends_on", "restart", "volumes")

_INSTANCE_PROBE = """
import json
from dagster import DagsterInstance
from dagster._core.storage.sql import ALEMBIC_SCRIPTS_LOCATION
from alembic.config import Config
from alembic.script import ScriptDirectory

config = Config()
config.set_main_option("script_location", ALEMBIC_SCRIPTS_LOCATION)
with DagsterInstance.get() as instance:
    queue = instance.get_concurrency_config().run_queue_config
    print(json.dumps({
        "head": ScriptDirectory.from_config(config).get_current_head(),
        "coordinator": type(instance.run_coordinator).__name__,
        "max_user_code_failure_retries": queue.max_user_code_failure_retries,
        "telemetry_enabled": instance.telemetry_enabled,
        "storages": sorted(type(s).__name__ for s in (
            instance.run_storage, instance.event_log_storage, instance.schedule_storage)),
    }))
"""


#: queue daemon이 보는 그대로 run을 만들고 그 daemon의 dequeue 판정을 돌린다. run은 Dagster 자신의
#: `create_run`이 `remote_job_origin`(schedule·sensor·launchpad·backfill·재실행이 모두 주는 것)으로
#: 만든다 — tag를 손으로 달면 "어느 tag가 run 본문에 들어가는가"를 이 테스트가 정하게 된다. 판정은
#: `QueuedRunCoordinatorDaemon._get_runs_to_dequeue`(daemon이 매 iteration 부르는 것)다.
#: 인자 JSON의 `concurrency`가 있으면 그 절로 바꿔 읽는다(대조군).
_QUEUE_PROBE = """
import json, os, sys
from dagster import DagsterInstance
from dagster._core.instance.ref import InstanceRef
from dagster._core.remote_origin import (
    GrpcServerCodeLocationOrigin, RemoteJobOrigin, RemoteRepositoryOrigin)
from dagster._core.storage.dagster_run import DagsterRunStatus, RunsFilter
from dagster._daemon.run_coordinator.queued_run_coordinator_daemon import (
    QueuedRunCoordinatorDaemon)

request = json.loads(sys.argv[1])
overrides = {"concurrency": request["concurrency"]} if request.get("concurrency") else None
ref = InstanceRef.from_dir(os.environ["DAGSTER_HOME"], overrides=overrides)

def create(instance, location, status):
    origin = RemoteJobOrigin(
        repository_origin=RemoteRepositoryOrigin(
            code_location_origin=GrpcServerCodeLocationOrigin(
                host="127.0.0.1", port=4000, location_name=location),
            repository_name="__repository__"),
        job_name="queue_probe")
    return instance.create_run(
        job_name="queue_probe", run_id=None, run_config={}, status=status, tags={},
        root_run_id=None, parent_run_id=None, step_keys_to_execute=None,
        execution_plan_snapshot=None, job_snapshot=None, parent_job_snapshot=None,
        asset_selection=None, asset_check_selection=None, resolved_op_selection=None,
        op_selection=None, remote_job_origin=origin, job_code_origin=None, asset_graph=None)

with DagsterInstance.from_ref(ref) as instance:
    for run in instance.get_runs():
        instance.delete_run(run.run_id)
    for _ in range(request["in_progress"]):
        create(instance, request["busy"], DagsterRunStatus.STARTED)
    next_busy = create(instance, request["busy"], DagsterRunStatus.QUEUED)
    first_idle = create(instance, request["idle"], DagsterRunStatus.QUEUED)
    daemon = QueuedRunCoordinatorDaemon(interval_seconds=1)
    dequeued = {
        run.run_id for run in daemon._get_runs_to_dequeue(
            instance, instance.get_concurrency_config(), fixed_iteration_time=None)}
    started = instance.get_run_records(
        filters=RunsFilter(statuses=[DagsterRunStatus.STARTED]))
    print(json.dumps({
        "storage": type(instance.run_storage).__name__,
        "in_progress": len(started),
        "busy_dequeued": next_busy.run_id in dequeued,
        "idle_dequeued": first_idle.run_id in dequeued,
    }))
"""


@dataclass(frozen=True)
class _Plane:
    compose: tuple[str, ...]
    env: dict[str, str]
    postgres: str
    database: str
    role: str


def _canonical() -> dict[str, Any]:
    document = load_yaml_rejecting_duplicate_keys(_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _carried(service: dict[str, Any]) -> dict[str, Any]:
    carried = {key: service[key] for key in _CARRIED_KEYS if key in service}
    # 같은 netns에 붙어 127.0.0.1로 격리 instance에 닿는다(정본은 host network).
    carried["network_mode"] = f"service:{_SHARED_POSTGRES}"
    carried["volumes"] = [
        # 상대 source는 정본 저장소 기준이다 — 격리 compose 파일은 tmp에 있다.
        str(_REPO_ROOT / volume[2:]) if str(volume).startswith("./") else volume
        for volume in carried.get("volumes", [])
    ]
    if not carried["volumes"]:
        del carried["volumes"]
    return carried


@pytest.fixture
def isolated_plane(tmp_path: Path) -> Iterator[_Plane]:
    canonical = _canonical()
    services = canonical["services"]
    postgres = _fixture_service()
    # 이미지 entrypoint가 initdb 뒤 임시 서버를 `pg_ctl stop`으로 내린다 — 기본 대기 60초는 n150의
    # 디스크 대기 속에서 종료 checkpoint를 못 기다려 컨테이너가 exit 1로 끝난다(2026-09-29 실측,
    # load 12). 서버가 아니라 pg_ctl만 읽는 값이라 정본의 실행 형태는 그대로다.
    postgres["environment"]["PGCTLTIMEOUT"] = "300"
    try:
        available = (
            _run("docker", "compose", "version").returncode == 0
            and _run("docker", "image", "inspect", postgres["image"]).returncode == 0
            and _run("docker", "image", "inspect", services[_DB_INIT]["image"]).returncode == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        available = False
    if not available:
        _unavailable_docker_fixture("Docker Compose 또는 공용 PostgreSQL 이미지를 쓸 수 없음")

    project = f"{_PROJECT_PREFIX}{os.getpid()}-{tmp_path.name[-8:]}".lower()
    image = f"{project}:it"
    context = _REPO_ROOT / services[_MIGRATE]["build"]["context"]

    used_secrets = {
        entry["source"]
        for name in (_DB_INIT, _MIGRATE)
        for entry in services[name].get("secrets", [])
    }
    admin_password = postgres["environment"]["POSTGRES_PASSWORD"]
    env = {
        **os.environ,
        "KOR_TRAVEL_SHARED_DB_PORT": _ISOLATED_PORT,
    }
    for secret in used_secrets:
        provider = canonical["secrets"][secret]["environment"]
        # admin secret은 격리 instance의 admin 비밀번호와 같아야 db-init이 붙는다.
        env[provider] = (
            admin_password if secret == "kor-travel-shared-postgres-password" else secrets.token_hex(16)
        )

    document = {
        "services": {
            _SHARED_POSTGRES: postgres,
            _DB_INIT: _carried(services[_DB_INIT]),
            # 정본의 tag는 내용 해시(운영 이름)다 — 격리 tag로 바꿔 운영 이미지를 만들지 않는다.
            _MIGRATE: {**_carried(services[_MIGRATE]), "image": image},
        },
        "secrets": {secret: canonical["secrets"][secret] for secret in used_secrets},
    }
    compose_path = tmp_path / "compose.yml"
    compose_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    compose = ("docker", "compose", "--file", str(compose_path), "--project-name", project)
    environment = services[_DB_INIT]["environment"]
    built = False
    try:
        build = _run("docker", "build", "--tag", image, str(context), timeout=_BUILD_TIMEOUT)
        assert build.returncode == 0, build.stdout[-3000:] + build.stderr[-3000:]
        built = True
        up = _run(*compose, "up", "--detach", "--pull", "never", _SHARED_POSTGRES, env=env)
        assert up.returncode == 0, up.stderr
        ps = _run(*compose, "ps", "--all", "--quiet", _SHARED_POSTGRES, env=env)
        assert ps.returncode == 0 and ps.stdout.strip(), ps.stderr
        yield _Plane(
            compose=compose,
            env=env,
            postgres=ps.stdout.strip(),
            database=environment["KOR_TRAVEL_DAGSTER_SHARED_DB"],
            role=environment["KOR_TRAVEL_DAGSTER_SHARED_APP_USER"],
        )
    finally:
        down = _run(
            *compose, "down", "--volumes", "--remove-orphans", "--timeout", "30",
            env=env, timeout=400,
        )
        residue = _remove_project_residue(project)
        removed = _run("docker", "image", "rm", "--force", image) if built else None
        if down.returncode != 0 or residue or _remove_project_residue(project):
            pytest.fail(f"fixture cleanup 실패: down={down.returncode}, 잔여={residue}")
        if removed is not None and removed.returncode != 0:
            pytest.fail(f"격리 이미지 {image}를 지우지 못함: {removed.stderr}")


def _one_shot(plane: _Plane, service: str) -> subprocess.CompletedProcess[str]:
    return _run(
        *plane.compose, "run", "--rm", "--no-deps", service,
        env=plane.env, timeout=_RUN_TIMEOUT,
    )


def _sql(plane: _Plane, sql: str) -> list[str]:
    """bootstrap DB에서 admin으로 묻는다."""

    details = _inspect(plane.postgres)
    return [line for line in _psql(plane.postgres, details, sql).splitlines() if line]


def _sql_in(plane: _Plane, database: str, sql: str) -> list[str]:
    """`database`에 admin으로 붙어 묻는다 — superuser는 CONNECT 권한과 무관하게 붙는다."""

    details = _inspect(plane.postgres)
    result = _run(
        "docker", "exec", "--user", "postgres", plane.postgres,
        "psql", "--no-psqlrc", "--tuples-only", "--no-align", "--field-separator", "|",
        "--port", _ISOLATED_PORT,
        "--username", next(
            entry.partition("=")[2]
            for entry in details["Config"]["Env"]
            if entry.startswith("POSTGRES_USER=")
        ),
        "--dbname", database, "--command", sql,
    )
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def _storage_state(plane: _Plane) -> tuple[list[str], list[str], list[str]]:
    version = _sql_in(plane, plane.database, "SELECT version_num FROM alembic_version")
    tables = _sql_in(
        plane, plane.database,
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1",
    )
    owners = _sql_in(
        plane, plane.database,
        "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'",
    )
    return version, tables, owners


def test_db_init_and_migrate_prepare_the_shared_storage_idempotently(
    isolated_plane: _Plane,
) -> None:
    plane = isolated_plane
    _wait_healthy(plane.postgres)

    first_init = _one_shot(plane, _DB_INIT)
    assert first_init.returncode == 0, first_init.stdout[-2000:] + first_init.stderr[-2000:]
    first_migrate = _one_shot(plane, _MIGRATE)
    assert first_migrate.returncode == 0, (
        first_migrate.stdout[-3000:] + first_migrate.stderr[-3000:]
    )

    # role·DB·CONNECT — db-init의 효과.
    database, role = plane.database, plane.role
    assert _sql(
        plane,
        "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
        f"rolbypassrls, rolconnlimit FROM pg_roles WHERE rolname = '{role}'",
    ) == ["t|f|f|f|f|f|45"]
    assert _sql(
        plane,
        "SELECT count(*) FROM pg_auth_members "
        f"WHERE member = (SELECT oid FROM pg_roles WHERE rolname = '{role}')",
    ) == ["0"], "role이 다른 role의 소속이다"
    assert _sql(
        plane, f"SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '{database}'"
    ) == [role]
    assert _sql(
        plane,
        "SELECT count(*) FROM pg_database AS d, aclexplode(d.datacl) AS a "
        f"WHERE d.datname = '{database}' AND a.grantee = 0 AND a.privilege_type = 'CONNECT'",
    ) == ["0"], "PUBLIC이 dagster_shared에 CONNECT를 가진다"
    assert _sql(plane, f"SELECT has_database_privilege('{role}', '{database}', 'CONNECT')") == [
        "t"
    ]
    bootstrap = _inspect(plane.postgres)["Config"]["Env"]
    bootstrap_db = next(e.partition("=")[2] for e in bootstrap if e.startswith("POSTGRES_DB="))
    assert _sql(
        plane, f"SELECT has_database_privilege('{role}', '{bootstrap_db}', 'CONNECT')"
    ) == ["f"]

    # schema — migrate의 효과. 설치된 Dagster의 head와 같고, table은 전부 app role 소유다.
    probe = _run(
        *plane.compose, "run", "--rm", "--no-deps", "--entrypoint", "python", _MIGRATE,
        "-I", "-c", _INSTANCE_PROBE,
        env=plane.env, timeout=_RUN_TIMEOUT,
    )
    assert probe.returncode == 0, probe.stdout[-2000:] + probe.stderr[-2000:]
    instance = json.loads(probe.stdout.strip().splitlines()[-1])

    version, tables, owners = _storage_state(plane)
    assert version == [instance["head"]]
    assert {"runs", "event_logs", "jobs", "instigators", "daemon_heartbeats"} <= set(tables)
    assert owners == [role]

    # Dagster가 공용 설정을 받아들였고, 결과가 파일에 적은 것과 같다.
    config = load_yaml_rejecting_duplicate_keys(
        (_REPO_ROOT / "config" / "dagster-shared" / "dagster.yaml").read_text(encoding="utf-8")
    )
    assert instance["coordinator"] == "QueuedRunCoordinator"
    assert instance["storages"] == [
        "PostgresEventLogStorage", "PostgresRunStorage", "PostgresScheduleStorage"
    ]
    assert instance["max_user_code_failure_retries"] == (
        config["run_queue"]["max_user_code_failure_retries"]
    )
    assert instance["telemetry_enabled"] is False

    _assert_what_the_role_can_do_in_a_default_acl_database(plane)

    # 멱등 — 두 번째 실행은 성공하고 아무것도 바꾸지 않는다.
    for service in (_DB_INIT, _MIGRATE):
        again = _one_shot(plane, service)
        assert again.returncode == 0, service + again.stdout[-2000:] + again.stderr[-2000:]
    assert _storage_state(plane) == (version, tables, owners)


def test_db_init_refuses_a_password_that_would_break_the_url(isolated_plane: _Plane) -> None:
    """URL 예약 문자가 든 비밀번호는 role을 만들기 **전에** 거부한다."""

    plane = isolated_plane
    _wait_healthy(plane.postgres)
    canonical = _canonical()
    app_secret = next(
        entry["source"]
        for entry in canonical["services"][_DB_INIT]["secrets"]
        if entry["source"] != "kor-travel-shared-postgres-password"
    )
    provider = canonical["secrets"][app_secret]["environment"]
    broken = _with_env(plane, {provider: "has@reserved/chars"})
    refused = _one_shot(broken, _DB_INIT)
    assert refused.returncode != 0
    assert "URI-unreserved" in refused.stderr
    assert _sql(plane, f"SELECT count(*) FROM pg_roles WHERE rolname = '{plane.role}'") == ["0"]


def _with_env(plane: _Plane, overrides: dict[str, str]) -> _Plane:
    return _Plane(
        compose=plane.compose,
        env={**plane.env, **overrides},
        postgres=plane.postgres,
        database=plane.database,
        role=plane.role,
    )


_PROBE_DB = "acl_probe"


def _as_role(plane: _Plane, database: str, sql: str) -> subprocess.CompletedProcess[str]:
    """공용 Dagster role로 TCP(`127.0.0.1`) 접속 — 합류한 code-server가 붙는 길과 같다."""

    canonical = _canonical()
    app_secret = next(
        entry["source"]
        for entry in canonical["services"][_DB_INIT]["secrets"]
        if entry["source"] != "kor-travel-shared-postgres-password"
    )
    password = plane.env[canonical["secrets"][app_secret]["environment"]]
    return _run(
        "docker", "exec", "--env", f"PGPASSWORD={password}", plane.postgres,
        "psql", "--no-psqlrc", "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1",
        "--host", "127.0.0.1", "--port", _ISOLATED_PORT,
        "--username", plane.role, "--dbname", database, "--command", sql,
    )


def _assert_what_the_role_can_do_in_a_default_acl_database(plane: _Plane) -> None:
    """PUBLIC CONNECT가 남은 다른 DB(n150의 `pinvi`와 같은 기본 ACL)에서 새 role의 권한.

    **붙는다** — 그것이 노출이다(catalog를 읽고 TEMP table을 만든다). 그러나 그 DB의 table은
    PUBLIC에 GRANT되지 않았으면 못 읽고, `public` schema에 만들지도 못한다(PG15+ 기본). 그 DB를
    걷는 것은 소유 저장소의 일이다(PinVi). readiness의 PUBLIC CONNECT 검사가 이 DB를 고르고
    `dagster_shared`는 고르지 않는지도 같은 instance에서 본다.
    """

    _sql(plane, f"CREATE DATABASE {_PROBE_DB}")
    _sql_in(
        plane, _PROBE_DB,
        "CREATE TABLE tenant_rows (secret text); INSERT INTO tenant_rows VALUES ('x')",
    )

    connected = _as_role(plane, _PROBE_DB, "SELECT current_user")
    assert connected.returncode == 0, connected.stderr
    assert connected.stdout.strip() == plane.role
    temp = _as_role(plane, _PROBE_DB, "CREATE TEMP TABLE scratch (x int)")
    assert temp.returncode == 0, temp.stderr

    read = _as_role(plane, _PROBE_DB, "SELECT secret FROM tenant_rows")
    assert read.returncode != 0 and "permission denied for table tenant_rows" in read.stderr
    create = _as_role(plane, _PROBE_DB, "CREATE TABLE public.planted (x int)")
    assert create.returncode != 0 and "permission denied for schema public" in create.stderr

    # 걷힌 DB에는 붙지도 못한다 — bootstrap DB가 그렇다.
    bootstrap = _inspect(plane.postgres)["Config"]["Env"]
    bootstrap_db = next(e.partition("=")[2] for e in bootstrap if e.startswith("POSTGRES_DB="))
    refused = _as_role(plane, bootstrap_db, "SELECT 1")
    assert refused.returncode != 0
    assert f'permission denied for database "{bootstrap_db}"' in refused.stderr

    exposed = set(_sql(plane, PUBLIC_CONNECT_SQL))
    assert _PROBE_DB in exposed
    assert plane.database not in exposed and bootstrap_db not in exposed


def _location_caps(concurrency: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [
        entry for entry in concurrency["runs"]["tag_concurrency_limits"] if entry["key"] == key
    ]


def _queue_probe(plane: _Plane, request: dict[str, Any]) -> dict[str, Any]:
    probe = _run(
        *plane.compose, "run", "--rm", "--no-deps", "--entrypoint", "python", _MIGRATE,
        "-I", "-c", _QUEUE_PROBE, json.dumps(request),
        env=plane.env, timeout=_RUN_TIMEOUT,
    )
    assert probe.returncode == 0, probe.stdout[-2000:] + probe.stderr[-3000:]
    result: dict[str, Any] = json.loads(probe.stdout.strip().splitlines()[-1])
    return result


def test_the_location_cap_blocks_the_next_run_of_that_location_only(
    isolated_plane: _Plane,
) -> None:
    """한 location이 상한만큼 돌고 있으면 그 location의 다음 run은 queue에 남고 다른 location은 나간다.

    대조군: 같은 상한을 옛 `.dagster/repository` key로 걸면 **막지 않는다** — 그 tag는 run 본문에
    없다. 대조군이 빨갛지 않으면 이 테스트는 무엇을 막는지 보지 못하는 것이다.
    """

    plane = isolated_plane
    _wait_healthy(plane.postgres)
    for service in (_DB_INIT, _MIGRATE):
        prepared = _one_shot(plane, service)
        assert prepared.returncode == 0, (
            service + prepared.stdout[-2000:] + prepared.stderr[-2000:]
        )

    concurrency = load_yaml_rejecting_duplicate_keys(
        (_REPO_ROOT / "config" / "dagster-shared" / "dagster.yaml").read_text(encoding="utf-8")
    )["concurrency"]
    caps = _location_caps(concurrency, "dagster/code_location")
    assert len(caps) >= 2, caps
    busy, idle = caps[0], caps[1]
    # 전역 상한이 막는 것이 아니어야 한다 — 상한만큼 돌아도 전역에는 두 자리가 남는다.
    assert busy["limit"] + 2 <= concurrency["runs"]["max_concurrent_runs"]
    request = {"in_progress": busy["limit"], "busy": busy["value"], "idle": idle["value"]}

    shared = _queue_probe(plane, request)
    assert shared == {
        "storage": "PostgresRunStorage",
        "in_progress": busy["limit"],
        "busy_dequeued": False,
        "idle_dequeued": True,
    }

    control = copy.deepcopy(concurrency)
    for entry in _location_caps(control, "dagster/code_location"):
        entry["key"] = ".dagster/repository"
        entry["value"] = f"__repository__@{entry['value']}"
    uncounted = _queue_probe(plane, {**request, "concurrency": control})
    assert uncounted["busy_dequeued"] is True, "옛 key의 상한이 막았다 — 대조군이 무의미해졌다"
    assert uncounted["idle_dequeued"] is True
