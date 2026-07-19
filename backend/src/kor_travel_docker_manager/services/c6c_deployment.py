from __future__ import annotations

import fcntl
import grp
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote, urlencode, urlsplit

import yaml
from dotenv import dotenv_values

_MAP_API_SERVICE = "kor-travel-map-api"
_MAP_UI_SERVICE = "kor-travel-map-ui"
_PINVI_API_SERVICE = "pinvi-api"
_MAP_READ_ENV = "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN"
_MAP_CANCEL_ENV = "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN"
_MAP_REQUIRED_ENV = "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED"
_PINVI_READ_ENV = "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN"
_PINVI_CANCEL_ENV = "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN"
_MAP_UI_USERNAME_ENV = "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME"
_MAP_UI_PASSWORD_HASH_ENV = "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH"
_MAP_UI_SESSION_SECRET_ENV = "KOR_TRAVEL_MAP_UI_SESSION_SECRET"
_MAP_UI_PASSWORD_ENV = "KTDM_C6C_MAP_UI_ADMIN_PASSWORD"
_PINVI_ADMIN_PASSWORD_ENV = "KTDM_C6C_PINVI_ADMIN_PASSWORD"
_FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES = frozenset(
    {
        "KOR_TRAVEL_MAP_DATA_GO_KR_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KMA_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KMA_APIHUB_KEY",
        "KOR_TRAVEL_MAP_API_OPINET_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_DATAGOKR_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_VISITKOREA_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KREX_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KNPS_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_AIRKOREA_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KRFOREST_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_ETL_LIVE_PREVIEW_ENABLED",
    }
)
_MANAGER_ONLY_CREDENTIAL_NAMES = frozenset(
    {
        "KTDM_C6C_CONTRACT_GENERATION",
        _MAP_UI_PASSWORD_ENV,
        "KTDM_C6C_PINVI_ADMIN_EMAIL",
        _PINVI_ADMIN_PASSWORD_ENV,
        "KTDM_C6C_CANCEL_PROBE_JOB_ID",
    }
)
_MAP_UI_AUTH_ENV_NAMES = frozenset(
    {
        _MAP_UI_USERNAME_ENV,
        _MAP_UI_PASSWORD_HASH_ENV,
        _MAP_UI_SESSION_SECRET_ENV,
    }
)
_CANDIDATE_REQUIRED_PROTECTED_SERVICES = frozenset(
    {_MAP_API_SERVICE, _PINVI_API_SERVICE, _MAP_UI_SERVICE}
)
_OPS_ENV_NAMES = frozenset(
    {
        _MAP_READ_ENV,
        _MAP_CANCEL_ENV,
        _MAP_REQUIRED_ENV,
        _PINVI_READ_ENV,
        _PINVI_CANCEL_ENV,
    }
)
_CANDIDATE_ALLOWED_API_ENV_SOURCES = {
    (_MAP_API_SERVICE, _MAP_READ_ENV): _MAP_READ_ENV,
    (_MAP_API_SERVICE, _MAP_CANCEL_ENV): _MAP_CANCEL_ENV,
    (_MAP_API_SERVICE, _MAP_REQUIRED_ENV): _MAP_REQUIRED_ENV,
    (_PINVI_API_SERVICE, _PINVI_READ_ENV): _MAP_READ_ENV,
    (_PINVI_API_SERVICE, _PINVI_CANCEL_ENV): _MAP_CANCEL_ENV,
    (_MAP_UI_SERVICE, _MAP_UI_USERNAME_ENV): _MAP_UI_USERNAME_ENV,
    (_MAP_UI_SERVICE, _MAP_UI_PASSWORD_HASH_ENV): _MAP_UI_PASSWORD_HASH_ENV,
    (_MAP_UI_SERVICE, _MAP_UI_SESSION_SECRET_ENV): _MAP_UI_SESSION_SECRET_ENV,
}
_CANDIDATE_CANONICAL_API_ENV_VALUES = {
    (_MAP_API_SERVICE, _MAP_READ_ENV): "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}",
    (_MAP_API_SERVICE, _MAP_CANCEL_ENV): "${KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN:-}",
    (_MAP_API_SERVICE, _MAP_REQUIRED_ENV): (
        "${KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED:?"
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED must be explicitly set}"
    ),
    (_PINVI_API_SERVICE, _PINVI_READ_ENV): "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}",
    (_PINVI_API_SERVICE, _PINVI_CANCEL_ENV): (
        "${KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN:-}"
    ),
    (_MAP_UI_SERVICE, _MAP_UI_USERNAME_ENV): (
        "${KOR_TRAVEL_MAP_UI_ADMIN_USERNAME:?"
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME must be explicitly set}"
    ),
    (_MAP_UI_SERVICE, _MAP_UI_PASSWORD_HASH_ENV): (
        "${KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH:?"
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH must be explicitly set}"
    ),
    (_MAP_UI_SERVICE, _MAP_UI_SESSION_SECRET_ENV): (
        "${KOR_TRAVEL_MAP_UI_SESSION_SECRET:?"
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET must be explicitly set}"
    ),
}
_CANDIDATE_PROTECTED_VALUE_ENV_NAMES = (
    (_OPS_ENV_NAMES - {_MAP_REQUIRED_ENV})
    | _MANAGER_ONLY_CREDENTIAL_NAMES
    | {
        _MAP_UI_PASSWORD_HASH_ENV,
        _MAP_UI_SESSION_SECRET_ENV,
    }
)
_C6C_API_IDENTIFIERS = frozenset(
    {
        _MAP_API_SERVICE,
        _PINVI_API_SERVICE,
        "kor-travel-map-api-latest",
        "pinvi-api-latest",
    }
)
_ISO8601_DATETIME_WITH_OFFSET = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:[Zz]|[+-]\d{2}:?\d{2})$"
)
_OPERATION_STATES = frozenset({"queued", "running", "done", "failed", "cancelled"})
_PROVIDER_SYNC_STATUSES = frozenset(
    {"active", "paused", "disabled", "failed", "never_run"}
)
_RETRYABLE_CANCELLATION_ERROR_CODES = frozenset(
    {
        "DAGSTER_TERMINATE_FAILED",
        "DAGSTER_TERMINATION_TIMEOUT",
        "DAGSTER_UNAVAILABLE",
    }
)
_FAILED_CANCELLATION_ERROR_CODES = frozenset(
    {
        "DAGSTER_RECONCILE_FAILED",
        "PIPELINE_CANCELLATION_INVARIANT",
        "PIPELINE_CANCELLATION_UNSAFE",
    }
)
_IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_CONTRACT_GENERATION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_COMPOSE_PROJECT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
_ASCII_RETRY_AFTER = re.compile(r"^[0-9]+$")
_MAP_UI_PASSWORD_HASH_PATTERN = re.compile(
    r"^pbkdf2_sha256\$([0-9]+)\$[0-9A-Za-z_-]+\$[0-9A-Za-z_-]+$"
)
_CANDIDATE_EXTERNAL_FILE_MAX_BYTES = 1_048_576
_CANDIDATE_ALLOWED_EXTERNAL_RESOURCE_REFERENCES: frozenset[
    tuple[str, str, str]
] = frozenset()
_CANDIDATE_ALLOWED_SYSTEM_BINDS = {
    ("cadvisor", "/sys", True): "/sys",
    ("cadvisor", "/var/run/docker.sock", True): "/var/run/docker.sock",
}
_CANDIDATE_ALLOWED_OPERATOR_BINDS = {
    (
        "kor-travel-geo-postgres",
        "/var/lib/postgresql/data",
        False,
    ): "${KOR_TRAVEL_GEO_PGDATA:-/home/digitie/kor-travel-geo-data/pgdata-final-20260529}",
    (
        "kor-travel-geo-postgres",
        "/data/juso",
        True,
    ): "${KOR_TRAVEL_GEO_JUSO_DATA:-/mnt/f/dev/kor-travel-geo/data/juso}",
    (
        "kor-travel-geo-postgres",
        "/docker-entrypoint-initdb.d/010-ensure-kor-travel-geo-db.sh",
        True,
    ): "./scripts/ensure-kor-travel-geo-db.sh",
    (
        "kor-travel-geo-postgres",
        "/opt/kor-travel-docker-manager/ensure-kor-travel-geo-db.sh",
        True,
    ): "./scripts/ensure-kor-travel-geo-db.sh",
    (
        "kor-travel-geo-postgres",
        "/opt/kor-travel-docker-manager/verify-kor-travel-geo-source.sh",
        True,
    ): "./scripts/verify-kor-travel-geo-source.sh",
    ("rustfs", "/data", False): (
        "${RUSTFS_DATA_DIR:-/home/digitie/kor-travel-geo-data/rustfs}"
    ),
    (
        "rustfs-init",
        "/opt/kor-travel-docker-manager/ensure-rustfs-buckets.sh",
        True,
    ): "./scripts/ensure-rustfs-buckets.sh",
    ("kor-travel-geo-api", "/data", True): (
        "${KOR_TRAVEL_GEO_APP_DATA_DIR:-../kor-travel-geo/data}"
    ),
    ("kor-travel-geo-api", "/app/data/backups", False): (
        "${KOR_TRAVEL_GEO_BACKUP_DIR:-../kor-travel-geo/data/backups}"
    ),
    ("prometheus", "/etc/prometheus/prometheus.yml", True): (
        "./config/prometheus/prometheus.yml"
    ),
    ("prometheus", "/prometheus", False): (
        "${PROMETHEUS_DATA_DIR:-/home/digitie/kor-travel-geo-data/prometheus}"
    ),
    ("grafana", "/var/lib/grafana", False): (
        "${GRAFANA_DATA_DIR:-/home/digitie/kor-travel-geo-data/grafana}"
    ),
    ("grafana", "/etc/grafana/provisioning/datasources", True): (
        "./config/grafana/provisioning/datasources"
    ),
    ("kor-travel-geo-dagster", "/app/data/backups", False): (
        "${KOR_TRAVEL_GEO_BACKUP_DIR:-../kor-travel-geo/data/backups}"
    ),
    ("kor-travel-geo-dagster-daemon", "/app/data/backups", False): (
        "${KOR_TRAVEL_GEO_BACKUP_DIR:-../kor-travel-geo/data/backups}"
    ),
}
_CANDIDATE_ALLOWED_EXTERNAL_VOLUME_REFERENCES: frozenset[str] = frozenset()
_PAIR_MANIFEST_VERSION = 3
_HELD_DEPLOYMENT_LOCKS: ContextVar[frozenset[str]] = ContextVar(
    "held_c6c_deployment_locks", default=frozenset()
)


class _CompatiblePairMutationCapability:
    __slots__ = ()


class _ManagedComposeMutationCapability:
    __slots__ = ()


_COMPATIBLE_PAIR_MUTATION_CAPABILITY = _CompatiblePairMutationCapability()
_MANAGED_COMPOSE_MUTATION_CAPABILITY = _ManagedComposeMutationCapability()


class DeploymentContractError(ValueError):
    """C6c 배포가 컨테이너 변경 전에 중단되어야 하는 계약 위반."""


class ComposeCandidateContractError(DeploymentContractError):
    """compose candidate가 C6c 보호값 격리 계약을 위반했다."""

    code = "COMPOSE_CANDIDATE_PROTECTED_REFERENCE"


