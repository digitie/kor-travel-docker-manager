from __future__ import annotations

import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import FunctionType
from typing import Literal

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import PinnedRuntimeStatePaths
from kor_travel_docker_manager.services.pinned_runtime_release import PINNED_RUNTIME_RELEASE
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    materialize_pinned_runtime_sources,
    pinned_runtime_source_paths,
)

GitRunner = Callable[..., subprocess.CompletedProcess[str]]
GitInvocation = tuple[list[str], dict[str, object]]

_TEST_PROJECT_NAME = "f1d-source-test"


def _state_paths(tmp_path: Path) -> PinnedRuntimeStatePaths:
    state_root = tmp_path / "state-root" / _TEST_PROJECT_NAME
    return PinnedRuntimeStatePaths(
        state_root=state_root,
        manifest=state_root / "pinned-runtime-generation-v5.json",
        journal=state_root / "pinned-runtime-rebuild-v5.json",
        tombstone_receipt=state_root / "pinned-runtime-v5" / "legacy-tombstone-v5.json",
    )


def _values(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    map_source = tmp_path / "map-source"
    pinvi_source = tmp_path / "pinvi-source"
    map_source.mkdir(mode=0o700)
    pinvi_source.mkdir(mode=0o700)
    return (
        {
            "KOR_TRAVEL_MAP_REPO_DIR": str(map_source),
            "PINVI_REPO_DIR": str(pinvi_source),
            "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
            "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
            "PINVI_ENVIRONMENT": "production",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
            "COMPOSE_PROJECT_NAME": _TEST_PROJECT_NAME,
            "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state-root"),
        },
        map_source,
        pinvi_source,
    )


