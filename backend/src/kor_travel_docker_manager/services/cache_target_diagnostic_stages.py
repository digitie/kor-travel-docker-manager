"""cache-target 사전 진단(T-049B)의 DB stage primitive.

설계 문서 3절의 9개 stage를 `cache_target_backup.py`의 기존 pg_dump/pg_restore
subprocess 패턴 위에서 typed `DiagnosticStageReceipt`로 분해한다. 각 함수는
예상 가능한 실패(subprocess nonzero, 정책상 거부된 stderr, timeout, 구조/무결성
불일치, admin command 실패, cleanup 실패)를 raise하지 않고 `status="failed"`
receipt로 반환한다. 입력 자체가 계약 위반인 경우(runtime/식별자 검증 실패)에만
`DeploymentContractError`를 raise한다.

`diagnose_scratch_create`부터 `diagnose_scratch_cleanup`까지는 독립적으로 호출
가능한 primitive이며, `cache_target_backup._rehearse_database_restore`의
try/finally cleanup 보장을 여기서는 재현하지 않는다. `diagnose_scratch_restore`
또는 그 이후 stage가 실패해도 `diagnose_scratch_cleanup`을 호출하는 것은
호출자(T-049C orchestration)의 책임이며, 호출하지 않으면 scratch database가
남는다.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_backup import (
    _DATABASE_IDENTIFIER,
    _DATABASE_RESTORE_TIMEOUT_SECONDS,
    DatabaseRuntime,
    _database_admin_command,
    _is_circular_foreign_key_restore_advisory,
    _read_database_identity,
    _read_database_owner,
    _run_checked,
    _validate_owner_only_directory,
    _validate_runtime,
)
from kor_travel_docker_manager.services.cache_target_diagnostics import (
    _MAX_STAGE_ELAPSED_MS,
    DiagnosticFailureClass,
    DiagnosticStage,
    DiagnosticStageReceipt,
)


def diagnostic_scratch_database_name(runtime: DatabaseRuntime, diagnostic_id: str) -> str:
    try:
        canonical = str(uuid.UUID(diagnostic_id))
    except ValueError as exc:
        raise DeploymentContractError(
            "cache-target diagnostic scratch transaction ID is invalid"
        ) from exc
    if canonical != diagnostic_id:
        raise DeploymentContractError(
            "cache-target diagnostic scratch transaction ID must be canonical"
        )
    suffix = diagnostic_id.replace("-", "")[:20]
    name = f"ktddiag_{runtime.role}_{suffix}"
    if not _DATABASE_IDENTIFIER.fullmatch(name):
        raise DeploymentContractError("cache-target diagnostic scratch database name is invalid")
    return name


def diagnose_source_archive(
    runtime: DatabaseRuntime,
    archive_path: Path,
) -> DiagnosticStageReceipt:
    _validate_runtime(runtime)
    _validate_owner_only_directory(archive_path.parent)
    start = time.monotonic()
    try:
        with archive_path.open("wb") as output:
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
    except subprocess.TimeoutExpired:
        return _stage_receipt(runtime.role, "source_archive", start, failure_class="timeout")
    except (OSError, subprocess.SubprocessError):
        return _stage_receipt(
            runtime.role, "source_archive", start, failure_class="subprocess_nonzero"
        )
    if completed.returncode != 0:
        return _stage_receipt(
            runtime.role, "source_archive", start, failure_class="subprocess_nonzero"
        )
    if completed.stderr:
        return _stage_receipt(
            runtime.role, "source_archive", start, failure_class="stderr_policy_rejected"
        )
    try:
        digest, size = _hash_file(archive_path)
    except OSError:
        return _stage_receipt(
            runtime.role, "source_archive", start, failure_class="subprocess_nonzero"
        )
    if size <= 0:
        return _stage_receipt(
            runtime.role, "source_archive", start, failure_class="subprocess_nonzero"
        )
    return _stage_receipt(runtime.role, "source_archive", start, archive_sha256=digest)


def diagnose_source_schema_inventory(runtime: DatabaseRuntime) -> DiagnosticStageReceipt:
    _validate_runtime(runtime)
    start = time.monotonic()
    digest, failure_class = _run_logical_inventory(runtime, schema_only=True)
    if failure_class is not None:
        return _stage_receipt(
            runtime.role, "source_schema_inventory", start, failure_class=failure_class
        )
    return _stage_receipt(
        runtime.role, "source_schema_inventory", start, schema_inventory_sha256=digest
    )


def diagnose_source_data_inventory(runtime: DatabaseRuntime) -> DiagnosticStageReceipt:
    _validate_runtime(runtime)
    start = time.monotonic()
    digest, failure_class = _run_logical_inventory(runtime, schema_only=False)
    if failure_class is not None:
        return _stage_receipt(
            runtime.role, "source_data_inventory", start, failure_class=failure_class
        )
    return _stage_receipt(
        runtime.role, "source_data_inventory", start, data_inventory_sha256=digest
    )


def diagnose_archive_structure(
    runtime: DatabaseRuntime,
    archive_path: Path,
) -> DiagnosticStageReceipt:
    _validate_runtime(runtime)
    _validate_owner_only_directory(archive_path.parent)
    start = time.monotonic()
    try:
        with archive_path.open("rb") as dump:
            completed = subprocess.run(
                [
                    *_database_admin_command(runtime, "pg_restore", interactive=True),
                    "--list",
                ],
                stdin=dump,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=300,
            )
    except (OSError, subprocess.SubprocessError):
        return _stage_receipt(
            runtime.role, "archive_structure", start, failure_class="archive_invalid"
        )
    if completed.returncode != 0 or completed.stderr:
        return _stage_receipt(
            runtime.role, "archive_structure", start, failure_class="archive_invalid"
        )
    return _stage_receipt(runtime.role, "archive_structure", start)


def diagnose_scratch_create(
    runtime: DatabaseRuntime,
    scratch_runtime: DatabaseRuntime,
    diagnostic_id: str,
) -> DiagnosticStageReceipt:
    _validate_runtime(runtime)
    _validate_runtime(scratch_runtime)
    _assert_scratch_does_not_collide_with_production(runtime, scratch_runtime)
    start = time.monotonic()
    try:
        stale_owner = _read_database_owner(scratch_runtime)
        if stale_owner not in (None, runtime.owner_name):
            return _stage_receipt(
                runtime.role, "scratch_create", start, failure_class="admin_command_failed"
            )
        # `dropdb --if-exists`는 scratch DB가 원래 없는(diagnostic의 일반적인) 경우에도
        # "does not exist, skipping" NOTICE를 stderr에 낸다. `_run_checked`는 stderr가
        # 하나라도 있으면 실패로 처리하므로, `_read_database_owner`가 이미 존재하지
        # 않음(None)을 확인해준 경우엔 dropdb 자체를 생략해 이 무해한 NOTICE가 매번
        # scratch_create를 거짓으로 실패시키는 것을 막는다. stale_owner가
        # runtime.owner_name과 일치하는(재사용 가능한) 경우에만 실제로 정리한다.
        if stale_owner is not None:
            _run_checked(
                [
                    *_database_admin_command(runtime, "dropdb"),
                    "--if-exists",
                    "--force",
                    scratch_runtime.database_name,
                ],
                label=f"{runtime.role} stale diagnostic scratch database cleanup",
            )
        _run_checked(
            [
                *_database_admin_command(runtime, "createdb"),
                "--owner",
                runtime.owner_name,
                scratch_runtime.database_name,
            ],
            label=f"{runtime.role} diagnostic scratch database create",
        )
        scratch_identity = _read_database_identity(scratch_runtime, diagnostic_id)
    except DeploymentContractError:
        return _stage_receipt(
            runtime.role, "scratch_create", start, failure_class="admin_command_failed"
        )
    return _stage_receipt(
        runtime.role,
        "scratch_create",
        start,
        scratch_identity_sha256=scratch_identity,
    )


def diagnose_scratch_restore(
    runtime: DatabaseRuntime,
    scratch_runtime: DatabaseRuntime,
    archive_path: Path,
) -> DiagnosticStageReceipt:
    _validate_runtime(runtime)
    _validate_runtime(scratch_runtime)
    _assert_scratch_does_not_collide_with_production(runtime, scratch_runtime)
    _validate_owner_only_directory(archive_path.parent)
    start = time.monotonic()
    try:
        with archive_path.open("rb") as dump:
            completed = subprocess.run(
                [
                    *_database_admin_command(scratch_runtime, "pg_restore", interactive=True),
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    "--dbname",
                    scratch_runtime.database_name,
                ],
                stdin=dump,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=_DATABASE_RESTORE_TIMEOUT_SECONDS,
            )
    except subprocess.TimeoutExpired:
        return _stage_receipt(runtime.role, "scratch_restore", start, failure_class="timeout")
    except (OSError, subprocess.SubprocessError):
        return _stage_receipt(
            runtime.role, "scratch_restore", start, failure_class="restore_failed"
        )
    if completed.returncode != 0 or completed.stderr:
        return _stage_receipt(
            runtime.role, "scratch_restore", start, failure_class="restore_failed"
        )
    return _stage_receipt(runtime.role, "scratch_restore", start)


def diagnose_scratch_schema_inventory(
    runtime: DatabaseRuntime,
    scratch_runtime: DatabaseRuntime,
    *,
    expected_schema_inventory_sha256: str,
) -> DiagnosticStageReceipt:
    _validate_runtime(runtime)
    _validate_runtime(scratch_runtime)
    _assert_scratch_does_not_collide_with_production(runtime, scratch_runtime)
    start = time.monotonic()
    digest, failure_class = _run_logical_inventory(scratch_runtime, schema_only=True)
    if failure_class is not None:
        return _stage_receipt(
            runtime.role, "scratch_schema_inventory", start, failure_class=failure_class
        )
    if digest != expected_schema_inventory_sha256:
        return _stage_receipt(
            runtime.role,
            "scratch_schema_inventory",
            start,
            failure_class="inventory_mismatch",
        )
    return _stage_receipt(
        runtime.role,
        "scratch_schema_inventory",
        start,
        schema_inventory_sha256=digest,
    )


def diagnose_scratch_data_inventory(
    runtime: DatabaseRuntime,
    scratch_runtime: DatabaseRuntime,
    *,
    expected_data_inventory_sha256: str,
) -> DiagnosticStageReceipt:
    _validate_runtime(runtime)
    _validate_runtime(scratch_runtime)
    _assert_scratch_does_not_collide_with_production(runtime, scratch_runtime)
    start = time.monotonic()
    digest, failure_class = _run_logical_inventory(scratch_runtime, schema_only=False)
    if failure_class is not None:
        return _stage_receipt(
            runtime.role, "scratch_data_inventory", start, failure_class=failure_class
        )
    if digest != expected_data_inventory_sha256:
        return _stage_receipt(
            runtime.role,
            "scratch_data_inventory",
            start,
            failure_class="inventory_mismatch",
        )
    return _stage_receipt(
        runtime.role,
        "scratch_data_inventory",
        start,
        data_inventory_sha256=digest,
    )


def diagnose_scratch_cleanup(
    runtime: DatabaseRuntime,
    scratch_runtime: DatabaseRuntime,
) -> DiagnosticStageReceipt:
    _validate_runtime(runtime)
    _validate_runtime(scratch_runtime)
    _assert_scratch_does_not_collide_with_production(runtime, scratch_runtime)
    start = time.monotonic()
    try:
        _run_checked(
            [
                *_database_admin_command(runtime, "dropdb"),
                "--if-exists",
                "--force",
                scratch_runtime.database_name,
            ],
            label=f"{runtime.role} diagnostic scratch database cleanup",
        )
        owner_after = _read_database_owner(scratch_runtime)
    except DeploymentContractError:
        return _stage_receipt(
            runtime.role, "scratch_cleanup", start, failure_class="cleanup_failed"
        )
    if owner_after is not None:
        return _stage_receipt(
            runtime.role, "scratch_cleanup", start, failure_class="cleanup_failed"
        )
    return _stage_receipt(runtime.role, "scratch_cleanup", start)


def _assert_scratch_does_not_collide_with_production(
    runtime: DatabaseRuntime,
    scratch_runtime: DatabaseRuntime,
) -> None:
    if scratch_runtime.database_name == runtime.database_name:
        raise DeploymentContractError(
            "cache-target diagnostic scratch database collides with production"
        )


def remove_diagnostic_archive(archive_path: Path) -> None:
    """source archive를 root-only 임시 영역에서 제거한다. 성공·실패 무관하게 호출된다."""

    archive_path.unlink(missing_ok=True)


def _run_logical_inventory(
    runtime: DatabaseRuntime,
    *,
    schema_only: bool,
) -> tuple[str | None, DiagnosticFailureClass | None]:
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
            if completed.returncode != 0:
                return None, "subprocess_nonzero"
            if completed.stderr and (
                schema_only or not _is_circular_foreign_key_restore_advisory(completed.stderr)
            ):
                return None, "stderr_policy_rejected"
            output.seek(0)
            digest = hashlib.sha256()
            while chunk := output.read(1024 * 1024):
                digest.update(chunk)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except (OSError, subprocess.SubprocessError):
        return None, "subprocess_nonzero"
    return digest.hexdigest(), None


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _stage_receipt(
    role: str,
    stage: DiagnosticStage,
    start: float,
    *,
    failure_class: DiagnosticFailureClass | None = None,
    archive_sha256: str | None = None,
    schema_inventory_sha256: str | None = None,
    data_inventory_sha256: str | None = None,
    scratch_identity_sha256: str | None = None,
) -> DiagnosticStageReceipt:
    elapsed_ms = max(0, min(_MAX_STAGE_ELAPSED_MS, round((time.monotonic() - start) * 1000)))
    return DiagnosticStageReceipt(
        role=role,  # type: ignore[arg-type]
        stage=stage,
        status="failed" if failure_class is not None else "succeeded",
        failure_class=failure_class,
        elapsed_ms=elapsed_ms,
        archive_sha256=archive_sha256,
        schema_inventory_sha256=schema_inventory_sha256,
        data_inventory_sha256=data_inventory_sha256,
        scratch_identity_sha256=scratch_identity_sha256,
    )
