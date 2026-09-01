import asyncio
import hashlib
import os
import time
import uuid
from unittest.mock import Mock, patch

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import kor_travel_docker_manager.database
from kor_travel_docker_manager.services.auth_service import hash_password_for_env
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.public_api_key_service import public_api_key_is_valid
from kor_travel_docker_manager.services.standalone_backup import (
    BACKUP_ROLES,
    StandaloneBackupError,
)

FRONTEND_ORIGIN = "http://localhost:12905"
os.environ["KTDM_ADMIN_USERNAME"] = "admin"
TEST_ADMIN_PASSWORD = "manager-test-password-only"
os.environ["KTDM_ADMIN_PASSWORD_HASH"] = hash_password_for_env(TEST_ADMIN_PASSWORD)
os.environ["KTDM_SESSION_SECRET"] = "test-session-secret-minimum-32-bytes-value"
os.environ["KTDM_FRONTEND_ORIGINS"] = FRONTEND_ORIGIN

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
kor_travel_docker_manager.database.engine = test_engine
kor_travel_docker_manager.database.SessionLocal = TestSessionLocal

from kor_travel_docker_manager.api.websocket import status_manager
from kor_travel_docker_manager.main import app

client = TestClient(app)
client.headers.update({"Origin": FRONTEND_ORIGIN})


def login_client():
    client.cookies.clear()
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD, "next": "/"},
    )
    assert login_response.status_code == 200


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "kor-travel-docker-manager-backend"}


# --- GM-16: 요청 상관관계 ID -----------------------------------------------------


def test_every_response_carries_a_fresh_x_request_id_header():
    """GM-16: 성공 응답에도 오류 응답에도 항상 붙는다 — 미들웨어가
    call_next() 뒤에 헤더를 다는데, call_next는 등록된 예외 핸들러를 거친
    뒤의 Response를 돌려주므로 두 경로 모두 커버된다."""

    success = client.get("/health")
    assert success.status_code == 200
    uuid.UUID(success.headers["x-request-id"])  # 유효한 uuid4 형태인지

    login_client()
    not_found = client.get("/api/v1/backups/geo/jobs/does-not-exist")
    assert not_found.status_code == 404
    uuid.UUID(not_found.headers["x-request-id"])

    # 요청마다 새로 발급된다 — 두 응답이 같은 값을 재사용하지 않는다.
    assert success.headers["x-request-id"] != not_found.headers["x-request-id"]


def test_client_supplied_request_id_is_ignored_not_trusted():
    """서버가 신뢰하지 않고 항상 새로 발급한다 — 클라이언트가 보낸 값을
    그대로 돌려주면 로그 검색 키를 외부에서 위조(로그 스푸핑)할 수 있다."""

    response = client.get(
        "/health", headers={"X-Request-ID": "attacker-supplied-value"}
    )
    assert response.headers["x-request-id"] != "attacker-supplied-value"
    uuid.UUID(response.headers["x-request-id"])


@patch("kor_travel_docker_manager.api.routes.compose_service")
def test_error_response_request_id_matches_the_response_header(mock_compose_service):
    """GM-12 예외 핸들러가 본문에 심는 request_id가 미들웨어가 헤더에 심는
    것과 같은 값이어야 "UI 오류 → 로그 라인"이 실제로 한 키로 조인된다."""

    login_client()
    mock_compose_service.ensure_target.side_effect = DeploymentContractError(
        "C6c production preflight failed"
    )

    response = client.post("/api/v1/targets/main/ensure", json={"recreate": True})

    assert response.status_code == 409
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_admin_api_requires_frontend_origin_and_session():
    # Origin 헤더가 없으면 origin 가드(require_frontend_origin)에서 403으로 단락된다.
    unauthenticated = TestClient(app)
    response = unauthenticated.get("/api/v1/containers")
    assert response.status_code == 403


def test_admin_api_with_valid_origin_no_session_returns_401():
    # 유효한 Origin이지만 세션 쿠키가 없으면 origin 가드를 통과한 뒤 401(AUTH_REQUIRED)이어야 한다.
    client.cookies.clear()
    response = client.get("/api/v1/containers")
    assert response.status_code == 401


def test_auth_me_returns_username_when_authenticated_and_401_otherwise():
    client.cookies.clear()
    assert client.get("/api/v1/auth/me").status_code == 401

    login_client()
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["authenticated"] is True
    assert body["username"] == "admin"


def test_logout_revokes_session_and_blocks_cookie_reuse():
    login_client()
    cookie = client.cookies.get("ktdm_admin_session")
    assert cookie

    assert client.post("/api/v1/auth/logout").status_code == 200

    # 폐기(revoked_at)된 세션 쿠키를 재사용하면 401이어야 한다.
    client.cookies.set("ktdm_admin_session", cookie, domain="testserver")
    reuse = client.get("/api/v1/auth/me")
    assert reuse.status_code == 401
    client.cookies.clear()


def test_tampered_session_cookie_rejected():
    login_client()
    cookie = client.cookies.get("ktdm_admin_session")
    assert cookie
    # 서명 마지막 1자를 변조하면 HMAC 불일치로 거부(401)되어야 한다.
    tampered = cookie[:-1] + ("A" if cookie[-1] != "A" else "B")
    client.cookies.clear()
    client.cookies.set("ktdm_admin_session", tampered, domain="testserver")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    client.cookies.clear()


def test_login_rejects_invalid_password_and_records_audit_event():
    login_client()
    response = client.post(
        "/api/v1/auth/login",
        headers={"x-forwarded-for": "203.0.113.7"},
        json={"username": "admin", "password": "wrong", "next": "/"},
    )
    assert response.status_code == 401
    # 구조화된 { code, message } 봉투여야 한다 — bare 문자열이면 프런트가 CODE_MESSAGES를
    # 조회하지 못하고 이 토큰을 그대로 사용자에게 노출한다.
    body = response.json()["detail"]
    assert body["code"] == "INVALID_CREDENTIALS"
    assert isinstance(body["message"], str) and body["message"]
    assert body["message"] != "INVALID_CREDENTIALS"

    events = client.get("/api/v1/admin/login-audit-events?event_type=login").json()
    # 기본 TestClient(client.host="testclient")는 신뢰 프록시가 아니므로 X-Forwarded-For가
    # 무시되어야 한다. 저장된 client_ip_hash는 forwarded IP 해시가 아니라 client.host 해시여야 한다.
    untrusted_hash = hashlib.sha256(b"testclient").hexdigest()
    forwarded_hash = hashlib.sha256(b"203.0.113.7").hexdigest()
    denied = next(
        event
        for event in events
        if event["outcome"] == "denied"
        and event["reason"] == "invalid_credentials"
        and event["client_ip_hash"] == untrusted_hash
    )
    assert denied["client_ip_hash"] != forwarded_hash


@patch("kor_travel_docker_manager.api.auth.verify_admin_password")
def test_login_misconfigured_returns_structured_error_envelope(mock_verify):
    """AUTH_MISCONFIGURED도 bare 문자열이 아니라 { code, message } 봉투여야 한다.

    /api/v1/auth/login 자체는 세션을 요구하지 않는 로그인 엔드포인트이므로
    login_client() 없이 미인증 상태로 바로 호출한다."""
    client.cookies.clear()
    mock_verify.return_value = "misconfigured"

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "whatever", "next": "/"},
    )

    assert response.status_code == 503
    body = response.json()["detail"]
    assert body["code"] == "AUTH_MISCONFIGURED"
    assert isinstance(body["message"], str) and body["message"]
    assert body["message"] != "AUTH_MISCONFIGURED"


def test_client_ip_trusts_forwarded_only_from_trusted_proxy():
    # 신뢰 프록시 판정/ X-Forwarded-For 처리를 실제 코드 경로로 직접 검증한다.
    from starlette.requests import Request

    from kor_travel_docker_manager.services.auth_service import _client_ip

    def make_request(client_host: str, xff: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": [(b"x-forwarded-for", xff.encode("utf-8"))],
                "client": (client_host, 12345),
                "scheme": "http",
                "server": ("testserver", 80),
            }
        )

    # 신뢰 프록시(loopback)에서는 X-Forwarded-For의 가장 오른쪽 홉을 사용한다.
    trusted = make_request("127.0.0.1", "198.51.100.2, 203.0.113.9")
    assert _client_ip(trusted) == "203.0.113.9"

    # 신뢰되지 않은 클라이언트의 X-Forwarded-For는 무시하고 실제 client.host를 사용한다.
    untrusted = make_request("203.0.113.50", "203.0.113.9")
    assert _client_ip(untrusted) == "203.0.113.50"


def test_trusted_proxy_requires_secret_header_when_configured(monkeypatch):
    # KTDM_TRUSTED_PROXY_SECRET 설정 시, loopback이라도 일치하는 시크릿 헤더가 있어야 XFF를 신뢰한다.
    from starlette.requests import Request

    from kor_travel_docker_manager.services.auth_service import _client_ip

    def make_request(headers):
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": headers,
                "client": ("127.0.0.1", 40000),
                "scheme": "http",
                "server": ("testserver", 80),
            }
        )

    monkeypatch.setenv("KTDM_TRUSTED_PROXY_SECRET", "s3cr3t-proxy-value")
    # 시크릿 헤더 없음 → XFF 무시, 실제 loopback client.host 사용
    no_secret = make_request([(b"x-forwarded-for", b"203.0.113.10")])
    assert _client_ip(no_secret) == "127.0.0.1"
    # 올바른 시크릿 헤더 → XFF 신뢰
    with_secret = make_request(
        [
            (b"x-forwarded-for", b"203.0.113.10"),
            (b"x-ktdm-proxy-secret", b"s3cr3t-proxy-value"),
        ]
    )
    assert _client_ip(with_secret) == "203.0.113.10"


