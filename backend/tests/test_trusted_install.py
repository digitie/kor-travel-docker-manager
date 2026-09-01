"""GM-09: 신뢰 경로·글로벌 락 상수의 단일 정본화 회귀.

이 파일이 지키는 것은 하나다 — cli.py와 c6c_deployment.py가 **같은** global
mutation lock 경로·FD env 상수를 참조해야 pinned rebuild와 pin 회전이 실제로
서로를 직렬화한다. 상수가 각자 리터럴로 존재하던 시절에는 한쪽만 바뀌어도 아무
테스트도 실패하지 않았다(lock 부재가 개발 환경의 정상 통과 경로였기 때문) — 이
파일은 그 조용한 drift를 못 일어나게 막는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kor_travel_docker_manager import cli
from kor_travel_docker_manager.services import (
    c6c_deployment,
    pinned_runtime_generation,
    runtime_execution_registry,
    runtime_pair_rotation,
    runtime_pin_registry,
    runtime_pin_request,
)
from kor_travel_docker_manager.services import trusted_install as trusted_install_module
from kor_travel_docker_manager.services.trusted_install import (
    GLOBAL_MUTATION_LOCK_FD_ENV,
    GLOBAL_MUTATION_LOCK_PATH,
    TRUSTED_INSTALL_ROOT,
    TRUSTED_PUBLIC_ROOT,
    TRUSTED_REQUEST_ROOT,
    TRUSTED_STATE_ROOT,
    running_from_trusted_install_root,
)


def test_cli_and_c6c_reference_the_identical_lock_path_object() -> None:
    """리터럴 두 벌이던 시절의 핵심 위험 — 같은 객체가 아니면 값이 갈릴 수 있다."""

    assert cli._GLOBAL_MUTATION_LOCK_PATH is GLOBAL_MUTATION_LOCK_PATH
    assert c6c_deployment._C6C_GLOBAL_MUTATION_LOCK is GLOBAL_MUTATION_LOCK_PATH
    assert cli._GLOBAL_MUTATION_LOCK_PATH is c6c_deployment._C6C_GLOBAL_MUTATION_LOCK


def test_cli_and_c6c_reference_the_identical_lock_fd_env_name() -> None:
    assert cli._INHERITED_GLOBAL_MUTATION_LOCK_FD_ENV is GLOBAL_MUTATION_LOCK_FD_ENV
    assert (
        c6c_deployment._PINNED_REBUILD_INHERITED_GLOBAL_LOCK_FD_ENV
        is GLOBAL_MUTATION_LOCK_FD_ENV
    )


@pytest.mark.parametrize(
    "module, attr, expected",
    [
        (runtime_pin_registry, "_TRUSTED_INSTALL_ROOT", TRUSTED_INSTALL_ROOT),
        (runtime_pin_registry, "_TRUSTED_STATE_ROOT", TRUSTED_STATE_ROOT),
        (runtime_pin_registry, "_TRUSTED_PUBLIC_ROOT", TRUSTED_PUBLIC_ROOT),
        (runtime_pin_request, "_TRUSTED_INSTALL_ROOT", TRUSTED_INSTALL_ROOT),
        (runtime_pin_request, "_TRUSTED_REQUEST_ROOT", TRUSTED_REQUEST_ROOT),
        (runtime_execution_registry, "_TRUSTED_INSTALL_ROOT", TRUSTED_INSTALL_ROOT),
        (runtime_execution_registry, "_TRUSTED_STATE_ROOT", TRUSTED_STATE_ROOT),
        (runtime_execution_registry, "_TRUSTED_PUBLIC_ROOT", TRUSTED_PUBLIC_ROOT),
        (runtime_pair_rotation, "_TRUSTED_STATE_ROOT", TRUSTED_STATE_ROOT),
    ],
)
def test_every_consumer_shares_the_canonical_trusted_root_constant(
    module: object, attr: str, expected: Path
) -> None:
    assert getattr(module, attr) is expected


def test_running_from_trusted_install_root_falls_back_to_project_root_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """개발 checkout(이 저장소 테스트 실행 환경)에서는 세 조건 모두 거짓이어야 한다.

    `__file__`은 `/opt/...` 아래가 아니고, `sys.prefix`도 그 venv가 아니며,
    `get_project_root()`도 이 저장소 checkout을 가리킨다 — trusted root와 다르다.
    """

    assert running_from_trusted_install_root() is False


def test_running_from_trusted_install_root_is_true_when_sys_prefix_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        trusted_install_module.sys,
        "prefix",
        str(TRUSTED_INSTALL_ROOT / "backend" / ".venv"),
    )

    assert running_from_trusted_install_root() is True


def test_running_from_trusted_install_root_is_true_when_project_root_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", str(TRUSTED_INSTALL_ROOT)
    )

    assert running_from_trusted_install_root() is True


def test_runtime_pin_request_no_longer_false_negatives_on_wheel_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GM-09가 고친 실제 버그: 이전에는 runtime_pin_request.py만
    get_project_root() 비교 하나였다 — wheel 직접 실행(entrypoint의 project-root
    env 없이 sys.prefix만 설치 venv인 상태)에서 이 모듈만 "trusted root 아님"으로
    오판해, backend가 쓴 요청 파일을 root CLI가 다른 경로에서 찾는 latent
    불일치를 냈다. 이제는 공유 판정을 쓰므로 그 케이스도 잡는다.
    """

    monkeypatch.delenv(runtime_pin_request.RUNTIME_PIN_REQUEST_FILE_ENV, raising=False)
    monkeypatch.setattr(
        trusted_install_module.sys,
        "prefix",
        str(TRUSTED_INSTALL_ROOT / "backend" / ".venv"),
    )

    assert runtime_pin_request._running_from_trusted_install_root() is True
    assert runtime_pin_request.runtime_pin_request_path() == (
        TRUSTED_REQUEST_ROOT / "runtime-pin-requests.json"
    )


