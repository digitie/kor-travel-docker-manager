from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_backup import DatabaseRole
from kor_travel_docker_manager.services.cache_target_cutover import (
    read_owner_only_state,
    write_cutover_state,
)
from kor_travel_docker_manager.services.cache_target_window import (
    _canonical_uuid,
    _validate_sha256,
)

# T-049A: `ktdctl cache-target diagnose`(T-049C가 결선)가 쓰는 typed model과 owner-only
# atomic storage. 이 모듈은 orchestration(writer fence 호출, DB backup/restore 실행,
# runtime smoke 호출)을 전혀 하지 않는다 — 그건 T-049B(DB primitive)와 T-049C(orchestration)
# 소유다. 여기서는 phase/stage/failure_class를 sealed Literal union으로 고정하고, journal이
# 항상 다음을 만족하도록 검증만 한다:
#   - `external_event_count`는 절대 0이 아니면 안 된다(진단은 read-mostly다) — 0이 아니면
#     security failure로 간주해 즉시 거부한다.
#   - receipt/journal에는 raw stdout/stderr, DSN, credential, resolved Compose 원문,
#     backup/scratch 파일 경로가 **타입 자체에** 존재하지 않는다(redaction 함수가 아니라
#     "애초에 그런 필드를 만들지 않는" design-by-construction으로 비밀을 막는다 —
#     cache_target_backup.py의 `DatabaseBackupReceipt`와 같은 원칙).
#   - 저장은 `cache_target_cutover.write_cutover_state`/`read_owner_only_state`를 그대로
#     재사용한다(0600 파일·0700 부모 디렉터리·atomic replace+fsync는 이미 그 함수가 보장한다).

DiagnosticPhase = Literal[
    "prepared",
    "writers_fencing",
    "writers_draining",
    "writers_drained",
    "writers_stopping",
    "writers_fenced",
    "map_application_checked",
    "map_dagster_checked",
    "pinvi_checked",
    "runtime_smoke_checked",
    "completed",
    "failed",
    "aborted",
]
DiagnosticStage = Literal[
    "source_archive",
    "source_schema_inventory",
    "source_data_inventory",
    "archive_structure",
    "scratch_create",
    "scratch_restore",
    "scratch_schema_inventory",
    "scratch_data_inventory",
    "scratch_cleanup",
    "writer_drain",
]
DiagnosticFailureClass = Literal[
    "subprocess_nonzero",
    "stderr_policy_rejected",
    "timeout",
    "archive_invalid",
    "admin_command_failed",
    "restore_failed",
    "inventory_mismatch",
    "cleanup_failed",
    "drain_timeout",
]
DiagnosticStageStatus = Literal["succeeded", "failed"]
LegacyDiagnosticRetirementPhase = Literal["prepared", "writers_fencing"]
InertDiagnosticRetirementPhase = Literal["prepared", "writers_fencing"]

_FORWARD_PHASES: tuple[DiagnosticPhase, ...] = (
    "prepared",
    "writers_fencing",
    "writers_draining",
    "writers_drained",
    "writers_stopping",
    "writers_fenced",
    "map_application_checked",
    "map_dagster_checked",
    "pinvi_checked",
    "runtime_smoke_checked",
    "completed",
)
TERMINAL_PHASES: frozenset[DiagnosticPhase] = frozenset({"completed", "failed", "aborted"})
_DATABASE_ROLES: frozenset[DatabaseRole] = frozenset({"map_application", "map_dagster", "pinvi"})
_DIAGNOSTIC_STAGES: frozenset[DiagnosticStage] = frozenset(
    {
        "source_archive",
        "source_schema_inventory",
        "source_data_inventory",
        "archive_structure",
        "scratch_create",
        "scratch_restore",
        "scratch_schema_inventory",
        "scratch_data_inventory",
        "scratch_cleanup",
        "writer_drain",
    }
)
_DIAGNOSTIC_FAILURE_CLASSES: frozenset[DiagnosticFailureClass] = frozenset(
    {
        "subprocess_nonzero",
        "stderr_policy_rejected",
        "timeout",
        "archive_invalid",
        "admin_command_failed",
        "restore_failed",
        "inventory_mismatch",
        "cleanup_failed",
        "drain_timeout",
    }
)
# 설계 문서 5절의 "각 60분" 시도별 budget과 같은 상한(ms). 진단 하나의 개별 stage가
# 이 값을 넘으면 receipt 자체가 계약 위반이다 — orchestration의 실제 timeout 판단(더
# 짧을 수 있다)과는 별개로, 저장 계층이 받아들이는 절대 상한이다.
_MAX_STAGE_ELAPSED_MS = 3_600_000

_IDENTITY_FIELDS = frozenset(
    {
        "manager_release_sha256",
        "pg_dump_major_version",
        "pg_restore_major_version",
        "active_pair_sha256",
        "rollback_pair_sha256",
        "raw_compose_sha256",
        "resolved_compose_sha256",
        "role_binding_sha256",
        "writer_registry_sha256",
        "smoke_contract_sha256",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "role",
        "stage",
        "status",
        "failure_class",
        "elapsed_ms",
        "archive_sha256",
        "schema_inventory_sha256",
        "data_inventory_sha256",
        "scratch_identity_sha256",
    }
)
_JOURNAL_FIELDS = frozenset(
    {
        "version",
        "diagnostic_id",
        "phase",
        "identity",
        "started_at_unix",
        "external_event_count",
        "writer_drain_lease_id",
        "writer_drain_receipt_sha256",
        "writer_drain_restore_receipt_sha256",
        "writer_fence_sha256",
        "map_application_receipts",
        "map_dagster_receipts",
        "pinvi_receipts",
        "runtime_smoke_sha256",
        "failure_stage",
        "failure_class",
        "completed_at_unix",
    }
)
_LEGACY_V1_JOURNAL_FIELDS = _JOURNAL_FIELDS.difference(
    {
        "writer_drain_lease_id",
        "writer_drain_receipt_sha256",
        "writer_drain_restore_receipt_sha256",
    }
)
_LEGACY_RETIREMENT_RECEIPT_FIELDS = frozenset(
    {
        "version",
        "retired_journal_sha256",
        "retired_phase",
        "retired_at_unix",
    }
)
_INERT_RETIREMENT_RECEIPT_FIELDS = frozenset(
    {
        "version",
        "retired_diagnostic_version",
        "retired_journal_sha256",
        "retired_phase",
        "retired_at_unix",
    }
)


