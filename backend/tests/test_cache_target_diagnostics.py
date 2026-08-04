from __future__ import annotations

import json
import stat
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_diagnostics import (
    CacheTargetDiagnosticIdentity,
    CacheTargetDiagnosticJournal,
    DiagnosticAttemptLog,
    DiagnosticStageReceipt,
    diagnostic_attempt_budget_exceeded,
    diagnostic_failure_is_reproduced,
    diagnostic_receipt_is_fresh,
    prepare_cache_target_diagnostic,
    read_cache_target_diagnostic,
    read_cache_target_diagnostic_attempt_log,
    read_or_create_cache_target_diagnostic_attempt_log,
    record_diagnostic_attempt,
    transition_cache_target_diagnostic,
    write_cache_target_diagnostic,
    write_cache_target_diagnostic_attempt_log,
)

_DIAGNOSTIC_ID = "8a3e6b2c-8f1e-4c8b-9c3d-0f1a2b3c4d5e"


def _identity(**overrides: object) -> CacheTargetDiagnosticIdentity:
    fields: dict[str, object] = {
        "manager_release_sha256": "1" * 64,
        "pg_dump_major_version": 16,
        "pg_restore_major_version": 16,
        "active_pair_sha256": "2" * 64,
        "rollback_pair_sha256": "3" * 64,
        "raw_compose_sha256": "4" * 64,
        "resolved_compose_sha256": "5" * 64,
        "role_binding_sha256": "6" * 64,
        "writer_registry_sha256": "7" * 64,
        "smoke_contract_sha256": "8" * 64,
    }
    fields.update(overrides)
    return CacheTargetDiagnosticIdentity(**fields)  # type: ignore[arg-type]


def _prepared() -> CacheTargetDiagnosticJournal:
    return prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID,
        identity=_identity(),
        started_at_unix=1_700_000_000,
    )


def _drained(
    journal: CacheTargetDiagnosticJournal,
) -> CacheTargetDiagnosticJournal:
    return transition_cache_target_diagnostic(
        journal,
        "writers_drained",
        writer_drain_lease_id="99999999-9999-4999-8999-999999999999",
        writer_drain_receipt_sha256="f" * 64,
    )


def _receipt(
    role: str = "map_application",
    stage: str = "source_archive",
    *,
    status: str = "succeeded",
    failure_class: str | None = None,
) -> DiagnosticStageReceipt:
    return DiagnosticStageReceipt(
        role=role,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        failure_class=failure_class,  # type: ignore[arg-type]
        elapsed_ms=1_500,
        archive_sha256="a" * 64 if status == "succeeded" else None,
        schema_inventory_sha256="b" * 64 if status == "succeeded" else None,
        data_inventory_sha256="c" * 64 if status == "succeeded" else None,
        scratch_identity_sha256=None,
    )


def _completed() -> CacheTargetDiagnosticJournal:
    journal = _prepared()
    journal = transition_cache_target_diagnostic(journal, "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")
    journal = transition_cache_target_diagnostic(
        journal, "writers_fenced", writer_fence_sha256="d" * 64
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "map_application_checked",
        map_application_receipts=(_receipt("map_application"),),
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "map_dagster_checked",
        map_dagster_receipts=(_receipt("map_dagster"),),
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "pinvi_checked",
        pinvi_receipts=(_receipt("pinvi"),),
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "runtime_smoke_checked",
        runtime_smoke_sha256="e" * 64,
    )
    return transition_cache_target_diagnostic(
        journal,
        "completed",
        completed_at_unix=1_700_000_500,
        writer_drain_restore_receipt_sha256="e" * 64,
    )


