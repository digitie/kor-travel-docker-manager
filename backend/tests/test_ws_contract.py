"""C7 WebSocket 종료 코드 계약 회귀 테스트.

핵심 문제: Starlette TestClient는 ASGI `websocket.close` 메시지를 그대로 되던지므로
pre-accept close와 accept-then-close를 모두 같은 `WebSocketDisconnect(4401)`로 보고한다.
그래서 TestClient만으로는 계약을 구분할 수 없다. 반면 uvicorn은 accept 이전 close를
HTTP 403 handshake 거절로 바꿔 보내고, 브라우저는 4401 대신 1006만 본다.

따라서 계약은 ASGI app을 직접 구동해 **app이 내보낸 메시지 시퀀스**로 고정한다.
uvicorn이 403을 보낼지 101을 보낼지는 이 시퀀스의 첫 메시지 type 하나로 결정된다.
"""

import asyncio
import datetime
import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import kor_travel_docker_manager.database
from kor_travel_docker_manager.services.auth_service import hash_password_for_env

FRONTEND_ORIGIN = "http://localhost:12905"
os.environ["KTDM_ADMIN_USERNAME"] = "admin"
# 이 모듈은 로그인을 하지 않는다. app 기동에 필요한 형식만 갖추면 되므로 운영 비밀번호와
# 같은 값을 쓰지 않는다(런북: 테스트 비번이 prod 관리자 비번과 같아 전파 금지).
os.environ["KTDM_ADMIN_PASSWORD_HASH"] = hash_password_for_env(
    "ws-contract-tests-never-log-in"
)
os.environ["KTDM_SESSION_SECRET"] = "test-session-secret-minimum-32-bytes-value"
os.environ["KTDM_FRONTEND_ORIGINS"] = FRONTEND_ORIGIN

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
kor_travel_docker_manager.database.engine = test_engine
kor_travel_docker_manager.database.SessionLocal = TestSessionLocal

from kor_travel_docker_manager._time import utcnow  # noqa: E402
from kor_travel_docker_manager.api import websocket as ws_mod  # noqa: E402
from kor_travel_docker_manager.main import app  # noqa: E402
from kor_travel_docker_manager.services.auth_service import AdminSessionContext  # noqa: E402
from kor_travel_docker_manager.services.docker_service import MANAGED_CONTAINERS  # noqa: E402


def _context(*, ttl_seconds: float = 3600.0) -> AdminSessionContext:
    """운영과 동일하게 naive UTC를 쓴다(_time.utcnow()와 DB 컬럼이 모두 naive)."""
    return AdminSessionContext(
        username="admin",
        session_id_hash="x" * 64,
        expires_at=utcnow() + datetime.timedelta(seconds=ttl_seconds),
    )

ORIGIN_HEADER = (b"origin", FRONTEND_ORIGIN.encode())


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    """시퀀스 테스트는 순서만 본다. settle 값 자체는 전용 resolver 테스트가 검증한다."""
    monkeypatch.setenv(ws_mod._ACCEPT_CLOSE_SETTLE_ENV, "0")


def drive_websocket(path: str, *, headers: list[tuple[bytes, bytes]]) -> list[dict]:
    """ASGI app을 직접 구동하고 app이 내보낸 메시지 시퀀스를 그대로 돌려준다."""
    sent: list[dict] = []

    async def main() -> list[dict]:
        incoming: asyncio.Queue = asyncio.Queue()
        await incoming.put({"type": "websocket.connect"})

        async def receive():
            return await incoming.get()

        async def send(message):
            sent.append(message)

        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
        }
        await app(scope, receive, send)
        return sent

    return asyncio.run(main())


def _assert_no_data_frames(sent: list[dict]) -> None:
    assert not any(m["type"].startswith("websocket.send") for m in sent), (
        f"거절 소켓에 data frame이 실렸다: {sent}"
    )


# --- accept-then-close 시퀀스 -------------------------------------------------------


def test_ws_status_auth_reject_accepts_then_closes():
    """미인증 거절은 accept(101) → data frame 0건 → close(4401) 이어야 한다.

    pre-accept close로 되돌아가면 sent == [close] 라 길이/첫 type에서 즉시 깨진다.
    """
    sent = drive_websocket("/api/v1/ws/status", headers=[ORIGIN_HEADER])

    assert [m["type"] for m in sent] == ["websocket.accept", "websocket.close"]
    assert sent[-1]["code"] == ws_mod.WS_CLOSE_AUTH_REQUIRED
    assert sent[-1]["reason"] == "AUTH_REQUIRED"
    _assert_no_data_frames(sent)


