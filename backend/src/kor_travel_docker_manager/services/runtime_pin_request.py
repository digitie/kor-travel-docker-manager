"""UI가 남기는 pin 회전 **요청** — 제안이지 pin이 아니다.

설계 정본: ``docs/ktdctl-ui-migration.md`` §1.2 (c), 오너 승인 Q4.

registry는 root `0600`이고 회전은 root CLI(``ktdctl pin apply-pending
--expect-revision <40-hex> --confirm``)만
한다. 그래서 UI는 **요청만** 남긴다.

.. warning::

   **"backend가 비-root라 물리적으로 못 쓴다"는 논거에 기대지 마라.** n150 운영 배포는
   ``.env``가 root ``0600``이라 backend를 ``sudo -n``으로 띄운다
   (``docs/deploy-runbook.local.md`` §3-3, 실제 uid 0으로 확인). 그 호스트에서 uid
   경계는 존재하지 않는다. 아래 1·3·4가 실제로 강제되는 보호이고, 2는 비-root로
   돌리는 호스트에서만 추가로 얹힌다.

**이 저장소가 pin이 될 수 없는 이유** (설계의 핵심 논거이므로 여기 남긴다):

1. **어떤 로드 경로도 이 파일을 읽지 않는다.** authority는
   ``current_pinned_runtime_release()`` → ``load_runtime_pin_registry()`` 하나뿐이고
   rebuild 소비처도 ``compose_service.rebuild_pinned_runtime`` 한 곳이다. HTTP 계층에
   registry mutator가 없다는 사실은 회귀
   ``test_the_http_layer_never_mutates_the_pin_registry``가 지킨다.
2. **registry는 읽을 때마다 무결성 검사를 통과해야 한다** — 소유자가 root이거나
   자기 자신이어야 하고 group/other 쓰기가 금지된다. (위 경고 참조: backend가 root면
   이 조건은 자동으로 만족되므로 경계가 아니다.)
3. **apply-pending은 요청에서 role과 40-hex revision, 표시용 문자열만 취한다.**
   canonical URL은 코드가, digest는 코드가 재계산하고, 차단 목록은 root registry와
   코드 하한선에서 다시 만든다. 요청이 이 중 무엇도 결정하지 못한다.
4. **적용은 root + ``--confirm`` + base pinset 일치 + revision 명시를 동시에
   요구한다**(``--expect-revision`` 또는 ``--any-revision``).

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
from kor_travel_docker_manager.services.trusted_install import (
    TRUSTED_INSTALL_ROOT,
    TRUSTED_REQUEST_ROOT,
    running_from_trusted_install_root,
)

RUNTIME_PIN_REQUEST_SCHEMA: Final = "kor-travel-docker-manager.runtime-pin-request.v1"
RUNTIME_PIN_REQUEST_FILE_ENV: Final = "KTDM_RUNTIME_PIN_REQUEST_FILE"
MAX_REASON_LENGTH: Final = 500
MAX_ACTOR_LENGTH: Final = 200
_MAX_REQUEST_BYTES: Final = 64 * 1024
# GM-09: 경로 상수와 trusted-root 판정의 정본은 services/trusted_install.py다.
_TRUSTED_INSTALL_ROOT: Final = TRUSTED_INSTALL_ROOT
_TRUSTED_REQUEST_ROOT: Final = TRUSTED_REQUEST_ROOT
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


class RuntimePinRequestReadableError(RuntimePinRequestError):
    """읽을 수 있는 요청을 잔재 제거 도구로 지우려 했다.

    별도 타입인 이유: 이전에는 오류 **문자열에 "readable"이 들어 있는가**로 판정했다.
    그 방식은 다른 무결성 실패(hardlink, group-writable 부모 등)를 전부 "손상됨"으로
    분류해 **멀쩡하고 id로 지울 수 있는 요청을 삭제**한다.
    """


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
    # GM-09: 예전에는 get_project_root() 비교 하나뿐이라, wheel 직접 실행처럼
    # registry.py의 4단계 상위 경로 규칙이 깨지는 실행 형태에서 이 모듈만 false
    # negative를 냈다 — backend가 쓴 요청 파일을 root CLI가 다른 경로에서 찾는
    # latent 불일치였다. running_from_trusted_install_root()가 그 케이스를 포함한
    # 셋을 모두 확인한다.
    return running_from_trusted_install_root()


def runtime_pin_request_path() -> Path:
    """요청 파일 경로. **backend가 쓸 수 있어야 하므로 registry와 다른 트리다.**"""

    configured = os.environ.get(RUNTIME_PIN_REQUEST_FILE_ENV, "").strip()
    if configured:
        return Path(configured)
    if _running_from_trusted_install_root():
        return _TRUSTED_REQUEST_ROOT / _DEFAULT_BASENAME
    return Path(get_project_root()) / ".ktdm-runtime-pin-requests.json"


def _open_verified_request_file(path: Path) -> int:
    """무결성을 확인한 **그 inode의 fd**를 돌려준다.

    root가 비-root의 파일을 읽는 유일한 지점이므로, 내용이 아니라 **누가 이 자리에 쓸
    수 있었는가**를 본다. 검사와 읽기가 서로 다른 syscall이면 그 사이에 파일을
    바꿔치기할 수 있으므로, ``O_NOFOLLOW``로 연 fd에 ``fstat``을 걸어 **검사한 대상과
    읽는 대상이 같은 inode임을 보장**한다.

    hardlink도 거부한다(``st_nlink != 1``). 그러지 않으면 root 소유 임의 파일로의
    hardlink가 "root가 썼으니 신뢰"라는 규칙을 통과한다.
    """

    # 파일부터 연다. 부모 디렉터리가 없는 경우도 "요청이 없다"이지 오류가 아니므로,
    # 부모를 먼저 stat하면 정상적인 부재를 손상으로 오인한다.
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        raise
    except NotADirectoryError as exc:
        raise FileNotFoundError(str(path)) from exc
    except OSError as exc:
        # ELOOP(symlink)도 여기로 온다 — 따라가지 않고 거부한다.
        raise RuntimePinRequestError(
            f"runtime pin request file cannot be opened: {path}"
        ) from exc
    try:
        try:
            parent_stat = path.parent.stat()
        except OSError as exc:
            raise RuntimePinRequestError(
                f"runtime pin request directory cannot be inspected: {path.parent}"
            ) from exc
        if stat.S_IMODE(parent_stat.st_mode) & 0o022:
            raise RuntimePinRequestError(
                f"runtime pin request directory must not be group or world writable: "
                f"{path.parent} — fix it with: sudo chmod 0700 {path.parent}"
            )
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise RuntimePinRequestError(
                f"runtime pin request path is not a regular file: {path}"
            )
        if file_stat.st_nlink != 1:
            raise RuntimePinRequestError(
                f"runtime pin request file has more than one link: {path}"
            )
        if stat.S_IMODE(file_stat.st_mode) & 0o022:
            raise RuntimePinRequestError(
                f"runtime pin request file must not be group or world writable: {path}"
            )
        if file_stat.st_uid not in {0, parent_stat.st_uid}:
            raise RuntimePinRequestError(
                f"runtime pin request file is owned by an unexpected user: {path}"
            )
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _prepare_request_parent(parent: Path) -> None:
    """요청 디렉터리를 준비한다.

    **우리가 만든 디렉터리만** mode를 정한다. 개발 체크아웃에서 요청 경로의 부모는
    저장소 루트 자체이므로, 이미 있는 디렉터리에 chmod 0700을 걸면 한 번의 클릭이
    트리 전체를 소유자 전용으로 만든다(같은 트리를 읽는 nginx·다른 앱이 함께 죽는다).
    """

    created = not parent.exists()
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimePinRequestError(
            f"runtime pin request directory cannot be created: {parent} — create it once "
            f"as the backend user: sudo install -d -o $(id -un) -g $(id -gn) -m 0700 {parent}"
        ) from exc
    if created:
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass


def _fsync_directory(parent: Path) -> None:
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


def _atomic_write_json(path: Path, payload: Mapping[str, Any], *, mode: int) -> None:
    """registry의 원자 쓰기를 복제한다 — 그쪽은 private이고 디렉터리 mode 계약이 다르다."""

    parent = path.parent
    _prepare_request_parent(parent)
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
    _fsync_directory(parent)


def _exclusive_write_json(path: Path, payload: Mapping[str, Any], *, mode: int) -> None:
    """배타 생성. 이미 있으면 커널이 ``FileExistsError``로 거절한다.

    ``os.replace``와 달리 승자가 하나로 정해지므로, 두 요청이 동시에 도착해도 나중
    것이 앞의 것을 덮지 않는다.
    """

    _prepare_request_parent(path.parent)
    body = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=False) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError as exc:
        raise RuntimePinRequestError(
            "a runtime pin rotation request is already pending"
        ) from exc
    except OSError as exc:
        raise RuntimePinRequestError(
            f"runtime pin request file cannot be created: {path}"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        # umask가 mode를 깎을 수 있으므로 명시적으로 다시 건다.
        os.chmod(path, mode)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def read_runtime_pin_request(*, path: Path | None = None) -> RuntimePinRequest | None:
    """대기 중인 요청. 없으면 ``None``, 손상됐으면 예외다."""

    target = path or runtime_pin_request_path()
    try:
        descriptor = _open_verified_request_file(target)
    except FileNotFoundError:
        return None
    try:
        raw = os.read(descriptor, _MAX_REQUEST_BYTES + 1)
    except OSError as exc:
        raise RuntimePinRequestError(
            f"runtime pin request file cannot be read: {target}"
        ) from exc
    finally:
        os.close(descriptor)
    if len(raw) > _MAX_REQUEST_BYTES:
        raise RuntimePinRequestError(f"runtime pin request file is too large: {target}")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # 경로를 함께 알린다 — 손상된 파일은 지워야 하고, 어디를 지울지 모르면
        # 회전 요청 경로 전체가 잠긴다.
        raise RuntimePinRequestError(
            f"runtime pin request file is not valid JSON: {target}"
        ) from exc
    return RuntimePinRequest.from_payload(document)


def write_runtime_pin_request(
    request: RuntimePinRequest,
    *,
    path: Path | None = None,
    replace_existing: bool = False,
) -> Path:
    """요청을 기록한다. **기존 요청을 조용히 덮어쓰지 않는다.**

    "읽어 보고 없으면 쓴다"로는 부족하다. 이 핸들러는 threadpool에서 돌아 두 관리자가
    (또는 한 관리자의 두 탭이) 실제로 경합하고, 그때 뒤에 도착한 쓰기가 앞의 요청을
    말없이 덮으면서 **둘 다 "기록됨"으로 감사에 남는다.** 그래서 배타 생성
    (``O_CREAT|O_EXCL``)으로 커널이 승자를 정하게 한다.
    """

    target = path or runtime_pin_request_path()
    if replace_existing:
        _atomic_write_json(target, request.to_payload(), mode=0o600)
        return target
    # 형식이 깨진 잔재가 남아 있으면 그 사실부터 알린다 — O_EXCL은 "이미 있다"만
    # 말할 수 있고, 무엇이 있는지는 말하지 못한다.
    existing = read_runtime_pin_request(path=target)
    if existing is not None:
        raise RuntimePinRequestError("a runtime pin rotation request is already pending")
    _exclusive_write_json(target, request.to_payload(), mode=0o600)
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


def discard_unreadable_runtime_pin_request(*, path: Path | None = None) -> Path | None:
    """읽을 수 없는 요청 파일을 **파싱하지 않고** 지운다.

    id 대조 삭제만 있으면 손상된 파일은 영원히 남는다. 읽지 못하니 id를 알 수 없고,
    파일이 있으니 새 요청도 받을 수 없어 회전 요청 경로 전체가 잠긴다. 여기서 내용을
    믿고 무엇을 하는 것이 아니라 **버리기만** 하므로 파싱하지 않는 것이 옳다.

    이미 잘 읽히는 요청은 이 함수로 지울 수 없다 — 그것은 id 대조 경로의 일이다.
    """

    target = path or runtime_pin_request_path()

    # `exists()`는 symlink를 따라간다. 끊어진 symlink는 "없다"로 읽히는데 `write`는 그
    # 자리 때문에 계속 실패한다 — 요청 경로가 영구히 잠기고 --force가 그것을 못 푼다.
    if target.is_symlink():
        target.unlink(missing_ok=True)
        return target
    if not target.exists():
        return None

    # **무결성 실패와 내용 손상을 구분한다.** 무결성(hardlink, 소유자, 부모 권한)은
    # "이 내용을 믿고 행동해도 되는가"의 문제고, 버릴지 말지는 "이것이 요청이기는
    # 한가"의 문제다. 둘을 뭉뚱그려 지우면 멀쩡하고 id로 취소할 수 있는 요청이
    # 사라진다 — 무결성 문제는 고칠 수 있고, 지워진 요청은 되돌릴 수 없다.
    try:
        raw = target.read_bytes()[: _MAX_REQUEST_BYTES + 1]
        RuntimePinRequest.from_payload(json.loads(raw.decode("utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimePinRequestError):
        target.unlink(missing_ok=True)
        return target
    raise RuntimePinRequestReadableError(
        f"the pending request parses as a valid request; cancel it by id instead "
        f"(fix the integrity problem first if one is reported): {target}"
    )


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
    "RuntimePinRequestReadableError",
    "clear_runtime_pin_request",
    "discard_unreadable_runtime_pin_request",
    "prospective_pinset_sha256",
    "read_runtime_pin_request",
    "runtime_pin_request_path",
    "utc_timestamp",
    "write_runtime_pin_request",
]
