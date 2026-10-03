import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import urllib.request
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
    MAP_BOOTSTRAP_ADMIN_USER_ENV,
    MAP_BOOTSTRAP_PORT_ENV,
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
    c6c_state_paths,
    compose_volume_graph_hash,
    derive_curation_service_principal_environment,
    inspect_c6c_image_source_revision,
    load_c6c_deployment_config_from_environment,
    manager_mutation_lock,
    manager_mutation_lock_path,
    postgres_server_services,
    revalidate_candidate_system_bind_snapshots,
    run_pinvi_canonical_smoke,
    validate_c6c_build_source_wiring,
    validate_c6c_operation_tokens,
    validate_compose_candidate_protected_values,
    validate_current_map_ui_auth_runtime,
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
    ensure_map_databases_isolated,
    map_application_login,
    read_database_identity,
    read_database_schema_revision,
    require_databases_resettable,
    require_map_application_database_convergible,
    require_map_bootstrap_admin_ready,
    require_map_databases_isolatable,
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
    command_output_tail,
)
from kor_travel_docker_manager.services.map_application_candidate import (
    MapApplicationCandidate,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeGeneration,
    PinnedRuntimeStatePaths,
    ensure_pinned_runtime_state_directory,
    generation_logical_sha256,
    pinned_runtime_state_paths,
)
from kor_travel_docker_manager.services.pinned_runtime_rebuild import (
    CandidateRuntimeBuild,
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
    prune_pinned_runtime_sources,
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
from kor_travel_docker_manager.services.runtime_topology import (
    COMPOSE_BUILT_RUNTIME_SLOTS,
    RUNTIME_SLOTS,
    DagsterFamily,
    RuntimeSlot,
    RuntimeTopology,
    SharedDagsterPlane,
    derive_shared_dagster_plane,
    installed_code_location,
    installed_container_name,
    installed_location_owners,
    installed_shared_dagster_plane,
    listen_address,
    runtime_topology,
    shared_workspace_source,
    slot_project,
    workspace_location_names,
)
from kor_travel_docker_manager.services.trusted_install import (
    require_pinned_runtime_rebuild_root,
    trusted_pinned_runtime_project_root,
)
from kor_travel_docker_manager.services.yaml_strict import (
    load_yaml_rejecting_duplicate_keys,
)

#: pinned 재구축이 `run`하는 one-shot writer. 옛 `kor-travel-map-dagster-storage-migrate`(옛 Map Dagster
#: metadata DB를 migrate했다)는 없다 — Map Dagster storage는 공용 `dagster_shared`이고 그 schema는
#: `kor-travel-dagster-storage-migrate`(`ensure dagster`의 init step)가 올린다. 그 DB가 막힌 뒤 이 순서에
#: 남아 있던 그 one-shot이 Map·PinVi를 멈춘 다음 실패했다(2026-10-03 22:51Z, platform-topology.md §7 4단계).
_PINNED_RUNTIME_ONESHOT_WRITERS = (
    "kor-travel-map-db-role-bootstrap",
    _MAP_APPLICATION_SCHEMA_SERVICE,
    "pinvi-admin-bootstrap",
)


def _unescape_compose(value: str) -> str:
    """compose의 `$$` escape를 컨테이너가 받는 `$`로 푼다(렌더된 값에는 보간할 `${…}`가 남지 않는다)."""

    return value.replace("$$", "$")


def _with_generation_companions(
    slots: Sequence[RuntimeSlot],
    companions: Mapping[str, RuntimeSlot],
    topology: RuntimeTopology,
) -> tuple[str, ...]:
    """slot 서비스와, 그 slot의 이미지를 공유하는 companion. slot에 서비스가 없으면(공용 plane에
    합류한 target의 daemon) 그 slot은 아무것도 띄우지 않는다."""

    return (
        *(name for name, owner in companions.items() if owner in slots),
        *topology.services_for(slots),
    )


#: 명시 `up`이 의존성을 끌어올 때 함께 닿는 API. (API에 기대는 slot, 그 API slot).
_API_DEPENDENT_SLOTS: Final[tuple[tuple[RuntimeSlot, RuntimeSlot], ...]] = (
    ("map_ui", "map_api"),
    ("map_dagster", "map_api"),
    ("map_dagster_daemon", "map_api"),
    ("pinvi_web", "pinvi_api"),
    ("pinvi_dagster", "pinvi_api"),
)
#: Dagster family를 가진 pinned target과 그 API slot.
_DAGSTER_TARGET_API_SLOTS: Final[tuple[tuple[str, RuntimeSlot], ...]] = (
    ("map", "map_api"),
    ("pinvi", "pinvi_api"),
)

_PINNED_RUNTIME_EXTERNAL_PREREQUISITES = (
    "rustfs",
    "kor-travel-geo-api",
    "kor-travel-concierge-api",
)
#: 명시 서비스의 `depends_on`까지 만들거나 다시 만들거나 시작할 수 있는 compose 명령. `--no-deps`가
#: 없으면 R3 chokepoint가 그 의존성 closure를 범위에 넣는다. `--no-deps`를 받는 명령(up·run·
#: restart·scale)과, 그 플래그 없이 의존성을 끌어오는 명령(create는 n150 Compose v5.2.0에 그
#: 플래그가 없다 — drift된 의존 PostgreSQL을 다시 만든다, start·watch)이다. stop·rm·kill·pause·
#: down은 의존성 쪽으로 번지지 않고, build·pull·push는 컨테이너를 바꾸지 않는다.
_COMPOSE_COMMANDS_THAT_REACH_DEPENDENCIES: Final = frozenset(
    {"create", "restart", "run", "scale", "start", "up", "watch"}
)


def _compose_dependency_closure(
    resolved: Mapping[str, Any],
    roots: set[str],
) -> set[str]:
    """``roots``와, resolved 문서의 `depends_on`을 따라 그것들이 끌어오는 서비스 전체.

    문서에 없는 이름(`cp`의 `SERVICE:PATH` 조각 등)은 뿌리로만 남는다. `depends_on`을 읽을 수
    없으면 closure를 모르므로 거부한다.
    """

    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("compose services mapping is unreadable")
    reached: set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        service = services.get(name)
        if not isinstance(service, Mapping):
            continue
        dependencies = service.get("depends_on") or {}
        if not isinstance(dependencies, Mapping | list | tuple) or not all(
            isinstance(dependency, str) for dependency in dependencies
        ):
            raise DeploymentContractError(f"compose service {name} depends_on is unreadable")
        pending.extend(dependencies)
    return reached


# frozen transaction은 실행 전에 one-shot service까지 exact resolved document에 결박한다.
# profile을 해석 단계에서 빼면 `run --profile bootstrap`가 같은 문서에서 service를 찾지 못한다.
_FROZEN_COMPOSE_PROFILES = ("bootstrap",)


_REBUILD_STAGE_ATTRIBUTE = "_ktdm_rebuild_stage"


@contextmanager
def _rebuild_stage(stage: str) -> Iterator[None]:
    """실패한 재구축 단계 이름을 예외에 붙이고 **원래 예외를 그대로** 다시 던진다.

    ADR-51 잃는 보장 G: 예전에는 이 자리가 원인을 고정 문구 하나로 봉인했다. 이제 타입·
    메시지·traceback은 바뀌지 않고 단계 이름만 더해진다. 가장 안쪽 단계가 이긴다.
    """

    try:
        yield
    except Exception as exc:
        if rebuild_failure_stage(exc) is None:
            try:
                setattr(exc, _REBUILD_STAGE_ATTRIBUTE, stage)
            except (AttributeError, TypeError):  # pragma: no cover - 속성을 못 받는 예외
                pass
        raise


def rebuild_failure_stage(exc: BaseException) -> str | None:
    """재구축 실패가 난 단계 이름. 단계 밖(journal 뒤 배포 본문 등)이면 ``None``."""

    stage = getattr(exc, _REBUILD_STAGE_ATTRIBUTE, None)
    return stage if isinstance(stage, str) else None


_ROLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class PinnedRuntimeComposeFailure(DeploymentContractError):
    """pinned runtime rebuild Compose 실행 실패."""


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


def _read_map_source_file(
    repository: Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> bytes | None:
    """materialize된 Map source root 아래의 파일 하나를 읽는다. 없으면 ``None``.

    root는 핀된 revision 그대로의 트리다(재구축이 materialize한 source). 그래서 파일이 곧
    그 revision의 blob이다 — git을 부르지 않는다(ADR-51 E). 일반 파일이 아니거나 실행
    비트가 있거나 ``max_bytes``보다 크면 ``DeploymentContractError``로 거부하고, 호출자가
    자기 문맥의 문구를 고를 수 있게 사유만 짧게 싣는다.
    """

    path = repository / relative_path
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DeploymentContractError("unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o111:
        raise DeploymentContractError("not a regular non-executable file")
    if metadata.st_size > max_bytes:
        raise DeploymentContractError("too large")
    try:
        with path.open("rb") as handle:
            content = handle.read(max_bytes + 1)
    except OSError as exc:
        raise DeploymentContractError("unreadable") from exc
    if len(content) > max_bytes:
        raise DeploymentContractError("too large")
    return content


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
_MAP_SOURCE_MANIFEST_MAX_BYTES = 4 * 1024 * 1024


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
    payload: Mapping[str, Any],
) -> None:
    """source compose env_file의 경로·옵션과 source 트리에 있는 내용을 고정한다."""

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
    for referenced_path in sorted(referenced_paths):
        try:
            raw_content = _read_map_source_file(
                repository,
                referenced_path,
                max_bytes=_MAP_SOURCE_TRACKED_ENV_FILE_MAX_BYTES,
            )
        except DeploymentContractError as exc:
            if str(exc) == "too large":
                raise DeploymentContractError(
                    "Map source environment contract tracked env_file exceeds 64 KiB"
                ) from exc
            raise DeploymentContractError(
                "Map source environment contract tracked env_file is not a regular 100644 blob"
            ) from exc
        if raw_content is None:
            # 핀된 revision이 추적하지 않는 파일이다 — runtime에만 생긴다.
            continue
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
    # root는 핀된 revision 그대로의 트리다 — 그 파일이 곧 그 revision의 compose다(ADR-51 E).
    try:
        raw_manifest = _read_map_source_file(
            repository,
            "docker-compose.yml",
            max_bytes=_MAP_SOURCE_MANIFEST_MAX_BYTES,
        )
    except DeploymentContractError as exc:
        raise DeploymentContractError(
            "Map source environment contract manifest is unreadable"
        ) from exc
    if raw_manifest is None:
        raise DeploymentContractError(
            "Map source environment contract manifest is missing"
        )
    try:
        payload = _load_unique_map_source_yaml(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
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
    _validate_map_source_env_files(repository, payload)
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
    # ADR-51 C-3: lock 경로는 `.env` 파일의 값으로 정한다. 종전에는 비운영 분기에서
    # 파일에 없는 이름을 프로세스 환경으로 채웠다 — 신뢰 결정의 입력을 프로세스 env에서
    # 읽지 않는다(ADR-41 교훈). 모드가 없으면 host 변경 lock ``G``다(fail closed).
    # 단 프로세스 환경이 **명시적으로** local이 아니면 파일이 local이어도 ``G``다 — 파일만
    # 고치고 재시작하지 않은 backend는 모드 검사를 프로세스 env(운영)로 하면서 lock은
    # `$HOME`으로 잡아 "한 번에 한 mutator"가 깨진다. 이 분기는 더 엄격한 쪽으로만 간다.
    lock_path = manager_mutation_lock_path(values)
    process_mode = os.environ.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
    if process_mode not in ("", "local"):
        lock_path = manager_mutation_lock_path({"KTDM_DEPLOYMENT_ENVIRONMENT": process_mode})
    return C6cDeploymentLockSnapshot(
        lock_path=lock_path,
        env_path=env_path,
        env_file_identity=before,
        env_file_sha256=hashlib.sha256(raw).hexdigest(),
    )


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
    prewrite_admission: Callable[["ComposeEnvironmentSnapshot"], None],
) -> Iterator["ComposeEnvironmentSnapshot"]:
    """rebuild 전용 lock/snapshot 순서(ADR-51 C-3).

    host 변경 lock ``G`` 하나를 잡은 **뒤에** trusted `/opt` root `.env`만 process
    ambient 없이 frozen snapshot으로 읽는다. lifecycle 게이트·token 검증·non-mutating
    admission을 통과해야 본문으로 넘어가며, 본문 전체가 같은 ``G`` 안에서 돈다.
    launcher가 물려준 G fd가 있으면 ``manager_mutation_lock()``이 그것을 검증해 쓴다.
    """

    with manager_mutation_lock():
        with _rebuild_stage("environment_admission"):
            environment_snapshot = _capture_pinned_runtime_rebuild_environment_snapshot()
        # 배포 lifecycle 게이트는 단계 밖이다 — 거부 문장 자체가 운영자가 알아야 할 전부다.
        assert_pinned_runtime_rebuild_allowed(environment=environment_snapshot.effective)
        with _rebuild_stage("environment_admission"):
            validate_c6c_operation_tokens(
                environment_snapshot.effective,
                require_nonempty=True,
            )
        # M05 폐기 전에는 여기서 Manager가 PinVi role 자격증명을 생성해 **루트
        # `.env`에 써 넣고** 그 위에서 두 번째 snapshot을 떴다. geo 패턴에서는
        # 자격증명이 하나뿐이고 그것은 운영자가 `.env`에 둔 `PINVI_APP_DB_PASSWORD`
        # 이므로, rebuild가 `.env`를 변형할 이유가 사라졌다 — snapshot도 하나다.
        prewrite_admission(environment_snapshot)
        yield environment_snapshot


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
        # 양쪽 다 resolve한다 — root는 release symlink라 한쪽만 풀면 정당한 값이 거부된다.
        if (
            not configured.is_absolute()
            or configured.resolve(strict=False) != expected_path.resolve(strict=False)
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


def _assert_env_file_evidence_matches(
    environment_snapshot: "ComposeEnvironmentSnapshot",
    *,
    env_path: Path,
    env_file_identity: ComposeEnvFileIdentity,
    env_file_sha256: str,
    reference: str,
) -> None:
    """transaction이 쓰는 `.env`가 기준 캡처와 같은 파일·identity·바이트인지 본다."""

    if Path(environment_snapshot.env_path).resolve(strict=False) != env_path:
        raise ComposeCandidateContractError(
            f"compose transaction env-file path differs from {reference}"
        )
    if environment_snapshot.env_file_identity != env_file_identity:
        raise ComposeCandidateContractError(
            f"compose transaction env-file identity differs from {reference}"
        )
    if hashlib.sha256(environment_snapshot.env_file_bytes).hexdigest() != env_file_sha256:
        raise ComposeCandidateContractError(
            f"compose transaction env-file bytes differ from {reference}"
        )


def assert_environment_snapshot_matches_c6c_lock(
    environment_snapshot: "ComposeEnvironmentSnapshot",
    lock_snapshot: C6cDeploymentLockSnapshot,
) -> None:
    # ADR-51 C-3: lock 경로 동등 검사는 지웠다. lock 경로는 이 `.env` 바이트만으로
    # 정해지므로(`manager_mutation_lock_path`) 바이트 해시가 같으면 같은 lock이다.
    # effective에 겹친 프로세스 환경은 lock 선택에 쓰지 않는다.
    _assert_env_file_evidence_matches(
        environment_snapshot,
        env_path=lock_snapshot.env_path,
        env_file_identity=lock_snapshot.env_file_identity,
        env_file_sha256=lock_snapshot.env_file_sha256,
        reference="deployment lock snapshot",
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
    # compose 경로는 resolve하지 않는다(ADR-51 D). 이 경로의 부모가 `--project-directory`가
    # 되고, 풀린 release 경로가 들어가면 상대 bind source가 지워질 release에 묶인다.
    compose_path = Path(
        os.path.abspath(get_compose_path() if compose_path is None else compose_path)
    )
    override_path = Path(
        os.path.abspath(get_override_path() if override_path is None else override_path)
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


# 이 타임아웃은 멈춤 감지용이지 성능 예산이 아니다. n150은 SATA SSD가 92% 차서 IO
# 압력 `full`이 상시 50~60%이고, 2026-09-26 실측에서 `docker run --rm /bin/true` 하나가
# 112초, `ktm-application-schema head`가 74초 걸렸다 — 60초였을 때 t57a가 명령은
# 정상인데 타임아웃으로 죽었다.
_PINNED_RUNTIME_STATIC_INSPECTION_TIMEOUT_SECONDS = 600
#: compose `--wait-timeout` 초. **정수**로 둔다 — head는 revision 문자열이라
#: 형이 다르고, 이 파일에 따옴표 두른 숫자가 남지 않아 head 리터럴 게이트가
#: 파일 단위 면제 없이 이 파일을 전부 볼 수 있다. 면제는 그 자체로 사각지대였다.
#:
#: ADR-069 뒤 Map은 code-server → webserver → daemon이 `service_healthy`로 **직렬**
#: 기동한다. 위 실측대로 컨테이너 하나가 뜨는 데만 1~2분이 걸리므로 300초는 부족하다.
_COMPOSE_WAIT_TIMEOUT_SECONDS: Final = 900
#: 공용 plane이 **이 재구축의** location을 싣기까지 기다리는 상한과 간격(ADR-54 개정, 적대 리뷰 H1). 다른 테넌트의
#: location·code-server 상태는 보지 않는다 — plane의 healthcheck(`--wait`)는 그것까지 봐서 Map 배포를 geo·weather에
#: 묶었다.
_SHARED_PLANE_PROBE_TIMEOUT_SECONDS: Final = 300.0
_SHARED_PLANE_PROBE_INTERVAL_SECONDS: Final = 5.0
_SHARED_PLANE_WORKSPACE_QUERY: Final = (
    "{ workspaceOrError { __typename ... on Workspace { locationEntries { name "
    "locationOrLoadError { __typename } } } } }"
)


def _run_pinned_runtime_static_command(
    image_id: str,
    command: Sequence[str],
    *,
    label: str,
    entrypoint: str | None = None,
) -> str:
    """candidate artifact를 network 없이 검사한다. 성공 출력은 호출자가 파싱한다."""

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
        raise DeploymentContractError(
            f"{label} candidate static inspection failed (exit {completed.returncode})"
            + command_output_tail("stderr", completed.stderr)
            + command_output_tail("stdout", completed.stdout)
        )
    return completed.stdout


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
                capture_output=True,
                check=False,
                timeout=3600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                "application 300 image build could not start"
            ) from exc
        if completed.returncode != 0:
            # buildx는 진행과 오류를 stderr로 낸다 — 끝부분에 실패한 단계가 있다.
            raise DeploymentContractError(
                f"application 300 image build failed ({dockerfile}, exit {completed.returncode})"
                + command_output_tail("stderr", completed.stderr)
            )


def _inspect_local_image_id(image: str) -> str:
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            cwd="/",
            capture_output=True,
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
            f"application 300 image {image} cannot be inspected"
            + command_output_tail("stderr", completed.stderr)
        )
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
                stderr=subprocess.PIPE,
                check=False,
                timeout=900,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                "Map application immutable base image is unavailable"
            ) from exc
        if pulled.returncode != 0:
            raise DeploymentContractError(
                f"Map application immutable base image {image_reference} is unavailable"
                + command_output_tail("docker pull stderr", pulled.stderr)
            )
        try:
            verified = subprocess.run(
                ["docker", "image", "inspect", image_reference],
                cwd="/",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                "Map application immutable base image is unavailable"
            ) from exc
        if verified.returncode != 0:
            raise DeploymentContractError(
                f"Map application immutable base image {image_reference} is unavailable "
                "after pull" + command_output_tail("stderr", verified.stderr)
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
        `rebuild_pinned_runtime`은 `_pinned_runtime_rebuild_environment_lock()`이
        `manager_mutation_lock()`으로 host 변경 lock ``G``를 직접 잡아 같은 flock에
        도달한다(ADR-51 C-3) — 어느 경로든 "이 host의 c6c deployment lock을 쥔 채로"만
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
                    # resolve 금지 — 위 `_capture_compose_environment_snapshot`과 같은 이유.
                    os.path.dirname(os.path.abspath(compose_path or get_compose_path())),
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
    ) -> dict[str, Any]:
        return self.run(
            args,
            capture_output=capture_output,
            mutation_capability=mutation_capability,
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
                # stderr만 싣는다 — stdout은 비밀이 보간된 설정 문서 자체다.
                raise ComposeCandidateContractError(
                    "compose candidate resolution failed"
                    + command_output_tail("stderr", completed.stderr)
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
        """Compose 명령을 read-only allowlist로 분류하고 mutation 대상을 보수적으로 찾는다.

        명시 서비스가 없거나 해석할 수 없는 mutation은 두 API 전부에 닿는다고 본다.
        """

        scope = ComposeService._compose_mutation_scope(args)
        if scope is None:
            return [*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE]
        return scope

    @staticmethod
    def _compose_command_index(args: Sequence[str]) -> int | None:
        """전역 옵션을 건너뛴 compose 하위 명령의 위치. 전역 옵션을 해석할 수 없으면 ``None``."""

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
                    return None
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
                    return None
                continue
            if item.startswith("-"):
                if item not in global_flags:
                    return None
                continue
            command_index = index
            break
        return command_index

    @staticmethod
    def _compose_mutation_scope(args: Sequence[str]) -> list[str] | None:
        """read-only면 ``[]``, 명시 서비스가 있는 mutation이면 그 식별자, 그 밖은 ``None``.

        ``None``은 "이 호출이 무엇을 바꾸는지 서비스 이름으로 말할 수 없다"는 뜻이다 — 인자가
        없거나, 해석에 실패했거나, 모르는 명령이거나, 서비스를 명시하지 않은 mutation(compose가
        **모든** 서비스로 읽는다)이다. 명시 mutation의 목록은 비지 않으므로 ``[]``와 섞이지 않는다.
        """

        return ComposeService._parse_compose_mutation(args)[0]

    @staticmethod
    def _parse_compose_mutation(
        args: Sequence[str],
    ) -> tuple[list[str] | None, frozenset[str]]:
        """``_compose_mutation_scope``의 범위와, 명시 mutation에서 compose가 **플래그로 읽은** 명령 옵션.

        `--no-deps`·`--remove-orphans`는 argv 어디에 있느냐가 아니라 compose가 그것을 플래그로
        읽었느냐로 센다. `run SERVICE` 뒤는 컨테이너 argv이고, 값을 받는 옵션의 값 자리도 플래그가
        아니다 — `run … SERVICE --no-deps`는 의존성을 끌어온다(적대 리뷰 2026-09-29). 범위가
        ``None``이거나 ``[]``이면 플래그는 비어 있다.
        """

        # 파생(runtime_topology)은 **해석이 끝난 뒤, 그것이 필요한 mutation에서만** 한다 — read-only
        # (`ps`·`config`·`logs`)와 명시 서비스 `stop`·`rm`은 설치된 모델을 읽지 않는다. 모델이 깨져도 보고
        # 멈추는 길은 남는다(적대 리뷰 2026-09-30 MED-1).
        if not args:
            return None, frozenset()
        command_index = ComposeService._compose_command_index(args)
        if command_index is None:
            return None, frozenset()
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
                    return None, frozenset()
                if item in read_options_with_value:
                    if index + 1 >= len(config_items):
                        return None, frozenset()
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
                        return None, frozenset()
                    continue
                if item not in read_flags:
                    return None, frozenset()
            return [], frozenset()
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
            return [], frozenset()
        if command == "wait":
            if any(
                item == "--down-project" or item.startswith("--down-project=")
                for item in args
            ):
                return None, frozenset()
            wait_items = args[command_index + 1 :]
            if any(item.startswith("-") for item in wait_items):
                return None, frozenset()
            return [], frozenset()
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
            return None, frozenset()
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
        parsed_flags: set[str] = set()
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
                    return None, frozenset()
                explicit_services.append(service)
                skip_next = True
                continue
            if item == "--scale":
                return None, frozenset()
            if item.startswith("--scale="):
                service = item.removeprefix("--scale=").partition("=")[0]
                if not service:
                    return None, frozenset()
                explicit_services.append(service)
                continue
            if command == "scale" and "=" in item and not item.startswith("-"):
                explicit_services.append(item.partition("=")[0])
                continue
            if item in options_with_value:
                if index + 1 >= len(items):
                    return None, frozenset()
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
                    return None, frozenset()
                continue
            if item.startswith("-"):
                if item not in flag_options:
                    return None, frozenset()
                parsed_flags.add(item)
                continue
            explicit_services.append(item)
        if explicit_services:
            explicit_services.extend(
                item.partition(":")[0]
                for item in tuple(explicit_services)
                if ":" in item
            )
            if command in {"up", "create", "restart", "watch"} and "--no-deps" not in parsed_flags:
                # API에 기대는 slot 서비스 → 그 API. Dagster slot은 스위치를 따른다(ADR-54). 공용
                # plane에 합류해 `legacy-dagster`로 내려간 옛 서비스도 명시하면 compose가 띄우고 그
                # `depends_on`(API)까지 끌어오므로 같은 API에 닿는다고 센다.
                topology = runtime_topology()
                api_dependencies = {
                    service: topology.require_service(api_slot)
                    for slot, api_slot in _API_DEPENDENT_SLOTS
                    if (service := topology.service(slot)) is not None
                }
                for target_id, api_slot in _DAGSTER_TARGET_API_SLOTS:
                    for retired in topology.families[target_id].retired:
                        api_dependencies[retired] = topology.require_service(api_slot)
                explicit_services.extend(
                    api_dependencies[service]
                    for service in tuple(explicit_services)
                    if service in api_dependencies
                )
            if "--remove-orphans" in parsed_flags:
                explicit_services.extend([*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE])
            return explicit_services, frozenset(parsed_flags)
        # down/rm --all/unknown command/option parse failure may affect either API.
        return None, frozenset()

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
    ) -> dict[str, Any]:
        compose_action = self._pinned_runtime_compose_action(args)
        # compose가 플래그로 읽은 `--no-deps`만 센다(`run SERVICE` 뒤 컨테이너 argv는 아니다).
        # 서비스를 말하지 않거나 해석할 수 없는 호출은 아래 R3가 거부한다.
        command_index = self._compose_command_index(args)
        scope, flags = self._parse_compose_mutation(args)
        if (
            scope
            and command_index is not None
            and args[command_index] in {"run", "up"}
            and "--no-deps" not in flags
        ):
            raise DeploymentContractError(
                "pinned runtime rebuild Compose startup requires --no-deps"
            )
        self._require_rebuild_compose_spares_foreign_postgres(args, transaction=transaction)
        # Compose turns a multi-target build into one BuildKit bake request.  On
        # the small n150 host that request opens several frontend sessions at
        # once; a second build (for example an unrelated tvnm05 build) can then
        # exhaust the daemon's single-session limit and leave every target
        # waiting until its context deadline.  Keep the frozen transaction and
        # provenance checks identical, but give each candidate service its own
        # BuildKit request so a target completes before the next one starts.
        built_services = runtime_topology().services_for(COMPOSE_BUILT_RUNTIME_SLOTS)
        if tuple(args) == (
            "build",
            *built_services,
        ):
            build_result: dict[str, Any] = {}
            for service in built_services:
                # 실패 메시지의 명령(`build <service>`)이 어느 서비스인지 말한다.
                build_result = self._run_pinned_runtime_rebuild_compose(
                    ["build", service],
                    transaction=transaction,
                    capture_output=capture_output,
                )
            return build_result
        result = self._run_frozen_recovery(
            args,
            transaction=transaction,
            mutation_capability=_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
            capture_output=capture_output,
        )
        if result["success"]:
            return result
        # 원인 원문을 싣는다(ADR-51 잃는 보장 G). one-shot `run`은 원인(migration
        # traceback, typed error JSON)을 컨테이너 stdout으로도 낸다. 그 밖의 명령의
        # stdout은 원인이 아니라 데이터(`ps --format json`)라 싣지 않는다.
        tail = command_output_tail("stderr", result.get("stderr"))
        if compose_action == "run":
            tail += command_output_tail("stdout", result.get("stdout"))
        raise PinnedRuntimeComposeFailure(
            f"pinned runtime rebuild Compose {' '.join(args)} failed "
            f"(exit {result['returncode']}){tail}"
        )

    @staticmethod
    def _require_rebuild_compose_spares_foreign_postgres(
        args: Sequence[str],
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        """재구축은 PostgreSQL 서버 서비스를 **하나도** 바꾸지 않는다(R3 chokepoint, ADR-53).

        Map·PinVi DB는 모든 tenant가 같이 쓰는 공용 instance에 산다. 그 컨테이너를 재구축이
        멈추거나 다시 만들면 모든 tenant가 끊긴다 — 재구축은 instance를 readiness로만 본다
        (`_require_pinned_runtime_database_instances_ready`). 그래서 재구축의 모든 compose 호출이
        지나는 이 한 자리에서, 무엇을 바꾸는지 서비스 이름으로 말할 수 없는 mutation(명시 서비스
        없음·해석 불가)을 거부하고, 명시 식별자 가운데 PostgreSQL 서버가 있으면 거부한다. M1까지는
        Map 전용 instance 하나가 예외였고, 그 예외가 사라져 울타리가 절대가 됐다. mutation 분류와
        식별자는 기존 해석기(`_parse_compose_mutation`)가, PostgreSQL 서버 판정은
        C6c(`postgres_server_services`)가 소유한다 — 이름 목록이 없다.

        **compose가 실제로 닿는 것을 센다.** 이름 붙은 서비스만 보면 `create pinvi-api`가
        통과하고, compose는 drift된 공용 instance를 의존성으로 다시 만든다(n150 실측). 그래서
        의존성으로 번지는 명령(`_COMPOSE_COMMANDS_THAT_REACH_DEPENDENCIES`)이 `--no-deps` 없이
        오면 frozen resolved 문서의 `depends_on` closure를 범위에 넣는다 — resolved 문서는 links·
        `network_mode: service:`·`volumes_from`도 `depends_on`으로 정규화해 담는다. 이름 없는
        컨테이너를 지우는 `--remove-orphans`와, 서비스 목록을 읽을 수 없는 문서는 거부한다.
        두 플래그는 compose가 플래그로 읽은 것만 센다(``_parse_compose_mutation``) — `run SERVICE`
        뒤의 컨테이너 argv에 같은 글자가 있어도 compose는 의존성을 끌어온다.
        """

        scope, flags = ComposeService._parse_compose_mutation(args)
        if scope is None:
            raise DeploymentContractError(
                "pinned runtime rebuild Compose mutation must name its services explicitly"
            )
        if not scope:
            return
        if "--remove-orphans" in flags:
            raise DeploymentContractError(
                "pinned runtime rebuild Compose must not remove orphan containers"
            )
        postgres = postgres_server_services(transaction.resolved)
        touched = set(scope)
        command_index = ComposeService._compose_command_index(args)
        if (
            command_index is not None
            and args[command_index] in _COMPOSE_COMMANDS_THAT_REACH_DEPENDENCIES
            and "--no-deps" not in flags
        ):
            touched |= _compose_dependency_closure(transaction.resolved, touched)
        foreign = sorted(postgres & touched)
        if foreign:
            raise DeploymentContractError(
                "pinned runtime rebuild must not mutate a PostgreSQL service: "
                f"{', '.join(foreign)}"
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

    def _retire_pinned_runtime_oneshot_writers(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        """reset 전 frozen project one-shot writer를 제거하고 부재를 증명한다.

        `docker compose run --rm`의 Manager process가 강제 종료되면 Docker
        container가 계속 DB에 연결할 수 있다. 동일 frozen project/service label로만
        stop+remove한 뒤 `ps --all`에서 그 서비스들이 전부 사라진 것을 확인한다.
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
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                "PinVi fresh migration fence could not be established"
            ) from exc
        if completed.returncode != 0:
            raise DeploymentContractError(
                "PinVi fresh migration fence could not be established "
                f"(psql exit {completed.returncode})"
                + command_output_tail("psql stderr", completed.stderr)
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
        지점이 없으니 PinVi role lifecycle 오류로 감쌀 단계가 남지 않는다.
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
    def _inspect_container_running(container_name: str, *, label: str) -> bool | None:
        """컨테이너가 돌고 있는가. 없으면 ``None``. 읽을 수 없으면 거부한다(fail-closed)."""

        try:
            completed = subprocess.run(
                ["docker", "container", "inspect", "--format={{.State.Running}}", container_name],
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(f"cannot inspect the {label} container") from exc
        state = completed.stdout.strip()
        if completed.returncode == 0 and state in {"true", "false"}:
            return state == "true"
        if completed.returncode == 1 and "no such container" in completed.stderr.lower():
            return None
        raise DeploymentContractError(
            f"cannot inspect the {label} container" + command_output_tail("stderr", completed.stderr)
        )

    def _require_retired_dagster_containers_stopped(self, topology: RuntimeTopology) -> None:
        """공용 plane에 합류한 target의 옛 webserver·daemon·gateway 컨테이너가 돌고 있지 않은가(ADR-54).

        전환 전에는 전체 경로의 첫 `stop`이 Map daemon을 멈춰 migration 동안 run을 띄우지 못하게 했다. 전환 뒤
        그 서비스는 `legacy-dagster`라 frozen render에 없고 `stop`에도 들지 않는다 — 그래서 docker에서 컨테이너
        이름(설치된 targets의 `containers`에서 파생)으로 직접 본다. 돌고 있으면 무엇도 멈추거나 migration하기
        전에 거부한다. `own`이면 볼 것이 없다.
        """

        running = [
            f"{service} ({container})"
            for service in topology.retired_services
            if self._inspect_container_running(
                container := installed_container_name(service), label=service
            )
        ]
        if running:
            raise DeploymentContractError(
                "retired Dagster services of a target on the shared control plane are still "
                "running; stop them before the pinned rebuild: " + ", ".join(running)
            )

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
    ) -> dict[RuntimeSlot, str]:
        built_image_ids = {
            slot: self._inspect_image_reference_id(
                build.image_names[slot],
                label=build.topology.require_service(slot),
            )
            for slot in COMPOSE_BUILT_RUNTIME_SLOTS
        }
        map_revision = build.sources.release.source_for("map").revision
        pinvi_revision = build.sources.release.source_for("pinvi").revision
        for slot in COMPOSE_BUILT_RUNTIME_SLOTS:
            service = build.topology.require_service(slot)
            project = slot_project(slot)
            expected_revision = map_revision if project == "map" else pinvi_revision
            observed_revision = self._inspect_image_source_revision(
                built_image_ids[slot],
                label=service,
                expected_build_environment=("production" if project == "pinvi" else None),
            )
            if observed_revision != expected_revision:
                raise DeploymentContractError(
                    f"{service} candidate image revision differs from the release pin"
                )
        image_ids: dict[RuntimeSlot, str] = {
            "map_api": map_candidate.api_image_id,
            "map_ui": built_image_ids["map_ui"],
            "map_dagster": map_candidate.dagster_image_id,
            "map_dagster_daemon": map_candidate.dagster_image_id,
            "pinvi_api": built_image_ids["pinvi_api"],
            "pinvi_web": built_image_ids["pinvi_web"],
            "pinvi_dagster": built_image_ids["pinvi_dagster"],
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
                build.topology.require_service(slot): (
                    map_context if slot_project(slot) == "map" else pinvi_context
                )
                for slot in COMPOSE_BUILT_RUNTIME_SLOTS
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
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime],
    ) -> dict[DatabaseRole, DeployedDatabase] | None:
        """두 DB(Map application·PinVi)의 identity. 하나라도 없으면 ``None``."""

        observed: dict[DatabaseRole, DeployedDatabase] = {}
        for runtime in runtimes:
            identity = read_database_identity(runtime)
            if identity is None:
                return None
            observed[runtime.role] = DeployedDatabase(*identity)
        return observed

    @staticmethod
    def _observe_schema_heads(
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime],
    ) -> dict[str, str] | None:
        """두 DB(Map application·PinVi)의 Alembic head. 하나라도 읽을 수 없으면 ``None``."""

        try:
            return {
                runtime.role: read_database_schema_revision(runtime) for runtime in runtimes
            }
        except DeploymentContractError:
            return None

    @staticmethod
    def _deployed_images(
        candidate: PinnedRuntimeGeneration,
        companions: Mapping[str, RuntimeSlot],
        topology: RuntimeTopology | None = None,
    ) -> dict[str, str]:
        """slot 서비스는 자기 이미지, companion은 owner slot의 이미지다. 서비스 없는 slot은 없다."""

        topology = runtime_topology() if topology is None else topology
        slot_images = candidate.image_ids
        images = {
            service: slot_images[slot]
            for slot in RUNTIME_SLOTS
            if (service := topology.service(slot)) is not None
        }
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

        실패는 원래 예외 그대로 올라간다. 단계 안에서 났으면 그 이름이 붙는다
        (``rebuild_failure_stage``) — CLI가 JSON 판정에 싣는다.
        """

        if restart_reason is not None and adopt_reason is not None:
            raise DeploymentContractError(
                "a deploy either restarts or adopts the live databases, not both"
            )
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
        ) -> None:
            nonlocal release
            del environment_snapshot
            # lock을 잡은 뒤 registry snapshot 하나를 만든다 — rotate가 두 read 사이에
            # 끼어 old release와 new 상태를 섞지 못하게 한다.
            release = current_pinned_runtime_release()
            warnings.extend(_pinned_runtime_admission_warnings(release.pinset_sha256))

        with _pinned_runtime_rebuild_environment_lock(
            prewrite_admission=prewrite_admission
        ) as environment_snapshot:
            if release is None:  # pragma: no cover - context contract 방어
                raise DeploymentContractError("pinned runtime release snapshot is unavailable")
            values = environment_snapshot.effective
            with _rebuild_stage("state_initialization"):
                validate_c6c_operation_tokens(values, require_nonempty=True)
                state_paths = pinned_runtime_state_paths(
                    values,
                    pinset_sha256=release.pinset_sha256,
                )
                ensure_pinned_runtime_state_directory(state_paths.state_root)
                status_path = deploy_status_path(state_paths.state_root)
                previous = read_deploy_status(status_path)
            with _rebuild_stage("prebuild_snapshot"):
                prebuild_transaction, _ = self.capture_transaction_unlocked(
                    environment_snapshot=environment_snapshot,
                )
            with _rebuild_stage("external_prerequisites"):
                self._require_services_ready(
                    _PINNED_RUNTIME_EXTERNAL_PREREQUISITES,
                    transaction=prebuild_transaction,
                    frozen_recovery=True,
                )
            with _rebuild_stage("source_materialization"):
                sources = materialize_pinned_runtime_sources(
                    release=release,
                    state_paths=state_paths,
                )
                # 이번 pair가 쓰지 않는 옛 revision·끊긴 시도를 지운다(G 안, 실패해도 배포는 계속).
                prune_pinned_runtime_sources(state_paths, keep=sources)
            # 공용 Dagster plane(ADR-54) 스위치를 포함한 slot → 서비스. 한 배포는 한 모양으로 돈다.
            topology = runtime_topology()
            with _rebuild_stage("application_base_images"):
                paired_build_images = map_application_300_paired_build_image_names(
                    sources, topology
                )
                _ensure_map_application_300_python_base_images(sources)
            with _rebuild_stage("application_builder"):
                # 이미지 태그는 pinset에 묶인다. 이미 있으면 같은 소스에서 나온 것이므로
                # 다시 빌드하지 않는다 — 다시 빌드하면 재현되지 않는 digest가 나와 같은
                # pair의 재실행이 "새 이미지"가 된다.
                if not all(_local_image_present(ref) for ref in paired_build_images.values()):
                    _build_map_application_300_images(
                        sources=sources,
                        api_image=paired_build_images["map_api"],
                        dagster_image=paired_build_images["map_dagster"],
                    )
            with _rebuild_stage("application_candidate"):
                map_candidate = _load_application_300_candidate(
                    sources=sources,
                    api_image=paired_build_images["map_api"],
                    dagster_image=paired_build_images["map_dagster"],
                )
                build = CandidateRuntimeBuild(
                    sources=sources,
                    map_application_candidate=map_candidate,
                    topology=topology,
                )
                candidate_build_references = {**paired_build_images, **build.image_names}
            candidate_environment = {
                **build.compose_environment(),
                "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": map_candidate.application_head,
            }
            with _rebuild_stage("candidate_snapshot"):
                candidate_transaction, _ = self.capture_transaction_unlocked(
                    environment_override=candidate_environment,
                    environment_snapshot=environment_snapshot,
                )
            with _rebuild_stage("candidate_contract"):
                self._validate_pinned_runtime_candidate_build_contract(
                    candidate_transaction,
                    build=build,
                    environment_override=candidate_environment,
                )
            with _rebuild_stage("candidate_compose_build"):
                if not all(_local_image_present(ref) for ref in build.image_names.values()):
                    self._run_pinned_runtime_rebuild_compose(
                        ["build", *build.build_services],
                        transaction=candidate_transaction,
                    )
            with _rebuild_stage("candidate_images"):
                image_ids = self._attest_pinned_runtime_candidate_images(
                    build=build,
                    map_candidate=map_candidate,
                )
            with _rebuild_stage("candidate_bootstrap_settings"):
                self._verify_pinned_runtime_pinvi_bootstrap_settings(
                    transaction=candidate_transaction,
                )
            with _rebuild_stage("candidate_heads"):
                # Map application head는 `_load_application_300_candidate`가 이미 한 번
                # 관측했다. PinVi head는 후보 이미지를 network-less로 한 번 돌린다. 옛 Map Dagster
                # storage head는 묻지 않는다 — 그 metadata DB는 퇴역했다(platform-topology.md §7 4단계).
                pinvi_head = parse_candidate_static_head(
                    _run_pinned_runtime_static_command(
                        image_ids["pinvi_api"],
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
                    pinvi_head=pinvi_head,
                )
            with _rebuild_stage("runtime_generation"):
                runtime_environment = {
                    **build.compose_environment(),
                    **generation_compose_environment(candidate),
                    "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": candidate.map_application_head,
                }
            with _rebuild_stage("runtime_transaction"):
                runtime_transaction, _ = self.capture_transaction_unlocked(
                    environment_override=runtime_environment,
                    environment_snapshot=environment_snapshot,
                )
                companions = generation_companion_services(
                    runtime_transaction.resolved,
                    candidate.image_ids,
                    excluded_services=_PINNED_RUNTIME_ONESHOT_WRITERS,
                    topology=topology,
                )
            ensure_generation_references((candidate,), cwd=get_project_root())
            runtimes = database_runtimes_from_frozen_contract(
                resolved=runtime_transaction.resolved,
                environment=runtime_transaction.environment.effective,
            )
            # R4가 Map application DB에 CONNECT를 줄 login. 멈추기 전에 유도해 둔다.
            map_login = map_application_login(runtime_transaction.environment.effective)
            expected_images = self._deployed_images(candidate, companions, topology)
            # 수렴 판정과 identity 기준선은 **실제로 migration할 cluster**를 읽어야 한다. 그
            # cluster는 공용 instance이고 재구축이 띄우거나 다시 만들지 않는다(R3) — 판정 전에
            # frozen Compose의 그 컨테이너가 떠 있고 healthy인지만 본다.
            self._require_pinned_runtime_database_instances_ready(
                runtimes,
                transaction=runtime_transaction,
            )
            # 수렴이든 전체 경로든 무엇을 멈추거나 migration하기 전에 — 전환된 target의 옛 daemon은
            # `stop`에 들지 않으므로 떠 있으면 migration 중 run을 띄운다.
            self._require_retired_dagster_containers_stopped(topology)
            self._require_shared_plane_releases_own_targets(topology, runtime_transaction)

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
                    topology=topology,
                    companions=companions,
                    expected_images=expected_images,
                    runtimes=runtimes,
                    map_login=map_login,
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

            # 전체 경로가 DB 앞에서 거부할 상태라면 런타임을 멈추기 **전에** 읽기만으로 거부한다.
            # 결박은 각 단계 안의 같은 판정이다 — 여기서는 멈춘 뒤의 거부를 앞당길 뿐이다.
            if restart is not None:
                # `--restart`의 R2(이름·허용 소유자·Map 소유자 배타성). 리셋 뒤에는 Map fresh
                # bootstrap이 instance admin으로 돈다(S1).
                require_databases_resettable(runtimes)
                require_map_bootstrap_admin_ready(
                    runtimes[0],
                    resolved=runtime_transaction.resolved,
                    environment=runtime_transaction.environment.effective,
                )
            elif require_map_application_database_convergible(runtimes[0]) == "present":
                # R4의 live 전제. 넘겨받은 app DB를 전체 경로는 R4 전에 바꾸지 않는다(없거나
                # bootstrap 전인 DB는 만든 뒤 R4가 판정한다).
                require_map_databases_isolatable(runtimes[0], login=map_login)
            else:
                # 앱 DB가 없거나 bootstrap 전이다 — role bootstrap이 instance admin으로 돈다(S1).
                require_map_bootstrap_admin_ready(
                    runtimes[0],
                    resolved=runtime_transaction.resolved,
                    environment=runtime_transaction.environment.effective,
                )

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
            try:
                committed = self._deploy_forward(
                    status=status,
                    status_path=status_path,
                    restart=restart is not None,
                    candidate=candidate,
                    runtimes=runtimes,
                    runtime_transaction=runtime_transaction,
                    topology=topology,
                    companions=companions,
                    expected_images=expected_images,
                    state_paths=state_paths,
                    values=values,
                    map_login=map_login,
                )
            except Exception:
                try:
                    self._run_pinned_runtime_rebuild_compose(
                        ["stop", *topology.runtime_services, *companions],
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
            # 처음부터 다시 돈다(멱등). 커밋이 남기는 기록은 deploy-status.json 하나다 —
            # v6 manifest는 ADR-51 D-2부터 쓰지 않는다.
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
        candidate_build_references: Mapping[RuntimeSlot, str],
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

    def _require_pinned_runtime_database_instances_ready(
        self,
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime],
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        """두 DB가 사는 PostgreSQL instance가 떠 있고 healthy인지 **보기만** 한다(ADR-53).

        instance는 DSN 포트에서 유도했다(`database_runtimes_from_frozen_contract`). 공용
        instance는 모든 tenant가 쓰므로 재구축이 `up`·재생성·재시작하지 않는다(R3) — `compose
        ps`로 running·healthy·컨테이너 이름만 확인한다. 이미지·설정은 그 instance의 소유자
        (Manager 설치·배포 창)가 맞춘다.
        """

        self._require_services_ready(
            tuple(dict.fromkeys(runtime.service_name for runtime in runtimes)),
            transaction=transaction,
            frozen_recovery=True,
        )

    def _converge_committed_runtime(
        self,
        *,
        runtime_transaction: ComposeTransactionSnapshot,
        topology: RuntimeTopology,
        companions: Mapping[str, RuntimeSlot],
        expected_images: Mapping[str, str],
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime],
        map_login: str,
    ) -> None:
        """committed와 같은 pair: 빌드·migration 없이 떠 있어야 할 것만 맞춘다.

        compose는 설정이 달라진 컨테이너만 다시 만든다. 이미지·설정이 같으면 무연산이다.
        Map DB 격리·연결 상한(R4)은 `up` 전에 다시 건다 — 같은 pair 수렴만으로 적용되고,
        이미 맞으면 멱등이다.
        """

        ensure_map_databases_isolated(runtimes[0], login=map_login)
        self._run_pinned_runtime_rebuild_compose(
            [
                "up",
                "-d",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                str(_COMPOSE_WAIT_TIMEOUT_SECONDS),
                *_with_generation_companions(RUNTIME_SLOTS, companions, topology),
            ],
            transaction=runtime_transaction,
        )
        self._converge_shared_dagster_plane(
            runtime_transaction=runtime_transaction,
            topology=topology,
        )
        self._verify_pinned_runtime_services(
            runtime_transaction=runtime_transaction,
            topology=topology,
            companions=companions,
            expected_images=expected_images,
        )

    def _converge_shared_dagster_plane(
        self,
        *,
        runtime_transaction: ComposeTransactionSnapshot,
        topology: RuntimeTopology,
    ) -> None:
        """pinned target이 공용 plane에 합류했으면 plane이 그 location을 싣게 한다(ADR-54 개정).

        - **plane 서비스는 frozen render에서** 모양으로 파생하고, 설치된 release의 것과 다르면 거부한다.
        - **실을 workspace의 location마다** 그 target의 plane 밖 webserver·daemon·gateway가 멈춰 있어야 한다(한 규칙,
          이름 없음 — Map·PinVi만이 아니라 설치됐지만 아직 펜스 전인 다른 target도). 하나라도 돌면 이중 발화라 거부한다.
        - **다시 만들 때만 `up`한다.** 떠 있는 daemon·webserver가 frozen render가 만들 컨테이너와 같으면(이미지 ID, render
          env 전부, command·entrypoint, 돌고 재시작 중 아님) `up`하지 않는다 — frozen render와 평범한 render의 config
          hash가 달라 무조건 `up`은 매 재구축 plane을 다시 만든다(`_shared_plane_current`).
        - **이 target의 location만 기다린다.** `up -d --no-deps`(`--wait` 없음) 뒤 webserver의 `workspaceOrError`에서
          합류한 carrier의 location이 `RepositoryLocation`이고 daemon 컨테이너가 도는지 본다. 다른 테넌트의
          code-server가 내려가 있어도 이 배포를 막지 않는다. 상한(300초) 안에 안 되면 거부한다(fail-closed).

        모두 `own`이면 아무것도 하지 않는다.
        """

        if not topology.shared_dagster_slots:
            return
        shared_targets = {
            family.target for family in topology.families.values() if family.shared
        }
        plane, services, locations = self._frozen_shared_plane(runtime_transaction)
        owners = installed_location_owners()
        wanted = tuple(
            sorted(
                location for location, family in owners.items() if family.target in shared_targets
            )
        )
        missing = [location for location in wanted if location not in locations]
        if not wanted or missing:
            raise DeploymentContractError(
                "the shared plane workspace does not list the shared pinned locations: "
                + ", ".join(missing or sorted(shared_targets))
            )
        self._require_plane_location_owners_fenced(locations, owners)
        if not self._shared_plane_current(plane, services):
            self._run_pinned_runtime_rebuild_compose(
                ["up", "-d", "--no-deps", *plane.services],
                transaction=runtime_transaction,
            )
        self._wait_shared_plane_locations(plane, services, wanted)

    def _frozen_shared_plane(
        self, runtime_transaction: ComposeTransactionSnapshot
    ) -> tuple[SharedDagsterPlane, Mapping[str, Any], tuple[str, ...]]:
        """frozen render의 plane(설치된 release와 같아야 한다), 그 서비스 정의, 붙인 workspace의 location."""

        resolved = runtime_transaction.resolved
        plane = derive_shared_dagster_plane(resolved)
        if plane != installed_shared_dagster_plane():
            raise DeploymentContractError(
                "the frozen render's shared Dagster plane differs from the installed release"
            )
        services = cast(Mapping[str, Any], resolved["services"])
        sources = {shared_workspace_source(services[name]) for name in plane.services}
        if len(sources) != 1:
            raise DeploymentContractError("shared plane services mount different workspaces")
        return plane, services, self._read_shared_workspace_locations(next(iter(sources)))

    @staticmethod
    def _read_shared_workspace_locations(source: str) -> tuple[str, ...]:
        path = Path(source)
        if not path.is_absolute():
            path = Path(get_project_root()) / path
        try:
            document = load_yaml_rejecting_duplicate_keys(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise DeploymentContractError("cannot read the shared plane workspace") from exc
        return workspace_location_names(document)

    def _require_plane_location_owners_fenced(
        self,
        locations: Sequence[str],
        owners: Mapping[str, DagsterFamily],
    ) -> None:
        """plane이 실을 location마다 그 target의 plane 밖 webserver·daemon·gateway가 돌지 않는다(이중 발화 방지)."""

        running: list[str] = []
        for location in locations:
            family = owners.get(location)
            if family is None:
                raise DeploymentContractError(
                    f"the shared plane workspace lists {location}, which no Dagster target serves"
                )
            for service in family.legacy:
                # 형제 프로젝트(transport)의 옛 서비스는 그 project의 이름 규칙으로 찾는다.
                container = family.container_name(service)
                if self._inspect_container_running(container, label=service):
                    running.append(f"{service} ({container}, location {location})")
        if running:
            raise DeploymentContractError(
                "the shared plane would load a location whose own Dagster services are still "
                "running (double fire); fence them first: " + ", ".join(running)
            )

    def _require_shared_plane_releases_own_targets(
        self,
        topology: RuntimeTopology,
        runtime_transaction: ComposeTransactionSnapshot,
    ) -> None:
        """`own`인 pinned target의 location을 공용 plane이 싣고 있지 않다(적대 리뷰 M2 — 되돌리기의 반대 방향).

        Manager만 되돌린 release(스위치 `own`)를 재구축하면 own daemon이 뜬다. 그때 plane이 아직 그 location을
        싣고 있으면(설치본 workspace가 적었거나, 떠 있는 webserver가 옛 workspace로 싣고 있으면) 같은 schedule을 둘이
        쏜다. 둘 다 아니어야 한다. 떠 있는 webserver에 물을 수 없는데 plane daemon이 돌면 판정할 수 없어 거부한다.
        되돌리기는 창 스크립트(`scripts/dagster-shared-cutover.sh <target> rollback`)가 plane에서 먼저 내린다.
        """

        # Map·PinVi family만 본다 — 다른 target의 모양이 어긋나도 이 판정은 막히지 않는다.
        owned = {
            installed_code_location(family)
            for family in topology.families.values()
            if not family.shared
        }
        if not owned:
            return
        plane, services, locations = self._frozen_shared_plane(runtime_transaction)
        listed = owned & set(locations)
        if listed:
            raise DeploymentContractError(
                "the installed shared plane workspace still lists an own target's location: "
                + ", ".join(sorted(listed))
            )
        host, port = listen_address(services[plane.webserver])
        loaded = self._query_shared_plane_locations(host, port)
        if loaded is None:
            if self._inspect_container_running(
                installed_container_name(plane.daemon), label=plane.daemon
            ):
                raise DeploymentContractError(
                    "the shared Dagster daemon is running but its webserver cannot be asked "
                    "which locations it loads; refusing to start an own Dagster daemon"
                )
            return
        still = owned & set(loaded)
        if still:
            raise DeploymentContractError(
                "the running shared plane still loads an own target's location (roll back "
                "through the cutover script first): " + ", ".join(sorted(still))
            )

    def _shared_plane_current(
        self, plane: SharedDagsterPlane, services: Mapping[str, Any]
    ) -> bool:
        """떠 있는 daemon·webserver가 frozen render가 만들 컨테이너와 같은가 — 그러면 `up`하지 않는다.

        config hash는 쓰지 않는다(frozen·평범한 render가 다르게 낸다). 대신 재생성을 부를 실행 형태를 직접 본다:
        이미지(render의 `image:` 참조가 가리키는 image ID와 컨테이너의 `.Image`), render의 env 전부(공용 URL 앵커,
        heartbeat tolerance, `*_DIGEST` — 컨테이너 env가 그 값들을 그대로 싣는다), command·entrypoint. 컨테이너가 돌고
        재시작 중이 아니어야 한다. 하나라도 다르면(이미지 참조를 풀 수 없는 것 포함) `up`한다. 컨테이너를 **읽을 수
        없으면** 거부한다(fail-closed, `_inspect_plane_container`).

        render는 compose의 `$$` escape를 그대로 싣는다(`docker compose config --format json`) — Docker는 컨테이너를
        만들 때 그것을 `$`로 푼다(storage 가드 argv의 `exec "$$@"` → Cmd의 `exec "$@"`, n150 실측). 그래서 render의
        command·entrypoint·env 값을 같은 규칙으로 푼 뒤 비교한다(재리뷰 HIGH — 안 풀면 매번 달라 M3가 되살아난다).
        """

        for name in plane.services:
            render = services[name]
            observed = self._inspect_plane_container(installed_container_name(name), label=name)
            if observed is None or not observed["running"] or observed["restarting"]:
                return False
            image = render.get("image")
            if not isinstance(image, str) or not image:
                return False
            try:
                wanted_image = self._inspect_image_reference_id(image, label=name)
            except DeploymentContractError:
                return False
            if observed["image_id"] != wanted_image:
                return False
            environment = render.get("environment") or {}
            if not isinstance(environment, Mapping):
                return False
            actual_env = observed["env"]
            for key, value in environment.items():
                if value is None:
                    continue
                if actual_env.get(str(key)) != _unescape_compose(str(value)):
                    return False
            command = [_unescape_compose(str(word)) for word in render.get("command") or []]
            if command != observed["cmd"]:
                return False
            entrypoint = render.get("entrypoint")
            if entrypoint is not None and [
                _unescape_compose(str(word)) for word in entrypoint
            ] != observed["entrypoint"]:
                return False
        return True

    @staticmethod
    def _inspect_plane_container(container_name: str, *, label: str) -> Mapping[str, Any] | None:
        """plane 컨테이너의 실행 형태(이미지 ID·env·command·entrypoint·상태). 없으면 ``None``, 읽을 수 없으면 거부.

        env에는 비밀(공용 metadata URL)이 있다 — 비교에만 쓰고 어디에도 싣지 않는다.
        """

        try:
            completed = subprocess.run(
                ["docker", "container", "inspect", "--format={{json .}}", container_name],
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(f"cannot inspect the {label} container") from exc
        if completed.returncode == 1 and "no such container" in completed.stderr.lower():
            return None
        if completed.returncode != 0:
            raise DeploymentContractError(f"cannot inspect the {label} container")
        try:
            document = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DeploymentContractError(f"cannot inspect the {label} container") from exc
        config = document.get("Config") if isinstance(document, Mapping) else None
        state = document.get("State") if isinstance(document, Mapping) else None
        if not isinstance(config, Mapping) or not isinstance(state, Mapping):
            raise DeploymentContractError(f"cannot inspect the {label} container")
        env: dict[str, str] = {}
        for line in config.get("Env") or []:
            key, separator, value = str(line).partition("=")
            if separator:
                env[key] = value
        entrypoint = config.get("Entrypoint")
        return {
            "image_id": str(document.get("Image") or ""),
            "env": env,
            "cmd": [str(word) for word in config.get("Cmd") or []],
            "entrypoint": None if entrypoint is None else [str(word) for word in entrypoint],
            "running": state.get("Running") is True,
            "restarting": state.get("Restarting") is True,
        }

    @staticmethod
    def _query_shared_plane_locations(host: str, port: int) -> Mapping[str, str | None] | None:
        """공용 webserver의 location → 로드 상태(`__typename`). 물을 수 없으면 ``None``."""

        request = urllib.request.Request(
            f"http://{host}:{port}/graphql",
            data=json.dumps({"query": _SHARED_PLANE_WORKSPACE_QUERY}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - loopback
                payload = json.loads(response.read(1_048_577))
        except (OSError, ValueError):
            return None
        data = payload.get("data") if isinstance(payload, Mapping) else None
        workspace = data.get("workspaceOrError") if isinstance(data, Mapping) else None
        entries = workspace.get("locationEntries") if isinstance(workspace, Mapping) else None
        if not isinstance(entries, list):
            return None
        states: dict[str, str | None] = {}
        for entry in entries:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str):
                return None
            load = entry.get("locationOrLoadError")
            states[entry["name"]] = load.get("__typename") if isinstance(load, Mapping) else None
        return states

    def _wait_shared_plane_locations(
        self,
        plane: SharedDagsterPlane,
        services: Mapping[str, Any],
        wanted: Sequence[str],
    ) -> None:
        host, port = listen_address(services[plane.webserver])
        daemon_container = installed_container_name(plane.daemon)
        deadline = time.monotonic() + _SHARED_PLANE_PROBE_TIMEOUT_SECONDS
        while True:
            loaded = self._query_shared_plane_locations(host, port)
            # 도는가·재시작 중이 아닌가만 본다(L3). daemon의 health는 workspace의 code-server **전부**가 SERVING인지 보므로
            # 다른 테넌트에 묶인다(H1) — 이 target의 location 적재는 위 webserver 물음이 본다.
            observed = self._inspect_plane_container(daemon_container, label=plane.daemon)
            daemon_running = (
                observed is not None and observed["running"] and not observed["restarting"]
            )
            if (
                daemon_running
                and loaded is not None
                and all(loaded.get(location) == "RepositoryLocation" for location in wanted)
            ):
                return
            if time.monotonic() >= deadline:
                state = {location: (loaded or {}).get(location) for location in wanted}
                raise DeploymentContractError(
                    "the shared Dagster plane did not load the shared pinned locations within "
                    f"{int(_SHARED_PLANE_PROBE_TIMEOUT_SECONDS)} s: {state}, daemon running "
                    f"{daemon_running}"
                )
            time.sleep(_SHARED_PLANE_PROBE_INTERVAL_SECONDS)

    def _verify_pinned_runtime_services(
        self,
        *,
        runtime_transaction: ComposeTransactionSnapshot,
        topology: RuntimeTopology,
        companions: Mapping[str, RuntimeSlot],
        expected_images: Mapping[str, str],
    ) -> None:
        """전 서비스 readiness·이미지·C6c secret isolation을 확인한다."""

        runtime_records = self._require_services_ready(
            (*topology.runtime_services, *companions),
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
                [*topology.runtime_services, *companions],
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
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime],
        runtime_transaction: ComposeTransactionSnapshot,
        topology: RuntimeTopology,
        companions: Mapping[str, RuntimeSlot],
        expected_images: Mapping[str, str],
        state_paths: PinnedRuntimeStatePaths,
        values: Mapping[str, str],
        map_login: str,
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
            ["stop", *topology.runtime_services, *companions],
            transaction=runtime_transaction,
        )
        self._retire_pinned_runtime_oneshot_writers(transaction=runtime_transaction)
        reconcile_orphaned_pinvi_bootstrap_credentials(
            state_paths=state_paths,
            values=values,
            global_mutation_lock_held=True,
            all_one_shot_containers_absent=True,
        )

        # PostgreSQL instance는 판정 전에 readiness만 봤다. 재구축은 그것을 띄우거나 다시 만들지
        # 않는다(R3) — 판정과 migration 사이에 cluster가 바뀔 자리를 만들지 않는다.
        if restart:
            reset_databases_for_application_300(runtimes)
            # 지운 **뒤에** 기준선을 비우고 리셋을 표시한다. 리셋 전에 죽으면 DB는 그대로이므로
            # 다음 일반 실행이 여전히 옛 기준으로 확인해야 하고, 리셋 기록도 가져가지 않는다.
            status = replace(status, databases=None, step=RESET_DONE_STEP)
            write_deploy_status(status_path, status)
        # PinVi DB가 없으면(새 호스트·지워진 DB) Map을 건드리기 전에 만든다.
        create_database_if_absent(runtimes[1])

        # Map application DB: 없으면 만들고 role bootstrap, 이미 bootstrap됐으면 그대로.
        # instance admin 이름·포트는 비밀이 아니다 — 앱 DB runtime에서 유도해 실행 시점 `-e`로
        # 준다(compose가 공용 instance의 보간식을 두 번 적지 않게). admin password는 one-shot이
        # 그 instance의 secret file에서 스스로 읽는다(ADR-53 S1) — Manager argv에도, 어느 Map
        # 런타임에도 들어가지 않는다.
        ensure_map_application_database(
            runtimes[0],
            run_role_bootstrap=lambda: self._run_pinned_runtime_rebuild_compose(
                [
                    "--profile",
                    "bootstrap",
                    "run",
                    "--rm",
                    "--no-deps",
                    "-e",
                    f"{MAP_BOOTSTRAP_ADMIN_USER_ENV}={runtimes[0].admin_name}",
                    "-e",
                    f"{MAP_BOOTSTRAP_PORT_ENV}={runtimes[0].port}",
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

        # Map app DB를 PUBLIC에 닫고 login CONNECT·연결 상한을 건다(R4). fresh bootstrap은 기본
        # ACL을 요구하므로 bootstrap 뒤, Map이 처음 연결하기 전이다.
        #
        # 옛 Map Dagster metadata DB는 만들지도(init), migrate하지도(`kor-travel-map-dagster-storage-migrate`),
        # head를 읽지도 않는다 — Map Dagster storage는 공용 `dagster_shared`이고 그 schema는
        # `kor-travel-dagster-storage-migrate`가 올린다. 옛 DB가 막힌 뒤 이 자리의 migrate가 Map·PinVi를 멈춘
        # 채 실패했다(2026-10-03 22:51Z, platform-topology.md §7 4단계).
        ensure_map_databases_isolated(runtimes[0], login=map_login)
        compose_up("kor-travel-map-api")
        require_head(
            runtimes[0],
            candidate.map_application_head,
            "Map application schema differs from candidate head",
        )
        # Map UI와 Map Dagster slot. 공용 plane에 합류했으면 carrier가 code-server이고 daemon slot은
        # 비어 있다 — 옛 webserver·daemon은 이름으로도 부르지 않는다(ADR-54).
        compose_up(
            *_with_generation_companions(
                ("map_ui", "map_dagster", "map_dagster_daemon"),
                companions,
                topology,
            )
        )

        # PinVi: 0101 fresh-install fence는 빈 DB에서만 필요하다(기존 DB에서는 0101이
        # 다시 돌지 않는다). bootstrap은 `alembic upgrade head` 뒤 admin을 만들거나
        # 고친다 — 둘 다 멱등이다.
        if not schema_revision_table_exists(runtimes[1]):
            self._ensure_pinvi_fresh_migration_fence(values=values)
        self._run_pinvi_admin_bootstrap(
            transaction=runtime_transaction,
            state_paths=state_paths,
            values=values,
            transaction_id=status.run_id,
        )
        require_head(runtimes[1], candidate.pinvi_head, "PinVi schema differs from candidate head")
        compose_up("pinvi-api")
        self._require_services_ready(
            ("pinvi-api",),
            transaction=runtime_transaction,
            frozen_recovery=True,
        )
        # 공용 plane에 합류한 target이 있으면 smoke 전에 그 code-server와 plane을 맞춘다 — PinVi admin
        # (`/admin/etl/summary`)과 Map ops(`/v1/ops/pipeline/*`, PinVi provider-sync가 부른다)는 공용
        # webserver에 자기 location을 묻는다. 모두 `own`이면 아무것도 하지 않는다(호출 순서가 파생 이전과 같다).
        shared_slots = topology.shared_dagster_slots
        if shared_slots:
            compose_up(*_with_generation_companions(shared_slots, companions, topology))
            self._converge_shared_dagster_plane(
                runtime_transaction=runtime_transaction,
                topology=topology,
            )
        run_pinvi_canonical_smoke(
            load_c6c_deployment_config_from_environment(values),
            cancel_probe_state=PinviCancelProbeState(transaction_id=status.run_id),
        )
        compose_up(
            *_with_generation_companions(("pinvi_web", "pinvi_dagster"), companions, topology)
        )
        self._verify_pinned_runtime_services(
            runtime_transaction=runtime_transaction,
            topology=topology,
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
            # 고르라"며 거부했다. 그 조언은 의존 폐포가 **항상** 두 프로젝트에 걸치는
            # 외부 target에 대해 **따를 수 없었다** — 그 target 이름이 자기 서비스를
            # 가리키는 유일한 이름이기 때문이다(적대 리뷰 2026-09-18 F3, 당시 실례는
            # `airport` → `airport-db`였다).
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
            # 번역하고 Manager 컨테이너는 id를 그대로 넘겼다 — `kor-travel-shared-postgresql`
            # (서비스는 `kor-travel-shared-postgres`)처럼 둘이 다른 이름에서 `no such
            # service`다(적대 리뷰 2026-09-18 B-F12, 선재 결함). 번역은 소속과 무관하므로
            # 양쪽에 똑같이 한다.
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
