from __future__ import annotations

from unittest.mock import Mock

import pytest

import kor_travel_docker_manager.services.database_runtime as database_runtime
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.database_runtime import (
    DatabaseRuntime,
    database_runtimes_from_frozen_contract,
    read_database_schema_revision,
    recreate_empty_database,
    recreate_empty_databases,
)


def _runtime(role: database_runtime.DatabaseRole) -> DatabaseRuntime:
    return DatabaseRuntime(
        role=role,
        container_name="postgres-rehearsal",
        database_name={
            "map_application": "map_app",
            "map_dagster": "map_dagster",
            "pinvi": "pin_app",
        }[role],
        owner_name="pin_owner" if role == "pinvi" else "map_owner",
        admin_name="cluster_admin",
    )


def test_database_runtime_identity_comes_from_frozen_contract() -> None:
    runtimes = database_runtimes_from_frozen_contract(
        resolved={
            "services": {
                "kor-travel-geo-postgres": {
                    "container_name": "postgres-production",
                    "environment": {"POSTGRES_USER": "cluster_admin"},
                }
            }
        },
        environment={
            "KRTOUR_MAP_POSTGRES_DB": "map_app",
            "KRTOUR_MAP_DAGSTER_POSTGRES_DB": "map_dagster",
            "KRTOUR_MAP_POSTGRES_USER": "map_owner",
            "PINVI_POSTGRES_DB": "pin_app",
            "PINVI_POSTGRES_USER": "pin_owner",
        },
    )

    assert [
        (runtime.role, runtime.database_name, runtime.owner_name, runtime.admin_name)
        for runtime in runtimes
    ] == [
        ("map_application", "map_app", "map_owner", "cluster_admin"),
        ("map_dagster", "map_dagster", "map_owner", "cluster_admin"),
        ("pinvi", "pin_app", "pin_owner", "cluster_admin"),
    ]
    assert {runtime.container_name for runtime in runtimes} == {"postgres-production"}


@pytest.mark.parametrize(
    "postgres_environment",
    [{}, {"POSTGRES_USER": ""}, {"POSTGRES_USER": "cluster-admin"}],
)
def test_database_runtime_rejects_invalid_frozen_admin_role(
    postgres_environment: object,
) -> None:
    with pytest.raises(DeploymentContractError, match="admin role"):
        database_runtimes_from_frozen_contract(
            resolved={
                "services": {
                    "kor-travel-geo-postgres": {
                        "container_name": "postgres-production",
                        "environment": postgres_environment,
                    }
                }
            },
            environment={},
        )


def test_recreate_empty_databases_uses_only_canonical_frozen_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str]] = []

    def run_checked(arguments: list[str], *, label: str) -> bytes:
        calls.append((arguments, label))
        return b""

    runtimes = (_runtime("map_application"), _runtime("map_dagster"), _runtime("pinvi"))
    monkeypatch.setattr(
        database_runtime,
        "_read_database_owner",
        Mock(side_effect=lambda runtime: runtime.owner_name),
    )
    monkeypatch.setattr(database_runtime, "_run_checked", Mock(side_effect=run_checked))

    recreate_empty_databases(runtimes)

    assert [label for _, label in calls] == [
        "map_application database destructive drop",
        "map_application database destructive create",
        "map_dagster database destructive drop",
        "map_dagster database destructive create",
        "pinvi database destructive drop",
        "pinvi database destructive create",
    ]
    assert ["dropdb" if "dropdb" in arguments else "createdb" for arguments, _ in calls] == [
        "dropdb",
        "createdb",
        "dropdb",
        "createdb",
        "dropdb",
        "createdb",
    ]
    assert all(arguments[arguments.index("--user") + 1] == "postgres" for arguments, _ in calls)
    assert all("password" not in " ".join(arguments).lower() for arguments, _ in calls)


def test_recreate_empty_database_refuses_foreign_owned_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock()
    monkeypatch.setattr(database_runtime, "_read_database_owner", Mock(return_value="foreign"))
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    with pytest.raises(DeploymentContractError, match="owner differs"):
        recreate_empty_database(_runtime("pinvi"))

    runner.assert_not_called()


def test_recreate_empty_databases_requires_three_canonical_roles() -> None:
    runtime = _runtime("pinvi")

    with pytest.raises(DeploymentContractError, match="database roles"):
        recreate_empty_databases((runtime, runtime, runtime))


@pytest.mark.parametrize(
    ("role", "canonical_table"),
    [
        ("map_application", '"public"."alembic_version"'),
        ("map_dagster", '"public"."alembic_version"'),
        ("pinvi", '"app"."alembic_version"'),
    ],
)
def test_schema_revision_uses_role_canonical_table(
    role: database_runtime.DatabaseRole,
    canonical_table: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_query = f"SELECT version_num FROM {canonical_table}"

    def run_checked(arguments: list[str], *, label: str) -> bytes:
        del label
        return b"canonical_revision\n" if arguments[-1] == expected_query else b"poison\n"

    runner = Mock(side_effect=run_checked)
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    assert read_database_schema_revision(_runtime(role)) == "canonical_revision"
    command = runner.call_args.args[0]
    assert command[-1] == expected_query
    assert "FROM alembic_version" not in command[-1]


def test_schema_revision_rejects_ambiguous_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        database_runtime,
        "_run_checked",
        Mock(return_value=b"canonical_revision\npoison_same_name_revision\n"),
    )

    with pytest.raises(DeploymentContractError, match="revision output"):
        read_database_schema_revision(_runtime("map_application"))
