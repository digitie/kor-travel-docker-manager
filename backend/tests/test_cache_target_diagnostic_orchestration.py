from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_backup import DatabaseRuntime
from kor_travel_docker_manager.services.cache_target_diagnostics import (
    CacheTargetDiagnosticIdentity,
    DiagnosticAttemptLog,
    DiagnosticStageReceipt,
    prepare_cache_target_diagnostic,
    read_cache_target_diagnostic,
    read_cache_target_diagnostic_attempt_log,
    record_diagnostic_attempt,
    transition_cache_target_diagnostic,
    write_cache_target_diagnostic,
    write_cache_target_diagnostic_attempt_log,
)
from kor_travel_docker_manager.services.compose_service import (
    _COMPATIBLE_PAIR_MUTATION_CAPABILITY,
    ComposeService,
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


def _receipt(
    role: str,
    stage: str,
    *,
    status: str = "succeeded",
    failure_class: str | None = None,
) -> DiagnosticStageReceipt:
    return DiagnosticStageReceipt(
        role=role,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        failure_class=failure_class,  # type: ignore[arg-type]
        elapsed_ms=100,
        archive_sha256="a" * 64 if status == "succeeded" and stage == "source_archive" else None,
        schema_inventory_sha256=("b" * 64 if status == "succeeded" and "schema" in stage else None),
        data_inventory_sha256=("c" * 64 if status == "succeeded" and "data" in stage else None),
        scratch_identity_sha256=(
            "d" * 64 if status == "succeeded" and stage == "scratch_create" else None
        ),
    )


def _runtime(role: str) -> DatabaseRuntime:
    return DatabaseRuntime(
        role=role,  # type: ignore[arg-type]
        container_name="kor-travel-geo-postgres",
        database_name=f"{role}_db",
        owner_name=f"{role}_owner",
        admin_name="postgres",
    )


def _install_diagnostic_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ComposeService, Path, Path]:
    service = ComposeService()
    journal_path = tmp_path / "cache-target-diagnostic-v1.json"
    attempt_log_path = tmp_path / "cache-target-diagnostic-attempts-v1.json"
    manifest_path = tmp_path / "compatible-pair-v4.json"
    transaction = SimpleNamespace(
        manifest_path=str(manifest_path),
        resolved={},
        environment=SimpleNamespace(effective={}),
        compose_source_bytes=b"",
        resolved_document_hash="0" * 64,
    )
    config = SimpleNamespace(
        production=True,
        cache_target=SimpleNamespace(role_binding_sha256="6" * 64),
    )
    manifest = SimpleNamespace(
        active=SimpleNamespace(),
        rollback=SimpleNamespace(),
    )
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
        "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
        Mock(return_value=manifest),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.cache_target_diagnostic_journal_path",
        Mock(return_value=journal_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.cache_target_diagnostic_attempt_log_path",
        Mock(return_value=attempt_log_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.assert_manager_mutation_allowed",
        Mock(),
    )
    monkeypatch.setattr(
        service,
        "_cache_target_diagnostic_identity",
        Mock(return_value=_identity()),
    )
    return service, journal_path, attempt_log_path


def test_diagnose_resumes_terminal_journal_without_rerunning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, journal_path, _attempt_log_path = _install_diagnostic_context(tmp_path, monkeypatch)
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID, identity=_identity(), started_at_unix=1_700_000_000
    )
    journal = replace(
        journal, phase="failed", failure_stage="source_archive", failure_class="timeout"
    )
    write_cache_target_diagnostic(journal_path, journal)
    unlocked = Mock(side_effect=AssertionError("must not re-run a terminal diagnostic"))
    monkeypatch.setattr(service, "_run_cache_target_diagnostic_unlocked", unlocked)

    result = service.run_cache_target_diagnostic(diagnostic_id=_DIAGNOSTIC_ID)

    assert result["resumed"] is True
    assert result["phase"] == "failed"
    unlocked.assert_not_called()


def test_diagnose_rejects_crashed_nonterminal_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, journal_path, _attempt_log_path = _install_diagnostic_context(tmp_path, monkeypatch)
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID, identity=_identity(), started_at_unix=1_700_000_000
    )
    write_cache_target_diagnostic(journal_path, journal)

    with pytest.raises(DeploymentContractError, match="crashed mid-run"):
        service.run_cache_target_diagnostic(diagnostic_id=_DIAGNOSTIC_ID)


