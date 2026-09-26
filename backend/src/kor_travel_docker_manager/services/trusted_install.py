"""신뢰 설치 경로와 host-wide mutation lock의 단일 정본 (GM-09).

이 저장소 전체 mutation 직렬화는 다음 두 값이 모든 launcher·모듈에서 정확히
같아야만 성립한다:

- `GLOBAL_MUTATION_LOCK_PATH` — Manager의 유일한 host 변경 lock ``G``다(ADR-51 C-3에서
  pinned rebuild lease가 없어졌다). launcher·pinned rebuild·pin 회전·installer·backend
  mutator가 같은 파일을 잠가야 서로를 직렬화한다. 한쪽만 리터럴이 바뀌면 두 mutation이
  각자 다른 파일을 잠근 채 동시에 진행돼도 아무도 실패하지 않는다 — 모든 획득자가
  lock 파일이 없으면 만들어 잡기 때문에(ADR-51 C-1) 이 drift는 조용하다.
- `GLOBAL_MUTATION_LOCK_FD_ENV` — launcher가 미리 연 lock fd를 CLI에 물려줄 때
  쓰는 env 변수 이름. 이름이 어긋나면 CLI가 상속을 못 받아 직접 열기로 떨어지고,
  launcher가 이미 그 lock을 쥐고 있어 `BlockingIOError`로 fail-close한다 —
  이쪽은 시끄럽게 실패하므로 lock *경로* drift만큼 위험하지는 않지만, 여전히
  하나의 정본이 필요하다.

`TRUSTED_INSTALL_ROOT` 계열 경로 상수도 여기 모은다. `running_from_trusted_install_root`는
세 개 모듈이 각자 만들었던 서로 다른 판정(`__file__` 상대경로, `sys.prefix` 특례,
`get_project_root()` 비교)을 **전부 OR로 합친 것**이다 — 하나만 쓰면 특정 실행
형태(wheel 직접 실행, venv 위치, 개발 checkout)에서 그 모듈만 오탐(false negative)이
나 다른 경로를 본다. OR 결합은 기존에 참이던 조건을 하나도 잃지 않으면서, 그중
가장 좁았던 구현(단순 `get_project_root()` 비교만 하던 쪽)이 놓치던 wheel 실행
케이스를 함께 잡는다.

lock 경로·FD env 리터럴은 `scripts/run-pinned-rebuild-once`·
`scripts/run-m05-isolated-e2e-once`·`scripts/install-ktdm-trusted-release`에도
있다. 그 launcher들은 검증 전 프로젝트 코드를 import하지 않으려고 의도적으로
`python3 -I -S`로 격리 실행하므로 이 모듈을 import할 수 없다 — 대신
`tests/test_trusted_install.py`가 스크립트 텍스트와 이 모듈의 상수를 직접
비교해 drift를 잡는다.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Final

from kor_travel_docker_manager.services.errors import DeploymentContractError

TRUSTED_INSTALL_ROOT: Final = Path("/opt/kor-travel-docker-manager")
TRUSTED_STATE_ROOT: Final = Path("/var/lib/kor-travel-docker-manager")
TRUSTED_PUBLIC_ROOT: Final = Path("/var/lib/kor-travel-docker-manager-public")
TRUSTED_REQUEST_ROOT: Final = Path("/var/lib/kor-travel-docker-manager-requests")

GLOBAL_MUTATION_LOCK_PATH: Final = Path(
    "/run/lock/kor-travel-docker-manager/global-mutation.lock"
)
GLOBAL_MUTATION_LOCK_FD_ENV: Final = "KTDM_PINNED_REBUILD_GLOBAL_LOCK_FD"


def running_from_trusted_install_root() -> bool:
    """trusted installer가 통째 교체하는 canonical execution root에서 도는가.

    셋 중 하나라도 참이면 참이다:
    1. 이 모듈 자신의 `__file__`이 trusted root 아래에 있다 — wheel 설치가
       `TRUSTED_INSTALL_ROOT/backend/.venv/...`에 패키지를 두므로, venv가
       거기 있으면 이 모듈 파일도 거기 있다.
    2. `sys.prefix`가 정확히 `TRUSTED_INSTALL_ROOT/backend/.venv`다 — root
       launcher가 wheel 안의 Python을 `-I`로 직접 실행해 project-root env
       주입이 없는 경우를 잡는다.
    3. `registry.get_project_root()`가 trusted root와 같다 — 개발 checkout
       기준 4단계 상위 경로 규칙. wheel 설치에서는 site-packages 안쪽으로
       잘못 해석될 수 있어(1)·(2)가 이미 못 잡은 경우에만 최후 수단으로 쓴다.
    """

    try:
        if Path(__file__).resolve().is_relative_to(TRUSTED_INSTALL_ROOT.resolve()):
            return True
    except OSError:
        pass

    try:
        if Path(sys.prefix) == TRUSTED_INSTALL_ROOT / "backend" / ".venv":
            return True
    except OSError:
        pass

    try:
        from kor_travel_docker_manager.services.registry import get_project_root

        return Path(get_project_root()).resolve() == TRUSTED_INSTALL_ROOT.resolve()
    except OSError:
        return False


def require_pinned_runtime_rebuild_root() -> None:
    """source staging·state owner와 host-wide destructive mutation authority를
    root로 고정한다. compose_service.py·c6c_deployment.py에 바이트 그대로
    중복돼 있던 2줄짜리 확인이다. ADR-51 C-3부터는 compose_service.py의 rebuild
    입구 하나만 부른다 — c6c_deployment.py 쪽 사본은 그것을 쓰던 pinned rebuild
    lease와 함께 지웠다. G 자체의 root 요구는 lock 디렉터리 소유자 검사가 맡는다.

    `DeploymentContractError`는 c6c_deployment.py에 있다 — 모듈 scope에서
    import하면 그 모듈이 이 모듈의 lock 상수를 import하는 것과 맞물려 순환이
    된다. 함수 안에서 지연 import하면 호출 시점에는 두 모듈 모두 이미 완전히
    초기화돼 있으므로 순환이 실제로 발생하지 않는다 — 위 `running_from_trusted_install_root`가
    `registry.get_project_root`에 이미 쓰는 것과 같은 패턴이다.
    """

    from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

    if os.geteuid() != 0:
        raise DeploymentContractError("pinned runtime rebuild requires root execution")


def trusted_pinned_runtime_project_root() -> Path:
    """root `rebuild-pinned`가 쓸 수 있는 유일한 trusted release root를 반환한다.

    ADR-46 역전으로 PinVi의 다중 role 모델이 폐기되면서
    `pinvi_database_role_credentials`가 통째로 사라졌다. 이 함수만은 그 모델과
    무관하게 pinned rebuild의 env snapshot 경로가 쓰므로 경로 상수의 정본인
    여기로 옮겼다(GM-09).
    """

    raw_root = TRUSTED_INSTALL_ROOT
    try:
        metadata = raw_root.lstat()
    except OSError as exc:
        raise DeploymentContractError(
            "trusted PinVi rebuild project root cannot be inspected"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise DeploymentContractError(
            "trusted PinVi rebuild project root has unsafe ownership or mode"
        )
    root = raw_root.resolve(strict=True)
    if root != raw_root:
        raise DeploymentContractError("trusted PinVi rebuild project root is not canonical")
    return root
