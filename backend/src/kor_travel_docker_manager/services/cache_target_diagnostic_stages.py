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
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import replace
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


def diagnose_source_schema_inventory(
    runtime: DatabaseRuntime,
    diagnostic_id: str,
) -> DiagnosticStageReceipt:
    _validate_runtime(runtime)
    start = time.monotonic()
    digest, failure_class = _run_normalized_source_schema_inventory(runtime, diagnostic_id)
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


# PostgreSQL은 원본 DDL 텍스트의 `(ARRAY['a', 'b'])::text[]`(배열 전체 cast) 형태
# CHECK 제약을, dump한 그 텍스트를 다시 파싱해 저장할 때(즉 restore 뒤) constant
# folding으로 `ARRAY[('a')::text, ('b')::text]`(원소별 cast) 형태로 바꿔 저장한다.
# 의미는 완전히 동일하지만 `pg_get_constraintdef`의 재-deparse 결과가 달라져, source와
# scratch(restore된) 스키마의 raw pg_dump 텍스트가 byte-identical하지 않게 된다
# (n150 pinvi 스키마 실측: 이 패턴만으로 88줄 차이, 다른 비결정성 없음 확인).
# schema inventory hash를 이 렌더링 차이에 안전하게 만들기 위해, hash 전에 원소별
# cast 형태를 배열 전체 cast 형태로 정규화한다.
_PER_ELEMENT_ARRAY_CAST = re.compile(
    rb"ARRAY\[((?:\('(?:[^']|'')*'::[\w ]+\)::text,\s*)*\('(?:[^']|'')*'::[\w ]+\)::text)\]"
)
_PER_ELEMENT_ARRAY_ITEM = re.compile(rb"\('((?:[^']|'')*)'::([\w ]+)\)::text")


def _canonicalize_schema_dump(raw: bytes) -> bytes:
    def _rewrite(match: re.Match[bytes]) -> bytes:
        items = _PER_ELEMENT_ARRAY_ITEM.findall(match.group(1))
        elements = b", ".join(b"'" + value + b"'::" + cast_type for value, cast_type in items)
        return b"(ARRAY[" + elements + b"])::text[]"

    return _PER_ELEMENT_ARRAY_CAST.sub(_rewrite, raw)


def _split_sql_statements(raw: bytes) -> list[bytes]:
    """`;`로 top-level(문자열 리터럴 밖) 경계에서만 문장을 나눈다.

    `--rows-per-insert=1`이어도 값 안에 실제 개행 문자가 들어있으면 한 INSERT가
    여러 물리적 줄에 걸칠 수 있어, 단순 줄 단위 분리는 안전하지 않다.
    """

    statements: list[bytes] = []
    start = 0
    in_string = False
    index = 0
    length = len(raw)
    while index < length:
        byte = raw[index : index + 1]
        if in_string:
            if byte == b"'":
                if raw[index + 1 : index + 2] == b"'":
                    index += 2
                    continue
                in_string = False
            index += 1
            continue
        if byte == b"'":
            in_string = True
            index += 1
            continue
        if byte == b";":
            statements.append(raw[start : index + 1])
            start = index + 1
        index += 1
    tail = raw[start:]
    if tail.strip():
        statements.append(tail)
    return statements


def _canonicalize_data_dump(raw: bytes) -> bytes:
    """행 emission 순서에 무관하게 만들되 내용 변화는 그대로 잡아낸다.

    scratch DB는 archive를 restore해서 만들어지는데, 그 restore 순서가 source의
    원래 물리적 힙 순서와 달라질 수 있어(n150 map_dagster `job_ticks` 실측: 행
    내용은 100% 동일, 위치만 다름) `--data-only --inserts` 출력을 그대로 hash하면
    같은 데이터도 다른 digest가 나온다. 문장을 quote-aware하게 분리해 정렬한 뒤
    hash하면 순서는 무시하고 실제 내용 변화(행 추가/삭제/수정)만 감지한다.
    """

    statements = [statement.strip() for statement in _split_sql_statements(raw)]
    statements = [statement for statement in statements if statement]
    statements.sort()
    return b"\n".join(statements)


def _schema_roundtrip_database_name(runtime: DatabaseRuntime, diagnostic_id: str) -> str:
    name = diagnostic_scratch_database_name(runtime, diagnostic_id) + "_sr"
    if not _DATABASE_IDENTIFIER.fullmatch(name):
        raise DeploymentContractError(
            "cache-target diagnostic schema round-trip database name is invalid"
        )
    return name


def _dump_schema_only_raw(
    runtime: DatabaseRuntime,
) -> tuple[bytes | None, DiagnosticFailureClass | None]:
    arguments = [
        *_database_admin_command(runtime, "pg_dump"),
        "--no-owner",
        "--no-acl",
        "--schema-only",
        "--dbname",
        runtime.database_name,
    ]
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
            if completed.stderr:
                return None, "stderr_policy_rejected"
            output.seek(0)
            return output.read(), None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except (OSError, subprocess.SubprocessError):
        return None, "subprocess_nonzero"


