"""Manager가 Map의 application head를 **특정 revision에 다시 못 박도록** 한다.

## 왜 이 게이트가 필요한가

Manager는 "설치된 Map DB가 정확히 기대한 revision인가"를 여러 지점에서 확인한다 —
paired contract, fresh fence, finalize 결과, final permit, 그리고 기동 시
``KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD``. 그 엄격함은 옳다.

문제는 기대값이 리터럴 ``"300"``이었다는 것이다. Map이 migration을 **하나** 더하자
Manager가 candidate를 거절했고, Map 쪽 fresh installer는
``installed active Alembic graph head is not exactly 300``으로 멈췄다. 스키마 진화를
막은 것은 배포 안전성이 아니라 **값 고정**이었다.

여기서 값은 풀되 결박은 강화한다. head는 두 독립 출처가 일치할 때만 받는다.

1. paired receipt의 baseline contract — ``_canonical_digest(contract)``가
   ``application_contract_sha256``으로 결박돼 receipt sha256까지 전파된다.
2. candidate API image가 network 없이 출력한 installed graph의 head
   (``/usr/local/bin/ktm-application-schema head``).

## 무엇이 여전히 고정인가

``300``은 **baseline root**로만 남는다 — ``0236 → 300`` handoff의 목적지이자
"Dagster metadata DB는 application raw revision을 갖지 않는다"는 격리 선언이 가리키는
역사적 좌표다. 그것은 head가 아니고, migration이 쌓여도 움직이지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
from test_pinned_runtime_rebuild import (  # type: ignore[import-not-found]
    _candidate_image_ids,
    _map_application_300_candidate,
    _sources,
)

from kor_travel_docker_manager.services.map_application_300 import (
    BASELINE_ROOT_REVISION,
    Application300Contract,
    MapApplication300ContractError,
)
from kor_travel_docker_manager.services.pinned_runtime_rebuild import (
    DeploymentContractError,
    build_candidate_generation,
)

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

_EXEMPT: dict[str, str] = {
    "map_application_300.py": (
        "`BASELINE_ROOT_REVISION` 선언. `0236 → 300` handoff의 stamp 목적지이자 "
        "Dagster metadata 격리 선언이 가리키는 역사적 좌표이며 head가 아니다."
    ),
    "map_application_300_candidate.py": (
        "`_BASELINE_ROOT_REVISION` 선언과 `forbidden_application_raw_revision` 대조. "
        "둘 다 baseline root이며 head가 아니다."
    ),
    "compose_service.py": (
        "`--wait-timeout` 뒤의 `\"300\"`은 초 단위 인자다. 종전에는 앞 줄을 보고 "
        "좁혔는데, 그 예외가 **뒤따르는 어떤 줄이든 세탁**했다 — 상수 두 개를 나란히 "
        "두면 두 번째가 head 핀이어도 통과했다. 파일 단위 면제로 바꾸고, 이 파일이 "
        "head를 쓰는 자리는 `test_expected_head_environment_is_never_a_literal`과 "
        "`test_generation_requires_the_two_head_sources_to_agree`가 값으로 고정한다."
    ),
}
"""head 리터럴이 **정당한** 파일과 사유.

사유 없는 면제는 두지 않는다. 여기 이름을 더하는 것은 "이 파일의 `300`은 head가 아니라
baseline root(또는 초 단위 인자)다"라는 주장이고, 그 주장이 틀리면 프로덕션이 죽는다.
"""


def _scanned_files() -> list[Path]:
    """Manager 전체 — backend `src/` 재귀 + `scripts/` 재귀, 확장자 무관.

    종전에는 `services/` 아래 **네 파일 이름**만 훑었다. 적대 리뷰가 새 모듈
    `services/map_application_head_fence.py` 하나로 통과했고, `api/`·`cli.py`·
    `main.py`는 애초에 범위 밖이었다.
    """
    files: list[Path] = []
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


def test_manager_does_not_pin_the_map_application_head() -> None:
    """**이 게이트의 본체 — 존재 기준.**

    비교인지 대입인지 묻지 않는다. 리터럴과 비교를 다른 줄에 두는 것은 우회가 아니라
    평범한 코드이고, 그러니 "비교에 쓰였나"를 묻는 규칙은 원리적으로 완결될 수 없다.
    """
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
        "Manager가 Map application head를 리터럴로 박았다 — candidate가 선언한 head를 "
        "쓸 것(`contract.application_head` / `generation.map_application_head`). "
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
    """적대 리뷰가 실행으로 뚫은 형태를 되짚는다.

    게이트를 고쳤다는 주장은 "고친 코드가 통과한다"가 아니라 "뚫렸던 형태가 이제
    걸린다"로 증명해야 한다.
    """
    assert _QUOTED.search(line) or _ENV_ASSIGNMENT.search(line)


def test_baseline_root_stays_pinned() -> None:
    """baseline root는 반대로 **움직이면 안 된다.**"""
    assert BASELINE_ROOT_REVISION == "300"


@pytest.mark.parametrize("head", ["300", "301_m03_import_children", "0412_x.y-z"])
def test_contract_accepts_any_syntactically_valid_head(head: str) -> None:
    """contract가 `300` 아닌 head를 그대로 실어 나른다 — 왕복 무손실."""
    contract = _contract(head)
    assert contract.application_head == head
    assert contract.to_payload()["application_head"] == head
    assert Application300Contract.from_payload(contract.to_payload()) == contract


@pytest.mark.parametrize("head", ["", "300 ", "３００", "A300", "x" * 129, "-300"])
def test_contract_rejects_malformed_heads(head: str) -> None:
    """값은 풀되 **문법은 조인다** — 임의 문자열이 head 자리에 들어오면 거절한다."""
    with pytest.raises(MapApplication300ContractError):
        _contract(head)


def test_generation_requires_the_two_head_sources_to_agree() -> None:
    """**이 게이트의 본체.**

    receipt가 선언한 head와 candidate image가 실제로 담고 있는 head가 다르면, 그것은
    재빌드 없이 receipt를 재사용한 상태다 — 값 고정이 잡아주던 것보다 **더 나쁜** 상태이고
    반드시 거절해야 한다.
    """
    sources = _sources()
    paired = _map_application_300_candidate(sources)
    declared = paired.application_contract.application_head

    generation = build_candidate_generation(
        sources=sources,
        map_application_300_candidate=paired,
        image_ids=_candidate_image_ids(paired),
        map_application_head=declared,
        map_dagster_head="map-dagster-head",
        pinvi_head="pinvi-head",
    )
    assert generation.map_application_head == declared

    with pytest.raises(DeploymentContractError, match="differs from the paired"):
        build_candidate_generation(
            sources=sources,
            map_application_300_candidate=paired,
            image_ids=_candidate_image_ids(paired),
            map_application_head=f"{declared}_drifted",
            map_dagster_head="map-dagster-head",
            pinvi_head="pinvi-head",
        )


def _contract(head: str) -> Application300Contract:
    sources = _sources()
    paired = _map_application_300_candidate(sources)
    return replace(paired.application_contract, application_head=head)
