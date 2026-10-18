"""승인된 배포 health 간격만 고정하고 Weather를 다시3배로 늘리지 않는다."""

from pathlib import Path

import pytest

from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("service_name", "interval"),
    [
        ("kor-travel-geo-api", "30s"),
        ("kor-travel-geo-dagster-code-server", "90s"),
        ("kor-travel-geo-ui", "30s"),
        ("kor-travel-weather-api", "90s"),
        ("kor-travel-weather-dagster-code-server", "90s"),
        ("kor-travel-dagster-webserver", "90s"),
        ("kor-travel-shared-postgres", "30s"),
        ("rustfs", "30s"),
    ],
)
def test_approved_health_intervals(service_name: str, interval: str) -> None:
    """기존 서비스 키를 검사하므로 운영 별칭으로 새 서비스를 만들지 않는다."""
    services = load_yaml_rejecting_duplicate_keys(
        (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )["services"]
    assert services[service_name]["healthcheck"]["interval"] == interval


def test_weather_web_does_not_gain_a_healthcheck() -> None:
    """간격 정합을 이유로 원래 없던 UI 점검을 추가하지 않는다."""
    services = load_yaml_rejecting_duplicate_keys(
        (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )["services"]
    assert "healthcheck" not in services["kor-travel-weather-web"]
