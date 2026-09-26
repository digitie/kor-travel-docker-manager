"""신뢰 설치기(ADR-51 D, I-2) 계약 테스트.

설치기를 끝까지 돌리는 hermetic 테스트는 없다 — root·systemd·`/opt`가 필요하다. 최종 수락은
n150 절차(`docs/prod-deployment.md` §3)다. 여기서는 root 없이 확인할 수 있는 것만 본다:
문법, n150 헬퍼가 넘기는 인자의 호환, G lock 구간을 잘라 실제로 실행한 결과, 그리고 설치본의
실행 비트가 오는 유일한 자리인 git index.
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _ROOT / "scripts" / "install-ktdm-trusted-release"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_INSTALLER), *args], capture_output=True, text=True, check=False
    )


def test_installer_parses() -> None:
    subprocess.run(["bash", "-n", str(_INSTALLER)], check=True)


def test_the_helper_arguments_are_accepted() -> None:
    """n150 헬퍼는 `--restart-backend --expected-source-revision SHA CLONE`을 넘긴다.

    옛 옵션이 'unknown option'으로 죽으면 새 설치기의 첫 실행이 헬퍼에서 막힌다. 인자 검사는
    root 검사보다 먼저 하므로, 틀린 revision을 주면 실행 계정과 무관하게 그 자리에서 멈춘다.
    """

    completed = _run("--restart-backend", "--allow-live", "--expected-source-revision", "x", "/tmp")

    assert completed.returncode == 126
    assert "must be a full 40-hex revision" in completed.stderr


def test_an_option_without_its_value_is_refused() -> None:
    completed = _run("--expected-source-revision")

    assert completed.returncode == 126
    assert "--expected-source-revision requires a value" in completed.stderr


def test_retired_options_are_refused() -> None:
    completed = _run("--env-file", "/opt/kor-travel-docker-manager/.env")

    assert completed.returncode == 126
    assert "unknown option: --env-file" in completed.stderr


def test_help_describes_the_usage() -> None:
    completed = _run("--help")

    assert completed.returncode == 0
    assert "usage: install-ktdm-trusted-release" in completed.stdout


def _lock_block(lock_dir: Path) -> str:
    """설치기의 `# >>> G` ~ `# <<< G` 구간을 비-root로 돌릴 수 있게 잘라 낸다.

    바꾸는 것은 소유자 지정과 uid 기대값뿐이다. umask·mode·nlink·flock은 원문 그대로 남는다.
    """

    text = _INSTALLER.read_text(encoding="utf-8")
    block = text[text.index("# >>> G") : text.index("# <<< G")]
    assert "-o root -g root " in block and '"0:600:1"' in block
    block = block.replace("-o root -g root ", "").replace(
        '"0:600:1"', f'"{os.getuid()}:600:1"'
    )
    return (
        "set -euo pipefail\n"
        'die() { echo "$1" >&2; exit 126; }\n'
        f"LOCK_DIR={lock_dir}\n"
        'LOCK="${LOCK_DIR}/global-mutation.lock"\n'
        f"{block}\n"
        "echo HELD\n"
    )


def test_the_lock_is_created_private_under_a_permissive_umask(tmp_path: Path) -> None:
    """없던 G를 만들 때 0600이어야 한다 — 다른 획득자는 0600·nlink 1이 아니면 거부하므로,
    0644로 생기면 재부팅 전까지 Manager의 모든 mutation이 막힌다."""

    lock_dir = tmp_path / "lock"

    completed = subprocess.run(
        ["bash", "-c", "umask 002\n" + _lock_block(lock_dir)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "HELD"
    lock = lock_dir / "global-mutation.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert stat.S_IMODE(lock_dir.stat().st_mode) == 0o700


def test_a_second_mutation_is_refused_without_waiting(tmp_path: Path) -> None:
    lock_dir = tmp_path / "lock"
    lock_dir.mkdir(mode=0o700)
    lock = lock_dir / "global-mutation.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(lock, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = subprocess.run(
            ["bash", "-c", _lock_block(lock_dir)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    finally:
        os.close(descriptor)

    assert completed.returncode == 2
    assert "another manager mutation is already active" in completed.stderr


def test_root_launchers_are_executable_in_the_git_index() -> None:
    """설치기는 archive의 실행 비트를 그대로 쓴다(ADR-51 D) — git index가 유일한 정본이다.

    옛 설치기는 archive mode를 전부 0644로 눕힌 뒤 손으로 든 목록만 0755로 되돌렸고, 그 목록에서
    빠진 `rotate-pinned-pair`가 설치본에서 조용히 실행 불가가 된 적이 있다(2026-09-02).
    """

    listed = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-s", "--", "scripts"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    modes = {
        line.split("\t", 1)[1].rsplit("/", 1)[-1]: line.split(" ", 1)[0]
        for line in listed.splitlines()
        if line
    }

    for name in (
        "install-ktdm-trusted-release",
        "run-pinned-rebuild-once",
        "run-m05-isolated-e2e-once",
        "rotate-pinned-pair",
    ):
        assert modes.get(name) == "100755", name


def test_a_symlinked_lock_directory_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    lock_dir = tmp_path / "lock"
    lock_dir.symlink_to(real)

    completed = subprocess.run(
        ["bash", "-c", _lock_block(lock_dir)], capture_output=True, text=True, check=False
    )

    assert completed.returncode == 126
    assert "lock directory is a symlink" in completed.stderr


def test_a_planted_fifo_is_refused_instead_of_hanging(tmp_path: Path) -> None:
    """lock 자리에 FIFO가 있으면 `>>` 열기가 영원히 막힌다 — 열기 전에 거부해야 한다."""

    lock_dir = tmp_path / "lock"
    lock_dir.mkdir(mode=0o700)
    os.mkfifo(lock_dir / "global-mutation.lock")

    completed = subprocess.run(
        ["bash", "-c", _lock_block(lock_dir)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 126
    assert "not a regular file" in completed.stderr


def _extract_block() -> str:
    text = _INSTALLER.read_text(encoding="utf-8")
    return text[text.index("# >>> extract") : text.index("# <<< extract")]


def test_the_release_keeps_index_exec_bits_and_drops_group_other_write(tmp_path: Path) -> None:
    """release는 `git archive`를 그대로 풀고 group/other 쓰기만 지운다(ADR-51 D).

    `tar.umask=0`과 `umask 000`으로 archive의 world-writable mode가 그대로 풀리는 최악의 경우를
    만든다 — 옛 설치기가 모든 mode를 0644로 눕혔던 이유다. 설치기의 추출 구간을 그대로 돌린다.
    """

    release = tmp_path / "release"
    release.mkdir()
    script = (
        "set -euo pipefail\n"
        "umask 000\n"
        f'git_src() {{ git -C "{_ROOT}" -c tar.umask=0 "$@" scripts deploy; }}\n'
        "SHA=HEAD\n"
        f'REL="{release}"\n' + _extract_block()
    )

    subprocess.run(["bash", "-c", script], check=True)

    extracted = list(release.rglob("*"))
    assert len(extracted) > 10
    for path in extracted:
        assert not stat.S_IMODE(path.lstat().st_mode) & 0o022, path
    for name in (
        "install-ktdm-trusted-release",
        "run-pinned-rebuild-once",
        "run-m05-isolated-e2e-once",
        "rotate-pinned-pair",
    ):
        assert stat.S_IMODE((release / "scripts" / name).stat().st_mode) == 0o755, name
    unit = release / "deploy" / "systemd" / "ktdm-backend.service"
    assert stat.S_IMODE(unit.stat().st_mode) == 0o644
