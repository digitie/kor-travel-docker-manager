"""저장소를 읽는 모든 `git` 호출이 `refs/replace/` 치환을 무시하는지 본다.

pinned rebuild와 deployment readiness는 **소스 revision이 무엇인지**를 git에게
물어 provenance를 세운다. 그런데 `refs/replace/`가 있는 저장소에서는 `git show`
`git cat-file`이 진짜 객체가 아니라 대체 객체를 돌려준다. 커밋 SHA는 그대로인데
내용만 달라지므로, revision을 검증하는 쪽은 통과했다고 믿고 다른 코드를 굽는다.

replace ref는 공격이 아니어도 생긴다 — `git replace`로 히스토리를 손보거나,
그런 ref를 가진 원격에서 딸려오면 그대로 남는다. provenance를 읽는 경로에서는
그 편의가 그대로 위험이다.

호출부를 **열거하지 않는다**. 새 호출부가 생기면 열거는 늘 뒤처지므로, 소스에서
`["git",`로 시작하는 리스트를 전부 찾아 그 다음 토큰을 검사한다.
"""

from __future__ import annotations

import re
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "kor_travel_docker_manager"
_INVOCATION = re.compile(r'\[\s*"git"\s*,\s*"([^"]*)"')


def _invocations() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = _INVOCATION.search(line)
            if match is not None:
                found.append((path.name, number, match.group(1)))
    return found


def test_the_gate_has_something_to_guard() -> None:
    """호출부가 사라지면 이 게이트는 공허하게 통과한다 — 하한을 둔다."""
    assert len(_invocations()) >= 4


def test_every_git_invocation_ignores_replace_objects() -> None:
    offenders = [
        f"{name}:{number} -> {first}"
        for name, number, first in _invocations()
        if first != "--no-replace-objects"
    ]
    assert offenders == [], offenders
