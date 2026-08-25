from __future__ import annotations

from unittest.mock import Mock

import pytest

import kor_travel_docker_manager.services.database_runtime as database_runtime
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.database_runtime import (
    DagsterMetadataRoleAttributes,
    DatabaseRuntime,
    assert_map_database_principal_bootstrap,
    create_fresh_application_300_database,
    database_runtimes_from_frozen_contract,
    initialize_application_300_dagster_metadata_database,
    inspect_application_300_bootstrap_state,
    read_application_300_dagster_metadata_identity,
    read_application_300_database_identity,
    read_database_schema_revision,
    recreate_empty_database,
    recreate_empty_databases,
    reset_databases_for_application_300,
)


def _runtime(role: database_runtime.DatabaseRole) -> DatabaseRuntime:
    return DatabaseRuntime(
        role=role,
        container_name="postgres-rehearsal",
        port=12800 if role == "pinvi" else 12700,
        database_name={
            "map_application": "map_app",
            "map_dagster": "map_dagster",
            "pinvi": "pin_app",
        }[role],
        owner_name="pin_owner" if role == "pinvi" else "map_owner",
        admin_name="cluster_admin",
    )


def _metadata_runtime() -> DatabaseRuntime:
    return DatabaseRuntime(
        role="map_dagster",
        container_name="postgres-rehearsal",
        port=12700,
        database_name="map_dagster",
        owner_name="map_owner",
        admin_name="cluster_admin",
        additional_owner_names=frozenset({"map_dagster_metadata"}),
    )


def test_database_runtime_identity_comes_from_frozen_contract() -> None:
    runtimes = database_runtimes_from_frozen_contract(
        resolved={
            "services": {
                "kor-travel-geo-postgres": {
                    "container_name": "geo-postgres-production",
                    "environment": {"POSTGRES_USER": "cluster_admin"},
                },
                "kor-travel-map-postgres": {
                    "container_name": "map-postgres-production",
                    "environment": {"POSTGRES_USER": "map_cluster_admin"},
                },
                "pinvi-postgres": {
                    "container_name": "pinvi-postgres-production",
                    "environment": {"POSTGRES_USER": "pin_cluster_admin"},
                },
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
            runtime.port,
            runtime.database_name,
            runtime.owner_name,
            runtime.admin_name,
        )
        for runtime in runtimes
    ] == [
        ("map_application", "map-postgres-production", 12700, "map_app", "map_owner", "map_cluster_admin"),
        ("map_dagster", "map-postgres-production", 12700, "map_dagster", "map_owner", "map_cluster_admin"),
        ("pinvi", "pinvi-postgres-production", 12800, "pin_app", "pin_owner", "pin_cluster_admin"),
    ]
    assert {runtime.container_name for runtime in runtimes} == {
        "map-postgres-production",
        "pinvi-postgres-production",
    }
    assert runtimes[1].additional_owner_names == frozenset({"map_dagster_metadata"})


def test_database_runtime_rejects_pinvi_container_alias() -> None:
    with pytest.raises(DeploymentContractError, match="distinct frozen PostgreSQL container"):
        database_runtimes_from_frozen_contract(
            resolved={
                "services": {
                    "kor-travel-map-postgres": {
                        "container_name": "map-postgres-production",
                        "environment": {"POSTGRES_USER": "map_cluster_admin"},
                    },
                    "pinvi-postgres": {
                        "container_name": "map-postgres-production",
                        "environment": {"POSTGRES_USER": "pin_cluster_admin"},
                    },
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


def test_database_runtime_rejects_database_name_alias() -> None:
    with pytest.raises(DeploymentContractError, match="distinct frozen database names"):
        database_runtimes_from_frozen_contract(
            resolved={
                "services": {
                    "kor-travel-map-postgres": {
                        "container_name": "map-postgres-production",
                        "environment": {"POSTGRES_USER": "map_cluster_admin"},
                    },
                    "pinvi-postgres": {
                        "container_name": "pinvi-postgres-production",
                        "environment": {"POSTGRES_USER": "pin_cluster_admin"},
                    },
                }
            },
            environment={
                "KOR_TRAVEL_MAP_POSTGRES_DB": "map_app",
                "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB": "map_dagster",
                "KOR_TRAVEL_MAP_POSTGRES_USER": "map_owner",
                "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER": "map_dagster_metadata",
                "PINVI_POSTGRES_DB": "map_app",
                "PINVI_POSTGRES_USER": "pin_owner",
            },
        )


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
                        "environment": {"POSTGRES_USER": "geo_admin"},
                    },
                    "kor-travel-map-postgres": {
                        "container_name": "map-postgres-production",
                        "environment": {"POSTGRES_USER": "map_cluster_admin"},
                    },
                    "pinvi-postgres": {
                        "container_name": "pinvi-postgres-production",
                        "environment": postgres_environment,
                    },
                }
            },
            environment={"KOR_TRAVEL_MAP_DAGSTER_METADATA_USER": "map_dagster_metadata"},
        )