def test_diagnose_new_id_aborts_and_archives_crashed_journal_before_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, journal_path, attempt_log_path = _install_diagnostic_context(tmp_path, monkeypatch)
    previous_id = "99999999-9999-4999-8999-999999999999"
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=previous_id,
        identity=_identity(),
        started_at_unix=1_700_000_000,
    )
    journal = transition_cache_target_diagnostic(journal, "writers_fencing")
    journal = transition_cache_target_diagnostic(
        journal,
        "writers_fenced",
        writer_fence_sha256="f" * 64,
    )
    write_cache_target_diagnostic(journal_path, journal)
    unlocked = Mock(return_value={"phase": "prepared"})
    monkeypatch.setattr(service, "_run_cache_target_diagnostic_unlocked", unlocked)
    monkeypatch.setattr("kor_travel_docker_manager.services.compose_service.time.time", lambda: 1_700_000_100)

    result = service.run_cache_target_diagnostic(diagnostic_id=_DIAGNOSTIC_ID)

    assert result == {"phase": "prepared"}
    assert read_cache_target_diagnostic(journal_path).diagnostic_id == _DIAGNOSTIC_ID
    archived = list(
        tmp_path.glob(f"cache-target-diagnostic-archive-v1-{previous_id}-aborted.json")
    )
    assert len(archived) == 1
    assert read_cache_target_diagnostic(archived[0]).phase == "aborted"
    attempts = read_cache_target_diagnostic_attempt_log(attempt_log_path)
    assert [(item.diagnostic_id, item.phase) for item in attempts.attempts] == [
        (previous_id, "aborted")
    ]
    unlocked.assert_called_once()


def test_diagnose_new_id_archives_preflight_crash_without_spending_attempt_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, journal_path, attempt_log_path = _install_diagnostic_context(tmp_path, monkeypatch)
    previous_id = "99999999-9999-4999-8999-999999999999"
    journal = transition_cache_target_diagnostic(
        prepare_cache_target_diagnostic(
            diagnostic_id=previous_id,
            identity=_identity(),
            started_at_unix=1_700_000_000,
        ),
        "writers_fencing",
    )
    write_cache_target_diagnostic(journal_path, journal)
    unlocked = Mock(return_value={"phase": "prepared"})
    monkeypatch.setattr(service, "_run_cache_target_diagnostic_unlocked", unlocked)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.time.time",
        lambda: 1_700_000_100,
    )

    service.run_cache_target_diagnostic(diagnostic_id=_DIAGNOSTIC_ID)

    archived = list(
        tmp_path.glob(f"cache-target-diagnostic-archive-v1-{previous_id}-aborted.json")
    )
    assert len(archived) == 1
    assert read_cache_target_diagnostic(archived[0]).phase == "aborted"
    assert not attempt_log_path.exists()
    unlocked.assert_called_once()


def test_diagnose_new_id_archives_terminal_journal_without_duplicate_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, journal_path, attempt_log_path = _install_diagnostic_context(tmp_path, monkeypatch)
    previous_id = "99999999-9999-4999-8999-999999999999"
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=previous_id,
        identity=_identity(),
        started_at_unix=1_700_000_000,
    )
    journal = replace(
        journal,
        phase="failed",
        failure_stage="source_archive",
        failure_class="timeout",
    )
    write_cache_target_diagnostic(journal_path, journal)
    attempts = record_diagnostic_attempt(
        DiagnosticAttemptLog(version=1),
        journal,
        now_unix=1_700_000_100,
    )
    write_cache_target_diagnostic_attempt_log(attempt_log_path, attempts)
    unlocked = Mock(return_value={"phase": "prepared"})
    monkeypatch.setattr(service, "_run_cache_target_diagnostic_unlocked", unlocked)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.time.time",
        lambda: 1_700_000_100,
    )

    service.run_cache_target_diagnostic(diagnostic_id=_DIAGNOSTIC_ID)

    archived = list(
        tmp_path.glob(f"cache-target-diagnostic-archive-v1-{previous_id}-failed.json")
    )
    assert len(archived) == 1
    attempts = read_cache_target_diagnostic_attempt_log(attempt_log_path)
    assert [(item.diagnostic_id, item.phase) for item in attempts.attempts] == [
        (previous_id, "failed")
    ]
    unlocked.assert_called_once()


