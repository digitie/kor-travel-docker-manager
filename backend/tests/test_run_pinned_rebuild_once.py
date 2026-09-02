"""`run-pinned-rebuild-once`의 소각 결정 표면 회귀 테스트.

이 launcher는 `ktdctl` 실행 **전에** `O_EXCL` claim을 쓰고, 실패하면 같은 pinset
재실행이 영구 거절된다 — 즉 rebuild 실패 = 회전 사이클 1회 손실(새 Map+PinVi
revision부터 다시). 그런데 이 파일을 **실제로 실행하는 테스트가 0건**이었다
(적대 감사).

여기서는 launcher tail(실행 이후 구간)을 잘라내 스텁 `ktdctl`과 함께 진짜 bash로
돌린다. 텍스트 단언이 아니라 **동작**을 본다.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_LAUNCHER = Path(__file__).resolve().parents[2] / "scripts/run-pinned-rebuild-once"

# durable journal 이전에 fail-close하는 stage — compose_service의 정본과 같아야 한다.
_PREJOURNAL_STAGES = frozenset(
    {
        "environment_admission",
        "state_initialization",
        "prebuild_snapshot",
        "external_prerequisites",
        "source_materialization",
        "application_base_images",
        "application_builder",
        "application_candidate",
        "candidate_snapshot",
        "candidate_contract",
    }
)


def test_prejournal_stage_literal_mirrors_the_service_canon() -> None:
    """launcher는 root 최소 신뢰 표면이라 backend를 import할 수 없다.

    그래서 stage 목록을 리터럴로 들고 있는데, 정본과 갈라지면 (a) 넓어지면 실제로
    소비된 실행을 재시도 허용으로 풀고 (b) 좁아지면 소비 안 한 후보를 계속 태운다.
    """

    from kor_travel_docker_manager.services.compose_service import (
        _PINNED_RUNTIME_PREJOURNAL_FAILURE_STAGES,
    )

    launcher = _LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("PREJOURNAL_STAGES = frozenset(")
    end = launcher.index(")", launcher.index("}", start)) + 1
    import re

    declared = set(re.findall(r'"([a-z_]+)"', launcher[start:end]))

    assert declared == set(_PINNED_RUNTIME_PREJOURNAL_FAILURE_STAGES)
    assert declared == set(_PREJOURNAL_STAGES)


def _tail(launcher: str) -> str:
    start = launcher.index('result_tmp="${output_dir}/.result.json.tmp"')
    return launcher[start:]


def _run_tail(
    tmp_path: Path, *, child_status: int, result: object, pinset: str = "a" * 64
) -> tuple[subprocess.CompletedProcess[str], Path]:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    claim = ledger / pinset
    claim.write_text("{}" + chr(10), encoding="utf-8")

    output = tmp_path / "out"
    output.mkdir()
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


def test_prejournal_failure_releases_the_claim_for_retry(tmp_path: Path) -> None:
    """CLI가 "아무것도 소비하지 않았다"를 명시하면 재시도를 허용해야 한다.

    종전에는 launcher가 `result.json`을 dict인지만 보고 버려서, `docker pull` 한
    번의 rate limit(`application_base_images`)이나 빌드 중 DNS 순간 장애
    (`application_builder`)가 회전 사이클 1회를 태웠다.
    """

    completed, claim = _run_tail(
        tmp_path,
        child_status=2,
        result={
            "status": "failed",
            "classification": "prejournal_failure",
            "stage": "application_base_images",
        },
    )

    assert completed.returncode == 2, completed.stderr
    assert not claim.exists(), "claim이 그대로면 같은 pinset 재실행이 영구 거절된다"
    released = list(claim.parent.glob(claim.name + ".prejournal-*"))
    assert len(released) == 1, "해제는 삭제가 아니라 개명이어야 한다(원장 보존)"
    assert "may be retried" in completed.stderr


@pytest.mark.parametrize(
    ("label", "child_status", "result"),
    [
        ("post_journal_failure", 1, {"status": "failed"}),
        ("unknown_classification", 2, {"status": "failed", "classification": "other"}),
        ("stage_outside_allowlist", 2, {"status": "failed", "classification": "prejournal_failure", "stage": "durable_journal"}),
        ("unparseable", 2, None),
        ("success", 0, {"status": "succeeded"}),
    ],
)
def test_claim_is_kept_without_positive_evidence(
    tmp_path: Path, label: str, child_status: int, result: object
) -> None:
    """양성 증거가 없으면 소각(=claim 유지)이 기본값이다 — 과도 완화 방지 가드."""

    completed, claim = _run_tail(tmp_path, child_status=child_status, result=result)

    assert completed.returncode == child_status, completed.stderr
    assert claim.exists(), label
    assert not list(claim.parent.glob(claim.name + ".prejournal-*")), label


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


def test_burn_decision_commands_use_absolute_paths() -> None:
    """소각·신뢰 판정을 PATH에 맡기지 않는다(형제 launcher와 대칭)."""

    launcher = _LAUNCHER.read_text(encoding="utf-8")
    import re

    for command in ("python3", "install", "chown", "chmod", "mv"):
        bare = re.search(r"(?m)^[ ]*" + command + r"[ ]", launcher)
        assert bare is None, f"{command}가 PATH에 의존한다: {bare.group(0) if bare else ''}"
