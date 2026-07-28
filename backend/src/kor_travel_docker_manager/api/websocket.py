import asyncio
import contextlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kor_travel_docker_manager._time import utcnow
from kor_travel_docker_manager.services.auth_service import (
    SESSION_COOKIE_NAME,
    AdminSessionContext,
    validate_session_cookie,
    websocket_origin_allowed,
)
from kor_travel_docker_manager.services.docker_service import MANAGED_CONTAINERS, docker_service

logger = logging.getLogger(__name__)
router = APIRouter()

# --- C7 WebSocket 종료 코드 계약 (docs/docker-management.md) --------------------------
WS_CLOSE_NORMAL = 1000              # 정상 종료(스트림 EOF 포함)
WS_CLOSE_INTERNAL_ERROR = 1011      # 서버측 오류로 종료
WS_CLOSE_INVALID_CONTAINER = 4000   # 알 수 없는 container_id
WS_CLOSE_AUTH_REQUIRED = 4401       # 미인증 / 허용되지 않은 Origin / 세션 만료·폐기

# accept()/close()는 각각 이 시간 안에 끝나야 한다(wedged transport가 handler를 막지 않게).
_WS_HANDSHAKE_TIMEOUT_SECONDS = 1.0

# 거절 경로의 close 상한. websockets의 close()는 peer의 close echo를 기다리므로, 미인증
# peer가 echo를 보내지 않으면 그 시간만큼 소켓과 ASGI task를 잡아 둘 수 있다. 거절은
# close frame이 나간 시점에 이미 계약을 만족하므로 echo를 오래 기다리지 않는다.
_REJECT_CLOSE_TIMEOUT_SECONDS = 0.1

# accept(101)과 close frame 사이 settle 대기(초). 배포 토폴로지별로 env로 튜닝한다.
_ACCEPT_CLOSE_SETTLE_ENV = "KTDM_WS_ACCEPT_CLOSE_SETTLE_SECONDS"
_DEFAULT_ACCEPT_CLOSE_SETTLE_SECONDS = 0.25
_MAX_ACCEPT_CLOSE_SETTLE_SECONDS = 5.0

# 살아 있는 연결의 세션 재검증 주기(초).
_REAUTH_INTERVAL_SECONDS = 60.0

# 상태 broadcast 주기(초).
_STATUS_BROADCAST_INTERVAL_SECONDS = 2.0

# ws_logs의 blocking docker read 전용 pool. 기본 pool(asyncio.to_thread)을 쓰면 idle
# container의 미회수 reader가 metrics_collector·log cleanup의 to_thread까지 굶긴다.
_LOG_STREAM_MAX_WORKERS = 8
_log_stream_executor = ThreadPoolExecutor(
    max_workers=_LOG_STREAM_MAX_WORKERS, thread_name_prefix="ktdm-log-stream"
)

# 로그 스트림 EOF sentinel. None(아직 데이터 없음)과 반드시 구분되어야 한다.
_EOF = object()


def shutdown_log_stream_executor() -> None:
    """lifespan 종료 시 로그 스트림 전용 pool을 정리한다."""
    _log_stream_executor.shutdown(wait=False, cancel_futures=True)


def _resolve_accept_close_settle_seconds() -> float:
    """reject-close의 accept(101)→close frame settle 대기(초)를 env에서 읽는다.

    ASGI에는 transport drain acknowledgement가 없다. accept와 close가 한 write로
    coalesce되면 리버스 프록시 엣지가 close frame을 잘라 브라우저 WebSocket API가
    4401 대신 1006으로 뭉갠다. 값은 실브라우저 측정으로 정하고, 비정상 값은
    [0, 5]로 clamp해 sleep을 bounded로 유지한다.
    """
    raw = os.environ.get(_ACCEPT_CLOSE_SETTLE_ENV)
    if raw is None or not raw.strip():
        return _DEFAULT_ACCEPT_CLOSE_SETTLE_SECONDS
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_ACCEPT_CLOSE_SETTLE_SECONDS
    return min(_MAX_ACCEPT_CLOSE_SETTLE_SECONDS, max(0.0, seconds))


