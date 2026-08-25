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
Application300BootstrapState = Literal[
    "absent",
    "virgin",
    "partial",
    "exact_complete",
    "foreign",
]

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
_MAP_BASELINE_300_MEMBERSHIPS = (
    ("ktm_curation_admin_executor", "ktm_feature_api_runtime", False, True, False),
    ("ktm_curation_audit_writer", _MAP_SCHEMA_OWNER, False, False, True),
    ("ktm_curation_command_owner", _MAP_SCHEMA_OWNER, False, False, True),
    (
        "ktm_curation_provider_executor",
        "ktm_feature_dagster_runtime",
        False,
        True,
        False,
    ),
    ("ktm_feature_audit_writer", _MAP_SCHEMA_OWNER, False, False, True),
    (
        "ktm_feature_create_provider_executor",
        "ktm_feature_dagster_runtime",
        False,
        True,
        False,
    ),
    (
        "ktm_feature_reference_reconciliation_service_executor",
        "ktm_feature_api_runtime",
        False,
        True,
        False,
    ),
    (
        "ktm_feature_request_admin_executor",
        "ktm_feature_api_runtime",
        False,
        True,
        False,
    ),
    ("ktm_feature_request_procedure_owner", _MAP_SCHEMA_OWNER, False, False, True),
    (
        "ktm_feature_request_service_executor",
        "ktm_feature_api_runtime",
        False,
        True,
        False,
    ),
    ("ktm_feature_runtime", "ktm_feature_api_runtime", False, True, False),
    ("ktm_feature_runtime", "ktm_feature_dagster_runtime", False, True, False),
    (_MAP_SCHEMA_OWNER, "ktm_feature_migrator", False, False, True),
    ("ktm_feature_state_procedure_owner", _MAP_SCHEMA_OWNER, False, False, True),
    (
        "ktm_manual_feature_admin_executor",
        "ktm_feature_api_runtime",
        False,
        True,
        False,
    ),
    ("ktm_manual_feature_procedure_owner", _MAP_SCHEMA_OWNER, False, False, True),
    (
        "ktm_manual_provider_dedup_admin_executor",
        "ktm_feature_api_runtime",
        False,
        True,
        False,
    ),
    (
        "ktm_manual_provider_dedup_detector_executor",
        "ktm_feature_dagster_runtime",
        False,
        True,
        False,
    ),
    (
        "ktm_manual_provider_dedup_procedure_owner",
        _MAP_SCHEMA_OWNER,
        False,
        False,
        True,
    ),
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


@dataclass(frozen=True)
class Application300DatabaseIdentity:
    """application 300 permit/fence에 쓰는 non-secret database identity."""

    database_name: str
    database_oid: int
    database_owner: str
    postgres_system_identifier: str


@dataclass(frozen=True)
class PinnedDatabaseIdentity:
    """committed generation에 보존하는 일반 DB/owner-login identity."""

    system_identifier: str
    name: str
    oid: int
    owner: str
    login_role: str


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


def reset_databases_for_application_300(
    runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
) -> None:
    """Map 두 DB는 제거하고 PinVi DB만 즉시 다시 만든다.

    application-300은 application DB를 ``template0``에서 별도 생성하고 metadata
    DB도 격리된 identity producer가 만든다. 따라서 generic drop/create가 두 Map
    DB를 미리 만들면 virgin-root 및 sealed metadata permit 계약을 우회한다.
    """

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


def inspect_application_300_bootstrap_state(
    runtime: DatabaseRuntime,
) -> Application300BootstrapState:
    """fresh application DB를 부재·virgin·exact bootstrap·그 외로 분류한다.

    ``createdb``와 role bootstrap은 서로 다른 process 경계다. 전자는 응답 유실 뒤
    stock ``template0`` DB를 남길 수 있고, 후자는 upstream의
    ``--single-transaction`` 때문에 정상 crash 결과가 virgin 또는 exact-complete
    둘 중 하나여야 한다. 허용 owner이면서 어느 정본에도 맞지 않는 상태는 partial,
    owner부터 다른 상태는 foreign으로 분리해 호출자가 fail-close할 수 있게 한다.
    """

    _validate_runtime(runtime)
    if runtime.role != "map_application":
        raise DeploymentContractError(
            "application 300 bootstrap state requires Map application DB"
        )
    if runtime.owner_name == _MAP_SCHEMA_OWNER:
        raise DeploymentContractError("application 300 bootstrap owners must be distinct")
    owner = _read_database_owner(runtime)
    if owner is None:
        return "absent"
    if owner == runtime.owner_name:
        return (
            "virgin"
            if _read_application_300_state_attestation(
                runtime,
                query=_application_300_virgin_attestation_query(runtime),
                expected="virgin",
                label="Map application 300 virgin database attestation",
            )
            else "partial"
        )
    if owner == _MAP_SCHEMA_OWNER:
        return (
            "exact_complete"
            if _read_application_300_state_attestation(
                runtime,
                query=_application_300_bootstrap_attestation_query(runtime),
                expected="exact_complete",
                label="Map application 300 role bootstrap attestation",
            )
            else "partial"
        )
    return "foreign"


def _read_application_300_state_attestation(
    runtime: DatabaseRuntime,
    *,
    query: str,
    expected: str,
    label: str,
) -> bool:
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
        label=label,
    ).decode("ascii").strip()
    if output == expected:
        return True
    if output == "partial":
        return False
    raise DeploymentContractError("Map application 300 state attestation is ambiguous")


