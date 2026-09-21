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
    marker = tmp_path / "out" / "claim-released"
    assert marker.is_file(), "해제 사실이 output leaf에서 판독 가능해야 한다"
    assert oct(marker.stat().st_mode)[-3:] == "600"
    assert "may be retried" in completed.stderr


@pytest.mark.parametrize(
    ("label", "child_status", "result"),
    [
        ("post_journal_failure", 1, {"status": "failed"}),
        ("unknown_classification", 2, {"status": "failed", "classification": "other"}),
        # 봉인된 실패라도 journal이 이미 있으면 CLI가 이 값을 낸다. launcher는
        # `prejournal_failure`에서만 해제하므로 여기서도 유지여야 한다.
        (
            "postjournal_failure",
            2,
            {
                "status": "failed",
                "classification": "postjournal_failure",
                "stage": "external_prerequisites",
            },
        ),
        ("unparseable", 2, None),
        # 아래 셋은 해제 술어의 각 연접을 단독으로 판별한다 — 하나만 지워도
        # 잡히도록(적대 리뷰 M-2: 종전 표는 전부 다른 이유로 먼저 걸러졌다).
        (
            "status_not_failed",
            2,
            {"status": "succeeded", "classification": "prejournal_failure"},
        ),
        (
            "classification_unclassified",
            2,
            {"status": "failed", "classification": "unclassified"},
        ),
        (
            "matching_payload_wrong_exit",
            3,
            {"status": "failed", "classification": "prejournal_failure"},
        ),
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

    for command in ("python3", "install", "chown", "chmod", "mv", "id", "stat"):
        bare = re.search(r"(?m)(^|[^/\w])" + command + r"[ ]-", launcher)
        assert bare is None, f"{command}가 PATH에 의존한다: {bare.group(0) if bare else ''}"


def test_repeated_prejournal_failures_preserve_every_record(tmp_path: Path) -> None:
    """두 번째 prejournal 실패가 첫 기록을 덮어쓰면 안 된다.

    `os.rename`은 POSIX에서 대상을 **조용히 덮어쓴다** — `FileExistsError`로 빈
    슬롯을 찾는 형태는 동작하지 않는다(실측 확인). 원장은 "이 pinset이 몇 번
    시도됐는가"의 유일한 증거이므로 기록을 잃으면 감사가 무너진다.
    """

    pinset = "b" * 64
    prejournal = {
        "status": "failed",
        "classification": "prejournal_failure",
        "stage": "application_builder",
    }

    first, claim = _run_tail(tmp_path, child_status=2, result=prejournal, pinset=pinset)
    assert first.returncode == 2, first.stderr
    ledger = claim.parent
    assert sorted(item.name for item in ledger.iterdir()) == [f"{pinset}.prejournal-01"]

    # 같은 원장에 두 번째 시도를 얹는다(새 claim을 쓰고 다시 실패).
    claim.write_text("{}" + chr(10), encoding="utf-8")
    second_output = tmp_path / "out2"
    second_output.mkdir()
    second, _claim = _run_tail(
        tmp_path, child_status=2, result=prejournal, pinset=pinset, output_name="out2"
    )
    assert second.returncode == 2, second.stderr

    records = sorted(item.name for item in ledger.iterdir())
    assert records == [f"{pinset}.prejournal-01", f"{pinset}.prejournal-02"], records


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


# ── claim 획득 표면 ────────────────────────────────────────────────────────
#
# 이 구간은 회귀 테스트가 **0건**이었고, 그래서 "파일명이 순수 content hash라
# attempt 차원이 없다"는 결함이 오래 살아남았다. 후보 내용과 무관한 호스트 실패
# 하나가 같은 (map, pinvi) 쌍을 영구히 실행 불가능하게 만들었다 — registry가
# 동일 쌍 회전을 거절하므로 재회전으로도 빠져나갈 수 없었다.


def _claim_block(launcher: str) -> str:
    """claim을 획득하는 python heredoc 본문만 잘라낸다."""

    anchor = launcher.index("def next_claim_filename(")
    start = launcher.rindex("<<" + chr(39) + "PY" + chr(39), 0, anchor)
    start = launcher.index(chr(10), start) + 1
    end = launcher.index(chr(10) + "PY" + chr(10), start)
    return launcher[start:end]


def _acquire_claim(ledger: Path, pinset: str, output_dir: str):
    block = _claim_block(_LAUNCHER.read_text(encoding="utf-8"))
    # 비-root 테스트에서 돌 수 있게 원장 소유권 단언만 완화한다(위 tail 테스트가
    # chown을 `true`로 바꾸는 것과 같은 이유). 나머지 판정은 원문 그대로 돈다.
    block = block.replace("    or metadata.st_uid != 0" + chr(10), "", 1)
    return subprocess.run(
        [
            "python3",
            "-I",
            "-S",
            "-c",
            block,
            str(ledger),
            pinset,
            "b" * 40,
            output_dir,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_first_claim_uses_the_bare_pinset_filename(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir(mode=0o700)
    pinset = "c" * 64

    completed = _acquire_claim(ledger, pinset, str(tmp_path / "out"))

    assert completed.returncode == 0, completed.stderr
    assert (ledger / pinset).exists()
    recorded = json.loads((ledger / pinset).read_text(encoding="utf-8"))
    # 파일명만 attempt 차원을 갖는다 — claim 내용은 identity-pure로 남는다.
    assert recorded["pinset_sha256"] == pinset


def test_a_concluded_failure_does_not_burn_the_pair(tmp_path: Path) -> None:
    """**이 저장소가 실제로 겪은 사고의 회귀다.**

    이전 실행이 결론(result.json)을 썼다는 이유만으로 같은 쌍이 영구 소각됐다.
    그 결론이 "호스트 설정이 틀려서 아무것도 배포하지 못했다"여도 마찬가지였고,
    탈출구는 아무 커밋이나 새로 올리는 것뿐이었다.
    """

    ledger = tmp_path / "ledger"
    ledger.mkdir(mode=0o700)
    pinset = "d" * 64
    previous = tmp_path / "out-1"
    previous.mkdir()
    (ledger / pinset).write_text("{}" + chr(10), encoding="utf-8")
    # 이전 실행이 결론을 남겼다 — 종전에는 이것이 재시도를 영구히 막았다.
    (previous / "result.json").write_text(
        json.dumps({"status": "failed", "classification": "unclassified"}),
        encoding="utf-8",
    )

    completed = _acquire_claim(ledger, pinset, str(tmp_path / "out-2"))

    assert completed.returncode == 0, completed.stderr
    assert (ledger / (pinset + "-01")).exists()
    # 앞선 기록은 감사 흔적으로 남는다 — 되찾기가 아니라 덧쓰기다.
    assert (ledger / pinset).exists()


def test_legacy_prejournal_records_do_not_shift_the_ordinal(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir(mode=0o700)
    pinset = "e" * 64
    (ledger / (pinset + ".prejournal-01")).write_text("{}" + chr(10), encoding="utf-8")
    (ledger / (pinset + ".prejournal-02")).write_text("{}" + chr(10), encoding="utf-8")

    completed = _acquire_claim(ledger, pinset, str(tmp_path / "out"))

    assert completed.returncode == 0, completed.stderr
    # `.` 접두는 ordinal 계산에 들어가지 않는다.
    assert (ledger / pinset).exists()


def test_claim_attempts_are_capped(tmp_path: Path) -> None:
    """상한은 후보 예산이 아니라 폭주 방어다 — 넘으면 fail-closed."""

    ledger = tmp_path / "ledger"
    ledger.mkdir(mode=0o700)
    pinset = "f" * 64
    (ledger / pinset).write_text("{}" + chr(10), encoding="utf-8")
    for ordinal in range(1, 12):
        (ledger / (pinset + f"-{ordinal:02d}")).write_text("{}" + chr(10), encoding="utf-8")

    completed = _acquire_claim(ledger, pinset, str(tmp_path / "out"))

    assert completed.returncode != 0
    assert "attempts exceeded the limit" in completed.stderr
