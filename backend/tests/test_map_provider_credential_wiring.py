"""Map provider 자격증명이 compose에서 **같은 원천**에 닿는지 센다.

2026-09-18 n150 실측: `feature_place_krex_rest_areas_job`이 자격증명 없이 돌았다.
`KOR_TRAVEL_MAP_KREX_GO_API_KEY`가 빈 문자열이었기 때문이다.

원인은 이 문서의 두 줄이 **다른 원천**을 봤다는 것이다.

    KOR_TRAVEL_MAP_DATA_GO_KR_SERVICE_KEY: ${KRTOUR_MAP_DATA_GO_KR_SERVICE_KEY:-}
    KOR_TRAVEL_MAP_KREX_GO_API_KEY:        ${KOR_TRAVEL_MAP_KREX_GO_API_KEY:-}

둘은 **같은 비밀**이다 — `python-krex-api`의 `.env`에서 `KEX_GO_API_KEY`와
`DATA_GO_KR_SERVICE_KEY`가 같은 다이제스트임을 확인했고, 그 값이 prod 컨테이너의
`KOR_TRAVEL_MAP_DATA_GO_KR_SERVICE_KEY`(len 64)와도 같다. 그런데 아래 줄은
`KRTOUR_` 접두 원천을 보지 않으므로, 운영자가 그 접두 이름 하나만 채운 배포에서
조용히 빈 값이 된다. `.env.example`은 `KOR_TRAVEL_MAP_KREX_GO_API_KEY`를 적어 두고
`KRTOUR_MAP_DATA_GO_KR_SERVICE_KEY`는 적지 않으므로 그 어긋남을 안내하지도 못한다.

그래서 이름을 손으로 적지 않는다. **data.go.kr 줄이 실제로 보는 원천 집합을 문서에서
읽어**, KREX go 줄이 그 원천에 닿는지를 센다. 원천 이름이 바뀌면 두 줄이 함께
따라가야 하고, 폴백을 지우면 그 자리에서 빨개진다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

#: `KEY: ${...}` 한 줄에서 키 이름과 보간식을 뽑는다.
_ENTRY = re.compile(r"^\s{2,}([A-Z][A-Z0-9_]*):\s*(\$\{.+\})\s*$", re.MULTILINE)

#: 보간식 안에 등장하는 변수 이름. `${NAME:-...}`의 중첩도 같은 패턴으로 전부 잡힌다.
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")

_DATA_GO_KR = "KOR_TRAVEL_MAP_DATA_GO_KR_SERVICE_KEY"
_KREX_GO = "KOR_TRAVEL_MAP_KREX_GO_API_KEY"


def _entries(name: str) -> list[str]:
    text = _COMPOSE.read_text(encoding="utf-8")
    return [value for key, value in _ENTRY.findall(text) if key == name]


def _sources(expression: str, *, own_name: str) -> frozenset[str]:
    """보간식이 실제로 값을 길어오는 **외부** 원천 이름.

    자기 자신과 같은 이름은 제외한다 — `${X:-...}`의 `X`는 "운영자가 X를 직접
    채웠다면 그것을 쓴다"는 뜻이라 원천이라기보다 통로다. 문제는 그 뒤가 비었을
    때이므로, 세야 하는 것은 **뒤에 무엇이 있는가**다.
    """

    return frozenset(_VAR.findall(expression)) - {own_name}


@pytest.fixture(scope="module")
def data_go_kr_sources() -> frozenset[str]:
    entries = _entries(_DATA_GO_KR)
    assert entries, f"{_DATA_GO_KR} 항목이 compose에 없다"
    sources: set[str] = set()
    for expression in entries:
        sources |= _sources(expression, own_name=_DATA_GO_KR)
    assert sources, f"{_DATA_GO_KR}가 외부 원천을 하나도 보지 않는다: {entries}"
    return frozenset(sources)


def test_the_two_data_go_kr_entries_appear_on_the_same_services() -> None:
    """두 키는 같은 서비스 집합에 놓여야 한다 — 한쪽만 있는 서비스는 반쪽 배포다."""

    assert len(_entries(_KREX_GO)) == len(_entries(_DATA_GO_KR)), (
        f"{_KREX_GO} {len(_entries(_KREX_GO))}곳 vs "
        f"{_DATA_GO_KR} {len(_entries(_DATA_GO_KR))}곳"
    )


def test_the_krex_go_key_reaches_the_data_go_kr_source(
    data_go_kr_sources: frozenset[str],
) -> None:
    """KREX go key가 data.go.kr service key와 **같은 원천**에 닿는다.

    둘은 같은 비밀이다. 원천이 갈리면, 운영자가 한쪽 이름만 채운 배포에서 다른
    쪽이 조용히 빈 값이 되고 provider job이 자격증명 없이 돈다(2026-09-18 prod
    실측). 폴백을 지우는 변이는 이 검사에서 죽는다.
    """

    entries = _entries(_KREX_GO)
    assert entries, f"{_KREX_GO} 항목이 compose에 없다"
    for expression in entries:
        reached = _sources(expression, own_name=_KREX_GO)
        missing = data_go_kr_sources - reached
        assert not missing, (
            f"{_KREX_GO}가 data.go.kr 원천 {sorted(missing)}에 닿지 않는다: "
            f"{expression}"
        )


def test_the_krex_go_key_is_not_a_dead_end(
    data_go_kr_sources: frozenset[str],
) -> None:
    """빈 기본값 하나만 남은 형태를 거부한다.

    `${KOR_TRAVEL_MAP_KREX_GO_API_KEY:-}`가 정확히 prod에서 빈 값을 만든 형태다.
    자기 이름 하나에 빈 기본값이면 원천이 없다.
    """

    for expression in _entries(_KREX_GO):
        assert _sources(expression, own_name=_KREX_GO), (
            f"{_KREX_GO}가 자기 이름 말고는 아무 원천도 보지 않는다: {expression}"
        )
    # 검사가 공허하지 않은지: 비교 대상이 실제로 비어 있지 않다.
    assert data_go_kr_sources
