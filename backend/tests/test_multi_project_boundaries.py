"""멀티프로젝트 target — Manager의 compose가 남의 프로젝트에 새지 않는 경계.

`compose_service`는 **그 프로젝트 안에서만** 유일한 이름이다. 새로 등록한 컨테이너
중 `prometheus`(weather)·`postgres`(airport-db)·`backend`·`frontend`·`web`은 전부 흔한
이름이고, 그중 `prometheus`는 **Manager 자신의 서비스 이름과 정확히 겹친다**.

Manager는 그 값을 자기 compose에 곧장 조회했다. 그래서 겹치는 순간 셋이 한꺼번에
틀어졌다(적대 리뷰 2026-09-18 C-3):

    목록 화면    weather Prometheus 카드에 **Manager Prometheus의** ports·env가 뜬다
    저장         그 화면의 편집이 **Manager의 docker-compose.yml**로 간다
    없는 것 start `kor-travel-weather-prometheus`를 켜라 했는데 **Manager의 Prometheus가
                 생기고** "만들어서 시작했다"고 성공을 보고한다

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
from typing import Any

import pytest

from kor_travel_docker_manager.services import docker_service as docker_service_module
from kor_travel_docker_manager.services import registry as registry_module
from kor_travel_docker_manager.services.docker_service import (
    DockerService,
    ExternalContainerMutationError,
)
from kor_travel_docker_manager.services.registry import TargetsConfigError

_MANAGER_PROMETHEUS_CONFIG = {
    "ports": ["12401:12401"],
    "environment": {"KTDM_ONLY": "manager"},
    "volumes": ["/manager/prometheus:/prometheus"],
    "networks": ["kor-travel-net"],
}


@pytest.fixture
def manager_compose(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Manager compose에 `prometheus`가 **있는** 상태. 겹침이 실재해야 검사가 뜻이 있다."""

    document = {"services": {"prometheus": copy.deepcopy(_MANAGER_PROMETHEUS_CONFIG)}}
    monkeypatch.setattr(
        docker_service_module, "get_compose_config", lambda path=None: document
    )
    return document


# ── 표시: 외부 컨테이너의 config는 Manager compose에서 오지 않는다 ───────


def test_external_container_config_does_not_come_from_the_manager_compose(
    manager_compose: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """이름이 겹쳤다는 이유로 남의 카드에 내 설정을 그리면 안 된다.

    화면이 보여준 값은 그대로 편집기의 초기값이 된다 — 표시가 틀리면 그 다음 저장이
    **Manager의 compose를 남의 값으로 덮어쓴다**. 표시와 변경은 같은 실수의 앞뒤다.
    """

    monkeypatch.setattr(
        DockerService, "_get_client", lambda self: (_ for _ in ()).throw(RuntimeError())
    )
    entries = {entry["id"]: entry for entry in DockerService().get_containers_status()}

    external = entries["kor-travel-weather-prometheus"]["config"]
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
) -> None:
    """`update` · `reset` · NotFound 재생성이 전부 이 함수로 모인다.

    guard를 호출부마다 복사하면 어느 한 벌을 지워도 아무 검사가 빨개지지 않는다.
    한 자리에 두는 것이 요점이라, 이 검사도 그 한 자리를 본다.
    """

    with pytest.raises(ExternalContainerMutationError, match="kor-travel-weather"):
        DockerService()._update_container_config_unlocked(
            "kor-travel-weather-prometheus",
            ["14104:9090"],
            {},
            [],
            [],
            environment_snapshot=None,  # type: ignore[arg-type]
        )


def test_starting_a_missing_external_container_does_not_recreate_a_manager_service(
    manager_compose: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """NotFound 분기가 Manager compose에서 같은 이름을 찾아 만들어 버리는 경로.

    사용자가 켜려던 것은 weather의 Prometheus인데 Manager의 Prometheus가 생기고
    **성공이 보고된다**. 조용한 오답이 실패보다 나쁘다.
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
            "kor-travel-weather-prometheus",
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
            assert name == "kor-travel-weather-prometheus-1"
            return _Container()

    class _Client:
        containers = _Containers()

    monkeypatch.setattr(DockerService, "_get_client", lambda self: _Client())
    result = DockerService()._control_container_unlocked(
        "kor-travel-weather-prometheus",
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
        "external_project": "kor-travel-weather",
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
    적대 리뷰는 weather target에 Manager 서비스 이름과 존재하지 않는 이름을 넣어도
    무결성 검사가 하나도 빨개지지 않는 것을 실측했다.
    """

    config = _real_config()
    weather = config["targets"]["weather"]
    config["targets"]["weather"] = {
        **weather,
        "services": [name for name in weather["services"] if name != "prometheus"],
    }
    with pytest.raises(TargetsConfigError, match="the target does not list"):
        _validate(config)


def test_one_shot_services_need_no_container_registration() -> None:
    """반대 방향은 강제하지 않는다 — one-shot은 `services`에만 있는 것이 이 저장소 규칙이다.

    `migrate`는 정상 상태가 `exited(0)`이라 `containers:`에 넣으면 대시보드에 상시
    비정상 카드로 남고 metrics 관측 대상이 된다(`rustfs-init`이 같은 이유로 빠져 있다).
    """

    config = _real_config()
    assert "migrate" in config["targets"]["weather"]["services"]
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

    `ensure`는 의존 폐포를 보고 거부하므로, `map`에 `depends_on: [weather]`를 더하면
    `ensure map`이 "target 'map' belongs to an external compose project"로 죽는다.
    원인을 찾기 아주 어려운 모양이라 선언 시점에 막는다.
    """

    config = _real_config()
    config["targets"]["map"] = {
        **config["targets"]["map"],
        "depends_on": [*config["targets"]["map"]["depends_on"], "weather"],
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
        "include": [*config["targets"]["all"]["include"], "weather"],
    }
    with pytest.raises(TargetsConfigError, match="can no longer be deployed"):
        _validate(config)


def test_duplicate_config_files_are_rejected() -> None:
    """compose는 `-f`를 순서대로 병합한다 — 같은 파일을 두 번 적으면 뒤엣것이 이긴다."""

    config = _real_config()
    config["targets"]["weather"] = {
        **config["targets"]["weather"],
        "external_project": {
            "project": "kor-travel-weather",
            "working_dir": "/home/digitie/kor-travel-weather",
            "config_files": ["compose.yaml", "compose.yaml"],
        },
    }
    with pytest.raises(TargetsConfigError, match="duplicate entry"):
        _validate(config)


def test_a_dotted_prefix_is_not_a_path_escape() -> None:
    """`startswith("..")`는 `..hidden/compose.yml`을 거부하는 오탐이었다.

    경로 **구성요소**로 봐야 한다. 오탐은 조용하지 않지만, 정당한 선언을 막는다.
    """

    config = _real_config()
    config["targets"]["weather"] = {
        **config["targets"]["weather"],
        "external_project": {
            "project": "kor-travel-weather",
            "working_dir": "/home/digitie/kor-travel-weather",
            "config_files": ["..hidden/compose.yml"],
        },
    }
    _validate(config)

    config["targets"]["weather"]["external_project"]["config_files"] = [
        "sub/../../outside.yml"
    ]
    with pytest.raises(TargetsConfigError, match="stay inside"):
        _validate(config)
