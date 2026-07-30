from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import http.cookies
import json
import os
import pwd
import re
import secrets
import signal
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from io import StringIO
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
    assert_manager_mutation_allowed,
    c6c_deployment_lock,
    c6c_global_mutation_lock_path,
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
_DOCKER_BIN = "/usr/bin/docker"
_DOCKER_HOST = "unix:///var/run/docker.sock"
_ROTATION_STATE_ROOT = Path("/var/lib/kor-travel-docker-manager/map-ui-auth-rotation")
_MANAGER_SOURCE_REVISION_ENV = "KTDM_MANAGER_SOURCE_REVISION"
_MANAGER_SOURCE_REVISION_FILE = ".ktdm-source-revision"
_ROTATION_JOURNAL_VERSION = 1
_JOURNAL_PHASES = frozenset(
    {
        "prepared",
        "env_new",
        "recreate_started",
        "ui_new_healthy",
        "login_verified",
        "committed",
        "rolled_back",
    }
)
_ACTIVE_PAIR_RUNTIME = {
    "kor-travel-map-api-latest": ("kor-travel-map-api", "map_image_id"),
    "kor-travel-map-ui-latest": ("kor-travel-map-ui", "map_ui_image_id"),
    "kor-travel-map-dagster-latest": ("kor-travel-map-dagster", "map_dagster_image_id"),
    "kor-travel-map-dagster-daemon-latest": (
        "kor-travel-map-dagster-daemon",
        "map_dagster_daemon_image_id",
    ),
    "pinvi-api-latest": ("pinvi-api", "pinvi_image_id"),
}
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")

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
    frozen_compose_path: Path


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
    parent_stat: os.stat_result
    stat_result: os.stat_result
    sha256: str

    def rewritten(self, values: Mapping[str, str]) -> bytes:
        lines = list(self.lines)
        for key, value in values.items():
            span = self.spans[key]
            newline = "\n" if lines[span.index].endswith("\n") else ""
            lines[span.index] = f"{key}='{_single_quote_env_value(value)}'{newline}"
        return "".join(lines).encode("utf-8")


@dataclass(frozen=True)
class StrictFileEvidence:
    path: Path
    parent_stat: os.stat_result
    stat_result: os.stat_result
    sha256: str
    raw: bytes


def generate_map_pbkdf2_hash(password: str, *, salt: bytes | None = None) -> str:
    """Map UI가 요구하는 exact PBKDF2 hash 형식을 생성한다."""

    _validate_new_password(password)
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

    _validate_current_password(current_password)
    _validate_new_password(new_password)
    if current_password == new_password:
        raise DeploymentContractError(
            "new Map UI password must differ from the current password"
        )

    runner = _default_command_runner if command_runner is None else command_runner
    resolved_project_root = _project_root(project_root)
    resolved_env_path = _project_child_path(
        resolved_project_root,
        env_path,
        ".env",
    )
    resolved_compose_path = _project_child_path(
        resolved_project_root,
        compose_path,
        "docker-compose.yml",
    )
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

    with _masked_rotation_signals(), c6c_deployment_lock(str(paths.lock_path)):
        _prepare_private_state_dir(paths.rotation_dir)
        recovered = _recover_pending_journal(
            paths=paths,
            env_values=env_values,
            runner=runner,
        )
        if recovered is not None:
            return recovered
        orphan_recovery = _recover_orphan_rotation_artifacts(paths)
        if orphan_recovery is not None:
            return orphan_recovery

        locked_env_values = _load_env_file_values(paths.env_path)
        if locked_env_values != env_values:
            raise DeploymentContractError("canonical manager .env changed before lock acquisition")
        if not verify_map_pbkdf2_hash(
            current_password,
            locked_env_values[MAP_UI_PASSWORD_HASH_ENV],
        ):
            raise DeploymentContractError("current Map UI password no longer matches .env")
        origin = _production_map_ui_origin(locked_env_values)
        env_values = locked_env_values
        env_document = _read_strict_env_document(paths.env_path)
        _validate_manager_source_evidence(paths.project_root, env_values)
        manifest = load_pair_manifest(str(paths.manifest_path))
        _validate_active_pair_runtime(manifest.active, env_values)
        active_ui_image = manifest.active.map_ui_image_id
        before_ui = _inspect_container(env_values.get(MAP_UI_CONTAINER_ENV, _MAP_UI_CONTAINER))
        _validate_map_ui_container(before_ui, env_values, active_ui_image)
        before_ui_signature = _ui_stable_signature(before_ui)
        before_non_ui = _non_ui_snapshot(env_values[COMPOSE_PROJECT_ENV])
        old_cookie = _verify_auth_lifecycle(
            origin=origin,
            username=env_values[MAP_UI_USERNAME_ENV],
            password=current_password,
            expect_cookie_reject=None,
            preserve_active_session=True,
        )

        operation_id = str(uuid.uuid4())
        new_hash = generate_map_pbkdf2_hash(new_password)
        new_session_secret = _new_session_secret()
        new_values = {
            MAP_UI_PASSWORD_ENV: new_password,
            MAP_UI_PASSWORD_HASH_ENV: new_hash,
            MAP_UI_SESSION_SECRET_ENV: new_session_secret,
        }
        load_c6c_deployment_config_from_environment({**env_values, **new_values})
        new_env_bytes = env_document.rewritten(new_values)
        _write_secret_backup(paths.backup_path, env_document.original_bytes)
        _write_journal(
            paths.journal_path,
            {
                "operation_id": operation_id,
                "version": _ROTATION_JOURNAL_VERSION,
                "phase": "prepared",
                "old_env_sha256": _sha256(env_document.original_bytes),
                "new_env_sha256": _sha256(new_env_bytes),
                "prepared_at": _utc_now(),
            },
        )

        try:
            _atomic_replace_file(
                paths.env_path,
                new_env_bytes,
                parent_stat=env_document.parent_stat,
                stat_result=env_document.stat_result,
                expected_sha256=env_document.sha256,
            )
            _write_journal(paths.journal_path, {"operation_id": operation_id, "phase": "env_new"})
            _write_frozen_compose(paths, active_ui_image, runner)
            _write_journal(
                paths.journal_path,
                {"operation_id": operation_id, "phase": "recreate_started"},
            )
            _compose_recreate_map_ui(paths, active_ui_image, runner)
            _write_journal(
                paths.journal_path,
                {"operation_id": operation_id, "phase": "ui_new_healthy"},
            )
            after_ui = _inspect_container(env_values.get(MAP_UI_CONTAINER_ENV, _MAP_UI_CONTAINER))
            _validate_map_ui_container(after_ui, {**env_values, **new_values}, active_ui_image)
            if _ui_stable_signature(after_ui) != before_ui_signature:
                raise DeploymentContractError("Map UI runtime config drifted during rotation")
            _assert_non_ui_unchanged(before_non_ui, env_values[COMPOSE_PROJECT_ENV])
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
            _write_journal(
                paths.journal_path,
                {"operation_id": operation_id, "phase": "committed"},
            )
            _unlink_private(paths.frozen_compose_path)
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
                before_non_ui=before_non_ui,
                runner=runner,
                original_error=exc,
                operation_id=operation_id,
            )


