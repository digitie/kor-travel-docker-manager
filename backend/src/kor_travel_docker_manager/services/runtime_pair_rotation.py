"""복수 runtime registry 회전의 복구 가능한 일반 transaction 경계.

source pin(v5)과 Manager-aware execution(v6)은 각각 private/public 사본을 가진 별도
문서다. 파일 하나의 ``rename`` 원자성만으로는 이 네 문서를 동시에 바꿀 수 없다.
그래서 이 모듈은 대상 문서를 먼저 모두 준비하고 root-only intent를 durable하게 남긴
뒤, 같은 intent를 idempotent하게 끝낼 때까지 mutation gate를 fail-close한다.

Map/PinVi/M05는 이 일반 lifecycle의 소비자일 뿐 이 모듈에는 들어오지 않는다.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.runtime_execution_registry import (
    RuntimeExecutionRegistry,
    RuntimeExecutionRegistryError,
    write_runtime_execution_registry,
)
from kor_travel_docker_manager.services.runtime_pin_registry import (
    RuntimePinRegistry,
    RuntimePinRegistryError,
    build_runtime_pin_pair_rotation,
    load_runtime_pin_registry,
    utc_timestamp,
    write_runtime_pin_registry,
)

RUNTIME_PAIR_ROTATION_SCHEMA: Final = "kor-travel-docker-manager.runtime-pair-rotation.v1"
RUNTIME_PAIR_ROTATION_FILE_ENV: Final = "KTDM_RUNTIME_PAIR_ROTATION_FILE"
RUNTIME_PAIR_ROTATION_ALLOW_INSECURE_MODE_ENV: Final = (
    "KTDM_RUNTIME_PAIR_ROTATION_ALLOW_INSECURE_MODE"
)
_TRUSTED_STATE_ROOT: Final = Path("/var/lib/kor-travel-docker-manager")
_DEFAULT_BASENAME: Final = "runtime-pair-rotation.json"


class RuntimePairRotationError(DeploymentContractError):
    """pair rotation transaction의 부재·손상·미완료 상태를 fail-close로 알린다."""


def runtime_pair_rotation_path() -> Path:
    configured = os.environ.get(RUNTIME_PAIR_ROTATION_FILE_ENV, "").strip()
    return Path(configured) if configured else _TRUSTED_STATE_ROOT / _DEFAULT_BASENAME


def _insecure_mode_allowed() -> bool:
    """공유 마운트 개발 fixture만 명시적으로 완화하고 root에서는 절대 열지 않는다."""

    geteuid = getattr(os, "geteuid", None)
    return (
        (geteuid is None or geteuid() != 0)
        and os.environ.get(RUNTIME_PAIR_ROTATION_ALLOW_INSECURE_MODE_ENV) == "1"
    )


def _require_root_private_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimePairRotationError("runtime pair rotation intent is unsafe") from exc
    expected_uid = getattr(os, "geteuid", lambda: None)()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or (expected_uid is not None and metadata.st_uid != expected_uid)
        or (
            stat.S_IMODE(metadata.st_mode) != 0o600
            and not _insecure_mode_allowed()
        )
    ):
        raise RuntimePairRotationError("runtime pair rotation intent is unsafe")


def _require_private_parent(path: Path) -> None:
    try:
        metadata = path.parent.lstat()
    except OSError as exc:
        raise RuntimePairRotationError("runtime pair rotation directory is unsafe") from exc
    expected_uid = getattr(os, "geteuid", lambda: None)()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or (expected_uid is not None and metadata.st_uid != expected_uid)
        or (stat.S_IMODE(metadata.st_mode) & 0o022 and not _insecure_mode_allowed())
    ):
        raise RuntimePairRotationError("runtime pair rotation directory is unsafe")


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    _require_private_parent(path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class RuntimePairRotation:
    """이미 계산된 v5/v6 target을 보존하는 재개 가능한 intent."""

    created_at: str
    pin_registry: RuntimePinRegistry
    execution_registry: RuntimeExecutionRegistry

    def __post_init__(self) -> None:
        if self.execution_registry.current.source_pinset_sha256 != self.pin_registry.pinset_sha256:
            raise RuntimePairRotationError("runtime pair rotation target binding differs")
        if (
            self.execution_registry.current.map_revision != self.pin_registry.map_revision
            or self.execution_registry.current.pinvi_revision != self.pin_registry.pinvi_revision
        ):
            raise RuntimePairRotationError("runtime pair rotation target revisions differ")

    @classmethod
    def from_payload(cls, payload: Any) -> RuntimePairRotation:
        if not isinstance(payload, dict) or set(payload) != {
            "schema", "created_at", "pin_registry", "execution_registry"
        }:
            raise RuntimePairRotationError("runtime pair rotation intent is malformed")
        if payload["schema"] != RUNTIME_PAIR_ROTATION_SCHEMA:
            raise RuntimePairRotationError("runtime pair rotation schema is not supported")
        created_at = payload["created_at"]
        if not isinstance(created_at, str) or not created_at.endswith("Z"):
            raise RuntimePairRotationError("runtime pair rotation timestamp is invalid")
        try:
            return cls(
                created_at=created_at,
                pin_registry=RuntimePinRegistry.from_payload(payload["pin_registry"]),
                execution_registry=RuntimeExecutionRegistry.from_payload(
                    payload["execution_registry"]
                ),
            )
        except (RuntimePinRegistryError, RuntimeExecutionRegistryError) as exc:
            raise RuntimePairRotationError("runtime pair rotation target is invalid") from exc

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": RUNTIME_PAIR_ROTATION_SCHEMA,
            "created_at": self.created_at,
            "pin_registry": self.pin_registry.to_payload(),
            "execution_registry": self.execution_registry.to_payload(),
        }


def load_pending_runtime_pair_rotation() -> RuntimePairRotation | None:
    path = runtime_pair_rotation_path()
    if not path.exists():
        return None
    _require_private_parent(path)
    _require_root_private_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimePairRotationError("runtime pair rotation intent cannot be read") from exc
    return RuntimePairRotation.from_payload(payload)


def require_no_pending_runtime_pair_rotation() -> None:
    if load_pending_runtime_pair_rotation() is not None:
        raise RuntimePairRotationError(
            "runtime pair rotation is incomplete; resume the same root pin rotate-pair command"
        )


def _clear_pending_runtime_pair_rotation() -> None:
    path = runtime_pair_rotation_path()
    _require_private_parent(path)
    _require_root_private_file(path)
    try:
        path.unlink()
        directory = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise RuntimePairRotationError("runtime pair rotation intent cannot be cleared") from exc


def _same_requested_target(
    pending: RuntimePairRotation,
    *,
    map_revision: str,
    pinvi_revision: str,
    manager_source_revision: str,
) -> bool:
    return (
        pending.pin_registry.map_revision == map_revision
        and pending.pin_registry.pinvi_revision == pinvi_revision
        and pending.execution_registry.current.manager_source_revision == manager_source_revision
    )


def rotate_pair_with_execution(
    *,
    map_revision: str,
    pinvi_revision: str,
    manager_source_revision: str,
    reason: str,
    rotated_by: str,
    block_previous: bool,
) -> RuntimePinRegistry:
    """v5 source와 v6 execution을 durable intent 아래 함께 회전/복구한다.

    모든 target을 write 전에 만들고, 실패하면 intent를 지우지 않는다. 다음 **같은**
    ``rotate-pair``가 정확한 target을 다시 publish한 뒤 intent를 지워 복구한다. pending
    intent가 있는 동안 모든 runtime mutation은 별도 gate에서 거부된다.
    """

    pending = load_pending_runtime_pair_rotation()
    if pending is not None:
        if not _same_requested_target(
            pending,
            map_revision=map_revision,
            pinvi_revision=pinvi_revision,
            manager_source_revision=manager_source_revision,
        ):
            raise RuntimePairRotationError(
                "runtime pair rotation is incomplete for a different target"
            )
        target = pending
    else:
        current = load_runtime_pin_registry()
        target_pins = build_runtime_pin_pair_rotation(
            current=current,
            map_revision=map_revision,
            pinvi_revision=pinvi_revision,
            reason=reason,
            rotated_by=rotated_by,
            block_previous=block_previous,
        )
        from kor_travel_docker_manager.services.runtime_execution_registry import (
            load_runtime_execution_registry,
            rotate_execution_source_binding,
        )

        executions = load_runtime_execution_registry()
        target_executions = rotate_execution_source_binding(
            registry=executions,
            pins=target_pins,
            manager_source_revision=manager_source_revision,
            bound_by=rotated_by,
            reason=reason,
        )
        target = RuntimePairRotation(
            created_at=utc_timestamp(),
            pin_registry=target_pins,
            execution_registry=target_executions,
        )
        _atomic_write(runtime_pair_rotation_path(), target.to_payload())

    # 각 writer는 private/public copy까지 자신이 소유한다. 어느 단계에서 실패해도
    # intent는 남고 retry가 target 전체를 다시 publish한다.
    write_runtime_pin_registry(target.pin_registry)
    write_runtime_execution_registry(target.execution_registry)
    _clear_pending_runtime_pair_rotation()
    return target.pin_registry
