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
    "backups_committed",
    "candidate_built",
    "databases_forwarded",
    "csv_forwarded",
    "generation_bootstrapped",
    "initial_committed",
    "sync_enabled",
    "canary_verified",
    "gc_verified",
    "forward_committed",
    "rollback_preparing",
    "new_runtime_stopped",
    "map_db_restored",
    "map_dagster_db_restored",
    "pinvi_db_restored",
    "manager_state_restored",
    "old_runtime_restored",
    "rolled_back",
]
MapHelperOperation = Literal["preflight", "migrate", "csv5", "verify"]
JsonScalar = str | int | bool | None

FORWARD_PHASES: tuple[WindowPhase, ...] = (
    "prepared",
    "backups_committed",
    "candidate_built",
    "databases_forwarded",
    "csv_forwarded",
    "generation_bootstrapped",
    "initial_committed",
    "sync_enabled",
    "canary_verified",
    "gc_verified",
    "forward_committed",
)
ROLLBACK_PHASES: tuple[WindowPhase, ...] = (
    "rollback_preparing",
    "new_runtime_stopped",
    "map_db_restored",
    "map_dagster_db_restored",
    "pinvi_db_restored",
    "manager_state_restored",
    "old_runtime_restored",
    "rolled_back",
)
TERMINAL_PHASES = frozenset({"forward_committed", "rolled_back"})
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
        "rollback_bundle_sha256",
        "map_application_backup",
        "map_dagster_backup",
        "pinvi_backup",
        "candidate_pair_sha256",
        "last_map_receipt_sha256",
        "initial_receipt_sha256",
        "external_event_count",
        "forward_boundary",
    }
)
_BACKUP_FIELDS = frozenset(
    {
        "database_identity",
        "schema_revision",
        "logical_backup_id",
        "byte_size",
        "sha256",
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
        "runtime_mutation_count",
        "external_event_count",
    }
)
_CHECK_FIELDS = frozenset({"name", "expected", "observed", "passed"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SCHEMA_REVISION = re.compile(r"^[0-9]{4}_[a-z0-9_]+$")


@dataclass(frozen=True)
class DatabaseBackupReceipt:
    database_identity: str
    schema_revision: str
    logical_backup_id: str
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class CacheTargetWindowJournal:
    version: Literal[1]
    transaction_id: str
    cutover_id: str
    phase: WindowPhase
    expected_restore_epoch: int
    reason_sha256: str
    environment_sha256: str
    compose_sha256: str
    resolved_compose_sha256: str
    old_manifest_sha256: str
    rollback_bundle_sha256: str | None = None
    map_application_backup: DatabaseBackupReceipt | None = None
    map_dagster_backup: DatabaseBackupReceipt | None = None
    pinvi_backup: DatabaseBackupReceipt | None = None
    candidate_pair_sha256: str | None = None
    last_map_receipt_sha256: str | None = None
    initial_receipt_sha256: str | None = None
    external_event_count: int = 0
    forward_boundary: Literal["not_crossed", "committed"] = "not_crossed"


@dataclass(frozen=True)
class MapHelperCheck:
    name: str
    expected: JsonScalar
    observed: JsonScalar
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
    runtime_mutation_count: Literal[0]
    external_event_count: Literal[0]


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
        version=1,
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
    rollback_bundle_sha256: str | None = None,
    map_application_backup: DatabaseBackupReceipt | None = None,
    map_dagster_backup: DatabaseBackupReceipt | None = None,
    pinvi_backup: DatabaseBackupReceipt | None = None,
    candidate_pair_sha256: str | None = None,
    last_map_receipt_sha256: str | None = None,
    initial_receipt_sha256: str | None = None,
    external_event_count: int | None = None,
) -> CacheTargetWindowJournal:
    if phase not in _allowed_next_phases(journal):
        raise DeploymentContractError("cache-target window phase transition is invalid")
    if phase == "rollback_preparing" and not old_restore_is_authorized(journal):
        raise DeploymentContractError(
            "cache-target old restore is forbidden after the forward boundary"
        )
    updated = replace(
        journal,
        phase=phase,
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


def old_restore_is_authorized(journal: CacheTargetWindowJournal) -> bool:
    return (
        journal.forward_boundary == "not_crossed"
        and journal.external_event_count == 0
        and journal.phase != "forward_committed"
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
                document[field_name] = DatabaseBackupReceipt(**value)
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
        raw_checks = document["checks"]
        if not isinstance(raw_checks, list):
            raise TypeError
        checks: list[MapHelperCheck] = []
        for raw_check in raw_checks:
            if not isinstance(raw_check, dict) or set(raw_check) != _CHECK_FIELDS:
                raise TypeError
            checks.append(MapHelperCheck(**raw_check))
        document["checks"] = tuple(checks)
        receipt = MapHelperReceipt(**document)
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


def logical_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _allowed_next_phases(journal: CacheTargetWindowJournal) -> frozenset[WindowPhase]:
    if journal.phase in FORWARD_PHASES:
        index = FORWARD_PHASES.index(journal.phase)
        next_phase = (
            frozenset({FORWARD_PHASES[index + 1]})
            if index + 1 < len(FORWARD_PHASES)
            else frozenset()
        )
        rollback = (
            frozenset({"rollback_preparing"})
            if journal.phase != "forward_committed"
            else frozenset()
        )
        return frozenset((*next_phase, *rollback))
    index = ROLLBACK_PHASES.index(journal.phase)
    return (
        frozenset({ROLLBACK_PHASES[index + 1]})
        if index + 1 < len(ROLLBACK_PHASES)
        else frozenset()
    )


def _validate_journal(journal: CacheTargetWindowJournal) -> None:
    if journal.version != 1 or journal.phase not in (*FORWARD_PHASES, *ROLLBACK_PHASES):
        raise DeploymentContractError("cache-target window journal contract is invalid")
    _canonical_uuid(journal.transaction_id, "transaction ID")
    _canonical_uuid(journal.cutover_id, "cutover ID")
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
    for label, digest in (
        ("rollback bundle", journal.rollback_bundle_sha256),
        ("candidate pair", journal.candidate_pair_sha256),
        ("Map receipt", journal.last_map_receipt_sha256),
        ("initial receipt", journal.initial_receipt_sha256),
    ):
        if digest is not None:
            _validate_sha256(digest, label)
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
    if journal.forward_boundary == "committed" and journal.phase != "forward_committed":
        raise DeploymentContractError("cache-target forward boundary phase is invalid")


def _validate_phase_evidence(journal: CacheTargetWindowJournal) -> None:
    if journal.phase in ROLLBACK_PHASES:
        if ROLLBACK_PHASES.index(journal.phase) >= ROLLBACK_PHASES.index(
            "map_db_restored"
        ) and any(
            value is None
            for value in (
                journal.rollback_bundle_sha256,
                journal.map_application_backup,
                journal.map_dagster_backup,
                journal.pinvi_backup,
            )
        ):
            raise DeploymentContractError(
                "cache-target rollback backup evidence is incomplete"
            )
        return
    phase_index = (
        FORWARD_PHASES.index(journal.phase)
    )
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
    if phase_index >= FORWARD_PHASES.index("candidate_built") and (
        journal.candidate_pair_sha256 is None
    ):
        raise DeploymentContractError("cache-target candidate evidence is missing")
    if phase_index >= FORWARD_PHASES.index("databases_forwarded") and (
        journal.last_map_receipt_sha256 is None
    ):
        raise DeploymentContractError("cache-target Map helper evidence is missing")
    if phase_index >= FORWARD_PHASES.index("initial_committed") and (
        journal.initial_receipt_sha256 is None
    ):
        raise DeploymentContractError("cache-target initial receipt evidence is missing")
    if phase_index >= FORWARD_PHASES.index("canary_verified") and (
        journal.external_event_count <= 0
    ):
        raise DeploymentContractError("cache-target causal event evidence is missing")


def _validate_backup_receipt(receipt: DatabaseBackupReceipt) -> None:
    _validate_sha256(receipt.database_identity, "database identity")
    if not _SCHEMA_REVISION.fullmatch(receipt.schema_revision):
        raise DeploymentContractError("database backup schema revision is invalid")
    _canonical_uuid(receipt.logical_backup_id, "logical backup ID")
    if type(receipt.byte_size) is not int or receipt.byte_size <= 0:
        raise DeploymentContractError("database backup size is invalid")
    _validate_sha256(receipt.sha256, "database backup")


def _validate_map_helper_receipt(receipt: MapHelperReceipt) -> None:
    _canonical_uuid(receipt.transaction_id, "Map helper transaction ID")
    if not _SOURCE_REVISION.fullmatch(receipt.source_revision):
        raise DeploymentContractError("Map helper source revision is invalid")
    _validate_sha256(receipt.database_identity, "Map helper database identity")
    _validate_sha256(receipt.request_digest, "Map helper request")
    if receipt.prior_receipt_digest is not None:
        _validate_sha256(receipt.prior_receipt_digest, "prior Map helper receipt")
    for schema in (receipt.schema_before, receipt.schema_after):
        if not _SCHEMA_REVISION.fullmatch(schema):
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
        or type(check.expected) not in {str, int, bool, type(None)}
        or type(check.observed) not in {str, int, bool, type(None)}
        for check in receipt.checks
    ):
        raise DeploymentContractError("Map helper checks are invalid")


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
