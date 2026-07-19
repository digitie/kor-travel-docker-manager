import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import (
    _COMPATIBLE_PAIR_MUTATION_CAPABILITY,
    _MANAGED_COMPOSE_MUTATION_CAPABILITY,
    _MAP_API_SERVICE,
    _MAP_DAGSTER_DAEMON_SERVICE,
    _MAP_DAGSTER_SERVICE,
    _MAP_RUNTIME_CONTAINERS,
    _MAP_RUNTIME_SERVICES,
    _MAP_UI_SERVICE,
    _PINVI_API_SERVICE,
    C6cBuildProvenance,
    C6cDeploymentConfig,
    CandidateSystemBindSnapshot,
    CompatibleImagePair,
    CompatiblePairManifest,
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
    PinviCancelProbeState,
    _assert_candidate_single_file_boundary,
    _expand_env_path,
    assert_c6c_mutation_allowed,
    assert_compose_mutation_allowed,
    assert_manager_mutation_allowed,
    assert_pair_manifest_bootstrap_allowed,
    c6c_deployment_lock,
    c6c_global_mutation_lock_path,
    c6c_state_paths,
    complete_map_production_env_migration,
    compose_volume_graph_hash,
    initial_pair_manifest,
    load_c6c_deployment_config_from_environment,
    load_pair_manifest,
    load_or_create_map_production_env_migration,
    manifest_with_active_pair,
    new_image_pair,
    revalidate_candidate_system_bind_snapshots,
    run_map_ops_smoke,
    run_map_ui_auth_preflight,
    run_pinvi_canonical_smoke,
    run_ui_auth_smoke,
    validate_c6c_build_source_wiring,
    validate_compose_candidate_protected_values,
    validate_current_map_ui_auth_runtime,
    validate_resolved_c6c_build_provenance,
    validate_resolved_compose_candidate_protected_values,
    validate_resolved_compose_image_pair,
    validate_resolved_compose_secret_isolation,
    validate_runtime_secret_isolation,
    write_pair_manifest,
)
from kor_travel_docker_manager.services.registry import (
    get_target,
    init_steps_for_target,
    is_known_target,
    runtime_services_for_target,
    services_for_target,
    target_sequence_for_target,
)


def get_project_root() -> str:
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


