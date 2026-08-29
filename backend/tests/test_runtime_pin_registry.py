"""runtime pin registry 계약 테스트.

live n150에서 실제 `rebuild-pinned`를 돌려 검증할 수 없는 경로(파괴적 재구축,
root 전용 파일 소유권)는 여기서 격리 tmp_path + mock으로 검증한다. 실제 호스트에서
검증한 것은 읽기 전용 CLI(`pin show/verify`)와 조회 API뿐이며, 그 범위는 저널에
기록한다.
"""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import runtime_execution_registry as execution_module
from kor_travel_docker_manager.services import runtime_pin_registry as registry_module
from kor_travel_docker_manager.services.runtime_pin_registry import (
    RUNTIME_PIN_REGISTRY_SCHEMA,
    BlockedPinset,
    RuntimePinRegistry,
    RuntimePinRegistryError,
    block_runtime_pinset,
    build_registry,
    clear_runtime_pin_registry_cache,
    load_runtime_pin_registry,
    publish_runtime_pins,
    read_published_runtime_pins,
    rollback_runtime_pin,
    rotate_runtime_pin,
    rotate_runtime_pin_pair,
    verify_runtime_pin_registry,
    write_runtime_pin_registry,
)

MAP_A = "a" * 40
PINVI_B = "b" * 40
MAP_C = "c" * 40
PINVI_D = "d" * 40


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """모든 테스트를 tmp_path의 registry로 격리한다."""

    registry_path = tmp_path / "runtime-pins.json"
    public_path = tmp_path / "public" / ".ktdm-runtime-pins.json"
    monkeypatch.setenv(registry_module.RUNTIME_PINS_FILE_ENV, str(registry_path))
    monkeypatch.setenv(registry_module.RUNTIME_PINS_PUBLIC_FILE_ENV, str(public_path))
    monkeypatch.setenv(
        execution_module.RUNTIME_EXECUTIONS_FILE_ENV, str(tmp_path / "runtime-executions.json")
    )
    monkeypatch.setenv(
        execution_module.RUNTIME_EXECUTIONS_PUBLIC_FILE_ENV,
        str(tmp_path / "public" / ".ktdm-runtime-executions.json"),
    )
    monkeypatch.setenv(execution_module.RUNTIME_EXECUTIONS_ALLOW_INSECURE_MODE_ENV, "1")
    clear_runtime_pin_registry_cache()
    yield registry_path, public_path
    clear_runtime_pin_registry_cache()


def _seed(**overrides) -> RuntimePinRegistry:
    values = {
        "release_version": 5,
        "map_revision": MAP_A,
        "pinvi_revision": PINVI_B,
        "rotated_by": "tester",
        "reason": "seed",
    }
    values.update(overrides)
    registry = build_registry(**values)
    write_runtime_pin_registry(registry, preserve_previous=False)
    return registry


# --- 로딩과 fail-close -------------------------------------------------------


def test_missing_registry_fails_closed_without_a_constant_fallback() -> None:
    with pytest.raises(RuntimePinRegistryError, match="missing"):
        load_runtime_pin_registry()


def test_registry_round_trips_into_a_valid_release() -> None:
    seeded = _seed()

    release = load_runtime_pin_registry().release()

    assert release.pinset_sha256 == seeded.pinset_sha256
    assert release.source_for("map").revision == MAP_A
    assert release.source_for("pinvi").revision == PINVI_B


def test_installed_isolated_interpreter_uses_external_runtime_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """root one-shot이 ``python -I``로 실행돼도 wheel 부모를 project root로 오인하지 않는다."""

    monkeypatch.delenv(registry_module.RUNTIME_PINS_FILE_ENV)
    monkeypatch.delenv(registry_module.RUNTIME_PINS_PUBLIC_FILE_ENV)
    monkeypatch.setattr(
        registry_module.sys,
        "prefix",
        "/opt/kor-travel-docker-manager/backend/.venv",
    )

    assert registry_module.runtime_pin_registry_path() == (
        Path("/var/lib/kor-travel-docker-manager") / "runtime-pins.json"
    )
    assert registry_module.runtime_pin_registry_public_path() == (
        Path("/var/lib/kor-travel-docker-manager-public") / "runtime-pins.json"
    )


