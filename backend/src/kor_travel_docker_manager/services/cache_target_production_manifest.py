from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_contract import (
    CacheTargetRuntimeContract,
)


@dataclass(frozen=True)
class CacheTargetProductionPinManifest:
    version: int
    contract_generation: str
    service_openapi_sha256: str
    map_functional_owner_revision: str
    map_release_revision: str
    pinvi_reviewed_candidate_revision: str
    pinvi_release_revision: str | None


class CacheTargetPairProvenance(Protocol):
    @property
    def map_source_revision(self) -> str: ...

    @property
    def pinvi_source_revision(self) -> str: ...


# T-VN-41 production cutover의 tracked 정본이다. reviewed candidate는 감사 정보일
# 뿐 release fallback이 아니다. release는 적대적 리뷰를 통과한 exact squash merge SHA다.
CACHE_TARGET_PRODUCTION_PINS = CacheTargetProductionPinManifest(
    version=1,
    contract_generation="7",
    service_openapi_sha256=(
        "622ea54c98e9b0c09592cf84aced36227992c6bdf256742a3532b892f0efccf2"
    ),
    map_functional_owner_revision="9b945ce832ecc3ed037d66c9d4e7bda9a1a69ae0",
    map_release_revision="d50bb2c53c179d182b9cf017308df075b691414e",
    pinvi_reviewed_candidate_revision="6ac8baae2814fae5b16c95846ee40d77cc7fe283",
    pinvi_release_revision="4943282006139fa3b4ef3cb247780bfd9721b4c7",
)


def require_cache_target_production_release(
    contract: CacheTargetRuntimeContract,
    *,
    pairs: tuple[CacheTargetPairProvenance, ...] = (),
    candidate_map_source_revision: str | None = None,
    candidate_source_revision: str | None = None,
) -> CacheTargetProductionPinManifest:
    """명시 release와 exact contract/pair provenance 없이는 production을 차단한다."""

    manifest = CACHE_TARGET_PRODUCTION_PINS
    release_revision = manifest.pinvi_release_revision
    if release_revision is None:
        raise DeploymentContractError(
            "cache-target production PinVi release revision is not pinned"
        )
    if (
        contract.expected_contract_generation != manifest.contract_generation
        or contract.expected_openapi_sha256 != manifest.service_openapi_sha256
        or contract.expected_source_revision
        != manifest.map_functional_owner_revision
    ):
        raise DeploymentContractError(
            "cache-target production contract differs from the tracked pin manifest"
        )
    if candidate_source_revision is not None and candidate_source_revision != release_revision:
        raise DeploymentContractError(
            "cache-target PinVi candidate differs from the pinned release revision"
        )
    if (
        candidate_map_source_revision is not None
        and candidate_map_source_revision != manifest.map_release_revision
    ):
        raise DeploymentContractError(
            "cache-target Map candidate differs from the pinned release revision"
        )
    if any(
        pair.pinvi_source_revision != release_revision
        or pair.map_source_revision != manifest.map_release_revision
        for pair in pairs
    ):
        raise DeploymentContractError(
            "cache-target compatible pair differs from the pinned Map/PinVi release revision"
        )
    return manifest
