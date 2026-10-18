"""Weather API 생존 점검의 포트·비용·실패 확인 계약."""

from pathlib import Path

import yaml

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _service() -> dict:
    return yaml.safe_load(_COMPOSE.read_text())["services"]["kor-travel-weather-api"]


def test_weather_api_uses_quiet_core_http_cli_without_shell_or_framework_import() -> None:
    service = _service()
    test = service["healthcheck"]["test"]
    assert test == [
        "CMD", "python", "-I", "-m", "kortravelcommon.http_health",
        "--port", "${KOR_TRAVEL_WEATHER_API_PORT:-14101}",
        "--timeout-seconds", "3",
    ]


def test_weather_api_command_mapping_and_probe_share_the_port_expression() -> None:
    service = _service()
    command = service["command"]
    port = command[command.index("--port") + 1]
    test = service["healthcheck"]["test"]
    assert port == test[test.index("--port") + 1]
    assert service["ports"] == [f"{port}:{port}"]
    # 고정 listen 포트에 probe만 override되는 회귀를 막는다.
    assert port == "${KOR_TRAVEL_WEATHER_API_PORT:-14101}"


def test_weather_api_health_has_startup_margin_and_approved_cadence() -> None:
    health = _service()["healthcheck"]
    test = health["test"]
    http_seconds = float(test[test.index("--timeout-seconds") + 1])
    assert http_seconds == 3
    assert health["timeout"] == "12s"  # Python 시작 + HTTP 예산을 Docker가 감싼다.
    assert health["interval"] == "90s"
    assert health["retries"] == 4
    assert int(health["interval"][:-1]) * health["retries"] == 360
    assert http_seconds < int(health["timeout"][:-1])
