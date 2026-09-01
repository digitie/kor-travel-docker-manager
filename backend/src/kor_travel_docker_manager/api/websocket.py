import asyncio
import contextlib
import json
import logging
import os
import random
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
WS_CLOSE_TRY_AGAIN_LATER = 1013     # 인가 동시성 상한 초과 — 재시도 가능
WS_CLOSE_INVALID_CONTAINER = 4000   # 알 수 없는 container_id
WS_CLOSE_AUTH_REQUIRED = 4401       # 미인증 / 허용되지 않은 Origin / 세션 만료·폐기

# 동시에 인가(Origin+세션 쿠키 검증) 처리 중인 handshake 상한.
#
# **이 상한이 실제로 묶는 것과 묶지 않는 것을 정확히 적는다.** 적대적 리뷰에서 최초 근거가
# 틀린 것으로 측정됐다.
#
# 묶는 것: `to_thread` dispatch와 동시 인가 handshake 수. 유효 서명 쿠키를 가진 peer에
#   한해 executor queue 깊이.
# 묶지 **않는** 것: fd, uvicorn protocol 객체, ASGI task, `_accept_and_close`의
#   accept/close 소켓 수명. flood에서 실제로 고갈되는 자원은 이쪽이다.
#
# 미인증 peer는 SQLite에 **도달하지 못한다**. `validate_session_cookie`는 쿠키가 없으면
# session을 열기 전에 None을 돌려주고, `get_db_session()` SELECT는 HMAC 서명 검증
# 뒤에 있어 KTDM_SESSION_SECRET 없이는 통과할 수 없다(측정: 미인증 경로 DB session 0건,
# 호출당 0.2~2.6us). 따라서 "거절마다 DB 조회가 잡힌다"는 서술로 이 값을 튜닝하지 말 것.
#
# **per-IP 제한을 쓰지 않는 이유**: 이 배포의 공개 트래픽은 전부 리버스 프록시 IP 하나로
# 도착하고(신뢰 프록시 CIDR이 loopback 전용이라 X-Forwarded-For를 신뢰하지 않는다),
# per-IP 버킷은 인터넷 전체를 한 키에 묶어 정상 관리자까지 함께 막는다. per-IP로 가려면
# `KTDM_TRUSTED_PROXY_CIDRS`에 프록시 IP를 **먼저** 추가해야 하고(이게 필수 조건),
# `KTDM_TRUSTED_PROXY_SECRET`는 loopback 위조를 막는 추가 방어다.
#
# 전체 동시 연결 수는 이 상한의 범위가 아니다. uvicorn `--limit-concurrency`로는 막을 수
# 없다 — h11_impl은 WebSocket upgrade를 503 검사 **이전에** return한다(0.28.1
# h11_impl.py:221-230). 연결 수 제한은 프록시(HAProxy `maxconn`/stick-table)에서 건다.
#
# 이 값은 프로세스(uvicorn worker)당 상한이다. 워커를 늘리면 실효 상한도 그만큼 곱해진다.
_MAX_PENDING_WS_AUTH_ENV = "KTDM_WS_MAX_PENDING_AUTHORIZATIONS"
_DEFAULT_MAX_PENDING_WS_AUTH = 64
_MIN_MAX_PENDING_WS_AUTH = 1
_MAX_MAX_PENDING_WS_AUTH = 10_000

# 단일 event loop에서만 갱신하므로 lock이 필요 없다(검사와 증가 사이에 await가 없다).
_pending_ws_authorizations = 0
# shed 상태 진입/해제에서만 로그를 남긴다. 거절 1건마다 logger.warning을 부르면 attacker가
# 제어하는 동기 디스크 write가 되어, shed 경로가 완화하려던 경로보다 오히려 비싸진다
# (측정: 거절당 1039~1207us vs 로그 제거 시 57~62us, 4401 경로는 285~313us).
_ws_shedding = False
_ws_shed_count = 0

# accept()/close()는 각각 이 시간 안에 끝나야 한다(wedged transport가 handler를 막지 않게).
_WS_HANDSHAKE_TIMEOUT_SECONDS = 1.0

# 거절 경로의 close 상한. websockets의 close()는 peer의 close echo를 기다리므로, 미인증
# peer가 echo를 보내지 않으면 그 시간만큼 소켓과 ASGI task를 잡아 둘 수 있다. 거절은
# close frame이 나간 시점에 이미 계약을 만족하므로 echo를 오래 기다리지 않는다.
_REJECT_CLOSE_TIMEOUT_SECONDS = 0.1