def _validate_current_password(password: str) -> None:
    if not isinstance(password, str) or not password:
        raise DeploymentContractError("current Map UI password is empty")
    if "\r" in password or "\n" in password:
        raise DeploymentContractError("current Map UI password must not contain line breaks")


def _validate_new_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < 16:
        raise DeploymentContractError("new Map UI password is too short")
    if any(character.isspace() for character in password):
        raise DeploymentContractError("new Map UI password must not contain whitespace")


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


def _project_child_path(project_root: Path, configured: str | None, name: str) -> Path:
    expected = project_root / name
    if configured is None:
        return expected
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    if candidate != expected:
        raise DeploymentContractError(f"canonical manager {name} path must be an exact child")
    return candidate


def _child_exists_nofollow(parent: Path, name: str) -> bool:
    try:
        os.lstat(parent / name)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DeploymentContractError(f"canonical manager {name} is unavailable") from exc
    return True


def _load_env_file_values(env_path: Path) -> dict[str, str]:
    evidence = _capture_strict_child_file(env_path.parent, env_path.name, kind="env")
    try:
        text = evidence.raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentContractError("canonical manager .env must be UTF-8") from exc
    values = {
        key: value or ""
        for key, value in dotenv_values(stream=StringIO(text)).items()
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
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise DeploymentContractError("production Map UI URL must be an exact HTTPS origin")
    return f"https://{parsed.netloc}"


def _rotation_paths(
    *,
    project_root: Path,
    compose_path: Path,
    env_path: Path,
    env_values: Mapping[str, str],
) -> RotationPaths:
    _project_child_path(project_root, str(compose_path), "docker-compose.yml")
    _project_child_path(project_root, str(env_path), ".env")
    _validate_single_file_compose_boundary(compose_path)
    env_evidence = _capture_strict_child_file(env_path.parent, env_path.name, kind="env")
    env_stat = env_evidence.stat_result
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
    rotation_dir = _ROTATION_STATE_ROOT / project_name
    return RotationPaths(
        project_root=project_root,
        compose_path=compose_path,
        env_path=env_path,
        state_dir=state_dir,
        manifest_path=state_dir / "compatible-pair-v4.json",
        lock_path=Path(c6c_global_mutation_lock_path(env_values)),
        rotation_dir=rotation_dir,
        journal_path=rotation_dir / "journal.json",
        backup_path=rotation_dir / "env.backup",
        audit_path=rotation_dir / "audit.jsonl",
        frozen_compose_path=rotation_dir / "frozen-compose.yml",
    )


def _validate_single_file_compose_boundary(compose_path: Path) -> None:
    override_path = compose_path.with_name("docker-compose.override.yml")
    if override_path.exists() or override_path.is_symlink():
        raise DeploymentContractError("Map UI auth rotation requires a single compose file")
    evidence = _capture_strict_child_file(
        compose_path.parent,
        compose_path.name,
        kind="compose",
    )
    try:
        payload = yaml.safe_load(evidence.raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DeploymentContractError("canonical docker-compose.yml is invalid") from exc
    if not isinstance(payload, Mapping):
        raise DeploymentContractError("canonical docker-compose.yml is invalid")
    if "include" in payload:
        raise DeploymentContractError("compose include is forbidden for Map UI auth rotation")
    services = payload.get("services", {})
    if not isinstance(services, Mapping):
        raise DeploymentContractError("canonical docker-compose.yml has no services")
    for service in services.values():
        if isinstance(service, Mapping) and "extends" in service:
            raise DeploymentContractError("compose extends is forbidden for Map UI auth rotation")


def _validate_manager_source_evidence(
    project_root: Path,
    env_values: Mapping[str, str],
) -> str:
    expected_revision = env_values.get(_MANAGER_SOURCE_REVISION_ENV, "").strip()
    if _child_exists_nofollow(project_root, _MANAGER_SOURCE_REVISION_FILE):
        revision_evidence = _capture_strict_child_file(
            project_root,
            _MANAGER_SOURCE_REVISION_FILE,
            kind="revision",
        )
        file_revision = revision_evidence.raw.decode("utf-8").strip()
        if expected_revision and expected_revision != file_revision:
            raise DeploymentContractError("manager source revision evidence is inconsistent")
        expected_revision = file_revision
    git_dir = project_root / ".git"
    if git_dir.exists():
        revision = _git_output(project_root, ["rev-parse", "HEAD"])
        dirty = _git_output(
            project_root,
            ["status", "--porcelain=v1", "--untracked-files=normal"],
        )
        if dirty:
            raise DeploymentContractError("manager source checkout must be clean")
        if expected_revision and expected_revision != revision:
            raise DeploymentContractError("manager source revision differs from .env evidence")
        _validate_git_revision(revision)
        return revision
    _validate_git_revision(expected_revision)
    return expected_revision


def _git_output(project_root: Path, argv: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *argv],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentContractError("manager source revision cannot be verified") from exc
    if completed.returncode != 0:
        raise DeploymentContractError("manager source revision cannot be verified")
    return completed.stdout.strip()


def _validate_git_revision(value: str) -> None:
    if not _GIT_REVISION_RE.fullmatch(value):
        raise DeploymentContractError("manager source revision must be an exact git SHA-1")


@contextmanager
def _masked_rotation_signals() -> Iterator[None]:
    signals = (signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM)
    previous: dict[signal.Signals, Any] = {}
    pending: list[signal.Signals] = []

    def handler(signum: int, _frame: Any) -> None:
        pending.append(signal.Signals(signum))

    for sig in signals:
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, handler)
    try:
        yield
    finally:
        for sig, old_handler in previous.items():
            signal.signal(sig, old_handler)
        if pending:
            os.kill(os.getpid(), int(pending[0]))


def _read_strict_env_document(env_path: Path) -> EnvDocument:
    evidence = _capture_strict_child_file(env_path.parent, env_path.name, kind="env")
    st = evidence.stat_result
    raw = evidence.raw
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
        parent_stat=evidence.parent_stat,
        stat_result=st,
        sha256=evidence.sha256,
    )


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.rstrip("\r\n")
    if not stripped or stripped.lstrip().startswith("#"):
        return None
    exported = False
    if stripped.startswith("export "):
        exported = True
        stripped = stripped.removeprefix("export ").lstrip()
    key, separator, value_text = stripped.partition("=")
    if separator != "=":
        return None
    if key != key.strip() or not _ENV_KEY_RE.fullmatch(key):
        return None
    if key not in ROTATED_ENV_NAMES:
        return None
    if exported:
        raise DeploymentContractError("Map UI auth keys must not use export syntax")
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


