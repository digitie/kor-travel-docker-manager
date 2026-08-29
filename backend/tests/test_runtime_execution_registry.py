"""v6 execution registry가 v5 source terminal을 재작성하지 않는 회귀."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from kor_travel_docker_manager import cli
from kor_travel_docker_manager.cli import build_parser
from kor_travel_docker_manager.services import runtime_execution_registry as execution_registry
from kor_travel_docker_manager.services import runtime_pair_rotation as pair_rotation
from kor_travel_docker_manager.services.runtime_execution_registry import (
    RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV,
    RuntimeExecutionRegistryError,
    block_current_execution,
    load_runtime_execution_registry,
    migrate_execution_registry,
    rebind_execution_registry,
    rotate_execution_source_binding,
    trusted_manager_source_revision,
    verify_runtime_execution_registry,
    write_runtime_execution_registry,
)
from kor_travel_docker_manager.services.runtime_pair_rotation import (
    RuntimePairRotationError,
    load_pending_runtime_pair_rotation,
    require_no_pending_runtime_pair_rotation,
    rotate_pair_with_execution,
)
from kor_travel_docker_manager.services.runtime_pin_registry import (
    build_registry,
    verify_runtime_pin_registry,
)

_MAP = "a" * 40
_PINVI = "b" * 40
_MANAGER_A = "c" * 40
_MANAGER_B = "d" * 40


@pytest.fixture(autouse=True)
def _allow_drvfs_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV, "1")
    monkeypatch.setenv(pair_rotation.RUNTIME_PAIR_ROTATION_ALLOW_INSECURE_MODE_ENV, "1")


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


def test_execution_can_rebind_only_for_new_manager_revision() -> None:
    pins = _pins()
    registry = migrate_execution_registry(
        pins=pins, manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
    )
    rebound = rebind_execution_registry(
        registry=registry,
        pins=pins,
        manager_source_revision=_MANAGER_B,
        bound_by="tester",
        reason="Manager fix",
    )

    assert rebound.current.source_pinset_sha256 == registry.current.source_pinset_sha256
    assert rebound.current.execution_identity_sha256 != registry.current.execution_identity_sha256
    assert not rebound.blocked_executions
    assert not rebound.is_unconditionally_blocked_current()

    terminal = block_current_execution(registry=registry, reason="terminal")
    terminal_rebound = rebind_execution_registry(
        registry=terminal,
        pins=pins,
        manager_source_revision=_MANAGER_B,
        bound_by="tester",
        reason="Manager fix",
    )
    assert len(terminal_rebound.blocked_executions) == 1
    assert not terminal_rebound.is_unconditionally_blocked_current()


def test_source_rotation_creates_a_new_execution_and_preserves_terminal_audit() -> None:
    initial = _pins()
    terminal = block_current_execution(
        registry=migrate_execution_registry(
            pins=initial, manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
        ),
        reason="terminal",
    )
    rotated_pins = build_registry(
        release_version=5,
        map_revision="e" * 40,
        pinvi_revision=_PINVI,
        rotated_by="tester",
        reason="source rotation",
    )

    rotated = rotate_execution_source_binding(
        registry=terminal,
        pins=rotated_pins,
        manager_source_revision=_MANAGER_A,
        bound_by="tester",
        reason="source rotation",
    )

    assert rotated.current.source_pinset_sha256 == rotated_pins.pinset_sha256
    assert not rotated.is_unconditionally_blocked_current()
    assert len(rotated.history) == 2
    assert len(rotated.blocked_executions) == 1


def test_cli_legacy_terminal_migration_creates_only_the_current_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = build_parser().parse_args(
        ["pin", "migrate-execution-v6", "--reason", "release transition", "--confirm"]
    )
    pins = _pins()
    saved: list[object] = []

    monkeypatch.setattr(cli, "_running_as_root", lambda: True)
    monkeypatch.setattr(cli, "_runtime_pin_mutation_lock", lambda **_kwargs: nullcontext())
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


def test_cli_pair_rotation_advances_an_existing_execution_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = build_parser().parse_args(
        [
            "pin",
            "rotate-pair",
            "--map-revision",
            "e" * 40,
            "--pinvi-revision",
            _PINVI,
            "--reason",
            "correct pair",
            "--confirm",
        ]
    )
    initial = _pins()
    executions = block_current_execution(
        registry=migrate_execution_registry(
            pins=initial, manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
        ),
        reason="terminal",
    )
    rotated_pins = build_registry(
        release_version=5,
        map_revision="e" * 40,
        pinvi_revision=_PINVI,
        rotated_by="tester",
        reason="correct pair",
    )
    saved: list[object] = []

    monkeypatch.setattr(cli, "_runtime_pin_mutation_lock", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(cli, "load_runtime_execution_registry", lambda: executions)
    monkeypatch.setattr(cli, "trusted_manager_source_revision", lambda: _MANAGER_A)
    monkeypatch.setattr(cli, "rotate_pair_with_execution", lambda **_kwargs: saved.append(_kwargs) or rotated_pins)

    assert cli._cmd_pin_rotate_pair(args) == 0
    assert saved[0]["map_revision"] == "e" * 40
    assert saved[0]["manager_source_revision"] == _MANAGER_A


def test_cli_pending_pair_recovery_never_falls_back_to_legacy_when_v6_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = build_parser().parse_args(
        [
            "pin",
            "rotate-pair",
            "--map-revision",
            "e" * 40,
            "--pinvi-revision",
            _PINVI,
            "--reason",
            "recover partial pair",
            "--confirm",
        ]
    )
    recovered = build_registry(
        release_version=5,
        map_revision="e" * 40,
        pinvi_revision=_PINVI,
        rotated_by="tester",
        reason="recover partial pair",
    )
    called: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_runtime_pin_mutation_lock", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(cli, "load_pending_runtime_pair_rotation", lambda: object())
    monkeypatch.setattr(
        cli,
        "load_runtime_execution_registry",
        lambda: (_ for _ in ()).throw(AssertionError("must not choose legacy branch")),
    )
    monkeypatch.setattr(cli, "trusted_manager_source_revision", lambda: _MANAGER_A)
    monkeypatch.setattr(
        cli,
        "rotate_pair_with_execution",
        lambda **kwargs: called.append(kwargs) or recovered,
    )

    assert cli._cmd_pin_rotate_pair(args) == 0
    assert called[0]["map_revision"] == "e" * 40


def test_pair_rotation_recovers_partial_v5_v6_write_without_manual_state_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v5가 먼저 기록돼도 intent가 남아 같은 CLI target으로 끝까지 복구한다."""

    from kor_travel_docker_manager.services import runtime_execution_registry as executions_module
    from kor_travel_docker_manager.services import runtime_pin_registry as pins_module

    pin_private = tmp_path / "pins.json"
    pin_public = tmp_path / "public" / "pins.json"
    execution_private = tmp_path / "executions.json"
    execution_public = tmp_path / "public" / "executions.json"
    transaction = tmp_path / "rotation.json"
    monkeypatch.setenv(pins_module.RUNTIME_PINS_FILE_ENV, str(pin_private))
    monkeypatch.setenv(pins_module.RUNTIME_PINS_PUBLIC_FILE_ENV, str(pin_public))
    monkeypatch.setenv(executions_module.RUNTIME_EXECUTIONS_FILE_ENV, str(execution_private))
    monkeypatch.setenv(
        executions_module.RUNTIME_EXECUTIONS_PUBLIC_FILE_ENV, str(execution_public)
    )
    monkeypatch.setenv(pair_rotation.RUNTIME_PAIR_ROTATION_FILE_ENV, str(transaction))

    initial_pins = _pins()
    from kor_travel_docker_manager.services.runtime_pin_registry import write_runtime_pin_registry

    write_runtime_pin_registry(initial_pins, preserve_previous=False)
    initial_executions = migrate_execution_registry(
        pins=initial_pins,
        manager_source_revision=_MANAGER_A,
        bound_by="tester",
        reason="migrate",
    )
    write_runtime_execution_registry(initial_executions)

    original_writer = pair_rotation.write_runtime_execution_registry
    monkeypatch.setattr(
        pair_rotation,
        "write_runtime_execution_registry",
        lambda _registry: (_ for _ in ()).throw(OSError("simulated v6 write failure")),
    )
    with pytest.raises(OSError, match="simulated"):
        rotate_pair_with_execution(
            map_revision="e" * 40,
            pinvi_revision=_PINVI,
            manager_source_revision=_MANAGER_A,
            reason="source correction",
            rotated_by="tester",
            block_previous=True,
        )

    pending = load_pending_runtime_pair_rotation()
    assert pending is not None
    assert pending.pin_registry.map_revision == "e" * 40
    assert load_runtime_execution_registry().current_matches(
        pins=initial_pins, manager_source_revision=_MANAGER_A
    )
    with pytest.raises(RuntimePairRotationError, match="incomplete"):
        require_no_pending_runtime_pair_rotation()

    monkeypatch.setattr(pair_rotation, "write_runtime_execution_registry", original_writer)
    recovered = rotate_pair_with_execution(
        map_revision="e" * 40,
        pinvi_revision=_PINVI,
        manager_source_revision=_MANAGER_A,
        reason="source correction",
        rotated_by="tester",
        block_previous=True,
    )
    assert load_pending_runtime_pair_rotation() is None
    assert load_runtime_execution_registry().current_matches(
        pins=recovered, manager_source_revision=_MANAGER_A
    )
    assert verify_runtime_pin_registry()["published_copy"] == "current"
    assert verify_runtime_execution_registry()["execution_public_copy"] == "current"


