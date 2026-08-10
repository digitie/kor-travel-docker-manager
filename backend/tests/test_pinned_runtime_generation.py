from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

import pytest
from kor_travel_docker_manager.services.c6c_deployment import (
    _PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
    DeploymentContractError,
    assert_compose_mutation_allowed,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeCancelProbeOutcome,
    PinnedRuntimeCancelProbeReceipt,
    PinnedRuntimeGeneration,
    PinnedRuntimeManifest,
    PinnedRuntimeRebuildJournal,
    ensure_pinned_runtime_state_directory,
    f1d_legacy_artifact_paths,
    generation_logical_sha256,
    legacy_tombstone_receipt_path,
    load_deployment_mode,
    pinned_runtime_state_paths,
    read_manifest,
    read_rebuild_journal,
    require_rebuildable_mode,
    retire_f1d_legacy_artifacts,
    write_manifest,
    write_rebuild_journal,
)

_PINSET_SHA256 = "a" * 64


def _generation(seed: str = "a") -> PinnedRuntimeGeneration:
    return PinnedRuntimeGeneration(
        map_api_image_id=f"sha256:{seed * 64}",
        map_ui_image_id=f"sha256:{seed * 64}",
        map_dagster_image_id=f"sha256:{seed * 64}",
        map_dagster_daemon_image_id=f"sha256:{seed * 64}",
        pinvi_api_image_id=f"sha256:{seed * 64}",
        pinvi_web_image_id=f"sha256:{seed * 64}",
        pinvi_dagster_image_id=f"sha256:{seed * 64}",
        map_source_revision=seed * 40,
        pinvi_source_revision=seed * 40,
        map_application_head="0084_c6c_cancel_probe_fixtures",
        map_dagster_head="dagster-1",
        pinvi_head="20260801_0050",
        pinset_sha256=seed * 64,
        recorded_at="2026-08-06T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("environment", "lifecycle", "pinvi_environment", "required"),
    [
        ("local", "development", "development", "false"),
        ("rehearsal", "rebuildable", "production", "true"),
        ("production", "operational", "production", "true"),
    ],
)
def test_load_deployment_mode_accepts_only_typed_pairs(
    environment: str,
    lifecycle: str,
    pinvi_environment: str,
    required: str,
) -> None:
    mode = load_deployment_mode(
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": environment,
            "KTDM_DEPLOYMENT_LIFECYCLE": lifecycle,
            "PINVI_ENVIRONMENT": pinvi_environment,
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": required,
        }
    )

    assert mode.environment == environment
    assert mode.lifecycle == lifecycle
    assert mode.rebuildable is (lifecycle == "rebuildable")


def test_rebuildable_rejects_production_environment_even_with_lifecycle_flag() -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
    }

    with pytest.raises(DeploymentContractError, match="environment/lifecycle"):
        require_rebuildable_mode(values)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS", '[{"id":"configured"}]'),
        ("PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED", "true"),
        ("PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_COMMAND_TOKEN", "configured"),
        ("PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_ID", "other-consumer"),
    ],
)
def test_rebuildable_rejects_configured_cache_target_runtime(
    name: str, value: str
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        name: value,
    }

    with pytest.raises(DeploymentContractError, match="inert cache-target"):
        require_rebuildable_mode(values)


def test_rebuild_capability_allows_compose_mutation_only_in_rebuildable_mode() -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
    }

    assert_compose_mutation_allowed(
        ("kor-travel-map-api", "pinvi-api"),
        environment=values,
        capability=_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
    )

    with pytest.raises(DeploymentContractError, match="rehearsal/rebuildable"):
        assert_compose_mutation_allowed(
            ("kor-travel-map-api",),
            environment={**values, "KTDM_DEPLOYMENT_LIFECYCLE": "operational"},
            capability=_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
        )


