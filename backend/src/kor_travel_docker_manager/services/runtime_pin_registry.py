"""Runtime pin registry — Map·PinVi pinned revision의 파일 기반 authority.

이 모듈은 v5 rebuild가 소비하는 pinned source revision을 소스코드 상수가 아니라
root 소유 JSON registry 파일에서 읽는다. 값은 파일에 있어도 **검증은 코드가 소유한다** —
canonical URL 집합, 40-hex revision 형식, role 순서, pinset digest 재계산 대조는
파싱 직후 그대로 실행되고 하나라도 어긋나면 fail-close한다. 따라서 파일을 편집해
임의 저장소를 가리키게 만드는 것은 코드 수정 없이는 불가능하다.

registry는 현재 pin뿐 아니라 pinset의 **생애 상태**도 담는다. ``blocked_pinsets``는
terminal(재시도 금지) 판정을 받은 candidate 목록이고, ``history``는 회전 체인이다.
이전에는 이 두 가지가 각각 코드 상수(d9 계열)와 세 저장소의 수기 문서에만 있었다.

설계 정본: ``docs/ktdctl-ui-migration.md`` 1부(P1·P10-1·P10-2).
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

RUNTIME_PIN_REGISTRY_SCHEMA: Final = "kor-travel-docker-manager.runtime-pin-registry.v1"
RUNTIME_PINS_FILE_ENV: Final = "KTDM_RUNTIME_PINS_FILE"
RUNTIME_PINS_PUBLIC_FILE_ENV: Final = "KTDM_RUNTIME_PINS_PUBLIC_FILE"
# 개발 전용 완화. 기본은 안전하고 완화만 명시적이다. Windows 공유 마운트(drvfs 등)는
# 모든 파일을 0777로 보고해 mode 검사를 통과할 수 없으므로 그 환경에서만 쓴다.
# root에서는 절대 적용되지 않는다 — 파괴적 작업을 수행하는 주체가 완화 대상이 되면
# 완화가 곧 구멍이 된다.
RUNTIME_PINS_ALLOW_INSECURE_MODE_ENV: Final = "KTDM_RUNTIME_PINS_ALLOW_INSECURE_MODE"

_DEFAULT_REGISTRY_RELPATH: Final = ("config", "runtime-pins.json")
# 저장소에 추적되는 읽기 전용 부트스트랩 입력. 회전 대상이 아니다.
_SEED_BASENAME: Final = "runtime-pins.seed.json"
_SEED_RELPATH: Final = ("config", _SEED_BASENAME)
# ``pinned_runtime_release.PINNED_RUNTIME_RELEASE_VERSION``의 거울. 그 모듈을 module
# scope에서 import하면 순환이 되므로 값을 복제하고 테스트로 동일성을 고정한다.
_SUPPORTED_RELEASE_VERSION: Final = 5
_DEFAULT_PUBLIC_BASENAME: Final = ".ktdm-runtime-pins.json"
# trusted installer의 canonical execution root. 이 트리는 release마다 통째 교체된다.
_TRUSTED_INSTALL_ROOT: Final = Path("/opt/kor-travel-docker-manager")
# 설치 root에서 돌 때의 registry 기본 위치. 트리 교체에 살아남아야 하므로 트리 밖이다.
_TRUSTED_STATE_ROOT: Final = Path("/var/lib/kor-travel-docker-manager")
# 공개 사본은 **별도 트리**다. installer가 위 상태 root를 매 설치마다 0700 root:root로
# 되돌리므로 그 안에 두면 비-root backend가 traverse조차 못 해 조회 API가 영구
# ``unknown``이 된다(n150 실측). 사본은 비밀이 없으므로 world-readable 트리에 둔다.
_TRUSTED_PUBLIC_ROOT: Final = Path("/var/lib/kor-travel-docker-manager-public")

_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")

# 쓰기 경로가 읽기 경로에서 거부되는 파일을 만들면 스스로를 brick한다.
# ``_require_text``는 **문자 수**를 제한하는데 저장은 ``ensure_ascii=True``라 비ASCII
# 1자가 6~12바이트가 된다. 이 프로젝트의 사유 문자열은 한국어이므로 상한을 가득 채운
# registry(history 500 + blocked 500)의 실측 크기가 ASCII 약 1MB, 한국어 약 4.5MB다.
_MAX_REGISTRY_BYTES: Final = 16 * 1024 * 1024
_MAX_REASON_LENGTH: Final = 500
_MAX_ACTOR_LENGTH: Final = 200
_MAX_HISTORY_ENTRIES: Final = 500
_MAX_BLOCKED_ENTRIES: Final = 500


class RuntimePinRegistryError(DeploymentContractError):
    """registry 파일의 부재·손상·계약 위반을 fail-close로 알린다."""


def _require_text(value: Any, field: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise RuntimePinRegistryError(f"runtime pin registry {field} must be a string")
    text = value.strip()
    if not text:
        raise RuntimePinRegistryError(f"runtime pin registry {field} must not be empty")
    if len(text) > max_length:
        raise RuntimePinRegistryError(f"runtime pin registry {field} is too long")
    if any(character in text for character in ("\n", "\r", "\x00")):
        raise RuntimePinRegistryError(f"runtime pin registry {field} must be a single line")
    return text


def _require_revision(value: Any, field: str) -> str:
    if not isinstance(value, str) or _REVISION.fullmatch(value) is None:
        raise RuntimePinRegistryError(f"runtime pin registry {field} must be a 40-hex revision")
    return value


def _require_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RuntimePinRegistryError(f"runtime pin registry {field} must be a sha256 digest")
    return value


def _require_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise RuntimePinRegistryError(
            f"runtime pin registry {field} must be a UTC RFC3339 timestamp"
        )
    return value


def _compute_pinset_sha256(*, release_version: int, map_revision: str, pinvi_revision: str) -> str:
    from kor_travel_docker_manager.services.pinned_runtime_release import (
        canonical_pinset_sha256,
        source_specs_for,
    )

    return canonical_pinset_sha256(
        version=release_version,
        sources=source_specs_for(map_revision=map_revision, pinvi_revision=pinvi_revision),
    )


def utc_timestamp() -> str:
    """registry가 기록하는 canonical UTC timestamp."""

    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class BlockedPinset:
    """재시도가 영구 금지된 candidate pinset.

    ``phase``가 있으면 그 phase의 journal에서만 차단하고, 없으면 해당 pinset의 모든
    재개를 차단한다. 이전에는 이 정보가 ``pinned_runtime_release`` 의 d9 상수 3종과
    kor-travel-map·pinvi 저장소 문서의 수기 목록에만 존재했다.
    """

    pinset_sha256: str
    map_revision: str
    pinvi_revision: str
    reason: str
    blocked_at: str
    phase: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.pinset_sha256, "blocked_pinsets[].pinset_sha256")
        _require_revision(self.map_revision, "blocked_pinsets[].map_revision")
        _require_revision(self.pinvi_revision, "blocked_pinsets[].pinvi_revision")
        _require_text(self.reason, "blocked_pinsets[].reason", max_length=_MAX_REASON_LENGTH)
        _require_timestamp(self.blocked_at, "blocked_pinsets[].blocked_at")
        if self.phase is not None:
            _require_text(self.phase, "blocked_pinsets[].phase", max_length=_MAX_ACTOR_LENGTH)
        # digest와 revision이 어긋난 차단 항목은 어떤 journal에도 매치하지 않는다 —
        # 즉 "차단했다"고 기록됐지만 실제로는 아무것도 막지 못하는 조용한 무력화다.
        # 최상위 pinset digest와 같은 강도로 재계산 대조한다.
        expected = _compute_pinset_sha256(
            release_version=_SUPPORTED_RELEASE_VERSION,
            map_revision=self.map_revision,
            pinvi_revision=self.pinvi_revision,
        )
        if self.pinset_sha256 != expected:
            raise RuntimePinRegistryError(
                "runtime pin registry blocked_pinsets[] digest does not match its revisions"
            )

    def matches(
        self,
        *,
        pinset_sha256: str,
        map_source_revision: str,
        pinvi_source_revision: str,
        phase: str,
    ) -> bool:
        """journal 식별자가 이 차단 항목에 해당하는지 판정한다."""

        if (
            pinset_sha256 != self.pinset_sha256
            or map_source_revision != self.map_revision
            or pinvi_source_revision != self.pinvi_revision
        ):
            return False
        return self.phase is None or self.phase == phase

    @classmethod
    def from_payload(cls, payload: Any) -> BlockedPinset:
        if not isinstance(payload, dict):
            raise RuntimePinRegistryError("runtime pin registry blocked_pinsets[] must be objects")
        unknown = set(payload) - {
            "pinset_sha256",
            "map_revision",
            "pinvi_revision",
            "reason",
            "blocked_at",
            "phase",
        }
        if unknown:
            raise RuntimePinRegistryError(
                "runtime pin registry blocked_pinsets[] has unknown fields"
            )
        return cls(
            pinset_sha256=payload.get("pinset_sha256"),
            map_revision=payload.get("map_revision"),
            pinvi_revision=payload.get("pinvi_revision"),
            reason=payload.get("reason"),
            blocked_at=payload.get("blocked_at"),
            phase=payload.get("phase"),
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pinset_sha256": self.pinset_sha256,
            "map_revision": self.map_revision,
            "pinvi_revision": self.pinvi_revision,
            "reason": self.reason,
            "blocked_at": self.blocked_at,
        }
        if self.phase is not None:
            payload["phase"] = self.phase
        return payload


# 코드가 소유하는 제거 불가능한 차단 하한선.
#
# 값은 registry가 소유하지만 **하한선은 코드가 소유한다.** registry가 손상되거나,
# 오래된 사본으로 시딩되거나, 사람이 목록에서 지워도 이 항목만은 계속 차단된다.
# d9은 lifecycle receipt schema 도입 전 PinVi role topology failure로 끝난 historical
# candidate로, 전환 이전에는 코드 상수 3종으로 무조건 차단되던 대상이다 — 파일로
# 옮기면서 그 방어를 잃지 않기 위해 여기 남긴다.
_CODE_ENFORCED_BLOCKED_PINSETS: Final[tuple[BlockedPinset, ...]] = (
    BlockedPinset(
        pinset_sha256="d9aded44779114ed0595d3a4fb50908efb56b57c85148faf3083b0087a35e898",
        map_revision="14d18230e5a9ff21caf26d6abe37aed1e4944685",
        pinvi_revision="93296aee5d47676e6b9b79303bf417c598a273ac",
        phase="map_runtime_ready",
        reason=(
            "lifecycle receipt schema 도입 전 PinVi role topology failure로 끝난 "
            "historical candidate (코드가 강제하는 차단 하한선)"
        ),
        blocked_at="2026-08-27T09:26:00Z",
    ),
)


def code_enforced_blocked_entry(
    *,
    pinset_sha256: str,
    map_source_revision: str,
    pinvi_source_revision: str,
    phase: str,
) -> BlockedPinset | None:
    """registry를 읽지 못해도 성립하는 차단 판정."""

    for entry in _CODE_ENFORCED_BLOCKED_PINSETS:
        if entry.matches(
            pinset_sha256=pinset_sha256,
            map_source_revision=map_source_revision,
            pinvi_source_revision=pinvi_source_revision,
            phase=phase,
        ):
            return entry
    return None


@dataclass(frozen=True)
class PinRotation:
    """회전 1건의 감사 기록."""

    pinset_sha256: str
    rotated_at: str
    rotated_by: str
    reason: str
    supersedes_pinset_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.pinset_sha256, "history[].pinset_sha256")
        _require_timestamp(self.rotated_at, "history[].rotated_at")
        _require_text(self.rotated_by, "history[].rotated_by", max_length=_MAX_ACTOR_LENGTH)
        _require_text(self.reason, "history[].reason", max_length=_MAX_REASON_LENGTH)
        if self.supersedes_pinset_sha256 is not None:
            _require_digest(self.supersedes_pinset_sha256, "history[].supersedes_pinset_sha256")

    @classmethod
    def from_payload(cls, payload: Any) -> PinRotation:
        if not isinstance(payload, dict):
            raise RuntimePinRegistryError("runtime pin registry history[] must be objects")
        unknown = set(payload) - {
            "pinset_sha256",
            "rotated_at",
            "rotated_by",
            "reason",
            "supersedes_pinset_sha256",
        }
        if unknown:
            raise RuntimePinRegistryError("runtime pin registry history[] has unknown fields")
        return cls(
            pinset_sha256=payload.get("pinset_sha256"),
            rotated_at=payload.get("rotated_at"),
            rotated_by=payload.get("rotated_by"),
            reason=payload.get("reason"),
            supersedes_pinset_sha256=payload.get("supersedes_pinset_sha256"),
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pinset_sha256": self.pinset_sha256,
            "rotated_at": self.rotated_at,
            "rotated_by": self.rotated_by,
            "reason": self.reason,
        }
        if self.supersedes_pinset_sha256 is not None:
            payload["supersedes_pinset_sha256"] = self.supersedes_pinset_sha256
        return payload


@dataclass(frozen=True)
class RuntimePinRegistry:
    """registry 파일 1개의 파싱·검증된 표현."""

    release_version: int
    map_revision: str
    pinvi_revision: str
    pinset_sha256: str
    rotated_at: str
    rotated_by: str
    reason: str
    history: tuple[PinRotation, ...] = ()
    blocked_pinsets: tuple[BlockedPinset, ...] = ()

    def __post_init__(self) -> None:
        if self.release_version != _SUPPORTED_RELEASE_VERSION:
            raise RuntimePinRegistryError(
                "runtime pin registry release_version is not the supported version"
            )
        _require_revision(self.map_revision, "sources[map].revision")
        _require_revision(self.pinvi_revision, "sources[pinvi].revision")
        _require_digest(self.pinset_sha256, "pinset_sha256")
        _require_timestamp(self.rotated_at, "rotated_at")
        _require_text(self.rotated_by, "rotated_by", max_length=_MAX_ACTOR_LENGTH)
        _require_text(self.reason, "reason", max_length=_MAX_REASON_LENGTH)
        if len(self.history) > _MAX_HISTORY_ENTRIES:
            raise RuntimePinRegistryError("runtime pin registry history is too long")
        if len(self.blocked_pinsets) > _MAX_BLOCKED_ENTRIES:
            raise RuntimePinRegistryError("runtime pin registry blocked_pinsets is too long")
        # digest는 파일에 기록하되 항상 재계산 대조한다. 부분 편집·truncation 방어.
        expected = self._expected_pinset_sha256()
        if self.pinset_sha256 != expected:
            raise RuntimePinRegistryError(
                "runtime pin registry pinset digest differs from the canonical recomputation"
            )

    def _expected_pinset_sha256(self) -> str:
        from kor_travel_docker_manager.services.pinned_runtime_release import (
            canonical_pinset_sha256,
            source_specs_for,
        )

        return canonical_pinset_sha256(
            version=self.release_version,
            sources=source_specs_for(
                map_revision=self.map_revision,
                pinvi_revision=self.pinvi_revision,
            ),
        )

    def release(self) -> Any:
        """registry 값으로 ``PinnedRuntimeRelease``를 구성한다."""

        from kor_travel_docker_manager.services.pinned_runtime_release import (
            PinnedRuntimeRelease,
            source_specs_for,
        )

        return PinnedRuntimeRelease(
            version=self.release_version,  # type: ignore[arg-type]
            sources=source_specs_for(
                map_revision=self.map_revision,
                pinvi_revision=self.pinvi_revision,
            ),
            pinset_sha256=self.pinset_sha256,
        )

    @property
    def effective_blocked_pinsets(self) -> tuple[BlockedPinset, ...]:
        """registry 목록과 코드 하한선의 합집합.

        ``to_payload``에는 넣지 않는다 — 하한선은 파일이 아니라 코드가 소유하므로
        파일에 기록하면 사람이 지울 수 있는 값이 되어 하한선이 아니게 된다.
        """

        declared = {
            (entry.pinset_sha256, entry.phase) for entry in self.blocked_pinsets
        }
        extra = tuple(
            entry
            for entry in _CODE_ENFORCED_BLOCKED_PINSETS
            if (entry.pinset_sha256, entry.phase) not in declared
        )
        return (*self.blocked_pinsets, *extra)

    def blocked_entry_for(
        self,
        *,
        pinset_sha256: str,
        map_source_revision: str,
        pinvi_source_revision: str,
        phase: str,
    ) -> BlockedPinset | None:
        """journal 식별자에 해당하는 차단 항목을 찾는다."""

        for entry in self.effective_blocked_pinsets:
            if entry.matches(
                pinset_sha256=pinset_sha256,
                map_source_revision=map_source_revision,
                pinvi_source_revision=pinvi_source_revision,
                phase=phase,
            ):
                return entry
        return None

    def is_blocked_pinset(self, pinset_sha256: str) -> bool:
        """pinset digest가 차단 목록에 있는지 확인한다(phase 무관).

        회전·rollback이 "차단된 곳으로 가지 않는다"를 판정할 때 쓴다 — 그 판단에는
        phase 조건이 의미가 없기 때문이다.
        """

        return any(
            entry.pinset_sha256 == pinset_sha256 for entry in self.effective_blocked_pinsets
        )

    def is_unconditionally_blocked_pinset(self, pinset_sha256: str) -> bool:
        """이 pinset의 **모든** 실행이 금지됐는지 확인한다.

        phase가 지정된 항목은 특정 journal 상태의 재개만 막는 것이므로 여기서
        제외한다 — 그 판정은 resume admission이 소유한다. rebuild 시작 게이트는
        조건 없는 차단만 근거로 삼아야 정확하다.
        """

        return any(
            entry.pinset_sha256 == pinset_sha256 and entry.phase is None
            for entry in self.effective_blocked_pinsets
        )

    @classmethod
    def from_payload(cls, payload: Any) -> RuntimePinRegistry:
        """registry JSON 문서를 strict parse한다."""

        if not isinstance(payload, dict):
            raise RuntimePinRegistryError("runtime pin registry document must be an object")
        unknown = set(payload) - {
            "schema",
            "release_version",
            "sources",
            "pinset_sha256",
            "rotated_at",
            "rotated_by",
            "reason",
            "history",
            "blocked_pinsets",
        }
        if unknown:
            raise RuntimePinRegistryError("runtime pin registry document has unknown fields")
        if payload.get("schema") != RUNTIME_PIN_REGISTRY_SCHEMA:
            raise RuntimePinRegistryError("runtime pin registry schema is not supported")

        release_version = payload.get("release_version")
        if not isinstance(release_version, int) or isinstance(release_version, bool):
            raise RuntimePinRegistryError("runtime pin registry release_version must be an integer")

        revisions = _parse_sources(payload.get("sources"), release_version=release_version)

        history_payload = payload.get("history", [])
        if not isinstance(history_payload, list):
            raise RuntimePinRegistryError("runtime pin registry history must be a list")
        blocked_payload = payload.get("blocked_pinsets", [])
        if not isinstance(blocked_payload, list):
            raise RuntimePinRegistryError("runtime pin registry blocked_pinsets must be a list")

        return cls(
            release_version=release_version,
            map_revision=revisions["map"],
            pinvi_revision=revisions["pinvi"],
            pinset_sha256=payload.get("pinset_sha256"),
            rotated_at=payload.get("rotated_at"),
            rotated_by=payload.get("rotated_by"),
            reason=payload.get("reason"),
            history=tuple(PinRotation.from_payload(entry) for entry in history_payload),
            blocked_pinsets=tuple(BlockedPinset.from_payload(entry) for entry in blocked_payload),
        )

    def to_payload(self) -> dict[str, Any]:
        """registry 파일과 공개 사본이 공유하는 wire shape."""

        from kor_travel_docker_manager.services.pinned_runtime_release import (
            CANONICAL_RUNTIME_SOURCE_URLS,
        )

        return {
            "schema": RUNTIME_PIN_REGISTRY_SCHEMA,
            "release_version": self.release_version,
            "sources": [
                {
                    "role": "map",
                    "url": CANONICAL_RUNTIME_SOURCE_URLS["map"],
                    "revision": self.map_revision,
                },
                {
                    "role": "pinvi",
                    "url": CANONICAL_RUNTIME_SOURCE_URLS["pinvi"],
                    "revision": self.pinvi_revision,
                },
            ],
            "pinset_sha256": self.pinset_sha256,
            "rotated_at": self.rotated_at,
            "rotated_by": self.rotated_by,
            "reason": self.reason,
            "history": [entry.to_payload() for entry in self.history],
            "blocked_pinsets": [entry.to_payload() for entry in self.blocked_pinsets],
        }


def _parse_sources(payload: Any, *, release_version: int) -> Mapping[str, str]:
    """sources 배열을 role 순서까지 고정해 파싱한다."""

    from kor_travel_docker_manager.services.pinned_runtime_release import (
        CANONICAL_RUNTIME_SOURCE_URLS,
        RUNTIME_SOURCE_ROLES,
    )

    del release_version
    if not isinstance(payload, list) or len(payload) != len(RUNTIME_SOURCE_ROLES):
        raise RuntimePinRegistryError(
            "runtime pin registry sources must list map then pinvi exactly once"
        )
    revisions: dict[str, str] = {}
    for expected_role, entry in zip(RUNTIME_SOURCE_ROLES, payload, strict=True):
        if not isinstance(entry, dict):
            raise RuntimePinRegistryError("runtime pin registry sources[] must be objects")
        if set(entry) != {"role", "url", "revision"}:
            raise RuntimePinRegistryError(
                "runtime pin registry sources[] must declare role, url and revision"
            )
        if entry["role"] != expected_role:
            raise RuntimePinRegistryError(
                "runtime pin registry sources must list map then pinvi exactly once"
            )
        # URL이 파일에 있어도 canonical 집합과 다르면 거부한다 — 파일 편집으로
        # 임의 저장소를 가리키게 만드는 경로를 코드가 계속 막는다.
        if entry["url"] != CANONICAL_RUNTIME_SOURCE_URLS[expected_role]:
            raise RuntimePinRegistryError("runtime pin registry source URL is not canonical")
        revisions[expected_role] = _require_revision(
            entry["revision"], f"sources[{expected_role}].revision"
        )
    return revisions


def _project_root() -> Path:
    # trusted release의 root launcher는 wheel 안의 Python을 ``-I``로 직접 실행한다.
    # 그때 entrypoint가 주입하는 project-root env가 없더라도 sys.prefix는 설치 venv를
    # 그대로 보존한다. package 부모를 네 번 거슬러 올리는 개발 checkout 규칙을 먼저
    # 적용하면 ``.../.venv/lib/config``이라는 존재하지 않는 경로가 되어 one-shot이
    # ledger claim 전 import 단계에서 끝난다.
    if Path(sys.prefix) == _TRUSTED_INSTALL_ROOT / "backend" / ".venv":
        return _TRUSTED_INSTALL_ROOT

    from kor_travel_docker_manager.services.registry import get_project_root

    return Path(get_project_root())


def _running_from_trusted_install_root() -> bool:
    """trusted installer가 통째 교체하는 canonical execution root에서 도는가."""

    project_root = _project_root()
    try:
        return project_root.resolve() == _TRUSTED_INSTALL_ROOT.resolve()
    except OSError:
        return False


def runtime_pin_registry_path() -> Path:
    """registry 파일 경로.

    **운영 기본값은 배포 트리 밖이다.** trusted installer는 canonical execution root를
    staging→commit으로 통째 교체하므로 registry가 트리 안에 있으면 다음 release 설치가
    회전 결과를 조용히 되돌린다 — 그 조용한 되돌림은 이 전환이 없애려던 실패 그
    자체다. 설치 root에서 도는 경우 env가 없어도 ``/var/lib/...`` 상태 디렉터리를
    기본값으로 쓰고, 저장소 안의 ``config/runtime-pins.json``은 개발 기본값이자
    ``pin init``의 seed로만 남는다. ``KTDM_RUNTIME_PINS_FILE``로 언제든 덮어쓸 수 있다.
    """

    configured = os.environ.get(RUNTIME_PINS_FILE_ENV, "").strip()
    if configured:
        return Path(configured)
    if _running_from_trusted_install_root():
        return _TRUSTED_STATE_ROOT / "runtime-pins.json"
    return _project_root().joinpath(*_DEFAULT_REGISTRY_RELPATH)


def runtime_pin_registry_public_path() -> Path:
    """backend가 읽는 secret-free 공개 사본 경로.

    registry 본체는 root 0600이라 비-root backend가 읽지 못한다. root가 실행하는
    rotate/init이 공개 사본을 함께 갱신한다 — trusted installer가
    ``.ktdm-release-manifest.json``을 0644로 남기는 선례와 같은 패턴이다. 다만 사본도
    registry와 같은 이유로 배포 트리 밖에 두어야 release 설치에 지워지지 않는다.
    """

    configured = os.environ.get(RUNTIME_PINS_PUBLIC_FILE_ENV, "").strip()
    if configured:
        return Path(configured)
    if _running_from_trusted_install_root():
        return _TRUSTED_PUBLIC_ROOT / "runtime-pins.json"
    return _project_root() / _DEFAULT_PUBLIC_BASENAME


_CACHE: dict[str, tuple[tuple[int, int, int], RuntimePinRegistry]] = {}


def clear_runtime_pin_registry_cache() -> None:
    """테스트와 파일 교체 직후에 캐시를 비운다."""

    _CACHE.clear()


def _effective_uid() -> int | None:
    """POSIX가 아니면 ``None``. 소유자 개념이 없는 플랫폼에서 AttributeError로 죽지 않는다."""

    geteuid = getattr(os, "geteuid", None)
    return geteuid() if geteuid is not None else None


def _insecure_mode_allowed() -> bool:
    """개발 환경에서만 mode 검사를 완화한다. 소유자 검사는 완화하지 않는다."""

    if _effective_uid() == 0:
        return False
    return os.environ.get(RUNTIME_PINS_ALLOW_INSECURE_MODE_ENV, "").strip() == "1"


def _assert_registry_file_integrity(path: Path) -> None:
    """읽기 시점에 파일 자체를 검증한다.

    "값은 파일, 신뢰는 소유권"이 이 전환의 안전 논거인데, 그 소유권을 실제로 보는
    코드가 없으면 논거가 성립하지 않는다. 같은 저장소의 root 아티팩트 표준
    (``map_application_300._require_artifact_directory``)과 같은 기준을 쓴다.

    - ``lstat``으로 본다. symlink를 따라가 다른 파일을 읽지 않는다.
    - 일반 파일이어야 한다.
    - 소유자는 root이거나 이 프로세스 자신이어야 한다.
    - group·other 쓰기 권한이 있으면 거부한다. 0600(운영)과 0644(개발 체크아웃)는
      통과하고 0664·0666은 거부된다. 모든 파일을 0777로 보고하는 공유 마운트에서만
      ``KTDM_RUNTIME_PINS_ALLOW_INSECURE_MODE=1``로 이 항목을 완화할 수 있고, 그
      완화는 root에서 무효다.
    - stat 실패는 통과가 아니라 거부다.
    """

    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise RuntimePinRegistryError(
            f"runtime pin registry file is missing: {path.name} (bootstrap it with "
            "'ktdctl pin init --confirm')"
        ) from exc
    except OSError as exc:
        raise RuntimePinRegistryError(
            f"runtime pin registry file cannot be inspected: {path.name}"
        ) from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimePinRegistryError(
            f"runtime pin registry path is not a regular file: {path.name}"
        )
    euid = _effective_uid()
    if euid is not None and file_stat.st_uid not in {0, euid}:
        raise RuntimePinRegistryError(
            f"runtime pin registry file is owned by an unexpected user: {path.name}"
        )
    if stat.S_IMODE(file_stat.st_mode) & 0o022 and not _insecure_mode_allowed():
        raise RuntimePinRegistryError(
            f"runtime pin registry file must not be group or world writable: {path.name}"
        )


def _read_registry_document(path: Path) -> dict[str, Any]:
    _assert_registry_file_integrity(path)
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_REGISTRY_BYTES + 1)
    except FileNotFoundError as exc:
        raise RuntimePinRegistryError(
            f"runtime pin registry file is missing: {path.name} "
            "(bootstrap it with 'ktdctl pin init --confirm')"
        ) from exc
    except OSError as exc:
        raise RuntimePinRegistryError(
            f"runtime pin registry file cannot be read: {path.name}"
        ) from exc
    if len(raw) > _MAX_REGISTRY_BYTES:
        raise RuntimePinRegistryError("runtime pin registry file is too large")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimePinRegistryError("runtime pin registry file is not valid JSON") from exc
    if not isinstance(document, dict):
        raise RuntimePinRegistryError("runtime pin registry document must be an object")
    return document


def load_runtime_pin_registry(*, path: Path | None = None) -> RuntimePinRegistry:
    """registry를 로드한다. 부재·파싱 실패·digest 불일치는 전부 fail-close다.

    ``lru_cache``를 쓰지 않는다 — 그 선례는 불변 파일 전제이고, registry는 rotate로
    바뀐다. mtime·inode·size가 그대로일 때만 캐시를 재사용하므로 ``pin rotate``는
    실행 중 Manager에 재기동 없이 즉시 반영된다.
    """

    registry_path = path or runtime_pin_registry_path()
    key = str(registry_path)
    try:
        file_stat = registry_path.stat()
        stamp = (file_stat.st_mtime_ns, file_stat.st_size, file_stat.st_ino)
    except FileNotFoundError as exc:
        _CACHE.pop(key, None)
        raise RuntimePinRegistryError(
            f"runtime pin registry file is missing: {registry_path.name} "
            "(bootstrap it with 'ktdctl pin init --confirm')"
        ) from exc
    except OSError as exc:
        _CACHE.pop(key, None)
        raise RuntimePinRegistryError(
            f"runtime pin registry file cannot be read: {registry_path.name}"
        ) from exc

    cached = _CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        # 캐시 적중이어도 파일 무결성은 매번 확인한다 — 내용이 그대로여도 소유권이나
        # 권한이 바뀌었을 수 있고, 그 상태로 파괴적 작업을 진행해서는 안 된다.
        _assert_registry_file_integrity(registry_path)
        return cached[1]

    registry = RuntimePinRegistry.from_payload(_read_registry_document(registry_path))
    _CACHE[key] = (stamp, registry)
    return registry


def _atomic_write_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    mode: int,
    directory_mode: int | None = None,
) -> None:
    """같은 디렉터리의 임시 파일에 쓰고 fsync 뒤 원자 교체한다."""

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if directory_mode is not None:
        # 파일 mode만 맞아도 부모가 traverse 불가면 읽는 쪽은 lstat조차 못 한다.
        try:
            os.chmod(parent, directory_mode)
        except OSError:
            pass
    body = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=False) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    try:
        directory_fd = os.open(str(parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def publish_runtime_pins(
    registry: RuntimePinRegistry,
    *,
    public_path: Path | None = None,
) -> Path:
    """backend가 읽을 수 있는 secret-free 공개 사본을 갱신한다.

    내용은 공개 저장소의 commit SHA와 회전 메타뿐이라 구조적으로 비밀이 없다. 다만
    ``reason``·``rotated_by``는 운영자 자유 입력이므로 **그대로 world-readable 사본과
    인증된 API 응답에 실린다** — 비밀을 사유에 적지 않는다는 규약이 필요하다.
    사본에도 ``pinset_sha256``이 있어 읽는 쪽이 재계산 대조할 수 있다.
    """

    target = public_path or runtime_pin_registry_public_path()
    payload = dict(registry.to_payload())
    payload["published_at"] = utc_timestamp()
    _atomic_write_json(target, payload, mode=0o644, directory_mode=0o755)
    return target


def read_published_runtime_pins() -> dict[str, Any]:
    """backend용 읽기 경로. 값을 추측하지 않는 것이 이 함수의 계약이다.

    - 공개 사본을 읽었으면 ``ok``.
    - 사본이 없어 registry 본체를 대신 읽었으면 ``degraded`` — 그 파일이 이 호스트의
      운영 registry가 아니라 배포본에 딸려 온 개발 seed일 수 있기 때문이다. 값을
      보여주되 권위 있는 값이라고 말하지 않는다.
    - 둘 다 읽을 수 없으면 ``unknown``.
    """

    public_path = runtime_pin_registry_public_path()
    for source, candidate in (
        ("published_copy", public_path),
        ("registry", runtime_pin_registry_path()),
    ):
        try:
            document = _read_registry_document(candidate)
        except RuntimePinRegistryError:
            continue
        published_at = document.pop("published_at", None)
        try:
            registry = RuntimePinRegistry.from_payload(document)
        except RuntimePinRegistryError as exc:
            return {
                "status": "unknown",
                "source": source,
                "detail": str(exc),
            }
        payload = registry.to_payload()
        payload["blocked_pinsets"] = [
            entry.to_payload() for entry in registry.effective_blocked_pinsets
        ]
        if source == "published_copy":
            payload["status"] = "ok"
            # 판별할 수 있으면 판별한다. registry를 읽을 수 있는 프로세스가 stale
            # 사본을 "정상"이라고 보고하면, 회전 뒤 UI가 옛 pin을 자신 있게 보여준다.
            authoritative = _authoritative_pinset_or_none()
            if authoritative is not None and authoritative != registry.pinset_sha256:
                payload["status"] = "stale"
                payload["detail"] = (
                    "공개 사본이 registry보다 오래됐습니다. root로 "
                    "'ktdctl pin verify'를 실행해 사본을 갱신하세요."
                )
        else:
            payload["status"] = "degraded"
            payload["detail"] = (
                "공개 사본이 없어 registry 파일을 직접 읽었습니다. 이 값은 이 호스트의 "
                "운영 pin이 아니라 배포본 기본값일 수 있습니다 — root로 "
                "'ktdctl pin verify'를 실행해 공개 사본을 확인하세요."
            )
        payload["source"] = source
        if published_at is not None:
            payload["published_at"] = published_at
        return payload
    return {
        "status": "unknown",
        "source": None,
        "detail": "runtime pin registry is not readable by this process",
    }


def _authoritative_pinset_or_none() -> str | None:
    """registry 본체를 읽을 수 있으면 그 pinset을, 아니면 ``None``을 준다."""

    try:
        return load_runtime_pin_registry().pinset_sha256
    except RuntimePinRegistryError:
        return None


def _preserved_copy_path(registry_path: Path, pinset_sha256: str) -> Path:
    return registry_path.with_name(f"{registry_path.stem}.{pinset_sha256}.json")


def packaged_seed_path() -> Path:
    """저장소에 추적된 읽기 전용 seed 경로(``pin init``의 기본 입력)."""

    return _project_root().joinpath(*_SEED_RELPATH)


def _is_packaged_seed_path(path: Path) -> bool:
    try:
        return path.resolve() == packaged_seed_path().resolve()
    except OSError:
        return False


def _assert_registry_is_writable_target(path: Path) -> None:
    """회전 대상 registry가 운영 규약을 만족하는지 mutation 이전에 확인한다.

    두 가지를 막는다.

    1. **설치 트리 안에서의 회전.** trusted installer는 canonical execution root를
       staging→commit으로 통째 교체하므로, 배포 트리 안의 registry에 회전하면 다음
       release 설치가 그 결과를 조용히 되돌린다. 이 조용한 되돌림은 registry 전환이
       없애려던 실패 모드 그 자체라 fail-close한다.
    2. **그룹·타인 접근 가능한 운영 registry.** 회전 권한이 root 밖으로 새는 상태다.
       저장소 안의 개발 기본값은 git이 관리하는 world-readable 파일이므로 예외다.
    """

    try:
        # 양쪽 다 resolve한다 — 설치 root가 symlink면 한쪽만 해석해서는 가드를 지나친다.
        inside_trusted_root = path.resolve().is_relative_to(_TRUSTED_INSTALL_ROOT.resolve())
    except (OSError, ValueError):
        inside_trusted_root = False
    if inside_trusted_root:
        raise RuntimePinRegistryError(
            "runtime pin registry must not live inside the trusted install root "
            f"({_TRUSTED_INSTALL_ROOT}); the next release install would revert the "
            f"rotation. Point {RUNTIME_PINS_FILE_ENV} outside the deploy tree and "
            f"bootstrap it with 'ktdctl pin init --seed {_TRUSTED_INSTALL_ROOT}/config/"
            f"{_SEED_BASENAME} --confirm'"
        )
    if _is_packaged_seed_path(path):
        raise RuntimePinRegistryError(
            f"the packaged seed ({_SEED_BASENAME}) is read-only bootstrap input and is "
            "never a rotation target; bootstrap a registry with 'ktdctl pin init' first"
        )
    if path.exists():
        _assert_registry_file_integrity(path)


def write_runtime_pin_registry(
    registry: RuntimePinRegistry,
    *,
    path: Path | None = None,
    publish: bool = True,
    preserve_previous: bool = True,
) -> Path:
    """registry를 원자적으로 교체하고 이전 상태를 digest 이름으로 보존한다."""

    registry_path = path or runtime_pin_registry_path()
    # 모든 쓰기 경로가 같은 가드를 지나게 한다. rotate/block/rollback은 계산 전에 미리
    # 부르지만, ``pin init``은 여기로 바로 들어오므로 여기서도 확인해야 설치 트리 안이나
    # 읽기 전용 seed로 부트스트랩하는 사고를 막을 수 있다.
    _assert_registry_is_writable_target(registry_path)
    if preserve_previous and registry_path.exists():
        try:
            previous = load_runtime_pin_registry(path=registry_path)
        except RuntimePinRegistryError:
            previous = None
        if previous is not None and previous.pinset_sha256 != registry.pinset_sha256:
            _atomic_write_json(
                _preserved_copy_path(registry_path, previous.pinset_sha256),
                previous.to_payload(),
                mode=0o600,
            )
    _atomic_write_json(registry_path, registry.to_payload(), mode=0o600)
    clear_runtime_pin_registry_cache()
    if publish:
        try:
            publish_runtime_pins(registry)
        except OSError as exc:
            # registry 교체는 이미 확정됐다. 실패를 그대로 던지면 운영자가 "회전이
            # 실패했다"고 읽는다 — 실제 상태를 정확히 말한다.
            raise RuntimePinRegistryError(
                "the rotation was applied but the published copy could not be updated; "
                "the read-only API will report a stale or unknown value until a root "
                "'ktdctl pin verify' refreshes it"
            ) from exc
    return registry_path


def build_registry(
    *,
    release_version: int,
    map_revision: str,
    pinvi_revision: str,
    rotated_by: str,
    reason: str,
    rotated_at: str | None = None,
    history: Sequence[PinRotation] = (),
    blocked_pinsets: Sequence[BlockedPinset] = (),
) -> RuntimePinRegistry:
    """digest를 자동 계산해 registry 값을 만든다 — 사람이 digest를 손으로 쓰지 않는다."""

    map_revision = _require_revision(map_revision, "sources[map].revision")
    pinvi_revision = _require_revision(pinvi_revision, "sources[pinvi].revision")
    return RuntimePinRegistry(
        release_version=release_version,
        map_revision=map_revision,
        pinvi_revision=pinvi_revision,
        pinset_sha256=_compute_pinset_sha256(
            release_version=release_version,
            map_revision=map_revision,
            pinvi_revision=pinvi_revision,
        ),
        rotated_at=rotated_at or utc_timestamp(),
        rotated_by=rotated_by,
        reason=reason,
        history=tuple(history),
        blocked_pinsets=tuple(blocked_pinsets),
    )


def _apply_runtime_pin_rotation(
    *,
    current: RuntimePinRegistry,
    registry_path: Path,
    map_revision: str,
    pinvi_revision: str,
    reason: str,
    rotated_by: str,
    block_previous: bool,
    block_reason: str | None,
) -> RuntimePinRegistry:
    """검증된 두 revision을 원자적으로 교체하고 lifecycle 이력을 남긴다."""

    updated = build_runtime_pin_pair_rotation(
        current=current,
        map_revision=map_revision,
        pinvi_revision=pinvi_revision,
        reason=reason,
        rotated_by=rotated_by,
        block_previous=block_previous,
        block_reason=block_reason,
    )
    write_runtime_pin_registry(updated, path=registry_path)
    return updated


def build_runtime_pin_pair_rotation(
    *,
    current: RuntimePinRegistry,
    map_revision: str,
    pinvi_revision: str,
    reason: str,
    rotated_by: str,
    block_previous: bool,
    block_reason: str | None = None,
) -> RuntimePinRegistry:
    """pair 회전 결과를 **기록하지 않고** 준비한다.

    source registry와 Manager-aware execution registry를 함께 바꿔야 하는 일반
    runtime에서는 두 문서를 모두 검증한 뒤 durable transaction intent를 먼저 남긴다.
    그 준비 단계가 v5만 먼저 영구 반영하는 일을 막으려면, v5 회전 계산도 writer와
    분리되어 있어야 한다.
    """

    map_revision = _require_revision(map_revision, "map revision")
    pinvi_revision = _require_revision(pinvi_revision, "pinvi revision")
    if (map_revision, pinvi_revision) == (current.map_revision, current.pinvi_revision):
        raise RuntimePinRegistryError("runtime pin rotation would not change any revision")

    rotated_at = utc_timestamp()
    blocked = list(current.blocked_pinsets)
    # phase 한정 항목은 terminal이 아니다. 여기서 phase 무관 술어를 쓰면 그런
    # pinset에 --block-previous를 걸어도 아무것도 등재되지 않고 exit 0으로 성공을
    # 보고한다 — 이후 모든 시작 게이트가 그 pinset을 실행 가능하다고 판정한다.
    if block_previous and not current.is_unconditionally_blocked_pinset(
        current.pinset_sha256
    ):
        blocked.append(
            BlockedPinset(
                pinset_sha256=current.pinset_sha256,
                map_revision=current.map_revision,
                pinvi_revision=current.pinvi_revision,
                reason=_require_text(
                    block_reason or reason, "block reason", max_length=_MAX_REASON_LENGTH
                ),
                blocked_at=rotated_at,
            )
        )

    next_pinset = _compute_pinset_sha256(
        release_version=current.release_version,
        map_revision=map_revision,
        pinvi_revision=pinvi_revision,
    )
    # declared 목록뿐 아니라 코드 하한선까지 본다 — registry에서 d9을 지운 상태에서
    # 그 pinset으로 회전하면 회전은 성공하지만 resume이 영구 불가한 곳에 착지한다.
    if current.is_blocked_pinset(next_pinset) or any(
        entry.pinset_sha256 == next_pinset for entry in blocked
    ):
        raise RuntimePinRegistryError(
            "runtime pin rotation targets a pinset that is permanently blocked"
        )

    history = (
        *current.history,
        PinRotation(
            pinset_sha256=next_pinset,
            rotated_at=rotated_at,
            rotated_by=rotated_by,
            reason=reason,
            supersedes_pinset_sha256=current.pinset_sha256,
        ),
    )[-_MAX_HISTORY_ENTRIES:]

    updated = build_registry(
        release_version=current.release_version,
        map_revision=map_revision,
        pinvi_revision=pinvi_revision,
        rotated_by=rotated_by,
        reason=reason,
        rotated_at=rotated_at,
        history=history,
        blocked_pinsets=tuple(blocked),
    )
    return updated


def rotate_runtime_pin(
    *,
    role: str,
    revision: str,
    reason: str,
    rotated_by: str,
    path: Path | None = None,
    block_previous: bool = False,
    block_reason: str | None = None,
) -> RuntimePinRegistry:
    """한 role의 revision을 교체한다.

    현재 pinset이 terminal이면 두 source의 compatibility를 한 번에 바꿔야 한다. 그
    상태에서 role별 회전을 열어 두면 M05가 pair-incomplete pinset을 one-shot ledger에
    먼저 소비할 수 있으므로 ``rotate_runtime_pin_pair``만 허용한다.
    """

    from kor_travel_docker_manager.services.pinned_runtime_release import RUNTIME_SOURCE_ROLES

    if role not in RUNTIME_SOURCE_ROLES:
        raise RuntimePinRegistryError("runtime pin role must be map or pinvi")
    registry_path = path or runtime_pin_registry_path()
    _assert_registry_is_writable_target(registry_path)
    current = load_runtime_pin_registry(path=registry_path)
    if current.is_unconditionally_blocked_pinset(current.pinset_sha256):
        raise RuntimePinRegistryError(
            "a terminal current pinset requires atomic Map/PinVi pair rotation"
        )
    revision = _require_revision(revision, "revision")
    return _apply_runtime_pin_rotation(
        current=current,
        registry_path=registry_path,
        map_revision=revision if role == "map" else current.map_revision,
        pinvi_revision=revision if role == "pinvi" else current.pinvi_revision,
        reason=reason,
        rotated_by=rotated_by,
        block_previous=block_previous,
        block_reason=block_reason,
    )


def rotate_runtime_pin_pair(
    *,
    map_revision: str,
    pinvi_revision: str,
    reason: str,
    rotated_by: str,
    path: Path | None = None,
    block_previous: bool = False,
    block_reason: str | None = None,
) -> RuntimePinRegistry:
    """Map·PinVi revision을 하나의 registry replace로 원자 회전한다."""

    registry_path = path or runtime_pin_registry_path()
    _assert_registry_is_writable_target(registry_path)
    current = load_runtime_pin_registry(path=registry_path)
    return _apply_runtime_pin_rotation(
        current=current,
        registry_path=registry_path,
        map_revision=map_revision,
        pinvi_revision=pinvi_revision,
        reason=reason,
        rotated_by=rotated_by,
        block_previous=block_previous,
        block_reason=block_reason,
    )


def block_runtime_pinset(
    *,
    pinset_sha256: str,
    reason: str,
    map_revision: str | None = None,
    pinvi_revision: str | None = None,
    phase: str | None = None,
    path: Path | None = None,
) -> RuntimePinRegistry:
    """terminal 판정된 pinset을 영구 차단 목록에 등재한다."""

    registry_path = path or runtime_pin_registry_path()
    _assert_registry_is_writable_target(registry_path)
    current = load_runtime_pin_registry(path=registry_path)
    pinset_sha256 = _require_digest(pinset_sha256, "pinset_sha256")
    if pinset_sha256 == current.pinset_sha256:
        map_revision = map_revision or current.map_revision
        pinvi_revision = pinvi_revision or current.pinvi_revision
    if map_revision is None or pinvi_revision is None:
        raise RuntimePinRegistryError(
            "blocking a pinset other than the current one requires both revisions"
        )
    # phase-scoped journal entries only block the corresponding resume phase. A
    # later terminal verdict must add an unconditional entry rather than treat
    # the scoped record as an idempotent terminal block.
    if current.is_unconditionally_blocked_pinset(pinset_sha256):
        return current
    entry = BlockedPinset(
        pinset_sha256=pinset_sha256,
        map_revision=map_revision,
        pinvi_revision=pinvi_revision,
        reason=reason,
        blocked_at=utc_timestamp(),
        phase=phase,
    )
    updated = RuntimePinRegistry(
        release_version=current.release_version,
        map_revision=current.map_revision,
        pinvi_revision=current.pinvi_revision,
        pinset_sha256=current.pinset_sha256,
        rotated_at=current.rotated_at,
        rotated_by=current.rotated_by,
        reason=current.reason,
        history=current.history,
        # 차단 목록은 truncate하지 않는다 — 가장 오래된 terminal pinset이 조용히
        # 빠지면 그 candidate가 다시 실행 가능해진다. 초과는 fail-close다.
        blocked_pinsets=(*current.blocked_pinsets, entry),
    )
    write_runtime_pin_registry(updated, path=registry_path, preserve_previous=False)
    return updated


def rollback_runtime_pin(
    *,
    pinset_sha256: str,
    rotated_by: str,
    reason: str,
    path: Path | None = None,
) -> RuntimePinRegistry:
    """보존된 이전 registry로 원복한다.

    차단(terminal) pinset으로의 원복은 거부한다 — Map·PinVi 저장소가 문서 규율로
    지켜 온 "terminal candidate는 재시도하지 않는다"를 코드가 깨뜨리지 않기 위해서다.
    """

    registry_path = path or runtime_pin_registry_path()
    _assert_registry_is_writable_target(registry_path)
    current = load_runtime_pin_registry(path=registry_path)
    pinset_sha256 = _require_digest(pinset_sha256, "pinset_sha256")
    if pinset_sha256 == current.pinset_sha256:
        raise RuntimePinRegistryError("runtime pin registry already uses this pinset")
    if current.is_blocked_pinset(pinset_sha256):
        raise RuntimePinRegistryError("runtime pin rollback targets a permanently blocked pinset")
    preserved_path = _preserved_copy_path(registry_path, pinset_sha256)
    preserved = RuntimePinRegistry.from_payload(_read_registry_document(preserved_path))
    if preserved.pinset_sha256 != pinset_sha256:
        raise RuntimePinRegistryError("preserved runtime pin registry digest differs")

    rotated_at = utc_timestamp()
    history = (
        *current.history,
        PinRotation(
            pinset_sha256=pinset_sha256,
            rotated_at=rotated_at,
            rotated_by=rotated_by,
            reason=reason,
            supersedes_pinset_sha256=current.pinset_sha256,
        ),
    )[-_MAX_HISTORY_ENTRIES:]
    updated = build_registry(
        release_version=preserved.release_version,
        map_revision=preserved.map_revision,
        pinvi_revision=preserved.pinvi_revision,
        rotated_by=rotated_by,
        reason=reason,
        rotated_at=rotated_at,
        history=history,
        blocked_pinsets=current.blocked_pinsets,
    )
    write_runtime_pin_registry(updated, path=registry_path)
    return updated


def verify_runtime_pin_registry(*, path: Path | None = None) -> dict[str, Any]:
    """digest 재계산·canonical URL·공개 사본 정합을 점검한다. 읽기 전용."""

    registry_path = path or runtime_pin_registry_path()
    registry = load_runtime_pin_registry(path=registry_path)
    public_path = runtime_pin_registry_public_path()
    published_state = "missing"
    try:
        published_document = _read_registry_document(public_path)
    except RuntimePinRegistryError:
        published_document = None
    if published_document is not None:
        published_document.pop("published_at", None)
        try:
            published = RuntimePinRegistry.from_payload(published_document)
        except RuntimePinRegistryError:
            published_state = "malformed"
        else:
            published_state = (
                "current" if published.pinset_sha256 == registry.pinset_sha256 else "stale"
            )
    return {
        "registry_path_name": registry_path.name,
        "pinset_sha256": registry.pinset_sha256,
        "map_revision": registry.map_revision,
        "pinvi_revision": registry.pinvi_revision,
        "digest_recomputation": "ok",
        "published_copy": published_state,
        # digest가 맞아도 현재 pinset이 terminal이면 rebuild는 거부된다. verify가 그
        # 사실을 말하지 않으면 운영자가 "ok"를 보고 안심한 뒤 rebuild에서 막힌다.
        "current_pinset_is_blocked": registry.is_unconditionally_blocked_pinset(
            registry.pinset_sha256
        ),
        "blocked_pinset_count": len(registry.effective_blocked_pinsets),
        "history_length": len(registry.history),
    }
