import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, NoReturn, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

import yaml
from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import (
    _MANAGED_COMPOSE_MUTATION_CAPABILITY,
    _MAP_APPLICATION_FRESH_300_SERVICE,
    _MAP_APPLICATION_FRESH_FINALIZE_SERVICE,
    _MAP_RUNTIME_SERVICES,
    _PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
    _PINVI_ADMIN_BOOTSTRAP_SERVICE,
    _PINVI_API_SERVICE,
    _PINVI_DB_RUNTIME_ROLE_SERVICE,
    C6cBuildProvenance,
    C6cCancelProbeFixture,
    C6cDeploymentConfig,
    CandidateSystemBindSnapshot,
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
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
from kor_travel_docker_manager.services.database_runtime import (
    Application300DatabaseIdentity as RuntimeApplication300DatabaseIdentity,
)
from kor_travel_docker_manager.services.database_runtime import (
    DagsterMetadataDatabaseIdentity as RuntimeDagsterMetadataDatabaseIdentity,
)
from kor_travel_docker_manager.services.database_runtime import (
    DatabaseRuntime,
    PinnedDatabaseIdentity,
    create_fresh_application_300_database,
    database_runtimes_from_frozen_contract,
    initialize_application_300_dagster_metadata_database,
    inspect_application_300_bootstrap_state,
    read_application_300_dagster_metadata_identity,
    read_application_300_database_identity,
    read_database_schema_revision,
    read_pinned_database_identity,
    reset_databases_for_application_300,
)
from kor_travel_docker_manager.services.map_application_300 import (
    Application300Candidate as Application300ExecutionCandidate,
)
from kor_travel_docker_manager.services.map_application_300 import (
    ApplicationDatabaseIdentity,
    DagsterDatabaseIdentity,
    DagsterLoginRoleAttributes,
    DagsterStorageCandidate,
    FreshFinalizeResult,
    FreshRootMissingReceipt,
    FreshRootResult,
    JournalStamp,
    MapApplication300ContractError,
    build_application_final_permit,
    build_dagster_metadata_permit,
    build_fresh_finalize_fence,
    build_fresh_migration_fence,
    parse_fresh_finalize_missing_receipt,
    parse_fresh_finalize_result,
    parse_fresh_root_missing_receipt,
    parse_fresh_root_result,
    publish_root_read_only_artifact,
    read_owner_only_artifact,
    read_root_read_only_artifact,
    replace_root_read_only_artifact,
    write_owner_only_artifact,
)
from kor_travel_docker_manager.services.map_application_300_candidate import (
    ImmutableImageObservation,
    MapApplication300Candidate,
    MapApplication300CandidateError,
    load_map_application_300_candidate,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PINVI_ROLE_CATALOG_RESET_DIAGNOSTICS,
    REBUILD_PHASES,
    RUNTIME_SERVICES,
    MapApplication300ApplicationDatabaseIdentity,
    MapApplication300DagsterMetadataDatabaseIdentity,
    MapApplication300DagsterMetadataRoleAttributes,
    MapApplication300OperationPlan,
    PinnedRuntimeCancelProbeOutcome,
    PinnedRuntimeCancelProbeReceipt,
    PinnedRuntimeDatabaseIdentity,
    PinnedRuntimeManifest,
    PinnedRuntimeRebuildJournal,
    PinnedRuntimeStatePaths,
    PinviRoleCatalogResetDiagnostic,
    PinviRoleLifecycleBlock,
    RebuildPhase,
    RuntimeService,
    ensure_pinned_runtime_state_directory,
    generation_logical_sha256,
    pinned_runtime_state_paths,
    rebuild_journal_sha256,
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
    COMPOSE_BUILT_RUNTIME_SERVICES,
    CandidateRuntimeBuild,
    MapApplication300ArtifactDirectories,
    build_candidate_generation,
    generation_compose_environment,
    map_application_300_paired_build_image_names,
    new_candidate_journal,
    parse_candidate_static_head,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    PinnedRuntimeRelease,
    current_pinned_runtime_release,
    is_blocked_pinset_retry,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    PinnedRuntimeSourceMaterialization,
    materialize_pinned_runtime_sources,
)
from kor_travel_docker_manager.services.pinvi_bootstrap_credential import (
    pinvi_bootstrap_credential_file,
    reconcile_orphaned_pinvi_bootstrap_credentials,
)
from kor_travel_docker_manager.services.pinvi_database_role_credentials import (
    ensure_pinned_runtime_pinvi_role_credentials,
    pinvi_role_credentials_are_all_undeclared,
    rebind_source_environment_sha256,
    trusted_pinned_runtime_project_root,
)
from kor_travel_docker_manager.services.registry import (
    get_project_root,
    init_steps_for_target,
    is_known_target,
    runtime_services_for_target,
    services_for_target,
    target_sequence_for_target,
)

_PINNED_RUNTIME_ONESHOT_WRITERS = (
    "pinvi-db-init",
    _PINVI_DB_RUNTIME_ROLE_SERVICE,
    "kor-travel-map-dagster-db-init",
    "kor-travel-map-db-role-bootstrap",
    _MAP_APPLICATION_FRESH_300_SERVICE,
    _MAP_APPLICATION_FRESH_FINALIZE_SERVICE,
    "kor-travel-map-dagster-storage-migrate",
    "pinvi-admin-bootstrap",
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
    }
)
_MAP_APPLICATION_FRESH_PYTHON = "/usr/local/bin/python"
_MAP_APPLICATION_FRESH_300_EXECUTABLE = (
    "/usr/local/bin/ktm-application-schema-fresh-300"
)
_MAP_APPLICATION_FRESH_FINALIZE_EXECUTABLE = (
    "/usr/local/bin/ktm-application-schema-fresh-finalize"
)
# frozen transaction은 실행 전에 one-shot service까지 exact resolved document에 결박한다.
# profile을 해석 단계에서 빼면 `run --profile bootstrap`가 같은 문서에서 service를 찾지 못한다.
_FROZEN_COMPOSE_PROFILES = ("bootstrap",)
_PINVI_ROLE_TOPOLOGY_DIAGNOSTIC_SCHEMA = "pinvi.role-topology-diagnostic.v1"
_PINVI_ROLE_TOPOLOGY_NONCANONICAL_REASONS = (
    "principal_identity",
    "bootstrap_catalog",
    "fence_acl",
    "runtime_role",
    "schema_owner_membership",
    "migration_owner_policy",
    "migrator_sealed",
    "migrator_membership_setting",
    "app_ownership",
    "extension_ownership",
)


class PinnedRuntimePrejournalFailure(DeploymentContractError):
    """journal 전 후보 준비 실패를 비밀 없는 고정 단계로 전달한다."""

    def __init__(self, stage: str) -> None:
        if stage not in _PINNED_RUNTIME_PREJOURNAL_FAILURE_STAGES:
            raise ValueError("pinned runtime pre-journal failure stage is invalid")
        self.stage = stage
        super().__init__("pinned runtime candidate preparation failed")


@contextmanager
def _pinned_runtime_prejournal_step(stage: str) -> Iterator[None]:
    """journal 전 ``DeploymentContractError``를 safe stage로 봉인한다."""

    try:
        yield
    except PinnedRuntimePrejournalFailure:
        raise
    except DeploymentContractError as exc:
        raise PinnedRuntimePrejournalFailure(stage) from exc


def _application_300_profile_operation_args(
    *,
    service: str,
    executable: str,
    operation: str,
    operation_id: str,
) -> list[str]:
    """고정 entrypoint를 보존하면서 recovery/probe argv를 명시한다."""

    return [
        "--profile",
        "bootstrap",
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        _MAP_APPLICATION_FRESH_PYTHON,
        service,
        "-I",
        executable,
        operation,
        "--operation-id",
        operation_id,
    ]


_MAP_APPLICATION_300_RECEIPT_DIRECTORY = "map-application-300-candidate"
_MAP_APPLICATION_300_ARTIFACT_DIRECTORY = "map-application-300-artifacts"
_MAP_APPLICATION_300_POSTGRES_REFERENCE = "postgis/postgis:16-3.5-alpine"
_MAP_DAGSTER_STORAGE_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "permit_sha256",
        "candidate_sha256",
        "head",
        "version_num",
        "database_name",
        "database_oid",
        "database_owner",
        "postgres_system_identifier",
        "catalog_sha256",
    }
)


def _validate_map_dagster_storage_receipt(
    receipt: object,
    *,
    journal: PinnedRuntimeRebuildJournal,
    candidate: MapApplication300Candidate,
) -> None:
    """Map v3 receipt를 journal·permit·candidate·DB identity에 exact 결박한다."""

    database = (
        journal.map_application_300_execution_evidence
        .dagster_metadata_database_identity
    )
    permit_sha256 = (
        journal.map_application_300_execution_evidence.metadata_permit_sha256
    )
    candidate_binding = (
        f"{candidate.dagster_image_id}:{candidate.receipt_sha256}:"
        f"{candidate.dagster_yaml_sha256}"
    )
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != _MAP_DAGSTER_STORAGE_RECEIPT_FIELDS
        or database is None
        or permit_sha256 is None
        or receipt.get("schema")
        != "kor-travel-map.dagster-storage-migration.v3"
        or receipt.get("status") != "migrated"
        or receipt.get("operation_id") != journal.transaction_id
        or receipt.get("permit_sha256") != permit_sha256
        or receipt.get("candidate_sha256")
        != hashlib.sha256(candidate_binding.encode()).hexdigest()
        or receipt.get("head") != journal.candidate.map_dagster_head
        or receipt.get("version_num") != journal.candidate.map_dagster_head
        or receipt.get("database_name") != database.name
        or receipt.get("database_oid") != str(database.oid)
        or receipt.get("database_owner") != database.owner
        or receipt.get("postgres_system_identifier")
        != database.system_identifier
        or not isinstance(receipt.get("catalog_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", cast(str, receipt["catalog_sha256"]))
        is None
    ):
        raise DeploymentContractError(
            "Map Dagster storage receipt differs from journal"
        )


def _pinvi_cancel_probe_state_from_journal(
    journal: PinnedRuntimeRebuildJournal,
) -> PinviCancelProbeState:
    """v6 secret-free receipt만으로 F1J helper의 resume state를 복원한다."""

    receipt = journal.cancel_probe
    if receipt.stage == "uninitialized":
        return PinviCancelProbeState(transaction_id=journal.transaction_id)
    if receipt.stage in {"armed", "cancel_post_attempted"}:
        fixture = C6cCancelProbeFixture(
            transaction_id=journal.transaction_id,
            job_id=receipt.job_id or "",
            state="armed",
            cancellation_id=None,
            canonical_unsafe_outcome=None,
            created_at=receipt.fixture_created_at,
        )
        return PinviCancelProbeState(
            transaction_id=journal.transaction_id,
            fixture=fixture,
            attempted=receipt.stage == "cancel_post_attempted",
        )
    outcome = receipt.outcome
    if outcome is None:
        raise DeploymentContractError("pinned runtime consumed cancel probe has no outcome")
    fixture = C6cCancelProbeFixture(
        transaction_id=journal.transaction_id,
        job_id=receipt.job_id or "",
        state="finalized" if receipt.stage == "finalized" else "consumed",
        cancellation_id=receipt.cancellation_id,
        canonical_unsafe_outcome=outcome.to_payload(),
        created_at=receipt.fixture_created_at,
        consumed_at=receipt.fixture_consumed_at,
        finalized_at=receipt.fixture_finalized_at,
    )
    return PinviCancelProbeState(
        transaction_id=journal.transaction_id,
        fixture=fixture,
        attempted=True,
        finalize_attempted=receipt.stage in {"finalize_post_attempted", "finalized"},
        result=outcome.to_payload(),
    )


def _cancel_probe_receipt_from_pinvi_state(
    state: PinviCancelProbeState,
) -> PinnedRuntimeCancelProbeReceipt:
    """helper의 mutable state를 journal high-watermark 하나로 정규화한다."""

    fixture = state.fixture
    if fixture is None:
        if state.attempted or state.finalize_attempted or state.result is not None:
            raise DeploymentContractError("pinned runtime cancel probe state lacks fixture receipt")
        return PinnedRuntimeCancelProbeReceipt()
    if fixture.state == "armed":
        if state.result is not None or state.finalize_attempted:
            raise DeploymentContractError("armed cancel probe state has cancellation evidence")
        return PinnedRuntimeCancelProbeReceipt(
            stage="cancel_post_attempted" if state.attempted else "armed",
            job_id=fixture.job_id,
            fixture_created_at=fixture.created_at,
        )
    if (
        fixture.cancellation_id is None
        or fixture.canonical_unsafe_outcome is None
        or not state.attempted
    ):
        raise DeploymentContractError("consumed cancel probe state is incomplete")
    outcome_payload = fixture.canonical_unsafe_outcome
    if (
        set(outcome_payload) != {"name", "status", "code"}
        or not isinstance(outcome_payload.get("name"), str)
        or type(outcome_payload.get("status")) is not int
        or not isinstance(outcome_payload.get("code"), str)
    ):
        raise DeploymentContractError("consumed cancel probe outcome is invalid")
    outcome = PinnedRuntimeCancelProbeOutcome(
        name=cast(Literal["pinvi_cancel_error"], outcome_payload["name"]),
        status=cast(Literal[409], outcome_payload["status"]),
        code=cast(
            Literal["PIPELINE_CANCELLATION_UNSAFE"],
            outcome_payload["code"],
        ),
    )
    if state.result != outcome.to_payload():
        raise DeploymentContractError("consumed cancel probe result differs from fixture")
    if fixture.state == "consumed":
        stage = "finalize_post_attempted" if state.finalize_attempted else "consumed"
    elif fixture.state == "finalized":
        if not state.finalize_attempted:
            raise DeploymentContractError("finalized cancel probe has no durable attempt")
        stage = "finalized"
    else:
        raise DeploymentContractError("cancel probe fixture state is invalid")
    return PinnedRuntimeCancelProbeReceipt(
        stage=cast(
            Literal[
                "consumed",
                "finalize_post_attempted",
                "finalized",
            ],
            stage,
        ),
        job_id=fixture.job_id,
        cancellation_id=fixture.cancellation_id,
        outcome=outcome,
        fixture_created_at=fixture.created_at,
        fixture_consumed_at=fixture.consumed_at,
        fixture_finalized_at=fixture.finalized_at,
    )


def _pinned_runtime_reset_required(journal: PinnedRuntimeRebuildJournal) -> bool:
    """같은 v8 transaction의 durable reset 경계에서만 DB reset을 허용한다.

    ``databases_recreated`` 이후에는 fixture 상태와 관계없이 자동 reset을 금지한다.
    application-300 root/finalize와 metadata identity는 journal의 exact DB identity에
    결박되므로, 이후 checkpoint에서 DB를 다시 만들면 보존된 fence/permit이 무효가 된다.
    """

    return journal.phase in {"candidate_attested", "reset_intent_durable"}


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
_PINVI_DB_RUNTIME_ROLE_ERROR_CODE_BY_LINE = {
    "invalid PostgreSQL role name": "role_input_invalid",
    "invalid POSTGRES_DB": "role_input_invalid",
    "PINVI_M05_LEGACY_REBASELINE must be 0 or 1": "role_input_invalid",
    "PINVI_MIGRATOR_DISABLE_LOGIN must be 0 or 1": "role_input_invalid",
    (
        "PINVI_DB_HOST and PINVI_DB_PORT must name an approved PostgreSQL endpoint"
    ): "role_input_invalid",
    (
        "runtime, schema owner, migration owner, migrator, and bootstrap roles must differ"
    ): "role_input_invalid",
    "Postgres TCP endpoint did not become ready for DB role bootstrap": "role_endpoint_not_ready",
    (
        "existing app objects are not owned by PINVI_APP_SCHEMA_OWNER; "
        "use the approved root-only legacy rebaseline profile"
    ): "role_existing_owner_noncanonical",
    "runtime/migrator/migration-owner role topology is not canonical": (
        "role_topology_noncanonical"
    ),
}
_PINVI_DB_RUNTIME_ROLE_ERROR_CODES = frozenset(
    _PINVI_DB_RUNTIME_ROLE_ERROR_CODE_BY_LINE.values()
)