def test_tampered_digest_is_rejected_even_though_the_file_says_otherwise(
    _isolated_registry,
) -> None:
    registry_path, _ = _isolated_registry
    _seed()
    document = json.loads(registry_path.read_text(encoding="utf-8"))
    document["pinset_sha256"] = "f" * 64
    registry_path.write_text(json.dumps(document), encoding="utf-8")
    clear_runtime_pin_registry_cache()

    with pytest.raises(RuntimePinRegistryError, match="digest differs"):
        load_runtime_pin_registry()


def test_noncanonical_source_url_in_the_file_is_rejected(_isolated_registry) -> None:
    """파일로 옮겨도 임의 저장소를 가리키게 만들 수 없다 — 전환의 핵심 안전 논거."""

    registry_path, _ = _isolated_registry
    _seed()
    document = json.loads(registry_path.read_text(encoding="utf-8"))
    document["sources"][0]["url"] = "https://github.com/attacker/kor-travel-map.git"
    registry_path.write_text(json.dumps(document), encoding="utf-8")
    clear_runtime_pin_registry_cache()

    with pytest.raises(RuntimePinRegistryError, match="canonical"):
        load_runtime_pin_registry()


@pytest.mark.parametrize(
    "mutate,error",
    [
        (lambda d: d.update(schema="other.v1"), "schema"),
        (lambda d: d.update(surprise=1), "unknown fields"),
        (lambda d: d["sources"].pop(), "exactly once"),
        (lambda d: d["sources"].reverse(), "exactly once"),
        (lambda d: d["sources"][1].update(revision="XYZ"), "40-hex"),
        (lambda d: d.update(rotated_at="not-a-timestamp"), "timestamp"),
        (lambda d: d.update(reason=""), "empty"),
    ],
)
def test_malformed_documents_fail_closed(_isolated_registry, mutate, error) -> None:
    registry_path, _ = _isolated_registry
    _seed()
    document = json.loads(registry_path.read_text(encoding="utf-8"))
    mutate(document)
    registry_path.write_text(json.dumps(document), encoding="utf-8")
    clear_runtime_pin_registry_cache()

    with pytest.raises(RuntimePinRegistryError, match=error):
        load_runtime_pin_registry()


