from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.m05_isolated_harness import (
    M05_ISOLATED_HARNESS_KIND,
    M05_ISOLATED_MANAGER_ADMISSION_KIND,
    M05IsolatedHarnessPlan,
    M05IsolatedNetworkExpectation,
    M05IsolatedPairEvidence,
    M05IsolatedRuntimeExpectation,
    M05IsolatedServiceExpectation,
    assert_m05_isolated_runtime,
    build_m05_isolated_manager_admission,
    build_m05_isolated_runtime_provenance,
    claim_m05_isolated_harness_ledger,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    current_pinned_runtime_release,
)
from kor_travel_docker_manager.services.runtime_execution_identity import (
    canonical_execution_identity_sha256,
)

PINNED_RUNTIME_RELEASE = current_pinned_runtime_release()


def _plan() -> M05IsolatedHarnessPlan:
    manager_revision = "a" * 40
    return M05IsolatedHarnessPlan(
        release=PINNED_RUNTIME_RELEASE,
        manager_source_revision=manager_revision,
        execution_identity_sha256=canonical_execution_identity_sha256(
            source_pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
            manager_source_revision=manager_revision,
        ),
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
            "pinvi-api": M05IsolatedServiceExpectation(
                "pinvi", 8000, 30102, "sha256:" + "3" * 64, ("map",)
            ),
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
            "Networks": {
                expectation.network_for(role).name: {
                    "NetworkID": expectation.network_for(role).network_id
                }
                for role in (
                    service_expectation.role,
                    *service_expectation.additional_network_roles,
                )
            },
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


def _image_inspects(expectation: M05IsolatedRuntimeExpectation) -> dict[str, dict[str, object]]:
    return {
        service.image_id: {
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": (
                        expectation.pair.map_source_revision
                        if service.role == "map"
                        else expectation.pair.pinvi_source_revision
                    )
                }
            },
            "Id": service.image_id,
        }
        for service in expectation.services.values()
    }


def _provenance_image_inspects(
    expectation: M05IsolatedRuntimeExpectation,
) -> dict[str, dict[str, object]]:
    image_ids = {
        "map-admin": "sha256:" + "4" * 64,
        "map-api": expectation.services["map-api"].image_id,
        "map-frontend": "sha256:" + "5" * 64,
        "pinvi-api": expectation.services["pinvi-api"].image_id,
        "pinvi-dagster": "sha256:" + "6" * 64,
        "pinvi-web": "sha256:" + "7" * 64,
    }
    return {
        name: {
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": (
                        expectation.pair.map_source_revision
                        if name.startswith("map-")
                        else expectation.pair.pinvi_source_revision
                    )
                }
            },
            "Id": image_id,
        }
        for name, image_id in image_ids.items()
    }


def test_plan_claim_is_canonical_and_does_not_include_transaction() -> None:
    plan = _plan()
    payload = json.loads(plan.claim_bytes)
    assert payload == {
        "harness": M05_ISOLATED_HARNESS_KIND,
        "manager_source_revision": "a" * 40,
        "execution_identity_sha256": _plan().execution_identity_sha256,
        "pinset_sha256": PINNED_RUNTIME_RELEASE.pinset_sha256,
        "version": 1,
    }
    assert plan.map_project.startswith("m05i-map-")
    assert plan.pinvi_project.startswith("m05i-pinvi-")


def test_claim_is_durable_and_appends_attempt_ordinal(tmp_path: Path) -> None:
    """재claim은 파일명 ordinal로 append-only 감사 흔적을 남긴다(R1-S2).

    재실행 허용 여부의 정본은 runtime execution registry(phase-scoped 차단)다 —
    ledger가 O_EXCL 파일명 충돌로 scoped 재실행까지 소각하면 I-1이 무효화된다.
    claim 내용(claim_bytes)은 identity-순수하게 유지되고 ordinal은 파일명에만 붙는다.
    """
    ledger_root = tmp_path / "ledger"
    ledger_root.mkdir(mode=0o700)
    plan = _plan()
    if os.geteuid() == 0:
        first = claim_m05_isolated_harness_ledger(ledger_root=ledger_root, plan=plan)
        assert first.read_bytes() == plan.claim_bytes
        assert first.stat().st_mode & 0o777 == 0o600
        assert first.name == plan.ledger_filename
        second = claim_m05_isolated_harness_ledger(ledger_root=ledger_root, plan=plan)
        assert second.name == f"{plan.ledger_filename}-01"
        assert second.read_bytes() == plan.claim_bytes
        assert first.exists() and second.exists()
    else:
        pytest.skip("root-only durable ledger contract")