def _run_normalized_source_schema_inventory(
    runtime: DatabaseRuntime,
    diagnostic_id: str,
) -> tuple[str | None, DiagnosticFailureClass | None]:
    """source의 schema-only dump를 scratch와 같은 조건(한 번의 dump→적용→dump
    round trip)으로 정규화한 뒤 hash한다.

    PostgreSQL은 CHECK 제약을 원문 그대로 재-deparse하지 않는다 — DDL을 다시
    파싱해서 저장할 때 AND 중첩을 평탄화하거나(`(A AND B) AND (C AND D)` ->
    `A AND B AND (C AND D)`) 배열 cast 표현을 바꾸는(`_canonicalize_schema_dump`가
    막는 것과 같은 종류지만 다른 패턴) 등, 알려진 것만 나열해 정규식으로 막기엔
    끝이 없는 방식으로 텍스트가 달라진다(n150 krtour_map 실측: AND 중첩 평탄화가
    ARRAY cast와 별개로 5개 이상 제약에서 재현됨). scratch는 `source_archive`
    dump를 restore해서 만들어지므로 이미 "한 번의 dump-적용-dump" 변환을 거친
    상태다 — source도 같은 변환을 한 번 거치게 하면, 어떤 구체적 재-deparse
    패턴이든 양쪽에 동일하게 적용되어 비교가 항상 대칭이 된다(개별 패턴을
    쫓아다니지 않아도 된다).
    """

    raw, failure_class = _dump_schema_only_raw(runtime)
    if failure_class is not None:
        return None, failure_class
    roundtrip_runtime = replace(
        runtime, database_name=_schema_roundtrip_database_name(runtime, diagnostic_id)
    )
    created = False
    try:
        stale_owner = _read_database_owner(roundtrip_runtime)
        if stale_owner not in (None, runtime.owner_name):
            return None, "admin_command_failed"
        # `dropdb --if-exists`는 대상이 원래 없을 때도 "does not exist, skipping"
        # NOTICE를 stderr에 낸다 — `_run_checked`는 stderr가 하나라도 있으면 실패로
        # 처리하므로, 실제로 뭔가 있을 때만(stale_owner is not None) 정리한다
        # (diagnose_scratch_create의 동일 실공백 수정과 같은 패턴).
        if stale_owner is not None:
            _run_checked(
                [
                    *_database_admin_command(runtime, "dropdb"),
                    "--if-exists",
                    "--force",
                    roundtrip_runtime.database_name,
                ],
                label=f"{runtime.role} schema round-trip database cleanup",
            )
        _run_checked(
            [
                *_database_admin_command(runtime, "createdb"),
                "--owner",
                runtime.owner_name,
                roundtrip_runtime.database_name,
            ],
            label=f"{runtime.role} schema round-trip database create",
        )
        created = True
        completed = subprocess.run(
            [
                *_database_admin_command(roundtrip_runtime, "psql", interactive=True),
                "--no-psqlrc",
                "--dbname",
                roundtrip_runtime.database_name,
                "--set",
                "ON_ERROR_STOP=1",
            ],
            input=raw,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=300,
        )
        if completed.returncode != 0 or completed.stderr:
            return None, "subprocess_nonzero"
        return _run_logical_inventory(roundtrip_runtime, schema_only=True)
    except DeploymentContractError:
        return None, "admin_command_failed"
    except (OSError, subprocess.SubprocessError):
        return None, "subprocess_nonzero"
    finally:
        # 여기서는 `created`가 True일 때만(실제로 존재할 때만) drop한다 — 위와 같은
        # 이유로 무조건 dropdb를 호출하면 존재하지 않는 경우의 NOTICE가 실패로
        # 오탐된다. drop 자체가 실패해도(권한 등) 이 함수가 이미 계산한
        # 성공/실패 결과를 덮어쓰지 않는다.
        if created:
            try:
                _run_checked(
                    [
                        *_database_admin_command(runtime, "dropdb"),
                        "--if-exists",
                        "--force",
                        roundtrip_runtime.database_name,
                    ],
                    label=f"{runtime.role} schema round-trip database final cleanup",
                )
            except DeploymentContractError:
                pass


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
            # 순서-무관 비교(canonicalize)는 문장 경계를 알아야 해서 기존의
            # 1MB 청크 스트리밍 hash를 쓸 수 없다 — 전체를 메모리에 올린다. 가장 큰
            # 실측 테이블(map_application feature_weather_values, 1,780만 행)의
            # data-only dump가 수 GB대라 이 role의 메모리 사용량이 그만큼 늘어난다.
            # 근본적으로 스트리밍 가능한 순서-무관 비교(예: 행별 hash를
            # XOR/합산하는 SQL 집계)로 바꾸는 건 별도 후속 작업이다.
            raw = output.read()
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except (OSError, subprocess.SubprocessError):
        return None, "subprocess_nonzero"
    canonical = _canonicalize_schema_dump(raw) if schema_only else _canonicalize_data_dump(raw)
    digest = hashlib.sha256(canonical)
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