class ComposePostMutationContractError(DeploymentContractError):
    """mutation 성공 뒤 계약 drift가 발생해 복구 결과를 함께 보존한다."""

    code = "COMPOSE_POST_MUTATION_CONTRACT_FAILURE"

    def __init__(
        self,
        error: Exception,
        *,
        recovery_attempted: bool,
        recovery_succeeded: bool,
        recovery_error: str | None,
        restoration: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(str(error))
        self.original_error = error
        self.recovery_attempted = recovery_attempted
        self.recovery_succeeded = recovery_succeeded
        self.recovery_error = recovery_error
        self.restoration = restoration


@dataclass(frozen=True)
class CandidatePathIdentity:
    path: str
    device: int
    inode: int
    mode: int
    uid: int
    gid: int


@dataclass(frozen=True)
class CandidateSystemBindSnapshot:
    service: str
    source: str
    target: str
    read_only: bool
    path_chain: tuple[CandidatePathIdentity, ...]


@dataclass(frozen=True)
class CandidateVolumeMount:
    kind: str
    source: str
    target: str
    read_only: bool
    declared_source: str | None = None
    declared_target: str | None = None


@dataclass(frozen=True)
class C6cSmokeConfig:
    pinvi_api_base_url: str
    map_ui_base_url: str
    pinvi_web_base_url: str
    map_ui_username: str
    map_ui_password: str = field(repr=False)
    pinvi_admin_email: str = field(repr=False)
    pinvi_admin_password: str = field(repr=False)
    cancel_probe_job_id: str = field(repr=False)


@dataclass(frozen=True)
class C6cDeploymentConfig:
    deployment_environment: str
    pinvi_environment: str
    base_url: str
    map_container_port: int
    read_token: str = field(repr=False)
    cancel_token: str = field(repr=False)
    map_container: str
    map_ui_container: str
    map_ui_password_hash: str = field(repr=False)
    map_ui_session_secret: str = field(repr=False)
    pinvi_container: str
    contract_generation: str = field(repr=False)
    smoke: C6cSmokeConfig

    @property
    def production(self) -> bool:
        return self.deployment_environment == "production"


@dataclass(frozen=True)
class CompatibleImagePair:
    map_image_id: str
    map_source_revision: str
    pinvi_image_id: str
    pinvi_source_revision: str
    contract_generation: str
    recorded_at: str


@dataclass(frozen=True)
class C6cBuildProvenance:
    map_source_revision: str
    pinvi_source_revision: str

    def compose_environment(self) -> dict[str, str]:
        return {
            "KOR_TRAVEL_MAP_GIT_COMMIT": self.map_source_revision,
            "PINVI_SOURCE_REVISION": self.pinvi_source_revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        }


@dataclass(frozen=True)
class CompatiblePairManifest:
    version: int
    rollback: CompatibleImagePair
    active: CompatibleImagePair


@dataclass(frozen=True)
class HttpProbeResponse:
    status: int
    payload: Any | None
    retry_after: int | None = None
    retry_after_present: bool | None = None
    set_cookie: bool = False
    location: str | None = None
    body_text: str | None = None
    content_type: str | None = None


@dataclass
class PinviCancelProbeState:
    """한 compatible-pair transaction의 파괴적 cancel probe 1회 상태."""

    attempted: bool = False
    result: dict[str, int | str] | None = None


def assert_manager_mutation_allowed(
    *,
    env_path: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """모든 manager mutation이 공유하는 명시적 실행 환경 계약을 검증한다."""

    if environment is None:
        if env_path is None:
            raise DeploymentContractError(
                "manager mutation requires a frozen environment"
            )
        environment = effective_environment(env_path)
    return _validate_mutation_environment(environment)


def assert_c6c_mutation_allowed(
    identifiers: Iterable[str],
    *,
    env_path: str | None = None,
    environment: Mapping[str, str] | None = None,
    capability: object | None = None,
) -> None:
    """Map/PinVi API mutation은 production compatible-pair 경로만 허용한다."""

    normalized = {str(identifier).strip() for identifier in identifiers}
    if not normalized.intersection(_C6C_API_IDENTIFIERS):
        return
    if environment is None:
        if env_path is None:
            raise DeploymentContractError(
                "C6c mutation requires a frozen environment"
            )
        environment = effective_environment(env_path)
    values = environment
    mode = _validate_mutation_environment(values)
    if mode == "production" and capability is not _COMPATIBLE_PAIR_MUTATION_CAPABILITY:
        raise DeploymentContractError(
            "production Map/PinVi API mutation requires the compatible-pair workflow"
        )


def assert_compose_mutation_allowed(
    identifiers: Iterable[str],
    *,
    env_path: str | None = None,
    environment: Mapping[str, str] | None = None,
    capability: object | None = None,
) -> None:
    """production low-level Compose mutation은 신뢰된 상위 workflow만 호출한다."""

    normalized = {str(identifier).strip() for identifier in identifiers}
    if not normalized:
        return
    mode = assert_manager_mutation_allowed(
        env_path=env_path,
        environment=environment,
    )
    if (
        mode == "production"
        and normalized.intersection(_C6C_API_IDENTIFIERS)
        and capability is not _COMPATIBLE_PAIR_MUTATION_CAPABILITY
    ):
        raise DeploymentContractError(
            "production Map/PinVi API mutation requires the compatible-pair workflow"
        )
    if (
        mode == "production"
        and capability is not _MANAGED_COMPOSE_MUTATION_CAPABILITY
        and capability is not _COMPATIBLE_PAIR_MUTATION_CAPABILITY
    ):
        raise DeploymentContractError(
            "production Compose mutation requires a managed workflow capability"
        )


def _validate_mutation_environment(values: Mapping[str, str]) -> str:
    """모든 managed mutation 진입점이 공유하는 최소 fail-close 환경 계약."""

    deployment_environment = values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
    pinvi_environment = values.get("PINVI_ENVIRONMENT", "").strip().lower()
    if deployment_environment not in {"local", "production"}:
        raise DeploymentContractError(
            "KTDM_DEPLOYMENT_ENVIRONMENT must be explicitly set before manager mutation"
        )
    if pinvi_environment not in {"development", "production"}:
        raise DeploymentContractError(
            "PINVI_ENVIRONMENT must be explicitly set before manager mutation"
        )
    expected_pinvi_environment = (
        "production" if deployment_environment == "production" else "development"
    )
    if pinvi_environment != expected_pinvi_environment:
        raise DeploymentContractError(
            "KTDM_DEPLOYMENT_ENVIRONMENT must map local->development and "
            "production->production for PINVI_ENVIRONMENT"
        )

    required_text = values.get(_MAP_REQUIRED_ENV, "").strip().lower()
    expected_required = "true" if deployment_environment == "production" else "false"
    if required_text != expected_required:
        raise DeploymentContractError(
            f"{_MAP_REQUIRED_ENV} must be explicitly set to {expected_required} "
            "before manager mutation"
        )
    _validate_raw_token_pair(
        values.get(_MAP_READ_ENV, ""),
        values.get(_MAP_CANCEL_ENV, ""),
        require_nonempty=deployment_environment == "production",
    )
    return deployment_environment


@contextmanager
def c6c_deployment_lock(path: str) -> Iterator[None]:
    """배포 preflight부터 manifest commit/복구까지 host-wide nonblocking lock."""

    lock_path = Path(path)
    lock_key = str(lock_path.resolve(strict=False))
    held_locks = _HELD_DEPLOYMENT_LOCKS.get()
    if lock_key in held_locks:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle: BinaryIO | None = None
    context_token = None
    try:
        try:
            handle = lock_path.open("a+b")
            os.chmod(lock_path, 0o600)
        except OSError as exc:
            raise DeploymentContractError("cannot acquire C6c deployment lock") from exc
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentContractError(
                "another C6c compatible-pair operation is already active"
            ) from exc
        context_token = _HELD_DEPLOYMENT_LOCKS.set(held_locks | {lock_key})
        yield
    finally:
        if context_token is not None:
            _HELD_DEPLOYMENT_LOCKS.reset(context_token)
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def effective_environment(env_path: str) -> dict[str, str]:
    """Compose와 같은 우선순위로 env-file 위에 process env를 겹친다."""

    values: dict[str, str] = {}
    if os.path.exists(env_path):
        values.update(
            {
                key: value or ""
                for key, value in dotenv_values(env_path).items()
                if isinstance(key, str)
            }
        )
    values.update(os.environ)
    return values


def c6c_state_paths(values: Mapping[str, str]) -> tuple[str, str]:
    """Frozen environment로 manifest를, process policy로 global lock을 정한다."""

    production = values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower() == "production"
    project_name = values.get("COMPOSE_PROJECT_NAME", "").strip().lower()
    if not project_name and not production:
        project_name = "kor-travel-local"
    if not _COMPOSE_PROJECT_PATTERN.fullmatch(project_name):
        raise DeploymentContractError(
            "COMPOSE_PROJECT_NAME must be explicit and canonical for C6c state"
        )
    default_root = Path.home() / ".local" / "state" / "kor-travel-docker-manager"
    configured_root = values.get("KTDM_C6C_STATE_ROOT", "").strip()
    if production and configured_root and Path(configured_root) != default_root:
        raise DeploymentContractError(
            "production KTDM_C6C_STATE_ROOT is fixed to the canonical manager state root"
        )
    root = _canonical_absolute_path(
        configured_root or str(default_root),
        "KTDM_C6C_STATE_ROOT",
    )
    state_dir = _canonical_absolute_path(
        str(root / project_name),
        "C6c deployment state directory",
    )
    manifest_override = values.get("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", "").strip()
    lock_override = values.get("KTDM_C6C_DEPLOYMENT_LOCK", "").strip()
    if production and (manifest_override or lock_override):
        raise DeploymentContractError(
            "production C6c manifest and global lock paths are fixed"
        )
    manifest = _canonical_absolute_path(
        manifest_override or str(state_dir / "compatible-pair-v3.json"),
        "KTDM_C6C_COMPATIBLE_PAIR_MANIFEST",
    )
    lock = Path(c6c_global_mutation_lock_path())
    if manifest == lock:
        raise DeploymentContractError("C6c manifest and lock paths must differ")
    return str(manifest), str(lock)


def c6c_global_mutation_lock_path() -> str:
    """모든 Compose mutation이 공유하는 `.env` 비의존 host-global lock."""

    default = (
        Path.home()
        / ".local"
        / "state"
        / "kor-travel-docker-manager"
        / "global-mutation.lock"
    )
    override = os.environ.get("KTDM_C6C_DEPLOYMENT_LOCK", "").strip()
    process_mode = os.environ.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
    if override:
        if process_mode != "local":
            raise DeploymentContractError(
                "production C6c global mutation lock path is fixed"
            )
        return str(
            _canonical_absolute_path(override, "KTDM_C6C_DEPLOYMENT_LOCK")
        )
    return str(default.resolve(strict=False))


def _canonical_absolute_path(value: str, env_name: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path != path.resolve(strict=False):
        raise DeploymentContractError(f"{env_name} must be a canonical absolute path")
    return path


def load_c6c_deployment_config(env_path: str) -> C6cDeploymentConfig:
    return load_c6c_deployment_config_from_environment(
        effective_environment(env_path)
    )


def load_c6c_deployment_config_from_environment(
    environment: Mapping[str, str],
) -> C6cDeploymentConfig:
    values = dict(environment)
    deployment_environment = values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
    pinvi_environment = values.get("PINVI_ENVIRONMENT", "").strip().lower()

    if deployment_environment not in {"local", "production"}:
        raise DeploymentContractError(
            "KTDM_DEPLOYMENT_ENVIRONMENT must be explicitly set to local or production"
        )
    if pinvi_environment not in {"development", "production"}:
        raise DeploymentContractError(
            "PINVI_ENVIRONMENT must be explicitly set to development or production"
        )
    expected_pinvi_environment = (
        "production" if deployment_environment == "production" else "development"
    )
    if pinvi_environment != expected_pinvi_environment:
        raise DeploymentContractError(
            "KTDM_DEPLOYMENT_ENVIRONMENT must map local->development and "
            "production->production for PINVI_ENVIRONMENT"
        )

    required_text = values.get(_MAP_REQUIRED_ENV, "").strip().lower()
    expected_required = "true" if deployment_environment == "production" else "false"
    if required_text != expected_required:
        raise DeploymentContractError(
            f"{_MAP_REQUIRED_ENV} must be explicitly set to {expected_required} "
            f"for {deployment_environment}"
        )

    map_container_port = _parse_port(
        values.get("KOR_TRAVEL_MAP_API_CONTAINER_PORT", "12701"),
        "KOR_TRAVEL_MAP_API_CONTAINER_PORT",
    )
    pinvi_api_port = _parse_port(values.get("PINVI_API_PORT", "12801"), "PINVI_API_PORT")
    map_ui_port = _parse_port(
        values.get("KOR_TRAVEL_MAP_UI_PORT", "12705"),
        "KOR_TRAVEL_MAP_UI_PORT",
    )
    pinvi_web_port = _parse_port(values.get("PINVI_WEB_PORT", "12805"), "PINVI_WEB_PORT")

    base_url = values.get(
        "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL",
        f"http://127.0.0.1:{map_container_port}",
    )
    contract_generation = values.get("KTDM_C6C_CONTRACT_GENERATION", "").strip().lower()
    if not isinstance(contract_generation, str) or not _CONTRACT_GENERATION_PATTERN.fullmatch(
        contract_generation
    ):
        raise DeploymentContractError(
            "KTDM_C6C_CONTRACT_GENERATION must be an explicit stable identifier"
        )
    if not _map_ui_auth_values_are_valid(values):
        raise DeploymentContractError(
            "Map UI runtime authentication environment is invalid"
        )

    config = C6cDeploymentConfig(
        deployment_environment=deployment_environment,
        pinvi_environment=pinvi_environment,
        base_url=base_url,
        map_container_port=map_container_port,
        read_token=values.get(_MAP_READ_ENV, ""),
        cancel_token=values.get(_MAP_CANCEL_ENV, ""),
        map_container=values.get("KOR_TRAVEL_MAP_API_CONTAINER", "kor-travel-map-api-latest"),
        map_ui_container=values.get(
            "KOR_TRAVEL_MAP_UI_CONTAINER", "kor-travel-map-ui-latest"
        ),
        map_ui_password_hash=values.get(_MAP_UI_PASSWORD_HASH_ENV, ""),
        map_ui_session_secret=values.get(_MAP_UI_SESSION_SECRET_ENV, ""),
        pinvi_container=values.get("PINVI_API_CONTAINER", "pinvi-api-latest"),
        contract_generation=contract_generation,
        smoke=C6cSmokeConfig(
            pinvi_api_base_url=f"http://127.0.0.1:{pinvi_api_port}",
            map_ui_base_url=f"http://127.0.0.1:{map_ui_port}",
            pinvi_web_base_url=f"http://127.0.0.1:{pinvi_web_port}",
            map_ui_username=values.get(_MAP_UI_USERNAME_ENV, ""),
            map_ui_password=values.get(_MAP_UI_PASSWORD_ENV, ""),
            pinvi_admin_email=values.get("KTDM_C6C_PINVI_ADMIN_EMAIL", ""),
            pinvi_admin_password=values.get(_PINVI_ADMIN_PASSWORD_ENV, ""),
            cancel_probe_job_id=values.get("KTDM_C6C_CANCEL_PROBE_JOB_ID", ""),
        ),
    )
    _validate_token_pair(config, require_nonempty=config.production)
    if config.production:
        c6c_state_paths(values)
        _validate_production_config(config, values)
    return config


def _map_ui_auth_values_are_valid(values: Mapping[str, str]) -> bool:
    username = values.get(_MAP_UI_USERNAME_ENV, "")
    password_hash = values.get(_MAP_UI_PASSWORD_HASH_ENV, "")
    session_secret = values.get(_MAP_UI_SESSION_SECRET_ENV, "")
    if not all(
        isinstance(value, str) for value in (username, password_hash, session_secret)
    ):
        return False
    if (
        not username
        or username != username.strip()
        or "\r" in username
        or "\n" in username
    ):
        return False
    match = _MAP_UI_PASSWORD_HASH_PATTERN.fullmatch(password_hash)
    if match is None:
        return False
    try:
        iterations = int(match.group(1))
    except ValueError:
        return False
    if iterations < 100_000:
        return False
    return len(session_secret) >= 32 and not any(
        character.isspace() for character in session_secret
    )


def _compose_resolved_escaped_value(value: str) -> str:
    """Compose resolved JSON이 literal `$`를 표현하는 결정적 값을 반환한다."""

    return value.replace("$", "$$")


def _parse_port(value: str, env_name: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise DeploymentContractError(f"{env_name} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise DeploymentContractError(f"{env_name} must be between 1 and 65535")
    return port


def _validate_token_pair(
    config: C6cDeploymentConfig,
    *,
    require_nonempty: bool,
) -> None:
    _validate_raw_token_pair(
        config.read_token,
        config.cancel_token,
        require_nonempty=require_nonempty,
    )


def _validate_raw_token_pair(
    read_token: str,
    cancel_token: str,
    *,
    require_nonempty: bool,
) -> None:
    if not read_token and not cancel_token:
        if require_nonempty:
            raise DeploymentContractError("production C6c tokens must both be configured")
        return
    if not read_token or not cancel_token:
        raise DeploymentContractError("C6c read and cancel tokens must be configured as a pair")
    for env_name, token in (
        (_MAP_READ_ENV, read_token),
        (_MAP_CANCEL_ENV, cancel_token),
    ):
        if len(token) < 32:
            raise DeploymentContractError(f"{env_name} must contain at least 32 characters")
        if any(character.isspace() for character in token):
            raise DeploymentContractError(f"{env_name} must not contain whitespace")
    if hmac.compare_digest(read_token, cancel_token):
        raise DeploymentContractError("C6c read and cancel tokens must differ")


def _validate_production_config(
    config: C6cDeploymentConfig,
    values: Mapping[str, str],
) -> None:
    if config.map_container != "kor-travel-map-api-latest":
        raise DeploymentContractError(
            "production C6c deployment requires the canonical Map API container identity"
        )
    if config.pinvi_container != "pinvi-api-latest":
        raise DeploymentContractError(
            "production C6c deployment requires the canonical PinVi API container identity"
        )
    if config.map_ui_container != "kor-travel-map-ui-latest":
        raise DeploymentContractError(
            "production C6c deployment requires the canonical Map UI container identity"
        )
    if config.map_container_port != 12701:
        raise DeploymentContractError(
            "production KOR_TRAVEL_MAP_API_CONTAINER_PORT must be exactly 12701"
        )
    if values.get("KTDM_DOCKER_NETWORK_MODE", "").strip().lower() != "host":
        raise DeploymentContractError(
            "production C6c deployment requires KTDM_DOCKER_NETWORK_MODE=host"
        )
    if not values.get("PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL", "").strip():
        raise DeploymentContractError(
            "production C6c deployment requires an explicit PinVi Map base URL"
        )

    try:
        parsed = urlsplit(config.base_url)
        port = parsed.port
    except ValueError as exc:
        raise DeploymentContractError(
            "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL must be a valid host-network URL"
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port != 12701
    ):
        raise DeploymentContractError(
            "production PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL must be "
            "exactly http://127.0.0.1:12701"
        )

    smoke = config.smoke
    identities = (
        (_MAP_UI_USERNAME_ENV, smoke.map_ui_username),
        ("KTDM_C6C_PINVI_ADMIN_EMAIL", smoke.pinvi_admin_email),
    )
    passwords = (
        (_MAP_UI_PASSWORD_ENV, smoke.map_ui_password),
        (_PINVI_ADMIN_PASSWORD_ENV, smoke.pinvi_admin_password),
    )
    for env_name, value in identities:
        if not value:
            raise DeploymentContractError(f"{env_name} must be configured for production smoke")
        if "\r" in value or "\n" in value:
            raise DeploymentContractError(f"{env_name} must not contain line breaks")
    for env_name, value in passwords:
        if len(value) < 16:
            raise DeploymentContractError(
                f"{env_name} must contain at least 16 characters for production smoke"
            )
        if "\r" in value or "\n" in value:
            raise DeploymentContractError(f"{env_name} must not contain line breaks")
    try:
        uuid.UUID(smoke.cancel_probe_job_id)
    except ValueError as exc:
        raise DeploymentContractError(
            "KTDM_C6C_CANCEL_PROBE_JOB_ID must be an owned typed-failure UUID fixture"
        ) from exc


def validate_resolved_compose_secret_isolation(
    resolved: Mapping[str, Any],
    config: C6cDeploymentConfig,
) -> None:
    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("resolved compose config has no services mapping")

    map_service = _service_mapping(services, _MAP_API_SERVICE)
    pinvi_service = _service_mapping(services, _PINVI_API_SERVICE)
    map_ui_service = _service_mapping(services, _MAP_UI_SERVICE)
    map_environment = _environment_mapping(map_service.get("environment"))
    pinvi_environment = _environment_mapping(pinvi_service.get("environment"))
    removed_provider_names = _FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES.intersection(
        map_environment
    )
    if removed_provider_names:
        raise DeploymentContractError(
            "resolved compose Map API includes removed provider runtime environment"
        )
    if map_service.get("command") is not None or map_service.get("entrypoint") is not None:
        raise DeploymentContractError(
            "resolved compose Map API must use the immutable image entrypoint and command"
        )

    for service_name, service in (
        (_MAP_API_SERVICE, map_service),
        (_PINVI_API_SERVICE, pinvi_service),
        (_MAP_UI_SERVICE, map_ui_service),
    ):
        if service.get("network_mode") != "host":
            raise DeploymentContractError(
                f"resolved compose requires host network for {service_name}"
            )
        if service.get("env_file"):
            raise DeploymentContractError(
                f"resolved compose forbids env_file on {service_name}"
            )
    if map_service.get("container_name") != config.map_container:
        raise DeploymentContractError("resolved compose Map API container identity is invalid")
    if pinvi_service.get("container_name") != config.pinvi_container:
        raise DeploymentContractError("resolved compose PinVi API container identity is invalid")
    if map_ui_service.get("container_name") != config.map_ui_container:
        raise DeploymentContractError(
            "resolved compose Map UI container identity is invalid"
        )
    if map_environment.get("KOR_TRAVEL_MAP_API_PORT") != str(config.map_container_port):
        raise DeploymentContractError("resolved compose Map API bind port is invalid")
    if pinvi_environment.get("PINVI_ENVIRONMENT") != "production":
        raise DeploymentContractError("resolved compose PinVi mode must be production")
    if pinvi_environment.get("PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL") != config.base_url:
        raise DeploymentContractError("resolved compose PinVi Map base URL is invalid")

    expected = {
        _MAP_API_SERVICE: {
            _MAP_READ_ENV: _compose_resolved_escaped_value(config.read_token),
            _MAP_CANCEL_ENV: _compose_resolved_escaped_value(config.cancel_token),
            _MAP_REQUIRED_ENV: "true",
        },
        _PINVI_API_SERVICE: {
            _PINVI_READ_ENV: _compose_resolved_escaped_value(config.read_token),
            _PINVI_CANCEL_ENV: _compose_resolved_escaped_value(config.cancel_token),
        },
        _MAP_UI_SERVICE: {
            _MAP_UI_USERNAME_ENV: _compose_resolved_escaped_value(
                config.smoke.map_ui_username
            ),
            _MAP_UI_PASSWORD_HASH_ENV: _compose_resolved_escaped_value(
                config.map_ui_password_hash
            ),
            _MAP_UI_SESSION_SECRET_ENV: _compose_resolved_escaped_value(
                config.map_ui_session_secret
            ),
        },
    }
    for service_name, expected_environment in expected.items():
        service = _service_mapping(services, service_name)
        environment = _environment_mapping(service.get("environment"))
        for env_name, expected_value in expected_environment.items():
            actual = environment.get(env_name)
            if actual is None or not hmac.compare_digest(actual, expected_value):
                raise DeploymentContractError(
                    f"resolved compose does not wire {env_name} to {service_name}"
                )

    allowed_paths = {
        (
            "services",
            _MAP_API_SERVICE,
            "environment",
            _MAP_READ_ENV,
        ): _compose_resolved_escaped_value(config.read_token),
        (
            "services",
            _MAP_API_SERVICE,
            "environment",
            _MAP_CANCEL_ENV,
        ): _compose_resolved_escaped_value(config.cancel_token),
        ("services", _MAP_API_SERVICE, "environment", _MAP_REQUIRED_ENV): "true",
        (
            "services",
            _PINVI_API_SERVICE,
            "environment",
            _PINVI_READ_ENV,
        ): _compose_resolved_escaped_value(config.read_token),
        (
            "services",
            _PINVI_API_SERVICE,
            "environment",
            _PINVI_CANCEL_ENV,
        ): _compose_resolved_escaped_value(config.cancel_token),
        (
            "services",
            _MAP_UI_SERVICE,
            "environment",
            _MAP_UI_USERNAME_ENV,
        ): _compose_resolved_escaped_value(config.smoke.map_ui_username),
        (
            "services",
            _MAP_UI_SERVICE,
            "environment",
            _MAP_UI_PASSWORD_HASH_ENV,
        ): _compose_resolved_escaped_value(config.map_ui_password_hash),
        (
            "services",
            _MAP_UI_SERVICE,
            "environment",
            _MAP_UI_SESSION_SECRET_ENV,
        ): _compose_resolved_escaped_value(config.map_ui_session_secret),
    }
    for path, scalar in _walk_scalars(resolved):
        if path in allowed_paths or (
            path[-1:] == ("<key>",) and path[:-1] in allowed_paths
        ):
            continue
        text = str(scalar)
        if any(
            env_name in text
            for env_name in (
                _OPS_ENV_NAMES
                | _MANAGER_ONLY_CREDENTIAL_NAMES
                | _MAP_UI_AUTH_ENV_NAMES
            )
        ):
            raise DeploymentContractError(
                "C6c protected environment name leaks outside its exact wiring"
            )
        if any(
            escaped and escaped in text
            for escaped in (
                _compose_resolved_escaped_value(config.read_token),
                _compose_resolved_escaped_value(config.cancel_token),
                _compose_resolved_escaped_value(config.map_ui_password_hash),
                _compose_resolved_escaped_value(config.map_ui_session_secret),
                _compose_resolved_escaped_value(config.smoke.map_ui_password),
                _compose_resolved_escaped_value(config.smoke.pinvi_admin_email),
                _compose_resolved_escaped_value(config.smoke.pinvi_admin_password),
                _compose_resolved_escaped_value(config.smoke.cancel_probe_job_id),
                _compose_resolved_escaped_value(config.contract_generation),
            )
        ):
            raise DeploymentContractError(
                "C6c protected value leaks outside its exact wiring"
            )


def validate_resolved_compose_candidate_protected_values(
    resolved: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
    compose_path: str | None = None,
    root_env_path: str | None = None,
) -> tuple[CandidateSystemBindSnapshot, ...]:
    """resolved compose 전체 graph의 C6c 보호 이름·현재 값을 검사한다."""

    _assert_candidate_single_file_boundary(resolved, environment=environment)
    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise ComposeCandidateContractError(
            "resolved compose candidate has no valid services mapping"
        )
    missing_services = _CANDIDATE_REQUIRED_PROTECTED_SERVICES.difference(services)
    if missing_services:
        raise ComposeCandidateContractError(
            "resolved compose candidate is missing required protected services: "
            + ", ".join(sorted(missing_services))
        )
    protected_names = (
        _OPS_ENV_NAMES | _MANAGER_ONLY_CREDENTIAL_NAMES | _MAP_UI_AUTH_ENV_NAMES
    )
    protected_values = tuple(
        _compose_resolved_escaped_value(value)
        for name in _CANDIDATE_PROTECTED_VALUE_ENV_NAMES
        if (value := environment.get(name, ""))
    )
    allowed_paths = {
        ("services", service_name, "environment", target_name)
        for service_name, target_name in _CANDIDATE_ALLOWED_API_ENV_SOURCES
    }

    for service_name in (_MAP_API_SERVICE, _PINVI_API_SERVICE, _MAP_UI_SERVICE):
        service = services[service_name]
        if not isinstance(service, Mapping):
            raise ComposeCandidateContractError(
                f"resolved compose candidate service {service_name} is invalid"
            )
        service_environment = service.get("environment")
        if not isinstance(service_environment, Mapping):
            raise ComposeCandidateContractError(
                f"resolved compose candidate {service_name} has no environment mapping"
            )
        if service_name == _MAP_API_SERVICE and (
            _FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES.intersection(service_environment)
        ):
            raise ComposeCandidateContractError(
                "resolved compose candidate Map API includes removed provider runtime environment"
            )
        if service_name == _MAP_API_SERVICE and (
            service.get("command") is not None or service.get("entrypoint") is not None
        ):
            raise ComposeCandidateContractError(
                "resolved compose candidate Map API must use the immutable image entrypoint and command"
            )
        for (allowed_service, target_name), source_name in (
            _CANDIDATE_ALLOWED_API_ENV_SOURCES.items()
        ):
            if allowed_service != service_name:
                continue
            actual = service_environment.get(target_name)
            expected = _compose_resolved_escaped_value(
                environment.get(source_name, "")
            )
            if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
                raise ComposeCandidateContractError(
                    f"resolved compose candidate {service_name}.{target_name} wiring is invalid"
                )
        if service_name == _MAP_UI_SERVICE and not _map_ui_auth_values_are_valid(
            environment
        ):
            raise ComposeCandidateContractError(
                "resolved compose candidate Map UI authentication is invalid"
            )
        if _env_file_entries(service.get("env_file")):
            raise ComposeCandidateContractError(
                f"resolved compose candidate forbids env_file on {service_name}"
            )

    for path, scalar in _walk_scalars(resolved):
        if path in allowed_paths or (
            path[-1:] == ("<key>",) and path[:-1] in allowed_paths
        ):
            continue
        text = "" if scalar is None else str(scalar)
        if any(name in text for name in protected_names) or any(
            value in text for value in protected_values
        ):
            raise ComposeCandidateContractError(
                "resolved compose candidate leaks a protected C6c reference"
            )

    _validate_candidate_external_resource_references(
        resolved,
        services=services,
        environment=environment,
        protected_names=protected_names,
        protected_values=protected_values,
    )
    compose_directory: Path | None = None
    root_env: Path | None = None
    if compose_path is not None and root_env_path is not None:
        try:
            compose_directory = Path(compose_path).resolve().parent
            root_env = Path(root_env_path).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ComposeCandidateContractError(
                "resolved compose candidate source path cannot be resolved"
            ) from exc
    return _validate_candidate_volume_graph(
        resolved,
        services,
        compose_directory=compose_directory,
        root_env=root_env,
        environment=environment,
        protected_names=protected_names,
        protected_values=protected_values,
        resolved_document=True,
    )


def validate_resolved_compose_image_pair(
    resolved: Mapping[str, Any],
    config: C6cDeploymentConfig,
    pair: CompatibleImagePair,
) -> None:
    validate_resolved_compose_secret_isolation(resolved, config)
    if pair.contract_generation != config.contract_generation:
        raise DeploymentContractError("compatible pair contract generation is not active")
    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("resolved compose config has no services mapping")
    expected_images = {
        _MAP_API_SERVICE: pair.map_image_id,
        _PINVI_API_SERVICE: pair.pinvi_image_id,
    }
    for service_name, expected_image in expected_images.items():
        service = _service_mapping(services, service_name)
        if service.get("image") != expected_image:
            raise DeploymentContractError(
                f"resolved compose immutable image does not match {service_name} manifest"
            )
    validate_resolved_c6c_build_provenance(
        resolved,
        C6cBuildProvenance(
            map_source_revision=pair.map_source_revision,
            pinvi_source_revision=pair.pinvi_source_revision,
        ),
    )


def validate_resolved_c6c_build_provenance(
    resolved: Mapping[str, Any],
    provenance: C6cBuildProvenance,
    *,
    expected_build_contexts: Mapping[str, str] | None = None,
) -> None:
    """production API build arg가 clean checkout 파생값과 정확히 같은지 검사한다."""

    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("resolved compose config has no services mapping")
    expected_args = {
        _MAP_API_SERVICE: {
            "KOR_TRAVEL_MAP_GIT_COMMIT": provenance.map_source_revision,
        },
        _PINVI_API_SERVICE: {
            "PINVI_SOURCE_REVISION": provenance.pinvi_source_revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        },
    }
    expected_dockerfiles = {
        _MAP_API_SERVICE: "docker/api.Dockerfile",
        _PINVI_API_SERVICE: "apps/api/Dockerfile",
    }
    for service_name, service_expected_args in expected_args.items():
        service = _service_mapping(services, service_name)
        build = service.get("build")
        if not isinstance(build, Mapping):
            raise DeploymentContractError(
                f"resolved compose is missing {service_name} build contract"
            )
        if set(build) != {"context", "dockerfile", "args"}:
            raise DeploymentContractError(
                f"resolved compose {service_name} build inputs are not canonical"
            )
        args = build.get("args")
        if not isinstance(args, Mapping) or set(args) != set(service_expected_args):
            raise DeploymentContractError(
                f"resolved compose {service_name} provenance build args are invalid"
            )
        for arg_name, expected_value in service_expected_args.items():
            if args.get(arg_name) != expected_value:
                raise DeploymentContractError(
                    f"resolved compose {service_name} provenance build arg is invalid"
                )
        if expected_build_contexts is None:
            continue
        expected_context = expected_build_contexts.get(service_name)
        if expected_context is None:
            raise DeploymentContractError(
                f"resolved compose {service_name} expected build context is missing"
            )
        try:
            expected_context_path = Path(expected_context).resolve(strict=True)
            context_value = build.get("context")
            if not isinstance(context_value, str) or not Path(context_value).is_absolute():
                raise ValueError("resolved build context must be absolute")
            context_path = Path(context_value).resolve(strict=True)
            dockerfile_value = build.get("dockerfile")
            if not isinstance(dockerfile_value, str):
                raise ValueError("resolved Dockerfile must be a string")
            dockerfile_path = Path(dockerfile_value)
            if not dockerfile_path.is_absolute():
                dockerfile_path = context_path / dockerfile_path
            dockerfile_path = dockerfile_path.resolve(strict=True)
            expected_dockerfile = (
                context_path / expected_dockerfiles[service_name]
            ).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise DeploymentContractError(
                f"resolved compose {service_name} build path is invalid"
            ) from exc
        if (
            context_path != expected_context_path
            or dockerfile_path != expected_dockerfile
            or not expected_dockerfile.is_file()
        ):
            raise DeploymentContractError(
                f"resolved compose {service_name} build path is not the Git snapshot"
            )


def validate_c6c_build_source_wiring(candidate: Mapping[str, Any]) -> None:
    """canonical source가 manager-derived provenance 변수만 참조하는지 검사한다."""

    services = candidate.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("compose source has no services mapping")
    expected = {
        _MAP_API_SERVICE: {
            "context": "${KOR_TRAVEL_MAP_REPO_DIR:-../kor-travel-map}",
            "dockerfile": "docker/api.Dockerfile",
            "args": {
                "KOR_TRAVEL_MAP_GIT_COMMIT": "${KOR_TRAVEL_MAP_GIT_COMMIT:-development}",
            },
        },
        _MAP_UI_SERVICE: {
            "context": "${KOR_TRAVEL_MAP_REPO_DIR:-../kor-travel-map}",
            "dockerfile": "docker/frontend.Dockerfile",
            "args": {
                "KOR_TRAVEL_MAP_GIT_COMMIT": "${KOR_TRAVEL_MAP_GIT_COMMIT:-development}",
                "NEXT_PUBLIC_KOR_TRAVEL_MAP_API": (
                    "${KTDM_PROD_URL_MAP_API:-http://127.0.0.1:"
                    "${KOR_TRAVEL_MAP_API_PORT:-12701}}"
                ),
                "NEXT_PUBLIC_KOR_TRAVEL_MAP_DAGSTER_URL": (
                    "${KTDM_PROD_URL_MAP_DAGSTER:-http://127.0.0.1:"
                    "${KOR_TRAVEL_MAP_DAGSTER_PORT:-12702}}"
                ),
                "NEXT_PUBLIC_KOR_TRAVEL_GEO_BASE_URL": (
                    "${KTDM_PROD_URL_GEO_API:-http://127.0.0.1:12501}"
                ),
                "NEXT_PUBLIC_VWORLD_API_KEY": "${NEXT_PUBLIC_VWORLD_API_KEY:-}",
            },
        },
        "kor-travel-map-dagster": {
            "context": "${KOR_TRAVEL_MAP_REPO_DIR:-../kor-travel-map}",
            "dockerfile": "docker/dagster.Dockerfile",
            "args": {
                "KOR_TRAVEL_MAP_GIT_COMMIT": "${KOR_TRAVEL_MAP_GIT_COMMIT:-development}",
            },
        },
        "kor-travel-map-dagster-daemon": {
            "context": "${KOR_TRAVEL_MAP_REPO_DIR:-../kor-travel-map}",
            "dockerfile": "docker/dagster.Dockerfile",
            "args": {
                "KOR_TRAVEL_MAP_GIT_COMMIT": "${KOR_TRAVEL_MAP_GIT_COMMIT:-development}",
            },
        },
        _PINVI_API_SERVICE: {
            "context": "${PINVI_REPO_DIR:-../pinvi}",
            "dockerfile": "apps/api/Dockerfile",
            "args": {
                "PINVI_SOURCE_REVISION": "${PINVI_SOURCE_REVISION:-development}",
                "PINVI_BUILD_ENVIRONMENT": "${PINVI_BUILD_ENVIRONMENT:-development}",
            },
        },
    }
    for service_name, expected_build in expected.items():
        service = _service_mapping(services, service_name)
        build = service.get("build")
        if not isinstance(build, Mapping) or build != expected_build:
            raise DeploymentContractError(
                f"compose source {service_name} provenance build wiring is invalid"
            )


def _service_mapping(services: Mapping[str, Any], service_name: str) -> Mapping[str, Any]:
    service = services.get(service_name)
    if not isinstance(service, Mapping):
        raise DeploymentContractError(f"resolved compose is missing {service_name}")
    return service


def validate_compose_env_file_isolation(
    compose_paths: Iterable[str],
    *,
    root_env_path: str,
    environment: Mapping[str, str],
) -> None:
    root_env = Path(root_env_path).resolve()
    for compose_path_text in compose_paths:
        compose_path = Path(compose_path_text)
        if not compose_path.exists():
            continue
        try:
            loaded = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise DeploymentContractError(
                f"cannot validate compose source: {compose_path.name}"
            ) from exc
        services = loaded.get("services", {})
        if not isinstance(services, Mapping):
            continue
        for service_name, service in services.items():
            if not isinstance(service, Mapping):
                continue
            for env_file in _env_file_entries(service.get("env_file")):
                if service_name in {
                    _MAP_API_SERVICE,
                    _PINVI_API_SERVICE,
                    _MAP_UI_SERVICE,
                }:
                    raise DeploymentContractError(
                        "C6c protected services must use explicit environment, "
                        "not env_file"
                    )
                expanded = _expand_env_path(env_file, environment)
                path = Path(expanded)
                if not path.is_absolute():
                    path = compose_path.parent / path
                resolved_path = path.resolve()
                if resolved_path == root_env:
                    raise DeploymentContractError(
                        "managed services must not load the manager root .env through env_file"
                    )
                if not resolved_path.exists():
                    continue
                env_keys = dotenv_values(resolved_path, interpolate=False).keys()
                if any(
                    key
                    in (
                        _OPS_ENV_NAMES
                        | _MANAGER_ONLY_CREDENTIAL_NAMES
                        | _MAP_UI_AUTH_ENV_NAMES
                    )
                    for key in env_keys
                ):
                    raise DeploymentContractError(
                        "managed service env_file must not carry C6c ops secrets"
                    )


def validate_compose_candidate_protected_values(
    candidate: Mapping[str, Any],
    *,
    compose_path: str,
    root_env_path: str,
    environment: Mapping[str, str],
    require_api_wiring: bool = True,
    external_file_contents: Mapping[str, bytes] | None = None,
) -> tuple[CandidateSystemBindSnapshot, ...]:
    """파일 반영 전 raw compose 전체의 C6c 보호 이름·값 격리를 검사한다."""

    _assert_candidate_single_file_boundary(candidate, environment=environment)
    services = candidate.get("services")
    if not isinstance(services, Mapping):
        raise ComposeCandidateContractError(
            "compose candidate has no valid services mapping"
        )
    missing_services = _CANDIDATE_REQUIRED_PROTECTED_SERVICES.difference(services)
    if missing_services:
        raise ComposeCandidateContractError(
            "compose candidate is missing required protected services: "
            + ", ".join(sorted(missing_services))
        )
    protected_names = (
        _OPS_ENV_NAMES | _MANAGER_ONLY_CREDENTIAL_NAMES | _MAP_UI_AUTH_ENV_NAMES
    )
    protected_values = tuple(
        value
        for name in _CANDIDATE_PROTECTED_VALUE_ENV_NAMES
        if (value := environment.get(name, ""))
    )
    allowed_paths = {
        ("services", service_name, "environment", target_name)
        for service_name, target_name in _CANDIDATE_ALLOWED_API_ENV_SOURCES
    }

    for service_name in (_MAP_API_SERVICE, _PINVI_API_SERVICE, _MAP_UI_SERVICE):
        service = services[service_name]
        if not isinstance(service, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate service {service_name} is invalid"
            )
        raw_environment = service.get("environment")
        if not isinstance(raw_environment, Mapping):
            if require_api_wiring or raw_environment is not None:
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} must use mapping environment"
                )
            raw_environment = {}
        if service_name == _MAP_API_SERVICE and (
            _FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES.intersection(raw_environment)
        ):
            raise ComposeCandidateContractError(
                "compose candidate Map API includes removed provider runtime environment"
            )
        if service_name == _MAP_API_SERVICE and (
            service.get("command") is not None or service.get("entrypoint") is not None
        ):
            raise ComposeCandidateContractError(
                "compose candidate Map API must use the immutable image entrypoint and command"
            )
        for allowed_service, target_name in _CANDIDATE_ALLOWED_API_ENV_SOURCES:
            if allowed_service != service_name:
                continue
            if not require_api_wiring and target_name not in raw_environment:
                continue
            raw_value = raw_environment.get(target_name)
            canonical = _CANDIDATE_CANONICAL_API_ENV_VALUES[
                (service_name, target_name)
            ]
            if raw_value != canonical:
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name}.{target_name} wiring is invalid"
                )
        if (
            service_name == _MAP_UI_SERVICE
            and require_api_wiring
            and not _map_ui_auth_values_are_valid(environment)
        ):
            raise ComposeCandidateContractError(
                "compose candidate Map UI authentication is invalid"
            )
        if _env_file_entries(service.get("env_file")):
            raise ComposeCandidateContractError(
                f"compose candidate forbids env_file on {service_name}"
            )

    for path, scalar in _walk_scalars(candidate):
        if path in allowed_paths:
            continue
        if path[-1:] == ("<key>",) and path[:-1] in allowed_paths:
            continue
        text = "" if scalar is None else str(scalar)
        if any(name in text for name in protected_names) or any(
            value in text for value in protected_values
        ):
            raise ComposeCandidateContractError(
                "compose candidate leaks a protected C6c reference"
            )

    try:
        compose_directory = Path(compose_path).resolve().parent
        root_env = Path(root_env_path).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ComposeCandidateContractError(
            "compose candidate source path cannot be resolved"
        ) from exc
    _validate_candidate_external_resource_references(
        candidate,
        services=services,
        environment=environment,
        protected_names=protected_names,
        protected_values=protected_values,
    )
    system_bind_snapshots = _validate_candidate_volume_graph(
        candidate,
        services,
        compose_directory=compose_directory,
        root_env=root_env,
        environment=environment,
        protected_names=protected_names,
        protected_values=protected_values,
        allow_undeclared_named_volumes=not require_api_wiring,
    )
    for service_name, service in services.items():
        assert isinstance(service, Mapping)
        for env_file in _env_file_entries(service.get("env_file")):
            expanded = _expand_env_path(env_file, environment)
            resolved_path = _resolve_candidate_path(expanded, compose_directory)
            if resolved_path == root_env:
                raise ComposeCandidateContractError(
                    "compose candidate service must not load the manager root .env"
                )
            try:
                if external_file_contents is None:
                    if not resolved_path.exists():
                        continue
                    _assert_candidate_regular_file(resolved_path)
                    env_values = dotenv_values(resolved_path, interpolate=False)
                else:
                    payload = external_file_contents.get(str(resolved_path))
                    if payload is None:
                        raise ComposeCandidateContractError(
                            "compose candidate external input snapshot is incomplete"
                        )
                    env_values = dotenv_values(
                        stream=StringIO(payload.decode("utf-8")),
                        interpolate=False,
                    )
            except (OSError, UnicodeError, ValueError) as exc:
                raise ComposeCandidateContractError(
                    f"compose candidate cannot validate env_file for {service_name}"
                ) from exc
            for key, raw_value in env_values.items():
                text = "" if raw_value is None else str(raw_value)
                if (
                    any(name in str(key) for name in protected_names)
                    or any(name in text for name in protected_names)
                    or any(value in text for value in protected_values)
                ):
                    raise ComposeCandidateContractError(
                        f"compose candidate env_file leaks C6c data for {service_name}"
                    )

    for collection_name in ("secrets", "configs"):
        collection = candidate.get(collection_name)
        if collection is None:
            continue
        if not isinstance(collection, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate top-level {collection_name} is invalid"
            )
        for _source_name, source in collection.items():
            if not isinstance(source, Mapping) or "file" not in source:
                continue
            raise ComposeCandidateContractError(
                f"compose candidate top-level {collection_name} file resources are unsupported"
            )
    return system_bind_snapshots


def run_map_ops_smoke(config: C6cDeploymentConfig) -> list[dict[str, int | str]]:
    base_url = config.base_url.rstrip("/")
    read_headers = {
        "X-Kor-Travel-Map-Ops-Token": config.read_token,
        "X-Kor-Travel-Map-Ops-Scope": "ops:read",
    }
    cancel_headers = {
        "X-Kor-Travel-Map-Ops-Token": config.cancel_token,
        "X-Kor-Travel-Map-Ops-Scope": "ops:cancel",
    }
    results: list[dict[str, int | str]] = []

    status, payload = _request_json(
        f"{base_url}/v1/ops/datasets",
        method="GET",
        headers=read_headers,
    )
    if status != 200 or not _validate_map_datasets_envelope(payload):
        raise DeploymentContractError("C6c signed canonical read smoke did not return 200 envelope")
    results.append({"name": "signed_read", "status": status})

    status, payload = _request_json(
        f"{base_url}/v1/ops/datasets",
        method="GET",
        headers={"X-Kor-Travel-Map-Ops-Scope": "ops:read"},
        read_error_body=True,
    )
    if status != 401 or not _validate_problem(
        payload,
        expected_status=401,
        expected_code="OPS_TOKEN_REQUIRED",
    ):
        raise DeploymentContractError(
            "C6c tokenless canonical read smoke did not return typed 401"
        )
    results.append({"name": "tokenless_read", "status": status})

    status, payload = _request_json(
        f"{base_url}/v1/ops/datasets",
        method="GET",
        headers=cancel_headers,
        read_error_body=True,
    )
    if status != 403 or not _validate_problem(
        payload,
        expected_status=403,
        expected_code="OPS_SCOPE_FORBIDDEN",
    ):
        raise DeploymentContractError(
            "C6c cancel token canonical read smoke did not return typed 403"
        )
    results.append({"name": "cancel_token_read_rejection", "status": status})

    missing_execution_id = uuid.uuid4()
    cancel_url = (
        f"{base_url}/v1/ops/pipeline/executions/import_job/"
        f"{missing_execution_id}/cancel"
    )
    status, payload = _request_json(
        cancel_url,
        method="POST",
        headers={
            **read_headers,
            "X-Kor-Travel-Map-Ops-Scope": "ops:cancel",
        },
        read_error_body=True,
    )
    if status != 403 or not _validate_problem(
        payload,
        expected_status=403,
        expected_code="OPS_SCOPE_FORBIDDEN",
    ):
        raise DeploymentContractError(
            "C6c read token exact cancel smoke did not return typed 403"
        )
    results.append({"name": "read_token_cancel_rejection", "status": status})

    status, payload = _request_json(
        cancel_url,
        method="POST",
        headers=cancel_headers,
        read_error_body=True,
    )
    if status != 404 or not _validate_problem(
        payload,
        expected_status=404,
        expected_code="PIPELINE_EXECUTION_NOT_FOUND",
    ):
        raise DeploymentContractError(
            "C6c exact import-job cancel authentication smoke did not reach the typed 404 domain boundary"
        )
    results.append({"name": "cancel_auth_boundary", "status": status})

    status, payload = _request_json(
        f"{base_url}/v1/ops/pipeline/schedules/__c6c_auth_probe__/commands",
        method="POST",
        headers={
            **cancel_headers,
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        },
        body=b'{"command":"start","reason":"c6c authorization preflight"}',
        read_error_body=True,
    )
    if status != 403 or not _validate_problem(
        payload,
        expected_status=403,
        expected_code="OPS_SCOPE_FORBIDDEN",
    ):
        raise DeploymentContractError(
            "C6c cancel token non-cancel mutation smoke did not return typed 403"
        )
    results.append({"name": "cancel_scope_rejection", "status": status})
    return results


def run_pinvi_canonical_smoke(
    config: C6cDeploymentConfig,
    *,
    cancel_probe_state: PinviCancelProbeState | None = None,
) -> list[dict[str, int | str]]:
    """PinVi admin session이 canonical Map read/cancel 계약을 보존하는지 검사한다."""

    smoke = config.smoke
    opener = _cookie_opener(follow_redirects=False)
    login = _session_request(
        opener,
        f"{smoke.pinvi_api_base_url}/auth/login",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {"email": smoke.pinvi_admin_email, "password": smoke.pinvi_admin_password}
        ).encode(),
        read_error_body=False,
    )
    if (
        login.status != 200
        or not login.set_cookie
        or not _pinvi_envelope_ok(login.payload)
    ):
        raise DeploymentContractError("C6c PinVi admin login smoke failed")

    results: list[dict[str, int | str]] = [{"name": "pinvi_login", "status": 200}]
    for name, path in (
        ("pinvi_etl_summary", "/admin/etl/summary"),
        ("pinvi_provider_sync", "/admin/provider-sync"),
    ):
        response = _session_request(
            opener,
            f"{smoke.pinvi_api_base_url}{path}",
            method="GET",
            headers={},
            read_error_body=False,
        )
        validator = (
            _validate_pinvi_etl_summary
            if name == "pinvi_etl_summary"
            else _validate_pinvi_provider_sync
        )
        if response.status != 200 or not validator(response.payload):
            raise DeploymentContractError(f"C6c {name} canonical envelope smoke failed")
        results.append({"name": name, "status": 200})

    state = cancel_probe_state or PinviCancelProbeState()
    if state.result is None:
        if state.attempted:
            raise DeploymentContractError(
                "C6c destructive PinVi cancel probe cannot be repeated after an uncertain result"
            )
        state.attempted = True
        cancel = _session_request(
            opener,
            (
                f"{smoke.pinvi_api_base_url}/admin/provider-sync/import-jobs/"
                f"{smoke.cancel_probe_job_id}/cancel"
            ),
            method="POST",
            headers={"Content-Type": "application/json"},
            body=(
                b'{"access_reason":"c6c compatible-pair contract probe",'
                b'"kor_travel_map_reason":"c6c owned typed-failure fixture"}'
            ),
            read_error_body=True,
        )
        error = (
            cancel.payload.get("error")
            if isinstance(cancel.payload, Mapping)
            else None
        )
        error_code = error.get("code") if isinstance(error, Mapping) else None
        if not isinstance(error_code, str):
            raise DeploymentContractError(
                "C6c PinVi cancel typed error/Retry-After preservation smoke failed"
            )
        expected_pairs = {
            (409, "PIPELINE_CANCELLATION_IN_PROGRESS"): True,
            (409, "PIPELINE_CANCELLATION_UNSAFE"): False,
            (502, "DAGSTER_TERMINATE_FAILED"): True,
            (503, "DAGSTER_UNAVAILABLE"): True,
            (503, "DAGSTER_TERMINATION_TIMEOUT"): True,
        }
        expected_retry_after = expected_pairs.get((cancel.status, error_code))
        retry_after_present = (
            cancel.retry_after is not None
            if cancel.retry_after_present is None
            else cancel.retry_after_present
        )
        if (
            expected_retry_after is None
            or (
                expected_retry_after
                and (
                    not retry_after_present
                    or cancel.retry_after is None
                    or cancel.retry_after <= 0
                )
            )
            or (
                not expected_retry_after
                and (retry_after_present or cancel.retry_after is not None)
            )
        ):
            raise DeploymentContractError(
                "C6c PinVi cancel typed error/Retry-After preservation smoke failed"
            )
        _validate_owned_cancel_error_details(
            error.get("details") if isinstance(error, Mapping) else None,
            expected_status=cancel.status,
            expected_code=error_code,
            expected_root_id=smoke.cancel_probe_job_id,
        )
        state.result = {
            "name": "pinvi_cancel_error",
            "status": cancel.status,
            "code": error_code,
        }
    if not _validate_pinvi_cancel_probe_result(state.result):
        raise DeploymentContractError(
            "C6c cached PinVi cancel probe evidence is invalid"
        )
    assert state.result is not None
    results.append(dict(state.result))

    logout = _session_request(
        opener,
        f"{smoke.pinvi_api_base_url}/auth/logout",
        method="POST",
        headers={},
        read_error_body=False,
    )
    if logout.status != 204 or not logout.set_cookie:
        raise DeploymentContractError("C6c PinVi admin logout smoke failed")
    protected = _session_request(
        opener,
        f"{smoke.pinvi_api_base_url}/admin/provider-sync",
        method="GET",
        headers={},
        read_error_body=False,
    )
    if protected.status != 401:
        raise DeploymentContractError("C6c PinVi post-logout protection smoke failed")
    results.extend(
        [
            {"name": "pinvi_logout", "status": logout.status},
            {"name": "pinvi_post_logout_protected", "status": protected.status},
        ]
    )
    return results