def _runner_for_materialization(
    *,
    paths: PinnedRuntimeStatePaths,
    calls: list[GitInvocation],
    fail_status_once: bool = False,
    staging_mode: int = 0o700,
) -> GitRunner:
    release = PINNED_RUNTIME_RELEASE
    runtime_paths = pinned_runtime_source_paths(state_paths=paths, release=release)

    status_failure_pending = fail_status_once

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal status_failure_pending
        command = args[0]
        assert isinstance(command, list)
        assert all(isinstance(item, str) for item in command)
        calls.append((command, dict(kwargs)))
        if "config" in command:
            root = command[command.index("-C") + 1]
            role: Literal["map", "pinvi"] = "map" if root.endswith("map-source") else "pinvi"
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{release.source_for(role).canonical_url}\n",
                stderr="",
            )
        if "init" in command and "--bare" in command:
            Path(command[-1]).mkdir(mode=0o700)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "worktree" in command and "add" in command:
            target = Path(command[command.index("--detach") + 1])
            target.mkdir(mode=0o700)
            os.chmod(target, staging_mode)
            (target / ".git").write_text("gitdir: managed\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "worktree" in command and "move" in command:
            staging = Path(command[-2])
            target = Path(command[-1])
            os.chmod(staging, 0o700)
            staging.rename(target)
            os.chmod(target, 0o555)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "worktree" in command and "remove" in command:
            target = Path(command[-1])
            os.chmod(target, 0o700)
            shutil.rmtree(target)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "status" in command and status_failure_pending:
            status_failure_pending = False
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="failure")
        if "rev-parse" in command:
            expression = command[-1]
            if expression == "--verify":
                expression = command[-1]
            materialized_role: Literal["map", "pinvi"] = (
                "map" if "map" in " ".join(command) else "pinvi"
            )
            if expression == "HEAD" or command[-1] == "HEAD":
                output = release.source_for(materialized_role).revision
            elif "tree" in expression:
                output = ("c" if materialized_role == "map" else "d") * 40
            else:
                output = ("c" if materialized_role == "map" else "d") * 40
            return subprocess.CompletedProcess(command, 0, stdout=f"{output}\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    assert runtime_paths.pinset_directory.name == release.pinset_sha256
    return runner


def test_materializes_exact_v5_release_without_changing_canonical_sources(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    values, map_source, pinvi_source = _values(tmp_path)
    calls: list[GitInvocation] = []

    result = materialize_pinned_runtime_sources(
        release=PINNED_RUNTIME_RELEASE,
        state_paths=paths,
        values=values,
        runner=_runner_for_materialization(paths=paths, calls=calls),
    )

    assert result.pinset_sha256 == PINNED_RUNTIME_RELEASE.pinset_sha256
    assert result.source_for("map").revision == PINNED_RUNTIME_RELEASE.source_for("map").revision
    assert result.source_for("pinvi").revision == PINNED_RUNTIME_RELEASE.source_for("pinvi").revision
    assert result.source_roots == {
        "map": result.source_for("map").root,
        "pinvi": result.source_for("pinvi").root,
    }
    assert map_source.stat().st_mode & 0o777 == 0o700
    assert pinvi_source.stat().st_mode & 0o777 == 0o700
    fetches = [command for command, _kwargs in calls if "fetch" in command]
    assert len(fetches) == 2
    for source, command in zip(PINNED_RUNTIME_RELEASE.sources, fetches, strict=True):
        assert command[-2:] == [source.canonical_url, source.revision]
        assert command[:9] == [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "credential.helper=",
        ]

    source_origin_calls = [
        (command, kwargs) for command, kwargs in calls if "config" in command
    ]
    assert len(source_origin_calls) == 2
    for source, (command, kwargs) in zip(
        PINNED_RUNTIME_RELEASE.sources,
        source_origin_calls,
        strict=True,
    ):
        source_root = {"map": map_source, "pinvi": pinvi_source}[source.role]
        assert command == [
            "/usr/bin/git",
            "-C",
            str(source_root),
            "config",
            "--local",
            "--get",
            "remote.origin.url",
        ]
        assert kwargs["cwd"] == "/"
        assert kwargs["env"] == {
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "file",
        }
        preexec_fn = kwargs.get("preexec_fn")
        assert isinstance(preexec_fn, FunctionType)
        closure = preexec_fn.__closure__
        assert closure is not None
        captured = {cell.cell_contents for cell in closure}
        assert source_root.stat().st_uid in captured
        assert source_root.stat().st_gid in captured

    root_git_calls = [
        (command, kwargs) for command, kwargs in calls if "config" not in command
    ]
    assert root_git_calls
    for command, kwargs in root_git_calls:
        assert kwargs["cwd"] == "/"
        assert "preexec_fn" not in kwargs
        assert kwargs["env"] == {
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "https",
        }
        assert command[:9] == [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "credential.helper=",
        ]
        assert str(map_source) not in command
        assert str(pinvi_source) not in command


def test_rejects_noncanonical_source_origin_before_root_git_staging(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    values, _map_source, _pinvi_source = _values(tmp_path)
    calls: list[GitInvocation] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        calls.append((command, dict(kwargs)))
        if "config" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="https://github.com/example/not-map.git\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(DeploymentContractError, match="canonical HTTPS URL"):
        materialize_pinned_runtime_sources(
            release=PINNED_RUNTIME_RELEASE,
            state_paths=paths,
            values=values,
            runner=runner,
        )

    assert all("fetch" not in command for command, _kwargs in calls)


def test_existing_materialization_rejects_revision_drift(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    values, _map_source, _pinvi_source = _values(tmp_path)
    source_paths = pinned_runtime_source_paths(state_paths=paths, release=PINNED_RUNTIME_RELEASE)
    paths.state_root.mkdir(mode=0o700, parents=True)
    source_paths.state_directory.mkdir(mode=0o700)
    source_paths.pinset_directory.mkdir(mode=0o700)
    source_paths.bare_directory.mkdir(mode=0o700)
    source_paths.worktrees_directory.mkdir(mode=0o700)
    target = source_paths.worktree(PINNED_RUNTIME_RELEASE.source_for("map"))
    target.parent.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    (target / ".git").write_text("gitdir: managed\n", encoding="utf-8")
    os.chmod(target / ".git", 0o444)
    os.chmod(target, 0o555)
    calls: list[GitInvocation] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        calls.append((command, dict(kwargs)))
        if "config" in command:
            root = command[command.index("-C") + 1]
            role: Literal["map", "pinvi"] = "map" if root.endswith("map-source") else "pinvi"
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{PINNED_RUNTIME_RELEASE.source_for(role).canonical_url}\n",
                stderr="",
            )
        if "init" in command and "--bare" in command:
            Path(command[-1]).mkdir(mode=0o700)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "rev-parse" in command and command[-1] == "HEAD":
            return subprocess.CompletedProcess(command, 0, stdout=f"{'e' * 40}\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(DeploymentContractError, match="revision drifted"):
        materialize_pinned_runtime_sources(
            release=PINNED_RUNTIME_RELEASE,
            state_paths=paths,
            values=values,
            runner=runner,
        )

    assert all("fetch" not in command for command, _kwargs in calls)


@pytest.mark.parametrize("unsafe", ["mode", "symlink"])
def test_rejects_unsafe_canonical_source_root(tmp_path: Path, unsafe: str) -> None:
    paths = _state_paths(tmp_path)
    values, map_source, _pinvi_source = _values(tmp_path)
    if unsafe == "mode":
        os.chmod(map_source, 0o775)
    else:
        replacement = tmp_path / "map-target"
        replacement.mkdir(mode=0o700)
        map_source.rmdir()
        map_source.symlink_to(replacement, target_is_directory=True)

    with pytest.raises(DeploymentContractError, match="unsafe|symbolic link"):
        materialize_pinned_runtime_sources(
            release=PINNED_RUNTIME_RELEASE,
            state_paths=paths,
            values=values,
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
        )


def test_existing_materialization_is_idempotently_validated_without_refetch(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    values, _map_source, _pinvi_source = _values(tmp_path)
    first_calls: list[GitInvocation] = []

    first = materialize_pinned_runtime_sources(
        release=PINNED_RUNTIME_RELEASE,
        state_paths=paths,
        values=values,
        runner=_runner_for_materialization(paths=paths, calls=first_calls),
    )
    second_calls: list[GitInvocation] = []
    second = materialize_pinned_runtime_sources(
        release=PINNED_RUNTIME_RELEASE,
        state_paths=paths,
        values=values,
        runner=_runner_for_materialization(paths=paths, calls=second_calls),
    )

    assert second == first
    assert all("fetch" not in command for command, _kwargs in second_calls)
    assert stat.S_IMODE(first.source_for("map").root.stat().st_mode) == 0o555


def test_materialization_seals_git_default_staging_mode_before_inspection(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    values, _map_source, _pinvi_source = _values(tmp_path)
    calls: list[GitInvocation] = []

    result = materialize_pinned_runtime_sources(
        release=PINNED_RUNTIME_RELEASE,
        state_paths=paths,
        values=values,
        runner=_runner_for_materialization(
            paths=paths,
            calls=calls,
            staging_mode=0o755,
        ),
    )

    assert stat.S_IMODE(result.source_for("map").root.stat().st_mode) == 0o555
    assert stat.S_IMODE(result.source_for("pinvi").root.stat().st_mode) == 0o555


def test_later_staging_failure_is_cleaned_and_same_pinset_retry_succeeds(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    values, _map_source, _pinvi_source = _values(tmp_path)
    calls: list[GitInvocation] = []
    runner = _runner_for_materialization(
        paths=paths,
        calls=calls,
        fail_status_once=True,
    )
    source_paths = pinned_runtime_source_paths(state_paths=paths, release=PINNED_RUNTIME_RELEASE)
    map_source = PINNED_RUNTIME_RELEASE.source_for("map")
    map_target = source_paths.worktree(map_source)

    with pytest.raises(DeploymentContractError, match="staging Git operation failed"):
        materialize_pinned_runtime_sources(
            release=PINNED_RUNTIME_RELEASE,
            state_paths=paths,
            values=values,
            runner=runner,
        )

    assert not map_target.exists()
    assert any(
        "worktree" in command and "remove" in command for command, _kwargs in calls
    )

    result = materialize_pinned_runtime_sources(
        release=PINNED_RUNTIME_RELEASE,
        state_paths=paths,
        values=values,
        runner=runner,
    )

    assert result.source_for("map").root == map_target
    assert result.source_for("pinvi").root == source_paths.worktree(
        PINNED_RUNTIME_RELEASE.source_for("pinvi")
    )
    assert stat.S_IMODE(map_target.stat().st_mode) == 0o555


def test_rejects_forged_state_root_before_any_git_invocation(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    values, _map_source, _pinvi_source = _values(tmp_path)
    forged = replace(paths, state_root=tmp_path / "forged-state")
    calls: list[GitInvocation] = []

    with pytest.raises(DeploymentContractError, match="differ from canonical rebuildable state"):
        materialize_pinned_runtime_sources(
            release=PINNED_RUNTIME_RELEASE,
            state_paths=forged,
            values=values,
            runner=_runner_for_materialization(paths=paths, calls=calls),
        )

    assert calls == []


def test_rejects_nonrebuildable_lifecycle_before_any_git_invocation(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    values, _map_source, _pinvi_source = _values(tmp_path)
    values.update(
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
            "KTDM_DEPLOYMENT_LIFECYCLE": "operational",
        }
    )
    calls: list[GitInvocation] = []

    with pytest.raises(DeploymentContractError, match="requires rehearsal/rebuildable"):
        materialize_pinned_runtime_sources(
            release=PINNED_RUNTIME_RELEASE,
            state_paths=paths,
            values=values,
            runner=_runner_for_materialization(paths=paths, calls=calls),
        )

    assert calls == []
