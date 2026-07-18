from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from kor_travel_docker_manager.services import docker_service as docker_service_module
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
    compose_volume_graph_hash,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeEnvFileIdentity,
    ComposeEnvironmentSnapshot,
    ComposeExternalInputSnapshot,
    ComposeTransactionSnapshot,
    ValidatedComposeCandidate,
    _resolved_compose_document_hash,
)
from kor_travel_docker_manager.services.docker_service import DockerService

_ROOT = Path(__file__).resolve().parents[2]
_CONCIERGE_BASE_URL_ENV = "${KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_BASE_URL:-http://127.0.0.1:12601}"
_CONCIERGE_API_KEY_ENV = "${KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY:-}"
_MAP_FETCH_SERVICES = (
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
)
_MAP_INGESTION_SERVICES = (
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
)
_MAP_API_SERVICE = "kor-travel-map-api"
_PINVI_API_SERVICE = "pinvi-api"
_OPS_READ_SOURCE = "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}"
_OPS_CANCEL_SOURCE = "${KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN:-}"
_PINVI_MAP_BASE_URL_SOURCE = (
    "${PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL:-http://127.0.0.1:"
    "${KOR_TRAVEL_MAP_API_CONTAINER_PORT:-12701}}"
)
_OPINET_API_KEY_ENV = "${KOR_TRAVEL_MAP_OPINET_API_KEY:-}"
_API_OPINET_SERVICE_KEY_ENV = (
    "${KOR_TRAVEL_MAP_API_OPINET_SERVICE_KEY:-${KOR_TRAVEL_MAP_OPINET_API_KEY:-}}"
)
_KREX_EX_API_KEY_ENV = "${KOR_TRAVEL_MAP_KREX_EX_API_KEY:-}"
_KREX_GO_API_KEY_ENV = "${KOR_TRAVEL_MAP_KREX_GO_API_KEY:-}"
_API_KREX_SERVICE_KEY_ENV = (
    "${KOR_TRAVEL_MAP_API_KREX_SERVICE_KEY:-${KOR_TRAVEL_MAP_KREX_EX_API_KEY:-}}"
)


def _compose_success(command: list[str] | None = None) -> dict[str, object]:
    return {
        "success": True,
        "returncode": 0,
        "command": command or ["docker", "compose"],
        "stdout": "",
        "stderr": "",
    }


