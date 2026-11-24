"""PinVi ETL code location이 resource로 읽는 env가 **렌더된** compose에 닿는지 센다.

2026-09-30 n150: `kasi_special_days_job`·`pinvi_trip_day_rise_sets`·
`pinvi_weather_retention_horizon` 세 schedule은 켤 수 없었다. `pinvi.etl.definitions`가
`EnvVar("DATA_GO_KR_SERVICE_KEY")`·`EnvVar("PINVI_KOR_TRAVEL_WEATHER_BASE_URL")`을
resource에 넘기는데 `pinvi-dagster-code-server`(run worker의 부모)에 둘 다 없었기 때문이다.

원문 문자열이 아니라 `docker compose config`가 **해석한 값**을 본다 — 폴백이 실제로
Map key에 닿는지, URL이 weather API가 실제로 있는 곳을 가리키는지가 효과다.

2026-10-14 weather가 odroid(`192.168.1.13`)로 옮겼고 소유자가 공개 대역 포트 `12201`로 통일했다 —
n150의 weather API(`127.0.0.1:14101`)는 멈췄다. URL은 이제 odroid weather이고, 운영자가 root `.env`로
재정의할 수 있다(ADR-58 R2; 모든 소비자 기본값의 스캔은 `test_consumer_origin_defaults.py`).
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
#: 여행 날씨 화면이 같은 weather API를 부른다(ADR-58 S17) — ETL과 같은 원천·기본값이어야 한다.
_PINVI_API = "pinvi-api"
#: odroid weather API — 소유자가 통일한 공개 대역 포트(2026-10-14).
_ODROID_WEATHER_ORIGIN = "http://192.168.1.13:12201"
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

#: 예약 알림 planner — PinVi `definitions.py`의 `PinviNotificationPlannerResource`가 읽는 두 이름과, API
#: (`app/core/config.py` `pinvi_telegram_planner_key`)가 읽는 키. URL은 API가 읽지 않는다.
_PLANNER_KEY_ENV = "PINVI_TELEGRAM_PLANNER_KEY"
_PLANNER_URL_ENV = "PINVI_NOTIFICATION_PLANNER_API_URL"
_PINVI_API_PORT_ENV = "PINVI_API_PORT"
_PLANNER_KEY = "planner-key-sentinel-6b1f0c93"
#: planner 키를 받으면 안 되는 PinVi 서비스 — 키는 API와 code-server 둘에만 간다.
_PLANNER_KEY_FREE_SERVICES = ("pinvi-web", "pinvi-admin-bootstrap", *_NON_EXECUTING_PINVI_DAGSTER)
_TARGETS = _ROOT / "config" / "docker-targets.yml"

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
        if key not in {
            _DEDICATED_SOURCE, _MAP_SOURCE, _WEATHER_URL_ENV, "KTDM_DOCKER_NETWORK_MODE",
            _PLANNER_KEY_ENV, _PLANNER_URL_ENV, _PINVI_API_PORT_ENV,
        }
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


def test_weather_url_points_at_the_odroid_weather_api() -> None:
    """ETL code-server와 pinvi-api 둘 다 odroid weather를 부른다(옛 n150 `127.0.0.1:14101`이 아니다)."""

    rendered = _render(_CODE_SERVER, _PINVI_API, environment_update={})
    assert rendered[_CODE_SERVER]["environment"][_WEATHER_URL_ENV] == _ODROID_WEATHER_ORIGIN
    assert rendered[_PINVI_API]["environment"][_WEATHER_URL_ENV] == _ODROID_WEATHER_ORIGIN


def test_weather_url_follows_the_operator_override() -> None:
    """root `.env`의 같은 이름이 두 서비스 모두를 바꾼다 — 리터럴이면 재정의가 조용히 무시된다."""

    override = "http://weather.override.invalid:18080"
    rendered = _render(_CODE_SERVER, _PINVI_API, environment_update={_WEATHER_URL_ENV: override})
    assert rendered[_CODE_SERVER]["environment"][_WEATHER_URL_ENV] == override
    assert rendered[_PINVI_API]["environment"][_WEATHER_URL_ENV] == override


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


# --------------------------------------------------------------------------- 예약 알림 planner


def _api_listen_port(rendered: dict[str, Any]) -> str:
    command = [str(item) for item in rendered[_PINVI_API]["command"]]
    return command[command.index("--port") + 1]


def test_planner_key_reaches_both_the_api_and_the_code_server() -> None:
    """API는 키로 내부 경로를 열고 code-server는 같은 키를 헤더로 보낸다 — 한 root `.env` 값이 둘에 간다."""

    rendered = _render(_CODE_SERVER, _PINVI_API, environment_update={_PLANNER_KEY_ENV: _PLANNER_KEY})
    assert rendered[_CODE_SERVER]["environment"][_PLANNER_KEY_ENV] == _PLANNER_KEY
    assert rendered[_PINVI_API]["environment"][_PLANNER_KEY_ENV] == _PLANNER_KEY


def test_an_unset_planner_key_renders_as_an_empty_string_on_both() -> None:
    """키가 없으면 둘 다 빈 문자열을 받는다(이름 누락이 아니다).

    PinVi에서 빈 값은 꺼짐이다: API `_require_planner_key`는 기대값이 비면 404, code-server의 schedule 점검은 run을
    띄우지 않고 asset은 `configured: False`로 no-op. 빈 문자열은 `str` 설정이라 기동도 막지 않는다.
    """

    rendered = _render(_CODE_SERVER, _PINVI_API, environment_update={})
    assert rendered[_CODE_SERVER]["environment"][_PLANNER_KEY_ENV] == ""
    assert rendered[_PINVI_API]["environment"][_PLANNER_KEY_ENV] == ""


def test_planner_url_is_the_loopback_port_the_api_listens_on() -> None:
    """URL은 pinvi-api가 실제로 듣는 포트(`--port`)와 registry `containers.pinvi-api.connection`에서 나온 값이다."""

    rendered = _render(_CODE_SERVER, _PINVI_API, environment_update={})
    url = rendered[_CODE_SERVER]["environment"][_PLANNER_URL_ENV]
    assert url == f"http://127.0.0.1:{_api_listen_port(rendered)}"
    # loopback은 둘 다 host network일 때만 같은 프로세스 공간을 가리킨다(production C6c도 host를 강제한다).
    assert rendered[_CODE_SERVER]["network_mode"] == "host"
    assert rendered[_PINVI_API]["network_mode"] == "host"
    targets = yaml.safe_load(_TARGETS.read_text(encoding="utf-8"))
    # 같은 target(pinvi)·같은 호스트 안 참조는 loopback이다(`docs/ports.md` 호스트 규칙).
    assert _PINVI_API in targets["targets"]["pinvi"]["services"]
    assert _CODE_SERVER in targets["targets"]["pinvi"]["services"]
    assert url == targets["containers"][_PINVI_API]["connection"].replace("{host}", "127.0.0.1")


def test_planner_url_follows_the_api_port_and_ignores_a_root_env_url() -> None:
    """API 포트를 옮기면 URL도 옮긴다. root `.env`의 URL(PinVi `.env.example`의 bridge 이름 등)은 쓰이지 않는다."""

    rendered = _render(
        _CODE_SERVER,
        _PINVI_API,
        environment_update={_PINVI_API_PORT_ENV: "12899", _PLANNER_URL_ENV: "http://app-api:8000"},
    )
    assert _api_listen_port(rendered) == "12899"
    assert rendered[_CODE_SERVER]["environment"][_PLANNER_URL_ENV] == "http://127.0.0.1:12899"


def test_planner_env_is_only_where_pinvi_reads_it() -> None:
    """키는 API와 code-server에만, URL은 code-server에만(API는 읽지 않는다). 다른 PinVi 서비스로 비밀을 넓히지 않는다."""

    services = _source_services()
    # 두 서비스가 **같은 원천** 하나를 읽어야 한다 — 한쪽만 다른 변수(폴백 포함)를 읽으면 값이 갈라져 API 404로 매분 run이
    # 실패한다(점검은 404에서 fail-open).
    for name in (_CODE_SERVER, _PINVI_API):
        assert services[name]["environment"][_PLANNER_KEY_ENV] == f"${{{_PLANNER_KEY_ENV}:-}}", name
    assert _PLANNER_URL_ENV in services[_CODE_SERVER]["environment"]
    assert _PLANNER_URL_ENV not in services[_PINVI_API]["environment"]
    for name in _PLANNER_KEY_FREE_SERVICES:
        environment = services[name].get("environment") or {}
        assert _PLANNER_KEY_ENV not in environment, name
        assert _PLANNER_URL_ENV not in environment, name
    for name, service in services.items():
        if name in {_CODE_SERVER, _PINVI_API}:
            continue
        assert _PLANNER_KEY_ENV not in (service.get("environment") or {}), name


def test_the_rendered_planner_key_is_scrubbed_from_failure_output() -> None:
    """렌더 결과(`docker compose config`)가 실패 출력에 실려도 키 원문은 가림 규칙에 걸린다."""

    from kor_travel_docker_manager.services.secret_scrub import redact_secret_text

    rendered = _render(_CODE_SERVER, _PINVI_API, environment_update={_PLANNER_KEY_ENV: _PLANNER_KEY})
    text = json.dumps(rendered)
    assert _PLANNER_KEY in text
    scrubbed = redact_secret_text(text, {_PLANNER_KEY_ENV: _PLANNER_KEY})
    assert _PLANNER_KEY not in scrubbed
    # URL은 비밀이 아니다 — 가리면 원인 진단이 망가진다.
    assert "http://127.0.0.1:12801" in scrubbed


def test_env_example_documents_the_planner_key_as_an_empty_placeholder() -> None:
    lines = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith(f"{_PLANNER_KEY_ENV}=")] == [f"{_PLANNER_KEY_ENV}="]
    # URL은 파생값이라 `.env.example`에 두지 않는다 — 두면 재정의 가능한 것처럼 읽힌다.
    assert not [line for line in lines if line.startswith(f"{_PLANNER_URL_ENV}=")]
