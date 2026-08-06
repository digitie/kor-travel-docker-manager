from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeGeneration,
    PinnedRuntimeManifest,
    PinnedRuntimeRebuildJournal,
    load_deployment_mode,
    read_manifest,
    read_rebuild_journal,
    require_rebuildable_mode,
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
