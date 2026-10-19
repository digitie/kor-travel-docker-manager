"""REST와 WebSocket의 중복 상태 조회를 합치고 변경 작업 뒤 캐시를 버린다."""

from collections.abc import Callable
from concurrent.futures import Future
from copy import deepcopy
from functools import wraps
from threading import Lock
from time import monotonic
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")
Snapshot = list[dict[str, Any]]

_epoch_lock = Lock()
_epoch = 0
_mutations = 0


def _version() -> tuple[int, int]:
    with _epoch_lock:
        return _epoch, _mutations


def invalidates_status(method: Callable[P, R]) -> Callable[P, R]:
    """실패/부분 변경도 포함하여 작업 전후의 관측 캐시를 무효화한다."""
    @wraps(method)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        global _epoch, _mutations
        with _epoch_lock:
            _epoch += 1
            _mutations += 1
        try:
            return method(*args, **kwargs)
        finally:
            with _epoch_lock:
                _epoch += 1
                _mutations -= 1
    return wrapped


class StatusSnapshot:
    """동기 스레드 공유. asyncio 대기자의 취소는 조회 Future에 전달되지 않는다."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._flight: Future[tuple[int, Snapshot]] | None = None
        self._cached: Snapshot | None = None
        self._expires = 0.0
        self._epoch = -1

    def get(self, collect: Callable[[], Snapshot]) -> Snapshot:
        while True:
            with self._lock:
                epoch, active = _version()
                if (not active and self._epoch == epoch and self._cached is not None
                        and monotonic() < self._expires):
                    return deepcopy(self._cached)
                leader = self._flight is None
                if leader:
                    self._flight = Future()
                flight = self._flight
            assert flight is not None
            if leader:
                try:
                    result = collect()
                    with self._lock:
                        current, active = _version()
                        # offline/error도 응답은 유지하되 다음 호출에서 다시 확인한다.
                        if current == epoch and not active and all(
                            row.get("status") not in {"error", "offline", "unreachable", "down"} for row in result
                        ):
                            self._cached = deepcopy(result)
                            self._epoch = epoch
                            self._expires = monotonic() + 2.0
                        flight.set_result((epoch, result))
                        self._flight = None
                except BaseException as exc:
                    with self._lock:
                        flight.set_exception(exc)
                        self._flight = None
            observed_epoch, result = flight.result()
            if observed_epoch == _version()[0]:
                return deepcopy(result)
            # 변경 전에 시작한 조회는 변경 후 요청의 정본으로 반환하지 않는다.