@pytest.mark.parametrize(
    "port_value",
    ["", "not-a-port", "0", "65536"],
)
def test_database_runtime_rejects_invalid_frozen_port(port_value: str) -> None:
    with pytest.raises(DeploymentContractError, match="PostgreSQL port is invalid"):
        database_runtimes_from_frozen_contract(
            resolved={
                "services": {
                    "kor-travel-map-postgres": {
                        "container_name": "map-postgres-production",
                        "environment": {"POSTGRES_USER": "map_cluster_admin"},
                    },
                    "pinvi-postgres": {
                        "container_name": "pinvi-postgres-production",
                        "environment": {"POSTGRES_USER": "pin_cluster_admin"},
                    },
                }
            },
            environment={
                "KOR_TRAVEL_MAP_POSTGRES_DB": "map_app",
                "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB": "map_dagster",
                "KOR_TRAVEL_MAP_POSTGRES_USER": "map_owner",
                "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER": "map_dagster_metadata",
                "PINVI_POSTGRES_DB": "pin_app",
                "PINVI_POSTGRES_USER": "pin_owner",
                "PINVI_DB_PORT": port_value,
            },
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
    assert all("--port" in arguments for arguments, _ in calls)
    assert [
        arguments[arguments.index("--port") + 1]
        for arguments, _ in calls
    ] == ["12700", "12700", "12700", "12700", "12800", "12800"]
    assert all("password" not in " ".join(arguments).lower() for arguments, _ in calls)


def test_recreate_empty_databases_preflights_all_owners_before_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtimes = (_runtime("map_application"), _runtime("map_dagster"), _runtime("pinvi"))
    owner_probes: list[str] = []
    runner = Mock()

    def read_owner(runtime: DatabaseRuntime) -> str:
        owner_probes.append(runtime.role)
        if runtime.role == "map_dagster":
            raise DeploymentContractError("owner probe failed")
        return runtime.owner_name

    monkeypatch.setattr(database_runtime, "_read_database_owner", read_owner)
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    with pytest.raises(DeploymentContractError, match="owner probe failed"):
        recreate_empty_databases(runtimes)

    assert owner_probes == ["map_application", "map_dagster"]
    runner.assert_not_called()


def test_application_300_reset_leaves_map_databases_absent_and_recreates_pinvi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str]] = []
    runtimes = (_runtime("map_application"), _runtime("map_dagster"), _runtime("pinvi"))
    monkeypatch.setattr(
        database_runtime,
        "_read_database_owner",
        Mock(side_effect=lambda runtime: runtime.owner_name),
    )

    def run_checked(arguments: list[str], *, label: str) -> bytes:
        calls.append((arguments, label))
        return b""

    monkeypatch.setattr(
        database_runtime,
        "_run_checked",
        Mock(side_effect=run_checked),
    )

    reset_databases_for_application_300(runtimes)

    assert [label for _, label in calls] == [
        "map_application database destructive drop",
        "map_dagster database destructive drop",
        "pinvi database destructive drop",
        "pinvi database destructive create",
    ]
    assert sum("createdb" in arguments for arguments, _ in calls) == 1
    assert "createdb" in calls[-1][0]


