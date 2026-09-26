"""ADR-51 C-1: host 변경 lock ``G``는 획득 경로가 하나이고 실제 프로세스 사이에서 배제한다.

같은 프로세스 안의 두 번째 fd로 경합을 흉내 내면 "프로세스 경계에서 정말 막히는가"를
보지 못한다. 그래서 여기서는 **실제 자식 프로세스**가 launcher와 같은 방식(raw
``os.open`` + ``flock``, ``python3 -I -S``라 backend를 import하지 않는다)으로 G를 잡는다.
Docker는 쓰지 않는다.

- (a) 보유 중이면 CLI pin mutator·``manager_mutation_lock()``·pinned rebuild lease가
  전부 기다리지 않고 거절되고, 거절 전에 아무것도 부르지 않는다.
- (b) 보유자가 SIGKILL로 죽으면 곧바로 다시 잡힌다 — stale lock이 없다.
- (c) 부팅 직후처럼 lock 파일(또는 디렉터리)이 없으면 처음 온 획득자가 ``0600``으로
  만들어 잡는다 — lock 없이 진행하는 분기가 없다.
- (d) launcher가 물려준 fd는 terminal block 정책(`allow_inherited_terminal_block`)으로만
  쓰이고, fd를 갖지 않은 형제는 거절된다.

lock 경로는 conftest가 테스트마다 자기 소유 ``0700`` tmp 디렉터리로 옮겨 둔다. 자식
프로세스는 그 monkeypatch를 물려받지 못하므로 경로와 소유자 seam을 스스로 설정한다.
"""

from __future__ import annotations

import sys

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("flock·POSIX 소유권 계약은 Linux에서만 검증한다", allow_module_level=True)

import os
import signal
import stat
import subprocess
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from kor_travel_docker_manager import cli as cli_module
from kor_travel_docker_manager.services import c6c_deployment
from kor_travel_docker_manager.services.errors import (
    DeploymentContractError,
    ManagerMutationActiveError,
)
from kor_travel_docker_manager.services.runtime_pair_rotation import (
    RUNTIME_PAIR_ROTATION_FILE_ENV,
)
from kor_travel_docker_manager.services.trusted_install import GLOBAL_MUTATION_LOCK_FD_ENV

_BUSY = "another Manager mutation is already active; nothing was changed"
# n150은 디스크 대기로 프로세스 기동이 수십 초 걸린 실측이 있다. 멈춤 감지용으로만 쓴다.
_CHILD_TIMEOUT_SECONDS = 300

# launcher(`run-pinned-rebuild-once` 등)가 G를 잡는 방식 그대로다.
_LAUNCHER_STYLE_HOLDER = textwrap.dedent(
    """
    import fcntl
    import os
    import sys

    fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("held", flush=True)
    sys.stdin.read()
    """
)

_NONBLOCKING_PROBE = textwrap.dedent(
    """
    import fcntl
    import os
    import sys

    fd = os.open(sys.argv[1], os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("busy")
    else:
        print("free")
    """
)

# 자식 프로세스가 conftest의 tmp 격리를 스스로 재현하는 머리말.
_BACKEND_PRELUDE = textwrap.dedent(
    """
    import os
    import sys
    from pathlib import Path

    from kor_travel_docker_manager import cli
    from kor_travel_docker_manager.services import c6c_deployment
    from kor_travel_docker_manager.services.errors import DeploymentContractError

    c6c_deployment._C6C_GLOBAL_MUTATION_LOCK = Path(sys.argv[1])
    c6c_deployment._GLOBAL_LOCK_OWNER_UID = os.geteuid()
    """
)

# launcher의 terminal fallback(`pin block-execution`)처럼 상속 fd로 들어간다.
_INHERITING_CHILD = _BACKEND_PRELUDE + textwrap.dedent(
    """
    import fcntl

    with cli._runtime_pin_mutation_lock(allow_inherited_terminal_block=True):
        print("allowed", flush=True)
        contender = os.open(sys.argv[1], os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("still-exclusive", flush=True)
        finally:
            os.close(contender)
    try:
        with cli._runtime_pin_mutation_lock():
            print("entered-without-allow", flush=True)
    except DeploymentContractError as exc:
        print(f"refused: {exc}", flush=True)
    """
)

