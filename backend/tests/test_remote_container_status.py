"""원격 HTTP 관측은 로컬 Docker 상태나 변경 권한이 아니다."""

from types import SimpleNamespace

import pytest
from docker.errors import NotFound

from kor_travel_docker_manager.services import registry


@pytest.fixture
def remote_registry(monkeypatch, tmp_path):
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / "config/docker-targets.yml").read_text())
    monkeypatch.setattr(registry, "load_targets_config", lambda: config)
    monkeypatch.setenv(registry.PROJECT_ROOT_ENV, str(tmp_path))
    monkeypatch.setenv("KTDM_HOST", registry.default_host(config))
    return config


@pytest.mark.parametrize("docker_available", [True, False])
def test_missing_remote_container_is_observed_not_reported_as_local_absent(remote_registry, monkeypatch, docker_available):
    from kor_travel_docker_manager.services import docker_service as module

    service = module.DockerService.__new__(module.DockerService)
    def missing(_name):
        raise NotFound("missing")
    def unavailable():
        raise RuntimeError("offline")
    monkeypatch.setattr(service, "_get_client", (lambda: SimpleNamespace(containers=SimpleNamespace(get=missing, list=lambda **kwargs: []))) if docker_available else unavailable)
    monkeypatch.setattr(module, "get_compose_config", lambda: {})
    observed = []
    def probe(connections):
        observed.extend(connections)
        return {name: "up" for name in connections}
    monkeypatch.setattr(module, "probe_remote_connections", probe, raising=False)
    entries = {entry["id"]: entry for entry in service.get_containers_status()}
    remote = entries["kor-travel-geo-api"]
    assert remote["status"] == "up"
    assert remote["remote"] is True
    assert "metrics" not in remote and "config" not in remote
    assert "kor-travel-geo-api" in observed
    assert entries["kor-travel-shared-postgresql"]["status"] == ("not_created" if docker_available else "offline")


def test_existing_local_orphan_keeps_docker_status_and_stop_path(remote_registry, monkeypatch):
    from kor_travel_docker_manager.services import docker_service as module

    service = module.DockerService.__new__(module.DockerService)
    local = SimpleNamespace(status="running", attrs={"Id": "owned-test", "State": {"Status": "running"}},
                            image=SimpleNamespace(tags=["test-image"], short_id="test-image"))
    def get(name):
        if name == remote_registry["containers"]["kor-travel-geo-api"]["name"]:
            return local
        raise NotFound("missing")
    monkeypatch.setattr(service, "_get_client", lambda: SimpleNamespace(containers=SimpleNamespace(get=get, list=lambda **kwargs: [])))
    monkeypatch.setattr(module, "get_compose_config", lambda: {})
    monkeypatch.setattr(module.metrics_collector, "get_latest_metric", lambda *_a, **_k: {})
    observed = []
    monkeypatch.setattr(module, "probe_remote_connections", lambda values: observed.extend(values) or {name: "remote" for name in values}, raising=False)
    entries = {entry["id"]: entry for entry in service.get_containers_status()}
    assert entries["kor-travel-geo-api"]["status"] == "running"
    assert not entries["kor-travel-geo-api"].get("remote")
    assert "kor-travel-geo-api" not in observed


def test_remote_http_observation_reads_no_body_redirect_auth_or_environment_proxy():
    import asyncio

    import httpx

    from kor_travel_docker_manager.services.remote_status import _probe_connections

    requests = []
    closed = []
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("observation consumed response body")
            yield b"unreachable"
        async def aclose(self):
            closed.append(1)
    async def handle(request):
        requests.append(request)
        code = {"/ok": 200, "/redirect": 302, "/unauthorized": 401}[request.url.path]
        return httpx.Response(code, headers={"location": "https://example.invalid/forbidden"}, stream=Body())
    result = asyncio.run(_probe_connections({
        "ok": "https://example.invalid/ok", "redirect": "https://example.invalid/redirect",
        "bad": "https://example.invalid/unauthorized", "grpc": "gRPC service",
    }, transport=httpx.MockTransport(handle)))
    assert result == {"ok": "up", "redirect": "up", "bad": "down", "grpc": "remote"}
    assert len(requests) == len(closed) == 3
    assert all(request.method == "GET" and "authorization" not in request.headers for request in requests)


def test_remote_probe_checks_all_connections_and_bounds_concurrency_and_total_time():
    import asyncio

    import httpx

    from kor_travel_docker_manager.services.remote_status import _probe_connections

    active = maximum = completed = 0
    async def handle(request):
        nonlocal active, maximum, completed
        active += 1
        maximum = max(maximum, active)
        try:
            if request.url.path == "/slow":
                await asyncio.sleep(10)
            elif request.url.path == "/refused":
                raise httpx.ConnectError("private raw cause", request=request)
            else:
                await asyncio.sleep(0.005)
            return httpx.Response(200)
        finally:
            active -= 1
            completed += 1
    result = asyncio.run(_probe_connections({
        "all": "http://example.invalid/slow http://example.invalid/refused http://example.invalid/fast",
        **{str(index): f"http://example.invalid/{index}" for index in range(8)},
        "credential": "https://user:never-send@example.invalid/auth",
    }, timeout_seconds=0.05, transport=httpx.MockTransport(handle)))
    assert result["all"] == "unreachable" and result["credential"] == "unreachable"
    assert all(result[str(index)] == "up" for index in range(8))
    assert completed == 11 and active == 0 and maximum <= 4


def test_remote_status_uses_one_registry_snapshot(remote_registry, monkeypatch):
    from kor_travel_docker_manager.services import docker_service as module

    original = registry.current_host
    calls = []
    monkeypatch.setattr(registry, "current_host", lambda config: calls.append(1) or original(config))
    monkeypatch.setattr(module, "get_compose_config", lambda: {})
    service = module.DockerService.__new__(module.DockerService)
    def unavailable():
        raise RuntimeError("offline")
    monkeypatch.setattr(service, "_get_client", unavailable)
    monkeypatch.setattr(module, "probe_remote_connections", lambda values: {name: "remote" for name in values})
    assert service.get_containers_status()
    assert len(calls) == 1
