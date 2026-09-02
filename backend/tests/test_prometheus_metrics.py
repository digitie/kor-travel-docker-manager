import asyncio

import pytest
from fastapi.testclient import TestClient

from kor_travel_docker_manager.main import app
from kor_travel_docker_manager.services.docker_service import MANAGED_CONTAINERS
from kor_travel_docker_manager.services.metrics_collector import MetricsCollector
from kor_travel_docker_manager.services.metrics_service import metrics_service


def test_prometheus_endpoint_exposes_managed_container_series_without_authentication():
    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert response.headers["cache-control"] == "no-store"
    assert "# HELP ktdm_container_state" in response.text
    assert "# TYPE ktdm_container_state gauge" in response.text
    assert "# HELP ktdm_container_memory_usage_bytes" in response.text
    assert "# HELP ktdm_container_network_interface_receive_bytes" in response.text
    for container_id in MANAGED_CONTAINERS:
        assert f'container_id="{container_id}"' in response.text

    # Prometheus 라벨에는 환경변수·비밀번호 관련 필드를 만들지 않는다.
    assert 'password=' not in response.text.lower()
    assert 'secret=' not in response.text.lower()
    assert 'token=' not in response.text.lower()


class _FakeContainer:
    status = "running"
    image = type("FakeImage", (), {"tags": ["example/service:latest"], "short_id": "sha256:fake"})()
    attrs = {
        "Image": "sha256:fake-image",
        "Id": "sha256:fake-container",
        "Created": "2026-09-01T00:00:00.000000000Z",
        "RestartCount": 2,
        "Config": {"Image": "example/service:latest"},
        "State": {
            "Status": "running",
            "Running": True,
            "Paused": False,
            "Restarting": False,
            "OOMKilled": False,
            "Dead": False,
            "ExitCode": 0,
            "StartedAt": "2026-09-01T00:01:00.000000000Z",
            "FinishedAt": "0001-01-01T00:00:00Z",
            "Health": {"Status": "healthy"},
        },
        "HostConfig": {"PortBindings": {"8080/tcp": [{"HostPort": "18080"}]}},
        "NetworkSettings": {"Networks": {"bridge": {}}},
        "Mounts": [{"Source": "/var/lib/example"}],
    }

    def __init__(self):
        self.round = 0

    def stats(self, stream=False):
        assert stream is False
        if self.round == 0:
            self.round = 1
            return _stats(100, 0, 1_000, 0, 10, 20, 100, 200)
        return _stats(120, 100, 1_100, 1_000, 110, 220, 300, 500)


def _stats(
    cpu_total: int,
    previous_cpu_total: int,
    system_total: int,
    previous_system_total: int,
    read_bytes: int,
    write_bytes: int,
    receive_bytes: int,
    transmit_bytes: int,
):
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": cpu_total, "percpu_usage": [cpu_total, 0]},
            "system_cpu_usage": system_total,
            "online_cpus": 2,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": previous_cpu_total},
            "system_cpu_usage": previous_system_total,
        },
        "memory_stats": {"usage": 512, "limit": 1_024},
        "blkio_stats": {
            "io_service_bytes_recursive": [
                {"op": "Read", "value": read_bytes},
                {"op": "Write", "value": write_bytes},
            ]
        },
        "networks": {
            "eth0": {
                "rx_bytes": receive_bytes,
                "tx_bytes": transmit_bytes,
                "rx_packets": 30,
                "tx_packets": 40,
                "rx_errors": 1,
                "tx_errors": 2,
            }
        },
        "pids_stats": {"current": 7, "limit": 100},
    }


class _FakeContainers:
    def __init__(self, container):
        self.container = container

    def get(self, name):
        assert name
        return self.container


class _FakeDockerClient:
    def __init__(self, container):
        self.containers = _FakeContainers(container)


def test_collector_keeps_detailed_resource_observation_and_prometheus_series(monkeypatch):
    fake_container = _FakeContainer()
    fake_client = _FakeDockerClient(fake_container)
    collector = MetricsCollector()
    container_id = next(iter(MANAGED_CONTAINERS))

    collector.set_docker_client_provider(lambda: fake_client)
    monkeypatch.setattr(metrics_service, "save_metric", lambda **kwargs: None)

    asyncio.run(collector.collect_metrics())
    first = collector.get_latest_metric(container_id)
    assert first["io_read_total"] == 10
    assert first["io_write_total"] == 20
    assert first["network_rx_bytes"] == 100
    assert first["network_tx_bytes"] == 200
    assert first["pids_current"] == 7
    assert first["stats_available"] is True
    assert collector.get_latest_metric(container_id, docker_id="sha256:new-container")["stats_available"] is False

    asyncio.run(collector.collect_metrics())
    second = collector.get_latest_metric(container_id)
    assert second["io_read"] == 100
    assert second["io_write"] == 200
    assert second["io_read_total"] == 110
    assert second["io_write_total"] == 220

    observation = collector.get_container_observation(container_id)
    assert observation is not None
    assert observation["state"] == "running"
    assert observation["health"] == "healthy"
    assert observation["restart_count"] == 2
    assert observation["network_interfaces"]["eth0"]["rx_bytes"] == 300

    rendered = collector.render_prometheus_metrics()
    assert f'container_id="{container_id}"' in rendered
    assert 'state="running"' in rendered
    assert 'status="healthy"' in rendered
    assert "ktdm_container_pids_current" in rendered
    assert "ktdm_container_exit_code_available" in rendered
    assert "ktdm_container_pids_available" in rendered
    assert 'interface="eth0"' in rendered


