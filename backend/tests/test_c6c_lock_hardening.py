"""C6c host lease가 자기 자신으로 상호배제를 보증하는지 확인한다.

``flock``은 경로가 아니라 inode 단위다. lease 디렉터리 권한만으로 "아무도 경로를
바꿔치기할 수 없다"고 가정하면, 그 가정이 깨지는 날 두 주체가 서로 다른 inode를 잠근
채 각자 상호배제를 얻었다고 믿는다. 그래서 획득 직후 경로를 다시 대조한다 —
``_verified_inherited_global_mutation_lock_fd``가 이미 하던 대조를 일반 경로에도
적용한 것이다.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import c6c_deployment as c6c_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError


def _open_lock(path: Path) -> int:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    return descriptor


def test_path_re_check_passes_for_an_untouched_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_lock(lock_path)
    try:
        c6c_module._assert_locked_fd_still_owns_path(descriptor, lock_path)
    finally:
        os.close(descriptor)


def test_path_re_check_rejects_a_replaced_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_lock(lock_path)
    try:
        lock_path.unlink()
        replacement = _open_lock(lock_path)
        os.close(replacement)
        with pytest.raises(DeploymentContractError, match="replaced during acquisition"):
            c6c_module._assert_locked_fd_still_owns_path(descriptor, lock_path)
    finally:
        os.close(descriptor)


def test_path_re_check_rejects_a_vanished_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_lock(lock_path)
    try:
        lock_path.unlink()
        with pytest.raises(DeploymentContractError, match="vanished after acquisition"):
            c6c_module._assert_locked_fd_still_owns_path(descriptor, lock_path)
    finally:
        os.close(descriptor)


def test_path_re_check_rejects_a_symlinked_lock_path(tmp_path: Path) -> None:
    """``lstat``이라 경로가 symlink로 바뀐 경우도 inode 불일치로 걸린다."""

    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_lock(lock_path)
    try:
        other = tmp_path / "other.lock"
        os.close(_open_lock(other))
        lock_path.unlink()
        lock_path.symlink_to(other)
        with pytest.raises(DeploymentContractError, match="replaced during acquisition"):
            c6c_module._assert_locked_fd_still_owns_path(descriptor, lock_path)
    finally:
        os.close(descriptor)


def test_deployment_lock_still_acquires_normally(tmp_path: Path) -> None:
    """재대조를 넣어도 정상 획득 경로는 그대로다."""

    lock_path = tmp_path / "deployment.lock"
    with c6c_module.c6c_deployment_lock(str(lock_path)):
        contender = os.open(lock_path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(contender)


def test_deployment_lock_fails_closed_when_the_path_is_swapped_mid_acquisition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """open과 flock 사이에 경로가 바꿔치기되면 획득이 아니라 거부여야 한다.

    재대조가 없으면 이 케이스는 조용히 성공하고, 호출자는 실제로는 아무도 배제하지
    못하는 lease를 들고 mutation을 진행한다.
    """

    lock_path = tmp_path / "deployment.lock"
    real_flock = fcntl.flock

    def swapping_flock(fd: int, operation: int) -> None:
        result = real_flock(fd, operation)
        if operation & fcntl.LOCK_EX:
            # 경합자가 경로를 새 inode로 갈아끼운 상황을 재현한다.
            lock_path.unlink()
            os.close(_open_lock(lock_path))
        return result

    monkeypatch.setattr(fcntl, "flock", swapping_flock)

    with pytest.raises(DeploymentContractError, match="replaced during acquisition"):
        with c6c_module.c6c_deployment_lock(str(lock_path)):
            pytest.fail("바꿔치기된 lock으로 임계 구역에 들어가면 안 된다")
