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

_SRC = Path(__file__).resolve().parents[1] / "src" / "kor_travel_docker_manager"
_SERVICES = _SRC / "services"

_HEAD_LITERAL = re.compile(r"""(?<![0-9a-zA-Z_])["']300["'](?![0-9])""")

_HEAD_BEARING_MODULES = (
    "map_application_300.py",
    "map_application_300_candidate.py",
    "pinned_runtime_rebuild.py",
    "compose_service.py",
)


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """주석·docstring 본문을 제외한 실행 라인만 돌려준다.

    설명문에서 `"300"`을 언급하는 것은 막을 이유가 없다 — 막아야 하는 것은 **비교와
    주입**이다.
    """
    lines: list[tuple[int, str]] = []
    in_doc = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        fences = stripped.count('"""') + stripped.count("'''")
        if in_doc:
            if fences:
                in_doc = False
            continue
        if stripped.startswith(('"""', "'''")):
            if fences == 1:
                in_doc = True
            continue
        if stripped.startswith("#"):
            continue
        lines.append((number, raw))
    return lines


@pytest.mark.parametrize("name", _HEAD_BEARING_MODULES)
def test_manager_does_not_pin_the_map_application_head(name: str) -> None:
    """head를 다루는 모듈이 `300` 리터럴을 실행 코드에 두지 않아야 한다.

    baseline root 선언(`BASELINE_ROOT_REVISION`/`_BASELINE_ROOT_REVISION`) 한 줄만
    예외다. 그것은 head가 아니다.
    """
    path = _SERVICES / name
    assert path.exists(), f"head를 다루는 모듈이 사라졌다: {name}"

    code = _code_lines(path)
    # `--wait-timeout` 뒤에 오는 `"300"`은 초 단위 인자다. 정규식만으로는 head와 구별할
    # 수 없으므로 **바로 앞 코드 줄**을 본다. 이 예외를 두지 않으면 게이트가 진짜 head
    # 리터럴을 못 보게 될 만큼 시끄러워지고, 넓게 두면 head 리터럴이 숨을 자리가 생긴다.
    previous = ["", *(line for _, line in code)][: len(code)]
    offenders = [
        f"{name}:{number}: {line.strip()[:88]}"
        for (number, line), prior in zip(code, previous, strict=True)
        if _HEAD_LITERAL.search(line)
        and "BASELINE_ROOT_REVISION" not in line
        and "--wait-timeout" not in prior
    ]

    assert not offenders, (
        "Manager가 Map application head를 리터럴로 박았다 — candidate가 선언한 head를 "
        "쓸 것(`contract.application_head` / `generation.map_application_head`):\n  "
        + "\n  ".join(offenders)
    )


def test_expected_head_environment_is_never_a_literal() -> None:
    """`KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD`는 파생값이어야 한다.

    이 값이 리터럴이면 Map API 컨테이너가 head 불일치로 **기동 자체를 거부한다.**
    테스트도 CI도 못 보는 자리이므로 정적으로 고정한다.
    """
    offenders: list[str] = []
    for path in (*(_SERVICES.glob("*.py")), *(_SRC.parent.parent.parent / "scripts").glob("*.py")):
        for number, line in _code_lines(path):
            if "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD" not in line:
                continue
            if _HEAD_LITERAL.search(line) or re.search(r"EXPECTED_HEAD=[0-9]", line):
                offenders.append(f"{path.name}:{number}: {line.strip()[:88]}")

    assert not offenders, (
        "기동 기대 head가 리터럴이다 — Map이 migration을 하나 더하면 API가 기동하지 "
        "못한다:\n  " + "\n  ".join(offenders)
    )


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


def test_the_wait_timeout_exemption_is_narrow() -> None:
    """`--wait-timeout` 예외가 head 리터럴까지 통과시키면 게이트가 무의미하다.

    예외의 폭을 직접 시험한다 — 같은 `"300"`이라도 앞 줄이 무엇이냐에 따라 결과가
    달라져야 한다.
    """
    timeout_shaped = _code_lines(_SERVICES / "compose_service.py")
    assert any(
        _HEAD_LITERAL.search(line) for _, line in timeout_shaped
    ), "compose_service.py에 `300` 인자가 사라졌다 — 예외가 죽은 코드가 됐는지 확인할 것"

    sample = ['                    "--wait-timeout",', '                    "300",']
    previous = ["", *sample][: len(sample)]
    exempted = [
        line
        for line, prior in zip(sample, previous, strict=True)
        if _HEAD_LITERAL.search(line) and "--wait-timeout" not in prior
    ]
    assert exempted == []

    sample = ['        expected_head = (', '            "300"']
    previous = ["", *sample][: len(sample)]
    caught = [
        line
        for line, prior in zip(sample, previous, strict=True)
        if _HEAD_LITERAL.search(line) and "--wait-timeout" not in prior
    ]
    assert caught == ['            "300"']