def test_collect_metrics_persists_save_metric_off_the_event_loop(monkeypatch):
    """GM-14: collect_metrics()의 `metrics_service.save_metric(...)` 호출도
    `asyncio.to_thread`로 감싸져 있다(_collect_loop의 cleanup_old_metrics와
    같은 이유 — 동기 DB 쓰기가 event loop를 막지 않게 하기 위함) — 하지만
    test_metrics.py의 대응 테스트는 일부러 `collector._running = False`로
    _collect_loop 본문(=collect_metrics, Docker client mocking 필요)을
    건드리지 않는다. 여기서는 이 파일에 이미 있는 `_FakeDockerClient`로
    collect_metrics()를 실제로 실행시키면서, save_metric을 단순 no-op으로
    몽키패치하는 대신 event loop 위에서 실행되지 않았음을 증명하는
    capture_loop_state 기법으로 대체해 end-to-end로 검증한다."""

    ran_without_a_running_loop = None

    def capture_loop_state(*args, **kwargs):
        nonlocal ran_without_a_running_loop
        try:
            asyncio.get_running_loop()
            ran_without_a_running_loop = False
        except RuntimeError:
            ran_without_a_running_loop = True

    fake_container = _FakeContainer()
    fake_client = _FakeDockerClient(fake_container)
    collector = MetricsCollector()

    collector.set_docker_client_provider(lambda: fake_client)
    monkeypatch.setattr(metrics_service, "save_metric", capture_loop_state)

    asyncio.run(collector.collect_metrics())

    assert ran_without_a_running_loop is True


def test_prometheus_distinguishes_unavailable_exit_code_and_pid_values():
    collector = MetricsCollector()
    container_id = next(iter(MANAGED_CONTAINERS))
    rendered = collector.render_prometheus_metrics()

    assert f'container_id="{container_id}"' in rendered
    assert "ktdm_container_exit_code_available" in rendered
    assert "ktdm_container_pids_available" in rendered
    assert not any(
        line.startswith("ktdm_container_exit_code{") and f'container_id="{container_id}"' in line
        for line in rendered.splitlines()
    )
    assert not any(
        line.startswith("ktdm_container_pids_current{") and f'container_id="{container_id}"' in line
        for line in rendered.splitlines()
    )


def test_metrics_collector_construction_survives_broken_targets_config(tmp_path, monkeypatch) -> None:
    """GM-followups(docker-targets.yml 스키마 검증 잔여): `metrics_collector.py`
    맨 끝의 `metrics_collector = MetricsCollector()`는 이 모듈이 import되는 순간
    바로 실행되는 모듈 레벨 싱글턴이다. registry.py의 `MANAGED_CONTAINERS`를
    지연 계산(`_LazyMapping`)으로 바꿔도, `__init__`이 그 최초 실제 순회를
    수행하는 자리라면 여전히 import 시점에 config 검증이 실행돼 `ktdctl`처럼
    이 모듈을 그저 import만 하는 프로세스까지 raw traceback으로 죽는다 — 그래서
    `__init__` 자신이 `MANAGED_CONTAINERS` 접근 실패를 fail-open(빈 dict로
    시작)으로 흡수해야 한다(`DockerService.__init__`의 `_backup_default_config`
    try/except와 같은 이유)."""

    from kor_travel_docker_manager.services import registry as registry_module

    config_path = tmp_path / "docker-targets.yml"
    config_path.write_text(
        """
containers:
  geo_db:
    compose_service: geo-db
    name: geo_db
    display_name: Geo DB
    role: database
    connection: {}
    expected_ports: []
targets:
  geo:
    containers: [geo_db]
    services: [geo-db]
    depends_on: [does_not_exist]
dependency_order: [geo]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", str(config_path))
    registry_module.load_targets_config.cache_clear()
    try:
        collector = MetricsCollector()  # 여기서 raise하면 이 테스트 자체가 에러로 죽는다.
        assert collector._container_observations == {}

        # 대조: `render_prometheus_metrics()`는 생성자와 달리 실제 호출 시점의
        # 메서드라서 그 안의 MANAGED_CONTAINERS 순회가 같은 ValueError를 그대로
        # 다시 내는 것이 맞다 — 여기서 흡수해 조용히 빈 결과를 주면 실제 config
        # 문제를 API 호출자에게 숨기게 된다. import 시점 생성자만 fail-open이어야
        # 한다는 것의 반증으로 이 대조를 남겨둔다.
        with pytest.raises(ValueError, match="depends_on: unknown target 'does_not_exist'"):
            collector.render_prometheus_metrics()
    finally:
        monkeypatch.delenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", raising=False)
        registry_module.load_targets_config.cache_clear()