def test_login_rate_limit_durable_via_audit_log():
    # 인메모리 카운터가 아니라 감사 로그에서 실패를 집계하므로 재시작/멀티워커에서도 유지된다.
    from starlette.requests import Request

    import kor_travel_docker_manager.database as db
    from kor_travel_docker_manager._time import utcnow
    from kor_travel_docker_manager.models import LoginAuditEvent
    from kor_travel_docker_manager.services.auth_service import (
        LOGIN_FAILURE_LIMIT,
        check_login_rate_limit,
    )

    ip = "198.51.100.123"
    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": (ip, 55555),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )
    client_hash = hashlib.sha256(ip.encode("utf-8")).hexdigest()

    with db.get_db_session() as s:
        s.query(LoginAuditEvent).filter(LoginAuditEvent.client_ip_hash == client_hash).delete()
        s.commit()

    assert check_login_rate_limit(req) is None

    with db.get_db_session() as s:
        for i in range(LOGIN_FAILURE_LIMIT):
            s.add(
                LoginAuditEvent(
                    audit_event_id=f"rl-fail-{i}",
                    event_type="login",
                    outcome="denied",
                    reason="invalid_credentials",
                    client_ip_hash=client_hash,
                    occurred_at=utcnow(),
                )
            )
        s.commit()

    assert check_login_rate_limit(req) is not None

    # 성공 이벤트 이후에는 카운터가 리셋되어 다시 허용되어야 한다.
    with db.get_db_session() as s:
        s.add(
            LoginAuditEvent(
                audit_event_id="rl-success",
                event_type="login",
                outcome="succeeded",
                reason="authenticated",
                client_ip_hash=client_hash,
                occurred_at=utcnow(),
            )
        )
        s.commit()

    assert check_login_rate_limit(req) is None


def test_ws_status_requires_session():
    """미인증 거절은 accept-then-close다. handshake는 성립하고 첫 수신에서 4401이 온다.

    브라우저가 실제로 보는 지점과 같은 층위를 측정한다. 계약(accept → close 순서) 자체는
    tests/test_ws_contract.py의 ASGI 메시지 시퀀스 회귀가 고정한다.
    """
    client.cookies.clear()
    with client.websocket_connect(
        "/api/v1/ws/status", headers={"Origin": FRONTEND_ORIGIN}
    ) as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 4401


@patch("kor_travel_docker_manager.api.websocket.docker_service")
def test_ws_status_accepts_authenticated_session(mock_docker_service):
    mock_docker_service.get_containers_status.return_value = []
    login_client()
    baseline = len(status_manager.active_connections)
    with client.websocket_connect(
        "/api/v1/ws/status", headers={"Origin": FRONTEND_ORIGIN}
    ) as ws:
        message = ws.receive_json()
        assert message["type"] == "status"
        assert message["containers"] == []
        assert len(status_manager.active_connections) == baseline + 1
    assert len(status_manager.active_connections) == baseline


def test_public_api_key_lifecycle(monkeypatch):
    login_client()
    created = client.post("/api/v1/admin/public-api-keys", json={"label": "테스트 키"})
    assert created.status_code == 200
    payload = created.json()
    assert len(payload["key"]) == 32
    assert payload["item"]["key_hint"] == payload["key"][-6:]
    assert public_api_key_is_valid(payload["key"]) is True

    listed = client.get("/api/v1/admin/public-api-keys")
    assert listed.status_code == 200
    assert listed.json()[0]["public_api_key_id"] == payload["item"]["public_api_key_id"]

    revoked = client.delete(
        f"/api/v1/admin/public-api-keys/{payload['item']['public_api_key_id']}"
    )
    assert revoked.status_code == 200
    assert revoked.json()["state"] == "revoked"
    assert public_api_key_is_valid(payload["key"]) is False

    monkeypatch.setenv("KOR_TRAVEL_GEO_VWORLD_API_KEY", payload["key"])
    monkeypatch.setenv("NEXT_PUBLIC_VWORLD_API_KEY", payload["key"])
    assert public_api_key_is_valid(payload["key"]) is False

    missing = client.delete("/api/v1/admin/public-api-keys/not-a-uuid")
    assert missing.status_code == 404
    # 구조화된 { code, message } 봉투여야 한다 — bare 문자열이면 프런트가 CODE_MESSAGES를
    # 조회하지 못하고 이 토큰을 그대로 사용자에게 노출한다.
    missing_body = missing.json()["detail"]
    assert missing_body["code"] == "PUBLIC_API_KEY_NOT_FOUND"
    assert isinstance(missing_body["message"], str) and missing_body["message"]
    assert missing_body["message"] != "PUBLIC_API_KEY_NOT_FOUND"


def test_metrics_route_is_open_by_default(monkeypatch):
    """GM-19: KTDM_METRICS_REQUIRE_KEY 미설정(기본값)이면 인증 없이 접근 가능해야
    한다 — 기존 Prometheus scrape(config/prometheus/prometheus.yml)를 깨지 않는다."""
    monkeypatch.delenv("KTDM_METRICS_REQUIRE_KEY", raising=False)
    client.cookies.clear()

    response = client.get("/metrics")

    assert response.status_code == 200


def test_metrics_route_requires_key_when_opted_in(monkeypatch):
    """GM-19: KTDM_METRICS_REQUIRE_KEY=1이면 세션도 키도 없는 요청은 거부돼야
    한다 — require_public_api_key에 첫 실제 소비처가 생긴 것의 회귀 검증."""
    monkeypatch.setenv("KTDM_METRICS_REQUIRE_KEY", "1")
    client.cookies.clear()

    response = client.get("/metrics")

    assert response.status_code == 401


def test_metrics_route_accepts_a_valid_key_when_opted_in(monkeypatch):
    login_client()
    created = client.post("/api/v1/admin/public-api-keys", json={"label": "metrics test"})
    assert created.status_code == 200
    key = created.json()["key"]
    client.cookies.clear()

    monkeypatch.setenv("KTDM_METRICS_REQUIRE_KEY", "1")
    response = client.get("/metrics", params={"key": key})

    assert response.status_code == 200