def test_claim_attempts_are_capped(tmp_path: Path) -> None:
    """ordinal 폭주 방어 — 상한 도달 시 fail-closed."""
    ledger_root = tmp_path / "ledger"
    ledger_root.mkdir(mode=0o700)
    plan = _plan()
    if os.geteuid() == 0:
        base = plan.ledger_filename
        (ledger_root / base).write_bytes(plan.claim_bytes)
        for ordinal in range(1, 32):
            (ledger_root / f"{base}-{ordinal:02d}").write_bytes(plan.claim_bytes)
        with pytest.raises(DeploymentContractError, match="exceeded the limit"):
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
        image_inspects=_image_inspects(expectation),
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
            image_inspects=_image_inspects(expectation),
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
            image_inspects=_image_inspects(expectation),
            network_inspects=_network_inspects(expectation),
        )


def test_pinvi_api_accepts_only_the_planned_map_bridge_attachment() -> None:
    expectation = _expectation()
    containers = {
        "map-api": _inspect(expectation, service="map-api"),
        "pinvi-api": _inspect(expectation, service="pinvi-api"),
    }

    assert_m05_isolated_runtime(
        expectation=expectation,
        containers=containers,
        image_inspects=_image_inspects(expectation),
        network_inspects=_network_inspects(expectation),
    )

    pinvi_networks = containers["pinvi-api"]["NetworkSettings"]["Networks"]  # type: ignore[index]
    del pinvi_networks[expectation.plan.map_network]  # type: ignore[index]
    with pytest.raises(DeploymentContractError, match="network differs"):
        assert_m05_isolated_runtime(
            expectation=expectation,
            containers=containers,
            image_inspects=_image_inspects(expectation),
            network_inspects=_network_inspects(expectation),
        )


def test_pinvi_api_rejects_an_unplanned_third_network_attachment() -> None:
    expectation = _expectation()
    containers = {
        "map-api": _inspect(expectation, service="map-api"),
        "pinvi-api": _inspect(expectation, service="pinvi-api"),
    }
    pinvi_networks = containers["pinvi-api"]["NetworkSettings"]["Networks"]  # type: ignore[index]
    pinvi_networks["unplanned-network"] = {"NetworkID": "a" * 64}  # type: ignore[index]

    with pytest.raises(DeploymentContractError, match="network differs"):
        assert_m05_isolated_runtime(
            expectation=expectation,
            containers=containers,
            image_inspects=_image_inspects(expectation),
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
            image_inspects=_image_inspects(expectation),
            network_inspects=_network_inspects(expectation),
        )
    containers["map-api"] = _inspect(expectation, service="map-api")
    network_inspects = _network_inspects(expectation)
    network_inspects[expectation.plan.map_network]["Driver"] = "host"
    with pytest.raises(DeploymentContractError, match="Docker network differs"):
        assert_m05_isolated_runtime(
            expectation=expectation,
            containers=containers,
            image_inspects=_image_inspects(expectation),
            network_inspects=network_inspects,
        )


def test_expectation_rejects_a_missing_runtime_role() -> None:
    plan = _plan()
    with pytest.raises(DeploymentContractError, match="service set is invalid"):
        M05IsolatedRuntimeExpectation(
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
                "map-api": M05IsolatedServiceExpectation(
                    "map", 8000, 30101, "sha256:" + "2" * 64
                )
            },
        )


