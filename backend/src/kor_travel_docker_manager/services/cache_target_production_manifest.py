from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_contract import (
    CacheTargetRuntimeContract,
)
from kor_travel_docker_manager.services.map_service_contract import (
    CACHE_TARGET_CAPABILITY_GENERATION,
)

_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_CONTRACT_GENERATION_PATTERN = re.compile(r"[1-9][0-9]*")
_ALEMBIC_HEAD_PATTERN = re.compile(r"[0-9]{4}_[a-z0-9_]+")


@dataclass(frozen=True)
class CacheTargetProductionPinManifest:
    """F1F v2 production deployment input의 유일한 release authority다."""

    version: int = 2
    map_release_revision: str = "8c5bdcf8ce892439a8bb8e0013edf74127bf076a"
    pinvi_release_revision: str = "3b87c19cc78a07121c27df7d7a4c382c2d3aa068"
    service_openapi_sha256: str = (
        "c7838b20bd70bf333590cb440a705dd7e893f9e366078d6c11200d701d40bdcd"
    )
    contract_generation: str = str(CACHE_TARGET_CAPABILITY_GENERATION)
    map_application_alembic_head: str = "0083_nonderived_uuid_generator"

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 2:
            raise ValueError("cache-target production pin manifest version must be int 2")
        _require_pattern(
            "map_release_revision", self.map_release_revision, _REVISION_PATTERN
        )
        _require_pattern(
            "pinvi_release_revision", self.pinvi_release_revision, _REVISION_PATTERN
        )
        _require_pattern(
            "service_openapi_sha256", self.service_openapi_sha256, _SHA256_PATTERN
        )
        _require_pattern(
            "contract_generation",
            self.contract_generation,
            _CONTRACT_GENERATION_PATTERN,
        )
        _require_pattern(
            "map_application_alembic_head",
            self.map_application_alembic_head,
            _ALEMBIC_HEAD_PATTERN,
        )

    def canonical_pinset_json(self) -> str:
        """v2 semantic field를 정렬된 compact JSON으로 직렬화한다."""

        return json.dumps(
            {
                "version": self.version,
                "map_release_revision": self.map_release_revision,
                "pinvi_release_revision": self.pinvi_release_revision,
                "service_openapi_sha256": self.service_openapi_sha256,
                "contract_generation": self.contract_generation,
                "map_application_alembic_head": self.map_application_alembic_head,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def pinset_sha256(self) -> str:
        return hashlib.sha256(self.canonical_pinset_json().encode("utf-8")).hexdigest()


def _require_pattern(name: str, value: object, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"cache-target production pin manifest {name} is invalid")


class CacheTargetPairProvenance(Protocol):
    @property
    def map_source_revision(self) -> str: ...

    @property
    def pinvi_source_revision(self) -> str: ...


# T-VN-41 F1F v2 pinned deployment input의 tracked 정본이다. Map/PinVi exact
# release만 source authority이며, 이전 functional-owner/reviewed-candidate 감사 참조는
# v1 이력에만 남긴다.
CACHE_TARGET_PRODUCTION_PINS = CacheTargetProductionPinManifest()


def require_cache_target_production_release(
    contract: CacheTargetRuntimeContract,
    *,
    pairs: tuple[CacheTargetPairProvenance, ...] = (),
    candidate_map_source_revision: str | None = None,
    candidate_source_revision: str | None = None,
) -> CacheTargetProductionPinManifest:
    """v2 exact release와 contract/pair provenance 없이는 production을 차단한다."""

    manifest = CACHE_TARGET_PRODUCTION_PINS
    if (
        contract.expected_contract_generation != manifest.contract_generation
        or contract.expected_openapi_sha256 != manifest.service_openapi_sha256
        or contract.expected_source_revision != manifest.map_release_revision
    ):
        raise DeploymentContractError(
            "cache-target production contract differs from the tracked pin manifest"
        )
    if (
        candidate_source_revision is not None
        and candidate_source_revision != manifest.pinvi_release_revision
    ):
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
        pair.pinvi_source_revision != manifest.pinvi_release_revision
        or pair.map_source_revision != manifest.map_release_revision
        for pair in pairs
    ):
        raise DeploymentContractError(
            "cache-target compatible pair differs from the pinned Map/PinVi release revision"
        )
    return manifest
