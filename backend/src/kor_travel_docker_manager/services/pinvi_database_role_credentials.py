"""PinVi 분리 DB 역할 credential의 root-only 초기화 경계.

`rebuild-pinned`가 새 PinVi schema를 만들기 전에만 사용한다. 기존 credential을
회전하거나 부분 보정하지 않으며, 여섯 값이 전부 없는 fresh 환경만 한 번
초기화한다.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from types import MappingProxyType

from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

_ROLE_ENVIRONMENT_NAMES = (
    "PINVI_APP_DB_USER",
    "PINVI_APP_DB_PASSWORD",
    "PINVI_APP_SCHEMA_OWNER",
    "PINVI_MIGRATION_OWNER",
    "PINVI_MIGRATOR_DB_USER",
    "PINVI_MIGRATOR_DB_PASSWORD",
)
_ROLE_NAME_ENVIRONMENT_NAMES = (
    "PINVI_APP_DB_USER",
    "PINVI_APP_SCHEMA_OWNER",
    "PINVI_MIGRATION_OWNER",
    "PINVI_MIGRATOR_DB_USER",
)
_ROLE_PASSWORD_ENVIRONMENT_NAMES = (
    "PINVI_APP_DB_PASSWORD",
    "PINVI_MIGRATOR_DB_PASSWORD",
)
ROLE_CREDENTIALS_REBIND_SOURCE_SHA256_ENV = (
    "KTDM_PINVI_ROLE_CREDENTIALS_REBIND_SOURCE_SHA256"
)
_MANAGED_ENVIRONMENT_NAMES = (
    *_ROLE_ENVIRONMENT_NAMES,
    ROLE_CREDENTIALS_REBIND_SOURCE_SHA256_ENV,
)
_GENERATED_ROLE_NAMES = {
    "PINVI_APP_DB_USER": "pinvi_application_runtime",
    "PINVI_APP_SCHEMA_OWNER": "pinvi_application_schema_owner",
    "PINVI_MIGRATION_OWNER": "pinvi_migration_owner",
    "PINVI_MIGRATOR_DB_USER": "pinvi_migrator_runtime",
}
_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TRUSTED_PINNED_RUNTIME_PROJECT_ROOT = Path("/opt/kor-travel-docker-manager")


@dataclass(frozen=True)
class _EnvironmentFileIdentity:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    nlink: int


def ensure_pinned_runtime_pinvi_role_credentials(
    env_path: Path,
    *,
    require_root: bool = True,
    expected_environment_bytes: bytes | None = None,
    rebind_source_sha256: str | None = None,
) -> Mapping[str, str]:
    """fresh root `.env`에만 PinVi 분리 역할 credential을 한 번 만든다.

    모든 역할 값이 이미 있으면 기존 값을 그대로 반환한다. 하나라도 없거나
    비어 있으면 기존 authority가 불완전한 것이므로 절대 추측하거나 덮어쓰지
    않고 fail-close한다.
    """

    if require_root:
        trusted_root = trusted_pinned_runtime_project_root()
        if env_path != trusted_root / ".env":
            raise DeploymentContractError(
                "PinVi root environment path is not the trusted rebuild environment"
            )
    original, identity = _read_safe_root_environment(
        env_path,
        require_root=require_root,
    )
    if (
        expected_environment_bytes is not None
        and not hmac.compare_digest(original, expected_environment_bytes)
    ):
        raise DeploymentContractError(
            "PinVi root environment changed before role credential initialization"
        )
    values = _read_dotenv_values(original)
    _assert_no_duplicate_assignments(original)
    declared = tuple(name in values for name in _ROLE_ENVIRONMENT_NAMES)
    if not any(declared):
        if ROLE_CREDENTIALS_REBIND_SOURCE_SHA256_ENV in values:
            raise DeploymentContractError(
                "PinVi role credential rebind source is invalid"
            )
        if (
            rebind_source_sha256 is not None
            and (
                _SHA256.fullmatch(rebind_source_sha256) is None
                or not hmac.compare_digest(
                    rebind_source_sha256, hashlib.sha256(original).hexdigest()
                )
            )
        ):
            raise DeploymentContractError(
                "PinVi role credential rebind source is invalid"
            )
        credentials = _new_credentials(values)
        updates: dict[str, str] = dict(credentials)
        if rebind_source_sha256 is not None:
            updates[ROLE_CREDENTIALS_REBIND_SOURCE_SHA256_ENV] = rebind_source_sha256
        updated = _apply_dotenv_updates(
            original,
            updates,
        )
        _write_atomic(
            env_path,
            updated,
            expected_identity=identity,
            require_root=require_root,
        )
        return MappingProxyType(credentials)
    if not all(declared) or any(not values[name] for name in _ROLE_ENVIRONMENT_NAMES):
        raise DeploymentContractError(
            "PinVi database role credentials are partially configured"
        )

    credentials = {name: values[name] for name in _ROLE_ENVIRONMENT_NAMES}
    _validate_credentials(credentials, values)
    rebind_source_environment_sha256(values)
    return MappingProxyType(credentials)


def trusted_pinned_runtime_project_root() -> Path:
    """root `rebuild-pinned`가 쓸 수 있는 유일한 trusted release root를 반환한다."""

    raw_root = _TRUSTED_PINNED_RUNTIME_PROJECT_ROOT
    try:
        metadata = raw_root.lstat()
    except OSError as exc:
        raise DeploymentContractError(
            "trusted PinVi rebuild project root cannot be inspected"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise DeploymentContractError(
            "trusted PinVi rebuild project root has unsafe ownership or mode"
        )
    root = raw_root.resolve(strict=True)
    if root != raw_root:
        raise DeploymentContractError("trusted PinVi rebuild project root is not canonical")
    return root


def rebind_source_environment_sha256(values: Mapping[str, str]) -> str | None:
    """fresh role 생성 직전 환경 digest를 secret 없이 반환한다."""

    value = values.get(ROLE_CREDENTIALS_REBIND_SOURCE_SHA256_ENV)
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise DeploymentContractError("PinVi role credential rebind source is invalid")
    return value


def pinvi_role_credentials_are_all_undeclared(values: Mapping[str, str]) -> bool:
    """fresh write 전 여섯 role authority가 정말 전부 없는지 판정한다."""

    return all(name not in values for name in _ROLE_ENVIRONMENT_NAMES)


def _new_credentials(root_values: Mapping[str, str]) -> dict[str, str]:
    credentials = {
        **_GENERATED_ROLE_NAMES,
        "PINVI_APP_DB_PASSWORD": secrets.token_urlsafe(48),
        "PINVI_MIGRATOR_DB_PASSWORD": secrets.token_urlsafe(48),
    }
    _validate_credentials(credentials, root_values)
    return credentials


def _validate_credentials(
    credentials: Mapping[str, str], root_values: Mapping[str, str]
) -> None:
    names = tuple(credentials[name] for name in _ROLE_NAME_ENVIRONMENT_NAMES)
    passwords = tuple(credentials[name] for name in _ROLE_PASSWORD_ENVIRONMENT_NAMES)
    root_user = root_values.get("PINVI_POSTGRES_USER", "pinvi")
    root_password = root_values.get("PINVI_POSTGRES_PASSWORD", "")
    if (
        any(
            not isinstance(value, str)
            or not value
            or "\x00" in value
            or "\r" in value
            or "\n" in value
            for value in credentials.values()
        )
        or any(_ROLE_NAME.fullmatch(name) is None for name in names)
        or not isinstance(root_user, str)
        or not root_user
        or len({root_user, *names}) != len(names) + 1
        or not isinstance(root_password, str)
        or not root_password
        or len(set(passwords)) != len(passwords)
        or any(hmac.compare_digest(password, root_password) for password in passwords)
    ):
        raise DeploymentContractError("PinVi database role credentials are invalid")


def _assert_safe_root_environment(
    metadata: os.stat_result,
    *,
    require_root: bool,
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (require_root and metadata.st_uid != 0)
    ):
        raise DeploymentContractError("PinVi root environment has unsafe ownership or mode")


def _read_safe_root_environment(
    path: Path,
    *,
    require_root: bool,
) -> tuple[bytes, _EnvironmentFileIdentity]:
    _assert_safe_environment_parent(path.parent, require_root=require_root)
    try:
        before = path.lstat()
    except OSError as exc:
        raise DeploymentContractError("PinVi root environment cannot be inspected") from exc
    _assert_safe_root_environment(before, require_root=require_root)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int) or nofollow == 0:
        raise DeploymentContractError("PinVi root environment requires O_NOFOLLOW")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | nofollow)
        opened = os.fstat(descriptor)
        _assert_safe_root_environment(opened, require_root=require_root)
        identity = _environment_identity(opened)
        if identity != _environment_identity(before):
            raise DeploymentContractError("PinVi root environment changed while opening")
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            payload.extend(chunk)
        after = path.lstat()
        if identity != _environment_identity(after):
            raise DeploymentContractError("PinVi root environment changed while reading")
        return bytes(payload), identity
    except OSError as exc:
        raise DeploymentContractError("PinVi root environment cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _assert_safe_environment_parent(path: Path, *, require_root: bool) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DeploymentContractError("PinVi root environment parent cannot be inspected") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (require_root and metadata.st_uid != 0)
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise DeploymentContractError("PinVi root environment parent is unsafe")


def _environment_identity(metadata: os.stat_result) -> _EnvironmentFileIdentity:
    return _EnvironmentFileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        nlink=metadata.st_nlink,
    )


def _read_dotenv_values(payload: bytes) -> dict[str, str]:
    try:
        parsed = dotenv_values(
            stream=StringIO(payload.decode("utf-8")), interpolate=False
        )
    except (UnicodeError, ValueError) as exc:
        raise DeploymentContractError("PinVi root environment cannot be parsed") from exc
    return {name: value or "" for name, value in parsed.items() if isinstance(name, str)}


def _assert_no_duplicate_assignments(payload: bytes) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise DeploymentContractError("PinVi root environment cannot be parsed") from exc
    seen: set[str] = set()
    for line in text.splitlines():
        name = _dotenv_assignment_name(line)
        if name is None or name not in _MANAGED_ENVIRONMENT_NAMES:
            continue
        if name in seen:
            raise DeploymentContractError(
                "PinVi root environment has duplicate role credentials"
            )
        seen.add(name)


def _dotenv_assignment_name(line: str) -> str | None:
    match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
    return match.group(1) if match is not None else None


def _apply_dotenv_updates(original: bytes, updates: Mapping[str, str]) -> bytes:
    try:
        text = original.decode("utf-8")
    except UnicodeError as exc:
        raise DeploymentContractError("PinVi root environment cannot be decoded") from exc
    if "\x00" in text:
        raise DeploymentContractError("PinVi root environment is invalid")
    seen: set[str] = set()
    lines: list[str] = []
    for line in text.splitlines():
        name = _dotenv_assignment_name(line)
        if name is not None and name in updates:
            seen.add(name)
            lines.append(f"{name}={_quote_dotenv_value(updates[name])}")
        else:
            lines.append(line)
    for name in updates:
        if name not in seen:
            lines.append(f"{name}={_quote_dotenv_value(updates[name])}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _quote_dotenv_value(value: str) -> str:
    if "\x00" in value or "\r" in value or "\n" in value:
        raise DeploymentContractError("PinVi database role credentials are invalid")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _write_atomic(
    path: Path,
    payload: bytes,
    *,
    expected_identity: _EnvironmentFileIdentity,
    require_root: bool,
) -> None:
    temporary: Path | None = None
    replaced = False
    try:
        _assert_safe_environment_parent(path.parent, require_root=require_root)
        current = path.lstat()
        _assert_safe_root_environment(current, require_root=require_root)
        if _environment_identity(current) != expected_identity:
            raise DeploymentContractError(
                "PinVi root environment changed before role credential initialization"
            )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        current = path.lstat()
        _assert_safe_root_environment(current, require_root=require_root)
        if _environment_identity(current) != expected_identity:
            raise DeploymentContractError(
                "PinVi root environment changed before role credential initialization"
            )
        os.replace(temporary, path)
        replaced = True
        replacement = path.lstat()
        _assert_safe_root_environment(replacement, require_root=require_root)
        _fsync_directory(path.parent)
    except (DeploymentContractError, OSError) as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        if replaced:
            raise DeploymentContractError(
                "PinVi root environment durability is uncertain"
            ) from exc
        raise DeploymentContractError("PinVi root environment cannot be updated atomically") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise DeploymentContractError("PinVi root environment directory cannot be synchronized") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise DeploymentContractError("PinVi root environment directory cannot be synchronized") from exc
    finally:
        os.close(descriptor)
