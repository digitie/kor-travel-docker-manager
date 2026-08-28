from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from kor_travel_docker_manager.services.auth_service import require_admin_session
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.compose_service import compose_service
from kor_travel_docker_manager.services.docker_service import (
    ContainerConfigValidationError,
    docker_service,
)
from kor_travel_docker_manager.services.metrics_service import metrics_service
from kor_travel_docker_manager.services.registry import list_targets
from kor_travel_docker_manager.services.runtime_pin_registry import (
    read_published_runtime_pins,
)
from kor_travel_docker_manager.services.standalone_backup import (
    BACKUP_ROLES,
    StandaloneBackupError,
    list_standalone_backups,
)

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


@router.get("/backups")
def get_backups(role: str | None = Query(default=None)):
    """Read-only standalone DB backup listing (issue #177). `create`/`gc` stay
    CLI-only (`ktdctl db-backup ...`) and are not exposed here. Restore isn't
    implemented anywhere yet (CLI or API) — this route only lists what exists."""
    roles = BACKUP_ROLES if role is None else (role,)
    if role is not None and role not in BACKUP_ROLES:
        raise HTTPException(status_code=400, detail=f"unknown backup role: {role}")
    backups: list[dict[str, Any]] = []
    for backup_role in roles:
        try:
            backups.extend(
                manifest.to_json() for manifest in list_standalone_backups(backup_role)
            )
        except StandaloneBackupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    backups.sort(key=lambda item: item["created_at_unix"])
    return {"backups": backups}


@router.get("/runtime-pins")
def get_runtime_pins():
    """Read-only view of the Map/PinVi runtime pin registry.

    Rotation stays root-only via `ktdctl pin rotate` — the API process cannot write
    the registry, and that boundary is the cheapest safety we have. When the
    published copy is missing or malformed the payload says `unknown` instead of
    guessing a value."""
    payload = read_published_runtime_pins()
    if payload.get("status") != "ok":
        return {
            "status": payload.get("status", "unknown"),
            "source": payload.get("source"),
            "detail": payload.get("detail"),
            "pins": None,
        }
    blocked = payload.get("blocked_pinsets", [])
    pinset_sha256 = payload.get("pinset_sha256")
    current_is_blocked = any(
        entry.get("pinset_sha256") == pinset_sha256 for entry in blocked
    )
    return {
        "status": "ok",
        "source": payload.get("source"),
        "published_at": payload.get("published_at"),
        "pins": {
            "release_version": payload.get("release_version"),
            "pinset_sha256": pinset_sha256,
            "sources": payload.get("sources", []),
            "rotated_at": payload.get("rotated_at"),
            "rotated_by": payload.get("rotated_by"),
            "reason": payload.get("reason"),
        },
        "lifecycle": {
            "current_pinset_is_blocked": current_is_blocked,
            "blocked_pinsets": blocked,
            "history": payload.get("history", []),
        },
        "summary": _runtime_pin_summary(
            current_is_blocked=current_is_blocked,
            rotated_at=payload.get("rotated_at"),
        ),
    }


def _runtime_pin_summary(
    *,
    current_is_blocked: bool,
    rotated_at: str | None,
) -> dict[str, str]:
    """Plain-language status for operators who do not read digests."""
    if current_is_blocked:
        return {
            "state": "action_required",
            "text": (
                "현재 고정된 pinset은 재시도가 금지된 candidate입니다. "
                "새 revision으로 회전해야 재구축할 수 있습니다."
            ),
            "next_action": "ktdctl pin rotate --role <map|pinvi> --revision <40-hex> --confirm",
        }
    return {
        "state": "ok",
        "text": f"고정된 pinset이 정상 등록돼 있습니다. 마지막 회전: {rotated_at or '알 수 없음'}",
        "next_action": "",
    }


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
    except ContainerConfigValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