def test_fresh_application_300_database_requires_absence_and_template0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(return_value=b"")
    monkeypatch.setattr(database_runtime, "_read_database_owner", Mock(return_value=None))
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    create_fresh_application_300_database(_runtime("map_application"))

    arguments = runner.call_args.args[0]
    assert "createdb" in arguments
    assert arguments[arguments.index("--template") + 1] == "template0"
    assert arguments[arguments.index("--owner") + 1] == "map_owner"
    assert runner.call_args.kwargs["label"] == "map_application fresh 300 database create"


def test_fresh_application_300_database_refuses_existing_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock()
    monkeypatch.setattr(
        database_runtime,
        "_read_database_owner",
        Mock(return_value="map_owner"),
    )
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    with pytest.raises(DeploymentContractError, match="already exists"):
        create_fresh_application_300_database(_runtime("map_application"))

    runner.assert_not_called()


@pytest.mark.parametrize(
    ("owner", "attestation", "expected"),
    (
        (None, None, "absent"),
        ("map_owner", b"virgin\n", "virgin"),
        ("map_owner", b"partial\n", "partial"),
        ("ktm_feature_schema_owner", b"exact_complete\n", "exact_complete"),
        ("ktm_feature_schema_owner", b"partial\n", "partial"),
        ("foreign_owner", None, "foreign"),
    ),
)
def test_application_300_bootstrap_state_is_exactly_classified(
    owner: str | None,
    attestation: bytes | None,
    expected: database_runtime.Application300BootstrapState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(return_value=attestation)
    monkeypatch.setattr(database_runtime, "_read_database_owner", Mock(return_value=owner))
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    assert inspect_application_300_bootstrap_state(_runtime("map_application")) == expected

    if attestation is None:
        runner.assert_not_called()
    else:
        command = runner.call_args.args[0]
        assert command[command.index("--dbname") + 1] == "map_app"
        assert "THEN" in command[-1]


def test_application_300_exact_bootstrap_attestation_binds_full_role_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        database_runtime,
        "_read_database_owner",
        Mock(return_value="ktm_feature_schema_owner"),
    )
    runner = Mock(return_value=b"exact_complete\n")
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    assert (
        inspect_application_300_bootstrap_state(_runtime("map_application"))
        == "exact_complete"
    )

    query = runner.call_args.args[0][-1]
    assert "expected_membership" in query
    assert "actual_membership" in query
    assert "ktm_feature_reference_reconciliation_service_executor" in query
    assert "pg_prewarm:x_extension" in query
    assert "fuzzystrmatch:public" in query
    assert "search_path=public, x_extension" in query


def test_application_300_bootstrap_attestation_rejects_ambiguous_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        database_runtime,
        "_read_database_owner",
        Mock(return_value="map_owner"),
    )
    monkeypatch.setattr(database_runtime, "_run_checked", Mock(return_value=b"virgin\nextra\n"))

    with pytest.raises(DeploymentContractError, match="attestation is ambiguous"):
        inspect_application_300_bootstrap_state(_runtime("map_application"))


def test_application_300_database_identity_uses_maintenance_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_checked(arguments: list[str], *, label: str) -> bytes:
        assert label == "Map application 300 database identity"
        assert arguments[arguments.index("--dbname") + 1] == "postgres"
        assert "pg_catalog.pg_control_system()" in arguments[-1]
        assert "WHERE datname = 'map_app'" in arguments[-1]
        return b"map_app|127001|ktm_feature_schema_owner|7474747474747474747\n"

    runner = Mock(side_effect=run_checked)
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    identity = read_application_300_database_identity(_runtime("map_application"))

    assert identity.database_name == "map_app"
    assert identity.database_oid == 127001
    assert identity.database_owner == "ktm_feature_schema_owner"
    assert identity.postgres_system_identifier == "7474747474747474747"
    assert "password" not in " ".join(runner.call_args.args[0]).lower()


