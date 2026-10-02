"""형제 프로젝트(transport)의 Dagster family — 선언에서 만들고, 펜스 검사가 그 project의 옛 컨테이너를 본다(ADR-54).

transport의 compose는 그 저장소에 있다. Manager는 그 모양(`config/docker-targets.yml`의 `dagster.external`)을
선언으로 받는다. 공용 workspace가 그 location을 싣는 순간부터 pinned 재구축의 plane 수렴
(`_require_plane_location_owners_fenced`)은 그 location의 옛 webserver·daemon·gateway가 멈춰 있어야 plane을
다시 만든다 — 그 컨테이너 이름은 transport project의 compose 기본 이름이다. 소유자가 없는 location은 거부하므로
(`which no Dagster target serves`), 선언이 빠지면 Map·PinVi 재구축이 막힌다 — 그것도 여기서 본다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from kor_travel_docker_manager.services import runtime_topology as topology
from kor_travel_docker_manager.services.compose_service import ComposeService
from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.registry import TargetsConfigError, _validate_targets_config


def _targets() -> dict[str, Any]:
    _, targets = topology._installed_documents()
    return copy.deepcopy(dict(targets))


def _fence(owners: Any, locations: tuple[str, ...], running: set[str]) -> list[str]:
    """펜스 검사를 docker 없이 — 본 컨테이너 이름을 돌려준다(검사가 무엇을 봤는지 센다)."""

    seen: list[str] = []
    service = object.__new__(ComposeService)

    def inspect(container: str, *, label: str) -> bool | None:
        seen.append(container)
        return container in running

    service._inspect_container_running = inspect  # type: ignore[method-assign]
    service._require_plane_location_owners_fenced(locations, owners)
    return seen


def _transport_location() -> str:
    location, _ = topology.external_dagster_locations(_targets())["transport"]
    return location


def test_the_fence_checks_the_sibling_projects_old_containers() -> None:
    owners = topology.installed_location_owners()
    location = _transport_location()
    seen = _fence(owners, (location,), running=set())
    assert seen == [
        "kor-travel-transport-dagster-webserver-1",
        "kor-travel-transport-dagster-daemon-1",
        "kor-travel-transport-dagster-gateway-1",
    ]


@pytest.mark.parametrize(
    "running",
    ["kor-travel-transport-dagster-daemon-1", "kor-travel-transport-dagster-webserver-1", "kor-travel-transport-dagster-gateway-1"],
)
def test_a_running_old_transport_service_refuses_the_plane(running: str) -> None:
    """빨간 대조군: 공용 workspace가 transport를 싣는데 옛 daemon(등)이 돌면 이중 발화라 거부한다."""

    owners = topology.installed_location_owners()
    with pytest.raises(DeploymentContractError, match=f"double fire.*{running}"):
        _fence(owners, (_transport_location(),), running={running})


def test_without_the_declaration_the_workspace_location_has_no_owner() -> None:
    """선언(`dagster.external`)이 빠지면 transport location은 소유자가 없어 plane 수렴이 거부한다(fail-closed)."""

    targets = _targets()
    del targets["targets"]["transport"]["dagster"]
    compose, _ = topology._installed_documents()
    owners_without = {
        location: family
        for location, family in topology.installed_location_owners().items()
        if family.target != "transport"
    }
    assert _transport_location() not in owners_without
    with pytest.raises(DeploymentContractError, match="which no Dagster target serves"):
        _fence(owners_without, (_transport_location(),), running=set())
    assert "transport" not in topology.external_dagster_locations(targets)
    assert compose  # 문서를 읽었다


@pytest.mark.parametrize(
    ("mutate", "said"),
    [
        (lambda b: b.pop("external"), "a sibling project must declare its Dagster shape"),
        (lambda b: b["external"].pop("port"), "missing ['port']"),
        (lambda b: b["external"].update(port="14005"), "must be a literal TCP port"),
        (lambda b: b["external"].update(port=True), "must be a literal TCP port"),
        (lambda b: b["external"].update(extra=1), "unknown ['extra']"),
        (lambda b: b["external"].update(gateways="dagster-gateway"), "must be a list of compose service names"),
        (lambda b: b["external"].update(daemon="dagster-webserver"), "service names must be distinct"),
        (lambda b: b["external"].update(location_name="bad name"), "must be a code location name"),
        (lambda b: b.update(consumers={"transport-admin-web": {"X_URL": "internal"}}), "live in its own repository"),
    ],
)
def test_a_sibling_projects_dagster_declaration_is_validated(mutate: Any, said: str) -> None:
    targets = _targets()
    mutate(targets["targets"]["transport"]["dagster"])
    with pytest.raises(TargetsConfigError) as excinfo:
        _validate_targets_config(targets, label="test.yml")
    assert said in str(excinfo.value)


def test_a_manager_target_may_not_declare_an_external_shape() -> None:
    targets = _targets()
    targets["targets"]["geo"]["dagster"]["external"] = copy.deepcopy(targets["targets"]["transport"]["dagster"]["external"])
    with pytest.raises(TargetsConfigError, match="only a sibling project"):
        _validate_targets_config(targets, label="test.yml")


def test_the_committed_declaration_validates() -> None:
    _validate_targets_config(_targets(), label="test.yml")
