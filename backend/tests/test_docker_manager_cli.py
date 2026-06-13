import tomllib
from pathlib import Path
from unittest.mock import patch

from kor_travel_docker_manager.cli import build_parser, main
from kor_travel_docker_manager.services.compose_service import ComposeService
from kor_travel_docker_manager.services.docker_service import _redact_env_pair
from kor_travel_docker_manager.services.registry import (
    get_target,
    init_steps_for_target,
    runtime_services_for_target,
    services_for_target,
    target_sequence_for_target,
)


def test_cli_console_script_is_ktdctl():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())

    scripts = pyproject["tool"]["poetry"]["scripts"]
    assert scripts == {"ktdctl": "kor_travel_docker_manager.cli:main"}
    assert build_parser().prog == "ktdctl"


def test_registry_resolves_application_targets_to_shared_services():
    target = get_target("srv")

    assert target["id"] == "pinvi"
    assert target_sequence_for_target("srv") == [
        "db",
        "storage",
        "gra",
        "cadv",
        "prom",
        "geo",
        "conc",
        "map",
        "pinvi",
    ]
    assert services_for_target("srv") == [
        "kor-travel-geo-postgres",
        "rustfs",
        "grafana",
        "cadvisor",
        "prometheus",
        "kor-travel-geo-api",
        "kor-travel-geo-ui",
        "kor-travel-concierge-api",
        "kor-travel-concierge-mcp",
        "kor-travel-concierge-scheduler",
        "kor-travel-concierge-ui",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "pinvi-api",
        "pinvi-web",
    ]
    assert runtime_services_for_target("srv") == [
        "kor-travel-geo-postgres",
        "rustfs",
        "grafana",
        "cadvisor",
        "prometheus",
        "kor-travel-geo-api",
        "kor-travel-geo-ui",
        "kor-travel-concierge-api",
        "kor-travel-concierge-mcp",
        "kor-travel-concierge-scheduler",
        "kor-travel-concierge-ui",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "pinvi-api",
        "pinvi-web",
    ]
    assert [step["name"] for step in init_steps_for_target("srv")] == [
        "db-schema-recovery",
        "rustfs-bucket-recovery",
        "geo-source-verification",
    ]


def test_short_aliases_resolve_dependency_order():
    assert get_target("db")["id"] == "db"
    assert get_target("storage")["id"] == "storage"
    assert get_target("geo")["id"] == "geo"
    assert get_target("kor-travel-geo")["id"] == "geo"
    assert get_target("gra")["id"] == "gra"
    assert get_target("grafana")["id"] == "gra"
    assert get_target("cadv")["id"] == "cadv"
    assert get_target("cadvisor")["id"] == "cadv"
    assert get_target("prom")["id"] == "prom"
    assert get_target("prometheus")["id"] == "prom"
    assert get_target("conc")["id"] == "conc"
    assert get_target("kor-travel-concierge")["id"] == "conc"
    assert get_target("map")["id"] == "map"
    assert get_target("kor-travel-map")["id"] == "map"
    assert get_target("srv")["id"] == "pinvi"
    assert get_target("pinvi")["id"] == "pinvi"
    assert get_target("tripmate")["id"] == "pinvi"
    assert get_target("main")["id"] == "pinvi"
    assert get_target("metrics")["id"] == "prom"
    assert target_sequence_for_target("conc") == [
        "db",
        "storage",
        "gra",
        "cadv",
        "prom",
        "geo",
        "conc",
    ]
    assert target_sequence_for_target("map") == [
        "db",
        "storage",
        "gra",
        "cadv",
        "prom",
        "geo",
        "conc",
        "map",
    ]
    assert target_sequence_for_target("srv") == [
        "db",
        "storage",
        "gra",
        "cadv",
        "prom",
        "geo",
        "conc",
        "map",
        "pinvi",
    ]
    assert services_for_target("geo") == [
        "kor-travel-geo-postgres",
        "rustfs",
        "grafana",
        "cadvisor",
        "prometheus",
        "kor-travel-geo-api",
        "kor-travel-geo-ui",
    ]
    assert services_for_target("prom")[-3:] == ["grafana", "cadvisor", "prometheus"]


