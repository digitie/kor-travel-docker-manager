from __future__ import annotations

import fcntl
import inspect
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
import yaml

from kor_travel_docker_manager.services import c6c_deployment, runtime_pin_registry
from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services import (
    pinned_runtime_rebuild as pinned_runtime_rebuild_module,
)
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
    RUNTIME_SERVICES,
    PinnedRuntimeGeneration,
    PinnedRuntimeManifest,
    RuntimeService,
    manifest_from_payload,
    pinned_runtime_state_paths,
    write_manifest,
)
from kor_travel_docker_manager.services.pinned_runtime_rebuild import (
    COMPOSE_BUILT_RUNTIME_SERVICES,
    CandidateRuntimeBuild,
    MapApplication300ArtifactDirectories,
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
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    MaterializedRuntimeSource,
    PinnedRuntimeSourceMaterialization,
)

PINNED_RUNTIME_RELEASE = current_pinned_runtime_release()
_WAIT_TIMEOUT = str(compose_service_module._COMPOSE_WAIT_TIMEOUT_SECONDS)

_real_map_application_300_paths = compose_service_module._map_application_300_paths


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
    tmp_path: Path,
) -> None:
    """root 전용 host primitive는 별도 회귀 외에는 unit orchestration에서 격리한다."""

    monkeypatch.setattr(
        compose_service_module,
        "pinned_runtime_rebuild_lock",
        lambda: nullcontext(),
    )
    # 이 모듈의 대형 orchestration fixture는 각 사례가 필요한 v5 source release를
    # 직접 주입한다. 실제 root registry/trusted Manager v6 snapshot은 만들지 않으므로
    # admission 판정은 여기서만 격리한다. 판정 자체는 ``test_runtime_pin_registry``가 소유한다.
    monkeypatch.setattr(
        compose_service_module,
        "_pinned_runtime_admission_warnings",
        lambda _pinset_sha256: [],
    )
    base = tmp_path / "application-300"
    monkeypatch.setattr(
        compose_service_module,
        "_map_application_300_paths",
        lambda *, state_root, pinset_sha256: compose_service_module._MapApplication300Paths(
            api_receipt=base / "receipts" / "api.json",
            paired_receipt=base / "receipts" / "paired.json",
            metadata_permit_directory=base / "dagster-storage-permit",
        ),
    )
    # 각 orchestration 회귀는 그 이전/이후 phase만 격리한다. root `.env`를 실제로
    # 바꾸는 fresh role credential 초기화는 전용 unit suite가 소유한다. admission과
    # frozen snapshot 전달 순서는 production과 동일하게 유지한다.
    @contextmanager
    def isolated_rebuild_environment_lock(*, prewrite_admission: Any) -> Any:
        with compose_service_module.pinned_runtime_rebuild_lock():
            snapshot = compose_service_module._capture_compose_environment_snapshot(
                environment_override=None
            )
            # M05 폐기 전에는 여기서 role 자격증명이 이미 구성된 상태를 흉내 냈다.
            # 이제 rebuild가 `.env`에 자격증명을 쓰지 않으므로 흉내 낼 것이 없다.
            prewrite_admission(snapshot)
            with compose_service_module.c6c_deployment_lock_from_environment() as lock:
                yield lock, snapshot, False

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
    return object()


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


def _paired_builder_inputs(
    tmp_path: Path,
) -> tuple[
    PinnedRuntimeSourceMaterialization,
    compose_service_module._MapApplication300Paths,
]:
    map_root = tmp_path / "map"
    script = map_root / "scripts" / "build-application-300-paired-candidate.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    receipt_directory = tmp_path / "receipts"
    receipt_directory.mkdir(mode=0o700)
    paths = compose_service_module._MapApplication300Paths(
        api_receipt=receipt_directory / "api.json",
        paired_receipt=receipt_directory / "paired.json",
        metadata_permit_directory=tmp_path / "dagster-storage-permit",
    )
    release = PINNED_RUNTIME_RELEASE
    return (
        PinnedRuntimeSourceMaterialization(
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
        ),
        paths,
    )


def _map_application_candidate(
    sources: PinnedRuntimeSourceMaterialization | None = None,
    *,
    api_image_id: str = f"sha256:{101:064x}",
    dagster_image_id: str = f"sha256:{102:064x}",
    postgres_image_id: str = f"sha256:{103:064x}",
) -> MapApplicationCandidate:
    materialized = sources or _sources()
    return MapApplicationCandidate(
        candidate_commit=materialized.source_for("map").revision,
        candidate_git_tree=materialized.source_for("map").tree,
        api_image_id=api_image_id,
        dagster_image_id=dagster_image_id,
        postgres_image_id=postgres_image_id,
        dagster_config_sha256="b" * 64,
        application_head="300",
    )


