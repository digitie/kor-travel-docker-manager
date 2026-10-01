"""테스트 전역 설정.

pinned revision은 이제 registry 파일에서 온다. 테스트 모듈 일부가 import 시점에
``current_pinned_runtime_release()``를 호출하므로, 그 시점의 registry 경로가
개발자 셸의 ``KTDM_RUNTIME_PINS_FILE``에 좌우되면 수집 자체가 환경에 의존한다.
여기서 저장소에 추적된 읽기 전용 seed로 고정해 결정적으로 만든다. 개별 테스트는
필요하면 monkeypatch로 자기 격리 registry를 계속 지정할 수 있다.
"""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import kor_travel_docker_manager.database as _database_module

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED = _REPO_ROOT / "config" / "runtime-pins.seed.json"

# GM-14 리뷰: test_api.py/test_metrics.py가 모듈 레벨에서
# `kor_travel_docker_manager.database.engine`을 테스트용 인메모리 엔진으로
# 바꿔치기한다. conftest.py는 pytest가 어떤 test_*.py보다도 먼저 임포트하므로,
# 여기서 미리 참조를 잡아 두면 이후 어느 파일이 스와핑을 하든(수집 순서와
# 무관하게) "실제 프로덕션 엔진에 원하는 리스너가 걸려 있는가" 같은 검증을
# 안전하게 할 수 있다.
ORIGINAL_METRICS_DB_ENGINE = _database_module.engine


@pytest.fixture(scope="session")
def original_metrics_db_engine():
    return ORIGINAL_METRICS_DB_ENGINE


os.environ["KTDM_RUNTIME_PINS_FILE"] = str(_SEED)
# 개발 체크아웃이 Windows 공유 마운트(WSL drvfs)에 있으면 모든 파일이 0777로 보고돼
# registry 무결성 검사의 mode 항목을 만족할 수 없다. 테스트에서만 그 항목을 완화한다
# (소유자 검사는 그대로 유효하고, root에서는 이 완화 자체가 무효다).
os.environ.setdefault("KTDM_RUNTIME_PINS_ALLOW_INSECURE_MODE", "1")
# 공개 사본은 저장소를 오염시키지 않도록 임시 경로로 보낸다. seed는 읽기 전용이라
# 테스트가 publish를 실행하지 않지만, 기본값이 저장소 안을 가리키게 두지 않는다.
os.environ.setdefault(
    "KTDM_RUNTIME_PINS_PUBLIC_FILE",
    str(Path(tempfile.gettempdir()) / "ktdm-test-runtime-pins.json"),
)

_REAL_GLOBAL_MUTATION_LOCK_MARKER = "real_global_mutation_lock"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{_REAL_GLOBAL_MUTATION_LOCK_MARKER}: 자동 tmp 격리 없이 실제 host 변경 lock "
        "상수(`/run/lock/...`)와 root 소유자 값을 그대로 본다 — 상수 동일성 검사 전용",
    )