def _validate_pinvi_cancel_probe_result(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"name", "status", "code"}:
        return False
    status = value.get("status")
    code = value.get("code")
    if type(status) is not int or not isinstance(code, str):
        return False
    return (
        value.get("name") == "pinvi_cancel_error"
        and (status, code)
        in {
            (409, "PIPELINE_CANCELLATION_IN_PROGRESS"),
            (409, "PIPELINE_CANCELLATION_UNSAFE"),
            (502, "DAGSTER_TERMINATE_FAILED"),
            (503, "DAGSTER_UNAVAILABLE"),
            (503, "DAGSTER_TERMINATION_TIMEOUT"),
        }
    )


def _validate_owned_cancel_error_details(
    details: Any,
    *,
    expected_status: int,
    expected_code: str,
    expected_root_id: str,
) -> None:
    if not isinstance(details, Mapping):
        raise DeploymentContractError("C6c PinVi cancel fixture details are missing")
    expected_attempt = {
        (409, "PIPELINE_CANCELLATION_IN_PROGRESS"): ("in_progress", False),
        (409, "PIPELINE_CANCELLATION_UNSAFE"): ("failed", False),
        (502, "DAGSTER_TERMINATE_FAILED"): ("retryable", True),
        (503, "DAGSTER_UNAVAILABLE"): ("retryable", True),
        (503, "DAGSTER_TERMINATION_TIMEOUT"): ("retryable", True),
    }.get((expected_status, expected_code))
    if expected_attempt is None:
        raise DeploymentContractError(
            "C6c PinVi cancel fixture status/code pair is unsupported"
        )
    if (
        expected_code == "PIPELINE_CANCELLATION_IN_PROGRESS"
        and set(details) == {"root", "cancellation"}
    ):
        root = details.get("root")
        if (
            not isinstance(root, Mapping)
            or set(root) != {"kind", "id"}
            or root.get("kind") != "import_job"
            or root.get("id") != expected_root_id
            or not _is_uuid(root.get("id"))
            or details.get("cancellation") is not None
        ):
            raise DeploymentContractError(
                "C6c PinVi root-only cancellation detail is invalid"
            )
        return

    status_text, retryable = expected_attempt
    expected_fields = {
        "cancellation_id",
        "previous_cancellation_id",
        "root",
        "status",
        "requested_at",
        "requested_by",
        "reason",
        "error",
        "updated_at",
        "finished_at",
        "retryable",
        "unresolved_member_count",
        "members",
        "dagster_runs",
        "committed_data_rolled_back",
        "warnings",
    }
    root = details.get("root")
    if (
        set(details) != expected_fields
        or not isinstance(root, Mapping)
        or set(root) != {"kind", "id"}
        or root.get("kind") != "import_job"
        or root.get("id") != expected_root_id
        or details.get("status") != status_text
        or details.get("retryable") is not retryable
        or not _is_uuid(details.get("cancellation_id"))
        or not _is_nullable_uuid(details.get("previous_cancellation_id"))
        or not _is_iso8601(details.get("requested_at"))
        or not isinstance(details.get("requested_by"), str)
        or not bool(details["requested_by"])
        or not isinstance(details.get("reason"), (str, type(None)))
        or not _validate_cancellation_error(details.get("error"))
        or not _is_iso8601(details.get("updated_at"))
        or not _is_nullable_iso8601(details.get("finished_at"))
        or details.get("committed_data_rolled_back") is not False
    ):
        raise DeploymentContractError(
            "C6c PinVi cancel attempt lifecycle contract diverged"
        )
    if retryable and (
        not isinstance(details.get("error"), Mapping)
        or details["error"].get("code") != expected_code
    ):
        raise DeploymentContractError(
            "C6c retryable cancel attempt requires a structured error"
        )
    cancellation_id = str(details["cancellation_id"])
    previous_cancellation_id = details.get("previous_cancellation_id")
    finished_at = details.get("finished_at")
    attempt_error = details.get("error")
    if (
        (
            status_text == "in_progress"
            and (finished_at is not None or attempt_error is not None)
        )
        or (
            status_text in {"retryable", "failed"}
            and (finished_at is None or attempt_error is None)
        )
        or previous_cancellation_id == cancellation_id
    ):
        raise DeploymentContractError(
            "C6c PinVi cancel attempt DB lifecycle contract diverged"
        )
    unresolved = details.get("unresolved_member_count")
    members = details.get("members")
    dagster_runs = details.get("dagster_runs")
    warnings = details.get("warnings")
    unresolved_results = {"pending", "cancel_failed"}
    if not isinstance(members, list) or not all(
        _validate_cancellation_member(member) for member in members
    ):
        raise DeploymentContractError("C6c PinVi cancel members are invalid")
    if not isinstance(dagster_runs, list) or not all(
        _validate_cancellation_run(run) for run in dagster_runs
    ):
        raise DeploymentContractError("C6c PinVi cancel Dagster runs are invalid")
    member_ids = [str(member["job_id"]) for member in members]
    unresolved_count = sum(
        member.get("result") in unresolved_results for member in members
    )
    owned_members = [
        member for member in members if member.get("job_id") == expected_root_id
    ]
    run_ids = [str(run["dagster_run_id"]) for run in dagster_runs]
    member_run_ids = {
        str(member["dagster_run_id"])
        for member in members
        if member.get("dagster_run_id") is not None
    }
    if (
        not _is_nonnegative_int(unresolved)
        or not member_ids
        or len(member_ids) != len(set(member_ids))
        or (
            previous_cancellation_id is None
            and len(owned_members) != 1
        )
        or (
            previous_cancellation_id is not None
            and len(owned_members) > 1
        )
        or (
            previous_cancellation_id is not None
            and any(
                member.get("requires_run_termination") is not True
                for member in members
            )
        )
        or unresolved != unresolved_count
        or len(run_ids) != len(set(run_ids))
        or set(run_ids) != member_run_ids
        or not isinstance(warnings, list)
        or not warnings
        or not all(isinstance(item, str) for item in warnings)
    ):
        raise DeploymentContractError(
            "C6c PinVi cancel member/run/warning detail is invalid"
        )
    if status_text == "retryable" and (
        any(member.get("result") == "pending" for member in members)
        or any(run.get("result") == "pending" for run in dagster_runs)
        or not any(member.get("result") == "cancel_failed" for member in members)
    ):
        raise DeploymentContractError(
            "C6c retryable cancellation lifecycle is invalid"
        )
    run_by_id = {str(run["dagster_run_id"]): run for run in dagster_runs}
    canonical_error_codes = (
        _RETRYABLE_CANCELLATION_ERROR_CODES | _FAILED_CANCELLATION_ERROR_CODES
    )
    if status_text == "retryable":
        for member in members:
            if member.get("result") != "cancel_failed":
                continue
            run_id = member.get("dagster_run_id")
            member_error = member.get("error")
            if (
                member.get("requires_run_termination") is not True
                or not isinstance(run_id, str)
                or not isinstance(member_error, Mapping)
                or member_error.get("code")
                not in _RETRYABLE_CANCELLATION_ERROR_CODES
            ):
                raise DeploymentContractError(
                    "C6c retryable cancellation member evidence is invalid"
                )
            run = run_by_id[run_id]
            run_error = run.get("error")
            if (
                run.get("result") != "cancel_failed"
                or not isinstance(run_error, Mapping)
                or run_error.get("code")
                not in _RETRYABLE_CANCELLATION_ERROR_CODES
            ):
                raise DeploymentContractError(
                    "C6c retryable cancellation run evidence is invalid"
                )
    if status_text == "failed":
        attempt_error = details.get("error")
        if (
            not isinstance(attempt_error, Mapping)
            or attempt_error.get("code") not in _FAILED_CANCELLATION_ERROR_CODES
        ):
            raise DeploymentContractError(
                "C6c failed cancellation attempt error is invalid"
            )
        for member in members:
            if member.get("result") != "cancel_failed":
                continue
            member_error = member.get("error")
            member_error_code = (
                member_error.get("code")
                if isinstance(member_error, Mapping)
                else None
            )
            if member_error_code in _RETRYABLE_CANCELLATION_ERROR_CODES:
                run_id = member.get("dagster_run_id")
                if (
                    member.get("requires_run_termination") is not True
                    or not isinstance(run_id, str)
                    or run_by_id[run_id].get("result") != "cancel_failed"
                    or not isinstance(run_by_id[run_id].get("error"), Mapping)
                    or run_by_id[run_id]["error"].get("code")
                    not in _RETRYABLE_CANCELLATION_ERROR_CODES
                ):
                    raise DeploymentContractError(
                        "C6c failed cancellation retryable evidence is invalid"
                    )
                continue
            if member_error_code not in _FAILED_CANCELLATION_ERROR_CODES or (
                member.get("initial_status") != "running"
                and member.get("requires_run_termination") is not True
            ):
                raise DeploymentContractError(
                    "C6c failed cancellation member error is invalid"
                )
        for run in dagster_runs:
            if run.get("result") != "cancel_failed":
                continue
            run_error = run.get("error")
            if (
                not isinstance(run_error, Mapping)
                or run_error.get("code") not in canonical_error_codes
            ):
                raise DeploymentContractError(
                    "C6c failed cancellation run error is invalid"
                )
    if status_text == "in_progress":
        attempt_error = details.get("error")
        if (
            isinstance(attempt_error, Mapping)
            and attempt_error.get("code") not in canonical_error_codes
        ):
            raise DeploymentContractError(
                "C6c in-progress cancellation attempt error is invalid"
            )
        for member in members:
            if member.get("result") != "cancel_failed":
                continue
            member_error = member.get("error")
            if (
                not isinstance(member_error, Mapping)
                or member_error.get("code") not in canonical_error_codes
            ):
                raise DeploymentContractError(
                    "C6c in-progress cancellation member error is invalid"
                )
            run_id = member.get("dagster_run_id")
            member_error_code = member_error.get("code")
            if not isinstance(run_id, str):
                if member_error_code not in _FAILED_CANCELLATION_ERROR_CODES:
                    raise DeploymentContractError(
                        "C6c in-progress runless failure must be definitive"
                    )
                continue
            run = run_by_id[run_id]
            if run.get("result") in {"cancelled", "already_terminal"}:
                continue
            run_error = run.get("error")
            if (
                run.get("result") != "cancel_failed"
                or not isinstance(run_error, Mapping)
                or run_error.get("code") not in canonical_error_codes
            ):
                raise DeploymentContractError(
                    "C6c in-progress failed member has impossible run evidence"
                )
            expected_run_error_codes = (
                _RETRYABLE_CANCELLATION_ERROR_CODES
                if member_error_code in _RETRYABLE_CANCELLATION_ERROR_CODES
                else _FAILED_CANCELLATION_ERROR_CODES
            )
            if run_error.get("code") not in expected_run_error_codes:
                raise DeploymentContractError(
                    "C6c in-progress member/run failure policies must match"
                )
    success_tracking_run_ids = {
        str(member["dagster_run_id"])
        for member in members
        if member.get("dagster_run_id") is not None
        and member.get("operation_kind") == "provider_feature_load"
        and member.get("initial_status") != "done"
    }
    for member in members:
        result = str(member.get("result"))
        if result == "pending":
            continue
        if member.get("requires_run_termination") is not True:
            initial_status = member.get("initial_status")
            if initial_status == "queued" and result != "cancelled":
                raise DeploymentContractError(
                    "C6c queued cancellation requires the explicit DB cancel path"
                )
            if initial_status in {"done", "failed", "cancelled"} and (
                result != "already_terminal"
                or member.get("terminal_status") != initial_status
            ):
                raise DeploymentContractError(
                    "C6c initially terminal member must remain already-terminal"
                )
            continue
        run_id = member.get("dagster_run_id")
        if not isinstance(run_id, str):
            raise DeploymentContractError(
                "C6c run-backed cancellation member has no Dagster run"
            )
        run = run_by_id[run_id]
        if result == "cancel_failed":
            transient_terminal_run = (
                status_text == "in_progress"
                and details.get("error") is None
                and details.get("finished_at") is None
                and run.get("result") in {"cancelled", "already_terminal"}
            )
            if (
                status_text != "failed"
                and run.get("result") != "cancel_failed"
                and not transient_terminal_run
            ):
                raise DeploymentContractError(
                    "C6c run-backed failed member requires a failed run snapshot"
                )
            continue
        expected_run_terminal = {
            ("cancelled", "cancelled"): ("cancelled", "CANCELED"),
            ("already_terminal", "done"): ("already_terminal", "SUCCESS"),
            ("already_terminal", "failed"): ("already_terminal", "FAILURE"),
        }.get((result, member.get("terminal_status")))
        actual_run_terminal = (run.get("result"), run.get("terminal_status"))
        tracking_failure_after_success = (
            result == "already_terminal"
            and member.get("terminal_status") == "failed"
            and member.get("operation_kind")
            in {"provider_feature_load_run", "provider_feature_load"}
            and run_id in success_tracking_run_ids
            and actual_run_terminal == ("already_terminal", "SUCCESS")
        )
        if (
            expected_run_terminal != actual_run_terminal
            and not tracking_failure_after_success
        ):
            raise DeploymentContractError(
                "C6c resolved member status does not match Dagster terminal result"
            )


