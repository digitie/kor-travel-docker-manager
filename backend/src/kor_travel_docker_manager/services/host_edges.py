"""교차 호스트 간선 검사(ADR-58 R7, T-MGR-NH NH-2) — `targets validate`가 설치 전에 본다.

compose `depends_on`은 같은 호스트 안의 간선이다. 소비자 서비스를 소유한 호스트 집합이 제공자를 소유한 호스트 집합에
들어 있지 않으면, 그 호스트의 `up`이 남의 호스트 tenant를 되살리거나(R4 위반) loopback 간선이 비어 버린다. C6c가
loopback을 고정한 간선(PinVi→Map·concierge UI→API·PG DSN)도 compose에 `depends_on`으로 선언돼 있어서, 이 검사 하나가
"같은 호스트여야 한다"를 강제한다(`docs/multi-host.md` §3.2). 공용 PG는 소비 target이 직접 소유하므로 구성상 성립한다(§3.1).

소유자는 `compose_service.compose_service_owners` 한 자리에서 온다(변경 경계·소비자 origin과 같은 판정) — target
`services` ∪ `init_steps`가 건드리는 서비스 ∪ pinned 재구축 one-shot ∪ Dagster family 전체(전환 뒤 profile로 남은 것 포함).
**소유자 없는 compose 서비스는 위반**이다 — 검사가 모르는 서비스를 조용히 건너뛰지 않게. 예외는 registry의 명시 목록
`unowned_services:`(이름 목록)뿐이고, 목록에 있는데 compose에 없는 이름도 위반이다(낡은 허용이 남지 않게). 목록이 코드가
아니라 registry에 있는 것은 옛 서비스 이름을 코드 literal로 두지 않는 규칙(ADR-54, 적대 리뷰 M2) 때문이다.

규칙 하나를 더 본다: 공용 Dagster에 합류한(`dagster.control_plane: shared`) target의 호스트에는 그 workspace를 실을
공용 plane이 있어야 한다 — plane 서비스(렌더 모양으로 파생)를 소유한 호스트 집합에 들어 있어야 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kor_travel_docker_manager.services import registry
from kor_travel_docker_manager.services.compose_service import compose_service_owners
from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.runtime_topology import (
    derive_dagster_families,
    derive_shared_dagster_plane,
)


def _depends_on(service: Mapping[str, Any]) -> list[str]:
    value = service.get("depends_on") or {}
    return [str(name) for name in (value if isinstance(value, (list, Mapping)) else [])]


def unowned_allowed(config: Mapping[str, Any]) -> frozenset[str]:
    """registry `unowned_services:` — 소유 target이 없는 것이 정당한 compose 서비스(이름 목록, 중복 없음)."""
    allowed = config.get("unowned_services") or []
    if (
        not isinstance(allowed, list)
        or any(not isinstance(name, str) or not name for name in allowed)
        or len(set(allowed)) != len(allowed)
    ):
        raise registry.TargetsConfigError("unowned_services must be a list of distinct compose service names")
    return frozenset(allowed)


def host_edge_violations(compose: Mapping[str, Any], config: Mapping[str, Any]) -> list[str]:
    """위반 문구 목록(비면 통과). 이름과 호스트 집합만 싣는다."""
    services = compose.get("services") or {}
    owners = compose_service_owners(config, families=derive_dagster_families(compose, config))
    allowed = unowned_allowed(config)
    violations: list[str] = []
    for name in services:
        if name not in owners and name not in allowed:
            violations.append(f"compose service {name} is owned by no target (add it to a target or to unowned_services)")
    violations += [
        f"unowned_services lists {name}, which compose no longer declares"
        for name in sorted(allowed)
        if name not in services
    ]
    for name, spec in services.items():
        consumer = owners.get(name)
        if consumer is None or not isinstance(spec, Mapping):
            continue
        for provider in _depends_on(spec):
            provided = owners.get(provider)
            if provided is not None and not consumer <= provided:
                violations.append(
                    f"{name} ({', '.join(sorted(consumer))}) depends_on {provider} "
                    f"({', '.join(sorted(provided))}) on another host"
                )
    try:
        plane = derive_shared_dagster_plane(compose)
    except DeploymentContractError:
        return violations  # plane 모양 오류는 workspace digest 검사가 말한다
    planed = frozenset().union(*(owners.get(name, frozenset()) for name in plane.services))
    for target, spec in config["targets"].items():
        if (spec.get("dagster") or {}).get("control_plane") != "shared":
            continue
        missing = registry.target_hosts(target, config) - planed
        if missing:
            violations.append(
                f"target {target} joins the shared Dagster plane on {', '.join(sorted(missing))}, "
                "where no shared plane runs"
            )
    return violations
