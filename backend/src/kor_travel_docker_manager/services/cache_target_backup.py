from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_window import (
    DatabaseBackupReceipt,
    DatabaseRestoreRehearsalReceipt,
)

DatabaseRole = Literal["map_application", "map_dagster", "pinvi"]

_DATABASE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_SCHEMA_REVISION = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
# n150 production에서 map_application(krtour_map)의 feature.feature_weather_values
# 단일 테이블(1,780만 행)만으로 pg_restore(COPY + constraint + 4개 index 재생성)가
# 실측 약 97분 걸렸다(2026-08-03, cache-target diagnose 실측). stdin으로 archive를
# 스트리밍하는 현재 구조는 pg_restore --jobs 병렬화를 쓸 수 없어(seekable archive가
# 필요) 단일 스레드로 순차 처리한다. 이 값은 그 실측에 여유를 더한 잠정 값이며,
# 테이블이 계속 커지면 다시 부족해진다 — 근본 해결(파일 기반 병렬 restore 등)은
# 별도 후속 작업이 필요하다.
_DATABASE_RESTORE_TIMEOUT_SECONDS = 10_800
_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_ROLE_CONFIG: dict[DatabaseRole, tuple[str, str, str, str]] = {
    "map_application": (
        "KRTOUR_MAP_POSTGRES_DB",
        "krtour_map",
        "KRTOUR_MAP_POSTGRES_USER",
        "krtour_map",
    ),
    "map_dagster": (
        "KRTOUR_MAP_DAGSTER_POSTGRES_DB",
        "krtour_map_dagster",
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
_MANAGER_STATE_FILENAMES = {
    "map_env_migration": "map-production-env-migration-v1.json",
    "initial_receipt": "cache-target-initial-cutover-v1.json",
    "enable_journal": "cache-target-enable-v1.json",
}
_CIRCULAR_FOREIGN_KEY_WARNING_HEADINGS = frozenset(
    {
        "pg_dump: warning: there are circular foreign-key constraints on this table:",
        "pg_dump: warning: there are circular foreign-key constraints among these tables:",
    }
)
_CIRCULAR_FOREIGN_KEY_WARNING_HINTS = (
    "pg_dump: hint: You might not be able to restore the dump without using "
    "--disable-triggers or temporarily dropping the constraints.",
    "pg_dump: hint: Consider using a full dump instead of a --data-only dump "
    "to avoid this problem.",
)


@dataclass(frozen=True)
class DatabaseRuntime:
    role: DatabaseRole
    container_name: str
    database_name: str
    owner_name: str
    admin_name: str


@dataclass(frozen=True)
class DatabaseWriteCounter:
    inserted: int
    updated: int
    deleted: int
    stats_reset_identity: str


@dataclass(frozen=True)
class PinBoundaryAuditRow:
    audit_id: str
    audit_request_sha256: str
    evidence_sha256: str
    map_final_evidence_sha256: str
    initial_writer_fence_sha256: str
    final_writer_fence_sha256: str
    prior_receipt_sha256: str
    canary_run_id: str


class _CoupledRollbackCapability:
    __slots__ = ()


_COUPLED_ROLLBACK_CAPABILITY = _CoupledRollbackCapability()


def database_runtimes_from_frozen_contract(
    *,
    resolved: Mapping[str, object],
    environment: Mapping[str, str],
) -> tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime]:
    services = resolved.get("services")
    postgres = services.get("kor-travel-geo-postgres") if isinstance(services, Mapping) else None
    container_name = (
        postgres.get("container_name") if isinstance(postgres, Mapping) else None
    )
    if not isinstance(container_name, str) or not _CONTAINER_NAME.fullmatch(container_name):
        raise DeploymentContractError(
            "cutover PostgreSQL container identity is invalid"
        )
    postgres_environment = (
        postgres.get("environment") if isinstance(postgres, Mapping) else None
    )
    admin_name = (
        postgres_environment.get("POSTGRES_USER")
        if isinstance(postgres_environment, Mapping)
        else None
    )
    if not isinstance(admin_name, str) or not _DATABASE_IDENTIFIER.fullmatch(
        admin_name
    ):
        raise DeploymentContractError("cutover PostgreSQL admin role is invalid")
    runtimes: list[DatabaseRuntime] = []
    for role, (database_env, database_default, owner_env, owner_default) in (
        _ROLE_CONFIG.items()
    ):
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


def assert_cutover_backup_space_available(
    *,
    state_directory: Path,
    runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
) -> None:
    database_sizes = [_read_database_size(runtime) for runtime in runtimes]
    required_backup_bytes = sum(database_sizes)
    required_scratch_bytes = max(database_sizes)
    try:
        host_free = shutil.disk_usage(state_directory).free
    except OSError as exc:
        raise DeploymentContractError("cutover backup free space is unavailable") from exc
    postgres_free = _read_postgres_free_bytes(runtimes[0])
    if host_free < required_backup_bytes * 2:
        raise DeploymentContractError("cutover backup host space is insufficient")
    if postgres_free < required_scratch_bytes * 2:
        raise DeploymentContractError("cutover scratch database space is insufficient")


def read_database_write_counter(runtime: DatabaseRuntime) -> DatabaseWriteCounter:
    _validate_runtime(runtime)
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            runtime.database_name,
            "--command",
            (
                "SELECT tup_inserted, tup_updated, tup_deleted, "
                "COALESCE(stats_reset::text, 'never') "
                "FROM pg_stat_database WHERE datname = current_database()"
            ),
        ],
        label=f"{runtime.role} database write counter",
    ).decode("ascii").strip()
    parts = output.split("|")
    if (
        len(parts) != 4
        or any(not part.isdigit() for part in parts[:3])
        or not parts[3]
        or len(parts[3]) > 64
        or not parts[3].isascii()
    ):
        raise DeploymentContractError(
            f"{runtime.role} database write counter output is invalid"
        )
    return DatabaseWriteCounter(
        inserted=int(parts[0]),
        updated=int(parts[1]),
        deleted=int(parts[2]),
        stats_reset_identity=parts[3],
    )


