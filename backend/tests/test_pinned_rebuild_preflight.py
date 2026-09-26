"""재구축 실행 가능 판정 계약 테스트 (KUM-M14).

이 모듈의 요점은 **버튼이 아니라는 것**이다. 판정만 하고 실행하지 않으며, 근거를
하나라도 잃으면 초록불을 켜지 않는다. 잘못된 초록불은 pinset 하나를 태우고 terminal
규약 때문에 그것은 되돌릴 수 없다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import deployment_readiness
from kor_travel_docker_manager.services import pinned_rebuild_preflight as preflight
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import DeploymentMode

PINSET = "a" * 64
_REBUILDABLE = DeploymentMode(
    environment="rehearsal",
    lifecycle="rebuildable",
    pinvi_environment="production",
    map_ops_principal_required=True,
)
_OPERATIONAL = DeploymentMode(
    environment="production",
    lifecycle="operational",
    pinvi_environment="production",
    map_ops_principal_required=True,
)


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


def _mode(outcome: DeploymentMode | Exception = _REBUILDABLE) -> Callable[[], DeploymentMode]:
    """`read_deployment_mode` 스텁. 예외를 주면 `.env`·모드를 읽지 못한 호스트다."""

    def read() -> DeploymentMode:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return read


def _generation(
    *, status: str = "ok", binding: str = "match"
) -> dict[str, Any]:
    return {
        "status": status,
        "pinset_binding": {"status": binding},
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
    mode: DeploymentMode | Exception = _REBUILDABLE,
    readiness: dict[str, Any] | None = None,
    generation: dict[str, Any] | None = None,
) -> None:
    monkeypatch.setattr(preflight, "read_published_runtime_pins", lambda: pins or _pins())
    monkeypatch.setattr(
        preflight,
        "read_published_pinned_runtime_generation",
        lambda: generation or _generation(),
    )
    monkeypatch.setattr(preflight, "read_deployment_mode", _mode(mode))
    # 실제 함수는 `force_refresh` 키워드를 받는다 — 스텁이 그것을 못 받으면 TypeError가
    # 광범위 except에 먹혀 "관측 실패"로 둔갑하고, 테스트가 엉뚱한 경로를 검증하게 된다.
    monkeypatch.setattr(
        preflight,
        "read_deployment_readiness",
        lambda *, force_refresh=False: readiness or _readiness(),
    )


def test_a_clean_source_requires_root_execution_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch)

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["summary"]["state"] == "unverified"
    assert payload["can_start"] is False
    assert payload["blockers"] == []
    assert [row["code"] for row in payload["unverified"]] == [
        "EXECUTION_VERIFICATION_REQUIRED"
    ]
    # 실행 주체는 언제나 SSH의 사람이다 — payload가 주는 것은 명령 문자열뿐이다.
    assert payload["command"].endswith("rebuild-pinned --confirm")
    # 화면(`PinnedRebuildPreflight`)과 같은 키 집합이다. journal 재개 경고(`warnings`)는
    # ADR-51 B3에서 지웠다.
    assert set(payload) == {
        "schema",
        "collected_at",
        "can_start",
        "pinset_sha256",
        "blockers",
        "unverified",
        "command",
        "summary",
    }


def test_a_legacy_terminal_requires_root_execution_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert payload["blockers"] == []
    assert [row["code"] for row in payload["unverified"]] == ["LEGACY_SOURCE_TERMINAL"]


def test_a_phase_scoped_block_is_not_read_as_a_legacy_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """phase가 있는 항목은 옛 journal 재개 단계의 기록이다(재개는 ADR-51에서 없어졌다).

    조건 없는 차단(`LEGACY_SOURCE_TERMINAL`)으로 읽지 않고 깨끗한 source와 같이 다룬다.
    """

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
    assert payload["can_start"] is False
    assert [row["code"] for row in payload["unverified"]] == [
        "EXECUTION_VERIFICATION_REQUIRED"
    ]


@pytest.mark.parametrize(
    ("generation", "expected_status", "expected_binding"),
    [
        (_generation(status="unknown", binding="unknown"), "unknown", "unknown"),
        (_generation(status="unverified", binding="unknown"), "unverified", "unknown"),
        # 결박이 받아들일 값이어도 사본 자체가 ok가 아니면 초록불을 주지 않는다.
        (
            _generation(status="unknown", binding="pending_rebuild"),
            "unknown",
            "pending_rebuild",
        ),
        (_generation(status="ok", binding="unknown"), "ok", "unknown"),
    ],
)
def test_an_invalid_public_generation_withholds_the_green_light(
    monkeypatch: pytest.MonkeyPatch,
    generation: dict[str, Any],
    expected_status: str,
    expected_binding: str,
) -> None:
    _patch(monkeypatch, generation=generation)

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["can_start"] is False
    assert payload["summary"]["state"] == "unverified"
    assert [row["code"] for row in payload["unverified"]] == [
        "EXECUTION_VERIFICATION_REQUIRED",
        "GENERATION_UNVERIFIED",
    ]
    assert f"status={expected_status}" in payload["unverified"][1]["text"]
    assert f"binding={expected_binding}" in payload["unverified"][1]["text"]


def test_a_valid_pending_generation_allows_the_new_pair_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, generation=_generation(binding="pending_rebuild"))

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["can_start"] is False
    assert [row["code"] for row in payload["unverified"]] == [
        "EXECUTION_VERIFICATION_REQUIRED"
    ]


def test_a_non_rebuildable_mode_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, mode=_OPERATIONAL)

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["can_start"] is False
    assert payload["summary"]["state"] == "blocked"
    assert [row["code"] for row in payload["blockers"]] == ["MODE_NOT_REBUILDABLE"]


@pytest.mark.parametrize(
    "failure",
    [
        DeploymentContractError("deployment environment/lifecycle pair is invalid"),
        FileNotFoundError(2, "No such file or directory", "/opt/x/.env"),
    ],
)
def test_an_unreadable_mode_withholds_the_green_light(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """모드를 못 읽었다는 것을 "재구축 가능"으로도 "불가"로도 읽지 않는다."""

    _patch(monkeypatch, mode=failure)

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["summary"]["state"] == "unverified"
    assert payload["can_start"] is False
    assert payload["blockers"] == []
    assert [row["code"] for row in payload["unverified"]] == [
        "EXECUTION_VERIFICATION_REQUIRED",
        "MODE_UNVERIFIABLE",
    ]
    assert str(failure) in payload["unverified"][1]["text"]


def _write_env(path: Path, *, environment: str, lifecycle: str) -> None:
    pinvi, required = (
        ("development", "false") if environment == "local" else ("production", "true")
    )
    path.write_text(
        f"KTDM_DEPLOYMENT_ENVIRONMENT={environment}\n"
        f"KTDM_DEPLOYMENT_LIFECYCLE={lifecycle}\n"
        f"PINVI_ENVIRONMENT={pinvi}\n"
        f"KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED={required}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("env_mode", "blockers", "unverified"),
    [
        (("rehearsal", "rebuildable"), [], ["EXECUTION_VERIFICATION_REQUIRED"]),
        (
            ("local", "development"),
            ["MODE_NOT_REBUILDABLE"],
            ["EXECUTION_VERIFICATION_REQUIRED"],
        ),
        (
            ("production", "operational"),
            ["MODE_NOT_REBUILDABLE"],
            ["EXECUTION_VERIFICATION_REQUIRED"],
        ),
        # `.env`가 없다 — 모드를 추측하지 않는다.
        (None, [], ["EXECUTION_VERIFICATION_REQUIRED", "MODE_UNVERIFIABLE"]),
    ],
)
def test_the_mode_is_read_from_the_real_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_mode: tuple[str, str] | None,
    blockers: list[str],
    unverified: list[str],
) -> None:
    """스텁 없이 실제 `read_deployment_mode`가 `.env`를 읽는 경로."""

    env_path = tmp_path / ".env"
    if env_mode is not None:
        _write_env(env_path, environment=env_mode[0], lifecycle=env_mode[1])
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", str(env_path))
    _patch(monkeypatch)
    # 나머지 관측은 스텁으로 두고 모드 reader만 실제 함수로 되돌린다.
    monkeypatch.setattr(
        preflight, "read_deployment_mode", deployment_readiness.read_deployment_mode
    )

    payload = preflight.read_pinned_rebuild_preflight()

    assert [row["code"] for row in payload["blockers"]] == blockers
    assert [row["code"] for row in payload["unverified"]] == unverified


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


def test_legacy_terminal_and_unverified_mode_stay_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UI public audit만으로 v6 실행권을 추측하지 않는다."""

    _patch(
        monkeypatch,
        mode=DeploymentContractError("deployment environment/lifecycle pair is invalid"),
        pins=_pins(
            blocked_pinsets=[{"pinset_sha256": PINSET, "phase": None, "reason": "t"}]
        ),
    )

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["summary"]["state"] == "unverified"


