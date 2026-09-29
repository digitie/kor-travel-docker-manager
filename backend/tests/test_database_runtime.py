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

#: 합성 포트 — 운영 포트 리터럴을 쓰지 않는다. instance는 이 포트를 `-p`로 듣는 서버에서 유도된다.
_MAP_PORT = 15101
_PINVI_PORT = 15102


def _runtime(role: database_runtime.DatabaseRole) -> DatabaseRuntime:
    pinvi = role == "pinvi"
    return DatabaseRuntime(
        role=role,
        service_name="pg-pinvi" if pinvi else "pg-map",
        container_name="postgres-rehearsal",
        port=_PINVI_PORT if pinvi else _MAP_PORT,
        database_name={
            "map_application": "map_app",
            "map_dagster": "map_dagster",
            "pinvi": "pin_app",
        }[role],
        owner_name="pin_owner" if pinvi else "map_owner",
        admin_name="cluster_admin",
    )


def _metadata_runtime() -> DatabaseRuntime:
    return DatabaseRuntime(
        role="map_dagster",
        service_name="pg-map",
        container_name="postgres-rehearsal",
        port=_MAP_PORT,
        database_name="map_dagster",
        owner_name="map_owner",
        admin_name="cluster_admin",
        additional_owner_names=frozenset({"map_dagster_metadata"}),
    )


def _postgres_server(container: str, port: int, admin: str | None) -> dict[str, object]:
    """PostgreSQL 서버 서비스 하나(C6c가 `command`로 목격하는 모양). admin이 None이면 env가 비었다."""

    return {
        "container_name": container,
        "command": ["postgres", "-c", "listen_addresses=127.0.0.1", "-p", str(port)],
        "environment": {} if admin is None else {"POSTGRES_USER": admin},
    }


def _frozen_resolved(
    servers: dict[str, tuple[int, str | None]],
    *,
    pinvi_port: int,
) -> dict[str, object]:
    """합성 서버들 + `pinvi-api`의 resolved `PINVI_DATABASE_URL`(그 포트로)."""

    services: dict[str, object] = {
        name: _postgres_server(f"{name}-container", port, admin)
        for name, (port, admin) in servers.items()
    }
    services["pinvi-api"] = {
        "environment": {
            "PINVI_DATABASE_URL": (
                f"postgresql+asyncpg://pin_owner:pin-password@127.0.0.1:{pinvi_port}/pin_app"
            )
        }
    }
    return {"services": services}


def _frozen_environment(*, map_port: int, dagster_port: int | None = None, **values: str) -> dict[str, str]:
    environment = {
        "KOR_TRAVEL_MAP_POSTGRES_DB": "map_app",
        "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB": "map_dagster",
        "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER": "map_dagster_metadata",
        "PINVI_POSTGRES_DB": "pin_app",
        "PINVI_APP_DB_USER": "pin_owner",
        **values,
    }
    database = environment["KOR_TRAVEL_MAP_POSTGRES_DB"]
    dagster = environment["KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB"]
    metadata = environment["KOR_TRAVEL_MAP_DAGSTER_METADATA_USER"]
    environment.setdefault(
        "KOR_TRAVEL_MAP_PG_DSN",
        f"postgresql+asyncpg://ktm_feature_service:s@127.0.0.1:{map_port}/{database}",
    )
    environment.setdefault(
        "KOR_TRAVEL_MAP_DAGSTER_PG_URL",
        f"postgresql://{metadata}:m@127.0.0.1:{dagster_port or map_port}/{dagster}",
    )
    return environment