def test_diagnose_refuses_archive_collision_before_starting_new_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, journal_path, _attempt_log_path = _install_diagnostic_context(tmp_path, monkeypatch)
    previous_id = "99999999-9999-4999-8999-999999999999"
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=previous_id,
        identity=_identity(),
        started_at_unix=1_700_000_000,
    )
    write_cache_target_diagnostic(journal_path, journal)
    archive_path = tmp_path / f"cache-target-diagnostic-archive-v1-{previous_id}-aborted.json"
    archive_path.write_text("collision")
    unlocked = Mock(side_effect=AssertionError("must not start after archive collision"))
    monkeypatch.setattr(service, "_run_cache_target_diagnostic_unlocked", unlocked)
    monkeypatch.setattr("kor_travel_docker_manager.services.compose_service.time.time", lambda: 1_700_000_100)

    with pytest.raises(DeploymentContractError, match="archive already exists"):
        service.run_cache_target_diagnostic(diagnostic_id=_DIAGNOSTIC_ID)

    assert read_cache_target_diagnostic(journal_path).phase == "aborted"
    unlocked.assert_not_called()

def test_diagnose_rejects_when_abort_budget_is_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _journal_path, _attempt_log_path = _install_diagnostic_context(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnostic_attempt_budget_exceeded",
        Mock(return_value=True),
    )
    unlocked = Mock(side_effect=AssertionError("must not run once budget is exhausted"))
    monkeypatch.setattr(service, "_run_cache_target_diagnostic_unlocked", unlocked)

    with pytest.raises(DeploymentContractError, match="abort budget"):
        service.run_cache_target_diagnostic(diagnostic_id=_DIAGNOSTIC_ID)
    unlocked.assert_not_called()


def test_diagnose_rejects_malformed_diagnostic_id() -> None:
    service = ComposeService()
    with pytest.raises(DeploymentContractError, match="diagnostic ID is invalid"):
        service.run_cache_target_diagnostic(diagnostic_id="not-a-uuid")


def test_diagnose_rejects_noncanonical_diagnostic_id() -> None:
    service = ComposeService()
    with pytest.raises(DeploymentContractError, match="canonical"):
        service.run_cache_target_diagnostic(diagnostic_id="8A3E6B2C-8F1E-4C8B-9C3D-0F1A2B3C4D5E")


def _install_unlocked_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ComposeService, Path, Path, SimpleNamespace, Mock, Mock]:
    service = ComposeService()
    journal_path = tmp_path / "cache-target-diagnostic-v1.json"
    attempt_log_path = tmp_path / "cache-target-diagnostic-attempts-v1.json"
    transaction = SimpleNamespace(resolved={}, environment=SimpleNamespace(effective={}))
    runtimes = (_runtime("map_application"), _runtime("map_dagster"), _runtime("pinvi"))
    monkeypatch.setattr(service, "_cache_target_writer_names", Mock(return_value=("svc",)))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.database_runtimes_from_frozen_contract",
        Mock(return_value=runtimes),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_database_inflight_count",
        Mock(return_value=0),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.read_dagster_inflight_run_count",
        Mock(return_value=0),
    )
    monkeypatch.setattr(service, "_run_frozen_recovery", Mock(return_value={"success": True}))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.cache_target_writer_environments_from_resolved_compose",
        Mock(return_value={}),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.attest_cache_target_global_writer_fence",
        Mock(return_value=SimpleNamespace(inventory_sha256="e" * 64)),
    )
    monkeypatch.setattr(
        service, "_inspect_current_pair", Mock(return_value=SimpleNamespace(id="stable-pair"))
    )
    monkeypatch.setattr(
        service, "_pair_matches", Mock(side_effect=lambda a, b: a is b or a == b)
    )
    activated = Mock()
    monkeypatch.setattr(service, "_activate_cache_target_writers", activated)
    monkeypatch.setattr(service, "_attest_cache_target_pair", Mock())
    monkeypatch.setattr(service, "_attest_cache_target_prebootstrap_pair", Mock())
    smoke = Mock()
    monkeypatch.setattr(service, "_run_cache_target_rollback_health_smoke", smoke)
    return service, journal_path, attempt_log_path, transaction, activated, smoke


