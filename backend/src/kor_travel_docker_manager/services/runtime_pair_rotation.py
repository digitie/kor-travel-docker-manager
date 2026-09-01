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
    build_runtime_pin_rollback,
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
) -> bool:
    # target 유일성은 operator가 선언하는 map·pinvi revision으로 판정한다.
    # manager_source_revision은 여기에 넣지 않는다: operator가 선언하지 않는 값이고
    # `trusted_manager_source_revision()`은 trusted 설치마다 바뀌므로, 비교에 넣으면
    # v5/v6 사이 crash 뒤 Manager release를 설치한 순간 모든 재개 경로가
    # "different target"으로 거부돼 host가 wedge된다(mainline clear 경로도 없다).
    # 재개는 intent에 baked된 execution을 그대로 publish하고, Manager가 그 사이
    # 바뀌었으면 이후 `pin rebind-execution`이 정본 복구다(verify가 manager_drift로 안내).
    return (
        pending.pin_registry.map_revision == map_revision
        and pending.pin_registry.pinvi_revision == pinvi_revision
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


def _publish_rotation_target(target: RuntimePairRotation) -> RuntimePinRegistry:
    """intent에 준비된 v5/v6 target을 끝까지 publish하고 intent를 지운다.

    ``rotate_pair_with_execution``과 동일한 꼬리 계약: 어느 단계에서 실패해도 intent가
    남아 같은 target의 retry가 전체를 다시 publish한다.
    """

    write_runtime_pin_registry(target.pin_registry)
    write_runtime_execution_registry(target.execution_registry)
    _clear_pending_runtime_pair_rotation()
    return target.pin_registry


def rotate_single_role_with_execution(
    *,
    role: str,
    revision: str,
    manager_source_revision: str,
    reason: str,
    rotated_by: str,
    block_previous: bool,
) -> RuntimePinRegistry:
    """한 role의 source 회전을 v6 execution과 durable intent 아래 함께 기록한다.

    v6 host에서 단일 role 회전이 v5만 바꾸면 execution binding이 stale이 되어 모든
    runtime mutation이 fail-close된다 — 이 함수가 있기 전 ``pin rotate``와
    ``pin apply-pending``이 정확히 그 상태를 만들었다. pair 회전과 같은 transaction
    계약을 쓰되, terminal current pinset은 ``rotate_runtime_pin``과 동일하게 거부한다.
    pair 선언 없는 단일 role 탈출은 M05가 pair-incomplete pinset을 one-shot ledger에
    먼저 소비할 수 있는 문이기 때문이다. terminal에서의 탈출은 두 revision을 함께
    선언하는 ``rotate-pair``만 허용된다.
    """

    from kor_travel_docker_manager.services.pinned_runtime_release import RUNTIME_SOURCE_ROLES
    from kor_travel_docker_manager.services.runtime_execution_registry import (
        load_runtime_execution_registry,
        rotate_execution_source_binding,
    )

    if role not in RUNTIME_SOURCE_ROLES:
        raise RuntimePairRotationError("runtime pin role must be map or pinvi")

    current = load_runtime_pin_registry()
    requested_map = revision if role == "map" else current.map_revision
    requested_pinvi = revision if role == "pinvi" else current.pinvi_revision

    pending = load_pending_runtime_pair_rotation()
    if pending is not None:
        # v5 write 이후 crash면 current의 다른 role slot은 이미 pending과 같고, write
        # 이전 crash면 회전 전 값 그대로 pending과 같다 — 어느 crash 지점에서도 이
        # 대조는 "같은 단일 role 재시도"만 통과시킨다.
        if not _same_requested_target(
            pending,
            map_revision=requested_map,
            pinvi_revision=requested_pinvi,
        ):
            raise RuntimePairRotationError(
                "runtime pair rotation is incomplete for a different target"
            )
        return _publish_rotation_target(pending)

    if current.is_unconditionally_blocked_pinset(current.pinset_sha256):
        raise RuntimePairRotationError(
            "a terminal current pinset requires atomic Map/PinVi pair rotation"
        )
    executions = load_runtime_execution_registry()
    # v5 미차단이 v6 미차단을 뜻하지 않는다. M05 launcher의 terminal 판정은 `pin
    # block-execution`으로 v6에만 기록되고 v5 blocked_pinsets는 비어 있는 것이 정상
    # 경로다(run-m05-isolated-e2e-once). v6 terminal도 검사하지 않으면, 단일 role
    # 회전이 새 미차단 execution identity를 만들어 rebuild gate를 다시 열어 준다 —
    # pair 선언 없이 terminal one-shot을 탈출하는 정확히 그 구멍이다. 탈출은
    # rotate-pair(두 revision 선언)로만 허용한다.
    if executions.is_unconditionally_blocked_current():
        raise RuntimePairRotationError(
            "a terminal current execution requires atomic Map/PinVi pair rotation"
        )
    target_pins = build_runtime_pin_pair_rotation(
        current=current,
        map_revision=requested_map,
        pinvi_revision=requested_pinvi,
        reason=reason,
        rotated_by=rotated_by,
        block_previous=block_previous,
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
    return _publish_rotation_target(target)


def rollback_with_execution(
    *,
    pinset_sha256: str,
    manager_source_revision: str,
    reason: str,
    rotated_by: str,
) -> RuntimePinRegistry:
    """보존본 원복을 v6 execution과 durable intent 아래 함께 기록한다.

    v6가 이미 target pinset을 가리키는 **치유형** 사례 — 단일 role 회전 결함이나 수동
    조작으로 v5만 앞서간 상태를 되돌리는 경우 — 에서는 execution을 그대로 보존한다.
    ``rotate_execution_source_binding``의 "did not change" 거부를 여기서 흡수하지
    않으면 정확히 그 치유가 막힌다. v5/v6가 함께 current인 일반 원복에서는 execution도
    새 binding으로 이행한다.
    """

    from kor_travel_docker_manager.services.runtime_execution_registry import (
        load_runtime_execution_registry,
        rotate_execution_source_binding,
    )

    normalized = pinset_sha256.strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise RuntimePairRotationError("rollback pinset must be a 64-hex digest")

    pending = load_pending_runtime_pair_rotation()
    if pending is not None:
        if (
            pending.pin_registry.pinset_sha256 != normalized
            or pending.execution_registry.current.manager_source_revision
            != manager_source_revision
        ):
            raise RuntimePairRotationError(
                "runtime pair rotation is incomplete for a different target"
            )
        return _publish_rotation_target(pending)

    target_pins = build_runtime_pin_rollback(
        pinset_sha256=normalized,
        rotated_by=rotated_by,
        reason=reason,
    )
    executions = load_runtime_execution_registry()
    if executions.current_matches(
        pins=target_pins, manager_source_revision=manager_source_revision
    ):
        # 치유형: v6는 이미 정확하다. 새 binding을 만들면 동일 identity가 history에
        # 중복 등재되고, terminal audit이 붙어 있던 execution이 새 것으로 교체된다.
        target_executions = executions
    else:
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
    return _publish_rotation_target(target)
