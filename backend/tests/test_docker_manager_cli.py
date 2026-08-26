import os
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from kor_travel_docker_manager.cli import build_parser, main
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
        "kor-travel-concierge-postgres",
        "kor-travel-concierge-api",
        "kor-travel-concierge-mcp",
        "kor-travel-concierge-scheduler",
        "kor-travel-concierge-ui",
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "pinvi-postgres",
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
    lock_directory = Path("/tmp") / tmp_path.name
    lock_directory.mkdir(mode=0o700, exist_ok=True)

    with patch.dict(
        os.environ,
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": "local",
            "PINVI_ENVIRONMENT": "development",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "false",
            "KTDM_C6C_DEPLOYMENT_LOCK": str(lock_directory / "ensure.lock"),
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
        "kor-travel-concierge-postgres",
        "kor-travel-concierge-api",
        "kor-travel-concierge-mcp",
        "kor-travel-concierge-scheduler",
        "kor-travel-concierge-ui",
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "pinvi-postgres",
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
def test_cli_pinned_runtime_rebuild_requires_confirmation(mock_compose_service, capsys):
    assert main(["pinvi-pair", "rebuild-pinned"]) == 2

    assert "requires --confirm" in capsys.readouterr().err
    mock_compose_service.rebuild_pinned_runtime.assert_not_called()


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_rebuilds_pinned_runtime(mock_compose_service):
    mock_compose_service.rebuild_pinned_runtime.return_value = {
        "success": True,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }

    assert main(["pinvi-pair", "rebuild-pinned", "--confirm", "--json"]) == 0

    mock_compose_service.rebuild_pinned_runtime.assert_called_once_with()


@patch("kor_travel_docker_manager.cli.retire_legacy_compose_override")
def test_cli_legacy_override_retirement_requires_confirmation(mock_retirement, capsys):
    assert main(["compose-boundary", "retire-legacy-override"]) == 2

    assert "requires --confirm" in capsys.readouterr().err
    mock_retirement.assert_not_called()


@patch("kor_travel_docker_manager.cli.stage_legacy_compose_override")
def test_cli_legacy_override_stage_requires_confirmation(mock_stage, capsys):
    assert (
        main(
            [
                "compose-boundary",
                "stage-legacy-override",
                "--source",
                "/legacy/kor-travel-docker-manager/docker-compose.override.yml",
            ]
        )
        == 2
    )

    assert "requires --confirm" in capsys.readouterr().err
    mock_stage.assert_not_called()


@patch("kor_travel_docker_manager.cli.stage_legacy_compose_override")
def test_cli_stages_legacy_override_through_official_boundary(mock_stage):
    source = "/legacy/kor-travel-docker-manager/docker-compose.override.yml"

    assert (
        main(
            [
                "compose-boundary",
                "stage-legacy-override",
                "--source",
                source,
                "--confirm",
            ]
        )
        == 0
    )

    mock_stage.assert_called_once_with(source_path=Path(source))


@patch("kor_travel_docker_manager.cli.retire_legacy_compose_override")
def test_cli_retires_legacy_override_through_official_boundary(mock_retirement):
    assert main(["compose-boundary", "retire-legacy-override", "--confirm"]) == 0

    mock_retirement.assert_called_once_with()


@patch("kor_travel_docker_manager.cli.activate_canonical_concierge")
def test_cli_canonical_concierge_activation_requires_confirmation(mock_activation, capsys):
    assert main(["compose-boundary", "activate-concierge"]) == 2

    assert "requires --confirm" in capsys.readouterr().err
    mock_activation.assert_not_called()


@patch("kor_travel_docker_manager.cli.activate_canonical_concierge")
def test_cli_activates_canonical_concierge_through_official_boundary(mock_activation):
    assert main(["compose-boundary", "activate-concierge", "--confirm"]) == 0

    mock_activation.assert_called_once_with()


@pytest.mark.parametrize(
    "legacy_action",
    ["bootstrap-pinned-drift", "deploy", "rollback"],
)
def test_cli_does_not_expose_legacy_pair_actions(legacy_action: str) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["pinvi-pair", legacy_action])


