"""Manager-aware runtime execution registry.

v5 runtime-pins registry는 Map·PinVi source materialization의 역사적 authority다.
이 module은 그것을 수정하거나 terminal entry를 승격하지 않는다. 대신 trusted Manager
release까지 결박한 v6 execution identity와 그 lifecycle만 별도 root registry에 기록한다.
M05 isolated E2E는 이 일반 실행 identity의 첫 소비자일 뿐, schema·CLI·차단 규칙은
특정 test harness에 의존하지 않는다.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.runtime_execution_identity import (
    ExecutionIdentityV6,
)
from kor_travel_docker_manager.services.runtime_pin_registry import (
    RuntimePinRegistry,
    utc_timestamp,
)

RUNTIME_EXECUTION_REGISTRY_SCHEMA: Final = (
    "kor-travel-docker-manager.runtime-execution-registry.v1"
)
RUNTIME_EXECUTIONS_FILE_ENV: Final = "KTDM_RUNTIME_EXECUTIONS_FILE"
RUNTIME_EXECUTIONS_PUBLIC_FILE_ENV: Final = "KTDM_RUNTIME_EXECUTIONS_PUBLIC_FILE"
RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV: Final = (
    "KTDM_RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE"
)

_TRUSTED_STATE_ROOT: Final = Path("/var/lib/kor-travel-docker-manager")
_TRUSTED_PUBLIC_ROOT: Final = Path("/var/lib/kor-travel-docker-manager-public")
_TRUSTED_INSTALL_ROOT: Final = Path("/opt/kor-travel-docker-manager")
_DEFAULT_BASENAME: Final = "runtime-executions.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_MAX_TEXT = 500
_MAX_HISTORY = 500
_MAX_BLOCKED = 500


class RuntimeExecutionRegistryError(DeploymentContractError):
    """execution registry의 부재·손상·권한 위반을 fail-close로 알린다."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise RuntimeExecutionRegistryError(f"runtime execution registry {field} must be a string")
    text = value.strip()
    if not text or len(text) > _MAX_TEXT or any(char in text for char in ("\n", "\r", "\x00")):
        raise RuntimeExecutionRegistryError(f"runtime execution registry {field} is invalid")
    return text


def _revision(value: Any, field: str) -> str:
    if not isinstance(value, str) or _REVISION.fullmatch(value) is None:
        raise RuntimeExecutionRegistryError(f"runtime execution registry {field} must be a 40-hex revision")
    return value


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RuntimeExecutionRegistryError(f"runtime execution registry {field} must be a sha256")
    return value


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise RuntimeExecutionRegistryError(f"runtime execution registry {field} must be a UTC timestamp")
    return value


