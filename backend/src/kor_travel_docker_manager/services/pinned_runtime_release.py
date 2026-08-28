"""F1D v5 rebuild가 소비하는 tracked Map·PinVi source release authority.

이 모듈은 cache-target release나 legacy compatible-pair의 pin을 읽지 않는다.
v5 rebuild는 exact revision·canonical HTTPS URL과 canonical pinset digest만 source
provenance로 수용한다.

**값의 출처는 registry 파일이고, 계약의 소유자는 이 모듈이다.** pinned revision은
``runtime_pin_registry``가 읽는 root 소유 JSON 파일에 있지만, canonical URL 집합·
40-hex 형식·role 순서·digest 재계산 대조는 여기 dataclass가 파싱 직후 강제한다.
파일을 편집해 임의 저장소를 가리키게 만드는 것은 코드 수정 없이는 불가능하다.
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


def source_specs_for(
    *,
    map_revision: str,
    pinvi_revision: str,
) -> tuple[PinnedRuntimeSourceSpec, ...]:
    """canonical role 순서로 source spec tuple을 만든다(URL은 코드가 공급한다)."""

    return (
        PinnedRuntimeSourceSpec(
            role="map",
            canonical_url=CANONICAL_RUNTIME_SOURCE_URLS["map"],
            revision=map_revision,
        ),
        PinnedRuntimeSourceSpec(
            role="pinvi",
            canonical_url=CANONICAL_RUNTIME_SOURCE_URLS["pinvi"],
            revision=pinvi_revision,
        ),
    )


def is_blocked_pinset_retry(
    *,
    pinset_sha256: str,
    map_source_revision: str,
    pinvi_source_revision: str,
    phase: str,
) -> bool:
    """registry의 차단 목록에 해당하는 candidate 재실행인지 판정한다.

    이전에는 receipt schema 도입 전 topology failure로 끝난 d9 pinset 하나만 코드
    상수 3종으로 고정했다. 실제 운영 규율은 "terminal 판정 candidate는 영구 재시도
    금지"이고 그 목록은 회전마다 늘어나므로, 목록은 registry가 소유한다.

    registry를 읽지 못하면 예외를 전파한다. 여기서 ``False``를 반환하면 파일이 사라진
    순간 d9 계열 차단이 통째로 열리는 fail-open이 된다 — 호출자가 이미 registry를 읽은
    뒤라 정상 경로에서 예외가 날 일이 없으므로 전파 비용은 0이고, 이득은 "파일이
    사라지면 멈춘다"이다. 코드가 강제하는 하한선은 registry와 무관하게 먼저 판정한다.
    """

    from kor_travel_docker_manager.services.runtime_pin_registry import (
        code_enforced_blocked_entry,
        load_runtime_pin_registry,
    )

    if (
        code_enforced_blocked_entry(
            pinset_sha256=pinset_sha256,
            map_source_revision=map_source_revision,
            pinvi_source_revision=pinvi_source_revision,
            phase=phase,
        )
        is not None
    ):
        return True
    registry = load_runtime_pin_registry()
    return (
        registry.blocked_entry_for(
            pinset_sha256=pinset_sha256,
            map_source_revision=map_source_revision,
            pinvi_source_revision=pinvi_source_revision,
            phase=phase,
        )
        is not None
    )


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


def current_pinned_runtime_release() -> PinnedRuntimeRelease:
    """tracked v5 release authority를 registry 파일에서 읽어 반환한다.

    시그니처는 상수 시절과 동일하다 — rebuild 경로의 소비처는
    ``compose_service.rebuild_pinned_runtime`` 한 곳뿐이라 전파가 없다.
    registry 부재·파싱 실패·digest 불일치는 상수 폴백 없이 fail-close다.
    """

    from kor_travel_docker_manager.services.runtime_pin_registry import (
        load_runtime_pin_registry,
    )

    return load_runtime_pin_registry().release()


def current_map_source_revision() -> str:
    """Map application 300 계약이 기대하는 source commit."""

    return current_pinned_runtime_release().source_for("map").revision