def test_map_and_pinvi_derive_one_shared_instance_from_dsn_ports() -> None:
    """ADR-53: 세 DB의 DSN이 같은 포트면 셋 다 그 포트를 듣는 **한** 서버에 산다."""

    runtimes = database_runtimes_from_frozen_contract(
        resolved=_frozen_resolved(
            {
                "pg-shared": (_MAP_PORT, "shared_admin"),
                # 다른 포트의 서버는 끌려오지 않는다.
                "pg-other": (_PINVI_PORT, "other_admin"),
            },
            pinvi_port=_MAP_PORT,
        ),
        environment=_frozen_environment(map_port=_MAP_PORT),
    )

    assert [
        (
            runtime.role,
            runtime.service_name,
            runtime.container_name,
            runtime.port,
            runtime.database_name,
            runtime.owner_name,
            runtime.admin_name,
        )
        for runtime in runtimes
    ] == [
        ("map_application", "pg-shared", "pg-shared-container", _MAP_PORT, "map_app", "shared_admin", "shared_admin"),
        ("map_dagster", "pg-shared", "pg-shared-container", _MAP_PORT, "map_dagster", "shared_admin", "shared_admin"),
        ("pinvi", "pg-shared", "pg-shared-container", _MAP_PORT, "pin_app", "pin_owner", "shared_admin"),
    ]
    assert runtimes[1].additional_owner_names == frozenset({"map_dagster_metadata"})


def test_database_runtime_identity_comes_from_frozen_contract() -> None:
    """포트가 다르면 instance도 다르다 — 이름이 아니라 포트가 instance를 고른다."""

    map_application, map_dagster, pinvi = database_runtimes_from_frozen_contract(
        resolved=_frozen_resolved(
            {"pg-a": (_MAP_PORT, "a_admin"), "pg-b": (_PINVI_PORT, "b_admin")},
            pinvi_port=_PINVI_PORT,
        ),
        environment=_frozen_environment(map_port=_MAP_PORT),
    )

    assert (map_application.service_name, map_application.admin_name) == ("pg-a", "a_admin")
    assert (map_dagster.service_name, map_dagster.port) == ("pg-a", _MAP_PORT)
    assert (pinvi.service_name, pinvi.container_name, pinvi.port, pinvi.admin_name) == (
        "pg-b",
        "pg-b-container",
        _PINVI_PORT,
        "b_admin",
    )


def test_map_owner_is_the_instance_admin() -> None:
    """S1: Map fresh bootstrap은 instance의 기존 admin으로 돈다 — env의 Map superuser는 없다."""

    map_application, map_dagster, _pinvi = database_runtimes_from_frozen_contract(
        resolved=_frozen_resolved({"pg-shared": (_MAP_PORT, "shared_admin")}, pinvi_port=_MAP_PORT),
        # 퇴역한 키가 남아 있어도 소유자를 바꾸지 못한다.
        environment=_frozen_environment(
            map_port=_MAP_PORT, KOR_TRAVEL_MAP_POSTGRES_USER="kor_travel_map"
        ),
    )

    assert map_application.owner_name == map_application.admin_name == "shared_admin"
    assert map_dagster.owner_name == map_dagster.admin_name == "shared_admin"
    # R2: admin은 절대 파기 소유자가 아니다.
    assert "shared_admin" not in database_runtime._permitted_existing_owners(map_application)
    assert "shared_admin" not in database_runtime._permitted_existing_owners(map_dagster)


@pytest.mark.parametrize(
    ("servers", "match"),
    (
        # 0: DSN 포트를 듣는 서버가 없다(퇴역 instance의 포트가 남은 env).
        ({"pg-shared": (_PINVI_PORT, "shared_admin")}, r"found 0"),
        # 2: 같은 포트를 두 서버가 말한다 — 어느 cluster인지 모른다.
        (
            {"pg-a": (_MAP_PORT, "a_admin"), "pg-b": (_MAP_PORT, "b_admin")},
            r"found 2",
        ),
    ),
)
def test_instance_derivation_requires_exactly_one_postgres_service_per_port(
    servers: dict[str, tuple[int, str | None]],
    match: str,
) -> None:
    with pytest.raises(DeploymentContractError, match=match):
        database_runtimes_from_frozen_contract(
            resolved=_frozen_resolved(servers, pinvi_port=_PINVI_PORT),
            environment=_frozen_environment(map_port=_MAP_PORT),
        )


