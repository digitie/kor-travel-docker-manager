"""원격 target 상태 — 다른 호스트에 있는 target을 HTTP로만 본다(ADR-58 R4).

Manager 사이에는 SSH도 원격 Docker도 없다(ADR-58 결정 2). 그래서 이 호스트의 `status`가 다른 호스트의 target에
대해 말할 수 있는 것은 registry `connection:`(R1이 그 호스트 주소로 렌더한 것)의 `http(s)://` URL에 직접 요청한
결과뿐이다. 판정은 세 값이다:

- `up` — 2xx·3xx(리다이렉트는 따라가지 않는다 — 응답이 왔다는 사실이 판정이다)
- `down` — 연결은 됐으나 응답이 비정상(4xx·5xx, 연결 뒤 시간 초과·끊김, TLS 실패)
- `unreachable` — 연결 자체가 안 됨(거부·연결 시간 초과·이름 풀이 실패)

registry에는 방화벽 개방 여부 필드가 없다 — 다른 호스트의 HAProxy 전용 포트는 이 호스트에서 `unreachable`이
**정상**이다(`docs/docker-management.md` §3.2). `http(s)://`가 없는 connection(설명 문자열·gRPC·DB)은
`remote(not probed)`다.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Final

_URL: Final = re.compile(r"https?://[^\s)]+")
NOT_PROBED: Final = "remote(not probed)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401 - urllib hook
        return None


_OPENER: Final = urllib.request.build_opener(_NoRedirect)


def probe_url(url: str, *, timeout: float = 3.0) -> str:
    """URL 하나를 GET해 `up`/`down`/`unreachable`로 판정한다. 응답 본문은 읽지 않는다."""

    try:
        with _OPENER.open(urllib.request.Request(url, method="GET"), timeout=timeout) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except urllib.error.URLError as exc:
        # urllib은 연결·요청 송신 단계의 실패만 URLError로 감싼다. TLS 핸드셰이크 실패는 상대가 응답한 것이다.
        import ssl

        return "down" if isinstance(exc.reason, ssl.SSLError) else "unreachable"
    except (TimeoutError, OSError, ValueError):
        # 연결 뒤(응답 대기·읽기)의 시간 초과·끊김 — 상대는 있었다.
        return "down"
    return "up" if 200 <= status < 400 else "down"


def probe_connection(connection: object, *, timeout: float = 3.0) -> dict[str, Any]:
    """렌더된 connection 하나의 URL **전부**를 본다. URL이 없으면 `remote(not probed)`."""

    urls = _URL.findall(connection) if isinstance(connection, str) else []
    if not urls:
        return {"state": NOT_PROBED, "probes": []}
    probes = [{"url": url, "state": probe_url(url, timeout=timeout)} for url in urls]
    states = {probe["state"] for probe in probes}
    # 하나라도 나쁘면 그 값이 컨테이너의 값이다(unreachable이 down보다 앞선다 — 더 모르는 쪽).
    state = next(value for value in ("unreachable", "down", "up") if value in states)
    return {"state": state, "probes": probes}


def probe_remote_containers(
    containers: Mapping[str, Mapping[str, Any]], names: list[str], *, timeout: float = 3.0
) -> dict[str, dict[str, Any]]:
    return {name: probe_connection(containers[name].get("connection"), timeout=timeout) for name in names}
