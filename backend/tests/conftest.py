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


#: `airport-db`가 2026-09-28까지 `config/docker-targets.yml`에 실제로 갖고 있던
#: 선언이다. 그 형제 DB 인스턴스(`kor-travel-airport-db-postgres-1`, :14000)는 n150에서
#: 이미 사라졌고 transport는 공용 instance(:11000)를 쓴다 — 그래서 정본 설정에서 뺐다.
#: 그런데 그것이 저장소의 **유일한 "target 하나, 프로젝트 둘"** 실례였다
#: (`airport` → `airport-db`). 그 기능 자체(의존 폐포가 두 compose 프로젝트에 걸치는
#: 경우의 묶음·실행·로그 경계)는 여전히 코드에 있으므로, weather의 옛 좌표를
#: 합성으로 복원하는 `weather_as_external`과 같은 원리로 이 선언을 합성 복원해 그
#: 경로를 계속 실제 코드로 태운다.
_LEGACY_AIRPORT_DB_TARGET: dict[str, object] = {
    "external_project": {
        "project": "kor-travel-airport-db",
        "working_dir": "/home/digitie/apps/kor-travel-airport",
        "config_files": ["docker-compose.db.yml"],
    },
    "port_band": "14000-14000",
    "depends_on": [],
    "display_name": "Kor Travel Airport DB",
    "description": "(합성) 2026-09-28 이전의 Kor Travel Airport 전용 PostgreSQL target.",
    "aliases": ["airport-postgresql", "airport-postgres"],
    "services": ["postgres"],
    "runtime_services": ["postgres"],
    "containers": ["kor-travel-airport-postgresql"],
}
_LEGACY_AIRPORT_DB_CONTAINER: dict[str, object] = {
    "name": "kor-travel-airport-db-postgres-1",
    "compose_service": "postgres",
    "external_project": "kor-travel-airport-db",
    "role": "airport-postgresql",
    "display_name": "Kor Travel Airport 전용 PostgreSQL",
    "connection": "postgresql://127.0.0.1:14000",
    "expected_ports": ["14000:5432"],
}


def _with_legacy_airport_db(config: dict[str, Any]) -> dict[str, Any]:
    """설정 사본에 옛 `airport-db` target·컨테이너를 얹고 `airport`가 그것에 의존하게 한다."""

    config = copy.deepcopy(dict(config))
    order = list(config["dependency_order"])
    order.insert(order.index("airport"), "airport-db")
    config["dependency_order"] = order
    config["targets"] = dict(config["targets"])
    config["targets"]["airport-db"] = copy.deepcopy(_LEGACY_AIRPORT_DB_TARGET)
    config["targets"]["airport"] = {
        **config["targets"]["airport"],
        "depends_on": ["airport-db"],
    }
    config["containers"] = dict(config["containers"])
    config["containers"]["kor-travel-airport-postgresql"] = copy.deepcopy(
        _LEGACY_AIRPORT_DB_CONTAINER
    )
    return config


@pytest.fixture
def legacy_airport_db() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """설정 dict를 받아 옛 `airport-db`를 얹은 사본을 돌려주는 함수."""

    return _with_legacy_airport_db


@pytest.fixture
def airport_with_legacy_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """`registry.load_targets_config()` 자체를 옛 `airport-db`가 있는 설정으로 갈아 끼운다.

    `MANAGED_CONTAINERS`/`_targets()` 등은 `_LazyMapping`으로 접근할 때마다 이 함수를
    다시 부르므로 patch 하나로 `registry.py`와 그 소비자가 일관되게 새 값을 본다.
    합성 설정도 실제 무결성 검사를 통과해야 한다 — 통과하지 못하면 검사가 헛돈다.
    """

    from kor_travel_docker_manager.services import registry as registry_module

    registry_module.load_targets_config.cache_clear()
    try:
        config = _with_legacy_airport_db(dict(registry_module.load_targets_config()))
    finally:
        registry_module.load_targets_config.cache_clear()
    registry_module._validate_targets_config(copy.deepcopy(config), label="<legacy airport-db>")
    monkeypatch.setattr(registry_module, "load_targets_config", lambda: config)
