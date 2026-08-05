import json
import os
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from kor_travel_docker_manager.cli import build_parser, main
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
    ValidatedComposeCandidate,
)
from kor_travel_docker_manager.services.docker_service import (
    _redact_argv,
    _redact_env_pair,
    _sanitize_labels,
)
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
        "pinvi-dagster",
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
        "pinvi-dagster",
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
    assert get_target("pinvi-api")["id"] == "pinvi"
    assert get_target("main")["id"] == "pinvi"
    assert get_target("metrics")["id"] == "prom"
    # concierge는 geo에 의존하지 않는다(prometheus 다음 별도 분기).
    assert target_sequence_for_target("conc") == [
        "db",
        "storage",
        "gra",
        "cadv",
        "prom",
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


@pytest.mark.parametrize(
    "key",
    [
        # `API_KEY`는 `ACCESS_KEY`에 걸리지 않아 예전에는 평문으로 나갔다.
        # T-012가 inspect를 대시보드에 연결하면서 브라우저에 그대로 보이게 됐던 값들이다.
        "KOR_TRAVEL_MAP_OPINET_API_KEY",
        "KOR_TRAVEL_MAP_KREX_EX_API_KEY",
        "KOR_TRAVEL_MAP_KREX_GO_API_KEY",
        "KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY",
        "KOR_TRAVEL_GEO_VWORLD_API_KEY",
        "SOME_APIKEY",
        "SERVICE_CREDENTIAL",
        "DB_PASSWD",
    ],
)
def test_env_redaction_masks_provider_api_keys(key):
    assert _redact_env_pair(f"{key}=super-secret-value") == f"{key}=<redacted>"


@pytest.mark.parametrize(
    "pair",
    [
        "POSTGRES_DB=kor_travel_geo",
        "KOR_TRAVEL_GEO_SOURCE_DIR=/data/juso",
        "KTDM_ADMIN_USERNAME=admin",
        "PORT=12901",
    ],
)
def test_env_redaction_keeps_non_secret_values(pair):
    """과다 redaction은 안전하지만, 운영에 필요한 일반 설정까지 가리면 패널이 쓸모없어진다."""
    assert _redact_env_pair(pair) == pair


@pytest.mark.parametrize(
    ("pair", "expected"),
    [
        # key 이름이 어떤 SENSITIVE_KEY_PARTS에도 걸리지 않는데 값에 비밀번호가 박혀 있다.
        # 적대적 리뷰 2명이 각각 찾은 실제 노출 경로다.
        (
            "PINVI_DATABASE_URL=postgresql+asyncpg://pinvi:s3cr3t@127.0.0.1:5432/pinvi",
            "PINVI_DATABASE_URL=postgresql+asyncpg://pinvi:<redacted>@127.0.0.1:5432/pinvi",
        ),
        (
            "KOR_TRAVEL_MAP_PG_DSN=postgresql+psycopg://map:pw123@db:5432/kor_travel_map",
            "KOR_TRAVEL_MAP_PG_DSN=postgresql+psycopg://map:<redacted>@db:5432/kor_travel_map",
        ),
        (
            "KTG_DAGSTER_PG_URL=postgresql://geo:hunter2@127.0.0.1/geo",
            "KTG_DAGSTER_PG_URL=postgresql://geo:<redacted>@127.0.0.1/geo",
        ),
    ],
)
def test_env_redaction_masks_credentials_embedded_in_dsn_values(pair, expected):
    assert _redact_env_pair(pair) == expected


@pytest.mark.parametrize(
    "pair",
    [
        # credential이 없는 URL은 그대로 읽혀야 패널이 쓸모 있다.
        "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL=http://127.0.0.1:12501",
        "KOR_TRAVEL_MAP_API_DAGSTER_GRAPHQL_URL=http://127.0.0.1:12702/graphql",
        "KOR_TRAVEL_MAP_OBJECT_STORE_ENDPOINT_URL=http://127.0.0.1:12101",
    ],
)
def test_env_redaction_keeps_credential_free_urls(pair):
    assert _redact_env_pair(pair) == pair


def test_argv_redaction_masks_url_credentials():
    """cmd/entrypoint는 그동안 어떤 필터도 거치지 않던 통로였다."""
    assert _redact_argv(["sh", "-c", "psql postgresql://u:pw@h/db -c 'select 1'"]) == [
        "sh",
        "-c",
        "psql postgresql://u:<redacted>@h/db -c 'select 1'",
    ]
    assert _redact_argv(None) is None
    assert _redact_argv([]) == []
    # credential이 없는 일반 command는 그대로 둔다.
    assert _redact_argv(["postgres", "-c", "shared_buffers=512MB"]) == [
        "postgres",
        "-c",
        "shared_buffers=512MB",
    ]


def test_label_sanitizer_uses_the_same_predicate():
    assert _sanitize_labels({"OPINET_API_KEY": "v", "app": "manager"}) == {
        "OPINET_API_KEY": "<redacted>",
        "app": "manager",
    }


@patch.object(
    ComposeService,
    "_validate_current_compose_candidate_unlocked",
    return_value=ValidatedComposeCandidate(
        resolved={},
        system_bind_snapshots=(),
        raw_volume_graph_hash="raw-stable",
        resolved_volume_graph_hash="resolved-stable",
    ),
)
@patch("kor_travel_docker_manager.services.compose_service.subprocess.run")
@patch("kor_travel_docker_manager.services.compose_service.os.path.exists", return_value=False)
def test_compose_ensure_build_command(
    mock_exists,
    mock_run,
    _mock_candidate_validation,
    tmp_path: Path,
):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "started"
    mock_run.return_value.stderr = ""

    with patch.dict(
        os.environ,
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": "local",
            "PINVI_ENVIRONMENT": "development",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "false",
            "KTDM_C6C_DEPLOYMENT_LOCK": str(tmp_path / "ensure.lock"),
        },
    ):
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
        "pinvi-dagster",
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

    assert main(["status", "pinvi"]) == 17


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


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_deploys_only_through_compatible_pair_workflow(mock_compose_service):
    mock_compose_service.deploy_compatible_pinvi_pair.return_value = {
        "success": True,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }

    assert main(["pinvi-pair", "deploy", "--build"]) == 0
    mock_compose_service.deploy_compatible_pinvi_pair.assert_called_once_with(
        build=True,
        recreate=True,
        wait_timeout=120,
        expected_alembic_head=None,
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_deploy_passes_explicit_wait_timeout(mock_compose_service):
    """issue #88: 마이그레이션을 수반하는 배포는 기본 120초보다 큰 값을 지정해야 한다."""
    mock_compose_service.deploy_compatible_pinvi_pair.return_value = {
        "success": True,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }

    assert main(["pinvi-pair", "deploy", "--wait-timeout", "1200"]) == 0
    mock_compose_service.deploy_compatible_pinvi_pair.assert_called_once_with(
        build=False,
        recreate=True,
        wait_timeout=1200,
        expected_alembic_head=None,
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_deploy_passes_expected_alembic_head(mock_compose_service):
    """issue #109: candidate image의 alembic head를 명시하면 그대로 전달돼야 한다."""
    mock_compose_service.deploy_compatible_pinvi_pair.return_value = {
        "success": True,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }

    assert (
        main(
            [
                "pinvi-pair",
                "deploy",
                "--expected-alembic-head",
                "0078_cache_target_gc_observe",
            ]
        )
        == 0
    )
    mock_compose_service.deploy_compatible_pinvi_pair.assert_called_once_with(
        build=False,
        recreate=True,
        wait_timeout=120,
        expected_alembic_head="0078_cache_target_gc_observe",
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_captures_only_verified_compatible_pair(mock_compose_service):
    mock_compose_service.capture_compatible_pinvi_pair.return_value = {
        "success": True,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }

    assert main(["pinvi-pair", "capture", "--verified-compatible", "--build"]) == 0
    mock_compose_service.capture_compatible_pinvi_pair.assert_called_once_with(
        verified_compatible=True,
        build=True,
        wait_timeout=120,
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_capture_passes_explicit_wait_timeout(mock_compose_service):
    """issue #88: clean bootstrap capture도 kor-travel-map API의 alembic 마이그레이션을
    기다려야 하므로 같은 --wait-timeout 오버라이드가 필요하다."""
    mock_compose_service.capture_compatible_pinvi_pair.return_value = {
        "success": True,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }

    assert main(
        ["pinvi-pair", "capture", "--verified-compatible", "--wait-timeout", "1200"]
    ) == 0
    mock_compose_service.capture_compatible_pinvi_pair.assert_called_once_with(
        verified_compatible=True,
        build=False,
        wait_timeout=1200,
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_rolls_back_only_the_whole_compatible_pair(mock_compose_service):
    mock_compose_service.rollback_compatible_pinvi_pair.return_value = {
        "success": True,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }

    assert main(["pinvi-pair", "rollback"]) == 0
    mock_compose_service.rollback_compatible_pinvi_pair.assert_called_once_with()


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_runs_cache_target_cutover_as_one_process_window(
    mock_compose_service,
):
    mock_compose_service.run_cache_target_cutover.return_value = {
        "success": True,
        "returncode": 0,
    }

    assert main(
        [
            "cache-target",
            "cutover",
            "--cutover-id",
            "11111111-1111-4111-8111-111111111111",
            "--expected-restore-epoch",
            "3",
            "--reason",
            "production cutover",
            "--wait-timeout",
            "1200",
        ]
    ) == 0
    mock_compose_service.run_cache_target_cutover.assert_called_once_with(
        cutover_id="11111111-1111-4111-8111-111111111111",
        expected_restore_epoch=3,
        reason="production cutover",
        wait_timeout=1200,
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_bootstraps_default_off_contract_only_with_explicit_confirmation(
    mock_compose_service, capsys
):
    assert main(["cache-target", "bootstrap"]) == 2
    mock_compose_service.bootstrap_cache_target_default_off.assert_not_called()
    assert "requires --confirm" in capsys.readouterr().err

    mock_compose_service.bootstrap_cache_target_default_off.return_value = {
        "success": True,
        "returncode": 0,
        "sync_enabled": "false",
        "role_binding_sha256": "a" * 64,
    }

    assert main(["cache-target", "bootstrap", "--confirm", "--json"]) == 0
    mock_compose_service.bootstrap_cache_target_default_off.assert_called_once_with()
    payload = json.loads(capsys.readouterr().out)
    assert payload["sync_enabled"] == "false"
    assert payload["role_binding_sha256"] == "a" * 64


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_runs_db_backup_create_with_selected_role(mock_compose_service):
    mock_compose_service.create_standalone_backup.return_value = {
        "success": True,
        "returncode": 0,
        "role": "pinvi",
        "backup_filename": "20260101T000000Z_pinvi_0001.dump",
    }

    assert main(["db-backup", "create", "--role", "pinvi"]) == 0
    mock_compose_service.create_standalone_backup.assert_called_once_with(role="pinvi")


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_db_backup_create_rejects_unknown_role(mock_compose_service):
    with pytest.raises(SystemExit):
        main(["db-backup", "create", "--role", "not_a_role"])
    mock_compose_service.create_standalone_backup.assert_not_called()


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_runs_db_backup_list_with_defaults(mock_compose_service):
    mock_compose_service.list_standalone_backups.return_value = {
        "success": True,
        "returncode": 0,
        "backups": [],
        "warnings": [],
    }

    assert main(["db-backup", "list"]) == 0
    mock_compose_service.list_standalone_backups.assert_called_once_with(
        role=None, gc=False, keep_count=5, keep_days=14
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_runs_db_backup_list_with_gc_and_explicit_role(mock_compose_service):
    mock_compose_service.list_standalone_backups.return_value = {
        "success": True,
        "returncode": 0,
        "backups": [],
        "warnings": [],
        "gc": [],
    }

    assert (
        main(
            [
                "db-backup",
                "list",
                "--role",
                "pinvi",
                "--gc",
                "--keep-count",
                "2",
                "--keep-days",
                "7",
            ]
        )
        == 0
    )
    mock_compose_service.list_standalone_backups.assert_called_once_with(
        role="pinvi", gc=True, keep_count=2, keep_days=7
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_db_backup_list_prints_warnings_to_stderr(mock_compose_service, capsys):
    mock_compose_service.list_standalone_backups.return_value = {
        "success": True,
        "returncode": 0,
        "backups": [],
        "warnings": ["map_application: corrupt.manifest.json is invalid"],
    }

    assert main(["db-backup", "list"]) == 0
    captured = capsys.readouterr()
    assert "corrupt.manifest.json" in captured.err


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_db_backup_list_json_output_includes_warnings_and_backups(
    mock_compose_service, capsys
):
    mock_compose_service.list_standalone_backups.return_value = {
        "success": True,
        "returncode": 0,
        "backups": [{"role": "pinvi", "backup_filename": "x.dump"}],
        "warnings": [],
    }

    assert main(["db-backup", "list", "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["backups"][0]["role"] == "pinvi"


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_db_backup_restore_refuses_without_confirm(
    mock_compose_service, capsys
) -> None:
    """T-055: --confirm 없이는 compose_service를 아예 호출하지 않는다 —
    fail-closed 기본값."""
    assert (
        main(
            [
                "db-backup",
                "restore",
                "--role",
                "pinvi",
                "--backup-id",
                "x.dump",
                "--expected-schema-revision",
                "0001_abc",
            ]
        )
        == 2
    )
    mock_compose_service.restore_standalone_backup.assert_not_called()
    captured = capsys.readouterr()
    assert "--confirm" in captured.err


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_db_backup_restore_runs_with_confirm(mock_compose_service) -> None:
    mock_compose_service.restore_standalone_backup.return_value = {
        "success": True,
        "returncode": 0,
        "role": "pinvi",
        "backup_filename": "x.dump",
        "schema_revision": "0001_abc",
        "sha256": "a" * 64,
        "byte_size": 5,
        "created_at_unix": 1_700_000_000,
    }

    assert (
        main(
            [
                "db-backup",
                "restore",
                "--role",
                "pinvi",
                "--backup-id",
                "x.dump",
                "--expected-schema-revision",
                "0001_abc",
                "--confirm",
            ]
        )
        == 0
    )
    mock_compose_service.restore_standalone_backup.assert_called_once_with(
        role="pinvi", backup_filename="x.dump", expected_schema_revision="0001_abc"
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_db_backup_restore_propagates_contract_error(
    mock_compose_service, capsys
) -> None:
    mock_compose_service.restore_standalone_backup.side_effect = DeploymentContractError(
        "pinvi current schema revision differs from the operator-confirmed expectation"
    )

    assert (
        main(
            [
                "db-backup",
                "restore",
                "--role",
                "pinvi",
                "--backup-id",
                "x.dump",
                "--expected-schema-revision",
                "0001_abc",
                "--confirm",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "differs from the operator-confirmed expectation" in captured.err


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_db_backup_list_human_readable_output_includes_timestamp(
    mock_compose_service, capsys
):
    """적대적 리뷰에서 human-readable 출력에 시각 필드가 빠져 있던 것을 찾았다 —
    코드를 요구사항에 맞추는 대신 요구사항 문구를 코드에 맞춰 낮춰놨던 실수를
    바로잡는 회귀 테스트."""
    mock_compose_service.list_standalone_backups.return_value = {
        "success": True,
        "returncode": 0,
        "backups": [
            {
                "role": "pinvi",
                "backup_filename": "20260101T000000Z_pinvi_0001_abc.dump",
                "schema_revision": "0001_abc",
                "byte_size": 5,
                "sha256": "a" * 64,
                "created_at_unix": 1_735_689_600,
            }
        ],
        "warnings": [],
    }

    assert main(["db-backup", "list"]) == 0
    captured = capsys.readouterr()
    assert "2025-01-01T00:00:00Z" in captured.out


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_runs_cache_target_initial_cutover_with_explicit_evidence(
    mock_compose_service,
):
    mock_compose_service.run_cache_target_initial_cutover.return_value = {
        "success": True,
        "returncode": 0,
    }

    assert main(
        [
            "cache-target",
            "initial",
            "--cutover-id",
            "11111111-1111-4111-8111-111111111111",
            "--expected-restore-epoch",
            "3",
            "--reason",
            "production initial cutover",
        ]
    ) == 0
    mock_compose_service.run_cache_target_initial_cutover.assert_called_once_with(
        cutover_id="11111111-1111-4111-8111-111111111111",
        expected_restore_epoch=3,
        reason="production initial cutover",
    )


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_enables_cache_target_through_durable_coordinator(mock_compose_service):
    mock_compose_service.enable_cache_target_sync.return_value = {
        "success": True,
        "returncode": 0,
    }

    assert main(["cache-target", "enable"]) == 0
    mock_compose_service.enable_cache_target_sync.assert_called_once_with()
