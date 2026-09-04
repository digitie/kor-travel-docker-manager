"""실행이 봉인된 핀 소스 트리를 건드리지 않는지 본다.

격리 e2e의 러너는 저장소 루트를 컨테이너에 **root RW**로 마운트하고 그 안에서
`npm ci`와 Playwright를 돌린다. 봉인 트리(0555/0444)를 그대로 주면 root는 모드를
무시하므로 `apps/web/node_modules`가 실제로 쓰이고 Docker가 만드는 마운트포인트
셋이 0755 디렉터리로 남는다. 그러면 다음 preflight의 `_validate_immutable_tree`가
정당하게 거부해 **같은 pinset 재실행이 불가능해진다** — 2026-09-03·04에 연속으로
재현됐고 각각 한 사이클(약 1.5시간)을 태웠다.

그래서 실행은 일회용 체크아웃에서 한다. 그 체크아웃은 디스크 사본이 아니라
**object store에서 재유도**된다 — 같은 bare 저장소, 같은 revision, 같은 tree
object다. 파일 모드도 잔여물도 물려받지 않는다.

여기서는 텍스트가 아니라 **동작**을 본다 — 진짜 git 저장소를 만들고, 봉인하고,
일회용 체크아웃에 쓰고, 봉인이 그대로인지 확인한다.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services.pinned_runtime_sources import (
    _validate_immutable_tree,
)

pytestmark = pytest.mark.skipif(
    not hasattr(os, "geteuid"), reason="봉인 검사는 POSIX 소유자 개념을 요구한다"
)


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )
    return completed.stdout.strip()


@pytest.fixture()
def repository(tmp_path: Path) -> dict[str, Any]:
    """bare + 봉인 worktree + 그 bare에서 나온 일회용 체크아웃."""
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "apps").mkdir()
    (origin / "apps" / "app.txt").write_text("pinned\n", encoding="utf-8")
    _git("init", "-q", "-b", "main", str(origin))
    _git("-C", str(origin), "config", "user.email", "t@example.invalid")
    _git("-C", str(origin), "config", "user.name", "t")
    _git("-C", str(origin), "add", "-A")
    _git("-C", str(origin), "commit", "-qm", "pinned")
    revision = _git("-C", str(origin), "rev-parse", "HEAD")

    bare = tmp_path / "pinvi.git"
    _git("clone", "-q", "--bare", str(origin), str(bare))

    sealed = tmp_path / "sealed"
    _git("--git-dir", str(bare), "worktree", "add", "-q", "--detach", str(sealed), revision)
    _seal(sealed)
    return {"bare": bare, "sealed": sealed, "revision": revision, "tmp": tmp_path}


def _seal(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False):
        for name in files:
            os.chmod(Path(current) / name, 0o444)
        for name in directories:
            os.chmod(Path(current) / name, 0o555)
    os.chmod(root, 0o555)


def _unseal(root: Path) -> None:
    """tmp_path 정리를 위해 되돌린다."""
    for current, directories, files in os.walk(root, topdown=False):
        for name in files:
            os.chmod(Path(current) / name, 0o644)
        for name in directories:
            os.chmod(Path(current) / name, 0o755)
    os.chmod(root, 0o755)


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() != 0,
    reason="이 케이스의 요점은 **root가 모드를 무시한다**는 것이다 — 비-root로는 재현되지 않는다",
)
def test_writing_into_the_sealed_tree_breaks_the_seal(repository: dict[str, Any]) -> None:
    """이 게이트가 지키는 사실 자체 — 봉인 트리에 쓰면 다음 검사가 거부한다.

    하네스는 root로 돌므로 0555 디렉터리 안에도 쓴다(CAP_DAC_OVERRIDE). 그것이
    2026-09-03·04에 같은 pinset 재실행을 막은 잔여물을 만들었다.
    """
    sealed: Path = repository["sealed"]
    _validate_immutable_tree(sealed)  # 지금은 통과한다

    # 러너가 하는 일과 같다: root가 0555를 무시하고 디렉터리를 만든다.
    residue = sealed / "node_modules"
    residue.mkdir(mode=0o755)
    try:
        with pytest.raises(Exception, match="unsafe"):
            _validate_immutable_tree(sealed)
    finally:
        residue.rmdir()
        _unseal(sealed)


def test_a_disposable_checkout_is_rederived_not_copied(repository: dict[str, Any]) -> None:
    """일회용 체크아웃은 봉인 트리의 모드를 물려받지 않는다.

    디스크 사본(`cp -a`)이었다면 0555/0444가 그대로 따라와 `npm ci`가 다시 root
    권한에 기대게 된다. object store에서 재유도하면 그 결합이 사라진다.
    """
    bare: Path = repository["bare"]
    run_root: Path = repository["tmp"] / "run"
    _git("--git-dir", str(bare), "worktree", "add", "-q", "--detach", str(run_root), repository["revision"])
    try:
        assert _git("-C", str(run_root), "rev-parse", "HEAD") == repository["revision"]
        # 봉인 트리와 같은 내용이지만 쓰기가 가능하다.
        assert (run_root / "apps" / "app.txt").read_text(encoding="utf-8") == "pinned\n"
        mode = stat.S_IMODE((run_root / "apps").lstat().st_mode)
        assert mode & stat.S_IWUSR, oct(mode)
        (run_root / "node_modules").mkdir()
        assert _git("-C", str(run_root), "status", "--porcelain") == ""
    finally:
        _git("--git-dir", str(bare), "worktree", "remove", "--force", str(run_root))


def test_running_in_the_disposable_checkout_leaves_the_seal_intact(
    repository: dict[str, Any],
) -> None:
    """핵심 주장 — 일회용 체크아웃에 써도 봉인 트리는 그대로다."""
    bare: Path = repository["bare"]
    sealed: Path = repository["sealed"]
    run_root: Path = repository["tmp"] / "run2"
    _git("--git-dir", str(bare), "worktree", "add", "-q", "--detach", str(run_root), repository["revision"])
    try:
        # 러너가 남기던 것 넷을 그대로 만든다.
        for relative in ("node_modules", "apps/node_modules", "apps/test-results"):
            (run_root / relative).mkdir(parents=True, exist_ok=True)
        (run_root / "node_modules" / "x.txt").write_text("y", encoding="utf-8")
        _validate_immutable_tree(sealed)  # 봉인은 그대로여야 한다
    finally:
        _git("--git-dir", str(bare), "worktree", "remove", "--force", str(run_root))
        _unseal(sealed)


def test_removal_clears_the_git_admin_entry(repository: dict[str, Any]) -> None:
    """디렉터리만 지우면 bare에 stale 엔트리가 남는다 — `worktree prune`은 운영 금지다."""
    bare: Path = repository["bare"]
    run_root: Path = repository["tmp"] / "run3"
    _git("--git-dir", str(bare), "worktree", "add", "-q", "--detach", str(run_root), repository["revision"])
    listed = _git("--git-dir", str(bare), "worktree", "list")
    assert str(run_root) in listed

    _git("--git-dir", str(bare), "worktree", "remove", "--force", str(run_root))
    assert not run_root.exists()
    assert str(run_root) not in _git("--git-dir", str(bare), "worktree", "list")
    _unseal(repository["sealed"])