@dataclass(frozen=True)
class ExecutionBinding:
    """한 trusted Manager release가 source pinset에 결박된 immutable runtime execution."""

    source_pinset_sha256: str
    map_revision: str
    pinvi_revision: str
    manager_source_revision: str
    execution_identity_sha256: str
    bound_at: str
    bound_by: str
    reason: str

    def __post_init__(self) -> None:
        _digest(self.source_pinset_sha256, "source_pinset_sha256")
        _revision(self.map_revision, "map_revision")
        _revision(self.pinvi_revision, "pinvi_revision")
        _revision(self.manager_source_revision, "manager_source_revision")
        _timestamp(self.bound_at, "bound_at")
        _text(self.bound_by, "bound_by")
        _text(self.reason, "reason")
        identity = ExecutionIdentityV6.build(
            source_pinset_sha256=self.source_pinset_sha256,
            manager_source_revision=self.manager_source_revision,
        )
        if self.execution_identity_sha256 != identity.execution_identity_sha256:
            raise RuntimeExecutionRegistryError(
                "runtime execution registry execution identity differs"
            )

    @classmethod
    def from_payload(cls, payload: Any) -> ExecutionBinding:
        if not isinstance(payload, dict) or set(payload) != {
            "source_pinset_sha256",
            "map_revision",
            "pinvi_revision",
            "manager_source_revision",
            "execution_identity_sha256",
            "bound_at",
            "bound_by",
            "reason",
        }:
            raise RuntimeExecutionRegistryError("runtime execution binding has invalid fields")
        return cls(**payload)

    def to_payload(self) -> dict[str, str]:
        return {
            "source_pinset_sha256": self.source_pinset_sha256,
            "map_revision": self.map_revision,
            "pinvi_revision": self.pinvi_revision,
            "manager_source_revision": self.manager_source_revision,
            "execution_identity_sha256": self.execution_identity_sha256,
            "bound_at": self.bound_at,
            "bound_by": self.bound_by,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BlockedExecution:
    """unconditional terminal은 v6 execution identity 하나에만 귀속된다."""

    execution_identity_sha256: str
    source_pinset_sha256: str
    manager_source_revision: str
    reason: str
    blocked_at: str
    phase: str | None = None

    def __post_init__(self) -> None:
        _digest(self.execution_identity_sha256, "blocked_executions[].execution_identity_sha256")
        _digest(self.source_pinset_sha256, "blocked_executions[].source_pinset_sha256")
        _revision(self.manager_source_revision, "blocked_executions[].manager_source_revision")
        _text(self.reason, "blocked_executions[].reason")
        _timestamp(self.blocked_at, "blocked_executions[].blocked_at")
        if self.phase is not None:
            _text(self.phase, "blocked_executions[].phase")
        expected = ExecutionIdentityV6.build(
            source_pinset_sha256=self.source_pinset_sha256,
            manager_source_revision=self.manager_source_revision,
        ).execution_identity_sha256
        if self.execution_identity_sha256 != expected:
            raise RuntimeExecutionRegistryError(
                "runtime execution registry blocked execution identity differs"
            )

    @classmethod
    def from_payload(cls, payload: Any) -> BlockedExecution:
        if not isinstance(payload, dict) or set(payload) - {
            "execution_identity_sha256",
            "source_pinset_sha256",
            "manager_source_revision",
            "reason",
            "blocked_at",
            "phase",
        }:
            raise RuntimeExecutionRegistryError("runtime execution blocked entry has invalid fields")
        phase = payload.get("phase")
        return cls(
            execution_identity_sha256=_digest(
                payload.get("execution_identity_sha256"),
                "blocked_executions[].execution_identity_sha256",
            ),
            source_pinset_sha256=_digest(
                payload.get("source_pinset_sha256"),
                "blocked_executions[].source_pinset_sha256",
            ),
            manager_source_revision=_revision(
                payload.get("manager_source_revision"),
                "blocked_executions[].manager_source_revision",
            ),
            reason=_text(payload.get("reason"), "blocked_executions[].reason"),
            blocked_at=_timestamp(payload.get("blocked_at"), "blocked_executions[].blocked_at"),
            phase=_text(phase, "blocked_executions[].phase") if phase is not None else None,
        )

    def to_payload(self) -> dict[str, str]:
        payload = {
            "execution_identity_sha256": self.execution_identity_sha256,
            "source_pinset_sha256": self.source_pinset_sha256,
            "manager_source_revision": self.manager_source_revision,
            "reason": self.reason,
            "blocked_at": self.blocked_at,
        }
        if self.phase is not None:
            payload["phase"] = self.phase
        return payload


@dataclass(frozen=True)
class RuntimeExecutionRegistry:
    """현재 execution과 v6 lifecycle audit."""

    current: ExecutionBinding
    history: tuple[ExecutionBinding, ...] = ()
    blocked_executions: tuple[BlockedExecution, ...] = ()

    def __post_init__(self) -> None:
        history = tuple(self.history)
        blocked = tuple(self.blocked_executions)
        object.__setattr__(self, "history", history)
        object.__setattr__(self, "blocked_executions", blocked)
        if len(history) > _MAX_HISTORY or len(blocked) > _MAX_BLOCKED:
            raise RuntimeExecutionRegistryError("runtime execution registry lifecycle is too long")
        if history and history[-1].execution_identity_sha256 != self.current.execution_identity_sha256:
            raise RuntimeExecutionRegistryError("runtime execution registry history does not end at current")

    def current_matches(self, *, pins: RuntimePinRegistry, manager_source_revision: str) -> bool:
        return (
            self.current.source_pinset_sha256 == pins.pinset_sha256
            and self.current.map_revision == pins.map_revision
            and self.current.pinvi_revision == pins.pinvi_revision
            and self.current.manager_source_revision == manager_source_revision
        )

    def is_unconditionally_blocked_current(self) -> bool:
        return any(
            entry.execution_identity_sha256 == self.current.execution_identity_sha256
            and entry.phase is None
            for entry in self.blocked_executions
        )

    @classmethod
    def from_payload(cls, payload: Any) -> RuntimeExecutionRegistry:
        if not isinstance(payload, dict) or set(payload) != {
            "schema", "current", "history", "blocked_executions"
        }:
            raise RuntimeExecutionRegistryError("runtime execution registry document has invalid fields")
        if payload["schema"] != RUNTIME_EXECUTION_REGISTRY_SCHEMA:
            raise RuntimeExecutionRegistryError("runtime execution registry schema is not supported")
        if not isinstance(payload["history"], list) or not isinstance(payload["blocked_executions"], list):
            raise RuntimeExecutionRegistryError("runtime execution registry lifecycle must be lists")
        return cls(
            current=ExecutionBinding.from_payload(payload["current"]),
            history=tuple(ExecutionBinding.from_payload(item) for item in payload["history"]),
            blocked_executions=tuple(
                BlockedExecution.from_payload(item) for item in payload["blocked_executions"]
            ),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": RUNTIME_EXECUTION_REGISTRY_SCHEMA,
            "current": self.current.to_payload(),
            "history": [item.to_payload() for item in self.history],
            "blocked_executions": [item.to_payload() for item in self.blocked_executions],
        }


def runtime_execution_registry_path() -> Path:
    configured = os.environ.get(RUNTIME_EXECUTIONS_FILE_ENV, "").strip()
    return Path(configured) if configured else _TRUSTED_STATE_ROOT / _DEFAULT_BASENAME


def runtime_execution_registry_public_path() -> Path:
    configured = os.environ.get(RUNTIME_EXECUTIONS_PUBLIC_FILE_ENV, "").strip()
    return Path(configured) if configured else _TRUSTED_PUBLIC_ROOT / _DEFAULT_BASENAME


def _read_trusted_text(path: Path, *, expected_uid: int) -> str:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != expected_uid
            or stat.S_IMODE(before.st_mode) != 0o644
        ):
            raise RuntimeExecutionRegistryError("trusted Manager provenance file is unsafe")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise RuntimeExecutionRegistryError("trusted Manager provenance cannot be opened") from exc
    try:
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeExecutionRegistryError("trusted Manager provenance changed while reading")
        chunks: list[bytes] = []
        remaining = 1_000_001
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > 1_000_000:
        raise RuntimeExecutionRegistryError("trusted Manager provenance is too large")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeExecutionRegistryError("trusted Manager provenance is not text") from exc