def _config_transaction(
    compose_path: Path,
    config: dict[str, object],
) -> tuple[ComposeTransactionSnapshot, ValidatedComposeCandidate]:
    source_bytes = yaml.safe_dump(
        config,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    resolved = deepcopy(config)
    environment = ComposeEnvironmentSnapshot(
        effective={
            "KTDM_DEPLOYMENT_ENVIRONMENT": "local",
            "PINVI_ENVIRONMENT": "development",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "false",
        },
        env_path=str(compose_path.parent / ".env"),
        compose_path=str(compose_path),
        override_path=str(compose_path.parent / "missing.override.yml"),
        env_file_identity=ComposeEnvFileIdentity(exists=False),
        env_file_bytes=b"",
    )
    external = ComposeExternalInputSnapshot(references=(), files=())
    transaction = ComposeTransactionSnapshot(
        environment=environment,
        external_inputs=external,
        compose_source_bytes=source_bytes,
        compose_source_mode=0o640,
        system_bind_snapshots=(),
        raw_volume_graph_hash=compose_volume_graph_hash(config),
        resolved_volume_graph_hash=compose_volume_graph_hash(resolved),
        resolved=resolved,
        resolved_document_hash=_resolved_compose_document_hash(resolved),
    )
    return transaction, ValidatedComposeCandidate(
        resolved=resolved,
        system_bind_snapshots=(),
        raw_volume_graph_hash=transaction.raw_volume_graph_hash,
        resolved_volume_graph_hash=transaction.resolved_volume_graph_hash,
        environment_snapshot=environment,
        external_input_snapshot=external,
        transaction_snapshot=transaction,
    )


def _candidate_capture_for(compose_path: Path):  # type: ignore[no-untyped-def]
    def capture(candidate, **_kwargs):  # type: ignore[no-untyped-def]
        return _config_transaction(compose_path, candidate)[1]

    return capture


def test_nontrivial_config_change_runs_candidate_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    original_config: dict[str, object] = {
        "services": {
                "kor-travel-geo-postgres": {
                    "image": "postgres:16",
                    "environment": {"POSTGRES_DB": "before"},
                    "volumes": [],
                }
        }
    }
    baseline, baseline_validation = _config_transaction(
        compose_path, original_config
    )
    compose_path.write_bytes(baseline.compose_source_bytes)
    compose_path.chmod(baseline.compose_source_mode)
    candidate_transactions: list[ComposeTransactionSnapshot] = []

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )

    def capture_candidate(candidate, **_kwargs):  # type: ignore[no-untyped-def]
        transaction, validation = _config_transaction(compose_path, candidate)
        candidate_transactions.append(transaction)
        return validation

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        capture_candidate,
    )
    forward = Mock(return_value=_compose_success())
    monkeypatch.setattr(docker_service_module.compose_service, "run", forward)

    result = DockerService()._update_container_config_unlocked(
        "kor-travel-geo-postgresql",
        ["5432:5432"],
        {"POSTGRES_DB": "after"},
        [],
        [],
        environment_snapshot=baseline.environment,
    )

    assert result["success"] is True
    candidate = candidate_transactions[0]
    assert candidate is not baseline
    assert compose_path.read_bytes() == candidate.compose_source_bytes
    assert forward.call_args.kwargs["transaction"] is candidate


def test_candidate_failure_restores_exact_baseline_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    original_config: dict[str, object] = {
        "services": {
                "kor-travel-geo-postgres": {
                    "image": "postgres:16",
                    "environment": {"POSTGRES_DB": "before"},
                    "volumes": [],
                }
        }
    }
    baseline, baseline_validation = _config_transaction(
        compose_path, original_config
    )
    compose_path.write_bytes(baseline.compose_source_bytes)
    compose_path.chmod(baseline.compose_source_mode)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )

    def capture_candidate(candidate, **_kwargs):  # type: ignore[no-untyped-def]
        return _config_transaction(compose_path, candidate)[1]

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        capture_candidate,
    )
    forward = Mock(
        return_value={
            **_compose_success(),
            "success": False,
            "returncode": 1,
            "stderr": "candidate failed",
        }
    )
    recovery = Mock(return_value=_compose_success())
    monkeypatch.setattr(docker_service_module.compose_service, "run", forward)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_run_frozen_recovery",
        recovery,
    )

    result = DockerService()._update_container_config_unlocked(
        "kor-travel-geo-postgresql",
        ["5432:5432"],
        {"POSTGRES_DB": "after"},
        [],
        [],
        environment_snapshot=baseline.environment,
    )

    assert result["success"] is False
    assert compose_path.read_bytes() == baseline.compose_source_bytes
    assert compose_path.stat().st_mode & 0o777 == baseline.compose_source_mode
    assert recovery.call_args.kwargs["transaction"] is baseline