def test_pending_pair_rotation_refuses_a_different_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transaction = tmp_path / "rotation.json"
    monkeypatch.setenv(pair_rotation.RUNTIME_PAIR_ROTATION_FILE_ENV, str(transaction))
    pins = _pins()
    executions = migrate_execution_registry(
        pins=pins, manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
    )
    target_pins = build_registry(
        release_version=5,
        map_revision="e" * 40,
        pinvi_revision=_PINVI,
        rotated_by="tester",
        reason="source correction",
    )
    target_executions = rotate_execution_source_binding(
        registry=executions,
        pins=target_pins,
        manager_source_revision=_MANAGER_A,
        bound_by="tester",
        reason="source correction",
    )
    pending = pair_rotation.RuntimePairRotation(
        created_at="2026-08-29T00:00:00Z",
        pin_registry=target_pins,
        execution_registry=target_executions,
    )
    pair_rotation._atomic_write(transaction, pending.to_payload())

    monkeypatch.setattr(cli, "_GLOBAL_MUTATION_LOCK_PATH", tmp_path / "missing.lock")
    with pytest.raises(RuntimePairRotationError, match="incomplete"):
        with cli._runtime_pin_mutation_lock():
            pass
    with cli._runtime_pin_mutation_lock(allow_pending_pair_recovery=True):
        pass

    with pytest.raises(RuntimePairRotationError, match="different target"):
        rotate_pair_with_execution(
            map_revision="f" * 40,
            pinvi_revision=_PINVI,
            manager_source_revision=_MANAGER_A,
            reason="wrong",
            rotated_by="tester",
            block_previous=True,
        )