def test_dagster_metadata_identity_query_is_strict_and_uses_maintenance_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_checked(arguments: list[str], *, label: str) -> bytes:
        assert label == "Map Dagster metadata database identity"
        assert arguments[arguments.index("--dbname") + 1] == "postgres"
        assert "pg_catalog.pg_control_system()" in arguments[-1]
        assert "database_row.datname = 'map_dagster'" in arguments[-1]
        assert "role.rolname = 'map_dagster_metadata'" in arguments[-1]
        assert "role.rolcanlogin" in arguments[-1]
        assert "role.rolinherit" in arguments[-1]
        return (
            b"7474747474747474747|map_dagster|127002|map_dagster_metadata|"
            b"map_dagster_metadata|t|f|f|f|f|f|f|0|0\n"
        )

    monkeypatch.setattr(database_runtime, "_run_checked", Mock(side_effect=run_checked))

    identity = read_application_300_dagster_metadata_identity(
        _metadata_runtime(),
        metadata_user="map_dagster_metadata",
    )

    assert identity.system_identifier == "7474747474747474747"
    assert identity.name == "map_dagster"
    assert identity.oid == 127002
    assert identity.owner == "map_dagster_metadata"
    assert identity.login_role == "map_dagster_metadata"
    assert identity.login_role_attributes == DagsterMetadataRoleAttributes(
        superuser=False,
        create_database=False,
        create_role=False,
        replication=False,
        bypass_rls=False,
        granted_role_count=0,
        member_role_count=0,
        can_login=True,
        inherit=False,
    )


def test_dagster_metadata_identity_rejects_privileged_or_membered_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        database_runtime,
        "_run_checked",
        Mock(
            return_value=(
                b"7474747474747474747|map_dagster|127002|map_dagster_metadata|"
                b"map_dagster_metadata|t|f|t|f|f|f|f|0|0\n"
            )
        ),
    )

    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_application_300_dagster_metadata_identity(
            _metadata_runtime(),
            metadata_user="map_dagster_metadata",
        )


@pytest.mark.parametrize(
    "role_flags",
    (
        "f|f|f|f|f|f|f|0|0",
        "t|t|f|f|f|f|f|0|0",
    ),
)
def test_dagster_metadata_identity_rejects_login_attribute_drift(
    role_flags: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        database_runtime,
        "_run_checked",
        Mock(
            return_value=(
                "7474747474747474747|map_dagster|127002|map_dagster_metadata|"
                f"map_dagster_metadata|{role_flags}\n"
            ).encode("ascii")
        ),
    )

    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_application_300_dagster_metadata_identity(
            _metadata_runtime(),
            metadata_user="map_dagster_metadata",
        )


def test_dagster_metadata_database_init_preflights_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutation = Mock()
    monkeypatch.setattr(database_runtime, "_read_database_owner", Mock(return_value="map_owner"))
    monkeypatch.setattr(database_runtime, "_read_dagster_metadata_role_preflight", Mock())
    monkeypatch.setattr(database_runtime, "_run_checked", mutation)
    monkeypatch.setattr(database_runtime, "_run_checked_with_input", mutation)

    with pytest.raises(DeploymentContractError, match="already exists"):
        initialize_application_300_dagster_metadata_database(
            _metadata_runtime(),
            metadata_user="map_dagster_metadata",
            metadata_password="metadata-secret",
        )

    mutation.assert_not_called()


