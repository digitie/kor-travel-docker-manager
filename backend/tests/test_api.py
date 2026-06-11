from unittest.mock import patch

from fastapi.testclient import TestClient

from tripmate_manager.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "tripmate-manager-backend"}

@patch("tripmate_manager.api.routes.docker_service")
def test_list_containers(mock_docker_service):
    # Setup mock status list
    mock_docker_service.get_containers_status.return_value = [
        {"id": "tripmate-postgresql", "name": "tripmate-postgres", "status": "running", "state": "running", "ports": ["55432:5432"]},
        {"id": "kraddr-geo-postgresql", "name": "kraddr-geo-postgres", "status": "running", "state": "running", "ports": ["15434:5432"]},
        {"id": "rustfs", "name": "tripmate-rustfs", "status": "exited", "state": "exited", "ports": []}
    ]

    # Target versioned route v1
    response = client.get("/api/v1/containers")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["id"] == "tripmate-postgresql"
    assert data[0]["status"] == "running"
    assert data[1]["id"] == "kraddr-geo-postgresql"
    assert data[1]["ports"] == ["15434:5432"]
    assert data[2]["id"] == "rustfs"
    assert data[2]["status"] == "exited"

@patch("tripmate_manager.api.routes.docker_service")
def test_control_container_success(mock_docker_service):
    mock_docker_service.control_container.return_value = {"success": True, "message": "Successfully stopped tripmate-postgres."}

    # Target versioned route v1
    response = client.post("/api/v1/containers/tripmate-postgresql/action", json={"action": "stop"})
    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Successfully stopped tripmate-postgres."}
    mock_docker_service.control_container.assert_called_once_with("tripmate-postgresql", "stop")

@patch("tripmate_manager.api.routes.docker_service")
def test_control_container_invalid_action(mock_docker_service):
    # Target versioned route v1
    response = client.post("/api/v1/containers/tripmate-postgresql/action", json={"action": "invalid"})
    assert response.status_code == 400
    assert "Action must be start, stop, or restart" in response.json()["detail"]

@patch("tripmate_manager.api.routes.docker_service")
def test_get_container_logs_success(mock_docker_service):
    mock_docker_service.get_container_logs.return_value = {"success": True, "logs": "Sample logs content"}
    
    # Target versioned route v1
    response = client.get("/api/v1/containers/postgresql/logs")
    assert response.status_code == 200
    assert response.json() == {"logs": "Sample logs content"}
    mock_docker_service.get_container_logs.assert_called_once_with("postgresql", tail=100)

@patch("tripmate_manager.api.routes.docker_service")
def test_update_container_config_success(mock_docker_service):
    mock_docker_service.update_container_config.return_value = {"success": True, "message": "Successfully updated config and recreated tripmate-postgres."}
    
    # Target versioned route v1
    response = client.post("/api/v1/containers/tripmate-postgresql/config", json={
        "ports": ["55432:5432"],
        "env": {"POSTGRES_PASSWORD": "new_password"},
        "volumes": ["tripmate-pgdata:/var/lib/postgresql/data"],
        "networks": ["default"]
    })
    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Successfully updated config and recreated tripmate-postgres."}
    mock_docker_service.update_container_config.assert_called_once_with(
        "tripmate-postgresql", 
        ["55432:5432"], 
        {"POSTGRES_PASSWORD": "new_password"},
        ["tripmate-pgdata:/var/lib/postgresql/data"],
        ["default"]
    )

@patch("tripmate_manager.api.routes.docker_service")
def test_reset_container_config_success(mock_docker_service):
    mock_docker_service.reset_container_config.return_value = {"success": True, "message": "Successfully updated config and recreated tripmate-postgres."}
    
    # Target versioned route v1
    response = client.post("/api/v1/containers/tripmate-postgresql/reset")
    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Successfully updated config and recreated tripmate-postgres."}
    mock_docker_service.reset_container_config.assert_called_once_with("tripmate-postgresql")
