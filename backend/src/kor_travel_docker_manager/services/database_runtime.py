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
_ROLE_CONFIG: dict[DatabaseRole, tuple[str, str, str, str]] = {
    "map_application": (
        "KRTOUR_MAP_POSTGRES_DB",
        "kor_travel_map",
        "KRTOUR_MAP_POSTGRES_USER",
        "krtour_map",
    ),
    "map_dagster": (
        "KRTOUR_MAP_DAGSTER_POSTGRES_DB",
        "kor_travel_map_dagster",
        "KRTOUR_MAP_POSTGRES_USER",
        "krtour_map",
    ),
    "pinvi": (
        "PINVI_POSTGRES_DB",
        "pinvi",
        "PINVI_POSTGRES_USER",
        "pinvi",
    ),
}
_SCHEMA_REVISION_LOCATION: dict[DatabaseRole, tuple[str, str]] = {
    "map_application": ("public", "alembic_version"),
    "map_dagster": ("public", "alembic_version"),
    "pinvi": ("app", "alembic_version"),
}


@dataclass(frozen=True)
class DatabaseRuntime:
    """동결된 Compose 계약에서 얻은 하나의 PostgreSQL database identity."""

    role: DatabaseRole
    container_name: str
    database_name: str
    owner_name: str
    admin_name: str


def database_runtimes_from_frozen_contract(
    *,
    resolved: Mapping[str, object],
    environment: Mapping[str, str],
) -> tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime]:
    """동결된 resolved Compose와 env에서 v5의 canonical 세 DB를 유도한다."""

    services = resolved.get("services")
    postgres = services.get("kor-travel-geo-postgres") if isinstance(services, Mapping) else None
    container_name = postgres.get("container_name") if isinstance(postgres, Mapping) else None
    if not isinstance(container_name, str) or not _CONTAINER_NAME.fullmatch(container_name):
        raise DeploymentContractError("pinned runtime PostgreSQL container identity is invalid")
    postgres_environment = postgres.get("environment") if isinstance(postgres, Mapping) else None
    admin_name = (
        postgres_environment.get("POSTGRES_USER")
        if isinstance(postgres_environment, Mapping)
        else None
    )
    if not isinstance(admin_name, str) or not _DATABASE_IDENTIFIER.fullmatch(admin_name):
        raise DeploymentContractError("pinned runtime PostgreSQL admin role is invalid")

    runtimes: list[DatabaseRuntime] = []
    for role, (database_env, database_default, owner_env, owner_default) in _ROLE_CONFIG.items():
        database_name = environment.get(database_env, database_default)
        owner_name = environment.get(owner_env, owner_default)
        if not _DATABASE_IDENTIFIER.fullmatch(database_name):
            raise DeploymentContractError(f"{role} database name is invalid")
        if not _DATABASE_IDENTIFIER.fullmatch(owner_name):
            raise DeploymentContractError(f"{role} database owner is invalid")
        runtimes.append(
            DatabaseRuntime(
                role=role,
                container_name=container_name,
                database_name=database_name,
                owner_name=owner_name,
                admin_name=admin_name,
            )
        )
    return runtimes[0], runtimes[1], runtimes[2]


def recreate_empty_database(runtime: DatabaseRuntime) -> None:
    """계약상 owner가 맞는 하나의 DB만 파기 후 같은 owner로 다시 만든다."""

    _validate_runtime(runtime)
    existing_owner = _read_database_owner(runtime)
    if existing_owner is not None:
        if existing_owner != runtime.owner_name:
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
        recreate_empty_database(runtime)


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


def _validate_runtime(runtime: DatabaseRuntime) -> None:
    if runtime.role not in _ROLE_CONFIG:
        raise DeploymentContractError("pinned runtime database role is invalid")
    if not _CONTAINER_NAME.fullmatch(runtime.container_name):
        raise DeploymentContractError("pinned runtime database container is invalid")
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
