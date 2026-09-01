"""관리자 비밀번호를 ``.env`` 단일 키로만 회전한다 (KUM-M10 / 설계 P6).

``verify_admin_password``가 호출 시마다 ``os.environ``을 읽으므로 파일과 프로세스 환경을
함께 갱신하면 재기동 없이 즉시 적용된다. 세션 검증은 password hash를 보지 않아 진행 중
세션은 죽지 않는다.

``pinvi_database_role_credentials``의 **검증 논리만** 참고하고 함수는 재사용하지 않는다 —
그 모듈은 root 소유 설치본 ``.env``만 다루는 rebuild 전용 경로이고, 여기는 backend 실행
사용자가 자기 ``.env``를 고치는 다른 경계다.

**미종결 rebuild journal 가드가 왜 세 갈래인가**

resume은 journal의 ``environment_sha256``을 현재 ``.env`` 바이트와 대조한다. 비밀번호를
바꾸면 그 digest가 달라져 **진행 중이던 rebuild의 재개가 영구 차단된다.** 그래서 미종결
journal이 있으면 막아야 하는데, backend가 그것을 **항상 볼 수 있는 것은 아니다**:

journal은 ``rebuild-pinned``를 실행한 프로세스의 ``$HOME`` 아래 ``0700`` 디렉터리에 있고
``rebuild-pinned``는 root를 요구한다. backend가 비-root로 돌면 ``Path.home()``이 달라 같은
경로를 계산조차 못 하고, 계산해도 읽지 못한다. n150 운영 기동은 backend를 root 권한으로
띄우므로 실제로는 탐지가 동작하지만, 그것은 **배포 구성의 우연**이지 코드가 보장하는
성질이 아니다.

따라서 "확인 불가"는 "안전"이 아니라 별도의 fail-close 상태로 다룬다:

- ``not_rebuildable`` / ``no_journal`` — 통과. rebuild가 시작될 수도 재개될 수도 없거나,
  미종결 journal이 실제로 없다.
- ``unfinished_journal`` — **거부. 우회 경로 없음.** 증명된 사실이고, 증명됐다는 것은
  재개가 실제로 걸려 있다는 뜻이다.
- ``unverifiable`` / ``unknown`` — 명시적 승인 없이는 거부. 운영자가 SSH에서 확인한 뒤
  책임지고 진행하는 경로만 남긴다.
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
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.compose_service import get_env_path
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    load_deployment_mode,
    pinned_runtime_state_root,
    read_rebuild_journal,
)

logger = logging.getLogger(__name__)

ADMIN_PASSWORD_HASH_ENV: Final = "KTDM_ADMIN_PASSWORD_HASH"
# 임의 key=value 쓰기는 구현하지 않는다. 이 집합이 곧 경계다.
_ALLOWED_ENV_KEYS: Final[frozenset[str]] = frozenset({ADMIN_PASSWORD_HASH_ENV})
_MAX_ENV_BYTES: Final = 1_048_576
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_ENCODED_HASH = re.compile(r"^pbkdf2_sha256:[0-9]{4,8}:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$")
_JOURNAL_GLOB: Final = "pinned-runtime-rebuild-v8-*.json"
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


# --- 미종결 rebuild journal 가드 ---------------------------------------------


def pinned_rebuild_guard_state(*, env_path: Path | None = None) -> dict[str, Any]:
    """비밀번호 변경이 진행 중인 rebuild의 재개를 무효화하는지 판정한다.

    UI가 폼을 그리기 전에 먼저 읽는다 — 눌러 본 뒤에야 거부를 알게 하지 않는다.
    """

    path = _env_path(env_path)
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return _verdict(
            "unverifiable",
            f".env를 읽지 못해 진행 중인 재구축이 있는지 확인할 수 없습니다: {path}",
        )
    values = _parse_dotenv(text)
    try:
        mode = load_deployment_mode(values)
    except DeploymentContractError as exc:
        return _verdict("unknown", f"배포 모드를 판정할 수 없습니다: {exc}")
    if not mode.rebuildable:
        # 이 모드에서는 journal이 만들어지지도 재개되지도 않는다 — 시작과 재개 모두
        # `require_rebuildable_mode`를 지나므로 `.env` 변경이 무효화할 것이 없다.
        return _verdict(
            "not_rebuildable",
            "이 배포 모드에서는 재구축이 시작되거나 재개될 수 없어, 비밀번호 변경이 "
            "무효화할 재구축이 없습니다.",
        )
    try:
        state_root = pinned_runtime_state_root(values)
    except DeploymentContractError as exc:
        return _verdict("unknown", f"재구축 상태 경로를 계산할 수 없습니다: {exc}")
    try:
        metadata = state_root.lstat()
    except FileNotFoundError:
        return _verdict("no_journal", "진행 중인 재구축 기록이 없습니다.")
    except OSError as exc:
        return _verdict("unverifiable", f"재구축 상태 디렉터리를 읽을 수 없습니다: {exc}")
    euid = getattr(os, "geteuid", lambda: metadata.st_uid)()
    if metadata.st_uid != euid or stat.S_IMODE(metadata.st_mode) != 0o700:
        # 다른 사용자의 0700 디렉터리다. 못 읽는 것을 "없다"로 말하지 않는다.
        return _verdict(
            "unverifiable",
            "재구축 기록이 다른 사용자 소유라 이 프로세스가 확인할 수 없습니다: "
            f"{state_root}",
        )
    try:
        journals = sorted(state_root.glob(_JOURNAL_GLOB))
    except OSError as exc:
        return _verdict("unverifiable", f"재구축 기록 목록을 읽을 수 없습니다: {exc}")
    for journal_path in journals:
        try:
            journal = read_rebuild_journal(journal_path)
        except (DeploymentContractError, OSError) as exc:
            return _verdict(
                "unverifiable",
                f"재구축 기록을 해석할 수 없습니다({journal_path.name}): {exc}",
            )
        if journal.phase != "committed":
            return _verdict(
                "unfinished_journal",
                f"미종결 재구축 기록이 있습니다({journal_path.name}, 단계 "
                f"{journal.phase}). 지금 비밀번호를 바꾸면 그 재구축의 재개가 영구 "
                "차단됩니다.",
            )
    return _verdict("no_journal", "미종결 재구축 기록이 없습니다.")


def _journal_check_command(env_path: Path | None = None) -> str:
    """운영자가 SSH에서 실행할 확인 명령.

    화면에 `<COMPOSE_PROJECT_NAME>` 같은 placeholder를 넣으면 안 된다. 붙여넣으면
    존재하지 않는 경로를 조회해 `No such file or directory`가 나오고, 운영자는 그것을
    **"journal이 없다 = 안전"**으로 읽는다 — 살아 있는 재구축의 재개를 영구 차단하는
    선택을 그 오해 위에서 하게 된다. 경로를 아는 쪽이 명령을 만든다.
    """

    try:
        text = _env_path(env_path).read_bytes().decode("utf-8")
        state_root = pinned_runtime_state_root(_parse_dotenv(text))
    except (OSError, UnicodeDecodeError, DeploymentContractError):
        return "sudo -n backend/.venv/bin/ktdctl pin verify   # 경로를 해석하지 못했습니다"
    return f"sudo ls -l {state_root}/{_JOURNAL_GLOB}"


def _verdict(verdict: str, detail: str, *, env_path: Path | None = None) -> dict[str, Any]:
    blocking = verdict == "unfinished_journal"
    return {
        "verdict": verdict,
        "detail": detail,
        "requires_acknowledgement": verdict in {"unverifiable", "unknown"},
        "blocking": blocking,
        "check_command": _journal_check_command(env_path),
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


# --- 공개 진입점 --------------------------------------------------------------


def change_admin_password(
    *,
    current_password: str,
    new_password: str,
    acknowledge_pinned_rebuild_invalidation: bool = False,
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

    state = pinned_rebuild_guard_state(env_path=path)
    if state["verdict"] == "unfinished_journal":
        # 우회 경로를 두지 않는다. 증명된 미종결 journal은 재개가 실제로 걸려 있다는 뜻이다.
        raise AdminPasswordError("PINNED_REBUILD_JOURNAL_UNFINISHED", str(state["detail"]))
    if state["requires_acknowledgement"] and not acknowledge_pinned_rebuild_invalidation:
        raise AdminPasswordError(
            "PINNED_REBUILD_JOURNAL_UNVERIFIABLE",
            f"{state['detail']} 진행 중인 재구축이 없는지 SSH에서 확인한 뒤 명시적으로 "
            "승인해야 진행할 수 있습니다.",
        )

    new_hash = hash_password_for_env(new_password)
    if _ENCODED_HASH.fullmatch(new_hash) is None:
        # 줄바꿈을 품은 값이 파일에 닿는 경로를 원천 차단한다.
        raise AdminPasswordError(
            "HASH_INVALID", "생성된 해시 형식이 올바르지 않습니다.", status_code=500
        )

    _rewrite_env_single_key(path, ADMIN_PASSWORD_HASH_ENV, new_hash)
    # **파일이 먼저다.** env를 먼저 갱신하고 쓰기가 실패하면 재기동이 비밀번호를 조용히
    # 되돌린다 — 가장 나쁜 실패다. 반대 순서에서는 파일이 새 값이고 살아 있는 프로세스만
    # 옛 값을 받는데, 그것은 재기동으로 복구되는 방향이다.
    os.environ[ADMIN_PASSWORD_HASH_ENV] = new_hash
    return {
        "ok": True,
        "guard": state["verdict"],
        "acknowledged": bool(acknowledge_pinned_rebuild_invalidation),
        "env_path": str(path),
    }


__all__ = [
    "ADMIN_PASSWORD_HASH_ENV",
    "MIN_NEW_PASSWORD_LENGTH",
    "AdminPasswordError",
    "change_admin_password",
    "pinned_rebuild_guard_state",
]
