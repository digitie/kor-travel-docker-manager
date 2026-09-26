import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, cast

import yaml
from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import (
    _MAP_APPLICATION_SCHEMA_SERVICE,
    _MAP_RUNTIME_SERVICES,
    _PINVI_ADMIN_BOOTSTRAP_SERVICE,
    _PINVI_API_SERVICE,
    C6cBuildProvenance,
    C6cDeploymentConfig,
    CandidateSystemBindSnapshot,
    PinviCancelProbeState,
    _assert_candidate_single_file_boundary,
    _expand_env_path,
    assert_compose_mutation_allowed,
    assert_manager_mutation_allowed,
    assert_pinned_runtime_rebuild_allowed,
    c6c_deployment_lock,
    c6c_global_mutation_lock_path,
    c6c_state_paths,
    compose_volume_graph_hash,
    derive_curation_service_principal_environment,
    inspect_c6c_image_source_revision,
    load_c6c_deployment_config_from_environment,
    pinned_runtime_rebuild_lock,
    revalidate_candidate_system_bind_snapshots,
    run_pinvi_canonical_smoke,
    validate_c6c_build_source_wiring,
    validate_c6c_operation_tokens,
    validate_compose_candidate_protected_values,
    validate_current_map_ui_auth_runtime,
    validate_map_postgres_runtime_secret_isolation,
    validate_pinvi_postgres_runtime_secret_isolation,
    validate_resolved_c6c_build_provenance,
    validate_resolved_compose_candidate_protected_values,
    validate_runtime_secret_isolation,
)
from kor_travel_docker_manager.services.c6c_image_retention import (
    ensure_generation_references,
    reconcile_candidate_build_references,
    reconcile_generation_references,
)
from kor_travel_docker_manager.services.capabilities import (
    _MANAGED_COMPOSE_MUTATION_CAPABILITY,
    _PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
)
from kor_travel_docker_manager.services.database_runtime import (
    DatabaseRole,
    DatabaseRuntime,
    create_database_if_absent,
    database_runtimes_from_frozen_contract,
    ensure_map_application_database,
    initialize_application_300_dagster_metadata_database,
    read_database_identity,
    read_database_schema_revision,
    require_map_application_database_convergible,
    reset_databases_for_application_300,
    schema_revision_table_exists,
)
from kor_travel_docker_manager.services.deploy_status import (
    RESET_DONE_STEP,
    DeployedDatabase,
    DeployRestart,
    DeployStatus,
    begin_deploy,
    commit_deploy,
    deploy_status_path,
    read_deploy_status,
    write_deploy_status,
)
from kor_travel_docker_manager.services.errors import (
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.map_application_candidate import (
    MapApplicationCandidate,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    RUNTIME_SERVICES,
    PinnedRuntimeGeneration,
    PinnedRuntimeManifest,
    PinnedRuntimeStatePaths,
    RuntimeService,
    ensure_pinned_runtime_state_directory,
    generation_logical_sha256,
    pinned_runtime_state_paths,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    write_manifest as write_pinned_runtime_manifest,
)
from kor_travel_docker_manager.services.pinned_runtime_rebuild import (
    COMPOSE_BUILT_RUNTIME_SERVICES,
    CandidateRuntimeBuild,
    MapApplication300ArtifactDirectories,
    build_candidate_generation,
    generation_companion_services,
    generation_compose_environment,
    map_application_300_paired_build_image_names,
    parse_candidate_static_head,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    PinnedRuntimeRelease,
    current_pinned_runtime_release,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    PinnedRuntimeSourceMaterialization,
    materialize_pinned_runtime_sources,
)
from kor_travel_docker_manager.services.pinvi_bootstrap_credential import (
    pinvi_bootstrap_credential_file,
    reconcile_orphaned_pinvi_bootstrap_credentials,
)
from kor_travel_docker_manager.services.registry import (
    MANAGED_CONTAINERS,
    ExternalProject,
    container_id_to_compose_service,
    external_project_for_container,
    external_project_for_target,
    get_project_root,
    init_steps_for_target,
    is_known_target,
    runtime_services_for_target,
    service_groups_for_target,
    services_for_target,
    target_is_external,
    target_sequence_for_target,
)
from kor_travel_docker_manager.services.trusted_install import (
    require_pinned_runtime_rebuild_root,
    trusted_pinned_runtime_project_root,
)
from kor_travel_docker_manager.services.yaml_strict import (
    load_yaml_rejecting_duplicate_keys,
)

_PINNED_RUNTIME_ONESHOT_WRITERS = (
    "pinvi-db-init",
    "kor-travel-map-dagster-db-init",
    "kor-travel-map-db-role-bootstrap",
    _MAP_APPLICATION_SCHEMA_SERVICE,
    "kor-travel-map-dagster-storage-migrate",
    "pinvi-admin-bootstrap",
)


def _with_generation_companions(
    services: Sequence[str],
    companions: Mapping[str, RuntimeService],
) -> tuple[str, ...]:
    return (
        *(name for name, owner in companions.items() if owner in services),
        *services,
    )


_PINNED_RUNTIME_EXTERNAL_PREREQUISITES = (
    "rustfs",
    "kor-travel-geo-api",
    "kor-travel-concierge-api",
)
_PINNED_RUNTIME_PREJOURNAL_FAILURE_STAGES = frozenset(
    {
        "environment_admission",
        "state_initialization",
        "prebuild_snapshot",
        "external_prerequisites",
        "source_materialization",
        "application_base_images",
        "application_builder",
        "application_candidate",
        "candidate_snapshot",
        "candidate_contract",
        # journal 직전 runtime transaction 구간. 종전에는 봉인 밖이라 여기서
        # 닫히면 result가 `unclassified`가 되고, `--json`은 원문을 내지 않아
        # **어디에도 진단이 남지 않았다**(2026-09-02 rebuild: 29분 실행,
        # stderr 0바이트). 이 트랙의 확립된 절차대로 원문을 노출하는 대신
        # 비밀 없는 고정 어휘를 넓혀 다음 실행이 지점을 특정하게 한다.
        # fresh candidate 빌드 구간. 이 흐름에서 가장 오래 걸리고 가장 잘
        # 실패하는 곳인데 봉인 밖이었다 — 2026-09-02 rebuild가 `pinvi-web`
        # 빌드에서 exit 1로 닫히고 `unclassified`가 돼 회전 사이클 1회를 태웠다.
        "candidate_compose_build",
        "candidate_images",
        "candidate_bootstrap_settings",
        "candidate_heads",
        "runtime_generation",
        "runtime_transaction",
        "runtime_transaction_lock",
    }
)
# frozen transaction은 실행 전에 one-shot service까지 exact resolved document에 결박한다.
# profile을 해석 단계에서 빼면 `run --profile bootstrap`가 같은 문서에서 service를 찾지 못한다.
_FROZEN_COMPOSE_PROFILES = ("bootstrap",)


class PinnedRuntimePrejournalFailure(DeploymentContractError):
    """journal 전 후보 준비 실패를 비밀 없는 고정 단계로 전달한다."""

    def __init__(self, stage: str, service: str | None = None) -> None:
        if stage not in _PINNED_RUNTIME_PREJOURNAL_FAILURE_STAGES:
            raise ValueError("pinned runtime pre-journal failure stage is invalid")
        # 후보 Compose 빌드는 서비스가 넷이고 각각 다른 이유로 죽는다.
        # stage 하나로 접으면 다음 실행이 여전히 어느 서비스인지 모른 채
        # 30분을 다시 쓴다. 값은 `COMPOSE_BUILT_RUNTIME_SERVICES` 안에서만
        # 나오므로 비밀이 없다 — 자유 문자열을 여는 것이 아니다.
        if service is not None and service not in COMPOSE_BUILT_RUNTIME_SERVICES:
            raise ValueError("pinned runtime compose build service is invalid")
        self.stage = stage
        self.service = service
        super().__init__("pinned runtime candidate preparation failed")


_PINNED_RUNTIME_PREJOURNAL_MARK = "_ktdm_pinned_runtime_failed_before_journal"


class _PinnedRuntimeJournalWatermark:
    """이 실행이 배포 상태를 ``in_progress``로 바꿨는지 기록한다(ADR-51).

    launcher(`run-pinned-rebuild-once`)는 이 판정 하나로 claim을 해제할지 정한다.
    ``in_progress``를 쓰기 전의 실패는 **데이터를** 바꾸지 않았으므로 해제한다(DB 서버
    기동·이미지 태그는 그 전에 일어날 수 있지만 모두 멱등이다). 쓴 뒤의
    실패도 이제 재시도할 수 있지만 launcher의 attempt 원장은 감사 흔적으로 남긴다.
    """

    def __init__(self) -> None:
        self._reached = False

    def mark_reached(self) -> None:
        self._reached = True

    def reached(self) -> bool:
        return self._reached


def _mark_pinned_runtime_prejournal(exc: DeploymentContractError) -> None:
    """예외를 **바꾸지 않고** "이 실행은 journal을 쓰지 않았다"만 붙인다.

    타입·메시지·traceback이 그대로라 상위 `except` 절이 하나도 달라지지 않고,
    비-JSON 경로의 원문 출력도 그대로다. 달라지는 것은 JSON classification 하나다.
    """

    setattr(exc, _PINNED_RUNTIME_PREJOURNAL_MARK, True)


def pinned_runtime_failed_before_journal(exc: BaseException) -> bool:
    """봉인하지 않은 실패가 journal 전이었는지 읽는다."""

    return getattr(exc, _PINNED_RUNTIME_PREJOURNAL_MARK, False) is True


_PINNED_RUNTIME_JOURNAL_REACHED_MARK = "_ktdm_pinned_runtime_journal_reached"


def _mark_pinned_runtime_journal_reached(exc: DeploymentContractError) -> None:
    """봉인된 실패에도 관측 결과를 싣는다.

    **봉인은 메시지 정책이고 watermark는 소각 정책이다 — 다른 질문이다.**
    종전에는 봉인된 실패가 무조건 `prejournal_failure`였다. 그런데 봉인 단계는
    전부 resume 분기보다 **앞**에서 돌기 때문에, journal이 이미 존재하는
    resume 실행에서 봉인 단계가 실패하면 — 예: rustfs가 불건강해
    `external_prerequisites`가 거절 — **이미 DB를 리셋하고 compose를 적용한**
    후보의 claim이 해제됐다(적대 리뷰 M-2).
    """

    setattr(exc, _PINNED_RUNTIME_JOURNAL_REACHED_MARK, True)


def pinned_runtime_journal_was_reached(exc: BaseException) -> bool:
    """봉인된 실패 시점에 journal이 이미 존재했는지 읽는다."""

    return getattr(exc, _PINNED_RUNTIME_JOURNAL_REACHED_MARK, False) is True


_PINNED_RUNTIME_COMPOSE_SERVICE_MARK = "_ktdm_pinned_runtime_compose_service"


@contextmanager
def _pinned_runtime_prejournal_step(stage: str) -> Iterator[None]:
    """journal 전 ``DeploymentContractError``를 safe stage로 봉인한다."""

    try:
        yield
    except PinnedRuntimePrejournalFailure:
        raise
    except DeploymentContractError as exc:
        service = getattr(exc, _PINNED_RUNTIME_COMPOSE_SERVICE_MARK, None)
        if service not in COMPOSE_BUILT_RUNTIME_SERVICES:
            service = None
        raise PinnedRuntimePrejournalFailure(stage, service) from exc


_MAP_APPLICATION_300_RECEIPT_DIRECTORY = "map-application-300-candidate"
_MAP_APPLICATION_300_ARTIFACT_DIRECTORY = "map-application-300-artifacts"
_MAP_APPLICATION_300_POSTGRES_REFERENCE = "postgis/postgis:16-3.5-alpine"
_ROLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


_MAP_DAGSTER_STORAGE_MIGRATION_ERROR_SCHEMA = (
    "kor-travel-map.dagster-storage-migration-error.v1"
)
# 닫힌 코드 목록이 아니라 모양만 본다(ADR-51 G). 목록이면 Map이 코드를 더하거나 빼는
# 리비전마다 Manager가 새 원인을 조용히 삼킨다. 모양 검사는 값이 섞여 드는 것만 막는다.
_MAP_DAGSTER_STORAGE_MIGRATION_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PINVI_ADMIN_BOOTSTRAP_ERROR_PHASE_BY_CODE = {
    "alembic_config_missing": "migration",
    "credential_file_changed": "credential_file",
    "credential_file_env_missing": "credential_file",
    "credential_file_json_invalid": "credential_file",
    "credential_file_link_count_invalid": "credential_file",
    "credential_file_missing": "credential_file",
    "credential_file_mode_invalid": "credential_file",
    "credential_file_not_regular": "credential_file",
    "credential_file_owner_mismatch": "credential_file",
    "credential_file_path_invalid": "credential_file",
    "credential_file_size_invalid": "credential_file",
    "credential_file_unavailable": "credential_file",
    "internal_error": "runtime",
    "invalid_arguments": "startup",
    "migration_failed": "migration",
    "schema_revision_mismatch": "schema_check",
    "schema_version_invalid": "schema_check",
    "schema_version_unavailable": "schema_check",
    "static_head_unavailable": "migration",
}


@dataclass(frozen=True)
class _ComposeFailureDiagnostic:
    """pinned runtime rebuild 실패 진단을 사람이 읽는 문구와 기계 판독 코드로 나눈다.

    ``message_suffix``는 로그·CLI에 그대로 보이는 문구다(``"; pinvi_role:code"``
    형태, 하위호환 유지). ``pinvi_role_code``는 그 문구를 나중에 다시 파싱하지 않고
    바로 쓰는 구조화된 값이다 — 문구 조립 형식(괄호 위치 등)이 바뀌어도 lifecycle
    분류가 조용히 깨지지 않게 한다.
    """

    message_suffix: str
    pinvi_role_code: str | None = None


class PinnedRuntimeComposeFailure(DeploymentContractError):
    """pinned runtime rebuild Compose 실행 실패. 진단 코드를 속성으로 전달한다.

    ``_pinvi_lifecycle_diagnostic``는 이 속성을 우선 쓰고, 이 타입이 아니거나 속성이
    없는 예외에 대해서만 메시지 재파싱으로 폴백한다 — 기존 경로를 깨지 않는 additive
    변경이다.
    """

    def __init__(self, message: str, *, pinvi_role_diagnostic: str | None = None) -> None:
        super().__init__(message)
        self.pinvi_role_diagnostic = pinvi_role_diagnostic


# fresh Dagster DB의 PostgreSQL readiness window를 덮되 총 retry 대기는 58초를 넘지 않는다.


def _json_object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """typed one-shot error envelope의 중복 JSON key를 fail-close한다."""

    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate JSON object key")
        payload[key] = value
    return payload


def _compose_prefixed_typed_error_candidate(line: str, *, target: str) -> str | None:
    """정확한 Compose service attach prefix 뒤의 JSON 한 줄만 반환한다."""

    prefix, separator, candidate = line.partition(" | ")
    if not separator:
        return None
    normalized_prefix = prefix.strip()
    if normalized_prefix == target:
        return candidate
    replica_prefix = f"{target}-"
    if not normalized_prefix.startswith(replica_prefix):
        return None
    replica_suffix = normalized_prefix.removeprefix(replica_prefix)
    if replica_suffix and replica_suffix.isdecimal():
        return candidate
    return None


def _require_pinned_runtime_rebuild_root() -> None:
    """source staging·state owner와 Docker mutation authority를 root로 고정한다.

    GM-09: 정본은 services/trusted_install.py다.
    """

    require_pinned_runtime_rebuild_root()


def _pinned_runtime_admission_warnings(pinset_sha256: str) -> list[str]:
    """배포 전 원장 판정. 막힌 pinset·낡은 실행 결박은 이제 **경고**다(ADR-51 B).

    종전에는 둘 다 영구 거부였고, Manager를 설치할 때마다 실행 결박이 낡아 새 Map 커밋 없이는
    같은 pair를 다시 돌릴 수 없었다. 마이그레이션 전진에서는 모든 배포가 멱등이라 다시
    돌려도 데이터를 잃지 않는다 — 운영자가 알고 재배포할 수 있게 경고로 남긴다. 대기 중인
    회전 intent는 여전히 거부한다(원장이 두 쌍 사이에 있다).
    """

    from kor_travel_docker_manager.services.runtime_execution_registry import (
        load_runtime_execution_registry,
        trusted_manager_source_revision,
    )
    from kor_travel_docker_manager.services.runtime_pair_rotation import (
        require_no_pending_runtime_pair_rotation,
    )
    from kor_travel_docker_manager.services.runtime_pin_registry import (
        load_runtime_pin_registry,
    )

    require_no_pending_runtime_pair_rotation()
    registry = load_runtime_pin_registry()
    warnings: list[str] = []
    if registry.is_unconditionally_blocked_pinset(pinset_sha256):
        warnings.append("this pinset was previously judged terminal; deploying it again")
    try:
        execution = load_runtime_execution_registry()
        runnable = execution.current_matches(
            pins=registry, manager_source_revision=trusted_manager_source_revision()
        ) and not execution.is_unconditionally_blocked_current()
    except DeploymentContractError:
        runnable = False
    if not runnable:
        warnings.append(
            "the trusted execution binding is missing, stale, or terminal; "
            "recorded for audit only"
        )
    return warnings


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _local_image_present(image: str) -> bool:
    """이 태그가 로컬 image store에 있는가(pinset에 묶인 후보 태그의 재사용 판단)."""

    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            cwd="/",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_PINNED_RUNTIME_STATIC_INSPECTION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError("candidate image store cannot be inspected") from exc
    return completed.returncode == 0


def get_compose_path() -> str:
    return os.environ.get(
        "KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE",
        os.path.join(get_project_root(), "docker-compose.yml"),
    )


def get_env_path() -> str:
    return os.environ.get(
        "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE",
        os.path.join(get_project_root(), ".env"),
    )


def get_override_path() -> str:
    """legacy read-only 명령이 인식하는 override 경로.

    Manager mutation은 raw/resolved volume graph를 하나의 파일에 고정하므로 실제
    override가 존재하거나 명시되면 candidate 검증에서 거부한다.
    """
    override = os.environ.get("KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE")
    if override:
        return override
    return os.path.join(
        os.path.dirname(get_compose_path()), "docker-compose.override.yml"
    )


def _create_frozen_compose_descriptor(label: str) -> int:
    """child process에만 `/proc/self/fd`로 보이는 unlinked Compose descriptor를 연다."""

    try:
        return os.memfd_create(label, flags=os.MFD_CLOEXEC)
    except AttributeError:
        pass
    descriptor, temporary_path = tempfile.mkstemp(prefix=f"{label}-")
    try:
        os.unlink(temporary_path)
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def _resolve_repository_path(
    configured_path: str,
    *,
    compose_directory: Path,
    label: str,
) -> Path:
    path = Path(configured_path)
    if not path.is_absolute():
        path = compose_directory / path
    try:
        repository = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DeploymentContractError(
            f"{label} build context cannot be resolved"
        ) from exc
    if not repository.is_dir():
        raise DeploymentContractError(f"{label} build context is not a directory")
    return repository


def _run_git_read(
    repository: Path,
    args: Sequence[str],
    *,
    label: str,
    allow_output_whitespace: bool = False,
) -> str:
    try:
        completed = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repository), *args],
            cwd=get_project_root(),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise DeploymentContractError(
            f"cannot inspect {label} build context Git state"
        ) from exc
    if completed.returncode != 0:
        raise DeploymentContractError(
            f"cannot inspect {label} build context Git state"
        )
    if allow_output_whitespace:
        return completed.stdout.rstrip("\r\n")
    return completed.stdout.strip()


def _run_git_bytes(
    repository: Path,
    args: Sequence[str],
    *,
    label: str,
) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repository), *args],
            cwd=get_project_root(),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise DeploymentContractError(
            f"cannot inspect {label} build context Git state"
        ) from exc
    if completed.returncode != 0:
        raise DeploymentContractError(
            f"cannot inspect {label} build context Git state"
        )
    return completed.stdout


_MAP_SOURCE_V3_API_ENVIRONMENT = {
    "KOR_TRAVEL_MAP_API_PROFILE": "${KOR_TRAVEL_MAP_API_PROFILE:-production}",
    "KOR_TRAVEL_MAP_API_DEBUG_ROUTES_ENABLED": (
        "${KOR_TRAVEL_MAP_API_DEBUG_ROUTES_ENABLED:-false}"
    ),
    "KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED": (
        "${KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED:-true}"
    ),
    "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET": (
        "${KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET:?KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET is required}"
    ),
    "KOR_TRAVEL_MAP_API_SERVICE_TOKEN": (
        "${KOR_TRAVEL_MAP_API_SERVICE_TOKEN:?KOR_TRAVEL_MAP_API_SERVICE_TOKEN is required}"
    ),
    "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256": (
        "${KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256:?"
        "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256 is required}"
    ),
    "KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED": (
        "${KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED:-false}"
    ),
}
_MAP_SOURCE_V3_UI_ENVIRONMENT = {
    "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET": (
        "${KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET:?KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET is required}"
    ),
    "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": ("${KOR_TRAVEL_MAP_UI_ADMIN_USERNAME:-admin}"),
    "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": (
        "${KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH:?"
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH is required}"
    ),
    "KOR_TRAVEL_MAP_UI_SESSION_SECRET": (
        "${KOR_TRAVEL_MAP_UI_SESSION_SECRET:?KOR_TRAVEL_MAP_UI_SESSION_SECRET is required}"
    ),
    "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN": (
        "${KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN:?"
        "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN is required}"
    ),
}
_MAP_SOURCE_V4_CURSOR_ENV_VALUE = (
    "${KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET:?"
    "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET is required}"
)
_MAP_SOURCE_DAGSTER_PROFILE_FALLBACK_VALUE = (
    "${KOR_TRAVEL_MAP_DAGSTER_PROFILE:-${KOR_TRAVEL_MAP_API_PROFILE:-production}}"
)
_MAP_SOURCE_DAGSTER_PROFILE_ENV_NAME = "KOR_TRAVEL_MAP_DAGSTER_PROFILE"
_MAP_SOURCE_PROTECTED_ENV_VALUES = {
    "KOR_TRAVEL_MAP_API_PROFILE": (_MAP_SOURCE_V3_API_ENVIRONMENT["KOR_TRAVEL_MAP_API_PROFILE"]),
    "KOR_TRAVEL_MAP_API_DEBUG_ROUTES_ENABLED": (
        _MAP_SOURCE_V3_API_ENVIRONMENT["KOR_TRAVEL_MAP_API_DEBUG_ROUTES_ENABLED"]
    ),
    "KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED": (
        _MAP_SOURCE_V3_API_ENVIRONMENT["KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED"]
    ),
    "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET": (
        "${KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET:?KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET is required}"
    ),
    "KOR_TRAVEL_MAP_API_SERVICE_TOKEN": (
        "${KOR_TRAVEL_MAP_API_SERVICE_TOKEN:?KOR_TRAVEL_MAP_API_SERVICE_TOKEN is required}"
    ),
    "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256": (
        "${KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256:?"
        "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256 is required}"
    ),
    "KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED": (
        "${KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED:-false}"
    ),
    "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN": (
        "${KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN:?"
        "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN is required}"
    ),
    "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET": (_MAP_SOURCE_V4_CURSOR_ENV_VALUE),
}
_MAP_SOURCE_ENV_FILE_CONTRACT = {
    "api": [
        {
            "path": "packages/kor-travel-map-api/.env",
            "required": True,
            "format": "raw",
        }
    ],
}
_MAP_SOURCE_TRACKED_ENV_FILE_MAX_BYTES = 64 * 1024


# GM-11: 중복 키 거부 YAML 로더의 정본은 services/yaml_strict.py다 — registry.py도
# 같은 로더를 쓰지만, 여기서 그 모듈을 두면 registry.py→compose_service.py
# import와 맞물려 순환이 된다.
def _load_unique_map_source_yaml(source: str) -> Any:
    return load_yaml_rejecting_duplicate_keys(source)