def _atomic_replace_file(
    path: Path,
    data: bytes,
    *,
    parent_stat: os.stat_result,
    stat_result: os.stat_result,
    expected_sha256: str,
) -> None:
    dir_fd = _open_strict_directory(path.parent)
    tmp_fd: int | None = None
    tmp_name: str | None = None
    try:
        _validate_parent_stat(os.fstat(dir_fd), parent_stat)
        tmp_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        tmp_fd = os.open(tmp_name, flags, 0o600, dir_fd=dir_fd)
        os.fchown(tmp_fd, stat_result.st_uid, stat_result.st_gid)
        os.fchmod(tmp_fd, 0o600)
        with os.fdopen(tmp_fd, "wb", closefd=True) as handle:
            tmp_fd = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _revalidate_env_file_at(
            dir_fd,
            path.parent,
            path.name,
            parent_stat=parent_stat,
            stat_result=stat_result,
            expected_sha256=expected_sha256,
        )
        os.replace(tmp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_name = None
        os.fsync(dir_fd)
    finally:
        if tmp_fd is not None:
            os.close(tmp_fd)
        if tmp_name is not None:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        os.close(dir_fd)


def _capture_strict_child_file(parent: Path, name: str, *, kind: str) -> StrictFileEvidence:
    dir_fd = _open_strict_directory(parent)
    try:
        return _capture_strict_child_file_at(dir_fd, parent, name, kind=kind)
    finally:
        os.close(dir_fd)


def _capture_strict_child_file_at(
    dir_fd: int,
    parent: Path,
    name: str,
    *,
    kind: str,
) -> StrictFileEvidence:
    fd: int | None = None
    try:
        parent_stat = os.fstat(dir_fd)
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(name, flags, dir_fd=dir_fd)
        st = os.fstat(fd)
        if kind == "env":
            _validate_env_stat(st, expected_uid=st.st_uid)
        elif kind == "compose":
            _validate_compose_stat(st)
        elif kind == "revision":
            _validate_revision_stat(st)
        else:
            _validate_private_file_stat(st, label=kind)
        raw = _read_all_from_fd(fd)
        return StrictFileEvidence(
            path=parent / name,
            parent_stat=parent_stat,
            stat_result=st,
            sha256=_sha256(raw),
            raw=raw,
        )
    except OSError as exc:
        raise DeploymentContractError(f"canonical manager {name} is unavailable") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _open_strict_directory(path: Path) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DeploymentContractError("canonical manager directory is unavailable") from exc
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        os.close(fd)
        raise DeploymentContractError("canonical manager directory is invalid")
    return fd


def _read_all_from_fd(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _validate_env_stat(st: os.stat_result, *, expected_uid: int) -> None:
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_nlink != 1
        or st.st_uid != expected_uid
        or stat.S_IMODE(st.st_mode) != 0o600
    ):
        raise DeploymentContractError("canonical manager .env must be a private regular file")


def _validate_compose_stat(st: os.stat_result) -> None:
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_nlink != 1
        or (stat.S_IMODE(st.st_mode) & 0o022)
    ):
        raise DeploymentContractError(
            "canonical docker-compose.yml must be a non-writable regular file"
        )


