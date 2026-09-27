"""실패한 외부 명령의 **stdout**도 실패 텍스트에 남는지 본다.

하네스는 실패 명령의 stderr만 잡았다. 그런데 많은 러너는 진짜 진단을 stdout으로
낸다 — Playwright는 어느 spec의 어떤 단언이 깨졌는지를 거기 쓰고, stderr에는
npm의 lifecycle 오류(`command failed`, `code 1`)만 남는다.

2026-09-03 e2e22가 그래서 1시간 39분을 태우고
`M05 live attestation failed: M04 live UI command exited with 1` 하나만 남겼다.
어느 테스트가 왜 깨졌는지는 통째로 사라졌고, 다음 시도는 눈을 가린 채 같은
1.5시간을 다시 써야 했다.

ADR-51 잃는 보장 G-3부터 캡처는 항상 켜져 있고, 두 스트림은 가린 실패 텍스트 하나로
stderr에 간다(launcher가 root 0600 `stderr.log`로 받는다). 여기서는 텍스트가 아니라
**동작**을 본다 — 진짜 하위 프로세스를 실패시킨다.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "m05_isolated_e2e.py"

pytestmark = pytest.mark.skipif(
    not hasattr(os, "getuid"), reason="driver는 POSIX 프로세스 모델을 전제한다"
)


def _harness() -> Any:
    spec = importlib.util.spec_from_file_location("_m05_isolated_e2e", _HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._SECRET_ENV_FILE = Path("/nonexistent/m05-driver-test.env")
    return module


def test_a_failing_command_carries_its_stdout_without_any_flag() -> None:
    """`_command`가 실패하면 stdout 바이트가 예외에 실려야 한다 — 환경 변수 없이."""
    module = _harness()

    with pytest.raises(module._PhaseError) as raised:
        module._command(
            sys.executable,
            "-c",
            "import sys; print('the assertion that broke'); sys.exit(3)",
        )
    error = raised.value
    assert error.phase == "runtime_command_failed"
    assert error.returncode == 3
    assert error.stdout is not None
    assert b"the assertion that broke" in error.stdout


def test_the_failure_text_carries_both_streams() -> None:
    """실패 텍스트 하나가 두 스트림을 같은 규칙으로 싣는다."""
    module = _harness()

    with pytest.raises(module._PhaseError) as raised:
        module._command(
            sys.executable,
            "-c",
            "import sys; print('the assertion that broke');"
            " print('npm lifecycle noise', file=sys.stderr); sys.exit(3)",
        )
    text = module._failure_text(raised.value, progress_phase="m04_attestation")

    assert text.startswith("M05 isolated run failed during m04_attestation")
    assert "--- stderr (tail) ---\nnpm lifecycle noise" in text
    assert "--- stdout (tail) ---\nthe assertion that broke" in text


def test_the_tail_keeps_the_last_line_of_a_huge_output() -> None:
    """앞 256 KiB가 아니라 끝을 남긴다 — 깨진 단언은 출력의 마지막에 있다."""
    module = _harness()
    limit = module._OUTPUT_TAIL_LIMIT

    with pytest.raises(module._PhaseError) as raised:
        module._command(
            sys.executable,
            "-c",
            f"import sys; print('x' * {limit * 2}); print('FAILED spec: the last line'); sys.exit(1)",
        )
    stdout = raised.value.stdout
    assert stdout is not None
    assert len(stdout) <= limit
    assert stdout.rstrip().endswith(b"FAILED spec: the last line")


def test_a_large_successful_output_is_never_a_failure() -> None:
    """캡처 상한은 실패 사유가 아니다 — 출력이 큰 성공 명령이 뒤집히면 안 된다."""
    module = _harness()

    assert module._command(
        sys.executable, "-c", f"print('x' * {module._OUTPUT_TAIL_LIMIT * 2})"
    ) == ""
