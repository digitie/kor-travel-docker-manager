"""멀티프로젝트 target — 형제 프로젝트를 관리 대상으로 삼는 계약.

Manager의 target 모델은 지금까지 **단일 프로젝트·단일 compose 파일**을 전제했다.
`services_for_target`이 평평한 서비스 목록을 돌려주고, 그것이 Manager 자신의
`docker-compose.yml`에 대한 **한 번의** `docker compose` 호출로 들어갔다.

형제 프로젝트는 그 전제 밖이다(n150 실측):

    kor-travel-weather      compose.yaml + deploy/compose.n150.yaml   (파일 둘)
    kor-travel-airport      docker-compose.yml
    kor-travel-airport-db   docker-compose.db.yml                      (프로젝트 둘)

그래서 target이 자기 프로젝트를 선언할 수 있게 했다. **선언이 없으면 오늘과 똑같이
Manager 자신의 프로젝트**이고, 기존 target의 동작은 한 글자도 바뀌지 않는다.

weather는 2026-09-05에 **등록 해제된 적이 있다** — targets와 compose가 같은 커밋에서
같이 움직여야 한다는 제약을 외부 프로젝트가 만족할 수 없었기 때문이다. 이번 재등록은
그 제약을 프로젝트 좌표로 대체해서 푼 것이고, 아래 검사들이 그 대체가 구멍이 되지
않게 잡는다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from kor_travel_docker_manager.services import registry as registry_module
from kor_travel_docker_manager.services.compose_service import ComposeService
from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.registry import (
    ExternalProject,
    TargetsConfigError,
    external_project_for_container,
    external_project_for_target,
    service_groups_for_target,
    target_is_external,
)

# ── 선언된 좌표가 실측과 일치하는가 ──────────────────────────────────────


def test_external_targets_declare_the_measured_project_coordinates() -> None:
    """선언한 좌표가 n150에서 실제로 도는 프로젝트와 같아야 한다.

    이 값들은 추측이 아니라 실행 중인 컨테이너의 `com.docker.compose.*` 라벨과
    `docker compose config`에서 읽은 것이다. 좌표가 틀리면 Manager가 엉뚱한
    프로젝트에 명령을 보내거나 `no configuration file provided`로 죽는다.
    """

    assert external_project_for_target("weather") == ExternalProject(
        project="kor-travel-weather",
        working_dir="/home/digitie/kor-travel-weather",
        config_files=("compose.yaml", "deploy/compose.n150.yaml"),
    )
    assert external_project_for_target("airport") == ExternalProject(
        project="kor-travel-airport",
        working_dir="/home/digitie/apps/kor-travel-airport",
        config_files=("docker-compose.yml",),
    )
    assert external_project_for_target("airport-db") == ExternalProject(
        project="kor-travel-airport-db",
        working_dir="/home/digitie/apps/kor-travel-airport",
        config_files=("docker-compose.db.yml",),
    )


def test_manager_targets_are_untouched_by_the_new_model() -> None:
    """Manager 자신의 target은 외부 프로젝트가 **없다** — 회귀 방지의 핵심.

    이 검사가 없으면 새 모델이 기존 target에 스며들어도 아무도 모른다.
    """

    for target in ("db", "storage", "geo", "conc", "map", "pinvi", "all"):
        assert external_project_for_target(target) is None, target
    for target in ("db", "storage", "geo", "conc", "map", "pinvi"):
        assert target_is_external(target) is False, target


# ── 그룹핑: 평평한 목록으로는 표현할 수 없는 것 ──────────────────────────


def test_a_multi_project_target_produces_one_group_per_project() -> None:
    """`airport`는 **두 프로젝트**에 걸친다 — 묶음도 둘이고 의존성 순서를 지킨다.

    이것이 단일 프로젝트 전제를 깨는 자리다. 평평한 목록(`services_for_target`)은
    `['postgres', 'backend', 'frontend']`를 돌려주는데, 그것을 한 번의 compose
    호출로 보내면 `postgres`가 `kor-travel-airport` 프로젝트에 없어서 죽는다.
    """

    groups = service_groups_for_target("airport")
    assert [group.project_label for group in groups] == [
        "kor-travel-airport-db",
        "kor-travel-airport",
    ], "airport-db가 airport보다 먼저 와야 한다(depends_on)"
    assert [list(group.services) for group in groups] == [
        ["postgres"],
        ["backend", "frontend"],
    ]


def test_weather_is_one_group_with_both_compose_files() -> None:
    """weather는 프로젝트 하나지만 **파일이 둘**이다(n150 오버레이)."""

    groups = service_groups_for_target("weather")
    assert len(groups) == 1
    external = groups[0].external
    assert external is not None
    assert external.config_files == ("compose.yaml", "deploy/compose.n150.yaml")
    assert list(groups[0].services) == [
        "db",
        "migrate",
        "api",
        "dagster",
        "dagster-gateway",
        "web",
        "prometheus",
    ]


def test_runtime_groups_drop_the_one_shot_migration() -> None:
    """`migrate`는 `restart: no`인 one-shot이라 runtime 목록에서 빠진다.

    정상 상태가 `exited(0)`이므로 runtime에 두면 상태 판정이 늘 실패로 읽힌다.
    """

    groups = service_groups_for_target("weather", runtime_only=True)
    assert len(groups) == 1
    assert "migrate" not in groups[0].services
    assert "api" in groups[0].services


def test_manager_target_stays_a_single_group() -> None:
    """Manager target은 묶음이 하나이고 `external`이 `None`이다."""

    groups = service_groups_for_target("map")
    assert len(groups) == 1
    assert groups[0].external is None
    assert groups[0].project_label == "kor-travel-docker-manager"


# ── 명령 구성 ────────────────────────────────────────────────────────────


def test_external_command_carries_project_directory_and_every_file() -> None:
    """`-p` · `--project-directory` · 파일마다 `-f`.

    그리고 Manager의 `--env-file`을 **붙이지 않는다** — 형제 프로젝트는 자기
    `working_dir`의 `.env`를 compose가 알아서 읽고, Manager env를 주입하면 남의
    프로젝트 값을 덮어쓴다.
    """

    service = ComposeService()
    groups = service_groups_for_target("weather")
    command = service.build_command(["ps", *groups[0].services], external=groups[0].external)

    assert command[:2] == ["docker", "compose"]
    assert "-p" in command and command[command.index("-p") + 1] == "kor-travel-weather"
    assert command[command.index("--project-directory") + 1] == (
        "/home/digitie/kor-travel-weather"
    )
    assert command.count("-f") == 2
    assert "compose.yaml" in command and "deploy/compose.n150.yaml" in command
    assert "--env-file" not in command, "남의 프로젝트에 Manager env를 주입하면 안 된다"


def test_manager_command_shape_is_unchanged() -> None:
    """외부 인자를 주지 않으면 명령이 종전과 같다."""

    service = ComposeService()
    command = service.build_command(["ps", "kor-travel-map-api"])
    assert "-p" not in command, "Manager 경로에는 프로젝트 플래그가 붙지 않는다"
    assert "--project-directory" not in command
    assert "--env-file" in command


def test_single_file_boundary_is_refused_for_an_external_project() -> None:
    """단일파일 경계는 Manager 후보의 계약이다 — 외부에 적용하려 하면 거부한다."""

    service = ComposeService()
    with pytest.raises(DeploymentContractError, match="does not apply to an external project"):
        service.build_command(
            ["config"],
            canonical_single_file=True,
            external=external_project_for_target("weather"),
        )


# ── C6c 계약 경로는 외부를 거부한다 ──────────────────────────────────────


@pytest.mark.parametrize("target", ["weather", "airport", "airport-db"])
def test_ensure_target_refuses_external_projects(target: str) -> None:
    """`ensure`는 Manager 자신의 후보만 다룬다.

    `ensure`가 전제하는 것은 보호값 스캔·볼륨 그래프·단일파일 경계·핀셋을 통과한
    **Manager의** 후보다. 형제 프로젝트의 compose는 그 계약을 받은 적이 없고 정본도
    이 저장소가 아니다. 수명주기는 `control_container`(Docker SDK)가 다루고, 배포는
    각 저장소가 계속 소유한다.

    이 검사가 없으면 `ensure weather`가 Manager의 compose에 대고 weather 서비스
    이름을 찾다가 `no such service`로 죽는다 — 원인을 말하지 않는 실패다.
    """

    service = ComposeService()
    with pytest.raises(DeploymentContractError, match="external compose project"):
        service.ensure_target(target)


def test_ensure_target_still_works_for_manager_targets() -> None:
    """거부가 Manager target까지 막지 않는다 — 전제가 깨지면 이 검사가 잡는다.

    실제 배포는 하지 않는다(production 거부 경로로 충분히 확인된다). 여기서 보는
    것은 "외부 거부가 먼저 걸리지 않는다"는 것뿐이다.
    """

    service = ComposeService()
    with pytest.raises(DeploymentContractError) as rejection:
        service.ensure_target("map")
    assert "external compose project" not in str(rejection.value)


# ── 로그: 프로젝트를 하나로 좁혀야 한다 ──────────────────────────────────


def test_logs_refuses_a_target_that_spans_projects() -> None:
    """여러 프로젝트의 로그를 한 스트림으로 합칠 수 없다 — 고르게 한다.

    조용히 하나만 보여주면 나머지가 없는 것처럼 읽힌다. `-f`에서는 더 나쁘다.
    """

    service = ComposeService()
    with pytest.raises(DeploymentContractError, match="spans multiple compose projects"):
        service.logs("airport")


def test_container_scoped_logs_resolve_their_owning_project() -> None:
    """컨테이너를 직접 가리키면 그 컨테이너의 프로젝트를 쓴다."""

    assert external_project_for_container("kor-travel-weather-api") == (
        external_project_for_target("weather")
    )
    assert external_project_for_container("kor-travel-airport-postgresql") == (
        external_project_for_target("airport-db")
    )
    # Manager 자신의 컨테이너는 외부가 아니다.
    assert external_project_for_container("kor-travel-map-postgresql") is None


# ── 스키마 검증: 오타가 조용히 통과하지 않는다 ───────────────────────────


def _config_with_external(external: Any) -> dict[str, Any]:
    """weather target의 `external_project`만 바꾼 설정 사본."""

    registry_module.load_targets_config.cache_clear()
    try:
        config = copy.deepcopy(dict(registry_module.load_targets_config()))
    finally:
        registry_module.load_targets_config.cache_clear()
    config["targets"] = copy.deepcopy(dict(config["targets"]))
    config["targets"]["weather"] = copy.deepcopy(dict(config["targets"]["weather"]))
    if external is None:
        config["targets"]["weather"].pop("external_project", None)
    else:
        config["targets"]["weather"]["external_project"] = external
    config["containers"] = copy.deepcopy(dict(config["containers"]))
    return config


@pytest.mark.parametrize(
    ("label", "external"),
    [
        ("mapping이 아님", ["kor-travel-weather"]),
        ("모르는 필드", {
            "project": "p", "working_dir": "/tmp", "config_files": ["a.yml"], "typo": 1,
        }),
        ("필드 누락", {"project": "p", "working_dir": "/tmp"}),
        ("빈 project", {"project": "  ", "working_dir": "/tmp", "config_files": ["a.yml"]}),
        ("상대 working_dir", {
            "project": "p", "working_dir": "relative", "config_files": ["a.yml"],
        }),
        ("정규화 안 된 working_dir", {
            "project": "p", "working_dir": "/tmp/../tmp", "config_files": ["a.yml"],
        }),
        ("빈 config_files", {"project": "p", "working_dir": "/tmp", "config_files": []}),
        ("절대 경로 config_files", {
            "project": "p", "working_dir": "/tmp", "config_files": ["/etc/compose.yml"],
        }),
        ("탈출하는 config_files", {
            "project": "p", "working_dir": "/tmp", "config_files": ["../outside.yml"],
        }),
    ],
)
def test_malformed_external_project_is_rejected(label: str, external: Any) -> None:
    """형태 오류는 **선언 시점에** 걸린다.

    오타 하나가 조용히 "Manager 자신의 프로젝트"로 해석되면 다른 프로젝트를 대상으로
    명령이 돌아간다. 특히 절대 경로 `config_files`는 `working_dir`을 무의미하게 만들어
    선언을 읽는 사람이 기준 디렉터리를 알 수 없게 한다.
    """

    config = _config_with_external(external)
    with pytest.raises(TargetsConfigError):
        registry_module._validate_targets_config(config, label="<test>")


def test_external_targets_cannot_declare_init_steps() -> None:
    """`init_steps`는 Manager compose의 `exec`로 돈다 — 외부에 쓰면 엉뚱한 컨테이너다."""

    config = _config_with_external(
        {
            "project": "kor-travel-weather",
            "working_dir": "/home/digitie/kor-travel-weather",
            "config_files": ["compose.yaml"],
        }
    )
    config["targets"]["weather"]["init_steps"] = [
        {"name": "x", "description": "y", "command": ["exec", "-T", "db", "true"]}
    ]
    with pytest.raises(TargetsConfigError, match="init_steps"):
        registry_module._validate_targets_config(config, label="<test>")


def test_the_real_config_passes_its_own_validation() -> None:
    """저장소의 실제 설정이 위 규칙을 통과한다 — 규칙이 공허하지 않다는 증거."""

    registry_module.load_targets_config.cache_clear()
    try:
        config = dict(registry_module.load_targets_config())
    finally:
        registry_module.load_targets_config.cache_clear()
    registry_module._validate_targets_config(copy.deepcopy(config), label="<real>")
