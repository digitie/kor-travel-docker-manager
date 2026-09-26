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

    assert runner.call_args_list[0].kwargs["label"] == (
        "map_application database destructive drop"
    )


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

    with pytest.raises(DeploymentContractError, match="is not destructible"):
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
