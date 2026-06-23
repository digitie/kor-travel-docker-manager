import datetime
import logging
from typing import Any

from sqlalchemy import delete, select

from kor_travel_docker_manager import database
from kor_travel_docker_manager._time import utcnow
from kor_travel_docker_manager.database import get_db_session
from kor_travel_docker_manager.models import Base, Metric

logger = logging.getLogger(__name__)

class MetricsService:
    def init_db(self):
        """Initialize SQLite database tables using SQLAlchemy Metadata."""
        # database.engine 을 import 시점이 아니라 호출 시점에 참조해 (테스트의) 엔진 재할당을
        # 따르게 한다. 스키마 생성 실패는 치명적이므로 삼키지 않고 재발생시켜 fail-fast 한다.
        try:
            Base.metadata.create_all(bind=database.engine)
            logger.info("Metrics database initialized via SQLAlchemy.")
        except Exception:
            logger.exception("Failed to initialize metrics database")
            raise

    def save_metric(
        self, 
        container_id: str, 
        cpu_pct: float, 
        mem_usage: int, 
        mem_limit: int, 
        mem_pct: float, 
        io_read: int, 
        io_write: int
    ):
        """Save a new metric point to the database."""
        try:
            with get_db_session() as session:
                metric = Metric(
                    container_id=container_id,
                    cpu_pct=cpu_pct,
                    mem_usage=mem_usage,
                    mem_limit=mem_limit,
                    mem_pct=mem_pct,
                    io_read=io_read,
                    io_write=io_write
                )
                session.add(metric)
                session.commit()
        except Exception as e:
            logger.error(f"Failed to save ORM metric for {container_id}: {e}")

    def get_recent_metrics(self, container_id: str, hours: int = 1) -> list[dict[str, Any]]:
        """Retrieve metrics for the specified container over the last N hours."""
        try:
            # SQLAlchemy func.now() 혹은 SQLite CURRENT_TIMESTAMP는 UTC 기준이므로,
            # 파이썬 레벨에서 UTC cutoff를 계산하여 필터링합니다.
            cutoff = utcnow() - datetime.timedelta(hours=hours)

            with get_db_session() as session:
                stmt = (
                    select(Metric)
                    .where(Metric.container_id == container_id, Metric.timestamp >= cutoff)
                    .order_by(Metric.timestamp.asc())
                )
                results = session.scalars(stmt).all()
                
                metrics_list = []
                for row in results:
                    metrics_list.append({
                        # ISO 8601 포맷으로 반환하여 프론트엔드 파싱 편의 제공
                        "timestamp": row.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "cpu_pct": round(row.cpu_pct, 2),
                        "mem_usage": row.mem_usage,
                        "mem_limit": row.mem_limit,
                        "mem_pct": round(row.mem_pct, 2),
                        "io_read": row.io_read,
                        "io_write": row.io_write
                    })
                return metrics_list
        except Exception as e:
            logger.error(f"Failed to query recent ORM metrics for {container_id}: {e}")
            return []

    def cleanup_old_metrics(self, days: int = 30):
        """Delete metrics older than N days."""
        try:
            cutoff = utcnow() - datetime.timedelta(days=days)
            with get_db_session() as session:
                stmt = delete(Metric).where(Metric.timestamp < cutoff)
                result = session.execute(stmt)
                deleted_rows = result.rowcount
                session.commit()
                
                if deleted_rows > 0:
                    logger.info(f"Cleaned up {deleted_rows} old ORM metric records (older than {days} days).")
        except Exception as e:
            logger.error(f"Failed to cleanup old ORM metrics: {e}")

metrics_service = MetricsService()
