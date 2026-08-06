import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import (
    _MANAGED_COMPOSE_MUTATION_CAPABILITY,
    _MAP_RUNTIME_SERVICES,
    _PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
    _PINVI_API_SERVICE,
    C6cBuildProvenance,
    C6cDeploymentConfig,
    CandidateSystemBindSnapshot,
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
    _assert_candidate_single_file_boundary,
    _expand_env_path,
    assert_compose_mutation_allowed,
    assert_manager_mutation_allowed,
    c6c_deployment_lock,
    c6c_global_mutation_lock_path,
    c6c_state_paths,
    compose_volume_graph_hash,
    inspect_c6c_image_source_revision,
    revalidate_candidate_system_bind_snapshots,
    validate_c6c_build_source_wiring,
    validate_c6c_operation_tokens,
    validate_compose_candidate_protected_values,
    validate_resolved_c6c_build_provenance,
    validate_resolved_compose_candidate_protected_values,
)
from kor_travel_docker_manager.services.c6c_image_retention import (
    ensure_generation_references,
    reconcile_generation_references,
)
from kor_travel_docker_manager.services.database_runtime import (
    database_runtimes_from_frozen_contract,
    read_database_schema_revision,
    recreate_empty_databases,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    REBUILD_PHASES,
    RUNTIME_SERVICES,
    PinnedRuntimeManifest,
    PinnedRuntimeRebuildJournal,
    RebuildPhase,
    RuntimeService,
    ensure_pinned_runtime_state_directory,
    generation_logical_sha256,
    pinned_runtime_state_paths,
    retire_f1d_legacy_artifacts,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    read_manifest as read_pinned_runtime_manifest,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    read_rebuild_journal as read_pinned_runtime_rebuild_journal,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    write_manifest as write_pinned_runtime_manifest,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    write_rebuild_journal as write_pinned_runtime_rebuild_journal,
)
from kor_travel_docker_manager.services.pinned_runtime_rebuild import (
    CandidateRuntimeBuild,
    build_candidate_generation,
    generation_compose_environment,
    new_candidate_journal,
    parse_candidate_static_head,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    current_pinned_runtime_release,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    materialize_pinned_runtime_sources,
)
from kor_travel_docker_manager.services.pinvi_bootstrap_credential import (
    pinvi_bootstrap_credential_file,
    retire_stale_pinvi_bootstrap_credential,
)
from kor_travel_docker_manager.services.registry import (
    init_steps_for_target,
    is_known_target,
    runtime_services_for_target,
    services_for_target,
    target_sequence_for_target,
)

_PINNED_RUNTIME_ONESHOT_WRITERS = (
    "kor-travel-map-dagster-storage-migrate",
    "pinvi-admin-bootstrap",
)
# frozen transaction은 실행 전에 one-shot service까지 exact resolved document에 결박한다.
# profile을 해석 단계에서 빼면 `run --profile bootstrap`가 같은 문서에서 service를 찾지 못한다.
_FROZEN_COMPOSE_PROFILES = ("bootstrap",)
_CANDIDATE_MAP_APPLICATION_HEAD_PLACEHOLDER = "candidate_static_attestation"
_MAP_DAGSTER_STORAGE_MIGRATION_ERROR_SCHEMA = (
    "kor-travel-map.dagster-storage-migration-error.v1"
)
_MAP_DAGSTER_STORAGE_MIGRATION_ERROR_CODES = frozenset(
    {
        "dagster_storage_head_ambiguous",
        "dagster_storage_head_unavailable",
        "dagster_instance_migrate_failed",
        "dagster_instance_migrate_unavailable",
        "dagster_version_mismatch",
        "dagster_version_row_count_invalid",
        "dagster_version_table_unavailable",
        "invalid_arguments",
        "invalid_dagster_home",
        "invalid_dagster_yaml",
        "missing_dagster_home",
        "missing_dagster_pg_url",
        "missing_dagster_yaml",
    }
)
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
# fresh Dagster DB의 PostgreSQL readiness window를 덮되 총 retry 대기는 58초를 넘지 않는다.
_PINNED_RUNTIME_DAGSTER_MIGRATION_ATTEMPTS = 30
_PINNED_RUNTIME_DAGSTER_MIGRATION_RETRY_SECONDS = 2


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


