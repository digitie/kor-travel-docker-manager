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

ADR-51 C-2(rehearsal이 같은 lock에 합류):

- (e) rehearsal `.env`로 도는 Compose mutator 입구·legacy stage/retire·UI 컨테이너 조작·
  관리자 비밀번호 변경이 G 보유 중에는 전부 거절되고(API는 409
  ``MANAGER_MUTATION_ACTIVE``), Docker SDK도 `.env`도 건드리지 않는다.
- (f) lock 경로 유도표: production·rehearsal은 G, local은 ``$HOME`` 개발 lock, override는
  local에서만.
- (g) 재구축은 실제 파일 lock을 G와 P 두 개만 잡는다 — 세 번째 획득은 G key 재진입이다.

lock 경로는 conftest가 테스트마다 자기 소유 ``0700`` tmp 디렉터리로 옮겨 둔다. 자식
프로세스는 그 monkeypatch를 물려받지 못하므로 경로와 소유자 seam을 스스로 설정한다.
"""

from __future__ import annotations

import sys

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("flock·POSIX 소유권 계약은 Linux에서만 검증한다", allow_module_level=True)

import datetime
import fcntl
import os
import re
import signal
import stat
import subprocess
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from dotenv import dotenv_values
from fastapi.testclient import TestClient

from kor_travel_docker_manager import cli as cli_module
from kor_travel_docker_manager.api import admin as admin_api
from kor_travel_docker_manager.main import app
from kor_travel_docker_manager.services import c6c_deployment, legacy_override_retirement
from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services import docker_service as docker_service_module
from kor_travel_docker_manager.services.admin_password_service import ADMIN_PASSWORD_HASH_ENV
from kor_travel_docker_manager.services.auth_service import (
    AdminSessionContext,
    hash_password_for_env,
    require_admin_session,
)
from kor_travel_docker_manager.services.errors import (
    DeploymentContractError,
    ManagerMutationActiveError,
)
from kor_travel_docker_manager.services.legacy_override_retirement import (
    LegacyOverrideRetirementError,
)
from kor_travel_docker_manager.services.registry import (
    MANAGED_CONTAINERS,
    external_project_for_container,
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


# --- ADR-51 C-2: rehearsal이 같은 lock에 합류한다 ------------------------------------

_REHEARSAL_VALUES = {
    "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
    "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
    "PINVI_ENVIRONMENT": "production",
    "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
}
# 실제 target이고 `external_project`가 없다 — Manager 자신의 Compose 프로젝트 소유라
# lock을 지난다. 형제 프로젝트 컨테이너(airport)는 lock 없이 SDK로 가므로 쓰면 안 된다.
_MANAGER_OWNED_TARGET = "kor-travel-shared-postgresql"
_CURRENT_PASSWORD = "current-password-1234"


@pytest.fixture
def rehearsal_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """n150과 같은 모양의 rehearsal `.env`를 Manager env-file로 지정한다.

    lock 경로 유도가 보는 이름은 전부 파일에 둔다 — 파일에 없으면 호출부가 프로세스
    환경으로 채우므로(`_c6c_lock_path_from_values`) 러너의 환경이 판정에 새어 든다.
    ``HOME``도 옮겨, ``$HOME`` 개발 lock으로 새면 흔적이 tmp에 남아 단언이 잡는다.
    """

    project = tmp_path / "project"
    project.mkdir()
    project.chmod(0o755)
    values = {
        **_REHEARSAL_VALUES,
        "COMPOSE_PROJECT_NAME": "ktdm-c2-contention",
        "KTDM_C6C_STATE_ROOT": str((tmp_path / "state").resolve()),
    }
    env_path = project / ".env"
    env_path.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()), encoding="utf-8"
    )
    env_path.chmod(0o600)
    compose_path = project / "docker-compose.yml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    compose_path.chmod(0o644)
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", str(env_path))
    for name in ("KTDM_C6C_DEPLOYMENT_LOCK", "KTDM_C6C_COMPATIBLE_PAIR_MANIFEST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return env_path


def _admin_session() -> AdminSessionContext:
    return AdminSessionContext(
        username="admin",
        session_id_hash="adr-51-c2-contention",
        expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
    )


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """세션·감사 DB는 이 검사의 대상이 아니다 — 인증 의존성만 대역으로 바꾼다."""

    monkeypatch.setitem(app.dependency_overrides, require_admin_session, _admin_session)
    return TestClient(app)


def test_e_rehearsal_compose_mutators_and_legacy_retirement_are_refused_while_g_is_held(
    rehearsal_env: Path,
    tmp_path: Path,
) -> None:
    """rehearsal `.env`의 lock 경로가 G라 UI·CLI Compose mutator가 경합에서 거절된다.

    `c6c_deployment_lock_from_environment()`는 docker_service의 네 mutator(save_compose·
    control·update·reset)와 compose_service의 run·candidate capture·ensure가 공유하는
    한 입구다. 종전에는 이 경로가 실행 사용자 ``$HOME`` lock이어서 G 보유자와 무관했다.
    """

    project = rehearsal_env.parent
    with _launcher_style_holder(_global_lock_path()):
        with pytest.raises(ManagerMutationActiveError) as refused:
            with compose_service_module.c6c_deployment_lock_from_environment():
                pytest.fail("보유 중인 G 안으로 들어가면 안 된다")
        assert refused.value.code == "MANAGER_MUTATION_ACTIVE"

        selected = legacy_override_retirement._select_lock_path(
            _REHEARSAL_VALUES,
            project_root=Path("/irrelevant"),
            lock_path=None,
            require_root=True,
        )
        assert selected == str(_global_lock_path())
        # 같은 선택으로 stage에 들어가면 source를 읽기도 전에 lock에서 거절된다.
        source = tmp_path / "legacy" / "kor-travel-docker-manager" / "docker-compose.override.yml"
        with pytest.raises(
            LegacyOverrideRetirementError,
            match=f"^cannot acquire the Manager mutation lock: {re.escape(_BUSY)}$",
        ):
            legacy_override_retirement.stage_legacy_compose_override(
                source_path=source,
                project_root=project,
                lock_path=selected,
                require_root=False,
            )
        assert not (project / ".legacy-compose-override-state").exists()

    assert not (tmp_path / "home").exists()


def test_e_the_container_action_api_answers_409_without_touching_docker(
    rehearsal_env: Path,
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _MANAGER_OWNED_TARGET in MANAGED_CONTAINERS
    assert external_project_for_container(_MANAGER_OWNED_TARGET) is None
    sdk = Mock(name="docker SDK client")
    monkeypatch.setattr(docker_service_module.DockerService, "_get_client", sdk)

    with _launcher_style_holder(_global_lock_path()):
        response = api_client.post(
            f"/api/v1/containers/{_MANAGER_OWNED_TARGET}/action",
            json={"action": "restart"},
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {"code": "MANAGER_MUTATION_ACTIVE", "message": _BUSY}
    sdk.assert_not_called()


def test_e_the_admin_password_api_answers_409_and_leaves_the_env_bytes(
    rehearsal_env: Path,
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """legacy retire와 같은 lock이다 — 둘이 겹치면 한쪽의 `.env` 재작성이 사라졌다."""

    current_hash = hash_password_for_env(_CURRENT_PASSWORD)
    monkeypatch.setenv("KTDM_ADMIN_USERNAME", "admin")
    monkeypatch.setenv(ADMIN_PASSWORD_HASH_ENV, current_hash)
    monkeypatch.setenv("KTDM_SESSION_SECRET", "test-session-secret-minimum-32-bytes-value")
    monkeypatch.setattr(admin_api, "check_login_rate_limit", lambda _request: None)
    audit = Mock()
    monkeypatch.setattr(admin_api, "record_login_audit_event", audit)
    before = rehearsal_env.read_bytes()

    with _launcher_style_holder(_global_lock_path()):
        response = api_client.post(
            "/api/v1/admin/password",
            json={
                "current_password": _CURRENT_PASSWORD,
                "new_password": "brand-new-password-5678",
            },
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {"code": "MANAGER_MUTATION_ACTIVE", "message": _BUSY}
    assert rehearsal_env.read_bytes() == before
    assert os.environ[ADMIN_PASSWORD_HASH_ENV] == current_hash
    # 자격증명 추측이 아니다 — 로그인 실패 카운터(`event_type="login"`)에 합류하지 않는다.
    assert all(call.kwargs.get("event_type") != "login" for call in audit.call_args_list)


@pytest.mark.parametrize(
    ("mode", "with_override", "expected"),
    [
        ("production", False, "global"),
        ("rehearsal", False, "global"),
        ("local", False, "home"),
        # 모드 미지정은 아직 개발 기본값이다. C-3이 G로 뒤집는다(fail closed).
        ("", False, "home"),
        ("local", True, "override"),
        ("rehearsal", True, "refused"),
        ("production", True, "refused"),
    ],
)
def test_f_the_manager_mutation_lock_path_derivation(
    mode: str,
    with_override: bool,
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    override = (tmp_path / "override" / "dev.lock").resolve()
    values = {"KTDM_DEPLOYMENT_ENVIRONMENT": mode}
    if with_override:
        values["KTDM_C6C_DEPLOYMENT_LOCK"] = str(override)

    if expected == "refused":
        with pytest.raises(DeploymentContractError, match="lock path is fixed"):
            c6c_deployment.c6c_global_mutation_lock_path(values)
        return
    expected_path = {
        "global": str(_global_lock_path()),
        "home": str(
            (
                home / ".local" / "state" / "kor-travel-docker-manager" / "global-mutation.lock"
            ).resolve(strict=False)
        ),
        "override": str(override),
    }[expected]
    assert c6c_deployment.c6c_global_mutation_lock_path(values) == expected_path


def test_g_the_rebuild_takes_exactly_g_then_p_and_no_third_lock(
    rehearsal_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실제 `_pinned_runtime_rebuild_environment_lock`이 여는 파일 lock의 목록.

    종전 rehearsal에서는 세 번째 획득이 ``$HOME`` lock이라, UI 요청이 그 lock을 잠깐 잡은
    순간 G·P를 쥔 재구축이 거기서 실패했다. 이제 세 번째 획득은 이미 잡은 G key의 재진입
    no-op이다. 환경 snapshot 캡처와 token 검증만 대역이고 lifecycle 게이트는 실제다.
    """

    global_lock = _global_lock_path()
    pinned_lease = global_lock.parent / "pinned-runtime-rebuild.lock"
    monkeypatch.setattr(c6c_deployment, "_PINNED_RUNTIME_REBUILD_LOCK", pinned_lease)
    monkeypatch.setattr(c6c_deployment, "_require_pinned_runtime_rebuild_root", lambda: None)
    effective = {
        name: value for name, value in dotenv_values(rehearsal_env).items() if value is not None
    }
    snapshot = compose_service_module.ComposeEnvironmentSnapshot(
        effective=effective,
        env_path=str(rehearsal_env),
        compose_path=str(rehearsal_env.parent / "docker-compose.yml"),
        override_path=str(rehearsal_env.parent / "docker-compose.override.yml"),
        env_file_identity=compose_service_module._env_file_identity(rehearsal_env),
        env_file_bytes=rehearsal_env.read_bytes(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_pinned_runtime_rebuild_environment_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(
        compose_service_module,
        "validate_c6c_operation_tokens",
        lambda _values, *, require_nonempty: None,
    )
    admission = Mock(return_value=None)

    real_flock = fcntl.flock
    flocks: list[tuple[str, int]] = []

    def recording_flock(fd: int, operation: int) -> None:
        flocks.append((os.readlink(f"/proc/self/fd/{fd}"), operation))
        real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", recording_flock)
    global_path = str(global_lock.resolve())
    pinned_path = str(pinned_lease.resolve())

    with compose_service_module._pinned_runtime_rebuild_environment_lock(
        prewrite_admission=admission
    ) as (lock_snapshot, environment_snapshot, _credentials_initialized):
        assert environment_snapshot is snapshot
        # 세 번째 획득이 고른 경로는 G다 — 이미 잡은 key라 파일을 다시 열지 않았다.
        assert lock_snapshot.lock_path == str(global_lock)
        exclusive = [path for path, operation in flocks if operation & fcntl.LOCK_EX]
        assert exclusive == [global_path, pinned_path]
        assert _probe(global_lock) == "busy"
        assert _probe(pinned_lease) == "busy"

    admission.assert_called_once_with(snapshot)
    assert {path for path, _operation in flocks} == {global_path, pinned_path}
    assert not (tmp_path / "home").exists()
    assert _probe(global_lock) == "free"
    assert _probe(pinned_lease) == "free"
