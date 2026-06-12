from unittest.mock import patch

from fastapi.testclient import TestClient

from kor_travel_docker_manager.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "kor-travel-docker-manager-backend"}


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_list_containers(mock_docker_service):
    # Setup mock status list
    mock_docker_service.get_containers_status.return_value = [
        {
            "id": "kraddr-geo-postgresql",
            "name": "kraddr-geo-postgres",
            "status": "running",
            "state": "running",
            "ports": ["5432:5432"],
        },
        {
            "id": "rustfs",
            "name": "tripmate-rustfs",
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
    assert data[0]["id"] == "kraddr-geo-postgresql"
    assert data[0]["status"] == "running"
    assert data[0]["ports"] == ["5432:5432"]
    assert data[1]["id"] == "rustfs"
    assert data[1]["status"] == "exited"


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_control_container_success(mock_docker_service):
    mock_docker_service.control_container.return_value = {
        "success": True,
        "message": "Successfully stopped kraddr-geo-postgres.",
    }

    # Target versioned route v1
    response = client.post(
        "/api/v1/containers/kraddr-geo-postgresql/action", json={"action": "stop"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Successfully stopped kraddr-geo-postgres.",
    }
    mock_docker_service.control_container.assert_called_once_with("kraddr-geo-postgresql", "stop")


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_control_container_invalid_action(mock_docker_service):
    # Target versioned route v1
    response = client.post(
        "/api/v1/containers/kraddr-geo-postgresql/action", json={"action": "invalid"}
    )
    assert response.status_code == 400
    assert "Action must be start, stop, or restart" in response.json()["detail"]


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_get_container_logs_success(mock_docker_service):
    mock_docker_service.get_container_logs.return_value = {
        "success": True,
        "logs": "Sample logs content",
    }

    # Target versioned route v1
    response = client.get("/api/v1/containers/kraddr-geo-postgresql/logs")
    assert response.status_code == 200
    assert response.json() == {"logs": "Sample logs content"}
    mock_docker_service.get_container_logs.assert_called_once_with(
        "kraddr-geo-postgresql", tail=100
    )


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_update_container_config_success(mock_docker_service):
    mock_docker_service.update_container_config.return_value = {
        "success": True,
        "message": "Successfully updated config and recreated kraddr-geo-postgres.",
    }

    # Target versioned route v1
    response = client.post(
        "/api/v1/containers/kraddr-geo-postgresql/config",
        json={
            "ports": ["5432:5432"],
            "env": {"POSTGRES_PASSWORD": "${KRADDR_GEO_POSTGRES_PASSWORD:-addr}"},
            "volumes": ["${KRADDR_GEO_PGDATA:-/tmp/pgdata}:/var/lib/postgresql/data"],
            "networks": ["default"],
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Successfully updated config and recreated kraddr-geo-postgres.",
    }
    mock_docker_service.update_container_config.assert_called_once_with(
        "kraddr-geo-postgresql",
        ["5432:5432"],
        {"POSTGRES_PASSWORD": "${KRADDR_GEO_POSTGRES_PASSWORD:-addr}"},
        ["${KRADDR_GEO_PGDATA:-/tmp/pgdata}:/var/lib/postgresql/data"],
        ["default"],
    )


@patch("kor_travel_docker_manager.api.routes.docker_service")
def test_reset_container_config_success(mock_docker_service):
    mock_docker_service.reset_container_config.return_value = {
        "success": True,
        "message": "Successfully updated config and recreated kraddr-geo-postgres.",
    }

    # Target versioned route v1
    response = client.post("/api/v1/containers/kraddr-geo-postgresql/reset")
    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Successfully updated config and recreated kraddr-geo-postgres.",
    }
    mock_docker_service.reset_container_config.assert_called_once_with("kraddr-geo-postgresql")


def test_get_targets():
    response = client.get("/api/v1/targets")
    assert response.status_code == 200
    data = response.json()
    assert [target["id"] for target in data[:7]] == [
        "db",
        "storage",
        "geo",
        "map",
        "ai",
        "main",
        "observability",
    ]
    assert data[5]["resolved_sequence"] == ["db", "storage", "geo", "map"]
    assert data[5]["resolved_services"] == [
        "kraddr-geo-postgres",
        "rustfs",
        "kraddr-geo-api",
        "kraddr-geo-ui",
    ]
    assert data[6]["resolved_services"][-3:] == ["cadvisor", "prometheus", "grafana"]
    assert any(target["id"] == "all" for target in data)


@patch("kor_travel_docker_manager.api.routes.compose_service")
def test_ensure_target_success(mock_compose_service):
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
                "kraddr-geo-postgres",
                "rustfs",
                "kraddr-geo-api",
                "kraddr-geo-ui",
            ]
        ],
        "stdout": "ok",
        "stderr": "",
        "target": "main",
        "target_sequence": ["db", "storage", "geo", "map"],
        "services": ["kraddr-geo-postgres", "rustfs", "kraddr-geo-api", "kraddr-geo-ui"],
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
    mock_docker_service.inspect_container.return_value = {
        "success": True,
        "container": {
            "id": "kraddr-geo-postgresql",
            "name": "kraddr-geo-postgres",
            "config": {"env": ["POSTGRES_PASSWORD=<redacted>"]},
        },
    }

    response = client.get("/api/v1/containers/kraddr-geo-postgresql/inspect")
    assert response.status_code == 200
    assert response.json()["config"]["env"] == ["POSTGRES_PASSWORD=<redacted>"]
