from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml

from kor_travel_docker_manager.services import compose_service, registry
from kor_travel_docker_manager.services.errors import DeploymentContractError

_GEO_URL = "http://192.168.1.13:12501"
_MAP_CONSUMERS = (
    "kor-travel-map-api",
    "kor-travel-map-ui",
    "kor-travel-map-dagster-code-server",
)


def _transaction(
    *,
    map_url: str = _GEO_URL,
    code_server_url: str | None = None,
    ui_url: str | None = None,
    pinvi_url: str = _GEO_URL,
) -> Any:
    map_environment = {"KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL": map_url}
    services = {
        name: {"environment": map_environment.copy()}
        for name in _MAP_CONSUMERS
        if name != "kor-travel-map-ui"
    }
    services["kor-travel-map-ui"] = {
        "environment": {"KOR_TRAVEL_GEO_INTERNAL_URL": ui_url or map_url}
    }
    if code_server_url is not None:
        services["kor-travel-map-dagster-code-server"]["environment"][
            "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL"
        ] = code_server_url
    services["pinvi-api"] = {
        "environment": {"PINVI_KOR_TRAVEL_GEO_BASE_URL": pinvi_url}
    }
    return SimpleNamespace(resolved={"services": services})


class _ChunkedBody(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self):
        for offset in range(0, len(self.content), 1024):
            yield self.content[offset : offset + 1024]

    async def aclose(self) -> None:
        return None


def test_remote_geo_gate_checks_db_readiness_with_bounded_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    options: dict[str, object] = {}
    real_client = httpx.AsyncClient

    def capture_client(**kwargs: object) -> httpx.AsyncClient:
        options.update(kwargs)
        return real_client(**kwargs)

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            stream=_ChunkedBody(b'{"ready":true,"database":"ok"}'),
        )

    monkeypatch.setattr(compose_service.httpx, "AsyncClient", capture_client)
    monkeypatch.setattr(
        compose_service,
        "current_host",
        lambda: "n150",
    )
    compose_service.ComposeService._require_geo_api_ready(
        _transaction(), transport=httpx.MockTransport(handle)
    )

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == f"{_GEO_URL}/v1/readyz"
    assert "authorization" not in requests[0].headers
    assert requests[0].headers["accept-encoding"] == "identity"
    assert options["follow_redirects"] is False
    assert options["trust_env"] is False
    assert options["timeout"] == httpx.Timeout(4.0)


def test_remote_geo_gate_rejects_coordinated_drift_from_configured_geo_target() -> None:
    wrong_origin = "http://192.0.2.44:12501"
    transaction = _transaction(
        map_url=wrong_origin,
        code_server_url=wrong_origin,
        ui_url=wrong_origin,
        pinvi_url=wrong_origin,
    )
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=_ChunkedBody(b'{"ready":true}'))

    with pytest.raises(
        DeploymentContractError, match="does not match configured Geo target"
    ):
        compose_service.ComposeService._require_geo_api_ready(
            transaction, transport=httpx.MockTransport(handle)
        )

    assert requests == []
    targets = registry.load_targets_config()
    assert registry.consumer_origin("geo", "n150", targets) == _GEO_URL


def test_remote_geo_gate_accepts_split_consumer_hosts_and_probes_manager_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = copy.deepcopy(registry.load_targets_config())
    targets["targets"]["pinvi"]["host"] = "odroid"
    monkeypatch.setattr(compose_service, "load_targets_config", lambda: targets)
    monkeypatch.setattr(compose_service, "current_host", lambda: "n150")

    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=_ChunkedBody(b'{"ready":true}'))

    compose_service.ComposeService._require_geo_api_ready(
        _transaction(pinvi_url="http://127.0.0.1:12501"),
        transport=httpx.MockTransport(handle),
    )

    assert len(requests) == 1
    assert str(requests[0].url) == f"{_GEO_URL}/v1/readyz"


