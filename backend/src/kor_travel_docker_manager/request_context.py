"""요청 상관관계 ID — 로그·감사·오류 응답을 하나의 키로 잇는다(GM-16).

`main.py`의 미들웨어가 요청마다 값을 설정하고, 이 모듈이 정의하는 로그
필터가 그 값을 모든 로그 레코드에 주입한다(어느 서브모듈 로거에서 왔든
상관없다 — 필터는 핸들러에 붙이므로 propagate로 도달하는 모든 레코드를
본다). `auth_service.py`의 감사 기록과 `main.py`의 계약 위반 예외 핸들러도
같은 값을 읽어 각자의 출력에 남긴다.

`contextvars.ContextVar`는 `asyncio.to_thread`가 현재 컨텍스트를 복사해서
실행하므로(3.9+ 표준 동작), 요청 처리 중 스레드로 내려간 동기 코드(감사
기록, threadpool sync 라우트)에서도 같은 값을 그대로 읽는다 — 호출부마다
값을 수동으로 전달할 필요가 없다.

세 소비자(main.py/auth_service.py/routes.py)가 순환 import 없이 값을
주고받게 하려고 별도 모듈로 뺐다(yaml_strict.py와 같은 이유의 패턴).
"""

from __future__ import annotations

import contextvars
import logging

REQUEST_ID_HEADER = "X-Request-ID"
_NO_REQUEST_ID = "-"

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=_NO_REQUEST_ID
)


def current_request_id() -> str:
    """현재 컨텍스트의 request id. HTTP 요청 밖(백그라운드 루프 등)에서는 "-"."""

    return request_id_var.get()


class RequestIdLogFilter(logging.Filter):
    """모든 로그 레코드에 `request_id` 속성을 주입한다.

    로거가 아니라 핸들러에 붙여야 한다 — propagate로 조상 로거에 도달한
    레코드는 그 조상 로거 자신의 필터를 다시 타지 않고, 그 로거에 달린
    핸들러의 필터만 거친다."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True