def _require_pinned_runtime_rebuild_root() -> None:
    """source staging·state owner와 Docker mutation authority를 root로 고정한다."""

    if os.geteuid() != 0:
        raise DeploymentContractError("pinned runtime rebuild requires root execution")


def get_project_root() -> str:
    configured = os.environ.get("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", "").strip()
    if configured:
        return os.path.abspath(configured)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(current_dir, "../../../../"))


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


def _clean_repository_revision(
    configured_path: str,
    *,
    compose_directory: Path,
    label: str,
) -> str:
    repository = _resolve_repository_path(
        configured_path,
        compose_directory=compose_directory,
        label=label,
    )

    root = _run_git_read(repository, ["rev-parse", "--show-toplevel"], label=label)
    try:
        git_root = Path(root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DeploymentContractError(f"{label} Git root cannot be resolved") from exc
    if git_root != repository:
        raise DeploymentContractError(
            f"{label} build context must be the exact Git worktree root"
        )
    status = _run_git_read(
        repository,
        ["status", "--porcelain=v1", "--untracked-files=normal"],
        label=label,
        allow_output_whitespace=True,
    )
    if status:
        raise DeploymentContractError(f"{label} build context worktree is not clean")
    revision = _run_git_read(
        repository,
        ["rev-parse", "--verify", "HEAD"],
        label=label,
    )
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise DeploymentContractError(
            f"{label} build context HEAD is not an exact lowercase commit"
        )
    return revision


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


@contextmanager
def _c6c_source_snapshot_environment(
    environment: Mapping[str, str],
    *,
    compose_path: str,
    provenance: C6cBuildProvenance,
) -> Iterator[dict[str, str]]:
    """live 파일 대신 두 exact Git tree를 일회성 build context로 제공한다."""

    compose_directory = Path(compose_path).resolve().parent
    repositories = {
        "KOR_TRAVEL_MAP_REPO_DIR": (
            _resolve_repository_path(
                environment.get("KOR_TRAVEL_MAP_REPO_DIR", "../kor-travel-map"),
                compose_directory=compose_directory,
                label="Map",
            ),
            provenance.map_source_revision,
            "Map",
        ),
        "PINVI_REPO_DIR": (
            _resolve_repository_path(
                environment.get("PINVI_REPO_DIR", "../pinvi"),
                compose_directory=compose_directory,
                label="PinVi",
            ),
            provenance.pinvi_source_revision,
            "PinVi",
        ),
    }
    with tempfile.TemporaryDirectory(prefix="ktdm-c6c-source-") as temporary:
        snapshot_root = Path(temporary)
        build_environment = provenance.compose_environment()
        for env_name, (repository, revision, label) in repositories.items():
            target = snapshot_root / env_name.lower()
            target.mkdir(mode=0o700)
            _export_git_tree(repository, revision, target, label=label)
            build_environment[env_name] = str(target)
        yield build_environment


def _export_git_tree(
    repository: Path,
    revision: str,
    target: Path,
    *,
    label: str,
) -> None:
    tree = _run_git_read(
        repository,
        ["ls-tree", "-r", "--full-tree", revision],
        label=label,
        allow_output_whitespace=True,
    )
    if re.search(r"(?m)^160000 ", tree) is not None:
        raise DeploymentContractError(
            f"{label} build context Git submodules are not supported"
        )
    archive_path = target.parent / f"{target.name}.tar"
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "archive",
                "--format=tar",
                f"--output={archive_path}",
                revision,
            ],
            cwd=get_project_root(),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise DeploymentContractError(
            f"cannot snapshot {label} build context Git tree"
        ) from exc
    if completed.returncode != 0:
        raise DeploymentContractError(
            f"cannot snapshot {label} build context Git tree"
        )
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            for member in archive.getmembers():
                parts = Path(member.name).parts
                if (
                    not parts
                    or Path(member.name).is_absolute()
                    or ".." in parts
                    or not (member.isfile() or member.isdir())
                ):
                    raise DeploymentContractError(
                        f"{label} Git tree has an unsafe build context entry"
                    )
            archive.extractall(target)
    except (OSError, tarfile.TarError) as exc:
        raise DeploymentContractError(
            f"cannot extract {label} build context Git tree"
        ) from exc
    finally:
        archive_path.unlink(missing_ok=True)