def _candidate_image_ids(
    candidate: MapApplicationCandidate,
) -> dict[RuntimeService, str]:
    image_ids: dict[RuntimeService, str] = {
        service: f"sha256:{index + 1:064x}"
        for index, service in enumerate(RUNTIME_SERVICES)
    }
    image_ids["kor-travel-map-api"] = candidate.api_image_id
    image_ids["kor-travel-map-dagster"] = candidate.dagster_image_id
    image_ids["kor-travel-map-dagster-daemon"] = candidate.dagster_image_id
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
        map_dagster_head="map-dagster-head",
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
    assert set(build.image_names) == set(COMPOSE_BUILT_RUNTIME_SERVICES)
    assert set(paired_build_names) == {
        "kor-travel-map-api",
        "kor-travel-map-dagster",
    }
    assert set(paired_build_names).isdisjoint(build.image_names)
    assert all(
        image.endswith(PINNED_RUNTIME_RELEASE.pinset_sha256)
        and image.startswith("kor-travel-docker-manager/pinned-runtime-candidate-v6/")
        for image in build.image_names.values()
    )
    assert set(build.runtime_image_references) == set(RUNTIME_SERVICES)
    assert build.runtime_image_references["kor-travel-map-api"] == candidate.api_image_id
    assert (
        build.runtime_image_references["kor-travel-map-dagster"]
        == build.runtime_image_references["kor-travel-map-dagster-daemon"]
        == candidate.dagster_image_id
    )
    assert environment["KOR_TRAVEL_MAP_API_IMAGE"] == candidate.api_image_id
    assert environment["KOR_TRAVEL_MAP_DAGSTER_IMAGE"] == candidate.dagster_image_id
    assert "KOR_TRAVEL_MAP_DAGSTER_DAEMON_IMAGE" not in environment
    assert environment["KOR_TRAVEL_MAP_POSTGRES_IMAGE_ID"] == candidate.postgres_image_id
    assert environment["KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256"] == (
        candidate.dagster_config_sha256
    )


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
        map_dagster_head="dagster_storage_1",
        pinvi_head="20260806_0001",
        recorded_at="2026-08-06T00:00:00+00:00",
    )

    assert generation.map_application_head == "300"
    assert generation.map_source_revision == paired.candidate_commit
    assert generation.map_application_300_candidate_evidence.candidate_git_tree == (
        paired.candidate_git_tree
    )
    assert manifest_from_payload(
        PinnedRuntimeManifest(version=6, active_generation=generation).to_payload()
    ).active_generation == generation

    artifact_directories = MapApplication300ArtifactDirectories(
        dagster_storage_permit=Path("/state/metadata-permit"),
    )
    runtime_environment = generation_compose_environment(
        generation,
        artifact_directories=artifact_directories,
    )

    assert runtime_environment["PINVI_DAGSTER_IMAGE"] == generation.pinvi_dagster_image_id
    assert runtime_environment["KOR_TRAVEL_MAP_API_IMAGE"] == paired.api_image_id
    assert runtime_environment["KOR_TRAVEL_MAP_DAGSTER_IMAGE"] == paired.dagster_image_id
    assert "KOR_TRAVEL_MAP_DAGSTER_DAEMON_IMAGE" not in runtime_environment
    assert runtime_environment["KOR_TRAVEL_MAP_POSTGRES_IMAGE_ID"] == (
        paired.postgres_image_id
    )
    assert runtime_environment["KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256"] == (
        paired.dagster_config_sha256
    )
    assert runtime_environment[
        "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR"
    ] == "/state/metadata-permit"


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
            image_ids={**image_ids, "kor-travel-map-api": f"sha256:{999:064x}"},
                map_dagster_head="dagster_storage_1",
            pinvi_head="20260806_0001",
        )

    with pytest.raises(DeploymentContractError, match="web and daemon"):
        build_candidate_generation(
            sources=sources,
            map_application_candidate=paired,
            image_ids={
                **image_ids,
                "kor-travel-map-dagster-daemon": f"sha256:{998:064x}",
            },
                map_dagster_head="dagster_storage_1",
            pinvi_head="20260806_0001",
        )


def test_rebuild_requires_root_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    with pytest.raises(DeploymentContractError, match="requires root execution"):
        ComposeService().rebuild_pinned_runtime()


def test_application_300_paths_separate_private_and_read_only_mount_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """appuser mount 하나만 0755이고 영수증 디렉터리와 부모는 계속 0700이다.

    ADR-101 이전에는 mount가 넷이었다 — fence 둘, application final permit,
    storage permit. 앞의 셋은 읽던 코드가 사라져 함께 지웠다. 결과 디렉터리도
    영수증 사이드카를 담던 자리라 없다.
    """

    original_lstat = Path.lstat

    def root_owned_lstat(path: Path) -> os.stat_result:
        metadata = original_lstat(path)
        fields = list(metadata)
        fields[4] = 0
        return os.stat_result(fields)

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(Path, "lstat", root_owned_lstat)

    paths = _real_map_application_300_paths(
        state_root=tmp_path,
        pinset_sha256="a" * 64,
    )

    mount_directories = (paths.metadata_permit_directory,)
    private_directories = {
        paths.api_receipt.parent,
        paths.api_receipt.parent.parent,
    }
    assert all(directory.stat().st_mode & 0o777 == 0o755 for directory in mount_directories)
    assert all(directory.stat().st_mode & 0o777 == 0o700 for directory in private_directories)
    assert all(directory.lstat().st_uid == 0 for directory in (*mount_directories, *private_directories))


def test_application_300_paths_reject_a_symlinked_private_directory(
    tmp_path: Path,
) -> None:
    receipt_parent = tmp_path / "map-application-300-candidate"
    receipt_parent.mkdir(mode=0o700)
    receipt_parent.chmod(0o700)
    target = tmp_path / "redirected-receipts"
    target.mkdir(mode=0o700)
    target.chmod(0o700)
    (receipt_parent / ("b" * 64)).symlink_to(target, target_is_directory=True)

    with pytest.raises(DeploymentContractError, match="state directory is unsafe"):
        _real_map_application_300_paths(
            state_root=tmp_path,
            pinset_sha256="b" * 64,
        )


def test_map_application_300_python_base_images_pull_and_reinspect_missing_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, _ = _paired_builder_inputs(tmp_path)
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
        assert invocation.kwargs["stderr"] is subprocess.DEVNULL