def test_metrics_route_accepts_an_admin_session_when_opted_in(monkeypatch):
    login_client()
    monkeypatch.setenv("KTDM_METRICS_REQUIRE_KEY", "1")

    response = client.get("/metrics")

    assert response.status_code == 200


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_list_containers(mock_docker_service):
    login_client()
    # Setup mock status list
    mock_docker_service.get_containers_status.return_value = [
        {
            "id": "kor-travel-geo-postgresql",
            "name": "kor-travel-geo-postgres",
            "status": "running",
            "state": "running",
            "ports": ["5432:5432"],
        },
        {
            "id": "rustfs",
            "name": "kor-travel-rustfs",
            "status": "exited",
            "state": "exited",
            "ports": [],
        },
    ]

    # Target versioned route v1
    response = client.get("/api/v1/containers")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["id"] == "kor-travel-geo-postgresql"
    assert data[0]["status"] == "running"
    assert data[0]["ports"] == ["5432:5432"]
    assert data[1]["id"] == "rustfs"
    assert data[1]["status"] == "exited"


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_control_container_success(mock_docker_service):
    login_client()
    mock_docker_service.control_container.return_value = {
        "success": True,
        "message": "Successfully stopped kor-travel-geo-postgres.",
    }

    # Target versioned route v1
    response = client.post(
        "/api/v1/containers/kor-travel-geo-postgresql/action", json={"action": "stop"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Successfully stopped kor-travel-geo-postgres.",
    }
    mock_docker_service.control_container.assert_called_once_with("kor-travel-geo-postgresql", "stop")


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_control_container_invalid_action(mock_docker_service):
    login_client()
    # Target versioned route v1
    response = client.post(
        "/api/v1/containers/kor-travel-geo-postgresql/action", json={"action": "invalid"}
    )
    assert response.status_code == 400
    assert "Action must be start, stop, or restart" in response.json()["detail"]


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_get_container_logs_success(mock_docker_service):
    login_client()
    mock_docker_service.get_container_logs.return_value = {
        "success": True,
        "logs": "Sample logs content",
    }

    # Target versioned route v1
    response = client.get("/api/v1/containers/kor-travel-geo-postgresql/logs")
    assert response.status_code == 200
    assert response.json() == {"logs": "Sample logs content"}
    mock_docker_service.get_container_logs.assert_called_once_with(
        "kor-travel-geo-postgresql", tail=100
    )


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_update_container_config_success(mock_docker_service):
    login_client()
    mock_docker_service.update_container_config.return_value = {
        "success": True,
        "message": "Successfully updated config and recreated kor-travel-geo-postgres.",
    }

    # Target versioned route v1
    response = client.post(
        "/api/v1/containers/kor-travel-geo-postgresql/config",
        json={
            "ports": ["5432:5432"],
            "env": {"POSTGRES_PASSWORD": "${KOR_TRAVEL_GEO_POSTGRES_PASSWORD:-addr}"},
            "volumes": ["${KOR_TRAVEL_GEO_PGDATA:-/tmp/pgdata}:/var/lib/postgresql/data"],
            "networks": ["default"],
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Successfully updated config and recreated kor-travel-geo-postgres.",
    }
    mock_docker_service.update_container_config.assert_called_once_with(
        "kor-travel-geo-postgresql",
        ["5432:5432"],
        {"POSTGRES_PASSWORD": "${KOR_TRAVEL_GEO_POSTGRES_PASSWORD:-addr}"},
        ["${KOR_TRAVEL_GEO_PGDATA:-/tmp/pgdata}:/var/lib/postgresql/data"],
        ["default"],
    )


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_update_container_config_failure_preserves_restoration_detail(
    mock_docker_service,
):
    login_client()
    restoration = {
        "config_restored": True,
        "runtime_restored": False,
        "command": ["docker", "compose"],
    }
    mock_docker_service.update_container_config.return_value = {
        "success": False,
        "error": "candidate recreate failed",
        "restoration": restoration,
    }

    response = client.post(
        "/api/v1/containers/rustfs/config",
        json={
            "ports": ["12101:12101"],
            "env": {},
            "volumes": ["rustfs:/data"],
            "networks": [],
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "message": "candidate recreate failed",
        "restoration": restoration,
    }


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_update_container_candidate_rejection_returns_typed_unchanged_detail(
    mock_docker_service,
):
    login_client()
    mock_docker_service.update_container_config.side_effect = (
        ComposeCandidateContractError(
            "compose candidate rustfs bind source exposes a manager file"
        )
    )

    response = client.post(
        "/api/v1/containers/rustfs/config",
        json={
            "ports": [],
            "env": {},
            "volumes": ["./.env:/run/manager.env:ro"],
            "networks": [],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "COMPOSE_CANDIDATE_PROTECTED_REFERENCE",
        "message": "compose candidate rustfs bind source exposes a manager file",
        "stage": "candidate_validation",
        "mutation_applied": False,
    }
    mock_docker_service.update_container_config.assert_called_once_with(
        "rustfs",
        [],
        {},
        ["./.env:/run/manager.env:ro"],
        [],
    )


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_update_container_post_mutation_drift_returns_typed_recovery_detail(
    mock_docker_service,
) -> None:
    login_client()
    original_error = ComposeCandidateContractError(
        "compose resolved volume graph changed during the request"
    )
    restoration = {
        "config_restored": True,
        "runtime_restored": False,
        "error": "persisted runtime recovery failed",
    }
    mock_docker_service.update_container_config.side_effect = (
        ComposePostMutationContractError(
            original_error,
            recovery_attempted=True,
            recovery_succeeded=False,
            recovery_error="persisted runtime recovery failed",
            restoration=restoration,
        )
    )

    response = client.post(
        "/api/v1/containers/rustfs/config",
        json={"ports": [], "env": {}, "volumes": [], "networks": []},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "COMPOSE_POST_MUTATION_CONTRACT_FAILURE",
        "message": str(original_error),
        "stage": "post_mutation_recovery",
        "mutation_applied": True,
        "original_error": {
            "code": "COMPOSE_CANDIDATE_PROTECTED_REFERENCE",
            "message": str(original_error),
        },
        "recovery_attempted": True,
        "recovery_succeeded": False,
        "recovery_error": "persisted runtime recovery failed",
        "restoration": restoration,
    }


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_update_container_preflight_restore_failure_is_typed_durable_mutation(
    mock_docker_service,
) -> None:
    login_client()
    original_error = ComposeCandidateContractError(
        "compose candidate source changed during the config request"
    )
    restoration = {
        "config_restored": False,
        "runtime_restored": False,
        "runtime_recovery_attempted": False,
        "durable_config_mutation": True,
        "error": "atomic compose restore failed",
    }
    mock_docker_service.update_container_config.side_effect = (
        ComposePostMutationContractError(
            original_error,
            recovery_attempted=True,
            recovery_succeeded=False,
            recovery_error="atomic compose restore failed",
            restoration=restoration,
        )
    )

    response = client.post(
        "/api/v1/containers/rustfs/config",
        json={"ports": [], "env": {}, "volumes": [], "networks": []},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "COMPOSE_POST_MUTATION_CONTRACT_FAILURE",
        "message": str(original_error),
        "stage": "post_mutation_recovery",
        "mutation_applied": True,
        "original_error": {
            "code": "COMPOSE_CANDIDATE_PROTECTED_REFERENCE",
            "message": str(original_error),
        },
        "recovery_attempted": True,
        "recovery_succeeded": False,
        "recovery_error": "atomic compose restore failed",
        "restoration": restoration,
    }


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_rustfs_missing_bind_rejection_returns_typed_no_mutation_detail(
    mock_docker_service,
):
    login_client()
    mock_docker_service.update_container_config.side_effect = (
        ComposeCandidateContractError(
            "compose candidate rustfs bind source does not exist"
        )
    )

    response = client.post(
        "/api/v1/containers/rustfs/config",
        json={
            "ports": [],
            "env": {},
            "volumes": ["./future-secret:/run/future-secret:ro"],
            "networks": [],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "COMPOSE_CANDIDATE_PROTECTED_REFERENCE",
        "message": "compose candidate rustfs bind source does not exist",
        "stage": "candidate_validation",
        "mutation_applied": False,
    }
    mock_docker_service.update_container_config.assert_called_once_with(
        "rustfs",
        [],
        {},
        ["./future-secret:/run/future-secret:ro"],
        [],
    )


@pytest.mark.parametrize(
    "volumes",
    [
        ["/sys:/sys:rw", "/var/run/docker.sock:/var/run/docker.sock:ro"],
        [
            {
                "type": "bind",
                "source": "/sys",
                "target": "/sys",
                "read_only": False,
            },
            {
                "type": "bind",
                "source": "/var/run/docker.sock",
                "target": "/var/run/docker.sock",
                "read_only": True,
            },
        ],
    ],
)
@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_cadvisor_writable_bind_returns_typed_no_mutation_detail(
    mock_docker_service,
    volumes: list[object],
) -> None:
    login_client()
    error = ComposeCandidateContractError(
        "compose candidate volume configuration is immutable through the Manager API"
    )
    mock_docker_service.update_container_config.side_effect = error

    response = client.post(
        "/api/v1/containers/cadvisor/config",
        json={"ports": [], "env": {}, "volumes": volumes, "networks": []},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "COMPOSE_CANDIDATE_PROTECTED_REFERENCE",
        "message": str(error),
        "stage": "candidate_validation",
        "mutation_applied": False,
    }
    mock_docker_service.update_container_config.assert_called_once_with(
        "cadvisor", [], {}, volumes, []
    )


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_reset_container_config_success(mock_docker_service):
    login_client()
    mock_docker_service.reset_container_config.return_value = {
        "success": True,
        "message": "Successfully updated config and recreated kor-travel-geo-postgres.",
    }

    # Target versioned route v1
    response = client.post("/api/v1/containers/kor-travel-geo-postgresql/reset")
    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Successfully updated config and recreated kor-travel-geo-postgres.",
    }
    mock_docker_service.reset_container_config.assert_called_once_with("kor-travel-geo-postgresql")


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_reset_container_config_failure_preserves_restoration_detail(
    mock_docker_service,
):
    login_client()
    restoration = {
        "config_restored": False,
        "runtime_restored": False,
        "command": ["docker", "compose"],
    }
    mock_docker_service.reset_container_config.return_value = {
        "success": False,
        "error": "config restore failed",
        "restoration": restoration,
    }

    response = client.post("/api/v1/containers/rustfs/reset")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "message": "config restore failed",
        "restoration": restoration,
    }


@pytest.mark.parametrize("graph", ["raw", "resolved"])
@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_reset_volume_graph_drift_returns_typed_no_mutation_detail(
    mock_docker_service,
    graph: str,
) -> None:
    login_client()
    error = ComposeCandidateContractError(
        f"compose candidate {graph} volume graph differs from persisted compose"
    )
    mock_docker_service.reset_container_config.side_effect = error

    response = client.post("/api/v1/containers/rustfs/reset")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "COMPOSE_CANDIDATE_PROTECTED_REFERENCE",
        "message": str(error),
        "stage": "candidate_validation",
        "mutation_applied": False,
    }
    mock_docker_service.reset_container_config.assert_called_once_with("rustfs")


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_missing_container_action_failure_preserves_restoration_detail(
    mock_docker_service,
):
    login_client()
    restoration = {
        "config_restored": True,
        "runtime_restored": False,
        "returncode": 9,
        "stdout": "restore stdout",
        "stderr": "restore stderr",
        "error": "restore stderr",
    }
    mock_docker_service.control_container.return_value = {
        "success": False,
        "error": "missing container create failed",
        "command": ["docker", "compose"],
        "returncode": 1,
        "stdout": "candidate stdout",
        "stderr": "candidate stderr",
        "restoration": restoration,
    }

    response = client.post(
        "/api/v1/containers/rustfs/action",
        json={"action": "start"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "message": "missing container create failed",
        "command": ["docker", "compose"],
        "returncode": 1,
        "stdout": "candidate stdout",
        "stderr": "candidate stderr",
        "restoration": restoration,
    }


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_production_api_container_action_contract_failure_is_conflict(mock_docker_service):
    login_client()
    mock_docker_service.control_container.side_effect = DeploymentContractError(
        "production Map runtime/PinVi API mutation requires the compatible-pair workflow"
    )

    response = client.post(
        "/api/v1/containers/kor-travel-map-api/action",
        json={"action": "restart"},
    )

    assert response.status_code == 409
    assert "compatible-pair" in response.json()["detail"]


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_production_api_container_config_contract_failure_is_conflict(mock_docker_service):
    login_client()
    mock_docker_service.update_container_config.side_effect = DeploymentContractError(
        "production Map runtime/PinVi API mutation requires the compatible-pair workflow"
    )

    response = client.post(
        "/api/v1/containers/pinvi-api/config",
        json={"ports": [], "env": {}, "volumes": [], "networks": []},
    )

    assert response.status_code == 409
    assert "compatible-pair" in response.json()["detail"]


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_production_api_container_reset_contract_failure_is_conflict(mock_docker_service):
    login_client()
    mock_docker_service.reset_container_config.side_effect = DeploymentContractError(
        "production Map runtime/PinVi API mutation requires the compatible-pair workflow"
    )

    response = client.post("/api/v1/containers/kor-travel-map-api/reset")

    assert response.status_code == 409
    assert "compatible-pair" in response.json()["detail"]


def test_get_targets():
    login_client()
    response = client.get("/api/v1/targets")
    assert response.status_code == 200
    data = response.json()
    assert [target["id"] for target in data[:9]] == [
        "db",
        "storage",
        "gra",
        "cadv",
        "prom",
        "geo",
        "conc",
        "map",
        "pinvi",
    ]
    assert data[8]["resolved_sequence"] == [
        "db",
        "storage",
        "gra",
        "cadv",
        "prom",
        "geo",
        "conc",
        "map",
        "pinvi",
    ]
    assert data[8]["resolved_services"] == [
        "kor-travel-geo-postgres",
        "rustfs",
        "grafana",
        "cadvisor",
        "prometheus",
        "kor-travel-geo-api",
        "kor-travel-geo-ui",
        "kor-travel-concierge-postgres",
        "kor-travel-concierge-api",
        "kor-travel-concierge-mcp",
        "kor-travel-concierge-scheduler",
        "kor-travel-concierge-ui",
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "pinvi-postgres",
        "pinvi-api",
        "pinvi-web",
        "pinvi-dagster",
    ]
    assert data[4]["resolved_services"][-3:] == ["grafana", "cadvisor", "prometheus"]
    assert any(target["id"] == "all" for target in data)


@patch("kor_travel_docker_manager.api.routes.compose_service")
def test_ensure_target_success(mock_compose_service):
    login_client()
    mock_compose_service.ensure_target.return_value = {
        "success": True,
        "returncode": 0,
        "command": [
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--build",
                "kor-travel-geo-postgres",
                "rustfs",
                "grafana",
                "cadvisor",
                "prometheus",
                "kor-travel-geo-api",
                "kor-travel-geo-ui",
                "kor-travel-concierge-postgres",
                "kor-travel-concierge-api",
                "kor-travel-concierge-mcp",
                "kor-travel-concierge-scheduler",
                "kor-travel-concierge-ui",
                "kor-travel-map-api",
                "kor-travel-map-ui",
                "kor-travel-map-dagster",
                "kor-travel-map-dagster-daemon",
                "pinvi-postgres",
                "pinvi-api",
                "pinvi-web",
                "pinvi-dagster",
            ]
        ],
        "stdout": "ok",
        "stderr": "",
        "target": "main",
        "target_sequence": [
            "db",
            "storage",
            "gra",
            "cadv",
            "prom",
            "geo",
            "conc",
            "map",
            "pinvi",
        ],
        "services": [
            "kor-travel-geo-postgres",
            "rustfs",
            "grafana",
            "cadvisor",
            "prometheus",
            "kor-travel-geo-api",
            "kor-travel-geo-ui",
            "kor-travel-concierge-postgres",
            "kor-travel-concierge-api",
            "kor-travel-concierge-mcp",
            "kor-travel-concierge-scheduler",
            "kor-travel-concierge-ui",
            "kor-travel-map-postgres",
            "kor-travel-map-api",
            "kor-travel-map-ui",
            "kor-travel-map-dagster",
            "kor-travel-map-dagster-daemon",
            "pinvi-postgres",
            "pinvi-api",
            "pinvi-web",
            "pinvi-dagster",
        ],
        "init_results": [],
    }

    response = client.post("/api/v1/targets/main/ensure", json={"build": True})
    assert response.status_code == 200
    assert response.json()["success"] is True
    mock_compose_service.ensure_target.assert_called_once_with(
        "main",
        build=True,
        recreate=False,
    )


@patch("kor_travel_docker_manager.api.routes.compose_service")
def test_ensure_target_contract_failure_is_conflict_without_secret_body(
    mock_compose_service,
):
    login_client()
    mock_compose_service.ensure_target.side_effect = DeploymentContractError(
        "C6c production preflight failed"
    )

    response = client.post("/api/v1/targets/main/ensure", json={"recreate": True})

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "C6c production preflight failed"
    # GM-16: request_id는 요청마다 새로 발급되는 uuid4라 리터럴로 고정할 수
    # 없다 — 존재와 형태만 확인하고, "비밀이 안 새는지"라는 이 테스트 본래의
    # 목적은 detail이 정확히 이 문자열 하나뿐임을 확인하는 것으로 유지한다.
    assert set(body.keys()) == {"detail", "request_id"}
    uuid.UUID(body["request_id"])


@patch("kor_travel_docker_manager.api.routes.compose_service")
def test_ensure_target_unknown_target_is_not_found_not_conflict(mock_compose_service):
    """GM-12: DeploymentContractError는 ValueError의 하위클래스라, ensure_target의
    남은 로컬 `except ValueError` 절이 순서상 그것까지 삼켜 404로 잘못 바꿀 수
    있다 — 위의 `except DeploymentContractError: raise`가 먼저 가로채 막는다. 이
    테스트는 그 반대 경로(계약 위반이 아닌 bare ValueError는 여전히 404)가 이번
    app 레벨 핸들러 통합 이후에도 유지되는지 확인한다."""
    login_client()
    mock_compose_service.ensure_target.side_effect = ValueError("unknown target: bogus")

    response = client.post("/api/v1/targets/bogus/ensure", json={})

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown target: bogus"}


@patch("kor_travel_docker_manager.api.routes.compose_service")
def test_ensure_target_candidate_rejection_returns_typed_unchanged_detail(
    mock_compose_service,
):
    login_client()
    mock_compose_service.ensure_target.side_effect = ComposeCandidateContractError(
        "compose candidate resolution failed"
    )

    response = client.post("/api/v1/targets/storage/ensure", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "COMPOSE_CANDIDATE_PROTECTED_REFERENCE",
        "message": "compose candidate resolution failed",
        "stage": "candidate_validation",
        "mutation_applied": False,
    }


@patch("kor_travel_docker_manager.api.routes.compose_service")
def test_ensure_target_env_file_drift_returns_typed_no_mutation_detail(
    mock_compose_service,
) -> None:
    login_client()
    error = ComposeCandidateContractError(
        "compose env-file identity changed during the transaction"
    )
    mock_compose_service.ensure_target.side_effect = error

    response = client.post("/api/v1/targets/storage/ensure", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "COMPOSE_CANDIDATE_PROTECTED_REFERENCE",
        "message": str(error),
        "stage": "candidate_validation",
        "mutation_applied": False,
    }


@patch("kor_travel_docker_manager.api.routes.compose_service")
def test_ensure_target_post_mutation_drift_returns_typed_recovery_detail(
    mock_compose_service,
) -> None:
    login_client()
    original_error = ComposeCandidateContractError(
        "compose raw volume graph changed during the request"
    )
    restoration = {
        "success": True,
        "recovery_attempted": True,
        "command": ["docker", "compose", "up"],
    }
    mock_compose_service.ensure_target.side_effect = (
        ComposePostMutationContractError(
            original_error,
            recovery_attempted=True,
            recovery_succeeded=True,
            recovery_error=None,
            restoration=restoration,
        )
    )

    response = client.post("/api/v1/targets/storage/ensure", json={})

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "COMPOSE_POST_MUTATION_CONTRACT_FAILURE",
        "message": str(original_error),
        "stage": "post_mutation_recovery",
        "mutation_applied": True,
        "original_error": {
            "code": "COMPOSE_CANDIDATE_PROTECTED_REFERENCE",
            "message": str(original_error),
        },
        "recovery_attempted": True,
        "recovery_succeeded": True,
        "recovery_error": None,
        "restoration": restoration,
    }
    mock_compose_service.ensure_target.assert_called_once_with(
        "storage", build=False, recreate=False
    )


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_inspect_container_success(mock_docker_service):
    login_client()
    mock_docker_service.inspect_container.return_value = {
        "success": True,
        "container": {
            "id": "kor-travel-geo-postgresql",
            "name": "kor-travel-geo-postgres",
            "config": {"env": ["POSTGRES_PASSWORD=<redacted>"]},
        },
    }

    response = client.get("/api/v1/containers/kor-travel-geo-postgresql/inspect")
    assert response.status_code == 200
    assert response.json()["config"]["env"] == ["POSTGRES_PASSWORD=<redacted>"]


def test_rate_limited_login_returns_retry_after_header():
    # 429 응답은 Retry-After 헤더를 포함해야 한다(HTTPException headers로 전달).
    # 주의: 이 테스트는 마지막에 두고 testclient 버킷을 정리해 후속 영향이 없게 한다.
    import kor_travel_docker_manager.database as _db
    from kor_travel_docker_manager._time import utcnow as _utcnow
    from kor_travel_docker_manager.models import LoginAuditEvent

    client_hash = hashlib.sha256(b"testclient").hexdigest()

    def _clear():
        with _db.get_db_session() as s:
            s.query(LoginAuditEvent).filter(LoginAuditEvent.client_ip_hash == client_hash).delete()
            s.commit()

    _clear()
    with _db.get_db_session() as s:
        for i in range(5):
            s.add(
                LoginAuditEvent(
                    audit_event_id=f"ra-fail-{i}",
                    event_type="login",
                    outcome="denied",
                    reason="invalid_credentials",
                    client_ip_hash=client_hash,
                    occurred_at=_utcnow(),
                )
            )
        s.commit()

    client.cookies.clear()
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD, "next": "/"},
    )
    assert resp.status_code == 429
    assert resp.headers.get("retry-after") is not None
    assert int(resp.headers["retry-after"]) >= 1
    # 구조화된 { code, message } 봉투여야 한다 — bare 문자열이면 프런트가 CODE_MESSAGES를
    # 조회하지 못하고 이 토큰을 그대로 사용자에게 노출한다.
    body = resp.json()["detail"]
    assert body["code"] == "RATE_LIMITED"
    assert isinstance(body["message"], str) and body["message"]
    assert body["message"] != "RATE_LIMITED"

    _clear()


