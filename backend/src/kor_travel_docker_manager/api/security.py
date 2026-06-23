from typing import Annotated

from fastapi import HTTPException, Query, Request

from kor_travel_docker_manager.services.auth_service import (
    SESSION_COOKIE_NAME,
    validate_session_cookie,
)
from kor_travel_docker_manager.services.public_api_key_service import (
    PUBLIC_API_KEY_QUERY_PARAM,
    public_api_key_is_valid,
)


def require_public_api_key(
    request: Request,
    key: Annotated[str | None, Query(alias=PUBLIC_API_KEY_QUERY_PARAM)] = None,
) -> None:
    """외부 공개 API용 VWorld 호환 key 검증 dependency.

    현재 manager에는 공개 API surface가 없지만, 향후 외부 노출 endpoint는 이
    dependency를 붙이면 된다. 로그인된 신뢰 UI 요청은 key 검증을 생략한다.
    """
    if validate_session_cookie(request.cookies.get(SESSION_COOKIE_NAME), request) is not None:
        return
    if key is None or not public_api_key_is_valid(key):
        raise HTTPException(status_code=401, detail="INVALID_API_KEY")
