import tomllib
from pathlib import Path
from unittest.mock import patch

from tripmate_manager.cli import build_parser, main
from tripmate_manager.services.compose_service import ComposeService
from tripmate_manager.services.docker_service import _redact_env_pair
from tripmate_manager.services.registry import (
    get_target,
    init_steps_for_target,
    runtime_services_for_target,
    services_for_target,
    target_sequence_for_target,
)


def test_cli_console_script_is_tmctl():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())

    scripts = pyproject["tool"]["poetry"]["scripts"]
    assert scripts == {"tmctl": "tripmate_manager.cli:main"}
    assert build_parser().prog == "tmctl"


def test_registry_resolves_application_targets_to_shared_services():
    target = get_target("main")

    assert target["id"] == "main"
    assert target_sequence_for_target("main") == ["db", "storage", "geo", "map", "ai", "main"]
    assert services_for_target("main") == ["kraddr-geo-postgres", "rustfs"]
    assert runtime_services_for_target("main") == ["kraddr-geo-postgres", "rustfs"]
    assert [step["name"] for step in init_steps_for_target("main")] == [
        "db-schema-recovery",
        "rustfs-bucket-recovery",
        "geo-source-verification",
    ]


def test_short_aliases_resolve_dependency_order():
    assert get_target("db")["id"] == "db"
    assert get_target("storage")["id"] == "storage"
    assert get_target("geo")["id"] == "geo"
    assert get_target("map")["id"] == "map"
    assert get_target("ai")["id"] == "ai"
    assert get_target("tripmate")["id"] == "main"
    assert target_sequence_for_target("map") == ["db", "storage", "geo", "map"]
    assert target_sequence_for_target("ai") == ["db", "storage", "geo", "map", "ai"]
    assert target_sequence_for_target("main") == ["db", "storage", "geo", "map", "ai", "main"]
    assert services_for_target("geo") == ["kraddr-geo-postgres", "rustfs"]


def test_env_redaction_masks_sensitive_values():
    assert _redact_env_pair("POSTGRES_PASSWORD=addr") == "POSTGRES_PASSWORD=<redacted>"
    assert _redact_env_pair("RUSTFS_ACCESS_KEY=rustfsadmin") == "RUSTFS_ACCESS_KEY=<redacted>"
    assert _redact_env_pair("POSTGRES_DB=kraddr_geo") == "POSTGRES_DB=kraddr_geo"


@patch("tripmate_manager.services.compose_service.subprocess.run")
@patch("tripmate_manager.services.compose_service.os.path.exists", return_value=False)
def test_compose_ensure_build_command(mock_exists, mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "started"
    mock_run.return_value.stderr = ""

    result = ComposeService().ensure_target("main", build=True, recreate=True)

    assert result["success"] is True
    assert result["services"] == ["kraddr-geo-postgres", "rustfs"]
    assert result["target_sequence"] == ["db", "storage", "geo", "map", "ai", "main"]
    up_command = result["command"][0]
    assert up_command[:2] == ["docker", "compose"]
    assert "up" in up_command
    assert "--build" in up_command
    assert "--force-recreate" in up_command
    assert "kraddr-geo-postgres" in up_command
    assert mock_run.call_count == 4


@patch("tripmate_manager.cli.compose_service")
def test_cli_status_returns_compose_exit_code(mock_compose_service):
    mock_compose_service.status_target.return_value = {
        "success": False,
        "returncode": 17,
        "command": ["docker", "compose", "ps"],
        "stdout": "",
        "stderr": "compose failed",
    }

    assert main(["status", "tripmate"]) == 17


@patch("tripmate_manager.cli.compose_service")
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


@patch("tripmate_manager.cli.compose_service")
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