async def _accept_best_effort(websocket: WebSocket) -> bool:
    """handshake를 bounded로 수락한다. 실패를 False로 알리고 예외를 삼킨다."""
    try:
        await asyncio.wait_for(websocket.accept(), timeout=_WS_HANDSHAKE_TIMEOUT_SECONDS)
        return True
    except TimeoutError:
        logger.warning("WebSocket accept timeout")
    except Exception:  # noqa: BLE001 — close/teardown으로 이어지는 격리 경계다.
        logger.exception("WebSocket accept failed")
    return False


async def _close_best_effort(
    websocket: WebSocket,
    *,
    code: int,
    reason: str = "",
    close_timeout: float = _WS_HANDSHAKE_TIMEOUT_SECONDS,
) -> None:
    try:
        await asyncio.wait_for(
            websocket.close(code=code, reason=reason),
            timeout=close_timeout,
        )
    except TimeoutError:
        logger.warning("WebSocket close timeout: code=%s", code)
    except Exception:  # noqa: BLE001 — connection 정리는 best effort 경계다.
        logger.debug("WebSocket close failed: code=%s", code, exc_info=True)


async def _accept_and_close(websocket: WebSocket, *, code: int, reason: str) -> None:
    """거절을 accept-then-close로 전달한다.

    accept 이전 close는 ASGI상 handshake 거절이라 uvicorn이 HTTP 403으로 바꿔 보내고
    브라우저는 4401 대신 1006만 본다. data frame을 한 건도 보내지 않는 최소 handshake를
    완료한 뒤 application close code로 닫아야 client가 재시도를 멈출 수 있다.
    """

    async def _accept_settle_close() -> None:
        if not await _accept_best_effort(websocket):
            # accept 실패 뒤 close를 보내면 다시 pre-handshake 거절로 퇴화한다.
            return
        settle = _resolve_accept_close_settle_seconds()
        if settle > 0:
            await asyncio.sleep(settle)
        await _close_best_effort(
            websocket,
            code=code,
            reason=reason,
            close_timeout=_REJECT_CLOSE_TIMEOUT_SECONDS,
        )

    # accept 성공 직후 도착한 outer cancellation이 close를 지워 1006으로 되돌아가지
    # 않도록 전체 시퀀스를 하나의 child task로 묶어 shield한다. 반복 취소도 안전하다.
    # shield가 잡아 두는 시간은 accept(<=1s) + settle(<=_MAX) + close(<=0.1s)로 bounded다.
    # settle도 shield 안에 있으므로 _MAX_ACCEPT_CLOSE_SETTLE_SECONDS가 곧 graceful
    # shutdown 지연의 상한이 된다.
    operation = asyncio.create_task(_accept_settle_close())
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            await asyncio.shield(operation)
            break
        except asyncio.CancelledError as exc:
            if operation.done():
                raise
            cancellation = exc
    if cancellation is not None:
        raise cancellation