@dataclass(frozen=True)
class CacheTargetDiagnosticIdentity:
    """T-VN-41 cutover gate가 재사용할 "input logical identity"(설계 문서 4절).

    실제 값(도메인, credential, 경로)은 어디에도 없다 — 전부 sha256 digest 또는
    작은 정수(major version)뿐이다.
    """

    manager_release_sha256: str
    pg_dump_major_version: int
    pg_restore_major_version: int
    active_pair_sha256: str
    rollback_pair_sha256: str
    raw_compose_sha256: str
    resolved_compose_sha256: str
    role_binding_sha256: str
    writer_registry_sha256: str
    smoke_contract_sha256: str


@dataclass(frozen=True)
class DiagnosticStageReceipt:
    """설계 문서 3절 표의 단일 stage 결과. `role`·`stage`·`status`·`failure_class`·
    bounded elapsed time·digest만 있다 — stdout/stderr/table 이름/command argv/database
    name/backup 경로/credential은 타입에 존재하지 않는다."""

    role: DatabaseRole
    stage: DiagnosticStage
    status: DiagnosticStageStatus
    failure_class: DiagnosticFailureClass | None
    elapsed_ms: int
    archive_sha256: str | None
    schema_inventory_sha256: str | None
    data_inventory_sha256: str | None
    scratch_identity_sha256: str | None


@dataclass(frozen=True)
class CacheTargetDiagnosticJournal:
    version: Literal[2]
    diagnostic_id: str
    phase: DiagnosticPhase
    identity: CacheTargetDiagnosticIdentity
    started_at_unix: int
    external_event_count: Literal[0] = 0
    writer_drain_lease_id: str | None = None
    writer_drain_receipt_sha256: str | None = None
    writer_drain_restore_receipt_sha256: str | None = None
    writer_fence_sha256: str | None = None
    map_application_receipts: tuple[DiagnosticStageReceipt, ...] = ()
    map_dagster_receipts: tuple[DiagnosticStageReceipt, ...] = ()
    pinvi_receipts: tuple[DiagnosticStageReceipt, ...] = ()
    runtime_smoke_sha256: str | None = None
    failure_stage: DiagnosticStage | None = None
    failure_class: DiagnosticFailureClass | None = None
    completed_at_unix: int | None = None


@dataclass(frozen=True)
class LegacyDiagnosticRetirementReceipt:
    """v1 pre-stop diagnostic을 폐기했다는 최소 secret-free evidence다."""

    version: Literal[1]
    retired_journal_sha256: str
    retired_phase: LegacyDiagnosticRetirementPhase
    retired_at_unix: int


@dataclass(frozen=True)
class InertDiagnosticRetirementReceipt:
    """writer-drain 전 v2 diagnostic 퇴역의 최소 비밀값 없는 evidence다."""

    version: Literal[1]
    retired_diagnostic_version: Literal[2]
    retired_journal_sha256: str
    retired_phase: InertDiagnosticRetirementPhase
    retired_at_unix: int


def prepare_cache_target_diagnostic(
    *,
    diagnostic_id: str,
    identity: CacheTargetDiagnosticIdentity,
    started_at_unix: int,
) -> CacheTargetDiagnosticJournal:
    _canonical_uuid(diagnostic_id, "diagnostic ID")
    _validate_identity(identity)
    if started_at_unix <= 0:
        raise DeploymentContractError("cache-target diagnostic start time is invalid")
    return CacheTargetDiagnosticJournal(
        version=2,
        diagnostic_id=diagnostic_id,
        phase="prepared",
        identity=identity,
        started_at_unix=started_at_unix,
    )


def transition_cache_target_diagnostic(
    journal: CacheTargetDiagnosticJournal,
    phase: DiagnosticPhase,
    *,
    writer_drain_lease_id: str | None = None,
    writer_drain_receipt_sha256: str | None = None,
    writer_drain_restore_receipt_sha256: str | None = None,
    writer_fence_sha256: str | None = None,
    map_application_receipts: tuple[DiagnosticStageReceipt, ...] | None = None,
    map_dagster_receipts: tuple[DiagnosticStageReceipt, ...] | None = None,
    pinvi_receipts: tuple[DiagnosticStageReceipt, ...] | None = None,
    runtime_smoke_sha256: str | None = None,
    failure_stage: DiagnosticStage | None = None,
    failure_class: DiagnosticFailureClass | None = None,
    completed_at_unix: int | None = None,
) -> CacheTargetDiagnosticJournal:
    if phase not in _allowed_next_phases(journal):
        raise DeploymentContractError("cache-target diagnostic phase transition is invalid")
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
        writer_fence_sha256=(
            writer_fence_sha256 if writer_fence_sha256 is not None else journal.writer_fence_sha256
        ),
        map_application_receipts=(
            map_application_receipts
            if map_application_receipts is not None
            else journal.map_application_receipts
        ),
        map_dagster_receipts=(
            map_dagster_receipts
            if map_dagster_receipts is not None
            else journal.map_dagster_receipts
        ),
        pinvi_receipts=(pinvi_receipts if pinvi_receipts is not None else journal.pinvi_receipts),
        runtime_smoke_sha256=(
            runtime_smoke_sha256
            if runtime_smoke_sha256 is not None
            else journal.runtime_smoke_sha256
        ),
        failure_stage=(failure_stage if failure_stage is not None else journal.failure_stage),
        failure_class=(failure_class if failure_class is not None else journal.failure_class),
        completed_at_unix=(
            completed_at_unix if completed_at_unix is not None else journal.completed_at_unix
        ),
    )
    _validate_journal(updated)
    _validate_phase_evidence(updated)
    return updated


