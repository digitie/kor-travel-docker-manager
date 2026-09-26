"""재구축 source 트리: revision으로 이름 붙은 `git archive` 디렉터리(ADR-51 E-3).

진짜 git으로 로컬 origin 두 개를 만들고 프로덕션 함수를 직접 부른다. canonical HTTPS URL만
로컬 `file://`로 바꾸는 runner를 주입한다 — 나머지 인자와 정화된 환경은 프로덕션 그대로다.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import PinnedRuntimeStatePaths
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    pinned_runtime_state_paths as canonical_pinned_runtime_state_paths,
)
from kor_travel_docker_manager.services.pinned_runtime_release import RUNTIME_SOURCE_ROLES
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    materialize_pinned_runtime_sources,
    pinned_runtime_sources_directory,
    prune_pinned_runtime_sources,
)

_URLS = {role: f"https://github.com/example/{role}.git" for role in RUNTIME_SOURCE_ROLES}


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        },
    )
    return completed.stdout.strip()


def _origin(root: Path, role: str) -> str:
    """실행 파일 하나와 export-ignore 표시가 된 파일 하나를 가진 저장소."""

    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run.sh").write_text("#!/bin/sh\necho run\n", encoding="utf-8")
    (root / "scripts" / "run.sh").chmod(0o755)
    (root / "docker-compose.yml").write_text(f"name: {role}\n", encoding="utf-8")
    (root / "ignored-by-archive.txt").write_text("still in the tree\n", encoding="utf-8")
    (root / ".gitattributes").write_text("ignored-by-archive.txt export-ignore\n", encoding="utf-8")
    _git("init", "-q", "-b", "main", str(root))
    _git("-C", str(root), "add", "-A")
    _git("-C", str(root), "commit", "-qm", f"{role} pinned")
    _git("-C", str(root), "config", "uploadpack.allowAnySHA1InWant", "true")
    return _git("-C", str(root), "rev-parse", "HEAD")


@pytest.fixture()
def world(tmp_path: Path) -> Any:
    origins = {role: tmp_path / "origins" / role for role in RUNTIME_SOURCE_ROLES}
    revisions = {role: _origin(path, role) for role, path in origins.items()}
    specs = [
        SimpleNamespace(role=role, revision=revisions[role], canonical_url=_URLS[role])
        for role in RUNTIME_SOURCE_ROLES
    ]
    release = SimpleNamespace(
        sources=specs,
        source_for=lambda role: next(spec for spec in specs if spec.role == role),
        pinset_sha256="0" * 64,
    )
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "COMPOSE_PROJECT_NAME": "e3-source-test",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state-root"),
    }
    state_paths: PinnedRuntimeStatePaths = canonical_pinned_runtime_state_paths(
        values, pinset_sha256="0" * 64
    )
    calls: list[dict[str, Any]] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": list(argv), "env": dict(kwargs.get("env") or {})})
        rewritten = [
            next(
                (f"file://{origins[role]}" for role, url in _URLS.items() if part == url),
                part,
            )
            for part in argv
        ]
        rewritten = [
            "protocol.file.allow=always" if part == "protocol.file.allow=never" else part
            for part in rewritten
        ]
        env = {**kwargs.pop("env"), "GIT_ALLOW_PROTOCOL": "file"}
        return subprocess.run(rewritten, env=env, **kwargs)

    return SimpleNamespace(
        origins=origins,
        revisions=revisions,
        release=release,
        state_paths=state_paths,
        calls=calls,
        runner=runner,
    )


def _materialize(world: Any) -> Any:
    return materialize_pinned_runtime_sources(
        release=world.release, state_paths=world.state_paths, runner=world.runner
    )


def test_the_source_tree_is_the_archive_of_the_pinned_revision(world: Any) -> None:
    """트리는 그 revision의 파일 전부이고, 모드는 0644/0755(실행 비트는 index 그대로)다.

    `export-ignore`가 붙은 파일도 들어 있다 — archive가 tree와 같아야 build context가 revision과
    같다. 호출자의 umask가 077이어도(M05 driver) 디렉터리는 0755다.
    """

    previous = os.umask(0o077)
    try:
        result = _materialize(world)
    finally:
        os.umask(previous)

    for role in RUNTIME_SOURCE_ROLES:
        source = result.source_for(role)
        root = source.root
        assert root == (
            pinned_runtime_sources_directory(world.state_paths)
            / f"{role}-{world.revisions[role]}"
            / "tree"
        )
        tracked = set(
            _git("-C", str(world.origins[role]), "ls-tree", "-r", "--name-only", "HEAD").split()
        )
        present = {
            str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
        }
        assert present == tracked
        assert not (root / ".git").exists()
        assert source.tree == _git("-C", str(world.origins[role]), "rev-parse", "HEAD^{tree}")
        assert stat.S_IMODE((root / "scripts" / "run.sh").stat().st_mode) == 0o755
        assert stat.S_IMODE((root / "docker-compose.yml").stat().st_mode) == 0o644
        for path in [root, *root.rglob("*")]:
            mode = stat.S_IMODE(path.stat().st_mode)
            assert not mode & 0o022, path
            if path.is_dir():
                assert mode == 0o755, path


def test_an_existing_revision_is_reused_without_git(world: Any) -> None:
    first = _materialize(world)
    world.calls.clear()

    second = _materialize(world)

    assert world.calls == []
    assert [source.root for source in second.sources] == [
        source.root for source in first.sources
    ]
    assert [source.tree for source in second.sources] == [
        source.tree for source in first.sources
    ]


def test_prune_keeps_the_current_pair_and_removes_the_rest(world: Any) -> None:
    sources = _materialize(world)
    directory = pinned_runtime_sources_directory(world.state_paths)
    stale = directory / f"map-{'1' * 40}"
    (stale / "tree").mkdir(parents=True)
    interrupted = directory / f".pinvi-{'2' * 40}.partial-abc"
    interrupted.mkdir()

    prune_pinned_runtime_sources(world.state_paths, keep=sources)

    assert sorted(path.name for path in directory.iterdir()) == sorted(
        f"{role}-{world.revisions[role]}" for role in RUNTIME_SOURCE_ROLES
    )


def test_a_failed_fetch_leaves_nothing_behind(world: Any) -> None:
    def failing(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "fetch" in argv:
            return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: secret path")
        return world.runner(argv, **kwargs)

    with pytest.raises(DeploymentContractError) as raised:
        materialize_pinned_runtime_sources(
            release=world.release, state_paths=world.state_paths, runner=failing
        )

    assert str(raised.value) == "pinned runtime source Git operation failed"
    assert list(pinned_runtime_sources_directory(world.state_paths).iterdir()) == []


def test_root_git_is_https_only_and_ignores_human_config(world: Any) -> None:
    """비-root 사용자의 gitconfig(`insteadOf` 등)가 root git에 닿지 않는다."""

    _materialize(world)

    fetches = [call for call in world.calls if "fetch" in call["argv"]]
    assert len(fetches) == len(RUNTIME_SOURCE_ROLES)
    for call in world.calls:
        argv, env = call["argv"], call["env"]
        assert argv[0] == "/usr/bin/git"
        for setting in (
            "core.hooksPath=/dev/null",
            "protocol.file.allow=never",
            "protocol.ext.allow=never",
            "credential.helper=",
        ):
            assert setting in argv
        assert env["GIT_ALLOW_PROTOCOL"] == "https"
        assert env["HOME"] == "/nonexistent"
        assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    for call, role in zip(fetches, RUNTIME_SOURCE_ROLES, strict=True):
        assert call["argv"][-2:] == [_URLS[role], world.revisions[role]]
        assert "--depth" in call["argv"]


def test_a_tampered_source_record_is_refused(world: Any) -> None:
    sources = _materialize(world)
    record = sources.source_for("map").root.parent / "source.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["revision"] = "f" * 40
    record.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DeploymentContractError, match="source record is invalid"):
        _materialize(world)
