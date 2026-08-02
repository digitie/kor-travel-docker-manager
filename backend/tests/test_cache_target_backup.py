from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

import kor_travel_docker_manager.services.cache_target_backup as backup_service
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_backup import (
    _COUPLED_ROLLBACK_CAPABILITY,
    DatabaseRuntime,
    DatabaseWriteCounter,
    create_database_backup,
    create_manager_rollback_bundle,
    database_identity_v1,
    database_runtimes_from_frozen_contract,
    read_database_schema_revision,
    read_pin_boundary_audit,
    restore_database_backup,
    restore_manager_rollback_bundle,
    verify_database_backup,
    verify_manager_rollback_bundle,
)
from kor_travel_docker_manager.services.cache_target_window import (
    prepare_cache_target_window,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
    cache_target_writer_registry_sha256,
)

_TRANSACTION_ID = "11111111-1111-4111-8111-111111111111"


def test_database_identity_v1_matches_cross_repository_golden_vector() -> None:
    assert database_identity_v1(
        transaction_id="00000000-0000-0000-0000-000000000001",
        role="map_application",
        database_name="kor_travel_map",
        system_identifier="12345678901234567890",
    ) == "9bca9b82ad2304759581ebf16e724461fcfd7c657e2b41ce5ae3ae54847dee5a"


def test_pin_boundary_audit_requires_one_exact_typed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = {
        "audit_id": _TRANSACTION_ID,
        "audit_request_sha256": "1" * 64,
        "evidence_sha256": "2" * 64,
        "map_final_evidence_sha256": "3" * 64,
        "initial_writer_fence_sha256": "4" * 64,
        "final_writer_fence_sha256": "5" * 64,
        "prior_receipt_sha256": "6" * 64,
        "canary_run_id": "22222222-2222-4222-8222-222222222222",
    }
    runner = Mock(
        return_value=(json.dumps(document, separators=(",", ":")) + "\n").encode()
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.cache_target_backup._run_checked",
        runner,
    )
    runtime = DatabaseRuntime(
        role="pinvi",
        container_name="postgres-production",
        database_name="pinvi",
        owner_name="pinvi",
        admin_name="cluster_admin",
    )

    assert read_pin_boundary_audit(runtime, _TRANSACTION_ID).evidence_sha256 == "2" * 64
    command = runner.call_args.args[0]
    assert f"transaction_id={_TRANSACTION_ID}" in command
    assert command[command.index("--user") + 1] == "postgres"
    assert command[command.index("--username") + 1] == "cluster_admin"
    assert not any("password" in argument.lower() for argument in command)
    assert command[-1].count("encode(") == 6
    assert "\\x" not in command[-1]

    runner.return_value = b""
    with pytest.raises(DeploymentContractError, match="audit row"):
        read_pin_boundary_audit(runtime, _TRANSACTION_ID)


def test_restore_rehearsal_never_drops_foreign_owned_scratch_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(side_effect=AssertionError("foreign scratch must not be mutated"))
    monkeypatch.setattr(backup_service, "_validate_archive_structure", Mock())
    monkeypatch.setattr(
        backup_service,
        "_read_database_owner",
        Mock(return_value="foreign_owner"),
    )
    monkeypatch.setattr(backup_service, "_run_checked", runner)

    with pytest.raises(DeploymentContractError, match="foreign role"):
        backup_service._rehearse_database_restore(
            backup_path=tmp_path / "backup.dump",
            runtime=DatabaseRuntime(
                role="map_application",
                container_name="postgres-production",
                database_name="map_app",
                owner_name="map_owner",
                admin_name="cluster_admin",
            ),
            transaction_id=_TRANSACTION_ID,
            source_database_identity="1" * 64,
            archive_sha256="2" * 64,
            expected_schema_revision="0063_pipeline_root_id",
            expected_schema_inventory_sha256="3" * 64,
            expected_data_inventory_sha256="4" * 64,
        )

    runner.assert_not_called()


