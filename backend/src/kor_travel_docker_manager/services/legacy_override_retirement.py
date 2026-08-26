"""Legacy Compose override를 single-file boundary로 안전하게 이관한다."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    DeploymentContractError,
    c6c_deployment_lock,
    c6c_global_mutation_lock_path,
    is_pbkdf2_sha256_password_hash,
    validate_concierge_ui_canonical_compose_boundary,
)
from kor_travel_docker_manager.services.compose_service import get_project_root

_OVERRIDE_NAME = "docker-compose.override.yml"
_ARCHIVE_DIRECTORY_NAME = ".retired-compose-overrides"
_GEO_SERVICES = (
    "kor-travel-geo-api",
    "kor-travel-geo-dagster",
    "kor-travel-geo-dagster-daemon",
)
_GEO_ENV_MIGRATIONS = {
    "KTG_BACKUP_SCHEDULE_ENABLED": "KOR_TRAVEL_GEO_BACKUP_SCHEDULE_ENABLED",
    "KTG_BACKUP_SCHEDULE_INTERVAL_HOURS": "KOR_TRAVEL_GEO_BACKUP_SCHEDULE_INTERVAL_HOURS",
    "KTG_BACKUP_ARTIFACT_TTL_DAYS": "KOR_TRAVEL_GEO_BACKUP_ARTIFACT_TTL_DAYS",
    "KTG_BACKUP_RETENTION_KEEP_MIN": "KOR_TRAVEL_GEO_BACKUP_RETENTION_KEEP_MIN",
}
_CONCIERGE_SOURCE_ENV_MIGRATIONS = {
    "API_KEYS": "KOR_TRAVEL_CONCIERGE_API_KEYS",
    "APP_ENV": "KOR_TRAVEL_CONCIERGE_APP_ENV",
    "API_AUTH_ENABLED": "KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED",
    "KTC_ADMIN_USERNAME": "KOR_TRAVEL_CONCIERGE_UI_ADMIN_USERNAME",
    "KTC_ADMIN_PASSWORD_HASH": "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PASSWORD_HASH",
    "KTC_UI_SESSION_SECRET": "KOR_TRAVEL_CONCIERGE_UI_SESSION_SECRET",
    "KTC_ADMIN_PROXY_SECRET": "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET",
    "KTC_UI_TRUST_FORWARDED_IPS": "KOR_TRAVEL_CONCIERGE_UI_TRUST_FORWARDED_IPS",
    "KTC_UI_PUBLIC_ORIGINS": "KOR_TRAVEL_CONCIERGE_UI_PUBLIC_ORIGINS",
}
_CONCIERGE_LEGACY_ROOT_VWORLD_ENV = "NEXT_PUBLIC_VWORLD_API_KEY"
_CONCIERGE_ROOT_VWORLD_ENV = "KOR_TRAVEL_CONCIERGE_UI_VWORLD_SERVICE_KEY"
_CONCIERGE_ROOT_BACKEND_KEY_ENV = "KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY"
_CONCIERGE_ROOT_PUBLIC_API_BASE_ENV = "KOR_TRAVEL_CONCIERGE_UI_PUBLIC_API_BASE_URL"
_CONCIERGE_REPO_DIR_ENV = "KOR_TRAVEL_CONCIERGE_REPO_DIR"
_FORBIDDEN_COMPOSE_AMBIENT_ENV_NAMES = frozenset(
    {
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_PATH_SEPARATOR",
        "KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE",
        "KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT",
    }
)


class LegacyOverrideRetirementError(RuntimeError):
    """Legacy override 이관 사전 조건 또는 검증 실패."""


class LegacyOverrideArchiveDurabilityError(LegacyOverrideRetirementError):
    """override rename 뒤 archive directory durability를 확정하지 못한 상태."""


class LegacyRootEnvironmentDurabilityError(LegacyOverrideRetirementError):
    """root `.env` rename 뒤 directory durability를 확정하지 못한 상태."""


class LegacyOverrideActivationError(LegacyOverrideRetirementError):
    """archive 뒤 canonical Concierge 재생성을 완료하지 못한 상태."""


@dataclass(frozen=True)
class ComposeConfigResult:
    """비밀을 출력하지 않고 Compose resolution만 전달하는 결과."""

    returncode: int
    stdout: str


ComposeConfigRunner = Callable[[list[str], Path, Mapping[str, str]], ComposeConfigResult]
ComposeUpRunner = Callable[[list[str], Path, Mapping[str, str]], int]

_CONCIERGE_RECREATE_SERVICES = (
    "kor-travel-concierge-api",
    "kor-travel-concierge-mcp",
    "kor-travel-concierge-scheduler",
    "kor-travel-concierge-ui",
)


def retire_legacy_compose_override(
    *,
    project_root: Path | None = None,
    compose_config_runner: ComposeConfigRunner | None = None,
    compose_up_runner: ComposeUpRunner | None = None,
    lock_path: str | None = None,
    require_root: bool = True,
) -> Path:
    """legacy override를 이관·archive하고 canonical Concierge를 즉시 재생성한다.

    C6c host-wide mutation lock 아래에서 raw/resolved Compose contract를 통과한
    candidate만 archive한다. archive 성공 뒤에는 같은 lock 안에서 API/MCP/scheduler/UI를
    canonical single-file source로 재생성한다. ``project_root``·runner·``require_root``
    인수는 단위 테스트 격리용이다.
    """

    root, env_path, compose_path, override_path = _prepare_project_context(
        project_root=project_root,
        require_root=require_root,
        require_override=True,
    )
    initial_values = _read_dotenv_values(_read_regular_bytes(env_path), "Manager root environment")
    selected_lock_path = _select_lock_path(
        initial_values,
        project_root=root,
        lock_path=lock_path,
        require_root=require_root,
    )
    try:
        with c6c_deployment_lock(selected_lock_path):
            # lock 확보 뒤 다시 읽어 snapshot과 candidate/archive를 하나의 lease로 묶는다.
            _assert_safe_regular_file(env_path, require_root=require_root, exact_mode=0o600)
            _assert_safe_regular_file(override_path, require_root=require_root, exact_mode=0o600)
            root_bytes = _read_regular_bytes(env_path)
            root_values = _read_dotenv_values(root_bytes, "Manager root environment")
            override = _read_override(override_path)
            updates = _collect_updates(root, root_values, override)
            _assert_existing_values_are_compatible(root_values, updates)
            candidate_bytes = _apply_dotenv_updates(root_bytes, updates)
            validation_environment = _read_dotenv_values(
                candidate_bytes, "candidate environment"
            )

            _write_atomic(env_path, candidate_bytes, mode=0o600)
            try:
                _validate_canonical_compose_boundary(
                    root,
                    compose_path,
                    env_path,
                    validation_environment,
                    compose_config_runner or _run_canonical_compose_config,
                )
            except Exception:
                _write_atomic(env_path, root_bytes, mode=0o600)
                raise

            try:
                archive = _archive_override(override_path, root, require_root=require_root)
            except LegacyOverrideArchiveDurabilityError:
                # rename 자체가 성공한 뒤 directory fsync만 실패한 경우다. 이때 root `.env`를
                # 과거 값으로 되돌리면 archived override와 canonical 설정이 split-brain이 된다.
                raise
            except Exception:
                _write_atomic(env_path, root_bytes, mode=0o600)
                raise

            try:
                _activate_canonical_concierge_locked(
                    root,
                    compose_path,
                    env_path,
                    validation_environment,
                    compose_config_runner or _run_canonical_compose_config,
                    compose_up_runner or _run_canonical_concierge_recreate,
                )
            except Exception as exc:
                raise LegacyOverrideActivationError(
                    "legacy override was retired but canonical Concierge recreation failed; "
                    "run compose-boundary activate-concierge --confirm after remediation"
                ) from exc
            return archive
    except DeploymentContractError as exc:
        raise LegacyOverrideRetirementError("cannot acquire canonical Compose mutation lock") from exc


def activate_canonical_concierge(
    *,
    project_root: Path | None = None,
    compose_config_runner: ComposeConfigRunner | None = None,
    compose_up_runner: ComposeUpRunner | None = None,
    lock_path: str | None = None,
    require_root: bool = True,
) -> None:
    """archive 완료 뒤 fail-closed retry에 쓰는 공식 Concierge 단일-file 재생성 경로."""

    root, env_path, compose_path, override_path = _prepare_project_context(
        project_root=project_root,
        require_root=require_root,
        require_override=False,
    )
    initial_values = _read_dotenv_values(_read_regular_bytes(env_path), "Manager root environment")
    selected_lock_path = _select_lock_path(
        initial_values,
        project_root=root,
        lock_path=lock_path,
        require_root=require_root,
    )
    try:
        with c6c_deployment_lock(selected_lock_path):
            _assert_override_is_absent(override_path)
            validation_environment = _read_dotenv_values(
                _read_regular_bytes(env_path), "Manager root environment"
            )
            _activate_canonical_concierge_locked(
                root,
                compose_path,
                env_path,
                validation_environment,
                compose_config_runner or _run_canonical_compose_config,
                compose_up_runner or _run_canonical_concierge_recreate,
            )
    except DeploymentContractError as exc:
        raise LegacyOverrideRetirementError("cannot acquire canonical Compose mutation lock") from exc


def _prepare_project_context(
    *, project_root: Path | None, require_root: bool, require_override: bool
) -> tuple[Path, Path, Path, Path]:
    if require_root and os.geteuid() != 0:
        raise LegacyOverrideRetirementError("legacy override retirement requires root execution")
    if require_root and os.environ.get("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT"):
        raise LegacyOverrideRetirementError("legacy override retirement rejects project root override")
    raw_root = project_root or Path(get_project_root())
    try:
        raw_root_metadata = raw_root.lstat()
    except OSError as exc:
        raise LegacyOverrideRetirementError("required directory cannot be inspected") from exc
    if not stat.S_ISDIR(raw_root_metadata.st_mode):
        raise LegacyOverrideRetirementError("required directory has unsafe ownership or mode")
    root = raw_root.resolve()
    _assert_safe_directory(root, require_root=require_root)
    env_path = root / ".env"
    compose_path = root / "docker-compose.yml"
    override_path = root / _OVERRIDE_NAME
    _assert_safe_regular_file(env_path, require_root=require_root, exact_mode=0o600)
    _assert_safe_regular_file(compose_path, require_root=require_root, exact_mode=None)
    if require_override:
        _assert_safe_regular_file(override_path, require_root=require_root, exact_mode=0o600)
    return root, env_path, compose_path, override_path


def _select_lock_path(
    values: Mapping[str, str],
    *,
    project_root: Path,
    lock_path: str | None,
    require_root: bool,
) -> str:
    if require_root and lock_path is not None:
        raise LegacyOverrideRetirementError("production retirement lock path is fixed")
    if require_root and values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower() != "production":
        raise LegacyOverrideRetirementError(
            "legacy override retirement requires explicit production deployment environment"
        )
    if lock_path is not None:
        return lock_path
    if require_root:
        return c6c_global_mutation_lock_path(values)
    return str((project_root / ".legacy-override-retirement.lock").resolve())


def _validate_canonical_compose_boundary(
    project_root: Path,
    compose_path: Path,
    env_path: Path,
    environment: Mapping[str, str],
    runner: ComposeConfigRunner,
) -> None:
    try:
        raw_document = yaml.safe_load(_read_regular_bytes(compose_path).decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise LegacyOverrideRetirementError("canonical Compose cannot be parsed") from exc
    if not isinstance(raw_document, Mapping):
        raise LegacyOverrideRetirementError("canonical Compose is invalid")
    result = runner(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_path),
            "--file",
            str(compose_path),
            "config",
            "--format",
            "json",
        ],
        project_root,
        environment,
    )
    if result.returncode != 0:
        raise LegacyOverrideRetirementError("canonical Compose validation failed")
    try:
        resolved_document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LegacyOverrideRetirementError("canonical Compose resolved output is invalid") from exc
    if not isinstance(resolved_document, Mapping):
        raise LegacyOverrideRetirementError("canonical Compose resolved output is invalid")
    try:
        validate_concierge_ui_canonical_compose_boundary(
            raw_document,
            resolved_document,
            environment=environment,
        )
    except ComposeCandidateContractError as exc:
        raise LegacyOverrideRetirementError("canonical Compose C6c contract is invalid") from exc


def _activate_canonical_concierge_locked(
    project_root: Path,
    compose_path: Path,
    env_path: Path,
    environment: Mapping[str, str],
    config_runner: ComposeConfigRunner,
    up_runner: ComposeUpRunner,
) -> None:
    _validate_canonical_compose_boundary(
        project_root, compose_path, env_path, environment, config_runner
    )
    command = [
        "docker",
        "compose",
        "--env-file",
        str(env_path),
        "--file",
        str(compose_path),
        "up",
        "--detach",
        "--no-deps",
        "--force-recreate",
        *_CONCIERGE_RECREATE_SERVICES,
    ]
    if up_runner(command, project_root, environment) != 0:
        raise LegacyOverrideActivationError("canonical Concierge recreation failed")


def _assert_override_is_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise LegacyOverrideRetirementError("legacy override state cannot be inspected") from exc
    raise LegacyOverrideRetirementError("legacy override is still present; retire it first")


def _collect_updates(
    project_root: Path,
    root_values: Mapping[str, str],
    override: Mapping[str, Any],
) -> dict[str, str]:
    geo_values = _collect_geo_backup_values(override)
    source_env_path = _concierge_source_env_path(project_root, root_values)
    _assert_safe_regular_file(
        source_env_path,
        require_root=False,
        exact_mode=None,
        require_private=True,
    )
    source_payload = _read_regular_bytes(source_env_path)
    _assert_no_duplicate_dotenv_assignments(
        source_payload, set(_CONCIERGE_SOURCE_ENV_MIGRATIONS), "Concierge source environment"
    )
    source_values = _read_dotenv_values(source_payload, "Concierge source environment")
    source_updates = {
        target: _required_value(source_values, source, "Concierge source environment")
        for source, target in _CONCIERGE_SOURCE_ENV_MIGRATIONS.items()
    }
    _validate_concierge_source_values(source_updates, root_values)
    return {
        **geo_values,
        **source_updates,
        _CONCIERGE_ROOT_VWORLD_ENV: _required_value(
            root_values,
            _CONCIERGE_LEGACY_ROOT_VWORLD_ENV,
            "Manager root environment",
        ),
        _CONCIERGE_ROOT_PUBLIC_API_BASE_ENV: "",
    }


def _collect_geo_backup_values(override: Mapping[str, Any]) -> dict[str, str]:
    if set(override) != {"services"}:
        raise LegacyOverrideRetirementError("legacy override has unsupported top-level content")
    services = override.get("services")
    expected_services = {*_GEO_SERVICES, "kor-travel-concierge-ui"}
    if not isinstance(services, Mapping) or set(services) != expected_services:
        raise LegacyOverrideRetirementError("legacy override service set is not recognized")

    values_by_target: dict[str, str] = {}
    for service_name in _GEO_SERVICES:
        service = services.get(service_name)
        if not isinstance(service, Mapping) or set(service) != {"environment"}:
            raise LegacyOverrideRetirementError("legacy Geo override shape is not recognized")
        environment = service.get("environment")
        if not isinstance(environment, Mapping) or set(environment) != set(_GEO_ENV_MIGRATIONS):
            raise LegacyOverrideRetirementError("legacy Geo backup environment is not recognized")
        for source_name, target_name in _GEO_ENV_MIGRATIONS.items():
            value = _scalar_value(environment.get(source_name), "legacy Geo backup value")
            current = values_by_target.setdefault(target_name, value)
            if current != value:
                raise LegacyOverrideRetirementError(
                    "legacy Geo backup values disagree between services"
                )

    ui_service = services.get("kor-travel-concierge-ui")
    if not isinstance(ui_service, Mapping) or set(ui_service) != {"command", "env_file"}:
        raise LegacyOverrideRetirementError("legacy Concierge UI override shape is not recognized")
    # legacy UI env_file의 path나 내용은 trusted input으로 쓰지 않는다. source .env는
    # Manager root의 canonical sibling source에서만 다시 찾는다.
    return values_by_target


def _concierge_source_env_path(
    project_root: Path, root_values: Mapping[str, str]
) -> Path:
    source_root_value = root_values.get(_CONCIERGE_REPO_DIR_ENV, "../kor-travel-concierge")
    if not isinstance(source_root_value, str) or not source_root_value:
        raise LegacyOverrideRetirementError("Concierge source root is invalid")
    source_root = Path(source_root_value)
    if not source_root.is_absolute():
        source_root = project_root / source_root
    try:
        source_metadata = source_root.lstat()
        if not stat.S_ISDIR(source_metadata.st_mode) or stat.S_IMODE(source_metadata.st_mode) & 0o022:
            raise LegacyOverrideRetirementError("Concierge source root has unsafe ownership or mode")
        source_root = source_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LegacyOverrideRetirementError("Concierge source root cannot be resolved") from exc
    if source_root.parent != project_root.parent:
        raise LegacyOverrideRetirementError("Concierge source root must be a project sibling")
    return source_root / ".env"


def _validate_concierge_source_values(
    updates: Mapping[str, str], root_values: Mapping[str, str]
) -> None:
    username = updates["KOR_TRAVEL_CONCIERGE_UI_ADMIN_USERNAME"]
    password_hash = updates["KOR_TRAVEL_CONCIERGE_UI_ADMIN_PASSWORD_HASH"]
    session_secret = updates["KOR_TRAVEL_CONCIERGE_UI_SESSION_SECRET"]
    proxy_secret = updates["KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET"]
    api_keys = updates["KOR_TRAVEL_CONCIERGE_API_KEYS"]
    backend_key = _required_value(
        root_values, _CONCIERGE_ROOT_BACKEND_KEY_ENV, "Manager root environment"
    )
    if (
        username != username.strip()
        or any(character.isspace() for character in username)
        or not is_pbkdf2_sha256_password_hash(password_hash)
        or len(session_secret) < 32
        or any(character.isspace() for character in session_secret)
        or len(proxy_secret) < 32
        or any(character.isspace() for character in proxy_secret)
    ):
        raise LegacyOverrideRetirementError("Concierge UI source authentication values are invalid")
    api_key_set = api_keys.split(",")
    if (
        not api_keys
        or any(not value or value != value.strip() for value in api_key_set)
        or backend_key not in api_key_set
    ):
        raise LegacyOverrideRetirementError(
            "Concierge backend key is not an exact member of the API key set"
        )
    if (
        updates["KOR_TRAVEL_CONCIERGE_APP_ENV"] != "production"
        or updates["KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED"] != "true"
    ):
        raise LegacyOverrideRetirementError(
            "Concierge API source must keep production authentication enabled"
        )
    if updates["KOR_TRAVEL_CONCIERGE_UI_TRUST_FORWARDED_IPS"] not in {"true", "false"}:
        raise LegacyOverrideRetirementError("Concierge trusted-proxy flag is invalid")
    _validate_https_origin_list(updates["KOR_TRAVEL_CONCIERGE_UI_PUBLIC_ORIGINS"])


def _validate_https_origin_list(value: str) -> None:
    origins = [origin.strip() for origin in value.split(",") if origin.strip()]
    if value and (not origins or ",".join(origins) != value):
        raise LegacyOverrideRetirementError("Concierge public origin list is invalid")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise LegacyOverrideRetirementError("Concierge public origin list is invalid")
    if len(origins) != len(set(origins)):
        raise LegacyOverrideRetirementError("Concierge public origin list is invalid")


def _required_value(values: Mapping[str, str], name: str, label: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value or "\r" in value or "\n" in value:
        raise LegacyOverrideRetirementError(f"{label} is missing a required migration value")
    return value


def _scalar_value(value: Any, label: str) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (str, int)):
        normalized = str(value)
        if normalized and "\r" not in normalized and "\n" not in normalized:
            return normalized
    raise LegacyOverrideRetirementError(f"{label} is invalid")


def _read_override(path: Path) -> Mapping[str, Any]:
    try:
        document = yaml.safe_load(_read_regular_bytes(path).decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise LegacyOverrideRetirementError("legacy override cannot be parsed") from exc
    if not isinstance(document, Mapping):
        raise LegacyOverrideRetirementError("legacy override is invalid")
    return document


def _read_dotenv_values(payload: bytes, label: str) -> dict[str, str]:
    try:
        values = dotenv_values(stream=StringIO(payload.decode("utf-8")), interpolate=False)
    except (UnicodeError, ValueError) as exc:
        raise LegacyOverrideRetirementError(f"{label} cannot be parsed") from exc
    return {key: value or "" for key, value in values.items() if isinstance(key, str)}


def _assert_existing_values_are_compatible(
    existing: Mapping[str, str], updates: Mapping[str, str]
) -> None:
    for name, desired in updates.items():
        current = existing.get(name, "")
        if current and current != desired:
            raise LegacyOverrideRetirementError(
                "existing Manager root migration value disagrees with the legacy source"
            )


def _apply_dotenv_updates(original: bytes, updates: Mapping[str, str]) -> bytes:
    try:
        text = original.decode("utf-8")
    except UnicodeError as exc:
        raise LegacyOverrideRetirementError("Manager root environment cannot be decoded") from exc
    if "\x00" in text:
        raise LegacyOverrideRetirementError("Manager root environment is invalid")
    _assert_no_duplicate_dotenv_assignments(
        original, set(updates), "Manager root environment"
    )
    seen: set[str] = set()
    updated_lines: list[str] = []
    for line in text.splitlines():
        name = _dotenv_assignment_name(line)
        if name is not None and name in updates:
            if name in seen:
                raise LegacyOverrideRetirementError(
                    "Manager root environment has duplicate migration variables"
                )
            seen.add(name)
            updated_lines.append(f"{name}={_quote_dotenv_value(updates[name])}")
        else:
            updated_lines.append(line)
    for name, value in updates.items():
        if name not in seen:
            updated_lines.append(f"{name}={_quote_dotenv_value(value)}")
    return ("\n".join(updated_lines) + "\n").encode("utf-8")


def _quote_dotenv_value(value: str) -> str:
    if "\r" in value or "\n" in value or "\x00" in value:
        raise LegacyOverrideRetirementError("migration value has an unsupported control character")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _assert_no_duplicate_dotenv_assignments(
    payload: bytes, names: set[str], label: str
) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise LegacyOverrideRetirementError(f"{label} cannot be parsed") from exc
    seen: set[str] = set()
    for line in text.splitlines():
        name = _dotenv_assignment_name(line)
        if name in names:
            if name in seen:
                raise LegacyOverrideRetirementError(
                    f"{label} has unsupported duplicate migration variables"
                )
            seen.add(name)


def _dotenv_assignment_name(line: str) -> str | None:
    """python-dotenv가 수용하는 단순 assignment 선언을 같은 방식으로 정규화한다."""

    match = re.match(
        r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=",
        line,
    )
    return match.group(1) if match is not None else None


def _run_canonical_compose_config(
    command: list[str], project_root: Path, values: Mapping[str, str]
) -> ComposeConfigResult:
    environment = _canonical_compose_environment(values)
    completed = subprocess.run(
        command,
        cwd=project_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return ComposeConfigResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
    )


def _run_canonical_concierge_recreate(
    command: list[str], project_root: Path, values: Mapping[str, str]
) -> int:
    completed = subprocess.run(
        command,
        cwd=project_root,
        env=_canonical_compose_environment(values),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode


def _canonical_compose_environment(values: Mapping[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    for name in _FORBIDDEN_COMPOSE_AMBIENT_ENV_NAMES:
        environment.pop(name, None)
    environment.update(values)
    for name in _FORBIDDEN_COMPOSE_AMBIENT_ENV_NAMES:
        environment.pop(name, None)
    return environment


def _archive_override(override_path: Path, project_root: Path, *, require_root: bool) -> Path:
    archive_directory = project_root / _ARCHIVE_DIRECTORY_NAME
    try:
        archive_directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _assert_safe_directory(archive_directory, require_root=require_root, exact_mode=0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = archive_directory / f"{_OVERRIDE_NAME}.{timestamp}.retired"
    if archive_path.exists():
        raise LegacyOverrideRetirementError("legacy override archive destination already exists")
    _assert_safe_regular_file(override_path, require_root=require_root, exact_mode=0o600)
    try:
        os.replace(override_path, archive_path)
    except OSError as exc:
        raise LegacyOverrideRetirementError("legacy override could not be archived") from exc
    try:
        _fsync_directory(archive_directory)
        _fsync_directory(project_root)
    except LegacyOverrideRetirementError as exc:
        raise LegacyOverrideArchiveDurabilityError(
            "legacy override archive durability is uncertain; root environment was retained"
        ) from exc
    return archive_path


def _assert_safe_directory(
    path: Path, *, require_root: bool, exact_mode: int = 0o755
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LegacyOverrideRetirementError("required directory cannot be inspected") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != exact_mode
        or (require_root and metadata.st_uid != 0)
    ):
        raise LegacyOverrideRetirementError("required directory has unsafe ownership or mode")


def _assert_safe_regular_file(
    path: Path,
    *,
    require_root: bool,
    exact_mode: int | None,
    require_private: bool = False,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LegacyOverrideRetirementError("required migration input cannot be inspected") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or (require_root and metadata.st_uid != 0)
        or (exact_mode is not None and stat.S_IMODE(metadata.st_mode) != exact_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or (require_private and stat.S_IMODE(metadata.st_mode) & 0o077)
    ):
        raise LegacyOverrideRetirementError("required migration input has unsafe ownership or mode")


def _read_regular_bytes(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read()
    except OSError as exc:
        raise LegacyOverrideRetirementError("required migration input cannot be read") from exc


def _write_atomic(path: Path, payload: bytes, *, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(path.parent)
    except (LegacyOverrideRetirementError, OSError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if replaced:
            raise LegacyRootEnvironmentDurabilityError(
                "Manager root environment durability is uncertain; legacy override was retained"
            ) from exc
        raise LegacyOverrideRetirementError("Manager root environment cannot be updated atomically") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise LegacyOverrideRetirementError("migration directory cannot be synchronized") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise LegacyOverrideRetirementError("migration directory cannot be synchronized") from exc
    finally:
        os.close(descriptor)
