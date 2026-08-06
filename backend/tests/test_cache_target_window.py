from __future__ import annotations

import json
import os
import stat
from contextlib import nullcontext
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import c6c_deployment
from kor_travel_docker_manager.services.c6c_deployment import (
    CompatibleImagePair,
    CompatiblePairManifest,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.cache_target_backup import PinBoundaryAuditRow
from kor_travel_docker_manager.services.cache_target_cutover import (
    CacheTargetFrozenEvidence,
    EnableCutoverJournal,
    InitialCutoverReceipt,
    initial_receipt_logical_sha256,
    write_cutover_state,
)
from kor_travel_docker_manager.services.cache_target_enable import (
    read_enable_cutover_journal,
)
from kor_travel_docker_manager.services.cache_target_window import (
    CacheTargetWindowJournal,
    DatabaseBackupReceipt,
    DatabaseRestoreRehearsalReceipt,
    MapFinalEvidence,
    MapHelperCheck,
    MapHelperReceipt,
    PinBoundaryReceipt,
    logical_sha256,
    map_helper_receipt_sha256,
    old_restore_is_authorized,
    parse_map_helper_receipt,
    parse_pin_boundary_receipt,
    pin_boundary_receipt_sha256,
    prepare_cache_target_window,
    read_cache_target_window,
    record_window_failure,
    transition_cache_target_window,
    validate_map_final_evidence_binding,
    write_cache_target_window,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
    _compatible_pair_logical_sha256,
    _read_bound_cache_target_initial_receipt,
)

_TRANSACTION_ID = "11111111-1111-4111-8111-111111111111"
_CUTOVER_ID = "22222222-2222-4222-8222-222222222222"
_MAP_REVISION = "a" * 40
_DATABASE_IDENTITY = "b" * 64


def _prepared() -> CacheTargetWindowJournal:
    return prepare_cache_target_window(
        transaction_id=_TRANSACTION_ID,
        cutover_id=_CUTOVER_ID,
        expected_restore_epoch=3,
        reason="production H35 and generation 7 cutover",
        environment_sha256="1" * 64,
        compose_sha256="2" * 64,
        resolved_compose_sha256="3" * 64,
        old_manifest_sha256="4" * 64,
    )


def _initial_receipt_for_journal(
    journal: CacheTargetWindowJournal,
    *,
    cutover_id: str | None = None,
    expected_restore_epoch: int | None = None,
    count: int = 12,
    merkle_root: str = "9" * 64,
) -> InitialCutoverReceipt:
    candidate_pair_sha256 = journal.candidate_pair_sha256 or "6" * 64
    return InitialCutoverReceipt(
        version=1,
        cutover_id=cutover_id or journal.cutover_id,
        expected_restore_epoch=(
            expected_restore_epoch or journal.expected_restore_epoch
        ),
        reason_sha256="5" * 64,
        request_id="33333333-3333-4333-8333-333333333333",
        count=count,
        merkle_root=merkle_root,
        published=count,
        evidence=CacheTargetFrozenEvidence(
            env_sha256=journal.environment_sha256,
            raw_compose_sha256=journal.compose_sha256,
            resolved_compose_sha256=journal.resolved_compose_sha256,
            active_pair_sha256=candidate_pair_sha256,
            rollback_pair_sha256=candidate_pair_sha256,
            role_binding_sha256="7" * 64,
            expected_openapi_sha256="8" * 64,
            expected_source_revision="a" * 40,
            expected_contract_generation="7",
        ),
    )


def _write_initial_receipt_for_journal(
    state_directory: Path,
    journal: CacheTargetWindowJournal,
) -> InitialCutoverReceipt:
    receipt = _initial_receipt_for_journal(journal)
    write_cutover_state(
        state_directory / "cache-target-initial-cutover-v1.json",
        receipt,
    )
    return receipt


def _initial_committed_journal() -> tuple[
    CacheTargetWindowJournal,
    InitialCutoverReceipt,
]:
    journal = _generation_bootstrapped_journal()
    receipt = _initial_receipt_for_journal(journal)
    return (
        transition_cache_target_window(
            journal,
            "initial_committed",
            initial_receipt_sha256=initial_receipt_logical_sha256(receipt),
            external_event_count=receipt.published,
        ),
        receipt,
    )


def _terminal_pair() -> CompatibleImagePair:
    return CompatibleImagePair(
        map_image_id=f"sha256:{'1' * 64}",
        map_ui_image_id=f"sha256:{'2' * 64}",
        map_dagster_image_id=f"sha256:{'3' * 64}",
        map_dagster_daemon_image_id=f"sha256:{'4' * 64}",
        map_source_revision="a" * 40,
        pinvi_image_id=f"sha256:{'5' * 64}",
        pinvi_source_revision="b" * 40,
        contract_generation="7",
        recorded_at="2026-08-02T00:00:00+00:00",
    )


def _runtime_activated_journal(
    pair: CompatibleImagePair,
) -> tuple[CacheTargetWindowJournal, InitialCutoverReceipt]:
    journal = replace(
        _map_final_verified_journal(),
        candidate_pair_sha256=_compatible_pair_logical_sha256(pair),
    )
    receipt = _initial_receipt_for_journal(journal)
    journal = replace(
        journal,
        initial_receipt_sha256=initial_receipt_logical_sha256(receipt),
    )
    journal = transition_cache_target_window(
        journal,
        "final_boundary_verified",
        pin_final_receipt_sha256="8" * 64,
    )
    journal = transition_cache_target_window(journal, "forward_committed")
    return (
        transition_cache_target_window(
            journal,
            "runtime_activated",
            writer_drain_restore_receipt_sha256="f" * 64,
        ),
        receipt,
    )


def _write_terminal_enable_journal(
    state_directory: Path,
    journal: CacheTargetWindowJournal,
    receipt: InitialCutoverReceipt,
) -> None:
    pair_sha256 = journal.candidate_pair_sha256
    assert pair_sha256 is not None
    write_cutover_state(
        state_directory / "cache-target-enable-v1.json",
        EnableCutoverJournal(
            version=2,
            transaction_id="55555555-5555-4555-8555-555555555555",
            cutover_id=journal.cutover_id,
            window_transaction_id=journal.transaction_id,
            attempt=1,
            supersedes_transaction_id=None,
            phase="committed",
            initial_receipt_sha256=initial_receipt_logical_sha256(receipt),
            old_env_sha256="1" * 64,
            new_env_sha256="2" * 64,
            enabled_resolved_compose_sha256="3" * 64,
            active_pair_sha256=pair_sha256,
            rollback_pair_sha256=pair_sha256,
            verified_evidence_sha256="4" * 64,
        ),
    )


def _backup(seed: str, schema: str) -> DatabaseBackupReceipt:
    database_identity = seed * 64
    archive_sha256 = ("f" if seed != "f" else "e") * 64
    return DatabaseBackupReceipt(
        transaction_id=_TRANSACTION_ID,
        database_identity=database_identity,
        schema_revision=schema,
        logical_backup_id=f"{seed * 8}-{seed * 4}-4{seed * 3}-8{seed * 3}-{seed * 12}",
        byte_size=1024,
        sha256=archive_sha256,
        schema_inventory_sha256="a" * 64,
        data_inventory_sha256="b" * 64,
        writer_fence_sha256="c" * 64,
        writer_mutation_count=0,
        restore_rehearsal=DatabaseRestoreRehearsalReceipt(
            transaction_id=_TRANSACTION_ID,
            database_identity="d" * 64,
            source_database_identity=database_identity,
            archive_sha256=archive_sha256,
            schema_revision=schema,
            schema_inventory_sha256="a" * 64,
            data_inventory_sha256="b" * 64,
            verified=True,
        ),
    )


def _backups_committed() -> CacheTargetWindowJournal:
    fencing = transition_cache_target_window(_prepared(), "writers_fencing")
    draining = transition_cache_target_window(fencing, "writers_draining")
    drained = transition_cache_target_window(
        draining,
        "writers_drained",
        writer_drain_lease_id="44444444-4444-4444-8444-444444444444",
        writer_drain_receipt_sha256="d" * 64,
    )
    stopping = transition_cache_target_window(drained, "writers_stopping")
    fenced = transition_cache_target_window(
        stopping,
        "writers_fenced",
        initial_writer_fence_sha256="c" * 64,
    )
    return transition_cache_target_window(
        fenced,
        "backups_committed",
        rollback_bundle_sha256="5" * 64,
        map_application_backup=_backup("1", "0063_pipeline_root_id"),
        map_dagster_backup=_backup("2", "0063_pipeline_root_id"),
        pinvi_backup=_backup("3", "0007_cache_target_generation"),
    )


def _map_receipt(
    operation: str,
    prior_receipt_digest: str | None,
) -> MapHelperReceipt:
    preflight = operation == "preflight"
    return MapHelperReceipt(
        contract_version="h35-map/v1",
        operation=operation,  # type: ignore[arg-type]
        transaction_id=_TRANSACTION_ID,
        status="accepted",
        source_revision=_MAP_REVISION,
        database_identity=_DATABASE_IDENTITY,
        request_digest="e" * 64,
        prior_receipt_digest=prior_receipt_digest,
        schema_before=("0063_pipeline_root_id" if preflight else "0078_cache_target"),
        schema_after=("0063_pipeline_root_id" if preflight else "0078_cache_target"),
        forward_boundary="not_crossed" if preflight else "schema_0078",
        row_counts={"public_item_count": 3265},
        checks=(MapHelperCheck("identity", 0, 0, True),),
        cache_target_evidence=(
            _map_final_evidence() if operation == "verify" else None
        ),
        runtime_mutation_count=0,
        external_event_count=0,
    )


def _map_final_evidence() -> MapFinalEvidence:
    return MapFinalEvidence(
        contract_version="ktm-cache-target-final-evidence/v1",
        external_system="pinvi",
        stream_state="ready",
        consumer_id="pinvi-cache-target-consumer",
        restore_epoch=3,
        control_version=7,
        stream_control_etag="etag-7",
        high_watermark_cursor="cursor-42",
        snapshot_count=12,
        snapshot_merkle_root="9" * 64,
        reconciliation_backlog_count=0,
        outbox_backlog_count=0,
        claim_backlog_count=0,
        delivery_backlog_count=0,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("consumer_id", "foreign-consumer"),
        ("restore_epoch", 4),
        ("snapshot_count", 11),
        ("snapshot_merkle_root", "8" * 64),
    ],
)
def test_map_final_evidence_rejects_drift_from_initial_baseline(
    field: str,
    value: object,
) -> None:
    evidence = MapFinalEvidence(
        **{**asdict(_map_final_evidence()), field: value}
    )

    with pytest.raises(DeploymentContractError, match="initial baseline"):
        validate_map_final_evidence_binding(
            evidence,
            consumer_id="pinvi-cache-target-consumer",
            restore_epoch=3,
            snapshot_count=12,
            snapshot_merkle_root="9" * 64,
        )


