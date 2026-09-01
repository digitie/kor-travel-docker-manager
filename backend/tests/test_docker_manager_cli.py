import json
import os
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kor_travel_docker_manager.cli import build_parser, main
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
    PinnedRuntimePrejournalFailure,
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


@patch("kor_travel_docker_manager.cli.compose_service")


def test_cli_rebuild_pinned_runtime_emits_safe_prejournal_failure_json(
    mock_compose_service,
    capsys,
):
    mock_compose_service.rebuild_pinned_runtime.side_effect = PinnedRuntimePrejournalFailure(
        "application_builder"
    )

    assert main(["pinvi-pair", "rebuild-pinned", "--confirm", "--json"]) == 2

    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "classification": "prejournal_failure",
        "stage": "application_builder",
    }


@patch("kor_travel_docker_manager.cli.compose_service")


def test_cli_rebuild_pinned_runtime_hides_unclassified_contract_error_in_json(
    mock_compose_service,
    capsys,
):
    mock_compose_service.rebuild_pinned_runtime.side_effect = DeploymentContractError(
        "sensitive unexpected contract detail"
    )

    assert main(["pinvi-pair", "rebuild-pinned", "--confirm", "--json"]) == 2

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "status": "failed",
        "classification": "unclassified",
    }
    assert "sensitive unexpected contract detail" not in captured.out
    assert not captured.err


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
    from kor_travel_docker_manager.services.standalone_backup import GcOutcome

    mock_gc.return_value = GcOutcome(deleted=("geo-1000.dump",), orphans_removed=())

    assert main(["db-backup", "gc", "geo", "--keep", "2"]) == 0

    mock_gc.assert_called_once_with("geo", keep=2)
    assert "geo-1000.dump" in capsys.readouterr().out


@patch("kor_travel_docker_manager.cli.gc_standalone_backups")


def test_cli_db_backup_gc_reports_orphans_separately(
    mock_gc, capsys: pytest.CaptureFixture[str]
) -> None:
    """회전과 잔해 수거를 합쳐 세면 '왜 예상보다 많이 지워졌나'를 알 수 없다."""

    from kor_travel_docker_manager.services.standalone_backup import GcOutcome

    mock_gc.return_value = GcOutcome(
        deleted=("geo-1000.dump",), orphans_removed=("geo-2000.dump",)
    )

    assert main(["db-backup", "gc", "geo", "--keep", "2"]) == 0

    output = capsys.readouterr().out
    assert "deleted 1 backup(s)" in output
    assert "removed 1 orphaned dump(s)" in output
    assert "geo-2000.dump" in output


# --- ktdctl pin (KUM-M1·M2) ---------------------------------------------------


@pytest.fixture
def pin_cli_env(tmp_path, monkeypatch):
    """pin CLI를 격리 registry에서 실행한다."""

    from kor_travel_docker_manager.services import runtime_pin_registry

    registry_path = tmp_path / "runtime-pins.json"
    monkeypatch.setenv(runtime_pin_registry.RUNTIME_PINS_FILE_ENV, str(registry_path))
    monkeypatch.setenv(
        runtime_pin_registry.RUNTIME_PINS_PUBLIC_FILE_ENV,
        str(tmp_path / "public.json"),
    )
    runtime_pin_registry.clear_runtime_pin_registry_cache()
    yield registry_path
    runtime_pin_registry.clear_runtime_pin_registry_cache()


def _seed_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "runtime-pins.seed.json"


def test_pin_parser_registers_every_leaf_command():
    parser = build_parser()

    for action in (
        "init",
        "show",
        "verify",
        "publish-generation",
        "rotate",
        "rotate-pair",
        "block",
        "rollback",
    ):
        args = parser.parse_args(
            {
                "init": ["pin", "init", "--seed", "x"],
                "show": ["pin", "show"],
                "verify": ["pin", "verify"],
                "publish-generation": [
                    "pin",
                    "publish-generation",
                    "--manifest",
                    "/root/state/pinned-runtime-generation-v6.json",
                    "--journal",
                    "/root/state/pinned-runtime-rebuild-v8-a.json",
                ],
                "rotate": [
                    "pin",
                    "rotate",
                    "--role",
                    "map",
                    "--revision",
                    "a" * 40,
                    "--reason",
                    "r",
                ],
                "rotate-pair": [
                    "pin",
                    "rotate-pair",
                    "--map-revision",
                    "a" * 40,
                    "--pinvi-revision",
                    "b" * 40,
                    "--reason",
                    "r",
                ],
                "block": ["pin", "block", "a" * 64, "--reason", "r"],
                "rollback": ["pin", "rollback", "--to", "a" * 64, "--reason", "r"],
            }[action]
        )
        assert args.pin_action == action
        assert callable(args.func)


def test_pin_rotate_role_choices_come_from_the_canonical_runtime_source_roles():
    """GM-18: `pin rotate --role`의 choices는 `RUNTIME_SOURCE_ROLES`(정본,
    pinned_runtime_release.py)에서 가져와야 한다 — 여기서 독립적으로
    `["map", "pinvi"]`를 다시 적으면 정본이 바뀌어도 이 CLI만 조용히
    구식으로 남을 수 있다."""

    from kor_travel_docker_manager.services.pinned_runtime_release import (
        RUNTIME_SOURCE_ROLES,
    )

    parser = build_parser()

    # 정본에 있는 각 role은 그대로 받아들여야 한다.
    for role in RUNTIME_SOURCE_ROLES:
        args = parser.parse_args(
            ["pin", "rotate", "--role", role, "--revision", "a" * 40, "--reason", "r"]
        )
        assert args.role == role

    # 정본에 없는 값은 choices 제약으로 거부돼야 한다(하드코딩된 목록이 아니라
    # 실제로 RUNTIME_SOURCE_ROLES를 참조하고 있다는 것의 반증 — 이 값이 우연히
    # 통과하면 choices가 더 이상 정본과 연결돼 있지 않다는 뜻이다).
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["pin", "rotate", "--role", "not-a-real-role", "--revision", "a" * 40, "--reason", "r"]
        )


