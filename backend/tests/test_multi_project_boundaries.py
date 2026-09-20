"""멀티프로젝트 target — Manager의 compose가 남의 프로젝트에 새지 않는 경계.

`compose_service`는 **그 프로젝트 안에서만** 유일한 이름이다. 등록 당시 컨테이너
중 `prometheus`(weather)·`postgres`(airport-db)·`backend`·`frontend`·`web`은 전부 흔한
이름이고, 그중 `prometheus`는 **Manager 자신의 서비스 이름과 정확히 겹쳤다**.

Manager는 그 값을 자기 compose에 곧장 조회했다. 그래서 겹치는 순간 셋이 한꺼번에
틀어졌다(적대 리뷰 2026-09-18 C-3):

    목록 화면    weather Prometheus 카드에 **Manager Prometheus의** ports·env가 뜬다
    저장         그 화면의 편집이 **Manager의 docker-compose.yml**로 간다
    없는 것 start `kor-travel-weather-prometheus`를 켜라 했는데 **Manager의 Prometheus가
                 생기고** "만들어서 시작했다"고 성공을 보고한다

weather는 2026-09-20(ADR-47)부터 Manager internal target이라(자기 `prometheus`도
`kor-travel-weather-prometheus`로 개명) 이 정확한 실제 사례는 저장소에서 사라졌다.
아래 테스트들은 `colliding_external_container` fixture로 같은 이름 충돌을 airport
아래에 합성해 재현한다 — 메커니즘 자체는 이름이 실재하든 합성이든 똑같이 유효하다.

Docker SDK 경로(start/stop/restart)는 compose 프로젝트와 무관하므로 막지 않는다 —
외부 컨테이너도 이름으로 껐다 켤 수 있어야 하고 그것이 이 기능의 값어치다. 막는
것은 **compose 파일을 쓰는 것**뿐이다.

그리고 target 선언 사이의 무결성. 이 검사들은 한때 `test_registry_targets_config.py`의
assert였다 — 저장소의 `config/docker-targets.yml`만 봤으므로 설치본이나
`KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE`로 온 설정에는 아무 효력이 없었고
`ktdctl targets validate`도 잡지 못했다. 이제 검증기 안에 있고, 여기서는 그 검증기가
**공허하지 않음**을 증명한다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import docker_service as docker_service_module
from kor_travel_docker_manager.services import registry as registry_module
from kor_travel_docker_manager.services.docker_service import (
    DockerService,
    ExternalContainerMutationError,
)
from kor_travel_docker_manager.services.registry import (
    TargetsConfigError,
)

#: 정본 `docker-compose.yml` 1행이 선언하는 이름. 구현에서 파생하지 않는다.
_MANAGER_PROJECT_NAME = "kor-travel-docker-manager"


class _FakeContainer:
    def __init__(self, name: str, project: str | None) -> None:
        labels = {"com.docker.compose.project": project} if project else {}
        self.attrs = {
            "Id": f"sha256:{name}",
            "Config": {"Labels": labels},
            "HostConfig": {"PortBindings": {}},
            "State": {"Status": "running"},
        }
        self.status = "running"
        self.image = None


class _FakeClient:
    """`get_containers_status`의 **live 분기**를 태우기 위한 최소 docker client."""

    def __init__(self, project_overrides: dict[str, str] | None = None) -> None:
        self._overrides = project_overrides or {}
        self.containers = self

    def get(self, name: str) -> Any:
        from kor_travel_docker_manager.services.registry import (
            MANAGED_CONTAINERS,
            external_project_for_container,
        )

        container_id = next(
            (key for key, spec in MANAGED_CONTAINERS.items() if spec["name"] == name),
            None,
        )
        if container_id is None:
            from docker.errors import NotFound

            raise NotFound(name)
        if container_id in self._overrides:
            project = self._overrides[container_id]
        else:
            external = external_project_for_container(container_id)
            # **구현과 같은 식으로 만들지 않는다.** 첫 판은 Manager 라벨을
            # `Path(get_project_root()).name`으로 만들었고, 구현도 같은 식이어서
            # 불일치가 **원리상 발생할 수 없었다** — 그 탓에 구현이 compose의 정본
            # 순서를 잘못 모델링한 것을 스위트가 구조적으로 못 봤다(적대 리뷰
            # 2026-09-18 E-R2-03). 정본 문서가 선언한 이름을 리터럴로 쓴다.
            project = (
                external.project if external is not None else _MANAGER_PROJECT_NAME
            )
        return _FakeContainer(name, project)


_ROOT = Path(__file__).resolve().parents[2]

_MANAGER_PROMETHEUS_CONFIG = {
    "ports": ["12401:12401"],
    "environment": {"KTDM_ONLY": "manager"},
    "volumes": ["/manager/prometheus:/prometheus"],
    "networks": ["kor-travel-net"],
}


@pytest.fixture
def manager_compose(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Manager compose에 `prometheus`가 **있는** 상태. 겹침이 실재해야 검사가 뜻이 있다."""

    # **`name:`을 함께 준다.** 정본 `docker-compose.yml` 1행이 그것을 선언하고,
    # 프로젝트 이름 해석이 그 값을 본다. 빼면 디렉터리 이름으로 떨어져 라벨 대조가
    # 전부 불일치가 된다 — 검사를 약하게 하지 않고 fragment를 완전하게 한다(S1 처방).
    document = {
        "name": _MANAGER_PROJECT_NAME,
        "services": {"prometheus": copy.deepcopy(_MANAGER_PROMETHEUS_CONFIG)},
    }
    monkeypatch.setattr(
        docker_service_module, "get_compose_config", lambda path=None: document
    )
    return document


