from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kor_travel_docker_manager.services.cache_target_backup import (
    _DATABASE_RESTORE_TIMEOUT_SECONDS,
    DatabaseRuntime,
)
from kor_travel_docker_manager.services.cache_target_diagnostic_stages import (
    _canonicalize_data_dump,
    _canonicalize_schema_dump,
    _split_sql_statements,
    diagnose_archive_structure,
    diagnose_scratch_cleanup,
    diagnose_scratch_create,
    diagnose_scratch_data_inventory,
    diagnose_scratch_restore,
    diagnose_scratch_schema_inventory,
    diagnose_source_archive,
    diagnose_source_data_inventory,
    diagnose_source_schema_inventory,
    diagnostic_scratch_database_name,
    remove_diagnostic_archive,
)

_DIAGNOSTIC_ID = "8a3e6b2c-8f1e-4c8b-9c3d-0f1a2b3c4d5e"

_RUNTIME = DatabaseRuntime(
    role="map_application",
    container_name="krtour-postgres",
    database_name="krtour_map",
    owner_name="krtour_map",
    admin_name="krtour_map",
)


def _scratch_runtime() -> DatabaseRuntime:
    name = diagnostic_scratch_database_name(_RUNTIME, _DIAGNOSTIC_ID)
    return DatabaseRuntime(
        role=_RUNTIME.role,
        container_name=_RUNTIME.container_name,
        database_name=name,
        owner_name=_RUNTIME.owner_name,
        admin_name=_RUNTIME.admin_name,
    )


def _fake_schema_roundtrip_run(schema_text: bytes, *, source_dump_stderr: bytes = b"") -> Any:
    """`diagnose_source_schema_inventory`의 round-trip 흐름(source dump ->
    `_read_database_owner` 조회 -> createdb -> psql 적용 -> round-trip DB dump)을
    시뮬레이션하는 `subprocess.run` 대체 함수를 만든다. 실제 PostgreSQL 재-deparse는
    일어나지 않으므로(fake이므로) round-trip 뒤 텍스트는 입력과 동일하게 돌려주고,
    `_canonicalize_schema_dump`의 정규식 정규화만 검증한다. 첫 번째(source) dump
    호출에만 `source_dump_stderr`를 반영한다."""

    call_count = 0

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        nonlocal call_count
        if "pg_dump" in args:
            call_count += 1
            if "stdout" in kwargs and hasattr(kwargs["stdout"], "write"):
                kwargs["stdout"].write(schema_text)
            stderr = source_dump_stderr if call_count == 1 else b""
            return _Completed(stderr=stderr)
        if "psql" in args and "--command" in args:
            return _Completed()
        if "createdb" in args or "dropdb" in args:
            return _Completed()
        if "psql" in args:
            return _Completed()
        return _Completed()

    return fake_run


class _Completed:
    def __init__(self, returncode: int = 0, stderr: bytes = b"", stdout: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def test_diagnostic_scratch_database_name_is_deterministic_and_valid() -> None:
    name = diagnostic_scratch_database_name(_RUNTIME, _DIAGNOSTIC_ID)
    assert name.startswith("ktddiag_map_application_")
    assert name == diagnostic_scratch_database_name(_RUNTIME, _DIAGNOSTIC_ID)


def test_diagnostic_scratch_database_name_rejects_noncanonical_id() -> None:
    with pytest.raises(Exception, match="canonical"):
        diagnostic_scratch_database_name(_RUNTIME, _DIAGNOSTIC_ID.upper())


def test_diagnose_source_archive_succeeds(tmp_path: Path) -> None:
    archive_path = tmp_path / "map_application.dump"

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        kwargs["stdout"].write(b"pgdump-archive-bytes")
        return _Completed()

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_source_archive(_RUNTIME, archive_path)

    assert receipt.status == "succeeded"
    assert receipt.stage == "source_archive"
    assert receipt.role == "map_application"
    assert receipt.archive_sha256 is not None
    assert receipt.failure_class is None


def test_diagnose_source_archive_rejects_nonzero_exit(tmp_path: Path) -> None:
    archive_path = tmp_path / "map_application.dump"
    with patch("subprocess.run", return_value=_Completed(returncode=1)):
        receipt = diagnose_source_archive(_RUNTIME, archive_path)
    assert receipt.status == "failed"
    assert receipt.failure_class == "subprocess_nonzero"


def test_diagnose_source_archive_rejects_unexpected_stderr(tmp_path: Path) -> None:
    archive_path = tmp_path / "map_application.dump"

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        kwargs["stdout"].write(b"partial")
        return _Completed(stderr=b"pg_dump: error: something went wrong")

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_source_archive(_RUNTIME, archive_path)
    assert receipt.status == "failed"
    assert receipt.failure_class == "stderr_policy_rejected"


def test_diagnose_source_archive_reports_timeout(tmp_path: Path) -> None:
    archive_path = tmp_path / "map_application.dump"
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pg_dump", timeout=3600),
    ):
        receipt = diagnose_source_archive(_RUNTIME, archive_path)
    assert receipt.status == "failed"
    assert receipt.failure_class == "timeout"