def test_pin_rotate_role_choices_track_the_canonical_tuple_dynamically(monkeypatch):
    """GM-18 리뷰 반영(2인 적대적 리뷰 공통 지적): 위 테스트는 지금 값이 우연히
    같은 독립 하드코딩(`choices=["map", "pinvi"]`)과 실제 배선을 구분하지
    못한다 — 둘 다 오늘은 같은 값을 받아들인다. 이 테스트는
    `kor_travel_docker_manager.cli.RUNTIME_SOURCE_ROLES` 자체를 다른 값으로
    바꿔치기해 `build_parser()`의 choices가 그 값을 실제로 따라가는지 본다.
    하드코딩이었다면 patch는 무시되고 여전히 ("map", "pinvi")만 받아들였을
    것이다."""

    import kor_travel_docker_manager.cli as cli_module

    monkeypatch.setattr(cli_module, "RUNTIME_SOURCE_ROLES", ("only-patched-role",))

    parser = cli_module.build_parser()

    args = parser.parse_args(
        ["pin", "rotate", "--role", "only-patched-role", "--revision", "a" * 40, "--reason", "r"]
    )
    assert args.role == "only-patched-role"

    # patch 전에는 유효했던 "map"이 지금은 거부돼야 한다 — choices가 patch된
    # 값을 그대로 반영한다는 뜻이고, 어딘가 ["map", "pinvi"]가 여전히
    # 하드코딩돼 있었다면 이 assert가 실패했을 것이다.
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["pin", "rotate", "--role", "map", "--revision", "a" * 40, "--reason", "r"]
        )


@pytest.mark.parametrize(
    "argv",
    [
        ["pin", "init", "--seed", "seed.json"],
        ["pin", "rotate", "--role", "map", "--revision", "a" * 40, "--reason", "r"],
        [
            "pin",
            "rotate-pair",
            "--map-revision",
            "a" * 40,
            "--pinvi-revision",
            "b" * 40,
            "--reason",
            "r",
        ],
        ["pin", "block", "a" * 64, "--reason", "r"],
        ["pin", "rollback", "--to", "a" * 64, "--reason", "r"],
    ],
)
def test_pin_mutations_refuse_without_confirm(argv, pin_cli_env, capsys):
    assert main(argv) == 2
    assert "--confirm" in capsys.readouterr().err
    assert not pin_cli_env.exists()


def test_pin_publish_generation_refuses_without_confirm(capsys):
    assert (
        main(
            [
                "pin",
                "publish-generation",
                "--manifest",
                "/root/state/pinned-runtime-generation-v6.json",
                "--journal",
                "/root/state/pinned-runtime-rebuild-v8-a.json",
            ]
        )
        == 2
    )
    assert "--confirm" in capsys.readouterr().err


def test_pin_publish_generation_requires_root(capsys):
    with patch("kor_travel_docker_manager.cli._running_as_root", return_value=False):
        assert (
            main(
                [
                    "pin",
                    "publish-generation",
                    "--manifest",
                    "/root/state/pinned-runtime-generation-v6.json",
                    "--journal",
                    "/root/state/pinned-runtime-rebuild-v8-a.json",
                    "--confirm",
                ]
            )
            == 2
        )
    assert "root" in capsys.readouterr().err


def test_pin_show_without_a_registry_fails_closed(pin_cli_env, capsys):
    assert main(["pin", "show"]) == 2
    assert "missing" in capsys.readouterr().err


def test_pin_init_bootstraps_from_the_packaged_seed(pin_cli_env, capsys):
    assert main(["pin", "init", "--seed", str(_seed_path()), "--confirm"]) == 0

    assert pin_cli_env.exists()
    output = capsys.readouterr().out
    assert "bootstrapped" in output
    # seed의 terminal 목록은 부트스트랩에서도 유지된다.
    assert "blocked" in output


