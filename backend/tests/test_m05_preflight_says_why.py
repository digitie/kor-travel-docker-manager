"""preflight가 거부할 때 **이유를 말하는지** 본다.

`preflight()`의 독스트링은 "거부 이유를 stdout으로 낸다"고 약속한다. 그런데
`_PhaseError`가 아닌 예외를 받는 분기는 아무것도 출력하지 않고 exit 1만 냈다.
그래서 launcher는 `M05 isolated source pair preflight is not runnable:` 뒤에
빈칸을 찍었다.

2026-09-03 e2e23이 그 침묵으로 죽었고, 계측 스크립트를 따로 붙여서야 진짜 사유
(당시 문구 `pinned runtime source worktree is unsafe` — 앞선 실행이 불변 핀 소스 트리에
`node_modules`를 쓴 것)를 알 수 있었다. 그 왕복이 한 사이클을 더 썼다.

ADR-51 잃는 보장 G-3부터는 닫힌 어휘로 거르지 않는다. 어떤 예외든 타입 이름과 **가린
첫 줄**을 낸다 — launcher가 이 줄을 journald로 옮기므로 요약만 낸다.
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
    module._SECRET_ENV_FILE = Path("/nonexistent/m05-driver-test.env")
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
    """Manager가 쓴 문구를 그대로 낸다."""
    module = _harness()
    _refusing_preflight(
        module, monkeypatch, RuntimeError("pinned runtime source Git operation failed")
    )

    assert module.preflight("a" * 40) == 1
    printed = capsys.readouterr().out.strip()
    assert "source_materialization" in printed
    assert "pinned runtime source Git operation failed" in printed


def test_an_unknown_message_is_printed_scrubbed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """어휘 밖 문구도 낸다(e2e23은 그 침묵으로 죽었다) — 비밀만 가리고."""
    module = _harness()
    monkeypatch.setenv("KTDM_TEST_PREFLIGHT_SECRET", "preflight-secret-0042")
    _refusing_preflight(
        module,
        monkeypatch,
        OSError("/home/someone/state is missing near preflight-secret-0042"),
    )

    assert module.preflight("a" * 40) == 1
    printed = capsys.readouterr().out.strip()
    assert printed == (
        "source_materialization: OSError: /home/someone/state is missing near <redacted>"
    )


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


def test_only_the_first_line_of_a_multi_line_message_is_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-51 G-2부터 source 문구 둘째 줄에 명령(경로)과 git 원문 tail이 붙는다 — 첫 줄만 낸다."""
    module = _harness()
    _refusing_preflight(
        module,
        monkeypatch,
        RuntimeError(
            "pinned runtime source Git operation failed (exit 128)\n--- command ---\n"
            "git --git-dir /var/lib/kor-travel-docker-manager/state/repo.git fetch\n"
            "--- stderr ---\nfatal: unable to access host-detail"
        ),
    )

    assert module.preflight("a" * 40) == 1
    printed = capsys.readouterr().out.strip()
    assert printed == (
        "source_materialization: RuntimeError: "
        "pinned runtime source Git operation failed (exit 128)"
    )
