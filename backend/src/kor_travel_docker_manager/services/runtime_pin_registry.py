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

_DEFAULT_REGISTRY_RELPATH: Final = ("config", "runtime-pins.json")
_DEFAULT_PUBLIC_BASENAME: Final = ".ktdm-runtime-pins.json"

_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")

_MAX_REGISTRY_BYTES: Final = 512 * 1024
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

    def blocked_entry_for(
        self,
        *,
        pinset_sha256: str,
        map_source_revision: str,
        pinvi_source_revision: str,
        phase: str,
    ) -> BlockedPinset | None:
        """journal 식별자에 해당하는 차단 항목을 찾는다."""

        for entry in self.blocked_pinsets:
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

        return any(entry.pinset_sha256 == pinset_sha256 for entry in self.blocked_pinsets)

    def is_unconditionally_blocked_pinset(self, pinset_sha256: str) -> bool:
        """이 pinset의 **모든** 실행이 금지됐는지 확인한다.

        phase가 지정된 항목은 특정 journal 상태의 재개만 막는 것이므로 여기서
        제외한다 — 그 판정은 resume admission이 소유한다. rebuild 시작 게이트는
        조건 없는 차단만 근거로 삼아야 정확하다.
        """

        return any(
            entry.pinset_sha256 == pinset_sha256 and entry.phase is None
            for entry in self.blocked_pinsets
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
    from kor_travel_docker_manager.services.registry import get_project_root

    return Path(get_project_root())


def runtime_pin_registry_path() -> Path:
    """registry 파일 경로. prod는 배포 트리 밖을 env로 지정한다.

    배포 트리 안에 두면 trusted installer의 staging→commit 트리 교체가 회전 결과를
    덮어 registry의 존재 이유가 무너진다. 개발 기본값만 저장소 안의
    ``config/runtime-pins.json``이다.
    """

    configured = os.environ.get(RUNTIME_PINS_FILE_ENV, "").strip()
    if configured:
        return Path(configured)
    return _project_root().joinpath(*_DEFAULT_REGISTRY_RELPATH)


def runtime_pin_registry_public_path() -> Path:
    """backend가 읽는 secret-free 공개 사본 경로.

    registry 본체는 root 0600이라 비-root backend가 읽지 못한다. root가 실행하는
    rotate/init이 공개 사본을 함께 갱신한다 — trusted installer가
    ``.ktdm-release-manifest.json``을 0644로 남기는 선례와 같은 패턴이다.
    """

    configured = os.environ.get(RUNTIME_PINS_PUBLIC_FILE_ENV, "").strip()
    if configured:
        return Path(configured)
    return _project_root() / _DEFAULT_PUBLIC_BASENAME


_CACHE: dict[str, tuple[tuple[int, int, int], RuntimePinRegistry]] = {}


def clear_runtime_pin_registry_cache() -> None:
    """테스트와 파일 교체 직후에 캐시를 비운다."""

    _CACHE.clear()


def _read_registry_document(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_REGISTRY_BYTES + 1)
    except FileNotFoundError as exc:
        raise RuntimePinRegistryError(
            f"runtime pin registry file is missing: {path.name} "
            "(run 'ktdctl pin init --confirm' to bootstrap it)"
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
            "(run 'ktdctl pin init --confirm' to bootstrap it)"
        ) from exc
    except OSError as exc:
        _CACHE.pop(key, None)
        raise RuntimePinRegistryError(
            f"runtime pin registry file cannot be read: {registry_path.name}"
        ) from exc

    cached = _CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]

    registry = RuntimePinRegistry.from_payload(_read_registry_document(registry_path))
    _CACHE[key] = (stamp, registry)
    return registry