def test_prebootstrap_attestation_materializes_old_pair_without_changing_candidate_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """old runtime 재-attestation만 materialize하고 receipt identity 입력은 바꾸지 않는다."""
    service = ComposeService()
    active_pair = SimpleNamespace()
    manifest = SimpleNamespace(active=active_pair, rollback=SimpleNamespace())
    transaction = SimpleNamespace(name="candidate")
    prebootstrap_transaction = SimpleNamespace(name="old-pair")
    release_gate = Mock(side_effect=AssertionError("diagnostic must not require candidate release"))
    materialize = Mock(return_value=prebootstrap_transaction)
    ready = Mock()
    resolved = Mock()
    runtime_configs = [SimpleNamespace()]
    secret_isolation = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service._require_cache_target_release",
        release_gate,
    )
    monkeypatch.setattr(service, "_materialize_active_recovery_transaction_unlocked", materialize)
    monkeypatch.setattr(service, "_require_services_ready", ready)
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", resolved)
    monkeypatch.setattr(service, "_inspect_current_pair", Mock(return_value=active_pair))
    monkeypatch.setattr(service, "_pair_matches", Mock(return_value=True))
    monkeypatch.setattr(service, "_inspect_c6c_runtime_configs", Mock(return_value=runtime_configs))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.validate_runtime_secret_isolation",
        secret_isolation,
    )

    config = SimpleNamespace()
    service._attest_cache_target_prebootstrap_pair(config, manifest, transaction)

    release_gate.assert_not_called()
    materialize.assert_called_once_with(transaction, config, active_pair)
    assert ready.call_args.kwargs["transaction"] is prebootstrap_transaction
    assert resolved.call_args.kwargs["transaction"] is prebootstrap_transaction
    assert (
        service._inspect_c6c_runtime_configs.call_args.kwargs["transaction"]
        is prebootstrap_transaction
    )
    secret_isolation.assert_called_once_with(runtime_configs, config)


def test_diagnostic_captures_candidate_identity_before_old_pair_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _journal_path, _attempt_log_path = _install_diagnostic_context(tmp_path, monkeypatch)
    candidate_transaction = service._capture_transaction_unlocked.return_value[0]
    identity = _identity(resolved_compose_sha256="c" * 64)
    identity_factory = Mock(return_value=identity)
    old_pair_transaction = SimpleNamespace(name="old-pair")
    monkeypatch.setattr(service, "_cache_target_diagnostic_identity", identity_factory)
    monkeypatch.setattr(
        service,
        "_materialize_active_recovery_transaction_unlocked",
        Mock(return_value=old_pair_transaction),
    )
    monkeypatch.setattr(service, "_require_services_ready", Mock())
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", Mock())
    monkeypatch.setattr(service, "_inspect_current_pair", Mock(return_value=SimpleNamespace()))
    monkeypatch.setattr(service, "_pair_matches", Mock(return_value=True))
    monkeypatch.setattr(service, "_inspect_c6c_runtime_configs", Mock(return_value=[]))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.validate_runtime_secret_isolation",
        Mock(),
    )

    def verify_unlocked(**kwargs: object) -> dict[str, str]:
        assert kwargs["transaction"] is candidate_transaction
        assert kwargs["journal"].identity == identity
        service._attest_cache_target_prebootstrap_pair(
            kwargs["config"],  # type: ignore[arg-type]
            kwargs["manifest"],  # type: ignore[arg-type]
            kwargs["transaction"],  # type: ignore[arg-type]
        )
        return {"phase": "prepared"}

    monkeypatch.setattr(service, "_run_cache_target_diagnostic_unlocked", verify_unlocked)

    service.run_cache_target_diagnostic(diagnostic_id=_DIAGNOSTIC_ID)

    assert identity_factory.call_args.kwargs["transaction"] is candidate_transaction


def test_generation_bootstrap_rejects_candidate_release_mismatch_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    config = SimpleNamespace(cache_target=SimpleNamespace(sync_enabled="false"))
    active_pair = SimpleNamespace()
    transaction = SimpleNamespace(manifest_path="pair-manifest.json")
    candidate = SimpleNamespace(
        map_source_revision="m" * 40,
        pinvi_source_revision="p" * 40,
    )
    release_gate = Mock(
        side_effect=DeploymentContractError("candidate release does not match tracked pin")
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
        Mock(return_value=SimpleNamespace(active=active_pair, rollback=SimpleNamespace())),
    )
    monkeypatch.setattr(service, "_inspect_current_pair", Mock(return_value=active_pair))
    monkeypatch.setattr(service, "_pair_matches", Mock(return_value=True))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service._require_cache_target_release",
        release_gate,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.ensure_pair_references",
        Mock(side_effect=AssertionError("must not mutate after release mismatch")),
    )

    with pytest.raises(DeploymentContractError, match="candidate release"):
        service._bootstrap_cache_target_generation(
            config=config,
            transaction=transaction,
            candidate=candidate,
            wait_timeout=1,
        )

    release_gate.assert_called_once_with(
        config,
        candidate_map_source_revision=candidate.map_source_revision,
        candidate_source_revision=candidate.pinvi_source_revision,
    )


