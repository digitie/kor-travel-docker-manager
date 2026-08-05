from __future__ import annotations

import hashlib
import os
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import kor_travel_docker_manager.services.compose_service as compose_service_module
import kor_travel_docker_manager.services.pinned_source_install as source_install
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.compose_service import ComposeService
from kor_travel_docker_manager.services.pinned_source_install import (
    PinnedSourceInstallPaths,
    RepoSpec,
    SourceIdentity,
    assert_pinned_source_installation_allows_pair_mutation,
    canonical_source_identity,
    parse_pinned_source_selection,
    pinned_source_install_paths,
)

_MAP = RepoSpec(
    label="map",
    source_key="KOR_TRAVEL_MAP_REPO_DIR",
    revision_key="KOR_TRAVEL_MAP_GIT_COMMIT",
    canonical_url="https://github.com/digitie/kor-travel-map.git",
    revision="a" * 40,
)
_PINVI = RepoSpec(
    label="pinvi",
    source_key="PINVI_REPO_DIR",
    revision_key="PINVI_SOURCE_REVISION",
    canonical_url="https://github.com/digitie/pinvi.git",
    revision="b" * 40,
)
_SPECS = (_MAP, _PINVI)


def _raw_env(map_root: Path, pinvi_root: Path, *, scalars: bool = True) -> bytes:
    lines = [
        "UNRELATED=value\n",
        f"KOR_TRAVEL_MAP_REPO_DIR={map_root}\n",
        f"PINVI_REPO_DIR={pinvi_root}\n",
    ]
    if scalars:
        lines.extend(
            [
                f"KOR_TRAVEL_MAP_GIT_COMMIT={_MAP.revision}\n",
                f"PINVI_SOURCE_REVISION={_PINVI.revision}\n",
            ]
        )
    return "".join(lines).encode()


def test_parse_pinned_source_selection_replaces_exact_keyset_and_preserves_other_bytes(
    tmp_path: Path,
) -> None:
    map_root = tmp_path / "map-source"
    pinvi_root = tmp_path / "pinvi-source"
    map_root.mkdir(mode=0o700)
    pinvi_root.mkdir(mode=0o700)
    targets = {
        "map": tmp_path / "state" / "map" / _MAP.revision,
        "pinvi": tmp_path / "state" / "pinvi" / _PINVI.revision,
    }

    selection = parse_pinned_source_selection(
        _raw_env(map_root, pinvi_root),
        specs=_SPECS,
        target_roots=targets,
    )

    assert selection.source_roots == {"map": map_root, "pinvi": pinvi_root}
    assert selection.rendered_env == (
        "UNRELATED=value\n"
        f"KOR_TRAVEL_MAP_REPO_DIR={targets['map']}\n"
        f"PINVI_REPO_DIR={targets['pinvi']}\n"
        f"KOR_TRAVEL_MAP_GIT_COMMIT={_MAP.revision}\n"
        f"PINVI_SOURCE_REVISION={_PINVI.revision}\n"
    ).encode()


