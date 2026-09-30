"""PinVi ETL code location이 resource로 읽는 env가 **렌더된** compose에 닿는지 센다.

2026-09-30 n150: `kasi_special_days_job`·`pinvi_trip_day_rise_sets`·
`pinvi_weather_retention_horizon` 세 schedule은 켤 수 없었다. `pinvi.etl.definitions`가
`EnvVar("DATA_GO_KR_SERVICE_KEY")`·`EnvVar("PINVI_KOR_TRAVEL_WEATHER_BASE_URL")`을
resource에 넘기는데 `pinvi-dagster-code-server`(run worker의 부모)에 둘 다 없었기 때문이다.

원문 문자열이 아니라 `docker compose config`가 **해석한 값**을 본다 — 폴백이 실제로
Map key에 닿는지, URL이 weather API가 실제로 듣는 포트를 가리키는지가 효과다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker-compose.yml"

_CODE_SERVER = "pinvi-dagster-code-server"
_WEATHER_API = "kor-travel-weather-api"
#: code location을 import하지 않는 PinVi Dagster 서비스 — run을 실행하지 않는다.
_NON_EXECUTING_PINVI_DAGSTER = ("pinvi-dagster", "pinvi-dagster-daemon")

#: PinVi `apps/etl/pinvi/etl/definitions.py`가 `EnvVar`로 읽는 이름.
_KASI_KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
_WEATHER_URL_ENV = "PINVI_KOR_TRAVEL_WEATHER_BASE_URL"
#: 운영자 `.env`의 원천 — 전용 key가 우선, 없으면 Map key.
_DEDICATED_SOURCE = "PINVI_DATA_GO_KR_SERVICE_KEY"
_MAP_SOURCE = "KRTOUR_MAP_DATA_GO_KR_SERVICE_KEY"

_MAP_KEY = "map-data-go-kr-key-sentinel"
_DEDICATED_KEY = "pinvi-dedicated-key-sentinel"

#: `${NAME:?...}` — 값이 없으면 config 자체가 실패하는 필수 변수.
_REQUIRED_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?")


def _source_services() -> dict[str, Any]:
    document = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    services = document["services"]
    assert isinstance(services, dict)
    return services


def _render(*service_names: str, environment_update: dict[str, str]) -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose가 없어 resolved compose를 렌더할 수 없음")
    source = _source_services()
    fragment_services: dict[str, Any] = {}
    for name in service_names:
        service = deepcopy(source[name])
        # 조각만 렌더한다 — 조각 밖 서비스를 가리키는 의존과 빌드 문맥은 이 검사와 무관하다.
        service.pop("depends_on", None)
        service.pop("build", None)
        fragment_services[name] = service
    fragment_text = yaml.safe_dump({"services": fragment_services}, sort_keys=False)

    environment = {
        key: value
        for key, value in os.environ.items()
        # 호스트 값이 새어 들어와 결과를 바꾸지 않게 한다.
        if key not in {_DEDICATED_SOURCE, _MAP_SOURCE, "KTDM_DOCKER_NETWORK_MODE"}
        and not key.startswith("COMPOSE_")
    }
    environment.update(
        {name: "contract-placeholder" for name in _REQUIRED_VAR.findall(fragment_text)}
    )
    environment["COMPOSE_PROJECT_NAME"] = "ktdm-pinvi-etl-env-contract"
    environment.update(environment_update)
    completed = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "--file", "-", "config", "--format", "json"],
        cwd=_ROOT,
        env=environment,
        input=fragment_text,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    rendered = json.loads(completed.stdout)["services"]
    assert isinstance(rendered, dict)
    return rendered


def _listening_port(command: list[str]) -> str:
    """uvicorn argv의 `--port` 값 — host network에서 실제로 듣는 포트다."""

    index = command.index("--port")
    return command[index + 1]


def test_kasi_key_falls_back_to_the_map_key_when_no_dedicated_key_is_set() -> None:
    rendered = _render(_CODE_SERVER, environment_update={_MAP_SOURCE: _MAP_KEY})
    environment = rendered[_CODE_SERVER]["environment"]
    assert environment[_KASI_KEY_ENV] == _MAP_KEY


def test_kasi_key_prefers_the_dedicated_key() -> None:
    rendered = _render(
        _CODE_SERVER,
        environment_update={_MAP_SOURCE: _MAP_KEY, _DEDICATED_SOURCE: _DEDICATED_KEY},
    )
    assert rendered[_CODE_SERVER]["environment"][_KASI_KEY_ENV] == _DEDICATED_KEY


def test_empty_dedicated_key_still_falls_back() -> None:
    """`.env.example`의 빈 placeholder(`PINVI_DATA_GO_KR_SERVICE_KEY=`)가 폴백을 막으면 안 된다."""

    rendered = _render(
        _CODE_SERVER,
        environment_update={_MAP_SOURCE: _MAP_KEY, _DEDICATED_SOURCE: ""},
    )
    assert rendered[_CODE_SERVER]["environment"][_KASI_KEY_ENV] == _MAP_KEY


def test_weather_url_points_at_the_port_the_weather_api_listens_on() -> None:
    rendered = _render(_CODE_SERVER, _WEATHER_API, environment_update={})
    code_server = rendered[_CODE_SERVER]
    weather = rendered[_WEATHER_API]
    # 127.0.0.1이 weather API에 닿는 것은 둘 다 host network일 때뿐이다.
    assert code_server["network_mode"] == "host"
    assert weather["network_mode"] == "host"
    assert code_server["environment"][_WEATHER_URL_ENV] == (
        f"http://127.0.0.1:{_listening_port(weather['command'])}"
    )


def test_etl_env_is_only_on_the_service_that_executes_runs() -> None:
    """webserver·daemon은 code location을 import하지 않는다 — key를 넓게 뿌리지 않는다."""

    services = _source_services()
    assert _KASI_KEY_ENV in services[_CODE_SERVER]["environment"]
    for name in _NON_EXECUTING_PINVI_DAGSTER:
        environment = services[name].get("environment") or {}
        assert _KASI_KEY_ENV not in environment, name
        assert _WEATHER_URL_ENV not in environment, name


def test_env_example_has_an_empty_dedicated_key_placeholder() -> None:
    lines = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith(f"{_DEDICATED_SOURCE}=")] == [
        f"{_DEDICATED_SOURCE}="
    ]
