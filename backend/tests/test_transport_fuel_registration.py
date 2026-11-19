"""공개 유가 두 역할이 기존 Transport와 다른 Compose 경계에만 등록된다."""

from copy import deepcopy
from pathlib import Path

import pytest

from kor_travel_docker_manager import service_release_cli as release_cli
from kor_travel_docker_manager.services import registry
from kor_travel_docker_manager.services.service_release import (
    ServiceReleaseError,
    _profile,
)
from kor_travel_docker_manager.services.yaml_strict import (
    load_yaml_rejecting_duplicate_keys,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def config(monkeypatch, tmp_path):
    value = load_yaml_rejecting_duplicate_keys(
        (ROOT / "config/docker-targets.yml").read_text()
    )
    registry._validate_targets_config(value, label="fuel registration")
    monkeypatch.setattr(registry, "load_targets_config", lambda: value)
    monkeypatch.setenv(registry.PROJECT_ROOT_ENV, str(tmp_path))
    monkeypatch.setenv("KTDM_HOST", "odroid")
    return value


def test_explicit_fuel_target_is_separate_and_never_joins_all(config, monkeypatch):
    assert registry.resolve_target_name("transport-fuel") == "fuel"
    assert registry.target_sequence_for_target("fuel") == ["fuel"]
    for host in ("odroid", "n150"):
        monkeypatch.setenv("KTDM_HOST", host)
        assert "fuel" not in registry.target_sequence_for_target("all")
    assert registry.target_hosts("fuel", config) == frozenset({"odroid"})
    group, = registry.service_groups_for_target("fuel", runtime_only=True)
    assert group.services == ("fuel-api-gateway", "fuel-web")
    assert group.external.project == "kor-travel-transport-fuel"
    assert group.external.working_dir == "/opt/kor-travel-odroid/transport-fuel"
    assert group.external.config_files == ("docker-compose.fuel.yml",)
    assert registry.init_steps_for_target("fuel") == []
    assert "dagster" not in config["targets"]["fuel"]


def test_fuel_public_coordinates_do_not_inherit_admin_or_backend_roles(config):
    declarations = config["containers"]
    selected = [declarations[key] for key in config["targets"]["fuel"]["containers"]]
    assert {entry["compose_service"] for entry in selected} == {
        "fuel-api-gateway", "fuel-web",
    }
    assert {entry["external_project"] for entry in selected} == {
        "kor-travel-transport-fuel",
    }
    assert {entry["name"] for entry in selected} == {
        "kor-travel-transport-fuel-fuel-api-gateway-1",
        "kor-travel-transport-fuel-fuel-web-1",
    }
    assert {entry["connection"] for entry in selected} == {
        "http://{host}:12307", "http://{host}:12308",
    }
    assert {entry["prod_url_env"] for entry in selected} == {
        "KTDM_PROD_URL_FUEL_API", "KTDM_PROD_URL_FUEL",
    }
    assert set(config["service_releases"]["transport"]["groups"]) == {"core", "admin"}
    assert registry.external_project_for_target("transport").project == "kor-travel-transport"
    assert not set(config["targets"]["fuel"]["containers"]) & set(
        config["targets"]["transport"]["containers"]
    )


def test_fuel_release_profile_is_public_pair_with_exact_compose_coordinates(config):
    declaration = release_cli._declaration("fuel", config)
    profile = _profile(release_cli._profile("fuel", declaration, []))
    public = profile["groups"]["public"]
    assert set(profile["groups"]) == {"public"}
    assert public == {
        "project": "kor-travel-transport-fuel",
        "working_dir": "/opt/kor-travel-odroid/transport-fuel",
        "config_files": ["/opt/kor-travel-odroid/transport-fuel/docker-compose.fuel.yml"],
        "env_file": "/opt/kor-travel-odroid/transport-fuel/.env",
        "runtime_services": ["fuel-api-gateway", "fuel-web"],
        "oneshot_images": {},
    }
    release_cli._check_roles(profile, declaration)
    missing = deepcopy(profile)
    missing["groups"]["public"]["runtime_services"].remove("fuel-web")
    with pytest.raises(ServiceReleaseError, match="역할"):
        release_cli._check_roles(missing, declaration)
    mixed = deepcopy(profile)
    mixed["groups"]["public"]["runtime_services"].append("transport-admin-web")
    with pytest.raises(ServiceReleaseError, match="역할"):
        release_cli._check_roles(mixed, declaration)


def test_fuel_release_is_rejected_on_n150(config, monkeypatch):
    monkeypatch.setenv("KTDM_HOST", "n150")
    with pytest.raises(ServiceReleaseError, match="호스트"):
        release_cli._declaration("fuel", config)