# accept(101)과 close frame 사이 settle 대기(초). 배포 토폴로지별로 env로 튜닝한다.
#
# 기본값 0.0은 추정이 아니라 실측 결과다. 운영 HAProxy TLS 엣지를 경유한 실제 Chromium
# 으로 0.25에서 10/10, 0.0에서 12/12 모두 `code=4401, wasClean=true, data frame 0건`을
# 관측했고 1006은 한 번도 나오지 않았다(거절 왕복 264~791ms → 79~373ms).
# 근거: uvicorn 0.28.1의 legacy websockets_impl은 `websocket.close`를
# `handshake_completed_event.wait()` 뒤에 처리하므로 101과 close frame이 서버 단에서
# 이미 직렬화된다. Map(T-VN-H11)이 0.25가 필요했던 것은 websockets-sansio 구현이라
# 그 수치는 이 스택에 그대로 이식되지 않는다.
#
# 다만 uvicorn의 ws 구현을 바꾸거나(sansio 등) 프록시 토폴로지가 달라지면 coalescing
# 위험이 되살아난다. 그때는 이 env로 올리고 반드시 실브라우저로 재측정한다.
_ACCEPT_CLOSE_SETTLE_ENV = "KTDM_WS_ACCEPT_CLOSE_SETTLE_SECONDS"
_DEFAULT_ACCEPT_CLOSE_SETTLE_SECONDS = 0.0
_MAX_ACCEPT_CLOSE_SETTLE_SECONDS = 5.0

# 살아 있는 연결의 세션 재검증 주기(초). jitter로 위상 고정을 푼다.
_REAUTH_INTERVAL_SECONDS = 60.0
_REAUTH_JITTER_RATIO = 0.2

# 상태 broadcast 주기(초).
_STATUS_BROADCAST_INTERVAL_SECONDS = 2.0

# ConnectionManager.broadcast()가 client 하나로의 send를 기다리는 상한(초).
#
# TCP 버퍼가 가득 찬 죽은-그러나-닫히지-않은 피어(노트북 절전, 네트워크 단절)는
# send_text를 예외 없이 매달리게 만든다 — 죽은 peer는 TCP 재전송 타임아웃(tcp_retries2,
# 보통 13~30분)까지, zero-window인 살아있는 peer는 문자 그대로 무기한. broadcast는
# status_broadcast_loop가 직접 await하는 유일한 fan-out이라, 이 상한이 없으면 그 시간
# 동안 다른 모든 client의 상태 갱신이 함께 멈춘다(GM-15).
_BROADCAST_SEND_TIMEOUT_SECONDS = 3.0

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


def _next_reauth_deadline(loop: asyncio.AbstractEventLoop) -> float:
    """재인가 deadline을 jitter와 함께 계산한다.

    배포 때 백엔드를 재기동하면 열려 있던 모든 tab이 같은 순간에 재연결해 deadline이
    영구히 위상 고정된다. jitter가 없으면 N개 소켓의 세션 조회가 60초마다 같은
    밀리초에 몰린다(공유 executor로).
    """
    jitter = _REAUTH_INTERVAL_SECONDS * _REAUTH_JITTER_RATIO * random.random()
    return loop.time() + _REAUTH_INTERVAL_SECONDS + jitter


def _resolve_max_pending_ws_authorizations() -> int:
    """동시 인가 상한을 env에서 읽는다. 비정상 값은 기본값/범위로 clamp한다."""
    raw = os.environ.get(_MAX_PENDING_WS_AUTH_ENV)
    if raw is None or not raw.strip():
        return _DEFAULT_MAX_PENDING_WS_AUTH
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_PENDING_WS_AUTH
    return min(_MAX_MAX_PENDING_WS_AUTH, max(_MIN_MAX_PENDING_WS_AUTH, value))


async def _authorize_with_limit(
    websocket: WebSocket,
) -> tuple[bool, AdminSessionContext | None]:
    """동시성 상한 안에서 인가를 수행한다.

    반환 `(granted, context)`. `granted=False`는 과부하라 인가 자체를 수행하지 않았다는
    뜻이다 — 이 경로는 DB를 건드리지 않으므로 flood 시 가장 싼 경로로 부하를 흘려보낸다.
    """
    global _pending_ws_authorizations, _ws_shedding, _ws_shed_count

    if _pending_ws_authorizations >= _resolve_max_pending_ws_authorizations():
        _ws_shed_count += 1
        if not _ws_shedding:
            # edge-triggered: 진입 시 1회만 기록한다.
            _ws_shedding = True
            logger.warning(
                "WebSocket authorization shedding started (pending=%d at limit)",
                _pending_ws_authorizations,
            )
        return False, None

    if _ws_shedding:
        _ws_shedding = False
        logger.warning(
            "WebSocket authorization shedding ended (total shed=%d)", _ws_shed_count
        )

    _pending_ws_authorizations += 1
    try:
        return True, await asyncio.to_thread(_websocket_authorize, websocket)
    finally:
        _pending_ws_authorizations -= 1


