"""F1D pinned runtime의 두 PostgreSQL 데이터베이스 재생성 경계.

동결된 Compose 계약에서 Map application, PinVi의 정확한 대상만 유도한다. 이 모듈은
백업·복원·진단 상태를 알지 못한다. v5 rebuild는 서비스가 모두 멈춘 뒤 이 경계로 두 DB를
파기하고, 각 이미지의 bootstrap/migration으로 데이터를 다시 만든다.

옛 Map Dagster metadata DB(`map_dagster`)는 이 경계에서 뺐다(platform-topology.md §7 4단계). Map
Dagster는 공용 plane(`dagster_shared`)에서 돌고 그 storage는 `kor-travel-dagster-storage-migrate`가
올린다. 옛 DB는 막힌 뒤 DROP된다 — 재구축이 그것을 만들거나, 읽거나, migrate하면 막힌 DB 위에서
Map·PinVi를 멈춘 채 실패한다(2026-10-03 22:51Z 사고).

두 DB가 어느 PostgreSQL instance에 사는지는 이름이 아니라 **DSN 포트**에서 유도한다
(ADR-53): 그 포트를 `-p`로 듣는 PostgreSQL 서버 서비스가 정확히 하나여야 한다.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import unquote, urlsplit

from kor_travel_docker_manager.services.c6c_deployment import (
    _PINVI_API_SERVICE,
    _PINVI_DATABASE_URL_ENV,
    MAP_PRINCIPAL_PREFIX,
    POSTGRES_IDENTIFIER,
    DeploymentContractError,
    loopback_dsn_authority,
    postgres_admin_secret,
    postgres_server_admin_name,
    postgres_server_services_on_port,
)
from kor_travel_docker_manager.services.errors import command_output_tail

DatabaseRole = Literal["map_application", "pinvi"]
MapApplicationEnsureOutcome = Literal["created", "bootstrapped", "present"]

#: role·database 이름의 모양 — C6c의 것 하나다(instance admin 이름을 두 모듈이 같게 읽는다).
_DATABASE_IDENTIFIER = POSTGRES_IDENTIFIER
_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_SCHEMA_REVISION = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
#: role마다 (DB 이름 env, 기본값, 소유자 env·기본값). 소유자가 ``None``이면 **그 instance의
#: admin**이다(ADR-53 S1) — Map fresh bootstrap은 instance의 기존 admin으로 돌고, bootstrap이
#: 끝나면 앱 DB를 schema owner에게 넘긴다. 전용 Map superuser(`KOR_TRAVEL_MAP_POSTGRES_USER`)는
#: 퇴역했다.
_ROLE_CONFIG: dict[DatabaseRole, tuple[str, str, tuple[str, str] | None]] = {
    "map_application": ("KOR_TRAVEL_MAP_POSTGRES_DB", "kor_travel_map", None),
    # ADR-46: PinVi의 application DB는 공용 제어 평면 instance에 있고, **소유자는
    # 그 project의 app role이다** — geo/concierge/weather와 같은 모양이다.
    #
    # 한때 소유자를 bootstrap owner(`shared_admin`)로 두었는데, 그것은 PinVi의 다중
    # role 모델(M05)이 "runtime role은 database owner일 수 없다"를 세 곳에서 강제했기
    # 때문이다. 그 모델을 폐기하면서 그 제약도 함께 사라졌다 — 이제 role 하나가 자기
    # database를 소유하고, 그래서 migration이 별도 권한 창 없이 DDL을 실행한다.
    "pinvi": ("PINVI_POSTGRES_DB", "pinvi", ("PINVI_APP_DB_USER", "pinvi_app")),
}

#: **절대 파기할 수 없는 database 이름.**
#:
#: 파괴 경로(`reset_databases_for_application_300`)는 `runtime.database_name`을 그대로 `dropdb --force`의
#: 인자로 넘긴다. 그 이름의 유일한 출처는 운영자 `.env`이고, 유일한 필터는
#: `_DATABASE_IDENTIFIER` 정규식이었다. 바로 앞의 owner preflight가 "현재 소유자가
#: 이 role의 허용 소유자 집합에 있을 것"을 요구하므로 형제 프로젝트의 운영 DB는
#: 이미 막힌다(geo·concierge·weather DB는 각자의 app role 소유다). 그래서 공용
#: instance에서 소유자가 겹치는 것은 **cluster 유지보수 DB뿐**이다 — 그것들은
#: bootstrap owner 소유라 preflight를 통과해 버린다. PinVi가 전용 instance에 있을
#: 때는 그 사고의 상한이 자기 cluster였지만, 이제 같은 실수가 네 프로젝트의 관리
#: 경로를 한 번에 없앤다. 이름을 프로젝트별 allowlist로 묶지 않는 이유는 그것이
#: 운영 DB 이름을 테스트까지 전파시켜 합성 이름을 못 쓰게 만들기 때문이다 —
#: 막아야 할 것은 예약어 쪽이다.
#:
#: **수렴(ensure) 경로에도 같은 울타리를 건다.** 공용 instance의 `postgres`는 instance
#: admin 소유이고 `alembic_version`이 없다 — `KOR_TRAVEL_MAP_POSTGRES_DB`가 잘못 박히면
#: ensure 경로가 그것을 "bootstrap 전에 죽은 Map DB"로 읽고 role bootstrap을 돌린다.
#: 지금 그것을 막는 것은 Map bootstrap 스크립트의 fresh 검사 하나뿐이었다.
_RESERVED_DATABASES: Final = frozenset({"postgres", "template0", "template1"})
_SCHEMA_REVISION_LOCATION: dict[DatabaseRole, tuple[str, str]] = {
    "map_application": ("public", "alembic_version"),
    "pinvi": ("app", "alembic_version"),
}
_MAP_SCHEMA_OWNER = "ktm_feature_schema_owner"


@dataclass(frozen=True)
class DatabaseRuntime:
    """동결된 Compose 계약에서 얻은 하나의 PostgreSQL database identity."""

    role: DatabaseRole
    #: DB가 사는 PostgreSQL 서버의 compose 서비스 — readiness를 이 이름으로 본다.
    service_name: str
    container_name: str
    port: int
    database_name: str
    owner_name: str
    admin_name: str


@dataclass(frozen=True)
class _PostgresInstance:
    """DSN 포트에서 유도한 PostgreSQL 서버 하나(서비스·컨테이너·admin·포트)."""

    service_name: str
    container_name: str
    admin_name: str
    port: int


def _dsn_authority(dsn: object, *, label: str) -> tuple[str, int]:
    """DSN의 (host, port). host는 `127.0.0.1`이어야 한다 — 모든 instance가 loopback만 듣는다.

    판정은 C6c의 `loopback_dsn_authority` 하나다.
    """

    authority = loopback_dsn_authority(dsn)
    if authority is None:
        raise DeploymentContractError(f"{label} database DSN authority is invalid")
    return authority


def _instance_for_dsn(
    resolved: Mapping[str, object],
    dsn: object,
    *,
    label: str,
) -> _PostgresInstance:
    """DSN이 가리키는 PostgreSQL instance를 frozen resolved 문서에서 유도한다(ADR-53).

    그 포트를 `-p`로 듣는 PostgreSQL 서버 서비스(C6c `postgres_server_services_on_port` — 서버
    판정과 명령 파서가 C6c의 것 하나다)가 **정확히 하나**여야 한다. 없으면 DSN이 이 compose 밖을
    가리키고, 둘이면 어느 cluster인지 말할 수 없다. 컨테이너 이름과 admin(`POSTGRES_USER`)은 그
    서비스에서 읽는다 — 이름·포트 리터럴이 없다.
    """

    _host, port = _dsn_authority(dsn, label=label)
    matches = postgres_server_services_on_port(resolved, port)
    if len(matches) != 1:
        raise DeploymentContractError(
            f"{label} database DSN port must be served by exactly one PostgreSQL "
            f"service (found {len(matches)})"
        )
    services = resolved.get("services")
    postgres = services.get(matches[0]) if isinstance(services, Mapping) else None
    container_name = postgres.get("container_name") if isinstance(postgres, Mapping) else None
    if not isinstance(container_name, str) or not _CONTAINER_NAME.fullmatch(container_name):
        raise DeploymentContractError(f"{label} PostgreSQL container identity is invalid")
    # admin은 C6c가 Map DSN 검사에서 읽는 것과 같은 helper로 읽는다 — 술어가 둘이면 C6c가 받은
    # 이름을 여기서 (멈춘 뒤에) 거부하게 된다.
    admin_name = postgres_server_admin_name(resolved, matches[0])
    if admin_name is None:
        raise DeploymentContractError(f"{label} PostgreSQL admin role is invalid")
    return _PostgresInstance(
        service_name=matches[0],
        container_name=container_name,
        admin_name=admin_name,
        port=port,
    )


def database_runtimes_from_frozen_contract(
    *,
    resolved: Mapping[str, object],
    environment: Mapping[str, str],
) -> tuple[DatabaseRuntime, DatabaseRuntime]:
    """동결된 resolved Compose와 env에서 canonical 두 DB(Map application, PinVi)를 유도한다.

    instance는 DSN에서 온다 — Map은 `KOR_TRAVEL_MAP_PG_DSN`, PinVi는 `pinvi-api`의 resolved
    `PINVI_DATABASE_URL`이다. Map 소유자는 그 instance의 admin이다(S1). 두 DB가 한 instance에
    있어도 된다 — 이름이 서로 다르기만 하면 된다. 옛 Map Dagster metadata URL은 읽지 않는다.
    """

    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("pinned runtime Compose services are invalid")

    map_dsn = environment.get("KOR_TRAVEL_MAP_PG_DSN", "")
    pinvi_api = services.get(_PINVI_API_SERVICE)
    pinvi_environment = pinvi_api.get("environment") if isinstance(pinvi_api, Mapping) else None
    pinvi_dsn = (
        pinvi_environment.get(_PINVI_DATABASE_URL_ENV)
        if isinstance(pinvi_environment, Mapping)
        else None
    )
    instances: dict[DatabaseRole, _PostgresInstance] = {
        "map_application": _instance_for_dsn(resolved, map_dsn, label="Map"),
        "pinvi": _instance_for_dsn(resolved, pinvi_dsn, label="PinVi"),
    }

    runtimes: list[DatabaseRuntime] = []
    for role, (database_env, database_default, owner_config) in _ROLE_CONFIG.items():
        instance = instances[role]
        database_name = environment.get(database_env, database_default)
        owner_name = (
            instance.admin_name
            if owner_config is None
            else environment.get(owner_config[0], owner_config[1])
        )
        if not _DATABASE_IDENTIFIER.fullmatch(database_name):
            raise DeploymentContractError(f"{role} database name is invalid")
        if not _DATABASE_IDENTIFIER.fullmatch(owner_name):
            raise DeploymentContractError(f"{role} database owner is invalid")
        runtimes.append(
            DatabaseRuntime(
                role=role,
                service_name=instance.service_name,
                container_name=instance.container_name,
                port=instance.port,
                database_name=database_name,
                owner_name=owner_name,
                admin_name=instance.admin_name,
            )
        )
    if len({runtime.database_name for runtime in runtimes}) != len(runtimes):
        raise DeploymentContractError(
            "pinned runtime databases must have distinct frozen database names"
        )
    return runtimes[0], runtimes[1]


def _require_tenant_database_name(runtime: DatabaseRuntime) -> None:
    """**파괴·수렴 직전의 이름 울타리.** cluster 유지보수 DB와 template은 tenant DB가 아니다.

    `_validate_runtime`이 아니라 바꾸는 경로에만 둔다 — 읽기 경로(schema revision·
    identity)는 이름에 무관해야 하고, 위험한 것은 drop·bootstrap·권한 변경이다.
    """

    if (
        runtime.database_name in _RESERVED_DATABASES
        or runtime.database_name.startswith("template")
    ):
        raise DeploymentContractError(
            "pinned runtime database name is a reserved cluster database"
        )


def _recreate_empty_database_after_owner_preflight(
    runtime: DatabaseRuntime,
    *,
    existing_owner: str | None,
) -> None:
    """사전 owner 검증이 끝난 하나의 DB를 파기·재생성한다."""

    _validate_runtime(runtime)
    _require_tenant_database_name(runtime)
    if existing_owner is not None:
        if existing_owner not in _permitted_existing_owners(runtime):
            raise DeploymentContractError(
                f"{runtime.role} database owner differs from the frozen contract"
            )
        _run_checked(
            [
                *_database_admin_command(runtime, "dropdb"),
                "--force",
                runtime.database_name,
            ],
            label=f"{runtime.role} database destructive drop",
        )
    create_command = [
        *_database_admin_command(runtime, "createdb"),
        "--owner",
        runtime.owner_name,
    ]
    # PinVi의 fresh role-catalog reset은 extension·user namespace가 전혀 없는
    # catalog만 허용한다. template1은 클러스터 관리자가 추가한 객체를 상속할 수 있으므로
    # PinVi target은 PostgreSQL 기본 template0에서만 다시 만든다.
    if runtime.role == "pinvi":
        create_command.extend(("--template", "template0"))
    create_command.append(runtime.database_name)
    _run_checked(
        create_command,
        label=f"{runtime.role} database destructive create",
    )


def reset_databases_for_application_300(
    runtimes: tuple[DatabaseRuntime, DatabaseRuntime],
) -> None:
    """Map application DB는 제거하고 PinVi DB만 즉시 다시 만든다.

    application-300은 application DB를 ``template0``에서 별도 생성한다. 따라서 generic
    drop/create가 Map DB를 미리 만들면 virgin-root 계약을 우회한다. 옛 Map Dagster metadata
    DB는 이 리셋의 대상이 아니다(모듈 docstring) — 지우지도 다시 만들지도 않는다.
    """

    existing_owners = _read_resettable_owners(runtimes)
    map_runtime, pinvi_runtime = runtimes
    if existing_owners[0] is not None:
        _run_checked(
            [
                *_database_admin_command(map_runtime, "dropdb"),
                "--force",
                map_runtime.database_name,
            ],
            label=f"{map_runtime.role} database destructive drop",
        )
    _recreate_empty_database_after_owner_preflight(
        pinvi_runtime,
        existing_owner=existing_owners[1],
    )


def require_databases_resettable(
    runtimes: tuple[DatabaseRuntime, DatabaseRuntime],
) -> None:
    """``reset_databases_for_application_300``이 거부할 상태를 **읽기만으로** 먼저 거부한다(R2).

    전체 배포 경로는 리셋 전에 Map·PinVi 런타임을 멈춘다. 이름·소유자·배타성 거부가 그 뒤에야
    나면 pair가 내려간 채 남는다 — 오늘 전용 instance에서는 schema owner가 다른 DB도 소유하므로
    `--restart`가 매번 그랬다(적대 리뷰 2026-09-29). 그래서 배포는 멈추기 **전에** 같은 판정
    (``_read_resettable_owners``)을 한 번 돌린다. 결박은 여전히 drop 직전의 같은 판정이다.
    """

    _read_resettable_owners(runtimes)


def _read_resettable_owners(
    runtimes: tuple[DatabaseRuntime, DatabaseRuntime],
) -> tuple[str | None, ...]:
    """리셋의 R2 판정(이름·허용 소유자·Map 소유자 배타성). 아무것도 바꾸지 않고 기존 소유자를 낸다."""

    if tuple(runtime.role for runtime in runtimes) != ("map_application", "pinvi"):
        raise DeploymentContractError("pinned runtime database roles are invalid")
    for runtime in runtimes:
        _validate_runtime(runtime)
        # 두 DB 모두 **첫 drop 전에** 본다. 종전에는 이 울타리가 PinVi 재생성 안에만
        # 있어서 Map DB는 울타리 없이 drop됐고, PinVi 이름이 막혀도 Map은 이미
        # 지워진 뒤였다.
        _require_tenant_database_name(runtime)
    existing_owners = tuple(_read_database_owner(runtime) for runtime in runtimes)
    for runtime, existing_owner in zip(runtimes, existing_owners, strict=True):
        if existing_owner is not None and existing_owner not in _permitted_existing_owners(
            runtime
        ):
            raise DeploymentContractError(
                f"{runtime.role} database owner differs from the frozen contract"
            )
    # Map drop 소유자는 Map application DB 밖의 DB를 소유하지 않아야 한다(R2). 소유자 집합은 대상을
    # 가리키는 바로 그 `.env` 값에서 오므로, 공용 instance에서는 다른 tenant의 login이
    # 일관되게 잘못 박히면 그대로 통과한다. 이름 목록 대신 live 소유 관계로 막는다. PinVi에는
    # 걸지 않는다 — PinVi app role은 이 재구축 밖의 DB(옛 `pinvi_dagster`, DROP 전까지)를
    # 정당하게 소유할 수 있다.
    map_runtime = runtimes[0]
    map_owner = existing_owners[0]
    if map_owner is not None and not _read_databases_owned_by(map_runtime, map_owner) <= {
        map_runtime.database_name
    }:
        raise DeploymentContractError(
            f"{map_runtime.role} database owner also owns a database outside the Map database"
        )
    return existing_owners


def create_fresh_application_300_database(runtime: DatabaseRuntime) -> None:
    """부재가 확인된 Map application DB를 ``template0``에서 한 번만 만든다."""

    _validate_runtime(runtime)
    if runtime.role != "map_application":
        raise DeploymentContractError("fresh application 300 database role is invalid")
    if _read_database_owner(runtime) is not None:
        raise DeploymentContractError("fresh application 300 database already exists")
    _run_checked(
        [
            *_database_admin_command(runtime, "createdb"),
            "--template",
            "template0",
            "--owner",
            runtime.owner_name,
            runtime.database_name,
        ],
        label="map_application fresh 300 database create",
    )


def ensure_map_application_database(
    runtime: DatabaseRuntime,
    *,
    run_role_bootstrap: Callable[[], None],
) -> MapApplicationEnsureOutcome:
    """마이그레이션 전진 배포에서 Map application DB를 한 번에 맞는 상태로 수렴한다(ADR-51).

    소유자 하나로 세 경우를 가른다.

    - DB가 없다: ``template0``에서 만들고 role bootstrap one-shot을 돌린다.
    - 아직 bootstrap 소유자(`runtime.owner_name`) 것이고 `alembic_version`이 없다: 만든 뒤
      bootstrap 전에 죽은 경우다. bootstrap만 돌린다.
    - schema owner 것이다: 이미 bootstrap된 운영 DB다. 아무것도 하지 않는다 — schema
      one-shot(`alembic upgrade head` + 권한 재조정)이 뒤따른다.

    그 밖의 상태는 거부한다(``require_map_application_database_convergible``). 나머지
    잔재 판정은 Map의 bootstrap 스크립트가 스스로 한다(첫 변경 전에 거부).
    """

    state = require_map_application_database_convergible(runtime)
    if state == "absent":
        create_fresh_application_300_database(runtime)
        run_role_bootstrap()
        return "created"
    if state == "unbootstrapped":
        run_role_bootstrap()
        return "bootstrapped"
    return "present"


def require_map_application_database_convergible(
    runtime: DatabaseRuntime,
) -> Literal["absent", "unbootstrapped", "present"]:
    """``ensure_map_application_database``가 갈 길을 읽기만으로 정하고, 못 가는 상태는 거부한다.

    배포는 이것을 런타임을 멈추기 **전에** 한 번 부른다. bootstrap 소유자 것인데 이미
    schema가 있는 DB(소유자 없이 복원된 백업 — ``createdb --owner`` + ``pg_restore``)는
    fresh 전용 role bootstrap이 거부하므로, 멈춘 뒤에야 알면 재실행마다 같은 자리에서
    런타임이 내려간 채 끝난다(B2 적대 리뷰 2차).
    """

    _validate_runtime(runtime)
    if runtime.role != "map_application":
        raise DeploymentContractError("Map application database role is invalid")
    _require_tenant_database_name(runtime)
    owner = _read_database_owner(runtime)
    if owner is None:
        return "absent"
    if owner == _MAP_SCHEMA_OWNER:
        return "present"
    if owner != runtime.owner_name:
        raise DeploymentContractError(
            "map_application database owner differs from the frozen contract"
        )
    if schema_revision_table_exists(runtime):
        # 공용 instance에서는 bootstrap 소유자(instance admin) 것인 같은 이름의 DB가 Map의
        # 것이라는 보장이 없다 — 넘기기 전에 확인하라고 말한다.
        raise DeploymentContractError(
            "map_application database already has a schema but is still owned by the "
            f"bootstrap owner; verify it is Map's database (its public.alembic_version is "
            f"a Map head) before handing it over with ALTER DATABASE {runtime.database_name} "
            f"OWNER TO {_MAP_SCHEMA_OWNER} or dropping it by hand, then rerun "
            "(docs/docker-management.md §7.7 'R2 거부')"
        )
    return "unbootstrapped"


#: Map `scripts/database-credential-preflight.sh`가 bootstrap password에 요구하는 모양의 거울.
_MAP_BOOTSTRAP_PASSWORD = re.compile(r"^[A-Za-z0-9._~-]{32,256}$")
#: Map fresh bootstrap이 instance에 요구하는 extension(Map `postgres-role-bootstrap.sh`).
_MAP_BOOTSTRAP_EXTENSIONS: Final = ("postgis", "pg_prewarm")
#: PostgreSQL `pg_authid.rolpassword`의 SCRAM-SHA-256 verifier:
#: `SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>`(base64).
_SCRAM_VERIFIER = re.compile(
    r"SCRAM-SHA-256\$(?P<iterations>[1-9][0-9]{0,9}):(?P<salt>[A-Za-z0-9+/]+={0,2})"
    r"\$(?P<stored_key>[A-Za-z0-9+/]+={0,2}):(?P<server_key>[A-Za-z0-9+/]+={0,2})"
)
#: 판정이 계산을 끝낼 수 있는 상한(기본 4096). 그보다 큰 verifier는 판정하지 않고 거부한다.
_SCRAM_MAX_ITERATIONS: Final = 10_000_000


def _scram_verifier_accepts(verifier: str, password: str) -> bool:
    """SCRAM-SHA-256 verifier가 ``password``를 받는가 — 네트워크·argv 없이 프로세스 안에서.

    RFC 5802/7677: SaltedPassword = PBKDF2-HMAC-SHA256(password, salt, i), ClientKey =
    HMAC(SaltedPassword, "Client Key"), StoredKey = SHA256(ClientKey). PostgreSQL은 password에
    SASLprep을 거는데, 이 판정을 부르는 자리는 URI-unreserved ASCII만 받으므로 그대로다. 두 값은
    비교만 하고 어디에도 싣지 않는다. 읽을 수 없는 verifier는 받지 않는다(거부 쪽).
    """

    match = _SCRAM_VERIFIER.fullmatch(verifier)
    if match is None or int(match["iterations"]) > _SCRAM_MAX_ITERATIONS:
        return False
    try:
        salt = base64.b64decode(match["salt"], validate=True)
        stored_key = base64.b64decode(match["stored_key"], validate=True)
    except (binascii.Error, ValueError):
        return False
    salted = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(match["iterations"])
    )
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    return hmac.compare_digest(hashlib.sha256(client_key).digest(), stored_key)


def _sql_like_prefix(prefix: str) -> str:
    """``prefix``로 시작하는 이름의 LIKE 패턴(escape 문자 `\\`). `_`·`%`는 글자 그대로다."""

    escaped = prefix.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
    return f"{escaped}%"


def require_map_bootstrap_admin_ready(
    runtime: DatabaseRuntime,
    *,
    resolved: Mapping[str, object],
    environment: Mapping[str, str],
) -> None:
    """Map fresh bootstrap이 instance admin으로 돌 수 있는지 **읽기만으로** 먼저 판정한다(ADR-53 S1).

    bootstrap one-shot은 Map·PinVi 런타임을 멈춘 **뒤에** 돈다. S1 고유의 실패(admin이 superuser가
    아니다, 거부될 role setting이 있다, extension이 없다, password 모양이 틀렸다)가 그때야 나면
    pair가 내려간 채 남는다. 그래서 bootstrap이 돌 때(앱 DB가 없거나 bootstrap 전이거나
    `--restart`) 멈추기 전에 한 번 본다:

    - socket으로 붙은 admin(`current_user`)이 superuser다.
    - database 0에 role 0·`current_user`·`ktm\\_%` role의 `pg_db_role_setting` 행이 없다.
    - `postgis`·`pg_prewarm`이 `pg_available_extensions`에 있다.
    - instance admin password(그 instance의 secret이 가리키는 `.env` 변수에서 읽는다)가 32–256자의
      URI-unreserved 문자이고 Map service·Dagster metadata password와 다르다. 비교만 한다 —
      값은 어디에도 싣지 않는다.
    - 그 password가 admin의 **살아있는** SCRAM-SHA-256 verifier에 맞는다. 평상시에 그 password로
      TCP 인증하는 것은 없다(Manager·백업·이 판정은 socket trust) — `ALTER ROLE` 회전이나 `.env`
      편집으로 둘이 어긋나면 창의 B9에서야 bootstrap이 "30초 안에 접속을 받지 않음"으로 멈춘 뒤
      실패한다. verifier는 같은 socket 경로로 읽고 프로세스 안에서만 비교한다.

    Map의 스크립트가 여전히 정본이다. 이것은 그 규칙을 **멈추기 전에** 비출 뿐이고, 스크립트가
    더 엄격해지면 one-shot이 오늘처럼 거부한다.
    """

    _validate_runtime(runtime)
    if runtime.role != "map_application" or runtime.owner_name != runtime.admin_name:
        raise DeploymentContractError("Map bootstrap runtime must be owned by the instance admin")
    secret = postgres_admin_secret(resolved, runtime.service_name)
    password = environment.get(secret.environment, "")
    if not _MAP_BOOTSTRAP_PASSWORD.fullmatch(password):
        raise DeploymentContractError(
            f"instance admin password ({secret.environment}) must be 32..256 URI-unreserved "
            "characters for the Map bootstrap"
        )
    if password in {
        environment.get("KOR_TRAVEL_MAP_SERVICE_PASSWORD"),
        environment.get("KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD"),
    }:
        raise DeploymentContractError(
            f"instance admin password ({secret.environment}) must differ from the Map service "
            "and Dagster metadata passwords"
        )
    extensions = ", ".join(f"'{name}'" for name in _MAP_BOOTSTRAP_EXTENSIONS)
    map_principals = _sql_like_prefix(MAP_PRINCIPAL_PREFIX)
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            "postgres",
            "--command",
            (
                "SELECT "
                "(SELECT role.rolsuper FROM pg_catalog.pg_roles AS role "
                "WHERE role.rolname = current_user), "
                "(SELECT count(*)::bigint FROM pg_catalog.pg_db_role_setting AS setting_row "
                "WHERE setting_row.setdatabase = 0 AND (setting_row.setrole = 0 "
                "OR setting_row.setrole IN (SELECT role.oid FROM pg_catalog.pg_roles AS role "
                "WHERE role.rolname = current_user "
                f"OR role.rolname LIKE '{map_principals}' ESCAPE '\\'))), "
                "(SELECT count(DISTINCT extension.name)::bigint "
                "FROM pg_catalog.pg_available_extensions AS extension "
                f"WHERE extension.name IN ({extensions}))"
            ),
        ],
        label="Map bootstrap admin readiness",
    ).decode("ascii").strip()
    fields = output.split("|") if len(output.splitlines()) == 1 else []
    if len(fields) != 3:
        raise DeploymentContractError("Map bootstrap admin readiness output is invalid")
    if not _parse_psql_bool(fields[0], "Map bootstrap admin superuser"):
        raise DeploymentContractError(
            "the instance admin must be a superuser for the Map bootstrap"
        )
    if _parse_non_negative_int(fields[1], "Map bootstrap role settings") != 0:
        raise DeploymentContractError(
            "the instance has cluster-wide role settings the Map bootstrap refuses "
            "(ALTER ROLE ALL/<admin>/ktm_* SET)"
        )
    if _parse_non_negative_int(fields[2], "Map bootstrap extensions") != len(
        _MAP_BOOTSTRAP_EXTENSIONS
    ):
        raise DeploymentContractError(
            "the instance lacks an extension the Map bootstrap requires "
            f"({', '.join(_MAP_BOOTSTRAP_EXTENSIONS)})"
        )
    # superuser만 `pg_authid`를 읽는다 — 그래서 superuser임을 확인한 **뒤** 따로 읽는다(한 질의에
    # 넣으면 superuser가 아닌 admin에게 권한 오류가 나 위의 거부 문구를 가린다).
    verifier_lines = (
        _run_checked(
            [
                *_database_admin_command(runtime, "psql"),
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--dbname",
                "postgres",
                "--command",
                "SELECT COALESCE(authid.rolpassword, '') FROM pg_catalog.pg_authid AS authid "
                "WHERE authid.rolname = current_user",
            ],
            label="Map bootstrap admin password verifier",
        )
        .decode("ascii", errors="replace")
        .strip()
        .splitlines()
    )
    verifier = verifier_lines[0] if len(verifier_lines) == 1 else ""
    if not _scram_verifier_accepts(verifier, password):
        # verifier도 password도 문구에 넣지 않는다 — 어느 변수인지만 말한다.
        raise DeploymentContractError(
            f"instance admin password ({secret.environment}) does not match the instance "
            "admin's live SCRAM-SHA-256 verifier; the Map bootstrap could not authenticate"
        )


def create_database_if_absent(runtime: DatabaseRuntime) -> bool:
    """DB가 없을 때만 frozen 계약의 소유자로 만든다(PinVi는 ``template0``). 만들었으면 True.

    마이그레이션 전진 배포의 일반 경로는 DB를 지우지 않는다. 그래도 새 호스트나 지워진
    DB에서는 만들 길이 있어야 한다 — 없으면 Map을 이미 올린 뒤 PinVi bootstrap에서
    실패해 전 서비스가 정지한다(B2 적대 리뷰).
    """

    _validate_runtime(runtime)
    _require_tenant_database_name(runtime)
    if read_database_identity(runtime) is not None:
        return False
    _recreate_empty_database_after_owner_preflight(runtime, existing_owner=None)
    return True


def map_application_connection_cap(usable: int) -> int:
    """non-superuser가 쓸 수 있는 슬롯 수 ``usable``에서 Map application DB의 연결 상한을 낸다(D5).

    ``floor(0.4 × usable)``, 최소 1. 공용 instance에서 Map의 최악(API·code-server·run
    프로세스마다 풀)이 다른 tenant의 슬롯을 다 먹지 않게 묶는다. 0.4가 유일한 정책
    숫자다 — 72 h 관측에서 `too many connections for database`가 보이면 여기만 올린다.
    정수 산술이라 부동소수 경계가 없다.
    """

    if isinstance(usable, bool) or not isinstance(usable, int) or usable < 1:
        raise DeploymentContractError("PostgreSQL usable connection slots are invalid")
    return max(1, usable * 2 // 5)


def map_application_login(environment: Mapping[str, str]) -> str:
    """frozen env ``KOR_TRAVEL_MAP_PG_DSN``의 login — Map application DB에 CONNECT를 받는 유일한 login.

    이름은 env에서만 온다. C6c가 그 DSN의 endpoint·DB와 login의 자리(Map principal이 아님)를
    결박하고, 그것이 정말 Map의 login인지는 R4가 live role 그래프로 확인한다
    (``ensure_map_databases_isolated``).
    """

    try:
        username = urlsplit(environment.get("KOR_TRAVEL_MAP_PG_DSN", "")).username
    except ValueError as exc:
        raise DeploymentContractError("Map application login is invalid") from exc
    login = unquote(username or "")
    if not _DATABASE_IDENTIFIER.fullmatch(login):
        raise DeploymentContractError("Map application login is invalid")
    return login


def ensure_map_databases_isolated(
    app: DatabaseRuntime,
    *,
    login: str,
) -> None:
    """Map application DB를 PUBLIC에 닫고 login CONNECT와 연결 상한을 건 뒤 **읽어서** 확인한다(R4).

    공용 instance의 DB 단위 CONNECT는 다른 모든 tenant에서 Manager가 소유한다(db-init
    one-shot). Map DB만 기본값(PUBLIC CONNECT)으로 남으면 같은 instance의 모든 login이
    Map DB에 붙을 수 있다. app DB의 login(`ktm_feature_service`)은 소유 role을
    ``INHERIT FALSE``로 들고 있어 소유자 권한으로는 붙지 못하므로 명시적으로 준다. 상한은 같은
    instance에서 live로 읽은 슬롯에서 유도한다(``map_application_connection_cap``) — superuser는
    상한을 받지 않는다. 옛 Map Dagster metadata DB는 다루지 않는다(모듈 docstring) — 막힌
    DB의 ACL을 다시 쓰지 않는다.

    **env가 아니라 live 그래프에 결박한다.** 이름은 모두 `.env`에서 온다. 일관되게 잘못 박힌
    env는 그 자신과 비교하는 read-back을 그대로 통과한다 — 다른 tenant의 login에 Map DB CONNECT를
    주거나, 다른 tenant DB의 PUBLIC CONNECT를 걷는다(적대 리뷰 2026-09-28 실측). 그래서 한
    transaction(``--single-transaction``) 안에서 바꾸기 **전에** live 소유·role 그래프를 확인하고
    (``_map_isolation_precondition_sql``), 바꾼 **뒤** 같은 transaction에서 다시 읽는다
    (``_map_isolation_readback_sql``). 어느 쪽이 거부해도 GRANT·REVOKE·상한이 함께 롤백된다 —
    거부가 반쯤 바뀐 ACL을 남기지 않는다.

    app DB의 명시 CONNECT 가운데 소유자·login 밖의 것은 걷는다(``_revoke_stray_connect_sql``).
    옛 login·손으로 준 grant 하나가 이후 모든 수렴·배포를 막지 않게 하는 수렴이다.

    fresh bootstrap은 ``datacl IS NULL``·template1과 같은 ``datconnlimit``을 요구하므로 반드시
    bootstrap **뒤에** 부른다. 멱등이다 — 같은 입력으로 다시 부르면 ACL이 바뀌지 않는다.
    PinVi DB는 건드리지 않는다.

    REVOKE는 연결할 때만 검사되므로, read-back이 통과한 뒤 같은 transaction의 끝에서 Map DB에
    붙어 있지만 이제 CONNECT가 없는 client 세션을 끝낸다(``_terminate_sessions_without_connect_sql``).
    """

    _validate_map_isolation(app, login=login)
    cap = map_application_connection_cap(_read_usable_connection_slots(app))
    app_database = _sql_identifier(app.database_name)
    sql = (
        _map_isolation_precondition_sql(app, login=login)
        + f"REVOKE CONNECT ON DATABASE {app_database} FROM PUBLIC;\n"
        + _revoke_stray_connect_sql(app, keep=login)
        + f"GRANT CONNECT ON DATABASE {app_database} TO {_sql_identifier(login)};\n"
        + f"ALTER DATABASE {app_database} CONNECTION LIMIT {cap};\n"
        + _map_isolation_readback_sql(app, login=login, connection_limit=cap)
        + _terminate_sessions_without_connect_sql(app)
    )
    _run_checked_with_input(
        [
            *_database_admin_interactive_command(app, "psql"),
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--single-transaction",
            "--dbname",
            "postgres",
        ],
        input_bytes=sql.encode("ascii"),
        label="Map database isolation",
    )


def require_map_databases_isolatable(
    app: DatabaseRuntime,
    *,
    login: str,
) -> None:
    """R4의 live 전제(``_map_isolation_precondition_sql``)를 **읽기만으로** 먼저 판정한다.

    전체 배포 경로는 R4 전에 Map·PinVi 런타임을 멈추고 Map schema를 head로 올린다. 전제 거부가
    그 뒤에야 나면 pair가 내려간 채 남는다(적대 리뷰 2026-09-29 — 예: `KOR_TRAVEL_MAP_PG_DSN`을
    Map의 member가 아닌 login으로 바꾼 뒤의 새 pair). 넘겨받은 app DB의 소유자와 login의
    membership은 그 경로가 R4 전에 바꾸지 않는다 — role bootstrap은 fresh DB에서만 돈다. 그래서
    같은 DO 블록을 ``READ ONLY`` transaction에서 멈추기 **전에** 돌려 같은 거부를 앞당긴다.
    결박은 여전히 ``ensure_map_databases_isolated``의 transaction 안 판정이다.

    app DB가 schema owner 것일 때(``require_map_application_database_convergible``이
    ``present``)만 부른다.
    """

    _validate_map_isolation(app, login=login)
    _run_checked_with_input(
        [
            *_database_admin_interactive_command(app, "psql"),
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--single-transaction",
            "--dbname",
            "postgres",
        ],
        input_bytes=(
            "SET TRANSACTION READ ONLY;\n" + _map_isolation_precondition_sql(app, login=login)
        ).encode("ascii"),
        label="Map database isolation preflight",
    )


def _validate_map_isolation(app: DatabaseRuntime, *, login: str) -> None:
    """R4 입력(Map application runtime·login)을 확인한다. 아무것도 읽지 않는다."""

    _validate_runtime(app)
    if app.role != "map_application":
        raise DeploymentContractError("Map database isolation roles are invalid")
    _require_tenant_database_name(app)
    if not _DATABASE_IDENTIFIER.fullmatch(login):
        raise DeploymentContractError("Map application login is invalid")


def _map_isolation_precondition_sql(
    app: DatabaseRuntime,
    *,
    login: str,
) -> str:
    """R4가 무엇이든 바꾸기 **전에** live 소유·role 그래프를 확인하는 DO 블록(거부는 RAISE → 롤백).

    - app DB는 Map schema owner 소유다 — ensure 경로가 이미 요구하는 넘겨받음 그대로다.
    - login은 LOGIN이고 superuser가 아니며 그 소유자의 member다. Map bootstrap이 service login에
      schema owner를 ``INHERIT FALSE``로 주는 불변식이다. 다른 tenant의 login은 Map schema owner의
      member가 아니므로 이름 목록 없이 갈린다.
    """

    return (
        "DO $r4_precondition$\n"
        "DECLARE\n"
        "    app_owner oid;\n"
        "BEGIN\n"
        "    SELECT datdba INTO app_owner FROM pg_catalog.pg_database\n"
        f"        WHERE datname = '{app.database_name}';\n"
        "    IF app_owner IS NULL\n"
        f"        OR pg_catalog.pg_get_userbyid(app_owner) <> '{_MAP_SCHEMA_OWNER}' THEN\n"
        f"        RAISE EXCEPTION 'map_application database {app.database_name} is not owned by "
        f"{_MAP_SCHEMA_OWNER} (owner=%)', pg_catalog.pg_get_userbyid(app_owner);\n"
        "    END IF;\n"
        "    IF NOT EXISTS (\n"
        "        SELECT 1 FROM pg_catalog.pg_roles AS role\n"
        f"        WHERE role.rolname = '{login}' AND role.rolcanlogin AND NOT role.rolsuper\n"
        "          AND pg_catalog.pg_has_role(role.oid, app_owner, 'MEMBER')\n"
        "    ) THEN\n"
        f"        RAISE EXCEPTION 'Map application login {login} is not a non-superuser "
        f"LOGIN member of {_MAP_SCHEMA_OWNER}';\n"
        "    END IF;\n"
        "END\n"
        "$r4_precondition$;\n"
    )


def _revoke_stray_connect_sql(runtime: DatabaseRuntime, *, keep: str) -> str:
    """이 DB의 명시 CONNECT 가운데 소유자·``keep``·PUBLIC 밖의 grantee에서 CONNECT를 걷는 DO 블록.

    이름 목록이 없다 — ACL에서 유도한다. superuser의 REVOKE는 소유자가 준 grant만 걷는다. 다른
    grantor가 grant option으로 준 것은 남고, 뒤따르는 read-back이 그것을 잡아 전체를 롤백한다.
    """

    return (
        "DO $r4_converge$\n"
        "DECLARE\n"
        "    stray text;\n"
        "BEGIN\n"
        "    FOR stray IN\n"
        "        SELECT DISTINCT pg_catalog.pg_get_userbyid(entry.grantee)\n"
        "        FROM pg_catalog.pg_database AS database_row\n"
        "        CROSS JOIN LATERAL pg_catalog.aclexplode(database_row.datacl) AS entry\n"
        f"        WHERE database_row.datname = '{runtime.database_name}'\n"
        "          AND entry.privilege_type = 'CONNECT'\n"
        "          AND entry.grantee <> 0\n"
        "          AND entry.grantee <> database_row.datdba\n"
        f"          AND pg_catalog.pg_get_userbyid(entry.grantee) <> '{keep}'\n"
        "    LOOP\n"
        "        EXECUTE pg_catalog.format(\n"
        f"            'REVOKE CONNECT ON DATABASE %I FROM %I', '{runtime.database_name}', stray\n"
        "        );\n"
        "    END LOOP;\n"
        "END\n"
        "$r4_converge$;\n"
    )


def _map_isolation_readback_sql(
    runtime: DatabaseRuntime,
    *,
    login: str,
    connection_limit: int | None,
) -> str:
    """같은 transaction 안의 read-back DO 블록. 어긋나면 RAISE → 앞선 GRANT·REVOKE·상한이 롤백된다.

    ACL이 있고, PUBLIC CONNECT가 없고, CONNECT 가능한 non-superuser login이 정확히 ``login``이며,
    상한이 주어졌으면 그 값이다. ACL 술어만으로는 role membership으로 얻는 CONNECT를 놓친다 —
    그래서 ``has_database_privilege``로 login 집합 전체를 재고, 거부 문구에 그 집합을 싣는다(role
    이름은 비밀이 아니다).
    """

    limit_mismatch = (
        ""
        if connection_limit is None
        else f"\n        OR database_row.datconnlimit <> {connection_limit}"
    )
    return (
        "DO $r4_readback$\n"
        "DECLARE\n"
        "    database_row pg_catalog.pg_database%ROWTYPE;\n"
        "    public_connect boolean;\n"
        "    connect_logins text[];\n"
        "BEGIN\n"
        "    SELECT * INTO STRICT database_row FROM pg_catalog.pg_database\n"
        f"        WHERE datname = '{runtime.database_name}';\n"
        "    public_connect := EXISTS (\n"
        "        SELECT 1 FROM pg_catalog.aclexplode(database_row.datacl) AS entry\n"
        "        WHERE entry.grantee = 0 AND entry.privilege_type = 'CONNECT'\n"
        "    );\n"
        "    connect_logins := COALESCE((\n"
        "        SELECT pg_catalog.array_agg(role.rolname::text ORDER BY role.rolname)\n"
        "        FROM pg_catalog.pg_roles AS role\n"
        "        WHERE role.rolcanlogin AND NOT role.rolsuper\n"
        "          AND pg_catalog.has_database_privilege(role.oid, database_row.oid, 'CONNECT')\n"
        "    ), ARRAY[]::text[]);\n"
        "    IF database_row.datacl IS NULL OR public_connect\n"
        f"        OR connect_logins <> ARRAY['{login}']::text[]{limit_mismatch} THEN\n"
        f"        RAISE EXCEPTION '{runtime.role} database is not isolated after the grant "
        "(acl=%, public_connect=%, connect_logins=%, connection_limit=%)', "
        "database_row.datacl IS NOT NULL, public_connect, connect_logins, "
        "database_row.datconnlimit;\n"
        "    END IF;\n"
        "END\n"
        "$r4_readback$;\n"
    )


def _terminate_sessions_without_connect_sql(app: DatabaseRuntime) -> str:
    """Map DB에 붙어 있으나 이제 CONNECT가 없는 client 세션을 끝낸다(R4 transaction의 끝).

    REVOKE CONNECT는 **연결할 때만** 검사된다. 앱 DB는 createdb부터 R4까지(bootstrap·alembic,
    몇 분) PUBLIC CONNECT이므로, 그 사이 붙은 다른 tenant login의 세션은 R4 뒤에도
    살아 연결 상한을 먹는다. 같은 transaction이 방금 바꾼 ACL로 판정한다(`has_database_privilege`는
    membership까지 센다) — Map login·superuser는 남는다. 거부할 수 있는 모든 검사
    (read-back) **뒤에** 두어, 거부된 R4는 아무 세션도 끊지 않는다.
    """

    return (
        "SELECT pg_catalog.pg_terminate_backend(activity.pid)\n"
        "    FROM pg_catalog.pg_stat_activity AS activity\n"
        "    WHERE activity.backend_type = 'client backend'\n"
        f"      AND activity.datname = '{app.database_name}'\n"
        "      AND NOT pg_catalog.has_database_privilege(\n"
        "          activity.usesysid, activity.datid, 'CONNECT'\n"
        "      );\n"
    )


def _read_usable_connection_slots(runtime: DatabaseRuntime) -> int:
    """non-superuser 슬롯 = ``max_connections − superuser_reserved − reserved``(live)."""

    _validate_runtime(runtime)
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            "postgres",
            "--command",
            (
                "SELECT pg_catalog.current_setting('max_connections')::integer "
                "- pg_catalog.current_setting('superuser_reserved_connections')::integer "
                # PostgreSQL 16부터 있다. 없는 판에서는 0이다.
                "- COALESCE(pg_catalog.current_setting('reserved_connections', true), '0')"
                "::integer"
            ),
        ],
        label=f"{runtime.role} usable connection slots",
    ).decode("ascii").strip()
    return _parse_positive_int(output, "PostgreSQL usable connection slots")


def read_database_identity(runtime: DatabaseRuntime) -> tuple[str, int, str] | None:
    """maintenance DB에서 (이름, oid, system identifier)를 읽는다. DB가 없으면 ``None``.

    마이그레이션 전진 배포는 이 셋으로 "지난 배포가 본 그 DB인가"를 잰다(ADR-51) —
    누가 지우고 다시 만들면 oid가 바뀐다.
    """

    _validate_runtime(runtime)
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            "postgres",
            "--command",
            (
                "SELECT datname, oid::bigint, "
                "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) "
                "FROM pg_catalog.pg_database "
                f"WHERE datname = '{runtime.database_name}'"
            ),
        ],
        label=f"{runtime.role} database identity",
    ).decode("ascii").strip()
    if not output:
        return None
    lines = output.splitlines()
    fields = lines[0].split("|") if len(lines) == 1 else []
    if len(fields) != 3 or fields[0] != runtime.database_name:
        raise DeploymentContractError(f"{runtime.role} database identity output is invalid")
    return (
        fields[0],
        _parse_positive_int(fields[1], f"{runtime.role} database oid"),
        _parse_system_identifier(fields[2], f"{runtime.role} PostgreSQL system identifier"),
    )


def schema_revision_table_exists(runtime: DatabaseRuntime) -> bool:
    """role의 Alembic 표가 있는가 — 없으면 한 번도 migration되지 않은 빈 DB다(ADR-51).

    PinVi의 fresh-install fence처럼 **빈 DB에서만** 필요한 단계를 고를 때 쓴다.
    """

    _validate_runtime(runtime)
    schema_name, table_name = _SCHEMA_REVISION_LOCATION[runtime.role]
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            runtime.database_name,
            "--command",
            f"SELECT to_regclass('\"{schema_name}\".\"{table_name}\"') IS NOT NULL",
        ],
        label=f"{runtime.role} schema revision table",
    ).decode("ascii").strip()
    return _parse_psql_bool(output, f"{runtime.role} schema revision table")


def read_database_schema_revision(runtime: DatabaseRuntime) -> str:
    """role에 고정된 Alembic table에서 하나의 revision만 읽는다."""

    _validate_runtime(runtime)
    schema_name, table_name = _SCHEMA_REVISION_LOCATION[runtime.role]
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            runtime.database_name,
            "--command",
            f'SELECT version_num FROM "{schema_name}"."{table_name}"',
        ],
        label=f"{runtime.role} schema revision",
    ).decode("ascii").strip()
    lines = output.splitlines()
    if len(lines) != 1 or not _SCHEMA_REVISION.fullmatch(lines[0]):
        raise DeploymentContractError(f"{runtime.role} schema revision output is invalid")
    return lines[0]


def _read_database_owner(runtime: DatabaseRuntime) -> str | None:
    _validate_runtime(runtime)
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            "postgres",
            "--command",
            (
                "SELECT pg_get_userbyid(datdba) FROM pg_database "
                f"WHERE datname = '{runtime.database_name}'"
            ),
        ],
        label=f"{runtime.role} database owner",
    ).decode("ascii").strip()
    if not output:
        return None
    if "\n" in output or not _DATABASE_IDENTIFIER.fullmatch(output):
        raise DeploymentContractError(f"{runtime.role} database owner output is invalid")
    return output


def _read_databases_owned_by(runtime: DatabaseRuntime, owner: str) -> frozenset[str]:
    """``owner``가 이 instance에서 소유한 DB 이름들(maintenance DB에서 읽는다).

    한 줄에 이름 하나다. 줄바꿈을 품은 이름은 조각으로 읽혀 Map 쌍의 부분집합이 될 수 없으므로
    거부 쪽으로 떨어진다.
    """

    _validate_runtime(runtime)
    if not _DATABASE_IDENTIFIER.fullmatch(owner):
        raise DeploymentContractError(f"{runtime.role} database owner is invalid")
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            "postgres",
            "--command",
            (
                "SELECT datname FROM pg_catalog.pg_database WHERE datdba = "
                "(SELECT oid FROM pg_catalog.pg_roles "
                f"WHERE rolname = '{owner}')"
            ),
        ],
        label=f"{runtime.role} database owner's databases",
    ).decode("utf-8").strip()
    return frozenset(line for line in output.splitlines() if line)


def _permitted_existing_owners(runtime: DatabaseRuntime) -> frozenset[str]:
    """destructive reset이 지워도 되는 기존 소유자. **instance admin은 절대 들지 않는다.**

    Map bootstrap 뒤 ownership만 추가로 수용한다. admin을 빼는 이유(R2): 공용 instance의
    admin은 `postgres`·template·다른 tenant가 반쯤 만든 DB를 소유한다. admin 소유 DB는
    이 재구축이 만든 것이라는 증거가 없으므로 지우지 않는다 — 전용 instance에서도 Map
    bootstrap 소유자가 admin이라, 반쯤 만든 Map DB는 `--restart`가 지우지 않고 거부한다
    (fail-closed, 손으로 지우는 절차는 docs/docker-management.md §7.7 'R2 거부').
    """

    if runtime.role == "map_application":
        owners = frozenset({runtime.owner_name, _MAP_SCHEMA_OWNER})
    else:
        owners = frozenset({runtime.owner_name})
    return owners - {runtime.admin_name}


def _validate_runtime(runtime: DatabaseRuntime) -> None:
    if runtime.role not in _ROLE_CONFIG:
        raise DeploymentContractError("pinned runtime database role is invalid")
    if not _CONTAINER_NAME.fullmatch(runtime.service_name):
        raise DeploymentContractError("pinned runtime database service is invalid")
    if not _CONTAINER_NAME.fullmatch(runtime.container_name):
        raise DeploymentContractError("pinned runtime database container is invalid")
    if not 1 <= runtime.port <= 65535:
        raise DeploymentContractError("pinned runtime database port is invalid")
    if not _DATABASE_IDENTIFIER.fullmatch(runtime.database_name):
        raise DeploymentContractError("pinned runtime database name is invalid")
    if not _DATABASE_IDENTIFIER.fullmatch(runtime.owner_name):
        raise DeploymentContractError("pinned runtime database owner is invalid")
    if not _DATABASE_IDENTIFIER.fullmatch(runtime.admin_name):
        raise DeploymentContractError("pinned runtime database admin role is invalid")


def _database_admin_command(
    runtime: DatabaseRuntime,
    executable: Literal["psql", "dropdb", "createdb"],
) -> list[str]:
    _validate_runtime(runtime)
    return [
        "docker",
        "exec",
        "--user",
        "postgres",
        runtime.container_name,
        executable,
        "--username",
        runtime.admin_name,
        "--port",
        str(runtime.port),
    ]


def _database_admin_interactive_command(
    runtime: DatabaseRuntime,
    executable: Literal["psql", "dropdb", "createdb"],
) -> list[str]:
    command = _database_admin_command(runtime, executable)
    return [*command[:2], "--interactive", *command[2:]]


def _sql_identifier(value: str) -> str:
    if not _DATABASE_IDENTIFIER.fullmatch(value):
        raise DeploymentContractError("PostgreSQL identifier is invalid")
    return f'"{value}"'


def _parse_psql_bool(value: str, label: str) -> bool:
    if value == "t":
        return True
    if value == "f":
        return False
    raise DeploymentContractError(f"{label} output is invalid")


def _parse_positive_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise DeploymentContractError(f"{label} output is invalid") from exc
    if parsed <= 0:
        raise DeploymentContractError(f"{label} output is invalid")
    return parsed


def _parse_non_negative_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise DeploymentContractError(f"{label} output is invalid") from exc
    if parsed < 0:
        raise DeploymentContractError(f"{label} output is invalid")
    return parsed


def _parse_system_identifier(value: str, label: str) -> str:
    if not value.isdigit():
        raise DeploymentContractError(f"{label} output is invalid")
    return value


def _run_checked(arguments: list[str], *, label: str) -> bytes:
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(f"{label} could not run") from exc
    if completed.returncode != 0 or completed.stderr:
        # stderr만 싣는다(ADR-51 잃는 보장 G). stdout은 조회 결과라 원인이 아니다.
        raise DeploymentContractError(
            f"{label} failed (exit {completed.returncode})"
            + command_output_tail("stderr", completed.stderr)
        )
    if not isinstance(completed.stdout, bytes):
        raise DeploymentContractError(f"{label} produced invalid output")
    return completed.stdout


def _run_checked_with_input(
    arguments: list[str],
    *,
    input_bytes: bytes,
    label: str,
) -> bytes:
    try:
        completed = subprocess.run(
            arguments,
            input=input_bytes,
            capture_output=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(f"{label} could not run") from exc
    if completed.returncode != 0 or completed.stderr:
        # stderr만 싣는다(ADR-51 잃는 보장 G). stdout은 조회 결과라 원인이 아니다.
        raise DeploymentContractError(
            f"{label} failed (exit {completed.returncode})"
            + command_output_tail("stderr", completed.stderr)
        )
    if not isinstance(completed.stdout, bytes):
        raise DeploymentContractError(f"{label} produced invalid output")
    return completed.stdout
