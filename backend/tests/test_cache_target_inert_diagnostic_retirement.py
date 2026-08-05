from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

import kor_travel_docker_manager.services.cache_target_diagnostics as diagnostics_module
import kor_travel_docker_manager.services.compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_diagnostics import (
    CacheTargetDiagnosticIdentity,
    InertDiagnosticRetirementReceipt,
    inert_diagnostic_retirement_receipt_path,
    prepare_cache_target_diagnostic,
    read_inert_diagnostic_retirement_receipt,
    retire_inert_cache_target_diagnostic,
    write_inert_diagnostic_retirement_receipt,
)
from kor_travel_docker_manager.services.compose_service import ComposeService

_DIAGNOSTIC_ID = "7a3e6b2c-8f1e-4c8b-9c3d-0f1a2b3c4d5e"


def _identity() -> CacheTargetDiagnosticIdentity:
    return CacheTargetDiagnosticIdentity(
        manager_release_sha256="1" * 64,
        pg_dump_major_version=16,
        pg_restore_major_version=16,
        active_pair_sha256="2" * 64,
        rollback_pair_sha256="3" * 64,
        raw_compose_sha256="4" * 64,
        resolved_compose_sha256="5" * 64,
        role_binding_sha256="6" * 64,
        writer_registry_sha256="7" * 64,
        smoke_contract_sha256="8" * 64,
    )


def _inert_v2_document(*, phase: str = "writers_fencing") -> dict[str, object]:
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID,
        identity=_identity(),
        started_at_unix=1_700_000_000,
    )
    document = asdict(journal)
    document["phase"] = phase
    return document


def _write_v2_document(path: Path, document: dict[str, object]) -> bytes:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    path.write_bytes(payload)
    path.chmod(0o600)
    return payload


def _write_inert_v2(path: Path, *, phase: str = "writers_fencing") -> bytes:
    return _write_v2_document(path, _inert_v2_document(phase=phase))


def test_retirement_writes_separate_receipt_then_removes_inert_v2_journal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    payload = _write_inert_v2(path)
    # F1C companion이 이미 있어도 F1H namespace와 replay를 공유하지 않는다.
    legacy_path = path.with_name("cache-target-diagnostic-retirement-v1.json")
    legacy_path.write_bytes(b"{}")
    legacy_path.chmod(0o600)

    receipt = retire_inert_cache_target_diagnostic(
        path,
        retired_at_unix=1_700_000_100,
    )

    assert receipt == InertDiagnosticRetirementReceipt(
        version=1,
        retired_diagnostic_version=2,
        retired_journal_sha256=hashlib.sha256(payload).hexdigest(),
        retired_phase="writers_fencing",
        retired_at_unix=1_700_000_100,
    )
    assert not path.exists()
    assert read_inert_diagnostic_retirement_receipt(
        inert_diagnostic_retirement_receipt_path(path)
    ) == receipt