def test_pinned_runtime_generation_still_recognizes_its_own_file_relative_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예전 구현(`__file__` 상대경로만)이 잡던 경우를 통합 판정이 계속 잡는지
    확인한다 — union 결합이라 기존 참 조건을 잃으면 안 된다."""

    monkeypatch.setattr(
        trusted_install_module.sys,
        "prefix",
        str(TRUSTED_INSTALL_ROOT / "backend" / ".venv"),
    )

    assert pinned_runtime_generation._running_from_trusted_install_root() is True


# --- launcher script 텍스트 대 상수 동일성 (검증 노트 (b)) ----------------------
#
# 이 launcher들은 검증 전 프로젝트 코드를 import하지 않으려 `python3 -I -S`로
# 격리 실행한다 — 그래서 trusted_install 모듈을 import할 수 없고, 리터럴을 각자
# 다시 적을 수밖에 없다. import로 통일할 수 없으니, 텍스트가 상수와 여전히
# 같은지를 이 테스트가 대신 지킨다.

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
_LOCK_PARENT_DIR = str(GLOBAL_MUTATION_LOCK_PATH.parent)
_LOCK_BASENAME = GLOBAL_MUTATION_LOCK_PATH.name


@pytest.mark.parametrize(
    "script_name",
    ["run-pinned-rebuild-once", "run-m05-isolated-e2e-once", "install-ktdm-trusted-release"],
)
def test_launcher_script_lock_path_literal_matches_the_shared_constant(
    script_name: str,
) -> None:
    text = (_SCRIPTS_DIR / script_name).read_text(encoding="utf-8")
    assert _LOCK_PARENT_DIR in text, (
        f"{script_name}: lock 디렉터리 리터럴이 GLOBAL_MUTATION_LOCK_PATH.parent"
        f"({_LOCK_PARENT_DIR})와 어긋났다 — pinned rebuild와 pin 회전이 서로 다른"
        " 파일을 잠글 수 있다."
    )
    assert _LOCK_BASENAME in text, (
        f"{script_name}: lock 파일명 리터럴이 GLOBAL_MUTATION_LOCK_PATH.name"
        f"({_LOCK_BASENAME})와 어긋났다."
    )


@pytest.mark.parametrize(
    "script_name",
    ["run-pinned-rebuild-once", "run-m05-isolated-e2e-once"],
)
def test_launcher_script_lock_fd_env_literal_matches_the_shared_constant(
    script_name: str,
) -> None:
    text = (_SCRIPTS_DIR / script_name).read_text(encoding="utf-8")
    assert GLOBAL_MUTATION_LOCK_FD_ENV in text, (
        f"{script_name}: lock fd env 이름 리터럴이 GLOBAL_MUTATION_LOCK_FD_ENV"
        f"({GLOBAL_MUTATION_LOCK_FD_ENV})와 어긋났다 — CLI가 상속을 못 받아 직접"
        " 열기로 떨어진다(시끄러운 실패지만, 여전히 하나의 정본이어야 한다)."
    )