def test_map_application_300_python_base_images_reject_invalid_source_contract(
    tmp_path: Path,
) -> None:
    sources, _ = _paired_builder_inputs(tmp_path)
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
    sources, _ = _paired_builder_inputs(tmp_path)
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


def test_application_300_mount_directory_rejects_nonroot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    with pytest.raises(DeploymentContractError, match="requires root"):
        compose_service_module._ensure_application_300_mount_directory(
            tmp_path / "mount"
        )


def test_runtime_container_image_mismatch_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    candidate = _candidate_generation()
    records = [
        {"Service": runtime_service, "Name": f"container-{runtime_service}"}
        for runtime_service in RUNTIME_SERVICES
    ]
    observed = dict(candidate.image_ids)
    observed["pinvi-web"] = f"sha256:{999:064x}"
    monkeypatch.setattr(
        service,
        "_inspect_container_image_id",
        lambda container_name, *, label: observed[cast(RuntimeService, label)],
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
    companions: dict[str, RuntimeService] = {
        "kor-travel-map-dagster-code-server": "kor-travel-map-dagster",
        "pinvi-dagster-daemon": "pinvi-dagster",
    }
    records = [
        {"Service": name, "Name": f"container-{name}"}
        for name in (*RUNTIME_SERVICES, *companions)
    ]
    slot_images = candidate.image_ids
    observed: dict[str, str] = {
        **slot_images,
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
    dagster_image = image_ids["kor-travel-map-dagster"]
    resolved = {
        "services": {
            **{slot: {"image": image_ids[slot]} for slot in RUNTIME_SERVICES},
            "kor-travel-map-dagster-code-server": {"image": dagster_image},
            "kor-travel-map-dagster-storage-migrate": {"image": dagster_image},
            "pinvi-dagster-daemon": {"image": image_ids["pinvi-dagster"]},
            "prometheus": {"image": "prom/prometheus:v2.53.1"},
            "kor-travel-map-postgres": {"image": image_ids["kor-travel-map-api"] + "x"},
        }
    }

    companions = generation_companion_services(
        resolved,
        image_ids,
        excluded_services=("kor-travel-map-dagster-storage-migrate",),
    )

    # daemon과 dagster가 같은 이미지여도 owner는 RUNTIME_SERVICES 순서상 먼저인 slot이다.
    assert dict(companions) == {
        "kor-travel-map-dagster-code-server": "kor-travel-map-dagster",
        "pinvi-dagster-daemon": "pinvi-dagster",
    }
    with pytest.raises(DeploymentContractError, match="services are invalid"):
        generation_companion_services({"services": []}, image_ids, excluded_services=())


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
    )

    assert dict(companions) == {
        "kor-travel-map-dagster-code-server": "kor-travel-map-dagster",
        "pinvi-dagster-code-server": "pinvi-dagster",
        "pinvi-dagster-daemon": "pinvi-dagster",
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
        "pinned_runtime_rebuild_lock",
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

    @contextmanager
    def environment_lease() -> Any:
        lock_events.append("environment-enter")
        try:
            yield object()
        finally:
            lock_events.append("environment-exit")

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        environment_lease,
    )
    monkeypatch.setattr(
        compose_service_module,
        "pinned_runtime_rebuild_lock",
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

    with pytest.raises(compose_service_module.PinnedRuntimePrejournalFailure) as captured:
        ComposeService().rebuild_pinned_runtime()

    assert captured.value.stage == "state_initialization"
    assert isinstance(captured.value.__cause__, DeploymentContractError)

    assert lock_events == [
        "host-enter",
        "environment-enter",
        "environment-exit",
        "host-exit",
    ]
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
            stdout="",
            stderr="candidate failed",
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

    assert str(captured.value) == "compose candidate resolution failed"
    assert "candidate failed" not in str(captured.value)


def test_prebuild_compose_resolution_overrides_blank_artifact_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """빈 ambient artifact 값도 frozen prebuild override로 실제 해석한다."""

    names = (
        "KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR",
        "KOR_TRAVEL_MAP_APPLICATION_FRESH_FINALIZE_FENCE_DIR",
        "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DIR",
        "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR",
    )
    artifact_root = tmp_path / "prebuild-artifacts"
    overrides = {
        name: str(artifact_root / directory)
        for name, directory in zip(
            names,
            (
                "fresh-root-fence",
                "fresh-finalize-fence",
                "application-final-permit",
                "dagster-storage-permit",
            ),
            strict=True,
        )
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
            "prebuild-probe": {
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

    volumes = resolved["services"]["prebuild-probe"]["volumes"]
    assert [volume["source"] for volume in volumes] == list(overrides.values())
    assert [volume["target"] for volume in volumes] == [
        f"/artifact-{index}" for index in range(len(names))
    ]
    assert all(volume["read_only"] is True for volume in volumes)


def test_rebuild_compose_error_names_the_failed_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-compose-output-token-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 23,
                "stdout": secret,
                "stderr": (
                    secret
                    + "\n"
                    + '{"code":"dagster_instance_migrate_failed",'
                    + '"schema":"kor-travel-map.dagster-storage-migration-error.v1"}'
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 23; dagster_instance_migrate_failed\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


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


def test_rebuild_never_retries_a_failed_dagster_storage_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    run = Mock(
        return_value={"success": False, "returncode": 1, "stdout": "", "stderr": ""}
    )
    monkeypatch.setattr(service, "_run_frozen_recovery", run)

    with pytest.raises(DeploymentContractError, match="Compose run command failed"):
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=_opaque_transaction(),
        )

    run.assert_called_once()


def test_rebuild_compose_runner_has_no_retryable_argument() -> None:
    parameters = inspect.signature(
        ComposeService._run_pinned_runtime_rebuild_compose
    ).parameters

    assert "retryable" not in parameters


def test_rebuild_one_shot_failure_exposes_only_allowlisted_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-retry-output-must-not-leak"
    run = Mock(
        return_value={
            "success": False,
            "returncode": 1,
            "stdout": secret,
            "stderr": (
                secret
                + "\n"
                + '{"code":"dagster_instance_migrate_failed",'
                + '"schema":"kor-travel-map.dagster-storage-migration-error.v1"}'
            ),
        }
    )
    monkeypatch.setattr(service, "_run_frozen_recovery", run)

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1; dagster_instance_migrate_failed\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=_opaque_transaction(),
        )

    run.assert_called_once()
    assert secret not in str(captured.value)


def test_rebuild_compose_error_ignores_malformed_diagnostic_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-malformed-diagnostic-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 23,
                "stdout": secret,
                "stderr": json.dumps(
                    {
                        "code": ["dagster_instance_migrate_failed"],
                        "schema": "kor-travel-map.dagster-storage-migration-error.v1",
                    }
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 23\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


@pytest.mark.parametrize(
    ("code", "exposed"),
    (
        # 옛 닫힌 목록에 없던 코드 — Map이 코드를 더해도 원인이 보여야 한다.
        ("dagster_storage_permit_unavailable", True),
        ("postgres://user:secret@host/db", False),
        ("Dagster_Instance_Failed", False),
        ("x" * 65, False),
    ),
)
def test_rebuild_compose_error_exposes_map_storage_codes_by_shape(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    exposed: bool,
) -> None:
    service = ComposeService()
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": "",
                "stderr": json.dumps(
                    {
                        "code": code,
                        "schema": "kor-travel-map.dagster-storage-migration-error.v1",
                    }
                ),
            }
        ),
    )

    with pytest.raises(DeploymentContractError) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=_opaque_transaction(),
        )

    message = str(captured.value)
    assert (f"; {code})" in message) is exposed
    if not exposed:
        assert code not in message


