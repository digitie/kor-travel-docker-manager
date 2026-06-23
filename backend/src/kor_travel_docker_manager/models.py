import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
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


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=datetime.datetime.utcnow,
        index=True,
        nullable=False,
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime, index=True, nullable=False)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    client_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(255), nullable=True)


class LoginAuditEvent(Base):
    __tablename__ = "login_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audit_event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=datetime.datetime.utcnow,
        index=True,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    attempted_username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    next_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    session_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class PublicApiKey(Base):
    __tablename__ = "public_api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_api_key_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="active", index=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=datetime.datetime.utcnow,
        index=True,
        nullable=False,
    )
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
