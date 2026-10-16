"""설치된 공용 Dagster workspace 파일 == digest env(ADR-58 결정 6·B-7, T-MGR-02) — 효과로 본다.

공용 plane 서비스(daemon·webserver)는 healthcheck가 마운트된 `workspace.yaml`의 sha256[:16]을
`KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST`와 대조해, 다르면 unhealthy가 된다. 그 어긋남을 설치·재생성 **뒤가 아니라** `targets
validate`(설치기의 flip 전 검사 포함)에서 먼저 드러낸다.

대상은 compose 원문에서 파생한다 — 값을 외우지 않는다: `KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST` env를 가진 서비스마다, 같은
서비스에서 `…/workspace.yaml`로 마운트되는 bind의 소스 파일. 둘 다 `${VAR:-default}`를 compose처럼 풀어 쓴다(`KTDM_HOST`는
이 Manager의 자기 호스트, 그 밖은 root `.env`). 소스 경로는 project root 기준이다.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, Final

DIGEST_ENV: Final = "KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST"
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}")


def _interpolate(text: str, value: Callable[[str], str]) -> str:
    return _VAR.sub(lambda match: value(match.group(1)) or (match.group(2) or ""), text)


def _binds(service: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    for volume in service.get("volumes") or []:
        if isinstance(volume, str):
            parts = volume.split(":")
            if len(parts) >= 2:
                yield parts[0], parts[1]
        elif isinstance(volume, Mapping) and volume.get("type", "bind") == "bind":
            yield str(volume.get("source") or ""), str(volume.get("target") or "")


def workspace_digest_violations(
    compose: Mapping[str, Any], project_root: Path, value: Callable[[str], str]
) -> list[str]:
    """`value(name)`은 compose 보간 값(비었으면 기본값을 쓴다). 위반 문구에는 경로·digest만 싣는다."""
    seen: set[tuple[str, str]] = set()
    violations: list[str] = []
    for name, service in (compose.get("services") or {}).items():
        environment = service.get("environment")
        if not isinstance(environment, Mapping) or DIGEST_ENV not in environment:
            continue
        sources = [source for source, target in _binds(service) if target.endswith("/workspace.yaml")]
        if len(sources) != 1:
            continue
        digest = _interpolate(str(environment[DIGEST_ENV]), value).strip()
        source = _interpolate(sources[0], value)
        if (source, digest) in seen:
            continue
        seen.add((source, digest))
        path = Path(source) if source.startswith("/") else project_root / source
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except OSError:
            violations.append(f"{name}: workspace file {source} is missing (expected digest {digest})")
            continue
        if actual != digest:
            violations.append(
                f"{name}: workspace file {source} sha256[:16] {actual} != {DIGEST_ENV} {digest} "
                "(the shared plane would be unhealthy; regenerate the file or fix KTDM_DAGSTER_WORKSPACE_DIGEST)"
            )
    return violations
