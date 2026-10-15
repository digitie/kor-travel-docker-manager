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


@pytest.mark.parametrize("operation", ["pins", "published", "execution", "request", "clear", "discard"])
def test_pin_writers_refuse_remote_before_private_state_changes(config, monkeypatch, tmp_path, operation):
    from types import SimpleNamespace

    from kor_travel_docker_manager.services import runtime_execution_registry as execution
    from kor_travel_docker_manager.services import runtime_pin_registry as pins
    from kor_travel_docker_manager.services import runtime_pin_request as requests

    monkeypatch.setenv("KTDM_HOST", config["targets"]["geo"]["host"])
    path = tmp_path / "state.json"
    path.write_text("{}")
    if operation == "clear":
        monkeypatch.setattr(requests, "read_runtime_pin_request", lambda **_kw: SimpleNamespace(role="map", request_id="same"))
    with pytest.raises(DeploymentContractError, match="host"):
        if operation == "pins":
            pins.write_runtime_pin_registry(None, path=path, publish=False)
        elif operation == "published":
            pins.publish_runtime_pins(None, public_path=path)
        elif operation == "execution":
            execution.write_runtime_execution_registry(None, path=path, public_path=path)
        elif operation == "request":
            requests.write_runtime_pin_request(SimpleNamespace(role="map"), path=path, replace_existing=True)
        elif operation == "clear":
            requests.clear_runtime_pin_request(expect_request_id="same", path=path)
        else:
            requests.discard_unreadable_runtime_pin_request(path=path)
    assert path.read_text() == "{}"


@pytest.mark.parametrize("operation", ["pair", "single", "rollback"])
def test_rotation_refuses_remote_before_intent_or_pending_reads(config, monkeypatch, operation):
    from kor_travel_docker_manager.services import runtime_pair_rotation as rotation

    monkeypatch.setenv("KTDM_HOST", config["targets"]["geo"]["host"])

    def unexpected(*_args, **_kwargs):
        pytest.fail("remote rotation reached private intent")

    monkeypatch.setattr(rotation, "load_pending_runtime_pair_rotation", unexpected)
    monkeypatch.setattr(rotation, "load_runtime_pin_registry", unexpected)
    with pytest.raises(DeploymentContractError, match="host"):
        if operation == "pair":
            rotation.rotate_pair_with_execution(map_revision="a" * 40, pinvi_revision="b" * 40,
                manager_source_revision="c" * 40, reason="test", rotated_by="test", block_previous=False)
        elif operation == "single":
            rotation.rotate_single_role_with_execution(role="map", revision="a" * 40,
                manager_source_revision="c" * 40, reason="test", rotated_by="test", block_previous=False)
        else:
            rotation.rollback_with_execution(pinset_sha256="a" * 64, manager_source_revision="c" * 40,
                reason="test", rotated_by="test")



def test_pair_writer_requires_each_member_when_targets_split(config, monkeypatch, tmp_path):
    from kor_travel_docker_manager.services import runtime_pin_registry as pins

    config["targets"]["pinvi"]["host"] = config["targets"]["geo"]["host"]
    path = tmp_path / "pins.json"
    with pytest.raises(DeploymentContractError, match="pinvi"):
        pins.write_runtime_pin_registry(None, path=path, publish=False)
    assert not path.exists()


def test_single_target_request_does_not_require_a_remote_peer(config, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from kor_travel_docker_manager.services import runtime_pin_request as requests

    config["targets"]["pinvi"]["host"] = config["targets"]["geo"]["host"]
    path = tmp_path / "request.json"
    request = SimpleNamespace(role="map", to_payload=lambda: {"inert": True})
    requests.write_runtime_pin_request(request, path=path, replace_existing=True)
    assert path.exists()


@pytest.mark.parametrize("args", [
    ["up", "--no-deps", "kor-travel-geo-api"], ["up", "-d"],
    ["down"], ["up", "unknown-service"],
])
def test_public_compose_refuses_unsafe_host_scope_before_lock(config, monkeypatch, args):
    from kor_travel_docker_manager.services import compose_service as module

    def unexpected(*_args, **_kwargs):
        pytest.fail("off-host compose reached mutation lock")

    monkeypatch.setattr(module, "c6c_deployment_lock_from_environment", unexpected)
    with pytest.raises(DeploymentContractError, match="host"):
        module.ComposeService().run(args)


def test_frozen_compose_dependencies_cannot_start_a_foreign_tenant(config):
    from kor_travel_docker_manager.services.compose_service import ComposeService

    resolved = {"services": {
        "kor-travel-map-api": {"depends_on": {"kor-travel-geo-api": {}}},
        "kor-travel-geo-api": {},
    }}
    with pytest.raises(DeploymentContractError, match="host"):
        ComposeService._assert_host_mutation_scope(["up", "kor-travel-map-api"], resolved=resolved)
    ComposeService._assert_host_mutation_scope(["up", "--no-deps", "kor-travel-map-api"], resolved=resolved)
    # SERVICE 뒤의 --no-deps는 container argv이므로 의존성 면제가 아니다.
    with pytest.raises(DeploymentContractError, match="host"):
        ComposeService._assert_host_mutation_scope(["run", "kor-travel-map-api", "--no-deps"], resolved=resolved)


def test_compose_stop_and_read_survive_missing_host_config(config, monkeypatch):
    from kor_travel_docker_manager.services.compose_service import ComposeService

    monkeypatch.setattr(registry, "load_targets_config", lambda: pytest.fail("read/stop loaded registry"))
    for args in [["ps"], ["logs", "kor-travel-geo-api"], ["stop", "kor-travel-geo-api"]]:
        ComposeService._assert_host_mutation_scope(args)


def test_pinned_rebuild_rejects_remote_before_root_or_intent(config, monkeypatch):
    from kor_travel_docker_manager.services import compose_service as module

    monkeypatch.setenv("KTDM_HOST", config["targets"]["geo"]["host"])
    monkeypatch.setattr(module, "_require_pinned_runtime_rebuild_root", lambda: pytest.fail("root/intent reached"))
    with pytest.raises(DeploymentContractError, match="host"):
        module.ComposeService().rebuild_pinned_runtime()


@pytest.mark.parametrize("service", [
    "rustfs-init", "kor-travel-shared-db-init-dagster", "kor-travel-dagster-storage-migrate",
    "kor-travel-map-db-role-bootstrap", "kor-travel-map-application-schema", "pinvi-admin-bootstrap",
])
def test_existing_initializers_and_bootstraps_keep_logical_owners(config, service):
    from kor_travel_docker_manager.services.compose_service import ComposeService

    ComposeService._assert_host_mutation_scope(
        ["run", "--rm", "--no-deps", service], resolved={"services": {service: {}}},
    )


@pytest.mark.parametrize("frozen", [False, True])
def test_lower_compose_guard_does_not_trust_frozen_or_capability_scope(config, monkeypatch, frozen):
    from kor_travel_docker_manager.services.compose_service import ComposeService

    service = ComposeService()
    monkeypatch.setattr(service, "build_command", lambda *_a, **_k: pytest.fail("Docker argv reached"))
    with pytest.raises(DeploymentContractError, match="host"):
        service._run_unlocked(
            ["up", "--no-deps", "kor-travel-geo-api"], capture_output=True,
            environment=None, expected_system_bind_snapshots=None, expected_compose_source_bytes=None,
            environment_snapshot=None, external_input_snapshot=None,
            materialized_compose={"services": {"kor-travel-geo-api": {}}} if frozen else None,
        )
