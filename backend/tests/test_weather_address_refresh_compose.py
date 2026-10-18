"""Weather 주소 설정을 두 활성 runtime에만 같은 기본값으로 전달한다."""

from pathlib import Path

import pytest
import yaml
from test_dagster_shared_workspace_is_derived import _resolve

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("kor-travel-weather-api", "kor-travel-weather-dagster-code-server")
DEFAULTS = {
    "KOR_TRAVEL_WEATHER_VWORLD_API_KEY": "",
    "KOR_TRAVEL_WEATHER_KAKAO_REST_API_KEY": "",
    "KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_AUTO_ENABLED": "true",
    "KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_BATCH_SIZE": "50",
    "KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_TIMEOUT_SECONDS": "5",
}


def services():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


@pytest.mark.parametrize("name", SERVICES)
def test_weather_address_default_interpolation_matches_settings(name):
    environment = services()[name]["environment"]
    for key, default in DEFAULTS.items():
        assert environment[key] == "$" + "{" + key + ":-" + default + "}"
        assert _resolve(environment[key], {}) == default
        assert _resolve(environment[key], {key: ""}) == default


@pytest.mark.parametrize("name", SERVICES)
def test_weather_address_explicit_values_survive_static_interpolation(name):
    environment = services()[name]["environment"]
    overrides = dict(zip(DEFAULTS, ("fixture-vworld", "fixture-kakao", "false", "7", "2.5"), strict=True))
    assert {key: _resolve(environment[key], overrides) for key in DEFAULTS} == overrides


def test_weather_address_values_are_not_forwarded_to_other_services():
    for name, spec in services().items():
        if name not in SERVICES:
            assert not set(DEFAULTS).intersection(spec.get("environment", {})), name


def test_weather_address_example_has_placeholders_and_same_defaults():
    lines = (ROOT / ".env.example").read_text().splitlines()
    for key, default in DEFAULTS.items():
        assert lines.count(key + "=" + default) == 1
