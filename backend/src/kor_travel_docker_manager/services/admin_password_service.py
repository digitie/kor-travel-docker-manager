"""관리자 비밀번호를 ``.env`` 단일 키로만 회전한다 (KUM-M10 / 설계 P6).

``verify_admin_password``가 호출 시마다 ``os.environ``을 읽으므로 파일과 프로세스 환경을
함께 갱신하면 재기동 없이 즉시 적용된다. 세션 검증은 password hash를 보지 않아 진행 중
세션은 죽지 않는다.

``pinvi_database_role_credentials``의 **검증 논리만** 참고하고 함수는 재사용하지 않는다 —
그 모듈은 root 소유 설치본 ``.env``만 다루는 rebuild 전용 경로이고, 여기는 backend 실행
사용자가 자기 ``.env``를 고치는 다른 경계다.

재구축 가드는 없다. 종전 재구축은 journal에 ``.env`` 해시를 동결하고 재개 때 대조했으므로
미종결 journal이 있으면 비밀번호 변경을 막았지만, ADR-51 뒤 배포는 재개하지 않고 처음부터
다시 돌며 ``.env``를 동결하지 않는다 — 막을 것이 없다(ADR-51 B3에서 가드와 승인 입력을 지웠다).
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import stat
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any, Final

from dotenv import dotenv_values

from kor_travel_docker_manager.services.auth_service import (
    admin_username,
    hash_password_for_env,
    verify_admin_password,
)
from kor_travel_docker_manager.services.c6c_deployment import (
    c6c_deployment_lock,
    manager_mutation_lock_path,
)
from kor_travel_docker_manager.services.compose_service import get_env_path
from kor_travel_docker_manager.services.errors import ManagerMutationActiveError

logger = logging.getLogger(__name__)

ADMIN_PASSWORD_HASH_ENV: Final = "KTDM_ADMIN_PASSWORD_HASH"
# 임의 key=value 쓰기는 구현하지 않는다. 이 집합이 곧 경계다.
_ALLOWED_ENV_KEYS: Final[frozenset[str]] = frozenset({ADMIN_PASSWORD_HASH_ENV})
_MAX_ENV_BYTES: Final = 1_048_576
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_ENCODED_HASH = re.compile(r"^pbkdf2_sha256:[0-9]{4,8}:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$")
MIN_NEW_PASSWORD_LENGTH: Final = 12


class AdminPasswordError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _env_path(env_path: Path | None) -> Path:
    return env_path if env_path is not None else Path(get_env_path())


def _parse_dotenv(text: str) -> dict[str, str]:
    return {
        name: value
        for name, value in dotenv_values(stream=StringIO(text), interpolate=False).items()
        if value is not None
    }


# --- .env 단일 키 재작성 ------------------------------------------------------


def _assert_parent(parent: Path) -> None:
    try:
        metadata = parent.lstat()
    except OSError as exc:
        raise AdminPasswordError(
            "ENV_PARENT_UNSAFE", f".env 디렉터리를 확인할 수 없습니다: {parent}"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise AdminPasswordError(
            "ENV_PARENT_UNSAFE",
            f".env 디렉터리가 group/other 쓰기 가능하거나 디렉터리가 아닙니다: {parent}",
        )


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    """파일을 식별하는 튜플.

    크기와 ctime을 포함해야 **제자리 수정**을 감지한다. inode 기반 필드만 보면
    `vim`(backupcopy=yes)이나 `>` 리다이렉트처럼 같은 inode를 유지하며 내용을 바꾸는
    편집이 전부 통과하고, 우리 `os.replace`가 그 편집을 조용히 덮어쓴다.
    """

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_ctime_ns,
    )


def _read_env(path: Path) -> tuple[str, tuple[int, ...]]:
    """``.env``를 무결성 검사와 함께 읽고, 쓰기 전 대조할 identity를 함께 돌려준다."""

    _assert_parent(path.parent)
    try:
        before = path.lstat()
    except OSError as exc:
        raise AdminPasswordError(
            "ENV_UNREADABLE", f".env를 찾을 수 없습니다: {path}"
        ) from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise AdminPasswordError("ENV_UNREADABLE", f".env가 일반 파일이 아닙니다: {path}")
    if stat.S_IMODE(before.st_mode) != 0o600:
        raise AdminPasswordError(
            "ENV_MODE_UNSAFE",
            f".env의 권한이 0{stat.S_IMODE(before.st_mode):o}입니다(0600이어야 합니다).",
        )
    euid = getattr(os, "geteuid", lambda: before.st_uid)()
    if before.st_uid != euid:
        # 권한을 완화하라고 하지 않는다 — 그것이 이 파일의 유일한 보호다.
        raise AdminPasswordError(
            "ENV_NOT_WRITABLE",
            f".env가 uid {before.st_uid} 소유라 이 프로세스(uid {euid})가 쓸 수 "
            "없습니다. 권한을 완화하지 말고, backend를 해당 소유자 권한으로 재기동하거나 "
            "SSH에서 해시를 직접 교체하세요.",
            status_code=409,
        )
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise AdminPasswordError("ENV_UNREADABLE", f".env를 열 수 없습니다: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise AdminPasswordError(
                "ENV_CHANGED_DURING_READ", ".env가 읽는 도중 바뀌었습니다."
            )
        raw = os.read(descriptor, _MAX_ENV_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > _MAX_ENV_BYTES:
        raise AdminPasswordError("ENV_TOO_LARGE", ".env가 예상 범위를 벗어나게 큽니다.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdminPasswordError("ENV_NOT_UTF8", ".env가 UTF-8이 아닙니다.") from exc
    if "\x00" in text:
        raise AdminPasswordError("ENV_INVALID", ".env에 NUL 바이트가 있습니다.")
    return text, _identity(before)


def _rewrite_env_single_key(path: Path, name: str, value: str) -> None:
    """정확히 한 키만 바꿔 원자적으로 다시 쓴다.

    함수가 매핑을 받지 않으므로 임의 key=value 쓰기는 **표현할 수 없다.** 그 위에
    allowlist와 사후 대조를 얹는다.
    """

    if name not in _ALLOWED_ENV_KEYS:
        raise AdminPasswordError("ENV_KEY_NOT_ALLOWED", f"허용되지 않은 키입니다: {name}")
    text, identity = _read_env(path)
    lines = text.split("\n")
    matches = [
        index
        for index, line in enumerate(lines)
        if (assignment := _ASSIGNMENT.match(line)) is not None
        and assignment.group(1) == name
    ]
    if len(matches) > 1:
        raise AdminPasswordError(
            "ENV_DUPLICATE_ASSIGNMENT",
            f".env에 {name} 할당이 {len(matches)}개 있습니다. 어느 것이 유효한지 "
            "모호하므로 손으로 정리한 뒤 다시 시도하세요.",
        )
    trailing_newline = text.endswith("\n")
    if matches:
        lines[matches[0]] = f"{name}={value}"
    else:
        while lines and lines[-1] == "":
            lines.pop()
        lines.append(f"{name}={value}")
    new_text = "\n".join(lines)
    if trailing_newline and not new_text.endswith("\n"):
        new_text += "\n"

    # 사후 대조: 이 키 하나만 달라져야 한다. 정규식이 예상 밖의 줄을 건드렸다면
    # 여기서 걸리고, 아무것도 쓰지 않는다.
    before_values = _parse_dotenv(text)
    after_values = _parse_dotenv(new_text)
    if set(before_values) - {name} != set(after_values) - {name}:
        raise AdminPasswordError(
            "ENV_REWRITE_WOULD_CHANGE_OTHER_KEYS", ".env 재작성이 다른 키를 건드립니다."
        )
    differing = {
        key
        for key in set(before_values) | set(after_values)
        if before_values.get(key) != after_values.get(key)
    }
    if differing != {name}:
        raise AdminPasswordError(
            "ENV_REWRITE_WOULD_CHANGE_OTHER_KEYS",
            f".env 재작성이 예상 밖의 키를 바꿉니다: {', '.join(sorted(differing))}",
        )

    # GM-10: services/secure_state_file.py에 이 패턴의 정본이 있다. 이 자리는
    # 개별 소유권 정책 검토 없이 옮기지 않기로 결정돼 아직 남아 있다(docs/tasks.md).
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        if _identity(path.lstat()) != identity:
            raise AdminPasswordError(
                "ENV_CHANGED_BEFORE_WRITE", ".env가 쓰기 직전에 바뀌었습니다."
            )
        os.replace(temporary_path, path)
    except AdminPasswordError:
        temporary_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise AdminPasswordError("ENV_WRITE_FAILED", f".env를 쓰지 못했습니다: {exc}") from exc
    # 여기부터는 바이트가 이미 자리에 있다. 디렉터리 fsync 실패로 예외를 던지면
    # 호출자가 os.environ을 갱신하지 못해 파일과 프로세스가 어긋난다.
    try:
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        logger.warning("could not fsync the .env directory after a password change")
    finally:
        os.close(directory_fd)


def _rewrite_env_single_key_under_mutation_lock(path: Path, name: str, value: str) -> None:
    """``.env`` 재작성을 Manager mutation lock 안에서만 한다(ADR-51 C-2).

    legacy retire는 lock 아래에서 읽은 ``.env`` 바이트로 파일 전체를 다시 쓴다. 그 사이에
    끼어든 비밀번호 변경은 조용히 사라진다(lost update). 그래서 같은 lock을 잡는다.

    lock 경로는 **다시 쓸 그 ``.env``의 값**에서 정한다 — local이면 실행 사용자 ``$HOME``
    아래 개발 lock, 그 밖의 모드(미지정 포함, ADR-51 C-3)는 host 변경 lock ``G``다.
    프로세스 환경으로 채우지 않는다. 경합이면 409 ``MANAGER_MUTATION_ACTIVE``로 거절하고,
    파일은 한 바이트도 바뀌지 않는다. 그 거절을 ``AdminPasswordError``로 올리는 이유: 이 route는 모든 거절을
    감사에 남긴다(남지 않은 거절은 조사할 수 없다) — 맞는 자격증명으로 한 시도가 흔적 없이
    사라지면 안 된다(C-2 적대 리뷰).
    """

    text, identity = _read_env(path)
    try:
        with c6c_deployment_lock(manager_mutation_lock_path(_parse_dotenv(text))):
            # lock 경로를 고른 뒤 잡기 전까지 `.env`가 바뀌었다면 그 선택은 낡았다.
            if _read_env(path)[1] != identity:
                raise AdminPasswordError(
                    "ENV_CHANGED_BEFORE_WRITE", ".env가 쓰기 직전에 바뀌었습니다."
                )
            _rewrite_env_single_key(path, name, value)
    except ManagerMutationActiveError as exc:
        # 본문은 이 예외를 내지 않는다 — lock 획득의 경합뿐이다.
        raise AdminPasswordError("MANAGER_MUTATION_ACTIVE", str(exc), status_code=409) from exc


# --- 공개 진입점 --------------------------------------------------------------


def change_admin_password(
    *,
    current_password: str,
    new_password: str,
    env_path: Path | None = None,
) -> dict[str, Any]:
    path = _env_path(env_path)

    # PBKDF2는 31만 회 반복이다 — 한 번만 부르고 결과로 분기한다.
    outcome = verify_admin_password(admin_username(), current_password)
    if outcome == "misconfigured":
        raise AdminPasswordError(
            "AUTH_MISCONFIGURED",
            "관리자 인증 설정이 완전하지 않아 비밀번호를 바꿀 수 없습니다.",
            status_code=503,
        )
    if outcome != "ok":
        raise AdminPasswordError(
            "INVALID_CREDENTIALS", "현재 비밀번호가 일치하지 않습니다.", status_code=401
        )

    if len(new_password) < MIN_NEW_PASSWORD_LENGTH:
        raise AdminPasswordError(
            "NEW_PASSWORD_TOO_SHORT",
            f"새 비밀번호는 {MIN_NEW_PASSWORD_LENGTH}자 이상이어야 합니다.",
            status_code=422,
        )
    if any(character in new_password for character in ("\x00", "\r", "\n")):
        raise AdminPasswordError(
            "NEW_PASSWORD_INVALID",
            "새 비밀번호에 줄바꿈이나 NUL을 쓸 수 없습니다.",
            status_code=422,
        )
    if hmac.compare_digest(new_password, current_password):
        raise AdminPasswordError(
            "NEW_PASSWORD_UNCHANGED",
            "새 비밀번호가 현재 비밀번호와 같습니다.",
            status_code=422,
        )

    new_hash = hash_password_for_env(new_password)
    if _ENCODED_HASH.fullmatch(new_hash) is None:
        # 줄바꿈을 품은 값이 파일에 닿는 경로를 원천 차단한다.
        raise AdminPasswordError(
            "HASH_INVALID", "생성된 해시 형식이 올바르지 않습니다.", status_code=500
        )

    _rewrite_env_single_key_under_mutation_lock(path, ADMIN_PASSWORD_HASH_ENV, new_hash)
    # **파일이 먼저다.** env를 먼저 갱신하고 쓰기가 실패하면 재기동이 비밀번호를 조용히
    # 되돌린다 — 가장 나쁜 실패다. 반대 순서에서는 파일이 새 값이고 살아 있는 프로세스만
    # 옛 값을 받는데, 그것은 재기동으로 복구되는 방향이다.
    os.environ[ADMIN_PASSWORD_HASH_ENV] = new_hash
    return {"ok": True, "env_path": str(path)}


__all__ = [
    "ADMIN_PASSWORD_HASH_ENV",
    "MIN_NEW_PASSWORD_LENGTH",
    "AdminPasswordError",
    "change_admin_password",
]
