from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.m05_isolated_harness import (
    M05_ISOLATED_HARNESS_KIND,
    M05IsolatedHarnessPlan,
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


def _inspect(plan: M05IsolatedHarnessPlan, *, network: str, port: int, host_port: int) -> dict[str, object]:
    return {
        "Config": {"Labels": dict(plan.labels)},
        "HostConfig": {"NetworkMode": "bridge"},
        "Id": "c" * 64,
        "Image": "sha256:" + "d" * 64,
        "NetworkSettings": {
            "Networks": {network: {}},
            "Ports": {f"{port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]},
        },
        "State": {"Running": True},
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
    plan = _plan()
    containers = {
        "map-api": _inspect(plan, network=plan.map_network, port=8000, host_port=30101),
        "pinvi-api": _inspect(plan, network=plan.pinvi_network, port=8000, host_port=30102),
    }
    identities = assert_m05_isolated_runtime(
        plan=plan,
        containers=containers,
        expected_ports={"map-api": (8000, 30101), "pinvi-api": (8000, 30102)},
        expected_networks={"map-api": plan.map_network, "pinvi-api": plan.pinvi_network},
    )
    assert identities == {"map-api": "sha256:" + "d" * 64, "pinvi-api": "sha256:" + "d" * 64}


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
    plan = _plan()
    item = _inspect(plan, network=plan.map_network, port=8000, host_port=30101)
    target: object = item
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(DeploymentContractError, match=message):
        assert_m05_isolated_runtime(
            plan=plan,
            containers={"map-api": item},
            expected_ports={"map-api": (8000, 30101)},
            expected_networks={"map-api": plan.map_network},
        )


def test_runtime_rejects_a_second_or_wrong_network() -> None:
    plan = _plan()
    item = _inspect(plan, network=plan.map_network, port=8000, host_port=30101)
    networks = item["NetworkSettings"]["Networks"]  # type: ignore[index]
    networks[plan.pinvi_network] = {}  # type: ignore[index]
    with pytest.raises(DeploymentContractError, match="network differs"):
        assert_m05_isolated_runtime(
            plan=plan,
            containers={"map-api": item},
            expected_ports={"map-api": (8000, 30101)},
            expected_networks={"map-api": plan.map_network},
        )