def test_writer_fence_binds_global_container_inventory_before_db_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    ordered_writers = tuple(
        sorted(
            (
                "kor-travel-map-api",
                "kor-travel-map-dagster",
                "kor-travel-map-dagster-daemon",
                "pinvi-api",
                "pinvi-dagster",
            )
        )
    )
    transaction = SimpleNamespace(resolved={"services": {}})
    expected_environments = {name: {"DATABASE_URL": name} for name in ordered_writers}
    environment_builder = Mock(return_value=expected_environments)
    global_evidence = [
        SimpleNamespace(
            contract_version="ktdm-cache-target-global-writer-fence/v1",
            inventory_sha256="a" * 64,
            protected_target_count=3,
            expected_stopped_writer_count=5,
        ),
        SimpleNamespace(
            contract_version="ktdm-cache-target-global-writer-fence/v1",
            inventory_sha256="b" * 64,
            protected_target_count=3,
            expected_stopped_writer_count=5,
        ),
    ]
    global_attestor = Mock()
    events: list[str] = []
    monkeypatch.setattr(
        service,
        "_snapshot_service_states",
        Mock(return_value={name: "exited" for name in ordered_writers}),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.cache_target_writer_environments_from_resolved_compose",
        environment_builder,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.attest_cache_target_global_writer_fence",
        global_attestor,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_database_inflight_count",
        lambda _runtime: events.append("db_probe") or 0,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_dagster_inflight_run_count",
        lambda _runtime: 0,
    )
    counter = SimpleNamespace(
        inserted=0,
        updated=0,
        deleted=0,
        stats_reset_identity="never",
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_database_write_counter",
        lambda _runtime: counter,
    )
    def attest_in_order(**kwargs: object) -> object:
        del kwargs
        events.append("global_fence")
        return global_evidence.pop(0)

    global_attestor.side_effect = attest_in_order
    runtimes = (Mock(), Mock(), Mock())

    first_sha, _ = service._read_cache_target_writer_fence_evidence(
        journal=_prepared(),
        transaction=transaction,
        runtimes=runtimes,
        ordered_writers=ordered_writers,
    )
    second_sha, _ = service._read_cache_target_writer_fence_evidence(
        journal=_prepared(),
        transaction=transaction,
        runtimes=runtimes,
        ordered_writers=ordered_writers,
    )

    assert first_sha != second_sha
    assert events[0:2] == ["global_fence", "db_probe"]
    environment_builder.assert_called_with(transaction.resolved, ordered_writers)
    assert all(
        call.kwargs["expected_stopped_writers"] == expected_environments
        for call in global_attestor.call_args_list
    )


def _map_gc_receipt(prior_receipt_digest: str) -> MapHelperReceipt:
    return MapHelperReceipt(
        contract_version="h35-map/v1",
        operation="gc",
        transaction_id=_TRANSACTION_ID,
        status="accepted",
        source_revision=_MAP_REVISION,
        database_identity=_DATABASE_IDENTITY,
        request_digest="e" * 64,
        prior_receipt_digest=prior_receipt_digest,
        schema_before="0078_cache_target",
        schema_after="0078_cache_target",
        forward_boundary="schema_0078",
        row_counts={
            "batches": 1,
            "deleted_headers": 1,
            "deleted_items": 2,
            "referenced_headers": 3,
            "referenced_items": 9,
            "remaining_headers": 0,
            "remaining_items": 0,
        },
        checks=(
            MapHelperCheck("gc_lock_acquired", True, True, True),
            MapHelperCheck("gc_not_skipped", False, False, True),
            MapHelperCheck("gc_remaining_items", 0, 0, True),
            MapHelperCheck("gc_remaining_headers", 0, 0, True),
            MapHelperCheck("gc_referenced_items_preserved", True, True, True),
            MapHelperCheck("gc_referenced_headers_preserved", True, True, True),
            MapHelperCheck(
                "gc_observation_run_id",
                f"h35:{_TRANSACTION_ID}:cache-target-snapshot-gc:v1",
                f"h35:{_TRANSACTION_ID}:cache-target-snapshot-gc:v1",
                True,
            ),
            MapHelperCheck(
                "gc_observation_referenced_items_fresh",
                True,
                True,
                True,
            ),
            MapHelperCheck(
                "gc_observation_referenced_headers_fresh",
                True,
                True,
                True,
            ),
            MapHelperCheck("gc_observation_timestamp_present", True, True, True),
        ),
        cache_target_evidence=None,
        runtime_mutation_count=0,
        external_event_count=0,
    )


def _generation_bootstrapped_journal() -> CacheTargetWindowJournal:
    journal = _backups_committed()
    journal = transition_cache_target_window(
        journal,
        "candidate_built",
        candidate_pair_sha256="6" * 64,
    )
    journal = transition_cache_target_window(
        journal,
        "pin_preflight_verified",
        pin_preflight_receipt_sha256="a" * 64,
    )
    preflight = _map_receipt("preflight", None)
    journal = transition_cache_target_window(
        journal,
        "map_preflight_verified",
        last_map_receipt=preflight,
        last_map_receipt_sha256=map_helper_receipt_sha256(preflight),
    )
    migration = _map_receipt("migrate", map_helper_receipt_sha256(preflight))
    journal = transition_cache_target_window(
        journal,
        "map_database_forwarded",
        last_map_receipt=migration,
        last_map_receipt_sha256=map_helper_receipt_sha256(migration),
    )
    journal = transition_cache_target_window(
        journal,
        "databases_forwarded",
        pin_migration_receipt_sha256="b" * 64,
    )
    csv_receipt = _map_receipt("csv5", map_helper_receipt_sha256(migration))
    journal = transition_cache_target_window(
        journal,
        "csv_forwarded",
        last_map_receipt=csv_receipt,
        last_map_receipt_sha256=map_helper_receipt_sha256(csv_receipt),
    )
    return transition_cache_target_window(journal, "generation_bootstrapped")


def _map_final_verified_journal() -> CacheTargetWindowJournal:
    journal = _backups_committed()
    journal = transition_cache_target_window(
        journal,
        "candidate_built",
        candidate_pair_sha256="6" * 64,
    )
    journal = transition_cache_target_window(
        journal,
        "pin_preflight_verified",
        pin_preflight_receipt_sha256="a" * 64,
    )
    preflight = _map_receipt("preflight", None)
    journal = transition_cache_target_window(
        journal,
        "map_preflight_verified",
        last_map_receipt=preflight,
        last_map_receipt_sha256=map_helper_receipt_sha256(preflight),
    )
    migration = _map_receipt("migrate", map_helper_receipt_sha256(preflight))
    journal = transition_cache_target_window(
        journal,
        "map_database_forwarded",
        last_map_receipt=migration,
        last_map_receipt_sha256=map_helper_receipt_sha256(migration),
    )
    journal = transition_cache_target_window(
        journal,
        "databases_forwarded",
        pin_migration_receipt_sha256="b" * 64,
    )
    csv_receipt = _map_receipt("csv5", map_helper_receipt_sha256(migration))
    journal = transition_cache_target_window(
        journal,
        "csv_forwarded",
        last_map_receipt=csv_receipt,
        last_map_receipt_sha256=map_helper_receipt_sha256(csv_receipt),
    )
    journal = transition_cache_target_window(journal, "generation_bootstrapped")
    initial_receipt = _initial_receipt_for_journal(journal)
    journal = transition_cache_target_window(
        journal,
        "initial_committed",
        initial_receipt_sha256=initial_receipt_logical_sha256(initial_receipt),
        external_event_count=12,
    )
    journal = transition_cache_target_window(journal, "sync_enabled")
    journal = transition_cache_target_window(
        journal,
        "canary_verified",
        external_event_count=14,
    )
    journal = transition_cache_target_window(journal, "gc_started")
    gc_receipt = _map_gc_receipt(map_helper_receipt_sha256(csv_receipt))
    gc_digest = map_helper_receipt_sha256(gc_receipt)
    journal = transition_cache_target_window(
        journal,
        "gc_verified",
        last_map_receipt=gc_receipt,
        last_map_receipt_sha256=gc_digest,
        gc_receipt_sha256=gc_digest,
    )
    journal = transition_cache_target_window(journal, "final_writers_fencing")
    counters = tuple(
        SimpleNamespace(
            inserted=index,
            updated=index,
            deleted=index,
            stats_reset_identity="never",
        )
        for index in range(3)
    )
    journal = transition_cache_target_window(
        journal,
        "final_writers_fenced",
        final_writer_fence_sha256="f" * 64,
        final_map_write_counters_sha256=(
            ComposeService._cache_target_map_write_counters_sha256(counters)
        ),
    )
    verify = _map_receipt("verify", gc_digest)
    evidence = verify.cache_target_evidence
    assert evidence is not None
    return transition_cache_target_window(
        journal,
        "map_final_verified",
        last_map_receipt=verify,
        last_map_receipt_sha256=map_helper_receipt_sha256(verify),
        map_final_evidence=evidence,
        map_final_evidence_sha256=logical_sha256(asdict(evidence)),
    )


