"""v6 execution registry가 v5 source terminal을 재작성하지 않는 회귀."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from kor_travel_docker_manager import cli
from kor_travel_docker_manager.cli import build_parser
from kor_travel_docker_manager.services.runtime_execution_registry import (
    RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV,
    RuntimeExecutionRegistryError,
    block_current_execution,
    load_runtime_execution_registry,
    migrate_execution_registry,
    rebind_execution_registry,
    trusted_manager_source_revision,
    verify_runtime_execution_registry,
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


def test_registry_writer_refuses_a_symlinked_state_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    registry = migrate_execution_registry(
        pins=_pins(), manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
    )

    with pytest.raises(RuntimeExecutionRegistryError, match="directory is unsafe"):
        write_runtime_execution_registry(
            registry,
            path=linked / "private.json",
            public_path=tmp_path / "public.json",
        )


def test_execution_verify_requires_an_exact_public_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private.json"
    public = tmp_path / "public.json"
    monkeypatch.setenv("KTDM_RUNTIME_EXECUTIONS_FILE", str(private))
    monkeypatch.setenv("KTDM_RUNTIME_EXECUTIONS_PUBLIC_FILE", str(public))
    registry = migrate_execution_registry(
        pins=_pins(), manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
    )
    write_runtime_execution_registry(registry, path=private, public_path=public)

    assert verify_runtime_execution_registry()["execution_public_copy"] == "current"
    public.write_text(
        json.dumps(
            migrate_execution_registry(
                pins=_pins(), manager_source_revision=_MANAGER_B, bound_by="tester", reason="stale"
            ).to_payload()
        ),
        encoding="utf-8",
    )
    public.chmod(0o644)
    assert verify_runtime_execution_registry()["execution_public_copy"] == "stale"
    public.write_text("{", encoding="utf-8")
    public.chmod(0o644)
    assert verify_runtime_execution_registry()["execution_public_copy"] == "malformed"


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


def test_cli_legacy_terminal_migration_creates_only_the_current_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = build_parser().parse_args(
        ["pin", "migrate-execution-v6", "--reason", "release transition", "--confirm"]
    )
    pins = _pins()
    saved: list[object] = []

    monkeypatch.setattr(cli, "_running_as_root", lambda: True)
    monkeypatch.setattr(cli, "_runtime_pin_mutation_lock", lambda: nullcontext())
    monkeypatch.setattr(
        cli,
        "load_runtime_execution_registry",
        lambda: (_ for _ in ()).throw(RuntimeExecutionRegistryError("absent")),
    )
    monkeypatch.setattr(cli, "load_runtime_pin_registry", lambda: pins)
    monkeypatch.setattr(cli, "trusted_manager_source_revision", lambda: _MANAGER_B)
    monkeypatch.setattr(cli, "write_runtime_execution_registry", saved.append)

    assert cli._cmd_pin_migrate_execution(args) == 0
    migrated = saved[0]
    assert migrated.current.manager_source_revision == _MANAGER_B
    assert not migrated.is_unconditionally_blocked_current()
    assert migrated.history == (migrated.current,)
    assert not migrated.blocked_executions


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


def test_trusted_manager_revision_requires_two_root_provenance_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / ".ktdm-source-revision").write_text(_MANAGER_A, encoding="utf-8")
    (install / ".ktdm-release-manifest.json").write_text(
        '{"manager_source_revision":"' + _MANAGER_A + '"}', encoding="utf-8"
    )

    monkeypatch.setattr("os.geteuid", lambda: 0, raising=False)
    # tmp_path는 root가 소유하지 않으므로 public function은 fail-close한다.
    with pytest.raises(RuntimeExecutionRegistryError, match="install root is unsafe"):
        trusted_manager_source_revision(install_root=install)


def test_cli_exposes_generic_execution_migration_and_rebind_commands() -> None:
    parser = build_parser()
    migrated = parser.parse_args(
        ["pin", "migrate-execution-v6", "--reason", "migration", "--confirm"]
    )
    rebound = parser.parse_args(
        [
            "pin",
            "rebind-execution",
            "--expected-manager-revision",
            _MANAGER_B,
            "--reason",
            "implementation fix",
            "--confirm",
        ]
    )
    shown = parser.parse_args(["pin", "show-execution", "--json"])
    blocked = parser.parse_args(
        ["pin", "block-execution", "--reason", "safe receipt unavailable", "--confirm"]
    )

    assert migrated.pin_action == "migrate-execution-v6"
    assert rebound.expected_manager_revision == _MANAGER_B
    assert shown.pin_action == "show-execution"
    assert blocked.pin_action == "block-execution"


def test_launcher_fallback_block_execution_allows_only_the_inherited_terminal_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = build_parser().parse_args(
        ["pin", "block-execution", "--reason", "safe receipt unavailable", "--confirm"]
    )
    registry = migrate_execution_registry(
        pins=_pins(), manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
    )
    seen: dict[str, object] = {}

    def lock(*, allow_inherited_terminal_block: bool = False):
        seen["allow"] = allow_inherited_terminal_block
        return nullcontext()

    monkeypatch.setattr(cli, "_running_as_root", lambda: True)
    monkeypatch.setattr(cli, "_runtime_pin_mutation_lock", lock)
    monkeypatch.setattr(cli, "load_runtime_pin_registry", _pins)
    monkeypatch.setattr(cli, "load_runtime_execution_registry", lambda: registry)
    monkeypatch.setattr(cli, "trusted_manager_source_revision", lambda: _MANAGER_A)
    monkeypatch.setattr(
        cli, "block_current_execution", lambda **_kwargs: block_current_execution(
            registry=registry, reason="safe receipt unavailable"
        )
    )
    monkeypatch.setattr(cli, "write_runtime_execution_registry", lambda _registry: None)

    assert cli._cmd_pin_block_execution(args) == 0
    assert seen["allow"] is True