def test_ws_logs_unauthenticated_valid_container_is_4401():
    """존재하는 container라도 미인증이면 4401이다."""
    valid_id = next(iter(MANAGED_CONTAINERS))
    sent = drive_websocket(f"/api/v1/ws/logs/{valid_id}", headers=[ORIGIN_HEADER])

    assert [m["type"] for m in sent] == ["websocket.accept", "websocket.close"]
    assert sent[-1]["code"] == ws_mod.WS_CLOSE_AUTH_REQUIRED
    _assert_no_data_frames(sent)


def test_ws_logs_unauthenticated_unknown_container_is_also_4401():
    """미인증 peer에게는 container 존재 여부가 새면 안 된다.

    인증 검사가 MANAGED_CONTAINERS 조회보다 반드시 먼저 와야 한다. 순서가 뒤집히면
    미인증 peer가 4000/4401 차이로 18개 container id를 열거할 수 있다.
    """
    unknown_id = "no-such-container"
    assert unknown_id not in MANAGED_CONTAINERS

    sent = drive_websocket(f"/api/v1/ws/logs/{unknown_id}", headers=[ORIGIN_HEADER])

    assert sent[-1]["code"] == ws_mod.WS_CLOSE_AUTH_REQUIRED, (
        "미인증인데 4000이 나왔다 — container 존재 여부가 누출된다"
    )


def test_ws_logs_unknown_container_accepts_then_closes(monkeypatch):
    """인증은 통과하고 container_id만 모르는 경우 4000으로 구분되어야 한다."""
    monkeypatch.setattr(ws_mod, "_websocket_authorize", lambda ws: _context())

    sent = drive_websocket("/api/v1/ws/logs/no-such-container", headers=[ORIGIN_HEADER])

    assert [m["type"] for m in sent] == ["websocket.accept", "websocket.close"]
    assert sent[-1]["code"] == ws_mod.WS_CLOSE_INVALID_CONTAINER
    assert sent[-1]["reason"] == "INVALID_CONTAINER_ID"
    _assert_no_data_frames(sent)


def test_ws_status_rejects_foreign_origin(monkeypatch):
    """쿠키가 유효해도 허용되지 않은 Origin이면 거절한다(CSWSH 게이트)."""
    monkeypatch.setattr(
        ws_mod,
        "validate_session_cookie",
        lambda *args, **kwargs: pytest.fail("Origin 거절이 쿠키 검증보다 먼저여야 한다"),
    )

    sent = drive_websocket(
        "/api/v1/ws/status", headers=[(b"origin", b"https://evil.example")]
    )

    assert [m["type"] for m in sent] == ["websocket.accept", "websocket.close"]
    assert sent[-1]["code"] == ws_mod.WS_CLOSE_AUTH_REQUIRED


# --- accept 실패 / 취소 경계 --------------------------------------------------------


def test_accept_failure_does_not_send_close(monkeypatch):
    """accept가 실패하면 close를 보내지 않는다 — 보내면 pre-handshake 거절로 퇴화한다."""

    async def _fail_accept(_websocket):
        return False

    async def _forbidden_close(*args, **kwargs):
        raise AssertionError("accept 실패 뒤 close를 보내면 안 된다")

    monkeypatch.setattr(ws_mod, "_accept_best_effort", _fail_accept)
    monkeypatch.setattr(ws_mod, "_close_best_effort", _forbidden_close)

    asyncio.run(
        ws_mod._accept_and_close(object(), code=ws_mod.WS_CLOSE_AUTH_REQUIRED, reason="x")
    )


def test_reject_close_survives_outer_cancellation():
    """accept 직후 도착한 취소가 close를 지우면 브라우저는 다시 1006을 본다."""
    closes: list[int] = []

    class _FakeWebSocket:
        async def accept(self):
            await asyncio.sleep(0)

        async def close(self, code=1000, reason=""):
            closes.append(code)

    async def main():
        websocket = _FakeWebSocket()
        task = asyncio.create_task(
            ws_mod._accept_and_close(
                websocket, code=ws_mod.WS_CLOSE_AUTH_REQUIRED, reason="AUTH_REQUIRED"
            )
        )
        # accept가 시작되도록 한 번 양보한 뒤 바깥에서 취소한다.
        await asyncio.sleep(0)
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        return result[0]

    outcome = asyncio.run(main())

    assert closes == [ws_mod.WS_CLOSE_AUTH_REQUIRED], "취소가 close를 삼켰다"
    assert isinstance(outcome, asyncio.CancelledError), "취소는 다시 전파되어야 한다"


