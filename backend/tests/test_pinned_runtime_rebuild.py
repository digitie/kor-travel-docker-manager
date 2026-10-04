from __future__ import annotations

import copy
import inspect
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
import yaml

from kor_travel_docker_manager.services import c6c_deployment, runtime_pin_registry
from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services import database_runtime as database_runtime_module
from kor_travel_docker_manager.services import (
    pinned_runtime_rebuild as pinned_runtime_rebuild_module,
)
from kor_travel_docker_manager.services import runtime_topology as runtime_topology_module
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
)
from kor_travel_docker_manager.services.database_runtime import (
    DatabaseRuntime,
)
from kor_travel_docker_manager.services.deploy_status import (
    DeployedDatabase,
    DeployStatus,
    deploy_status_path,
    read_deploy_status,
    write_deploy_status,
)
from kor_travel_docker_manager.services.map_application_candidate import (
    MapApplicationCandidate,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeGeneration,
    pinned_runtime_state_paths,
)
from kor_travel_docker_manager.services.pinned_runtime_rebuild import (
    CandidateRuntimeBuild,
    build_candidate_generation,
    generation_companion_services,
    generation_compose_environment,
    map_application_300_paired_build_image_names,
    parse_candidate_static_head,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    CANONICAL_RUNTIME_SOURCE_URLS,
    PinnedRuntimeRelease,
    PinnedRuntimeSourceSpec,
    canonical_pinset_sha256,
    current_pinned_runtime_release,
    source_specs_for,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    MaterializedRuntimeSource,
    PinnedRuntimeSourceMaterialization,
)
from kor_travel_docker_manager.services.runtime_topology import (
    COMPOSE_BUILT_RUNTIME_SLOTS,
    RUNTIME_SLOTS,
    RuntimeSlot,
    RuntimeTopology,
    derive_dagster_families,
    runtime_topology,
)

PINNED_RUNTIME_RELEASE = current_pinned_runtime_release()
#: 모든 target이 `own`일 때 slot이 도는 서비스 — ADR-54 파생 이전 literal 그대로다. 파생이 이것과 같아야
#: 배포 기록(`deploy-status.json`의 images 키)·candidate tag·보존 tag가 바뀌지 않는다.
RUNTIME_SERVICES: tuple[str, ...] = (
    "kor-travel-map-api",
    "kor-travel-map-ui",
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
    "pinvi-api",
    "pinvi-web",
    "pinvi-dagster",
)
COMPOSE_BUILT_RUNTIME_SERVICES: tuple[str, ...] = (
    "kor-travel-map-ui",
    "pinvi-api",
    "pinvi-web",
    "pinvi-dagster",
)
_WAIT_TIMEOUT = str(compose_service_module._COMPOSE_WAIT_TIMEOUT_SECONDS)

#: pinned pair의 기제는 "Map·PinVi 모두 own" 기준선 위에서 본다 — 전환은 `flipped=(…)`/`_flip`으로 얹는다(conftest).
pytestmark = pytest.mark.usefixtures("own_pinned_pair")


@pytest.fixture(autouse=True)
def _isolate_runtime_pin_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """rebuild orchestration 단위는 lifecycle 정책과 분리해 검증한다.

    저장소 기본 registry는 현재 pinset을 terminal로 등재하고 있어서
    ``rebuild_pinned_runtime``이 시작 게이트에서 즉시 거부된다(그 게이트 자체는
    ``test_runtime_pin_registry``와 아래 전용 회귀가 검증한다). 여기서는 조건 없는
    차단만 제거한 사본으로 격리해 orchestration 경로를 그대로 확인한다.
    phase 한정 차단(d9 계열)은 seed 그대로 남긴다 — 배포를 막지 않아야 하는 항목이다.
    """

    packaged = Path(__file__).resolve().parents[2] / "config" / "runtime-pins.seed.json"
    document = json.loads(packaged.read_text(encoding="utf-8"))
    document["blocked_pinsets"] = [
        entry for entry in document.get("blocked_pinsets", []) if entry.get("phase")
    ]
    isolated = tmp_path / "runtime-pins-unit.json"
    isolated.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv(runtime_pin_registry.RUNTIME_PINS_FILE_ENV, str(isolated))
    monkeypatch.setenv(
        runtime_pin_registry.RUNTIME_PINS_PUBLIC_FILE_ENV,
        str(tmp_path / "public-runtime-pins.json"),
    )
    runtime_pin_registry.clear_runtime_pin_registry_cache()
    yield
    runtime_pin_registry.clear_runtime_pin_registry_cache()


@pytest.fixture(autouse=True)
def _isolate_map_application_300_base_image_preflight(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """orchestration unit은 전용 회귀 밖 host I/O/one-shot을 격리한다."""

    if not request.node.name.startswith("test_map_application_300_python_base_images"):
        monkeypatch.setattr(
            compose_service_module,
            "_ensure_map_application_300_python_base_images",
            lambda _sources: None,
        )


@pytest.fixture
def linux_tmp_path() -> Iterator[Path]:
    """owner/mode receipt test는 NTFS pytest temp가 아닌 Linux filesystem을 쓴다."""

    path = Path(tempfile.mkdtemp(prefix="ktdm-pinned-runtime-test.", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path)


@pytest.fixture(autouse=True)
def _bypass_root_host_lease_in_nonroot_unit_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """root 전용 host primitive는 별도 회귀 외에는 unit orchestration에서 격리한다."""

    # 이 모듈의 대형 orchestration fixture는 각 사례가 필요한 v5 source release를
    # 직접 주입한다. 실제 root registry/trusted Manager v6 snapshot은 만들지 않으므로
    # admission 판정은 여기서만 격리한다. 판정 자체는 ``test_runtime_pin_registry``가 소유한다.
    monkeypatch.setattr(
        compose_service_module,
        "_pinned_runtime_admission_warnings",
        lambda _pinset_sha256: [],
    )
    # 각 orchestration 회귀는 그 이전/이후 phase만 격리한다. trusted `/opt` `.env` 대신
    # 테스트 env를 캡처하고 lifecycle 게이트·token 검증은 건너뛰지만, lock(G 하나,
    # ADR-51 C-3)·admission·frozen snapshot 전달 순서는 production과 동일하게 유지한다.
    # G는 conftest가 테스트마다 tmp로 옮겨 둔다.
    @contextmanager
    def isolated_rebuild_environment_lock(*, prewrite_admission: Any) -> Any:
        with compose_service_module.manager_mutation_lock():
            snapshot = compose_service_module._capture_compose_environment_snapshot(
                environment_override=None
            )
            # M05 폐기 전에는 여기서 role 자격증명이 이미 구성된 상태를 흉내 냈다.
            # 이제 rebuild가 `.env`에 자격증명을 쓰지 않으므로 흉내 낼 것이 없다.
            prewrite_admission(snapshot)
            yield snapshot

    monkeypatch.setattr(
        compose_service_module,
        "_pinned_runtime_rebuild_environment_lock",
        isolated_rebuild_environment_lock,
    )


def _sources() -> PinnedRuntimeSourceMaterialization:
    return PinnedRuntimeSourceMaterialization(
        release=PINNED_RUNTIME_RELEASE,
        sources=(
            MaterializedRuntimeSource(
                role="map",
                root=Path("/state/map"),
                revision=PINNED_RUNTIME_RELEASE.source_for("map").revision,
                tree="a" * 40,
            ),
            MaterializedRuntimeSource(
                role="pinvi",
                root=Path("/state/pinvi"),
                revision=PINNED_RUNTIME_RELEASE.source_for("pinvi").revision,
                tree="b" * 40,
            ),
        ),
    )


def _opaque_transaction() -> Any:
    # 재구축 compose 실행기는 R3 chokepoint에서 resolved 문서의 PostgreSQL 서버만 본다.
    # 그 밖은 여전히 불투명하다.
    return SimpleNamespace(resolved={"services": {}})


def _sources_for(release: PinnedRuntimeRelease) -> PinnedRuntimeSourceMaterialization:
    return PinnedRuntimeSourceMaterialization(
        release=release,
        sources=(
            MaterializedRuntimeSource(
                role="map",
                root=Path("/state/map"),
                revision=release.source_for("map").revision,
                tree="a" * 40,
            ),
            MaterializedRuntimeSource(
                role="pinvi",
                root=Path("/state/pinvi"),
                revision=release.source_for("pinvi").revision,
                tree="b" * 40,
            ),
        ),
    )


def _paired_builder_inputs(tmp_path: Path) -> PinnedRuntimeSourceMaterialization:
    map_root = tmp_path / "map"
    script = map_root / "scripts" / "build-application-300-paired-candidate.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    release = PINNED_RUNTIME_RELEASE
    return PinnedRuntimeSourceMaterialization(
        release=release,
        sources=(
            MaterializedRuntimeSource(
                role="map",
                root=map_root,
                revision=release.source_for("map").revision,
                tree="a" * 40,
            ),
            MaterializedRuntimeSource(
                role="pinvi",
                root=tmp_path / "pinvi",
                revision=release.source_for("pinvi").revision,
                tree="b" * 40,
            ),
        ),
    )


def _map_application_candidate(
    sources: PinnedRuntimeSourceMaterialization | None = None,
    *,
    api_image_id: str = f"sha256:{101:064x}",
    dagster_image_id: str = f"sha256:{102:064x}",
) -> MapApplicationCandidate:
    materialized = sources or _sources()
    return MapApplicationCandidate(
        candidate_commit=materialized.source_for("map").revision,
        candidate_git_tree=materialized.source_for("map").tree,
        api_image_id=api_image_id,
        dagster_image_id=dagster_image_id,
        dagster_config_sha256="b" * 64,
        application_head="300",
    )


def _candidate_image_ids(
    candidate: MapApplicationCandidate,
) -> dict[RuntimeSlot, str]:
    image_ids: dict[RuntimeSlot, str] = {
        slot: f"sha256:{index + 1:064x}"
        for index, slot in enumerate(RUNTIME_SLOTS)
    }
    image_ids["map_api"] = candidate.api_image_id
    image_ids["map_dagster"] = candidate.dagster_image_id
    image_ids["map_dagster_daemon"] = candidate.dagster_image_id
    return image_ids


def _candidate_generation(
    sources: PinnedRuntimeSourceMaterialization | None = None,
) -> PinnedRuntimeGeneration:
    materialized = sources or _sources()
    paired = _map_application_candidate(materialized)
    return build_candidate_generation(
        sources=materialized,
        map_application_candidate=paired,
        image_ids=_candidate_image_ids(paired),
        pinvi_head="pinvi-head",
    )


def _release_with_pinvi_revision(pinvi_revision: str) -> PinnedRuntimeRelease:
    sources = (
        PINNED_RUNTIME_RELEASE.source_for("map"),
        PinnedRuntimeSourceSpec(
            role="pinvi",
            canonical_url=CANONICAL_RUNTIME_SOURCE_URLS["pinvi"],
            revision=pinvi_revision,
        ),
    )
    return PinnedRuntimeRelease(
        version=5,
        sources=sources,
        pinset_sha256=canonical_pinset_sha256(version=5, sources=sources),
    )


def test_candidate_build_uses_private_deterministic_tags_and_staged_sources() -> None:
    sources = _sources()
    candidate = _map_application_candidate(sources)
    build = CandidateRuntimeBuild(sources, candidate)
    paired_build_names = map_application_300_paired_build_image_names(sources)

    environment = build.compose_environment()

    assert environment["KOR_TRAVEL_MAP_REPO_DIR"] == "/state/map"
    assert environment["PINVI_REPO_DIR"] == "/state/pinvi"
    assert environment["PINVI_BUILD_ENVIRONMENT"] == "production"
    assert set(build.image_names) == set(COMPOSE_BUILT_RUNTIME_SLOTS)
    assert build.build_services == COMPOSE_BUILT_RUNTIME_SERVICES
    assert set(paired_build_names) == {"map_api", "map_dagster"}
    assert set(paired_build_names).isdisjoint(build.image_names)
    prefix = "kor-travel-docker-manager/pinned-runtime-candidate-v6/"
    pinset = PINNED_RUNTIME_RELEASE.pinset_sha256
    # tag 이름은 파생 이전과 같다 — 같은 pair의 재실행이 이미 있는 이미지를 다시 빌드하지 않는다.
    assert dict(paired_build_names) == {
        "map_api": f"{prefix}kor-travel-map-api:{pinset}",
        "map_dagster": f"{prefix}kor-travel-map-dagster:{pinset}",
    }
    assert dict(build.image_names) == {
        slot: f"{prefix}{service}:{pinset}"
        for slot, service in zip(COMPOSE_BUILT_RUNTIME_SLOTS, COMPOSE_BUILT_RUNTIME_SERVICES, strict=True)
    }
    assert set(build.runtime_image_references) == set(RUNTIME_SLOTS)
    assert build.runtime_image_references["map_api"] == candidate.api_image_id
    assert (
        build.runtime_image_references["map_dagster"]
        == build.runtime_image_references["map_dagster_daemon"]
        == candidate.dagster_image_id
    )
    assert environment["KOR_TRAVEL_MAP_API_IMAGE"] == candidate.api_image_id
    assert environment["KOR_TRAVEL_MAP_DAGSTER_IMAGE"] == candidate.dagster_image_id
    assert "KOR_TRAVEL_MAP_DAGSTER_DAEMON_IMAGE" not in environment
    # ADR-53: Map DB는 공용 instance에 산다 — 재구축은 PostgreSQL 이미지를 주입하지 않는다.
    assert not [name for name in environment if "POSTGRES" in name]
    # ADR-51 D-3: M1 이후 Map storage one-shot은 config sha를 읽지 않는다.
    assert "KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256" not in environment


def test_compose_run_mutation_scope_stops_at_the_service_name() -> None:
    assert ComposeService._compose_mutation_identifiers(
        [
            "--profile",
            "bootstrap",
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "/bin/sh",
            "kor-travel-map-migration-boundary",
            "./docker/migrate-to-m01-bootstrap-boundary.sh",
        ]
    ) == ["kor-travel-map-migration-boundary"]


def test_materialized_compose_escapes_environment_dollars_without_changing_commands() -> None:
    resolved: dict[str, Any] = {
        "services": {
            "bootstrap": {
                "environment": {
                    "DSN": "postgresql://user:literal$aB@host/db",
                    "ALREADY_ESCAPED": "literal$$aB",
                    "PLAIN": "value",
                },
                "command": ["sh", "-ec", 'psql "$$DSN"'],
            },
            "list-env": {
                "environment": ["VALUE=literal$aB", "PLAIN=value"],
            },
        }
    }

    actual = compose_service_module._escape_materialized_compose_environment_values(
        resolved
    )

    assert actual["services"]["bootstrap"]["environment"]["DSN"] == (
        "postgresql://user:literal$$aB@host/db"
    )
    assert actual["services"]["bootstrap"]["environment"]["ALREADY_ESCAPED"] == (
        "literal$$aB"
    )
    assert actual["services"]["bootstrap"]["environment"]["PLAIN"] == "value"
    assert actual["services"]["bootstrap"]["command"] == [
        "sh",
        "-ec",
        'psql "$$DSN"',
    ]
    assert actual["services"]["list-env"]["environment"] == [
        "VALUE=literal$$aB",
        "PLAIN=value",
    ]
    assert resolved["services"]["bootstrap"]["environment"]["DSN"] == (
        "postgresql://user:literal$aB@host/db"
    )


def test_static_head_parser_accepts_exact_one_line_schema_contract() -> None:
    assert parse_candidate_static_head(
        '{"pinvi_head":"20260806_0001","schema":"pinvi.candidate-head.v1"}',
        schema="pinvi.candidate-head.v1",
        field="pinvi_head",
    ) == "20260806_0001"

    with pytest.raises(DeploymentContractError, match="output"):
        parse_candidate_static_head(
            '{"head":"x","schema":"pinvi.candidate-head.v1"}\nextra',
            schema="pinvi.candidate-head.v1",
            field="pinvi_head",
        )


def test_candidate_generation_binds_all_runtime_inputs() -> None:
    sources = _sources()
    paired = _map_application_candidate(sources)
    image_ids = _candidate_image_ids(paired)
    generation = build_candidate_generation(
        sources=sources,
        map_application_candidate=paired,
        image_ids=image_ids,
        pinvi_head="20260806_0001",
        recorded_at="2026-08-06T00:00:00+00:00",
    )

    assert generation.map_application_head == "300"
    assert generation.map_source_revision == paired.candidate_commit
    assert generation.map_application_300_candidate_evidence.candidate_git_tree == (
        paired.candidate_git_tree
    )

    runtime_environment = generation_compose_environment(generation)

    assert runtime_environment["PINVI_DAGSTER_IMAGE"] == generation.pinvi_dagster_image_id
    assert runtime_environment["KOR_TRAVEL_MAP_API_IMAGE"] == paired.api_image_id
    assert runtime_environment["KOR_TRAVEL_MAP_DAGSTER_IMAGE"] == paired.dagster_image_id
    assert "KOR_TRAVEL_MAP_DAGSTER_DAEMON_IMAGE" not in runtime_environment
    assert not [name for name in runtime_environment if "POSTGRES" in name]
    # ADR-51 D-3: permit 디렉터리와 config sha는 runtime override에서 빠졌다.
    assert not [
        name
        for name in runtime_environment
        if "PERMIT" in name or "CONFIG_SHA256" in name
    ]


def test_candidate_generation_rejects_paired_source_and_image_drift() -> None:
    sources = _sources()
    paired = _map_application_candidate(sources)
    image_ids = _candidate_image_ids(paired)

    with pytest.raises(DeploymentContractError, match="source differs"):
        CandidateRuntimeBuild(
            sources,
            replace(paired, candidate_git_tree="f" * 40),
        )

    with pytest.raises(DeploymentContractError, match="Map API candidate image differs"):
        build_candidate_generation(
            sources=sources,
            map_application_candidate=paired,
            image_ids={**image_ids, "map_api": f"sha256:{999:064x}"},
            pinvi_head="20260806_0001",
        )

    with pytest.raises(DeploymentContractError, match="web and daemon"):
        build_candidate_generation(
            sources=sources,
            map_application_candidate=paired,
            image_ids={
                **image_ids,
                "map_dagster_daemon": f"sha256:{998:064x}",
            },
            pinvi_head="20260806_0001",
        )


def test_rebuild_requires_root_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    with pytest.raises(DeploymentContractError, match="requires root execution"):
        ComposeService().rebuild_pinned_runtime()


def test_map_application_300_python_base_images_pull_and_reinspect_missing_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _paired_builder_inputs(tmp_path)
    docker_directory = sources.source_for("map").root / "docker"
    docker_directory.mkdir()
    base = "python@sha256:" + "a" * 64
    for name in ("api.Dockerfile", "dagster.Dockerfile"):
        (docker_directory / name).write_text(
            f"FROM {base} AS builder\nFROM {base} AS runtime\n",
            encoding="utf-8",
        )
    runner = Mock(
        side_effect=(
            subprocess.CompletedProcess(args=(), returncode=1),
            subprocess.CompletedProcess(args=(), returncode=0),
            subprocess.CompletedProcess(args=(), returncode=0),
        )
    )
    monkeypatch.setattr(compose_service_module.subprocess, "run", runner)

    compose_service_module._ensure_map_application_300_python_base_images(sources)

    assert [call.args[0] for call in runner.call_args_list] == [
        ["docker", "image", "inspect", base],
        ["docker", "pull", base],
        ["docker", "image", "inspect", base],
    ]
    for invocation in runner.call_args_list:
        assert invocation.kwargs["stdout"] is subprocess.DEVNULL
    # 존재 확인 inspect의 실패는 예상된 분기라 버리고, pull·재확인의 stderr는 실패에 싣는다.
    assert [invocation.kwargs["stderr"] for invocation in runner.call_args_list] == [
        subprocess.DEVNULL,
        subprocess.PIPE,
        subprocess.PIPE,
    ]


def test_map_application_300_python_base_images_reject_invalid_source_contract(
    tmp_path: Path,
) -> None:
    sources = _paired_builder_inputs(tmp_path)
    docker_directory = sources.source_for("map").root / "docker"
    docker_directory.mkdir()
    (docker_directory / "api.Dockerfile").write_text(
        "FROM python:latest AS builder\n", encoding="utf-8"
    )
    (docker_directory / "dagster.Dockerfile").write_text(
        "FROM python:latest AS builder\n", encoding="utf-8"
    )

    with pytest.raises(
        DeploymentContractError,
        match="Map application candidate base image contract is invalid",
    ):
        compose_service_module._ensure_map_application_300_python_base_images(sources)


def test_map_application_300_python_base_images_reject_extra_docker_stage(
    tmp_path: Path,
) -> None:
    sources = _paired_builder_inputs(tmp_path)
    docker_directory = sources.source_for("map").root / "docker"
    docker_directory.mkdir()
    base = "python@sha256:" + "a" * 64
    for name in ("api.Dockerfile", "dagster.Dockerfile"):
        (docker_directory / name).write_text(
            "\n".join(
                (
                    f"FROM {base} AS builder",
                    f"FROM {base} AS runtime",
                    "FROM registry.example/other:latest AS auxiliary",
                    "",
                )
            ),
            encoding="utf-8",
        )

    with pytest.raises(
        DeploymentContractError,
        match="Map application candidate base image contract is invalid",
    ):
        compose_service_module._ensure_map_application_300_python_base_images(sources)


def test_runtime_container_image_mismatch_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    candidate = _candidate_generation()
    records = [
        {"Service": runtime_service, "Name": f"container-{runtime_service}"}
        for runtime_service in RUNTIME_SERVICES
    ]
    observed = ComposeService._deployed_images(candidate, {})
    observed["pinvi-web"] = f"sha256:{999:064x}"
    monkeypatch.setattr(
        service,
        "_inspect_container_image_id",
        lambda container_name, *, label: observed[label],
    )

    with pytest.raises(
        DeploymentContractError,
        match="pinvi-web runtime image differs from committed generation",
    ):
        service._assert_pinned_runtime_container_images(
            records,
            expected_images=ComposeService._deployed_images(candidate, {}),
        )


def test_runtime_companion_containers_are_bound_to_their_owner_slot_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    candidate = _candidate_generation()
    companions: dict[str, RuntimeSlot] = {
        "kor-travel-map-dagster-code-server": "map_dagster",
        "pinvi-dagster-daemon": "pinvi_dagster",
    }
    records = [
        {"Service": name, "Name": f"container-{name}"}
        for name in (*RUNTIME_SERVICES, *companions)
    ]
    slot_images = candidate.image_ids
    observed: dict[str, str] = {
        **{service: slot_images[slot] for service, slot in zip(RUNTIME_SERVICES, RUNTIME_SLOTS, strict=True)},
        **{name: slot_images[owner] for name, owner in companions.items()},
    }
    monkeypatch.setattr(
        service,
        "_inspect_container_image_id",
        lambda container_name, *, label: observed[label],
    )

    expected = ComposeService._deployed_images(candidate, companions)
    service._assert_pinned_runtime_container_images(records, expected_images=expected)

    observed["pinvi-dagster-daemon"] = f"sha256:{998:064x}"
    with pytest.raises(
        DeploymentContractError,
        match="pinvi-dagster-daemon runtime image differs from committed generation",
    ):
        service._assert_pinned_runtime_container_images(records, expected_images=expected)

    with pytest.raises(DeploymentContractError, match="evidence is incomplete"):
        service._assert_pinned_runtime_container_images(
            records[:-1], expected_images=expected
        )


def test_generation_companions_are_non_slot_services_sharing_a_slot_image() -> None:
    image_ids = _candidate_generation().image_ids
    dagster_image = image_ids["map_dagster"]
    resolved = {
        "services": {
            **{
                service: {"image": image_ids[slot]}
                for service, slot in zip(RUNTIME_SERVICES, RUNTIME_SLOTS, strict=True)
            },
            "kor-travel-map-dagster-code-server": {"image": dagster_image},
            "kor-travel-map-dagster-storage-migrate": {"image": dagster_image},
            "pinvi-dagster-daemon": {"image": image_ids["pinvi_dagster"]},
            "prometheus": {"image": "prom/prometheus:v2.53.1"},
            "kor-travel-shared-postgres": {"image": image_ids["map_api"] + "x"},
        }
    }

    companions = generation_companion_services(
        resolved,
        image_ids,
        excluded_services=("kor-travel-map-dagster-storage-migrate",),
        topology=runtime_topology(),
    )

    # daemon과 dagster가 같은 이미지여도 owner는 RUNTIME_SLOTS 순서상 먼저인 slot이다.
    assert dict(companions) == {
        "kor-travel-map-dagster-code-server": "map_dagster",
        "pinvi-dagster-daemon": "pinvi_dagster",
    }
    with pytest.raises(DeploymentContractError, match="services are invalid"):
        generation_companion_services(
            {"services": []}, image_ids, excluded_services=(), topology=runtime_topology()
        )


def test_real_compose_generation_companions_are_every_dagster_process_sharing_an_image() -> None:
    """실제 compose에서 파생되는 companion 집합을 고정한다.

    code-server가 자기 이미지 변수를 따로 갖게 되면 companion에서 조용히 빠져 다시
    기동되지 않는다(2026-09-25 t56e/t56h: rebuild가 한 번도 Map code-server를 띄운
    적이 없었다). 그 회귀를 이 단언이 잡는다.
    """

    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    image_ids = _candidate_generation().image_ids
    image_by_variable = {
        variable: image_ids[slot]
        for slot, variable in pinned_runtime_rebuild_module._IMAGE_ENVIRONMENT.items()
    }
    services: dict[str, dict[str, str]] = {}
    for name, service in document["services"].items():
        raw_image = str(service.get("image", ""))
        match = re.fullmatch(r"\$\{([A-Z0-9_]+)[^}]*\}", raw_image)
        resolved_image = (
            image_by_variable.get(match.group(1), raw_image) if match else raw_image
        )
        services[name] = {"image": resolved_image}

    companions = generation_companion_services(
        {"services": services},
        image_ids,
        excluded_services=compose_service_module._PINNED_RUNTIME_ONESHOT_WRITERS,
        topology=runtime_topology(),
    )

    assert dict(companions) == {
        "kor-travel-map-dagster-code-server": "map_dagster",
        "pinvi-dagster-code-server": "pinvi_dagster",
        "pinvi-dagster-daemon": "pinvi_dagster",
    }


def test_rebuild_host_lease_blocks_before_source_or_database_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialize = Mock()

    def contended_rebuild_lease() -> object:
        raise DeploymentContractError("pinned runtime rebuild lease is already held")

    monkeypatch.setattr(
        compose_service_module,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "manager_mutation_lock",
        contended_rebuild_lease,
    )
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        materialize,
    )

    with pytest.raises(DeploymentContractError, match="rebuild lease is already held"):
        ComposeService().rebuild_pinned_runtime()

    materialize.assert_not_called()


def test_rebuild_requires_all_operation_tokens_before_source_or_database_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "COMPOSE_PROJECT_NAME": "f1d-token-preflight",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
    }
    environment = SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n")
    materialize = Mock()
    lock_events: list[str] = []

    @contextmanager
    def host_lease() -> Any:
        lock_events.append("host-enter")
        try:
            yield
        finally:
            lock_events.append("host-exit")

    monkeypatch.setattr(
        compose_service_module,
        "manager_mutation_lock",
        host_lease,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: environment,
    )
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        materialize,
    )

    with pytest.raises(DeploymentContractError) as captured:
        ComposeService().rebuild_pinned_runtime()

    assert compose_service_module.rebuild_failure_stage(captured.value) == "state_initialization"

    # ADR-51 C-3: 재구축의 lock은 G 하나다(실제 파일 lock 목록은
    # `test_global_mutation_lock_contention`의 (g)가 본다). 거부는 그 안에서 났다.
    assert lock_events == ["host-enter", "host-exit"]
    materialize.assert_not_called()