def test_retirement_finishes_after_receipt_first_crash_state(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    payload = _write_inert_v2(path, phase="prepared")
    receipt = InertDiagnosticRetirementReceipt(
        version=1,
        retired_diagnostic_version=2,
        retired_journal_sha256=hashlib.sha256(payload).hexdigest(),
        retired_phase="prepared",
        retired_at_unix=1_700_000_100,
    )
    write_inert_diagnostic_retirement_receipt(
        inert_diagnostic_retirement_receipt_path(path),
        receipt,
    )

    assert retire_inert_cache_target_diagnostic(
        path,
        retired_at_unix=1_700_000_200,
    ) == receipt
    assert not path.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("phase", "writers_draining"),
        ("writer_drain_lease_id", "7f4e2d1c-0b9a-48d7-8c6b-5a4f3e2d1c0b"),
        ("writer_fence_sha256", "a" * 64),
        ("map_application_receipts", [{"foreign": "receipt"}]),
        ("runtime_smoke_sha256", "b" * 64),
        ("failure_class", "timeout"),
        ("external_event_count", 1),
        ("version", 2.0),
        ("started_at_unix", True),
    ],
)
def test_retirement_rejects_non_inert_or_malformed_v2_journal(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path = tmp_path / field / "cache-target-diagnostic-v1.json"
    document = _inert_v2_document()
    document[field] = value
    before = _write_v2_document(path, document)

    with pytest.raises(DeploymentContractError, match="diagnostic"):
        retire_inert_cache_target_diagnostic(path, retired_at_unix=1_700_000_100)

    assert path.read_bytes() == before


def test_retirement_rejects_v1_source_and_legacy_receipt_only_replay(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    document = _inert_v2_document()
    document["version"] = 1
    _write_v2_document(path, document)

    with pytest.raises(DeploymentContractError, match="diagnostic"):
        retire_inert_cache_target_diagnostic(path, retired_at_unix=1_700_000_100)

    path.unlink()
    legacy_path = path.with_name("cache-target-diagnostic-retirement-v1.json")
    legacy_path.write_bytes(b"{}")
    legacy_path.chmod(0o600)
    with pytest.raises(DeploymentContractError, match="unavailable"):
        retire_inert_cache_target_diagnostic(path, retired_at_unix=1_700_000_200)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", True),
        ("retired_diagnostic_version", 2.0),
        ("retired_phase", []),
        ("retired_at_unix", True),
    ],
)
def test_retirement_rejects_malformed_inert_receipt_when_source_is_absent(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    source_path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    receipt_path = inert_diagnostic_retirement_receipt_path(source_path)
    receipt_path.parent.mkdir(mode=0o700, parents=True)
    receipt_path.parent.chmod(0o700)
    document: dict[str, object] = {
        "version": 1,
        "retired_diagnostic_version": 2,
        "retired_journal_sha256": "a" * 64,
        "retired_phase": "prepared",
        "retired_at_unix": 1_700_000_100,
    }
    document[field] = value
    receipt_path.write_bytes(
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    )
    receipt_path.chmod(0o600)

    with pytest.raises(DeploymentContractError, match="inert diagnostic retirement"):
        retire_inert_cache_target_diagnostic(
            source_path,
            retired_at_unix=1_700_000_200,
        )


def test_retirement_rejects_source_change_after_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    _write_inert_v2(path)
    original_write = diagnostics_module.write_inert_diagnostic_retirement_receipt

    def write_then_change(
        receipt_path: Path,
        receipt: InertDiagnosticRetirementReceipt,
    ) -> str:
        result = original_write(receipt_path, receipt)
        _write_inert_v2(path, phase="prepared")
        return result

    monkeypatch.setattr(
        diagnostics_module,
        "write_inert_diagnostic_retirement_receipt",
        write_then_change,
    )

    with pytest.raises(DeploymentContractError, match="changed before retirement"):
        retire_inert_cache_target_diagnostic(path, retired_at_unix=1_700_000_100)

    assert path.exists()


def test_compose_service_retirement_uses_inert_state_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path = tmp_path / "cache-target-diagnostic-v1.json"
    environment = SimpleNamespace(effective={"KTDM_DEPLOYMENT_ENVIRONMENT": "production"})
    receipt = InertDiagnosticRetirementReceipt(
        version=1,
        retired_diagnostic_version=2,
        retired_journal_sha256="a" * 64,
        retired_phase="writers_fencing",
        retired_at_unix=1_700_000_100,
    )
    called: list[object] = []

    @contextmanager
    def lock():
        yield SimpleNamespace(lock_path="/lock")

    monkeypatch.setattr(compose_service_module, "c6c_deployment_lock_from_environment", lock)
    monkeypatch.setattr(
        compose_service_module,
        "_prepare_inert_cache_target_state_retirement",
        lambda lock_snapshot: called.append(lock_snapshot.lock_path) or environment,
    )
    monkeypatch.setattr(
        compose_service_module,
        "cache_target_diagnostic_journal_path",
        lambda _environment: journal_path,
    )
    monkeypatch.setattr(
        compose_service_module,
        "retire_inert_cache_target_diagnostic_journal",
        lambda path, *, retired_at_unix: called.extend((path, retired_at_unix)) or receipt,
    )

    result = ComposeService().retire_inert_cache_target_diagnostic()

    assert result["retired_diagnostic_version"] == 2
    assert result["retired_phase"] == "writers_fencing"
    assert called[0] == "/lock"
    assert journal_path in called
