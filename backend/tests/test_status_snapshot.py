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


def test_incomplete_inventory_is_retried_and_recovery_is_cached():
    cache = StatusSnapshot()
    calls = []

    def collect():
        calls.append(1)
        return [{"status": "running", "inventory_complete": len(calls) > 1}]

    assert cache.get(collect)[0]["inventory_complete"] is False
    assert cache.get(collect)[0]["inventory_complete"] is True
    assert cache.get(collect)[0]["inventory_complete"] is True
    assert len(calls) == 2


def test_repeated_mutation_stops_without_returning_stale_snapshot():
    cache = StatusSnapshot()
    calls = []

    @invalidates_status
    def mutate():
        pass

    def collect():
        calls.append(1)
        if len(calls) > 2:
            pytest.fail("status collection retried beyond its request budget")
        mutate()
        return [{"status": "running", "version": "stale"}]

    with pytest.raises(RuntimeError, match="changed repeatedly"):
        cache.get(collect)
    assert len(calls) == 2
    assert cache.get(lambda: [{"status": "running", "version": "fresh"}])[0]["version"] == "fresh"


def test_collector_owned_values_and_each_cached_return_are_detached():
    cache = StatusSnapshot()
    original = [{"status": "running", "nested": {"values": [1]}}]
    first = cache.get(lambda: original)
    original[0]["nested"]["values"].append(2)
    second = cache.get(lambda: pytest.fail("cached snapshot was not reused"))
    first[0]["nested"]["values"].append(3)
    assert second == [{"status": "running", "nested": {"values": [1]}}]


def test_cancelled_joined_waiter_keeps_shared_collection_alive(monkeypatch):
    asyncio.run(_cancel_joined_waiter(monkeypatch))


async def _cancel_joined_waiter(monkeypatch):
    cache = StatusSnapshot()
    entered, release, joined = Event(), Event(), Event()
    original = module.Future.result
    calls = []

    def observed(future, *args, **kwargs):
        joined.set()
        return original(future, *args, **kwargs)

    def collect():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return [{"status": "running"}]

    monkeypatch.setattr(module.Future, "result", observed)
    leader = asyncio.create_task(asyncio.to_thread(cache.get, collect))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        waiter = asyncio.create_task(asyncio.to_thread(cache.get, collect))
        try:
            assert await asyncio.to_thread(joined.wait, 3)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        finally:
            waiter.cancel()
    finally:
        release.set()
    assert await leader == [{"status": "running"}]
    assert len(calls) == 1


def test_rest_temporary_snapshot_failure_is_503_with_fixed_detail(monkeypatch):
    from fastapi import HTTPException

    from kor_travel_docker_manager.api import routes

    def unavailable():
        raise module.StatusSnapshotUnavailableError("synthetic internal sentinel")

    monkeypatch.setattr(routes.docker_service, "get_containers_status", unavailable)
    with pytest.raises(HTTPException) as raised:
        routes.list_containers()
    assert raised.value.status_code == 503
    assert raised.value.detail == "Container status is changing; retry the request"
    assert isinstance(raised.value.__cause__, module.StatusSnapshotUnavailableError)


def test_websocket_temporary_snapshot_failure_uses_retry_later(monkeypatch):
    asyncio.run(_websocket_temporary_snapshot_failure(monkeypatch))


async def _websocket_temporary_snapshot_failure(monkeypatch):
    from types import SimpleNamespace

    from starlette.websockets import WebSocket

    from kor_travel_docker_manager.api import websocket as ws

    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    async def authorize(websocket):
        return True, object()

    def unavailable():
        raise module.StatusSnapshotUnavailableError("synthetic internal sentinel")

    manager = ws.ConnectionManager()
    monkeypatch.setattr(ws, "_authorize_with_limit", authorize)
    monkeypatch.setattr(ws, "status_manager", manager)
    monkeypatch.setattr(ws, "docker_service", SimpleNamespace(get_containers_status=unavailable))
    websocket = WebSocket({"type": "websocket"}, receive=receive, send=send)
    await ws.ws_status(websocket)
    assert [message["type"] for message in sent] == ["websocket.accept", "websocket.close"]
    assert sent[-1]["code"] == ws.WS_CLOSE_TRY_AGAIN_LATER
    assert "synthetic internal sentinel" not in str(sent)
    assert manager.active_connections == []


def test_other_rest_collection_errors_keep_the_existing_500(monkeypatch):
    from fastapi import HTTPException

    from kor_travel_docker_manager.api import routes

    def fail():
        raise RuntimeError("synthetic existing failure")

    monkeypatch.setattr(routes.docker_service, "get_containers_status", fail)
    with pytest.raises(HTTPException) as raised:
        routes.list_containers()
    assert raised.value.status_code == 500
    assert raised.value.detail == "synthetic existing failure"