def _validate_cancellation_error(value: Any) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, Mapping)
        and set(value) == {"code", "message", "details"}
        and isinstance(value.get("code"), str)
        and bool(value["code"].strip())
        and isinstance(value.get("message"), str)
        and bool(value["message"].strip())
        and isinstance(value.get("details"), (Mapping, type(None)))
    )


def _validate_cancellation_member(value: Any) -> bool:
    expected_fields = {
        "job_id",
        "dagster_run_id",
        "operation_kind",
        "requires_run_termination",
        "initial_status",
        "result",
        "terminal_status",
        "error",
        "updated_at",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        return False
    result = value.get("result")
    terminal_status = value.get("terminal_status")
    error = value.get("error")
    dagster_run_id = value.get("dagster_run_id")
    operation_kind = value.get("operation_kind")
    initial_status = value.get("initial_status")
    expected_run_termination = dagster_run_id is not None and (
        initial_status == "running"
        or (
            initial_status == "queued"
            and operation_kind
            in {"provider_feature_load_run", "provider_feature_load"}
        )
    )
    return (
        _is_uuid(value.get("job_id"))
        and isinstance(dagster_run_id, (str, type(None)))
        and isinstance(operation_kind, (str, type(None)))
        and (
            operation_kind is None
            or (bool(operation_kind) and operation_kind == operation_kind.strip())
        )
        and value.get("requires_run_termination") is expected_run_termination
        and isinstance(initial_status, str)
        and bool(value["initial_status"])
        and result in {"pending", "cancelled", "already_terminal", "cancel_failed"}
        and isinstance(terminal_status, (str, type(None)))
        and _validate_cancellation_error(error)
        and _is_iso8601(value.get("updated_at"))
        and (
            (result == "pending" and terminal_status is None and error is None)
            or (result == "cancelled" and terminal_status == "cancelled" and error is None)
            or (
                result == "already_terminal"
                and terminal_status in {"done", "failed", "cancelled"}
                and error is None
            )
            or (result == "cancel_failed" and terminal_status is None and error is not None)
        )
    )


def _validate_cancellation_run(value: Any) -> bool:
    expected_fields = {
        "dagster_run_id",
        "initial_status",
        "termination_reserved_at",
        "result",
        "terminal_status",
        "error",
        "engine_started_at",
        "engine_finished_at",
        "updated_at",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        return False
    result = value.get("result")
    terminal_status = value.get("terminal_status")
    error = value.get("error")
    engine_started_at = value.get("engine_started_at")
    engine_finished_at = value.get("engine_finished_at")
    return (
        isinstance(value.get("dagster_run_id"), str)
        and bool(value["dagster_run_id"])
        and isinstance(value.get("initial_status"), (str, type(None)))
        and _is_nullable_iso8601(value.get("termination_reserved_at"))
        and (
            value.get("termination_reserved_at") is None
            or value.get("initial_status") is not None
        )
        and result in {"pending", "cancelled", "already_terminal", "cancel_failed"}
        and isinstance(terminal_status, (str, type(None)))
        and _validate_cancellation_error(error)
        and _is_nullable_iso8601(engine_started_at)
        and _is_nullable_iso8601(engine_finished_at)
        and _validate_cancellation_engine_times(
            result,
            engine_started_at,
            engine_finished_at,
        )
        and _is_iso8601(value.get("updated_at"))
        and (
            (result == "pending" and terminal_status is None and error is None)
            or (result == "cancelled" and terminal_status == "CANCELED" and error is None)
            or (
                result == "already_terminal"
                and terminal_status in {None, "SUCCESS", "FAILURE"}
                and error is None
            )
            or (result == "cancel_failed" and terminal_status is None and error is not None)
        )
    )


def _validate_cancellation_engine_times(
    result: Any,
    engine_started_at: Any,
    engine_finished_at: Any,
) -> bool:
    if engine_started_at is None and engine_finished_at is None:
        return True
    if result not in {"cancelled", "already_terminal"} or engine_finished_at is None:
        return False
    finished = _parse_iso8601_datetime(engine_finished_at)
    started = _parse_iso8601_datetime(engine_started_at)
    return finished is not None and (
        engine_started_at is None
        or (started is not None and started <= finished)
    )


def run_map_ui_auth_preflight(
    config: C6cDeploymentConfig,
) -> list[dict[str, int | str]]:
    """현재 plaintext credential로 Map UI auth lifecycle만 비파괴 검사한다."""

    smoke = config.smoke
    opener = _cookie_opener(follow_redirects=False)
    origin = smoke.map_ui_base_url
    login = _session_request(
        opener,
        f"{origin}/api/auth/login",
        method="POST",
        headers={"Content-Type": "application/json", "Origin": origin},
        body=json.dumps(
            {
                "username": smoke.map_ui_username,
                "password": smoke.map_ui_password,
                "next": "/ops/providers",
            }
        ).encode(),
        read_error_body=False,
    )
    if login.status != 200 or not login.set_cookie:
        raise DeploymentContractError("C6c Map UI login smoke failed")
    protected = _session_request(
        opener,
        f"{origin}/ops/providers",
        method="GET",
        headers={},
        read_error_body=False,
    )
    if protected.status != 200:
        raise DeploymentContractError("C6c Map UI protected-page smoke failed")
    logout = _session_request(
        opener,
        f"{origin}/api/auth/logout",
        method="POST",
        headers={"Origin": origin},
        read_error_body=False,
    )
    if logout.status != 200 or not logout.set_cookie:
        raise DeploymentContractError("C6c Map UI logout smoke failed")
    post_logout = _session_request(
        opener,
        f"{origin}/ops/providers",
        method="GET",
        headers={},
        read_error_body=False,
    )
    redirect_path = urlsplit(post_logout.location or "").path
    if post_logout.status not in {302, 303, 307, 308} or redirect_path != "/login":
        raise DeploymentContractError("C6c Map UI post-logout protection smoke failed")
    return [
        {"name": "map_ui_login", "status": login.status},
        {"name": "map_ui_protected", "status": protected.status},
        {"name": "map_ui_logout", "status": logout.status},
        {"name": "map_ui_post_logout", "status": post_logout.status},
    ]


def run_ui_auth_smoke(config: C6cDeploymentConfig) -> list[dict[str, int | str]]:
    """필수 app readiness 뒤 Map UI auth lifecycle와 PinVi login shell을 검사한다."""

    map_ui_smoke = run_map_ui_auth_preflight(config)
    smoke = config.smoke

    pinvi_login_shell = _session_request(
        _cookie_opener(follow_redirects=False),
        f"{smoke.pinvi_web_base_url}/admin/login",
        method="GET",
        headers={},
        read_error_body=False,
    )
    if (
        pinvi_login_shell.status != 200
        or not (pinvi_login_shell.content_type or "").lower().startswith("text/html")
        or not pinvi_login_shell.body_text
        or 'data-testid="admin-login-form"' not in pinvi_login_shell.body_text
        or "/_next/static/" not in pinvi_login_shell.body_text
    ):
        raise DeploymentContractError("C6c PinVi Web login shell smoke failed")
    return [
        *map_ui_smoke,
        {"name": "pinvi_web_login", "status": pinvi_login_shell.status},
    ]


def _pinvi_envelope_ok(payload: Any | None) -> bool:
    return isinstance(payload, Mapping) and isinstance(payload.get("data"), Mapping)


def _validate_problem(
    payload: Any | None,
    *,
    expected_status: int,
    expected_code: str,
) -> bool:
    return (
        isinstance(payload, Mapping)
        and type(payload.get("status")) is int
        and payload.get("status") == expected_status
        and payload.get("code") == expected_code
        and isinstance(payload.get("type"), str)
        and bool(payload["type"])
        and isinstance(payload.get("title"), str)
        and bool(payload["title"])
        and isinstance(payload.get("detail"), str)
        and bool(payload["detail"])
        and isinstance(payload.get("request_id"), str)
        and bool(payload["request_id"])
        and isinstance(payload.get("errors"), list)
    )


def _validate_map_datasets_envelope(payload: Any | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    data = payload.get("data")
    meta = payload.get("meta")
    return (
        isinstance(data, Mapping)
        and isinstance(data.get("items"), list)
        and data.get("schedule_source_status") in {"ok", "unavailable", "error"}
        and isinstance(data.get("schedule_source_errors"), list)
        and all(isinstance(item, str) for item in data["schedule_source_errors"])
        and data.get("execution_coverage")
        == "db_recorded_canonical_operations"
        and all(_validate_map_dataset_row(item) for item in data["items"])
        and isinstance(meta, Mapping)
        and _is_nonnegative_number(meta.get("duration_ms"))
        and isinstance(meta.get("request_id"), str)
    )


def _validate_pinvi_etl_summary(payload: Any | None) -> bool:
    if not _pinvi_envelope_ok(payload):
        return False
    if not isinstance(payload, Mapping):
        return False
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return False
    pinvi = data.get("pinvi")
    kor_travel_map = data.get("kor_travel_map")
    status_values = {"ok", "degraded", "down", "unknown"}
    return (
        _is_iso8601(data.get("generated_at"))
        and isinstance(pinvi, Mapping)
        and pinvi.get("status") in status_values
        and all(
            _is_nonnegative_int(pinvi.get(field))
            for field in (
                "repository_count",
                "job_count",
                "asset_count",
                "schedule_count",
                "sensor_count",
            )
        )
        and all(
            isinstance(pinvi.get(field), list)
            for field in (
                "repositories",
                "recent_runs",
                "assets",
                "jobs",
                "schedules",
                "sensors",
            )
        )
        and all(_validate_dagster_run(item) for item in pinvi["recent_runs"])
        and all(_validate_dagster_repository(item) for item in pinvi["repositories"])
        and all(_validate_etl_asset(item) for item in pinvi["assets"])
        and all(_validate_etl_job(item) for item in pinvi["jobs"])
        and all(_validate_etl_schedule(item) for item in pinvi["schedules"])
        and all(_validate_etl_sensor(item) for item in pinvi["sensors"])
        and _is_nullable_iso8601(pinvi.get("checked_at"))
        and isinstance(kor_travel_map, Mapping)
        and kor_travel_map.get("status") in status_values
        and isinstance(kor_travel_map.get("dagster_status"), str)
        and _validate_nonnegative_int_mapping(kor_travel_map.get("run_counts"))
        and _validate_nonnegative_int_mapping(
            kor_travel_map.get("operations_by_status"),
            allowed_keys=_OPERATION_STATES,
        )
        and all(
            isinstance(kor_travel_map.get(field), list)
            for field in (
                "dagster_errors",
                "recent_runs",
                "recent_import_jobs",
                "errors",
            )
        )
        and all(_validate_dagster_run(item) for item in kor_travel_map["recent_runs"])
        and all(
            _validate_provider_import_job(item)
            for item in kor_travel_map["recent_import_jobs"]
        )
        and all(isinstance(item, str) for item in kor_travel_map["dagster_errors"])
        and all(isinstance(item, str) for item in kor_travel_map["errors"])
    )


def _validate_pinvi_provider_sync(payload: Any | None) -> bool:
    if not _pinvi_envelope_ok(payload):
        return False
    if not isinstance(payload, Mapping):
        return False
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return False
    items = data.get("items")
    total = data.get("total")
    errors = data.get("schedule_source_errors")
    if (
        not isinstance(items, list)
        or not _is_nonnegative_int(total)
        or total != len(items)
        or data.get("schedule_source_status") not in {"ok", "unavailable", "error"}
        or not isinstance(errors, list)
        or not all(isinstance(error, str) for error in errors)
    ):
        return False
    for item in items:
        if (
            not isinstance(item, Mapping)
            or not all(
                isinstance(item.get(field), str) and bool(item[field])
                for field in ("provider", "dataset_key")
            )
            or not _is_canonical_sync_scope(item.get("sync_scope"))
            or item.get("status") not in _PROVIDER_SYNC_STATUSES
            or not _is_nonnegative_int(item.get("consecutive_failures"))
            or not _validate_provider_links(item.get("links"))
            or not all(
                _is_nullable_iso8601(item.get(field))
                for field in (
                    "last_success_at",
                    "last_failure_at",
                    "eligible_after",
                    "schedule_next_scheduled_at",
                )
            )
            or not isinstance(item.get("refresh_policy"), (Mapping, type(None)))
        ):
            return False
    return True


def _is_canonical_sync_scope(value: Any) -> bool:
    if value in {"dataset_wide", "target_grids"}:
        return True
    if not isinstance(value, str) or not value.startswith("external_system:"):
        return False
    external_system = value.removeprefix("external_system:")
    return (
        bool(external_system)
        and external_system == external_system.strip()
        and len(external_system) <= 112
    )


def _validate_map_dataset_row(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    provider = value.get("provider")
    dataset_key = value.get("dataset_key")
    sync_scope = value.get("sync_scope")
    freshness = value.get("freshness")
    schedule = value.get("schedule")
    dataset_issues = value.get("dataset_issues")
    provider_issues = value.get("provider_issues")
    catalog_state = value.get("catalog_state")
    expected_detail_url = (
        "/v1/ops/datasets/detail?"
        + urlencode(
            {
                "provider": provider,
                "dataset_key": dataset_key,
                "sync_scope": sync_scope,
            },
            quote_via=quote,
        )
    )
    return (
        all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("provider", "dataset_key", "detail_url")
        )
        and value.get("detail_url") == expected_detail_url
        and _is_canonical_sync_scope(sync_scope)
        and value.get("status") in _PROVIDER_SYNC_STATUSES
        and all(
            _is_nullable_iso8601(value.get(field))
            for field in ("last_success_at", "last_failure_at", "eligible_after")
        )
        and _is_nonnegative_int(value.get("consecutive_failures"))
        and _validate_dataset_freshness(freshness)
        and _validate_dataset_schedule(schedule)
        and _validate_dataset_execution(
            value.get("latest_execution"),
            provider=provider,
            dataset_key=dataset_key,
            sync_scope=sync_scope,
            active=False,
        )
        and _validate_dataset_execution(
            value.get("active_execution"),
            provider=provider,
            dataset_key=dataset_key,
            sync_scope=sync_scope,
            active=True,
        )
        and catalog_state in {"canonical", "orphan"}
        and isinstance(value.get("mutable"), bool)
        and _validate_refresh_policy(
            value.get("refresh_policy"),
            provider=provider,
            dataset_key=dataset_key,
        )
        and _validate_issue_summary(dataset_issues)
        and _validate_issue_summary(provider_issues)
        and (
            (
                catalog_state == "canonical"
                and _validate_dataset_catalog(value.get("catalog"))
                and value.get("orphan_reason") is None
                and value.get("mutable") is True
            )
            or (
                catalog_state == "orphan"
                and value.get("catalog") is None
                and isinstance(value.get("orphan_reason"), str)
                and bool(value["orphan_reason"])
                and value.get("mutable") is False
            )
        )
    )


def _validate_dataset_execution(
    value: Any,
    *,
    provider: Any,
    dataset_key: Any,
    sync_scope: Any,
    active: bool,
) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    kind = value.get("kind")
    execution_id = value.get("id")
    operation_member_id = value.get("operation_member_id")
    execution_scope = value.get("sync_scope")
    provider_datasets = value.get("provider_datasets")
    providers = value.get("providers")
    dataset_keys = value.get("dataset_keys")
    if (
        kind not in {"import_job", "update_request"}
        or not _is_uuid(execution_id)
        or value.get("detail_url")
        != f"/v1/ops/pipeline/executions/{kind}/{execution_id}"
        or value.get("status") not in _OPERATION_STATES
        or value.get("pair_status") not in _OPERATION_STATES
        or not _is_uuid(operation_member_id)
        or not (
            execution_scope is None or _is_canonical_sync_scope(execution_scope)
        )
        or not isinstance(providers, list)
        or not all(isinstance(item, str) and bool(item) for item in providers)
        or not isinstance(dataset_keys, list)
        or not all(isinstance(item, str) and bool(item) for item in dataset_keys)
        or not isinstance(provider_datasets, list)
        or not all(
            _validate_dataset_provider_identity(item) for item in provider_datasets
        )
        or not _is_iso8601(value.get("created_at"))
        or not all(
            _is_nullable_iso8601(value.get(field))
            for field in ("started_at", "finished_at")
        )
        or not all(
            isinstance(value.get(field), (str, type(None)))
            for field in (
                "dagster_run_id",
                "dagster_run_status",
                "trigger_kind",
                "operation_registry_version",
                "error_message",
            )
        )
        or not _validate_dataset_projected_job(value.get("projected_job"))
        or not _validate_cancellation_summary(value.get("cancellation"))
    ):
        return False
    member_keys = [
        (item["provider"], item["dataset_key"])
        for item in provider_datasets
        if isinstance(item, Mapping)
    ]
    matching_members = [
        item
        for item in provider_datasets
        if isinstance(item, Mapping)
        and item.get("provider") == provider
        and item.get("dataset_key") == dataset_key
        and item.get("operation_member_id") == operation_member_id
    ]
    logical_scope_matches = execution_scope == sync_scope or (
        sync_scope == "dataset_wide" and execution_scope is None
    )
    allowed_pair_states = {"queued", "running"} if active else {
        "done",
        "failed",
        "cancelled",
    }
    return (
        len(member_keys) == len(set(member_keys))
        and set(providers) == {item[0] for item in member_keys}
        and set(dataset_keys) == {item[1] for item in member_keys}
        and len(matching_members) == 1
        and logical_scope_matches
        and matching_members[0].get("sync_scope") == execution_scope
        and matching_members[0].get("status") == value.get("pair_status")
        and value.get("pair_status") in allowed_pair_states
    )


def _validate_dataset_provider_identity(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("provider", "dataset_key")
        )
        and (
            value.get("sync_scope") is None
            or _is_canonical_sync_scope(value.get("sync_scope"))
        )
        and _is_uuid(value.get("operation_member_id"))
        and value.get("status") in _OPERATION_STATES
    )


def _validate_dataset_projected_job(value: Any) -> bool:
    if not isinstance(value, Mapping) or not _is_uuid(value.get("id")):
        return False
    return (
        isinstance(value.get("job_kind"), str)
        and bool(value["job_kind"])
        and value.get("status") in _OPERATION_STATES
        and _is_progress(value.get("progress"))
        and all(
            isinstance(value.get(field), (str, type(None)))
            for field in (
                "current_stage",
                "error_message",
                "dagster_run_id",
                "dagster_run_status",
                "trigger_kind",
                "operation_registry_version",
            )
        )
        and _is_iso8601(value.get("created_at"))
        and all(
            _is_nullable_iso8601(value.get(field))
            for field in ("started_at", "finished_at")
        )
        and _is_nonnegative_int(value.get("depth"))
        and value.get("detail_url")
        == f"/v1/ops/pipeline/executions/import_job/{value['id']}"
    )


def _validate_dataset_catalog(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    scope_refresh = value.get("scope_refresh")
    preview = value.get("preview")
    return (
        all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("feature_kind", "provider_state_default_scope", "label")
        )
        and isinstance(value.get("is_feature_load"), bool)
        and isinstance(value.get("is_refreshable"), bool)
        and isinstance(scope_refresh, Mapping)
        and isinstance(scope_refresh.get("supported"), bool)
        and scope_refresh.get("selector") in {"none", "poi_cache_targets"}
        and scope_refresh.get("effect") in {"dataset_wide", "sync_scope"}
        and isinstance(scope_refresh.get("default_sync_scope"), str)
        and bool(scope_refresh["default_sync_scope"])
        and isinstance(scope_refresh.get("allowed_sync_scopes"), list)
        and all(
            isinstance(item, str) and _is_canonical_sync_scope(item)
            for item in scope_refresh["allowed_sync_scopes"]
        )
        and len(scope_refresh["allowed_sync_scopes"])
        == len(set(scope_refresh["allowed_sync_scopes"]))
        and isinstance(scope_refresh.get("reason"), (str, type(None)))
        and (
            (
                scope_refresh.get("selector") == "none"
                and scope_refresh.get("supported") is False
                and scope_refresh.get("effect") == "dataset_wide"
                and scope_refresh.get("default_sync_scope") == "dataset_wide"
                and not scope_refresh["allowed_sync_scopes"]
                and isinstance(scope_refresh.get("reason"), str)
                and bool(scope_refresh["reason"])
            )
            or (
                scope_refresh.get("selector") == "poi_cache_targets"
                and scope_refresh.get("supported") is True
                and scope_refresh.get("effect") == "sync_scope"
                and scope_refresh.get("default_sync_scope") == "target_grids"
                and bool(scope_refresh["allowed_sync_scopes"])
                and scope_refresh["allowed_sync_scopes"][0] == "target_grids"
                and scope_refresh.get("reason") is None
            )
        )
        and (
            value.get("is_refreshable") is True
            or scope_refresh.get("selector") == "none"
        )
        and isinstance(preview, Mapping)
        and isinstance(preview.get("supported"), bool)
        and isinstance(preview.get("sources"), list)
        and all(item == "fixture" for item in preview["sources"])
        and preview.get("supported") == (preview["sources"] == ["fixture"])
        and preview.get("input_kind") == "none"
        and type(preview.get("default_max_items")) is int
        and preview.get("default_max_items") == 20
        and type(preview.get("max_items_limit")) is int
        and preview.get("max_items_limit") == 100
        and type(preview.get("timeout_seconds")) in {int, float}
        and preview.get("timeout_seconds") == 5.0
        and type(preview.get("external_call_budget")) is int
        and preview.get("external_call_budget") == 0
    )


def _validate_refresh_policy(
    value: Any,
    *,
    provider: Any,
    dataset_key: Any,
) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    nullable_int_fields = (
        "system_interval_seconds",
        "optimal_interval_seconds",
        "min_interval_seconds",
        "stale_after_minutes",
        "max_requests_per_minute",
        "max_requests_per_hour",
        "max_requests_per_day",
        "burst_size",
    )
    return (
        value.get("provider") == provider
        and value.get("dataset_key") == dataset_key
        and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("source_kind", "targeted_policy", "config_source")
        )
        and all(_is_nullable_int(value.get(field)) for field in nullable_int_fields)
        and isinstance(value.get("max_concurrent"), int)
        and not isinstance(value.get("max_concurrent"), bool)
        and isinstance(value.get("rate_limit_source"), Mapping)
        and isinstance(value.get("enabled"), bool)
        and isinstance(value.get("revision"), str)
        and re.fullmatch(r"[1-9][0-9]*", value["revision"]) is not None
        and _is_iso8601(value.get("created_at"))
        and _is_iso8601(value.get("updated_at"))
    )


def _validate_dataset_freshness(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("state")
        in {"never_run", "fresh", "overdue", "disabled", "unknown"}
        and value.get("basis") in {"policy_stale_after", "unknown", "disabled"}
        and (
            value.get("sla_seconds") is None
            or _is_nonnegative_int(value.get("sla_seconds"))
        )
        and _is_nullable_iso8601(value.get("due_at"))
        and isinstance(value.get("is_overdue"), bool)
        and _is_nonnegative_int(value.get("overdue_by_seconds"))
    )


def _validate_dataset_schedule(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    schedule_names = value.get("schedule_names")
    active_names = value.get("active_schedule_names")
    return (
        value.get("source") == "dagster_graphql"
        and value.get("basis")
        in {"dagster_definition_tags", "not_scheduled", "unknown"}
        and isinstance(value.get("status"), (str, type(None)))
        and isinstance(schedule_names, list)
        and all(isinstance(item, str) for item in schedule_names)
        and isinstance(active_names, list)
        and all(isinstance(item, str) for item in active_names)
        and set(active_names).issubset(schedule_names)
        and _is_nullable_iso8601(value.get("next_scheduled_at"))
    )


def _validate_issue_summary(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and _is_nonnegative_int(value.get("open_count"))
        and _validate_nonnegative_int_mapping(value.get("severity_counts"))
    )


def _validate_nonnegative_int_mapping(
    value: Any,
    *,
    allowed_keys: frozenset[str] | None = None,
) -> bool:
    return (
        isinstance(value, Mapping)
        and all(
            isinstance(key, str)
            and bool(key)
            and (allowed_keys is None or key in allowed_keys)
            and _is_nonnegative_int(item)
            for key, item in value.items()
        )
    )


def _validate_dagster_run(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("run_id"), str)
        and bool(value["run_id"])
        and isinstance(value.get("status"), str)
        and bool(value["status"])
        and isinstance(value.get("job_name"), (str, type(None)))
        and all(
            _is_nullable_number(value.get(field))
            for field in ("start_time", "end_time", "update_time")
        )
        and isinstance(value.get("tags"), Mapping)
    )


def _validate_dagster_repository(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    jobs = value.get("jobs")
    schedules = value.get("schedules")
    sensors = value.get("sensors")
    return (
        isinstance(value.get("name"), str)
        and bool(value["name"])
        and isinstance(value.get("location_name"), (str, type(None)))
        and isinstance(jobs, list)
        and all(
            isinstance(item, Mapping)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("is_job"), bool)
            for item in jobs
        )
        and isinstance(schedules, list)
        and all(_validate_repository_schedule(item) for item in schedules)
        and isinstance(sensors, list)
        and all(_validate_repository_sensor(item) for item in sensors)
        and _is_nonnegative_int(value.get("asset_count"))
        and isinstance(value.get("asset_groups"), list)
        and all(isinstance(item, str) for item in value["asset_groups"])
    )


def _validate_repository_schedule(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("name"), str)
        and bool(value["name"])
        and all(
            isinstance(value.get(field), (str, type(None)))
            for field in ("job_name", "cron_schedule", "execution_timezone", "status")
        )
    )