class _PinviRoleLifecycleError(DeploymentContractError):
    """lifecycle의 공개 오류와 재실행 차단 receipt를 함께 전달한다."""

    def __init__(
        self,
        message: str,
        *,
        role_topology_block: PinviRoleLifecycleBlock | None,
    ) -> None:
        super().__init__(message)
        self.role_topology_block = role_topology_block


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


def _parse_pinvi_role_catalog_reset_result(
    raw: bytes, *, transaction_id: str, pinset_sha256: str
) -> str:
    try:
        payload = json.loads(raw, object_pairs_hook=_json_object_without_duplicate_keys)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return "unclassified"
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema", "status", "class", "transaction", "pinset"
    }:
        return "unclassified"
    if (
        payload.get("schema") != "pinvi.role-catalog-reset-diagnostic.v1"
        or payload.get("transaction") != transaction_id
        or payload.get("pinset") != pinset_sha256
    ):
        return "unclassified"
    if payload.get("status") == "completed" and payload.get("class") == "completed":
        return "completed"
    if (
        payload.get("status") == "failed"
        and payload.get("class") in PINVI_ROLE_CATALOG_RESET_DIAGNOSTICS
        and payload.get("class") not in {"permit_invalid", "unclassified"}
    ):
        return cast(str, payload["class"])
    return "unclassified"


def _read_pinvi_role_catalog_reset_result(
    path: Path,
    *,
    expected_identity: tuple[int, int],
    transaction_id: str,
    pinset_sha256: str,
) -> str:
    """Read a reset receipt only when the Manager-created inode is preserved."""

    raw = read_owner_only_artifact(path)
    try:
        observed = path.lstat()
    except OSError as exc:
        raise MapApplication300ContractError("reset receipt is unavailable") from exc
    if (observed.st_dev, observed.st_ino) != expected_identity:
        return "unclassified"
    return _parse_pinvi_role_catalog_reset_result(
        raw,
        transaction_id=transaction_id,
        pinset_sha256=pinset_sha256,
    )


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
    """source staging·state owner와 Docker mutation authority를 root로 고정한다."""

    if os.geteuid() != 0:
        raise DeploymentContractError("pinned runtime rebuild requires root execution")


def _assert_pinset_is_not_permanently_blocked(pinset_sha256: str) -> None:
    """legacy source terminal 또는 현재 v6 execution terminal을 mutation 전에 거부한다.

    Map·PinVi 저장소는 "terminal candidate는 영구 재시도 금지"를 문서 규율로만
    지켜 왔고 어긴 실행을 막는 기계 게이트가 없었다. v5 차단 목록은 source audit을
    소유하고, 실제 재실행 판정은 결박된 v6 execution lifecycle이 소유한다.
    """

    from kor_travel_docker_manager.services.runtime_pair_rotation import (
        require_no_pending_runtime_pair_rotation,
    )
    from kor_travel_docker_manager.services.runtime_pin_registry import (
        load_runtime_pin_registry,
    )

    require_no_pending_runtime_pair_rotation()

    # release를 이미 registry에서 읽은 뒤이므로 여기서 실패하면 파일이 방금
    # 사라진 것이다. 차단 판정을 못 하는 상태로 파괴적 작업을 진행하지 않는다.
    registry = load_runtime_pin_registry()

    # v5 terminal은 source materialization의 감사 기록이며 Manager revision을 담지
    # 않는다. 그러나 v5가 미차단이라고 v6 execution이 미차단이라는 뜻은 아니다.
    # 따라서 모든 destructive rebuild는 exact trusted v6 binding과 그 terminal state를
    # 확인한다. 이 gate를 legacy terminal일 때만 적용하면 실제 v6 one-shot terminal을
    # 다음 rebuild가 우회할 수 있다.
    from kor_travel_docker_manager.services.runtime_execution_registry import (
        load_runtime_execution_registry,
        trusted_manager_source_revision,
    )

    try:
        execution = load_runtime_execution_registry()
        execution_is_runnable = execution.current_matches(
            pins=registry, manager_source_revision=trusted_manager_source_revision()
        ) and not execution.is_unconditionally_blocked_current()
    except DeploymentContractError:
        execution_is_runnable = False
    if not execution_is_runnable:
        source_state = (
            "legacy source pinset is terminal and "
            if registry.is_unconditionally_blocked_pinset(pinset_sha256)
            else ""
        )
        raise DeploymentContractError(
            "pinned runtime rebuild is blocked: "
            + source_state
            + "the current trusted execution is missing, stale, or terminal"
        )


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
        assert_pinned_runtime_rebuild_allowed(
            environment=initial_environment_snapshot.effective
        )
        with _pinned_runtime_prejournal_step("environment_admission"):
            validate_c6c_operation_tokens(
                initial_environment_snapshot.effective,
                require_nonempty=True,
            )
        # 기존 journal의 lifecycle admission은 immutable terminal evidence를
        # 해석하는 경계다. 새 candidate preparation failure로 재분류하지 않는다.
        rebind_source_sha256 = prewrite_admission(initial_environment_snapshot)
        with _pinned_runtime_prejournal_step("environment_admission"):
            role_credentials = ensure_pinned_runtime_pinvi_role_credentials(
                Path(initial_environment_snapshot.env_path),
                expected_environment_bytes=initial_environment_snapshot.env_file_bytes,
                rebind_source_sha256=rebind_source_sha256,
            )
            current_environment_snapshot = (
                _capture_pinned_runtime_rebuild_environment_snapshot(
                    environment_override=role_credentials
                )
            )
            assert_pinned_runtime_rebuild_allowed(
                environment=current_environment_snapshot.effective
            )
            validate_c6c_operation_tokens(
                current_environment_snapshot.effective,
                require_nonempty=True,
            )
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
_ALEMBIC_HEAD_INSPECTION_TIMEOUT_SECONDS = 60
_PINNED_RUNTIME_STATIC_INSPECTION_TIMEOUT_SECONDS = 60
#: compose `--wait-timeout` 초. **정수**로 둔다 — head는 revision 문자열이라
#: 형이 다르고, 이 파일에 따옴표 두른 숫자가 남지 않아 head 리터럴 게이트가
#: 파일 단위 면제 없이 이 파일을 전부 볼 수 있다. 면제는 그 자체로 사각지대였다.
_COMPOSE_WAIT_TIMEOUT_SECONDS: Final = 300


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
    api_receipt: Path
    paired_receipt: Path
    root_fence_directory: Path
    finalize_fence_directory: Path
    application_permit_directory: Path
    metadata_permit_directory: Path
    result_directory: Path

    @property
    def root_result(self) -> Path:
        return self.result_directory / "fresh-root-result.json"

    @property
    def finalize_result(self) -> Path:
        return self.result_directory / "fresh-finalize-result.json"

    @property
    def root_fence(self) -> Path:
        return self.root_fence_directory / "fence.json"

    @property
    def finalize_fence(self) -> Path:
        return self.finalize_fence_directory / "fence.json"

    @property
    def application_permit(self) -> Path:
        return self.application_permit_directory / "permit.json"

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
    artifact_directories = tuple(
        artifact_directory / name
        for name in (
            "fresh-root-fence",
            "fresh-finalize-fence",
            "application-final-permit",
            "dagster-storage-permit",
            "results",
        )
    )
    for directory in artifact_directories[:4]:
        _ensure_application_300_mount_directory(directory)
    _ensure_application_300_private_directory(artifact_directories[4])
    return _MapApplication300Paths(
        api_receipt=receipt_directory / "api-candidate-build.json",
        paired_receipt=receipt_directory / "paired-candidate-build.json",
        root_fence_directory=artifact_directories[0],
        finalize_fence_directory=artifact_directories[1],
        application_permit_directory=artifact_directories[2],
        metadata_permit_directory=artifact_directories[3],
        result_directory=artifact_directories[4],
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


def _application_300_execution_candidate(
    candidate: MapApplication300Candidate,
) -> Application300ExecutionCandidate:
    try:
        return Application300ExecutionCandidate(
            map_source_commit=candidate.candidate_commit,
            api_image_id=candidate.api_image_id,
            dagster_image_id=candidate.dagster_image_id,
        )
    except MapApplication300ContractError as exc:
        raise DeploymentContractError(
            "application 300 execution candidate is invalid"
        ) from exc


def _application_300_database_identities(
    identity: RuntimeApplication300DatabaseIdentity,
) -> tuple[ApplicationDatabaseIdentity, MapApplication300ApplicationDatabaseIdentity]:
    try:
        contract_identity = ApplicationDatabaseIdentity(
            name=identity.database_name,
            oid=identity.database_oid,
            owner=identity.database_owner,
            system_identifier=identity.postgres_system_identifier,
        )
        journal_identity = _application_300_journal_database_identity(identity)
    except (DeploymentContractError, MapApplication300ContractError) as exc:
        raise DeploymentContractError(
            "application 300 database identity is invalid"
        ) from exc
    return contract_identity, journal_identity


def _application_300_journal_database_identity(
    identity: RuntimeApplication300DatabaseIdentity,
) -> MapApplication300ApplicationDatabaseIdentity:
    """bootstrap 전·후 owner를 모두 보존할 수 있는 journal identity로 바꾼다."""

    try:
        return MapApplication300ApplicationDatabaseIdentity(
            database_name=identity.database_name,
            database_oid=identity.database_oid,
            database_owner=identity.database_owner,
            postgres_system_identifier=identity.postgres_system_identifier,
        )
    except DeploymentContractError as exc:
        raise DeploymentContractError(
            "application 300 journal database identity is invalid"
        ) from exc


def _pinned_runtime_journal_database_identity(
    identity: PinnedDatabaseIdentity,
) -> PinnedRuntimeDatabaseIdentity:
    try:
        return PinnedRuntimeDatabaseIdentity(
            system_identifier=identity.system_identifier,
            name=identity.name,
            oid=identity.oid,
            owner=identity.owner,
            login_role=identity.login_role,
        )
    except DeploymentContractError as exc:
        raise DeploymentContractError(
            "pinned runtime database identity is invalid"
        ) from exc


def _application_300_dagster_identities(
    identity: RuntimeDagsterMetadataDatabaseIdentity,
) -> tuple[DagsterDatabaseIdentity, MapApplication300DagsterMetadataDatabaseIdentity]:
    try:
        contract_attributes = DagsterLoginRoleAttributes(
            can_login=identity.login_role_attributes.can_login,
            inherit=identity.login_role_attributes.inherit,
            superuser=identity.login_role_attributes.superuser,
            create_database=identity.login_role_attributes.create_database,
            create_role=identity.login_role_attributes.create_role,
            replication=identity.login_role_attributes.replication,
            bypass_rls=identity.login_role_attributes.bypass_rls,
            granted_role_count=identity.login_role_attributes.granted_role_count,
            member_role_count=identity.login_role_attributes.member_role_count,
            connection_limit=identity.login_role_attributes.connection_limit,
            valid_until_is_null=identity.login_role_attributes.valid_until_is_null,
            role_config_count=identity.login_role_attributes.role_config_count,
            database_role_setting_count=(
                identity.login_role_attributes.database_role_setting_count
            ),
        )
        journal_attributes = MapApplication300DagsterMetadataRoleAttributes(
            can_login=identity.login_role_attributes.can_login,
            inherit=identity.login_role_attributes.inherit,
            superuser=identity.login_role_attributes.superuser,
            create_database=identity.login_role_attributes.create_database,
            create_role=identity.login_role_attributes.create_role,
            replication=identity.login_role_attributes.replication,
            bypass_rls=identity.login_role_attributes.bypass_rls,
            granted_role_count=identity.login_role_attributes.granted_role_count,
            member_role_count=identity.login_role_attributes.member_role_count,
            connection_limit=identity.login_role_attributes.connection_limit,
            valid_until_is_null=identity.login_role_attributes.valid_until_is_null,
            role_config_count=identity.login_role_attributes.role_config_count,
            database_role_setting_count=(
                identity.login_role_attributes.database_role_setting_count
            ),
        )
        contract_identity = DagsterDatabaseIdentity(
            system_identifier=identity.system_identifier,
            name=identity.name,
            oid=identity.oid,
            owner=identity.owner,
            login_role=identity.login_role,
            login_role_attributes=contract_attributes,
        )
        journal_identity = MapApplication300DagsterMetadataDatabaseIdentity(
            system_identifier=identity.system_identifier,
            name=identity.name,
            oid=identity.oid,
            owner=identity.owner,
            login_role=identity.login_role,
            login_role_attributes=journal_attributes,
        )
    except (DeploymentContractError, MapApplication300ContractError) as exc:
        raise DeploymentContractError(
            "application 300 Dagster metadata identity is invalid"
        ) from exc
    return contract_identity, journal_identity


def _application_300_plan_expiry(plan: MapApplication300OperationPlan) -> datetime:
    try:
        value = datetime.fromisoformat(plan.writer_fence_expires_at)
    except ValueError as exc:
        raise DeploymentContractError(
            "application 300 operation expiry is invalid"
        ) from exc
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise DeploymentContractError("application 300 operation expiry is invalid")
    return value


def _application_300_journal_stamp(
    plan: MapApplication300OperationPlan,
) -> JournalStamp:
    try:
        return JournalStamp(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            journal_sha256=plan.basis_journal_sha256,
            journal_generation=plan.basis_journal_generation,
        )
    except MapApplication300ContractError as exc:
        raise DeploymentContractError("application 300 journal stamp is invalid") from exc


def _application_300_root_fence(
    *,
    candidate: MapApplication300Candidate,
    database: ApplicationDatabaseIdentity,
    plan: MapApplication300OperationPlan,
) -> bytes:
    try:
        artifact = build_fresh_migration_fence(
            contract=candidate.application_contract,
            candidate=_application_300_execution_candidate(candidate),
            database=database,
            journal=_application_300_journal_stamp(plan),
            writer_fence_expires_at=_application_300_plan_expiry(plan),
        )
    except MapApplication300ContractError as exc:
        raise DeploymentContractError("application 300 root fence is invalid") from exc
    if artifact.sha256 != plan.fence_sha256:
        raise DeploymentContractError("application 300 root fence digest differs")
    return artifact.raw


def _application_300_finalize_fence(
    *,
    candidate: MapApplication300Candidate,
    database: ApplicationDatabaseIdentity,
    prior: FreshRootResult,
    plan: MapApplication300OperationPlan,
) -> bytes:
    try:
        artifact = build_fresh_finalize_fence(
            contract=candidate.application_contract,
            candidate=_application_300_execution_candidate(candidate),
            database=database,
            journal=_application_300_journal_stamp(plan),
            prior=prior,
            writer_fence_expires_at=_application_300_plan_expiry(plan),
        )
    except MapApplication300ContractError as exc:
        raise DeploymentContractError("application 300 finalize fence is invalid") from exc
    if artifact.sha256 != plan.fence_sha256:
        raise DeploymentContractError("application 300 finalize fence digest differs")
    return artifact.raw


def _application_300_root_result(
    *,
    raw: bytes,
    candidate: MapApplication300Candidate,
    database: ApplicationDatabaseIdentity,
    plan: MapApplication300OperationPlan,
) -> FreshRootResult:
    try:
        result = parse_fresh_root_result(
            raw,
            contract=candidate.application_contract,
            candidate=_application_300_execution_candidate(candidate),
        )
    except MapApplication300ContractError as exc:
        raise DeploymentContractError(
            "application 300 root result is invalid"
        ) from exc
    if (
        result.writer_fence_receipt_sha256 != plan.fence_sha256
        or result.writer_fence_transaction_id != plan.transaction_id
        or result.operation_id != plan.operation_id
        or result.journal_sha256 != plan.basis_journal_sha256
        or result.journal_generation != plan.basis_journal_generation
        or result.database_identity != database
    ):
        raise DeploymentContractError("application 300 root result differs from plan")
    return result


def _application_300_root_missing_receipt(
    *,
    raw: bytes,
    candidate: MapApplication300Candidate,
    database: ApplicationDatabaseIdentity,
    plan: MapApplication300OperationPlan,
) -> FreshRootMissingReceipt:
    try:
        result = parse_fresh_root_missing_receipt(
            raw,
            contract=candidate.application_contract,
            candidate=_application_300_execution_candidate(candidate),
        )
    except MapApplication300ContractError as exc:
        raise DeploymentContractError(
            "application 300 root missing-receipt proof is invalid"
        ) from exc
    if (
        result.operation_id != plan.operation_id
        or result.writer_fence_receipt_sha256 != plan.fence_sha256
        or result.writer_fence_transaction_id != plan.transaction_id
        or result.journal_sha256 != plan.basis_journal_sha256
        or result.journal_generation != plan.basis_journal_generation
        or result.database_identity != database
    ):
        raise DeploymentContractError(
            "application 300 root missing-receipt proof differs from plan"
        )
    return result


def _application_300_finalize_result(
    *,
    raw: bytes,
    candidate: MapApplication300Candidate,
    prior: FreshRootResult,
    plan: MapApplication300OperationPlan,
) -> FreshFinalizeResult:
    try:
        result = parse_fresh_finalize_result(
            raw,
            contract=candidate.application_contract,
            candidate=_application_300_execution_candidate(candidate),
            prior=prior,
        )
    except MapApplication300ContractError as exc:
        raise DeploymentContractError(
            "application 300 finalize result is invalid"
        ) from exc
    if (
        result.writer_fence_receipt_sha256 != plan.fence_sha256
        or result.writer_fence_transaction_id != plan.transaction_id
        or result.operation_id != plan.operation_id
        or result.journal_sha256 != plan.basis_journal_sha256
        or result.journal_generation != plan.basis_journal_generation
        or result.database_identity != prior.database_identity
    ):
        raise DeploymentContractError(
            "application 300 finalize result differs from plan"
        )
    return result


def _application_300_finalize_missing_receipt(
    *,
    raw: bytes,
    candidate: MapApplication300Candidate,
    prior: FreshRootResult,
    plan: MapApplication300OperationPlan,
) -> None:
    try:
        result = parse_fresh_finalize_missing_receipt(
            raw,
            contract=candidate.application_contract,
            candidate=_application_300_execution_candidate(candidate),
            prior=prior,
        )
    except MapApplication300ContractError as exc:
        raise DeploymentContractError(
            "application 300 finalize missing-receipt proof is invalid"
        ) from exc
    if result.operation_id != plan.operation_id:
        raise DeploymentContractError(
            "application 300 finalize missing-receipt proof differs from plan"
        )


def _application_300_plan_expired(plan: MapApplication300OperationPlan) -> bool:
    return _application_300_plan_expiry(plan) <= datetime.now(UTC)


def _application_300_renewal_expiry(
    plan: MapApplication300OperationPlan,
) -> datetime:
    """Return a time-independent renewal expiry for crash reconciliation."""

    expiry = _application_300_plan_expiry(plan) + timedelta(days=3650)
    deterministic_floor = datetime(2100, 1, 1, tzinfo=UTC)
    while expiry <= deterministic_floor:
        expiry += timedelta(days=3650)
    return expiry


def _application_300_renewal_transaction_id(
    *,
    journal: PinnedRuntimeRebuildJournal,
    plan: MapApplication300OperationPlan,
    label: Literal["root", "finalize"],
) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            ":".join(
                (
                    "kor-travel-docker-manager",
                    "map-application-300-renewal",
                    label,
                    journal.transaction_id,
                    str(journal.journal_generation),
                    plan.operation_id,
                    plan.transaction_id,
                    plan.fence_sha256,
                )
            ),
        )
    )