def test_is_https_via_configured_public_origin(monkeypatch):
    # TLS 종단 프록시가 신뢰 X-Forwarded-Proto를 주입하지 않아도, 브라우저 Origin이 설정된
    # https 공개 origin과 일치하면 https로 간주(세션 쿠키 Secure 플래그)해야 한다.
    from starlette.requests import Request

    from kor_travel_docker_manager.services.auth_service import _is_https

    monkeypatch.setenv("KTDM_FRONTEND_ORIGINS", "https://manager.example.org,http://localhost:12905")

    def make_request(scheme, origin):
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "scheme": scheme,
                "headers": [(b"origin", origin.encode())] if origin else [],
                "client": ("192.168.1.1", 40000),  # non-loopback: X-Forwarded-Proto 미신뢰
                "server": ("testserver", 80),
            }
        )

    # http 연결이지만 브라우저 Origin이 설정된 https 공개 origin -> https로 간주
    assert _is_https(make_request("http", "https://manager.example.org")) is True
    # http LAN origin -> https 아님
    assert _is_https(make_request("http", "http://localhost:12905")) is False
    # 화이트리스트에 없는 https origin -> 거부(위조 방지)
    assert _is_https(make_request("http", "https://evil.example.org")) is False
    # 직접 https 연결 -> https
    assert _is_https(make_request("https", None)) is True


@patch("kor_travel_docker_manager.api.routes.list_standalone_backups_for_display")
def test_get_backups_lists_all_roles_when_role_is_omitted(mock_list):
    login_client()

    def fake_list(role):
        return [
            {
                "role": role,
                "created_at_unix": 1000,
                "duration_sec": 1.0,
                "byte_size": 1,
                "sha256": "a" * 64,
                "backup_filename": f"{role}-1000.dump",
                "instance": "container:127.0.0.1:12345/db",
                "db_size_bytes": 100,
                "toc_entry_count": 2,
                "alembic_head": "0001_head",
            }
        ]

    mock_list.side_effect = fake_list

    response = client.get("/api/v1/backups")

    assert response.status_code == 200
    data = response.json()
    assert len(data["backups"]) == 6
    assert {backup["role"] for backup in data["backups"]} == {
        "geo",
        "geo_dagster",
        "concierge",
        "map_application",
        "map_dagster",
        "pinvi",
    }
    # GM-18: 프론트가 select/생성 버튼 role 목록을 하드코딩하지 않고 여기서 파생할 수
    # 있도록, 응답이 canonical 목록을 함께 실어야 한다.
    assert data["roles"] == list(BACKUP_ROLES)