def _validate_revision_stat(st: os.stat_result) -> None:
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_nlink != 1
        or (stat.S_IMODE(st.st_mode) & 0o022)
    ):
        raise DeploymentContractError("manager source revision file is unsafe")


def _revalidate_env_file_at(
    dir_fd: int,
    parent: Path,
    name: str,
    *,
    parent_stat: os.stat_result,
    stat_result: os.stat_result,
    expected_sha256: str,
) -> None:
    evidence = _capture_strict_child_file_at(dir_fd, parent, name, kind="env")
    if (
        not _same_dir_stat(evidence.parent_stat, parent_stat)
        or evidence.stat_result.st_dev != stat_result.st_dev
        or evidence.stat_result.st_ino != stat_result.st_ino
    ):
        raise DeploymentContractError("canonical manager .env changed during rotation")
    if not hmac.compare_digest(evidence.sha256, expected_sha256):
        raise DeploymentContractError("canonical manager .env changed during rotation")


def _validate_parent_stat(current: os.stat_result, expected: os.stat_result) -> None:
    if not _same_dir_stat(current, expected):
        raise DeploymentContractError("canonical manager directory changed during rotation")


def _same_dir_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _revalidate_file_evidence(evidence: StrictFileEvidence, *, kind: str) -> None:
    current = _capture_strict_child_file(evidence.path.parent, evidence.path.name, kind=kind)
    if (
        current.parent_stat.st_dev != evidence.parent_stat.st_dev
        or current.parent_stat.st_ino != evidence.parent_stat.st_ino
        or current.stat_result.st_dev != evidence.stat_result.st_dev
        or current.stat_result.st_ino != evidence.stat_result.st_ino
        or current.stat_result.st_uid != evidence.stat_result.st_uid
        or stat.S_IMODE(current.stat_result.st_mode)
        != stat.S_IMODE(evidence.stat_result.st_mode)
        or not hmac.compare_digest(current.sha256, evidence.sha256)
    ):
        raise DeploymentContractError(f"canonical manager {evidence.path.name} changed")


def _write_secret_backup(path: Path, data: bytes) -> None:
    _write_private_file(path, data, create_exclusive=True)