def test_rebuild_compose_error_exposes_only_allowlisted_pinvi_bootstrap_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-pinvi-bootstrap-diagnostic-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": (
                    secret
                    + "\n"
                    + 'pinvi-admin-bootstrap-1  | {"error_code":"credential_file_owner_mismatch",'
                    + '"phase":"credential_file"}'
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1; pinvi:credential_file_owner_mismatch\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            [
                "--profile",
                "bootstrap",
                "run",
                "--rm",
                "--no-deps",
                "-v",
                "/run/manager/credential.json:/run/pinvi/bootstrap-admin.json:ro",
                "-e",
                "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE=/run/pinvi/bootstrap-admin.json",
                "pinvi-admin-bootstrap",
            ],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


@pytest.mark.parametrize(
    "stderr",
    (
        '{"error_code":"credential_file_owner_mismatch",'
        '"phase":"credential_file","extra":"ignored"}',
        '{"code":"dagster_instance_migrate_failed",'
        '"schema":"kor-travel-map.dagster-storage-migration-error.v1"}',
        '{"error_code":"credential_file_owner_mismatch",'
        '"error_code":"internal_error","phase":"runtime"}',
    ),
)
def test_rebuild_compose_error_rejects_noncanonical_pinvi_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
) -> None:
    service = ComposeService()
    secret = "test-pinvi-noncanonical-diagnostic-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": stderr,
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "pinvi-admin-bootstrap"],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


@pytest.mark.parametrize(
    "stderr",
    (
        'untrusted-log | {"error_code":"credential_file_owner_mismatch",'
        '"phase":"credential_file"}',
        '123 | {"error_code":"credential_file_owner_mismatch",'
        '"phase":"credential_file"}',
        'pinvi-admin-bootstrap-run | {"error_code":"credential_file_owner_mismatch",'
        '"phase":"credential_file"}',
        'kor-travel-map-dagster-storage-migrate-1 | '
        '{"error_code":"credential_file_owner_mismatch","phase":"credential_file"}',
        'pinvi-admin-bootstrap-1 | {"error_code":"credential_file_owner_mismatch",'
        '"error_code":"internal_error","phase":"runtime"}',
    ),
)
def test_rebuild_compose_error_rejects_untrusted_pinvi_compose_prefix(
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
) -> None:
    service = ComposeService()
    secret = "test-pinvi-prefix-spoof-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": stderr,
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "pinvi-admin-bootstrap"],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


def test_rebuild_compose_error_accepts_map_compose_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": "",
                "stderr": (
                    'kor-travel-map-dagster-storage-migrate-1 | '
                    '{"code":"dagster_instance_migrate_failed",'
                    '"schema":"kor-travel-map.dagster-storage-migration-error.v1"}'
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1; dagster_instance_migrate_failed\)",
    ):
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=_opaque_transaction(),
        )


def test_rebuild_compose_error_rejects_pinvi_payload_for_map_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-map-cross-payload-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": json.dumps(
                    {
                        "error_code": "credential_file_owner_mismatch",
                        "phase": "credential_file",
                    }
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate"],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


def test_rebuild_compose_error_ignores_pinvi_code_with_wrong_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-pinvi-malformed-diagnostic-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": json.dumps(
                    {
                        "error_code": "credential_file_owner_mismatch",
                        "phase": "runtime",
                    }
                ),
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "pinvi-admin-bootstrap"],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


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
        label="Map Dagster",
        entrypoint="/usr/local/bin/ktm-dagster-storage",
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
        "/usr/local/bin/ktm-dagster-storage",
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
        "pinvi-db-init",
        "kor-travel-map-dagster-db-init",
        "kor-travel-map-db-role-bootstrap",
        "kor-travel-map-application-schema",
        "kor-travel-map-dagster-storage-migrate",
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