# fd를 받지 않은 형제. env 번호만 흉내 내도, env 없이 정면으로 와도 거절돼야 한다.
_SIBLING_WITHOUT_FD = _BACKEND_PRELUDE + textwrap.dedent(
    """
    try:
        with cli._runtime_pin_mutation_lock(allow_inherited_terminal_block=True):
            print("forged-entered", flush=True)
    except DeploymentContractError as exc:
        print(f"forged-refused: {exc}", flush=True)
    os.environ.pop(sys.argv[2], None)
    try:
        with cli._runtime_pin_mutation_lock():
            print("plain-entered", flush=True)
    except DeploymentContractError as exc:
        print(f"plain-refused: {getattr(exc, 'code', None)}: {exc}", flush=True)
    """
)

# G를 잡은 채 fd를 물려준 자식과 물려주지 않은 형제를 차례로 띄운다.
_INHERITING_HOLDER = textwrap.dedent(
    """
    import fcntl
    import os
    import subprocess
    import sys

    path, fd_env, inheriting, sibling, timeout = sys.argv[1:6]
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    env = dict(os.environ)
    env[fd_env] = str(fd)
    for label, code, fds in (("child", inheriting, (fd,)), ("sibling", sibling, ())):
        result = subprocess.run(
            [sys.executable, "-c", code, path, fd_env],
            pass_fds=fds,
            env=env,
            capture_output=True,
            text=True,
            timeout=float(timeout),
        )
        for line in result.stdout.splitlines():
            print(f"{label}|{line}", flush=True)
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            raise SystemExit(f"{label} exited {result.returncode}")
    """
)


@pytest.fixture(autouse=True)
def _no_inherited_descriptor_from_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytest를 launcher 안에서 띄웠더라도 이 모듈의 판정이 그 fd에 기대지 않게 한다.
    monkeypatch.delenv(GLOBAL_MUTATION_LOCK_FD_ENV, raising=False)


def _global_lock_path() -> Path:
    return c6c_deployment._C6C_GLOBAL_MUTATION_LOCK


def _child_environment(tmp_path: Path) -> dict[str, str]:
    """자식이 같은 backend 코드를 import하고 root 전용 경로를 건드리지 않게 한다."""

    environment = dict(os.environ)
    source_root = str(Path(cli_module.__file__).resolve().parents[1])
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        f"{source_root}{os.pathsep}{existing}" if existing else source_root
    )
    environment[RUNTIME_PAIR_ROTATION_FILE_ENV] = str(tmp_path / "runtime-pair-rotation.json")
    environment.pop(GLOBAL_MUTATION_LOCK_FD_ENV, None)
    return environment


@contextmanager
def _launcher_style_holder(path: Path) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", _LAUNCHER_STYLE_HOLDER, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        # 자식이 죽으면 stdout이 닫혀 빈 줄이 온다 — 무한 대기하지 않는다.
        assert process.stdout.readline().strip() == "held"
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=_CHILD_TIMEOUT_SECONDS)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


def _probe(path: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", _NONBLOCKING_PROBE, str(path)],
        capture_output=True,
        text=True,
        timeout=_CHILD_TIMEOUT_SECONDS,
        check=True,
    )
    return result.stdout.strip()


def test_a_launcher_style_holder_refuses_every_backend_acquirer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned_lease = tmp_path / "pinned-runtime-rebuild.lock"
    monkeypatch.setattr(c6c_deployment, "_PINNED_RUNTIME_REBUILD_LOCK", pinned_lease)
    monkeypatch.setattr(c6c_deployment, "_require_pinned_runtime_rebuild_root", lambda: None)

    with _launcher_style_holder(_global_lock_path()):
        with (
            patch.object(cli_module, "_running_as_root", return_value=True),
            patch.object(cli_module, "load_pending_runtime_pair_rotation") as pending,
            patch.object(cli_module, "rotate_runtime_pin_pair") as rotate_pair,
            patch.object(cli_module, "rotate_pair_with_execution") as rotate_with_execution,
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
                        "contention",
                        "--confirm",
                    ]
                )
                == 2
            )
        pending.assert_not_called()
        rotate_pair.assert_not_called()
        rotate_with_execution.assert_not_called()
        assert _BUSY in capsys.readouterr().err

        with (
            patch.object(cli_module, "_running_as_root", return_value=True),
            patch.object(cli_module, "block_runtime_pinset") as block,
        ):
            assert (
                cli_module.main(
                    ["pin", "block", "c" * 64, "--reason", "contention", "--confirm"]
                )
                == 2
            )
        block.assert_not_called()
        assert _BUSY in capsys.readouterr().err

        with pytest.raises(ManagerMutationActiveError) as refused:
            with c6c_deployment.manager_mutation_lock():
                pytest.fail("보유 중인 G 안으로 들어가면 안 된다")
        assert refused.value.code == "MANAGER_MUTATION_ACTIVE"
        assert str(refused.value) == _BUSY

        with pytest.raises(ManagerMutationActiveError):
            with c6c_deployment.pinned_runtime_rebuild_lock():
                pytest.fail("G를 못 잡은 rebuild가 P로 넘어가면 안 된다")
        # G → P 순서: G에서 거절됐으므로 P는 열리지도 않았다.
        assert not pinned_lease.exists()