def diagnostic_receipt_is_fresh(
    journal: CacheTargetDiagnosticJournal,
    *,
    current_identity: CacheTargetDiagnosticIdentity,
    now_unix: int,
    max_age_seconds: int,
) -> bool:
    """T-049D의 cutover gate가 재사용할 stale-input 판정(설계 문서 4절).

    `completed`이고 지금 identity와 정확히 같고 만료되지 않은 receipt만 fresh다.
    receipt가 없거나(호출측이 애초에 이 함수를 호출하지 않으면 자연히 처리), `failed`/
    `aborted`거나, identity 중 하나라도 다르거나, 만료됐으면 `False` — 새 cutover를 시작할
    수 없다.
    """
    if journal.phase != "completed" or journal.completed_at_unix is None:
        return False
    if journal.identity != current_identity:
        return False
    if now_unix < journal.completed_at_unix:
        return False
    return now_unix - journal.completed_at_unix <= max_age_seconds


def write_cache_target_diagnostic(path: Path, journal: CacheTargetDiagnosticJournal) -> str:
    _validate_journal(journal)
    _validate_phase_evidence(journal)
    return write_cutover_state(path, journal)  # type: ignore[arg-type]


def read_cache_target_diagnostic(path: Path) -> CacheTargetDiagnosticJournal:
    try:
        document = json.loads(read_owner_only_state(path))
        return _diagnostic_journal_from_document(document)
    except DeploymentContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError("cache-target diagnostic journal is invalid") from exc


def _diagnostic_journal_from_document(document: object) -> CacheTargetDiagnosticJournal:
    if isinstance(document, dict) and document.get("version") == 1:
        raise DeploymentContractError(
            "cache-target diagnostic journal v1 is unsupported; "
            "reset the isolated state before TVN41"
        )
    if not isinstance(document, dict) or set(document) != _JOURNAL_FIELDS:
        raise TypeError
    parsed = dict(document)
    identity_value = parsed["identity"]
    if not isinstance(identity_value, dict) or set(identity_value) != _IDENTITY_FIELDS:
        raise TypeError
    parsed["identity"] = CacheTargetDiagnosticIdentity(**identity_value)
    for field_name in (
        "map_application_receipts",
        "map_dagster_receipts",
        "pinvi_receipts",
    ):
        receipts_value = parsed[field_name]
        if not isinstance(receipts_value, list):
            raise TypeError
        parsed_receipts: list[DiagnosticStageReceipt] = []
        for item in receipts_value:
            if not isinstance(item, dict) or set(item) != _RECEIPT_FIELDS:
                raise TypeError
            parsed_receipts.append(DiagnosticStageReceipt(**item))
        parsed[field_name] = tuple(parsed_receipts)
    journal = CacheTargetDiagnosticJournal(**parsed)
    _validate_journal(journal)
    _validate_phase_evidence(journal)
    return journal


def retire_legacy_pre_stop_cache_target_diagnostic(
    path: Path,
    *,
    retired_at_unix: int,
) -> LegacyDiagnosticRetirementReceipt:
    """writer stop 이전 v1 diagnostic만 receipt-first로 퇴역한다.

    v1은 durable writer-drain lease/restore evidence가 없으므로 post-drain state를
    migrate하거나 recovery하지 않는다. 이 함수는 exact source journal 하나만
    unlink하고, current attempt log와 다른 C6c state는 호출자가 접근할 수 없다.
    """

    if retired_at_unix <= 0:
        raise DeploymentContractError("legacy diagnostic retirement time is invalid")
    receipt_path = legacy_diagnostic_retirement_receipt_path(path)
    try:
        receipt_path.lstat()
    except FileNotFoundError:
        existing_receipt = None
    except OSError as exc:
        raise DeploymentContractError(
            "legacy diagnostic retirement receipt path is unavailable"
        ) from exc
    else:
        existing_receipt = read_legacy_diagnostic_retirement_receipt(receipt_path)

    try:
        path.lstat()
    except FileNotFoundError:
        if existing_receipt is not None:
            try:
                _fsync_state_directory(path.parent)
            except OSError as exc:
                raise DeploymentContractError(
                    "legacy diagnostic journal retirement recovery failed"
                ) from exc
            return existing_receipt
        raise DeploymentContractError("legacy diagnostic journal is unavailable") from None
    except OSError as exc:
        raise DeploymentContractError("legacy diagnostic journal is unavailable") from exc

    raw, source_identity, phase = _read_legacy_pre_stop_diagnostic(path)
    retired_journal_sha256 = hashlib.sha256(raw).hexdigest()
    receipt = LegacyDiagnosticRetirementReceipt(
        version=1,
        retired_journal_sha256=retired_journal_sha256,
        retired_phase=phase,
        retired_at_unix=retired_at_unix,
    )
    _validate_legacy_retirement_receipt(receipt)
    if existing_receipt is None:
        write_legacy_diagnostic_retirement_receipt(receipt_path, receipt)
    else:
        if (
            existing_receipt.retired_journal_sha256 != receipt.retired_journal_sha256
            or existing_receipt.retired_phase != receipt.retired_phase
        ):
            raise DeploymentContractError(
                "legacy diagnostic retirement receipt conflicts with the journal"
            )
        receipt = existing_receipt

    current_raw, current_identity, current_phase = _read_legacy_pre_stop_diagnostic(path)
    if (
        current_raw != raw
        or current_identity != source_identity
        or current_phase != phase
    ):
        raise DeploymentContractError("legacy diagnostic journal changed before retirement")
    try:
        path.unlink()
        _fsync_state_directory(path.parent)
    except OSError as exc:
        raise DeploymentContractError("legacy diagnostic journal retirement failed") from exc
    return receipt


def legacy_diagnostic_retirement_receipt_path(
    path: Path,
) -> Path:
    return path.with_name("cache-target-diagnostic-retirement-v1.json")


def write_legacy_diagnostic_retirement_receipt(
    path: Path,
    receipt: LegacyDiagnosticRetirementReceipt,
) -> str:
    _validate_legacy_retirement_receipt(receipt)
    return write_cutover_state(path, receipt)  # type: ignore[arg-type]