def test_pinned_runtime_rebuild_lease_path_is_fixed() -> None:
    assert c6c_deployment.pinned_runtime_rebuild_lock_path() == (
        "/run/lock/kor-travel-docker-manager/pinned-runtime-rebuild.lock"
    )


def test_pinned_runtime_rebuild_lease_uses_real_nonblocking_flock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "pinned-runtime-rebuild.lock"
    monkeypatch.setattr(c6c_deployment, "_PINNED_RUNTIME_REBUILD_LOCK", lock_path)
    monkeypatch.setattr(c6c_deployment, "_require_pinned_runtime_rebuild_root", lambda: None)
    # NTFS drvfs는 mode를 0777로 보이게 한다. 이 회귀의 대상은 mode 정책이 아니라
    # second holder가 실제 flock을 얻지 못하는지다.
    monkeypatch.setattr(c6c_deployment, "_validate_c6c_lock_fd", lambda *_args, **_kwargs: None)
    original_lock = c6c_deployment.c6c_deployment_lock

    @contextmanager
    def lock_without_global(path: str):
        if path == str(c6c_deployment._C6C_GLOBAL_MUTATION_LOCK):
            yield
        else:
            with original_lock(path):
                yield

    monkeypatch.setattr(c6c_deployment, "c6c_deployment_lock", lock_without_global)
    holder = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(c6c_deployment.DeploymentContractError) as excinfo:
            with c6c_deployment.pinned_runtime_rebuild_lock():
                pass  # pragma: no cover - contended lock must not enter.
    finally:
        os.close(holder)

    assert str(excinfo.value) == "another C6c compatible-pair operation is already active"


def test_pinned_runtime_rebuild_lease_acquires_global_before_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """release/v6 snapshot과 rotate를 같은 lease ordering으로 직렬화한다."""

    acquired: list[str] = []
    monkeypatch.setattr(c6c_deployment, "_require_pinned_runtime_rebuild_root", lambda: None)

    @contextmanager
    def record_lock(path: str):
        acquired.append(path)
        yield

    monkeypatch.setattr(c6c_deployment, "c6c_deployment_lock", record_lock)

    with c6c_deployment.pinned_runtime_rebuild_lock():
        pass

    assert acquired == [
        str(c6c_deployment._C6C_GLOBAL_MUTATION_LOCK),
        c6c_deployment.pinned_runtime_rebuild_lock_path(),
    ]


def test_pinned_runtime_rebuild_lease_rejects_nonroot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(c6c_deployment.os, "geteuid", lambda: 1000)

    with pytest.raises(c6c_deployment.DeploymentContractError, match="requires root"):
        with c6c_deployment.pinned_runtime_rebuild_lock():
            pass  # pragma: no cover - root gate must reject before entering.


def test_journal_watermark_reports_unreached_without_a_path() -> None:
    """경로를 모른 채 닫혔다면 그 실행은 journal에 도달한 적이 없다.

    host lease 경합·root 아님·lifecycle 게이트 거부가 전부 여기다 — 종전에는
    `unclassified`로 접혀 흔한 lock 경합 한 번이 회전 사이클 1회를 태웠다.
    """

    assert compose_service_module._PinnedRuntimeJournalWatermark().reached() is False


def test_rebuild_timeouts_outlast_a_saturated_disk() -> None:
    """타임아웃은 멈춤 감지용이다 — 느린 디스크에서 정상 명령을 죽이면 안 된다.

    n150 실측(2026-09-26, IO 압력 full 50~60%): `docker run /bin/true` 112초,
    static head 74초. Map은 code-server → webserver → daemon을 직렬로 띄운다.
    """

    per_container = 112 + 74
    module = compose_service_module
    assert module._PINNED_RUNTIME_STATIC_INSPECTION_TIMEOUT_SECONDS >= 2 * per_container
    assert module._ALEMBIC_HEAD_INSPECTION_TIMEOUT_SECONDS >= 2 * per_container
    assert module._COMPOSE_WAIT_TIMEOUT_SECONDS >= 3 * per_container


# ── 마이그레이션 전진 배포(ADR-51 B2) ──────────────────────────────────────────

_FORWARD_COMPANIONS: dict[str, RuntimeService] = {
    "kor-travel-map-dagster-code-server": "kor-travel-map-dagster",
    "pinvi-dagster-code-server": "pinvi-dagster",
    "pinvi-dagster-daemon": "pinvi-dagster",
}
_FORWARD_ONESHOTS = (
    "kor-travel-map-dagster-storage-migrate",
    "kor-travel-map-application-schema",
    "pinvi-admin-bootstrap",
    "kor-travel-map-db-role-bootstrap",
)
_STORAGE_RUN = ("run", "--rm", "--no-deps", "kor-travel-map-dagster-storage-migrate")
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
    "map_dagster": ("kor_travel_map_dagster", 16402, "7300000000000000001"),
    "pinvi": ("pinvi", 20001, "7300000000000000002"),
}