def test_unlocked_diagnostic_rejects_writer_restart_onto_a_drifted_image_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """진단은 read-mostly라 writer stop/restart가 새 candidate 활성화 수단이 되면
    안 된다. floating tag가 stop~restart 사이 다른 이미지로 이미 바뀌어 있어서
    재기동 뒤 pair가 재기동 전과 달라지면(manifest의 active pair와는 우연히 같더라도)
    즉시 거부해야 한다 — 실 production에서 이 정확한 경로로 재현된 사고(issue #109)."""
    service, journal_path, attempt_log_path, transaction, activated, _smoke = (
        _install_unlocked_context(tmp_path, monkeypatch)
    )
    pairs = iter([SimpleNamespace(id="before"), SimpleNamespace(id="after-drifted")])
    monkeypatch.setattr(service, "_inspect_current_pair", Mock(side_effect=lambda _c: next(pairs)))
    monkeypatch.setattr(service, "_pair_matches", Mock(side_effect=lambda a, b: a.id == b.id))
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID, identity=_identity(), started_at_unix=1_700_000_000
    )
    write_cache_target_diagnostic(journal_path, journal)
    monkeypatch.setattr(
        service,
        "_run_cache_target_diagnostic_role",
        Mock(side_effect=lambda runtime, *_a: (_receipt(runtime.role, "source_archive"),)),
    )

    with pytest.raises(DeploymentContractError, match="different image pair"):
        service._run_cache_target_diagnostic_unlocked(
            journal_path=journal_path,
            attempt_log_path=attempt_log_path,
            journal=journal,
            transaction=transaction,
            config=SimpleNamespace(),
            manifest=SimpleNamespace(),
            state_directory=tmp_path,
        )

    activated.assert_called_once()
    service._attest_cache_target_pair.assert_not_called()
    service._attest_cache_target_prebootstrap_pair.assert_not_called()


def test_unlocked_diagnostic_completes_and_always_restarts_writers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, journal_path, attempt_log_path, transaction, activated, smoke = (
        _install_unlocked_context(tmp_path, monkeypatch)
    )
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID, identity=_identity(), started_at_unix=1_700_000_000
    )
    write_cache_target_diagnostic(journal_path, journal)

    def role_receipts(
        runtime: DatabaseRuntime, *_args: object
    ) -> tuple[DiagnosticStageReceipt, ...]:
        return (_receipt(runtime.role, "source_archive"),)

    monkeypatch.setattr(
        service, "_run_cache_target_diagnostic_role", Mock(side_effect=role_receipts)
    )

    result = service._run_cache_target_diagnostic_unlocked(
        journal_path=journal_path,
        attempt_log_path=attempt_log_path,
        journal=journal,
        transaction=transaction,
        config=SimpleNamespace(),
        manifest=SimpleNamespace(),
        state_directory=tmp_path,
    )

    assert result["success"] is True
    assert result["phase"] == "completed"
    activated.assert_called_once()
    service._attest_cache_target_prebootstrap_pair.assert_called_once()
    service._attest_cache_target_pair.assert_not_called()
    smoke.assert_called_once()
    final_journal = read_cache_target_diagnostic(journal_path)
    assert final_journal.phase == "completed"
    attempt_log = read_cache_target_diagnostic_attempt_log(attempt_log_path)
    assert len(attempt_log.attempts) == 1
    assert attempt_log.attempts[0].phase == "completed"