def test_pinned_runtime_state_paths_are_rebuildable_project_scoped(
    tmp_path: Path,
) -> None:
    paths = pinned_runtime_state_paths(
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
            "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
            "PINVI_ENVIRONMENT": "production",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
            "COMPOSE_PROJECT_NAME": "f1d-isolated",
            "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path),
        },
        pinset_sha256=_PINSET_SHA256,
    )

    ensure_pinned_runtime_state_directory(paths.state_root)

    assert paths.state_root == tmp_path / "f1d-isolated"
    assert paths.manifest == paths.state_root / "pinned-runtime-generation-v5.json"
    assert paths.journal == (
        paths.state_root / f"pinned-runtime-rebuild-v7-{_PINSET_SHA256}.json"
    )
    assert paths.tombstone_receipt == legacy_tombstone_receipt_path(
        paths.state_root,
        pinset_sha256=_PINSET_SHA256,
    )
    assert paths.tombstone_receipt == (
        paths.state_root / f"legacy-tombstone-v7-{_PINSET_SHA256}.json"
    )
    assert stat.S_IMODE(paths.state_root.stat().st_mode) == 0o700


def test_pinned_runtime_state_paths_reject_nonrebuildable_or_invalid_project(
    tmp_path: Path,
) -> None:
    common = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path),
    }

    with pytest.raises(DeploymentContractError, match="COMPOSE_PROJECT_NAME"):
        pinned_runtime_state_paths(common, pinset_sha256=_PINSET_SHA256)
    with pytest.raises(DeploymentContractError, match="environment/lifecycle"):
        pinned_runtime_state_paths(
            {
                **common,
                "COMPOSE_PROJECT_NAME": "f1d-isolated",
                "KTDM_DEPLOYMENT_LIFECYCLE": "operational",
            },
            pinset_sha256=_PINSET_SHA256,
        )


def test_pinned_runtime_journal_and_tombstone_paths_are_pinset_scoped(
    tmp_path: Path,
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "COMPOSE_PROJECT_NAME": "f1d-isolated",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path),
    }
    previous = pinned_runtime_state_paths(values, pinset_sha256="a" * 64)
    next_release = pinned_runtime_state_paths(values, pinset_sha256="b" * 64)

    assert previous.state_root == next_release.state_root
    assert previous.manifest == next_release.manifest
    assert previous.journal != next_release.journal
    assert previous.tombstone_receipt != next_release.tombstone_receipt