def test_writer_registry_matches_cross_repository_golden_vector() -> None:
    assert cache_target_writer_registry_sha256(_writer_services()) == (
        "526240609e2919357699b90244eb8cc8b9505f37db6c60552a98c7a37ed22d7c"
    )


def test_database_runtime_identity_comes_from_frozen_contract() -> None:
    resolved = {
        "services": {
            "kor-travel-geo-postgres": {
                "container_name": "postgres-production",
                "environment": {"POSTGRES_USER": "cluster_admin"},
            }
        }
    }
    environment = {
        "KRTOUR_MAP_POSTGRES_DB": "map_app",
        "KRTOUR_MAP_DAGSTER_POSTGRES_DB": "map_dagster",
        "KRTOUR_MAP_POSTGRES_USER": "map_owner",
        "PINVI_POSTGRES_DB": "pin_app",
        "PINVI_POSTGRES_USER": "pin_owner",
    }

    runtimes = database_runtimes_from_frozen_contract(
        resolved=resolved,
        environment=environment,
    )

    assert [
        (runtime.role, runtime.database_name, runtime.owner_name, runtime.admin_name)
        for runtime in runtimes
    ] == [
        ("map_application", "map_app", "map_owner", "cluster_admin"),
        ("map_dagster", "map_dagster", "map_owner", "cluster_admin"),
        ("pinvi", "pin_app", "pin_owner", "cluster_admin"),
    ]
    assert {runtime.container_name for runtime in runtimes} == {
        "postgres-production"
    }


@pytest.mark.parametrize(
    "postgres_environment",
    [
        {},
        {"POSTGRES_USER": ""},
        {"POSTGRES_USER": "cluster-admin"},
        {"POSTGRES_USER": ["cluster_admin"]},
    ],
)
def test_database_runtime_rejects_missing_or_invalid_canonical_admin_role(
    postgres_environment: object,
) -> None:
    resolved = {
        "services": {
            "kor-travel-geo-postgres": {
                "container_name": "postgres-production",
                "environment": postgres_environment,
            }
        }
    }

    with pytest.raises(DeploymentContractError, match="admin role"):
        database_runtimes_from_frozen_contract(
            resolved=resolved,
            environment={},
        )


@pytest.mark.parametrize(
    ("role", "database_name", "canonical_table"),
    [
        ("map_application", "map_app", '"public"."alembic_version"'),
        ("map_dagster", "map_dagster", '"public"."alembic_version"'),
        ("pinvi", "pin_app", '"app"."alembic_version"'),
    ],
)
def test_schema_revision_uses_role_canonical_table_despite_search_path_poison(
    role: backup_service.DatabaseRole,
    database_name: str,
    canonical_table: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_query = f"SELECT version_num FROM {canonical_table}"

    def run_checked(arguments: list[str], *, label: str) -> bytes:
        del label
        if arguments[-1] == expected_query:
            return b"canonical_revision\n"
        return b"poison_same_name_revision\n"

    runner = Mock(side_effect=run_checked)
    monkeypatch.setattr(backup_service, "_run_checked", runner)
    runtime = DatabaseRuntime(
        role=role,
        container_name="postgres-production",
        database_name=database_name,
        owner_name="database_owner",
        admin_name="cluster_admin",
    )

    assert read_database_schema_revision(runtime) == "canonical_revision"
    command = runner.call_args.args[0]
    assert command[command.index("--dbname") + 1] == database_name
    assert command[-1] == expected_query
    assert "FROM alembic_version" not in command[-1]


def test_schema_revision_rejects_ambiguous_canonical_table_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backup_service,
        "_run_checked",
        Mock(return_value=b"canonical_revision\npoison_same_name_revision\n"),
    )
    runtime = DatabaseRuntime(
        role="map_application",
        container_name="postgres-production",
        database_name="map_app",
        owner_name="map_owner",
        admin_name="cluster_admin",
    )

    with pytest.raises(DeploymentContractError, match="revision output"):
        read_database_schema_revision(runtime)