def _validate_repository_sensor(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("name"), str)
        and bool(value["name"])
        and isinstance(value.get("status"), (str, type(None)))
    )


def _validate_etl_asset(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("key"), str)
        and bool(value["key"])
        and all(
            isinstance(value.get(field), (str, type(None)))
            for field in ("group_name", "description")
        )
    )


def _validate_etl_job(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("name", "trigger")
        )
        and isinstance(value.get("description"), (str, type(None)))
        and isinstance(value.get("asset_keys"), list)
        and all(isinstance(item, str) for item in value["asset_keys"])
    )


def _validate_etl_schedule(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("name", "job_name", "cron_schedule", "status")
        )
        and isinstance(value.get("execution_timezone"), (str, type(None)))
    )


def _validate_etl_sensor(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("name"), str)
        and bool(value["name"])
        and isinstance(value.get("job_name"), (str, type(None)))
        and isinstance(value.get("status"), str)
        and bool(value["status"])
    )


def _validate_provider_import_job(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        uuid.UUID(str(value.get("job_id", "")))
        uuid.UUID(str(value.get("projected_job_id", "")))
    except ValueError:
        return False
    return (
        value.get("kind") == "import_job"
        and value.get("status") in _OPERATION_STATES
        and value.get("projected_job_status") in _OPERATION_STATES
        and _is_nullable_progress(value.get("progress"))
        and _is_progress(value.get("projected_job_progress"))
        and isinstance(value.get("projected_job_kind"), str)
        and bool(value["projected_job_kind"])
        and _is_nullable_uuid(value.get("projected_job_load_batch_id"))
        and _is_nullable_uuid(value.get("projected_job_parent_job_id"))
        and _validate_cancellation_summary(value.get("cancellation"))
        and isinstance(value.get("payload"), Mapping)
        and all(
            isinstance(value.get(field), (str, type(None)))
            for field in ("status_url", "current_stage", "error_message")
        )
        and _is_iso8601(value.get("created_at"))
        and all(
            _is_nullable_iso8601(value.get(field))
            for field in ("started_at", "finished_at")
        )
        and _validate_provider_links(value.get("links"))
    )


def _validate_cancellation_summary(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    return (
        _is_uuid(value.get("cancellation_id"))
        and value.get("status")
        in {"in_progress", "retryable", "completed", "failed"}
        and _is_iso8601(value.get("requested_at"))
        and isinstance(value.get("requested_by"), str)
        and bool(value["requested_by"])
        and isinstance(value.get("reason"), (str, type(None)))
        and isinstance(value.get("retryable"), bool)
        and _is_nonnegative_int(value.get("unresolved_member_count"))
    )


def _validate_provider_links(value: Any) -> bool:
    if isinstance(value, Mapping):
        return True
    return isinstance(value, list) and all(
        isinstance(link, Mapping)
        and isinstance(link.get("rel"), str)
        and isinstance(link.get("href"), str)
        and isinstance(link.get("label"), (str, type(None)))
        for link in value
    )


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return isinstance(value, str)


def _is_nullable_uuid(value: Any) -> bool:
    return value is None or _is_uuid(value)


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_nullable_int(value: Any) -> bool:
    return value is None or (
        isinstance(value, int) and not isinstance(value, bool)
    )


def _is_progress(value: Any) -> bool:
    return _is_nonnegative_int(value) and value <= 100


def _is_nullable_progress(value: Any) -> bool:
    return value is None or _is_progress(value)


def _is_nonnegative_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
    )


def _is_nullable_number(value: Any) -> bool:
    return value is None or _is_nonnegative_number(value)


def _is_iso8601(value: Any) -> bool:
    return _parse_iso8601_datetime(value) is not None


def _parse_iso8601_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not _ISO8601_DATETIME_WITH_OFFSET.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00").replace("z", "+00:00")
        )
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _is_nullable_iso8601(value: Any) -> bool:
    return value is None or _is_iso8601(value)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class _LoopbackSmokeCookiePolicy(http.cookiejar.DefaultCookiePolicy):
    """production Secure cookie를 TLS 종단 뒤 loopback smoke에서만 허용한다."""

    def return_ok_secure(
        self,
        cookie: http.cookiejar.Cookie,
        request: urllib.request.Request,
    ) -> bool:
        if cookie.secure and urlsplit(request.full_url).hostname == "127.0.0.1":
            return True
        return super().return_ok_secure(cookie, request)