def test_manifest_is_single_active_generation_without_rollback(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    path = state / "pinned-runtime-generation-v5.json"
    manifest = PinnedRuntimeManifest(version=5, active_generation=_generation())

    write_manifest(path, manifest)

    assert read_manifest(path) == manifest
    assert '"rollback"' not in path.read_text(encoding="utf-8")


def test_manifest_rejects_unsafe_file_mode(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    path = state / "pinned-runtime-generation-v5.json"
    write_manifest(path, PinnedRuntimeManifest(version=5, active_generation=_generation()))
    os.chmod(path, 0o644)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_manifest(path)


def test_rebuild_journal_requires_candidate_first_and_exact_phase_order(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    journal = PinnedRuntimeRebuildJournal(
        version=7,
        transaction_id=str(uuid.uuid4()),
        phase="candidate_attested",
        candidate=_generation(),
        environment_sha256="b" * 64,
        compose_sha256="c" * 64,
        resolved_compose_sha256="d" * 64,
        created_at="2026-08-06T00:00:00+00:00",
    )
    path = state / "pinned-runtime-rebuild-v7.json"

    write_rebuild_journal(path, journal)
    restored = read_rebuild_journal(path)

    assert restored == journal
    assert restored.transition("reset_intent_durable").phase == "reset_intent_durable"
    with pytest.raises(DeploymentContractError, match="phase transition"):
        restored.transition("databases_recreated")


def test_rebuild_journal_requires_durable_cancel_post_and_finalize_receipts() -> None:
    receipt = PinnedRuntimeCancelProbeReceipt()
    armed = receipt.transition(
        "armed",
        job_id=str(uuid.uuid4()),
        fixture_created_at="2026-08-06T00:00:00+00:00",
    )
    attempted = armed.transition("cancel_post_attempted")
    consumed = attempted.transition(
        "consumed",
        cancellation_id=str(uuid.uuid4()),
        outcome=PinnedRuntimeCancelProbeOutcome(
            name="pinvi_cancel_error",
            status=409,
            code="PIPELINE_CANCELLATION_UNSAFE",
        ),
        fixture_consumed_at="2026-08-06T00:01:00+00:00",
    )
    finalize_attempted = consumed.transition("finalize_post_attempted")
    finalized = finalize_attempted.transition(
        "finalized",
        fixture_finalized_at="2026-08-06T00:02:00+00:00",
    )

    assert finalized.stage == "finalized"
    with pytest.raises(DeploymentContractError, match="transition"):
        armed.transition("consumed")


@pytest.mark.parametrize(
    ("consumed_at", "finalized_at"),
    [
        ("2026-08-06T00:00:00+00:00", "2026-08-06T00:02:00+00:00"),
        ("2026-08-06T00:02:00+00:00", "2026-08-06T00:01:00+00:00"),
    ],
)
def test_pinned_runtime_receipt_rejects_reversed_fixture_timestamps(
    consumed_at: str,
    finalized_at: str,
) -> None:
    with pytest.raises(DeploymentContractError, match="timestamp order"):
        PinnedRuntimeCancelProbeReceipt(
            stage="finalized",
            job_id=str(uuid.uuid4()),
            cancellation_id=str(uuid.uuid4()),
            outcome=PinnedRuntimeCancelProbeOutcome(
                name="pinvi_cancel_error",
                status=409,
                code="PIPELINE_CANCELLATION_UNSAFE",
            ),
            fixture_created_at="2026-08-06T00:01:00+00:00",
            fixture_consumed_at=consumed_at,
            fixture_finalized_at=finalized_at,
        )


def test_rebuild_journal_rejects_fixture_timestamp_drift() -> None:
    journal = PinnedRuntimeRebuildJournal(
        version=7,
        transaction_id=str(uuid.uuid4()),
        phase="candidate_attested",
        candidate=_generation(),
        environment_sha256="b" * 64,
        compose_sha256="c" * 64,
        resolved_compose_sha256="d" * 64,
        created_at="2026-08-06T00:00:00+00:00",
    ).transition("reset_intent_durable")
    journal = journal.transition("databases_recreated")
    journal = journal.transition("map_application_ready")
    journal = journal.transition("map_dagster_ready")
    journal = journal.transition("map_runtime_ready")
    journal = journal.transition("pinvi_schema_ready")
    journal = journal.transition("pinvi_api_ready")
    armed = PinnedRuntimeCancelProbeReceipt().transition(
        "armed",
        job_id=str(uuid.uuid4()),
        fixture_created_at="2026-08-06T00:00:00+00:00",
    )
    journal = journal.with_cancel_probe(armed)

    with pytest.raises(DeploymentContractError, match="identity drifted"):
        journal.with_cancel_probe(
            PinnedRuntimeCancelProbeReceipt(
                stage="cancel_post_attempted",
                job_id=armed.job_id,
                fixture_created_at="2026-08-06T00:00:01+00:00",
            )
        )


def test_generation_logical_sha256_excludes_recording_timestamp() -> None:
    initial = _generation()
    later = PinnedRuntimeGeneration(
        **{**initial.to_payload(), "recorded_at": "2026-08-06T01:00:00+00:00"}
    )

    assert generation_logical_sha256(initial) == generation_logical_sha256(later)


def test_legacy_tombstone_receipt_is_fsynced_before_allowlisted_unlink(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o700)
    legacy = state_root / "compatible-pair-v4.json"
    legacy.write_text('{"version":4}\n', encoding="utf-8")
    os.chmod(legacy, 0o600)
    transaction_id = str(uuid.uuid4())

    receipt = retire_f1d_legacy_artifacts(
        state_root=state_root,
        transaction_id=transaction_id,
        candidate=_generation(),
        recorded_at="2026-08-06T00:00:00+00:00",
    )

    assert receipt.transaction_id == transaction_id
    assert receipt.requested_paths == f1d_legacy_artifact_paths()
    assert tuple(entry.relative_path for entry in receipt.retired) == (
        "compatible-pair-v4.json",
    )
    assert not legacy.exists()
    stored = legacy_tombstone_receipt_path(
        state_root,
        pinset_sha256=_generation().pinset_sha256,
    )
    assert stored.exists()
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert retire_f1d_legacy_artifacts(
        state_root=state_root,
        transaction_id=transaction_id,
        candidate=_generation(),
        recorded_at="2026-08-06T00:00:00+00:00",
    ) == receipt


def test_legacy_tombstone_rejects_artifact_that_appears_after_receipt(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o700)
    transaction_id = str(uuid.uuid4())
    retire_f1d_legacy_artifacts(
        state_root=state_root,
        transaction_id=transaction_id,
        candidate=_generation(),
        recorded_at="2026-08-06T00:00:00+00:00",
    )
    foreign = state_root / "cache-target-window-v1.json"
    foreign.write_text("{}\n", encoding="utf-8")
    os.chmod(foreign, 0o600)

    with pytest.raises(DeploymentContractError, match="conflicts"):
        retire_f1d_legacy_artifacts(
            state_root=state_root,
            transaction_id=transaction_id,
            candidate=_generation(),
            recorded_at="2026-08-06T00:00:00+00:00",
        )


def test_v7_tombstone_retires_v5_journal_without_reusing_v5_receipt(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o700)
    old_journal = state_root / "pinned-runtime-rebuild-v5.json"
    old_journal.write_text('{"version":5}\n', encoding="utf-8")
    os.chmod(old_journal, 0o600)
    old_receipt_directory = state_root / "pinned-runtime-v5"
    old_receipt_directory.mkdir(mode=0o700)
    os.chmod(old_receipt_directory, 0o700)
    (old_receipt_directory / "legacy-tombstone-v5.json").write_text(
        '{"historical":true}\n',
        encoding="utf-8",
    )
    os.chmod(old_receipt_directory / "legacy-tombstone-v5.json", 0o600)

    receipt = retire_f1d_legacy_artifacts(
        state_root=state_root,
        transaction_id=str(uuid.uuid4()),
        candidate=_generation(),
        recorded_at="2026-08-06T00:00:00+00:00",
    )

    assert not old_journal.exists()
    assert "pinned-runtime-rebuild-v5.json" in receipt.requested_paths
    assert legacy_tombstone_receipt_path(
        state_root,
        pinset_sha256=_generation().pinset_sha256,
    ).exists()


def test_v7_tombstone_retires_static_v6_v7_journals_and_receipts(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o700)
    old_journal = state_root / "pinned-runtime-rebuild-v6.json"
    old_journal.write_text('{"version":6}\n', encoding="utf-8")
    os.chmod(old_journal, 0o600)
    old_receipt_directory = state_root / "pinned-runtime-v6"
    old_receipt_directory.mkdir(mode=0o700)
    os.chmod(old_receipt_directory, 0o700)
    old_receipt = old_receipt_directory / "legacy-tombstone-v6.json"
    old_receipt.write_text('{"version":6}\n', encoding="utf-8")
    os.chmod(old_receipt, 0o600)
    old_v7_journal = state_root / "pinned-runtime-rebuild-v7.json"
    old_v7_journal.write_text('{"version":7}\n', encoding="utf-8")
    os.chmod(old_v7_journal, 0o600)
    old_v7_receipt_directory = state_root / "pinned-runtime-v7"
    old_v7_receipt_directory.mkdir(mode=0o700)
    os.chmod(old_v7_receipt_directory, 0o700)
    old_v7_receipt = old_v7_receipt_directory / "legacy-tombstone-v7.json"
    old_v7_receipt.write_text('{"version":7}\n', encoding="utf-8")
    os.chmod(old_v7_receipt, 0o600)

    receipt = retire_f1d_legacy_artifacts(
        state_root=state_root,
        transaction_id=str(uuid.uuid4()),
        candidate=_generation(),
        recorded_at="2026-08-06T00:00:00+00:00",
    )

    assert not old_journal.exists()
    assert not old_receipt.exists()
    assert not old_v7_journal.exists()
    assert not old_v7_receipt.exists()
    assert {
        "pinned-runtime-rebuild-v6.json",
        "pinned-runtime-v6/legacy-tombstone-v6.json",
        "pinned-runtime-rebuild-v7.json",
        "pinned-runtime-v7/legacy-tombstone-v7.json",
    } <= set(entry.relative_path for entry in receipt.retired)
    assert legacy_tombstone_receipt_path(
        state_root,
        pinset_sha256=_generation().pinset_sha256,
    ).exists()


def test_legacy_tombstone_rejects_unsafe_artifact_before_receipt(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o700)
    legacy = state_root / "compatible-pair-v4.json"
    legacy.write_text("{}\n", encoding="utf-8")
    os.chmod(legacy, 0o644)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        retire_f1d_legacy_artifacts(
            state_root=state_root,
            transaction_id=str(uuid.uuid4()),
            candidate=_generation(),
            recorded_at="2026-08-06T00:00:00+00:00",
        )

    assert not legacy_tombstone_receipt_path(
        state_root,
        pinset_sha256=_generation().pinset_sha256,
    ).exists()