def test_instance_derivation_ignores_a_non_postgres_service_on_the_port() -> None:
    """같은 `-p`를 든 비-PostgreSQL 서비스는 instance 후보가 아니다(C6c 서버 판정)."""

    resolved = _frozen_resolved({"pg-shared": (_MAP_PORT, "shared_admin")}, pinvi_port=_MAP_PORT)
    services = resolved["services"]
    assert isinstance(services, dict)
    services["proxy"] = {"container_name": "proxy", "command": ["socat", "-p", str(_MAP_PORT)]}

    runtimes = database_runtimes_from_frozen_contract(
        resolved=resolved, environment=_frozen_environment(map_port=_MAP_PORT)
    )

    assert {runtime.service_name for runtime in runtimes} == {"pg-shared"}


def test_map_dsns_must_share_one_authority() -> None:
    with pytest.raises(DeploymentContractError, match="share one PostgreSQL authority"):
        database_runtimes_from_frozen_contract(
            resolved=_frozen_resolved(
                {"pg-a": (_MAP_PORT, "a_admin"), "pg-b": (_PINVI_PORT, "b_admin")},
                pinvi_port=_PINVI_PORT,
            ),
            environment=_frozen_environment(map_port=_MAP_PORT, dagster_port=_PINVI_PORT),
        )


@pytest.mark.parametrize(
    "dsn",
    (
        "",
        "postgresql+asyncpg://ktm_feature_service:s@localhost:15101/map_app",
        "postgresql+asyncpg://ktm_feature_service:s@127.0.0.1/map_app",
        "postgresql+asyncpg://ktm_feature_service:s@127.0.0.1:not-a-port/map_app",
    ),
)
def test_database_runtime_rejects_an_invalid_dsn_authority(dsn: str) -> None:
    with pytest.raises(DeploymentContractError, match="authority"):
        database_runtimes_from_frozen_contract(
            resolved=_frozen_resolved({"pg-shared": (_MAP_PORT, "shared_admin")}, pinvi_port=_MAP_PORT),
            environment=_frozen_environment(
                map_port=_MAP_PORT,
                KOR_TRAVEL_MAP_PG_DSN=dsn,
                KOR_TRAVEL_MAP_DAGSTER_PG_URL=dsn,
            ),
        )


def test_database_runtime_rejects_database_name_alias() -> None:
    with pytest.raises(DeploymentContractError, match="distinct frozen database names"):
        database_runtimes_from_frozen_contract(
            resolved=_frozen_resolved({"pg-shared": (_MAP_PORT, "shared_admin")}, pinvi_port=_MAP_PORT),
            environment=_frozen_environment(map_port=_MAP_PORT, PINVI_POSTGRES_DB="map_app"),
        )