def read_legacy_diagnostic_retirement_receipt(
    path: Path,
) -> LegacyDiagnosticRetirementReceipt:
    try:
        document = json.loads(read_owner_only_state(path))
        if not isinstance(document, dict) or set(document) != _LEGACY_RETIREMENT_RECEIPT_FIELDS:
            raise TypeError
        receipt = LegacyDiagnosticRetirementReceipt(**document)
    except DeploymentContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError("legacy diagnostic retirement receipt is invalid") from exc
    _validate_legacy_retirement_receipt(receipt)
    return receipt


def retire_inert_cache_target_diagnostic(
    path: Path,
    *,
    retired_at_unix: int,
) -> InertDiagnosticRetirementReceipt:
    """writer-drain 전 exact v2 diagnostic만 receipt-first로 퇴역한다."""

    if type(retired_at_unix) is not int or retired_at_unix <= 0:
        raise DeploymentContractError("inert diagnostic retirement time is invalid")
    receipt_path = inert_diagnostic_retirement_receipt_path(path)
    try:
        receipt_path.lstat()
    except FileNotFoundError:
        existing_receipt = None
    except OSError as exc:
        raise DeploymentContractError(
            "inert diagnostic retirement receipt path is unavailable"
        ) from exc
    else:
        existing_receipt = read_inert_diagnostic_retirement_receipt(receipt_path)

    try:
        path.lstat()
    except FileNotFoundError:
        if existing_receipt is not None:
            try:
                _fsync_state_directory(path.parent)
            except OSError as exc:
                raise DeploymentContractError(
                    "inert diagnostic retirement recovery failed"
                ) from exc
            return existing_receipt
        raise DeploymentContractError("inert diagnostic journal is unavailable") from None
    except OSError as exc:
        raise DeploymentContractError("inert diagnostic journal is unavailable") from exc

    raw, source_identity, phase = _read_inert_current_diagnostic(path)
    receipt = InertDiagnosticRetirementReceipt(
        version=1,
        retired_diagnostic_version=2,
        retired_journal_sha256=hashlib.sha256(raw).hexdigest(),
        retired_phase=phase,
        retired_at_unix=retired_at_unix,
    )
    _validate_inert_retirement_receipt(receipt)
    if existing_receipt is None:
        write_inert_diagnostic_retirement_receipt(receipt_path, receipt)
    else:
        if (
            existing_receipt.retired_diagnostic_version
            != receipt.retired_diagnostic_version
            or existing_receipt.retired_journal_sha256 != receipt.retired_journal_sha256
            or existing_receipt.retired_phase != receipt.retired_phase
        ):
            raise DeploymentContractError(
                "inert diagnostic retirement receipt conflicts with the journal"
            )
        receipt = existing_receipt

    current_raw, current_identity, current_phase = _read_inert_current_diagnostic(path)
    if (
        current_raw != raw
        or current_identity != source_identity
        or current_phase != phase
    ):
        raise DeploymentContractError("inert diagnostic journal changed before retirement")
    try:
        path.unlink()
        _fsync_state_directory(path.parent)
    except OSError as exc:
        raise DeploymentContractError("inert diagnostic retirement failed") from exc
    return receipt


def inert_diagnostic_retirement_receipt_path(path: Path) -> Path:
    return path.with_name("cache-target-diagnostic-inert-retirement-v1.json")


def write_inert_diagnostic_retirement_receipt(
    path: Path,
    receipt: InertDiagnosticRetirementReceipt,
) -> str:
    _validate_inert_retirement_receipt(receipt)
    return write_cutover_state(path, receipt)  # type: ignore[arg-type]


def read_inert_diagnostic_retirement_receipt(
    path: Path,
) -> InertDiagnosticRetirementReceipt:
    try:
        document = json.loads(read_owner_only_state(path))
        if not isinstance(document, dict) or set(document) != _INERT_RETIREMENT_RECEIPT_FIELDS:
            raise TypeError
        receipt = InertDiagnosticRetirementReceipt(**document)
    except DeploymentContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError("inert diagnostic retirement receipt is invalid") from exc
    _validate_inert_retirement_receipt(receipt)
    return receipt


def _read_inert_current_diagnostic(
    path: Path,
) -> tuple[bytes, tuple[int, int, int, int, int, int], InertDiagnosticRetirementPhase]:
    source_identity = _owner_only_file_identity(path)
    try:
        raw = read_owner_only_state(path)
        journal = _diagnostic_journal_from_document(json.loads(raw))
    except DeploymentContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError(
            "current diagnostic journal is not eligible for inert retirement"
        ) from exc
    if not _is_inert_diagnostic(journal):
        raise DeploymentContractError(
            "current diagnostic journal is not eligible for inert retirement"
        )
    current_identity = _owner_only_file_identity(path)
    if current_identity != source_identity:
        raise DeploymentContractError("inert diagnostic journal changed while reading")
    return raw, source_identity, cast(InertDiagnosticRetirementPhase, journal.phase)


def _is_inert_diagnostic(journal: CacheTargetDiagnosticJournal) -> bool:
    return (
        journal.phase in {"prepared", "writers_fencing"}
        and journal.external_event_count == 0
        and journal.writer_drain_lease_id is None
        and journal.writer_drain_receipt_sha256 is None
        and journal.writer_drain_restore_receipt_sha256 is None
        and journal.writer_fence_sha256 is None
        and not journal.map_application_receipts
        and not journal.map_dagster_receipts
        and not journal.pinvi_receipts
        and journal.runtime_smoke_sha256 is None
        and journal.failure_stage is None
        and journal.failure_class is None
        and journal.completed_at_unix is None
    )