def test_map_services_share_single_concierge_read_key_source() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services_with_key = {
        service_name
        for service_name, service in compose["services"].items()
        if "KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY"
        in service.get("environment", {})
    }
    assert services_with_key == set(_MAP_FETCH_SERVICES)

    for service_name in _MAP_FETCH_SERVICES:
        environment = compose["services"][service_name]["environment"]
        assert environment["KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_BASE_URL"] == (
            _CONCIERGE_BASE_URL_ENV
        )
        assert (
            environment["KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY"]
            == _CONCIERGE_API_KEY_ENV
        )

    key_lines = [
        line
        for line in (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.startswith("KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY=")
    ]
    assert key_lines == ["KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY="]


def test_map_ingestion_services_interpolate_provider_credentials_from_current_env_names() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    provider_keys = {
        "KOR_TRAVEL_MAP_OPINET_API_KEY": _OPINET_API_KEY_ENV,
        "KOR_TRAVEL_MAP_KREX_EX_API_KEY": _KREX_EX_API_KEY_ENV,
        "KOR_TRAVEL_MAP_KREX_GO_API_KEY": _KREX_GO_API_KEY_ENV,
    }
    for key in provider_keys:
        services_with_key = {
            service_name
            for service_name, service in compose["services"].items()
            if key in service.get("environment", {})
        }
        assert services_with_key == set(_MAP_INGESTION_SERVICES)

    for service_name in _MAP_INGESTION_SERVICES:
        environment = compose["services"][service_name]["environment"]
        for key, source_expression in provider_keys.items():
            assert environment[key] == source_expression


def test_map_api_interpolates_only_provider_preview_credentials() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    api_environment = compose["services"][_MAP_API_SERVICE]["environment"]
    assert "KOR_TRAVEL_MAP_OPINET_API_KEY" not in api_environment
    assert "KOR_TRAVEL_MAP_KREX_EX_API_KEY" not in api_environment
    assert "KOR_TRAVEL_MAP_KREX_GO_API_KEY" not in api_environment
    assert (
        api_environment["KOR_TRAVEL_MAP_API_OPINET_SERVICE_KEY"]
        == _API_OPINET_SERVICE_KEY_ENV
    )
    assert (
        api_environment["KOR_TRAVEL_MAP_API_KREX_SERVICE_KEY"]
        == _API_KREX_SERVICE_KEY_ENV
    )

    for key in (
        "KOR_TRAVEL_MAP_API_OPINET_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KREX_SERVICE_KEY",
    ):
        services_with_key = {
            service_name
            for service_name, service in compose["services"].items()
            if key in service.get("environment", {})
        }
        assert services_with_key == {_MAP_API_SERVICE}


def test_map_provider_credentials_have_empty_env_example_placeholders() -> None:
    env_example_lines = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    for key in (
        "KOR_TRAVEL_MAP_OPINET_API_KEY",
        "KOR_TRAVEL_MAP_API_OPINET_SERVICE_KEY",
        "KOR_TRAVEL_MAP_KREX_EX_API_KEY",
        "KOR_TRAVEL_MAP_KREX_GO_API_KEY",
        "KOR_TRAVEL_MAP_API_KREX_SERVICE_KEY",
    ):
        assert [line for line in env_example_lines if line.startswith(f"{key}=")] == [
            f"{key}="
        ]


def test_map_pinvi_ops_principal_is_api_only_and_uses_single_secret_source() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    map_environment = services[_MAP_API_SERVICE]["environment"]
    pinvi_environment = services[_PINVI_API_SERVICE]["environment"]

    assert map_environment["KOR_TRAVEL_MAP_API_OPS_READ_TOKEN"] == _OPS_READ_SOURCE
    assert map_environment["KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN"] == _OPS_CANCEL_SOURCE
    assert map_environment["KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED"] == (
        "${KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED:?"
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED must be explicitly set}"
    )
    assert pinvi_environment["PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN"] == _OPS_READ_SOURCE
    assert pinvi_environment["PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN"] == _OPS_CANCEL_SOURCE
    assert (
        pinvi_environment["PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL"]
        == _PINVI_MAP_BASE_URL_SOURCE
    )
    assert pinvi_environment["PINVI_ENVIRONMENT"] == (
        "${PINVI_ENVIRONMENT:?PINVI_ENVIRONMENT must be explicitly set}"
    )

    assert {
        service_name
        for service_name, service in services.items()
        if "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN" in service.get("environment", {})
        or "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN" in service.get("environment", {})
        or "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED" in service.get("environment", {})
    } == {_MAP_API_SERVICE}
    assert {
        service_name
        for service_name, service in services.items()
        if "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN" in service.get("environment", {})
        or "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN" in service.get("environment", {})
    } == {_PINVI_API_SERVICE}