def test_frozen_compose_resolution_includes_bootstrap_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    commands: list[list[str]] = []
    candidate = {
        "services": {
            "pinvi-admin-bootstrap": {
                "image": "pinvi-api:test",
                "profiles": ["bootstrap"],
            }
        }
    }
    resolved = json.dumps(candidate)

    monkeypatch.setattr(
        compose_service_module,
        "_revalidate_compose_external_input_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_materialize_external_inputs_with_memfd",
        lambda candidate, _inputs: (candidate, ()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "revalidate_candidate_system_bind_snapshots",
        lambda _snapshots: None,
    )

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=resolved, stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    actual = service._resolve_compose_candidate_unlocked(
        candidate,
        environment={},
        expected_system_bind_snapshots=(),
        environment_snapshot=cast(
            Any, SimpleNamespace(compose_path="/tmp/compose.yml")
        ),
        environment_override=None,
        external_input_snapshot=cast(Any, object()),
    )

    assert actual == candidate
    assert commands == [
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--profile",
            "bootstrap",
            "--project-directory",
            "/tmp",
            "-f",
            "-",
            "config",
            "--format",
            "json",
        ]
    ]


def test_frozen_compose_resolution_preserves_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    candidate: dict[str, Any] = {"services": {}}

    monkeypatch.setattr(
        compose_service_module,
        "_revalidate_compose_external_input_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_materialize_external_inputs_with_memfd",
        lambda candidate, _inputs: (candidate, ()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "revalidate_candidate_system_bind_snapshots",
        lambda _snapshots: None,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout="resolved-config-stdout-marker",
            stderr=(
                'required variable KTDM_PROBE is missing a value: '
                "KTDM_PROBE must be explicitly set"
            ),
        ),
    )

    with pytest.raises(ComposeCandidateContractError) as captured:
        service._resolve_compose_candidate_unlocked(
            candidate,
            environment={},
            expected_system_bind_snapshots=(),
            environment_snapshot=cast(
                Any, SimpleNamespace(compose_path="/tmp/compose.yml")
            ),
            environment_override=None,
            external_input_snapshot=cast(Any, object()),
        )

    # ADR-51 잃는 보장 G: `${X:?}` 문구가 곧 원인이다. stdout은 보간된 설정 문서라 싣지 않는다.
    message = str(captured.value)
    assert message.startswith("compose candidate resolution failed")
    assert "KTDM_PROBE must be explicitly set" in message
    assert "resolved-config-stdout-marker" not in message


def test_compose_resolution_override_replaces_a_blank_ambient_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """빈 ambient 값도 frozen override로 실제 해석한다.

    ADR-51 D-3 전에는 prebuild snapshot이 이 경로로 permit 디렉터리를 넘겼다. 그
    호출자는 사라졌지만 candidate·runtime snapshot이 같은 override 경로로 이미지 env를
    넘기므로, 운영 키에 기대지 않는 중립 이름으로 경로 자체를 계속 결박한다.
    """

    names = (
        "KTDM_TEST_OVERRIDE_SOURCE_A",
        "KTDM_TEST_OVERRIDE_SOURCE_B",
    )
    artifact_root = tmp_path / "override-sources"
    overrides = {
        name: str(artifact_root / directory)
        for name, directory in zip(names, ("source-a", "source-b"), strict=True)
    }
    for directory in overrides.values():
        Path(directory).mkdir(parents=True)
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "".join(f"{name}=\n" for name in names),
        encoding="utf-8",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(compose_service_module, "get_env_path", lambda: str(env_path))
    monkeypatch.setattr(
        compose_service_module, "get_compose_path", lambda: str(compose_path)
    )
    monkeypatch.setattr(
        compose_service_module,
        "get_override_path",
        lambda: str(tmp_path / "missing.override.yml"),
    )
    snapshot = compose_service_module._capture_compose_environment_snapshot(
        environment_override=None
    )
    assert {name: snapshot.effective[name] for name in names} == {
        name: "" for name in names
    }
    environment = compose_service_module._effective_snapshot_environment(
        snapshot,
        overrides,
    )
    candidate = {
        "services": {
            "override-probe": {
                "image": "busybox:1.36",
                "volumes": [
                    f"${{{name}:?{name} must be explicitly set}}:/artifact-{index}:ro"
                    for index, name in enumerate(names)
                ],
            }
        }
    }

    resolved = ComposeService()._resolve_compose_candidate_unlocked(
        candidate,
        environment=environment,
        expected_system_bind_snapshots=(),
        environment_snapshot=snapshot,
        environment_override=overrides,
        external_input_snapshot=compose_service_module.ComposeExternalInputSnapshot(
            references=(),
            files=(),
        ),
    )

    volumes = resolved["services"]["override-probe"]["volumes"]
    assert [volume["source"] for volume in volumes] == list(overrides.values())
    assert [volume["target"] for volume in volumes] == [
        f"/artifact-{index}" for index in range(len(names))
    ]
    assert all(volume["read_only"] is True for volume in volumes)


def test_rebuild_compose_error_carries_the_command_and_its_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """one-shot `run` 실패는 명령·종료값·stderr·stdout 끝부분을 그대로 싣는다(ADR-51 G)."""

    service = ComposeService()
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 23,
                "stdout": "alembic.util.exc.CommandError: Can't locate revision 0412",
                "stderr": 'kor-travel-map-application-schema-1 | {"code":"x"}',
            }
        ),
    )

    with pytest.raises(DeploymentContractError) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--no-deps", "kor-travel-map-application-schema"],
            transaction=_opaque_transaction(),
        )

    message = str(captured.value)
    assert message.startswith(
        "pinned runtime rebuild Compose run --no-deps "
        "kor-travel-map-application-schema failed (exit 23)"
    )
    assert 'application-schema-1 | {"code":"x"}' in message
    assert "Can't locate revision 0412" in message