#: 합성 충돌 컨테이너의 등록 id. weather의 옛 `prometheus`(2026-09-20 ADR-47 이전)가
#: 실제로 제공하던 유일한 사례 — "외부 target의 compose_service 이름이 Manager 자신의
#: 서비스 이름과 정확히 겹친다" — 를 대신한다. weather가 internal target으로 바뀌며
#: (그리고 자기 `prometheus`도 `kor-travel-weather-prometheus`로 개명하며) 그 실제
#: 사례가 저장소에서 사라졌다 — 남은 외부 target(airport/airport-db)의 실제
#: compose_service 이름(`postgres`/`backend`/`frontend`) 중에는 Manager 서비스와
#: 겹치는 것이 하나도 없다(실측). 이 파일의 목적(C-3 회귀: 이름이 겹치면 목록·변경·
#: 없는 것 start 세 경로가 한꺼번에 틀어진다) 자체는 이름이 실재하든 합성이든
#: 똑같이 유효하므로, 진짜 외부 프로젝트(airport) 아래에 이름만 겹치는 컨테이너
#: 하나를 더해 그 시나리오를 계속 실제 코드 경로로 태운다.
_COLLISION_CONTAINER_ID = "kor-travel-test-collision-prometheus"
_COLLISION_CONTAINER_NAME = f"{_COLLISION_CONTAINER_ID}-1"


@pytest.fixture
def colliding_external_container(monkeypatch: pytest.MonkeyPatch) -> str:
    """`load_targets_config()`가 반환하는 실제 설정에 합성 충돌 컨테이너를 얹는다.

    `MANAGED_CONTAINERS`/`external_project_for_container`/`_targets()`는 전부
    `_LazyMapping`으로 `load_targets_config()`를 접근할 때마다 다시 부른다
    (`registry.py`의 `_LazyMapping` docstring 참고, 그 함수 자신은
    `@lru_cache`라 재계산 비용이 없다) — 그래서 이 함수 하나만 갈아 끼우면
    `docker_service.py`와 `registry.py` 양쪽이 일관되게 새 값을 본다.
    """

    config = _real_config()
    config["containers"][_COLLISION_CONTAINER_ID] = {
        "name": _COLLISION_CONTAINER_NAME,
        "compose_service": "prometheus",
        "external_project": "kor-travel-airport",
        "role": "test-collision-prometheus",
        "display_name": "Test Collision Prometheus",
        "connection": "http://127.0.0.1:19999",
        "expected_ports": [],
    }
    config["targets"]["airport"] = {
        **config["targets"]["airport"],
        "services": [*config["targets"]["airport"]["services"], "prometheus"],
        "containers": [
            *config["targets"]["airport"]["containers"],
            _COLLISION_CONTAINER_ID,
        ],
    }
    _validate(config)  # 합성 config 자체가 무결성 검사를 통과하는지 먼저 확인한다.
    monkeypatch.setattr(registry_module, "load_targets_config", lambda: config)
    return _COLLISION_CONTAINER_ID


# ── 표시: 외부 컨테이너의 config는 Manager compose에서 오지 않는다 ───────


