from pathlib import Path
from unittest.mock import patch

import yaml

from kor_travel_docker_manager.services.docker_service import DockerService

_ROOT = Path(__file__).resolve().parents[2]
_CONCIERGE_BASE_URL_ENV = "${KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_BASE_URL:-http://127.0.0.1:12601}"
_CONCIERGE_API_KEY_ENV = "${KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY:-}"
_MAP_FETCH_SERVICES = (
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
)
_MAP_PROVIDER_SERVICES = (
    "kor-travel-map-api",
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
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


def test_map_services_interpolate_provider_credentials_from_current_env_names() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    for service_name in _MAP_PROVIDER_SERVICES:
        environment = compose["services"][service_name]["environment"]
        assert environment["KOR_TRAVEL_MAP_OPINET_API_KEY"] == _OPINET_API_KEY_ENV
        assert environment["KOR_TRAVEL_MAP_KREX_EX_API_KEY"] == _KREX_EX_API_KEY_ENV
        assert environment["KOR_TRAVEL_MAP_KREX_GO_API_KEY"] == _KREX_GO_API_KEY_ENV

    api_environment = compose["services"]["kor-travel-map-api"]["environment"]
    assert (
        api_environment["KOR_TRAVEL_MAP_API_OPINET_SERVICE_KEY"]
        == _API_OPINET_SERVICE_KEY_ENV
    )
    assert (
        api_environment["KOR_TRAVEL_MAP_API_KREX_SERVICE_KEY"]
        == _API_KREX_SERVICE_KEY_ENV
    )

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


@patch("kor_travel_docker_manager.services.docker_service.compose_service.run")
@patch("kor_travel_docker_manager.services.docker_service.save_compose_config")
@patch("kor_travel_docker_manager.services.docker_service.get_compose_config")
def test_update_container_config_recreates_with_compose_and_preserves_host_network(
    mock_get_compose_config,
    mock_save_compose_config,
    mock_compose_run,
):
    compose_config = {
        "services": {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "network_mode": "${KTDM_DOCKER_NETWORK_MODE:-host}",
                "environment": {"RUSTFS_ACCESS_KEY": "${RUSTFS_ACCESS_KEY:-rustfsadmin}"},
            }
        }
    }
    mock_get_compose_config.return_value = compose_config
    mock_compose_run.return_value = _compose_success()

    result = DockerService().update_container_config(
        "rustfs",
        ["${RUSTFS_API_PORT:-12101}:${RUSTFS_API_CONTAINER_PORT:-12101}"],
        {"RUSTFS_ACCESS_KEY": "${RUSTFS_ACCESS_KEY:-rustfsadmin}"},
        ["${RUSTFS_DATA_DIR:-/tmp/rustfs}:/data"],
        [],
    )

    assert result["success"] is True
    saved_service = mock_save_compose_config.call_args.args[0]["services"]["rustfs"]
    assert saved_service["network_mode"] == "${KTDM_DOCKER_NETWORK_MODE:-host}"
    assert "networks" not in saved_service
    assert mock_compose_run.call_args_list[0].args == (
        ["up", "-d", "--force-recreate", "rustfs"],
    )
    assert mock_compose_run.call_args_list[0].kwargs == {"capture_output": True}
    assert mock_compose_run.call_args_list[1].args == (["run", "--rm", "rustfs-init"],)


@patch("kor_travel_docker_manager.services.docker_service.compose_service.run")
@patch("kor_travel_docker_manager.services.docker_service.save_compose_config")
@patch("kor_travel_docker_manager.services.docker_service.get_compose_config")
def test_update_container_config_switches_to_compose_networks_when_requested(
    mock_get_compose_config,
    mock_save_compose_config,
    mock_compose_run,
):
    compose_config = {
        "services": {
            "kor-travel-geo-postgres": {
                "image": "postgis/postgis:16-3.5",
                "network_mode": "${KTDM_DOCKER_NETWORK_MODE:-host}",
            }
        }
    }
    mock_get_compose_config.return_value = compose_config
    mock_compose_run.return_value = _compose_success()

    result = DockerService().update_container_config(
        "kor-travel-geo-postgresql",
        ["5432:5432"],
        {"POSTGRES_DB": "kor_travel_geo"},
        ["pgdata:/var/lib/postgresql/data"],
        ["default"],
    )

    assert result["success"] is True
    saved_service = mock_save_compose_config.call_args.args[0]["services"][
        "kor-travel-geo-postgres"
    ]
    assert saved_service["networks"] == ["default"]
    assert "network_mode" not in saved_service
    assert mock_compose_run.call_args.args == (
        ["up", "-d", "--force-recreate", "kor-travel-geo-postgres"],
    )