def _application_300_virgin_attestation_query(runtime: DatabaseRuntime) -> str:
    return (
        "SELECT CASE WHEN "
        "current_database() = "
        f"'{runtime.database_name}' "
        "AND (SELECT pg_get_userbyid(datdba) FROM pg_catalog.pg_database "
        f"WHERE datname = '{runtime.database_name}') = '{runtime.owner_name}' "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace AS namespace "
        "WHERE namespace.nspname !~ '^pg_' "
        "AND namespace.nspname NOT IN ('information_schema', 'public')) "
        "AND EXISTS (SELECT 1 FROM pg_catalog.pg_namespace AS namespace "
        "WHERE namespace.nspname = 'public' "
        "AND namespace.nspowner = 'pg_database_owner'::regrole "
        "AND (SELECT COALESCE(array_agg(entry::text ORDER BY entry::text), "
        "ARRAY[]::text[]) FROM unnest(namespace.nspacl) AS entry) "
        "IS NOT DISTINCT FROM ARRAY['=U/pg_database_owner', "
        "'pg_database_owner=UC/pg_database_owner']::text[]) "
        "AND (SELECT count(*) FROM pg_catalog.pg_extension) = 1 "
        "AND EXISTS (SELECT 1 FROM pg_catalog.pg_extension AS extension "
        "JOIN pg_catalog.pg_namespace AS namespace "
        "ON namespace.oid = extension.extnamespace "
        "WHERE extension.extname = 'plpgsql' "
        "AND namespace.nspname = 'pg_catalog') "
        "AND (SELECT COALESCE(array_agg(language.lanname::text "
        "ORDER BY language.lanname), ARRAY[]::text[]) "
        "FROM pg_catalog.pg_language AS language) "
        "IS NOT DISTINCT FROM ARRAY['c', 'internal', 'plpgsql', 'sql']::text[] "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_default_acl) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_db_role_setting AS setting_row "
        "WHERE setting_row.setdatabase = (SELECT oid FROM pg_catalog.pg_database "
        f"WHERE datname = '{runtime.database_name}')) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class AS object "
        "JOIN pg_catalog.pg_namespace AS namespace "
        "ON namespace.oid = object.relnamespace "
        "WHERE namespace.nspname = 'public' "
        "UNION ALL SELECT 1 FROM pg_catalog.pg_proc AS object "
        "JOIN pg_catalog.pg_namespace AS namespace "
        "ON namespace.oid = object.pronamespace "
        "WHERE namespace.nspname = 'public' "
        "UNION ALL SELECT 1 FROM pg_catalog.pg_type AS object "
        "JOIN pg_catalog.pg_namespace AS namespace "
        "ON namespace.oid = object.typnamespace "
        "WHERE namespace.nspname = 'public') "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_foreign_server) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_user_mapping) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_publication) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_subscription AS subscription "
        "WHERE subscription.subdbid = (SELECT oid FROM pg_catalog.pg_database "
        f"WHERE datname = '{runtime.database_name}')) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger) "
        "THEN 'virgin' ELSE 'partial' END"
    )


