import datetime
import os
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# 몽키 패칭을 위해 임시 인메모리 DB 엔진 및 세션 생성
test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

import kor_travel_docker_manager.database
from kor_travel_docker_manager.services.auth_service import hash_password_for_env

FRONTEND_ORIGIN = "http://localhost:12905"
os.environ["KTDM_ADMIN_USERNAME"] = "admin"
os.environ["KTDM_ADMIN_PASSWORD_HASH"] = hash_password_for_env("ad.min")
os.environ["KTDM_SESSION_SECRET"] = "test-session-secret-minimum-32-bytes-value"
os.environ["KTDM_FRONTEND_ORIGINS"] = FRONTEND_ORIGIN

kor_travel_docker_manager.database.engine = test_engine
kor_travel_docker_manager.database.SessionLocal = TestSessionLocal

from kor_travel_docker_manager.main import app
from kor_travel_docker_manager.models import Base, Metric
from kor_travel_docker_manager.services.metrics_service import metrics_service

client = TestClient(app)
client.headers.update({"Origin": FRONTEND_ORIGIN})


def login_client():
    client.cookies.clear()
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "ad.min", "next": "/"},
    )
    assert login_response.status_code == 200


def clear_metrics():
    with kor_travel_docker_manager.database.get_db_session() as session:
        session.query(Metric).delete()
        session.commit()


def test_metrics_service_save_and_retrieve():
    # 테이블 스키마 초기화
    Base.metadata.create_all(bind=test_engine)
    
    # 1. Test save_metric
    metrics_service.save_metric("test-container", 12.5, 1024, 4096, 25.0, 100, 200)
    
    # 2. Test get_recent_metrics
    metrics = metrics_service.get_recent_metrics("test-container", hours=1)
    
    assert len(metrics) == 1
    assert metrics[0]["cpu_pct"] == 12.5
    assert metrics[0]["mem_usage"] == 1024
    assert metrics[0]["mem_limit"] == 4096
    assert metrics[0]["mem_pct"] == 25.0
    assert metrics[0]["io_read"] == 100
    assert metrics[0]["io_write"] == 200
    
    # 테이블 데이터 삭제
    clear_metrics()

def test_metrics_service_cleanup():
    Base.metadata.create_all(bind=test_engine)
    
    # 현재 시점 메트릭 저장
    metrics_service.save_metric("test-container", 10.0, 100, 200, 50.0, 10, 20)
    
    # 31일 전 오래된 메트릭 저장 (직접 세션을 열어 timestamp 강제 설정)
    with kor_travel_docker_manager.database.get_db_session() as session:
        old_metric = Metric(
            container_id="test-container",
            timestamp=datetime.datetime.utcnow() - datetime.timedelta(days=31),
            cpu_pct=20.0,
            mem_usage=200,
            mem_limit=200,
            mem_pct=100.0,
            io_read=50,
            io_write=50
        )
        session.add(old_metric)
        session.commit()
    
    # 클린업 전 데이터 개수 확인 (2개)
    with kor_travel_docker_manager.database.get_db_session() as session:
        count_before = session.query(Metric).count()
        assert count_before == 2
        
    # 30일 데이터 기준 클린업 수행
    metrics_service.cleanup_old_metrics(days=30)
    
    # 클린업 후 데이터 개수 확인 (신규 메트릭 1개만 잔존)
    with kor_travel_docker_manager.database.get_db_session() as session:
        count_after = session.query(Metric).count()
        assert count_after == 1
        
    clear_metrics()

@patch("kor_travel_docker_manager.api.routes.metrics_service")
def test_metrics_api_route(mock_metrics_service):
    login_client()
    # Setup mock return data
    mock_metrics_service.get_recent_metrics.return_value = [
        {"timestamp": "2026-06-11 12:00:00", "cpu_pct": 5.5, "mem_usage": 100, "mem_limit": 200, "mem_pct": 50.0, "io_read": 10, "io_write": 10}
    ]
    
    # 버저닝 반영 경로 (/api/v1/...) 검증
    response = client.get("/api/v1/containers/test-container/metrics?hours=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["cpu_pct"] == 5.5
    assert data[0]["io_read"] == 10
    mock_metrics_service.get_recent_metrics.assert_called_once_with("test-container", hours=1)