@pytest.mark.parametrize("admin", [None, "", "cluster-admin"])
def test_database_runtime_rejects_invalid_frozen_admin_role(admin: str | None) -> None:
    with pytest.raises(DeploymentContractError, match="admin role"):
        database_runtimes_from_frozen_contract(
            resolved=_frozen_resolved({"pg-shared": (_MAP_PORT, admin)}, pinvi_port=_MAP_PORT),
            environment=_frozen_environment(map_port=_MAP_PORT),
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
        *[str(_MAP_PORT)] * 4,
        *[str(_PINVI_PORT)] * 2,
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
    """n150 모양(ADR-53: 세 DB가 공용 instance 하나)으로 frozen 계약에서 유도한 세 허용 집합.

    Map app과 Dagster는 구성상 `owner_name`(= instance admin, S1)을 공유하므로 "세 runtime 모두
    서로소"는 불가능하다. Map 쪽은 PinVi의 허용 집합과 instance admin과 겹치지 않는다. 다른 tenant
    login과의 서로소는 여기서 이름으로 보지 않는다 — 이 env에 없는 이름은 겹칠 수 없어 공허하다.
    그 경계는 live 소유 관계(배타성·회전 preflight)와 실 PostgreSQL T-R2d가 본다.
    """

    map_application, map_dagster, pinvi = database_runtimes_from_frozen_contract(
        resolved=_frozen_resolved({"pg-shared": (_MAP_PORT, "shared_admin")}, pinvi_port=_MAP_PORT),
        environment=_frozen_environment(
            map_port=_MAP_PORT,
            KOR_TRAVEL_MAP_POSTGRES_DB="kor_travel_map",
            KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB="kor_travel_map_dagster",
            KOR_TRAVEL_MAP_DAGSTER_METADATA_USER="kor_travel_map_dagster",
            PINVI_POSTGRES_DB="pinvi",
            PINVI_APP_DB_USER="pinvi_application_runtime",
        ),
    )
    permitted = database_runtime._permitted_existing_owners
    # instance admin은 frozen 문서의 `POSTGRES_USER`에서 온다 — 허용 집합에서 빠져야 한다.
    admins = {runtime.admin_name for runtime in (map_application, map_dagster, pinvi)}

    assert admins == {"shared_admin"}
    assert permitted(map_application) == {"ktm_feature_schema_owner"}
    assert permitted(map_dagster) == {"kor_travel_map_dagster"}
    assert permitted(pinvi) == {"pinvi_application_runtime"}
    for runtime in (map_application, map_dagster):
        assert permitted(runtime).isdisjoint(permitted(pinvi) | admins)
    assert permitted(pinvi).isdisjoint(admins)


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
) -> tuple[list[tuple[str, list[str]]], list[bytes]]:
    """슬롯 읽기만 답한다. 전제·수렴·read-back은 모두 한 transaction 스크립트 안에 있어야 한다."""

    reads: list[tuple[str, list[str]]] = []
    scripts: list[bytes] = []

    def run_checked(arguments: list[str], *, label: str) -> bytes:
        reads.append((label, list(arguments)))
        assert label.endswith("usable connection slots"), label
        return f"{usable}\n".encode()

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


def _do_block(script: str, tag: str) -> list[str]:
    """``DO $tag$ … $tag$;`` 블록들의 본문."""

    return [part.split(f"${tag}$;", 1)[0] for part in script.split(f"DO ${tag}$")[1:]]


def test_isolation_sql_names_only_map_databases_and_the_dsn_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads, scripts = _isolation_harness(monkeypatch)
    login = database_runtime.map_application_login(
        {
            "KOR_TRAVEL_MAP_PG_DSN": (
                "postgresql+asyncpg://ktm_feature_service:secret@127.0.0.1:15101/map_app"
            )
        }
    )

    database_runtime.ensure_map_databases_isolated(*_isolation_runtimes(), login=login)

    assert login == "ktm_feature_service"
    (script,) = (item.decode("ascii") for item in scripts)
    # 한 transaction 안의 순서: live 결박 → 바꾸기 → 같은 transaction의 read-back.
    statements = [
        "DO $r4_precondition$",
        'REVOKE CONNECT ON DATABASE "map_app" FROM PUBLIC;',
        "DO $r4_converge$",
        'GRANT CONNECT ON DATABASE "map_app" TO "ktm_feature_service";',
        'REVOKE CONNECT ON DATABASE "map_dagster" FROM PUBLIC;',
        'ALTER DATABASE "map_app" CONNECTION LIMIT 38;',
        "DO $r4_readback$",
    ]
    positions = [script.index(statement) for statement in statements]
    assert positions == sorted(positions)
    (precondition,) = _do_block(script, "r4_precondition")
    assert "WHERE datname = 'map_app'" in precondition
    assert "<> 'ktm_feature_schema_owner'" in precondition
    assert "role.rolname = 'ktm_feature_service'" in precondition
    assert "pg_catalog.pg_has_role(role.oid, app_owner, 'MEMBER')" in precondition
    assert "WHERE datname = 'map_dagster'" in precondition
    assert "<> 'map_dagster_metadata'" in precondition
    # 수렴은 app DB 하나에만 — Dagster DB는 소유자 이름으로만 결박되므로 PUBLIC만 걷는다.
    (converge,) = _do_block(script, "r4_converge")
    assert "database_row.datname = 'map_app'" in converge
    assert "<> 'ktm_feature_service'" in converge
    app_readback, dagster_readback = _do_block(script, "r4_readback")
    assert "WHERE datname = 'map_app'" in app_readback
    assert "ARRAY['ktm_feature_service']::text[]" in app_readback
    assert "datconnlimit <> 38" in app_readback
    assert "WHERE datname = 'map_dagster'" in dagster_readback
    assert "ARRAY['map_dagster_metadata']::text[]" in dagster_readback
    assert "datconnlimit <>" not in dagster_readback
    assert "pinvi" not in script
    # commit 뒤의 읽기가 없다 — 거부는 transaction 안에서 나고 전체가 롤백된다.
    assert [label for label, _ in reads] == ["map_application usable connection slots"]


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
    _, scripts = _isolation_harness(monkeypatch, usable="47")

    database_runtime.ensure_map_databases_isolated(
        *_isolation_runtimes(), login="ktm_feature_service"
    )

    assert b'ALTER DATABASE "map_app" CONNECTION LIMIT 18;\n' in scripts[0]
    assert b"datconnlimit <> 18" in scripts[0]