def _validate_inert_retirement_receipt(
    receipt: InertDiagnosticRetirementReceipt,
) -> None:
    if type(receipt.version) is not int or receipt.version != 1:
        raise DeploymentContractError("inert diagnostic retirement receipt version is invalid")
    if (
        type(receipt.retired_diagnostic_version) is not int
        or receipt.retired_diagnostic_version != 2
    ):
        raise DeploymentContractError(
            "inert diagnostic retirement source version is invalid"
        )
    if not isinstance(receipt.retired_journal_sha256, str):
        raise DeploymentContractError("inert diagnostic retirement journal is invalid")
    _validate_sha256(
        receipt.retired_journal_sha256,
        "inert diagnostic retirement journal",
    )
    if (
        not isinstance(receipt.retired_phase, str)
        or receipt.retired_phase not in {"prepared", "writers_fencing"}
    ):
        raise DeploymentContractError("inert diagnostic retirement phase is invalid")
    if type(receipt.retired_at_unix) is not int or receipt.retired_at_unix <= 0:
        raise DeploymentContractError("inert diagnostic retirement time is invalid")


def _read_legacy_pre_stop_diagnostic(
    path: Path,
) -> tuple[bytes, tuple[int, int, int, int, int, int], LegacyDiagnosticRetirementPhase]:
    source_identity = _owner_only_file_identity(path)
    try:
        raw = read_owner_only_state(path)
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != _LEGACY_V1_JOURNAL_FIELDS:
            raise TypeError
        if type(document["version"]) is not int or document["version"] != 1:
            raise ValueError
        phase = document["phase"]
        if not isinstance(phase, str) or phase not in {"prepared", "writers_fencing"}:
            raise ValueError
        diagnostic_id = document["diagnostic_id"]
        if not isinstance(diagnostic_id, str):
            raise TypeError
        _canonical_uuid(diagnostic_id, "legacy diagnostic ID")
        identity_value = document["identity"]
        if (
            not isinstance(identity_value, dict)
            or set(identity_value) != _IDENTITY_FIELDS
            or type(document["started_at_unix"]) is not int
            or document["started_at_unix"] <= 0
            or type(document["external_event_count"]) is not int
            or document["external_event_count"] != 0
            or document["writer_fence_sha256"] is not None
            or any(
                not isinstance(document[field], list) or document[field]
                for field in (
                    "map_application_receipts",
                    "map_dagster_receipts",
                    "pinvi_receipts",
                )
            )
            or document["runtime_smoke_sha256"] is not None
            or document["failure_stage"] is not None
            or document["failure_class"] is not None
            or document["completed_at_unix"] is not None
        ):
            raise ValueError
        _validate_identity(CacheTargetDiagnosticIdentity(**identity_value))
    except DeploymentContractError:
        raise
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise DeploymentContractError(
            "legacy diagnostic journal is not an eligible pre-stop v1 state"
        ) from exc
    current_identity = _owner_only_file_identity(path)
    if current_identity != source_identity:
        raise DeploymentContractError("legacy diagnostic journal changed while reading")
    return raw, source_identity, cast(LegacyDiagnosticRetirementPhase, phase)


def _owner_only_file_identity(path: Path) -> tuple[int, int, int, int, int, int]:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError("legacy diagnostic journal is unavailable") from exc
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != os.geteuid()
        or file_stat.st_nlink != 1
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise DeploymentContractError("legacy diagnostic journal is unsafe")
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_uid,
        file_stat.st_nlink,
        stat.S_IMODE(file_stat.st_mode),
        file_stat.st_size,
    )


def _validate_legacy_retirement_receipt(
    receipt: LegacyDiagnosticRetirementReceipt,
) -> None:
    if type(receipt.version) is not int or receipt.version != 1:
        raise DeploymentContractError("legacy diagnostic retirement receipt version is invalid")
    if not isinstance(receipt.retired_journal_sha256, str):
        raise DeploymentContractError("legacy diagnostic retirement journal is invalid")
    _validate_sha256(
        receipt.retired_journal_sha256,
        "legacy diagnostic retirement journal",
    )
    if (
        not isinstance(receipt.retired_phase, str)
        or receipt.retired_phase not in {"prepared", "writers_fencing"}
    ):
        raise DeploymentContractError("legacy diagnostic retirement phase is invalid")
    if type(receipt.retired_at_unix) is not int or receipt.retired_at_unix <= 0:
        raise DeploymentContractError("legacy diagnostic retirement time is invalid")


def _fsync_state_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def archive_cache_target_diagnostic(
    path: Path,
    journal: CacheTargetDiagnosticJournal,
) -> Path:
    """검증을 마친 terminal journal을 owner-only archive로 원자 이동한다.

    canonical path에는 현재 diagnostic 하나만 둘 수 있다. 새 ID가 terminal 이전
    receipt를 명시적으로 supersede할 때, receipt와 attempt log는 보존하면서 이 함수로
    canonical path를 비운다. archive target 충돌은 기록 유실 가능성이므로 fail-close한다.
    """

    _validate_journal(journal)
    _validate_phase_evidence(journal)
    if journal.phase not in TERMINAL_PHASES:
        raise DeploymentContractError(
            "cache-target diagnostic archive requires a terminal journal phase"
        )
    if read_cache_target_diagnostic(path) != journal:
        raise DeploymentContractError(
            "cache-target diagnostic journal changed before archive"
        )
    archive_path = path.with_name(
        f"cache-target-diagnostic-archive-v1-{journal.diagnostic_id}-{journal.phase}.json"
    )
    try:
        archive_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise DeploymentContractError(
            "cache-target diagnostic archive path is unavailable"
        ) from exc
    else:
        raise DeploymentContractError("cache-target diagnostic archive already exists")
    try:
        os.replace(path, archive_path)
        if read_cache_target_diagnostic(archive_path) != journal:
            raise DeploymentContractError(
                "cache-target diagnostic archive verification failed"
            )
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except DeploymentContractError:
        raise
    except OSError as exc:
        raise DeploymentContractError("cache-target diagnostic archive failed") from exc
    return archive_path