def test_backup_and_restore_stream_without_dsn_or_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        if "pg_dump" in arguments:
            kwargs["stdout"].write(b"typed-custom-dump")
            return subprocess.CompletedProcess(arguments, 0, stdout=b"", stderr=b"")
        if "pg_restore" in arguments:
            assert kwargs["stdin"].read() == b"typed-custom-dump"
            return subprocess.CompletedProcess(arguments, 0, stdout=b"", stderr=b"")
        if "psql" in arguments:
            query = arguments[-1]
            stdout = (
                b"0063_pipeline_root_id\n"
                if "version_num" in query
                else (
                    b"map_owner\n"
                    if "pg_get_userbyid" in query
                    else b"7493039601109800889\n"
                )
            )
            return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr=b"")
        return subprocess.CompletedProcess(arguments, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(
        "kor_travel_docker_manager.services.cache_target_backup.subprocess.run",
        run,
    )
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    runtime = DatabaseRuntime(
        role="map_application",
        container_name="postgres-production",
        database_name="map_app",
        owner_name="map_owner",
        admin_name="cluster_admin",
    )

    receipt = create_database_backup(
        state_directory=state_directory,
        transaction_id=_TRANSACTION_ID,
        runtime=runtime,
        writer_fence_sha256="f" * 64,
    )
    verify_database_backup(
        state_directory=state_directory,
        transaction_id=_TRANSACTION_ID,
        runtime=runtime,
        receipt=receipt,
        writer_fence_sha256="f" * 64,
    )
    restore_database_backup(
        state_directory=state_directory,
        transaction_id=_TRANSACTION_ID,
        runtime=runtime,
        receipt=receipt,
        capability=_COUPLED_ROLLBACK_CAPABILITY,
    )

    backup_path = (
        state_directory
        / f"cache-target-window-{_TRANSACTION_ID}"
        / "map_application.dump"
    )
    assert backup_path.read_bytes() == b"typed-custom-dump"
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    assert receipt.schema_revision == "0063_pipeline_root_id"
    assert receipt.database_identity == (
        "8a9bc0341d62ad8e089acb8cb5f38090e99d39a928a858a11a787f4b7475540c"
    )
    assert receipt.restore_rehearsal.database_identity != receipt.database_identity
    assert receipt.restore_rehearsal.source_database_identity == receipt.database_identity
    assert receipt.restore_rehearsal.archive_sha256 == receipt.sha256
    serialized_commands = "\n".join(" ".join(command) for command in calls)
    assert "postgresql://" not in serialized_commands
    assert "password" not in serialized_commands.lower()
    assert "secret" not in serialized_commands.lower()
    database_commands = {
        command[command.index("postgres-production") + 1]
        for command in calls
        if command[0:2] == ["docker", "exec"]
        and command[command.index("postgres-production") + 1]
        in {"psql", "pg_dump", "pg_restore", "dropdb", "createdb"}
    }
    assert database_commands == {"psql", "pg_dump", "pg_restore", "dropdb", "createdb"}
    for command in calls:
        if not any(executable in command for executable in database_commands):
            continue
        assert command[command.index("--user") + 1] == "postgres"
        assert command[command.index("--username") + 1] == "cluster_admin"
        assert "--username=postgres" not in command
    assert "cluster_admin" not in repr(receipt)
    restore_commands = [
        command[command.index("postgres-production") + 1]
        for command in calls
        if command[0:2] == ["docker", "exec"]
        and command[command.index("postgres-production") + 1]
        in {"dropdb", "createdb", "pg_restore"}
    ]
    assert restore_commands[-3:] == ["dropdb", "createdb", "pg_restore"]
    scratch_lifecycle = [
        command[command.index("postgres-production") + 1]
        for command in calls
        if command[0:2] == ["docker", "exec"]
        and command[-1].startswith("ktdm_map_application_")
        and command[command.index("postgres-production") + 1]
        in {"dropdb", "createdb"}
    ]
    assert scratch_lifecycle[:2] == ["dropdb", "createdb"]


def test_logical_inventory_accepts_pg_dump_restore_advisory_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"deterministic-logical-inventory"
    advisory = (
        b"pg_dump: warning: there are circular foreign-key constraints on this table:\n"
        b"pg_dump: detail: features\n"
        b"pg_dump: hint: You might not be able to restore the dump without using "
        b"--disable-triggers or temporarily dropping the constraints.\n"
        b"pg_dump: hint: Consider using a full dump instead of a --data-only dump "
        b"to avoid this problem.\n"
        b"pg_dump: warning: there are circular foreign-key constraints among these tables:\n"
        b"pg_dump: detail: source_records\n"
        b"pg_dump: detail: source_entities\n"
        b"pg_dump: hint: You might not be able to restore the dump without using "
        b"--disable-triggers or temporarily dropping the constraints.\n"
        b"pg_dump: hint: Consider using a full dump instead of a --data-only dump "
        b"to avoid this problem.\n"
    )

    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        kwargs["stdout"].write(payload)
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout=b"",
            stderr=advisory,
        )

    monkeypatch.setattr(backup_service.subprocess, "run", run)
    runtime = DatabaseRuntime(
        role="map_application",
        container_name="postgres-production",
        database_name="map_app",
        owner_name="map_owner",
        admin_name="cluster_admin",
    )

    assert backup_service._logical_inventory_sha256(  # noqa: SLF001
        runtime,
        schema_only=False,
    ) == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    ("schema_only", "stderr"),
    [
        (
            True,
            b"pg_dump: warning: there are circular foreign-key constraints on this table:\n"
            b"pg_dump: detail: features\n"
            b"pg_dump: hint: You might not be able to restore the dump without using "
            b"--disable-triggers or temporarily dropping the constraints.\n"
            b"pg_dump: hint: Consider using a full dump instead of a --data-only dump "
            b"to avoid this problem.\n",
        ),
        (
            False,
            b"pg_dump: warning: there are circular foreign-key constraints on this table:\n"
            b"pg_dump: detail: features\n"
            b"pg_dump: hint: You might not be able to restore the dump without using "
            b"--disable-triggers or temporarily dropping the constraints.\n"
            b"pg_dump: hint: Consider using a full dump instead of a --data-only dump "
            b"to avoid this problem.\n"
            b"pg_dump: warning: unknown logical inventory warning\n",
        ),
    ],
)
def test_logical_inventory_rejects_schema_or_unknown_warning(
    monkeypatch: pytest.MonkeyPatch,
    schema_only: bool,
    stderr: bytes,
) -> None:
    def run(arguments: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(arguments, 0, stdout=b"", stderr=stderr)

    monkeypatch.setattr(backup_service.subprocess, "run", run)
    runtime = DatabaseRuntime(
        role="map_application",
        container_name="postgres-production",
        database_name="map_app",
        owner_name="map_owner",
        admin_name="cluster_admin",
    )

    with pytest.raises(DeploymentContractError, match="logical inventory failed"):
        backup_service._logical_inventory_sha256(runtime, schema_only=schema_only)  # noqa: SLF001


def test_logical_inventory_rejects_pg_dump_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(arguments: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            arguments,
            1,
            stdout=b"",
            stderr=b"pg_dump: fatal: failed\n",
        )

    monkeypatch.setattr(backup_service.subprocess, "run", run)
    runtime = DatabaseRuntime(
        role="map_application",
        container_name="postgres-production",
        database_name="map_app",
        owner_name="map_owner",
        admin_name="cluster_admin",
    )

    with pytest.raises(DeploymentContractError, match="logical inventory failed"):
        backup_service._logical_inventory_sha256(runtime, schema_only=True)  # noqa: SLF001


def test_manager_bundle_restores_env_manifest_and_exact_state(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    env_path = tmp_path / ".env"
    manifest_path = state_directory / "compatible-pair-v4.json"
    initial_path = state_directory / "cache-target-initial-cutover-v1.json"
    for path, payload in (
        (env_path, b"SYNC=false\n"),
        (manifest_path, b'{"version":4}\n'),
        (initial_path, b'{"version":1}\n'),
    ):
        path.write_bytes(payload)
        path.chmod(0o600)

    bundle_sha = create_manager_rollback_bundle(
        state_directory=state_directory,
        transaction_id=_TRANSACTION_ID,
        env_path=env_path,
        manifest_path=manifest_path,
        environment_bytes=env_path.read_bytes(),
        manifest_bytes=manifest_path.read_bytes(),
    )
    verify_manager_rollback_bundle(
        state_directory=state_directory,
        transaction_id=_TRANSACTION_ID,
        expected_sha256=bundle_sha,
    )
    env_path.write_bytes(b"SYNC=true\n")
    manifest_path.write_bytes(b'{"version":4,"new":true}\n')
    initial_path.unlink()
    enable_path = state_directory / "cache-target-enable-v1.json"
    enable_path.write_bytes(b'{"phase":"committed"}\n')
    enable_path.chmod(0o600)

    restore_manager_rollback_bundle(
        state_directory=state_directory,
        transaction_id=_TRANSACTION_ID,
        expected_sha256=bundle_sha,
        env_path=env_path,
        manifest_path=manifest_path,
    )

    assert env_path.read_bytes() == b"SYNC=false\n"
    assert manifest_path.read_bytes() == b'{"version":4}\n'
    assert initial_path.read_bytes() == b'{"version":1}\n'
    assert not enable_path.exists()
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("failure_mode", ["restore", "schema", "content"])
def test_backup_rehearsal_rejects_restore_schema_and_content_mismatch(
    failure_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        if "pg_dump" in arguments:
            database = arguments[arguments.index("--dbname") + 1]
            if "--format=custom" in arguments:
                payload = b"corrupt-but-listable-archive"
            elif (
                failure_mode == "content"
                and database.startswith("ktdm_")
                and "--data-only" in arguments
            ):
                payload = b"foreign-logical-content"
            else:
                payload = b"expected-logical-content"
            kwargs["stdout"].write(payload)
            return subprocess.CompletedProcess(arguments, 0, stdout=b"", stderr=b"")
        if "pg_restore" in arguments:
            kwargs["stdin"].read()
            failed = failure_mode == "restore" and "--list" not in arguments
            return subprocess.CompletedProcess(
                arguments,
                1 if failed else 0,
                stdout=b"",
                stderr=b"restore failed" if failed else b"",
            )
        if "psql" in arguments:
            database = arguments[arguments.index("--dbname") + 1]
            query = arguments[-1]
            if "system_identifier" in query:
                stdout = b"7493039601109800889\n"
            elif "pg_get_userbyid" in query:
                stdout = b""
            elif failure_mode == "schema" and database.startswith("ktdm_"):
                stdout = b"0078_cache_target_gc_observe\n"
            else:
                stdout = b"0063_pipeline_root_id\n"
            return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr=b"")
        return subprocess.CompletedProcess(arguments, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(
        "kor_travel_docker_manager.services.cache_target_backup.subprocess.run",
        run,
    )
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    runtime = DatabaseRuntime(
        role="map_application",
        container_name="postgres-production",
        database_name="map_app",
        owner_name="map_owner",
        admin_name="cluster_admin",
    )

    with pytest.raises(DeploymentContractError, match="restore rehearsal failed"):
        create_database_backup(
            state_directory=state_directory,
            transaction_id=_TRANSACTION_ID,
            runtime=runtime,
            writer_fence_sha256="f" * 64,
        )

    dropped_databases = [
        command[-1]
        for command in calls
        if "dropdb" in command
    ]
    assert dropped_databases
    assert all(database.startswith("ktdm_") for database in dropped_databases)
    assert "map_app" not in dropped_databases


@pytest.mark.parametrize("registry_failure", ["missing", "unknown"])
def test_writer_registry_failure_has_zero_docker_mutation(
    registry_failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    services = _writer_services()
    if registry_failure == "missing":
        del services["pinvi-dagster"]
    else:
        services["foreign-writer"] = {
            "environment": {"PINVI_DATABASE_URL": "redacted"}
        }
    transaction = SimpleNamespace(resolved={"services": services})
    runner = Mock()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_database_inflight_count",
        Mock(side_effect=AssertionError("DB probe must follow registry validation")),
    )

    with pytest.raises(DeploymentContractError, match="registry"):
        service._establish_cache_target_writer_fence(
            journal=_writer_journal(),
            transaction=transaction,
            runtimes=_writer_runtimes(),
        )

    runner.assert_not_called()


def test_inflight_writer_failure_has_zero_docker_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    transaction = SimpleNamespace(resolved={"services": _writer_services()})
    runner = Mock()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_database_inflight_count",
        Mock(side_effect=[1, 0, 0]),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_dagster_inflight_run_count",
        Mock(return_value=0),
    )

    with pytest.raises(DeploymentContractError, match="in-flight"):
        service._establish_cache_target_writer_fence(
            journal=_writer_journal(),
            transaction=transaction,
            runtimes=_writer_runtimes(),
        )

    runner.assert_not_called()


def test_post_backup_fence_revalidates_inflight_and_dagster_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    transaction = SimpleNamespace(resolved={"services": _writer_services()})
    _mock_global_writer_inventory(monkeypatch)
    monkeypatch.setattr(
        service,
        "_snapshot_service_states",
        Mock(return_value={name: "exited" for name in _writer_services()}),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_database_inflight_count",
        Mock(side_effect=[0, 1, 0]),
    )
    dagster = Mock(return_value=0)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_dagster_inflight_run_count",
        dagster,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_database_write_counter",
        Mock(
            return_value=DatabaseWriteCounter(
                inserted=1,
                updated=2,
                deleted=3,
                stats_reset_identity="never",
            )
        ),
    )

    with pytest.raises(DeploymentContractError, match="retained in-flight"):
        service._revalidate_cache_target_writer_fence(
            journal=_writer_journal(),
            transaction=transaction,
            runtimes=_writer_runtimes(),
            expected_writer_fence_sha256="f" * 64,
        )

    dagster.assert_called_once()


def test_final_fence_allows_only_pin_audit_counter_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    transaction = SimpleNamespace(resolved={"services": _writer_services()})
    _mock_global_writer_inventory(monkeypatch)
    monkeypatch.setattr(
        service,
        "_snapshot_service_states",
        Mock(return_value={name: "exited" for name in _writer_services()}),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_database_inflight_count",
        Mock(return_value=0),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_dagster_inflight_run_count",
        Mock(return_value=0),
    )
    map_app = DatabaseWriteCounter(10, 11, 12, "never")
    map_dagster = DatabaseWriteCounter(20, 21, 22, "never")
    pin_before = DatabaseWriteCounter(30, 31, 32, "never")
    pin_after = DatabaseWriteCounter(31, 31, 32, "never")
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_database_write_counter",
        Mock(
            side_effect=[
                map_app,
                map_dagster,
                pin_before,
                map_app,
                map_dagster,
                pin_after,
            ]
        ),
    )

    before_fence, before_counters = service._read_cache_target_writer_fence_evidence(
        journal=_writer_journal(),
        transaction=transaction,
        runtimes=_writer_runtimes(),
        ordered_writers=tuple(sorted(_writer_services())),
        boundary="final",
    )
    after_fence, after_counters = service._read_cache_target_writer_fence_evidence(
        journal=_writer_journal(),
        transaction=transaction,
        runtimes=_writer_runtimes(),
        ordered_writers=tuple(sorted(_writer_services())),
        boundary="final",
    )

    assert before_fence == after_fence
    assert (
        service._cache_target_map_write_counters_sha256(before_counters)
        == service._cache_target_map_write_counters_sha256(after_counters)
    )
    map_changed = (DatabaseWriteCounter(11, 11, 12, "never"), *after_counters[1:])
    assert (
        service._cache_target_map_write_counters_sha256(map_changed)
        != service._cache_target_map_write_counters_sha256(after_counters)
    )


