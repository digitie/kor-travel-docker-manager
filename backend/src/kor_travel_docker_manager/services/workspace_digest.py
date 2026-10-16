"""설치된 공용 Dagster workspace 파일 == digest env(ADR-58 결정 6·B-7, T-MGR-02) — 효과로 본다.

공용 plane 서비스(daemon·webserver)는 healthcheck가 마운트된 `workspace.yaml`의 sha256[:16]을
`KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST`와 대조해, 다르면 unhealthy가 된다. 그 어긋남을 설치·재생성 **뒤가 아니라** `targets
validate`(설치기의 flip 전 검사 포함)에서 먼저 드러낸다.

검사 대상은 ensure·converge가 쓰는 같은 파생이다 — plane은 `derive_shared_dagster_plane`(공용 workspace를 붙인 활성
daemon·webserver), workspace 소스는 `shared_workspace_source`(서비스마다 정확히 하나), 파일·digest 대조는
`read_host_workspace`(이 호스트의 파일인가 + 두 서비스의 digest가 그 bytes와 같은가). 여기서 더하는 것은 compose 원문의
`${VAR:-default}` 치환뿐이다. plane이 없거나, workspace bind가 하나가 아니거나, digest env가 없으면 **위반**이다 —
대상이 0이라 조용히 통과하지 않는다.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.runtime_topology import (
    derive_shared_dagster_plane,
    read_host_workspace,
    shared_workspace_source,
)

DIGEST_ENV: Final = "KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST"
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def interpolate(text: str, value: Callable[[str], str | None]) -> str:
    """compose의 `${VAR:-default}`(비었거나 없으면 기본값)와 `${VAR}`만 푼다."""

    def replace(match: re.Match[str]) -> str:
        current = value(match.group(1))
        if match.group(2) is not None:
            return current or match.group(2)
        return current or ""

    return _VAR.sub(replace, text)


def workspace_digest_violations(
    compose: Mapping[str, Any],
    targets: Mapping[str, Any],
    project_root: Path,
    value: Callable[[str], str | None],
) -> list[str]:
    """`value(name)`은 compose 보간 값(없으면 None). 위반 문구에는 경로·digest만 싣는다."""
    source = "(unknown)"
    digests: list[str] = []
    try:
        plane = derive_shared_dagster_plane(compose)
        services = compose["services"]
        sources = {interpolate(shared_workspace_source(services[name]), value) for name in plane.services}
        if len(sources) != 1:
            raise DeploymentContractError("shared plane services mount different workspaces")
        source = next(iter(sources))
        for name in plane.services:
            environment = services[name].get("environment")
            if not isinstance(environment, Mapping) or DIGEST_ENV not in environment:
                raise DeploymentContractError(f"shared plane service {name} has no {DIGEST_ENV}")
            digests.append(interpolate(str(environment[DIGEST_ENV]), value).strip())
        read_host_workspace(source, root=project_root, targets=targets, digests=digests)
    except DeploymentContractError as exc:
        if source == "(unknown)":
            return [f"shared Dagster workspace: {exc}"]
        path = Path(source) if source.startswith("/") else project_root / source
        try:
            detail = f"file {source} sha256[:16] {hashlib.sha256(path.read_bytes()).hexdigest()[:16]}"
        except OSError:
            detail = f"file {source} is missing"
        return [f"shared Dagster workspace: {exc} ({detail}, {DIGEST_ENV} {', '.join(digests) or 'unknown'})"]
    return []
