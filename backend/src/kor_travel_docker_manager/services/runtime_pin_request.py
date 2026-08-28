"""UI가 남기는 pin 회전 **요청** — 제안이지 pin이 아니다.

설계 정본: ``docs/ktdctl-ui-migration.md`` §1.2 (c), 오너 승인 Q4.

registry는 root `0600`이라 API 프로세스가 물리적으로 쓸 수 없고, 그 경계가 이
시스템에서 가장 값싼 안전장치다. 그래서 UI는 **요청만** 남기고 적용은 root CLI가
한다(``ktdctl pin apply-pending --confirm``).

**이 저장소가 pin이 될 수 없는 이유** (설계의 핵심 논거이므로 여기 남긴다):

1. **어떤 로드 경로도 이 파일을 읽지 않는다.** authority는
   ``current_pinned_runtime_release()`` → ``load_runtime_pin_registry()`` 하나뿐이고
   rebuild 소비처도 ``compose_service.rebuild_pinned_runtime`` 한 곳이다.
2. **registry는 읽을 때마다 무결성 검사를 통과해야 한다** — 소유자가 root이거나
   자기 자신이어야 하고 group/other 쓰기가 금지된다. 비-root backend는 registry를
   만들 수도 위조할 수도 없다.
3. **apply-pending은 요청에서 role과 40-hex revision, 표시용 문자열만 취한다.**
   canonical URL은 코드가, digest는 코드가 재계산하고, 차단 목록은 root registry와
   코드 하한선에서 다시 만든다. 요청이 이 중 무엇도 결정하지 못한다.
4. **적용은 root + ``--confirm`` + base pinset 일치를 동시에 요구한다.**

**왜 SQLite가 아닌가**: ``database.py``의 DB 경로는 ``__file__``에서 유도되고 env
오버라이드가 없다. 운영에서 backend는 소스 트리에서, ``ktdctl``은 wheel이 설치된
site-packages에서 돌아 **같은 호스트에서 서로 다른 파일**을 연다. 사람이 읽는 감사
행은 그대로 SQLite에 남기되, 기계가 주고받는 pending request는 경로를 명시적으로
지정할 수 있는 파일이어야 한다.
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
from kor_travel_docker_manager.services.registry import get_project_root
from kor_travel_docker_manager.services.runtime_pin_registry import utc_timestamp

RUNTIME_PIN_REQUEST_SCHEMA: Final = "kor-travel-docker-manager.runtime-pin-request.v1"
RUNTIME_PIN_REQUEST_FILE_ENV: Final = "KTDM_RUNTIME_PIN_REQUEST_FILE"
MAX_REASON_LENGTH: Final = 500
MAX_ACTOR_LENGTH: Final = 200
_MAX_REQUEST_BYTES: Final = 64 * 1024
_TRUSTED_INSTALL_ROOT: Final = Path("/opt/kor-travel-docker-manager")
_TRUSTED_REQUEST_ROOT: Final = Path("/var/lib/kor-travel-docker-manager-requests")
_DEFAULT_BASENAME: Final = "runtime-pin-requests.json"

# registry 모듈의 private 정규식을 import하지 않고 복제한다 — 두 모듈의 계약이
# 우연히 같을 뿐이고, 한쪽이 바뀔 때 다른 쪽이 조용히 따라가면 안 된다.
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class RuntimePinRequestError(DeploymentContractError):
    """요청 파일의 손상·계약 위반을 fail-close로 알린다."""


def _require_text(value: Any, field: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise RuntimePinRequestError(f"runtime pin request {field} must be a string")
    text = value.strip()
    if not text:
        raise RuntimePinRequestError(f"runtime pin request {field} must not be empty")
    if len(text) > max_length:
        raise RuntimePinRequestError(f"runtime pin request {field} is too long")
    if any(character in text for character in ("\n", "\r", "\x00")):
        raise RuntimePinRequestError(f"runtime pin request {field} must be a single line")
    return text


@dataclass(frozen=True)
class RuntimePinRequest:
    request_id: str
    role: str
    revision: str
    reason: str
    requested_by: str
    requested_at: str
    base_pinset_sha256: str
    prospective_pinset_sha256: str
    audit_event_id: str | None = None

    def __post_init__(self) -> None:
        from kor_travel_docker_manager.services.pinned_runtime_release import (
            RUNTIME_SOURCE_ROLES,
        )

        if _UUID4.fullmatch(self.request_id) is None:
            raise RuntimePinRequestError("runtime pin request id must be a uuid4")
        if self.role not in RUNTIME_SOURCE_ROLES:
            raise RuntimePinRequestError("runtime pin request role must be map or pinvi")
        if _REVISION.fullmatch(self.revision) is None:
            raise RuntimePinRequestError("runtime pin request revision must be a 40-hex commit")
        _require_text(self.reason, "reason", max_length=MAX_REASON_LENGTH)
        _require_text(self.requested_by, "requested_by", max_length=MAX_ACTOR_LENGTH)
        if _TIMESTAMP.fullmatch(self.requested_at) is None:
            raise RuntimePinRequestError("runtime pin request timestamp is invalid")
        for field, value in (
            ("base_pinset_sha256", self.base_pinset_sha256),
            ("prospective_pinset_sha256", self.prospective_pinset_sha256),
        ):
            if _SHA256.fullmatch(value) is None:
                raise RuntimePinRequestError(f"runtime pin request {field} must be a sha256")
        # 아무것도 바꾸지 않는 요청은 형식 자체가 잘못된 것이다.
        if self.base_pinset_sha256 == self.prospective_pinset_sha256:
            raise RuntimePinRequestError("runtime pin request would not change the pinset")

    @classmethod
    def from_payload(cls, payload: Any) -> RuntimePinRequest:
        if not isinstance(payload, dict):
            raise RuntimePinRequestError("runtime pin request document must be an object")
        unknown = set(payload) - {
            "schema",
            "request_id",
            "role",
            "revision",
            "reason",
            "requested_by",
            "requested_at",
            "base_pinset_sha256",
            "prospective_pinset_sha256",
            "audit_event_id",
        }
        if unknown:
            raise RuntimePinRequestError("runtime pin request document has unknown fields")
        if payload.get("schema") != RUNTIME_PIN_REQUEST_SCHEMA:
            raise RuntimePinRequestError("runtime pin request schema is not supported")
        return cls(
            request_id=payload.get("request_id"),
            role=payload.get("role"),
            revision=payload.get("revision"),
            reason=payload.get("reason"),
            requested_by=payload.get("requested_by"),
            requested_at=payload.get("requested_at"),
            base_pinset_sha256=payload.get("base_pinset_sha256"),
            prospective_pinset_sha256=payload.get("prospective_pinset_sha256"),
            audit_event_id=payload.get("audit_event_id"),
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": RUNTIME_PIN_REQUEST_SCHEMA,
            "request_id": self.request_id,
            "role": self.role,
            "revision": self.revision,
            "reason": self.reason,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
            "base_pinset_sha256": self.base_pinset_sha256,
            "prospective_pinset_sha256": self.prospective_pinset_sha256,
        }
        if self.audit_event_id is not None:
            payload["audit_event_id"] = self.audit_event_id
        return payload


def _running_from_trusted_install_root() -> bool:
    try:
        return Path(get_project_root()).resolve() == _TRUSTED_INSTALL_ROOT.resolve()
    except OSError:
        return False


def runtime_pin_request_path() -> Path:
    """요청 파일 경로. **backend가 쓸 수 있어야 하므로 registry와 다른 트리다.**"""

    configured = os.environ.get(RUNTIME_PIN_REQUEST_FILE_ENV, "").strip()
    if configured:
        return Path(configured)
    if _running_from_trusted_install_root():
        return _TRUSTED_REQUEST_ROOT / _DEFAULT_BASENAME
    return Path(get_project_root()) / ".ktdm-runtime-pin-requests.json"


def _assert_request_file_integrity(path: Path) -> None:
    """root가 비-root의 파일을 읽는 유일한 지점이다.

    그래서 내용이 아니라 **누가 이 자리에 쓸 수 있었는가**를 본다. symlink를 따라가지
    않고, 일반 파일이어야 하며, 파일과 그 부모 모두 group/other 쓰기가 금지돼야 한다.
    """

    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RuntimePinRequestError(
            f"runtime pin request file cannot be inspected: {path.name}"
        ) from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimePinRequestError(
            f"runtime pin request path is not a regular file: {path.name}"
        )
    if stat.S_IMODE(file_stat.st_mode) & 0o022:
        raise RuntimePinRequestError(
            f"runtime pin request file must not be group or world writable: {path.name}"
        )
    try:
        parent_stat = path.parent.stat()
    except OSError as exc:
        raise RuntimePinRequestError(
            "runtime pin request directory cannot be inspected"
        ) from exc
    if stat.S_IMODE(parent_stat.st_mode) & 0o022:
        raise RuntimePinRequestError(
            "runtime pin request directory must not be group or world writable"
        )
    if file_stat.st_uid not in {0, parent_stat.st_uid}:
        raise RuntimePinRequestError(
            f"runtime pin request file is owned by an unexpected user: {path.name}"
        )


def _atomic_write_json(path: Path, payload: Mapping[str, Any], *, mode: int) -> None:
    """registry의 원자 쓰기를 복제한다 — 그쪽은 private이고 디렉터리 mode 계약이 다르다."""

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
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


def read_runtime_pin_request(*, path: Path | None = None) -> RuntimePinRequest | None:
    """대기 중인 요청. 없으면 ``None``, 손상됐으면 예외다."""

    target = path or runtime_pin_request_path()
    try:
        _assert_request_file_integrity(target)
        raw = target.read_bytes()[: _MAX_REQUEST_BYTES + 1]
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimePinRequestError(
            f"runtime pin request file cannot be read: {target.name}"
        ) from exc
    if len(raw) > _MAX_REQUEST_BYTES:
        raise RuntimePinRequestError("runtime pin request file is too large")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimePinRequestError("runtime pin request file is not valid JSON") from exc
    return RuntimePinRequest.from_payload(document)


def write_runtime_pin_request(
    request: RuntimePinRequest,
    *,
    path: Path | None = None,
    replace_existing: bool = False,
) -> Path:
    """요청을 기록한다. **기존 요청을 조용히 덮어쓰지 않는다.**"""

    target = path or runtime_pin_request_path()
    if not replace_existing and read_runtime_pin_request(path=target) is not None:
        raise RuntimePinRequestError("a runtime pin rotation request is already pending")
    _atomic_write_json(target, request.to_payload(), mode=0o600)
    return target


def clear_runtime_pin_request(*, expect_request_id: str, path: Path | None = None) -> bool:
    """id가 일치할 때만 지운다.

    적용·취소 사이에 새 요청이 들어왔다면 그것을 지우면 안 된다 — 오래된 화면이
    최신 요청을 없애는 경로를 막는다.
    """

    target = path or runtime_pin_request_path()
    existing = read_runtime_pin_request(path=target)
    if existing is None or existing.request_id != expect_request_id:
        return False
    target.unlink(missing_ok=True)
    return True


def prospective_pinset_sha256(
    *, release_version: int, map_revision: str, pinvi_revision: str
) -> str:
    """요청이 적용됐을 때 나올 pinset digest. 계산은 언제나 코드가 한다."""

    from kor_travel_docker_manager.services.pinned_runtime_release import (
        canonical_pinset_sha256,
        source_specs_for,
    )

    return canonical_pinset_sha256(
        version=release_version,
        sources=source_specs_for(map_revision=map_revision, pinvi_revision=pinvi_revision),
    )


__all__ = [
    "MAX_REASON_LENGTH",
    "RUNTIME_PIN_REQUEST_FILE_ENV",
    "RUNTIME_PIN_REQUEST_SCHEMA",
    "RuntimePinRequest",
    "RuntimePinRequestError",
    "clear_runtime_pin_request",
    "prospective_pinset_sha256",
    "read_runtime_pin_request",
    "runtime_pin_request_path",
    "utc_timestamp",
    "write_runtime_pin_request",
]
