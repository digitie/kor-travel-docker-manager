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

여기서는 텍스트가 아니라 **동작**을 본다 — 진짜 git 저장소를 만들고 프로덕션 함수를
**직접 호출**한다. 초판(2026-09-04)은 `_validate_immutable_tree`만 import하고 raw
`git`으로 git 자체의 동작을 확인해서, 신규 함수 셋을 전부 `pass`로 바꿔도 green이었다
(적대 리뷰 BLOCKER-2). 아래 각 테스트는 대응하는 프로덕션 동작을 되돌리면 red가 된다.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kor_travel_docker_manager.services import pinned_runtime_sources as module
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    DeploymentContractError,
    _promote_staging_worktree,
    _validate_immutable_tree,
    assert_pinned_worktree_is_still_sealed,
    materialize_disposable_run_worktree,
    remove_disposable_run_worktree,
    summarize_disposable_run_worktree,
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


# ---------------------------------------------------------------- materialize


def test_materialize_rederives_the_pinned_tree(pinned: Any) -> None:
    """일회용 체크아웃은 같은 revision·tree이면서 **쓰기가 가능**하다.

    디스크 사본(`cp -a`)이었다면 0555/0444가 그대로 따라와 `npm ci`가 다시 root
    권한에 기대게 된다. object store에서 재유도하면 그 결합이 사라진다.
    """

    destination = pinned.run_parent / "pinvi-run"
    root = pinned.call(
        materialize_disposable_run_worktree,
        expected_tree=pinned.tree,
        destination=destination,
    )
    assert root == destination
    assert root != pinned.sealed
    assert _git("-C", str(root), "rev-parse", "HEAD") == pinned.revision
    assert (root / "apps/web/app.txt").read_text(encoding="utf-8") == "pinned\n"
    # 쓰기가 가능해야 러너가 root 권한에 기대지 않는다.
    assert stat.S_IMODE((root / "apps").lstat().st_mode) & stat.S_IWUSR
    (root / "apps/web/node_modules").mkdir()
    # 봉인 트리는 그대로다 — 이것이 이 설계의 핵심 주장이다.
    _validate_immutable_tree(pinned.sealed)


def test_materialize_refuses_a_tree_that_does_not_match_the_pin(pinned: Any) -> None:
    """기대 tree는 **호출자가 준 값**과 대조한다.

    같은 bare에서 다시 유도해 비교하면 git 결정성만 확인하는 자기참조가 되어, 어떤
    값을 줘도 통과한다(적대 리뷰 #8). 결박이 살아 있으면 틀린 값은 거부된다.
    """

    with pytest.raises(DeploymentContractError, match="tree does not match"):
        pinned.call(
            materialize_disposable_run_worktree,
            expected_tree="0" * 40,
            destination=pinned.run_parent / "mismatch",
        )


def test_materialize_refuses_an_existing_destination(pinned: Any) -> None:
    """이미 있는 경로를 덮어쓰지 않는다."""

    destination = pinned.run_parent / "taken"
    destination.mkdir(mode=0o700)
    with pytest.raises(DeploymentContractError, match="already exists"):
        pinned.call(
            materialize_disposable_run_worktree,
            expected_tree=pinned.tree,
            destination=destination,
        )


# -------------------------------------------------------------------- removal


def test_removal_clears_the_git_admin_entry(pinned: Any) -> None:
    """디렉터리만 지우면 bare에 stale 엔트리가 남는다 — `worktree prune`은 운영 금지다."""

    destination = pinned.run_parent / "pinvi-run"
    pinned.call(
        materialize_disposable_run_worktree,
        expected_tree=pinned.tree,
        destination=destination,
    )
    assert str(destination) in _git("--git-dir", str(pinned.bare), "worktree", "list")

    pinned.call(remove_disposable_run_worktree, destination=destination)
    assert not destination.exists()
    assert str(destination) not in _git("--git-dir", str(pinned.bare), "worktree", "list")