def read_database_schema_revision(runtime: DatabaseRuntime) -> str:
    return _read_schema_revision(runtime)


def read_database_identity(runtime: DatabaseRuntime, transaction_id: str) -> str:
    return _read_database_identity(runtime, transaction_id)


def read_database_inflight_count(runtime: DatabaseRuntime) -> int:
    _validate_runtime(runtime)
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            runtime.database_name,
            "--command",
            (
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND state <> 'idle'"
            ),
        ],
        label=f"{runtime.role} database in-flight transaction count",
    ).decode("ascii").strip()
    if not output.isdigit():
        raise DeploymentContractError(
            f"{runtime.role} database in-flight count is invalid"
        )
    return int(output)


def read_dagster_inflight_run_count(runtime: DatabaseRuntime) -> int:
    if runtime.role != "map_dagster":
        raise DeploymentContractError(
            "Dagster in-flight run query requires the Map Dagster database"
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
            (
                "SELECT count(*) FROM runs WHERE status IN "
                "('QUEUED', 'STARTING', 'STARTED', 'CANCELING')"
            ),
        ],
        label="Map Dagster in-flight run count",
    ).decode("ascii").strip()
    if not output.isdigit():
        raise DeploymentContractError("Map Dagster in-flight run count is invalid")
    return int(output)


def read_pin_boundary_audit(
    runtime: DatabaseRuntime,
    transaction_id: str,
) -> PinBoundaryAuditRow:
    if runtime.role != "pinvi":
        raise DeploymentContractError(
            "Pin boundary audit query requires the PinVi database"
        )
    try:
        canonical_transaction_id = str(uuid.UUID(transaction_id))
    except (AttributeError, ValueError) as exc:
        raise DeploymentContractError(
            "Pin boundary audit transaction ID is invalid"
        ) from exc
    if canonical_transaction_id != transaction_id:
        raise DeploymentContractError(
            "Pin boundary audit transaction ID is not canonical"
        )
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set",
            f"transaction_id={transaction_id}",
            "--dbname",
            runtime.database_name,
            "--command",
            (
                "SELECT json_build_object("
                "'audit_id', transaction_id::text, "
                "'audit_request_sha256', encode(audit_request_sha256, 'hex'), "
                "'evidence_sha256', encode(evidence_sha256, 'hex'), "
                "'map_final_evidence_sha256', encode(map_final_evidence_sha256, 'hex'), "
                "'initial_writer_fence_sha256', encode(initial_writer_fence_sha256, 'hex'), "
                "'final_writer_fence_sha256', encode(final_writer_fence_sha256, 'hex'), "
                "'prior_receipt_sha256', encode(prior_receipt_sha256, 'hex'), "
                "'canary_run_id', canary_run_id::text)::text "
                "FROM app.ktm_cache_target_boundary_audits "
                "WHERE transaction_id = :'transaction_id'::uuid"
            ),
        ],
        label="Pin boundary audit row",
    ).decode("ascii").strip()
    try:
        document = json.loads(output)
        if not isinstance(document, dict) or set(document) != {
            "audit_id",
            "audit_request_sha256",
            "evidence_sha256",
            "map_final_evidence_sha256",
            "initial_writer_fence_sha256",
            "final_writer_fence_sha256",
            "prior_receipt_sha256",
            "canary_run_id",
        }:
            raise TypeError
        row = PinBoundaryAuditRow(**document)
    except (json.JSONDecodeError, TypeError) as exc:
        raise DeploymentContractError("Pin boundary audit row is invalid") from exc
    if row.audit_id != transaction_id:
        raise DeploymentContractError("Pin boundary audit row is foreign")
    for value in (
        row.audit_request_sha256,
        row.evidence_sha256,
        row.map_final_evidence_sha256,
        row.initial_writer_fence_sha256,
        row.final_writer_fence_sha256,
        row.prior_receipt_sha256,
    ):
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise DeploymentContractError("Pin boundary audit digest is invalid")
    try:
        if str(uuid.UUID(row.canary_run_id)) != row.canary_run_id:
            raise ValueError
    except (AttributeError, ValueError) as exc:
        raise DeploymentContractError("Pin boundary audit canary ID is invalid") from exc
    return row