def test_writer_fence_rejects_absent_runtime_before_database_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    writers = tuple(sorted(_writer_services()))
    states = {name: "exited" for name in writers}
    states[writers[0]] = "absent"
    monkeypatch.setattr(service, "_snapshot_service_states", Mock(return_value=states))
    database_probe = Mock(side_effect=AssertionError("must follow exact runtime proof"))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_database_inflight_count",
        database_probe,
    )

    with pytest.raises(DeploymentContractError, match="five stopped runtimes"):
        service._read_cache_target_writer_fence_evidence(
            journal=_writer_journal(),
            transaction=SimpleNamespace(resolved={"services": _writer_services()}),
            runtimes=_writer_runtimes(),
            ordered_writers=writers,
            boundary="final",
        )

    database_probe.assert_not_called()


def _writer_services() -> dict[str, dict[str, object]]:
    return {
        "kor-travel-map-api": {
            "container_name": "kor-travel-map-api-latest",
            "environment": {
                "KOR_TRAVEL_MAP_PG_DSN": (
                    "postgresql://map:redacted@postgres/map"
                )
            },
        },
        "kor-travel-map-dagster": {
            "container_name": "kor-travel-map-dagster-latest",
            "environment": {
                "KOR_TRAVEL_MAP_DAGSTER_PG_URL": (
                    "postgresql://map:redacted@postgres/map_dagster"
                )
            },
        },
        "kor-travel-map-dagster-daemon": {
            "container_name": "kor-travel-map-dagster-daemon-latest",
            "environment": {
                "KOR_TRAVEL_MAP_DAGSTER_PG_URL": (
                    "postgresql://map:redacted@postgres/map_dagster"
                )
            },
        },
        "pinvi-api": {
            "container_name": "pinvi-api-latest",
            "environment": {
                "PINVI_DATABASE_URL": (
                    "postgresql://pinvi:redacted@postgres/pinvi"
                )
            },
        },
        "pinvi-dagster": {
            "container_name": "pinvi-dagster-latest",
            "environment": {
                "PINVI_DATABASE_URL": (
                    "postgresql://pinvi:redacted@postgres/pinvi"
                )
            },
        },
    }


def _mock_global_writer_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.attest_cache_target_global_writer_fence",
        Mock(
            return_value=SimpleNamespace(
                contract_version="ktdm-cache-target-global-writer-fence/v1",
                inventory_sha256="e" * 64,
                protected_target_count=3,
                expected_stopped_writer_count=5,
            )
        ),
    )


def _writer_runtimes() -> tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime]:
    return (
        DatabaseRuntime("map_application", "postgres", "map", "map", "cluster_admin"),
        DatabaseRuntime("map_dagster", "postgres", "map_dagster", "map", "cluster_admin"),
        DatabaseRuntime("pinvi", "postgres", "pinvi", "pinvi", "cluster_admin"),
    )


def _writer_journal():
    return prepare_cache_target_window(
        transaction_id=_TRANSACTION_ID,
        cutover_id="22222222-2222-4222-8222-222222222222",
        expected_restore_epoch=3,
        reason="writer fence test",
        environment_sha256="1" * 64,
        compose_sha256="2" * 64,
        resolved_compose_sha256="3" * 64,
        old_manifest_sha256="4" * 64,
    )
