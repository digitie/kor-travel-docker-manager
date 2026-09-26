"""봉인 worktree 승격의 복구 진단.

ADR-51 E-2부터 M05는 봉인 트리 대신 실행별 checkout(`checkout_pinned_run_source`,
`test_m05_run_checkout.py`)에서 돈다. 일회용 worktree 재유도·제거·요약·봉인 사후조건은
그때 지웠다. 남은 것은 재구축 source 승격의 진단 하나이고, 이 파일은 E-3(재구축 소스를
archive로)에서 함께 없어진다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kor_travel_docker_manager.services import pinned_runtime_sources as module
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    DeploymentContractError,
    _promote_staging_worktree,
)

pytestmark = pytest.mark.skipif(
    not hasattr(os, "geteuid"), reason="봉인 검사는 POSIX 소유자 개념을 요구한다"
)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )
    return completed.stdout.strip()


def _seal(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False):
        for name in files:
            os.chmod(Path(current) / name, 0o444)
        for name in directories:
            os.chmod(Path(current) / name, 0o555)
    os.chmod(root, 0o555)


def _unseal(root: Path) -> None:
    """tmp_path 정리를 위해 되돌린다."""

    if not root.exists():
        return
    for current, directories, files in os.walk(root, topdown=False):
        for name in files:
            os.chmod(Path(current) / name, 0o644)
        for name in directories:
            os.chmod(Path(current) / name, 0o755)
    os.chmod(root, 0o755)


@pytest.fixture()
def pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """bare + 봉인 worktree + 프로덕션 함수가 볼 수 있게 배선된 상태.

    상태 경로 계약(`_require_canonical_rebuildable_state_paths`)은 실제 호스트
    레이아웃을 요구하므로 여기서는 무해화하고, **검사 대상인 git·모드 동작만** 진짜로
    남긴다.
    """

    origin = tmp_path / "origin"
    (origin / "apps" / "web").mkdir(parents=True)
    (origin / "apps" / "web" / "app.txt").write_text("pinned\n", encoding="utf-8")
    (origin / ".gitignore").write_text("node_modules/\ntest-results/\n", encoding="utf-8")
    _git("init", "-q", "-b", "main", str(origin))
    _git("-C", str(origin), "config", "user.email", "t@example.invalid")
    _git("-C", str(origin), "config", "user.name", "t")
    _git("-C", str(origin), "add", "-A")
    _git("-C", str(origin), "commit", "-qm", "pinned")
    revision = _git("-C", str(origin), "rev-parse", "HEAD")
    tree = _git("-C", str(origin), "rev-parse", "HEAD^{tree}")

    bare = tmp_path / "pinvi.git"
    _git("clone", "-q", "--bare", str(origin), str(bare))

    sealed = tmp_path / "sealed"
    _git("--git-dir", str(bare), "worktree", "add", "-q", "--detach", str(sealed), revision)
    _seal(sealed)

    # 일회용 체크아웃의 parent는 0700이어야 한다(하네스의 `runtime/`과 같은 계약).
    run_parent = tmp_path / "runtime"
    run_parent.mkdir(mode=0o700)

    release = SimpleNamespace(
        sources=[SimpleNamespace(role="pinvi", revision=revision, tree=tree)]
    )
    monkeypatch.setattr(
        module, "_require_canonical_rebuildable_state_paths", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        module,
        "pinned_runtime_source_paths",
        lambda **_kwargs: SimpleNamespace(
            bare_repository=lambda _role: bare,
            worktree=lambda _source: sealed,
        ),
    )
    state = SimpleNamespace(
        bare=bare,
        sealed=sealed,
        revision=revision,
        tree=tree,
        run_parent=run_parent,
        release=release,
        call=lambda function, **kwargs: function(
            release=release, state_paths=SimpleNamespace(), values={}, role="pinvi", **kwargs
        ),
    )
    try:
        yield state
    finally:
        _unseal(sealed)


# ------------------------------------------------------------------ 복구 진단


def test_promotion_names_the_fix_for_a_registered_but_missing_target(pinned: Any) -> None:
    """오염된 봉인 트리를 `rm -rf`로 지우면 **등록만 남고 다음 실행이 죽는다.**

    종전에는 그 fatal이 일반 Git 실패로 접혀 사유가 보이지 않았고, 같은 모양으로 또 한
    사이클을 태웠다. 진단이 조치까지 말해야 런북이 실제 복구 레버를 가리킨다
    (적대 리뷰 #2 실측).
    """

    _unseal(pinned.sealed)
    shutil.rmtree(pinned.sealed)
    with pytest.raises(DeploymentContractError, match="registered but missing"):
        _promote_staging_worktree(
            bare=pinned.bare,
            staging=pinned.run_parent / "staging",
            target=pinned.sealed,
            runner=subprocess.run,
        )