def create_database_backup(
    *,
    state_directory: Path,
    transaction_id: str,
    runtime: DatabaseRuntime,
    writer_fence_sha256: str,
) -> DatabaseBackupReceipt:
    _validate_runtime(runtime)
    if re.fullmatch(r"[0-9a-f]{64}", writer_fence_sha256) is None:
        raise DeploymentContractError("database backup writer fence is invalid")
    transaction_directory = _prepare_transaction_directory(
        state_directory,
        transaction_id,
    )
    backup_path = _backup_path(transaction_directory, runtime.role)
    schema_revision = _read_schema_revision(runtime)
    database_identity = _read_database_identity(runtime, transaction_id)
    logical_backup_id = str(uuid.uuid5(uuid.UUID(transaction_id), runtime.role))
    if backup_path.exists():
        _validate_owner_only_file(backup_path)
    else:
        _write_pg_dump(backup_path, runtime)
    payload_sha256, byte_size = _file_sha256(backup_path)
    schema_inventory_sha256 = _logical_inventory_sha256(runtime, schema_only=True)
    data_inventory_sha256 = _logical_inventory_sha256(runtime, schema_only=False)
    restore_rehearsal = _rehearse_database_restore(
        backup_path=backup_path,
        runtime=runtime,
        transaction_id=transaction_id,
        source_database_identity=database_identity,
        archive_sha256=payload_sha256,
        expected_schema_revision=schema_revision,
        expected_schema_inventory_sha256=schema_inventory_sha256,
        expected_data_inventory_sha256=data_inventory_sha256,
    )
    return DatabaseBackupReceipt(
        transaction_id=transaction_id,
        database_identity=database_identity,
        schema_revision=schema_revision,
        logical_backup_id=logical_backup_id,
        byte_size=byte_size,
        sha256=payload_sha256,
        schema_inventory_sha256=schema_inventory_sha256,
        data_inventory_sha256=data_inventory_sha256,
        writer_fence_sha256=writer_fence_sha256,
        writer_mutation_count=0,
        restore_rehearsal=restore_rehearsal,
    )


def verify_database_backup(
    *,
    state_directory: Path,
    transaction_id: str,
    runtime: DatabaseRuntime,
    receipt: DatabaseBackupReceipt,
    writer_fence_sha256: str,
) -> None:
    _validate_runtime(runtime)
    backup_path = _backup_path(
        _transaction_directory(state_directory, transaction_id),
        runtime.role,
    )
    payload_sha256, byte_size = _file_sha256(backup_path)
    if (
        payload_sha256 != receipt.sha256
        or byte_size != receipt.byte_size
        or receipt.transaction_id != transaction_id
        or receipt.writer_fence_sha256 != writer_fence_sha256
        or receipt.writer_mutation_count != 0
        or receipt.restore_rehearsal.verified is not True
        or _read_schema_revision(runtime) != receipt.schema_revision
        or _read_database_identity(runtime, transaction_id)
        != receipt.database_identity
        or str(uuid.uuid5(uuid.UUID(transaction_id), runtime.role))
        != receipt.logical_backup_id
        or _logical_inventory_sha256(runtime, schema_only=True)
        != receipt.schema_inventory_sha256
        or _logical_inventory_sha256(runtime, schema_only=False)
        != receipt.data_inventory_sha256
    ):
        raise DeploymentContractError(
            f"{runtime.role} backup evidence differs from the live database"
        )
    rehearsal = _rehearse_database_restore(
        backup_path=backup_path,
        runtime=runtime,
        transaction_id=transaction_id,
        source_database_identity=receipt.database_identity,
        archive_sha256=receipt.sha256,
        expected_schema_revision=receipt.schema_revision,
        expected_schema_inventory_sha256=receipt.schema_inventory_sha256,
        expected_data_inventory_sha256=receipt.data_inventory_sha256,
    )
    if rehearsal != receipt.restore_rehearsal:
        raise DeploymentContractError(
            f"{runtime.role} backup restore rehearsal evidence differs"
        )