@patch("kor_travel_docker_manager.api.routes.list_standalone_backups_for_display")
def test_get_backups_filters_by_role(mock_list):
    login_client()
    mock_list.return_value = [
        {
            "role": "pinvi",
            "created_at_unix": 1000,
            "duration_sec": 1.0,
            "byte_size": 1,
            "sha256": "a" * 64,
            "backup_filename": "pinvi-1000.dump",
            "instance": "container:127.0.0.1:12345/db",
            "db_size_bytes": 100,
            "toc_entry_count": 2,
            "alembic_head": "0001_head",
        }
    ]

    response = client.get("/api/v1/backups?role=pinvi")

    assert response.status_code == 200
    assert response.json() == {
        "backups": [
            {
                "role": "pinvi",
                "created_at_unix": 1000,
                "duration_sec": 1.0,
                "byte_size": 1,
                "sha256": "a" * 64,
                "backup_filename": "pinvi-1000.dump",
                "instance": "container:127.0.0.1:12345/db",
                "db_size_bytes": 100,
                "toc_entry_count": 2,
                "alembic_head": "0001_head",
            }
        ],
        # GM-18: role 필터가 걸려도 roles는 항상 전체 canonical 목록이다 — "무엇을
        # 고를 수 있는가"는 지금 보고 있는 필터와 무관하다.
        "roles": list(BACKUP_ROLES),
    }
    mock_list.assert_called_once_with("pinvi")


def test_get_backups_rejects_unknown_role():
    login_client()

    response = client.get("/api/v1/backups?role=not-a-real-role")

    assert response.status_code == 400
    assert "not-a-real-role" in response.json()["detail"]


@patch("kor_travel_docker_manager.api.routes.list_standalone_backups_for_display")
def test_get_backups_degrades_a_single_corrupt_manifest_instead_of_hiding_everything(
    mock_list,
):
    """GM-13: geo 백업 세트를 map 디렉터리에 잘못 복사하는 것 같은 흔한 실수 하나로
    장애 중 가장 필요한 순간에 멀쩡한 백업 전체 목록이 사라지던 문제의 핵심 회귀
    테스트. 손상된 manifest 1건은 200 응답 안에서 {"state": "unreadable", ...}
    행으로 격하되고, 같은 role의 나머지 정상 manifest는 그대로 보인다."""

    login_client()

    def fake_list(role):
        if role != "geo":
            return []
        return [
            {
                "role": "geo",
                "created_at_unix": 1000,
                "duration_sec": 1.0,
                "byte_size": 1,
                "sha256": "a" * 64,
                "backup_filename": "geo-1000.dump",
                "instance": "container:127.0.0.1:12500/kor_travel_geo",
                "db_size_bytes": 100,
                "toc_entry_count": 2,
                "alembic_head": "0001_head",
            },
            {
                "state": "unreadable",
                "filename": "geo-999.manifest",
                "reason": "manifest role does not match the requested role: geo-999.manifest",
            },
        ]

    mock_list.side_effect = fake_list

    response = client.get("/api/v1/backups?role=geo")

    assert response.status_code == 200
    backups = response.json()["backups"]
    # 순서까지 고정한다 — 손상된 항목을 목록 어디에 두는지는 그 자체가 회귀 대상이다
    # (적대적 리뷰가 지적: 순서를 안 보는 단언은 정렬 버그를 놓친다).
    assert backups == [
        {
            "role": "geo",
            "created_at_unix": 1000,
            "duration_sec": 1.0,
            "byte_size": 1,
            "sha256": "a" * 64,
            "backup_filename": "geo-1000.dump",
            "instance": "container:127.0.0.1:12500/kor_travel_geo",
            "db_size_bytes": 100,
            "toc_entry_count": 2,
            "alembic_head": "0001_head",
        },
        {
            "state": "unreadable",
            "filename": "geo-999.manifest",
            "reason": "manifest role does not match the requested role: geo-999.manifest",
        },
    ]


@patch("kor_travel_docker_manager.api.routes.list_standalone_backups_for_display")
def test_get_backups_sorts_unreadable_entries_after_every_readable_entry_across_roles(
    mock_list,
):
    """GM-13 리뷰 반영: unreadable 항목은 created_at_unix가 없어, role을 섞어
    전역 재정렬할 때 기본값을 잘못 고르면(예: 0) 실제 시각과 무관하게 맨 앞으로
    쏠린다 — geo의 unreadable 항목 하나가 map_application의 훨씬 나중 백업보다도
    앞에 뜨는 식으로 재현된다. 이 테스트는 role 두 개에 걸쳐 readable 두 건과
    unreadable 한 건을 섞어, 최종 응답이 시간순 readable 다음에 unreadable이
    오는지(그 반대나 뒤섞임이 아닌지) 직접 확인한다."""

    login_client()

    def fake_list(role):
        if role == "geo":
            return [
                {
                    "state": "unreadable",
                    "filename": "geo-1.manifest",
                    "reason": "manifest is unreadable: geo-1.manifest",
                }
            ]
        if role == "map_application":
            return [
                {
                    "role": "map_application",
                    "created_at_unix": 2_000_000_000,
                    "duration_sec": 1.0,
                    "byte_size": 1,
                    "sha256": "b" * 64,
                    "backup_filename": "map_application-2000000000.dump",
                    "instance": "container:127.0.0.1:12700/kor_travel_map",
                    "db_size_bytes": 100,
                    "toc_entry_count": 2,
                    "alembic_head": "0002_head",
                }
            ]
        if role == "pinvi":
            return [
                {
                    "role": "pinvi",
                    "created_at_unix": 1,
                    "duration_sec": 1.0,
                    "byte_size": 1,
                    "sha256": "c" * 64,
                    "backup_filename": "pinvi-1.dump",
                    "instance": "container:127.0.0.1:12800/pinvi",
                    "db_size_bytes": 100,
                    "toc_entry_count": 2,
                    "alembic_head": "0003_head",
                }
            ]
        return []

    mock_list.side_effect = fake_list

    response = client.get("/api/v1/backups")

    assert response.status_code == 200
    backups = response.json()["backups"]
    states = [row.get("state") for row in backups]
    # readable 항목은 시간순(pinvi created_at_unix=1이 map_application의
    # 2_000_000_000보다 먼저), unreadable은 시각과 무관하게 맨 뒤 하나뿐이어야 한다.
    assert [row.get("backup_filename") for row in backups if row.get("state") != "unreadable"] == [
        "pinvi-1.dump",
        "map_application-2000000000.dump",
    ]
    assert states[-1] == "unreadable"
    assert states.count("unreadable") == 1


@patch("kor_travel_docker_manager.api.routes.list_standalone_backups_for_display")
def test_get_backups_surfaces_unreadable_directory_as_service_unavailable(mock_list):
    """디렉터리 자체를 못 읽는 것(권한 문제 등)은 개별 manifest 손상과 다르다 —
    이건 여전히 fail-close(503)다. 이전 GM-13 이전 동작은 409였다."""

    from kor_travel_docker_manager.services.standalone_backup import StandaloneBackupError

    login_client()
    mock_list.side_effect = StandaloneBackupError("geo backup directory is unreadable: ...")

    response = client.get("/api/v1/backups?role=geo")

    assert response.status_code == 503
    assert "unreadable" in response.json()["detail"]


def test_get_backups_requires_authentication():
    client.cookies.clear()

    response = client.get("/api/v1/backups")

    assert response.status_code == 401


@patch("kor_travel_docker_manager.api.routes.read_offbox_sync_status")
def test_get_offbox_sync_status_reports_none_when_never_run(mock_status):
    login_client()
    mock_status.return_value = None

    with patch(
        "kor_travel_docker_manager.api.routes.offbox_sync_is_configured", return_value=False
    ):
        response = client.get("/api/v1/backups/offbox-sync-status")

    assert response.status_code == 200
    assert response.json() == {"status": None, "configured": False}


@patch("kor_travel_docker_manager.api.routes.read_offbox_sync_status")
def test_get_offbox_sync_status_reports_the_last_result(mock_status):
    login_client()
    mock_status.return_value = {"destination_host": "backup-vault.internal", "all_verified": True}

    with patch(
        "kor_travel_docker_manager.api.routes.offbox_sync_is_configured", return_value=True
    ):
        response = client.get("/api/v1/backups/offbox-sync-status")

    assert response.status_code == 200
    assert response.json()["status"]["all_verified"] is True
    assert response.json()["configured"] is True


@patch("kor_travel_docker_manager.api.routes.read_offbox_sync_status")
def test_get_offbox_sync_status_treats_a_half_set_env_as_not_configured(mock_status):
    """host만 설정되고 user/remote_root가 없으면 offbox_sync_is_configured가 예외를
    낸다 — 이 읽기 전용 상태 조회는 그 misconfiguration으로 500이 나면 안 된다."""

    from kor_travel_docker_manager.services.offbox_backup_sync import OffboxSyncError

    login_client()
    mock_status.return_value = None

    with patch(
        "kor_travel_docker_manager.api.routes.offbox_sync_is_configured",
        side_effect=OffboxSyncError("KTDM_OFFBOX_HOST is set but KTDM_OFFBOX_USER is missing"),
    ):
        response = client.get("/api/v1/backups/offbox-sync-status")

    assert response.status_code == 200
    assert response.json() == {"status": None, "configured": False}


