"""재구축 실행 가능 판정 계약 테스트 (KUM-M14).

이 모듈의 요점은 **버튼이 아니라는 것**이다. 판정만 하고 실행하지 않으며, 근거를
하나라도 잃으면 초록불을 켜지 않는다. 잘못된 초록불은 pinset 하나를 태우고 terminal
규약 때문에 그것은 되돌릴 수 없다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import pinned_rebuild_preflight as preflight

PINSET = "a" * 64


def _pins(**overrides: Any) -> dict[str, Any]:
    payload = {
        "status": "ok",
        "pinset_sha256": PINSET,
        "sources": [
            {"role": "map", "url": "u", "revision": "b" * 40},
            {"role": "pinvi", "url": "u", "revision": "c" * 40},
        ],
        "blocked_pinsets": [],
    }
    payload.update(overrides)
    return payload


def _readiness(state: str = "ok", checks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "summary": {"state": state, "text": "요약"},
        "checks": checks if checks is not None else [],
    }


def _guard(verdict: str = "no_journal") -> dict[str, Any]:
    return {
        "verdict": verdict,
        "detail": "상세",
        "requires_acknowledgement": verdict in {"unverifiable", "unknown"},
        "blocking": verdict == "unfinished_journal",
    }


@pytest.fixture(autouse=True)
def _clear_readiness_cache():
    from kor_travel_docker_manager.services.deployment_readiness import (
        clear_deployment_readiness_cache,
    )

    clear_deployment_readiness_cache()
    yield
    clear_deployment_readiness_cache()


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pins: dict[str, Any] | None = None,
    guard: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
) -> None:
    monkeypatch.setattr(preflight, "read_published_runtime_pins", lambda: pins or _pins())
    monkeypatch.setattr(preflight, "pinned_rebuild_guard_state", lambda: guard or _guard())
    monkeypatch.setattr(
        preflight, "read_deployment_readiness", lambda: readiness or _readiness()
    )


def test_a_clean_host_reports_ok_and_still_does_not_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch)

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["summary"]["state"] == "ok"
    assert payload["can_start"] is True
    assert payload["blockers"] == []
    # 실행 주체는 언제나 SSH의 사람이다 — payload가 주는 것은 명령 문자열뿐이다.
    assert payload["command"].endswith("rebuild-pinned --confirm")


def test_a_terminal_pinset_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        pins=_pins(
            blocked_pinsets=[
                {"pinset_sha256": PINSET, "phase": None, "reason": "terminal"}
            ]
        ),
    )

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["can_start"] is False
    assert [row["code"] for row in payload["blockers"]] == ["PINSET_TERMINAL"]


def test_a_phase_scoped_block_does_not_block_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """phase 한정 차단은 journal 재개만 막는다 — 여기서 합치면 과차단이 된다."""

    _patch(
        monkeypatch,
        pins=_pins(
            blocked_pinsets=[
                {"pinset_sha256": PINSET, "phase": "map_runtime_ready", "reason": "d9"}
            ]
        ),
    )

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["blockers"] == []
    assert payload["can_start"] is True


def test_a_non_rebuildable_mode_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, guard=_guard("not_rebuildable"))

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["can_start"] is False
    assert [row["code"] for row in payload["blockers"]] == ["MODE_NOT_REBUILDABLE"]


def test_an_unfinished_journal_warns_that_it_will_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """차단이 아니다 — 다만 새로 시작하는 것이 아니라는 사실을 모르면 결과를 잘못 읽는다."""

    _patch(monkeypatch, guard=_guard("unfinished_journal"))

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["can_start"] is True
    assert [row["code"] for row in payload["warnings"]] == ["JOURNAL_WILL_RESUME"]


@pytest.mark.parametrize("verdict", ["unverifiable", "unknown"])
def test_an_unverifiable_journal_withholds_the_green_light(
    monkeypatch: pytest.MonkeyPatch, verdict: str
) -> None:
    _patch(monkeypatch, guard=_guard(verdict))

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["summary"]["state"] == "unverified"
    assert payload["can_start"] is False
    assert [row["code"] for row in payload["unverified"]] == ["JOURNAL_UNVERIFIABLE"]


def test_unverified_pins_withhold_the_green_light(monkeypatch: pytest.MonkeyPatch) -> None:
    """어느 pinset을 재구축하는지 말할 수 없으면 나머지 판정도 의미가 없다."""

    _patch(monkeypatch, pins={"status": "stale", "pinset_sha256": PINSET})

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["can_start"] is False
    assert payload["pinset_sha256"] is None
    assert [row["code"] for row in payload["unverified"]] == ["PINS_UNVERIFIED"]


def test_a_readiness_blocker_is_named_row_by_row(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        readiness=_readiness(
            "blocked",
            [
                {
                    "id": "pinvi_role_bootstrap_modes",
                    "state": "missing",
                    "label_ko": "고정된 PinVi revision의 역할 부트스트랩 계약",
                    "detail": "모드 3종이 없습니다",
                },
                {"id": "compose_single_file", "state": "ok", "label_ko": "x", "detail": "y"},
            ],
        ),
    )

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["can_start"] is False
    assert [row["code"] for row in payload["blockers"]] == [
        "READINESS_PINVI_ROLE_BOOTSTRAP_MODES"
    ]
    assert "모드 3종이 없습니다" in payload["blockers"][0]["text"]


def test_a_blocker_outranks_an_unverified_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    """확인 못 한 것이 확실한 차단을 덮으면 사람이 실제로 막는 것부터 고치지 못한다."""

    _patch(
        monkeypatch,
        guard=_guard("unverifiable"),
        pins=_pins(
            blocked_pinsets=[{"pinset_sha256": PINSET, "phase": None, "reason": "t"}]
        ),
    )

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["summary"]["state"] == "blocked"


def test_the_entry_point_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """진단 route가 500을 내면 운영자는 상태를 볼 유일한 창을 잃는다."""

    def explode() -> dict[str, Any]:
        raise RuntimeError("호스트를 읽을 수 없음")

    monkeypatch.setattr(preflight, "read_published_runtime_pins", explode)
    monkeypatch.setattr(preflight, "pinned_rebuild_guard_state", explode)
    monkeypatch.setattr(preflight, "read_deployment_readiness", explode)

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["summary"]["state"] == "unverified"
    assert payload["can_start"] is False


def test_the_module_never_executes_a_rebuild() -> None:
    """판정만 한다 — 이 모듈이 mutation 경로를 얻으면 경계가 사라진다."""

    source = Path(preflight.__file__).read_text(encoding="utf-8")
    for forbidden in ("subprocess", "rebuild_pinned_runtime", "compose_service"):
        assert forbidden not in source, forbidden
