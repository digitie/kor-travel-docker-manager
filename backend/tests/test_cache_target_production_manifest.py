from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from kor_travel_docker_manager.services import cache_target_production_manifest as manifest_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_contract import CacheTargetRuntimeContract
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
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
        expected_source_revision=(
            CACHE_TARGET_PRODUCTION_PINS.map_functional_owner_revision
        ),
        expected_contract_generation=CACHE_TARGET_PRODUCTION_PINS.contract_generation,
        role_binding_sha256="b" * 64,
    )


def test_tracked_candidate_is_not_implicitly_promoted_to_release() -> None:
    assert CACHE_TARGET_PRODUCTION_PINS.pinvi_reviewed_candidate_revision == (
        "6ac8baae2814fae5b16c95846ee40d77cc7fe283"
    )
    assert CACHE_TARGET_PRODUCTION_PINS.pinvi_release_revision is None

    with pytest.raises(DeploymentContractError, match="release revision is not pinned"):
        require_cache_target_production_release(_contract())


def test_unconfigured_cache_target_does_not_require_release_pin() -> None:
    _require_cache_target_release(SimpleNamespace(cache_target=None))  # type: ignore[arg-type]


def test_release_requires_exact_contract_candidate_and_both_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = "a" * 40
    monkeypatch.setattr(
        manifest_module,
        "CACHE_TARGET_PRODUCTION_PINS",
        replace(CACHE_TARGET_PRODUCTION_PINS, pinvi_release_revision=release),
    )
    pairs = (
        SimpleNamespace(pinvi_source_revision=release),
        SimpleNamespace(pinvi_source_revision=release),
    )

    require_cache_target_production_release(
        _contract(),
        pairs=pairs,
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
    with pytest.raises(DeploymentContractError, match="compatible pair differs"):
        require_cache_target_production_release(
            _contract(),
            pairs=(pairs[0], SimpleNamespace(pinvi_source_revision="b" * 40)),
        )
