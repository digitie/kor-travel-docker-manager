"""Compose build context는 운영 source 디렉터리의 owner-only mode를 이미지로 옮기지 않는다.

2026-10-27 Concierge 배포: ``KOR_TRAVEL_CONCIERGE_REPO_DIR``(700/600)를 그대로 context로 빌드한
code server가 비-root ``USER``로 ``No module named 'ktc.dagster'``에 실패했다. 이 파일은 그 tree를
재현해서, Docker에 건네는 context가 world-readable인지를 효과로 본다.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kor_travel_docker_manager.services.build_context_export import (
    DockerIgnore,
    compose_document_with_exported_build_contexts,
    export_build_context,
)
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.compose_service import ComposeService

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode semantics")

_CONCIERGE_DOCKERIGNORE = """\
.git
.gitignore
.env
.env.*
!.env.example

**/__pycache__/
**/*.pyc
*.db

frontend/node_modules/
frontend/.next/
.venv/
**/*.local.md
"""


@pytest.fixture
def owner_only_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def _owner_only_repo(root: Path) -> Path:
    """운영 sync 사본처럼 디렉터리 700, 파일 600(스크립트 700)인 source tree."""

    files = {
        "Dockerfile.python": "FROM python:3.12-slim\n",
        ".dockerignore": _CONCIERGE_DOCKERIGNORE,
        ".env": "SECRET=do-not-ship\n",
        ".env.prod": "SECRET=do-not-ship\n",
        ".env.example": "SECRET=\n",
        "backend/ktc/__init__.py": "",
        "backend/ktc/dagster/__init__.py": "",
        "backend/ktc/dagster/definitions.py": "defs = None\n",
        "backend/ktc/__pycache__/x.cpython-312.pyc": "bytecode",
        "backend/notes.local.md": "private\n",
        "scripts/entrypoint.sh": "#!/bin/sh\n",
        "tripmate.db": "db",
        "frontend/package.json": "{}\n",
        "frontend/node_modules/left-pad/index.js": "module.exports = 1\n",
        ".venv/bin/python": "",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)
    os.chmod(root / "scripts/entrypoint.sh", 0o700)
    os.symlink("backend/ktc", root / "ktc-link")
    for current, directories, _files in os.walk(root):
        for name in directories:
            os.chmod(os.path.join(current, name), 0o700)
    os.chmod(root, 0o700)
    return root


def _modes(root: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for current, directories, files in os.walk(root):
        for name in [*directories, *files]:
            path = Path(current, name)
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                continue
            found[path.relative_to(root).as_posix()] = stat.S_IMODE(metadata.st_mode)
    found["."] = stat.S_IMODE(root.lstat().st_mode)
    return found


def test_export_normalizes_owner_only_tree_to_world_readable(
    tmp_path: Path, owner_only_umask: None
) -> None:
    source = _owner_only_repo(tmp_path / "repo")
    # 전제: 원본은 정말로 group/other가 읽을 수 없다(이 전제가 깨지면 아래 단언은 무의미하다).
    assert all(mode & 0o077 == 0 for mode in _modes(source).values())

    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    os.symlink(outside, source / "outside-link")

    destination = tmp_path / "export"
    export_build_context(
        source,
        destination,
        ignore=DockerIgnore.from_file(source / ".dockerignore"),
        keep={"Dockerfile.python", ".dockerignore"},
    )

    # symlink는 링크로 옮기고, 정규화가 링크를 따라 사본 밖의 mode를 바꾸지 않는다.
    assert os.readlink(destination / "outside-link") == str(outside)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700
    modes = _modes(destination)
    for relative, mode in modes.items():
        path = destination / relative
        if path.is_dir():
            assert mode == 0o755, relative
        else:
            assert mode in {0o644, 0o755}, relative
            assert mode & 0o004, f"{relative} must be world-readable"
    assert modes["scripts/entrypoint.sh"] == 0o755
    assert modes["backend/ktc/dagster/definitions.py"] == 0o644
    assert (destination / "backend/ktc/dagster/definitions.py").read_text() == "defs = None\n"
    assert os.readlink(destination / "ktc-link") == "backend/ktc"


def test_export_applies_docker_ignore_rules(tmp_path: Path) -> None:
    source = _owner_only_repo(tmp_path / "repo")
    destination = tmp_path / "export"
    export_build_context(
        source,
        destination,
        ignore=DockerIgnore.from_file(source / ".dockerignore"),
        keep={"Dockerfile.python", ".dockerignore"},
    )

    exported = set(_modes(destination))
    assert ".env" not in exported
    assert ".env.prod" not in exported
    assert ".env.example" in exported  # `!` 예외
    assert "backend/ktc/__pycache__" not in exported
    assert "backend/notes.local.md" not in exported
    assert "tripmate.db" not in exported
    assert "frontend/node_modules" not in exported
    assert ".venv" not in exported
    assert {"Dockerfile.python", ".dockerignore", "frontend/package.json"} <= exported


def test_export_keeps_dockerfile_even_when_ignored(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    (source / "docker").mkdir(parents=True)
    (source / "docker/api.Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (source / "app.py").write_text("", encoding="utf-8")
    destination = tmp_path / "export"

    export_build_context(
        source,
        destination,
        ignore=DockerIgnore(["docker", "*.py"]),
        keep={"docker/api.Dockerfile"},
    )

    assert (destination / "docker/api.Dockerfile").is_file()
    assert not (destination / "app.py").exists()


@pytest.mark.parametrize(
    ("patterns", "path", "excluded"),
    [
        (["node_modules"], "node_modules/a/b.js", True),
        (["*/node_modules"], "frontend/node_modules/x", True),
        (["*/node_modules"], "a/b/node_modules/x", False),
        (["**/node_modules"], "a/b/node_modules/x", True),
        (["*.md", "!README.md"], "README.md", False),
        (["*.md", "!README.md"], "docs/x.md", False),
        (["**/*.md", "!README.md"], "docs/x.md", True),
        (["/build"], "build/out", True),
        (["./build/"], "build", True),
        (["a?c"], "abc", True),
        (["a?c"], "a/c", False),
        (["docs/**"], "docs/a/b", True),
        (["docs/**"], "docs", False),
        (["# comment", ""], "# comment", False),
        (["dir", "!dir/keep"], "dir/keep", False),
        (["dir", "!dir/keep"], "dir/drop", True),
        (["a.b"], "aXb", False),
        # BuildKit은 부모에서 건너뛴 규칙을 자식 경로만으로 다시 본다(MatchesUsingParentResults).
        (["d", "!d/*", "d"], "d/f", False),
        (["  #x"], "#x", True),
        (["a"], "a\n", False),
    ],
)
def test_docker_ignore_matches_moby_semantics(
    patterns: list[str], path: str, excluded: bool
) -> None:
    assert DockerIgnore(patterns).excludes(path) is excluded


def test_export_skips_excluded_directories_that_no_exception_reaches(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repo"
    (source / ".native-test").mkdir(parents=True)
    (source / ".native-test/secret").write_text("x", encoding="utf-8")
    (source / "keep").mkdir()
    (source / "keep/a.txt").write_text("a", encoding="utf-8")
    (source / ".env.example").write_text("", encoding="utf-8")
    # 읽을 수 없는 제외 디렉터리 — Docker는 열지 않으므로 사본도 열지 않아야 한다.
    os.chmod(source / ".native-test", 0)
    ignore = DockerIgnore([".native-test/", ".env*", "!.env.example", "keep", "!keep/*.txt"])
    assert not ignore.may_reinclude_below(".native-test")
    assert ignore.may_reinclude_below("keep")
    destination = tmp_path / "export"
    try:
        if os.geteuid() == 0:
            pytest.skip("root reads mode-0 directories")
        export_build_context(source, destination, ignore=ignore)
    finally:
        os.chmod(source / ".native-test", 0o700)
    assert (destination / ".env.example").is_file()
    assert (destination / "keep/a.txt").is_file()
    assert not (destination / ".native-test").exists()


def test_export_does_not_follow_a_swapped_symlink(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("secret", encoding="utf-8")
    os.chmod(victim, 0o600)
    os.symlink(victim, source / "file")
    destination = tmp_path / "export"

    export_build_context(source, destination, ignore=DockerIgnore(()))

    assert os.readlink(destination / "file") == str(victim)
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600


def _compose_document(source: Path) -> dict[str, Any]:
    return {
        "services": {
            "concierge-dagster": {
                "image": "concierge-dagster:candidate",
                "build": {
                    "context": str(source),
                    "dockerfile": "Dockerfile.python",
                    "target": "dagster",
                },
                "env_file": [{"path": str(source / ".env"), "required": False}],
            },
            "concierge-api": {
                "image": "concierge-api:candidate",
                "build": {"context": str(source), "dockerfile": "Dockerfile.python"},
            },
            "concierge-ui": {
                "image": "concierge-ui:candidate",
                "build": {"context": str(source / "frontend")},
            },
            "postgres": {"image": "postgis/postgis:17"},
        }
    }


def test_compose_document_never_points_a_build_at_the_live_source(
    tmp_path: Path, owner_only_umask: None
) -> None:
    source = _owner_only_repo(tmp_path / "repo")
    (source / "frontend/Dockerfile").write_text("FROM node:22\n", encoding="utf-8")
    original = _compose_document(source)
    snapshot = json.dumps(original, sort_keys=True)
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)

    rewritten = compose_document_with_exported_build_contexts(
        original,
        build_services={"concierge-dagster", "concierge-api"},
        staging_directory=staging,
    )

    assert json.dumps(original, sort_keys=True) == snapshot  # 입력은 바꾸지 않는다
    services = rewritten["services"]
    dagster_build = services["concierge-dagster"]["build"]
    api_build = services["concierge-api"]["build"]
    # 같은 context·ignore 파일은 사본 하나를 나눠 쓴다.
    assert dagster_build["context"] == api_build["context"]
    context = Path(dagster_build["context"])
    assert context.parent == staging
    assert dagster_build["dockerfile"] == "Dockerfile.python"
    assert dagster_build["target"] == "dagster"
    # env_file은 운영 디렉터리 그대로다 — 바뀌는 것은 build context뿐이다.
    assert services["concierge-dagster"]["env_file"] == original["services"][
        "concierge-dagster"
    ]["env_file"]
    # 빌드 대상이 아닌 서비스는 implicit build를 할 수 없다.
    assert "build" not in services["concierge-ui"]
    assert services["postgres"] == {"image": "postgis/postgis:17"}
    # 효과: 비-root USER가 읽어야 하는 코드가 world-readable이다.
    definitions = context / "backend/ktc/dagster/definitions.py"
    assert stat.S_IMODE(definitions.stat().st_mode) == 0o644
    for parent in definitions.relative_to(context).parents:
        assert stat.S_IMODE((context / parent).stat().st_mode) == 0o755
    assert not (context / ".env").exists()


def test_compose_document_rejects_unresolved_or_additional_contexts(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(DeploymentContractError, match="must be absolute"):
        compose_document_with_exported_build_contexts(
            {"services": {"a": {"build": {"context": "${REPO_DIR:-../repo}"}}}},
            build_services={"a"},
            staging_directory=staging,
        )
    with pytest.raises(DeploymentContractError, match="additional build contexts"):
        compose_document_with_exported_build_contexts(
            {
                "services": {
                    "a": {
                        "build": {
                            "context": str(tmp_path),
                            "additional_contexts": {"x": str(tmp_path)},
                        }
                    }
                }
            },
            build_services={"a"},
            staging_directory=staging,
        )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["up", "-d", "--no-deps", "concierge-dagster"], frozenset()),
        (["stop", "concierge-api"], frozenset()),
        (["build", "concierge-api"], frozenset({"concierge-api"})),
        (
            ["up", "-d", "--build", "--no-deps", "concierge-dagster"],
            frozenset({"concierge-dagster"}),
        ),
        (
            ["build"],
            frozenset({"concierge-dagster", "concierge-api", "concierge-ui"}),
        ),
    ],
)
def test_build_targets_are_only_explicit_builds(
    tmp_path: Path, args: list[str], expected: frozenset[str]
) -> None:
    document = _compose_document(tmp_path)
    assert ComposeService()._compose_build_targets(args, document) == expected


def test_compose_document_for_docker_exports_under_manager_state(
    tmp_path: Path, owner_only_umask: None
) -> None:
    from contextlib import ExitStack

    source = _owner_only_repo(tmp_path / "repo")
    state_root = tmp_path / "state"
    snapshot: Any = SimpleNamespace(
        effective={
            "COMPOSE_PROJECT_NAME": "kor-travel-test",
            "KTDM_PINNED_RUNTIME_STATE_ROOT": str(state_root),
        }
    )
    service = ComposeService()
    with ExitStack() as stack:
        document = service._compose_document_for_docker(
            ["build", "concierge-dagster"],
            _compose_document(source),
            environment_snapshot=snapshot,
            stack=stack,
        )
        context = Path(document["services"]["concierge-dagster"]["build"]["context"])
        assert context.is_relative_to(state_root / "kor-travel-test")
        assert context != source
        assert (context / "backend/ktc/dagster/definitions.py").stat().st_mode & 0o004
        assert "build" not in document["services"]["concierge-api"]
    # Compose가 끝나면 사본은 지운다.
    assert not context.exists()
