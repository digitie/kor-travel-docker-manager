"""v6 execution registry가 v5 source terminal을 재작성하지 않는 회귀."""

from __future__ import annotations

from pathlib import Path

import pytest

from kor_travel_docker_manager.services.runtime_execution_registry import (
    RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV,
    RuntimeExecutionRegistryError,
    block_current_execution,
    load_runtime_execution_registry,
    migrate_execution_registry,
    rebind_execution_registry,
    write_runtime_execution_registry,
)
from kor_travel_docker_manager.services.runtime_pin_registry import build_registry

_MAP = "a" * 40
_PINVI = "b" * 40
_MANAGER_A = "c" * 40
_MANAGER_B = "d" * 40


@pytest.fixture(autouse=True)
def _allow_drvfs_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV, "1")


def _pins():
    return build_registry(
        release_version=5,
        map_revision=_MAP,
        pinvi_revision=_PINVI,
        rotated_by="tester",
        reason="seed",
    )


def test_migration_creates_manager_aware_execution_without_changing_source_pinset(
    tmp_path: Path,
) -> None:
    pins = _pins()
    registry = migrate_execution_registry(
        pins=pins, manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
    )
    write_runtime_execution_registry(
        registry, path=tmp_path / "private.json", public_path=tmp_path / "public.json"
    )

    loaded = load_runtime_execution_registry(path=tmp_path / "private.json")
    assert loaded.current.source_pinset_sha256 == pins.pinset_sha256
    assert loaded.current.manager_source_revision == _MANAGER_A
    assert loaded.current.execution_identity_sha256 != pins.pinset_sha256


def test_terminal_execution_can_rebind_only_for_new_manager_revision() -> None:
    pins = _pins()
    registry = migrate_execution_registry(
        pins=pins, manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
    )
    terminal = block_current_execution(registry=registry, reason="terminal")
    rebound = rebind_execution_registry(
        registry=terminal,
        pins=pins,
        manager_source_revision=_MANAGER_B,
        bound_by="tester",
        reason="Manager fix",
    )

    assert rebound.current.source_pinset_sha256 == terminal.current.source_pinset_sha256
    assert rebound.current.execution_identity_sha256 != terminal.current.execution_identity_sha256
    assert len(rebound.blocked_executions) == 1
    assert not rebound.is_unconditionally_blocked_current()


def test_rebind_refuses_nonterminal_or_same_manager_revision() -> None:
    pins = _pins()
    registry = migrate_execution_registry(
        pins=pins, manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
    )
    with pytest.raises(RuntimeExecutionRegistryError, match="not terminal"):
        rebind_execution_registry(
            registry=registry,
            pins=pins,
            manager_source_revision=_MANAGER_B,
            bound_by="tester",
            reason="wrong",
        )
    with pytest.raises(RuntimeExecutionRegistryError, match="did not change"):
        rebind_execution_registry(
            registry=block_current_execution(registry=registry, reason="terminal"),
            pins=pins,
            manager_source_revision=_MANAGER_A,
            bound_by="tester",
            reason="wrong",
        )