def trusted_manager_source_revision(*, install_root: Path = _TRUSTED_INSTALL_ROOT) -> str:
    """clean trusted installation에서만 Manager revision을 읽는다.

    CLI/환경 입력을 수용하지 않는다. 이 값은 execution rebind를 위한 freshness 권한이므로
    root-owned install directory와 provenance 두 파일이 모두 exact contract를 만족해야 한다.
    """

    try:
        root = install_root.lstat()
    except OSError as exc:
        raise RuntimeExecutionRegistryError("trusted Manager install root cannot be inspected") from exc
    if (
        install_root.is_symlink()
        or not stat.S_ISDIR(root.st_mode)
        or root.st_uid != 0
        or stat.S_IMODE(root.st_mode) & 0o022
    ):
        raise RuntimeExecutionRegistryError("trusted Manager install root is unsafe")
    revision = _revision(
        _read_trusted_text(
            install_root / ".ktdm-source-revision", expected_uid=0
        ).strip(),
        "trusted Manager source revision",
    )
    try:
        manifest = json.loads(
            _read_trusted_text(
                install_root / ".ktdm-release-manifest.json", expected_uid=0
            )
        )
    except json.JSONDecodeError as exc:
        raise RuntimeExecutionRegistryError("trusted Manager release manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("manager_source_revision") != revision:
        raise RuntimeExecutionRegistryError("trusted Manager provenance revisions differ")
    return revision


def _insecure_mode_allowed() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return (
        (geteuid is None or geteuid() != 0)
        and os.environ.get(RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV) == "1"
    )


def _expected_registry_owner() -> int | None:
    geteuid = getattr(os, "geteuid", None)
    return geteuid() if geteuid is not None else None


def _assert_registry_parent(path: Path) -> None:
    try:
        metadata = path.parent.lstat()
    except OSError as exc:
        raise RuntimeExecutionRegistryError("runtime execution registry directory is unsafe") from exc
    expected_owner = _expected_registry_owner()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or (expected_owner is not None and metadata.st_uid != expected_owner)
        or (stat.S_IMODE(metadata.st_mode) & 0o022 and not _insecure_mode_allowed())
    ):
        raise RuntimeExecutionRegistryError("runtime execution registry directory is unsafe")


