from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from tripmate_manager.services.docker_service import docker_service

router = APIRouter()

class ActionRequest(BaseModel):
    action: str = Field(..., description="Action to perform: 'start', 'stop', or 'restart'")

@router.get("/containers")
def list_containers():
    """Retrieve state and ports for TripMate PostgreSQL and RustFS services."""
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

@router.get("/containers/{container_id}/logs")
def get_container_logs(container_id: str, tail: int = 100):
    """Retrieve recent console output logs from a container."""
    result = docker_service.get_container_logs(container_id, tail=tail)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))
        
    return {"logs": result.get("logs")}