def _websocket_authorize(websocket: WebSocket) -> AdminSessionContext | None:
    """Origin 허용 + 세션 쿠키 검증.

    동기 SQLite 접근(validate_session_cookie → _ensure_db)을 포함하므로 event loop에서
    직접 호출하지 말고 asyncio.to_thread로 호출한다.
    """
    if not websocket_origin_allowed(websocket):
        return None
    return validate_session_cookie(websocket.cookies.get(SESSION_COOKIE_NAME), websocket)


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    def register(self, websocket: WebSocket) -> None:
        self.active_connections.append(websocket)
        logger.info("New client connected. Total connections: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("Client disconnected. Total connections: %d", len(self.active_connections))

    async def broadcast(self, message: dict) -> None:
        if not self.active_connections:
            return
        try:
            # 직렬화를 fan-out 밖에서 한 번만 한다. 직렬화 불가 payload가 모든 client를
            # 조용히 evict하지 않고 서버 오류로 귀속된다.
            # ensure_ascii=True는 Starlette send_json과 동일한 인코딩이다. False로 두면
            # os.environ의 surrogateescape 바이트(예: KTDM_PROD_URL_*)가 lone surrogate로
            # 남아 websockets의 utf-8 encode에서 UnicodeEncodeError가 나고, 모든 client가
            # 2초마다 evict/재연결을 반복한다.
            payload = json.dumps(message, ensure_ascii=True, separators=(",", ":"))
        except (TypeError, ValueError, UnicodeEncodeError):
            logger.error("Status payload is not JSON serializable; broadcast skipped", exc_info=True)
            return
        for connection in list(self.active_connections):
            try:
                await connection.send_text(payload)
            except Exception as e:  # noqa: BLE001 — 예외 종류를 좁히면 dead connection이 샌다.
                logger.warning("Dropping WebSocket client after send failure: %s", e)
                self.disconnect(connection)


status_manager = ConnectionManager()


async def status_broadcast_loop():
    """주기적으로 컨테이너 상태/메트릭을 broadcast한다."""
    while True:
        try:
            # 연결이 없으면 docker sweep 자체를 건너뛴다.
            if status_manager.active_connections:
                # get_containers_status는 compose YAML 파싱 + 컨테이너당 docker
                # round-trip인 동기 코드다. event loop에서 직접 부르면 hung dockerd가
                # 모든 HTTP route와 /health를 함께 얼린다.
                status = await asyncio.to_thread(docker_service.get_containers_status)
                await status_manager.broadcast({"type": "status", "containers": status})
        except Exception:
            logger.exception("Error in status broadcast loop")
        await asyncio.sleep(_STATUS_BROADCAST_INTERVAL_SECONDS)


@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    context = await asyncio.to_thread(_websocket_authorize, websocket)
    if context is None:
        await _accept_and_close(websocket, code=WS_CLOSE_AUTH_REQUIRED, reason="AUTH_REQUIRED")
        return

    if not await _accept_best_effort(websocket):
        return  # accept 실패 뒤에는 close도 보내지 않는다.

    status_manager.register(websocket)
    close_code = WS_CLOSE_NORMAL
    recv_task: asyncio.Task | None = None
    try:
        status = await asyncio.to_thread(docker_service.get_containers_status)
        await websocket.send_json({"type": "status", "containers": status})

        recv_task = asyncio.create_task(websocket.receive())
        loop = asyncio.get_running_loop()
        # 재인가 시점은 monotonic deadline에 고정한다. wait의 timeout을 그대로 쓰면
        # 프레임이 올 때마다 창이 처음부터 다시 시작해, keepalive를 주기보다 자주 보내는
        # client는 영원히 재검증되지 않는다(logout/TTL이 살아 있는 소켓에 적용되지 않음).
        deadline = loop.time() + _REAUTH_INTERVAL_SECONDS
        while True:
            # wait_for가 아니라 wait를 쓴다: timeout이 나도 recv_task를 취소하지 않으므로
            # websockets의 recv()를 반복 취소하는 위험이 없다.
            done, _ = await asyncio.wait(
                {recv_task}, timeout=max(0.0, deadline - loop.time())
            )

            if recv_task in done:
                message = recv_task.result()
                if message["type"] == "websocket.disconnect":
                    break
                # text/binary 어떤 프레임이든 keepalive로 무시한다.
                recv_task = asyncio.create_task(websocket.receive())
                if loop.time() < deadline:
                    continue

            # deadline 도달 — logout(revoked_at)/TTL 만료를 살아 있는 소켓에도 적용한다.
            deadline = loop.time() + _REAUTH_INTERVAL_SECONDS
            if utcnow() >= context.expires_at:
                close_code = WS_CLOSE_AUTH_REQUIRED
                break
            refreshed = await asyncio.to_thread(_websocket_authorize, websocket)
            if refreshed is None:
                close_code = WS_CLOSE_AUTH_REQUIRED
                break
            context = refreshed
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WS status handler failed")
        close_code = WS_CLOSE_INTERNAL_ERROR
    finally:
        # 규칙: accept된 WebSocket handler는 명시적 close 없이 return하지 않는다.
        # finally이므로 CancelledError(BaseException)에서도 등록이 해제된다.
        status_manager.disconnect(websocket)
        if recv_task is not None and not recv_task.done():
            recv_task.cancel()
        await _close_best_effort(websocket, code=close_code)


def _read_next_chunk(stream) -> Any:
    """다음 로그 chunk를 읽는다. 스트림이 끝나면 _EOF sentinel을 돌려준다.

    StopIteration만 EOF로 접고 나머지 예외는 그대로 전파해 호출부의 except가 처리한다.
    둘 다 None으로 접으면 '아직 데이터 없음'과 구분되지 않아 소진된 generator를
    영원히 polling한다.
    """
    try:
        return next(stream)
    except StopIteration:
        return _EOF


def _discard_future_result(future) -> None:
    with contextlib.suppress(BaseException):
        future.exception()


def _open_container(cname: str):
    return docker_service._get_client().containers.get(cname)


def _open_log_stream(container):
    return container.logs(stdout=True, stderr=True, stream=True, follow=True, tail=100)


@router.websocket("/ws/logs/{container_id}")
async def ws_logs(websocket: WebSocket, container_id: str):
    # 인증을 먼저 본다: container 존재 여부가 미인증 peer에게 새면 안 된다.
    context = await asyncio.to_thread(_websocket_authorize, websocket)
    if context is None:
        await _accept_and_close(websocket, code=WS_CLOSE_AUTH_REQUIRED, reason="AUTH_REQUIRED")
        return
    if container_id not in MANAGED_CONTAINERS:
        await _accept_and_close(
            websocket, code=WS_CLOSE_INVALID_CONTAINER, reason="INVALID_CONTAINER_ID"
        )
        return

    if not await _accept_best_effort(websocket):
        return  # accept 실패 뒤에는 close도 보내지 않는다.

    cname = MANAGED_CONTAINERS[container_id]["name"]
    close_code = WS_CLOSE_NORMAL
    log_stream = None                       # finally가 미바인딩 이름을 만지지 않게
    recv_task: asyncio.Task | None = None
    read_task: asyncio.Future | None = None
    loop = asyncio.get_running_loop()

    try:
        container = await asyncio.to_thread(_open_container, cname)
        log_stream = await asyncio.to_thread(_open_log_stream, container)

        # read와 receive를 각각 한 개만 유지한다. 같은 generator에 next()를 동시에 두 번
        # 걸면 ValueError: generator already executing이 난다.
        recv_task = asyncio.create_task(websocket.receive())
        read_task = loop.run_in_executor(_log_stream_executor, _read_next_chunk, log_stream)

        # ws_status와 같은 재인가 계약을 적용한다. 로그 stdout/stderr는 status보다
        # 민감할 수 있으므로 revoked/만료 세션이 스트림을 이어받게 두지 않는다.
        deadline = loop.time() + _REAUTH_INTERVAL_SECONDS
        while True:
            done, _ = await asyncio.wait(
                {read_task, recv_task},
                timeout=max(0.0, deadline - loop.time()),
                return_when=asyncio.FIRST_COMPLETED,
            )

            if recv_task in done:
                message = recv_task.result()
                if message["type"] == "websocket.disconnect":
                    break            # idle container에서도 client 종료를 관측한다
                recv_task = asyncio.create_task(websocket.receive())

            if read_task in done:
                chunk = read_task.result()
                if chunk is _EOF:
                    # 기존 {"error": ...} envelope을 유지해 프론트 변경을 강제하지 않는다.
                    await websocket.send_json({"error": "로그 스트림이 종료되었습니다."})
                    break
                await websocket.send_json({"log": chunk.decode("utf-8", errors="ignore")})
                read_task = loop.run_in_executor(
                    _log_stream_executor, _read_next_chunk, log_stream
                )

            if loop.time() >= deadline:
                deadline = loop.time() + _REAUTH_INTERVAL_SECONDS
                if utcnow() >= context.expires_at:
                    close_code = WS_CLOSE_AUTH_REQUIRED
                    break
                refreshed = await asyncio.to_thread(_websocket_authorize, websocket)
                if refreshed is None:
                    close_code = WS_CLOSE_AUTH_REQUIRED
                    break
                context = refreshed
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error streaming logs for %s", container_id)
        close_code = WS_CLOSE_INTERNAL_ERROR
        with contextlib.suppress(Exception):
            await websocket.send_json({"error": "로그 스트림을 열지 못했습니다."})
    finally:
        if recv_task is not None and not recv_task.done():
            recv_task.cancel()
        if read_task is not None:
            # 실행 중인 blocking read는 취소되지 않는다. 결과/예외만 소비해 경고를 막는다.
            # 이미 done인 future도 포함해야 한다: disconnect와 read 실패가 같은 wait에서
            # 함께 끝나면 result()를 읽지 않은 채 break하므로 예외가 회수되지 않는다.
            read_task.add_done_callback(_discard_future_result)
        if log_stream is not None:
            try:
                # docker-py 7.x의 CancellableStream.close()는 소켓을 shutdown해
                # next()에 park된 worker thread를 깨운다(그 뒤 __next__가 StopIteration).
                log_stream.close()
            except Exception:
                logger.warning(
                    "log stream teardown failed for %s", container_id, exc_info=True
                )
        await _close_best_effort(websocket, code=close_code)