def test_window_journal_is_owner_only_and_exactly_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-window-v1.json"
    path.parent.mkdir(mode=0o700)
    journal = _backups_committed()

    write_cache_target_window(path, journal)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_cache_target_window(path) == journal


def test_window_rejects_legacy_v1_state_before_any_mutation(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-window-v1.json"
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
        read_cache_target_window(path)


def test_record_window_failure_freezes_last_safe_phase_and_class(
    tmp_path: Path,
) -> None:
    journal = _backups_committed()
    journal = transition_cache_target_window(
        journal, "candidate_built", candidate_pair_sha256="6" * 64
    )

    failed = record_window_failure(journal, failure_class="contract_violation")

    assert failed.failure_stage == "candidate_built"
    assert failed.failure_class == "contract_violation"
    assert failed.phase == "candidate_built"

    path = tmp_path / "state" / "cache-target-window-v1.json"
    path.parent.mkdir(mode=0o700)
    write_cache_target_window(path, failed)
    assert read_cache_target_window(path) == failed

    rolled = transition_cache_target_window(failed, "rollback_preparing")
    assert rolled.failure_stage == "candidate_built"
    assert rolled.failure_class == "contract_violation"


def test_window_rejects_restore_receipt_without_prior_lease() -> None:
    """적대적 리뷰가 cache_target_diagnostics.py에서 찾은 것과 같은 공백:
    restore receipt만 있고 lease/receipt는 없는 논리적으로 불가능한 조합이
    phase 문턱 검사만으로는 걸러지지 않았다."""
    journal = replace(_prepared(), writer_drain_restore_receipt_sha256="9" * 64)
    with pytest.raises(DeploymentContractError, match="precedes its lease"):
        write_cache_target_window(Path("/nonexistent/unused.json"), journal)


def test_record_window_failure_rejects_non_forward_phase() -> None:
    journal = transition_cache_target_window(_prepared(), "rollback_preparing")
    with pytest.raises(DeploymentContractError, match="forward phase"):
        record_window_failure(journal, failure_class="unexpected_error")


def test_window_rejects_mismatched_failure_stage_and_class_pairing() -> None:
    journal = replace(_backups_committed(), failure_stage="backups_committed")
    with pytest.raises(DeploymentContractError, match="must be set together"):
        write_cache_target_window(Path("/nonexistent/unused.json"), journal)

    journal = replace(_backups_committed(), failure_class="contract_violation")
    with pytest.raises(DeploymentContractError, match="must be set together"):
        write_cache_target_window(Path("/nonexistent/unused.json"), journal)


def test_window_rejects_invalid_failure_stage_and_class_values() -> None:
    journal = replace(
        _backups_committed(),
        failure_stage="rolled_back",  # not a forward phase
        failure_class="contract_violation",
    )
    with pytest.raises(DeploymentContractError, match="failure stage is invalid"):
        write_cache_target_window(Path("/nonexistent/unused.json"), journal)

    journal = replace(
        _backups_committed(),
        failure_stage="backups_committed",
        failure_class="bogus",  # type: ignore[arg-type]
    )
    with pytest.raises(DeploymentContractError, match="failure class is invalid"):
        write_cache_target_window(Path("/nonexistent/unused.json"), journal)


def test_window_rejects_phase_skip_and_old_restore_after_external_event() -> None:
    with pytest.raises(DeploymentContractError, match="phase transition"):
        transition_cache_target_window(_prepared(), "candidate_built")

    journal = _backups_committed()
    journal = transition_cache_target_window(
        journal,
        "candidate_built",
        candidate_pair_sha256="6" * 64,
    )
    journal = transition_cache_target_window(
        journal,
        "pin_preflight_verified",
        pin_preflight_receipt_sha256="a" * 64,
    )
    map_preflight = _map_receipt("preflight", None)
    journal = transition_cache_target_window(
        journal,
        "map_preflight_verified",
        last_map_receipt=map_preflight,
        last_map_receipt_sha256=map_helper_receipt_sha256(map_preflight),
    )
    map_migration = _map_receipt(
        "migrate",
        map_helper_receipt_sha256(map_preflight),
    )
    journal = transition_cache_target_window(
        journal,
        "map_database_forwarded",
        last_map_receipt=map_migration,
        last_map_receipt_sha256=map_helper_receipt_sha256(map_migration),
    )
    journal = transition_cache_target_window(
        journal,
        "databases_forwarded",
        pin_migration_receipt_sha256="b" * 64,
    )
    csv_receipt = _map_receipt(
        "csv5",
        map_helper_receipt_sha256(map_migration),
    )
    journal = transition_cache_target_window(
        journal,
        "csv_forwarded",
        last_map_receipt=csv_receipt,
        last_map_receipt_sha256=map_helper_receipt_sha256(csv_receipt),
    )
    journal = transition_cache_target_window(journal, "generation_bootstrapped")
    journal = transition_cache_target_window(
        journal,
        "initial_committed",
        initial_receipt_sha256="9" * 64,
        external_event_count=12,
    )

    assert old_restore_is_authorized(journal) is False
    with pytest.raises(DeploymentContractError, match="old restore is forbidden"):
        transition_cache_target_window(journal, "rollback_preparing")


def test_window_allows_ordered_pre_event_coupled_rollback() -> None:
    journal = replace(
        _prepared(),
        writer_drain_lease_id="44444444-4444-4444-8444-444444444444",
        writer_drain_receipt_sha256="d" * 64,
    )
    journal = transition_cache_target_window(journal, "rollback_preparing")
    for phase in (
        "new_runtime_stopped",
        "map_db_restored",
        "map_dagster_db_restored",
        "pinvi_db_restored",
        "manager_state_restored",
        "writers_restored",
        "old_runtime_restored",
        "rolled_back",
    ):
        if phase == "writers_restored":
            journal = transition_cache_target_window(
                journal,
                phase,
                writer_drain_restore_receipt_sha256="e" * 64,
            )
        else:
            journal = transition_cache_target_window(journal, phase)  # type: ignore[arg-type]

    assert journal.phase == "rolled_back"
    assert old_restore_is_authorized(journal) is False


def test_writers_restored_crash_resumes_coupled_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    path = tmp_path / "cache-target-window-v1.json"
    journal = replace(
        _prepared(),
        writer_drain_lease_id="44444444-4444-4444-8444-444444444444",
        writer_drain_receipt_sha256="d" * 64,
    )
    for phase in (
        "rollback_preparing",
        "new_runtime_stopped",
        "map_db_restored",
        "map_dagster_db_restored",
        "pinvi_db_restored",
        "manager_state_restored",
    ):
        journal = transition_cache_target_window(journal, phase)  # type: ignore[arg-type]
    journal = transition_cache_target_window(
        journal,
        "writers_restored",
        writer_drain_restore_receipt_sha256="e" * 64,
    )
    write_cache_target_window(path, journal)
    rolled_back = transition_cache_target_window(journal, "old_runtime_restored")
    rolled_back = transition_cache_target_window(rolled_back, "rolled_back")
    rollback = Mock(return_value=rolled_back)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.database_runtimes_from_frozen_contract",
        Mock(return_value=(Mock(), Mock(), Mock())),
    )
    monkeypatch.setattr(service, "_resume_cache_target_coupled_rollback", rollback)

    result = service._run_cache_target_window_unlocked(
        journal_path=path,
        journal=journal,
        transaction=SimpleNamespace(
            manifest_path=str(tmp_path / "compatible-pair-v4.json"),
            resolved={},
            environment=SimpleNamespace(effective={}),
        ),
        config=Mock(),
        reason="crash resume",
        wait_timeout=1,
        lock_path=str(tmp_path / "lock"),
    )

    assert result["phase"] == "rolled_back"
    assert result["resumed"] is True
    assert rollback.call_args.kwargs["journal"].phase == "writers_restored"


