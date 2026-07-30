from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import hmac
import json
import os
import pwd
import re
import secrets
import stat
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
    assert_manager_mutation_allowed,
    load_c6c_deployment_config_from_environment,
    load_pair_manifest,
)

MAP_UI_PASSWORD_ENV = "KTDM_C6C_MAP_UI_ADMIN_PASSWORD"
MAP_UI_PASSWORD_HASH_ENV = "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH"
MAP_UI_SESSION_SECRET_ENV = "KOR_TRAVEL_MAP_UI_SESSION_SECRET"
MAP_UI_USERNAME_ENV = "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME"
MAP_UI_IMAGE_ENV = "KOR_TRAVEL_MAP_UI_IMAGE"
MAP_UI_CONTAINER_ENV = "KOR_TRAVEL_MAP_UI_CONTAINER"
COMPOSE_PROJECT_ENV = "COMPOSE_PROJECT_NAME"
PROD_MAP_UI_URL_ENV = "KTDM_PROD_URL_MAP"
DEPLOYMENT_ENV = "KTDM_DEPLOYMENT_ENVIRONMENT"
ROTATED_ENV_NAMES = (
    MAP_UI_PASSWORD_ENV,
    MAP_UI_PASSWORD_HASH_ENV,
    MAP_UI_SESSION_SECRET_ENV,
)
_PROCESS_ENV_OVERRIDE_DENYLIST = frozenset(
    {
        *ROTATED_ENV_NAMES,
        MAP_UI_USERNAME_ENV,
        MAP_UI_CONTAINER_ENV,
        MAP_UI_IMAGE_ENV,
        "COMPOSE_FILE",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE",
        "KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE",
    }
)
_MAP_UI_SERVICE = "kor-travel-map-ui"
_MAP_UI_CONTAINER = "kor-travel-map-ui-latest"
_MAP_UI_PROTECTED_PATH = "/ops/datasets"
_NON_UI_CONTAINERS = (
    "kor-travel-map-api-latest",
    "kor-travel-map-dagster-latest",
    "kor-travel-map-dagster-daemon-latest",
    "pinvi-api-latest",
)
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

MAP_PBKDF2_ALGORITHM = "pbkdf2_sha256"
MAP_PBKDF2_ITERATIONS = 310_000
MAP_PBKDF2_SALT_BYTES = 16
MAP_PBKDF2_DIGEST_BYTES = 32
_MAP_PBKDF2_PATTERN = re.compile(
    r"^pbkdf2_sha256\$(\d+)\$([A-Za-z0-9_-]+)\$([A-Za-z0-9_-]+)$"
)


@dataclass(frozen=True)
class MapUiAuthRotationResult:
    success: bool
    returncode: int
    phase: str
    audit_path: str | None = None
    journal_path: str | None = None
    rollback_state: str | None = None
    checks: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""

    def as_process_result(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "returncode": self.returncode,
            "phase": self.phase,
            "audit_path": self.audit_path,
            "journal_path": self.journal_path,
            "rollback_state": self.rollback_state,
            "checks": list(self.checks),
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[
    [list[str], Path, Mapping[str, str], str | None, int],
    CommandResult,
]


@dataclass(frozen=True)
class RotationPaths:
    project_root: Path
    compose_path: Path
    env_path: Path
    state_dir: Path
    manifest_path: Path
    lock_path: Path
    rotation_dir: Path
    journal_path: Path
    backup_path: Path
    audit_path: Path


@dataclass(frozen=True)
class EnvSpan:
    key: str
    value: str
    index: int


@dataclass(frozen=True)
class EnvDocument:
    lines: tuple[str, ...]
    spans: Mapping[str, EnvSpan]
    original_bytes: bytes
    stat_result: os.stat_result

    def rewritten(self, values: Mapping[str, str]) -> bytes:
        lines = list(self.lines)
        for key, value in values.items():
            span = self.spans[key]
            newline = "\n" if lines[span.index].endswith("\n") else ""
            lines[span.index] = f"{key}='{_single_quote_env_value(value)}'{newline}"
        return "".join(lines).encode("utf-8")


def generate_map_pbkdf2_hash(password: str, *, salt: bytes | None = None) -> str:
    """Map UI가 요구하는 exact PBKDF2 hash 형식을 생성한다."""

    _validate_plaintext_password(password, label="new Map UI password")
    salt_bytes = secrets.token_bytes(MAP_PBKDF2_SALT_BYTES) if salt is None else salt
    if len(salt_bytes) != MAP_PBKDF2_SALT_BYTES:
        raise DeploymentContractError("Map UI password hash salt size is invalid")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        MAP_PBKDF2_ITERATIONS,
        dklen=MAP_PBKDF2_DIGEST_BYTES,
    )
    return (
        f"{MAP_PBKDF2_ALGORITHM}${MAP_PBKDF2_ITERATIONS}$"
        f"{_b64url_no_padding(salt_bytes)}${_b64url_no_padding(digest)}"
    )