def test_the_entry_point_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """진단 route가 500을 내면 운영자는 상태를 볼 유일한 창을 잃는다."""

    def explode() -> dict[str, Any]:
        raise RuntimeError("호스트를 읽을 수 없음")

    monkeypatch.setattr(preflight, "read_published_runtime_pins", explode)
    monkeypatch.setattr(preflight, "read_published_pinned_runtime_generation", explode)
    monkeypatch.setattr(preflight, "read_deployment_mode", explode)
    monkeypatch.setattr(preflight, "read_deployment_readiness", explode)

    payload = preflight.read_pinned_rebuild_preflight()

    assert payload["summary"]["state"] == "unverified"
    assert payload["can_start"] is False


def test_the_module_never_executes_a_rebuild() -> None:
    """판정만 한다 — 이 모듈이 mutation 경로를 얻으면 경계가 사라진다."""

    source = Path(preflight.__file__).read_text(encoding="utf-8")
    for forbidden in ("subprocess", "rebuild_pinned_runtime", "compose_service"):
        assert forbidden not in source, forbidden


def test_force_refresh_reaches_the_readiness_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """카드와 그 위 사전 점검이 다른 스냅샷을 보면 모순된 판정이 나란히 뜬다."""

    seen: list[bool] = []

    def readiness(*, force_refresh: bool = False) -> dict[str, Any]:
        seen.append(force_refresh)
        return _readiness()

    monkeypatch.setattr(preflight, "read_published_runtime_pins", lambda: _pins())
    monkeypatch.setattr(
        preflight, "read_published_pinned_runtime_generation", lambda: _generation()
    )
    monkeypatch.setattr(preflight, "read_deployment_mode", _mode())
    monkeypatch.setattr(preflight, "read_deployment_readiness", readiness)

    preflight.read_pinned_rebuild_preflight(force_refresh=True)

    assert seen == [True]