def test_removal_clears_a_registration_whose_directory_is_already_gone(pinned: Any) -> None:
    """**중단된 실행이 남긴 등록**을 하네스가 스스로 치울 수 있어야 한다.

    `main()`의 `finally`는 SIGTERM/SIGHUP에서 돌지 않는다. 그렇게 죽은 뒤 운영자가
    경로만 지우면 등록이 남는데, 종전 구현은 `_path_exists`가 False라는 이유로 그냥
    돌아가서 그 상태를 **영구히** 남겼다. 그러면 같은 경로의 다음 `worktree add`가
    "missing but already registered"로 죽는다(적대 리뷰 #1 실측).
    """

    destination = pinned.run_parent / "pinvi-run"
    pinned.call(
        materialize_disposable_run_worktree,
        expected_tree=pinned.tree,
        destination=destination,
    )
    # 비정상 종료 뒤 운영자가 경로만 지운 상태를 그대로 만든다.
    shutil.rmtree(destination)
    assert str(destination) in _git("--git-dir", str(pinned.bare), "worktree", "list")

    pinned.call(remove_disposable_run_worktree, destination=destination)
    assert str(destination) not in _git("--git-dir", str(pinned.bare), "worktree", "list")
    # 등록이 사라졌으므로 같은 경로를 다시 쓸 수 있다.
    pinned.call(
        materialize_disposable_run_worktree,
        expected_tree=pinned.tree,
        destination=destination,
    )


def test_removal_of_an_unknown_path_is_not_an_error(pinned: Any) -> None:
    """등록도 경로도 없으면 성공이다 — 이미 원하는 상태이기 때문이다."""

    pinned.call(remove_disposable_run_worktree, destination=pinned.run_parent / "never")


# -------------------------------------------------------------------- summary


def test_the_summary_counts_ignored_residue_before_removal(pinned: Any) -> None:
    """삭제 전에 **무엇이 남았는지**를 증거로 만든다.

    봉인 트리를 실행에서 뺀 뒤로는 gitignore 경로 쓰기를 관측하던 유일한 탐지기(다음
    preflight의 모드 검사)가 사라진다. 여기서 세지 않으면 증거 없이 삭제된다
    (적대 리뷰 #3). `--untracked-files=all`만으로는 `node_modules/`가 보이지 않는다.
    """

    destination = pinned.run_parent / "pinvi-run"
    pinned.call(
        materialize_disposable_run_worktree,
        expected_tree=pinned.tree,
        destination=destination,
    )
    (destination / "node_modules").mkdir()
    (destination / "node_modules" / "x.txt").write_text("y", encoding="utf-8")
    (destination / "untracked.txt").write_text("z", encoding="utf-8")

    summary = summarize_disposable_run_worktree(destination=destination)
    assert summary["ignored_entries"] >= 1
    assert summary["untracked_entries"] >= 1
    assert "node_modules" in summary["top_level_names"]
    # 호스트 경로는 담지 않는다 — repo-상대 최상위 이름만 남긴다.
    assert all("/" not in name for name in summary["top_level_names"])


# -------------------------------------------------------------- 사후조건 봉인


def test_the_seal_postcondition_accepts_an_intact_seal(pinned: Any) -> None:
    pinned.call(assert_pinned_worktree_is_still_sealed)


def test_the_seal_postcondition_rejects_a_broken_seal(pinned: Any) -> None:
    """실행이 봉인 트리의 모드를 바꿨다면 사후조건이 그 자리에서 잡아야 한다.

    이 검사가 없으면 다음 실행의 preflight에서야 드러나고, 그때는 이미 한 사이클을
    태운 뒤다.
    """

    victim = pinned.sealed / "apps/web/app.txt"
    os.chmod(victim, 0o644)
    with pytest.raises(DeploymentContractError, match="unsafe"):
        pinned.call(assert_pinned_worktree_is_still_sealed)


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() != 0,
    reason="이 케이스의 요점은 **root가 모드를 무시한다**는 것이다 — 비-root로는 재현되지 않는다",
)
def test_writing_into_the_sealed_tree_breaks_the_seal(pinned: Any) -> None:
    """이 게이트가 지키는 사실 자체 — 봉인 트리에 쓰면 다음 검사가 거부한다.

    하네스는 root로 돌므로 0555 디렉터리 안에도 쓴다(CAP_DAC_OVERRIDE). 그것이
    2026-09-03·04에 같은 pinset 재실행을 막은 잔여물을 만들었다.
    """

    _validate_immutable_tree(pinned.sealed)  # 지금은 통과한다
    residue = pinned.sealed / "node_modules"
    residue.mkdir(mode=0o755)
    try:
        with pytest.raises(DeploymentContractError, match="unsafe"):
            _validate_immutable_tree(pinned.sealed)
    finally:
        residue.rmdir()


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
