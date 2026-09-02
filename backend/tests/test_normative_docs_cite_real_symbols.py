"""규범 문서가 지목하는 테스트·심볼이 실제로 존재하는지 확인한다.

이 저장소의 문서는 자주 **결정의 근거로 테스트를 지목한다** — "정본이 늘어나는
순간을 잡도록 `test_x`가 단언한다", "이 호출을 뒤로 옮기면 `test_y`가 깨진다,
깨지면 고치지 말고 되돌려라". 그런 문장은 읽는 사람의 행동을 바꾸는 **하중을
받는 주장**이다.

그런데 그 주장을 지키는 기계가 없었다. 2026-09-02에 두 건이 드러났다:

- `docs/runtime-pin-registry.md` §8이 존재하지 않는 단언(`lock.assert_not_called()`)을
  근거로 "깨지면 되돌려라"라고 지시하고 있었다.
- `docs/tasks.md`의 won't-fix 결정이 `test_m05_isolated_harness_is_deliberately_two_role`을
  안전망으로 들었는데 **그 테스트가 없었다.**

둘 다 "한 사실이 두 곳에 독립 선언되고 둘을 묶는 기계가 없음"의 인스턴스다.
문서와 코드가 그 두 곳이다.

**과거 기록은 검사하지 않는다.** `docs/journal.md`·`docs/tasks-done.md`·감사 보고서는
그때 참이었던 것을 적은 것이고, 지금 기준으로 고치라고 요구하면 역사를 다시 쓰게
된다. 검사 대상은 **지금 사람이 따라야 하는 규범 문서**뿐이다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# 지금 사람이 따라야 하는 문서. 과거 기록(journal/tasks-done/감사 보고서)은 제외한다.
_NORMATIVE_DOCS: tuple[str, ...] = (
    "AGENTS.md",
    "SKILL.md",
    "CLAUDE.md",
    "docs/tasks.md",
    "docs/resume.md",
    "docs/runtime-pin-registry.md",
    "docs/docker-management.md",
)

_TEST_NAME = re.compile(r"`(test_[A-Za-z0-9_]+)`")
_CALL = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\(\))`")
_FILE_SUFFIXES = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".json",
    ".md",
    ".sh",
    ".yml",
    ".yaml",
    ".toml",
)


def _test_bodies() -> dict[str, str]:
    """모든 테스트 함수의 이름 → 본문 소스."""

    bodies: dict[str, str] = {}
    for path in (_ROOT / "backend/tests").rglob("test_*.py"):
        source = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - 문법 오류는 별도 게이트가 잡는다
            continue
        lines = source.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                bodies[node.name] = "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return bodies


def _test_file_stems() -> set[str]:
    """`test_docker_manager_cli`처럼 **파일**을 가리키는 이름은 함수가 아니다."""

    return {path.stem for path in (_ROOT / "backend/tests").rglob("test_*.py")}


def _normative_text() -> list[tuple[str, str]]:
    documents: list[tuple[str, str]] = []
    for relative in _NORMATIVE_DOCS:
        path = _ROOT / relative
        if not path.exists():
            continue
        documents.append((relative, path.read_text(encoding="utf-8", errors="ignore")))
    assert documents, "규범 문서를 하나도 읽지 못했다 — 이 검사가 공허해졌다"
    return documents


def test_normative_docs_only_cite_tests_that_exist() -> None:
    """규범 문서가 지목한 테스트는 실제로 있어야 한다.

    없으면 그 문서가 근거로 든 안전망이 없는 것이다 — won't-fix 결정이 존재하지
    않는 가드에 기대고 있던 실제 사례가 있다.
    """

    bodies = _test_bodies()
    stems = _test_file_stems()
    assert bodies, "테스트 함수를 하나도 수집하지 못했다 — 이 검사가 공허해졌다"

    missing: list[str] = []
    for relative, text in _normative_text():
        for name in sorted(set(_TEST_NAME.findall(text))):
            if name in bodies or name in stems:
                continue
            missing.append(f"{relative}: {name}")

    assert not missing, (
        "규범 문서가 존재하지 않는 테스트를 근거로 들고 있다 — "
        "테스트를 쓰거나 문서를 고쳐라: " + repr(missing)
    )


def test_normative_docs_only_cite_assertions_those_tests_make() -> None:
    """문서가 "테스트 X가 `foo()`로 결박한다"고 쓰면 그 호출이 X 안에 있어야 한다.

    `docs/runtime-pin-registry.md` §8이 존재하지 않는 `lock.assert_not_called()`를
    근거로 "깨지면 고치지 말고 되돌려라"라고 지시하고 있었다. 그 문장을 믿고
    되돌리면 실제로는 아무것도 지켜지지 않는다.

    같은 **문장** 안에 있을 때만 본다 — 단락 단위로 넓히면 인접한 파일명·무관한
    심볼까지 걸려 게이트가 잡음이 된다(실측: 단락 기준 72건 중 대부분 오탐).
    """

    bodies = _test_bodies()
    stems = _test_file_stems()

    broken: list[str] = []
    for relative, text in _normative_text():
        for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
            names = {n for n in _TEST_NAME.findall(sentence) if n not in stems}
            if not names:
                continue
            calls = {
                call
                for call in _CALL.findall(sentence)
                if not call.endswith(_FILE_SUFFIXES)
            }
            for name in sorted(names):
                body = bodies.get(name)
                if body is None:
                    continue  # 위 테스트가 담당한다
                for call in sorted(calls):
                    bare = call.rstrip("()").split(".")[-1]
                    if bare not in body:
                        broken.append(f"{relative}: {name} 에 {call} 가 없다")

    assert not broken, (
        "규범 문서가 테스트가 하지 않는 단언을 그 테스트의 것이라고 적고 있다: "
        + repr(broken)
    )