def encode_status_payload(message: dict) -> str | None:
    """상태 payload를 WebSocket text frame 문자열로 직렬화한다. 실패하면 None.

    ensure_ascii=True는 starlette 0.37.2 `send_json`(ensure_ascii=False)과 **의도적으로**
    다르다. False로 두면 os.environ의 surrogateescape 바이트(예: `_public_url()`이 읽는
    `KTDM_PROD_URL_*`)가 lone surrogate로 남아 websockets의 utf-8 encode에서
    UnicodeEncodeError가 난다. broadcast에서는 전 client가 evict/재연결을 반복하고,
    최초 프레임에서는 연결이 곧바로 1011로 죽는다. 두 경로가 갈라지지 않도록 반드시
    이 helper를 함께 쓴다.
    """
    try:
        # UnicodeEncodeError는 ValueError의 하위 클래스라 아래 guard에 이미 포함된다.
        return json.dumps(message, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError):
        logger.error("Status payload is not JSON serializable", exc_info=True)
        return None


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
        # 직렬화를 fan-out 밖에서 한 번만 한다. 직렬화 불가 payload가 모든 client를
        # 조용히 evict하지 않고 서버 오류로 귀속된다.
        payload = encode_status_payload(message)
        if payload is None:
            return
        for connection in list(self.active_connections):
            try:
                await asyncio.wait_for(
                    connection.send_text(payload), timeout=_BROADCAST_SEND_TIMEOUT_SECONDS
                )
            except TimeoutError:
                logger.warning(
                    "Dropping WebSocket client after send timed out (%.1fs) — "
                    "peer likely wedged (dead-but-not-closed transport)",
                    _BROADCAST_SEND_TIMEOUT_SECONDS,
                )
                self.disconnect(connection)
                # 목록에서만 지우면 이 연결을 만든 핸들러(예: ws_status)의 자기
                # receive 루프는 이 사실을 모른 채 계속 park된다 — 60초 재인가도
                # 계속 성공해 zombie handler로 남는다. 명시적으로 닫아 그 루프를
                # 깨운다(best-effort — 이미 매달린 소켓이라 close 자체도 실패할 수
                # 있지만, 그때도 무기한 대기하지 않는다).
                await _close_best_effort(connection, code=WS_CLOSE_INTERNAL_ERROR)
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
    granted, context = await _authorize_with_limit(websocket)
    if not granted:
        await _accept_and_close(
            websocket, code=WS_CLOSE_TRY_AGAIN_LATER, reason="TRY_AGAIN_LATER"
        )
        return
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
        # broadcast와 같은 encoder를 쓴다. send_json은 ensure_ascii=False라 lone surrogate가
        # 섞이면 최초 프레임에서만 죽어 소켓이 영구히 1011로 끊긴다.
        initial = encode_status_payload({"type": "status", "containers": status})
        if initial is not None:
            await websocket.send_text(initial)

        recv_task = asyncio.create_task(websocket.receive())
        loop = asyncio.get_running_loop()
        # 재인가 시점은 monotonic deadline에 고정한다. wait의 timeout을 그대로 쓰면
        # 프레임이 올 때마다 창이 처음부터 다시 시작해, keepalive를 주기보다 자주 보내는
        # client는 영원히 재검증되지 않는다(logout/TTL이 살아 있는 소켓에 적용되지 않음).
        deadline = _next_reauth_deadline(loop)
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
            deadline = _next_reauth_deadline(loop)
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
        if recv_task is not None:
            if not recv_task.done():
                recv_task.cancel()
            # done인 task도 예외가 회수되지 않을 수 있다(ws_logs와 같은 모양으로 맞춘다).
            recv_task.add_done_callback(_discard_future_result)
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
    granted, context = await _authorize_with_limit(websocket)
    if not granted:
        await _accept_and_close(
            websocket, code=WS_CLOSE_TRY_AGAIN_LATER, reason="TRY_AGAIN_LATER"
        )
        return
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
        deadline = _next_reauth_deadline(loop)
        while True:
            done, _ = await asyncio.wait(
                {read_task, recv_task},
                timeout=max(0.0, deadline - loop.time()),
                return_when=asyncio.FIRST_COMPLETED,
            )

            # 재인가를 send보다 먼저 본다. 뒤에 두면 deadline이 지난 iteration에서
            # 폐기된 세션에 로그 chunk를 한 번 더 흘려보낸다.
            if loop.time() >= deadline:
                deadline = _next_reauth_deadline(loop)
                if utcnow() >= context.expires_at:
                    close_code = WS_CLOSE_AUTH_REQUIRED
                    break
                refreshed = await asyncio.to_thread(_websocket_authorize, websocket)
                if refreshed is None:
                    close_code = WS_CLOSE_AUTH_REQUIRED
                    break
                context = refreshed

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
