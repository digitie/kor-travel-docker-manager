from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_cutover import (
    read_owner_only_state,
    write_cutover_state,
)

WindowPhase = Literal[
    "prepared",
    "writers_fencing",
    "writers_draining",
    "writers_drained",
    "writers_stopping",
    "writers_fenced",
    "backups_committed",
    "candidate_built",
    "pin_preflight_verified",
    "map_preflight_verified",
    "map_database_forwarded",
    "databases_forwarded",
    "csv_forwarded",
    "generation_bootstrapped",
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
    "rollback_preparing",
    "new_runtime_stopped",
    "map_db_restored",
    "map_dagster_db_restored",
    "pinvi_db_restored",
    "manager_state_restored",
    "writers_restored",
    "old_runtime_restored",
    "rolled_back",
]
MapHelperOperation = Literal["preflight", "migrate", "csv5", "gc", "verify"]
PinBoundaryOperation = Literal["preflight", "finalize"]
WindowFailureClass = Literal["contract_violation", "unexpected_error"]
JsonScalar = str | int | bool | None
JsonEvidence = JsonScalar | tuple[str, ...]

FORWARD_PHASES: tuple[WindowPhase, ...] = (
    "prepared",
    "writers_fencing",
    "writers_draining",
    "writers_drained",
    "writers_stopping",
    "writers_fenced",
    "backups_committed",
    "candidate_built",
    "pin_preflight_verified",
    "map_preflight_verified",
    "map_database_forwarded",
    "databases_forwarded",
    "csv_forwarded",
    "generation_bootstrapped",
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
)
ROLLBACK_PHASES: tuple[WindowPhase, ...] = (
    "rollback_preparing",
    "new_runtime_stopped",
    "map_db_restored",
    "map_dagster_db_restored",
    "pinvi_db_restored",
    "manager_state_restored",
    "writers_restored",
    "old_runtime_restored",
    "rolled_back",
)
TERMINAL_PHASES = frozenset({"runtime_activated", "rolled_back"})
_JOURNAL_FIELDS = frozenset(
    {
        "version",
        "transaction_id",
        "cutover_id",
        "phase",
        "expected_restore_epoch",
        "reason_sha256",
        "environment_sha256",
        "compose_sha256",
        "resolved_compose_sha256",
        "old_manifest_sha256",
        "writer_drain_lease_id",
        "writer_drain_receipt_sha256",
        "writer_drain_restore_receipt_sha256",
        "initial_writer_fence_sha256",
        "final_writer_fence_sha256",
        "final_map_write_counters_sha256",
        "map_final_evidence",
        "map_final_evidence_sha256",
        "gc_receipt_sha256",
        "pin_preflight_receipt_sha256",
        "pin_migration_receipt_sha256",
        "rollback_bundle_sha256",
        "map_application_backup",
        "map_dagster_backup",
        "pinvi_backup",
        "candidate_pair_sha256",
        "last_map_receipt",
        "last_map_receipt_sha256",
        "initial_receipt_sha256",
        "pin_final_receipt_sha256",
        "external_event_count",
        "forward_boundary",
        "failure_stage",
        "failure_class",
    }
)
_BACKUP_FIELDS = frozenset(
    {
        "transaction_id",
        "database_identity",
        "schema_revision",
        "logical_backup_id",
        "byte_size",
        "sha256",
        "schema_inventory_sha256",
        "data_inventory_sha256",
        "writer_fence_sha256",
        "writer_mutation_count",
        "restore_rehearsal",
    }
)
_REHEARSAL_FIELDS = frozenset(
    {
        "transaction_id",
        "database_identity",
        "source_database_identity",
        "archive_sha256",
        "schema_revision",
        "schema_inventory_sha256",
        "data_inventory_sha256",
        "verified",
    }
)
_HELPER_FIELDS = frozenset(
    {
        "contract_version",
        "operation",
        "transaction_id",
        "status",
        "source_revision",
        "database_identity",
        "request_digest",
        "prior_receipt_digest",
        "schema_before",
        "schema_after",
        "forward_boundary",
        "row_counts",
        "checks",
        "cache_target_evidence",
        "runtime_mutation_count",
        "external_event_count",
    }
)
_CHECK_FIELDS = frozenset({"name", "expected", "observed", "passed"})
_PIN_BOUNDARY_REQUEST_FIELDS = frozenset(
    {
        "contract_version",
        "operation",
        "transaction_id",
        "cutover_id",
        "source_revision",
        "database_identity",
        "writer_registry_sha256",
        "initial_writer_fence_sha256",
        "final_writer_fence_sha256",
        "prior_receipt_sha256",
        "canary_run_id",
        "map_final_evidence",
        "map_final_evidence_sha256",
    }
)
_PIN_BOUNDARY_RECEIPT_FIELDS = _PIN_BOUNDARY_REQUEST_FIELDS | frozenset(
    {
        "status",
        "schema_revision",
        "pending_command_count",
        "leased_command_count",
        "dead_letter_command_count",
        "in_flight_command_count",
        "database_in_flight_transaction_count",
        "email_queue_pending_count",
        "telegram_outbox_pending_count",
        "location_audit_outbox_pending_count",
        "expected_initial_command_count",
        "expected_initial_event_count",
        "expected_initial_claim_item_count",
        "expected_synthetic_command_count",
        "expected_synthetic_event_count",
        "expected_synthetic_claim_count",
        "unexpected_generation7_command_count",
        "unexpected_non_synthetic_event_count",
        "unexpected_non_synthetic_claim_count",
        "initial_evidence_sha256",
        "canary_provenance_sha256",
        "final_local_remote_evidence_sha256",
        "evidence_sha256",
        "runtime_mutation_count",
        "external_mutation_count",
        "audit_id",
        "audit_request_sha256",
        "audit_row_count",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_BACKUP_SCHEMA_REVISION = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_MAP_SCHEMA_REVISION = re.compile(r"^[0-9]{4}_[a-z0-9_]+$")
_PIN_WRITER_REGISTRY_SHA256 = (
    "526240609e2919357699b90244eb8cc8b9505f37db6c60552a98c7a37ed22d7c"
)


@dataclass(frozen=True)
class DatabaseRestoreRehearsalReceipt:
    transaction_id: str
    database_identity: str
    source_database_identity: str
    archive_sha256: str
    schema_revision: str
    schema_inventory_sha256: str
    data_inventory_sha256: str
    verified: Literal[True]


@dataclass(frozen=True)
class DatabaseBackupReceipt:
    transaction_id: str
    database_identity: str
    schema_revision: str
    logical_backup_id: str
    byte_size: int
    sha256: str
    schema_inventory_sha256: str
    data_inventory_sha256: str
    writer_fence_sha256: str
    writer_mutation_count: Literal[0]
    restore_rehearsal: DatabaseRestoreRehearsalReceipt


@dataclass(frozen=True)
class CacheTargetWindowJournal:
    version: Literal[2]
    transaction_id: str
    cutover_id: str
    phase: WindowPhase
    expected_restore_epoch: int
    reason_sha256: str
    environment_sha256: str
    compose_sha256: str
    resolved_compose_sha256: str
    old_manifest_sha256: str
    writer_drain_lease_id: str | None = None
    writer_drain_receipt_sha256: str | None = None
    writer_drain_restore_receipt_sha256: str | None = None
    initial_writer_fence_sha256: str | None = None
    final_writer_fence_sha256: str | None = None
    final_map_write_counters_sha256: str | None = None
    map_final_evidence: MapFinalEvidence | None = None
    map_final_evidence_sha256: str | None = None
    gc_receipt_sha256: str | None = None
    pin_preflight_receipt_sha256: str | None = None
    pin_migration_receipt_sha256: str | None = None
    rollback_bundle_sha256: str | None = None
    map_application_backup: DatabaseBackupReceipt | None = None
    map_dagster_backup: DatabaseBackupReceipt | None = None
    pinvi_backup: DatabaseBackupReceipt | None = None
    candidate_pair_sha256: str | None = None
    last_map_receipt: MapHelperReceipt | None = None
    last_map_receipt_sha256: str | None = None
    initial_receipt_sha256: str | None = None
    pin_final_receipt_sha256: str | None = None
    external_event_count: int = 0
    forward_boundary: Literal["not_crossed", "committed"] = "not_crossed"
    failure_stage: WindowPhase | None = None
    failure_class: WindowFailureClass | None = None


@dataclass(frozen=True)
class MapHelperCheck:
    name: str
    expected: JsonEvidence
    observed: JsonEvidence
    passed: bool


@dataclass(frozen=True)
class MapHelperReceipt:
    contract_version: Literal["h35-map/v1"]
    operation: MapHelperOperation
    transaction_id: str
    status: Literal["accepted"]
    source_revision: str
    database_identity: str
    request_digest: str
    prior_receipt_digest: str | None
    schema_before: str
    schema_after: str
    forward_boundary: Literal["not_crossed", "schema_0078"]
    row_counts: dict[str, int]
    checks: tuple[MapHelperCheck, ...]
    cache_target_evidence: MapFinalEvidence | None
    runtime_mutation_count: Literal[0]
    external_event_count: Literal[0]


@dataclass(frozen=True)
class MapFinalEvidence:
    contract_version: Literal["ktm-cache-target-final-evidence/v1"]
    external_system: Literal["pinvi"]
    stream_state: Literal["ready"]
    consumer_id: str
    restore_epoch: int
    control_version: int
    stream_control_etag: str
    high_watermark_cursor: str
    snapshot_count: int
    snapshot_merkle_root: str
    reconciliation_backlog_count: Literal[0]
    outbox_backlog_count: Literal[0]
    claim_backlog_count: Literal[0]
    delivery_backlog_count: Literal[0]


@dataclass(frozen=True)
class PinBoundaryReceipt:
    contract_version: Literal["pinvi-cache-target-final-boundary/v1"]
    operation: PinBoundaryOperation
    transaction_id: str
    cutover_id: str
    source_revision: str
    database_identity: str
    writer_registry_sha256: str
    initial_writer_fence_sha256: str
    final_writer_fence_sha256: str | None
    prior_receipt_sha256: str | None
    canary_run_id: str | None
    map_final_evidence: MapFinalEvidence | None
    map_final_evidence_sha256: str | None
    status: Literal["succeeded"]
    schema_revision: Literal["20260801_0047", "20260802_0048"]
    pending_command_count: int
    leased_command_count: int
    dead_letter_command_count: int
    in_flight_command_count: int
    database_in_flight_transaction_count: int
    email_queue_pending_count: int
    telegram_outbox_pending_count: int
    location_audit_outbox_pending_count: int
    expected_initial_command_count: int
    expected_initial_event_count: int
    expected_initial_claim_item_count: int
    expected_synthetic_command_count: int
    expected_synthetic_event_count: int
    expected_synthetic_claim_count: int
    unexpected_generation7_command_count: int
    unexpected_non_synthetic_event_count: int
    unexpected_non_synthetic_claim_count: int
    initial_evidence_sha256: str | None
    canary_provenance_sha256: str | None
    final_local_remote_evidence_sha256: str | None
    evidence_sha256: str
    runtime_mutation_count: Literal[0]
    external_mutation_count: Literal[0]
    audit_id: str | None
    audit_request_sha256: str | None
    audit_row_count: Literal[0, 1]


@dataclass(frozen=True)
class PinMigrationReceipt:
    contract_version: Literal["pinvi-cache-target-migration/v1"]
    transaction_id: str
    source_revision: str
    database_identity: str
    writer_registry_sha256: str
    initial_writer_fence_sha256: str
    prior_receipt_sha256: str
    candidate_image_id: str
    schema_before: Literal["20260801_0047"]
    schema_after: Literal["20260802_0048"]
    command_sha256: str
    status: Literal["succeeded"]


def prepare_cache_target_window(
    *,
    transaction_id: str,
    cutover_id: str,
    expected_restore_epoch: int,
    reason: str,
    environment_sha256: str,
    compose_sha256: str,
    resolved_compose_sha256: str,
    old_manifest_sha256: str,
) -> CacheTargetWindowJournal:
    _canonical_uuid(transaction_id, "transaction ID")
    _canonical_uuid(cutover_id, "cutover ID")
    if expected_restore_epoch <= 0:
        raise DeploymentContractError("cache-target restore epoch must be positive")
    if not reason or reason != reason.strip() or "\n" in reason or "\r" in reason:
        raise DeploymentContractError("cache-target cutover reason is invalid")
    for label, digest in (
        ("environment", environment_sha256),
        ("compose", compose_sha256),
        ("resolved compose", resolved_compose_sha256),
        ("old manifest", old_manifest_sha256),
    ):
        _validate_sha256(digest, label)
    return CacheTargetWindowJournal(
        version=2,
        transaction_id=transaction_id,
        cutover_id=cutover_id,
        phase="prepared",
        expected_restore_epoch=expected_restore_epoch,
        reason_sha256=hashlib.sha256(reason.encode()).hexdigest(),
        environment_sha256=environment_sha256,
        compose_sha256=compose_sha256,
        resolved_compose_sha256=resolved_compose_sha256,
        old_manifest_sha256=old_manifest_sha256,
    )


def transition_cache_target_window(
    journal: CacheTargetWindowJournal,
    phase: WindowPhase,
    *,
    writer_drain_lease_id: str | None = None,
    writer_drain_receipt_sha256: str | None = None,
    writer_drain_restore_receipt_sha256: str | None = None,
    rollback_bundle_sha256: str | None = None,
    initial_writer_fence_sha256: str | None = None,
    final_writer_fence_sha256: str | None = None,
    final_map_write_counters_sha256: str | None = None,
    map_final_evidence: MapFinalEvidence | None = None,
    map_final_evidence_sha256: str | None = None,
    gc_receipt_sha256: str | None = None,
    pin_preflight_receipt_sha256: str | None = None,
    pin_migration_receipt_sha256: str | None = None,
    map_application_backup: DatabaseBackupReceipt | None = None,
    map_dagster_backup: DatabaseBackupReceipt | None = None,
    pinvi_backup: DatabaseBackupReceipt | None = None,
    candidate_pair_sha256: str | None = None,
    last_map_receipt: MapHelperReceipt | None = None,
    last_map_receipt_sha256: str | None = None,
    initial_receipt_sha256: str | None = None,
    pin_final_receipt_sha256: str | None = None,
    external_event_count: int | None = None,
) -> CacheTargetWindowJournal:
    if phase not in _allowed_next_phases(journal):
        raise DeploymentContractError("cache-target window phase transition is invalid")
    if phase == "rollback_preparing" and not old_restore_is_authorized(journal):
        raise DeploymentContractError(
            "cache-target old restore is forbidden after the external-event or "
            "final forward boundary"
        )
    updated = replace(
        journal,
        phase=phase,
        writer_drain_lease_id=(
            writer_drain_lease_id
            if writer_drain_lease_id is not None
            else journal.writer_drain_lease_id
        ),
        writer_drain_receipt_sha256=(
            writer_drain_receipt_sha256
            if writer_drain_receipt_sha256 is not None
            else journal.writer_drain_receipt_sha256
        ),
        writer_drain_restore_receipt_sha256=(
            writer_drain_restore_receipt_sha256
            if writer_drain_restore_receipt_sha256 is not None
            else journal.writer_drain_restore_receipt_sha256
        ),
        initial_writer_fence_sha256=(
            initial_writer_fence_sha256
            if initial_writer_fence_sha256 is not None
            else journal.initial_writer_fence_sha256
        ),
        final_writer_fence_sha256=(
            final_writer_fence_sha256
            if final_writer_fence_sha256 is not None
            else journal.final_writer_fence_sha256
        ),
        final_map_write_counters_sha256=(
            final_map_write_counters_sha256
            if final_map_write_counters_sha256 is not None
            else journal.final_map_write_counters_sha256
        ),
        map_final_evidence=(
            map_final_evidence
            if map_final_evidence is not None
            else journal.map_final_evidence
        ),
        map_final_evidence_sha256=(
            map_final_evidence_sha256
            if map_final_evidence_sha256 is not None
            else journal.map_final_evidence_sha256
        ),
        gc_receipt_sha256=(
            gc_receipt_sha256
            if gc_receipt_sha256 is not None
            else journal.gc_receipt_sha256
        ),
        pin_preflight_receipt_sha256=(
            pin_preflight_receipt_sha256
            if pin_preflight_receipt_sha256 is not None
            else journal.pin_preflight_receipt_sha256
        ),
        pin_migration_receipt_sha256=(
            pin_migration_receipt_sha256
            if pin_migration_receipt_sha256 is not None
            else journal.pin_migration_receipt_sha256
        ),
        rollback_bundle_sha256=(
            rollback_bundle_sha256
            if rollback_bundle_sha256 is not None
            else journal.rollback_bundle_sha256
        ),
        map_application_backup=(
            map_application_backup
            if map_application_backup is not None
            else journal.map_application_backup
        ),
        map_dagster_backup=(
            map_dagster_backup
            if map_dagster_backup is not None
            else journal.map_dagster_backup
        ),
        pinvi_backup=(pinvi_backup if pinvi_backup is not None else journal.pinvi_backup),
        candidate_pair_sha256=(
            candidate_pair_sha256
            if candidate_pair_sha256 is not None
            else journal.candidate_pair_sha256
        ),
        last_map_receipt=(
            last_map_receipt
            if last_map_receipt is not None
            else journal.last_map_receipt
        ),
        last_map_receipt_sha256=(
            last_map_receipt_sha256
            if last_map_receipt_sha256 is not None
            else journal.last_map_receipt_sha256
        ),
        initial_receipt_sha256=(
            initial_receipt_sha256
            if initial_receipt_sha256 is not None
            else journal.initial_receipt_sha256
        ),
        pin_final_receipt_sha256=(
            pin_final_receipt_sha256
            if pin_final_receipt_sha256 is not None
            else journal.pin_final_receipt_sha256
        ),
        external_event_count=(
            external_event_count
            if external_event_count is not None
            else journal.external_event_count
        ),
        forward_boundary=(
            "committed" if phase == "forward_committed" else journal.forward_boundary
        ),
    )
    _validate_journal(updated)
    _validate_phase_evidence(updated)
    return updated


def record_window_failure(
    journal: CacheTargetWindowJournal,
    *,
    failure_class: WindowFailureClass,
) -> CacheTargetWindowJournal:
    """설계 문서 4절: pre-forward-boundary 실패로 coupled rollback에 들어가기 직전,
    마지막으로 안전했던 forward phase와 실패 분류를 얼린다. `journal.phase` 자체는
    바꾸지 않는다 — caller가 뒤이어 호출하는
    `transition_cache_target_window(..., "rollback_preparing")`가 이 값을 그대로
    carry-forward한다. raw process output/stderr는 여기서도 확장하지 않는다."""

    if journal.phase not in FORWARD_PHASES:
        raise DeploymentContractError(
            "cache-target window failure can only be recorded from a forward phase"
        )
    updated = replace(journal, failure_stage=journal.phase, failure_class=failure_class)
    _validate_journal(updated)
    return updated


def old_restore_is_authorized(journal: CacheTargetWindowJournal) -> bool:
    return (
        journal.forward_boundary == "not_crossed"
        and journal.external_event_count == 0
        and journal.phase not in TERMINAL_PHASES
    )


def write_cache_target_window(
    path: Path,
    journal: CacheTargetWindowJournal,
) -> str:
    _validate_journal(journal)
    _validate_phase_evidence(journal)
    return write_cutover_state(path, journal)  # type: ignore[arg-type]


def read_cache_target_window(path: Path) -> CacheTargetWindowJournal:
    try:
        document = json.loads(read_owner_only_state(path))
        if isinstance(document, dict) and document.get("version") == 1:
            raise DeploymentContractError(
                "cache-target window journal v1 is unsupported; "
                "reset the isolated state before TVN41"
            )
        if not isinstance(document, dict) or set(document) != _JOURNAL_FIELDS:
            raise TypeError
        for field_name in (
            "map_application_backup",
            "map_dagster_backup",
            "pinvi_backup",
        ):
            value = document[field_name]
            if value is not None:
                if not isinstance(value, dict) or set(value) != _BACKUP_FIELDS:
                    raise TypeError
                rehearsal = value["restore_rehearsal"]
                if not isinstance(rehearsal, dict) or set(rehearsal) != _REHEARSAL_FIELDS:
                    raise TypeError
                value["restore_rehearsal"] = DatabaseRestoreRehearsalReceipt(
                    **rehearsal
                )
                document[field_name] = DatabaseBackupReceipt(**value)
        raw_map_receipt = document["last_map_receipt"]
        if raw_map_receipt is not None:
            document["last_map_receipt"] = _map_helper_receipt_from_document(
                raw_map_receipt
            )
        raw_final_evidence = document["map_final_evidence"]
        if raw_final_evidence is not None:
            document["map_final_evidence"] = _map_final_evidence_from_document(
                raw_final_evidence
            )
        journal = CacheTargetWindowJournal(**document)
    except DeploymentContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError("cache-target window journal is invalid") from exc
    _validate_journal(journal)
    _validate_phase_evidence(journal)
    return journal


def parse_map_helper_receipt(
    *,
    stdout: str,
    stderr: str,
    operation: MapHelperOperation,
    transaction_id: str,
    source_revision: str,
    database_identity: str,
    request: dict[str, Any],
    prior_receipt_digest: str | None,
) -> MapHelperReceipt:
    if stderr:
        raise DeploymentContractError("Map H35 helper wrote to stderr")
    lines = stdout.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise DeploymentContractError("Map H35 helper must return one JSON line")
    try:
        document = json.loads(lines[0])
        if not isinstance(document, dict) or set(document) != _HELPER_FIELDS:
            raise TypeError
        receipt = _map_helper_receipt_from_document(document)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError("Map H35 helper receipt is invalid") from exc
    expected_request_digest = logical_sha256(request)
    if (
        receipt.contract_version != "h35-map/v1"
        or receipt.operation != operation
        or receipt.transaction_id != transaction_id
        or receipt.status != "accepted"
        or receipt.source_revision != source_revision
        or receipt.database_identity != database_identity
        or receipt.request_digest != expected_request_digest
        or receipt.prior_receipt_digest != prior_receipt_digest
        or receipt.runtime_mutation_count != 0
        or receipt.external_event_count != 0
    ):
        raise DeploymentContractError("Map H35 helper receipt binding is invalid")
    _validate_map_helper_receipt(receipt)
    return receipt


def map_helper_receipt_sha256(receipt: MapHelperReceipt) -> str:
    return logical_sha256(asdict(receipt))


def _map_helper_receipt_from_document(value: Any) -> MapHelperReceipt:
    if not isinstance(value, dict) or set(value) != _HELPER_FIELDS:
        raise TypeError
    raw_checks = value["checks"]
    if not isinstance(raw_checks, list):
        raise TypeError
    checks: list[MapHelperCheck] = []
    for raw_check in raw_checks:
        if not isinstance(raw_check, dict) or set(raw_check) != _CHECK_FIELDS:
            raise TypeError
        checks.append(
            MapHelperCheck(
                name=raw_check["name"],
                expected=_map_check_evidence(raw_check["expected"]),
                observed=_map_check_evidence(raw_check["observed"]),
                passed=raw_check["passed"],
            )
        )
    raw_evidence = value["cache_target_evidence"]
    evidence = (
        None if raw_evidence is None else _map_final_evidence_from_document(raw_evidence)
    )
    return MapHelperReceipt(
        **{
            **value,
            "checks": tuple(checks),
            "cache_target_evidence": evidence,
        }
    )


def parse_pin_boundary_receipt(
    *,
    stdout: str,
    stderr: str,
    request: dict[str, Any],
    expected_initial_count: int,
) -> PinBoundaryReceipt:
    if set(request) != _PIN_BOUNDARY_REQUEST_FIELDS:
        raise DeploymentContractError("Pin boundary request schema is invalid")
    if stderr:
        raise DeploymentContractError("Pin boundary helper wrote to stderr")
    lines = stdout.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise DeploymentContractError("Pin boundary helper must return one JSON line")
    try:
        document = json.loads(lines[0])
        if not isinstance(document, dict) or set(document) != (
            _PIN_BOUNDARY_RECEIPT_FIELDS
        ):
            raise TypeError
        raw_evidence = document["map_final_evidence"]
        receipt = PinBoundaryReceipt(
            **{
                **document,
                "map_final_evidence": (
                    None
                    if raw_evidence is None
                    else _map_final_evidence_from_document(raw_evidence)
                ),
            }
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError("Pin boundary receipt is invalid") from exc
    receipt_document = asdict(receipt)
    if any(receipt_document[field] != value for field, value in request.items()):
        raise DeploymentContractError("Pin boundary receipt binding is invalid")
    expected_audit_request_sha256 = (
        logical_sha256(request) if receipt.operation == "finalize" else None
    )
    if receipt.audit_request_sha256 != expected_audit_request_sha256:
        raise DeploymentContractError("Pin boundary audit request binding is invalid")
    _validate_pin_boundary_receipt(
        receipt,
        expected_initial_count=expected_initial_count,
    )
    return receipt


def pin_boundary_receipt_sha256(receipt: PinBoundaryReceipt) -> str:
    return logical_sha256(asdict(receipt))


def pin_migration_receipt_sha256(receipt: PinMigrationReceipt) -> str:
    _canonical_uuid(receipt.transaction_id, "Pin migration transaction ID")
    if receipt.contract_version != "pinvi-cache-target-migration/v1":
        raise DeploymentContractError("Pin migration contract version is invalid")
    if not _SOURCE_REVISION.fullmatch(receipt.source_revision):
        raise DeploymentContractError("Pin migration source revision is invalid")
    for label, digest in (
        ("Pin migration database identity", receipt.database_identity),
        ("Pin migration writer registry", receipt.writer_registry_sha256),
        ("Pin migration writer fence", receipt.initial_writer_fence_sha256),
        ("Pin migration prior receipt", receipt.prior_receipt_sha256),
        ("Pin migration command", receipt.command_sha256),
    ):
        _validate_sha256(digest, label)
    if (
        receipt.writer_registry_sha256 != _PIN_WRITER_REGISTRY_SHA256
        or not receipt.candidate_image_id.startswith("sha256:")
        or len(receipt.candidate_image_id) != 71
        or not _SHA256.fullmatch(receipt.candidate_image_id.removeprefix("sha256:"))
        or receipt.schema_before != "20260801_0047"
        or receipt.schema_after != "20260802_0048"
        or receipt.status != "succeeded"
    ):
        raise DeploymentContractError("Pin migration receipt is invalid")
    return logical_sha256(asdict(receipt))


def logical_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def map_final_evidence_sha256(evidence: MapFinalEvidence) -> str:
    _validate_map_final_evidence(evidence)
    return logical_sha256(asdict(evidence))


def validate_map_final_evidence_binding(
    evidence: MapFinalEvidence,
    *,
    consumer_id: str,
    restore_epoch: int,
    snapshot_count: int,
    snapshot_merkle_root: str,
) -> None:
    """Map 최종 증거를 manager가 동결한 initial baseline에 exact 결박한다."""

    _validate_map_final_evidence(evidence)
    if (
        evidence.consumer_id != consumer_id
        or evidence.restore_epoch != restore_epoch
        or evidence.snapshot_count != snapshot_count
        or evidence.snapshot_merkle_root != snapshot_merkle_root
    ):
        raise DeploymentContractError(
            "Map final evidence differs from the frozen initial baseline"
        )


def _map_final_evidence_from_document(value: Any) -> MapFinalEvidence:
    if not isinstance(value, dict) or set(value) != {
        "contract_version",
        "external_system",
        "stream_state",
        "consumer_id",
        "restore_epoch",
        "control_version",
        "stream_control_etag",
        "high_watermark_cursor",
        "snapshot_count",
        "snapshot_merkle_root",
        "reconciliation_backlog_count",
        "outbox_backlog_count",
        "claim_backlog_count",
        "delivery_backlog_count",
    }:
        raise TypeError
    evidence = MapFinalEvidence(**value)
    _validate_map_final_evidence(evidence)
    return evidence


def _map_check_evidence(value: Any) -> JsonEvidence:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if (
        isinstance(value, list)
        and all(isinstance(item, str) and item for item in value)
        and value == sorted(set(value))
    ):
        return tuple(value)
    raise TypeError("Map helper check evidence is invalid")


def _allowed_next_phases(journal: CacheTargetWindowJournal) -> frozenset[WindowPhase]:
    if journal.phase in FORWARD_PHASES:
        index = FORWARD_PHASES.index(journal.phase)
        next_phase = (
            frozenset({FORWARD_PHASES[index + 1]})
            if index + 1 < len(FORWARD_PHASES)
            else frozenset()
        )
        allowed: set[WindowPhase] = set(next_phase)
        if journal.forward_boundary == "not_crossed":
            allowed.add("rollback_preparing")
        return frozenset(allowed)
    index = ROLLBACK_PHASES.index(journal.phase)
    return (
        frozenset({ROLLBACK_PHASES[index + 1]})
        if index + 1 < len(ROLLBACK_PHASES)
        else frozenset()
    )


def _validate_journal(journal: CacheTargetWindowJournal) -> None:
    if journal.version != 2 or journal.phase not in (*FORWARD_PHASES, *ROLLBACK_PHASES):
        raise DeploymentContractError("cache-target window journal contract is invalid")
    _canonical_uuid(journal.transaction_id, "transaction ID")
    _canonical_uuid(journal.cutover_id, "cutover ID")
    if journal.writer_drain_lease_id is not None:
        _canonical_uuid(journal.writer_drain_lease_id, "writer drain lease ID")
    if journal.expected_restore_epoch <= 0:
        raise DeploymentContractError("cache-target restore epoch must be positive")
    for label, digest in (
        ("reason", journal.reason_sha256),
        ("environment", journal.environment_sha256),
        ("compose", journal.compose_sha256),
        ("resolved compose", journal.resolved_compose_sha256),
        ("old manifest", journal.old_manifest_sha256),
    ):
        _validate_sha256(digest, label)
    for label, optional_digest in (
        ("writer drain receipt", journal.writer_drain_receipt_sha256),
        ("writer drain restore receipt", journal.writer_drain_restore_receipt_sha256),
        ("initial writer fence", journal.initial_writer_fence_sha256),
        ("final writer fence", journal.final_writer_fence_sha256),
        ("final Map write counters", journal.final_map_write_counters_sha256),
        ("Map final evidence", journal.map_final_evidence_sha256),
        ("GC receipt", journal.gc_receipt_sha256),
        ("Pin preflight receipt", journal.pin_preflight_receipt_sha256),
        ("Pin migration receipt", journal.pin_migration_receipt_sha256),
        ("rollback bundle", journal.rollback_bundle_sha256),
        ("candidate pair", journal.candidate_pair_sha256),
        ("Map receipt", journal.last_map_receipt_sha256),
        ("initial receipt", journal.initial_receipt_sha256),
        ("Pin final receipt", journal.pin_final_receipt_sha256),
    ):
        if optional_digest is not None:
            _validate_sha256(optional_digest, label)
    if journal.last_map_receipt is not None:
        _validate_map_helper_receipt(journal.last_map_receipt)
        if (
            journal.last_map_receipt_sha256
            != map_helper_receipt_sha256(journal.last_map_receipt)
        ):
            raise DeploymentContractError(
                "cache-target Map receipt payload differs from its digest"
            )
    if journal.map_final_evidence is not None:
        _validate_map_final_evidence(journal.map_final_evidence)
        if (
            journal.map_final_evidence_sha256
            != map_final_evidence_sha256(journal.map_final_evidence)
        ):
            raise DeploymentContractError(
                "cache-target Map final evidence differs from its digest"
            )
    for backup in (
        journal.map_application_backup,
        journal.map_dagster_backup,
        journal.pinvi_backup,
    ):
        if backup is not None:
            _validate_backup_receipt(backup)
    if type(journal.external_event_count) is not int or journal.external_event_count < 0:
        raise DeploymentContractError("cache-target external event count is invalid")
    if journal.forward_boundary not in {"not_crossed", "committed"}:
        raise DeploymentContractError("cache-target forward boundary is invalid")
    if journal.forward_boundary == "committed" and journal.phase not in {
        "forward_committed",
        "runtime_activated",
    }:
        raise DeploymentContractError("cache-target forward boundary phase is invalid")
    if journal.failure_stage is not None and journal.failure_stage not in FORWARD_PHASES:
        raise DeploymentContractError("cache-target window failure stage is invalid")
    if journal.failure_class is not None and journal.failure_class not in (
        "contract_violation",
        "unexpected_error",
    ):
        raise DeploymentContractError("cache-target window failure class is invalid")
    if (journal.failure_stage is None) != (journal.failure_class is None):
        raise DeploymentContractError(
            "cache-target window failure stage and class must be set together"
        )


def _validate_phase_evidence(journal: CacheTargetWindowJournal) -> None:
    # 적대적 리뷰(2명)가 cache_target_diagnostics.py에서 찾은 것과 같은 공백:
    # 아래 phase 문턱 검사들은 각각 독립적이라, restore receipt가 있는데 그보다
    # 먼저 있어야 할 lease/receipt가 없는(논리적으로 불가능한) journal이 phase와
    # 무관하게 통과할 수 있었다. restore는 lease/receipt 없이는 절대 존재할 수
    # 없다는 불변식을 rollback/forward 어느 쪽이든, phase와 무관하게 강제한다.
    if journal.writer_drain_restore_receipt_sha256 is not None and (
        journal.writer_drain_lease_id is None or journal.writer_drain_receipt_sha256 is None
    ):
        raise DeploymentContractError(
            "cache-target writer drain restore evidence precedes its lease"
        )
    if journal.phase in ROLLBACK_PHASES:
        rollback_evidence = (
            journal.rollback_bundle_sha256,
            journal.map_application_backup,
            journal.map_dagster_backup,
            journal.pinvi_backup,
        )
        if (
            ROLLBACK_PHASES.index(journal.phase)
            >= ROLLBACK_PHASES.index("map_db_restored")
            and any(value is not None for value in rollback_evidence)
            and any(value is None for value in rollback_evidence)
        ):
            raise DeploymentContractError(
                "cache-target rollback backup evidence is incomplete"
            )
        if ROLLBACK_PHASES.index(journal.phase) >= ROLLBACK_PHASES.index(
            "writers_restored"
        ) and (
            journal.writer_drain_lease_id is None
            or journal.writer_drain_receipt_sha256 is None
            or journal.writer_drain_restore_receipt_sha256 is None
        ):
            raise DeploymentContractError(
                "cache-target rollback writer drain restore evidence is missing"
            )
        return
    phase_index = (
        FORWARD_PHASES.index(journal.phase)
    )
    if phase_index >= FORWARD_PHASES.index("writers_drained") and (
        journal.writer_drain_lease_id is None
        or journal.writer_drain_receipt_sha256 is None
    ):
        raise DeploymentContractError("cache-target writer drain evidence is missing")
    if phase_index >= FORWARD_PHASES.index("runtime_activated") and (
        journal.writer_drain_restore_receipt_sha256 is None
    ):
        raise DeploymentContractError(
            "cache-target writer drain restore evidence is missing"
        )
    if phase_index >= FORWARD_PHASES.index("writers_fenced") and (
        journal.initial_writer_fence_sha256 is None
    ):
        raise DeploymentContractError("cache-target writer fence evidence is missing")
    if phase_index >= FORWARD_PHASES.index("backups_committed") and any(
        value is None
        for value in (
            journal.rollback_bundle_sha256,
            journal.map_application_backup,
            journal.map_dagster_backup,
            journal.pinvi_backup,
        )
    ):
        raise DeploymentContractError("cache-target backup evidence is incomplete")
    if phase_index >= FORWARD_PHASES.index("backups_committed") and any(
        receipt is not None
        and receipt.writer_fence_sha256 != journal.initial_writer_fence_sha256
        for receipt in (
            journal.map_application_backup,
            journal.map_dagster_backup,
            journal.pinvi_backup,
        )
    ):
        raise DeploymentContractError("cache-target backup writer fence differs")
    if phase_index >= FORWARD_PHASES.index("candidate_built") and (
        journal.candidate_pair_sha256 is None
    ):
        raise DeploymentContractError("cache-target candidate evidence is missing")
    if phase_index >= FORWARD_PHASES.index("pin_preflight_verified") and (
        journal.pin_preflight_receipt_sha256 is None
    ):
        raise DeploymentContractError("cache-target Pin preflight evidence is missing")
    if phase_index >= FORWARD_PHASES.index("map_preflight_verified") and (
        journal.last_map_receipt_sha256 is None
        or journal.last_map_receipt is None
    ):
        raise DeploymentContractError("cache-target Map helper evidence is missing")
    if phase_index >= FORWARD_PHASES.index("databases_forwarded") and (
        journal.pin_migration_receipt_sha256 is None
    ):
        raise DeploymentContractError("cache-target Pin migration evidence is missing")
    if phase_index >= FORWARD_PHASES.index("initial_committed") and (
        journal.initial_receipt_sha256 is None
    ):
        raise DeploymentContractError("cache-target initial receipt evidence is missing")
    if phase_index >= FORWARD_PHASES.index("canary_verified") and (
        journal.external_event_count <= 0
    ):
        raise DeploymentContractError("cache-target causal event evidence is missing")
    if phase_index >= FORWARD_PHASES.index("gc_verified") and (
        journal.gc_receipt_sha256 is None
    ):
        raise DeploymentContractError("cache-target GC evidence is missing")
    if (
        FORWARD_PHASES.index("gc_verified")
        <= phase_index
        < FORWARD_PHASES.index("map_final_verified")
        and (
            journal.last_map_receipt is None
            or journal.last_map_receipt.operation != "gc"
            or journal.gc_receipt_sha256 != journal.last_map_receipt_sha256
        )
    ):
        raise DeploymentContractError("cache-target GC receipt binding is invalid")
    if phase_index >= FORWARD_PHASES.index("final_writers_fenced") and (
        journal.final_writer_fence_sha256 is None
        or journal.final_map_write_counters_sha256 is None
    ):
        raise DeploymentContractError("cache-target final writer fence is missing")
    if phase_index >= FORWARD_PHASES.index("map_final_verified") and (
        journal.map_final_evidence is None
        or journal.map_final_evidence_sha256 is None
    ):
        raise DeploymentContractError("cache-target Map final evidence is missing")
    if phase_index >= FORWARD_PHASES.index("map_final_verified") and (
        journal.last_map_receipt is None
        or journal.last_map_receipt.operation != "verify"
        or journal.last_map_receipt.cache_target_evidence
        != journal.map_final_evidence
    ):
        raise DeploymentContractError("cache-target Map final receipt binding is invalid")
    if phase_index >= FORWARD_PHASES.index("final_boundary_verified") and (
        journal.pin_final_receipt_sha256 is None
    ):
        raise DeploymentContractError("cache-target Pin final evidence is missing")


def _validate_backup_receipt(receipt: DatabaseBackupReceipt) -> None:
    _canonical_uuid(receipt.transaction_id, "database backup transaction ID")
    _validate_sha256(receipt.database_identity, "database identity")
    if not _BACKUP_SCHEMA_REVISION.fullmatch(receipt.schema_revision):
        raise DeploymentContractError("database backup schema revision is invalid")
    _canonical_uuid(receipt.logical_backup_id, "logical backup ID")
    if type(receipt.byte_size) is not int or receipt.byte_size <= 0:
        raise DeploymentContractError("database backup size is invalid")
    _validate_sha256(receipt.sha256, "database backup")
    _validate_sha256(receipt.schema_inventory_sha256, "database schema inventory")
    _validate_sha256(receipt.data_inventory_sha256, "database data inventory")
    _validate_sha256(receipt.writer_fence_sha256, "database writer fence")
    rehearsal = receipt.restore_rehearsal
    _canonical_uuid(rehearsal.transaction_id, "restore rehearsal transaction ID")
    for label, digest in (
        ("restore rehearsal database identity", rehearsal.database_identity),
        ("restore rehearsal source identity", rehearsal.source_database_identity),
        ("restore rehearsal archive", rehearsal.archive_sha256),
        ("restore rehearsal schema inventory", rehearsal.schema_inventory_sha256),
        ("restore rehearsal data inventory", rehearsal.data_inventory_sha256),
    ):
        _validate_sha256(digest, label)
    if not _BACKUP_SCHEMA_REVISION.fullmatch(rehearsal.schema_revision):
        raise DeploymentContractError(
            "database restore rehearsal schema revision is invalid"
        )
    if (
        receipt.writer_mutation_count != 0
        or rehearsal.verified is not True
        or rehearsal.transaction_id != receipt.transaction_id
        or rehearsal.database_identity == receipt.database_identity
        or rehearsal.source_database_identity != receipt.database_identity
        or rehearsal.archive_sha256 != receipt.sha256
        or rehearsal.schema_revision != receipt.schema_revision
        or rehearsal.schema_inventory_sha256 != receipt.schema_inventory_sha256
        or rehearsal.data_inventory_sha256 != receipt.data_inventory_sha256
    ):
        raise DeploymentContractError("database backup rehearsal evidence is invalid")


def _validate_map_helper_receipt(receipt: MapHelperReceipt) -> None:
    _canonical_uuid(receipt.transaction_id, "Map helper transaction ID")
    if not _SOURCE_REVISION.fullmatch(receipt.source_revision):
        raise DeploymentContractError("Map helper source revision is invalid")
    _validate_sha256(receipt.database_identity, "Map helper database identity")
    _validate_sha256(receipt.request_digest, "Map helper request")
    if receipt.prior_receipt_digest is not None:
        _validate_sha256(receipt.prior_receipt_digest, "prior Map helper receipt")
    for schema in (receipt.schema_before, receipt.schema_after):
        if not _MAP_SCHEMA_REVISION.fullmatch(schema):
            raise DeploymentContractError("Map helper schema revision is invalid")
    expected_boundary = "not_crossed" if receipt.operation == "preflight" else "schema_0078"
    if receipt.forward_boundary != expected_boundary:
        raise DeploymentContractError("Map helper forward boundary observation is invalid")
    if not receipt.row_counts or any(
        not isinstance(name, str)
        or not name
        or type(count) is not int
        or count < 0
        for name, count in receipt.row_counts.items()
    ):
        raise DeploymentContractError("Map helper row counts are invalid")
    if not receipt.checks or any(
        not check.name
        or check.name != check.name.strip()
        or not check.passed
        or type(check.passed) is not bool
        or check.expected != check.observed
        or not _valid_map_check_evidence(check.expected)
        or not _valid_map_check_evidence(check.observed)
        for check in receipt.checks
    ):
        raise DeploymentContractError("Map helper checks are invalid")
    if receipt.operation == "gc":
        _validate_map_gc_receipt(receipt)
        if receipt.cache_target_evidence is not None:
            raise DeploymentContractError("Map GC final evidence is unexpected")
    elif receipt.operation == "verify":
        if receipt.cache_target_evidence is None:
            raise DeploymentContractError("Map final evidence is missing")
        _validate_map_final_evidence(receipt.cache_target_evidence)
    elif receipt.cache_target_evidence is not None:
        raise DeploymentContractError("Map operation evidence is unexpected")


def _validate_map_gc_receipt(receipt: MapHelperReceipt) -> None:
    if set(receipt.row_counts) != {
        "batches",
        "deleted_headers",
        "deleted_items",
        "referenced_headers",
        "referenced_items",
        "remaining_headers",
        "remaining_items",
    } or (
        receipt.row_counts["remaining_headers"],
        receipt.row_counts["remaining_items"],
    ) != (0, 0):
        raise DeploymentContractError("Map GC row-count evidence is invalid")
    checks = {check.name: check for check in receipt.checks}
    expected_run_id = (
        f"h35:{receipt.transaction_id}:cache-target-snapshot-gc:v1"
    )
    exact = {
        "gc_lock_acquired": (True, True),
        "gc_not_skipped": (False, False),
        "gc_remaining_items": (0, 0),
        "gc_remaining_headers": (0, 0),
        "gc_observation_run_id": (expected_run_id, expected_run_id),
        "gc_observation_timestamp_present": (True, True),
        "gc_referenced_items_preserved": (True, True),
        "gc_referenced_headers_preserved": (True, True),
        "gc_observation_referenced_items_fresh": (True, True),
        "gc_observation_referenced_headers_fresh": (True, True),
    }
    if any(
        name not in checks
        or (checks[name].expected, checks[name].observed) != evidence
        for name, evidence in exact.items()
    ):
        raise DeploymentContractError("Map GC observation evidence is invalid")


def _validate_map_final_evidence(evidence: MapFinalEvidence) -> None:
    if (
        evidence.contract_version != "ktm-cache-target-final-evidence/v1"
        or evidence.external_system != "pinvi"
        or evidence.stream_state != "ready"
        or not isinstance(evidence.consumer_id, str)
        or not evidence.consumer_id
        or type(evidence.restore_epoch) is not int
        or evidence.restore_epoch <= 0
        or type(evidence.control_version) is not int
        or evidence.control_version <= 0
        or not isinstance(evidence.stream_control_etag, str)
        or not evidence.stream_control_etag
        or not isinstance(evidence.high_watermark_cursor, str)
        or not evidence.high_watermark_cursor
        or type(evidence.snapshot_count) is not int
        or evidence.snapshot_count < 0
        or not _SHA256.fullmatch(evidence.snapshot_merkle_root)
        or (
            evidence.reconciliation_backlog_count,
            evidence.outbox_backlog_count,
            evidence.claim_backlog_count,
            evidence.delivery_backlog_count,
        )
        != (0, 0, 0, 0)
    ):
        raise DeploymentContractError("Map final evidence is invalid")


def _valid_map_check_evidence(value: JsonEvidence) -> bool:
    if type(value) in {str, int, bool, type(None)}:
        return True
    return (
        isinstance(value, tuple)
        and all(isinstance(item, str) and item for item in value)
        and value == tuple(sorted(set(value)))
    )


def _validate_pin_boundary_receipt(
    receipt: PinBoundaryReceipt,
    *,
    expected_initial_count: int,
) -> None:
    if receipt.contract_version != "pinvi-cache-target-final-boundary/v1":
        raise DeploymentContractError("Pin boundary contract version is invalid")
    _canonical_uuid(receipt.transaction_id, "Pin boundary transaction ID")
    _canonical_uuid(receipt.cutover_id, "Pin boundary cutover ID")
    if receipt.canary_run_id is not None:
        _canonical_uuid(receipt.canary_run_id, "Pin boundary canary run ID")
    if not _SOURCE_REVISION.fullmatch(receipt.source_revision):
        raise DeploymentContractError("Pin boundary source revision is invalid")
    for label, digest in (
        ("Pin boundary database identity", receipt.database_identity),
        ("Pin boundary writer registry", receipt.writer_registry_sha256),
        ("Pin boundary initial writer fence", receipt.initial_writer_fence_sha256),
        ("Pin boundary evidence", receipt.evidence_sha256),
    ):
        _validate_sha256(digest, label)
    if receipt.writer_registry_sha256 != _PIN_WRITER_REGISTRY_SHA256:
        raise DeploymentContractError("Pin boundary writer registry is foreign")
    if receipt.prior_receipt_sha256 is not None:
        _validate_sha256(receipt.prior_receipt_sha256, "prior Pin boundary receipt")
    counts = (
        receipt.pending_command_count,
        receipt.leased_command_count,
        receipt.dead_letter_command_count,
        receipt.in_flight_command_count,
        receipt.database_in_flight_transaction_count,
        receipt.email_queue_pending_count,
        receipt.telegram_outbox_pending_count,
        receipt.location_audit_outbox_pending_count,
        receipt.expected_initial_command_count,
        receipt.expected_initial_event_count,
        receipt.expected_initial_claim_item_count,
        receipt.expected_synthetic_command_count,
        receipt.expected_synthetic_event_count,
        receipt.expected_synthetic_claim_count,
        receipt.unexpected_generation7_command_count,
        receipt.unexpected_non_synthetic_event_count,
        receipt.unexpected_non_synthetic_claim_count,
        receipt.runtime_mutation_count,
        receipt.external_mutation_count,
        receipt.audit_row_count,
    )
    if any(type(count) is not int or count < 0 for count in counts):
        raise DeploymentContractError("Pin boundary count evidence is invalid")
    if (
        receipt.status != "succeeded"
        or receipt.pending_command_count != 0
        or receipt.leased_command_count != 0
        or receipt.dead_letter_command_count != 0
        or receipt.in_flight_command_count
        != receipt.pending_command_count + receipt.leased_command_count
        or receipt.database_in_flight_transaction_count != 0
        or receipt.unexpected_generation7_command_count != 0
        or receipt.unexpected_non_synthetic_event_count != 0
        or receipt.unexpected_non_synthetic_claim_count != 0
        or receipt.runtime_mutation_count != 0
        or receipt.external_mutation_count != 0
    ):
        raise DeploymentContractError("Pin boundary zero evidence is invalid")
    evidence_hashes = (
        receipt.initial_evidence_sha256,
        receipt.canary_provenance_sha256,
        receipt.final_local_remote_evidence_sha256,
    )
    if receipt.operation == "preflight":
        if (
            receipt.schema_revision != "20260801_0047"
            or receipt.final_writer_fence_sha256 is not None
            or receipt.prior_receipt_sha256 is not None
            or receipt.canary_run_id is not None
            or receipt.map_final_evidence is not None
            or receipt.map_final_evidence_sha256 is not None
            or receipt.audit_id is not None
            or receipt.audit_request_sha256 is not None
            or receipt.audit_row_count != 0
            or any(evidence is not None for evidence in evidence_hashes)
            or any(
                count != 0
                for count in (
                    receipt.expected_initial_command_count,
                    receipt.expected_initial_event_count,
                    receipt.expected_initial_claim_item_count,
                    receipt.expected_synthetic_command_count,
                    receipt.expected_synthetic_event_count,
                    receipt.expected_synthetic_claim_count,
                )
            )
        ):
            raise DeploymentContractError("Pin preflight evidence is invalid")
        return
    if receipt.operation != "finalize":
        raise DeploymentContractError("Pin boundary operation is invalid")
    if (
        expected_initial_count < 0
        or receipt.schema_revision != "20260802_0048"
        or receipt.prior_receipt_sha256 is None
        or receipt.canary_run_id is None
        or receipt.final_writer_fence_sha256 is None
        or receipt.map_final_evidence is None
        or receipt.map_final_evidence_sha256 is None
        or receipt.audit_id != receipt.transaction_id
        or receipt.audit_request_sha256 is None
        or receipt.audit_row_count != 1
        or receipt.expected_initial_command_count != expected_initial_count
        or receipt.expected_initial_event_count != expected_initial_count + 1
        or receipt.expected_initial_claim_item_count != expected_initial_count + 1
        or (
            receipt.expected_synthetic_command_count,
            receipt.expected_synthetic_event_count,
            receipt.expected_synthetic_claim_count,
        )
        != (2, 2, 2)
        or any(evidence is None for evidence in evidence_hashes)
    ):
        raise DeploymentContractError("Pin final boundary evidence is invalid")
    _validate_sha256(
        receipt.final_writer_fence_sha256,
        "Pin final writer fence",
    )
    _validate_sha256(receipt.audit_request_sha256, "Pin audit request")
    _validate_map_final_evidence(receipt.map_final_evidence)
    if (
        receipt.map_final_evidence_sha256
        != map_final_evidence_sha256(receipt.map_final_evidence)
    ):
        raise DeploymentContractError("Pin Map final evidence digest is invalid")
    for label, optional_digest in zip(
        (
            "Pin initial evidence",
            "Pin canary provenance",
            "Pin local/remote evidence",
        ),
        evidence_hashes,
        strict=True,
    ):
        if optional_digest is None:
            raise DeploymentContractError(f"{label} is missing")
        _validate_sha256(optional_digest, label)


def _validate_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise DeploymentContractError(f"{label} SHA-256 is invalid")


def _canonical_uuid(value: str, label: str) -> str:
    try:
        canonical = str(uuid.UUID(value))
    except (AttributeError, ValueError) as exc:
        raise DeploymentContractError(f"cache-target {label} is invalid") from exc
    if canonical != value:
        raise DeploymentContractError(f"cache-target {label} must be canonical")
    return canonical
