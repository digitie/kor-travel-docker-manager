import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import (
    _CACHE_TARGET_WINDOW_MUTATION_CAPABILITY,
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
    cache_target_diagnostic_attempt_log_path,
    cache_target_diagnostic_journal_path,
    cache_target_window_journal_path,
    cache_target_window_mutation_scope,
    compatible_pair_image_environment,
    complete_map_production_env_migration,
    compose_volume_graph_hash,
    initial_pair_manifest,
    inspect_c6c_image_source_revision,
    load_c6c_deployment_config_from_environment,
    load_or_create_map_production_env_migration,
    load_pair_manifest,
    manifest_with_active_pair,
    new_image_pair,
    require_local_c6c_image,
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
    verify_compatible_pair_image_provenance,
    write_pair_manifest,
)
from kor_travel_docker_manager.services.c6c_image_retention import (
    ensure_pair_references,
    reconcile_pair_references,
    require_empty_retention_namespace,
    validate_retention_namespace_is_reserved,
)
from kor_travel_docker_manager.services.cache_target_backup import (
    _COUPLED_ROLLBACK_CAPABILITY,
    _STANDALONE_RESTORE_CAPABILITY,
    STANDALONE_BACKUP_DEFAULT_KEEP_COUNT,
    STANDALONE_BACKUP_DEFAULT_KEEP_DAYS,
    DatabaseRole,
    DatabaseRuntime,
    DatabaseWriteCounter,
    PinBoundaryAuditRow,
    StandaloneBackupManifest,
    assert_cutover_backup_space_available,
    create_database_backup,
    create_manager_rollback_bundle,
    create_standalone_database_backup,
    database_runtimes_from_frozen_contract,
    gc_standalone_database_backups,
    list_standalone_database_backups,
    read_dagster_inflight_run_count,
    read_database_identity,
    read_database_inflight_count,
    read_database_schema_revision,
    read_database_write_counter,
    read_pin_boundary_audit,
    restore_database_backup,
    restore_manager_rollback_bundle,
    restore_standalone_database_backup,
    verify_manager_rollback_bundle,
)
from kor_travel_docker_manager.services.cache_target_bootstrap import (
    DEFAULT_OFF_BOOTSTRAP_ENV_NAMES,
    prepare_default_off_cache_target_bootstrap,
)
from kor_travel_docker_manager.services.cache_target_canary import (
    execute_cache_target_causal_canary,
)
from kor_travel_docker_manager.services.cache_target_contract import PINVI_SYNC_ENV
from kor_travel_docker_manager.services.cache_target_cutover import (
    CacheTargetFrozenEvidence,
    InitialCutoverReceipt,
    InitialCutoverResult,
    build_initial_cutover_receipt,
    commit_initial_cutover_receipt,
    initial_receipt_logical_sha256,
    initial_runner_compose_arguments,
    parse_initial_cutover_output,
    read_initial_cutover_receipt,
    scavenge_initial_runner_secret_bundle,
    with_initial_runner_secret_bundle,
)
from kor_travel_docker_manager.services.cache_target_diagnostic_stages import (
    diagnose_archive_structure,
    diagnose_scratch_cleanup,
    diagnose_scratch_create,
    diagnose_scratch_data_inventory,
    diagnose_scratch_restore,
    diagnose_scratch_schema_inventory,
    diagnose_source_archive,
    diagnose_source_data_inventory,
    diagnose_source_schema_inventory,
    diagnostic_scratch_database_name,
    remove_diagnostic_archive,
)
from kor_travel_docker_manager.services.cache_target_diagnostics import (
    TERMINAL_PHASES,
    CacheTargetDiagnosticIdentity,
    CacheTargetDiagnosticJournal,
    DiagnosticFailureClass,
    DiagnosticStage,
    DiagnosticStageReceipt,
    archive_cache_target_diagnostic,
    diagnostic_attempt_budget_exceeded,
    diagnostic_failure_is_reproduced,
    diagnostic_receipt_is_fresh,
    prepare_cache_target_diagnostic,
    read_cache_target_diagnostic,
    read_or_create_cache_target_diagnostic_attempt_log,
    record_diagnostic_attempt,
    retire_legacy_pre_stop_cache_target_diagnostic,
    transition_cache_target_diagnostic,
    write_cache_target_diagnostic,
    write_cache_target_diagnostic_attempt_log,
)
from kor_travel_docker_manager.services.cache_target_enable import (
    execute_cache_target_enable,
    read_canonical_env_file,
    read_enable_cutover_journal,
    replace_canonical_env_file,
)
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
    require_cache_target_production_release,
)
from kor_travel_docker_manager.services.cache_target_window import (
    FORWARD_PHASES,
    CacheTargetWindowJournal,
    DatabaseBackupReceipt,
    MapHelperOperation,
    MapHelperReceipt,
    PinBoundaryOperation,
    PinBoundaryReceipt,
    PinMigrationReceipt,
    WindowFailureClass,
    WindowPhase,
    map_final_evidence_sha256,
    map_helper_receipt_sha256,
    old_restore_is_authorized,
    parse_map_helper_receipt,
    parse_pin_boundary_receipt,
    pin_boundary_receipt_sha256,
    pin_migration_receipt_sha256,
    prepare_cache_target_window,
    read_cache_target_window,
    record_window_failure,
    transition_cache_target_window,
    validate_map_final_evidence_binding,
    write_cache_target_window,
)
from kor_travel_docker_manager.services.cache_target_writer_drain import (
    WriterDrainOwnerKind,
    WriterDrainReceipt,
    build_writer_drain_request,
    parse_writer_drain_receipt,
    writer_drain_receipt_sha256,
)
from kor_travel_docker_manager.services.cache_target_writer_fence import (
    attest_cache_target_global_writer_fence,
    cache_target_writer_environments_from_resolved_compose,
)
from kor_travel_docker_manager.services.registry import (
    get_target,
    init_steps_for_target,
    is_known_target,
    runtime_services_for_target,
    services_for_target,
    target_sequence_for_target,
)

_CACHE_TARGET_WRITER_SERVICES = frozenset(
    {
        _MAP_API_SERVICE,
        _MAP_DAGSTER_SERVICE,
        _MAP_DAGSTER_DAEMON_SERVICE,
        _PINVI_API_SERVICE,
        "pinvi-dagster",
    }
)
_CACHE_TARGET_WRITER_REGISTRY_SHA256 = (
    "526240609e2919357699b90244eb8cc8b9505f37db6c60552a98c7a37ed22d7c"
)
_CACHE_TARGET_POST_INITIAL_PHASES = frozenset(
    {
        "initial_committed",
        "sync_enabled",
        "canary_verified",
        "gc_started",
        "gc_verified",
        "final_writers_fencing",
        "final_writers_fenced",
        "map_final_verified",
        "final_boundary_verified",
        "forward_committed",
        "runtime_activated",
    }
)


def cache_target_writer_registry_sha256(names: Sequence[str]) -> str:
    ordered = tuple(sorted(names))
    if len(ordered) != len(set(ordered)) or frozenset(ordered) != (
        _CACHE_TARGET_WRITER_SERVICES
    ):
        raise DeploymentContractError(
            "cache-target writer capability registry is incomplete or unknown"
        )
    try:
        payload = b"pinvi-cache-target-writer-registry-v1\0" + b"".join(
            name.encode("ascii") + b"\0" for name in ordered
        )
    except UnicodeEncodeError as exc:
        raise DeploymentContractError(
            "cache-target writer registry identity is invalid"
        ) from exc
    digest = hashlib.sha256(payload).hexdigest()
    if digest != _CACHE_TARGET_WRITER_REGISTRY_SHA256:
        raise DeploymentContractError(
            "cache-target writer registry identity is invalid"
        )
    return digest


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


def _compatible_pair_logical_sha256(pair: CompatibleImagePair) -> str:
    payload = {
        "contract_generation": pair.contract_generation,
        "map_dagster_daemon_image_id": pair.map_dagster_daemon_image_id,
        "map_dagster_image_id": pair.map_dagster_image_id,
        "map_image_id": pair.map_image_id,
        "map_source_revision": pair.map_source_revision,
        "map_ui_image_id": pair.map_ui_image_id,
        "pinvi_image_id": pair.pinvi_image_id,
        "pinvi_source_revision": pair.pinvi_source_revision,
        "recorded_at": pair.recorded_at,
    }
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()


_DIAGNOSTIC_SMOKE_CONTRACT_SHA256 = hashlib.sha256(
    b"ktdm-cache-target-diagnostic-runtime-smoke-contract-v1\0"
).hexdigest()
_PG_TOOL_VERSION_PATTERN = re.compile(r"(\d+)")
# 설계 문서 4절: "만료 시간은 운영 정책으로 짧게 고정" — cutover gate가 요구하는
# 진단 receipt의 신선도 window다. T-049C의 abort budget(24시간, 몇 번 *재시도*를
# 허용하는지에 대한 정책)과는 목적이 다르다: 여기는 그 receipt를 실제 cutover의
# 근거로 얼마나 오래 신뢰할지를 정한다. 짧게 고정해 schema/pair/Compose가 진단
# 이후 조용히 바뀌는 창을 최소화한다.
_CUTOVER_GATE_MAX_DIAGNOSTIC_AGE_SECONDS = 1_800
# issue #115: writer 전체를 멈추기 전 Dagster daemon만 먼저 멈추고 이미 떠 있던
# run이 스스로 끝나기를 기다리는 bounded wait 상한이다. 설계 문서 5절의 60분
# per-attempt 예산보다 훨씬 짧게 잡아, drain 자체가 그 예산을 다 써버리지 않게
# 한다 — timeout에 도달하면 graceful 대기를 포기하고 정식 run-terminate API로
# 넘어간다.


def _manager_release_sha256() -> str:
    """Manager release identity(설계 문서 4절). 실제 버전 문자열은 노출하지 않고
    digest만 identity에 남긴다."""

    try:
        release = _package_version("kor-travel-docker-manager-backend")
    except PackageNotFoundError:
        release = "0"
    return hashlib.sha256(f"ktdm-manager-release-v1:{release}".encode()).hexdigest()


