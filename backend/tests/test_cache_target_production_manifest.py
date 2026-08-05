from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_contract import CacheTargetRuntimeContract
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
    CacheTargetProductionPinManifest,
    require_cache_target_production_release,
)
from kor_travel_docker_manager.services.compose_service import (
    _require_cache_target_release,
)


def _contract() -> CacheTargetRuntimeContract:
    return CacheTargetRuntimeContract(
        registry_json="[]",
        sync_enabled="false",
        command_token="c" * 32,
        consumer_token="u" * 32,
        restore_fence_token="r" * 32,
        recovery_token="v" * 32,
        consumer_id="pinvi-cache-target-consumer",
        expected_openapi_sha256=CACHE_TARGET_PRODUCTION_PINS.service_openapi_sha256,
        expected_source_revision=(CACHE_TARGET_PRODUCTION_PINS.map_functional_owner_revision),
        expected_contract_generation=CACHE_TARGET_PRODUCTION_PINS.contract_generation,
        role_binding_sha256="b" * 64,
    )


def test_tracked_release_is_the_current_generation_seven_pair() -> None:
    assert CACHE_TARGET_PRODUCTION_PINS == CacheTargetProductionPinManifest(
        version=1,
        contract_generation="7",
        service_openapi_sha256=("144b4335d98fc021368b3297f5b8ed7b1c560e9850ebbdd8af71e45623ba7b3d"),
        map_functional_owner_revision="e12494bd5c4b5b2e1d51c72b6ddcf18eead0e53f",
        map_release_revision="c0afaa4e318a2e2e6d85f53bb889af3e6adec8c1",
        pinvi_reviewed_candidate_revision="51289cb1651e7771b0ff5c685989a9768d81b870",
        pinvi_release_revision="3ff54b8b15965c6ecd5c55b1419208e65831c7fe",
    )


def test_unconfigured_cache_target_does_not_require_release_pin() -> None:
    _require_cache_target_release(SimpleNamespace(cache_target=None))  # type: ignore[arg-type]


def test_release_requires_exact_contract_candidate_and_both_pairs() -> None:
    release = CACHE_TARGET_PRODUCTION_PINS.pinvi_release_revision
    assert release is not None
    pairs = (
        SimpleNamespace(
            map_source_revision=CACHE_TARGET_PRODUCTION_PINS.map_release_revision,
            pinvi_source_revision=release,
        ),
        SimpleNamespace(
            map_source_revision=CACHE_TARGET_PRODUCTION_PINS.map_release_revision,
            pinvi_source_revision=release,
        ),
    )

    require_cache_target_production_release(
        _contract(),
        pairs=pairs,
        candidate_map_source_revision=(CACHE_TARGET_PRODUCTION_PINS.map_release_revision),
        candidate_source_revision=release,
    )

    with pytest.raises(DeploymentContractError, match="tracked pin manifest"):
        require_cache_target_production_release(
            replace(_contract(), expected_contract_generation="6"),
        )
    with pytest.raises(DeploymentContractError, match="candidate differs"):
        require_cache_target_production_release(
            _contract(),
            candidate_source_revision="b" * 40,
        )
    with pytest.raises(DeploymentContractError, match="candidate differs"):
        require_cache_target_production_release(
            _contract(),
            candidate_source_revision=(
                CACHE_TARGET_PRODUCTION_PINS.pinvi_reviewed_candidate_revision
            ),
        )
    with pytest.raises(DeploymentContractError, match="Map candidate differs"):
        require_cache_target_production_release(
            _contract(),
            candidate_map_source_revision="b" * 40,
        )
    with pytest.raises(DeploymentContractError, match="compatible pair differs"):
        require_cache_target_production_release(
            _contract(),
            pairs=(
                pairs[0],
                SimpleNamespace(
                    map_source_revision=(CACHE_TARGET_PRODUCTION_PINS.map_release_revision),
                    pinvi_source_revision="b" * 40,
                ),
            ),
        )
    with pytest.raises(DeploymentContractError, match="compatible pair differs"):
        require_cache_target_production_release(
            _contract(),
            pairs=(
                pairs[0],
                SimpleNamespace(
                    map_source_revision="c" * 40,
                    pinvi_source_revision=release,
                ),
            ),
        )
