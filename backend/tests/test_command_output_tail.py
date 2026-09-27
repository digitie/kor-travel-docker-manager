"""실패한 명령 출력 tail(ADR-51 잃는 보장 G-2).

재구축 경로의 subprocess 실패는 원인 원문(끝부분)을 메시지에 싣는다. 가림은 출력 경계의
스크러버 하나가 맡는다 — 여기서는 싣는 규칙만 본다.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from kor_travel_docker_manager.services import database_runtime, pinned_runtime_sources
from kor_travel_docker_manager.services.errors import (
    COMMAND_OUTPUT_TAIL_BYTES,
    DeploymentContractError,
    command_output_tail,
)


@pytest.mark.parametrize("output", [None, "", "  \n", b"", b"\n"])
def test_empty_output_adds_nothing(output: str | bytes | None) -> None:
    assert command_output_tail("stderr", output) == ""


def test_short_output_is_carried_whole() -> None:
    assert command_output_tail("stderr", b"fatal: bad object\n") == (
        "\n--- stderr ---\nfatal: bad object"
    )


def test_long_output_keeps_the_end_and_drops_the_cut_line() -> None:
    """자른 첫 줄은 버린다 — 비밀 값의 뒷조각만 남으면 스크러버가 알아보지 못한다."""

    secret = "split-secret-value-0123456789"
    last = "ERROR: the actual cause"
    # 끝 16 KiB의 경계가 secret 한가운데를 지나도록 뒤를 채운다.
    kept_half = secret[len(secret) // 2 :]
    filler = "x" * (COMMAND_OUTPUT_TAIL_BYTES - len(kept_half) - 1 - len(last))
    output = "head line\n" + secret + filler + "\n" + last
    assert output.encode()[-COMMAND_OUTPUT_TAIL_BYTES:].startswith(kept_half.encode())

    tail = command_output_tail("stderr", output)

    assert tail.startswith("\n--- stderr (last 16 KiB) ---\n")
    assert tail.endswith(last)
    assert kept_half not in tail
    assert "head line" not in tail


def test_a_root_git_failure_carries_the_command_and_stderr() -> None:
    def runner(arguments: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            arguments,
            128,
            stdout="",
            stderr="fatal: remote error: upload-pack: not our ref " + "c" * 40,
        )

    with pytest.raises(DeploymentContractError) as captured:
        pinned_runtime_sources._run_root_git(
            ["--git-dir", "/tmp/repo.git", "fetch", "-q", "https://example.test/r.git", "c" * 40],
            runner=runner,
        )

    message = str(captured.value)
    assert "git --git-dir /tmp/repo.git fetch" in message
    assert "(exit 128)" in message
    assert "not our ref" in message


def test_a_checked_database_command_carries_stderr_not_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psql 조회 결과(stdout)는 원인이 아니다 — stderr만 싣는다."""

    monkeypatch.setattr(
        database_runtime.subprocess,
        "run",
        lambda arguments, **_kwargs: subprocess.CompletedProcess(
            arguments,
            2,
            stdout=b"query-result-stdout-marker",
            stderr=b'psql: error: connection to server on socket failed: FATAL:  role "x" does not exist',
        ),
    )

    with pytest.raises(DeploymentContractError) as captured:
        database_runtime._run_checked(["psql"], label="owner probe")

    message = str(captured.value)
    assert message.startswith("owner probe failed (exit 2)")
    assert 'role "x" does not exist' in message
    assert "query-result-stdout-marker" not in message