def test_runtime_rejects_image_label_drift() -> None:
    expectation = _expectation()
    containers = {
        "map-api": _inspect(expectation, service="map-api"),
        "pinvi-api": _inspect(expectation, service="pinvi-api"),
    }
    image_inspects = _image_inspects(expectation)
    image_inspects["sha256:" + "2" * 64]["Config"]["Labels"][
        "org.opencontainers.image.revision"
    ] = "0" * 40
    with pytest.raises(DeploymentContractError, match="source revision differs"):
        assert_m05_isolated_runtime(
            expectation=expectation,
            containers=containers,
            image_inspects=image_inspects,
            network_inspects=_network_inspects(expectation),
        )


def test_runtime_provenance_seals_all_six_image_identities() -> None:
    expectation = _expectation()
    receipt = build_m05_isolated_runtime_provenance(
        expectation=expectation,
        image_inspects=_provenance_image_inspects(expectation),
    )

    assert receipt["kind"] == "m05-isolated-runtime-provenance-v1"
    assert receipt["pinset_sha256"] == PINNED_RUNTIME_RELEASE.pinset_sha256
    assert receipt["map"] == {
        "admin_image_id": "sha256:" + "4" * 64,
        "api_image_id": "sha256:" + "2" * 64,
        "frontend_image_id": "sha256:" + "5" * 64,
        "full_openapi_sha256": "1" * 64,
        "source_revision": PINNED_RUNTIME_RELEASE.source_for("map").revision,
    }
    assert receipt["pinvi"] == {
        "api_image_id": "sha256:" + "3" * 64,
        "dagster_image_id": "sha256:" + "6" * 64,
        "source_revision": PINNED_RUNTIME_RELEASE.source_for("pinvi").revision,
        "web_image_id": "sha256:" + "7" * 64,
    }


def test_manager_admission_binds_the_exact_pair_and_transaction() -> None:
    expectation = _expectation()

    admission = build_m05_isolated_manager_admission(
        plan=expectation.plan, pair=expectation.pair
    )

    assert dict(admission) == {
        "kind": M05_ISOLATED_MANAGER_ADMISSION_KIND,
        "execution_identity_sha256": expectation.plan.execution_identity_sha256,
        "manager_source_revision": "a" * 40,
        "map_source_revision": PINNED_RUNTIME_RELEASE.source_for("map").revision,
        "pinset_sha256": PINNED_RUNTIME_RELEASE.pinset_sha256,
        "pinvi_source_revision": PINNED_RUNTIME_RELEASE.source_for("pinvi").revision,
        "transaction_id": "b" * 32,
        "version": 1,
    }


def test_runtime_provenance_rejects_unexpected_image_or_source_label() -> None:
    expectation = _expectation()
    image_inspects = _provenance_image_inspects(expectation)
    image_inspects["map-frontend"]["Config"]["Labels"][
        "org.opencontainers.image.revision"
    ] = "0" * 40
    with pytest.raises(DeploymentContractError, match="image provenance differs"):
        build_m05_isolated_runtime_provenance(
            expectation=expectation,
            image_inspects=image_inspects,
        )


def test_runtime_tolerates_unbound_expose_metadata_but_rejects_extra_bindings() -> None:
    """Docker는 EXPOSE 메타데이터를 binding 없는 Ports 항목(None)으로 항상
    나열한다 — Map api 이미지는 prod 12701을 EXPOSE하고 격리 publish는
    13701이라 키 정확일치는 원리적으로 통과 불가였다(e2e12 traceback 실측).
    published(실제 binding)만 비교하되, 추가 host binding은 여전히 거절한다."""

    expectation = _expectation()
    containers = {
        service: _inspect(expectation, service=service)
        for service in expectation.services
    }
    # EXPOSE-only 항목(binding None)은 published가 아니다 — 통과해야 한다.
    containers["map-api"]["NetworkSettings"]["Ports"]["12701/tcp"] = None
    assert_m05_isolated_runtime(
        expectation=expectation,
        containers=containers,
        image_inspects=_image_inspects(expectation),
        network_inspects=_network_inspects(expectation),
    )

    # 실제 binding이 하나 더 있으면 여전히 fail-close.
    containers["map-api"]["NetworkSettings"]["Ports"]["9999/tcp"] = [
        {"HostIp": "127.0.0.1", "HostPort": "29999"}
    ]
    with pytest.raises(DeploymentContractError, match="published ports differ"):
        assert_m05_isolated_runtime(
            expectation=expectation,
            containers=containers,
            image_inspects=_image_inspects(expectation),
            network_inspects=_network_inspects(expectation),
        )