def test_parse_pinned_source_selection_appends_absent_revision_scalars(
    tmp_path: Path,
) -> None:
    map_root = tmp_path / "map-source"
    pinvi_root = tmp_path / "pinvi-source"
    map_root.mkdir(mode=0o700)
    pinvi_root.mkdir(mode=0o700)
    targets = {"map": tmp_path / "new-map", "pinvi": tmp_path / "new-pinvi"}

    selection = parse_pinned_source_selection(
        _raw_env(map_root, pinvi_root, scalars=False),
        specs=_SPECS,
        target_roots=targets,
    )

    assert selection.rendered_env.endswith(
        (
            f"KOR_TRAVEL_MAP_GIT_COMMIT={_MAP.revision}\n"
            f"PINVI_SOURCE_REVISION={_PINVI.revision}\n"
        ).encode()
    )


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("export KOR_TRAVEL_MAP_REPO_DIR=/tmp/map", "must not use export"),
        ("KOR_TRAVEL_MAP_REPO_DIR=${MAP_ROOT}", "blank or interpolated"),
        ("KOR_TRAVEL_MAP_REPO_DIR=relative/map", "canonical absolute"),
        ("KOR_TRAVEL_MAP_GIT_COMMIT=old", "differs from release pin"),
        ("KOR_TRAVEL_MAP_REPO_DIR=", "blank or interpolated"),
    ],
)
def test_parse_pinned_source_selection_rejects_ambiguous_or_unpinned_values(
    tmp_path: Path,
    line: str,
    message: str,
) -> None:
    map_root = tmp_path / "map-source"
    pinvi_root = tmp_path / "pinvi-source"
    map_root.mkdir(mode=0o700)
    pinvi_root.mkdir(mode=0o700)
    raw = _raw_env(map_root, pinvi_root).decode()
    key = line.partition("=")[0].removeprefix("export ")
    raw = "\n".join(
        line if current.startswith(f"{key}=") else current
        for current in raw.splitlines()
    ).encode()

    with pytest.raises(DeploymentContractError, match=message):
        parse_pinned_source_selection(
            raw,
            specs=_SPECS,
            target_roots={"map": tmp_path / "new-map", "pinvi": tmp_path / "new-pinvi"},
        )


def test_parse_pinned_source_selection_rejects_duplicate_source_root(
    tmp_path: Path,
) -> None:
    map_root = tmp_path / "map-source"
    pinvi_root = tmp_path / "pinvi-source"
    map_root.mkdir(mode=0o700)
    pinvi_root.mkdir(mode=0o700)
    raw = _raw_env(map_root, pinvi_root) + f"KOR_TRAVEL_MAP_REPO_DIR={map_root}\n".encode()

    with pytest.raises(DeploymentContractError, match="exactly once"):
        parse_pinned_source_selection(
            raw,
            specs=_SPECS,
            target_roots={"map": tmp_path / "new-map", "pinvi": tmp_path / "new-pinvi"},
        )


