"""소비자 origin(ADR-58 R2, `docs/bindings.md` B-6) — compose 소비자 기본값과 root `.env` 재정의를 registry에서 파생한
기대값과 대조한다.

R2: 내부 target(Map·PinVi)이 형제 target(registry에 `consumer_origin`이 있는 target)을 부르는 env는 **compose 기본값을
커밋된 파생물로** 둔다. 대상은 줄 번호가 아니라 규칙이다 — "형제 target의 포트를 가진 URL을 값에 가진 **모든** compose
env(비활성 profile 포함)". 형제 포트는 registry에서 읽는다: `consumer_origin`의 포트와 형제 자신의 컨테이너
`connection`의 포트(옛 포트로 되돌린 소비자도 잡는다). 기대 origin은 `registry.consumer_origin`(R1) — 소비자 서비스를
실행하는 target의 호스트에서 본 값이다.

스캔 범위: 모든 서비스의 `environment`와 `build.args`. 빼는 것 — (1) 형제 자신의 서비스(같은 호스트 안의 자기 호출),
(2) `NEXT_PUBLIC_*`(브라우저가 부르는 공개 origin — 정본은 root `.env`의 `KTDM_PROD_URL_*`, R6).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from kor_travel_docker_manager.services import registry

_URL = re.compile(r"https?://([^/:}\s'\"]+):(\d+)")
_TEMPLATE_URL = re.compile(r"https?://\{host\}:(\d+)")
_DEFAULTED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*)\}")


@dataclass(frozen=True)
class ConsumerEnv:
    service: str
    key: str
    sibling: str
    value: str
    expected: str | None

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
    """형제 target의 포트 → target. `consumer_origin`과 형제 컨테이너 `connection`의 `{host}` URL 포트."""
    ports: dict[int, str] = {}
    containers = config["containers"]
    for target, spec in config["targets"].items():
        origin = spec.get("consumer_origin")
        if not origin:
            continue
        candidates = [origin, *(str(containers[c].get("connection") or "") for c in spec.get("containers") or [])]
        for text in candidates:
            for port in _TEMPLATE_URL.findall(text):
                ports.setdefault(int(port), target)
    return ports


def _service_owners(config: Mapping[str, Any]) -> dict[str, set[str]]:
    """compose 서비스 → 그것을 실행하거나 관찰하는 target들(형제 자기 호출 제외와 소비자 호스트 판정에 쓴다)."""
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


def _env_items(service: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    for block in (service.get("environment"), (service.get("build") or {}).get("args")):
        if isinstance(block, Mapping):
            yield from ((str(k), str(v)) for k, v in block.items() if v is not None)
        elif isinstance(block, list):
            for entry in block:
                key, _, value = str(entry).partition("=")
                yield key, value


def _consumer_host(service: str, owners: Mapping[str, set[str]], config: Mapping[str, Any]) -> str:
    """소비자 서비스가 도는 호스트 — 그 서비스를 **실행**하는 target의 호스트(하나일 때). 아니면 default 호스트."""
    running = {
        host
        for target in owners.get(service, set())
        if service in (config["targets"][target].get("services") or [])
        for host in registry.target_hosts(target, config)
    }
    return next(iter(running)) if len(running) == 1 else registry.default_host(config)


def scan(compose: Mapping[str, Any], config: Mapping[str, Any]) -> dict[tuple[str, str], ConsumerEnv]:
    """(서비스, env) → 소비자 env. 형제 포트를 가진 URL이 값 어디에든(기본값 포함) 있으면 잡는다."""
    ports = sibling_ports(config)
    owners = _service_owners(config)
    found: dict[tuple[str, str], ConsumerEnv] = {}
    for name, service in (compose.get("services") or {}).items():
        for key, value in _env_items(service):
            if key.startswith("NEXT_PUBLIC_"):
                continue
            for _host, port in _URL.findall(value):
                sibling = ports.get(int(port))
                if sibling is None or sibling in owners.get(name, set()):
                    continue
                expected = registry.consumer_origin(sibling, _consumer_host(name, owners, config), config)
                found[(name, key)] = ConsumerEnv(name, key, sibling, value, expected)
    return found


def default_violations(compose: Mapping[str, Any], config: Mapping[str, Any]) -> list[str]:
    """커밋된 compose 기본값이 registry 기대 origin과 다른 곳."""
    violations: list[str] = []
    for (name, key), env in sorted(scan(compose, config).items()):
        if env.source_variable is None:
            violations.append(
                f"`{name}`.{key}: `{env.value}` — root `.env`로 재정의할 수 있는 `${{VAR:-{env.expected}}}` 꼴이어야 한다"
            )
        elif env.default != env.expected:
            violations.append(
                f"`{name}`.{key}: 기본값 `{env.default}` ≠ 형제 `{env.sibling}`의 origin `{env.expected}`"
            )
    return violations


def env_override_violations(
    compose: Mapping[str, Any], config: Mapping[str, Any], env_value: Callable[[str], str]
) -> list[str]:
    """root `.env`가 소비자 origin의 원천 변수를 기대값과 다른 값으로 재정의한 곳(빈 값은 compose가 기본값을 쓴다).

    문구에는 변수 이름과 기대값만 싣는다 — 재정의된 값은 싣지 않는다(`.env`의 다른 줄처럼 다룬다).
    """
    violations: dict[str, str] = {}
    for env in scan(compose, config).values():
        variable = env.source_variable
        if variable is None or variable in violations:
            continue
        value = env_value(variable).strip()
        if value and value != env.expected:
            violations[variable] = (
                f"root .env {variable} overrides the consumer origin of '{env.sibling}' "
                f"(expected {env.expected} from the registry, ADR-58 R2)"
            )
    return [violations[name] for name in sorted(violations)]
