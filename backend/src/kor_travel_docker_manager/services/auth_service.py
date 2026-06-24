import base64
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
from dataclasses import dataclass
from uuid import uuid4

from fastapi import HTTPException, Request, Response, WebSocket, status
from sqlalchemy import delete, select

from kor_travel_docker_manager import database
from kor_travel_docker_manager._time import utcnow
from kor_travel_docker_manager.models import AdminSession, Base, LoginAuditEvent

SESSION_COOKIE_NAME = "ktdm_admin_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
SESSION_AUDIENCE = "kor-travel-docker-manager-dashboard"
SESSION_VERSION = 1
PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 310_000
PASSWORD_SALT_BYTES = 16
SESSION_ID_BYTES = 32
SESSION_SECRET_MIN_LENGTH = 32
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 10 * 60
LOGIN_AUDIT_MAX_ROWS = 5000
# brute-force 카운트로 집계하는 로그인 실패 사유(rate_limited 응답 행은 제외).
LOGIN_FAILURE_REASONS = ("invalid_credentials", "misconfigured")
TRUSTED_PROXY_SECRET_HEADER = "x-ktdm-proxy-secret"
DEFAULT_FRONTEND_ORIGINS = ("http://localhost:12905", "http://127.0.0.1:12905")
DEFAULT_TRUSTED_PROXY_CIDRS = ("127.0.0.1/32", "::1/128")

_db_initialized = False
_db_engine_id: int | None = None


@dataclass(frozen=True, slots=True)
class AdminSessionContext:
    username: str
    session_id_hash: str
    expires_at: datetime.datetime


def admin_username() -> str:
    return os.environ.get("KTDM_ADMIN_USERNAME", "admin").strip() or "admin"


def hash_password_for_env(password: str) -> str:
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return ":".join(
        (
            PASSWORD_HASH_ALGORITHM,
            str(PASSWORD_HASH_ITERATIONS),
            _base64url_encode(salt),
            _base64url_encode(digest),
        )
    )


def verify_admin_password(username: str, password: str) -> str:
    expected_username = admin_username()
    password_hash = os.environ.get("KTDM_ADMIN_PASSWORD_HASH", "").strip()
    session_secret = _session_secret()
    if not password_hash or session_secret is None:
        return "misconfigured"
    # 사용자명이 불일치하더라도 PBKDF2 검증을 항상 수행한다. 사용자명 불일치 시 즉시 반환하면
    # 응답 시간이 사용자명 일치 여부에 의존해 username 열거 타이밍 사이드채널이 된다.
    username_ok = hmac.compare_digest(
        username.strip().encode("utf-8"), expected_username.encode("utf-8")
    )
    password_ok = _verify_password(password, password_hash)
    return "ok" if (username_ok and password_ok) else "invalid"


def require_frontend_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin or _normalize_origin(origin) not in allowed_frontend_origins():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="INVALID_ORIGIN")


def websocket_origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    return bool(origin and _normalize_origin(origin) in allowed_frontend_origins())


def allowed_frontend_origins() -> tuple[str, ...]:
    raw = os.environ.get("KTDM_FRONTEND_ORIGINS") or os.environ.get("KTDM_CORS_ALLOW_ORIGINS")
    if not raw or raw.strip() == "*":
        return DEFAULT_FRONTEND_ORIGINS
    origins = tuple(
        _normalize_origin(item)
        for item in raw.split(",")
        if item.strip() and item.strip() != "*"
    )
    return origins or DEFAULT_FRONTEND_ORIGINS


def create_admin_session(request: Request, response: Response, username: str) -> AdminSessionContext:
    _ensure_db()
    secret = _require_session_secret()
    now = utcnow()
    expires_at = now + datetime.timedelta(seconds=SESSION_TTL_SECONDS)
    session_id = _base64url_encode(secrets.token_bytes(SESSION_ID_BYTES))
    session_id_hash = _hash_value(session_id)
    payload = {
        "aud": SESSION_AUDIENCE,
        "exp": int(expires_at.timestamp()),
        "fp": _session_fingerprint(request, secret),
        "iat": int(now.timestamp()),
        "sid": session_id,
        "sub": username,
        "v": SESSION_VERSION,
    }
    payload_part = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    cookie_value = f"{payload_part}.{_sign(payload_part, secret)}"
    with database.get_db_session() as session:
        session.add(
            AdminSession(
                session_id_hash=session_id_hash,
                username=username,
                expires_at=expires_at,
                client_ip_hash=_client_ip_hash(request),
                user_agent_hash=_user_agent_hash(request),
                origin=_safe_header(request.headers.get("origin"), 255),
            )
        )
        session.commit()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_value,
        httponly=True,
        max_age=SESSION_TTL_SECONDS,
        path="/",
        samesite="strict",
        secure=_is_https(request),
    )
    return AdminSessionContext(username=username, session_id_hash=session_id_hash, expires_at=expires_at)