def restore_database_backup(
    *,
    state_directory: Path,
    transaction_id: str,
    runtime: DatabaseRuntime,
    receipt: DatabaseBackupReceipt,
    capability: object | None = None,
) -> None:
    if capability is not _COUPLED_ROLLBACK_CAPABILITY:
        raise DeploymentContractError(
            "production database restore requires the coupled rollback capability"
        )
    _validate_runtime(runtime)
    backup_path = _backup_path(
        _transaction_directory(state_directory, transaction_id),
        runtime.role,
    )
    payload_sha256, byte_size = _file_sha256(backup_path)
    if payload_sha256 != receipt.sha256 or byte_size != receipt.byte_size:
        raise DeploymentContractError(
            f"{runtime.role} backup payload identity is invalid"
        )
    # `dropdb --if-exists`는 database가 원래 없을 때도 "does not exist, skipping"
    # NOTICE를 stderr에 낸다. `_run_checked`는 stderr가 하나라도 있으면 실패로
    # 처리하므로, database가 실제로 존재할 때만 drop을 실행해 이 무해한 NOTICE가
    # restore 자체를 거짓으로 실패시키지 않게 한다(cache-target diagnostic의 동일
    # 실공백에서 확인된 패턴).
    if _read_database_owner(runtime) is not None:
        _run_checked(
            [
                *_database_admin_command(runtime, "dropdb"),
                "--if-exists",
                "--force",
                runtime.database_name,
            ],
            label=f"{runtime.role} database drop",
        )
    _run_checked(
        [
            *_database_admin_command(runtime, "createdb"),
            "--owner",
            runtime.owner_name,
            runtime.database_name,
        ],
        label=f"{runtime.role} database create",
    )
    try:
        with backup_path.open("rb") as dump:
            completed = subprocess.run(
                [
                    *_database_admin_command(
                        runtime,
                        "pg_restore",
                        interactive=True,
                    ),
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    "--dbname",
                    runtime.database_name,
                ],
                stdin=dump,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=_DATABASE_RESTORE_TIMEOUT_SECONDS,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            f"{runtime.role} database restore could not run"
        ) from exc
    if completed.returncode != 0 or completed.stderr:
        raise DeploymentContractError(f"{runtime.role} database restore failed")
    if (
        _read_schema_revision(runtime) != receipt.schema_revision
        or _read_database_identity(runtime, transaction_id)
        != receipt.database_identity
        or _logical_inventory_sha256(runtime, schema_only=True)
        != receipt.schema_inventory_sha256
        or _logical_inventory_sha256(runtime, schema_only=False)
        != receipt.data_inventory_sha256
    ):
        raise DeploymentContractError(
            f"{runtime.role} restored database evidence is invalid"
        )