def test_runtime_rejects_expected_port_that_is_exposed_but_unbound() -> None:
    """기대 container 포트가 EXPOSE만 되고 실제 publish되지 않으면 fail-close.

    published 집합 비교로 바뀐 뒤 유일하게 실패 경로가 이동한 케이스 —
    '기대 포트 미publish 통과' 회귀가 열릴 자리를 고정한다(적대 리뷰)."""

    expectation = _expectation()
    containers = {
        service: _inspect(expectation, service=service)
        for service in expectation.services
    }
    containers["map-api"]["NetworkSettings"]["Ports"]["8000/tcp"] = None
    with pytest.raises(DeploymentContractError, match="published ports differ"):
        assert_m05_isolated_runtime(
            expectation=expectation,
            containers=containers,
            image_inspects=_image_inspects(expectation),
            network_inspects=_network_inspects(expectation),
        )


def test_m05_isolated_runtime_role_is_wired_to_the_canonical_runtime_source_role() -> None:
    """GM-18 후속: 이 파일의 `M05IsolatedRuntimeRole`은 독립적인
    `Literal["map", "pinvi"]` 재선언이 아니라 `pinned_runtime_release.RuntimeSourceRole`을
    직접 참조해야 한다 — 그래야 정본이 role을 늘리거나 바꿀 때 이 파일도 같이 움직인다.

    identity 비교(`M05IsolatedRuntimeRole is RuntimeSourceRole`)만으로는 부족하다:
    `typing.Literal`은 동일 인자 조합을 `_tp_cache`로 캐시하므로, 이 파일이
    `from typing import Literal`을 유지한 채 `M05IsolatedRuntimeRole = Literal["map", "pinvi"]`를
    독립적으로 다시 적었더라도 오늘은 두 Literal이 같은 ("map", "pinvi") 인자를 쓰기 때문에
    캐시 덕분에 identity 비교가 우연히 참이 된다 — 즉 이 비교만으로는 '진짜 배선'과
    '우연히 같은 값의 독립 선언'을 구분하지 못한다. 그래서 소스 텍스트까지 확인해
    (1) import 블록이 실제로 canonical 모듈에서 `RuntimeSourceRole`을 가져오고,
    (2) 독립적인 `Literal[` 재선언이 전혀 없다는 것을 함께 대조한다."""

    import inspect

    from kor_travel_docker_manager.services import m05_isolated_harness
    from kor_travel_docker_manager.services.m05_isolated_harness import (
        M05IsolatedRuntimeRole,
    )
    from kor_travel_docker_manager.services.pinned_runtime_release import RuntimeSourceRole

    # 약한 신호: 오늘은 참이지만, 캐시된 우연의 일치일 수도 있어 이것만으로는 불충분하다.
    assert M05IsolatedRuntimeRole is RuntimeSourceRole

    source = inspect.getsource(m05_isolated_harness)

    # 강한 신호: import 블록이 실제로 canonical 모듈에서 RuntimeSourceRole을 가져온다.
    marker = "from kor_travel_docker_manager.services.pinned_runtime_release import"
    assert marker in source, "pinned_runtime_release import 블록을 찾을 수 없다"
    imported_block = source.split(marker, 1)[1].split(")", 1)[0]
    assert "RuntimeSourceRole" in imported_block

    # 가장 강한 신호: 독립적인 Literal[...] 재선언이 파일 어디에도 없다 — 있었다면
    # 위 identity 비교는 캐시 덕에 통과했을 뿐 실제로는 정본과 무관한 하드코딩이다.
    assert "Literal[" not in source
    assert "typing.Literal" not in source