def test_initial_event_boundary_fsync_failure_never_invokes_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    path = tmp_path / "cache-target-window-v1.json"
    journal = _generation_bootstrapped_journal()
    write_cache_target_window(path, journal)
    candidate = SimpleNamespace()
    transaction = SimpleNamespace(
        manifest_path=str(tmp_path / "compatible-pair-v4.json"),
        resolved={},
        environment=SimpleNamespace(
            effective={},
            env_path=str(tmp_path / ".env"),
            env_file_identity=SimpleNamespace(uid=os.geteuid(), gid=os.getegid()),
        ),
    )
    initial = Mock(side_effect=AssertionError("runner must follow durable boundary"))
    rollback = Mock(return_value=journal)
    original_write = write_cache_target_window

    def fail_event_boundary_fsync(
        target: Path,
        updated: CacheTargetWindowJournal,
    ) -> str:
        if (
            updated.phase == "generation_bootstrapped"
            and updated.external_event_count == 1
        ):
            raise OSError("simulated external-event boundary fsync failure")
        return original_write(target, updated)

    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.database_runtimes_from_frozen_contract",
        Mock(return_value=(Mock(), Mock(), Mock())),
    )
    monkeypatch.setattr(
        service,
        "_load_or_build_window_candidate",
        Mock(return_value=candidate),
    )
    monkeypatch.setattr(
        service,
        "_run_cache_target_initial_cutover_unlocked",
        initial,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.write_cache_target_window",
        fail_event_boundary_fsync,
    )
    monkeypatch.setattr(service, "_resume_cache_target_coupled_rollback", rollback)

    with pytest.raises(OSError, match="boundary fsync failure"):
        service._run_cache_target_window_unlocked(
            journal_path=path,
            journal=journal,
            transaction=transaction,
            config=Mock(),
            reason="test",
            wait_timeout=1,
            lock_path=str(tmp_path / "lock"),
        )

    persisted = read_cache_target_window(path)
    assert persisted.external_event_count == 0
    assert old_restore_is_authorized(persisted) is True
    initial.assert_not_called()
    # T-049D: pre-forward-boundary 실패 시 rollback으로 넘어가기 직전 마지막 안전
    # phase/class를 얼린다 — disk에 남은 `persisted`에는 아직 없고(그 상태에서 다시
    # 읽었을 뿐), 실제로 rollback에 전달되는 in-memory journal에만 기록된다.
    rollback_journal = rollback.call_args.kwargs["journal"]
    assert rollback_journal.failure_stage == "generation_bootstrapped"
    assert rollback_journal.failure_class == "unexpected_error"
    assert rollback_journal == replace(
        persisted,
        failure_stage="generation_bootstrapped",
        failure_class="unexpected_error",
    )


def test_initial_response_loss_forbids_restore_and_same_cutover_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    path = tmp_path / "cache-target-window-v1.json"
    journal = _generation_bootstrapped_journal()
    write_cache_target_window(path, journal)
    candidate = SimpleNamespace()
    transaction = SimpleNamespace(
        manifest_path=str(tmp_path / "compatible-pair-v4.json"),
        resolved={},
        environment=SimpleNamespace(effective={}),
    )
    initial = Mock(
        side_effect=[
            DeploymentContractError("simulated initial response loss"),
            {"success": True},
        ]
    )
    rollback = Mock(side_effect=AssertionError("old restore must remain forbidden"))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.database_runtimes_from_frozen_contract",
        Mock(return_value=(Mock(), Mock(), Mock())),
    )
    monkeypatch.setattr(
        service,
        "_load_or_build_window_candidate",
        Mock(return_value=candidate),
    )
    monkeypatch.setattr(
        service,
        "_run_cache_target_initial_cutover_unlocked",
        initial,
    )
    monkeypatch.setattr(service, "_resume_cache_target_coupled_rollback", rollback)
    monkeypatch.setattr(
        service,
        "_enable_cache_target_sync_unlocked",
        Mock(side_effect=DeploymentContractError("stop after initial resume")),
    )

    with pytest.raises(DeploymentContractError, match="response loss"):
        service._run_cache_target_window_unlocked(
            journal_path=path,
            journal=journal,
            transaction=transaction,
            config=Mock(),
            reason="test",
            wait_timeout=1,
            lock_path=str(tmp_path / "lock"),
        )

    event_bounded = read_cache_target_window(path)
    assert event_bounded.phase == "generation_bootstrapped"
    assert event_bounded.external_event_count == 1
    assert old_restore_is_authorized(event_bounded) is False
    _write_initial_receipt_for_journal(tmp_path, event_bounded)

    with pytest.raises(DeploymentContractError, match="stop after initial resume"):
        service._run_cache_target_window_unlocked(
            journal_path=path,
            journal=event_bounded,
            transaction=transaction,
            config=Mock(),
            reason="test",
            wait_timeout=1,
            lock_path=str(tmp_path / "lock"),
        )

    resumed = read_cache_target_window(path)
    assert resumed.transaction_id == event_bounded.transaction_id
    assert resumed.cutover_id == event_bounded.cutover_id
    assert resumed.phase == "initial_committed"
    assert resumed.external_event_count == 12
    assert initial.call_count == 2
    rollback.assert_not_called()


def test_bound_initial_receipt_legitimate_crash_resume_is_idempotent(
    tmp_path: Path,
) -> None:
    journal, receipt = _initial_committed_journal()
    write_cutover_state(
        tmp_path / "cache-target-initial-cutover-v1.json",
        receipt,
    )

    assert _read_bound_cache_target_initial_receipt(tmp_path, journal) == receipt
    assert _read_bound_cache_target_initial_receipt(tmp_path, journal) == receipt


@pytest.mark.parametrize(
    "replacement_kind",
    [
        "foreign_cutover_same_snapshot",
        "different_count",
        "different_merkle",
        "different_restore_epoch",
        "different_pair_provenance",
    ],
)
def test_bound_initial_receipt_rejects_valid_post_commit_replacement(
    tmp_path: Path,
    replacement_kind: str,
) -> None:
    journal, receipt = _initial_committed_journal()
    replacement = receipt
    if replacement_kind == "foreign_cutover_same_snapshot":
        replacement = replace(
            receipt,
            cutover_id="44444444-4444-4444-8444-444444444444",
        )
    elif replacement_kind == "different_count":
        replacement = replace(receipt, count=11, published=11)
    elif replacement_kind == "different_merkle":
        replacement = replace(receipt, merkle_root="8" * 64)
    elif replacement_kind == "different_restore_epoch":
        replacement = replace(receipt, expected_restore_epoch=4)
    elif replacement_kind == "different_pair_provenance":
        replacement = replace(
            receipt,
            evidence=replace(
                receipt.evidence,
                active_pair_sha256="f" * 64,
            ),
        )
    else:  # pragma: no cover - parameter list가 정본이다.
        raise AssertionError(replacement_kind)
    write_cutover_state(
        tmp_path / "cache-target-initial-cutover-v1.json",
        replacement,
    )

    with pytest.raises(DeploymentContractError, match="committed window"):
        _read_bound_cache_target_initial_receipt(tmp_path, journal)


def test_bound_initial_receipt_rejects_missing_or_extra_fields(
    tmp_path: Path,
) -> None:
    journal, receipt = _initial_committed_journal()
    with pytest.raises(DeploymentContractError, match="state is unavailable"):
        _read_bound_cache_target_initial_receipt(tmp_path, journal)

    path = tmp_path / "cache-target-initial-cutover-v1.json"
    write_cutover_state(path, receipt)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["unexpected"] = "field"
    path.write_text(
        json.dumps(document, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(DeploymentContractError, match="receipt is invalid"):
        _read_bound_cache_target_initial_receipt(tmp_path, journal)


@pytest.mark.parametrize(
    "phase",
    [
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
    ],
)
def test_every_post_initial_resume_rejects_replaced_receipt_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    service = ComposeService()
    committed, receipt = _initial_committed_journal()
    journal = replace(committed, phase=phase)
    write_cutover_state(
        tmp_path / "cache-target-initial-cutover-v1.json",
        replace(
            receipt,
            cutover_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    transaction = SimpleNamespace(
        manifest_path=str(tmp_path / "compatible-pair-v4.json"),
        resolved={},
        environment=SimpleNamespace(effective={}),
    )
    mutation = Mock(side_effect=AssertionError("mutation must not run"))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.database_runtimes_from_frozen_contract",
        Mock(return_value=(Mock(), Mock(), Mock())),
    )
    monkeypatch.setattr(service, "_load_or_build_window_candidate", mutation)
    monkeypatch.setattr(service, "_enable_cache_target_sync_unlocked", mutation)

    with pytest.raises(DeploymentContractError, match="committed window"):
        service._run_cache_target_window_unlocked(
            journal_path=tmp_path / "cache-target-window-v1.json",
            journal=journal,
            transaction=transaction,
            config=Mock(),
            reason="test",
            wait_timeout=1,
            lock_path=str(tmp_path / "lock"),
        )

    mutation.assert_not_called()


def _install_public_terminal_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    ComposeService,
    CacheTargetWindowJournal,
    InitialCutoverReceipt,
    Path,
    Mock,
    Mock,
]:
    service = ComposeService()
    pair = _terminal_pair()
    journal, receipt = _runtime_activated_journal(pair)
    journal_path = tmp_path / "cache-target-window-v1.json"
    manifest_path = tmp_path / "compatible-pair-v4.json"
    write_cache_target_window(journal_path, journal)
    write_cutover_state(
        tmp_path / "cache-target-initial-cutover-v1.json",
        receipt,
    )
    _write_terminal_enable_journal(tmp_path, journal, receipt)
    transaction = SimpleNamespace(
        manifest_path=str(manifest_path),
        resolved={},
        environment=SimpleNamespace(
            effective={},
            env_path=str(tmp_path / ".env"),
            env_file_identity=SimpleNamespace(uid=os.geteuid(), gid=os.getegid()),
        ),
    )
    config = SimpleNamespace(
        production=True,
        cache_target=SimpleNamespace(sync_enabled="true"),
    )
    manifest = CompatiblePairManifest(version=4, active=pair, rollback=pair)
    mutation = Mock(side_effect=AssertionError("terminal mutation must not run"))
    attestor = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock_from_environment",
        lambda: nullcontext(SimpleNamespace(lock_path=str(tmp_path / "lock"))),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service._assert_transaction_matches_c6c_lock",
        Mock(),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, None)),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        Mock(return_value=config),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service._require_cache_target_release",
        Mock(),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.assert_pinned_deployment_input_allows_pair_mutation",
        Mock(),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.cache_target_window_journal_path",
        Mock(return_value=journal_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
        Mock(return_value=manifest),
    )
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", Mock())
    monkeypatch.setattr(service, "_inspect_current_pair", Mock(return_value=pair))
    monkeypatch.setattr(service, "_pair_matches", Mock(return_value=True))
    monkeypatch.setattr(service, "_attest_cache_target_pair", attestor)
    monkeypatch.setattr(service, "_run_cache_target_window_unlocked", mutation)
    monkeypatch.setattr(service, "_run_frozen_recovery", mutation)
    return service, journal, receipt, journal_path, mutation, attestor