def test_pin_init_refuses_to_overwrite_without_force(pin_cli_env, capsys):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()

    assert main(["pin", "init", "--seed", str(_seed_path()), "--confirm"]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err


def test_pin_show_and_verify_are_read_only_and_report_lifecycle(pin_cli_env, capsys):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()
    before = pin_cli_env.read_bytes()

    assert main(["pin", "show", "--json"]) == 0
    show_output = capsys.readouterr().out
    assert '"blocked_pinsets"' in show_output

    # generation public copy가 없으면 registry digest가 맞아도 verify는 비정상 종료한다.
    # registry만 보고 0을 주면 M05 public generation gate가 반쪽 상태를 놓친다.
    verify_code = main(["pin", "verify", "--json"])
    verify_output = capsys.readouterr().out
    assert '"digest_recomputation": "ok"' in verify_output
    assert '"current_pinset_is_blocked"' in verify_output
    assert '"generation_public_copy": "invalid"' in verify_output
    assert verify_code == 1
    assert pin_cli_env.read_bytes() == before


def test_pin_show_without_json_flag_still_only_prints_to_stderr(pin_cli_env, capsys):
    """--json이 없으면 실패해도 stdout은 비운다 — 사람이 읽는 출력은 그대로 stderr다."""

    assert main(["pin", "show"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing" in captured.err


@pytest.mark.parametrize(
    "argv, patch_root",
    [
        (["pin", "show", "--json"], False),
        (["pin", "verify", "--json"], False),
        (
            [
                "pin",
                "rotate",
                "--role",
                "map",
                "--revision",
                "a" * 40,
                "--reason",
                "r",
                "--confirm",
                "--json",
            ],
            False,
        ),
        (
            [
                "pin",
                "rotate-pair",
                "--map-revision",
                "a" * 40,
                "--pinvi-revision",
                "b" * 40,
                "--reason",
                "r",
                "--confirm",
                "--json",
            ],
            False,
        ),
        (["pin", "block", "a" * 64, "--reason", "r", "--confirm", "--json"], True),
        (
            ["pin", "rollback", "--to", "a" * 64, "--reason", "r", "--confirm", "--json"],
            False,
        ),
    ],
)
def test_pin_json_failures_always_emit_status_failed_json_to_stdout(
    argv, patch_root, pin_cli_env, capsys
):
    """GM-06: --json이면 실패해도 stdout이 비지 않는다 (pin show-pending 관례 확장).

    registry 파일이 없는 상태에서 각 pin 하위 명령을 --json으로 실행하면, 예전에는
    stderr에만 사람이 읽는 메시지를 내고 stdout은 비웠다 — `| jq`가 파싱 대상 없이
    죽는다. 이제는 stdout에 {"status": "failed", "detail": ...} JSON을 낸다.
    """

    if patch_root:
        with patch("kor_travel_docker_manager.cli._running_as_root", return_value=True):
            code = main(argv)
    else:
        code = main(argv)

    captured = capsys.readouterr()
    assert code == 2
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    assert "missing" in payload["detail"]
    assert "missing" in captured.err


def test_pin_init_json_failure_emits_status_failed_json_to_stdout(
    pin_cli_env, capsys, tmp_path
):
    missing_seed = tmp_path / "does-not-exist-seed.json"

    code = main(["pin", "init", "--seed", str(missing_seed), "--confirm", "--json"])

    captured = capsys.readouterr()
    assert code == 2
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    assert "missing" in payload["detail"]
    assert "missing" in captured.err


@patch("kor_travel_docker_manager.cli.verify_runtime_pin_registry")
@patch("kor_travel_docker_manager.cli.read_published_pinned_runtime_generation")
def test_pin_verify_allows_a_valid_terminal_generation_pending_new_pair(
    generation_reader,
    registry_verifier,
    capsys,
    monkeypatch,
):
    registry_verifier.return_value = {
        "published_copy": "current",
        "current_pinset_is_blocked": False,
    }
    generation_reader.return_value = {
        "status": "ok",
        "pinset_binding": {"status": "pending_rebuild"},
    }
    execution_registry = MagicMock()
    execution_registry.current_matches.return_value = True
    execution_registry.is_unconditionally_blocked_current.return_value = False
    monkeypatch.setattr(
        "kor_travel_docker_manager.cli.load_runtime_execution_registry",
        lambda: execution_registry,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.cli.trusted_manager_source_revision",
        lambda: "a" * 40,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.cli.verify_runtime_execution_registry",
        lambda: {"execution_public_copy": "current"},
    )

    assert main(["pin", "verify", "--json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["generation_public_copy"] == "pending_rebuild"
    assert output["generation_pinset_binding"] == "pending_rebuild"
    assert output["execution_binding"] == "current"
    assert output["execution_public_copy"] == "current"


@patch("kor_travel_docker_manager.cli.verify_runtime_pin_registry")
@patch("kor_travel_docker_manager.cli.read_published_pinned_runtime_generation")
def test_pin_verify_allows_a_legacy_terminal_with_current_unblocked_execution(
    generation_reader,
    registry_verifier,
    capsys,
    monkeypatch,
):
    registry_verifier.return_value = {
        "published_copy": "current",
        "current_pinset_is_blocked": True,
    }
    generation_reader.return_value = {
        "status": "ok",
        "pinset_binding": {"status": "pending_rebuild"},
    }
    execution_registry = MagicMock()
    execution_registry.current_matches.return_value = True
    execution_registry.is_unconditionally_blocked_current.return_value = False
    monkeypatch.setattr(
        "kor_travel_docker_manager.cli.load_runtime_execution_registry",
        lambda: execution_registry,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.cli.trusted_manager_source_revision",
        lambda: "a" * 40,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.cli.verify_runtime_execution_registry",
        lambda: {"execution_public_copy": "current"},
    )

    assert main(["pin", "verify", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["current_pinset_is_blocked"] is True
    assert output["execution_binding"] == "current"
    assert output["current_execution_is_blocked"] is False


@patch("kor_travel_docker_manager.cli.verify_runtime_pin_registry")
@patch("kor_travel_docker_manager.cli.read_published_pinned_runtime_generation")
def test_pin_verify_guides_manager_drift_to_rebind_not_a_self_targeted_rollback(
    generation_reader,
    registry_verifier,
    capsys,
    monkeypatch,
):
    """GM-01 F1 회귀: source는 그대로고 trusted Manager revision만 바뀐 표준 업그레이드에서,
    verify는 rebind-execution을 안내해야 한다. rollback --to <현재 pinset>은 항상
    'already uses this pinset'으로 거부되므로 안내로 주면 안 된다.
    """
    from unittest.mock import MagicMock

    registry_verifier.return_value = {
        "published_copy": "current",
        "current_pinset_is_blocked": False,
    }
    generation_reader.return_value = {"status": "ok", "pinset_binding": {"status": "match"}}

    pins = MagicMock()
    pins.pinset_sha256 = "a" * 64
    pins.map_revision = "1" * 40
    pins.pinvi_revision = "2" * 40

    execution = MagicMock()
    execution.current_matches.return_value = False  # manager revision만 다름
    execution.current.source_pinset_sha256 = "a" * 64  # == 현재 pinset
    execution.current.map_revision = "1" * 40
    execution.current.pinvi_revision = "2" * 40
    execution.is_unconditionally_blocked_current.return_value = False

    monkeypatch.setattr(
        "kor_travel_docker_manager.cli.load_runtime_execution_registry", lambda: execution
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.cli.load_runtime_pin_registry", lambda: pins
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.cli.trusted_manager_source_revision", lambda: "9" * 40
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.cli.verify_runtime_execution_registry",
        lambda: {"execution_public_copy": "current"},
    )

    code = main(["pin", "verify"])
    captured = capsys.readouterr()
    assert code == 1
    assert "rebind-execution" in captured.err
    # 자기 자신을 향하는(항상 실패하는) rollback 안내를 주지 않는다.
    assert "rollback --to" not in captured.err


def test_pin_rotate_computes_the_digest_and_records_the_reason(pin_cli_env, capsys):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()

    exit_code = main(
        [
            "pin",
            "rotate-pair",
            "--map-revision",
            "c" * 40,
            "--pinvi-revision",
            "d" * 40,
            "--reason",
            "새 PinVi head",
            "--confirm",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "rotated Map/PinVi pair" in output
    assert "새 PinVi head" in output


def test_cli_db_backup_restore_plan_is_read_only_and_gates_on_findings(capsys) -> None:
    """계획은 아무것도 바꾸지 않고, 차단 요인이 있으면 비정상 종료로 알린다."""

    from types import SimpleNamespace

    plan = SimpleNamespace(
        role="geo",
        backup_filename="geo-1.dump",
        dump_path="/backups/geo/geo-1.dump",
        manifest=SimpleNamespace(
            byte_size=10,
            sha256="a" * 64,
            alembic_head="0001_head",
            to_json=lambda: {},
        ),
        observed_sha256="b" * 64,
        observed_byte_size=10,
        live_alembic_head="0007_later",
        containers=("kor-travel-geo-postgres",),
        findings=(
            SimpleNamespace(
                code="SHA256_MISMATCH", text="digest가 다릅니다", blocking=True
            ),
        ),
        restorable=False,
        to_json=lambda: {"restorable": False},
    )

    with patch(
        "kor_travel_docker_manager.cli.plan_standalone_restore", return_value=plan
    ) as planner:
        exit_code = main(["db-backup", "restore-plan", "geo"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "digest가 다릅니다" in output
    assert "복원하면 안 됩니다" in output
    planner.assert_called_once_with("geo", backup_filename=None)


def test_cli_db_backup_restore_plan_reports_a_healthy_backup(capsys) -> None:
    from types import SimpleNamespace

    plan = SimpleNamespace(
        role="geo",
        backup_filename="geo-1.dump",
        dump_path="/backups/geo/geo-1.dump",
        manifest=SimpleNamespace(
            byte_size=10, sha256="a" * 64, alembic_head="0001_head", to_json=lambda: {}
        ),
        observed_sha256="a" * 64,
        observed_byte_size=10,
        live_alembic_head="0001_head",
        containers=(),
        findings=(SimpleNamespace(code="OK", text="모두 일치합니다", blocking=False),),
        restorable=True,
        to_json=lambda: {"restorable": True},
    )

    with patch("kor_travel_docker_manager.cli.plan_standalone_restore", return_value=plan):
        assert main(["db-backup", "restore-plan", "geo"]) == 0

    output = capsys.readouterr().out
    # 파괴적 복원 명령은 아직 없지만, scratch DB 리허설 경로는 안내한다.
    assert "rehearse-restore" in output


def test_cli_db_backup_rehearse_restore_reports_a_verified_backup(capsys) -> None:
    from types import SimpleNamespace

    outcome = SimpleNamespace(
        role="geo",
        backup_filename="geo-1.dump",
        plan=SimpleNamespace(to_json=lambda: {}),
        attempted=True,
        restore_succeeded=True,
        scratch_database="ktdm_rehearsal_1000",
        restored_alembic_head="0001_head",
        restored_db_size_bytes=12345,
        duration_sec=1.23,
        findings=(SimpleNamespace(code="OK", text="검증 성공", blocking=False),),
        verified=True,
        to_json=lambda: {"verified": True},
    )

    with patch(
        "kor_travel_docker_manager.cli.rehearse_standalone_restore", return_value=outcome
    ) as rehearser:
        exit_code = main(["db-backup", "rehearse-restore", "geo"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "검증됐습니다" in output
    assert "ktdm_rehearsal_1000" in output
    rehearser.assert_called_once_with("geo", backup_filename=None, timeout=14_400)


def test_cli_db_backup_rehearse_restore_fails_closed_when_restore_is_unverified(
    capsys,
) -> None:
    from types import SimpleNamespace

    outcome = SimpleNamespace(
        role="geo",
        backup_filename="geo-1.dump",
        plan=SimpleNamespace(to_json=lambda: {}),
        attempted=True,
        restore_succeeded=False,
        scratch_database="ktdm_rehearsal_1000",
        restored_alembic_head=None,
        restored_db_size_bytes=None,
        duration_sec=1.23,
        findings=(
            SimpleNamespace(
                code="REHEARSAL_RESTORE_FAILED", text="pg_restore가 실패했습니다", blocking=True
            ),
        ),
        verified=False,
        to_json=lambda: {"verified": False},
    )

    with patch(
        "kor_travel_docker_manager.cli.rehearse_standalone_restore", return_value=outcome
    ):
        exit_code = main(["db-backup", "rehearse-restore", "geo", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["verified"] is False


# --- ktdctl offbox-sync (GM-08) -----------------------------------------------


def test_cli_offbox_sync_run_requires_root(capsys) -> None:
    with patch("kor_travel_docker_manager.cli._running_as_root", return_value=False):
        assert main(["offbox-sync", "run"]) == 2
    assert "root" in capsys.readouterr().err


def test_cli_offbox_sync_run_reports_all_verified(capsys) -> None:
    from types import SimpleNamespace

    outcome = SimpleNamespace(
        destination_host="backup-vault.internal",
        targets=(
            SimpleNamespace(label="geo", synced=True, verified=True, detail="synced and verified"),
        ),
        all_verified=True,
        to_json=lambda: {"all_verified": True},
    )

    with (
        patch("kor_travel_docker_manager.cli._running_as_root", return_value=True),
        patch(
            "kor_travel_docker_manager.cli.sync_backups_offbox", return_value=outcome
        ) as syncer,
    ):
        exit_code = main(["offbox-sync", "run", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_verified"] is True
    syncer.assert_called_once_with(include_pin_registry=True, timeout=14_400)


def test_cli_offbox_sync_run_json_failure_when_not_configured(capsys) -> None:
    from kor_travel_docker_manager.services.offbox_backup_sync import (
        OffboxSyncNotConfiguredError,
    )

    with (
        patch("kor_travel_docker_manager.cli._running_as_root", return_value=True),
        patch(
            "kor_travel_docker_manager.cli.sync_backups_offbox",
            side_effect=OffboxSyncNotConfiguredError("KTDM_OFFBOX_HOST is not set"),
        ),
    ):
        exit_code = main(["offbox-sync", "run", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 1
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    assert "KTDM_OFFBOX_HOST" in payload["detail"]
    assert "KTDM_OFFBOX_HOST" in captured.err


def test_cli_offbox_sync_status_reports_never_run(capsys) -> None:
    with patch(
        "kor_travel_docker_manager.cli.read_offbox_sync_status", return_value=None
    ):
        exit_code = main(["offbox-sync", "status", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "never_run"


def test_cli_offbox_sync_status_reports_the_last_result(capsys) -> None:
    status = {
        "destination_host": "backup-vault.internal",
        "started_at_unix": 1000,
        "all_verified": True,
        "targets": [{"label": "geo", "verified": True}],
    }
    with patch(
        "kor_travel_docker_manager.cli.read_offbox_sync_status", return_value=status
    ):
        exit_code = main(["offbox-sync", "status", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == status


# --- ktdctl pin apply-pending (KUM-M5) ---------------------------------------


@pytest.fixture
def pending_request_env(tmp_path, monkeypatch):
    """UI가 남긴 요청 파일을 격리한다."""

    from kor_travel_docker_manager.services import runtime_pin_request

    target = tmp_path / "requests" / "runtime-pin-requests.json"
    monkeypatch.setenv(runtime_pin_request.RUNTIME_PIN_REQUEST_FILE_ENV, str(target))
    return target


def _init_rotatable_registry():
    """단일 role 회전이 가능한 상태를 만든다.

    동봉 seed의 현재 pinset은 terminal이고, terminal 상태에서는 registry가 단일 role
    회전을 거부한다(pair-incomplete pinset을 M05 ledger가 먼저 소비할 수 있기 때문).
    회전 요청 경로 자체를 시험하려면 먼저 pair 회전으로 신선한 pinset을 만들어야 한다.
    """

    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    main(
        [
            "pin",
            "rotate-pair",
            "--map-revision",
            "1" * 40,
            "--pinvi-revision",
            "2" * 40,
            "--reason",
            "회전 요청 경로 시험용 신선 pinset",
            "--confirm",
        ]
    )


def _file_a_request(*, role="pinvi", revision="d" * 40, reason="새 PinVi head"):
    """현재 registry를 base로 하는 요청을 UI가 남긴 것처럼 기록한다."""

    from kor_travel_docker_manager.services.runtime_pin_registry import (
        load_runtime_pin_registry,
    )
    from kor_travel_docker_manager.services.runtime_pin_request import (
        RuntimePinRequest,
        prospective_pinset_sha256,
        utc_timestamp,
        write_runtime_pin_request,
    )

    registry = load_runtime_pin_registry()
    request = RuntimePinRequest(
        request_id="6f9619ff-8b86-4d01-b42d-00cf4fc964ff",
        role=role,
        revision=revision,
        reason=reason,
        requested_by="admin",
        requested_at=utc_timestamp(),
        base_pinset_sha256=registry.pinset_sha256,
        prospective_pinset_sha256=prospective_pinset_sha256(
            release_version=registry.release_version,
            map_revision=revision if role == "map" else registry.map_revision,
            pinvi_revision=revision if role == "pinvi" else registry.pinvi_revision,
        ),
    )
    write_runtime_pin_request(request)
    return request


def test_pin_pending_parser_registers_every_leaf_command():
    parser = build_parser()

    for action, argv in {
        "apply-pending": ["pin", "apply-pending", "--any-revision"],
        "show-pending": ["pin", "show-pending"],
        "clear-pending": ["pin", "clear-pending", "--request-id", "x"],
    }.items():
        args = parser.parse_args(argv)
        assert args.pin_action == action
        assert callable(args.func)


def test_pin_apply_pending_refuses_without_confirm(
    pin_cli_env, pending_request_env, capsys
):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()
    _file_a_request()
    before = pin_cli_env.read_bytes()

    assert main(["pin", "apply-pending"]) == 2

    assert "--confirm" in capsys.readouterr().err
    assert pin_cli_env.read_bytes() == before
    assert pending_request_env.exists()


def test_pin_apply_pending_refuses_without_root(pin_cli_env, pending_request_env, capsys):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()
    _file_a_request()

    with patch("kor_travel_docker_manager.cli._running_as_root", return_value=False):
        assert main(["pin", "apply-pending", "--any-revision", "--confirm"]) == 2

    assert "root" in capsys.readouterr().err
    assert pending_request_env.exists()


def test_pin_show_pending_is_read_only(pin_cli_env, pending_request_env, capsys):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()
    _file_a_request()
    before = pin_cli_env.read_bytes()

    assert main(["pin", "show-pending"]) == 0

    output = capsys.readouterr().out
    assert "새 PinVi head" in output
    assert "apply-pending" in output
    assert pin_cli_env.read_bytes() == before


def test_pin_show_pending_reports_nothing_pending(pin_cli_env, pending_request_env, capsys):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()

    assert main(["pin", "show-pending"]) == 1
    assert "없습니다" in capsys.readouterr().out


def test_pin_apply_pending_json_reports_absent_when_nothing_pending(
    pin_cli_env, pending_request_env, capsys
):
    """GM-06 잔여 지적 회귀: apply-pending도 --json이면 stdout이 비지 않는다.

    request가 없는 상태는 실패가 아니라 상태 보고이므로 show-pending과 같은
    "absent" 어휘를 쓴다.
    """

    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()

    with patch("kor_travel_docker_manager.cli._running_as_root", return_value=True):
        exit_code = main(["pin", "apply-pending", "--any-revision", "--confirm", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 1
    payload = json.loads(captured.out)
    assert payload == {"status": "absent"}


def test_pin_apply_pending_json_failure_when_expect_revision_mismatches(
    pin_cli_env, pending_request_env, capsys
):
    _init_rotatable_registry()
    capsys.readouterr()
    _file_a_request()

    with patch("kor_travel_docker_manager.cli._running_as_root", return_value=True):
        exit_code = main(
            [
                "pin",
                "apply-pending",
                "--expect-revision",
                "f" * 40,
                "--confirm",
                "--json",
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 2
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    assert "expect-revision" in payload["detail"]
    assert "expect-revision" in captured.err


def test_pin_apply_pending_json_failure_when_registry_is_missing(
    pin_cli_env, pending_request_env, capsys
):
    """등록된 registry 없이 요청 파일만 있으면 read_runtime_pin_request 다음 단계인
    load_runtime_pin_registry가 DeploymentContractError를 낸다 — 그 경로도 --json이면
    stdout에 JSON을 내야 한다."""

    from kor_travel_docker_manager.services.runtime_pin_request import (
        RuntimePinRequest,
        utc_timestamp,
        write_runtime_pin_request,
    )

    write_runtime_pin_request(
        RuntimePinRequest(
            request_id="6f9619ff-8b86-4d01-b42d-00cf4fc964ff",
            role="pinvi",
            revision="d" * 40,
            reason="registry 없는 상태에서의 회귀 테스트",
            requested_by="admin",
            requested_at=utc_timestamp(),
            base_pinset_sha256="a" * 64,
            prospective_pinset_sha256="b" * 64,
        )
    )

    with patch("kor_travel_docker_manager.cli._running_as_root", return_value=True):
        exit_code = main(["pin", "apply-pending", "--any-revision", "--confirm", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 2
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    assert "missing" in payload["detail"]
    assert "missing" in captured.err


def test_pin_apply_pending_rotates_and_records_both_actors(
    pin_cli_env, pending_request_env, capsys
):
    _init_rotatable_registry()
    capsys.readouterr()
    request = _file_a_request()

    with patch("kor_travel_docker_manager.cli._running_as_root", return_value=True):
        exit_code = main(
            ["pin", "apply-pending", "--expect-revision", "d" * 40, "--confirm"]
        )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "applied pending rotation for pinvi" in output
    # 요청자와 적용자가 모두 남아야 사후에 누가 무엇을 했는지 알 수 있다.
    assert "<-admin" in output
    assert request.request_id in output
    # 적용된 요청은 남겨 두지 않는다 — 두 번 적용될 여지를 없앤다.
    assert not pending_request_env.exists()


def test_pin_apply_pending_refuses_a_request_the_pin_moved_past(
    pin_cli_env, pending_request_env, capsys
):
    _init_rotatable_registry()
    capsys.readouterr()
    _file_a_request(role="pinvi", revision="d" * 40)
    # 요청을 남긴 뒤 운영자가 SSH에서 직접 회전시킨 상황.
    main(
        [
            "pin",
            "rotate",
            "--role",
            "map",
            "--revision",
            "e" * 40,
            "--reason",
            "직접 회전",
            "--confirm",
        ]
    )
    capsys.readouterr()

    with patch("kor_travel_docker_manager.cli._running_as_root", return_value=True):
        exit_code = main(["pin", "apply-pending", "--any-revision", "--confirm"])

    assert exit_code == 2
    error = capsys.readouterr().err
    assert "pin이 바뀌었습니다" in error
    # 자동으로 지우지 않는다 — 무엇이 버려지는지 사람이 보고 결정해야 한다.
    assert pending_request_env.exists()


def test_pin_apply_pending_honours_expect_revision(
    pin_cli_env, pending_request_env, capsys
):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()
    _file_a_request(role="pinvi", revision="d" * 40)

    with patch("kor_travel_docker_manager.cli._running_as_root", return_value=True):
        exit_code = main(
            ["pin", "apply-pending", "--expect-revision", "f" * 40, "--confirm"]
        )

    assert exit_code == 2
    assert "--expect-revision" in capsys.readouterr().err
    assert pending_request_env.exists()


def test_pin_clear_pending_requires_confirm_and_the_exact_id(
    pin_cli_env, pending_request_env, capsys
):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()
    request = _file_a_request()

    assert main(["pin", "clear-pending", "--request-id", request.request_id]) == 2
    assert "--confirm" in capsys.readouterr().err
    assert pending_request_env.exists()

    assert (
        main(
            [
                "pin",
                "clear-pending",
                "--request-id",
                "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                "--confirm",
            ]
        )
        == 1
    )
    assert pending_request_env.exists()

    assert (
        main(["pin", "clear-pending", "--request-id", request.request_id, "--confirm"])
        == 0
    )
    assert not pending_request_env.exists()


def test_pin_apply_pending_requires_the_operator_to_name_the_revision(
    pin_cli_env, pending_request_env, capsys
):
    """무엇을 고정하는지 적지 않으면 '파일에 있던 것'이 그대로 적용된다."""

    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()
    _file_a_request()
    before = pin_cli_env.read_bytes()

    with patch("kor_travel_docker_manager.cli._running_as_root", return_value=True):
        assert main(["pin", "apply-pending", "--confirm"]) == 2

    assert "--expect-revision" in capsys.readouterr().err
    assert pin_cli_env.read_bytes() == before
    assert pending_request_env.exists()


def test_pin_apply_pending_reports_a_distinct_code_when_cleanup_fails(
    pin_cli_env, pending_request_env, capsys
):
    """'적용됨'과 '할 일 없음'이 같은 코드면 스크립트가 pinset 소모를 놓친다."""

    _init_rotatable_registry()
    capsys.readouterr()
    _file_a_request()

    with (
        patch("kor_travel_docker_manager.cli._running_as_root", return_value=True),
        patch(
            "kor_travel_docker_manager.cli.clear_runtime_pin_request",
            side_effect=OSError("read-only file system"),
        ),
    ):
        exit_code = main(["pin", "apply-pending", "--any-revision", "--confirm"])

    assert exit_code == 3
    captured = capsys.readouterr()
    assert "회전은 적용됐으나" in captured.err
    assert str(pending_request_env) in captured.err
    # 적용된 registry 상태는 그래도 보여 준다 — 무엇이 됐는지 봐야 수습할 수 있다.
    assert "pinset" in captured.out


def _make_v6_host(tmp_path, monkeypatch):
    """격리 v5 registry 위에 v6 execution registry와 intent 경로를 얹는다."""

    from kor_travel_docker_manager.services import runtime_execution_registry as executions_module
    from kor_travel_docker_manager.services import runtime_pair_rotation as pair_rotation
    from kor_travel_docker_manager.services.runtime_execution_registry import (
        migrate_execution_registry,
        write_runtime_execution_registry,
    )
    from kor_travel_docker_manager.services.runtime_pin_registry import (
        load_runtime_pin_registry,
    )

    monkeypatch.setenv(
        executions_module.RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV, "1"
    )
    monkeypatch.setenv(
        pair_rotation.RUNTIME_PAIR_ROTATION_ALLOW_INSECURE_MODE_ENV, "1"
    )
    monkeypatch.setenv(
        executions_module.RUNTIME_EXECUTIONS_FILE_ENV, str(tmp_path / "executions.json")
    )
    monkeypatch.setenv(
        executions_module.RUNTIME_EXECUTIONS_PUBLIC_FILE_ENV,
        str(tmp_path / "public" / "executions.json"),
    )
    monkeypatch.setenv(
        pair_rotation.RUNTIME_PAIR_ROTATION_FILE_ENV, str(tmp_path / "rotation.json")
    )
    manager = "9" * 40
    executions = migrate_execution_registry(
        pins=load_runtime_pin_registry(),
        manager_source_revision=manager,
        bound_by="tester",
        reason="migrate",
    )
    write_runtime_execution_registry(executions)
    return manager


def test_pin_apply_pending_updates_the_execution_registry_on_a_v6_host(
    pin_cli_env, pending_request_env, tmp_path, monkeypatch, capsys
):
    """GM-01 회귀: UI 요청 승인 한 번이 v6 binding을 stale로 만들면 안 된다."""

    from kor_travel_docker_manager.services.runtime_execution_registry import (
        load_runtime_execution_registry,
    )
    from kor_travel_docker_manager.services.runtime_pin_registry import (
        load_runtime_pin_registry,
    )

    _init_rotatable_registry()
    manager = _make_v6_host(tmp_path, monkeypatch)
    capsys.readouterr()
    _file_a_request()

    with (
        patch("kor_travel_docker_manager.cli._running_as_root", return_value=True),
        patch(
            "kor_travel_docker_manager.cli.trusted_manager_source_revision",
            return_value=manager,
        ),
    ):
        exit_code = main(["pin", "apply-pending", "--any-revision", "--confirm"])

    assert exit_code == 0
    rotated = load_runtime_pin_registry()
    assert rotated.pinvi_revision == "d" * 40
    assert load_runtime_execution_registry().current_matches(
        pins=rotated, manager_source_revision=manager
    )
    assert not pending_request_env.exists()


def test_pin_apply_pending_resumes_a_partial_v5_v6_write(
    pin_cli_env, pending_request_env, tmp_path, monkeypatch, capsys
):
    """v5/v6 사이 crash 뒤 같은 apply-pending 재실행이 끝까지 publish하고 요청을 지운다."""

    from kor_travel_docker_manager.services import runtime_pair_rotation as pair_rotation
    from kor_travel_docker_manager.services.runtime_execution_registry import (
        load_runtime_execution_registry,
    )
    from kor_travel_docker_manager.services.runtime_pin_registry import (
        load_runtime_pin_registry,
    )

    _init_rotatable_registry()
    manager = _make_v6_host(tmp_path, monkeypatch)
    capsys.readouterr()
    _file_a_request()

    with (
        patch("kor_travel_docker_manager.cli._running_as_root", return_value=True),
        patch(
            "kor_travel_docker_manager.cli.trusted_manager_source_revision",
            return_value=manager,
        ),
        patch.object(
            pair_rotation,
            "write_runtime_execution_registry",
            side_effect=OSError("simulated v6 write failure"),
        ),
    ):
        first = main(["pin", "apply-pending", "--any-revision", "--confirm"])

    assert first == 2
    # 요청은 남아 있고 intent도 남아 있다 — 이 상태에서 재실행이 복구여야 한다.
    assert pending_request_env.exists()
    assert pair_rotation.load_pending_runtime_pair_rotation() is not None
    capsys.readouterr()

    with (
        patch("kor_travel_docker_manager.cli._running_as_root", return_value=True),
        patch(
            "kor_travel_docker_manager.cli.trusted_manager_source_revision",
            return_value=manager,
        ),
    ):
        second = main(["pin", "apply-pending", "--any-revision", "--confirm"])

    assert second == 0
    assert pair_rotation.load_pending_runtime_pair_rotation() is None
    rotated = load_runtime_pin_registry()
    assert load_runtime_execution_registry().current_matches(
        pins=rotated, manager_source_revision=manager
    )
    assert not pending_request_env.exists()


def test_pin_apply_pending_refuses_a_request_unrelated_to_a_pending_intent(
    pin_cli_env, pending_request_env, tmp_path, monkeypatch, capsys
):
    """재개 경로는 intent가 이 요청의 것일 때만 가드를 건너뛴다.

    운영자의 pair 회전이 v5 write 뒤 crash한 상태에서, 남아 있던 무관한 단일 role
    요청을 apply-pending이 "적용됨"으로 소비하고 pair 결과를 그 요청에 귀속시키면
    안 된다. intent의 pinset이 요청의 prospective와 다르면 거부해야 한다.
    """

    from kor_travel_docker_manager.services import runtime_pair_rotation as pair_rotation
    from kor_travel_docker_manager.services.runtime_execution_registry import (
        migrate_execution_registry,
        rotate_execution_source_binding,
    )
    from kor_travel_docker_manager.services.runtime_pin_registry import (
        build_registry,
        load_runtime_pin_registry,
    )

    _init_rotatable_registry()
    manager = _make_v6_host(tmp_path, monkeypatch)
    capsys.readouterr()
    # 단일 role 요청: pinvi → d*40
    _file_a_request()

    # 요청과 무관한 pair(map e*40, pinvi f*40)를 향하는 pending intent를 손으로 남긴다.
    current = load_runtime_pin_registry()
    other_pins = build_registry(
        release_version=current.release_version,
        map_revision="e" * 40,
        pinvi_revision="f" * 40,
        rotated_by="tester",
        reason="무관한 pair",
    )
    executions = migrate_execution_registry(
        pins=current, manager_source_revision=manager, bound_by="tester", reason="seed"
    )
    other_executions = rotate_execution_source_binding(
        registry=executions,
        pins=other_pins,
        manager_source_revision=manager,
        bound_by="tester",
        reason="무관한 pair",
    )
    intent = pair_rotation.RuntimePairRotation(
        created_at="2026-09-01T00:00:00Z",
        pin_registry=other_pins,
        execution_registry=other_executions,
    )
    pair_rotation._atomic_write(pair_rotation.runtime_pair_rotation_path(), intent.to_payload())

    before = load_runtime_pin_registry().pinset_sha256
    with (
        patch("kor_travel_docker_manager.cli._running_as_root", return_value=True),
        patch(
            "kor_travel_docker_manager.cli.trusted_manager_source_revision",
            return_value=manager,
        ),
    ):
        exit_code = main(["pin", "apply-pending", "--any-revision", "--confirm"])

    assert exit_code == 2
    assert "다른 pinset을 향합니다" in capsys.readouterr().err
    # 요청도 registry도 건드리지 않았다 — 무관한 요청을 소비하지 않는다.
    assert pending_request_env.exists()
    assert load_runtime_pin_registry().pinset_sha256 == before


def test_pin_clear_pending_force_removes_an_unreadable_request(
    pin_cli_env, pending_request_env, capsys
):
    """읽을 수 없는 파일은 id를 알 수 없어 id 대조 삭제로는 영원히 남는다."""

    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()
    pending_request_env.parent.mkdir(parents=True, exist_ok=True)
    pending_request_env.write_text("{not json", encoding="utf-8")
    pending_request_env.chmod(0o600)

    assert main(["pin", "show-pending"]) == 2
    assert "clear-pending --force" in capsys.readouterr().err

    assert main(["pin", "clear-pending", "--force", "--confirm"]) == 0
    assert not pending_request_env.exists()


def test_pin_clear_pending_force_refuses_a_readable_request(
    pin_cli_env, pending_request_env, capsys
):
    """--force는 잔재 제거용이다. 멀쩡한 요청까지 id 없이 지우면 안 된다."""

    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()
    _file_a_request()

    assert main(["pin", "clear-pending", "--force", "--confirm"]) == 2
    assert "cancel it by id" in capsys.readouterr().err
    assert pending_request_env.exists()


def test_pin_show_pending_json_is_parseable_on_every_path(
    pin_cli_env, pending_request_env, capsys
):
    """--json이 사람 문장을 stdout에 섞으면 스크립트가 파싱할 수 없다."""

    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()

    assert main(["pin", "show-pending", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "absent"}

    _file_a_request()
    assert main(["pin", "show-pending", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pending"
    assert payload["role"] == "pinvi"


def test_applied_reason_keeps_the_request_provenance_when_the_reason_is_long(
    pin_cli_env, pending_request_env, capsys
):
    """긴 사유를 그냥 이어 붙이면 요청 id·요청자·시각이 통째로 잘려 나간다."""

    from kor_travel_docker_manager.cli import _applied_actor, _applied_reason
    from kor_travel_docker_manager.services.runtime_pin_request import MAX_REASON_LENGTH

    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()
    request = _file_a_request(reason="가" * MAX_REASON_LENGTH)

    reason = _applied_reason(request)
    assert len(reason) <= MAX_REASON_LENGTH
    assert request.request_id in reason
    assert request.requested_by in reason

    actor = _applied_actor(request)
    assert len(actor) <= 200
    assert actor.endswith(request.requested_by)


def test_pin_rotate_rejects_a_malformed_revision(pin_cli_env, capsys):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()

    exit_code = main(
        [
            "pin",
            "rotate-pair",
            "--map-revision",
            "not-a-sha",
            "--pinvi-revision",
            "d" * 40,
            "--reason",
            "bad",
            "--confirm",
        ]
    )

    assert exit_code == 2
    assert "40-hex" in capsys.readouterr().err


def test_terminal_seed_refuses_a_single_role_rotation(pin_cli_env, capsys):
    main(["pin", "init", "--seed", str(_seed_path()), "--confirm"])
    capsys.readouterr()

    assert (
        main(
            [
                "pin",
                "rotate",
                "--role",
                "map",
                "--revision",
                "c" * 40,
                "--reason",
                "would split M05 pair",
                "--confirm",
            ]
        )
        == 2
    )
    assert "atomic Map/PinVi pair" in capsys.readouterr().err
