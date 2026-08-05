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
    LegacyDiagnosticRetirementReceipt,
    legacy_diagnostic_retirement_receipt_path,
    prepare_cache_target_diagnostic,
    read_legacy_diagnostic_retirement_receipt,
    retire_legacy_pre_stop_cache_target_diagnostic,
    write_cache_target_diagnostic,
    write_legacy_diagnostic_retirement_receipt,
)
from kor_travel_docker_manager.services.compose_service import ComposeService

_DIAGNOSTIC_ID = "8a3e6b2c-8f1e-4c8b-9c3d-0f1a2b3c4d5e"


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


def _legacy_v1_document(*, phase: str = "writers_fencing") -> dict[str, object]:
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID,
        identity=_identity(),
        started_at_unix=1_700_000_000,
    )
    document = asdict(journal)
    for field in (
        "writer_drain_lease_id",
        "writer_drain_receipt_sha256",
        "writer_drain_restore_receipt_sha256",
    ):
        del document[field]
    document["version"] = 1
    document["phase"] = phase
    return document


def _write_legacy_v1(path: Path, *, phase: str = "writers_fencing") -> bytes:
    return _write_legacy_v1_document(path, _legacy_v1_document(phase=phase))


def _write_legacy_v1_document(path: Path, document: dict[str, object]) -> bytes:
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


def test_retirement_writes_receipt_then_removes_only_eligible_legacy_journal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    payload = _write_legacy_v1(path)

    receipt = retire_legacy_pre_stop_cache_target_diagnostic(
        path,
        retired_at_unix=1_700_000_100,
    )

    assert receipt == LegacyDiagnosticRetirementReceipt(
        version=1,
        retired_journal_sha256=hashlib.sha256(payload).hexdigest(),
        retired_phase="writers_fencing",
        retired_at_unix=1_700_000_100,
    )
    assert not path.exists()
    receipt_path = legacy_diagnostic_retirement_receipt_path(path)
    assert read_legacy_diagnostic_retirement_receipt(receipt_path) == receipt