def _application_300_bootstrap_attestation_query(runtime: DatabaseRuntime) -> str:
    group_roles = (*_MAP_REQUIRED_GROUP_ROLES, *_MAP_FUTURE_PHASE_ROLES)
    login_roles = _MAP_REQUIRED_LOGIN_ROLES
    all_roles = (*group_roles, *login_roles)
    expected_role_values = ", ".join(
        f"('{role}', {'TRUE' if role in login_roles else 'FALSE'})" for role in all_roles
    )
    expected_membership_values = ", ".join(
        "(" + ", ".join(
            (
                f"'{granted}'",
                f"'{member}'",
                "TRUE" if admin_option else "FALSE",
                "TRUE" if inherit_option else "FALSE",
                "TRUE" if set_option else "FALSE",
            )
        ) + ")"
        for granted, member, admin_option, inherit_option, set_option in (
            _MAP_BASELINE_300_MEMBERSHIPS
        )
    )
    application_acl_roles = (
        "ktm_feature_state_procedure_owner",
        "ktm_feature_audit_writer",
        "ktm_curation_command_owner",
        "ktm_curation_audit_writer",
        "ktm_manual_feature_procedure_owner",
        "ktm_feature_request_procedure_owner",
        "ktm_manual_provider_dedup_procedure_owner",
    )
    extension_acl_roles = (
        _MAP_SCHEMA_OWNER,
        "ktm_feature_state_procedure_owner",
        "ktm_feature_runtime",
        "ktm_feature_api_runtime",
        "ktm_feature_dagster_runtime",
        "ktm_curation_command_owner",
        "ktm_manual_provider_dedup_procedure_owner",
    )
    expected_acl_values = ", ".join(
        [
            f"('{schema}', '{role}', '{privilege}', FALSE)"
            for schema in ("feature", "provider_sync", "ops")
            for role in application_acl_roles
            for privilege in ("USAGE", "CREATE")
        ]
        + [
            f"('x_extension', '{role}', 'USAGE', FALSE)"
            for role in extension_acl_roles
        ]
        # PostgreSQL retains CREATE for the owner of a schema.  The Map
        # bootstrap script intentionally leaves that owner privilege in place
        # while revoking PUBLIC and the other application roles.
        + [f"('x_extension', '{_MAP_SCHEMA_OWNER}', 'CREATE', FALSE)"]
    )
    role_names = ", ".join(f"'{role}'" for role in all_roles)
    return (
        "WITH expected_role(rolname, can_login) AS (VALUES "
        f"{expected_role_values}), "
        "expected_membership(granted_role, member_role, admin_option, inherit_option, "
        "set_option) "
        f"AS (VALUES {expected_membership_values}), "
        "actual_membership AS (SELECT granted.rolname AS granted_role, "
        "member.rolname AS member_role, membership.admin_option, membership.inherit_option, "
        "membership.set_option FROM pg_catalog.pg_auth_members AS membership "
        "JOIN pg_catalog.pg_roles AS granted ON granted.oid = membership.roleid "
        "JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member "
        f"WHERE granted.rolname IN ({role_names}) OR member.rolname IN ({role_names})), "
        "expected_acl(schema_name, role_name, privilege_type, is_grantable) AS (VALUES "
        f"{expected_acl_values}), actual_acl AS (SELECT namespace.nspname AS schema_name, "
        "role.rolname AS role_name, privilege.privilege_type, privilege.is_grantable "
        "FROM pg_catalog.pg_namespace AS namespace "
        "CROSS JOIN LATERAL aclexplode(namespace.nspacl) AS privilege "
        "JOIN pg_catalog.pg_roles AS role ON role.oid = privilege.grantee "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops', 'x_extension')) "
        "SELECT CASE WHEN current_database() = "
        f"'{runtime.database_name}' "
        "AND (SELECT pg_get_userbyid(datdba) FROM pg_catalog.pg_database "
        f"WHERE datname = '{runtime.database_name}') = '{_MAP_SCHEMA_OWNER}' "
        "AND NOT EXISTS (SELECT 1 FROM expected_role LEFT JOIN pg_catalog.pg_roles "
        "USING (rolname) WHERE pg_roles.rolname IS NULL "
        "OR pg_roles.rolcanlogin IS DISTINCT FROM expected_role.can_login "
        "OR pg_roles.rolinherit OR pg_roles.rolsuper OR pg_roles.rolcreatedb "
        "OR pg_roles.rolcreaterole OR pg_roles.rolbypassrls OR pg_roles.rolreplication) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles AS role "
        "WHERE role.rolname LIKE 'ktm\\_%' ESCAPE '\\' "
        f"AND role.rolname NOT IN ({role_names})) "
        "AND NOT EXISTS (SELECT * FROM expected_membership EXCEPT "
        "SELECT * FROM actual_membership) "
        "AND NOT EXISTS (SELECT * FROM actual_membership EXCEPT "
        "SELECT * FROM expected_membership) "
        "AND (SELECT count(*) FROM pg_catalog.pg_namespace AS namespace "
        "WHERE namespace.nspname !~ '^pg_' "
        "AND namespace.nspname <> 'information_schema') = 5 "
        "AND (SELECT count(*) FROM pg_catalog.pg_namespace AS namespace "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops', 'x_extension') "
        f"AND pg_get_userbyid(namespace.nspowner) = '{_MAP_SCHEMA_OWNER}') = 4 "
        "AND NOT EXISTS (SELECT * FROM expected_acl EXCEPT SELECT * FROM actual_acl) "
        "AND NOT EXISTS (SELECT * FROM actual_acl EXCEPT SELECT * FROM expected_acl) "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace AS namespace "
        "CROSS JOIN LATERAL aclexplode(namespace.nspacl) AS privilege "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops', 'x_extension') "
        "AND privilege.grantee = 0) "
        "AND (SELECT COALESCE(array_agg(extension.extname || ':' || namespace.nspname "
        "ORDER BY extension.extname), ARRAY[]::text[]) "
        "FROM pg_catalog.pg_extension AS extension "
        "JOIN pg_catalog.pg_namespace AS namespace "
        "ON namespace.oid = extension.extnamespace) IS NOT DISTINCT FROM "
        "ARRAY['fuzzystrmatch:public', 'pg_prewarm:x_extension', "
        "'pg_trgm:x_extension', 'pgcrypto:x_extension', 'plpgsql:pg_catalog', "
        "'postgis:x_extension']::text[] "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class AS object "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = object.relnamespace "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops') "
        "UNION ALL SELECT 1 FROM pg_catalog.pg_proc AS object "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = object.pronamespace "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops') "
        "UNION ALL SELECT 1 FROM pg_catalog.pg_type AS object "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = object.typnamespace "
        "WHERE namespace.nspname IN ('feature', 'provider_sync', 'ops') "
        "AND object.typtype IN ('b', 'c', 'd', 'e', 'r')) "
        "AND to_regclass('public.alembic_version') IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_default_acl) "
        "AND (SELECT count(*) FROM pg_catalog.pg_db_role_setting AS setting_row "
        "WHERE setting_row.setdatabase = (SELECT oid FROM pg_catalog.pg_database "
        f"WHERE datname = '{runtime.database_name}')) = 1 "
        "AND EXISTS (SELECT 1 FROM pg_catalog.pg_db_role_setting AS setting_row "
        "WHERE setting_row.setdatabase = (SELECT oid FROM pg_catalog.pg_database "
        f"WHERE datname = '{runtime.database_name}') AND setting_row.setrole = 0 "
        "AND setting_row.setconfig = ARRAY['search_path=public, x_extension']::text[]) "
        "THEN 'exact_complete' ELSE 'partial' END"
    )


