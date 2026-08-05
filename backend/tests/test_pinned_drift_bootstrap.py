from __future__ import annotations

import json
import os
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import (
    CompatibleImagePair,
    ComposePostMutationContractError,
    DeploymentContractError,
    initial_pair_manifest,
    new_image_pair,
)
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
)
from kor_travel_docker_manager.services.compose_service import ComposeService
from kor_travel_docker_manager.services.pinned_drift_bootstrap import (
    PinnedDriftBootstrapJournal,
    PinnedDriftCancelProbeReceipt,
    assert_pinned_drift_bootstrap_allows_pair_mutation,
    assert_pinned_drift_bootstrap_frozen_inputs,
    assert_pinned_drift_bootstrap_inputs,
    prepare_pinned_drift_bootstrap,
    read_pinned_drift_bootstrap,
    record_pinned_drift_bootstrap_attempt,
    record_pinned_drift_bootstrap_cancel_probe,
    record_pinned_drift_bootstrap_failure,
    transition_pinned_drift_bootstrap,
    write_pinned_drift_bootstrap,
)


def _pair(seed: str) -> CompatibleImagePair:
    return new_image_pair(
        f"sha256:{seed * 64}",
        f"sha256:{seed * 64}",
        "gen7",
        map_ui_image_id=f"sha256:{seed * 64}",
        map_dagster_image_id=f"sha256:{seed * 64}",
        map_dagster_daemon_image_id=f"sha256:{seed * 64}",
        map_source_revision=seed * 40,
        pinvi_source_revision=seed * 40,
    )


def _journal() -> PinnedDriftBootstrapJournal:
    return prepare_pinned_drift_bootstrap(
        production_pin_version=1,
        environment_sha256="1" * 64,
        compose_sha256="2" * 64,
        resolved_compose_sha256="3" * 64,
        old_manifest_sha256="4" * 64,
        old_active=_pair("a"),
        old_rollback=_pair("b"),
        candidate=_pair("c"),
        database_heads={
            "map_application": "0078_cache_target_gc_observe",
            "map_dagster": "abc123",
            "pinvi": "20260802_0048",
        },
    )


def test_pinned_drift_journal_is_durable_and_blocks_other_pair_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path = tmp_path / "pinned-drift-bootstrap-v1.json"
    journal = _journal()
    write_pinned_drift_bootstrap(journal_path, journal)

    assert read_pinned_drift_bootstrap(journal_path) == journal
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.pinned_drift_bootstrap.pinned_drift_bootstrap_journal_path",
        lambda _values: journal_path,
    )
    with pytest.raises(DeploymentContractError, match="unfinished"):
        assert_pinned_drift_bootstrap_allows_pair_mutation({})

    activated = transition_pinned_drift_bootstrap(journal, "runtime_activated")
    manifest_committing = transition_pinned_drift_bootstrap(
        activated,
        "manifest_committing",
    )
    committed = transition_pinned_drift_bootstrap(manifest_committing, "committed")
    write_pinned_drift_bootstrap(journal_path, committed)

    assert_pinned_drift_bootstrap_allows_pair_mutation({})


def test_pinned_drift_journal_rejects_changed_frozen_input() -> None:
    journal = _journal()

    with pytest.raises(DeploymentContractError, match="inputs changed"):
        assert_pinned_drift_bootstrap_inputs(
            journal,
            production_pin_version=1,
            environment_sha256="f" * 64,
            compose_sha256="2" * 64,
            resolved_compose_sha256="3" * 64,
            old_manifest_sha256="4" * 64,
            database_heads={
                "map_application": "0078_cache_target_gc_observe",
                "map_dagster": "abc123",
                "pinvi": "20260802_0048",
            },
        )


def test_pinned_drift_terminal_revalidation_keeps_frozen_evidence() -> None:
    journal = transition_pinned_drift_bootstrap(
        transition_pinned_drift_bootstrap(
            transition_pinned_drift_bootstrap(_journal(), "runtime_activated"),
            "manifest_committing",
        ),
        "committed",
    )

    with pytest.raises(DeploymentContractError, match="inputs changed"):
        assert_pinned_drift_bootstrap_frozen_inputs(
            journal,
            production_pin_version=2,
            environment_sha256="1" * 64,
            compose_sha256="2" * 64,
            resolved_compose_sha256="3" * 64,
            database_heads={
                "map_application": "0078_cache_target_gc_observe",
                "map_dagster": "abc123",
                "pinvi": "20260802_0048",
            },
        )