def _discard_application_300_receipt(path: Path) -> None:
    """Discard one exact pre-journal candidate receipt before a fresh build."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DeploymentContractError(
            "application 300 stale candidate receipt cannot be inspected"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise DeploymentContractError(
            "application 300 stale candidate receipt is unsafe"
        )
    try:
        path.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise DeploymentContractError(
            "application 300 stale candidate receipt cannot be discarded"
        ) from exc


def _run_map_application_300_paired_builder(
    *,
    sources: PinnedRuntimeSourceMaterialization,
    api_image: str,
    dagster_image: str,
    paths: _MapApplication300Paths,
    resume_journal: bool,
) -> None:
    map_source = sources.source_for("map")
    script = map_source.root / "scripts" / "build-application-300-paired-candidate.sh"
    try:
        script_metadata = script.lstat()
    except OSError as exc:
        raise DeploymentContractError(
            "application 300 paired builder is unavailable"
        ) from exc
    if (
        not stat.S_ISREG(script_metadata.st_mode)
        or stat.S_ISLNK(script_metadata.st_mode)
        or script_metadata.st_uid != os.geteuid()
    ):
        raise DeploymentContractError("application 300 paired builder is unsafe")
    receipt_presence_values: list[bool] = []
    for receipt_path in (paths.api_receipt, paths.paired_receipt):
        try:
            receipt_path.lstat()
        except FileNotFoundError:
            receipt_presence_values.append(False)
        except OSError as exc:
            raise DeploymentContractError(
                "application 300 paired receipt set cannot be inspected"
            ) from exc
        else:
            # A symlink or foreign-owned entry is still an existing partial
            # result.  Pass it to the sealed builder, whose O_NOFOLLOW and
            # exact-ownership checks must reject it rather than treating it as
            # an absent receipt that may be overwritten.
            receipt_presence_values.append(True)
    receipt_presence = tuple(receipt_presence_values)
    if resume_journal and receipt_presence != (True, True):
        raise DeploymentContractError(
            "application 300 journal resume requires a complete receipt set"
        )
    if not resume_journal and any(receipt_presence):
        # A receipt pair without a durable rebuild journal is only a pre-journal
        # candidate.  It may be left behind by a failed static inspection (or
        # by a response-loss crash) and must never silently become ``--verify``
        # evidence on the next run.  Remove only the two exact, owner-only
        # receipt paths; the sealed builder will create a fresh pair below.
        for receipt_path in (paths.api_receipt, paths.paired_receipt):
            _discard_application_300_receipt(receipt_path)
        receipt_presence = (False, False)
    if receipt_presence not in {(False, False), (True, False), (True, True)}:
        raise DeploymentContractError(
            "application 300 paired receipt set is incomplete"
        )
    command = [
        str(script),
        "--candidate-commit",
        map_source.revision,
        "--api-image",
        api_image,
        "--dagster-image",
        dagster_image,
        "--api-receipt",
        str(paths.api_receipt),
        "--receipt",
        str(paths.paired_receipt),
        "--git-root",
        str(map_source.root),
    ]
    if resume_journal and receipt_presence == (True, True):
        command.append("--verify")
    builder_environment = {
        name: value
        for name in (
            "DOCKER_CONFIG",
            "DOCKER_HOST",
            "PATH",
            "XDG_RUNTIME_DIR",
        )
        if (value := os.environ.get(name))
    }
    builder_environment["PATH"] = (
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )
    builder_environment["TMPDIR"] = "/tmp"
    try:
        completed = subprocess.run(
            command,
            cwd="/",
            env=builder_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            "application 300 paired builder could not complete"
        ) from exc
    if completed.returncode != 0:
        raise DeploymentContractError(
            "application 300 paired builder failed: "
            f"{_map_application_300_builder_failure_code(paths)}"
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


def _map_application_300_builder_failure_code(
    paths: _MapApplication300Paths,
) -> str:
    """sealed builder 출력 대신 owner-only receipt 상태만 분류한다."""

    api_status = _application_300_owner_only_receipt_status(paths.api_receipt)
    paired_status = _application_300_owner_only_receipt_status(paths.paired_receipt)
    if api_status == "missing" and paired_status == "missing":
        return "api_receipt_missing"
    if api_status == "trusted" and paired_status == "missing":
        return "paired_receipt_missing"
    return "unclassified"


def _application_300_owner_only_receipt_status(path: Path) -> str:
    """분류에 쓸 수 있는 owner-only receipt 상태만 fail-close로 관측한다."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe"
    if (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
    ):
        return "trusted"
    return "unsafe"


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
            transport_compose = _escape_materialized_compose_environment_values(
                materialized_compose
            )
            process_input = json.dumps(
                transport_compose,
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
                build_result = self._run_pinned_runtime_rebuild_compose(
                    ["build", service],
                    transaction=transaction,
                    capture_output=capture_output,
                    allow_typed_error_diagnostic=allow_typed_error_diagnostic,
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
                    if target == _PINVI_DB_RUNTIME_ROLE_SERVICE:
                        code = _PINVI_DB_RUNTIME_ROLE_ERROR_CODE_BY_LINE.get(
                            candidate.strip()
                        )
                        if code is not None:
                            return _ComposeFailureDiagnostic(
                                message_suffix=f"; pinvi_role:{code}",
                                pinvi_role_code=code,
                            )
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
                            and code in _MAP_DAGSTER_STORAGE_MIGRATION_ERROR_CODES
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
                            # 두 코드 공간(role/admin-bootstrap)이 같은 다운스트림
                            # ``_pinvi_lifecycle_diagnostic`` 판정으로 합류하므로 같은
                            # 속성에 싣는다. 두 enum이 겹치지 않아 충돌하지 않는다.
                            return _ComposeFailureDiagnostic(
                                message_suffix=f"; pinvi:{code}",
                                pinvi_role_code=code,
                            )
        if target == _PINVI_DB_RUNTIME_ROLE_SERVICE:
            return _ComposeFailureDiagnostic(
                message_suffix="; pinvi_role:unclassified",
                pinvi_role_code="unclassified",
            )
        return _ComposeFailureDiagnostic(message_suffix="")

    @staticmethod
    def _pinvi_lifecycle_diagnostic(error: BaseException) -> str:
        """이미 allowlist한 PinVi one-shot 코드만 lifecycle 오류에 보존한다.

        ``PinnedRuntimeComposeFailure``가 코드를 속성으로 실어 오면 그것을 그대로
        쓴다 — 메시지 문구·괄호 위치가 바뀌어도 판정이 깨지지 않는다. 그 타입이
        아니거나 속성이 비어 있으면(다른 경로에서 온 예외, 과거 raw 예외 등) 기존
        메시지 재파싱으로 폴백한다.
        """

        if (
            isinstance(error, PinnedRuntimeComposeFailure)
            and error.pinvi_role_diagnostic is not None
        ):
            return error.pinvi_role_diagnostic
        message = str(error)
        for code in _PINVI_DB_RUNTIME_ROLE_ERROR_CODES | {"unclassified"}:
            if f"; pinvi_role:{code})" in message:
                return code
        for code in _PINVI_ADMIN_BOOTSTRAP_ERROR_PHASE_BY_CODE:
            if f"; pinvi:{code})" in message:
                return code
        return "unclassified"

    @staticmethod
    def _pinvi_role_topology_block(
        *,
        stage: str,
        diagnostic: str,
    ) -> PinviRoleLifecycleBlock | None:
        """정확히 확인된 topology failure만 same-pinset terminal receipt로 만든다."""

        if (
            stage not in {"pinvi_role_open", "pinvi_role_seal"}
            or diagnostic != "role_topology_noncanonical"
        ):
            return None
        return PinviRoleLifecycleBlock(
            stage=cast(Literal["pinvi_role_open", "pinvi_role_seal"], stage),
            code="role_topology_noncanonical",
        )

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

    def _verify_pinned_runtime_pinvi_role_topology(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        """frozen candidate transaction에서 sealed topology JSON만 엄격히 읽는다."""

        result = self._run_pinned_runtime_rebuild_compose(
            [
                "--profile",
                "bootstrap",
                "run",
                "--rm",
                "--no-deps",
                "-e",
                "PINVI_ROLE_TOPOLOGY_VERIFY_ONLY=1",
                "-e",
                "PINVI_MIGRATOR_DISABLE_LOGIN=1",
                "-e",
                "PINVI_M05_LEGACY_REBASELINE=0",
                _PINVI_DB_RUNTIME_ROLE_SERVICE,
            ],
            transaction=transaction,
        )
        output = result.get("stdout")
        if not isinstance(output, str):
            raise DeploymentContractError(
                "PinVi sealed role topology verifier is unavailable"
            )
        try:
            payload = json.loads(
                output,
                object_pairs_hook=_json_object_without_duplicate_keys,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise DeploymentContractError(
                "PinVi sealed role topology verifier is unavailable"
            ) from exc
        if not isinstance(payload, Mapping) or set(payload) != {
            "schema",
            "status",
            "mode",
            "reasons",
        }:
            raise DeploymentContractError(
                "PinVi sealed role topology verifier is unavailable"
            )
        status = payload.get("status")
        reasons = payload.get("reasons")
        if (
            payload.get("schema") != _PINVI_ROLE_TOPOLOGY_DIAGNOSTIC_SCHEMA
            or payload.get("mode") != "sealed"
            or not isinstance(status, str)
            or not isinstance(reasons, list)
            or not all(isinstance(reason, str) for reason in reasons)
        ):
            raise DeploymentContractError(
                "PinVi sealed role topology verifier is unavailable"
            )
        if status == "canonical" and reasons == []:
            return
        if (
            status == "noncanonical"
            and reasons
            and all(
                reason in _PINVI_ROLE_TOPOLOGY_NONCANONICAL_REASONS
                for reason in reasons
            )
            and len(set(reasons)) == len(reasons)
            and tuple(reasons)
            == tuple(
                sorted(
                    reasons,
                    key=_PINVI_ROLE_TOPOLOGY_NONCANONICAL_REASONS.index,
                )
            )
        ):
            raise DeploymentContractError("PinVi sealed role topology is noncanonical")
        if (
            (status == "invalid" and reasons == ["input_invalid"])
            or (
                status == "unavailable"
                and reasons in (
                    ["endpoint_unavailable"],
                    ["verification_unavailable"],
                )
            )
        ):
            raise DeploymentContractError(
                "PinVi sealed role topology verifier is unavailable"
            )
        raise DeploymentContractError("PinVi sealed role topology verifier is unavailable")

    def _verify_pinned_runtime_pinvi_role_topology_after_bootstrap(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
    ) -> None:
        """fresh PinVi target DB의 sealed 후조건을 terminal receipt로 바꾼다.

        기존 DB는 rebuild가 폐기할 입력일 뿐 sealed runtime target이 아니다. 따라서
        role open·admin/migration bootstrap·seal 뒤의 fresh DB에만 full verifier를
        적용한다. verifier 원문과 reason enum은 receipt·CLI에 보존하지 않는다.
        """

        try:
            self._verify_pinned_runtime_pinvi_role_topology(
                transaction=transaction
            )
        except DeploymentContractError as exc:
            code: Literal[
                "role_topology_noncanonical", "role_topology_unavailable"
            ] = (
                "role_topology_noncanonical"
                if str(exc) == "PinVi sealed role topology is noncanonical"
                else "role_topology_unavailable"
            )
            raise _PinviRoleLifecycleError(
                "PinVi sealed role topology verification failed",
                role_topology_block=PinviRoleLifecycleBlock(
                    stage="pinvi_role_verify",
                    code=code,
                ),
            ) from None

    def _terminate_pinned_runtime_after_pinvi_role_topology_failure(
        self,
        *,
        journal: PinnedRuntimeRebuildJournal,
        journal_path: Path,
        transaction: ComposeTransactionSnapshot,
        error: _PinviRoleLifecycleError,
    ) -> NoReturn:
        """sealed target-state failure를 durable no-retry receipt 뒤 runtime stop으로 끝낸다."""

        self._record_pinvi_role_lifecycle_block(
            journal,
            journal_path=journal_path,
            error=error,
        )
        try:
            self._run_pinned_runtime_rebuild_compose(
                ["stop", *RUNTIME_SERVICES],
                transaction=transaction,
            )
        except DeploymentContractError as stop_error:
            raise DeploymentContractError(
                "PinVi runtime could not be stopped after sealed topology failure"
            ) from stop_error
        raise error

    def _run_pinvi_schema_bootstrap_with_role_lifecycle(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
        state_paths: PinnedRuntimeStatePaths,
        values: Mapping[str, str],
        transaction_id: str,
    ) -> None:
        """짧은 migrator login을 열어 PinVi bootstrap 뒤 반드시 다시 봉인한다."""

        role_command_prefix = [
            "--profile",
            "bootstrap",
            "run",
            "--rm",
            "--no-deps",
            "-e",
        ]
        open_role = [
            *role_command_prefix,
            "PINVI_MIGRATOR_DISABLE_LOGIN=0",
            _PINVI_DB_RUNTIME_ROLE_SERVICE,
        ]
        seal_role = [
            *role_command_prefix,
            "PINVI_MIGRATOR_DISABLE_LOGIN=1",
            _PINVI_DB_RUNTIME_ROLE_SERVICE,
        ]
        primary_stage = "pinvi_role_open"
        primary_lifecycle_error: str | None = None
        role_topology_block: PinviRoleLifecycleBlock | None = None
        try:
            self._run_pinned_runtime_rebuild_compose(open_role, transaction=transaction)
            primary_stage = "pinvi_bootstrap_credential"
            credential_context = pinvi_bootstrap_credential_file(
                state_paths=state_paths,
                values=values,
                transaction_id=transaction_id,
                email=values["KTDM_C6C_PINVI_ADMIN_EMAIL"],
                password=values["KTDM_C6C_PINVI_ADMIN_PASSWORD"],
            )
            admin_error: BaseException | None = None
            with credential_context as credential:
                try:
                    primary_stage = "pinvi_admin_bootstrap"
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
                except BaseException as exc:
                    admin_error = exc
                finally:
                    primary_stage = "pinvi_bootstrap_credential_cleanup"
            if admin_error is not None:
                primary_stage = "pinvi_admin_bootstrap"
                raise admin_error
        except BaseException as primary_error:
            primary_diagnostic = self._pinvi_lifecycle_diagnostic(primary_error)
            try:
                self._run_pinned_runtime_rebuild_compose(seal_role, transaction=transaction)
            except BaseException as seal_error:
                if not isinstance(primary_error, Exception) or not isinstance(
                    seal_error, Exception
                ):
                    raise DeploymentContractError(
                        "PinVi migrator login could not be sealed after bootstrap failure"
                    ) from seal_error
                seal_diagnostic = self._pinvi_lifecycle_diagnostic(seal_error)
                role_topology_block = self._pinvi_role_topology_block(
                    stage=primary_stage,
                    diagnostic=primary_diagnostic,
                ) or self._pinvi_role_topology_block(
                    stage="pinvi_role_seal",
                    diagnostic=seal_diagnostic,
                )
                primary_lifecycle_error = (
                    "PinVi bootstrap failed at "
                    f"{primary_stage} ({primary_diagnostic}); migrator seal also failed "
                    f"at pinvi_role_seal ({seal_diagnostic})"
                )
            else:
                if isinstance(primary_error, Exception):
                    role_topology_block = self._pinvi_role_topology_block(
                        stage=primary_stage,
                        diagnostic=primary_diagnostic,
                    )
                    primary_lifecycle_error = (
                        f"PinVi bootstrap failed at {primary_stage} ({primary_diagnostic})"
                    )
                else:
                    raise
        if primary_lifecycle_error is not None:
            raise _PinviRoleLifecycleError(
                primary_lifecycle_error,
                role_topology_block=role_topology_block,
            ) from None
        final_seal_error: str | None = None
        try:
            self._run_pinned_runtime_rebuild_compose(seal_role, transaction=transaction)
        except BaseException as seal_error:
            if not isinstance(seal_error, Exception):
                raise DeploymentContractError(
                    "PinVi migrator login could not be sealed after bootstrap"
                ) from seal_error
            seal_diagnostic = self._pinvi_lifecycle_diagnostic(seal_error)
            role_topology_block = self._pinvi_role_topology_block(
                stage="pinvi_role_seal",
                diagnostic=seal_diagnostic,
            )
            final_seal_error = (
                "PinVi migrator seal failed at "
                f"pinvi_role_seal ({seal_diagnostic})"
            )
        if final_seal_error is not None:
            raise _PinviRoleLifecycleError(
                final_seal_error,
                role_topology_block=role_topology_block,
            ) from None

    def _run_pinvi_fresh_role_catalog_reset(
        self,
        *,
        transaction: ComposeTransactionSnapshot,
        state_paths: PinnedRuntimeStatePaths,
        journal: PinnedRuntimeRebuildJournal,
        runtime: DatabaseRuntime,
    ) -> None:
        """Manager가 방금 만든 PinVi DB에만 root-owned reset permit을 발행한다."""

        identity = journal.pinvi_database_identity
        if identity is None:
            raise _PinviRoleLifecycleError(
                "PinVi fresh role catalog reset failed",
                role_topology_block=PinviRoleLifecycleBlock(
                    stage="pinvi_role_catalog_reset",
                    code="role_catalog_reset_failed",
                ),
            ) from None
        live_identity = read_pinned_database_identity(runtime)
        if not isinstance(live_identity, PinnedDatabaseIdentity):
            raise _PinviRoleLifecycleError(
                "PinVi fresh role catalog reset failed",
                role_topology_block=PinviRoleLifecycleBlock(
                    stage="pinvi_role_catalog_reset",
                    code="role_catalog_reset_failed",
                ),
            ) from None
        observed_identity = _pinned_runtime_journal_database_identity(live_identity)
        if observed_identity != identity:
            raise _PinviRoleLifecycleError(
                "PinVi fresh role catalog reset failed",
                role_topology_block=PinviRoleLifecycleBlock(
                    stage="pinvi_role_catalog_reset",
                    code="role_catalog_reset_failed",
                ),
            ) from None
        permit_path = (
            state_paths.state_root
            / f"pinvi-role-catalog-reset-{journal.candidate.pinset_sha256}.permit"
        )
        result_path = (
            state_paths.state_root
            / f"pinvi-role-catalog-reset-{journal.candidate.pinset_sha256}.result"
        )
        permit = (
            "pinvi-role-catalog-reset-v2|"
            f"{journal.transaction_id}|{journal.candidate.pinset_sha256}|"
            f"{identity.system_identifier}|{identity.oid}|{identity.name}|{identity.owner}|"
            "revoke_external_memberships\n"
        ).encode()
        result_identity: tuple[int, int] | None = None
        try:
            write_owner_only_artifact(permit_path, permit)
            write_owner_only_artifact(result_path, b"{}")
            result_metadata = result_path.lstat()
            result_identity = (result_metadata.st_dev, result_metadata.st_ino)
            self._run_pinned_runtime_rebuild_compose(
                [
                    "--profile",
                    "bootstrap",
                    "run",
                    "--rm",
                    "--no-deps",
                    "-v",
                    f"{permit_path}:/run/pinvi/role-catalog-reset.permit:ro",
                    "-v",
                    f"{result_path}:/run/pinvi/role-catalog-reset.result",
                    "-e",
                    "PINVI_ROLE_CATALOG_RESET_ONLY=1",
                    "-e",
                    "PINVI_ROLE_CATALOG_RESET_PERMIT_FILE=/run/pinvi/role-catalog-reset.permit",
                    "-e",
                    "PINVI_ROLE_CATALOG_RESET_RESULT_FILE=/run/pinvi/role-catalog-reset.result",
                    _PINVI_DB_RUNTIME_ROLE_SERVICE,
                ],
                transaction=transaction,
                capture_output=False,
                allow_typed_error_diagnostic=False,
            )
            if _read_pinvi_role_catalog_reset_result(
                result_path,
                expected_identity=result_identity,
                transaction_id=journal.transaction_id,
                pinset_sha256=journal.candidate.pinset_sha256,
            ) != "completed":
                raise DeploymentContractError("PinVi fresh role catalog reset result is invalid")
        except (DeploymentContractError, MapApplication300ContractError):
            if result_identity is None:
                diagnostic = "unclassified"
            else:
                try:
                    diagnostic = _read_pinvi_role_catalog_reset_result(
                        result_path,
                        expected_identity=result_identity,
                        transaction_id=journal.transaction_id,
                        pinset_sha256=journal.candidate.pinset_sha256,
                    )
                except MapApplication300ContractError:
                    diagnostic = "unclassified"
            raise _PinviRoleLifecycleError(
                "PinVi fresh role catalog reset failed",
                role_topology_block=PinviRoleLifecycleBlock(
                    stage="pinvi_role_catalog_reset",
                    code="role_catalog_reset_failed",
                    diagnostic=cast(PinviRoleCatalogResetDiagnostic, diagnostic),
                ),
            ) from None

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

    @staticmethod
    def _assert_committed_application_database_identities(
        runtimes: Sequence[Any],
        *,
        journal: PinnedRuntimeRebuildJournal,
        metadata_user: str,
    ) -> None:
        """committed fast path에서도 application/Dagster DB identity를 재대조한다."""

        if len(runtimes) != 3 or not metadata_user:
            raise DeploymentContractError(
                "pinned runtime committed database identity input is incomplete"
            )
        evidence = journal.map_application_300_execution_evidence
        expected_application = evidence.application_database_identity
        expected_dagster = evidence.dagster_metadata_database_identity
        if expected_application is None or expected_dagster is None:
            raise DeploymentContractError(
                "pinned runtime committed database identity evidence is incomplete"
            )
        live_application = _application_300_journal_database_identity(
            read_application_300_database_identity(runtimes[0])
        )
        if live_application != expected_application:
            raise DeploymentContractError(
                "Map application database identity differs from committed journal"
            )
        _contract_dagster, live_dagster = _application_300_dagster_identities(
            read_application_300_dagster_metadata_identity(
                runtimes[1],
                metadata_user=metadata_user,
            )
        )
        if live_dagster != expected_dagster:
            raise DeploymentContractError(
                "Map Dagster metadata identity differs from committed journal"
            )
        expected_pinvi = journal.pinvi_database_identity
        if expected_pinvi is None:
            raise DeploymentContractError(
                "PinVi database identity evidence is incomplete"
            )
        live_pinvi = _pinned_runtime_journal_database_identity(
            read_pinned_database_identity(runtimes[2])
        )
        if live_pinvi != expected_pinvi:
            raise DeploymentContractError(
                "PinVi database identity differs from committed journal"
            )

    def _assert_committed_postgres_images(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        transaction: ComposeTransactionSnapshot,
        map_candidate: MapApplication300Candidate,
    ) -> None:
        """두 PostgreSQL container를 frozen Compose와 paired Map image에 결박한다."""

        services = transaction.resolved.get("services")
        if not isinstance(services, Mapping):
            raise DeploymentContractError(
                "pinned runtime resolved PostgreSQL services are invalid"
            )
        expected_records = {
            "kor-travel-map-postgres": map_candidate.postgres_image_id,
        }
        pinvi_service = services.get("pinvi-postgres")
        pinvi_reference = (
            pinvi_service.get("image") if isinstance(pinvi_service, Mapping) else None
        )
        if (
            not isinstance(pinvi_reference, str)
            or re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", pinvi_reference) is None
        ):
            raise DeploymentContractError(
                "PinVi PostgreSQL resolved image is not digest-pinned"
            )
        expected_records["pinvi-postgres"] = self._inspect_image_reference_id(
            pinvi_reference,
            label="PinVi PostgreSQL",
        )
        observed: dict[str, str] = {}
        for record in records:
            service = record.get("Service")
            name = record.get("Name")
            if (
                not isinstance(service, str)
                or service not in expected_records
                or service in observed
                or not isinstance(name, str)
                or not name
            ):
                raise DeploymentContractError(
                    "pinned runtime PostgreSQL container evidence is invalid"
                )
            observed[service] = self._inspect_container_image_id(
                name,
                label=service,
            )
        if set(observed) != set(expected_records):
            raise DeploymentContractError(
                "pinned runtime PostgreSQL container evidence is incomplete"
            )
        for service, expected_image in expected_records.items():
            if observed[service] != expected_image:
                raise DeploymentContractError(
                    f"{service} runtime image differs from committed generation"
                )

    def _assert_pinned_runtime_container_images(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        journal: PinnedRuntimeRebuildJournal,
    ) -> None:
        """실행 중인 seven-service container를 committed exact image에 결박한다."""

        expected_images = journal.candidate.image_ids
        if len(records) != len(RUNTIME_SERVICES):
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
            if observed_image != expected_images[cast(RuntimeService, service)]:
                raise DeploymentContractError(
                    f"{service} runtime image differs from committed generation"
                )
        if observed_services != set(RUNTIME_SERVICES):
            raise DeploymentContractError(
                "pinned runtime container image evidence is incomplete"
            )

    def _attest_pinned_runtime_candidate_images(
        self,
        *,
        build: CandidateRuntimeBuild,
        map_candidate: MapApplication300Candidate,
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

    def _load_application_300_paired_candidate(
        self,
        *,
        sources: PinnedRuntimeSourceMaterialization,
        paths: _MapApplication300Paths,
    ) -> MapApplication300Candidate:
        map_source = sources.source_for("map")

        def attest_image(role: str, image_id: str) -> ImmutableImageObservation:
            if role == "map_postgres":
                try:
                    observed_id = self._inspect_image_reference_id(
                        image_id,
                        label="Map PostgreSQL",
                    )
                except DeploymentContractError:
                    try:
                        pulled = subprocess.run(
                            ["docker", "pull", _MAP_APPLICATION_300_POSTGRES_REFERENCE],
                            cwd="/",
                            text=True,
                            capture_output=True,
                            check=False,
                            timeout=900,
                        )
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise DeploymentContractError(
                            "Map PostgreSQL candidate image is unavailable"
                        ) from exc
                    if pulled.returncode != 0:
                        raise DeploymentContractError(
                            "Map PostgreSQL candidate image is unavailable"
                        ) from None
                    observed_id = self._inspect_image_reference_id(
                        image_id,
                        label="Map PostgreSQL",
                    )
                return ImmutableImageObservation(
                    available=True,
                    image_id=observed_id,
                    oci_revision=None,
                )
            if role not in {"map_api", "map_dagster"}:
                raise DeploymentContractError(
                    "application 300 image role is invalid"
                )
            observed_id = self._inspect_image_reference_id(image_id, label=role)
            observed_revision = self._inspect_image_source_revision(
                observed_id,
                label=role,
            )
            return ImmutableImageObservation(
                available=True,
                image_id=observed_id,
                oci_revision=observed_revision,
            )

        try:
            return load_map_application_300_candidate(
                paths.paired_receipt,
                paths.api_receipt,
                expected_candidate_commit=map_source.revision,
                expected_candidate_tree=map_source.tree,
                attest_image=attest_image,
            )
        except MapApplication300CandidateError as exc:
            raise DeploymentContractError(
                "application 300 paired candidate attestation failed"
            ) from exc

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
    def _assert_pinvi_role_credential_rebind_admission(
        journal: PinnedRuntimeRebuildJournal,
        *,
        environment_bytes: bytes,
        values: Mapping[str, str],
    ) -> str | None:
        """root `.env` write 전에 current v8 resume과의 유일한 재결박을 판정한다."""

        current_environment_sha256 = hashlib.sha256(environment_bytes).hexdigest()
        rebind_source_sha256 = rebind_source_environment_sha256(values)
        if journal.environment_sha256 == current_environment_sha256:
            if (
                pinvi_role_credentials_are_all_undeclared(values)
                and (
                    journal.phase != "map_runtime_ready"
                    or journal.pinvi_role_credential_environment_rebind is not None
                )
            ):
                raise DeploymentContractError(
                    "PinVi role credentials cannot rebind this pinned runtime journal"
                )
            if pinvi_role_credentials_are_all_undeclared(values):
                return journal.environment_sha256
            return None
        if (
            rebind_source_sha256 != journal.environment_sha256
            or journal.phase != "map_runtime_ready"
            or journal.pinvi_role_credential_environment_rebind is not None
        ):
            raise DeploymentContractError(
                "PinVi role credentials differ from the pinned runtime journal"
            )
        return None

    @staticmethod
    def _assert_pinvi_role_lifecycle_block_admission(
        journal: PinnedRuntimeRebuildJournal,
    ) -> None:
        """terminal role topology receipt가 있으면 어떤 same-pinset write도 시작하지 않는다."""

        if journal.pinvi_role_lifecycle_block is not None or (
            is_blocked_pinset_retry(
                pinset_sha256=journal.candidate.pinset_sha256,
                map_source_revision=journal.candidate.map_source_revision,
                pinvi_source_revision=journal.candidate.pinvi_source_revision,
                phase=journal.phase,
            )
        ):
            raise DeploymentContractError(
                "pinned runtime rebuild is blocked by durable PinVi role topology failure"
            )

    @staticmethod
    def _record_pinvi_role_lifecycle_block(
        journal: PinnedRuntimeRebuildJournal,
        *,
        journal_path: Path,
        error: _PinviRoleLifecycleError,
    ) -> PinnedRuntimeRebuildJournal:
        """확정 topology failure를 같은 v8 journal에 먼저 fsync한다."""

        if error.role_topology_block is None:
            return journal
        updated = journal.with_pinvi_role_lifecycle_block(error.role_topology_block)
        write_pinned_runtime_rebuild_journal(journal_path, updated)
        return updated

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
    def _assert_pinned_runtime_journal_matches_map_candidate(
        journal: PinnedRuntimeRebuildJournal,
        *,
        map_candidate: MapApplication300Candidate,
    ) -> None:
        """resume receipt/image evidence must be the journal's exact Map pair."""

        evidence = journal.map_application_300_candidate_evidence
        if (
            evidence.paired_receipt_sha256 != map_candidate.receipt_sha256
            or evidence.api_receipt_sha256 != map_candidate.api_receipt_sha256
            or evidence.candidate_git_tree != map_candidate.candidate_git_tree
            or evidence.postgres_image_id != map_candidate.postgres_image_id
            or evidence.dagster_config_sha256 != map_candidate.dagster_config_sha256
            or evidence.dagster_yaml_sha256 != map_candidate.dagster_yaml_sha256
            or evidence.application_contract_sha256
            != map_candidate.application_contract_sha256
            or evidence.launch_contract_sha256 != map_candidate.launch_contract_sha256
            or journal.candidate.map_api_image_id != map_candidate.api_image_id
            or journal.candidate.map_dagster_image_id != map_candidate.dagster_image_id
        ):
            raise DeploymentContractError(
                "pinned runtime journal differs from current Map paired candidate"
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
        # `transition` 자체가 enum order를 검증한다. 이미 더 먼 checkpoint이면
        # 재개 과정의 read-only 검증만 허용하고 high-watermark를 되돌리지 않는다.
        if REBUILD_PHASES.index(journal.phase) > REBUILD_PHASES.index(phase):
            return journal
        if REBUILD_PHASES.index(journal.phase) + 1 != REBUILD_PHASES.index(phase):
            raise DeploymentContractError("pinned runtime rebuild phase is inconsistent")
        return journal.transition(phase)

    def _converge_application_300_database_bootstrap(
        self,
        *,
        journal: PinnedRuntimeRebuildJournal,
        runtime: DatabaseRuntime,
        transaction: ComposeTransactionSnapshot,
        journal_path: Path,
    ) -> tuple[PinnedRuntimeRebuildJournal, ApplicationDatabaseIdentity]:
        """createdb/bootstrap crash를 durable phase와 exact catalog로 수렴한다."""

        if journal.phase == "databases_recreated":
            updated = journal.with_application_create_intent()
            write_pinned_runtime_rebuild_journal(journal_path, updated)
            journal = updated

        if journal.phase == "application_create_intent_durable":
            create_state = inspect_application_300_bootstrap_state(runtime)
            if create_state == "absent":
                create_fresh_application_300_database(runtime)
                create_state = inspect_application_300_bootstrap_state(runtime)
            if create_state != "virgin":
                raise DeploymentContractError(
                    "application 300 create result is not an exact virgin database"
                )
            runtime_create_identity = read_application_300_database_identity(runtime)
            if runtime_create_identity.database_owner != runtime.owner_name:
                raise DeploymentContractError(
                    "application 300 create result owner differs from contract"
                )
            journal_create_identity = _application_300_journal_database_identity(
                runtime_create_identity
            )
            updated = journal.with_application_created(
                application_create_database_identity=journal_create_identity
            )
            write_pinned_runtime_rebuild_journal(journal_path, updated)
            journal = updated

        if journal.phase == "application_created":
            if inspect_application_300_bootstrap_state(runtime) != "virgin":
                raise DeploymentContractError(
                    "application 300 database changed before role bootstrap intent"
                )
            runtime_create_identity = read_application_300_database_identity(runtime)
            journal_create_identity = _application_300_journal_database_identity(
                runtime_create_identity
            )
            if (
                journal.map_application_300_execution_evidence
                .application_create_database_identity
                != journal_create_identity
            ):
                raise DeploymentContractError(
                    "application 300 create result differs from journal"
                )
            updated = journal.with_application_bootstrap_intent()
            write_pinned_runtime_rebuild_journal(journal_path, updated)
            journal = updated

        if journal.phase == "application_bootstrap_intent_durable":
            bootstrap_state = inspect_application_300_bootstrap_state(runtime)
            if bootstrap_state == "virgin":
                self._run_pinned_runtime_rebuild_compose(
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
                    transaction=transaction,
                )
                bootstrap_state = inspect_application_300_bootstrap_state(runtime)
            if bootstrap_state != "exact_complete":
                raise DeploymentContractError(
                    "application 300 role bootstrap result is not exact"
                )
            runtime_application_identity = read_application_300_database_identity(
                runtime
            )
            application_database, journal_application_database = (
                _application_300_database_identities(runtime_application_identity)
            )
            updated = journal.with_application_roles_ready(
                application_database_identity=journal_application_database
            )
            write_pinned_runtime_rebuild_journal(journal_path, updated)
            return updated, application_database

        runtime_application_identity = read_application_300_database_identity(runtime)
        application_database, journal_application_database = (
            _application_300_database_identities(runtime_application_identity)
        )
        expected_application_identity = (
            journal.map_application_300_execution_evidence
            .application_database_identity
        )
        if (
            expected_application_identity is None
            or expected_application_identity != journal_application_database
        ):
            raise DeploymentContractError(
                "application 300 database identity differs from journal"
            )
        return journal, application_database

    def _renew_fresh_root_operation_plan(
        self,
        *,
        journal: PinnedRuntimeRebuildJournal,
        plan: MapApplication300OperationPlan,
        map_candidate: MapApplication300Candidate,
        execution_candidate: Application300ExecutionCandidate,
        application_database: ApplicationDatabaseIdentity,
        application_paths: _MapApplication300Paths,
        journal_path: Path,
    ) -> tuple[PinnedRuntimeRebuildJournal, MapApplication300OperationPlan]:
        renewed_plan, root_fence_raw = self._build_fresh_root_renewal(
            journal=journal,
            plan=plan,
            map_candidate=map_candidate,
            execution_candidate=execution_candidate,
            application_database=application_database,
        )
        try:
            replace_root_read_only_artifact(
                application_paths.root_fence,
                expected_old_sha256=plan.fence_sha256,
                raw=root_fence_raw,
            )
        except MapApplication300ContractError as exc:
            raise DeploymentContractError(
                "application 300 root fence renewal failed"
            ) from exc
        updated = journal.with_renewed_fresh_root_execution_intent(
            fresh_root_operation_plan=renewed_plan
        )
        write_pinned_runtime_rebuild_journal(journal_path, updated)
        return updated, renewed_plan

    def _build_fresh_root_renewal(
        self,
        *,
        journal: PinnedRuntimeRebuildJournal,
        plan: MapApplication300OperationPlan,
        map_candidate: MapApplication300Candidate,
        execution_candidate: Application300ExecutionCandidate,
        application_database: ApplicationDatabaseIdentity,
    ) -> tuple[MapApplication300OperationPlan, bytes]:
        renewal_basis_sha256 = rebuild_journal_sha256(journal)
        renewal_generation = journal.journal_generation
        renewal_expiry = _application_300_renewal_expiry(plan)
        renewal_transaction_id = _application_300_renewal_transaction_id(
            journal=journal,
            plan=plan,
            label="root",
        )
        try:
            root_fence = build_fresh_migration_fence(
                contract=map_candidate.application_contract,
                candidate=execution_candidate,
                database=application_database,
                journal=JournalStamp(
                    transaction_id=renewal_transaction_id,
                    operation_id=plan.operation_id,
                    journal_sha256=renewal_basis_sha256,
                    journal_generation=renewal_generation,
                ),
                writer_fence_expires_at=renewal_expiry,
            )
            renewed_plan = MapApplication300OperationPlan(
                transaction_id=renewal_transaction_id,
                operation_id=plan.operation_id,
                basis_journal_sha256=renewal_basis_sha256,
                basis_journal_generation=renewal_generation,
                writer_fence_expires_at=renewal_expiry.isoformat(),
                fence_sha256=root_fence.sha256,
            )
        except MapApplication300ContractError as exc:
            raise DeploymentContractError(
                "application 300 root fence renewal failed"
            ) from exc
        return renewed_plan, root_fence.raw

    def _reconcile_expired_fresh_root_fence(
        self,
        *,
        journal: PinnedRuntimeRebuildJournal,
        plan: MapApplication300OperationPlan,
        map_candidate: MapApplication300Candidate,
        execution_candidate: Application300ExecutionCandidate,
        application_database: ApplicationDatabaseIdentity,
        application_paths: _MapApplication300Paths,
        journal_path: Path,
    ) -> tuple[PinnedRuntimeRebuildJournal, MapApplication300OperationPlan]:
        """Converge a fence-first renewal crash before consuming a probe."""

        if not _application_300_plan_expired(plan):
            return journal, plan
        try:
            renewed_plan, renewed_fence_raw = self._build_fresh_root_renewal(
                journal=journal,
                plan=plan,
                map_candidate=map_candidate,
                execution_candidate=execution_candidate,
                application_database=application_database,
            )
        except DeploymentContractError:
            return journal, plan
        try:
            current_fence_raw = read_root_read_only_artifact(application_paths.root_fence)
        except (FileNotFoundError, MapApplication300ContractError):
            # Let the typed probe inspect the same mounted artifact.  A missing or
            # unsafe file can never satisfy the old-plan binding, so this remains
            # fail-closed while preserving the recovery proof path.
            return journal, plan
        if current_fence_raw == renewed_fence_raw:
            updated = journal.with_renewed_fresh_root_execution_intent(
                fresh_root_operation_plan=renewed_plan
            )
            write_pinned_runtime_rebuild_journal(journal_path, updated)
            return updated, renewed_plan
        if hashlib.sha256(current_fence_raw).hexdigest() == plan.fence_sha256:
            return journal, plan
        # Unknown bytes are deliberately not adopted.  The probe below must bind to
        # the old durable plan and will reject them before any root re-execution.
        return journal, plan

    def _renew_fresh_finalize_operation_plan(
        self,
        *,
        journal: PinnedRuntimeRebuildJournal,
        plan: MapApplication300OperationPlan,
        map_candidate: MapApplication300Candidate,
        execution_candidate: Application300ExecutionCandidate,
        application_database: ApplicationDatabaseIdentity,
        application_paths: _MapApplication300Paths,
        journal_path: Path,
        root_result: FreshRootResult,
    ) -> tuple[PinnedRuntimeRebuildJournal, MapApplication300OperationPlan]:
        renewed_plan, finalize_fence_raw = self._build_fresh_finalize_renewal(
            journal=journal,
            plan=plan,
            map_candidate=map_candidate,
            execution_candidate=execution_candidate,
            application_database=application_database,
            root_result=root_result,
        )
        try:
            replace_root_read_only_artifact(
                application_paths.finalize_fence,
                expected_old_sha256=plan.fence_sha256,
                raw=finalize_fence_raw,
            )
        except (MapApplication300ContractError, AttributeError) as exc:
            raise DeploymentContractError(
                "application 300 finalize fence renewal failed"
            ) from exc
        updated = journal.with_renewed_fresh_finalize_execution_intent(
            fresh_finalize_operation_plan=renewed_plan
        )
        write_pinned_runtime_rebuild_journal(journal_path, updated)
        return updated, renewed_plan

    def _build_fresh_finalize_renewal(
        self,
        *,
        journal: PinnedRuntimeRebuildJournal,
        plan: MapApplication300OperationPlan,
        map_candidate: MapApplication300Candidate,
        execution_candidate: Application300ExecutionCandidate,
        application_database: ApplicationDatabaseIdentity,
        root_result: FreshRootResult,
    ) -> tuple[MapApplication300OperationPlan, bytes]:
        renewal_basis_sha256 = rebuild_journal_sha256(journal)
        renewal_generation = journal.journal_generation
        renewal_expiry = _application_300_renewal_expiry(plan)
        renewal_transaction_id = _application_300_renewal_transaction_id(
            journal=journal,
            plan=plan,
            label="finalize",
        )
        try:
            finalize_fence = build_fresh_finalize_fence(
                contract=map_candidate.application_contract,
                candidate=execution_candidate,
                database=application_database,
                journal=JournalStamp(
                    transaction_id=renewal_transaction_id,
                    operation_id=plan.operation_id,
                    journal_sha256=renewal_basis_sha256,
                    journal_generation=renewal_generation,
                ),
                prior=root_result,
                writer_fence_expires_at=renewal_expiry,
            )
            renewed_plan = MapApplication300OperationPlan(
                transaction_id=renewal_transaction_id,
                operation_id=plan.operation_id,
                basis_journal_sha256=renewal_basis_sha256,
                basis_journal_generation=renewal_generation,
                writer_fence_expires_at=renewal_expiry.isoformat(),
                fence_sha256=finalize_fence.sha256,
            )
        except (MapApplication300ContractError, AttributeError) as exc:
            raise DeploymentContractError(
                "application 300 finalize fence renewal failed"
            ) from exc
        return renewed_plan, finalize_fence.raw

    def _reconcile_expired_fresh_finalize_fence(
        self,
        *,
        journal: PinnedRuntimeRebuildJournal,
        plan: MapApplication300OperationPlan,
        map_candidate: MapApplication300Candidate,
        execution_candidate: Application300ExecutionCandidate,
        application_database: ApplicationDatabaseIdentity,
        application_paths: _MapApplication300Paths,
        journal_path: Path,
        root_result: FreshRootResult,
    ) -> tuple[PinnedRuntimeRebuildJournal, MapApplication300OperationPlan]:
        """Converge a fence-first finalize renewal crash before consuming a probe."""

        if not _application_300_plan_expired(plan):
            return journal, plan
        try:
            renewed_plan, renewed_fence_raw = self._build_fresh_finalize_renewal(
                journal=journal,
                plan=plan,
                map_candidate=map_candidate,
                execution_candidate=execution_candidate,
                application_database=application_database,
                root_result=root_result,
            )
        except DeploymentContractError:
            return journal, plan
        try:
            current_fence_raw = read_root_read_only_artifact(
                application_paths.finalize_fence
            )
        except (FileNotFoundError, MapApplication300ContractError):
            # See the root reconciliation path: the strict probe remains the
            # authority for missing/unsafe mounted bytes.
            return journal, plan
        if current_fence_raw == renewed_fence_raw:
            updated = journal.with_renewed_fresh_finalize_execution_intent(
                fresh_finalize_operation_plan=renewed_plan
            )
            write_pinned_runtime_rebuild_journal(journal_path, updated)
            return updated, renewed_plan
        if hashlib.sha256(current_fence_raw).hexdigest() == plan.fence_sha256:
            return journal, plan
        return journal, plan

    def rebuild_pinned_runtime(self) -> dict[str, Any]:
        """application-300 paired candidate에 결박된 destructive rebuild를 실행한다."""

        _require_pinned_runtime_rebuild_root()
        release: PinnedRuntimeRelease | None = None
        resume_journal: PinnedRuntimeRebuildJournal | None = None

        def prewrite_admission(
            environment_snapshot: ComposeEnvironmentSnapshot,
        ) -> str | None:
            nonlocal release, resume_journal
            # global mutation lock을 잡은 뒤 하나의 registry snapshot을 만들고 즉시
            # source/v6 execution gate를 확인한다. rotate가 두 read 사이에 끼어 old
            # release와 new execution을 섞는 TOCTOU를 막는다.
            release = current_pinned_runtime_release()
            _assert_pinset_is_not_permanently_blocked(release.pinset_sha256)
            state_paths = pinned_runtime_state_paths(
                environment_snapshot.effective,
                pinset_sha256=release.pinset_sha256,
            )
            try:
                state_paths.journal.lstat()
            except FileNotFoundError:
                return None
            resume_journal = read_pinned_runtime_rebuild_journal(state_paths.journal)
            self._assert_pinvi_role_lifecycle_block_admission(resume_journal)
            return self._assert_pinvi_role_credential_rebind_admission(
                resume_journal,
                environment_bytes=environment_snapshot.env_file_bytes,
                values=environment_snapshot.effective,
            )

        with _pinned_runtime_rebuild_environment_lock(
            prewrite_admission=prewrite_admission
        ) as (
            lock_snapshot,
            environment_snapshot,
            _role_credentials_initialized,
        ):
            if release is None:  # pragma: no cover - context contract 방어
                raise DeploymentContractError("pinned runtime release snapshot is unavailable")
            # application head 300은 paired receipt와 설치된 baseline contract가
            # 정본이다. Dagster/PinVi head만 network-less candidate 명령으로 읽는다.
            with _pinned_runtime_prejournal_step("state_initialization"):
                validate_c6c_operation_tokens(
                    environment_snapshot.effective,
                    require_nonempty=True,
                )
                state_paths = pinned_runtime_state_paths(
                    environment_snapshot.effective,
                    pinset_sha256=release.pinset_sha256,
                )
                ensure_pinned_runtime_state_directory(state_paths.state_root)
                application_paths = _map_application_300_paths(
                    state_root=state_paths.state_root,
                    pinset_sha256=release.pinset_sha256,
                )
                artifact_directories = MapApplication300ArtifactDirectories(
                    fresh_migrate_fence=application_paths.root_fence_directory,
                    fresh_finalize_fence=application_paths.finalize_fence_directory,
                    application_final_permit=(
                        application_paths.application_permit_directory
                    ),
                    dagster_storage_permit=application_paths.metadata_permit_directory,
                )
            with _pinned_runtime_prejournal_step("prebuild_snapshot"):
                prebuild_transaction, _ = self._capture_transaction_unlocked(
                    environment_override=dict(artifact_directories.compose_environment()),
                    environment_snapshot=environment_snapshot,
                )
                _assert_transaction_matches_c6c_lock(
                    prebuild_transaction, lock_snapshot
                )
            # 외부 prerequisite는 source materialize, paired builder, image tag,
            # receipt/journal write보다 먼저 확인한다. fixed artifact directory만
            # base candidate가 volume graph를 검증할 수 있도록 먼저 준비한다.
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
                    values=environment_snapshot.effective,
                )
            journal_exists = resume_journal is not None
            paired_build_images = map_application_300_paired_build_image_names(sources)
            with _pinned_runtime_prejournal_step("application_base_images"):
                _ensure_map_application_300_python_base_images(sources)
            with _pinned_runtime_prejournal_step("application_builder"):
                _run_map_application_300_paired_builder(
                    sources=sources,
                    api_image=paired_build_images["kor-travel-map-api"],
                    dagster_image=paired_build_images["kor-travel-map-dagster"],
                    paths=application_paths,
                    resume_journal=journal_exists,
                )
            with _pinned_runtime_prejournal_step("application_candidate"):
                map_candidate = self._load_application_300_paired_candidate(
                    sources=sources,
                    paths=application_paths,
                )
            build = CandidateRuntimeBuild(
                sources=sources,
                map_application_300_candidate=map_candidate,
            )
            candidate_build_references = {
                **paired_build_images,
                **build.image_names,
            }
            candidate_environment = {
                **build.compose_environment(),
                **artifact_directories.compose_environment(),
                "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": (
                    map_candidate.application_contract.application_head
                ),
            }
            with _pinned_runtime_prejournal_step("candidate_snapshot"):
                candidate_transaction, _ = self._capture_transaction_unlocked(
                    environment_override=candidate_environment,
                    environment_snapshot=environment_snapshot,
                )
                _assert_transaction_matches_c6c_lock(
                    candidate_transaction, lock_snapshot
                )
            with _pinned_runtime_prejournal_step("candidate_contract"):
                self._validate_pinned_runtime_candidate_build_contract(
                    candidate_transaction,
                    build=build,
                    environment_override=candidate_environment,
                )
            # Geo·Concierge·RustFS는 F1D가 소유하지 않는 선행 runtime이다. 후보를
            # build하거나 journal을 쓰기 전에 frozen Compose의 read-only ``ps``로
            # exact readiness를 증명하고, 부재/불건강이면 시작·변경 없이 닫는다.
            with _pinned_runtime_prejournal_step("external_prerequisites"):
                self._require_services_ready(
                    _PINNED_RUNTIME_EXTERNAL_PREREQUISITES,
                    transaction=candidate_transaction,
                    frozen_recovery=True,
                )
            if journal_exists:
                journal = cast(PinnedRuntimeRebuildJournal, resume_journal)
                if journal.phase == "committed":
                    manifest = read_pinned_runtime_manifest(state_paths.manifest)
                    if manifest.active_generation != journal.candidate:
                        raise DeploymentContractError(
                            "pinned runtime manifest differs from committed journal"
                        )
                self._assert_pinned_runtime_journal_matches_map_candidate(
                    journal,
                    map_candidate=map_candidate,
                )
                self._attest_pinned_runtime_candidate_images(
                    build=build,
                    map_candidate=map_candidate,
                )
                ensure_generation_references((journal.candidate,), cwd=get_project_root())
            else:
                self._run_pinned_runtime_rebuild_compose(
                    ["build", *COMPOSE_BUILT_RUNTIME_SERVICES],
                    transaction=candidate_transaction,
                )
                image_ids = self._attest_pinned_runtime_candidate_images(
                    build=build,
                    map_candidate=map_candidate,
                )
                self._verify_pinned_runtime_pinvi_bootstrap_settings(
                    transaction=candidate_transaction,
                )
                map_application_output = _run_pinned_runtime_static_command(
                    image_ids["kor-travel-map-api"],
                    ("head",),
                    label="Map application",
                    entrypoint="/usr/local/bin/ktm-application-schema",
                )
                map_application_head = parse_candidate_static_head(
                    map_application_output,
                    schema="kor-travel-map.application-head.v1",
                    field="head",
                )
                map_dagster_output = _run_pinned_runtime_static_command(
                    image_ids["kor-travel-map-dagster"],
                    ("head",),
                    label="Map Dagster",
                    entrypoint="/usr/local/bin/ktm-dagster-storage",
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
                    map_application_300_candidate=map_candidate,
                    image_ids=image_ids,
                    map_application_head=map_application_head,
                    map_dagster_head=map_dagster_head,
                    pinvi_head=pinvi_head,
                )

            candidate_generation = journal.candidate if journal_exists else candidate
            runtime_environment = {
                **build.compose_environment(),
                **generation_compose_environment(
                    candidate_generation,
                    artifact_directories=artifact_directories,
                ),
                "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": (
                    candidate_generation.map_application_head
                ),
            }
            runtime_transaction, _ = self._capture_transaction_unlocked(
                environment_override=runtime_environment,
                environment_snapshot=environment_snapshot,
            )
            _assert_transaction_matches_c6c_lock(runtime_transaction, lock_snapshot)
            if journal_exists:
                current_environment_sha256 = hashlib.sha256(
                    environment_snapshot.env_file_bytes
                ).hexdigest()
                if journal.environment_sha256 != current_environment_sha256:
                    rebind_source_sha256 = rebind_source_environment_sha256(
                        environment_snapshot.effective
                    )
                    if rebind_source_sha256 is None:
                        raise DeploymentContractError(
                            "PinVi role credential rebind source is missing"
                        )
                    journal = journal.with_pinvi_role_credential_environment_rebind(
                        previous_environment_sha256=rebind_source_sha256,
                        compose_sha256=hashlib.sha256(
                            runtime_transaction.compose_source_bytes
                        ).hexdigest(),
                        current_environment_sha256=current_environment_sha256,
                        current_resolved_compose_sha256=(
                            runtime_transaction.resolved_document_hash
                        ),
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, journal)
                self._assert_pinned_runtime_journal_matches_candidate_input(
                    journal,
                    release_pinset_sha256=release.pinset_sha256,
                    map_revision=release.source_for("map").revision,
                    pinvi_revision=release.source_for("pinvi").revision,
                    environment_bytes=environment_snapshot.env_file_bytes,
                    compose_source_bytes=runtime_transaction.compose_source_bytes,
                    resolved_compose_sha256=runtime_transaction.resolved_document_hash,
                )
            else:
                journal = new_candidate_journal(
                    candidate=candidate,
                    environment_bytes=environment_snapshot.env_file_bytes,
                    compose_source_bytes=runtime_transaction.compose_source_bytes,
                    resolved_compose_sha256=runtime_transaction.resolved_document_hash,
                )
                write_pinned_runtime_rebuild_journal(state_paths.journal, journal)
                ensure_generation_references((candidate,), cwd=get_project_root())

            # legacy journal이 이미 남았더라도 tombstone write/unlink 사이의 crash 또는
            # 검증 실패를 건너뛰면 안 된다. idempotent receipt 검증은 runtime/DB
            # mutation 전에 매 실행한다.
            retire_f1d_legacy_artifacts(
                state_root=state_paths.state_root,
                transaction_id=journal.transaction_id,
                candidate=journal.candidate,
                recorded_at=journal.created_at,
            )

            runtimes = database_runtimes_from_frozen_contract(
                resolved=runtime_transaction.resolved,
                environment=runtime_transaction.environment.effective,
            )
            resumed = journal_exists
            if journal.phase == "committed":
                runtime_records = self._require_services_ready(
                    RUNTIME_SERVICES,
                    transaction=runtime_transaction,
                    frozen_recovery=True,
                )
                self._assert_pinned_runtime_container_images(
                    runtime_records,
                    journal=journal,
                )
                postgres_records = self._require_services_ready(
                    ("kor-travel-map-postgres", "pinvi-postgres"),
                    transaction=runtime_transaction,
                    frozen_recovery=True,
                )
                self._assert_committed_postgres_images(
                    postgres_records,
                    transaction=runtime_transaction,
                    map_candidate=map_candidate,
                )
                validate_map_postgres_runtime_secret_isolation(
                    self._inspect_container_runtime_config(
                        str(postgres_records[0]["Name"])
                    )
                )
                validate_pinvi_postgres_runtime_secret_isolation(
                    self._inspect_container_runtime_config(
                        str(postgres_records[1]["Name"])
                    )
                )
                self._retire_pinned_runtime_oneshot_writers(
                    transaction=runtime_transaction,
                )
                reconcile_orphaned_pinvi_bootstrap_credentials(
                    state_paths=state_paths,
                    values=environment_snapshot.effective,
                    global_mutation_lock_held=True,
                    all_one_shot_containers_absent=True,
                )
                self._assert_pinned_runtime_database_heads(runtimes, journal=journal)
                metadata_user = runtime_transaction.environment.effective.get(
                    "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER"
                )
                if not isinstance(metadata_user, str):
                    raise DeploymentContractError(
                        "Map Dagster metadata user is unavailable"
                    )
                self._assert_committed_application_database_identities(
                    runtimes,
                    journal=journal,
                    metadata_user=metadata_user,
                )
                config = load_c6c_deployment_config_from_environment(
                    runtime_transaction.environment.effective
                )
                if isinstance(config, C6cDeploymentConfig):
                    runtime_configs = self._inspect_c6c_runtime_configs(
                        config,
                        list(RUNTIME_SERVICES),
                        transaction=runtime_transaction,
                        frozen_recovery=True,
                    )
                    validate_runtime_secret_isolation(runtime_configs, config)
                    validate_current_map_ui_auth_runtime(
                        runtime_configs[config.map_ui_container],
                        config,
                    )
                reconcile_generation_references(
                    (journal.candidate,),
                    cwd=get_project_root(),
                )
                reconcile_candidate_build_references(
                    candidate_build_references,
                    journal.candidate,
                    cwd=get_project_root(),
                )
                return self._pinned_runtime_result(journal, resumed=True)
            try:
                if _pinned_runtime_reset_required(journal):
                    updated = self._advance_pinned_runtime_journal(
                        journal, "reset_intent_durable"
                    )
                    if updated != journal:
                        write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                        journal = updated

                # fixture receipt가 있는 resume도 Map/PinVi writer와 one-shot
                # bootstrap을 정지·부재 검증한 뒤에만 controlled startup으로 간다.
                # fixture outcome만 보존하며, live runtime/partial writer를
                # 재사용하지는 않는다.
                self._run_pinned_runtime_rebuild_compose(
                    ["stop", *RUNTIME_SERVICES],
                    transaction=runtime_transaction,
                )
                self._retire_pinned_runtime_oneshot_writers(
                    transaction=runtime_transaction,
                )
                reconcile_orphaned_pinvi_bootstrap_credentials(
                    state_paths=state_paths,
                    values=environment_snapshot.effective,
                    global_mutation_lock_held=True,
                    all_one_shot_containers_absent=True,
                )

                # Map 두 DB runtime은 shared PostgreSQL이 아니라 #171 전용 instance에,
                # PinVi DB는 별도 instance에 있다. reset 전에 두 PostgreSQL을 frozen
                # Compose로 health까지 보장해 `docker exec`가 존재하지 않는 container를
                # 향하거나 shared DB로 fallback하지 않게 한다.
                self._run_pinned_runtime_rebuild_compose(
                    [
                        "up",
                        "-d",
                        "--no-deps",
                        "--wait",
                        "--wait-timeout",
                        str(_COMPOSE_WAIT_TIMEOUT_SECONDS),
                        "kor-travel-map-postgres",
                        "pinvi-postgres",
                    ],
                    transaction=runtime_transaction,
                )
                # raw/resolved Compose는 secret alias mount를 고정하지만, 실제
                # long-lived container가 legacy password Env를 보존하지 않았는지도
                # destructive DB reset 전에 Docker inspect로 fail-close한다.
                map_postgres_records = self._require_services_ready(
                    ("kor-travel-map-postgres", "pinvi-postgres"),
                    transaction=runtime_transaction,
                    frozen_recovery=True,
                )
                validate_map_postgres_runtime_secret_isolation(
                    self._inspect_container_runtime_config(
                        str(map_postgres_records[0]["Name"])
                    )
                )
                validate_pinvi_postgres_runtime_secret_isolation(
                    self._inspect_container_runtime_config(
                        str(map_postgres_records[1]["Name"])
                    )
                )
                observed_map_postgres_image = self._inspect_container_image_id(
                    str(map_postgres_records[0]["Name"]),
                    label="Map PostgreSQL",
                )
                if observed_map_postgres_image != map_candidate.postgres_image_id:
                    raise DeploymentContractError(
                        "Map PostgreSQL runtime image differs from paired candidate"
                    )

                reset_required = _pinned_runtime_reset_required(journal)
                if reset_required:
                    reset_databases_for_application_300(runtimes)
                    pinvi_database_identity = _pinned_runtime_journal_database_identity(
                        read_pinned_database_identity(runtimes[2])
                    )
                    updated = journal.with_databases_recreated(
                        pinvi_database_identity=pinvi_database_identity
                    )
                    if updated != journal:
                        write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                        journal = updated
                else:
                    expected_pinvi_database_identity = journal.pinvi_database_identity
                    if expected_pinvi_database_identity is None:
                        raise DeploymentContractError(
                            "pinned runtime journal has no PinVi database identity"
                        )
                    live_pinvi_database_identity = (
                        _pinned_runtime_journal_database_identity(
                            read_pinned_database_identity(runtimes[2])
                        )
                    )
                    if (
                        live_pinvi_database_identity
                        != expected_pinvi_database_identity
                    ):
                        raise DeploymentContractError(
                            "PinVi database identity differs from rebuild journal"
                        )

                journal, application_database = (
                    self._converge_application_300_database_bootstrap(
                        journal=journal,
                        runtime=runtimes[0],
                        transaction=runtime_transaction,
                        journal_path=state_paths.journal,
                    )
                )

                execution_candidate = _application_300_execution_candidate(
                    map_candidate
                )
                root_plan: MapApplication300OperationPlan | None = None
                if journal.phase == "application_roles_ready":
                    root_transaction_id = str(uuid4())
                    root_operation_id = str(uuid4())
                    root_expiry = datetime.now(UTC) + timedelta(hours=2)
                    root_basis_sha256 = rebuild_journal_sha256(journal)
                    try:
                        root_fence = build_fresh_migration_fence(
                            contract=map_candidate.application_contract,
                            candidate=execution_candidate,
                            database=application_database,
                            journal=JournalStamp(
                                transaction_id=root_transaction_id,
                                operation_id=root_operation_id,
                                journal_sha256=root_basis_sha256,
                                journal_generation=journal.journal_generation,
                            ),
                            writer_fence_expires_at=root_expiry,
                        )
                    except MapApplication300ContractError as exc:
                        raise DeploymentContractError(
                            "application 300 root plan is invalid"
                        ) from exc
                    root_plan = MapApplication300OperationPlan(
                        transaction_id=root_transaction_id,
                        operation_id=root_operation_id,
                        basis_journal_sha256=root_basis_sha256,
                        basis_journal_generation=journal.journal_generation,
                        writer_fence_expires_at=root_expiry.isoformat(),
                        fence_sha256=root_fence.sha256,
                    )
                    updated = journal.with_fresh_root_plan_ready(
                        fresh_root_operation_plan=root_plan
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                root_plan = (
                    journal.map_application_300_execution_evidence
                    .fresh_root_operation_plan
                )
                if root_plan is None:
                    raise DeploymentContractError(
                        "application 300 root plan is missing"
                    )
                if journal.phase == "fresh_root_plan_ready":
                    root_fence_raw = _application_300_root_fence(
                        candidate=map_candidate,
                        database=application_database,
                        plan=root_plan,
                    )
                    try:
                        publish_root_read_only_artifact(
                            application_paths.root_fence,
                            root_fence_raw,
                        )
                    except MapApplication300ContractError as exc:
                        raise DeploymentContractError(
                            "application 300 root fence cannot be published"
                        ) from exc
                    updated = journal.with_fresh_root_fence_ready(
                        fresh_root_operation_plan=root_plan
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                if journal.phase == "fresh_root_fence_ready":
                    updated = journal.with_fresh_root_execution_intent(
                        fresh_root_operation_plan=root_plan
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                if journal.phase == "fresh_root_execution_intent":
                    try:
                        application_paths.root_result.lstat()
                        root_result_raw = read_owner_only_artifact(
                            application_paths.root_result
                        )
                    except FileNotFoundError:
                        try:
                            root_recover_command = (
                                self._run_pinned_runtime_rebuild_compose(
                                    _application_300_profile_operation_args(
                                        service=_MAP_APPLICATION_FRESH_300_SERVICE,
                                        executable=_MAP_APPLICATION_FRESH_300_EXECUTABLE,
                                        operation="recover",
                                        operation_id=root_plan.operation_id,
                                    ),
                                    transaction=runtime_transaction,
                                )
                            )
                            root_recover_stdout = root_recover_command.get("stdout")
                            if not isinstance(root_recover_stdout, str):
                                raise DeploymentContractError(
                                    "application 300 root recovery output is invalid"
                                )
                            root_result_raw = root_recover_stdout.encode("utf-8")
                        except DeploymentContractError:
                            try:
                                journal, root_plan = (
                                    self._reconcile_expired_fresh_root_fence(
                                        journal=journal,
                                        plan=root_plan,
                                        map_candidate=map_candidate,
                                        execution_candidate=execution_candidate,
                                        application_database=application_database,
                                        application_paths=application_paths,
                                        journal_path=state_paths.journal,
                                    )
                                )
                                missing_probe_command = (
                                    self._run_pinned_runtime_rebuild_compose(
                                        _application_300_profile_operation_args(
                                            service=_MAP_APPLICATION_FRESH_300_SERVICE,
                                            executable=_MAP_APPLICATION_FRESH_300_EXECUTABLE,
                                            operation="probe-missing",
                                            operation_id=root_plan.operation_id,
                                        ),
                                        transaction=runtime_transaction,
                                    )
                                )
                                missing_probe_stdout = missing_probe_command.get(
                                    "stdout"
                                )
                                if not isinstance(missing_probe_stdout, str):
                                    raise DeploymentContractError(
                                        "application 300 root missing-receipt "
                                        "proof output is invalid"
                                    )
                                _application_300_root_missing_receipt(
                                    raw=missing_probe_stdout.encode("utf-8"),
                                    candidate=map_candidate,
                                    database=application_database,
                                    plan=root_plan,
                                )
                            except DeploymentContractError as probe_error:
                                raise DeploymentContractError(
                                    "application 300 root execution result is uncertain"
                                ) from probe_error
                            if _application_300_plan_expired(root_plan):
                                journal, root_plan = (
                                    self._renew_fresh_root_operation_plan(
                                        journal=journal,
                                        plan=root_plan,
                                        map_candidate=map_candidate,
                                        execution_candidate=execution_candidate,
                                        application_database=application_database,
                                        application_paths=application_paths,
                                        journal_path=state_paths.journal,
                                    )
                                )
                            root_command = self._run_pinned_runtime_rebuild_compose(
                                [
                                    "--profile",
                                    "bootstrap",
                                    "run",
                                    "--rm",
                                    "--no-deps",
                                    _MAP_APPLICATION_FRESH_300_SERVICE,
                                ],
                                transaction=runtime_transaction,
                            )
                            root_stdout = root_command.get("stdout")
                            if not isinstance(root_stdout, str):
                                raise DeploymentContractError(
                                    "application 300 root result output is invalid"
                                ) from None
                            root_result_raw = root_stdout.encode("utf-8")
                    except MapApplication300ContractError as exc:
                        raise DeploymentContractError(
                            "application 300 root result artifact is invalid"
                        ) from exc
                    root_result = _application_300_root_result(
                        raw=root_result_raw,
                        candidate=map_candidate,
                        database=application_database,
                        plan=root_plan,
                    )
                    try:
                        write_owner_only_artifact(
                            application_paths.root_result,
                            root_result_raw,
                        )
                    except MapApplication300ContractError as exc:
                        raise DeploymentContractError(
                            "application 300 root result cannot be persisted"
                        ) from exc
                    root_plan = root_plan.with_result(root_result.payload_sha256)
                    updated = journal.with_fresh_root_ready(
                        fresh_root_operation_plan=root_plan
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                root_plan = (
                    journal.map_application_300_execution_evidence
                    .fresh_root_operation_plan
                )
                if root_plan is None or root_plan.result_sha256 is None:
                    raise DeploymentContractError(
                        "application 300 root result is missing"
                    )
                try:
                    root_result_raw = read_owner_only_artifact(
                        application_paths.root_result,
                        expected_sha256=root_plan.result_sha256,
                    )
                except MapApplication300ContractError as exc:
                    raise DeploymentContractError(
                        "application 300 root result artifact is invalid"
                    ) from exc
                root_result = _application_300_root_result(
                    raw=root_result_raw,
                    candidate=map_candidate,
                    database=application_database,
                    plan=root_plan,
                )

                finalize_plan: MapApplication300OperationPlan | None = None
                if journal.phase == "fresh_root_ready":
                    finalize_transaction_id = str(uuid4())
                    finalize_operation_id = str(uuid4())
                    finalize_expiry = datetime.now(UTC) + timedelta(hours=2)
                    finalize_basis_sha256 = rebuild_journal_sha256(journal)
                    try:
                        finalize_fence = build_fresh_finalize_fence(
                            contract=map_candidate.application_contract,
                            candidate=execution_candidate,
                            database=application_database,
                            journal=JournalStamp(
                                transaction_id=finalize_transaction_id,
                                operation_id=finalize_operation_id,
                                journal_sha256=finalize_basis_sha256,
                                journal_generation=journal.journal_generation,
                            ),
                            prior=root_result,
                            writer_fence_expires_at=finalize_expiry,
                        )
                    except MapApplication300ContractError as exc:
                        raise DeploymentContractError(
                            "application 300 finalize plan is invalid"
                        ) from exc
                    finalize_plan = MapApplication300OperationPlan(
                        transaction_id=finalize_transaction_id,
                        operation_id=finalize_operation_id,
                        basis_journal_sha256=finalize_basis_sha256,
                        basis_journal_generation=journal.journal_generation,
                        writer_fence_expires_at=finalize_expiry.isoformat(),
                        fence_sha256=finalize_fence.sha256,
                    )
                    updated = journal.with_fresh_finalize_plan_ready(
                        fresh_finalize_operation_plan=finalize_plan
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                finalize_plan = (
                    journal.map_application_300_execution_evidence
                    .fresh_finalize_operation_plan
                )
                if finalize_plan is None:
                    raise DeploymentContractError(
                        "application 300 finalize plan is missing"
                    )
                if journal.phase == "fresh_finalize_plan_ready":
                    finalize_fence_raw = _application_300_finalize_fence(
                        candidate=map_candidate,
                        database=application_database,
                        prior=root_result,
                        plan=finalize_plan,
                    )
                    try:
                        publish_root_read_only_artifact(
                            application_paths.finalize_fence,
                            finalize_fence_raw,
                        )
                    except MapApplication300ContractError as exc:
                        raise DeploymentContractError(
                            "application 300 finalize fence cannot be published"
                        ) from exc
                    updated = journal.with_fresh_finalize_fence_ready(
                        fresh_finalize_operation_plan=finalize_plan
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                if journal.phase == "fresh_finalize_fence_ready":
                    updated = journal.with_fresh_finalize_execution_intent(
                        fresh_finalize_operation_plan=finalize_plan
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                if journal.phase == "fresh_finalize_execution_intent":
                    try:
                        application_paths.finalize_result.lstat()
                        finalize_result_raw = read_owner_only_artifact(
                            application_paths.finalize_result
                        )
                    except FileNotFoundError:
                        try:
                            finalize_recover_command = (
                                self._run_pinned_runtime_rebuild_compose(
                                    _application_300_profile_operation_args(
                                        service=_MAP_APPLICATION_FRESH_FINALIZE_SERVICE,
                                        executable=_MAP_APPLICATION_FRESH_FINALIZE_EXECUTABLE,
                                        operation="recover",
                                        operation_id=finalize_plan.operation_id,
                                    ),
                                    transaction=runtime_transaction,
                                )
                            )
                            finalize_recover_stdout = finalize_recover_command.get(
                                "stdout"
                            )
                            if not isinstance(finalize_recover_stdout, str):
                                raise DeploymentContractError(
                                    "application 300 finalize recovery output is invalid"
                                )
                            finalize_result_raw = finalize_recover_stdout.encode(
                                "utf-8"
                            )
                        except DeploymentContractError:
                            try:
                                journal, finalize_plan = (
                                    self._reconcile_expired_fresh_finalize_fence(
                                        journal=journal,
                                        plan=finalize_plan,
                                        map_candidate=map_candidate,
                                        execution_candidate=execution_candidate,
                                        application_database=application_database,
                                        application_paths=application_paths,
                                        journal_path=state_paths.journal,
                                        root_result=root_result,
                                    )
                                )
                                missing_probe_command = (
                                    self._run_pinned_runtime_rebuild_compose(
                                        _application_300_profile_operation_args(
                                            service=_MAP_APPLICATION_FRESH_FINALIZE_SERVICE,
                                            executable=_MAP_APPLICATION_FRESH_FINALIZE_EXECUTABLE,
                                            operation="probe-missing",
                                            operation_id=finalize_plan.operation_id,
                                        ),
                                        transaction=runtime_transaction,
                                    )
                                )
                                missing_probe_stdout = missing_probe_command.get(
                                    "stdout"
                                )
                                if not isinstance(missing_probe_stdout, str):
                                    raise DeploymentContractError(
                                        "application 300 finalize missing-receipt "
                                        "proof output is invalid"
                                    )
                                _application_300_finalize_missing_receipt(
                                    raw=missing_probe_stdout.encode("utf-8"),
                                    candidate=map_candidate,
                                    prior=root_result,
                                    plan=finalize_plan,
                                )
                            except DeploymentContractError as probe_error:
                                raise DeploymentContractError(
                                    "application 300 finalize execution result is uncertain"
                                ) from probe_error
                            if _application_300_plan_expired(finalize_plan):
                                journal, finalize_plan = (
                                    self._renew_fresh_finalize_operation_plan(
                                        journal=journal,
                                        plan=finalize_plan,
                                        map_candidate=map_candidate,
                                        execution_candidate=execution_candidate,
                                        application_database=application_database,
                                        application_paths=application_paths,
                                        journal_path=state_paths.journal,
                                        root_result=root_result,
                                    )
                                )
                            finalize_command = self._run_pinned_runtime_rebuild_compose(
                                [
                                    "--profile",
                                    "bootstrap",
                                    "run",
                                    "--rm",
                                    "--no-deps",
                                    _MAP_APPLICATION_FRESH_FINALIZE_SERVICE,
                                ],
                                transaction=runtime_transaction,
                            )
                            finalize_stdout = finalize_command.get("stdout")
                            if not isinstance(finalize_stdout, str):
                                raise DeploymentContractError(
                                    "application 300 finalize result output is invalid"
                                ) from None
                            finalize_result_raw = finalize_stdout.encode("utf-8")
                    except MapApplication300ContractError as exc:
                        raise DeploymentContractError(
                            "application 300 finalize result artifact is invalid"
                        ) from exc
                    finalize_result = _application_300_finalize_result(
                        raw=finalize_result_raw,
                        candidate=map_candidate,
                        prior=root_result,
                        plan=finalize_plan,
                    )
                    try:
                        write_owner_only_artifact(
                            application_paths.finalize_result,
                            finalize_result_raw,
                        )
                    except MapApplication300ContractError as exc:
                        raise DeploymentContractError(
                            "application 300 finalize result cannot be persisted"
                        ) from exc
                    finalize_plan = finalize_plan.with_result(
                        finalize_result.payload_sha256
                    )
                    updated = journal.with_fresh_finalize_ready(
                        fresh_finalize_operation_plan=finalize_plan
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                finalize_plan = (
                    journal.map_application_300_execution_evidence
                    .fresh_finalize_operation_plan
                )
                if finalize_plan is None or finalize_plan.result_sha256 is None:
                    raise DeploymentContractError(
                        "application 300 finalize result is missing"
                    )
                try:
                    finalize_result_raw = read_owner_only_artifact(
                        application_paths.finalize_result,
                        expected_sha256=finalize_plan.result_sha256,
                    )
                except MapApplication300ContractError as exc:
                    raise DeploymentContractError(
                        "application 300 finalize result artifact is invalid"
                    ) from exc
                finalize_result = _application_300_finalize_result(
                    raw=finalize_result_raw,
                    candidate=map_candidate,
                    prior=root_result,
                    plan=finalize_plan,
                )
                finalize_recover_command = self._run_pinned_runtime_rebuild_compose(
                    _application_300_profile_operation_args(
                        service=_MAP_APPLICATION_FRESH_FINALIZE_SERVICE,
                        executable=_MAP_APPLICATION_FRESH_FINALIZE_EXECUTABLE,
                        operation="recover",
                        operation_id=finalize_plan.operation_id,
                    ),
                    transaction=runtime_transaction,
                )
                finalize_recover_stdout = finalize_recover_command.get("stdout")
                if not isinstance(finalize_recover_stdout, str):
                    raise DeploymentContractError(
                        "application 300 finalize recovery output is invalid"
                    )
                finalize_recover_raw = finalize_recover_stdout.encode("utf-8")
                _application_300_finalize_result(
                    raw=finalize_recover_raw,
                    candidate=map_candidate,
                    prior=root_result,
                    plan=finalize_plan,
                )
                if finalize_recover_raw != finalize_result_raw:
                    raise DeploymentContractError(
                        "application 300 finalize recovery differs from stored result"
                    )

                try:
                    application_permit = build_application_final_permit(
                        contract=map_candidate.application_contract,
                        candidate=execution_candidate,
                        database=application_database,
                        finalize_result=finalize_result,
                    )
                    publish_root_read_only_artifact(
                        application_paths.application_permit,
                        application_permit.raw,
                    )
                except MapApplication300ContractError as exc:
                    raise DeploymentContractError(
                        "application 300 final permit is invalid"
                    ) from exc
                if journal.phase == "fresh_finalize_ready":
                    updated = journal.with_application_permit_ready(
                        app_final_permit_sha256=application_permit.sha256
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                elif (
                    journal.map_application_300_execution_evidence
                    .app_final_permit_sha256
                    != application_permit.sha256
                ):
                    raise DeploymentContractError(
                        "application 300 final permit differs from journal"
                    )

                metadata_user = runtime_transaction.environment.effective.get(
                    "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER"
                )
                metadata_password = runtime_transaction.environment.effective.get(
                    "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD"
                )
                if not isinstance(metadata_user, str) or not isinstance(
                    metadata_password, str
                ):
                    raise DeploymentContractError(
                        "Map Dagster metadata credentials are unavailable"
                    )
                if journal.phase == "application_permit_ready":
                    try:
                        runtime_dagster_identity = (
                            read_application_300_dagster_metadata_identity(
                                runtimes[1],
                                metadata_user=metadata_user,
                            )
                        )
                    except DeploymentContractError:
                        runtime_dagster_identity = (
                            initialize_application_300_dagster_metadata_database(
                                runtimes[1],
                                metadata_user=metadata_user,
                                metadata_password=metadata_password,
                            )
                        )
                else:
                    runtime_dagster_identity = (
                        read_application_300_dagster_metadata_identity(
                            runtimes[1],
                            metadata_user=metadata_user,
                        )
                    )
                dagster_database, journal_dagster_database = (
                    _application_300_dagster_identities(runtime_dagster_identity)
                )
                dagster_storage_candidate = DagsterStorageCandidate(
                    dagster_image_id=map_candidate.dagster_image_id,
                    paired_candidate_build_receipt_sha256=(
                        map_candidate.receipt_sha256
                    ),
                    dagster_config_sha256=map_candidate.dagster_yaml_sha256,
                )
                try:
                    metadata_permit = build_dagster_metadata_permit(
                        candidate=dagster_storage_candidate,
                        dagster_database=dagster_database,
                        application_database=application_database,
                        operation_id=journal.transaction_id,
                    )
                    publish_root_read_only_artifact(
                        application_paths.metadata_permit,
                        metadata_permit.raw,
                    )
                except MapApplication300ContractError as exc:
                    raise DeploymentContractError(
                        "Map Dagster metadata permit is invalid"
                    ) from exc
                if journal.phase == "application_permit_ready":
                    updated = journal.with_metadata_permit_ready(
                        dagster_metadata_database_identity=journal_dagster_database,
                        metadata_permit_sha256=metadata_permit.sha256,
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                else:
                    expected_dagster_identity = (
                        journal.map_application_300_execution_evidence
                        .dagster_metadata_database_identity
                    )
                    if (
                        expected_dagster_identity != journal_dagster_database
                        or journal.map_application_300_execution_evidence
                        .metadata_permit_sha256
                        != metadata_permit.sha256
                    ):
                        raise DeploymentContractError(
                            "Map Dagster metadata permit differs from journal"
                        )

                self._run_pinned_runtime_rebuild_compose(
                    [
                        "up",
                        "-d",
                        "--no-deps",
                        "--wait",
                        "--wait-timeout",
                        str(_COMPOSE_WAIT_TIMEOUT_SECONDS),
                        "kor-travel-map-api",
                    ],
                    transaction=runtime_transaction,
                )
                if (
                    read_database_schema_revision(runtimes[0])
                    != journal.candidate.map_application_head
                ):
                    raise DeploymentContractError(
                        "Map application schema differs from candidate head"
                    )
                if journal.phase == "metadata_permit_ready":
                    updated = journal.with_map_application_ready()
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated

                if journal.phase == "map_application_ready":
                    updated = self._advance_pinned_runtime_journal(
                        journal,
                        "map_dagster_storage_intent_durable",
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                if journal.phase == "map_dagster_storage_intent_durable":
                    storage_command = self._run_pinned_runtime_rebuild_compose(
                        [
                            "run",
                            "--rm",
                            "--no-deps",
                            "kor-travel-map-dagster-storage-migrate",
                        ],
                        transaction=runtime_transaction,
                    )
                    storage_stdout = storage_command.get("stdout")
                    if not isinstance(storage_stdout, str):
                        raise DeploymentContractError(
                            "Map Dagster storage receipt output is invalid"
                        )
                    try:
                        storage_receipt = json.loads(
                            storage_stdout,
                            object_pairs_hook=_json_object_without_duplicate_keys,
                        )
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise DeploymentContractError(
                            "Map Dagster storage receipt output is invalid"
                        ) from exc
                    _validate_map_dagster_storage_receipt(
                        storage_receipt,
                        journal=journal,
                        candidate=map_candidate,
                    )
                    try:
                        observed_dagster_head = read_database_schema_revision(
                            runtimes[1]
                        )
                    except DeploymentContractError as exc:
                        raise DeploymentContractError(
                            "Map Dagster storage execution result is uncertain"
                        ) from exc
                    if observed_dagster_head != journal.candidate.map_dagster_head:
                        raise DeploymentContractError(
                            "Map Dagster storage execution result is uncertain"
                        )
                    updated = self._advance_pinned_runtime_journal(
                        journal,
                        "map_dagster_ready",
                    )
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                elif (
                    read_database_schema_revision(runtimes[1])
                    != journal.candidate.map_dagster_head
                ):
                    raise DeploymentContractError(
                        "Map Dagster schema differs from candidate head"
                    )

                self._run_pinned_runtime_rebuild_compose(
                    [
                        "up",
                        "-d",
                        "--no-deps",
                        "--wait",
                        "--wait-timeout",
                        str(_COMPOSE_WAIT_TIMEOUT_SECONDS),
                        "kor-travel-map-ui",
                        "kor-travel-map-dagster",
                        "kor-travel-map-dagster-daemon",
                    ],
                    transaction=runtime_transaction,
                )
                updated = self._advance_pinned_runtime_journal(
                    journal, "map_runtime_ready"
                )
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                if journal.phase == "map_runtime_ready":
                    try:
                        if journal.pinvi_role_catalog_reset is None:
                            raise _PinviRoleLifecycleError(
                                "PinVi fresh role catalog reset receipt is missing",
                                role_topology_block=PinviRoleLifecycleBlock(
                                    stage="pinvi_role_catalog_reset",
                                    code="role_catalog_reset_failed",
                                ),
                            )
                        if journal.pinvi_role_catalog_reset.state == "intent":
                            if not reset_required:
                                raise _PinviRoleLifecycleError(
                                    "PinVi fresh role catalog reset outcome is ambiguous",
                                    role_topology_block=PinviRoleLifecycleBlock(
                                        stage="pinvi_role_catalog_reset",
                                        code="role_catalog_reset_failed",
                                    ),
                                )
                            self._run_pinvi_fresh_role_catalog_reset(
                                transaction=runtime_transaction,
                                state_paths=state_paths,
                                journal=journal,
                                runtime=runtimes[2],
                            )
                            updated = journal.with_pinvi_role_catalog_reset_completed()
                            write_pinned_runtime_rebuild_journal(
                                state_paths.journal, updated
                            )
                            journal = updated
                        self._run_pinvi_schema_bootstrap_with_role_lifecycle(
                            transaction=runtime_transaction,
                            state_paths=state_paths,
                            values=environment_snapshot.effective,
                            transaction_id=journal.transaction_id,
                        )
                    except _PinviRoleLifecycleError as exc:
                        journal = self._record_pinvi_role_lifecycle_block(
                            journal,
                            journal_path=state_paths.journal,
                            error=exc,
                        )
                        raise
                if read_database_schema_revision(runtimes[2]) != journal.candidate.pinvi_head:
                    raise DeploymentContractError("PinVi schema differs from candidate head")
                try:
                    # sealed verifier는 기존 DB admission이 아니라 fresh target-state
                    # 후조건이다. open → bootstrap/migration → seal과 head 검증 뒤에만
                    # 실행하고 raw verifier output은 durable receipt에 남기지 않는다.
                    self._verify_pinned_runtime_pinvi_role_topology_after_bootstrap(
                        transaction=runtime_transaction,
                    )
                except _PinviRoleLifecycleError as exc:
                    self._terminate_pinned_runtime_after_pinvi_role_topology_failure(
                        journal=journal,
                        journal_path=state_paths.journal,
                        transaction=runtime_transaction,
                        error=exc,
                    )
                updated = self._advance_pinned_runtime_journal(
                    journal, "pinvi_schema_ready"
                )
                if updated != journal:
                    write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                    journal = updated
                self._run_pinned_runtime_rebuild_compose(
                    [
                        "up",
                        "-d",
                        "--no-deps",
                        "--wait",
                        "--wait-timeout",
                        str(_COMPOSE_WAIT_TIMEOUT_SECONDS),
                        "pinvi-api",
                    ],
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
                if REBUILD_PHASES.index(journal.phase) < REBUILD_PHASES.index(
                    "cancel_probe_finalized"
                ):
                    config = load_c6c_deployment_config_from_environment(
                        runtime_transaction.environment.effective
                    )
                    cancel_probe_state = _pinvi_cancel_probe_state_from_journal(journal)

                    def record_cancel_probe_state(state: PinviCancelProbeState) -> None:
                        nonlocal journal
                        updated = journal.with_cancel_probe(
                            _cancel_probe_receipt_from_pinvi_state(state)
                        )
                        if updated != journal:
                            write_pinned_runtime_rebuild_journal(
                                state_paths.journal,
                                updated,
                            )
                            journal = updated

                    run_pinvi_canonical_smoke(
                        config,
                        cancel_probe_state=cancel_probe_state,
                        state_recorder=record_cancel_probe_state,
                    )
                    updated = self._advance_pinned_runtime_journal(
                        journal,
                        "cancel_probe_finalized",
                    )
                    if updated != journal:
                        write_pinned_runtime_rebuild_journal(state_paths.journal, updated)
                        journal = updated
                self._run_pinned_runtime_rebuild_compose(
                    [
                        "up",
                        "-d",
                        "--no-deps",
                        "--wait",
                        "--wait-timeout",
                        str(_COMPOSE_WAIT_TIMEOUT_SECONDS),
                        "pinvi-web",
                        "pinvi-dagster",
                    ],
                    transaction=runtime_transaction,
                )
                runtime_records = self._require_services_ready(
                    RUNTIME_SERVICES,
                    transaction=runtime_transaction,
                    frozen_recovery=True,
                )
                self._assert_pinned_runtime_container_images(
                    runtime_records,
                    journal=journal,
                )
                config = load_c6c_deployment_config_from_environment(
                    runtime_transaction.environment.effective
                )
                if isinstance(config, C6cDeploymentConfig):
                    runtime_configs = self._inspect_c6c_runtime_configs(
                        config,
                        list(RUNTIME_SERVICES),
                        transaction=runtime_transaction,
                        frozen_recovery=True,
                    )
                    validate_runtime_secret_isolation(runtime_configs, config)
                    validate_current_map_ui_auth_runtime(
                        runtime_configs[config.map_ui_container],
                        config,
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
                    PinnedRuntimeManifest(version=6, active_generation=journal.candidate),
                )
                reconcile_generation_references(
                    (journal.candidate,),
                    cwd=get_project_root(),
                )
                reconcile_candidate_build_references(
                    candidate_build_references,
                    journal.candidate,
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
                    reconcile_orphaned_pinvi_bootstrap_credentials(
                        state_paths=state_paths,
                        values=environment_snapshot.effective,
                        global_mutation_lock_held=True,
                        all_one_shot_containers_absent=True,
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