def _run_git_read(
    repository: Path,
    args: Sequence[str],
    *,
    label: str,
    allow_output_whitespace: bool = False,
) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *args],
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
            ["git", "-C", str(repository), *args],
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
}
_MAP_SOURCE_V4_CURSOR_ENV_VALUE = (
    "${KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET:?"
    "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET is required}"
)
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
    "dagster": [{"path": ".env", "required": False, "format": "raw"}],
    "dagster-daemon": [
        {"path": ".env", "required": False, "format": "raw"}
    ],
}
_MAP_SOURCE_TRACKED_ENV_FILE_MAX_BYTES = 64 * 1024


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_yaml_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_yaml_mapping,
)


def _load_unique_map_source_yaml(source: str) -> Any:
    loader = _UniqueKeySafeLoader(source)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()  # type: ignore[no-untyped-call]


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
        if not matching_names:
            continue
        if path[-1:] == ("<key>",):
            value_path = path[:-1]
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


def get_compatible_pair_manifest_path(
    environment: Mapping[str, str] | None = None,
) -> str:
    """Manifest 경로는 lock 안에서 전달된 frozen environment로만 해석한다."""

    if environment is None:
        raise DeploymentContractError(
            "compatible-pair manifest path requires a frozen environment snapshot"
        )
    return c6c_state_paths(environment)[0]


def get_c6c_deployment_lock_path() -> str:
    return _capture_c6c_deployment_lock_snapshot().lock_path


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
        return snapshot.effective
    merged = dict(snapshot.effective)
    merged.update(environment_override)
    return MappingProxyType(merged)


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
) -> ComposeEnvironmentSnapshot:
    env_path = Path(get_env_path()).resolve(strict=False)
    compose_path = Path(get_compose_path()).resolve(strict=False)
    override_path = Path(get_override_path()).resolve(strict=False)
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
                        stream=StringIO(decoded)
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
    values.update(dict(os.environ))
    if environment_override is not None:
        values.update(environment_override)
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
    """`deploy`/`capture`가 공유하는 `wait_timeout` 검증. lock 진입 전에 호출해야 한다."""
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
_ALEMBIC_HEAD_INSPECTION_TIMEOUT_SECONDS = 60
_PINNED_RUNTIME_STATIC_INSPECTION_TIMEOUT_SECONDS = 60


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
) -> str:
    """candidate artifact를 network 없이 검사하고 raw output은 호출자만 파싱한다."""

    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise DeploymentContractError(f"{label} candidate image ID is invalid")
    if not command or any(not argument or "\x00" in argument for argument in command):
        raise DeploymentContractError(f"{label} candidate static command is invalid")
    try:
        completed = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", image_id, *command],
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