def test_rebuild_compose_error_leaves_out_data_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run`이 아닌 명령의 stdout은 원인이 아니라 데이터다 — 싣지 않는다."""

    service = ComposeService()
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": '[{"Name":"data-stdout-marker"}]',
                "stderr": "service pinvi-web failed to build: exit code 1",
            }
        ),
    )

    with pytest.raises(DeploymentContractError) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["build", "pinvi-web"],
            transaction=_opaque_transaction(),
        )

    message = str(captured.value)
    assert "Compose build pinvi-web failed (exit 1)" in message
    assert "service pinvi-web failed to build" in message
    assert "data-stdout-marker" not in message


def test_rebuild_candidate_builds_only_manager_services_sequentially(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    run = Mock(
        return_value={
            "success": True,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        }
    )
    monkeypatch.setattr(service, "_run_frozen_recovery", run)

    service._run_pinned_runtime_rebuild_compose(
        ["build", *COMPOSE_BUILT_RUNTIME_SERVICES],
        transaction=_opaque_transaction(),
    )

    assert [call.args[0] for call in run.call_args_list] == [
        ["build", runtime_service]
        for runtime_service in COMPOSE_BUILT_RUNTIME_SERVICES
    ]


@pytest.mark.parametrize(
    "arguments",
    (
        ["up", "-d", "pinvi-api"],
        ["run", "--rm", "pinvi-admin-bootstrap"],
    ),
)
def test_rebuild_startup_rejects_implicit_compose_dependencies(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    runner = Mock()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)

    with pytest.raises(DeploymentContractError, match="requires --no-deps"):
        service._run_pinned_runtime_rebuild_compose(
            arguments,
            transaction=_opaque_transaction(),
        )

    runner.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    (
        # `run SERVICE` 뒤는 컨테이너 argv다 — compose는 의존성을 끌어온다.
        [
            "--profile",
            "bootstrap",
            "run",
            "--rm",
            "pinvi-admin-bootstrap",
            "pinvi-admin-bootstrap",
            "--no-deps",
        ],
        # 값을 받는 옵션의 값 자리다(`-e --no-deps`는 환경 변수 이름이다).
        ["run", "--rm", "-e", "--no-deps", "pinvi-admin-bootstrap"],
    ),
    ids=("after-the-service", "option-value"),
)
def test_rebuild_startup_counts_only_the_no_deps_compose_parses_as_a_flag(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """argv 어딘가의 `--no-deps` 글자가 아니라 compose가 플래그로 읽은 것만 센다(적대 리뷰 2026-09-29)."""

    service = ComposeService()
    runner = Mock()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)

    assert "--no-deps" in arguments
    with pytest.raises(DeploymentContractError, match="requires --no-deps"):
        service._run_pinned_runtime_rebuild_compose(
            arguments,
            transaction=_opaque_transaction(),
        )

    runner.assert_not_called()


@pytest.mark.parametrize(
    ("arguments", "services", "flags"),
    (
        (
            ["run", "--rm", "pinvi-api", "sh", "--no-deps", "--remove-orphans"],
            ["pinvi-api"],
            {"--rm"},
        ),
        (["run", "--rm", "-e", "--no-deps", "pinvi-api"], ["pinvi-api"], {"--rm"}),
        # `run` 밖에서는 서비스 뒤의 플래그도 compose 플래그다.
        (["up", "-d", "pinvi-api", "--no-deps"], ["pinvi-api"], {"-d", "--no-deps"}),
        (["ps", "--no-deps"], [], set()),
        (["down", "--no-deps"], None, set()),
    ),
)
def test_compose_mutation_parse_reports_the_flags_compose_reads(
    arguments: list[str],
    services: list[str] | None,
    flags: set[str],
) -> None:
    assert ComposeService._parse_compose_mutation(arguments) == (services, frozenset(flags))


def test_rebuild_never_retries_a_failed_one_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    run = Mock(
        return_value={"success": False, "returncode": 1, "stdout": "", "stderr": ""}
    )
    monkeypatch.setattr(service, "_run_frozen_recovery", run)

    with pytest.raises(DeploymentContractError, match=r"Compose run .* failed \(exit 1\)"):
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "kor-travel-map-application-schema"],
            transaction=_opaque_transaction(),
        )

    run.assert_called_once()


def test_rebuild_compose_runner_has_no_retryable_argument() -> None:
    parameters = inspect.signature(
        ComposeService._run_pinned_runtime_rebuild_compose
    ).parameters

    assert "retryable" not in parameters
    # typed 진단 추출은 ADR-51 G-2에서 원문 tail로 바뀌었다 — 끄고 켤 스위치가 없다.
    assert "allow_typed_error_diagnostic" not in parameters


def test_static_command_failure_carries_both_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(
        return_value=SimpleNamespace(
            returncode=1,
            stdout="partial-static-output",
            stderr="exec /usr/local/bin/ktm-application-schema: no such file or directory",
        )
    )
    monkeypatch.setattr(compose_service_module.subprocess, "run", runner)

    with pytest.raises(DeploymentContractError) as captured:
        compose_service_module._run_pinned_runtime_static_command(
            f"sha256:{'a' * 64}",
            ("head",),
            label="Map application",
            entrypoint="/usr/local/bin/ktm-application-schema",
        )

    message = str(captured.value)
    assert message.startswith("Map application candidate static inspection failed (exit 1)")
    assert "no such file or directory" in message
    assert "partial-static-output" in message


def test_application_image_build_failure_carries_the_buildx_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """buildx 출력을 버리던 자리다 — 실패한 단계가 메시지에 보여야 한다."""

    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["docker"],
            1,
            stdout=b"",
            stderr=b"#12 ERROR: failed to solve: process \"/bin/sh -c uv sync\" exit code 2",
        )
    )
    monkeypatch.setattr(compose_service_module.subprocess, "run", runner)
    sources = cast(
        Any,
        SimpleNamespace(
            source_for=lambda _role: SimpleNamespace(root=tmp_path, revision="a" * 40)
        ),
    )

    with pytest.raises(DeploymentContractError) as captured:
        compose_service_module._build_map_application_300_images(
            sources=sources,
            api_image="ktm-api:probe",
            dagster_image="ktm-dagster:probe",
        )

    message = str(captured.value)
    assert "application 300 image build failed (docker/api.Dockerfile, exit 1)" in message
    assert "failed to solve" in message
    assert runner.call_args.kwargs["capture_output"] is True


def test_map_application_300_python_base_images_pull_failure_carries_the_registry_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = "python@sha256:" + "b" * 64
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if command[1] == "pull":
            return subprocess.CompletedProcess(
                command, 1, stdout=b"", stderr=b"toomanyrequests: You have reached your pull rate limit"
            )
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"")

    monkeypatch.setattr(compose_service_module.subprocess, "run", run)
    monkeypatch.setattr(
        compose_service_module,
        "_map_application_300_python_base_references",
        lambda _sources: (reference,),
    )

    with pytest.raises(DeploymentContractError) as captured:
        compose_service_module._ensure_map_application_300_python_base_images(
            cast(Any, object())
        )

    message = str(captured.value)
    assert reference in message
    assert "pull rate limit" in message
    assert [command[1] for command in calls] == ["image", "pull"]


def test_static_command_can_bypass_a_sealed_image_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(
        return_value=SimpleNamespace(returncode=0, stdout="static-output", stderr="")
    )
    monkeypatch.setattr(compose_service_module.subprocess, "run", runner)

    output = compose_service_module._run_pinned_runtime_static_command(
        f"sha256:{'a' * 64}",
        ("head",),
        label="Map application",
        entrypoint="/usr/local/bin/ktm-application-schema",
    )

    assert output == "static-output"
    command = runner.call_args.args[0]
    assert command == [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "/usr/local/bin/ktm-application-schema",
        f"sha256:{'a' * 64}",
        "head",
    ]


def test_oneshot_writer_liveness_must_be_empty_before_database_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    operations: list[tuple[str, ...]] = []
    transaction = cast(Any, SimpleNamespace())

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operations.append(tuple(args))
        if "ps" not in args:
            return {"success": True, "stdout": ""}
        return {
            "success": True,
            "stdout": (
                '[{"Name":"f1d-pinvi-bootstrap",'
                '"Service":"pinvi-admin-bootstrap","State":"running"}]'
            ),
        }

    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    with pytest.raises(DeploymentContractError, match="one-shot writer remained"):
        service._retire_pinned_runtime_oneshot_writers(transaction=transaction)

    assert [command[2] for command in operations] == ["rm", "ps"]
    expected_writers = (
        "kor-travel-map-db-role-bootstrap",
        "kor-travel-map-application-schema",
        "pinvi-admin-bootstrap",
    )
    assert operations[0] == (
        "--profile",
        "bootstrap",
        "rm",
        "-f",
        "-s",
        *expected_writers,
    )
    assert operations[1] == (
        "--profile",
        "bootstrap",
        "ps",
        "--all",
        "--format",
        "json",
        *expected_writers,
    )


def test_the_manager_mutation_lock_rejects_nonroot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재구축이 잡는 유일한 lock G도 root만 연다(ADR-51 C-3 — 별도 rebuild lease는 없다).

    conftest가 소유자 seam을 실행 euid로 바꿔 두므로 운영 값(root)으로 되돌린다.
    """

    monkeypatch.setattr(c6c_deployment, "_GLOBAL_LOCK_OWNER_UID", 0)
    monkeypatch.setattr(c6c_deployment.os, "geteuid", lambda: 1000)

    with pytest.raises(c6c_deployment.DeploymentContractError, match="requires root"):
        with c6c_deployment.manager_mutation_lock():
            pass  # pragma: no cover - root gate must reject before entering.
    assert not c6c_deployment._C6C_GLOBAL_MUTATION_LOCK.exists()


def test_rebuild_timeouts_outlast_a_saturated_disk() -> None:
    """타임아웃은 멈춤 감지용이다 — 느린 디스크에서 정상 명령을 죽이면 안 된다.

    n150 실측(2026-09-26, IO 압력 full 50~60%): `docker run /bin/true` 112초,
    static head 74초. Map은 code-server → webserver → daemon을 직렬로 띄운다.
    """

    per_container = 112 + 74
    module = compose_service_module
    assert module._PINNED_RUNTIME_STATIC_INSPECTION_TIMEOUT_SECONDS >= 2 * per_container
    assert module._COMPOSE_WAIT_TIMEOUT_SECONDS >= 3 * per_container


# ── 마이그레이션 전진 배포(ADR-51 B2) ──────────────────────────────────────────

_FORWARD_COMPANIONS: dict[str, RuntimeSlot] = {
    "kor-travel-map-dagster-code-server": "map_dagster",
    "pinvi-dagster-code-server": "pinvi_dagster",
    "pinvi-dagster-daemon": "pinvi_dagster",
}
_FORWARD_ONESHOTS = (
    "kor-travel-map-application-schema",
    "pinvi-admin-bootstrap",
    "kor-travel-map-db-role-bootstrap",
)
#: 옛 Map Dagster metadata DB의 migrate one-shot. 재구축은 이것을 부르지 않는다(platform-topology.md §7 4단계).
_RETIRED_STORAGE_MIGRATE = "kor-travel-map-dagster-storage-migrate"
_SCHEMA_RUN = (
    "--profile",
    "bootstrap",
    "run",
    "--rm",
    "--no-deps",
    "kor-travel-map-application-schema",
)
_LIVE_IDENTITIES: dict[str, tuple[str, int, str]] = {
    "map_application": ("kor_travel_map", 16401, "7300000000000000001"),
    "pinvi": ("pinvi", 20001, "7300000000000000002"),
}


_ISOLATION = "ensure-map-databases-isolated"


#: ADR-53 모양 — 세 DB가 공용 instance 하나에 산다.
_FORWARD_INSTANCE = "kor-travel-shared-postgres"
_FORWARD_PORT = 11000
_FORWARD_ADMIN = "cluster_admin"


def _forward_runtimes() -> tuple[DatabaseRuntime, DatabaseRuntime]:
    def runtime(role: Any, name: str) -> DatabaseRuntime:
        return DatabaseRuntime(
            role=role,
            service_name=_FORWARD_INSTANCE,
            container_name="shared-postgres",
            port=_FORWARD_PORT,
            database_name=name,
            # S1: Map 소유자는 instance admin이다.
            owner_name="pinvi_app" if role == "pinvi" else _FORWARD_ADMIN,
            admin_name=_FORWARD_ADMIN,
        )

    return (
        runtime("map_application", "kor_travel_map"),
        runtime("pinvi", "pinvi"),
    )


def _deployed_databases() -> dict[Any, DeployedDatabase]:
    return {role: DeployedDatabase(*identity) for role, identity in _LIVE_IDENTITIES.items()}


def _committed_status(
    candidate: PinnedRuntimeGeneration,
    **overrides: Any,
) -> DeployStatus:
    fields: dict[str, Any] = {
        "state": "committed",
        "run_id": str(uuid.UUID(int=7)),
        "started_at": "2026-09-26T00:00:00+00:00",
        "committed_at": "2026-09-26T01:00:00+00:00",
        "manager_revision": "e" * 40,
        "map_revision": candidate.map_source_revision,
        "pinvi_revision": candidate.pinvi_source_revision,
        "pinset_sha256": candidate.pinset_sha256,
        "images": ComposeService._deployed_images(candidate, _FORWARD_COMPANIONS),
        "schema_heads": {
            str(role): head for role, head in candidate.schema_heads.items()
        },
        "databases": _deployed_databases(),
    }
    fields.update(overrides)
    return DeployStatus(**fields)


def _forward_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    previous: DeployStatus | None = None,
    images_present: bool = True,
    flipped: tuple[str, ...] = (),
    companions: Mapping[str, RuntimeSlot] = _FORWARD_COMPANIONS,
) -> SimpleNamespace:
    """재구축을 실제 오케스트레이션 그대로 돌리는 대역. DB·docker·compose는 대역이다.

    compose 호출, readiness 요청, 이미지 조회 label, C6c 검사 대상을 기록하고, 라이브 DB
    identity·head는 `live`로 바꿀 수 있다.

    ``flipped``는 공용 Dagster plane(ADR-54)에 합류시킬 target이다. 설치된 compose·targets를 참조
    전환(`_flip`)한 모델로 slot 서비스를 파생하게 하고, frozen render는 그 모델 그대로(옛
    webserver·daemon은 `legacy-dagster`라 빠진다) — ``companions``가 그 render의 companion이다.
    """

    from test_dagster_shared_workspace_is_derived import (
        _derived_workspace,
        _flip,
        _own_pair_documents,
    )

    compose_document, targets_document = _own_pair_documents()
    for target_id in flipped:
        _flip(compose_document, targets_document, target_id)
    if flipped:
        families = derive_dagster_families(compose_document, targets_document)
        monkeypatch.setattr(
            runtime_topology_module, "installed_dagster_family", lambda target: families[target]
        )
    # 공용 plane(ADR-54 개정): frozen render에 plane 서비스가 있고(보간은 기본값으로 푼다 — `compose config`처럼),
    # 붙인 workspace는 이 모델의 파생 workspace다.
    plane_model = runtime_topology_module.derive_shared_dagster_plane(compose_document)
    plane_resolved = {
        name: yaml.safe_load(
            re.sub(
                r"\$\{[A-Z0-9_]+:-([^${}]*)\}",
                r"\1",
                yaml.safe_dump(compose_document["services"][name]),
            )
        )
        for name in plane_model.services
    }
    workspace_locations = tuple(
        entry["grpc_server"]["location_name"]
        for entry in _derived_workspace(compose_document, targets_document)["load_from"]
    )

    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        "KOR_TRAVEL_MAP_PG_DSN": (
            f"postgresql+asyncpg://ktm_feature_service:service-password@127.0.0.1:{_FORWARD_PORT}/"
            "kor_travel_map"
        ),
        "COMPOSE_PROJECT_NAME": "f1d-migrate-forward",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state"),
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "rebuild-admin-password",
    }
    candidate = _candidate_generation()
    map_candidate = _map_application_candidate()
    image_ids = candidate.image_ids
    topology = runtime_topology()
    resolved_services: dict[str, object] = {
        service: {"image": image_ids[slot]}
        for slot in RUNTIME_SLOTS
        if (service := topology.service(slot)) is not None
    }
    resolved_services.update(
        {name: {"image": image_ids[owner]} for name, owner in companions.items()}
    )
    # 같은 slot 이미지를 쓰는 one-shot writer는 companion이 아니다.
    resolved_services.update(
        {
            "kor-travel-map-application-schema": {"image": image_ids["map_api"]},
            "pinvi-admin-bootstrap": {"image": image_ids["pinvi_api"]},
        }
    )
    resolved_services.update(plane_resolved)
    deployed_images = ComposeService._deployed_images(candidate, companions, topology)
    transaction = SimpleNamespace(
        environment=SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n"),
        compose_source_bytes=b"services: {}\n",
        resolved_document_hash="c" * 64,
        resolved={"services": resolved_services},
    )
    state_paths = pinned_runtime_state_paths(
        values,
        pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
    )
    state_paths.state_root.mkdir(parents=True, mode=0o700)
    status_path = deploy_status_path(state_paths.state_root)
    if previous is not None:
        write_deploy_status(status_path, previous)
    runtimes = _forward_runtimes()
    live: dict[str, Any] = {
        "identities": dict(_LIVE_IDENTITIES),
        "heads": {
            "map_application": candidate.map_application_head,
            "pinvi": candidate.pinvi_head,
        },
        "pinvi_schema_table": True,
        # identity·head를 읽은 DB role(옛 `map_dagster`가 여기 나타나면 안 된다).
        "read_roles": set(),
        # readiness가 거부할 서비스(ADR-53: instance는 readiness로만 본다).
        "not_ready": set(),
        # docker에서 돌고 있는 컨테이너 이름(전환된 target의 옛 컨테이너 검사, ADR-54).
        "running_containers": set(),
        # 공용 plane: 붙인 workspace의 location, 떠 있는 webserver가 싣는 것(None이면 물을 수 없음), plane 컨테이너가
        # 도는가, 떠 있는 plane 컨테이너의 digest env.
        "workspace_locations": workspace_locations,
        "plane_loaded": {location: "RepositoryLocation" for location in workspace_locations},
        "plane_running": True,
        "plane_restarting": False,
        # 떠 있는 plane 컨테이너가 frozen render와 같은가(True면 `up`하지 않는다), 그리고 서비스별 덮어쓰기.
        "plane_current": False,
        "plane_overrides": {},
    }
    operations: list[tuple[str, ...]] = []
    readiness_requests: list[tuple[str, ...]] = []
    image_labels: list[str] = []
    inspected_services: list[tuple[str, ...]] = []
    mocks = SimpleNamespace(
        reset=Mock(),
        ensure_map=Mock(return_value="present"),
        fence=Mock(),
        pinvi_bootstrap=Mock(),
        smoke=Mock(),
        paired_builder=Mock(),
        materialize=Mock(side_effect=lambda **_kwargs: _sources()),
        contract=Mock(),
        prerequisites=Mock(),
        create_pinvi=Mock(return_value=False),
        map_precheck=Mock(return_value="present"),
        # 멈추기 전의 읽기 전용 판정(`--restart`의 R2, 일반·adopt 경로의 R4 전제).
        reset_preflight=Mock(),
        isolation_preflight=Mock(),
        # S1: bootstrap이 돌 때 instance admin을 멈추기 전에 판정한다.
        admin_preflight=Mock(),
        retention_generation=Mock(),
        retention_candidate=Mock(),
    )

    def run_compose(arguments: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operations.append(tuple(arguments))
        return {"success": True, "stdout": ""}

    def require_ready(
        services: Sequence[str],
        *,
        transaction: object,
        frozen_recovery: bool = False,
    ) -> list[Mapping[str, Any]]:
        del transaction, frozen_recovery
        readiness_requests.append(tuple(services))
        if tuple(services) == compose_service_module._PINNED_RUNTIME_EXTERNAL_PREREQUISITES:
            mocks.prerequisites()
        not_ready = sorted(set(services) & cast(set[str], live["not_ready"]))
        if not_ready:
            raise DeploymentContractError(
                "mandatory services do not satisfy canonical readiness: " + ", ".join(not_ready)
            )
        return [
            {"Name": f"{name}-latest", "Service": name, "State": "running"}
            for name in services
        ]

    container_checks: list[str] = []
    plane_containers = {
        runtime_topology_module.installed_container_name(name) for name in plane_model.services
    }

    plane_image_id = "sha256:" + "e" * 64
    plane_container_service = {
        runtime_topology_module.installed_container_name(name): name for name in plane_model.services
    }

    def plane_container(container_name: str, *, label: str) -> object:
        del label
        operations.append(("plane-inspect", container_name))
        if not live["plane_running"]:
            return None
        render = plane_resolved[plane_container_service[container_name]]

        def docker(value: object) -> str:
            # Docker가 컨테이너를 만들 때처럼 compose의 `$$` escape를 `$`로 푼다(render는 `$$`를 싣는다).
            return str(value).replace("$$", "$")

        observed = {
            "image_id": plane_image_id,
            "env": (
                {
                    str(k): docker(v)
                    for k, v in (render.get("environment") or {}).items()
                    if v is not None
                }
                if live["plane_current"]
                else {}
            ),
            "cmd": [docker(word) for word in render.get("command") or []],
            "entrypoint": (
                None if render.get("entrypoint") is None else [docker(w) for w in render["entrypoint"]]
            ),
            "running": True,
            "restarting": bool(live["plane_restarting"]),
        }
        observed.update(live["plane_overrides"].get(plane_container_service[container_name], {}))
        return observed

    def plane_query(host: str, port: int) -> object:
        del host, port
        loaded = live["plane_loaded"]
        return None if loaded is None else dict(loaded)

    def container_running(container_name: str, *, label: str) -> bool | None:
        del label
        container_checks.append(container_name)
        # 검사도 같은 기록에 남겨 "무엇을 멈추기 전에"를 순서로 단언할 수 있게 한다.
        operations.append(("container-inspect", container_name))
        if container_name in plane_containers:
            return True if live["plane_running"] else None
        return True if container_name in cast(set[str], live["running_containers"]) else None

    def inspect_image(container_name: str, *, label: str) -> str:
        del container_name
        image_labels.append(label)
        return deployed_images[label]

    class _C6cConfig:
        map_ui_container = "kor-travel-map-ui-latest"

    def inspect_c6c(
        config: object,
        services: list[str],
        *,
        transaction: object,
        frozen_recovery: bool = False,
    ) -> dict[str, Mapping[str, Any]]:
        del config, transaction, frozen_recovery
        inspected_services.append(tuple(services))
        return {_C6cConfig.map_ui_container: {}}

    def isolate(app: DatabaseRuntime, *, login: str) -> None:
        # DB 권한 변경도 순서를 단언할 수 있게 compose 호출과 같은 기록에 남긴다.
        operations.append((_ISOLATION, app.database_name, login))

    def read_identity(runtime: DatabaseRuntime) -> tuple[str, int, str] | None:
        live["read_roles"].add(runtime.role)
        identities = cast(dict[str, Any], live["identities"])
        return cast("tuple[str, int, str] | None", identities.get(runtime.role))

    def read_head(runtime: DatabaseRuntime) -> str:
        live["read_roles"].add(runtime.role)
        head = cast(dict[str, Any], live["heads"]).get(runtime.role)
        if head is None:
            raise DeploymentContractError(f"{runtime.role} schema revision output is invalid")
        return cast(str, head)

    from kor_travel_docker_manager.services import runtime_execution_registry

    monkeypatch.setattr(
        runtime_execution_registry, "trusted_manager_source_revision", lambda: "e" * 40
    )
    for name, replacement in {
        "_require_pinned_runtime_rebuild_root": lambda: None,
        "_capture_compose_environment_snapshot": (
            lambda *, environment_override: transaction.environment
        ),
        # 진짜 materialize는 network와 git이 필요하다 — 이 harness는 source를 대역으로 준다.
        "materialize_pinned_runtime_sources": mocks.materialize,
        "prune_pinned_runtime_sources": Mock(),
        "_ensure_map_application_300_python_base_images": Mock(),
        "_build_map_application_300_images": mocks.paired_builder,
        "_load_application_300_candidate": Mock(return_value=map_candidate),
        "_local_image_present": lambda _image: images_present,
        "_run_pinned_runtime_static_command": Mock(return_value="{}"),
        "parse_candidate_static_head": Mock(return_value="head"),
        "build_candidate_generation": lambda **_kwargs: candidate,
        "ensure_generation_references": Mock(),
        "database_runtimes_from_frozen_contract": lambda **_kwargs: runtimes,
        "read_database_identity": read_identity,
        "read_database_schema_revision": read_head,
        "schema_revision_table_exists": lambda _runtime: live["pinvi_schema_table"],
        "ensure_map_application_database": mocks.ensure_map,
        "reset_databases_for_application_300": mocks.reset,
        "ensure_map_databases_isolated": isolate,
        "reconcile_orphaned_pinvi_bootstrap_credentials": Mock(),
        "run_pinvi_canonical_smoke": mocks.smoke,
        "C6cDeploymentConfig": _C6cConfig,
        "load_c6c_deployment_config_from_environment": Mock(return_value=_C6cConfig()),
        "validate_runtime_secret_isolation": Mock(),
        "validate_current_map_ui_auth_runtime": Mock(),
        "reconcile_generation_references": mocks.retention_generation,
        "reconcile_candidate_build_references": mocks.retention_candidate,
        "create_database_if_absent": mocks.create_pinvi,
        "require_map_application_database_convergible": mocks.map_precheck,
        "require_databases_resettable": mocks.reset_preflight,
        "require_map_databases_isolatable": mocks.isolation_preflight,
        "require_map_bootstrap_admin_ready": mocks.admin_preflight,
    }.items():
        monkeypatch.setattr(compose_service_module, name, replacement)
    service = ComposeService()
    for name, replacement in {
        "capture_transaction_unlocked": lambda **_kwargs: (transaction, None),
        "_validate_pinned_runtime_candidate_build_contract": mocks.contract,
        "_attest_pinned_runtime_candidate_images": Mock(return_value=dict(image_ids)),
        "_verify_pinned_runtime_pinvi_bootstrap_settings": Mock(),
        "_retire_pinned_runtime_oneshot_writers": Mock(),
        "_run_pinned_runtime_rebuild_compose": run_compose,
        "_require_services_ready": require_ready,
        "_inspect_container_runtime_config": Mock(return_value={}),
        "_inspect_container_image_id": inspect_image,
        "_inspect_container_running": container_running,
        "_inspect_c6c_runtime_configs": inspect_c6c,
        "_ensure_pinvi_fresh_migration_fence": mocks.fence,
        "_run_pinvi_admin_bootstrap": mocks.pinvi_bootstrap,
        "_read_shared_workspace_locations": lambda _source: tuple(live["workspace_locations"]),
        "_query_shared_plane_locations": plane_query,
        "_inspect_plane_container": plane_container,
        "_inspect_image_reference_id": lambda _reference, *, label: plane_image_id,
    }.items():
        monkeypatch.setattr(service, name, replacement)
    return SimpleNamespace(
        service=service,
        transaction=transaction,
        candidate=candidate,
        runtimes=runtimes,
        status_path=status_path,
        live=live,
        operations=operations,
        readiness_requests=readiness_requests,
        image_labels=image_labels,
        container_checks=container_checks,
        inspected_services=inspected_services,
        mocks=mocks,
        expected_images=deployed_images,
        topology=topology,
        plane=plane_model,
        plane_resolved=plane_resolved,
    )


def _assert_the_retired_metadata_database_was_never_touched(harness: SimpleNamespace) -> None:
    """옛 Map Dagster metadata DB(4단계에서 막힌 뒤 DROP)는 migrate·identity·head 어느 것으로도 닿지 않는다."""

    assert not any(_RETIRED_STORAGE_MIGRATE in operation for operation in harness.operations)
    assert harness.live["read_roles"] <= {"map_application", "pinvi"}


def _mutating_operations(harness: SimpleNamespace) -> list[tuple[str, ...]]:
    """무언가를 바꾸는 호출(compose·DB 권한). readiness 읽기는 따로 기록된다.

    ADR-53부터 재구축은 PostgreSQL 서버를 띄우지 않는다 — 기록된 호출이 전부 변경이다.
    """

    return list(harness.operations)


def test_first_deploy_runs_the_idempotent_full_path_and_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)

    result = harness.service.rebuild_pinned_runtime()

    # launcher·chain17이 읽는 결과 키는 그대로다.
    assert result["success"] is True
    assert result["phase"] == "committed"
    assert result["outcome"] == "deployed"
    assert result["pinset_sha256"] == harness.candidate.pinset_sha256
    assert result["schema_heads"] == {
        str(role): head for role, head in harness.candidate.schema_heads.items()
    }
    harness.mocks.reset.assert_not_called()
    harness.mocks.ensure_map.assert_called_once()
    assert harness.operations.count(_SCHEMA_RUN) == 1
    _assert_the_retired_metadata_database_was_never_touched(harness)
    harness.mocks.pinvi_bootstrap.assert_called_once()
    status = read_deploy_status(harness.status_path)
    assert status is not None
    assert status.state == "committed"
    assert dict(status.images) == harness.expected_images
    assert dict(status.databases or {}) == _deployed_databases()
    # 커밋이 남기는 기록은 deploy-status.json 하나다 — v6 manifest는 ADR-51 D-2부터 쓰지
    # 않는다. 호출 대역이 아니라 state root에 파일이 생기지 않았는지로 확인한다.
    assert not (harness.status_path.parent / "pinned-runtime-generation-v6.json").exists()


def test_leftover_v6_v8_state_is_not_adopted_and_is_left_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``deploy-status.json``이 없으면 기준선 없는 전체 경로 한 번이다(ADR-51 B3).

    **지금 배포할 바로 그 세대**를 담은 유효한 v6 manifest와 옛 v8 journal 파일이 state
    root에 남아 있어도 넘겨받지 않는다(n150에는 그런 파일이 남아 있다). manifest를
    넘겨받았다면 같은 pair라 결과는 ``converged``였을 것이다 — manifest가 유효하므로
    이 검사는 "읽지 못해서 안 넘겨받음"과 "넘겨받지 않음"을 가른다. v8 journal 모델은
    B3에서, v6 manifest 모델은 D-2에서 지워져 그 자리에는 원시 바이트만 심는다. 커밋은
    v6를 더 쓰지 않으므로(ADR-51 D-2) 두 파일은 바이트 그대로 남아야 한다.
    """

    harness = _forward_harness(monkeypatch, tmp_path)
    state_root = harness.status_path.parent
    manifest_path = state_root / "pinned-runtime-generation-v6.json"
    journal_path = (
        state_root / f"pinned-runtime-rebuild-v8-{harness.candidate.pinset_sha256}.json"
    )
    # D-2 이전 Manager가 이 세대를 커밋하며 남겼을 v6 문서의 바이트 그대로다.
    manifest_path.write_bytes(
        (
            json.dumps(
                {"version": 6, "active_generation": harness.candidate.to_payload()},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    )
    os.chmod(manifest_path, 0o600)
    journal_path.write_bytes(b'{"version": 8}')
    os.chmod(journal_path, 0o600)
    planted = {path: path.read_bytes() for path in (manifest_path, journal_path)}

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "deployed"
    harness.mocks.reset.assert_not_called()
    status = read_deploy_status(harness.status_path)
    assert status is not None and status.state == "committed"
    assert status.carried_over_from is None
    assert dict(status.databases or {}) == _deployed_databases()
    for path, content in planted.items():
        assert path.read_bytes() == content


def test_the_same_committed_pair_only_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 pair는 빌드·migration·정지 없이 떠 있어야 할 것만 맞춘다."""

    candidate = _candidate_generation()
    previous = _committed_status(candidate)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "converged"
    assert result["transaction_id"] == previous.run_id
    assert not any(operation[0] == "stop" for operation in harness.operations)
    assert not any(
        writer in operation for operation in harness.operations for writer in _FORWARD_ONESHOTS
    )
    runtime_up = [
        operation
        for operation in _mutating_operations(harness)
        if operation[0] == "up"
    ]
    assert len(runtime_up) == 1
    assert set(runtime_up[0][6:]) == {*RUNTIME_SERVICES, *_FORWARD_COMPANIONS}
    assert (*RUNTIME_SERVICES, *_FORWARD_COMPANIONS) in harness.readiness_requests
    assert set(_FORWARD_COMPANIONS) <= set(harness.image_labels)
    harness.mocks.paired_builder.assert_not_called()
    harness.mocks.reset.assert_not_called()
    assert read_deploy_status(harness.status_path) == previous


def test_a_new_pair_migrates_forward_on_the_same_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_generation()
    previous = _committed_status(candidate, map_revision="0" * 40)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "deployed"
    harness.mocks.reset.assert_not_called()
    _assert_the_retired_metadata_database_was_never_touched(harness)
    status = read_deploy_status(harness.status_path)
    assert status is not None and status.state == "committed"
    assert status.map_revision == candidate.map_source_revision
    # 같은 DB — oid가 그대로다.
    assert dict(status.databases or {}) == _deployed_databases()


def test_a_replaced_database_is_refused_before_anything_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """지난 배포가 본 DB가 아니면(누가 지우고 다시 만들었다) 무엇도 바꾸기 전에 멈춘다."""

    candidate = _candidate_generation()
    previous = _committed_status(candidate, map_revision="0" * 40)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    harness.live["identities"]["pinvi"] = ("pinvi", 29999, "7300000000000000002")

    with pytest.raises(DeploymentContractError, match="--restart") as captured:
        harness.service.rebuild_pinned_runtime()

    assert _mutating_operations(harness) == []
    assert read_deploy_status(harness.status_path) == previous
    # 단계 밖(런타임 transaction 뒤)의 거부다 — JSON에 stage가 없다.
    assert compose_service_module.rebuild_failure_stage(captured.value) is None


def test_restart_resets_once_and_rebaselines_the_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_generation()
    previous = _committed_status(candidate)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    new_identities = {
        "map_application": ("kor_travel_map", 17001, "7300000000000000001"),
        "pinvi": ("pinvi", 27001, "7300000000000000002"),
    }
    harness.live["identities"] = {"map_application": None, "pinvi": None}
    harness.live["identities"].update(_LIVE_IDENTITIES)

    def reset(runtimes: object) -> None:
        del runtimes
        harness.live["identities"].update(new_identities)

    harness.mocks.reset.side_effect = reset

    result = harness.service.rebuild_pinned_runtime(restart_reason="rebuild from empty")

    assert result["outcome"] == "deployed"
    harness.mocks.reset.assert_called_once()
    status = read_deploy_status(harness.status_path)
    assert status is not None and status.state == "committed"
    assert status.restart is not None and status.restart.reason == "rebuild from empty"
    assert {role: database.oid for role, database in (status.databases or {}).items()} == {
        "map_application": 17001,
        "pinvi": 27001,
    }
    _assert_the_retired_metadata_database_was_never_touched(harness)


def test_a_failure_after_in_progress_cleans_up_and_the_rerun_finishes_without_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_generation()
    previous = _committed_status(candidate, map_revision="0" * 40)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    harness.live["heads"]["map_application"] = "older-application-head"

    with pytest.raises(
        DeploymentContractError, match="Map application schema differs from candidate head"
    ) as captured:
        harness.service.rebuild_pinned_runtime()

    stop = ("stop", *RUNTIME_SERVICES, *sorted(_FORWARD_COMPANIONS))
    # 기동 전 정지 + 실패 정리 정지. 정리에서 companion이 빠지면 실패한 세대의
    # code-server가 살아남는다.
    assert harness.operations.count(stop) == 2
    status = read_deploy_status(harness.status_path)
    assert status is not None and status.state == "in_progress"
    assert dict(status.databases or {}) == _deployed_databases()
    # in_progress를 쓴 뒤의 실패는 단계 밖이다.
    assert compose_service_module.rebuild_failure_stage(captured.value) is None

    harness.live["heads"]["map_application"] = candidate.map_application_head
    harness.operations.clear()
    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "deployed"
    harness.mocks.reset.assert_not_called()
    committed = read_deploy_status(harness.status_path)
    assert committed is not None and committed.state == "committed"


def test_companions_ride_every_step_of_the_full_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """t56e~t56h는 호출 하나에서 companion이 빠진 것만으로 Map code-server가 한 번도
    뜨지 않았다. 호출처 하나를 지우면 이 테스트의 단언 하나가 깨져야 한다."""

    harness = _forward_harness(monkeypatch, tmp_path)

    harness.service.rebuild_pinned_runtime()

    companions = tuple(sorted(_FORWARD_COMPANIONS))
    wait = ("up", "-d", "--no-deps", "--wait", "--wait-timeout", _WAIT_TIMEOUT)
    assert ("stop", *RUNTIME_SERVICES, *companions) in harness.operations
    assert (
        *wait,
        "kor-travel-map-dagster-code-server",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
    ) in harness.operations
    assert (
        *wait,
        "pinvi-dagster-code-server",
        "pinvi-dagster-daemon",
        "pinvi-web",
        "pinvi-dagster",
    ) in harness.operations
    assert not any(
        writer in operation
        for operation in harness.operations
        if operation[0] in {"stop", "up"}
        for writer in _FORWARD_ONESHOTS
    )
    assert (*RUNTIME_SERVICES, *companions) in harness.readiness_requests
    assert set(companions) <= set(harness.image_labels)
    assert harness.inspected_services == [(*RUNTIME_SERVICES, *companions)]


@pytest.mark.parametrize("schema_table", (True, False))
def test_the_pinvi_fresh_install_fence_is_only_for_an_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema_table: bool
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.live["pinvi_schema_table"] = schema_table

    harness.service.rebuild_pinned_runtime()

    assert harness.mocks.fence.called is (not schema_table)
    harness.mocks.pinvi_bootstrap.assert_called_once()


def test_a_same_pair_rerun_after_stage_4_converges_without_the_old_metadata_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-10-03 22:51Z 사고의 재현: 4단계 전의 Manager가 쓴 deploy-status.json(옛 `map_dagster` 항목 포함)과
    막힌 옛 Map Dagster metadata DB 위에서 같은 pair를 다시 돌린다.

    그때는 옛 DB의 head를 읽지 못해 수렴하지 못하고 전체 경로로 가서 Map·PinVi를 멈춘 뒤
    `kor-travel-map-dagster-storage-migrate`에서 죽었다. 이제 그 항목은 읽을 때 버려지고 옛 DB는 닿지 않으므로
    아무것도 멈추지 않고 수렴한다.
    """

    candidate = _candidate_generation()
    payload = _committed_status(candidate).to_payload()
    databases = payload["databases"]
    heads = payload["schema_heads"]
    assert isinstance(databases, dict) and isinstance(heads, dict)
    databases["map_dagster"] = {
        "name": "kor_travel_map_dagster",
        "oid": 16402,
        "system_identifier": "7300000000000000001",
    }
    heads["map_dagster"] = "7e2f3204cf8e"
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.status_path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(harness.status_path, 0o600)

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "converged"
    assert not any(operation[0] == "stop" for operation in harness.operations)
    assert not any(
        writer in operation for operation in harness.operations for writer in _FORWARD_ONESHOTS
    )
    _assert_the_retired_metadata_database_was_never_touched(harness)


def test_existing_candidate_images_are_not_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """태그는 pinset에 묶인다 — 다시 빌드하면 재현되지 않는 digest가 같은 pair를 새 이미지로 만든다."""

    harness = _forward_harness(monkeypatch, tmp_path, images_present=True)

    harness.service.rebuild_pinned_runtime()

    harness.mocks.paired_builder.assert_not_called()
    assert not any(operation[0] == "build" for operation in harness.operations)


def test_a_candidate_compose_build_failure_names_its_own_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path, images_present=False)

    def run_compose(arguments: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        harness.operations.append(tuple(arguments))
        if arguments[0] == "build":
            raise DeploymentContractError(
                "pinned runtime rebuild Compose build command failed (exit 1)"
            )
        return {"success": True, "stdout": ""}

    monkeypatch.setattr(harness.service, "_run_pinned_runtime_rebuild_compose", run_compose)

    with pytest.raises(DeploymentContractError) as captured:
        harness.service.rebuild_pinned_runtime()

    assert compose_service_module.rebuild_failure_stage(captured.value) == "candidate_compose_build"
    # 원래 예외가 그대로 올라온다 — 봉인 문구로 바뀌지 않는다.
    assert "Compose build command failed" in str(captured.value)
    assert read_deploy_status(harness.status_path) is None
    harness.mocks.reset.assert_not_called()


def test_a_candidate_contract_refusal_precedes_any_runtime_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.mocks.contract.side_effect = DeploymentContractError("candidate contract refused")

    with pytest.raises(DeploymentContractError, match="candidate contract refused") as captured:
        harness.service.rebuild_pinned_runtime()

    assert compose_service_module.rebuild_failure_stage(captured.value) == "candidate_contract"
    assert harness.operations == []
    assert read_deploy_status(harness.status_path) is None


def test_external_prerequisites_are_checked_before_sources_are_materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.mocks.prerequisites.side_effect = DeploymentContractError("geo is not ready")

    with pytest.raises(DeploymentContractError, match="geo is not ready") as captured:
        harness.service.rebuild_pinned_runtime()

    assert compose_service_module.rebuild_failure_stage(captured.value) == "external_prerequisites"
    harness.mocks.materialize.assert_not_called()
    assert harness.operations == []


def test_admission_warnings_ride_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    monkeypatch.setattr(
        compose_service_module,
        "_pinned_runtime_admission_warnings",
        lambda _pinset: ["the trusted execution binding is stale"],
    )

    result = harness.service.rebuild_pinned_runtime()

    assert result["warnings"] == ["the trusted execution binding is stale"]


@pytest.mark.parametrize(
    ("change", "expected_outcome"),
    (
        # 수렴 조건의 항 하나씩. 하나라도 어긋나면 수렴이 아니라 전체 경로다.
        ("heads", "deployed"),
        ("images", "deployed"),
        ("pinvi_revision", "deployed"),
        ("in_progress", "deployed"),
        ("none", "converged"),
    ),
)
def test_every_term_of_the_convergence_condition_matters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    expected_outcome: str,
) -> None:
    candidate = _candidate_generation()
    overrides: dict[str, Any] = {}
    if change == "images":
        images = ComposeService._deployed_images(candidate, _FORWARD_COMPANIONS)
        images["pinvi-web"] = f"sha256:{321:064x}"
        overrides["images"] = images
    elif change == "pinvi_revision":
        overrides["pinvi_revision"] = "1" * 40
    elif change == "in_progress":
        overrides.update(state="in_progress", committed_at=None)
    previous = _committed_status(candidate, **overrides)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    if change == "heads":
        harness.live["heads"]["pinvi"] = "older-pinvi-head"

        def migrate(**_kwargs: object) -> None:
            harness.live["heads"]["pinvi"] = candidate.pinvi_head

        harness.mocks.pinvi_bootstrap.side_effect = migrate

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == expected_outcome
    harness.mocks.reset.assert_not_called()


def test_a_replaced_database_of_the_same_pair_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 pair라도 DB가 바뀌었으면 수렴하지 않고 거부한다(수렴 조건의 identity 항)."""

    candidate = _candidate_generation()
    harness = _forward_harness(
        monkeypatch, tmp_path, previous=_committed_status(candidate)
    )
    harness.live["identities"]["map_application"] = (
        "kor_travel_map",
        19999,
        "7300000000000000001",
    )

    with pytest.raises(DeploymentContractError, match="--adopt-live-databases"):
        harness.service.rebuild_pinned_runtime()

    assert _mutating_operations(harness) == []


def test_restart_bypasses_the_baseline_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_generation()
    harness = _forward_harness(
        monkeypatch, tmp_path, previous=_committed_status(candidate)
    )
    harness.live["identities"]["pinvi"] = ("pinvi", 29999, "7300000000000000002")

    result = harness.service.rebuild_pinned_runtime(restart_reason="rebuild")

    assert result["outcome"] == "deployed"
    harness.mocks.reset.assert_called_once()


def test_adopting_live_databases_rebaselines_without_a_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """백업 복원처럼 비파괴로 DB가 바뀌었을 때 `--restart` 말고 빠져나갈 길이다."""

    candidate = _candidate_generation()
    harness = _forward_harness(
        monkeypatch, tmp_path, previous=_committed_status(candidate)
    )
    restored = ("pinvi", 29999, "7300000000000000002")
    harness.live["identities"]["pinvi"] = restored

    result = harness.service.rebuild_pinned_runtime(adopt_reason="restored from backup")

    assert result["outcome"] == "deployed"
    harness.mocks.reset.assert_not_called()
    status = read_deploy_status(harness.status_path)
    assert status is not None and status.state == "committed"
    assert status.adopted is not None and status.adopted.reason == "restored from backup"
    assert (status.databases or {})["pinvi"] == DeployedDatabase(*restored)


def test_a_restart_that_dies_before_the_reset_keeps_the_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_generation()
    previous = _committed_status(candidate)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    harness.mocks.reset.side_effect = DeploymentContractError("owner differs")

    with pytest.raises(DeploymentContractError):
        harness.service.rebuild_pinned_runtime(restart_reason="rebuild")

    status = read_deploy_status(harness.status_path)
    assert status is not None and status.state == "in_progress"
    # 지우지 못했으므로 다음 일반 실행은 여전히 옛 기준으로 확인해야 한다.
    assert dict(status.databases or {}) == dict(previous.databases or {})


@pytest.mark.parametrize("scenario", ("first_deploy", "same_pair", "new_pair", "restart"))
def test_rebuild_checks_shared_instance_readiness_without_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """ADR-53: 세 DB의 instance는 readiness로만 본다 — `up`·재생성·재시작이 없다(R3).

    readiness는 수렴·identity 판정보다 먼저이고, 요청하는 서비스는 runtime들이 DSN에서 유도한
    instance(중복 없이)다. 어떤 compose 호출도 그 instance를 이름으로 부르지 않는다.
    """

    candidate = _candidate_generation()
    previous = {
        "first_deploy": None,
        "same_pair": _committed_status(candidate),
        "new_pair": _committed_status(candidate, map_revision="0" * 40),
        "restart": _committed_status(candidate, map_revision="0" * 40),
    }[scenario]
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    judged: list[str] = []
    harness.mocks.map_precheck.side_effect = lambda _runtime: judged.append(
        f"after {len(harness.readiness_requests)} readiness requests"
    ) or "present"

    harness.service.rebuild_pinned_runtime(
        **({"restart_reason": "readiness"} if scenario == "restart" else {})
    )

    instance_requests = [
        index
        for index, request in enumerate(harness.readiness_requests)
        if request == (_FORWARD_INSTANCE,)
    ]
    # 외부 전제 다음, 판정 전에 한 번이다.
    assert instance_requests == [1]
    assert harness.readiness_requests[0] == (
        compose_service_module._PINNED_RUNTIME_EXTERNAL_PREREQUISITES
    )
    if scenario in {"first_deploy", "new_pair"}:
        assert judged == ["after 2 readiness requests"]
    assert not [operation for operation in harness.operations if _FORWARD_INSTANCE in operation]
    assert "Map PostgreSQL" not in harness.image_labels


def test_an_unready_shared_instance_is_refused_before_anything_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """멈춰 있거나 unhealthy인 instance를 재구축이 띄우지 않는다 — 거부하고 아무것도 바꾸지 않는다."""

    candidate = _candidate_generation()
    previous = _committed_status(candidate, map_revision="0" * 40)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    harness.live["not_ready"] = {_FORWARD_INSTANCE}

    with pytest.raises(DeploymentContractError, match="canonical readiness"):
        harness.service.rebuild_pinned_runtime()

    assert harness.operations == []
    assert read_deploy_status(harness.status_path) == previous
    harness.mocks.map_precheck.assert_not_called()


def test_a_map_database_the_bootstrap_would_refuse_is_refused_before_the_runtime_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_generation()
    previous = _committed_status(candidate, map_revision="0" * 40)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    harness.mocks.map_precheck.side_effect = DeploymentContractError(
        "map_application database already has a schema but is still owned by the "
        "bootstrap owner"
    )

    with pytest.raises(DeploymentContractError, match="bootstrap owner") as captured:
        harness.service.rebuild_pinned_runtime(adopt_reason="restored from backup")

    assert _mutating_operations(harness) == []
    assert read_deploy_status(harness.status_path) == previous
    assert compose_service_module.rebuild_failure_stage(captured.value) is None


def test_restart_skips_the_map_database_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """리셋은 그 DB를 지운다 — 지울 DB의 소유 상태로 리셋을 막지 않는다."""

    harness = _forward_harness(
        monkeypatch, tmp_path, previous=_committed_status(_candidate_generation())
    )
    harness.mocks.map_precheck.side_effect = DeploymentContractError("bootstrap owner")

    result = harness.service.rebuild_pinned_runtime(restart_reason="rebuild")

    assert result["outcome"] == "deployed"
    harness.mocks.map_precheck.assert_not_called()
    harness.mocks.isolation_preflight.assert_not_called()
    harness.mocks.reset_preflight.assert_called_once_with(harness.runtimes)


def test_a_restart_the_r2_fence_would_refuse_is_refused_before_the_runtime_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """오늘 전용 instance의 `--restart`: schema owner가 다른 DB도 소유해 R2가 거부한다.

    종전에는 그 거부가 Map·PinVi를 멈춘 뒤 리셋 안에서 났고, pair가 내려간 채 남았다(적대 리뷰
    2026-09-29). 리셋 대역도 같은 판정으로 거부하게 두어, 거부가 **어디서** 나는지를 본다.
    """

    candidate = _candidate_generation()
    previous = _committed_status(candidate)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    refusal = DeploymentContractError(
        "map_application database owner also owns a database outside the Map pair"
    )
    harness.mocks.reset_preflight.side_effect = refusal
    harness.mocks.reset.side_effect = refusal

    with pytest.raises(DeploymentContractError, match="outside the Map pair"):
        harness.service.rebuild_pinned_runtime(restart_reason="rebuild")

    assert _mutating_operations(harness) == []
    harness.mocks.reset.assert_not_called()
    assert read_deploy_status(harness.status_path) == previous


@pytest.mark.parametrize("adopt_reason", (None, "restored from backup"), ids=("new-pair", "adopt"))
def test_an_isolation_precondition_refusal_comes_before_the_runtime_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adopt_reason: str | None
) -> None:
    """R4의 live 전제 거부(예: DSN login이 Map schema owner의 member가 아니다)는 멈추기 전에 난다.

    종전에는 런타임을 멈추고 Map schema를 올린 뒤 R4 transaction 안에서 났다(적대 리뷰 2026-09-29).
    R4 대역도 같은 판정으로 거부하게 두어, 거부가 **어디서** 나는지를 본다.
    """

    candidate = _candidate_generation()
    previous = _committed_status(candidate, map_revision="0" * 40)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    refusal = DeploymentContractError(
        "Map application login ktm_rotated is not a non-superuser LOGIN member of "
        "ktm_feature_schema_owner"
    )
    harness.mocks.isolation_preflight.side_effect = refusal
    monkeypatch.setattr(
        compose_service_module, "ensure_map_databases_isolated", Mock(side_effect=refusal)
    )

    with pytest.raises(DeploymentContractError, match="not a non-superuser LOGIN member"):
        harness.service.rebuild_pinned_runtime(adopt_reason=adopt_reason)

    assert _mutating_operations(harness) == []
    harness.mocks.ensure_map.assert_not_called()
    assert read_deploy_status(harness.status_path) == previous
    harness.mocks.isolation_preflight.assert_called_once_with(
        harness.runtimes[0], login="ktm_feature_service"
    )


@pytest.mark.parametrize(
    ("state", "preflighted"),
    (("present", True), ("unbootstrapped", False), ("absent", False)),
)
def test_the_isolation_preflight_reads_only_a_handed_over_application_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, preflighted: bool
) -> None:
    """없거나 bootstrap 전인 app DB는 전체 경로가 R4 전에 만든다 — 그 전제를 미리 보면 거짓 거부다."""

    harness = _forward_harness(monkeypatch, tmp_path)
    harness.mocks.map_precheck.return_value = state

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "deployed"
    assert harness.mocks.isolation_preflight.called is preflighted
    harness.mocks.reset_preflight.assert_not_called()


def test_an_interrupted_adoption_keeps_protecting_the_adopted_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2 적대 리뷰 2차(minor): 채택이 중간에 죽으면 기준선이 비어 다음 일반 실행이 무엇이든
    커밋했고, 채택 기록도 사라졌다."""

    candidate = _candidate_generation()
    harness = _forward_harness(
        monkeypatch, tmp_path, previous=_committed_status(candidate)
    )
    restored = ("pinvi", 29999, "7300000000000000002")
    harness.live["identities"]["pinvi"] = restored
    harness.mocks.smoke.side_effect = DeploymentContractError("smoke failed")

    with pytest.raises(DeploymentContractError, match="smoke failed"):
        harness.service.rebuild_pinned_runtime(adopt_reason="restored from backup")

    interrupted = read_deploy_status(harness.status_path)
    assert interrupted is not None and interrupted.state == "in_progress"
    assert (interrupted.databases or {})["pinvi"] == DeployedDatabase(*restored)

    # 그사이 DB가 또 바뀌었다 — 일반 재실행은 거부한다.
    harness.live["identities"]["pinvi"] = ("pinvi", 30001, "7300000000000000002")
    with pytest.raises(DeploymentContractError, match="--adopt-live-databases"):
        harness.service.rebuild_pinned_runtime()

    # 되돌리면 일반 재실행이 채택 기록을 이어받아 끝낸다.
    harness.live["identities"]["pinvi"] = restored
    harness.mocks.smoke.side_effect = None
    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "deployed"
    committed = read_deploy_status(harness.status_path)
    assert committed is not None and committed.state == "committed"
    assert committed.adopted is not None
    assert committed.adopted.reason == "restored from backup"


def test_a_plain_rerun_after_the_reset_keeps_the_restart_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_generation()
    harness = _forward_harness(
        monkeypatch, tmp_path, previous=_committed_status(candidate)
    )
    harness.mocks.smoke.side_effect = DeploymentContractError("smoke failed")

    with pytest.raises(DeploymentContractError, match="smoke failed"):
        harness.service.rebuild_pinned_runtime(restart_reason="rebuild from empty")

    harness.mocks.smoke.side_effect = None
    harness.service.rebuild_pinned_runtime()

    committed = read_deploy_status(harness.status_path)
    assert committed is not None and committed.state == "committed"
    assert committed.restart is not None
    assert committed.restart.reason == "rebuild from empty"
    harness.mocks.reset.assert_called_once()


def test_a_restart_on_a_host_without_a_baseline_that_dies_before_the_reset_is_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기준선 없이(`deploy-status.json`이 없는 호스트) 시작한 `--restart`가 리셋 전에 죽었다. 이어서
    끝내는 일반 실행은 옛 DB를 전진시켰을 뿐이다 — 리셋 기록을 남기면 거짓이다(B2 적대 리뷰 3차)."""

    harness = _forward_harness(monkeypatch, tmp_path)
    original = harness.service._run_pinned_runtime_rebuild_compose

    def fail_first_stop(arguments: list[str], *, transaction: object) -> dict[str, object]:
        if arguments[:1] == ["stop"] and not any(
            operation[:1] == ("stop",) for operation in harness.operations
        ):
            harness.operations.append(tuple(arguments))
            raise DeploymentContractError("stop failed")
        return original(arguments, transaction=transaction)

    monkeypatch.setattr(harness.service, "_run_pinned_runtime_rebuild_compose", fail_first_stop)

    with pytest.raises(DeploymentContractError):
        harness.service.rebuild_pinned_runtime(restart_reason="rebuild from empty")

    harness.mocks.reset.assert_not_called()
    harness.service.rebuild_pinned_runtime()

    committed = read_deploy_status(harness.status_path)
    assert committed is not None and committed.state == "committed"
    assert committed.restart is None
    harness.mocks.reset.assert_not_called()


def test_the_fast_path_retries_image_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 pair의 재실행은 수렴만 한다 — 커밋 직후 정리가 실패했으면 다른 기회가 없다."""

    harness = _forward_harness(
        monkeypatch, tmp_path, previous=_committed_status(_candidate_generation())
    )

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "converged"
    harness.mocks.retention_generation.assert_called_once()
    harness.mocks.retention_candidate.assert_called_once()


def test_an_absent_pinvi_database_is_created_before_the_map_migrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    order: list[str] = []
    harness.mocks.create_pinvi.side_effect = lambda runtime: order.append(runtime.role)
    harness.mocks.ensure_map.side_effect = lambda *_args, **_kwargs: order.append("map")

    harness.service.rebuild_pinned_runtime()

    assert order == ["pinvi", "map"]


def test_a_failed_bookkeeping_write_leaves_the_verified_runtime_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """디스크가 차서 기록을 못 써도 검증이 끝난 런타임을 내리지 않는다.

    커밋 뒤 남는 기록은 deploy-status.json 하나다(ADR-51 D-2) — 그 committed 쓰기만
    실패시킨다. 시작 때의 in_progress 쓰기는 그대로 둬야 실패 뒤 상태를 읽을 수 있다.
    """

    harness = _forward_harness(monkeypatch, tmp_path)
    real_write_deploy_status = compose_service_module.write_deploy_status

    def fail_committed_write(path: Path, status: DeployStatus) -> None:
        if status.state == "committed":
            raise DeploymentContractError("disk full")
        real_write_deploy_status(path, status)

    monkeypatch.setattr(compose_service_module, "write_deploy_status", fail_committed_write)

    with pytest.raises(DeploymentContractError, match="disk full"):
        harness.service.rebuild_pinned_runtime()

    stop = ("stop", *RUNTIME_SERVICES, *sorted(_FORWARD_COMPANIONS))
    assert harness.operations.count(stop) == 1
    status = read_deploy_status(harness.status_path)
    assert status is not None and status.state == "in_progress"


# --- R3: 재구축은 PostgreSQL 서버를 하나도 바꾸지 않는다(M1 울타리, ADR-53으로 절대) -----------


def _transaction_with_postgres(**extra: Mapping[str, Any]) -> Any:
    """ADR-53 뒤의 n150처럼 PostgreSQL 서버가 공용 instance 하나인 frozen 문서."""

    return SimpleNamespace(
        resolved={
            "services": {
                "kor-travel-shared-postgres": {"command": ["postgres", "-p", "11000"]},
                "kor-travel-map-api": {"image": "sha256:" + "1" * 64},
                **extra,
            }
        }
    )


def _succeeding_recovery() -> Mock:
    return Mock(return_value={"success": True, "returncode": 0, "stdout": "", "stderr": ""})


@pytest.mark.parametrize(
    "arguments",
    [
        *(
            [action, *(["--no-deps"] if action in {"up", "run"} else []), "kor-travel-shared-postgres"]
            for action in (
                "up",
                "run",
                "create",
                "start",
                "restart",
                "stop",
                "kill",
                "rm",
                "down",
                "pause",
            )
        ),
        ["--profile", "bootstrap", "rm", "-f", "-s", "kor-travel-shared-postgres"],
        ["up", "-d", "--no-deps", "--wait", "kor-travel-map-api", "kor-travel-shared-postgres"],
    ],
    ids=lambda arguments: " ".join(arguments),
)
def test_rebuild_compose_refuses_mutating_a_shared_postgres_service(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    runner = _succeeding_recovery()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)

    with pytest.raises(
        DeploymentContractError, match="must not mutate a PostgreSQL service: kor-travel-shared-postgres"
    ):
        service._run_pinned_runtime_rebuild_compose(
            arguments, transaction=_transaction_with_postgres()
        )

    runner.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["ps", "--format", "json", "kor-travel-shared-postgres"],
        ["--profile", "bootstrap", "ps", "--all", "--format", "json", "kor-travel-shared-postgres"],
        ["stop", "kor-travel-map-api"],
        ["up", "-d", "--no-deps", "--wait", "kor-travel-map-api"],
    ],
    ids=lambda arguments: " ".join(arguments),
)
def test_rebuild_compose_allows_reads_and_non_postgres_mutations(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    runner = _succeeding_recovery()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)

    service._run_pinned_runtime_rebuild_compose(
        arguments, transaction=_transaction_with_postgres()
    )

    runner.assert_called_once()


def test_rebuild_compose_refuses_a_witnessed_postgres_server_that_is_not_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이름 목록이 없다 — 문서가 서버 실행 형태를 드러내면 이름이 무엇이든 PostgreSQL이다."""

    service = ComposeService()
    runner = _succeeding_recovery()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)

    with pytest.raises(DeploymentContractError, match="PostgreSQL service: sidecar-db"):
        service._run_pinned_runtime_rebuild_compose(
            ["stop", "sidecar-db"],
            transaction=_transaction_with_postgres(
                **{"sidecar-db": {"command": "sh -c 'exec /usr/lib/postgresql/16/bin/postgres'"}}
            ),
        )

    runner.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["stop"],
        ["down"],
        ["up", "-d", "--no-deps"],
        ["rm", "-f", "-s"],
        ["restart"],
        ["--profile", "bootstrap", "kill"],
        ["frobnicate", "kor-travel-map-api"],
        ["--bogus-flag", "stop", "kor-travel-map-api"],
    ],
    ids=lambda arguments: " ".join(arguments),
)
def test_rebuild_compose_refuses_a_mutating_call_without_explicit_services(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """서비스를 말하지 않는 mutation은 compose가 **모든** 서비스로 읽는다 — 공용 instance 포함."""

    service = ComposeService()
    runner = _succeeding_recovery()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)

    with pytest.raises(DeploymentContractError, match="must name its services explicitly"):
        service._run_pinned_runtime_rebuild_compose(
            arguments, transaction=_transaction_with_postgres()
        )

    runner.assert_not_called()


def _transaction_with_dependents() -> Any:
    """n150의 의존 그래프 모양: PinVi API는 공용 instance에, Map API는 geo API를 거쳐 그것에 의존한다."""

    healthy = {"condition": "service_healthy", "required": True}
    return _transaction_with_postgres(
        **{
            "pinvi-api": {"depends_on": {"kor-travel-shared-postgres": healthy}},
            "kor-travel-geo-api": {"depends_on": {"kor-travel-shared-postgres": healthy}},
            "kor-travel-map-api": {"depends_on": {"kor-travel-geo-api": healthy}},
        }
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["create", "pinvi-api"],
        # 이름 붙은 것은 Map API뿐이지만 closure가 geo API를 거쳐 공용 instance에 닿는다.
        ["create", "kor-travel-map-api"],
        ["start", "pinvi-api"],
        ["restart", "pinvi-api"],
        ["scale", "pinvi-api=1"],
        ["watch", "pinvi-api"],
    ],
    ids=lambda arguments: " ".join(arguments),
)
def test_rebuild_compose_refuses_a_call_whose_dependencies_reach_a_shared_postgres(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compose가 **실제로 닿는 것**을 센다. `create`에는 `--no-deps`가 없고, drift된 의존
    PostgreSQL을 다시 만든다(n150 Compose v5.2.0 실측) — 이름 붙은 서비스만 보면 통과한다.
    """

    service = ComposeService()
    runner = _succeeding_recovery()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)

    with pytest.raises(
        DeploymentContractError, match="must not mutate a PostgreSQL service: kor-travel-shared-postgres"
    ):
        service._run_pinned_runtime_rebuild_compose(
            arguments, transaction=_transaction_with_dependents()
        )

    runner.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["restart", "--no-deps", "pinvi-api"],
        ["scale", "--no-deps", "pinvi-api=1"],
        ["up", "-d", "--no-deps", "--wait", "kor-travel-map-api"],
        # 의존성 쪽으로 번지지 않는 명령이다.
        ["stop", "pinvi-api"],
        ["--profile", "bootstrap", "rm", "-f", "-s", "kor-travel-map-api"],
        ["build", "kor-travel-map-api"],
    ],
    ids=lambda arguments: " ".join(arguments),
)
def test_rebuild_compose_allows_dependents_when_compose_does_not_reach_their_dependencies(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """위 거부의 대조군 — 같은 의존 그래프에서 `--no-deps`거나 의존성으로 번지지 않으면 통과한다."""

    service = ComposeService()
    runner = _succeeding_recovery()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)

    service._run_pinned_runtime_rebuild_compose(
        arguments, transaction=_transaction_with_dependents()
    )

    runner.assert_called_once()


def test_r3_counts_only_the_no_deps_compose_parses_as_a_flag() -> None:
    """R3 자체도 argv 글자가 아니라 compose가 읽은 플래그로 판정한다 — startup gate와 독립으로.

    `run --rm pinvi-api pinvi-api --no-deps`에서 `--no-deps`는 컨테이너 argv다. compose는 PinVi API의
    `depends_on`(공용 instance)을 만들고, drift됐으면 다시 만든다(적대 리뷰 2026-09-29, n150 재현).
    """

    with pytest.raises(
        DeploymentContractError, match="must not mutate a PostgreSQL service: kor-travel-shared-postgres"
    ):
        ComposeService._require_rebuild_compose_spares_foreign_postgres(
            ["--profile", "bootstrap", "run", "--rm", "pinvi-api", "pinvi-api", "--no-deps"],
            transaction=_transaction_with_dependents(),
        )
    # 대조군: 같은 호출에서 `--no-deps`가 compose 옵션 자리에 있으면 통과한다.
    ComposeService._require_rebuild_compose_spares_foreign_postgres(
        ["--profile", "bootstrap", "run", "--rm", "--no-deps", "pinvi-api", "pinvi-api"],
        transaction=_transaction_with_dependents(),
    )


def test_r3_refuses_remove_orphans_only_where_compose_reads_it() -> None:
    """`--remove-orphans`도 compose가 플래그로 읽을 때만 이름 없는 컨테이너를 지운다."""

    with pytest.raises(DeploymentContractError, match="must not remove orphan containers"):
        ComposeService._require_rebuild_compose_spares_foreign_postgres(
            ["up", "-d", "--no-deps", "kor-travel-map-api", "--remove-orphans"],
            transaction=_transaction_with_dependents(),
        )
    # `run SERVICE` 뒤의 같은 글자는 컨테이너 argv다 — compose는 orphan을 지우지 않는다.
    ComposeService._require_rebuild_compose_spares_foreign_postgres(
        ["run", "--rm", "--no-deps", "kor-travel-map-api", "echo", "--remove-orphans"],
        transaction=_transaction_with_dependents(),
    )


@pytest.mark.parametrize(
    ("arguments", "transaction", "message"),
    [
        (
            ["up", "-d", "--no-deps", "--remove-orphans", "kor-travel-map-api"],
            _transaction_with_postgres(),
            "must not remove orphan containers",
        ),
        (
            ["stop", "kor-travel-map-api"],
            SimpleNamespace(resolved={"services": None}),
            "services mapping is unreadable",
        ),
        (
            ["create", "pinvi-api"],
            _transaction_with_postgres(
                **{"pinvi-api": {"depends_on": "kor-travel-shared-postgres"}}
            ),
            "pinvi-api depends_on is unreadable",
        ),
    ],
    ids=["remove-orphans", "no-services-mapping", "unreadable-depends-on"],
)
def test_rebuild_compose_refuses_what_it_cannot_classify(
    arguments: list[str],
    transaction: Any,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """분류할 수 없으면 통과가 아니라 거부다 — 이름 없는 orphan 제거, 읽을 수 없는 문서·의존성."""

    service = ComposeService()
    runner = _succeeding_recovery()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)

    with pytest.raises(DeploymentContractError, match=message):
        service._run_pinned_runtime_rebuild_compose(arguments, transaction=transaction)

    runner.assert_not_called()


def test_postgres_server_services_is_declared_or_witnessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        c6c_deployment,
        "_declared_postgres_compose_services",
        lambda: frozenset({"declared-db", "declared-but-absent"}),
    )

    assert c6c_deployment.postgres_server_services(
        {
            "services": {
                "declared-db": {"image": "anything"},
                "witnessed-db": {"command": "sh -c 'exec postgres -p 1'"},
                "env-db": {"environment": {"POSTGRES_PASSWORD_FILE": "/run/secrets/x"}},
                "app": {"command": ["uvicorn", "app:api"]},
                # psql 클라이언트의 DB 이름 `postgres`는 서버가 아니다.
                "client": {"command": ["psql", "-d", "postgres"]},
            }
        }
    ) == {"declared-db", "witnessed-db", "env-db"}


def test_rebuild_compose_refuses_even_the_retired_dedicated_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1까지의 예외(Map 전용 instance의 `up`)는 ADR-53으로 사라졌다 — 울타리가 절대다.

    옛 문서 모양(두 서버)을 그대로 두고 옛 호출을 보낸다. 전용 집합이 남아 있었다면 통과했다.
    """

    service = ComposeService()
    runner = _succeeding_recovery()
    monkeypatch.setattr(service, "_run_frozen_recovery", runner)

    with pytest.raises(DeploymentContractError, match="must not mutate a PostgreSQL service: map-pg"):
        service._run_pinned_runtime_rebuild_compose(
            ["up", "-d", "--no-deps", "--wait", "map-pg"],
            transaction=_transaction_with_postgres(
                **{"map-pg": {"command": ["postgres", "-p", "15101"]}}
            ),
        )

    runner.assert_not_called()


@pytest.mark.parametrize("scenario", ("first_deploy", "same_pair", "new_pair", "restart"))
def test_full_rebuild_never_names_a_postgres_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """재구축 전체를 대역으로 끝까지 돌리고, Docker에 닿은 **모든** compose argv를 본다.

    chokepoint(`_run_pinned_runtime_rebuild_compose`)는 진짜를 쓴다 — Docker 직전의
    `_run_frozen_recovery`만 기록기로 바꾼다. 단언은 chokepoint와 **해석기 모두와** 독립이다:
    기록된 argv의 낱말에서 PostgreSQL 서버 이름을 찾고, 의존성으로 번질 수 있는 명령 낱말이 든
    argv가 모두 `--no-deps`를 다는지 본다. M1의 전용 집합(Map 전용 instance)은 ADR-53이 비웠다 —
    그래서 단언은 "어떤 PostgreSQL 서버도 이름으로 불리지 않는다"다. instance readiness는 이
    기록기를 지나지 않는 읽기(`_require_services_ready`)다.
    """

    candidate = _candidate_generation()
    previous = {
        "first_deploy": None,
        "same_pair": _committed_status(candidate),
        "new_pair": _committed_status(candidate, map_revision="0" * 40),
        "restart": _committed_status(candidate, map_revision="0" * 40),
    }[scenario]
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    harness.transaction.resolved["services"].update(
        {
            _FORWARD_INSTANCE: {"command": ["postgres", "-p", str(_FORWARD_PORT)]},
            # 문서에 남은 다른 서버도 이름으로 불리지 않는다.
            "sidecar-db": {"command": ["postgres", "-p", "15101"]},
        }
    )
    recorded: list[tuple[str, ...]] = []

    def recover(
        arguments: Sequence[str],
        *,
        transaction: object,
        mutation_capability: object,
        capture_output: bool = True,
    ) -> dict[str, Any]:
        del transaction, mutation_capability, capture_output
        recorded.append(tuple(arguments))
        return {"success": True, "returncode": 0, "stdout": "", "stderr": ""}

    # harness가 대역으로 바꾼 compose 실행기를 걷어 진짜 chokepoint를 태운다.
    monkeypatch.delattr(harness.service, "_run_pinned_runtime_rebuild_compose")
    monkeypatch.setattr(harness.service, "_run_frozen_recovery", recover)

    if scenario == "restart":
        harness.service.rebuild_pinned_runtime(restart_reason="R3 end-to-end")
    else:
        harness.service.rebuild_pinned_runtime()

    postgres = c6c_deployment.postgres_server_services(harness.transaction.resolved)
    assert postgres == {_FORWARD_INSTANCE, "sidecar-db"}
    # 탐지기가 공허하지 않다: 재구축의 mutation이 실제로 기록됐다.
    assert any(operation[0] == "up" for operation in recorded)
    named = {token for operation in recorded for token in operation} & postgres
    assert named == set(), sorted(named)
    starters = [
        operation
        for operation in recorded
        if {"create", "restart", "run", "scale", "start", "up", "watch"} & set(operation)
    ]
    assert starters
    assert all("--no-deps" in operation for operation in starters), [
        operation for operation in starters if "--no-deps" not in operation
    ]


def test_converge_applies_isolation_before_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 pair 수렴 한 번으로 R4(PUBLIC 차단·login CONNECT·연결 상한)가 live에 걸린다."""

    candidate = _candidate_generation()
    harness = _forward_harness(monkeypatch, tmp_path, previous=_committed_status(candidate))

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "converged"
    isolation = [
        index for index, operation in enumerate(harness.operations) if operation[0] == _ISOLATION
    ]
    runtime_up = [
        index
        for index, operation in enumerate(harness.operations)
        if operation[0] == "up" and "kor-travel-map-api" in operation
    ]
    assert len(isolation) == 1 and len(runtime_up) == 1
    assert isolation[0] < runtime_up[0]
    assert harness.operations[isolation[0]] == (
        _ISOLATION,
        "kor_travel_map",
        "ktm_feature_service",
    )


def test_deploy_applies_isolation_after_the_bootstrap_and_before_the_map_api_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fresh bootstrap은 기본 ACL(`datacl IS NULL`)을 요구한다 — 격리는 그 뒤, Map이 붙기 전이다."""

    harness = _forward_harness(monkeypatch, tmp_path)
    harness.mocks.ensure_map.side_effect = lambda *_args, **_kwargs: (
        harness.operations.append(("ensure-map-application-database",)) or "created"
    )

    harness.service.rebuild_pinned_runtime()

    names = [operation[0] for operation in harness.operations]
    isolation = names.index(_ISOLATION)
    assert names.count(_ISOLATION) == 1
    assert names.index("ensure-map-application-database") < isolation
    assert harness.operations.index(_SCHEMA_RUN) < isolation
    api_up = next(
        index
        for index, operation in enumerate(harness.operations)
        if operation[0] == "up" and "kor-travel-map-api" in operation
    )
    assert isolation < api_up


def test_a_malformed_map_login_is_refused_before_anything_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.transaction.environment.effective["KOR_TRAVEL_MAP_PG_DSN"] = (
        f"postgresql+asyncpg://127.0.0.1:{_FORWARD_PORT}/kor_travel_map"
    )

    with pytest.raises(DeploymentContractError, match="Map application login is invalid"):
        harness.service.rebuild_pinned_runtime()

    assert harness.operations == []


# --- ADR-53 S1: bootstrap은 instance admin으로, 판정은 멈추기 전에 ---------------------------------

_SHARED_ADMIN_PASSWORD = "shared-admin-password-0123456789-abcdef"
#: PostgreSQL 16이 `_SHARED_ADMIN_PASSWORD`로 만든 진짜 verifier(test_database_runtime.py와 같다).
_SHARED_ADMIN_VERIFIER = (
    "SCRAM-SHA-256$4096:YtxppmrpF2qWtqnoXM/7/g==$MKxyZdyNGGUPggov7ygSmRWUC/cNH8b7fKdl9GXL3qI="
    ":H+sCke8S9deqymWjIMFhKyQN9vPTRqbdM7XYVmpjQjA="
)


def _s1_output(head: str) -> bytes:
    return f"{head}\n".encode("ascii")


def _with_real_admin_preflight(
    harness: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    *,
    output: bytes,
    password: str = _SHARED_ADMIN_PASSWORD,
) -> Mock:
    """harness의 대역 대신 진짜 `require_map_bootstrap_admin_ready`를 태운다(psql만 대역).

    instance의 admin secret은 frozen 문서에서 유도된다 — 서버의 `POSTGRES_PASSWORD_FILE` →
    `secrets[]` → 최상위 `secrets.<source>.environment` → frozen env 값.
    """

    harness.transaction.resolved["services"][_FORWARD_INSTANCE] = {
        "command": ["postgres", "-p", str(_FORWARD_PORT)],
        "environment": {
            "POSTGRES_USER": _FORWARD_ADMIN,
            "POSTGRES_PASSWORD_FILE": "/run/secrets/shared-pw",
        },
        "secrets": [{"source": "shared-pw", "target": "/run/secrets/shared-pw"}],
    }
    harness.transaction.resolved["secrets"] = {"shared-pw": {"environment": "SHARED_PW"}}
    harness.transaction.environment.effective.update(
        {
            "SHARED_PW": password,
            "KOR_TRAVEL_MAP_SERVICE_PASSWORD": "map-service-password-0123456789-abcdef",
        }
    )
    # 첫 읽기는 superuser·role setting·extension, 둘째는 admin의 `pg_authid` verifier다.
    reads = Mock(side_effect=[output, f"{_SHARED_ADMIN_VERIFIER}\n".encode("ascii")])
    monkeypatch.setattr(database_runtime_module, "_run_checked", reads)
    monkeypatch.setattr(
        compose_service_module,
        "require_map_bootstrap_admin_ready",
        database_runtime_module.require_map_bootstrap_admin_ready,
    )
    return reads


@pytest.mark.parametrize(
    ("output", "password", "match"),
    (
        (_s1_output("f|0|2"), _SHARED_ADMIN_PASSWORD, "must be a superuser"),
        (_s1_output("t|2|2"), _SHARED_ADMIN_PASSWORD, "cluster-wide role settings"),
        (_s1_output("t|0|1"), _SHARED_ADMIN_PASSWORD, "lacks an extension"),
        (_s1_output("t|0|2"), "too-short", "32..256 URI-unreserved"),
        (_s1_output("t|0|2"), "map-service-password-0123456789-abcdef", "must differ"),
        # `.env`의 password가 모양은 맞지만 admin의 live verifier와 다르다(회전·편집 drift).
        (_s1_output("t|0|2"), "rotated-admin-password-0123456789-abcdef", "live SCRAM"),
    ),
    ids=(
        "superuser",
        "role-settings",
        "extension",
        "password-shape",
        "password-distinct",
        "password-drift",
    ),
)
def test_an_s1_refusal_comes_before_the_runtime_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: bytes,
    password: str,
    match: str,
) -> None:
    """S1 고유의 실패가 bootstrap one-shot 안에서야 나면 Map·PinVi가 내려간 채 남는다(§1.3 b).

    검사 하나마다 진짜 판정을 태워, 거부가 **어디서** 나는지 본다 — 멈춤(`stop`)도 다른 어떤 변경도
    기록되지 않아야 한다.
    """

    candidate = _candidate_generation()
    previous = _committed_status(candidate, map_revision="0" * 40)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    harness.mocks.map_precheck.return_value = "absent"
    _with_real_admin_preflight(harness, monkeypatch, output=output, password=password)

    with pytest.raises(DeploymentContractError, match=match) as raised:
        harness.service.rebuild_pinned_runtime()

    assert not any(operation[0] == "stop" for operation in harness.operations)
    assert _mutating_operations(harness) == []
    harness.mocks.ensure_map.assert_not_called()
    assert read_deploy_status(harness.status_path) == previous
    assert password not in str(raised.value)


@pytest.mark.parametrize(
    ("state", "restart", "checked"),
    (
        ("absent", False, True),
        ("unbootstrapped", False, True),
        ("present", False, False),
        # 리셋은 Map DB를 지운다 — 그 뒤 bootstrap이 돈다.
        ("present", True, True),
    ),
)
def test_admin_ready_check_is_skipped_when_the_map_database_is_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    restart: bool,
    checked: bool,
) -> None:
    harness = _forward_harness(
        monkeypatch,
        tmp_path,
        previous=_committed_status(_candidate_generation(), map_revision="0" * 40),
    )
    harness.mocks.map_precheck.return_value = state

    result = harness.service.rebuild_pinned_runtime(
        **({"restart_reason": "rebuild"} if restart else {})
    )

    assert result["outcome"] == "deployed"
    if checked:
        harness.mocks.admin_preflight.assert_called_once_with(
            harness.runtimes[0],
            resolved=harness.transaction.resolved,
            environment=harness.transaction.environment.effective,
        )
    else:
        harness.mocks.admin_preflight.assert_not_called()


def test_the_same_pair_convergence_never_checks_the_bootstrap_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(
        monkeypatch, tmp_path, previous=_committed_status(_candidate_generation())
    )

    assert harness.service.rebuild_pinned_runtime()["outcome"] == "converged"
    harness.mocks.admin_preflight.assert_not_called()


def test_bootstrap_one_shot_gets_the_derived_admin_and_port_and_no_password_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """admin 이름·포트는 앱 DB runtime에서 유도한 실행 시점 `-e`다. password는 argv에 없다.

    one-shot은 그 instance의 secret file에서 password를 스스로 읽는다(ADR-53 S1). Manager가
    `--env NAME`으로 값을 넘기던 옛 모양은 전용 superuser password를 argv 옆 환경으로 옮겼다.
    """

    harness = _forward_harness(monkeypatch, tmp_path)
    # instance의 admin secret을 frozen 문서에서 **유도할 수 있게** 둔다(서버의
    # `POSTGRES_PASSWORD_FILE` → secrets[] → `SHARED_PW`). 그래야 password를 `-e`로 넘기는
    # 회귀가 실제로 값을 argv에 올릴 수 있고, 아래 단언이 그것을 잡는다.
    harness.transaction.resolved["services"][_FORWARD_INSTANCE] = {
        "command": ["postgres", "-p", str(_FORWARD_PORT)],
        "environment": {
            "POSTGRES_USER": _FORWARD_ADMIN,
            "POSTGRES_PASSWORD_FILE": "/run/secrets/shared-pw",
        },
        "secrets": [{"source": "shared-pw", "target": "/run/secrets/shared-pw"}],
    }
    harness.transaction.resolved["secrets"] = {"shared-pw": {"environment": "SHARED_PW"}}
    harness.transaction.environment.effective["SHARED_PW"] = _SHARED_ADMIN_PASSWORD
    assert (
        c6c_deployment.postgres_admin_secret(
            harness.transaction.resolved, _FORWARD_INSTANCE
        ).environment
        == "SHARED_PW"
    )
    harness.runtimes = (
        replace(harness.runtimes[0], admin_name="derived_admin", owner_name="derived_admin", port=15432),
        *harness.runtimes[1:],
    )
    monkeypatch.setattr(
        compose_service_module,
        "database_runtimes_from_frozen_contract",
        lambda **_kwargs: harness.runtimes,
    )

    def ensure(runtime: DatabaseRuntime, *, run_role_bootstrap: Any) -> str:
        del runtime
        run_role_bootstrap()
        return "created"

    harness.mocks.ensure_map.side_effect = ensure

    harness.service.rebuild_pinned_runtime()

    (bootstrap,) = [
        operation
        for operation in harness.operations
        if "kor-travel-map-db-role-bootstrap" in operation
        and "run" in operation
    ]
    assert bootstrap == (
        "--profile",
        "bootstrap",
        "run",
        "--rm",
        "--no-deps",
        "-e",
        "KOR_TRAVEL_MAP_POSTGRES_USER=derived_admin",
        "-e",
        "KTDM_MAP_BOOTSTRAP_PGPORT=15432",
        "kor-travel-map-db-role-bootstrap",
    )
    tokens = " ".join(token for operation in harness.operations for token in operation)
    assert "--env" not in bootstrap
    assert _SHARED_ADMIN_PASSWORD not in tokens
    assert "SHARED_PW" not in tokens
    # 어떤 호출의 어떤 `-e NAME=VALUE`도 admin password를 싣지 않는다.
    passed = [
        operation[index + 1]
        for operation in harness.operations
        for index, token in enumerate(operation[:-1])
        if token in {"-e", "--env"}
    ]
    assert passed and not any(_SHARED_ADMIN_PASSWORD in value for value in passed)


# --- ADR-54: 공용 Dagster plane에 합류한 target의 pinned 재구축 ----------------------------------

_MAP_LEGACY_DAGSTER = ("kor-travel-map-dagster", "kor-travel-map-dagster-daemon")
_PINVI_LEGACY_DAGSTER = ("pinvi-dagster", "pinvi-dagster-daemon")
#: 전환된 target의 frozen render에 남는 companion(옛 webserver·daemon은 `legacy-dagster`라 없다).
_FLIP_CASES = {
    "map": (
        "kor-travel-map-dagster-code-server",
        _MAP_LEGACY_DAGSTER,
        {
            "pinvi-dagster-code-server": "pinvi_dagster",
            "pinvi-dagster-daemon": "pinvi_dagster",
        },
    ),
    "pinvi": (
        "pinvi-dagster-code-server",
        _PINVI_LEGACY_DAGSTER,
        {"kor-travel-map-dagster-code-server": "map_dagster"},
    ),
}


def _flipped_topology(*targets: str) -> RuntimeTopology:
    from test_dagster_shared_workspace_is_derived import _flip, _own_pair_documents

    compose_document, targets_document = _own_pair_documents()
    for target_id in targets:
        _flip(compose_document, targets_document, target_id)
    families = derive_dagster_families(compose_document, targets_document)
    # 실행 경로(`runtime_topology()`)처럼 Map·PinVi family만 넘긴다 — 이미 `shared`인 다른 target(weather)의
    # 옛 서비스는 pinned runtime의 집합이 아니다.
    return runtime_topology({target: families[target] for target in ("map", "pinvi")})


def test_every_own_target_derives_the_pre_adr54_slot_services() -> None:
    """모두 `own`이면 파생은 종전 literal과 글자까지 같다 — 배포 기록·tag·보존이 그대로다."""

    topology = runtime_topology()
    assert topology.runtime_services == RUNTIME_SERVICES
    assert topology.services_for(COMPOSE_BUILT_RUNTIME_SLOTS) == COMPOSE_BUILT_RUNTIME_SERVICES
    assert topology.retired_services == ()
    assert dict(topology.slot_services) == dict(zip(RUNTIME_SLOTS, RUNTIME_SERVICES, strict=True))


@pytest.mark.parametrize("target", sorted(_FLIP_CASES))
def test_a_flipped_target_plans_its_code_server_and_never_its_old_dagster(target: str) -> None:
    """planning: slot·build·candidate tag·companion·API 의존이 옛 webserver·daemon을 부르지 않는다."""

    carrier, legacy, _ = _FLIP_CASES[target]
    topology = _flipped_topology(target)
    assert carrier in topology.runtime_services
    assert set(legacy).isdisjoint(topology.runtime_services)
    assert set(topology.retired_services) == set(legacy)
    build = CandidateRuntimeBuild(_sources(), _map_application_candidate(), topology=topology)
    names = [
        *build.image_names.values(),
        *map_application_300_paired_build_image_names(_sources(), topology).values(),
    ]
    assert not [name for name in names for old in legacy if f"/{old}:" in name]
    assert set(legacy).isdisjoint(build.build_services)
    if target == "pinvi":
        assert build.build_services[-1] == carrier
    # 그 profile을 켠 render가 옛 서비스를 싣고 오면 companion으로 받지 않고 거부한다.
    image_ids = _candidate_generation().image_ids
    slot = "map_dagster" if target == "map" else "pinvi_dagster"
    with pytest.raises(DeploymentContractError, match="retired Dagster services"):
        generation_companion_services(
            {"services": {legacy[-1]: {"image": image_ids[slot]}}},
            image_ids,
            excluded_services=(),
            topology=topology,
        )


@pytest.mark.parametrize("target", sorted(_FLIP_CASES))
def test_a_flipped_target_never_starts_its_old_dagster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """실제 재구축 경로를 전환된 모델로 돌린다: 옛 webserver·daemon은 어떤 compose 호출·readiness·검사에도 없고
    code-server는 Map·PinVi 단계에서 `up`되고 배포 기록에 남는다."""

    carrier, legacy, companions = _FLIP_CASES[target]
    harness = _forward_harness(monkeypatch, tmp_path, flipped=(target,), companions=companions)

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "deployed"
    touched = [
        (kind, entry)
        for kind, entries in (
            ("operation", harness.operations),
            ("readiness", harness.readiness_requests),
            ("inspect", harness.inspected_services),
        )
        for entry in entries
        if set(entry) & set(legacy)
    ]
    assert touched == []
    assert set(legacy).isdisjoint(harness.image_labels)
    ups = [operation for operation in harness.operations if operation[0] == "up"]
    assert [operation for operation in ups if carrier in operation]
    status = read_deploy_status(harness.status_path)
    assert status is not None and status.state == "committed"
    assert carrier in status.images
    assert set(legacy).isdisjoint(status.images)


def test_flipping_both_pinned_targets_still_deploys_only_code_servers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = {*_MAP_LEGACY_DAGSTER, *_PINVI_LEGACY_DAGSTER}
    harness = _forward_harness(monkeypatch, tmp_path, flipped=("map", "pinvi"), companions={})

    harness.service.rebuild_pinned_runtime()

    assert not [operation for operation in harness.operations if set(operation) & legacy]
    runtime = runtime_topology().runtime_services
    assert "kor-travel-map-dagster-code-server" in runtime
    assert "pinvi-dagster-code-server" in runtime
    assert ("stop", *runtime) in harness.operations


@pytest.mark.parametrize("target", sorted(_FLIP_CASES))
def test_an_explicit_up_of_a_flipped_code_server_reaches_its_api(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """R3 분류: 명시 `up`이 의존성을 끌어오면 닿는 API. code-server가 slot 자리를 잇고, `legacy-dagster`로
    내려간 옛 서비스도 명시하면 compose가 그 API까지 끌어오므로 계속 센다(보수적 분류)."""

    carrier, legacy, _ = _FLIP_CASES[target]
    topology = _flipped_topology(target)
    monkeypatch.setattr(compose_service_module, "runtime_topology", lambda: topology)
    api = "kor-travel-map-api" if target == "map" else "pinvi-api"
    scope, _ = ComposeService._parse_compose_mutation(["up", "-d", carrier])
    assert scope == [carrier, api]
    for old in legacy:
        scope, _ = ComposeService._parse_compose_mutation(["up", "-d", old])
        assert scope == [old, api]
    # 모두 `own`이면 종전 분류 그대로다(daemon·webserver → API, code-server는 slot이 아니라 세지 않는다).
    monkeypatch.setattr(compose_service_module, "runtime_topology", runtime_topology)
    for old in legacy[: 2 if target == "map" else 1]:
        scope, _ = ComposeService._parse_compose_mutation(["up", "-d", old])
        assert scope == [old, api]
    scope, _ = ComposeService._parse_compose_mutation(["up", "-d", carrier])
    assert scope == [carrier]


@pytest.mark.parametrize("target", sorted(_FLIP_CASES))
def test_a_flipped_targets_running_old_container_is_refused_before_anything_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """전환 뒤 옛 daemon은 `stop`에 들지 않는다 — 떠 있으면 무엇을 멈추거나 migration하기 전에 거부한다."""

    _, legacy, companions = _FLIP_CASES[target]
    harness = _forward_harness(monkeypatch, tmp_path, flipped=(target,), companions=companions)
    expected = [f"{name}-latest" for name in legacy]
    daemon_container = f"{legacy[-1]}-latest"
    harness.live["running_containers"] = {daemon_container}

    with pytest.raises(DeploymentContractError, match="retired Dagster services .* still running"):
        harness.service.rebuild_pinned_runtime()

    assert harness.container_checks == expected
    assert [operation for operation in harness.operations if operation[0] != "container-inspect"] == []
    assert read_deploy_status(harness.status_path) is None

    # 멈춰 있으면(또는 없으면) 같은 경로가 끝까지 간다 — 검사는 수렴·전체 경로 모두의 앞이다.
    harness.live["running_containers"] = set()
    harness.service.rebuild_pinned_runtime()
    first_mutation = next(
        index for index, operation in enumerate(harness.operations) if operation[0] == "stop"
    )
    inspected = [
        index
        for index, operation in enumerate(harness.operations)
        if operation[0] == "container-inspect"
    ]
    before = [index for index in inspected if index < first_mutation]
    assert len(before) >= len(expected)
    # 뒤의 검사는 plane을 건드리기 직전의 재확인이다(ADR-54 개정) — 옛 컨테이너 전부를 plane `up` 전에 다시 본다.
    plane_up = _plane_ups(harness.operations)
    assert len(plane_up) == 1
    rechecked = {
        harness.operations[index][1]
        for index in inspected
        if first_mutation < index < plane_up[0]
    }
    assert set(expected) <= rechecked


def test_an_own_rebuild_inspects_no_retired_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.service.rebuild_pinned_runtime()
    assert harness.container_checks == []


def test_the_container_probe_reads_absent_running_and_refuses_the_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {
        "absent": (1, "", "Error response from daemon: No such container: absent\n"),
        "running": (0, "true\n", ""),
        "stopped": (0, "false\n", ""),
        "broken": (1, "", "Cannot connect to the Docker daemon\n"),
    }

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        code, out, err = answers[command[-1]]
        return subprocess.CompletedProcess(command, code, stdout=out, stderr=err)

    monkeypatch.setattr(compose_service_module.subprocess, "run", run)
    probe = ComposeService._inspect_container_running
    assert probe("absent", label="x") is None
    assert probe("running", label="x") is True
    assert probe("stopped", label="x") is False
    with pytest.raises(DeploymentContractError, match="cannot inspect the x container"):
        probe("broken", label="x")


@pytest.mark.parametrize(
    "arguments",
    (
        ["ps", "--format", "json"],
        ["config", "--format", "json"],
        ["logs", "--tail", "10", "kor-travel-map-api"],
        ["stop", "kor-travel-map-dagster-daemon"],
        ["up", "-d", "--no-deps", "grafana"],
    ),
)
def test_reading_and_explicit_stopping_do_not_need_the_installed_model(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    """모델이 깨져도(참조 파일 부재·어긋난 모양) 보고 멈추는 길은 남는다 — 파생은 필요한 mutation에서만."""

    def broken() -> RuntimeTopology:
        raise DeploymentContractError("reference compose cannot be read")

    monkeypatch.setattr(compose_service_module, "runtime_topology", broken)
    monkeypatch.setattr(runtime_topology_module, "installed_dagster_family", lambda _t: broken())
    ComposeService._parse_compose_mutation(arguments)
    ComposeService._compose_mutation_identifiers(arguments)
    # 의존성을 끌어오는 `up`은 파생이 필요하다 — 그때는 조용히 넘어가지 않고 멈춘다.
    with pytest.raises(DeploymentContractError, match="reference compose"):
        ComposeService._parse_compose_mutation(["up", "-d", "kor-travel-map-ui"])


# --- ADR-54 파생의 `own` 불변식: 파생 이전(6f384ba)의 지문과 같다 -----------------------------------

_OWN_FINGERPRINT = Path(__file__).resolve().parent / "fixtures" / "pinned_runtime_own_fingerprint.json"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            "|".join(key) if isinstance(key, tuple) else str(key): _jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(_jsonable(item) for item in value)
    return value


def _own_fingerprint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """모든 target이 `own`일 때 파생이 만드는 것 전부를 서비스 이름·문자열로. 픽스처와 같은 모양이다.

    픽스처는 파생 이전 코드(6f384ba, literal 집합)에서 같은 항목을 떠 seed revision·pinset만 자리표로 바꾼
    것이다. 2026-10-04 platform-topology.md §7 4단계에서 옛 Map·PinVi Dagster metadata DB 의존(옛 Map storage
    migrate one-shot·`map_dagster_head`·`map_dagster` DB role·두 metadata URL 행)을 지운 만큼만 다시 떴다 — 그
    변경은 의도한 것이고, 나머지 항목은 6f384ba 그대로다. 의도한 변경 셋은 빠져 있다 — M05 이미지 역할 키(`pinvi-app-dagster`), 보존 namespace가 알아보는
    이름(family 전부, 적대 리뷰 MED-3), 그리고 seed에 묶인 generation sha256.
    """

    import kor_travel_docker_manager.services.c6c_image_retention as retention
    import kor_travel_docker_manager.services.legacy_override_retirement as retirement
    import kor_travel_docker_manager.services.m05_isolated_harness as m05

    generation = _candidate_generation()
    sources = _sources()
    build = CandidateRuntimeBuild(sources, _map_application_candidate())
    topology = runtime_topology()
    out: dict[str, Any] = {
        "runtime_services": list(topology.runtime_services),
        "built_services": list(build.build_services),
    }
    payload = generation.to_payload()
    payload.pop("recorded_at")
    out["generation_payload"] = payload
    out["build_compose_environment"] = dict(build.compose_environment())
    out["generation_compose_environment"] = dict(generation_compose_environment(generation))
    out["candidate_image_names"] = sorted(build.image_names.values())
    out["paired_names"] = sorted(map_application_300_paired_build_image_names(sources).values())
    out["runtime_image_references"] = sorted(
        (topology.service(slot) or "-", image) for slot, image in build.runtime_image_references.items()
    )
    document = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    )
    by_variable = {
        variable: generation.image_ids[slot]
        for slot, variable in pinned_runtime_rebuild_module._IMAGE_ENVIRONMENT.items()
    }
    services: dict[str, dict[str, str]] = {}
    for name, service in document["services"].items():
        raw = str(service.get("image", ""))
        match = re.fullmatch(r"\$\{([A-Z0-9_]+)[^}]*\}", raw)
        services[name] = {"image": by_variable.get(match.group(1), raw) if match else raw}
    companions = generation_companion_services(
        {"services": services},
        generation.image_ids,
        excluded_services=compose_service_module._PINNED_RUNTIME_ONESHOT_WRITERS,
        topology=topology,
    )
    out["companions"] = {name: topology.service(owner) for name, owner in companions.items()}
    out["deployed_images"] = ComposeService._deployed_images(generation, companions)
    out["retention_desired"] = retention._desired_references([generation], topology)
    out["c6c_required"] = sorted(c6c_deployment._CANDIDATE_REQUIRED_PROTECTED_SERVICES)
    out["c6c_known"] = sorted(c6c_deployment._CANDIDATE_KNOWN_SERVICE_NAMES)
    out["c6c_map_runtime"] = list(c6c_deployment._MAP_RUNTIME_SERVICES)
    out["c6c_pinvi_dsn_credentials"] = [list(row) for row in c6c_deployment._PINVI_DSN_SERVICE_CREDENTIALS]
    out["c6c_pinvi_database_url_raw"] = list(dict(c6c_deployment._PINVI_DATABASE_URL_RAW_VALUES).items())
    out["c6c_map_database_canonical"] = [
        [list(key), value] for key, value in dict(c6c_deployment._MAP_DATABASE_CANONICAL_ENV_VALUES).items()
    ]
    out["c6c_candidate_canonical_api"] = [
        [list(key), value]
        for key, value in dict(c6c_deployment._CANDIDATE_CANONICAL_API_ENV_VALUES).items()
    ]
    out["c6c_contract_locked"] = {
        key: sorted(value)
        for key, value in sorted(dict(c6c_deployment._CONTRACT_LOCKED_ENV_NAMES_BY_SERVICE).items())
    }
    out["c6c_describe"] = [
        c6c_deployment._describe_candidate_service_key(name) for name in sorted(document["services"])
    ]
    out["mutation_parse_up"] = {
        name: ComposeService._parse_compose_mutation(["up", "-d", name])[0]
        for name in sorted(document["services"])
    }
    out["mutation_identifiers_down"] = ComposeService._compose_mutation_identifiers(["down"])
    out["geo_override_services"] = list(retirement._GEO_SERVICES)
    out["m05_roles_values"] = sorted(m05._RUNTIME_IMAGE_ROLES.values())
    out["pinset_seed"] = PINNED_RUNTIME_RELEASE.pinset_sha256

    harness = _forward_harness(monkeypatch, tmp_path / "first")
    result = harness.service.rebuild_pinned_runtime()
    out["first_deploy"] = {
        "outcome": result["outcome"],
        "operations": [list(operation) for operation in harness.operations],
        "readiness": [list(request) for request in harness.readiness_requests],
        "image_labels": harness.image_labels,
        "inspected": [list(services) for services in harness.inspected_services],
        "expected_images": harness.expected_images,
    }
    harness = _forward_harness(monkeypatch, tmp_path / "second", previous=_committed_status(generation))
    result = harness.service.rebuild_pinned_runtime()
    out["converge"] = {
        "outcome": result["outcome"],
        "operations": [list(operation) for operation in harness.operations],
        "readiness": [list(request) for request in harness.readiness_requests],
    }
    text = json.dumps(_jsonable(out), sort_keys=True, ensure_ascii=False)
    for value, placeholder in (
        (payload["pinset_sha256"], "<PINSET>"),
        (payload["map_source_revision"], "<MAP_REVISION>"),
        (payload["pinvi_source_revision"], "<PINVI_REVISION>"),
    ):
        text = text.replace(str(value), placeholder)
    return cast(dict[str, Any], json.loads(text))


def test_every_own_rebuild_matches_the_pre_adr54_fingerprint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """모두 `own`이면 파생은 파생 이전과 글자까지 같다 — slot 서비스·build·tag·companion·배포 images·보존·C6c
    표·mutation 분류·generation payload·compose env, 그리고 첫 배포·수렴의 호출 순서 전체."""

    expected = json.loads(_OWN_FINGERPRINT.read_text(encoding="utf-8"))
    expected.pop("_source")
    actual = _own_fingerprint(monkeypatch, tmp_path)
    assert sorted(actual) == sorted(expected)
    assert [key for key in sorted(expected) if actual[key] != expected[key]] == []


def test_the_production_pinset_is_a_function_of_the_sources_alone() -> None:
    """운영 pin(2026-09-30 회전, `7ea6689c`)을 이 코드로 재계산해도 같다 — pinset은 서비스 이름을 보지 않는다."""

    assert (
        canonical_pinset_sha256(
            version=5,
            sources=source_specs_for(
                map_revision="791f49f403553dcfe1a0a5a2b6b67a0557ab22e1",
                pinvi_revision="83f00171da70bc3429bace9b32b5d3782e3c72aa",
            ),
        )
        == "7ea6689c7d0051b64abf373b6af3bfed90fed816a3546e0aa5a7bac0b8b046b7"
    )


# --- plane를 아는 재구축(ADR-54 개정, Map 전환 준비) -------------------------------------------------

_PLANE_SERVICES = ("kor-travel-dagster-webserver", "kor-travel-dagster-daemon")


def _plane_ups(operations: Sequence[tuple[str, ...]]) -> list[int]:
    return [
        index
        for index, operation in enumerate(operations)
        if operation[:1] == ("up",) and set(_PLANE_SERVICES) <= set(operation)
    ]


def test_the_shared_plane_is_derived_from_its_shape() -> None:
    """plane은 공용 workspace를 붙인 활성 `dagster-webserver`·`dagster-daemon`이다 — 이름을 적지 않는다."""

    compose, _ = runtime_topology_module._installed_documents()
    plane = runtime_topology_module.derive_shared_dagster_plane(compose)
    assert plane.services == _PLANE_SERVICES
    # 모양이 어긋나면 거부한다: workspace를 떼면 없고, 둘이면 고르지 않는다.
    broken = copy.deepcopy(dict(compose))
    broken["services"] = dict(broken["services"])
    broken["services"]["kor-travel-dagster-daemon"] = {
        **broken["services"]["kor-travel-dagster-daemon"],
        "volumes": [],
    }
    with pytest.raises(DeploymentContractError, match="shared daemon"):
        runtime_topology_module.derive_shared_dagster_plane(broken)
    twin = copy.deepcopy(dict(compose))
    twin["services"] = dict(twin["services"])
    twin["services"]["kor-travel-dagster-webserver-twin"] = twin["services"]["kor-travel-dagster-webserver"]
    with pytest.raises(DeploymentContractError, match="shared webserver"):
        runtime_topology_module.derive_shared_dagster_plane(twin)
    # legacy-dagster로 내려간 서비스는 plane이 아니다.
    profiled = copy.deepcopy(dict(compose))
    profiled["services"] = dict(profiled["services"])
    profiled["services"]["kor-travel-dagster-daemon"] = {
        **profiled["services"]["kor-travel-dagster-daemon"],
        "profiles": ["legacy-dagster"],
    }
    with pytest.raises(DeploymentContractError, match="shared daemon"):
        runtime_topology_module.derive_shared_dagster_plane(profiled)


def test_an_all_own_rebuild_never_touches_the_plane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.service.rebuild_pinned_runtime()
    assert not [operation for operation in harness.operations if set(operation) & set(_PLANE_SERVICES)]
    assert runtime_topology().shared_dagster_slots == ()


@pytest.mark.parametrize("target", sorted(_FLIP_CASES))
def test_a_shared_target_is_on_the_plane_before_the_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """smoke(PinVi `/admin/etl/summary`, Map `/v1/ops/pipeline/*`)는 공용 webserver에 자기 location을 묻는다 —
    그 전에 carrier가 떠 있고 plane이 `up --wait`로 맞춰져 있다. 그 직전에 retired 컨테이너를 다시 본다."""

    carrier, legacy, companions = _FLIP_CASES[target]
    harness = _forward_harness(monkeypatch, tmp_path, flipped=(target,), companions=companions)
    harness.mocks.smoke.side_effect = lambda *_args, **_kwargs: harness.operations.append(("smoke",))

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "deployed"
    operations = harness.operations
    smoke = operations.index(("smoke",))
    plane = _plane_ups(operations)
    assert len(plane) == 1 and plane[0] < smoke, operations
    # `--wait`는 쓰지 않는다 — plane의 healthcheck는 다른 테넌트까지 본다(H1). 이 target의 location만 따로 본다.
    assert "--no-deps" in operations[plane[0]] and "--wait" not in operations[plane[0]]
    carrier_ups = [
        index for index, op in enumerate(operations) if op[:1] == ("up",) and carrier in op
    ]
    assert carrier_ups and carrier_ups[0] < plane[0]
    retired = {runtime_topology_module.installed_container_name(name) for name in legacy}
    between = {op[1] for op in operations[carrier_ups[0] : plane[0]] if op[:1] == ("container-inspect",)}
    assert retired <= between, (retired, between)


@pytest.mark.parametrize("target", sorted(_FLIP_CASES))
def test_a_retired_daemon_revived_mid_rebuild_keeps_the_plane_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """재구축 앞의 판정 뒤에 옛 daemon이 되살아나면(대시보드의 start) plane을 건드리기 직전에 거부한다."""

    _, legacy, companions = _FLIP_CASES[target]
    harness = _forward_harness(monkeypatch, tmp_path, flipped=(target,), companions=companions)
    revived = runtime_topology_module.installed_container_name(legacy[-1])
    harness.mocks.pinvi_bootstrap.side_effect = (
        lambda *_args, **_kwargs: cast(set[str], harness.live["running_containers"]).add(revived)
    )

    with pytest.raises(DeploymentContractError, match="double fire"):
        harness.service.rebuild_pinned_runtime()

    assert _plane_ups(harness.operations) == []
    harness.mocks.smoke.assert_not_called()


def test_both_targets_shared_bring_both_carriers_up_before_the_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path, flipped=("map", "pinvi"), companions={})
    harness.mocks.smoke.side_effect = lambda *_args, **_kwargs: harness.operations.append(("smoke",))

    harness.service.rebuild_pinned_runtime()

    operations = harness.operations
    plane = _plane_ups(operations)
    assert len(plane) == 1 and plane[0] < operations.index(("smoke",))
    for carrier in ("kor-travel-map-dagster-code-server", "pinvi-dagster-code-server"):
        assert any(
            op[:1] == ("up",) and carrier in op for op in operations[: plane[0]]
        ), (carrier, operations)


@pytest.mark.parametrize("target", sorted(_FLIP_CASES))
def test_a_same_pair_converge_also_converges_the_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    _, _, companions = _FLIP_CASES[target]
    first = _forward_harness(monkeypatch, tmp_path / "first", flipped=(target,), companions=companions)
    first.service.rebuild_pinned_runtime()
    status = read_deploy_status(first.status_path)
    harness = _forward_harness(
        monkeypatch, tmp_path / "second", previous=status, flipped=(target,), companions=companions
    )

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "converged"
    assert len(_plane_ups(harness.operations)) == 1


def test_the_plane_up_is_a_frozen_rebuild_mutation() -> None:
    """R3 분류: plane `up --no-deps`는 서비스를 명시하고 --no-deps를 싣는다(재구축 runner가 받는 모양)."""

    arguments = ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "600", *_PLANE_SERVICES]
    scope, flags = ComposeService._parse_compose_mutation(arguments)
    assert list(scope) == list(_PLANE_SERVICES)
    assert "--no-deps" in flags


class _PlaneClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize("target", sorted(_FLIP_CASES))
def test_another_tenants_broken_location_does_not_block_the_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """H1: plane은 이 target의 location만 기다린다 — geo·weather의 code-server가 내려가 있어도 배포는 선다."""

    _, _, companions = _FLIP_CASES[target]
    harness = _forward_harness(monkeypatch, tmp_path, flipped=(target,), companions=companions)
    clock = _PlaneClock()
    monkeypatch.setattr(compose_service_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(compose_service_module.time, "sleep", clock.sleep)
    loaded = dict(harness.live["plane_loaded"])
    others = [location for location in loaded if not location.startswith(("kortravelmap", "pinvi"))]
    assert others, loaded
    for location in others:
        loaded[location] = "PythonError"
    harness.live["plane_loaded"] = loaded

    assert harness.service.rebuild_pinned_runtime()["outcome"] == "deployed"


@pytest.mark.parametrize("target", sorted(_FLIP_CASES))
def test_the_plane_that_never_loads_this_location_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    _, _, companions = _FLIP_CASES[target]
    harness = _forward_harness(monkeypatch, tmp_path, flipped=(target,), companions=companions)
    clock = _PlaneClock()
    monkeypatch.setattr(compose_service_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(compose_service_module.time, "sleep", clock.sleep)
    location = runtime_topology_module.installed_code_location(
        runtime_topology().families[target]
    )
    harness.live["plane_loaded"] = {
        **harness.live["plane_loaded"],
        location: "RepositoryLocationLoadFailure",
    }

    with pytest.raises(DeploymentContractError, match="did not load the shared pinned locations"):
        harness.service.rebuild_pinned_runtime()

    assert clock.now >= compose_service_module._SHARED_PLANE_PROBE_TIMEOUT_SECONDS
    harness.mocks.smoke.assert_not_called()


def test_a_stopped_shared_daemon_fails_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path, flipped=("map",), companions=_FLIP_CASES["map"][2])
    clock = _PlaneClock()
    monkeypatch.setattr(compose_service_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(compose_service_module.time, "sleep", clock.sleep)
    harness.live["plane_running"] = False

    with pytest.raises(DeploymentContractError, match="daemon running False"):
        harness.service.rebuild_pinned_runtime()


@pytest.mark.parametrize("target", sorted(_FLIP_CASES))
def test_a_plane_already_like_the_render_is_not_recreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """M3: 떠 있는 daemon·webserver가 frozen render가 만들 컨테이너와 같으면 `up`하지 않고 location만 본다."""

    _, _, companions = _FLIP_CASES[target]
    harness = _forward_harness(monkeypatch, tmp_path, flipped=(target,), companions=companions)
    harness.live["plane_current"] = True
    # render는 compose의 `$$` escape를 싣고(storage 가드의 `exec "$$@"`), 컨테이너는 Docker가 푼 `$`를 싣는다 —
    # 비교가 그것을 풀지 않으면 매번 다르다(재리뷰 HIGH).
    assert any("$$" in str(word) for word in harness.plane_resolved[harness.plane.daemon]["command"])

    assert harness.service.rebuild_pinned_runtime()["outcome"] == "deployed"
    assert _plane_ups(harness.operations) == []


def _plane_env_key(harness: SimpleNamespace, suffix: str) -> tuple[str, str]:
    for name, service in harness.plane_resolved.items():
        for key in (service.get("environment") or {}):
            if str(key).endswith(suffix):
                return name, str(key)
    raise AssertionError(suffix)


@pytest.mark.parametrize(
    "drift",
    ["image", "shared_url", "heartbeat", "digest", "command", "entrypoint"],
)
def test_a_plane_that_differs_from_the_render_is_recreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    """리뷰 MED: digest env만 같다고 넘기지 않는다 — 이미지(Dagster 올림)·공용 URL 앵커·heartbeat tolerance·command가
    다르거나, 재시작 중이거나 없으면 다시 만든다."""

    harness = _forward_harness(monkeypatch, tmp_path, flipped=("map",), companions=_FLIP_CASES["map"][2])
    harness.live["plane_current"] = True
    daemon = harness.plane.daemon
    if drift == "image":
        harness.live["plane_overrides"] = {daemon: {"image_id": "sha256:" + "d" * 64}}
    elif drift in {"shared_url", "heartbeat", "digest"}:
        suffix = {
            "shared_url": "_SHARED_PG_URL",
            "heartbeat": "HEARTBEAT_TOLERANCE",
            "digest": "WORKSPACE_DIGEST",
        }[drift]
        name, key = _plane_env_key(harness, suffix)
        env = {
            str(k): str(v)
            for k, v in (harness.plane_resolved[name].get("environment") or {}).items()
            if v is not None
        }
        harness.live["plane_overrides"] = {name: {"env": {**env, key: "stale"}}}
    elif drift == "command":
        harness.live["plane_overrides"] = {daemon: {"cmd": ["dagster-daemon", "run"]}}
    elif drift == "entrypoint":
        harness.live["plane_overrides"] = {daemon: {"entrypoint": ["/stale"]}}
        harness.plane_resolved[daemon]["entrypoint"] = ["/bin/sh"]

    harness.service.rebuild_pinned_runtime()

    assert len(_plane_ups(harness.operations)) == 1


def test_a_restarting_shared_daemon_fails_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L3: daemon이 돌기만 하는 것이 아니라 재시작 중이 아니어야 한다."""

    harness = _forward_harness(monkeypatch, tmp_path, flipped=("map",), companions=_FLIP_CASES["map"][2])
    clock = _PlaneClock()
    monkeypatch.setattr(compose_service_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(compose_service_module.time, "sleep", clock.sleep)
    harness.live["plane_restarting"] = True

    with pytest.raises(DeploymentContractError, match="daemon running False"):
        harness.service.rebuild_pinned_runtime()


def test_a_workspace_location_whose_own_daemon_runs_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1: plane이 실을 workspace의 **모든** location에 한 규칙 — 그 target의 plane 밖 daemon이 돌면 거부한다."""

    harness = _forward_harness(monkeypatch, tmp_path, flipped=("map",), companions=_FLIP_CASES["map"][2])
    owners = runtime_topology_module.installed_location_owners()
    other = next(
        family
        for location, family in owners.items()
        if location in harness.live["workspace_locations"] and family.target not in {"map", "pinvi"}
    )
    running = other.container_name(other.daemon)
    harness.mocks.pinvi_bootstrap.side_effect = (
        lambda *_args, **_kwargs: cast(set[str], harness.live["running_containers"]).add(running)
    )

    with pytest.raises(DeploymentContractError, match=f"double fire.*{other.daemon}"):
        harness.service.rebuild_pinned_runtime()
    assert _plane_ups(harness.operations) == []


def test_a_workspace_location_no_target_serves_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path, flipped=("map",), companions=_FLIP_CASES["map"][2])
    harness.live["workspace_locations"] = (*harness.live["workspace_locations"], "stranger.definitions")

    with pytest.raises(DeploymentContractError, match="stranger.definitions"):
        harness.service.rebuild_pinned_runtime()
    assert _plane_ups(harness.operations) == []


@pytest.mark.parametrize("where", ["workspace", "running"])
def test_an_own_target_still_on_the_plane_is_refused_before_anything_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """M2: 스위치가 `own`인데 plane이 그 location을 싣고 있으면(설치본 workspace, 또는 떠 있는 webserver) own daemon을
    띄우기 전에 — 무엇을 멈추기도 전에 — 거부한다."""

    harness = _forward_harness(monkeypatch, tmp_path)
    location = runtime_topology_module.installed_code_location(runtime_topology().families["pinvi"])
    if where == "workspace":
        harness.live["workspace_locations"] = (*harness.live["workspace_locations"], location)
    else:
        harness.live["plane_loaded"] = {**harness.live["plane_loaded"], location: "RepositoryLocation"}

    with pytest.raises(DeploymentContractError, match="own target's location"):
        harness.service.rebuild_pinned_runtime()
    assert [op for op in harness.operations if op[0] not in {"container-inspect"}] == []


@pytest.mark.parametrize(("daemon_running", "refused"), [(True, True), (False, False)])
def test_an_unreachable_plane_webserver_blocks_only_while_its_daemon_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, daemon_running: bool, refused: bool
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.live["plane_loaded"] = None
    harness.live["plane_running"] = daemon_running
    if refused:
        with pytest.raises(DeploymentContractError, match="cannot be asked"):
            harness.service.rebuild_pinned_runtime()
    else:
        assert harness.service.rebuild_pinned_runtime()["outcome"] == "deployed"


def test_a_frozen_plane_that_differs_from_the_installed_release_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L4: plane은 frozen render에서 파생하고, 설치된 release의 것과 다르면 거부한다."""

    harness = _forward_harness(monkeypatch, tmp_path, flipped=("map",), companions=_FLIP_CASES["map"][2])
    resolved = harness.transaction.resolved["services"]
    resolved["kor-travel-dagster-webserver-renamed"] = resolved.pop("kor-travel-dagster-webserver")

    with pytest.raises(DeploymentContractError, match="differs from the installed release"):
        harness.service.rebuild_pinned_runtime()