def test_public_runtime_activated_retry_revalidates_terminal_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, journal, _receipt, _path, mutation, attestor = (
        _install_public_terminal_context(tmp_path, monkeypatch)
    )

    result = service.run_cache_target_cutover(
        cutover_id=journal.cutover_id,
        expected_restore_epoch=journal.expected_restore_epoch,
        reason="production H35 and generation 7 cutover",
    )

    assert result["success"] is True
    assert result["resumed"] is True
    mutation.assert_not_called()
    attestor.assert_called_once()


@pytest.mark.parametrize(
    "replacement_kind",
    [
        "missing",
        "digest_replacement",
        "foreign_cutover",
        "foreign_epoch",
        "foreign_compose",
        "foreign_pair",
    ],
)
def test_public_runtime_activated_retry_rejects_foreign_initial_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    service, journal, receipt, _path, mutation, attestor = (
        _install_public_terminal_context(tmp_path, monkeypatch)
    )
    receipt_path = tmp_path / "cache-target-initial-cutover-v1.json"
    if replacement_kind == "missing":
        receipt_path.unlink()
    else:
        replacement = receipt
        if replacement_kind == "digest_replacement":
            replacement = replace(
                receipt,
                request_id="66666666-6666-4666-8666-666666666666",
            )
        elif replacement_kind == "foreign_cutover":
            replacement = replace(
                receipt,
                cutover_id="44444444-4444-4444-8444-444444444444",
            )
        elif replacement_kind == "foreign_epoch":
            replacement = replace(receipt, expected_restore_epoch=4)
        elif replacement_kind == "foreign_compose":
            replacement = replace(
                receipt,
                evidence=replace(
                    receipt.evidence,
                    raw_compose_sha256="f" * 64,
                ),
            )
        elif replacement_kind == "foreign_pair":
            replacement = replace(
                receipt,
                evidence=replace(
                    receipt.evidence,
                    rollback_pair_sha256="f" * 64,
                ),
            )
        write_cutover_state(receipt_path, replacement)

    with pytest.raises(DeploymentContractError):
        service.run_cache_target_cutover(
            cutover_id=journal.cutover_id,
            expected_restore_epoch=journal.expected_restore_epoch,
            reason="production H35 and generation 7 cutover",
        )

    mutation.assert_not_called()
    attestor.assert_not_called()


@pytest.mark.parametrize(
    "foreign_boundary",
    ["enable_evidence", "manifest", "current_pair"],
)
def test_public_runtime_activated_retry_rejects_foreign_terminal_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    foreign_boundary: str,
) -> None:
    service, journal, _receipt, _path, mutation, attestor = (
        _install_public_terminal_context(tmp_path, monkeypatch)
    )
    if foreign_boundary == "enable_evidence":
        enable_path = tmp_path / "cache-target-enable-v1.json"
        enable_journal = read_enable_cutover_journal(enable_path)
        write_cutover_state(
            enable_path,
            replace(enable_journal, active_pair_sha256="f" * 64),
        )
    elif foreign_boundary == "manifest":
        active = _terminal_pair()
        foreign = replace(active, pinvi_source_revision="c" * 40)
        monkeypatch.setattr(
            "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
            Mock(
                return_value=CompatiblePairManifest(
                    version=4,
                    active=active,
                    rollback=foreign,
                )
            ),
        )
    elif foreign_boundary == "current_pair":
        monkeypatch.setattr(service, "_pair_matches", Mock(return_value=False))

    with pytest.raises(DeploymentContractError):
        service.run_cache_target_cutover(
            cutover_id=journal.cutover_id,
            expected_restore_epoch=journal.expected_restore_epoch,
            reason="production H35 and generation 7 cutover",
        )

    mutation.assert_not_called()
    attestor.assert_not_called()


def test_prebackup_writer_fence_failure_restores_drain_without_db_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    path = tmp_path / "cache-target-window-v1.json"
    write_cache_target_window(path, _prepared())
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.database_runtimes_from_frozen_contract",
        Mock(return_value=(Mock(), Mock(), Mock())),
    )
    monkeypatch.setattr(
        service,
        "_establish_cache_target_writer_fence",
        Mock(side_effect=RuntimeError("partial stop")),
    )
    monkeypatch.setattr(
        service,
        "_begin_cache_target_writer_drain",
        Mock(return_value=SimpleNamespace(lease_id="4" * 8 + "-4444-4444-8444-" + "4" * 12)),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.writer_drain_receipt_sha256",
        Mock(return_value="d" * 64),
    )
    restore = Mock()
    activate = Mock()
    monkeypatch.setattr(service, "_restore_cache_target_writer_drain", restore)
    monkeypatch.setattr(service, "_activate_cache_target_writers", activate)
    attest = Mock()
    monkeypatch.setattr(service, "_attest_cache_target_prebootstrap_pair", attest)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
        Mock(return_value=SimpleNamespace(active=SimpleNamespace())),
    )

    with pytest.raises(RuntimeError, match="partial stop"):
        service._run_cache_target_window_unlocked(
            journal_path=path,
            journal=_prepared(),
            transaction=SimpleNamespace(
                manifest_path=str(tmp_path / "manifest.json"),
                resolved={},
                environment=SimpleNamespace(effective={}),
            ),
            config=Mock(),
            reason="test",
            wait_timeout=1,
            lock_path=tmp_path / "lock",
        )

    assert not path.exists()
    restore.assert_called_once()
    activate.assert_called_once()
    attest.assert_called_once()


def test_coupled_rollback_restores_map_lease_before_daemon_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """backup에 든 drained Map state는 daemon을 열기 전에 Map이 복구한다."""

    service = ComposeService()
    path = tmp_path / "cache-target-window-v1.json"
    journal = replace(
        _prepared(),
        writer_drain_lease_id="44444444-4444-4444-8444-444444444444",
        writer_drain_receipt_sha256="d" * 64,
    )
    journal = transition_cache_target_window(journal, "rollback_preparing")
    write_cache_target_window(path, journal)
    transaction = SimpleNamespace(
        manifest_path=str(tmp_path / "compatible-pair-v4.json"),
        resolved={},
        environment=SimpleNamespace(effective={}, env_path=str(tmp_path / ".env")),
    )
    order: list[str] = []

    def record_stop(*_args: object, **_kwargs: object) -> dict[str, bool]:
        order.append("new_runtime_stop")
        return {"success": True}

    def record_restore(**_kwargs: object) -> SimpleNamespace:
        order.append("map_restore")
        return SimpleNamespace()

    def record_activation(*_args: object, **_kwargs: object) -> None:
        order.append("runtime_activation")

    def record_auxiliary(*_args: object, **_kwargs: object) -> None:
        order.append("pinvi_writer_activation")

    def record_attestation(*_args: object, **_kwargs: object) -> None:
        order.append("pair_attestation")

    monkeypatch.setattr(service, "_run_frozen_recovery", record_stop)
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, None)),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        Mock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
        Mock(return_value=SimpleNamespace(active=SimpleNamespace())),
    )
    monkeypatch.setattr(service, "_restore_cache_target_writer_drain", record_restore)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.writer_drain_receipt_sha256",
        Mock(return_value="e" * 64),
    )
    monkeypatch.setattr(service, "_activate_pair_sequentially", record_activation)
    monkeypatch.setattr(service, "_restart_cache_target_auxiliary_writer", record_auxiliary)
    monkeypatch.setattr(
        service,
        "_attest_cache_target_prebootstrap_pair",
        record_attestation,
    )

    restored = service._resume_cache_target_coupled_rollback(
        journal_path=path,
        journal=journal,
        transaction=transaction,
        config=SimpleNamespace(),
        runtimes=(Mock(), Mock(), Mock()),
        wait_timeout=1,
    )

    assert restored.phase == "rolled_back"
    assert restored.writer_drain_restore_receipt_sha256 == "e" * 64
    assert order.index("map_restore") < order.index("runtime_activation")
    assert order.index("runtime_activation") < order.index("pair_attestation")