def test_source_owner_origin_check_drops_privileges_and_never_uses_root_git_config(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    calls: list[dict[str, object]] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=f"{_MAP.canonical_url}\n",
            stderr="",
        )

    identity = canonical_source_identity(source_root, spec=_MAP, runner=runner)

    assert identity.uid == os.getuid()
    assert len(calls) == 1
    kwargs = calls[0]["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == "/"
    assert callable(kwargs["preexec_fn"])
    assert kwargs["env"] == {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file",
    }
    command = calls[0]["args"][0]
    assert isinstance(command, list)
    assert command[:5] == ["/usr/bin/git", "-C", str(source_root), "config", "--local"]


def test_source_owner_origin_check_rejects_alias_and_map_pinvi_swap(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="https://github.com/digitie/pinvi.git\n",
            stderr="",
        )

    with pytest.raises(DeploymentContractError, match="canonical HTTPS URL"):
        canonical_source_identity(source_root, spec=_MAP, runner=runner)


def test_root_git_environment_allows_only_https() -> None:
    assert source_install._root_git_environment()["GIT_ALLOW_PROTOCOL"] == "https"
    assert source_install._root_git_environment()["GIT_CONFIG_GLOBAL"] == "/dev/null"


def test_every_root_git_operation_disables_hooks_and_unsafe_protocols() -> None:
    calls: list[dict[str, object]] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    source_install._run_root_git(
        ["--git-dir", "/state/.bare/map.git", "worktree", "add", "--detach", "/state/map"],
        runner=runner,
    )

    command = calls[0]["args"][0]
    assert isinstance(command, list)
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


def test_pinned_fetch_uses_only_the_exact_canonical_url_and_release_sha(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    sources = state / "pinned-sources-v1"
    target = sources / "map" / _MAP.revision
    paths = PinnedSourceInstallPaths(
        state_directory=state,
        journal=state / "journal.json",
        backup=state / "backup",
        sources_directory=sources,
    )
    commands: list[list[str]] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        commands.append(command)
        if command[-3:] == ["init", "--bare", str(sources / ".bare" / "map.git")]:
            (sources / ".bare" / "map.git").mkdir(mode=0o700)
        elif "worktree" in command and "add" in command:
            target.mkdir(mode=0o755)
            (target / "tracked.py").write_text("pass\n")
        stdout = "c" * 40 if "rev-parse" in command else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    assert source_install._materialize_pinned_worktree(
        paths=paths,
        spec=_MAP,
        runner=runner,
    ) == "c" * 40

    fetch = next(command for command in commands if "fetch" in command)
    assert "--no-tags" in fetch
    assert _MAP.canonical_url in fetch
    assert _MAP.revision in fetch


def test_existing_pinned_worktree_cannot_bypass_submodule_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    sources = state / "pinned-sources-v1"
    bare = sources / ".bare" / "map.git"
    target = sources / "map" / _MAP.revision
    bare.mkdir(mode=0o700, parents=True)
    target.mkdir(mode=0o555, parents=True)
    os.chmod(sources, 0o700)
    os.chmod(sources / ".bare", 0o700)
    os.chmod(target.parent, 0o700)
    os.chmod(bare, 0o700)
    os.chmod(target, 0o555)
    paths = PinnedSourceInstallPaths(
        state_directory=state,
        journal=state / "journal.json",
        backup=state / "backup",
        sources_directory=sources,
    )
    validated = False

    def validate(*args: object, **kwargs: object) -> str:
        nonlocal validated
        validated = True
        return "c" * 40

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=f"160000 commit {'d' * 40}\tblocked-submodule\n",
            stderr="",
        )

    monkeypatch.setattr(source_install, "_validate_existing_pinned_worktree", validate)

    with pytest.raises(DeploymentContractError, match="must not contain submodules"):
        source_install._materialize_pinned_worktree(
            paths=paths,
            spec=_MAP,
            runner=runner,
        )

    assert not validated


def test_nonterminal_source_installation_blocks_pair_mutation(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    raw = _raw_env(tmp_path / "map-source", tmp_path / "pinvi-source")
    env_path.write_bytes(raw)
    env_path.chmod(0o600)
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "local",
        "COMPOSE_PROJECT_NAME": "pinned-source-test",
        "KTDM_C6C_STATE_ROOT": str(tmp_path / "state"),
    }
    pinned_map, pinned_pinvi = source_install.pinned_repo_specs()
    paths = pinned_source_install_paths(values)
    paths.state_directory.mkdir(mode=0o700, parents=True)
    source_install._write_private_backup(paths.backup, raw)
    source_install._write_journal(
        paths.journal,
        {
            "version": 1,
            "phase": "prepared",
            "old_env_sha256": hashlib.sha256(raw).hexdigest(),
            "new_env_sha256": "f" * 64,
            "backup_sha256": hashlib.sha256(raw).hexdigest(),
            "env_uid": os.getuid(),
            "env_gid": os.getgid(),
            "repositories": [
                {
                    "label": "map",
                    "source_root": str(tmp_path / "map-source"),
                    "target_root": str(
                        paths.sources_directory / "map" / pinned_map.revision
                    ),
                    "revision": pinned_map.revision,
                    "tree": "0" * 40,
                    "source_identity": SourceIdentity(1, 2, 3, 4, 0o700).__dict__,
                },
                {
                    "label": "pinvi",
                    "source_root": str(tmp_path / "pinvi-source"),
                    "target_root": str(
                        paths.sources_directory / "pinvi" / pinned_pinvi.revision
                    ),
                    "revision": pinned_pinvi.revision,
                    "tree": "0" * 40,
                    "source_identity": SourceIdentity(5, 6, 7, 8, 0o700).__dict__,
                },
            ],
        },
    )

    with pytest.raises(DeploymentContractError, match="unfinished"):
        assert_pinned_source_installation_allows_pair_mutation(
            environment=values,
            env_path=env_path,
            expected_owner_uid=os.getuid(),
            expected_owner_gid=os.getgid(),
        )


def test_rolled_back_source_installation_requires_the_original_env_bytes(
    tmp_path: Path,
) -> None:
    old_raw = b"OLD=value\n"
    new_raw = b"NEW=value\n"
    env_path = tmp_path / ".env"
    env_path.write_bytes(new_raw)
    env_path.chmod(0o600)
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "local",
        "COMPOSE_PROJECT_NAME": "pinned-source-test",
        "KTDM_C6C_STATE_ROOT": str(tmp_path / "state"),
    }
    paths = pinned_source_install_paths(values)
    paths.state_directory.mkdir(mode=0o700, parents=True)
    pinned_map, pinned_pinvi = source_install.pinned_repo_specs()
    source_install._write_private_backup(paths.backup, old_raw)
    source_install._write_journal(
        paths.journal,
        {
            "version": 1,
            "phase": "rolled_back",
            "old_env_sha256": hashlib.sha256(old_raw).hexdigest(),
            "new_env_sha256": hashlib.sha256(new_raw).hexdigest(),
            "backup_sha256": hashlib.sha256(old_raw).hexdigest(),
            "env_uid": os.getuid(),
            "env_gid": os.getgid(),
            "repositories": [
                {
                    "label": "map",
                    "source_root": str(tmp_path / "map-source"),
                    "target_root": str(
                        paths.sources_directory / "map" / pinned_map.revision
                    ),
                    "revision": pinned_map.revision,
                    "tree": "0" * 40,
                    "source_identity": SourceIdentity(1, 2, 3, 4, 0o700).__dict__,
                },
                {
                    "label": "pinvi",
                    "source_root": str(tmp_path / "pinvi-source"),
                    "target_root": str(
                        paths.sources_directory / "pinvi" / pinned_pinvi.revision
                    ),
                    "revision": pinned_pinvi.revision,
                    "tree": "0" * 40,
                    "source_identity": SourceIdentity(5, 6, 7, 8, 0o700).__dict__,
                },
            ],
        },
    )

    with pytest.raises(DeploymentContractError, match="requires the original"):
        assert_pinned_source_installation_allows_pair_mutation(
            environment=values,
            env_path=env_path,
            expected_owner_uid=os.getuid(),
            expected_owner_gid=os.getgid(),
        )


