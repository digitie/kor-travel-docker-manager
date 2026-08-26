"""F1D v5 rebuild가 소비하는 tracked Map·PinVi source release authority.

이 모듈은 cache-target release나 legacy compatible-pair의 pin을 읽지 않는다.
v5 rebuild는 여기의 exact revision·canonical HTTPS URL과 canonical pinset digest만
source provenance로 수용한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

RuntimeSourceRole = Literal["map", "pinvi"]

PINNED_RUNTIME_RELEASE_VERSION: Final = 5
RUNTIME_SOURCE_ROLES: Final[tuple[RuntimeSourceRole, ...]] = ("map", "pinvi")
CANONICAL_RUNTIME_SOURCE_URLS: Final[Mapping[RuntimeSourceRole, str]] = MappingProxyType(
    {
        "map": "https://github.com/digitie/kor-travel-map.git",
        "pinvi": "https://github.com/digitie/pinvi.git",
    }
)

_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PinnedRuntimeSourceSpec:
    """한 runtime source role의 immutable Git release pin."""

    role: RuntimeSourceRole
    canonical_url: str
    revision: str

    def __post_init__(self) -> None:
        canonical_url = CANONICAL_RUNTIME_SOURCE_URLS.get(self.role)
        if canonical_url is None:
            raise DeploymentContractError("pinned runtime source role is invalid")
        if self.canonical_url != canonical_url:
            raise DeploymentContractError("pinned runtime source URL is not canonical")
        if _REVISION.fullmatch(self.revision) is None:
            raise DeploymentContractError("pinned runtime source revision is invalid")

    def to_payload(self) -> dict[str, str]:
        """pinset digest에 쓰는 stable wire shape."""

        return {
            "role": self.role,
            "url": self.canonical_url,
            "revision": self.revision,
        }


def canonical_pinset_bytes(
    *,
    version: int,
    sources: tuple[PinnedRuntimeSourceSpec, ...],
) -> bytes:
    """정렬된 JSON object·compact separator로 v5 pinset bytes를 만든다."""

    payload = {
        "version": version,
        "sources": [source.to_payload() for source in sources],
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_pinset_sha256(
    *,
    version: int,
    sources: tuple[PinnedRuntimeSourceSpec, ...],
) -> str:
    """candidate generation과 durable journal이 공유하는 pinset identity."""

    return hashlib.sha256(canonical_pinset_bytes(version=version, sources=sources)).hexdigest()


@dataclass(frozen=True)
class PinnedRuntimeRelease:
    """C2가 source staging·candidate build에 전달하는 v5 release pinset."""

    version: Literal[5]
    sources: tuple[PinnedRuntimeSourceSpec, ...]
    pinset_sha256: str

    def __post_init__(self) -> None:
        if self.version != PINNED_RUNTIME_RELEASE_VERSION:
            raise DeploymentContractError("pinned runtime release version is invalid")
        sources = tuple(self.sources)
        object.__setattr__(self, "sources", sources)
        roles = tuple(source.role for source in sources)
        if roles != RUNTIME_SOURCE_ROLES:
            raise DeploymentContractError(
                "pinned runtime release source roles must be map then pinvi exactly once"
            )
        if _SHA256.fullmatch(self.pinset_sha256) is None:
            raise DeploymentContractError("pinned runtime release pinset digest is invalid")
        if self.pinset_sha256 != canonical_pinset_sha256(
            version=self.version,
            sources=sources,
        ):
            raise DeploymentContractError("pinned runtime release pinset digest differs")

    def source_for(self, role: RuntimeSourceRole) -> PinnedRuntimeSourceSpec:
        """role별 immutable source spec을 반환한다."""

        return self.sources[RUNTIME_SOURCE_ROLES.index(role)]

    @property
    def sources_by_role(self) -> Mapping[RuntimeSourceRole, PinnedRuntimeSourceSpec]:
        """외부 mutation이 불가능한 role→source view."""

        return MappingProxyType({source.role: source for source in self.sources})

    def to_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "sources": [source.to_payload() for source in self.sources],
            "pinset_sha256": self.pinset_sha256,
        }


MAP_PINNED_RUNTIME_SOURCE: Final = PinnedRuntimeSourceSpec(
    role="map",
    canonical_url=CANONICAL_RUNTIME_SOURCE_URLS["map"],
    revision="14d18230e5a9ff21caf26d6abe37aed1e4944685",
)
PINVI_PINNED_RUNTIME_SOURCE: Final = PinnedRuntimeSourceSpec(
    role="pinvi",
    canonical_url=CANONICAL_RUNTIME_SOURCE_URLS["pinvi"],
    revision="93296aee5d47676e6b9b79303bf417c598a273ac",
)
_CURRENT_SOURCES: Final[tuple[PinnedRuntimeSourceSpec, ...]] = (
    MAP_PINNED_RUNTIME_SOURCE,
    PINVI_PINNED_RUNTIME_SOURCE,
)
PINNED_RUNTIME_RELEASE: Final = PinnedRuntimeRelease(
    version=PINNED_RUNTIME_RELEASE_VERSION,
    sources=_CURRENT_SOURCES,
    pinset_sha256="d9aded44779114ed0595d3a4fb50908efb56b57c85148faf3083b0087a35e898",
)


def current_pinned_runtime_release() -> PinnedRuntimeRelease:
    """tracked v5 release authority를 반환한다."""

    return PINNED_RUNTIME_RELEASE