def create_manager_rollback_bundle(
    *,
    state_directory: Path,
    transaction_id: str,
    env_path: Path,
    manifest_path: Path,
    environment_bytes: bytes,
    manifest_bytes: bytes,
) -> str:
    transaction_directory = _prepare_transaction_directory(
        state_directory,
        transaction_id,
    )
    bundle_directory = transaction_directory / "manager-state"
    bundle_directory.mkdir(mode=0o700, exist_ok=True)
    _validate_owner_only_directory(bundle_directory)
    current_env = _read_exact_owner_file(env_path)
    current_manifest = _read_exact_owner_file(manifest_path)
    if current_env != environment_bytes or current_manifest != manifest_bytes:
        raise DeploymentContractError(
            "manager rollback source changed before bundle capture"
        )
    sources: dict[str, bytes | None] = {
        "environment": environment_bytes,
        "manifest": manifest_bytes,
        **{
            label: (
                _read_exact_owner_file(state_directory / filename)
                if (state_directory / filename).exists()
                else None
            )
            for label, filename in _MANAGER_STATE_FILENAMES.items()
        },
    }
    artifacts: dict[str, dict[str, object]] = {}
    for label, payload in sources.items():
        if payload is None:
            artifacts[label] = {"present": False, "sha256": None, "byte_size": 0}
            continue
        _write_exclusive_owner_file(bundle_directory / f"{label}.bin", payload)
        artifacts[label] = {
            "present": True,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "byte_size": len(payload),
        }
    index = {
        "version": 1,
        "transaction_id": transaction_id,
        "artifacts": artifacts,
    }
    index_payload = json.dumps(
        index,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    _write_exclusive_owner_file(bundle_directory / "index.json", index_payload)
    return hashlib.sha256(index_payload).hexdigest()


def verify_manager_rollback_bundle(
    *,
    state_directory: Path,
    transaction_id: str,
    expected_sha256: str,
) -> None:
    _load_manager_rollback_bundle(
        state_directory=state_directory,
        transaction_id=transaction_id,
        expected_sha256=expected_sha256,
    )


def restore_manager_rollback_bundle(
    *,
    state_directory: Path,
    transaction_id: str,
    expected_sha256: str,
    env_path: Path,
    manifest_path: Path,
) -> None:
    bundle_directory, artifacts = _load_manager_rollback_bundle(
        state_directory=state_directory,
        transaction_id=transaction_id,
        expected_sha256=expected_sha256,
    )
    destinations = {
        "environment": env_path,
        "manifest": manifest_path,
        **{
            label: state_directory / filename
            for label, filename in _MANAGER_STATE_FILENAMES.items()
        },
    }
    for label, destination in destinations.items():
        evidence = artifacts[label]
        if evidence["present"] is False:
            try:
                destination.unlink(missing_ok=True)
                _fsync_directory(destination.parent)
            except OSError as exc:
                raise DeploymentContractError(
                    "manager rollback absent state restore failed"
                ) from exc
            continue
        payload = _read_exact_owner_file(bundle_directory / f"{label}.bin")
        if (
            hashlib.sha256(payload).hexdigest() != evidence["sha256"]
            or len(payload) != evidence["byte_size"]
        ):
            raise DeploymentContractError(
                "manager rollback artifact identity is invalid"
            )
        _atomic_replace_owner_file(destination, payload)


def _rehearse_database_restore(
    *,
    backup_path: Path,
    runtime: DatabaseRuntime,
    transaction_id: str,
    source_database_identity: str,
    archive_sha256: str,
    expected_schema_revision: str,
    expected_schema_inventory_sha256: str,
    expected_data_inventory_sha256: str,
) -> DatabaseRestoreRehearsalReceipt:
    _validate_archive_structure(
        backup_path=backup_path,
        runtime=runtime,
    )
    scratch_name = _scratch_database_name(runtime.role, transaction_id)
    if scratch_name == runtime.database_name:
        raise DeploymentContractError("scratch database collides with production")
    scratch_runtime = DatabaseRuntime(
        role=runtime.role,
        container_name=runtime.container_name,
        database_name=scratch_name,
        owner_name=runtime.owner_name,
        admin_name=runtime.admin_name,
    )
    stale_owner = _read_database_owner(scratch_runtime)
    if stale_owner not in {None, runtime.owner_name}:
        raise DeploymentContractError(
            f"{runtime.role} scratch database is owned by a foreign role"
        )
    # `dropdb --if-exists`는 scratch database가 원래 없을 때도 "does not exist,
    # skipping" NOTICE를 stderr에 낸다. `_run_checked`는 stderr가 하나라도 있으면
    # 실패로 처리하므로, `_read_database_owner`가 이미 존재하지 않음(None)을 확인해준
    # 일반적인 경우엔 dropdb 자체를 생략해 이 무해한 NOTICE가 매번 rehearsal을 거짓으로
    # 실패시키는 것을 막는다(cache-target diagnostic의 동일 실공백에서 확인된 패턴).
    if stale_owner is not None:
        _run_checked(
            [
                *_database_admin_command(runtime, "dropdb"),
                "--if-exists",
                "--force",
                scratch_name,
            ],
            label=f"{runtime.role} stale scratch database cleanup",
        )
    created = False
    failure: Exception | None = None
    receipt: DatabaseRestoreRehearsalReceipt | None = None
    try:
        _run_checked(
            [
                *_database_admin_command(runtime, "createdb"),
                "--owner",
                runtime.owner_name,
                scratch_name,
            ],
            label=f"{runtime.role} scratch database create",
        )
        created = True
        _restore_archive_into_database(
            backup_path=backup_path,
            runtime=scratch_runtime,
            label=f"{runtime.role} scratch database restore",
        )
        if _read_schema_revision(scratch_runtime) != expected_schema_revision:
            raise DeploymentContractError(
                f"{runtime.role} scratch schema revision differs from backup source"
            )
        if (
            _logical_inventory_sha256(scratch_runtime, schema_only=True)
            != expected_schema_inventory_sha256
        ):
            raise DeploymentContractError(
                f"{runtime.role} scratch schema inventory differs from backup source"
            )
        if (
            _logical_inventory_sha256(scratch_runtime, schema_only=False)
            != expected_data_inventory_sha256
        ):
            raise DeploymentContractError(
                f"{runtime.role} scratch data inventory differs from backup source"
            )
        scratch_identity = _read_database_identity(scratch_runtime, transaction_id)
        if scratch_identity == source_database_identity:
            raise DeploymentContractError(
                f"{runtime.role} scratch database reused the source identity"
            )
        receipt = DatabaseRestoreRehearsalReceipt(
            transaction_id=transaction_id,
            database_identity=scratch_identity,
            source_database_identity=source_database_identity,
            archive_sha256=archive_sha256,
            schema_revision=expected_schema_revision,
            schema_inventory_sha256=expected_schema_inventory_sha256,
            data_inventory_sha256=expected_data_inventory_sha256,
            verified=True,
        )
    except Exception as exc:
        failure = exc
    finally:
        if created:
            try:
                _run_checked(
                    [
                        *_database_admin_command(runtime, "dropdb"),
                        "--if-exists",
                        "--force",
                        scratch_name,
                    ],
                    label=f"{runtime.role} scratch database cleanup",
                )
            except Exception as cleanup_error:
                if failure is None:
                    failure = cleanup_error
    if failure is not None:
        raise DeploymentContractError(
            f"{runtime.role} database restore rehearsal failed"
        ) from failure
    if receipt is None:
        raise DeploymentContractError(
            f"{runtime.role} database restore rehearsal produced no evidence"
        )
    return receipt


def _restore_archive_into_database(
    *,
    backup_path: Path,
    runtime: DatabaseRuntime,
    label: str,
) -> None:
    try:
        with backup_path.open("rb") as dump:
            completed = subprocess.run(
                [
                    *_database_admin_command(
                        runtime,
                        "pg_restore",
                        interactive=True,
                    ),
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    "--dbname",
                    runtime.database_name,
                ],
                stdin=dump,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=_DATABASE_RESTORE_TIMEOUT_SECONDS,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(f"{label} could not run") from exc
    if completed.returncode != 0 or completed.stderr:
        raise DeploymentContractError(f"{label} failed")


def _validate_archive_structure(
    *,
    backup_path: Path,
    runtime: DatabaseRuntime,
) -> None:
    try:
        with backup_path.open("rb") as dump:
            completed = subprocess.run(
                [
                    *_database_admin_command(
                        runtime,
                        "pg_restore",
                        interactive=True,
                    ),
                    "--list",
                ],
                stdin=dump,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=300,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            f"{runtime.role} backup structural validation could not run"
        ) from exc
    if completed.returncode != 0 or completed.stderr:
        raise DeploymentContractError(
            f"{runtime.role} backup structural validation failed"
        )


def _logical_inventory_sha256(
    runtime: DatabaseRuntime,
    *,
    schema_only: bool,
) -> str:
    arguments = [
        *_database_admin_command(runtime, "pg_dump"),
        "--no-owner",
        "--no-acl",
        "--schema-only" if schema_only else "--data-only",
    ]
    if not schema_only:
        arguments.extend(["--inserts", "--rows-per-insert=1"])
    arguments.extend(["--dbname", runtime.database_name])
    try:
        with tempfile.TemporaryFile() as output:
            completed = subprocess.run(
                arguments,
                stdout=output,
                stderr=subprocess.PIPE,
                check=False,
                timeout=3600,
            )
            # ``pg_dump --data-only`` emits this exact restore advisory for
            # valid circular foreign-key graphs.  Other stderr is fail-closed:
            # inventory equality is a source-to-scratch integrity boundary.
            if completed.returncode != 0 or (
                completed.stderr
                and (
                    schema_only
                    or not _is_circular_foreign_key_restore_advisory(
                        completed.stderr
                    )
                )
            ):
                raise DeploymentContractError(
                    f"{runtime.role} logical inventory failed"
                )
            output.seek(0)
            digest = hashlib.sha256()
            while chunk := output.read(1024 * 1024):
                digest.update(chunk)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            f"{runtime.role} logical inventory could not run"
        ) from exc
    return digest.hexdigest()


def _is_circular_foreign_key_restore_advisory(stderr: bytes) -> bool:
    """data-only dump의 알려진 circular-FK advisory block만 허용한다."""

    try:
        lines = stderr.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return False
    index = 0
    matched = False
    while index < len(lines):
        if lines[index] not in _CIRCULAR_FOREIGN_KEY_WARNING_HEADINGS:
            return False
        index += 1
        detail_count = 0
        while index < len(lines) and lines[index].startswith("pg_dump: detail: "):
            detail = lines[index].removeprefix("pg_dump: detail: ")
            if not detail or not detail.isprintable():
                return False
            detail_count += 1
            index += 1
        if detail_count == 0 or tuple(lines[index : index + 2]) != (
            _CIRCULAR_FOREIGN_KEY_WARNING_HINTS
        ):
            return False
        index += 2
        matched = True
    return matched


def _scratch_database_name(role: DatabaseRole, transaction_id: str) -> str:
    canonical = str(uuid.UUID(transaction_id))
    if canonical != transaction_id:
        raise DeploymentContractError(
            "cache-target scratch transaction ID must be canonical"
        )
    suffix = transaction_id.replace("-", "")[:20]
    name = f"ktdm_{role}_{suffix}"
    if not _DATABASE_IDENTIFIER.fullmatch(name):
        raise DeploymentContractError("cache-target scratch database name is invalid")
    return name


def _load_manager_rollback_bundle(
    *,
    state_directory: Path,
    transaction_id: str,
    expected_sha256: str,
) -> tuple[Path, dict[str, dict[str, object]]]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise DeploymentContractError("manager rollback bundle SHA-256 is invalid")
    bundle_directory = (
        _transaction_directory(state_directory, transaction_id) / "manager-state"
    )
    _validate_owner_only_directory(bundle_directory)
    index_payload = _read_exact_owner_file(bundle_directory / "index.json")
    if hashlib.sha256(index_payload).hexdigest() != expected_sha256:
        raise DeploymentContractError("manager rollback bundle identity is invalid")
    try:
        index = json.loads(index_payload)
        if (
            not isinstance(index, dict)
            or set(index) != {"version", "transaction_id", "artifacts"}
            or index["version"] != 1
            or index["transaction_id"] != transaction_id
            or not isinstance(index["artifacts"], dict)
            or set(index["artifacts"])
            != {"environment", "manifest", *_MANAGER_STATE_FILENAMES}
        ):
            raise TypeError
        artifacts = index["artifacts"]
        for evidence in artifacts.values():
            if (
                not isinstance(evidence, dict)
                or set(evidence) != {"present", "sha256", "byte_size"}
                or type(evidence["present"]) is not bool
                or type(evidence["byte_size"]) is not int
                or (
                    evidence["present"]
                    and (
                        not isinstance(evidence["sha256"], str)
                        or re.fullmatch(r"[0-9a-f]{64}", evidence["sha256"])
                        is None
                        or evidence["byte_size"] <= 0
                    )
                )
                or (
                    not evidence["present"]
                    and (evidence["sha256"] is not None or evidence["byte_size"] != 0)
                )
            ):
                raise TypeError
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError("manager rollback bundle index is invalid") from exc
    for label, evidence in artifacts.items():
        if evidence["present"]:
            payload = _read_exact_owner_file(bundle_directory / f"{label}.bin")
            if (
                hashlib.sha256(payload).hexdigest() != evidence["sha256"]
                or len(payload) != evidence["byte_size"]
            ):
                raise DeploymentContractError(
                    "manager rollback bundle artifact is invalid"
                )
    return bundle_directory, artifacts


def _read_schema_revision(runtime: DatabaseRuntime) -> str:
    _validate_runtime(runtime)
    schema_name, table_name = _SCHEMA_REVISION_LOCATION[runtime.role]
    if not _DATABASE_IDENTIFIER.fullmatch(
        schema_name
    ) or not _DATABASE_IDENTIFIER.fullmatch(table_name):
        raise DeploymentContractError(
            f"{runtime.role} canonical schema revision location is invalid"
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
            f'SELECT version_num FROM "{schema_name}"."{table_name}"',
        ],
        label=f"{runtime.role} schema revision",
    ).decode("ascii").strip()
    lines = output.splitlines()
    if len(lines) != 1 or not _SCHEMA_REVISION.fullmatch(lines[0]):
        raise DeploymentContractError(
            f"{runtime.role} schema revision output is invalid"
        )
    return lines[0]


