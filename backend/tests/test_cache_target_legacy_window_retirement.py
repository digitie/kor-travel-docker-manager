from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

import kor_travel_docker_manager.services.cache_target_window as window_module
import kor_travel_docker_manager.services.compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_window import (
    LegacyWindowRetirementReceipt,
    legacy_window_retirement_receipt_path,
    prepare_cache_target_window,
    read_legacy_window_retirement_receipt,
    retire_legacy_terminal_cache_target_window,
    write_legacy_window_retirement_receipt,
)
from kor_travel_docker_manager.services.compose_service import ComposeService

_TRANSACTION_ID = "7f4e2d1c-0b9a-48d7-8c6b-5a4f3e2d1c0b"
_CUTOVER_ID = "1c2d3e4f-5a6b-47c8-9d0e-1f2a3b4c5d6e"


def _legacy_v1_document(*, phase: str = "rolled_back") -> dict[str, object]:
    journal = prepare_cache_target_window(
        transaction_id=_TRANSACTION_ID,
        cutover_id=_CUTOVER_ID,
        expected_restore_epoch=1,
        reason="legacy rollback",
        environment_sha256="1" * 64,
        compose_sha256="2" * 64,
        resolved_compose_sha256="3" * 64,
        old_manifest_sha256="4" * 64,
    )
    document = asdict(journal)
    for field in (
        "writer_drain_lease_id",
        "writer_drain_receipt_sha256",
        "writer_drain_restore_receipt_sha256",
        "failure_stage",
        "failure_class",
    ):
        del document[field]
    document["version"] = 1
    document["phase"] = phase
    return document


def _write_legacy_v1(path: Path, *, phase: str = "rolled_back") -> bytes:
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