def _assert_registry_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeExecutionRegistryError("runtime execution registry file is unsafe") from exc
    expected_owner = _expected_registry_owner()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or (expected_owner is not None and metadata.st_uid != expected_owner)
        or (stat.S_IMODE(metadata.st_mode) & 0o022 and not _insecure_mode_allowed())
    ):
        raise RuntimeExecutionRegistryError("runtime execution registry file is unsafe")


def _read(path: Path) -> dict[str, Any]:
    try:
        _assert_registry_parent(path)
        _assert_registry_file(path)
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeExecutionRegistryError("runtime execution registry cannot be read") from exc
    if not isinstance(value, dict):
        raise RuntimeExecutionRegistryError("runtime execution registry document must be an object")
    return value


def load_runtime_execution_registry(*, path: Path | None = None) -> RuntimeExecutionRegistry:
    return RuntimeExecutionRegistry.from_payload(_read(path or runtime_execution_registry_path()))


def _write(path: Path, payload: Mapping[str, object], *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_registry_parent(path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_runtime_execution_registry(
    registry: RuntimeExecutionRegistry,
    *,
    path: Path | None = None,
    public_path: Path | None = None,
) -> None:
    _write(path or runtime_execution_registry_path(), registry.to_payload(), mode=0o600)
    _write(
        public_path or runtime_execution_registry_public_path(),
        registry.to_payload(),
        mode=0o644,
    )


def new_execution_binding(
    *, pins: RuntimePinRegistry, manager_source_revision: str, bound_by: str, reason: str
) -> ExecutionBinding:
    identity = ExecutionIdentityV6.build(
        source_pinset_sha256=pins.pinset_sha256,
        manager_source_revision=manager_source_revision,
    )
    return ExecutionBinding(
        source_pinset_sha256=pins.pinset_sha256,
        map_revision=pins.map_revision,
        pinvi_revision=pins.pinvi_revision,
        manager_source_revision=manager_source_revision,
        execution_identity_sha256=identity.execution_identity_sha256,
        bound_at=utc_timestamp(),
        bound_by=bound_by,
        reason=reason,
    )


def migrate_execution_registry(
    *, pins: RuntimePinRegistry, manager_source_revision: str, bound_by: str, reason: str
) -> RuntimeExecutionRegistry:
    current = new_execution_binding(
        pins=pins,
        manager_source_revision=manager_source_revision,
        bound_by=bound_by,
        reason=reason,
    )
    return RuntimeExecutionRegistry(current=current, history=(current,))


def rebind_execution_registry(
    *,
    registry: RuntimeExecutionRegistry,
    pins: RuntimePinRegistry,
    manager_source_revision: str,
    bound_by: str,
    reason: str,
) -> RuntimeExecutionRegistry:
    if not registry.is_unconditionally_blocked_current():
        raise RuntimeExecutionRegistryError(
            "current execution is not terminal; execution rebind is refused"
        )
    if (
        registry.current.source_pinset_sha256 != pins.pinset_sha256
        or registry.current.map_revision != pins.map_revision
        or registry.current.pinvi_revision != pins.pinvi_revision
    ):
        raise RuntimeExecutionRegistryError("execution rebind source pinset differs")
    if registry.current.manager_source_revision == manager_source_revision:
        raise RuntimeExecutionRegistryError("execution rebind Manager revision did not change")
    current = new_execution_binding(
        pins=pins,
        manager_source_revision=manager_source_revision,
        bound_by=bound_by,
        reason=reason,
    )
    return RuntimeExecutionRegistry(
        current=current,
        history=(*registry.history, current)[-_MAX_HISTORY:],
        blocked_executions=registry.blocked_executions,
    )


def block_current_execution(
    *, registry: RuntimeExecutionRegistry, reason: str, phase: str | None = None
) -> RuntimeExecutionRegistry:
    if registry.is_unconditionally_blocked_current():
        return registry
    entry = BlockedExecution(
        execution_identity_sha256=registry.current.execution_identity_sha256,
        source_pinset_sha256=registry.current.source_pinset_sha256,
        manager_source_revision=registry.current.manager_source_revision,
        reason=reason,
        blocked_at=utc_timestamp(),
        phase=phase,
    )
    return RuntimeExecutionRegistry(
        current=registry.current,
        history=registry.history,
        blocked_executions=(*registry.blocked_executions, entry),
    )