def test_diagnose_source_schema_inventory_succeeds() -> None:
    fake_run = _fake_schema_roundtrip_run(b"CREATE TABLE example ();")

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_source_schema_inventory(_RUNTIME, _DIAGNOSTIC_ID)
    assert receipt.status == "succeeded"
    assert receipt.schema_inventory_sha256 is not None


def test_diagnose_source_schema_inventory_rejects_any_stderr() -> None:
    advisory = (
        b"pg_dump: warning: there are circular foreign-key constraints "
        b"on this table:\npg_dump: detail: t\n"
        b"pg_dump: hint: You might not be able to restore the dump without using "
        b"--disable-triggers or temporarily dropping the constraints.\n"
        b"pg_dump: hint: Consider using a full dump instead of a --data-only dump "
        b"to avoid this problem."
    )
    fake_run = _fake_schema_roundtrip_run(b"", source_dump_stderr=advisory)

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_source_schema_inventory(_RUNTIME, _DIAGNOSTIC_ID)
    assert receipt.status == "failed"
    assert receipt.failure_class == "stderr_policy_rejected"


def test_diagnose_source_data_inventory_accepts_circular_fk_advisory() -> None:
    advisory = (
        b"pg_dump: warning: there are circular foreign-key constraints on this table:\n"
        b"pg_dump: detail: t\n"
        b"pg_dump: hint: You might not be able to restore the dump without using "
        b"--disable-triggers or temporarily dropping the constraints.\n"
        b"pg_dump: hint: Consider using a full dump instead of a --data-only dump "
        b"to avoid this problem."
    )

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        kwargs["stdout"].write(b"INSERT INTO example VALUES (1);")
        return _Completed(stderr=advisory)

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_source_data_inventory(_RUNTIME)
    assert receipt.status == "succeeded"
    assert receipt.data_inventory_sha256 is not None


def test_diagnose_source_data_inventory_rejects_unknown_advisory() -> None:
    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        return _Completed(stderr=b"pg_dump: warning: something else entirely")

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_source_data_inventory(_RUNTIME)
    assert receipt.status == "failed"
    assert receipt.failure_class == "stderr_policy_rejected"


def test_split_sql_statements_handles_embedded_newline_in_quoted_value() -> None:
    raw = b"INSERT INTO t VALUES (1, 'line1\nline2');\nINSERT INTO t VALUES (2, 'x');\n"
    statements = [s.strip() for s in _split_sql_statements(raw) if s.strip()]
    assert statements == [
        b"INSERT INTO t VALUES (1, 'line1\nline2');",
        b"INSERT INTO t VALUES (2, 'x');",
    ]


def test_split_sql_statements_handles_escaped_quote() -> None:
    raw = b"INSERT INTO t VALUES (1, 'it''s; still one value');\n"
    statements = [s.strip() for s in _split_sql_statements(raw) if s.strip()]
    assert statements == [b"INSERT INTO t VALUES (1, 'it''s; still one value');"]


def test_canonicalize_data_dump_is_order_insensitive_same_content() -> None:
    a = b"INSERT INTO t VALUES (1, 'x');\nINSERT INTO t VALUES (2, 'y');\n"
    b = b"INSERT INTO t VALUES (2, 'y');\nINSERT INTO t VALUES (1, 'x');\n"
    assert _canonicalize_data_dump(a) == _canonicalize_data_dump(b)