def _allowed_next_phases(
    journal: CacheTargetDiagnosticJournal,
) -> frozenset[DiagnosticPhase]:
    if journal.phase in TERMINAL_PHASES:
        return frozenset()
    index = _FORWARD_PHASES.index(journal.phase)
    allowed: set[DiagnosticPhase] = {"failed", "aborted"}
    if index + 1 < len(_FORWARD_PHASES):
        allowed.add(_FORWARD_PHASES[index + 1])
    return frozenset(allowed)


def _validate_identity(identity: CacheTargetDiagnosticIdentity) -> None:
    for label, digest in (
        ("Manager release", identity.manager_release_sha256),
        ("active pair", identity.active_pair_sha256),
        ("rollback pair", identity.rollback_pair_sha256),
        ("raw compose", identity.raw_compose_sha256),
        ("resolved compose", identity.resolved_compose_sha256),
        ("role binding", identity.role_binding_sha256),
        ("writer registry", identity.writer_registry_sha256),
        ("smoke contract", identity.smoke_contract_sha256),
    ):
        _validate_sha256(digest, label)
    for label, major_version in (
        ("pg_dump", identity.pg_dump_major_version),
        ("pg_restore", identity.pg_restore_major_version),
    ):
        if (
            not isinstance(major_version, int)
            or isinstance(major_version, bool)
            or not (1 <= major_version <= 999)
        ):
            raise DeploymentContractError(
                f"cache-target diagnostic {label} major version is invalid"
            )


def _validate_stage_receipt(receipt: DiagnosticStageReceipt) -> None:
    if receipt.role not in _DATABASE_ROLES:
        raise DeploymentContractError("cache-target diagnostic receipt role is invalid")
    if receipt.stage not in _DIAGNOSTIC_STAGES:
        raise DeploymentContractError("cache-target diagnostic receipt stage is invalid")
    if receipt.status not in ("succeeded", "failed"):
        raise DeploymentContractError("cache-target diagnostic receipt status is invalid")
    if receipt.status == "succeeded" and receipt.failure_class is not None:
        raise DeploymentContractError(
            "cache-target diagnostic succeeded receipt must not carry a failure class"
        )
    if receipt.status == "failed" and receipt.failure_class is None:
        raise DeploymentContractError(
            "cache-target diagnostic failed receipt requires a failure class"
        )
    if (
        receipt.failure_class is not None
        and receipt.failure_class not in _DIAGNOSTIC_FAILURE_CLASSES
    ):
        raise DeploymentContractError("cache-target diagnostic receipt failure class is invalid")
    if (
        not isinstance(receipt.elapsed_ms, int)
        or isinstance(receipt.elapsed_ms, bool)
        or not (0 <= receipt.elapsed_ms <= _MAX_STAGE_ELAPSED_MS)
    ):
        raise DeploymentContractError("cache-target diagnostic receipt elapsed time is invalid")
    for label, digest in (
        ("archive", receipt.archive_sha256),
        ("schema inventory", receipt.schema_inventory_sha256),
        ("data inventory", receipt.data_inventory_sha256),
        ("scratch identity", receipt.scratch_identity_sha256),
    ):
        if digest is not None:
            _validate_sha256(digest, label)


def _validate_journal(journal: CacheTargetDiagnosticJournal) -> None:
    if (
        type(journal.version) is not int
        or journal.version != 2
        or journal.phase not in (*_FORWARD_PHASES, "failed", "aborted")
    ):
        raise DeploymentContractError("cache-target diagnostic journal contract is invalid")
    _canonical_uuid(journal.diagnostic_id, "diagnostic ID")
    if journal.writer_drain_lease_id is not None:
        _canonical_uuid(journal.writer_drain_lease_id, "diagnostic writer drain lease ID")
    if journal.writer_drain_receipt_sha256 is not None:
        _validate_sha256(
            journal.writer_drain_receipt_sha256,
            "diagnostic writer drain receipt",
        )
    if journal.writer_drain_restore_receipt_sha256 is not None:
        _validate_sha256(
            journal.writer_drain_restore_receipt_sha256,
            "diagnostic writer drain restore receipt",
        )
    _validate_identity(journal.identity)
    if type(journal.started_at_unix) is not int or journal.started_at_unix <= 0:
        raise DeploymentContractError("cache-target diagnostic start time is invalid")
    # 설계 문서 2절: 진단은 read-mostly라 external event는 절대 0이어야 한다. 0이 아니면
    # 진단 자체를 security failure로 취급한다(단순 계약 위반이 아니라 즉시 fail-close).
    if type(journal.external_event_count) is not int or journal.external_event_count != 0:
        raise DeploymentContractError(
            "cache-target diagnostic observed a non-zero external event count"
        )
    if journal.writer_fence_sha256 is not None:
        _validate_sha256(journal.writer_fence_sha256, "writer fence")
    if journal.runtime_smoke_sha256 is not None:
        _validate_sha256(journal.runtime_smoke_sha256, "runtime smoke")
    # 적대적 리뷰(2명)가 찾은 공백: role 자체는 `_validate_stage_receipt`가 sealed
    # `DatabaseRole`인지 검사하지만, 그 receipt가 *어느 tuple에 담겼는지*와 role이
    # 실제로 일치하는지는 아무도 확인하지 않았다 — `map_application_receipts`에
    # `role="pinvi"` receipt가 그대로 저장돼도 검증을 통과했다. tuple identity와
    # role을 여기서 명시적으로 결박한다.
    for role, receipts in (
        ("map_application", journal.map_application_receipts),
        ("map_dagster", journal.map_dagster_receipts),
        ("pinvi", journal.pinvi_receipts),
    ):
        for receipt in receipts:
            _validate_stage_receipt(receipt)
            if receipt.role != role:
                raise DeploymentContractError(
                    f"cache-target diagnostic {role} receipts contain a {receipt.role} receipt"
                )
    # 적대적 리뷰가 찾은 두 번째 공백: `completed`에 도달했다는 것만으로는 모든
    # stage가 실제로 성공했다는 보장이 없었다 — 누적된 receipt 중 하나가
    # `status="failed"`여도 `completed`로 전이하는 것을 막지 않았다. 설계 문서
    # 3절의 의도(어느 stage든 실패하면 전체 진단이 `failed` terminal로 가야 한다)를
    # 여기서 강제한다.
    if journal.phase == "completed":
        for receipts in (
            journal.map_application_receipts,
            journal.map_dagster_receipts,
            journal.pinvi_receipts,
        ):
            if any(receipt.status != "succeeded" for receipt in receipts):
                raise DeploymentContractError(
                    "cache-target diagnostic completed phase requires every "
                    "recorded stage to have succeeded"
                )
    if journal.failure_stage is not None and journal.failure_stage not in _DIAGNOSTIC_STAGES:
        raise DeploymentContractError("cache-target diagnostic failure stage is invalid")
    if (
        journal.failure_class is not None
        and journal.failure_class not in _DIAGNOSTIC_FAILURE_CLASSES
    ):
        raise DeploymentContractError("cache-target diagnostic failure class is invalid")
    if journal.phase == "failed" and journal.failure_class is None:
        raise DeploymentContractError(
            "cache-target diagnostic failed phase requires a failure class"
        )
    if journal.phase != "failed" and (
        journal.failure_stage is not None or journal.failure_class is not None
    ):
        raise DeploymentContractError(
            "cache-target diagnostic failure evidence is only valid in the failed phase"
        )
    if journal.completed_at_unix is not None:
        if journal.phase != "completed":
            raise DeploymentContractError(
                "cache-target diagnostic completion time is only valid once completed"
            )
        if journal.completed_at_unix < journal.started_at_unix:
            raise DeploymentContractError(
                "cache-target diagnostic completion time precedes its start time"
            )
    if journal.phase == "completed" and journal.completed_at_unix is None:
        raise DeploymentContractError(
            "cache-target diagnostic completed phase requires a completion time"
        )


