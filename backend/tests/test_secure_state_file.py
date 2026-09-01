"""GM-10: root-safe atomic write·디렉터리 fsync 프리미티브."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import secure_state_file
from kor_travel_docker_manager.services.secure_state_file import (
    atomic_write_bytes,
    atomic_write_json,
    fsync_directory,
    insecure_mode_allowed,
)


def test_atomic_write_json_writes_content_and_sets_mode(tmp_path: Path) -> None:
    target = tmp_path / "state.json"

    atomic_write_json(target, {"a": 1}, mode=0o600)

    assert target.is_file()
    assert target.read_text(encoding="utf-8") == '{\n  "a": 1\n}\n'
    import stat

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_atomic_write_json_sets_directory_mode_when_given(tmp_path: Path) -> None:
    import stat

    target_dir = tmp_path / "state-dir"
    target = target_dir / "state.json"

    atomic_write_json(target, {}, mode=0o600, directory_mode=0o700)

    assert stat.S_IMODE(target_dir.stat().st_mode) == 0o700


def test_atomic_write_json_leaves_no_temp_file_behind_on_success(tmp_path: Path) -> None:
    target = tmp_path / "state.json"

    atomic_write_json(target, {"a": 1}, mode=0o600)

    remaining = list(tmp_path.iterdir())
    assert remaining == [target]


def test_atomic_write_bytes_cleans_up_the_temp_file_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """crash를 흉내 낸다 — 임시 파일에 쓰는 도중 실패하면 잔해를 남기면 안 된다."""

    target = tmp_path / "state.bin"

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(secure_state_file.os, "fsync", boom)

    with pytest.raises(OSError, match="disk full"):
        atomic_write_bytes(target, b"payload", mode=0o600)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_atomic_write_json_fsyncs_both_the_file_and_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GM-10의 핵심 회귀: 파일 fsync만으로는 rename의 durability를 보장하지 않는다
    — 디렉터리 자체도 fsync해야 crash에서 살아남는다. execution registry의 옛
    구현은 이 두 번째 단계가 없었다."""

    target = tmp_path / "state.json"
    fsynced_fds: list[int] = []
    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        fsynced_fds.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(secure_state_file.os, "fsync", spy_fsync)

    atomic_write_json(target, {"a": 1}, mode=0o600)

    assert len(fsynced_fds) == 2, "파일 핸들과 디렉터리 fd 둘 다 fsync돼야 한다"


def test_atomic_write_json_can_skip_the_directory_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    fsynced_fds: list[int] = []
    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        fsynced_fds.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(secure_state_file.os, "fsync", spy_fsync)

    atomic_write_json(target, {"a": 1}, mode=0o600, dir_fsync=False)

    assert len(fsynced_fds) == 1, "dir_fsync=False면 파일 핸들만 fsync해야 한다"


def test_fsync_directory_swallows_a_missing_directory(tmp_path: Path) -> None:
    fsync_directory(tmp_path / "does-not-exist")  # 예외를 내지 않아야 한다.


def test_fsync_directory_swallows_an_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        secure_state_file.os, "fsync", Mock(side_effect=OSError("not supported"))
    )

    fsync_directory(tmp_path)  # 예외를 내지 않아야 한다 — 파일 교체 자체는 이미 끝났다.


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1", True),
        (" 1 ", True),
        ("1\n", True),
        ("0", False),
        ("true", False),
        ("", False),
    ],
)
def test_insecure_mode_allowed_parses_exactly_one_as_true(
    raw: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KTDM_TEST_INSECURE_MODE", raw)

    assert insecure_mode_allowed("KTDM_TEST_INSECURE_MODE") is expected


def test_insecure_mode_allowed_is_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KTDM_TEST_INSECURE_MODE", raising=False)

    assert insecure_mode_allowed("KTDM_TEST_INSECURE_MODE") is False
