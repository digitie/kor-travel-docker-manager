from unittest.mock import patch

from kor_travel_docker_manager.services.docker_service import DockerService


def _compose_success(command: list[str] | None = None) -> dict[str, object]:
    return {
        "success": True,
        "returncode": 0,
        "command": command or ["docker", "compose"],
        "stdout": "",
        "stderr": "",
    }


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
