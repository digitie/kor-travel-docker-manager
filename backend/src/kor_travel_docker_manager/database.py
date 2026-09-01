import logging
import os
import sqlite3
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

current_dir = os.path.dirname(os.path.abspath(__file__))
# backend 루트 폴더 아래에 pinvi_metrics.db 생성되도록 경로 지정
DB_PATH = os.path.abspath(os.path.join(current_dir, "../../../../", "pinvi_metrics.db"))
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # SQLite를 멀티스레드 환경에서 안전하게 연동하기 위함
)


# GM-14: metrics collector(event loop 위)와 threadpool의 sync 라우트가 같은 SQLite
# 파일에 동시에 쓸 수 있어, 락 경합 시 각 연결의 기본 busy timeout이 곧 다른 쪽이
# 기다려야 하는 시간이 된다. pysqlite 기본값(5초)보다 짧게 못박아 한쪽이 오래
# 막히면 예외로 빨리 드러나게 한다 — PRAGMA는 연결 단위라 매 연결마다 다시 걸어야
# 한다.
@event.listens_for(engine, "connect")
def _set_sqlite_busy_timeout(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout = 3000")
    finally:
        cursor.close()


# GM-14 후속: WAL도 락 경합을 줄이지만, 이 DB 파일이 개발 환경에서 drvfs/9p
# 마운트(예: WSL에서 본 Windows 드라이브) 위에 있을 수 있어 WAL의 -shm/-wal
# sidecar 파일이 그런 파일시스템에서 오작동할 수 있다. 그래서 무조건 켜지
# 않고, 매 연결마다 "PRAGMA journal_mode=WAL"을 시도한 뒤 실제로 적용됐는지를
# "PRAGMA journal_mode" 재조회로 확인한다 — SQLite는 WAL 전환이 안 될 때 항상
# 예외를 던지는 게 아니라 조용히 이전 모드를 유지하는 경우가 있기 때문에(가장
# 흔한 예: ":memory:" DB는 요청과 무관하게 항상 "memory" 모드를 유지한다),
# 예외 캐치만으로는 부족하고 반드시 읽어온 값을 확인해야 한다. 실패하면
# 경고를 한 번만 남기고(매 연결마다 스팸하지 않음) 기본 rollback-journal
# 모드로 계속 진행한다 — busy_timeout은 이 성패와 무관하게 항상 걸린다.
_wal_fallback_warning_emitted = False


@event.listens_for(engine, "connect")
def _set_sqlite_wal_mode(dbapi_connection, connection_record) -> None:
    global _wal_fallback_warning_emitted
    cursor = dbapi_connection.cursor()
    try:
        journal_mode = ""
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA journal_mode")
            row = cursor.fetchone()
            if row is not None:
                journal_mode = str(row[0]).lower()
        except (OSError, sqlite3.OperationalError):
            journal_mode = ""

        if journal_mode != "wal" and not _wal_fallback_warning_emitted:
            logger.warning(
                "SQLite WAL 모드를 활성화하지 못했습니다(journal_mode=%s) — "
                "기본 rollback-journal 모드로 계속 진행합니다. busy_timeout은 "
                "그대로 적용됩니다.",
                journal_mode or "unknown",
            )
            _wal_fallback_warning_emitted = True
    finally:
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@contextmanager
def get_db_session():
    """Context manager for database sessions, useful in services and background tasks."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