def _read_database_owner(runtime: DatabaseRuntime) -> str | None:
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
        label=f"{runtime.role} scratch database owner",
    ).decode("ascii").strip()
    if not output:
        return None
    if "\n" in output or not _DATABASE_IDENTIFIER.fullmatch(output):
        raise DeploymentContractError(
            f"{runtime.role} scratch database owner output is invalid"
        )
    return output


def _read_database_size(runtime: DatabaseRuntime) -> int:
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            runtime.database_name,
            "--command",
            "SELECT pg_database_size(current_database())",
        ],
        label=f"{runtime.role} database size",
    ).decode("ascii").strip()
    if not output.isdigit() or int(output) <= 0:
        raise DeploymentContractError(f"{runtime.role} database size is invalid")
    return int(output)


def _read_postgres_free_bytes(runtime: DatabaseRuntime) -> int:
    output = _run_checked(
        [
            "docker",
            "exec",
            "--user",
            "postgres",
            runtime.container_name,
            "df",
            "--output=avail",
            "--block-size=1",
            "/var/lib/postgresql/data",
        ],
        label="cutover PostgreSQL free space",
    ).decode("ascii").strip()
    lines = output.splitlines()
    if len(lines) != 2 or lines[0].strip().lower() != "avail":
        raise DeploymentContractError("cutover PostgreSQL free space output is invalid")
    available = lines[1].strip()
    if not available.isdigit() or int(available) <= 0:
        raise DeploymentContractError("cutover PostgreSQL free space is invalid")
    return int(available)


