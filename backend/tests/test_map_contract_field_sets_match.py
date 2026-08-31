"""Map emitter와 Manager parser의 계약 field set이 어긋나지 않게 한다.

## 왜 필요한가

`_CONTRACT_FIELDS`(11필드)가 두 저장소 다섯 곳에 **손 사본**으로 존재한다 — Map의
배포 executable 셋(`docker/application-schema-{fresh-300,fresh-finalize,final-permit}.py`)
과 candidate 검증기(`_APPLICATION_CONTRACT_KEYS`), 그리고 Manager의
`map_application_300.py`. 공유 스키마 소스가 없어 **한쪽만 고치면 런타임에서만
드러난다** — 실제로 receipt 필드 2개 추가가 2-repo lockstep 배포를 요구했다.

이 테스트는 근본원인 감사 I-8의 **1단계만** 구현한다: descriptor 배포 경로(계약-as-data
4단계)는 lockstep을 하나 더 만들 위험이 있어 보류하고, 교차 대조 lint로 "한쪽만 고침"을
CI 전에 잡는다(적대 검증의 축소 지시 그대로).

## 어떻게 찾는가

Map 체크아웃은 ADR-044의 로컬 우선 규약을 따른다 — `KTDM_MAP_CHECKOUT` env가 있으면
그것을(**불완전하면 skip이 아니라 실패**), 없으면 형제 디렉터리 후보 중 대조 대상
파일이 전부 있는 것만 본다. 없으면 **시끄럽게 skip**한다 — skip이 출력에 남아
"게이트가 돌았다"는 착각이 생기지 않게.

후속(문서화된 한계): Manager CI에는 Map 체크아웃이 없어 이 게이트가 CI에서 skip된다.
cross-repo checkout은 PAT secret 배선이 필요하다. Map의 contract emitter
(`docker/application-schema-contract.py`)는 field를 동적으로 구성해 frozenset 추출
대상이 아니다 — 손 사본 5곳(아래 사이트)이 이 게이트의 범위다.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

_MANAGER_ROOT = Path(__file__).resolve().parents[2]
_MAP_CANDIDATE_PATHS = (
    _MANAGER_ROOT.parent / "kor-travel-map",
    _MANAGER_ROOT.parent / "ktm-m03",
)

#: (파일 상대경로, 변수명) — 전부 같은 계약(baseline contract 11필드)의 사본이어야 한다.
_MAP_SITES = (
    ("docker/application-schema-fresh-300.py", "_CONTRACT_FIELDS"),
    ("docker/application-schema-fresh-finalize.py", "_CONTRACT_FIELDS"),
    ("docker/application-schema-final-permit.py", "_CONTRACT_FIELDS"),
)
_MANAGER_SITES = (
    (
        "backend/src/kor_travel_docker_manager/services/map_application_300_candidate.py",
        "_APPLICATION_CONTRACT_KEYS",
    ),
)


def _checkout_is_complete(root: Path) -> bool:
    """모든 대조 대상 파일이 실제로 존재해야 후보로 인정한다(R2-S1).

    `docker/` 디렉터리 존재만 보면 stale 체크아웃이 선택돼 FileNotFoundError로
    죽거나, 파일이 일부만 있는 체크아웃이 절반의 대조로 초록이 된다.
    """
    return all((root / relative).is_file() for relative, _ in _MAP_SITES)


def _map_checkout() -> Path | None:
    override = os.environ.get("KTDM_MAP_CHECKOUT")
    if override:
        path = Path(override)
        # 명시 override가 쓸 수 없으면 skip이 아니라 실패다 — 운영자가 게이트를
        # 켰다고 믿는 상태에서 조용히 건너뛰면 "돌았다"는 착각이 생긴다(R2-S1).
        assert _checkout_is_complete(path), (
            f"KTDM_MAP_CHECKOUT={path}에 대조 대상 파일이 없다 — "
            "경로를 고치거나 env를 지울 것"
        )
        return path
    for candidate in _MAP_CANDIDATE_PATHS:
        if _checkout_is_complete(candidate):
            return candidate
    return None


def _frozenset_literal(path: Path, name: str) -> frozenset[str]:
    """이름이 재할당되면 fail-closed — 마지막/첫 매치를 조용히 고르지 않는다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[frozenset[str]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and value.args
            and isinstance(value.args[0], (ast.Set, ast.List, ast.Tuple))
        ):
            elements = value.args[0].elts
            for element in elements:
                if not isinstance(element, ast.Constant) or not isinstance(
                    element.value, str
                ):
                    raise AssertionError(f"{path.name}:{name} 비문자 원소")
            found.append(frozenset(e.value for e in elements))  # type: ignore[union-attr]
    if len(found) != 1:
        raise AssertionError(
            f"{path}에서 {name} frozenset 리터럴이 {len(found)}개다 — "
            "정확히 1개여야 한다(재할당은 어느 쪽이 정본인지 모호)"
        )
    return found[0]


def test_baseline_contract_field_sets_are_identical_across_repos() -> None:
    """**이 게이트의 본체.** 다섯 사본이 전부 같은 집합이어야 한다."""
    map_root = _map_checkout()
    if map_root is None:
        pytest.skip(
            "Map 로컬 체크아웃이 없어 교차 field-set 대조를 하지 못했다(ADR-044). "
            "provider/계약을 바꾸는 개발 환경에서는 KTDM_MAP_CHECKOUT을 지정해 반드시 "
            "돌릴 것 — 이 skip이 초록으로 읽히면 '한쪽만 고침'이 런타임까지 간다."
        )

    from kor_travel_docker_manager.services.map_application_300 import (
        _CONTRACT_FIELDS,
    )

    observed: dict[str, frozenset[str]] = {
        "manager:map_application_300._CONTRACT_FIELDS": _CONTRACT_FIELDS,
    }
    for relative, name in _MANAGER_SITES:
        observed[f"manager:{name}"] = _frozenset_literal(_MANAGER_ROOT / relative, name)
    for relative, name in _MAP_SITES:
        observed[f"map:{Path(relative).name}:{name}"] = _frozenset_literal(
            map_root / relative, name
        )

    baseline = observed["manager:map_application_300._CONTRACT_FIELDS"]
    diverged = {
        site: (sorted(fields - baseline), sorted(baseline - fields))
        for site, fields in observed.items()
        if fields != baseline
    }

    assert not diverged, (
        "baseline contract field set 사본이 어긋났다 — {site: (초과, 결손)}:\n"
        + "\n".join(f"  {site}: +{extra} -{missing}" for site, (extra, missing) in diverged.items())
        + "\n다섯 사본을 함께 고칠 것. 이 lint가 없던 동안에는 런타임에서만 드러났다."
    )
