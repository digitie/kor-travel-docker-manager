from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_diagnostics import (
    CacheTargetDiagnosticIdentity,
    DiagnosticStageReceipt,
    prepare_cache_target_diagnostic,
    transition_cache_target_diagnostic,
    write_cache_target_diagnostic,
)
from kor_travel_docker_manager.services.compose_service import ComposeService

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


def _receipt(role: str) -> DiagnosticStageReceipt:
    return DiagnosticStageReceipt(
        role=role,  # type: ignore[arg-type]
        stage="source_archive",  # type: ignore[arg-type]
        status="succeeded",  # type: ignore[arg-type]
        failure_class=None,
        elapsed_ms=100,
        archive_sha256="a" * 64,
        schema_inventory_sha256=None,
        data_inventory_sha256=None,
        scratch_identity_sha256=None,
    )


def _completed_diagnostic(
    *, identity: CacheTargetDiagnosticIdentity, completed_at_unix: int
) -> object:
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID,
        identity=identity,
        started_at_unix=completed_at_unix - 100,
    )
    journal = transition_cache_target_diagnostic(journal, "writers_fencing")
    journal = transition_cache_target_diagnostic(journal, "writers_draining")
    journal = transition_cache_target_diagnostic(
        journal,
        "writers_drained",
        writer_drain_lease_id="99999999-1111-2222-3333-444444444444",
        writer_drain_receipt_sha256="e" * 64,
    )
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
        journal, "runtime_smoke_checked", runtime_smoke_sha256="e" * 64
    )
    return transition_cache_target_diagnostic(
        journal,
        "completed",
        completed_at_unix=completed_at_unix,
        writer_drain_restore_receipt_sha256="f" * 64,
    )


def _install_gate_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    now_unix: int = 1_700_000_000,
) -> tuple[ComposeService, Path]:
    service = ComposeService()
    journal_path = tmp_path / "cache-target-diagnostic-v1.json"
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.cache_target_diagnostic_journal_path",
        Mock(return_value=journal_path),
    )
    monkeypatch.setattr(service, "_cache_target_diagnostic_identity", Mock(return_value=_identity()))
    monkeypatch.setattr("kor_travel_docker_manager.services.compose_service.time.time", lambda: now_unix)
    return service, journal_path


def test_gate_rejects_missing_diagnostic_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _journal_path = _install_gate_context(tmp_path, monkeypatch)

    with pytest.raises(DeploymentContractError, match="requires a completed diagnostic receipt"):
        service._require_fresh_cache_target_diagnostic(
            transaction=SimpleNamespace(
                environment=SimpleNamespace(effective={}),
                compose_source_bytes=b"",
                resolved_document_hash="0" * 64,
            ),
            config=SimpleNamespace(),
            manifest=SimpleNamespace(),
        )


def test_gate_rejects_noncompleted_diagnostic_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, journal_path = _install_gate_context(tmp_path, monkeypatch)
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID,
        identity=_identity(),
        started_at_unix=1_699_999_000,
    )
    write_cache_target_diagnostic(journal_path, journal)

    with pytest.raises(DeploymentContractError, match="requires a completed diagnostic receipt"):
        service._require_fresh_cache_target_diagnostic(
            transaction=SimpleNamespace(
                environment=SimpleNamespace(effective={}),
                compose_source_bytes=b"",
                resolved_document_hash="0" * 64,
            ),
            config=SimpleNamespace(),
            manifest=SimpleNamespace(),
        )


def test_gate_rejects_stale_diagnostic_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now_unix = 1_700_000_000
    service, journal_path = _install_gate_context(tmp_path, monkeypatch, now_unix=now_unix)
    completed = _completed_diagnostic(
        identity=_identity(), completed_at_unix=now_unix - 1_801
    )
    write_cache_target_diagnostic(journal_path, completed)

    with pytest.raises(DeploymentContractError, match="requires a fresh diagnostic receipt"):
        service._require_fresh_cache_target_diagnostic(
            transaction=SimpleNamespace(
                environment=SimpleNamespace(effective={}),
                compose_source_bytes=b"",
                resolved_document_hash="0" * 64,
            ),
            config=SimpleNamespace(),
            manifest=SimpleNamespace(),
        )


def test_gate_rejects_diagnostic_receipt_with_mismatched_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now_unix = 1_700_000_000
    service, journal_path = _install_gate_context(tmp_path, monkeypatch, now_unix=now_unix)
    completed = _completed_diagnostic(
        identity=_identity(active_pair_sha256="9" * 64), completed_at_unix=now_unix - 10
    )
    write_cache_target_diagnostic(journal_path, completed)

    with pytest.raises(DeploymentContractError, match="requires a fresh diagnostic receipt"):
        service._require_fresh_cache_target_diagnostic(
            transaction=SimpleNamespace(
                environment=SimpleNamespace(effective={}),
                compose_source_bytes=b"",
                resolved_document_hash="0" * 64,
            ),
            config=SimpleNamespace(),
            manifest=SimpleNamespace(),
        )


def test_gate_accepts_fresh_matching_completed_diagnostic_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now_unix = 1_700_000_000
    service, journal_path = _install_gate_context(tmp_path, monkeypatch, now_unix=now_unix)
    completed = _completed_diagnostic(identity=_identity(), completed_at_unix=now_unix - 10)
    write_cache_target_diagnostic(journal_path, completed)

    service._require_fresh_cache_target_diagnostic(
        transaction=SimpleNamespace(
            environment=SimpleNamespace(effective={}),
            compose_source_bytes=b"",
            resolved_document_hash="0" * 64,
        ),
        config=SimpleNamespace(),
        manifest=SimpleNamespace(),
    )
