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
    c6c_state_paths,
    ensure_c6c_state_directory,
    is_pbkdf2_sha256_password_hash,
    pinned_runtime_rebuild_lock_path,
    validate_concierge_ui_canonical_compose_boundary,
)
from kor_travel_docker_manager.services.compose_service import get_project_root

_OVERRIDE_NAME = "docker-compose.override.yml"
_ARCHIVE_DIRECTORY_NAME = ".retired-compose-overrides"
_LEGACY_STAGE_DIRECTORY_NAME = "legacy-compose-override"
_LEGACY_PENDING_DIRECTORY_NAME = "pending"
_STAGED_SOURCE_ENV_NAME = "concierge-source.env"
_MAX_IMPORT_BYTES = 128 * 1024
_TRUSTED_PRODUCTION_PROJECT_ROOT = Path("/opt/kor-travel-docker-manager")
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
class ProjectContext:
    """trusted canonical Compose 실행 경계."""

    root: Path
    env_path: Path
    compose_path: Path


@dataclass(frozen=True)
class LegacyStageContext:
    """legacy 입력을 보관하는 owner-only handoff 경계."""

    root: Path
    pending_path: Path
    archive_directory: Path


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


def stage_legacy_compose_override(
    *,
    source_path: Path,
    project_root: Path | None = None,
    stage_root: Path | None = None,
    lock_path: str | None = None,
    require_root: bool = True,
) -> Path:
    """legacy home 입력을 protected C6c state로 단방향 snapshot한다.

    이 단계는 Docker/Compose를 전혀 호출하지 않는다. source 경로는 legacy checkout의
    single-file override만 허용하며, 그 안의 고정된 Concierge `.env` reference도
    descriptor 기준으로 읽어 같은 owner-only pending snapshot에 넣는다. 이후 retire는
    home checkout을 다시 읽거나 Compose 입력으로 사용하지 않는다.
    """

    context = _prepare_project_context(project_root=project_root, require_root=require_root)
    initial_values = _read_dotenv_values(_read_regular_bytes(context.env_path), "Manager root environment")
    selected_lock_path = _select_lock_path(
        initial_values,
        project_root=context.root,
        lock_path=lock_path,
        require_root=require_root,
    )
    try:
        with c6c_deployment_lock(selected_lock_path):
            _assert_safe_regular_file(context.env_path, require_root=require_root, exact_mode=0o600)
            root_values = _read_dotenv_values(
                _read_regular_bytes(context.env_path), "Manager root environment"
            )
            stage = _prepare_legacy_stage_context(
                root_values,
                project_root=context.root,
                stage_root=stage_root,
                require_root=require_root,
            )
            override_payload = _read_legacy_import_bytes(
                source_path,
                label="legacy override source",
                require_root=require_root,
                expected_name=_OVERRIDE_NAME,
            )
            override = _read_override_payload(override_payload)
            source_env_path = _legacy_concierge_source_env_path(source_path, override)
            source_env_payload = _read_legacy_import_bytes(
                source_env_path,
                label="legacy Concierge source environment",
                require_root=require_root,
                expected_name=".env",
            )
            _stage_legacy_snapshot(
                stage,
                override_payload=override_payload,
                source_env_payload=source_env_payload,
                require_root=require_root,
            )
            return stage.pending_path
    except DeploymentContractError as exc:
        raise LegacyOverrideRetirementError("cannot acquire canonical Compose mutation lock") from exc


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

    context = _prepare_project_context(project_root=project_root, require_root=require_root)
    initial_values = _read_dotenv_values(
        _read_regular_bytes(context.env_path), "Manager root environment"
    )
    selected_lock_path = _select_lock_path(
        initial_values,
        project_root=context.root,
        lock_path=lock_path,
        require_root=require_root,
    )
    try:
        with c6c_deployment_lock(selected_lock_path):
            # lock 확보 뒤 다시 읽어 stage snapshot과 candidate/archive를 하나의 lease로 묶는다.
            _assert_safe_regular_file(context.env_path, require_root=require_root, exact_mode=0o600)
            root_bytes = _read_regular_bytes(context.env_path)
            root_values = _read_dotenv_values(root_bytes, "Manager root environment")
            stage = _prepare_legacy_stage_context(
                root_values,
                project_root=context.root,
                stage_root=None,
                require_root=require_root,
            )
            override_payload, source_env_payload = _read_pending_legacy_snapshot(
                stage, require_root=require_root
            )
            override = _read_override_payload(override_payload)
            updates = _collect_updates(root_values, override, source_env_payload)
            _assert_existing_values_are_compatible(root_values, updates)
            candidate_bytes = _apply_dotenv_updates(root_bytes, updates)
            validation_environment = _read_dotenv_values(
                candidate_bytes, "candidate environment"
            )

            _write_atomic(context.env_path, candidate_bytes, mode=0o600)
            try:
                _validate_canonical_compose_boundary(
                    context.root,
                    context.compose_path,
                    context.env_path,
                    validation_environment,
                    compose_config_runner or _run_canonical_compose_config,
                )
            except Exception:
                _write_atomic(context.env_path, root_bytes, mode=0o600)
                raise

            try:
                archive = _archive_legacy_snapshot(stage, require_root=require_root)
            except LegacyOverrideArchiveDurabilityError:
                # pending directory rename 자체가 성공한 뒤 fsync만 실패한 경우다. 이때 root
                # `.env`를 되돌리면 archive와 canonical 설정이 split-brain이 된다.
                raise
            except Exception:
                _write_atomic(context.env_path, root_bytes, mode=0o600)
                raise

            try:
                _activate_canonical_concierge_locked(
                    context.root,
                    context.compose_path,
                    context.env_path,
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

    context = _prepare_project_context(project_root=project_root, require_root=require_root)
    initial_values = _read_dotenv_values(
        _read_regular_bytes(context.env_path), "Manager root environment"
    )
    selected_lock_path = _select_lock_path(
        initial_values,
        project_root=context.root,
        lock_path=lock_path,
        require_root=require_root,
    )
    try:
        with c6c_deployment_lock(selected_lock_path):
            root_values = _read_dotenv_values(
                _read_regular_bytes(context.env_path), "Manager root environment"
            )
            stage = _prepare_legacy_stage_context(
                root_values,
                project_root=context.root,
                stage_root=None,
                require_root=require_root,
            )
            _assert_pending_legacy_snapshot_is_absent(stage, require_root=require_root)
            validation_environment = _read_dotenv_values(
                _read_regular_bytes(context.env_path), "Manager root environment"
            )
            _activate_canonical_concierge_locked(
                context.root,
                context.compose_path,
                context.env_path,
                validation_environment,
                compose_config_runner or _run_canonical_compose_config,
                compose_up_runner or _run_canonical_concierge_recreate,
            )
    except DeploymentContractError as exc:
        raise LegacyOverrideRetirementError("cannot acquire canonical Compose mutation lock") from exc


def _prepare_project_context(
    *, project_root: Path | None, require_root: bool
) -> ProjectContext:
    if require_root and os.geteuid() != 0:
        raise LegacyOverrideRetirementError("legacy override retirement requires root execution")
    if require_root:
        if project_root is not None:
            raise LegacyOverrideRetirementError("production canonical project root is fixed")
        # installed shim의 ambient project-root 값은 authority가 아니다. production mutation은
        # root-owned trusted release location 하나에서만 canonical Compose를 읽고 실행한다.
        raw_root = _TRUSTED_PRODUCTION_PROJECT_ROOT
    else:
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
    _assert_safe_regular_file(env_path, require_root=require_root, exact_mode=0o600)
    _assert_safe_regular_file(compose_path, require_root=require_root, exact_mode=None)
    return ProjectContext(root=root, env_path=env_path, compose_path=compose_path)


def _select_lock_path(
    values: Mapping[str, str],
    *,
    project_root: Path,
    lock_path: str | None,
    require_root: bool,
) -> str:
    if require_root and lock_path is not None:
        raise LegacyOverrideRetirementError("production retirement lock path is fixed")
    if lock_path is not None:
        return lock_path
    if require_root:
        deployment_environment = values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
        if deployment_environment == "production":
            return c6c_global_mutation_lock_path(values)
        if deployment_environment == "rehearsal":
            required = {
                "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
                "PINVI_ENVIRONMENT": "production",
                "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
            }
            if any(values.get(name, "").strip().lower() != expected for name, expected in required.items()):
                raise LegacyOverrideRetirementError(
                    "legacy override retirement requires the canonical rehearsal/rebuildable environment"
                )
            # stage/retire는 바로 다음 pinned rebuild와 같은 host lease를 쓴다. user-home
            # rehearsal lock을 쓰면 다른 root launcher와 직렬화되지 않아 C6c 경계를 우회한다.
            return pinned_runtime_rebuild_lock_path()
        raise LegacyOverrideRetirementError(
            "legacy override retirement requires production or canonical rehearsal/rebuildable environment"
        )
    return str((project_root / ".legacy-override-retirement.lock").resolve())


def _prepare_legacy_stage_context(
    values: Mapping[str, str],
    *,
    project_root: Path,
    stage_root: Path | None,
    require_root: bool,
) -> LegacyStageContext:
    """C6c state 아래의 fixed owner-only staging root를 준비한다."""

    if require_root:
        if stage_root is not None:
            raise LegacyOverrideRetirementError("production legacy stage root is fixed")
        try:
            legacy_state_path, _ = c6c_state_paths(values)
            root = Path(legacy_state_path).parent / _LEGACY_STAGE_DIRECTORY_NAME
            ensure_c6c_state_directory(root)
        except DeploymentContractError as exc:
            raise LegacyOverrideRetirementError("legacy stage root cannot be prepared") from exc
    else:
        root = stage_root or project_root / ".legacy-compose-override-state"
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root.chmod(0o700)
        except OSError as exc:
            raise LegacyOverrideRetirementError("legacy stage root cannot be prepared") from exc
    _assert_safe_directory(root, require_root=require_root, exact_mode=0o700)
    archive_directory = root / _ARCHIVE_DIRECTORY_NAME
    return LegacyStageContext(
        root=root,
        pending_path=root / _LEGACY_PENDING_DIRECTORY_NAME,
        archive_directory=archive_directory,
    )


def _read_legacy_import_bytes(
    path: Path,
    *,
    label: str,
    require_root: bool,
    expected_name: str,
) -> bytes:
    """unsafe parent 아래의 final regular file을 descriptor 기준으로 snapshot한다."""

    if not path.is_absolute() or path.name != expected_name:
        raise LegacyOverrideRetirementError(f"{label} path is invalid")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise LegacyOverrideRetirementError(f"{label} cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (require_root and metadata.st_uid != 0)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > _MAX_IMPORT_BYTES
        ):
            raise LegacyOverrideRetirementError(f"{label} has unsafe ownership or mode")
        payload = bytearray()
        while len(payload) <= _MAX_IMPORT_BYTES:
            chunk = os.read(descriptor, min(65_536, _MAX_IMPORT_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > _MAX_IMPORT_BYTES:
            raise LegacyOverrideRetirementError(f"{label} exceeds the supported size")
        return bytes(payload)
    except OSError as exc:
        raise LegacyOverrideRetirementError(f"{label} cannot be read safely") from exc
    finally:
        os.close(descriptor)


def _stage_legacy_snapshot(
    stage: LegacyStageContext,
    *,
    override_payload: bytes,
    source_env_payload: bytes,
    require_root: bool,
) -> None:
    """두 legacy 입력을 같은 protected pending directory에 원자적으로 고정한다."""

    _assert_safe_directory(stage.root, require_root=require_root, exact_mode=0o700)
    if stage.pending_path.exists():
        existing_override, existing_source_env = _read_pending_legacy_snapshot(
            stage, require_root=require_root
        )
        if existing_override == override_payload and existing_source_env == source_env_payload:
            return
        raise LegacyOverrideRetirementError("legacy stage already contains a different snapshot")
    try:
        temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=stage.root))
        temporary.chmod(0o700)
    except OSError as exc:
        raise LegacyOverrideRetirementError("legacy stage cannot create a pending snapshot") from exc
    try:
        _write_atomic(temporary / _OVERRIDE_NAME, override_payload, mode=0o600)
        _write_atomic(temporary / _STAGED_SOURCE_ENV_NAME, source_env_payload, mode=0o600)
        _fsync_directory(temporary)
        os.replace(temporary, stage.pending_path)
        _fsync_directory(stage.root)
    except (LegacyOverrideRetirementError, OSError) as exc:
        _remove_temporary_stage_directory(temporary)
        raise LegacyOverrideRetirementError("legacy stage cannot persist a pending snapshot") from exc


def _remove_temporary_stage_directory(path: Path) -> None:
    """이 함수가 만든 exact temporary stage directory만 best-effort로 제거한다."""

    for name in (_OVERRIDE_NAME, _STAGED_SOURCE_ENV_NAME):
        try:
            (path / name).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        path.rmdir()
    except OSError:
        pass


def _read_pending_legacy_snapshot(
    stage: LegacyStageContext, *, require_root: bool
) -> tuple[bytes, bytes]:
    try:
        stage.pending_path.lstat()
    except FileNotFoundError as exc:
        raise LegacyOverrideRetirementError(
            "legacy staged override is required; stage it before retirement"
        ) from exc
    except OSError as exc:
        raise LegacyOverrideRetirementError("legacy staged override cannot be inspected") from exc
    _assert_safe_pending_legacy_snapshot(stage, require_root=require_root)
    return (
        _read_regular_bytes(stage.pending_path / _OVERRIDE_NAME),
        _read_regular_bytes(stage.pending_path / _STAGED_SOURCE_ENV_NAME),
    )


def _assert_pending_legacy_snapshot_is_absent(
    stage: LegacyStageContext, *, require_root: bool
) -> None:
    try:
        stage.pending_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise LegacyOverrideRetirementError("legacy staged override cannot be inspected") from exc
    _assert_safe_pending_legacy_snapshot(stage, require_root=require_root)
    raise LegacyOverrideRetirementError("legacy staged override is still pending; retire it first")


def _assert_safe_pending_legacy_snapshot(
    stage: LegacyStageContext, *, require_root: bool
) -> None:
    _assert_safe_directory(stage.root, require_root=require_root, exact_mode=0o700)
    _assert_safe_directory(stage.pending_path, require_root=require_root, exact_mode=0o700)
    try:
        names = {entry.name for entry in stage.pending_path.iterdir()}
    except OSError as exc:
        raise LegacyOverrideRetirementError("legacy staged override cannot be inspected") from exc
    if names != {_OVERRIDE_NAME, _STAGED_SOURCE_ENV_NAME}:
        raise LegacyOverrideRetirementError("legacy staged override has unexpected content")
    _assert_safe_regular_file(
        stage.pending_path / _OVERRIDE_NAME, require_root=require_root, exact_mode=0o600
    )
    _assert_safe_regular_file(
        stage.pending_path / _STAGED_SOURCE_ENV_NAME, require_root=require_root, exact_mode=0o600
    )


def _archive_legacy_snapshot(stage: LegacyStageContext, *, require_root: bool) -> Path:
    """검증된 pending snapshot directory를 동일 protected filesystem 안에서 archive한다."""

    _assert_safe_pending_legacy_snapshot(stage, require_root=require_root)
    try:
        stage.archive_directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise LegacyOverrideRetirementError("legacy archive directory cannot be prepared") from exc
    _assert_safe_directory(stage.archive_directory, require_root=require_root, exact_mode=0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = stage.archive_directory / f"{_OVERRIDE_NAME}.{timestamp}.retired"
    try:
        archive_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise LegacyOverrideRetirementError("legacy archive destination cannot be inspected") from exc
    else:
        raise LegacyOverrideRetirementError("legacy override archive destination already exists")
    try:
        os.replace(stage.pending_path, archive_path)
    except OSError as exc:
        raise LegacyOverrideRetirementError("legacy staged override could not be archived") from exc
    try:
        _fsync_directory(stage.archive_directory)
        _fsync_directory(stage.root)
    except LegacyOverrideRetirementError as exc:
        raise LegacyOverrideArchiveDurabilityError(
            "legacy override archive durability is uncertain; root environment was retained"
        ) from exc
    return archive_path


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


def _collect_updates(
    root_values: Mapping[str, str],
    override: Mapping[str, Any],
    source_payload: bytes,
) -> dict[str, str]:
    geo_values = _collect_geo_backup_values(override)
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
    return values_by_target


def _legacy_concierge_source_env_path(
    override_path: Path, override: Mapping[str, Any]
) -> Path:
    """legacy UI의 고정된 sibling `.env`만 staging source로 계산한다.

    여기서는 source directory를 resolve하거나 Compose cwd로 사용하지 않는다. final file은
    이후 `O_NOFOLLOW` descriptor와 fstat으로 검증하므로, user-writable parent는 단지
    stage를 막을 수 있을 뿐 root-owned source 내용을 바꾸는 입력 경계가 될 수 없다.
    """

    if not override_path.is_absolute() or override_path.name != _OVERRIDE_NAME:
        raise LegacyOverrideRetirementError("legacy override source path is invalid")
    if override_path.parent.name != "kor-travel-docker-manager":
        raise LegacyOverrideRetirementError("legacy override source path is not recognized")
    source_env_path = override_path.parent.parent / "kor-travel-concierge" / ".env"
    _collect_geo_backup_values(override)
    _assert_legacy_concierge_source_reference(override, source_env_path)
    return source_env_path


def _assert_legacy_concierge_source_reference(
    override: Mapping[str, Any], source_env_path: Path
) -> None:
    """stage가 허용하는 legacy UI source reference를 exact file 하나로 고정한다."""

    services = override.get("services")
    if not isinstance(services, Mapping):
        raise LegacyOverrideRetirementError("legacy override service set is not recognized")
    ui_service = services.get("kor-travel-concierge-ui")
    if not isinstance(ui_service, Mapping):
        raise LegacyOverrideRetirementError("legacy Concierge UI override shape is not recognized")

    source_reference = ui_service.get("env_file")
    if source_reference == ["../kor-travel-concierge/.env"]:
        return
    if (
        isinstance(source_reference, list)
        and len(source_reference) == 1
        and isinstance(source_reference[0], Mapping)
        and source_reference[0].get("path") == str(source_env_path)
        and source_reference[0].get("required") is True
        and (
            set(source_reference[0]) == {"path", "required"}
            or (
                set(source_reference[0]) == {"path", "required", "format"}
                and source_reference[0].get("format") == "raw"
            )
        )
    ):
        return
    raise LegacyOverrideRetirementError("legacy Concierge UI source reference is not recognized")


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


def _read_override_payload(payload: bytes) -> Mapping[str, Any]:
    try:
        document = yaml.safe_load(payload.decode("utf-8"))
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