def _cookie_opener(*, follow_redirects: bool) -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar(policy=_LoopbackSmokeCookiePolicy())
    handlers: list[Any] = [urllib.request.HTTPCookieProcessor(jar)]
    if not follow_redirects:
        handlers.append(_NoRedirect())
    return urllib.request.build_opener(*handlers)


def _session_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None = None,
    read_error_body: bool,
) -> HttpProbeResponse:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with opener.open(  # noqa: S310 - production config validates loopback URLs
            request, timeout=10
        ) as response:
            raw = response.read(65_537)
            retry_after_raw = _response_header(response.headers, "Retry-After")
            return HttpProbeResponse(
                status=response.status,
                payload=_read_json_payload(raw),
                retry_after=_retry_after_header(retry_after_raw),
                retry_after_present=_has_response_header(
                    response.headers, "Retry-After"
                ),
                set_cookie=_has_response_header(response.headers, "Set-Cookie"),
                location=_response_header(response.headers, "Location"),
                body_text=_read_text_payload(raw),
                content_type=_response_header(response.headers, "Content-Type"),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read(65_537) if read_error_body else b""
        retry_after_raw = _response_header(exc.headers, "Retry-After")
        return HttpProbeResponse(
            status=exc.code,
            payload=_read_json_payload(raw) if read_error_body else None,
            retry_after=_retry_after_header(retry_after_raw),
            retry_after_present=_has_response_header(exc.headers, "Retry-After"),
            set_cookie=_has_response_header(exc.headers, "Set-Cookie"),
            location=_response_header(exc.headers, "Location"),
            body_text=_read_text_payload(raw) if read_error_body else None,
            content_type=_response_header(exc.headers, "Content-Type"),
        )
    except OSError as exc:
        raise DeploymentContractError("C6c authenticated smoke endpoint is unavailable") from exc


def _read_json_payload(raw: bytes) -> Any | None:
    if len(raw) > 65_536:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _read_text_payload(raw: bytes) -> str | None:
    if not raw or len(raw) > 65_536:
        return None
    return raw.decode("utf-8", errors="replace")


def _response_header(headers: Any | None, name: str) -> str | None:
    if headers is None or not hasattr(headers, "get"):
        return None
    value = headers.get(name)
    return str(value) if value is not None else None


def _has_response_header(headers: Any | None, name: str) -> bool:
    if headers is None:
        return False
    if hasattr(headers, "get_all"):
        return bool(headers.get_all(name))
    return _response_header(headers, name) is not None


def _retry_after_header(raw: str | None) -> int | None:
    if raw is None or _ASCII_RETRY_AFTER.fullmatch(raw) is None:
        return None
    value = int(raw)
    return value if 1 <= value <= 300 else None


def _request_json(
    url: str,
    *,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None = None,
    read_error_body: bool = False,
) -> tuple[int, Any | None]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        # S310: production config가 exact loopback origin을 강제한다.
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            status = response.status
            if status != 200:
                return status, None
            try:
                return status, json.loads(response.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                return status, None
    except urllib.error.HTTPError as exc:
        if not read_error_body:
            # 일반 오류 body에는 upstream 진단/요청 정보가 포함될 수 있으므로 읽지 않는다.
            return exc.code, None
        raw = exc.read(65_537)
        return exc.code, _read_json_payload(raw)
    except OSError as exc:
        raise DeploymentContractError("C6c Map smoke endpoint is unavailable") from exc


def validate_current_map_ui_auth_runtime(
    runtime_config: Mapping[str, Any],
    config: C6cDeploymentConfig,
) -> None:
    """mutation 전 현재 Map UI runtime 인증값만 frozen expected와 대조한다."""

    expected = {
        _MAP_UI_USERNAME_ENV: config.smoke.map_ui_username,
        _MAP_UI_PASSWORD_HASH_ENV: config.map_ui_password_hash,
        _MAP_UI_SESSION_SECRET_ENV: config.map_ui_session_secret,
    }
    actual: dict[str, str] = {}
    allowed_paths: set[tuple[str, ...]] = set()
    plaintext = config.smoke.map_ui_password
    for env_name, value, scalar_paths in _runtime_environment_entries(
        runtime_config.get("Env")
    ):
        if env_name in _MANAGER_ONLY_CREDENTIAL_NAMES:
            raise DeploymentContractError(
                "a C6c manager-only credential is present in the current Map UI"
            )
        if plaintext and plaintext in value:
            raise DeploymentContractError(
                "the current Map UI contains a plaintext smoke credential"
            )
        if env_name not in expected:
            continue
        if env_name in actual:
            raise DeploymentContractError(
                "the current Map UI has duplicate authentication variables"
            )
        actual[env_name] = value
        allowed_paths.update(scalar_paths)

    for env_name, expected_value in expected.items():
        actual_value = actual.get(env_name)
        if actual_value is None or not hmac.compare_digest(
            actual_value.encode("utf-8"), expected_value.encode("utf-8")
        ):
            raise DeploymentContractError(
                "the current Map UI authentication differs from the frozen environment"
            )

    protected_values = (
        config.map_ui_password_hash,
        config.map_ui_session_secret,
        plaintext,
    )
    protected_names = _MANAGER_ONLY_CREDENTIAL_NAMES | _MAP_UI_AUTH_ENV_NAMES
    for path, scalar in _walk_scalars(runtime_config):
        if path in allowed_paths:
            continue
        text = "" if scalar is None else str(scalar)
        if any(name in text for name in protected_names) or any(
            value and value in text for value in protected_values
        ):
            raise DeploymentContractError(
                "the current Map UI authentication leaks outside its exact "
                "environment path"
            )


def validate_runtime_secret_isolation(
    container_configs: Mapping[str, Mapping[str, Any]],
    config: C6cDeploymentConfig,
) -> None:
    expected = {
        config.map_container: {
            _MAP_READ_ENV: config.read_token,
            _MAP_CANCEL_ENV: config.cancel_token,
            _MAP_REQUIRED_ENV: "true",
        },
        config.pinvi_container: {
            _PINVI_READ_ENV: config.read_token,
            _PINVI_CANCEL_ENV: config.cancel_token,
        },
        config.map_ui_container: {
            _MAP_UI_USERNAME_ENV: config.smoke.map_ui_username,
            _MAP_UI_PASSWORD_HASH_ENV: config.map_ui_password_hash,
            _MAP_UI_SESSION_SECRET_ENV: config.map_ui_session_secret,
        },
    }
    for required_container in expected:
        if required_container not in container_configs:
            raise DeploymentContractError(
                "a required C6c container is missing from runtime inspection"
            )
    secret_values = tuple(
        secret
        for secret in (
            config.read_token,
            config.cancel_token,
            config.map_ui_password_hash,
            config.map_ui_session_secret,
            config.smoke.map_ui_password,
            config.smoke.pinvi_admin_email,
            config.smoke.pinvi_admin_password,
            config.smoke.cancel_probe_job_id,
            config.contract_generation,
        )
        if secret
    )
    protected_names = (
        _OPS_ENV_NAMES | _MANAGER_ONLY_CREDENTIAL_NAMES | _MAP_UI_AUTH_ENV_NAMES
    )
    for container_name, runtime_config in container_configs.items():
        if not isinstance(runtime_config, Mapping):
            raise DeploymentContractError("container returned invalid runtime config")
        allowed = expected.get(container_name, {})
        environment_entries = _runtime_environment_entries(runtime_config.get("Env"))
        environment: dict[str, str] = {}
        allowed_paths: set[tuple[str, ...]] = set()
        for env_name, value, scalar_paths in environment_entries:
            if env_name in environment:
                raise DeploymentContractError(
                    "duplicate runtime environment variables are forbidden"
                )
            environment[env_name] = value
            if env_name in _MANAGER_ONLY_CREDENTIAL_NAMES:
                raise DeploymentContractError(
                    "a C6c manager-only credential is present in a container"
                )
            if env_name in _OPS_ENV_NAMES | _MAP_UI_AUTH_ENV_NAMES:
                if env_name not in allowed:
                    raise DeploymentContractError(
                        "a C6c runtime protected value is present in an "
                        "unauthorized container"
                    )
                if not hmac.compare_digest(value, allowed[env_name]):
                    raise DeploymentContractError(
                        "C6c runtime protected value wiring is invalid"
                    )
                allowed_paths.update(scalar_paths)
            elif any(secret in value for secret in secret_values):
                raise DeploymentContractError(
                    "a C6c runtime secret value leaks in an unauthorized variable"
                )
        for env_name in allowed:
            if env_name not in environment:
                raise DeploymentContractError(
                    "C6c runtime protected value wiring is missing"
                )
        for path, scalar in _walk_scalars(runtime_config):
            if path in allowed_paths:
                continue
            text = "" if scalar is None else str(scalar)
            if any(name in text for name in protected_names) or any(
                secret in text for secret in secret_values
            ):
                raise DeploymentContractError(
                    "a C6c credential leaks outside its exact environment path"
                )
        if container_name == config.map_container:
            if _FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES.intersection(environment):
                raise DeploymentContractError(
                    "Map API runtime includes forbidden provider environment"
                )
            if runtime_config.get("Entrypoint") is not None or runtime_config.get(
                "Cmd"
            ) != ["./docker/api-entrypoint.sh"]:
                raise DeploymentContractError(
                    "Map API runtime must use the immutable image entrypoint and command"
                )


def new_image_pair(
    map_image_id: str,
    pinvi_image_id: str,
    contract_generation: str,
    *,
    map_source_revision: str,
    pinvi_source_revision: str,
) -> CompatibleImagePair:
    _validate_image_id(map_image_id, "Map")
    _validate_image_id(pinvi_image_id, "PinVi")
    _validate_source_revision(map_source_revision, "Map")
    _validate_source_revision(pinvi_source_revision, "PinVi")
    if not isinstance(contract_generation, str) or not _CONTRACT_GENERATION_PATTERN.fullmatch(
        contract_generation
    ):
        raise DeploymentContractError("compatible pair contract generation is invalid")
    return CompatibleImagePair(
        map_image_id=map_image_id,
        map_source_revision=map_source_revision,
        pinvi_image_id=pinvi_image_id,
        pinvi_source_revision=pinvi_source_revision,
        contract_generation=contract_generation,
        recorded_at=datetime.now(UTC).isoformat(),
    )


def load_pair_manifest(path: str) -> CompatiblePairManifest:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise DeploymentContractError(
            "compatible pair manifest is missing; run "
            "`ktdctl pinvi-pair capture --verified-compatible` first"
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, Mapping)
            or set(payload) != {"version", "rollback", "active"}
            or type(payload.get("version")) is not int
        ):
            raise TypeError("manifest version must be an exact integer")
        expected_pair_keys = {
            "map_image_id",
            "map_source_revision",
            "pinvi_image_id",
            "pinvi_source_revision",
            "contract_generation",
            "recorded_at",
        }
        for pair_name in ("rollback", "active"):
            pair_payload = payload.get(pair_name)
            if (
                not isinstance(pair_payload, Mapping)
                or set(pair_payload) != expected_pair_keys
            ):
                raise TypeError("manifest pair shape is invalid")
        manifest = CompatiblePairManifest(
            version=payload["version"],
            rollback=CompatibleImagePair(**payload["rollback"]),
            active=CompatibleImagePair(**payload["active"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
        raise DeploymentContractError("compatible pair manifest is invalid") from exc
    _validate_pair_manifest_contract(manifest)
    return manifest


def assert_pair_manifest_bootstrap_allowed(path: str) -> None:
    """manifest가 없는 환경에서만 초기 v3 bootstrap을 허용한다."""

    manifest_path = Path(path)
    if not manifest_path.exists():
        return
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or type(payload.get("version")) is not int:
            raise TypeError("manifest version must be an exact integer")
        version = payload["version"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
        raise DeploymentContractError(
            "invalid compatible pair manifest cannot be bootstrapped automatically"
        ) from exc
    if version == _PAIR_MANIFEST_VERSION:
        raise DeploymentContractError(
            "compatible pair manifest v3 already exists; use deploy or rollback"
        )
    raise DeploymentContractError(
        "legacy compatible pair manifest has no source provenance"
    )


def write_pair_manifest(path: str, manifest: CompatiblePairManifest) -> None:
    _validate_pair_manifest_contract(manifest)
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(manifest), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    payload_bytes = payload.encode("utf-8")
    previous_bytes = manifest_path.read_bytes() if manifest_path.exists() else None
    previous_mode = (
        manifest_path.stat().st_mode & 0o777 if previous_bytes is not None else None
    )
    temp_path: Path | None = None
    replaced = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
            temp_path = Path(temp.name)
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, manifest_path)
        replaced = True
        _fsync_directory(manifest_path.parent)
    except OSError as exc:
        if replaced:
            try:
                _restore_manifest_snapshot(
                    manifest_path,
                    previous_bytes=previous_bytes,
                    previous_mode=previous_mode,
                )
            except OSError:
                try:
                    current_bytes = manifest_path.read_bytes()
                except OSError:
                    current_bytes = None
                if current_bytes == payload_bytes:
                    return
                if current_bytes != previous_bytes:
                    raise DeploymentContractError(
                        "compatible pair manifest commit state is indeterminate"
                    ) from exc
        raise DeploymentContractError("compatible pair manifest write failed") from exc
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _restore_manifest_snapshot(
    manifest_path: Path,
    *,
    previous_bytes: bytes | None,
    previous_mode: int | None,
) -> None:
    if previous_bytes is None:
        manifest_path.unlink(missing_ok=True)
        _fsync_directory(manifest_path.parent)
        return
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.restore.",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp.write(previous_bytes)
            temp.flush()
            os.fsync(temp.fileno())
            temp_path = Path(temp.name)
        if previous_mode is not None:
            os.chmod(temp_path, previous_mode)
        os.replace(temp_path, manifest_path)
        _fsync_directory(manifest_path.parent)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _validate_pair_manifest_contract(manifest: CompatiblePairManifest) -> None:
    if type(manifest.version) is not int or manifest.version != _PAIR_MANIFEST_VERSION:
        raise DeploymentContractError("compatible pair manifest version is unsupported")
    for pair in (manifest.rollback, manifest.active):
        _validate_image_id(pair.map_image_id, "Map")
        _validate_image_id(pair.pinvi_image_id, "PinVi")
        _validate_source_revision(pair.map_source_revision, "Map")
        _validate_source_revision(pair.pinvi_source_revision, "PinVi")
        if not isinstance(
            pair.contract_generation, str
        ) or not _CONTRACT_GENERATION_PATTERN.fullmatch(pair.contract_generation):
            raise DeploymentContractError("compatible pair contract generation is invalid")
        if not _is_iso8601(pair.recorded_at):
            raise DeploymentContractError(
                "compatible pair recorded_at must be an offset ISO 8601 datetime"
            )


def manifest_with_active_pair(
    manifest: CompatiblePairManifest,
    active: CompatibleImagePair,
) -> CompatiblePairManifest:
    same_active_identity = (
        active.map_image_id == manifest.active.map_image_id
        and active.map_source_revision == manifest.active.map_source_revision
        and active.pinvi_image_id == manifest.active.pinvi_image_id
        and active.pinvi_source_revision == manifest.active.pinvi_source_revision
        and active.contract_generation == manifest.active.contract_generation
    )
    rollback = manifest.rollback if same_active_identity else manifest.active
    return CompatiblePairManifest(
        version=_PAIR_MANIFEST_VERSION,
        rollback=rollback,
        active=active,
    )


def initial_pair_manifest(pair: CompatibleImagePair) -> CompatiblePairManifest:
    return CompatiblePairManifest(
        version=_PAIR_MANIFEST_VERSION,
        rollback=pair,
        active=pair,
    )


def _validate_image_id(image_id: str, label: str) -> None:
    if not isinstance(image_id, str) or not _IMAGE_ID_PATTERN.fullmatch(image_id):
        raise DeploymentContractError(
            f"{label} image must be an immutable sha256 image ID, not a mutable tag"
        )


def _validate_source_revision(revision: str, label: str) -> None:
    if not isinstance(revision, str) or not _SOURCE_REVISION_PATTERN.fullmatch(revision):
        raise DeploymentContractError(
            f"{label} image source revision must be an exact lowercase commit"
        )


def _environment_mapping(value: Any) -> dict[str, str]:
    if isinstance(value, Mapping):
        return {str(key): "" if item is None else str(item) for key, item in value.items()}
    if isinstance(value, list):
        result: dict[str, str] = {}
        for item in value:
            key, _, env_value = str(item).partition("=")
            result[key] = env_value
        return result
    return {}


def _runtime_environment_entries(
    value: Any,
) -> list[tuple[str, str, set[tuple[str, ...]]]]:
    if isinstance(value, Mapping):
        return [
            (
                str(key),
                "" if item is None else str(item),
                {("Env", str(key)), ("Env", str(key), "<key>")},
            )
            for key, item in value.items()
        ]
    if isinstance(value, list):
        entries: list[tuple[str, str, set[tuple[str, ...]]]] = []
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise DeploymentContractError("container returned invalid runtime Env")
            key, separator, env_value = item.partition("=")
            if not key or not separator:
                raise DeploymentContractError("container returned invalid runtime Env")
            entries.append((key, env_value, {("Env", str(index))}))
        return entries
    if value is None:
        return []
    raise DeploymentContractError("container returned invalid runtime Env")


def _walk_scalars(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            yield from _walk_scalars(key_text, (*path, key_text, "<key>"))
            yield from _walk_scalars(item, (*path, key_text))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_scalars(item, (*path, str(index)))
        return
    if isinstance(value, (str, int, float, bool)) or value is None:
        yield path, value


def _env_file_entries(value: Any) -> list[str]:
    entries = value if isinstance(value, list) else [value]
    result: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            result.append(entry)
        elif isinstance(entry, Mapping) and isinstance(entry.get("path"), str):
            result.append(entry["path"])
    return result


def _expand_env_path(value: str, environment: Mapping[str, str]) -> str:
    """Compose path interpolation을 단일 단계로 해석하고 모호한 문법은 거부한다."""

    result: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "}":
            raise ComposeCandidateContractError(
                "compose candidate path contains unsupported interpolation"
            )
        if character != "$":
            result.append(character)
            index += 1
            continue
        if index + 1 >= len(value):
            raise ComposeCandidateContractError(
                "compose candidate path contains unresolved interpolation"
            )
        following = value[index + 1]
        if following == "$":
            result.append("$")
            index += 2
            continue
        if following == "{":
            closing = value.find("}", index + 2)
            if closing < 0:
                raise ComposeCandidateContractError(
                    "compose candidate path contains unresolved interpolation"
                )
            expression = value[index + 2 : closing]
            if "$" in expression or "{" in expression:
                raise ComposeCandidateContractError(
                    "compose candidate path contains unsupported interpolation"
                )
            match = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_]*)(?:(:-|-|:\?|\?|:\+|\+)(.*))?",
                expression,
            )
            if match is None:
                raise ComposeCandidateContractError(
                    "compose candidate path contains unsupported interpolation"
                )
            name, operator, word = match.groups()
            result.append(
                _resolve_compose_path_variable(
                    name,
                    operator,
                    word or "",
                    environment,
                )
            )
            index = closing + 1
            continue
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", value[index + 1 :])
        if match is None:
            raise ComposeCandidateContractError(
                "compose candidate path contains unsupported interpolation"
            )
        name = match.group(0)
        result.append(environment.get(name, ""))
        index += len(name) + 1
    expanded = "".join(result)
    if not expanded:
        raise ComposeCandidateContractError(
            "compose candidate path resolves to an empty value"
        )
    return expanded


def _resolve_candidate_path(value: str, compose_directory: Path) -> Path:
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = compose_directory / path
        return path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ComposeCandidateContractError(
            "compose candidate path cannot be resolved"
        ) from exc


def compose_volume_graph_hash(document: Mapping[str, Any]) -> str:
    services = document.get("services", {})
    if not isinstance(services, Mapping):
        raise ComposeCandidateContractError(
            "compose candidate has no valid services mapping"
        )
    graph = {
        "volumes": document.get("volumes"),
        "services": {
            str(service_name): service.get("volumes")
            for service_name, service in services.items()
            if isinstance(service, Mapping) and "volumes" in service
        },
    }
    try:
        encoded = json.dumps(
            graph,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ComposeCandidateContractError(
            "compose candidate volume graph cannot be normalized"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _assert_candidate_single_file_boundary(
    document: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
) -> None:
    if document.get("include") is not None:
        raise ComposeCandidateContractError(
            "compose candidate include is not supported by the single-file boundary"
        )
    if environment.get("COMPOSE_FILE", "").strip():
        raise ComposeCandidateContractError(
            "compose candidate COMPOSE_FILE composition is not supported"
        )
    if environment.get("KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE", "").strip():
        raise ComposeCandidateContractError(
            "compose candidate override composition is not supported"
        )
    services = document.get("services")
    if not isinstance(services, Mapping):
        return
    if any(
        isinstance(service, Mapping) and service.get("extends") is not None
        for service in services.values()
    ):
        raise ComposeCandidateContractError(
            "compose candidate service extends is not supported"
        )


def revalidate_candidate_system_bind_snapshots(
    snapshots: tuple[CandidateSystemBindSnapshot, ...],
) -> None:
    current = tuple(
        _capture_candidate_system_bind_snapshot(
            service=snapshot.service,
            source=snapshot.source,
            target=snapshot.target,
            read_only=snapshot.read_only,
        )
        for snapshot in snapshots
    )
    if current != snapshots:
        raise ComposeCandidateContractError(
            "compose candidate system bind identity changed during the request"
        )


def _validate_candidate_volume_graph(
    document: Mapping[str, Any],
    services: Mapping[str, Any],
    *,
    compose_directory: Path | None,
    root_env: Path | None,
    environment: Mapping[str, str],
    protected_names: frozenset[str],
    protected_values: tuple[str, ...],
    allow_undeclared_named_volumes: bool = False,
    resolved_document: bool = False,
) -> tuple[CandidateSystemBindSnapshot, ...]:
    named_volumes = _candidate_named_volume_definitions(
        document.get("volumes"),
        resolved_document=resolved_document,
        compose_project_name=document.get("name"),
    )
    manager_paths: tuple[Path, ...] = ()
    if root_env is not None:
        try:
            state_paths = tuple(
                Path(path).expanduser().resolve()
                for path in c6c_state_paths(environment)
            )
        except (DeploymentContractError, OSError, RuntimeError, ValueError) as exc:
            raise ComposeCandidateContractError(
                "compose candidate manager path cannot be resolved"
            ) from exc
        manager_paths = (root_env, *state_paths)

    system_snapshots: list[CandidateSystemBindSnapshot] = []
    for service_name, service in services.items():
        if not isinstance(service, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate service {service_name} is invalid"
            )
        mounts = tuple(
            _candidate_volume_mounts(
                service.get("volumes"),
                environment=environment,
            )
        )
        if str(service_name) == "cadvisor":
            _assert_candidate_cadvisor_mount_set(
                mounts,
                compose_directory=compose_directory,
                resolved_document=resolved_document,
            )
        for mount in mounts:
            if mount.kind == "volume":
                if (
                    mount.source not in named_volumes
                    and mount.source not in _CANDIDATE_ALLOWED_EXTERNAL_VOLUME_REFERENCES
                    and not allow_undeclared_named_volumes
                ):
                    raise ComposeCandidateContractError(
                        f"compose candidate {service_name} named volume is undeclared"
                    )
                continue
            if compose_directory is None or root_env is None:
                raise ComposeCandidateContractError(
                    "resolved compose bind source has no canonical path context"
                )
            if _is_windows_looking_path(mount.source):
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} bind source uses an unsupported path"
                )
            resolved_source = _resolve_candidate_path(
                mount.source,
                compose_directory,
            )
            if any(
                resolved_source == manager_path
                or resolved_source in manager_path.parents
                for manager_path in manager_paths
            ):
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} bind source exposes a manager file"
                )
            if not resolved_source.exists():
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} bind source does not exist"
                )
            system_source = _CANDIDATE_ALLOWED_SYSTEM_BINDS.get(
                (str(service_name), mount.target, mount.read_only)
            )
            if system_source is not None:
                expected_source = _resolve_candidate_path(
                    system_source,
                    compose_directory,
                )
                source_is_exact = (
                    mount.declared_source or mount.source
                ) == system_source
                if resolved_document:
                    source_is_exact = resolved_source == expected_source
                if not source_is_exact or resolved_source != expected_source:
                    raise ComposeCandidateContractError(
                        f"compose candidate {service_name} system bind is not canonical"
                    )
                system_snapshots.append(
                    _capture_candidate_system_bind_snapshot(
                        service=str(service_name),
                        source=system_source,
                        target=mount.target,
                        read_only=mount.read_only,
                    )
                )
                continue
            expected_raw_source = _CANDIDATE_ALLOWED_OPERATOR_BINDS.get(
                (str(service_name), mount.target, mount.read_only)
            )
            if expected_raw_source is None:
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} bind is not in the canonical baseline"
                )
            expected_source = _resolve_candidate_path(
                _expand_env_path(expected_raw_source, environment),
                compose_directory,
            )
            if resolved_source != expected_source:
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} bind source is not canonical"
                )
            try:
                source_stat = resolved_source.stat()
            except (OSError, ValueError) as exc:
                raise ComposeCandidateContractError(
                    f"compose candidate cannot inspect {service_name} bind source"
                ) from exc
            if stat.S_ISREG(source_stat.st_mode):
                _assert_candidate_regular_file(resolved_source)
                try:
                    source_text = resolved_source.read_text(encoding="utf-8")
                except (OSError, UnicodeError, ValueError) as exc:
                    raise ComposeCandidateContractError(
                        f"compose candidate cannot validate {service_name} bind source"
                    ) from exc
                if any(name in source_text for name in protected_names) or any(
                    value in source_text for value in protected_values
                ):
                    raise ComposeCandidateContractError(
                        f"compose candidate {service_name} bind source leaks C6c data"
                    )
            elif not stat.S_ISDIR(source_stat.st_mode):
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} operator bind is not a regular file or directory"
                )
    return tuple(
        sorted(
            system_snapshots,
            key=lambda item: (item.service, item.target, item.source),
        )
    )


