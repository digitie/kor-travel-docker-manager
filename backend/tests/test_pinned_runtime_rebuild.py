from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.compose_service import ComposeService
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    REBUILD_PHASES,
    RUNTIME_SERVICES,
    pinned_runtime_state_paths,
    read_rebuild_journal,
    write_rebuild_journal,
)
from kor_travel_docker_manager.services.pinned_runtime_rebuild import (
    CandidateRuntimeBuild,
    build_candidate_generation,
    generation_compose_environment,
    new_candidate_journal,
    parse_candidate_static_head,
)
from kor_travel_docker_manager.services.pinned_runtime_release import PINNED_RUNTIME_RELEASE
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    MaterializedRuntimeSource,
    PinnedRuntimeSourceMaterialization,
)


def _sources() -> PinnedRuntimeSourceMaterialization:
    return PinnedRuntimeSourceMaterialization(
        release=PINNED_RUNTIME_RELEASE,
        sources=(
            MaterializedRuntimeSource(
                role="map",
                root=Path("/state/map"),
                revision=PINNED_RUNTIME_RELEASE.source_for("map").revision,
                tree="a" * 40,
            ),
            MaterializedRuntimeSource(
                role="pinvi",
                root=Path("/state/pinvi"),
                revision=PINNED_RUNTIME_RELEASE.source_for("pinvi").revision,
                tree="b" * 40,
            ),
        ),
    )


def test_candidate_build_uses_private_deterministic_tags_and_staged_sources() -> None:
    build = CandidateRuntimeBuild(_sources())

    environment = build.compose_environment()

    assert environment["KOR_TRAVEL_MAP_REPO_DIR"] == "/state/map"
    assert environment["PINVI_REPO_DIR"] == "/state/pinvi"
    assert environment["PINVI_BUILD_ENVIRONMENT"] == "production"
    assert set(build.image_names) == set(RUNTIME_SERVICES)
    assert all(
        image.endswith(PINNED_RUNTIME_RELEASE.pinset_sha256)
        and image.startswith("kor-travel-docker-manager/pinned-runtime-candidate-v5/")
        for image in build.image_names.values()
    )


def test_static_head_parser_accepts_exact_one_line_schema_contract() -> None:
    assert parse_candidate_static_head(
        '{"pinvi_head":"20260806_0001","schema":"pinvi.candidate-head.v1"}',
        schema="pinvi.candidate-head.v1",
        field="pinvi_head",
    ) == "20260806_0001"

    with pytest.raises(DeploymentContractError, match="output"):
        parse_candidate_static_head(
            '{"head":"x","schema":"pinvi.candidate-head.v1"}\nextra',
            schema="pinvi.candidate-head.v1",
            field="pinvi_head",
        )


def test_candidate_generation_and_journal_bind_all_runtime_inputs() -> None:
    sources = _sources()
    generation = build_candidate_generation(
        sources=sources,
        image_ids={service: f"sha256:{index:064x}" for index, service in enumerate(RUNTIME_SERVICES)},
        map_application_head="0084_pipeline_root",
        map_dagster_head="dagster_storage_1",
        pinvi_head="20260806_0001",
        recorded_at="2026-08-06T00:00:00+00:00",
    )
    resolved = "c" * 64

    journal = new_candidate_journal(
        candidate=generation,
        environment_bytes=b"frozen-env\n",
        compose_source_bytes=b"services: {}\n",
        resolved_compose_sha256=resolved,
        created_at="2026-08-06T00:00:00+00:00",
    )

    assert journal.phase == "candidate_attested"
    assert journal.candidate == generation
    assert journal.environment_sha256 == hashlib.sha256(b"frozen-env\n").hexdigest()
    assert journal.resolved_compose_sha256 == resolved
    assert generation_compose_environment(generation)["PINVI_DAGSTER_IMAGE"] == (
        generation.pinvi_dagster_image_id
    )


def test_rebuild_requires_root_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compose_service_module.os, "geteuid", lambda: 1000)

    with pytest.raises(DeploymentContractError, match="requires root execution"):
        ComposeService().rebuild_pinned_runtime()


def test_rebuild_requires_all_operation_tokens_before_source_or_database_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
    }
    environment = SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n")
    materialize = Mock()

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: __import__("contextlib").nullcontext(object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: environment,
    )
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        materialize,
    )

    with pytest.raises(DeploymentContractError, match="tokens must be configured together"):
        ComposeService().rebuild_pinned_runtime()

    materialize.assert_not_called()


