"""소비자 origin 기본값(ADR-58 R2, `docs/bindings.md` B-6)을 **렌더된 compose를 스캔해** 결박한다 — 잠정판.

R2: 내부 target(Map·PinVi)이 형제 서비스(geo·transport·weather)를 부르는 env는 **compose 기본값을 커밋된
파생물로** 둔다. 대상은 줄 번호가 아니라 규칙이다 — "형제 서비스 포트를 기본값에 가진 **모든** compose env
(비활성 `legacy-dagster` profile 포함)". 지정 줄만 보면 하나를 빠뜨려도 초록이다(geo 플립이 map-api만 바꾸면
map code-server는 loopback에 남아 n150 geo stop 뒤 모든 geocode run이 connection refused).

**잠정(2026-10-14, 소유자 승인).** geo·weather·transport가 odroid(`192.168.1.13`)로 옮겼고 소유자가 odroid의
포트를 공개 대역으로 통일했다 — geo `12501`, weather `12201`(n150의 옛 `14101`이 아니다), transport는 backend
`14001`이 아니라 API gateway `12301`. 그래서 형제 → 소비자 포트만 아래 표로 적고, 호스트는 registry에서
파생한다 — `targets.<형제>.host`(생략 = default), default면 `127.0.0.1`, 아니면 `hosts[<host>].addr`(R1).
**geo만 명시 재정의다** — 소비자는 odroid geo로 옮겼지만 geo의 Dagster는 아직 n150 공용 plane에 있어 geo target은
default 호스트(n150)다(geo 플립 전 이중 상태). geo 플립(또는 T-MGR-NH)이 registry를 바꾸면 재정의는 군더더기가 되고
`test_host_overrides_only_cover_targets_still_on_another_host`가 빨개져 지우게 한다. 포트 표를 registry
(`connection:`)로 옮기는 것은 T-MGR-01c다.

스캔 범위: 모든 서비스(모든 profile)의 `environment`와 `build.args`. 빼는 것 — (1) 형제 자신의 서비스(geo
code-server의 `KTG_DAGSTER_ADMIN_API_URL`, weather-web의 `WEATHER_API_INTERNAL_URL`은 같은 호스트 안의 자기
호출이다), (2) `NEXT_PUBLIC_*`(브라우저가 부르는 공개 origin — 정본은 root `.env`의 `KTDM_PROD_URL_*`, R6).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services.runtime_topology import derive_dagster_families
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker-compose.yml"
_TARGETS = _ROOT / "config" / "docker-targets.yml"

#: 형제 target → 소비자가 부르는 포트. 2026-10-14 소유자가 통일한 odroid 포트.
_CONSUMER_PORT: Mapping[str, int] = {"geo": 12501, "transport": 12301, "weather": 12201}
#: 소비자 origin의 호스트를 registry `host:` 대신 이것으로 — geo만(위 docstring). 잠정, T-MGR-NH가 없앤다.
_HOST_OVERRIDE: Mapping[str, str] = {"geo": "odroid"}
#: 소비자 기본값에서 더는 나오면 안 되는 n150 시절 포트 — 스캔이 이것도 형제 포트로 본다(되돌림을 잡는다).
_RETIRED_CONSUMER_PORTS: Mapping[int, str] = {14001: "transport", 14101: "weather"}

#: 하한 — 스캔이 적어도 이것은 봐야 한다(추출이 낡아 아무것도 못 보면 아래 값 검사가 항진이 된다).
_KNOWN_CONSUMERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("kor-travel-map-api", "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL"),
        ("kor-travel-map-dagster-code-server", "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL"),
        ("kor-travel-map-dagster", "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL"),
        ("kor-travel-map-dagster-daemon", "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL"),
        ("kor-travel-map-dagster-code-server", "KOR_TRAVEL_MAP_KOR_TRAVEL_TRANSPORT_BASE_URL"),
        ("pinvi-dagster-code-server", "PINVI_KOR_TRAVEL_WEATHER_BASE_URL"),
        ("pinvi-api", "PINVI_KOR_TRAVEL_WEATHER_BASE_URL"),
        ("pinvi-api", "PINVI_KOR_TRAVEL_GEO_BASE_URL"),
    }
)

_URL = re.compile(r"https?://([^/:}\s'\"]+):(\d+)")
_DEFAULTED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*)\}")


def _documents() -> tuple[dict[str, Any], dict[str, Any]]:
    compose = load_yaml_rejecting_duplicate_keys(_COMPOSE.read_text(encoding="utf-8"))
    targets = load_yaml_rejecting_duplicate_keys(_TARGETS.read_text(encoding="utf-8"))
    return compose, targets


def _sibling_ports() -> dict[int, str]:
    ports = {port: target for target, port in _CONSUMER_PORT.items()}
    ports.update(_RETIRED_CONSUMER_PORTS)
    return ports


def _default_host(targets: Mapping[str, Any]) -> str:
    return next(name for name, spec in targets["hosts"].items() if spec.get("default") is True)


def _expected_origin(targets: Mapping[str, Any], sibling: str) -> str:
    default = _default_host(targets)
    host = _HOST_OVERRIDE.get(sibling) or targets["targets"][sibling].get("host", default)
    addr = "127.0.0.1" if host == default else targets["hosts"][host]["addr"]
    return f"http://{addr}:{_CONSUMER_PORT[sibling]}"


def _owned_services(compose: Mapping[str, Any], targets: Mapping[str, Any]) -> dict[str, str]:
    """compose 서비스 → 그것을 가진 target(services·containers·옛 Dagster family)."""

    owner: dict[str, str] = {}
    containers = targets["containers"]
    for target_id, spec in targets["targets"].items():
        if target_id == "all" or spec.get("external_project"):
            continue
        names = set(spec.get("services") or [])
        names |= {containers[c]["compose_service"] for c in spec.get("containers") or []}
        for name in names:
            owner.setdefault(name, target_id)
    for target_id, family in derive_dagster_families(compose, targets).items():
        for name in (*family.legacy, family.code_server):
            owner.setdefault(name, target_id)
    return owner


def _env_items(service: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    for block in (service.get("environment"), (service.get("build") or {}).get("args")):
        if isinstance(block, Mapping):
            yield from ((str(k), str(v)) for k, v in block.items() if v is not None)
        elif isinstance(block, list):
            for entry in block:
                key, _, value = str(entry).partition("=")
                yield key, value


def _scan(compose: Mapping[str, Any], targets: Mapping[str, Any]) -> dict[tuple[str, str], tuple[str, str]]:
    """(서비스, env) → (형제 target, 원문 값). 형제 포트를 가진 URL이 값 어디에든(기본값 포함) 있으면 잡는다."""

    ports = _sibling_ports()
    owner = _owned_services(compose, targets)
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for name, service in compose["services"].items():
        for key, value in _env_items(service):
            if key.startswith("NEXT_PUBLIC_"):
                continue
            for _host, port in _URL.findall(value):
                sibling = ports.get(int(port))
                if sibling is None or owner.get(name) == sibling:
                    continue
                found[(name, key)] = (sibling, value)
    return found


def _violations(compose: Mapping[str, Any], targets: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    for (name, key), (sibling, value) in sorted(_scan(compose, targets).items()):
        expected = _expected_origin(targets, sibling)
        match = _DEFAULTED.fullmatch(value)
        if match is None:
            violations.append(
                f"`{name}`.{key}: `{value}` — root `.env`로 재정의할 수 있는 `${{VAR:-{expected}}}` 꼴이어야 한다"
            )
        elif match.group(2) != expected:
            violations.append(f"`{name}`.{key}: 기본값 `{match.group(2)}` ≠ 형제 `{sibling}`의 origin `{expected}`")
    return violations


def test_the_scan_sees_every_known_consumer() -> None:
    compose, targets = _documents()
    found = set(_scan(compose, targets))
    assert _KNOWN_CONSUMERS <= found, sorted(_KNOWN_CONSUMERS - found)


def test_every_consumer_default_is_the_sibling_origin() -> None:
    compose, targets = _documents()
    assert _violations(compose, targets) == []


def _override_violations(targets: Mapping[str, Any]) -> list[str]:
    default = _default_host(targets)
    violations: list[str] = []
    for sibling, host in _HOST_OVERRIDE.items():
        if host not in targets["hosts"]:
            violations.append(f"`{sibling}` 재정의 호스트 `{host}`가 `hosts:`에 없다")
        elif targets["targets"][sibling].get("host", default) == host:
            violations.append(f"`{sibling}` 재정의는 이제 군더더기다 — registry가 같은 호스트를 말한다")
    return violations


def test_host_overrides_only_cover_targets_still_on_another_host() -> None:
    """재정의는 registry가 아직 못 말하는 것만 — geo가 odroid로 플립되면(registry가 같은 말을 하면) 지운다."""

    _, targets = _documents()
    assert _override_violations(targets) == []


def test_without_the_geo_override_the_geo_consumers_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """빨간 대조군 — 재정의를 빼면 geo 기대 origin은 registry의 n150 loopback이 되어 odroid를 부르는 geo 소비자가 빨갛다."""

    compose, targets = _documents()
    monkeypatch.setattr(sys.modules[__name__], "_HOST_OVERRIDE", {})
    assert [v for v in _violations(compose, targets) if "형제 `geo`" in v]


def test_a_redundant_override_is_named() -> None:
    """빨간 대조군 — geo를 registry에서 odroid로 옮기면 재정의가 군더더기라 빨갛다."""

    _, targets = _documents()
    targets["targets"]["geo"]["host"] = "odroid"
    assert any("`geo` 재정의는 이제 군더더기" in v for v in _override_violations(targets))


@pytest.mark.parametrize("sibling", ["transport", "weather"])
def test_moving_a_target_back_to_the_default_host_is_named(sibling: str) -> None:
    """빨간 대조군 — registry에서 `host:`를 지우면(n150으로 되돌리면) 기대 origin이 loopback이 되어 그 소비자가 빨갛다."""

    compose, targets = _documents()
    del targets["targets"][sibling]["host"]
    assert _expected_origin(targets, sibling).startswith("http://127.0.0.1:")
    named = [v for v in _violations(compose, targets) if "`" + sibling + "`" in v]
    assert named, _violations(compose, targets)


#: 형제 → n150 시절 소비자 포트(되돌림 대조군의 입력).
_N150_PORT: Mapping[str, int] = {"geo": 12501, "transport": 14001, "weather": 14101}


@pytest.mark.parametrize("consumer", sorted(_KNOWN_CONSUMERS))
def test_reverting_any_one_consumer_to_its_n150_loopback_is_named(consumer: tuple[str, str]) -> None:
    """빨간 대조군 — 한 곳만 옛 n150 loopback으로 되돌려도 그 곳이 이름으로 빨갛다."""

    compose, targets = _documents()
    name, key = consumer
    sibling, _value = _scan(compose, targets)[consumer]
    compose["services"][name]["environment"][key] = f"${{{key}:-http://127.0.0.1:{_N150_PORT[sibling]}}}"
    violations = _violations(compose, targets)
    assert [v for v in violations if v.startswith(f"`{name}`.{key}:")], violations


def test_a_literal_consumer_url_is_named() -> None:
    """`.env`로 재정의할 수 없는 리터럴(옛 PinVi ETL `http://127.0.0.1:14101`의 모양)은 빨갛다."""

    compose, targets = _documents()
    compose["services"]["pinvi-dagster-code-server"]["environment"]["PINVI_KOR_TRAVEL_WEATHER_BASE_URL"] = (
        "http://192.168.1.13:12201"
    )
    assert any("꼴이어야 한다" in v for v in _violations(compose, targets))


def test_env_example_does_not_shadow_a_consumer_default() -> None:
    """`.env.example`이 소비자 origin의 원천 변수에 값을 주면 그것을 베낀 root `.env`가 compose 기본값을 조용히 가린다
    (옛 예시 `KOR_TRAVEL_MAP_KOR_TRAVEL_TRANSPORT_BASE_URL=http://127.0.0.1:14001`). 원천 이름은 스캔한 기본값에서 읽는다."""

    compose, targets = _documents()
    sources = {
        match.group(1)
        for _sibling, value in _scan(compose, targets).values()
        if (match := _DEFAULTED.fullmatch(value)) is not None
    }
    assert sources, "스캔이 원천 변수를 하나도 못 찾았다 — 이 검사가 공허하다"
    lines = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assigned = sorted(
        line for line in lines if not line.lstrip().startswith("#") and line.partition("=")[0].strip() in sources
    )
    assert assigned == [], assigned