def verify_map_pbkdf2_hash(password: str, encoded: str) -> bool:
    """Map UI PBKDF2 hash를 독립 검증한다."""

    match = _MAP_PBKDF2_PATTERN.fullmatch(encoded)
    if match is None:
        return False
    try:
        iterations = int(match.group(1))
        salt = _b64url_decode_no_padding(match.group(2))
        expected = _b64url_decode_no_padding(match.group(3))
    except (ValueError, binascii.Error):
        return False
    if (
        iterations != MAP_PBKDF2_ITERATIONS
        or len(salt) != MAP_PBKDF2_SALT_BYTES
        or len(expected) != MAP_PBKDF2_DIGEST_BYTES
    ):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=MAP_PBKDF2_DIGEST_BYTES,
    )
    return hmac.compare_digest(actual, expected)


def rotate_map_ui_auth(
    *,
    current_password: str,
    new_password: str,
    project_root: str | None = None,
    compose_path: str | None = None,
    env_path: str | None = None,
    command_runner: CommandRunner | None = None,
    require_root: bool = True,
) -> MapUiAuthRotationResult:
    """Audited production Map UI credential rotation entrypoint."""

    _validate_plaintext_password(current_password, label="current Map UI password")
    _validate_plaintext_password(new_password, label="new Map UI password")
    if current_password == new_password:
        raise DeploymentContractError(
            "new Map UI password must differ from the current password"
        )

    runner = _default_command_runner if command_runner is None else command_runner
    resolved_project_root = _project_root(project_root)
    resolved_env_path = Path(env_path or resolved_project_root / ".env").resolve(strict=False)
    resolved_compose_path = Path(
        compose_path or resolved_project_root / "docker-compose.yml"
    ).resolve(strict=False)
    _reject_process_env_overrides()
    env_values = _load_env_file_values(resolved_env_path)
    assert_manager_mutation_allowed(environment=env_values)
    config = load_c6c_deployment_config_from_environment(env_values)
    if not config.production:
        raise DeploymentContractError("Map UI auth rotation is production-only")
    if require_root and os.geteuid() != 0:
        raise DeploymentContractError("Map UI auth rotation requires root privileges")
    if not verify_map_pbkdf2_hash(current_password, env_values[MAP_UI_PASSWORD_HASH_ENV]):
        raise DeploymentContractError("current Map UI password does not match the frozen hash")
    origin = _production_map_ui_origin(env_values)
    paths = _rotation_paths(
        project_root=resolved_project_root,
        compose_path=resolved_compose_path,
        env_path=resolved_env_path,
        env_values=env_values,
    )

    with _hardened_lock(paths.lock_path):
        paths.rotation_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _chmod_private_dir(paths.rotation_dir)
        recovered = _recover_pending_journal(
            paths=paths,
            env_values=env_values,
            runner=runner,
        )
        if recovered is not None:
            return recovered

        env_document = _read_strict_env_document(paths.env_path)
        manifest = load_pair_manifest(str(paths.manifest_path))
        active_ui_image = manifest.active.map_ui_image_id
        before_ui = _inspect_container(env_values.get(MAP_UI_CONTAINER_ENV, _MAP_UI_CONTAINER))
        _validate_map_ui_container(before_ui, env_values, active_ui_image)
        before_ui_signature = _ui_stable_signature(before_ui)
        before_non_ui = _non_ui_snapshot()
        old_cookie = _verify_auth_lifecycle(
            origin=origin,
            username=env_values[MAP_UI_USERNAME_ENV],
            password=current_password,
            expect_cookie_reject=None,
        )

        operation_id = str(uuid.uuid4())
        new_hash = generate_map_pbkdf2_hash(new_password)
        new_session_secret = _new_session_secret()
        new_values = {
            MAP_UI_PASSWORD_ENV: new_password,
            MAP_UI_PASSWORD_HASH_ENV: new_hash,
            MAP_UI_SESSION_SECRET_ENV: new_session_secret,
        }
        new_env_bytes = env_document.rewritten(new_values)
        _write_journal(
            paths.journal_path,
            {
                "operation_id": operation_id,
                "phase": "prepared",
                "old_env_sha256": _sha256(env_document.original_bytes),
                "new_env_sha256": _sha256(new_env_bytes),
                "prepared_at": _utc_now(),
            },
        )
        _write_secret_backup(paths.backup_path, env_document.original_bytes)

        try:
            _atomic_replace_file(
                paths.env_path,
                new_env_bytes,
                stat_result=env_document.stat_result,
            )
            _write_journal(paths.journal_path, {"operation_id": operation_id, "phase": "env_new"})
            _compose_config_quiet(paths, active_ui_image, runner)
            _compose_recreate_map_ui(paths, active_ui_image, runner)
            _write_journal(
                paths.journal_path,
                {"operation_id": operation_id, "phase": "ui_new_healthy"},
            )
            after_ui = _inspect_container(env_values.get(MAP_UI_CONTAINER_ENV, _MAP_UI_CONTAINER))
            _validate_map_ui_container(after_ui, {**env_values, **new_values}, active_ui_image)
            if _ui_stable_signature(after_ui) != before_ui_signature:
                raise DeploymentContractError("Map UI runtime config drifted during rotation")
            _assert_non_ui_unchanged(before_non_ui)
            _assert_plaintext_absent(after_ui, current_password, new_password)
            _verify_auth_lifecycle(
                origin=origin,
                username=env_values[MAP_UI_USERNAME_ENV],
                password=new_password,
                expect_cookie_reject=old_cookie,
            )
            _write_journal(
                paths.journal_path,
                {"operation_id": operation_id, "phase": "login_verified"},
            )
            _write_audit(
                paths.audit_path,
                {
                    "operation_id": operation_id,
                    "result": "committed",
                    "checks": [
                        "current_hash_verified",
                        "ui_only_recreated",
                        "runtime_auth_verified",
                        "old_session_rejected",
                        "non_ui_unchanged",
                    ],
                    "recorded_at": _utc_now(),
                },
            )
            _unlink_private(paths.backup_path)
            _unlink_private(paths.journal_path)
            return MapUiAuthRotationResult(
                success=True,
                returncode=0,
                phase="committed",
                audit_path=str(paths.audit_path),
                checks=[
                    "current_hash_verified",
                    "ui_only_recreated",
                    "runtime_auth_verified",
                    "old_session_rejected",
                    "non_ui_unchanged",
                ],
            )
        except Exception as exc:
            return _rollback_after_failure(
                paths=paths,
                env_values=env_values,
                active_ui_image=active_ui_image,
                runner=runner,
                original_error=exc,
                operation_id=operation_id,
            )


