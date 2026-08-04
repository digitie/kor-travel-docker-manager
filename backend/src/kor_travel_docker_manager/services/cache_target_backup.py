from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Literal

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


@dataclass(frozen=True)
class StandaloneBackupManifest:
    """T-053: cache-target cutover window와 무관하게 언제든 단독으로 만드는 백업의
    증적. raw stdout/stderr/DSN/credential/path는 담지 않는다 — typed digest·size·
    timestamp·schema revision만 남긴다."""

    role: DatabaseRole
    created_at_unix: int
    schema_revision: str
    sha256: str
    byte_size: int
    backup_filename: str


def create_standalone_database_backup(
    *,
    backups_root: Path,
    runtime: DatabaseRuntime,
    created_at_unix: int,
) -> StandaloneBackupManifest:
    """cache-target cutover window/journal과 완전히 분리된, 언제든 단독 호출
    가능한 백업. `~/backups/<role>/`에 owner-only(0700 디렉터리·0600 파일) `.dump`와
    같은 이름의 `.manifest.json`을 원자적으로 남긴다. 같은 초 안에 같은 role로
    재호출되면(동일 timestamp) 충돌을 조용히 덮지 않고 거부한다."""

    _validate_runtime(runtime)
    if created_at_unix <= 0:
        raise DeploymentContractError("standalone database backup timestamp is invalid")
    role_directory = backups_root / runtime.role
    # `Path.mkdir(mode=..., parents=True)`는 `mode`를 마지막(leaf) 디렉터리에만
    # 적용하고 자동 생성되는 상위 디렉터리는 시스템 기본 권한(umask 적용)으로
    # 만든다 — `backups_root`가 아직 없으면 leaf만 0700이 되고 `backups_root` 자체는
    # 0700이 아니게 된다. 두 단계를 각각 명시적으로 만들어 이 함정을 피한다.
    backups_root.mkdir(mode=0o700, exist_ok=True)
    _validate_owner_only_directory(backups_root)
    role_directory.mkdir(mode=0o700, exist_ok=True)
    _validate_owner_only_directory(role_directory)
    schema_revision = _read_schema_revision(runtime)
    if not _SCHEMA_REVISION.fullmatch(schema_revision):
        raise DeploymentContractError(
            f"{runtime.role} standalone backup schema revision is invalid"
        )
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(created_at_unix))
    filename_stem = f"{timestamp}_{runtime.role}_{schema_revision}"
    backup_path = role_directory / f"{filename_stem}.dump"
    manifest_path = role_directory / f"{filename_stem}.manifest.json"
    # `_write_pg_dump`는 대상이 이미 있으면 조용히 재사용을 허용한다(cutover
    # window의 idempotent 재시도 의미론). 이 독립 백업 경로는 "같은 초에 재호출되면
    # 거부"가 계약이므로 그 헬퍼를 재사용하지 않는다 — `O_CREAT|O_EXCL`로 최종
    # 파일명 자체를 원자적으로 선점한 뒤 그 fd에 직접 pg_dump를 스트리밍해,
    # 존재-확인과 쓰기 사이의 race에서 두 번째 호출이 조용히 성공하는 경로를
    # 원천적으로 없앤다.
    if manifest_path.exists():
        raise DeploymentContractError(
            f"{runtime.role} standalone backup already exists for this timestamp"
        )
    try:
        descriptor = os.open(
            backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError as exc:
        raise DeploymentContractError(
            f"{runtime.role} standalone backup already exists for this timestamp"
        ) from exc
    wrote_backup = False
    try:
        with os.fdopen(descriptor, "wb") as output:
            _stream_pg_dump_custom_format(
                output,
                runtime,
                subprocess_failure_message=f"{runtime.role} standalone backup failed",
            )
        if backup_path.stat().st_size <= 0:
            raise DeploymentContractError(f"{runtime.role} standalone backup is empty")
        wrote_backup = True
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            f"{runtime.role} standalone backup could not run"
        ) from exc
    finally:
        if not wrote_backup:
            backup_path.unlink(missing_ok=True)
    _fsync_directory(role_directory)
    payload_sha256, byte_size = _file_sha256(backup_path)
    manifest = StandaloneBackupManifest(
        role=runtime.role,
        created_at_unix=created_at_unix,
        schema_revision=schema_revision,
        sha256=payload_sha256,
        byte_size=byte_size,
        backup_filename=backup_path.name,
    )
    payload = json.dumps(
        asdict(manifest), sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    _atomic_replace_owner_file(manifest_path, payload)
    return manifest


_STANDALONE_BACKUP_FILE_SUFFIX = ".dump"
_STANDALONE_BACKUP_MANIFEST_SUFFIX = ".manifest.json"
_STANDALONE_BACKUP_MANIFEST_FIELDS = frozenset(
    {"role", "created_at_unix", "schema_revision", "sha256", "byte_size", "backup_filename"}
)
_STANDALONE_BACKUP_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STANDALONE_BACKUP_STEM = re.compile(
    r"^(?P<timestamp>\d{8}T\d{6}Z)_(?P<role>[a-z_]+)_(?P<schema_revision>[0-9a-z][0-9a-z_.-]{0,127})$"
)
STANDALONE_BACKUP_DEFAULT_KEEP_COUNT = 5
STANDALONE_BACKUP_DEFAULT_KEEP_DAYS = 14


@dataclass(frozen=True)
class StandaloneBackupListResult:
    """`list_standalone_database_backups`의 결과. `warnings`는 손상되었거나
    계약을 벗어난 manifest를 가리키는 사람이 읽을 수 있는 설명이며, 그런 항목은
    `manifests`에서 조용히 빠지는 대신 항상 `warnings`에 남는다(silent truncation
    금지). 파일명만 담고 raw 파일 내용은 절대 넣지 않는다."""

    manifests: tuple[StandaloneBackupManifest, ...]
    warnings: tuple[str, ...]


def list_standalone_database_backups(
    backups_root: Path,
    *,
    role: DatabaseRole | None = None,
) -> StandaloneBackupListResult:
    """`~/backups/<role>/`의 T-053 백업 manifest를 읽는다. 각 항목은 파일명 패턴·
    manifest JSON 스키마·참조된 `.dump` 파일의 owner-only 소유·크기 일치를 모두
    통과해야 유효로 인정한다 — 이 디렉터리에 있는 임의 파일을 신뢰하지 않는다.
    손상되거나 계약을 벗어난 manifest는 예외를 던져 전체 조회를 막는 대신
    `warnings`에 담아 나머지는 계속 보여준다(disaster-recovery 도구가 항목 하나
    손상됐다고 전체를 못 보여주면 더 위험하다)."""

    roles = (role,) if role is not None else tuple(_ROLE_CONFIG)
    manifests: list[StandaloneBackupManifest] = []
    warnings: list[str] = []
    for candidate_role in roles:
        if candidate_role not in _ROLE_CONFIG:
            raise DeploymentContractError("standalone database backup role is invalid")
        role_directory = backups_root / candidate_role
        try:
            _validate_owner_only_directory(role_directory)
        except DeploymentContractError:
            continue
        for manifest_path in sorted(
            role_directory.glob(f"*{_STANDALONE_BACKUP_MANIFEST_SUFFIX}")
        ):
            try:
                manifest = _read_standalone_backup_manifest(
                    manifest_path, expected_role=candidate_role
                )
            except DeploymentContractError:
                warnings.append(
                    f"{candidate_role}: {manifest_path.name} is invalid or its "
                    "backup payload is missing/altered"
                )
                continue
            manifests.append(manifest)
    manifests.sort(key=lambda item: item.created_at_unix, reverse=True)
    return StandaloneBackupListResult(
        manifests=tuple(manifests), warnings=tuple(warnings)
    )


def _read_standalone_backup_manifest(
    manifest_path: Path, *, expected_role: DatabaseRole
) -> StandaloneBackupManifest:
    stem_match = _STANDALONE_BACKUP_STEM.fullmatch(
        manifest_path.name.removesuffix(_STANDALONE_BACKUP_MANIFEST_SUFFIX)
    )
    if stem_match is None or stem_match.group("role") != expected_role:
        raise DeploymentContractError("standalone database backup filename is invalid")
    _validate_owner_only_file(manifest_path)
    try:
        payload = json.loads(manifest_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentContractError(
            "standalone database backup manifest is invalid"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != _STANDALONE_BACKUP_MANIFEST_FIELDS:
        raise DeploymentContractError("standalone database backup manifest is invalid")
    role = payload["role"]
    created_at_unix = payload["created_at_unix"]
    schema_revision = payload["schema_revision"]
    sha256 = payload["sha256"]
    byte_size = payload["byte_size"]
    backup_filename = payload["backup_filename"]
    if (
        role != expected_role
        or role != stem_match.group("role")
        or not isinstance(created_at_unix, int)
        or isinstance(created_at_unix, bool)
        or created_at_unix <= 0
        or not isinstance(schema_revision, str)
        or not _SCHEMA_REVISION.fullmatch(schema_revision)
        or schema_revision != stem_match.group("schema_revision")
        or not isinstance(sha256, str)
        or not _STANDALONE_BACKUP_SHA256.fullmatch(sha256)
        or not isinstance(byte_size, int)
        or isinstance(byte_size, bool)
        or byte_size <= 0
        or not isinstance(backup_filename, str)
        or backup_filename
        != manifest_path.name.removesuffix(_STANDALONE_BACKUP_MANIFEST_SUFFIX)
        + _STANDALONE_BACKUP_FILE_SUFFIX
    ):
        raise DeploymentContractError("standalone database backup manifest is invalid")
    backup_path = manifest_path.parent / backup_filename
    _validate_owner_only_file(backup_path)
    if backup_path.stat().st_size != byte_size:
        raise DeploymentContractError(
            "standalone database backup payload size differs from manifest"
        )
    return StandaloneBackupManifest(
        role=role,
        created_at_unix=created_at_unix,
        schema_revision=schema_revision,
        sha256=sha256,
        byte_size=byte_size,
        backup_filename=backup_filename,
    )


@dataclass(frozen=True)
class StandaloneBackupGcResult:
    """`gc_standalone_database_backups`의 결과. `kept`/`deleted` 모두 명시적으로
    담아 무엇이 지워지고 무엇이 남았는지 항상 CLI 출력에 드러낼 수 있게 한다
    (silent truncation 금지)."""

    role: DatabaseRole
    kept: tuple[StandaloneBackupManifest, ...]
    deleted: tuple[StandaloneBackupManifest, ...]
    warnings: tuple[str, ...]


def gc_standalone_database_backups(
    backups_root: Path,
    *,
    role: DatabaseRole,
    now_unix: int,
    keep_count: int = STANDALONE_BACKUP_DEFAULT_KEEP_COUNT,
    keep_days: int = STANDALONE_BACKUP_DEFAULT_KEEP_DAYS,
) -> StandaloneBackupGcResult:
    """가장 최근 `keep_count`개는 나이와 무관하게 항상 보존하고, 그 나머지 중
    `keep_days`일 이내인 것도 보존한다. 그 외는 지운다. 삭제는 manifest를 먼저
    지워 목록에서 즉시 빠지게 한 뒤 `.dump`를 지운다 — 중간에 죽어도 다음
    `list_standalone_database_backups` 호출이 고아 `.dump`(디스크만 낭비, 목록엔
    영향 없음) 이상으로 깨지지 않는다. 삭제 직전 owner-only 소유를 다시 검증해
    race로 다른 파일이 같은 이름에 끼어든 경우를 배제한다."""

    if role not in _ROLE_CONFIG:
        raise DeploymentContractError("standalone database backup role is invalid")
    if keep_count < 1:
        raise DeploymentContractError(
            "standalone database backup GC keep_count is invalid"
        )
    if keep_days < 0:
        raise DeploymentContractError(
            "standalone database backup GC keep_days is invalid"
        )
    if now_unix <= 0:
        raise DeploymentContractError(
            "standalone database backup GC timestamp is invalid"
        )
    listing = list_standalone_database_backups(backups_root, role=role)
    cutoff_unix = now_unix - keep_days * 86_400
    kept: list[StandaloneBackupManifest] = []
    to_delete: list[StandaloneBackupManifest] = []
    for index, manifest in enumerate(listing.manifests):
        if index < keep_count or manifest.created_at_unix >= cutoff_unix:
            kept.append(manifest)
        else:
            to_delete.append(manifest)
    role_directory = backups_root / role
    deleted: list[StandaloneBackupManifest] = []
    for manifest in to_delete:
        _delete_standalone_database_backup(role_directory, manifest)
        deleted.append(manifest)
    return StandaloneBackupGcResult(
        role=role,
        kept=tuple(kept),
        deleted=tuple(deleted),
        warnings=listing.warnings,
    )


def _delete_standalone_database_backup(
    role_directory: Path, manifest: StandaloneBackupManifest
) -> None:
    _validate_owner_only_directory(role_directory)
    backup_path = role_directory / manifest.backup_filename
    manifest_filename = (
        manifest.backup_filename.removesuffix(_STANDALONE_BACKUP_FILE_SUFFIX)
        + _STANDALONE_BACKUP_MANIFEST_SUFFIX
    )
    manifest_path = role_directory / manifest_filename
    _validate_owner_only_file(manifest_path)
    _validate_owner_only_file(backup_path)
    manifest_path.unlink()
    backup_path.unlink()
    _fsync_directory(role_directory)


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


class _StandaloneRestoreCapability:
    __slots__ = ()


_STANDALONE_RESTORE_CAPABILITY = _StandaloneRestoreCapability()


def restore_standalone_database_backup(
    *,
    backups_root: Path,
    runtime: DatabaseRuntime,
    backup_filename: str,
    expected_schema_revision: str,
    capability: object | None = None,
) -> StandaloneBackupManifest:
    """T-055: `ktdctl db-backup restore`. cache-target cutover window/journal과
    완전히 분리된, 언제든 단독 호출 가능한 복구다 — 그만큼 위험도 가장 크므로
    이중으로 fail-close한다.

    1차 방어는 CLI의 `--confirm` 없이는 이 함수 자체가 호출되지 않는 것이고,
    2차 방어는 이 `capability` sentinel이다 — 둘 중 하나가 뚫려도 나머지가 막는다.

    복구 전 대상 DB의 **현재** schema revision을 읽어 operator가 명시한
    `expected_schema_revision`과 정확히 일치하는지 대조하고, 다르면(또는 대상
    DB를 아예 읽을 수 없으면) 어떤 mutation도 하지 않고 즉시 거부한다 — T-050의
    `--expected-alembic-head` opt-in 패턴과 동일한 철학이다: "지금 무엇을
    덮어쓰는지 operator가 명시적으로 안다"는 것을 코드가 스스로 확인하기 전에는
    절대 진행하지 않는다.

    복구 직전 백업 파일을 재-해시해 manifest의 `sha256`과 대조한다 — 손상되거나
    변조된 백업으로부터는 복구하지 않는다. dropdb/createdb/pg_restore 시퀀스는
    기존 `restore_database_backup`과 동일한 stderr-정책-안전 패턴(존재 확인 뒤에만
    조건부 `dropdb`)을 따른다. 복구 뒤에는 결과 DB의 schema revision이 백업
    manifest가 기록한 값과 일치하는지 재확인한다.
    """

    if capability is not _STANDALONE_RESTORE_CAPABILITY:
        raise DeploymentContractError(
            "standalone database restore requires the restore capability"
        )
    _validate_runtime(runtime)
    if not _SCHEMA_REVISION.fullmatch(expected_schema_revision):
        raise DeploymentContractError(
            "standalone database restore expected schema revision is invalid"
        )
    current_schema_revision = _read_schema_revision(runtime)
    if current_schema_revision != expected_schema_revision:
        raise DeploymentContractError(
            f"{runtime.role} current schema revision differs from the "
            "operator-confirmed expectation"
        )
    role_directory = backups_root / runtime.role
    manifest_filename = (
        backup_filename.removesuffix(_STANDALONE_BACKUP_FILE_SUFFIX)
        + _STANDALONE_BACKUP_MANIFEST_SUFFIX
    )
    manifest_path = role_directory / manifest_filename
    try:
        manifest = _read_standalone_backup_manifest(
            manifest_path, expected_role=runtime.role
        )
    except DeploymentContractError as exc:
        raise DeploymentContractError(
            f"{runtime.role} standalone backup is not found or invalid"
        ) from exc
    if manifest.backup_filename != backup_filename:
        raise DeploymentContractError(
            f"{runtime.role} standalone backup id is invalid"
        )
    backup_path = role_directory / manifest.backup_filename
    payload_sha256, byte_size = _file_sha256(backup_path)
    if payload_sha256 != manifest.sha256 or byte_size != manifest.byte_size:
        raise DeploymentContractError(
            f"{runtime.role} standalone backup payload identity is invalid"
        )
    # `dropdb --if-exists`는 database가 원래 없을 때도 "does not exist, skipping"
    # NOTICE를 stderr에 낸다. `_run_checked`는 stderr가 하나라도 있으면 실패로
    # 처리하므로, database가 실제로 존재할 때만 drop을 실행한다(cache-target의
    # 동일 실공백에서 확인된 패턴 — `restore_database_backup`과 동일).
    if _read_database_owner(runtime) is not None:
        _run_checked(
            [
                *_database_admin_command(runtime, "dropdb"),
                "--if-exists",
                "--force",
                runtime.database_name,
            ],
            label=f"{runtime.role} standalone restore database drop",
        )
    _run_checked(
        [
            *_database_admin_command(runtime, "createdb"),
            "--owner",
            runtime.owner_name,
            runtime.database_name,
        ],
        label=f"{runtime.role} standalone restore database create",
    )
    try:
        with backup_path.open("rb") as dump:
            completed = subprocess.run(
                [
                    *_database_admin_command(runtime, "pg_restore", interactive=True),
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
            f"{runtime.role} standalone database restore could not run"
        ) from exc
    if completed.returncode != 0 or completed.stderr:
        raise DeploymentContractError(
            f"{runtime.role} standalone database restore failed"
        )
    if _read_schema_revision(runtime) != manifest.schema_revision:
        raise DeploymentContractError(
            f"{runtime.role} restored database schema revision differs from "
            "the backup manifest"
        )
    return manifest


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


def _stream_pg_dump_custom_format(
    output: IO[bytes],
    runtime: DatabaseRuntime,
    *,
    subprocess_failure_message: str,
) -> None:
    """T-057: cutover 내장 백업(`_write_pg_dump`)과 독립 백업
    (`create_standalone_database_backup`)이 각자 인라인으로 들고 있던 동일한
    `pg_dump --format=custom` subprocess 호출·fsync·성공 판정을 하나로 모았다.
    파일 생성 전략(idempotent 재사용 vs `O_CREAT|O_EXCL` 원자 선점)은 서로
    의미가 달라 호출자가 각자 소유하고, 이 함수는 그 사이에서 실제로 pg_dump를
    실행하는 부분만 공유한다. 에러 메시지는 호출자가 그대로 넘겨 기존 텍스트를
    바꾸지 않는다."""
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
        raise DeploymentContractError(subprocess_failure_message)


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
            _stream_pg_dump_custom_format(
                output,
                runtime,
                subprocess_failure_message=f"{runtime.role} database backup failed",
            )
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
