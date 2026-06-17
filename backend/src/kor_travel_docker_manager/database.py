import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

current_dir = os.path.dirname(os.path.abspath(__file__))
# backend 루트 폴더 아래에 pinvi_metrics.db 생성되도록 경로 지정
DB_PATH = os.path.abspath(os.path.join(current_dir, "../../../../", "pinvi_metrics.db"))
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False}  # SQLite를 멀티스레드 환경에서 안전하게 연동하기 위함
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@contextmanager
def get_db_session():
    """Context manager for database sessions, useful in services and background tasks."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