def _read_database_identity(runtime: DatabaseRuntime, transaction_id: str) -> str:
    output = _run_checked(
        [
            *_database_admin_command(runtime, "psql"),
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--dbname",
            runtime.database_name,
            "--command",
            "SELECT system_identifier::text FROM pg_control_system()",
        ],
        label=f"{runtime.role} database identity",
    ).decode("ascii").strip()
    if not output.isdigit() or len(output) > 32:
        raise DeploymentContractError(
            f"{runtime.role} database identity output is invalid"
        )
    return database_identity_v1(
        transaction_id=transaction_id,
        role=runtime.role,
        database_name=runtime.database_name,
        system_identifier=output,
    )


def database_identity_v1(
    *,
    transaction_id: str,
    role: DatabaseRole,
    database_name: str,
    system_identifier: str,
) -> str:
    try:
        canonical_transaction_id = str(uuid.UUID(transaction_id))
    except ValueError as exc:
        raise DeploymentContractError("database identity transaction ID is invalid") from exc
    if canonical_transaction_id != transaction_id:
        raise DeploymentContractError("database identity transaction ID is invalid")
    if role not in _ROLE_CONFIG:
        raise DeploymentContractError("database identity role is invalid")
    if not _DATABASE_IDENTIFIER.fullmatch(database_name):
        raise DeploymentContractError("database identity name is invalid")
    if not system_identifier.isascii() or not system_identifier.isdigit() or len(system_identifier) > 32:
        raise DeploymentContractError("database identity system identifier is invalid")
    payload = (
        b"h35-db-identity-v1\0"
        + transaction_id.encode("ascii")
        + b"\0"
        + role.encode("ascii")
        + b"\0"
        + database_name.encode("ascii")
        + b"\0"
        + system_identifier.encode("ascii")
        + b"\0"
    )
    return hashlib.sha256(payload).hexdigest()