def _validate_plaintext_password(password: str, *, label: str) -> None:
    if not isinstance(password, str) or len(password) < 12:
        raise DeploymentContractError(f"{label} is too short")
    if any(character.isspace() for character in password):
        raise DeploymentContractError(f"{label} must not contain whitespace")


def _b64url_no_padding(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode_no_padding(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _project_root(configured_root: str | None = None) -> Path:
    configured = (
        configured_root
        if configured_root is not None
        else os.environ.get("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", "").strip()
    )
    if configured:
        root = Path(configured).resolve(strict=True)
        if not (root / "docker-compose.yml").is_file():
            raise DeploymentContractError(
                "KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT must contain docker-compose.yml"
            )
        return root
    cwd_root = Path.cwd().resolve(strict=False)
    if (cwd_root / "docker-compose.yml").is_file():
        return cwd_root
    source_root = Path(__file__).resolve().parents[4]
    if (source_root / "docker-compose.yml").is_file():
        return source_root
    raise DeploymentContractError(
        "Map UI auth rotation must run from the canonical manager checkout or set "
        "KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT"
    )


def _load_env_file_values(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        raise DeploymentContractError("canonical manager .env is missing")
    values = {
        key: value or ""
        for key, value in dotenv_values(env_path).items()
        if isinstance(key, str)
    }
    missing = [name for name in ROTATED_ENV_NAMES if name not in values]
    missing.extend(
        name
        for name in (MAP_UI_USERNAME_ENV, DEPLOYMENT_ENV, COMPOSE_PROJECT_ENV)
        if name not in values
    )
    if missing:
        raise DeploymentContractError("canonical manager .env is missing Map UI auth values")
    return values


def _reject_process_env_overrides() -> None:
    present = sorted(name for name in _PROCESS_ENV_OVERRIDE_DENYLIST if name in os.environ)
    if present:
        raise DeploymentContractError(
            "Map UI auth rotation requires canonical .env without process env overrides"
        )


def _production_map_ui_origin(values: Mapping[str, str]) -> str:
    origin = values.get(PROD_MAP_UI_URL_ENV, "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(origin)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise DeploymentContractError("production Map UI URL must be an exact HTTPS origin")
    return origin


def _rotation_paths(
    *,
    project_root: Path,
    compose_path: Path,
    env_path: Path,
    env_values: Mapping[str, str],
) -> RotationPaths:
    if not compose_path.exists() or compose_path.name != "docker-compose.yml":
        raise DeploymentContractError("canonical docker-compose.yml is missing")
    env_stat = env_path.stat()
    owner_home = Path(pwd.getpwuid(env_stat.st_uid).pw_dir).resolve(strict=True)
    project_name = env_values.get(COMPOSE_PROJECT_ENV, "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", project_name):
        raise DeploymentContractError("COMPOSE_PROJECT_NAME must be explicit and canonical")
    default_root = owner_home / ".local" / "state" / "kor-travel-docker-manager"
    if env_values.get("KTDM_C6C_STATE_ROOT", "").strip():
        configured = Path(env_values["KTDM_C6C_STATE_ROOT"]).resolve(strict=False)
        if configured != default_root:
            raise DeploymentContractError("production C6c state root is fixed")
    if env_values.get("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", "").strip():
        raise DeploymentContractError("production compatible-pair manifest path is fixed")
    if env_values.get("KTDM_C6C_DEPLOYMENT_LOCK", "").strip():
        raise DeploymentContractError("production C6c lock path is fixed")
    state_dir = default_root / project_name
    rotation_dir = state_dir / "map-ui-auth-rotation"
    return RotationPaths(
        project_root=project_root,
        compose_path=compose_path,
        env_path=env_path,
        state_dir=state_dir,
        manifest_path=state_dir / "compatible-pair-v4.json",
        lock_path=default_root / "global-mutation.lock",
        rotation_dir=rotation_dir,
        journal_path=rotation_dir / "journal.json",
        backup_path=rotation_dir / "env.backup",
        audit_path=rotation_dir / "audit.jsonl",
    )


@contextmanager
def _hardened_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _chmod_private_dir(path.parent)
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise DeploymentContractError("cannot acquire C6c global mutation lock") from exc
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_nlink != 1
            or stat.S_IMODE(st.st_mode) != 0o600
        ):
            raise DeploymentContractError("C6c global mutation lock is unsafe")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentContractError(
                "another C6c compatible-pair operation is already active"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_strict_env_document(env_path: Path) -> EnvDocument:
    st = env_path.stat()
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_nlink != 1
        or stat.S_IMODE(st.st_mode) & 0o077
    ):
        raise DeploymentContractError("canonical manager .env must be a private regular file")
    raw = env_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentContractError("canonical manager .env must be UTF-8") from exc
    lines = tuple(text.splitlines(keepends=True))
    found: dict[str, EnvSpan] = {}
    for index, line in enumerate(lines):
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if key in ROTATED_ENV_NAMES:
            if key in found:
                raise DeploymentContractError("canonical manager .env has duplicate Map UI auth keys")
            found[key] = EnvSpan(key=key, value=value, index=index)
    if set(found) != set(ROTATED_ENV_NAMES):
        raise DeploymentContractError("canonical manager .env is missing Map UI auth keys")
    return EnvDocument(
        lines=lines,
        spans=found,
        original_bytes=raw,
        stat_result=st,
    )


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.rstrip("\r\n")
    if not stripped or stripped.lstrip().startswith("#"):
        return None
    if stripped.startswith("export "):
        raise DeploymentContractError("Map UI auth keys must not use export syntax")
    key, separator, value_text = stripped.partition("=")
    if separator != "=":
        return None
    if key != key.strip() or not _ENV_KEY_RE.fullmatch(key):
        raise DeploymentContractError("canonical manager .env contains an invalid key")
    if value_text.startswith("'") and value_text.endswith("'") and len(value_text) >= 2:
        value = value_text[1:-1]
    elif value_text.startswith('"') or value_text.endswith('"'):
        raise DeploymentContractError("Map UI auth keys must use literal single quotes or plain values")
    else:
        if value_text != value_text.strip():
            raise DeploymentContractError("Map UI auth key values must not carry outer whitespace")
        value = value_text
    return key, value


def _single_quote_env_value(value: str) -> str:
    if "\r" in value or "\n" in value or "'" in value:
        raise DeploymentContractError("Map UI auth value cannot be represented safely")
    return value


def _atomic_replace_file(path: Path, data: bytes, *, stat_result: os.stat_result) -> None:
    tmp_fd: int | None = None
    tmp_name: str | None = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        os.fchown(tmp_fd, stat_result.st_uid, stat_result.st_gid)
        os.fchmod(tmp_fd, 0o600)
        with os.fdopen(tmp_fd, "wb", closefd=True) as handle:
            tmp_fd = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
        _fsync_directory(path.parent)
    finally:
        if tmp_fd is not None:
            os.close(tmp_fd)
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def _write_secret_backup(path: Path, data: bytes) -> None:
    _write_private_file(path, data)


def _write_journal(path: Path, payload: Mapping[str, Any]) -> None:
    _write_private_file(
        path,
        (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )


def _write_audit(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _chmod_private_dir(path.parent)
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        line = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n"
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def _write_private_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _chmod_private_dir(path.parent)
    flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def _unlink_private(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _chmod_private_dir(path: Path) -> None:
    os.chmod(path, 0o700)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _compose_config_quiet(
    paths: RotationPaths,
    active_ui_image: str,
    runner: CommandRunner,
) -> None:
    result = runner(
        [
            "docker",
            "compose",
            "--env-file",
            str(paths.env_path),
            *_compose_file_args(paths),
            "config",
            "--quiet",
        ],
        paths.project_root,
        _sanitized_child_env({MAP_UI_IMAGE_ENV: active_ui_image}),
        None,
        120,
    )
    if result.returncode != 0:
        raise DeploymentContractError("docker compose config validation failed")


def _compose_recreate_map_ui(
    paths: RotationPaths,
    active_ui_image: str,
    runner: CommandRunner,
) -> None:
    result = runner(
        [
            "docker",
            "compose",
            "--env-file",
            str(paths.env_path),
            *_compose_file_args(paths),
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "--no-build",
            "--pull",
            "never",
            "--wait",
            "--wait-timeout",
            "120",
            _MAP_UI_SERVICE,
        ],
        paths.project_root,
        _sanitized_child_env({MAP_UI_IMAGE_ENV: active_ui_image}),
        None,
        180,
    )
    if result.returncode != 0:
        raise DeploymentContractError("Map UI recreate failed")


def _compose_file_args(paths: RotationPaths) -> list[str]:
    args = ["-f", str(paths.compose_path)]
    override_path = paths.compose_path.with_name("docker-compose.override.yml")
    if override_path.exists():
        try:
            resolved_override = override_path.resolve(strict=True)
        except OSError as exc:
            raise DeploymentContractError("canonical compose override is invalid") from exc
        if (
            not resolved_override.is_file()
            or resolved_override.parent != paths.compose_path.parent
        ):
            raise DeploymentContractError("canonical compose override is invalid")
        args.extend(["-f", str(resolved_override)])
    return args


def _default_command_runner(
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    stdin: str | None,
    timeout: int,
) -> CommandResult:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            env=dict(env),
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentContractError("managed command failed") from exc
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _sanitized_child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    path = os.environ.get("PATH", "/usr/bin:/bin")
    env = {"PATH": path, "HOME": "/root", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    if extra:
        env.update(extra)
    forbidden = set(ROTATED_ENV_NAMES)
    if forbidden.intersection(env):
        raise DeploymentContractError("child environment contains a Map UI auth secret")
    return env


def _inspect_container(container_name: str) -> Mapping[str, Any]:
    try:
        completed = subprocess.run(
            ["docker", "inspect", container_name],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentContractError("cannot inspect runtime container") from exc
    if completed.returncode != 0:
        raise DeploymentContractError("cannot inspect runtime container")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DeploymentContractError("docker inspect returned invalid JSON") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], Mapping):
        raise DeploymentContractError("docker inspect returned invalid container metadata")
    return payload[0]


def _validate_map_ui_container(
    inspect_payload: Mapping[str, Any],
    env_values: Mapping[str, str],
    active_ui_image: str,
) -> None:
    labels = _mapping_at(inspect_payload, "Config", "Labels")
    state = _mapping_at(inspect_payload, "State")
    if inspect_payload.get("Image") != active_ui_image:
        raise DeploymentContractError("running Map UI image differs from active manifest")
    if labels.get("com.docker.compose.service") != _MAP_UI_SERVICE:
        raise DeploymentContractError("running Map UI service identity is invalid")
    if labels.get("com.docker.compose.project") != "kor-travel-docker-manager":
        raise DeploymentContractError("running Map UI compose project is invalid")
    if state.get("Running") is not True:
        raise DeploymentContractError("running Map UI is not running")
    health = _mapping_at(state, "Health").get("Status", "")
    if health not in {"", "healthy"}:
        raise DeploymentContractError("running Map UI is not healthy")
    environment = _runtime_env_dict(inspect_payload)
    expected = {
        MAP_UI_USERNAME_ENV: env_values[MAP_UI_USERNAME_ENV],
        MAP_UI_PASSWORD_HASH_ENV: env_values[MAP_UI_PASSWORD_HASH_ENV],
        MAP_UI_SESSION_SECRET_ENV: env_values[MAP_UI_SESSION_SECRET_ENV],
    }
    for key, value in expected.items():
        if not hmac.compare_digest(environment.get(key, ""), value):
            raise DeploymentContractError("running Map UI auth env differs from canonical .env")
    if MAP_UI_PASSWORD_ENV in environment:
        raise DeploymentContractError("Map UI container contains manager-only plaintext")


def _runtime_env_dict(inspect_payload: Mapping[str, Any]) -> dict[str, str]:
    env_items = _mapping_at(inspect_payload, "Config").get("Env", [])
    if not isinstance(env_items, list):
        raise DeploymentContractError("container environment metadata is invalid")
    result: dict[str, str] = {}
    for item in env_items:
        if not isinstance(item, str) or "=" not in item:
            raise DeploymentContractError("container environment metadata is invalid")
        key, value = item.split("=", 1)
        if key in result:
            raise DeploymentContractError("container has duplicate environment variables")
        result[key] = value
    return result


def _ui_stable_signature(inspect_payload: Mapping[str, Any]) -> str:
    copy = json.loads(json.dumps(inspect_payload, sort_keys=True))
    for key in (
        "Id",
        "Created",
        "State",
        "NetworkSettings",
        "GraphDriver",
        "MountLabel",
        "ProcessLabel",
        "ResolvConfPath",
        "HostnamePath",
        "HostsPath",
        "LogPath",
    ):
        copy.pop(key, None)
    config = copy.get("Config")
    if isinstance(config, dict):
        env = []
        for item in config.get("Env") or []:
            if isinstance(item, str) and item.split("=", 1)[0] in ROTATED_ENV_NAMES:
                continue
            env.append(item)
        config["Env"] = env
        labels = config.get("Labels")
        if isinstance(labels, dict):
            labels.pop("com.docker.compose.config-hash", None)
    return json.dumps(copy, sort_keys=True, separators=(",", ":"))


def _non_ui_snapshot() -> dict[str, Mapping[str, str]]:
    snapshot: dict[str, Mapping[str, str]] = {}
    for container_name in _NON_UI_CONTAINERS:
        payload = _inspect_container(container_name)
        state = _mapping_at(payload, "State")
        snapshot[container_name] = {
            "Id": str(payload.get("Id", "")),
            "Image": str(payload.get("Image", "")),
            "StartedAt": str(state.get("StartedAt", "")),
            "RestartCount": str(payload.get("RestartCount", "")),
        }
    return snapshot


def _assert_non_ui_unchanged(before: Mapping[str, Mapping[str, str]]) -> None:
    if _non_ui_snapshot() != before:
        raise DeploymentContractError("non-UI runtime changed during Map UI auth rotation")


def _assert_plaintext_absent(
    inspect_payload: Mapping[str, Any],
    current_password: str,
    new_password: str,
) -> None:
    text = json.dumps(inspect_payload, sort_keys=True)
    if current_password in text or new_password in text:
        raise DeploymentContractError("Map UI runtime contains plaintext credentials")


def _verify_auth_lifecycle(
    *,
    origin: str,
    username: str,
    password: str,
    expect_cookie_reject: str | None,
) -> str:
    opener = _cookie_opener()
    login = _http_request(
        opener,
        f"{origin}/api/auth/login",
        method="POST",
        headers={"Content-Type": "application/json", "Origin": origin},
        body=json.dumps(
            {"username": username, "password": password, "next": _MAP_UI_PROTECTED_PATH}
        ).encode("utf-8"),
    )
    set_cookie = login["set_cookie"]
    if (
        login["status"] != 200
        or login.get("payload") != {"ok": True, "next": _MAP_UI_PROTECTED_PATH}
        or not _valid_login_cookie(str(set_cookie))
    ):
        raise DeploymentContractError("Map UI login verification failed")
    protected = _http_request(
        opener,
        f"{origin}{_MAP_UI_PROTECTED_PATH}",
        method="GET",
        headers={},
    )
    if protected["status"] != 200:
        raise DeploymentContractError("Map UI protected route verification failed")
    logout = _http_request(
        opener,
        f"{origin}/api/auth/logout",
        method="POST",
        headers={"Origin": origin},
    )
    if logout["status"] != 200 or not _valid_logout_cookie(str(logout["set_cookie"])):
        raise DeploymentContractError("Map UI logout verification failed")
    post_logout = _http_request(
        opener,
        f"{origin}{_MAP_UI_PROTECTED_PATH}",
        method="GET",
        headers={},
    )
    if not _is_login_redirect(post_logout):
        raise DeploymentContractError("Map UI post-logout protection verification failed")
    if expect_cookie_reject is not None:
        rejected = _http_request(
            _cookie_opener(),
            f"{origin}{_MAP_UI_PROTECTED_PATH}",
            method="GET",
            headers={"Cookie": expect_cookie_reject},
        )
        if not _is_login_redirect(rejected):
            raise DeploymentContractError("Map UI pre-rotation session was not rejected")
    return str(set_cookie).split(";", 1)[0]


def _cookie_opener() -> urllib.request.OpenerDirector:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
            return None

    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(),
        NoRedirect,
    )


def _http_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None = None,
) -> Mapping[str, Any]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with opener.open(request, timeout=10) as response:
            raw = response.read(65_536)
            return {
                "status": response.status,
                "set_cookie": response.headers.get("Set-Cookie", ""),
                "location": response.headers.get("Location", ""),
                "payload": _json_payload(raw),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read(65_536)
        return {
            "status": exc.code,
            "set_cookie": exc.headers.get("Set-Cookie", ""),
            "location": exc.headers.get("Location", ""),
            "payload": _json_payload(raw),
        }
    except OSError as exc:
        raise DeploymentContractError("Map UI auth endpoint is unavailable") from exc


def _valid_login_cookie(value: str) -> bool:
    lower = value.lower()
    return (
        "ktm_admin_session=" in value
        and "httponly" in lower
        and "secure" in lower
        and "samesite=strict" in lower
        and "path=/" in lower
    )


def _valid_logout_cookie(value: str) -> bool:
    lower = value.lower()
    return (
        "ktm_admin_session=" in value
        and ("max-age=0" in lower or "expires=" in lower)
        and "path=/" in lower
    )


def _json_payload(raw: bytes) -> Any | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _is_login_redirect(response: Mapping[str, Any]) -> bool:
    location_path = urllib.parse.urlsplit(str(response.get("location", ""))).path
    return response.get("status") in {302, 303, 307, 308} and location_path == "/login"


def _recover_pending_journal(
    *,
    paths: RotationPaths,
    env_values: Mapping[str, str],
    runner: CommandRunner,
) -> MapUiAuthRotationResult | None:
    if not paths.journal_path.exists():
        return None
    if not paths.backup_path.exists():
        raise DeploymentContractError("pending Map UI auth rotation journal has no backup")
    manifest = load_pair_manifest(str(paths.manifest_path))
    _restore_backup_with_recovery_session(
        paths,
        active_ui_image=manifest.active.map_ui_image_id,
        runner=runner,
    )
    _write_audit(
        paths.audit_path,
        {
            "result": "recovered_pending_journal",
            "rollback_state": "rolled_back_password_state_with_irreversible_session_invalidation",
            "recorded_at": _utc_now(),
        },
    )
    _unlink_private(paths.backup_path)
    _unlink_private(paths.journal_path)
    del env_values
    return MapUiAuthRotationResult(
        success=False,
        returncode=1,
        phase="recovered_pending_journal",
        audit_path=str(paths.audit_path),
        rollback_state="rolled_back_password_state_with_irreversible_session_invalidation",
        stderr="pending Map UI auth rotation was recovered before starting a new rotation",
    )


def _rollback_after_failure(
    *,
    paths: RotationPaths,
    env_values: Mapping[str, str],
    active_ui_image: str,
    runner: CommandRunner,
    original_error: Exception,
    operation_id: str,
) -> MapUiAuthRotationResult:
    try:
        _restore_backup_with_recovery_session(
            paths,
            active_ui_image=active_ui_image,
            runner=runner,
        )
        _write_audit(
            paths.audit_path,
            {
                "operation_id": operation_id,
                "result": "rolled_back",
                "rollback_state": "rolled_back_password_state_with_irreversible_session_invalidation",
                "recorded_at": _utc_now(),
            },
        )
        _unlink_private(paths.backup_path)
        _unlink_private(paths.journal_path)
        return MapUiAuthRotationResult(
            success=False,
            returncode=1,
            phase="rolled_back",
            audit_path=str(paths.audit_path),
            rollback_state="rolled_back_password_state_with_irreversible_session_invalidation",
            stderr=_sanitized_error(original_error),
        )
    except Exception as rollback_error:
        _write_audit(
            paths.audit_path,
            {
                "operation_id": operation_id,
                "result": "rollback_failed",
                "recorded_at": _utc_now(),
            },
        )
        del env_values
        return MapUiAuthRotationResult(
            success=False,
            returncode=1,
            phase="rollback_failed",
            audit_path=str(paths.audit_path),
            journal_path=str(paths.journal_path),
            stderr=_sanitized_error(rollback_error),
        )


def _restore_backup_with_recovery_session(
    paths: RotationPaths,
    *,
    active_ui_image: str,
    runner: CommandRunner,
) -> None:
    backup_document = _env_document_from_bytes(paths.backup_path.read_bytes(), paths.env_path.stat())
    recovery_session = _new_session_secret()
    restored = backup_document.rewritten({MAP_UI_SESSION_SECRET_ENV: recovery_session})
    _atomic_replace_file(paths.env_path, restored, stat_result=paths.env_path.stat())
    _compose_config_quiet(paths, active_ui_image, runner)
    _compose_recreate_map_ui(paths, active_ui_image, runner)


def _env_document_from_bytes(raw: bytes, stat_result: os.stat_result) -> EnvDocument:
    text = raw.decode("utf-8")
    lines = tuple(text.splitlines(keepends=True))
    found: dict[str, EnvSpan] = {}
    for index, line in enumerate(lines):
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if key in ROTATED_ENV_NAMES:
            found[key] = EnvSpan(key=key, value=value, index=index)
    if set(found) != set(ROTATED_ENV_NAMES):
        raise DeploymentContractError("backup .env is missing Map UI auth keys")
    return EnvDocument(lines=lines, spans=found, original_bytes=raw, stat_result=stat_result)


def _mapping_at(payload: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key, {})
    return current if isinstance(current, Mapping) else {}


def _new_session_secret() -> str:
    return secrets.token_urlsafe(48)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sanitized_error(error: Exception) -> str:
    if isinstance(error, DeploymentContractError):
        return str(error)
    return "Map UI auth rotation failed"