# T-049C: 설계 문서 5절의 abort budget(24시간 내 2회, 같은 failure_stage/failure_class
# 재현 시 aborted) 모델. orchestration(T-049C)이 진단을 시작하기 전에 이 log를 읽어
# budget 초과를 거부하고, 진단이 끝나면(모든 terminal phase) attempt를 기록한다. 이
# 파일도 cache_target_cutover의 owner-only atomic storage를 재사용한다.

_ATTEMPT_FIELDS = frozenset(
    {
        "diagnostic_id",
        "started_at_unix",
        "phase",
        "failure_stage",
        "failure_class",
    }
)
_ATTEMPT_LOG_FIELDS = frozenset({"version", "attempts"})
_ABORT_BUDGET_WINDOW_SECONDS = 86_400
_ABORT_BUDGET_MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class DiagnosticAttemptRecord:
    diagnostic_id: str
    started_at_unix: int
    phase: DiagnosticPhase
    failure_stage: DiagnosticStage | None
    failure_class: DiagnosticFailureClass | None


@dataclass(frozen=True)
class DiagnosticAttemptLog:
    version: Literal[1]
    attempts: tuple[DiagnosticAttemptRecord, ...] = ()


def _validate_attempt_record(record: DiagnosticAttemptRecord) -> None:
    _canonical_uuid(record.diagnostic_id, "diagnostic attempt ID")
    if record.started_at_unix <= 0:
        raise DeploymentContractError("cache-target diagnostic attempt start time is invalid")
    if record.phase not in TERMINAL_PHASES:
        raise DeploymentContractError("cache-target diagnostic attempt phase is invalid")
    if record.failure_stage is not None and record.failure_stage not in _DIAGNOSTIC_STAGES:
        raise DeploymentContractError("cache-target diagnostic attempt failure stage is invalid")
    if record.failure_class is not None and record.failure_class not in _DIAGNOSTIC_FAILURE_CLASSES:
        raise DeploymentContractError("cache-target diagnostic attempt failure class is invalid")
    if record.phase == "failed" and record.failure_class is None:
        raise DeploymentContractError(
            "cache-target diagnostic attempt failed phase requires a failure class"
        )
    if record.phase != "failed" and (
        record.failure_stage is not None or record.failure_class is not None
    ):
        raise DeploymentContractError(
            "cache-target diagnostic attempt failure evidence is only valid in the failed phase"
        )


def _validate_attempt_log(log: DiagnosticAttemptLog) -> None:
    if log.version != 1:
        raise DeploymentContractError("cache-target diagnostic attempt log contract is invalid")
    for record in log.attempts:
        _validate_attempt_record(record)
    ids = [record.diagnostic_id for record in log.attempts]
    if len(ids) != len(set(ids)):
        raise DeploymentContractError(
            "cache-target diagnostic attempt log contains duplicate diagnostic IDs"
        )


def prune_diagnostic_attempt_log(
    log: DiagnosticAttemptLog,
    *,
    now_unix: int,
    window_seconds: int = _ABORT_BUDGET_WINDOW_SECONDS,
) -> DiagnosticAttemptLog:
    """`window_seconds` 밖의 attempt를 제거한다. budget 판정 전에 항상 호출한다."""

    pruned = tuple(
        record for record in log.attempts if now_unix - record.started_at_unix <= window_seconds
    )
    return replace(log, attempts=pruned)


def diagnostic_attempt_budget_exceeded(
    log: DiagnosticAttemptLog,
    *,
    now_unix: int,
    window_seconds: int = _ABORT_BUDGET_WINDOW_SECONDS,
    max_attempts: int = _ABORT_BUDGET_MAX_ATTEMPTS,
) -> bool:
    """새 진단을 시작하기 전에 호출한다. 이미 window 안에 `max_attempts`회를 다 썼으면
    새 attempt를 시작해선 안 된다(설계 문서 5절 — 자동 재시도가 아니라 operator가
    명시적으로 시작할 수 있는 횟수 자체의 상한)."""

    pruned = prune_diagnostic_attempt_log(log, now_unix=now_unix, window_seconds=window_seconds)
    return len(pruned.attempts) >= max_attempts


