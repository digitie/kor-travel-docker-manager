"""ADR-58: 변경 소유권은 조회용 의존·관찰 집합으로 넓어지지 않는다."""

import copy
from pathlib import Path

import pytest
import yaml

from kor_travel_docker_manager.services import registry
from kor_travel_docker_manager.services.errors import DeploymentContractError


@pytest.fixture
def config(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[2]
    value = yaml.safe_load((root / "config/docker-targets.yml").read_text())
    monkeypatch.setattr(registry, "load_targets_config", lambda: value)
    monkeypatch.setenv(registry.PROJECT_ROOT_ENV, str(tmp_path))
    monkeypatch.setenv("KTDM_HOST", registry.default_host(value))
    return value


def test_foreign_alias_rejected_before_mutation(config):
    with pytest.raises(DeploymentContractError, match="host"):
        registry.require_local_targets(" GEO ", config=config)
    registry.require_local_targets(" MAP ", config=config)


def test_each_pair_member_must_be_local(config):
    config["targets"]["pinvi"]["host"] = config["targets"]["geo"]["host"]
    with pytest.raises(DeploymentContractError, match="pinvi"):
        registry.require_local_targets("map", "pinvi", config=config)


def test_observation_does_not_grant_start_ownership(config):
    assert "kor-travel-geo-api" in config["targets"]["map"]["containers"]
    with pytest.raises(DeploymentContractError, match="host"):
        registry.require_local_container("kor-travel-geo-api", config=config)
    registry.require_local_container("kor-travel-shared-postgresql", config=config)


def test_explicit_ensure_plan_filters_services_and_initializers_together(config):
    before = copy.deepcopy(config)
    plan = registry.local_mutation_target("map", config=config)
    assert "geo" not in plan["resolved_sequence"]
    assert "kor-travel-map-api" in plan["resolved_services"]
    assert "kor-travel-geo-api" not in plan["resolved_services"]
    assert all(step["target"] != "geo" for step in plan["resolved_init_steps"])
    assert config == before


def test_third_host_and_local_all_are_derived(config, monkeypatch):
    config["hosts"]["third"] = {"addr": "192.0.2.3"}
    config["targets"]["geo"]["host"] = "third"
    plan = registry.local_mutation_target("all", config=config)
    assert "geo" not in plan["resolved_sequence"]
    monkeypatch.setenv("KTDM_HOST", "third")
    plan = registry.local_mutation_target("geo", config=config)
    assert "geo" in plan["resolved_sequence"]
    assert "kor-travel-geo-api" in plan["resolved_services"]


def test_local_plan_checks_self_host_once(config, monkeypatch):
    calls = []
    original = registry.current_host
    monkeypatch.setattr(registry, "current_host", lambda value: calls.append(1) or original(value))
    registry.local_mutation_target("all", config=config)
    assert len(calls) == 1


@pytest.mark.parametrize("container_id", ["kor-travel-geo-api", "transport-backend"])
@pytest.mark.parametrize("action", ["start", "restart"])
def test_public_start_rejects_before_sdk_or_external_fast_path(config, monkeypatch, container_id, action):
    from kor_travel_docker_manager.services.docker_service import DockerService

    if container_id not in config["containers"]:
        container_id = next(name for name, spec in config["containers"].items() if spec.get("external_project"))
    service = DockerService.__new__(DockerService)
    monkeypatch.setattr(service, "_control_container_unlocked", lambda *_args, **_kwargs: pytest.fail("SDK mutation reached"))
    with pytest.raises(DeploymentContractError, match="host"):
        service.control_container(container_id, action)


@pytest.mark.parametrize("operation", ["config", "reset"])
def test_public_config_rejects_before_file_or_sdk_mutation(config, monkeypatch, operation):
    from kor_travel_docker_manager.services.docker_service import DockerService

    service = DockerService.__new__(DockerService)
    with pytest.raises(DeploymentContractError, match="host"):
        if operation == "config":
            service.update_container_config("kor-travel-geo-api", [], {}, [], [])
        else:
            service.reset_container_config("kor-travel-geo-api")
