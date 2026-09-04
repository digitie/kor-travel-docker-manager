"""preflight가 거부할 때 **이유를 말하는지** 본다.

`preflight()`의 독스트링은 "거부 이유를 stdout으로 낸다"고 약속한다. 그런데
`_PhaseError`가 아닌 예외를 받는 분기는 아무것도 출력하지 않고 exit 1만 냈다.
그래서 launcher는 `M05 isolated source pair preflight is not runnable:` 뒤에
빈칸을 찍었다.

2026-09-03 e2e23이 그 침묵으로 죽었고, 계측 스크립트를 따로 붙여서야 진짜 사유
(`pinned runtime source worktree is unsafe` — 앞선 실행이 불변 핀 소스 트리에
`node_modules`를 쓴 것)를 알 수 있었다. 그 왕복이 한 사이클을 더 썼다.

내용은 여전히 닫아 둔다. 예외 **타입 이름**은 호스트 상태를 담지 않으므로 항상
낼 수 있고, 메시지는 Manager 자신이 쓴 고정 문구일 때만 낸다. 문구를 열거하지
않고 접두로 거르므로 새 문구가 생겨도 드리프트하지 않는다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "m05_isolated_e2e.py"


def _harness() -> Any:
    spec = importlib.util.spec_from_file_location("_m05_isolated_e2e", _HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _refusing_preflight(
    module: Any, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    monkeypatch.setattr(module, "_validate_trusted_release", lambda _revision: None)
    monkeypatch.setattr(
        module, "_assert_current_m05_execution_is_runnable", lambda _revision: None
    )

    def _raise() -> None:
        raise error

    monkeypatch.setattr(module, "_source_pair_preflight", _raise)


def test_a_contract_refusal_names_its_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Manager 자신이 쓴 고정 문구는 그대로 낸다."""
    module = _harness()
    _refusing_preflight(
        module, monkeypatch, RuntimeError("pinned runtime source worktree is unsafe")
    )

    assert module.preflight("a" * 40) == 1
    printed = capsys.readouterr().out.strip()
    assert "source_materialization" in printed
    assert "pinned runtime source worktree is unsafe" in printed


def test_an_unknown_message_still_names_the_exception_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """모르는 문구는 내지 않되 **침묵하지도 않는다.**"""
    module = _harness()
    _refusing_preflight(
        module, monkeypatch, OSError("/home/someone/secret-path/state is missing")
    )

    assert module.preflight("a" * 40) == 1
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("source_materialization: OSError")
    # 호스트 경로는 나가지 않는다.
    assert "/home/" not in printed
    assert "secret-path" not in printed


def test_the_refusal_is_never_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """어떤 예외든 stdout에 무언가는 남아야 한다 — 이것이 e2e23을 죽인 결함이다."""
    module = _harness()
    for error in (
        OSError("boom"),
        RuntimeError("boom"),
        ValueError("boom"),
    ):
        _refusing_preflight(module, monkeypatch, error)
        assert module.preflight("a" * 40) == 1
        assert capsys.readouterr().out.strip() != "", type(error).__name__


def test_the_prefix_matches_the_manager_source_literals() -> None:
    """접두가 실제 문구들과 맞는지 확인한다 — 틀리면 게이트가 공허해진다."""
    module = _harness()
    sources = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "kor_travel_docker_manager"
        / "services"
        / "pinned_runtime_sources.py"
    ).read_text(encoding="utf-8")
    assert f'DeploymentContractError("{module._SOURCE_DIAGNOSTIC_PREFIX}' in sources