def test_map_pinvi_ops_principal_env_example_has_only_source_placeholders() -> None:
    env_example_lines = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "KTDM_DEPLOYMENT_ENVIRONMENT=local" in env_example_lines
    assert "COMPOSE_PROJECT_NAME=kor-travel-local" in env_example_lines
    assert "PINVI_ENVIRONMENT=development" in env_example_lines
    assert "KTDM_C6C_CONTRACT_GENERATION=c6c-ops-v1" in env_example_lines
    for key in (
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN",
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN",
    ):
        assert [line for line in env_example_lines if line.startswith(f"{key}=")] == [
            f"{key}="
        ]
    assert "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=false" in env_example_lines
    assert not any(
        line.startswith("PINVI_KOR_TRAVEL_MAP_OPS_") for line in env_example_lines
    )
    manager_only_names = {
        "KTDM_C6C_MAP_UI_ADMIN_USERNAME",
        "KTDM_C6C_MAP_UI_ADMIN_PASSWORD",
        "KTDM_C6C_PINVI_ADMIN_EMAIL",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD",
        "KTDM_C6C_CANCEL_PROBE_JOB_ID",
    }
    for name in manager_only_names:
        assert any(line.startswith(f"{name}=") for line in env_example_lines)

    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert all(
        name not in service.get("environment", {})
        for service in compose["services"].values()
        for name in manager_only_names
    )


def test_update_container_config_recreates_with_compose_and_preserves_host_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "network_mode": "${KTDM_DOCKER_NETWORK_MODE:-host}",
                "environment": {"RUSTFS_ACCESS_KEY": "${RUSTFS_ACCESS_KEY:-rustfsadmin}"},
                "volumes": ["${RUSTFS_DATA_DIR:-/tmp/rustfs}:/data"],
            }
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    baseline, baseline_validation = _config_transaction(compose_path, compose_config)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )

    def capture_candidate(candidate, **_kwargs):  # type: ignore[no-untyped-def]
        return _config_transaction(compose_path, candidate)[1]

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        capture_candidate,
    )
    compose_run.return_value = _compose_success()

    result = service.update_container_config(
        "rustfs",
        ["${RUSTFS_API_PORT:-12101}:${RUSTFS_API_CONTAINER_PORT:-12101}"],
        {"RUSTFS_ACCESS_KEY": "${RUSTFS_ACCESS_KEY:-rustfsadmin}"},
        ["${RUSTFS_DATA_DIR:-/tmp/rustfs}:/data"],
        [],
    )

    assert result["success"] is True
    saved_service = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"][
        "rustfs"
    ]
    assert saved_service["network_mode"] == "${KTDM_DOCKER_NETWORK_MODE:-host}"
    assert "networks" not in saved_service
    assert compose_run.call_args_list[0].args == (
        ["up", "-d", "--force-recreate", "rustfs"],
    )
    assert compose_run.call_args_list[0].kwargs["capture_output"] is True
    assert compose_run.call_args_list[0].kwargs["mutation_capability"] is not None
    assert compose_run.call_args_list[1].args == (["run", "--rm", "rustfs-init"],)
    assert compose_run.call_args_list[1].kwargs["mutation_capability"] is not None


def test_update_container_config_switches_to_compose_networks_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "kor-travel-geo-postgres": {
                "image": "postgis/postgis:16-3.5",
                "network_mode": "${KTDM_DOCKER_NETWORK_MODE:-host}",
                "volumes": ["pgdata:/var/lib/postgresql/data"],
            }
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    baseline, baseline_validation = _config_transaction(compose_path, compose_config)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )

    def capture_candidate(candidate, **_kwargs):  # type: ignore[no-untyped-def]
        return _config_transaction(compose_path, candidate)[1]

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        capture_candidate,
    )
    compose_run.return_value = _compose_success()

    result = service.update_container_config(
        "kor-travel-geo-postgresql",
        ["5432:5432"],
        {"POSTGRES_DB": "kor_travel_geo"},
        ["pgdata:/var/lib/postgresql/data"],
        ["default"],
    )

    assert result["success"] is True
    saved_service = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"][
        "kor-travel-geo-postgres"
    ]
    assert saved_service["networks"] == ["default"]
    assert "network_mode" not in saved_service
    assert compose_run.call_args.args == (
        ["up", "-d", "--force-recreate", "kor-travel-geo-postgres"],
    )


