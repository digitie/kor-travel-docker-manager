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