def test_cli_rejects_retired_capture_and_exposes_only_rebuild(capsys) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["pinvi-pair", "capture"])

    with pytest.raises(SystemExit, match="0"):
        main(["pinvi-pair", "--help"])

    output = capsys.readouterr().out
    assert "rebuild-pinned" in output
    assert "capture" not in output
    assert "deploy" not in output
    assert "rollback" not in output
    assert "bootstrap-pinned-drift" not in output


@pytest.mark.parametrize(
    "argv",
    [
        ["cache-target", "cutover"],
        ["cache-target", "bootstrap"],
        ["cache-target", "retire-legacy-diagnostic"],
        ["map-ui-auth", "rotate"],
    ],
)
def test_cli_does_not_expose_retired_f1d_commands(argv: list[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(argv)


def test_cli_db_backup_restore_is_not_implemented() -> None:
    # issue #177 reintroduced `db-backup create/list/gc` as a fresh, independent
    # primitive — `restore` is deliberately out of scope for now (separate task).
    with pytest.raises(SystemExit, match="2"):
        main(["db-backup", "restore"])


@patch("kor_travel_docker_manager.cli.create_standalone_backup")
def test_cli_db_backup_create_invokes_service_and_prints_summary(
    mock_create, capsys: pytest.CaptureFixture[str]
) -> None:
    from kor_travel_docker_manager.services.standalone_backup import BackupManifest

    mock_create.return_value = BackupManifest(
        role="geo",
        created_at_unix=1000,
        duration_sec=12.5,
        byte_size=4096,
        sha256="a" * 64,
        backup_filename="geo-1000.dump",
        instance="kor-travel-geo-postgres:127.0.0.1:12500/kor_travel_geo",
        db_size_bytes=8192,
        toc_entry_count=3,
        alembic_head="0099_abcdef",
    )

    assert main(["db-backup", "create", "geo"]) == 0

    mock_create.assert_called_once_with("geo", timeout=14_400)
    out = capsys.readouterr().out
    assert "geo-1000.dump" in out
    assert "4096 bytes" in out


@patch("kor_travel_docker_manager.cli.create_standalone_backup")
def test_cli_db_backup_create_passes_custom_timeout(mock_create) -> None:
    from kor_travel_docker_manager.services.standalone_backup import BackupManifest

    mock_create.return_value = BackupManifest(
        role="geo",
        created_at_unix=1000,
        duration_sec=1.0,
        byte_size=1,
        sha256="a" * 64,
        backup_filename="geo-1000.dump",
        instance="c:127.0.0.1:12500/db",
        db_size_bytes=1,
        toc_entry_count=1,
        alembic_head=None,
    )

    assert main(["db-backup", "create", "geo", "--timeout", "60"]) == 0

    mock_create.assert_called_once_with("geo", timeout=60)


@patch("kor_travel_docker_manager.cli.create_standalone_backup")
def test_cli_db_backup_create_rejects_unknown_role(mock_create) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["db-backup", "create", "not-a-real-role"])
    mock_create.assert_not_called()


@patch("kor_travel_docker_manager.cli.create_standalone_backup")
def test_cli_db_backup_create_surfaces_service_error(mock_create) -> None:
    from kor_travel_docker_manager.services.standalone_backup import StandaloneBackupError

    mock_create.side_effect = StandaloneBackupError("geo pg_dump failed")

    assert main(["db-backup", "create", "geo"]) == 2


@patch("kor_travel_docker_manager.cli.list_standalone_backups")
def test_cli_db_backup_list_invokes_service_with_role(
    mock_list, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_list.return_value = []

    assert main(["db-backup", "list", "pinvi"]) == 0

    mock_list.assert_called_once_with("pinvi")
    assert "no backups for role pinvi" in capsys.readouterr().out


@patch("kor_travel_docker_manager.cli.gc_standalone_backups")
def test_cli_db_backup_gc_requires_keep_flag(mock_gc) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["db-backup", "gc", "geo"])
    mock_gc.assert_not_called()


@patch("kor_travel_docker_manager.cli.gc_standalone_backups")
def test_cli_db_backup_gc_invokes_service_with_keep(
    mock_gc, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_gc.return_value = ["geo-1000.dump"]

    assert main(["db-backup", "gc", "geo", "--keep", "2"]) == 0

    mock_gc.assert_called_once_with("geo", keep=2)
    assert "geo-1000.dump" in capsys.readouterr().out
