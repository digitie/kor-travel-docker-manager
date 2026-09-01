"""GM-08: 백업·pin registry 보존본의 off-box 동기화."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import kor_travel_docker_manager.services.offbox_backup_sync as offbox_backup_sync
from kor_travel_docker_manager.services.offbox_backup_sync import (
    OffboxSyncError,
    OffboxSyncNotConfiguredError,
    offbox_sync_is_configured,
    read_offbox_sync_status,
    sync_backups_offbox,
)
from kor_travel_docker_manager.services.runtime_pin_registry import (
    RUNTIME_PINS_FILE_ENV,
    RUNTIME_PINS_PUBLIC_FILE_ENV,
)


@pytest.fixture
def offbox_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(offbox_backup_sync.OFFBOX_HOST_ENV, "backup-vault.internal")
    monkeypatch.setenv(offbox_backup_sync.OFFBOX_USER_ENV, "ktdm-sync")
    monkeypatch.setenv(offbox_backup_sync.OFFBOX_REMOTE_ROOT_ENV, "/srv/ktdm-offbox")


def test_offbox_sync_is_configured_reflects_the_host_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(offbox_backup_sync.OFFBOX_HOST_ENV, raising=False)
    assert offbox_sync_is_configured() is False

    monkeypatch.setenv(offbox_backup_sync.OFFBOX_HOST_ENV, "backup-vault.internal")
    monkeypatch.setenv(offbox_backup_sync.OFFBOX_USER_ENV, "ktdm-sync")
    monkeypatch.setenv(offbox_backup_sync.OFFBOX_REMOTE_ROOT_ENV, "/srv/ktdm-offbox")
    assert offbox_sync_is_configured() is True


def test_offbox_destination_requires_user_and_remote_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(offbox_backup_sync.OFFBOX_HOST_ENV, "backup-vault.internal")
    monkeypatch.delenv(offbox_backup_sync.OFFBOX_USER_ENV, raising=False)
    monkeypatch.delenv(offbox_backup_sync.OFFBOX_REMOTE_ROOT_ENV, raising=False)

    with pytest.raises(OffboxSyncError, match=offbox_backup_sync.OFFBOX_USER_ENV):
        offbox_backup_sync._offbox_destination()

    monkeypatch.setenv(offbox_backup_sync.OFFBOX_USER_ENV, "ktdm-sync")
    with pytest.raises(OffboxSyncError, match=offbox_backup_sync.OFFBOX_REMOTE_ROOT_ENV):
        offbox_backup_sync._offbox_destination()


def test_sync_backups_offbox_raises_when_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(offbox_backup_sync.OFFBOX_HOST_ENV, raising=False)

    with pytest.raises(OffboxSyncNotConfiguredError):
        sync_backups_offbox(roles=("geo",), backup_root=tmp_path, include_pin_registry=False)


def _seed_backup_dir(root: Path, role: str) -> Path:
    role_dir = root / role
    role_dir.mkdir(parents=True)
    (role_dir / f"{role}-1000.dump").write_bytes(b"dump-bytes")
    (role_dir / f"{role}-1000.dump.sha256").write_text(
        "deadbeef" * 8 + f"  {role}-1000.dump\n", encoding="ascii"
    )
    (role_dir / f"{role}-1000.manifest").write_text("{}", encoding="utf-8")
    return role_dir


def _seed_pin_registry_dir(base: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    registry_dir = base / "pin-registry"
    registry_dir.mkdir(parents=True)
    (registry_dir / "runtime-pins.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv(RUNTIME_PINS_FILE_ENV, str(registry_dir / "runtime-pins.json"))

    public_dir = base / "pin-registry-public"
    public_dir.mkdir(parents=True)
    (public_dir / "runtime-pins.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv(RUNTIME_PINS_PUBLIC_FILE_ENV, str(public_dir / "runtime-pins.json"))
    return registry_dir


def test_sync_backups_offbox_happy_path(
    offbox_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backup_root = tmp_path / "backups"
    _seed_backup_dir(backup_root, "geo")
    _seed_pin_registry_dir(tmp_path, monkeypatch)

    def fake_run(argv, *, timeout, input=None, capture_output=True, check=False):  # noqa: A002
        if argv[0] == "rsync":
            return Mock(returncode=0, stdout=b"", stderr=b"")
        if argv[0] == "ssh":
            return Mock(returncode=0, stdout=b"OK\n", stderr=b"")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(offbox_backup_sync.subprocess, "run", fake_run)

    outcome = sync_backups_offbox(roles=("geo",), backup_root=backup_root)

    assert outcome.destination_host == "backup-vault.internal"
    assert outcome.all_verified is True
    labels = {t.label for t in outcome.targets}
    assert labels == {"geo", "pin_registry", "pin_registry_public"}
    for target in outcome.targets:
        assert target.synced is True
        assert target.verified is True

    status_path = backup_root / ".offbox-sync-status.json"
    assert status_path.is_file()
    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["all_verified"] is True


def test_sync_backups_offbox_reports_a_partial_failure_without_crashing(
    offbox_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backup_root = tmp_path / "backups"
    _seed_backup_dir(backup_root, "geo")
    _seed_backup_dir(backup_root, "pinvi")
    monkeypatch.setattr(
        offbox_backup_sync, "runtime_pin_registry_path", lambda: tmp_path / "absent" / "x.json"
    )
    monkeypatch.setattr(
        offbox_backup_sync,
        "runtime_pin_registry_public_path",
        lambda: tmp_path / "absent-public" / "x.json",
    )

    def fake_run(argv, *, timeout, input=None, capture_output=True, check=False):  # noqa: A002
        if argv[0] == "rsync":
            if "pinvi" in argv[-2]:
                return Mock(returncode=1, stdout=b"", stderr=b"connection refused")
            return Mock(returncode=0, stdout=b"", stderr=b"")
        if argv[0] == "ssh":
            return Mock(returncode=0, stdout=b"OK\n", stderr=b"")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(offbox_backup_sync.subprocess, "run", fake_run)

    outcome = sync_backups_offbox(roles=("geo", "pinvi"), backup_root=backup_root)

    assert outcome.all_verified is False
    by_label = {t.label: t for t in outcome.targets}
    assert by_label["geo"].verified is True
    assert by_label["pinvi"].synced is False
    assert "connection refused" in by_label["pinvi"].detail
    # pin registry 디렉터리 자체가 없으면(부재 host) 건너뛰되 죽지 않는다.
    assert by_label["pin_registry"].synced is False


def test_backup_directory_checksum_manifest_reuses_the_existing_sidecar(
    tmp_path: Path,
) -> None:
    role_dir = _seed_backup_dir(tmp_path, "geo")

    manifest = offbox_backup_sync._backup_directory_checksum_manifest(role_dir)

    assert "geo-1000.dump" in manifest
    assert "deadbeef" in manifest
    # .sha256 파일 자체는 검증 대상 목록에 다시 나타나지 않는다.
    assert manifest.count("geo-1000.dump.sha256") == 0


def test_read_offbox_sync_status_returns_none_when_never_written(tmp_path: Path) -> None:
    assert read_offbox_sync_status(backup_root=tmp_path) is None
