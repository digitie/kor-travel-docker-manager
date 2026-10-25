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
    assert raised.value.detail == "Container status is temporarily unavailable; retry the request"
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


@pytest.mark.parametrize("row_fields", [
    {"status": "running"},
    {"status": "error"},
    {"status": "running", "inventory_complete": False},
])
def test_same_flight_waiter_cannot_observe_later_collector_mutation(monkeypatch, row_fields):
    from threading import current_thread

    cache = StatusSnapshot()
    entered, collected, joined, follower_release = Event(), Event(), Event(), Event()
    owned = [{**row_fields, "nested": [1]}]
    calls = []
    original_future = module.Future

    class HeldFollowerFuture(original_future):
        def result(self, *args, **kwargs):
            if current_thread().name.startswith("snapshot-follower"):
                joined.set()
                assert follower_release.wait(3)
            return super().result(*args, **kwargs)

    def collect():
        calls.append(1)
        entered.set()
        assert collected.wait(3)
        return owned

    monkeypatch.setattr(module, "Future", HeldFollowerFuture)
    with ThreadPoolExecutor(1, thread_name_prefix="snapshot-leader") as leader_pool:
        with ThreadPoolExecutor(1, thread_name_prefix="snapshot-follower") as follower_pool:
            try:
                leader = leader_pool.submit(cache.get, collect)
                assert entered.wait(3)
                follower = follower_pool.submit(cache.get, collect)
                assert joined.wait(3)
                collected.set()
                leader_value = leader.result(timeout=3)
                owned[0]["nested"].append(2)
                follower_release.set()
                follower_value = follower.result(timeout=3)
            finally:
                collected.set()
                follower_release.set()
    assert len(calls) == 1
    assert leader_value == follower_value == [{**row_fields, "nested": [1]}]


def test_successful_completion_keeps_one_private_copy_and_one_return_copy(monkeypatch):
    cache = StatusSnapshot()
    copy_calls = []
    original_copy = module.deepcopy

    def copy(value):
        copy_calls.append(1)
        return original_copy(value)

    monkeypatch.setattr(module, "deepcopy", copy)
    cache.get(lambda: [{"status": "running"}])
    assert len(copy_calls) == 2
    cache.get(lambda: pytest.fail("normal snapshot was not reused"))
    assert len(copy_calls) == 3


def test_pending_followers_have_one_fixed_join_budget_without_starting_another_collect(monkeypatch):
    from itertools import count

    clock = count(0.0, 5.0)
    last = [0.0]

    def now():
        last[0] = next(clock)
        return last[0]

    monkeypatch.setattr(module, "monotonic", now)
    cache = StatusSnapshot()
    entered, release = Event(), Event()
    calls = []

    def collect():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return [{"status": "running"}]

    with ThreadPoolExecutor(1) as pool:
        leader = pool.submit(cache.get, collect)
        try:
            assert entered.wait(3)
            for _ in range(2):
                with pytest.raises(module.StatusSnapshotUnavailableError):
                    cache.get(collect)
                assert cache._flight is not None and not cache._flight.done()
                assert len(calls) == 1
        finally:
            release.set()
        assert leader.result(timeout=3) == [{"status": "running"}]
    monkeypatch.setattr(module, "monotonic", lambda: last[0])
    assert cache.get(lambda: pytest.fail("completed snapshot was not recovered")) == [{"status": "running"}]


def test_collector_timeout_error_is_preserved_as_the_original_exception():
    cache = StatusSnapshot()
    original = TimeoutError("synthetic collector timeout")

    def collect():
        raise original

    with pytest.raises(TimeoutError) as raised:
        cache.get(collect)
    assert raised.value is original
    assert cache._flight is None


