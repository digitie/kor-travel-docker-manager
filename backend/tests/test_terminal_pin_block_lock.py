from __future__ import annotations

import fcntl
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from kor_travel_docker_manager import cli as cli_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError


def _open_held_lock(path: Path) -> int:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return descriptor


def test_external_terminal_pin_block_is_refused_while_global_mutation_is_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_held_lock(lock_path)
    monkeypatch.setattr(cli_module, "_GLOBAL_MUTATION_LOCK_PATH", lock_path)
    try:
        with pytest.raises(DeploymentContractError, match="mutation is active"):
            with cli_module._terminal_pin_block_mutation_lock():
                pass
    finally:
        os.close(descriptor)


def test_cli_pin_block_does_not_write_during_an_active_global_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_held_lock(lock_path)
    monkeypatch.setattr(cli_module, "_GLOBAL_MUTATION_LOCK_PATH", lock_path)
    try:
        with (
            patch.object(cli_module, "_running_as_root", return_value=True),
            patch.object(cli_module, "block_runtime_pinset") as block,
        ):
            assert (
                cli_module.main(
                    ["pin", "block", "a" * 64, "--reason", "terminal", "--confirm"]
                )
                == 2
            )
        block.assert_not_called()
    finally:
        os.close(descriptor)


def test_launcher_can_record_terminal_block_with_its_inherited_global_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_held_lock(lock_path)
    monkeypatch.setattr(cli_module, "_GLOBAL_MUTATION_LOCK_PATH", lock_path)
    monkeypatch.setenv(cli_module._INHERITED_GLOBAL_MUTATION_LOCK_FD_ENV, str(descriptor))
    try:
        with cli_module._terminal_pin_block_mutation_lock():
            contender = os.open(lock_path, os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(contender)
    finally:
        os.close(descriptor)
