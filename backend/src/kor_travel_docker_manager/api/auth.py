from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from kor_travel_docker_manager.services.auth_service import (
    SESSION_COOKIE_NAME,
    AdminSessionContext,
    admin_username,
    check_login_rate_limit,
    clear_login_failures,
    create_admin_session,
    expire_admin_cookie,
    record_login_audit_event,
    record_login_failure,
    require_admin_session,
    require_frontend_origin,
    revoke_admin_session,
    sanitize_local_path,
    verify_admin_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)
    next: str | None = Field(default="/", max_length=500)


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response):
    require_frontend_origin(request)
    next_path = sanitize_local_path(payload.next)
    retry_after = check_login_rate_limit(request)
    if retry_after is not None:
        record_login_audit_event(
            request,
            event_type="login",
            outcome="denied",
            attempted_username=payload.username,
            reason="rate_limited",
            next_path=next_path,
        )
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(status_code=429, detail="RATE_LIMITED")

    result = verify_admin_password(payload.username, payload.password)
    if result == "misconfigured":
        # 오설정(시크릿 미설정) 상태에서도 미인증 호출이 무제한으로 감사 행을 쓰지 못하도록
        # 실패 카운트를 증가시킨다(상한 초과 시 위 rate-limit 분기에서 429로 차단됨).
        record_login_failure(request)
        record_login_audit_event(
            request,
            event_type="login",
            outcome="failed",
            attempted_username=payload.username,
            reason="misconfigured",
            next_path=next_path,
        )
        raise HTTPException(status_code=503, detail="AUTH_MISCONFIGURED")
    if result != "ok":
        record_login_failure(request)
        record_login_audit_event(
            request,
            event_type="login",
            outcome="denied",
            attempted_username=payload.username,
            reason="invalid_credentials",
            next_path=next_path,
        )
        raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")

    clear_login_failures(request)
    old_session_hash = revoke_admin_session(request.cookies.get(SESSION_COOKIE_NAME), request)
    session = create_admin_session(request, response, admin_username())
    record_login_audit_event(
        request,
        event_type="login",
        outcome="succeeded",
        attempted_username=payload.username,
        reason="authenticated",
        next_path=next_path,
        session_id_hash=session.session_id_hash,
        detail={"replaced_session": old_session_hash is not None},
    )
    return {"ok": True, "username": session.username, "next": next_path}


@router.post("/logout")
def logout(request: Request, response: Response):
    require_frontend_origin(request)
    session_hash = revoke_admin_session(request.cookies.get(SESSION_COOKIE_NAME), request)
    expire_admin_cookie(request, response)
    # 유효 서명된 세션 쿠키가 제시된 경우에만 감사 행을 남긴다. 쿠키 없는/위조된 미인증
    # 호출이 logout으로 감사 테이블을 무제한 적재하는 것을 막는다.
    if session_hash is not None:
        record_login_audit_event(
            request,
            event_type="logout",
            outcome="succeeded",
            attempted_username=admin_username(),
            reason="user_logout",
            session_id_hash=session_hash,
        )
    return {"ok": True}


@router.get("/me")
def me(session: Annotated[AdminSessionContext, Depends(require_admin_session)]):
    return {
        "authenticated": True,
        "username": session.username,
        "expires_at": session.expires_at.isoformat(),
    }
