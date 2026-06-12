import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from logging.handlers import BaseRotatingHandler

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kor_travel_docker_manager.api.routes import router as container_router
from kor_travel_docker_manager.api.websocket import router as ws_router
from kor_travel_docker_manager.api.websocket import status_broadcast_loop
from kor_travel_docker_manager.services.metrics_collector import metrics_collector
from kor_travel_docker_manager.services.metrics_service import metrics_service


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
    logger.info("Application shutdown complete.")


app = FastAPI(
    title="Kor Travel Docker Manager API",
    description="API and WebSockets for monitoring and managing Kor Travel Docker services.",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers with v1 versioning
app.include_router(container_router, prefix="/api/v1", tags=["containers"])
app.include_router(ws_router, prefix="/api/v1", tags=["websocket"])


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "kor-travel-docker-manager-backend"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("kor_travel_docker_manager.main:app", host="0.0.0.0", port=12901, reload=True)