def diagnostic_failure_is_reproduced(
    log: DiagnosticAttemptLog,
    *,
    now_unix: int,
    failure_stage: DiagnosticStage,
    failure_class: DiagnosticFailureClass,
    window_seconds: int = _ABORT_BUDGET_WINDOW_SECONDS,
) -> bool:
    """가장 최근 window 내 `failed` attempt가 같은 (failure_stage, failure_class)로
    실패했는지 확인한다. 같으면 orchestration은 `failed` 대신 `aborted`로 끝내야 한다.

    `aborted` attempt는 journal 계약상 failure_stage/failure_class를 싣지 않으므로
    (`_validate_attempt_record`가 `failed`에서만 허용) 단순히 "가장 최근 attempt"가
    아니라 가장 최근 **`failed`** attempt와 비교해야 한다. `max_attempts`가 2보다
    커지거나 window 안에 `failed` 뒤에 `aborted`가 이어져도, 재현 여부는 항상 마지막
    실제 실패와 비교되도록 한다.
    """

    pruned = prune_diagnostic_attempt_log(log, now_unix=now_unix, window_seconds=window_seconds)
    failed_attempts = [record for record in pruned.attempts if record.phase == "failed"]
    if not failed_attempts:
        return False
    latest = max(failed_attempts, key=lambda record: record.started_at_unix)
    return latest.failure_stage == failure_stage and latest.failure_class == failure_class


def record_diagnostic_attempt(
    log: DiagnosticAttemptLog,
    journal: CacheTargetDiagnosticJournal,
    *,
    now_unix: int,
) -> DiagnosticAttemptLog:
    if journal.phase not in TERMINAL_PHASES:
        raise DeploymentContractError(
            "cache-target diagnostic attempt requires a terminal journal phase"
        )
    pruned = prune_diagnostic_attempt_log(log, now_unix=now_unix)
    if any(record.diagnostic_id == journal.diagnostic_id for record in pruned.attempts):
        raise DeploymentContractError(
            "cache-target diagnostic attempt already recorded for this diagnostic ID"
        )
    record = DiagnosticAttemptRecord(
        diagnostic_id=journal.diagnostic_id,
        started_at_unix=journal.started_at_unix,
        phase=journal.phase,
        failure_stage=journal.failure_stage,
        failure_class=journal.failure_class,
    )
    updated = replace(pruned, attempts=(*pruned.attempts, record))
    _validate_attempt_log(updated)
    return updated


def write_cache_target_diagnostic_attempt_log(path: Path, log: DiagnosticAttemptLog) -> str:
    _validate_attempt_log(log)
    return write_cutover_state(path, log)  # type: ignore[arg-type]


def read_cache_target_diagnostic_attempt_log(path: Path) -> DiagnosticAttemptLog:
    try:
        document = json.loads(read_owner_only_state(path))
        if not isinstance(document, dict) or set(document) != _ATTEMPT_LOG_FIELDS:
            raise TypeError
        attempts_value = document["attempts"]
        if not isinstance(attempts_value, list):
            raise TypeError
        parsed: list[DiagnosticAttemptRecord] = []
        for item in attempts_value:
            if not isinstance(item, dict) or set(item) != _ATTEMPT_FIELDS:
                raise TypeError
            parsed.append(DiagnosticAttemptRecord(**item))
        document["attempts"] = tuple(parsed)
        log = DiagnosticAttemptLog(**document)
    except DeploymentContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError("cache-target diagnostic attempt log is invalid") from exc
    _validate_attempt_log(log)
    return log


def read_or_create_cache_target_diagnostic_attempt_log(path: Path) -> DiagnosticAttemptLog:
    try:
        path.lstat()
    except FileNotFoundError:
        return DiagnosticAttemptLog(version=1)
    return read_cache_target_diagnostic_attempt_log(path)


def _validate_phase_evidence(journal: CacheTargetDiagnosticJournal) -> None:
    if journal.phase in ("failed", "aborted"):
        return
    index = _FORWARD_PHASES.index(journal.phase)
    # 적대적 리뷰(2명)가 찾은 공백: 아래 두 검사는 각각 phase 문턱으로만 걸려 있어서,
    # restore receipt가 있는데 그보다 먼저 있어야 할 lease/receipt가 없는(논리적으로
    # 불가능한) journal이 어느 phase에서든 통과할 수 있었다 — restore는 lease/receipt
    # 없이는 절대 존재할 수 없다는 불변식을 phase와 무관하게 명시적으로 강제한다.
    if journal.writer_drain_restore_receipt_sha256 is not None and (
        journal.writer_drain_lease_id is None or journal.writer_drain_receipt_sha256 is None
    ):
        raise DeploymentContractError(
            "cache-target diagnostic writer drain restore evidence precedes its lease"
        )
    if index >= _FORWARD_PHASES.index("writers_drained") and (
        journal.writer_drain_lease_id is None
        or journal.writer_drain_receipt_sha256 is None
    ):
        raise DeploymentContractError(
            "cache-target diagnostic writer drain evidence is missing"
        )
    if index >= _FORWARD_PHASES.index("completed") and (
        journal.writer_drain_restore_receipt_sha256 is None
    ):
        raise DeploymentContractError(
            "cache-target diagnostic writer drain restore evidence is missing"
        )
    if index >= _FORWARD_PHASES.index("writers_fenced") and journal.writer_fence_sha256 is None:
        raise DeploymentContractError("cache-target diagnostic writer fence evidence is missing")
    if (
        index >= _FORWARD_PHASES.index("map_application_checked")
        and not journal.map_application_receipts
    ):
        raise DeploymentContractError("cache-target diagnostic Map application evidence is missing")
    if index >= _FORWARD_PHASES.index("map_dagster_checked") and not journal.map_dagster_receipts:
        raise DeploymentContractError("cache-target diagnostic Map Dagster evidence is missing")
    if index >= _FORWARD_PHASES.index("pinvi_checked") and not journal.pinvi_receipts:
        raise DeploymentContractError("cache-target diagnostic PinVi evidence is missing")
    if (
        index >= _FORWARD_PHASES.index("runtime_smoke_checked")
        and journal.runtime_smoke_sha256 is None
    ):
        raise DeploymentContractError("cache-target diagnostic runtime smoke evidence is missing")