def test_get_offbox_sync_status_requires_authentication():
    client.cookies.clear()

    response = client.get("/api/v1/backups/offbox-sync-status")

    assert response.status_code == 401


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_get_runtime_pins_exposes_lifecycle_and_plain_language_summary(mock_read):
    login_client()
    mock_read.return_value = {
        "status": "ok",
        "source": "published_copy",
        "published_at": "2026-08-28T00:00:00Z",
        "release_version": 5,
        "pinset_sha256": "a" * 64,
        "sources": [
            {"role": "map", "url": "https://github.com/digitie/kor-travel-map.git", "revision": "b" * 40},
            {"role": "pinvi", "url": "https://github.com/digitie/pinvi.git", "revision": "c" * 40},
        ],
        "rotated_at": "2026-08-28T00:00:00Z",
        "rotated_by": "operator",
        "reason": "새 candidate",
        "history": [],
        "blocked_pinsets": [],
    }

    response = client.get("/api/v1/runtime-pins")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["pins"]["pinset_sha256"] == "a" * 64
    assert body["lifecycle"]["current_pinset_is_blocked"] is False
    assert body["summary"]["state"] == "ok"
    assert body["summary"]["next_action"] == ""


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_get_runtime_pins_flags_a_terminal_current_pinset(mock_read):
    login_client()
    mock_read.return_value = {
        "status": "ok",
        "source": "published_copy",
        "release_version": 5,
        "pinset_sha256": "a" * 64,
        "sources": [],
        "rotated_at": "2026-08-28T00:00:00Z",
        "rotated_by": "operator",
        "reason": "seed",
        "history": [],
        "blocked_pinsets": [
            {
                "pinset_sha256": "a" * 64,
                "map_revision": "b" * 40,
                "pinvi_revision": "c" * 40,
                "reason": "upstream이 terminal로 선언",
                "blocked_at": "2026-08-28T00:00:00Z",
            }
        ],
    }

    response = client.get("/api/v1/runtime-pins")

    body = response.json()
    assert body["lifecycle"]["current_pinset_is_blocked"] is True
    assert body["summary"]["state"] == "action_required"
    assert body["summary"]["next_action"].endswith("ktdctl pin verify")


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_get_runtime_pins_reports_unknown_instead_of_guessing(mock_read):
    login_client()
    mock_read.return_value = {
        "status": "unknown",
        "source": None,
        "detail": "runtime pin registry is not readable by this process",
    }

    response = client.get("/api/v1/runtime-pins")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown"
    assert body["pins"] is None


def test_get_runtime_pins_requires_authentication():
    client.cookies.clear()

    response = client.get("/api/v1/runtime-pins")

    assert response.status_code == 401


@patch("kor_travel_docker_manager.api.routes.read_published_pinned_runtime_generation")
def test_get_pinned_runtime_generation_returns_public_contract_and_summary(mock_read):
    login_client()
    mock_read.return_value = {
        "status": "ok",
        "source": "published_copy",
        "manifest": {"version": 6, "active_generation": {"pinset_sha256": "a" * 64}},
        "journal": {"version": 8, "phase": "committed", "candidate": {"pinset_sha256": "a" * 64}},
        "terminal": None,
        "summary": {
            "state": "committed",
            "text": "고정된 runtime 세대가 커밋되어 있습니다.",
            "next_action": "",
            "manifest_version": 6,
            "journal_version": 8,
        },
    }

    response = client.get("/api/v1/pinned-runtime/generation")

    assert response.status_code == 200
    assert response.json()["summary"]["state"] == "committed"
    assert response.json()["manifest"]["version"] == 6
    mock_read.assert_called_once_with()


def test_get_pinned_runtime_generation_requires_authentication():
    client.cookies.clear()

    assert client.get("/api/v1/pinned-runtime/generation").status_code == 401


# --- KUM-M10: 관리자 비밀번호 변경 ---------------------------------------------