def test_frozen_compose_resolution_includes_bootstrap_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    commands: list[list[str]] = []
    candidate = {
        "services": {
            "pinvi-admin-bootstrap": {
                "image": "pinvi-api:test",
                "profiles": ["bootstrap"],
            }
        }
    }
    resolved = json.dumps(candidate)

    monkeypatch.setattr(
        compose_service_module,
        "_revalidate_compose_external_input_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_materialize_external_inputs_with_memfd",
        lambda candidate, _inputs: (candidate, ()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "revalidate_candidate_system_bind_snapshots",
        lambda _snapshots: None,
    )

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=resolved, stderr="")

    monkeypatch.setattr(compose_service_module.subprocess, "run", run)

    actual = service._resolve_compose_candidate_unlocked(
        candidate,
        environment={},
        expected_system_bind_snapshots=(),
        environment_snapshot=SimpleNamespace(compose_path="/tmp/compose.yml"),
        environment_override=None,
        external_input_snapshot=object(),
    )

    assert actual == candidate
    assert commands == [
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--profile",
            "bootstrap",
            "--project-directory",
            "/tmp",
            "-f",
            "-",
            "config",
            "--format",
            "json",
        ]
    ]


def test_rebuild_compose_error_names_the_failed_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-compose-output-token-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 23,
                "stdout": secret,
                "stderr": (
                    secret
                    + "\n"
                    + '{"code":"dagster_instance_migrate_failed",'
                    + '"schema":"kor-travel-map.dagster-storage-migration-error.v1"}'
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 23; dagster_instance_migrate_failed\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "kor-travel-map-dagster-storage-migrate"],
            transaction=object(),
        )

    assert secret not in str(captured.value)


def test_rebuild_retries_only_the_idempotent_dagster_storage_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    run = Mock(
        side_effect=(
            {"success": False, "returncode": 1, "stdout": "", "stderr": ""},
            {"success": True, "returncode": 0, "stdout": "", "stderr": ""},
        )
    )
    sleep = Mock()
    monkeypatch.setattr(service, "_run_frozen_recovery", run)
    monkeypatch.setattr(compose_service_module.time, "sleep", sleep)

    result = service._run_pinned_runtime_rebuild_compose(
        ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
        transaction=object(),
        retryable=True,
    )

    assert result["success"] is True
    assert run.call_count == 2
    sleep.assert_called_once_with(2)


@pytest.mark.parametrize(
    "args",
    (
        ["up", "pinvi-api"],
        ["run", "--rm", "--no-deps", "pinvi-admin-bootstrap"],
    ),
)
def test_rebuild_rejects_retry_for_any_other_compose_action(args: list[str]) -> None:
    with pytest.raises(DeploymentContractError, match="only the idempotent"):
        ComposeService()._run_pinned_runtime_rebuild_compose(
            args,
            transaction=object(),
            retryable=True,
        )


def test_rebuild_retry_exhaustion_exposes_only_the_last_allowlist_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-retry-output-must-not-leak"
    run = Mock(
        return_value={
            "success": False,
            "returncode": 1,
            "stdout": secret,
            "stderr": (
                secret
                + "\n"
                + '{"code":"dagster_instance_migrate_failed",'
                + '"schema":"kor-travel-map.dagster-storage-migration-error.v1"}'
            ),
        }
    )
    sleep = Mock()
    monkeypatch.setattr(service, "_run_frozen_recovery", run)
    monkeypatch.setattr(compose_service_module.time, "sleep", sleep)

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1; dagster_instance_migrate_failed\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=object(),
            retryable=True,
        )

    assert run.call_count == compose_service_module._PINNED_RUNTIME_DAGSTER_MIGRATION_ATTEMPTS
    assert sleep.call_count == compose_service_module._PINNED_RUNTIME_DAGSTER_MIGRATION_ATTEMPTS - 1
    assert secret not in str(captured.value)


def test_rebuild_compose_error_ignores_malformed_diagnostic_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-malformed-diagnostic-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 23,
                "stdout": secret,
                "stderr": json.dumps(
                    {
                        "code": ["dagster_instance_migrate_failed"],
                        "schema": "kor-travel-map.dagster-storage-migration-error.v1",
                    }
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 23\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "kor-travel-map-dagster-storage-migrate"],
            transaction=object(),
        )

    assert secret not in str(captured.value)


