import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from logging.handlers import BaseRotatingHandler
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from kor_travel_docker_manager.api.admin import router as admin_router
from kor_travel_docker_manager.api.auth import router as auth_router
from kor_travel_docker_manager.api.routes import router as container_router
from kor_travel_docker_manager.api.security import require_public_api_key
from kor_travel_docker_manager.api.websocket import router as ws_router
from kor_travel_docker_manager.api.websocket import (
    shutdown_log_stream_executor,
    status_broadcast_loop,
)
from kor_travel_docker_manager.request_context import (
    REQUEST_ID_HEADER,
    RequestIdLogFilter,
    current_request_id,
    request_id_var,
)
from kor_travel_docker_manager.services.auth_service import allowed_frontend_origins
from kor_travel_docker_manager.services.compose_service import get_env_path
from kor_travel_docker_manager.services.errors import (
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.job_runner import job_runner
from kor_travel_docker_manager.services.metrics_collector import (
    _PROMETHEUS_CONTENT_TYPE,
    metrics_collector,
)
from kor_travel_docker_manager.services.metrics_service import metrics_service
from kor_travel_docker_manager.services.public_api_key_service import (
    PUBLIC_API_KEY_QUERY_PARAM,
)
from kor_travel_docker_manager.services.secure_state_file import env_flag

# 프로젝트 루트 .env(gitignore 대상)에서 prod 공개 주소/CORS 설정을 읽어온다.
# 개발 환경에서 .env가 없으면 아래 기본값(전체 허용)을 그대로 사용한다.
_ENV_PATH = get_env_path()
if os.path.exists(_ENV_PATH):
    load_dotenv(_ENV_PATH)


# -------------------------------------------------------------
# Monthly Log Rolling Handler & Clean-up Config
# -------------------------------------------------------------
class MonthlyRotatingFileHandler(BaseRotatingHandler):
    def __init__(self, filename, mode="a", encoding=None, delay=False):
        self.filename = os.path.abspath(filename)
        self.current_month = time.strftime("%Y-%m")
        super().__init__(filename, mode, encoding, delay)

    def shouldRollover(self, record):
        record_month = time.strftime("%Y-%m", time.localtime(record.created))
        return record_month != self.current_month

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        dfn = self.baseFilename + "." + self.current_month
        if os.path.exists(dfn):
            # GM-16: --reload 다중 워커 프로세스가 같은 로그 파일을 공유하면
            # 두 프로세스가 거의 동시에 rollover를 시도할 수 있다. 무조건
            # os.remove는 먼저 rollover한 프로세스가 이번 달 아카이브에 이미
            # 써 둔 로그를 통째로 파괴한다 — 대신 이어 붙이고 활성 로그
            # 파일만 비운다.
            with open(self.baseFilename, "rb") as src, open(dfn, "ab") as dst:
                dst.write(src.read())
            os.remove(self.baseFilename)
        else:
            os.rename(self.baseFilename, dfn)

        self.current_month = time.strftime("%Y-%m")
        if not self.delay:
            self.stream = self._open()


# 로그 디렉토리 정의 (backend/logs)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(BACKEND_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "kor_travel_docker_manager.log")

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s"


def _configure_logging(
    root_logger: logging.Logger, package_logger: logging.Logger, log_file_path: str
) -> None:
    """GM-16: 이전에는 핸들러를 패키지 로거에 붙인 뒤 그 리스트를 root
    logger에도 그대로 대입했다 — 패키지 로거의 propagate가 True(기본값)라
    하위 모든 로그 레코드가 패키지 레벨에서 한 번, 그 뒤 root로 전파돼 또
    한 번, 정확히 2회씩 콘솔·파일에 찍혔다(디스크 사용량 2배, 발생 빈도·계수
    판단 왜곡). 핸들러는 root에만 붙이고, 패키지 로거는 레벨만 설정해
    propagate에 맡긴다 — 레코드가 정확히 한 곳(root)에서만 emit된다.

    파라미터로 로거 객체를 받는다(전역 `logging.getLogger()`를 직접 잡지
    않는다) — pytest 자신도 root logger에 로그 캡처 핸들러를 붙이므로,
    테스트에서 진짜 root를 건드리지 않고 이 설정 로직만 독립적으로
    검증하기 위함이다."""

    formatter = logging.Formatter(_LOG_FORMAT)
    root_logger.setLevel(logging.INFO)

    # 기존 핸들러 초기화 방지(--reload 등으로 이 모듈이 재-import돼도 중복 부착하지 않는다)
    if not root_logger.handlers:
        # 1. Console Handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(RequestIdLogFilter())
        root_logger.addHandler(console_handler)

        # 2. Monthly File Handler
        file_handler = MonthlyRotatingFileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RequestIdLogFilter())
        root_logger.addHandler(file_handler)

    package_logger.setLevel(logging.INFO)