def test_writers_fenced_journal_write_crash_resumes_from_fencing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    path = tmp_path / "cache-target-window-v1.json"
    write_cache_target_window(path, _prepared())
    original_write = write_cache_target_window

    def crash_on_fenced(path: Path, journal: CacheTargetWindowJournal) -> str:
        if journal.phase == "writers_fenced":
            raise OSError("simulated fsync crash")
        return original_write(path, journal)

    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.database_runtimes_from_frozen_contract",
        Mock(return_value=(Mock(), Mock(), Mock())),
    )
    monkeypatch.setattr(
        service,
        "_establish_cache_target_writer_fence",
        Mock(return_value=("c" * 64, (Mock(), Mock(), Mock()))),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.write_cache_target_window",
        crash_on_fenced,
    )
    monkeypatch.setattr(
        service,
        "_begin_cache_target_writer_drain",
        Mock(return_value=SimpleNamespace(lease_id="4" * 8 + "-4444-4444-8444-" + "4" * 12)),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.writer_drain_receipt_sha256",
        Mock(return_value="d" * 64),
    )
    monkeypatch.setattr(service, "_restore_cache_target_writer_drain", Mock())
    monkeypatch.setattr(service, "_activate_cache_target_writers", Mock())
    monkeypatch.setattr(service, "_attest_cache_target_prebootstrap_pair", Mock())
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
        Mock(return_value=SimpleNamespace(active=SimpleNamespace())),
    )

    with pytest.raises(OSError, match="fsync crash"):
        service._run_cache_target_window_unlocked(
            journal_path=path,
            journal=_prepared(),
            transaction=SimpleNamespace(
                manifest_path=str(tmp_path / "manifest.json"),
                resolved={},
                environment=SimpleNamespace(effective={}),
            ),
            config=Mock(),
            reason="test",
            wait_timeout=1,
            lock_path=tmp_path / "lock",
        )

    assert not path.exists()


def test_window_uses_private_locked_executors_and_requires_final_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    path = tmp_path / "cache-target-window-v1.json"
    journal = _backups_committed()
    journal = transition_cache_target_window(
        journal,
        "candidate_built",
        candidate_pair_sha256="6" * 64,
    )
    journal = transition_cache_target_window(
        journal,
        "pin_preflight_verified",
        pin_preflight_receipt_sha256="a" * 64,
    )
    map_preflight = _map_receipt("preflight", None)
    journal = transition_cache_target_window(
        journal,
        "map_preflight_verified",
        last_map_receipt=map_preflight,
        last_map_receipt_sha256=map_helper_receipt_sha256(map_preflight),
    )
    map_migration = _map_receipt(
        "migrate",
        map_helper_receipt_sha256(map_preflight),
    )
    journal = transition_cache_target_window(
        journal,
        "map_database_forwarded",
        last_map_receipt=map_migration,
        last_map_receipt_sha256=map_helper_receipt_sha256(map_migration),
    )
    journal = transition_cache_target_window(
        journal,
        "databases_forwarded",
        pin_migration_receipt_sha256="b" * 64,
    )
    csv_receipt = _map_receipt(
        "csv5",
        map_helper_receipt_sha256(map_migration),
    )
    journal = transition_cache_target_window(
        journal,
        "csv_forwarded",
        last_map_receipt=csv_receipt,
        last_map_receipt_sha256=map_helper_receipt_sha256(csv_receipt),
    )
    journal = transition_cache_target_window(journal, "generation_bootstrapped")
    write_cache_target_window(path, journal)
    transaction = SimpleNamespace(
        manifest_path=str(tmp_path / "compatible-pair-v4.json"),
        resolved={},
        environment=SimpleNamespace(effective={}),
    )
    candidate = SimpleNamespace(
        map_source_revision="a" * 40,
        pinvi_source_revision="c" * 40,
        map_image_id=f"sha256:{'1' * 64}",
        pinvi_image_id=f"sha256:{'2' * 64}",
    )
    initial_receipt = _write_initial_receipt_for_journal(tmp_path, journal)
    enable_journal = SimpleNamespace(
        phase="committed",
        transaction_id="33333333-3333-4333-8333-333333333333",
    )
    final_document = _pin_boundary_receipt("finalize")
    final_document.update(
        prior_receipt_sha256="a" * 64,
        canary_run_id=enable_journal.transaction_id,
    )
    final_document["audit_request_sha256"] = logical_sha256(
        {
            key: final_document[key]
            for key in _pin_boundary_request("finalize")
        }
    )
    final_receipt = PinBoundaryReceipt(**final_document)
    private_initial = Mock(return_value={"success": True})
    private_enable = Mock(return_value={"success": True, "phase": "committed"})
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.database_runtimes_from_frozen_contract",
        Mock(return_value=(Mock(), Mock(), Mock())),
    )
    monkeypatch.setattr(service, "_load_or_build_window_candidate", Mock(return_value=candidate))
    monkeypatch.setattr(service, "run_cache_target_initial_cutover", Mock(side_effect=AssertionError))
    monkeypatch.setattr(service, "enable_cache_target_sync", Mock(side_effect=AssertionError))
    monkeypatch.setattr(service, "_run_cache_target_initial_cutover_unlocked", private_initial)
    monkeypatch.setattr(service, "_enable_cache_target_sync_unlocked", private_enable)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_enable_cutover_journal",
        Mock(return_value=enable_journal),
    )
    map_gc = _map_gc_receipt(map_helper_receipt_sha256(csv_receipt))
    map_verify = _map_receipt("verify", map_helper_receipt_sha256(map_gc))
    monkeypatch.setattr(
        service,
        "_run_map_h35_helper",
        Mock(side_effect=[map_gc, map_verify]),
    )
    counters = tuple(
        SimpleNamespace(
            inserted=index,
            updated=index,
            deleted=index,
            stats_reset_identity="never",
        )
        for index in range(3)
    )
    monkeypatch.setattr(
        service,
        "_establish_cache_target_writer_fence",
        Mock(return_value=("f" * 64, counters)),
    )
    monkeypatch.setattr(
        service,
        "_read_cache_target_writer_fence_evidence",
        Mock(return_value=("f" * 64, counters)),
    )
    monkeypatch.setattr(service, "_cache_target_writer_names", Mock(return_value=tuple()))
    monkeypatch.setattr(service, "_run_pin_boundary_helper", Mock(return_value=final_receipt))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_pin_boundary_audit",
        Mock(
            return_value=SimpleNamespace(
                audit_id=final_receipt.audit_id,
                audit_request_sha256=final_receipt.audit_request_sha256,
                evidence_sha256=final_receipt.evidence_sha256,
                map_final_evidence_sha256=final_receipt.map_final_evidence_sha256,
                initial_writer_fence_sha256=(
                    final_receipt.initial_writer_fence_sha256
                ),
                final_writer_fence_sha256=final_receipt.final_writer_fence_sha256,
                prior_receipt_sha256=final_receipt.prior_receipt_sha256,
                canary_run_id=final_receipt.canary_run_id,
            )
        ),
    )
    monkeypatch.setattr(service, "_capture_transaction_unlocked", Mock(return_value=(transaction, None)))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        Mock(
            return_value=SimpleNamespace(
                cache_target=SimpleNamespace(
                    consumer_id="pinvi-cache-target-consumer"
                )
            )
        ),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
        Mock(return_value=Mock()),
    )
    monkeypatch.setattr(service, "_attest_cache_target_pair", Mock())
    monkeypatch.setattr(service, "_cache_target_writer_names", Mock(return_value=tuple()))
    monkeypatch.setattr(
        service,
        "_restore_cache_target_writer_drain",
        Mock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.writer_drain_receipt_sha256",
        Mock(return_value="f" * 64),
    )
    monkeypatch.setattr(service, "_activate_cache_target_writers", Mock())
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.reconcile_pair_references",
        Mock(),
    )

    result = service._run_cache_target_window_unlocked(
        journal_path=path,
        journal=journal,
        transaction=transaction,
        config=Mock(),
        reason="test",
        wait_timeout=1,
        lock_path=tmp_path / "lock",
    )

    assert result["success"] is True
    assert read_cache_target_window(path).phase == "runtime_activated"
    private_initial.assert_called_once()
    private_enable.assert_called_once()
    assert private_enable.call_args.kwargs["receipt"] == initial_receipt