def require_admin_session(request: Request) -> AdminSessionContext:
    require_frontend_origin(request)
    context = validate_session_cookie(request.cookies.get(SESSION_COOKIE_NAME), request)
    if context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="AUTH_REQUIRED")
    return context


def validate_session_cookie(value: str | None, request: Request | WebSocket) -> AdminSessionContext | None:
    _ensure_db()
    secret = _session_secret()
    if not value or secret is None:
        return None
    payload = _decode_session_cookie(value, secret)
    if payload is None:
        return None
    now = utcnow()
    now_epoch = int(now.timestamp())
    if payload.get("aud") != SESSION_AUDIENCE or payload.get("v") != SESSION_VERSION:
        return None
    if payload.get("sub") != admin_username():
        return None
    if not isinstance(payload.get("exp"), int) or payload["exp"] <= now_epoch:
        return None
    if not isinstance(payload.get("iat"), int) or payload["iat"] > now_epoch + 60:
        return None
    if not isinstance(payload.get("sid"), str) or not payload["sid"]:
        return None
    if payload.get("fp") != _session_fingerprint(request, secret):
        return None
    session_id_hash = _hash_value(payload["sid"])
    with database.get_db_session() as session:
        row = session.scalars(
            select(AdminSession).where(AdminSession.session_id_hash == session_id_hash)
        ).first()
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            return None
        return AdminSessionContext(
            username=row.username,
            session_id_hash=row.session_id_hash,
            expires_at=row.expires_at,
        )


def revoke_admin_session(value: str | None, request: Request) -> str | None:
    _ensure_db()
    secret = _session_secret()
    if not value or secret is None:
        return None
    payload = _decode_session_cookie(value, secret)
    if payload is None or not isinstance(payload.get("sid"), str):
        return None
    session_id_hash = _hash_value(payload["sid"])
    with database.get_db_session() as session:
        row = session.scalars(
            select(AdminSession).where(AdminSession.session_id_hash == session_id_hash)
        ).first()
        if row is not None and row.revoked_at is None:
            row.revoked_at = utcnow()
            session.commit()
    return session_id_hash


def expire_admin_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="strict",
        secure=_is_https(request),
    )


def check_login_rate_limit(request: Request) -> int | None:
    """최근 로그인 실패 횟수를 감사 로그(durable, 워커 공유)에서 계산해 제한한다.

    인메모리 카운터와 달리 백엔드 재시작·다중 워커에서도 유지된다. 동일 client IP 해시의
    마지막 로그인 성공 이후 실패만 집계해, 성공 시 카운터가 리셋되는 효과를 보존한다.
    실패 행(invalid_credentials/misconfigured)은 호출부의 record_login_audit_event 가 남기므로
    별도 카운터 기록이 필요 없다.
    """
    _ensure_db()
    client_hash = _client_ip_hash(request)
    if client_hash is None:
        return None
    now = utcnow()
    window_start = now - datetime.timedelta(seconds=LOGIN_FAILURE_WINDOW_SECONDS)
    with database.get_db_session() as session:
        last_success = session.scalars(
            select(LoginAuditEvent.occurred_at)
            .where(
                LoginAuditEvent.client_ip_hash == client_hash,
                LoginAuditEvent.event_type == "login",
                LoginAuditEvent.outcome == "succeeded",
                LoginAuditEvent.occurred_at >= window_start,
            )
            .order_by(LoginAuditEvent.occurred_at.desc())
            .limit(1)
        ).first()
        effective_start = max(window_start, last_success) if last_success else window_start
        failure_times = session.scalars(
            select(LoginAuditEvent.occurred_at)
            .where(
                LoginAuditEvent.client_ip_hash == client_hash,
                LoginAuditEvent.event_type == "login",
                LoginAuditEvent.reason.in_(LOGIN_FAILURE_REASONS),
                LoginAuditEvent.occurred_at > effective_start,
            )
            .order_by(LoginAuditEvent.occurred_at.asc())
        ).all()
    if len(failure_times) < LOGIN_FAILURE_LIMIT:
        return None
    oldest = failure_times[0]
    retry_after = int(
        (oldest + datetime.timedelta(seconds=LOGIN_FAILURE_WINDOW_SECONDS) - now).total_seconds()
    )
    return max(retry_after, 1)