def test_dagster_metadata_database_init_refuses_unsafe_existing_role_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutation = Mock()
    monkeypatch.setattr(database_runtime, "_read_database_owner", Mock(return_value=None))
    monkeypatch.setattr(
        database_runtime,
        "_read_dagster_metadata_role_preflight",
        Mock(
            return_value=database_runtime._DagsterMetadataRolePreflight(
                can_login=True,
                inherit=False,
                attributes=DagsterMetadataRoleAttributes(
                    superuser=False,
                    create_database=False,
                    create_role=False,
                    replication=False,
                    bypass_rls=False,
                    granted_role_count=1,
                    member_role_count=0,
                ),
            )
        ),
    )
    monkeypatch.setattr(database_runtime, "_run_checked", mutation)
    monkeypatch.setattr(database_runtime, "_run_checked_with_input", mutation)

    with pytest.raises(DeploymentContractError, match="role is unsafe"):
        initialize_application_300_dagster_metadata_database(
            _metadata_runtime(),
            metadata_user="map_dagster_metadata",
            metadata_password="metadata-secret",
        )

    mutation.assert_not_called()


def test_dagster_metadata_database_init_rotates_only_password_for_safe_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role_mutations: list[tuple[list[str], bytes, str]] = []
    createdb_calls: list[tuple[list[str], str]] = []
    expected_identity = database_runtime.DagsterMetadataDatabaseIdentity(
        system_identifier="7474747474747474747",
        name="map_dagster",
        oid=127002,
        owner="map_dagster_metadata",
        login_role="map_dagster_metadata",
        login_role_attributes=DagsterMetadataRoleAttributes(
            superuser=False,
            create_database=False,
            create_role=False,
            replication=False,
            bypass_rls=False,
            granted_role_count=0,
            member_role_count=0,
        ),
    )
    monkeypatch.setattr(database_runtime, "_read_database_owner", Mock(return_value=None))
    monkeypatch.setattr(
        database_runtime,
        "_read_dagster_metadata_role_preflight",
        Mock(
            return_value=database_runtime._DagsterMetadataRolePreflight(
                can_login=True,
                inherit=False,
                attributes=expected_identity.login_role_attributes,
            )
        ),
    )

    def run_with_input(arguments: list[str], *, input_bytes: bytes, label: str) -> bytes:
        role_mutations.append((arguments, input_bytes, label))
        return b"ALTER ROLE\n"

    def run_checked(arguments: list[str], *, label: str) -> bytes:
        createdb_calls.append((arguments, label))
        return b""

    monkeypatch.setattr(database_runtime, "_run_checked_with_input", run_with_input)
    monkeypatch.setattr(database_runtime, "_run_checked", run_checked)
    monkeypatch.setattr(
        database_runtime,
        "read_application_300_dagster_metadata_identity",
        Mock(return_value=expected_identity),
    )

    identity = initialize_application_300_dagster_metadata_database(
        _metadata_runtime(),
        metadata_user="map_dagster_metadata",
        metadata_password="metadata-secret",
    )

    assert identity == expected_identity
    assert len(role_mutations) == 1
    role_command, role_sql, role_label = role_mutations[0]
    assert role_label == "Map Dagster metadata role password rotate"
    assert "--interactive" in role_command
    assert "metadata-secret" not in " ".join(role_command)
    assert role_sql.startswith(b'ALTER ROLE "map_dagster_metadata" PASSWORD ')
    assert b"LOGIN" not in role_sql
    assert b"NOINHERIT" not in role_sql
    assert [label for _, label in createdb_calls] == [
        "Map Dagster metadata database create"
    ]
    createdb = createdb_calls[0][0]
    assert createdb[createdb.index("--maintenance-db") + 1] == "postgres"
    assert createdb[createdb.index("--template") + 1] == "template0"
    assert createdb[createdb.index("--owner") + 1] == "map_dagster_metadata"