def read_application_300_database_identity(
    runtime: DatabaseRuntime,
) -> Application300DatabaseIdentity:
    """maintenance DB에서 Map application DB identity를 읽기 전용으로 조회한다."""

    _validate_runtime(runtime)
    if runtime.role != "map_application":
        raise DeploymentContractError("application 300 identity requires Map application DB")
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
                "SELECT datname, oid::bigint, pg_get_userbyid(datdba), "
                "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) "
                "FROM pg_catalog.pg_database "
                f"WHERE datname = '{runtime.database_name}'"
            ),
        ],
        label="Map application 300 database identity",
    )
    return _parse_application_database_identity(output, runtime=runtime)


def read_pinned_database_identity(runtime: DatabaseRuntime) -> PinnedDatabaseIdentity:
    """maintenance DB에서 일반 pinned DB와 owner-login identity를 strict 조회한다."""

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
                "SELECT control.system_identifier::text, database_row.datname, "
                "database_row.oid::bigint, pg_get_userbyid(database_row.datdba), "
                "owner_role.rolname "
                "FROM pg_catalog.pg_database AS database_row "
                "JOIN pg_catalog.pg_roles AS owner_role "
                "ON owner_role.oid = database_row.datdba "
                "CROSS JOIN pg_catalog.pg_control_system() AS control "
                f"WHERE database_row.datname = '{runtime.database_name}' "
                f"AND owner_role.rolname = '{runtime.owner_name}'"
            ),
        ],
        label=f"{runtime.role} database identity",
    )
    text = output.decode("ascii").strip()
    lines = text.splitlines()
    if len(lines) != 1:
        raise DeploymentContractError(f"{runtime.role} database identity is invalid")
    fields = lines[0].split("|")
    if len(fields) != 5:
        raise DeploymentContractError(f"{runtime.role} database identity is invalid")
    system_identifier, name, oid_raw, owner, login_role = fields
    if (
        name != runtime.database_name
        or owner != runtime.owner_name
        or login_role != runtime.owner_name
    ):
        raise DeploymentContractError(f"{runtime.role} database identity binding is invalid")
    return PinnedDatabaseIdentity(
        system_identifier=_parse_system_identifier(
            system_identifier,
            f"{runtime.role} PostgreSQL system identifier",
        ),
        name=name,
        oid=_parse_positive_int(oid_raw, f"{runtime.role} database oid"),
        owner=owner,
        login_role=login_role,
    )


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
        "JOIN pg_roles member_role ON member_role.oid = membership.member "
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
                "WHERE membership.roleid = role.oid) "
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
    if len(fields) != 13:
        raise DeploymentContractError("Map Dagster metadata role output is invalid")
    return _DagsterMetadataRolePreflight(
        can_login=_parse_psql_bool(fields[0], "Map Dagster metadata role login"),
        inherit=_parse_psql_bool(fields[1], "Map Dagster metadata role inherit"),
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


def _parse_application_database_identity(
    output: bytes,
    *,
    runtime: DatabaseRuntime,
) -> Application300DatabaseIdentity:
    text = output.decode("ascii").strip()
    lines = text.splitlines()
    if len(lines) != 1:
        raise DeploymentContractError("Map application 300 database identity is invalid")
    fields = lines[0].split("|")
    if len(fields) != 4:
        raise DeploymentContractError("Map application 300 database identity is invalid")
    name, oid_raw, owner, system_identifier = fields
    if name != runtime.database_name:
        raise DeploymentContractError("Map application 300 database name is invalid")
    if not _DATABASE_IDENTIFIER.fullmatch(owner):
        raise DeploymentContractError("Map application 300 database owner is invalid")
    return Application300DatabaseIdentity(
        database_name=name,
        database_oid=_parse_positive_int(
            oid_raw, "Map application 300 database oid"
        ),
        database_owner=owner,
        postgres_system_identifier=_parse_system_identifier(
            system_identifier,
            "Map application 300 PostgreSQL system identifier",
        ),
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
        raise DeploymentContractError(f"{label} failed")
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
        raise DeploymentContractError(f"{label} failed")
    if not isinstance(completed.stdout, bytes):
        raise DeploymentContractError(f"{label} produced invalid output")
    return completed.stdout
