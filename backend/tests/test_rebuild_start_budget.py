"""pinned 재구축의 `up --wait`가 기다리는 서비스는 **부하 속 냉기동**을 healthcheck 창 안에 마친다.

경위(2026-10-04 n150, 재구축 세 번 실패 — 매번 Map·PinVi를 멈춘 채 끝났다).

- docker는 `start_period` 안의 실패를 세지 않는다. 그 뒤로는 `interval`마다 하나씩 세어 `retries`번이면
  **unhealthy**다. compose `--wait`는 `starting`이면 기다리지만 `unhealthy`를 보는 즉시 실패한다
  (`isServiceHealthy`) — `--wait-timeout 900`까지 가지 않는다. 그래서 실제 기동 한도는
  `start_period + retries × interval`이다.
- Map code-server: 180초 + 5 × 30초 = 약 330초. 재구축 중(새 이미지라 page cache가 차갑고 Map UI가 같이
  뜬다) entrypoint 검사·`runtime_preflight`·proxy의 dagster import에 약 4분, 자식이 import를 시작한 것이
  약 4분 55초였다 — 5분 20초에 unhealthy. proxy는 자식이 다 실릴 때까지 listen하지 않으므로 그동안 probe는
  `Check`부터 닿지 못해 출력 없이 exit 1이다(공용 probe의 "첫 healthy 전" 길). 같은 컨테이너를 손으로
  `docker start`하니 2분 56초에 healthy — 180초 창을 2초 남기고 통과했다.
- pinvi-api: 20초 + 3 × 30초 = 약 110초. 재구축에서 131초에 unhealthy(로그 0줄 — uvicorn이 import를
  마치지 못했다). 손으로 띄운 것도 첫 healthy가 3분 뒤였다(그 사이 docker는 이미 unhealthy로 표시했다 —
  기다리는 이가 없었을 뿐이다).

그래서 창을 실측 냉기동보다 넉넉히 둔다. `start_interval`이 짧으므로 빨리 뜨면 창을 다 쓰지 않는다 —
창을 늘려도 정상 기동은 느려지지 않는다. 대신 창 전체(`start_period + retries × interval`)는 재구축의
`--wait-timeout` 안이어야 한다 — 넘으면 정말 실패한 기동이 unhealthy 대신 막연한 timeout으로 끝난다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from kor_travel_docker_manager.services import compose_service
from kor_travel_docker_manager.services.runtime_topology import runtime_topology

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

#: 장기 실행 code-server의 두 모양(liveness 계약과 같은 유도 — 이름으로 찾지 않는다).
_CODE_SERVER_COMMANDS = ("dagster code-server start", "dagster api grpc")

#: 실측 냉기동(위)보다 넉넉한 하한. code-server는 재구축 실패 때 5분 20초 안에 자식 import도 끝내지
#: 못했으므로 10분, 나머지(API·UI)는 pinvi-api 손 기동도 3분이었으므로 5분.
_MIN_CODE_SERVER_START_PERIOD_SECONDS = 600.0
_MIN_START_PERIOD_SECONDS = 300.0

#: 창 안에서 probe 간격. 이보다 길면 빨리 뜬 서비스도 그만큼 늦게 healthy가 된다.
_MAX_START_INTERVAL_SECONDS = 5.0

#: docker가 비워 둔 값에 쓰는 기본값(`docker run --health-*` 기본).
_DOCKER_DEFAULTS = {"interval": "30s", "timeout": "30s", "retries": 3, "start_interval": "5s"}


def _services() -> dict[str, dict[str, Any]]:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]


def _command_text(value: object) -> str:
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return value if isinstance(value, str) else ""


def _seconds(value: object) -> float:
    """compose duration(`90s`, `2m`, `1m30s`)을 초로 읽는다."""
    total, number = 0.0, ""
    for char in str(value):
        if char.isdigit() or char == ".":
            number += char
            continue
        assert char in {"h": 0, "m": 0, "s": 0} and number, (value, "unsupported duration")
        total += float(number) * {"h": 3600.0, "m": 60.0, "s": 1.0}[char]
        number = ""
    assert not number, (value, "duration without a unit")
    return total


def _code_servers() -> set[str]:
    return {
        name
        for name, service in _services().items()
        if any(
            command in _command_text(service.get(key))
            for command in _CODE_SERVER_COMMANDS
            for key in ("command", "entrypoint")
        )
    }


def _waited_services() -> list[str]:
    """재구축이 `up --wait`하는 slot 서비스(`_deploy_forward`가 slot에서 서비스를 고르는 그 topology)와
    모든 code-server(같은 공용 probe를 쓴다). healthcheck가 없는 서비스는 compose가 `running`으로 보므로
    창이 없다 — 뺀다."""
    services = _services()
    waited = set(runtime_topology().runtime_services) | _code_servers()
    return sorted(name for name in waited if (services.get(name) or {}).get("healthcheck"))


def _healthcheck(service_name: str) -> dict[str, Any]:
    return {**_DOCKER_DEFAULTS, **(_services()[service_name].get("healthcheck") or {})}


def test_the_waited_services_are_derived() -> None:
    waited = _waited_services()
    for name in (
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster-code-server",
        "pinvi-api",
        "pinvi-dagster-code-server",
    ):
        assert name in waited, (name, waited)


@pytest.mark.parametrize("service_name", _waited_services())
def test_a_cold_start_under_rebuild_load_fits_the_start_window(service_name: str) -> None:
    healthcheck = _healthcheck(service_name)
    floor = (
        _MIN_CODE_SERVER_START_PERIOD_SECONDS
        if service_name in _code_servers()
        else _MIN_START_PERIOD_SECONDS
    )
    start_period = _seconds(healthcheck.get("start_period", "0s"))
    assert start_period >= floor, (
        f"`{service_name}`의 start_period가 {start_period:g}초다(하한 {floor:g}초) — 재구축 중 냉기동이 그 창을 "
        "넘겨 compose `--wait`가 unhealthy로 실패하고 재구축이 Map·PinVi를 멈춘다(2026-10-04 n150)."
    )
    start_interval = _seconds(healthcheck["start_interval"])
    assert start_interval <= _MAX_START_INTERVAL_SECONDS, (
        f"`{service_name}`의 start_interval이 {start_interval:g}초다 — 긴 창에서 빨리 뜬 서비스가 "
        "늦게 healthy가 된다."
    )


@pytest.mark.parametrize("service_name", _waited_services())
def test_the_whole_start_window_ends_inside_the_rebuild_wait(service_name: str) -> None:
    """창 = start_period + retries × (interval + timeout). probe 하나가 timeout까지 걸리면 다음 probe는
    그만큼 늦게 시작한다."""
    healthcheck = _healthcheck(service_name)
    cycle = _seconds(healthcheck["interval"]) + _seconds(healthcheck["timeout"])
    window = _seconds(healthcheck.get("start_period", "0s")) + int(healthcheck["retries"]) * cycle
    assert window < compose_service._COMPOSE_WAIT_TIMEOUT_SECONDS, (
        f"`{service_name}`의 기동 창({window:g}초)이 재구축 `--wait-timeout`"
        f"({compose_service._COMPOSE_WAIT_TIMEOUT_SECONDS}초) 이상이다 — 정말 실패한 기동이 "
        "unhealthy 대신 timeout으로 끝난다."
    )