def test_pinned_drift_journal_rejects_non_owner_only_mode(tmp_path: Path) -> None:
    journal_path = tmp_path / "pinned-drift-bootstrap-v1.json"
    write_pinned_drift_bootstrap(journal_path, _journal())
    os.chmod(journal_path, 0o644)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_pinned_drift_bootstrap(journal_path)


def test_manifest_commit_crash_resumes_from_candidate_only_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    old = _pair("a")
    candidate = _pair("c")
    manifest = initial_pair_manifest(candidate)
    manifest_path = tmp_path / "compatible-pair-v4.json"
    manifest_path.write_text("candidate-only\n", encoding="utf-8")
    transaction = SimpleNamespace(
        environment=SimpleNamespace(
            effective={"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
            env_path=str(tmp_path / ".env"),
            env_file_bytes=b"environment",
            compose_path=str(tmp_path / "docker-compose.yml"),
            env_file_identity=SimpleNamespace(uid=1000, gid=1000),
        ),
        compose_source_bytes=b"compose",
        resolved_document_hash="3" * 64,
        manifest_path=str(manifest_path),
    )
    journal = prepare_pinned_drift_bootstrap(
        production_pin_version=CACHE_TARGET_PRODUCTION_PINS.version,
        environment_sha256=sha256(b"environment").hexdigest(),
        compose_sha256=sha256(b"compose").hexdigest(),
        resolved_compose_sha256="3" * 64,
        old_manifest_sha256="4" * 64,
        old_active=old,
        old_rollback=old,
        candidate=candidate,
        database_heads={
            "map_application": "0078_cache_target_gc_observe",
            "map_dagster": "abc123",
            "pinvi": "20260802_0048",
        },
    )
    journal = transition_pinned_drift_bootstrap(journal, "runtime_activated")
    journal = transition_pinned_drift_bootstrap(journal, "manifest_committing")
    database_heads = dict(journal.database_heads)
    writes: list[PinnedDriftBootstrapJournal] = []
    write_manifest = Mock()

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: nullcontext(SimpleNamespace()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_assert_transaction_matches_c6c_lock",
        Mock(),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, None)),
    )
    monkeypatch.setattr(compose_service_module, "assert_manager_mutation_allowed", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "load_c6c_deployment_config_from_environment",
        Mock(return_value=SimpleNamespace(production=True)),
    )
    monkeypatch.setattr(
        compose_service_module,
        "require_pinned_deployment_input_handoff",
        Mock(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "mark_pinned_deployment_input_f1d_completed",
        Mock(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "mark_pinned_deployment_input_f1d_started",
        Mock(),
    )
    monkeypatch.setattr(compose_service_module, "_require_cache_target_release", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "pinned_drift_bootstrap_journal_path",
        lambda _values: tmp_path / "pinned-drift-bootstrap-v1.json",
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_pinned_drift_bootstrap",
        Mock(return_value=journal),
    )
    monkeypatch.setattr(
        compose_service_module,
        "load_pair_manifest",
        Mock(return_value=manifest),
    )
    monkeypatch.setattr(service, "_pinned_drift_database_heads", Mock(return_value=database_heads))
    monkeypatch.setattr(service, "_require_pair_image_provenance", Mock())
    monkeypatch.setattr(service, "_assert_pinned_drift_candidate_database_heads", Mock())
    monkeypatch.setattr(compose_service_module, "ensure_pair_references", Mock())
    monkeypatch.setattr(
        service,
        "_verify_pinned_drift_candidate",
        Mock(return_value={"verified": True}),
    )
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_drift_bootstrap",
        lambda _path, persisted: writes.append(persisted),
    )
    monkeypatch.setattr(compose_service_module, "write_pair_manifest", write_manifest)
    monkeypatch.setattr(
        compose_service_module,
        "reconcile_pair_references",
        Mock(return_value=SimpleNamespace(removed=[])),
    )
    monkeypatch.setattr(service, "_pair_provenance_payload", Mock(return_value={}))

    result = service.bootstrap_pinned_drift()

    assert result["state"] == "committed"
    assert result["resumed"] is True
    write_manifest.assert_not_called()
    assert writes[-1].phase == "committed"


def test_runtime_reverification_failure_bubbles_to_bootstrap_fail_close_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    verification = Mock(side_effect=DeploymentContractError("verification failed"))

    monkeypatch.setattr(service, "_verify_active_contract", verification)

    with pytest.raises(DeploymentContractError, match="verification failed"):
        service._verify_pinned_drift_candidate(
            config=SimpleNamespace(),
            candidate=_pair("c"),
            services=["kor-travel-map-api", "pinvi-api"],
            transaction=SimpleNamespace(),
            expected_database_heads={
                "map_application": "0078_cache_target_gc_observe",
                "map_dagster": "abc123",
                "pinvi": "20260802_0048",
            },
            checkpoint_recorder=Mock(),
        )


def test_runtime_reverification_prefixes_contract_checkpoint_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    checkpoints: list[str] = []

    def verify(*_args, checkpoint_recorder, **_kwargs):
        checkpoint_recorder("services_ready")
        return {"verified": True}

    monkeypatch.setattr(service, "_verify_active_contract", verify)
    monkeypatch.setattr(service, "_assert_pinned_drift_database_heads", Mock())

    assert service._verify_pinned_drift_candidate(
        config=SimpleNamespace(),
        candidate=_pair("c"),
        services=["kor-travel-map-api", "pinvi-api"],
        transaction=SimpleNamespace(),
        expected_database_heads={
            "map_application": "0078_cache_target_gc_observe",
            "map_dagster": "abc123",
            "pinvi": "20260802_0048",
        },
        checkpoint_recorder=checkpoints.append,
    ) == {"verified": True}
    assert checkpoints == ["contract.services_ready", "database_heads"]


def test_pinned_drift_failure_evidence_preserves_prior_failure_on_retry() -> None:
    journal = record_pinned_drift_bootstrap_attempt(_journal(), "prepared.stop_pair")
    failed = record_pinned_drift_bootstrap_failure(journal, "prepared.stop_pair")
    retried = record_pinned_drift_bootstrap_attempt(failed, "prepared.map_api_up")

    assert retried.attempt_checkpoint == "prepared.map_api_up"
    assert retried.last_failure_checkpoint == "prepared.stop_pair"
    assert retried.last_failed_at == failed.last_failed_at
    assert retried.failure_count == 1


def test_pinned_drift_cancel_probe_receipt_is_monotonic_and_reconstructible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = "77777777-7777-4777-8777-777777777777"
    cancellation_id = "22222222-2222-4222-8222-222222222222"
    armed = PinnedDriftCancelProbeReceipt(
        job_id=job_id,
        state="armed",
        cancellation_id=None,
        attempted=False,
        response_status=None,
        response_code=None,
        response_verified_at=None,
        finalized_at=None,
    )
    journal = record_pinned_drift_bootstrap_cancel_probe(_journal(), armed)
    tracker = compose_service_module._PinnedDriftCheckpointTracker(
        journal_path=Path("/non-persistent-fixture.json"),
        journal=journal,
        fresh_journal=True,
    )
    state = tracker.cancel_probe_state()
    assert state.transaction_id == journal.transaction_id
    assert state.fixture is not None
    assert state.fixture.job_id == job_id
    assert state.attempted is False
    assert state.result is None

    writes: list[PinnedDriftBootstrapJournal] = []
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_drift_bootstrap",
        lambda _path, persisted: writes.append(persisted),
    )
    state.attempted = True
    state.fixture = state.fixture.__class__(
        transaction_id=state.transaction_id,
        job_id=job_id,
        state="consumed",
        cancellation_id=cancellation_id,
        canonical_unsafe_outcome=dict(state.result or {}),
    )
    state.result = {
        "name": "pinvi_cancel_error",
        "status": 409,
        "code": "PIPELINE_CANCELLATION_UNSAFE",
    }
    tracker.persist_cancel_probe(state)

    consumed = tracker.journal.cancel_probe
    assert consumed is not None
    assert consumed.state == "consumed"
    assert consumed.response_status == 409
    assert consumed.response_verified_at is not None
    assert len(writes) == 1

    state.fixture = state.fixture.__class__(
        transaction_id=state.transaction_id,
        job_id=job_id,
        state="finalized",
        cancellation_id=cancellation_id,
        canonical_unsafe_outcome=dict(state.result or {}),
    )
    tracker.persist_cancel_probe(state)

    finalized = tracker.journal.cancel_probe
    assert finalized is not None
    assert finalized.state == "finalized"
    assert finalized.finalized_at is not None
    assert tracker.cancel_probe_state().result == state.result

    with pytest.raises(DeploymentContractError, match="regressed"):
        record_pinned_drift_bootstrap_cancel_probe(
            tracker.journal,
            PinnedDriftCancelProbeReceipt(
                job_id=job_id,
                state="armed",
                cancellation_id=None,
                attempted=False,
                response_status=None,
                response_code=None,
                response_verified_at=None,
                finalized_at=None,
            ),
        )