@pytest.mark.parametrize(
    "dsn",
    (
        "",
        "postgresql+asyncpg://127.0.0.1:15101/map_app",
        "postgresql+asyncpg://Bad-Login:x@127.0.0.1:15101/map_app",
        "postgresql+asyncpg://a%27b:x@127.0.0.1:15101/map_app",
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
            app, replace(dagster, port=_PINVI_PORT), login="ktm_feature_service"
        )

    assert reads == [] and scripts == []


_ISOLATION_ENTRY_POINTS = {
    "isolate": database_runtime.ensure_map_databases_isolated,
    "preflight": database_runtime.require_map_databases_isolatable,
}


def _silent_runners(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock]:
    """어떤 PostgreSQL 명령도 돌면 안 되는 경우의 기록기(읽기·스크립트 모두)."""

    reads, scripts = Mock(return_value=b"97\n"), Mock(return_value=b"")
    monkeypatch.setattr(database_runtime, "_run_checked", reads)
    monkeypatch.setattr(database_runtime, "_run_checked_with_input", scripts)
    return reads, scripts


@pytest.mark.parametrize("entry_point", tuple(_ISOLATION_ENTRY_POINTS))
@pytest.mark.parametrize("role", ("map_application", "map_dagster"))
@pytest.mark.parametrize("reserved", ("postgres", "template0", "template1", "template_postgis"))
def test_isolation_refuses_reserved_database_names_before_any_command(
    monkeypatch: pytest.MonkeyPatch, entry_point: str, role: str, reserved: str
) -> None:
    """R4도 권한을 바꾸는 경로다 — 이름 울타리가 live 전제보다 먼저, 명령 하나 없이 거부한다."""

    reads, scripts = _silent_runners(monkeypatch)
    app, dagster = _isolation_runtimes()
    if role == "map_application":
        app = replace(app, database_name=reserved)
    else:
        dagster = replace(dagster, database_name=reserved)

    with pytest.raises(DeploymentContractError, match="reserved cluster database"):
        _ISOLATION_ENTRY_POINTS[entry_point](app, dagster, login="ktm_feature_service")

    reads.assert_not_called()
    scripts.assert_not_called()


@pytest.mark.parametrize("entry_point", tuple(_ISOLATION_ENTRY_POINTS))
@pytest.mark.parametrize(
    "metadata_users",
    (frozenset(), frozenset({"map_dagster_metadata", "other_metadata"})),
    ids=("none", "two"),
)
def test_isolation_refuses_a_dagster_runtime_without_exactly_one_metadata_user(
    monkeypatch: pytest.MonkeyPatch, entry_point: str, metadata_users: frozenset[str]
) -> None:
    """Dagster DB의 CONNECT를 받을 login은 frozen metadata user **하나**다 — 고르지 않고 거부한다."""

    reads, scripts = _silent_runners(monkeypatch)
    app, dagster = _isolation_runtimes()

    with pytest.raises(DeploymentContractError, match="metadata role is not frozen"):
        _ISOLATION_ENTRY_POINTS[entry_point](
            app,
            replace(dagster, additional_owner_names=metadata_users),
            login="ktm_feature_service",
        )

    reads.assert_not_called()
    scripts.assert_not_called()


def _preflight_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dagster_owner: str | None,
) -> tuple[list[str], list[tuple[list[str], str, bytes]]]:
    """owner 읽기는 ``dagster_owner``로 답하고, 스크립트는 인자·label·본문을 남긴다."""

    owner_reads: list[str] = []
    scripts: list[tuple[list[str], str, bytes]] = []

    def read_owner(runtime: DatabaseRuntime) -> str | None:
        owner_reads.append(runtime.role)
        return dagster_owner if runtime.role == "map_dagster" else "ktm_feature_schema_owner"

    def run_with_input(arguments: list[str], *, input_bytes: bytes, label: str) -> bytes:
        scripts.append((list(arguments), label, input_bytes))
        return b""

    monkeypatch.setattr(database_runtime, "_read_database_owner", read_owner)
    monkeypatch.setattr(
        database_runtime, "_run_checked", Mock(side_effect=AssertionError("no other read"))
    )
    monkeypatch.setattr(database_runtime, "_run_checked_with_input", run_with_input)
    return owner_reads, scripts