def test_rebuild_compose_error_exposes_only_allowlisted_pinvi_bootstrap_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-pinvi-bootstrap-diagnostic-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": (
                    secret
                    + "\n"
                    + 'pinvi-admin-bootstrap-1  | {"error_code":"credential_file_owner_mismatch",'
                    + '"phase":"credential_file"}'
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1; pinvi:credential_file_owner_mismatch\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            [
                "--profile",
                "bootstrap",
                "run",
                "--rm",
                "--no-deps",
                "-v",
                "/run/manager/credential.json:/run/pinvi/bootstrap-admin.json:ro",
                "-e",
                "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE=/run/pinvi/bootstrap-admin.json",
                "pinvi-admin-bootstrap",
            ],
            transaction=object(),
        )

    assert secret not in str(captured.value)


@pytest.mark.parametrize(
    "stderr",
    (
        '{"error_code":"credential_file_owner_mismatch",'
        '"phase":"credential_file","extra":"ignored"}',
        '{"code":"dagster_instance_migrate_failed",'
        '"schema":"kor-travel-map.dagster-storage-migration-error.v1"}',
        '{"error_code":"credential_file_owner_mismatch",'
        '"error_code":"internal_error","phase":"runtime"}',
    ),
)
def test_rebuild_compose_error_rejects_noncanonical_pinvi_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
) -> None:
    service = ComposeService()
    secret = "test-pinvi-noncanonical-diagnostic-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": stderr,
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "pinvi-admin-bootstrap"],
            transaction=object(),
        )

    assert secret not in str(captured.value)


@pytest.mark.parametrize(
    "stderr",
    (
        'untrusted-log | {"error_code":"credential_file_owner_mismatch",'
        '"phase":"credential_file"}',
        '123 | {"error_code":"credential_file_owner_mismatch",'
        '"phase":"credential_file"}',
        'pinvi-admin-bootstrap-run | {"error_code":"credential_file_owner_mismatch",'
        '"phase":"credential_file"}',
        'kor-travel-map-dagster-storage-migrate-1 | '
        '{"error_code":"credential_file_owner_mismatch","phase":"credential_file"}',
        'pinvi-admin-bootstrap-1 | {"error_code":"credential_file_owner_mismatch",'
        '"error_code":"internal_error","phase":"runtime"}',
    ),
)
def test_rebuild_compose_error_rejects_untrusted_pinvi_compose_prefix(
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
) -> None:
    service = ComposeService()
    secret = "test-pinvi-prefix-spoof-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": stderr,
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "pinvi-admin-bootstrap"],
            transaction=object(),
        )

    assert secret not in str(captured.value)


def test_rebuild_compose_error_accepts_map_compose_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": "",
                "stderr": (
                    'kor-travel-map-dagster-storage-migrate-1 | '
                    '{"code":"dagster_instance_migrate_failed",'
                    '"schema":"kor-travel-map.dagster-storage-migration-error.v1"}'
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1; dagster_instance_migrate_failed\)",
    ):
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=object(),
        )


def test_rebuild_compose_error_rejects_pinvi_payload_for_map_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-map-cross-payload-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": json.dumps(
                    {
                        "error_code": "credential_file_owner_mismatch",
                        "phase": "credential_file",
                    }
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=object(),
        )

    assert secret not in str(captured.value)


def test_rebuild_compose_error_ignores_pinvi_code_with_wrong_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-pinvi-malformed-diagnostic-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": json.dumps(
                    {
                        "error_code": "credential_file_owner_mismatch",
                        "phase": "runtime",
                    }
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "pinvi-admin-bootstrap"],
            transaction=object(),
        )

    assert secret not in str(captured.value)