def test_diagnostic_journal_is_owner_only_and_exactly_round_trips(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    path.parent.mkdir(mode=0o700)
    journal = _completed()

    write_cache_target_diagnostic(path, journal)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_cache_target_diagnostic(path) == journal


def test_diagnostic_rejects_legacy_v1_state_before_any_mutation(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    path.parent.mkdir(mode=0o700)
    document = asdict(_prepared())
    document["version"] = 1
    for field_name in (
        "writer_drain_lease_id",
        "writer_drain_receipt_sha256",
        "writer_drain_restore_receipt_sha256",
    ):
        del document[field_name]
    path.write_text(json.dumps(document))
    path.chmod(0o600)

    with pytest.raises(DeploymentContractError, match="v1 is unsupported"):
        read_cache_target_diagnostic(path)


def test_diagnostic_rejects_phase_skip() -> None:
    with pytest.raises(DeploymentContractError, match="phase transition"):
        transition_cache_target_diagnostic(_prepared(), "map_application_checked")


def test_diagnostic_requires_durable_stop_boundary_before_writer_fence() -> None:
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    with pytest.raises(DeploymentContractError, match="phase transition"):
        transition_cache_target_diagnostic(
            journal,
            "writers_fenced",
            writer_fence_sha256="d" * 64,
        )
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    with pytest.raises(DeploymentContractError, match="phase transition"):
        transition_cache_target_diagnostic(
            journal,
            "writers_fenced",
            writer_fence_sha256="d" * 64,
        )
    assert transition_cache_target_diagnostic(_drained(journal), "writers_stopping").phase == (
        "writers_stopping"
    )


def test_diagnostic_allows_failure_or_abort_from_any_non_terminal_phase() -> None:
    journal = transition_cache_target_diagnostic(
        _prepared(),
        "failed",
        failure_stage="source_archive",
        failure_class="timeout",
    )
    assert journal.phase == "failed"

    aborted = transition_cache_target_diagnostic(_prepared(), "aborted")
    assert aborted.phase == "aborted"


def test_diagnostic_rejects_transition_out_of_terminal_phase() -> None:
    failed = transition_cache_target_diagnostic(
        _prepared(), "failed", failure_stage="source_archive", failure_class="timeout"
    )
    with pytest.raises(DeploymentContractError, match="phase transition"):
        transition_cache_target_diagnostic(failed, "writers_fencing")


def test_diagnostic_rejects_writers_fenced_without_writer_fence_evidence() -> None:
    journal = replace(
        _prepared(),
        phase="writers_fenced",
        writer_drain_lease_id="99999999-9999-4999-8999-999999999999",
        writer_drain_receipt_sha256="f" * 64,
    )
    with pytest.raises(DeploymentContractError, match="writer fence evidence"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


def test_diagnostic_rejects_restore_receipt_without_prior_lease() -> None:
    """적대적 리뷰가 찾은 공백: restore receipt만 있고 lease/receipt는 없는
    논리적으로 불가능한 조합이 phase 문턱 검사만으로는 걸러지지 않았다."""
    journal = replace(
        _prepared(),
        writer_drain_restore_receipt_sha256="9" * 64,
    )
    with pytest.raises(DeploymentContractError, match="precedes its lease"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


def test_diagnostic_rejects_map_application_checked_without_its_evidence() -> None:
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")
    journal = transition_cache_target_diagnostic(
        journal, "writers_fenced", writer_fence_sha256="d" * 64
    )
    journal = replace(journal, phase="map_application_checked")
    with pytest.raises(DeploymentContractError, match="Map application evidence"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


def test_diagnostic_rejects_map_dagster_checked_without_its_evidence() -> None:
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")
    journal = transition_cache_target_diagnostic(
        journal, "writers_fenced", writer_fence_sha256="d" * 64
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "map_application_checked",
        map_application_receipts=(_receipt("map_application"),),
    )
    journal = replace(journal, phase="map_dagster_checked")
    with pytest.raises(DeploymentContractError, match="Map Dagster evidence"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


def test_diagnostic_rejects_pinvi_checked_without_its_evidence() -> None:
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")
    journal = transition_cache_target_diagnostic(
        journal, "writers_fenced", writer_fence_sha256="d" * 64
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "map_application_checked",
        map_application_receipts=(_receipt("map_application"),),
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "map_dagster_checked",
        map_dagster_receipts=(_receipt("map_dagster"),),
    )
    journal = replace(journal, phase="pinvi_checked")
    with pytest.raises(DeploymentContractError, match="PinVi evidence"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


def test_diagnostic_rejects_runtime_smoke_checked_without_its_evidence() -> None:
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")
    journal = transition_cache_target_diagnostic(
        journal, "writers_fenced", writer_fence_sha256="d" * 64
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "map_application_checked",
        map_application_receipts=(_receipt("map_application"),),
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "map_dagster_checked",
        map_dagster_receipts=(_receipt("map_dagster"),),
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "pinvi_checked",
        pinvi_receipts=(_receipt("pinvi"),),
    )
    journal = replace(journal, phase="runtime_smoke_checked")
    with pytest.raises(DeploymentContractError, match="runtime smoke evidence"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


def test_diagnostic_rejects_nonzero_external_event_count() -> None:
    journal = replace(_prepared(), external_event_count=1)  # type: ignore[arg-type]
    with pytest.raises(DeploymentContractError, match="external event"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


def test_diagnostic_failed_phase_requires_failure_class() -> None:
    journal = replace(_prepared(), phase="failed")
    with pytest.raises(DeploymentContractError, match="failure class"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


def test_diagnostic_rejects_failure_evidence_outside_failed_phase() -> None:
    journal = replace(_prepared(), failure_class="timeout")
    with pytest.raises(DeploymentContractError, match="only valid in the failed phase"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


def test_diagnostic_completed_phase_requires_completion_time() -> None:
    journal = _completed()
    journal = replace(journal, completed_at_unix=None)
    with pytest.raises(DeploymentContractError, match="requires a completion time"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


def test_diagnostic_rejects_completion_time_before_start_time() -> None:
    journal = _completed()
    journal = replace(journal, completed_at_unix=journal.started_at_unix - 1)
    with pytest.raises(DeploymentContractError, match="precedes its start time"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), journal)


@pytest.mark.parametrize(
    "invalid_identity_kwargs",
    [
        {"manager_release_sha256": "not-a-digest"},
        {"active_pair_sha256": "A" * 64},  # uppercase hex rejected
        {"raw_compose_sha256": "1" * 63},  # too short
        {"pg_dump_major_version": 0},
        {"pg_restore_major_version": -1},
        {"pg_dump_major_version": True},  # bool is not a legitimate int here
    ],
)
def test_diagnostic_rejects_invalid_identity_fields(
    invalid_identity_kwargs: dict[str, object],
) -> None:
    with pytest.raises(DeploymentContractError):
        prepare_cache_target_diagnostic(
            diagnostic_id=_DIAGNOSTIC_ID,
            identity=_identity(**invalid_identity_kwargs),
            started_at_unix=1_700_000_000,
        )


def test_diagnostic_rejects_succeeded_receipt_with_failure_class() -> None:
    receipt = _receipt(status="succeeded", failure_class="timeout")
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")
    journal = transition_cache_target_diagnostic(
        journal, "writers_fenced", writer_fence_sha256="d" * 64
    )
    with pytest.raises(DeploymentContractError, match="must not carry a failure class"):
        transition_cache_target_diagnostic(
            journal,
            "map_application_checked",
            map_application_receipts=(receipt,),
        )


def test_diagnostic_rejects_failed_receipt_without_failure_class() -> None:
    receipt = _receipt(status="failed", failure_class=None)
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")
    journal = transition_cache_target_diagnostic(
        journal, "writers_fenced", writer_fence_sha256="d" * 64
    )
    with pytest.raises(DeploymentContractError, match="requires a failure class"):
        transition_cache_target_diagnostic(
            journal,
            "map_application_checked",
            map_application_receipts=(receipt,),
        )


@pytest.mark.parametrize("bad_elapsed_ms", [-1, 3_600_001, True])
def test_diagnostic_rejects_out_of_bounds_stage_elapsed_time(
    bad_elapsed_ms: object,
) -> None:
    receipt = replace(_receipt(), elapsed_ms=bad_elapsed_ms)  # type: ignore[arg-type]
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")
    journal = transition_cache_target_diagnostic(
        journal, "writers_fenced", writer_fence_sha256="d" * 64
    )
    with pytest.raises(DeploymentContractError, match="elapsed time is invalid"):
        transition_cache_target_diagnostic(
            journal,
            "map_application_checked",
            map_application_receipts=(receipt,),
        )


def test_diagnostic_rejects_receipt_role_not_matching_its_evidence_group() -> None:
    mismatched = _receipt(role="pinvi")
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")
    journal = transition_cache_target_diagnostic(
        journal, "writers_fenced", writer_fence_sha256="d" * 64
    )
    with pytest.raises(DeploymentContractError, match="map_application receipts contain a pinvi"):
        transition_cache_target_diagnostic(
            journal,
            "map_application_checked",
            map_application_receipts=(mismatched,),
        )


def test_diagnostic_rejects_completed_phase_with_a_failed_receipt() -> None:
    journal = _completed()
    failed_receipt = _receipt(role="pinvi", status="failed", failure_class="timeout")
    tampered = replace(journal, pinvi_receipts=(failed_receipt,))
    with pytest.raises(DeploymentContractError, match="every recorded stage to have succeeded"):
        write_cache_target_diagnostic(Path("/nonexistent/unused.json"), tampered)


def test_diagnostic_write_never_persists_an_invalid_journal(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    path.parent.mkdir(mode=0o700)
    journal = replace(_prepared(), external_event_count=1)  # type: ignore[arg-type]

    with pytest.raises(DeploymentContractError):
        write_cache_target_diagnostic(path, journal)

    assert not path.exists()


def test_diagnostic_round_trips_with_empty_receipt_tuples_at_early_phase(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    path.parent.mkdir(mode=0o700)
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")

    write_cache_target_diagnostic(path, journal)

    assert read_cache_target_diagnostic(path) == journal


def test_diagnostic_transition_carries_forward_prior_evidence_when_unspecified() -> None:
    journal = transition_cache_target_diagnostic(_prepared(), "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = _drained(journal)
    journal = transition_cache_target_diagnostic(journal, "writers_stopping")
    journal = transition_cache_target_diagnostic(
        journal, "writers_fenced", writer_fence_sha256="d" * 64
    )
    journal = transition_cache_target_diagnostic(
        journal,
        "map_application_checked",
        map_application_receipts=(_receipt("map_application"),),
    )

    journal = transition_cache_target_diagnostic(
        journal,
        "map_dagster_checked",
        map_dagster_receipts=(_receipt("map_dagster"),),
    )

    assert journal.writer_fence_sha256 == "d" * 64
    assert len(journal.map_application_receipts) == 1


def test_diagnostic_read_rejects_missing_field(tmp_path: Path) -> None:
    import json

    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    path.parent.mkdir(mode=0o700)
    journal = _completed()
    write_cache_target_diagnostic(path, journal)

    document = json.loads(path.read_bytes())
    del document["external_event_count"]
    path.write_bytes(json.dumps(document).encode())
    path.chmod(0o600)

    with pytest.raises(DeploymentContractError, match="journal is invalid"):
        read_cache_target_diagnostic(path)


def test_diagnostic_receipt_is_fresh_accepts_a_valid_fresh_receipt() -> None:
    journal = _completed()
    identity = journal.identity

    assert diagnostic_receipt_is_fresh(
        journal,
        current_identity=identity,
        now_unix=journal.completed_at_unix + 100,  # type: ignore[operator]
        max_age_seconds=3600,
    )


def test_diagnostic_receipt_is_stale_when_not_completed() -> None:
    journal = _prepared()
    assert not diagnostic_receipt_is_fresh(
        journal,
        current_identity=journal.identity,
        now_unix=journal.started_at_unix + 10,
        max_age_seconds=3600,
    )


def test_diagnostic_receipt_is_stale_when_identity_differs() -> None:
    journal = _completed()
    other_identity = _identity(active_pair_sha256="9" * 64)
    assert not diagnostic_receipt_is_fresh(
        journal,
        current_identity=other_identity,
        now_unix=journal.completed_at_unix,  # type: ignore[arg-type]
        max_age_seconds=3600,
    )


def test_diagnostic_receipt_is_stale_when_expired() -> None:
    journal = _completed()
    assert not diagnostic_receipt_is_fresh(
        journal,
        current_identity=journal.identity,
        now_unix=journal.completed_at_unix + 3601,  # type: ignore[operator]
        max_age_seconds=3600,
    )


def test_attempt_log_budget_allows_first_two_attempts_then_exhausts() -> None:
    log = DiagnosticAttemptLog(version=1)
    journal = replace(
        _completed(), phase="failed", failure_stage="source_archive", failure_class="timeout"
    )
    now = 1_700_000_000

    assert not diagnostic_attempt_budget_exceeded(log, now_unix=now)
    log = record_diagnostic_attempt(
        log, replace(journal, diagnostic_id=_DIAGNOSTIC_ID), now_unix=now
    )

    assert not diagnostic_attempt_budget_exceeded(log, now_unix=now + 10)
    log = record_diagnostic_attempt(
        log,
        replace(journal, diagnostic_id="99999999-9999-4999-8999-999999999999"),
        now_unix=now + 10,
    )

    assert diagnostic_attempt_budget_exceeded(log, now_unix=now + 20)


def test_attempt_log_budget_prunes_attempts_outside_the_window() -> None:
    log = DiagnosticAttemptLog(version=1)
    journal = replace(
        _completed(), phase="failed", failure_stage="source_archive", failure_class="timeout"
    )
    now = 1_700_000_000
    log = record_diagnostic_attempt(
        log, replace(journal, diagnostic_id=_DIAGNOSTIC_ID), now_unix=now
    )
    log = record_diagnostic_attempt(
        log,
        replace(journal, diagnostic_id="99999999-9999-4999-8999-999999999999"),
        now_unix=now,
    )

    assert diagnostic_attempt_budget_exceeded(log, now_unix=now + 100)
    assert not diagnostic_attempt_budget_exceeded(log, now_unix=now + 86_401)


def test_diagnostic_failure_is_reproduced_matches_latest_same_class() -> None:
    log = DiagnosticAttemptLog(version=1)
    journal = replace(
        _completed(),
        diagnostic_id=_DIAGNOSTIC_ID,
        phase="failed",
        failure_stage="source_archive",
        failure_class="timeout",
    )
    now = 1_700_000_000
    log = record_diagnostic_attempt(log, journal, now_unix=now)

    assert diagnostic_failure_is_reproduced(
        log, now_unix=now + 10, failure_stage="source_archive", failure_class="timeout"
    )
    assert not diagnostic_failure_is_reproduced(
        log, now_unix=now + 10, failure_stage="source_archive", failure_class="subprocess_nonzero"
    )
    assert not diagnostic_failure_is_reproduced(
        log, now_unix=now + 10, failure_stage="archive_structure", failure_class="timeout"
    )


def test_diagnostic_failure_is_reproduced_ignores_a_succeeded_latest_attempt() -> None:
    log = DiagnosticAttemptLog(version=1)
    journal = replace(_completed(), diagnostic_id=_DIAGNOSTIC_ID)
    log = record_diagnostic_attempt(log, journal, now_unix=1_700_000_000)

    assert not diagnostic_failure_is_reproduced(
        log,
        now_unix=1_700_000_010,
        failure_stage="source_archive",
        failure_class="timeout",
    )


def test_diagnostic_failure_is_reproduced_ignores_an_aborted_latest_attempt() -> None:
    """`aborted` attempt는 journal 계약상 failure_stage/failure_class를 싣지 않는다.
    최신 attempt가 아니라 최신 **failed** attempt와 비교해야, `aborted` 뒤에도
    원래의 (stage, class) 재현 판정이 계속 유지된다."""
    log = DiagnosticAttemptLog(version=1)
    failed_journal = replace(
        _completed(),
        diagnostic_id=_DIAGNOSTIC_ID,
        phase="failed",
        failure_stage="source_archive",
        failure_class="timeout",
    )
    now = 1_700_000_000
    log = record_diagnostic_attempt(log, failed_journal, now_unix=now)
    aborted_journal = replace(
        _completed(),
        diagnostic_id="99999999-9999-4999-8999-999999999999",
        phase="aborted",
    )
    log = record_diagnostic_attempt(log, aborted_journal, now_unix=now + 10)

    assert diagnostic_failure_is_reproduced(
        log, now_unix=now + 20, failure_stage="source_archive", failure_class="timeout"
    )


def test_record_diagnostic_attempt_rejects_non_terminal_journal() -> None:
    log = DiagnosticAttemptLog(version=1)
    with pytest.raises(DeploymentContractError, match="terminal"):
        record_diagnostic_attempt(log, _prepared(), now_unix=1_700_000_000)


def test_record_diagnostic_attempt_rejects_duplicate_diagnostic_id() -> None:
    log = DiagnosticAttemptLog(version=1)
    journal = replace(_completed(), diagnostic_id=_DIAGNOSTIC_ID)
    log = record_diagnostic_attempt(log, journal, now_unix=1_700_000_000)
    with pytest.raises(DeploymentContractError, match="already recorded"):
        record_diagnostic_attempt(log, journal, now_unix=1_700_000_010)


def test_attempt_log_round_trips_and_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-attempts-v1.json"
    path.parent.mkdir(mode=0o700)
    log = DiagnosticAttemptLog(version=1)
    journal = replace(_completed(), diagnostic_id=_DIAGNOSTIC_ID)
    log = record_diagnostic_attempt(log, journal, now_unix=1_700_000_000)

    write_cache_target_diagnostic_attempt_log(path, log)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_cache_target_diagnostic_attempt_log(path) == log


def test_read_or_create_attempt_log_returns_empty_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-attempts-v1.json"
    assert read_or_create_cache_target_diagnostic_attempt_log(path) == DiagnosticAttemptLog(
        version=1
    )


def test_attempt_log_rejects_duplicate_diagnostic_ids_on_read(tmp_path: Path) -> None:
    import json

    path = tmp_path / "state" / "cache-target-diagnostic-attempts-v1.json"
    path.parent.mkdir(mode=0o700)
    journal = replace(_completed(), diagnostic_id=_DIAGNOSTIC_ID)
    log = record_diagnostic_attempt(
        DiagnosticAttemptLog(version=1), journal, now_unix=1_700_000_000
    )
    write_cache_target_diagnostic_attempt_log(path, log)

    document = json.loads(path.read_bytes())
    document["attempts"].append(document["attempts"][0])
    path.write_bytes(json.dumps(document).encode())
    path.chmod(0o600)

    with pytest.raises(DeploymentContractError, match="duplicate"):
        read_cache_target_diagnostic_attempt_log(path)


def test_diagnostic_read_rejects_tampered_field_set(tmp_path: Path) -> None:
    import json

    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    path.parent.mkdir(mode=0o700)
    journal = _completed()
    write_cache_target_diagnostic(path, journal)

    document = json.loads(path.read_bytes())
    document["unexpected_extra_field"] = "x"
    path.write_bytes(json.dumps(document).encode())
    path.chmod(0o600)

    with pytest.raises(DeploymentContractError, match="journal is invalid"):
        read_cache_target_diagnostic(path)