def _derive_c6c_build_provenance(
    environment: Mapping[str, str],
    *,
    compose_path: str,
) -> C6cBuildProvenance:
    """Map runtime과 PinVi build context의 clean HEAD를 provenance로 확정한다."""

    compose_directory = Path(compose_path).resolve().parent
    revisions = {
        "KOR_TRAVEL_MAP_GIT_COMMIT": _clean_repository_revision(
            environment.get("KOR_TRAVEL_MAP_REPO_DIR", "../kor-travel-map"),
            compose_directory=compose_directory,
            label="Map",
        ),
        "PINVI_SOURCE_REVISION": _clean_repository_revision(
            environment.get("PINVI_REPO_DIR", "../pinvi"),
            compose_directory=compose_directory,
            label="PinVi",
        ),
    }
    for env_name, expected in revisions.items():
        configured = environment.get(env_name)
        if configured is not None and configured != expected:
            raise DeploymentContractError(
                f"{env_name} must match the clean build context HEAD"
            )
    configured_build_environment = environment.get("PINVI_BUILD_ENVIRONMENT")
    if (
        configured_build_environment is not None
        and configured_build_environment != "production"
    ):
        raise DeploymentContractError(
            "PINVI_BUILD_ENVIRONMENT must be production for C6c build"
        )
    return C6cBuildProvenance(
        map_source_revision=revisions["KOR_TRAVEL_MAP_GIT_COMMIT"],
        pinvi_source_revision=revisions["PINVI_SOURCE_REVISION"],
    )


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
        "${KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET:?"
        "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET is required}"
    ),
    "KOR_TRAVEL_MAP_API_SERVICE_TOKEN": (
        "${KOR_TRAVEL_MAP_API_SERVICE_TOKEN:?"
        "KOR_TRAVEL_MAP_API_SERVICE_TOKEN is required}"
    ),
}
_MAP_SOURCE_V3_UI_ENVIRONMENT = {
    "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET": (
        "${KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET:?"
        "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET is required}"
    ),
    "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": (
        "${KOR_TRAVEL_MAP_UI_ADMIN_USERNAME:-admin}"
    ),
    "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": (
        "${KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH:?"
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH is required}"
    ),
    "KOR_TRAVEL_MAP_UI_SESSION_SECRET": (
        "${KOR_TRAVEL_MAP_UI_SESSION_SECRET:?"
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET is required}"
    ),
}
_MAP_SOURCE_V4_CURSOR_ENV_VALUE = (
    "${KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET:?"
    "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET is required}"
)
_MAP_SOURCE_PROTECTED_ENV_VALUES = {
    "KOR_TRAVEL_MAP_API_PROFILE": (
        _MAP_SOURCE_V3_API_ENVIRONMENT["KOR_TRAVEL_MAP_API_PROFILE"]
    ),
    "KOR_TRAVEL_MAP_API_DEBUG_ROUTES_ENABLED": (
        _MAP_SOURCE_V3_API_ENVIRONMENT[
            "KOR_TRAVEL_MAP_API_DEBUG_ROUTES_ENABLED"
        ]
    ),
    "KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED": (
        _MAP_SOURCE_V3_API_ENVIRONMENT[
            "KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED"
        ]
    ),
    "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET": (
        "${KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET:?"
        "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET is required}"
    ),
    "KOR_TRAVEL_MAP_API_SERVICE_TOKEN": (
        "${KOR_TRAVEL_MAP_API_SERVICE_TOKEN:?"
        "KOR_TRAVEL_MAP_API_SERVICE_TOKEN is required}"
    ),
    "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET": (
        _MAP_SOURCE_V4_CURSOR_ENV_VALUE
    ),
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
        loader.dispose()


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
    allowed_values = {
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
    return c6c_global_mutation_lock_path()


@dataclass(frozen=True)
class ComposeEnvFileIdentity:
    exists: bool
    device: int | None = None
    inode: int | None = None
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None


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
            manifest_path=(
                get_compatible_pair_manifest_path(environment_snapshot.effective)
                if derive_manifest_path
                else None
            ),
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

    def _materialize_active_recovery_transaction_unlocked(
        self,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        active_pair: CompatibleImagePair,
    ) -> ComposeTransactionSnapshot:
        """Frozen root 입력만으로 manifest active pair 복구 문서를 만든다."""

        self._validate_frozen_transaction_unlocked(transaction)
        try:
            source = yaml.safe_load(
                transaction.compose_source_bytes.decode("utf-8")
            ) or {}
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise ComposeCandidateContractError(
                "active recovery transaction source is invalid"
            ) from exc
        if not isinstance(source, Mapping):
            raise ComposeCandidateContractError(
                "active recovery transaction source is not a mapping"
            )
        environment = dict(transaction.environment.effective)
        environment.update(self._pair_image_environment(active_pair))
        descriptors: tuple[int, ...] = ()
        try:
            materialized, descriptors = _materialize_external_inputs_with_memfd(
                source,
                transaction.external_inputs,
            )
            completed = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    "/dev/null",
                    "--project-directory",
                    str(Path(transaction.environment.compose_path).parent),
                    "-f",
                    "-",
                    "config",
                    "--format",
                    "json",
                ],
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
                env=environment,
                pass_fds=descriptors,
                input=yaml.safe_dump(
                    materialized,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                ),
            )
            if completed.returncode != 0:
                raise ComposeCandidateContractError(
                    "active recovery transaction resolution failed"
                )
            try:
                resolved = json.loads(completed.stdout)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ComposeCandidateContractError(
                    "active recovery transaction returned invalid JSON"
                ) from exc
            if not isinstance(resolved, Mapping):
                raise ComposeCandidateContractError(
                    "active recovery transaction resolved document is invalid"
                )
            _assert_resolved_external_inputs_materialized(resolved)
            if (
                compose_volume_graph_hash(resolved)
                != transaction.resolved_volume_graph_hash
            ):
                raise ComposeCandidateContractError(
                    "active recovery transaction volume graph changed"
                )
            revalidate_candidate_system_bind_snapshots(
                transaction.system_bind_snapshots
            )
            validate_resolved_compose_image_pair(resolved, config, active_pair)
            frozen_resolved = json.loads(
                _serialize_resolved_compose_document(resolved)
            )
            if not isinstance(frozen_resolved, Mapping):
                raise ComposeCandidateContractError(
                    "active recovery transaction cannot be frozen"
                )
            return replace(
                transaction,
                resolved=frozen_resolved,
                resolved_document_hash=(
                    _resolved_compose_document_hash(frozen_resolved)
                ),
            )
        except OSError as exc:
            raise ComposeCandidateContractError(
                "active recovery transaction could not start"
            ) from exc
        finally:
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

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
            and _frozen_recovery_capability
            is not _TRUSTED_FROZEN_RECOVERY_CAPABILITY
        ):
            raise ComposeCandidateContractError(
                "untrusted frozen recovery capability"
            )
        frozen_recovery = (
            _frozen_recovery_capability
            is _TRUSTED_FROZEN_RECOVERY_CAPABILITY
        )
        mutation_identifiers = self._compose_mutation_identifiers(args)
        if (
            mutation_identifiers
            or transaction is not None
            or expected_environment_snapshot is not None
        ):
            with c6c_deployment_lock(get_c6c_deployment_lock_path()):
                if frozen_recovery:
                    if transaction is None or environment is not None:
                        raise ComposeCandidateContractError(
                            "frozen recovery requires one closed transaction"
                        )
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
                captured_validation: ValidatedComposeCandidate | None = None
                if transaction is None and expected_environment_snapshot is None:
                    transaction, captured_validation = (
                        self._capture_transaction_unlocked(
                            environment_override=environment,
                        )
                    )
                environment_snapshot = (
                    transaction.environment
                    if transaction is not None
                    else expected_environment_snapshot
                )
                if environment_snapshot is None:
                    raise ComposeCandidateContractError(
                        "compose transaction has no environment snapshot"
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
                if (
                    transaction is not None
                    and snapshots != transaction.system_bind_snapshots
                ):
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

        with c6c_deployment_lock(get_c6c_deployment_lock_path()):
            transaction, persisted = self._capture_transaction_unlocked(
                environment_override=environment_override,
                environment_snapshot=environment_snapshot,
            )
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
        if (
            candidate_validation.raw_volume_graph_hash
            != baseline_validation.raw_volume_graph_hash
        ):
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
            materialized_candidate, external_descriptors = (
                _materialize_external_inputs_with_memfd(
                    candidate,
                    external_input_snapshot,
                )
            )
            candidate_input = yaml.safe_dump(
                materialized_candidate,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
            command = ["docker", "compose"]
            command.extend(["--env-file", "/dev/null"])
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
        with c6c_deployment_lock(get_c6c_deployment_lock_path()):
            transaction, validation = self._capture_transaction_unlocked()
            assert_manager_mutation_allowed(
                environment=transaction.environment.effective
            )
            assert_c6c_mutation_allowed(
                services,
                environment=transaction.environment.effective,
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
            if (
                transaction.resolved_volume_graph_hash
                != expected_resolved_volume_graph_hash
            ):
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

    def deploy_compatible_pinvi_pair(
        self,
        *,
        build: bool = False,
        recreate: bool = True,
    ) -> dict[str, Any]:
        """production Map runtime+PinVi API set의 유일한 배포 mutation 진입점."""

        with c6c_deployment_lock(get_c6c_deployment_lock_path()):
            transaction, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            assert_manager_mutation_allowed(
                environment=transaction.environment.effective
            )
            config = load_c6c_deployment_config_from_environment(
                transaction.environment.effective
            )
            if not config.production:
                raise DeploymentContractError(
                    "compatible-pair deploy is available only in production mode"
                )
            build_provenance = (
                _derive_c6c_build_provenance(
                    transaction.environment.effective,
                    compose_path=transaction.environment.compose_path,
                )
                if build
                else None
            )
            return self._ensure_production_pinvi_target(
                "pinvi",
                config=config,
                build=build,
                recreate=recreate,
                capture_output=True,
                transaction=transaction,
                build_provenance=build_provenance,
            )

    def _ensure_production_pinvi_target(
        self,
        target: str,
        *,
        config: C6cDeploymentConfig,
        build: bool,
        recreate: bool,
        capture_output: bool,
        transaction: ComposeTransactionSnapshot,
        build_provenance: C6cBuildProvenance | None = None,
    ) -> dict[str, Any]:
        """C6c compatible runtime set을 Map 검증 뒤 PinVi로 단계 배포한다."""

        manifest = self._production_preflight(
            config,
            transaction=transaction,
            build_provenance=build_provenance,
        )
        active_recovery_transaction = (
            self._materialize_active_recovery_transaction_unlocked(
                transaction,
                config,
                manifest.active,
            )
        )
        cancel_probe_state = PinviCancelProbeState()
        target_sequence = target_sequence_for_target(target)
        services = services_for_target(target)

        # Pair transaction은 Map runtime 네 service와 PinVi API를 함께 변경한다.
        # 나머지 dependency/init/app은 현재 ready여야 한다.
        self._require_services_ready(
            services,
            transaction=transaction,
        )
        preflight_ui_smoke = self._preflight_current_map_ui_auth(
            config,
            manifest=manifest,
            transaction=transaction,
        )

        result: dict[str, Any] = {
            "success": True,
            "returncode": 0,
            "target": target,
            "target_sequence": target_sequence,
            "services": services,
            "init_results": [],
            "stages": [],
            "smoke": [],
            "pinvi_smoke": [],
            "ui_smoke": [],
            "preflight_ui_smoke": preflight_ui_smoke,
            "runtime_secret_isolation": False,
            "deployment_state": "preflight_complete",
            "command": [],
            "stdout": "",
            "stderr": "",
        }

        candidate_pair, prebuild_result = self._prepare_c6c_candidate_pair(
            config,
            build=build,
            build_provenance=build_provenance,
            transaction=transaction,
        )
        if prebuild_result is not None:
            self._append_stage_result(
                result,
                "prebuild_candidate_pair",
                prebuild_result,
                config,
            )
        result["candidate_image_provenance"] = self._pair_provenance_payload(
            candidate_pair
        )

        try:
            self._revalidate_c6c_build_provenance(
                build_provenance,
                transaction=transaction,
            )
            verification = self._activate_pair_sequentially(
                result,
                config,
                candidate_pair,
                services,
                stage_prefix="deploy",
                cancel_probe_state=cancel_probe_state,
                transaction=transaction,
            )
            result["smoke"] = verification["map_smoke"]
            result["pinvi_smoke"] = verification["pinvi_smoke"]
            result["ui_smoke"] = verification["ui_smoke"]
            result["runtime_secret_isolation"] = True
            result["activation_verification"] = verification
            result["image_provenance"] = self._pair_provenance_payload(candidate_pair)
            if transaction.manifest_path is None:
                raise DeploymentContractError(
                    "compatible-pair transaction has no manifest path"
                )
            write_pair_manifest(
                transaction.manifest_path,
                manifest_with_active_pair(manifest, candidate_pair),
            )
            result["deployment_state"] = "active_manifest_committed"
            return result
        except Exception as exc:
            self._fail_result(
                result,
                str(exc)
                if isinstance(exc, DeploymentContractError)
                else "unexpected compatible-pair transaction failure",
            )
            recovery = self._recover_previous_pair(
                result,
                config,
                manifest.active,
                services,
                cancel_probe_state=cancel_probe_state,
                transaction=active_recovery_transaction,
            )
            raise ComposePostMutationContractError(
                exc,
                recovery_attempted=True,
                recovery_succeeded=bool(recovery.get("success")),
                recovery_error=(
                    None
                    if recovery.get("success")
                    else str(recovery.get("error") or recovery.get("state"))
                ),
                restoration=recovery,
            ) from exc

    def _production_preflight(
        self,
        config: C6cDeploymentConfig,
        *,
        transaction: ComposeTransactionSnapshot,
        build_provenance: C6cBuildProvenance | None = None,
    ) -> CompatiblePairManifest:
        self._validate_resolved_compose_contract(
            config,
            transaction=transaction,
        )
        self._revalidate_c6c_build_provenance(
            build_provenance,
            transaction=transaction,
        )

        if transaction.manifest_path is None:
            raise DeploymentContractError(
                "compatible-pair transaction has no manifest path"
            )
        manifest = load_pair_manifest(transaction.manifest_path)
        for pair in (manifest.rollback, manifest.active):
            if pair.contract_generation != config.contract_generation:
                raise DeploymentContractError(
                    "compatible pair manifest generation differs from deployment contract"
                )
            self._require_pair_image_provenance(pair)
        self._validate_resolved_compose_contract(
            config,
            environment_override=self._pair_image_environment(manifest.active),
            expected_pair=manifest.active,
            transaction=transaction,
        )
        current = self._inspect_current_pair(config)
        if not self._pair_matches(current, manifest.active):
            raise DeploymentContractError(
                "running Map+PinVi image pair drifted from the captured compatible manifest"
            )
        return manifest

    def _revalidate_c6c_build_provenance(
        self,
        expected: C6cBuildProvenance | None,
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        if expected is None:
            return
        actual = _derive_c6c_build_provenance(
            transaction.environment.effective,
            compose_path=transaction.environment.compose_path,
        )
        if actual != expected:
            raise DeploymentContractError(
                "C6c build context revision changed during the transaction"
            )
        try:
            source = yaml.safe_load(
                transaction.compose_source_bytes.decode("utf-8")
            ) or {}
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise ComposeCandidateContractError(
                "C6c provenance compose source is invalid"
            ) from exc
        if not isinstance(source, Mapping):
            raise ComposeCandidateContractError(
                "C6c provenance compose source is not a mapping"
            )
        validate_c6c_build_source_wiring(source)
        validation = self._validate_current_compose_candidate_unlocked(
            environment_override=expected.compose_environment(),
            environment_snapshot=transaction.environment,
            external_input_snapshot=transaction.external_inputs,
        )
        if (
            validation.raw_volume_graph_hash != transaction.raw_volume_graph_hash
            or validation.resolved_volume_graph_hash
            != transaction.resolved_volume_graph_hash
        ):
            raise ComposeCandidateContractError(
                "C6c provenance resolution changed the frozen volume graph"
            )
        validate_resolved_c6c_build_provenance(validation.resolved, expected)

    def _validate_c6c_snapshot_build_contract(
        self,
        provenance: C6cBuildProvenance,
        build_environment: Mapping[str, str],
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        validation = self._validate_current_compose_candidate_unlocked(
            environment_override=build_environment,
            environment_snapshot=transaction.environment,
            external_input_snapshot=transaction.external_inputs,
        )
        if (
            validation.raw_volume_graph_hash != transaction.raw_volume_graph_hash
            or validation.resolved_volume_graph_hash
            != transaction.resolved_volume_graph_hash
        ):
            raise ComposeCandidateContractError(
                "C6c source snapshot resolution changed the frozen volume graph"
            )
        validate_resolved_c6c_build_provenance(
            validation.resolved,
            provenance,
            expected_build_contexts={
                service_name: build_environment["KOR_TRAVEL_MAP_REPO_DIR"]
                for service_name in _MAP_RUNTIME_SERVICES
            }
            | {
                _PINVI_API_SERVICE: build_environment["PINVI_REPO_DIR"],
            },
        )

    def _prepare_c6c_candidate_pair(
        self,
        config: C6cDeploymentConfig,
        *,
        build: bool,
        build_provenance: C6cBuildProvenance | None,
        transaction: ComposeTransactionSnapshot,
    ) -> tuple[CompatibleImagePair, Mapping[str, Any] | None]:
        """Map runtime set과 PinVi candidate를 container 변경 없이 build/attest한다."""

        if build != (build_provenance is not None):
            raise DeploymentContractError(
                "C6c build flag and source provenance must be provided together"
            )
        if build_provenance is None:
            return (
                self._inspect_c6c_candidate_pair(
                    config,
                    environment_override=None,
                    transaction=transaction,
                ),
                None,
            )
        self._revalidate_c6c_build_provenance(
            build_provenance,
            transaction=transaction,
        )
        with _c6c_source_snapshot_environment(
            transaction.environment.effective,
            compose_path=transaction.environment.compose_path,
            provenance=build_provenance,
        ) as build_environment:
            self._validate_c6c_snapshot_build_contract(
                build_provenance,
                build_environment,
                transaction=transaction,
            )
            build_result = self.run(
                ["build", *_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE],
                capture_output=True,
                environment=build_environment,
                mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                redact_config=config,
                transaction=transaction,
            )
            if not build_result["success"]:
                raise DeploymentContractError(
                    "C6c candidate image build failed before container mutation"
                )
            self._revalidate_c6c_build_provenance(
                build_provenance,
                transaction=transaction,
            )
            pair = self._inspect_c6c_candidate_pair(
                config,
                environment_override=build_environment,
                transaction=transaction,
            )
        self._require_expected_source_provenance(pair, build_provenance)
        return pair, build_result

    def _inspect_c6c_candidate_pair(
        self,
        config: C6cDeploymentConfig,
        *,
        environment_override: Mapping[str, str] | None,
        transaction: ComposeTransactionSnapshot,
    ) -> CompatibleImagePair:
        """resolved image reference를 immutable ID와 OCI provenance로 고정한다."""

        validation = self._validate_current_compose_candidate_unlocked(
            environment_override=environment_override,
            environment_snapshot=transaction.environment,
            external_input_snapshot=transaction.external_inputs,
        )
        services = validation.resolved.get("services")
        if not isinstance(services, Mapping):
            raise DeploymentContractError(
                "resolved compose config has no services mapping"
            )
        image_references: dict[str, str] = {}
        for service_name in (*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE):
            service = services.get(service_name)
            image = service.get("image") if isinstance(service, Mapping) else None
            if not isinstance(image, str) or not image:
                raise DeploymentContractError(
                    f"resolved compose is missing {service_name} candidate image"
                )
            image_references[service_name] = image
        map_image_ids = {
            service_name: self._inspect_image_reference_id(
                image_references[service_name],
                label=service_name,
            )
            for service_name in _MAP_RUNTIME_SERVICES
        }
        pinvi_image_id = self._inspect_image_reference_id(
            image_references[_PINVI_API_SERVICE],
            label="PinVi",
        )
        pair = new_image_pair(
            map_image_ids[_MAP_API_SERVICE],
            pinvi_image_id,
            config.contract_generation,
            map_ui_image_id=map_image_ids[_MAP_UI_SERVICE],
            map_dagster_image_id=map_image_ids[_MAP_DAGSTER_SERVICE],
            map_dagster_daemon_image_id=map_image_ids[
                _MAP_DAGSTER_DAEMON_SERVICE
            ],
            map_source_revision=self._inspect_image_source_revision(
                map_image_ids[_MAP_API_SERVICE],
                label="Map",
            ),
            pinvi_source_revision=self._inspect_image_source_revision(
                pinvi_image_id,
                label="PinVi",
                expected_build_environment="production",
            ),
        )
        for service_name in _MAP_RUNTIME_SERVICES[1:]:
            revision = self._inspect_image_source_revision(
                map_image_ids[service_name],
                label=service_name,
            )
            if revision != pair.map_source_revision:
                raise DeploymentContractError(
                    f"{service_name} candidate image revision differs from Map API"
                )
        return pair

    @staticmethod
    def _require_expected_source_provenance(
        pair: CompatibleImagePair,
        expected: C6cBuildProvenance | None,
    ) -> None:
        if expected is None:
            return
        if (
            pair.map_source_revision != expected.map_source_revision
            or pair.pinvi_source_revision != expected.pinvi_source_revision
        ):
            raise DeploymentContractError(
                "built C6c image provenance differs from the clean checkout HEAD"
            )

    @staticmethod
    def _pair_provenance_payload(pair: CompatibleImagePair) -> dict[str, Any]:
        return {
            "map": {
                "image_id": pair.map_image_id,
                "source_revision": pair.map_source_revision,
                "runtime_images": {
                    _MAP_API_SERVICE: pair.map_image_id,
                    _MAP_UI_SERVICE: pair.map_ui_image_id,
                    _MAP_DAGSTER_SERVICE: pair.map_dagster_image_id,
                    _MAP_DAGSTER_DAEMON_SERVICE: pair.map_dagster_daemon_image_id,
                },
            },
            "pinvi": {
                "image_id": pair.pinvi_image_id,
                "source_revision": pair.pinvi_source_revision,
            },
        }

    def _preflight_current_map_ui_auth(
        self,
        config: C6cDeploymentConfig,
        *,
        manifest: CompatiblePairManifest,
        transaction: ComposeTransactionSnapshot,
    ) -> list[dict[str, int | str]]:
        source_contract_versions = {
            source_revision: _map_source_environment_contract_version(
                transaction.environment.effective,
                compose_path=transaction.environment.compose_path,
                source_revision=source_revision,
            )
            for source_revision in {
                manifest.active.map_source_revision,
                manifest.rollback.map_source_revision,
            }
        }
        active_source_env_contract_version = source_contract_versions[
            manifest.active.map_source_revision
        ]
        source_contract_version_set = set(source_contract_versions.values())
        if transaction.manifest_path is None:
            raise DeploymentContractError(
                "compatible-pair transaction has no migration marker path"
            )
        marker = load_or_create_map_production_env_migration(
            transaction.manifest_path,
            baseline_manifest=manifest,
            allow_create=source_contract_version_set == {3},
        )
        if marker.state == "pending" and source_contract_version_set != {3}:
            raise DeploymentContractError(
                "pending Map production env migration requires the original v3 baseline"
            )
        allow_legacy_admin_proxy_absence = (
            marker.state == "pending"
        )
        runtime_config = self._inspect_container_runtime_config(
            config.map_ui_container
        )
        validate_current_map_ui_auth_runtime(
            runtime_config,
            config,
            source_env_contract_version=active_source_env_contract_version,
            allow_legacy_admin_proxy_absence=(
                allow_legacy_admin_proxy_absence
            ),
        )
        return run_map_ui_auth_preflight(config)

    def _validate_resolved_compose_contract(
        self,
        config: C6cDeploymentConfig,
        *,
        environment_override: Mapping[str, str] | None = None,
        expected_pair: CompatibleImagePair | None = None,
        transaction: ComposeTransactionSnapshot,
        frozen_recovery: bool = False,
    ) -> Mapping[str, Any]:
        if frozen_recovery:
            if environment_override is not None:
                raise ComposeCandidateContractError(
                    "frozen recovery must not resolve a new environment override"
                )
            resolved = self._validate_frozen_transaction_unlocked(transaction)
        else:
            validation = self._validate_current_compose_candidate_unlocked(
                environment_override=environment_override,
                environment_snapshot=transaction.environment,
                external_input_snapshot=transaction.external_inputs,
            )
            resolved = validation.resolved
        if expected_pair is None:
            validate_resolved_compose_secret_isolation(resolved, config)
        else:
            validate_resolved_compose_image_pair(resolved, config, expected_pair)
        return resolved

    def _run_up_stage(
        self,
        result: dict[str, Any],
        stage: str,
        services: list[str],
        *,
        build: bool,
        recreate: bool,
        no_deps: bool,
        wait: bool = False,
        capture_output: bool,
        environment: Mapping[str, str] | None = None,
        mutation_capability: object | None = None,
        redact_config: C6cDeploymentConfig | None = None,
        transaction: ComposeTransactionSnapshot,
        frozen_recovery: bool = False,
        build_provenance: C6cBuildProvenance | None = None,
    ) -> bool:
        if frozen_recovery and build_provenance is not None:
            raise ComposeCandidateContractError(
                "frozen recovery must not carry build provenance"
            )
        self._revalidate_c6c_build_provenance(
            build_provenance,
            transaction=transaction,
        )
        stage_environment = dict(environment or {})
        if build_provenance is not None:
            stage_environment.update(build_provenance.compose_environment())
        args = ["up", "-d"]
        if no_deps:
            args.append("--no-deps")
        if wait:
            args.extend(["--wait", "--wait-timeout", "120"])
        if build:
            args.append("--build")
        if recreate:
            args.append("--force-recreate")
        args.extend(services)
        if frozen_recovery:
            if environment is not None:
                raise ComposeCandidateContractError(
                    "frozen recovery stage must use the resolved transaction"
                )
            stage_result = self._run_frozen_recovery(
                args,
                capture_output=capture_output,
                mutation_capability=mutation_capability,
                redact_config=redact_config,
                transaction=transaction,
            )
        else:
            stage_result = self.run(
                args,
                capture_output=capture_output,
                environment=stage_environment or None,
                mutation_capability=mutation_capability,
                redact_config=redact_config,
                transaction=transaction,
            )
        result["command"].append(stage_result["command"])
        result["stages"].append(
            {"name": stage, "services": services, "success": stage_result["success"]}
        )
        stdout = stage_result.get("stdout", "")
        stderr = stage_result.get("stderr", "")
        if redact_config is not None:
            stdout = self._redact_c6c_output(stdout, redact_config)
            stderr = self._redact_c6c_output(stderr, redact_config)
        result["stdout"] += stdout
        result["stderr"] += stderr
        if stage_result["success"]:
            return True
        result["success"] = False
        result["returncode"] = stage_result["returncode"]
        return False

    def _run_init_steps(
        self,
        result: dict[str, Any],
        init_steps: list[dict[str, Any]],
        *,
        capture_output: bool,
        transaction: ComposeTransactionSnapshot,
    ) -> bool:
        for step in init_steps:
            try:
                step_result = self.run(
                    step.get("command", []),
                    capture_output=capture_output,
                    mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                    transaction=transaction,
                )
            except Exception:
                self._fail_result(result, "bootstrap init command raised unexpectedly")
                return False
            annotated = {
                "target": step.get("target"),
                "name": step.get("name"),
                "description": step.get("description"),
                **step_result,
            }
            result["init_results"].append(annotated)
            result["command"].append(step_result["command"])
            result["stdout"] += step_result.get("stdout", "")
            result["stderr"] += step_result.get("stderr", "")
            if step_result["success"]:
                continue
            result["success"] = False
            result["returncode"] = step_result["returncode"]
            return False
        return True

    @staticmethod
    def _fail_result(result: dict[str, Any], message: str) -> None:
        result["success"] = False
        result["returncode"] = 1
        result["stderr"] += f"{message}\n"

    @staticmethod
    def _redact_c6c_output(text: str, config: C6cDeploymentConfig) -> str:
        redacted = text
        credentials = {
            value
            for value in (
                config.read_token,
                config.cancel_token,
                config.map_ui_password_hash,
                config.map_ui_session_secret,
                config.map_admin_proxy_secret,
                config.map_service_token,
                config.map_cursor_signing_secret,
                config.smoke.map_ui_password,
                config.smoke.pinvi_admin_email,
                config.smoke.pinvi_admin_password,
                config.smoke.cancel_probe_job_id,
                config.contract_generation,
            )
            if value
        }
        for credential in sorted(
            credentials,
            key=lambda value: (-len(value), value),
        ):
            redacted = redacted.replace(credential, "<redacted>")
        return redacted

    def _append_stage_result(
        self,
        result: dict[str, Any],
        stage: str,
        stage_result: Mapping[str, Any],
        config: C6cDeploymentConfig,
    ) -> None:
        result["command"].append(stage_result["command"])
        result["stages"].append(
            {"name": stage, "services": [], "success": stage_result["success"]}
        )
        result["stdout"] += self._redact_c6c_output(
            str(stage_result.get("stdout", "")), config
        )
        result["stderr"] += self._redact_c6c_output(
            str(stage_result.get("stderr", "")), config
        )
        if not stage_result["success"]:
            result["success"] = False
            result["returncode"] = int(stage_result["returncode"])

    @staticmethod
    def _pair_matches(first: CompatibleImagePair, second: CompatibleImagePair) -> bool:
        return (
            first.map_image_id == second.map_image_id
            and first.map_ui_image_id == second.map_ui_image_id
            and first.map_dagster_image_id == second.map_dagster_image_id
            and first.map_dagster_daemon_image_id
            == second.map_dagster_daemon_image_id
            and first.map_source_revision == second.map_source_revision
            and first.pinvi_image_id == second.pinvi_image_id
            and first.pinvi_source_revision == second.pinvi_source_revision
            and first.contract_generation == second.contract_generation
        )

    @staticmethod
    def _pair_image_environment(pair: CompatibleImagePair) -> dict[str, str]:
        return {
            "KOR_TRAVEL_MAP_API_IMAGE": pair.map_image_id,
            "KOR_TRAVEL_MAP_UI_IMAGE": pair.map_ui_image_id,
            "KOR_TRAVEL_MAP_DAGSTER_IMAGE": pair.map_dagster_image_id,
            "KOR_TRAVEL_MAP_DAGSTER_DAEMON_IMAGE": pair.map_dagster_daemon_image_id,
            "KOR_TRAVEL_MAP_GIT_COMMIT": pair.map_source_revision,
            "PINVI_API_IMAGE": pair.pinvi_image_id,
            "PINVI_SOURCE_REVISION": pair.pinvi_source_revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        }

    def _verify_active_contract(
        self,
        config: C6cDeploymentConfig,
        expected_pair: CompatibleImagePair,
        services: list[str],
        *,
        cancel_probe_state: PinviCancelProbeState | None = None,
        transaction: ComposeTransactionSnapshot,
        frozen_recovery: bool = False,
    ) -> dict[str, Any]:
        self._require_services_ready(
            services,
            transaction=transaction,
            frozen_recovery=frozen_recovery,
        )
        self._validate_resolved_compose_contract(
            config,
            environment_override=(
                None
                if frozen_recovery
                else self._pair_image_environment(expected_pair)
            ),
            expected_pair=expected_pair,
            transaction=transaction,
            frozen_recovery=frozen_recovery,
        )
        actual = self._inspect_current_pair(config)
        if not self._pair_matches(actual, expected_pair):
            raise DeploymentContractError("compatible pair image verification failed")
        map_smoke = run_map_ops_smoke(config)
        pinvi_smoke = run_pinvi_canonical_smoke(
            config,
            cancel_probe_state=cancel_probe_state,
        )
        ui_smoke = run_ui_auth_smoke(config)
        runtime_configs = self._inspect_c6c_runtime_configs(
            config,
            services,
            transaction=transaction,
            frozen_recovery=frozen_recovery,
        )
        validate_runtime_secret_isolation(runtime_configs, config)
        if not frozen_recovery:
            if transaction.manifest_path is None:
                raise DeploymentContractError(
                    "compatible-pair transaction has no migration marker path"
                )
            complete_map_production_env_migration(
                transaction.manifest_path
            )
        return {
            "contract_generation": expected_pair.contract_generation,
            "image_provenance": self._pair_provenance_payload(expected_pair),
            "map_smoke": map_smoke,
            "pinvi_smoke": pinvi_smoke,
            "ui_smoke": ui_smoke,
            "runtime_secret_isolation": True,
        }

    def _recover_previous_pair(
        self,
        result: dict[str, Any],
        config: C6cDeploymentConfig,
        active_at_start: CompatibleImagePair,
        services: list[str],
        *,
        cancel_probe_state: PinviCancelProbeState | None = None,
        transaction: ComposeTransactionSnapshot,
    ) -> dict[str, Any]:
        """실패 시 배포 시작 시점 active pair를 복원하고 manifest는 건드리지 않는다."""

        state_key = "rollback_state" if "rollback_state" in result else "deployment_state"
        result["success"] = False
        result["returncode"] = result.get("returncode") or 1
        result[state_key] = "recovery_started"
        try:
            self._validate_resolved_compose_contract(
                config,
                expected_pair=active_at_start,
                transaction=transaction,
                frozen_recovery=True,
            )
            result["recovery_verification"] = self._activate_pair_sequentially(
                result,
                config,
                active_at_start,
                services,
                stage_prefix="recovery",
                cancel_probe_state=cancel_probe_state,
                transaction=transaction,
                frozen_recovery=True,
            )
            result[state_key] = "previous_active_pair_restored"
            return {
                "success": True,
                "state": result[state_key],
                "image_provenance": self._pair_provenance_payload(active_at_start),
            }
        except Exception as recovery_error:
            halt = self._halt_c6c_pair(
                result,
                config,
                state_key,
                transaction=transaction,
            )
            return {
                "success": False,
                "state": result[state_key],
                "error": str(recovery_error),
                "halt": halt,
            }

    def _activate_pair_sequentially(
        self,
        result: dict[str, Any],
        config: C6cDeploymentConfig,
        pair: CompatibleImagePair,
        services: list[str],
        *,
        stage_prefix: str,
        cancel_probe_state: PinviCancelProbeState | None = None,
        transaction: ComposeTransactionSnapshot,
        frozen_recovery: bool = False,
    ) -> dict[str, Any]:
        """혼합 set 실행 없이 Map runtime 검증 뒤 PinVi와 전체 계약을 복원한다."""

        environment = None if frozen_recovery else self._pair_image_environment(pair)
        if frozen_recovery:
            stop_result = self._run_frozen_recovery(
                ["stop", _PINVI_API_SERVICE, *_MAP_RUNTIME_SERVICES],
                mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                transaction=transaction,
            )
        else:
            stop_result = self.run(
                ["stop", _PINVI_API_SERVICE, *_MAP_RUNTIME_SERVICES],
                mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                transaction=transaction,
            )
        self._append_stage_result(result, f"{stage_prefix}_stop_pair", stop_result, config)
        if not stop_result["success"]:
            raise DeploymentContractError("compatible pair stop failed")
        if not self._run_up_stage(
            result,
            f"{stage_prefix}_map_api",
            ["kor-travel-map-api"],
            build=False,
            recreate=True,
            no_deps=True,
            wait=True,
            capture_output=True,
            environment=environment,
            mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
            redact_config=config,
            transaction=transaction,
            frozen_recovery=frozen_recovery,
        ):
            raise DeploymentContractError("Map API pair restoration failed")
        self._verify_running_image_source_provenance(
            config.map_container,
            label="Map",
            expected_revision=pair.map_source_revision,
        )
        result[f"{stage_prefix}_map_smoke"] = run_map_ops_smoke(config)
        if not self._run_up_stage(
            result,
            f"{stage_prefix}_map_runtime_dependents",
            list(_MAP_RUNTIME_SERVICES[1:]),
            build=False,
            recreate=True,
            no_deps=True,
            wait=True,
            capture_output=True,
            environment=environment,
            mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
            redact_config=config,
            transaction=transaction,
            frozen_recovery=frozen_recovery,
        ):
            raise DeploymentContractError("Map runtime dependent restoration failed")
        self._verify_map_runtime_source_provenance(
            pair.map_source_revision,
            include_api=False,
        )
        if not self._run_up_stage(
            result,
            f"{stage_prefix}_pinvi_api",
            ["pinvi-api"],
            build=False,
            recreate=True,
            no_deps=True,
            wait=True,
            capture_output=True,
            environment=environment,
            mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
            redact_config=config,
            transaction=transaction,
            frozen_recovery=frozen_recovery,
        ):
            raise DeploymentContractError("PinVi API pair restoration failed")
        self._verify_running_image_source_provenance(
            config.pinvi_container,
            label="PinVi",
            expected_revision=pair.pinvi_source_revision,
            expected_build_environment="production",
        )
        return self._verify_active_contract(
            config,
            pair,
            services,
            cancel_probe_state=cancel_probe_state,
            transaction=transaction,
            frozen_recovery=frozen_recovery,
        )

    def _halt_c6c_pair(
        self,
        result: dict[str, Any],
        config: C6cDeploymentConfig,
        state_key: str,
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> dict[str, Any]:
        try:
            halt_result = self._run_frozen_recovery(
                ["stop", _PINVI_API_SERVICE, *_MAP_RUNTIME_SERVICES],
                mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                transaction=transaction,
            )
            self._append_stage_result(
                result, "halt_unverified_pair", halt_result, config
            )
            result[state_key] = (
                "halted_requires_operator"
                if halt_result["success"]
                else "halt_failed_requires_operator"
            )
            return {
                "success": bool(halt_result["success"]),
                "state": result[state_key],
                "command": halt_result.get("command"),
                "returncode": halt_result.get("returncode"),
                "stderr": halt_result.get("stderr"),
            }
        except Exception as halt_error:
            result[state_key] = "halt_failed_requires_operator"
            return {
                "success": False,
                "state": result[state_key],
                "error": str(halt_error),
            }

    def capture_compatible_pinvi_pair(
        self,
        *,
        verified_compatible: bool,
        build: bool = False,
    ) -> dict[str, Any]:
        """clean 환경에서 candidate runtime set을 단계 검증해 최초 v4를 기록한다."""

        if not verified_compatible:
            raise DeploymentContractError(
                "capturing a rollback pair requires --verified-compatible"
            )
        with c6c_deployment_lock(get_c6c_deployment_lock_path()):
            transaction, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            assert_manager_mutation_allowed(
                environment=transaction.environment.effective
            )
            config = load_c6c_deployment_config_from_environment(
                transaction.environment.effective
            )
            if not config.production:
                raise DeploymentContractError(
                    "compatible pair capture is available only in production mode"
                )
            build_provenance = (
                _derive_c6c_build_provenance(
                    transaction.environment.effective,
                    compose_path=transaction.environment.compose_path,
                )
                if build
                else None
            )
            self._validate_resolved_compose_contract(
                config,
                transaction=transaction,
            )
            self._revalidate_c6c_build_provenance(
                build_provenance,
                transaction=transaction,
            )
            manifest_path = transaction.manifest_path
            if manifest_path is None:
                raise DeploymentContractError(
                    "compatible-pair transaction has no manifest path"
                )
            assert_pair_manifest_bootstrap_allowed(manifest_path)
            load_or_create_map_production_env_migration(
                manifest_path,
                baseline_manifest=None,
            )
            services = services_for_target("pinvi")
            map_services = list(get_target("map").get("services", []))
            pinvi_services = list(get_target("pinvi").get("services", []))
            base_target_names = [
                target_name
                for target_name in target_sequence_for_target("pinvi")
                if target_name not in {"map", "pinvi"}
            ]
            if tuple(map_services) != _MAP_RUNTIME_SERVICES:
                raise DeploymentContractError(
                    "Map target must contain the canonical runtime service set"
                )
            map_dependents = list(_MAP_RUNTIME_SERVICES[1:])
            pinvi_dependents = [
                service for service in pinvi_services if service != "pinvi-api"
            ]
            initial_states = self._snapshot_service_states(
                services,
                transaction=transaction,
            )
            touched_services: set[str] = set()
            cancel_probe_state = PinviCancelProbeState()
            result: dict[str, Any] = {
                "success": True,
                "returncode": 0,
                "target": "pinvi-compatible-pair-bootstrap",
                "services": [*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE],
                "stages": [],
                "init_results": [],
                "command": [],
                "stdout": "",
                "stderr": "",
                "manifest": manifest_path,
                "deployment_state": "bootstrap_preflight_complete",
            }
            candidate_pair, prebuild_result = self._prepare_c6c_candidate_pair(
                config,
                build=build,
                build_provenance=build_provenance,
                transaction=transaction,
            )
            if prebuild_result is not None:
                self._append_stage_result(
                    result,
                    "prebuild_candidate_pair",
                    prebuild_result,
                    config,
                )
            candidate_environment = self._pair_image_environment(candidate_pair)
            result["candidate_image_provenance"] = self._pair_provenance_payload(
                candidate_pair
            )
            mutation_attempted = False
            try:
                for target_name in base_target_names:
                    target_config = get_target(target_name)
                    target_services = list(target_config.get("services", []))
                    touched_services.update(target_services)
                    mutation_attempted = True
                    if not self._run_up_stage(
                        result,
                        f"bootstrap_base_{target_name}",
                        target_services,
                        build=False,
                        recreate=False,
                        no_deps=True,
                        wait=True,
                        capture_output=True,
                        mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                        transaction=transaction,
                        build_provenance=build_provenance,
                    ):
                        raise DeploymentContractError(
                            "bootstrap base service deployment failed"
                        )
                    direct_init_steps = [
                        {"target": target_name, **step}
                        for step in target_config.get("init_steps", [])
                    ]
                    self._revalidate_c6c_build_provenance(
                        build_provenance,
                        transaction=transaction,
                    )
                    if not self._run_init_steps(
                        result,
                        direct_init_steps,
                        capture_output=True,
                        transaction=transaction,
                    ):
                        raise DeploymentContractError(
                            "bootstrap init command failed"
                        )
                self._revalidate_c6c_build_provenance(
                    build_provenance,
                    transaction=transaction,
                )
                touched_services.update((*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE))
                stop_result = self.run(
                    ["stop", _PINVI_API_SERVICE, *_MAP_RUNTIME_SERVICES],
                    mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                    transaction=transaction,
                )
                self._append_stage_result(
                    result,
                    "bootstrap_stop_pair",
                    stop_result,
                    config,
                )
                if not stop_result["success"]:
                    raise DeploymentContractError("bootstrap pair stop failed")
                touched_services.add("kor-travel-map-api")
                if not self._run_up_stage(
                    result,
                    "bootstrap_map_api",
                    ["kor-travel-map-api"],
                    build=False,
                    recreate=True,
                    no_deps=True,
                    wait=True,
                    capture_output=True,
                    environment=candidate_environment,
                    mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                    redact_config=config,
                    transaction=transaction,
                    build_provenance=build_provenance,
                ):
                    raise DeploymentContractError("bootstrap Map API failed")
                self._verify_running_image_source_provenance(
                    config.map_container,
                    label="Map",
                    expected_revision=candidate_pair.map_source_revision,
                )
                result["smoke"] = run_map_ops_smoke(config)
                touched_services.update(map_dependents)
                if not self._run_up_stage(
                    result,
                    "bootstrap_map_dependents",
                    map_dependents,
                    build=False,
                    recreate=True,
                    no_deps=True,
                    wait=True,
                    capture_output=True,
                    environment=candidate_environment,
                    mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                    redact_config=config,
                    transaction=transaction,
                    build_provenance=build_provenance,
                ):
                    raise DeploymentContractError(
                        "bootstrap Map dependents failed"
                    )
                self._verify_map_runtime_source_provenance(
                    candidate_pair.map_source_revision,
                    include_api=False,
                )
                touched_services.add("pinvi-api")
                if not self._run_up_stage(
                    result,
                    "bootstrap_pinvi_api",
                    ["pinvi-api"],
                    build=False,
                    recreate=True,
                    no_deps=True,
                    wait=True,
                    capture_output=True,
                    environment=candidate_environment,
                    mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                    redact_config=config,
                    transaction=transaction,
                    build_provenance=build_provenance,
                ):
                    raise DeploymentContractError("bootstrap PinVi API failed")
                self._verify_running_image_source_provenance(
                    config.pinvi_container,
                    label="PinVi",
                    expected_revision=candidate_pair.pinvi_source_revision,
                    expected_build_environment="production",
                )
                result["pinvi_smoke"] = run_pinvi_canonical_smoke(
                    config,
                    cancel_probe_state=cancel_probe_state,
                )
                touched_services.update(pinvi_dependents)
                if not self._run_up_stage(
                    result,
                    "bootstrap_pinvi_dependents",
                    pinvi_dependents,
                    build=False,
                    recreate=False,
                    no_deps=True,
                    wait=True,
                    capture_output=True,
                    mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                    transaction=transaction,
                    build_provenance=build_provenance,
                ):
                    raise DeploymentContractError(
                        "bootstrap PinVi dependents failed"
                    )
                pair = self._inspect_current_pair(config)
                self._require_expected_source_provenance(pair, build_provenance)
                if not self._pair_matches(pair, candidate_pair):
                    raise DeploymentContractError(
                        "running C6c pair differs from pre-attested bootstrap images"
                    )
                self._require_pair_image_provenance(pair)
                verification = self._verify_active_contract(
                    config,
                    pair,
                    services,
                    cancel_probe_state=cancel_probe_state,
                    transaction=transaction,
                )
                write_pair_manifest(manifest_path, initial_pair_manifest(pair))
                result["verification"] = verification
                result["contract_generation"] = pair.contract_generation
                result["image_provenance"] = self._pair_provenance_payload(pair)
                result["deployment_state"] = "initial_v4_manifest_committed"
                result["stdout"] += (
                    f"compatible Map+PinVi image pair bootstrapped: {manifest_path}\n"
                )
                return result
            except Exception as exc:
                if not mutation_attempted:
                    raise
                self._fail_result(
                    result,
                    str(exc)
                    if isinstance(exc, DeploymentContractError)
                    else "unexpected compatible-pair capture failure",
                )
                recovery = self._cleanup_bootstrap(
                    result,
                    config,
                    initial_states,
                    touched_services,
                    transaction=transaction,
                )
                raise ComposePostMutationContractError(
                    exc,
                    recovery_attempted=True,
                    recovery_succeeded=bool(recovery.get("success")),
                    recovery_error=(
                        None
                        if recovery.get("success")
                        else str(recovery.get("error") or recovery.get("state"))
                    ),
                    restoration=recovery,
                ) from exc

    def _snapshot_service_states(
        self,
        services: list[str],
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> dict[str, str]:
        ps_result = self.run(
            ["ps", "--all", "--format", "json", *services],
            transaction=transaction,
        )
        if not ps_result["success"]:
            raise DeploymentContractError("cannot capture bootstrap service state")
        return {
            str(record["Service"]): str(record.get("State", "")).strip().lower()
            for record in self._compose_ps_records(
                str(ps_result.get("stdout", "")), allow_empty=True
            )
        }

    def _cleanup_bootstrap(
        self,
        result: dict[str, Any],
        config: C6cDeploymentConfig,
        initial_states: Mapping[str, str],
        touched_services: set[str],
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> dict[str, Any]:
        """bootstrap이 만든 서비스만 제거하고 기존 비실행 서비스는 원상 정지한다."""

        halt_ok = False
        try:
            self._halt_c6c_pair(
                result,
                config,
                "deployment_state",
                transaction=transaction,
            )
            halt_ok = result.get("deployment_state") == "halted_requires_operator"
        except Exception:
            self._fail_result(result, "bootstrap halt command raised unexpectedly")
            result["deployment_state"] = "halt_failed_requires_operator"
        protected_runtime_services = {*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE}
        created = sorted(
            service for service in touched_services if service not in initial_states
        )
        restore_stopped = sorted(
            service
            for service in touched_services - protected_runtime_services
            if service in initial_states and initial_states[service] != "running"
        )
        cleanup_ok = halt_ok
        if created:
            removal_capability = (
                _COMPATIBLE_PAIR_MUTATION_CAPABILITY
                if set(created).intersection(protected_runtime_services)
                else _MANAGED_COMPOSE_MUTATION_CAPABILITY
            )
            try:
                remove_result = self._run_frozen_recovery(
                    ["rm", "--force", "--stop", *created],
                    mutation_capability=removal_capability,
                    transaction=transaction,
                )
                self._append_stage_result(
                    result, "bootstrap_remove_created", remove_result, config
                )
                cleanup_ok = cleanup_ok and bool(remove_result["success"])
            except Exception:
                self._fail_result(
                    result, "bootstrap created-service cleanup raised unexpectedly"
                )
                cleanup_ok = False
        if restore_stopped:
            try:
                stop_result = self._run_frozen_recovery(
                    ["stop", *restore_stopped],
                    mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                    transaction=transaction,
                )
                self._append_stage_result(
                    result, "bootstrap_restore_stopped", stop_result, config
                )
                cleanup_ok = cleanup_ok and bool(stop_result["success"])
            except Exception:
                self._fail_result(
                    result, "bootstrap stopped-service restore raised unexpectedly"
                )
                cleanup_ok = False
        if not cleanup_ok:
            result["deployment_state"] = (
                "bootstrap_cleanup_failed_requires_operator"
                if halt_ok
                else "halt_failed_requires_operator"
            )
        return {
            "success": cleanup_ok,
            "state": result["deployment_state"],
            "error": None if cleanup_ok else "bootstrap cleanup failed",
        }

    def rollback_compatible_pinvi_pair(self) -> dict[str, Any]:
        """manifest pair를 Map smoke 뒤 PinVi 순서로 복원해 혼합 실행을 막는다."""

        with c6c_deployment_lock(get_c6c_deployment_lock_path()):
            transaction, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            assert_manager_mutation_allowed(
                environment=transaction.environment.effective
            )
            config = load_c6c_deployment_config_from_environment(
                transaction.environment.effective
            )
            if not config.production:
                raise DeploymentContractError(
                    "compatible pair rollback is available only in production mode"
                )
            manifest_path = transaction.manifest_path
            if manifest_path is None:
                raise DeploymentContractError(
                    "compatible-pair transaction has no manifest path"
                )
            manifest = load_pair_manifest(manifest_path)
            active_at_start = manifest.active
            rollback = manifest.rollback
            for pair in (active_at_start, rollback):
                if pair.contract_generation != config.contract_generation:
                    raise DeploymentContractError(
                        "rollback pair generation differs from the active deployment contract"
                    )
                self._require_pair_image_provenance(pair)
            active_recovery_transaction = (
                self._materialize_active_recovery_transaction_unlocked(
                    transaction,
                    config,
                    active_at_start,
                )
            )
            if not self._pair_matches(self._inspect_current_pair(config), active_at_start):
                raise DeploymentContractError(
                    "running pair differs from manifest active pair before rollback"
                )
            active_environment = self._pair_image_environment(active_at_start)
            rollback_environment = self._pair_image_environment(rollback)
            self._validate_resolved_compose_contract(
                config,
                environment_override=active_environment,
                expected_pair=active_at_start,
                transaction=transaction,
            )
            self._validate_resolved_compose_contract(
                config,
                environment_override=rollback_environment,
                expected_pair=rollback,
                transaction=transaction,
            )

            services = services_for_target("pinvi")
            self._require_services_ready(services, transaction=transaction)
            preflight_ui_smoke = self._preflight_current_map_ui_auth(
                config,
                manifest=manifest,
                transaction=transaction,
            )
            result: dict[str, Any] = {
                "success": True,
                "returncode": 0,
                "target": "pinvi-compatible-pair-rollback",
                "services": [*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE],
                "stages": [],
                "command": [],
                "stdout": "",
                "stderr": "",
                "rollback_state": "preflight_complete",
                "preflight_ui_smoke": preflight_ui_smoke,
            }
            cancel_probe_state = PinviCancelProbeState()
            try:
                verification = self._activate_pair_sequentially(
                    result,
                    config,
                    rollback,
                    services,
                    stage_prefix="rollback",
                    cancel_probe_state=cancel_probe_state,
                    transaction=transaction,
                )
                result["verification"] = verification
                result["image_provenance"] = self._pair_provenance_payload(rollback)
                write_pair_manifest(
                    manifest_path,
                    manifest_with_active_pair(manifest, rollback),
                )
                result["rollback_state"] = "active_manifest_committed"
                return result
            except Exception as exc:
                self._fail_result(
                    result,
                    str(exc)
                    if isinstance(exc, DeploymentContractError)
                    else "unexpected compatible-pair rollback failure",
                )
                recovery = self._recover_previous_pair(
                    result,
                    config,
                    active_at_start,
                    services,
                    cancel_probe_state=cancel_probe_state,
                    transaction=active_recovery_transaction,
                )
                raise ComposePostMutationContractError(
                    exc,
                    recovery_attempted=True,
                    recovery_succeeded=bool(recovery.get("success")),
                    recovery_error=(
                        None
                        if recovery.get("success")
                        else str(recovery.get("error") or recovery.get("state"))
                    ),
                    restoration=recovery,
                ) from exc

    def _inspect_current_pair(self, config: C6cDeploymentConfig) -> CompatibleImagePair:
        map_image_ids = {
            service_name: self._inspect_container_image_id(container_name)
            for service_name, container_name in _MAP_RUNTIME_CONTAINERS.items()
        }
        pinvi_image_id = self._inspect_container_image_id(config.pinvi_container)
        map_source_revision = self._inspect_image_source_revision(
            map_image_ids[_MAP_API_SERVICE],
            label="Map",
        )
        for service_name in _MAP_RUNTIME_SERVICES[1:]:
            if self._inspect_image_source_revision(
                map_image_ids[service_name],
                label=service_name,
            ) != map_source_revision:
                raise DeploymentContractError(
                    f"{service_name} running image revision differs from Map API"
                )
        return new_image_pair(
            map_image_ids[_MAP_API_SERVICE],
            pinvi_image_id,
            config.contract_generation,
            map_ui_image_id=map_image_ids[_MAP_UI_SERVICE],
            map_dagster_image_id=map_image_ids[_MAP_DAGSTER_SERVICE],
            map_dagster_daemon_image_id=map_image_ids[
                _MAP_DAGSTER_DAEMON_SERVICE
            ],
            map_source_revision=map_source_revision,
            pinvi_source_revision=self._inspect_image_source_revision(
                pinvi_image_id,
                label="PinVi",
                expected_build_environment="production",
            ),
        )

    @staticmethod
    def _inspect_container_image_id(container_name: str) -> str:
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
                "cannot inspect immutable image ID for a C6c runtime container"
            ) from exc
        if completed.returncode != 0:
            raise DeploymentContractError(
                "cannot inspect immutable image ID for a C6c runtime container"
            )
        return completed.stdout.strip()

    def _verify_running_image_source_provenance(
        self,
        container_name: str,
        *,
        label: str,
        expected_revision: str | None = None,
        expected_build_environment: str | None = None,
    ) -> str:
        image_id = self._inspect_container_image_id(container_name)
        revision = self._inspect_image_source_revision(
            image_id,
            label=label,
            expected_build_environment=expected_build_environment,
        )
        if expected_revision is not None and revision != expected_revision:
            raise DeploymentContractError(
                f"{label} running image revision differs from the clean checkout HEAD"
            )
        return revision

    def _verify_map_runtime_source_provenance(
        self,
        expected_revision: str,
        *,
        include_api: bool = True,
    ) -> None:
        services = (
            _MAP_RUNTIME_SERVICES
            if include_api
            else _MAP_RUNTIME_SERVICES[1:]
        )
        for service_name in services:
            self._verify_running_image_source_provenance(
                _MAP_RUNTIME_CONTAINERS[service_name],
                label=service_name,
                expected_revision=expected_revision,
            )

    @staticmethod
    def _require_local_image(image_id: str) -> None:
        try:
            completed = subprocess.run(
                ["docker", "image", "inspect", "--format={{.Id}}", image_id],
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise DeploymentContractError(
                "compatible pair image ID cannot be inspected locally"
            ) from exc
        if completed.returncode != 0 or completed.stdout.strip() != image_id:
            raise DeploymentContractError("compatible pair image ID is not available locally")

    @staticmethod
    def _inspect_image_reference_id(image_reference: str, *, label: str) -> str:
        try:
            completed = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format={{.Id}}",
                    image_reference,
                ],
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

    def _require_pair_image_provenance(self, pair: CompatibleImagePair) -> None:
        map_image_ids = {
            _MAP_API_SERVICE: pair.map_image_id,
            _MAP_UI_SERVICE: pair.map_ui_image_id,
            _MAP_DAGSTER_SERVICE: pair.map_dagster_image_id,
            _MAP_DAGSTER_DAEMON_SERVICE: pair.map_dagster_daemon_image_id,
        }
        for image_id in map_image_ids.values():
            self._require_local_image(image_id)
        self._require_local_image(pair.pinvi_image_id)
        map_revisions = {
            service_name: self._inspect_image_source_revision(
                image_id,
                label=service_name,
            )
            for service_name, image_id in map_image_ids.items()
        }
        pinvi_revision = self._inspect_image_source_revision(
            pair.pinvi_image_id,
            label="PinVi",
            expected_build_environment="production",
        )
        if (
            any(
                revision != pair.map_source_revision
                for revision in map_revisions.values()
            )
            or pinvi_revision != pair.pinvi_source_revision
        ):
            raise DeploymentContractError(
                "compatible pair image labels differ from manifest source provenance"
            )

    @staticmethod
    def _inspect_image_source_revision(
        image_id: str,
        *,
        label: str,
        expected_build_environment: str | None = None,
    ) -> str:
        try:
            completed = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format={{json .Config.Labels}}",
                    image_id,
                ],
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise DeploymentContractError(
                f"cannot inspect {label} image source provenance"
            ) from exc
        if completed.returncode != 0:
            raise DeploymentContractError(
                f"cannot inspect {label} image source provenance"
            )
        try:
            labels = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DeploymentContractError(
                f"{label} image provenance labels are invalid"
            ) from exc
        if not isinstance(labels, Mapping):
            raise DeploymentContractError(
                f"{label} image provenance labels are missing"
            )
        revision = labels.get("org.opencontainers.image.revision")
        if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise DeploymentContractError(
                f"{label} image source revision label is invalid"
            )
        if expected_build_environment is not None and labels.get(
            "io.pinvi.build.environment"
        ) != expected_build_environment:
            raise DeploymentContractError(
                f"{label} image build environment label is invalid"
            )
        return revision

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
            records = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            try:
                records = [json.loads(line) for line in payload.splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                raise DeploymentContractError(
                    "docker compose ps returned invalid container metadata"
                ) from exc
        valid_records = [
            record
            for record in records
            if isinstance(record, Mapping) and record.get("Name") and record.get("Service")
        ]
        if not valid_records and not allow_empty:
            raise DeploymentContractError("docker compose ps returned no managed containers")
        return valid_records

    def _require_services_ready(
        self,
        services: Sequence[str],
        *,
        transaction: ComposeTransactionSnapshot,
        frozen_recovery: bool = False,
    ) -> list[Mapping[str, Any]]:
        """필수 서비스가 실행 중이고 healthcheck가 있으면 healthy인지 확인한다."""

        expected = list(dict.fromkeys(services))
        if not expected:
            return []
        if frozen_recovery:
            ps_result = self._run_frozen_recovery(
                ["ps", "--format", "json", *expected],
                transaction=transaction,
            )
        else:
            ps_result = self.run(
                ["ps", "--format", "json", *expected],
                transaction=transaction,
            )
        if not ps_result["success"]:
            raise DeploymentContractError("cannot inspect mandatory service readiness")
        records = self._compose_ps_records(str(ps_result.get("stdout", "")))
        by_service = {str(record["Service"]): record for record in records}
        missing = [service for service in expected if service not in by_service]
        if missing:
            raise DeploymentContractError(
                "mandatory services are not running: " + ", ".join(missing)
            )
        not_ready: list[str] = []
        for service in expected:
            record = by_service[service]
            state = str(record.get("State", "")).strip().lower()
            health = str(record.get("Health", "")).strip().lower()
            if state != "running" or health not in {"", "healthy"}:
                not_ready.append(service)
        if not_ready:
            raise DeploymentContractError(
                "mandatory services are not running/healthy: " + ", ".join(not_ready)
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