def test_rebind_same_manager_revision_is_an_exact_target_repair() -> None:
    pins = _pins()
    registry = migrate_execution_registry(
        pins=pins, manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
    )
    assert (
        rebind_execution_registry(
            registry=registry,
            pins=pins,
            manager_source_revision=_MANAGER_A,
            bound_by="tester",
            reason="retry",
        )
        == registry
    )


def test_same_target_rebind_recovers_after_the_public_copy_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private.json"
    public = tmp_path / "public.json"
    monkeypatch.setenv("KTDM_RUNTIME_EXECUTIONS_FILE", str(private))
    monkeypatch.setenv("KTDM_RUNTIME_EXECUTIONS_PUBLIC_FILE", str(public))
    initial = migrate_execution_registry(
        pins=_pins(), manager_source_revision=_MANAGER_A, bound_by="tester", reason="migrate"
    )
    write_runtime_execution_registry(initial)
    target = rebind_execution_registry(
        registry=initial,
        pins=_pins(),
        manager_source_revision=_MANAGER_B,
        bound_by="tester",
        reason="release",
    )

    original_write = execution_registry._write

    def fail_public(path: Path, payload: object, *, mode: int) -> None:
        if path == public:
            raise OSError("simulated public copy failure")
        original_write(path, payload, mode=mode)

    monkeypatch.setattr(execution_registry, "_write", fail_public)
    with pytest.raises(OSError, match="simulated public copy failure"):
        write_runtime_execution_registry(target)

    partial = load_runtime_execution_registry()
    assert partial == target
    assert verify_runtime_execution_registry()["execution_public_copy"] != "current"

    monkeypatch.setattr(execution_registry, "_write", original_write)
    repaired = rebind_execution_registry(
        registry=partial,
        pins=_pins(),
        manager_source_revision=_MANAGER_B,
        bound_by="tester",
        reason="retry",
    )
    assert repaired == target
    write_runtime_execution_registry(repaired)
    assert verify_runtime_execution_registry()["execution_public_copy"] == "current"


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