def _assert_candidate_cadvisor_mount_set(
    mounts: tuple[CandidateVolumeMount, ...],
    *,
    compose_directory: Path | None,
    resolved_document: bool,
) -> None:
    expected_sources = {
        "/sys": "/sys",
        "/var/run/docker.sock": "/var/run/docker.sock",
    }
    expected = {
        ("bind", source, target, True)
        for source, target in expected_sources.items()
    }
    if resolved_document:
        if compose_directory is None:
            raise ComposeCandidateContractError(
                "resolved cAdvisor mounts have no canonical path context"
            )
        expected = {
            (
                "bind",
                str(_resolve_candidate_path(source, compose_directory)),
                target,
                True,
            )
            for source, target in expected_sources.items()
        }
    actual: set[tuple[str, str, str, bool]] = set()
    for mount in mounts:
        source = mount.declared_source or mount.source
        target = mount.declared_target or mount.target
        if resolved_document and mount.kind == "bind":
            assert compose_directory is not None
            source = str(_resolve_candidate_path(mount.source, compose_directory))
            target = mount.target
        actual.add((mount.kind, source, target, mount.read_only))
    if actual != expected or len(mounts) != len(expected):
        raise ComposeCandidateContractError(
            "compose candidate cAdvisor mounts must be exactly read-only /sys and Docker socket"
        )


