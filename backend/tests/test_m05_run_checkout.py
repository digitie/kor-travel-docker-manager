"""M05 실행별 소스 checkout이 PinVi attestation의 요구를 만족한다(ADR-51 E-2).

M05는 봉인 트리 대신 실행마다 canonical HTTPS에서 핀된 revision만 얕게 받아 checkout한다.
PinVi `m05_activation_attestation.py`의 `_assert_clean_checkout`은 `--show-toplevel`이 root이고,
HEAD가 핀이고, `status --porcelain --untracked-files=all`이 비어 있기를 요구한다. `_git_blob`은
Map root에서 service 릴리스 revision의 blob을 `git show`로 읽는다. 여기서는 진짜 git으로
checkout을 만들어 그 네 가지를 거울처럼 확인한다. canonical URL만 로컬 `file://` origin으로
바꾸는 runner를 주입한다 — 나머지 인자와 정화된 환경은 프로덕션 그대로다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kor_travel_docker_manager.services.pinned_runtime_sources import (
    DeploymentContractError,
    checkout_pinned_run_source,
)

_CANONICAL = "https://github.com/example/kor-travel-map.git"


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
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


@pytest.fixture()
def origin(tmp_path: Path) -> Any:
    """두 commit을 가진 origin — 오래된 쪽이 service 릴리스, 새 쪽이 핀이다."""

    root = tmp_path / "origin"
    (root / "packages").mkdir(parents=True)
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    surface = root / "packages" / "openapi.service.json"
    surface.write_text('{"release": "service"}\n', encoding="utf-8")
    _git("init", "-q", "-b", "main", str(root))
    _git("-C", str(root), "add", "-A")
    _git("-C", str(root), "commit", "-qm", "service release")
    service = _git("-C", str(root), "rev-parse", "HEAD")
    surface.write_text('{"release": "pinned"}\n', encoding="utf-8")
    _git("-C", str(root), "commit", "-qam", "pinned")
    pinned = _git("-C", str(root), "rev-parse", "HEAD")
    # 오래된 commit도 SHA로 받을 수 있어야 한다(GitHub는 도달 가능한 commit을 준다).
    _git("-C", str(root), "config", "uploadpack.allowAnySHA1InWant", "true")
    release = SimpleNamespace(
        sources=[SimpleNamespace(role="map", revision=pinned, canonical_url=_CANONICAL)]
    )
    return SimpleNamespace(root=root, service=service, pinned=pinned, release=release)


def _local_runner(origin_root: Path, calls: list[dict[str, Any]]) -> Any:
    """canonical HTTPS를 로컬 `file://` origin으로만 바꾼다."""

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": list(argv), "env": dict(kwargs.get("env") or {})})
        rewritten = [
            f"file://{origin_root}" if part == _CANONICAL else part for part in argv
        ]
        rewritten = [
            "protocol.file.allow=always" if part == "protocol.file.allow=never" else part
            for part in rewritten
        ]
        env = {**kwargs.pop("env"), "GIT_ALLOW_PROTOCOL": "file"}
        return subprocess.run(rewritten, env=env, **kwargs)

    return run


def test_the_run_checkout_satisfies_the_attestation_contract(
    origin: Any, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []
    destination = tmp_path / "runtime" / "map-src"
    destination.parent.mkdir(mode=0o700)

    root = checkout_pinned_run_source(
        release=origin.release,
        role="map",
        destination=destination,
        extra_revisions=(origin.service,),
        runner=_local_runner(origin.root, calls),
    )

    assert root == destination
    assert (destination / ".git").is_dir()
    assert Path(_git("-C", str(destination), "rev-parse", "--show-toplevel")).resolve() == (
        destination.resolve()
    )
    assert _git("-C", str(destination), "rev-parse", "HEAD") == origin.pinned
    assert _git("-C", str(destination), "status", "--porcelain", "--untracked-files=all") == ""
    assert (
        _git("-C", str(destination), "show", f"{origin.service}:packages/openapi.service.json")
        == '{"release": "service"}'
    )
    assert (destination / "packages" / "openapi.service.json").read_text(encoding="utf-8") == (
        '{"release": "pinned"}\n'
    )


def test_the_run_checkout_uses_the_sanitized_https_only_git(
    origin: Any, tmp_path: Path
) -> None:
    """root git은 hook·credential·file/ext 프로토콜 없이, 사람의 gitconfig 없이 돈다."""

    calls: list[dict[str, Any]] = []
    checkout_pinned_run_source(
        release=origin.release,
        role="map",
        destination=tmp_path / "map-src",
        runner=_local_runner(origin.root, calls),
    )

    fetch = next(call for call in calls if "fetch" in call["argv"])
    assert fetch["argv"][fetch["argv"].index("fetch") :] == [
        "fetch",
        "-q",
        "--depth",
        "1",
        "--no-tags",
        _CANONICAL,
        origin.pinned,
    ]
    for call in calls:
        assert "protocol.ext.allow=never" in call["argv"]
        assert "core.hooksPath=/dev/null" in call["argv"]
        assert call["env"]["GIT_ALLOW_PROTOCOL"] == "https"
        assert call["env"]["HOME"] == "/nonexistent"
        assert call["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"


def test_an_existing_destination_is_refused(origin: Any, tmp_path: Path) -> None:
    destination = tmp_path / "map-src"
    destination.mkdir()

    with pytest.raises(FileExistsError):
        checkout_pinned_run_source(
            release=origin.release,
            role="map",
            destination=destination,
            runner=_local_runner(origin.root, []),
        )


def test_a_malformed_extra_revision_is_refused(origin: Any, tmp_path: Path) -> None:
    with pytest.raises(DeploymentContractError, match="extra revision is invalid"):
        checkout_pinned_run_source(
            release=origin.release,
            role="map",
            destination=tmp_path / "map-src",
            extra_revisions=("HEAD",),
            runner=_local_runner(origin.root, []),
        )
    assert not (tmp_path / "map-src").exists()