def test_isolation_preflight_runs_the_same_precondition_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """멈추기 전의 판정은 R4 transaction의 **바로 그** 전제 블록이고, 그것만 READ ONLY로 돈다."""

    _, preflight_scripts = _preflight_harness(monkeypatch, dagster_owner="map_dagster_metadata")

    database_runtime.require_map_databases_isolatable(
        *_isolation_runtimes(), login="ktm_feature_service"
    )

    ((arguments, label, body),) = preflight_scripts
    script = body.decode("ascii")
    assert label == "Map database isolation preflight"
    assert "--single-transaction" in arguments
    assert arguments[arguments.index("--set") + 1] == "ON_ERROR_STOP=1"
    assert arguments[arguments.index("--dbname") + 1] == "postgres"
    assert script.startswith("SET TRANSACTION READ ONLY;\n")
    for mutation in ("REVOKE", "GRANT", "ALTER", "$r4_converge$", "$r4_readback$"):
        assert mutation not in script, mutation
    # 결박하는 R4 transaction의 전제와 글자까지 같다 — 정본은 하나다.
    _, isolation_scripts = _isolation_harness(monkeypatch)
    database_runtime.ensure_map_databases_isolated(
        *_isolation_runtimes(), login="ktm_feature_service"
    )
    (isolation,) = (item.decode("ascii") for item in isolation_scripts)
    assert _do_block(script, "r4_precondition") == _do_block(isolation, "r4_precondition")
    assert len(_do_block(script, "r4_precondition")) == 1


