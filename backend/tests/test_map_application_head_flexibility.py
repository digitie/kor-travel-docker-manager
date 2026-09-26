"""Manager가 Map의 application head를 **특정 revision에 다시 못 박도록** 한다.

## 왜 이 게이트가 필요한가

과거에는 기대값이 리터럴 ``"300"``이었다. Map이 migration을 **하나** 더하자 Manager가
candidate를 거절했고, Map 쪽 fresh installer는
``installed active Alembic graph head is not exactly 300``으로 멈췄다. 스키마 진화를
막은 것은 배포 안전성이 아니라 **값 고정**이었다.

여기서 값은 풀되, head 자체는 여전히 candidate API image가 network 없이 출력한
installed graph의 head 하나(``/usr/local/bin/ktm-application-schema head``)로만
결정된다 -- ADR-101 이전처럼 receipt가 선언한 값과 대조할 두 번째 출처는 없다.
직접 관측한 값 하나가 정본이다.

## 무엇이 여전히 고정인가

없다. ``300``은 한때 **baseline root** 상수로 남아 있었다 -- ``0236 → 300`` handoff의
목적지이자 "Dagster metadata DB는 application raw revision을 갖지 않는다"는 격리 선언이
가리키는 역사적 좌표였다. 그 상수를 선언한 모듈은 죽은 코드로 지워졌고(ADR-51 B3),
그래서 면제 목록도 비어 있다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_SRC = _REPO_ROOT / "backend" / "src" / "kor_travel_docker_manager"
_SCRIPTS = _REPO_ROOT / "scripts"

_SKIPPED_DIRECTORIES = frozenset({"__pycache__", "node_modules", ".venv", "dist", ".next"})
_BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".whl", ".woff", ".woff2"}
)

#: 따옴표로 감싼 형태만 본다 — 숫자 300(초·픽셀)과 구분한다. head는 문자열이다.
_QUOTED = re.compile(r"""["']300["']""")
_ENV_ASSIGNMENT = re.compile(
    r"KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD\s*[=:]\s*[\"']?300"
)

_EXEMPT: dict[str, str] = {}
"""head 리터럴이 **정당한** 파일과 사유.

사유 없는 면제는 두지 않는다. 여기 이름을 더하는 것은 "이 파일의 `300`은 head가 아니라
baseline root(또는 초 단위 인자)다"라는 주장이고, 그 주장이 틀리면 프로덕션이 죽는다.
"""


def _scanned_files() -> list[Path]:
    """Manager 전체 — backend `src/` 재귀 + `scripts/` 재귀, 확장자 무관."""

    files: list[Path] = []
    files.extend(
        path
        for pattern in (".env*", "docker-compose*.yml")
        for path in sorted(_REPO_ROOT.glob(pattern))
        if path.is_file()
    )
    for root in (_BACKEND_SRC, _SCRIPTS):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if _SKIPPED_DIRECTORIES.intersection(path.relative_to(root).parts):
                continue
            if path.suffix.lower() in _BINARY_SUFFIXES:
                continue
            files.append(path)
    return files


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def test_the_scan_actually_reaches_files() -> None:
    """스캔이 비면 아래 게이트가 조용히 무의미해진다."""
    files = _scanned_files()

    assert len(files) > 30, f"Manager 자산 스캔이 {len(files)}개만 찾았다 — 경로가 틀렸다"
    names = {path.name for path in files}
    assert "compose_service.py" in names
    assert "m05_isolated_e2e.py" in names
    assert "cli.py" in names, "`services/` 밖이 스캔에 없다"
    assert ".env.example" in names, "저장소 루트 `.env*`가 스캔에 없다"
    assert "docker-compose.yml" in names, "compose 파일이 스캔에 없다"


def test_manager_does_not_pin_the_map_application_head() -> None:
    """**이 게이트의 본체 — 존재 기준.**"""
    offenders: list[str] = []
    for path in _scanned_files():
        if path.name in _EXEMPT:
            continue
        source = _text(path)
        if source is None:
            continue
        for number, line in enumerate(source.splitlines(), 1):
            if _QUOTED.search(line) or _ENV_ASSIGNMENT.search(line):
                offenders.append(
                    f"{path.relative_to(_REPO_ROOT).as_posix()}:{number}: {line.strip()[:88]}"
                )

    assert not offenders, (
        "Manager가 Map application head를 리터럴로 박았다 — candidate가 관측한 head를 "
        "쓸 것(`candidate.application_head` / `generation.map_application_head`). "
        "baseline root를 가리키는 정당한 언급이라면 `_EXEMPT`에 **사유와 함께** 선언할 "
        "것:\n  " + "\n  ".join(offenders)
    )


def test_every_exemption_is_alive_reasoned_and_needed() -> None:
    """면제는 실재 파일에만, 사유와 함께, 실제로 필요한 것만."""
    names = {path.name for path in _scanned_files()}
    dead = sorted(set(_EXEMPT) - names)
    empty = sorted(name for name, reason in _EXEMPT.items() if len(reason.strip()) < 20)
    unnecessary = sorted(
        path.name
        for path in _scanned_files()
        if path.name in _EXEMPT
        and (source := _text(path)) is not None
        and not _QUOTED.search(source)
        and not _ENV_ASSIGNMENT.search(source)
    )

    assert not dead, f"면제 목록에 존재하지 않는 파일: {dead}"
    assert not empty, f"사유가 없거나 부실한 면제: {empty}"
    assert not unnecessary, f"리터럴이 없는데 면제된 파일: {unnecessary}"


@pytest.mark.parametrize(
    "line",
    [
        '_MAP_APPLICATION_EXPECTED_HEAD: Final[str] = "300"',
        'EXPECTED_MAP_APPLICATION_HEAD: Final[str] = "300"',
        '    if installed_head != "300":',
        "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD=300",
        "      KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD: 300",
        "    head_pin = '300'",
    ],
)
def test_the_rule_catches_every_shape_that_bypassed_the_old_one(line: str) -> None:
    """적대 리뷰가 실행으로 뚫은 형태를 되짚는다."""
    assert _QUOTED.search(line) or _ENV_ASSIGNMENT.search(line)