def test_canonicalize_data_dump_detects_modified_row() -> None:
    a = b"INSERT INTO t VALUES (1, 'x');\nINSERT INTO t VALUES (2, 'y');\n"
    modified = b"INSERT INTO t VALUES (1, 'x');\nINSERT INTO t VALUES (2, 'CHANGED');\n"
    assert _canonicalize_data_dump(a) != _canonicalize_data_dump(modified)


def test_canonicalize_data_dump_detects_missing_row() -> None:
    full = b"INSERT INTO t VALUES (1, 'x');\nINSERT INTO t VALUES (2, 'y');\n"
    missing = b"INSERT INTO t VALUES (1, 'x');\n"
    assert _canonicalize_data_dump(full) != _canonicalize_data_dump(missing)


def test_canonicalize_data_dump_detects_extra_row() -> None:
    base = b"INSERT INTO t VALUES (1, 'x');\nINSERT INTO t VALUES (2, 'y');\n"
    extra = base + b"INSERT INTO t VALUES (3, 'z');\n"
    assert _canonicalize_data_dump(base) != _canonicalize_data_dump(extra)


def test_canonicalize_schema_dump_normalizes_per_element_array_cast() -> None:
    whole_array_cast = (
        b"CHECK (((x)::text = ANY ((ARRAY['a'::character varying, "
        b"'b'::character varying])::text[])))"
    )
    per_element_cast = (
        b"CHECK (((x)::text = ANY (ARRAY[('a'::character varying)::text, "
        b"('b'::character varying)::text])))"
    )
    assert _canonicalize_schema_dump(whole_array_cast) == _canonicalize_schema_dump(
        per_element_cast
    )


def test_canonicalize_schema_dump_detects_real_constraint_change() -> None:
    original = (
        b"CHECK (((x)::text = ANY ((ARRAY['a'::character varying, "
        b"'b'::character varying])::text[])))"
    )
    changed = (
        b"CHECK (((x)::text = ANY (ARRAY[('a'::character varying)::text, "
        b"('DIFFERENT'::character varying)::text])))"
    )
    assert _canonicalize_schema_dump(original) != _canonicalize_schema_dump(changed)


def test_diagnose_source_data_inventory_hash_is_order_insensitive() -> None:
    """T-049E n150 실측: source vs scratch(restore된) 데이터 dump가 행 emission
    순서만 다르고 내용은 동일할 때, hash가 일치해야 한다(더는 inventory_mismatch로
    오탐하지 않는다)."""

    def fake_run_a(args: list[str], **kwargs: Any) -> _Completed:
        kwargs["stdout"].write(b"INSERT INTO t VALUES (1, 'x');\nINSERT INTO t VALUES (2, 'y');\n")
        return _Completed()

    def fake_run_b(args: list[str], **kwargs: Any) -> _Completed:
        kwargs["stdout"].write(b"INSERT INTO t VALUES (2, 'y');\nINSERT INTO t VALUES (1, 'x');\n")
        return _Completed()

    with patch("subprocess.run", side_effect=fake_run_a):
        receipt_a = diagnose_source_data_inventory(_RUNTIME)
    with patch("subprocess.run", side_effect=fake_run_b):
        receipt_b = diagnose_source_data_inventory(_RUNTIME)

    assert receipt_a.status == "succeeded"
    assert receipt_b.status == "succeeded"
    assert receipt_a.data_inventory_sha256 == receipt_b.data_inventory_sha256


def test_diagnose_source_schema_inventory_hash_is_cast_rendering_insensitive() -> None:
    """T-049E n150 실측: 동일 CHECK 제약이 배열 전체 cast/원소별 cast로 다르게
    렌더링돼도 schema hash는 일치해야 한다."""

    fake_run_whole = _fake_schema_roundtrip_run(
        b"CHECK (((x)::text = ANY ((ARRAY['a'::character varying, "
        b"'b'::character varying])::text[])))"
    )
    fake_run_per_element = _fake_schema_roundtrip_run(
        b"CHECK (((x)::text = ANY (ARRAY[('a'::character varying)::text, "
        b"('b'::character varying)::text])))"
    )

    with patch("subprocess.run", side_effect=fake_run_whole):
        receipt_whole = diagnose_source_schema_inventory(_RUNTIME, _DIAGNOSTIC_ID)
    with patch("subprocess.run", side_effect=fake_run_per_element):
        receipt_per_element = diagnose_source_schema_inventory(_RUNTIME, _DIAGNOSTIC_ID)

    assert receipt_whole.status == "succeeded"
    assert receipt_per_element.status == "succeeded"
    assert receipt_whole.schema_inventory_sha256 == receipt_per_element.schema_inventory_sha256


