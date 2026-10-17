"""중앙 Prometheus의 scrape 타깃을 registry에서 파생한다(ADR-58 결정 11·R1, `docs/bindings.md` B-8, T-MGR-04).

`config/prometheus/prometheus.yml`은 **커밋된 파생물**이다 — Prometheus는 registry를 읽지 못한다. 규칙 하나:

- registry 컨테이너 중 `metrics_path`가 있는 것마다 job 하나(`job_name` = 컨테이너 id). 포트는 connection의 포트,
  metrics가 다른 포트에서 나오면 컨테이너의 `metrics_port`.
- 그 컨테이너가 존재하는 호스트(`registry.container_hosts`)마다 static_config 하나 — 주소는 R1: Prometheus가 도는
  호스트면 `127.0.0.1`, 아니면 그 호스트의 `addr`; 포트는 `connection`(`scheme://{host}:<port>`)의 포트;
  `labels: {host: <호스트 이름>}`. 호스트를 하나 더하면 코드 수정 없이 static_config만 늘어난다.
- Prometheus가 도는 호스트는 이 파일을 마운트하는 compose 서비스(registry `compose_binds`)의 컨테이너 호스트다.

registry 밖의 job(Manager 자신의 backend `kor-travel-docker-manager` — compose 컨테이너가 아니다)은 이 파생의 대상이
아니고 파일에 그대로 둔다(`NON_REGISTRY_JOBS`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from kor_travel_docker_manager.services import registry

PROMETHEUS_CONFIG: Final = "./config/prometheus/prometheus.yml"
#: registry 컨테이너가 아닌 수집 대상. 호스트별 Manager job은 odroid Manager 설치(T-OPS-01) 뒤에 같은 규칙으로 들인다.
NON_REGISTRY_JOBS: Final = frozenset({"kor-travel-docker-manager"})



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


def derived_scrape_configs(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """registry에서 파생한 scrape_configs(registry 컨테이너 순서, 호스트는 `hosts:` 선언 순서)."""
    home = prometheus_host(config)
    hosts = config["hosts"]
    jobs: list[dict[str, Any]] = []
    for name, spec in config["containers"].items():
        path = spec.get("metrics_path")
        if path is None:
            continue
        # metrics가 connection과 다른 포트에서 나오면 `metrics_port`(T-MGR-04r), 아니면 connection의 포트(형태는 로더가 강제한다).
        port = spec.get("metrics_port") or registry.HOST_URL.fullmatch(spec["connection"]).group(1)
        present = registry.container_hosts(name, config)
        static_configs = [
            {
                "targets": [f"{'127.0.0.1' if host == home else hosts[host]['addr']}:{port}"],
                "labels": {"host": host},
            }
            for host in hosts
            if host in present
        ]
        if static_configs:
            jobs.append({"job_name": name, "metrics_path": path, "static_configs": static_configs})
    return jobs
