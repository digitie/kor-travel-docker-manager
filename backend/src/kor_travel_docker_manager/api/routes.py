from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from kor_travel_docker_manager.services.auth_service import require_admin_session
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
)
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
    volumes: list[Any] = Field(
        ...,
        description=(
            "Immutable Compose volumes list; callers must echo the current exact value"
        ),
    )
    networks: list[str] = Field(..., description="Compose networks list, e.g. ['default']")


def _config_failure_detail(result: dict[str, Any]) -> dict[str, Any]:
    detail = {
        "message": result.get("error"),
        "restoration": result.get("restoration"),
    }
    for field in ("command", "returncode", "stdout", "stderr"):
        if field in result:
            detail[field] = result.get(field)
    return detail


def _candidate_contract_detail(
    error: ComposeCandidateContractError,
) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": str(error),
        "stage": "candidate_validation",
        "mutation_applied": False,
    }


def _post_mutation_contract_detail(
    error: ComposePostMutationContractError,
) -> dict[str, Any]:
    original_code = getattr(error.original_error, "code", None)
    return {
        "code": error.code,
        "message": str(error),
        "stage": "post_mutation_recovery",
        "mutation_applied": True,
        "original_error": {
            "code": original_code,
            "message": str(error.original_error),
        },
        "recovery_attempted": error.recovery_attempted,
        "recovery_succeeded": error.recovery_succeeded,
        "recovery_error": error.recovery_error,
        "restoration": error.restoration,
    }


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
    except ComposePostMutationContractError as exc:
        raise HTTPException(
            status_code=500, detail=_post_mutation_contract_detail(exc)
        ) from exc
    except ComposeCandidateContractError as exc:
        raise HTTPException(
            status_code=409, detail=_candidate_contract_detail(exc)
        ) from exc
    except DeploymentContractError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
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

    try:
        result = docker_service.control_container(container_id, action)
    except ComposePostMutationContractError as exc:
        raise HTTPException(
            status_code=500, detail=_post_mutation_contract_detail(exc)
        ) from exc
    except ComposeCandidateContractError as exc:
        raise HTTPException(
            status_code=409, detail=_candidate_contract_detail(exc)
        ) from exc
    except DeploymentContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.get("success"):
        detail: Any = result.get("error")
        if "restoration" in result:
            detail = _config_failure_detail(result)
        raise HTTPException(status_code=500, detail=detail)

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
    try:
        result = docker_service.update_container_config(
            container_id, payload.ports, payload.env, payload.volumes, payload.networks
        )
    except ComposePostMutationContractError as exc:
        raise HTTPException(
            status_code=500, detail=_post_mutation_contract_detail(exc)
        ) from exc
    except ComposeCandidateContractError as exc:
        raise HTTPException(
            status_code=409, detail=_candidate_contract_detail(exc)
        ) from exc
    except DeploymentContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=_config_failure_detail(result))

    return {"status": "success", "message": result.get("message")}


@router.post("/containers/{container_id}/reset")
def reset_container_config(container_id: str):
    """Reset container configurations to default and recreate the container using Docker SDK."""
    try:
        result = docker_service.reset_container_config(container_id)
    except ComposePostMutationContractError as exc:
        raise HTTPException(
            status_code=500, detail=_post_mutation_contract_detail(exc)
        ) from exc
    except ComposeCandidateContractError as exc:
        raise HTTPException(
            status_code=409, detail=_candidate_contract_detail(exc)
        ) from exc
    except DeploymentContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=_config_failure_detail(result))

    return {"status": "success", "message": result.get("message")}


@router.get("/containers/{container_id}/metrics")
def get_container_metrics_history(container_id: str, hours: int = 1):
    """Retrieve historical metrics (CPU, Memory, IO) for a container over the last N hours."""
    try:
        return metrics_service.get_recent_metrics(container_id, hours=hours)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