@pytest.mark.parametrize("fail", [False, True])
def test_completed_future_timeout_and_completion_race_are_not_classified_as_pending(monkeypatch, fail):
    from concurrent.futures import Future

    cache = StatusSnapshot()
    original = TimeoutError("synthetic completed collector timeout")

    class CompletedDuringTimeout(Future):
        def result(self, timeout=None):
            if not self.done():
                if fail:
                    self.set_exception(original)
                else:
                    self.set_result((module._version()[0], [{"status": "running"}]))
                raise TimeoutError()
            return super().result(timeout=timeout)

    cache._flight = CompletedDuringTimeout()
    if fail:
        with pytest.raises(TimeoutError) as raised:
            cache.get(lambda: pytest.fail("follower started a collector"))
        assert raised.value is original
    else:
        assert cache.get(lambda: pytest.fail("follower started a collector")) == [{"status": "running"}]


def test_two_stale_join_attempts_share_one_entry_deadline(monkeypatch):
    from concurrent.futures import Future

    times = iter([100.0, 101.0, 104.0])
    monkeypatch.setattr(module, "monotonic", lambda: next(times))
    cache = StatusSnapshot()
    timeouts = []

    @invalidates_status
    def mutate():
        pass

    class StaleJoin(Future):
        def result(self, timeout=None):
            timeouts.append(timeout)
            epoch = module._version()[0]
            mutate()
            cache._flight = StaleJoin()
            return epoch, [{"status": "running"}]

    cache._flight = StaleJoin()
    with pytest.raises(module.StatusSnapshotUnavailableError, match="changed repeatedly"):
        cache.get(lambda: pytest.fail("follower started a collector"))
    assert timeouts == [4.0, 1.0]


def test_cancelled_async_follower_worker_is_released_while_collection_stays_pending(monkeypatch):
    asyncio.run(_cancel_pending_follower_worker(monkeypatch))


async def _cancel_pending_follower_worker(monkeypatch):
    from threading import local

    state = local()

    def clock():
        calls = getattr(state, "calls", 0)
        state.calls = calls + 1
        return 0.0 if calls == 0 else 4.95

    monkeypatch.setattr(module, "monotonic", clock)
    cache = StatusSnapshot()
    entered, release, joined, drained = Event(), Event(), Event(), Event()
    original_future = module.Future
    calls = []

    class JoinedFuture(original_future):
        def result(self, *args, **kwargs):
            if kwargs.get("timeout") is not None:
                joined.set()
            return super().result(*args, **kwargs)

    def collect():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return [{"status": "running"}]

    def wait_as_follower():
        try:
            with pytest.raises(module.StatusSnapshotUnavailableError):
                cache.get(collect)
        finally:
            drained.set()

    monkeypatch.setattr(module, "Future", JoinedFuture)
    with ThreadPoolExecutor(1) as pool:
        leader = pool.submit(cache.get, collect)
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            waiter = asyncio.create_task(asyncio.to_thread(wait_as_follower))
            assert await asyncio.to_thread(joined.wait, 3)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert await asyncio.to_thread(drained.wait, 1)
            assert not leader.done() and len(calls) == 1
        finally:
            release.set()
        assert leader.result(timeout=3) == [{"status": "running"}]


def test_actual_collector_timeout_is_shared_without_unavailable_translation(monkeypatch):
    cache = StatusSnapshot()
    entered, release, joined = Event(), Event(), Event()
    original = TimeoutError("synthetic owned collector timeout")
    original_future = module.Future

    class JoinedFuture(original_future):
        def result(self, *args, **kwargs):
            if kwargs.get("timeout") is not None:
                joined.set()
            return super().result(*args, **kwargs)

    def collect():
        entered.set()
        assert release.wait(3)
        raise original

    monkeypatch.setattr(module, "Future", JoinedFuture)
    with ThreadPoolExecutor(2) as pool:
        leader = pool.submit(cache.get, collect)
        try:
            assert entered.wait(3)
            follower = pool.submit(cache.get, collect)
            assert joined.wait(3)
        finally:
            release.set()
        for task in (leader, follower):
            with pytest.raises(TimeoutError) as raised:
                task.result(timeout=3)
            assert raised.value is original
    assert cache._flight is None