def test_retirement_writes_receipt_then_removes_only_eligible_legacy_window(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "cache-target-window-v1.json"
    payload = _write_legacy_v1(path)

    receipt = retire_legacy_terminal_cache_target_window(
        path,
        retired_at_unix=1_700_000_100,
    )

    assert receipt == LegacyWindowRetirementReceipt(
        version=1,
        retired_journal_sha256=hashlib.sha256(payload).hexdigest(),
        retired_phase="rolled_back",
        retired_at_unix=1_700_000_100,
    )
    assert not path.exists()
    assert read_legacy_window_retirement_receipt(
        legacy_window_retirement_receipt_path(path)
    ) == receipt


def test_retirement_finishes_after_receipt_first_crash_state(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-window-v1.json"
    payload = _write_legacy_v1(path)
    receipt = LegacyWindowRetirementReceipt(
        version=1,
        retired_journal_sha256=hashlib.sha256(payload).hexdigest(),
        retired_phase="rolled_back",
        retired_at_unix=1_700_000_100,
    )
    write_legacy_window_retirement_receipt(
        legacy_window_retirement_receipt_path(path),
        receipt,
    )

    assert retire_legacy_terminal_cache_target_window(
        path,
        retired_at_unix=1_700_000_200,
    ) == receipt
    assert not path.exists()


@pytest.mark.parametrize("phase", ["prepared", "runtime_activated", "forward_committed"])
def test_retirement_rejects_legacy_states_that_are_not_terminal_rolled_back(
    tmp_path: Path,
    phase: str,
) -> None:
    path = tmp_path / "state" / "cache-target-window-v1.json"
    _write_legacy_v1(path, phase=phase)

    with pytest.raises(DeploymentContractError, match="not eligible"):
        retire_legacy_terminal_cache_target_window(path, retired_at_unix=1_700_000_100)

    assert path.exists()


def test_retirement_rejects_v2_journal_without_modifying_it(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-window-v1.json"
    document = _legacy_v1_document()
    document["version"] = 2
    before = _write_legacy_v1_document(path, document)

    with pytest.raises(DeploymentContractError, match="not eligible"):
        retire_legacy_terminal_cache_target_window(path, retired_at_unix=1_700_000_100)

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_restore_epoch", False),
        ("expected_restore_epoch", True),
        ("forward_boundary", "committed"),
        ("last_map_receipt", {"foreign": "value"}),
    ],
)
def test_retirement_rejects_malformed_legacy_window(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path = tmp_path / field / "cache-target-window-v1.json"
    document = _legacy_v1_document()
    document[field] = value
    before = _write_legacy_v1_document(path, document)

    with pytest.raises(DeploymentContractError, match="not eligible"):
        retire_legacy_terminal_cache_target_window(path, retired_at_unix=1_700_000_100)

    assert path.read_bytes() == before


def test_retirement_rejects_source_change_after_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state" / "cache-target-window-v1.json"
    _write_legacy_v1(path)
    original_write = window_module.write_legacy_window_retirement_receipt

    def write_then_change(
        receipt_path: Path,
        receipt: LegacyWindowRetirementReceipt,
    ) -> str:
        result = original_write(receipt_path, receipt)
        changed = _legacy_v1_document()
        changed["reason_sha256"] = "f" * 64
        _write_legacy_v1_document(path, changed)
        return result

    monkeypatch.setattr(
        window_module,
        "write_legacy_window_retirement_receipt",
        write_then_change,
    )

    with pytest.raises(DeploymentContractError, match="changed before retirement"):
        retire_legacy_terminal_cache_target_window(path, retired_at_unix=1_700_000_100)

    assert path.exists()


def test_retirement_replays_receipt_after_unlink_before_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state" / "cache-target-window-v1.json"
    _write_legacy_v1(path)
    original_fsync = window_module._fsync_state_directory

    monkeypatch.setattr(
        window_module,
        "_fsync_state_directory",
        lambda _path: (_ for _ in ()).throw(OSError("simulated directory fsync failure")),
    )
    with pytest.raises(DeploymentContractError, match="retirement failed"):
        retire_legacy_terminal_cache_target_window(path, retired_at_unix=1_700_000_100)

    assert not path.exists()
    monkeypatch.setattr(window_module, "_fsync_state_directory", original_fsync)
    receipt = retire_legacy_terminal_cache_target_window(
        path,
        retired_at_unix=1_700_000_200,
    )
    assert receipt.retired_at_unix == 1_700_000_100


def test_compose_service_retirement_uses_narrow_legacy_window_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path = tmp_path / "cache-target-window-v1.json"
    receipt = LegacyWindowRetirementReceipt(
        version=1,
        retired_journal_sha256="a" * 64,
        retired_phase="rolled_back",
        retired_at_unix=1_700_000_100,
    )
    environment = SimpleNamespace(
        effective={"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
        compose_path=str(tmp_path / "compose.yml"),
    )
    source_identity = object()
    called: list[object] = []

    @contextmanager
    def lock():
        yield SimpleNamespace(lock_path="/lock")

    monkeypatch.setattr(compose_service_module, "c6c_deployment_lock_from_environment", lock)

    def unexpected_candidate_materialization(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("legacy retirement must not materialize a compose candidate")

    monkeypatch.setattr(
        ComposeService,
        "_capture_transaction_unlocked",
        unexpected_candidate_materialization,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: called.append("environment") or environment,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_frozen_compose_source",
        lambda _environment: (b"services: {}\n", source_identity),
    )
    monkeypatch.setattr(
        compose_service_module,
        "assert_environment_snapshot_matches_c6c_lock",
        lambda _environment, _lock: called.append("lock"),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_revalidate_compose_environment_snapshot",
        lambda _environment: called.append("env-revalidated"),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_revalidate_frozen_compose_source",
        lambda _environment, *, source_bytes, source_identity: called.append(
            (source_bytes, source_identity)
        ),
    )
    monkeypatch.setattr(
        compose_service_module,
        "assert_legacy_window_retirement_allowed",
        lambda *, environment: called.append(environment) or "production",
    )
    monkeypatch.setattr(
        compose_service_module,
        "load_c6c_deployment_config_from_environment",
        lambda _environment: SimpleNamespace(production=True, cache_target=object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "cache_target_window_journal_path",
        lambda _environment: journal_path,
    )
    monkeypatch.setattr(
        compose_service_module,
        "retire_legacy_terminal_cache_target_window_journal",
        lambda path, *, retired_at_unix: called.extend((path, retired_at_unix)) or receipt,
    )

    result = ComposeService().retire_legacy_terminal_cache_target_window()

    assert result["retired_phase"] == "rolled_back"
    assert result["retired_journal_sha256"] == "a" * 64
    assert called[:3] == ["environment", "lock", environment.effective]
    assert "env-revalidated" in called
    assert (b"services: {}\n", source_identity) in called
    assert journal_path in called


def test_frozen_compose_source_rejects_change_before_legacy_retirement(
    tmp_path: Path,
) -> None:
    compose_path = tmp_path / "compose.yml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    environment = SimpleNamespace(compose_path=str(compose_path))

    source_bytes, source_identity = compose_service_module._capture_frozen_compose_source(
        environment
    )
    compose_path.write_text("services:\n  changed: {}\n", encoding="utf-8")

    with pytest.raises(DeploymentContractError, match="compose source"):
        compose_service_module._revalidate_frozen_compose_source(
            environment,
            source_bytes=source_bytes,
            source_identity=source_identity,
        )