# --- settle window ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0.25),
        ("", 0.25),
        ("   ", 0.25),
        ("abc", 0.25),
        ("-1", 0.0),
        ("0", 0.0),
        ("0.01", 0.01),
        ("99", 5.0),
    ],
)
def test_accept_close_settle_seconds_resolver(monkeypatch, raw, expected):
    """비정상 env가 sleep을 무한정 늘리지 못하도록 [0, 5]로 clamp한다."""
    if raw is None:
        monkeypatch.delenv(ws_mod._ACCEPT_CLOSE_SETTLE_ENV, raising=False)
    else:
        monkeypatch.setenv(ws_mod._ACCEPT_CLOSE_SETTLE_ENV, raw)

    assert ws_mod._resolve_accept_close_settle_seconds() == expected


def test_reject_settles_between_accept_and_close(monkeypatch):
    """settle 대기는 accept 이후, close 이전에 정확히 한 번 일어난다."""
    monkeypatch.setenv(ws_mod._ACCEPT_CLOSE_SETTLE_ENV, "0.05")
    events: list[str] = []

    real_sleep = asyncio.sleep

    async def _recording_sleep(delay, *args, **kwargs):
        if delay == 0.05:
            events.append(f"sleep:{delay}")
        return await real_sleep(0, *args, **kwargs)

    class _FakeWebSocket:
        async def accept(self):
            events.append("accept")

        async def close(self, code=1000, reason=""):
            events.append("close")

    monkeypatch.setattr(ws_mod.asyncio, "sleep", _recording_sleep)

    asyncio.run(
        ws_mod._accept_and_close(
            _FakeWebSocket(), code=ws_mod.WS_CLOSE_AUTH_REQUIRED, reason="x"
        )
    )

    assert events == ["accept", "sleep:0.05", "close"]


# --- ConnectionManager --------------------------------------------------------------


class _FakeConnection:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.received: list[str] = []

    async def send_text(self, payload: str) -> None:
        if self.fail:
            raise RuntimeError("transport gone")
        self.received.append(payload)


def test_broadcast_isolates_failing_connection():
    """한 client의 전송 실패가 다른 client의 수신을 막지 않는다."""
    manager = ws_mod.ConnectionManager()
    ok_a, bad, ok_b = _FakeConnection(), _FakeConnection(fail=True), _FakeConnection()
    for conn in (ok_a, bad, ok_b):
        manager.register(conn)

    asyncio.run(manager.broadcast({"type": "status", "containers": []}))

    assert len(ok_a.received) == 1
    assert len(ok_b.received) == 1
    assert manager.active_connections == [ok_a, ok_b]


def test_broadcast_skips_unserializable_payload():
    """직렬화 불가 payload가 모든 client를 조용히 evict하면 안 된다."""
    manager = ws_mod.ConnectionManager()
    conn = _FakeConnection()
    manager.register(conn)

    asyncio.run(manager.broadcast({"bad": datetime.date(2026, 7, 20)}))

    assert conn.received == []
    assert manager.active_connections == [conn]


def test_status_broadcast_loop_skips_docker_without_connections():
    """연결이 없으면 docker sweep 자체를 하지 않는다."""

    async def main():
        with patch.object(ws_mod, "docker_service") as mock_docker:
            mock_docker.get_containers_status.return_value = []

            task = asyncio.create_task(ws_mod.status_broadcast_loop())
            for _ in range(5):
                await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert mock_docker.get_containers_status.call_count == 0

            conn = _FakeConnection()
            ws_mod.status_manager.register(conn)
            try:
                task = asyncio.create_task(ws_mod.status_broadcast_loop())
                for _ in range(20):
                    await asyncio.sleep(0)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            finally:
                ws_mod.status_manager.disconnect(conn)

            assert mock_docker.get_containers_status.call_count >= 1
            assert conn.received, "연결이 있으면 broadcast가 전달되어야 한다"

    asyncio.run(main())


# --- 로그 스트림 종료 경로 ------------------------------------------------------------