def test_dagster_metadata_database_init_creates_absent_role_with_template0_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role_mutations: list[tuple[bytes, str]] = []
    createdb_calls: list[list[str]] = []
    expected_identity = database_runtime.DagsterMetadataDatabaseIdentity(
        system_identifier="7474747474747474747",
        name="map_dagster",
        oid=127002,
        owner="map_dagster_metadata",
        login_role="map_dagster_metadata",
        login_role_attributes=DagsterMetadataRoleAttributes(
            superuser=False,
            create_database=False,
            create_role=False,
            replication=False,
            bypass_rls=False,
            granted_role_count=0,
            member_role_count=0,
        ),
    )
    monkeypatch.setattr(database_runtime, "_read_database_owner", Mock(return_value=None))
    monkeypatch.setattr(
        database_runtime,
        "_read_dagster_metadata_role_preflight",
        Mock(return_value=None),
    )

    def record_role_mutation(
        arguments: list[str], *, input_bytes: bytes, label: str
    ) -> bytes:
        del arguments
        role_mutations.append((input_bytes, label))
        return b"CREATE ROLE\n"

    def record_createdb(arguments: list[str], *, label: str) -> bytes:
        del label
        createdb_calls.append(arguments)
        return b""

    monkeypatch.setattr(
        database_runtime,
        "_run_checked_with_input",
        Mock(side_effect=record_role_mutation),
    )
    monkeypatch.setattr(
        database_runtime,
        "_run_checked",
        Mock(side_effect=record_createdb),
    )
    monkeypatch.setattr(
        database_runtime,
        "read_application_300_dagster_metadata_identity",
        Mock(return_value=expected_identity),
    )

    assert (
        initialize_application_300_dagster_metadata_database(
            _metadata_runtime(),
            metadata_user="map_dagster_metadata",
            metadata_password="metadata-secret",
        )
        == expected_identity
    )

    assert role_mutations == [
        (
            b'CREATE ROLE "map_dagster_metadata" LOGIN NOINHERIT PASSWORD '
            b"'metadata-secret';\n",
            "Map Dagster metadata role create",
        )
    ]
    assert createdb_calls[0][createdb_calls[0].index("--template") + 1] == "template0"
    assert createdb_calls[0][createdb_calls[0].index("--maintenance-db") + 1] == "postgres"


def test_dagster_metadata_database_init_requires_frozen_metadata_owner() -> None:
    with pytest.raises(DeploymentContractError, match="not frozen"):
        initialize_application_300_dagster_metadata_database(
            _runtime("map_dagster"),
            metadata_user="map_dagster_metadata",
            metadata_password="metadata-secret",
        )


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
    assert "ktm_curation_command_owner" in command[-1]
    assert "ktm_curation_admin_executor" in command[-1]
    assert "pg_auth_members" in command[-1]
    assert "pg_default_acl" in command[-1]
    assert "map_dagster_metadata" in command[-1]
    assert "granted_role.rolname" in command[-1]
    # PostgreSQL roles survive the three-DB recreation.  Memberships owned by
    # later M01~M05 phases are valid cluster residue at this legacy checkpoint
    # and are checked by their own phase bootstrap assertions.
    assert "pg_get_userbyid(membership.roleid) NOT IN" in command[-1]
    assert "member_role.rolname NOT IN" in command[-1]
    assert "JOIN pg_roles member_role ON member_role.oid = membership.member" in command[-1]
    for future_role in (
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
    ):
        assert future_role in command[-1]
    assert "privilege.grantee = 0" in command[-1]
    # 실데이터 덤프/복원 경로(#171)에서 이 테이블 소유권이 넘어가지 않으면
    # migrator가 첫 `SELECT version_num`에서 42501로 죽는다. fresh DB에서는
    # 테이블이 없어 무증상이라 assertion이 직접 봐야 한다.
    assert "relation.relname = 'alembic_version'" in command[-1]
    assert "namespace.nspname = 'public'" in command[-1]


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
