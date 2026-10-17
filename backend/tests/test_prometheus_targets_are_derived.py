"""중앙 Prometheus 타깃 == registry 파생값(ADR-58 결정 11, `docs/bindings.md` B-8, T-MGR-04).

`config/prometheus/prometheus.yml`은 커밋된 파생물이다. 기대값은 `services/prometheus_targets.py`가 registry에서 계산한다 —
타깃을 registry와 다르게 고치거나(호스트 이동·포트·경로) registry를 바꾸고 파일을 그대로 두면 빨갛다.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

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
    committed = dict(jobs)
    problems = [f"missing job {name}" for name in sorted(set(derived) - set(committed))]
    problems += [f"job {name} is not derived from the registry" for name in sorted(set(committed) - set(derived))]
    problems += [
        f"job {name}: {committed[name]} != {derived[name]}"
        for name in sorted(set(derived) & set(committed))
        if committed[name] != derived[name]
    ]
    return problems


def test_prometheus_targets_are_the_registry_derivation() -> None:
    config = _registry()
    problems = _mismatch(config, _file_jobs())
    # 빨가면 아래 YAML이 registry가 기대하는 scrape_configs다 — `config/prometheus/prometheus.yml`의 파생 구간을 이것으로 바꾼다.
    expected = yaml.safe_dump(prometheus_targets.derived_scrape_configs(config), allow_unicode=True, sort_keys=False)
    assert problems == [], "\n".join([*problems, "expected registry-derived scrape_configs:", expected])


def test_the_derivation_sees_remote_and_local_hosts() -> None:
    # 하한은 본 것에 건다 — 파생이 비거나 한 호스트만 보면 위 대조가 항진이 된다.
    config = _registry()
    labels = {
        static["labels"]["host"]
        for job in prometheus_targets.derived_scrape_configs(config)
        for static in job["static_configs"]
    }
    assert labels == set(config["hosts"])


def test_no_job_in_the_file_is_hand_written() -> None:
    """NH-6: 파일의 job 집합 == 파생 job 집합(Manager backend job 포함). 손으로 쓴 job이 하나라도 있으면 빨갛다."""

    derived = {job["job_name"] for job in prometheus_targets.derived_scrape_configs(_registry())}
    assert prometheus_targets.MANAGER_JOB in derived
    assert set(_file_jobs()) == derived


def test_the_manager_job_has_one_static_config_per_declared_host() -> None:
    """Manager는 선언된 모든 호스트에서 돈다(ADR-58 결정 2) — 호스트마다 R1 주소·`host` label 하나."""

    config = _registry()
    home = prometheus_targets.prometheus_host(config)
    port = prometheus_targets.manager_backend_port()
    job = _file_jobs()[prometheus_targets.MANAGER_JOB]
    expected = [
        {"targets": [f"{'127.0.0.1' if host == home else spec['addr']}:{port}"], "labels": {"host": host}}
        for host, spec in config["hosts"].items()
    ]
    assert job["static_configs"] == expected
    assert len(expected) >= 2  # 하한은 본 것에: 원격 호스트가 하나도 없으면 R1의 addr 갈래를 안 본 것이다
    # odroid를 빼면(= 파일에서 그 static_config를 지우면) 빨갛다.
    shrunk = copy.deepcopy(_file_jobs())
    shrunk[prometheus_targets.MANAGER_JOB]["static_configs"] = expected[:1]
    assert _mismatch(config, shrunk)


def test_the_manager_port_comes_from_the_backend_unit(tmp_path: Path) -> None:
    unit = _ROOT / prometheus_targets.MANAGER_BACKEND_UNIT
    text = unit.read_text(encoding="utf-8")
    port = prometheus_targets.manager_backend_port()
    assert f"--port {port}" in text
    # unit의 포트를 바꾸면 파생이 따라 바뀌고, 파일을 그대로 두면 빨갛다.
    moved = tmp_path / prometheus_targets.MANAGER_BACKEND_UNIT
    moved.parent.mkdir(parents=True)
    moved.write_text(text.replace(f"--port {port}", f"--port {port + 1}"), encoding="utf-8")
    other = prometheus_targets.manager_backend_port(str(tmp_path))
    assert other == port + 1
    config = _registry()
    derived = prometheus_targets.derived_scrape_configs(config, manager_port=other)
    committed = _file_jobs()[prometheus_targets.MANAGER_JOB]
    assert next(job for job in derived if job["job_name"] == prometheus_targets.MANAGER_JOB) != committed
    # --port가 없거나 둘이면 파생을 거부한다(조용히 기본값을 고르지 않는다).
    moved.write_text(text.replace(f" --port {port}", ""), encoding="utf-8")
    with pytest.raises(registry.TargetsConfigError, match="--port"):
        prometheus_targets.manager_backend_port(str(tmp_path))


@pytest.mark.parametrize(
    "exec_start",
    [
        # 한 줄에 두 번 — 줄당 마지막 하나만 세면 조용히 뒤엣것을 고른다.
        "ExecStart=/usr/bin/python -m uvicorn app --port 12901 --port 12902",
        # 두 줄(override 꼴) — 어느 쪽이 도는지 파일만으로 정할 수 없다.
        "ExecStart=/usr/bin/python -m uvicorn app --port 12901\nExecStart=/usr/bin/python -m uvicorn app --port=12902",
    ],
)
def test_a_unit_with_two_ports_is_rejected(tmp_path: Path, exec_start: str) -> None:
    unit = tmp_path / prometheus_targets.MANAGER_BACKEND_UNIT
    unit.parent.mkdir(parents=True)
    unit.write_text(f"[Service]\n{exec_start}\n", encoding="utf-8")
    with pytest.raises(registry.TargetsConfigError, match="exactly one ExecStart --port"):
        prometheus_targets.manager_backend_port(str(tmp_path))


def test_every_hard_coded_backend_port_is_the_unit_port() -> None:
    """설치기의 health 확인(`curl …/health`·`ss sport`)과 dev 진입점(`main.py`)의 포트 == backend unit의 `--port`.

    둘은 셸·`__main__`이라 unit을 읽지 않는다. unit 포트를 바꾸고 이들을 두면 설치기는 새 release를 영영 healthy로
    보지 못해 롤백한다 — 그래서 여기서 같은 값인지 본다.
    """

    port = prometheus_targets.manager_backend_port()
    installer = (_ROOT / "scripts/install-ktdm-trusted-release").read_text(encoding="utf-8")
    seen = re.findall(r"127\.0\.0\.1:(\d+)/health", installer) + re.findall(r"sport = :(\d+)", installer)
    assert len(seen) >= 2, seen  # 하한은 본 것에 — 패턴이 빗나가면 아래 비교가 항진이 된다
    assert set(seen) == {str(port)}, seen
    main = (_ROOT / "backend/src/kor_travel_docker_manager/main.py").read_text(encoding="utf-8")
    dev = re.findall(r"uvicorn\.run\([^)]*\bport=(\d+)", main)
    assert dev == [str(port)], dev


def test_the_manager_metrics_path_is_a_backend_route() -> None:
    from kor_travel_docker_manager.main import app

    assert prometheus_targets.MANAGER_METRICS_PATH in {getattr(route, "path", None) for route in app.routes}


def test_moving_a_target_is_red_until_the_file_follows() -> None:
    config = _registry()
    moved = next(
        name for name, spec in config["targets"].items()
        if spec.get("host") not in (None, "all", registry.default_host(config)) and not spec.get("external_project")
        and any(config["containers"][c].get("metrics_path") for c in spec.get("containers") or [])
    )
    config["targets"][moved]["host"] = registry.default_host(config)  # default(= Prometheus 호스트)로 되돌린다
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
    # Manager job도 호스트마다 하나라 함께 늘어난다(NH-6) — mars가 Manager job에 없으면 빨갛다.
    assert changed == everywhere | {prometheus_targets.MANAGER_JOB} and everywhere
    for name in everywhere | {prometheus_targets.MANAGER_JOB}:
        assert {"targets": ["192.0.2.7:" + after[name]["static_configs"][0]["targets"][0].rsplit(":", 1)[1]],
                "labels": {"host": "mars"}} in after[name]["static_configs"]
    assert _mismatch(grown, _file_jobs())


def test_a_changed_port_or_path_is_red() -> None:
    jobs = _file_jobs()
    name = next(iter(jobs))
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


# --- T-MGR-04r: metrics가 connection과 다른 포트에서 나오는 컨테이너(`metrics_port`) --------------------------------


def test_a_metrics_port_overrides_the_connection_port() -> None:
    config = _registry()
    with_port = [name for name, spec in config["containers"].items() if spec.get("metrics_port")]
    assert with_port  # 전제: weather code-server(gRPC connection, prometheus_client 서버는 다른 포트)
    jobs = {job["job_name"]: job for job in prometheus_targets.derived_scrape_configs(config)}
    for name in with_port:
        port = config["containers"][name]["metrics_port"]
        assert all(target.endswith(f":{port}") for static in jobs[name]["static_configs"] for target in static["targets"])
    # 포트를 바꾸고 파일을 따라 고치지 않으면 빨강.
    config["containers"][with_port[0]]["metrics_port"] += 1
    assert _mismatch(config, _file_jobs())


@pytest.mark.parametrize("port", [0, 70000, "12203", True])
def test_registry_rejects_an_invalid_metrics_port(port: Any) -> None:
    config = _registry()
    name = next(name for name, spec in config["containers"].items() if spec.get("metrics_port"))
    config["containers"][name]["metrics_port"] = port
    with pytest.raises(registry.TargetsConfigError, match="metrics_port"):
        registry._validate_targets_config(config, label="test")


def test_a_metrics_port_needs_a_metrics_path() -> None:
    config = _registry()
    name = next(name for name, spec in config["containers"].items() if spec.get("metrics_port"))
    del config["containers"][name]["metrics_path"]
    with pytest.raises(registry.TargetsConfigError, match="metrics_port"):
        registry._validate_targets_config(config, label="test")
