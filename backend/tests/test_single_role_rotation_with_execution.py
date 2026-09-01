"""GM-01 회귀: v6 host의 단일 role 회전·rollback이 execution registry를 함께 갱신한다.

이 계약이 없을 때의 사고 경로: `pin rotate`/`pin apply-pending`이 v5만 회전하면 v6
execution binding이 stale이 되어 rebuild가 fail-close되고, `pin verify`가 안내하던
migrate/rebind는 둘 다 거부된다(migrate는 registry 존재로, rebind는 pinset 불일치로).
복구는 rollback→rotate-pair 두 명령이지만 시스템의 어떤 안내도 그 경로를 가리키지
않았다. 여기의 테스트들은 (1) 그 stale 상태가 mainline 명령으로 더는 만들어지지
않고, (2) 이미 stale인 host를 rollback이 치유하며, (3) terminal 규율과 durable
intent 계약이 pair 회전과 동일하게 유지됨을 고정한다.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from kor_travel_docker_manager import cli
from kor_travel_docker_manager.cli import build_parser
from kor_travel_docker_manager.services import runtime_execution_registry as executions_module
from kor_travel_docker_manager.services import runtime_pair_rotation as pair_rotation
from kor_travel_docker_manager.services import runtime_pin_registry as pins_module
from kor_travel_docker_manager.services.runtime_execution_registry import (
    RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV,
    RuntimeExecutionRegistryError,
    load_runtime_execution_registry,
    migrate_execution_registry,
    verify_runtime_execution_registry,
    write_runtime_execution_registry,
)
from kor_travel_docker_manager.services.runtime_pair_rotation import (
    RuntimePairRotationError,
    load_pending_runtime_pair_rotation,
    rollback_with_execution,
    rotate_pair_with_execution,
    rotate_single_role_with_execution,
)
from kor_travel_docker_manager.services.runtime_pin_registry import (
    BlockedPinset,
    build_registry,
    load_runtime_pin_registry,
    rotate_runtime_pin,
    utc_timestamp,
    verify_runtime_pin_registry,
    write_runtime_pin_registry,
)

_MAP = "a" * 40
_PINVI = "b" * 40
_MAP_NEXT = "e" * 40
_MANAGER = "c" * 40


@pytest.fixture(autouse=True)
def _allow_drvfs_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV, "1")
    monkeypatch.setenv(pair_rotation.RUNTIME_PAIR_ROTATION_ALLOW_INSECURE_MODE_ENV, "1")


@pytest.fixture
def v6_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """v5/v6/intent 파일 전체를 격리 경로로 돌리고 v6 host 상태를 seed한다."""

    monkeypatch.setenv(pins_module.RUNTIME_PINS_FILE_ENV, str(tmp_path / "pins.json"))
    monkeypatch.setenv(
        pins_module.RUNTIME_PINS_PUBLIC_FILE_ENV, str(tmp_path / "public" / "pins.json")
    )
    monkeypatch.setenv(
        executions_module.RUNTIME_EXECUTIONS_FILE_ENV, str(tmp_path / "executions.json")
    )
    monkeypatch.setenv(
        executions_module.RUNTIME_EXECUTIONS_PUBLIC_FILE_ENV,
        str(tmp_path / "public" / "executions.json"),
    )
    monkeypatch.setenv(
        pair_rotation.RUNTIME_PAIR_ROTATION_FILE_ENV, str(tmp_path / "rotation.json")
    )
    pins = build_registry(
        release_version=5,
        map_revision=_MAP,
        pinvi_revision=_PINVI,
        rotated_by="tester",
        reason="seed",
    )
    write_runtime_pin_registry(pins, preserve_previous=False)
    executions = migrate_execution_registry(
        pins=pins, manager_source_revision=_MANAGER, bound_by="tester", reason="migrate"
    )
    write_runtime_execution_registry(executions)
    return pins


def test_single_role_rotate_fails_closed_when_v6_registry_is_unreadable(
    v6_host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v6 파일이 있는데 읽기 불가면 legacy host로 오인해 v5만 회전하면 안 된다(F4).

    이 fail-close가 없으면 `_uses_execution_registry`가 legacy를 반환해 단일 role
    회전이 v5만 바꾸고 execution binding이 stale이 되는 GM-01의 원래 사고로 되돌아간다.
    """

    from kor_travel_docker_manager import cli
    from kor_travel_docker_manager.services.runtime_execution_registry import (
        RuntimeExecutionRegistryError,
    )

    # v6 파일은 존재하지만 로드가 실패하는 상황(손상·권한 등)을 재현한다.
    monkeypatch.setattr(
        cli,
        "load_runtime_execution_registry",
        lambda: (_ for _ in ()).throw(RuntimeExecutionRegistryError("unreadable v6")),
    )
    before = load_runtime_pin_registry().pinset_sha256

    # v6 파일이 존재하므로(v6_host가 seed) load 실패는 legacy로 삼키지 않고 전파돼야 한다.
    with pytest.raises(RuntimeExecutionRegistryError):
        cli._uses_execution_registry()

    # v5 registry는 손대지 않았다.
    assert load_runtime_pin_registry().pinset_sha256 == before