def test_finalize_audit_commit_response_loss_replays_exact_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    path = tmp_path / "cache-target-window-v1.json"
    journal = _map_final_verified_journal()
    write_cache_target_window(path, journal)
    _write_initial_receipt_for_journal(tmp_path, journal)
    transaction = SimpleNamespace(
        manifest_path=str(tmp_path / "compatible-pair-v4.json"),
        resolved={},
        environment=SimpleNamespace(effective={}),
    )
    candidate = SimpleNamespace(
        map_source_revision="a" * 40,
        pinvi_source_revision="c" * 40,
        map_image_id=f"sha256:{'1' * 64}",
        pinvi_image_id=f"sha256:{'2' * 64}",
    )
    request = _pin_boundary_request(
        "finalize",
        prior_receipt_sha256="a" * 64,
        canary_run_id="33333333-3333-4333-8333-333333333333",
    )
    final_document = _pin_boundary_receipt("finalize")
    final_document.update(request)
    final_document["audit_request_sha256"] = logical_sha256(request)
    final_receipt = parse_pin_boundary_receipt(
        stdout=json.dumps(final_document) + "\n",
        stderr="",
        request=request,
        expected_initial_count=12,
    )
    audit_row = PinBoundaryAuditRow(
        audit_id=final_receipt.audit_id or "",
        audit_request_sha256=final_receipt.audit_request_sha256 or "",
        evidence_sha256=final_receipt.evidence_sha256,
        map_final_evidence_sha256=final_receipt.map_final_evidence_sha256 or "",
        initial_writer_fence_sha256=final_receipt.initial_writer_fence_sha256,
        final_writer_fence_sha256=final_receipt.final_writer_fence_sha256 or "",
        prior_receipt_sha256=final_receipt.prior_receipt_sha256 or "",
        canary_run_id=final_receipt.canary_run_id or "",
    )
    counters = tuple(
        SimpleNamespace(
            inserted=index,
            updated=index,
            deleted=index,
            stats_reset_identity="never",
        )
        for index in range(3)
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.database_runtimes_from_frozen_contract",
        Mock(return_value=(Mock(), Mock(), Mock())),
    )
    monkeypatch.setattr(service, "_load_or_build_window_candidate", Mock(return_value=candidate))
    monkeypatch.setattr(service, "_capture_transaction_unlocked", Mock(return_value=(transaction, None)))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        Mock(return_value=Mock()),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_enable_cutover_journal",
        Mock(
            return_value=SimpleNamespace(
                transaction_id="33333333-3333-4333-8333-333333333333"
            )
        ),
    )
    pin_helper = Mock(return_value=final_receipt)
    monkeypatch.setattr(service, "_run_pin_boundary_helper", pin_helper)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_pin_boundary_audit",
        Mock(return_value=audit_row),
    )
    monkeypatch.setattr(
        service,
        "_establish_cache_target_writer_fence",
        Mock(return_value=("f" * 64, counters)),
    )
    monkeypatch.setattr(
        service,
        "_read_cache_target_writer_fence_evidence",
        Mock(return_value=("f" * 64, counters)),
    )
    monkeypatch.setattr(
        service,
        "_cache_target_writer_names",
        Mock(return_value=tuple()),
    )
    monkeypatch.setattr(
        service,
        "_restore_cache_target_writer_drain",
        Mock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.writer_drain_receipt_sha256",
        Mock(return_value="f" * 64),
    )
    monkeypatch.setattr(service, "_activate_cache_target_writers", Mock())
    monkeypatch.setattr(service, "_attest_cache_target_pair", Mock())
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
        Mock(return_value=Mock()),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.reconcile_pair_references",
        Mock(),
    )
    original_write = write_cache_target_window
    crash_once = True

    def crash_after_audit(
        target: Path,
        updated: CacheTargetWindowJournal,
    ) -> str:
        nonlocal crash_once
        if crash_once and updated.phase == "final_boundary_verified":
            crash_once = False
            raise OSError("simulated journal fsync loss")
        return original_write(target, updated)

    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.write_cache_target_window",
        crash_after_audit,
    )

    with pytest.raises(OSError, match="fsync loss"):
        service._run_cache_target_window_unlocked(
            journal_path=path,
            journal=journal,
            transaction=transaction,
            config=Mock(),
            reason="test",
            wait_timeout=1,
            lock_path=str(tmp_path / "lock"),
        )
    assert read_cache_target_window(path).phase == "map_final_verified"

    result = service._run_cache_target_window_unlocked(
        journal_path=path,
        journal=read_cache_target_window(path),
        transaction=transaction,
        config=Mock(),
        reason="test",
        wait_timeout=1,
        lock_path=str(tmp_path / "lock"),
    )

    assert result["success"] is True
    assert read_cache_target_window(path).phase == "runtime_activated"
    assert pin_helper.call_count == 2


def test_forward_commit_restart_failure_resumes_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    path = tmp_path / "cache-target-window-v1.json"
    journal = transition_cache_target_window(
        _map_final_verified_journal(),
        "final_boundary_verified",
        pin_final_receipt_sha256="8" * 64,
    )
    journal = transition_cache_target_window(journal, "forward_committed")
    write_cache_target_window(path, journal)
    _write_initial_receipt_for_journal(tmp_path, journal)
    transaction = SimpleNamespace(
        manifest_path=str(tmp_path / "compatible-pair-v4.json"),
        resolved={},
        environment=SimpleNamespace(effective={}),
    )
    candidate = SimpleNamespace()
    activation = Mock(
        side_effect=[
            DeploymentContractError("simulated activation failure"),
            None,
        ]
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.database_runtimes_from_frozen_contract",
        Mock(return_value=(Mock(), Mock(), Mock())),
    )
    monkeypatch.setattr(service, "_load_or_build_window_candidate", Mock(return_value=candidate))
    monkeypatch.setattr(service, "_capture_transaction_unlocked", Mock(return_value=(transaction, None)))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        Mock(return_value=Mock()),
    )
    monkeypatch.setattr(
        service,
        "_restore_cache_target_writer_drain",
        Mock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.writer_drain_receipt_sha256",
        Mock(return_value="f" * 64),
    )
    monkeypatch.setattr(service, "_activate_cache_target_writers", activation)
    monkeypatch.setattr(service, "_attest_cache_target_pair", Mock())
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
        Mock(return_value=Mock()),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.reconcile_pair_references",
        Mock(),
    )

    with pytest.raises(DeploymentContractError, match="activation failure"):
        service._run_cache_target_window_unlocked(
            journal_path=path,
            journal=journal,
            transaction=transaction,
            config=Mock(),
            reason="test",
            wait_timeout=1,
            lock_path=str(tmp_path / "lock"),
        )
    assert read_cache_target_window(path).phase == "forward_committed"

    result = service._run_cache_target_window_unlocked(
        journal_path=path,
        journal=read_cache_target_window(path),
        transaction=transaction,
        config=Mock(),
        reason="test",
        wait_timeout=1,
        lock_path=str(tmp_path / "lock"),
    )

    assert result["success"] is True
    assert read_cache_target_window(path).phase == "runtime_activated"
    assert activation.call_count == 2


def test_unfinished_window_blocks_foreign_manager_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setattr(
        c6c_deployment,
        "_C6C_PRODUCTION_STATE_ROOT",
        state_root,
    )
    environment = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
        "PINVI_ENVIRONMENT": "production",
        "COMPOSE_PROJECT_NAME": "pinvi-prod",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
            "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
            "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
            "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        }
    path = c6c_deployment.cache_target_window_journal_path(environment)
    path.parent.mkdir(mode=0o700, parents=True)
    write_cache_target_window(path, _prepared())

    with pytest.raises(DeploymentContractError, match="blocks every other"):
        c6c_deployment.assert_manager_mutation_allowed(environment=environment)

    with c6c_deployment.cache_target_window_mutation_scope(
        _TRANSACTION_ID,
        capability=c6c_deployment._CACHE_TARGET_WINDOW_MUTATION_CAPABILITY,
    ):
        assert (
            c6c_deployment.assert_manager_mutation_allowed(environment=environment)
            == "production"
        )

    foreign = "33333333-3333-4333-8333-333333333333"
    with c6c_deployment.cache_target_window_mutation_scope(
        foreign,
        capability=c6c_deployment._CACHE_TARGET_WINDOW_MUTATION_CAPABILITY,
    ):
        with pytest.raises(DeploymentContractError, match="blocks every other"):
            c6c_deployment.assert_manager_mutation_allowed(environment=environment)


def test_map_helper_receipt_requires_exact_secret_free_binding() -> None:
    request = {
        "contract_version": "h35-map/v1",
        "operation": "preflight",
        "transaction_id": _TRANSACTION_ID,
        "source_revision": _MAP_REVISION,
        "database_identity": _DATABASE_IDENTITY,
        "prior_receipt": None,
        "prior_receipt_digest": None,
    }
    document = {
        **{key: value for key, value in request.items() if key != "prior_receipt"},
        "status": "accepted",
        "request_digest": logical_sha256(request),
        "schema_before": "0063_pipeline_root_id",
        "schema_after": "0063_pipeline_root_id",
        "forward_boundary": "not_crossed",
        "row_counts": {"public_item_count": 3265},
            "checks": [
            {
                "name": "identity_violations",
                "expected": 0,
                "observed": 0,
                "passed": True,
            },
            {
                "name": "0075_0078_tables",
                "expected": ["ops.cache_target_a", "ops.cache_target_b"],
                "observed": ["ops.cache_target_a", "ops.cache_target_b"],
                "passed": True,
            },
            ],
            "cache_target_evidence": None,
            "runtime_mutation_count": 0,
        "external_event_count": 0,
    }
    receipt = parse_map_helper_receipt(
        stdout=json.dumps(document, separators=(",", ":")) + "\n",
        stderr="",
        operation="preflight",
        transaction_id=_TRANSACTION_ID,
        source_revision=_MAP_REVISION,
        database_identity=_DATABASE_IDENTITY,
        request=request,
        prior_receipt_digest=None,
    )

    assert receipt.row_counts == {"public_item_count": 3265}
    assert receipt.checks[1].observed == (
        "ops.cache_target_a",
        "ops.cache_target_b",
    )
    assert map_helper_receipt_sha256(receipt) == logical_sha256(asdict(receipt))

    with pytest.raises(DeploymentContractError, match="one JSON line"):
        parse_map_helper_receipt(
            stdout=json.dumps(document) + "\n{}\n",
            stderr="",
            operation="preflight",
            transaction_id=_TRANSACTION_ID,
            source_revision=_MAP_REVISION,
            database_identity=_DATABASE_IDENTITY,
            request=request,
            prior_receipt_digest=None,
        )


