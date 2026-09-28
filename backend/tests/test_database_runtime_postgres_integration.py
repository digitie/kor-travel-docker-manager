"""`database_runtime`의 tenant 울타리를 **진짜 PostgreSQL**에서 확인한다(M1 R2·R4, §1.6).

대역은 SQL 문자열을 볼 뿐이라 "그 SQL이 실제로 무엇을 지우고 누구를 막는가"를 모른다. 여기서는
공용 instance와 같은 이미지(digest 고정)를 host port 없이(`--network none`) 띄우고, 운영과
똑같이 `docker exec --user postgres … --port`의 socket 경로로 진짜 함수를 부른다. 모양은 S1이다 —
Map bootstrap 소유자가 instance admin(`it_admin`)이다.

gate `KTDM_REQUIRE_DOCKER_INTEGRATION`: 0이면 Docker가 없을 때 skip, 1이면 실패. GitHub CI는
gate를 켜지 않는다. n150에서 1로 도는 것이 머지 gate다.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import time
import uuid
from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import database_runtime
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.database_runtime import (
    DatabaseRuntime,
    ensure_map_application_database,
    ensure_map_databases_isolated,
    initialize_application_300_dagster_metadata_database,
    reset_databases_for_application_300,
)

#: 공용 instance(`kor-travel-shared-postgres`)가 도는 바로 그 이미지다(2026-09-28 n150 실측).
_SHARED_IMAGE = (
    "postgis/postgis@sha256:8b33190b6486ab9905dea999171817c1ac461733a7078dd4c836091c6e6b5d40"
)
_REQUIRED_GATE_ENV = "KTDM_REQUIRE_DOCKER_INTEGRATION"
_ADMIN = "it_admin"
_PORT = 15432
_SCHEMA_OWNER = "ktm_feature_schema_owner"
_LOGIN = "it_map_login"
_METADATA = "kor_travel_map_dagster"
#: non-superuser 슬롯 = 10 − 3 − 0 = 7 → 상한 floor(0.4 × 7) = 2. T-CAP이 그 상한을 채울 수 있게
#: 순수 helper의 **입력**을 instance 설정으로 정한다(helper를 바꿔치지 않는다).
_MAX_CONNECTIONS = 10
_SUPERUSER_RESERVED = 3
_TEST_DATABASES = ("kor_travel_map", "kor_travel_map_dagster", "pinvi", "foreign_dagster")
_TEST_ROLES = (_METADATA, "pinvi_app", _LOGIN)


def _docker(*arguments: str, timeout: int = 120, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments],
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        **kwargs,  # type: ignore[arg-type]
    )


def _required_docker_gate() -> bool:
    value = os.environ.get(_REQUIRED_GATE_ENV, "0").strip()
    if value not in {"0", "1"}:
        pytest.fail(f"{_REQUIRED_GATE_ENV}는 0 또는 1이어야 함")
    return value == "1"


def _unavailable(reason: str) -> None:
    if _required_docker_gate():
        pytest.fail(reason)
    pytest.skip(f"{reason}; 필수 gate는 {_REQUIRED_GATE_ENV}=1로 실행")


def _require_shared_image() -> None:
    try:
        info = _docker("info")
    except (OSError, subprocess.TimeoutExpired):
        _unavailable("Docker를 사용할 수 없음")
    if info.returncode != 0:
        _unavailable("Docker를 사용할 수 없음")
    if _docker("image", "inspect", _SHARED_IMAGE).returncode == 0:
        return
    if not _required_docker_gate():
        _unavailable(f"pull 없는 로컬 {_SHARED_IMAGE}를 사용할 수 없음")
    pull = _docker("pull", _SHARED_IMAGE, timeout=600)
    if pull.returncode != 0:
        pytest.fail(f"공용 PostgreSQL 이미지 pull 실패: {pull.stderr.strip()}")


def _psql(
    container: str,
    sql: str,
    *,
    user: str = _ADMIN,
    database: str = "postgres",
) -> subprocess.CompletedProcess[str]:
    """socket으로 한 번 접속해 ``sql``을 stdin으로 돌린다(운영 admin 경로와 같은 모양)."""

    return _docker(
        "exec",
        "--interactive",
        "--user",
        "postgres",
        container,
        "psql",
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
        "--set",
        "ON_ERROR_STOP=1",
        "--username",
        user,
        "--port",
        str(_PORT),
        "--dbname",
        database,
        input=sql,
    )


def _admin(container: str, sql: str, *, database: str = "postgres") -> str:
    completed = _psql(container, sql, database=database)
    # 출력(비밀 해시가 있을 수 있다)은 오류에 싣지 않는다 — stderr만.
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _database_oids(container: str) -> dict[str, int]:
    rows = _admin(container, "SELECT datname, oid FROM pg_catalog.pg_database ORDER BY 1")
    return {name: int(oid) for name, oid in (line.split("|") for line in rows.splitlines())}


def _datacl(container: str, database: str) -> str:
    return _admin(
        container,
        "SELECT COALESCE(datacl::text, 'NULL') || ' ' || datconnlimit "
        f"FROM pg_catalog.pg_database WHERE datname = '{database}'",
    )


def _password_fingerprint(container: str, role: str) -> str:
    """`pg_authid.rolpassword`의 sha256. 값은 프로세스 안에서만 다루고 출력하지 않는다."""

    verifier = _admin(
        container, f"SELECT rolpassword FROM pg_catalog.pg_authid WHERE rolname = '{role}'"
    )
    assert verifier, "role has no password"
    return hashlib.sha256(verifier.encode("utf-8")).hexdigest()


def _connect(container: str, user: str, database: str) -> subprocess.CompletedProcess[str]:
    return _psql(container, "SELECT 1", user=user, database=database)


@pytest.fixture(scope="module")
def cluster() -> Iterator[str]:
    _require_shared_image()
    name = f"ktdm-it-dbrt-{uuid.uuid4().hex[:12]}"
    started = _docker(
        "run",
        "--detach",
        "--network",
        "none",
        "--name",
        name,
        # 익명 volume을 남기지 않고 디스크 대기가 큰 호스트에서 I/O를 피한다.
        "--tmpfs",
        "/var/lib/postgresql/data",
        "--env",
        f"POSTGRES_USER={_ADMIN}",
        # 값은 argv가 아니라 이 프로세스 env에서 docker가 읽는다.
        "--env",
        "POSTGRES_PASSWORD",
        _SHARED_IMAGE,
        "postgres",
        "-p",
        str(_PORT),
        "-c",
        f"max_connections={_MAX_CONNECTIONS}",
        "-c",
        f"superuser_reserved_connections={_SUPERUSER_RESERVED}",
        env={**os.environ, "POSTGRES_PASSWORD": secrets.token_urlsafe(32)},
    )
    try:
        assert started.returncode == 0, started.stderr
        deadline = time.monotonic() + 300
        while True:
            # 초기화 중 임시 서버는 기본 포트에서만 듣는다 — 이 포트가 답하면 최종 서버다.
            ready = _docker(
                "exec", "--user", "postgres", name,
                "pg_isready", "--port", str(_PORT), "--username", _ADMIN,
            )
            if ready.returncode == 0:
                break
            if time.monotonic() > deadline:
                pytest.fail("PostgreSQL fixture가 준비되지 않음: " + ready.stdout.strip())
            time.sleep(1)
        _admin(
            name,
            f"CREATE ROLE foreign_app LOGIN NOINHERIT PASSWORD '{secrets.token_urlsafe(24)}';\n"
            f"CREATE ROLE {_SCHEMA_OWNER} NOLOGIN;\n"
            "CREATE DATABASE foreign_db OWNER foreign_app;\n"
            "REVOKE CONNECT ON DATABASE foreign_db FROM PUBLIC;\n",
        )
        yield name
    finally:
        _docker("rm", "--force", "--volumes", name)
        residue = _docker("ps", "--all", "--quiet", "--filter", f"name=^/{name}$")
        assert residue.returncode == 0 and residue.stdout.strip() == "", "fixture residue"


@pytest.fixture(autouse=True)
def _clean_slate(cluster: str) -> Iterator[None]:
    """테스트마다 이 파일이 만드는 DB·role만 지운다. seed(`foreign_*`)와 유지보수 DB는 그대로다."""

    def reset() -> None:
        _admin(
            cluster,
            "".join(f"DROP DATABASE IF EXISTS {name} WITH (FORCE);\n" for name in _TEST_DATABASES)
            + "".join(f"DROP ROLE IF EXISTS {name};\n" for name in _TEST_ROLES),
        )

    reset()
    yield
    reset()


def _runtimes(
    container: str,
    *,
    app: str = "kor_travel_map",
    dagster: str = "kor_travel_map_dagster",
    metadata: str = _METADATA,
    pinvi: str = "pinvi",
) -> tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime]:
    def runtime(
        role: database_runtime.DatabaseRole,
        name: str,
        *,
        owner: str = _ADMIN,
        additional: frozenset[str] = frozenset(),
    ) -> DatabaseRuntime:
        return DatabaseRuntime(
            role=role,
            container_name=container,
            port=_PORT,
            database_name=name,
            owner_name=owner,
            admin_name=_ADMIN,
            additional_owner_names=additional,
        )

    return (
        runtime("map_application", app),
        runtime("map_dagster", dagster, additional=frozenset({metadata})),
        runtime("pinvi", pinvi, owner="pinvi_app"),
    )


def _seed_map_pair(container: str) -> None:
    """bootstrap 뒤의 Map 모양: app DB는 schema owner, login은 그 role을 INHERIT FALSE로 든다."""

    _admin(
        container,
        f"CREATE ROLE {_LOGIN} LOGIN NOINHERIT;\n"
        f"GRANT {_SCHEMA_OWNER} TO {_LOGIN} WITH INHERIT FALSE;\n"
        f"CREATE ROLE {_METADATA} LOGIN NOINHERIT;\n"
        f"CREATE DATABASE kor_travel_map OWNER {_SCHEMA_OWNER};\n"
        f"CREATE DATABASE kor_travel_map_dagster OWNER {_METADATA};\n",
    )


# --- R2: owner fences -----------------------------------------------------------------------


def test_admin_owned_map_named_db_is_not_dropped(cluster: str) -> None:
    """T-R2a: 반쯤 만든(admin 소유) Map 이름의 DB는 `--restart`도 지우지 않는다."""

    _admin(cluster, f"CREATE DATABASE kor_travel_map OWNER {_ADMIN};\nCREATE ROLE pinvi_app LOGIN;\n")
    before = _database_oids(cluster)

    with pytest.raises(DeploymentContractError, match="map_application database owner differs"):
        reset_databases_for_application_300(_runtimes(cluster))

    assert _database_oids(cluster) == before


def test_foreign_owned_db_is_not_dropped_even_under_the_map_name(cluster: str) -> None:
    """T-R2b: 다른 tenant의 DB 이름이 Map 이름으로 박혀도 소유자가 막는다."""

    before = _database_oids(cluster)
    acl = _datacl(cluster, "foreign_db")

    with pytest.raises(DeploymentContractError, match="map_application database owner differs"):
        reset_databases_for_application_300(_runtimes(cluster, app="foreign_db"))

    assert _database_oids(cluster) == before
    assert _datacl(cluster, "foreign_db") == acl


def test_happy_path_drops_only_runtime_dbs(cluster: str) -> None:
    """T-R2c: 대조군 — 정상 소유자의 Map DB는 **실제로** 지워진다(탐지기가 빨갛게 될 수 있다)."""

    _seed_map_pair(cluster)
    _admin(cluster, "CREATE ROLE pinvi_app LOGIN;\nCREATE DATABASE pinvi OWNER pinvi_app;\n")
    before = _database_oids(cluster)

    reset_databases_for_application_300(_runtimes(cluster))

    after = _database_oids(cluster)
    assert "kor_travel_map" not in after
    assert "kor_travel_map_dagster" not in after
    assert after["pinvi"] != before["pinvi"]
    for untouched in ("foreign_db", "postgres", "template0", "template1", "template_postgis"):
        assert after[untouched] == before[untouched], untouched


def test_foreign_login_named_as_metadata_user_is_neither_rotated_nor_dropped(cluster: str) -> None:
    """T-R2d: 속성 검사를 다 통과하는 다른 tenant의 login(`LOGIN NOINHERIT`, 멤버십 없음)이다."""

    _admin(cluster, "CREATE DATABASE foreign_dagster OWNER foreign_app;\nCREATE ROLE pinvi_app LOGIN;\n")
    fingerprint = _password_fingerprint(cluster, "foreign_app")
    before = _database_oids(cluster)

    # (1) Dagster DB가 없고 metadata user가 foreign login → password를 돌리지 않는다.
    _, dagster, _ = _runtimes(cluster, metadata="foreign_app")
    with pytest.raises(DeploymentContractError, match="role is unsafe"):
        initialize_application_300_dagster_metadata_database(
            dagster,
            metadata_user="foreign_app",
            metadata_password=secrets.token_urlsafe(32),
        )
    assert _password_fingerprint(cluster, "foreign_app") == fingerprint
    assert _database_oids(cluster) == before

    # (2) Dagster DB 이름까지 그 tenant의 DB → 아무것도 지우지 않는다.
    with pytest.raises(DeploymentContractError, match="outside the Map pair"):
        reset_databases_for_application_300(
            _runtimes(cluster, dagster="foreign_dagster", metadata="foreign_app")
        )
    assert _database_oids(cluster) == before
    assert _password_fingerprint(cluster, "foreign_app") == fingerprint

    # 대조군: 아무것도 소유하지 않는 남은 metadata role은 **돌린다**.
    _admin(
        cluster,
        f"CREATE ROLE {_METADATA} LOGIN NOINHERIT PASSWORD '{secrets.token_urlsafe(24)}';\n",
    )
    leftover = _password_fingerprint(cluster, _METADATA)
    _, dagster, _ = _runtimes(cluster)
    identity = initialize_application_300_dagster_metadata_database(
        dagster,
        metadata_user=_METADATA,
        metadata_password=secrets.token_urlsafe(32),
    )
    assert identity.owner == _METADATA
    assert _password_fingerprint(cluster, _METADATA) != leftover


# --- name fence --------------------------------------------------------------------------------


def test_ensure_refuses_reserved_names(cluster: str) -> None:
    """T-NAME: 공용 instance의 `postgres`는 admin 소유에 `alembic_version`이 없다."""

    app, _, _ = _runtimes(cluster, app="postgres")
    bootstrap = Mock()

    with pytest.raises(DeploymentContractError, match="reserved cluster database"):
        database_runtime.require_map_application_database_convergible(app)
    with pytest.raises(DeploymentContractError, match="reserved cluster database"):
        ensure_map_application_database(app, run_role_bootstrap=bootstrap)

    bootstrap.assert_not_called()


# --- R4: closed to PUBLIC, CONNECT for the login, derived cap ----------------------------------


def test_map_databases_end_closed_to_public(cluster: str) -> None:
    """T-R4: 다른 tenant login은 막히고, DSN login과 Dagster 소유자만 붙는다."""

    _seed_map_pair(cluster)
    foreign_acl = _datacl(cluster, "foreign_db")
    # 격리 전에는 PUBLIC CONNECT로 붙는다 — 아래 거부가 이 함수 때문임을 보인다.
    assert _connect(cluster, "foreign_app", "kor_travel_map").returncode == 0
    app, dagster, _ = _runtimes(cluster)

    ensure_map_databases_isolated(app, dagster, login=_LOGIN)

    for user, database in (
        ("foreign_app", "kor_travel_map"),
        ("foreign_app", "kor_travel_map_dagster"),
        (_LOGIN, "kor_travel_map_dagster"),
        (_METADATA, "kor_travel_map"),
    ):
        denied = _connect(cluster, user, database)
        assert denied.returncode != 0, (user, database)
        assert "permission denied for database" in denied.stderr, (user, database)
    assert _connect(cluster, _LOGIN, "kor_travel_map").returncode == 0
    assert _connect(cluster, _METADATA, "kor_travel_map_dagster").returncode == 0
    assert _datacl(cluster, "foreign_db") == foreign_acl


def test_isolation_is_idempotent(cluster: str) -> None:
    """T-R4i: 두 번째 호출은 아무것도 바꾸지 않는다."""

    _seed_map_pair(cluster)
    app, dagster, _ = _runtimes(cluster)
    ensure_map_databases_isolated(app, dagster, login=_LOGIN)
    first = (_datacl(cluster, "kor_travel_map"), _datacl(cluster, "kor_travel_map_dagster"))

    ensure_map_databases_isolated(app, dagster, login=_LOGIN)

    assert (_datacl(cluster, "kor_travel_map"), _datacl(cluster, "kor_travel_map_dagster")) == first
    assert first[0].endswith(" 2")


def test_isolation_readback_catches_connect_through_membership(cluster: str) -> None:
    """ACL 술어는 membership으로 얻는 CONNECT를 못 본다 — read-back이 login 집합으로 잡는다."""

    _seed_map_pair(cluster)
    # PG16은 inherit 옵션의 기본을 member의 `rolinherit`에서 가져온다(foreign_app은 NOINHERIT).
    _admin(cluster, f"GRANT {_LOGIN} TO foreign_app WITH INHERIT TRUE;\n")
    app, dagster, _ = _runtimes(cluster)

    try:
        with pytest.raises(DeploymentContractError, match="not isolated after the grant"):
            ensure_map_databases_isolated(app, dagster, login=_LOGIN)
    finally:
        _admin(cluster, f"REVOKE {_LOGIN} FROM foreign_app;\n")


def test_cap_applies_to_the_login_not_the_admin(cluster: str) -> None:
    """T-CAP: 상한 2를 login 연결 둘이 채우면 셋째는 거부되고, admin(superuser)은 붙는다."""

    _seed_map_pair(cluster)
    app, dagster, _ = _runtimes(cluster)
    usable = database_runtime._read_usable_connection_slots(app)
    assert usable == _MAX_CONNECTIONS - _SUPERUSER_RESERVED
    assert database_runtime.map_application_connection_cap(usable) == 2
    ensure_map_databases_isolated(app, dagster, login=_LOGIN)

    holders = [
        subprocess.Popen(
            [
                "docker", "exec", "--user", "postgres", cluster,
                "psql", "--no-psqlrc", "--username", _LOGIN, "--port", str(_PORT),
                "--dbname", "kor_travel_map", "--command", "SELECT pg_sleep(120)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(2)
    ]
    try:
        deadline = time.monotonic() + 60
        while (
            _admin(
                cluster,
                "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                f"WHERE datname = 'kor_travel_map' AND usename = '{_LOGIN}'",
            )
            != "2"
        ):
            assert time.monotonic() < deadline, "login sessions did not start"
            time.sleep(0.5)

        third = _connect(cluster, _LOGIN, "kor_travel_map")
        assert third.returncode != 0
        assert "too many connections for database" in third.stderr
        assert _admin(cluster, "SELECT 1", database="kor_travel_map") == "1"
    finally:
        _admin(
            cluster,
            "SELECT count(pg_catalog.pg_terminate_backend(pid)) FROM pg_catalog.pg_stat_activity "
            f"WHERE usename = '{_LOGIN}'",
        )
        for holder in holders:
            try:
                holder.wait(timeout=60)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait(timeout=30)
