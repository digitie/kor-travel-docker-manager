"""실패한 외부 명령의 **stdout**도 증거로 남는지 본다.

하네스는 실패 명령의 stderr만 잡았다. 그런데 많은 러너는 진짜 진단을 stdout으로
낸다 — Playwright는 어느 spec의 어떤 단언이 깨졌는지를 거기 쓰고, stderr에는
npm의 lifecycle 오류(`command failed`, `code 1`)만 남는다.

2026-09-03 e2e22가 그래서 1시간 39분을 태우고
`M05 live attestation failed: M04 live UI command exited with 1` 하나만 남겼다.
어느 테스트가 왜 깨졌는지는 통째로 사라졌고, 다음 시도는 눈을 가린 채 같은
1.5시간을 다시 써야 했다.

여기서는 텍스트가 아니라 **동작**을 본다 — 진짜 하위 프로세스를 실패시키고
증거 leaf를 읽는다.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

_HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "m05_isolated_e2e.py"

pytestmark = pytest.mark.skipif(
    not hasattr(os, "getuid"), reason="증거 leaf는 root 소유 POSIX 파일을 요구한다"
)


def _harness() -> Any:
    spec = importlib.util.spec_from_file_location("_m05_isolated_e2e", _HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_failing_command_carries_its_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_command`가 실패하면 stdout 바이트가 예외에 실려야 한다."""
    module = _harness()
    monkeypatch.setenv(module._FORENSIC_CAPTURE_ENV, "1")

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


def test_evidence_leaf_records_both_streams(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """증거 writer가 두 스트림을 같은 규칙으로 남긴다."""
    module = _harness()
    monkeypatch.setenv(module._FORENSIC_CAPTURE_ENV, "1")

    receipt = tmp_path / "failed-thing-command.json"
    module._write_command_failure_evidence(
        receipt,
        returncode=3,
        stderr=b"npm lifecycle noise",
        stdout=b"the assertion that broke",
    )
    assert receipt.exists()
    stdout_leaf = receipt.with_suffix(".stdout")
    stderr_leaf = receipt.with_suffix(".stderr")
    assert b"the assertion that broke" in stdout_leaf.read_bytes()
    assert b"npm lifecycle noise" in stderr_leaf.read_bytes()
    for leaf in (stdout_leaf, stderr_leaf):
        assert stat.S_IMODE(leaf.lstat().st_mode) == 0o600, leaf


def test_forensic_capture_off_writes_neither_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """opt-in 경계는 그대로다 — forensic이 아니면 원문을 남기지 않는다."""
    module = _harness()
    monkeypatch.delenv(module._FORENSIC_CAPTURE_ENV, raising=False)

    receipt = tmp_path / "failed-thing-command.json"
    module._write_command_failure_evidence(
        receipt, returncode=3, stderr=b"noise", stdout=b"detail"
    )
    assert receipt.exists()
    assert not receipt.with_suffix(".stdout").exists()
    assert not receipt.with_suffix(".stderr").exists()


def test_evidence_capture_never_turns_a_large_success_into_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """증거용 상한은 실패 사유가 아니다.

    forensic 모드에서 stdout을 잡되, 그 상한을 넘겼다고
    `runtime_command_output_too_large`로 뒤집으면 출력이 큰 성공 명령이 실패한다.
    """
    module = _harness()
    monkeypatch.setenv(module._FORENSIC_CAPTURE_ENV, "1")
    monkeypatch.setattr(module, "_FORENSIC_CAPTURE_LIMIT", 64)

    # 상한보다 큰 출력을 내고 **성공**하는 명령.
    module._command(
        sys.executable,
        "-c",
        "print('x' * 4096)",
    )