_configure_logging(logging.getLogger(), logging.getLogger("kor_travel_docker_manager"), log_file)

logger = logging.getLogger("kor_travel_docker_manager")


def cleanup_old_log_files():
    logger.info("Running scheduled log cleanup task (1 year retention)...")
    now = time.time()
    cutoff = now - (365 * 24 * 60 * 60)  # 1 year in seconds

    if os.path.exists(LOG_DIR):
        for filename in os.listdir(LOG_DIR):
            if filename.startswith("kor_travel_docker_manager.log."):
                file_path = os.path.join(LOG_DIR, filename)
                if os.path.isfile(file_path):
                    file_time = os.path.getmtime(file_path)
                    if file_time < cutoff:
                        logger.info(f"Removing expired log file: {filename} (older than 1 year)")
                        os.remove(file_path)


# 1년 경과 로그 자동 삭제 백그라운드 태스크
async def log_cleanup_loop():
    while True:
        try:
            await asyncio.to_thread(cleanup_old_log_files)
        except Exception as e:
            logger.error(f"Error during log cleanup: {e}")

        # 24시간 간격
        await asyncio.sleep(86400)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing SQLAlchemy database schema...")
    metrics_service.init_db()

    logger.info("Starting background metrics collection...")
    metrics_collector.start()

    logger.info("Starting WebSocket broadcast loop...")
    broadcast_task = asyncio.create_task(status_broadcast_loop())

    logger.info("Starting log cleanup background task...")
    cleanup_task = asyncio.create_task(log_cleanup_loop())

    yield

    # Shutdown
    logger.info("Stopping metrics collection...")
    metrics_collector.stop()

    logger.info("Stopping WebSocket broadcast task...")
    broadcast_task.cancel()

    logger.info("Stopping log cleanup task...")
    cleanup_task.cancel()

    try:
        await asyncio.gather(broadcast_task, cleanup_task, return_exceptions=True)
    except Exception:
        pass

    # job은 취소하지 않는다 — asyncio.to_thread는 실행 중인 pg_dump를 중단시키지
    # 못하고, 취소는 기록만 잃는다. 짧게 배수하고 남은 job은 경고로 남긴다.
    logger.info("Draining background jobs...")
    await job_runner.shutdown()

    logger.info("Shutting down log stream executor...")
    shutdown_log_stream_executor()

    logger.info("Application shutdown complete.")


def _resolve_cors_allow_origins() -> list[str]:
    """대시보드 프론트엔드의 허용 Origin을 환경변수로 제어한다.

    `KTDM_CORS_ALLOW_ORIGINS`(콤마 구분)에 prod 대시보드 Origin만 지정하면
    노출 범위를 좁힐 수 있다. 미설정이거나 ``*``이면 전체 허용(개발 기본값)을
    유지한다. 실제 prod 도메인은 저장소에 커밋하지 않고 gitignore된 `.env`에만 둔다.
    """
    raw = os.environ.get("KTDM_CORS_ALLOW_ORIGINS", "*").strip()
    if not raw or raw == "*":
        return list(allowed_frontend_origins())
    # allow_credentials=True 와 함께 와일드카드 '*' 가 섞여 들어가지 않도록 명시 분기에서도
    # '*' 항목을 제거한다(자격증명 포함 CORS는 정확한 Origin 매칭이어야 한다).
    origins = [
        origin.strip()
        for origin in raw.split(",")
        if origin.strip() and origin.strip() != "*"
    ]
    return origins or list(allowed_frontend_origins())


CORS_ALLOW_ORIGINS = _resolve_cors_allow_origins()

app = FastAPI(
    title="Docker Manager UI API",
    description="Docker Manager UI의 인프라 컨테이너 모니터링·관리 API와 WebSocket입니다.",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # GM-16: 커스텀 응답 헤더는 기본적으로 브라우저 JS에 노출되지 않는다 —
    # X-Request-ID를 프론트가 읽어 오류 신고에 실으려면 명시적으로 열어야 한다.
    expose_headers=[REQUEST_ID_HEADER],
)


@app.middleware("http")
async def _assign_request_id(request: Request, call_next):
    """요청마다 상관관계 ID를 발급해 로그·감사·오류 응답을 하나의 키로 잇는다(GM-16).

    클라이언트가 보낸 X-Request-ID는 신뢰하지 않는다 — 이 값은 서버 로그
    검색 키이자 감사 행에 남는 값이라, 외부에서 위조된 문자열을 그대로
    받으면 로그 스푸핑(다른 요청의 로그처럼 보이게 하기)이 가능해진다.
    항상 서버가 새로 발급한다.

    `call_next`는 Starlette의 예외 미들웨어를 거쳐 오므로, 등록된 예외
    핸들러가 처리하는 모든 오류를 포함해 항상 Response를 돌려준다 — 성공·
    실패 응답 모두에 헤더가 붙는다."""

    request_id = str(uuid.uuid4())
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response

