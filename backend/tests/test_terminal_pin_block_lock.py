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


def test_external_runtime_pin_mutation_is_refused_while_global_mutation_is_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_held_lock(lock_path)
    monkeypatch.setattr(cli_module, "_GLOBAL_MUTATION_LOCK_PATH", lock_path)
    try:
        with pytest.raises(DeploymentContractError, match="mutation is active"):
            with cli_module._runtime_pin_mutation_lock():
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


def test_cli_pin_rotate_pair_does_not_write_during_an_active_global_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_held_lock(lock_path)
    monkeypatch.setattr(cli_module, "_GLOBAL_MUTATION_LOCK_PATH", lock_path)
    try:
        with (
            patch.object(cli_module, "_running_as_root", return_value=True),
            patch.object(cli_module, "rotate_runtime_pin_pair") as rotate_pair,
        ):
            assert (
                cli_module.main(
                    [
                        "pin",
                        "rotate-pair",
                        "--map-revision",
                        "a" * 40,
                        "--pinvi-revision",
                        "b" * 40,
                        "--reason",
                        "candidate",
                        "--confirm",
                    ]
                )
                == 2
            )
        rotate_pair.assert_not_called()
    finally:
        os.close(descriptor)


def test_cli_pin_init_does_not_read_or_write_during_an_active_global_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_held_lock(lock_path)
    monkeypatch.setattr(cli_module, "_GLOBAL_MUTATION_LOCK_PATH", lock_path)
    try:
        with patch.object(cli_module, "load_runtime_pin_registry") as load_registry:
            assert cli_module.main(["pin", "init", "--confirm"]) == 2
        load_registry.assert_not_called()
    finally:
        os.close(descriptor)


def test_cli_pin_apply_pending_does_not_read_during_an_active_global_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_held_lock(lock_path)
    monkeypatch.setattr(cli_module, "_GLOBAL_MUTATION_LOCK_PATH", lock_path)
    try:
        with (
            patch.object(cli_module, "_running_as_root", return_value=True),
            patch.object(cli_module, "read_runtime_pin_request") as read_request,
        ):
            assert (
                cli_module.main(
                    [
                        "pin",
                        "apply-pending",
                        "--expect-revision",
                        "a" * 40,
                        "--confirm",
                    ]
                )
                == 2
            )
        read_request.assert_not_called()
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
        with cli_module._runtime_pin_mutation_lock(allow_inherited_terminal_block=True):
            contender = os.open(lock_path, os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(contender)
    finally:
        os.close(descriptor)