def test_unlocked_diagnostic_stops_writers_with_compatible_pair_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """다른 모든 writer stop/start 지점과 같은 production mutation capability를
    써야 한다 — 여기서만 다른 sentinel을 쓰면 production에서 이 CLI 명령이 항상
    `assert_compose_mutation_allowed`에 막혀 동작하지 않는다."""
    service, journal_path, attempt_log_path, transaction, _activated, _smoke = (
        _install_unlocked_context(tmp_path, monkeypatch)
    )
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID, identity=_identity(), started_at_unix=1_700_000_000
    )
    write_cache_target_diagnostic(journal_path, journal)
    monkeypatch.setattr(
        service,
        "_run_cache_target_diagnostic_role",
        Mock(side_effect=lambda runtime, *_a: (_receipt(runtime.role, "source_archive"),)),
    )

    service._run_cache_target_diagnostic_unlocked(
        journal_path=journal_path,
        attempt_log_path=attempt_log_path,
        journal=journal,
        transaction=transaction,
        config=SimpleNamespace(),
        manifest=SimpleNamespace(),
        state_directory=tmp_path,
    )

    stop_call = next(
        call
        for call in service._run_frozen_recovery.call_args_list
        if call.args and call.args[0] and call.args[0][0] == "stop"
    )
    assert stop_call.kwargs["mutation_capability"] is _COMPATIBLE_PAIR_MUTATION_CAPABILITY