def test_pinned_drift_cancel_probe_rejects_unverified_consumed_receipt() -> None:
    with pytest.raises(DeploymentContractError, match="consumed"):
        record_pinned_drift_bootstrap_cancel_probe(
            _journal(),
            PinnedDriftCancelProbeReceipt(
                job_id="77777777-7777-4777-8777-777777777777",
                state="consumed",
                cancellation_id="22222222-2222-4222-8222-222222222222",
                attempted=True,
                response_status=None,
                response_code=None,
                response_verified_at=None,
                finalized_at=None,
            ),
        )


def test_pinned_drift_journal_reads_exact_base_v2_shape_and_rewrites_extended_shape(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "pinned-drift-bootstrap-v2.json"
    write_pinned_drift_bootstrap(journal_path, _journal())
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    for key in (
        "attempt_checkpoint",
        "last_failure_checkpoint",
        "last_failed_at",
        "failure_count",
    ):
        del payload[key]
    journal_path.write_text(json.dumps(payload), encoding="utf-8")

    legacy = read_pinned_drift_bootstrap(journal_path)

    assert legacy is not None
    assert legacy.attempt_checkpoint is None
    assert legacy.last_failure_checkpoint is None
    assert legacy.last_failed_at is None
    assert legacy.failure_count == 0

    write_pinned_drift_bootstrap(
        journal_path,
        record_pinned_drift_bootstrap_attempt(legacy, "prepared.stop_pair"),
    )
    rewritten = json.loads(journal_path.read_text(encoding="utf-8"))
    assert rewritten["attempt_checkpoint"] == "prepared.stop_pair"
    assert rewritten["last_failure_checkpoint"] is None
    assert rewritten["failure_count"] == 0


def test_pinned_drift_failure_evidence_requires_complete_typed_triplet(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "pinned-drift-bootstrap-v2.json"
    write_pinned_drift_bootstrap(journal_path, _journal())
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    payload["failure_count"] = True
    journal_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DeploymentContractError, match="failure evidence"):
        read_pinned_drift_bootstrap(journal_path)


def test_fresh_pre_mutation_checkpoint_failure_does_not_halt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    tracker = compose_service_module._PinnedDriftCheckpointTracker(
        journal_path=tmp_path / "pinned-drift-bootstrap-v2.json",
        journal=_journal(),
        fresh_journal=True,
        current_checkpoint="prepared.stop_pair",
    )
    halt = Mock()
    monkeypatch.setattr(service, "_halt_c6c_pair", halt)

    with pytest.raises(DeploymentContractError, match="before runtime mutation"):
        service._raise_pinned_drift_bootstrap_failure(
            result={"success": True, "returncode": 0, "stderr": ""},
            config=SimpleNamespace(),
            transaction=SimpleNamespace(),
            error=DeploymentContractError("journal write failed"),
            checkpoint_tracker=tracker,
        )

    halt.assert_not_called()


def test_post_mutation_failure_halts_when_failure_evidence_cannot_persist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    tracker = compose_service_module._PinnedDriftCheckpointTracker(
        journal_path=tmp_path / "pinned-drift-bootstrap-v2.json",
        journal=_journal(),
        fresh_journal=False,
        current_checkpoint="prepared.contract.ui_auth",
    )
    halt = Mock(return_value={"success": True, "state": "halted_requires_operator"})
    monkeypatch.setattr(tracker, "persist_failure", Mock(side_effect=OSError("fsync failed")))
    monkeypatch.setattr(service, "_halt_c6c_pair", halt)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service._raise_pinned_drift_bootstrap_failure(
            result={"success": True, "returncode": 0, "stderr": ""},
            config=SimpleNamespace(),
            transaction=SimpleNamespace(),
            error=DeploymentContractError("candidate verification failed"),
            checkpoint_tracker=tracker,
        )

    assert caught.value.restoration == {
        "success": True,
        "state": "halted_requires_operator",
        "failure_checkpoint": "prepared.contract.ui_auth",
        "failure_count": 0,
        "failure_evidence_persisted": False,
    }
    halt.assert_called_once()


def test_pinned_drift_candidate_head_mismatch_blocks_before_runtime_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    old = _pair("a")
    candidate = _pair("c")
    manifest = initial_pair_manifest(old)
    manifest_path = tmp_path / "compatible-pair-v4.json"
    manifest_path.write_text("old-pair\n", encoding="utf-8")
    transaction = SimpleNamespace(
        environment=SimpleNamespace(
            effective={"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
            env_path=str(tmp_path / ".env"),
            env_file_bytes=b"environment",
            compose_path=str(tmp_path / "docker-compose.yml"),
            env_file_identity=SimpleNamespace(uid=1000, gid=1000),
        ),
        compose_source_bytes=b"compose",
        resolved_document_hash="3" * 64,
        manifest_path=str(manifest_path),
    )
    candidate_build = Mock(
        return_value=SimpleNamespace(
            map_source_revision=candidate.map_source_revision,
            pinvi_source_revision=candidate.pinvi_source_revision,
        )
    )
    prepare_candidate = Mock(return_value=(candidate, None))
    activate_candidate = Mock()
    retention = Mock()

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: nullcontext(SimpleNamespace()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_assert_transaction_matches_c6c_lock",
        Mock(),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, None)),
    )
    monkeypatch.setattr(compose_service_module, "assert_manager_mutation_allowed", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "load_c6c_deployment_config_from_environment",
        Mock(return_value=SimpleNamespace(production=True)),
    )
    monkeypatch.setattr(
        compose_service_module,
        "require_pinned_deployment_input_handoff",
        Mock(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "mark_pinned_deployment_input_f1d_started",
        Mock(),
    )
    monkeypatch.setattr(compose_service_module, "_require_cache_target_release", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "pinned_drift_bootstrap_journal_path",
        lambda _values: tmp_path / "pinned-drift-bootstrap-v1.json",
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_pinned_drift_bootstrap",
        Mock(return_value=None),
    )
    monkeypatch.setattr(
        compose_service_module,
        "load_pair_manifest",
        Mock(return_value=manifest),
    )
    monkeypatch.setattr(
        service,
        "_pinned_drift_database_heads",
        Mock(
            return_value={
                "map_application": "0078_cache_target_gc_observe",
                "map_dagster": "abc123",
                "pinvi": "20260802_0048",
            }
        ),
    )
    monkeypatch.setattr(service, "_require_pair_image_provenance", Mock())
    monkeypatch.setattr(
        service,
        "_validate_resolved_compose_contract",
        Mock(),
    )
    legacy_ready = Mock()
    legacy_runtime_config = Mock()
    legacy_secret_isolation = Mock()
    legacy_ui_smoke = Mock()
    monkeypatch.setattr(service, "_require_services_ready", legacy_ready)
    monkeypatch.setattr(
        service,
        "_inspect_c6c_runtime_configs",
        legacy_runtime_config,
    )
    monkeypatch.setattr(
        compose_service_module,
        "validate_runtime_secret_isolation",
        legacy_secret_isolation,
    )
    monkeypatch.setattr(
        compose_service_module,
        "run_map_ui_auth_preflight",
        legacy_ui_smoke,
    )
    monkeypatch.setattr(
        service,
        "_prepare_c6c_candidate_pair",
        prepare_candidate,
    )
    monkeypatch.setattr(
        service,
        "_assert_pinned_drift_candidate_database_heads",
        Mock(side_effect=DeploymentContractError("candidate Map API head differs")),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_derive_c6c_build_provenance",
        candidate_build,
    )
    monkeypatch.setattr(compose_service_module, "ensure_pair_references", retention)
    monkeypatch.setattr(service, "_activate_pair_sequentially", activate_candidate)

    with pytest.raises(DeploymentContractError, match="candidate Map API"):
        service.bootstrap_pinned_drift()

    candidate_build.assert_called_once()
    prepare_candidate.assert_called_once()
    legacy_ready.assert_not_called()
    legacy_runtime_config.assert_not_called()
    legacy_secret_isolation.assert_not_called()
    legacy_ui_smoke.assert_not_called()
    retention.assert_not_called()
    activate_candidate.assert_not_called()
