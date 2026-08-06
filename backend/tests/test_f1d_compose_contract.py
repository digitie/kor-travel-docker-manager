"""F1D의 일회성 schema bootstrap Compose 경계를 회귀 고정한다."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_PATH = _ROOT / "docker-compose.yml"
_MAP_RUNTIME_SERVICES = (
    "kor-travel-map-api",
    "kor-travel-map-ui",
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
)
_PINVI_RUNTIME_SERVICES = ("pinvi-api", "pinvi-web", "pinvi-dagster")


def _source_compose() -> dict[str, Any]:
    document = yaml.safe_load(_COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _compose_fragment(*service_names: str) -> dict[str, object]:
    """선택 service와 그 dependency 이름을 실제 Compose resolver로 해석한다."""

    source_services = _source_compose()["services"]
    assert isinstance(source_services, dict)
    services: dict[str, object] = {
        name: deepcopy(source_services[name]) for name in service_names
    }
    pending = list(service_names)
    while pending:
        name = pending.pop()
        service = services[name]
        assert isinstance(service, dict)
        depends_on = service.get("depends_on") or {}
        assert isinstance(depends_on, dict)
        for dependency in depends_on:
            if dependency not in services:
                # dependency의 실행 내용은 이 계약의 대상이 아니다. 실제 resolver가
                # dependency graph를 검증하게 이름만 최소 stub으로 둔다.
                services[dependency] = {"image": "alpine:3.20"}

    return {"services": services}


def _resolved_compose(*service_names: str) -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose가 없어 resolved Compose 계약을 실행할 수 없음")

    environment = {
        **os.environ,
        "COMPOSE_PROJECT_NAME": "ktdm-f1d-compose-contract",
        "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET": "test-admin-proxy-secret",
        "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET": "test-cursor-signing-secret",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_SERVICE_TOKEN": "test-service-token",
        "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY": "test-geo-key",
        "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": "test-map-head",
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": "test-password-hash",
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": "admin",
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET": "test-ui-session-secret",
        "PINVI_ENVIRONMENT": "production",
    }
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--profile",
            "bootstrap",
            "--file",
            "-",
            "config",
            "--format",
            "json",
        ],
        cwd=_ROOT,
        env=environment,
        input=yaml.safe_dump(_compose_fragment(*service_names), sort_keys=False),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    document = json.loads(completed.stdout)
    assert isinstance(document, dict)
    return document


def test_resolved_map_dagster_services_require_candidate_storage_migration() -> None:
    resolved = _resolved_compose(
        "kor-travel-map-api",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "kor-travel-map-dagster-storage-migrate",
    )
    services = resolved["services"]
    assert isinstance(services, dict)

    migration = services["kor-travel-map-dagster-storage-migrate"]
    assert migration["image"] == "kor-travel-map-dagster:latest-main"
    assert "build" not in migration
    assert migration["command"] == ["ktm-dagster-storage", "migrate"]
    assert migration["restart"] == "no"
    assert migration["network_mode"] == "host"
    assert migration["environment"] == {
        "DAGSTER_DISABLE_TELEMETRY": "yes",
        "DAGSTER_HOME": "/opt/dagster/dagster_home",
        "KOR_TRAVEL_MAP_DAGSTER_PG_URL": (
            "postgresql://krtour_map:krtour_map_dev_password@127.0.0.1:5432/"
            "kor_travel_map_dagster"
        ),
    }
    assert migration["depends_on"]["kor-travel-geo-postgres"]["condition"] == (
        "service_healthy"
    )
    assert migration["extra_hosts"] == ["host.docker.internal=host-gateway"]

    for service_name in (
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
    ):
        dependency = services[service_name]["depends_on"]
        assert dependency["kor-travel-map-dagster-storage-migrate"]["condition"] == (
            "service_completed_successfully"
        )


def test_resolved_pinvi_api_has_no_implicit_schema_mutation_or_bootstrap_secret() -> None:
    resolved = _resolved_compose("pinvi-api", "pinvi-admin-bootstrap")
    services = resolved["services"]
    assert isinstance(services, dict)

    api = services["pinvi-api"]
    assert api["command"] == [
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "12801",
        "--workers",
        "1",
    ]
    assert "alembic" not in json.dumps(api["command"]).lower()

    bootstrap = services["pinvi-admin-bootstrap"]
    assert bootstrap["profiles"] == ["bootstrap"]
    assert bootstrap["image"] == api["image"]
    assert "build" not in bootstrap
    assert bootstrap["command"] == ["pinvi-admin-bootstrap"]
    assert bootstrap["restart"] == "no"
    assert bootstrap["network_mode"] == "host"
    assert "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE" not in bootstrap["environment"]


def test_ordinary_runtime_services_never_receive_bootstrap_credential_contract() -> None:
    services = _source_compose()["services"]
    assert isinstance(services, dict)

    for service_name in (*_MAP_RUNTIME_SERVICES, *_PINVI_RUNTIME_SERVICES):
        assert "PINVI_BOOTSTRAP_ADMIN" not in json.dumps(services[service_name])
