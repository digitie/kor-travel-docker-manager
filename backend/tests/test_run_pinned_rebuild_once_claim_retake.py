"""죽은 실행이 남긴 claim을 되찾는 판정의 동작 테스트.

소각(=claim 유지)은 실행권을 **소비했다는 양성 증거**가 있을 때만 정당하다.
그런데 종전 launcher에서 해제는 자기 프로세스가 살아서 결과를 분류할 때만
일어났다. 프로세스 그룹이 시그널로 죽으면 분류기 자체가 돌지 않으므로, 소각은
기본값이 아니라 **유일한 결과**였다 — 2026-09-03 rebuild-021이 60분을 태우고
0바이트로 사라졌을 때, registry는 generation이 오르지 않았다고 말하는데도 다음
실행이 `already claimed`로 거부됐다. 회전 사이클 하나가 아무 근거 없이 죽었다.

그래서 반대 방향의 양성 증거를 둘 요구한다. registry가 이 pinset의 generation을
아직 `pending_rebuild`로 보고, 그 claim이 가리키는 output에 `result.json`이
없어야 한다. 전역 lock이 동시 실행을 이미 막으므로, 둘이 함께 참이면 그 claim을
만든 실행은 아무것도 소비하지 않고 죽은 것이다.

여기서는 launcher 본문에서 판정 함수를 잘라내 **실제로 실행한다** — 텍스트
단언이 아니라 동작을 본다(형제 `test_run_pinned_rebuild_once.py`와 같은 방식).
launcher의 ledger 검사는 uid 0을 요구하므로 claim 블록 전체는 비-root 테스트에서
돌릴 수 없고, 판정만 떼어 낸다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_LAUNCHER = Path(__file__).resolve().parents[2] / "scripts/run-pinned-rebuild-once"
_PINSET = "a" * 64


def _decision(generation_binding: str) -> Callable[[Path], bool]:
    """launcher 본문의 판정 함수를 그대로 실행 가능한 형태로 꺼낸다."""
    source = _LAUNCHER.read_text(encoding="utf-8")
    start = source.index("def stale_claim_is_retakable(path):")
    end = source.index("\nmetadata = ledger_dir.lstat()", start)
    namespace: dict[str, Any] = {
        "json": json,
        "pathlib": __import__("pathlib"),
        "open": open,
        "pinset": _PINSET,
        "generation_binding": generation_binding,
    }
    exec(compile(source[start:end], str(_LAUNCHER), "exec"), namespace)  # noqa: S102
    return namespace["stale_claim_is_retakable"]


def _claim(tmp_path: Path, *, pinset: str = _PINSET, output: Path | None = None) -> Path:
    claim = tmp_path / "claim"
    claim.write_text(
        json.dumps(
            {
                "manager_source_revision": "b" * 40,
                "output_directory": str(output if output is not None else tmp_path / "out"),
                "pinset_sha256": pinset,
            }
        ),
        encoding="ascii",
    )
    return claim


def test_a_dead_run_leaves_a_retakable_claim(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    assert _decision("pending_rebuild")(_claim(tmp_path, output=output)) is True


@pytest.mark.parametrize("binding", ["match", "drift", "unknown", ""])
def test_a_generation_that_moved_is_never_retakable(tmp_path: Path, binding: str) -> None:
    """registry가 `pending_rebuild`를 말하지 않으면 되찾지 않는다."""
    output = tmp_path / "out"
    output.mkdir()
    assert _decision(binding)(_claim(tmp_path, output=output)) is False


def test_a_run_that_wrote_its_conclusion_is_never_retakable(tmp_path: Path) -> None:
    """`result.json`이 있으면 그 실행은 결론을 남겼다 — 되찾지 않는다."""
    output = tmp_path / "out"
    output.mkdir()
    (output / "result.json").write_text("{}", encoding="ascii")
    assert _decision("pending_rebuild")(_claim(tmp_path, output=output)) is False


def test_a_claim_for_another_pinset_is_never_retakable(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    claim = _claim(tmp_path, pinset="c" * 64, output=output)
    assert _decision("pending_rebuild")(claim) is False


@pytest.mark.parametrize("body", ["", "not json", '{"output_directory": "relative"}', "[]"])
def test_an_unreadable_claim_is_never_retakable(tmp_path: Path, body: str) -> None:
    """판정할 수 없으면 되찾지 않는다(fail-close)."""
    claim = tmp_path / "claim"
    claim.write_text(body, encoding="ascii")
    assert _decision("pending_rebuild")(claim) is False


def test_a_missing_claim_is_never_retakable(tmp_path: Path) -> None:
    assert _decision("pending_rebuild")(tmp_path / "absent") is False


def test_the_launcher_feeds_the_registry_evidence_into_the_claim_block() -> None:
    """판정이 근거 없이 돌지 않도록, launcher가 실제로 그 값을 넘기는지 본다."""
    source = _LAUNCHER.read_text(encoding="utf-8")
    assert '"${generation_binding}" <<' in source
    assert 'generation_binding = sys.argv[5] if len(sys.argv) > 5 else ""' in source
    assert "generation_pinset_binding" in source
    # 되찾지 못하면 종전과 똑같이 거절해야 한다.
    assert 'raise SystemExit("pinned rebuild candidate was already claimed")' in source
