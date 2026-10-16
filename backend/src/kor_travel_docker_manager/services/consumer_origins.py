"""소비자 origin(ADR-58 R2, `docs/bindings.md` B-6) — compose 소비자 기본값과 root `.env` 재정의를 registry에서 파생한
기대값과 대조한다.

R2: 내부 target(Map·PinVi)이 형제 target(registry에 `consumer_origin`이 있는 target)을 부르는 env는 **compose 기본값을
커밋된 파생물로** 둔다. 대상은 줄 번호가 아니라 규칙이다 — "형제 포트를 가진 URL을 값에 가진 **모든** compose env(비활성
profile 포함)". 형제 포트는 registry가 **명시한 것만**이다: 형제의 `consumer_origin` 포트와 `retired_consumer_ports`(옛 포트로
되돌린 소비자를 잡는다). 형제가 가진 다른 포트(UI·Dagster·S3 등)는 소비자 origin이 아니다 — 그것까지 잡으면 무관한 env가
형제 소비자로 분류돼 validate·설치기 flip이 막힌다.

기대 origin은 `registry.consumer_origin`(R1) — 소비자 서비스가 도는 호스트에서 본 값이다. 그 호스트는 서비스를 **실행**하는
target의 호스트, 없으면(옛 `legacy-dagster` profile 서비스) 그 서비스가 속한 Dagster family target의 호스트다. 둘 다 없거나
호스트가 하나로 정해지지 않으면 기대값을 지어내지 않고 `problem`으로 보고한다.

스캔 범위: 모든 서비스의 `environment`와 `build.args`. 빼는 것 — (1) 형제 자신의 서비스(같은 호스트 안의 자기 호출),
(2) `NEXT_PUBLIC_*`(브라우저가 부르는 공개 origin — 정본은 root `.env`의 `KTDM_PROD_URL_*`, R6).
비교는 끝 `/`를 무시한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from kor_travel_docker_manager.services import registry
from kor_travel_docker_manager.services.runtime_topology import derive_dagster_families

_URL = re.compile(r"https?://([^/:}\s'\"]+):(\d+)")
_DEFAULTED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*)\}")


def _same_origin(left: str | None, right: str | None) -> bool:
    return left is not None and right is not None and left.rstrip("/") == right.rstrip("/")


@dataclass(frozen=True)
class ConsumerEnv:
    service: str
    key: str
    sibling: str
    value: str
    expected: str | None
    #: 기대값을 정할 수 없는 이유(소비자 호스트 미해석·형제 주소가 하나가 아님). 있으면 `expected`는 None이다.
    problem: str | None = None

    @property
    def source_variable(self) -> str | None:
        """root `.env`로 재정의하는 원천 변수(`${VAR:-default}`의 VAR). 리터럴이면 None."""
        match = _DEFAULTED.fullmatch(self.value)
        return match.group(1) if match else None

    @property
    def default(self) -> str | None:
        match = _DEFAULTED.fullmatch(self.value)
        return match.group(2) if match else None


def sibling_ports(config: Mapping[str, Any]) -> dict[int, str]:
    """형제 포트 → target. registry가 명시한 것만: `consumer_origin` 포트와 `retired_consumer_ports`."""
    ports: dict[int, str] = {}
    for target, spec in config["targets"].items():
        origin = spec.get("consumer_origin")
        if not origin:
            continue
        for port in [int(origin.rsplit(":", 1)[1]), *(spec.get("retired_consumer_ports") or [])]:
            ports.setdefault(int(port), target)
    return ports


def _service_owners(config: Mapping[str, Any]) -> dict[str, set[str]]:
    """compose 서비스 → 그것을 실행하거나 관찰하는 target들(형제 자기 호출 제외에 쓴다)."""
    owners: dict[str, set[str]] = {}
    containers = config["containers"]
    for target, spec in config["targets"].items():
        if spec.get("include") or spec.get("external_project"):
            continue
        names = set(spec.get("services") or [])
        names |= {containers[c]["compose_service"] for c in spec.get("containers") or [] if c in containers}
        for name in names:
            owners.setdefault(name, set()).add(target)
    return owners


def _consumer_hosts(compose: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """compose 서비스 → 그것이 도는 호스트 집합(실행 target, 없으면 Dagster family target)."""
    hosts: dict[str, set[str]] = {}
    for target, spec in config["targets"].items():
        if spec.get("include") or spec.get("external_project"):
            continue
        for service in spec.get("services") or []:
            hosts.setdefault(service, set()).update(registry.target_hosts(target, config))
    for target, family in derive_dagster_families(compose, config).items():
        for service in (*family.legacy, family.code_server):
            if service not in hosts:
                hosts[service] = set(registry.target_hosts(target, config))
    return {service: frozenset(value) for service, value in hosts.items()}


def _env_items(service: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    for block in (service.get("environment"), (service.get("build") or {}).get("args")):
        if isinstance(block, Mapping):
            yield from ((str(k), str(v)) for k, v in block.items() if v is not None)
        elif isinstance(block, list):
            for entry in block:
                key, _, value = str(entry).partition("=")
                yield key, value


def _expected(sibling: str, consumer_hosts: frozenset[str], config: Mapping[str, Any]) -> tuple[str | None, str | None]:
    if len(consumer_hosts) != 1:
        where = ", ".join(sorted(consumer_hosts)) or "no target runs it"
        return None, f"the consumer's host is unresolved ({where})"
    origin = registry.consumer_origin(sibling, next(iter(consumer_hosts)), config)
    if origin is None:
        hosts = ", ".join(sorted(registry.target_hosts(sibling, config)))
        return None, f"sibling '{sibling}' is on several hosts ({hosts}); its consumer origin has no single address"
    return origin, None


def scan(compose: Mapping[str, Any], config: Mapping[str, Any]) -> dict[tuple[str, str], ConsumerEnv]:
    """(서비스, env) → 소비자 env. 형제 포트를 가진 URL이 값 어디에든(기본값 포함) 있으면 잡는다."""
    ports = sibling_ports(config)
    owners = _service_owners(config)
    consumer_hosts = _consumer_hosts(compose, config)
    found: dict[tuple[str, str], ConsumerEnv] = {}
    for name, service in (compose.get("services") or {}).items():
        for key, value in _env_items(service):
            if key.startswith("NEXT_PUBLIC_"):
                continue
            for _host, port in _URL.findall(value):
                sibling = ports.get(int(port))
                if sibling is None or sibling in owners.get(name, set()):
                    continue
                expected, problem = _expected(sibling, consumer_hosts.get(name, frozenset()), config)
                found[(name, key)] = ConsumerEnv(name, key, sibling, value, expected, problem)
    return found


def default_violations(compose: Mapping[str, Any], config: Mapping[str, Any]) -> list[str]:
    """커밋된 compose 기본값이 registry 기대 origin과 다른 곳."""
    violations: list[str] = []
    for (name, key), env in sorted(scan(compose, config).items()):
        if env.problem is not None:
            violations.append(f"`{name}`.{key}: {env.problem}")
        elif env.source_variable is None:
            violations.append(
                f"`{name}`.{key}: `{env.value}` — root `.env`로 재정의할 수 있는 `${{VAR:-{env.expected}}}` 꼴이어야 한다"
            )
        elif not _same_origin(env.default, env.expected):
            violations.append(
                f"`{name}`.{key}: 기본값 `{env.default}` ≠ 형제 `{env.sibling}`의 origin `{env.expected}`"
            )
    return violations


def env_override_violations(
    compose: Mapping[str, Any], config: Mapping[str, Any], env_value: Callable[[str], str]
) -> list[str]:
    """root `.env`가 소비자 origin의 원천 변수를 기대값과 다른 값으로 재정의한 곳(빈 값은 compose가 기본값을 쓴다).

    문구에는 변수 이름과 기대값만 싣는다 — 재정의된 값은 싣지 않는다(`.env`의 다른 줄처럼 다룬다). 기대값을 정할 수 없는
    소비자에 재정의가 있으면 그 사실을 보고한다(fail-closed).
    """
    violations: dict[str, str] = {}
    for env in scan(compose, config).values():
        variable = env.source_variable
        if variable is None or variable in violations:
            continue
        value = env_value(variable).strip()
        if not value:
            continue
        if env.problem is not None:
            violations[variable] = f"root .env {variable} overrides a consumer origin that cannot be checked: {env.problem}"
        elif not _same_origin(value, env.expected):
            violations[variable] = (
                f"root .env {variable} overrides the consumer origin of '{env.sibling}' "
                f"(expected {env.expected} from the registry, ADR-58 R2)"
            )
    return [violations[name] for name in sorted(violations)]