@patch("kor_travel_docker_manager.api.admin.change_admin_password")
def test_post_admin_password_records_the_verdict_but_never_the_secret(mock_change):
    login_client()
    mock_change.return_value = {
        "ok": True,
        "guard": "no_journal",
        "acknowledged": False,
        "env_path": "/opt/x/.env",
    }

    response = client.post(
        "/api/v1/admin/password",
        json={"current_password": TEST_ADMIN_PASSWORD, "new_password": "a-new-password-1"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "guard": "no_journal"}
    events = client.get(
        "/api/v1/admin/login-audit-events?event_type=admin_password&outcome=succeeded"
    ).json()
    detail = events[0]["detail"]
    assert detail["guard"] == "no_journal"
    assert detail["acknowledged"] is False
    # GM-16: http_request_id는 요청마다 새로 발급되는 uuid4라 리터럴로 고정할
    # 수 없다 — 존재와 형태만 확인한다. 이 테스트 본래의 목적(env_path/비밀번호가
    # 안 새는지)은 정확한 키 집합 확인으로 유지한다. 감사 주입 키를
    # "request_id"가 아니라 "http_request_id"로 부르는 이유는 runtime-pin
    # 회전 요청처럼 도메인 객체가 이미 "request_id"라는 이름을 쓰는 경우와
    # 충돌해 그 값을 지우는 것을 막기 위해서다(적대적 리뷰가 실제로 재현).
    assert set(detail.keys()) == {"guard", "acknowledged", "http_request_id"}
    uuid.UUID(detail["http_request_id"])
    # 비밀번호도 해시도 감사에 남기지 않는다.
    assert "a-new-password-1" not in str(events)


@patch("kor_travel_docker_manager.api.admin.change_admin_password")
def test_audit_event_request_id_matches_the_triggering_response_header(mock_change):
    """GM-16의 핵심 주장: UI가 받은 오류(또는 성공) 응답의 request_id가 그
    요청이 남긴 감사 행의 http_request_id와 정확히 같은 값이어야 둘을 하나의
    키로 조인할 수 있다 — 존재만이 아니라 *일치*를 확인한다. 감사 쪽 키
    이름이 "request_id"가 아니라 "http_request_id"인 이유는 위
    test_post_admin_password_records_the_verdict_but_never_the_secret의
    주석 참고(도메인 객체의 자체 request_id와 충돌 방지)."""

    login_client()
    mock_change.return_value = {
        "ok": True,
        "guard": "no_journal",
        "acknowledged": False,
        "env_path": "/opt/x/.env",
    }

    response = client.post(
        "/api/v1/admin/password",
        json={"current_password": TEST_ADMIN_PASSWORD, "new_password": "a-different-password-2"},
    )

    assert response.status_code == 200
    triggering_request_id = response.headers["x-request-id"]

    events = client.get(
        "/api/v1/admin/login-audit-events?event_type=admin_password&outcome=succeeded"
    ).json()
    assert events[0]["detail"]["http_request_id"] == triggering_request_id


def test_admin_password_rate_limit_returns_structured_error_envelope():
    """POST /api/v1/admin/password도 로그인과 같은 durable 카운터로 429를 낸다 —
    RATE_LIMITED가 bare 문자열이 아니라 { code, message } 봉투여야 한다."""
    import kor_travel_docker_manager.database as _db
    from kor_travel_docker_manager._time import utcnow as _utcnow
    from kor_travel_docker_manager.models import LoginAuditEvent

    login_client()
    client_hash = hashlib.sha256(b"testclient").hexdigest()

    def _clear():
        with _db.get_db_session() as s:
            s.query(LoginAuditEvent).filter(
                LoginAuditEvent.client_ip_hash == client_hash,
                LoginAuditEvent.audit_event_id.like("admin-pw-ra-fail-%"),
            ).delete()
            s.commit()

    with _db.get_db_session() as s:
        for i in range(5):
            s.add(
                LoginAuditEvent(
                    audit_event_id=f"admin-pw-ra-fail-{i}",
                    event_type="login",
                    outcome="denied",
                    reason="invalid_credentials",
                    client_ip_hash=client_hash,
                    occurred_at=_utcnow(),
                )
            )
        s.commit()

    response = client.post(
        "/api/v1/admin/password",
        json={"current_password": TEST_ADMIN_PASSWORD, "new_password": "a-new-password-9"},
    )

    assert response.status_code == 429
    assert response.headers.get("retry-after") is not None
    body = response.json()["detail"]
    assert body["code"] == "RATE_LIMITED"
    assert isinstance(body["message"], str) and body["message"]
    assert body["message"] != "RATE_LIMITED"

    _clear()


@patch("kor_travel_docker_manager.api.admin.change_admin_password")
def test_a_wrong_current_password_joins_the_login_bruteforce_counter(mock_change):
    """자격증명 추측만 로그인 카운터에 합류시킨다 — 다른 거부로 오염시키지 않는다."""

    from kor_travel_docker_manager.services.admin_password_service import (
        AdminPasswordError,
    )

    login_client()
    mock_change.side_effect = AdminPasswordError(
        "INVALID_CREDENTIALS", "현재 비밀번호가 일치하지 않습니다.", status_code=401
    )

    response = client.post(
        "/api/v1/admin/password",
        json={"current_password": "wrong", "new_password": "a-new-password-1"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"
    login_events = client.get(
        "/api/v1/admin/login-audit-events?event_type=login&outcome=denied"
    ).json()
    assert any(event["reason"] == "invalid_credentials" for event in login_events)


@patch("kor_travel_docker_manager.api.admin.change_admin_password")
def test_a_guard_refusal_does_not_pollute_the_login_counter(mock_change):
    from kor_travel_docker_manager.services.admin_password_service import (
        AdminPasswordError,
    )

    login_client()
    mock_change.side_effect = AdminPasswordError(
        "PINNED_REBUILD_JOURNAL_UNFINISHED", "미종결 재구축 기록이 있습니다."
    )

    response = client.post(
        "/api/v1/admin/password",
        json={"current_password": TEST_ADMIN_PASSWORD, "new_password": "a-new-password-1"},
    )

    assert response.status_code == 409
    denied = client.get(
        "/api/v1/admin/login-audit-events?event_type=admin_password&outcome=denied"
    ).json()
    assert any(
        event["reason"] == "pinned_rebuild_journal_unfinished" for event in denied
    )


def test_the_password_route_enforces_the_minimum_length_before_any_work():
    login_client()

    response = client.post(
        "/api/v1/admin/password",
        json={"current_password": TEST_ADMIN_PASSWORD, "new_password": "short"},
    )

    assert response.status_code == 422


def test_admin_password_routes_require_authentication():
    client.cookies.clear()

    assert client.get("/api/v1/admin/password/preflight").status_code == 401
    assert (
        client.post(
            "/api/v1/admin/password",
            json={"current_password": "x", "new_password": "a-new-password-1"},
        ).status_code
        == 401
    )


# --- KUM-M9: 백업 생성은 202 + job id로 비동기다 -------------------------------


@pytest.fixture
def clean_job_runner():
    """모듈 싱글턴이므로 남은 running 기록이 다음 테스트의 submit을 막는다."""

    from kor_travel_docker_manager.services.job_runner import job_runner

    job_runner.reset()
    yield job_runner
    for _ in range(200):
        if job_runner.latest(kind="db_backup_create", key="geo") is None:
            break
        if job_runner.latest(kind="db_backup_create", key="geo").state != "running":
            break
        time.sleep(0.05)
    try:
        job_runner.reset()
    except RuntimeError:
        pass


def _await_job(role: str, job_id: str) -> dict:
    for _ in range(200):
        body = client.get(f"/api/v1/backups/{role}/jobs/{job_id}").json()
        if body["state"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


@patch("kor_travel_docker_manager.api.routes.create_standalone_backup")
def test_post_backup_returns_202_and_a_job_that_finishes(mock_create, clean_job_runner):
    """4시간짜리 dump를 HTTP 요청 수명에 묶을 수 없다."""

    login_client()
    manifest = Mock()
    manifest.to_json.return_value = {"role": "geo", "backup_filename": "geo-1.dump"}
    mock_create.return_value = manifest

    response = client.post("/api/v1/backups/geo", json={"timeout_seconds": 60})

    assert response.status_code == 202
    started = response.json()
    assert started["state"] == "running"
    assert started["key"] == "geo"

    finished = _await_job("geo", started["job_id"])
    assert finished["state"] == "succeeded"
    assert finished["result"]["backup_filename"] == "geo-1.dump"
    mock_create.assert_called_once_with("geo", timeout=60)


@patch("kor_travel_docker_manager.api.routes.record_login_audit_event")
@patch("kor_travel_docker_manager.api.routes.create_standalone_backup")
def test_post_backup_records_the_audit_event_off_the_event_loop_thread(
    mock_create, mock_audit, clean_job_runner
):
    """GM-14: 이 감사 기록은 asyncio.to_thread로 내려야 한다 — 그냥 동기 호출로
    두면 이 async 핸들러가 event loop 스레드 위에서 직접 블로킹 DB 쓰기를
    하게 되어, 그 사이 /health·모든 WebSocket·broadcast가 함께 멈춘다.

    스레드 *이름* 비교로는 이걸 못 잡는다 — TestClient 자체가 이미 메인
    스레드가 아닌 별도 스레드에서 앱의 이벤트 루프를 돌리므로, to_thread를
    빼도 "메인 스레드가 아니다"는 여전히 참이 돼 오탐 없이 통과해 버린다
    (직접 재현·mutation으로 확인). 대신 asyncio.get_running_loop()의
    성공 여부로 판별한다 — to_thread의 ThreadPoolExecutor 워커 스레드에는
    바인딩된 이벤트 루프가 없어 RuntimeError가 나지만, 이벤트 루프 스레드
    위에서 직접 호출되면 루프가 잡힌다."""

    login_client()
    manifest = Mock()
    manifest.to_json.return_value = {"role": "geo", "backup_filename": "geo-1.dump"}
    mock_create.return_value = manifest
    ran_without_a_running_loop = None

    def capture_loop_state(*args, **kwargs):
        nonlocal ran_without_a_running_loop
        try:
            asyncio.get_running_loop()
            ran_without_a_running_loop = False
        except RuntimeError:
            ran_without_a_running_loop = True

    mock_audit.side_effect = capture_loop_state

    response = client.post("/api/v1/backups/geo", json={"timeout_seconds": 60})

    assert response.status_code == 202
    assert ran_without_a_running_loop is True


@patch("kor_travel_docker_manager.api.routes.record_login_audit_event")
@patch("kor_travel_docker_manager.api.routes.create_standalone_backup")
def test_post_backup_still_returns_202_when_the_audit_write_fails(
    mock_create, mock_audit, clean_job_runner
):
    """GM-14: 감사 기록은 job이 이미 시작된 *뒤*에 남긴다 — 그 기록이 실패해도
    백업 자체는 멀쩡히 도는데 이걸 500으로 보고하면 클라이언트가 '시작 안 됐다'고
    오판하고 재시도해 이중 pg_dump를 유발할 수 있다."""

    login_client()
    manifest = Mock()
    manifest.to_json.return_value = {"role": "geo", "backup_filename": "geo-1.dump"}
    mock_create.return_value = manifest
    mock_audit.side_effect = RuntimeError("database is locked")

    response = client.post("/api/v1/backups/geo", json={"timeout_seconds": 60})

    assert response.status_code == 202
    started = response.json()
    assert started["state"] == "running"
    assert "failed to record" in started["audit_warning"]
    mock_audit.assert_called_once()


@patch("kor_travel_docker_manager.api.routes.create_standalone_backup")
def test_a_failed_backup_job_reports_the_failure_instead_of_vanishing(
    mock_create, clean_job_runner
):
    login_client()
    mock_create.side_effect = StandaloneBackupError("pg_dump produced an empty file")

    started = client.post("/api/v1/backups/geo", json={}).json()
    finished = _await_job("geo", started["job_id"])

    assert finished["state"] == "failed"
    assert "empty file" in finished["error"]


def test_backup_job_lookup_rejects_an_unknown_role_and_id(clean_job_runner):
    login_client()

    assert client.post("/api/v1/backups/nope", json={}).status_code == 400
    assert client.get("/api/v1/backups/nope/jobs").status_code == 400
    assert client.get("/api/v1/backups/geo/jobs/missing").status_code == 404
    assert client.get("/api/v1/backups/geo/jobs").json() == {"job": None}


def test_backup_routes_require_authentication():
    client.cookies.clear()

    assert client.post("/api/v1/backups/geo", json={}).status_code == 401
    assert client.get("/api/v1/backups/geo/jobs").status_code == 401


# --- KUM-M5: UI는 회전을 '요청'만 하고, 적용은 root CLI가 한다 -----------------

MAP_REVISION = "b" * 40
PINVI_REVISION = "c" * 40


def _published_pins(**overrides):
    payload = {
        "status": "ok",
        "source": "published_copy",
        "published_at": "2026-08-28T00:00:00Z",
        "release_version": 5,
        "pinset_sha256": "a" * 64,
        "sources": [
            {
                "role": "map",
                "url": "https://github.com/digitie/kor-travel-map.git",
                "revision": MAP_REVISION,
            },
            {
                "role": "pinvi",
                "url": "https://github.com/digitie/pinvi.git",
                "revision": PINVI_REVISION,
            },
        ],
        "rotated_at": "2026-08-28T00:00:00Z",
        "rotated_by": "operator",
        "reason": "seed",
        "history": [],
        "blocked_pinsets": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def isolated_pin_requests(tmp_path, monkeypatch):
    """요청 파일을 tmp_path로 옮긴다 — 테스트가 호스트 상태를 건드리면 안 된다."""

    from kor_travel_docker_manager.services import runtime_pin_request

    target = tmp_path / "requests" / "runtime-pin-requests.json"
    monkeypatch.setenv(runtime_pin_request.RUNTIME_PIN_REQUEST_FILE_ENV, str(target))
    return target


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_post_runtime_pin_request_records_a_proposal_not_a_rotation(
    mock_read, isolated_pin_requests
):
    login_client()
    mock_read.return_value = _published_pins()

    response = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": "d" * 40, "reason": "새 후보 커밋"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["request"]["role"] == "map"
    assert body["request"]["base_pinset_sha256"] == "a" * 64
    assert "apply-pending" in body["next_action"]
    # 요청은 파일 하나일 뿐이고 registry는 건드리지 않는다.
    assert isolated_pin_requests.exists()


def test_post_runtime_pin_request_rejects_a_role_outside_the_canonical_set(
    isolated_pin_requests,
):
    """GM-18: `RuntimePinRotationRequestBody.role`은
    `pinned_runtime_release.RuntimeSourceRole`(정본)을 참조해야 한다 —
    독립적으로 `Literal["map", "pinvi"]`를 다시 적으면 정본이 바뀔 때
    이 라우트만 조용히 구식으로 남을 수 있다. Pydantic 검증이 라우트
    본문에 들어가기도 전에 422로 거부하므로 published pins mock조차
    필요 없다."""

    login_client()

    response = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "not-a-real-role", "revision": "d" * 40, "reason": "x"},
    )

    assert response.status_code == 422
    assert not isolated_pin_requests.exists()


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_post_runtime_pin_request_refuses_when_the_published_copy_is_stale(
    mock_read, isolated_pin_requests
):
    login_client()
    mock_read.return_value = _published_pins(status="stale")

    response = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": "d" * 40, "reason": "새 후보 커밋"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RUNTIME_PINS_UNVERIFIED"
    assert not isolated_pin_requests.exists()


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_post_runtime_pin_request_refuses_a_no_op(mock_read, isolated_pin_requests):
    login_client()
    mock_read.return_value = _published_pins()

    response = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": MAP_REVISION, "reason": "그대로 두기"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RUNTIME_PIN_UNCHANGED"
    assert not isolated_pin_requests.exists()


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_post_runtime_pin_request_refuses_a_single_role_rotation_on_a_terminal_pinset(
    mock_read, isolated_pin_requests
):
    """terminal 상태에서는 registry가 단일 role 회전을 거부한다 — 결코 적용될 수 없는
    요청을 화면에 대기 중으로 남기면 그 자체가 거짓말이다."""

    login_client()
    mock_read.return_value = _published_pins(
        blocked_pinsets=[
            {
                "pinset_sha256": "a" * 64,
                "map_revision": MAP_REVISION,
                "pinvi_revision": PINVI_REVISION,
                "reason": "terminal",
                "blocked_at": "2026-08-28T00:00:00Z",
            }
        ]
    )

    response = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": "d" * 40, "reason": "새 후보 커밋"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "RUNTIME_PIN_TERMINAL_REQUIRES_PAIR"
    # 실제 해소 명령을 함께 준다.
    assert "rotate-pair" in detail["message"]
    assert not isolated_pin_requests.exists()


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_post_runtime_pin_request_refuses_a_permanently_blocked_target(
    mock_read, isolated_pin_requests
):
    from kor_travel_docker_manager.services.runtime_pin_request import (
        prospective_pinset_sha256,
    )

    login_client()
    target_revision = "d" * 40
    blocked_digest = prospective_pinset_sha256(
        release_version=5, map_revision=target_revision, pinvi_revision=PINVI_REVISION
    )
    mock_read.return_value = _published_pins(
        blocked_pinsets=[
            {
                "pinset_sha256": blocked_digest,
                "map_revision": target_revision,
                "pinvi_revision": PINVI_REVISION,
                "reason": "upstream이 terminal로 선언",
                "blocked_at": "2026-08-28T00:00:00Z",
            }
        ]
    )

    response = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": target_revision, "reason": "재시도"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RUNTIME_PIN_BLOCKED_TARGET"
    assert not isolated_pin_requests.exists()


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_post_runtime_pin_request_never_overwrites_a_pending_one(
    mock_read, isolated_pin_requests
):
    login_client()
    mock_read.return_value = _published_pins()
    first = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": "d" * 40, "reason": "첫 요청"},
    )
    assert first.status_code == 201

    second = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "pinvi", "revision": "e" * 40, "reason": "두 번째 요청"},
    )

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["code"] == "RUNTIME_PIN_REQUEST_EXISTS"
    assert detail["request_id"] == first.json()["request"]["request_id"]


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_runtime_pin_audit_event_keeps_its_own_domain_request_id(
    mock_read, isolated_pin_requests
):
    """GM-16 적대적 리뷰 발견: 감사 기록이 모든 행에 HTTP 상관관계 ID를
    "request_id"라는 이름으로 주입했다면, 여기서 이미 그 이름을 domain
    id(대기 중인 회전 요청 자신의 id — 나중에 DELETE .../requests/{id}로
    그대로 넘겨야 하는 값)로 쓰는 이 이벤트의 값을 조용히 덮어써 지웠을
    것이다. 감사 쪽 키를 "http_request_id"로 분리했으므로 둘 다 살아남아야
    한다."""

    login_client()
    mock_read.return_value = _published_pins()

    response = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": "d" * 40, "reason": "첫 요청"},
    )
    assert response.status_code == 201
    domain_request_id = response.json()["request"]["request_id"]
    http_request_id = response.headers["x-request-id"]
    assert domain_request_id != http_request_id  # 서로 다른 개념임을 전제로 한 테스트다

    events = client.get(
        "/api/v1/admin/login-audit-events?event_type=runtime_pin&outcome=succeeded"
    ).json()
    detail = events[0]["detail"]
    assert detail["request_id"] == domain_request_id
    assert detail["http_request_id"] == http_request_id


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_post_runtime_pin_request_rejects_a_multiline_reason(
    mock_read, isolated_pin_requests
):
    login_client()
    mock_read.return_value = _published_pins()

    response = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": "d" * 40, "reason": "사유\n두 번째 줄"},
    )

    # 여기서 통과시키면 CLI가 읽지 못해 요청이 영원히 적용되지 않는다.
    assert response.status_code == 422
    assert not isolated_pin_requests.exists()


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_get_runtime_pins_marks_a_request_stale_after_the_pin_moved(
    mock_read, isolated_pin_requests
):
    login_client()
    mock_read.return_value = _published_pins()
    client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": "d" * 40, "reason": "새 후보 커밋"},
    )

    pending = client.get("/api/v1/runtime-pins").json()["pending_request"]
    assert pending["status"] == "pending"

    # 그 사이 누군가 SSH에서 회전시켰다면, 이 요청으로는 더 이상 적용되지 않는다.
    mock_read.return_value = _published_pins(pinset_sha256="f" * 64)
    moved = client.get("/api/v1/runtime-pins").json()["pending_request"]

    assert moved["status"] == "stale"


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_delete_runtime_pin_request_requires_the_exact_id(
    mock_read, isolated_pin_requests
):
    login_client()
    mock_read.return_value = _published_pins()
    created = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": "d" * 40, "reason": "새 후보 커밋"},
    ).json()["request"]

    # 오래된 화면이 그 사이 들어온 다른 요청을 지우지 못한다.
    stale_delete = client.delete(
        "/api/v1/runtime-pins/requests/6f9619ff-8b86-4d01-b42d-00cf4fc964ff"
    )
    assert stale_delete.status_code == 404
    assert stale_delete.json()["detail"]["code"] == "RUNTIME_PIN_REQUEST_NOT_FOUND"

    cancelled = client.delete(f"/api/v1/runtime-pins/requests/{created['request_id']}")

    assert cancelled.status_code == 200
    assert cancelled.json() == {"status": "cancelled", "request_id": created["request_id"]}
    assert client.get("/api/v1/runtime-pins").json()["pending_request"] is None


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_a_pending_request_stays_visible_when_the_registry_is_unreadable(
    mock_read, isolated_pin_requests
):
    """id를 볼 수 없으면 취소도 못 한다 — 정작 그때 가장 필요한 정보다."""

    login_client()
    mock_read.return_value = _published_pins()
    created = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": "d" * 40, "reason": "새 후보 커밋"},
    ).json()["request"]

    mock_read.return_value = {"status": "unknown", "source": None, "detail": "unreadable"}
    body = client.get("/api/v1/runtime-pins").json()

    assert body["status"] == "unknown"
    assert body["pins"] is None
    assert body["pending_request"]["request_id"] == created["request_id"]
    # base를 대조할 값이 없으므로 'pending'이라고 단정하지도 않는다.
    assert body["pending_request"]["status"] == "pending"


