"""공용 Dagster runtime의 열린 파일 상한·launch 예산·tick 보존을 렌더 원문에서 결박한다(2026-11-23 수집 실패 감사).

- **nofile**: n150 code-server가 soft 1024에 닿아 launch가 `[Errno 24] Too many open files`로 실패했다(14일 1,888건,
  원인은 PinVi·Concierge의 일회성 fd 누수 두 건 — 상향은 완화다). 상시 Dagster 서비스(daemon·webserver·공용 plane
  code-server)는 `x-dagster-nofile` 한 정의를 받는다. 누수를 오래 숨기지 않게 상한은 보수적이다(soft 8192·hard 65536). 이름을 외우지
  않는다 — 공용 instance URL을 받는 상시 서비스(꺼진 legacy profile 제외)를 모양으로 찾는다.
- **launch 예산**: daemon의 StartRun은 code-server proxy를 거쳐 자식에 닿는다. proxy 예산이 daemon보다 크거나 같으면
  daemon 예산을 올려도 proxy가 먼저 끊는다. 그래서 모든 code-server proxy < daemon < run 시작 감시 상한이다.
- **queue 재시도**: launch 전 재시도 창(횟수 × 간격)이 code-server 냉기동 창(healthcheck `start_period`)을 덮어야
  code-server 재시작 동안 꺼낸 run이 FAILURE가 되지 않는다.
- **호스트 파생**: odroid의 instance 파일은 기본 파일에서 파생한다 — 기본의 재시도·보존·로그 수준이 odroid 파생본에도
  그대로 있어야 한다(감사 §2.6 7: odroid plane이 #610·#550을 못 받았다).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_dagster_shared_workspace_is_derived import _resolve

from kor_travel_docker_manager.services import shared_dagster_instance as instance
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker-compose.yml"
_TARGETS = _ROOT / "config" / "docker-targets.yml"
_INSTANCE = _ROOT / "config" / "dagster-shared" / "dagster.yaml"
_URL_ENV = "KOR_TRAVEL_DAGSTER_SHARED_PG_URL"
_DAEMON = "kor-travel-dagster-daemon"
_WEBSERVER = "kor-travel-dagster-webserver"
#: odroid root `.env`·host compose의 오늘 예산(daemon 360, geo 300). code-server 공통 변수도 같은 자리에 둔다.
_ODROID_BUDGET = {
    "KTDM_DAGSTER_GRPC_TIMEOUT_SECONDS": "360",
    "KTDM_GEO_DAGSTER_GRPC_TIMEOUT_SECONDS": "300",
    "KTDM_CODE_SERVER_DAGSTER_GRPC_TIMEOUT_SECONDS": "300",
}
_PINNED_RPC = {
    "DAGSTER_SCHEDULE_GRPC_TIMEOUT_SECONDS": "60",
    "DAGSTER_SENSOR_GRPC_TIMEOUT_SECONDS": "60",
    "DAGSTER_REPOSITORY_GRPC_TIMEOUT_SECONDS": "180",
    "DAGSTER_GRPC_SHUTDOWN_GRACE_PERIOD": "60",
}


def _compose() -> dict[str, Any]:
    document = load_yaml_rejecting_duplicate_keys(_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _instance() -> dict[str, Any]:
    document = load_yaml_rejecting_duplicate_keys(_INSTANCE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _long_running_dagster_services() -> dict[str, Mapping[str, Any]]:
    """공용 instance URL을 받는 상시 서비스(legacy profile 제외) — storage migrate one-shot은 빠진다."""

    found = {}
    for name, service in _compose()["services"].items():
        environment = service.get("environment") or {}
        if (_URL_ENV in environment and service.get("restart") == "unless-stopped"
                and not service.get("profiles")):
            found[name] = service
    return found


def _code_servers() -> dict[str, Mapping[str, Any]]:
    servers = {}
    for name, service in _long_running_dagster_services().items():
        command = [str(part) for part in service.get("command") or []]
        if any(command[i:i + 2] == ["code-server", "start"] for i in range(len(command))):
            servers[name] = service
    return servers


def _seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)(s|m)?", str(value))
    assert match, value
    return int(match.group(1)) * (60 if match.group(2) == "m" else 1)


def test_the_derivation_finds_the_whole_runtime() -> None:
    services = _long_running_dagster_services()
    assert {_DAEMON, _WEBSERVER} <= set(services)
    # 오늘 compose의 공용 plane code-server 다섯(concierge·map·pinvi·geo·weather). 새 code-server는 자동으로 든다.
    assert len(_code_servers()) >= 5, sorted(_code_servers())
    assert set(services) == {_DAEMON, _WEBSERVER, *_code_servers()}, sorted(services)


def test_every_dagster_runtime_service_raises_the_open_file_soft_limit() -> None:
    anchor = _compose()["x-dagster-nofile"]
    nofile = anchor["nofile"]
    assert type(nofile["soft"]) is int and type(nofile["hard"]) is int
    # 평소 fd 13~21개의 수백 배 여유, 그러나 누수를 며칠씩 숨기지 않을 만큼만(soft 8192·hard 65536).
    assert (nofile["soft"], nofile["hard"]) == (8192, 65536)
    missing = {name: service.get("ulimits") for name, service in _long_running_dagster_services().items()
               if service.get("ulimits") != anchor}
    assert not missing, missing


def test_every_code_server_proxy_budget_is_below_the_daemon_budget() -> None:
    services = _compose()["services"]
    start_timeout = _instance()["run_monitoring"]["start_timeout_seconds"]
    daemon = services[_DAEMON]["environment"]["DAGSTER_GRPC_TIMEOUT_SECONDS"]
    for overrides in ({}, _ODROID_BUDGET):
        outer = int(_resolve(daemon, overrides))
        for name, service in _code_servers().items():
            environment = service["environment"]
            assert "DAGSTER_GRPC_TIMEOUT_SECONDS" in environment, name
            inner = int(_resolve(str(environment["DAGSTER_GRPC_TIMEOUT_SECONDS"]), overrides))
            assert 60 < inner < outer < start_timeout, (name, overrides, inner, outer)
            # 일반 예산을 올려도 schedule·sensor·repository·shutdown 유예의 기본값이 따라 오르지 않는다.
            assert {key: environment.get(key) for key in _PINNED_RPC} == _PINNED_RPC, name
    assert int(_resolve(daemon, {})) == 180


def test_queue_retry_window_covers_a_code_server_cold_start() -> None:
    queue = _instance()["run_queue"]
    window = queue["max_user_code_failure_retries"] * queue["user_code_failure_retry_delay"]
    starts = [_seconds(service["healthcheck"]["start_period"]) for service in _code_servers().values()]
    assert starts and window >= max(starts), (window, starts)


def test_skipped_sensor_ticks_are_kept_shortest() -> None:
    sensor = _instance()["retention"]["sensor"]["purge_after_days"]
    assert sensor["skipped"] == 2
    assert sensor["skipped"] < min(sensor["success"], sensor["started"], sensor["failure"])


@pytest.mark.parametrize("host", ["n150", "odroid"])
def test_each_host_derivation_keeps_the_base_runtime_policy(host: str) -> None:
    """odroid 파생본도 기본의 queue 재시도·자동 재실행·보존·로그 수준을 그대로 싣는다."""

    raw = _INSTANCE.read_bytes()
    spec = yaml.safe_load(_TARGETS.read_bytes())["hosts"][host]
    effective = yaml.safe_load(instance._host_configuration(raw, spec["dagster_max_concurrent_runs"], spec))
    base = _instance()
    for section in ("run_queue", "run_retries", "retention", "run_monitoring"):
        assert effective[section] == base[section], (host, section)
    assert effective["python_logs"] == base["python_logs"]
    assert effective["python_logs"]["python_log_level"] == "INFO"
    assert effective["concurrency"]["runs"]["max_concurrent_runs"] == spec["dagster_max_concurrent_runs"]
