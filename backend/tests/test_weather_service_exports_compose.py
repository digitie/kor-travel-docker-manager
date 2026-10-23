"""Weather API/worker가 동일한 승인 파일과 활성화 상태를 사용해야 한다."""

from pathlib import Path, PurePosixPath

import pytest
import yaml
from test_dagster_shared_workspace_is_derived import _resolve

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("kor-travel-weather-api", "kor-travel-weather-dagster-code-server")
FLAG = "KOR_TRAVEL_WEATHER_SERVICE_EXPORTS_ENABLED"
ARTIFACT_ROOT = "KOR_TRAVEL_WEATHER_SERVICE_EXPORT_ARTIFACT_ROOT"


def document():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())


@pytest.mark.parametrize("value", [None, "", "true", "false"])
def test_weather_export_activation_matches_between_runtime_services(value):
    env = {} if value is None else {FLAG: value}
    resolved = [_resolve(document()["services"][name]["environment"][FLAG], env) for name in SERVICES]
    assert resolved == [value or "false"] * len(SERVICES)


def test_weather_export_files_survive_runtime_replacement_in_same_rw_volume():
    compose = document()
    api, worker = [compose["services"][name] for name in SERVICES]
    root = PurePosixPath(api["environment"][ARTIFACT_ROOT])
    assert root.is_absolute()
    assert worker["environment"][ARTIFACT_ROOT] == str(root)
    api_mounts = api["volumes"]
    assert len(api_mounts) == 1
    volume_name, mount_target = api_mounts[0].split(":")
    assert PurePosixPath(mount_target) == root.parent
    assert compose["volumes"][volume_name] == {}
    assert api_mounts[0] in worker["volumes"]
    # 기존 shared instance의 읽기 전용 mount를 보존한다.
    assert any(mount.endswith(":/opt/dagster/home/dagster.yaml:ro") for mount in worker["volumes"])
    for name, service in compose["services"].items():
        if name not in SERVICES:
            assert FLAG not in service.get("environment", {})
            assert ARTIFACT_ROOT not in service.get("environment", {})
            assert not any(mount.startswith(volume_name + ":") for mount in service.get("volumes", []) if isinstance(mount, str))


def test_weather_export_flag_example_is_disabled_once():
    assert (ROOT / ".env.example").read_text().splitlines().count(FLAG + "=false") == 1
