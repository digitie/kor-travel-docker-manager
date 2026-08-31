import asyncio

from fastapi.testclient import TestClient
from kor_travel_docker_manager.main import app
from kor_travel_docker_manager.services.docker_service import MANAGED_CONTAINERS, docker_service
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

    monkeypatch.setattr(docker_service, "_get_client", lambda: fake_client)
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