def test_diagnose_source_schema_inventory_cleans_up_roundtrip_database_on_success() -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        calls.append(args)
        if "pg_dump" in args and "stdout" in kwargs and hasattr(kwargs["stdout"], "write"):
            kwargs["stdout"].write(b"CREATE TABLE example ();")
        return _Completed()

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_source_schema_inventory(_RUNTIME, _DIAGNOSTIC_ID)

    assert receipt.status == "succeeded"
    assert any("createdb" in call for call in calls)
    assert any("dropdb" in call for call in calls)


def test_diagnose_source_schema_inventory_skips_roundtrip_dropdb_when_createdb_never_ran() -> None:
    """round-trip database가 createdb까지 못 갔으면(생성된 적 없으면) cleanup에서
    무조건 dropdb를 호출해선 안 된다 — 존재하지 않는 대상의 `dropdb --if-exists`
    NOTICE가 `_run_checked`의 stderr 정책에 걸려 오탐을 만드는 것과 같은 함정이다.
    `_read_database_owner`가 foreign owner를 발견해 createdb 이전에 실패하는
    경로를 재현한다."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        calls.append(args)
        if "pg_dump" in args and "stdout" in kwargs and hasattr(kwargs["stdout"], "write"):
            kwargs["stdout"].write(b"CREATE TABLE example ();")
            return _Completed()
        if "psql" in args and "--command" in args:
            return _Completed(stdout=b"someone_else\n")
        return _Completed()

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_source_schema_inventory(_RUNTIME, _DIAGNOSTIC_ID)

    assert receipt.status == "failed"
    assert receipt.failure_class == "admin_command_failed"
    assert not any("createdb" in call for call in calls)
    assert not any("dropdb" in call for call in calls)


def test_diagnose_archive_structure_succeeds(tmp_path: Path) -> None:
    archive_path = tmp_path / "map_application.dump"
    archive_path.write_bytes(b"fake-archive")
    with patch("subprocess.run", return_value=_Completed()):
        receipt = diagnose_archive_structure(_RUNTIME, archive_path)
    assert receipt.status == "succeeded"


def test_diagnose_archive_structure_rejects_invalid_archive(tmp_path: Path) -> None:
    archive_path = tmp_path / "map_application.dump"
    archive_path.write_bytes(b"fake-archive")
    with patch("subprocess.run", return_value=_Completed(returncode=1)):
        receipt = diagnose_archive_structure(_RUNTIME, archive_path)
    assert receipt.status == "failed"
    assert receipt.failure_class == "archive_invalid"


def test_diagnose_scratch_create_rejects_foreign_owner() -> None:
    scratch_runtime = _scratch_runtime()
    with patch(
        "kor_travel_docker_manager.services.cache_target_diagnostic_stages._read_database_owner",
        return_value="someone_else",
    ):
        receipt = diagnose_scratch_create(_RUNTIME, scratch_runtime, _DIAGNOSTIC_ID)
    assert receipt.status == "failed"
    assert receipt.failure_class == "admin_command_failed"


def test_diagnose_scratch_create_rejects_colliding_database_name() -> None:
    with pytest.raises(Exception, match="collides with production"):
        diagnose_scratch_create(_RUNTIME, _RUNTIME, _DIAGNOSTIC_ID)


def test_diagnose_scratch_create_skips_dropdb_when_nothing_stale_exists() -> None:
    """`dropdb --if-exists`는 scratch DB가 원래 없을 때도 "does not exist, skipping"
    NOTICE를 stderr에 낸다. `_read_database_owner`가 이미 존재하지 않음(None)을
    확인해준 일반적인 경우, 이 무해한 NOTICE가 `_run_checked`의 any-stderr-is-failure
    정책에 걸려 매번 admin_command_failed로 오탐하지 않도록 dropdb 자체를 생략해야
    한다(실 production에서 재현된 회귀)."""
    scratch_runtime = _scratch_runtime()
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        calls.append(args)
        return _Completed(stdout=b"123456789012345")

    with (
        patch(
            "kor_travel_docker_manager.services.cache_target_diagnostic_stages._read_database_owner",
            return_value=None,
        ),
        patch("subprocess.run", side_effect=fake_run),
    ):
        receipt = diagnose_scratch_create(_RUNTIME, scratch_runtime, _DIAGNOSTIC_ID)

    assert receipt.status == "succeeded"
    assert receipt.failure_class is None
    assert not any("dropdb" in call for call in calls)
    assert any("createdb" in call for call in calls)


def test_diagnose_scratch_create_runs_dropdb_when_reusable_stale_database_exists() -> None:
    scratch_runtime = _scratch_runtime()
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        calls.append(args)
        return _Completed(stdout=b"123456789012345")

    with (
        patch(
            "kor_travel_docker_manager.services.cache_target_diagnostic_stages._read_database_owner",
            return_value=_RUNTIME.owner_name,
        ),
        patch("subprocess.run", side_effect=fake_run),
    ):
        receipt = diagnose_scratch_create(_RUNTIME, scratch_runtime, _DIAGNOSTIC_ID)

    assert receipt.status == "succeeded"
    assert any("dropdb" in call for call in calls)


def test_diagnose_scratch_restore_reports_restore_failure(tmp_path: Path) -> None:
    scratch_runtime = _scratch_runtime()
    archive_path = tmp_path / "map_application.dump"
    archive_path.write_bytes(b"fake-archive")
    with patch("subprocess.run", return_value=_Completed(returncode=1)):
        receipt = diagnose_scratch_restore(_RUNTIME, scratch_runtime, archive_path)
    assert receipt.status == "failed"
    assert receipt.failure_class == "restore_failed"


def test_diagnose_scratch_restore_succeeds(tmp_path: Path) -> None:
    scratch_runtime = _scratch_runtime()
    archive_path = tmp_path / "map_application.dump"
    archive_path.write_bytes(b"fake-archive")
    with patch("subprocess.run", return_value=_Completed()):
        receipt = diagnose_scratch_restore(_RUNTIME, scratch_runtime, archive_path)
    assert receipt.status == "succeeded"
    assert receipt.failure_class is None


def test_diagnose_scratch_restore_uses_shared_restore_timeout(tmp_path: Path) -> None:
    """n150 실측(2026-08-03): map_application의 feature.feature_weather_values
    단일 테이블만으로 pg_restore가 약 97분 걸려 기존 3600초(60분) timeout에
    걸렸다. `_DATABASE_RESTORE_TIMEOUT_SECONDS`(cache_target_backup.py 공유
    상수)를 실제로 쓰는지 확인한다 — 하드코딩된 3600으로 되돌아가면 이 회귀가
    다시 재현된다."""
    scratch_runtime = _scratch_runtime()
    archive_path = tmp_path / "map_application.dump"
    archive_path.write_bytes(b"fake-archive")
    captured_kwargs: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        captured_kwargs.update(kwargs)
        return _Completed()

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_scratch_restore(_RUNTIME, scratch_runtime, archive_path)

    assert receipt.status == "succeeded"
    assert captured_kwargs["timeout"] == _DATABASE_RESTORE_TIMEOUT_SECONDS
    assert captured_kwargs["timeout"] > 3600


def test_diagnose_scratch_restore_rejects_colliding_scratch_runtime(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "map_application.dump"
    archive_path.write_bytes(b"fake-archive")
    with pytest.raises(Exception, match="collides with production"):
        diagnose_scratch_restore(_RUNTIME, _RUNTIME, archive_path)


def test_diagnose_source_archive_rejects_empty_output(tmp_path: Path) -> None:
    archive_path = tmp_path / "map_application.dump"
    with patch("subprocess.run", return_value=_Completed()):
        receipt = diagnose_source_archive(_RUNTIME, archive_path)
    assert receipt.status == "failed"
    assert receipt.failure_class == "subprocess_nonzero"


def test_diagnose_scratch_schema_inventory_propagates_subprocess_failure_class() -> None:
    scratch_runtime = _scratch_runtime()
    with patch("subprocess.run", return_value=_Completed(returncode=1)):
        receipt = diagnose_scratch_schema_inventory(
            _RUNTIME,
            scratch_runtime,
            expected_schema_inventory_sha256="f" * 64,
        )
    assert receipt.status == "failed"
    assert receipt.failure_class == "subprocess_nonzero"


def test_diagnose_scratch_schema_inventory_propagates_timeout_failure_class() -> None:
    scratch_runtime = _scratch_runtime()
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pg_dump", timeout=3600),
    ):
        receipt = diagnose_scratch_schema_inventory(
            _RUNTIME,
            scratch_runtime,
            expected_schema_inventory_sha256="f" * 64,
        )
    assert receipt.status == "failed"
    assert receipt.failure_class == "timeout"


def test_diagnose_scratch_schema_inventory_rejects_colliding_scratch_runtime() -> None:
    with pytest.raises(Exception, match="collides with production"):
        diagnose_scratch_schema_inventory(
            _RUNTIME, _RUNTIME, expected_schema_inventory_sha256="f" * 64
        )


def test_diagnose_scratch_data_inventory_propagates_subprocess_failure_class() -> None:
    scratch_runtime = _scratch_runtime()
    with patch("subprocess.run", return_value=_Completed(returncode=1)):
        receipt = diagnose_scratch_data_inventory(
            _RUNTIME,
            scratch_runtime,
            expected_data_inventory_sha256="f" * 64,
        )
    assert receipt.status == "failed"
    assert receipt.failure_class == "subprocess_nonzero"


def test_diagnose_scratch_cleanup_rejects_colliding_scratch_runtime() -> None:
    with pytest.raises(Exception, match="collides with production"):
        diagnose_scratch_cleanup(_RUNTIME, _RUNTIME)


def test_diagnose_scratch_schema_inventory_rejects_mismatch() -> None:
    scratch_runtime = _scratch_runtime()

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        kwargs["stdout"].write(b"CREATE TABLE differs ();")
        return _Completed()

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_scratch_schema_inventory(
            _RUNTIME,
            scratch_runtime,
            expected_schema_inventory_sha256="f" * 64,
        )
    assert receipt.status == "failed"
    assert receipt.failure_class == "inventory_mismatch"


def test_diagnose_scratch_data_inventory_accepts_matching_digest() -> None:
    scratch_runtime = _scratch_runtime()
    payload = b"INSERT INTO example VALUES (1);"

    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        kwargs["stdout"].write(payload)
        return _Completed()

    import hashlib

    expected = hashlib.sha256(payload).hexdigest()
    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_scratch_data_inventory(
            _RUNTIME,
            scratch_runtime,
            expected_data_inventory_sha256=expected,
        )
    assert receipt.status == "succeeded"
    assert receipt.data_inventory_sha256 == expected


def test_diagnose_scratch_cleanup_rejects_lingering_owner() -> None:
    scratch_runtime = _scratch_runtime()
    with (
        patch(
            "kor_travel_docker_manager.services.cache_target_diagnostic_stages._run_checked",
            return_value=b"",
        ),
        patch(
            "kor_travel_docker_manager.services.cache_target_diagnostic_stages."
            "_read_database_owner",
            return_value="krtour_map",
        ),
    ):
        receipt = diagnose_scratch_cleanup(_RUNTIME, scratch_runtime)
    assert receipt.status == "failed"
    assert receipt.failure_class == "cleanup_failed"


def test_diagnose_scratch_cleanup_succeeds_when_absent() -> None:
    scratch_runtime = _scratch_runtime()
    with (
        patch(
            "kor_travel_docker_manager.services.cache_target_diagnostic_stages._run_checked",
            return_value=b"",
        ),
        patch(
            "kor_travel_docker_manager.services.cache_target_diagnostic_stages."
            "_read_database_owner",
            return_value=None,
        ),
    ):
        receipt = diagnose_scratch_cleanup(_RUNTIME, scratch_runtime)
    assert receipt.status == "succeeded"


def test_remove_diagnostic_archive_is_idempotent(tmp_path: Path) -> None:
    archive_path = tmp_path / "map_application.dump"
    archive_path.write_bytes(b"x")
    remove_diagnostic_archive(archive_path)
    assert not archive_path.exists()
    remove_diagnostic_archive(archive_path)