def test_remote_geo_gate_probes_local_geo_origin_when_manager_is_on_odroid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compose_service, "current_host", lambda: "odroid")
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=_ChunkedBody(b'{"ready":true}'))

    compose_service.ComposeService._require_geo_api_ready(
        _transaction(), transport=httpx.MockTransport(handle)
    )

    assert len(requests) == 1
    assert str(requests[0].url) == "http://127.0.0.1:12501/v1/readyz"


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (301, {"ready": True}),
        (302, {"ready": True}),
        (503, {"ready": False}),
        (200, {"ready": False}),
        (200, {"status": "ok"}),
    ],
)
def test_remote_geo_gate_rejects_redirects_and_unready_responses(
    status_code: int, payload: dict[str, object]
) -> None:
    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"location": "https://example.invalid/forbidden"},
            stream=_ChunkedBody(json.dumps(payload).encode()),
        )

    with pytest.raises(DeploymentContractError, match="health check failed") as captured:
        compose_service.ComposeService._require_geo_api_ready(
            _transaction(), transport=httpx.MockTransport(handle)
        )

    assert _GEO_URL not in str(captured.value)


def test_remote_geo_gate_rejects_invalid_and_oversized_json_bodies(
) -> None:
    async def invalid(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_ChunkedBody(b"{not-json"))

    with pytest.raises(DeploymentContractError, match="health check failed"):
        compose_service.ComposeService._require_geo_api_ready(
            _transaction(), transport=httpx.MockTransport(invalid)
        )

    async def oversized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_ChunkedBody(b" " * 4097))

    with pytest.raises(DeploymentContractError, match="health check failed"):
        compose_service.ComposeService._require_geo_api_ready(
            _transaction(), transport=httpx.MockTransport(oversized)
        )

    async def compressed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=_ChunkedBody(b"compressed body"),
        )

    with pytest.raises(DeploymentContractError, match="health check failed"):
        compose_service.ComposeService._require_geo_api_ready(
            _transaction(), transport=httpx.MockTransport(compressed)
        )


def test_remote_geo_gate_fails_closed_on_total_wall_clock_timeout() -> None:
    async def handle(_request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        return httpx.Response(200, json={"ready": True})

    with pytest.raises(TimeoutError):
        asyncio.run(
            compose_service.ComposeService._probe_geo_api_ready(
                _GEO_URL,
                timeout_seconds=0.05,
                max_body_bytes=4096,
                transport=httpx.MockTransport(handle),
            )
        )


def test_remote_geo_gate_rejects_code_server_origin_drift_without_request(
) -> None:
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ready": True})

    with pytest.raises(
        DeploymentContractError, match="does not match configured Geo target"
    ):
        compose_service.ComposeService._require_geo_api_ready(
            _transaction(code_server_url="http://127.0.0.1:12501"),
            transport=httpx.MockTransport(handle),
        )

    assert requests == []


def test_remote_geo_gate_rejects_map_ui_origin_drift_without_request(
) -> None:
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=_ChunkedBody(b'{"ready":true}'))

    with pytest.raises(
        DeploymentContractError, match="does not match configured Geo target"
    ):
        compose_service.ComposeService._require_geo_api_ready(
            _transaction(ui_url="http://127.0.0.1:12501"),
            transport=httpx.MockTransport(handle),
        )

    assert requests == []


@pytest.mark.parametrize(
    "service_name",
    [*_MAP_CONSUMERS, "pinvi-api"],
)
def test_remote_geo_gate_requires_every_frozen_consumer(
    service_name: str
) -> None:
    transaction = _transaction()
    del transaction.resolved["services"][service_name]

    async def unexpected(_request: httpx.Request) -> httpx.Response:
        pytest.fail("readiness request ran despite missing consumer configuration")

    with pytest.raises(
        DeploymentContractError, match=f"endpoint is missing: {service_name}"
    ):
        compose_service.ComposeService._require_geo_api_ready(
            transaction, transport=httpx.MockTransport(unexpected)
        )


def test_remote_geo_gate_origin_inventory_matches_active_compose_consumers() -> None:
    compose_document = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
    )
    environment_names = {
        "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL",
        "KOR_TRAVEL_GEO_INTERNAL_URL",
        "PINVI_KOR_TRAVEL_GEO_BASE_URL",
    }
    active_consumers = {
        (service_name, variable_name)
        for service_name, service in compose_document["services"].items()
        if "legacy-dagster" not in service.get("profiles", [])
        for variable_name in environment_names.intersection(
            (service.get("environment") or {}).keys()
        )
    }

    assert active_consumers == set(
        compose_service._PINNED_RUNTIME_GEO_CONSUMER_ORIGINS
    )
    assert dict(compose_service._PINNED_RUNTIME_GEO_CONSUMER_TARGETS) == {
        "kor-travel-map-api": "map",
        "kor-travel-map-ui": "map",
        "kor-travel-map-dagster-code-server": "map",
        "pinvi-api": "pinvi",
    }

