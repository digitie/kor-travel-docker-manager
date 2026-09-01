import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

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
# 한다. WAL도 경합을 줄이지만 이 DB 파일이 개발 환경에서 drvfs/9p 마운트 위에
# 있을 수 있어(WAL의 shm/mmap이 그런 파일시스템에서 실패할 수 있음) 별도 검증
# 없이 여기서 켜지 않는다(추적: docs/tasks.md).
@event.listens_for(engine, "connect")
def _set_sqlite_busy_timeout(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout = 3000")
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
