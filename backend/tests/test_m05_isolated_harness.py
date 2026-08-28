from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.m05_isolated_harness import (
    M05_ISOLATED_HARNESS_KIND,
    M05IsolatedHarnessPlan,
    M05IsolatedNetworkExpectation,
    M05IsolatedPairEvidence,
    M05IsolatedRuntimeExpectation,
    M05IsolatedServiceExpectation,
    assert_m05_isolated_runtime,
    claim_m05_isolated_harness_ledger,
)
from kor_travel_docker_manager.services.pinned_runtime_release import PINNED_RUNTIME_RELEASE


def _plan() -> M05IsolatedHarnessPlan:
    return M05IsolatedHarnessPlan(
        release=PINNED_RUNTIME_RELEASE,
        manager_source_revision="a" * 40,
        transaction_id="b" * 32,
    )


def _expectation() -> M05IsolatedRuntimeExpectation:
    plan = _plan()
    return M05IsolatedRuntimeExpectation(
        plan=plan,
        networks=(
            M05IsolatedNetworkExpectation("map", plan.map_network, "e" * 64),
            M05IsolatedNetworkExpectation("pinvi", plan.pinvi_network, "f" * 64),
        ),
        pair=M05IsolatedPairEvidence(
            map_full_openapi_sha256="1" * 64,
            map_source_revision=PINNED_RUNTIME_RELEASE.source_for("map").revision,
            pinvi_full_openapi_sha256="1" * 64,
            pinvi_source_revision=PINNED_RUNTIME_RELEASE.source_for("pinvi").revision,
        ),
        services={
            "map-api": M05IsolatedServiceExpectation("map", 8000, 30101, "sha256:" + "2" * 64),
            "pinvi-api": M05IsolatedServiceExpectation("pinvi", 8000, 30102, "sha256:" + "3" * 64),
        },
    )


def _inspect(
    expectation: M05IsolatedRuntimeExpectation, *, service: str
) -> dict[str, object]:
    plan = expectation.plan
    service_expectation = expectation.services[service]
    network = expectation.network_for(service_expectation.role)
    source_revision = (
        expectation.pair.map_source_revision
        if service_expectation.role == "map"
        else expectation.pair.pinvi_source_revision
    )
    return {
        "Config": {
            "Labels": {
                **dict(plan.labels),
                "org.opencontainers.image.revision": source_revision,
            }
        },
        "HostConfig": {"NetworkMode": network.name},
        "Id": "c" * 64,
        "Image": service_expectation.image_id,
        "NetworkSettings": {
            "Networks": {network.name: {"NetworkID": network.network_id}},
            "Ports": {
                f"{service_expectation.container_port}/tcp": [
                    {"HostIp": "127.0.0.1", "HostPort": str(service_expectation.host_port)}
                ]
            },
        },
        "State": {"Running": True},
    }


def _network_inspects(expectation: M05IsolatedRuntimeExpectation) -> dict[str, dict[str, object]]:
    return {
        item.name: {"Driver": "bridge", "Id": item.network_id, "Internal": False, "Labels": dict(expectation.plan.labels), "Name": item.name}
        for item in expectation.networks
    }


def test_plan_claim_is_canonical_and_does_not_include_transaction() -> None:
    plan = _plan()
    payload = json.loads(plan.claim_bytes)
    assert payload == {
        "harness": M05_ISOLATED_HARNESS_KIND,
        "manager_source_revision": "a" * 40,
        "pinset_sha256": PINNED_RUNTIME_RELEASE.pinset_sha256,
        "version": 1,
    }
    assert plan.map_project.startswith("m05i-map-")
    assert plan.pinvi_project.startswith("m05i-pinvi-")


def test_claim_is_durable_and_refuses_replay(tmp_path: Path) -> None:
    ledger_root = tmp_path / "ledger"
    ledger_root.mkdir(mode=0o700)
    plan = _plan()
    if os.geteuid() == 0:
        claim = claim_m05_isolated_harness_ledger(ledger_root=ledger_root, plan=plan)
        assert claim.read_bytes() == plan.claim_bytes
        assert claim.stat().st_mode & 0o777 == 0o600
        with pytest.raises(DeploymentContractError, match="already claimed"):
            claim_m05_isolated_harness_ledger(ledger_root=ledger_root, plan=plan)
    else:
        pytest.skip("root-only durable ledger contract")


def test_runtime_accepts_only_expected_loopback_bridge_bindings() -> None:
    expectation = _expectation()
    containers = {
        "map-api": _inspect(expectation, service="map-api"),
        "pinvi-api": _inspect(expectation, service="pinvi-api"),
    }
    identities = assert_m05_isolated_runtime(
        expectation=expectation,
        containers=containers,
        network_inspects=_network_inspects(expectation),
    )
    assert identities == {"map-api": "sha256:" + "2" * 64, "pinvi-api": "sha256:" + "3" * 64}


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("HostConfig", "NetworkMode"), "host", "bridge network"),
        (("NetworkSettings", "Ports", "8000/tcp", 0, "HostIp"), "0.0.0.0", "loopback"),
        (("Config", "Labels", "io.kortravelmap.m05.pinset"), "e" * 64, "labels differ"),
    ],
)
def test_runtime_rejects_topology_or_provenance_drift(
    path: tuple[object, ...], value: object, message: str
) -> None:
    expectation = _expectation()
    item = _inspect(expectation, service="map-api")
    target: object = item
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(DeploymentContractError, match=message):
        assert_m05_isolated_runtime(
            expectation=expectation,
            containers={
                "map-api": item,
                "pinvi-api": _inspect(expectation, service="pinvi-api"),
            },
            network_inspects=_network_inspects(expectation),
        )


def test_runtime_rejects_a_second_or_wrong_network() -> None:
    expectation = _expectation()
    item = _inspect(expectation, service="map-api")
    networks = item["NetworkSettings"]["Networks"]  # type: ignore[index]
    networks[expectation.plan.pinvi_network] = {}  # type: ignore[index]
    with pytest.raises(DeploymentContractError, match="network differs"):
        assert_m05_isolated_runtime(
            expectation=expectation,
            containers={
                "map-api": item,
                "pinvi-api": _inspect(expectation, service="pinvi-api"),
            },
            network_inspects=_network_inspects(expectation),
        )


def test_runtime_rejects_image_or_network_inspect_drift() -> None:
    expectation = _expectation()
    containers = {
        "map-api": _inspect(expectation, service="map-api"),
        "pinvi-api": _inspect(expectation, service="pinvi-api"),
    }
    containers["map-api"]["Image"] = "sha256:" + "4" * 64
    with pytest.raises(DeploymentContractError, match="image ID differs"):
        assert_m05_isolated_runtime(
            expectation=expectation,
            containers=containers,
            network_inspects=_network_inspects(expectation),
        )
    containers["map-api"] = _inspect(expectation, service="map-api")
    network_inspects = _network_inspects(expectation)
    network_inspects[expectation.plan.map_network]["Driver"] = "host"
    with pytest.raises(DeploymentContractError, match="Docker network differs"):
        assert_m05_isolated_runtime(
            expectation=expectation,
            containers=containers,
            network_inspects=network_inspects,
        )