@pytest.fixture(scope="session")
def _global_mutation_lock_root() -> Iterator[Path]:
    """테스트별 lock 디렉터리를 담는 세션 디렉터리. 정리는 세션 끝에 한 번만 한다.

    테스트마다 teardown에서 지우면, 같은 ``monkeypatch``로 ``os.open``을 가로챈 테스트의
    대역이 아직 살아 있는 동안 ``shutil.rmtree``가 돌아 그 대역에 걸린다(teardown 순서상
    autouse 픽스처의 뒷정리가 ``monkeypatch`` 원복보다 먼저다).
    """

    root = Path(tempfile.mkdtemp(prefix="ktdm-global-lock.", dir="/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_global_mutation_lock(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    _global_mutation_lock_root: Path,
) -> Iterator[None]:
    """host 변경 lock ``G``를 테스트마다 자기 소유의 빈 ``0700`` 디렉터리로 옮긴다.

    ADR-51 C-1 전에는 CLI pin 테스트가 **환경의 우연**으로 통과했다 — CI에는
    ``/run/lock/kor-travel-docker-manager``가 없어서(``FileNotFoundError``), n150
    비root에서는 읽을 수 없어서(``PermissionError``) lock 없이 진행했다. 그 분기가
    사라졌으므로 전제를 구성으로 만든다: 어느 호스트든 lock은 처음 온 획득자가 만든다.

    ``/tmp``를 쓰는 이유: Windows 공유 마운트(drvfs)는 mode를 ``0777``로 보고해
    ``0700``/``0600`` 검사를 만족할 수 없다. 소유자 기대값만 실행 euid로 바꾸고
    나머지 검사(0600·nlink 1·dev/ino·디렉터리 0700)는 그대로 돈다.
    """

    if request.node.get_closest_marker(_REAL_GLOBAL_MUTATION_LOCK_MARKER) is not None:
        yield
        return
    from kor_travel_docker_manager.services import c6c_deployment

    directory = Path(tempfile.mkdtemp(prefix="test.", dir=_global_mutation_lock_root))
    os.chmod(directory, 0o700)
    monkeypatch.setattr(
        c6c_deployment, "_C6C_GLOBAL_MUTATION_LOCK", directory / "global-mutation.lock"
    )
    monkeypatch.setattr(c6c_deployment, "_GLOBAL_LOCK_OWNER_UID", os.geteuid())
    yield


#: 합성 외부 target 쌍 — 멀티프로젝트 메커니즘(묶음·실행·로그 경계·선언 검증기)을
#: **실제 외부 target의 이름에 기대지 않고** 태운다.
#:
#: 이 자리에는 원래 실제 선언이 있었다. 2026-09-28까지 외부 target 하나가 그 전용 DB
#: target에 `depends_on`으로 매달려 있었고, 그것이 저장소의 유일한 "target 하나,
#: 프로젝트 둘" 실례였다. DB instance가 사라져 그 선언을 뺐고(#429), 같은 날 남은 외부
#: target의 배포 identity도 `transport`로 바뀌었다. 검사가 실제 이름을 예시로 쓰면 이름이
#: 바뀔 때마다 메커니즘 검사까지 함께 고쳐야 한다. 그래서 메커니즘은 이 합성 쌍으로 세고,
#: 실제 선언은 그 선언을 직접 겨냥한 검사(`test_multi_project_targets.py`의 좌표·옛 이름
#: 부재 검사, 실제 컨테이너의 수명주기·guard 검사)만 센다.
#:
#: 형태는 옛 실례를 그대로 따른다 — 같은 디렉터리에 compose 파일이 둘이고 파일마다
#: 프로젝트가 다르며, 앱 쪽이 DB 쪽에 의존한다.
_SIBLING_WORKING_DIR = "/srv/kor-travel-test-sibling"
_SIBLING_TARGETS: dict[str, dict[str, object]] = {
    "test-sibling-db": {
        "external_project": {
            "project": "kor-travel-test-sibling-db",
            "working_dir": _SIBLING_WORKING_DIR,
            "config_files": ["docker-compose.db.yml"],
        },
        "port_band": "19000-19000",
        "depends_on": [],
        "display_name": "Test Sibling DB",
        "description": "(합성) 외부 target 쌍의 의존 쪽 — 같은 디렉터리의 두 번째 compose 프로젝트.",
        "aliases": [],
        "services": ["postgres"],
        "runtime_services": ["postgres"],
        "containers": ["kor-travel-test-sibling-postgresql"],
    },
    "test-sibling": {
        "external_project": {
            "project": "kor-travel-test-sibling",
            "working_dir": _SIBLING_WORKING_DIR,
            "config_files": ["docker-compose.yml"],
        },
        "port_band": "19001-19099",
        "depends_on": ["test-sibling-db"],
        "display_name": "Test Sibling",
        "description": "(합성) 외부 target — 멀티프로젝트 메커니즘 검사 전용.",
        "aliases": [],
        "services": ["backend", "frontend"],
        "runtime_services": ["backend", "frontend"],
        "containers": ["kor-travel-test-sibling-backend", "kor-travel-test-sibling-frontend"],
    },
}
_SIBLING_CONTAINERS: dict[str, dict[str, object]] = {
    "kor-travel-test-sibling-postgresql": {
        "name": "kor-travel-test-sibling-db-postgres-1",
        "compose_service": "postgres",
        "external_project": "kor-travel-test-sibling-db",
        "role": "test-sibling-postgresql",
        "display_name": "Test Sibling PostgreSQL",
        "connection": "postgresql://127.0.0.1:19000",
        "expected_ports": ["19000:5432"],
    },
    "kor-travel-test-sibling-backend": {
        "name": "kor-travel-test-sibling-backend-1",
        "compose_service": "backend",
        "external_project": "kor-travel-test-sibling",
        "role": "test-sibling-backend",
        "display_name": "Test Sibling Backend",
        "connection": "http://127.0.0.1:19001",
        "expected_ports": ["19001:8000"],
    },
    "kor-travel-test-sibling-frontend": {
        "name": "kor-travel-test-sibling-frontend-1",
        "compose_service": "frontend",
        "external_project": "kor-travel-test-sibling",
        "role": "test-sibling-frontend",
        "display_name": "Test Sibling Frontend",
        "connection": "http://127.0.0.1:19002",
        "expected_ports": ["19002:3000"],
    },
}


def _with_sibling_projects(config: dict[str, Any]) -> dict[str, Any]:
    """설정 사본에 합성 외부 target 쌍(`test-sibling` → `test-sibling-db`)을 얹는다.

    `dependency_order`에 둘 다 넣는다. 빠지면 `target_sequence_for_target`이 폐포를
    같은 키(`len(order)`)로 정렬해 두 묶음의 순서가 set 순회 순서에 맡겨진다.
    """

    config = copy.deepcopy(dict(config))
    config["dependency_order"] = [
        *config["dependency_order"],
        "test-sibling-db",
        "test-sibling",
    ]
    config["targets"] = {**config["targets"], **copy.deepcopy(_SIBLING_TARGETS)}
    config["containers"] = {**config["containers"], **copy.deepcopy(_SIBLING_CONTAINERS)}
    return config


@pytest.fixture
def add_sibling_projects() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """설정 dict를 받아 합성 외부 target 쌍을 얹은 사본을 돌려주는 함수."""

    return _with_sibling_projects


@pytest.fixture
def sibling_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    """`registry.load_targets_config()` 자체를 합성 외부 target 쌍이 있는 설정으로 갈아 끼운다.

    `MANAGED_CONTAINERS`/`_targets()` 등은 `_LazyMapping`으로 접근할 때마다 이 함수를
    다시 부르므로 patch 하나로 `registry.py`와 그 소비자가 일관되게 새 값을 본다.
    합성 설정도 실제 무결성 검사를 통과해야 한다 — 통과하지 못하면 검사가 헛돈다.
    """

    from kor_travel_docker_manager.services import registry as registry_module

    registry_module.load_targets_config.cache_clear()
    try:
        config = _with_sibling_projects(dict(registry_module.load_targets_config()))
    finally:
        registry_module.load_targets_config.cache_clear()
    registry_module._validate_targets_config(copy.deepcopy(config), label="<sibling projects>")
    monkeypatch.setattr(registry_module, "load_targets_config", lambda: config)


# ── pinned pair의 "모두 own" 기준선(ADR-54) ───────────────────────────────


def _dagster_topology_cache_clears() -> list[Callable[[], None]]:
    """설치 모델에서 파생해 캐시하는 함수들의 `cache_clear` — 기준선을 바꾸는 테스트 앞뒤로 비운다."""

    from kor_travel_docker_manager.services import c6c_deployment
    from kor_travel_docker_manager.services import runtime_topology as topology

    clears = [
        topology.installed_dagster_family.cache_clear,
    ]
    for value in vars(c6c_deployment).values():
        if callable(value) and hasattr(value, "cache_clear") and getattr(value, "__module__", "") == c6c_deployment.__name__:
            clears.append(value.cache_clear)
    return clears


@pytest.fixture
def own_pinned_pair(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """pinned Map·PinVi pair를 **둘 다 `own`**으로 둔 설치 모델을 쓴다.

    pinned 재구축·C6c·이미지 보존의 기제 테스트는 #447부터 "모두 own"을 기준선으로 쓰고, 공용 plane 전환은
    `flipped=(…)`로 그 위에 얹어 본다. 저장소의 설치 모델에서 PinVi(그리고 뒤에 Map)가 실제로 `shared`가 된 뒤에도
    그 기제 테스트가 같은 기준선을 보도록, 이 fixture가 체크아웃 모델에서 두 target의 스위치만 `own`으로 되돌린
    문서를 설치 모델로 준다. 파생은 스위치와 모양만 보므로(`derive_dagster_family`) 스위치 하나로 충분하다.
    실제 설치 모델을 보는 테스트(`test_dagster_shared_*`, 레지스트리·CLI)는 이 fixture를 쓰지 않는다.

    패치는 테스트와 **같은** `monkeypatch`에 건다 — 되돌리기는 pytest가 그 fixture의 정리에서 건 순서의 역순으로
    한다(테스트가 같은 속성을 다시 바꿔 끼워도 이 문서, 그다음 진짜 것으로 돌아간다). 여기서 `undo()`를 부르면
    테스트의 패치까지 앞당겨 되돌리고, 따로 연 문맥은 정리 순서가 테스트의 `monkeypatch`와 엇갈려 이 문서를 남긴다.
    캐시는 **진짜 함수의 것**을 setup 때 잡아 두고 비운다 — 테스트가 그 함수를 바꿔 끼운 채여도 정리가 닿는다.
    """

    from kor_travel_docker_manager.services import runtime_topology as topology

    from test_dagster_shared_workspace_is_derived import _unflip

    compose, targets = topology._installed_documents()
    # 스위치와 compose를 함께 전환 전 모양으로 되돌린다 — Map·PinVi가 모두 합류한 뒤에도(2026-10-01) 기준선이
    # 일관된다(스위치만 `own`이고 compose는 공용 plane이면 C6c의 스위치 의존 검사가 둘을 섞어 본다).
    own_compose = copy.deepcopy(dict(compose))
    own_targets = copy.deepcopy(dict(targets))
    for target in (topology.MAP_TARGET, topology.PINVI_TARGET):
        _unflip(own_compose, own_targets, target)
    clears = _dagster_topology_cache_clears()
    for clear in clears:
        clear()
    # 문서를 바꿔 끼운다 — 파생 함수는 진짜 것이 그대로 돈다(테스트가 그 함수를 다시 바꿔 끼워도 된다).
    monkeypatch.setattr(topology, "_installed_documents", lambda: (own_compose, own_targets))
    try:
        yield
    finally:
        for clear in clears:
            clear()
