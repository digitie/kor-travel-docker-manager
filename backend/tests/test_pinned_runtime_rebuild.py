from __future__ import annotations

import hashlib
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
    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
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

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
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
