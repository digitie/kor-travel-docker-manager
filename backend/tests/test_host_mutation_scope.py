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


def test_public_remote_ensure_rejects_before_environment_or_lock(config, monkeypatch):
    from kor_travel_docker_manager.services.compose_service import ComposeService

    service = ComposeService()
    monkeypatch.setattr(service, "capture_transaction_unlocked", lambda **_kwargs: pytest.fail("transaction reached"))
    with pytest.raises(DeploymentContractError, match="host"):
        service.ensure_target("GEO")


def _cross_host_declared_dependencies(compose, config):
    owners = {}
    for target, spec in config["targets"].items():
        if spec.get("external_project"):
            continue
        for service in spec.get("services", []):
            owners.setdefault(service, set()).update(registry.target_hosts(target, config))
    return {
        (consumer, provider)
        for consumer, spec in compose["services"].items()
        for provider in spec.get("depends_on", {})
        if consumer in owners and provider in owners
        and not owners[consumer] <= owners[provider]
    }


def test_declared_compose_dependencies_do_not_start_off_host_tenants(config):
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert _cross_host_declared_dependencies(compose, config) == set()
    # 같은-host 의존과 host-all 기반은 보존한다.
    assert {"kor-travel-shared-postgres", "rustfs", "kor-travel-concierge-api"} <= set(compose["services"]["kor-travel-map-api"]["depends_on"])
    # 원래 위험 간선을 되돌리면 회귀가 실제로 거부한다.
    compose["services"]["kor-travel-map-api"]["depends_on"]["kor-travel-geo-api"] = {"condition": "service_healthy"}
    assert ("kor-travel-map-api", "kor-travel-geo-api") in _cross_host_declared_dependencies(compose, config)


@pytest.mark.parametrize("role", ["geo", "transport"])
@pytest.mark.parametrize("operation", ["create", "gc", "rehearse"])
def test_backup_role_refuses_remote_before_shared_pg_or_artifact_access(
    config, monkeypatch, tmp_path, role, operation,
):
    from kor_travel_docker_manager.services import standalone_backup as backup

    def unexpected(*_args, **_kwargs):
        pytest.fail("remote role reached shared PG or artifact access")

    monkeypatch.setattr(backup, "_discover_port", unexpected)
    monkeypatch.setattr(backup, "_resolve_backup_root", unexpected)
    monkeypatch.setattr(backup, "plan_standalone_restore", unexpected)
    with pytest.raises(backup.StandaloneBackupError, match="host"):
        if operation == "create":
            backup.create_standalone_backup(role, backup_root=tmp_path)
        elif operation == "gc":
            backup.gc_standalone_backups(role, keep=3, backup_root=tmp_path)
        else:
            backup.rehearse_standalone_restore(role, backup_root=tmp_path)


@pytest.mark.parametrize("role,target", [
    ("geo", "geo"), ("transport", "transport"), ("concierge", "conc"),
    ("map_application", "map"), ("pinvi", "pinvi"), ("dagster_shared", "dagster"),
])
def test_backup_role_uses_logical_owner_not_shared_pg(config, monkeypatch, role, target):
    from kor_travel_docker_manager.services import standalone_backup as backup

    for selected in config["hosts"]:
        monkeypatch.setenv("KTDM_HOST", selected)
        if selected in registry.target_hosts(target, config):
            backup.require_local_backup_role(role)
        else:
            with pytest.raises(backup.StandaloneBackupError, match="host"):
                backup.require_local_backup_role(role)


@pytest.mark.parametrize("mode", ["unknown_host", "missing_operating_host"])
def test_backup_host_config_errors_keep_typed_backup_failure(config, monkeypatch, mode):
    from kor_travel_docker_manager.services import standalone_backup as backup

    if mode == "unknown_host":
        monkeypatch.setenv("KTDM_HOST", "undeclared")
    else:
        Path(registry.get_project_root(), ".ktdm-source-revision").touch()
        monkeypatch.delenv("KTDM_HOST", raising=False)
    with pytest.raises(backup.StandaloneBackupError, match="KTDM_HOST"):
        backup.require_local_backup_role("map_application")


@pytest.mark.parametrize("role", ["geo", "transport"])
def test_public_backup_handler_refuses_remote_before_job_or_file_access(
    config, monkeypatch, role,
):
    import asyncio

    from fastapi import HTTPException

    from kor_travel_docker_manager.api import routes

    def unexpected(*_args, **_kwargs):
        pytest.fail("remote backup reached job/file mutation")

    monkeypatch.setattr(routes, "ui_backup_owner_conflict", unexpected)
    monkeypatch.setattr(routes.job_runner, "submit", unexpected)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(routes.post_backup(role, None, None, None))
    assert raised.value.status_code == 409
    assert "host" in raised.value.detail