def _pg_tool_major_version(
    container_name: str, executable: Literal["pg_dump", "pg_restore"]
) -> int:
    try:
        completed = subprocess.run(
            ["docker", "exec", "--user", "postgres", container_name, executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            f"cache-target diagnostic {executable} version check failed"
        ) from exc
    if completed.returncode != 0:
        raise DeploymentContractError(f"cache-target diagnostic {executable} version check failed")
    match = _PG_TOOL_VERSION_PATTERN.search(completed.stdout)
    if match is None:
        raise DeploymentContractError(
            f"cache-target diagnostic {executable} version output is invalid"
        )
    return int(match.group(1))


def _cache_target_diagnostic_process_result(
    journal: CacheTargetDiagnosticJournal,
    *,
    resumed: bool,
) -> dict[str, Any]:
    return {
        "success": journal.phase == "completed",
        "returncode": 0 if journal.phase == "completed" else 1,
        "diagnostic_id": journal.diagnostic_id,
        "phase": journal.phase,
        "failure_stage": journal.failure_stage,
        "failure_class": journal.failure_class,
        "resumed": resumed,
    }


def _standalone_backup_manifest_dict(manifest: StandaloneBackupManifest) -> dict[str, Any]:
    return {
        "role": manifest.role,
        "created_at_unix": manifest.created_at_unix,
        "schema_revision": manifest.schema_revision,
        "sha256": manifest.sha256,
        "byte_size": manifest.byte_size,
        "backup_filename": manifest.backup_filename,
    }


def _initial_receipt_process_result(
    receipt: InitialCutoverReceipt,
    *,
    resumed: bool,
) -> dict[str, Any]:
    return {
        "success": True,
        "returncode": 0,
        "cutover_id": receipt.cutover_id,
        "request_id": receipt.request_id,
        "expected_restore_epoch": receipt.expected_restore_epoch,
        "count": receipt.count,
        "merkle_root": receipt.merkle_root,
        "published": receipt.published,
        "resumed": resumed,
    }


def _read_bound_cache_target_initial_receipt(
    state_directory: Path,
    journal: CacheTargetWindowJournal,
) -> InitialCutoverReceipt:
    """post-initial 단계가 journal에 commit된 exact receipt만 사용하게 한다."""

    expected_digest = journal.initial_receipt_sha256
    candidate_pair_sha256 = journal.candidate_pair_sha256
    if expected_digest is None or candidate_pair_sha256 is None:
        raise DeploymentContractError(
            "cache-target initial receipt binding is missing from the window"
        )
    receipt = read_initial_cutover_receipt(
        state_directory / "cache-target-initial-cutover-v1.json"
    )
    evidence = receipt.evidence
    if (
        initial_receipt_logical_sha256(receipt) != expected_digest
        or receipt.cutover_id != journal.cutover_id
        or receipt.expected_restore_epoch != journal.expected_restore_epoch
        or evidence.env_sha256 != journal.environment_sha256
        or evidence.raw_compose_sha256 != journal.compose_sha256
        or evidence.resolved_compose_sha256 != journal.resolved_compose_sha256
        or evidence.active_pair_sha256 != candidate_pair_sha256
        or evidence.rollback_pair_sha256 != candidate_pair_sha256
    ):
        raise DeploymentContractError(
            "cache-target initial receipt differs from the committed window"
        )
    return receipt


def _enable_journal_process_result(
    *,
    transaction_id: str,
    cutover_id: str,
    phase: str,
) -> dict[str, Any]:
    return {
        "success": phase == "committed",
        "returncode": 0 if phase == "committed" else 1,
        "transaction_id": transaction_id,
        "cutover_id": cutover_id,
        "phase": phase,
    }


def _require_cache_target_release(
    config: C6cDeploymentConfig,
    *,
    pairs: tuple[CompatibleImagePair, ...] = (),
    candidate_map_source_revision: str | None = None,
    candidate_source_revision: str | None = None,
) -> None:
    if config.cache_target is None:
        return
    require_cache_target_production_release(
        config.cache_target,
        pairs=pairs,
        candidate_map_source_revision=candidate_map_source_revision,
        candidate_source_revision=candidate_source_revision,
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
            raise DeploymentContractError(f"{env_name} must match the clean build context HEAD")
    configured_build_environment = environment.get("PINVI_BUILD_ENVIRONMENT")
    if configured_build_environment is not None and configured_build_environment != "production":
        raise DeploymentContractError("PINVI_BUILD_ENVIRONMENT must be production for C6c build")
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
            if compose_volume_graph_hash(resolved) != transaction.resolved_volume_graph_hash:
                raise ComposeCandidateContractError(
                    "active recovery transaction volume graph changed"
                )
            revalidate_candidate_system_bind_snapshots(transaction.system_bind_snapshots)
            validate_resolved_compose_image_pair(resolved, config, active_pair)
            frozen_resolved = json.loads(_serialize_resolved_compose_document(resolved))
            if not isinstance(frozen_resolved, Mapping):
                raise ComposeCandidateContractError("active recovery transaction cannot be frozen")
            return replace(
                transaction,
                resolved=frozen_resolved,
                resolved_document_hash=(_resolved_compose_document_hash(frozen_resolved)),
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
            assert_c6c_mutation_allowed(
                services,
                environment=preflight_environment.effective,
            )
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
            assert_c6c_mutation_allowed(
                services,
                environment=transaction.environment.effective,
            )
            # T-044: `assert_c6c_mutation_allowed`는 target이 Map/PinVi API 런타임이 아니면
            # production에서도 그대로 통과시킨다 — 그 target들은 개별 컨테이너
            # start/stop/config 경로로 production에서도 정상 운영되어야 하기 때문이다(의도된
            # 동작). 그러나 `ensure`는 target 전체(의존 서비스 다건)를 대상으로
            # `--build`/`--force-recreate`까지 허용하는 범용 dev 부트스트랩 경로라, target과
            # 무관하게 production에서는 전면 차단해야 한다. 지금까지는 프론트가 production
            # 빌드에서 버튼을 숨기는 것이 유일한 방어선이었는데, 이는 브라우저 번들 속성일
            # 뿐 백엔드 방어가 아니다 — dev 프론트를 production 백엔드에 붙이면 그대로
            # 실행된다(T-012 적대적 리뷰에서 확인). 이 지점은 C6c target이면 위
            # `assert_c6c_mutation_allowed`가 이미 걸러 내므로 항상 비-C6c target에서만
            # 도달한다 — compatible-pair 워크플로는 여기서 적용 대상이 아니라 메시지에
            # 넣지 않는다.
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

    def deploy_compatible_pinvi_pair(
        self,
        *,
        build: bool = False,
        recreate: bool = True,
        wait_timeout: int = _DEFAULT_C6C_WAIT_TIMEOUT_SECONDS,
        expected_alembic_head: str | None = None,
    ) -> dict[str, Any]:
        """production Map runtime+PinVi API set의 유일한 배포 mutation 진입점.

        `wait_timeout`은 각 활성화 단계의 `docker compose up --wait`가 healthy를
        기다리는 초 단위 상한이다. kor-travel-map API는 uvicorn 기동 전에
        `alembic upgrade head`를 실행하므로(issue #88), 긴 마이그레이션을 수반하는
        배포는 기본값(120초)보다 큰 값을 명시적으로 지정해야 한다 — 그렇지 않으면
        마이그레이션이 끝나기 전에 timeout으로 실패 판정되어 `_recover_previous_pair`
        rollback이 발동하고, 진행 중이던 마이그레이션 컨테이너가 뜯긴다.

        `expected_alembic_head`(issue #109)를 명시하면 candidate Map API 이미지의
        `alembic heads`를 기동/DB 접속 없이 정적으로 읽어 이 값과 다르면 mutation
        전에 fail-close한다. floating tag(`latest-main`)가 pin보다 오래 빌드된
        이미지를 가리키고 있어도(git revision provenance 검증만으로는 놓치는
        경우 — provenance는 `build=True`일 때만, 그것도 소스 checkout 기준으로
        검증되고 실제 이미지 안 migration chain의 head까지는 보지 않는다) 이 값을
        생략하지 않는 한 여기서 잡힌다. 생략하면(`None`) 기존 동작과 완전히
        동일하다 — 이 게이트는 명시적 opt-in이며, 이미 이 값이 알려진 배포에서는
        항상 지정해야 한다. `_prepare_c6c_candidate_pair`가 돌려준
        `candidate_pair.map_image_id`(빌드가 있었다면 그 결과 immutable ID)를
        검사한다 — build 전 floating tag를 검사하면 build가 그 태그를 덮어써서
        검사가 무의미해지므로, 반드시 build 뒤(또는 build가 없으면 현재 resolve된)
        exact 이미지를 검사해야 한다.

        Map API만 검사하고 PinVi는 대칭으로 검사하지 않는다: PinVi의 alembic
        migration은 이 경로의 일반 컨테이너 기동에서 자동 실행되지 않는다 —
        오직 cache-target cutover의 receipt-gated one-off runner
        (`_run_pin_candidate_oneoff`, `schema_before`/`schema_after` 검증 포함)에서만
        명시적으로 실행된다. 반면 Map API 이미지는 이 경로가 기동시키는 순간
        entrypoint가 조건 없이 `alembic upgrade head`를 실행하므로(issue #88/#109),
        위험이 비대칭이다.
        """

        _validate_c6c_wait_timeout(wait_timeout)
        if expected_alembic_head is not None:
            _validate_expected_alembic_head(expected_alembic_head)

        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
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
            _require_cache_target_release(config)
            build_provenance = (
                _derive_c6c_build_provenance(
                    transaction.environment.effective,
                    compose_path=transaction.environment.compose_path,
                )
                if build
                else None
            )
            if build_provenance is not None:
                _require_cache_target_release(
                    config,
                    candidate_map_source_revision=build_provenance.map_source_revision,
                    candidate_source_revision=build_provenance.pinvi_source_revision,
                )
            return self._ensure_production_pinvi_target(
                "pinvi",
                config=config,
                build=build,
                recreate=recreate,
                capture_output=True,
                transaction=transaction,
                build_provenance=build_provenance,
                wait_timeout=wait_timeout,
                expected_alembic_head=expected_alembic_head,
            )

    def run_cache_target_cutover(
        self,
        *,
        cutover_id: str,
        expected_restore_epoch: int,
        reason: str,
        wait_timeout: int = _DEFAULT_C6C_WAIT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """H35와 generation 7 전환을 하나의 lock/process journal로 완료한다."""

        _validate_c6c_wait_timeout(wait_timeout)
        try:
            canonical_cutover_id = str(uuid.UUID(cutover_id))
        except ValueError as exc:
            raise DeploymentContractError("cache-target cutover ID is invalid") from exc
        if canonical_cutover_id != cutover_id:
            raise DeploymentContractError(
                "cache-target cutover ID must be canonical lowercase UUID"
            )
        if expected_restore_epoch <= 0:
            raise DeploymentContractError(
                "cache-target expected restore epoch must be positive"
            )
        if not reason or reason != reason.strip() or "\n" in reason or "\r" in reason:
            raise DeploymentContractError("cache-target cutover reason is invalid")

        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            config = load_c6c_deployment_config_from_environment(
                transaction.environment.effective
            )
            contract = config.cache_target
            if not config.production or contract is None:
                raise DeploymentContractError(
                    "cache-target cutover requires the production contract"
                )
            if contract.sync_enabled not in {"false", "true"}:
                raise DeploymentContractError(
                    "cache-target cutover sync state is invalid"
                )
            _require_cache_target_release(config)
            manifest_path_text = transaction.manifest_path
            if manifest_path_text is None:
                raise DeploymentContractError(
                    "cache-target cutover transaction has no pair manifest"
                )
            manifest_path = Path(manifest_path_text)
            journal_path = cache_target_window_journal_path(
                transaction.environment.effective
            )
            try:
                journal_path.lstat()
            except FileNotFoundError:
                assert_manager_mutation_allowed(
                    environment=transaction.environment.effective
                )
                if contract.sync_enabled != "false":
                    raise DeploymentContractError(
                        "new cache-target cutover requires sync=false"
                    ) from None
                old_manifest = load_pair_manifest(manifest_path_text)
                current_pair = self._inspect_current_pair(config)
                if not self._pair_matches(current_pair, old_manifest.active):
                    raise DeploymentContractError(
                        "running old pair differs from the pre-cutover manifest"
                    ) from None
                for pair in (old_manifest.active, old_manifest.rollback):
                    self._require_pair_image_provenance(pair)
                build_provenance = _derive_c6c_build_provenance(
                    transaction.environment.effective,
                    compose_path=transaction.environment.compose_path,
                )
                _require_cache_target_release(
                    config,
                    candidate_map_source_revision=(
                        build_provenance.map_source_revision
                    ),
                    candidate_source_revision=(
                        build_provenance.pinvi_source_revision
                    ),
                )
                self._require_fresh_cache_target_diagnostic(
                    transaction=transaction,
                    config=config,
                    manifest=old_manifest,
                )
                journal = prepare_cache_target_window(
                    transaction_id=str(uuid.uuid4()),
                    cutover_id=cutover_id,
                    expected_restore_epoch=expected_restore_epoch,
                    reason=reason,
                    environment_sha256=hashlib.sha256(
                        transaction.environment.env_file_bytes
                    ).hexdigest(),
                    compose_sha256=hashlib.sha256(
                        transaction.compose_source_bytes
                    ).hexdigest(),
                    resolved_compose_sha256=transaction.resolved_document_hash,
                    old_manifest_sha256=hashlib.sha256(
                        manifest_path.read_bytes()
                    ).hexdigest(),
                )
                write_cache_target_window(journal_path, journal)
            except OSError as exc:
                raise DeploymentContractError(
                    "cache-target window journal path is unavailable"
                ) from exc
            else:
                journal = read_cache_target_window(journal_path)
                if (
                    journal.cutover_id != cutover_id
                    or journal.expected_restore_epoch != expected_restore_epoch
                    or journal.reason_sha256
                    != hashlib.sha256(reason.encode()).hexdigest()
                ):
                    raise DeploymentContractError(
                        "existing cache-target window belongs to another request"
                    )
            if journal.phase == "rolled_back":
                return self._cache_target_window_result(journal, resumed=True)
            if journal.phase == "runtime_activated":
                self._validate_cache_target_runtime_activated_terminal(
                    journal_path=journal_path,
                    journal=journal,
                    transaction=transaction,
                    config=config,
                )
                return self._cache_target_window_result(journal, resumed=True)
            with cache_target_window_mutation_scope(
                journal.transaction_id,
                capability=_CACHE_TARGET_WINDOW_MUTATION_CAPABILITY,
            ):
                assert_manager_mutation_allowed(
                    environment=transaction.environment.effective
                )
                return self._run_cache_target_window_unlocked(
                    journal_path=journal_path,
                    journal=journal,
                    transaction=transaction,
                    config=config,
                    reason=reason,
                    wait_timeout=wait_timeout,
                    lock_path=lock_snapshot.lock_path,
                )

    def bootstrap_cache_target_default_off(self) -> dict[str, Any]:
        """Manager만 production cache-target의 첫 default-off contract를 provision한다."""

        if any(env_name in os.environ for env_name in DEFAULT_OFF_BOOTSTRAP_ENV_NAMES):
            raise DeploymentContractError(
                "cache-target default-off bootstrap forbids process environment overrides"
            )
        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked()
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            assert_manager_mutation_allowed(environment=transaction.environment.effective)
            config = load_c6c_deployment_config_from_environment(
                transaction.environment.effective
            )
            if not config.production:
                raise DeploymentContractError(
                    "cache-target default-off bootstrap is available only in production mode"
                )
            if config.cache_target is not None:
                raise DeploymentContractError(
                    "cache-target default-off bootstrap requires an unconfigured contract"
                )
            bootstrap = prepare_default_off_cache_target_bootstrap(
                transaction.environment.env_file_bytes,
                base_url=config.base_url,
                expected_openapi_sha256=(
                    CACHE_TARGET_PRODUCTION_PINS.service_openapi_sha256
                ),
                expected_source_revision=(
                    CACHE_TARGET_PRODUCTION_PINS.map_functional_owner_revision
                ),
                expected_contract_generation=(
                    CACHE_TARGET_PRODUCTION_PINS.contract_generation
                ),
            )
            require_cache_target_production_release(bootstrap.contract)
            replace_canonical_env_file(
                Path(transaction.environment.env_path),
                expected_sha256=hashlib.sha256(
                    transaction.environment.env_file_bytes
                ).hexdigest(),
                replacement=bootstrap.replacement,
                **_frozen_canonical_env_owner(transaction.environment),
            )
            return {
                "success": True,
                "returncode": 0,
                "sync_enabled": bootstrap.contract.sync_enabled,
                "role_binding_sha256": bootstrap.contract.role_binding_sha256,
                "environment_sha256": hashlib.sha256(bootstrap.replacement).hexdigest(),
                "contract_generation": bootstrap.contract.expected_contract_generation,
                "service_openapi_sha256": bootstrap.contract.expected_openapi_sha256,
                "map_functional_owner_revision": bootstrap.contract.expected_source_revision,
                "map_release_revision": CACHE_TARGET_PRODUCTION_PINS.map_release_revision,
                "pinvi_release_revision": CACHE_TARGET_PRODUCTION_PINS.pinvi_release_revision,
            }

    def retire_legacy_pre_stop_cache_target_diagnostic(self) -> dict[str, Any]:
        """F1C: v1 pre-stop diagnostic 하나만 receipt-first로 퇴역한다."""

        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked()
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            assert_manager_mutation_allowed(environment=transaction.environment.effective)
            config = load_c6c_deployment_config_from_environment(
                transaction.environment.effective
            )
            if not config.production or config.cache_target is None:
                raise DeploymentContractError(
                    "legacy diagnostic retirement requires the production cache-target contract"
                )
            receipt = retire_legacy_pre_stop_cache_target_diagnostic(
                cache_target_diagnostic_journal_path(transaction.environment.effective),
                retired_at_unix=int(time.time()),
            )
            return {
                "success": True,
                "returncode": 0,
                "retired_phase": receipt.retired_phase,
                "retired_journal_sha256": receipt.retired_journal_sha256,
                "retired_at_unix": receipt.retired_at_unix,
            }

    def _validate_cache_target_runtime_activated_terminal(
        self,
        *,
        journal_path: Path,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
    ) -> None:
        """완료 journal도 current receipt/pair attestation 뒤에만 성공 재보고한다."""

        contract = config.cache_target
        manifest_path = transaction.manifest_path
        candidate_pair_sha256 = journal.candidate_pair_sha256
        if (
            journal.phase != "runtime_activated"
            or not config.production
            or contract is None
            or contract.sync_enabled != "true"
            or manifest_path is None
            or candidate_pair_sha256 is None
        ):
            raise DeploymentContractError(
                "cache-target activated terminal contract is invalid"
            )
        self._validate_resolved_compose_contract(config, transaction=transaction)
        receipt = _read_bound_cache_target_initial_receipt(
            journal_path.parent,
            journal,
        )
        enable_journal = read_enable_cutover_journal(
            journal_path.parent / "cache-target-enable-v1.json"
        )
        if (
            enable_journal.phase != "committed"
            or enable_journal.cutover_id != journal.cutover_id
            or enable_journal.window_transaction_id != journal.transaction_id
            or enable_journal.initial_receipt_sha256
            != initial_receipt_logical_sha256(receipt)
            or enable_journal.active_pair_sha256 != candidate_pair_sha256
            or enable_journal.rollback_pair_sha256 != candidate_pair_sha256
        ):
            raise DeploymentContractError(
                "cache-target activated enable evidence is foreign"
            )
        manifest = load_pair_manifest(manifest_path)
        if (
            manifest.rollback is None
            or _compatible_pair_logical_sha256(manifest.active)
            != candidate_pair_sha256
            or _compatible_pair_logical_sha256(manifest.rollback)
            != candidate_pair_sha256
        ):
            raise DeploymentContractError(
                "cache-target activated manifest is foreign"
            )
        _require_cache_target_release(
            config,
            pairs=(manifest.active, manifest.rollback),
        )
        current_pair = self._inspect_current_pair(config)
        if not self._pair_matches(current_pair, manifest.active):
            raise DeploymentContractError(
                "cache-target activated runtime differs from the manifest"
            )
        self._attest_cache_target_pair(config, manifest, transaction)

    def _run_cache_target_window_unlocked(
        self,
        *,
        journal_path: Path,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        reason: str,
        wait_timeout: int,
        lock_path: str,
    ) -> dict[str, Any]:
        state_directory = journal_path.parent
        manifest_path = Path(transaction.manifest_path or "")
        runtimes = database_runtimes_from_frozen_contract(
            resolved=transaction.resolved,
            environment=transaction.environment.effective,
        )
        if journal.phase in {
            "rollback_preparing",
            "new_runtime_stopped",
            "map_db_restored",
            "map_dagster_db_restored",
            "pinvi_db_restored",
            "manager_state_restored",
            "writers_restored",
            "old_runtime_restored",
        }:
            rolled_back = self._resume_cache_target_coupled_rollback(
                journal_path=journal_path,
                journal=journal,
                transaction=transaction,
                config=config,
                runtimes=runtimes,
                wait_timeout=wait_timeout,
            )
            return self._cache_target_window_result(rolled_back, resumed=True)
        bound_initial_receipt = (
            _read_bound_cache_target_initial_receipt(
                state_directory,
                journal,
            )
            if journal.phase in _CACHE_TARGET_POST_INITIAL_PHASES
            else None
        )
        try:
            if journal.phase == "prepared":
                journal = transition_cache_target_window(
                    journal,
                    "writers_fencing",
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "writers_fencing":
                journal = transition_cache_target_window(journal, "writers_draining")
                write_cache_target_window(journal_path, journal)
            if journal.phase == "writers_draining":
                drain_receipt = self._begin_cache_target_writer_drain(
                    owner_kind="cutover",
                    owner_id=journal.cutover_id,
                    transaction=transaction,
                )
                journal = transition_cache_target_window(
                    journal,
                    "writers_drained",
                    writer_drain_lease_id=drain_receipt.lease_id,
                    writer_drain_receipt_sha256=writer_drain_receipt_sha256(
                        drain_receipt
                    ),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "writers_drained":
                journal = transition_cache_target_window(journal, "writers_stopping")
                write_cache_target_window(journal_path, journal)
            if journal.phase == "writers_stopping":
                initial_writer_fence_sha256, _ = self._establish_cache_target_writer_fence(
                    journal=journal,
                    transaction=transaction,
                    runtimes=runtimes,
                    boundary="initial",
                )
                journal = transition_cache_target_window(
                    journal,
                    "writers_fenced",
                    initial_writer_fence_sha256=initial_writer_fence_sha256,
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "writers_fenced":
                expected_writer_fence = journal.initial_writer_fence_sha256
                if expected_writer_fence is None:
                    raise DeploymentContractError("cache-target writer fence evidence is missing")
                writer_fence_sha256, before_writes = self._establish_cache_target_writer_fence(
                    journal=journal,
                    transaction=transaction,
                    runtimes=runtimes,
                    boundary="initial",
                )
                if writer_fence_sha256 != expected_writer_fence:
                    raise DeploymentContractError(
                        "cache-target live writer fence differs on resume"
                    )
                assert_cutover_backup_space_available(
                    state_directory=state_directory,
                    runtimes=runtimes,
                )
                rollback_bundle_sha256 = create_manager_rollback_bundle(
                    state_directory=state_directory,
                    transaction_id=journal.transaction_id,
                    env_path=Path(transaction.environment.env_path),
                    manifest_path=manifest_path,
                    environment_bytes=transaction.environment.env_file_bytes,
                    manifest_bytes=manifest_path.read_bytes(),
                )
                backups = tuple(
                    create_database_backup(
                        state_directory=state_directory,
                        transaction_id=journal.transaction_id,
                        runtime=runtime,
                        writer_fence_sha256=expected_writer_fence,
                    )
                    for runtime in runtimes
                )
                after_writes = self._revalidate_cache_target_writer_fence(
                    journal=journal,
                    transaction=transaction,
                    runtimes=runtimes,
                    expected_writer_fence_sha256=expected_writer_fence,
                    boundary="initial",
                )
                if after_writes != before_writes:
                    raise DeploymentContractError(
                        "database writes occurred inside the cutover backup fence"
                    )
                journal = transition_cache_target_window(
                    journal,
                    "backups_committed",
                    rollback_bundle_sha256=rollback_bundle_sha256,
                    map_application_backup=backups[0],
                    map_dagster_backup=backups[1],
                    pinvi_backup=backups[2],
                )
                write_cache_target_window(journal_path, journal)

            candidate = self._load_or_build_window_candidate(
                journal=journal,
                transaction=transaction,
                config=config,
            )
            map_backup = journal.map_application_backup
            pin_backup = journal.pinvi_backup
            if map_backup is None or pin_backup is None:
                raise DeploymentContractError(
                    "cache-target database backup evidence is missing"
                )
            if journal.phase == "backups_committed":
                journal = transition_cache_target_window(
                    journal,
                    "candidate_built",
                    candidate_pair_sha256=_compatible_pair_logical_sha256(candidate),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "candidate_built":
                pin_preflight = self._run_pin_boundary_helper(
                    operation="preflight",
                    journal=journal,
                    transaction=transaction,
                    config=config,
                    candidate=candidate,
                    database_identity=pin_backup.database_identity,
                    prior_receipt_sha256=None,
                    canary_run_id=None,
                    expected_initial_count=0,
                )
                journal = transition_cache_target_window(
                    journal,
                    "pin_preflight_verified",
                    pin_preflight_receipt_sha256=pin_boundary_receipt_sha256(
                        pin_preflight
                    ),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "pin_preflight_verified":
                map_preflight = self._run_map_h35_helper(
                    operation="preflight",
                    journal=journal,
                    transaction=transaction,
                    config=config,
                    candidate=candidate,
                    database_identity=map_backup.database_identity,
                    prior_receipt_digest=None,
                )
                journal = transition_cache_target_window(
                    journal,
                    "map_preflight_verified",
                    last_map_receipt=map_preflight,
                    last_map_receipt_sha256=map_helper_receipt_sha256(
                        map_preflight
                    ),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "map_preflight_verified":
                map_migration = self._run_map_h35_helper(
                    operation="migrate",
                    journal=journal,
                    transaction=transaction,
                    config=config,
                    candidate=candidate,
                    database_identity=map_backup.database_identity,
                    prior_receipt_digest=journal.last_map_receipt_sha256,
                )
                journal = transition_cache_target_window(
                    journal,
                    "map_database_forwarded",
                    last_map_receipt=map_migration,
                    last_map_receipt_sha256=map_helper_receipt_sha256(
                        map_migration
                    ),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "map_database_forwarded":
                preflight_sha256 = journal.pin_preflight_receipt_sha256
                if preflight_sha256 is None:
                    raise DeploymentContractError(
                        "Pin migration preflight evidence is missing"
                    )
                pin_migration = self._run_pin_database_migration(
                    journal=journal,
                    transaction=transaction,
                    config=config,
                    candidate=candidate,
                    runtime=runtimes[2],
                    database_identity=pin_backup.database_identity,
                    prior_receipt_sha256=preflight_sha256,
                )
                journal = transition_cache_target_window(
                    journal,
                    "databases_forwarded",
                    pin_migration_receipt_sha256=(
                        pin_migration_receipt_sha256(pin_migration)
                    ),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "databases_forwarded":
                csv_receipt = self._run_map_h35_helper(
                    operation="csv5",
                    journal=journal,
                    transaction=transaction,
                    config=config,
                    candidate=candidate,
                    database_identity=map_backup.database_identity,
                    prior_receipt_digest=journal.last_map_receipt_sha256,
                )
                journal = transition_cache_target_window(
                    journal,
                    "csv_forwarded",
                    last_map_receipt=csv_receipt,
                    last_map_receipt_sha256=map_helper_receipt_sha256(csv_receipt),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "csv_forwarded":
                self._bootstrap_cache_target_generation(
                    config=config,
                    transaction=transaction,
                    candidate=candidate,
                    wait_timeout=wait_timeout,
                )
                self._restart_cache_target_auxiliary_writer(
                    transaction=transaction,
                    config=config,
                )
                journal = transition_cache_target_window(
                    journal,
                    "generation_bootstrapped",
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "generation_bootstrapped":
                # initial runner는 remote event를 만들 수 있다. 응답 유실 시 성공을
                # 추측해 old restore하지 않도록 invocation 전에 권한을 폐기한다.
                if journal.external_event_count == 0:
                    journal = replace(journal, external_event_count=1)
                    write_cache_target_window(journal_path, journal)
                self._run_cache_target_initial_cutover_unlocked(
                    transaction=transaction,
                    config=config,
                    cutover_id=journal.cutover_id,
                    expected_restore_epoch=journal.expected_restore_epoch,
                    reason=reason,
                )
                initial_receipt = read_initial_cutover_receipt(
                    state_directory / "cache-target-initial-cutover-v1.json"
                )
                journal = transition_cache_target_window(
                    journal,
                    "initial_committed",
                    initial_receipt_sha256=initial_receipt_logical_sha256(initial_receipt),
                    external_event_count=max(1, initial_receipt.published),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "initial_committed":
                bound_initial_receipt = _read_bound_cache_target_initial_receipt(
                    state_directory,
                    journal,
                )
                enabled = self._enable_cache_target_sync_unlocked(
                    transaction=transaction,
                    config=config,
                    lock_path=lock_path,
                    window_transaction_id=journal.transaction_id,
                    receipt=bound_initial_receipt,
                )
                if not enabled.get("success") or enabled.get("phase") != "committed":
                    raise DeploymentContractError(
                        "cache-target sync enable did not commit"
                    )
                journal = transition_cache_target_window(journal, "sync_enabled")
                write_cache_target_window(journal_path, journal)
            if journal.phase == "sync_enabled":
                enable_journal = read_enable_cutover_journal(
                    state_directory / "cache-target-enable-v1.json"
                )
                if enable_journal.phase != "committed":
                    raise DeploymentContractError(
                        "cache-target causal canary evidence is not committed"
                    )
                journal = transition_cache_target_window(
                    journal,
                    "canary_verified",
                    external_event_count=journal.external_event_count + 2,
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "canary_verified":
                journal = transition_cache_target_window(journal, "gc_started")
                write_cache_target_window(journal_path, journal)
            if journal.phase == "gc_started":
                current, _ = self._capture_transaction_unlocked(
                    derive_manifest_path=True,
                )
                current_config = load_c6c_deployment_config_from_environment(
                    current.environment.effective
                )
                map_gc = self._run_map_h35_helper(
                    operation="gc",
                    journal=journal,
                    transaction=current,
                    config=current_config,
                    candidate=candidate,
                    database_identity=map_backup.database_identity,
                    prior_receipt_digest=journal.last_map_receipt_sha256,
                )
                manifest = load_pair_manifest(str(manifest_path))
                self._attest_cache_target_pair(current_config, manifest, current)
                journal = transition_cache_target_window(
                    journal,
                    "gc_verified",
                    last_map_receipt=map_gc,
                    last_map_receipt_sha256=map_helper_receipt_sha256(map_gc),
                    gc_receipt_sha256=map_helper_receipt_sha256(map_gc),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "gc_verified":
                journal = transition_cache_target_window(
                    journal,
                    "final_writers_fencing",
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "final_writers_fencing":
                current, _ = self._capture_transaction_unlocked(
                    derive_manifest_path=True,
                )
                final_fence_sha256, final_counters = self._establish_cache_target_writer_fence(
                    journal=journal,
                    transaction=current,
                    runtimes=runtimes,
                    boundary="final",
                )
                journal = transition_cache_target_window(
                    journal,
                    "final_writers_fenced",
                    final_writer_fence_sha256=final_fence_sha256,
                    final_map_write_counters_sha256=(
                        self._cache_target_map_write_counters_sha256(final_counters)
                    ),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "final_writers_fenced":
                current, _ = self._capture_transaction_unlocked(
                    derive_manifest_path=True,
                )
                current_config = load_c6c_deployment_config_from_environment(
                    current.environment.effective
                )
                expected_final_fence = journal.final_writer_fence_sha256
                if expected_final_fence is None:
                    raise DeploymentContractError(
                        "cache-target final writer fence evidence is missing"
                    )
                actual_final_fence, actual_final_counters = (
                    self._establish_cache_target_writer_fence(
                    journal=journal,
                    transaction=current,
                    runtimes=runtimes,
                    boundary="final",
                    )
                )
                if actual_final_fence != expected_final_fence:
                    raise DeploymentContractError(
                        "cache-target final writer fence changed on resume"
                    )
                if (
                    self._cache_target_map_write_counters_sha256(
                        actual_final_counters
                    )
                    != journal.final_map_write_counters_sha256
                ):
                    raise DeploymentContractError(
                        "cache-target Map writes changed after final fencing"
                    )
                map_verify = self._run_map_h35_helper(
                    operation="verify",
                    journal=journal,
                    transaction=current,
                    config=current_config,
                    candidate=candidate,
                    database_identity=map_backup.database_identity,
                    prior_receipt_digest=journal.last_map_receipt_sha256,
                )
                final_evidence = map_verify.cache_target_evidence
                if final_evidence is None:
                    raise DeploymentContractError(
                        "cache-target Map final evidence is missing"
                    )
                initial_receipt = _read_bound_cache_target_initial_receipt(
                    state_directory,
                    journal,
                )
                current_contract = current_config.cache_target
                if current_contract is None:
                    raise DeploymentContractError(
                        "cache-target final contract is missing"
                    )
                validate_map_final_evidence_binding(
                    final_evidence,
                    consumer_id=current_contract.consumer_id,
                    restore_epoch=journal.expected_restore_epoch,
                    snapshot_count=initial_receipt.count,
                    snapshot_merkle_root=initial_receipt.merkle_root,
                )
                journal = transition_cache_target_window(
                    journal,
                    "map_final_verified",
                    last_map_receipt=map_verify,
                    last_map_receipt_sha256=map_helper_receipt_sha256(map_verify),
                    map_final_evidence=final_evidence,
                    map_final_evidence_sha256=map_final_evidence_sha256(
                        final_evidence
                    ),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "map_final_verified":
                current, _ = self._capture_transaction_unlocked(
                    derive_manifest_path=True,
                )
                current_config = load_c6c_deployment_config_from_environment(
                    current.environment.effective
                )
                expected_final_fence_sha256 = journal.final_writer_fence_sha256
                if expected_final_fence_sha256 is None:
                    raise DeploymentContractError(
                        "cache-target final writer fence evidence is missing"
                    )
                live_final_fence, before_finalize_counters = (
                    self._establish_cache_target_writer_fence(
                        journal=journal,
                        transaction=current,
                        runtimes=runtimes,
                        boundary="final",
                    )
                )
                if live_final_fence != expected_final_fence_sha256 or (
                    self._cache_target_map_write_counters_sha256(
                        before_finalize_counters
                    )
                    != journal.final_map_write_counters_sha256
                ):
                    raise DeploymentContractError(
                        "cache-target final evidence drifted before Pin finalize"
                    )
                initial_receipt = _read_bound_cache_target_initial_receipt(
                    state_directory,
                    journal,
                )
                enable_journal = read_enable_cutover_journal(
                    state_directory / "cache-target-enable-v1.json"
                )
                preflight_sha256 = journal.pin_preflight_receipt_sha256
                if preflight_sha256 is None:
                    raise DeploymentContractError(
                        "Pin final preflight evidence is missing"
                    )
                final_receipt = self._run_pin_boundary_helper(
                    operation="finalize",
                    journal=journal,
                    transaction=current,
                    config=current_config,
                    candidate=candidate,
                    database_identity=pin_backup.database_identity,
                    prior_receipt_sha256=preflight_sha256,
                    canary_run_id=enable_journal.transaction_id,
                    expected_initial_count=initial_receipt.count,
                )
                audit_row = read_pin_boundary_audit(
                    runtimes[2],
                    journal.transaction_id,
                )
                self._assert_cache_target_pin_audit_receipt(
                    receipt=final_receipt,
                    audit_row=audit_row,
                )
                live_after_finalize, after_finalize_counters = (
                    self._read_cache_target_writer_fence_evidence(
                        journal=journal,
                        transaction=current,
                        runtimes=runtimes,
                        ordered_writers=self._cache_target_writer_names(current),
                        boundary="final",
                    )
                )
                if live_after_finalize != expected_final_fence_sha256 or (
                    self._cache_target_map_write_counters_sha256(
                        after_finalize_counters
                    )
                    != journal.final_map_write_counters_sha256
                ):
                    raise DeploymentContractError(
                        "cache-target Map writes changed during Pin finalize"
                    )
                journal = transition_cache_target_window(
                    journal,
                    "final_boundary_verified",
                    pin_final_receipt_sha256=pin_boundary_receipt_sha256(
                        final_receipt
                    ),
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "final_boundary_verified":
                journal = transition_cache_target_window(
                    journal,
                    "forward_committed",
                )
                write_cache_target_window(journal_path, journal)
            if journal.phase == "forward_committed":
                current, _ = self._capture_transaction_unlocked(
                    derive_manifest_path=True,
                )
                current_config = load_c6c_deployment_config_from_environment(
                    current.environment.effective
                )
                if (
                    journal.writer_drain_lease_id is None
                    or journal.writer_drain_receipt_sha256 is None
                ):
                    raise DeploymentContractError(
                        "cache-target writer drain receipt is missing before runtime activation"
                    )
                restore_receipt = self._restore_cache_target_writer_drain(
                    owner_kind="cutover",
                    owner_id=journal.cutover_id,
                    transaction=current,
                    lease_id=journal.writer_drain_lease_id,
                    prior_receipt_sha256=journal.writer_drain_receipt_sha256,
                )
                self._activate_cache_target_writers(
                    transaction=current,
                    config=current_config,
                )
                manifest = load_pair_manifest(str(manifest_path))
                self._attest_cache_target_pair(current_config, manifest, current)
                reconcile_pair_references((candidate,), cwd=get_project_root())
                journal = transition_cache_target_window(
                    journal,
                    "runtime_activated",
                    writer_drain_restore_receipt_sha256=writer_drain_receipt_sha256(
                        restore_receipt
                    ),
                )
                write_cache_target_window(journal_path, journal)
            return self._cache_target_window_result(journal, resumed=False)
        except Exception as exc:
            latest = read_cache_target_window(journal_path)
            if latest.phase == "prepared":
                self._discard_prebackup_cache_target_window(journal_path)
            elif latest.phase in {
                "writers_fencing",
                "writers_draining",
                "writers_drained",
                "writers_stopping",
                "writers_fenced",
            }:
                self._unwind_prebackup_cache_target_writer_drain(
                    journal_path=journal_path,
                    journal=latest,
                    transaction=transaction,
                    config=config,
                )
            elif old_restore_is_authorized(latest):
                # 설계 문서 4절: pre-forward-boundary 실패로 처음 rollback에 들어갈
                # 때만(아직 FORWARD_PHASES인 동안만) 마지막 안전 stage/class를
                # 얼린다. 이미 rollback 도중(재시작 후 resume)이면 최초 진입 때
                # 남긴 값을 그대로 둔다 — raw exception 내용은 어디에도 남기지
                # 않고 두 개의 sealed 값(마지막 안전 phase, contract_violation
                # 여부)만 기록한다.
                if latest.phase in FORWARD_PHASES:
                    failure_class: WindowFailureClass = (
                        "contract_violation"
                        if isinstance(exc, DeploymentContractError)
                        else "unexpected_error"
                    )
                    latest = record_window_failure(latest, failure_class=failure_class)
                self._resume_cache_target_coupled_rollback(
                    journal_path=journal_path,
                    journal=latest,
                    transaction=transaction,
                    config=config,
                    runtimes=runtimes,
                    wait_timeout=wait_timeout,
                )
            raise

    def _load_or_build_window_candidate(
        self,
        *,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
    ) -> CompatibleImagePair:
        if journal.phase == "prepared":
            raise DeploymentContractError(
                "cache-target backups must commit before candidate build"
            )
        build = journal.phase == "backups_committed"
        build_provenance = (
            _derive_c6c_build_provenance(
                transaction.environment.effective,
                compose_path=transaction.environment.compose_path,
            )
            if build
            else None
        )
        candidate, _ = self._prepare_c6c_candidate_pair(
            config,
            build=build,
            build_provenance=build_provenance,
            transaction=transaction,
        )
        if (
            journal.candidate_pair_sha256 is not None
            and _compatible_pair_logical_sha256(candidate)
            != journal.candidate_pair_sha256
        ):
            raise DeploymentContractError(
                "cache-target candidate differs from the window journal"
            )
        return candidate

    @staticmethod
    def _cache_target_window_result(
        journal: CacheTargetWindowJournal,
        *,
        resumed: bool,
    ) -> dict[str, Any]:
        success = journal.phase == "runtime_activated"
        return {
            "success": success,
            "returncode": 0 if success else 1,
            "transaction_id": journal.transaction_id,
            "cutover_id": journal.cutover_id,
            "phase": journal.phase,
            "forward_boundary": journal.forward_boundary,
            "external_event_count": journal.external_event_count,
            "resumed": resumed,
        }

    def run_cache_target_initial_cutover(
        self,
        *,
        cutover_id: str,
        expected_restore_epoch: int,
        reason: str,
    ) -> dict[str, Any]:
        """frozen compatible pair에서 default-off initial runner를 한 번 실행한다."""

        try:
            canonical_cutover_id = str(uuid.UUID(cutover_id))
        except ValueError as exc:
            raise DeploymentContractError("cache-target cutover ID is invalid") from exc
        if canonical_cutover_id != cutover_id:
            raise DeploymentContractError(
                "cache-target cutover ID must be canonical lowercase UUID"
            )
        if expected_restore_epoch <= 0:
            raise DeploymentContractError(
                "cache-target expected restore epoch must be positive"
            )
        if not reason or reason != reason.strip() or "\n" in reason or "\r" in reason:
            raise DeploymentContractError("cache-target cutover reason is invalid")

        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            assert_manager_mutation_allowed(
                environment=transaction.environment.effective
            )
            config = load_c6c_deployment_config_from_environment(
                transaction.environment.effective
            )
            return self._run_cache_target_initial_cutover_unlocked(
                transaction=transaction,
                config=config,
                cutover_id=cutover_id,
                expected_restore_epoch=expected_restore_epoch,
                reason=reason,
            )

    def _run_cache_target_initial_cutover_unlocked(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        cutover_id: str,
        expected_restore_epoch: int,
        reason: str,
    ) -> dict[str, Any]:
        contract = config.cache_target
        if not config.production or contract is None:
            raise DeploymentContractError(
                "cache-target initial cutover requires the production contract"
            )
        if contract.sync_enabled != "false":
            raise DeploymentContractError(
                "cache-target initial cutover requires sync=false"
            )
        self._validate_resolved_compose_contract(
            config,
            transaction=transaction,
        )
        manifest_path = transaction.manifest_path
        if manifest_path is None:
            raise DeploymentContractError(
                "cache-target cutover transaction has no pair manifest"
            )
        manifest = load_pair_manifest(manifest_path)
        if manifest.rollback is None:
            raise DeploymentContractError(
                "cache-target cutover requires an attested rollback pair"
            )
        _require_cache_target_release(
            config,
            pairs=(manifest.active, manifest.rollback),
        )
        self._attest_cache_target_pair(config, manifest, transaction)
        evidence = CacheTargetFrozenEvidence(
            env_sha256=hashlib.sha256(
                transaction.environment.env_file_bytes
            ).hexdigest(),
            raw_compose_sha256=hashlib.sha256(
                transaction.compose_source_bytes
            ).hexdigest(),
            resolved_compose_sha256=transaction.resolved_document_hash,
            active_pair_sha256=_compatible_pair_logical_sha256(manifest.active),
            rollback_pair_sha256=_compatible_pair_logical_sha256(
                manifest.rollback
            ),
            role_binding_sha256=contract.role_binding_sha256,
            expected_openapi_sha256=contract.expected_openapi_sha256,
            expected_source_revision=contract.expected_source_revision,
            expected_contract_generation=(contract.expected_contract_generation),
        )
        state_directory = Path(manifest_path).parent
        receipt_path = state_directory / "cache-target-initial-cutover-v1.json"
        runner_name = f"ktdm-cache-target-initial-{cutover_id}"
        self._cleanup_cache_target_initial_runner(
            runner_name,
            expected_image_id=manifest.active.pinvi_image_id,
        )
        scavenge_initial_runner_secret_bundle(state_directory, cutover_id)
        if receipt_path.exists():
            receipt = read_initial_cutover_receipt(receipt_path)
            expected_reason_sha = hashlib.sha256(reason.encode()).hexdigest()
            if (
                receipt.cutover_id != cutover_id
                or receipt.expected_restore_epoch != expected_restore_epoch
                or receipt.reason_sha256 != expected_reason_sha
                or receipt.evidence != evidence
            ):
                raise DeploymentContractError(
                    "existing initial cutover receipt belongs to foreign evidence"
                )
            return _initial_receipt_process_result(receipt, resumed=True)

        def run(secret_path: Path) -> InitialCutoverResult:
            arguments = initial_runner_compose_arguments(
                secret_path=secret_path,
                cutover_id=cutover_id,
                expected_restore_epoch=expected_restore_epoch,
                reason=reason,
            )
            try:
                result = self._run_frozen_recovery(
                    arguments,
                    transaction=transaction,
                    mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                    redact_config=config,
                )
                if not result.get("success"):
                    raise DeploymentContractError(
                        "cache-target initial runner failed"
                    )
                return parse_initial_cutover_output(str(result.get("stdout", "")))
            finally:
                self._cleanup_cache_target_initial_runner(
                    runner_name,
                    expected_image_id=manifest.active.pinvi_image_id,
                )

        runner_result = with_initial_runner_secret_bundle(
            state_directory,
            cutover_id,
            contract.command_token,
            contract.consumer_token,
            contract.recovery_token,
            run,
        )
        receipt = build_initial_cutover_receipt(
            cutover_id=cutover_id,
            expected_restore_epoch=expected_restore_epoch,
            reason=reason,
            evidence=evidence,
            result=runner_result,
        )
        commit_initial_cutover_receipt(receipt_path, receipt)
        return _initial_receipt_process_result(receipt, resumed=False)

    def enable_cache_target_sync(self) -> dict[str, Any]:
        """하나의 C6c lock에서 durable sync enable 또는 rollback resume를 수행한다."""

        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            assert_manager_mutation_allowed(
                environment=transaction.environment.effective
            )
            config = load_c6c_deployment_config_from_environment(
                transaction.environment.effective
            )
            if not config.production or config.cache_target is None:
                raise DeploymentContractError(
                    "cache-target enable requires the production contract"
                )
            _require_cache_target_release(config)
            self._validate_resolved_compose_contract(
                config,
                transaction=transaction,
            )
            manifest_path = transaction.manifest_path
            if manifest_path is None:
                raise DeploymentContractError(
                    "cache-target enable transaction has no pair manifest"
                )
            state_directory = Path(manifest_path).parent
            receipt = read_initial_cutover_receipt(
                state_directory / "cache-target-initial-cutover-v1.json"
            )
            journal_path = state_directory / "cache-target-enable-v1.json"
            env_path = Path(transaction.environment.env_path).resolve(strict=False)
            try:
                journal_path.lstat()
            except FileNotFoundError:
                enabled_effective = dict(transaction.environment.effective)
                enabled_effective[PINVI_SYNC_ENV] = "true"
                enabled_snapshot = replace(
                    transaction.environment,
                    effective=enabled_effective,
                )
                enabled_candidate, _ = self._capture_transaction_unlocked(
                    environment_override={PINVI_SYNC_ENV: "true"},
                    derive_manifest_path=True,
                    environment_snapshot=enabled_snapshot,
                )
                if (
                    enabled_candidate.environment != enabled_snapshot
                    or enabled_candidate.external_inputs != transaction.external_inputs
                    or enabled_candidate.compose_source_bytes
                    != transaction.compose_source_bytes
                    or enabled_candidate.compose_source_mode
                    != transaction.compose_source_mode
                    or enabled_candidate.system_bind_snapshots
                    != transaction.system_bind_snapshots
                    or enabled_candidate.raw_volume_graph_hash
                    != transaction.raw_volume_graph_hash
                    or enabled_candidate.resolved_volume_graph_hash
                    != transaction.resolved_volume_graph_hash
                    or enabled_candidate.manifest_path != manifest_path
                ):
                    raise DeploymentContractError(
                        "cache-target enabled compose candidate drifted"
                    ) from None
                enabled_config = load_c6c_deployment_config_from_environment(enabled_effective)
                self._validate_resolved_compose_contract(
                    enabled_config,
                    transaction=enabled_candidate,
                )
                enabled_resolved_compose_sha256 = enabled_candidate.resolved_document_hash
            except OSError as exc:
                raise DeploymentContractError(
                    "cache-target enable journal path is unavailable"
                ) from exc
            else:
                enabled_resolved_compose_sha256 = read_enable_cutover_journal(
                    journal_path
                ).enabled_resolved_compose_sha256

            def capture_current(
                enabled: bool,
                *,
                attest_pair: bool,
            ) -> tuple[
                C6cDeploymentConfig,
                CompatiblePairManifest,
                ComposeTransactionSnapshot,
            ]:
                current, _ = self._capture_transaction_unlocked(
                    derive_manifest_path=True,
                )
                if c6c_state_paths(current.environment.effective)[1] != (
                    lock_snapshot.lock_path
                ):
                    raise DeploymentContractError(
                        "cache-target enable drifted outside the held global lock"
                    )
                if Path(current.environment.env_path).resolve(strict=False) != env_path:
                    raise DeploymentContractError(
                        "cache-target enable canonical env path drifted"
                    )
                assert_manager_mutation_allowed(
                    environment=current.environment.effective
                )
                current_config = load_c6c_deployment_config_from_environment(
                    current.environment.effective
                )
                current_contract = current_config.cache_target
                expected_sync = "true" if enabled else "false"
                if (
                    not current_config.production
                    or current_contract is None
                    or current_contract.sync_enabled != expected_sync
                ):
                    raise DeploymentContractError(
                        "cache-target enable canonical sync state is invalid"
                    )
                self._validate_resolved_compose_contract(
                    current_config,
                    transaction=current,
                )
                if current.manifest_path != manifest_path:
                    raise DeploymentContractError(
                        "cache-target enable pair manifest path drifted"
                    )
                current_manifest = load_pair_manifest(manifest_path)
                if current_manifest.rollback is None:
                    raise DeploymentContractError(
                        "cache-target enable requires an attested rollback pair"
                    )
                _require_cache_target_release(
                    current_config,
                    pairs=(current_manifest.active, current_manifest.rollback),
                )
                if (
                    hashlib.sha256(current.compose_source_bytes).hexdigest()
                    != receipt.evidence.raw_compose_sha256
                    or _compatible_pair_logical_sha256(current_manifest.active)
                    != receipt.evidence.active_pair_sha256
                    or _compatible_pair_logical_sha256(current_manifest.rollback)
                    != receipt.evidence.rollback_pair_sha256
                    or current_contract.role_binding_sha256
                    != receipt.evidence.role_binding_sha256
                    or current_contract.expected_openapi_sha256
                    != receipt.evidence.expected_openapi_sha256
                    or current_contract.expected_source_revision
                    != receipt.evidence.expected_source_revision
                    or current_contract.expected_contract_generation
                    != receipt.evidence.expected_contract_generation
                ):
                    raise DeploymentContractError(
                        "cache-target enable frozen evidence drifted"
                    )
                if not enabled and (
                    hashlib.sha256(current.environment.env_file_bytes).hexdigest()
                    != receipt.evidence.env_sha256
                    or current.resolved_document_hash
                    != receipt.evidence.resolved_compose_sha256
                ):
                    raise DeploymentContractError(
                        "cache-target disabled evidence differs from initial receipt"
                    )
                if enabled and (
                    current.resolved_document_hash
                    != enabled_resolved_compose_sha256
                ):
                    raise DeploymentContractError(
                        "cache-target enabled resolved compose evidence drifted"
                    )
                if attest_pair:
                    self._attest_cache_target_pair(
                        current_config,
                        current_manifest,
                        current,
                    )
                return current_config, current_manifest, current

            capture_current(
                config.cache_target.sync_enabled == "true",
                attest_pair=True,
            )

            def recreate_pinvi_api(enabled: bool) -> None:
                current_config, _current_manifest, current = capture_current(
                    enabled,
                    attest_pair=False,
                )
                result = self._run_frozen_recovery(
                    [
                        "up",
                        "-d",
                        "--no-deps",
                        "--force-recreate",
                        "--no-build",
                        "--pull",
                        "never",
                        "--wait",
                        _PINVI_API_SERVICE,
                    ],
                    transaction=current,
                    mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                    redact_config=current_config,
                )
                if not result.get("success"):
                    raise DeploymentContractError(
                        "cache-target PinVi API recreate failed"
                    )
                if not enabled:
                    self._run_cache_target_rollback_health_smoke(
                        current_config,
                        current,
                    )

            def attest(enabled: bool) -> None:
                capture_current(enabled, attest_pair=True)

            def run_canary(run_id: str) -> Mapping[str, Any]:
                current_config, current_manifest, current = capture_current(
                    True,
                    attest_pair=False,
                )
                raw_receipt = execute_cache_target_causal_canary(
                    container_name=current_config.pinvi_container,
                    run_id=run_id,
                )
                return {
                    **raw_receipt,
                    "cutover_id": receipt.cutover_id,
                    "active_pair_sha256": receipt.evidence.active_pair_sha256,
                    "contract_generation": (receipt.evidence.expected_contract_generation),
                }

            journal = execute_cache_target_enable(
                receipt=receipt,
                journal_path=journal_path,
                enabled_resolved_compose_sha256=(enabled_resolved_compose_sha256),
                read_env=lambda: read_canonical_env_file(
                    env_path,
                    **_frozen_canonical_env_owner(transaction.environment),
                ),
                replace_env=lambda expected, replacement: replace_canonical_env_file(
                    env_path,
                    expected_sha256=expected,
                    replacement=replacement,
                    **_frozen_canonical_env_owner(transaction.environment),
                ),
                attest=attest,
                recreate_pinvi_api=recreate_pinvi_api,
                causal_canary=run_canary,
            )
            return _enable_journal_process_result(
                transaction_id=journal.transaction_id,
                cutover_id=journal.cutover_id,
                phase=journal.phase,
            )

    def _enable_cache_target_sync_unlocked(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        lock_path: str,
        window_transaction_id: str,
        receipt: InitialCutoverReceipt,
    ) -> dict[str, Any]:
        if not config.production or config.cache_target is None:
            raise DeploymentContractError(
                "cache-target enable requires the production contract"
            )
        _require_cache_target_release(config)
        self._validate_resolved_compose_contract(config, transaction=transaction)
        manifest_path = transaction.manifest_path
        if manifest_path is None:
            raise DeploymentContractError(
                "cache-target enable transaction has no pair manifest"
            )
        state_directory = Path(manifest_path).parent
        journal_path = state_directory / "cache-target-enable-v1.json"
        env_path = Path(transaction.environment.env_path).resolve(strict=False)
        try:
            journal_path.lstat()
        except FileNotFoundError:
            enabled_effective = dict(transaction.environment.effective)
            enabled_effective[PINVI_SYNC_ENV] = "true"
            enabled_snapshot = replace(
                transaction.environment,
                effective=enabled_effective,
            )
            enabled_candidate, _ = self._capture_transaction_unlocked(
                environment_override={PINVI_SYNC_ENV: "true"},
                derive_manifest_path=True,
                environment_snapshot=enabled_snapshot,
            )
            if (
                enabled_candidate.environment != enabled_snapshot
                or enabled_candidate.external_inputs != transaction.external_inputs
                or enabled_candidate.compose_source_bytes
                != transaction.compose_source_bytes
                or enabled_candidate.compose_source_mode
                != transaction.compose_source_mode
                or enabled_candidate.system_bind_snapshots
                != transaction.system_bind_snapshots
                or enabled_candidate.raw_volume_graph_hash
                != transaction.raw_volume_graph_hash
                or enabled_candidate.resolved_volume_graph_hash
                != transaction.resolved_volume_graph_hash
                or enabled_candidate.manifest_path != manifest_path
            ):
                raise DeploymentContractError(
                    "cache-target enabled compose candidate drifted"
                ) from None
            enabled_config = load_c6c_deployment_config_from_environment(enabled_effective)
            self._validate_resolved_compose_contract(
                enabled_config,
                transaction=enabled_candidate,
            )
            enabled_resolved_compose_sha256 = enabled_candidate.resolved_document_hash
        except OSError as exc:
            raise DeploymentContractError(
                "cache-target enable journal path is unavailable"
            ) from exc
        else:
            enabled_resolved_compose_sha256 = read_enable_cutover_journal(
                journal_path
            ).enabled_resolved_compose_sha256

        def capture_current(
            enabled: bool,
            *,
            attest_pair: bool,
        ) -> tuple[
            C6cDeploymentConfig,
            CompatiblePairManifest,
            ComposeTransactionSnapshot,
        ]:
            current, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            if c6c_state_paths(current.environment.effective)[1] != lock_path:
                raise DeploymentContractError(
                    "cache-target enable drifted outside the held global lock"
                )
            if Path(current.environment.env_path).resolve(strict=False) != env_path:
                raise DeploymentContractError(
                    "cache-target enable canonical env path drifted"
                )
            assert_manager_mutation_allowed(
                environment=current.environment.effective
            )
            current_config = load_c6c_deployment_config_from_environment(
                current.environment.effective
            )
            current_contract = current_config.cache_target
            expected_sync = "true" if enabled else "false"
            if (
                not current_config.production
                or current_contract is None
                or current_contract.sync_enabled != expected_sync
            ):
                raise DeploymentContractError(
                    "cache-target enable canonical sync state is invalid"
                )
            self._validate_resolved_compose_contract(
                current_config,
                transaction=current,
            )
            if current.manifest_path != manifest_path:
                raise DeploymentContractError(
                    "cache-target enable pair manifest path drifted"
                )
            current_manifest = load_pair_manifest(manifest_path)
            if current_manifest.rollback is None:
                raise DeploymentContractError(
                    "cache-target enable requires an attested rollback pair"
                )
            _require_cache_target_release(
                current_config,
                pairs=(current_manifest.active, current_manifest.rollback),
            )
            if (
                hashlib.sha256(current.compose_source_bytes).hexdigest()
                != receipt.evidence.raw_compose_sha256
                or _compatible_pair_logical_sha256(current_manifest.active)
                != receipt.evidence.active_pair_sha256
                or _compatible_pair_logical_sha256(current_manifest.rollback)
                != receipt.evidence.rollback_pair_sha256
                or current_contract.role_binding_sha256
                != receipt.evidence.role_binding_sha256
                or current_contract.expected_openapi_sha256
                != receipt.evidence.expected_openapi_sha256
                or current_contract.expected_source_revision
                != receipt.evidence.expected_source_revision
                or current_contract.expected_contract_generation
                != receipt.evidence.expected_contract_generation
            ):
                raise DeploymentContractError("cache-target enable frozen evidence drifted")
            if not enabled and (
                hashlib.sha256(current.environment.env_file_bytes).hexdigest()
                != receipt.evidence.env_sha256
                or current.resolved_document_hash != receipt.evidence.resolved_compose_sha256
            ):
                raise DeploymentContractError(
                    "cache-target disabled evidence differs from initial receipt"
                )
            if enabled and current.resolved_document_hash != enabled_resolved_compose_sha256:
                raise DeploymentContractError(
                    "cache-target enabled resolved compose evidence drifted"
                )
            if attest_pair:
                self._attest_cache_target_pair(
                    current_config,
                    current_manifest,
                    current,
                )
            return current_config, current_manifest, current

        capture_current(
            config.cache_target.sync_enabled == "true",
            attest_pair=True,
        )

        def recreate_pinvi_api(enabled: bool) -> None:
            current_config, _current_manifest, current = capture_current(
                enabled,
                attest_pair=False,
            )
            result = self._run_frozen_recovery(
                [
                    "up",
                    "-d",
                    "--no-deps",
                    "--force-recreate",
                    "--no-build",
                    "--pull",
                    "never",
                    "--wait",
                    _PINVI_API_SERVICE,
                ],
                transaction=current,
                mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                redact_config=current_config,
            )
            if not result.get("success"):
                raise DeploymentContractError(
                    "cache-target PinVi API recreate failed"
                )
            if not enabled:
                self._run_cache_target_rollback_health_smoke(
                    current_config,
                    current,
                )

        def attest(enabled: bool) -> None:
            capture_current(enabled, attest_pair=True)

        def run_canary(run_id: str) -> Mapping[str, Any]:
            current_config, _current_manifest, _current = capture_current(
                True,
                attest_pair=False,
            )
            raw_receipt = execute_cache_target_causal_canary(
                container_name=current_config.pinvi_container,
                run_id=run_id,
            )
            return {
                **raw_receipt,
                "cutover_id": receipt.cutover_id,
                "active_pair_sha256": receipt.evidence.active_pair_sha256,
                "contract_generation": (receipt.evidence.expected_contract_generation),
            }

        journal = execute_cache_target_enable(
            receipt=receipt,
            journal_path=journal_path,
            enabled_resolved_compose_sha256=(enabled_resolved_compose_sha256),
            read_env=lambda: read_canonical_env_file(
                env_path,
                **_frozen_canonical_env_owner(transaction.environment),
            ),
            replace_env=lambda expected, replacement: replace_canonical_env_file(
                env_path,
                expected_sha256=expected,
                replacement=replacement,
                **_frozen_canonical_env_owner(transaction.environment),
            ),
            attest=attest,
            recreate_pinvi_api=recreate_pinvi_api,
            causal_canary=run_canary,
            window_transaction_id=window_transaction_id,
        )
        return _enable_journal_process_result(
            transaction_id=journal.transaction_id,
            cutover_id=journal.cutover_id,
            phase=journal.phase,
        )

    def create_standalone_backup(self, *, role: DatabaseRole) -> dict[str, Any]:
        """T-053: `ktdctl db-backup create`. cache-target cutover window/journal과
        완전히 분리된, 언제든 단독 호출 가능한 DB 백업이다. 성공/실패와 무관하게
        어떤 mutation도 하지 않는다(순수 `pg_dump`) — writer를 멈추지 않고,
        `.env`/manifest/candidate build를 건드리지 않는다.

        production에서는 C6c 전역 lock을 짧게(스냅샷 캡처와 백업 실행 동안만) 잡고,
        frozen resolved Compose 계약에서 파생한 `DatabaseRuntime`(container·
        database_name·owner)로만 대상을 식별한다 — role 문자열 하나로 임의 DSN을
        조립하지 않으므로 잘못된 DB를 실수로 백업할 위험이 없다.
        """

        def _run(
            transaction: ComposeTransactionSnapshot,
        ) -> StandaloneBackupManifest:
            runtimes = database_runtimes_from_frozen_contract(
                resolved=transaction.resolved,
                environment=transaction.environment.effective,
            )
            try:
                runtime = next(candidate for candidate in runtimes if candidate.role == role)
            except StopIteration as exc:
                raise DeploymentContractError(
                    "standalone database backup role is invalid"
                ) from exc
            return create_standalone_database_backup(
                backups_root=Path.home() / "backups",
                runtime=runtime,
                created_at_unix=int(time.time()),
            )

        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked()
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            manifest = _run(transaction)
        return {
            "success": True,
            "returncode": 0,
            "role": manifest.role,
            "created_at_unix": manifest.created_at_unix,
            "schema_revision": manifest.schema_revision,
            "sha256": manifest.sha256,
            "byte_size": manifest.byte_size,
            "backup_filename": manifest.backup_filename,
        }

    def list_standalone_backups(
        self,
        *,
        role: DatabaseRole | None = None,
        gc: bool = False,
        keep_count: int = STANDALONE_BACKUP_DEFAULT_KEEP_COUNT,
        keep_days: int = STANDALONE_BACKUP_DEFAULT_KEEP_DAYS,
    ) -> dict[str, Any]:
        """T-054: `ktdctl db-backup list`. T-053이 남긴 owner-only manifest를 읽는
        순수 조회 작업이다 — mutation이 없으므로 C6c lock이나 frozen Compose
        transaction이 필요 없다. `gc=True`면 조회 직후 같은 role(들)에 보존
        정책(최근 `keep_count`개는 나이 무관 보존, 나머지는 `keep_days`일 이내만
        보존)을 적용하고 지운 것·남긴 것을 모두 결과에 담는다(silent truncation
        금지)."""

        backups_root = Path.home() / "backups"
        roles: tuple[DatabaseRole, ...] = (
            (role,) if role is not None else ("map_application", "map_dagster", "pinvi")
        )
        entries: list[dict[str, Any]] = []
        warnings: list[str] = []
        gc_summaries: list[dict[str, Any]] = []
        now_unix = int(time.time())
        for candidate_role in roles:
            if gc:
                gc_result = gc_standalone_database_backups(
                    backups_root,
                    role=candidate_role,
                    now_unix=now_unix,
                    keep_count=keep_count,
                    keep_days=keep_days,
                )
                warnings.extend(gc_result.warnings)
                gc_summaries.append(
                    {
                        "role": gc_result.role,
                        "kept": [
                            _standalone_backup_manifest_dict(manifest)
                            for manifest in gc_result.kept
                        ],
                        "deleted": [
                            _standalone_backup_manifest_dict(manifest)
                            for manifest in gc_result.deleted
                        ],
                    }
                )
                entries.extend(
                    _standalone_backup_manifest_dict(manifest)
                    for manifest in gc_result.kept
                )
            else:
                listing = list_standalone_database_backups(
                    backups_root, role=candidate_role
                )
                warnings.extend(listing.warnings)
                entries.extend(
                    _standalone_backup_manifest_dict(manifest)
                    for manifest in listing.manifests
                )
        entries.sort(key=lambda item: item["created_at_unix"], reverse=True)
        result: dict[str, Any] = {
            "success": True,
            "returncode": 0,
            "backups": entries,
            "warnings": warnings,
        }
        if gc:
            result["gc"] = gc_summaries
        return result

    def restore_standalone_backup(
        self,
        *,
        role: DatabaseRole,
        backup_filename: str,
        expected_schema_revision: str,
    ) -> dict[str, Any]:
        """T-055: `ktdctl db-backup restore`. cache-target cutover window와
        완전히 분리된, 언제든 단독 호출 가능한 복구다 — 이 CLI 명령 밖에서는
        `_STANDALONE_RESTORE_CAPABILITY`를 아무도 얻지 못하므로, 실제 실행 경로는
        `--confirm`을 명시한 이 메서드 호출 하나뿐이다.

        production에서는 C6c 전역 lock을 잡고 frozen resolved Compose 계약에서
        파생한 `DatabaseRuntime`으로만 대상을 식별한다. 복구 전 대상 DB의 현재
        schema revision을 `expected_schema_revision`과 대조해 다르면 어떤
        mutation도 하지 않고 거부한다 — 실수로 엉뚱한 DB(naming drift 등)를
        덮어쓰는 것을 막는다.
        """

        def _run(
            transaction: ComposeTransactionSnapshot,
        ) -> StandaloneBackupManifest:
            runtimes = database_runtimes_from_frozen_contract(
                resolved=transaction.resolved,
                environment=transaction.environment.effective,
            )
            try:
                runtime = next(candidate for candidate in runtimes if candidate.role == role)
            except StopIteration as exc:
                raise DeploymentContractError(
                    "standalone database restore role is invalid"
                ) from exc
            return restore_standalone_database_backup(
                backups_root=Path.home() / "backups",
                runtime=runtime,
                backup_filename=backup_filename,
                expected_schema_revision=expected_schema_revision,
                capability=_STANDALONE_RESTORE_CAPABILITY,
            )

        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked()
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            manifest = _run(transaction)
        return {
            "success": True,
            "returncode": 0,
            "role": manifest.role,
            "created_at_unix": manifest.created_at_unix,
            "schema_revision": manifest.schema_revision,
            "sha256": manifest.sha256,
            "byte_size": manifest.byte_size,
            "backup_filename": manifest.backup_filename,
        }

    def run_cache_target_diagnostic(self, *, diagnostic_id: str) -> dict[str, Any]:
        """T-049C: `ktdctl cache-target diagnose`. writer fence 안에서 3-role DB
        진단을 직렬 실행하고 writer를 재기동한다.

        candidate build, migration, initial event, sync enable, `.env`/manifest
        mutation은 절대 하지 않는다 — 성공은 cutover 성공을 뜻하지 않는다(설계 문서
        1절). writer는 진단 동안만 멈추고 끝나면 성공/실패와 무관하게 항상 다시
        올린다. 같은 diagnostic ID로 재호출했을 때 journal이 이미 terminal이면 그
        결과를 재보고한다. 같은 ID의 nonterminal journal은 crash로 보고 fail-close한다.
        새 ID는 이전 journal을 lock 안에서 terminal attempt로 확정·archive한 뒤에만
        시작한다. 따라서 새 rehearsal이 기존 receipt를 삭제하거나 덮어쓰지 않는다.
        """

        try:
            canonical_diagnostic_id = str(uuid.UUID(diagnostic_id))
        except ValueError as exc:
            raise DeploymentContractError("cache-target diagnostic ID is invalid") from exc
        if canonical_diagnostic_id != diagnostic_id:
            raise DeploymentContractError(
                "cache-target diagnostic ID must be canonical lowercase UUID"
            )

        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked(derive_manifest_path=True)
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
            config = load_c6c_deployment_config_from_environment(transaction.environment.effective)
            contract = config.cache_target
            if not config.production or contract is None:
                raise DeploymentContractError(
                    "cache-target diagnostic requires the production contract"
                )
            manifest_path_text = transaction.manifest_path
            if manifest_path_text is None:
                raise DeploymentContractError(
                    "cache-target diagnostic transaction has no pair manifest"
                )
            manifest_path = Path(manifest_path_text)
            manifest = load_pair_manifest(manifest_path_text)
            if manifest.rollback is None:
                raise DeploymentContractError(
                    "cache-target diagnostic requires an attested rollback pair"
                )
            state_directory = manifest_path.parent
            journal_path = cache_target_diagnostic_journal_path(transaction.environment.effective)
            attempt_log_path = cache_target_diagnostic_attempt_log_path(
                transaction.environment.effective
            )

            try:
                journal_path.lstat()
                journal_exists = True
            except FileNotFoundError:
                journal_exists = False
            except OSError as exc:
                raise DeploymentContractError(
                    "cache-target diagnostic journal path is unavailable"
                ) from exc

            if journal_exists:
                journal = read_cache_target_diagnostic(journal_path)
                if journal.diagnostic_id == diagnostic_id:
                    if journal.phase in TERMINAL_PHASES:
                        return _cache_target_diagnostic_process_result(journal, resumed=True)
                    raise DeploymentContractError(
                        "cache-target diagnostic crashed mid-run; start a new diagnostic ID"
                    )
                assert_manager_mutation_allowed(environment=transaction.environment.effective)
                self._archive_superseded_cache_target_diagnostic(
                    journal_path=journal_path,
                    attempt_log_path=attempt_log_path,
                    journal=journal,
                    transaction=transaction,
                    config=config,
                    now_unix=int(time.time()),
                )

            assert_manager_mutation_allowed(environment=transaction.environment.effective)
            now_unix = int(time.time())
            attempt_log = read_or_create_cache_target_diagnostic_attempt_log(attempt_log_path)
            if diagnostic_attempt_budget_exceeded(attempt_log, now_unix=now_unix):
                raise DeploymentContractError("cache-target diagnostic abort budget is exhausted")
            identity = self._cache_target_diagnostic_identity(
                transaction=transaction,
                config=config,
                manifest=manifest,
            )
            journal = prepare_cache_target_diagnostic(
                diagnostic_id=diagnostic_id,
                identity=identity,
                started_at_unix=now_unix,
            )
            write_cache_target_diagnostic(journal_path, journal)
            return self._run_cache_target_diagnostic_unlocked(
                journal_path=journal_path,
                attempt_log_path=attempt_log_path,
                journal=journal,
                transaction=transaction,
                config=config,
                manifest=manifest,
                state_directory=state_directory,
            )

    def _archive_superseded_cache_target_diagnostic(
        self,
        *,
        journal_path: Path,
        attempt_log_path: Path,
        journal: CacheTargetDiagnosticJournal,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        now_unix: int,
    ) -> None:
        """새 diagnostic ID 전의 receipt를 terminal attempt로 보존·archive한다."""

        reached_writer_stop_boundary = journal.phase not in {"prepared", "writers_fencing"}
        if journal.phase not in TERMINAL_PHASES:
            if journal.phase not in {"prepared", "writers_fencing"}:
                if (
                    journal.writer_drain_lease_id is None
                    or journal.writer_drain_receipt_sha256 is None
                ):
                    if journal.phase != "writers_draining":
                        raise DeploymentContractError(
                            "cache-target diagnostic drain recovery evidence is missing"
                        )
                    drain_receipt = self._begin_cache_target_writer_drain(
                        owner_kind="diagnostic",
                        owner_id=journal.diagnostic_id,
                        transaction=transaction,
                    )
                    journal = transition_cache_target_diagnostic(
                        journal,
                        "writers_drained",
                        writer_drain_lease_id=drain_receipt.lease_id,
                        writer_drain_receipt_sha256=writer_drain_receipt_sha256(
                            drain_receipt
                        ),
                    )
                    write_cache_target_diagnostic(journal_path, journal)
                manifest_path = transaction.manifest_path
                if manifest_path is None:
                    raise DeploymentContractError(
                        "cache-target diagnostic recovery has no pair manifest"
                    )
                manifest = load_pair_manifest(manifest_path)
                if (
                    _compatible_pair_logical_sha256(manifest.active)
                    != journal.identity.active_pair_sha256
                ):
                    raise DeploymentContractError(
                        "cache-target diagnostic recovery pair differs from the "
                        "pre-stop pair"
                    )
                lease_id = journal.writer_drain_lease_id
                prior_receipt_sha256 = journal.writer_drain_receipt_sha256
                if lease_id is None or prior_receipt_sha256 is None:
                    raise DeploymentContractError(
                        "cache-target diagnostic drain recovery evidence is missing"
                    )
                restore_receipt = self._restore_cache_target_writer_drain(
                    owner_kind="diagnostic",
                    owner_id=journal.diagnostic_id,
                    transaction=transaction,
                    lease_id=lease_id,
                    prior_receipt_sha256=prior_receipt_sha256,
                )
                self._activate_cache_target_writers(
                    transaction=transaction,
                    config=config,
                )
                self._attest_cache_target_prebootstrap_pair(
                    config,
                    manifest,
                    transaction,
                )
                journal = transition_cache_target_diagnostic(
                    journal,
                    "aborted",
                    writer_drain_restore_receipt_sha256=writer_drain_receipt_sha256(
                        restore_receipt
                    ),
                )
            else:
                journal = transition_cache_target_diagnostic(journal, "aborted")
            write_cache_target_diagnostic(journal_path, journal)

        # `prepared`/`writers_fencing`은 writer가 멈추기 전의 quiescence preflight다.
        # 이 지점의 crash/거부는 DB·runtime을 바꾸지 않았으므로 24시간 내 두 번이라는
        # expensive rehearsal attempt budget을 소모시키지 않는다. stop 직전 durable
        # `writers_stopping` phase부터는 partial stop/crash 가능성이 있으므로 digest가
        # 아직 없어도 실제 rehearsal attempt로 보존한다.
        if not reached_writer_stop_boundary:
            archive_cache_target_diagnostic(journal_path, journal)
            return

        attempt_log = read_or_create_cache_target_diagnostic_attempt_log(attempt_log_path)
        matching_attempts = tuple(
            attempt
            for attempt in attempt_log.attempts
            if attempt.diagnostic_id == journal.diagnostic_id
        )
        if not matching_attempts:
            attempt_log = record_diagnostic_attempt(
                attempt_log,
                journal,
                now_unix=now_unix,
            )
            write_cache_target_diagnostic_attempt_log(attempt_log_path, attempt_log)
        elif len(matching_attempts) != 1 or (
            matching_attempts[0].started_at_unix != journal.started_at_unix
            or matching_attempts[0].phase != journal.phase
            or matching_attempts[0].failure_stage != journal.failure_stage
            or matching_attempts[0].failure_class != journal.failure_class
        ):
            raise DeploymentContractError(
                "cache-target diagnostic attempt does not match the terminal journal"
            )
        archive_cache_target_diagnostic(journal_path, journal)

    def _cache_target_diagnostic_identity(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        manifest: CompatiblePairManifest,
    ) -> CacheTargetDiagnosticIdentity:
        contract = config.cache_target
        if contract is None or manifest.rollback is None:
            raise DeploymentContractError(
                "cache-target diagnostic requires the production contract"
            )
        ordered_writers = self._cache_target_writer_names(transaction)
        runtimes = database_runtimes_from_frozen_contract(
            resolved=transaction.resolved,
            environment=transaction.environment.effective,
        )
        container_name = runtimes[0].container_name
        return CacheTargetDiagnosticIdentity(
            manager_release_sha256=_manager_release_sha256(),
            pg_dump_major_version=_pg_tool_major_version(container_name, "pg_dump"),
            pg_restore_major_version=_pg_tool_major_version(container_name, "pg_restore"),
            active_pair_sha256=_compatible_pair_logical_sha256(manifest.active),
            rollback_pair_sha256=_compatible_pair_logical_sha256(manifest.rollback),
            raw_compose_sha256=hashlib.sha256(transaction.compose_source_bytes).hexdigest(),
            resolved_compose_sha256=transaction.resolved_document_hash,
            role_binding_sha256=contract.role_binding_sha256,
            writer_registry_sha256=cache_target_writer_registry_sha256(ordered_writers),
            smoke_contract_sha256=_DIAGNOSTIC_SMOKE_CONTRACT_SHA256,
        )

    def _require_fresh_cache_target_diagnostic(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        manifest: CompatiblePairManifest,
    ) -> None:
        """설계 문서 4절: 새 forward window는 지금과 같은 input logical identity로
        만족하는 `completed`·미만료 진단 receipt 없이는 열리지 않는다. receipt가
        없거나 `failed`/non-terminal이거나 identity가 다르거나 만료됐으면 즉시
        거부한다 — 사전 진단의 backup/inventory는 여기서 재사용하지 않는다."""

        journal_path = cache_target_diagnostic_journal_path(transaction.environment.effective)
        try:
            journal_path.lstat()
        except FileNotFoundError:
            raise DeploymentContractError(
                "cache-target cutover requires a completed diagnostic receipt"
            ) from None
        except OSError as exc:
            raise DeploymentContractError(
                "cache-target diagnostic journal path is unavailable"
            ) from exc
        diagnostic_journal = read_cache_target_diagnostic(journal_path)
        if diagnostic_journal.phase != "completed":
            raise DeploymentContractError(
                "cache-target cutover requires a completed diagnostic receipt"
            )
        current_identity = self._cache_target_diagnostic_identity(
            transaction=transaction,
            config=config,
            manifest=manifest,
        )
        if not diagnostic_receipt_is_fresh(
            diagnostic_journal,
            current_identity=current_identity,
            now_unix=int(time.time()),
            max_age_seconds=_CUTOVER_GATE_MAX_DIAGNOSTIC_AGE_SECONDS,
        ):
            raise DeploymentContractError(
                "cache-target cutover requires a fresh diagnostic receipt"
            )

    def _run_cache_target_diagnostic_unlocked(
        self,
        *,
        journal_path: Path,
        attempt_log_path: Path,
        journal: CacheTargetDiagnosticJournal,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        manifest: CompatiblePairManifest,
        state_directory: Path,
    ) -> dict[str, Any]:
        ordered_writers = self._cache_target_writer_names(transaction)
        runtimes = database_runtimes_from_frozen_contract(
            resolved=transaction.resolved,
            environment=transaction.environment.effective,
        )

        journal = transition_cache_target_diagnostic(journal, "writers_fencing")
        write_cache_target_diagnostic(journal_path, journal)

        inflight_before_stop = tuple(read_database_inflight_count(runtime) for runtime in runtimes)
        if any(inflight_before_stop):
            raise DeploymentContractError(
                "cache-target diagnostic writer fence has in-flight database transactions"
            )

        # 진단은 read-mostly라 writer를 멈췄다 재기동하는 것 자체가 새 candidate를
        # 활성화하는 수단이 되면 안 된다. 재기동 직전(여기)의 exact running pair를
        # 찍어 두고, finally에서 재기동 뒤 pair가 조금이라도 달라졌으면(예: floating
        # tag가 stop~restart 사이 다른 이미지로 이미 바뀌어 있던 경우) 즉시
        # fail-close한다 — `_attest_cache_target_pair`는 manifest에 기록된 active
        # pair와만 비교하므로, manifest 자체가 이미 stale하면 이 drift를 못 잡는다.
        pre_stop_pair = self._inspect_current_pair(config)
        failure: tuple[DiagnosticStage, DiagnosticFailureClass] | None = None
        writer_drain_restore_receipt: WriterDrainReceipt | None = None
        # writer stop 전 Map이 자기 소유 schedule/sensor를 durable lease로 멈추고
        # terminal run=0 receipt를 낸다. Manager가 daemon/GraphQL을 직접 다루지
        # 않으므로 resume/retry도 exact owner ID의 Map lease로만 이어진다.
        journal = transition_cache_target_diagnostic(journal, "writers_draining")
        write_cache_target_diagnostic(journal_path, journal)
        try:
            drain_receipt = self._begin_cache_target_writer_drain(
                owner_kind="diagnostic",
                owner_id=journal.diagnostic_id,
                transaction=transaction,
            )
            journal = transition_cache_target_diagnostic(
                journal,
                "writers_drained",
                writer_drain_lease_id=drain_receipt.lease_id,
                writer_drain_receipt_sha256=writer_drain_receipt_sha256(
                    drain_receipt
                ),
            )
            write_cache_target_diagnostic(journal_path, journal)
        except DeploymentContractError:
            failure = ("writer_drain", "admin_command_failed")

        try:
            if failure is None:
                journal = transition_cache_target_diagnostic(journal, "writers_stopping")
                write_cache_target_diagnostic(journal_path, journal)
                stopped = self._run_frozen_recovery(
                    ["stop", *ordered_writers],
                    mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                    transaction=transaction,
                )
                if not stopped.get("success"):
                    raise DeploymentContractError(
                        "cache-target diagnostic writer fence stop failed"
                    )

                inflight_after_stop = tuple(
                    read_database_inflight_count(runtime) for runtime in runtimes
                )
                dagster_runs_after_stop = read_dagster_inflight_run_count(runtimes[1])
                if any(inflight_after_stop) or dagster_runs_after_stop:
                    raise DeploymentContractError(
                        "cache-target diagnostic writer fence has in-flight database "
                        "transactions after stop"
                    )

                expected_writer_environments = (
                    cache_target_writer_environments_from_resolved_compose(
                        transaction.resolved, ordered_writers
                    )
                )
                global_fence = attest_cache_target_global_writer_fence(
                    expected_stopped_writers=expected_writer_environments,
                    cwd=get_project_root(),
                )
                journal = transition_cache_target_diagnostic(
                    journal,
                    "writers_fenced",
                    writer_fence_sha256=global_fence.inventory_sha256,
                )
                write_cache_target_diagnostic(journal_path, journal)

                for runtime in runtimes:
                    receipts = self._run_cache_target_diagnostic_role(
                        runtime, journal.diagnostic_id, state_directory
                    )
                    if runtime.role == "map_application":
                        journal = transition_cache_target_diagnostic(
                            journal,
                            "map_application_checked",
                            map_application_receipts=receipts,
                        )
                    elif runtime.role == "map_dagster":
                        journal = transition_cache_target_diagnostic(
                            journal,
                            "map_dagster_checked",
                            map_dagster_receipts=receipts,
                        )
                    else:
                        journal = transition_cache_target_diagnostic(
                            journal,
                            "pinvi_checked",
                            pinvi_receipts=receipts,
                        )
                    write_cache_target_diagnostic(journal_path, journal)
                    if failure is None:
                        for receipt in receipts:
                            if receipt.status != "succeeded":
                                assert receipt.failure_class is not None
                                failure = (receipt.stage, receipt.failure_class)
                                break
        finally:
            # 설계 문서 2절: "writer를 재기동"은 성공/실패와 무관하게 항상 일어나야
            # 하고, 재기동 뒤에는 기존 pair의 exact 상태를 다시 attest해야 한다.
            # `stop` 자체가 부분 실패해도(일부 writer만 멈춘 상태) 여기서 항상
            # `up -d --wait`로 재기동을 시도하고, 그 다음 실제로 attested active
            # pair가 맞는지 재확인한다 — "재기동했으니 됐다"는 가정을 두지 않는다.
            if (
                journal.writer_drain_lease_id is None
                or journal.writer_drain_receipt_sha256 is None
            ) and journal.phase == "writers_draining":
                # begin의 process 응답 유실은 Map lease가 없었다는 증거가 아니다.
                # 동일 owner 재호출은 Map의 idempotent receipt replay이므로, restore
                # 전 exact lease/digest를 다시 얻을 수 없으면 writer를 재기동하지
                # 않고 fail-close한다.
                drain_receipt = self._begin_cache_target_writer_drain(
                    owner_kind="diagnostic",
                    owner_id=journal.diagnostic_id,
                    transaction=transaction,
                )
                journal = transition_cache_target_diagnostic(
                    journal,
                    "writers_drained",
                    writer_drain_lease_id=drain_receipt.lease_id,
                    writer_drain_receipt_sha256=writer_drain_receipt_sha256(
                        drain_receipt
                    ),
                )
                write_cache_target_diagnostic(journal_path, journal)
            if (
                journal.writer_drain_lease_id is not None
                and journal.writer_drain_receipt_sha256 is not None
            ):
                writer_drain_restore_receipt = self._restore_cache_target_writer_drain(
                    owner_kind="diagnostic",
                    owner_id=journal.diagnostic_id,
                    transaction=transaction,
                    lease_id=journal.writer_drain_lease_id,
                    prior_receipt_sha256=journal.writer_drain_receipt_sha256,
                )
            self._activate_cache_target_writers(transaction=transaction, config=config)
            if not self._pair_matches(self._inspect_current_pair(config), pre_stop_pair):
                raise DeploymentContractError(
                    "cache-target diagnostic writer restart activated a different "
                    "image pair than was running before the writer fence"
                )
            self._attest_cache_target_prebootstrap_pair(config, manifest, transaction)

        now_unix = int(time.time())
        attempt_log = read_or_create_cache_target_diagnostic_attempt_log(attempt_log_path)

        if failure is not None:
            failure_stage, failure_class = failure
            reproduced = diagnostic_failure_is_reproduced(
                attempt_log,
                now_unix=now_unix,
                failure_stage=failure_stage,
                failure_class=failure_class,
            )
            if reproduced:
                # `aborted`는 journal 계약상 failure_stage/failure_class를 싣지
                # 않는다(모델이 이를 `failed`에서만 허용한다) — attempt log가 이미
                # 같은 (stage, class) 재현을 기록하므로 journal에는 다시 담지 않고,
                # operator에게 보이는 process result에만 로컬 변수로 남긴다.
                journal = transition_cache_target_diagnostic(
                    journal,
                    "aborted",
                    writer_drain_restore_receipt_sha256=(
                        writer_drain_receipt_sha256(writer_drain_restore_receipt)
                        if writer_drain_restore_receipt is not None
                        else None
                    ),
                )
            else:
                journal = transition_cache_target_diagnostic(
                    journal,
                    "failed",
                    failure_stage=failure_stage,
                    failure_class=failure_class,
                    writer_drain_restore_receipt_sha256=(
                        writer_drain_receipt_sha256(writer_drain_restore_receipt)
                        if writer_drain_restore_receipt is not None
                        else None
                    ),
                )
            write_cache_target_diagnostic(journal_path, journal)
            attempt_log = record_diagnostic_attempt(attempt_log, journal, now_unix=now_unix)
            write_cache_target_diagnostic_attempt_log(attempt_log_path, attempt_log)
            result = _cache_target_diagnostic_process_result(journal, resumed=False)
            if reproduced:
                result["failure_stage"] = failure_stage
                result["failure_class"] = failure_class
            return result

        self._run_cache_target_rollback_health_smoke(config, transaction)
        runtime_smoke_sha256 = hashlib.sha256(
            f"ktdm-cache-target-diagnostic-runtime-smoke-v1:{journal.diagnostic_id}".encode()
        ).hexdigest()
        journal = transition_cache_target_diagnostic(
            journal, "runtime_smoke_checked", runtime_smoke_sha256=runtime_smoke_sha256
        )
        write_cache_target_diagnostic(journal_path, journal)
        journal = transition_cache_target_diagnostic(
            journal,
            "completed",
            completed_at_unix=now_unix,
            writer_drain_restore_receipt_sha256=(
                writer_drain_receipt_sha256(writer_drain_restore_receipt)
                if writer_drain_restore_receipt is not None
                else None
            ),
        )
        write_cache_target_diagnostic(journal_path, journal)
        attempt_log = record_diagnostic_attempt(attempt_log, journal, now_unix=now_unix)
        write_cache_target_diagnostic_attempt_log(attempt_log_path, attempt_log)
        return _cache_target_diagnostic_process_result(journal, resumed=False)

    def _run_cache_target_diagnostic_role(
        self,
        runtime: DatabaseRuntime,
        diagnostic_id: str,
        state_directory: Path,
    ) -> tuple[DiagnosticStageReceipt, ...]:
        archive_path = (
            state_directory / f"cache-target-diagnostic-{diagnostic_id}-{runtime.role}.dump"
        )
        scratch_runtime = replace(
            runtime,
            database_name=diagnostic_scratch_database_name(runtime, diagnostic_id),
        )
        receipts: list[DiagnosticStageReceipt] = []
        scratch_created = False
        schema_digest: str | None = None
        data_digest: str | None = None
        stages: tuple[tuple[str, Callable[[], DiagnosticStageReceipt]], ...] = (
            ("source_archive", lambda: diagnose_source_archive(runtime, archive_path)),
            (
                "source_schema_inventory",
                lambda: diagnose_source_schema_inventory(runtime, diagnostic_id),
            ),
            ("source_data_inventory", lambda: diagnose_source_data_inventory(runtime)),
            (
                "archive_structure",
                lambda: diagnose_archive_structure(runtime, archive_path),
            ),
            (
                "scratch_create",
                lambda: diagnose_scratch_create(runtime, scratch_runtime, diagnostic_id),
            ),
            (
                "scratch_restore",
                lambda: diagnose_scratch_restore(runtime, scratch_runtime, archive_path),
            ),
        )
        try:
            for stage_name, stage in stages:
                receipt = stage()
                receipts.append(receipt)
                if stage_name == "source_schema_inventory":
                    schema_digest = receipt.schema_inventory_sha256
                elif stage_name == "source_data_inventory":
                    data_digest = receipt.data_inventory_sha256
                elif stage_name == "scratch_create" and receipt.status == "succeeded":
                    scratch_created = True
                if receipt.status != "succeeded":
                    break
            else:
                assert schema_digest is not None
                scratch_schema_receipt = diagnose_scratch_schema_inventory(
                    runtime,
                    scratch_runtime,
                    expected_schema_inventory_sha256=schema_digest,
                )
                receipts.append(scratch_schema_receipt)
                if scratch_schema_receipt.status == "succeeded":
                    assert data_digest is not None
                    receipts.append(
                        diagnose_scratch_data_inventory(
                            runtime,
                            scratch_runtime,
                            expected_data_inventory_sha256=data_digest,
                        )
                    )
        finally:
            remove_diagnostic_archive(archive_path)
            if scratch_created:
                receipts.append(diagnose_scratch_cleanup(runtime, scratch_runtime))
        return tuple(receipts)

    def _attest_cache_target_pair(
        self,
        config: C6cDeploymentConfig,
        manifest: CompatiblePairManifest,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        _require_cache_target_release(
            config,
            pairs=(manifest.active, manifest.rollback),
        )
        services = [*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE]
        self._require_services_ready(
            services,
            transaction=transaction,
            frozen_recovery=True,
        )
        self._validate_resolved_compose_contract(
            config,
            expected_pair=manifest.active,
            transaction=transaction,
            frozen_recovery=True,
        )
        if not self._pair_matches(self._inspect_current_pair(config), manifest.active):
            raise DeploymentContractError(
                "cache-target running pair differs from the attested active pair"
            )
        runtime_configs = self._inspect_c6c_runtime_configs(
            config,
            services,
            transaction=transaction,
            frozen_recovery=True,
        )
        validate_runtime_secret_isolation(runtime_configs, config)

    def _attest_cache_target_prebootstrap_pair(
        self,
        config: C6cDeploymentConfig,
        manifest: CompatiblePairManifest,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        """진단 재기동 뒤 old pair가 frozen 계약과 같은지 확인한다.

        generation bootstrap 전 diagnostic은 아직 tracked release로 바뀌지 않은
        active/rollback pair를 검사한다. 따라서 이 경로에서 새 release pin을 요구하면
        fresh diagnostic receipt를 만들 수 없다. candidate bootstrap과 그 이후 runtime은
        `_attest_cache_target_pair`가 release provenance까지 계속 검증한다.
        """

        prebootstrap_transaction = self._materialize_active_recovery_transaction_unlocked(
            transaction,
            config,
            manifest.active,
        )
        services = [*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE]
        self._require_services_ready(
            services,
            transaction=prebootstrap_transaction,
            frozen_recovery=True,
        )
        self._validate_resolved_compose_contract(
            config,
            expected_pair=manifest.active,
            transaction=prebootstrap_transaction,
            frozen_recovery=True,
        )
        if not self._pair_matches(self._inspect_current_pair(config), manifest.active):
            raise DeploymentContractError(
                "cache-target pre-bootstrap running pair differs from the attested active pair"
            )
        runtime_configs = self._inspect_c6c_runtime_configs(
            config,
            services,
            transaction=prebootstrap_transaction,
            frozen_recovery=True,
        )
        validate_runtime_secret_isolation(runtime_configs, config)

    def _run_cache_target_rollback_health_smoke(
        self,
        _config: C6cDeploymentConfig,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        self._require_services_ready(
            [_PINVI_API_SERVICE],
            transaction=transaction,
            frozen_recovery=True,
        )

    def _restart_cache_target_auxiliary_writer(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
    ) -> None:
        result = self._run_frozen_recovery(
            [
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "pinvi-dagster",
            ],
            transaction=transaction,
            mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
            redact_config=config,
        )
        if not result.get("success"):
            raise DeploymentContractError(
                "cache-target PinVi Dagster restart failed"
            )

    def _activate_cache_target_writers(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
    ) -> None:
        ordered_writers = self._cache_target_writer_names(transaction)
        result = self._run_frozen_recovery(
            [
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                *ordered_writers,
            ],
            transaction=transaction,
            mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
            redact_config=config,
        )
        if not result.get("success"):
            raise DeploymentContractError(
                "cache-target final writer activation failed"
            )
        states = self._snapshot_service_states(
            list(ordered_writers),
            transaction=transaction,
        )
        if any(states.get(name) != "running" for name in ordered_writers):
            raise DeploymentContractError(
                "cache-target activated writer runtime is not fully running"
            )

    def _establish_cache_target_writer_fence(
        self,
        *,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
        boundary: Literal["initial", "final"] = "initial",
    ) -> tuple[
        str,
        tuple[DatabaseWriteCounter, DatabaseWriteCounter, DatabaseWriteCounter],
    ]:
        ordered_writers = self._cache_target_writer_names(transaction)
        if boundary == "initial":
            inflight_before_stop = tuple(
                read_database_inflight_count(runtime) for runtime in runtimes
            )
            dagster_runs_before_stop = read_dagster_inflight_run_count(runtimes[1])
            if any(inflight_before_stop) or dagster_runs_before_stop:
                raise DeploymentContractError(
                    "cache-target writer fence has in-flight database transactions"
                )
        stopped = self._run_frozen_recovery(
            ["stop", *ordered_writers],
            mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
            transaction=transaction,
        )
        if not stopped.get("success"):
            raise DeploymentContractError("cache-target writer fence stop failed")
        return self._read_cache_target_writer_fence_evidence(
            journal=journal,
            transaction=transaction,
            runtimes=runtimes,
            ordered_writers=ordered_writers,
            boundary=boundary,
        )

    @staticmethod
    def _cache_target_writer_names(
        transaction: ComposeTransactionSnapshot,
    ) -> tuple[str, ...]:
        services = transaction.resolved.get("services")
        if not isinstance(services, Mapping):
            raise DeploymentContractError("cutover resolved services are invalid")
        database_environment_names = {
            "KOR_TRAVEL_MAP_PG_DSN",
            "KOR_TRAVEL_MAP_DAGSTER_PG_URL",
            "PINVI_DATABASE_URL",
        }
        discovered_writers: set[str] = set()
        for service_name, service in services.items():
            environment = service.get("environment") if isinstance(service, Mapping) else None
            if (
                isinstance(service_name, str)
                and isinstance(environment, Mapping)
                and database_environment_names.intersection(environment)
            ):
                discovered_writers.add(service_name)
        ordered_writers = tuple(sorted(discovered_writers))
        cache_target_writer_registry_sha256(ordered_writers)
        return ordered_writers

    def _run_map_writer_drain(
        self,
        *,
        operation: Literal["begin", "attest", "restore"],
        owner_kind: WriterDrainOwnerKind,
        owner_id: str,
        transaction: ComposeTransactionSnapshot,
        lease_id: str | None = None,
        prior_receipt_sha256: str | None = None,
    ) -> WriterDrainReceipt:
        """동결 Compose의 Map API image에서만 private drain command를 실행한다."""

        request = build_writer_drain_request(
            operation=operation,
            owner_kind=owner_kind,
            owner_id=owner_id,
            lease_id=lease_id,
            prior_receipt_sha256=prior_receipt_sha256,
        )
        expected_image_id = self._map_api_image_id(transaction)
        runner_name = f"ktdm-writer-drain-{owner_id}-{operation}"
        self._cleanup_map_h35_runner(runner_name, expected_image_id=expected_image_id)
        descriptor: int | None = None
        try:
            descriptor = _create_frozen_compose_descriptor("ktdm-writer-drain-compose")
            os.write(
                descriptor,
                json.dumps(
                    transaction.resolved,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode(),
            )
            os.lseek(descriptor, 0, os.SEEK_SET)
            command = [
                "docker",
                "compose",
                "--progress",
                "quiet",
                "--env-file",
                "/dev/null",
                "--project-directory",
                str(Path(transaction.environment.compose_path).parent),
                "-f",
                f"/proc/self/fd/{descriptor}",
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--name",
                runner_name,
                "--entrypoint",
                "python",
                _MAP_API_SERVICE,
                "-m",
                "kortravelmap.api.writer_drain_command",
            ]
            completed = subprocess.run(
                command,
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
                env=dict(transaction.environment.effective),
                pass_fds=(descriptor,),
                input=json.dumps(
                    request,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
                timeout=600,
            )
            if completed.returncode != 0:
                raise DeploymentContractError(
                    f"Map writer drain {operation} command failed"
                )
            return parse_writer_drain_receipt(
                stdout=completed.stdout,
                stderr=completed.stderr,
                request=request,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                f"Map writer drain {operation} command could not run"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            self._cleanup_map_h35_runner(runner_name, expected_image_id=expected_image_id)

    def _begin_cache_target_writer_drain(
        self,
        *,
        owner_kind: WriterDrainOwnerKind,
        owner_id: str,
        transaction: ComposeTransactionSnapshot,
    ) -> WriterDrainReceipt:
        begin = self._run_map_writer_drain(
            operation="begin",
            owner_kind=owner_kind,
            owner_id=owner_id,
            transaction=transaction,
        )
        return self._run_map_writer_drain(
            operation="attest",
            owner_kind=owner_kind,
            owner_id=owner_id,
            transaction=transaction,
            lease_id=begin.lease_id,
            prior_receipt_sha256=writer_drain_receipt_sha256(begin),
        )

    def _restore_cache_target_writer_drain(
        self,
        *,
        owner_kind: WriterDrainOwnerKind,
        owner_id: str,
        transaction: ComposeTransactionSnapshot,
        lease_id: str,
        prior_receipt_sha256: str,
    ) -> WriterDrainReceipt:
        self._start_cache_target_drain_control_webserver(transaction=transaction)
        return self._run_map_writer_drain(
            operation="restore",
            owner_kind=owner_kind,
            owner_id=owner_id,
            transaction=transaction,
            lease_id=lease_id,
            prior_receipt_sha256=prior_receipt_sha256,
        )

    def _start_cache_target_drain_control_webserver(
        self, *, transaction: ComposeTransactionSnapshot
    ) -> None:
        result = self._run_frozen_recovery(
            [
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                _MAP_DAGSTER_SERVICE,
            ],
            transaction=transaction,
            mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
        )
        if not result.get("success"):
            raise DeploymentContractError(
                "cache-target writer drain control webserver start failed"
            )

    def _unwind_prebackup_cache_target_writer_drain(
        self,
        *,
        journal_path: Path,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
    ) -> None:
        """backup 전 실패는 DB rollback 없이 Map lease만 exact 복구한다."""

        if (
            journal.writer_drain_lease_id is None
            or journal.writer_drain_receipt_sha256 is None
        ):
            # begin command 응답이 유실됐어도 owner ID는 고정이다. Map은 동일 owner의
            # lease/receipt를 재생하므로 새 실행을 만들지 않고 recovery evidence를
            # 다시 얻을 수 있다.
            drain_receipt = self._begin_cache_target_writer_drain(
                owner_kind="cutover",
                owner_id=journal.cutover_id,
                transaction=transaction,
            )
            journal = replace(
                journal,
                writer_drain_lease_id=drain_receipt.lease_id,
                writer_drain_receipt_sha256=writer_drain_receipt_sha256(
                    drain_receipt
                ),
            )
            write_cache_target_window(journal_path, journal)
        lease_id = journal.writer_drain_lease_id
        prior_receipt_sha256 = journal.writer_drain_receipt_sha256
        if lease_id is None or prior_receipt_sha256 is None:
            raise DeploymentContractError(
                "pre-backup writer drain recovery evidence is missing"
            )
        self._restore_cache_target_writer_drain(
            owner_kind="cutover",
            owner_id=journal.cutover_id,
            transaction=transaction,
            lease_id=lease_id,
            prior_receipt_sha256=prior_receipt_sha256,
        )
        self._activate_cache_target_writers(transaction=transaction, config=config)
        manifest_path = transaction.manifest_path
        if manifest_path is None:
            raise DeploymentContractError(
                "pre-backup writer recovery has no compatible-pair manifest"
            )
        self._attest_cache_target_prebootstrap_pair(
            config,
            load_pair_manifest(manifest_path),
            transaction,
        )
        self._discard_prebackup_cache_target_window(journal_path)

    @staticmethod
    def _discard_prebackup_cache_target_window(journal_path: Path) -> None:
        try:
            journal_path.unlink()
            directory_fd = os.open(journal_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise DeploymentContractError(
                "pre-backup cache-target window cleanup failed"
            ) from exc

    def _map_api_image_id(self, transaction: ComposeTransactionSnapshot) -> str:
        services = transaction.resolved.get("services")
        if not isinstance(services, Mapping):
            raise DeploymentContractError("cutover resolved services are invalid")
        service = services.get(_MAP_API_SERVICE)
        image = service.get("image") if isinstance(service, Mapping) else None
        if not isinstance(image, str) or not image:
            raise DeploymentContractError("cache-target Map API image is missing")
        return self._inspect_image_reference_id(image, label="Map API")

    def _read_cache_target_writer_fence_evidence(
        self,
        *,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
        ordered_writers: tuple[str, ...],
        boundary: Literal["initial", "final"] = "initial",
    ) -> tuple[
        str,
        tuple[DatabaseWriteCounter, DatabaseWriteCounter, DatabaseWriteCounter],
    ]:
        states = self._snapshot_service_states(
            list(ordered_writers),
            transaction=transaction,
        )
        if any(states.get(name) != "exited" for name in ordered_writers):
            raise DeploymentContractError(
                "cache-target writer fence does not contain five stopped runtimes"
            )
        expected_writer_environments = cache_target_writer_environments_from_resolved_compose(
            transaction.resolved,
            ordered_writers,
        )
        global_fence = attest_cache_target_global_writer_fence(
            expected_stopped_writers=expected_writer_environments,
            cwd=get_project_root(),
        )
        inflight_after_stop = tuple(read_database_inflight_count(runtime) for runtime in runtimes)
        dagster_runs_after_stop = read_dagster_inflight_run_count(runtimes[1])
        if any(inflight_after_stop) or dagster_runs_after_stop:
            raise DeploymentContractError(
                "cache-target writer fence retained in-flight database transactions"
            )
        counters = (
            read_database_write_counter(runtimes[0]),
            read_database_write_counter(runtimes[1]),
            read_database_write_counter(runtimes[2]),
        )
        evidence: dict[str, Any] = {
            "transaction_id": journal.transaction_id,
            "boundary": boundary,
            "environment_sha256": journal.environment_sha256,
            "compose_sha256": journal.compose_sha256,
            "writer_registry_sha256": cache_target_writer_registry_sha256(
                ordered_writers
            ),
            "global_writer_fence_contract": global_fence.contract_version,
            "global_writer_inventory_sha256": global_fence.inventory_sha256,
            "global_writer_protected_target_count": (
                global_fence.protected_target_count
            ),
            "global_writer_stopped_count": (
                global_fence.expected_stopped_writer_count
            ),
            "writers": {name: states.get(name, "absent") for name in ordered_writers},
            "inflight_transactions": list(inflight_after_stop),
            "inflight_dagster_runs": dagster_runs_after_stop,
        }
        if boundary == "initial":
            evidence["write_counters"] = [
                {
                    "inserted": counter.inserted,
                    "updated": counter.updated,
                    "deleted": counter.deleted,
                    "stats_reset_identity": counter.stats_reset_identity,
                }
                for counter in counters
            ]
        payload = json.dumps(
            evidence,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest(), counters

    def _revalidate_cache_target_writer_fence(
        self,
        *,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
        expected_writer_fence_sha256: str,
        boundary: Literal["initial", "final"] = "initial",
    ) -> tuple[DatabaseWriteCounter, DatabaseWriteCounter, DatabaseWriteCounter]:
        ordered_writers = self._cache_target_writer_names(transaction)
        actual_fence, counters = self._read_cache_target_writer_fence_evidence(
            journal=journal,
            transaction=transaction,
            runtimes=runtimes,
            ordered_writers=ordered_writers,
            boundary=boundary,
        )
        if actual_fence != expected_writer_fence_sha256:
            raise DeploymentContractError(
                "cache-target writer fence changed during backup"
            )
        return counters

    @staticmethod
    def _cache_target_map_write_counters_sha256(
        counters: tuple[
            DatabaseWriteCounter,
            DatabaseWriteCounter,
            DatabaseWriteCounter,
        ],
    ) -> str:
        payload = [
            {
                "inserted": counter.inserted,
                "updated": counter.updated,
                "deleted": counter.deleted,
                "stats_reset_identity": counter.stats_reset_identity,
            }
            for counter in counters[:2]
        ]
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def _run_map_h35_helper(
        self,
        *,
        operation: MapHelperOperation,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        candidate: CompatibleImagePair,
        database_identity: str,
        prior_receipt_digest: str | None,
    ) -> MapHelperReceipt:
        prior_receipt = journal.last_map_receipt
        actual_prior_digest = (
            map_helper_receipt_sha256(prior_receipt)
            if prior_receipt is not None
            else None
        )
        if actual_prior_digest != prior_receipt_digest:
            raise DeploymentContractError(
                "Map H35 prior receipt payload differs from its digest"
            )
        request: dict[str, Any] = {
            "contract_version": "h35-map/v1",
            "operation": operation,
            "transaction_id": journal.transaction_id,
            "source_revision": candidate.map_source_revision,
            "database_identity": database_identity,
            "prior_receipt": (
                asdict(prior_receipt) if prior_receipt is not None else None
            ),
            "prior_receipt_digest": prior_receipt_digest,
        }
        candidate_transaction = self._materialize_active_recovery_transaction_unlocked(
            transaction,
            config,
            candidate,
        )
        runner_name = f"ktdm-h35-{journal.transaction_id}-{operation}"
        self._cleanup_map_h35_runner(
            runner_name,
            expected_image_id=candidate.map_image_id,
        )
        descriptor: int | None = None
        try:
            descriptor = _create_frozen_compose_descriptor("ktdm-h35-compose")
            os.write(
                descriptor,
                json.dumps(
                    candidate_transaction.resolved,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode(),
            )
            os.lseek(descriptor, 0, os.SEEK_SET)
            command = [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "--project-directory",
                str(Path(transaction.environment.compose_path).parent),
                "-f",
                f"/proc/self/fd/{descriptor}",
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--name",
                runner_name,
                "--entrypoint",
                "python",
                _MAP_API_SERVICE,
                "scripts/h35/h35_cutover.py",
                operation,
            ]
            completed = subprocess.run(
                command,
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
                env=dict(transaction.environment.effective),
                pass_fds=(descriptor,),
                input=json.dumps(
                    request,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
                timeout=3600,
            )
            if completed.returncode != 0:
                raise DeploymentContractError(f"Map H35 {operation} helper failed")
            return parse_map_helper_receipt(
                stdout=completed.stdout,
                stderr=completed.stderr,
                operation=operation,
                transaction_id=journal.transaction_id,
                source_revision=candidate.map_source_revision,
                database_identity=database_identity,
                request=request,
                prior_receipt_digest=prior_receipt_digest,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError(
                f"Map H35 {operation} helper could not run"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            self._cleanup_map_h35_runner(
                runner_name,
                expected_image_id=candidate.map_image_id,
            )

    def _bootstrap_cache_target_generation(
        self,
        *,
        config: C6cDeploymentConfig,
        transaction: ComposeTransactionSnapshot,
        candidate: CompatibleImagePair,
        wait_timeout: int,
    ) -> None:
        if config.cache_target is None or config.cache_target.sync_enabled != "false":
            raise DeploymentContractError(
                "generation bootstrap requires the sync=false contract"
            )
        manifest_path = transaction.manifest_path
        if manifest_path is None:
            raise DeploymentContractError(
                "generation bootstrap has no compatible-pair manifest"
            )
        old_manifest = load_pair_manifest(manifest_path)
        if not self._pair_matches(self._inspect_current_pair(config), old_manifest.active):
            raise DeploymentContractError(
                "generation bootstrap running old pair drifted"
            )
        _require_cache_target_release(
            config,
            candidate_map_source_revision=candidate.map_source_revision,
            candidate_source_revision=candidate.pinvi_source_revision,
        )
        ensure_pair_references(
            (old_manifest.active, old_manifest.rollback, candidate),
            cwd=get_project_root(),
        )
        result: dict[str, Any] = {
            "success": True,
            "returncode": 0,
            "stages": [],
            "command": [],
            "stdout": "",
            "stderr": "",
        }
        candidate_transaction = self._materialize_active_recovery_transaction_unlocked(
            transaction,
            config,
            candidate,
        )
        self._activate_pair_sequentially(
            result,
            config,
            candidate,
            [*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE],
            stage_prefix="generation_bootstrap",
            cancel_probe_state=PinviCancelProbeState(),
            transaction=transaction,
            wait_timeout=wait_timeout,
        )
        candidate_manifest = initial_pair_manifest(candidate)
        self._attest_cache_target_pair(
            config,
            candidate_manifest,
            candidate_transaction,
        )
        write_pair_manifest(manifest_path, candidate_manifest)
        reconcile_pair_references(
            (old_manifest.active, old_manifest.rollback, candidate),
            cwd=get_project_root(),
        )

    def _run_pin_boundary_helper(
        self,
        *,
        operation: PinBoundaryOperation,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        candidate: CompatibleImagePair,
        database_identity: str,
        prior_receipt_sha256: str | None,
        canary_run_id: str | None,
        expected_initial_count: int,
    ) -> PinBoundaryReceipt:
        initial_fence_sha256 = journal.initial_writer_fence_sha256
        if initial_fence_sha256 is None:
            raise DeploymentContractError("Pin initial writer fence is missing")
        final_fence_sha256 = journal.final_writer_fence_sha256 if operation == "finalize" else None
        final_evidence = journal.map_final_evidence if operation == "finalize" else None
        final_evidence_sha256 = (
            journal.map_final_evidence_sha256 if operation == "finalize" else None
        )
        if operation == "finalize" and (
            final_fence_sha256 is None or final_evidence is None or final_evidence_sha256 is None
        ):
            raise DeploymentContractError("Pin final boundary evidence is missing")
        request: dict[str, Any] = {
            "contract_version": "pinvi-cache-target-final-boundary/v1",
            "operation": operation,
            "transaction_id": journal.transaction_id,
            "cutover_id": journal.cutover_id,
            "source_revision": candidate.pinvi_source_revision,
            "database_identity": database_identity,
            "writer_registry_sha256": _CACHE_TARGET_WRITER_REGISTRY_SHA256,
            "initial_writer_fence_sha256": initial_fence_sha256,
            "final_writer_fence_sha256": final_fence_sha256,
            "prior_receipt_sha256": prior_receipt_sha256,
            "canary_run_id": canary_run_id,
            "map_final_evidence": (
                asdict(final_evidence) if final_evidence is not None else None
            ),
            "map_final_evidence_sha256": final_evidence_sha256,
        }
        completed = self._run_pin_candidate_oneoff(
            transaction=transaction,
            config=config,
            candidate=candidate,
            runner_name=(
                f"ktdm-pin-boundary-{journal.transaction_id}-{operation}"
            ),
            entrypoint="pinvi-cache-target-final-boundary",
            arguments=[operation],
            stdin=json.dumps(
                request,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
        )
        if completed.returncode != 0:
            raise DeploymentContractError(
                f"Pin final-boundary {operation} helper failed"
            )
        return parse_pin_boundary_receipt(
            stdout=completed.stdout,
            stderr=completed.stderr,
            request=request,
            expected_initial_count=expected_initial_count,
        )

    @staticmethod
    def _assert_cache_target_pin_audit_receipt(
        *,
        receipt: PinBoundaryReceipt,
        audit_row: PinBoundaryAuditRow,
    ) -> None:
        if (
            receipt.audit_row_count != 1
            or receipt.audit_id != audit_row.audit_id
            or receipt.audit_request_sha256 != audit_row.audit_request_sha256
            or receipt.evidence_sha256 != audit_row.evidence_sha256
            or receipt.map_final_evidence_sha256
            != audit_row.map_final_evidence_sha256
            or receipt.initial_writer_fence_sha256
            != audit_row.initial_writer_fence_sha256
            or receipt.final_writer_fence_sha256
            != audit_row.final_writer_fence_sha256
            or receipt.prior_receipt_sha256 != audit_row.prior_receipt_sha256
            or receipt.canary_run_id != audit_row.canary_run_id
        ):
            raise DeploymentContractError(
                "Pin final boundary audit row differs from its receipt"
            )

    def _run_pin_database_migration(
        self,
        *,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        candidate: CompatibleImagePair,
        runtime: DatabaseRuntime,
        database_identity: str,
        prior_receipt_sha256: str,
    ) -> PinMigrationReceipt:
        writer_fence_sha256 = journal.initial_writer_fence_sha256
        if writer_fence_sha256 is None:
            raise DeploymentContractError("Pin migration writer fence is missing")
        schema_before = read_database_schema_revision(runtime)
        if schema_before not in {"20260801_0047", "20260802_0048"}:
            raise DeploymentContractError("Pin migration source schema is invalid")
        if read_database_identity(runtime, journal.transaction_id) != database_identity:
            raise DeploymentContractError("Pin migration database identity drifted")
        completed = self._run_pin_candidate_oneoff(
            transaction=transaction,
            config=config,
            candidate=candidate,
            runner_name=f"ktdm-pin-migrate-{journal.transaction_id}",
            entrypoint="alembic",
            arguments=["upgrade", "head"],
            stdin=None,
        )
        if completed.returncode != 0:
            raise DeploymentContractError("Pin database migration failed")
        if read_database_schema_revision(runtime) != "20260802_0048":
            raise DeploymentContractError("Pin database migration head is invalid")
        if read_database_identity(runtime, journal.transaction_id) != database_identity:
            raise DeploymentContractError("Pin migration database identity changed")
        receipt = PinMigrationReceipt(
            contract_version="pinvi-cache-target-migration/v1",
            transaction_id=journal.transaction_id,
            source_revision=candidate.pinvi_source_revision,
            database_identity=database_identity,
            writer_registry_sha256=_CACHE_TARGET_WRITER_REGISTRY_SHA256,
            initial_writer_fence_sha256=writer_fence_sha256,
            prior_receipt_sha256=prior_receipt_sha256,
            candidate_image_id=candidate.pinvi_image_id,
            schema_before="20260801_0047",
            schema_after="20260802_0048",
            command_sha256=hashlib.sha256(
                b"pinvi-cache-target-migration-v1\0alembic\0upgrade\0head\0"
            ).hexdigest(),
            status="succeeded",
        )
        pin_migration_receipt_sha256(receipt)
        return receipt

    def _run_pin_candidate_oneoff(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        candidate: CompatibleImagePair,
        runner_name: str,
        entrypoint: str,
        arguments: list[str],
        stdin: str | None,
    ) -> subprocess.CompletedProcess[str]:
        candidate_transaction = self._materialize_active_recovery_transaction_unlocked(
            transaction,
            config,
            candidate,
        )
        self._cleanup_pin_candidate_runner(
            runner_name,
            expected_image_id=candidate.pinvi_image_id,
        )
        descriptor: int | None = None
        try:
            descriptor = os.memfd_create("ktdm-pin-compose", flags=os.MFD_CLOEXEC)
            os.write(
                descriptor,
                json.dumps(
                    candidate_transaction.resolved,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode(),
            )
            os.lseek(descriptor, 0, os.SEEK_SET)
            return subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    "/dev/null",
                    "--project-directory",
                    str(Path(transaction.environment.compose_path).parent),
                    "-f",
                    f"/proc/self/fd/{descriptor}",
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--name",
                    runner_name,
                    "--entrypoint",
                    entrypoint,
                    _PINVI_API_SERVICE,
                    *arguments,
                ],
                cwd=get_project_root(),
                text=True,
                capture_output=True,
                check=False,
                env=dict(transaction.environment.effective),
                pass_fds=(descriptor,),
                input=stdin,
                timeout=3600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentContractError("Pin candidate one-off could not run") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            self._cleanup_pin_candidate_runner(
                runner_name,
                expected_image_id=candidate.pinvi_image_id,
            )

    def _resume_cache_target_coupled_rollback(
        self,
        *,
        journal_path: Path,
        journal: CacheTargetWindowJournal,
        transaction: ComposeTransactionSnapshot,
        config: C6cDeploymentConfig,
        runtimes: tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime],
        wait_timeout: int,
    ) -> CacheTargetWindowJournal:
        if journal.phase not in {
            "rollback_preparing",
            "new_runtime_stopped",
            "map_db_restored",
            "map_dagster_db_restored",
            "pinvi_db_restored",
            "manager_state_restored",
            "writers_restored",
            "old_runtime_restored",
        }:
            journal = transition_cache_target_window(journal, "rollback_preparing")
            write_cache_target_window(journal_path, journal)
        state_directory = journal_path.parent
        if journal.phase == "rollback_preparing":
            stopped = self._run_frozen_recovery(
                [
                    "stop",
                    "pinvi-dagster",
                    _PINVI_API_SERVICE,
                    *_MAP_RUNTIME_SERVICES,
                ],
                mutation_capability=_COMPATIBLE_PAIR_MUTATION_CAPABILITY,
                transaction=transaction,
            )
            if not stopped.get("success"):
                raise DeploymentContractError(
                    "cache-target coupled rollback could not stop new runtime"
                )
            journal = transition_cache_target_window(journal, "new_runtime_stopped")
            write_cache_target_window(journal_path, journal)
        receipts = (
            journal.map_application_backup,
            journal.map_dagster_backup,
            journal.pinvi_backup,
        )
        present_receipts = tuple(receipt for receipt in receipts if receipt is not None)
        if present_receipts and (
            len(present_receipts) != 3
            or {receipt.transaction_id for receipt in present_receipts}
            != {journal.transaction_id}
            or len({receipt.writer_fence_sha256 for receipt in present_receipts}) != 1
            or any(
                receipt.writer_mutation_count != 0
                or receipt.restore_rehearsal.verified is not True
                for receipt in present_receipts
            )
        ):
            raise DeploymentContractError(
                "cache-target coupled rollback backup set is inconsistent"
            )
        restore_steps: tuple[
            tuple[WindowPhase, WindowPhase, DatabaseRuntime, DatabaseBackupReceipt | None],
            ...,
        ] = (
            ("new_runtime_stopped", "map_db_restored", runtimes[0], receipts[0]),
            (
                "map_db_restored",
                "map_dagster_db_restored",
                runtimes[1],
                receipts[1],
            ),
            (
                "map_dagster_db_restored",
                "pinvi_db_restored",
                runtimes[2],
                receipts[2],
            ),
        )
        for current_phase, next_phase, runtime, receipt in restore_steps:
            if journal.phase != current_phase:
                continue
            if receipt is not None:
                restore_database_backup(
                    state_directory=state_directory,
                    transaction_id=journal.transaction_id,
                    runtime=runtime,
                    receipt=receipt,
                    capability=_COUPLED_ROLLBACK_CAPABILITY,
                )
            journal = transition_cache_target_window(journal, next_phase)
            write_cache_target_window(journal_path, journal)
        if journal.phase == "pinvi_db_restored":
            if journal.rollback_bundle_sha256 is not None:
                verify_manager_rollback_bundle(
                    state_directory=state_directory,
                    transaction_id=journal.transaction_id,
                    expected_sha256=journal.rollback_bundle_sha256,
                )
                restore_manager_rollback_bundle(
                    state_directory=state_directory,
                    transaction_id=journal.transaction_id,
                    expected_sha256=journal.rollback_bundle_sha256,
                    env_path=Path(transaction.environment.env_path),
                    manifest_path=Path(transaction.manifest_path or ""),
                )
            journal = transition_cache_target_window(journal, "manager_state_restored")
            write_cache_target_window(journal_path, journal)
        if journal.phase in {"manager_state_restored", "writers_restored"}:
            restored_transaction, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            restored_config = load_c6c_deployment_config_from_environment(
                restored_transaction.environment.effective
            )
            old_manifest = load_pair_manifest(restored_transaction.manifest_path or "")
        if journal.phase == "manager_state_restored":
            if (
                journal.writer_drain_lease_id is None
                or journal.writer_drain_receipt_sha256 is None
            ):
                raise DeploymentContractError(
                    "cache-target rollback writer drain evidence is missing"
                )
            restore_receipt = self._restore_cache_target_writer_drain(
                owner_kind="cutover",
                owner_id=journal.cutover_id,
                transaction=restored_transaction,
                lease_id=journal.writer_drain_lease_id,
                prior_receipt_sha256=journal.writer_drain_receipt_sha256,
            )
            journal = transition_cache_target_window(
                journal,
                "writers_restored",
                writer_drain_restore_receipt_sha256=writer_drain_receipt_sha256(
                    restore_receipt
                ),
            )
            write_cache_target_window(journal_path, journal)
        if journal.phase == "writers_restored":
            result: dict[str, Any] = {
                "success": True,
                "returncode": 0,
                "stages": [],
                "command": [],
                "stdout": "",
                "stderr": "",
            }
            self._activate_pair_sequentially(
                result,
                restored_config,
                old_manifest.active,
                [*_MAP_RUNTIME_SERVICES, _PINVI_API_SERVICE],
                stage_prefix="coupled_rollback",
                transaction=restored_transaction,
                wait_timeout=wait_timeout,
            )
            self._restart_cache_target_auxiliary_writer(
                transaction=restored_transaction,
                config=restored_config,
            )
            self._attest_cache_target_prebootstrap_pair(
                restored_config,
                old_manifest,
                restored_transaction,
            )
            journal = transition_cache_target_window(journal, "old_runtime_restored")
            write_cache_target_window(journal_path, journal)
        if journal.phase == "old_runtime_restored":
            journal = transition_cache_target_window(journal, "rolled_back")
            write_cache_target_window(journal_path, journal)
        return journal

    @staticmethod
    def _cleanup_map_h35_runner(
        container_name: str,
        *,
        expected_image_id: str,
    ) -> None:
        inspected = subprocess.run(
            ["docker", "container", "inspect", container_name],
            cwd=get_project_root(),
            text=True,
            capture_output=True,
            check=False,
        )
        if inspected.returncode != 0:
            if "No such" in inspected.stderr:
                return
            raise DeploymentContractError("Map H35 runner classification failed")
        try:
            payload = json.loads(inspected.stdout)
            container = payload[0]
            labels = container["Config"]["Labels"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise DeploymentContractError("Map H35 runner evidence is invalid") from exc
        if (
            container.get("Image") != expected_image_id
            or not isinstance(labels, Mapping)
            or labels.get("com.docker.compose.service") != _MAP_API_SERVICE
            or labels.get("com.docker.compose.oneoff") != "True"
        ):
            raise DeploymentContractError(
                "foreign container occupies the Map H35 runner identity"
            )
        removed = subprocess.run(
            ["docker", "container", "rm", "--force", container_name],
            cwd=get_project_root(),
            text=True,
            capture_output=True,
            check=False,
        )
        if removed.returncode != 0 or removed.stderr or removed.stdout != f"{container_name}\n":
            raise DeploymentContractError("Map H35 runner cleanup failed")

    @staticmethod
    def _cleanup_pin_candidate_runner(
        container_name: str,
        *,
        expected_image_id: str,
    ) -> None:
        inspected = subprocess.run(
            ["docker", "container", "inspect", container_name],
            cwd=get_project_root(),
            text=True,
            capture_output=True,
            check=False,
        )
        if inspected.returncode != 0:
            if "No such" in inspected.stderr:
                return
            raise DeploymentContractError("Pin candidate runner classification failed")
        try:
            payload = json.loads(inspected.stdout)
            container = payload[0]
            labels = container["Config"]["Labels"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise DeploymentContractError(
                "Pin candidate runner evidence is invalid"
            ) from exc
        if (
            container.get("Image") != expected_image_id
            or not isinstance(labels, Mapping)
            or labels.get("com.docker.compose.service") != _PINVI_API_SERVICE
            or labels.get("com.docker.compose.oneoff") != "True"
        ):
            raise DeploymentContractError(
                "foreign container occupies the Pin candidate runner identity"
            )
        removed = subprocess.run(
            ["docker", "container", "rm", "--force", container_name],
            cwd=get_project_root(),
            text=True,
            capture_output=True,
            check=False,
        )
        if removed.returncode != 0 or removed.stderr or removed.stdout != f"{container_name}\n":
            raise DeploymentContractError("Pin candidate runner cleanup failed")

    @staticmethod
    def _cleanup_cache_target_initial_runner(
        container_name: str,
        *,
        expected_image_id: str,
    ) -> None:
        inspected = subprocess.run(
            ["docker", "container", "inspect", container_name],
            cwd=get_project_root(),
            text=True,
            capture_output=True,
            check=False,
        )
        if inspected.returncode != 0:
            if "No such" in inspected.stderr:
                return
            raise DeploymentContractError(
                "cache-target runner orphan classification failed"
            )
        try:
            payload = json.loads(inspected.stdout)
            container = payload[0]
            labels = container["Config"]["Labels"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise DeploymentContractError(
                "cache-target runner orphan evidence is invalid"
            ) from exc
        if (
            container.get("Image") != expected_image_id
            or not isinstance(labels, Mapping)
            or labels.get("com.docker.compose.service") != _PINVI_API_SERVICE
            or labels.get("com.docker.compose.oneoff") != "True"
        ):
            raise DeploymentContractError(
                "foreign container occupies the cache-target runner identity"
            )
        removed = subprocess.run(
            ["docker", "container", "rm", "--force", container_name],
            cwd=get_project_root(),
            text=True,
            capture_output=True,
            check=False,
        )
        if removed.returncode != 0:
            raise DeploymentContractError("cache-target runner orphan cleanup failed")

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
        wait_timeout: int = _DEFAULT_C6C_WAIT_TIMEOUT_SECONDS,
        expected_alembic_head: str | None = None,
    ) -> dict[str, Any]:
        """C6c compatible runtime set을 Map 검증 뒤 PinVi로 단계 배포한다."""

        manifest = self._production_preflight(
            config,
            transaction=transaction,
            build_provenance=build_provenance,
        )
        active_recovery_transaction = self._materialize_active_recovery_transaction_unlocked(
            transaction,
            config,
            manifest.active,
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

        retention_preflight = reconcile_pair_references(
            (manifest.active, manifest.rollback),
            cwd=get_project_root(),
        )
        result["retention_preflight"] = {
            "ensured": retention_preflight.ensured,
            "removed": retention_preflight.removed,
        }

        candidate_pair, prebuild_result = self._prepare_c6c_candidate_pair(
            config,
            build=build,
            build_provenance=build_provenance,
            transaction=transaction,
        )
        if expected_alembic_head is not None:
            # `candidate_pair.map_image_id`는 `build=True`일 때 방금 끝난 build로
            # 새로 만들어진 immutable ID다(위 `_prepare_c6c_candidate_pair` 참고) —
            # build 전 floating tag를 검사하면 build가 그 태그를 덮어써서 검사가
            # 무의미해진다(issue #109 재현 실공백, 적대적 리뷰로 확인). 반드시
            # 실제로 활성화될 exact 이미지를 여기서 검사한다.
            _assert_candidate_image_alembic_head(
                candidate_pair.map_image_id,
                expected_alembic_head=expected_alembic_head,
                label="Map API",
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
            candidate_retention = ensure_pair_references(
                (candidate_pair,),
                cwd=get_project_root(),
            )
        except Exception:
            try:
                reconcile_pair_references(
                    (manifest.active, manifest.rollback),
                    cwd=get_project_root(),
                )
            except DeploymentContractError:
                pass
            raise
        result["candidate_retention"] = {
            "ensured": candidate_retention.ensured,
        }

        manifest_commit_started = False
        updated_manifest: CompatiblePairManifest | None = None
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
                wait_timeout=wait_timeout,
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
            updated_manifest = manifest_with_active_pair(manifest, candidate_pair)
            manifest_commit_started = True
            write_pair_manifest(transaction.manifest_path, updated_manifest)
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
            if bool(recovery.get("success")) and not manifest_commit_started:
                try:
                    cleanup = reconcile_pair_references(
                        (manifest.active, manifest.rollback),
                        cwd=get_project_root(),
                    )
                    recovery["retention_cleanup"] = {
                        "success": True,
                        "removed": cleanup.removed,
                    }
                except DeploymentContractError:
                    recovery["retention_cleanup"] = {
                        "success": False,
                    }
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

        assert updated_manifest is not None
        try:
            retention_cleanup = reconcile_pair_references(
                (updated_manifest.rollback,),
                cwd=get_project_root(),
            )
        except DeploymentContractError:
            result["deployment_state"] = "active_manifest_committed_retention_cleanup_pending"
            result["retention_cleanup"] = {"success": False}
            result["stderr"] += (
                "compatible pair retention cleanup is pending; the next mutation will fail closed\n"
            )
            return result
        result["retention_cleanup"] = {
            "success": True,
            "removed": retention_cleanup.removed,
        }
        result["deployment_state"] = "active_manifest_committed"
        return result

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
        _require_cache_target_release(
            config,
            pairs=(manifest.rollback, manifest.active),
        )
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
            pair = self._inspect_c6c_candidate_pair(
                config,
                environment_override=None,
                transaction=transaction,
            )
            _require_cache_target_release(
                config,
                candidate_map_source_revision=pair.map_source_revision,
                candidate_source_revision=pair.pinvi_source_revision,
            )
            return pair, None
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
        _require_cache_target_release(
            config,
            candidate_map_source_revision=pair.map_source_revision,
            candidate_source_revision=pair.pinvi_source_revision,
        )
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
        allow_legacy_admin_proxy_absence = marker.state == "pending"
        runtime_config = self._inspect_container_runtime_config(config.map_ui_container)
        validate_current_map_ui_auth_runtime(
            runtime_config,
            config,
            source_env_contract_version=active_source_env_contract_version,
            allow_legacy_admin_proxy_absence=(allow_legacy_admin_proxy_absence),
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
        validate_retention_namespace_is_reserved(resolved)
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
        wait_timeout: int = _DEFAULT_C6C_WAIT_TIMEOUT_SECONDS,
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
            args.extend(["--wait", "--wait-timeout", str(wait_timeout)])
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
                *(
                    config.cache_target.protected_values
                    if config.cache_target is not None
                    else ()
                ),
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
        return compatible_pair_image_environment(pair)

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
        wait_timeout: int = _DEFAULT_C6C_WAIT_TIMEOUT_SECONDS,
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
            wait_timeout=wait_timeout,
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
            wait_timeout=wait_timeout,
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
            wait_timeout=wait_timeout,
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
        wait_timeout: int = _DEFAULT_C6C_WAIT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """clean 환경에서 candidate runtime set을 단계 검증해 최초 v4를 기록한다.

        `bootstrap_map_api` 단계는 `deploy_compatible_pinvi_pair`와 마찬가지로
        kor-travel-map API의 `alembic upgrade head`(uvicorn 기동 전 실행)를 기다린다
        (issue #88). clean bootstrap은 전체 마이그레이션 이력을 처음부터 실행할 수
        있어 증분 배포보다 오래 걸릴 수 있으므로 같은 `wait_timeout`을 노출한다.
        """

        if not verified_compatible:
            raise DeploymentContractError(
                "capturing a rollback pair requires --verified-compatible"
            )
        _validate_c6c_wait_timeout(wait_timeout)
        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
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
            _require_cache_target_release(config)
            build_provenance = (
                _derive_c6c_build_provenance(
                    transaction.environment.effective,
                    compose_path=transaction.environment.compose_path,
                )
                if build
                else None
            )
            if build_provenance is not None:
                _require_cache_target_release(
                    config,
                    candidate_map_source_revision=build_provenance.map_source_revision,
                    candidate_source_revision=build_provenance.pinvi_source_revision,
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
            require_empty_retention_namespace(cwd=get_project_root())
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
            try:
                candidate_retention = ensure_pair_references(
                    (candidate_pair,),
                    cwd=get_project_root(),
                )
            except Exception:
                try:
                    reconcile_pair_references((), cwd=get_project_root())
                except DeploymentContractError:
                    pass
                raise
            result["candidate_retention"] = {
                "ensured": candidate_retention.ensured,
            }
            mutation_attempted = False
            manifest_commit_started = False
            updated_manifest: CompatiblePairManifest | None = None
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
                        wait_timeout=wait_timeout,
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
                    wait_timeout=wait_timeout,
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
                    wait_timeout=wait_timeout,
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
                    wait_timeout=wait_timeout,
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
                    wait_timeout=wait_timeout,
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
                updated_manifest = initial_pair_manifest(pair)
                image_provenance = self._pair_provenance_payload(pair)
                result["verification"] = verification
                result["contract_generation"] = pair.contract_generation
                result["image_provenance"] = image_provenance
                result["stdout"] += (
                    f"compatible Map+PinVi image pair bootstrapped: {manifest_path}\n"
                )
                manifest_commit_started = True
                write_pair_manifest(manifest_path, updated_manifest)
            except Exception as exc:
                if not mutation_attempted:
                    try:
                        reconcile_pair_references((), cwd=get_project_root())
                    except DeploymentContractError:
                        pass
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
                if bool(recovery.get("success")) and not manifest_commit_started:
                    try:
                        cleanup = reconcile_pair_references((), cwd=get_project_root())
                        recovery["retention_cleanup"] = {
                            "success": True,
                            "removed": cleanup.removed,
                        }
                    except DeploymentContractError:
                        recovery["retention_cleanup"] = {"success": False}
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

            assert updated_manifest is not None
            try:
                retention_cleanup = reconcile_pair_references(
                    (updated_manifest.rollback,),
                    cwd=get_project_root(),
                )
            except DeploymentContractError:
                result["deployment_state"] = (
                    "initial_v4_manifest_committed_retention_cleanup_pending"
                )
                result["retention_cleanup"] = {"success": False}
                result["stderr"] += (
                    "compatible pair retention cleanup is pending; "
                    "the next mutation will fail closed\n"
                )
                return result
            result["retention_cleanup"] = {
                "success": True,
                "removed": retention_cleanup.removed,
            }
            result["deployment_state"] = "initial_v4_manifest_committed"
            return result

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
        records = self._compose_ps_records(
            str(ps_result.get("stdout", "")),
            allow_empty=True,
        )
        contracts = _resolved_service_readiness_contracts(
            transaction.resolved,
            services,
        )
        indexed = _index_singleton_service_records(
            records,
            services,
            contracts,
            allow_missing=True,
        )
        return {
            str(record["Service"]): str(record.get("State", "")).strip().lower()
            for record in indexed.values()
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

        with c6c_deployment_lock_from_environment() as lock_snapshot:
            transaction, _ = self._capture_transaction_unlocked(
                derive_manifest_path=True,
            )
            _assert_transaction_matches_c6c_lock(transaction, lock_snapshot)
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
            _require_cache_target_release(
                config,
                pairs=(active_at_start, rollback),
            )
            for pair in (active_at_start, rollback):
                if pair.contract_generation != config.contract_generation:
                    raise DeploymentContractError(
                        "rollback pair generation differs from the active deployment contract"
                    )
                self._require_pair_image_provenance(pair)
            active_recovery_transaction = self._materialize_active_recovery_transaction_unlocked(
                transaction,
                config,
                active_at_start,
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
            retention_preflight = reconcile_pair_references(
                (active_at_start, rollback),
                cwd=get_project_root(),
            )
            result["retention_preflight"] = {
                "ensured": retention_preflight.ensured,
                "removed": retention_preflight.removed,
            }
            cancel_probe_state = PinviCancelProbeState()
            updated_manifest: CompatiblePairManifest | None = None
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
                updated_manifest = manifest_with_active_pair(manifest, rollback)
                write_pair_manifest(manifest_path, updated_manifest)
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

            assert updated_manifest is not None
            try:
                retention_cleanup = reconcile_pair_references(
                    (updated_manifest.rollback,),
                    cwd=get_project_root(),
                )
            except DeploymentContractError:
                result["rollback_state"] = "active_manifest_committed_retention_cleanup_pending"
                result["retention_cleanup"] = {"success": False}
                result["stderr"] += (
                    "compatible pair retention cleanup is pending; "
                    "the next mutation will fail closed\n"
                )
                return result
            result["retention_cleanup"] = {
                "success": True,
                "removed": retention_cleanup.removed,
            }
            result["rollback_state"] = "active_manifest_committed"
            return result

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
            if (
                self._inspect_image_source_revision(
                    map_image_ids[service_name],
                    label=service_name,
                )
                != map_source_revision
            ):
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
        services = _MAP_RUNTIME_SERVICES if include_api else _MAP_RUNTIME_SERVICES[1:]
        for service_name in services:
            self._verify_running_image_source_provenance(
                _MAP_RUNTIME_CONTAINERS[service_name],
                label=service_name,
                expected_revision=expected_revision,
            )

    @staticmethod
    def _require_local_image(image_id: str) -> None:
        require_local_c6c_image(image_id, cwd=get_project_root())

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
        verify_compatible_pair_image_provenance(
            pair,
            require_local_image=self._require_local_image,
            inspect_source_revision=self._inspect_image_source_revision,
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
