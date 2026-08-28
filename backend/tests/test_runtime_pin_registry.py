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
from pathlib import Path

import pytest

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
    verify_runtime_pin_registry,
    write_runtime_pin_registry,
)

MAP_A = "a" * 40
PINVI_B = "b" * 40
MAP_C = "c" * 40


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """모든 테스트를 tmp_path의 registry로 격리한다."""

    registry_path = tmp_path / "runtime-pins.json"
    public_path = tmp_path / "public" / ".ktdm-runtime-pins.json"
    monkeypatch.setenv(registry_module.RUNTIME_PINS_FILE_ENV, str(registry_path))
    monkeypatch.setenv(registry_module.RUNTIME_PINS_PUBLIC_FILE_ENV, str(public_path))
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


def test_rotation_that_changes_nothing_is_rejected() -> None:
    _seed()

    with pytest.raises(RuntimePinRegistryError, match="would not change"):
        rotate_runtime_pin(
            role="map", revision=MAP_A, reason="noop", rotated_by="tester"
        )


def test_rotation_refuses_a_group_readable_operational_registry(
    _isolated_registry,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX 퍼미션 계약")
    registry_path, _ = _isolated_registry
    _seed()
    os.chmod(registry_path, 0o644)

    with pytest.raises(RuntimePinRegistryError, match="world accessible"):
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


def test_block_entry_matches_only_the_declared_phase() -> None:
    entry = BlockedPinset(
        pinset_sha256="d" * 64,
        map_revision=MAP_A,
        pinvi_revision=PINVI_B,
        reason="terminal",
        blocked_at="2026-08-28T00:00:00Z",
        phase="map_runtime_ready",
    )

    assert entry.matches(
        pinset_sha256="d" * 64,
        map_source_revision=MAP_A,
        pinvi_source_revision=PINVI_B,
        phase="map_runtime_ready",
    )
    assert not entry.matches(
        pinset_sha256="d" * 64,
        map_source_revision=MAP_A,
        pinvi_source_revision=PINVI_B,
        phase="candidate_attested",
    )


def test_block_entry_without_a_phase_matches_every_phase() -> None:
    entry = BlockedPinset(
        pinset_sha256="d" * 64,
        map_revision=MAP_A,
        pinvi_revision=PINVI_B,
        reason="terminal",
        blocked_at="2026-08-28T00:00:00Z",
    )

    for phase in ("map_runtime_ready", "candidate_attested", "databases_recreated"):
        assert entry.matches(
            pinset_sha256="d" * 64,
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
    assert report["blocked_pinset_count"] == 1
    assert report["history_length"] == 1


# --- 저장소에 포함된 seed 파일 ------------------------------------------------


def test_packaged_seed_registry_is_valid_and_records_known_terminal_pinsets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """저장소의 개발 기본 registry가 계약을 만족하는지 확인한다."""

    seed_path = Path(__file__).resolve().parents[2] / "config" / "runtime-pins.json"
    monkeypatch.delenv(registry_module.RUNTIME_PINS_FILE_ENV, raising=False)
    clear_runtime_pin_registry_cache()

    registry = load_runtime_pin_registry(path=seed_path)

    assert registry.release_version == 5
    assert registry.release().pinset_sha256 == registry.pinset_sha256
    # 현재 pin이 terminal로 등재돼 있다는 것 자체가 이번 감사의 핵심 발견이다.
    assert registry.is_blocked_pinset(registry.pinset_sha256)


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

    with pytest.raises(DeploymentContractError, match="must not be retried"):
        _assert_pinset_is_not_permanently_blocked(registry.pinset_sha256)


def test_rebuild_start_gate_ignores_phase_scoped_blocks() -> None:
    """phase 한정 차단은 특정 journal 재개만 막는다 — 시작 게이트가 관여하면 과차단이다."""

    from kor_travel_docker_manager.services.compose_service import (
        _assert_pinset_is_not_permanently_blocked,
    )

    registry = _blocked_seed(phase="map_runtime_ready")

    _assert_pinset_is_not_permanently_blocked(registry.pinset_sha256)
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