def _atomic_write_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    mode: int,
) -> None:
    """같은 디렉터리의 임시 파일에 쓰고 fsync 뒤 원자 교체한다."""

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
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

    내용은 전부 공개 저장소의 commit SHA와 회전 메타뿐이라 비밀이 없다. 사본에도
    ``pinset_sha256``이 있어 읽는 쪽이 재계산 대조할 수 있다.
    """

    target = public_path or runtime_pin_registry_public_path()
    payload = dict(registry.to_payload())
    payload["published_at"] = utc_timestamp()
    _atomic_write_json(target, payload, mode=0o644)
    return target


def read_published_runtime_pins() -> dict[str, Any]:
    """backend용 읽기 경로. 사본이 없으면 registry 본체를 읽어보고, 둘 다 실패하면
    ``unknown``으로 정직하게 표시한다(추측하지 않는다).
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
        payload["status"] = "ok"
        payload["source"] = source
        if published_at is not None:
            payload["published_at"] = published_at
        return payload
    return {
        "status": "unknown",
        "source": None,
        "detail": "runtime pin registry is not readable by this process",
    }


def _preserved_copy_path(registry_path: Path, pinset_sha256: str) -> Path:
    return registry_path.with_name(f"{registry_path.stem}.{pinset_sha256}.json")


def _assert_registry_is_owner_only(path: Path) -> None:
    """운영 registry는 소유자 전용이어야 한다(개발 기본 경로는 예외)."""

    if path == _project_root().joinpath(*_DEFAULT_REGISTRY_RELPATH):
        return
    try:
        file_stat = path.stat()
    except OSError:
        return
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise RuntimePinRegistryError(
            "runtime pin registry file must not be group or world accessible"
        )


def write_runtime_pin_registry(
    registry: RuntimePinRegistry,
    *,
    path: Path | None = None,
    publish: bool = True,
    preserve_previous: bool = True,
) -> Path:
    """registry를 원자적으로 교체하고 이전 상태를 digest 이름으로 보존한다."""

    registry_path = path or runtime_pin_registry_path()
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
        publish_runtime_pins(registry)
    return registry_path


def _compute_pinset_sha256(*, release_version: int, map_revision: str, pinvi_revision: str) -> str:
    from kor_travel_docker_manager.services.pinned_runtime_release import (
        canonical_pinset_sha256,
        source_specs_for,
    )

    return canonical_pinset_sha256(
        version=release_version,
        sources=source_specs_for(map_revision=map_revision, pinvi_revision=pinvi_revision),
    )


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
    """한 role의 revision을 교체하고 digest·이력·직전 pinset 차단을 자동 처리한다."""

    from kor_travel_docker_manager.services.pinned_runtime_release import RUNTIME_SOURCE_ROLES

    if role not in RUNTIME_SOURCE_ROLES:
        raise RuntimePinRegistryError("runtime pin role must be map or pinvi")
    registry_path = path or runtime_pin_registry_path()
    _assert_registry_is_owner_only(registry_path)
    current = load_runtime_pin_registry(path=registry_path)
    revision = _require_revision(revision, "revision")
    map_revision = revision if role == "map" else current.map_revision
    pinvi_revision = revision if role == "pinvi" else current.pinvi_revision
    if (map_revision, pinvi_revision) == (current.map_revision, current.pinvi_revision):
        raise RuntimePinRegistryError("runtime pin rotation would not change any revision")

    rotated_at = utc_timestamp()
    blocked = list(current.blocked_pinsets)
    if block_previous and not current.is_blocked_pinset(current.pinset_sha256):
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
    for entry in blocked:
        if entry.pinset_sha256 == next_pinset:
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
    write_runtime_pin_registry(updated, path=registry_path)
    return updated


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
    _assert_registry_is_owner_only(registry_path)
    current = load_runtime_pin_registry(path=registry_path)
    pinset_sha256 = _require_digest(pinset_sha256, "pinset_sha256")
    if pinset_sha256 == current.pinset_sha256:
        map_revision = map_revision or current.map_revision
        pinvi_revision = pinvi_revision or current.pinvi_revision
    if map_revision is None or pinvi_revision is None:
        raise RuntimePinRegistryError(
            "blocking a pinset other than the current one requires both revisions"
        )
    if current.is_blocked_pinset(pinset_sha256):
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
        blocked_pinsets=(*current.blocked_pinsets, entry)[-_MAX_BLOCKED_ENTRIES:],
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
    _assert_registry_is_owner_only(registry_path)
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
        "blocked_pinset_count": len(registry.blocked_pinsets),
        "history_length": len(registry.history),
    }