def _forward_runtimes() -> tuple[DatabaseRuntime, DatabaseRuntime, DatabaseRuntime]:
    def runtime(role: Any, name: str, container: str, port: int) -> DatabaseRuntime:
        return DatabaseRuntime(
            role=role,
            container_name=container,
            port=port,
            database_name=name,
            owner_name="pinvi_app" if role == "pinvi" else "map_owner",
            admin_name="cluster_admin",
        )

    return (
        runtime("map_application", "kor_travel_map", "map-postgres", 12700),
        runtime("map_dagster", "kor_travel_map_dagster", "map-postgres", 12700),
        runtime("pinvi", "pinvi", "shared-postgres", 11000),
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
) -> SimpleNamespace:
    """재구축을 실제 오케스트레이션 그대로 돌리는 대역. DB·docker·compose는 대역이다.

    compose 호출, readiness 요청, 이미지 조회 label, C6c 검사 대상을 기록하고, 라이브 DB
    identity·head는 `live`로 바꿀 수 있다.
    """

    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER": "map_dagster_metadata",
        "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD": "metadata-password",
        "COMPOSE_PROJECT_NAME": "f1d-migrate-forward",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state"),
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "rebuild-admin-password",
    }
    candidate = _candidate_generation()
    map_candidate = _map_application_candidate()
    image_ids = candidate.image_ids
    resolved_services: dict[str, object] = {
        name: {"image": image_ids[owner]} for name, owner in _FORWARD_COMPANIONS.items()
    }
    # 같은 slot 이미지를 쓰는 one-shot writer는 companion이 아니다.
    resolved_services.update(
        {
            "kor-travel-map-dagster-storage-migrate": {
                "image": image_ids["kor-travel-map-dagster"]
            },
            "kor-travel-map-application-schema": {"image": image_ids["kor-travel-map-api"]},
            "pinvi-admin-bootstrap": {"image": image_ids["pinvi-api"]},
        }
    )
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
            "map_dagster": candidate.map_dagster_head,
            "pinvi": candidate.pinvi_head,
        },
        "pinvi_schema_table": True,
        # 떠 있는 Map PostgreSQL 컨테이너의 이미지. `up`이 후보 이미지로 다시 만든다.
        "map_postgres_image": map_candidate.postgres_image_id,
        # PostgreSQL `up`이 일어날 때 부르는 hook(PGDATA가 바뀌어 cluster가 바뀌는 경우 등).
        "on_postgres_up": None,
    }
    operations: list[tuple[str, ...]] = []
    readiness_requests: list[tuple[str, ...]] = []
    image_labels: list[str] = []
    inspected_services: list[tuple[str, ...]] = []
    mocks = SimpleNamespace(
        reset=Mock(),
        ensure_map=Mock(return_value="present"),
        dagster_init=Mock(),
        fence=Mock(),
        pinvi_bootstrap=Mock(),
        smoke=Mock(),
        paired_builder=Mock(),
        materialize=Mock(side_effect=lambda **_kwargs: _sources()),
        manifest_write=Mock(),
        contract=Mock(),
        prerequisites=Mock(),
        create_pinvi=Mock(return_value=False),
        map_precheck=Mock(return_value="present"),
        retention_generation=Mock(),
        retention_candidate=Mock(),
    )

    def run_compose(arguments: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operations.append(tuple(arguments))
        if arguments[:1] == ["up"] and "kor-travel-map-postgres" in arguments:
            live["map_postgres_image"] = map_candidate.postgres_image_id
            if live["on_postgres_up"] is not None:
                live["on_postgres_up"]()
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
        return [
            {"Name": f"{name}-latest", "Service": name, "State": "running"}
            for name in services
        ]

    def inspect_image(container_name: str, *, label: str) -> str:
        del container_name
        image_labels.append(label)
        if label == "Map PostgreSQL":
            return cast(str, live["map_postgres_image"])
        return image_ids[cast(Any, _FORWARD_COMPANIONS.get(label, label))]

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

    def read_identity(runtime: DatabaseRuntime) -> tuple[str, int, str] | None:
        identities = cast(dict[str, Any], live["identities"])
        return cast("tuple[str, int, str] | None", identities.get(runtime.role))

    def read_head(runtime: DatabaseRuntime) -> str:
        head = cast(dict[str, Any], live["heads"]).get(runtime.role)
        if head is None:
            raise DeploymentContractError(f"{runtime.role} schema revision output is invalid")
        return cast(str, head)

    from kor_travel_docker_manager.services import runtime_execution_registry

    monkeypatch.setattr(
        runtime_execution_registry, "trusted_manager_source_revision", lambda: "e" * 40
    )
    for name, replacement in {
        "c6c_deployment_lock_from_environment": lambda: nullcontext(object()),
        "_require_pinned_runtime_rebuild_root": lambda: None,
        "_capture_compose_environment_snapshot": (
            lambda *, environment_override: transaction.environment
        ),
        "_assert_transaction_matches_c6c_lock": Mock(),
        "materialize_pinned_runtime_sources": mocks.materialize,
        "_ensure_map_application_300_python_base_images": Mock(),
        "_build_map_application_300_images": mocks.paired_builder,
        "_load_application_300_candidate": Mock(return_value=map_candidate),
        "_local_image_present": lambda _image: images_present,
        "_run_pinned_runtime_static_command": Mock(return_value="{}"),
        "parse_candidate_static_head": Mock(return_value="head"),
        "build_candidate_generation": lambda **_kwargs: candidate,
        "ensure_generation_references": Mock(),
        "database_runtimes_from_frozen_contract": lambda **_kwargs: runtimes,
        "validate_map_postgres_runtime_secret_isolation": Mock(),
        "validate_pinvi_postgres_runtime_secret_isolation": Mock(),
        "read_database_identity": read_identity,
        "read_database_schema_revision": read_head,
        "schema_revision_table_exists": lambda _runtime: live["pinvi_schema_table"],
        "ensure_map_application_database": mocks.ensure_map,
        "initialize_application_300_dagster_metadata_database": mocks.dagster_init,
        "reset_databases_for_application_300": mocks.reset,
        "reconcile_orphaned_pinvi_bootstrap_credentials": Mock(),
        "run_pinvi_canonical_smoke": mocks.smoke,
        "C6cDeploymentConfig": _C6cConfig,
        "load_c6c_deployment_config_from_environment": Mock(return_value=_C6cConfig()),
        "validate_runtime_secret_isolation": Mock(),
        "validate_current_map_ui_auth_runtime": Mock(),
        "write_pinned_runtime_manifest": mocks.manifest_write,
        "reconcile_generation_references": mocks.retention_generation,
        "reconcile_candidate_build_references": mocks.retention_candidate,
        "create_database_if_absent": mocks.create_pinvi,
        "require_map_application_database_convergible": mocks.map_precheck,
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
        "_inspect_c6c_runtime_configs": inspect_c6c,
        "_ensure_pinvi_fresh_migration_fence": mocks.fence,
        "_run_pinvi_admin_bootstrap": mocks.pinvi_bootstrap,
    }.items():
        monkeypatch.setattr(service, name, replacement)
    return SimpleNamespace(
        service=service,
        candidate=candidate,
        runtimes=runtimes,
        status_path=status_path,
        live=live,
        operations=operations,
        readiness_requests=readiness_requests,
        image_labels=image_labels,
        inspected_services=inspected_services,
        mocks=mocks,
        expected_images=ComposeService._deployed_images(candidate, _FORWARD_COMPANIONS),
    )


def _mutating_operations(harness: SimpleNamespace) -> list[tuple[str, ...]]:
    """DB 서버 기동 말고 무언가를 바꾸는 compose 호출."""

    return [
        operation
        for operation in harness.operations
        if not (
            operation[:1] == ("up",)
            and operation[-2:] == ("kor-travel-map-postgres", "pinvi-postgres")
        )
    ]


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
    assert harness.operations.count(_STORAGE_RUN) == 1
    harness.mocks.pinvi_bootstrap.assert_called_once()
    status = read_deploy_status(harness.status_path)
    assert status is not None
    assert status.state == "committed"
    assert dict(status.images) == harness.expected_images
    assert dict(status.databases or {}) == _deployed_databases()
    harness.mocks.manifest_write.assert_called_once()


def test_leftover_v6_v8_state_is_not_adopted_and_is_left_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``deploy-status.json``이 없으면 기준선 없는 전체 경로 한 번이다(ADR-51 B3).

    **지금 배포할 바로 그 세대**를 담은 유효한 v6 manifest와 옛 v8 journal 파일이 state
    root에 남아 있어도 넘겨받지 않는다(n150에는 그런 파일이 남아 있다). manifest를
    넘겨받았다면 같은 pair라 결과는 ``converged``였을 것이다 — manifest가 유효하므로
    이 검사는 "읽지 못해서 안 넘겨받음"과 "넘겨받지 않음"을 가른다. v8 journal 모델은
    B3에서 지워져 그 자리에는 원시 바이트만 심는다. 여기서 v6 쓰기는
    대역(``mocks.manifest_write``)이라 두 파일은 바이트 그대로 남아야 한다.
    """

    monkeypatch.setenv("KTDM_PINNED_RUNTIME_PUBLIC_ROOT", str(tmp_path / "public"))
    harness = _forward_harness(monkeypatch, tmp_path)
    state_root = harness.status_path.parent
    manifest_path = state_root / "pinned-runtime-generation-v6.json"
    journal_path = (
        state_root / f"pinned-runtime-rebuild-v8-{harness.candidate.pinset_sha256}.json"
    )
    write_manifest(
        manifest_path,
        PinnedRuntimeManifest(version=6, active_generation=harness.candidate),
    )
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
    assert harness.operations.count(_STORAGE_RUN) == 1
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
    # in_progress 전의 거부다 — launcher는 claim을 해제한다.
    assert compose_service_module.pinned_runtime_failed_before_journal(captured.value)


def test_restart_resets_once_and_rebaselines_the_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_generation()
    previous = _committed_status(candidate)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    new_identities = {
        "map_application": ("kor_travel_map", 17001, "7300000000000000001"),
        "map_dagster": ("kor_travel_map_dagster", 17002, "7300000000000000001"),
        "pinvi": ("pinvi", 27001, "7300000000000000002"),
    }
    harness.live["identities"] = {"map_application": None, "map_dagster": None, "pinvi": None}
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
        "map_dagster": 17002,
        "pinvi": 27001,
    }


def test_a_failure_after_in_progress_cleans_up_and_the_rerun_finishes_without_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_generation()
    previous = _committed_status(candidate, map_revision="0" * 40)
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)
    harness.live["heads"]["map_dagster"] = "older-dagster-head"

    with pytest.raises(DeploymentContractError, match="storage execution result") as captured:
        harness.service.rebuild_pinned_runtime()

    stop = ("stop", *RUNTIME_SERVICES, *sorted(_FORWARD_COMPANIONS))
    # 기동 전 정지 + 실패 정리 정지. 정리에서 companion이 빠지면 실패한 세대의
    # code-server가 살아남는다.
    assert harness.operations.count(stop) == 2
    status = read_deploy_status(harness.status_path)
    assert status is not None and status.state == "in_progress"
    assert dict(status.databases or {}) == _deployed_databases()
    # in_progress를 쓴 뒤의 실패다 — prejournal로 표시하지 않는다.
    assert not compose_service_module.pinned_runtime_failed_before_journal(captured.value)

    harness.live["heads"]["map_dagster"] = candidate.map_dagster_head
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


def test_the_dagster_metadata_database_is_created_only_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.service.rebuild_pinned_runtime()
    harness.mocks.dagster_init.assert_not_called()

    absent = _forward_harness(monkeypatch, tmp_path / "absent")
    identities = absent.live["identities"]
    created = identities.pop("map_dagster")

    def create(runtime: object, **_kwargs: object) -> None:
        del runtime
        identities["map_dagster"] = created

    absent.mocks.dagster_init.side_effect = create
    absent.service.rebuild_pinned_runtime()

    absent.mocks.dagster_init.assert_called_once()


def test_existing_candidate_images_are_not_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """태그는 pinset에 묶인다 — 다시 빌드하면 재현되지 않는 digest가 같은 pair를 새 이미지로 만든다."""

    harness = _forward_harness(monkeypatch, tmp_path, images_present=True)

    harness.service.rebuild_pinned_runtime()

    harness.mocks.paired_builder.assert_not_called()
    assert not any(operation[0] == "build" for operation in harness.operations)


def test_a_candidate_compose_build_failure_is_sealed_with_its_own_stage(
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

    with pytest.raises(compose_service_module.PinnedRuntimePrejournalFailure) as captured:
        harness.service.rebuild_pinned_runtime()

    assert captured.value.stage == "candidate_compose_build"
    assert "Compose build command failed" in str(captured.value.__cause__)
    assert not compose_service_module.pinned_runtime_journal_was_reached(captured.value)
    assert read_deploy_status(harness.status_path) is None
    harness.mocks.reset.assert_not_called()


def test_a_candidate_contract_refusal_precedes_any_runtime_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.mocks.contract.side_effect = DeploymentContractError("candidate contract refused")

    with pytest.raises(compose_service_module.PinnedRuntimePrejournalFailure) as captured:
        harness.service.rebuild_pinned_runtime()

    assert captured.value.stage == "candidate_contract"
    assert harness.operations == []
    assert read_deploy_status(harness.status_path) is None


def test_external_prerequisites_are_checked_before_sources_are_materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _forward_harness(monkeypatch, tmp_path)
    harness.mocks.prerequisites.side_effect = DeploymentContractError("geo is not ready")

    with pytest.raises(compose_service_module.PinnedRuntimePrejournalFailure) as captured:
        harness.service.rebuild_pinned_runtime()

    assert captured.value.stage == "external_prerequisites"
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
    harness.live["identities"]["map_dagster"] = (
        "kor_travel_map_dagster",
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


def test_the_databases_are_brought_to_the_frozen_compose_before_any_judgment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PostgreSQL `up`은 수렴·identity 판정보다 먼저다(설정이 같으면 무연산이다)."""

    candidate = _candidate_generation()
    harness = _forward_harness(
        monkeypatch, tmp_path, previous=_committed_status(candidate, map_revision="0" * 40)
    )

    harness.service.rebuild_pinned_runtime()

    postgres_up = [
        index
        for index, operation in enumerate(harness.operations)
        if operation[:1] == ("up",) and "kor-travel-map-postgres" in operation
    ]
    # 판정 전 한 번뿐이다. 전체 경로가 다시 `up`하면 판정과 migration 사이에 cluster가
    # 바뀔 자리가 생긴다.
    assert postgres_up == [0]


@pytest.mark.parametrize("same_pair", (True, False))
def test_a_cluster_swapped_by_the_postgres_up_is_refused_before_anything_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_pair: bool
) -> None:
    """PGDATA가 바뀐 호스트: 옛 컨테이너로 기준선을 통과한 뒤 새 cluster를 커밋하면 안 된다.

    B2 적대 리뷰 2차(major). 판정 전에 `up`하므로 판정이 새 cluster를 본다.
    """

    candidate = _candidate_generation()
    previous = _committed_status(
        candidate, **({} if same_pair else {"map_revision": "0" * 40})
    )
    harness = _forward_harness(monkeypatch, tmp_path, previous=previous)

    def swap() -> None:
        harness.live["identities"]["map_application"] = (
            "kor_travel_map",
            55555,
            "7399999999999999999",
        )

    harness.live["on_postgres_up"] = swap

    with pytest.raises(DeploymentContractError, match="--adopt-live-databases"):
        harness.service.rebuild_pinned_runtime()

    assert _mutating_operations(harness) == []
    assert read_deploy_status(harness.status_path) == previous
    harness.mocks.ensure_map.assert_not_called()