def test_unlocked_diagnostic_restarts_writers_and_reattests_pair_even_when_stop_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`docker compose stop`이 일부 writer만 내리고 실패를 반환해도(예: 한 writer가
    shutdown hook에서 멈춤), writer 재기동과 pair 재-attestation은 여전히 실행돼야
    한다 — 안 그러면 global lock은 풀리는데 production writer는 내려간 채로 방치된다."""
    service, journal_path, attempt_log_path, transaction, activated, _smoke = (
        _install_unlocked_context(tmp_path, monkeypatch)
    )
    monkeypatch.setattr(service, "_run_frozen_recovery", Mock(return_value={"success": False}))
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID, identity=_identity(), started_at_unix=1_700_000_000
    )
    write_cache_target_diagnostic(journal_path, journal)

    with pytest.raises(DeploymentContractError, match="writer fence stop failed"):
        service._run_cache_target_diagnostic_unlocked(
            journal_path=journal_path,
            attempt_log_path=attempt_log_path,
            journal=journal,
            transaction=transaction,
            config=SimpleNamespace(),
            manifest=SimpleNamespace(),
            state_directory=tmp_path,
        )

    activated.assert_called_once()
    service._attest_cache_target_prebootstrap_pair.assert_called_once()
    service._attest_cache_target_pair.assert_not_called()


def test_unlocked_diagnostic_stage_failure_still_restarts_writers_and_marks_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, journal_path, attempt_log_path, transaction, activated, smoke = (
        _install_unlocked_context(tmp_path, monkeypatch)
    )
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID, identity=_identity(), started_at_unix=1_700_000_000
    )
    write_cache_target_diagnostic(journal_path, journal)

    def role_receipts(
        runtime: DatabaseRuntime, *_args: object
    ) -> tuple[DiagnosticStageReceipt, ...]:
        if runtime.role == "map_application":
            return (
                _receipt(
                    runtime.role,
                    "source_archive",
                    status="failed",
                    failure_class="timeout",
                ),
            )
        return (_receipt(runtime.role, "source_archive"),)

    monkeypatch.setattr(
        service, "_run_cache_target_diagnostic_role", Mock(side_effect=role_receipts)
    )

    result = service._run_cache_target_diagnostic_unlocked(
        journal_path=journal_path,
        attempt_log_path=attempt_log_path,
        journal=journal,
        transaction=transaction,
        config=SimpleNamespace(),
        manifest=SimpleNamespace(),
        state_directory=tmp_path,
    )

    assert result["success"] is False
    assert result["phase"] == "failed"
    assert result["failure_stage"] == "source_archive"
    assert result["failure_class"] == "timeout"
    activated.assert_called_once()
    smoke.assert_not_called()
    final_journal = read_cache_target_diagnostic(journal_path)
    assert final_journal.phase == "failed"


def test_unlocked_diagnostic_reproduced_failure_aborts_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, journal_path, attempt_log_path, transaction, activated, _smoke = (
        _install_unlocked_context(tmp_path, monkeypatch)
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnostic_failure_is_reproduced",
        Mock(return_value=True),
    )
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID, identity=_identity(), started_at_unix=1_700_000_000
    )
    write_cache_target_diagnostic(journal_path, journal)

    def role_receipts(
        runtime: DatabaseRuntime, *_args: object
    ) -> tuple[DiagnosticStageReceipt, ...]:
        return (_receipt(runtime.role, "source_archive", status="failed", failure_class="timeout"),)

    monkeypatch.setattr(
        service, "_run_cache_target_diagnostic_role", Mock(side_effect=role_receipts)
    )

    result = service._run_cache_target_diagnostic_unlocked(
        journal_path=journal_path,
        attempt_log_path=attempt_log_path,
        journal=journal,
        transaction=transaction,
        config=SimpleNamespace(),
        manifest=SimpleNamespace(),
        state_directory=tmp_path,
    )

    assert result["phase"] == "aborted"
    activated.assert_called_once()


def test_diagnostic_role_stops_at_first_failure_and_still_removes_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ComposeService()
    runtime = _runtime("pinvi")
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_source_archive",
        Mock(return_value=_receipt("pinvi", "source_archive")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_source_schema_inventory",
        Mock(
            return_value=_receipt(
                "pinvi", "source_schema_inventory", status="failed", failure_class="timeout"
            )
        ),
    )
    unexpected = Mock(side_effect=AssertionError("must not run once a stage fails"))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_source_data_inventory",
        unexpected,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_scratch_create",
        unexpected,
    )
    remove_archive = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.remove_diagnostic_archive",
        remove_archive,
    )
    cleanup = Mock(side_effect=AssertionError("must not clean up scratch that was never created"))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_scratch_cleanup",
        cleanup,
    )

    receipts = service._run_cache_target_diagnostic_role(runtime, _DIAGNOSTIC_ID, tmp_path)

    assert [receipt.stage for receipt in receipts] == [
        "source_archive",
        "source_schema_inventory",
    ]
    remove_archive.assert_called_once()


def test_diagnostic_role_cleans_up_scratch_after_a_later_stage_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ComposeService()
    runtime = _runtime("map_dagster")
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_source_archive",
        Mock(return_value=_receipt("map_dagster", "source_archive")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_source_schema_inventory",
        Mock(return_value=_receipt("map_dagster", "source_schema_inventory")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_source_data_inventory",
        Mock(return_value=_receipt("map_dagster", "source_data_inventory")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_archive_structure",
        Mock(return_value=_receipt("map_dagster", "archive_structure")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_scratch_create",
        Mock(return_value=_receipt("map_dagster", "scratch_create")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_scratch_restore",
        Mock(
            return_value=_receipt(
                "map_dagster", "scratch_restore", status="failed", failure_class="restore_failed"
            )
        ),
    )
    cleanup = Mock(return_value=_receipt("map_dagster", "scratch_cleanup"))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_scratch_cleanup",
        cleanup,
    )
    remove_archive = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.remove_diagnostic_archive",
        remove_archive,
    )

    receipts = service._run_cache_target_diagnostic_role(runtime, _DIAGNOSTIC_ID, tmp_path)

    assert [receipt.stage for receipt in receipts] == [
        "source_archive",
        "source_schema_inventory",
        "source_data_inventory",
        "archive_structure",
        "scratch_create",
        "scratch_restore",
        "scratch_cleanup",
    ]
    cleanup.assert_called_once()
    remove_archive.assert_called_once()


def test_diagnostic_role_runs_all_nine_stages_on_full_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ComposeService()
    runtime = _runtime("map_application")
    stages = (
        "source_archive",
        "source_schema_inventory",
        "source_data_inventory",
        "archive_structure",
        "scratch_create",
        "scratch_restore",
        "scratch_schema_inventory",
        "scratch_data_inventory",
        "scratch_cleanup",
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_source_archive",
        Mock(return_value=_receipt("map_application", "source_archive")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_source_schema_inventory",
        Mock(return_value=_receipt("map_application", "source_schema_inventory")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_source_data_inventory",
        Mock(return_value=_receipt("map_application", "source_data_inventory")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_archive_structure",
        Mock(return_value=_receipt("map_application", "archive_structure")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_scratch_create",
        Mock(return_value=_receipt("map_application", "scratch_create")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_scratch_restore",
        Mock(return_value=_receipt("map_application", "scratch_restore")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_scratch_schema_inventory",
        Mock(return_value=_receipt("map_application", "scratch_schema_inventory")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_scratch_data_inventory",
        Mock(return_value=_receipt("map_application", "scratch_data_inventory")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.diagnose_scratch_cleanup",
        Mock(return_value=_receipt("map_application", "scratch_cleanup")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.remove_diagnostic_archive",
        Mock(),
    )

    receipts = service._run_cache_target_diagnostic_role(runtime, _DIAGNOSTIC_ID, tmp_path)

    assert tuple(receipt.stage for receipt in receipts) == stages
    assert all(receipt.role == "map_application" for receipt in receipts)