def record_login_audit_event(
    request: Request,
    *,
    event_type: str,
    outcome: str,
    attempted_username: str | None = None,
    reason: str | None = None,
    next_path: str | None = None,
    session_id_hash: str | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    _ensure_db()
    with database.get_db_session() as session:
        session.add(
            LoginAuditEvent(
                audit_event_id=str(uuid4()),
                event_type=event_type,
                outcome=outcome,
                attempted_username=_safe_value(attempted_username, 120),
                reason=_safe_value(reason, 80),
                next_path=_safe_value(next_path, 500),
                client_ip_hash=_client_ip_hash(request),
                user_agent_hash=_user_agent_hash(request),
                origin=_safe_header(request.headers.get("origin"), 255),
                request_path=_safe_value(str(request.url.path), 500),
                session_id_hash=session_id_hash,
                detail_json=json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
            )
        )
        session.commit()
        _prune_login_audit_events(session)


def _login_audit_max_rows() -> int:
    raw = os.environ.get("KTDM_LOGIN_AUDIT_MAX_ROWS", str(LOGIN_AUDIT_MAX_ROWS))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return LOGIN_AUDIT_MAX_ROWS


def _prune_login_audit_events(session) -> None:
    """감사 로그 테이블이 무한 증식하지 않도록 보존 상한 초과분(오래된 행)을 정리한다.

    로그아웃/오설정 로그인 등 미인증 경로도 감사 행을 남기므로, 상한이 없으면 테이블이
    무제한으로 커질 수 있다. ``KTDM_LOGIN_AUDIT_MAX_ROWS`` 로 상한을 조정할 수 있다(<=0 이면 비활성).
    """
    max_rows = _login_audit_max_rows()
    if max_rows <= 0:
        return
    threshold_id = session.scalars(
        select(LoginAuditEvent.id)
        .order_by(LoginAuditEvent.id.desc())
        .offset(max_rows - 1)
        .limit(1)
    ).first()
    if threshold_id is None:
        return
    session.execute(delete(LoginAuditEvent).where(LoginAuditEvent.id < threshold_id))
    session.commit()


def list_login_audit_events(
    *, limit: int = 100, event_type: str | None = None, outcome: str | None = None
) -> list[dict[str, object]]:
    _ensure_db()
    with database.get_db_session() as session:
        stmt = select(LoginAuditEvent).order_by(LoginAuditEvent.occurred_at.desc()).limit(limit)
        if event_type:
            stmt = stmt.where(LoginAuditEvent.event_type == event_type)
        if outcome:
            stmt = stmt.where(LoginAuditEvent.outcome == outcome)
        rows = session.scalars(stmt).all()
        return [
            {
                "audit_event_id": row.audit_event_id,
                "occurred_at": row.occurred_at.isoformat(),
                "event_type": row.event_type,
                "outcome": row.outcome,
                "attempted_username": row.attempted_username,
                "reason": row.reason,
                "next_path": row.next_path,
                "client_ip_hash": row.client_ip_hash,
                "user_agent_hash": row.user_agent_hash,
                "origin": row.origin,
                "request_path": row.request_path,
                "session_id_hash": row.session_id_hash,
                "detail": json.loads(row.detail_json or "{}"),
            }
            for row in rows
        ]


def sanitize_local_path(value: str | None, fallback: str = "/") -> str:
    if not value:
        return fallback
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return fallback
    return value[:500]


def _ensure_db() -> None:
    global _db_engine_id, _db_initialized
    engine_id = id(database.engine)
    if _db_initialized and _db_engine_id == engine_id:
        return
    Base.metadata.create_all(bind=database.engine)
    _db_initialized = True
    _db_engine_id = engine_id


def _verify_password(password: str, encoded: str) -> bool:
    delimiter = "$" if "$" in encoded else ":"
    parts = encoded.split(delimiter)
    if len(parts) != 4 or parts[0] != PASSWORD_HASH_ALGORITHM:
        return False
    try:
        iterations = int(parts[1])
        salt = _base64url_decode(parts[2])
        expected = _base64url_decode(parts[3])
    except (ValueError, TypeError):
        return False
    if iterations < 100_000:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _decode_session_cookie(value: str, secret: str) -> dict[str, object] | None:
    parts = value.split(".")
    if len(parts) != 2:
        return None
    payload_part, signature = parts
    if not hmac.compare_digest(_sign(payload_part, secret), signature):
        return None
    try:
        payload = json.loads(_base64url_decode(payload_part).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _session_secret() -> str | None:
    value = os.environ.get("KTDM_SESSION_SECRET", "").strip()
    return value if len(value) >= SESSION_SECRET_MIN_LENGTH else None


def _require_session_secret() -> str:
    secret = _session_secret()
    if secret is None:
        raise HTTPException(status_code=503, detail="AUTH_MISCONFIGURED")
    return secret


def _session_fingerprint(request: Request | WebSocket, secret: str) -> str:
    user_agent = (request.headers.get("user-agent") or "")[:300]
    return _sign(f"fingerprint:{user_agent}", secret)


def _sign(value: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()
    return _base64url_encode(digest)


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _client_ip_hash(request: Request) -> str | None:
    client_ip = _client_ip(request)
    return _hash_value(client_ip) if client_ip else None


def _user_agent_hash(request: Request) -> str | None:
    user_agent = request.headers.get("user-agent")
    return _hash_value(user_agent[:500]) if user_agent else None


def _client_ip(request: Request) -> str | None:
    if _request_from_trusted_proxy(request):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            forwarded_parts = [part.strip() for part in forwarded.split(",") if part.strip()]
            if forwarded_parts:
                return forwarded_parts[-1][:128]
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()[:128]
    return request.client.host if request.client else None


def _normalize_origin(value: str) -> str:
    return value.strip().rstrip("/").lower()


def _safe_header(value: str | None, max_length: int) -> str | None:
    return _safe_value(value.strip() if value else None, max_length)


def _safe_value(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed[:max_length] if trimmed else None


def _is_https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto") if _request_from_trusted_proxy(request) else None
    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"
    if request.url.scheme == "https":
        return True
    # TLS 종단 프록시(라우터의 HAProxy 등)가 신뢰되는 X-Forwarded-Proto를 주입하지 않아 백엔드에는
    # http로 보여도, 브라우저 Origin이 설정된 https 공개 origin(allowed_frontend_origins)과 일치하면
    # https 요청으로 간주해 세션 쿠키에 Secure 플래그를 부여한다. 브라우저가 보낸 Origin을
    # 화이트리스트와 대조하므로 위조 위험이 없고, LAN http origin은 https가 아니라 영향이 없다.
    origin = request.headers.get("origin")
    if origin:
        normalized = _normalize_origin(origin)
        if normalized.startswith("https://") and normalized in allowed_frontend_origins():
            return True
    return False


def _request_from_trusted_proxy(request: Request) -> bool:
    if request.client is None:
        return False
    try:
        client_ip = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    if not any(client_ip in network for network in _trusted_proxy_networks()):
        return False
    # KTDM_TRUSTED_PROXY_SECRET 가 설정되어 있으면, 신뢰 CIDR(기본 loopback) 매칭만으로는 부족하고
    # 리버스 프록시가 설정한 시크릿 헤더가 일치해야 X-Forwarded-* 를 신뢰한다. host 네트워크의 로컬
    # 프로세스가 loopback 출처로 X-Forwarded-* 를 위조하는 것을 차단한다(미설정 시 기존 동작 유지).
    secret = _trusted_proxy_secret()
    if secret is not None:
        provided = request.headers.get(TRUSTED_PROXY_SECRET_HEADER, "")
        return hmac.compare_digest(provided.encode("utf-8"), secret.encode("utf-8"))
    return True


def _trusted_proxy_secret() -> str | None:
    value = os.environ.get("KTDM_TRUSTED_PROXY_SECRET", "").strip()
    return value or None


def _trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    raw = os.environ.get("KTDM_TRUSTED_PROXY_CIDRS", ",".join(DEFAULT_TRUSTED_PROXY_CIDRS))
    networks: list[ipaddress._BaseNetwork] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    if not networks:
        return tuple(ipaddress.ip_network(item) for item in DEFAULT_TRUSTED_PROXY_CIDRS)
    return tuple(networks)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