def _write_journal(path: Path, payload: Mapping[str, Any]) -> None:
    merged: dict[str, Any] = {}
    if _private_file_exists(path):
        try:
            existing = json.loads(_read_private_file(path, label="Map UI auth journal"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeploymentContractError("pending Map UI auth rotation journal is invalid") from exc
        if not isinstance(existing, dict):
            raise DeploymentContractError("pending Map UI auth rotation journal is invalid")
        merged.update(existing)
    merged.update(payload)
    _validate_journal_payload(merged)
    _atomic_private_replace(
        path,
        (json.dumps(merged, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )


def _write_audit(path: Path, payload: Mapping[str, Any]) -> None:
    _prepare_private_state_dir(path.parent)
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        _validate_private_file_fd(fd, label="Map UI auth audit")
        line = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n"
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def _write_private_file(path: Path, data: bytes, *, create_exclusive: bool = False) -> None:
    _prepare_private_state_dir(path.parent)
    flags = os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC
    if create_exclusive:
        flags |= os.O_EXCL
    else:
        flags |= os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        _validate_private_file_fd(fd, label="Map UI auth state")
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def _atomic_private_replace(path: Path, data: bytes) -> None:
    _prepare_private_state_dir(path.parent)
    tmp_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    tmp_path = path.parent / tmp_name
    try:
        _write_private_file(tmp_path, data, create_exclusive=True)
        os.replace(tmp_path, path)
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            _validate_private_file_fd(fd, label="Map UI auth state")
        finally:
            os.close(fd)
        _fsync_directory(path.parent)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _unlink_private(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _recover_orphan_rotation_artifacts(
    paths: RotationPaths,
) -> MapUiAuthRotationResult | None:
    backup_exists = _private_file_exists(paths.backup_path)
    frozen_exists = _private_file_exists(paths.frozen_compose_path)
    if not backup_exists and not frozen_exists:
        return None
    if frozen_exists:
        raise DeploymentContractError(
            "Map UI auth rotation has stale frozen compose without a journal"
        )
    current_sha = _capture_strict_child_file(
        paths.env_path.parent,
        paths.env_path.name,
        kind="env",
    ).sha256
    backup_sha = _sha256(_read_private_file_bytes(paths.backup_path, label="Map UI auth backup"))
    if current_sha != backup_sha:
        raise DeploymentContractError(
            "Map UI auth rotation has ambiguous orphan backup without a journal"
        )
    _write_audit(
        paths.audit_path,
        {
            "result": "cleared_orphan_backup_on_current_env",
            "recorded_at": _utc_now(),
        },
    )
    _unlink_private(paths.backup_path)
    return MapUiAuthRotationResult(
        success=False,
        returncode=1,
        phase="cleared_orphan_backup_on_current_env",
        audit_path=str(paths.audit_path),
        stderr="orphan Map UI auth backup matched current .env and was cleared",
    )


def _read_private_file(path: Path, *, label: str) -> str:
    raw = _read_private_file_bytes(path, label=label)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentContractError(f"{label} file is not UTF-8") from exc


def _read_private_file_bytes(path: Path, *, label: str) -> bytes:
    return _capture_strict_child_file(path.parent, path.name, kind=label).raw


def _private_file_exists(path: Path) -> bool:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DeploymentContractError("Map UI auth state file is unavailable") from exc
    _validate_private_file_stat(st, label="Map UI auth state")
    return True


def _prepare_private_state_dir(path: Path) -> None:
    if _is_relative_to(path, _ROTATION_STATE_ROOT):
        _ensure_private_dir(_ROTATION_STATE_ROOT.parent)
        current = _ROTATION_STATE_ROOT.parent
        for part in path.relative_to(_ROTATION_STATE_ROOT.parent).parts:
            current = current / part
            _ensure_private_dir(current)
        return
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _validate_private_dir(path)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _ensure_private_dir(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        created = False
    if created:
        os.chmod(path, 0o700)
    _validate_private_dir(path)


def _validate_private_dir(path: Path) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        raise DeploymentContractError("Map UI auth state directory is unavailable") from exc
    expected_uid = os.geteuid()
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != expected_uid
        or st.st_nlink < 1
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise DeploymentContractError("Map UI auth state directory is unsafe")


def _validate_private_file_fd(fd: int, *, label: str) -> None:
    st = os.fstat(fd)
    _validate_private_file_stat(st, label=label)


def _validate_private_file_stat(st: os.stat_result, *, label: str) -> None:
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_nlink != 1
        or st.st_uid != os.geteuid()
        or stat.S_IMODE(st.st_mode) != 0o600
    ):
        raise DeploymentContractError(f"{label} file is unsafe")


def _validate_journal_payload(payload: Mapping[str, Any]) -> None:
    if payload.get("version") != _ROTATION_JOURNAL_VERSION:
        raise DeploymentContractError("Map UI auth journal version is invalid")
    phase = payload.get("phase")
    if phase not in _JOURNAL_PHASES:
        raise DeploymentContractError("Map UI auth journal phase is invalid")
    for key in ("operation_id", "old_env_sha256", "new_env_sha256"):
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise DeploymentContractError("Map UI auth journal is missing required evidence")
        if key.endswith("_sha256") and not _SHA256_RE.fullmatch(value):
            raise DeploymentContractError("Map UI auth journal SHA evidence is invalid")


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_frozen_compose(
    paths: RotationPaths,
    active_ui_image: str,
    runner: CommandRunner,
) -> None:
    compose_evidence = _capture_strict_child_file(
        paths.compose_path.parent,
        paths.compose_path.name,
        kind="compose",
    )
    resolved = runner(
        [
            _DOCKER_BIN,
            "compose",
            "--env-file",
            str(paths.env_path),
            "-f",
            str(paths.compose_path),
            "config",
        ],
        paths.project_root,
        _sanitized_child_env({MAP_UI_IMAGE_ENV: active_ui_image}),
        None,
        120,
    )
    _revalidate_file_evidence(compose_evidence, kind="compose")
    if resolved.returncode != 0 or not resolved.stdout:
        raise DeploymentContractError("docker compose config validation failed")
    _write_private_file(
        paths.frozen_compose_path,
        resolved.stdout.encode("utf-8"),
        create_exclusive=True,
    )
    result = runner(
        [
            _DOCKER_BIN,
            "compose",
            "-f",
            str(paths.frozen_compose_path),
            "config",
            "--quiet",
        ],
        paths.project_root,
        _sanitized_child_env(),
        None,
        120,
    )
    _revalidate_file_evidence(compose_evidence, kind="compose")
    if result.returncode != 0:
        raise DeploymentContractError("frozen docker compose config validation failed")


def _compose_recreate_map_ui(
    paths: RotationPaths,
    active_ui_image: str,
    runner: CommandRunner,
) -> None:
    result = runner(
        [
            _DOCKER_BIN,
            "compose",
            "-f",
            str(paths.frozen_compose_path),
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
        _sanitized_child_env(),
        None,
        180,
    )
    if result.returncode != 0:
        raise DeploymentContractError("Map UI recreate failed")


def _default_command_runner(
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    stdin: str | None,
    timeout: int,
) -> CommandResult:
    if not argv or argv[0] != _DOCKER_BIN:
        raise DeploymentContractError("managed Docker command must use the canonical binary")
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=dict(env),
            text=True,
            stdin=subprocess.PIPE if stdin is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
            raise DeploymentContractError("managed command timed out") from exc
    except OSError as exc:
        raise DeploymentContractError("managed command failed") from exc
    return CommandResult(
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise DeploymentContractError("managed command did not terminate") from exc


def _sanitized_child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "DOCKER_HOST": _DOCKER_HOST,
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "NO_PROXY": "*",
    }
    if extra:
        env.update(extra)
    forbidden = set(ROTATED_ENV_NAMES)
    if forbidden.intersection(env):
        raise DeploymentContractError("child environment contains a Map UI auth secret")
    return env


def _inspect_container(container_name: str) -> Mapping[str, Any]:
    try:
        completed = subprocess.run(
            [_DOCKER_BIN, "inspect", container_name],
            env=_sanitized_child_env(),
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
    if labels.get("com.docker.compose.project") != env_values.get(COMPOSE_PROJECT_ENV):
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


def _validate_active_pair_runtime(active_pair: Any, env_values: Mapping[str, str]) -> None:
    project = env_values.get(COMPOSE_PROJECT_ENV)
    project_snapshot = _compose_project_snapshot(str(project), include_ui=True)
    for _container_name, (service_name, image_attr) in _ACTIVE_PAIR_RUNTIME.items():
        payload = project_snapshot.get(service_name, {}).get("_payload")
        if not isinstance(payload, Mapping):
            raise DeploymentContractError("running active compatible pair service is missing")
        expected_image = getattr(active_pair, image_attr)
        labels = _mapping_at(payload, "Config", "Labels")
        state = _mapping_at(payload, "State")
        if payload.get("Image") != expected_image:
            raise DeploymentContractError("running active compatible pair image drifted")
        if labels.get("com.docker.compose.project") != project:
            raise DeploymentContractError("running active compatible pair project drifted")
        if labels.get("com.docker.compose.service") != service_name:
            raise DeploymentContractError("running active compatible pair service drifted")
        if state.get("Running") is not True:
            raise DeploymentContractError("running active compatible pair is not ready")
        health = _mapping_at(state, "Health").get("Status", "")
        if health not in {"", "healthy"}:
            raise DeploymentContractError("running active compatible pair is not healthy")


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
        config.pop("Hostname", None)
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


def _compose_project_snapshot(
    project: str,
    *,
    include_ui: bool,
) -> dict[str, Mapping[str, Any]]:
    if not project:
        raise DeploymentContractError("COMPOSE_PROJECT_NAME must be explicit")
    snapshot: dict[str, Mapping[str, Any]] = {}
    seen_containers: set[str] = set()
    for container_name in _project_container_names(project):
        if container_name in seen_containers:
            raise DeploymentContractError("compose project container list is ambiguous")
        seen_containers.add(container_name)
        payload = _inspect_container(container_name)
        labels = _mapping_at(payload, "Config", "Labels")
        if labels.get("com.docker.compose.project") != project:
            raise DeploymentContractError("compose project container provenance drifted")
        service = str(labels.get("com.docker.compose.service", ""))
        if not service:
            raise DeploymentContractError("compose project service label is missing")
        if service in snapshot:
            raise DeploymentContractError("compose project service label is not unique")
        if service == _MAP_UI_SERVICE and not include_ui:
            continue
        state = _mapping_at(payload, "State")
        snapshot[service] = {
            "Id": str(payload.get("Id", "")),
            "Image": str(payload.get("Image", "")),
            "StartedAt": str(state.get("StartedAt", "")),
            "RestartCount": str(payload.get("RestartCount", "")),
            "Name": str(payload.get("Name", "")),
            "_payload": payload,
        }
    if not snapshot:
        raise DeploymentContractError("compose project has no runtime containers")
    return snapshot


def _project_container_names(project: str) -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            [
                _DOCKER_BIN,
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.Names}}",
            ],
            env=_sanitized_child_env(),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentContractError("cannot inspect compose project container set") from exc
    if completed.returncode != 0:
        raise DeploymentContractError("cannot inspect compose project container set")
    names = tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())
    if not names:
        raise DeploymentContractError("compose project container set is empty")
    return names


def _non_ui_snapshot(project: str) -> dict[str, Mapping[str, Any]]:
    snapshot = _compose_project_snapshot(project, include_ui=False)
    return {
        service: {
            key: value
            for key, value in metadata.items()
            if key != "_payload"
        }
        for service, metadata in snapshot.items()
    }


def _assert_non_ui_unchanged(
    before: Mapping[str, Mapping[str, Any]],
    project: str,
) -> None:
    if _non_ui_snapshot(project) != before:
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
    preserve_active_session: bool = False,
) -> str:
    opener, set_cookie, active_cookie = _login_and_verify(
        origin=origin,
        username=username,
        password=password,
    )
    if preserve_active_session:
        logout_opener, _logout_set_cookie, logout_active_cookie = _login_and_verify(
            origin=origin,
            username=username,
            password=password,
        )
        _logout_and_verify(
            origin=origin,
            opener=logout_opener,
            active_cookie=logout_active_cookie,
        )
    else:
        _logout_and_verify(
            origin=origin,
            opener=opener,
            active_cookie=active_cookie,
        )
    if expect_cookie_reject is not None:
        rejected = _http_request(
            _cookie_opener(),
            f"{origin}{_MAP_UI_PROTECTED_PATH}",
            method="GET",
            headers={"Cookie": expect_cookie_reject},
        )
        if not _is_login_redirect(rejected):
            raise DeploymentContractError("Map UI pre-rotation session was not rejected")
    return _cookie_header_from_set_cookie(str(set_cookie))


def _login_and_verify(
    *,
    origin: str,
    username: str,
    password: str,
) -> tuple[urllib.request.OpenerDirector, str, str]:
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
    return opener, str(set_cookie), _cookie_header_from_set_cookie(str(set_cookie))


def _logout_and_verify(
    *,
    origin: str,
    opener: urllib.request.OpenerDirector,
    active_cookie: str,
) -> None:
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
    revoked_rejected = _http_request(
        _cookie_opener(),
        f"{origin}{_MAP_UI_PROTECTED_PATH}",
        method="GET",
        headers={"Cookie": active_cookie},
    )
    if not _is_login_redirect(revoked_rejected):
        raise DeploymentContractError("Map UI revoked logout cookie was not rejected")


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
    morsel = _session_cookie(value)
    if morsel is None:
        return False
    return (
        bool(morsel.value)
        and bool(morsel["httponly"])
        and bool(morsel["secure"])
        and morsel["samesite"].lower() == "strict"
        and morsel["path"] == "/"
        and not morsel["max-age"]
        and not morsel["expires"]
    )


def _valid_logout_cookie(value: str) -> bool:
    morsel = _session_cookie(value)
    if morsel is None:
        return False
    return (
        morsel.value == ""
        and _cookie_is_expired(morsel)
        and morsel["path"] == "/"
    )


def _session_cookie(value: str) -> http.cookies.Morsel[str] | None:
    if "\r" in value or "\n" in value:
        return None
    lowered = value.lower()
    if lowered.count("ktm_admin_session=") != 1:
        return None
    for attribute in (
        "path=",
        "samesite=",
        "max-age=",
        "expires=",
        "httponly",
        "secure",
    ):
        if lowered.count(attribute) > 1:
            return None
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(value)
    except http.cookies.CookieError:
        return None
    if list(cookie.keys()) != ["ktm_admin_session"]:
        return None
    morsel = cookie.get("ktm_admin_session")
    return morsel if isinstance(morsel, http.cookies.Morsel) else None


def _cookie_is_expired(morsel: http.cookies.Morsel[str]) -> bool:
    if morsel["max-age"] == "0":
        return True
    expires = morsel["expires"]
    if not expires:
        return False
    try:
        expires_at = parsedate_to_datetime(expires)
    except (TypeError, ValueError, IndexError, OverflowError):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _cookie_header_from_set_cookie(value: str) -> str:
    morsel = _session_cookie(value)
    if morsel is None:
        raise DeploymentContractError("Map UI session cookie is invalid")
    return f"ktm_admin_session={morsel.value}"


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
    if not _private_file_exists(paths.journal_path):
        return None
    journal = _read_journal(paths.journal_path)
    phase = str(journal["phase"])
    old_sha = str(journal.get("old_env_sha256", ""))
    new_sha = str(journal.get("new_env_sha256", ""))
    current_sha = _capture_strict_child_file(
        paths.env_path.parent,
        paths.env_path.name,
        kind="env",
    ).sha256
    if current_sha not in {old_sha, new_sha}:
        raise DeploymentContractError(
            "pending Map UI auth rotation journal does not match the current .env"
        )
    if phase == "committed":
        if current_sha != new_sha:
            raise DeploymentContractError("committed Map UI auth journal is inconsistent")
        _write_audit(
            paths.audit_path,
            {
                "operation_id": journal["operation_id"],
                "result": "cleared_committed_journal",
                "recorded_at": _utc_now(),
            },
        )
        _unlink_private(paths.backup_path)
        _unlink_private(paths.journal_path)
        _unlink_private(paths.frozen_compose_path)
        return MapUiAuthRotationResult(
            success=True,
            returncode=0,
            phase="cleared_committed_journal",
            audit_path=str(paths.audit_path),
            stderr="previous committed Map UI auth journal residue was cleared",
        )
    if not _private_file_exists(paths.backup_path):
        raise DeploymentContractError("pending Map UI auth rotation journal has no backup")
    if current_sha == old_sha and phase in {"prepared", "rolled_back"}:
        _write_audit(
            paths.audit_path,
            {
                "operation_id": journal["operation_id"],
                "result": f"cleared_pending_journal_on_{phase}",
                "recorded_at": _utc_now(),
            },
        )
        _unlink_private(paths.backup_path)
        _unlink_private(paths.journal_path)
        _unlink_private(paths.frozen_compose_path)
        return MapUiAuthRotationResult(
            success=False,
            returncode=1,
            phase=f"cleared_pending_journal_on_{phase}",
            audit_path=str(paths.audit_path),
            stderr="pending Map UI auth rotation journal was already on a terminal old env",
        )
    if current_sha == old_sha and phase in {
        "env_new",
        "recreate_started",
        "ui_new_healthy",
        "login_verified",
    }:
        raise DeploymentContractError("pending Map UI auth journal phase conflicts with old .env")
    if current_sha != new_sha:
        raise DeploymentContractError("pending Map UI auth journal phase is not recoverable")
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


def _read_journal(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(_read_private_file(path, label="Map UI auth journal"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentContractError("pending Map UI auth rotation journal is invalid") from exc
    if not isinstance(payload, Mapping):
        raise DeploymentContractError("pending Map UI auth rotation journal is invalid")
    _validate_journal_payload(payload)
    return payload


def _rollback_after_failure(
    *,
    paths: RotationPaths,
    env_values: Mapping[str, str],
    active_ui_image: str,
    before_non_ui: Mapping[str, Mapping[str, Any]],
    runner: CommandRunner,
    original_error: Exception,
    operation_id: str,
) -> MapUiAuthRotationResult:
    try:
        restored_values = _restore_backup_with_recovery_session(
            paths,
            active_ui_image=active_ui_image,
            runner=runner,
        )
        restored_ui = _inspect_container(restored_values.get(MAP_UI_CONTAINER_ENV, _MAP_UI_CONTAINER))
        _validate_map_ui_container(restored_ui, restored_values, active_ui_image)
        _assert_non_ui_unchanged(before_non_ui, restored_values[COMPOSE_PROJECT_ENV])
        _verify_auth_lifecycle(
            origin=_production_map_ui_origin(restored_values),
            username=restored_values[MAP_UI_USERNAME_ENV],
            password=env_values[MAP_UI_PASSWORD_ENV],
            expect_cookie_reject=None,
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
        _unlink_private(paths.frozen_compose_path)
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
) -> dict[str, str]:
    current_evidence = _capture_strict_child_file(paths.env_path.parent, paths.env_path.name, kind="env")
    backup_document = _env_document_from_bytes(
        _read_private_file_bytes(paths.backup_path, label="Map UI auth backup"),
        current_evidence.parent_stat,
        current_evidence.stat_result,
    )
    recovery_session = _new_session_secret()
    restored = backup_document.rewritten({MAP_UI_SESSION_SECRET_ENV: recovery_session})
    _atomic_replace_file(
        paths.env_path,
        restored,
        parent_stat=current_evidence.parent_stat,
        stat_result=current_evidence.stat_result,
        expected_sha256=current_evidence.sha256,
    )
    try:
        _unlink_private(paths.frozen_compose_path)
    except DeploymentContractError:
        pass
    _write_frozen_compose(paths, active_ui_image, runner)
    _compose_recreate_map_ui(paths, active_ui_image, runner)
    values = _load_env_file_values(paths.env_path)
    values[MAP_UI_SESSION_SECRET_ENV] = recovery_session
    return values


def _env_document_from_bytes(
    raw: bytes,
    parent_stat: os.stat_result,
    stat_result: os.stat_result,
) -> EnvDocument:
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
    return EnvDocument(
        lines=lines,
        spans=found,
        original_bytes=raw,
        parent_stat=parent_stat,
        stat_result=stat_result,
        sha256=_sha256(raw),
    )


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
