from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock

import pytest

import kor_travel_docker_manager.services.database_runtime as database_runtime
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.database_runtime import (
    DagsterMetadataRoleAttributes,
    DatabaseRuntime,
    create_fresh_application_300_database,
    database_runtimes_from_frozen_contract,
    ensure_map_application_database,
    initialize_application_300_dagster_metadata_database,
    read_application_300_dagster_metadata_identity,
    read_database_schema_revision,
    reset_databases_for_application_300,
    schema_revision_table_exists,
)


def _runtime(role: database_runtime.DatabaseRole) -> DatabaseRuntime:
    return DatabaseRuntime(
        role=role,
        container_name="postgres-rehearsal",
        port=11000 if role == "pinvi" else 12700,
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
                "kor-travel-shared-postgres": {
                    "container_name": "shared-postgres-production",
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
            "PINVI_APP_DB_USER": "pin_owner",
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
        ("pinvi", "shared-postgres-production", 11000, "pin_app", "pin_owner", "pin_cluster_admin"),
    ]
    assert {runtime.container_name for runtime in runtimes} == {
        "map-postgres-production",
        "shared-postgres-production",
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
                    "kor-travel-shared-postgres": {
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
                "KOR_TRAVEL_SHARED_POSTGRES_USER": "pin_owner",
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
                    "kor-travel-shared-postgres": {
                        "container_name": "shared-postgres-production",
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
                "KOR_TRAVEL_SHARED_POSTGRES_USER": "pin_owner",
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
                    "kor-travel-shared-postgres": {
                        "container_name": "shared-postgres-production",
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
                    "kor-travel-shared-postgres": {
                        "container_name": "shared-postgres-production",
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
                "KOR_TRAVEL_SHARED_POSTGRES_USER": "pin_owner",
                "KOR_TRAVEL_SHARED_DB_PORT": port_value,
            },
        )


def test_application_300_reset_preflights_all_owners_before_drop(
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
        reset_databases_for_application_300(runtimes)

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
        # R2: Map drop 소유자가 Map 쌍 밖의 DB를 소유하지 않는지 **첫 drop 전에** 읽는다.
        "map_application database owner's databases",
        "map_dagster database owner's databases",
        "map_application database destructive drop",
        "map_dagster database destructive drop",
        "pinvi database destructive drop",
        "pinvi database destructive create",
    ]
    assert sum("createdb" in arguments for arguments, _ in calls) == 1
    assert "createdb" in calls[-1][0]
    assert calls[-1][0][calls[-1][0].index("--template") + 1] == "template0"
    assert all(arguments[arguments.index("--user") + 1] == "postgres" for arguments, _ in calls)
    assert [arguments[arguments.index("--port") + 1] for arguments, _ in calls] == [
        "12700",
        "12700",
        "12700",
        "12700",
        "11000",
        "11000",
    ]
    assert all("password" not in " ".join(arguments).lower() for arguments, _ in calls)


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
        assert "role.rolconnlimit" in arguments[-1]
        assert "role.rolvaliduntil IS NULL" in arguments[-1]
        assert "role.rolconfig" in arguments[-1]
        assert "pg_catalog.pg_db_role_setting" in arguments[-1]
        return (
            b"7474747474747474747|map_dagster|127002|map_dagster_metadata|"
            b"map_dagster_metadata|t|f|f|f|f|f|f|-1|t|0|0|0|0\n"
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
        connection_limit=-1,
        valid_until_is_null=True,
        role_config_count=0,
        database_role_setting_count=0,
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
                b"map_dagster_metadata|t|f|t|f|f|f|f|-1|t|0|0|0|0\n"
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
        "f|f|f|f|f|f|f|-1|t|0|0|0|0",
        "t|t|f|f|f|f|f|-1|t|0|0|0|0",
        "t|f|f|f|f|f|f|0|t|0|0|0|0",
        "t|f|f|f|f|f|f|-1|f|0|0|0|0",
        "t|f|f|f|f|f|f|-1|t|1|0|0|0",
        "t|f|f|f|f|f|f|-1|t|0|1|0|0",
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


@pytest.mark.parametrize(
    ("attribute", "value"),
    (
        ("granted_role_count", 1),
        ("connection_limit", 0),
        ("valid_until_is_null", False),
        ("role_config_count", 1),
        ("database_role_setting_count", 1),
    ),
)
def test_dagster_metadata_database_init_refuses_unsafe_existing_role_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    value: object,
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
                owned_database_count=0,
                shared_dependency_count=0,
                attributes=DagsterMetadataRoleAttributes(
                    superuser=False,
                    create_database=False,
                    create_role=False,
                    replication=False,
                    bypass_rls=False,
                    granted_role_count=(
                        value if attribute == "granted_role_count" else 0
                    ),
                    member_role_count=0,
                    connection_limit=(
                        value if attribute == "connection_limit" else -1
                    ),
                    valid_until_is_null=(
                        value if attribute == "valid_until_is_null" else True
                    ),
                    role_config_count=(
                        value if attribute == "role_config_count" else 0
                    ),
                    database_role_setting_count=(
                        value
                        if attribute == "database_role_setting_count"
                        else 0
                    ),
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
                owned_database_count=0,
                shared_dependency_count=0,
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


@pytest.mark.parametrize("foreign_role", ("map_application", "map_dagster", "pinvi"))
def test_application_300_reset_refuses_a_foreign_owned_database(
    monkeypatch: pytest.MonkeyPatch,
    foreign_role: database_runtime.DatabaseRole,
) -> None:
    runner = Mock()
    monkeypatch.setattr(
        database_runtime,
        "_read_database_owner",
        Mock(
            side_effect=lambda runtime: (
                "foreign" if runtime.role == foreign_role else runtime.owner_name
            )
        ),
    )
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    with pytest.raises(DeploymentContractError, match="owner differs"):
        reset_databases_for_application_300(
            (_runtime("map_application"), _runtime("map_dagster"), _runtime("pinvi"))
        )

    runner.assert_not_called()


def test_application_300_reset_accepts_the_bootstrapped_schema_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(return_value=b"")
    monkeypatch.setattr(
        database_runtime,
        "_read_database_owner",
        Mock(
            side_effect=lambda runtime: (
                "ktm_feature_schema_owner"
                if runtime.role == "map_application"
                else runtime.owner_name
            )
        ),
    )
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    reset_databases_for_application_300(
        (_runtime("map_application"), _runtime("map_dagster"), _runtime("pinvi"))
    )

    drops = [
        call.kwargs["label"]
        for call in runner.call_args_list
        if call.kwargs["label"].endswith("destructive drop")
    ]
    assert drops[0] == "map_application database destructive drop"


def test_application_300_reset_requires_three_canonical_roles() -> None:
    runtime = _runtime("pinvi")

    with pytest.raises(DeploymentContractError, match="database roles"):
        reset_databases_for_application_300((runtime, runtime, runtime))


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


@pytest.mark.parametrize("reserved_role", ("map_application", "map_dagster", "pinvi"))
@pytest.mark.parametrize(
    "reserved",
    ["postgres", "template0", "template1", "template_postgis"],
)
def test_destructive_reset_refuses_cluster_maintenance_databases(
    monkeypatch: pytest.MonkeyPatch,
    reserved: str,
    reserved_role: database_runtime.DatabaseRole,
) -> None:
    """공용 instance로 옮긴 뒤 남는 유일한 오조준 대상은 유지보수 DB다.

    바로 앞의 owner preflight가 "현재 소유자가 이 role의 허용 소유자 집합에 있을
    것"을 요구하므로 형제 프로젝트 운영 DB(geo·concierge·weather)는 각자의 app
    role 소유라 이미 막힌다. 그런데 유지보수 DB는 bootstrap owner 소유라 그
    preflight를 통과해 버리고, PinVi가 전용 instance에 있을 때와 달리 이제 그
    실수는 네 프로젝트의 관리 경로를 한 번에 없앤다.

    울타리는 **첫 drop 전에** 세 DB 모두에 걸린다 — 종전에는 PinVi 재생성 안에만
    있어서 Map 두 DB는 울타리 없이 지워졌다.
    """

    def runtime(role: database_runtime.DatabaseRole) -> DatabaseRuntime:
        base = _runtime(role)
        return replace(base, database_name=reserved) if role == reserved_role else base

    runtimes = (runtime("map_application"), runtime("map_dagster"), runtime("pinvi"))
    commands: list[list[str]] = []

    def _record(command, *, label):  # noqa: ANN001, ANN202
        commands.append(list(command))
        return b""

    # owner preflight 자체는 읽기다 — 그것까지 막으면 울타리가 아니라 읽기 실패를
    # 보게 된다. 소유자는 허용 집합과 일치시켜, 막는 것이 **이름**임을 고정한다.
    monkeypatch.setattr(
        database_runtime, "_read_database_owner", lambda runtime: runtime.owner_name
    )
    monkeypatch.setattr(database_runtime, "_run_checked", _record)

    with pytest.raises(DeploymentContractError, match="reserved cluster database"):
        reset_databases_for_application_300(runtimes)

    assert not any("dropdb" in token for command in commands for token in command)
    assert commands == []


def _ensure_harness(
    monkeypatch: pytest.MonkeyPatch,
    owner: str | None,
    *,
    schema_table: bool = False,
) -> tuple[Mock, Mock, list[str]]:
    """생성(createdb)과 bootstrap을 **한 기록**에 남겨 순서까지 단언할 수 있게 한다."""

    events: list[str] = []
    runner = Mock(side_effect=lambda arguments, *, label: events.append(label) or b"")
    bootstrap = Mock(side_effect=lambda: events.append("bootstrap"))
    owners = [owner, None] if owner is None else [owner]

    def read_owner(runtime: DatabaseRuntime) -> str | None:
        del runtime
        return owners.pop(0) if owners else None

    monkeypatch.setattr(database_runtime, "_read_database_owner", read_owner)
    monkeypatch.setattr(database_runtime, "_run_checked", runner)
    monkeypatch.setattr(
        database_runtime, "schema_revision_table_exists", lambda _runtime: schema_table
    )
    return runner, bootstrap, events


def test_ensure_map_application_database_creates_then_bootstraps_an_absent_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, bootstrap, events = _ensure_harness(monkeypatch, None)

    outcome = ensure_map_application_database(
        _runtime("map_application"), run_role_bootstrap=bootstrap
    )

    assert outcome == "created"
    create = runner.call_args.args[0]
    assert "createdb" in create
    assert create[create.index("--template") + 1] == "template0"
    assert events == ["map_application fresh 300 database create", "bootstrap"]


def test_ensure_map_application_database_bootstraps_a_created_but_unbootstrapped_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, bootstrap, _ = _ensure_harness(monkeypatch, "map_owner")

    outcome = ensure_map_application_database(
        _runtime("map_application"), run_role_bootstrap=bootstrap
    )

    assert outcome == "bootstrapped"
    runner.assert_not_called()
    bootstrap.assert_called_once_with()


def test_ensure_map_application_database_leaves_a_bootstrapped_database_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, bootstrap, _ = _ensure_harness(monkeypatch, "ktm_feature_schema_owner")

    outcome = ensure_map_application_database(
        _runtime("map_application"), run_role_bootstrap=bootstrap
    )

    assert outcome == "present"
    runner.assert_not_called()
    bootstrap.assert_not_called()


def test_a_restored_database_still_owned_by_the_bootstrap_owner_is_refused_not_bootstrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``createdb --owner`` + ``pg_restore``로 복원한 DB다. fresh 전용 bootstrap은 이것을
    거부하므로, 돌리기 전에 무엇을 하라는지와 함께 거부한다(B2 적대 리뷰 2차)."""

    runner, bootstrap, _ = _ensure_harness(monkeypatch, "map_owner", schema_table=True)

    with pytest.raises(DeploymentContractError, match="OWNER TO ktm_feature_schema_owner"):
        ensure_map_application_database(
            _runtime("map_application"), run_role_bootstrap=bootstrap
        )

    runner.assert_not_called()
    bootstrap.assert_not_called()


@pytest.mark.parametrize(
    ("owner", "schema_table", "expected"),
    (
        (None, False, "absent"),
        ("map_owner", False, "unbootstrapped"),
        ("ktm_feature_schema_owner", True, "present"),
    ),
)
def test_the_convergibility_check_only_reads(
    monkeypatch: pytest.MonkeyPatch,
    owner: str | None,
    schema_table: bool,
    expected: str,
) -> None:
    """배포는 런타임을 멈추기 전에 이것을 부른다 — 무엇도 만들거나 돌리지 않아야 한다."""

    runner, _, _ = _ensure_harness(monkeypatch, owner, schema_table=schema_table)

    assert (
        database_runtime.require_map_application_database_convergible(
            _runtime("map_application")
        )
        == expected
    )
    runner.assert_not_called()


@pytest.mark.parametrize("role", ("map_dagster", "pinvi"))
def test_ensure_map_application_database_refuses_other_roles(
    role: database_runtime.DatabaseRole,
) -> None:
    with pytest.raises(DeploymentContractError, match="role is invalid"):
        ensure_map_application_database(_runtime(role), run_role_bootstrap=Mock())


def test_ensure_map_application_database_refuses_a_foreign_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, bootstrap, _ = _ensure_harness(monkeypatch, "someone_else")

    with pytest.raises(DeploymentContractError, match="owner differs"):
        ensure_map_application_database(
            _runtime("map_application"), run_role_bootstrap=bootstrap
        )

    runner.assert_not_called()
    bootstrap.assert_not_called()


@pytest.mark.parametrize(
    ("role", "table", "output", "expected"),
    (
        ("pinvi", '\'"app"."alembic_version"\'', b"f\n", False),
        ("pinvi", '\'"app"."alembic_version"\'', b"t\n", True),
        ("map_dagster", '\'"public"."alembic_version"\'', b"t\n", True),
    ),
)
def test_schema_revision_table_exists_reads_the_role_table(
    monkeypatch: pytest.MonkeyPatch,
    role: database_runtime.DatabaseRole,
    table: str,
    output: bytes,
    expected: bool,
) -> None:
    runner = Mock(return_value=output)
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    assert schema_revision_table_exists(_runtime(role)) is expected
    command = runner.call_args.args[0]
    assert command[-1] == f"SELECT to_regclass({table}) IS NOT NULL"
    assert command[command.index("--dbname") + 1] == _runtime(role).database_name


def test_schema_revision_table_exists_rejects_ambiguous_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(database_runtime, "_run_checked", Mock(return_value=b"yes\n"))

    with pytest.raises(DeploymentContractError, match="output is invalid"):
        schema_revision_table_exists(_runtime("pinvi"))


# --- M1: tenant fences (R2 owner, name fence, R4 isolation) -------------------------------


def _dedicated_shape(role: database_runtime.DatabaseRole) -> DatabaseRuntime:
    """Map bootstrap 소유자가 instance admin인 모양 — 오늘 전용 instance, M2 뒤 공용 instance."""

    base = _metadata_runtime() if role == "map_dagster" else _runtime(role)
    return replace(base, owner_name="cluster_admin")


def _record_commands(
    monkeypatch: pytest.MonkeyPatch,
    *,
    owners: dict[str, str | None],
    owned: dict[str, str] | None = None,
) -> list[list[str]]:
    """owner 읽기는 ``owners``로, 소유 DB 읽기는 ``owned``(owner→줄)로 답하고 모든 명령을 남긴다."""

    commands: list[list[str]] = []

    def run_checked(arguments: list[str], *, label: str) -> bytes:
        commands.append(list(arguments))
        if label.endswith("owner's databases"):
            owner = arguments[-1].rsplit("rolname = '", 1)[1].split("'", 1)[0]
            return (owned or {}).get(owner, "").encode("utf-8")
        return b""

    monkeypatch.setattr(
        database_runtime, "_read_database_owner", lambda runtime: owners[runtime.role]
    )
    monkeypatch.setattr(database_runtime, "_run_checked", run_checked)
    return commands


def _dropped(commands: list[list[str]]) -> list[str]:
    return [command[-1] for command in commands if "dropdb" in command]


def test_reset_refuses_an_admin_owned_map_database_and_drops_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """반쯤 만든(admin 소유) Map DB는 이 재구축이 만든 것이라는 증거가 없다 — 지우지 않는다."""

    runtimes = (
        _dedicated_shape("map_application"),
        _dedicated_shape("map_dagster"),
        _runtime("pinvi"),
    )
    commands = _record_commands(
        monkeypatch,
        owners={"map_application": "cluster_admin", "map_dagster": None, "pinvi": None},
    )

    with pytest.raises(DeploymentContractError, match="map_application database owner differs"):
        reset_databases_for_application_300(runtimes)

    assert _dropped(commands) == []
    assert not any("createdb" in command for command in commands)


def test_recreate_refuses_an_admin_owned_pinvi_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PINVI_APP_DB_USER`가 instance admin으로 잘못 박혀도 admin 소유 DB는 지우지 않는다."""

    pinvi = replace(_runtime("pinvi"), owner_name="cluster_admin")
    direct = _record_commands(
        monkeypatch, owners={"map_application": None, "map_dagster": None, "pinvi": None}
    )
    with pytest.raises(DeploymentContractError, match="pinvi database owner differs"):
        database_runtime._recreate_empty_database_after_owner_preflight(
            pinvi, existing_owner="cluster_admin"
        )
    assert direct == []

    through_reset = _record_commands(
        monkeypatch,
        owners={"map_application": None, "map_dagster": None, "pinvi": "cluster_admin"},
    )
    with pytest.raises(DeploymentContractError, match="pinvi database owner differs"):
        reset_databases_for_application_300(
            (_runtime("map_application"), _runtime("map_dagster"), pinvi)
        )
    assert through_reset == []


@pytest.mark.parametrize("role", ("map_application", "map_dagster", "pinvi"))
def test_permitted_owner_sets_never_contain_the_instance_admin(
    role: database_runtime.DatabaseRole,
) -> None:
    runtime = replace(
        _dedicated_shape(role),
        additional_owner_names=frozenset({"cluster_admin", "map_dagster_metadata"})
        if role == "map_dagster"
        else frozenset(),
    )

    permitted = database_runtime._permitted_existing_owners(runtime)

    assert runtime.admin_name not in permitted
    # 빼는 것은 admin 하나뿐이다 — 나머지 허용 소유자는 그대로 남는다.
    expected = {
        "map_application": {"ktm_feature_schema_owner"},
        "map_dagster": {"map_dagster_metadata"},
        "pinvi": set(),
    }[role]
    assert permitted == expected


def test_map_owner_sets_are_disjoint_from_pinvi_and_foreign_owners() -> None:
    """n150 모양으로 frozen 계약에서 유도한 세 허용 집합. Map 쪽은 PinVi·admin·다른 tenant와 겹치지 않는다.

    Map app과 Dagster는 구성상 `owner_name`을 공유하므로 "세 runtime 모두 서로소"는 불가능하다.
    """

    map_application, map_dagster, pinvi = database_runtimes_from_frozen_contract(
        resolved={
            "services": {
                "kor-travel-map-postgres": {
                    "container_name": "kor-travel-map-postgres",
                    "environment": {"POSTGRES_USER": "kor_travel_map"},
                },
                "kor-travel-shared-postgres": {
                    "container_name": "kor-travel-shared-postgresql",
                    "environment": {"POSTGRES_USER": "shared_admin"},
                },
            }
        },
        environment={
            "KOR_TRAVEL_MAP_POSTGRES_DB": "kor_travel_map",
            "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB": "kor_travel_map_dagster",
            "KOR_TRAVEL_MAP_POSTGRES_USER": "kor_travel_map",
            "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER": "kor_travel_map_dagster",
            "PINVI_POSTGRES_DB": "pinvi",
            "PINVI_APP_DB_USER": "pinvi_application_runtime",
        },
    )
    permitted = database_runtime._permitted_existing_owners
    foreign = {
        # 재구축 밖의 DB를 소유하는 principal: 두 instance admin과 다른 tenant의 login.
        "kor_travel_map",
        "shared_admin",
        "geo_app",
        "concierge_app",
        "transport_app",
    }

    assert permitted(map_application) == {"ktm_feature_schema_owner"}
    assert permitted(map_dagster) == {"kor_travel_map_dagster"}
    assert permitted(pinvi) == {"pinvi_application_runtime"}
    for runtime in (map_application, map_dagster):
        assert permitted(runtime).isdisjoint(permitted(pinvi))
        assert permitted(runtime).isdisjoint(foreign)
    assert permitted(pinvi).isdisjoint(foreign)


def test_reset_refuses_a_map_owner_that_owns_a_foreign_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """metadata user·Dagster DB 이름이 다른 tenant의 login·DB로 일관되게 잘못 박힌 경우다."""

    runtimes = (_runtime("map_application"), _metadata_runtime(), _runtime("pinvi"))
    commands = _record_commands(
        monkeypatch,
        owners={
            "map_application": "ktm_feature_schema_owner",
            "map_dagster": "map_dagster_metadata",
            "pinvi": "pin_owner",
        },
        owned={
            "ktm_feature_schema_owner": "map_app\n",
            "map_dagster_metadata": "map_dagster\npin_dagster\n",
        },
    )

    with pytest.raises(DeploymentContractError, match="outside the Map pair"):
        reset_databases_for_application_300(runtimes)

    assert _dropped(commands) == []
    assert not any("createdb" in command for command in commands)


def test_reset_drops_map_owners_that_own_only_the_map_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """위 거부의 대조군 — 같은 대역에서 Map 쌍만 소유하면 지운다."""

    runtimes = (_runtime("map_application"), _metadata_runtime(), _runtime("pinvi"))
    commands = _record_commands(
        monkeypatch,
        owners={
            "map_application": "ktm_feature_schema_owner",
            "map_dagster": "map_dagster_metadata",
            "pinvi": "pin_owner",
        },
        owned={
            "ktm_feature_schema_owner": "map_app\n",
            "map_dagster_metadata": "map_dagster\n",
        },
    )

    reset_databases_for_application_300(runtimes)

    assert _dropped(commands) == ["map_app", "map_dagster", "pin_app"]


def _preflight_output(owned_databases: int, shared_dependencies: int) -> bytes:
    return f"t|f|f|f|f|f|f|-1|t|0|0|0|0|{owned_databases}|{shared_dependencies}\n".encode()


@pytest.mark.parametrize(
    ("owned_databases", "shared_dependencies"),
    ((2, 150), (1, 1), (0, 43)),
    ids=["tenant-login", "owns-one-database", "owns-objects-only"],
)
def test_rotate_preflight_refuses_a_role_that_owns_a_database_or_objects(
    monkeypatch: pytest.MonkeyPatch,
    owned_databases: int,
    shared_dependencies: int,
) -> None:
    """속성 검사를 다 통과하는 다른 tenant의 login(`LOGIN NOINHERIT`, 멤버십 없음)이다."""

    mutation = Mock()
    monkeypatch.setattr(database_runtime, "_read_database_owner", Mock(return_value=None))
    monkeypatch.setattr(
        database_runtime,
        "_run_checked",
        Mock(return_value=_preflight_output(owned_databases, shared_dependencies)),
    )
    monkeypatch.setattr(database_runtime, "_run_checked_with_input", mutation)

    with pytest.raises(DeploymentContractError, match="role is unsafe"):
        initialize_application_300_dagster_metadata_database(
            _metadata_runtime(),
            metadata_user="map_dagster_metadata",
            metadata_password="metadata-secret",
        )

    mutation.assert_not_called()


def test_rotate_preflight_accepts_a_leftover_role_that_owns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rotations: list[str] = []
    outputs = iter((_preflight_output(0, 0), b""))
    monkeypatch.setattr(database_runtime, "_read_database_owner", Mock(return_value=None))
    monkeypatch.setattr(
        database_runtime, "_run_checked", Mock(side_effect=lambda *_a, **_k: next(outputs))
    )
    monkeypatch.setattr(
        database_runtime,
        "_run_checked_with_input",
        Mock(side_effect=lambda *_a, label, **_k: rotations.append(label) or b""),
    )
    monkeypatch.setattr(
        database_runtime, "read_application_300_dagster_metadata_identity", Mock()
    )

    initialize_application_300_dagster_metadata_database(
        _metadata_runtime(),
        metadata_user="map_dagster_metadata",
        metadata_password="metadata-secret",
    )

    assert rotations == ["Map Dagster metadata role password rotate"]


def test_rotate_preflight_reads_owned_databases_and_shared_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(return_value=_preflight_output(0, 0))
    monkeypatch.setattr(database_runtime, "_run_checked", runner)

    preflight = database_runtime._read_dagster_metadata_role_preflight(
        _metadata_runtime(), "map_dagster_metadata"
    )

    assert preflight is not None
    assert (preflight.owned_database_count, preflight.shared_dependency_count) == (0, 0)
    query = runner.call_args.args[0][-1]
    assert "pg_catalog.pg_database owned WHERE owned.datdba = role.oid" in query
    assert "dependency.refclassid = 'pg_catalog.pg_authid'::regclass" in query
    assert "dependency.refobjid = role.oid" in query


@pytest.mark.parametrize(
    "reserved", ["postgres", "template0", "template1", "template_postgis"]
)
def test_convergible_rejects_reserved_database_names(
    monkeypatch: pytest.MonkeyPatch,
    reserved: str,
) -> None:
    """공용 instance의 `postgres`는 admin 소유에 `alembic_version`이 없다 — "bootstrap 전" Map DB로 읽힌다."""

    owner_reads = Mock(return_value="cluster_admin")
    runner = Mock(return_value=b"f\n")
    bootstrap = Mock()
    monkeypatch.setattr(database_runtime, "_read_database_owner", owner_reads)
    monkeypatch.setattr(database_runtime, "_run_checked", runner)
    runtime = replace(_dedicated_shape("map_application"), database_name=reserved)

    with pytest.raises(DeploymentContractError, match="reserved cluster database"):
        database_runtime.require_map_application_database_convergible(runtime)
    with pytest.raises(DeploymentContractError, match="reserved cluster database"):
        ensure_map_application_database(runtime, run_role_bootstrap=bootstrap)
    with pytest.raises(DeploymentContractError, match="reserved cluster database"):
        database_runtime.create_database_if_absent(replace(_runtime("pinvi"), database_name=reserved))

    owner_reads.assert_not_called()
    runner.assert_not_called()
    bootstrap.assert_not_called()


def test_the_schema_bearing_admin_owned_database_hint_asks_to_verify_it_is_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_harness(monkeypatch, "map_owner", schema_table=True)

    with pytest.raises(DeploymentContractError) as captured:
        database_runtime.require_map_application_database_convergible(
            _runtime("map_application")
        )

    message = str(captured.value)
    assert "verify it is Map's database (its public.alembic_version is a Map head)" in message
    assert "dropping it by hand" in message
    assert "docs/docker-management.md" in message


def _isolation_runtimes() -> tuple[DatabaseRuntime, DatabaseRuntime]:
    return _dedicated_shape("map_application"), _dedicated_shape("map_dagster")


def _isolation_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    usable: str = "97",
    readback: dict[str, str] | None = None,
) -> tuple[list[tuple[str, list[str]]], list[bytes]]:
    reads: list[tuple[str, list[str]]] = []
    scripts: list[bytes] = []
    answers = readback or {"map_app": "t|f|38|t", "map_dagster": "t|f|-1|t"}

    def run_checked(arguments: list[str], *, label: str) -> bytes:
        reads.append((label, list(arguments)))
        if label.endswith("usable connection slots"):
            return f"{usable}\n".encode()
        database = arguments[-1].rsplit("datname = '", 1)[1].split("'", 1)[0]
        return f"{answers[database]}\n".encode()

    def run_with_input(arguments: list[str], *, input_bytes: bytes, label: str) -> bytes:
        assert label == "Map database isolation"
        assert arguments[arguments.index("--dbname") + 1] == "postgres"
        assert "--single-transaction" in arguments
        assert arguments[arguments.index("--set") + 1] == "ON_ERROR_STOP=1"
        scripts.append(input_bytes)
        return b""

    monkeypatch.setattr(database_runtime, "_run_checked", run_checked)
    monkeypatch.setattr(database_runtime, "_run_checked_with_input", run_with_input)
    return reads, scripts


def test_isolation_sql_names_only_map_databases_and_the_dsn_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads, scripts = _isolation_harness(monkeypatch)
    login = database_runtime.map_application_login(
        {
            "KOR_TRAVEL_MAP_PG_DSN": (
                "postgresql+asyncpg://ktm_feature_service:secret@127.0.0.1:12700/map_app"
            )
        }
    )

    database_runtime.ensure_map_databases_isolated(*_isolation_runtimes(), login=login)

    assert login == "ktm_feature_service"
    assert scripts == [
        b'REVOKE CONNECT ON DATABASE "map_app" FROM PUBLIC;\n'
        b'GRANT CONNECT ON DATABASE "map_app" TO "ktm_feature_service";\n'
        b'REVOKE CONNECT ON DATABASE "map_dagster" FROM PUBLIC;\n'
        b'ALTER DATABASE "map_app" CONNECTION LIMIT 38;\n'
    ]
    assert [label for label, _ in reads] == [
        "map_application usable connection slots",
        "map_application database isolation read-back",
        "map_dagster database isolation read-back",
    ]
    # 읽기는 exact login 집합을 묻는다 — app은 DSN login, Dagster는 metadata user.
    assert "ARRAY['ktm_feature_service']::text[]" in reads[1][1][-1]
    assert "ARRAY['map_dagster_metadata']::text[]" in reads[2][1][-1]
    assert "pinvi" not in b"".join(scripts).decode() + "".join(r[1][-1] for r in reads)


@pytest.mark.parametrize(
    ("database", "answer"),
    (
        ("map_app", "t|t|38|t"),
        ("map_app", "f|f|38|t"),
        ("map_app", "t|f|38|f"),
        ("map_app", "t|f|-1|t"),
        ("map_dagster", "t|t|-1|t"),
        ("map_dagster", "t|f|-1|f"),
    ),
    ids=[
        "app-public-connect",
        "app-null-acl",
        "app-extra-login",
        "app-no-cap",
        "dagster-public-connect",
        "dagster-extra-login",
    ],
)
def test_isolation_readback_rejects_public_connect(
    monkeypatch: pytest.MonkeyPatch,
    database: str,
    answer: str,
) -> None:
    readback = {"map_app": "t|f|38|t", "map_dagster": "t|f|-1|t", database: answer}
    _isolation_harness(monkeypatch, readback=readback)

    with pytest.raises(DeploymentContractError, match="not isolated after the grant"):
        database_runtime.ensure_map_databases_isolated(
            *_isolation_runtimes(), login="ktm_feature_service"
        )


@pytest.mark.parametrize(
    ("usable", "cap"),
    ((97, 38), (100, 40), (7, 2), (5, 2), (2, 1), (1, 1)),
)
def test_connection_cap_is_forty_percent_of_usable_slots(usable: int, cap: int) -> None:
    assert database_runtime.map_application_connection_cap(usable) == cap


@pytest.mark.parametrize("usable", (0, -3, True))
def test_connection_cap_rejects_no_usable_slots(usable: int) -> None:
    with pytest.raises(DeploymentContractError, match="usable connection slots"):
        database_runtime.map_application_connection_cap(usable)


def test_isolation_cap_is_derived_from_the_live_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    _, scripts = _isolation_harness(
        monkeypatch, usable="47", readback={"map_app": "t|f|18|t", "map_dagster": "t|f|-1|t"}
    )

    database_runtime.ensure_map_databases_isolated(
        *_isolation_runtimes(), login="ktm_feature_service"
    )

    assert scripts[0].endswith(b'ALTER DATABASE "map_app" CONNECTION LIMIT 18;\n')


@pytest.mark.parametrize(
    "dsn",
    (
        "",
        "postgresql+asyncpg://127.0.0.1:12700/map_app",
        "postgresql+asyncpg://Bad-Login:x@127.0.0.1:12700/map_app",
        "postgresql+asyncpg://a%27b:x@127.0.0.1:12700/map_app",
    ),
)
def test_map_application_login_requires_an_identifier(dsn: str) -> None:
    with pytest.raises(DeploymentContractError, match="Map application login is invalid"):
        database_runtime.map_application_login({"KOR_TRAVEL_MAP_PG_DSN": dsn})


def test_isolation_refuses_runtimes_on_two_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    reads, scripts = _isolation_harness(monkeypatch)
    app, dagster = _isolation_runtimes()

    with pytest.raises(DeploymentContractError, match="share one PostgreSQL instance"):
        database_runtime.ensure_map_databases_isolated(
            app, replace(dagster, port=11000), login="ktm_feature_service"
        )

    assert reads == [] and scripts == []