def test_single_role_rotation_updates_both_registries(v6_host) -> None:
    rotated = rotate_single_role_with_execution(
        role="map",
        revision=_MAP_NEXT,
        manager_source_revision=_MANAGER,
        reason="새 Map head",
        rotated_by="tester",
        block_previous=False,
    )

    assert rotated.map_revision == _MAP_NEXT
    assert rotated.pinvi_revision == _PINVI
    assert load_runtime_pin_registry().pinset_sha256 == rotated.pinset_sha256
    assert load_runtime_execution_registry().current_matches(
        pins=rotated, manager_source_revision=_MANAGER
    )
    assert load_pending_runtime_pair_rotation() is None
    assert verify_runtime_pin_registry()["published_copy"] == "current"
    assert verify_runtime_execution_registry()["execution_public_copy"] == "current"


def test_single_role_rotation_refuses_terminal_current_pinset(
    v6_host, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """terminal에서의 단일 role 탈출은 pair 선언 없는 M05 pair-incomplete 소비 경로다."""

    current = load_runtime_pin_registry()
    terminal = build_registry(
        release_version=5,
        map_revision=_MAP,
        pinvi_revision=_PINVI,
        rotated_by="tester",
        reason="seed",
        blocked_pinsets=(
            BlockedPinset(
                pinset_sha256=current.pinset_sha256,
                map_revision=_MAP,
                pinvi_revision=_PINVI,
                reason="terminal",
                blocked_at=utc_timestamp(),
            ),
        ),
    )
    write_runtime_pin_registry(terminal, preserve_previous=False)

    with pytest.raises(RuntimePairRotationError, match="pair rotation"):
        rotate_single_role_with_execution(
            role="map",
            revision=_MAP_NEXT,
            manager_source_revision=_MANAGER,
            reason="탈출 시도",
            rotated_by="tester",
            block_previous=False,
        )
    # v5/v6 어느 쪽도 바뀌지 않았고 intent도 남지 않았다.
    assert load_runtime_pin_registry().map_revision == _MAP
    assert load_pending_runtime_pair_rotation() is None


def test_single_role_rotation_refuses_terminal_v6_execution(v6_host) -> None:
    """v5는 미차단인데 v6 execution만 terminal인 경우도 단일 role 탈출을 막아야 한다.

    M05 launcher는 terminal 판정을 `pin block-execution`으로 v6에만 쓰고 v5
    blocked_pinsets는 비운다(정상 경로). v5 terminal만 검사하면 단일 role 회전이 새
    미차단 execution을 만들어 terminal one-shot을 pair 선언 없이 탈출한다.
    """

    from kor_travel_docker_manager.services import runtime_execution_registry as ex_module
    from kor_travel_docker_manager.services.runtime_execution_registry import (
        block_current_execution,
        write_runtime_execution_registry,
    )

    # v6 execution만 무조건 차단(phase=None), v5는 그대로 미차단.
    blocked = block_current_execution(
        registry=load_runtime_execution_registry(), reason="terminal one-shot"
    )
    write_runtime_execution_registry(blocked)
    del ex_module
    assert not load_runtime_pin_registry().is_unconditionally_blocked_pinset(
        load_runtime_pin_registry().pinset_sha256
    )

    with pytest.raises(RuntimePairRotationError, match="pair rotation"):
        rotate_single_role_with_execution(
            role="map",
            revision=_MAP_NEXT,
            manager_source_revision=_MANAGER,
            reason="terminal v6 탈출 시도",
            rotated_by="tester",
            block_previous=False,
        )
    # v5/v6 어느 쪽도 바뀌지 않았고 intent도 남지 않았다.
    assert load_runtime_pin_registry().map_revision == _MAP
    assert load_pending_runtime_pair_rotation() is None


def test_resume_survives_a_manager_release_change_mid_crash(
    v6_host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """crash 창에서 trusted Manager release가 바뀌어도 재개가 host를 wedge하지 않는다.

    _same_requested_target이 manager_source_revision을 대조하면, v5 write 뒤 crash하고
    새 Manager release를 설치한 순간 모든 재개 경로가 'different target'으로 거부돼
    mainline 복구가 사라진다. target 유일성은 map·pinvi로만 판정해야 한다.
    """

    original_writer = pair_rotation.write_runtime_execution_registry
    monkeypatch.setattr(
        pair_rotation,
        "write_runtime_execution_registry",
        lambda _registry: (_ for _ in ()).throw(OSError("simulated v6 write failure")),
    )
    with pytest.raises(OSError, match="simulated"):
        rotate_single_role_with_execution(
            role="map",
            revision=_MAP_NEXT,
            manager_source_revision=_MANAGER,
            reason="새 Map head",
            rotated_by="tester",
            block_previous=False,
        )
    assert load_pending_runtime_pair_rotation() is not None

    # 그 사이 trusted Manager release가 바뀌었다(_MANAGER -> 다른 값)로 재개한다.
    monkeypatch.setattr(pair_rotation, "write_runtime_execution_registry", original_writer)
    recovered = rotate_single_role_with_execution(
        role="map",
        revision=_MAP_NEXT,
        manager_source_revision="f" * 40,  # 새 trusted Manager revision
        reason="새 Map head",
        rotated_by="tester",
        block_previous=False,
    )

    # wedge되지 않고 끝까지 publish됐다. intent가 baked한 원래 msr로 발행됐고, 이후
    # rebind가 정본 복구다.
    assert load_pending_runtime_pair_rotation() is None
    assert recovered.map_revision == _MAP_NEXT
    assert load_runtime_execution_registry().current.manager_source_revision == _MANAGER


def test_single_role_rotation_recovers_partial_v5_v6_write(
    v6_host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v5 write 뒤 crash해도 intent가 남아 같은 단일 role 재시도가 끝까지 publish한다."""

    original_writer = pair_rotation.write_runtime_execution_registry
    monkeypatch.setattr(
        pair_rotation,
        "write_runtime_execution_registry",
        lambda _registry: (_ for _ in ()).throw(OSError("simulated v6 write failure")),
    )
    with pytest.raises(OSError, match="simulated"):
        rotate_single_role_with_execution(
            role="map",
            revision=_MAP_NEXT,
            manager_source_revision=_MANAGER,
            reason="새 Map head",
            rotated_by="tester",
            block_previous=False,
        )

    assert load_pending_runtime_pair_rotation() is not None
    # v6는 아직 이전 source를 가리킨다 — 정확히 stale 상태다.
    assert load_runtime_execution_registry().current_matches(
        pins=v6_host, manager_source_revision=_MANAGER
    )

    monkeypatch.setattr(pair_rotation, "write_runtime_execution_registry", original_writer)
    recovered = rotate_single_role_with_execution(
        role="map",
        revision=_MAP_NEXT,
        manager_source_revision=_MANAGER,
        reason="새 Map head",
        rotated_by="tester",
        block_previous=False,
    )

    assert load_pending_runtime_pair_rotation() is None
    assert load_runtime_execution_registry().current_matches(
        pins=recovered, manager_source_revision=_MANAGER
    )
    assert verify_runtime_execution_registry()["execution_public_copy"] == "current"


def test_single_role_rotation_refuses_pending_intent_for_a_different_target(
    v6_host, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pair_rotation,
        "write_runtime_execution_registry",
        lambda _registry: (_ for _ in ()).throw(OSError("simulated v6 write failure")),
    )
    with pytest.raises(OSError, match="simulated"):
        rotate_single_role_with_execution(
            role="map",
            revision=_MAP_NEXT,
            manager_source_revision=_MANAGER,
            reason="새 Map head",
            rotated_by="tester",
            block_previous=False,
        )

    with pytest.raises(RuntimePairRotationError, match="different target"):
        rotate_single_role_with_execution(
            role="map",
            revision="f" * 40,
            manager_source_revision=_MANAGER,
            reason="다른 target",
            rotated_by="tester",
            block_previous=False,
        )


def test_rollback_with_execution_heals_a_stale_source_registry(v6_host) -> None:
    """GM-01의 핵심 복구 시나리오: v5만 앞서간 host를 rollback이 치유한다."""

    healthy_pinset = v6_host.pinset_sha256
    # 결함이 있던 시절의 단일 role 회전(v5 단독 writer)으로 stale 상태를 재현한다.
    rotate_runtime_pin(
        role="map", revision=_MAP_NEXT, reason="v5만 회전", rotated_by="tester"
    )
    executions_before = load_runtime_execution_registry()
    assert not executions_before.current_matches(
        pins=load_runtime_pin_registry(), manager_source_revision=_MANAGER
    )

    restored = rollback_with_execution(
        pinset_sha256=healthy_pinset,
        manager_source_revision=_MANAGER,
        reason="stale 치유",
        rotated_by="tester",
    )

    assert restored.pinset_sha256 == healthy_pinset
    executions_after = load_runtime_execution_registry()
    # 치유형: execution은 교체가 아니라 보존이다 — identity가 그대로여야 history와
    # terminal audit이 유지된다.
    assert (
        executions_after.current.execution_identity_sha256
        == executions_before.current.execution_identity_sha256
    )
    assert executions_after.current_matches(pins=restored, manager_source_revision=_MANAGER)
    assert verify_runtime_pin_registry()["published_copy"] == "current"
    assert verify_runtime_execution_registry()["execution_public_copy"] == "current"


def test_rollback_with_execution_moves_both_when_both_current(v6_host) -> None:
    original_pinset = v6_host.pinset_sha256
    rotated = rotate_pair_with_execution(
        map_revision=_MAP_NEXT,
        pinvi_revision=_PINVI,
        manager_source_revision=_MANAGER,
        reason="정상 pair 회전",
        rotated_by="tester",
        block_previous=False,
    )
    identity_after_rotation = (
        load_runtime_execution_registry().current.execution_identity_sha256
    )
    assert rotated.pinset_sha256 != original_pinset

    restored = rollback_with_execution(
        pinset_sha256=original_pinset,
        manager_source_revision=_MANAGER,
        reason="원복",
        rotated_by="tester",
    )

    assert restored.pinset_sha256 == original_pinset
    executions = load_runtime_execution_registry()
    assert executions.current_matches(pins=restored, manager_source_revision=_MANAGER)
    # 일반 원복은 새 execution binding으로 이행한다(치유형과 달리 교체).
    assert executions.current.execution_identity_sha256 != identity_after_rotation


def test_rollback_with_execution_rejects_a_malformed_pinset(v6_host) -> None:
    with pytest.raises(RuntimePairRotationError, match="64-hex"):
        rollback_with_execution(
            pinset_sha256="not-a-digest",
            manager_source_revision=_MANAGER,
            reason="원복",
            rotated_by="tester",
        )


# --- CLI 배선 ---------------------------------------------------------------


def _rotate_args() -> object:
    return build_parser().parse_args(
        [
            "pin",
            "rotate",
            "--role",
            "map",
            "--revision",
            _MAP_NEXT,
            "--reason",
            "새 Map head",
            "--confirm",
        ]
    )


def test_cli_single_role_rotate_routes_to_execution_writer_on_v6_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pins = build_registry(
        release_version=5,
        map_revision=_MAP,
        pinvi_revision=_PINVI,
        rotated_by="tester",
        reason="seed",
    )
    executions = migrate_execution_registry(
        pins=pins, manager_source_revision=_MANAGER, bound_by="tester", reason="migrate"
    )
    saved: list[dict[str, object]] = []

    monkeypatch.setattr(cli, "_runtime_pin_mutation_lock", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(cli, "load_pending_runtime_pair_rotation", lambda: None)
    monkeypatch.setattr(cli, "load_runtime_execution_registry", lambda: executions)
    monkeypatch.setattr(cli, "trusted_manager_source_revision", lambda: _MANAGER)
    monkeypatch.setattr(
        cli,
        "rotate_single_role_with_execution",
        lambda **kwargs: saved.append(kwargs) or pins,
    )
    monkeypatch.setattr(
        cli,
        "rotate_runtime_pin",
        lambda **_kwargs: pytest.fail("v6 host에서 v5 단독 writer를 호출하면 안 된다"),
    )

    assert cli._cmd_pin_rotate(_rotate_args()) == 0
    assert saved[0]["role"] == "map"
    assert saved[0]["revision"] == _MAP_NEXT
    assert saved[0]["manager_source_revision"] == _MANAGER


def test_cli_single_role_rotate_keeps_the_legacy_writer_without_v6_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pins = build_registry(
        release_version=5,
        map_revision=_MAP,
        pinvi_revision=_PINVI,
        rotated_by="tester",
        reason="seed",
    )
    saved: list[dict[str, object]] = []

    monkeypatch.setattr(cli, "_runtime_pin_mutation_lock", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(cli, "load_pending_runtime_pair_rotation", lambda: None)
    monkeypatch.setattr(
        cli,
        "load_runtime_execution_registry",
        lambda: (_ for _ in ()).throw(RuntimeExecutionRegistryError("missing")),
    )
    monkeypatch.setattr(
        cli, "runtime_execution_registry_path", lambda: tmp_path / "missing.json"
    )
    monkeypatch.setattr(
        cli, "rotate_runtime_pin", lambda **kwargs: saved.append(kwargs) or pins
    )
    monkeypatch.setattr(
        cli,
        "rotate_single_role_with_execution",
        lambda **_kwargs: pytest.fail("legacy host에서 v6 writer를 호출하면 안 된다"),
    )

    assert cli._cmd_pin_rotate(_rotate_args()) == 0
    assert saved[0]["role"] == "map"


def test_cli_rollback_routes_to_execution_writer_on_v6_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pins = build_registry(
        release_version=5,
        map_revision=_MAP,
        pinvi_revision=_PINVI,
        rotated_by="tester",
        reason="seed",
    )
    executions = migrate_execution_registry(
        pins=pins, manager_source_revision=_MANAGER, bound_by="tester", reason="migrate"
    )
    saved: list[dict[str, object]] = []
    args = build_parser().parse_args(
        [
            "pin",
            "rollback",
            "--to",
            pins.pinset_sha256,
            "--reason",
            "원복",
            "--confirm",
        ]
    )

    monkeypatch.setattr(cli, "_runtime_pin_mutation_lock", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(cli, "load_pending_runtime_pair_rotation", lambda: None)
    monkeypatch.setattr(cli, "load_runtime_execution_registry", lambda: executions)
    monkeypatch.setattr(cli, "trusted_manager_source_revision", lambda: _MANAGER)
    monkeypatch.setattr(
        cli, "rollback_with_execution", lambda **kwargs: saved.append(kwargs) or pins
    )
    monkeypatch.setattr(
        cli,
        "rollback_runtime_pin",
        lambda **_kwargs: pytest.fail("v6 host에서 v5 단독 rollback을 호출하면 안 된다"),
    )

    assert cli._cmd_pin_rollback(args) == 0
    assert saved[0]["pinset_sha256"] == pins.pinset_sha256
    assert saved[0]["manager_source_revision"] == _MANAGER
