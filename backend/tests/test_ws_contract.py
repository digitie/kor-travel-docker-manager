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
os.environ["KTDM_ADMIN_PASSWORD_HASH"] = hash_password_for_env("ad.min")
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

from kor_travel_docker_manager.api import websocket as ws_mod  # noqa: E402
from kor_travel_docker_manager.main import app  # noqa: E402
from kor_travel_docker_manager.services.auth_service import AdminSessionContext  # noqa: E402

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


def test_ws_logs_auth_reject_accepts_then_closes():
    sent = drive_websocket("/api/v1/ws/logs/db", headers=[ORIGIN_HEADER])

    assert [m["type"] for m in sent] == ["websocket.accept", "websocket.close"]
    assert sent[-1]["code"] == ws_mod.WS_CLOSE_AUTH_REQUIRED
    _assert_no_data_frames(sent)


def test_ws_logs_unknown_container_accepts_then_closes(monkeypatch):
    """인증은 통과하고 container_id만 모르는 경우 4000으로 구분되어야 한다."""
    context = AdminSessionContext(
        username="admin",
        session_id_hash="x" * 64,
        expires_at=datetime.datetime.now(datetime.UTC)
        + datetime.timedelta(hours=1),
    )
    monkeypatch.setattr(ws_mod, "_websocket_authorize", lambda ws: context)

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


def test_read_next_chunk_returns_eof_sentinel():
    """소진된 generator를 None(데이터 없음)과 구분하지 못하면 영원히 polling한다."""
    stream = iter([b"a\n"])

    assert ws_mod._read_next_chunk(stream) == b"a\n"
    assert ws_mod._read_next_chunk(stream) is ws_mod._EOF
    assert ws_mod._read_next_chunk(stream) is ws_mod._EOF
