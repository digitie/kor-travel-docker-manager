"""claim 획득 판정의 동작 테스트.

**이 파일의 주제가 한 번 바뀌었다.** 종전에는 "죽은 실행이 남긴 claim을 되찾을
수 있는가"를 두 증인으로 판정했다 — registry가 이 pinset의 generation을 아직
`pending_rebuild`로 보고, 그 claim이 가리키는 output에 `result.json`이 없을 것.
그 판정을 만든 사고는 보존할 가치가 있다: 2026-09-03 rebuild-021이 60분을 태우고
시그널로 죽었을 때, registry는 generation이 오르지 않았다고 말하는데도 다음
실행이 `already claimed`로 거부됐다. 회전 사이클 하나가 아무 근거 없이 죽었다.

그런데 그 판정은 **모르는 것을 소비의 증거로 취급**했다. `result.json`의 존재는
"파괴적 단계가 돌았다"가 아니라 "launcher가 결론을 쓸 만큼 살아 있었다"만
증명한다. 그래서 호스트 설정 하나가 틀려 아무것도 배포하지 못한 실행도 결론을
남겼다는 이유로 같은 (map, pinvi) 쌍을 **영구히** 실행 불가능하게 만들었다 —
`pinset_sha256`이 그 쌍의 순수 함수라 재회전으로도 같은 이름에 착지하고,
registry는 동일 쌍 회전을 아예 거절하므로 탈출구가 "아무 커밋이나 새로
올린다"밖에 없었다(실측: classification `unclassified`, 즉 "무슨 일이 났는지
모른다"가 영구 소각 사유가 됐다).

3-DB 재생성은 이 파일이 막는 것이 아니다 — ADR-51 뒤로는 `--restart`를 명시한 실행만
DB를 지우고 그 밖의 배포는 멱등이다. 동시 실행은 전역 flock(G)이 막는다.
그래서 원장은 감사 흔적만 맡고, 파일명이 attempt 차원을 갖는다 — 형제 M05
launcher가 적대 리뷰 R1-S2에서 먼저 받은 개정과 같다. 여기서는 그 새 판정을
launcher 본문에서 잘라내 **실제로 실행한다**(텍스트 단언이 아니라 동작).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_LAUNCHER = Path(__file__).resolve().parents[2] / "scripts/run-pinned-rebuild-once"
_PINSET = "a" * 64


def _chooser() -> Callable[..., str]:
    """launcher 본문의 파일명 선택 함수를 그대로 실행 가능한 형태로 꺼낸다.

    launcher의 ledger 검사는 uid 0을 요구해 claim 블록 전체는 비-root 테스트에서
    돌릴 수 없다. 판정만 떼어 낸다(종전 이 파일과 같은 방식).
    """

    source = _LAUNCHER.read_text(encoding="utf-8")
    start = source.index("_LEDGER_CLAIM_ATTEMPT_LIMIT")
    end = source.index(chr(10) + "metadata = ledger_dir.lstat()", start)
    namespace: dict[str, Any] = {"os": os}
    exec(compile(source[start:end], str(_LAUNCHER), "exec"), namespace)  # noqa: S102
    return namespace["next_claim_filename"]


def _ledger(tmp_path: Path, *names: str) -> Path:
    ledger = tmp_path / "ledger"
    ledger.mkdir(exist_ok=True)
    for name in names:
        (ledger / name).write_text("{}" + chr(10), encoding="ascii")
    return ledger


def test_the_first_attempt_uses_the_bare_pinset_filename(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert _chooser()(str(ledger), _PINSET) == _PINSET


def test_a_dead_run_does_not_block_the_next_attempt(tmp_path: Path) -> None:
    """rebuild-021 회귀: 시그널로 죽은 실행의 claim이 다음 시도를 막지 않는다."""

    ledger = _ledger(tmp_path, _PINSET)
    assert _chooser()(str(ledger), _PINSET) == _PINSET + "-01"


def test_a_concluded_failure_does_not_burn_the_pair(tmp_path: Path) -> None:
    """**이 저장소가 실제로 겪은 사고의 회귀다.**

    종전 판정에서는 이전 실행이 결론을 남겼다는 사실 하나로 같은 쌍이 영구
    소각됐다 — 그 결론이 "아무것도 배포하지 못했다"여도 마찬가지였다. 이제
    `result.json`의 존재는 파일명 선택에 아무 영향도 주지 않는다.
    """

    ledger = _ledger(tmp_path, _PINSET)
    previous = tmp_path / "out-1"
    previous.mkdir()
    (previous / "result.json").write_text("{}", encoding="ascii")

    assert _chooser()(str(ledger), _PINSET) == _PINSET + "-01"


def test_ordinals_continue_from_the_highest_record(tmp_path: Path) -> None:
    """count가 아니라 max+1이라, 사람이 중간 항목을 지워도 충돌하지 않는다."""

    ledger = _ledger(tmp_path, _PINSET, _PINSET + "-01", _PINSET + "-03")
    assert _chooser()(str(ledger), _PINSET) == _PINSET + "-04"


def test_legacy_prejournal_records_are_ignored(tmp_path: Path) -> None:
    """`<pinset>.prejournal-NN`은 `.` 접두라 ordinal 계산에 들어가지 않는다."""

    ledger = _ledger(tmp_path, _PINSET + ".prejournal-01", _PINSET + ".prejournal-02")
    assert _chooser()(str(ledger), _PINSET) == _PINSET


def test_another_pinset_does_not_shift_the_ordinal(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "c" * 64, "c" * 64 + "-01")
    assert _chooser()(str(ledger), _PINSET) == _PINSET


@pytest.mark.parametrize("suffix", ["-ab", "-", "-001x"])
def test_unparsable_suffixes_are_ignored(tmp_path: Path, suffix: str) -> None:
    """원장에 사람이 남긴 메모가 ordinal을 밀지 않는다."""

    ledger = _ledger(tmp_path, _PINSET + suffix)
    assert _chooser()(str(ledger), _PINSET) == _PINSET


def test_the_attempt_limit_fails_closed(tmp_path: Path) -> None:
    """상한은 후보 예산이 아니라 폭주 방어다 — 넘으면 거절한다.

    ADR-51 뒤 같은 pair를 다시 돌리는 것은 정상 수렴이라, 상한이 작으면 몇 주 안에 같은
    pair가 영구히 막힌다. 그래서 하한도 함께 건다.
    """

    chooser = _chooser()
    limit = chooser.__globals__["_LEDGER_CLAIM_ATTEMPT_LIMIT"]
    assert limit >= 100
    names = [_PINSET] + [_PINSET + f"-{ordinal:02d}" for ordinal in range(1, limit)]
    ledger = _ledger(tmp_path, *names)
    with pytest.raises(SystemExit) as captured:
        chooser(str(ledger), _PINSET)
    assert "attempts exceeded the limit" in str(captured.value)


def test_the_launcher_no_longer_gates_on_registry_evidence() -> None:
    """죽은 증인 배관이 실제로 걷혔는지 본다 — 주석만 남고 코드가 남으면 안 된다.

    `generation_pinset_binding`은 이 판정에 대해 정보량이 0이었다(실행 전후로
    항상 같은 값이다). 재실행 허용의 정본은 registry의 차단 목록과 journal의
    phase 가드다.
    """

    source = _LAUNCHER.read_text(encoding="utf-8")
    assert "stale_claim_is_retakable" not in source
    assert "generation_binding" not in source
    assert "def next_claim_filename(" in source
    # 같은 ordinal을 계산한 동시 claim은 종전과 똑같이 거절해야 한다.
    assert 'raise SystemExit("pinned rebuild candidate was already claimed")' in source