def _candidate_named_volume_definitions(
    value: Any,
    *,
    resolved_document: bool,
    compose_project_name: Any,
) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, Mapping):
        raise ComposeCandidateContractError(
            "compose candidate top-level volumes must be a mapping"
        )
    if resolved_document and (
        not isinstance(compose_project_name, str)
        or not _COMPOSE_PROJECT_PATTERN.fullmatch(compose_project_name)
    ):
        raise ComposeCandidateContractError(
            "resolved compose named volumes require a canonical project name"
        )
    names: set[str] = set()
    for raw_name, definition in value.items():
        name = str(raw_name)
        if not _is_named_volume_source(name):
            raise ComposeCandidateContractError(
                "compose candidate named volume has an invalid name"
            )
        if definition is None:
            if resolved_document:
                raise ComposeCandidateContractError(
                    f"resolved compose named volume {name} name is not canonical"
                )
            names.add(name)
            continue
        if not isinstance(definition, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate named volume {name} has an invalid definition"
            )
        allowed_keys = {"driver", "driver_opts"}
        if resolved_document:
            allowed_keys.update({"external", "name"})
        if set(definition) - allowed_keys:
            raise ComposeCandidateContractError(
                f"compose candidate named volume {name} has unsupported options"
            )
        driver = definition.get("driver")
        if driver is not None and driver != "local":
            raise ComposeCandidateContractError(
                f"compose candidate named volume {name} has an unsupported driver"
            )
        driver_opts = definition.get("driver_opts")
        if driver_opts is not None and (
            not isinstance(driver_opts, Mapping) or bool(driver_opts)
        ):
            raise ComposeCandidateContractError(
                f"compose candidate named volume {name} driver options are not allowed"
            )
        external = definition.get("external")
        if external is not None and external is not False:
            raise ComposeCandidateContractError(
                f"compose candidate named volume {name} cannot be external"
            )
        resolved_name = definition.get("name")
        if resolved_document:
            expected_name = f"{compose_project_name}_{name}"
            if resolved_name != expected_name:
                raise ComposeCandidateContractError(
                    f"resolved compose named volume {name} name is not canonical"
                )
        names.add(name)
    return frozenset(names)


def _candidate_volume_mounts(
    value: Any,
    *,
    environment: Mapping[str, str],
) -> Iterable[CandidateVolumeMount]:
    if value is None:
        return
    if not isinstance(value, list):
        raise ComposeCandidateContractError(
            "compose candidate service volumes must be a list"
        )
    for entry in value:
        if isinstance(entry, str):
            declared_source: str | None = None
            declared_target: str | None = None
            declared_mount = entry
            declared_mode = declared_mount.rpartition(":")[2]
            if declared_mode in {"ro", "rw"}:
                declared_mount = declared_mount.rpartition(":")[0]
            if ":" in declared_mount:
                declared_source, _, declared_target = declared_mount.rpartition(":")
            expanded = _expand_env_path(entry, environment)
            if re.match(r"^[A-Za-z]:", expanded) or expanded.startswith("\\\\"):
                raise ComposeCandidateContractError(
                    "compose candidate bind source uses an unsupported Windows path"
                )
            parts = expanded.split(":")
            if len(parts) == 1:
                raise ComposeCandidateContractError(
                    "compose candidate anonymous volume is not allowed"
                )
            if len(parts) not in {2, 3} or not parts[0]:
                raise ComposeCandidateContractError(
                    "compose candidate short volume syntax is ambiguous"
                )
            source = parts[0]
            target = parts[1]
            if not target:
                raise ComposeCandidateContractError(
                    "compose candidate short volume target is empty"
                )
            mode = parts[2] if len(parts) == 3 else "rw"
            if mode not in {"ro", "rw"}:
                raise ComposeCandidateContractError(
                    "compose candidate short volume mode is not allowed"
                )
            kind = "volume" if _is_named_volume_source(source) else "bind"
            yield CandidateVolumeMount(
                kind=kind,
                source=source,
                target=target,
                read_only=mode == "ro",
                declared_source=declared_source,
                declared_target=declared_target,
            )
            continue
        if not isinstance(entry, Mapping):
            raise ComposeCandidateContractError(
                "compose candidate volume entry is invalid"
            )
        raw_type = entry.get("type")
        if not isinstance(raw_type, str):
            raise ComposeCandidateContractError(
                "compose candidate long volume has no valid type"
            )
        volume_type = raw_type.strip().lower()
        if volume_type not in {"bind", "volume"}:
            raise ComposeCandidateContractError(
                "compose candidate long volume type is not allowed"
            )
        if "source" in entry and "src" in entry:
            raise ComposeCandidateContractError(
                "compose candidate long volume source is ambiguous"
            )
        if "target" in entry and "dst" in entry:
            raise ComposeCandidateContractError(
                "compose candidate long volume target is ambiguous"
            )
        raw_source = entry.get("source", entry.get("src"))
        raw_target = entry.get("target", entry.get("dst"))
        if not isinstance(raw_source, str):
            raise ComposeCandidateContractError(
                "compose candidate bind volume has no valid source"
            )
        if not isinstance(raw_target, str) or not raw_target:
            raise ComposeCandidateContractError(
                "compose candidate bind volume has no valid target"
            )
        read_only = entry.get("read_only", False)
        if type(read_only) is not bool:
            raise ComposeCandidateContractError(
                "compose candidate bind read_only must be a boolean"
            )
        common_keys = {"type", "source", "src", "target", "dst", "read_only"}
        extra_keys = set(entry) - common_keys
        option_name = "bind" if volume_type == "bind" else "volume"
        if extra_keys - {option_name}:
            raise ComposeCandidateContractError(
                "compose candidate long volume has unsupported options"
            )
        options = entry.get(option_name)
        if options is not None:
            if not isinstance(options, Mapping):
                raise ComposeCandidateContractError(
                    "compose candidate long volume options are invalid"
                )
            allowed_options = (
                {"create_host_path"} if volume_type == "bind" else set()
            )
            if set(options) - allowed_options:
                raise ComposeCandidateContractError(
                    "compose candidate long volume options are not allowed"
                )
            if "create_host_path" in options and options["create_host_path"] is not True:
                raise ComposeCandidateContractError(
                    "compose candidate bind create_host_path is invalid"
                )
        source = _expand_env_path(raw_source, environment)
        target = _expand_env_path(raw_target, environment)
        if volume_type == "volume" and not _is_named_volume_source(source):
            raise ComposeCandidateContractError(
                "compose candidate named volume source is invalid"
            )
        yield CandidateVolumeMount(
            kind=volume_type,
            source=source,
            target=target,
            read_only=read_only,
            declared_source=raw_source,
            declared_target=raw_target,
        )


def _capture_candidate_system_bind_snapshot(
    *,
    service: str,
    source: str,
    target: str,
    read_only: bool,
) -> CandidateSystemBindSnapshot:
    try:
        raw_path = Path(source)
        if raw_path.is_symlink():
            raise ComposeCandidateContractError(
                f"compose candidate {service} system bind cannot be a symlink"
            )
        resolved = raw_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ComposeCandidateContractError(
            f"compose candidate {service} system bind does not exist"
        ) from exc
    try:
        source_stat = resolved.stat()
    except (OSError, ValueError) as exc:
        raise ComposeCandidateContractError(
            f"compose candidate cannot inspect {service} system bind"
        ) from exc
    if source == "/sys":
        if resolved != Path("/sys") or not stat.S_ISDIR(source_stat.st_mode):
            raise ComposeCandidateContractError(
                "compose candidate cAdvisor /sys bind is not the expected directory"
            )
        if not os.path.ismount(resolved):
            raise ComposeCandidateContractError(
                "compose candidate cAdvisor /sys bind is not a mountpoint"
            )
    elif source == "/var/run/docker.sock":
        if not stat.S_ISSOCK(source_stat.st_mode):
            raise ComposeCandidateContractError(
                "compose candidate cAdvisor Docker source is not a socket"
            )
        try:
            docker_gid = grp.getgrnam("docker").gr_gid
        except KeyError as exc:
            raise ComposeCandidateContractError(
                "compose candidate Docker socket group cannot be verified"
            ) from exc
        if source_stat.st_gid != docker_gid:
            raise ComposeCandidateContractError(
                "compose candidate Docker socket is not owned by the docker group"
            )
        if source_stat.st_mode & stat.S_IWOTH:
            raise ComposeCandidateContractError(
                "compose candidate Docker socket is world-writable"
            )
        if stat.S_IMODE(source_stat.st_mode) != 0o660:
            raise ComposeCandidateContractError(
                "compose candidate Docker socket mode is not root:docker 0660"
            )
    else:
        raise ComposeCandidateContractError(
            f"compose candidate {service} system bind is not allowed"
        )
    if source_stat.st_uid != 0:
        raise ComposeCandidateContractError(
            f"compose candidate {service} system bind is not root-owned"
        )

    chain: list[CandidatePathIdentity] = []
    current = resolved
    first = True
    while True:
        try:
            current_stat = current.stat()
        except (OSError, ValueError) as exc:
            raise ComposeCandidateContractError(
                f"compose candidate cannot inspect {service} system bind parent"
            ) from exc
        if current_stat.st_uid != 0:
            raise ComposeCandidateContractError(
                f"compose candidate {service} system bind chain is not root-owned"
            )
        if not first and current_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ComposeCandidateContractError(
                f"compose candidate {service} system bind parent is writable"
            )
        if first and source == "/sys" and current_stat.st_mode & (
            stat.S_IWGRP | stat.S_IWOTH
        ):
            raise ComposeCandidateContractError(
                "compose candidate cAdvisor /sys source is writable"
            )
        chain.append(
            CandidatePathIdentity(
                path=str(current),
                device=current_stat.st_dev,
                inode=current_stat.st_ino,
                mode=current_stat.st_mode,
                uid=current_stat.st_uid,
                gid=current_stat.st_gid,
            )
        )
        parent = current.parent
        if parent == current:
            break
        current = parent
        first = False
    return CandidateSystemBindSnapshot(
        service=service,
        source=source,
        target=target,
        read_only=read_only,
        path_chain=tuple(chain),
    )


def _is_named_volume_source(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is not None


def _is_windows_looking_path(value: str) -> bool:
    return (
        re.match(r"^[A-Za-z]:", value) is not None
        or value.startswith("\\\\")
        or value.startswith("//")
        or "\\" in value
    )


def _validate_candidate_external_resource_references(
    document: Mapping[str, Any],
    *,
    services: Mapping[str, Any],
    environment: Mapping[str, str],
    protected_names: frozenset[str],
    protected_values: tuple[str, ...],
) -> None:
    for collection_name in ("secrets", "configs"):
        collection = document.get(collection_name)
        if collection is None:
            continue
        if not isinstance(collection, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate top-level {collection_name} is invalid"
            )
        external_aliases: set[str] = set()
        for alias, source in collection.items():
            if not isinstance(source, Mapping):
                continue
            environment_name = source.get("environment")
            if environment_name is not None:
                if not isinstance(environment_name, str):
                    raise ComposeCandidateContractError(
                        f"compose candidate {collection_name}.{alias} environment is invalid"
                    )
                environment_value = environment.get(environment_name)
                if environment_value is None:
                    raise ComposeCandidateContractError(
                        f"compose candidate {collection_name}.{alias} environment is unresolved"
                    )
                if any(name in environment_name for name in protected_names) or any(
                    value in environment_value for value in protected_values
                ):
                    raise ComposeCandidateContractError(
                        f"compose candidate {collection_name}.{alias} environment leaks C6c data"
                    )
            external = source.get("external")
            is_external = external is not None and external is not False
            has_uninspectable_name = "name" in source and not any(
                key in source for key in ("file", "content", "environment")
            )
            if is_external or has_uninspectable_name:
                external_aliases.add(str(alias))

        if not external_aliases:
            continue
        for service_name, service in services.items():
            if not isinstance(service, Mapping):
                raise ComposeCandidateContractError(
                    f"compose candidate service {service_name} is invalid"
                )
            for alias in _candidate_resource_references(
                service.get(collection_name),
                collection_name=collection_name,
            ):
                placement = (str(service_name), collection_name, alias)
                if (
                    alias in external_aliases
                    and placement
                    not in _CANDIDATE_ALLOWED_EXTERNAL_RESOURCE_REFERENCES
                ):
                    raise ComposeCandidateContractError(
                        f"compose candidate {service_name} uses uninspectable external {collection_name}"
                    )


def _candidate_resource_references(
    value: Any,
    *,
    collection_name: str,
) -> Iterable[str]:
    if value is None:
        return
    if not isinstance(value, list):
        raise ComposeCandidateContractError(
            f"compose candidate service {collection_name} must be a list"
        )
    for entry in value:
        if isinstance(entry, str):
            yield entry
            continue
        if not isinstance(entry, Mapping) or not isinstance(
            entry.get("source"), str
        ):
            raise ComposeCandidateContractError(
                f"compose candidate service {collection_name} reference is invalid"
            )
        yield entry["source"]


def _assert_candidate_regular_file(path: Path) -> None:
    try:
        file_stat = path.stat()
    except (OSError, ValueError) as exc:
        raise ComposeCandidateContractError(
            "compose candidate external file cannot be inspected"
        ) from exc
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_size > _CANDIDATE_EXTERNAL_FILE_MAX_BYTES
    ):
        raise ComposeCandidateContractError(
            "compose candidate external file must be a bounded regular file"
        )


def _resolve_compose_path_variable(
    name: str,
    operator: str | None,
    word: str,
    environment: Mapping[str, str],
) -> str:
    is_set = name in environment
    current = environment.get(name, "")
    is_nonempty = bool(current)
    if operator is None:
        return current
    if operator == ":-":
        return current if is_set and is_nonempty else word
    if operator == "-":
        return current if is_set else word
    if operator == ":+":
        return word if is_set and is_nonempty else ""
    if operator == "+":
        return word if is_set else ""
    if operator == ":?" and (not is_set or not is_nonempty):
        raise ComposeCandidateContractError(
            "compose candidate path requires a non-empty environment value"
        )
    if operator == "?" and not is_set:
        raise ComposeCandidateContractError(
            "compose candidate path requires a configured environment value"
        )
    return current
