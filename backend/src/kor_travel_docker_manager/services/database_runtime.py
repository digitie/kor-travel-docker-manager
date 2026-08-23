"""F1D pinned runtime의 세 PostgreSQL 데이터베이스 재생성 경계.

동결된 Compose 계약에서 Map application, Map Dagster, PinVi의 정확한 대상만
유도한다. 이 모듈은 백업·복원·진단 상태를 알지 못한다. v5 rebuild는 서비스가
모두 멈춘 뒤 이 경계로 세 DB를 파기하고, 각 이미지의 bootstrap/migration으로
데이터를 다시 만든다.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

DatabaseRole = Literal["map_application", "map_dagster", "pinvi"]

_DATABASE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_SCHEMA_REVISION = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_ROLE_CONFIG: dict[DatabaseRole, tuple[str, str, str, str, str]] = {
    "map_application": (
        "KOR_TRAVEL_MAP_POSTGRES_DB",
        "kor_travel_map",
        "KOR_TRAVEL_MAP_POSTGRES_USER",
        "kor_travel_map",
        "kor-travel-map-postgres",
    ),
    "map_dagster": (
        "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB",
        "kor_travel_map_dagster",
        "KOR_TRAVEL_MAP_POSTGRES_USER",
        "kor_travel_map",
        "kor-travel-map-postgres",
    ),
    "pinvi": (
        "PINVI_POSTGRES_DB",
        "pinvi",
        "PINVI_POSTGRES_USER",
        "pinvi",
        "pinvi-postgres",
    ),
}
_ROLE_PORT_CONFIG: dict[DatabaseRole, tuple[str, int]] = {
    "map_application": ("KOR_TRAVEL_MAP_POSTGRES_PORT", 12700),
    "map_dagster": ("KOR_TRAVEL_MAP_POSTGRES_PORT", 12700),
    "pinvi": ("PINVI_DB_PORT", 12800),
}
_SCHEMA_REVISION_LOCATION: dict[DatabaseRole, tuple[str, str]] = {
    "map_application": ("public", "alembic_version"),
    "map_dagster": ("public", "alembic_version"),
    "pinvi": ("app", "alembic_version"),
}
_MAP_SCHEMA_OWNER = "ktm_feature_schema_owner"
_MAP_REQUIRED_LOGIN_ROLES = (
    "ktm_feature_migrator",
    "ktm_feature_api_runtime",
    "ktm_feature_dagster_runtime",
)
_MAP_REQUIRED_GROUP_ROLES = (
    _MAP_SCHEMA_OWNER,
    "ktm_feature_state_procedure_owner",
    "ktm_feature_audit_writer",
    "ktm_feature_runtime",
    # F1D candidate heads 0226~0236 add the curation command/audit/executor
    # boundary.  These are NOLOGIN groups and are part of the exact role graph
    # produced by Map's canonical bootstrap script.
    "ktm_curation_command_owner",
    "ktm_curation_audit_writer",
    "ktm_curation_admin_executor",
    "ktm_curation_provider_executor",
)

# Map role bootstrap runs in a PostgreSQL cluster whose roles survive the
# application/Dagster database recreation.  A fresh legacy bootstrap therefore
# legitimately sees memberships created by the later M01~M05 phases from an
# earlier generation.  Those roles are verified by their own phase-specific
# bootstrap assertions; the legacy assertion must not mistake their preserved
# memberships for drift in the base graph.
_MAP_FUTURE_PHASE_ROLES = (
    "ktm_manual_feature_procedure_owner",
    "ktm_manual_feature_admin_executor",
    "ktm_feature_create_provider_executor",
    "ktm_feature_request_procedure_owner",
    "ktm_feature_request_service_executor",
    "ktm_feature_request_admin_executor",
    "ktm_manual_provider_dedup_procedure_owner",
    "ktm_manual_provider_dedup_detector_executor",
    "ktm_manual_provider_dedup_admin_executor",
    "ktm_feature_reference_reconciliation_service_executor",
)


@dataclass(frozen=True)
class DatabaseRuntime:
    """동결된 Compose 계약에서 얻은 하나의 PostgreSQL database identity."""

    role: DatabaseRole
    container_name: str
    port: int
    database_name: str
    owner_name: str
    admin_name: str
    additional_owner_names: frozenset[str] = frozenset()


def database_runtimes_from_frozen_contract(
    *,
    resolved: Mapping[str, object],
    environment: Mapping[str, str],
) -> tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime]:
    """동결된 resolved Compose와 env에서 v5의 canonical 세 DB를 유도한다."""

    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("pinned runtime Compose services are invalid")

    runtimes: list[DatabaseRuntime] = []
    for role, (
        database_env,
        database_default,
        owner_env,
        owner_default,
        postgres_service,
    ) in _ROLE_CONFIG.items():
        postgres = services.get(postgres_service)
        container_name = postgres.get("container_name") if isinstance(postgres, Mapping) else None
        if not isinstance(container_name, str) or not _CONTAINER_NAME.fullmatch(container_name):
            raise DeploymentContractError(
                f"{role} PostgreSQL container identity is invalid"
            )
        postgres_environment = postgres.get("environment") if isinstance(postgres, Mapping) else None
        admin_name = (
            postgres_environment.get("POSTGRES_USER")
            if isinstance(postgres_environment, Mapping)
            else None
        )
        if not isinstance(admin_name, str) or not _DATABASE_IDENTIFIER.fullmatch(admin_name):
            raise DeploymentContractError(f"{role} PostgreSQL admin role is invalid")
        port_env, port_default = _ROLE_PORT_CONFIG[role]
        port_text = environment.get(port_env, str(port_default))
        try:
            port = int(port_text)
        except (TypeError, ValueError) as exc:
            raise DeploymentContractError(f"{role} PostgreSQL port is invalid") from exc
        if not 1 <= port <= 65535:
            raise DeploymentContractError(f"{role} PostgreSQL port is invalid")
        database_name = environment.get(database_env, database_default)
        owner_name = environment.get(owner_env, owner_default)
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
                container_name=container_name,
                port=port,
                database_name=database_name,
                owner_name=owner_name,
                admin_name=admin_name,
                additional_owner_names=additional_owner_names,
            )
        )
    map_application, map_dagster, pinvi = runtimes
    if map_application.container_name != map_dagster.container_name:
        raise DeploymentContractError(
            "Map application and Dagster databases must share the frozen PostgreSQL container"
        )
    if pinvi.container_name == map_application.container_name:
        raise DeploymentContractError(
            "PinVi database must use a distinct frozen PostgreSQL container"
        )
    if len({runtime.database_name for runtime in runtimes}) != len(runtimes):
        raise DeploymentContractError(
            "pinned runtime databases must have distinct frozen database names"
        )
    return runtimes[0], runtimes[1], runtimes[2]


def recreate_empty_database(runtime: DatabaseRuntime) -> None:
    """계약상 owner가 맞는 하나의 DB만 파기 후 같은 owner로 다시 만든다."""

    _validate_runtime(runtime)
    _recreate_empty_database_after_owner_preflight(
        runtime,
        existing_owner=_read_database_owner(runtime),
    )


def _recreate_empty_database_after_owner_preflight(
    runtime: DatabaseRuntime,
    *,
    existing_owner: str | None,
) -> None:
    """사전 owner 검증이 끝난 하나의 DB를 파기·재생성한다."""

    _validate_runtime(runtime)
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
    _run_checked(
        [
            *_database_admin_command(runtime, "createdb"),
            "--owner",
            runtime.owner_name,
            runtime.database_name,
        ],
        label=f"{runtime.role} database destructive create",
    )
def recreate_empty_databases(
    runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
) -> None:
    """Map application·Dagster·PinVi DB를 canonical 순서로 함께 다시 만든다."""

    if tuple(runtime.role for runtime in runtimes) != (
        "map_application",
        "map_dagster",
        "pinvi",
    ):
        raise DeploymentContractError("pinned runtime database roles are invalid")
    for runtime in runtimes:
        _validate_runtime(runtime)
    existing_owners = tuple(_read_database_owner(runtime) for runtime in runtimes)
    for runtime, existing_owner in zip(runtimes, existing_owners, strict=True):
        if existing_owner is not None and existing_owner not in _permitted_existing_owners(
            runtime
        ):
            raise DeploymentContractError(
                f"{runtime.role} database owner differs from the frozen contract"
            )
    for runtime, existing_owner in zip(runtimes, existing_owners, strict=True):
        _recreate_empty_database_after_owner_preflight(
            runtime,
            existing_owner=existing_owner,
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


def assert_map_database_principal_bootstrap(
    runtime: DatabaseRuntime,
    dagster_runtime: DatabaseRuntime,
    dagster_metadata_user: str | None,
) -> None:
    """Map bootstrap의 역할·소유권·ACL 경계를 catalog에서 fail-close 검증한다.

    upstream bootstrap은 PostgreSQL 16 membership option, schema/object ownership,
    runtime ACL revoke를 모두 신뢰 경계로 정의한다. role 존재 여부만 확인하면
    checkpoint 뒤의 drift를 정상 bootstrap으로 오인할 수 있으므로, F1D resume은
    이 전체 catalog 상태를 매번 확인한다.
    """

    _validate_runtime(runtime)
    _validate_runtime(dagster_runtime)
    if runtime.role != "map_application" or dagster_runtime.role != "map_dagster":
        raise DeploymentContractError("Map principal assertion requires Map databases")
    if runtime.container_name != dagster_runtime.container_name:
        raise DeploymentContractError("Map principal assertion requires one PostgreSQL container")
    if runtime.admin_name != dagster_runtime.admin_name:
        raise DeploymentContractError("Map principal assertion requires one PostgreSQL admin")
    if (
        not isinstance(dagster_metadata_user, str)
        or not _DATABASE_IDENTIFIER.fullmatch(dagster_metadata_user)
        or dagster_metadata_user in (*_MAP_REQUIRED_GROUP_ROLES, *_MAP_REQUIRED_LOGIN_ROLES)
    ):
        raise DeploymentContractError("Map Dagster metadata role is invalid")

    expected_roles = (*_MAP_REQUIRED_GROUP_ROLES, *_MAP_REQUIRED_LOGIN_ROLES)
    expected_values = ", ".join(f"('{role}')" for role in expected_roles)
    expected_names = ", ".join(f"'{role}'" for role in expected_roles)
    future_phase_names = ", ".join(f"'{role}'" for role in _MAP_FUTURE_PHASE_ROLES)
    group_names = ", ".join(f"'{role}'" for role in _MAP_REQUIRED_GROUP_ROLES)
    login_names = ", ".join(f"'{role}'" for role in _MAP_REQUIRED_LOGIN_ROLES)
    runtime_principal_names = ", ".join(
        f"'{role}'"
        for role in (
            "ktm_feature_runtime",
            "ktm_feature_api_runtime",
            "ktm_feature_dagster_runtime",
        )
    )
    query = (
        f"WITH expected(role_name) AS (VALUES {expected_values}), "
        "expected_membership(member_name, role_name, inherit_option, set_option) AS "
        "(VALUES "
        "('ktm_feature_migrator', 'ktm_feature_schema_owner', FALSE, TRUE), "
        "('ktm_feature_api_runtime', 'ktm_feature_runtime', TRUE, FALSE), "
        "('ktm_feature_dagster_runtime', 'ktm_feature_runtime', TRUE, FALSE), "
        "('ktm_feature_schema_owner', 'ktm_feature_state_procedure_owner', FALSE, TRUE), "
        "('ktm_feature_schema_owner', 'ktm_feature_audit_writer', FALSE, TRUE), "
        "('ktm_feature_schema_owner', 'ktm_curation_command_owner', FALSE, TRUE), "
        "('ktm_feature_schema_owner', 'ktm_curation_audit_writer', FALSE, TRUE), "
        "('ktm_feature_api_runtime', 'ktm_curation_admin_executor', TRUE, FALSE), "
        "('ktm_feature_dagster_runtime', 'ktm_curation_provider_executor', TRUE, FALSE)) "
        "SELECT CASE WHEN "
        "(SELECT pg_get_userbyid(datdba) FROM pg_database "
        f"WHERE datname = '{runtime.database_name}') = '{_MAP_SCHEMA_OWNER}' "
        "AND (SELECT pg_get_userbyid(datdba) FROM pg_database "
        f"WHERE datname = '{dagster_runtime.database_name}') = '{dagster_metadata_user}' "
        "AND NOT EXISTS (SELECT 1 FROM expected LEFT JOIN pg_roles "
        "ON rolname = expected.role_name WHERE rolname IS NULL) "
        "AND NOT EXISTS (SELECT 1 FROM pg_roles "
        f"WHERE rolname IN ({expected_names}) "
        "AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolreplication)) "
        "AND NOT EXISTS (SELECT 1 FROM pg_roles "
        f"WHERE rolname IN ({group_names}) AND (rolcanlogin OR rolinherit)) "
        "AND NOT EXISTS (SELECT 1 FROM pg_roles "
        f"WHERE rolname IN ({login_names}, '{dagster_metadata_user}') "
        "AND (NOT rolcanlogin OR rolinherit)) "
        "AND NOT EXISTS (SELECT 1 FROM expected_membership expected "
        "LEFT JOIN pg_roles member_role ON member_role.rolname = expected.member_name "
        "LEFT JOIN pg_roles granted_role ON granted_role.rolname = expected.role_name "
        "LEFT JOIN pg_auth_members membership ON membership.member = member_role.oid "
        "AND membership.roleid = granted_role.oid "
        "WHERE membership.member IS NULL "
        "OR membership.admin_option "
        "OR membership.inherit_option IS DISTINCT FROM expected.inherit_option "
        "OR membership.set_option IS DISTINCT FROM expected.set_option) "
        "AND NOT EXISTS (SELECT 1 FROM pg_auth_members membership "
        "JOIN pg_roles member_role ON member_role.oid = membership.member "
        "LEFT JOIN expected_membership expected ON expected.member_name = member_role.rolname "
        "AND expected.role_name = pg_get_userbyid(membership.roleid) "
        f"WHERE member_role.rolname IN ({expected_names}, '{dagster_metadata_user}') "
        f"AND pg_get_userbyid(membership.roleid) NOT IN ({future_phase_names}) "
        "AND expected.member_name IS NULL) "
        "AND NOT EXISTS (SELECT 1 FROM pg_auth_members membership "
        "JOIN pg_roles granted_role ON granted_role.oid = membership.roleid "
        "LEFT JOIN expected_membership expected ON expected.member_name "
        "= pg_get_userbyid(membership.member) "
        "AND expected.role_name = granted_role.rolname "
        f"WHERE granted_role.rolname IN ({expected_names}) "
        f"AND member_role.rolname NOT IN ({future_phase_names}) "
        "AND expected.member_name IS NULL) "
        "AND (SELECT count(*) FROM pg_namespace namespace "
        "JOIN pg_roles owner_role ON owner_role.oid = namespace.nspowner "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops', 'x_extension') "
        f"AND owner_role.rolname = '{_MAP_SCHEMA_OWNER}') = 4 "
        "AND NOT EXISTS (SELECT 1 FROM pg_class relation "
        "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
        "JOIN pg_roles owner_role ON owner_role.oid = relation.relowner "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops') "
        "AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
        f"AND owner_role.rolname <> '{_MAP_SCHEMA_OWNER}') "
        "AND NOT EXISTS (SELECT 1 FROM pg_proc procedure "
        "JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace "
        "JOIN pg_roles owner_role ON owner_role.oid = procedure.proowner "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops') "
        f"AND owner_role.rolname <> '{_MAP_SCHEMA_OWNER}') "
        # `public.alembic_version`은 위 세 schema 밖이라 sweep에서도, 이 assertion
        # 에서도 오래 비어 있었다. 실데이터를 덤프/복원한 DB에서는 이 테이블이 구
        # superuser 소유로 남고, ADR-090 경로(migrator LOGIN -> SET ROLE schema
        # owner)가 첫 `SELECT version_num`에서 42501로 죽는다 — 단 한 revision도
        # 적용되지 못한다. fresh DB에서는 테이블 자체가 없어 무증상이었다.
        # 존재하면 반드시 schema owner여야 한다(없는 것은 fresh DB의 정상 상태).
        "AND NOT EXISTS (SELECT 1 FROM pg_class relation "
        "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
        "JOIN pg_roles owner_role ON owner_role.oid = relation.relowner "
        "WHERE namespace.nspname = 'public' AND relation.relname = 'alembic_version' "
        f"AND owner_role.rolname <> '{_MAP_SCHEMA_OWNER}') "
        "AND NOT EXISTS (SELECT 1 FROM pg_type data_type "
        "JOIN pg_namespace namespace ON namespace.oid = data_type.typnamespace "
        "JOIN pg_roles owner_role ON owner_role.oid = data_type.typowner "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops') "
        "AND data_type.typtype IN ('b', 'c', 'd', 'e', 'r') "
        "AND data_type.typelem = 0 AND data_type.typrelid = 0 "
        f"AND owner_role.rolname <> '{_MAP_SCHEMA_OWNER}') "
        "AND (SELECT count(*) FROM pg_extension extension "
        "JOIN pg_namespace namespace ON namespace.oid = extension.extnamespace "
        "WHERE extension.extname IN ('postgis', 'pg_trgm', 'pgcrypto') "
        "AND namespace.nspname = 'x_extension') = 3 "
        "AND NOT EXISTS (SELECT 1 FROM pg_namespace namespace "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops') "
        "AND (NOT has_schema_privilege('ktm_feature_runtime', namespace.oid, 'USAGE') "
        "OR has_schema_privilege('ktm_feature_runtime', namespace.oid, 'CREATE'))) "
        "AND NOT EXISTS (SELECT 1 FROM pg_class relation "
        "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
        "CROSS JOIN LATERAL aclexplode(relation.relacl) privilege "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops') "
        "AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
        "AND (privilege.grantee = 0 OR privilege.grantee IN (SELECT oid FROM pg_roles "
        f"WHERE rolname IN ({runtime_principal_names})))) "
        "AND NOT EXISTS (SELECT 1 FROM pg_default_acl default_acl "
        "CROSS JOIN LATERAL aclexplode(default_acl.defaclacl) privilege "
        "WHERE default_acl.defaclrole = (SELECT oid FROM pg_roles "
        f"WHERE rolname = '{_MAP_SCHEMA_OWNER}') "
        "AND (privilege.grantee = 0 OR privilege.grantee IN (SELECT oid FROM pg_roles "
        f"WHERE rolname IN ({runtime_principal_names})))) "
        "THEN 'ok' ELSE 'invalid' END"
    )
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            runtime.database_name,
            "--command",
            query,
        ],
        label="Map principal bootstrap assertion",
    ).decode("ascii").strip()
    if output != "ok":
        raise DeploymentContractError("Map principal bootstrap assertion failed")


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


def _permitted_existing_owners(runtime: DatabaseRuntime) -> frozenset[str]:
    """Map bootstrap 뒤 ownership만 다음 destructive reset에서 추가로 수용한다."""

    if runtime.role == "map_application":
        return frozenset({runtime.owner_name, _MAP_SCHEMA_OWNER})
    return frozenset({runtime.owner_name, *runtime.additional_owner_names})


def _validate_runtime(runtime: DatabaseRuntime) -> None:
    if runtime.role not in _ROLE_CONFIG:
        raise DeploymentContractError("pinned runtime database role is invalid")
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
        raise DeploymentContractError(f"{label} failed")
    if not isinstance(completed.stdout, bytes):
        raise DeploymentContractError(f"{label} produced invalid output")
    return completed.stdout
