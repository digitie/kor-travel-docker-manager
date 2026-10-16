"""중앙 Prometheus 타깃 == registry 파생값(ADR-58 결정 11, `docs/bindings.md` B-8, T-MGR-04).

`config/prometheus/prometheus.yml`은 커밋된 파생물이다. 기대값은 `services/prometheus_targets.py`가 registry에서 계산한다 —
타깃을 registry와 다르게 고치거나(호스트 이동·포트·경로) registry를 바꾸고 파일을 그대로 두면 빨갛다.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import prometheus_targets, registry
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_ROOT = Path(__file__).resolve().parents[2]


def _registry() -> dict[str, Any]:
    return load_yaml_rejecting_duplicate_keys((_ROOT / "config/docker-targets.yml").read_text(encoding="utf-8"))


def _file_jobs() -> dict[str, dict[str, Any]]:
    document = load_yaml_rejecting_duplicate_keys((_ROOT / "config/prometheus/prometheus.yml").read_text(encoding="utf-8"))
    return {job["job_name"]: job for job in document["scrape_configs"]}


def _mismatch(config: dict[str, Any], jobs: dict[str, dict[str, Any]]) -> list[str]:
    derived = {job["job_name"]: job for job in prometheus_targets.derived_scrape_configs(config)}
    committed = {name: job for name, job in jobs.items() if name not in prometheus_targets.NON_REGISTRY_JOBS}
    problems = [f"missing job {name}" for name in sorted(set(derived) - set(committed))]
    problems += [f"job {name} is not derived from the registry" for name in sorted(set(committed) - set(derived))]
    problems += [
        f"job {name}: {committed[name]} != {derived[name]}"
        for name in sorted(set(derived) & set(committed))
        if committed[name] != derived[name]
    ]
    return problems


def test_prometheus_targets_are_the_registry_derivation() -> None:
    assert _mismatch(_registry(), _file_jobs()) == []


def test_the_derivation_sees_remote_and_local_hosts() -> None:
    # 하한은 본 것에 건다 — 파생이 비거나 한 호스트만 보면 위 대조가 항진이 된다.
    config = _registry()
    labels = {
        static["labels"]["host"]
        for job in prometheus_targets.derived_scrape_configs(config)
        for static in job["static_configs"]
    }
    assert labels == set(config["hosts"])


def test_only_the_manager_backend_job_is_outside_the_registry() -> None:
    derived = {job["job_name"] for job in prometheus_targets.derived_scrape_configs(_registry())}
    assert set(_file_jobs()) - derived == set(prometheus_targets.NON_REGISTRY_JOBS)


def test_moving_a_target_is_red_until_the_file_follows() -> None:
    config = _registry()
    moved = next(
        name for name, spec in config["targets"].items()
        if spec.get("host") not in (None, "all") and not spec.get("external_project")
        and any(config["containers"][c].get("metrics_path") for c in spec.get("containers") or [])
    )
    del config["targets"][moved]["host"]  # default(= Prometheus 호스트)로 되돌린다
    assert _mismatch(config, _file_jobs())


def test_a_third_host_adds_static_configs_without_code_changes() -> None:
    """N-host: 호스트를 하나 더하면 `all` 호스트 exporter(cAdvisor)에 그 호스트의 static_config가 생기고 나머지는 그대로다."""

    config = _registry()
    before = {job["job_name"]: job for job in prometheus_targets.derived_scrape_configs(config)}
    grown = copy.deepcopy(config)
    grown["hosts"]["mars"] = {"addr": "192.0.2.7"}
    after = {job["job_name"]: job for job in prometheus_targets.derived_scrape_configs(grown)}
    changed = {name for name in after if after[name] != before.get(name)}
    everywhere = {
        name for name, spec in config["containers"].items()
        if spec.get("metrics_path") and registry.container_hosts(name, grown) == set(grown["hosts"])
    }
    assert changed == everywhere and everywhere
    for name in everywhere:
        assert {"targets": ["192.0.2.7:" + after[name]["static_configs"][0]["targets"][0].rsplit(":", 1)[1]],
                "labels": {"host": "mars"}} in after[name]["static_configs"]
    assert _mismatch(grown, _file_jobs())


def test_a_changed_port_or_path_is_red() -> None:
    jobs = _file_jobs()
    name = next(iter(job for job in jobs if job not in prometheus_targets.NON_REGISTRY_JOBS))
    broken = copy.deepcopy(jobs)
    broken[name]["metrics_path"] = "/elsewhere"
    assert _mismatch(_registry(), broken)
    broken = copy.deepcopy(jobs)
    broken[name]["static_configs"][0]["targets"] = ["127.0.0.1:1"]
    assert _mismatch(_registry(), broken)


@pytest.mark.parametrize(
    ("connection", "path"),
    [("http://{host}:12501", "metrics"), ("http://{host}:12501", 1), ("내부 전용(127.0.0.1:12501)", "/metrics")],
)
def test_registry_rejects_a_metrics_path_it_cannot_derive(connection: str, path: Any) -> None:
    config = _registry()
    container = next(name for name, spec in config["containers"].items() if spec.get("metrics_path"))
    config["containers"][container]["connection"] = connection
    config["containers"][container]["metrics_path"] = path
    with pytest.raises(registry.TargetsConfigError, match="metrics_path"):
        registry._validate_targets_config(config, label="test")


def test_the_prometheus_host_must_be_single() -> None:
    config = _registry()
    prom_target = next(
        name for name, spec in config["targets"].items()
        if "prometheus" in (spec.get("services") or [])
    )
    config["targets"][prom_target]["host"] = "all"
    with pytest.raises(registry.TargetsConfigError, match="exactly one host"):
        prometheus_targets.prometheus_host(config)