def test_a_changed_map_postgres_image_is_recreated_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """떠 있는 Map PostgreSQL이 옛 이미지여도 `up`이 후보 이미지로 다시 만든다.

    B2 적대 리뷰 2차(major): 준비된 컨테이너를 건너뛰면 이미지 대조가 모든 실행 —
    `--restart`·`--adopt-live-databases`까지 — 을 같은 자리에서 영구히 거부했다.
    """

    candidate = _candidate_generation()
    harness = _forward_harness(
        monkeypatch, tmp_path, previous=_committed_status(candidate)
    )
    harness.live["map_postgres_image"] = f"sha256:{999:064x}"

    result = harness.service.rebuild_pinned_runtime()

    assert result["outcome"] == "converged"


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
    assert compose_service_module.pinned_runtime_failed_before_journal(captured.value)


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
    """디스크가 차서 기록을 못 써도 검증이 끝난 런타임을 내리지 않는다."""

    harness = _forward_harness(monkeypatch, tmp_path)
    harness.mocks.manifest_write.side_effect = DeploymentContractError("disk full")

    with pytest.raises(DeploymentContractError, match="disk full"):
        harness.service.rebuild_pinned_runtime()

    stop = ("stop", *RUNTIME_SERVICES, *sorted(_FORWARD_COMPANIONS))
    assert harness.operations.count(stop) == 1
    status = read_deploy_status(harness.status_path)
    assert status is not None and status.state == "in_progress"
