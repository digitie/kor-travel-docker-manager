from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.compose_service import ComposeService
from kor_travel_docker_manager.services.pinned_runtime_generation import RUNTIME_SERVICES
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


def test_rebuild_runs_candidate_then_three_database_reset_and_seven_runtime_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(compose_service_module, "_assert_transaction_matches_c6c_lock", Mock())
    monkeypatch.setattr(service, "_capture_transaction_unlocked", capture)
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
    assert captured[-1] is not None
    assert captured[-1]["KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD"] == "map-application-head"
    assert operations[0] == ("build", *RUNTIME_SERVICES)
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


def test_oneshot_writer_liveness_must_be_empty_before_database_reset() -> None:
    service = ComposeService()
    operations: list[tuple[str, ...]] = []
    transaction = SimpleNamespace()

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

    service._run_pinned_runtime_rebuild_compose = run_compose  # type: ignore[method-assign]

    with pytest.raises(DeploymentContractError, match="one-shot writer remained"):
        service._retire_pinned_runtime_oneshot_writers(transaction=transaction)

    assert [command[2] for command in operations] == ["rm", "ps"]