def _walk_map_source_scalars(
    value: Any,
    path: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            yield (*path, key_text, "<key>"), key_text
            yield from _walk_map_source_scalars(item, (*path, key_text))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_map_source_scalars(item, (*path, str(index)))
        return
    yield path, value


def _validate_map_source_protected_scalar_tree(
    payload: Mapping[str, Any],
    *,
    contract_version: int,
) -> None:
    allowed_values: dict[tuple[str, ...], str] = {
        (
            "services",
            "api",
            "environment",
            "KOR_TRAVEL_MAP_API_PROFILE",
        ): _MAP_SOURCE_PROTECTED_ENV_VALUES[
            "KOR_TRAVEL_MAP_API_PROFILE"
        ],
        (
            "services",
            "api",
            "environment",
            "KOR_TRAVEL_MAP_API_DEBUG_ROUTES_ENABLED",
        ): _MAP_SOURCE_PROTECTED_ENV_VALUES[
            "KOR_TRAVEL_MAP_API_DEBUG_ROUTES_ENABLED"
        ],
        (
            "services",
            "api",
            "environment",
            "KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED",
        ): _MAP_SOURCE_PROTECTED_ENV_VALUES[
            "KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED"
        ],
        (
            "services",
            "api",
            "environment",
            "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET",
        ): _MAP_SOURCE_PROTECTED_ENV_VALUES[
            "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET"
        ],
        (
            "services",
            "frontend",
            "environment",
            "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET",
        ): _MAP_SOURCE_PROTECTED_ENV_VALUES[
            "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET"
        ],
        (
            "services",
            "api",
            "environment",
            "KOR_TRAVEL_MAP_API_SERVICE_TOKEN",
        ): _MAP_SOURCE_PROTECTED_ENV_VALUES[
            "KOR_TRAVEL_MAP_API_SERVICE_TOKEN"
        ],
        (
            "services",
            "api",
            "environment",
            "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256",
        ): _MAP_SOURCE_PROTECTED_ENV_VALUES[
            "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256"
        ],
        (
            "services",
            "api",
            "environment",
            "KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED",
        ): _MAP_SOURCE_PROTECTED_ENV_VALUES[
            "KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED"
        ],
        (
            "services",
            "dagster-db-init",
            "environment",
            "KOR_TRAVEL_MAP_DAGSTER_PROFILE",
        ): _MAP_SOURCE_DAGSTER_PROFILE_FALLBACK_VALUE,
        (
            "services",
            "dagster-db-init-fresh-300",
            "environment",
            "KOR_TRAVEL_MAP_DAGSTER_PROFILE",
        ): "local-dev",
        (
            "services",
            "dagster",
            "environment",
            "KOR_TRAVEL_MAP_DAGSTER_PROFILE",
        ): _MAP_SOURCE_DAGSTER_PROFILE_FALLBACK_VALUE,
        (
            "services",
            "dagster-code-server",
            "environment",
            "KOR_TRAVEL_MAP_DAGSTER_PROFILE",
        ): _MAP_SOURCE_DAGSTER_PROFILE_FALLBACK_VALUE,
        (
            "services",
            "dagster-daemon",
            "environment",
            "KOR_TRAVEL_MAP_DAGSTER_PROFILE",
        ): _MAP_SOURCE_DAGSTER_PROFILE_FALLBACK_VALUE,
        (
            "services",
            "dagster-storage-migrate",
            "environment",
            "KOR_TRAVEL_MAP_DAGSTER_PROFILE",
        ): _MAP_SOURCE_DAGSTER_PROFILE_FALLBACK_VALUE,
        (
            "services",
            "frontend",
            "environment",
            "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN",
        ): _MAP_SOURCE_PROTECTED_ENV_VALUES[
            "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN"
        ],
    }
    if contract_version == 4:
        allowed_values[
            (
                "services",
                "api",
                "environment",
                "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET",
            )
        ] = _MAP_SOURCE_PROTECTED_ENV_VALUES[
            "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET"
        ]

    seen_key_paths: set[tuple[str, ...]] = set()
    seen_value_paths: set[tuple[str, ...]] = set()
    protected_names = tuple(_MAP_SOURCE_PROTECTED_ENV_VALUES)
    for path, scalar in _walk_map_source_scalars(payload):
        text = "" if scalar is None else str(scalar)
        matching_names = tuple(name for name in protected_names if name in text)
        if path[-1:] == ("<key>",):
            value_path = path[:-1]
            if value_path in allowed_values:
                if text != value_path[-1]:
                    raise DeploymentContractError(
                        "Map source environment contract has a protected name outside its exact path"
                    )
                seen_key_paths.add(value_path)
                continue
            if text == _MAP_SOURCE_DAGSTER_PROFILE_ENV_NAME:
                raise DeploymentContractError(
                    "Map source environment contract has a protected name outside its exact path"
                )
            if not matching_names:
                continue
            if (
                value_path not in allowed_values
                or text != value_path[-1]
                or matching_names != (value_path[-1],)
            ):
                raise DeploymentContractError(
                    "Map source environment contract has a protected name outside its exact path"
                )
            seen_key_paths.add(value_path)
            continue
        expected_value = allowed_values.get(path)
        if expected_value is not None:
            if text != expected_value:
                raise DeploymentContractError(
                    "Map source environment contract has a protected placeholder outside its exact path"
                )
            seen_value_paths.add(path)
            continue
        if f"${{{_MAP_SOURCE_DAGSTER_PROFILE_ENV_NAME}" in text:
            matching_names = (*matching_names, _MAP_SOURCE_DAGSTER_PROFILE_ENV_NAME)
        if not matching_names:
            continue
        if expected_value is None or text != expected_value:
            raise DeploymentContractError(
                "Map source environment contract has a protected placeholder outside its exact path"
            )
        seen_value_paths.add(path)

    required_paths = set(allowed_values)
    if seen_key_paths != required_paths or seen_value_paths != required_paths:
        raise DeploymentContractError(
            "Map source environment contract protected wiring count is invalid"
        )


def _validate_map_source_env_files(
    repository: Path,
    source_revision: str,
    payload: Mapping[str, Any],
) -> None:
    """source compose env_file의 경로·옵션과 tracked 내용을 고정한다."""

    services = payload.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError(
            "Map source environment contract manifest has no services"
        )
    for service_name, service in services.items():
        if not isinstance(service, Mapping):
            raise DeploymentContractError(
                "Map source environment contract service shape is invalid"
            )
        expected = _MAP_SOURCE_ENV_FILE_CONTRACT.get(str(service_name))
        if "env_file" in service and (
            expected is None or service.get("env_file") != expected
        ):
            raise DeploymentContractError(
                "Map source environment contract env_file shape is invalid"
            )
    for service_name, expected in _MAP_SOURCE_ENV_FILE_CONTRACT.items():
        service = services.get(service_name)
        if not isinstance(service, Mapping) or service.get("env_file") != expected:
            raise DeploymentContractError(
                "Map source environment contract env_file shape is invalid"
            )

    protected_names = tuple(_MAP_SOURCE_PROTECTED_ENV_VALUES)
    referenced_paths = {
        str(entry["path"])
        for entries in _MAP_SOURCE_ENV_FILE_CONTRACT.values()
        for entry in entries
    }
    for referenced_path in referenced_paths:
        tree = _run_git_bytes(
            repository,
            [
                "ls-tree",
                "-z",
                source_revision,
                "--",
                referenced_path,
            ],
            label="Map",
        )
        if not tree:
            continue
        records = tree.split(b"\0")
        if len(records) != 2 or records[-1] != b"":
            raise DeploymentContractError(
                "Map source environment contract env_file tree lookup is invalid"
            )
        metadata, separator, path_bytes = records[0].partition(b"\t")
        fields = metadata.split(b" ")
        if (
            separator != b"\t"
            or len(fields) != 3
            or fields[0] != b"100644"
            or fields[1] != b"blob"
            or re.fullmatch(rb"[0-9a-f]{40}", fields[2]) is None
            or path_bytes != referenced_path.encode("utf-8")
        ):
            raise DeploymentContractError(
                "Map source environment contract tracked env_file is not a regular 100644 blob"
            )
        object_id = fields[2].decode("ascii")
        raw_size = _run_git_read(
            repository,
            ["cat-file", "-s", object_id],
            label="Map",
        )
        if re.fullmatch(r"[0-9]+", raw_size) is None:
            raise DeploymentContractError(
                "Map source environment contract tracked env_file size is invalid"
            )
        object_size = int(raw_size)
        if object_size > _MAP_SOURCE_TRACKED_ENV_FILE_MAX_BYTES:
            raise DeploymentContractError(
                "Map source environment contract tracked env_file exceeds 64 KiB"
            )
        raw_content = _run_git_bytes(
            repository,
            ["cat-file", "blob", object_id],
            label="Map",
        )
        if len(raw_content) != object_size:
            raise DeploymentContractError(
                "Map source environment contract tracked env_file size changed"
            )
        try:
            content = raw_content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DeploymentContractError(
                "Map source environment contract tracked env_file is not UTF-8"
            ) from exc
        if any(name in content for name in protected_names):
            raise DeploymentContractError(
                "Map source environment contract tracked env_file contains protected wiring"
            )


def _map_source_environment_contract_version(
    environment: Mapping[str, str],
    *,
    compose_path: str,
    source_revision: str,
) -> int:
    """active image exact source manifest의 production env 계약 세대를 판정한다."""

    if re.fullmatch(r"[0-9a-f]{40}", source_revision) is None:
        raise DeploymentContractError(
            "Map source environment contract requires an exact source revision"
        )
    repository = _resolve_repository_path(
        environment.get("KOR_TRAVEL_MAP_REPO_DIR", "../kor-travel-map"),
        compose_directory=Path(compose_path).resolve().parent,
        label="Map",
    )
    source_manifest = _run_git_read(
        repository,
        ["show", f"{source_revision}:docker-compose.yml"],
        label="Map",
        allow_output_whitespace=True,
    )
    try:
        payload = _load_unique_map_source_yaml(source_manifest)
    except yaml.YAMLError as exc:
        raise DeploymentContractError(
            "Map source environment contract manifest is invalid"
        ) from exc
    services = payload.get("services") if isinstance(payload, Mapping) else None
    api = services.get("api") if isinstance(services, Mapping) else None
    ui = services.get("frontend") if isinstance(services, Mapping) else None
    api_environment = api.get("environment") if isinstance(api, Mapping) else None
    ui_environment = ui.get("environment") if isinstance(ui, Mapping) else None
    if not isinstance(api_environment, Mapping) or not isinstance(
        ui_environment, Mapping
    ):
        raise DeploymentContractError(
            "Map source environment contract manifest has no canonical services"
        )
    if any(
        api_environment.get(name) != expected
        for name, expected in _MAP_SOURCE_V3_API_ENVIRONMENT.items()
    ) or any(
        ui_environment.get(name) != expected
        for name, expected in _MAP_SOURCE_V3_UI_ENVIRONMENT.items()
    ):
        raise DeploymentContractError(
            "Map source environment contract is outside the supported v3/v4 range"
        )
    cursor_value = api_environment.get(
        "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET"
    )
    if cursor_value is None:
        contract_version = 3
    elif cursor_value == _MAP_SOURCE_V4_CURSOR_ENV_VALUE:
        contract_version = 4
    else:
        raise DeploymentContractError(
            "Map source environment contract has an unsupported cursor secret wiring"
        )
    _validate_map_source_protected_scalar_tree(
        payload,
        contract_version=contract_version,
    )
    _validate_map_source_env_files(
        repository,
        source_revision,
        payload,
    )
    return contract_version


@dataclass(frozen=True)
class ComposeEnvFileIdentity:
    exists: bool
    device: int | None = None
    inode: int | None = None
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None


@dataclass(frozen=True, repr=False)
class C6cDeploymentLockSnapshot:
    lock_path: str
    env_path: Path
    env_file_identity: ComposeEnvFileIdentity
    env_file_sha256: str


def _capture_c6c_deployment_lock_snapshot() -> C6cDeploymentLockSnapshot:
    env_path = Path(get_env_path()).resolve(strict=False)
    before = _env_file_identity(env_path)
    raw = b""
    values: dict[str, str] = {}
    if before.exists:
        try:
            raw = env_path.read_bytes()
            decoded = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ComposeCandidateContractError(
                "compose env-file lock path snapshot cannot be read"
            ) from exc
        after = _env_file_identity(env_path)
        if after != before:
            raise ComposeCandidateContractError(
                "compose env-file identity changed during lock path capture"
            )
        try:
            values.update(
                {
                    key: value or ""
                    for key, value in dotenv_values(stream=StringIO(decoded)).items()
                    if isinstance(key, str)
                }
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise ComposeCandidateContractError(
                "compose env-file lock path snapshot cannot be parsed"
            ) from exc
    elif _env_file_identity(env_path).exists:
        raise ComposeCandidateContractError(
            "compose env-file appeared during lock path capture"
        )
    lock_path = _c6c_lock_path_from_values(values)
    return C6cDeploymentLockSnapshot(
        lock_path=lock_path,
        env_path=env_path,
        env_file_identity=before,
        env_file_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _c6c_lock_path_from_values(env_values: Mapping[str, str]) -> str:
    if env_values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower() == "production":
        return c6c_state_paths(env_values)[1]

    effective: dict[str, str] = dict(env_values)
    for name in (
        "KTDM_DEPLOYMENT_ENVIRONMENT",
        "COMPOSE_PROJECT_NAME",
        "KTDM_C6C_STATE_ROOT",
        "KTDM_C6C_COMPATIBLE_PAIR_MANIFEST",
        "KTDM_C6C_DEPLOYMENT_LOCK",
    ):
        if name not in effective and name in os.environ:
            effective[name] = os.environ[name]
    if effective:
        return c6c_state_paths(effective)[1]
    return c6c_global_mutation_lock_path({})


def _revalidate_c6c_deployment_lock_snapshot(
    snapshot: C6cDeploymentLockSnapshot,
) -> None:
    current = _env_file_identity(snapshot.env_path)
    if current != snapshot.env_file_identity:
        raise ComposeCandidateContractError(
            "compose env-file identity changed before deployment lock acquisition"
        )
    if not current.exists:
        current_sha256 = hashlib.sha256(b"").hexdigest()
    else:
        try:
            current_sha256 = hashlib.sha256(snapshot.env_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ComposeCandidateContractError(
                "compose env-file lock path snapshot cannot be revalidated"
            ) from exc
    if current_sha256 != snapshot.env_file_sha256:
        raise ComposeCandidateContractError(
            "compose env-file changed before deployment lock acquisition"
        )


@contextmanager
def c6c_deployment_lock_from_environment() -> Iterator[C6cDeploymentLockSnapshot]:
    snapshot = _capture_c6c_deployment_lock_snapshot()
    with c6c_deployment_lock(snapshot.lock_path):
        _revalidate_c6c_deployment_lock_snapshot(snapshot)
        yield snapshot


@contextmanager
def _pinned_runtime_rebuild_environment_lock(
    *,
    prewrite_admission: Callable[["ComposeEnvironmentSnapshot"], str | None],
) -> Iterator[
    tuple[C6cDeploymentLockSnapshot, "ComposeEnvironmentSnapshot", bool]
]:
    """fresh PinVi role credential을 포함한 rebuild 전용 lock/snapshot 순서.

    먼저 root-owned pinned lease로 legacy stage/retire와 직렬화한다. trusted `/opt`
    root `.env`만 process ambient 없이 frozen snapshot으로 읽고, non-mutating
    admission을 통과해야 fresh role credential을 초기화할 수 있다.
    """

    with pinned_runtime_rebuild_lock():
        with _pinned_runtime_prejournal_step("environment_admission"):
            initial_environment_snapshot = (
                _capture_pinned_runtime_rebuild_environment_snapshot()
            )
        # 배포 lifecycle 게이트는 **봉인 밖**이다. 이 거부는 호스트 상태에서 유도한
        # 진단이 아니라 고정 정책 문장("rehearsal/rebuildable이 아니다")이라 비밀이
        # 없고, 운영자가 알아야 하는 유일한 정보가 그 문장 자체다. 이것까지
        # "candidate preparation failed"로 봉인하면 왜 거부됐는지 알 방법이 사라진다.
        #
        # 다만 **봉인 여부와 소각 여부는 다른 질문**이다. 이 거부는 어떤 write보다
        # 먼저 일어나 후보를 소비하지 않으며, 그 사실은 여기서 선언하지 않는다 —
        # `rebuild_pinned_runtime`의 journal watermark가 관측으로 답한다.
        assert_pinned_runtime_rebuild_allowed(
            environment=initial_environment_snapshot.effective
        )
        with _pinned_runtime_prejournal_step("environment_admission"):
            validate_c6c_operation_tokens(
                initial_environment_snapshot.effective,
                require_nonempty=True,
            )
        # M05 폐기 전에는 여기서 Manager가 PinVi role 자격증명을 생성해 **루트
        # `.env`에 써 넣고** 그 위에서 두 번째 snapshot을 떴다. geo 패턴에서는
        # 자격증명이 하나뿐이고 그것은 운영자가 `.env`에 둔 `PINVI_APP_DB_PASSWORD`
        # 이므로, rebuild가 `.env`를 변형할 이유가 사라졌다 — snapshot도 하나다.
        prewrite_admission(initial_environment_snapshot)
        with _pinned_runtime_prejournal_step("environment_admission"):
            current_environment_snapshot = initial_environment_snapshot
            lock_snapshot = _c6c_deployment_lock_snapshot_from_environment(
                current_environment_snapshot
            )
        with c6c_deployment_lock(lock_snapshot.lock_path):
            _revalidate_c6c_deployment_lock_snapshot(lock_snapshot)
            yield (
                lock_snapshot,
                current_environment_snapshot,
                initial_environment_snapshot.env_file_bytes
                != current_environment_snapshot.env_file_bytes,
            )


def _capture_pinned_runtime_rebuild_environment_snapshot(
    *,
    environment_override: Mapping[str, str] | None = None,
) -> "ComposeEnvironmentSnapshot":
    """root rebuild의 canonical release root와 env/Compose pair를 ambient에서 분리한다."""

    root = trusted_pinned_runtime_project_root()
    _assert_pinned_runtime_rebuild_execution_paths(root)
    return _capture_compose_environment_snapshot(
        environment_override=environment_override,
        env_path=root / ".env",
        compose_path=root / "docker-compose.yml",
        override_path=root / "docker-compose.override.yml",
        include_process_environment=False,
        interpolate_env_file=False,
    )


def _assert_pinned_runtime_rebuild_execution_paths(root: Path) -> None:
    """root command가 caller의 Manager path override를 authority로 쓰지 못하게 막는다."""

    expected = {
        "KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT": root,
        "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE": root / ".env",
        "KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE": root / "docker-compose.yml",
    }
    for name, expected_path in expected.items():
        value = os.environ.get(name)
        if value is None or not value.strip():
            continue
        configured = Path(value)
        if (
            not configured.is_absolute()
            or configured.resolve(strict=False) != expected_path
        ):
            raise DeploymentContractError(
                "pinned runtime rebuild execution path is not trusted"
            )
    if os.environ.get("KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE", "").strip():
        raise DeploymentContractError(
            "pinned runtime rebuild does not permit an override-file path"
        )


def _c6c_deployment_lock_snapshot_from_environment(
    environment_snapshot: "ComposeEnvironmentSnapshot",
) -> C6cDeploymentLockSnapshot:
    return C6cDeploymentLockSnapshot(
        lock_path=c6c_state_paths(environment_snapshot.effective)[1],
        env_path=Path(environment_snapshot.env_path).resolve(strict=False),
        env_file_identity=environment_snapshot.env_file_identity,
        env_file_sha256=hashlib.sha256(environment_snapshot.env_file_bytes).hexdigest(),
    )


@contextmanager
def _c6c_deployment_lock_from_transaction(
    transaction: "ComposeTransactionSnapshot",
) -> Iterator[C6cDeploymentLockSnapshot]:
    snapshot = _c6c_deployment_lock_snapshot_from_environment(
        transaction.environment,
    )
    with c6c_deployment_lock(snapshot.lock_path):
        _assert_transaction_matches_c6c_lock(transaction, snapshot)
        yield snapshot


def assert_environment_snapshot_matches_c6c_lock(
    environment_snapshot: "ComposeEnvironmentSnapshot",
    lock_snapshot: C6cDeploymentLockSnapshot,
) -> None:
    transaction_env_path = Path(environment_snapshot.env_path).resolve(strict=False)
    if transaction_env_path != lock_snapshot.env_path:
        raise ComposeCandidateContractError(
            "compose transaction env-file path differs from deployment lock snapshot"
        )
    if environment_snapshot.env_file_identity != lock_snapshot.env_file_identity:
        raise ComposeCandidateContractError(
            "compose transaction env-file identity differs from deployment lock snapshot"
        )
    if hashlib.sha256(environment_snapshot.env_file_bytes).hexdigest() != (
        lock_snapshot.env_file_sha256
    ):
        raise ComposeCandidateContractError(
            "compose transaction env-file bytes differ from deployment lock snapshot"
        )
    if c6c_state_paths(environment_snapshot.effective)[1] != lock_snapshot.lock_path:
        raise ComposeCandidateContractError(
            "compose transaction deployment lock differs from env-file snapshot"
        )


def _assert_transaction_matches_c6c_lock(
    transaction: "ComposeTransactionSnapshot",
    lock_snapshot: C6cDeploymentLockSnapshot,
) -> None:
    assert_environment_snapshot_matches_c6c_lock(
        transaction.environment,
        lock_snapshot,
    )


@dataclass(frozen=True, eq=False, repr=False)
class ComposeEnvironmentSnapshot:
    effective: Mapping[str, str] = field(repr=False)
    env_path: str = field(repr=False)
    compose_path: str = field(repr=False)
    override_path: str = field(repr=False)
    env_file_identity: ComposeEnvFileIdentity
    env_file_bytes: bytes = field(repr=False)

    def __repr__(self) -> str:
        return "ComposeEnvironmentSnapshot(<redacted>)"


def _frozen_canonical_env_owner(
    environment: ComposeEnvironmentSnapshot,
) -> dict[str, int]:
    """root trusted mutation도 lock 안에서 고정한 env owner만 신뢰한다."""

    uid = environment.env_file_identity.uid
    gid = environment.env_file_identity.gid
    if uid is None or gid is None:
        raise DeploymentContractError(
            "canonical env frozen identity has no owner evidence"
        )
    return {"expected_owner_uid": uid, "expected_owner_gid": gid}


@dataclass(frozen=True, repr=False)
class ComposeExternalReference:
    service: str
    index: int
    raw_path: str = field(repr=False)
    resolved_path: str = field(repr=False)
    required: bool
    format: str


@dataclass(frozen=True, repr=False)
class ComposeExternalFileSnapshot:
    path: str = field(repr=False)
    identity: ComposeEnvFileIdentity
    contents: bytes = field(repr=False)


@dataclass(frozen=True, eq=False, repr=False)
class ComposeExternalInputSnapshot:
    references: tuple[ComposeExternalReference, ...] = field(repr=False)
    files: tuple[ComposeExternalFileSnapshot, ...] = field(repr=False)

    def __repr__(self) -> str:
        return "ComposeExternalInputSnapshot(<redacted>)"


@dataclass(frozen=True, eq=False, repr=False)
class ComposeTransactionSnapshot:
    environment: ComposeEnvironmentSnapshot = field(repr=False)
    external_inputs: ComposeExternalInputSnapshot = field(repr=False)
    compose_source_bytes: bytes = field(repr=False)
    compose_source_mode: int
    system_bind_snapshots: tuple[CandidateSystemBindSnapshot, ...]
    raw_volume_graph_hash: str
    resolved_volume_graph_hash: str
    resolved: Mapping[str, Any] = field(default_factory=dict, repr=False)
    resolved_document_hash: str = field(default="", repr=False)
    manifest_path: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return "ComposeTransactionSnapshot(<redacted>)"


class _ServiceReadinessPolicy(StrEnum):
    RUNNING = "running"
    HEALTHY = "healthy"


@dataclass(frozen=True)
class _ServiceReadinessContract:
    policy: _ServiceReadinessPolicy
    container_name: str | None


def _service_readiness_policy(
    service_name: str,
    service: Mapping[str, Any],
) -> _ServiceReadinessPolicy:
    if "healthcheck" not in service:
        return _ServiceReadinessPolicy.RUNNING
    healthcheck = service["healthcheck"]
    if not isinstance(healthcheck, Mapping):
        raise DeploymentContractError(
            f"canonical readiness healthcheck is invalid: {service_name}"
        )
    disabled = healthcheck.get("disable")
    if disabled is not None and not isinstance(disabled, bool):
        raise DeploymentContractError(
            f"canonical readiness healthcheck disable flag is invalid: {service_name}"
        )
    test = healthcheck.get("test")
    if disabled is True:
        if test not in (None, "NONE", ["NONE"]):
            raise DeploymentContractError(
                f"canonical readiness healthcheck is ambiguous: {service_name}"
            )
        return _ServiceReadinessPolicy.RUNNING
    if isinstance(test, str):
        normalized = test.strip()
        if not normalized:
            raise DeploymentContractError(
                f"canonical readiness healthcheck test is empty: {service_name}"
            )
        if normalized.upper() == "NONE":
            return _ServiceReadinessPolicy.RUNNING
        return _ServiceReadinessPolicy.HEALTHY
    if not isinstance(test, Sequence) or isinstance(test, (bytes, bytearray)):
        raise DeploymentContractError(
            f"canonical readiness healthcheck test is invalid: {service_name}"
        )
    commands = list(test)
    if not commands or any(not isinstance(item, str) for item in commands):
        raise DeploymentContractError(
            f"canonical readiness healthcheck test is invalid: {service_name}"
        )
    directive = commands[0].strip().upper()
    if directive == "NONE" and len(commands) == 1:
        return _ServiceReadinessPolicy.RUNNING
    if directive not in {"CMD", "CMD-SHELL"} or len(commands) < 2:
        raise DeploymentContractError(
            f"canonical readiness healthcheck test is unsupported: {service_name}"
        )
    return _ServiceReadinessPolicy.HEALTHY


def _service_singleton_container_name(
    service_name: str,
    service: Mapping[str, Any],
) -> str | None:
    if "scale" in service:
        scale = service["scale"]
        if type(scale) is not int or scale != 1:
            raise DeploymentContractError(
                f"canonical readiness service is not singleton: {service_name}"
            )
    if "deploy" in service:
        deploy = service["deploy"]
        if not isinstance(deploy, Mapping):
            raise DeploymentContractError(
                f"canonical readiness deploy contract is invalid: {service_name}"
            )
        mode = deploy.get("mode")
        if mode is not None and mode != "replicated":
            raise DeploymentContractError(
                f"canonical readiness deploy mode is not singleton: {service_name}"
            )
        if "replicas" in deploy:
            replicas = deploy["replicas"]
            if type(replicas) is not int or replicas != 1:
                raise DeploymentContractError(
                    f"canonical readiness replicas are not singleton: {service_name}"
                )
    if "container_name" not in service:
        return None
    container_name = service["container_name"]
    if not isinstance(container_name, str) or not container_name.strip():
        raise DeploymentContractError(
            f"canonical readiness container name is invalid: {service_name}"
        )
    return container_name


def _resolved_service_readiness_contracts(
    resolved: Mapping[str, Any],
    services: Sequence[str],
) -> dict[str, _ServiceReadinessContract]:
    resolved_services = resolved.get("services")
    if not isinstance(resolved_services, Mapping):
        raise DeploymentContractError(
            "canonical resolved compose has no readiness service mapping"
        )
    contracts: dict[str, _ServiceReadinessContract] = {}
    for service_name in services:
        service = resolved_services.get(service_name)
        if not isinstance(service, Mapping):
            raise DeploymentContractError(
                f"canonical resolved compose is missing readiness service: {service_name}"
            )
        contracts[service_name] = _ServiceReadinessContract(
            policy=_service_readiness_policy(service_name, service),
            container_name=_service_singleton_container_name(
                service_name,
                service,
            ),
        )
    return contracts


def _index_singleton_service_records(
    records: Sequence[Mapping[str, Any]],
    services: Sequence[str],
    contracts: Mapping[str, _ServiceReadinessContract],
    *,
    allow_missing: bool,
) -> dict[str, Mapping[str, Any]]:
    expected = set(services)
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        service_name = str(record["Service"])
        if service_name not in expected:
            raise DeploymentContractError(
                f"compose readiness returned unexpected service: {service_name}"
            )
        grouped.setdefault(service_name, []).append(record)
    duplicate = [
        service_name
        for service_name, service_records in grouped.items()
        if len(service_records) != 1
    ]
    if duplicate:
        raise DeploymentContractError(
            "compose readiness returned duplicate singleton services: "
            + ", ".join(duplicate)
        )
    missing = [service_name for service_name in services if service_name not in grouped]
    if missing and not allow_missing:
        raise DeploymentContractError(
            "mandatory services are not running: " + ", ".join(missing)
        )
    indexed = {
        service_name: service_records[0]
        for service_name, service_records in grouped.items()
    }
    for service_name, record in indexed.items():
        canonical_name = contracts[service_name].container_name
        if canonical_name is not None and record["Name"] != canonical_name:
            raise DeploymentContractError(
                f"compose readiness container name drifted: {service_name}"
            )
    return indexed


@dataclass(frozen=True)
class ValidatedComposeCandidate:
    resolved: Mapping[str, Any] = field(repr=False)
    system_bind_snapshots: tuple[CandidateSystemBindSnapshot, ...]
    raw_volume_graph_hash: str = ""
    resolved_volume_graph_hash: str = ""
    environment_snapshot: ComposeEnvironmentSnapshot | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    external_input_snapshot: ComposeExternalInputSnapshot | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    transaction_snapshot: ComposeTransactionSnapshot | None = field(
        default=None,
        repr=False,
        compare=False,
    )


_TRUSTED_FROZEN_RECOVERY_CAPABILITY = object()


def _serialize_resolved_compose_document(resolved: Mapping[str, Any]) -> str:
    return json.dumps(
        resolved,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _escape_materialized_compose_environment_values(
    resolved: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Compose 재입력에서 이미 해석된 환경값의 ``$``를 보존한다.

    ``docker compose config``가 만든 정본 문서를 다시 ``-f -``로
    전달하는 경로에서는 Compose가 환경값도 한 번 더 보간한다. 비밀번호가
    포함된 DSN처럼 값 자체에 홀수 개의 연속 ``$``가 있으면 그 문자가 변수
    시작으로 오인되어 값이 잘리고, 특히 bootstrap one-shot의 연결 문자열이
    손상된다. Compose config가 이미 보존용으로 짝지은 ``$$``는 그대로 두고,
    홀수 길이의 달러 run만 하나 늘린다. 환경값만 재보간 방지용으로
    이스케이프하고 command/entrypoint는 건드리지 않는다. command의
    ``$$VAR``는 컨테이너 셸에서 의도한 ``$VAR`` 동작을 유지해야 하기
    때문이다.
    """

    materialized = deepcopy(resolved)
    services = materialized.get("services")
    if not isinstance(services, dict):
        return materialized
    for service in services.values():
        if not isinstance(service, dict):
            continue
        environment = service.get("environment")
        if isinstance(environment, dict):
            for name, value in environment.items():
                if isinstance(value, str):
                    environment[name] = _escape_unpaired_compose_dollars(value)
        elif isinstance(environment, list):
            service["environment"] = [
                _escape_unpaired_compose_dollars(entry)
                if isinstance(entry, str)
                else entry
                for entry in environment
            ]
    return materialized


def _escape_unpaired_compose_dollars(value: str) -> str:
    escaped: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "$":
            escaped.append(value[index])
            index += 1
            continue
        end = index
        while end < len(value) and value[end] == "$":
            end += 1
        run_length = end - index
        escaped.append("$" * (run_length if run_length % 2 == 0 else run_length + 1))
        index = end
    return "".join(escaped)


def _resolved_compose_document_hash(resolved: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _serialize_resolved_compose_document(resolved).encode("utf-8")
    ).hexdigest()


_MAX_EXTERNAL_INPUT_BYTES = 1_048_576


def _effective_snapshot_environment(
    snapshot: ComposeEnvironmentSnapshot,
    environment_override: Mapping[str, str] | None,
) -> Mapping[str, str]:
    if environment_override is None:
        return MappingProxyType(
            derive_curation_service_principal_environment(snapshot.effective)
        )
    merged = dict(snapshot.effective)
    merged.update(environment_override)
    return MappingProxyType(derive_curation_service_principal_environment(merged))


def _external_reference_graph(
    candidate: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
    compose_path: str,
    root_env_path: str,
) -> tuple[ComposeExternalReference, ...]:
    for collection_name in ("secrets", "configs"):
        collection = candidate.get(collection_name)
        if collection is None:
            continue
        if not isinstance(collection, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate top-level {collection_name} is invalid"
            )
        if any(
            isinstance(source, Mapping) and "file" in source
            for source in collection.values()
        ):
            raise ComposeCandidateContractError(
                f"compose candidate top-level {collection_name} file resources are unsupported"
            )

    services = candidate.get("services")
    if not isinstance(services, Mapping):
        raise ComposeCandidateContractError(
            "compose candidate has no valid services mapping"
        )
    try:
        compose_directory = Path(compose_path).resolve().parent
        root_env = Path(root_env_path).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ComposeCandidateContractError(
            "compose external input paths cannot be resolved"
        ) from exc

    references: list[ComposeExternalReference] = []
    for service_name in sorted(str(name) for name in services):
        service = services.get(service_name)
        if not isinstance(service, Mapping):
            continue
        raw_entries = service.get("env_file")
        if raw_entries is None:
            continue
        if not isinstance(raw_entries, list):
            raise ComposeCandidateContractError(
                "compose candidate env_file syntax is unsupported"
            )
        for index, entry in enumerate(raw_entries):
            if (
                not isinstance(entry, Mapping)
                or set(entry) != {"path", "required", "format"}
                or not isinstance(entry.get("path"), str)
                or type(entry.get("required")) is not bool
                or entry.get("format") != "raw"
            ):
                raise ComposeCandidateContractError(
                    "compose candidate env_file syntax is unsupported"
                )
            raw_path = str(entry["path"])
            if not raw_path:
                raise ComposeCandidateContractError(
                    "compose candidate env_file path is empty"
                )
            try:
                expanded = _expand_env_path(raw_path, environment)
                path = Path(expanded)
                if not path.is_absolute():
                    path = compose_directory / path
                resolved_path = path.resolve()
            except (OSError, RuntimeError, ValueError) as exc:
                raise ComposeCandidateContractError(
                    "compose candidate env_file path cannot be resolved"
                ) from exc
            if resolved_path == root_env:
                raise ComposeCandidateContractError(
                    "compose candidate service must not load the manager root .env"
                )
            references.append(
                ComposeExternalReference(
                    service=service_name,
                    index=index,
                    raw_path=raw_path,
                    resolved_path=str(resolved_path),
                    required=bool(entry["required"]),
                    format="raw",
                )
            )
    return tuple(references)


def _capture_compose_external_input_snapshot(
    candidate: Mapping[str, Any],
    *,
    environment_snapshot: ComposeEnvironmentSnapshot,
    environment_override: Mapping[str, str] | None = None,
) -> ComposeExternalInputSnapshot:
    environment = _effective_snapshot_environment(
        environment_snapshot,
        environment_override,
    )
    references = _external_reference_graph(
        candidate,
        environment=environment,
        compose_path=environment_snapshot.compose_path,
        root_env_path=environment_snapshot.env_path,
    )
    required_by_path: dict[str, bool] = {}
    for reference in references:
        required_by_path[reference.resolved_path] = (
            required_by_path.get(reference.resolved_path, False)
            or reference.required
        )

    files: list[ComposeExternalFileSnapshot] = []
    for path_text in sorted(required_by_path):
        path = Path(path_text)
        before = _env_file_identity(path)
        if not before.exists:
            if required_by_path[path_text]:
                raise ComposeCandidateContractError(
                    "required compose external env_file is missing"
                )
            if _env_file_identity(path).exists:
                raise ComposeCandidateContractError(
                    "compose external env_file appeared during snapshot"
                )
            files.append(
                ComposeExternalFileSnapshot(
                    path=path_text,
                    identity=before,
                    contents=b"",
                )
            )
            continue
        if before.mode is None or not stat.S_ISREG(before.mode):
            raise ComposeCandidateContractError(
                "compose external env_file is not a regular file"
            )
        try:
            contents = path.read_bytes()
        except OSError as exc:
            raise ComposeCandidateContractError(
                "compose external env_file snapshot cannot be read"
            ) from exc
        if len(contents) > _MAX_EXTERNAL_INPUT_BYTES:
            raise ComposeCandidateContractError(
                "compose external env_file exceeds the snapshot limit"
            )
        if _env_file_identity(path) != before:
            raise ComposeCandidateContractError(
                "compose external env_file identity changed during snapshot"
            )
        files.append(
            ComposeExternalFileSnapshot(
                path=path_text,
                identity=before,
                contents=contents,
            )
        )
    return ComposeExternalInputSnapshot(
        references=references,
        files=tuple(files),
    )


def _revalidate_compose_external_input_snapshot(
    snapshot: ComposeExternalInputSnapshot,
    *,
    candidate: Mapping[str, Any] | None = None,
    environment_snapshot: ComposeEnvironmentSnapshot | None = None,
    environment_override: Mapping[str, str] | None = None,
) -> None:
    if candidate is not None:
        if environment_snapshot is None:
            raise ComposeCandidateContractError(
                "compose external input revalidation has no environment snapshot"
            )
        current_graph = _external_reference_graph(
            candidate,
            environment=_effective_snapshot_environment(
                environment_snapshot,
                environment_override,
            ),
            compose_path=environment_snapshot.compose_path,
            root_env_path=environment_snapshot.env_path,
        )
        if current_graph != snapshot.references:
            raise ComposeCandidateContractError(
                "compose external reference graph changed during the transaction"
            )
    for file_snapshot in snapshot.files:
        path = Path(file_snapshot.path)
        current_identity = _env_file_identity(path)
        if current_identity != file_snapshot.identity:
            raise ComposeCandidateContractError(
                "compose external env_file identity changed during the transaction"
            )
        if not current_identity.exists:
            continue
        try:
            current_contents = path.read_bytes()
        except OSError as exc:
            raise ComposeCandidateContractError(
                "compose external env_file cannot be revalidated"
            ) from exc
        if current_contents != file_snapshot.contents:
            raise ComposeCandidateContractError(
                "compose external env_file bytes changed during the transaction"
            )
        if _env_file_identity(path) != current_identity:
            raise ComposeCandidateContractError(
                "compose external env_file identity changed during revalidation"
            )


def _external_snapshot_contents(
    snapshot: ComposeExternalInputSnapshot,
) -> Mapping[str, bytes]:
    return MappingProxyType(
        {file_snapshot.path: file_snapshot.contents for file_snapshot in snapshot.files}
    )


def _materialize_external_inputs_with_memfd(
    candidate: Mapping[str, Any],
    snapshot: ComposeExternalInputSnapshot,
) -> tuple[dict[str, Any], tuple[int, ...]]:
    """Secret env_file bytes를 disk에 쓰지 않고 inherited memfd로 Compose에 준다."""

    document = deepcopy(dict(candidate))
    services = document.get("services")
    if not isinstance(services, dict):
        raise ComposeCandidateContractError(
            "compose candidate has no materializable services mapping"
        )
    contents_by_path = _external_snapshot_contents(snapshot)
    descriptors: dict[str, int] = {}
    opened: list[int] = []
    try:
        for file_snapshot in snapshot.files:
            try:
                descriptor = os.memfd_create("compose-env", flags=0)
            except (AttributeError, OSError) as exc:
                raise ComposeCandidateContractError(
                    "compose external input memory snapshot cannot be created"
                ) from exc
            opened.append(descriptor)
            payload = contents_by_path[file_snapshot.path]
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise ComposeCandidateContractError(
                        "compose external input memory snapshot cannot be written"
                    )
                view = view[written:]
            os.lseek(descriptor, 0, os.SEEK_SET)
            descriptors[file_snapshot.path] = descriptor
        for reference in snapshot.references:
            service = services.get(reference.service)
            if not isinstance(service, dict):
                raise ComposeCandidateContractError(
                    "compose external reference service changed"
                )
            entries = service.get("env_file")
            if not isinstance(entries, list) or reference.index >= len(entries):
                raise ComposeCandidateContractError(
                    "compose external reference graph changed"
                )
            entry = entries[reference.index]
            if not isinstance(entry, dict):
                raise ComposeCandidateContractError(
                    "compose external reference syntax changed"
                )
            entry["path"] = f"/proc/self/fd/{descriptors[reference.resolved_path]}"
        return document, tuple(opened)
    except Exception:
        for descriptor in opened:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def _assert_resolved_external_inputs_materialized(
    resolved: Mapping[str, Any],
) -> None:
    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise ComposeCandidateContractError(
            "resolved compose has no services mapping"
        )
    if any(
        isinstance(service, Mapping) and service.get("env_file")
        for service in services.values()
    ):
        raise ComposeCandidateContractError(
            "resolved compose retained a live env_file reference"
        )
    for collection_name in ("secrets", "configs"):
        collection = resolved.get(collection_name)
        if isinstance(collection, Mapping) and any(
            isinstance(source, Mapping) and source.get("file")
            for source in collection.values()
        ):
            raise ComposeCandidateContractError(
                "resolved compose retained an external file resource"
            )


def _env_file_identity(path: Path) -> ComposeEnvFileIdentity:
    try:
        source_stat = path.stat()
    except FileNotFoundError:
        return ComposeEnvFileIdentity(exists=False)
    except OSError as exc:
        raise ComposeCandidateContractError(
            "compose env-file identity cannot be inspected"
        ) from exc
    return ComposeEnvFileIdentity(
        exists=True,
        device=source_stat.st_dev,
        inode=source_stat.st_ino,
        mode=source_stat.st_mode,
        uid=source_stat.st_uid,
        gid=source_stat.st_gid,
    )


def _capture_compose_environment_snapshot(
    *,
    environment_override: Mapping[str, str] | None,
    env_path: Path | None = None,
    compose_path: Path | None = None,
    override_path: Path | None = None,
    include_process_environment: bool = True,
    interpolate_env_file: bool = True,
) -> ComposeEnvironmentSnapshot:
    env_path = (
        Path(get_env_path()).resolve(strict=False)
        if env_path is None
        else env_path.resolve(strict=False)
    )
    compose_path = (
        Path(get_compose_path()).resolve(strict=False)
        if compose_path is None
        else compose_path.resolve(strict=False)
    )
    override_path = (
        Path(get_override_path()).resolve(strict=False)
        if override_path is None
        else override_path.resolve(strict=False)
    )
    before = _env_file_identity(env_path)
    env_file_bytes = b""
    values: dict[str, str] = {}
    if before.exists:
        try:
            env_file_bytes = env_path.read_bytes()
            decoded = env_file_bytes.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ComposeCandidateContractError(
                "compose env-file snapshot cannot be read"
            ) from exc
        after = _env_file_identity(env_path)
        if after != before:
            raise ComposeCandidateContractError(
                "compose env-file identity changed during snapshot"
            )
        try:
            values.update(
                {
                    key: value or ""
                    for key, value in dotenv_values(
                        stream=StringIO(decoded),
                        interpolate=interpolate_env_file,
                    ).items()
                    if isinstance(key, str)
                }
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise ComposeCandidateContractError(
                "compose env-file snapshot cannot be parsed"
            ) from exc
    elif _env_file_identity(env_path).exists:
        raise ComposeCandidateContractError(
            "compose env-file appeared during snapshot"
        )
    if include_process_environment:
        values.update(dict(os.environ))
    if environment_override is not None:
        values.update(environment_override)
    values = derive_curation_service_principal_environment(values)
    return ComposeEnvironmentSnapshot(
        effective=MappingProxyType(values),
        env_path=str(env_path),
        compose_path=str(compose_path),
        override_path=str(override_path),
        env_file_identity=before,
        env_file_bytes=env_file_bytes,
    )


def _revalidate_compose_environment_snapshot(
    snapshot: ComposeEnvironmentSnapshot,
) -> None:
    env_path = Path(snapshot.env_path)
    current_identity = _env_file_identity(env_path)
    if current_identity != snapshot.env_file_identity:
        raise ComposeCandidateContractError(
            "compose env-file identity changed during the transaction"
        )
    if not current_identity.exists:
        return
    try:
        current_bytes = env_path.read_bytes()
    except OSError as exc:
        raise ComposeCandidateContractError(
            "compose env-file cannot be revalidated"
        ) from exc
    if current_bytes != snapshot.env_file_bytes:
        raise ComposeCandidateContractError(
            "compose env-file bytes changed during the transaction"
        )
    if _env_file_identity(env_path) != current_identity:
        raise ComposeCandidateContractError(
            "compose env-file identity changed during revalidation"
        )


def _atomic_restore_compose_source(
    path: Path,
    payload: bytes,
    *,
    mode: int,
) -> None:
    # GM-10: services/secure_state_file.py에 이 패턴의 정본이 있다. 한 차례
    # 정본 `atomic_write_bytes`로 옮겼다가 적대적 리뷰로 되돌렸다(PR #300) — 이
    # 자리의 디렉터리 fsync 실패는 `_recover_persisted_target_runtime`이
    # `recovery_succeeded`를 판정하는 유일한 신호원인데, 정본의
    # `fsync_directory`는 디렉터리 fsync 실패를 무조건 삼킨다(best-effort).
    # 그러면 os.replace의 crash-durability가 실제로는 확인되지 않았는데도
    # 이 함수가 정상 반환해 복구가 "성공"으로 보고된다 — 바로 이 이유로
    # `legacy_override_retirement.py`/`pinvi_database_role_credentials.py`의
    # `_write_atomic`도 정본으로 옮기지 않았다(docs/tasks.md). 같은 논리를
    # 이 자리에도 적용해 strict 원본을 유지한다.
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".restore",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        temporary_path = None
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


# compatible-pair 활성화 단계의 `docker compose up --wait --wait-timeout` 상한(초).
# kor-travel-map API는 uvicorn 기동 전에 `alembic upgrade head`를 실행하므로(issue #88),
# 긴 마이그레이션을 수반하는 배포는 기본값보다 큰 값이 필요하다. 하한/상한은 pathological
# 값(0·음수·사실상 무한대)을 막는 sanity bound다 — 실측된 최장 마이그레이션(8~18분)에
# 여유를 둔 1시간을 상한으로 잡는다.
_DEFAULT_C6C_WAIT_TIMEOUT_SECONDS = 120
_MIN_C6C_WAIT_TIMEOUT_SECONDS = 1
_MAX_C6C_WAIT_TIMEOUT_SECONDS = 3600


def _validate_c6c_wait_timeout(wait_timeout: int) -> None:
    """legacy C6c wait-timeout 입력의 범위를 검증한다.

    current `rebuild-pinned` CLI는 이 helper를 호출하지 않는다. 남은 내부 caller도
    lock 진입 전에 유효 범위를 확인해야 한다.
    """
    if not isinstance(wait_timeout, int) or isinstance(wait_timeout, bool):
        raise DeploymentContractError("wait_timeout must be an int")
    if not (
        _MIN_C6C_WAIT_TIMEOUT_SECONDS <= wait_timeout <= _MAX_C6C_WAIT_TIMEOUT_SECONDS
    ):
        raise DeploymentContractError(
            "wait_timeout must be between "
            f"{_MIN_C6C_WAIT_TIMEOUT_SECONDS} and "
            f"{_MAX_C6C_WAIT_TIMEOUT_SECONDS} seconds"
        )


# issue #109: `kor-travel-map-api`의 entrypoint는 기동마다 무조건 `alembic upgrade
# head`를 실행한다. floating tag(`latest-main`)로 배포된 이미지가 pin보다 오래
# 빌드된 채였고, 그 이미지의 alembic head(0072)까지만 prod schema가 조용히
# 올라가 공개 표면이 0이 됐다(issue #109). candidate image 자체를 절대 기동하지
# 않고 `alembic heads`만 읽어(DB에 아무 것도 하지 않는 static inspection) operator가
# 명시한 기대 head와 다르면 배포를 시작하기 전에 fail-close한다.
#
# 이 두 타임아웃은 멈춤 감지용이지 성능 예산이 아니다. n150은 SATA SSD가 92% 차서 IO
# 압력 `full`이 상시 50~60%이고, 2026-09-26 실측에서 `docker run --rm /bin/true` 하나가
# 112초, `ktm-application-schema head`가 74초 걸렸다 — 60초였을 때 t57a가 명령은
# 정상인데 타임아웃으로 죽었다.
_ALEMBIC_HEAD_INSPECTION_TIMEOUT_SECONDS = 600
_PINNED_RUNTIME_STATIC_INSPECTION_TIMEOUT_SECONDS = 600
#: compose `--wait-timeout` 초. **정수**로 둔다 — head는 revision 문자열이라
#: 형이 다르고, 이 파일에 따옴표 두른 숫자가 남지 않아 head 리터럴 게이트가
#: 파일 단위 면제 없이 이 파일을 전부 볼 수 있다. 면제는 그 자체로 사각지대였다.
#:
#: ADR-069 뒤 Map은 code-server → webserver → daemon이 `service_healthy`로 **직렬**
#: 기동한다. 위 실측대로 컨테이너 하나가 뜨는 데만 1~2분이 걸리므로 300초는 부족하다.
_COMPOSE_WAIT_TIMEOUT_SECONDS: Final = 900


def _validate_expected_alembic_head(expected_alembic_head: str) -> None:
    if (
        not expected_alembic_head
        or expected_alembic_head != expected_alembic_head.strip()
        or "\n" in expected_alembic_head
        or "\r" in expected_alembic_head
        or len(expected_alembic_head) > 128
    ):
        raise DeploymentContractError("expected alembic head is invalid")


def _assert_candidate_image_alembic_head(
    image: str,
    *,
    expected_alembic_head: str,
    label: str,
) -> None:
    """candidate `image`를 기동하지 않고 `alembic heads`만 정적으로 읽어 비교한다.

    DB에 연결하지 않는 `--entrypoint sh ... alembic heads`만 실행하므로 실제
    migration은 절대 실행되지 않는다. 여러 head(merge 누락 등)나 예상과 다른 head,
    실행 자체의 실패는 모두 배포를 막는 동일한 fail-close 사유다. raw stdout/stderr는
    노출하지 않는다 — 어느 head들이 나왔는지는 운영 감사에 필요하지 않고, 이미지
    내부 경로/의존성 정보를 노출할 수 있다.
    """

    try:
        completed = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                image,
                "-c",
                "cd /app && alembic heads",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_ALEMBIC_HEAD_INSPECTION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            f"{label} candidate image alembic head could not be inspected"
        ) from exc
    if completed.returncode != 0:
        raise DeploymentContractError(
            f"{label} candidate image alembic head inspection failed"
        )
    heads = [
        line.split()[0]
        for line in completed.stdout.splitlines()
        if line.strip() and "(head)" in line
    ]
    if len(heads) != 1 or heads[0] != expected_alembic_head:
        raise DeploymentContractError(
            f"{label} candidate image alembic head differs from the expected head"
        )


def _run_pinned_runtime_static_command(
    image_id: str,
    command: Sequence[str],
    *,
    label: str,
    entrypoint: str | None = None,
) -> str:
    """candidate artifact를 network 없이 검사하고 raw output은 호출자만 파싱한다."""

    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise DeploymentContractError(f"{label} candidate image ID is invalid")
    if not command or any(not argument or "\x00" in argument for argument in command):
        raise DeploymentContractError(f"{label} candidate static command is invalid")
    if entrypoint is not None and re.fullmatch(r"/[A-Za-z0-9._/-]+", entrypoint) is None:
        raise DeploymentContractError(f"{label} candidate static entrypoint is invalid")
    docker_command = ["docker", "run", "--rm", "--network", "none"]
    if entrypoint is not None:
        docker_command.extend(("--entrypoint", entrypoint))
    docker_command.extend((image_id, *command))
    try:
        completed = subprocess.run(
            docker_command,
            cwd=get_project_root(),
            text=True,
            capture_output=True,
            check=False,
            timeout=_PINNED_RUNTIME_STATIC_INSPECTION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            f"{label} candidate static inspection could not start"
        ) from exc
    if completed.returncode != 0 or len(completed.stdout) > 1024 or completed.stderr:
        raise DeploymentContractError(f"{label} candidate static inspection failed")
    return completed.stdout


@dataclass(frozen=True)
class _MapApplication300Paths:
    """ADR-101 이후 남은 host artifact — 빌드 영수증 둘과 metadata permit 하나."""

    api_receipt: Path
    paired_receipt: Path
    metadata_permit_directory: Path

    @property
    def metadata_permit(self) -> Path:
        return self.metadata_permit_directory / "permit.json"


def _map_application_300_paths(
    *, state_root: Path, pinset_sha256: str
) -> _MapApplication300Paths:
    if re.fullmatch(r"[0-9a-f]{64}", pinset_sha256) is None:
        raise DeploymentContractError("application 300 pinset identity is invalid")
    receipt_directory = (
        state_root / _MAP_APPLICATION_300_RECEIPT_DIRECTORY / pinset_sha256
    )
    artifact_directory = (
        state_root / _MAP_APPLICATION_300_ARTIFACT_DIRECTORY / pinset_sha256
    )
    for directory in (
        receipt_directory.parent,
        receipt_directory,
        artifact_directory.parent,
        artifact_directory,
    ):
        _ensure_application_300_private_directory(directory)
    metadata_permit_directory = artifact_directory / "dagster-storage-permit"
    _ensure_application_300_mount_directory(metadata_permit_directory)
    return _MapApplication300Paths(
        api_receipt=receipt_directory / "api-candidate-build.json",
        paired_receipt=receipt_directory / "paired-candidate-build.json",
        metadata_permit_directory=metadata_permit_directory,
    )


def _ensure_application_300_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        metadata = path.lstat()
    except OSError as exc:
        raise DeploymentContractError(
            "application 300 state directory is unavailable"
        ) from exc
    if (
        path != path.resolve(strict=True)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise DeploymentContractError("application 300 state directory is unsafe")


def _ensure_application_300_mount_directory(path: Path) -> None:
    """비-root image가 읽되 root만 쓸 수 있는 fixed-artifact 디렉터리를 만든다."""

    if os.geteuid() != 0:
        raise DeploymentContractError(
            "application 300 mount directory requires root"
        )
    try:
        path.mkdir(mode=0o755, exist_ok=True)
        metadata = path.lstat()
    except OSError as exc:
        raise DeploymentContractError(
            "application 300 mount directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or path != path.resolve(strict=True)
    ):
        raise DeploymentContractError(
            "application 300 mount directory is unsafe"
        )
    try:
        os.chmod(path, 0o755, follow_symlinks=False)
        normalized = path.lstat()
    except OSError as exc:
        raise DeploymentContractError(
            "application 300 mount directory cannot be normalized"
        ) from exc
    if (
        not stat.S_ISDIR(normalized.st_mode)
        or stat.S_ISLNK(normalized.st_mode)
        or normalized.st_uid != 0
        or stat.S_IMODE(normalized.st_mode) != 0o755
    ):
        raise DeploymentContractError(
            "application 300 mount directory is unsafe"
        )


def _build_map_application_300_images(
    *,
    sources: PinnedRuntimeSourceMaterialization,
    api_image: str,
    dagster_image: str,
) -> None:
    """api/dagster 두 이미지를 직접 빌드한다 -- sealed 외부 스크립트는 없다."""

    map_source = sources.source_for("map")
    builder_environment = {
        name: value
        for name in ("DOCKER_CONFIG", "DOCKER_HOST", "XDG_RUNTIME_DIR")
        if (value := os.environ.get(name)) is not None
    }
    builder_environment["PATH"] = (
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )
    for image, dockerfile in (
        (api_image, "docker/api.Dockerfile"),
        (dagster_image, "docker/dagster.Dockerfile"),
    ):
        try:
            completed = subprocess.run(
                [
                    "docker",
                    "buildx",
                    "build",
                    "--load",
                    "--file",
                    str(map_source.root / dockerfile),
                    "--tag",
                    image,
                    "--build-arg",
                    f"KOR_TRAVEL_MAP_GIT_COMMIT={map_source.revision}",
                    str(map_source.root),
                ],
                cwd="/",
                env=builder_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                "application 300 image build could not start"
            ) from exc
        if completed.returncode != 0:
            raise DeploymentContractError(
                f"application 300 image build failed ({dockerfile})"
            )


def _inspect_local_image_id(image: str) -> str:
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            cwd="/",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            "application 300 image cannot be inspected"
        ) from exc
    image_id = completed.stdout.decode("ascii", errors="replace").strip()
    if completed.returncode != 0 or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise DeploymentContractError(
            "application 300 image cannot be inspected"
        )
    return image_id


def _resolve_map_postgres_image_id() -> str:
    """참조 이미지를 pull-if-missing 뒤 content-addressed id로 관측한다."""

    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", _MAP_APPLICATION_300_POSTGRES_REFERENCE],
            cwd="/",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            "Map PostgreSQL candidate image cannot be inspected"
        ) from exc
    if completed.returncode != 0:
        try:
            pulled = subprocess.run(
                ["docker", "pull", _MAP_APPLICATION_300_POSTGRES_REFERENCE],
                cwd="/",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=900,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                "Map PostgreSQL candidate image is unavailable"
            ) from exc
        if pulled.returncode != 0:
            raise DeploymentContractError("Map PostgreSQL candidate image is unavailable")
        return _inspect_local_image_id(_MAP_APPLICATION_300_POSTGRES_REFERENCE)
    image_id = completed.stdout.decode("ascii", errors="replace").strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise DeploymentContractError("Map PostgreSQL candidate image cannot be inspected")
    return image_id


def _load_application_300_candidate(
    *,
    sources: PinnedRuntimeSourceMaterialization,
    api_image: str,
    dagster_image: str,
) -> MapApplicationCandidate:
    """빌드된 두 이미지를 관측해서 후보 identity를 만든다."""

    map_source = sources.source_for("map")
    dagster_config_sha256 = hashlib.sha256(
        (map_source.root / "docker" / "dagster.yaml").read_bytes()
    ).hexdigest()
    api_image_id = _inspect_local_image_id(api_image)
    application_head_output = _run_pinned_runtime_static_command(
        api_image_id,
        ("head",),
        label="Map application",
        entrypoint="/usr/local/bin/ktm-application-schema",
    )
    application_head = parse_candidate_static_head(
        application_head_output,
        schema="kor-travel-map.application-head.v1",
        field="head",
    )
    return MapApplicationCandidate(
        postgres_image_id=_resolve_map_postgres_image_id(),
        candidate_commit=map_source.revision,
        candidate_git_tree=map_source.tree,
        api_image_id=api_image_id,
        dagster_image_id=_inspect_local_image_id(dagster_image),
        dagster_config_sha256=dagster_config_sha256,
        application_head=application_head,
    )


def map_application_300_python_base_references_from_root(
    map_root: Path,
) -> tuple[str, ...]:
    """sealed Map Dockerfile이 요구하는 immutable Python base만 반환한다.

    root를 인자로 받는 이유는 읽기 전용 readiness 점검(P10-4)이 **같은 파서**를 써야
    하기 때문이다. 판독 규칙이 두 벌이 되면 화면의 사전 점검과 실제 rebuild가 서로
    다른 base를 보게 되고, 그건 이 점검이 없애려던 실패 그 자체다. 이 함수는
    materialization을 요구하지 않아 비-root 프로세스도 호출할 수 있으며, 그 root가
    어떤 revision인지(pin과 일치하는지)는 **호출자가 책임진다.**
    """

    references: set[str] = set()
    for dockerfile_name in ("api.Dockerfile", "dagster.Dockerfile"):
        try:
            dockerfile = (map_root / "docker" / dockerfile_name).read_text(
                encoding="utf-8"
            )
        except OSError as exc:
            raise DeploymentContractError(
                "Map application candidate Dockerfile is unavailable"
            ) from exc
        from_lines = tuple(
            line.strip()
            for line in dockerfile.splitlines()
            if re.match(r"^FROM(?:\s|$)", line.strip(), flags=re.IGNORECASE)
        )
        if len(from_lines) != 2:
            raise DeploymentContractError(
                "Map application candidate base image contract is invalid"
            )
        stages = tuple(
            re.fullmatch(
                r"FROM (python@sha256:[0-9a-f]{64}) AS (builder|runtime)", line
            )
            for line in from_lines
        )
        if any(stage is None for stage in stages):
            raise DeploymentContractError(
                "Map application candidate base image contract is invalid"
            )
        resolved_stages = tuple(cast(re.Match[str], stage).groups() for stage in stages)
        if {stage for _, stage in resolved_stages} != {
            "builder",
            "runtime",
        }:
            raise DeploymentContractError(
                "Map application candidate base image contract is invalid"
            )
        image_references = {reference for reference, _ in resolved_stages}
        if len(image_references) != 1:
            raise DeploymentContractError(
                "Map application candidate base image contract is invalid"
            )
        references.update(image_references)
    return tuple(sorted(references))


def _map_application_300_python_base_references(
    sources: PinnedRuntimeSourceMaterialization,
) -> tuple[str, ...]:
    """materialized pinned tree 전용 래퍼. 기존 호출부 계약을 그대로 보존한다."""

    return map_application_300_python_base_references_from_root(sources.source_for("map").root)


def _ensure_map_application_300_python_base_images(
    sources: PinnedRuntimeSourceMaterialization,
) -> None:
    """candidate build 전에 digest-pinned Python base를 cache에 확보·재관측한다."""

    for image_reference in _map_application_300_python_base_references(sources):
        try:
            inspected = subprocess.run(
                ["docker", "image", "inspect", image_reference],
                cwd="/",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                "Map application immutable base image is unavailable"
            ) from exc
        if inspected.returncode == 0:
            continue
        try:
            pulled = subprocess.run(
                ["docker", "pull", image_reference],
                cwd="/",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=900,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                "Map application immutable base image is unavailable"
            ) from exc
        if pulled.returncode != 0:
            raise DeploymentContractError(
                "Map application immutable base image is unavailable"
            )
        try:
            verified = subprocess.run(
                ["docker", "image", "inspect", image_reference],
                cwd="/",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                "Map application immutable base image is unavailable"
            ) from exc
        if verified.returncode != 0:
            raise DeploymentContractError(
                "Map application immutable base image is unavailable"
            )


#: 형제 프로젝트 호출에 물려줄 최소 환경. Compose에서 셸 환경은 `.env`보다 우선하므로
#: Manager의 변수를 그대로 상속하면 남의 프로젝트 설정을 조용히 덮어쓴다.
_EXTERNAL_PROJECT_PASSTHROUGH_ENV: Final = frozenset(
    {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "XDG_RUNTIME_DIR"}
)


class ComposeService:
    def capture_transaction_unlocked(
        self,
        *,
        environment_override: Mapping[str, str] | None = None,
        derive_manifest_path: bool = False,
        environment_snapshot: ComposeEnvironmentSnapshot | None = None,
    ) -> tuple[ComposeTransactionSnapshot, ValidatedComposeCandidate]:
        """GM-20: docker_service.py가 자신이 이미 확보한 host lock 아래에서 직접
        호출하도록 공개 승격했다(이전에는 프라이빗 크로스 모듈 호출이었다).

        **선행조건**: 호출자가 c6c deployment host lock(실제 flock)을 이미 잡고
        있어야 한다 — 이름의 "_unlocked"는 이 메서드 자신은 lock을 추가로 잡지
        않는다는 뜻이지, lock 없이 안전하다는 뜻이 아니다. 이 lock을 얻는 경로는
        하나가 아니다: `c6c_deployment_lock_from_environment()`가 가장 흔하지만,
        `rebuild_pinned_runtime`처럼 `pinned_runtime_rebuild_lock()` 경로를 타는
        호출자는 `_pinned_runtime_rebuild_environment_lock()` 안에서
        `c6c_deployment_lock(lock_snapshot.lock_path)`를 직접 잡아 같은 flock에
        도달한다 — 어느 경로든 "이 host의 c6c deployment lock을 쥔 채로"만
        만족하면 된다. lock 없이 호출하면 동시 mutation과 경합해 읽은 compose
        원문이 곧바로 stale해질 수 있다."""
        if environment_snapshot is None:
            environment_snapshot = _capture_compose_environment_snapshot(
                environment_override=None,
            )
        compose_path = Path(environment_snapshot.compose_path)
        try:
            source_bytes = compose_path.read_bytes()
            source_mode = compose_path.stat().st_mode & 0o777
        except OSError as exc:
            raise ComposeCandidateContractError(
                "compose transaction source cannot be snapshotted"
            ) from exc
        validation = self._validate_current_compose_candidate_unlocked(
            environment_override=environment_override,
            environment_snapshot=environment_snapshot,
        )
        external_inputs = validation.external_input_snapshot
        if external_inputs is None:
            try:
                source_document = yaml.safe_load(source_bytes.decode("utf-8")) or {}
            except (UnicodeError, ValueError, yaml.YAMLError) as exc:
                raise ComposeCandidateContractError(
                    "compose transaction source cannot be loaded"
                ) from exc
            if not isinstance(source_document, Mapping):
                raise ComposeCandidateContractError(
                    "compose transaction source is not a mapping"
                )
            external_inputs = _capture_compose_external_input_snapshot(
                source_document,
                environment_snapshot=environment_snapshot,
                environment_override=environment_override,
            )
            validation = replace(
                validation,
                environment_snapshot=environment_snapshot,
                external_input_snapshot=external_inputs,
            )
        if compose_path.read_bytes() != source_bytes:
            raise ComposeCandidateContractError(
                "compose transaction source changed during snapshot"
            )
        resolved = json.loads(_serialize_resolved_compose_document(validation.resolved))
        if not isinstance(resolved, Mapping):
            raise ComposeCandidateContractError(
                "compose transaction resolved document is invalid"
            )
        transaction = ComposeTransactionSnapshot(
            environment=environment_snapshot,
            external_inputs=external_inputs,
            compose_source_bytes=source_bytes,
            compose_source_mode=source_mode,
            system_bind_snapshots=validation.system_bind_snapshots,
            raw_volume_graph_hash=validation.raw_volume_graph_hash,
            resolved_volume_graph_hash=validation.resolved_volume_graph_hash,
            resolved=resolved,
            resolved_document_hash=_resolved_compose_document_hash(resolved),
            manifest_path=None,
        )
        return transaction, replace(
            validation,
            transaction_snapshot=transaction,
        )

    def build_command(
        self,
        args: Sequence[str],
        *,
        canonical_single_file: bool = False,
        compose_path: str | None = None,
        external: ExternalProject | None = None,
    ) -> list[str]:
        command = ["docker", "compose"]
        if external is not None:
            # 형제 프로젝트다. Manager의 `--env-file`도 override도 붙이지 않는다 —
            # 그 프로젝트는 자기 `working_dir`의 `.env`를 compose가 알아서 읽고,
            # Manager의 env를 주입하면 남의 프로젝트 값을 덮어쓴다.
            if canonical_single_file:
                raise DeploymentContractError(
                    "canonical single-file boundary does not apply to an external project"
                )
            command.extend(
                [
                    "-p",
                    external.project,
                    "--project-directory",
                    external.working_dir,
                ]
            )
            command.extend(external.compose_file_arguments())
            command.extend(args)
            return command
        if canonical_single_file:
            command.extend(
                [
                    "--env-file",
                    "/dev/null",
                    "--project-directory",
                    str(Path(compose_path or get_compose_path()).resolve().parent),
                    "-f",
                    "-",
                ]
            )
        else:
            env_path = get_env_path()
            if os.path.exists(env_path):
                command.extend(["--env-file", env_path])
        if not canonical_single_file:
            command.extend(["-f", compose_path or get_compose_path()])
        if not canonical_single_file:
            override_path = get_override_path()
            if os.path.exists(override_path):
                command.extend(["-f", override_path])
        command.extend(args)
        return command

    @staticmethod
    def _validate_frozen_transaction_unlocked(
        transaction: ComposeTransactionSnapshot,
    ) -> Mapping[str, Any]:
        try:
            source = yaml.safe_load(
                transaction.compose_source_bytes.decode("utf-8")
            ) or {}
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise ComposeCandidateContractError(
                "frozen compose transaction source is invalid"
            ) from exc
        if not isinstance(source, Mapping) or not isinstance(
            transaction.resolved, Mapping
        ):
            raise ComposeCandidateContractError(
                "frozen compose transaction document is invalid"
            )
        if transaction.compose_source_mode & ~0o777:
            raise ComposeCandidateContractError(
                "frozen compose transaction mode is invalid"
            )
        source_references: list[tuple[str, int, str, bool, str]] = []
        services = source.get("services")
        if not isinstance(services, Mapping):
            raise ComposeCandidateContractError(
                "frozen compose transaction has no services mapping"
            )
        for service_name in sorted(str(name) for name in services):
            service = services.get(service_name)
            if not isinstance(service, Mapping):
                continue
            entries = service.get("env_file", [])
            if not isinstance(entries, list):
                raise ComposeCandidateContractError(
                    "frozen compose transaction external graph is invalid"
                )
            for index, entry in enumerate(entries):
                if not isinstance(entry, Mapping):
                    raise ComposeCandidateContractError(
                        "frozen compose transaction external graph is invalid"
                    )
                source_references.append(
                    (
                        service_name,
                        index,
                        str(entry.get("path", "")),
                        entry.get("required") is True,
                        str(entry.get("format", "")),
                    )
                )
        snapshot_references = [
            (
                reference.service,
                reference.index,
                reference.raw_path,
                reference.required,
                reference.format,
            )
            for reference in transaction.external_inputs.references
        ]
        if source_references != snapshot_references:
            raise ComposeCandidateContractError(
                "frozen compose transaction external graph is inconsistent"
            )
        if compose_volume_graph_hash(source) != transaction.raw_volume_graph_hash:
            raise ComposeCandidateContractError(
                "frozen compose transaction raw graph is inconsistent"
            )
        if (
            compose_volume_graph_hash(transaction.resolved)
            != transaction.resolved_volume_graph_hash
        ):
            raise ComposeCandidateContractError(
                "frozen compose transaction resolved graph is inconsistent"
            )
        if (
            _resolved_compose_document_hash(transaction.resolved)
            != transaction.resolved_document_hash
        ):
            raise ComposeCandidateContractError(
                "frozen compose transaction resolved document is inconsistent"
            )
        _assert_resolved_external_inputs_materialized(transaction.resolved)
        revalidate_candidate_system_bind_snapshots(
            transaction.system_bind_snapshots
        )
        return transaction.resolved

    def _run_frozen_recovery(
        self,
        args: Sequence[str],
        *,
        transaction: ComposeTransactionSnapshot,
        capture_output: bool = True,
        mutation_capability: object | None = None,
        redact_config: C6cDeploymentConfig | None = None,
    ) -> dict[str, Any]:
        return self.run(
            args,
            capture_output=capture_output,
            mutation_capability=mutation_capability,
            redact_config=redact_config,
            transaction=transaction,
            _frozen_recovery_capability=_TRUSTED_FROZEN_RECOVERY_CAPABILITY,
        )

    def run(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = True,
        environment: Mapping[str, str] | None = None,
        mutation_capability: object | None = None,
        redact_config: C6cDeploymentConfig | None = None,
        expected_system_bind_snapshots: tuple[
            CandidateSystemBindSnapshot, ...
        ] | None = None,
        expected_raw_volume_graph_hash: str | None = None,
        expected_resolved_volume_graph_hash: str | None = None,
        expected_environment_snapshot: ComposeEnvironmentSnapshot | None = None,
        expected_external_input_snapshot: ComposeExternalInputSnapshot | None = None,
        transaction: ComposeTransactionSnapshot | None = None,
        external: ExternalProject | None = None,
        _frozen_recovery_capability: object | None = None,
    ) -> dict[str, Any]:
        # **guard와 분기가 같은 변수를 본다.** 첫 판은 guard를 `mutation_capability`
        # 같은 **인자의 존재**로 판정했는데, 변경 분기로 들어갈지를 실제로 정하는 것은
        # `_compose_mutation_identifiers(args)`다. 그래서 `run(["up","-d"], external=X)`가
        # guard를 통과했고, 그 아래 분기는 `external`을 조용히 버려서 형제 프로젝트를
        # 지시한 명령이 **Manager 자신의 프로젝트에** 갔다(적대 리뷰 2026-09-18 F1).
        #
        # 조건을 두 벌 쓰면 한쪽만 바뀌는 것이 이 버그의 모양이었다. 한 변수로 합치면
        # 드리프트가 구조적으로 불가능하다 — **이 변수를 분기에서 다시 풀어 쓰지 마라.**
        mutation_identifiers = self._compose_mutation_identifiers(args)
        enters_mutation_machinery = (
            bool(mutation_identifiers)
            or transaction is not None
            or expected_environment_snapshot is not None
        )
        if external is not None and (
            enters_mutation_machinery
            or mutation_capability is not None
            or expected_system_bind_snapshots is not None
        ):
            # 형제 프로젝트는 C6c 변경 기계를 통과한 적이 없다. 읽기(ps/logs)만
            # 허용하고 변경 경로는 여기서 끊는다 — `ensure_target`의 거부와 같은
            # 이유이고, 이쪽이 더 낮은 층이라 우회가 어렵다.
            raise DeploymentContractError(
                "external compose projects are read-only from the Manager; "
                "mutation paths are reserved for the Manager's own project"
            )
        if (
            _frozen_recovery_capability is not None
            and _frozen_recovery_capability is not _TRUSTED_FROZEN_RECOVERY_CAPABILITY
        ):
            raise ComposeCandidateContractError("untrusted frozen recovery capability")
        frozen_recovery = _frozen_recovery_capability is _TRUSTED_FROZEN_RECOVERY_CAPABILITY
        if enters_mutation_machinery:
            if frozen_recovery:
                if transaction is None or environment is not None:
                    raise ComposeCandidateContractError(
                        "frozen recovery requires one closed transaction"
                    )
                with _c6c_deployment_lock_from_transaction(transaction):
                    assert_compose_mutation_allowed(
                        mutation_identifiers,
                        environment=transaction.environment.effective,
                        capability=mutation_capability,
                    )
                    resolved = self._validate_frozen_transaction_unlocked(
                        transaction
                    )
                    return self._run_unlocked(
                        args,
                        capture_output=capture_output,
                        environment=None,
                        redact_config=redact_config,
                        expected_system_bind_snapshots=(
                            transaction.system_bind_snapshots
                        ),
                        expected_compose_source_bytes=None,
                        environment_snapshot=transaction.environment,
                        external_input_snapshot=None,
                        materialized_compose=resolved,
                    )
            with c6c_deployment_lock_from_environment() as lock_snapshot:
                captured_validation: ValidatedComposeCandidate | None = None
                if transaction is None and expected_environment_snapshot is None:
                    transaction, captured_validation = self.capture_transaction_unlocked(
                        environment_override=environment,
                    )
                    _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
                environment_snapshot = (
                    transaction.environment
                    if transaction is not None
                    else expected_environment_snapshot
                )
                if environment_snapshot is None:
                    raise ComposeCandidateContractError(
                        "compose transaction has no environment snapshot"
                    )
                assert_environment_snapshot_matches_c6c_lock(
                    environment_snapshot,
                    lock_snapshot,
                )
                assert_compose_mutation_allowed(
                    mutation_identifiers,
                    environment=environment_snapshot.effective,
                    capability=mutation_capability,
                )
                compose_source_bytes = (
                    transaction.compose_source_bytes
                    if transaction is not None
                    else Path(environment_snapshot.compose_path).read_bytes()
                )
                external_input_snapshot = (
                    transaction.external_inputs
                    if transaction is not None
                    else expected_external_input_snapshot
                )
                validation = captured_validation or (
                    self._validate_current_compose_candidate_unlocked(
                        environment_override=environment,
                        environment_snapshot=environment_snapshot,
                        external_input_snapshot=external_input_snapshot,
                    )
                )
                snapshots = validation.system_bind_snapshots
                if transaction is not None and snapshots != transaction.system_bind_snapshots:
                    raise ComposeCandidateContractError(
                        "compose candidate system bind snapshot differs from the transaction"
                    )
                if expected_system_bind_snapshots is not None:
                    if snapshots != expected_system_bind_snapshots:
                        raise ComposeCandidateContractError(
                            "compose candidate system bind snapshot differs from the request"
                        )
                    snapshots = expected_system_bind_snapshots
                if (
                    transaction is not None
                    and validation.raw_volume_graph_hash
                    != transaction.raw_volume_graph_hash
                ):
                    raise ComposeCandidateContractError(
                        "compose raw volume graph changed during the transaction"
                    )
                if (
                    transaction is not None
                    and validation.resolved_volume_graph_hash
                    != transaction.resolved_volume_graph_hash
                ):
                    raise ComposeCandidateContractError(
                        "compose resolved volume graph changed during the transaction"
                    )
                if (
                    expected_raw_volume_graph_hash is not None
                    and validation.raw_volume_graph_hash
                    != expected_raw_volume_graph_hash
                ):
                    raise ComposeCandidateContractError(
                        "compose raw volume graph changed during the request"
                    )
                if (
                    expected_resolved_volume_graph_hash is not None
                    and validation.resolved_volume_graph_hash
                    != expected_resolved_volume_graph_hash
                ):
                    raise ComposeCandidateContractError(
                        "compose resolved volume graph changed during the request"
                    )
                try:
                    source_unchanged = (
                        Path(environment_snapshot.compose_path).read_bytes()
                        == compose_source_bytes
                    )
                except OSError as exc:
                    raise ComposeCandidateContractError(
                        "compose candidate source cannot be revalidated"
                    ) from exc
                if not source_unchanged:
                    raise ComposeCandidateContractError(
                        "compose candidate source changed before Docker mutation"
                    )
                return self._run_unlocked(
                    args,
                    capture_output=capture_output,
                    environment=environment,
                    redact_config=redact_config,
                    expected_system_bind_snapshots=snapshots,
                    expected_compose_source_bytes=compose_source_bytes,
                    environment_snapshot=environment_snapshot,
                    external_input_snapshot=external_input_snapshot,
                    materialized_compose=validation.resolved,
                )
        return self._run_unlocked(
            args,
            capture_output=capture_output,
            environment=environment,
            redact_config=redact_config,
            expected_system_bind_snapshots=None,
            expected_compose_source_bytes=None,
            environment_snapshot=None,
            external_input_snapshot=None,
            materialized_compose=None,
            external=external,
        )

    def validate_compose_candidate_document(
        self,
        candidate: Mapping[str, Any],
        *,
        environment_override: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        """raw candidate와 Docker Compose resolved graph를 mutation 전에 검증한다."""

        return self.capture_compose_candidate_transaction(
            candidate,
            environment_override=environment_override,
        ).resolved

    def capture_compose_candidate_transaction(
        self,
        candidate: Mapping[str, Any],
        *,
        environment_override: Mapping[str, str] | None = None,
        environment_snapshot: ComposeEnvironmentSnapshot | None = None,
    ) -> ValidatedComposeCandidate:
        """mutex 안의 config transaction이 재검증할 candidate identity를 반환한다."""

        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, persisted = self.capture_transaction_unlocked(
                environment_override=environment_override,
                environment_snapshot=environment_snapshot,
            )
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            return self.capture_candidate_transaction_unlocked(
                candidate,
                baseline_transaction=transaction,
                baseline_validation=persisted,
                environment_override=environment_override,
            )

    def capture_candidate_transaction_unlocked(
        self,
        candidate: Mapping[str, Any],
        *,
        baseline_transaction: ComposeTransactionSnapshot,
        baseline_validation: ValidatedComposeCandidate,
        environment_override: Mapping[str, str] | None = None,
    ) -> ValidatedComposeCandidate:
        """GM-20: `capture_transaction_unlocked`와 같은 이유로 공개 승격했다.

        **선행조건**: `baseline_transaction`은 이미 host lock 아래에서 캡처된
        것이어야 한다(`capture_transaction_unlocked` 참고) — 이 메서드 자신은
        lock을 검증하지 않는다."""
        candidate_validation = self._validate_compose_candidate_document_unlocked(
            candidate,
            environment_override=environment_override,
            environment_snapshot=baseline_transaction.environment,
            external_input_snapshot=baseline_transaction.external_inputs,
        )
        if candidate_validation.raw_volume_graph_hash != baseline_validation.raw_volume_graph_hash:
            raise ComposeCandidateContractError(
                "compose candidate raw volume graph differs from persisted compose"
            )
        if (
            candidate_validation.resolved_volume_graph_hash
            != baseline_validation.resolved_volume_graph_hash
        ):
            raise ComposeCandidateContractError(
                "compose candidate resolved volume graph differs from persisted compose"
            )
        candidate_source_bytes = yaml.safe_dump(
            candidate,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ).encode()
        resolved = json.loads(
            _serialize_resolved_compose_document(candidate_validation.resolved)
        )
        if not isinstance(resolved, Mapping):
            raise ComposeCandidateContractError(
                "compose candidate resolved document is invalid"
            )
        candidate_transaction = ComposeTransactionSnapshot(
            environment=baseline_transaction.environment,
            external_inputs=baseline_transaction.external_inputs,
            compose_source_bytes=candidate_source_bytes,
            compose_source_mode=baseline_transaction.compose_source_mode,
            system_bind_snapshots=candidate_validation.system_bind_snapshots,
            raw_volume_graph_hash=candidate_validation.raw_volume_graph_hash,
            resolved_volume_graph_hash=(
                candidate_validation.resolved_volume_graph_hash
            ),
            resolved=resolved,
            resolved_document_hash=_resolved_compose_document_hash(resolved),
            manifest_path=baseline_transaction.manifest_path,
        )
        return replace(
            candidate_validation,
            transaction_snapshot=candidate_transaction,
        )

    def _validate_current_compose_candidate_unlocked(
        self,
        *,
        environment_override: Mapping[str, str] | None = None,
        environment_snapshot: ComposeEnvironmentSnapshot | None = None,
        external_input_snapshot: ComposeExternalInputSnapshot | None = None,
    ) -> ValidatedComposeCandidate:
        if environment_snapshot is None:
            environment_snapshot = _capture_compose_environment_snapshot(
                environment_override=environment_override,
            )
        compose_path = Path(environment_snapshot.compose_path)
        try:
            loaded = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise ComposeCandidateContractError(
                "compose candidate source cannot be loaded"
            ) from exc
        if not isinstance(loaded, Mapping):
            raise ComposeCandidateContractError(
                "compose candidate source is not a mapping"
            )
        return self._validate_compose_candidate_document_unlocked(
            loaded,
            environment_override=environment_override,
            environment_snapshot=environment_snapshot,
            external_input_snapshot=external_input_snapshot,
        )

    def _validate_compose_candidate_document_unlocked(
        self,
        candidate: Mapping[str, Any],
        *,
        environment_override: Mapping[str, str] | None,
        environment_snapshot: ComposeEnvironmentSnapshot | None = None,
        external_input_snapshot: ComposeExternalInputSnapshot | None = None,
    ) -> ValidatedComposeCandidate:
        if environment_snapshot is None:
            environment_snapshot = _capture_compose_environment_snapshot(
                environment_override=environment_override,
            )
        environment = _effective_snapshot_environment(
            environment_snapshot,
            environment_override,
        )
        if external_input_snapshot is None:
            external_input_snapshot = _capture_compose_external_input_snapshot(
                candidate,
                environment_snapshot=environment_snapshot,
                environment_override=environment_override,
            )
        else:
            _revalidate_compose_external_input_snapshot(
                external_input_snapshot,
                candidate=candidate,
                environment_snapshot=environment_snapshot,
                environment_override=environment_override,
            )
        raw_snapshots = validate_compose_candidate_protected_values(
            candidate,
            compose_path=environment_snapshot.compose_path,
            root_env_path=environment_snapshot.env_path,
            environment=environment,
            external_file_contents=_external_snapshot_contents(
                external_input_snapshot
            ),
        )

        try:
            override_path = Path(environment_snapshot.override_path)
            override_exists = override_path.exists()
        except (OSError, ValueError) as exc:
            raise ComposeCandidateContractError(
                "compose candidate override path cannot be resolved"
            ) from exc
        if override_exists:
            raise ComposeCandidateContractError(
                "compose candidate override file is not supported by the single-file boundary"
            )

        expected_snapshots = raw_snapshots

        resolved = self._resolve_compose_candidate_unlocked(
            candidate,
            environment=environment,
            expected_system_bind_snapshots=expected_snapshots,
            environment_snapshot=environment_snapshot,
            environment_override=environment_override,
            external_input_snapshot=external_input_snapshot,
        )
        resolved_snapshots = validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=environment,
            compose_path=environment_snapshot.compose_path,
            root_env_path=environment_snapshot.env_path,
        )
        if resolved_snapshots != expected_snapshots:
            raise ComposeCandidateContractError(
                "resolved compose system bind snapshot differs from raw compose"
            )
        return ValidatedComposeCandidate(
            resolved=resolved,
            system_bind_snapshots=resolved_snapshots,
            raw_volume_graph_hash=compose_volume_graph_hash(candidate),
            resolved_volume_graph_hash=compose_volume_graph_hash(resolved),
            environment_snapshot=environment_snapshot,
            external_input_snapshot=external_input_snapshot,
        )

    def _resolve_compose_candidate_unlocked(
        self,
        candidate: Mapping[str, Any],
        *,
        environment: Mapping[str, str],
        expected_system_bind_snapshots: tuple[
            CandidateSystemBindSnapshot, ...
        ],
        environment_snapshot: ComposeEnvironmentSnapshot,
        environment_override: Mapping[str, str] | None,
        external_input_snapshot: ComposeExternalInputSnapshot,
    ) -> Mapping[str, Any]:
        external_descriptors: tuple[int, ...] = ()
        try:
            compose_path = Path(environment_snapshot.compose_path)
            _revalidate_compose_external_input_snapshot(
                external_input_snapshot,
                candidate=candidate,
                environment_snapshot=environment_snapshot,
                environment_override=environment_override,
            )
            materialized_candidate, external_descriptors = _materialize_external_inputs_with_memfd(
                candidate,
                external_input_snapshot,
            )
            candidate_input = yaml.safe_dump(
                materialized_candidate,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
            command = ["docker", "compose"]
            command.extend(["--env-file", "/dev/null"])
            for profile in _FROZEN_COMPOSE_PROFILES:
                command.extend(["--profile", profile])
            command.extend(["--project-directory", str(compose_path.parent)])
            command.extend(["-f", "-"])
            command.extend(["config", "--format", "json"])
            revalidate_candidate_system_bind_snapshots(
                expected_system_bind_snapshots
            )
            try:
                completed = subprocess.run(
                    command,
                    cwd=get_project_root(),
                    text=True,
                    capture_output=True,
                    check=False,
                    env=dict(environment),
                    pass_fds=external_descriptors,
                    input=candidate_input,
                )
            except OSError as exc:
                raise ComposeCandidateContractError(
                    "compose candidate resolution could not start"
                ) from exc
            _revalidate_compose_external_input_snapshot(
                external_input_snapshot,
                candidate=candidate,
                environment_snapshot=environment_snapshot,
                environment_override=environment_override,
            )
            if completed.returncode != 0:
                raise ComposeCandidateContractError(
                    "compose candidate resolution failed"
                )
            try:
                resolved = json.loads(completed.stdout)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ComposeCandidateContractError(
                    "compose candidate resolution returned invalid JSON"
                ) from exc
            if not isinstance(resolved, Mapping):
                raise ComposeCandidateContractError(
                    "compose candidate resolution returned an invalid document"
                )
            _assert_resolved_external_inputs_materialized(resolved)
            return resolved
        except ComposeCandidateContractError:
            raise
        except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
            raise ComposeCandidateContractError(
                "compose candidate cannot be materialized"
            ) from exc
        finally:
            for descriptor in external_descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _run_unlocked(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        environment: Mapping[str, str] | None,
        redact_config: C6cDeploymentConfig | None,
        expected_system_bind_snapshots: tuple[
            CandidateSystemBindSnapshot, ...
        ] | None,
        expected_compose_source_bytes: bytes | None,
        environment_snapshot: ComposeEnvironmentSnapshot | None,
        external_input_snapshot: ComposeExternalInputSnapshot | None,
        materialized_compose: Mapping[str, Any] | None,
        external: ExternalProject | None = None,
    ) -> dict[str, Any]:
        if external is not None and (
            bool(self._compose_mutation_identifiers(args))
            or environment_snapshot is not None
            or materialized_compose is not None
            or expected_compose_source_bytes is not None
            or expected_system_bind_snapshots is not None
        ):
            # **마지막 그물.** `run()`의 guard를 고쳐도 변경 분기의 두 호출 지점은
            # `external`을 넘기지 않는 채 남는다 — 거기에 `assert`를 박으면 자리가
            # 둘이 되어 한쪽을 지워도 아무 검사가 빨개지지 않는다(S2에서 배운 것).
            # 그래서 여기 한 자리에서 거부한다.
            #
            # **술어가 `run()`과 같은 것을 봐야 한다.** 첫 판은 여기서 변경 *입력*
            # 넷만 봤는데, 변경 여부를 정하는 것은 **명령**이다 — 그래서
            # `_run_unlocked(["down","-v"], external=X)`가 형제 프로젝트의 볼륨을
            # 지웠다(적대 리뷰 2026-09-18 E-F2). 주석은 "가장 낮은 층이라 우회되지
            # 않는다"고 적었는데 술어가 두 벌이면 그 말이 성립하지 않는다.
            raise DeploymentContractError(
                "external compose projects cannot enter the Manager mutation "
                "machinery"
            )
        command = self.build_command(
            args,
            canonical_single_file=materialized_compose is not None,
            compose_path=(
                environment_snapshot.compose_path
                if environment_snapshot is not None
                else None
            ),
            external=external,
        )
        process_environment = None
        if environment_snapshot is not None:
            process_environment = dict(environment_snapshot.effective)
            if environment is not None:
                process_environment.update(environment)
        elif environment is not None:
            process_environment = {**os.environ, **environment}
        if expected_system_bind_snapshots is not None:
            revalidate_candidate_system_bind_snapshots(
                expected_system_bind_snapshots
            )
        if expected_compose_source_bytes is not None:
            self._revalidate_mutation_single_file_boundary(
                expected_compose_source_bytes,
                environment_snapshot=environment_snapshot,
                environment_override=environment,
                external_input_snapshot=external_input_snapshot,
            )
        process_input = None
        if materialized_compose is not None:
            transport_compose = _escape_materialized_compose_environment_values(
                materialized_compose
            )
            process_input = json.dumps(
                transport_compose,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        working_directory = get_project_root()
        if external is not None:
            # **`-f`는 `--project-directory`가 아니라 cwd 기준이다.** Manager 루트에서
            # 돌리면 `-f docker-compose.yml`이 Manager 자신의 compose를 연다(적대 리뷰
            # 2026-09-18 실측: `airport` 명령이 Manager의 concierge-ui 보간 오류를 냈다).
            working_directory = external.working_dir
            # 그리고 Manager의 프로세스 환경을 물려주지 않는다. Compose에서 셸 환경은
            # `.env`보다 **우선**하므로, 상속하면 형제 프로젝트의 `.env`를 조용히
            # 덮어쓴다 — `--env-file`을 뺀 것만으로는 그것을 막지 못했다.
            #
            # **좁히는 것은 상속되는 부분뿐이다.** 첫 판은 이 블록이
            # `if process_environment is None:` 안에 있어서, `environment` 인자가
            # 주어지면 그 앞의 `{**os.environ, **environment}`가 그대로 나갔다 —
            # 무조건문으로 적은 문서가 조건부였다(적대 리뷰 2026-09-18 F2). 명시 인자는
            # 호출자의 의도적 선택이므로 좁힌 것 **위에** 덮는다.
            inherited = {
                name: value
                for name, value in os.environ.items()
                if name in _EXTERNAL_PROJECT_PASSTHROUGH_ENV
                or name.startswith("DOCKER_")
            }
            process_environment = (
                inherited if environment is None else {**inherited, **environment}
            )
        try:
            completed = subprocess.run(
                command,
                cwd=working_directory,
                text=True,
                capture_output=capture_output,
                check=False,
                env=process_environment,
                input=process_input,
            )
        except OSError as exc:
            # 예외 이름조차 받지 않아서, `working_dir`이 없다는 가장 흔할 오설정이
            # "docker 바이너리 없음"과 **같은 문구**를 냈다(적대 리뷰 2026-09-18).
            # cwd가 호스트마다 다른 값이 된 지금은 그 구별이 진단의 전부다.
            return {
                "success": False,
                "returncode": 127,
                "command": command,
                "stdout": "",
                "stderr": (
                    f"docker compose command could not start in {working_directory}: {exc}"
                ),
            }

        stdout = completed.stdout if capture_output else ""
        stderr = completed.stderr if capture_output else ""
        if redact_config is not None:
            stdout = self._redact_c6c_output(stdout, redact_config)
            stderr = self._redact_c6c_output(stderr, redact_config)
        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "command": command,
            "stdout": stdout,
            "stderr": stderr,
        }

    def _revalidate_mutation_single_file_boundary(
        self,
        expected_source_bytes: bytes,
        *,
        environment_snapshot: ComposeEnvironmentSnapshot | None,
        environment_override: Mapping[str, str] | None,
        external_input_snapshot: ComposeExternalInputSnapshot | None,
    ) -> None:
        if environment_snapshot is None:
            raise ComposeCandidateContractError(
                "compose mutation has no frozen environment snapshot"
            )
        compose_path = Path(environment_snapshot.compose_path)
        try:
            source_bytes = compose_path.read_bytes()
            loaded = yaml.safe_load(source_bytes.decode("utf-8")) or {}
            override_exists = Path(environment_snapshot.override_path).exists()
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise ComposeCandidateContractError(
                "compose single-file mutation boundary cannot be revalidated"
            ) from exc
        if source_bytes != expected_source_bytes:
            raise ComposeCandidateContractError(
                "compose candidate source changed before Docker mutation"
            )
        if not isinstance(loaded, Mapping):
            raise ComposeCandidateContractError(
                "compose candidate source is not a mapping"
            )
        _revalidate_compose_environment_snapshot(environment_snapshot)
        if external_input_snapshot is None:
            raise ComposeCandidateContractError(
                "compose mutation has no frozen external input snapshot"
            )
        _revalidate_compose_external_input_snapshot(
            external_input_snapshot,
            candidate=loaded,
            environment_snapshot=environment_snapshot,
            environment_override=environment_override,
        )
        _assert_candidate_single_file_boundary(
            loaded,
            environment=_effective_snapshot_environment(
                environment_snapshot,
                environment_override,
            ),
        )
        if override_exists:
            raise ComposeCandidateContractError(
                "compose candidate override file appeared before Docker mutation"
            )

    @staticmethod
    def _compose_mutation_identifiers(args: Sequence[str]) -> list[str]:
        """Compose 명령을 read-only allowlist로 분류하고 mutation 대상을 보수적으로 찾는다."""

        runtime_identifiers = [*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE]
        if not args:
            return runtime_identifiers
        global_options_with_value = {
            "--ansi",
            "--env-file",
            "-f",
            "--file",
            "--parallel",
            "--profile",
            "--progress",
            "--project-directory",
            "-p",
            "--project-name",
        }
        global_flags = {
            "--all-resources",
            "--compatibility",
            "--dry-run",
            "--help",
            "--verbose",
            "--version",
        }
        command_index: int | None = None
        skip_next = False
        for index, item in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if item in global_options_with_value:
                if index + 1 >= len(args):
                    return runtime_identifiers
                skip_next = True
                continue
            inline_global_option = next(
                (
                    option
                    for option in global_options_with_value
                    if option.startswith("--")
                    and item.startswith(f"{option}=")
                ),
                None,
            )
            if inline_global_option is not None:
                if not item.partition("=")[2]:
                    return runtime_identifiers
                continue
            if item.startswith("-"):
                if item not in global_flags:
                    return runtime_identifiers
                continue
            command_index = index
            break
        if command_index is None:
            return runtime_identifiers
        command = args[command_index]
        if command == "config":
            read_options_with_value = {"--format", "--hash"}
            read_flags = {
                "--environment",
                "--images",
                "--no-consistency",
                "--no-interpolate",
                "--no-normalize",
                "--profiles",
                "-q",
                "--quiet",
                "--resolve-image-digests",
                "--services",
                "--variables",
                "--volumes",
            }
            config_items = list(args[command_index + 1 :])
            skip_next = False
            for index, item in enumerate(config_items):
                if skip_next:
                    skip_next = False
                    continue
                if (
                    item in {"-o", "--output"}
                    or item.startswith("--output=")
                    or (item.startswith("-o") and item != "-o")
                ):
                    return runtime_identifiers
                if item in read_options_with_value:
                    if index + 1 >= len(config_items):
                        return runtime_identifiers
                    skip_next = True
                    continue
                inline_read_option = next(
                    (
                        option
                        for option in read_options_with_value
                        if item.startswith(f"{option}=")
                    ),
                    None,
                )
                if inline_read_option is not None:
                    if not item.partition("=")[2]:
                        return runtime_identifiers
                    continue
                if item not in read_flags:
                    return runtime_identifiers
            return []
        read_only = {
            "events",
            "images",
            "logs",
            "ls",
            "port",
            "ps",
            "stats",
            "top",
            "version",
        }
        if command in read_only:
            return []
        if command == "wait":
            if any(
                item == "--down-project" or item.startswith("--down-project=")
                for item in args
            ):
                return runtime_identifiers
            wait_items = args[command_index + 1 :]
            if any(item.startswith("-") for item in wait_items):
                return runtime_identifiers
            return []
        mutation_commands = {
            "build",
            "cp",
            "create",
            "down",
            "exec",
            "kill",
            "pause",
            "pull",
            "push",
            "restart",
            "rm",
            "run",
            "scale",
            "start",
            "stop",
            "unpause",
            "up",
            "watch",
        }
        if command not in mutation_commands:
            return runtime_identifiers
        options_with_value = {
            "--attach",
            "--build-arg",
            "--change",
            "--env-file",
            "--env",
            "-e",
            "--entrypoint",
            "--exclude",
            "--index",
            "--label",
            "-l",
            "--name",
            "--no-attach",
            "--policy",
            "--timeout",
            "-t",
            "--user",
            "--volume",
            "-v",
            "--wait-timeout",
            "--workdir",
        }
        flag_options = {
            "--abort-on-container-exit",
            "--abort-on-container-failure",
            "--all",
            "--always-recreate-deps",
            "--attach-dependencies",
            "--build",
            "-d",
            "--detach",
            "--force",
            "--force-recreate",
            "--help",
            "--include-deps",
            "--menu",
            "--no-build",
            "--no-color",
            "--no-deps",
            "--no-log-prefix",
            "--no-recreate",
            "--no-start",
            "--no-TTY",
            "--privileged",
            "--quiet",
            "--remove-orphans",
            "--renew-anon-volumes",
            "-T",
            "--timestamps",
            "-V",
            "--wait",
            "-w",
            "--watch",
            "-y",
            "--yes",
        }
        command_options_with_value = {
            "create": {"--pull"},
            "kill": {"-s", "--signal"},
            "run": {"--pull"},
            "up": {"--pull"},
        }
        command_flags = {
            "build": {"--pull"},
            "rm": {"-f", "-s", "--stop"},
            "run": {"--rm"},
        }
        options_with_value.update(command_options_with_value.get(command, set()))
        flag_options.update(command_flags.get(command, set()))
        explicit_services: list[str] = []
        skip_next = False
        items = list(args[command_index + 1 :])
        for index, item in enumerate(items):
            # `docker compose run SERVICE COMMAND ...`의 SERVICE 뒤는
            # mutation 대상이 아닌 고정된 컨테이너 argv다. command token을
            # service identifier로 해석하면 frozen mutation scope가 불필요하게
            # 넓어지고, 경계 script 인자에 따라 allowlist가 흔들린다.
            if command == "run" and explicit_services:
                break
            if skip_next:
                skip_next = False
                continue
            if item == "--scale" and index + 1 < len(items):
                service = items[index + 1].partition("=")[0]
                if not service:
                    return runtime_identifiers
                explicit_services.append(service)
                skip_next = True
                continue
            if item == "--scale":
                return runtime_identifiers
            if item.startswith("--scale="):
                service = item.removeprefix("--scale=").partition("=")[0]
                if not service:
                    return runtime_identifiers
                explicit_services.append(service)
                continue
            if command == "scale" and "=" in item and not item.startswith("-"):
                explicit_services.append(item.partition("=")[0])
                continue
            if item in options_with_value:
                if index + 1 >= len(items):
                    return runtime_identifiers
                skip_next = True
                continue
            inline_value_option = next(
                (
                    option
                    for option in options_with_value
                    if option.startswith("--")
                    and item.startswith(f"{option}=")
                ),
                None,
            )
            if inline_value_option is not None:
                if not item.partition("=")[2]:
                    return runtime_identifiers
                continue
            if item.startswith("-"):
                if item not in flag_options:
                    return runtime_identifiers
                continue
            explicit_services.append(item)
        if explicit_services:
            explicit_services.extend(
                item.partition(":")[0]
                for item in tuple(explicit_services)
                if ":" in item
            )
            if command in {"up", "create", "restart", "watch"} and "--no-deps" not in args:
                api_dependencies = {
                    "kor-travel-map-ui": "kor-travel-map-api",
                    "kor-travel-map-dagster": "kor-travel-map-api",
                    "kor-travel-map-dagster-daemon": "kor-travel-map-api",
                    "pinvi-web": "pinvi-api",
                    "pinvi-dagster": "pinvi-api",
                }
                explicit_services.extend(
                    api_dependencies[service]
                    for service in tuple(explicit_services)
                    if service in api_dependencies
                )
            if "--remove-orphans" in args:
                explicit_services.extend(runtime_identifiers)
            return explicit_services
        # down/rm --all/unknown command/option parse failure may affect either API.
        return runtime_identifiers

    def ensure_target(
        self,
        target: str,
        *,
        build: bool = False,
        recreate: bool = False,
        capture_output: bool = True,
    ) -> dict[str, Any]:
        target_sequence = target_sequence_for_target(target)
        if target_is_external(target):
            # `ensure`는 Manager의 C6c 계약 기계(보호값 스캔·볼륨 그래프·단일파일
            # 경계·핀셋)를 통과한 **Manager 자신의** 후보를 전제한다. 형제 프로젝트의
            # compose는 그 계약을 받은 적이 없고, 정본도 이 저장소가 아니다.
            # 수명주기는 `control_container`(Docker SDK)로 다루고, 배포는 각 저장소가
            # 계속 소유한다.
            raise DeploymentContractError(
                f"target '{target}' belongs to an external compose project; "
                "ensure is only for the Manager's own project — use container "
                "start/stop/restart, or deploy from that project's repository"
            )
        services = services_for_target(target)
        preflight_environment = _capture_compose_environment_snapshot(
            environment_override=None,
        )
        preflight_mode = assert_manager_mutation_allowed(
            environment=preflight_environment.effective
        )
        if preflight_mode == "production":
            raise DeploymentContractError(
                "production ensure is not permitted; "
                "manage this service directly on the host instead"
            )
        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, validation = self.capture_transaction_unlocked()
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            mode = assert_manager_mutation_allowed(
                environment=transaction.environment.effective
            )
            if mode == "production":
                raise DeploymentContractError(
                    "production ensure is not permitted; "
                    "manage this service directly on the host instead"
                )
            compose_path = Path(transaction.environment.compose_path)
            try:
                baseline_unchanged = (
                    compose_path.read_bytes() == transaction.compose_source_bytes
                    and compose_path.stat().st_mode & 0o777
                    == transaction.compose_source_mode
                )
            except OSError as exc:
                raise ComposeCandidateContractError(
                    "compose baseline cannot be revalidated for ensure"
                ) from exc
            if not baseline_unchanged:
                raise ComposeCandidateContractError(
                    "compose baseline changed before ensure mutation"
                )
            return self._ensure_target_unlocked(
                target,
                target_sequence=target_sequence,
                services=services,
                build=build,
                recreate=recreate,
                capture_output=capture_output,
                expected_system_bind_snapshots=validation.system_bind_snapshots,
                expected_raw_volume_graph_hash=validation.raw_volume_graph_hash,
                expected_resolved_volume_graph_hash=(
                    validation.resolved_volume_graph_hash
                ),
                original_compose_bytes=transaction.compose_source_bytes,
                original_compose_mode=transaction.compose_source_mode,
                expected_environment_snapshot=transaction.environment,
                expected_external_input_snapshot=(
                    transaction.external_inputs
                ),
                transaction=transaction,
            )

    def _ensure_target_unlocked(
        self,
        target: str,
        *,
        target_sequence: list[str],
        services: list[str],
        build: bool,
        recreate: bool,
        capture_output: bool,
        expected_system_bind_snapshots: tuple[
            CandidateSystemBindSnapshot, ...
        ],
        expected_raw_volume_graph_hash: str,
        expected_resolved_volume_graph_hash: str,
        original_compose_bytes: bytes,
        original_compose_mode: int,
        expected_environment_snapshot: ComposeEnvironmentSnapshot,
        expected_external_input_snapshot: ComposeExternalInputSnapshot | None,
        transaction: ComposeTransactionSnapshot,
    ) -> dict[str, Any]:
        init_steps = init_steps_for_target(target)
        commands: list[list[str]] = []
        init_results: list[dict[str, Any]] = []

        result: dict[str, Any] = {
            "success": True,
            "returncode": 0,
            "target": target,
            "target_sequence": target_sequence,
            "services": services,
            "init_results": init_results,
            "command": [],
            "stdout": "",
            "stderr": "",
        }

        mutation_succeeded = False
        try:
            if services:
                args = ["up", "-d"]
                if build:
                    args.append("--build")
                if recreate:
                    args.append("--force-recreate")
                args.extend(services)
                up_result = self.run(
                    args,
                    capture_output=capture_output,
                    mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                    expected_system_bind_snapshots=expected_system_bind_snapshots,
                    expected_raw_volume_graph_hash=expected_raw_volume_graph_hash,
                    expected_resolved_volume_graph_hash=(
                        expected_resolved_volume_graph_hash
                    ),
                    expected_environment_snapshot=expected_environment_snapshot,
                    expected_external_input_snapshot=(
                        expected_external_input_snapshot
                    ),
                    transaction=transaction,
                )
                commands.append(up_result["command"])
                result["stdout"] += up_result.get("stdout", "")
                result["stderr"] += up_result.get("stderr", "")
                result["returncode"] = up_result["returncode"]
                result["success"] = up_result["success"]
                if not up_result["success"]:
                    result["command"] = commands
                    return result
                mutation_succeeded = True

            for step in init_steps:
                step_command = step.get("command", [])
                step_result = self.run(
                    step_command,
                    capture_output=capture_output,
                    mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                    expected_system_bind_snapshots=expected_system_bind_snapshots,
                    expected_raw_volume_graph_hash=expected_raw_volume_graph_hash,
                    expected_resolved_volume_graph_hash=(
                        expected_resolved_volume_graph_hash
                    ),
                    expected_environment_snapshot=expected_environment_snapshot,
                    expected_external_input_snapshot=(
                        expected_external_input_snapshot
                    ),
                    transaction=transaction,
                )
                step_result = {
                    "target": step.get("target"),
                    "name": step.get("name"),
                    "description": step.get("description"),
                    **step_result,
                }
                init_results.append(step_result)
                commands.append(step_result["command"])
                result["stdout"] += step_result.get("stdout", "")
                result["stderr"] += step_result.get("stderr", "")
                if not step_result["success"]:
                    result["success"] = False
                    result["returncode"] = step_result["returncode"]
                    result["command"] = commands
                    return result
                mutation_succeeded = True
        except ComposeCandidateContractError as exc:
            if not mutation_succeeded:
                raise
            recovery = self._recover_persisted_target_runtime(
                services,
                capture_output=capture_output,
                original_compose_bytes=original_compose_bytes,
                original_compose_mode=original_compose_mode,
                expected_system_bind_snapshots=expected_system_bind_snapshots,
                expected_raw_volume_graph_hash=expected_raw_volume_graph_hash,
                expected_resolved_volume_graph_hash=(
                    expected_resolved_volume_graph_hash
                ),
                expected_environment_snapshot=expected_environment_snapshot,
                expected_external_input_snapshot=expected_external_input_snapshot,
                transaction=transaction,
            )
            raise ComposePostMutationContractError(
                exc,
                recovery_attempted=True,
                recovery_succeeded=bool(recovery.get("success")),
                recovery_error=(
                    None if recovery.get("success") else str(recovery.get("error"))
                ),
                restoration=recovery,
            ) from exc

        result["command"] = commands
        return result

    def _recover_persisted_target_runtime(
        self,
        services: list[str],
        *,
        capture_output: bool,
        original_compose_bytes: bytes,
        original_compose_mode: int,
        expected_system_bind_snapshots: tuple[
            CandidateSystemBindSnapshot, ...
        ],
        expected_raw_volume_graph_hash: str,
        expected_resolved_volume_graph_hash: str,
        expected_environment_snapshot: ComposeEnvironmentSnapshot,
        expected_external_input_snapshot: ComposeExternalInputSnapshot | None,
        transaction: ComposeTransactionSnapshot,
    ) -> dict[str, Any]:
        compose_path = Path(expected_environment_snapshot.compose_path)
        baseline = {
            "raw_volume_graph_hash": expected_raw_volume_graph_hash,
            "resolved_volume_graph_hash": expected_resolved_volume_graph_hash,
            "system_bind_snapshots": len(expected_system_bind_snapshots),
        }
        try:
            _atomic_restore_compose_source(
                compose_path,
                original_compose_bytes,
                mode=original_compose_mode,
            )
        except Exception as exc:
            return {
                "success": False,
                "recovery_attempted": True,
                "config_restored": False,
                "contract_revalidated": False,
                "runtime_recovery_attempted": False,
                "baseline": baseline,
                "error": str(exc),
            }
        try:
            self._validate_frozen_transaction_unlocked(transaction)
            if transaction.system_bind_snapshots != expected_system_bind_snapshots:
                raise ComposeCandidateContractError(
                    "restored compose system bind snapshot differs from baseline"
                )
            if transaction.raw_volume_graph_hash != expected_raw_volume_graph_hash:
                raise ComposeCandidateContractError(
                    "restored compose raw volume graph differs from baseline"
                )
            if transaction.resolved_volume_graph_hash != expected_resolved_volume_graph_hash:
                raise ComposeCandidateContractError(
                    "restored compose resolved volume graph differs from baseline"
                )
            if (
                transaction.compose_source_bytes != original_compose_bytes
                or transaction.compose_source_mode != original_compose_mode
            ):
                raise ComposeCandidateContractError(
                    "frozen recovery transaction differs from baseline"
                )
        except Exception as exc:
            return {
                "success": False,
                "recovery_attempted": True,
                "config_restored": True,
                "contract_revalidated": False,
                "runtime_recovery_attempted": False,
                "baseline": baseline,
                "error": str(exc),
            }
        if not services:
            return {
                "success": True,
                "recovery_attempted": True,
                "config_restored": True,
                "contract_revalidated": True,
                "runtime_recovery_attempted": False,
                "baseline": baseline,
                "error": None,
            }
        try:
            recovery = self._run_frozen_recovery(
                ["up", "-d", "--force-recreate", *services],
                capture_output=capture_output,
                mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                transaction=transaction,
            )
        except Exception as exc:
            return {
                "success": False,
                "recovery_attempted": True,
                "config_restored": True,
                "contract_revalidated": True,
                "runtime_recovery_attempted": True,
                "baseline": baseline,
                "error": str(exc),
            }
        return {
            **recovery,
            "recovery_attempted": True,
            "config_restored": True,
            "contract_revalidated": True,
            "runtime_recovery_attempted": True,
            "baseline": baseline,
            "error": None if recovery.get("success") else (
                recovery.get("stderr") or recovery.get("stdout") or "recovery failed"
            ),
        }

    def _run_pinned_runtime_rebuild_compose(
        self,
        args: Sequence[str],
        *,
        transaction: ComposeTransactionSnapshot,
        capture_output: bool = True,
        allow_typed_error_diagnostic: bool = True,
    ) -> dict[str, Any]:
        compose_action = self._pinned_runtime_compose_action(args)
        if compose_action in {"run", "up"} and "--no-deps" not in args:
            raise DeploymentContractError(
                "pinned runtime rebuild Compose startup requires --no-deps"
            )
        # Compose turns a multi-target build into one BuildKit bake request.  On
        # the small n150 host that request opens several frontend sessions at
        # once; a second build (for example an unrelated tvnm05 build) can then
        # exhaust the daemon's single-session limit and leave every target
        # waiting until its context deadline.  Keep the frozen transaction and
        # provenance checks identical, but give each candidate service its own
        # BuildKit request so a target completes before the next one starts.
        if tuple(args) == (
            "build",
            *COMPOSE_BUILT_RUNTIME_SERVICES,
        ):
            build_result: dict[str, Any] = {}
            for service in COMPOSE_BUILT_RUNTIME_SERVICES:
                try:
                    build_result = self._run_pinned_runtime_rebuild_compose(
                        ["build", service],
                        transaction=transaction,
                        capture_output=capture_output,
                        allow_typed_error_diagnostic=allow_typed_error_diagnostic,
                    )
                except DeploymentContractError as exc:
                    # fan-out은 여기 한 곳에만 있다. 실패한 서비스를 여기서 실어
                    # 보내지 않으면 봉인이 result를 `candidate_compose_build`까지만
                    # 말하게 만들고, 다음 실행이 넷 중 어느 것인지 모른 채 같은
                    # 30분을 다시 쓴다(적대 리뷰 MINOR-10). 값은 이 고정 목록에서만
                    # 나오므로 자유 문자열을 여는 것이 아니다.
                    setattr(exc, _PINNED_RUNTIME_COMPOSE_SERVICE_MARK, service)
                    raise
            return build_result
        result = self._run_frozen_recovery(
            args,
            transaction=transaction,
            mutation_capability=_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
            capture_output=capture_output,
        )
        if result["success"]:
            return result
        diagnostic = (
            self._pinned_runtime_compose_failure_diagnostic(args, result)
            if allow_typed_error_diagnostic
            else _ComposeFailureDiagnostic(message_suffix="")
        )
        raise PinnedRuntimeComposeFailure(
            "pinned runtime rebuild Compose "
            f"{compose_action} command failed "
            f"(exit {result['returncode']}{diagnostic.message_suffix})",
            pinvi_role_diagnostic=diagnostic.pinvi_role_code,
        )

    @staticmethod
    def _pinned_runtime_compose_action(args: Sequence[str]) -> str:
        return next(
            (
                argument
                for argument in args
                if argument in {"build", "stop", "rm", "ps", "up", "run"}
            ),
            "unknown",
        )

    @staticmethod
    def _pinned_runtime_compose_failure_diagnostic(
        args: Sequence[str],
        result: Mapping[str, Any],
    ) -> _ComposeFailureDiagnostic:
        """허용된 one-shot typed error만 원문 없이 F1D 오류에 붙인다.

        ``pinvi_role_code``는 pinvi_role 대상일 때만 채운다 — 그 값이
        ``_pinvi_lifecycle_diagnostic``이 메시지를 재파싱하지 않고 바로 쓰는
        구조화된 판정 결과다.
        """

        compose_action = ComposeService._pinned_runtime_compose_action(args)
        if compose_action != "run":
            return _ComposeFailureDiagnostic(message_suffix="")
        target = args[-1] if args else ""
        for stream_name in ("stderr", "stdout"):
            output = result.get(stream_name)
            if not isinstance(output, str):
                continue
            for line in output.splitlines():
                candidates: tuple[str, ...] = (line,)
                prefixed = _compose_prefixed_typed_error_candidate(line, target=target)
                if prefixed is not None:
                    candidates += (prefixed,)
                for candidate in candidates:
                    try:
                        payload = json.loads(
                            candidate,
                            object_pairs_hook=_json_object_without_duplicate_keys,
                        )
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(payload, Mapping):
                        continue
                    if target == "kor-travel-map-dagster-storage-migrate":
                        code = payload.get("code")
                        if (
                            set(payload) == {"code", "schema"}
                            and payload.get("schema")
                            == _MAP_DAGSTER_STORAGE_MIGRATION_ERROR_SCHEMA
                            and isinstance(code, str)
                            and _MAP_DAGSTER_STORAGE_MIGRATION_ERROR_CODE.fullmatch(code)
                            is not None
                        ):
                            return _ComposeFailureDiagnostic(message_suffix=f"; {code}")
                        continue
                    if target == "pinvi-admin-bootstrap":
                        code = payload.get("error_code")
                        phase = payload.get("phase")
                        if (
                            set(payload) == {"error_code", "phase"}
                            and isinstance(code, str)
                            and isinstance(phase, str)
                            and _PINVI_ADMIN_BOOTSTRAP_ERROR_PHASE_BY_CODE.get(code)
                            == phase
                        ):
                            # M05 폐기로 role 코드 공간이 사라져 이제 이 속성을
                            # 쓰는 것은 admin-bootstrap 하나뿐이다.
                            return _ComposeFailureDiagnostic(
                                message_suffix=f"; pinvi:{code}",
                                pinvi_role_code=code,
                            )
        return _ComposeFailureDiagnostic(message_suffix="")

    def _retire_pinned_runtime_oneshot_writers(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        """reset 전 frozen project one-shot writer를 제거하고 부재를 증명한다.

        `docker compose run --rm`의 Manager process가 강제 종료되면 Docker
        container가 계속 DB에 연결할 수 있다. 동일 frozen project/service label로만
        stop+remove한 뒤 `ps --all`에서 exact seven service가 사라진 것을 확인한다.
        어느 단계라도 불명확하면 DB reset 전에 fail-close한다.
        """

        self._run_pinned_runtime_rebuild_compose(
            [
                "--profile",
                "bootstrap",
                "rm",
                "-f",
                "-s",
                *_PINNED_RUNTIME_ONESHOT_WRITERS,
            ],
            transaction=transaction,
        )
        inspection = self._run_pinned_runtime_rebuild_compose(
            [
                "--profile",
                "bootstrap",
                "ps",
                "--all",
                "--format",
                "json",
                *_PINNED_RUNTIME_ONESHOT_WRITERS,
            ],
            transaction=transaction,
        )
        records = self._compose_ps_records(
            str(inspection.get("stdout", "")),
            allow_empty=True,
        )
        if records:
            raise DeploymentContractError(
                "pinned runtime one-shot writer remained after forced removal"
            )

    def _verify_pinned_runtime_pinvi_bootstrap_settings(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        """candidate PinVi가 production Settings를 import할 수 있는지 reset 전에 확인한다.

        credential 파일·DB 의존성 없이 `head`만 실행한다. 따라서 Map/PinVi DB의
        reset과 journal durable write보다 반드시 앞선 fail-close gate다.
        """

        self._run_pinned_runtime_rebuild_compose(
            [
                "--profile",
                "bootstrap",
                "run",
                "--rm",
                "--no-deps",
                "pinvi-admin-bootstrap",
                "pinvi-admin-bootstrap",
                "head",
            ],
            transaction=transaction,
        )

    @staticmethod
    def _ensure_pinvi_fresh_migration_fence(*, values: Mapping[str, str]) -> None:
        """0101 migration이 요구하는 catalog-lock fence 함수를 매 rebuild마다 세운다.

        `pinvi_internal.acquire_fresh_0101_database_fence()`는 `pg_authid`/
        `pg_database`를 ACCESS EXCLUSIVE로 잠근다 -- scoped app role이 자기
        database의 owner라도 Postgres는 이 권한을 owner에게 주지 않는다(catalog
        전역이지 database 소유물이 아니다). M05 다중 role 모델을 폐기하며 이
        함수를 세우던 `bootstrap-pinvi-runtime-role.sh`도 함께 버렸는데, 이 한
        함수만은 role 분리와 무관하게 fresh install마다 여전히 필요하다.
        Destructive reset이 매 rebuild마다 `pinvi_internal` schema를 지우므로
        한 번이 아니라 매번 다시 세운다.
        """

        app_role = values["PINVI_APP_DB_USER"]
        bootstrap_owner = values.get("KOR_TRAVEL_SHARED_POSTGRES_USER", "shared_admin")
        if _ROLE_IDENTIFIER.fullmatch(app_role) is None:
            raise DeploymentContractError("PinVi app role name is invalid")
        if _ROLE_IDENTIFIER.fullmatch(bootstrap_owner) is None:
            raise DeploymentContractError("PinVi bootstrap owner name is invalid")
        container = values.get(
            "KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", "kor-travel-shared-postgres"
        )
        port = values.get("KOR_TRAVEL_SHARED_DB_PORT", "11000")
        database = values.get("PINVI_POSTGRES_DB", "pinvi")
        script = (
            f'CREATE SCHEMA IF NOT EXISTS pinvi_internal AUTHORIZATION "{app_role}";\n'
            'REVOKE ALL ON SCHEMA pinvi_internal FROM PUBLIC;\n'
            f'GRANT USAGE ON SCHEMA pinvi_internal TO "{app_role}";\n'
            "CREATE OR REPLACE FUNCTION pinvi_internal.acquire_fresh_0101_database_fence()\n"
            "RETURNS void\n"
            "LANGUAGE plpgsql\n"
            "SECURITY DEFINER\n"
            "SET search_path = pg_catalog\n"
            "AS $pinvi_fresh_0101_fence$\n"
            "BEGIN\n"
            "    LOCK TABLE pg_catalog.pg_database IN ACCESS EXCLUSIVE MODE;\n"
            "    LOCK TABLE pg_catalog.pg_authid, pg_catalog.pg_auth_members,\n"
            "              pg_catalog.pg_db_role_setting IN ACCESS EXCLUSIVE MODE;\n"
            "END\n"
            "$pinvi_fresh_0101_fence$;\n"
            "ALTER FUNCTION pinvi_internal.acquire_fresh_0101_database_fence() "
            f'OWNER TO "{bootstrap_owner}";\n'
            "REVOKE ALL ON FUNCTION pinvi_internal.acquire_fresh_0101_database_fence() "
            "FROM PUBLIC;\n"
            "GRANT EXECUTE ON FUNCTION pinvi_internal.acquire_fresh_0101_database_fence() "
            f'TO "{app_role}";\n'
        )
        try:
            completed = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    "--user",
                    "postgres",
                    container,
                    "psql",
                    "--no-psqlrc",
                    "--set=ON_ERROR_STOP=1",
                    "--port",
                    port,
                    "--username",
                    bootstrap_owner,
                    "--dbname",
                    database,
                ],
                input=script.encode("utf-8"),
                cwd="/",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                "PinVi fresh migration fence could not be established"
            ) from exc
        if completed.returncode != 0:
            raise DeploymentContractError(
                "PinVi fresh migration fence could not be established"
            )

    def _run_pinvi_admin_bootstrap(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
        state_paths: PinnedRuntimeStatePaths,
        values: Mapping[str, str],
        transaction_id: str,
    ) -> None:
        """PinVi admin bootstrap을 credential file 하나로 한 번 실행한다.

        종전에는 이 자리가 migrator login을 열고(`PINVI_MIGRATOR_DISABLE_LOGIN=0`)
        bootstrap 뒤 반드시 다시 봉인하는 140줄짜리 choreography였다. PinVi의 다중
        role 모델(M05)을 폐기하고 geo 패턴(scoped app role 하나가 자기 database를
        소유)으로 접으면서 열고 닫을 창 자체가 없어졌다 — role이 자기 database의
        owner라 DDL 권한을 상시 갖는다. 실패 분류도 함께 사라진다: open/seal 두
        지점이 없으니 `_PinviRoleLifecycleError`로 감쌀 단계가 남지 않는다.
        """

        with pinvi_bootstrap_credential_file(
            state_paths=state_paths,
            values=values,
            transaction_id=transaction_id,
            email=values["KTDM_C6C_PINVI_ADMIN_EMAIL"],
            password=values["KTDM_C6C_PINVI_ADMIN_PASSWORD"],
        ) as credential:
            self._run_pinned_runtime_rebuild_compose(
                [
                    "--profile",
                    "bootstrap",
                    "run",
                    "--rm",
                    "--no-deps",
                    "-v",
                    f"{credential.path}:/run/pinvi/bootstrap-admin.json:ro",
                    "-e",
                    "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE=/run/pinvi/bootstrap-admin.json",
                    _PINVI_ADMIN_BOOTSTRAP_SERVICE,
                ],
                transaction=transaction,
            )

    @staticmethod
    def _inspect_image_reference_id(image_reference: str, *, label: str) -> str:
        try:
            completed = subprocess.run(
                ["docker", "image", "inspect", "--format={{.Id}}", image_reference],
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise DeploymentContractError(
                f"cannot inspect {label} candidate image ID"
            ) from exc
        image_id = completed.stdout.strip()
        if completed.returncode != 0 or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            raise DeploymentContractError(
                f"{label} candidate image ID is not immutable"
            )
        return image_id

    @staticmethod
    def _inspect_container_image_id(container_name: str, *, label: str) -> str:
        try:
            completed = subprocess.run(
                ["docker", "inspect", "--format={{.Image}}", container_name],
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise DeploymentContractError(
                f"cannot inspect {label} runtime image ID"
            ) from exc
        image_id = completed.stdout.strip()
        if completed.returncode != 0 or re.fullmatch(
            r"sha256:[0-9a-f]{64}", image_id
        ) is None:
            raise DeploymentContractError(
                f"{label} runtime image ID is not immutable"
            )
        return image_id

    @staticmethod
    def _redact_c6c_output(text: str, config: C6cDeploymentConfig) -> str:
        credentials = (
            config.read_token,
            config.cancel_token,
            config.fixture_token,
            config.map_ui_password_hash,
            config.map_ui_session_secret,
            config.map_admin_proxy_secret,
            config.map_service_token,
            config.map_cursor_signing_secret,
            config.feature_create_token,
            config.feature_create_token_digest,
            config.smoke.map_ui_password,
            config.smoke.pinvi_admin_email,
            config.smoke.pinvi_admin_password,
            config.contract_generation,
        )
        redacted = text
        for credential in sorted(
            (value for value in credentials if value),
            key=lambda value: (-len(value), value),
        ):
            redacted = redacted.replace(credential, "<redacted>")
        return redacted

    def _assert_pinned_runtime_container_images(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        expected_images: Mapping[str, str],
    ) -> None:
        """실행 중인 slot·companion container를 배포한 exact image에 결박한다.

        companion은 자기 slot이 없으므로 owner slot의 이미지가 기대값이다
        (`_deployed_images`).
        """

        if len(records) != len(expected_images):
            raise DeploymentContractError(
                "pinned runtime container image evidence is incomplete"
            )
        observed_services: set[str] = set()
        for record in records:
            service = record.get("Service")
            container_name = record.get("Name")
            if (
                not isinstance(service, str)
                or service not in expected_images
                or service in observed_services
                or not isinstance(container_name, str)
                or not container_name
            ):
                raise DeploymentContractError(
                    "pinned runtime container image evidence is invalid"
                )
            observed_services.add(service)
            observed_image = self._inspect_container_image_id(
                container_name,
                label=service,
            )
            if observed_image != expected_images[service]:
                raise DeploymentContractError(
                    f"{service} runtime image differs from committed generation"
                )
        if observed_services != set(expected_images):
            raise DeploymentContractError(
                "pinned runtime container image evidence is incomplete"
            )

    def _attest_pinned_runtime_candidate_images(
        self,
        *,
        build: CandidateRuntimeBuild,
        map_candidate: MapApplicationCandidate,
    ) -> dict[RuntimeService, str]:
        built_image_ids = {
            service: self._inspect_image_reference_id(
                build.image_names[service],
                label=service,
            )
            for service in COMPOSE_BUILT_RUNTIME_SERVICES
        }
        map_revision = build.sources.release.source_for("map").revision
        pinvi_revision = build.sources.release.source_for("pinvi").revision
        for service in COMPOSE_BUILT_RUNTIME_SERVICES:
            expected_revision = map_revision if service.startswith("kor-travel-map-") else pinvi_revision
            observed_revision = self._inspect_image_source_revision(
                built_image_ids[service],
                label=service,
                expected_build_environment=("production" if service.startswith("pinvi-") else None),
            )
            if observed_revision != expected_revision:
                raise DeploymentContractError(
                    f"{service} candidate image revision differs from the release pin"
                )
        image_ids: dict[RuntimeService, str] = {
            "kor-travel-map-api": map_candidate.api_image_id,
            "kor-travel-map-ui": built_image_ids["kor-travel-map-ui"],
            "kor-travel-map-dagster": map_candidate.dagster_image_id,
            "kor-travel-map-dagster-daemon": map_candidate.dagster_image_id,
            "pinvi-api": built_image_ids["pinvi-api"],
            "pinvi-web": built_image_ids["pinvi-web"],
            "pinvi-dagster": built_image_ids["pinvi-dagster"],
        }
        return image_ids

    @staticmethod
    def _validate_pinned_runtime_candidate_build_contract(
        transaction: ComposeTransactionSnapshot,
        *,
        build: CandidateRuntimeBuild,
        environment_override: Mapping[str, str] | None = None,
    ) -> None:
        """candidate build 전 frozen Compose와 staged source 경계를 함께 고정한다."""

        try:
            source = yaml.safe_load(transaction.compose_source_bytes.decode("utf-8")) or {}
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise DeploymentContractError(
                "pinned runtime candidate compose source is invalid"
            ) from exc
        if not isinstance(source, Mapping):
            raise DeploymentContractError(
                "pinned runtime candidate compose source is invalid"
            )
        if isinstance(transaction, ComposeTransactionSnapshot):
            source_environment = dict(transaction.environment.effective)
            if environment_override is not None:
                source_environment.update(environment_override)
            _map_source_environment_contract_version(
                source_environment,
                compose_path=transaction.environment.compose_path,
                source_revision=build.sources.release.source_for("map").revision,
            )
        validate_c6c_build_source_wiring(source)
        map_context = str(build.sources.source_for("map").root)
        pinvi_context = str(build.sources.source_for("pinvi").root)
        validate_resolved_c6c_build_provenance(
            transaction.resolved,
            C6cBuildProvenance(
                map_source_revision=build.sources.release.source_for("map").revision,
                pinvi_source_revision=build.sources.release.source_for("pinvi").revision,
            ),
            expected_build_contexts={
                "kor-travel-map-ui": map_context,
                "pinvi-api": pinvi_context,
                "pinvi-web": pinvi_context,
                "pinvi-dagster": pinvi_context,
            },
        )

    @staticmethod
    def _pinned_runtime_result(
        status: DeployStatus,
        *,
        candidate: PinnedRuntimeGeneration,
        outcome: str,
        warnings: Sequence[str],
    ) -> dict[str, Any]:
        """launcher·chain17이 읽는 결과. 키는 종전과 같다(``success``·``phase``·
        ``pinset_sha256``·``schema_heads``) — ``phase``는 이제 ``committed`` 하나다."""

        return {
            "success": True,
            "returncode": 0,
            "resumed": False,
            "outcome": outcome,
            "transaction_id": status.run_id,
            "phase": "committed",
            "generation_sha256": generation_logical_sha256(candidate),
            "pinset_sha256": candidate.pinset_sha256,
            "schema_heads": dict(status.schema_heads),
            "warnings": list(warnings),
        }

    @staticmethod
    def _observe_deployed_databases(
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
    ) -> dict[DatabaseRole, DeployedDatabase] | None:
        """세 DB의 identity. 하나라도 없으면 ``None``."""

        observed: dict[DatabaseRole, DeployedDatabase] = {}
        for runtime in runtimes:
            identity = read_database_identity(runtime)
            if identity is None:
                return None
            observed[runtime.role] = DeployedDatabase(*identity)
        return observed

    @staticmethod
    def _observe_schema_heads(
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
    ) -> dict[str, str] | None:
        """세 DB의 Alembic head. 하나라도 읽을 수 없으면 ``None``."""

        try:
            return {
                runtime.role: read_database_schema_revision(runtime) for runtime in runtimes
            }
        except DeploymentContractError:
            return None

    @staticmethod
    def _deployed_images(
        candidate: PinnedRuntimeGeneration,
        companions: Mapping[str, RuntimeService],
    ) -> dict[str, str]:
        """slot은 자기 이미지, companion은 owner slot의 이미지다."""

        slot_images = candidate.image_ids
        images = {str(service): image for service, image in slot_images.items()}
        images.update((name, slot_images[owner]) for name, owner in companions.items())
        return images

    def rebuild_pinned_runtime(
        self,
        *,
        restart_reason: str | None = None,
        adopt_reason: str | None = None,
    ) -> dict[str, Any]:
        """핀된 Map·PinVi pair를 **마이그레이션 전진**으로 배포한다(ADR-51).

        DB는 배포를 넘어 보존된다. 같은 pair를 다시 돌리면 빌드 없이 수렴만 하고, 새
        pair는 멱등 one-shot으로 head까지 올린다. DB를 지우는 길은 ``restart_reason``을
        준 명시적 ``--restart`` 하나다. ``adopt_reason``(``--adopt-live-databases``)은
        지금 떠 있는 DB를 지우지 않고 새 identity 기준으로 받아들인다 — 백업 복원처럼
        비파괴로 DB가 바뀌었을 때 ``--restart`` 말고 빠져나갈 길이다.

        얇은 래퍼는 한 가지만 한다 — 실패에 "이 실행이 배포 상태를 ``in_progress``로
        바꿨는가"를 붙인다. launcher는 그 분류로 claim 해제를 정한다.
        """

        if restart_reason is not None and adopt_reason is not None:
            raise DeploymentContractError(
                "a deploy either restarts or adopts the live databases, not both"
            )
        watermark = _PinnedRuntimeJournalWatermark()
        try:
            return self._rebuild_pinned_runtime(
                watermark,
                restart_reason=restart_reason,
                adopt_reason=adopt_reason,
            )
        except PinnedRuntimePrejournalFailure as exc:
            if watermark.reached():
                _mark_pinned_runtime_journal_reached(exc)
            raise
        except DeploymentContractError as exc:
            if not watermark.reached():
                _mark_pinned_runtime_prejournal(exc)
            raise

    def _rebuild_pinned_runtime(
        self,
        watermark: _PinnedRuntimeJournalWatermark,
        *,
        restart_reason: str | None,
        adopt_reason: str | None,
    ) -> dict[str, Any]:
        """배포 본문. 분류는 호출자(래퍼)가 붙인다."""

        _require_pinned_runtime_rebuild_root()
        restart = (
            None
            if restart_reason is None
            else DeployRestart(reason=restart_reason, at=_utc_now())
        )
        adopted = (
            None
            if adopt_reason is None
            else DeployRestart(reason=adopt_reason, at=_utc_now())
        )
        explicit = restart is not None or adopted is not None
        release: PinnedRuntimeRelease | None = None
        warnings: list[str] = []

        def prewrite_admission(
            environment_snapshot: ComposeEnvironmentSnapshot,
        ) -> str | None:
            nonlocal release
            del environment_snapshot
            # lock을 잡은 뒤 registry snapshot 하나를 만든다 — rotate가 두 read 사이에
            # 끼어 old release와 new 상태를 섞지 못하게 한다.
            release = current_pinned_runtime_release()
            warnings.extend(_pinned_runtime_admission_warnings(release.pinset_sha256))
            return None

        with _pinned_runtime_rebuild_environment_lock(
            prewrite_admission=prewrite_admission
        ) as (
            lock_snapshot,
            environment_snapshot,
            _role_credentials_initialized,
        ):
            if release is None:  # pragma: no cover - context contract 방어
                raise DeploymentContractError("pinned runtime release snapshot is unavailable")
            values = environment_snapshot.effective
            with _pinned_runtime_prejournal_step("state_initialization"):
                validate_c6c_operation_tokens(values, require_nonempty=True)
                state_paths = pinned_runtime_state_paths(
                    values,
                    pinset_sha256=release.pinset_sha256,
                )
                ensure_pinned_runtime_state_directory(state_paths.state_root)
                application_paths = _map_application_300_paths(
                    state_root=state_paths.state_root,
                    pinset_sha256=release.pinset_sha256,
                )
                # pinset별 permit 디렉터리는 계속 마운트한다. 새로 발급하지는 않는다 —
                # M1 이전 Map 이미지로 이미 커밋된 세대가 자기 permit을 그대로 본다.
                artifact_directories = MapApplication300ArtifactDirectories(
                    dagster_storage_permit=application_paths.metadata_permit_directory,
                )
                status_path = deploy_status_path(state_paths.state_root)
                previous = read_deploy_status(status_path)
            with _pinned_runtime_prejournal_step("prebuild_snapshot"):
                prebuild_transaction, _ = self.capture_transaction_unlocked(
                    environment_override=dict(artifact_directories.compose_environment()),
                    environment_snapshot=environment_snapshot,
                )
                _assert_transaction_matches_c6c_lock(prebuild_transaction, lock_snapshot)
            with _pinned_runtime_prejournal_step("external_prerequisites"):
                self._require_services_ready(
                    _PINNED_RUNTIME_EXTERNAL_PREREQUISITES,
                    transaction=prebuild_transaction,
                    frozen_recovery=True,
                )
            with _pinned_runtime_prejournal_step("source_materialization"):
                sources = materialize_pinned_runtime_sources(
                    release=release,
                    state_paths=state_paths,
                    values=values,
                )
            with _pinned_runtime_prejournal_step("application_base_images"):
                paired_build_images = map_application_300_paired_build_image_names(sources)
                _ensure_map_application_300_python_base_images(sources)
            with _pinned_runtime_prejournal_step("application_builder"):
                # 이미지 태그는 pinset에 묶인다. 이미 있으면 같은 소스에서 나온 것이므로
                # 다시 빌드하지 않는다 — 다시 빌드하면 재현되지 않는 digest가 나와 같은
                # pair의 재실행이 "새 이미지"가 된다.
                if not all(_local_image_present(ref) for ref in paired_build_images.values()):
                    _build_map_application_300_images(
                        sources=sources,
                        api_image=paired_build_images["kor-travel-map-api"],
                        dagster_image=paired_build_images["kor-travel-map-dagster"],
                    )
            with _pinned_runtime_prejournal_step("application_candidate"):
                map_candidate = _load_application_300_candidate(
                    sources=sources,
                    api_image=paired_build_images["kor-travel-map-api"],
                    dagster_image=paired_build_images["kor-travel-map-dagster"],
                )
                build = CandidateRuntimeBuild(
                    sources=sources,
                    map_application_candidate=map_candidate,
                )
                candidate_build_references = {**paired_build_images, **build.image_names}
            candidate_environment = {
                **build.compose_environment(),
                **artifact_directories.compose_environment(),
                "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": map_candidate.application_head,
            }
            with _pinned_runtime_prejournal_step("candidate_snapshot"):
                candidate_transaction, _ = self.capture_transaction_unlocked(
                    environment_override=candidate_environment,
                    environment_snapshot=environment_snapshot,
                )
                _assert_transaction_matches_c6c_lock(candidate_transaction, lock_snapshot)
            with _pinned_runtime_prejournal_step("candidate_contract"):
                self._validate_pinned_runtime_candidate_build_contract(
                    candidate_transaction,
                    build=build,
                    environment_override=candidate_environment,
                )
            with _pinned_runtime_prejournal_step("candidate_compose_build"):
                if not all(_local_image_present(ref) for ref in build.image_names.values()):
                    self._run_pinned_runtime_rebuild_compose(
                        ["build", *COMPOSE_BUILT_RUNTIME_SERVICES],
                        transaction=candidate_transaction,
                    )
            with _pinned_runtime_prejournal_step("candidate_images"):
                image_ids = self._attest_pinned_runtime_candidate_images(
                    build=build,
                    map_candidate=map_candidate,
                )
            with _pinned_runtime_prejournal_step("candidate_bootstrap_settings"):
                self._verify_pinned_runtime_pinvi_bootstrap_settings(
                    transaction=candidate_transaction,
                )
            with _pinned_runtime_prejournal_step("candidate_heads"):
                # Map application head는 `_load_application_300_candidate`가 이미 한 번
                # 관측했다. 나머지 둘은 후보 이미지를 network-less로 한 번씩 돌린다.
                map_dagster_head = parse_candidate_static_head(
                    _run_pinned_runtime_static_command(
                        image_ids["kor-travel-map-dagster"],
                        ("head",),
                        label="Map Dagster",
                        entrypoint="/usr/local/bin/ktm-dagster-storage",
                    ),
                    schema="kor-travel-map.dagster-storage-head.v1",
                    field="head",
                )
                pinvi_head = parse_candidate_static_head(
                    _run_pinned_runtime_static_command(
                        image_ids["pinvi-api"],
                        ("pinvi-admin-bootstrap", "head"),
                        label="PinVi",
                    ),
                    schema="pinvi.candidate-head.v1",
                    field="pinvi_head",
                )
                candidate = build_candidate_generation(
                    sources=sources,
                    map_application_candidate=map_candidate,
                    image_ids=image_ids,
                    map_dagster_head=map_dagster_head,
                    pinvi_head=pinvi_head,
                )
            with _pinned_runtime_prejournal_step("runtime_generation"):
                runtime_environment = {
                    **build.compose_environment(),
                    **generation_compose_environment(
                        candidate,
                        artifact_directories=artifact_directories,
                    ),
                    "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": candidate.map_application_head,
                }
            with _pinned_runtime_prejournal_step("runtime_transaction"):
                runtime_transaction, _ = self.capture_transaction_unlocked(
                    environment_override=runtime_environment,
                    environment_snapshot=environment_snapshot,
                )
                companions = generation_companion_services(
                    runtime_transaction.resolved,
                    candidate.image_ids,
                    excluded_services=_PINNED_RUNTIME_ONESHOT_WRITERS,
                )
            with _pinned_runtime_prejournal_step("runtime_transaction_lock"):
                _assert_transaction_matches_c6c_lock(runtime_transaction, lock_snapshot)
            ensure_generation_references((candidate,), cwd=get_project_root())
            runtimes = database_runtimes_from_frozen_contract(
                resolved=runtime_transaction.resolved,
                environment=runtime_transaction.environment.effective,
            )
            expected_images = self._deployed_images(candidate, companions)
            # 수렴 판정과 identity 기준선은 **실제로 migration할 cluster**를 읽어야 한다.
            # 그래서 두 PostgreSQL을 판정보다 먼저 frozen Compose에 맞춘다. 뒤로 미루면
            # PGDATA·이미지가 바뀐 호스트에서 옛 컨테이너로 기준선을 통과한 뒤 전체 경로가
            # 새 cluster로 다시 만들어 그것을 커밋했다(B2 적대 리뷰 2차).
            self._start_pinned_runtime_databases(
                runtime_transaction=runtime_transaction,
                map_candidate=map_candidate,
            )

            if (
                not explicit
                and previous is not None
                and previous.state == "committed"
                and previous.map_revision == candidate.map_source_revision
                and previous.pinvi_revision == candidate.pinvi_source_revision
                and dict(previous.images) == expected_images
                and previous.databases is not None
                and self._observe_deployed_databases(runtimes) == dict(previous.databases)
                and self._observe_schema_heads(runtimes) == dict(previous.schema_heads)
            ):
                self._converge_committed_runtime(
                    runtime_transaction=runtime_transaction,
                    companions=companions,
                    expected_images=expected_images,
                )
                # 커밋 직후의 보존 정리가 실패했거나 그 사이에 죽었으면 여기서 다시 한다 —
                # 같은 pair의 재실행은 수렴만 하므로 다른 기회가 없다.
                self._reconcile_pinned_runtime_image_retention(
                    candidate,
                    candidate_build_references,
                    warnings,
                )
                return self._pinned_runtime_result(
                    previous,
                    candidate=candidate,
                    outcome="converged",
                    warnings=warnings,
                )

            if (
                not explicit
                and previous is not None
                and previous.databases is not None
                and self._observe_deployed_databases(runtimes) != dict(previous.databases)
            ):
                # 지난 배포가 본 DB가 아니다(누가 지우거나 다시 만들거나 복원했다). 무엇도
                # 바꾸기 전에 멈춘다 — 이대로 올리면 모르는 DB 위에 migration을 쌓는다.
                raise DeploymentContractError(
                    "live databases differ from the last deploy; accept them with "
                    "--adopt-live-databases or rebuild them with --restart"
                )

            if restart is None:
                # 전체 경로가 Map DB 앞에서 거부할 상태라면 런타임을 멈추기 **전에** 거부한다.
                require_map_application_database_convergible(runtimes[0])

            from kor_travel_docker_manager.services.runtime_execution_registry import (
                trusted_manager_source_revision,
            )

            status = begin_deploy(
                previous,
                run_id=str(uuid.uuid4()),
                started_at=_utc_now(),
                manager_revision=trusted_manager_source_revision(),
                map_revision=candidate.map_source_revision,
                pinvi_revision=candidate.pinvi_source_revision,
                pinset_sha256=candidate.pinset_sha256,
                restart=restart,
                adopted=adopted,
                # 채택은 지금 떠 있는 DB를 기준으로 삼는다. 중간에 죽어도 다음 일반 실행이
                # 그 기준으로 확인한다(모두 있을 때만 — 하나라도 없으면 커밋 때 잡힌다).
                adopted_databases=(
                    self._observe_deployed_databases(runtimes)
                    if adopted is not None
                    else None
                ),
            )
            write_deploy_status(status_path, status)
            watermark.mark_reached()
            try:
                committed = self._deploy_forward(
                    status=status,
                    status_path=status_path,
                    restart=restart is not None,
                    candidate=candidate,
                    runtimes=runtimes,
                    runtime_transaction=runtime_transaction,
                    companions=companions,
                    expected_images=expected_images,
                    state_paths=state_paths,
                    values=values,
                )
            except Exception:
                try:
                    self._run_pinned_runtime_rebuild_compose(
                        ["stop", *RUNTIME_SERVICES, *companions],
                        transaction=runtime_transaction,
                    )
                    self._retire_pinned_runtime_oneshot_writers(
                        transaction=runtime_transaction,
                    )
                    reconcile_orphaned_pinvi_bootstrap_credentials(
                        state_paths=state_paths,
                        values=values,
                        global_mutation_lock_held=True,
                        all_one_shot_containers_absent=True,
                    )
                except Exception as cleanup_error:
                    # 원래 오류는 이 예외의 __context__로 남는다 — CLI의 원인 출력이
                    # 둘 다 보여 준다.
                    raise DeploymentContractError(
                        "pinned runtime deploy failed and its cleanup could not prove "
                        "one-shot writer absence"
                    ) from cleanup_error
                raise
            # 여기서부터는 검증이 끝난 배포의 기록이다. 기록 쓰기가 실패해도(디스크 부족
            # 등) 떠 있는 런타임을 내리지 않는다 — 상태는 in_progress로 남고 다음 실행이
            # 처음부터 다시 돈다(멱등). v6 manifest는 한 릴리스 동안 계속 쓴다 — M05
            # driver가 읽는다(ADR-51 D에서 멈춘다).
            write_pinned_runtime_manifest(
                state_paths.manifest,
                PinnedRuntimeManifest(version=6, active_generation=candidate),
            )
            write_deploy_status(status_path, committed)
            self._reconcile_pinned_runtime_image_retention(
                candidate,
                candidate_build_references,
                warnings,
            )
            return self._pinned_runtime_result(
                committed,
                candidate=candidate,
                outcome="deployed",
                warnings=warnings,
            )

    @staticmethod
    def _reconcile_pinned_runtime_image_retention(
        candidate: PinnedRuntimeGeneration,
        candidate_build_references: Mapping[RuntimeService, str],
        warnings: list[str],
    ) -> None:
        """이미지 보존 정리. 배포가 끝난 뒤의 일이라 실패는 경고로만 남긴다."""

        try:
            reconcile_generation_references((candidate,), cwd=get_project_root())
            reconcile_candidate_build_references(
                candidate_build_references,
                candidate,
                cwd=get_project_root(),
            )
        except (DeploymentContractError, OSError):
            warnings.append("pinned runtime image retention could not be reconciled")

    def _start_pinned_runtime_databases(
        self,
        *,
        runtime_transaction: ComposeTransactionSnapshot,
        map_candidate: MapApplicationCandidate,
    ) -> None:
        """두 PostgreSQL을 frozen Compose로 health까지 띄우고 secret·이미지를 확인한다.

        ``up``은 설정이 같으면 무연산이고, 바뀌었으면(이미지·PGDATA·명령) 컨테이너를
        다시 만든다 — 떠 있는 런타임 아래에서 DB가 한 번 재시작된다. 그 대가로 뒤따르는
        identity 판정이 옛 컨테이너가 아니라 이번 배포가 쓸 cluster를 본다.
        """

        postgres = ("kor-travel-map-postgres", "pinvi-postgres")
        self._run_pinned_runtime_rebuild_compose(
            [
                "up",
                "-d",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                str(_COMPOSE_WAIT_TIMEOUT_SECONDS),
                *postgres,
            ],
            transaction=runtime_transaction,
        )
        postgres_records = self._require_services_ready(
            postgres,
            transaction=runtime_transaction,
            frozen_recovery=True,
        )
        validate_map_postgres_runtime_secret_isolation(
            self._inspect_container_runtime_config(str(postgres_records[0]["Name"]))
        )
        validate_pinvi_postgres_runtime_secret_isolation(
            self._inspect_container_runtime_config(str(postgres_records[1]["Name"]))
        )
        if (
            self._inspect_container_image_id(
                str(postgres_records[0]["Name"]),
                label="Map PostgreSQL",
            )
            != map_candidate.postgres_image_id
        ):
            raise DeploymentContractError(
                "Map PostgreSQL runtime image differs from paired candidate"
            )

    def _converge_committed_runtime(
        self,
        *,
        runtime_transaction: ComposeTransactionSnapshot,
        companions: Mapping[str, RuntimeService],
        expected_images: Mapping[str, str],
    ) -> None:
        """committed와 같은 pair: 빌드·migration 없이 떠 있어야 할 것만 맞춘다.

        compose는 설정이 달라진 컨테이너만 다시 만든다. 이미지·설정이 같으면 무연산이다.
        """

        self._run_pinned_runtime_rebuild_compose(
            [
                "up",
                "-d",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                str(_COMPOSE_WAIT_TIMEOUT_SECONDS),
                *_with_generation_companions(RUNTIME_SERVICES, companions),
            ],
            transaction=runtime_transaction,
        )
        self._verify_pinned_runtime_services(
            runtime_transaction=runtime_transaction,
            companions=companions,
            expected_images=expected_images,
        )

    def _verify_pinned_runtime_services(
        self,
        *,
        runtime_transaction: ComposeTransactionSnapshot,
        companions: Mapping[str, RuntimeService],
        expected_images: Mapping[str, str],
    ) -> None:
        """전 서비스 readiness·이미지·C6c secret isolation을 확인한다."""

        runtime_records = self._require_services_ready(
            (*RUNTIME_SERVICES, *companions),
            transaction=runtime_transaction,
            frozen_recovery=True,
        )
        self._assert_pinned_runtime_container_images(
            runtime_records,
            expected_images=expected_images,
        )
        config = load_c6c_deployment_config_from_environment(
            runtime_transaction.environment.effective
        )
        if isinstance(config, C6cDeploymentConfig):
            runtime_configs = self._inspect_c6c_runtime_configs(
                config,
                [*RUNTIME_SERVICES, *companions],
                transaction=runtime_transaction,
                frozen_recovery=True,
            )
            validate_runtime_secret_isolation(runtime_configs, config)
            validate_current_map_ui_auth_runtime(
                runtime_configs[config.map_ui_container],
                config,
            )

    def _deploy_forward(
        self,
        *,
        status: DeployStatus,
        status_path: Path,
        restart: bool,
        candidate: PinnedRuntimeGeneration,
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
        runtime_transaction: ComposeTransactionSnapshot,
        companions: Mapping[str, RuntimeService],
        expected_images: Mapping[str, str],
        state_paths: PinnedRuntimeStatePaths,
        values: Mapping[str, str],
    ) -> DeployStatus:
        """``in_progress`` 이후의 전체 경로. 모든 단계는 다시 돌려도 안전하다."""

        def compose_up(*services: str) -> None:
            self._run_pinned_runtime_rebuild_compose(
                [
                    "up",
                    "-d",
                    "--no-deps",
                    "--wait",
                    "--wait-timeout",
                    str(_COMPOSE_WAIT_TIMEOUT_SECONDS),
                    *services,
                ],
                transaction=runtime_transaction,
            )

        def require_head(runtime: DatabaseRuntime, expected: str, message: str) -> None:
            try:
                observed = read_database_schema_revision(runtime)
            except DeploymentContractError as exc:
                raise DeploymentContractError(message) from exc
            if observed != expected:
                raise DeploymentContractError(message)

        # 살아 있는 writer·runtime을 먼저 멈춘다. crash 뒤 남은 `compose run` one-shot이
        # 보존된 DB를 동시에 건드리지 못하게 하는 자리다 — 마이그레이션 전진에서 더
        # 중요해진다.
        self._run_pinned_runtime_rebuild_compose(
            ["stop", *RUNTIME_SERVICES, *companions],
            transaction=runtime_transaction,
        )
        self._retire_pinned_runtime_oneshot_writers(transaction=runtime_transaction)
        reconcile_orphaned_pinvi_bootstrap_credentials(
            state_paths=state_paths,
            values=values,
            global_mutation_lock_held=True,
            all_one_shot_containers_absent=True,
        )

        # PostgreSQL은 판정 전에 이미 frozen Compose에 맞췄다. 여기서 다시 `up`하지 않는다 —
        # 판정과 migration 사이에 cluster가 바뀔 자리를 만들지 않는다.
        if restart:
            reset_databases_for_application_300(runtimes)
            # 지운 **뒤에** 기준선을 비우고 리셋을 표시한다. 리셋 전에 죽으면 DB는 그대로이므로
            # 다음 일반 실행이 여전히 옛 기준으로 확인해야 하고, 리셋 기록도 가져가지 않는다.
            status = replace(status, databases=None, step=RESET_DONE_STEP)
            write_deploy_status(status_path, status)
        # PinVi DB가 없으면(새 호스트·지워진 DB) Map을 건드리기 전에 만든다.
        create_database_if_absent(runtimes[2])

        # Map application DB: 없으면 만들고 role bootstrap, 이미 bootstrap됐으면 그대로.
        ensure_map_application_database(
            runtimes[0],
            run_role_bootstrap=lambda: self._run_pinned_runtime_rebuild_compose(
                [
                    "--profile",
                    "bootstrap",
                    "run",
                    "--rm",
                    "--no-deps",
                    "--env",
                    "KOR_TRAVEL_MAP_POSTGRES_PASSWORD",
                    "kor-travel-map-db-role-bootstrap",
                ],
                transaction=runtime_transaction,
            ),
        )
        # `alembic upgrade head` 뒤 런타임 권한 재조정. 이미 head면 무연산이다. 성공의
        # 근거는 종료 코드가 아니라 Manager가 직접 읽은 head다.
        self._run_pinned_runtime_rebuild_compose(
            [
                "--profile",
                "bootstrap",
                "run",
                "--rm",
                "--no-deps",
                _MAP_APPLICATION_SCHEMA_SERVICE,
            ],
            transaction=runtime_transaction,
        )
        require_head(
            runtimes[0],
            candidate.map_application_head,
            "Map application schema differs from candidate head",
        )

        # Dagster metadata DB: 없을 때만 role과 DB를 만든다.
        if read_database_identity(runtimes[1]) is None:
            metadata_user = values.get("KOR_TRAVEL_MAP_DAGSTER_METADATA_USER")
            metadata_password = values.get("KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD")
            if not isinstance(metadata_user, str) or not isinstance(metadata_password, str):
                raise DeploymentContractError(
                    "Map Dagster metadata credentials are unavailable"
                )
            initialize_application_300_dagster_metadata_database(
                runtimes[1],
                metadata_user=metadata_user,
                metadata_password=metadata_password,
            )

        compose_up("kor-travel-map-api")
        require_head(
            runtimes[0],
            candidate.map_application_head,
            "Map application schema differs from candidate head",
        )
        self._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=runtime_transaction,
        )
        require_head(
            runtimes[1],
            candidate.map_dagster_head,
            "Map Dagster storage execution result is uncertain",
        )
        compose_up(
            *_with_generation_companions(
                (
                    "kor-travel-map-ui",
                    "kor-travel-map-dagster",
                    "kor-travel-map-dagster-daemon",
                ),
                companions,
            )
        )

        # PinVi: 0101 fresh-install fence는 빈 DB에서만 필요하다(기존 DB에서는 0101이
        # 다시 돌지 않는다). bootstrap은 `alembic upgrade head` 뒤 admin을 만들거나
        # 고친다 — 둘 다 멱등이다.
        if not schema_revision_table_exists(runtimes[2]):
            self._ensure_pinvi_fresh_migration_fence(values=values)
        self._run_pinvi_admin_bootstrap(
            transaction=runtime_transaction,
            state_paths=state_paths,
            values=values,
            transaction_id=status.run_id,
        )
        require_head(runtimes[2], candidate.pinvi_head, "PinVi schema differs from candidate head")
        compose_up("pinvi-api")
        self._require_services_ready(
            ("pinvi-api",),
            transaction=runtime_transaction,
            frozen_recovery=True,
        )
        run_pinvi_canonical_smoke(
            load_c6c_deployment_config_from_environment(values),
            cancel_probe_state=PinviCancelProbeState(transaction_id=status.run_id),
            state_recorder=lambda _state: None,
        )
        compose_up(*_with_generation_companions(("pinvi-web", "pinvi-dagster"), companions))
        self._verify_pinned_runtime_services(
            runtime_transaction=runtime_transaction,
            companions=companions,
            expected_images=expected_images,
        )

        databases = self._observe_deployed_databases(runtimes)
        if databases is None:
            raise DeploymentContractError("pinned runtime databases disappeared during deploy")
        return commit_deploy(
            status,
            committed_at=_utc_now(),
            images=expected_images,
            schema_heads={str(role): head for role, head in candidate.schema_heads.items()},
            databases=databases,
        )

    @staticmethod
    def _inspect_image_source_revision(
        image_id: str,
        *,
        label: str,
        expected_build_environment: str | None = None,
    ) -> str:
        return inspect_c6c_image_source_revision(
            image_id,
            label=label,
            expected_build_environment=expected_build_environment,
            cwd=get_project_root(),
        )

    def _inspect_c6c_runtime_configs(
        self,
        config: C6cDeploymentConfig,
        services: list[str],
        *,
        transaction: ComposeTransactionSnapshot,
        frozen_recovery: bool = False,
    ) -> dict[str, Mapping[str, Any]]:
        records = self._require_services_ready(
            services,
            transaction=transaction,
            frozen_recovery=frozen_recovery,
        )
        container_names = [str(record["Name"]) for record in records]
        if (
            config.map_container not in container_names
            or config.pinvi_container not in container_names
            or config.map_ui_container not in container_names
        ):
            raise DeploymentContractError(
                "C6c protected containers are missing from runtime inspection"
            )
        return {
            container_name: self._inspect_container_runtime_config(container_name)
            for container_name in container_names
        }

    @staticmethod
    def _compose_ps_records(
        payload: str,
        *,
        allow_empty: bool = False,
    ) -> list[Mapping[str, Any]]:
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, list):
                records = parsed
            elif isinstance(parsed, Mapping):
                records = [parsed]
            else:
                raise DeploymentContractError(
                    "docker compose ps returned invalid container metadata"
                )
        except json.JSONDecodeError:
            try:
                records = [json.loads(line) for line in payload.splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                raise DeploymentContractError(
                    "docker compose ps returned invalid container metadata"
                ) from exc
        validated: list[Mapping[str, Any]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise DeploymentContractError(
                    "docker compose ps returned invalid container metadata"
                )
            for field_name in ("Name", "Service", "State"):
                value = record.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    raise DeploymentContractError(
                        "docker compose ps returned invalid container metadata"
                    )
            health = record.get("Health")
            if health is not None and not isinstance(health, str):
                raise DeploymentContractError(
                    "docker compose ps returned invalid container metadata"
                )
            validated.append(record)
        if not validated and not allow_empty:
            raise DeploymentContractError("docker compose ps returned no managed containers")
        return validated

    def _require_services_ready(
        self,
        services: Sequence[str],
        *,
        transaction: ComposeTransactionSnapshot,
        frozen_recovery: bool = False,
    ) -> list[Mapping[str, Any]]:
        """필수 서비스가 canonical resolved Compose readiness인지 확인한다."""

        expected = list(dict.fromkeys(services))
        if not expected:
            return []
        contracts = _resolved_service_readiness_contracts(
            transaction.resolved,
            expected,
        )
        if frozen_recovery:
            ps_result = self._run_frozen_recovery(
                ["ps", "--all", "--format", "json", *expected],
                transaction=transaction,
            )
        else:
            ps_result = self.run(
                ["ps", "--all", "--format", "json", *expected],
                transaction=transaction,
            )
        if not ps_result["success"]:
            raise DeploymentContractError("cannot inspect mandatory service readiness")
        records = self._compose_ps_records(str(ps_result.get("stdout", "")))
        by_service = _index_singleton_service_records(
            records,
            expected,
            contracts,
            allow_missing=False,
        )
        not_ready: list[str] = []
        for service in expected:
            record = by_service[service]
            state = str(record.get("State", "")).strip().lower()
            health = str(record.get("Health", "")).strip().lower()
            if state != "running":
                not_ready.append(service)
                continue
            if contracts[service].policy is _ServiceReadinessPolicy.HEALTHY and health != "healthy":
                not_ready.append(service)
        if not_ready:
            raise DeploymentContractError(
                "mandatory services do not satisfy canonical readiness: " + ", ".join(not_ready)
            )
        return [by_service[service] for service in expected]

    @staticmethod
    def _inspect_container_runtime_config(container_name: str) -> Mapping[str, Any]:
        try:
            completed = subprocess.run(
                ["docker", "inspect", "--format={{json .Config}}", container_name],
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise DeploymentContractError(
                "cannot verify C6c runtime secret isolation"
            ) from exc
        if completed.returncode != 0:
            raise DeploymentContractError("cannot verify C6c runtime secret isolation")
        try:
            runtime_config = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DeploymentContractError(
                "container returned invalid runtime config metadata"
            ) from exc
        if not isinstance(runtime_config, Mapping):
            raise DeploymentContractError("container returned invalid runtime config metadata")
        return runtime_config

    def status_target(self, target: str = "all", *, capture_output: bool = True) -> dict[str, Any]:
        services = services_for_target(target)
        groups = service_groups_for_target(target)
        external_groups = [group for group in groups if group.external is not None]
        if not external_groups:
            result = self.run(["ps", *services], capture_output=capture_output)
            result["target"] = target
            result["target_sequence"] = target_sequence_for_target(target)
            result["services"] = services
            return result

        # 여러 프로젝트에 걸친 target은 **묶음마다 한 번씩** 돌아야 한다. 평평한
        # 목록으로 한 번에 부르면 남의 프로젝트 서비스 이름이 되어 `no such service`다.
        group_results: list[dict[str, Any]] = []
        for group in groups:
            group_result = self.run(
                ["ps", *group.services],
                capture_output=capture_output,
                external=group.external,
            )
            group_result["project"] = group.project_label
            group_result["services"] = list(group.services)
            group_results.append(group_result)
        # `returncode`가 없으면 `cli._emit_process_result`가 `int(result.get(
        # "returncode", 1))`로 **항상 1**을 낸다 — 전부 running이어도 exit 1이다.
        failed = [item for item in group_results if not item.get("success")]
        return {
            "success": not failed,
            "returncode": 0 if not failed else 1,
            "command": None,
            "stderr": "\n".join(
                str(item.get("stderr") or "") for item in group_results
            ).strip(),
            "target": target,
            "target_sequence": target_sequence_for_target(target),
            "services": services,
            "groups": group_results,
            # 묶음이 여러 개면 단일 stdout이 없다 — 합치면 어느 프로젝트의 줄인지
            # 알 수 없으므로 묶어서 내보내고, 소비자가 `groups`를 읽게 한다.
            "stdout": "\n".join(
                f"# project={item['project']}\n{item.get('stdout', '')}"
                for item in group_results
            ),
        }

    def logs(
        self,
        name: str,
        *,
        follow: bool = False,
        tail: int = 100,
        capture_output: bool = True,
    ) -> dict[str, Any]:
        external: ExternalProject | None = None
        omitted_projects: list[str] = []
        if is_known_target(name):
            services = runtime_services_for_target(name)
            groups = service_groups_for_target(name, runtime_only=True)
            # **지목한 target 자신의 프로젝트로 좁힌다.** 여러 프로젝트의 로그를 한
            # 스트림으로 합칠 수는 없는데, 첫 판은 그럴 때 "한 프로젝트의 target을
            # 고르라"며 거부했다. 그 조언은 `airport`에 대해 **따를 수 없었다** —
            # `depends_on: [airport-db]` 때문에 의존 폐포가 **항상** 두 프로젝트에
            # 걸치고, `airport`이 자기 서비스를 가리키는 유일한 이름이기 때문이다
            # (적대 리뷰 2026-09-18 F3).
            #
            # Manager target은 폐포 전체가 같은 프로젝트(`None`)라 **한 글자도 바뀌지
            # 않는다.** 빠진 프로젝트는 조용히 버리지 않고 결과에 실어 호출자가 알린다
            # — 조용한 생략이 원래 거부의 이유였다.
            own_external = external_project_for_target(name)
            own_project = own_external.project if own_external is not None else None
            selected = [
                group
                for group in groups
                if (group.external.project if group.external is not None else None)
                == own_project
            ]
            omitted_projects = [
                group.project_label for group in groups if group not in selected
            ]
            if selected:
                external = selected[0].external
                services = [
                    service for group in selected for service in group.services
                ]
            elif own_external is not None:
                # **술어를 폐포가 아니라 지목한 target의 소속에 건다.** 첫 판은
                # `elif groups:`였는데 `service_groups_for_target`이 빈 묶음을 버리므로
                # **폐포가 비면 이 팔이 아예 돌지 않았다** — 그러면 `external`은 `None`
                # 인데 `services`는 빈 목록이라, `docker compose -f <Manager compose>
                # logs`가 **서비스 필터 없이** 돌면서 Manager 전체 서비스의 로그를 그
                # target의 것으로 제시하고 `omitted_projects: []`로 "빠뜨린 것 없음"을
                # 단언했다(적대 리뷰 2026-09-18 E-R2-01 실측, CLI로 재현).
                #
                # 내가 쓴 검사가 그것을 못 본 이유가 더 중요하다 —
                # `service_groups_for_target`을 **의존 묶음만 남기도록** 스텁해서
                # `groups`가 비지 않는 절반만 태웠다.
                #
                # 빈 명령을 Manager 프로젝트에 돌리는 것도 답이 아니다 — 운영자가
                # 물어본 것은 이 target이다. 말하고 멈춘다.
                reached = ", ".join(group.project_label for group in groups) or "nothing"
                raise DeploymentContractError(
                    f"target '{name}' declares no runtime services in its own "
                    f"project ({own_project}); the closure only reaches {reached}"
                )
        elif name in MANAGED_CONTAINERS:
            # **컨테이너 id는 compose service 이름이 아니다.** 첫 판은 외부 컨테이너만
            # 번역하고 Manager 컨테이너는 id를 그대로 넘겼다 — `kor-travel-map-postgresql`
            # 처럼 둘이 다른 이름 넷에서 `no such service`다(적대 리뷰 2026-09-18 B-F12,
            # 선재 결함). 번역은 소속과 무관하므로 양쪽에 똑같이 한다.
            external = external_project_for_container(name)
            services = [container_id_to_compose_service(name)]
        else:
            services = [name]

        args = ["logs", f"--tail={tail}"]
        if follow:
            args.append("-f")
        args.extend(services)
        result = self.run(args, capture_output=capture_output, external=external)
        result["target"] = name
        if is_known_target(name):
            result["target_sequence"] = target_sequence_for_target(name)
        result["services"] = services
        # 빈 목록이어도 키를 둔다 — 소비자가 `.get()`의 기본값과 "정말 없음"을
        # 구분하지 못하는 것이 조용한 생략의 시작이다.
        result["omitted_projects"] = omitted_projects
        return result


compose_service = ComposeService()