def _write_pg_dump(path: Path, runtime: DatabaseRuntime) -> None:
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            completed = subprocess.run(
                [
                    *_database_admin_command(runtime, "pg_dump"),
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    "--dbname",
                    runtime.database_name,
                ],
                stdout=output,
                stderr=subprocess.PIPE,
                check=False,
                timeout=3600,
            )
            output.flush()
            os.fsync(output.fileno())
        if completed.returncode != 0 or completed.stderr:
            raise DeploymentContractError(f"{runtime.role} database backup failed")
        if temporary.stat().st_size <= 0:
            raise DeploymentContractError(
                f"{runtime.role} database backup is empty"
            )
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    except FileExistsError:
        _validate_owner_only_file(path)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            f"{runtime.role} database backup could not run"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    _validate_owner_only_file(path)


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
    return completed.stdout


def _prepare_transaction_directory(
    state_directory: Path,
    transaction_id: str,
) -> Path:
    directory = _transaction_directory(state_directory, transaction_id)
    state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    for candidate in (state_directory, directory):
        directory_stat = candidate.lstat()
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.geteuid()
            or stat.S_IMODE(directory_stat.st_mode) != 0o700
        ):
            raise DeploymentContractError(
                "cache-target backup directory is unsafe"
            )
    return directory


def _transaction_directory(state_directory: Path, transaction_id: str) -> Path:
    try:
        canonical = str(uuid.UUID(transaction_id))
    except ValueError as exc:
        raise DeploymentContractError(
            "cache-target backup transaction ID is invalid"
        ) from exc
    if canonical != transaction_id:
        raise DeploymentContractError(
            "cache-target backup transaction ID must be canonical"
        )
    return state_directory / f"cache-target-window-{transaction_id}"


def _backup_path(directory: Path, role: DatabaseRole) -> Path:
    if role not in _ROLE_CONFIG:
        raise DeploymentContractError("cache-target database role is invalid")
    return directory / f"{role}.dump"


def _file_sha256(path: Path) -> tuple[str, int]:
    _validate_owner_only_file(path)
    digest = hashlib.sha256()
    byte_size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                byte_size += len(chunk)
    except OSError as exc:
        raise DeploymentContractError("cache-target backup cannot be read") from exc
    _validate_owner_only_file(path)
    if byte_size <= 0:
        raise DeploymentContractError("cache-target backup is empty")
    return digest.hexdigest(), byte_size


def _validate_owner_only_file(path: Path) -> None:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError("cache-target backup is unavailable") from exc
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != os.geteuid()
        or file_stat.st_nlink != 1
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise DeploymentContractError("cache-target backup file is unsafe")


def _validate_owner_only_directory(path: Path) -> None:
    try:
        directory_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError(
            "cache-target backup directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.geteuid()
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
    ):
        raise DeploymentContractError("cache-target backup directory is unsafe")


def _read_exact_owner_file(path: Path) -> bytes:
    _validate_owner_only_file(path)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DeploymentContractError(
            "cache-target rollback artifact cannot be read"
        ) from exc
    if not payload or len(payload) > 16 * 1024 * 1024:
        raise DeploymentContractError(
            "cache-target rollback artifact size is invalid"
        )
    _validate_owner_only_file(path)
    return payload


def _write_exclusive_owner_file(path: Path, payload: bytes) -> None:
    if not payload:
        raise DeploymentContractError("cache-target rollback artifact is empty")
    if path.exists():
        if _read_exact_owner_file(path) != payload:
            raise DeploymentContractError(
                "foreign cache-target rollback artifact already exists"
            )
        return
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    except FileExistsError:
        if _read_exact_owner_file(path) != payload:
            raise DeploymentContractError(
                "foreign cache-target rollback artifact appeared concurrently"
            ) from None
    except OSError as exc:
        raise DeploymentContractError(
            "cache-target rollback artifact write failed"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    _validate_owner_only_file(path)


def _atomic_replace_owner_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _validate_owner_only_directory(path.parent)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".restore.tmp",
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    except OSError as exc:
        raise DeploymentContractError(
            "cache-target rollback artifact restore failed"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    _validate_owner_only_file(path)


def _validate_runtime(runtime: DatabaseRuntime) -> None:
    if runtime.role not in _ROLE_CONFIG:
        raise DeploymentContractError("cache-target database role is invalid")
    if not _CONTAINER_NAME.fullmatch(runtime.container_name):
        raise DeploymentContractError("cache-target database container is invalid")
    if not _DATABASE_IDENTIFIER.fullmatch(runtime.database_name):
        raise DeploymentContractError("cache-target database name is invalid")
    if not _DATABASE_IDENTIFIER.fullmatch(runtime.owner_name):
        raise DeploymentContractError("cache-target database owner is invalid")
    if not _DATABASE_IDENTIFIER.fullmatch(runtime.admin_name):
        raise DeploymentContractError("cache-target database admin role is invalid")


def _database_admin_command(
    runtime: DatabaseRuntime,
    executable: Literal["psql", "pg_dump", "pg_restore", "dropdb", "createdb"],
    *,
    interactive: bool = False,
) -> list[str]:
    _validate_runtime(runtime)
    return [
        "docker",
        "exec",
        *(["--interactive"] if interactive else []),
        "--user",
        "postgres",
        runtime.container_name,
        executable,
        "--username",
        runtime.admin_name,
    ]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
