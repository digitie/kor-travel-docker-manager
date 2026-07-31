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
    c6c_state_paths,
    compatible_pair_image_environment,
    ensure_c6c_state_directory,
    inspect_c6c_image_source_revision,
    load_c6c_deployment_config_from_environment,
    load_pair_manifest,
    require_local_c6c_image,
    validate_compose_candidate_protected_values,
    validate_resolved_compose_candidate_protected_values,
    validate_resolved_compose_image_pair,
    validate_resolved_compose_secret_isolation,
    verify_compatible_pair_image_provenance,
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
        "KOR_TRAVEL_MAP_API_IMAGE",
        "KOR_TRAVEL_MAP_DAGSTER_IMAGE",
        "KOR_TRAVEL_MAP_DAGSTER_DAEMON_IMAGE",
        "KOR_TRAVEL_MAP_GIT_COMMIT",
        "PINVI_API_IMAGE",
        "PINVI_SOURCE_REVISION",
        "PINVI_BUILD_ENVIRONMENT",
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
        "rollback_preparing",
        "rollback_prepared",
        "rollback_recreate_started",
        "rollback_verified",
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
    env_owner_uid: int
    source_owner_uid: int
    state_dir: Path
    manifest_path: Path
    lock_path: Path
    rotation_dir: Path
    journal_path: Path
    backup_path: Path
    audit_path: Path
    frozen_compose_path: Path
    recovery_path: Path