class ComposeService:
    def _capture_transaction_unlocked(
        self,
        *,
        environment_override: Mapping[str, str] | None = None,
        derive_manifest_path: bool = False,
        environment_snapshot: ComposeEnvironmentSnapshot | None = None,
    ) -> tuple[ComposeTransactionSnapshot, ValidatedComposeCandidate]:
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
    ) -> list[str]:
        command = ["docker", "compose"]
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
        _frozen_recovery_capability: object | None = None,
    ) -> dict[str, Any]:
        if (
            _frozen_recovery_capability is not None
            and _frozen_recovery_capability is not _TRUSTED_FROZEN_RECOVERY_CAPABILITY
        ):
            raise ComposeCandidateContractError("untrusted frozen recovery capability")
        frozen_recovery = _frozen_recovery_capability is _TRUSTED_FROZEN_RECOVERY_CAPABILITY
        mutation_identifiers = self._compose_mutation_identifiers(args)
        if (
            mutation_identifiers
            or transaction is not None
            or expected_environment_snapshot is not None
        ):
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
                    transaction, captured_validation = self._capture_transaction_unlocked(
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
            transaction, persisted = self._capture_transaction_unlocked(
                environment_override=environment_override,
                environment_snapshot=environment_snapshot,
            )
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            return self._capture_candidate_transaction_unlocked(
                candidate,
                baseline_transaction=transaction,
                baseline_validation=persisted,
                environment_override=environment_override,
            )

    def _capture_candidate_transaction_unlocked(
        self,
        candidate: Mapping[str, Any],
        *,
        baseline_transaction: ComposeTransactionSnapshot,
        baseline_validation: ValidatedComposeCandidate,
        environment_override: Mapping[str, str] | None = None,
    ) -> ValidatedComposeCandidate:
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
        ).encode("utf-8")
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
    ) -> dict[str, Any]:
        command = self.build_command(
            args,
            canonical_single_file=materialized_compose is not None,
            compose_path=(
                environment_snapshot.compose_path
                if environment_snapshot is not None
                else None
            ),
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
            process_input = json.dumps(
                materialized_compose,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        try:
            completed = subprocess.run(
                command,
                cwd=get_project_root(),
                text=True,
                capture_output=capture_output,
                check=False,
                env=process_environment,
                input=process_input,
            )
        except OSError:
            return {
                "success": False,
                "returncode": 127,
                "command": command,
                "stdout": "",
                "stderr": "docker compose command could not start",
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
            transaction, validation = self._capture_transaction_unlocked()
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
        retryable: bool = False,
    ) -> dict[str, Any]:
        if retryable and tuple(args) != (
            "run",
            "--rm",
            "--no-deps",
            "kor-travel-map-dagster-storage-migrate",
        ):
            raise DeploymentContractError(
                "only the idempotent Dagster storage migration may retry"
            )
        attempts = _PINNED_RUNTIME_DAGSTER_MIGRATION_ATTEMPTS if retryable else 1
        result: dict[str, Any] = {}
        for attempt in range(attempts):
            result = self._run_frozen_recovery(
                args,
                transaction=transaction,
                mutation_capability=_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
            )
            if result["success"]:
                return result
            if attempt + 1 < attempts:
                time.sleep(_PINNED_RUNTIME_DAGSTER_MIGRATION_RETRY_SECONDS)
        compose_action = self._pinned_runtime_compose_action(args)
        diagnostic = self._pinned_runtime_compose_failure_diagnostic(
            args,
            result,
        )
        raise DeploymentContractError(
            "pinned runtime rebuild Compose "
            f"{compose_action} command failed (exit {result['returncode']}{diagnostic})"
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
    ) -> str:
        """허용된 one-shot typed error만 원문 없이 F1D 오류에 붙인다."""

        compose_action = ComposeService._pinned_runtime_compose_action(args)
        if compose_action != "run":
            return ""
        target = args[-1] if args else ""
        for stream_name in ("stderr", "stdout"):
            output = result.get(stream_name)
            if not isinstance(output, str):
                continue
            for line in output.splitlines():
                try:
                    payload = json.loads(
                        line,
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
                        and code in _MAP_DAGSTER_STORAGE_MIGRATION_ERROR_CODES
                    ):
                        return f"; {code}"
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
                        return f"; pinvi:{code}"
        return ""

    def _retire_pinned_runtime_oneshot_writers(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        """reset 전 frozen project one-shot writer를 제거하고 부재를 증명한다.

        `docker compose run --rm`의 Manager process가 강제 종료되면 Docker
        container가 계속 DB에 연결할 수 있다. 동일 frozen project/service label로만
        stop+remove한 뒤 `ps --all`에서 exact two service가 사라진 것을 확인한다.
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

    @staticmethod
    def _assert_pinned_runtime_database_heads(
        runtimes: Sequence[Any],
        *,
        journal: PinnedRuntimeRebuildJournal,
    ) -> None:
        if len(runtimes) != 3:
            raise DeploymentContractError("pinned runtime database roles are incomplete")
        expected = (
            journal.candidate.map_application_head,
            journal.candidate.map_dagster_head,
            journal.candidate.pinvi_head,
        )
        if tuple(read_database_schema_revision(runtime) for runtime in runtimes) != expected:
            raise DeploymentContractError(
                "pinned runtime database schema differs from committed generation"
            )

    def _attest_pinned_runtime_candidate_images(
        self,
        *,
        build: CandidateRuntimeBuild,
    ) -> dict[RuntimeService, str]:
        image_ids = {
            service: self._inspect_image_reference_id(
                build.image_names[service],
                label=service,
            )
            for service in RUNTIME_SERVICES
        }
        map_revision = build.sources.release.source_for("map").revision
        pinvi_revision = build.sources.release.source_for("pinvi").revision
        for service in RUNTIME_SERVICES:
            expected_revision = map_revision if service.startswith("kor-travel-map-") else pinvi_revision
            observed_revision = self._inspect_image_source_revision(
                image_ids[service],
                label=service,
                expected_build_environment=("production" if service.startswith("pinvi-") else None),
            )
            if observed_revision != expected_revision:
                raise DeploymentContractError(
                    f"{service} candidate image revision differs from the release pin"
                )
        return image_ids

    @staticmethod
    def _validate_pinned_runtime_candidate_build_contract(
        transaction: ComposeTransactionSnapshot,
        *,
        build: CandidateRuntimeBuild,
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
                "kor-travel-map-api": map_context,
                "kor-travel-map-ui": map_context,
                "kor-travel-map-dagster": map_context,
                "kor-travel-map-dagster-daemon": map_context,
                "pinvi-api": pinvi_context,
                "pinvi-web": pinvi_context,
                "pinvi-dagster": pinvi_context,
            },
        )

    @staticmethod
    def _assert_pinned_runtime_journal_matches_candidate_input(
        journal: PinnedRuntimeRebuildJournal,
        *,
        release_pinset_sha256: str,
        map_revision: str,
        pinvi_revision: str,
        environment_bytes: bytes,
        compose_source_bytes: bytes,
        resolved_compose_sha256: str,
    ) -> None:
        if (
            journal.candidate.pinset_sha256 != release_pinset_sha256
            or journal.candidate.map_source_revision != map_revision
            or journal.candidate.pinvi_source_revision != pinvi_revision
            or journal.environment_sha256 != hashlib.sha256(environment_bytes).hexdigest()
            or journal.compose_sha256 != hashlib.sha256(compose_source_bytes).hexdigest()
            or journal.resolved_compose_sha256 != resolved_compose_sha256
        ):
            raise DeploymentContractError(
                "pinned runtime rebuild journal differs from frozen candidate input"
            )

    @staticmethod
    def _pinned_runtime_result(
        journal: PinnedRuntimeRebuildJournal,
        *,
        resumed: bool,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "returncode": 0,
            "resumed": resumed,
            "transaction_id": journal.transaction_id,
            "phase": journal.phase,
            "generation_sha256": generation_logical_sha256(journal.candidate),
            "pinset_sha256": journal.candidate.pinset_sha256,
            "schema_heads": dict(journal.candidate.schema_heads),
        }

    @staticmethod
    def _advance_pinned_runtime_journal(
        journal: PinnedRuntimeRebuildJournal,
        phase: RebuildPhase,
    ) -> PinnedRuntimeRebuildJournal:
        """resume의 high-watermark는 보존하고 아직 도달하지 않은 phase만 기록한다."""

        if journal.phase == phase:
            return journal
        # `transition` 자체가 enum order를 검증한다. 이미 더 먼 checkpoint면 reset
        # 뒤 side effect를 다시 실행하더라도 high-watermark는 되돌리지 않는다.
        if REBUILD_PHASES.index(journal.phase) > REBUILD_PHASES.index(phase):
            return journal
        if REBUILD_PHASES.index(journal.phase) + 1 != REBUILD_PHASES.index(phase):
            raise DeploymentContractError("pinned runtime rebuild phase is inconsistent")
        return journal.transition(phase)

    def rebuild_pinned_runtime(self) -> dict[str, Any]:
        """F1D v5의 candidate-first seven-service destructive rebootstrap을 실행한다."""

        _require_pinned_runtime_rebuild_root()
        with c6c_deployment_lock_from_environment() as lock_snapshot:
            # 새 Map application head는 candidate image가 static command로 직접
            # attest한 뒤에야 알 수 있다. 따라서 아직 실행하지 않는 candidate
            # build/inspection Compose에는 schema-shaped placeholder만 주고, 실제
            # runtime transaction에는 journal의 exact head만 넣는다.
            environment_snapshot = _capture_compose_environment_snapshot(
                environment_override=None
            )
            validate_c6c_operation_tokens(
                environment_snapshot.effective,
                require_nonempty=True,
            )
            state_paths = pinned_runtime_state_paths(environment_snapshot.effective)
            ensure_pinned_runtime_state_directory(state_paths.state_root)
            release = current_pinned_runtime_release()
            sources = materialize_pinned_runtime_sources(
                release=release,
                state_paths=state_paths,
                values=environment_snapshot.effective,
            )
            build = CandidateRuntimeBuild(sources)
            # 기존 v5 non-terminal journal은 당시 canonical env에 있던 runtime
            # head를 포함한 resolved Compose digest를 보존한다. 값이 있으면 이를
            # candidate에도 byte-for-byte 유지해 same-pin resume을 막지 않는다.
            # 새 env에서만 candidate-only placeholder를 쓴다.
            candidate_map_application_head = environment_snapshot.effective.get(
                "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD", ""
            ) or _CANDIDATE_MAP_APPLICATION_HEAD_PLACEHOLDER
            candidate_environment = {
                **build.compose_environment(),
                "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": (
                    candidate_map_application_head
                ),
            }
            candidate_transaction, _ = self._capture_transaction_unlocked(
                environment_override=candidate_environment,
                environment_snapshot=environment_snapshot,
            )
            _assert_transaction_matches_c6c_lock(candidate_transaction, lock_snapshot)
            self._validate_pinned_runtime_candidate_build_contract(
                candidate_transaction,
                build=build,
            )
            try:
                state_paths.journal.lstat()
                journal_exists = True
            except FileNotFoundError:
                journal_exists = False

            if journal_exists:
                journal = read_pinned_runtime_rebuild_journal(state_paths.journal)
                self._assert_pinned_runtime_journal_matches_candidate_input(
                    journal,
                    release_pinset_sha256=release.pinset_sha256,
                    map_revision=release.source_for("map").revision,
                    pinvi_revision=release.source_for("pinvi").revision,
                    environment_bytes=environment_snapshot.env_file_bytes,
                    compose_source_bytes=candidate_transaction.compose_source_bytes,
                    resolved_compose_sha256=candidate_transaction.resolved_document_hash,
                )
                if journal.phase == "committed":
                    manifest = read_pinned_runtime_manifest(state_paths.manifest)
                    if manifest.active_generation != journal.candidate:
                        raise DeploymentContractError(
                            "pinned runtime manifest differs from committed journal"
                        )
                else:
                    self._attest_pinned_runtime_candidate_images(build=build)
                ensure_generation_references((journal.candidate,), cwd=get_project_root())
            else:
                self._run_pinned_runtime_rebuild_compose(
                    ["build", *RUNTIME_SERVICES],
                    transaction=candidate_transaction,
                )
                image_ids = self._attest_pinned_runtime_candidate_images(build=build)
                map_application_output = _run_pinned_runtime_static_command(
                    image_ids["kor-travel-map-api"],
                    ("ktm-application-schema", "head"),
                    label="Map application",
                )
                map_application_head = parse_candidate_static_head(
                    map_application_output,
                    schema="kor-travel-map.application-head.v1",
                    field="head",
                )
                map_dagster_output = _run_pinned_runtime_static_command(
                    image_ids["kor-travel-map-dagster"],
                    ("ktm-dagster-storage", "head"),
                    label="Map Dagster",
                )
                map_dagster_head = parse_candidate_static_head(
                    map_dagster_output,
                    schema="kor-travel-map.dagster-storage-head.v1",
                    field="head",
                )
                pinvi_output = _run_pinned_runtime_static_command(
                    image_ids["pinvi-api"],
                    ("pinvi-admin-bootstrap", "head"),
                    label="PinVi",
                )
                pinvi_head = parse_candidate_static_head(
                    pinvi_output,
                    schema="pinvi.candidate-head.v1",
                    field="pinvi_head",
                )
                candidate = build_candidate_generation(
                    sources=sources,
                    image_ids=image_ids,
                    map_application_head=map_application_head,
                    map_dagster_head=map_dagster_head,
                    pinvi_head=pinvi_head,
                )
                journal = new_candidate_journal(
                    candidate=candidate,
                    environment_bytes=environment_snapshot.env_file_bytes,
                    compose_source_bytes=candidate_transaction.compose_source_bytes,
                    resolved_compose_sha256=candidate_transaction.resolved_document_hash,
                )
                write_pinned_runtime_rebuild_journal(state_paths.journal, journal)
                ensure_generation_references((candidate,), cwd=get_project_root())
                retire_f1d_legacy_artifacts(
                    state_root=state_paths.state_root,
                    transaction_id=journal.transaction_id,
                    candidate=candidate,
                    recorded_at=journal.created_at,
                )

            runtime_environment = {
                **build.compose_environment(),
                **generation_compose_environment(journal.candidate),
                "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": (
                    journal.candidate.map_application_head
                ),
            }
            runtime_transaction, _ = self._capture_transaction_unlocked(
                environment_override=runtime_environment,
                environment_snapshot=environment_snapshot,
            )
            runtimes = database_runtimes_from_frozen_contract(
                resolved=runtime_transaction.resolved,
                environment=runtime_transaction.environment.effective,
            )
            resumed = journal_exists
            if journal.phase == "committed":
                self._require_services_ready(
                    RUNTIME_SERVICES,
                    transaction=runtime_transaction,
                    frozen_recovery=True,
                )
                self._assert_pinned_runtime_database_heads(runtimes, journal=journal)
                reconcile_generation_references(
                    (journal.candidate,),
                    cwd=get_project_root(),
                )
                return self._pinned_runtime_result(journal, resumed=True)
            try:
                updated = self._advance_pinned_runtime_journal(
                    journal, "reset_intent_durable"
                )
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                self._run_pinned_runtime_rebuild_compose(
                    ["stop", *RUNTIME_SERVICES],
                    transaction=runtime_transaction,
                )
                self._retire_pinned_runtime_oneshot_writers(
                    transaction=runtime_transaction,
                )
                retire_stale_pinvi_bootstrap_credential(
                    state_paths=state_paths,
                    values=environment_snapshot.effective,
                    transaction_id=journal.transaction_id,
                )
                recreate_empty_databases(runtimes)
                updated = self._advance_pinned_runtime_journal(
                    journal, "databases_recreated"
                )
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                self._run_pinned_runtime_rebuild_compose(
                    ["up", "-d", "--wait", "--wait-timeout", "300", "kor-travel-map-api"],
                    transaction=runtime_transaction,
                )
                if read_database_schema_revision(runtimes[0]) != journal.candidate.map_application_head:
                    raise DeploymentContractError("Map application schema differs from candidate head")
                updated = self._advance_pinned_runtime_journal(
                    journal, "map_application_ready"
                )
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                self._run_pinned_runtime_rebuild_compose(
                    ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
                    transaction=runtime_transaction,
                    retryable=True,
                )
                if read_database_schema_revision(runtimes[1]) != journal.candidate.map_dagster_head:
                    raise DeploymentContractError("Map Dagster schema differs from candidate head")
                updated = self._advance_pinned_runtime_journal(journal, "map_dagster_ready")
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                self._run_pinned_runtime_rebuild_compose(
                    [
                        "up",
                        "-d",
                        "--wait",
                        "--wait-timeout",
                        "300",
                        "kor-travel-map-ui",
                        "kor-travel-map-dagster",
                        "kor-travel-map-dagster-daemon",
                    ],
                    transaction=runtime_transaction,
                )
                updated = self._advance_pinned_runtime_journal(journal, "map_runtime_ready")
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                with pinvi_bootstrap_credential_file(
                    state_paths=state_paths,
                    values=environment_snapshot.effective,
                    transaction_id=journal.transaction_id,
                    email=environment_snapshot.effective["KTDM_C6C_PINVI_ADMIN_EMAIL"],
                    password=environment_snapshot.effective["KTDM_C6C_PINVI_ADMIN_PASSWORD"],
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
                            "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE="
                            "/run/pinvi/bootstrap-admin.json",
                            "pinvi-admin-bootstrap",
                        ],
                        transaction=runtime_transaction,
                    )
                if read_database_schema_revision(runtimes[2]) != journal.candidate.pinvi_head:
                    raise DeploymentContractError("PinVi schema differs from candidate head")
                updated = self._advance_pinned_runtime_journal(
                    journal, "pinvi_schema_ready"
                )
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                self._run_pinned_runtime_rebuild_compose(
                    ["up", "-d", "--wait", "--wait-timeout", "300", "pinvi-api"],
                    transaction=runtime_transaction,
                )
                self._require_services_ready(
                    ("pinvi-api",),
                    transaction=runtime_transaction,
                    frozen_recovery=True,
                )
                updated = self._advance_pinned_runtime_journal(journal, "pinvi_api_ready")
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                self._run_pinned_runtime_rebuild_compose(
                    [
                        "up",
                        "-d",
                        "--wait",
                        "--wait-timeout",
                        "300",
                        "pinvi-web",
                        "pinvi-dagster",
                    ],
                    transaction=runtime_transaction,
                )
                self._require_services_ready(
                    RUNTIME_SERVICES,
                    transaction=runtime_transaction,
                    frozen_recovery=True,
                )
                updated = self._advance_pinned_runtime_journal(
                    journal, "pinvi_runtime_ready"
                )
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                updated = self._advance_pinned_runtime_journal(
                    journal, "contract_verified"
                )
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                updated = self._advance_pinned_runtime_journal(
                    journal, "manifest_committing"
                )
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                write_pinned_runtime_manifest(
                    state_paths.manifest,
                    PinnedRuntimeManifest(version=5, active_generation=journal.candidate),
                )
                reconcile_generation_references(
                    (journal.candidate,),
                    cwd=get_project_root(),
                )
                updated = self._advance_pinned_runtime_journal(journal, "committed")
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                return self._pinned_runtime_result(journal, resumed=resumed)
            except Exception:
                try:
                    self._run_pinned_runtime_rebuild_compose(
                        ["stop", *RUNTIME_SERVICES],
                        transaction=runtime_transaction,
                    )
                    self._retire_pinned_runtime_oneshot_writers(
                        transaction=runtime_transaction,
                    )
                    retire_stale_pinvi_bootstrap_credential(
                        state_paths=state_paths,
                        values=environment_snapshot.effective,
                        transaction_id=journal.transaction_id,
                    )
                except Exception as cleanup_error:
                    raise DeploymentContractError(
                        "pinned runtime rebuild failure cleanup could not prove one-shot writer absence"
                    ) from cleanup_error
                raise

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
        result = self.run(["ps", *services], capture_output=capture_output)
        result["target"] = target
        result["target_sequence"] = target_sequence_for_target(target)
        result["services"] = services
        return result

    def logs(
        self,
        name: str,
        *,
        follow: bool = False,
        tail: int = 100,
        capture_output: bool = True,
    ) -> dict[str, Any]:
        if is_known_target(name):
            services = runtime_services_for_target(name)
        else:
            services = [name]

        args = ["logs", f"--tail={tail}"]
        if follow:
            args.append("-f")
        args.extend(services)
        result = self.run(args, capture_output=capture_output)
        result["target"] = name
        if is_known_target(name):
            result["target_sequence"] = target_sequence_for_target(name)
        result["services"] = services
        return result


compose_service = ComposeService()