def test_map_helper_receipt_rejects_runtime_mutation_and_extra_key() -> None:
    request = {
        "contract_version": "h35-map/v1",
        "operation": "preflight",
        "transaction_id": _TRANSACTION_ID,
        "source_revision": _MAP_REVISION,
        "database_identity": _DATABASE_IDENTITY,
        "prior_receipt": None,
        "prior_receipt_digest": None,
    }
    base = {
        **{key: value for key, value in request.items() if key != "prior_receipt"},
        "status": "accepted",
        "request_digest": logical_sha256(request),
        "schema_before": "0063_pipeline_root_id",
        "schema_after": "0063_pipeline_root_id",
        "forward_boundary": "not_crossed",
        "row_counts": {"public_item_count": 3265},
        "checks": [
            {"name": "identity", "expected": 0, "observed": 0, "passed": True}
        ],
        "cache_target_evidence": None,
        "runtime_mutation_count": 1,
        "external_event_count": 0,
    }

    with pytest.raises(DeploymentContractError, match="binding"):
        parse_map_helper_receipt(
            stdout=json.dumps(base) + "\n",
            stderr="",
            operation="preflight",
            transaction_id=_TRANSACTION_ID,
            source_revision=_MAP_REVISION,
            database_identity=_DATABASE_IDENTITY,
            request=request,
            prior_receipt_digest=None,
        )

    with pytest.raises(DeploymentContractError, match="receipt is invalid"):
        parse_map_helper_receipt(
            stdout=json.dumps(
                {**base, "runtime_mutation_count": 0, "extra": True}
            )
            + "\n",
            stderr="",
            operation="preflight",
            transaction_id=_TRANSACTION_ID,
            source_revision=_MAP_REVISION,
            database_identity=_DATABASE_IDENTITY,
            request=request,
            prior_receipt_digest=None,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("remaining_items", 1),
        ("gc_lock_acquired", False),
        ("gc_observation_run_id", f"h35:{_TRANSACTION_ID}:gc"),
    ],
)
def test_map_gc_receipt_rejects_backlog_or_unobserved_state(
    field: str,
    value: object,
) -> None:
    prior_digest = "a" * 64
    request = {
        "contract_version": "h35-map/v1",
        "operation": "gc",
        "transaction_id": _TRANSACTION_ID,
        "source_revision": _MAP_REVISION,
        "database_identity": _DATABASE_IDENTITY,
        "prior_receipt": asdict(_map_receipt("csv5", None)),
        "prior_receipt_digest": prior_digest,
    }
    receipt = _map_gc_receipt(prior_digest)
    document = asdict(receipt)
    document["request_digest"] = logical_sha256(request)
    if field == "remaining_items":
        row_counts = dict(document["row_counts"])
        row_counts[field] = value
        document["row_counts"] = row_counts
    else:
        checks = list(document["checks"])
        for check in checks:
            if check["name"] == field:
                check["observed"] = value
                break
        document["checks"] = checks

    with pytest.raises(DeploymentContractError, match="Map"):
        parse_map_helper_receipt(
            stdout=json.dumps(document, separators=(",", ":")) + "\n",
            stderr="",
            operation="gc",
            transaction_id=_TRANSACTION_ID,
            source_revision=_MAP_REVISION,
            database_identity=_DATABASE_IDENTITY,
            request=request,
            prior_receipt_digest=prior_digest,
        )


@pytest.mark.parametrize(
    "check_name",
    [
        "gc_referenced_items_preserved",
        "gc_referenced_headers_preserved",
        "gc_observation_referenced_items_fresh",
        "gc_observation_referenced_headers_fresh",
    ],
)
def test_map_gc_receipt_rejects_false_equals_false_required_checks(
    check_name: str,
) -> None:
    prior_digest = "a" * 64
    request = {
        "contract_version": "h35-map/v1",
        "operation": "gc",
        "transaction_id": _TRANSACTION_ID,
        "source_revision": _MAP_REVISION,
        "database_identity": _DATABASE_IDENTITY,
        "prior_receipt": asdict(_map_receipt("csv5", None)),
        "prior_receipt_digest": prior_digest,
    }
    document = asdict(_map_gc_receipt(prior_digest))
    document["request_digest"] = logical_sha256(request)
    checks = list(document["checks"])
    for check in checks:
        if check["name"] == check_name:
            check["expected"] = False
            check["observed"] = False
            break
    document["checks"] = checks

    with pytest.raises(DeploymentContractError, match="observation evidence"):
        parse_map_helper_receipt(
            stdout=json.dumps(document, separators=(",", ":")) + "\n",
            stderr="",
            operation="gc",
            transaction_id=_TRANSACTION_ID,
            source_revision=_MAP_REVISION,
            database_identity=_DATABASE_IDENTITY,
            request=request,
            prior_receipt_digest=prior_digest,
        )

def test_pin_boundary_parser_accepts_exact_preflight_and_final_receipts() -> None:
    preflight_request = _pin_boundary_request("preflight")
    preflight = parse_pin_boundary_receipt(
        stdout=json.dumps(_pin_boundary_receipt("preflight")) + "\n",
        stderr="",
        request=preflight_request,
        expected_initial_count=12,
    )
    preflight_sha = pin_boundary_receipt_sha256(preflight)
    verify_request = _pin_boundary_request(
        "finalize",
        prior_receipt_sha256=preflight_sha,
        canary_run_id="33333333-3333-4333-8333-333333333333",
    )
    final = _pin_boundary_receipt("finalize")
    final.update(verify_request)
    final["audit_request_sha256"] = logical_sha256(verify_request)

    verified = parse_pin_boundary_receipt(
        stdout=json.dumps(final) + "\n",
        stderr="",
        request=verify_request,
        expected_initial_count=12,
    )

    assert verified.schema_revision == "20260802_0048"
    assert verified.expected_initial_event_count == 13

    audit_row = PinBoundaryAuditRow(
        audit_id=verified.audit_id or "",
        audit_request_sha256=verified.audit_request_sha256 or "",
        evidence_sha256=verified.evidence_sha256,
        map_final_evidence_sha256=verified.map_final_evidence_sha256 or "",
        initial_writer_fence_sha256=verified.initial_writer_fence_sha256,
        final_writer_fence_sha256=verified.final_writer_fence_sha256 or "",
        prior_receipt_sha256=verified.prior_receipt_sha256 or "",
        canary_run_id=verified.canary_run_id or "",
    )
    ComposeService._assert_cache_target_pin_audit_receipt(
        receipt=verified,
        audit_row=audit_row,
    )
    with pytest.raises(DeploymentContractError, match="audit row"):
        ComposeService._assert_cache_target_pin_audit_receipt(
            receipt=verified,
            audit_row=PinBoundaryAuditRow(
                **{
                    **asdict(audit_row),
                    "map_final_evidence_sha256": "f" * 64,
                }
            ),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unexpected_non_synthetic_event_count", 1),
        ("pending_command_count", 1),
        ("runtime_mutation_count", 1),
        ("writer_registry_sha256", "f" * 64),
    ],
)
def test_pin_boundary_parser_rejects_nonzero_or_foreign_evidence(
    field: str,
    value: object,
) -> None:
    request = _pin_boundary_request("preflight")
    receipt = _pin_boundary_receipt("preflight")
    receipt[field] = value

    with pytest.raises(DeploymentContractError, match="Pin"):
        parse_pin_boundary_receipt(
            stdout=json.dumps(receipt) + "\n",
            stderr="",
            request=request,
            expected_initial_count=12,
        )


def _pin_boundary_request(
    operation: str,
    *,
    prior_receipt_sha256: str | None = None,
    canary_run_id: str | None = None,
) -> dict[str, object]:
    final = operation == "finalize"
    evidence = _map_final_evidence() if final else None
    return {
        "contract_version": "pinvi-cache-target-final-boundary/v1",
        "operation": operation,
        "transaction_id": _TRANSACTION_ID,
        "cutover_id": _CUTOVER_ID,
        "source_revision": "c" * 40,
        "database_identity": "d" * 64,
        "writer_registry_sha256": (
            "526240609e2919357699b90244eb8cc8b9505f37db6c60552a98c7a37ed22d7c"
        ),
        "initial_writer_fence_sha256": "e" * 64,
        "final_writer_fence_sha256": "f" * 64 if final else None,
        "prior_receipt_sha256": prior_receipt_sha256,
        "canary_run_id": canary_run_id,
        "map_final_evidence": asdict(evidence) if evidence is not None else None,
        "map_final_evidence_sha256": (
            logical_sha256(asdict(evidence)) if evidence is not None else None
        ),
    }


def _pin_boundary_receipt(operation: str) -> dict[str, object]:
    preflight = operation == "preflight"
    request = _pin_boundary_request(operation)
    return {
        **request,
        "status": "succeeded",
        "schema_revision": "20260801_0047" if preflight else "20260802_0048",
        "pending_command_count": 0,
        "leased_command_count": 0,
        "dead_letter_command_count": 0,
        "in_flight_command_count": 0,
        "database_in_flight_transaction_count": 0,
        "email_queue_pending_count": 0,
        "telegram_outbox_pending_count": 0,
        "location_audit_outbox_pending_count": 0,
        "expected_initial_command_count": 0 if preflight else 12,
        "expected_initial_event_count": 0 if preflight else 13,
        "expected_initial_claim_item_count": 0 if preflight else 13,
        "expected_synthetic_command_count": 0 if preflight else 2,
        "expected_synthetic_event_count": 0 if preflight else 2,
        "expected_synthetic_claim_count": 0 if preflight else 2,
        "unexpected_generation7_command_count": 0,
        "unexpected_non_synthetic_event_count": 0,
        "unexpected_non_synthetic_claim_count": 0,
        "initial_evidence_sha256": None if preflight else "1" * 64,
        "canary_provenance_sha256": None if preflight else "2" * 64,
        "final_local_remote_evidence_sha256": None if preflight else "3" * 64,
        "evidence_sha256": "4" * 64,
        "runtime_mutation_count": 0,
        "external_mutation_count": 0,
        "audit_id": None if preflight else _TRANSACTION_ID,
        "audit_request_sha256": None if preflight else logical_sha256(request),
        "audit_row_count": 0 if preflight else 1,
    }
