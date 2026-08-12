from __future__ import annotations

from unittest.mock import Mock

import kor_travel_docker_manager.services.database_runtime as database_runtime
import pytest
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.database_runtime import (
    DatabaseRuntime,
    assert_map_database_principal_bootstrap,
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
                },
                "kor-travel-map-postgres": {
                    "container_name": "map-postgres-production",
                    "environment": {"POSTGRES_USER": "map_cluster_admin"},
                }
            }
        },
        environment={
            "KOR_TRAVEL_MAP_POSTGRES_DB": "map_app",
            "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB": "map_dagster",
            "KOR_TRAVEL_MAP_POSTGRES_USER": "map_owner",
            "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER": "map_dagster_metadata",
            "PINVI_POSTGRES_DB": "pin_app",
            "PINVI_POSTGRES_USER": "pin_owner",
        },
    )

    assert [
        (
            runtime.role,
            runtime.container_name,
            runtime.database_name,
            runtime.owner_name,
            runtime.admin_name,
        )
        for runtime in runtimes
    ] == [
        ("map_application", "map-postgres-production", "map_app", "map_owner", "map_cluster_admin"),
        ("map_dagster", "map-postgres-production", "map_dagster", "map_owner", "map_cluster_admin"),
        ("pinvi", "postgres-production", "pin_app", "pin_owner", "cluster_admin"),
    ]
    assert {runtime.container_name for runtime in runtimes} == {
        "map-postgres-production",
        "postgres-production",
    }
    assert runtimes[1].additional_owner_names == frozenset({"map_dagster_metadata"})


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
                    },
                    "kor-travel-map-postgres": {
                        "container_name": "map-postgres-production",
                        "environment": {"POSTGRES_USER": "map_cluster_admin"},
                    },
                }
            },
            environment={"KOR_TRAVEL_MAP_DAGSTER_METADATA_USER": "map_dagster_metadata"},
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
    assert [
        next(command for command in ("dropdb", "createdb", "psql") if command in arguments)
        for arguments, _ in calls
    ] == [
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


def test_recreate_empty_map_database_accepts_bootstrap_schema_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(return_value=b"")
    monkeypatch.setattr(
        database_runtime,
        "_read_database_owner",
        Mock(return_value="ktm_feature_schema_owner"),
    )
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    recreate_empty_database(_runtime("map_application"))

    assert [call.kwargs["label"] for call in runner.call_args_list] == [
        "map_application database destructive drop",
        "map_application database destructive create",
    ]


def test_map_principal_bootstrap_assertion_requires_exact_catalog_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(return_value=b"ok\n")
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    assert_map_database_principal_bootstrap(
        _runtime("map_application"),
        _runtime("map_dagster"),
        "map_dagster_metadata",
    )

    command = runner.call_args.args[0]
    assert command[command.index("--dbname") + 1] == "map_app"
    assert "ktm_feature_schema_owner" in command[-1]
    assert "ktm_feature_api_runtime" in command[-1]
    assert "pg_auth_members" in command[-1]
    assert "pg_default_acl" in command[-1]
    assert "map_dagster_metadata" in command[-1]
    assert "granted_role.rolname" in command[-1]
    assert "privilege.grantee = 0" in command[-1]


def test_map_principal_bootstrap_assertion_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(database_runtime, "_run_checked", Mock(return_value=b"invalid\n"))

    with pytest.raises(DeploymentContractError, match="bootstrap assertion failed"):
        assert_map_database_principal_bootstrap(
            _runtime("map_application"),
            _runtime("map_dagster"),
            "map_dagster_metadata",
        )


def test_map_principal_bootstrap_assertion_rejects_metadata_role_collision() -> None:
    with pytest.raises(DeploymentContractError, match="metadata role is invalid"):
        assert_map_database_principal_bootstrap(
            _runtime("map_application"),
            _runtime("map_dagster"),
            "ktm_feature_runtime",
        )


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
