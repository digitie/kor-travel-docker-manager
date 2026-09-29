"""F1D pinned runtime의 세 PostgreSQL 데이터베이스 재생성 경계.

동결된 Compose 계약에서 Map application, Map Dagster, PinVi의 정확한 대상만
유도한다. 이 모듈은 백업·복원·진단 상태를 알지 못한다. v5 rebuild는 서비스가
모두 멈춘 뒤 이 경계로 세 DB를 파기하고, 각 이미지의 bootstrap/migration으로
데이터를 다시 만든다.

세 DB가 어느 PostgreSQL instance에 사는지는 이름이 아니라 **DSN 포트**에서 유도한다
(ADR-53): 그 포트를 `-p`로 듣는 PostgreSQL 서버 서비스가 정확히 하나여야 한다.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import unquote, urlsplit

from kor_travel_docker_manager.services.c6c_deployment import (
    _PINVI_API_SERVICE,
    _PINVI_DATABASE_URL_ENV,
    DeploymentContractError,
    postgres_admin_secret,
    postgres_server_services_on_port,
)
from kor_travel_docker_manager.services.errors import command_output_tail

DatabaseRole = Literal["map_application", "map_dagster", "pinvi"]
MapApplicationEnsureOutcome = Literal["created", "bootstrapped", "present"]

_DATABASE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_SCHEMA_REVISION = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
#: role마다 (DB 이름 env, 기본값, 소유자 env·기본값). 소유자가 ``None``이면 **그 instance의
#: admin**이다(ADR-53 S1) — Map fresh bootstrap은 instance의 기존 admin으로 돌고, bootstrap이
#: 끝나면 앱 DB를 schema owner에게 넘긴다. 전용 Map superuser(`KOR_TRAVEL_MAP_POSTGRES_USER`)는
#: 퇴역했다.
_ROLE_CONFIG: dict[DatabaseRole, tuple[str, str, tuple[str, str] | None]] = {
    "map_application": ("KOR_TRAVEL_MAP_POSTGRES_DB", "kor_travel_map", None),
    "map_dagster": ("KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB", "kor_travel_map_dagster", None),
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
    "map_dagster": ("public", "alembic_version"),
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
    additional_owner_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DagsterMetadataRoleAttributes:
    """Dagster metadata login role의 privilege/membership snapshot."""

    superuser: bool
    create_database: bool
    create_role: bool
    replication: bool
    bypass_rls: bool
    granted_role_count: int
    member_role_count: int
    can_login: bool = True
    inherit: bool = False
    connection_limit: int = -1
    valid_until_is_null: bool = True
    role_config_count: int = 0
    database_role_setting_count: int = 0


@dataclass(frozen=True)
class DagsterMetadataDatabaseIdentity:
    """Dagster storage permit에 쓰는 non-secret metadata database identity."""

    system_identifier: str
    name: str
    oid: int
    owner: str
    login_role: str
    login_role_attributes: DagsterMetadataRoleAttributes


@dataclass(frozen=True)
class _DagsterMetadataRolePreflight:
    """mutation 전 기존 metadata role이 password-only rotate 대상인지 판정한다."""

    can_login: bool
    inherit: bool
    attributes: DagsterMetadataRoleAttributes
    #: 이 role이 소유한 DB 수와 `pg_shdepend` 참조 행 수. 둘 다 0이어야 남은 role이다(R2).
    owned_database_count: int
    shared_dependency_count: int


@dataclass(frozen=True)
class _PostgresInstance:
    """DSN 포트에서 유도한 PostgreSQL 서버 하나(서비스·컨테이너·admin·포트)."""

    service_name: str
    container_name: str
    admin_name: str
    port: int


def _dsn_authority(dsn: object, *, label: str) -> tuple[str, int]:
    """DSN의 (host, port). host는 `127.0.0.1`이어야 한다 — 모든 instance가 loopback만 듣는다."""

    try:
        parsed = urlsplit(dsn if isinstance(dsn, str) else "")
        host, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise DeploymentContractError(f"{label} database DSN authority is invalid") from exc
    if host != "127.0.0.1" or port is None or not 1 <= port <= 65535:
        raise DeploymentContractError(f"{label} database DSN authority is invalid")
    return host, port


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
    postgres_environment = postgres.get("environment") if isinstance(postgres, Mapping) else None
    admin_name = (
        postgres_environment.get("POSTGRES_USER")
        if isinstance(postgres_environment, Mapping)
        else None
    )
    if not isinstance(admin_name, str) or not _DATABASE_IDENTIFIER.fullmatch(admin_name):
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
) -> tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime]:
    """동결된 resolved Compose와 env에서 v5의 canonical 세 DB를 유도한다.

    instance는 DSN에서 온다 — Map 둘은 `KOR_TRAVEL_MAP_PG_DSN`(Dagster URL은 같은 authority여야
    한다), PinVi는 `pinvi-api`의 resolved `PINVI_DATABASE_URL`이다. Map 소유자는 그 instance의
    admin이다(S1). 세 DB가 한 instance에 있어도 된다 — 이름이 서로 다르기만 하면 된다.
    """

    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("pinned runtime Compose services are invalid")

    map_dsn = environment.get("KOR_TRAVEL_MAP_PG_DSN", "")
    if _dsn_authority(
        environment.get("KOR_TRAVEL_MAP_DAGSTER_PG_URL", ""), label="Map Dagster"
    ) != _dsn_authority(map_dsn, label="Map"):
        raise DeploymentContractError(
            "Map application and Dagster DSNs must share one PostgreSQL authority"
        )
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
    instances["map_dagster"] = instances["map_application"]

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
        additional_owner_names: frozenset[str] = frozenset()
        if role == "map_dagster":
            metadata_owner = environment.get("KOR_TRAVEL_MAP_DAGSTER_METADATA_USER", "")
            if not _DATABASE_IDENTIFIER.fullmatch(metadata_owner):
                raise DeploymentContractError("Map Dagster metadata role is invalid")
            additional_owner_names = frozenset({metadata_owner})
        runtimes.append(
            DatabaseRuntime(
                role=role,
                service_name=instance.service_name,
                container_name=instance.container_name,
                port=instance.port,
                database_name=database_name,
                owner_name=owner_name,
                admin_name=instance.admin_name,
                additional_owner_names=additional_owner_names,
            )
        )
    if len({runtime.database_name for runtime in runtimes}) != len(runtimes):
        raise DeploymentContractError(
            "pinned runtime databases must have distinct frozen database names"
        )
    return runtimes[0], runtimes[1], runtimes[2]


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
    runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
) -> None:
    """Map 두 DB는 제거하고 PinVi DB만 즉시 다시 만든다.

    application-300은 application DB를 ``template0``에서 별도 생성하고 metadata
    DB도 격리된 identity producer가 만든다. 따라서 generic drop/create가 두 Map
    DB를 미리 만들면 virgin-root 및 sealed metadata permit 계약을 우회한다.
    """

    existing_owners = _read_resettable_owners(runtimes)
    for runtime, existing_owner in zip(runtimes[:2], existing_owners[:2], strict=True):
        if existing_owner is not None:
            _run_checked(
                [
                    *_database_admin_command(runtime, "dropdb"),
                    "--force",
                    runtime.database_name,
                ],
                label=f"{runtime.role} database destructive drop",
            )
    _recreate_empty_database_after_owner_preflight(
        runtimes[2],
        existing_owner=existing_owners[2],
    )


def require_databases_resettable(
    runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
) -> None:
    """``reset_databases_for_application_300``이 거부할 상태를 **읽기만으로** 먼저 거부한다(R2).

    전체 배포 경로는 리셋 전에 Map·PinVi 런타임을 멈춘다. 이름·소유자·배타성 거부가 그 뒤에야
    나면 pair가 내려간 채 남는다 — 오늘 전용 instance에서는 schema owner가 다른 DB도 소유하므로
    `--restart`가 매번 그랬다(적대 리뷰 2026-09-29). 그래서 배포는 멈추기 **전에** 같은 판정
    (``_read_resettable_owners``)을 한 번 돌린다. 결박은 여전히 drop 직전의 같은 판정이다.
    """

    _read_resettable_owners(runtimes)


def _read_resettable_owners(
    runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
) -> tuple[str | None, ...]:
    """리셋의 R2 판정(이름·허용 소유자·Map 소유자 배타성). 아무것도 바꾸지 않고 기존 소유자를 낸다."""

    if tuple(runtime.role for runtime in runtimes) != (
        "map_application",
        "map_dagster",
        "pinvi",
    ):
        raise DeploymentContractError("pinned runtime database roles are invalid")
    for runtime in runtimes:
        _validate_runtime(runtime)
        # 세 DB 모두 **첫 drop 전에** 본다. 종전에는 이 울타리가 PinVi 재생성 안에만
        # 있어서 Map 두 DB는 울타리 없이 drop됐고, PinVi 이름이 막혀도 Map은 이미
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
    # Map drop 소유자는 Map 쌍 밖의 DB를 소유하지 않아야 한다(R2). 소유자 집합은 대상을
    # 가리키는 바로 그 `.env` 값에서 오므로, 공용 instance에서는 다른 tenant의 login이
    # 일관되게 잘못 박히면 그대로 통과한다(예: metadata user·Dagster DB 이름에 PinVi의
    # login·DB). 이름 목록 대신 live 소유 관계로 막는다. PinVi에는 걸지 않는다 — PinVi
    # app role은 이 재구축 밖의 `pinvi_dagster`를 정당하게 소유한다.
    map_databases = frozenset(runtime.database_name for runtime in runtimes[:2])
    for runtime, existing_owner in zip(runtimes[:2], existing_owners[:2], strict=True):
        if existing_owner is None:
            continue
        if not _read_databases_owned_by(runtime, existing_owner) <= map_databases:
            raise DeploymentContractError(
                f"{runtime.role} database owner also owns a database outside the Map pair"
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
                "WHERE role.rolname = current_user OR role.rolname LIKE 'ktm\\_%' ESCAPE '\\'))), "
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
    dagster: DatabaseRuntime,
    *,
    login: str,
) -> None:
    """Map 두 DB를 PUBLIC에 닫고 app DB에 login CONNECT와 연결 상한을 건 뒤 **읽어서** 확인한다(R4).

    공용 instance의 DB 단위 CONNECT는 다른 모든 tenant에서 Manager가 소유한다(db-init
    one-shot). Map DB만 기본값(PUBLIC CONNECT)으로 남으면 같은 instance의 모든 login이
    Map DB에 붙을 수 있다. app DB의 login(`ktm_feature_service`)은 소유 role을
    ``INHERIT FALSE``로 들고 있어 소유자 권한으로는 붙지 못하므로 명시적으로 준다. Dagster
    DB는 소유자(metadata user)가 CTc를 그대로 갖는다. 상한은 같은 instance에서 live로 읽은
    슬롯에서 유도한다(``map_application_connection_cap``) — superuser는 상한을 받지 않는다.

    **env가 아니라 live 그래프에 결박한다.** 이름은 모두 `.env`에서 온다. 일관되게 잘못 박힌
    env는 그 자신과 비교하는 read-back을 그대로 통과한다 — 다른 tenant의 login에 Map DB CONNECT를
    주거나, 다른 tenant DB의 PUBLIC CONNECT를 걷는다(적대 리뷰 2026-09-28 실측). 그래서 한
    transaction(``--single-transaction``) 안에서 바꾸기 **전에** live 소유·role 그래프를 확인하고
    (``_map_isolation_precondition_sql``), 바꾼 **뒤** 같은 transaction에서 다시 읽는다
    (``_map_isolation_readback_sql``). 어느 쪽이 거부해도 GRANT·REVOKE·상한이 함께 롤백된다 —
    거부가 반쯤 바뀐 ACL을 남기지 않는다.

    app DB의 명시 CONNECT 가운데 소유자·login 밖의 것은 걷는다(``_revoke_stray_connect_sql``).
    옛 login·손으로 준 grant 하나가 이후 모든 수렴·배포를 막지 않게 하는 수렴이다. Dagster DB에는
    걸지 않는다 — 그 DB는 소유자 이름으로만 결박되므로 거기서는 바꾸는 범위를 PUBLIC에 둔다.

    fresh bootstrap은 ``datacl IS NULL``·template1과 같은 ``datconnlimit``을 요구하므로 반드시
    bootstrap **뒤에** 부른다. 멱등이다 — 같은 입력으로 다시 부르면 ACL이 바뀌지 않는다.
    PinVi DB는 건드리지 않는다.
    """

    metadata_user = _map_isolation_metadata_user(app, dagster, login=login)
    cap = map_application_connection_cap(_read_usable_connection_slots(app))
    app_database = _sql_identifier(app.database_name)
    dagster_database = _sql_identifier(dagster.database_name)
    sql = (
        _map_isolation_precondition_sql(app, dagster, login=login, metadata_user=metadata_user)
        + f"REVOKE CONNECT ON DATABASE {app_database} FROM PUBLIC;\n"
        + _revoke_stray_connect_sql(app, keep=login)
        + f"GRANT CONNECT ON DATABASE {app_database} TO {_sql_identifier(login)};\n"
        + f"REVOKE CONNECT ON DATABASE {dagster_database} FROM PUBLIC;\n"
        + f"ALTER DATABASE {app_database} CONNECTION LIMIT {cap};\n"
        + _map_isolation_readback_sql(app, login=login, connection_limit=cap)
        + _map_isolation_readback_sql(dagster, login=metadata_user, connection_limit=None)
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
    dagster: DatabaseRuntime,
    *,
    login: str,
) -> None:
    """R4의 live 전제(``_map_isolation_precondition_sql``)를 **읽기만으로** 먼저 판정한다.

    전체 배포 경로는 R4 전에 Map·PinVi 런타임을 멈추고 Map schema를 head로 올린다. 전제 거부가
    그 뒤에야 나면 pair가 내려간 채 남는다(적대 리뷰 2026-09-29 — 예: `KOR_TRAVEL_MAP_PG_DSN`을
    Map의 member가 아닌 login으로 바꾼 뒤의 새 pair). 넘겨받은 app DB의 소유자와 login의
    membership, 이미 있는 Dagster DB의 소유자·배타성은 그 경로가 R4 전에 바꾸지 않는다 — role
    bootstrap은 fresh DB에서만 돌고, Dagster init은 DB가 없을 때만 돈다. 그래서 같은 DO 블록을
    ``READ ONLY`` transaction에서 멈추기 **전에** 돌려 같은 거부를 앞당긴다. 결박은 여전히
    ``ensure_map_databases_isolated``의 transaction 안 판정이다.

    app DB가 schema owner 것일 때(``require_map_application_database_convergible``이
    ``present``)만 부른다. Dagster DB가 없으면 판정하지 않는다 — init이 만든 뒤 R4가 판정한다.
    """

    metadata_user = _map_isolation_metadata_user(app, dagster, login=login)
    if _read_database_owner(dagster) is None:
        return
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
            "SET TRANSACTION READ ONLY;\n"
            + _map_isolation_precondition_sql(
                app, dagster, login=login, metadata_user=metadata_user
            )
        ).encode("ascii"),
        label="Map database isolation preflight",
    )


def _map_isolation_metadata_user(
    app: DatabaseRuntime,
    dagster: DatabaseRuntime,
    *,
    login: str,
) -> str:
    """R4 입력(두 Map runtime·login)을 확인하고 frozen metadata user를 낸다. 아무것도 읽지 않는다."""

    _validate_runtime(app)
    _validate_runtime(dagster)
    if app.role != "map_application" or dagster.role != "map_dagster":
        raise DeploymentContractError("Map database isolation roles are invalid")
    if (app.service_name, app.container_name, app.port, app.admin_name) != (
        dagster.service_name,
        dagster.container_name,
        dagster.port,
        dagster.admin_name,
    ):
        raise DeploymentContractError("Map databases must share one PostgreSQL instance")
    _require_tenant_database_name(app)
    _require_tenant_database_name(dagster)
    if len(dagster.additional_owner_names) != 1:
        raise DeploymentContractError("Map Dagster metadata role is not frozen")
    (metadata_user,) = dagster.additional_owner_names
    if not _DATABASE_IDENTIFIER.fullmatch(login):
        raise DeploymentContractError("Map application login is invalid")
    return metadata_user


def _map_isolation_precondition_sql(
    app: DatabaseRuntime,
    dagster: DatabaseRuntime,
    *,
    login: str,
    metadata_user: str,
) -> str:
    """R4가 무엇이든 바꾸기 **전에** live 소유·role 그래프를 확인하는 DO 블록(거부는 RAISE → 롤백).

    - app DB는 Map schema owner 소유다 — ensure 경로가 이미 요구하는 넘겨받음 그대로다.
    - login은 LOGIN이고 superuser가 아니며 그 소유자의 member다. Map bootstrap이 service login에
      schema owner를 ``INHERIT FALSE``로 주는 불변식이다. 다른 tenant의 login은 Map schema owner의
      member가 아니므로 이름 목록 없이 갈린다.
    - Dagster DB는 frozen metadata user 소유이고, 그 user는 다른 DB를 소유하지 않는다(R2 배타성과
      같은 모양). C6c의 metadata user = Dagster DB 이름 규칙과 함께, 자기 이름이 아닌 DB를 소유하는
      tenant login(PinVi처럼 둘을 가진 것 포함)을 막는다. 자기 이름의 DB **하나만** 가진 다른 tenant
      login은 이 그래프로 Map의 것과 갈리지 않는다 — 남은 위험으로 문서에 적었다(§7.7).
    """

    return (
        "DO $r4_precondition$\n"
        "DECLARE\n"
        "    app_owner oid;\n"
        "    dagster_owner oid;\n"
        "    other_databases text[];\n"
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
        "    SELECT datdba INTO dagster_owner FROM pg_catalog.pg_database\n"
        f"        WHERE datname = '{dagster.database_name}';\n"
        "    IF dagster_owner IS NULL\n"
        f"        OR pg_catalog.pg_get_userbyid(dagster_owner) <> '{metadata_user}' THEN\n"
        f"        RAISE EXCEPTION 'map_dagster database {dagster.database_name} is not owned by "
        f"the Dagster metadata user {metadata_user} (owner=%)', "
        "pg_catalog.pg_get_userbyid(dagster_owner);\n"
        "    END IF;\n"
        "    SELECT pg_catalog.array_agg(datname::text ORDER BY datname) INTO other_databases\n"
        "        FROM pg_catalog.pg_database\n"
        f"        WHERE datdba = dagster_owner AND datname <> '{dagster.database_name}';\n"
        "    IF other_databases IS NOT NULL THEN\n"
        f"        RAISE EXCEPTION 'Dagster metadata user {metadata_user} also owns %', "
        "other_databases;\n"
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


def initialize_application_300_dagster_metadata_database(
    runtime: DatabaseRuntime,
    *,
    metadata_user: str,
    metadata_password: str,
) -> DagsterMetadataDatabaseIdentity:
    """Map Dagster metadata role/DB를 fresh application 300용으로 생성한다.

    모든 read-only preflight를 먼저 끝낸 뒤, 기존 안전 role은 password만 바꾸고
    metadata DB는 ``template0``에서 새로 만든다. 이 함수는 기존 DB를 drop하지 않는다.
    """

    _validate_dagster_metadata_runtime(runtime, metadata_user)
    _validate_password(metadata_password)

    existing_owner = _read_database_owner(runtime)
    role_preflight = _read_dagster_metadata_role_preflight(runtime, metadata_user)
    if existing_owner is not None:
        raise DeploymentContractError("Map Dagster metadata database already exists")
    if role_preflight is not None:
        _assert_dagster_metadata_role_can_rotate_password_only(role_preflight)

    if role_preflight is None:
        _mutate_dagster_metadata_role(
            runtime,
            metadata_user=metadata_user,
            metadata_password=metadata_password,
            existing_role=False,
        )
    else:
        _mutate_dagster_metadata_role(
            runtime,
            metadata_user=metadata_user,
            metadata_password=metadata_password,
            existing_role=True,
        )
    _run_checked(
        [
            *_database_admin_command(runtime, "createdb"),
            "--maintenance-db",
            "postgres",
            "--template",
            "template0",
            "--owner",
            metadata_user,
            runtime.database_name,
        ],
        label="Map Dagster metadata database create",
    )
    return read_application_300_dagster_metadata_identity(
        runtime,
        metadata_user=metadata_user,
    )


def read_application_300_dagster_metadata_identity(
    runtime: DatabaseRuntime,
    *,
    metadata_user: str,
) -> DagsterMetadataDatabaseIdentity:
    """maintenance DB에서 Dagster metadata DB와 login role identity를 strict 조회한다."""

    _validate_dagster_metadata_runtime(runtime, metadata_user)
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
                "SELECT control.system_identifier::text, database_row.datname, "
                "database_row.oid::bigint, pg_get_userbyid(database_row.datdba), "
                "role.rolname, role.rolcanlogin, role.rolinherit, "
                "role.rolsuper, role.rolcreatedb, role.rolcreaterole, "
                "role.rolreplication, role.rolbypassrls, role.rolconnlimit, "
                "(role.rolvaliduntil IS NULL), "
                "COALESCE(pg_catalog.cardinality(role.rolconfig), 0), "
                "(SELECT count(*)::bigint FROM pg_catalog.pg_db_role_setting setting "
                "WHERE setting.setrole = role.oid), "
                "(SELECT count(*)::bigint FROM pg_catalog.pg_auth_members membership "
                "WHERE membership.member = role.oid), "
                "(SELECT count(*)::bigint FROM pg_catalog.pg_auth_members membership "
                "WHERE membership.roleid = role.oid) "
                "FROM pg_catalog.pg_database AS database_row "
                "JOIN pg_catalog.pg_roles AS role ON role.oid = database_row.datdba "
                "CROSS JOIN pg_catalog.pg_control_system() AS control "
                f"WHERE database_row.datname = '{runtime.database_name}' "
                f"AND role.rolname = '{metadata_user}'"
            ),
        ],
        label="Map Dagster metadata database identity",
    )
    return _parse_dagster_metadata_database_identity(
        output,
        runtime=runtime,
        metadata_user=metadata_user,
    )


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


def _read_dagster_metadata_role_preflight(
    runtime: DatabaseRuntime,
    metadata_user: str,
) -> _DagsterMetadataRolePreflight | None:
    _validate_dagster_metadata_runtime(runtime, metadata_user)
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
                "SELECT rolcanlogin, rolinherit, rolsuper, rolcreatedb, "
                "rolcreaterole, rolreplication, rolbypassrls, rolconnlimit, "
                "(rolvaliduntil IS NULL), "
                "COALESCE(pg_catalog.cardinality(rolconfig), 0), "
                "(SELECT count(*)::bigint FROM pg_catalog.pg_db_role_setting setting "
                "WHERE setting.setrole = role.oid), "
                "(SELECT count(*)::bigint FROM pg_catalog.pg_auth_members membership "
                "WHERE membership.member = role.oid), "
                "(SELECT count(*)::bigint FROM pg_catalog.pg_auth_members membership "
                "WHERE membership.roleid = role.oid), "
                # R2: 남은 metadata role은 DB가 사라지면 아무것도 소유하지 않는다(소유 DB와
                # 그 DB 안 객체의 행이 함께 사라진다). 무언가를 소유한 login은 남은 role이
                # 아니라 다른 tenant의 login이다 — 그 password를 돌리면 안 된다.
                "(SELECT count(*)::bigint FROM pg_catalog.pg_database owned "
                "WHERE owned.datdba = role.oid), "
                "(SELECT count(*)::bigint FROM pg_catalog.pg_shdepend dependency "
                "WHERE dependency.refclassid = 'pg_catalog.pg_authid'::regclass "
                "AND dependency.refobjid = role.oid) "
                "FROM pg_catalog.pg_roles AS role "
                f"WHERE role.rolname = '{metadata_user}'"
            ),
        ],
        label="Map Dagster metadata role preflight",
    ).decode("ascii").strip()
    if not output:
        return None
    lines = output.splitlines()
    if len(lines) != 1:
        raise DeploymentContractError("Map Dagster metadata role output is invalid")
    fields = lines[0].split("|")
    if len(fields) != 15:
        raise DeploymentContractError("Map Dagster metadata role output is invalid")
    return _DagsterMetadataRolePreflight(
        can_login=_parse_psql_bool(fields[0], "Map Dagster metadata role login"),
        inherit=_parse_psql_bool(fields[1], "Map Dagster metadata role inherit"),
        owned_database_count=_parse_non_negative_int(
            fields[13], "Map Dagster metadata role owned database count"
        ),
        shared_dependency_count=_parse_non_negative_int(
            fields[14], "Map Dagster metadata role shared dependency count"
        ),
        attributes=DagsterMetadataRoleAttributes(
            superuser=_parse_psql_bool(fields[2], "Map Dagster metadata role superuser"),
            create_database=_parse_psql_bool(
                fields[3], "Map Dagster metadata role createdb"
            ),
            create_role=_parse_psql_bool(
                fields[4], "Map Dagster metadata role createrole"
            ),
            replication=_parse_psql_bool(
                fields[5], "Map Dagster metadata role replication"
            ),
            bypass_rls=_parse_psql_bool(
                fields[6], "Map Dagster metadata role bypassrls"
            ),
            connection_limit=_parse_connection_limit(
                fields[7], "Map Dagster metadata role connection limit"
            ),
            valid_until_is_null=_parse_psql_bool(
                fields[8], "Map Dagster metadata role validity"
            ),
            role_config_count=_parse_non_negative_int(
                fields[9], "Map Dagster metadata role config count"
            ),
            database_role_setting_count=_parse_non_negative_int(
                fields[10], "Map Dagster metadata database role setting count"
            ),
            granted_role_count=_parse_non_negative_int(
                fields[11], "Map Dagster metadata role granted role count"
            ),
            member_role_count=_parse_non_negative_int(
                fields[12], "Map Dagster metadata role member role count"
            ),
            can_login=_parse_psql_bool(
                fields[0], "Map Dagster metadata role login"
            ),
            inherit=_parse_psql_bool(
                fields[1], "Map Dagster metadata role inherit"
            ),
        ),
    )


def _assert_dagster_metadata_role_can_rotate_password_only(
    role: _DagsterMetadataRolePreflight,
) -> None:
    attributes = role.attributes
    if (
        not role.can_login
        or role.inherit
        or attributes.superuser
        or attributes.create_database
        or attributes.create_role
        or attributes.replication
        or attributes.bypass_rls
        or attributes.connection_limit != -1
        or not attributes.valid_until_is_null
        or attributes.role_config_count != 0
        or attributes.database_role_setting_count != 0
        or attributes.granted_role_count != 0
        or attributes.member_role_count != 0
        or role.owned_database_count != 0
        or role.shared_dependency_count != 0
    ):
        raise DeploymentContractError("Map Dagster metadata role is unsafe")


def _mutate_dagster_metadata_role(
    runtime: DatabaseRuntime,
    *,
    metadata_user: str,
    metadata_password: str,
    existing_role: bool,
) -> None:
    _validate_dagster_metadata_runtime(runtime, metadata_user)
    _validate_password(metadata_password)
    role = _sql_identifier(metadata_user)
    password = _sql_literal(metadata_password)
    if existing_role:
        sql = f"ALTER ROLE {role} PASSWORD {password};\n"
        label = "Map Dagster metadata role password rotate"
    else:
        sql = f"CREATE ROLE {role} LOGIN NOINHERIT PASSWORD {password};\n"
        label = "Map Dagster metadata role create"
    _run_checked_with_input(
        [
            *_database_admin_interactive_command(runtime, "psql"),
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--dbname",
            "postgres",
        ],
        input_bytes=sql.encode("utf-8"),
        label=label,
    )


def _parse_dagster_metadata_database_identity(
    output: bytes,
    *,
    runtime: DatabaseRuntime,
    metadata_user: str,
) -> DagsterMetadataDatabaseIdentity:
    text = output.decode("ascii").strip()
    lines = text.splitlines()
    if len(lines) != 1:
        raise DeploymentContractError("Map Dagster metadata identity is invalid")
    fields = lines[0].split("|")
    if len(fields) != 18:
        raise DeploymentContractError("Map Dagster metadata identity is invalid")
    (
        system_identifier,
        name,
        oid_raw,
        owner,
        login_role,
        can_login,
        inherit,
        superuser,
        createdb,
        createrole,
        replication,
        bypass_rls,
        connection_limit,
        valid_until_is_null,
        role_config_count,
        database_role_setting_count,
        granted_count,
        member_count,
    ) = fields
    if name != runtime.database_name or owner != metadata_user or login_role != metadata_user:
        raise DeploymentContractError("Map Dagster metadata identity binding is invalid")
    attributes = DagsterMetadataRoleAttributes(
        superuser=_parse_psql_bool(superuser, "Map Dagster metadata role superuser"),
        create_database=_parse_psql_bool(createdb, "Map Dagster metadata role createdb"),
        create_role=_parse_psql_bool(createrole, "Map Dagster metadata role createrole"),
        replication=_parse_psql_bool(replication, "Map Dagster metadata role replication"),
        bypass_rls=_parse_psql_bool(bypass_rls, "Map Dagster metadata role bypassrls"),
        connection_limit=_parse_connection_limit(
            connection_limit, "Map Dagster metadata role connection limit"
        ),
        valid_until_is_null=_parse_psql_bool(
            valid_until_is_null, "Map Dagster metadata role validity"
        ),
        role_config_count=_parse_non_negative_int(
            role_config_count, "Map Dagster metadata role config count"
        ),
        database_role_setting_count=_parse_non_negative_int(
            database_role_setting_count,
            "Map Dagster metadata database role setting count",
        ),
        granted_role_count=_parse_non_negative_int(
            granted_count, "Map Dagster metadata role granted role count"
        ),
        member_role_count=_parse_non_negative_int(
            member_count, "Map Dagster metadata role member role count"
        ),
        can_login=_parse_psql_bool(can_login, "Map Dagster metadata role login"),
        inherit=_parse_psql_bool(inherit, "Map Dagster metadata role inherit"),
    )
    if (
        not attributes.can_login
        or attributes.inherit
        or attributes.superuser
        or attributes.create_database
        or attributes.create_role
        or attributes.replication
        or attributes.bypass_rls
        or attributes.connection_limit != -1
        or not attributes.valid_until_is_null
        or attributes.role_config_count != 0
        or attributes.database_role_setting_count != 0
        or attributes.granted_role_count != 0
        or attributes.member_role_count != 0
    ):
        raise DeploymentContractError("Map Dagster metadata identity is unsafe")
    return DagsterMetadataDatabaseIdentity(
        system_identifier=_parse_system_identifier(
            system_identifier,
            "Map Dagster metadata PostgreSQL system identifier",
        ),
        name=name,
        oid=_parse_positive_int(oid_raw, "Map Dagster metadata database oid"),
        owner=owner,
        login_role=login_role,
        login_role_attributes=attributes,
    )


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
        owners = frozenset({runtime.owner_name, *runtime.additional_owner_names})
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
    if any(
        not _DATABASE_IDENTIFIER.fullmatch(owner_name)
        for owner_name in runtime.additional_owner_names
    ):
        raise DeploymentContractError("pinned runtime database owner is invalid")


def _validate_dagster_metadata_runtime(runtime: DatabaseRuntime, metadata_user: str) -> None:
    _validate_runtime(runtime)
    if runtime.role != "map_dagster":
        raise DeploymentContractError("Map Dagster metadata database role is invalid")
    if not _DATABASE_IDENTIFIER.fullmatch(metadata_user):
        raise DeploymentContractError("Map Dagster metadata role is invalid")
    if metadata_user == runtime.owner_name or metadata_user not in runtime.additional_owner_names:
        raise DeploymentContractError("Map Dagster metadata role is not frozen")


def _validate_password(value: str) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DeploymentContractError("Map Dagster metadata password is invalid")


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


def _sql_literal(value: str) -> str:
    _validate_password(value)
    return "'" + value.replace("'", "''") + "'"


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


def _parse_connection_limit(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise DeploymentContractError(f"{label} output is invalid") from exc
    if parsed < -1:
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