# Include routers with v1 versioning
app.include_router(auth_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(container_router, prefix="/api/v1", tags=["containers"])
app.include_router(ws_router, prefix="/api/v1", tags=["websocket"])


def _candidate_contract_detail(error: ComposeCandidateContractError) -> dict:
    return {
        "code": error.code,
        "message": str(error),
        "stage": "candidate_validation",
        "mutation_applied": False,
    }


def _post_mutation_contract_detail(error: ComposePostMutationContractError) -> dict:
    original_code = getattr(error.original_error, "code", None)
    return {
        "code": error.code,
        "message": str(error),
        "stage": "post_mutation_recovery",
        "mutation_applied": True,
        "original_error": {
            "code": original_code,
            "message": str(error.original_error),
        },
        "recovery_attempted": error.recovery_attempted,
        "recovery_succeeded": error.recovery_succeeded,
        "recovery_error": error.recovery_error,
        "restoration": error.restoration,
    }


# GM-12: C6c 계약 위반을 라우트마다 3단 except로 반복해 매핑하던 것을 여기 한 곳으로
# 모은다. Starlette 예외 미들웨어는 발생한 예외 타입의 MRO를 훑어 가장 구체적으로
# 등록된 핸들러를 고르므로, 세 핸들러를 모두 등록해 두면 서브클래스 두 종류가 각자의
# 전용 핸들러로, 그 외 DeploymentContractError는 기본 핸들러로 정확히 갈라진다.
# base 케이스의 detail은 지금도 평문 문자열이다(예: "compatible-pair" 워크플로 안내) —
# 이를 {code, message} dict로 바꾸면 `"compatible-pair" in response.json()["detail"]`
# 같은 기존 부분 문자열 단언(in 연산이 dict에서는 키 검사가 되어 조용히 실패한다)과
# ~20곳의 exact-dict 단언이 함께 깨진다. 그래서 base는 그대로 평문 문자열로 둔다.
#
# GM-16: 세 핸들러 모두 top-level에 `request_id`를 추가한다 — `detail`의 모양은
# (문자열이든 dict든) 건드리지 않으므로 기존 `.detail` 단언은 전부 그대로다. 이
# id로 UI가 받은 오류 하나를 서버 로그·감사 행과 직접 조인할 수 있다.
@app.exception_handler(ComposePostMutationContractError)
async def _handle_post_mutation_contract_error(
    request: Request, exc: ComposePostMutationContractError
) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "detail": _post_mutation_contract_detail(exc),
            "request_id": current_request_id(),
        },
    )


@app.exception_handler(ComposeCandidateContractError)
async def _handle_candidate_contract_error(
    request: Request, exc: ComposeCandidateContractError
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": _candidate_contract_detail(exc),
            "request_id": current_request_id(),
        },
    )


@app.exception_handler(DeploymentContractError)
async def _handle_deployment_contract_error(
    request: Request, exc: DeploymentContractError
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc), "request_id": current_request_id()},
    )


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "kor-travel-docker-manager-backend"}


# GM-19: require_public_api_key는 이전에 부착된 라우트가 0개인 미사용 게이트였다.
# 기본값(미설정)은 기존 무인증 scrape(config/prometheus/prometheus.yml, 127.0.0.1
# 전용)를 그대로 유지하고, KTDM_METRICS_REQUIRE_KEY=1로 명시 opt-in한 배포만
# 인프라 토폴로지 노출(main.py 자체 host binding은 0.0.0.0일 수 있음)을 막는다.
# env를 매 요청마다 읽는다 — import 시점에 한 번만 고정하면 테스트가 이 라우트를
# 검증하려고 매번 모듈을 재로드해야 한다.
def _metrics_auth_gate(
    request: Request,
    key: Annotated[str | None, Query(alias=PUBLIC_API_KEY_QUERY_PARAM)] = None,
) -> None:
    if env_flag("KTDM_METRICS_REQUIRE_KEY"):
        require_public_api_key(request, key)


@app.get(
    "/metrics",
    include_in_schema=False,
    response_class=PlainTextResponse,
    dependencies=[Depends(_metrics_auth_gate)],
)
def prometheus_metrics() -> PlainTextResponse:
    """Prometheus용 캐시 기반 컨테이너 상태·리소스 메트릭을 노출한다."""
    return PlainTextResponse(
        content=metrics_collector.render_prometheus_metrics(),
        media_type=_PROMETHEUS_CONTENT_TYPE,
        headers={"Cache-Control": "no-store"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("kor_travel_docker_manager.main:app", host="0.0.0.0", port=12901, reload=True)
