import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass

class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    container_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime, 
        default=func.now(), 
        index=True, 
        nullable=False
    )
    cpu_pct: Mapped[float] = mapped_column(Float, nullable=False)
    mem_usage: Mapped[int] = mapped_column(Integer, nullable=False)
    mem_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    mem_pct: Mapped[float] = mapped_column(Float, nullable=False)
    io_read: Mapped[int] = mapped_column(Integer, nullable=False)
    io_write: Mapped[int] = mapped_column(Integer, nullable=False)
