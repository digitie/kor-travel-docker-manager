"""ADR-53: 퇴역한 Map 전용 PostgreSQL을 가리키는 **살아 있는** 참조가 없다(grep 게이트).

Map의 두 DB는 공용 instance로 옮겼다. 옛 포트(`12700`)나 옛 서비스·컨테이너 이름
(`kor-travel-map-postgres`)이 코드·compose·설정·스크립트·예시 env에 남아 있으면, 그 자리가 다음
배포에서 조용히 퇴역한 instance를 겨냥한다(이동 창은 그 컨테이너를 멈춰 두므로 **크게** 실패하지만,
롤백 사본을 되살리는 실수를 부른다).

허용은 파일이 아니라 **줄 모양**이다: Map 대역 선언 `port_band: "12700-12799"`는 남는다(`-`가 단어
경계라 `\\b12700\\b`가 그 줄에 걸린다). 같은 파일이라도 다른 모양의 줄은 그대로 걸린다. 역사 문서
(`docs/decisions.md`·`docs/journal.md`·`docs/tasks-done.md`)는 검사 범위 밖이다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PATTERN = re.compile(r"\b12700\b|kor-travel-map-postgres")
_ALLOWED_LINE = re.compile(r"^\s*port_band:")
_SCANNED = ("backend/src", "docker-compose.yml", "config", "scripts", ".env.example")


def _files(root: Path) -> Iterator[Path]:
    for entry in _SCANNED:
        path = root / entry
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from (
                candidate
                for candidate in sorted(path.rglob("*"))
                if candidate.is_file() and "__pycache__" not in candidate.parts
            )


def _hits(root: Path) -> list[str]:
    hits: list[str] = []
    for path in _files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _PATTERN.search(line) and not _ALLOWED_LINE.match(line):
                hits.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    return hits


def test_no_live_reference_to_retired_map_instance() -> None:
    assert _hits(_ROOT) == []


def test_the_gate_sees_the_tree_it_claims_to_scan() -> None:
    """검사기 하한은 **본 것**에 건다 — 빈 범위의 초록은 아무것도 재지 않는다."""

    scanned = list(_files(_ROOT))
    names = {path.relative_to(_ROOT).as_posix() for path in scanned}
    assert "docker-compose.yml" in names
    assert ".env.example" in names
    assert "config/docker-targets.yml" in names
    assert "backend/src/kor_travel_docker_manager/services/database_runtime.py" in names
    assert any(name.startswith("scripts/") for name in names)
    # 허용된 줄 모양이 실제로 있고, 패턴이 그것을 **보고** 허용한다.
    band_lines = [
        line
        for line in (_ROOT / "config" / "docker-targets.yml").read_text(encoding="utf-8").splitlines()
        if _PATTERN.search(line)
    ]
    assert band_lines, "전제: Map 대역 선언이 있다"
    assert all(_ALLOWED_LINE.match(line) for line in band_lines)


def test_the_gate_goes_red_on_a_new_literal(tmp_path: Path) -> None:
    """새 리터럴은 같은 파일의 허용 줄 옆에서도 걸린다 — 허용은 파일이 아니라 줄 모양이다."""

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "docker-targets.yml").write_text(
        'map:\n  port_band: "12700-12799"\n  connection: "postgresql://127.0.0.1:12700"\n',
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  kor-travel-map-postgres: {}\n", encoding="utf-8"
    )
    (tmp_path / ".env.example").write_text("MAP_PORT=127000\n", encoding="utf-8")

    assert _hits(tmp_path) == [
        "docker-compose.yml:2: kor-travel-map-postgres: {}",
        'config/docker-targets.yml:3: connection: "postgresql://127.0.0.1:12700"',
    ]