def test_retirement_finishes_after_receipt_first_crash_state(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    payload = _write_legacy_v1(path, phase="prepared")
    receipt = LegacyDiagnosticRetirementReceipt(
        version=1,
        retired_journal_sha256=hashlib.sha256(payload).hexdigest(),
        retired_phase="prepared",
        retired_at_unix=1_700_000_100,
    )
    write_legacy_diagnostic_retirement_receipt(
        legacy_diagnostic_retirement_receipt_path(path),
        receipt,
    )

    assert retire_legacy_pre_stop_cache_target_diagnostic(
        path,
        retired_at_unix=1_700_000_200,
    ) == receipt
    assert not path.exists()


@pytest.mark.parametrize("phase", ["writers_drained", "completed", "failed"])
def test_retirement_rejects_legacy_states_that_are_not_pre_stop(
    tmp_path: Path,
    phase: str,
) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    _write_legacy_v1(path, phase=phase)

    with pytest.raises(DeploymentContractError, match="eligible pre-stop"):
        retire_legacy_pre_stop_cache_target_diagnostic(path, retired_at_unix=1_700_000_100)

    assert path.exists()


def test_retirement_rejects_v2_journal_without_modifying_it(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    path.parent.mkdir(mode=0o700)
    path.parent.chmod(0o700)
    journal = prepare_cache_target_diagnostic(
        diagnostic_id=_DIAGNOSTIC_ID,
        identity=_identity(),
        started_at_unix=1_700_000_000,
    )
    write_cache_target_diagnostic(path, journal)
    before = path.read_bytes()

    with pytest.raises(DeploymentContractError, match="eligible pre-stop"):
        retire_legacy_pre_stop_cache_target_diagnostic(path, retired_at_unix=1_700_000_100)

    assert path.read_bytes() == before


@pytest.mark.parametrize("malformation", ["identity", "external_event_count", "receipt_list"])
def test_retirement_rejects_malformed_legacy_v1_journal(
    tmp_path: Path,
    malformation: str,
) -> None:
    path = tmp_path / malformation / "cache-target-diagnostic-v1.json"
    document = _legacy_v1_document()
    if malformation == "identity":
        document["identity"] = {"foreign": "value"}
    elif malformation == "external_event_count":
        document["external_event_count"] = False
    else:
        document["map_application_receipts"] = ""
    before = _write_legacy_v1_document(path, document)

    with pytest.raises(DeploymentContractError, match="eligible pre-stop"):
        retire_legacy_pre_stop_cache_target_diagnostic(path, retired_at_unix=1_700_000_100)

    assert path.read_bytes() == before


def test_retirement_rejects_source_change_after_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    _write_legacy_v1(path)
    original_write = diagnostics_module.write_legacy_diagnostic_retirement_receipt

    def write_then_change(
        receipt_path: Path,
        receipt: LegacyDiagnosticRetirementReceipt,
    ) -> str:
        result = original_write(receipt_path, receipt)
        _write_legacy_v1(path, phase="prepared")
        return result

    monkeypatch.setattr(
        diagnostics_module,
        "write_legacy_diagnostic_retirement_receipt",
        write_then_change,
    )

    with pytest.raises(DeploymentContractError, match="changed before retirement"):
        retire_legacy_pre_stop_cache_target_diagnostic(path, retired_at_unix=1_700_000_100)

    assert path.exists()


def test_retirement_replays_receipt_after_unlink_before_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    _write_legacy_v1(path)
    original_fsync = diagnostics_module._fsync_state_directory

    def fail_after_unlink(_path: Path) -> None:
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(diagnostics_module, "_fsync_state_directory", fail_after_unlink)
    with pytest.raises(DeploymentContractError, match="retirement failed"):
        retire_legacy_pre_stop_cache_target_diagnostic(path, retired_at_unix=1_700_000_100)

    assert not path.exists()
    fsync_calls: list[Path] = []

    def record_fsync(directory: Path) -> None:
        fsync_calls.append(directory)
        original_fsync(directory)

    monkeypatch.setattr(diagnostics_module, "_fsync_state_directory", record_fsync)
    receipt = retire_legacy_pre_stop_cache_target_diagnostic(
        path,
        retired_at_unix=1_700_000_200,
    )
    assert receipt.retired_at_unix == 1_700_000_100
    assert fsync_calls == [path.parent]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", True),
        ("retired_at_unix", 1.0),
        ("retired_phase", []),
    ],
)
def test_retirement_rejects_malformed_receipt_when_source_is_already_absent(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    source_path = tmp_path / "state" / "cache-target-diagnostic-v1.json"
    receipt_path = legacy_diagnostic_retirement_receipt_path(source_path)
    receipt_path.parent.mkdir(mode=0o700, parents=True)
    receipt_path.parent.chmod(0o700)
    document: dict[str, object] = {
        "version": 1,
        "retired_journal_sha256": "a" * 64,
        "retired_phase": "prepared",
        "retired_at_unix": 1_700_000_100,
    }
    document[field] = value
    receipt_path.write_bytes(
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    )
    receipt_path.chmod(0o600)

    with pytest.raises(DeploymentContractError, match="legacy diagnostic retirement"):
        retire_legacy_pre_stop_cache_target_diagnostic(
            source_path,
            retired_at_unix=1_700_000_200,
        )


def test_compose_service_retirement_uses_common_gate_and_changes_only_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path = tmp_path / "cache-target-diagnostic-v1.json"
    receipt = LegacyDiagnosticRetirementReceipt(
        version=1,
        retired_journal_sha256="a" * 64,
        retired_phase="writers_fencing",
        retired_at_unix=1_700_000_100,
    )
    transaction = SimpleNamespace(
        environment=SimpleNamespace(
            effective={"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
            env_path=str(tmp_path / ".env"),
        )
    )
    called: list[object] = []

    @contextmanager
    def lock():
        yield SimpleNamespace(lock_path="/lock")

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lock,
    )
    monkeypatch.setattr(
        ComposeService,
        "_capture_transaction_unlocked",
        lambda _self: (transaction, None),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_assert_transaction_matches_c6c_lock",
        lambda _transaction, _lock: called.append("lock"),
    )
    monkeypatch.setattr(
        compose_service_module,
        "assert_manager_mutation_allowed",
        lambda *, environment: called.append(environment),
    )
    monkeypatch.setattr(
        compose_service_module,
        "load_c6c_deployment_config_from_environment",
        lambda _environment: SimpleNamespace(production=True, cache_target=object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_frozen_canonical_env_owner",
        lambda _environment: {},
    )
    monkeypatch.setattr(
        compose_service_module,
        "assert_pinned_deployment_input_allows_pair_mutation",
        lambda **_kwargs: called.append("pinned_input"),
    )
    monkeypatch.setattr(
        compose_service_module,
        "cache_target_diagnostic_journal_path",
        lambda _environment: journal_path,
    )
    monkeypatch.setattr(
        compose_service_module,
        "retire_legacy_pre_stop_cache_target_diagnostic",
        lambda path, *, retired_at_unix: (
            called.extend((path, retired_at_unix)) or receipt
        ),
    )

    result = ComposeService().retire_legacy_pre_stop_cache_target_diagnostic()

    assert result["retired_phase"] == "writers_fencing"
    assert result["retired_journal_sha256"] == "a" * 64
    assert called[0] == "lock"
    assert transaction.environment.effective in called
    assert journal_path in called
