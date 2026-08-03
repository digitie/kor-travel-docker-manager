from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kor_travel_docker_manager.services.cache_target_backup import DatabaseRuntime
from kor_travel_docker_manager.services.cache_target_diagnostic_stages import (
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


class _Completed:
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = stderr


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
    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        kwargs["stdout"].write(b"CREATE TABLE example ();")
        return _Completed()

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_source_schema_inventory(_RUNTIME)
    assert receipt.status == "succeeded"
    assert receipt.schema_inventory_sha256 is not None


def test_diagnose_source_schema_inventory_rejects_any_stderr() -> None:
    def fake_run(args: list[str], **kwargs: Any) -> _Completed:
        return _Completed(
            stderr=(
                b"pg_dump: warning: there are circular foreign-key constraints "
                b"on this table:\npg_dump: detail: t\n"
                b"pg_dump: hint: You might not be able to restore the dump without using "
                b"--disable-triggers or temporarily dropping the constraints.\n"
                b"pg_dump: hint: Consider using a full dump instead of a --data-only dump "
                b"to avoid this problem."
            )
        )

    with patch("subprocess.run", side_effect=fake_run):
        receipt = diagnose_source_schema_inventory(_RUNTIME)
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
