"""registry connection의 원격 HTTP 응답만 관측한다. Docker 상태/변경 권한은 추정하지 않는다."""

import asyncio
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

_PROBE_SECONDS = 2.0
_PARALLEL_PROBES = 4


def _http_urls(connection: str) -> list[str]:
    if not connection.startswith(("http://", "https://")):
        return []
    return list(dict.fromkeys(re.findall(r"https?://[^\s,;]+", connection)))


async def _probe_connections(
    connections: Mapping[str, str], *, timeout_seconds: float = _PROBE_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str]:
    limiter = asyncio.Semaphore(_PARALLEL_PROBES)
    endpoints = {name: _http_urls(connection) for name, connection in connections.items()}

    async with httpx.AsyncClient(
        timeout=timeout_seconds, follow_redirects=False, trust_env=False,
        limits=httpx.Limits(max_connections=_PARALLEL_PROBES), transport=transport,
    ) as client:
        async def probe(url: str) -> str:
            async with limiter:
                try:
                    parsed = urlsplit(url)
                    if parsed.username is not None or parsed.password is not None:
                        return "unreachable"
                    # body를 읽지 않고 header만 관측한다. 느린 header도 socket 연산별
                    # timeout을 연장하며 무한 대기하지 않도록 전체 예산으로 감싼다.
                    async with asyncio.timeout(timeout_seconds), client.stream("GET", url) as response:
                        return "up" if 200 <= response.status_code < 400 else "down"
                except (TimeoutError, httpx.HTTPError, ValueError):
                    # raw 예외에는 URL/인증값이 있을 수 있으므로 응답·로그에 복사하지 않는다.
                    return "unreachable"

        urls = list(dict.fromkeys(url for values in endpoints.values() for url in values))
        values = await asyncio.gather(*(probe(url) for url in urls))
        observations = dict(zip(urls, values, strict=True))
    return {
        name: (
            "remote" if not urls else
            "unreachable" if any(observations[url] == "unreachable" for url in urls) else
            "down" if any(observations[url] == "down" for url in urls) else "up"
        )
        for name, urls in endpoints.items()
    }


def probe_remote_connections(connections: Mapping[str, str]) -> dict[str, str]:
    """동기 상태 수집 worker에서 한 snapshot을 최대 4개씩 관측한다. 재시도/redirect/body는 없다."""
    if not connections:
        return {}
    return asyncio.run(_probe_connections(connections))