def test_env_redaction_masks_sensitive_values():
    assert _redact_env_pair("POSTGRES_PASSWORD=addr") == "POSTGRES_PASSWORD=<redacted>"
    assert _redact_env_pair("RUSTFS_ACCESS_KEY=rustfsadmin") == "RUSTFS_ACCESS_KEY=<redacted>"
    assert _redact_env_pair("POSTGRES_DB=kor_travel_geo") == "POSTGRES_DB=kor_travel_geo"


@patch("kor_travel_docker_manager.services.compose_service.subprocess.run")
@patch("kor_travel_docker_manager.services.compose_service.os.path.exists", return_value=False)
def test_compose_ensure_build_command(mock_exists, mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "started"
    mock_run.return_value.stderr = ""

    result = ComposeService().ensure_target("srv", build=True, recreate=True)

    assert result["success"] is True
    assert result["services"] == [
        "kor-travel-geo-postgres",
        "rustfs",
        "grafana",
        "cadvisor",
        "prometheus",
        "kor-travel-geo-api",
        "kor-travel-geo-ui",
        "kor-travel-concierge-api",
        "kor-travel-concierge-mcp",
        "kor-travel-concierge-scheduler",
        "kor-travel-concierge-ui",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "pinvi-api",
        "pinvi-web",
    ]
    assert result["target_sequence"] == [
        "db",
        "storage",
        "gra",
        "cadv",
        "prom",
        "geo",
        "conc",
        "map",
        "pinvi",
    ]
    up_command = result["command"][0]
    assert up_command[:2] == ["docker", "compose"]
    assert "up" in up_command
    assert "--build" in up_command
    assert "--force-recreate" in up_command
    assert "kor-travel-geo-postgres" in up_command
    assert "grafana" in up_command
    assert "cadvisor" in up_command
    assert "prometheus" in up_command
    assert "kor-travel-geo-api" in up_command
    assert "kor-travel-geo-ui" in up_command
    assert "kor-travel-concierge-api" in up_command
    assert "kor-travel-map-api" in up_command
    assert "pinvi-api" in up_command
    assert mock_run.call_count == 4


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_status_returns_compose_exit_code(mock_compose_service):
    mock_compose_service.status_target.return_value = {
        "success": False,
        "returncode": 17,
        "command": ["docker", "compose", "ps"],
        "stdout": "",
        "stderr": "compose failed",
    }

    assert main(["status", "tripmate"]) == 17


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_ensure_passes_build_flag(mock_compose_service):
    mock_compose_service.ensure_target.return_value = {
        "success": True,
        "returncode": 0,
        "command": ["docker", "compose", "up", "-d", "--build"],
        "stdout": "",
        "stderr": "",
    }

    assert main(["ensure", "geo", "--build"]) == 0
    mock_compose_service.ensure_target.assert_called_once_with(
        "geo",
        build=True,
        recreate=False,
        capture_output=True,
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_direct_alias_runs_ensure(mock_compose_service):
    mock_compose_service.ensure_target.return_value = {
        "success": True,
        "returncode": 0,
        "command": [["docker", "compose", "up", "-d"]],
        "stdout": "",
        "stderr": "",
    }

    assert main(["db", "--build"]) == 0
    mock_compose_service.ensure_target.assert_called_once_with(
        "db",
        build=True,
        recreate=False,
        capture_output=True,
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_direct_gra_alias_runs_ensure(mock_compose_service):
    mock_compose_service.ensure_target.return_value = {
        "success": True,
        "returncode": 0,
        "command": [["docker", "compose", "up", "-d"]],
        "stdout": "",
        "stderr": "",
    }

    assert main(["gra"]) == 0
    mock_compose_service.ensure_target.assert_called_once_with(
        "gra",
        build=False,
        recreate=False,
        capture_output=True,
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_direct_srv_alias_runs_ensure(mock_compose_service):
    mock_compose_service.ensure_target.return_value = {
        "success": True,
        "returncode": 0,
        "command": [["docker", "compose", "up", "-d"]],
        "stdout": "",
        "stderr": "",
    }

    assert main(["srv", "--build"]) == 0
    mock_compose_service.ensure_target.assert_called_once_with(
        "srv",
        build=True,
        recreate=False,
        capture_output=True,
    )
