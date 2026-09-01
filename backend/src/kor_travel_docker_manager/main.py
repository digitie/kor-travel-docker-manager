import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from logging.handlers import BaseRotatingHandler

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from kor_travel_docker_manager.api.admin import router as admin_router
from kor_travel_docker_manager.api.auth import router as auth_router
from kor_travel_docker_manager.api.routes import router as container_router
from kor_travel_docker_manager.api.websocket import router as ws_router
from kor_travel_docker_manager.api.websocket import (
    shutdown_log_stream_executor,
    status_broadcast_loop,
)
from kor_travel_docker_manager.services.auth_service import allowed_frontend_origins
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.compose_service import get_env_path
from kor_travel_docker_manager.services.job_runner import job_runner
from kor_travel_docker_manager.services.metrics_collector import (
    _PROMETHEUS_CONTENT_TYPE,
    metrics_collector,
)
from kor_travel_docker_manager.services.metrics_service import metrics_service

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
            os.remove(dfn)
        os.rename(self.baseFilename, dfn)

        self.current_month = time.strftime("%Y-%m")
        if not self.delay:
            self.stream = self._open()


# 로그 디렉토리 정의 (backend/logs)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(BACKEND_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "kor_travel_docker_manager.log")

# Logger 설정
logger = logging.getLogger("kor_travel_docker_manager")
logger.setLevel(logging.INFO)

# 기존 핸들러 초기화 방지
if not logger.handlers:
    # 1. Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # 2. Monthly File Handler
    file_handler = MonthlyRotatingFileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

# 루트 로거도 동일한 핸들러를 사용하도록 전이 설정
logging.getLogger().handlers = logger.handlers
logging.getLogger().setLevel(logging.INFO)


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
)

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
@app.exception_handler(ComposePostMutationContractError)
async def _handle_post_mutation_contract_error(
    request: Request, exc: ComposePostMutationContractError
) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": _post_mutation_contract_detail(exc)})


@app.exception_handler(ComposeCandidateContractError)
async def _handle_candidate_contract_error(
    request: Request, exc: ComposeCandidateContractError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": _candidate_contract_detail(exc)})


@app.exception_handler(DeploymentContractError)
async def _handle_deployment_contract_error(
    request: Request, exc: DeploymentContractError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "kor-travel-docker-manager-backend"}


@app.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
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
