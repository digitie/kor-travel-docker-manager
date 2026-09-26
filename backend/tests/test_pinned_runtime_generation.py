from __future__ import annotations

import stat
from dataclasses import replace
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import c6c_deployment
from kor_travel_docker_manager.services.c6c_deployment import (
    _PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
    DeploymentContractError,
    assert_compose_mutation_allowed,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    MapApplication300CandidateEvidence,
    PinnedRuntimeGeneration,
    ensure_pinned_runtime_state_directory,
    generation_logical_sha256,
    load_deployment_mode,
    pinned_runtime_state_paths,
    require_rebuildable_mode,
)

_PINSET_SHA256 = "a" * 64


def _digest(seed: str) -> str:
    return seed * 64


def _revision(seed: str) -> str:
    return seed * 40


def _image_id(seed: str) -> str:
    return f"sha256:{_digest(seed)}"


def _candidate_evidence(seed: str = "a") -> MapApplication300CandidateEvidence:
    return MapApplication300CandidateEvidence(
        candidate_git_tree=_revision(seed),
        postgres_image_id=_image_id(seed),
        dagster_config_sha256=_digest(seed),
    )


def _generation(seed: str = "a") -> PinnedRuntimeGeneration:
    return PinnedRuntimeGeneration(
        map_api_image_id=_image_id(seed),
        map_ui_image_id=_image_id(seed),
        map_dagster_image_id=_image_id(seed),
        map_dagster_daemon_image_id=_image_id(seed),
        pinvi_api_image_id=_image_id(seed),
        pinvi_web_image_id=_image_id(seed),
        pinvi_dagster_image_id=_image_id(seed),
        map_source_revision=_revision(seed),
        pinvi_source_revision=_revision(seed),
        map_application_head="0084_c6c_cancel_probe_fixtures",
        map_dagster_head="dagster-1",
        pinvi_head="20260801_0050",
        pinset_sha256=_digest(seed),
        map_application_300_candidate_evidence=_candidate_evidence(seed),
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
    assert paths.pinset_sha256 == _PINSET_SHA256
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


def test_generation_logical_sha256_excludes_recording_timestamp() -> None:
    initial = _generation()
    later = replace(initial, recorded_at="2026-08-06T01:00:00+00:00")

    assert generation_logical_sha256(initial) == generation_logical_sha256(later)


def test_v4_manifest_api_is_absent_and_only_tombstoned() -> None:
    for name in (
        "CompatibleImagePair",
        "CompatiblePairManifest",
        "parse_pair_manifest",
        "initial_pair_manifest",
        "write_pair_manifest",
        "restore_pair_manifest_snapshot",
    ):
        assert not hasattr(c6c_deployment, name)
