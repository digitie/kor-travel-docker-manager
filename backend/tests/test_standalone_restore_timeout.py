"""복원 실패와 현재 실행 scratch 정리의 독립 회귀."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_standalone_backup import REHEARSAL_COMMANDS, _rehearsal_probes, _seed_backup

import kor_travel_docker_manager.services.standalone_backup as backup


@pytest.fixture(autouse=True)
def _isolated_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "disk_usage", Mock(return_value=Mock(free=1 << 50)))
    monkeypatch.setattr(backup, "require_local_targets", lambda *_args: None)


def _input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch)
    return root


def _cleanup_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rm_timeout: bool = False,
    drop_code: int = 0,
    readback: bytes = b"0|0\n",
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> Mock:
        calls.append(arguments)
        assert kwargs["timeout"] <= 60
        if "rm" in arguments and rm_timeout:
            raise subprocess.TimeoutExpired(arguments, 30)
        return Mock(
            returncode=drop_code if "dropdb" in arguments else 0,
            stdout=readback if "psql" in arguments else b"",
            stderr=b"",
        )

    monkeypatch.setattr(backup.subprocess, "run", run)
    return calls


def test_timeout_preserves_primary_cause_and_reports_cleanup(tmp_path, monkeypatch) -> None:
    root = _input(tmp_path, monkeypatch)
    cause = subprocess.TimeoutExpired(["docker", "exec"], 2)

    def restore(*args, **kwargs):
        raise backup.StandaloneBackupError("original restore timeout") from cause

    monkeypatch.setattr(backup, "_run_pg_restore", restore)
    calls = _cleanup_probe(monkeypatch, rm_timeout=True)
    with pytest.raises(backup.StandaloneBackupError, match="original restore timeout") as error:
        backup.rehearse_standalone_restore("geo", backup_root=root, timeout=2)
    assert "cleanup" in str(error.value).lower()
    assert "dump" in str(error.value).lower()
    assert any("--force" in call and "dropdb" in call for call in calls)
    assert any("psql" in call for call in calls)
    assert error.value.__cause__.__cause__ is cause


def test_dump_cleanup_timeout_cannot_skip_owned_database_cleanup(tmp_path, monkeypatch) -> None:
    root = _input(tmp_path, monkeypatch)
    calls = _cleanup_probe(monkeypatch, rm_timeout=True)
    outcome = backup.rehearse_standalone_restore("geo", backup_root=root)
    drop = next(call for call in calls if "dropdb" in call)
    assert "--force" in drop
    assert drop[-1] == outcome.scratch_database
    assert "--maintenance-db" in drop and drop[drop.index("--maintenance-db") + 1] == "postgres"
    read = next(call for call in calls if "psql" in call)
    assert outcome.scratch_database in read[-1]
    assert "pg_stat_activity" in read[-1] and "pg_database" in read[-1]
    assert outcome.restore_succeeded is True
    assert outcome.verified is False


@pytest.mark.parametrize("readback", [b"1|1\n", b"", b"unparseable\n"])
def test_cleanup_readback_must_prove_no_database_or_backend(
    tmp_path, monkeypatch, readback
) -> None:
    root = _input(tmp_path, monkeypatch)
    _cleanup_probe(monkeypatch, readback=readback)
    outcome = backup.rehearse_standalone_restore("geo", backup_root=root)
    assert outcome.restore_succeeded is True
    assert not outcome.verified
    assert "REHEARSAL_CLEANUP_INCOMPLETE" in [f.code for f in outcome.findings]


def test_create_failure_never_force_drops_an_unowned_database(tmp_path, monkeypatch) -> None:
    root = _input(tmp_path, monkeypatch)
    checked = backup._run_checked

    def fail_create(arguments, **kwargs):
        if "createdb" in arguments:
            raise backup.StandaloneBackupError("database already exists")
        return checked(arguments, **kwargs)

    monkeypatch.setattr(backup, "_run_checked", fail_create)
    calls = _cleanup_probe(monkeypatch)
    with pytest.raises(backup.StandaloneBackupError, match="database already exists"):
        backup.rehearse_standalone_restore("geo", backup_root=root)
    assert not any("dropdb" in call for call in calls)
    assert any("rm" in call for call in calls)


def test_restore_has_container_deadline_and_session_only_parallel_limit(
    tmp_path, monkeypatch
) -> None:
    root = _input(tmp_path, monkeypatch)
    _cleanup_probe(monkeypatch)
    outcome = backup.rehearse_standalone_restore("geo", backup_root=root, timeout=2)
    command = next(c for c in REHEARSAL_COMMANDS if "pg_restore" in c)
    assert "timeout" in command
    assert command[command.index("timeout") + 1 : command.index("timeout") + 6] == [
        "-s",
        "TERM",
        "-k",
        "5s",
        "2s",
    ]
    assert "PGOPTIONS=-c max_parallel_maintenance_workers=0" in command
    assert f"PGAPPNAME={outcome.scratch_database}" in command
    assert command[command.index("--dbname") + 1] == outcome.scratch_database
    assert "--exit-on-error" in command
    assert "--no-owner" not in command and "--no-privileges" not in command


def test_restore_local_timeout_records_duration_and_original_timeout_cause(monkeypatch) -> None:
    cause = subprocess.TimeoutExpired(["docker", "exec"], 2)
    monkeypatch.setattr(backup.subprocess, "run", Mock(side_effect=cause))
    with pytest.raises(backup.StandaloneBackupError, match="timed out after 2") as error:
        backup._run_pg_restore(["docker", "exec"], label="fixture restore", timeout=2)
    assert error.value.__cause__ is cause


@pytest.mark.parametrize("timeout", [0, -1, True, "2"])
def test_invalid_timeout_cannot_disable_the_container_deadline(monkeypatch, timeout) -> None:
    plan = Mock(side_effect=AssertionError("invalid timeout must fail before DB access"))
    monkeypatch.setattr(backup, "plan_standalone_restore", plan)
    with pytest.raises(backup.StandaloneBackupError, match="positive integer"):
        backup.rehearse_standalone_restore("geo", timeout=timeout)
    plan.assert_not_called()


@pytest.mark.parametrize(
    "database", ["kor_travel_geo", "ktdm_rehearsal_1_bad", "ktdm_rehearsal_1_abcd1234;"]
)
def test_cleanup_rejects_an_unscoped_name_before_running_any_command(monkeypatch, database) -> None:
    run = Mock(side_effect=AssertionError("invalid scope"))
    monkeypatch.setattr(backup.subprocess, "run", run)
    with pytest.raises(backup.StandaloneBackupError, match="cleanup scope"):
        backup._cleanup_current_rehearsal(
            "fixture",
            12345,
            "fixture_admin",
            database,
            "/tmp/unrelated",
            scratch_created=True,
        )
    run.assert_not_called()