def test_b_a_killed_holder_leaves_no_stale_lock() -> None:
    lock_path = _global_lock_path()
    with _launcher_style_holder(lock_path) as holder:
        with pytest.raises(ManagerMutationActiveError):
            with c6c_deployment.manager_mutation_lock():
                pytest.fail("보유 중인 G 안으로 들어가면 안 된다")
        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=_CHILD_TIMEOUT_SECONDS)
        assert holder.returncode == -signal.SIGKILL

        # 정리 절차 없이 곧바로 잡힌다. flock은 보유 프로세스와 함께 사라진다.
        with c6c_deployment.manager_mutation_lock():
            assert _probe(lock_path) == "busy"
    assert _probe(lock_path) == "free"


@pytest.mark.parametrize("missing", ["file", "directory"])
def test_c_the_first_acquirer_creates_the_lock_and_excludes_other_processes(
    missing: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(RUNTIME_PAIR_ROTATION_FILE_ENV, str(tmp_path / "runtime-pair-rotation.json"))
    if missing == "directory":
        # tmpfiles가 돌지 않은 호스트. `_prepare_c6c_lock_directory`의 폴백이 만든다.
        fresh = _global_lock_path().parent / "fresh-boot"
        monkeypatch.setattr(
            c6c_deployment, "_C6C_GLOBAL_MUTATION_LOCK", fresh / "global-mutation.lock"
        )
    lock_path = _global_lock_path()
    assert not lock_path.exists()

    with cli_module._runtime_pin_mutation_lock():
        metadata = lock_path.lstat()
        assert stat.S_ISREG(metadata.st_mode)
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert metadata.st_nlink == 1
        assert metadata.st_uid == os.geteuid()
        directory = lock_path.parent.lstat()
        assert stat.S_IMODE(directory.st_mode) == 0o700
        assert _probe(lock_path) == "busy"

    assert _probe(lock_path) == "free"


def test_c_a_caller_that_is_not_the_lock_owner_is_refused_without_a_lock(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """운영 값(소유자 root)을 비root 실행자에게 재현한다.

    종전 CLI는 바로 이 자리(비root라 lock을 열 수 없음)에서 lock 없이 진행했다.
    이제는 lock 파일을 만들기도 전에 거절된다.
    """

    monkeypatch.setattr(c6c_deployment, "_GLOBAL_LOCK_OWNER_UID", os.geteuid() + 1)

    with patch.object(cli_module, "load_runtime_pin_registry") as load_registry:
        assert cli_module.main(["pin", "init", "--confirm"]) == 2
    load_registry.assert_not_called()
    assert "the Manager mutation lock requires root" in capsys.readouterr().err

    with pytest.raises(DeploymentContractError, match="the Manager mutation lock requires root"):
        with c6c_deployment.manager_mutation_lock():
            pytest.fail("소유자가 아닌 실행자가 G 안으로 들어가면 안 된다")
    assert not _global_lock_path().exists()


def test_d_only_the_terminal_block_policy_may_use_an_inherited_descriptor(
    tmp_path: Path,
) -> None:
    lock_path = _global_lock_path()
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            _INHERITING_HOLDER,
            str(lock_path),
            GLOBAL_MUTATION_LOCK_FD_ENV,
            _INHERITING_CHILD,
            _SIBLING_WITHOUT_FD,
            str(_CHILD_TIMEOUT_SECONDS),
        ],
        env=_child_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=3 * _CHILD_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()

    # 물려받은 fd로는 terminal block 정책일 때만 들어가고, 들어간 동안에도 배타적이다.
    assert lines[:3] == [
        "child|allowed",
        "child|still-exclusive",
        "child|refused: runtime pin mutation inherited lock is invalid",
    ]
    # fd가 없는 형제: env 번호를 흉내 내면 descriptor 검증에서, 정면으로 오면 경합으로
    # 거절된다. 흉내 낸 번호가 형제 안에서 다른 파일일 수도 있어 문구 끝은 보지 않는다.
    forged, plain = lines[3:]
    assert forged.startswith("sibling|forged-refused: inherited C6c deployment lock descriptor is ")
    assert plain == f"sibling|plain-refused: MANAGER_MUTATION_ACTIVE: {_BUSY}"

    # 보유자가 끝나면 G는 비어 있다.
    with c6c_deployment.manager_mutation_lock():
        pass
