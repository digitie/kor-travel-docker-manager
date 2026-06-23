from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from kor_travel_docker_manager.services.auth_service import require_admin_session
from kor_travel_docker_manager.services.compose_service import compose_service
from kor_travel_docker_manager.services.docker_service import docker_service
from kor_travel_docker_manager.services.metrics_service import metrics_service
from kor_travel_docker_manager.services.registry import list_targets

router = APIRouter(dependencies=[Depends(require_admin_session)])


class ActionRequest(BaseModel):
    action: str = Field(..., description="Action to perform: 'start', 'stop', or 'restart'")


class EnsureTargetRequest(BaseModel):
    build: bool = Field(False, description="Run docker compose up with --build")
    recreate: bool = Field(False, description="Run docker compose up with --force-recreate")


class ContainerConfigUpdate(BaseModel):
    ports: list[Any] = Field(..., description="Compose ports list, e.g. ['5432:5432']")
    env: dict[str, Any] = Field(..., description="Compose environment variables dict")
    volumes: list[Any] = Field(..., description="Compose volumes list")
    networks: list[str] = Field(..., description="Compose networks list, e.g. ['default']")


@router.get("/targets")
def get_targets():
    """Retrieve application-oriented infrastructure targets for UI and CLI parity."""
    return list_targets()


@router.post("/targets/{target}/ensure")
def ensure_target(target: str, payload: EnsureTargetRequest):
    """Ensure a dependency target is running through docker compose."""
    try:
        result = compose_service.ensure_target(
            target,
            build=payload.build,
            recreate=payload.recreate,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail={
                "message": "docker compose ensure failed",
                "stderr": result.get("stderr"),
                "command": result.get("command"),
            },
        )

    return result


@router.get("/containers")
def list_containers():
    """Retrieve state and ports for managed PostgreSQL and RustFS services."""
    try:
        return docker_service.get_containers_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/containers/{container_id}/action")
def control_container(container_id: str, payload: ActionRequest):
    """Start, stop, or restart a specific infrastructure service."""
    action = payload.action.lower()
    if action not in ["start", "stop", "restart"]:
        raise HTTPException(status_code=400, detail="Action must be start, stop, or restart")

    result = docker_service.control_container(container_id, action)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    return {"status": "success", "message": result.get("message")}


@router.get("/containers/{container_id}/logs")
def get_container_logs(container_id: str, tail: int = 100):
    """Retrieve recent console output logs from a container."""
    result = docker_service.get_container_logs(container_id, tail=tail)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    return {"logs": result.get("logs")}


@router.get("/containers/{container_id}/inspect")
def inspect_container(container_id: str):
    """Retrieve a sanitized Docker inspect summary for a managed container."""
    result = docker_service.inspect_container(container_id)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result.get("container")


@router.post("/containers/{container_id}/config")
def update_container_config(container_id: str, payload: ContainerConfigUpdate):
    """Update container configurations (docker-compose) and recreate the container using Docker SDK."""
    result = docker_service.update_container_config(
        container_id, payload.ports, payload.env, payload.volumes, payload.networks
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    return {"status": "success", "message": result.get("message")}


@router.post("/containers/{container_id}/reset")
def reset_container_config(container_id: str):
    """Reset container configurations to default and recreate the container using Docker SDK."""
    result = docker_service.reset_container_config(container_id)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    return {"status": "success", "message": result.get("message")}


@router.get("/containers/{container_id}/metrics")
def get_container_metrics_history(container_id: str, hours: int = 1):
    """Retrieve historical metrics (CPU, Memory, IO) for a container over the last N hours."""
    try:
        return metrics_service.get_recent_metrics(container_id, hours=hours)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
