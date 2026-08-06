from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeGeneration,
    PinnedRuntimeManifest,
    PinnedRuntimeRebuildJournal,
    f1d_legacy_artifact_paths,
    generation_logical_sha256,
    legacy_tombstone_receipt_path,
    load_deployment_mode,
    read_manifest,
    read_rebuild_journal,
    require_rebuildable_mode,
    retire_f1d_legacy_artifacts,
    write_manifest,
    write_rebuild_journal,
)


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
        version=5,
        transaction_id=str(uuid.uuid4()),
        phase="candidate_attested",
        candidate=_generation(),
        environment_sha256="b" * 64,
        compose_sha256="c" * 64,
        resolved_compose_sha256="d" * 64,
        created_at="2026-08-06T00:00:00+00:00",
    )
    path = state / "pinned-runtime-rebuild-v5.json"

    write_rebuild_journal(path, journal)
    restored = read_rebuild_journal(path)

    assert restored == journal
    assert restored.transition("reset_intent_durable").phase == "reset_intent_durable"
    with pytest.raises(DeploymentContractError, match="phase transition"):
        restored.transition("databases_recreated")


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
    stored = legacy_tombstone_receipt_path(state_root)
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

    assert not legacy_tombstone_receipt_path(state_root).exists()
