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
    get_project_root,
)


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
            project = (
                external.project
                if external is not None
                else Path(get_project_root()).name
            )
        return _FakeContainer(name, project)


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
            assert name == "kor-travel-weather-prometheus-1"
            return _Container()

    class _Client:
        containers = _Containers()

    monkeypatch.setattr(DockerService, "_get_client", lambda self: _Client())
    monkeypatch.delenv("KTDM_DEPLOYMENT_ENVIRONMENT", raising=False)

    result = DockerService().control_container("kor-travel-weather-prometheus", "restart")
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
                "kor-travel-weather-prometheus", ["14104:9090"], {}, [], []
            )
        else:
            service.reset_container_config("kor-travel-weather-prometheus")


def test_the_read_only_boundary_carries_its_own_error_code() -> None:
    """전용 코드가 없으면 화면이 409의 일반 힌트를 붙인다.

    그 힌트는 "일시적 상태일 수 있습니다"인데 이 경계는 **항구적**이라 정반대의
    안내가 된다. 첫 판은 영문 원문 + 그 힌트가 함께 떴다.
    """

    assert ExternalContainerMutationError.code == "EXTERNAL_PROJECT_READ_ONLY"


def test_the_status_payload_names_the_owning_project(
    manager_compose: dict[str, Any], monkeypatch: pytest.MonkeyPatch
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
    assert entries["kor-travel-weather-prometheus"]["external_project"] == (
        "kor-travel-weather"
    )
    assert entries["prometheus"]["external_project"] is None


def test_the_live_daemon_branch_also_hides_manager_config(
    manager_compose: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**운영에서 도는 분기**를 태운다.

    첫 판의 검사는 `_get_client`를 `RuntimeError`로 막아 **daemon이 죽은 분기만**
    태웠다. 그래서 C-3의 실제 증상("목록 화면이 남의 카드에 Manager 설정을 그린다")을
    만드는 바로 그 줄을 되돌려도 아무 검사가 빨개지지 않았다.
    """

    monkeypatch.setattr(DockerService, "_get_client", lambda self: _FakeClient())
    entries = {entry["id"]: entry for entry in DockerService().get_containers_status()}
    assert entries["kor-travel-weather-prometheus"]["config"]["env"] == {}
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
    """target 절에는 있고 컨테이너 절에는 없던 검사.

    그 비대칭 때문에 `external_projct` 오타가 조용히 무시되고 컨테이너가 **Manager
    소유**로 취급됐다 — C-3의 세 증상이 그대로 복원되는 경로다.
    """

    config = _real_config()
    config["containers"]["kor-travel-weather-api"] = {
        **config["containers"]["kor-travel-weather-api"],
        "external_projct": "kor-travel-weather",
    }
    with pytest.raises(TargetsConfigError, match="unknown fields"):
        _validate(config)


def test_compose_binds_reject_a_service_that_only_exists_externally() -> None:
    """`compose_binds`는 **Manager 자신의** 보안 경계다.

    키가 `(compose service, container_path, read_only)`뿐이라 프로젝트 차원이 없다.
    형제를 겨냥해 쓴 한 줄이 Manager의 production bind allowlist를 넓힌다. 두
    프로젝트에 다 있는 이름(`prometheus`)은 Manager 쪽 정당한 항목이므로 막지 않는다.
    """

    config = _real_config()
    config["compose_binds"] = {
        **config["compose_binds"],
        "dagster-gateway": [
            {"container_path": "/data", "read_only": False, "source": "./x"}
        ],
    }
    with pytest.raises(TargetsConfigError, match="only exists in an external"):
        _validate(config)

    # 겹치는 이름은 통과한다 — 과결박이면 정당한 Manager 항목이 죽는다.
    config = _real_config()
    config["compose_binds"] = {
        **config["compose_binds"],
        "prometheus": [
            {"container_path": "/data", "read_only": False, "source": "./x"}
        ],
    }
    _validate(config)


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
    (`../outside.yml`)이 그 절만이 잡는 형태다.
    """

    config = _real_config()
    config["targets"]["weather"] = {
        **config["targets"]["weather"],
        "external_project": {
            "project": "kor-travel-weather",
            "working_dir": "/home/digitie/kor-travel-weather",
            "config_files": ["../outside.yml"],
        },
    }
    with pytest.raises(TargetsConfigError, match="stay inside"):
        _validate(config)