def test_external_container_config_does_not_come_from_the_manager_compose(
    manager_compose: dict[str, Any],
    colliding_external_container: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이름이 겹쳤다는 이유로 남의 카드에 내 설정을 그리면 안 된다.

    화면이 보여준 값은 그대로 편집기의 초기값이 된다 — 표시가 틀리면 그 다음 저장이
    **Manager의 compose를 남의 값으로 덮어쓴다**. 표시와 변경은 같은 실수의 앞뒤다.
    """

    monkeypatch.setattr(
        DockerService, "_get_client", lambda self: (_ for _ in ()).throw(RuntimeError())
    )
    entries = {entry["id"]: entry for entry in DockerService().get_containers_status()}

    external = entries[colliding_external_container]["config"]
    assert external["ports"] == []
    assert external["env"] == {}
    assert external["volumes"] == []
    assert external["locked_env"] == []

    # 그리고 Manager 자신의 것은 종전대로 읽힌다 — 좁히기가 너무 넓으면 여기가 빨개진다.
    assert entries["prometheus"]["config"]["ports"] == ["12401:12401"]
    assert entries["prometheus"]["config"]["env"] == {"KTDM_ONLY": "manager"}


# ── 변경: 세 경로가 모이는 한 자리에서 끊는다 ────────────────────────────


def test_compose_mutation_is_refused_for_an_external_container(
    manager_compose: dict[str, Any],
    colliding_external_container: str,
) -> None:
    """`update` · `reset` · NotFound 재생성이 전부 이 함수로 모인다.

    guard를 호출부마다 복사하면 어느 한 벌을 지워도 아무 검사가 빨개지지 않는다.
    한 자리에 두는 것이 요점이라, 이 검사도 그 한 자리를 본다.
    """

    with pytest.raises(ExternalContainerMutationError, match="kor-travel-airport"):
        DockerService()._update_container_config_unlocked(
            colliding_external_container,
            ["14104:9090"],
            {},
            [],
            [],
            environment_snapshot=None,  # type: ignore[arg-type]
        )


def test_starting_a_missing_external_container_does_not_recreate_a_manager_service(
    manager_compose: dict[str, Any],
    colliding_external_container: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NotFound 분기가 Manager compose에서 같은 이름을 찾아 만들어 버리는 경로.

    사용자가 켜려던 것은 (이름이 겹치는) 외부 target의 Prometheus인데 Manager의
    Prometheus가 생기고 **성공이 보고된다**. 조용한 오답이 실패보다 나쁘다.
    """

    from docker.errors import NotFound

    class _Containers:
        def get(self, name: str) -> Any:
            raise NotFound(name)

    class _Client:
        containers = _Containers()

    monkeypatch.setattr(DockerService, "_get_client", lambda self: _Client())
    written: list[Any] = []
    monkeypatch.setattr(
        docker_service_module,
        "_atomic_write",
        lambda *args, **kwargs: written.append(args),
    )

    class _Snapshot:
        compose_path = "/does/not/matter"

    # guard는 `_update_container_config_unlocked` **안**에 있다(세 경로가 모이는 한
    # 자리). 그래서 그 함수를 대역으로 갈아 끼우지 않고 진짜로 통과시킨 뒤, 위로
    # 올라온 예외와 **파일이 쓰이지 않았다는 것**을 함께 본다.
    with pytest.raises(ExternalContainerMutationError):
        DockerService()._control_container_unlocked(
            colliding_external_container,
            "start",
            environment_snapshot=_Snapshot(),  # type: ignore[arg-type]
        )
    assert written == [], "Manager의 compose 파일은 한 바이트도 쓰이지 않아야 한다"


def test_manager_containers_still_reach_the_compose_recreate_path(
    manager_compose: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """거부가 Manager 컨테이너까지 막지 않는다 — 좁히기가 넓어지면 여기가 잡는다."""

    from docker.errors import NotFound

    class _Containers:
        def get(self, name: str) -> Any:
            raise NotFound(name)

    class _Client:
        containers = _Containers()

    monkeypatch.setattr(DockerService, "_get_client", lambda self: _Client())
    seen: list[str] = []

    def _fake_update(self: DockerService, container_id: str, *args: Any, **kwargs: Any):
        seen.append(container_id)
        return {"success": True}

    monkeypatch.setattr(DockerService, "_update_container_config_unlocked", _fake_update)

    class _Snapshot:
        compose_path = "/does/not/matter"

    result = DockerService()._control_container_unlocked(
        "prometheus", "start", environment_snapshot=_Snapshot()  # type: ignore[arg-type]
    )
    assert seen == ["prometheus"]
    assert result["success"] is True


@pytest.mark.parametrize("action", ["stop", "restart"])
def test_lifecycle_actions_stay_available_for_external_containers(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SDK 경로는 compose 프로젝트와 무관하다 — 여기까지 막으면 기능이 사라진다."""

    performed: list[str] = []

    class _Container:
        def stop(self) -> None:
            performed.append("stop")

        def restart(self) -> None:
            performed.append("restart")

    class _Containers:
        def get(self, name: str) -> Any:
            assert name == "kor-travel-airport-backend-1"
            return _Container()

    class _Client:
        containers = _Containers()

    monkeypatch.setattr(DockerService, "_get_client", lambda self: _Client())
    result = DockerService()._control_container_unlocked(
        "kor-travel-airport-backend",
        action,
        environment_snapshot=None,  # type: ignore[arg-type]
    )
    assert result["success"] is True
    assert performed == [action]


# ── 선언 사이의 무결성은 **검증기** 안에 있다 ────────────────────────────


def _real_config() -> dict[str, Any]:
    registry_module.load_targets_config.cache_clear()
    try:
        config = copy.deepcopy(dict(registry_module.load_targets_config()))
    finally:
        registry_module.load_targets_config.cache_clear()
    config["targets"] = copy.deepcopy(dict(config["targets"]))
    config["containers"] = copy.deepcopy(dict(config["containers"]))
    return config


def _validate(config: dict[str, Any]) -> None:
    registry_module._validate_targets_config(config, label="<test>")


def test_container_declaring_an_undeclared_project_is_rejected() -> None:
    """오타는 `external_project_for_container`에서 조용히 `None`이 된다.

    그러면 그 컨테이너는 Manager 소속으로 취급되고, 곧 위의 C-3 세 증상이 그대로
    돌아온다. 선언 시점에 막는 것이 유일하게 싼 자리다.
    """

    config = _real_config()
    config["containers"]["kor-travel-weather-prometheus"] = {
        **config["containers"]["kor-travel-weather-prometheus"],
        "external_project": "kor-travel-wether",
    }
    with pytest.raises(TargetsConfigError, match="no target declares project"):
        _validate(config)


def test_a_manager_container_cannot_be_tagged_with_an_external_project() -> None:
    """소속이 선언과 어긋나면 어느 프로젝트의 것인지가 두 곳에서 갈린다."""

    config = _real_config()
    config["containers"]["prometheus"] = {
        **config["containers"]["prometheus"],
        "external_project": "kor-travel-airport",
    }
    with pytest.raises(TargetsConfigError, match="belongs to project"):
        _validate(config)


def test_container_external_project_must_be_a_string() -> None:
    config = _real_config()
    config["containers"]["kor-travel-weather-api"] = {
        **config["containers"]["kor-travel-weather-api"],
        "external_project": {"project": "kor-travel-weather"},
    }
    with pytest.raises(TargetsConfigError, match="non-empty string"):
        _validate(config)


def test_external_target_must_list_the_services_its_containers_name() -> None:
    """외부 target의 `services`는 저장소 밖 compose와 대조할 수 없다 — 선언끼리 묶는다.

    이 결박이 없으면 외부 target의 `services`가 **아무것과도** 대조되지 않는다.
    적대 리뷰는 (당시 외부 target이던) weather target에 Manager 서비스 이름과
    존재하지 않는 이름을 넣어도 무결성 검사가 하나도 빨개지지 않는 것을 실측했다
    — weather는 2026-09-20(ADR-47)부터 internal target이라 이제 airport로
    같은 것을 증명한다(여전히 외부 target).
    """

    config = _real_config()
    airport = config["targets"]["airport"]
    config["targets"]["airport"] = {
        **airport,
        "services": [name for name in airport["services"] if name != "frontend"],
    }
    with pytest.raises(TargetsConfigError, match="the target does not list"):
        _validate(config)


def test_one_shot_services_need_no_container_registration() -> None:
    """반대 방향은 강제하지 않는다 — one-shot은 `services`에만 있는 것이 이 저장소 규칙이다.

    `migrate`는 정상 상태가 `exited(0)`이라 `containers:`에 넣으면 대시보드에 상시
    비정상 카드로 남고 metrics 관측 대상이 된다(`rustfs-init`이 같은 이유로 빠져 있다).
    """

    config = _real_config()
    assert "kor-travel-weather-migrate" in config["targets"]["weather"]["services"]
    assert "kor-travel-weather-migrate" not in config["containers"]
    _validate(config)


def test_the_same_project_cannot_be_declared_with_two_coordinates() -> None:
    """묶음 키는 좌표 전체인데 컨테이너 소유 해석은 **이름**으로 첫 매치를 고른다.

    둘이 갈리면 컨테이너가 어느 좌표에 속하는지가 파일 순서로 정해진다.
    """

    config = _real_config()
    config["targets"]["airport"] = {
        **config["targets"]["airport"],
        "external_project": {
            "project": "kor-travel-airport-db",
            "working_dir": "/home/digitie/apps/kor-travel-airport",
            "config_files": ["docker-compose.yml"],
        },
    }
    with pytest.raises(TargetsConfigError, match="different coordinates"):
        _validate(config)


def test_a_manager_target_cannot_depend_on_an_external_target() -> None:
    """의존 한 줄이 **Manager target의** 배포를 막는다. 메시지는 Manager를 탓한다.

    `ensure`는 의존 폐포를 보고 거부하므로, `map`에 `depends_on: [airport]`를 더하면
    `ensure map`이 "target 'map' belongs to an external compose project"로 죽는다.
    원인을 찾기 아주 어려운 모양이라 선언 시점에 막는다(weather는 2026-09-20
    ADR-47부터 internal target이라 이 예시로 더 이상 쓸 수 없다 — airport가
    여전히 외부다).
    """

    config = _real_config()
    config["targets"]["map"] = {
        **config["targets"]["map"],
        "depends_on": [*config["targets"]["map"]["depends_on"], "airport"],
    }
    with pytest.raises(TargetsConfigError, match="can no longer be deployed"):
        _validate(config)


def test_all_must_include_every_manager_target() -> None:
    """`all`의 설명("dependency_order 전체")과 실제 내용의 차이를 규칙으로 못박는다.

    지금 `dependency_order` 12개 대 `all.include` 9개의 차이가 정확히 외부 셋인 것이
    **우연이 아니라 규칙**이다. 새 Manager target을 `dependency_order`에만 넣고
    `all`에서 빠뜨리면 `ensure all`이 조용히 그것을 건너뛴다.
    """

    config = _real_config()
    config["targets"]["all"] = {
        **config["targets"]["all"],
        "include": [
            name for name in config["targets"]["all"]["include"] if name != "map"
        ],
    }
    with pytest.raises(TargetsConfigError, match="missing Manager target 'map'"):
        _validate(config)


def test_all_cannot_reach_an_external_target() -> None:
    """반대쪽은 **M-4 검사 하나**가 막는다 — `all`은 Manager target이기 때문이다.

    같은 규칙을 `all` 전용으로 한 번 더 쓰면 한쪽을 지워도 아무 검사가 빨개지지
    않는다. 그래서 자리를 하나로 두고, 이 검사가 그 자리를 가리킨다.
    """

    config = _real_config()
    config["targets"]["all"] = {
        **config["targets"]["all"],
        "include": [*config["targets"]["all"]["include"], "airport"],
    }
    with pytest.raises(TargetsConfigError, match="can no longer be deployed"):
        _validate(config)


def test_duplicate_config_files_are_rejected() -> None:
    """compose는 `-f`를 순서대로 병합한다 — 같은 파일을 두 번 적으면 뒤엣것이 이긴다.

    airport로 센다(weather는 2026-09-20 ADR-47부터 internal target이라
    `external_project`를 target에만 더하면 weather 자신의 컨테이너들 — 전부
    `external_project`가 없는 Manager 소유 — 과 즉시 소속이 어긋나 이 검사가
    보려는 것(중복 파일)에 닿기 전에 "belongs to project"로 먼저 죽는다. airport는
    target·컨테이너 양쪽이 이미 일관되게 외부라 안전하다).
    """

    config = _real_config()
    config["targets"]["airport"] = {
        **config["targets"]["airport"],
        "external_project": {
            **config["targets"]["airport"]["external_project"],
            "config_files": ["docker-compose.yml", "docker-compose.yml"],
        },
    }
    with pytest.raises(TargetsConfigError, match="duplicate entry"):
        _validate(config)


def test_a_dotted_prefix_is_not_a_path_escape() -> None:
    """`startswith("..")`는 `..hidden/compose.yml`을 거부하는 오탐이었다.

    경로 **구성요소**로 봐야 한다. 오탐은 조용하지 않지만, 정당한 선언을 막는다.
    airport로 센다(이유는 `test_duplicate_config_files_are_rejected` 참고).
    """

    config = _real_config()
    config["targets"]["airport"] = {
        **config["targets"]["airport"],
        "external_project": {
            **config["targets"]["airport"]["external_project"],
            "config_files": ["..hidden/compose.yml"],
        },
    }
    _validate(config)

    config["targets"]["airport"]["external_project"]["config_files"] = [
        "sub/../../outside.yml"
    ]
    with pytest.raises(TargetsConfigError, match="stay inside"):
        _validate(config)


# ── 라운드 2: 선언이 아니라 **효과**에, 그리고 공개 진입점에 ─────────────


def test_lifecycle_actions_work_through_the_public_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**공개 진입점에서** 외부 컨테이너를 껐다 켤 수 있어야 한다.

    첫 판의 검사는 `_control_container_unlocked`를 직접 불러서, 공개
    `control_container`가 Manager의 c6c lock과 배포 환경 계약에 걸려 죽는 것을 보지
    못했다 — 실제로 `KTDM_DEPLOYMENT_ENVIRONMENT must be explicitly set`으로 거부됐다.
    수명주기가 산다는 주장이 `_unlocked` 층에서만 참이었다. **C-1과 똑같은 실수다.**
    """

    performed: list[str] = []

    class _Container:
        def restart(self) -> None:
            performed.append("restart")

    class _Containers:
        def get(self, name: str) -> Any:
            assert name == "kor-travel-airport-backend-1"
            return _Container()

    class _Client:
        containers = _Containers()

    monkeypatch.setattr(DockerService, "_get_client", lambda self: _Client())
    monkeypatch.delenv("KTDM_DEPLOYMENT_ENVIRONMENT", raising=False)

    result = DockerService().control_container("kor-travel-airport-backend", "restart")
    assert result["success"] is True
    assert performed == ["restart"]


@pytest.mark.parametrize("method", ["update", "reset"])
def test_config_routes_refuse_external_before_taking_the_deployment_lock(
    manager_compose: dict[str, Any], monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """**guard가 첫 관문이어야 한다.**

    첫 판은 lock 획득·Manager mutation env 계약·Manager baseline 조회를 전부 지난
    뒤에야 거부했다. 그래서 거부돼야 할 요청이 전역 배포 mutex를 건드리고, 남의
    컨테이너 요청의 답으로 Manager 계약 얘기가 나왔다.
    """

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("외부 요청이 배포 lock을 건드리면 안 된다")

    monkeypatch.setattr(
        docker_service_module, "c6c_deployment_lock_from_environment", forbidden
    )
    service = DockerService()
    with pytest.raises(ExternalContainerMutationError):
        if method == "update":
            service.update_container_config(
                "kor-travel-airport-backend", ["14104:9090"], {}, [], []
            )
        else:
            service.reset_container_config("kor-travel-airport-backend")


def test_the_read_only_boundary_carries_its_own_error_code() -> None:
    """전용 코드가 없으면 화면이 409의 일반 힌트를 붙인다.

    그 힌트는 "일시적 상태일 수 있습니다"인데 이 경계는 **항구적**이라 정반대의
    안내가 된다. 첫 판은 영문 원문 + 그 힌트가 함께 떴다.
    """

    assert ExternalContainerMutationError.code == "EXTERNAL_PROJECT_READ_ONLY"


def test_the_status_payload_names_the_owning_project(
    manager_compose: dict[str, Any],
    colliding_external_container: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """화면이 편집기를 잠글 **재료**를 실제로 준다.

    `_managed_service_config`의 docstring이 "화면은 `external_project`를 보고 편집기를
    잠근다"고 적었는데 **API가 그 필드를 내보내지 않았다**. 둘 중 하나는 거짓이어야
    했고, 고칠 쪽은 문서가 아니라 코드였다.
    """

    monkeypatch.setattr(
        DockerService, "_get_client", lambda self: (_ for _ in ()).throw(RuntimeError())
    )
    entries = {entry["id"]: entry for entry in DockerService().get_containers_status()}
    assert entries[colliding_external_container]["external_project"] == (
        "kor-travel-airport"
    )
    assert entries["prometheus"]["external_project"] is None


def test_the_live_daemon_branch_also_hides_manager_config(
    manager_compose: dict[str, Any],
    colliding_external_container: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**운영에서 도는 분기**를 태운다.

    첫 판의 검사는 `_get_client`를 `RuntimeError`로 막아 **daemon이 죽은 분기만**
    태웠다. 그래서 C-3의 실제 증상("목록 화면이 남의 카드에 Manager 설정을 그린다")을
    만드는 바로 그 줄을 되돌려도 아무 검사가 빨개지지 않았다.
    """

    monkeypatch.setattr(DockerService, "_get_client", lambda self: _FakeClient())
    entries = {entry["id"]: entry for entry in DockerService().get_containers_status()}
    assert entries[colliding_external_container]["config"]["env"] == {}
    assert entries["prometheus"]["config"]["env"] == {"KTDM_ONLY": "manager"}


def test_a_runtime_label_that_contradicts_the_declaration_fails_closed(
    manager_compose: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """선언이 틀렸을 때 **컨테이너 자신이 말하는 것**을 믿는다.

    컨테이너 절의 `external_project` **부재**는 "Manager 소유"와 구분되지 않는다 —
    H-1 검사는 값의 오타만 잡는다. 그런데 정본이 이미 실행 중 컨테이너에 있다:
    `com.docker.compose.project` 라벨이 `ExternalProject.project` 그대로다. 선언과
    어긋나면 설정을 그리지 않는다.
    """

    monkeypatch.setattr(
        DockerService,
        "_get_client",
        lambda self: _FakeClient(project_overrides={"prometheus": "somebody-else"}),
    )
    entries = {entry["id"]: entry for entry in DockerService().get_containers_status()}
    assert entries["prometheus"]["config"]["env"] == {}, (
        "라벨이 선언과 어긋나면 Manager 설정을 그리지 않는다"
    )


def test_container_specs_reject_unknown_fields() -> None:
    """오타 한 글자가 컨테이너를 **Manager 소유**로 만든다.

    `external_projct`가 조용히 무시되면 C-3의 세 증상이 그대로 복원된다.

    정정: 첫 판 docstring은 "target 절에는 이 검사가 있었다"고 적었는데 거짓이었다 —
    둘 다 없었다(적대 리뷰 2026-09-18 E-F8). target 쪽은
    `test_target_specs_reject_unknown_fields`가 센다.
    """

    config = _real_config()
    config["containers"]["kor-travel-weather-api"] = {
        **config["containers"]["kor-travel-weather-api"],
        "external_projct": "kor-travel-weather",
    }
    with pytest.raises(TargetsConfigError, match="unknown fields"):
        _validate(config)


def test_compose_binds_is_documented_as_the_managers_own_boundary() -> None:
    """`compose_binds`에는 프로젝트 차원이 **없다** — 그 사실을 문서가 말한다.

    키가 `(compose service, container_path, read_only)`뿐이라, 형제를 겨냥해 쓴 한 줄이
    Manager의 production bind allowlist를 넓힌다(적대 리뷰 2026-09-18 B-F8).

    **런타임 검사로 막지 않기로 했다.** "Manager 쪽"을 `containers:` 등재로 판정했더니
    그 파일 자신의 주석과 모순됐다 — 서비스 19개 중 8개(one-shot init 넷, geo dagster
    쌍, db role bootstrap 둘)는 `containers:`에 없다. 형제가 그 여덟 이름 중 하나를
    쓰는 순간 `load_targets_config()`가 죽어 **모든 CLI 명령과 라우트**가 함께 죽는다
    (리뷰 E-F6 실측). 막으려던 것은 공격이 아니라 오해이고, 그 파일은 prod에서 root
    소유다 — 과결박이라 거뒀다.

    남은 방어는 **경계의 뜻을 파일에 적어 두는 것**이다. 이 검사는 그 문장이 사라지지
    않게 한다.
    """

    path = _ROOT / "config" / "docker-targets.yml"
    text = path.read_text(encoding="utf-8")
    assert "이 절은 Manager 자신의 후보에만 적용된다" in text, (
        "compose_binds 헤더에서 경계의 범위를 말하는 문장이 사라졌다"
    )


def test_a_manager_target_can_opt_out_of_all() -> None:
    """**의도를 말할 자리를 둔다.**

    `all.include` 완전성은 안전 규칙이 아니라 관례 검사인데 `load_targets_config()`
    안에서 돌아 모든 CLI 명령과 라우트가 함께 죽는다. 탈출구가 없으면 의도적 제외
    하나가 설치본을 벽돌로 만든다 — 그런데 빠뜨림은 계속 잡아야 한다.
    """

    config = _real_config()
    config["targets"]["all"] = {
        **config["targets"]["all"],
        "include": [
            name for name in config["targets"]["all"]["include"] if name != "map"
        ],
    }
    with pytest.raises(TargetsConfigError, match="excluded_from_all"):
        _validate(config)

    config["targets"]["map"] = {**config["targets"]["map"], "excluded_from_all": True}
    _validate(config)


def test_a_normalized_path_escape_is_still_refused() -> None:
    """경로 **구성요소** 검사만이 잡는 형태를 센다.

    첫 판의 검사는 탈출 케이스로 `sub/../../outside.yml`을 썼는데 그것은 정규화 검사가
    먼저 잡는다 — 구성요소 검사를 지워도 초록이었다. 이미 정규화된 탈출
    (`../outside.yml`)이 그 절만이 잡는 형태다. airport로 센다(이유는
    `test_duplicate_config_files_are_rejected` 참고).
    """

    config = _real_config()
    config["targets"]["airport"] = {
        **config["targets"]["airport"],
        "external_project": {
            **config["targets"]["airport"]["external_project"],
            "config_files": ["../outside.yml"],
        },
    }
    with pytest.raises(TargetsConfigError, match="stay inside"):
        _validate(config)


# ── 라운드 3: 항진명제였던 검사들을 효과에 결박한다 ──────────────────────


def test_the_read_only_boundary_reaches_the_wire_with_its_code() -> None:
    """**코드가 와이어에 나가는지**를 센다 — 상수가 자기 자신과 같은지가 아니라.

    첫 판 검사는 `assert ...code == "EXTERNAL_PROJECT_READ_ONLY"` 한 줄이었다. 그런데
    `main.py`에서 `code`를 payload에 싣는 핸들러는 `ComposeCandidateContractError`
    **전용**이라, base 핸들러를 타는 이 예외의 코드는 **한 번도 나가지 않았다**.
    프런트는 `code: null`을 보고 409의 일반 힌트("일시적 상태일 수 있습니다")를
    붙였다 — 이 경계는 항구적이라 정반대의 안내다(적대 리뷰 2026-09-18 E-F1).

    `errors.ts`에 넣은 한국어 문구가 **도달 불가 코드**였다는 뜻이다.
    """

    from kor_travel_docker_manager.main import _contract_error_detail

    detail = _contract_error_detail(
        ExternalContainerMutationError("container 'x' belongs to project 'y'")
    )
    assert isinstance(detail, dict)
    assert detail["code"] == "EXTERNAL_PROJECT_READ_ONLY"
    assert "belongs to project" in detail["message"]

    # 코드가 없는 계약 오류는 종전대로 평문이다 — 좁히기가 너무 넓으면 여기가 잡는다.
    from kor_travel_docker_manager.services.errors import DeploymentContractError

    assert _contract_error_detail(DeploymentContractError("plain")) == "plain"


def test_the_frontend_has_a_message_for_that_code() -> None:
    """와이어에 나가는 코드와 화면 문구가 **같은 이름**을 쓴다.

    둘이 갈리면 코드는 나가는데 화면은 여전히 영문 원문을 보여 준다.
    """

    errors_ts = (_ROOT / "frontend" / "src" / "lib" / "errors.ts").read_text(
        encoding="utf-8"
    )
    assert "EXTERNAL_PROJECT_READ_ONLY:" in errors_ts


def test_target_specs_reject_unknown_fields() -> None:
    """target 절에도 컨테이너 절과 **같은 등급**의 검사를 둔다.

    내 주석과 검사 docstring이 둘 다 "target 절에는 있는데 컨테이너 절에는 없었다"고
    적었는데 `_ALLOWED_TARGET_FIELDS`가 **존재하지 않았다**(적대 리뷰 2026-09-18 E-F8).
    비대칭이 없어진 것이 아니라 방향이 뒤집혔고, `dependz_on` 오타 한 글자가 여전히
    조용히 의존을 지웠다.
    """

    config = _real_config()
    config["targets"]["map"] = {
        **config["targets"]["map"],
        "dependz_on": ["db"],
    }
    with pytest.raises(TargetsConfigError, match="unknown fields"):
        _validate(config)


@pytest.mark.parametrize("value", ["no", "false", 0, 1, "true"])
def test_excluded_from_all_must_be_a_boolean(value: object) -> None:
    """`"no"`처럼 **의미가 정반대인** YAML 값이 진리값으로는 참이다.

    이 탈출구의 존재 이유가 "빠뜨림"을 잡는 것인데, 그런 값을 통과시키면 그 실패를
    그대로 재도입한다(적대 리뷰 2026-09-18 E-F9).
    """

    config = _real_config()
    config["targets"]["map"] = {
        **config["targets"]["map"],
        "excluded_from_all": value,
    }
    with pytest.raises(TargetsConfigError, match="must be a boolean"):
        _validate(config)


def test_an_external_target_cannot_hold_a_manager_container() -> None:
    """소속 대조의 **반대 방향**.

    H-1을 검증기로 옮기면서 한 방향만 검사가 따라왔다 — 대조에 `is not None`을 더해
    Manager 컨테이너 쪽만 남기는 변이가 살아남았다(적대 리뷰 2026-09-18 E-M55).
    지워진 옛 assert는 두 방향을 다 봤다(weather는 2026-09-20 ADR-47부터
    internal target이라 이 예시로 더 이상 쓸 수 없다 — airport가 여전히 외부다).
    """

    config = _real_config()
    airport = config["targets"]["airport"]
    config["targets"]["airport"] = {
        **airport,
        "containers": [*airport["containers"], "prometheus"],
    }
    with pytest.raises(TargetsConfigError, match="belongs to project"):
        _validate(config)


def test_the_manager_project_name_follows_composes_own_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """compose의 우선순위를 그대로 따른다: env > 문서의 `name:` > 디렉터리.

    첫 판은 가운데를 건너뛰고 디렉터리를 봤다. 이 저장소의 `docker-compose.yml` 1행이
    `name: kor-travel-docker-manager`이므로, `.env`에 `COMPOSE_PROJECT_NAME`이 없고
    체크아웃 디렉터리 이름이 다른 호스트(= 이 worktree)에서는 **Manager 컨테이너
    21개 전부의 `config`가 빈 값**이 됐다 — 라벨 대조가 전부 불일치로 떨어진다
    (적대 리뷰 2026-09-18 E-R2-03).

    n150 prod는 두 값이 우연히 같아 영향이 없었다(실측: 라벨과 설치본 디렉터리 모두
    `kor-travel-docker-manager`). **같다는 것이 우연이 아니어야 한다.**
    """

    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    # 디렉터리 축을 **구성으로** 다르게 만든다. 첫 판은 "이 worktree의 디렉터리
    # 이름은 다르다"는 우연에 기댔는데, GitHub Actions의 체크아웃 디렉터리 이름은
    # 정확히 `kor-travel-docker-manager`라 그 전제가 깨졌다(CI 실측). 두 값이 같다는
    # 것이 우연이면 안 된다는 것이 이 검사의 요지인데, 전제 쪽이 우연이었다.
    elsewhere = tmp_path / "not-the-manager-project-name"
    elsewhere.mkdir()
    monkeypatch.setattr(
        docker_service_module, "get_project_root", lambda: str(elsewhere)
    )
    # `.env` 단계는 이 검사의 축이 아니다 — 전용 검사 둘이 따로 센다
    # (`..._env_file_step_is_read_by_the_function_itself`,
    # `..._missing_or_broken_env_file_falls_through`). 여기서 열어 두면 `.env`가
    # 있는 호스트에서 문서 단계를 못 보게 된다.
    monkeypatch.setattr(
        docker_service_module, "_env_file_compose_project", lambda: None
    )

    # (1) 문서의 `name:`이 디렉터리보다 앞이다.
    assert docker_service_module._manager_compose_project() == _MANAGER_PROJECT_NAME
    assert Path(docker_service_module.get_project_root()).name != _MANAGER_PROJECT_NAME

    # (2) env가 문서보다 앞이다.
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "from-environment")
    assert docker_service_module._manager_compose_project() == "from-environment"

    # (3) 문서가 이름을 선언하지 않으면 디렉터리로 떨어진다.
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    monkeypatch.setattr(
        docker_service_module, "get_compose_config", lambda path=None: {"services": {}}
    )
    assert docker_service_module._manager_compose_project() == Path(
        docker_service_module.get_project_root()
    ).name


def test_the_live_label_matches_the_declared_project_name() -> None:
    """실행 중 라벨과 문서 선언이 같은지 **실측 값으로** 센다.

    n150 실측(2026-09-18): Manager 컨테이너 21개의 `com.docker.compose.project` 라벨은
    전부 `kor-travel-docker-manager`이고, 그것이 정본 문서 1행의 선언과 같다. 이
    검사는 그 두 값이 갈리는 순간 빨개진다 — 그때 라벨 대조가 모든 Manager 컨테이너를
    불일치로 판정하기 때문이다.
    """

    declared = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines()[0]
    assert declared == f"name: {_MANAGER_PROJECT_NAME}", declared


#: `test_reset_reaches_the_guard_for_every_external_container`의 "name-collides"
#: 자리 표시자. 파라미터 목록은 수집 시점에 평가되므로 그 시점엔 아직 존재하지
#: 않는 fixture 값을 직접 넣을 수 없다 — 테스트 본문에서 이 sentinel을
#: `colliding_external_container` fixture의 실제 id로 바꿔 끼운다.
_COLLISION_SENTINEL = "__COLLISION__"


@pytest.mark.parametrize(
    "container_id",
    [
        pytest.param("kor-travel-airport-postgresql", id="name-differs"),
        pytest.param(_COLLISION_SENTINEL, id="name-collides"),
        pytest.param("kor-travel-airport-backend", id="airport"),
    ],
)
def test_reset_reaches_the_guard_for_every_external_container(
    manager_compose: dict[str, Any],
    colliding_external_container: str,
    container_id: str,
) -> None:
    """`reset`이 두 early-return **앞에서** 거부돼야 한다.

    라운드 3에서 이 자리의 조건을 "결박되지 않는 중복"이라며 지웠는데 **그 판단이
    틀렸다.** 중복이 아니라 **순서**가 문제였다 — `_default_compose_config` 부재와
    `svc_name not in default_services`가 guard보다 먼저 돌아서 외부 컨테이너 9개 중
    8개가 `Service db not found in default config backup.`이라는 **영문 내부 메시지 +
    HTTP 500 + code 없음**으로 답했다(적대 리뷰 2026-09-18 E-R2-02 실측).

    정상 동작하는 유일한 경우가 **이름이 우연히 겹치는** `prometheus`였다 —
    docstring이 "가장 위험하다"고 지목한 그 경우다. 그래서 이 검사는 이름이 다른 것·
    겹치는 것·또 다른 프로젝트 셋을 함께 태운다. weather가 2026-09-20(ADR-47)부터
    internal target이라 실제 이름 충돌 사례가 저장소에서 사라져,
    `colliding_external_container`가 합성으로 그 사례를 대신한다.
    """

    if container_id == _COLLISION_SENTINEL:
        container_id = colliding_external_container

    with pytest.raises(ExternalContainerMutationError):
        DockerService().reset_container_config(container_id)


def test_the_external_boundary_maps_to_409_with_its_code() -> None:
    """앱에 **등록된 핸들러**를 태워 status와 code를 함께 센다.

    첫 판 검사는 `_contract_error_detail`을 직접 불러서 핸들러 배선을 보지 않았다.
    전체 HTTP 스택은 이 파일의 대상이 아니다(origin 가드 + 세션 쿠키 하네스가 필요하고
    그것은 `test_api.py`가 갖고 있다) — 대신 **핸들러 선택과 그 응답**을 센다.
    """

    import asyncio

    from kor_travel_docker_manager.main import app
    from kor_travel_docker_manager.services.errors import DeploymentContractError

    handler = None
    for exception_type, candidate in app.exception_handlers.items():
        if exception_type is DeploymentContractError:
            handler = candidate
    assert handler is not None, "DeploymentContractError 핸들러가 등록돼 있어야 한다"

    error = ExternalContainerMutationError(
        "container 'kor-travel-airport-backend' belongs to external compose project "
        "'kor-travel-airport'"
    )
    response = asyncio.run(handler(None, error))
    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["detail"]["code"] == "EXTERNAL_PROJECT_READ_ONLY", payload
    assert "kor-travel-airport" in payload["detail"]["message"]


# ── 라운드 5: 내가 라운드 4에서 만든 표면 둘 ────────────────────────────


def test_the_env_file_step_is_read_by_the_function_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """**docstring이 적은 단계를 함수가 직접 읽는다.**

    첫 판은 `os.environ`만 보고 `--env-file` 단계를 `main.py`의
    `load_dotenv(_ENV_PATH)` 한 줄에 의존했다. 그 줄을 지우는 변이가 **1843건을 전부
    초록으로 통과했다**(적대 리뷰 2026-09-18 라운드4 F-03, 변이 53종 중 유일 생존) —
    즉 `.env`가 프로젝트 이름을 정하는 호스트에서 그 줄이 사라지면 Manager 컨테이너
    21/21의 `config`가 빈 값이 되는데 아무 검사도 빨개지지 않았다.

    import 시점 부작용에 기대면 결박할 것이 함수 밖에 남는다.
    """

    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "KTDM_OTHER=x\n"
        "COMPOSE_PROJECT_NAME=from-env-file\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        docker_service_module, "get_env_path", lambda: str(env_file)
    )
    # 문서에도 이름이 있지만 env-file이 **앞**이다.
    assert docker_service_module._manager_compose_project(
        {"name": _MANAGER_PROJECT_NAME}
    ) == "from-env-file"

    # 프로세스 env는 env-file보다 앞이다.
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "from-process-env")
    assert docker_service_module._manager_compose_project(
        {"name": _MANAGER_PROJECT_NAME}
    ) == "from-process-env"


def test_a_missing_or_broken_env_file_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """관측 경로에서 도는 함수는 예외를 던지지 않는다 — 다음 단계로 내려간다."""

    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    monkeypatch.setattr(
        docker_service_module, "get_env_path", lambda: str(tmp_path / "absent")
    )
    assert docker_service_module._manager_compose_project(
        {"name": _MANAGER_PROJECT_NAME}
    ) == _MANAGER_PROJECT_NAME

    broken = tmp_path / "broken.env"
    broken.write_bytes(bytes([0xFF, 0xFE]) + b" not utf-8")
    monkeypatch.setattr(docker_service_module, "get_env_path", lambda: str(broken))
    assert docker_service_module._manager_compose_project(
        {"name": _MANAGER_PROJECT_NAME}
    ) == _MANAGER_PROJECT_NAME


def test_the_compose_document_is_parsed_once_per_status_call(
    manager_compose: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**컨테이너당 92KB 재파싱을 막는다.**

    라운드 4에서 프로젝트 이름 해석이 `get_compose_config()`를 읽게 했고, 그것을
    `get_containers_status()`가 컨테이너마다 불렀다 — `COMPOSE_PROJECT_NAME`이 프로세스
    env에 없는 호스트에서 폴링 1회가 **98ms → 1917ms(19.6배, +1.8초)**가 됐다.
    websocket status broadcast 주기가 **2.0초**다(적대 리뷰 2026-09-18 라운드4 F-01).
    """

    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    calls = 0
    original = docker_service_module.get_compose_config

    def counting(path: str | None = None) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(DockerService, "_get_client", lambda self: _FakeClient())
    # 생성자의 `_backup_default_config()`가 한 번 읽는다 — 그것은 세지 않는다.
    service = DockerService()
    monkeypatch.setattr(docker_service_module, "get_compose_config", counting)

    entries = service.get_containers_status()
    assert len(entries) > 10, "컨테이너가 여러 개여야 이 검사가 뜻이 있다"
    assert calls == 1, (
        f"compose 문서를 {calls}번 파싱했다 — 컨테이너 수와 무관하게 1이어야 한다"
    )