@dataclass(frozen=True)
class RecoveryState:
    values: Mapping[str, str]
    sha256: str


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
    env_owner_uid = _deployment_owner_uid()
    source_owner_uid = _manager_source_owner_uid(require_root=require_root)
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
    _validate_manager_source_boundary(resolved_project_root, source_owner_uid=source_owner_uid)
    env_values = _load_env_file_values(resolved_env_path, expected_uid=env_owner_uid)
    assert_manager_mutation_allowed(environment=env_values)
    config = load_c6c_deployment_config_from_environment(env_values)
    if not config.production:
        raise DeploymentContractError("Map UI auth rotation is production-only")
    if require_root and os.geteuid() != 0:
        raise DeploymentContractError("Map UI auth rotation requires root privileges")
    origin = _production_map_ui_origin(env_values)
    paths = _rotation_paths(
        project_root=resolved_project_root,
        compose_path=resolved_compose_path,
        env_path=resolved_env_path,
        env_owner_uid=env_owner_uid,
        source_owner_uid=source_owner_uid,
        env_values=env_values,
    )

    with _masked_rotation_signals(), c6c_deployment_lock(str(paths.lock_path)):
        _prepare_private_state_dir(paths.rotation_dir)
        recovered = _recover_pending_journal(
            paths=paths,
            env_values=env_values,
            runner=runner,
            current_password=current_password,
            new_password=new_password,
        )
        if recovered is not None:
            return recovered
        orphan_recovery = _recover_orphan_rotation_artifacts(paths)
        if orphan_recovery is not None:
            return orphan_recovery

        if not verify_map_pbkdf2_hash(current_password, env_values[MAP_UI_PASSWORD_HASH_ENV]):
            raise DeploymentContractError("current Map UI password does not match the frozen hash")

        locked_env_values = _load_env_file_values(paths.env_path, expected_uid=paths.env_owner_uid)
        if locked_env_values != env_values:
            raise DeploymentContractError("canonical manager .env changed before lock acquisition")
        if not verify_map_pbkdf2_hash(
            current_password,
            locked_env_values[MAP_UI_PASSWORD_HASH_ENV],
        ):
            raise DeploymentContractError("current Map UI password no longer matches .env")
        origin = _production_map_ui_origin(locked_env_values)
        env_values = locked_env_values
        env_document = _read_strict_env_document(paths.env_path, expected_uid=paths.env_owner_uid)
        manager_source_revision = _validate_manager_source_evidence(
            paths.project_root,
            env_values,
            source_owner_uid=paths.source_owner_uid,
        )
        manifest = load_pair_manifest(str(paths.manifest_path))
        _validate_active_pair_runtime(manifest.active, env_values, paths.compose_path)
        active_ui_image = manifest.active.map_ui_image_id
        before_ui = _inspect_container(env_values.get(MAP_UI_CONTAINER_ENV, _MAP_UI_CONTAINER))
        _validate_map_ui_container(before_ui, env_values, active_ui_image)
        before_ui_sha256 = _ui_stable_signature(before_ui)
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
                "before_ui_sha256": before_ui_sha256,
                "before_non_ui": before_non_ui,
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
            _write_frozen_compose(paths, manifest.active, runner)
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
            if _ui_stable_signature(after_ui) != before_ui_sha256:
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
            _require_env_sha(paths, _sha256(new_env_bytes), label="new Map UI auth env")
            _write_journal(
                paths.journal_path,
                {"operation_id": operation_id, "phase": "committed"},
            )
            _write_terminal_audit_once(
                paths.audit_path,
                {
                    "operation_id": operation_id,
                    "result": "committed",
                    "operator_uid": _operator_uid(),
                    "manager_source_revision": manager_source_revision,
                    "active_pair": _active_pair_audit(manifest.active),
                    "env_sha256": {
                        "old": _sha256(env_document.original_bytes),
                        "new": _sha256(new_env_bytes),
                    },
                    "runtime_sha256": {
                        "before_ui": before_ui_sha256,
                        "before_non_ui": _digest_json(before_non_ui),
                    },
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
            _cleanup_rotation_artifacts(paths)
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
                active_pair=manifest.active,
                active_ui_image=active_ui_image,
                before_non_ui=before_non_ui,
                runner=runner,
                original_error=exc,
                operation_id=operation_id,
                manager_source_revision=manager_source_revision,
                current_password=current_password,
                before_ui_sha256=before_ui_sha256,
                old_env_sha256=_sha256(env_document.original_bytes),
                new_env_sha256=_sha256(new_env_bytes),
            )


def _validate_current_password(password: str) -> None:
    if not isinstance(password, str) or not password:
        raise DeploymentContractError("current Map UI password is empty")
    if "\0" in password or "\r" in password or "\n" in password:
        raise DeploymentContractError("current Map UI password contains a forbidden character")


def _validate_new_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < 16:
        raise DeploymentContractError("new Map UI password is too short")
    if (
        "\0" in password
        or "\r" in password
        or "\n" in password
        or "'" in password
        or "\\" in password
    ):
        raise DeploymentContractError("new Map UI password contains a forbidden character")
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


def _deployment_owner_uid() -> int:
    euid = os.geteuid()
    if euid != 0:
        return euid
    sudo_uid = os.environ.get("SUDO_UID", "").strip()
    if not sudo_uid:
        return 0
    try:
        uid = int(sudo_uid)
        if uid < 0:
            raise ValueError
        pwd.getpwuid(uid)
    except (KeyError, ValueError) as exc:
        raise DeploymentContractError("SUDO_UID must identify the canonical deployment owner") from exc
    return uid


def _operator_uid() -> int:
    sudo_uid = os.environ.get("SUDO_UID", "").strip()
    if sudo_uid:
        try:
            return int(sudo_uid)
        except ValueError as exc:
            raise DeploymentContractError("SUDO_UID must be numeric") from exc
    return os.getuid()


def _manager_source_owner_uid(*, require_root: bool) -> int:
    return 0 if require_root else os.geteuid()


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


def _load_env_file_values(env_path: Path, *, expected_uid: int) -> dict[str, str]:
    evidence = _capture_strict_child_file(
        env_path.parent,
        env_path.name,
        kind="env",
        expected_uid=expected_uid,
    )
    return _env_values_from_bytes(evidence.raw)


def _env_values_from_bytes(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8")
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
    strict_spans = _strict_rotated_env_spans(tuple(text.splitlines(keepends=True)))
    for key, span in strict_spans.items():
        if values.get(key) != span.value:
            raise DeploymentContractError("canonical manager .env parser disagreement on Map UI auth")
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
    env_owner_uid: int,
    source_owner_uid: int,
    env_values: Mapping[str, str],
) -> RotationPaths:
    _project_child_path(project_root, str(compose_path), "docker-compose.yml")
    _project_child_path(project_root, str(env_path), ".env")
    _validate_single_file_compose_boundary(compose_path, source_owner_uid=source_owner_uid)
    _capture_strict_child_file(
        env_path.parent,
        env_path.name,
        kind="env",
        expected_uid=env_owner_uid,
    )
    project_name = env_values.get(COMPOSE_PROJECT_ENV, "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", project_name):
        raise DeploymentContractError("COMPOSE_PROJECT_NAME must be explicit and canonical")
    manifest_path, lock_path = c6c_state_paths(env_values)
    state_dir = Path(manifest_path).parent
    rotation_dir = _ROTATION_STATE_ROOT / project_name
    return RotationPaths(
        project_root=project_root,
        compose_path=compose_path,
        env_path=env_path,
        env_owner_uid=env_owner_uid,
        source_owner_uid=source_owner_uid,
        state_dir=state_dir,
        manifest_path=Path(manifest_path),
        lock_path=Path(lock_path),
        rotation_dir=rotation_dir,
        journal_path=rotation_dir / "journal.json",
        backup_path=rotation_dir / "env.backup",
        audit_path=rotation_dir / "audit.jsonl",
        frozen_compose_path=rotation_dir / "frozen-compose.yml",
        recovery_path=rotation_dir / "env.recovery",
    )


def _validate_single_file_compose_boundary(compose_path: Path, *, source_owner_uid: int) -> None:
    override_path = compose_path.with_name("docker-compose.override.yml")
    if override_path.exists() or override_path.is_symlink():
        raise DeploymentContractError("Map UI auth rotation requires a single compose file")
    evidence = _capture_strict_child_file(
        compose_path.parent,
        compose_path.name,
        kind="compose",
        expected_uid=source_owner_uid,
    )
    payload = _compose_yaml_mapping(evidence.raw, label="canonical docker-compose.yml")
    if "include" in payload:
        raise DeploymentContractError("compose include is forbidden for Map UI auth rotation")
    services = payload.get("services", {})
    if not isinstance(services, Mapping):
        raise DeploymentContractError("canonical docker-compose.yml has no services")
    for service in services.values():
        if isinstance(service, Mapping) and "extends" in service:
            raise DeploymentContractError("compose extends is forbidden for Map UI auth rotation")


def _compose_yaml_mapping(raw: bytes, *, label: str) -> Mapping[str, Any]:
    try:
        payload = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DeploymentContractError(f"{label} is invalid") from exc
    if not isinstance(payload, Mapping):
        raise DeploymentContractError(f"{label} is invalid")
    return payload


def _validate_manager_source_boundary(project_root: Path, *, source_owner_uid: int) -> None:
    _validate_source_path_stat(
        project_root.stat(),
        expected_uid=source_owner_uid,
        label="canonical manager project root",
        require_regular=False,
    )
    git_path = project_root / ".git"
    try:
        git_stat = git_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DeploymentContractError("canonical manager .git metadata is unavailable") from exc
    if not (stat.S_ISDIR(git_stat.st_mode) or stat.S_ISREG(git_stat.st_mode)):
        raise DeploymentContractError("canonical manager .git metadata is unsafe")
    _validate_source_path_stat(
        git_stat,
        expected_uid=source_owner_uid,
        label="canonical manager .git metadata",
        require_regular=False,
    )


def _validate_manager_source_evidence(
    project_root: Path,
    env_values: Mapping[str, str],
    *,
    source_owner_uid: int,
) -> str:
    expected_revision = env_values.get(_MANAGER_SOURCE_REVISION_ENV, "").strip()
    if not _child_exists_nofollow(project_root, _MANAGER_SOURCE_REVISION_FILE):
        raise DeploymentContractError("manager source revision evidence file is required")
    revision_evidence = _capture_strict_child_file(
        project_root,
        _MANAGER_SOURCE_REVISION_FILE,
        kind="revision",
        expected_uid=source_owner_uid,
    )
    try:
        file_revision = revision_evidence.raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise DeploymentContractError("manager source revision evidence must be UTF-8") from exc
    if expected_revision and expected_revision != file_revision:
        raise DeploymentContractError("manager source revision evidence is inconsistent")
    _validate_git_revision(file_revision)
    return file_revision


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


def _read_strict_env_document(env_path: Path, *, expected_uid: int) -> EnvDocument:
    evidence = _capture_strict_child_file(
        env_path.parent,
        env_path.name,
        kind="env",
        expected_uid=expected_uid,
    )
    st = evidence.stat_result
    raw = evidence.raw
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentContractError("canonical manager .env must be UTF-8") from exc
    lines = tuple(text.splitlines(keepends=True))
    found = _strict_rotated_env_spans(lines)
    return EnvDocument(
        lines=lines,
        spans=found,
        original_bytes=raw,
        parent_stat=evidence.parent_stat,
        stat_result=st,
        sha256=evidence.sha256,
    )


def _strict_rotated_env_spans(lines: tuple[str, ...]) -> dict[str, EnvSpan]:
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
    return found


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
        _write_all_fd(tmp_fd, data)
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = None
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


def _capture_strict_child_file(
    parent: Path,
    name: str,
    *,
    kind: str,
    expected_uid: int | None = None,
) -> StrictFileEvidence:
    dir_fd = _open_strict_directory(parent)
    try:
        return _capture_strict_child_file_at(
            dir_fd,
            parent,
            name,
            kind=kind,
            expected_uid=expected_uid,
        )
    finally:
        os.close(dir_fd)


def _capture_strict_child_file_at(
    dir_fd: int,
    parent: Path,
    name: str,
    *,
    kind: str,
    expected_uid: int | None = None,
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
            if expected_uid is None:
                raise DeploymentContractError("canonical manager .env owner is not configured")
            _validate_env_stat(st, expected_uid=expected_uid)
        elif kind == "compose":
            if expected_uid is None:
                raise DeploymentContractError("canonical manager source owner is not configured")
            _validate_compose_stat(st, expected_uid=expected_uid)
        elif kind == "revision":
            if expected_uid is None:
                raise DeploymentContractError("canonical manager source owner is not configured")
            _validate_revision_stat(st, expected_uid=expected_uid)
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


def _write_all_fd(fd: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = os.write(fd, view[offset:])
        if written <= 0:
            raise DeploymentContractError("managed file write made no progress")
        offset += written


def _validate_env_stat(st: os.stat_result, *, expected_uid: int) -> None:
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_nlink != 1
        or st.st_uid != expected_uid
        or stat.S_IMODE(st.st_mode) != 0o600
    ):
        raise DeploymentContractError("canonical manager .env must be a private regular file")


def _validate_source_path_stat(
    st: os.stat_result,
    *,
    expected_uid: int,
    label: str,
    require_regular: bool,
) -> None:
    if (
        st.st_uid != expected_uid
        or (require_regular and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1))
        or (stat.S_IMODE(st.st_mode) & 0o022)
    ):
        raise DeploymentContractError(f"{label} must be owner-locked and non-writable")


def _validate_compose_stat(st: os.stat_result, *, expected_uid: int) -> None:
    if (
        st.st_uid != expected_uid
        or not stat.S_ISREG(st.st_mode)
        or st.st_nlink != 1
        or (stat.S_IMODE(st.st_mode) & 0o022)
    ):
        raise DeploymentContractError(
            "canonical docker-compose.yml must be an owner-locked non-writable regular file"
        )


def _validate_revision_stat(st: os.stat_result, *, expected_uid: int) -> None:
    if (
        st.st_uid != expected_uid
        or not stat.S_ISREG(st.st_mode)
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
    evidence = _capture_strict_child_file_at(
        dir_fd,
        parent,
        name,
        kind="env",
        expected_uid=stat_result.st_uid,
    )
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
    expected_uid = evidence.stat_result.st_uid if kind in {"env", "compose", "revision"} else None
    current = _capture_strict_child_file(
        evidence.path.parent,
        evidence.path.name,
        kind=kind,
        expected_uid=expected_uid,
    )
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
        _write_all_fd(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def _write_terminal_audit_once(path: Path, payload: Mapping[str, Any]) -> None:
    operation_id = payload.get("operation_id")
    result = payload.get("result")
    terminal_results = {"aborted", "committed", "rolled_back"}
    if result not in terminal_results:
        _write_audit(path, payload)
        return
    if not isinstance(operation_id, str) or not operation_id:
        raise DeploymentContractError("Map UI auth terminal audit operation_id is missing")
    if _private_file_exists(path):
        for line in _read_private_file(path, label="Map UI auth audit").splitlines():
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DeploymentContractError("Map UI auth audit log is invalid") from exc
            if not isinstance(existing, Mapping):
                raise DeploymentContractError("Map UI auth audit log is invalid")
            if existing.get("operation_id") != operation_id:
                continue
            existing_result = existing.get("result")
            if existing_result not in terminal_results:
                continue
            if existing_result != result:
                raise DeploymentContractError(
                    "Map UI auth audit already contains a conflicting terminal result"
                )
            return
    _write_audit(path, payload)


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
        _write_all_fd(fd, data)
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


def _cleanup_rotation_artifacts(paths: RotationPaths) -> None:
    _unlink_private(paths.frozen_compose_path)
    _unlink_private(paths.backup_path)
    _unlink_private(paths.recovery_path)
    _unlink_private(paths.journal_path)


def _recover_orphan_rotation_artifacts(
    paths: RotationPaths,
) -> MapUiAuthRotationResult | None:
    backup_exists = _private_file_exists(paths.backup_path)
    frozen_exists = _private_file_exists(paths.frozen_compose_path)
    recovery_exists = _private_file_exists(paths.recovery_path)
    if not backup_exists and not frozen_exists and not recovery_exists:
        return None
    if recovery_exists:
        raise DeploymentContractError(
            "Map UI auth rotation has stale recovery env without a journal"
        )
    if frozen_exists:
        raise DeploymentContractError(
            "Map UI auth rotation has stale frozen compose without a journal"
        )
    current_evidence = _capture_strict_child_file(
        paths.env_path.parent,
        paths.env_path.name,
        kind="env",
        expected_uid=paths.env_owner_uid,
    )
    current_sha = current_evidence.sha256
    backup_evidence = _capture_strict_child_file(
        paths.backup_path.parent,
        paths.backup_path.name,
        kind="Map UI auth backup",
    )
    backup_sha = backup_evidence.sha256
    if current_sha != backup_sha:
        raise DeploymentContractError(
            "Map UI auth rotation has ambiguous orphan backup without a journal"
        )
    backup_stat = backup_evidence.stat_result
    artifact_identity = (
        f"{backup_sha}:{backup_stat.st_dev}:{backup_stat.st_ino}:{backup_stat.st_ctime_ns}"
    )
    operation_id = f"orphan-backup-{_sha256(artifact_identity.encode('ascii'))}"
    _write_terminal_audit_once(
        paths.audit_path,
        {
            "operation_id": operation_id,
            "result": "aborted",
            "abort_reason": "orphan_backup_matches_current_env",
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


def _require_env_sha(paths: RotationPaths, expected_sha256: str, *, label: str) -> None:
    evidence = _capture_strict_child_file(
        paths.env_path.parent,
        paths.env_path.name,
        kind="env",
        expected_uid=paths.env_owner_uid,
    )
    if not hmac.compare_digest(evidence.sha256, expected_sha256):
        raise DeploymentContractError(f"{label} SHA evidence no longer matches canonical .env")


def _verify_env_password_for_phase(
    env_values: Mapping[str, str],
    *,
    password: str,
    label: str,
) -> None:
    if not verify_map_pbkdf2_hash(password, env_values[MAP_UI_PASSWORD_HASH_ENV]):
        raise DeploymentContractError(f"{label} password evidence is invalid")


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
        ensure_c6c_state_directory(_ROTATION_STATE_ROOT.parent)
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
    before_ui_sha = payload.get("before_ui_sha256")
    if not isinstance(before_ui_sha, str) or not _SHA256_RE.fullmatch(before_ui_sha):
        raise DeploymentContractError("Map UI auth journal UI evidence is invalid")
    _journal_before_non_ui(payload)
    if phase in {
        "rollback_prepared",
        "rollback_recreate_started",
        "rollback_verified",
        "rolled_back",
    }:
        recovery_sha = payload.get("recovery_env_sha256")
        if not isinstance(recovery_sha, str) or not _SHA256_RE.fullmatch(recovery_sha):
            raise DeploymentContractError("Map UI auth journal recovery SHA evidence is invalid")


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_frozen_compose(
    paths: RotationPaths,
    active_pair: Any,
    runner: CommandRunner,
) -> None:
    active_environment = compatible_pair_image_environment(active_pair)
    env_evidence = _capture_strict_child_file(
        paths.env_path.parent,
        paths.env_path.name,
        kind="env",
        expected_uid=paths.env_owner_uid,
    )
    environment = {
        **_env_values_from_bytes(env_evidence.raw),
        **active_environment,
    }
    config = load_c6c_deployment_config_from_environment(environment)
    compose_evidence = _capture_strict_child_file(
        paths.compose_path.parent,
        paths.compose_path.name,
        kind="compose",
        expected_uid=paths.source_owner_uid,
    )
    raw_compose = _compose_yaml_mapping(compose_evidence.raw, label="canonical docker-compose.yml")
    raw_snapshots = validate_compose_candidate_protected_values(
        raw_compose,
        compose_path=str(paths.compose_path),
        root_env_path=str(paths.env_path),
        environment=environment,
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
        _sanitized_child_env(active_environment),
        None,
        120,
    )
    _revalidate_file_evidence(env_evidence, kind="env")
    _revalidate_file_evidence(compose_evidence, kind="compose")
    if resolved.returncode != 0 or not resolved.stdout:
        raise DeploymentContractError("docker compose config validation failed")
    resolved_compose = _compose_yaml_mapping(
        resolved.stdout.encode("utf-8"),
        label="resolved docker compose config",
    )
    resolved_snapshots = validate_resolved_compose_candidate_protected_values(
        resolved_compose,
        environment=environment,
        compose_path=str(paths.compose_path),
        root_env_path=str(paths.env_path),
    )
    if resolved_snapshots != raw_snapshots:
        raise DeploymentContractError("resolved compose system bind snapshot differs from raw compose")
    validate_resolved_compose_secret_isolation(resolved_compose, config)
    validate_resolved_compose_image_pair(resolved_compose, config, active_pair)
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
    _revalidate_file_evidence(env_evidence, kind="env")
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
    health = _mapping_at(state, "Health").get("Status")
    if health != "healthy":
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


def _validate_active_pair_runtime(
    active_pair: Any,
    env_values: Mapping[str, str],
    compose_path: Path,
) -> None:
    project = env_values.get(COMPOSE_PROJECT_ENV)
    if active_pair.contract_generation != env_values.get("KTDM_C6C_CONTRACT_GENERATION"):
        raise DeploymentContractError("running active compatible pair generation drifted")
    project_snapshot = _compose_project_snapshot(str(project), include_ui=True)
    allowed_services = _canonical_compose_service_names(compose_path)
    expected_services = {service for service, _image_attr in _ACTIVE_PAIR_RUNTIME.values()}
    if not expected_services.issubset(allowed_services):
        raise DeploymentContractError("canonical compose active compatible pair service set is incomplete")
    unknown_services = set(project_snapshot).difference(allowed_services)
    if unknown_services:
        raise DeploymentContractError(
            "compose project contains services outside the canonical compose file: "
            + ", ".join(sorted(unknown_services))
        )
    if not expected_services.issubset(project_snapshot):
        raise DeploymentContractError("running active compatible pair service set is incomplete")
    for container_name, (service_name, image_attr) in _ACTIVE_PAIR_RUNTIME.items():
        payload = project_snapshot.get(service_name, {}).get("_payload")
        if not isinstance(payload, Mapping):
            raise DeploymentContractError("running active compatible pair service is missing")
        expected_image = getattr(active_pair, image_attr)
        labels = _mapping_at(payload, "Config", "Labels")
        state = _mapping_at(payload, "State")
        runtime_name = str(payload.get("Name", "")).lstrip("/")
        if runtime_name != container_name:
            raise DeploymentContractError("running active compatible pair container name drifted")
        if payload.get("Image") != expected_image:
            raise DeploymentContractError("running active compatible pair image drifted")
        if labels.get("com.docker.compose.project") != project:
            raise DeploymentContractError("running active compatible pair project drifted")
        if labels.get("com.docker.compose.service") != service_name:
            raise DeploymentContractError("running active compatible pair service drifted")
        if state.get("Running") is not True:
            raise DeploymentContractError("running active compatible pair is not ready")
        health = _mapping_at(state, "Health").get("Status")
        if health != "healthy":
            raise DeploymentContractError("running active compatible pair is not healthy")
    verify_compatible_pair_image_provenance(
        active_pair,
        require_local_image=lambda image_id: require_local_c6c_image(
            image_id,
            docker_bin=_DOCKER_BIN,
            env=_sanitized_child_env(),
        ),
        inspect_source_revision=lambda image_id, **kwargs: inspect_c6c_image_source_revision(
            image_id,
            docker_bin=_DOCKER_BIN,
            env=_sanitized_child_env(),
            **kwargs,
        ),
    )


def _canonical_compose_service_names(compose_path: Path) -> frozenset[str]:
    try:
        raw = compose_path.read_bytes()
    except OSError as exc:
        raise DeploymentContractError("canonical compose file cannot be read") from exc
    payload = _compose_yaml_mapping(raw, label="canonical docker-compose.yml")
    services = payload.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("canonical docker-compose.yml has no services")
    names = frozenset(name for name in services if isinstance(name, str) and name)
    if len(names) != len(services):
        raise DeploymentContractError("canonical docker-compose.yml service names are invalid")
    return names


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
            labels.pop("com.docker.compose.project.config_files", None)
            labels.pop("com.docker.compose.project.environment_file", None)
    stable = json.dumps(copy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(stable)


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


def _non_ui_snapshot(project: str) -> dict[str, str]:
    snapshot = _compose_project_snapshot(project, include_ui=False)
    result: dict[str, str] = {}
    for service, metadata in snapshot.items():
        allowed = {
            key: value
            for key, value in metadata.items()
            if key in {"Id", "Image", "StartedAt", "RestartCount", "Name"}
        }
        result[service] = _digest_json(allowed)
    return result


def _assert_non_ui_unchanged(
    before: Mapping[str, str],
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
        if not _is_login_redirect(rejected, origin=origin):
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
    if not _is_login_redirect(post_logout, origin=origin):
        raise DeploymentContractError("Map UI post-logout protection verification failed")
    revoked_rejected = _http_request(
        _cookie_opener(),
        f"{origin}{_MAP_UI_PROTECTED_PATH}",
        method="GET",
        headers={"Cookie": active_cookie},
    )
    if not _is_login_redirect(revoked_rejected, origin=origin):
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


def _is_login_redirect(response: Mapping[str, Any], *, origin: str) -> bool:
    if response.get("status") not in {302, 303, 307, 308}:
        return False
    location = urllib.parse.urlsplit(str(response.get("location", "")))
    if location.scheme or location.netloc:
        expected = urllib.parse.urlsplit(origin)
        if location.scheme != expected.scheme or location.netloc != expected.netloc:
            return False
    return location.path == "/login" and not location.query and not location.fragment


def _recover_pending_journal(
    *,
    paths: RotationPaths,
    env_values: Mapping[str, str],
    runner: CommandRunner,
    current_password: str,
    new_password: str,
) -> MapUiAuthRotationResult | None:
    if not _private_file_exists(paths.journal_path):
        return None
    journal = _read_journal(paths.journal_path)
    phase = str(journal["phase"])
    old_sha = str(journal.get("old_env_sha256", ""))
    new_sha = str(journal.get("new_env_sha256", ""))
    recovery_sha = str(journal.get("recovery_env_sha256", ""))
    current_evidence = _capture_strict_child_file(
        paths.env_path.parent,
        paths.env_path.name,
        kind="env",
        expected_uid=paths.env_owner_uid,
    )
    current_sha = current_evidence.sha256
    current_env_values = _env_values_from_bytes(current_evidence.raw)
    allowed_sha = {old_sha, new_sha}
    if recovery_sha:
        allowed_sha.add(recovery_sha)
    if current_sha not in allowed_sha:
        raise DeploymentContractError(
            "pending Map UI auth rotation journal does not match the current .env"
        )
    if phase == "committed":
        _verify_env_password_for_phase(
            current_env_values,
            password=new_password,
            label="committed Map UI auth env",
        )
        return _complete_committed_journal(
            paths=paths,
            journal=journal,
            current_sha=current_sha,
            current_password=new_password,
        )
    if phase == "rolled_back":
        _verify_env_password_for_phase(
            current_env_values,
            password=current_password,
            label="rolled back Map UI auth env",
        )
        return _complete_rolled_back_journal(
            paths=paths,
            journal=journal,
            current_sha=current_sha,
            current_password=current_password,
        )
    if not _private_file_exists(paths.backup_path):
        raise DeploymentContractError("pending Map UI auth rotation journal has no backup")
    backup_sha = _sha256(_read_private_file_bytes(paths.backup_path, label="Map UI auth backup"))
    if not hmac.compare_digest(backup_sha, old_sha):
        raise DeploymentContractError("pending Map UI auth rotation backup does not match journal")
    if current_sha == old_sha and phase == "prepared":
        _verify_env_password_for_phase(
            current_env_values,
            password=current_password,
            label="prepared Map UI auth env",
        )
        _write_terminal_audit_once(
            paths.audit_path,
            {
                "operation_id": journal["operation_id"],
                "result": "aborted",
                "abort_reason": "prepared_journal_without_env_mutation",
                "recorded_at": _utc_now(),
            },
        )
        _cleanup_rotation_artifacts(paths)
        return MapUiAuthRotationResult(
            success=False,
            returncode=1,
            phase="cleared_pending_journal_on_prepared",
            audit_path=str(paths.audit_path),
            stderr="pending Map UI auth rotation journal was already on a terminal old env",
        )
    rollback_phases = {
        "rollback_preparing",
        "rollback_prepared",
        "rollback_recreate_started",
        "rollback_verified",
    }
    if current_sha == old_sha and phase not in rollback_phases:
        raise DeploymentContractError("pending Map UI auth journal phase conflicts with old .env")
    if phase in rollback_phases:
        if current_sha not in {old_sha, new_sha, recovery_sha}:
            raise DeploymentContractError("pending Map UI auth journal phase is not recoverable")
    elif current_sha != new_sha:
        raise DeploymentContractError("pending Map UI auth journal phase is not recoverable")
    if current_sha == new_sha:
        _verify_env_password_for_phase(
            current_env_values,
            password=new_password,
            label="pending new Map UI auth env",
        )
    elif current_sha in {old_sha, recovery_sha}:
        _verify_env_password_for_phase(
            current_env_values,
            password=current_password,
            label="pending rollback Map UI auth env",
        )
    manifest = load_pair_manifest(str(paths.manifest_path))
    manager_source_revision = _manager_source_revision_for_audit(
        paths,
        current_env_values or env_values,
    )
    before_non_ui = _journal_before_non_ui(journal)
    before_ui_sha256 = _journal_before_ui_sha256(journal)
    restored = _restore_backup_with_recovery_session(
        paths,
        active_pair=manifest.active,
        runner=runner,
        operation_id=str(journal["operation_id"]),
        current_password=current_password,
        old_env_sha256=old_sha,
        expected_recovery_sha256=recovery_sha or None,
        allowed_current_sha256=(
            {old_sha, new_sha, recovery_sha}
            if recovery_sha
            else {old_sha, new_sha}
        ),
        allow_existing_recovery_without_sha=phase == "rollback_preparing",
    )
    _verify_restored_runtime(
        restored_values=restored.values,
        active_ui_image=manifest.active.map_ui_image_id,
        before_non_ui=before_non_ui,
        before_ui_sha256=before_ui_sha256,
        current_password=current_password,
    )
    _require_env_sha(paths, restored.sha256, label="recovered Map UI auth env")
    _write_journal(
        paths.journal_path,
        {
            "operation_id": journal["operation_id"],
            "phase": "rollback_verified",
            "recovery_env_sha256": restored.sha256,
        },
    )
    _write_journal(
        paths.journal_path,
        {
            "operation_id": journal["operation_id"],
            "phase": "rolled_back",
            "recovery_env_sha256": restored.sha256,
        },
    )
    _write_terminal_audit_once(
        paths.audit_path,
        {
            "operation_id": journal["operation_id"],
            "result": "rolled_back",
            "recovery_trigger": "pending_journal",
            "rollback_state": "rolled_back_password_state_with_irreversible_session_invalidation",
            "operator_uid": _operator_uid(),
            "manager_source_revision": manager_source_revision,
            "active_pair": _active_pair_audit(manifest.active),
            "env_sha256": {
                "old": old_sha,
                "new": new_sha,
                "recovery": restored.sha256,
            },
            "runtime_sha256": {
                "before_ui": before_ui_sha256,
                "before_non_ui": _digest_json(before_non_ui),
            },
            "journal_phase": "rolled_back",
            "recorded_at": _utc_now(),
        },
    )
    _cleanup_rotation_artifacts(paths)
    del env_values
    return MapUiAuthRotationResult(
        success=False,
        returncode=1,
        phase="recovered_pending_journal",
        audit_path=str(paths.audit_path),
        rollback_state="rolled_back_password_state_with_irreversible_session_invalidation",
        stderr="pending Map UI auth rotation was recovered before starting a new rotation",
    )


def _complete_committed_journal(
    *,
    paths: RotationPaths,
    journal: Mapping[str, Any],
    current_sha: str,
    current_password: str,
) -> MapUiAuthRotationResult:
    new_sha = str(journal["new_env_sha256"])
    if current_sha != new_sha:
        raise DeploymentContractError("committed Map UI auth journal is inconsistent")
    manifest = load_pair_manifest(str(paths.manifest_path))
    values = _load_env_file_values(paths.env_path, expected_uid=paths.env_owner_uid)
    if not verify_map_pbkdf2_hash(current_password, values[MAP_UI_PASSWORD_HASH_ENV]):
        raise DeploymentContractError("committed Map UI auth journal current password is invalid")
    _validate_terminal_runtime(
        paths=paths,
        journal=journal,
        values=values,
        active_ui_image=manifest.active.map_ui_image_id,
        current_password=current_password,
        expected_env_sha256=new_sha,
    )
    manager_source_revision = _validate_manager_source_evidence(
        paths.project_root,
        values,
        source_owner_uid=paths.source_owner_uid,
    )
    _write_terminal_audit_once(
        paths.audit_path,
        {
            "operation_id": journal["operation_id"],
            "result": "committed",
            "operator_uid": _operator_uid(),
            "manager_source_revision": manager_source_revision,
            "active_pair": _active_pair_audit(manifest.active),
            "env_sha256": {
                "old": journal["old_env_sha256"],
                "new": new_sha,
            },
            "runtime_sha256": {
                "before_ui": _journal_before_ui_sha256(journal),
                "before_non_ui": _digest_json(_journal_before_non_ui(journal)),
            },
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
    _cleanup_rotation_artifacts(paths)
    return MapUiAuthRotationResult(
        success=True,
        returncode=0,
        phase="committed",
        audit_path=str(paths.audit_path),
        stderr="previous committed Map UI auth journal residue was completed",
    )


def _complete_rolled_back_journal(
    *,
    paths: RotationPaths,
    journal: Mapping[str, Any],
    current_sha: str,
    current_password: str,
) -> MapUiAuthRotationResult:
    recovery_sha = str(journal["recovery_env_sha256"])
    if current_sha != recovery_sha:
        raise DeploymentContractError("rolled back Map UI auth journal is inconsistent")
    manifest = load_pair_manifest(str(paths.manifest_path))
    values = _load_env_file_values(paths.env_path, expected_uid=paths.env_owner_uid)
    if not verify_map_pbkdf2_hash(current_password, values[MAP_UI_PASSWORD_HASH_ENV]):
        raise DeploymentContractError("rolled back Map UI auth journal current password is invalid")
    _validate_terminal_runtime(
        paths=paths,
        journal=journal,
        values=values,
        active_ui_image=manifest.active.map_ui_image_id,
        current_password=current_password,
        expected_env_sha256=recovery_sha,
    )
    manager_source_revision = _manager_source_revision_for_audit(paths, values)
    _write_terminal_audit_once(
        paths.audit_path,
        {
            "operation_id": journal["operation_id"],
            "result": "rolled_back",
            "rollback_state": "rolled_back_password_state_with_irreversible_session_invalidation",
            "operator_uid": _operator_uid(),
            "manager_source_revision": manager_source_revision,
            "active_pair": _active_pair_audit(manifest.active),
            "env_sha256": {
                "old": journal["old_env_sha256"],
                "new": journal["new_env_sha256"],
                "recovery": recovery_sha,
            },
            "runtime_sha256": {
                "before_ui": _journal_before_ui_sha256(journal),
                "before_non_ui": _digest_json(_journal_before_non_ui(journal)),
            },
            "journal_phase": "rolled_back",
            "recorded_at": _utc_now(),
        },
    )
    _cleanup_rotation_artifacts(paths)
    return MapUiAuthRotationResult(
        success=False,
        returncode=1,
        phase="rolled_back",
        audit_path=str(paths.audit_path),
        rollback_state="rolled_back_password_state_with_irreversible_session_invalidation",
        stderr="previous rolled back Map UI auth journal residue was completed",
    )


def _validate_terminal_runtime(
    *,
    paths: RotationPaths,
    journal: Mapping[str, Any],
    values: Mapping[str, str],
    active_ui_image: str,
    current_password: str,
    expected_env_sha256: str,
) -> None:
    _verify_restored_runtime(
        restored_values=values,
        active_ui_image=active_ui_image,
        before_non_ui=_journal_before_non_ui(journal),
        before_ui_sha256=_journal_before_ui_sha256(journal),
        current_password=current_password,
    )
    _require_env_sha(paths, expected_env_sha256, label="terminal Map UI auth env")


def _read_journal(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(_read_private_file(path, label="Map UI auth journal"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentContractError("pending Map UI auth rotation journal is invalid") from exc
    if not isinstance(payload, Mapping):
        raise DeploymentContractError("pending Map UI auth rotation journal is invalid")
    _validate_journal_payload(payload)
    return payload


def _journal_before_non_ui(journal: Mapping[str, Any]) -> Mapping[str, str]:
    value = journal.get("before_non_ui")
    if not isinstance(value, Mapping):
        raise DeploymentContractError("Map UI auth journal is missing runtime recovery evidence")
    result: dict[str, str] = {}
    for service, digest in value.items():
        if (
            not isinstance(service, str)
            or not service
            or not isinstance(digest, str)
            or not _SHA256_RE.fullmatch(digest)
        ):
            raise DeploymentContractError("Map UI auth journal runtime recovery evidence is invalid")
        result[service] = digest
    return result


def _journal_before_ui_sha256(journal: Mapping[str, Any]) -> str:
    value = journal.get("before_ui_sha256")
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise DeploymentContractError("Map UI auth journal is missing UI recovery evidence")
    return value


def _verify_restored_runtime(
    *,
    restored_values: Mapping[str, str],
    active_ui_image: str,
    before_non_ui: Mapping[str, str],
    before_ui_sha256: str,
    current_password: str,
) -> None:
    restored_ui = _inspect_container(restored_values.get(MAP_UI_CONTAINER_ENV, _MAP_UI_CONTAINER))
    _validate_map_ui_container(restored_ui, restored_values, active_ui_image)
    if _ui_stable_signature(restored_ui) != before_ui_sha256:
        raise DeploymentContractError("restored Map UI runtime config drifted during rollback")
    _assert_non_ui_unchanged(before_non_ui, restored_values[COMPOSE_PROJECT_ENV])
    _verify_auth_lifecycle(
        origin=_production_map_ui_origin(restored_values),
        username=restored_values[MAP_UI_USERNAME_ENV],
        password=current_password,
        expect_cookie_reject=None,
    )


def _rollback_after_failure(
    *,
    paths: RotationPaths,
    env_values: Mapping[str, str],
    active_pair: Any,
    active_ui_image: str,
    before_non_ui: Mapping[str, str],
    runner: CommandRunner,
    original_error: Exception,
    operation_id: str,
    manager_source_revision: str,
    current_password: str,
    before_ui_sha256: str,
    old_env_sha256: str,
    new_env_sha256: str,
) -> MapUiAuthRotationResult:
    try:
        restored = _restore_backup_with_recovery_session(
            paths,
            active_pair=active_pair,
            runner=runner,
            operation_id=operation_id,
            current_password=current_password,
            old_env_sha256=old_env_sha256,
            expected_recovery_sha256=None,
            allowed_current_sha256={old_env_sha256, new_env_sha256},
            allow_existing_recovery_without_sha=False,
        )
        _verify_restored_runtime(
            restored_values=restored.values,
            active_ui_image=active_ui_image,
            before_non_ui=before_non_ui,
            before_ui_sha256=before_ui_sha256,
            current_password=current_password,
        )
        _require_env_sha(paths, restored.sha256, label="rolled back Map UI auth env")
        _write_journal(
            paths.journal_path,
            {
                "operation_id": operation_id,
                "phase": "rollback_verified",
                "recovery_env_sha256": restored.sha256,
            },
        )
        _write_journal(
            paths.journal_path,
            {
                "operation_id": operation_id,
                "phase": "rolled_back",
                "recovery_env_sha256": restored.sha256,
            },
        )
        _write_terminal_audit_once(
            paths.audit_path,
            {
                "operation_id": operation_id,
                "result": "rolled_back",
                "rollback_state": "rolled_back_password_state_with_irreversible_session_invalidation",
                "operator_uid": _operator_uid(),
                "manager_source_revision": manager_source_revision,
                "active_pair": _active_pair_audit(active_pair),
                "env_sha256": {
                    "old": old_env_sha256,
                    "new": new_env_sha256,
                    "recovery": restored.sha256,
                },
                "runtime_sha256": {
                    "before_ui": before_ui_sha256,
                    "before_non_ui": _digest_json(before_non_ui),
                },
                "journal_phase": "rolled_back",
                "recorded_at": _utc_now(),
            },
        )
        _cleanup_rotation_artifacts(paths)
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
                "event_id": str(uuid.uuid4()),
                "phase": "rollback_failed",
                "result": "rollback_attempt_failed",
                "operator_uid": _operator_uid(),
                "manager_source_revision": manager_source_revision,
                "active_pair": _active_pair_audit(active_pair),
                "env_sha256": {
                    "old": old_env_sha256,
                    "new": new_env_sha256,
                    "current": _current_env_sha256_for_audit(paths),
                },
                "runtime_sha256": {
                    "before_ui": before_ui_sha256,
                    "before_non_ui": _digest_json(before_non_ui),
                },
                "journal_phase": _audit_journal_phase(paths.journal_path),
                "error_code": {
                    "original": _sanitized_error_code(original_error),
                    "rollback": _sanitized_error_code(rollback_error),
                },
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
    active_pair: Any,
    runner: CommandRunner,
    operation_id: str,
    current_password: str,
    old_env_sha256: str,
    expected_recovery_sha256: str | None,
    allowed_current_sha256: set[str],
    allow_existing_recovery_without_sha: bool,
) -> RecoveryState:
    current_evidence = _capture_strict_child_file(
        paths.env_path.parent,
        paths.env_path.name,
        kind="env",
        expected_uid=paths.env_owner_uid,
    )
    if current_evidence.sha256 not in allowed_current_sha256:
        raise DeploymentContractError(
            "Map UI auth rollback refuses to overwrite a foreign current .env"
        )
    backup_raw = _read_private_file_bytes(paths.backup_path, label="Map UI auth backup")
    if not hmac.compare_digest(_sha256(backup_raw), old_env_sha256):
        raise DeploymentContractError("Map UI auth backup does not match journal old env")
    backup_document = _env_document_from_bytes(
        backup_raw,
        current_evidence.parent_stat,
        current_evidence.stat_result,
    )
    if not verify_map_pbkdf2_hash(
        current_password,
        backup_document.spans[MAP_UI_PASSWORD_HASH_ENV].value,
    ):
        raise DeploymentContractError("Map UI auth backup does not match current password")
    if _private_file_exists(paths.recovery_path):
        restored = _read_private_file_bytes(paths.recovery_path, label="Map UI auth recovery")
        recovery_sha256 = _sha256(restored)
        if expected_recovery_sha256 is None and not allow_existing_recovery_without_sha:
            raise DeploymentContractError("Map UI auth recovery env is not expected")
        if expected_recovery_sha256 is not None and not hmac.compare_digest(
            recovery_sha256,
            expected_recovery_sha256,
        ):
            raise DeploymentContractError("Map UI auth recovery env does not match journal")
    else:
        if expected_recovery_sha256 is not None:
            raise DeploymentContractError("Map UI auth recovery env is missing")
        recovery_session = _new_session_secret()
        restored = backup_document.rewritten({MAP_UI_SESSION_SECRET_ENV: recovery_session})
        recovery_sha256 = _sha256(restored)
        _write_journal(
            paths.journal_path,
            {
                "operation_id": operation_id,
                "phase": "rollback_preparing",
            },
        )
        _write_private_file(paths.recovery_path, restored, create_exclusive=True)
    recovery_document = _env_document_from_bytes(
        restored,
        current_evidence.parent_stat,
        current_evidence.stat_result,
    )
    _validate_recovery_env_matches_backup(
        backup_document,
        recovery_document,
        current_password=current_password,
    )
    if not verify_map_pbkdf2_hash(
        current_password,
        recovery_document.spans[MAP_UI_PASSWORD_HASH_ENV].value,
    ):
        raise DeploymentContractError("Map UI auth recovery env does not match current password")
    _write_journal(
        paths.journal_path,
        {
            "operation_id": operation_id,
            "phase": "rollback_prepared",
            "recovery_env_sha256": recovery_sha256,
        },
    )
    if not hmac.compare_digest(current_evidence.sha256, recovery_sha256):
        _atomic_replace_file(
            paths.env_path,
            restored,
            parent_stat=current_evidence.parent_stat,
            stat_result=current_evidence.stat_result,
            expected_sha256=current_evidence.sha256,
        )
    _require_env_sha(paths, recovery_sha256, label="recovery Map UI auth env")
    try:
        _unlink_private(paths.frozen_compose_path)
    except DeploymentContractError:
        pass
    _write_frozen_compose(paths, active_pair, runner)
    _write_journal(
        paths.journal_path,
        {
            "operation_id": operation_id,
            "phase": "rollback_recreate_started",
            "recovery_env_sha256": recovery_sha256,
        },
    )
    _compose_recreate_map_ui(paths, str(active_pair.map_ui_image_id), runner)
    values = _load_env_file_values(paths.env_path, expected_uid=paths.env_owner_uid)
    return RecoveryState(values=values, sha256=recovery_sha256)


def _validate_recovery_env_matches_backup(
    backup_document: EnvDocument,
    recovery_document: EnvDocument,
    *,
    current_password: str,
) -> None:
    recovery_secret = recovery_document.spans[MAP_UI_SESSION_SECRET_ENV].value
    if recovery_secret == backup_document.spans[MAP_UI_SESSION_SECRET_ENV].value:
        raise DeploymentContractError("Map UI auth recovery env did not rotate the session")
    expected = backup_document.rewritten({MAP_UI_SESSION_SECRET_ENV: recovery_secret})
    if not hmac.compare_digest(expected, recovery_document.original_bytes):
        raise DeploymentContractError("Map UI auth recovery env does not match backup")
    if not verify_map_pbkdf2_hash(
        current_password,
        recovery_document.spans[MAP_UI_PASSWORD_HASH_ENV].value,
    ):
        raise DeploymentContractError("Map UI auth recovery env does not match current password")


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
            if key in found:
                raise DeploymentContractError("backup .env has duplicate Map UI auth keys")
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


def _digest_json(payload: Mapping[str, Any]) -> str:
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _manager_source_revision_for_audit(
    paths: RotationPaths,
    env_values: Mapping[str, str],
) -> str:
    values = dict(env_values)
    if _MANAGER_SOURCE_REVISION_ENV not in values:
        values = _load_env_file_values(paths.env_path, expected_uid=paths.env_owner_uid)
    return _validate_manager_source_evidence(
        paths.project_root,
        values,
        source_owner_uid=paths.source_owner_uid,
    )


def _current_env_sha256_for_audit(paths: RotationPaths) -> str:
    return _capture_strict_child_file(
        paths.env_path.parent,
        paths.env_path.name,
        kind="env",
        expected_uid=paths.env_owner_uid,
    ).sha256


def _audit_journal_phase(path: Path) -> str:
    if not _private_file_exists(path):
        return "missing"
    try:
        return str(_read_journal(path).get("phase", "unknown"))
    except DeploymentContractError:
        return "unreadable"


def _active_pair_audit(active_pair: Any) -> Mapping[str, str]:
    return {
        "map_image_id": str(active_pair.map_image_id),
        "map_ui_image_id": str(active_pair.map_ui_image_id),
        "map_dagster_image_id": str(active_pair.map_dagster_image_id),
        "map_dagster_daemon_image_id": str(active_pair.map_dagster_daemon_image_id),
        "pinvi_image_id": str(active_pair.pinvi_image_id),
        "map_source_revision": str(active_pair.map_source_revision),
        "pinvi_source_revision": str(active_pair.pinvi_source_revision),
        "contract_generation": str(active_pair.contract_generation),
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sanitized_error(error: Exception) -> str:
    if isinstance(error, DeploymentContractError):
        return str(error)
    return "Map UI auth rotation failed"


def _sanitized_error_code(error: Exception) -> str:
    name = error.__class__.__name__ or "UnknownError"
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
