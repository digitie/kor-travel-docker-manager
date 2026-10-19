import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from kor_travel_docker_manager.services import status_snapshot as module
from kor_travel_docker_manager.services.status_snapshot import StatusSnapshot, invalidates_status


def test_cache_is_detached_and_expires_from_completion(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    cache = StatusSnapshot()
    calls = []
    def collect():
        calls.append(1)
        clock[0] += 10
        return [{"status": "running", "nested": []}]
    value = cache.get(collect)
    value[0]["nested"].append("caller")
    clock[0] = 11.9
    assert cache.get(collect)[0]["nested"] == []
    assert len(calls) == 1
    clock[0] = 12
    cache.get(collect)
    assert len(calls) == 2


@pytest.mark.parametrize("status", ["error", "offline", "unreachable", "down"])
def test_error_rows_are_not_cached(status):
    cache = StatusSnapshot()
    calls = []
    def collect():
        calls.append(1)
        return [{"status": status}]
    cache.get(collect)
    cache.get(collect)
    assert len(calls) == 2


def test_shared_exception_then_new_attempt(monkeypatch):
    cache = StatusSnapshot()
    entered, release, joined = Event(), Event(), Event()
    original = module.Future.result
    def observed(future, *args, **kwargs):
        joined.set()
        return original(future, *args, **kwargs)
    monkeypatch.setattr(module.Future, "result", observed)
    calls = []
    def collect():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        raise ValueError("synthetic")
    with ThreadPoolExecutor(2) as pool:
        one = pool.submit(cache.get, collect)
        assert entered.wait(3)
        two = pool.submit(cache.get, collect)
        assert joined.wait(3)
        release.set()
        for result in [one, two]:
            with pytest.raises(ValueError, match="synthetic"):
                result.result()
    assert len(calls) == 1
    assert cache.get(lambda: [{"status": "running"}])[0]["status"] == "running"


def test_write_epoch_rejects_inflight_stale_snapshot():
    cache = StatusSnapshot()
    entered, release = Event(), Event()
    calls = []
    def collect():
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
            return [{"status": "running", "version": "old"}]
        return [{"status": "running", "version": "new"}]
    @invalidates_status
    def mutate():
        pass
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(cache.get, collect)
        assert entered.wait(3)
        mutate()
        release.set()
        assert result.result()[0]["version"] == "new"
    assert len(calls) == 2


def test_failed_write_invalidates_and_active_write_does_not_cache():
    cache = StatusSnapshot()
    calls = []
    def collect():
        calls.append(1)
        return [{"status": "running"}]
    cache.get(collect)
    @invalidates_status
    def mutate():
        cache.get(collect)
        cache.get(collect)
        raise ValueError("partial write")
    with pytest.raises(ValueError):
        mutate()
    cache.get(collect)
    assert len(calls) == 4


def test_cancelled_waiter_does_not_cancel_leader():
    asyncio.run(_test_cancelled_waiter_does_not_cancel_leader())


async def _test_cancelled_waiter_does_not_cancel_leader():
    cache = StatusSnapshot()
    entered, release = Event(), Event()
    calls = []
    def collect():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return [{"status": "running"}]
    leader = asyncio.create_task(asyncio.to_thread(cache.get, collect))
    assert await asyncio.to_thread(entered.wait, 3)
    waiter = asyncio.create_task(asyncio.to_thread(cache.get, collect))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    assert (await leader)[0]["status"] == "running"
    assert len(calls) == 1


def test_real_rest_and_websocket_broadcast_share_snapshot(monkeypatch):
    asyncio.run(_test_real_rest_and_websocket_broadcast_share_snapshot(monkeypatch))


async def _test_real_rest_and_websocket_broadcast_share_snapshot(monkeypatch):
    from kor_travel_docker_manager.api import routes, websocket
    from kor_travel_docker_manager.services.docker_service import DockerService
    monkeypatch.setattr(DockerService, "_backup_default_config", lambda self: None)
    service = DockerService()
    entered, release = Event(), Event()
    calls = []
    def collect():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return [{"status": "running"}]
    monkeypatch.setattr(service, "_collect_containers_status", collect)
    monkeypatch.setattr(routes, "docker_service", service)
    monkeypatch.setattr(websocket, "docker_service", service)
    monkeypatch.setattr(websocket.status_manager, "active_connections", [object()])
    sent = asyncio.Event()
    async def broadcast(message):
        assert message["containers"] == [{"status": "running"}]
        sent.set()
    monkeypatch.setattr(websocket.status_manager, "broadcast", broadcast)
    rest = asyncio.create_task(asyncio.to_thread(routes.list_containers))
    assert await asyncio.to_thread(entered.wait, 3)
    ws = asyncio.create_task(websocket.status_broadcast_loop())
    await asyncio.sleep(0)
    release.set()
    assert await rest == [{"status": "running"}]
    await asyncio.wait_for(sent.wait(), 3)
    ws.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ws
    assert len(calls) == 1