def test_non_api_sdk_mutation_validates_environment_before_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DockerService()
    client = Mock()
    monkeypatch.setattr(service, "_get_client", client)
    monkeypatch.setattr(
        docker_service_module,
        "assert_manager_mutation_allowed",
        Mock(side_effect=DeploymentContractError("invalid manager environment")),
    )

    with pytest.raises(DeploymentContractError, match="invalid manager environment"):
        service.control_container("rustfs", "restart")

    client.assert_not_called()


def _prepare_candidate_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compose_config: dict[str, object],
) -> tuple[DockerService, Path, Mock]:
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        yaml.safe_dump(compose_config, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setattr(
        docker_service_module, "_get_compose_path", lambda: str(compose_path)
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(compose_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_env_path",
        lambda: str(tmp_path / ".env"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_override_path",
        lambda: str(tmp_path / "missing.override.yml"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        docker_service_module,
        "get_compose_config",
        lambda _path=None: compose_config,
    )
    monkeypatch.setattr(
        docker_service_module, "assert_manager_mutation_allowed", Mock()
    )
    monkeypatch.setattr(
        docker_service_module,
        "c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    compose_run = Mock()
    monkeypatch.setattr(docker_service_module.compose_service, "run", compose_run)
    return DockerService(), compose_path, compose_run


def test_non_api_config_update_rejects_candidate_before_write_or_recreate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "rustfs": {"image": "rustfs/rustfs:latest", "volumes": []}
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()

    with pytest.raises(ComposeCandidateContractError):
        service.update_container_config(
            "rustfs",
            [],
            {"ALIAS": "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}"},
            [],
            [],
        )

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_rustfs_config_rejects_root_env_bind_before_write_or_recreate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {"rustfs": {"image": "rustfs/rustfs:latest"}}
    }
    root_env = tmp_path / ".env"
    root_env.write_text("SAFE=value\n", encoding="utf-8")
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()

    with pytest.raises(
        ComposeCandidateContractError,
        match="volume configuration is immutable",
    ):
        service.update_container_config(
            "rustfs",
            [],
            {},
            ["./.env:/run/manager.env:ro"],
            [],
        )

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_rustfs_config_rejects_missing_bind_without_creating_or_mutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {"rustfs": {"image": "rustfs/rustfs:latest"}}
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()
    missing = tmp_path / "future-secret"

    with pytest.raises(
        ComposeCandidateContractError,
        match="volume configuration is immutable",
    ):
        service.update_container_config(
            "rustfs",
            [],
            {},
            ["./future-secret:/run/future-secret:ro"],
            [],
        )

    assert not missing.exists()
    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


@pytest.mark.parametrize(
    "volumes",
    [
        ["/var/run/docker.sock:/var/run/docker.sock:ro", "/sys:/sys:rw"],
        [
            {
                "type": "bind",
                "source": "/var/run/docker.sock",
                "target": "/var/run/docker.sock",
                "read_only": True,
            },
            {
                "type": "bind",
                "source": "/sys",
                "target": "/sys",
                "read_only": False,
            },
        ],
    ],
)
def test_cadvisor_config_rejects_writable_system_bind_without_mutation(
    volumes: list[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = [
        "/var/run/docker.sock:/var/run/docker.sock:ro",
        "/sys:/sys:ro",
    ]
    compose_config: dict[str, object] = {
        "services": {
            "cadvisor": {
                "image": "gcr.io/cadvisor/cadvisor:v0.52.1",
                "volumes": baseline,
            }
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()

    with pytest.raises(
        ComposeCandidateContractError,
        match="volume configuration is immutable",
    ):
        service.update_container_config("cadvisor", [], {}, volumes, [])

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_system_bind_snapshot_change_before_write_keeps_compose_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {"rustfs": {"image": "rustfs/rustfs:latest", "volumes": []}}
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        _candidate_capture_for(compose_path),
    )
    monkeypatch.setattr(
        docker_service_module,
        "revalidate_candidate_system_bind_snapshots",
        Mock(
            side_effect=ComposeCandidateContractError(
                "compose candidate system bind identity changed during the request"
            )
        ),
    )

    with pytest.raises(ComposeCandidateContractError, match="identity changed"):
        service.update_container_config("rustfs", [], {"SAFE": "updated"}, [], [])

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_preflight_rejection_restore_failure_is_typed_post_mutation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {"rustfs": {"image": "rustfs/rustfs:latest", "volumes": []}}
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()
    original_mode = compose_path.stat().st_mode & 0o777
    original_error = ComposeCandidateContractError(
        "compose candidate source changed during the config request"
    )

    def reject_candidate(
        _candidate: object, **_kwargs: object
    ) -> ValidatedComposeCandidate:
        compose_path.write_text(
            "services:\n  attacker:\n    volumes:\n    - /tmp:/host\n",
            encoding="utf-8",
        )
        raise original_error

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        reject_candidate,
    )
    restore = Mock(side_effect=OSError("atomic compose restore failed"))
    monkeypatch.setattr(docker_service_module, "_atomic_write", restore)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service.update_container_config("rustfs", [], {"SAFE": "updated"}, [], [])

    assert caught.value.original_error is original_error
    assert caught.value.recovery_attempted is True
    assert caught.value.recovery_succeeded is False
    assert caught.value.recovery_error == "atomic compose restore failed"
    assert caught.value.restoration == {
        "config_restored": False,
        "runtime_restored": False,
        "runtime_recovery_attempted": False,
        "durable_config_mutation": True,
        "error": "atomic compose restore failed",
    }
    assert compose_path.read_bytes() != original
    restore.assert_called_once_with(str(compose_path), original, mode=original_mode)
    compose_run.assert_not_called()


def test_system_bind_snapshot_change_before_subprocess_restores_compose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {"rustfs": {"image": "rustfs/rustfs:latest", "volumes": []}}
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()
    original_mode = compose_path.stat().st_mode & 0o777
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        _candidate_capture_for(compose_path),
    )
    compose_run.side_effect = ComposeCandidateContractError(
        "compose candidate system bind identity changed during the request"
    )

    with pytest.raises(ComposeCandidateContractError, match="identity changed"):
        service.update_container_config("rustfs", [], {"SAFE": "updated"}, [], [])

    assert compose_path.read_bytes() == original
    assert compose_path.stat().st_mode & 0o777 == original_mode


@pytest.mark.parametrize(
    ("recovery_result", "recovery_succeeded", "recovery_error"),
    [
        (_compose_success(), True, None),
        (
            {
                **_compose_success(),
                "success": False,
                "returncode": 9,
                "stderr": "persisted runtime recovery failed",
            },
            False,
            "persisted runtime recovery failed",
        ),
    ],
)
def test_rustfs_second_preflight_drift_restores_bytes_mode_and_runtime(
    recovery_result: dict[str, object],
    recovery_succeeded: bool,
    recovery_error: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "environment": {"ORIGINAL": "yes"},
                "volumes": [],
            }
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    compose_path.chmod(0o640)
    original = compose_path.read_bytes()
    original_error = ComposeCandidateContractError(
        "compose resolved volume graph changed during the request"
    )
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        _candidate_capture_for(compose_path),
    )
    compose_run.side_effect = [
        _compose_success(),
        original_error,
        recovery_result,
    ]

    with pytest.raises(ComposePostMutationContractError) as caught:
        service.update_container_config(
            "rustfs",
            [],
            {"UPDATED": "yes"},
            [],
            [],
        )

    assert caught.value.original_error is original_error
    assert caught.value.recovery_attempted is True
    assert caught.value.recovery_succeeded is recovery_succeeded
    assert caught.value.recovery_error == recovery_error
    assert compose_path.read_bytes() == original
    assert compose_path.stat().st_mode & 0o777 == 0o640
    assert caught.value.restoration is not None
    assert caught.value.restoration["config_restored"] is True
    assert caught.value.restoration["runtime_restored"] is recovery_succeeded
    assert compose_run.call_count == 3


def test_non_api_config_update_rejects_resolved_candidate_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "rustfs": {"image": "rustfs/rustfs:latest", "volumes": []}
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    candidate_error = ComposeCandidateContractError(
        "resolved compose candidate leaks a protected C6c reference"
    )
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        Mock(side_effect=candidate_error),
    )
    original = compose_path.read_bytes()

    with pytest.raises(ComposeCandidateContractError) as caught:
        service.update_container_config("rustfs", [], {}, [], [])

    assert caught.value is candidate_error
    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_non_api_config_reset_rejects_candidate_before_write_or_recreate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {"rustfs": {"image": "rustfs/rustfs:latest"}}
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    service._default_compose_config = {
        "services": {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "command": ["worker", "KTDM_C6C_CONTRACT_GENERATION"],
            }
        }
    }
    original = compose_path.read_bytes()

    with pytest.raises(ComposeCandidateContractError):
        service.reset_container_config("rustfs")

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


@pytest.mark.parametrize(
    "message",
    [
        "compose candidate raw volume graph differs from persisted compose",
        "compose candidate resolved volume graph differs from persisted compose",
    ],
)
def test_reset_rejects_persisted_volume_graph_drift_without_mutation(
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "volumes": [],
            }
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    service._default_compose_config = deepcopy(compose_config)
    original = compose_path.read_bytes()
    candidate_error = ComposeCandidateContractError(message)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        Mock(side_effect=candidate_error),
    )

    with pytest.raises(ComposeCandidateContractError) as caught:
        service.reset_container_config("rustfs")

    assert caught.value is candidate_error
    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_missing_non_api_container_create_rejects_candidate_before_recreate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "labels": {"leak": "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN"},
            }
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    client = Mock()
    client.containers.get.side_effect = docker_service_module.NotFound("missing")
    monkeypatch.setattr(service, "_get_client", Mock(return_value=client))
    original = compose_path.read_bytes()

    with pytest.raises(ComposeCandidateContractError):
        service.control_container("rustfs", "start")

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_config_recreate_failure_restores_exact_file_and_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (
        b"services:\n"
        b"  rustfs:\n"
        b"    image: rustfs/rustfs:latest\n"
        b"    environment:\n"
        b"      ORIGINAL: exact-format-preserved\n"
        b"    volumes:\n"
        b"    - rustfs:/data\n"
    )
    compose_config = yaml.safe_load(original.decode("utf-8"))
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    compose_path.write_bytes(original)
    compose_path.chmod(0o640)
    baseline, baseline_validation = _config_transaction(compose_path, compose_config)
    baseline = replace(
        baseline,
        compose_source_bytes=original,
        compose_source_mode=0o640,
    )
    baseline_validation = replace(
        baseline_validation,
        transaction_snapshot=baseline,
    )
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        _candidate_capture_for(compose_path),
    )
    compose_run.return_value = {
        **_compose_success(),
        "success": False,
        "returncode": 1,
        "stderr": "candidate recreate failed",
    }
    frozen_recovery = Mock(return_value=_compose_success())
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_run_frozen_recovery",
        frozen_recovery,
    )

    result = service.update_container_config(
        "rustfs",
        ["12101:12101"],
        {"CHANGED": "yes"},
        ["rustfs:/data"],
        [],
    )

    assert result["success"] is False
    assert compose_path.read_bytes() == original
    assert compose_path.stat().st_mode & 0o777 == 0o640
    assert result["restoration"] == {
        "config_restored": True,
        "runtime_restored": True,
        "command": ["docker", "compose"],
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "error": None,
    }
    assert compose_run.call_count == 1
    frozen_recovery.assert_called_once()


