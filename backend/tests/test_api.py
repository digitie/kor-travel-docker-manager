import hashlib
import os
from unittest.mock import patch

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import kor_travel_docker_manager.database
from kor_travel_docker_manager.services.auth_service import hash_password_for_env
from kor_travel_docker_manager.services.public_api_key_service import public_api_key_is_valid

FRONTEND_ORIGIN = "http://localhost:12905"
os.environ["KTDM_ADMIN_USERNAME"] = "admin"
os.environ["KTDM_ADMIN_PASSWORD_HASH"] = hash_password_for_env("ad.min")
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

from kor_travel_docker_manager.main import app

client = TestClient(app)
client.headers.update({"Origin": FRONTEND_ORIGIN})


def login_client():
    client.cookies.clear()
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "ad.min", "next": "/"},
    )
    assert login_response.status_code == 200


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "kor-travel-docker-manager-backend"}


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


def test_ws_status_requires_session():
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            "/api/v1/ws/status", headers={"Origin": FRONTEND_ORIGIN}
        ):
            pass
    assert excinfo.value.code == 4401


@patch("kor_travel_docker_manager.api.websocket.docker_service")
def test_ws_status_accepts_authenticated_session(mock_docker_service):
    mock_docker_service.get_containers_status.return_value = []
    login_client()
    with client.websocket_connect(
        "/api/v1/ws/status", headers={"Origin": FRONTEND_ORIGIN}
    ) as ws:
        message = ws.receive_json()
        assert message["type"] == "status"
        assert message["containers"] == []


def test_public_api_key_lifecycle():
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

    missing = client.delete("/api/v1/admin/public-api-keys/not-a-uuid")
    assert missing.status_code == 404


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
        "kor-travel-concierge-api",
        "kor-travel-concierge-mcp",
        "kor-travel-concierge-scheduler",
        "kor-travel-concierge-ui",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
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
                "kor-travel-concierge-api",
                "kor-travel-concierge-mcp",
                "kor-travel-concierge-scheduler",
                "kor-travel-concierge-ui",
                "kor-travel-map-api",
                "kor-travel-map-ui",
                "kor-travel-map-dagster",
                "kor-travel-map-dagster-daemon",
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
            "kor-travel-concierge-api",
            "kor-travel-concierge-mcp",
            "kor-travel-concierge-scheduler",
            "kor-travel-concierge-ui",
            "kor-travel-map-api",
            "kor-travel-map-ui",
            "kor-travel-map-dagster",
            "kor-travel-map-dagster-daemon",
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
