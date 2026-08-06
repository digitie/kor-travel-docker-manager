from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import RUNTIME_SERVICES
from kor_travel_docker_manager.services.pinned_runtime_rebuild import (
    CandidateRuntimeBuild,
    build_candidate_generation,
    new_candidate_journal,
    parse_candidate_static_head,
)
from kor_travel_docker_manager.services.pinned_runtime_release import PINNED_RUNTIME_RELEASE
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    MaterializedRuntimeSource,
    PinnedRuntimeSourceMaterialization,
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


def test_candidate_build_uses_private_deterministic_tags_and_staged_sources() -> None:
    build = CandidateRuntimeBuild(_sources())

    environment = build.compose_environment()

    assert environment["KOR_TRAVEL_MAP_REPO_DIR"] == "/state/map"
    assert environment["PINVI_REPO_DIR"] == "/state/pinvi"
    assert environment["PINVI_BUILD_ENVIRONMENT"] == "production"
    assert set(build.image_names) == set(RUNTIME_SERVICES)
    assert all(
        image.endswith(PINNED_RUNTIME_RELEASE.pinset_sha256)
        and image.startswith("kor-travel-docker-manager/pinned-runtime-candidate-v5/")
        for image in build.image_names.values()
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


def test_candidate_generation_and_journal_bind_all_runtime_inputs() -> None:
    sources = _sources()
    generation = build_candidate_generation(
        sources=sources,
        image_ids={service: f"sha256:{index:064x}" for index, service in enumerate(RUNTIME_SERVICES)},
        map_application_head="0084_pipeline_root",
        map_dagster_head="dagster_storage_1",
        pinvi_head="20260806_0001",
        recorded_at="2026-08-06T00:00:00+00:00",
    )
    resolved = "c" * 64

    journal = new_candidate_journal(
        candidate=generation,
        environment_bytes=b"frozen-env\n",
        compose_source_bytes=b"services: {}\n",
        resolved_compose_sha256=resolved,
        created_at="2026-08-06T00:00:00+00:00",
    )

    assert journal.phase == "candidate_attested"
    assert journal.candidate == generation
    assert journal.environment_sha256 == hashlib.sha256(b"frozen-env\n").hexdigest()
    assert journal.resolved_compose_sha256 == resolved