def test_truncated_file_is_rejected(_isolated_registry) -> None:
    registry_path, _ = _isolated_registry
    _seed()
    raw = registry_path.read_text(encoding="utf-8")
    registry_path.write_text(raw[: len(raw) // 2], encoding="utf-8")
    clear_runtime_pin_registry_cache()

    with pytest.raises(RuntimePinRegistryError, match="valid JSON"):
        load_runtime_pin_registry()


def test_rotation_is_visible_without_restarting_the_process(_isolated_registry) -> None:
    """lru_cache 대신 mtime/size/inode 스탬프를 쓰므로 재기동이 필요 없다."""

    _seed()
    assert load_runtime_pin_registry().map_revision == MAP_A

    rotate_runtime_pin(
        role="map", revision=MAP_C, reason="new map head", rotated_by="tester"
    )

    assert load_runtime_pin_registry().map_revision == MAP_C


# --- 파일 쓰기와 보존 --------------------------------------------------------


def test_registry_file_is_owner_only_and_preserves_the_previous_pinset(
    _isolated_registry,
) -> None:
    registry_path, _ = _isolated_registry
    previous = _seed()

    rotate_runtime_pin(role="map", revision=MAP_C, reason="rotate", rotated_by="tester")

    preserved = registry_path.with_name(f"runtime-pins.{previous.pinset_sha256}.json")
    assert preserved.exists()
    assert json.loads(preserved.read_text(encoding="utf-8"))["pinset_sha256"] == (
        previous.pinset_sha256
    )
    if os.name != "nt":
        assert stat.S_IMODE(registry_path.stat().st_mode) == 0o600


def test_rotation_records_history_with_the_superseded_pinset() -> None:
    previous = _seed()

    updated = rotate_runtime_pin(
        role="pinvi", revision=MAP_C, reason="pinvi fix", rotated_by="tester"
    )

    assert len(updated.history) == 1
    entry = updated.history[-1]
    assert entry.pinset_sha256 == updated.pinset_sha256
    assert entry.supersedes_pinset_sha256 == previous.pinset_sha256
    assert entry.reason == "pinvi fix"
    assert entry.rotated_by == "tester"


def test_pair_rotation_replaces_both_sources_without_an_intermediate_pinset(
    _isolated_registry,
) -> None:
    """terminal M05 source pair는 role별 두 write가 아닌 한 번의 replace로 회전한다."""

    registry_path, _ = _isolated_registry
    previous = _seed()

    updated = rotate_runtime_pin_pair(
        map_revision=MAP_C,
        pinvi_revision=PINVI_D,
        reason="compatible M05 pair",
        rotated_by="tester",
    )

    assert (updated.map_revision, updated.pinvi_revision) == (MAP_C, PINVI_D)
    assert len(updated.history) == 1
    assert updated.history[-1].supersedes_pinset_sha256 == previous.pinset_sha256
    intermediate = _consistent_digest(map_revision=MAP_C, pinvi_revision=PINVI_B)
    assert updated.pinset_sha256 != intermediate
    assert not registry_path.with_name(f"runtime-pins.{intermediate}.json").exists()


def test_terminal_current_pinset_refuses_single_role_rotation() -> None:
    seed = _seed()
    terminal = block_runtime_pinset(pinset_sha256=seed.pinset_sha256, reason="terminal")

    with pytest.raises(RuntimePinRegistryError, match="atomic Map/PinVi pair"):
        rotate_runtime_pin(
            role="map", revision=MAP_C, reason="would split pair", rotated_by="tester"
        )

    assert load_runtime_pin_registry().pinset_sha256 == terminal.pinset_sha256


def test_rotation_that_changes_nothing_is_rejected() -> None:
    _seed()

    with pytest.raises(RuntimePinRegistryError, match="would not change"):
        rotate_runtime_pin(
            role="map", revision=MAP_A, reason="noop", rotated_by="tester"
        )


def test_rotation_refuses_a_group_writable_operational_registry(
    _isolated_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX 퍼미션 계약")
    registry_path, _ = _isolated_registry
    _seed()
    monkeypatch.delenv(registry_module.RUNTIME_PINS_ALLOW_INSECURE_MODE_ENV, raising=False)
    os.chmod(registry_path, 0o666)

    with pytest.raises(RuntimePinRegistryError, match="world writable"):
        rotate_runtime_pin(
            role="map", revision=MAP_C, reason="rotate", rotated_by="tester"
        )


# --- lifecycle: 차단 목록 ----------------------------------------------------


def test_block_previous_records_the_superseded_pinset_as_terminal() -> None:
    previous = _seed()

    updated = rotate_runtime_pin(
        role="map",
        revision=MAP_C,
        reason="previous candidate ended terminal",
        rotated_by="tester",
        block_previous=True,
    )

    assert updated.is_blocked_pinset(previous.pinset_sha256)
    assert not updated.is_blocked_pinset(updated.pinset_sha256)


def test_rotating_into_a_blocked_pinset_is_refused() -> None:
    first = _seed()
    rotate_runtime_pin(
        role="map",
        revision=MAP_C,
        reason="rotate away",
        rotated_by="tester",
        block_previous=True,
    )

    # 원래 값으로 되돌아가면 차단된 pinset을 다시 만들게 된다.
    with pytest.raises(RuntimePinRegistryError, match="permanently blocked"):
        rotate_runtime_pin(
            role="map", revision=MAP_A, reason="undo", rotated_by="tester"
        )
    assert load_runtime_pin_registry().is_blocked_pinset(first.pinset_sha256)


def _consistent_digest(map_revision: str = MAP_A, pinvi_revision: str = PINVI_B) -> str:
    return registry_module._compute_pinset_sha256(
        release_version=5, map_revision=map_revision, pinvi_revision=pinvi_revision
    )


def test_block_entry_matches_only_the_declared_phase() -> None:
    digest = _consistent_digest()
    entry = BlockedPinset(
        pinset_sha256=digest,
        map_revision=MAP_A,
        pinvi_revision=PINVI_B,
        reason="terminal",
        blocked_at="2026-08-28T00:00:00Z",
        phase="map_runtime_ready",
    )

    assert entry.matches(
        pinset_sha256=digest,
        map_source_revision=MAP_A,
        pinvi_source_revision=PINVI_B,
        phase="map_runtime_ready",
    )
    assert not entry.matches(
        pinset_sha256=digest,
        map_source_revision=MAP_A,
        pinvi_source_revision=PINVI_B,
        phase="candidate_attested",
    )


def test_block_entry_without_a_phase_matches_every_phase() -> None:
    digest = _consistent_digest()
    entry = BlockedPinset(
        pinset_sha256=digest,
        map_revision=MAP_A,
        pinvi_revision=PINVI_B,
        reason="terminal",
        blocked_at="2026-08-28T00:00:00Z",
    )

    for phase in ("map_runtime_ready", "candidate_attested", "databases_recreated"):
        assert entry.matches(
            pinset_sha256=digest,
            map_source_revision=MAP_A,
            pinvi_source_revision=PINVI_B,
            phase=phase,
        )


def test_pin_block_registers_the_current_pinset_without_revision_arguments() -> None:
    seeded = _seed()

    updated = block_runtime_pinset(
        pinset_sha256=seeded.pinset_sha256, reason="declared terminal upstream"
    )

    assert updated.is_blocked_pinset(seeded.pinset_sha256)
    assert updated.pinset_sha256 == seeded.pinset_sha256


def test_pin_block_upgrades_a_phase_scoped_entry_to_an_unconditional_block() -> None:
    """safe launcher fallback must make a previously scoped record terminal."""

    seeded = _seed()
    scoped = block_runtime_pinset(
        pinset_sha256=seeded.pinset_sha256,
        reason="phase journal only",
        phase="map_runtime_ready",
    )
    assert not scoped.is_unconditionally_blocked_pinset(seeded.pinset_sha256)

    updated = block_runtime_pinset(
        pinset_sha256=seeded.pinset_sha256,
        reason="launcher result unavailable",
    )

    assert updated.is_unconditionally_blocked_pinset(seeded.pinset_sha256)
    assert len(updated.blocked_pinsets) == 2
    assert updated.blocked_pinsets[-1].phase is None


def test_pin_block_requires_revisions_for_a_foreign_pinset() -> None:
    _seed()

    with pytest.raises(RuntimePinRegistryError, match="requires both revisions"):
        block_runtime_pinset(pinset_sha256="e" * 64, reason="terminal")


# --- rollback ----------------------------------------------------------------


def test_rollback_restores_a_preserved_pinset() -> None:
    original = _seed()
    rotate_runtime_pin(role="map", revision=MAP_C, reason="rotate", rotated_by="tester")

    restored = rollback_runtime_pin(
        pinset_sha256=original.pinset_sha256, rotated_by="tester", reason="undo"
    )

    assert restored.pinset_sha256 == original.pinset_sha256
    assert restored.map_revision == MAP_A
    assert restored.history[-1].supersedes_pinset_sha256 != original.pinset_sha256


def test_rollback_into_a_blocked_pinset_is_refused() -> None:
    """무제한 rollback은 교차 저장소의 'terminal 재시도 금지' 규약을 깨뜨린다."""

    original = _seed()
    rotate_runtime_pin(
        role="map",
        revision=MAP_C,
        reason="rotate",
        rotated_by="tester",
        block_previous=True,
    )

    with pytest.raises(RuntimePinRegistryError, match="blocked"):
        rollback_runtime_pin(
            pinset_sha256=original.pinset_sha256, rotated_by="tester", reason="undo"
        )


def test_rollback_without_a_preserved_copy_fails_closed() -> None:
    _seed()

    with pytest.raises(RuntimePinRegistryError, match="missing"):
        rollback_runtime_pin(
            pinset_sha256="e" * 64, rotated_by="tester", reason="undo"
        )


# --- publisher와 backend 읽기 경로 -------------------------------------------


def test_published_copy_is_world_readable_and_secret_free(_isolated_registry) -> None:
    _, public_path = _isolated_registry
    seeded = _seed()

    assert public_path.exists()
    document = json.loads(public_path.read_text(encoding="utf-8"))
    assert document["schema"] == RUNTIME_PIN_REGISTRY_SCHEMA
    assert document["pinset_sha256"] == seeded.pinset_sha256
    assert "published_at" in document
    if os.name != "nt":
        assert stat.S_IMODE(public_path.stat().st_mode) == 0o644


def test_backend_read_path_prefers_the_published_copy(_isolated_registry) -> None:
    seeded = _seed()

    payload = read_published_runtime_pins()

    assert payload["status"] == "ok"
    assert payload["source"] == "published_copy"
    assert payload["pinset_sha256"] == seeded.pinset_sha256


def test_backend_read_path_reports_unknown_when_nothing_is_readable() -> None:
    payload = read_published_runtime_pins()

    assert payload["status"] == "unknown"
    assert payload["source"] is None


def test_backend_read_path_reports_unknown_for_a_malformed_copy(
    _isolated_registry,
) -> None:
    _, public_path = _isolated_registry
    _seed()
    public_path.write_text('{"schema": "wrong"}', encoding="utf-8")

    payload = read_published_runtime_pins()

    assert payload["status"] == "unknown"
    assert payload["source"] == "published_copy"


def test_verify_reports_a_stale_published_copy(_isolated_registry) -> None:
    registry_path, _ = _isolated_registry
    _seed()
    stale = build_registry(
        release_version=5,
        map_revision=MAP_C,
        pinvi_revision=PINVI_B,
        rotated_by="tester",
        reason="stale",
    )
    publish_runtime_pins(stale)

    report = verify_runtime_pin_registry()

    assert report["published_copy"] == "stale"
    assert report["digest_recomputation"] == "ok"
    assert report["registry_path_name"] == registry_path.name


def test_verify_counts_lifecycle_state() -> None:
    _seed()
    rotate_runtime_pin(
        role="map",
        revision=MAP_C,
        reason="rotate",
        rotated_by="tester",
        block_previous=True,
    )

    report = verify_runtime_pin_registry()

    assert report["published_copy"] == "current"
    assert report["current_pinset_is_blocked"] is False
    # 등재한 1건 + 코드가 강제하는 하한선.
    assert report["blocked_pinset_count"] == 1 + len(
        registry_module._CODE_ENFORCED_BLOCKED_PINSETS
    )
    assert report["history_length"] == 1


# --- 저장소에 포함된 seed 파일 ------------------------------------------------


def test_packaged_seed_registry_satisfies_the_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """저장소에 추적된 읽기 전용 seed가 계약을 만족하는지 확인한다.

    seed의 **현재 pin이 무엇인지**는 단언하지 않는다 — 그건 정당한 회전마다 바뀌는
    값이고, 그런 단언이 바로 이 전환이 없애려던 테스트 churn이다. 회전과 무관하게
    성립해야 하는 성질만 본다.
    """

    seed_path = registry_module.packaged_seed_path()
    monkeypatch.delenv(registry_module.RUNTIME_PINS_FILE_ENV, raising=False)
    clear_runtime_pin_registry_cache()

    registry = load_runtime_pin_registry(path=seed_path)

    assert seed_path.name == "runtime-pins.seed.json"
    assert registry.release_version == 5
    assert registry.release().pinset_sha256 == registry.pinset_sha256
    # seed가 실제로 선언한 목록을 본다. effective 집합을 보면 코드 하한선 때문에
    # seed가 비어 있어도 통과하는 동어반복이 된다.
    declared = {entry.pinset_sha256 for entry in registry.blocked_pinsets}
    for entry in registry_module._CODE_ENFORCED_BLOCKED_PINSETS:
        assert entry.pinset_sha256 in declared


def test_supported_release_version_mirror_matches_the_release_module() -> None:
    """순환 import를 피하려 복제한 상수가 갈라지지 않는지 고정한다."""

    from kor_travel_docker_manager.services.pinned_runtime_release import (
        PINNED_RUNTIME_RELEASE_VERSION,
    )

    assert registry_module._SUPPORTED_RELEASE_VERSION == PINNED_RUNTIME_RELEASE_VERSION


# --- rebuild 시작 게이트 ------------------------------------------------------


def _blocked_seed(*, phase: str | None) -> RuntimePinRegistry:
    seeded = build_registry(
        release_version=5,
        map_revision=MAP_A,
        pinvi_revision=PINVI_B,
        rotated_by="tester",
        reason="seed",
    )
    blocked = BlockedPinset(
        pinset_sha256=seeded.pinset_sha256,
        map_revision=MAP_A,
        pinvi_revision=PINVI_B,
        reason="terminal upstream",
        blocked_at="2026-08-28T00:00:00Z",
        phase=phase,
    )
    registry = RuntimePinRegistry(
        release_version=seeded.release_version,
        map_revision=seeded.map_revision,
        pinvi_revision=seeded.pinvi_revision,
        pinset_sha256=seeded.pinset_sha256,
        rotated_at=seeded.rotated_at,
        rotated_by=seeded.rotated_by,
        reason=seeded.reason,
        blocked_pinsets=(blocked,),
    )
    write_runtime_pin_registry(registry, preserve_previous=False)
    return registry


def test_rebuild_start_gate_refuses_an_unconditionally_blocked_pinset() -> None:
    """destructive 작업 이전에 거부한다 — 사람의 기억이 아니라 기계가 규약을 지킨다."""

    from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
    from kor_travel_docker_manager.services.compose_service import (
        _assert_pinset_is_not_permanently_blocked,
    )

    registry = _blocked_seed(phase=None)

    with pytest.raises(DeploymentContractError, match="missing, stale, or terminal"):
        _assert_pinset_is_not_permanently_blocked(registry.pinset_sha256)


def test_rebuild_start_gate_allows_only_a_current_unblocked_v6_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v5 terminal audit은 보존하되 새 trusted execution의 one-shot만 허용한다."""

    from kor_travel_docker_manager.services import compose_service
    from kor_travel_docker_manager.services.runtime_execution_registry import (
        migrate_execution_registry,
    )

    registry = _blocked_seed(phase=None)
    execution = migrate_execution_registry(
        pins=registry,
        manager_source_revision="e" * 40,
        bound_by="tester",
        reason="legacy audit migration",
    )
    monkeypatch.setattr(
        execution_module,
        "load_runtime_execution_registry",
        lambda: execution,
    )
    monkeypatch.setattr(
        execution_module,
        "trusted_manager_source_revision",
        lambda: "e" * 40,
    )

    compose_service._assert_pinset_is_not_permanently_blocked(registry.pinset_sha256)


def test_rebuild_start_gate_refuses_a_terminal_v6_execution_for_an_unblocked_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v5 source audit이 깨끗해도 v6 one-shot terminal은 절대 우회하지 않는다."""

    from kor_travel_docker_manager.services import compose_service
    from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
    from kor_travel_docker_manager.services.runtime_execution_registry import (
        block_current_execution,
        migrate_execution_registry,
    )

    registry = _seed()
    execution = block_current_execution(
        registry=migrate_execution_registry(
            pins=registry,
            manager_source_revision="e" * 40,
            bound_by="tester",
            reason="migrate",
        ),
        reason="terminal",
    )
    monkeypatch.setattr(execution_module, "load_runtime_execution_registry", lambda: execution)
    monkeypatch.setattr(execution_module, "trusted_manager_source_revision", lambda: "e" * 40)

    with pytest.raises(DeploymentContractError, match="current trusted execution.*terminal"):
        compose_service._assert_pinset_is_not_permanently_blocked(registry.pinset_sha256)


def test_rebuild_start_gate_ignores_phase_scoped_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """phase 한정 차단은 v6 execution이 유효할 때 시작 게이트를 막지 않는다."""

    from kor_travel_docker_manager.services import compose_service
    from kor_travel_docker_manager.services.runtime_execution_registry import (
        migrate_execution_registry,
    )

    registry = _blocked_seed(phase="map_runtime_ready")
    execution = migrate_execution_registry(
        pins=registry,
        manager_source_revision="e" * 40,
        bound_by="tester",
        reason="migrate",
    )
    monkeypatch.setattr(execution_module, "load_runtime_execution_registry", lambda: execution)
    monkeypatch.setattr(execution_module, "trusted_manager_source_revision", lambda: "e" * 40)

    compose_service._assert_pinset_is_not_permanently_blocked(registry.pinset_sha256)
    assert registry.is_blocked_pinset(registry.pinset_sha256)
    assert not registry.is_unconditionally_blocked_pinset(registry.pinset_sha256)


def test_rebuild_start_gate_fails_closed_when_the_registry_vanishes(
    _isolated_registry,
) -> None:
    from kor_travel_docker_manager.services.compose_service import (
        _assert_pinset_is_not_permanently_blocked,
    )

    registry_path, _ = _isolated_registry
    seeded = _seed()
    registry_path.unlink()
    clear_runtime_pin_registry_cache()

    with pytest.raises(RuntimePinRegistryError, match="missing"):
        _assert_pinset_is_not_permanently_blocked(seeded.pinset_sha256)


def test_blocked_pinset_retry_helper_honours_phase_scope() -> None:
    from kor_travel_docker_manager.services.pinned_runtime_release import (
        is_blocked_pinset_retry,
    )

    registry = _blocked_seed(phase="map_runtime_ready")

    assert is_blocked_pinset_retry(
        pinset_sha256=registry.pinset_sha256,
        map_source_revision=MAP_A,
        pinvi_source_revision=PINVI_B,
        phase="map_runtime_ready",
    )
    assert not is_blocked_pinset_retry(
        pinset_sha256=registry.pinset_sha256,
        map_source_revision=MAP_A,
        pinvi_source_revision=PINVI_B,
        phase="candidate_attested",
    )


# --- 리뷰 지적 회귀 (P2-7·P2-8·P2-9) -----------------------------------------


def test_external_rotation_is_seen_without_an_explicit_cache_clear(
    _isolated_registry,
) -> None:
    """실제 무효화 메커니즘을 검증한다.

    회전 헬퍼는 캐시를 명시적으로 비우므로, 그 경로만 보면 스탬프 로직을 통째로
    지워도 테스트가 통과한다. 여기서는 별도 프로세스가 파일을 교체한 상황을 흉내
    내어(캐시를 비우지 않고 os.replace) 재로드가 새 값을 주는지 본다 — root CLI가
    회전했을 때 실행 중 backend가 재기동 없이 보게 되는 바로 그 경로다.
    """

    registry_path, _ = _isolated_registry
    _seed()
    assert load_runtime_pin_registry().map_revision == MAP_A

    rotated = build_registry(
        release_version=5,
        map_revision=MAP_C,
        pinvi_revision=PINVI_B,
        rotated_by="another-process",
        reason="external rotation",
    )
    replacement = registry_path.with_suffix(".incoming")
    replacement.write_text(
        json.dumps(rotated.to_payload()), encoding="utf-8"
    )
    os.replace(replacement, registry_path)

    assert load_runtime_pin_registry().map_revision == MAP_C


def test_rebuild_refuses_a_blocked_pinset_before_touching_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트가 mutation 이전에 있다는 사실을 end-to-end로 결박한다.

    게이트 헬퍼만 단위로 부르면 누가 호출을 mutation 뒤로 옮기거나 지워도 스위트가
    초록으로 남는다. 실제 ``rebuild_pinned_runtime()``을 호출해 source materialize와
    DB reset이 **호출되지 않았음**을 단언한다.
    """

    from unittest.mock import Mock

    from kor_travel_docker_manager.services import compose_service as compose_service_module
    from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

    registry = _blocked_seed(phase=None)
    materialize = Mock()
    lock_entered = False

    @contextmanager
    def lock(*, prewrite_admission):
        nonlocal lock_entered
        lock_entered = True
        prewrite_admission(Mock())
        yield (Mock(), Mock(), False)
    monkeypatch.setattr(
        compose_service_module, "_require_pinned_runtime_rebuild_root", lambda: None
    )
    monkeypatch.setattr(
        compose_service_module, "materialize_pinned_runtime_sources", materialize
    )
    monkeypatch.setattr(
        compose_service_module, "_pinned_runtime_rebuild_environment_lock", lock
    )

    with pytest.raises(DeploymentContractError, match="missing, stale, or terminal"):
        compose_service_module.ComposeService().rebuild_pinned_runtime()

    materialize.assert_not_called()
    # release snapshot과 v6 gate는 회전과 같은 global lock 안에서만 읽는다.
    assert lock_entered
    assert registry.is_unconditionally_blocked_pinset(registry.pinset_sha256)


def test_pinset_digest_algorithm_is_pinned_to_a_literal() -> None:
    """digest는 kor-travel-map attestation과 공유하는 계약이다.

    self-consistent 단언만 있으면 알고리즘을 바꿔도 전부 통과한다. 알고리즘 교체가
    즉시 실패하도록 리터럴 하나를 직접 고정한다.
    """

    from kor_travel_docker_manager.services.pinned_runtime_release import (
        canonical_pinset_sha256,
        source_specs_for,
    )

    digest = canonical_pinset_sha256(
        version=5,
        sources=source_specs_for(map_revision="a" * 40, pinvi_revision="b" * 40),
    )

    assert digest == "46732f376843b2e84579267b86cc700041e18736f7f4858d5d84c5cd369d8f4e"


def test_every_write_path_refuses_the_read_only_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pin init``도 같은 가드를 지난다 — 추적되는 seed는 회전 대상이 아니다."""

    seed_path = registry_module.packaged_seed_path()
    registry = build_registry(
        release_version=5,
        map_revision=MAP_A,
        pinvi_revision=PINVI_B,
        rotated_by="tester",
        reason="should never land on the seed",
    )

    with pytest.raises(RuntimePinRegistryError, match="read-only bootstrap input"):
        write_runtime_pin_registry(registry, path=seed_path)


def test_every_write_path_refuses_a_path_inside_the_install_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """설치 트리 안이면 다음 release 설치가 회전을 조용히 되돌린다."""

    monkeypatch.setattr(
        registry_module, "_TRUSTED_INSTALL_ROOT", Path("/"), raising=True
    )
    registry = build_registry(
        release_version=5,
        map_revision=MAP_A,
        pinvi_revision=PINVI_B,
        rotated_by="tester",
        reason="inside the deploy tree",
    )

    with pytest.raises(RuntimePinRegistryError, match="trusted install root"):
        write_runtime_pin_registry(registry)


def test_rotation_target_is_checked_against_the_code_enforced_floor(
    _isolated_registry,
) -> None:
    """registry에서 하한선 항목을 지워도 그 pinset으로는 회전할 수 없다."""

    floor = registry_module._CODE_ENFORCED_BLOCKED_PINSETS[0]
    _seed(map_revision=floor.map_revision, pinvi_revision=MAP_C)

    with pytest.raises(RuntimePinRegistryError, match="permanently blocked"):
        rotate_runtime_pin(
            role="pinvi",
            revision=floor.pinvi_revision,
            reason="would land on the floor entry",
            rotated_by="tester",
        )


def test_unsupported_release_version_is_refused_at_parse_time(
    _isolated_registry,
) -> None:
    registry_path, _ = _isolated_registry
    _seed()
    document = json.loads(registry_path.read_text(encoding="utf-8"))
    document["release_version"] = 4
    registry_path.write_text(json.dumps(document), encoding="utf-8")
    clear_runtime_pin_registry_cache()

    with pytest.raises(RuntimePinRegistryError, match="release_version"):
        load_runtime_pin_registry()


def test_blocked_list_overflow_fails_closed_instead_of_dropping_entries() -> None:
    """가장 오래된 terminal 항목이 조용히 빠지면 그 candidate가 다시 실행 가능해진다."""

    entries = []
    for index in range(registry_module._MAX_BLOCKED_ENTRIES + 1):
        map_revision = f"{index:040x}"
        entries.append(
            BlockedPinset(
                pinset_sha256=_consistent_digest(map_revision, PINVI_B),
                map_revision=map_revision,
                pinvi_revision=PINVI_B,
                reason="terminal",
                blocked_at="2026-08-28T00:00:00Z",
            )
        )

    with pytest.raises(RuntimePinRegistryError, match="too long"):
        build_registry(
            release_version=5,
            map_revision=MAP_A,
            pinvi_revision=PINVI_B,
            rotated_by="tester",
            reason="overflow",
            blocked_pinsets=entries,
        )


def test_symlinked_registry_is_refused(_isolated_registry) -> None:
    """symlink를 따라가 다른 파일을 registry로 읽지 않는다."""

    if os.name == "nt":
        pytest.skip("POSIX symlink 계약")
    registry_path, _ = _isolated_registry
    _seed()
    real = registry_path.with_suffix(".real")
    registry_path.rename(real)
    registry_path.symlink_to(real)
    clear_runtime_pin_registry_cache()

    with pytest.raises(RuntimePinRegistryError, match="not a regular file"):
        load_runtime_pin_registry()


def test_backend_read_path_reports_stale_when_the_copy_lags(_isolated_registry) -> None:
    """공개 사본이 registry보다 오래됐으면 ok라고 말하지 않는다."""

    _seed()
    stale = build_registry(
        release_version=5,
        map_revision=MAP_C,
        pinvi_revision=PINVI_B,
        rotated_by="tester",
        reason="stale copy",
    )
    publish_runtime_pins(stale)

    payload = read_published_runtime_pins()

    assert payload["status"] == "stale"
    assert payload["source"] == "published_copy"


def test_backend_read_path_reports_degraded_when_only_the_registry_is_readable(
    _isolated_registry,
) -> None:
    """배포본 seed를 권위 있는 값으로 위장하지 않는다."""

    _, public_path = _isolated_registry
    _seed()
    public_path.unlink()

    payload = read_published_runtime_pins()

    assert payload["status"] == "degraded"
    assert payload["source"] == "registry"
    assert "배포본 기본값" in payload["detail"]
