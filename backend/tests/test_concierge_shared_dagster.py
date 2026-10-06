"""신규 공용 Dagster location에는 가상의 옛 webserver·daemon을 요구하지 않는다."""

from pathlib import Path

import pytest
import yaml

from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.runtime_topology import derive_dagster_family

ROOT = Path(__file__).resolve().parents[2]


def documents():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text()), yaml.safe_load((ROOT / "config/docker-targets.yml").read_text())


def test_shared_only_family_and_legacy_scheduler_are_separate():
    compose, targets = documents()
    family = derive_dagster_family(compose, targets, "conc")
    assert family.shared and family.webserver is None and family.daemon is None
    assert family.legacy == () and family.retired == ()
    assert family.names == family.processes == (family.code_server,)
    assert family.carrier == family.code_server and family.active_daemon is None
    scheduler = "kor-travel-concierge-scheduler"
    assert compose["services"][scheduler]["profiles"] == ["legacy-scheduler"]
    for spec in targets["targets"].values():
        assert scheduler not in spec.get("services", [])
        assert scheduler not in spec.get("runtime_services", [])
    code = compose["services"][family.code_server]
    assert code["build"]["target"] == "dagster"
    assert code["mem_limit"] == "2g" and code["init"] is True
    assert code["healthcheck"]["test"][-1] == "12603"
    assert code["healthcheck"]["test"][-2] == compose["x-dagster-code-server-probe"]


def test_missing_own_or_partial_legacy_family_still_fails():
    compose, targets = documents()
    targets["targets"]["conc"]["dagster"]["control_plane"] = "own"
    with pytest.raises(DeploymentContractError, match="webserver"):
        derive_dagster_family(compose, targets, "conc")
    targets["targets"]["conc"]["dagster"]["control_plane"] = "shared"
    family = derive_dagster_family(compose, targets, "conc")
    compose["services"]["legacy-web"] = {"command": ["dagster-webserver"], "depends_on": [family.code_server]}
    with pytest.raises(DeploymentContractError, match="daemon"):
        derive_dagster_family(compose, targets, "conc")


def test_existing_locations_keep_their_limits_and_concierge_has_bounded_lanes():
    config = yaml.safe_load((ROOT / "config/dagster-shared/dagster.yaml").read_text())
    limits = config["concurrency"]["runs"]["tag_concurrency_limits"]
    by_location = {entry["value"]: entry["limit"] for entry in limits if entry["key"] == "dagster/code_location"}
    assert by_location["ktc.dagster.definitions"] == 2
    assert by_location["kor-travel-transport"] == 3
    assert all(by_location[name] == 10 for name in ("kortravelmap.dagster.definitions", "pinvi.etl.definitions", "kortravelgeo_dagster.definitions", "kortravelweather_dagster.definitions"))
    lanes = {entry["value"]: entry["limit"] for entry in limits if entry["key"] == "kortravelcommon/job"}
    assert lanes == {"concierge/" + name: 1 for name in ("concierge_interactive", "concierge_batch", "concierge_source_scan", "concierge_feature_exports")}
