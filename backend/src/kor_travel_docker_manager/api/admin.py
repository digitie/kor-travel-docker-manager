from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from kor_travel_docker_manager.services.admin_password_service import (
    MIN_NEW_PASSWORD_LENGTH,
    AdminPasswordError,
    change_admin_password,
    pinned_rebuild_guard_state,
)
from kor_travel_docker_manager.services.auth_service import (
    AdminSessionContext,
    admin_username,
    check_login_rate_limit,
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


class AdminPasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=MIN_NEW_PASSWORD_LENGTH, max_length=200)
    # 미종결 rebuild journal을 backend가 **증명하지 못할 때만** 필요한 명시 승인.
    # 증명된 미종결 journal은 이 플래그로도 통과하지 못한다.
    acknowledge_pinned_rebuild_invalidation: bool = Field(default=False)


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


@router.get("/password/preflight")
def get_admin_password_preflight(
    _session: Annotated[AdminSessionContext, Depends(require_admin_session)],
):
    """UI가 폼을 그리기 전에 rebuild journal 가드 상태를 먼저 읽는다.

    눌러 본 뒤에야 거부를 알게 하지 않기 위한 읽기 전용 route다."""
    return pinned_rebuild_guard_state()


@router.post("/password")
def post_admin_password(
    payload: AdminPasswordChangeRequest,
    request: Request,
    session: Annotated[AdminSessionContext, Depends(require_admin_session)],
):
    """관리자 비밀번호를 `.env` 단일 키로 회전한다.

    현재 비밀번호 재검증이 typed confirmation 역할을 하지만, 세션을 쥔 상대가 현재
    비밀번호를 무제한 시도하는 것은 막아야 한다. 로그인과 같은 durable 카운터를 쓰려면
    실패 행의 `event_type`이 `login`이어야 하므로 그 경우에만 그렇게 기록한다."""
    retry_after = check_login_rate_limit(request)
    if retry_after is not None:
        record_login_audit_event(
            request,
            event_type="login",
            outcome="denied",
            attempted_username=session.username,
            reason="rate_limited",
            session_id_hash=session.session_id_hash,
        )
        raise HTTPException(
            status_code=429,
            detail="RATE_LIMITED",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        result = change_admin_password(
            current_password=payload.current_password,
            new_password=payload.new_password,
            acknowledge_pinned_rebuild_invalidation=(
                payload.acknowledge_pinned_rebuild_invalidation
            ),
        )
    except AdminPasswordError as exc:
        record_login_audit_event(
            request,
            # 잘못된 현재 비밀번호만 로그인 카운터에 합류시킨다. 나머지 거부는 자격증명
            # 추측이 아니므로 브루트포스 카운터를 오염시키면 안 된다.
            event_type="login" if exc.code == "INVALID_CREDENTIALS" else "admin_password",
            outcome="denied",
            attempted_username=admin_username(),
            reason=(
                "invalid_credentials"
                if exc.code == "INVALID_CREDENTIALS"
                else exc.code.lower()[:80]
            ),
            session_id_hash=session.session_id_hash,
            detail={"code": exc.code},
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    # 비밀번호도 해시도 감사에 넣지 않는다 — 판정 문자열과 불리언뿐이다.
    record_login_audit_event(
        request,
        event_type="admin_password",
        outcome="succeeded",
        attempted_username=session.username,
        reason="admin_password_changed",
        session_id_hash=session.session_id_hash,
        detail={"guard": result["guard"], "acknowledged": result["acknowledged"]},
    )
    return {"ok": True, "guard": result["guard"]}