def test_compose_service_pinned_source_entrypoint_never_captures_or_runs_compose(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    snapshot = SimpleNamespace(
        effective={"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
        env_path=str(tmp_path / ".env"),
        env_file_bytes=b"frozen-env",
        env_file_identity=SimpleNamespace(uid=1000, gid=1000),
    )
    installer_result = {"success": True, "state": "committed", "resumed": False}
    expected = {**installer_result, "returncode": 0}
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: nullcontext(SimpleNamespace()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda **kwargs: snapshot,
    )
    monkeypatch.setattr(
        compose_service_module,
        "assert_environment_snapshot_matches_c6c_lock",
        lambda *args: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "assert_manager_mutation_allowed",
        lambda **kwargs: "production",
    )
    monkeypatch.setattr(
        compose_service_module,
        "load_c6c_deployment_config_from_environment",
        lambda values: SimpleNamespace(production=True),
    )
    monkeypatch.setattr(compose_service_module, "_require_cache_target_release", lambda config: None)

    def install(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return installer_result

    monkeypatch.setattr(compose_service_module, "install_trusted_pinned_sources", install)
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        lambda **kwargs: pytest.fail("F1E must not capture Compose"),
    )

    assert service.install_pinned_sources() == expected
    assert captured == {
        "environment": snapshot.effective,
        "env_path": Path(snapshot.env_path),
        "env_bytes": snapshot.env_file_bytes,
        "expected_owner_uid": 1000,
        "expected_owner_gid": 1000,
    }