def test_isolation_preflight_leaves_an_absent_dagster_database_to_the_bound_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dagster DB가 없으면 init이 만든 뒤 R4 transaction이 판정한다 — 미리 보면 거짓 거부다."""

    owner_reads, scripts = _preflight_harness(monkeypatch, dagster_owner=None)

    database_runtime.require_map_databases_isolatable(
        *_isolation_runtimes(), login="ktm_feature_service"
    )

    assert owner_reads == ["map_dagster"]
    assert scripts == []


@pytest.mark.parametrize(
    ("owners", "owned", "message"),
    (
        (
            {"map_application": "cluster_admin", "map_dagster": None, "pinvi": None},
            {},
            "map_application database owner differs",
        ),
        (
            {
                "map_application": "ktm_feature_schema_owner",
                "map_dagster": "map_dagster_metadata",
                "pinvi": "pin_owner",
            },
            {
                # 오늘 n150 전용 instance의 모양: schema owner가 남은 검증 DB도 소유한다.
                "ktm_feature_schema_owner": "map_app\nktm_40b\nktm_gcverify\n",
                "map_dagster_metadata": "map_dagster\n",
            },
            "outside the Map pair",
        ),
    ),
    ids=("admin-owned", "schema-owner-owns-leftovers"),
)
def test_resettable_preflight_refuses_what_the_reset_refuses_and_only_reads(
    monkeypatch: pytest.MonkeyPatch,
    owners: dict[str, str | None],
    owned: dict[str, str],
    message: str,
) -> None:
    runtimes = (_dedicated_shape("map_application"), _metadata_runtime(), _runtime("pinvi"))
    commands = _record_commands(monkeypatch, owners=owners, owned=owned)

    with pytest.raises(DeploymentContractError, match=message):
        database_runtime.require_databases_resettable(runtimes)
    with pytest.raises(DeploymentContractError, match=message):
        reset_databases_for_application_300(runtimes)

    assert _dropped(commands) == []
    assert not any("createdb" in command for command in commands)


def test_resettable_preflight_passes_a_resettable_pair_without_dropping_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """대조군 — 같은 대역에서 리셋은 지우고, preflight는 읽기만 한다."""

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

    database_runtime.require_databases_resettable(runtimes)

    assert commands and all("psql" in command for command in commands)
    reset_databases_for_application_300(runtimes)
    assert _dropped(commands) == ["map_app", "map_dagster", "pin_app"]


# ── ADR-53 S1: Map bootstrap admin의 pre-stop 판정 ─────────────────────────────────────────

_ADMIN_PASSWORD = "shared-admin-password-0123456789-abcdef"


def _admin_ready_resolved() -> dict[str, object]:
    """admin secret을 `POSTGRES_PASSWORD_FILE` → secrets[] → 최상위 `environment`로 유도할 수 있는 문서."""

    server = _postgres_server("pg-shared-container", _MAP_PORT, "cluster_admin")
    server["environment"] = {
        "POSTGRES_USER": "cluster_admin",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/shared-admin-password",
    }
    server["secrets"] = [{"source": "shared-admin-password", "target": "/run/secrets/shared-admin-password"}]
    return {
        "services": {"pg-map": server},
        "secrets": {"shared-admin-password": {"environment": "SHARED_ADMIN_PASSWORD"}},
    }


def _admin_ready_environment(**overrides: str) -> dict[str, str]:
    return {
        "SHARED_ADMIN_PASSWORD": _ADMIN_PASSWORD,
        "KOR_TRAVEL_MAP_SERVICE_PASSWORD": "map-service-password-0123456789-abcdef",
        "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD": "map-dagster-password-0123456789-abcde",
        **overrides,
    }


def _admin_runtime() -> DatabaseRuntime:
    """S1 모양 — Map 앱 DB 소유자가 instance admin이다."""

    return replace(_runtime("map_application"), owner_name="cluster_admin")


def _admin_ready_runner(monkeypatch: pytest.MonkeyPatch, output: bytes) -> Mock:
    runner = Mock(return_value=output)
    monkeypatch.setattr(database_runtime, "_run_checked", runner)
    return runner


def test_admin_ready_check_accepts_a_ready_instance_and_only_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _admin_ready_runner(monkeypatch, b"t|0|2\n")

    database_runtime.require_map_bootstrap_admin_ready(
        _admin_runtime(),
        resolved=_admin_ready_resolved(),
        environment=_admin_ready_environment(),
    )

    (call,) = runner.call_args_list
    arguments = call.args[0]
    assert arguments[:5] == ["docker", "exec", "--user", "postgres", "postgres-rehearsal"]
    assert arguments[arguments.index("--username") + 1] == "cluster_admin"
    assert arguments[arguments.index("--port") + 1] == str(_MAP_PORT)
    query = arguments[arguments.index("--command") + 1]
    assert query.startswith("SELECT ")
    for fragment in (
        "rolsuper",
        "setting_row.setdatabase = 0",
        "setting_row.setrole = 0",
        "role.rolname = current_user",
        "LIKE 'ktm\\_%' ESCAPE '\\'",
        "'postgis'",
        "'pg_prewarm'",
    ):
        assert fragment in query
    assert _ADMIN_PASSWORD not in " ".join(arguments)


@pytest.mark.parametrize(
    ("output", "match"),
    (
        (b"f|0|2\n", "must be a superuser"),
        (b"t|1|2\n", "cluster-wide role settings"),
        (b"t|0|1\n", "lacks an extension"),
        (b"t|0\n", "output is invalid"),
        (b"t|0|2\nt|0|2\n", "output is invalid"),
    ),
)
def test_admin_ready_check_refuses_each_instance_precondition(
    monkeypatch: pytest.MonkeyPatch,
    output: bytes,
    match: str,
) -> None:
    _admin_ready_runner(monkeypatch, output)

    with pytest.raises(DeploymentContractError, match=match):
        database_runtime.require_map_bootstrap_admin_ready(
            _admin_runtime(),
            resolved=_admin_ready_resolved(),
            environment=_admin_ready_environment(),
        )


@pytest.mark.parametrize(
    ("overrides", "match"),
    (
        ({"SHARED_ADMIN_PASSWORD": "short"}, "32..256 URI-unreserved"),
        ({"SHARED_ADMIN_PASSWORD": "x" * 257}, "32..256 URI-unreserved"),
        ({"SHARED_ADMIN_PASSWORD": "has a space but is long enough 0123456"}, "32..256 URI-unreserved"),
        ({"SHARED_ADMIN_PASSWORD": ""}, "32..256 URI-unreserved"),
        ({"KOR_TRAVEL_MAP_SERVICE_PASSWORD": _ADMIN_PASSWORD}, "must differ"),
        ({"KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD": _ADMIN_PASSWORD}, "must differ"),
    ),
)
def test_admin_ready_check_refuses_an_unusable_admin_password_before_any_command(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
    match: str,
) -> None:
    runner = _admin_ready_runner(monkeypatch, b"t|0|2\n")

    with pytest.raises(DeploymentContractError, match=match) as raised:
        database_runtime.require_map_bootstrap_admin_ready(
            _admin_runtime(),
            resolved=_admin_ready_resolved(),
            environment=_admin_ready_environment(**overrides),
        )

    runner.assert_not_called()
    # 값은 거부 문구에 실리지 않는다 — 변수 이름만 말한다.
    assert "SHARED_ADMIN_PASSWORD" in str(raised.value)
    for value in overrides.values():
        if value:
            assert value not in str(raised.value)


def test_admin_ready_check_derives_the_secret_from_the_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """admin password 변수는 이름 목록이 아니라 그 instance의 secret에서 온다 — 끊기면 거부한다."""

    runner = _admin_ready_runner(monkeypatch, b"t|0|2\n")
    resolved = _admin_ready_resolved()
    services = resolved["services"]
    assert isinstance(services, dict)
    services["pg-map"]["secrets"] = []

    with pytest.raises(DeploymentContractError, match="admin secret is not derivable"):
        database_runtime.require_map_bootstrap_admin_ready(
            _admin_runtime(),
            resolved=resolved,
            environment=_admin_ready_environment(),
        )
    runner.assert_not_called()


def test_admin_ready_check_requires_the_s1_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _admin_ready_runner(monkeypatch, b"t|0|2\n")

    for runtime in (_runtime("map_application"), _runtime("pinvi")):
        with pytest.raises(DeploymentContractError, match="owned by the instance admin"):
            database_runtime.require_map_bootstrap_admin_ready(
                runtime,
                resolved=_admin_ready_resolved(),
                environment=_admin_ready_environment(),
            )
    runner.assert_not_called()