def test_config_runtime_restore_failure_preserves_compose_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("services:\n  rustfs:\n    image: rustfs:old\n", encoding="utf-8")
    monkeypatch.setattr(docker_service_module, "_get_compose_path", lambda: str(compose_path))
    restore_run = {
        "success": False,
        "returncode": 9,
        "command": ["docker", "compose", "up", "rustfs"],
        "stdout": "restore stdout",
        "stderr": "restore stderr",
    }
    frozen_run = Mock(return_value=restore_run)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_run_frozen_recovery",
        frozen_run,
    )
    transaction = _config_transaction(
        compose_path,
        yaml.safe_load(compose_path.read_text(encoding="utf-8")),
    )[0]

    restoration = DockerService()._restore_compose_transaction(
        compose_path.read_bytes(),
        0o640,
        "rustfs",
        transaction,
    )

    assert restoration == {
        "config_restored": True,
        "runtime_restored": False,
        "command": ["docker", "compose", "up", "rustfs"],
        "returncode": 9,
        "stdout": "restore stdout",
        "stderr": "restore stderr",
        "error": "restore stderr",
    }


def test_missing_container_start_preserves_nested_restoration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DockerService()
    client = Mock()
    client.containers.get.side_effect = docker_service_module.NotFound("missing")
    monkeypatch.setattr(service, "_get_client", lambda: client)
    restoration = {
        "config_restored": True,
        "runtime_restored": False,
        "returncode": 9,
        "stdout": "restore stdout",
        "stderr": "restore stderr",
        "error": "restore stderr",
    }
    monkeypatch.setattr(
        service,
        "_update_container_config_unlocked",
        Mock(
            return_value={
                "success": False,
                "error": "candidate recreate failed",
                "command": ["docker", "compose"],
                "returncode": 1,
                "stdout": "candidate stdout",
                "stderr": "candidate stderr",
                "restoration": restoration,
            }
        ),
    )

    environment_snapshot = Mock()
    environment_snapshot.compose_path = "/tmp/docker-compose.yml"
    result = service._control_container_unlocked(
        "rustfs",
        "start",
        environment_snapshot=environment_snapshot,
    )

    assert result["success"] is False
    assert result["restoration"] == restoration
    assert result["returncode"] == 1
    assert result["stderr"] == "candidate stderr"


def test_reset_config_uses_one_locked_update_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DockerService()
    service._default_compose_config = {
        "services": {
            "rustfs": {
                "ports": ["12101:12101"],
                "environment": {"RESET": "yes"},
                "volumes": ["rustfs:/data"],
            }
        }
    }
    monkeypatch.setattr(
        docker_service_module, "assert_manager_mutation_allowed", lambda **_kwargs: "local"
    )
    lock = Mock(return_value=nullcontext())
    monkeypatch.setattr(docker_service_module, "c6c_deployment_lock", lock)
    update = Mock(return_value={"success": True})
    monkeypatch.setattr(service, "_update_container_config_unlocked", update)

    result = service.reset_container_config("rustfs")

    assert result["success"] is True
    lock.assert_called_once()
    update.assert_called_once()
    assert update.call_args.args == (
        "rustfs",
        ["12101:12101"],
        {"RESET": "yes"},
        ["rustfs:/data"],
        [],
    )
    assert update.call_args.kwargs["replacement_service_config"] == (
        service._default_compose_config["services"]["rustfs"]
    )
    assert isinstance(
        update.call_args.kwargs["environment_snapshot"],
        ComposeEnvironmentSnapshot,
    )
