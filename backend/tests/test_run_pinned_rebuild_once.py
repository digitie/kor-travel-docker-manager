"""`run-pinned-rebuild-once`의 실행 이후 구간 회귀 테스트.

launcher는 `ktdctl` 실행 전에 감사용 claim을 쓴다. ADR-51 뒤로 같은 pinset의 재실행은
다음 ordinal을 받으므로 claim을 해제할 일이 없다(G-2에서 해제 경로를 지웠다). 남은 일은
result·stderr를 root 0600으로 옮기고 자식 종료값을 그대로 전달하는 것이다.

launcher tail을 잘라내 스텁 `ktdctl`과 함께 진짜 bash로 돌린다. 텍스트 단언이 아니라
**동작**을 본다.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_LAUNCHER = Path(__file__).resolve().parents[2] / "scripts/run-pinned-rebuild-once"

def _tail(launcher: str) -> str:
    start = launcher.index('result_tmp="${output_dir}/.result.json.tmp"')
    return launcher[start:]


def _run_tail(
    tmp_path: Path,
    *,
    child_status: int,
    result: object,
    pinset: str = "a" * 64,
    output_name: str = "out",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    ledger = tmp_path / "ledger"
    ledger.mkdir(exist_ok=True)
    claim = ledger / pinset
    if not claim.exists():
        claim.write_text("{}" + chr(10), encoding="utf-8")

    output = tmp_path / output_name
    output.mkdir(exist_ok=True)
    body = "" if result is None else json.dumps(result)

    tail = _tail(_LAUNCHER.read_text(encoding="utf-8"))
    # 실제 ktdctl 대신 스텁을 넣는다. 나머지 판정 로직은 원문 그대로 돈다.
    stub = (
        'printf "%s" "$STUB_BODY" >"${result_tmp}"' + chr(10) + 'status=$STUB_STATUS'
    )
    marker_start = tail.index("set +e" + chr(10) + "/opt/kor-travel-docker-manager")
    marker_end = tail.index('status="$?"' + chr(10) + "set -e") + len('status="$?"' + chr(10) + "set -e")
    tail = tail[:marker_start] + stub + tail[marker_end:]
    # 비-root 테스트에서 돌 수 있게 소유권 조정만 완화한다.
    tail = tail.replace('/usr/bin/chown root:root "${result_tmp}" "${stderr_path}"', "true")

    script = tmp_path / "tail.sh"
    script.write_text(
        chr(10).join(
            [
                "set -euo pipefail",
                'output_dir="$OUT"',
                'ledger_dir="$LEDGER"',
                'installed_pinset="$PINSET"',
                'touch "$output_dir/stderr.log"',
                tail,
            ]
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "OUT": str(output),
            "LEDGER": str(ledger),
            "PINSET": pinset,
            "STUB_BODY": body,
            "STUB_STATUS": str(child_status),
        },
    )
    return completed, claim


@pytest.mark.parametrize(
    ("label", "child_status", "result"),
    [
        # 옛 CLI가 내던 해제 신호다. 새 launcher는 이것도 claim을 건드리지 않는다.
        (
            "legacy_prejournal_payload",
            2,
            {
                "status": "failed",
                "classification": "prejournal_failure",
                "stage": "application_base_images",
            },
        ),
        ("staged_failure", 2, {"status": "failed", "stage": "candidate_compose_build"}),
        ("unstaged_failure", 2, {"status": "failed"}),
        ("unparseable", 2, None),
        ("success", 0, {"status": "succeeded"}),
    ],
)
def test_the_claim_is_never_released(
    tmp_path: Path, label: str, child_status: int, result: object
) -> None:
    """결과가 무엇이든 claim은 그 자리에 남고 해제 흔적도 없다(ADR-51 G-2).

    같은 pinset 재실행은 다음 ordinal을 받으므로 해제가 필요 없다. 원인은 stderr.log에 있다.
    """

    completed, claim = _run_tail(tmp_path, child_status=child_status, result=result)

    assert completed.returncode == child_status, completed.stderr
    assert claim.exists(), label
    assert sorted(item.name for item in claim.parent.iterdir()) == [claim.name], label
    assert not (tmp_path / "out" / "claim-released").exists(), label
    result_path = tmp_path / "out" / "result.json"
    assert result_path.is_file(), label
    assert oct(result_path.stat().st_mode)[-3:] == "600", label
    assert oct((tmp_path / "out" / "stderr.log").stat().st_mode)[-3:] == "600", label


@pytest.mark.parametrize("child_status", [126, 127, 137, 143])
def test_child_exit_status_survives_an_unreadable_result(
    tmp_path: Path, child_status: int
) -> None:
    """자식 종료값이 가장 강한 증거다 — JSON 검증기가 그걸 덮으면 안 된다.

    종전에는 검증기가 `set -e` 아래 있어, ktdctl이 stdout을 못 남기면
    `json.loads("")`가 먼저 죽어 126/127(기동 실패)·137(OOM-kill)·143(SIGTERM)이
    전부 exit 1 + raw traceback으로 접혔다.
    """

    completed, _claim = _run_tail(tmp_path, child_status=child_status, result=None)

    assert completed.returncode == child_status, completed.stderr
    assert "Traceback (most recent call last)" not in completed.stderr, completed.stderr
    assert "not a JSON object" in completed.stderr


def test_launcher_commands_use_absolute_paths() -> None:
    """claim·결과 처리를 PATH에 맡기지 않는다(형제 launcher와 대칭)."""

    launcher = _LAUNCHER.read_text(encoding="utf-8")
    import re

    for command in ("python3", "install", "chown", "chmod", "mv", "id", "stat"):
        bare = re.search(r"(?m)(^|[^/\w])" + command + r"[ ]-", launcher)
        assert bare is None, f"{command}가 PATH에 의존한다: {bare.group(0) if bare else ''}"


@pytest.mark.parametrize("body", [None, [1, 2, 3]])
def test_successful_child_with_an_unreadable_result_fails_closed(
    tmp_path: Path, body: object
) -> None:
    """`exit 0 ⇒ result.json은 JSON object`라는 불변식은 유지돼야 한다.

    검증기를 `set -e` 밖으로 뺀 것은 자식의 **실패** 종료값(126/127/137/143)을
    보존하기 위해서였다. 그 과정에서 자식이 성공을 주장하는 경우의 fail-close까지
    없애면, 1~2시간 rebuild 뒤 비가역 단계로 넘어가는 판단 지점에서 조용히
    안전장치가 하나 사라진다(적대 리뷰 M-1).
    """

    completed, _claim = _run_tail(tmp_path, child_status=0, result=body)

    assert completed.returncode == 1, completed.stderr
    assert "not a JSON object" in completed.stderr


# --- `--adopt-live-databases REASON` 통과(M1 a) ------------------------------------------
#
# launcher 앞머리(인자·사유 검증)와 꼬리(ktdctl 실행)를 이어 붙여 진짜 bash로 돌린다. 사이의
# root·설치본·원장 구간은 호스트 전제라 건너뛴다. ktdctl 자리에는 argv를 NUL로 기록하는 스텁을
# 넣는다 — 텍스트가 아니라 **실제로 넘어간 argv**를 본다.

_ROOT_CHECK = 'if [[ "$(/usr/bin/id -u)" != "0" ]]; then'
_KTDCTL = "/opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl"
_LOCK_EXEC = '  exec /usr/bin/python3 -I -S - "${BASH_SOURCE[0]}" "$@" <<\'PY\''
_REVISION = "a" * 40


def _recorded_ktdctl_argv(
    tmp_path: Path, *arguments: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    launcher = _LAUNCHER.read_text(encoding="utf-8")
    head = launcher[: launcher.index(_ROOT_CHECK)]
    tail = _tail(launcher)
    assert tail.count(_KTDCTL) == 1
    tail = tail.replace(_KTDCTL, '/usr/bin/bash "$KTDCTL_RECORDER"')
    tail = tail.replace('/usr/bin/chown root:root "${result_tmp}" "${stderr_path}"', "true")
    recorder = tmp_path / "ktdctl-recorder"
    argv_path = tmp_path / "argv.bin"
    # `/tmp`가 noexec일 수 있어 실행 비트 대신 bash로 부른다.
    recorder.write_text(
        'printf "%s\\0" "$@" >"$ARGV_OUT"\n' "printf '{}'\n",
        encoding="utf-8",
    )
    output = tmp_path / "out"
    output.mkdir()
    script = tmp_path / "head-tail.sh"
    script.write_text(
        head + 'output_dir="$OUT"\n' 'touch "$output_dir/stderr.log"\n' + tail,
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["bash", str(script), *arguments],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "OUT": str(output),
            "KTDCTL_RECORDER": str(recorder),
            "ARGV_OUT": str(argv_path),
        },
    )
    argv = argv_path.read_bytes().split(b"\0")[:-1] if argv_path.exists() else []
    return completed, [item.decode("utf-8") for item in argv]


def _run_until_the_lock(
    tmp_path: Path, *arguments: str
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """launcher 원문을 lock 직전까지 **root인 척** 돌린다. lock에 닿으면 표식을 남기고 97로 끝난다.

    root 검사만 무력화한다 — 비-root 테스트에서 root 검사가 먼저 exit 2를 내면 사유 검증이
    lock보다 앞선지 뒤선지 구분되지 않는다(탐지기가 초록으로 공허해진다).
    """

    launcher = _LAUNCHER.read_text(encoding="utf-8")
    assert launcher.count(_ROOT_CHECK) == 1
    assert launcher.count(_LOCK_EXEC) == 1
    marker = tmp_path / "lock-taken"
    script = tmp_path / "until-lock.sh"
    script.write_text(
        launcher.replace(_ROOT_CHECK, "if false; then").replace(
            _LOCK_EXEC, '  touch "$LOCK_MARKER"; exit 97\n  : <<\'PY\''
        ),
        encoding="utf-8",
    )
    output = tmp_path / "never-created"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "KTDM_PINNED_REBUILD_GLOBAL_LOCK_FD"
    }
    completed = subprocess.run(
        ["bash", str(script), _REVISION, str(output), *arguments],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**environment, "LOCK_MARKER": str(marker)},
    )
    return completed, marker, output


def test_adopt_pass_through_reaches_ktdctl_with_reason(tmp_path: Path) -> None:
    reason = "Map DB를 공용 instance로 옮긴다 (ADR-53)"

    completed, argv = _recorded_ktdctl_argv(
        tmp_path, _REVISION, str(tmp_path / "out"), "--adopt-live-databases", reason
    )

    assert completed.returncode == 0, completed.stderr
    assert argv == [
        "pinvi-pair",
        "rebuild-pinned",
        "--confirm",
        "--json",
        "--adopt-live-databases",
        "--reason",
        reason,
    ]


def test_two_argument_form_is_unchanged(tmp_path: Path) -> None:
    completed, argv = _recorded_ktdctl_argv(tmp_path, _REVISION, str(tmp_path / "out"))

    assert completed.returncode == 0, completed.stderr
    assert b"\0".join(item.encode() for item in argv) == (
        b"pinvi-pair\0rebuild-pinned\0--confirm\0--json"
    )


@pytest.mark.parametrize(
    "reason",
    [
        "",
        "two\nlines",
        "carriage\rreturn",
        "tab\there",
        "escape\x1b[31m",
        "x" * 201,
        "--restart",
        "-x",
    ],
    ids=["empty", "multiline", "cr", "tab", "escape", "overlong", "restart-flag", "dash"],
)
def test_adopt_reason_rejects_multiline_control_and_overlong(
    tmp_path: Path, reason: str
) -> None:
    completed, marker, output = _run_until_the_lock(tmp_path, "--adopt-live-databases", reason)

    assert completed.returncode == 2, completed.stderr
    assert "adopt reason must be" in completed.stderr
    assert not marker.exists()
    assert not output.exists()


def test_a_valid_adopt_reason_reaches_the_lock(tmp_path: Path) -> None:
    """위 거부 검사의 대조군이다 — 같은 대역이 유효한 사유로는 lock에 닿는다(200자 경계 포함)."""

    completed, marker, output = _run_until_the_lock(
        tmp_path, "--adopt-live-databases", "가" * 200
    )

    assert completed.returncode == 97, completed.stderr
    assert marker.exists()
    assert not output.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ("--restart", "move"),
        ("--restart",),
        ("--adopt-live-databases",),
        ("--adopt-live-databases", "move", "--restart"),
        ("--adopt-live-databases", "move", "--restart", "again"),
        ("--reason", "move"),
    ],
)
def test_restart_is_not_accepted_by_the_launcher(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    completed, marker, output = _run_until_the_lock(tmp_path, *arguments)

    assert completed.returncode == 2, completed.stderr
    assert completed.stderr.startswith("usage: run-pinned-rebuild-once")
    assert not marker.exists()
    assert not output.exists()