class _ScriptedWebSocket:
    """ws_status를 직접 구동하기 위한 최소 WebSocket 대역.

    receive()는 keepalive 프레임을 지정 간격으로 계속 흘려보내 재인가 창이 프레임에
    의해 리셋되는지(=우회되는지)를 드러낸다.
    """

    def __init__(self, *, keepalive_interval: float | None):
        self.keepalive_interval = keepalive_interval
        self.sent: list[dict] = []
        self.closed_with: int | None = None

    async def receive(self):
        if self.keepalive_interval is None:
            await asyncio.Event().wait()  # 아무것도 보내지 않는 client
        await asyncio.sleep(self.keepalive_interval)
        return {"type": "websocket.text", "text": "ping"}

    async def send_json(self, message):
        self.sent.append(message)

    async def close(self, code=1000, reason=""):
        self.closed_with = code


def _run_ws_status(websocket, *, authorize, interval: float, timeout: float = 2.0):
    async def main():
        await asyncio.wait_for(ws_status_direct(websocket, authorize, interval), timeout)

    asyncio.run(main())


async def ws_status_direct(websocket, authorize, interval):
    """monkeypatch 없이 모듈 전역만 바꿔 ws_status를 그대로 실행한다."""
    import contextlib as _contextlib

    original_authorize = ws_mod._websocket_authorize
    original_interval = ws_mod._REAUTH_INTERVAL_SECONDS
    original_accept = ws_mod._accept_best_effort
    original_docker = ws_mod.docker_service

    class _Docker:
        @staticmethod
        def get_containers_status():
            return []

    async def _accept(_ws):
        return True

    ws_mod._websocket_authorize = authorize
    ws_mod._REAUTH_INTERVAL_SECONDS = interval
    ws_mod._accept_best_effort = _accept
    ws_mod.docker_service = _Docker()
    try:
        with _contextlib.suppress(Exception):
            await ws_mod.ws_status(websocket)
    finally:
        ws_mod._websocket_authorize = original_authorize
        ws_mod._REAUTH_INTERVAL_SECONDS = original_interval
        ws_mod._accept_best_effort = original_accept
        ws_mod.docker_service = original_docker
        ws_mod.status_manager.disconnect(websocket)


def test_live_session_revocation_closes_4401():
    """살아 있는 소켓도 재인가에서 폐기가 확인되면 4401로 닫아야 한다."""
    calls = {"n": 0}

    def authorize(_ws):
        calls["n"] += 1
        return _context() if calls["n"] == 1 else None  # 두 번째 검사에서 폐기

    websocket = _ScriptedWebSocket(keepalive_interval=None)
    _run_ws_status(websocket, authorize=authorize, interval=0.05)

    assert calls["n"] >= 2, "재인가가 한 번도 다시 일어나지 않았다"
    assert websocket.closed_with == ws_mod.WS_CLOSE_AUTH_REQUIRED


def test_live_session_expiry_closes_4401():
    """expires_at이 지나면 재인가 결과와 무관하게 닫는다."""
    websocket = _ScriptedWebSocket(keepalive_interval=None)
    _run_ws_status(
        websocket,
        authorize=lambda _ws: _context(ttl_seconds=-1.0),  # 이미 만료된 세션
        interval=0.05,
    )

    assert websocket.closed_with == ws_mod.WS_CLOSE_AUTH_REQUIRED


def test_reauth_is_not_bypassed_by_keepalive_frames():
    """재인가 창이 프레임마다 리셋되면 keepalive를 보내는 client는 영원히 재검증되지 않는다.

    적대적 리뷰에서 실제로 재현된 우회다. 재인가 시점을 monotonic deadline에 고정하지
    않으면 이 테스트가 실패한다(재인가 0회, 정상 종료).
    """
    calls = {"n": 0}

    def authorize(_ws):
        calls["n"] += 1
        return _context() if calls["n"] == 1 else None

    # 재인가 주기(0.05s)보다 자주 프레임을 보내는 client.
    websocket = _ScriptedWebSocket(keepalive_interval=0.01)
    _run_ws_status(websocket, authorize=authorize, interval=0.05)

    assert calls["n"] >= 2, (
        f"keepalive 프레임이 재인가를 우회했다 — 재인가 호출 {calls['n']}회"
    )
    assert websocket.closed_with == ws_mod.WS_CLOSE_AUTH_REQUIRED


def test_read_next_chunk_returns_eof_sentinel():
    """소진된 generator를 None(데이터 없음)과 구분하지 못하면 영원히 polling한다."""
    stream = iter([b"a\n"])

    assert ws_mod._read_next_chunk(stream) == b"a\n"
    assert ws_mod._read_next_chunk(stream) is ws_mod._EOF
    assert ws_mod._read_next_chunk(stream) is ws_mod._EOF
