"""중앙 Prometheus의 scrape 타깃을 registry에서 파생한다(ADR-58 결정 11·R1, `docs/bindings.md` B-8, T-MGR-04).

`config/prometheus/prometheus.yml`은 **커밋된 파생물**이다 — Prometheus는 registry를 읽지 못한다. 규칙 하나:

- registry 컨테이너 중 `metrics_path`가 있는 것마다 job 하나(`job_name` = 컨테이너 id). 포트는 connection의 포트,
  metrics가 다른 포트에서 나오면 컨테이너의 `metrics_port`.
- 그 컨테이너가 존재하는 호스트(`registry.container_hosts`)마다 static_config 하나 — 주소는 R1: Prometheus가 도는
  호스트면 `127.0.0.1`, 아니면 그 호스트의 `addr`; 포트는 `connection`(`scheme://{host}:<port>`)의 포트;
  `labels: {host: <호스트 이름>}`. 호스트를 하나 더하면 코드 수정 없이 static_config만 늘어난다.
- Prometheus가 도는 호스트는 이 파일을 마운트하는 compose 서비스(registry `compose_binds`)의 컨테이너 호스트다.
- `/metrics`가 Bearer token을 요구하는 컨테이너는 registry `metrics_bearer_env`에 그 token의 env 이름을 적는다
  (T-READ-MGR-02). job은 `authorization.credentials_file`로 `SCRAPE_AUTH_DIR/<env 이름>`을 읽는다 — token은 파일에도
  repo에도 적지 않는다. 그 파일은 Prometheus 컨테이너의 entrypoint가 이 파일에 적힌 경로에서 env 이름을 읽어 같은 이름의
  env 값으로 만든다(`docker-compose.yml` `prometheus`). 그래서 compose `prometheus.environment`에는 그 이름들이 있어야 한다.

Manager 자신의 backend(`kor-travel-docker-manager` — compose 컨테이너가 아니라 systemd)도 같은 규칙으로 파생한다
(T-MGR-NH NH-6): Manager는 **선언된 모든 호스트**에서 돈다(ADR-58 결정 2) → `hosts:` 선언 순서로 호스트마다 static_config
하나, 주소는 R1. 포트는 그 프로세스를 띄우는 unit(`deploy/systemd/ktdm-backend.service`의 `--port`)에서 읽는다 — 손으로 쓴
job은 남지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from kor_travel_docker_manager.services import registry

PROMETHEUS_CONFIG: Final = "./config/prometheus/prometheus.yml"
#: Prometheus 컨테이너 안에서 entrypoint가 scrape Bearer token 파일을 두는 곳(`docker-compose.yml` `prometheus`).
SCRAPE_AUTH_DIR: Final = "/tmp/scrape-auth"
#: Manager backend job(NH-6). 경로는 backend의 `/metrics` route, 포트는 backend unit의 `--port`(아래 `manager_backend_port`).
MANAGER_JOB: Final = "kor-travel-docker-manager"
MANAGER_METRICS_PATH: Final = "/metrics"
MANAGER_BACKEND_UNIT: Final = "deploy/systemd/ktdm-backend.service"
_EXEC_START = re.compile(r"^ExecStart=(.*)$", re.MULTILINE)
#: 한 ExecStart 줄 안의 `--port` **전부**(줄당 마지막 하나만 세면 `--port A --port B`가 조용히 B를 고른다).
_PORT_FLAG = re.compile(r"(?:^|\s)--port[ =](\d+)(?=\s|$)")


#: 이 코드가 들어 있는 소스 트리(`backend/src/kor_travel_docker_manager/services/` 위 네 단계). `prometheus.yml`은 커밋된
#: 파생물이라 대조는 소스 트리에서 한다 — 배포 설정 root(`KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT`)를 따르지 않는다.
_SOURCE_ROOT: Final = Path(__file__).resolve().parents[4]


def manager_backend_port(source_root: str | None = None) -> int:
    """Manager backend가 듣는 포트 — 그것을 띄우는 systemd unit의 `ExecStart … --port N`(하나여야 한다)."""
    unit = Path(source_root) / MANAGER_BACKEND_UNIT if source_root else _SOURCE_ROOT / MANAGER_BACKEND_UNIT
    lines = _EXEC_START.findall(unit.read_text(encoding="utf-8"))
    ports = [port for line in lines for port in _PORT_FLAG.findall(line)]
    if len(ports) != 1:
        raise registry.TargetsConfigError(f"{MANAGER_BACKEND_UNIT} must have exactly one ExecStart --port (found {ports})")
    return int(ports[0])


def prometheus_host(config: Mapping[str, Any]) -> str:
    """이 파일을 읽는 Prometheus가 도는 호스트 — 파일을 마운트하는 compose 서비스의 컨테이너 호스트(하나여야 한다)."""
    services = [
        service
        for service, entries in (config.get("compose_binds") or {}).items()
        if any(entry.get("source") == PROMETHEUS_CONFIG for entry in entries or [])
    ]
    containers = [
        name for name, spec in config["containers"].items()
        if spec["compose_service"] in services and not spec.get("external_project")
    ]
    hosts = {host for name in containers for host in registry.container_hosts(name, config)}
    if len(hosts) != 1:
        raise registry.TargetsConfigError(
            f"the Prometheus that reads {PROMETHEUS_CONFIG} must run on exactly one host (found {sorted(hosts)})"
        )
    return next(iter(hosts))


def _address(host: str, home: str, hosts: Mapping[str, Any]) -> str:
    """R1: Prometheus가 도는 호스트면 127.0.0.1, 아니면 그 호스트의 `addr`."""
    return "127.0.0.1" if host == home else str(hosts[host]["addr"])


def derived_scrape_configs(config: Mapping[str, Any], *, manager_port: int | None = None) -> list[dict[str, Any]]:
    """파생한 scrape_configs — Manager backend job 먼저, 그다음 registry 컨테이너 순서. 호스트는 `hosts:` 선언 순서."""
    home = prometheus_host(config)
    hosts = config["hosts"]
    port_of_manager = manager_port if manager_port is not None else manager_backend_port()
    jobs: list[dict[str, Any]] = [
        {
            "job_name": MANAGER_JOB,
            "metrics_path": MANAGER_METRICS_PATH,
            "static_configs": [
                {"targets": [f"{_address(host, home, hosts)}:{port_of_manager}"], "labels": {"host": host}}
                for host in hosts
            ],
        }
    ]
    for name, spec in config["containers"].items():
        path = spec.get("metrics_path")
        if path is None:
            continue
        # metrics가 connection과 다른 포트에서 나오면 `metrics_port`(T-MGR-04r), 아니면 connection의 포트(형태는 로더가 강제한다).
        port = spec.get("metrics_port") or registry.HOST_URL.fullmatch(spec["connection"]).group(1)
        present = registry.container_hosts(name, config)
        static_configs = [
            {
                "targets": [f"{_address(host, home, hosts)}:{port}"],
                "labels": {"host": host},
            }
            for host in hosts
            if host in present
        ]
        if static_configs:
            job: dict[str, Any] = {"job_name": name, "metrics_path": path}
            bearer_env = spec.get("metrics_bearer_env")
            if bearer_env is not None:
                job["authorization"] = {"type": "Bearer", "credentials_file": f"{SCRAPE_AUTH_DIR}/{bearer_env}"}
            job["static_configs"] = static_configs
            jobs.append(job)
    return jobs


def scrape_bearer_envs(config: Mapping[str, Any]) -> list[str]:
    """scrape Bearer token의 env 이름들(registry `metrics_bearer_env`) — compose `prometheus.environment`가 받아야 하는 이름."""
    return sorted({str(spec["metrics_bearer_env"]) for spec in config["containers"].values() if spec.get("metrics_bearer_env")})