@pytest.mark.parametrize(
    ("configured_candidate_head", "expected_candidate_head"),
    [
        (None, "candidate_static_attestation"),
        ("previous-candidate-head", "previous-candidate-head"),
    ],
)
def test_rebuild_runs_candidate_then_three_database_reset_and_seven_runtime_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_candidate_head: str | None,
    expected_candidate_head: str,
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        "COMPOSE_PROJECT_NAME": "f1d-c2",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state"),
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "rebuild-admin-password",
    }
    if configured_candidate_head is not None:
        values["KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD"] = configured_candidate_head
    transaction = SimpleNamespace(
        environment=SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n"),
        compose_source_bytes=b"services: {}\n",
        resolved_document_hash="c" * 64,
        resolved={"services": {}},
    )
    captured: list[dict[str, str] | None] = []

    def capture(
        *,
        environment_override: dict[str, str] | None = None,
        environment_snapshot: object | None = None,
    ) -> tuple[SimpleNamespace, None]:
        del environment_snapshot
        captured.append(environment_override)
        return transaction, None

    service = ComposeService()
    operations: list[tuple[str, ...]] = []
    reset_operation_counts: list[int] = []
    static_commands: list[tuple[str, ...]] = []
    image_ids = {
        service_name: f"sha256:{index:064x}"
        for index, service_name in enumerate(RUNTIME_SERVICES)
    }
    revisions = iter(("map-application-head", "map-dagster-head", "pinvi-head"))

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: __import__("contextlib").nullcontext(object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: transaction.environment,
    )
    monkeypatch.setattr(compose_service_module, "_assert_transaction_matches_c6c_lock", Mock())
    monkeypatch.setattr(service, "_capture_transaction_unlocked", capture)
    candidate_contract = Mock()
    monkeypatch.setattr(
        service,
        "_validate_pinned_runtime_candidate_build_contract",
        candidate_contract,
    )
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        lambda **_kwargs: _sources(),
    )
    def run_compose(
        args: list[str],
        *,
        transaction: object,
        retryable: bool = False,
    ) -> dict[str, object]:
        del transaction
        del retryable
        operations.append(tuple(args))
        return {"success": True, "stdout": "[]" if "ps" in args else ""}

    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)
    monkeypatch.setattr(
        service,
        "_attest_pinned_runtime_candidate_images",
        lambda *, build: image_ids,
    )
    def static_command(_image: str, command: tuple[str, ...], *, label: str) -> str:
        del label
        static_commands.append(command)
        return {
            "ktm-application-schema": '{"head":"map-application-head","schema":"kor-travel-map.application-head.v1"}\n',
            "ktm-dagster-storage": '{"head":"map-dagster-head","schema":"kor-travel-map.dagster-storage-head.v1"}\n',
            "pinvi-admin-bootstrap": '{"pinvi_head":"pinvi-head","schema":"pinvi.candidate-head.v1"}\n',
        }[command[0]]

    monkeypatch.setattr(
        compose_service_module,
        "_run_pinned_runtime_static_command",
        static_command,
    )
    monkeypatch.setattr(
        compose_service_module,
        "database_runtimes_from_frozen_contract",
        lambda **_kwargs: (object(), object(), object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "recreate_empty_databases",
        lambda _runtimes: reset_operation_counts.append(len(operations)),
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_database_schema_revision",
        lambda _runtime: next(revisions),
    )
    monkeypatch.setattr(service, "_require_services_ready", Mock(return_value=[]))
    monkeypatch.setattr(compose_service_module, "ensure_generation_references", Mock())
    monkeypatch.setattr(compose_service_module, "reconcile_generation_references", Mock())
    monkeypatch.setattr(compose_service_module, "retire_f1d_legacy_artifacts", Mock())

    result = service.rebuild_pinned_runtime()

    assert result["success"] is True
    assert result["phase"] == "committed"
    assert captured[0] is not None
    assert captured[0]["KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD"] == expected_candidate_head
    assert captured[-1] is not None
    assert captured[-1]["KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD"] == "map-application-head"
    assert operations[0] == ("build", *RUNTIME_SERVICES)
    candidate_contract.assert_called_once_with(transaction, build=CandidateRuntimeBuild(_sources()))
    assert operations[1] == ("stop", *RUNTIME_SERVICES)
    assert operations[2] == (
        "--profile",
        "bootstrap",
        "rm",
        "-f",
        "-s",
        "kor-travel-map-dagster-storage-migrate",
        "pinvi-admin-bootstrap",
    )
    assert operations[3] == (
        "--profile",
        "bootstrap",
        "ps",
        "--all",
        "--format",
        "json",
        "kor-travel-map-dagster-storage-migrate",
        "pinvi-admin-bootstrap",
    )
    assert reset_operation_counts == [4]
    assert static_commands == [
        ("ktm-application-schema", "head"),
        ("ktm-dagster-storage", "head"),
        ("pinvi-admin-bootstrap", "head"),
    ]
    assert ("run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate") in operations
    bootstrap = next(command for command in operations if "pinvi-admin-bootstrap" in command)
    assert "--profile" in bootstrap
    assert all("rebuild-admin-password" not in part for part in bootstrap)
    assert all("pinvi_bootstrap" not in part for part in bootstrap)
    assert not (
        tmp_path / "state" / "f1d-c2" / "bootstrap" / str(result["transaction_id"])
    ).exists()


def test_oneshot_writer_liveness_must_be_empty_before_database_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    operations: list[tuple[str, ...]] = []
    transaction = cast(Any, SimpleNamespace())

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operations.append(tuple(args))
        if "ps" not in args:
            return {"success": True, "stdout": ""}
        return {
            "success": True,
            "stdout": (
                '[{"Name":"f1d-pinvi-bootstrap",'
                '"Service":"pinvi-admin-bootstrap","State":"running"}]'
            ),
        }

    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    with pytest.raises(DeploymentContractError, match="one-shot writer remained"):
        service._retire_pinned_runtime_oneshot_writers(transaction=transaction)

    assert [command[2] for command in operations] == ["rm", "ps"]


def test_retention_failure_cannot_create_a_terminal_rebuild_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        "COMPOSE_PROJECT_NAME": "f1d-retention-retry",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state"),
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "rebuild-admin-password",
    }
    transaction = SimpleNamespace(
        environment=SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n"),
        compose_source_bytes=b"services: {}\n",
        resolved_document_hash="c" * 64,
        resolved={"services": {}},
    )
    image_ids = {
        service_name: f"sha256:{index:064x}"
        for index, service_name in enumerate(RUNTIME_SERVICES)
    }
    candidate = build_candidate_generation(
        sources=_sources(),
        image_ids=image_ids,
        map_application_head="map-application-head",
        map_dagster_head="map-dagster-head",
        pinvi_head="pinvi-head",
    )
    journal = new_candidate_journal(
        candidate=candidate,
        environment_bytes=b"frozen-env\n",
        compose_source_bytes=b"services: {}\n",
        resolved_compose_sha256="c" * 64,
    )
    for phase in REBUILD_PHASES[1:-1]:
        journal = journal.transition(phase)
    state_paths = pinned_runtime_state_paths(values)
    state_paths.state_root.mkdir(parents=True, mode=0o700)
    write_rebuild_journal(state_paths.journal, journal)

    service = ComposeService()
    operations: list[tuple[str, ...]] = []
    revision_heads = iter(
        (
            "map-application-head",
            "map-dagster-head",
            "pinvi-head",
            "map-application-head",
            "map-dagster-head",
            "pinvi-head",
        )
    )
    reconcile_attempts = 0

    def capture(**_kwargs: object) -> tuple[SimpleNamespace, None]:
        return transaction, None

    def run_compose(
        args: list[str],
        *,
        transaction: object,
        retryable: bool = False,
    ) -> dict[str, object]:
        del transaction
        del retryable
        operations.append(tuple(args))
        return {"success": True, "stdout": "[]" if "ps" in args else ""}

    def reconcile(*_args: object, **_kwargs: object) -> None:
        nonlocal reconcile_attempts
        reconcile_attempts += 1
        if reconcile_attempts == 1:
            raise DeploymentContractError("retention failed")

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: __import__("contextlib").nullcontext(object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: transaction.environment,
    )
    monkeypatch.setattr(compose_service_module, "_assert_transaction_matches_c6c_lock", Mock())
    monkeypatch.setattr(service, "_capture_transaction_unlocked", capture)
    monkeypatch.setattr(
        service,
        "_validate_pinned_runtime_candidate_build_contract",
        Mock(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        lambda **_kwargs: _sources(),
    )
    monkeypatch.setattr(service, "_attest_pinned_runtime_candidate_images", Mock())
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)
    monkeypatch.setattr(
        compose_service_module,
        "database_runtimes_from_frozen_contract",
        lambda **_kwargs: (object(), object(), object()),
    )
    monkeypatch.setattr(compose_service_module, "recreate_empty_databases", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "read_database_schema_revision",
        lambda _runtime: next(revision_heads),
    )
    monkeypatch.setattr(service, "_require_services_ready", Mock(return_value=[]))
    monkeypatch.setattr(compose_service_module, "ensure_generation_references", Mock())
    monkeypatch.setattr(compose_service_module, "reconcile_generation_references", reconcile)

    with pytest.raises(DeploymentContractError, match="retention failed"):
        service.rebuild_pinned_runtime()

    assert read_rebuild_journal(state_paths.journal).phase == "manifest_committing"

    result = service.rebuild_pinned_runtime()

    assert result["phase"] == "committed"
    assert reconcile_attempts == 2
    assert operations.count(("stop", *RUNTIME_SERVICES)) == 3
