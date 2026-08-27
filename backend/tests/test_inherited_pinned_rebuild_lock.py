from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import c6c_deployment as c6c_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError


def _open_held_lock(path: Path) -> int:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return descriptor


def test_c6c_deployment_lock_reuses_verified_inherited_pinned_rebuild_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_held_lock(lock_path)
    monkeypatch.setattr(c6c_module, "_C6C_GLOBAL_MUTATION_LOCK", lock_path)
    monkeypatch.setattr(c6c_module, "_validate_c6c_lock_fd", lambda *_args, **_kwargs: None)
    monkeypatch.setenv(
        c6c_module._PINNED_REBUILD_INHERITED_GLOBAL_LOCK_FD_ENV,
        str(descriptor),
    )
    try:
        with c6c_module.c6c_deployment_lock(str(lock_path)):
            contender = os.open(lock_path, os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(contender)
    finally:
        os.close(descriptor)


def test_c6c_deployment_lock_rejects_mismatched_inherited_descriptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    path_descriptor = _open_held_lock(lock_path)
    os.close(path_descriptor)
    descriptor = _open_held_lock(tmp_path / "other.lock")
    monkeypatch.setattr(c6c_module, "_C6C_GLOBAL_MUTATION_LOCK", lock_path)
    monkeypatch.setenv(
        c6c_module._PINNED_REBUILD_INHERITED_GLOBAL_LOCK_FD_ENV,
        str(descriptor),
    )
    try:
        with pytest.raises(DeploymentContractError, match="descriptor is unsafe"):
            with c6c_module.c6c_deployment_lock(str(lock_path)):
                pass
    finally:
        os.close(descriptor)
