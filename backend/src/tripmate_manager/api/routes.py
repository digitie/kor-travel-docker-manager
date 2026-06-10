from typing import Dict, List, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from tripmate_manager.services.docker_service import docker_service

router = APIRouter()

class ActionRequest(BaseModel):
    action: str = Field(..., description="Action to perform: 'start', 'stop', or 'restart'")

@router.get("/containers")
def list_containers():
    """Retrieve state and ports for managed PostgreSQL and RustFS services."""
    try:
        return docker_service.get_containers_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

class ContainerConfigUpdate(BaseModel):
    ports: List[Any] = Field(..., description="Compose ports list, e.g. ['55432:5432']")
    env: Dict[str, Any] = Field(..., description="Compose environment variables dict, e.g. {'POSTGRES_PASSWORD': 'xyz'}")
    volumes: List[Any] = Field(..., description="Compose volumes list, e.g. ['tripmate-pgdata:/var/lib/postgresql/data']")
    networks: List[str] = Field(..., description="Compose networks list, e.g. ['default']")

@router.get("/containers/{container_id}/logs")
def get_container_logs(container_id: str, tail: int = 100):
    """Retrieve recent console output logs from a container."""
    result = docker_service.get_container_logs(container_id, tail=tail)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))
        
    return {"logs": result.get("logs")}

@router.post("/containers/{container_id}/config")
def update_container_config(container_id: str, payload: ContainerConfigUpdate):
    """Update container configurations (docker-compose) and recreate the container using Docker SDK."""
    result = docker_service.update_container_config(
        container_id, 
        payload.ports, 
        payload.env, 
        payload.volumes, 
        payload.networks
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