@patch("kor_travel_docker_manager.api.routes.read_published_runtime_pins")
def test_a_rejected_cancel_is_audited(mock_read, isolated_pin_requests):
    """남지 않은 거부는 조사할 수 없다."""

    login_client()
    mock_read.return_value = _published_pins()

    response = client.delete(
        "/api/v1/runtime-pins/requests/6f9619ff-8b86-4d01-b42d-00cf4fc964ff"
    )

    assert response.status_code == 404
    events = client.get(
        "/api/v1/admin/login-audit-events?event_type=runtime_pin&outcome=rejected"
    ).json()
    assert any(
        (event.get("detail") or {}).get("code") == "RUNTIME_PIN_REQUEST_NOT_FOUND"
        for event in events
    )


def test_runtime_pin_request_routes_require_authentication():
    client.cookies.clear()

    created = client.post(
        "/api/v1/runtime-pins/requests",
        json={"role": "map", "revision": "d" * 40, "reason": "새 후보 커밋"},
    )
    cancelled = client.delete("/api/v1/runtime-pins/requests/anything")

    assert created.status_code == 401
    assert cancelled.status_code == 401


@patch("kor_travel_docker_manager.api.routes.read_deployment_readiness")
def test_get_deployment_readiness_returns_the_service_payload(mock_read):
    login_client()
    mock_read.return_value = {
        "schema": "kor-travel-docker-manager.deployment-readiness.v1",
        "generated_at": "2026-08-28T00:00:00Z",
        "cached": False,
        "cache_age_seconds": 0.0,
        "summary": {"state": "blocked", "blocking_count": 1, "warn_count": 0,
                    "unknown_count": 0, "text": "지금 재구축을 실행하면 실패합니다."},
        "checks": [
            {"id": "compose_single_file", "state": "missing", "label_ko": "Compose 입력이 단일 파일인가",
             "detail": "override가 있습니다", "source": "project_root", "evidence": {}}
        ],
        "unavailable_checks": [{"id": "offline_wheelhouse", "label_ko": "x", "reason": "y"}],
    }

    response = client.get("/api/v1/deployment-readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["state"] == "blocked"
    assert body["checks"][0]["id"] == "compose_single_file"
    assert body["unavailable_checks"][0]["id"] == "offline_wheelhouse"


@patch("kor_travel_docker_manager.api.routes.read_deployment_readiness")
def test_get_deployment_readiness_does_not_500_on_an_unreadable_host(mock_read):
    """진단 패널이 500을 내면 운영자는 상태를 볼 유일한 창을 잃는다."""

    login_client()
    mock_read.return_value = {
        "schema": "kor-travel-docker-manager.deployment-readiness.v1",
        "generated_at": "2026-08-28T00:00:00Z",
        "cached": False,
        "cache_age_seconds": 0.0,
        "summary": {"state": "unverified", "blocking_count": 0, "warn_count": 0,
                    "unknown_count": 3, "text": "일부 항목을 확인하지 못했습니다."},
        "checks": [],
        "unavailable_checks": [],
    }

    response = client.get("/api/v1/deployment-readiness")

    assert response.status_code == 200
    assert response.json()["summary"]["state"] == "unverified"


def test_get_deployment_readiness_requires_authentication():
    client.cookies.clear()

    response = client.get("/api/v1/deployment-readiness")

    assert response.status_code == 401


@patch("kor_travel_docker_manager.api.routes.collect_source_status")
def test_get_source_status_returns_the_card(mock_collect):
    login_client()
    mock_collect.return_value = {
        "schema": "ktdm.source-status.v1",
        "collected_at": "2026-08-28T00:00:00Z",
        "cached": False,
        "summary": {"level": "ok", "text": "최신 상태입니다", "next_action": ""},
        "manager": {"state": "recorded"},
        "checkouts": [],
        "running_images": [],
        "contracts": [],
        "environment": {"state": "complete"},
    }

    response = client.get("/api/v1/source-status")

    assert response.status_code == 200
    assert response.json()["summary"]["level"] == "ok"
    mock_collect.assert_called_once_with(force_refresh=False)


@patch("kor_travel_docker_manager.api.routes.collect_source_status")
def test_get_source_status_honours_refresh(mock_collect):
    login_client()
    mock_collect.return_value = {"schema": "ktdm.source-status.v1", "summary": {"level": "ok"}}

    client.get("/api/v1/source-status?refresh=true")

    mock_collect.assert_called_once_with(force_refresh=True)


def test_get_source_status_requires_authentication():
    client.cookies.clear()

    response = client.get("/api/v1/source-status")

    assert response.status_code == 401


@patch("kor_travel_docker_manager.api.routes.read_disk_usage")
def test_get_disk_usage_returns_plain_language_summary(mock_read):
    login_client()
    mock_read.return_value = {
        "schema": "kor-travel-docker-manager.disk-usage.v1",
        "collected_at": "2026-08-28T00:00:00Z",
        "cached": False,
        "state": "warn",
        "rows": [{"type": "Images", "label_ko": "이미지"}],
        "reclaimable_bytes": 30 * 1024**3,
        "summary": {
            "state": "warn",
            "text": "정리 시 약 30.0 GB 확보 가능",
            "detail": "회수 가능한 용량이 큽니다.",
            "next_action": "sudo -n docker system prune --all --volumes",
        },
    }

    response = client.get("/api/v1/system/disk-usage")

    assert response.status_code == 200
    assert response.json()["summary"]["text"].startswith("정리 시 약")
    mock_read.assert_called_once_with(force_refresh=False)


def test_get_disk_usage_requires_authentication():
    client.cookies.clear()

    response = client.get("/api/v1/system/disk-usage")

    assert response.status_code == 401
