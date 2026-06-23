from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from kor_travel_docker_manager.services.auth_service import (
    AdminSessionContext,
    list_login_audit_events,
    record_login_audit_event,
    require_admin_session,
)
from kor_travel_docker_manager.services.public_api_key_service import (
    create_public_api_key,
    list_public_api_keys,
    revoke_public_api_key,
)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
)


class PublicApiKeyCreateRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)


@router.get("/login-audit-events")
def get_login_audit_events(
    _session: Annotated[AdminSessionContext, Depends(require_admin_session)],
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = Query(default=None, max_length=20),
    outcome: str | None = Query(default=None, max_length=20),
):
    return list_login_audit_events(limit=limit, event_type=event_type, outcome=outcome)


@router.get("/public-api-keys")
def get_public_api_keys(
    _session: Annotated[AdminSessionContext, Depends(require_admin_session)],
    limit: int = Query(default=100, ge=1, le=500),
):
    return list_public_api_keys(limit=limit)


@router.post("/public-api-keys")
def post_public_api_key(
    payload: PublicApiKeyCreateRequest,
    request: Request,
    session: Annotated[AdminSessionContext, Depends(require_admin_session)],
):
    result = create_public_api_key(label=payload.label, created_by=session.username)
    item = result["item"]
    record_login_audit_event(
        request,
        event_type="api_key",
        outcome="succeeded",
        attempted_username=session.username,
        reason="public_api_key_created",
        session_id_hash=session.session_id_hash,
        detail={"label": payload.label, "key_hint": item["key_hint"]},
    )
    return result


@router.delete("/public-api-keys/{public_api_key_id}")
def delete_public_api_key(
    public_api_key_id: str,
    request: Request,
    session: Annotated[AdminSessionContext, Depends(require_admin_session)],
):
    try:
        result = revoke_public_api_key(public_api_key_id, revoked_by=session.username)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="PUBLIC_API_KEY_NOT_FOUND") from exc
    record_login_audit_event(
        request,
        event_type="api_key",
        outcome="succeeded",
        attempted_username=session.username,
        reason="public_api_key_revoked",
        session_id_hash=session.session_id_hash,
        detail={"key_hint": result["key_hint"]},
    )
    return result
