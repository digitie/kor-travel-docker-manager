from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.compose_service import ComposeService
from kor_travel_docker_manager.services.database_runtime import (
    Application300DatabaseIdentity as RuntimeApplication300DatabaseIdentity,
)
from kor_travel_docker_manager.services.map_application_300 import (
    Application300Contract,
)
from kor_travel_docker_manager.services.map_application_300_candidate import (
    MapApplication300Candidate,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    REBUILD_PHASES,
    RUNTIME_SERVICES,
    MapApplication300ApplicationDatabaseIdentity,
    MapApplication300DagsterMetadataDatabaseIdentity,
    MapApplication300DagsterMetadataRoleAttributes,
    MapApplication300OperationPlan,
    PinnedRuntimeCancelProbeOutcome,
    PinnedRuntimeCancelProbeReceipt,
    PinnedRuntimeGeneration,
    PinnedRuntimeManifest,
    PinnedRuntimeRebuildJournal,
    RebuildPhase,
    RuntimeService,
    manifest_from_payload,
    pinned_runtime_state_paths,
    read_rebuild_journal,
    rebuild_journal_sha256,
    write_rebuild_journal,
)
from kor_travel_docker_manager.services.pinned_runtime_rebuild import (
    COMPOSE_BUILT_RUNTIME_SERVICES,
    CandidateRuntimeBuild,
    MapApplication300ArtifactDirectories,
    build_candidate_generation,
    generation_compose_environment,
    map_application_300_paired_build_image_names,
    new_candidate_journal,
    parse_candidate_static_head,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    CANONICAL_RUNTIME_SOURCE_URLS,
    PINNED_RUNTIME_RELEASE,
    PINVI_PINNED_RUNTIME_SOURCE,
    PinnedRuntimeRelease,
    PinnedRuntimeSourceSpec,
    canonical_pinset_sha256,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    MaterializedRuntimeSource,
    PinnedRuntimeSourceMaterialization,
)

_real_map_application_300_paths = compose_service_module._map_application_300_paths


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
    base = tmp_path / "application-300"
    monkeypatch.setattr(
        compose_service_module,
        "_map_application_300_paths",
        lambda *, state_root, pinset_sha256: compose_service_module._MapApplication300Paths(
            api_receipt=base / "receipts" / "api.json",
            paired_receipt=base / "receipts" / "paired.json",
            root_fence_directory=base / "fresh-root-fence",
            finalize_fence_directory=base / "fresh-finalize-fence",
            application_permit_directory=base / "application-final-permit",
            metadata_permit_directory=base / "dagster-storage-permit",
            result_directory=base / "results",
        ),
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


def _map_application_300_candidate(
    sources: PinnedRuntimeSourceMaterialization | None = None,
    *,
    api_image_id: str = f"sha256:{101:064x}",
    dagster_image_id: str = f"sha256:{102:064x}",
    postgres_image_id: str = f"sha256:{103:064x}",
) -> MapApplication300Candidate:
    materialized = sources or _sources()
    contract = Application300Contract(
        reference_manifest_sha256="1" * 64,
        postgres_image_id=postgres_image_id,
        source_catalog_sha256="2" * 64,
        destination_catalog_sha256="3" * 64,
        seed_sha256="4" * 64,
        privileged_residue_sha256="5" * 64,
        source_alembic_version_sha256="6" * 64,
        destination_alembic_version_sha256="7" * 64,
        runtime_invariants_sql_sha256="8" * 64,
    )
    return MapApplication300Candidate(
        receipt_sha256="9" * 64,
        api_receipt_sha256="a" * 64,
        candidate_commit=materialized.source_for("map").revision,
        candidate_git_tree=materialized.source_for("map").tree,
        api_image_id=api_image_id,
        dagster_image_id=dagster_image_id,
        postgres_image_id=postgres_image_id,
        dagster_config_sha256="b" * 64,
        dagster_yaml_sha256="c" * 64,
        application_contract=contract,
        application_contract_sha256="d" * 64,
        launch_contract_sha256="e" * 64,
        webserver_argv_prefix=("/usr/local/bin/dagster-webserver",),
        webserver_port_minimum=1,
        webserver_port_maximum=65535,
        daemon_argv=("/usr/local/bin/dagster-daemon", "run"),
        storage_migration_argv=("/usr/local/bin/ktm-dagster-storage", "migrate"),
    )


def _candidate_image_ids(
    candidate: MapApplication300Candidate,
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
    paired = _map_application_300_candidate(materialized)
    return build_candidate_generation(
        sources=materialized,
        map_application_300_candidate=paired,
        image_ids=_candidate_image_ids(paired),
        map_dagster_head="map-dagster-head",
        pinvi_head="pinvi-head",
    )


def _application_database_identity() -> MapApplication300ApplicationDatabaseIdentity:
    return MapApplication300ApplicationDatabaseIdentity(
        database_name="kor_travel_map",
        database_oid=127001,
        database_owner="ktm_feature_schema_owner",
        postgres_system_identifier="7474747474747474747",
    )


def _runtime_application_database_identity() -> RuntimeApplication300DatabaseIdentity:
    return RuntimeApplication300DatabaseIdentity(
        database_name="kor_travel_map",
        database_oid=127001,
        database_owner="ktm_feature_schema_owner",
        postgres_system_identifier="7474747474747474747",
    )


def _dagster_database_identity() -> MapApplication300DagsterMetadataDatabaseIdentity:
    return MapApplication300DagsterMetadataDatabaseIdentity(
        system_identifier="7474747474747474747",
        name="kor_travel_map_dagster",
        oid=127002,
        owner="map_dagster_metadata",
        login_role="map_dagster_metadata",
        login_role_attributes=MapApplication300DagsterMetadataRoleAttributes(
            superuser=False,
            create_database=False,
            create_role=False,
            replication=False,
            bypass_rls=False,
            granted_role_count=0,
            member_role_count=0,
        ),
    )


def _operation_plan(
    journal: PinnedRuntimeRebuildJournal,
    *,
    seed: str,
) -> MapApplication300OperationPlan:
    return MapApplication300OperationPlan(
        transaction_id=journal.transaction_id,
        operation_id=str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{journal.transaction_id}:{seed}")
        ),
        basis_journal_sha256=rebuild_journal_sha256(journal),
        basis_journal_generation=journal.journal_generation,
        writer_fence_expires_at="2026-08-06T00:05:00+00:00",
        fence_sha256=seed * 64,
    )


def _journal_at_application_300_phase(
    phase: RebuildPhase,
) -> PinnedRuntimeRebuildJournal:
    journal = new_candidate_journal(
        candidate=_candidate_generation(),
        environment_bytes=b"frozen-env\n",
        compose_source_bytes=b"services: {}\n",
        resolved_compose_sha256="c" * 64,
        created_at="2026-08-06T00:00:00+00:00",
    )
    journal = journal.transition("reset_intent_durable")
    if phase == "reset_intent_durable":
        return journal
    journal = journal.transition("databases_recreated")
    if phase == "databases_recreated":
        return journal
    journal = journal.with_application_roles_ready(
        application_database_identity=_application_database_identity()
    )
    if phase == "application_roles_ready":
        return journal
    root_plan = _operation_plan(journal, seed="2")
    journal = journal.with_fresh_root_plan_ready(fresh_root_operation_plan=root_plan)
    if phase == "fresh_root_plan_ready":
        return journal
    journal = journal.with_fresh_root_fence_ready(fresh_root_operation_plan=root_plan)
    if phase == "fresh_root_fence_ready":
        return journal
    journal = journal.with_fresh_root_execution_intent(
        fresh_root_operation_plan=root_plan
    )
    if phase == "fresh_root_execution_intent":
        return journal
    journal = journal.with_fresh_root_ready(
        fresh_root_operation_plan=root_plan.with_result("4" * 64)
    )
    if phase == "fresh_root_ready":
        return journal
    finalize_plan = _operation_plan(journal, seed="5")
    journal = journal.with_fresh_finalize_plan_ready(
        fresh_finalize_operation_plan=finalize_plan
    )
    if phase == "fresh_finalize_plan_ready":
        return journal
    journal = journal.with_fresh_finalize_fence_ready(
        fresh_finalize_operation_plan=finalize_plan
    )
    if phase == "fresh_finalize_fence_ready":
        return journal
    journal = journal.with_fresh_finalize_execution_intent(
        fresh_finalize_operation_plan=finalize_plan
    )
    if phase == "fresh_finalize_execution_intent":
        return journal
    journal = journal.with_fresh_finalize_ready(
        fresh_finalize_operation_plan=finalize_plan.with_result("7" * 64)
    )
    if phase == "fresh_finalize_ready":
        return journal
    journal = journal.with_application_permit_ready(
        app_final_permit_sha256="8" * 64
    )
    if phase == "application_permit_ready":
        return journal
    journal = journal.with_metadata_permit_ready(
        dagster_metadata_database_identity=_dagster_database_identity(),
        metadata_permit_sha256="9" * 64,
    )
    if phase == "metadata_permit_ready":
        return journal
    journal = journal.with_map_application_ready()
    if phase == "map_application_ready":
        return journal
    raise AssertionError(f"unsupported application-300 phase: {phase}")


def _cancel_probe_receipts() -> tuple[PinnedRuntimeCancelProbeReceipt, ...]:
    armed = PinnedRuntimeCancelProbeReceipt().transition(
        "armed",
        job_id="00000000-0000-0000-0000-000000000000",
        fixture_created_at="2026-08-06T00:00:00+00:00",
    )
    attempted = armed.transition("cancel_post_attempted")
    consumed = attempted.transition(
        "consumed",
        cancellation_id="11111111-1111-1111-1111-111111111111",
        outcome=PinnedRuntimeCancelProbeOutcome(
            name="pinvi_cancel_error",
            status=409,
            code="PIPELINE_CANCELLATION_UNSAFE",
        ),
        fixture_consumed_at="2026-08-06T00:01:00+00:00",
    )
    finalize_attempted = consumed.transition("finalize_post_attempted")
    finalized = finalize_attempted.transition(
        "finalized",
        fixture_finalized_at="2026-08-06T00:02:00+00:00",
    )
    return armed, attempted, consumed, finalize_attempted, finalized


def _finalized_cancel_probe() -> PinnedRuntimeCancelProbeReceipt:
    return _cancel_probe_receipts()[-1]


def _journal_at_runtime_phase(phase: RebuildPhase) -> PinnedRuntimeRebuildJournal:
    journal = _journal_at_application_300_phase("map_application_ready")
    for next_phase in REBUILD_PHASES[
        REBUILD_PHASES.index("map_application_ready") + 1 :
        REBUILD_PHASES.index(phase) + 1
    ]:
        if next_phase == "cancel_probe_finalized":
            for receipt in _cancel_probe_receipts():
                journal = journal.with_cancel_probe(receipt)
        journal = journal.transition(next_phase)
    return journal


def _release_with_map_revision(map_revision: str) -> PinnedRuntimeRelease:
    sources = (
        PinnedRuntimeSourceSpec(
            role="map",
            canonical_url=CANONICAL_RUNTIME_SOURCE_URLS["map"],
            revision=map_revision,
        ),
        PINVI_PINNED_RUNTIME_SOURCE,
    )
    return PinnedRuntimeRelease(
        version=5,
        sources=sources,
        pinset_sha256=canonical_pinset_sha256(version=5, sources=sources),
    )


def test_candidate_build_uses_private_deterministic_tags_and_staged_sources() -> None:
    sources = _sources()
    candidate = _map_application_300_candidate(sources)
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
        candidate.dagster_yaml_sha256
    )
    assert environment["KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256"] != (
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


def test_candidate_generation_and_journal_bind_all_runtime_inputs() -> None:
    sources = _sources()
    paired = _map_application_300_candidate(sources)
    image_ids = _candidate_image_ids(paired)
    generation = build_candidate_generation(
        sources=sources,
        map_application_300_candidate=paired,
        image_ids=image_ids,
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
    assert journal.version == 8
    assert journal.journal_generation == 0
    assert journal.candidate == generation
    assert journal.map_application_300_candidate_evidence == (
        generation.map_application_300_candidate_evidence
    )
    assert journal.environment_sha256 == hashlib.sha256(b"frozen-env\n").hexdigest()
    assert journal.resolved_compose_sha256 == resolved
    assert generation.map_application_head == "300"
    assert generation.map_source_revision == paired.candidate_commit
    assert generation.map_application_300_candidate_evidence.candidate_git_tree == (
        paired.candidate_git_tree
    )
    assert manifest_from_payload(
        PinnedRuntimeManifest(version=6, active_generation=generation).to_payload()
    ).active_generation == generation

    artifact_directories = MapApplication300ArtifactDirectories(
        fresh_migrate_fence=Path("/state/root-fence"),
        fresh_finalize_fence=Path("/state/finalize-fence"),
        application_final_permit=Path("/state/application-permit"),
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
    assert runtime_environment[
        "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PAIRED_RECEIPT_SHA256"
    ] == paired.receipt_sha256
    assert runtime_environment["KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256"] == (
        paired.dagster_yaml_sha256
    )
    assert runtime_environment["KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256"] != (
        paired.dagster_config_sha256
    )
    assert runtime_environment[
        "KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR"
    ] == "/state/root-fence"


def test_candidate_generation_rejects_paired_source_and_image_drift() -> None:
    sources = _sources()
    paired = _map_application_300_candidate(sources)
    image_ids = _candidate_image_ids(paired)

    with pytest.raises(DeploymentContractError, match="source differs"):
        CandidateRuntimeBuild(
            sources,
            replace(paired, candidate_git_tree="f" * 40),
        )

    with pytest.raises(DeploymentContractError, match="Map API candidate image differs"):
        build_candidate_generation(
            sources=sources,
            map_application_300_candidate=paired,
            image_ids={**image_ids, "kor-travel-map-api": f"sha256:{999:064x}"},
            map_dagster_head="dagster_storage_1",
            pinvi_head="20260806_0001",
        )

    with pytest.raises(DeploymentContractError, match="web and daemon"):
        build_candidate_generation(
            sources=sources,
            map_application_300_candidate=paired,
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
    """appuser mount 네 개만 0755이고 영수증·결과·부모는 계속 0700이다."""

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

    mount_directories = (
        paths.root_fence_directory,
        paths.finalize_fence_directory,
        paths.application_permit_directory,
        paths.metadata_permit_directory,
    )
    private_directories = {
        paths.api_receipt.parent,
        paths.api_receipt.parent.parent,
        paths.result_directory,
        paths.result_directory.parent,
        paths.result_directory.parent.parent,
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
    journal = _journal_at_runtime_phase("committed")
    records = [
        {"Service": runtime_service, "Name": f"container-{runtime_service}"}
        for runtime_service in RUNTIME_SERVICES
    ]
    observed = dict(journal.candidate.image_ids)
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
        service._assert_pinned_runtime_container_images(records, journal=journal)


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

    with pytest.raises(DeploymentContractError, match="tokens must be configured together"):
        ComposeService().rebuild_pinned_runtime()

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


def test_candidate_contract_refusal_precedes_journal_runtime_stop_and_database_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate typed refusal은 journal/Compose/DB mutation 전에 그대로 멈춘다."""

    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        "COMPOSE_PROJECT_NAME": "f1d-candidate-refusal",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state"),
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "rebuild-admin-password",
    }
    transaction = SimpleNamespace(
        environment=SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n"),
        compose_source_bytes=b"services: {}\n",
        resolved_document_hash="c" * 64,
        resolved={"services": {}},
    )
    service = ComposeService()
    candidate_refusal = ComposeCandidateContractError("candidate diagnostic preserved")
    run_compose = Mock()
    database_reset = Mock()
    journal_write = Mock()
    paired_builder = Mock()
    paired_candidate = _map_application_300_candidate()

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: __import__("contextlib").nullcontext(object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: transaction.environment,
    )
    monkeypatch.setattr(compose_service_module, "_assert_transaction_matches_c6c_lock", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        lambda **_kwargs: _sources(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_run_map_application_300_paired_builder",
        paired_builder,
    )
    monkeypatch.setattr(
        service,
        "_load_application_300_paired_candidate",
        Mock(return_value=paired_candidate),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        lambda **_kwargs: (transaction, None),
    )
    monkeypatch.setattr(
        service,
        "_validate_pinned_runtime_candidate_build_contract",
        Mock(side_effect=candidate_refusal),
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)
    monkeypatch.setattr(
        compose_service_module,
        "reset_databases_for_application_300",
        database_reset,
    )
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_runtime_rebuild_journal",
        journal_write,
    )

    with pytest.raises(ComposeCandidateContractError) as captured:
        service.rebuild_pinned_runtime()

    state_paths = pinned_runtime_state_paths(
        values,
        pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
    )
    assert captured.value is candidate_refusal
    assert str(captured.value) == "candidate diagnostic preserved"
    journal_write.assert_not_called()
    run_compose.assert_not_called()
    database_reset.assert_not_called()
    paired_builder.assert_called_once()
    assert not state_paths.journal.exists()


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
            ["run", "kor-travel-map-dagster-storage-migrate"],
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
            ["run", "kor-travel-map-dagster-storage-migrate"],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


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
            ["run", "--rm", "pinvi-admin-bootstrap"],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


def test_rebuild_candidate_journal_binds_application_300_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "KTDM_C6C_CONTRACT_GENERATION": "c6c-ops-v1",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        "COMPOSE_PROJECT_NAME": "f1d-c2",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state"),
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "rebuild-admin-password",
        "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": "previous-candidate-head",
    }
    transaction = SimpleNamespace(
        environment=SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n"),
        compose_source_bytes=b"services: {}\n",
        resolved_document_hash="c" * 64,
        resolved={"services": {}},
    )
    captured: list[dict[str, str] | None] = []

    def capture(
        *,
        environment_override: dict[str, str] | None = None,
        environment_snapshot: object | None = None,
    ) -> tuple[SimpleNamespace, None]:
        del environment_snapshot
        captured.append(environment_override)
        return transaction, None

    service = ComposeService()
    operations: list[tuple[str, ...]] = []
    static_commands: list[tuple[str, ...]] = []
    paired_candidate = _map_application_300_candidate()
    image_ids = _candidate_image_ids(paired_candidate)
    paired_builder = Mock()
    database_reset = Mock()

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: __import__("contextlib").nullcontext(object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: transaction.environment,
    )
    monkeypatch.setattr(compose_service_module, "_assert_transaction_matches_c6c_lock", Mock())
    monkeypatch.setattr(service, "_capture_transaction_unlocked", capture)
    candidate_contract = Mock()
    monkeypatch.setattr(
        service,
        "_validate_pinned_runtime_candidate_build_contract",
        candidate_contract,
    )
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        lambda **_kwargs: _sources(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_run_map_application_300_paired_builder",
        paired_builder,
    )
    monkeypatch.setattr(
        service,
        "_load_application_300_paired_candidate",
        Mock(return_value=paired_candidate),
    )
    def run_compose(
        args: list[str],
        *,
        transaction: object,
    ) -> dict[str, object]:
        del transaction
        operations.append(tuple(args))
        return {"success": True, "stdout": "[]" if "ps" in args else ""}

    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)
    monkeypatch.setattr(
        service,
        "_attest_pinned_runtime_candidate_images",
        lambda *, build, map_candidate: image_ids,
    )
    def static_command(_image: str, command: tuple[str, ...], *, label: str) -> str:
        del label
        static_commands.append(command)
        return {
            "ktm-dagster-storage": '{"head":"map-dagster-head","schema":"kor-travel-map.dagster-storage-head.v1"}\n',
            "pinvi-admin-bootstrap": '{"pinvi_head":"pinvi-head","schema":"pinvi.candidate-head.v1"}\n',
        }[command[0]]

    monkeypatch.setattr(
        compose_service_module,
        "_run_pinned_runtime_static_command",
        static_command,
    )
    monkeypatch.setattr(compose_service_module, "ensure_generation_references", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "reset_databases_for_application_300",
        database_reset,
    )
    monkeypatch.setattr(
        compose_service_module,
        "retire_f1d_legacy_artifacts",
        Mock(side_effect=DeploymentContractError("stop after candidate journal")),
    )

    with pytest.raises(DeploymentContractError, match="stop after candidate journal"):
        service.rebuild_pinned_runtime()

    candidate_journal = read_rebuild_journal(
        pinned_runtime_state_paths(
            values,
            pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
        ).journal
    )
    assert candidate_journal.phase == "candidate_attested"
    assert candidate_journal.candidate.map_application_head == "300"
    assert captured[0] is not None
    assert captured[0]["KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD"] == "300"
    assert captured[-1] is not None
    assert captured[-1]["KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD"] == "300"
    assert operations == [
        ("build", *COMPOSE_BUILT_RUNTIME_SERVICES),
        (
            "--profile",
            "bootstrap",
            "run",
            "--rm",
            "--no-deps",
            "pinvi-admin-bootstrap",
            "pinvi-admin-bootstrap",
            "head",
        ),
    ]
    assert static_commands == [
        ("ktm-dagster-storage", "head"),
        ("pinvi-admin-bootstrap", "head"),
    ]
    paired_builder.assert_called_once()
    candidate_contract.assert_called_once()
    database_reset.assert_not_called()


def test_cancel_probe_attempt_receipt_restores_the_exact_nonretriable_state() -> None:
    journal = _journal_at_runtime_phase("pinvi_api_ready")
    armed = PinnedRuntimeCancelProbeReceipt().transition(
        "armed",
        job_id="22222222-2222-2222-2222-222222222222",
        fixture_created_at="2026-08-06T00:00:00+00:00",
    )
    journal = journal.with_cancel_probe(armed)
    journal = journal.with_cancel_probe(armed.transition("cancel_post_attempted"))

    resumed = compose_service_module._pinvi_cancel_probe_state_from_journal(journal)

    assert resumed.transaction_id == journal.transaction_id
    assert resumed.fixture is not None
    assert resumed.fixture.job_id == "22222222-2222-2222-2222-222222222222"
    assert resumed.fixture.state == "armed"
    assert resumed.attempted is True
    assert resumed.finalize_attempted is False


def test_database_reset_is_forbidden_after_databases_recreated() -> None:
    journal = new_candidate_journal(
        candidate=_candidate_generation(),
        environment_bytes=b"frozen-env\n",
        compose_source_bytes=b"services: {}\n",
        resolved_compose_sha256="c" * 64,
    )

    assert compose_service_module._pinned_runtime_reset_required(journal) is True
    reset_intent = journal.transition("reset_intent_durable")
    assert compose_service_module._pinned_runtime_reset_required(reset_intent) is True
    databases_recreated = reset_intent.transition("databases_recreated")
    assert (
        compose_service_module._pinned_runtime_reset_required(databases_recreated)
        is False
    )
    journal = _journal_at_runtime_phase("pinvi_api_ready")
    assert compose_service_module._pinned_runtime_reset_required(journal) is False


@pytest.mark.parametrize(
    ("phase", "one_shot_service", "expected_error"),
    (
        (
            "fresh_root_execution_intent",
            "kor-travel-map-application-fresh-300",
            "application 300 root execution result is uncertain",
        ),
        (
            "fresh_finalize_execution_intent",
            "kor-travel-map-application-fresh-finalize",
            "application 300 finalize execution result is uncertain",
        ),
        (
            "map_dagster_storage_intent_durable",
            "kor-travel-map-dagster-storage-migrate",
            "Map Dagster storage execution result is uncertain",
        ),
        (
            "map_application_ready",
            "pinvi-admin-bootstrap",
            "stop after map runtime startup",
        ),
    ),
)
def test_application_300_one_shots_never_reexecute_after_durable_intent(
    phase: RebuildPhase,
    one_shot_service: str,
    expected_error: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        "COMPOSE_PROJECT_NAME": "f1d-intent-resume",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state"),
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "rebuild-admin-password",
    }
    transaction = SimpleNamespace(
        environment=SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n"),
        compose_source_bytes=b"services: {}\n",
        resolved_document_hash="c" * 64,
        resolved={"services": {}},
    )
    journal = (
        _journal_at_runtime_phase(phase)
        if phase in {
            "map_dagster_storage_intent_durable",
            "map_application_ready",
        }
        else _journal_at_application_300_phase(phase)
    )
    state_paths = pinned_runtime_state_paths(
        values,
        pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
    )
    state_paths.state_root.mkdir(parents=True, mode=0o700)
    write_rebuild_journal(state_paths.journal, journal)
    service = ComposeService()
    operations: list[tuple[str, ...]] = []
    database_reset = Mock()
    create_database = Mock()
    map_candidate = _map_application_300_candidate()

    def run_compose(
        arguments: list[str],
        *,
        transaction: object,
    ) -> dict[str, object]:
        del transaction
        operations.append(tuple(arguments))
        return {"success": True, "stdout": ""}

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: nullcontext(object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: transaction.environment,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_assert_transaction_matches_c6c_lock",
        Mock(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        lambda **_kwargs: _sources(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_run_map_application_300_paired_builder",
        Mock(),
    )
    monkeypatch.setattr(
        service,
        "_load_application_300_paired_candidate",
        Mock(return_value=map_candidate),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        lambda **_kwargs: (transaction, None),
    )
    monkeypatch.setattr(
        service,
        "_validate_pinned_runtime_candidate_build_contract",
        Mock(),
    )
    monkeypatch.setattr(service, "_attest_pinned_runtime_candidate_images", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "ensure_generation_references",
        Mock(),
    )
    monkeypatch.setattr(compose_service_module, "retire_f1d_legacy_artifacts", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "database_runtimes_from_frozen_contract",
        lambda **_kwargs: (object(), object(), object()),
    )
    monkeypatch.setattr(
        service,
        "_retire_pinned_runtime_oneshot_writers",
        Mock(),
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)
    monkeypatch.setattr(
        service,
        "_require_services_ready",
        Mock(
            return_value=[
                {"Name": "map-postgres"},
                {"Name": "pinvi-postgres"},
            ]
        ),
    )
    monkeypatch.setattr(service, "_inspect_container_runtime_config", Mock(return_value={}))
    monkeypatch.setattr(
        service,
        "_inspect_container_image_id",
        Mock(return_value=map_candidate.postgres_image_id),
    )
    monkeypatch.setattr(
        compose_service_module,
        "validate_map_postgres_runtime_secret_isolation",
        Mock(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "validate_pinvi_postgres_runtime_secret_isolation",
        Mock(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_application_300_database_identity",
        Mock(return_value=_runtime_application_database_identity()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "reset_databases_for_application_300",
        database_reset,
    )
    monkeypatch.setattr(
        compose_service_module,
        "create_fresh_application_300_database",
        create_database,
    )
    if phase in {
        "fresh_finalize_execution_intent",
        "map_dagster_storage_intent_durable",
        "map_application_ready",
    }:
        monkeypatch.setattr(
            compose_service_module,
            "read_owner_only_artifact",
            Mock(return_value=b"root-result\n"),
        )
        monkeypatch.setattr(
            compose_service_module,
            "_application_300_root_result",
            Mock(return_value=object()),
        )
    if phase in {
        "map_dagster_storage_intent_durable",
        "map_application_ready",
    }:
        monkeypatch.setattr(
            compose_service_module,
            "_application_300_finalize_result",
            Mock(return_value=object()),
        )
        monkeypatch.setattr(
            compose_service_module,
            "build_application_final_permit",
            Mock(return_value=SimpleNamespace(raw=b"app-permit", sha256="8" * 64)),
        )
        monkeypatch.setattr(
            compose_service_module,
            "publish_root_read_only_artifact",
            Mock(),
        )
        monkeypatch.setattr(
            compose_service_module,
            "read_application_300_dagster_metadata_identity",
            Mock(return_value=object()),
        )
        monkeypatch.setattr(
            compose_service_module,
            "_application_300_dagster_identities",
            Mock(return_value=(object(), _dagster_database_identity())),
        )
        monkeypatch.setattr(
            compose_service_module,
            "build_dagster_metadata_permit",
            Mock(
                return_value=SimpleNamespace(
                    raw=b"metadata-permit",
                    sha256="9" * 64,
                )
            ),
        )
        revision_heads = iter(
            (
                "300",
                (
                    "unexpected-dagster-head"
                    if phase == "map_dagster_storage_intent_durable"
                    else journal.candidate.map_dagster_head
                ),
            )
        )
        monkeypatch.setattr(
            compose_service_module,
            "read_database_schema_revision",
            lambda _runtime: next(revision_heads),
        )
    if phase == "map_application_ready":
        monkeypatch.setattr(
            compose_service_module,
            "pinvi_bootstrap_credential_file",
            Mock(
                side_effect=DeploymentContractError(
                    "stop after map runtime startup"
                )
            ),
        )

    with pytest.raises(DeploymentContractError, match=expected_error):
        service.rebuild_pinned_runtime()

    assert all(one_shot_service not in operation for operation in operations)
    storage_command = (
        "run",
        "--rm",
        "--no-deps",
        "kor-travel-map-dagster-storage-migrate",
    )
    if phase == "map_application_ready":
        assert operations.count(storage_command) == 1
        assert (
            "up",
            "-d",
            "--no-deps",
            "--wait",
            "--wait-timeout",
            "300",
            "kor-travel-map-ui",
            "kor-travel-map-dagster",
            "kor-travel-map-dagster-daemon",
        ) in operations
    elif phase == "map_dagster_storage_intent_durable":
        assert operations.count(storage_command) == 0
    database_reset.assert_not_called()
    create_database.assert_not_called()


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


def test_legacy_tombstone_failure_is_retried_before_any_database_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        "COMPOSE_PROJECT_NAME": "f1d-tombstone-retry",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state"),
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "rebuild-admin-password",
    }
    transaction = SimpleNamespace(
        environment=SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n"),
        compose_source_bytes=b"services: {}\n",
        resolved_document_hash="c" * 64,
        resolved={"services": {}},
    )
    candidate = _candidate_generation()
    journal = new_candidate_journal(
        candidate=candidate,
        environment_bytes=b"frozen-env\n",
        compose_source_bytes=b"services: {}\n",
        resolved_compose_sha256="c" * 64,
    )
    state_paths = pinned_runtime_state_paths(
        values,
        pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
    )
    state_paths.state_root.mkdir(parents=True, mode=0o700)
    write_rebuild_journal(state_paths.journal, journal)

    service = ComposeService()
    database_reset = Mock()
    run_compose = Mock()
    tombstone = Mock(side_effect=DeploymentContractError("legacy tombstone failed"))
    paired_builder = Mock()

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: __import__("contextlib").nullcontext(object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: transaction.environment,
    )
    monkeypatch.setattr(compose_service_module, "_assert_transaction_matches_c6c_lock", Mock())
    monkeypatch.setattr(service, "_capture_transaction_unlocked", lambda **_kwargs: (transaction, None))
    monkeypatch.setattr(service, "_validate_pinned_runtime_candidate_build_contract", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        lambda **_kwargs: _sources(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_run_map_application_300_paired_builder",
        paired_builder,
    )
    monkeypatch.setattr(
        service,
        "_load_application_300_paired_candidate",
        Mock(return_value=_map_application_300_candidate()),
    )
    monkeypatch.setattr(service, "_attest_pinned_runtime_candidate_images", Mock())
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)
    monkeypatch.setattr(compose_service_module, "ensure_generation_references", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "reset_databases_for_application_300",
        database_reset,
    )
    monkeypatch.setattr(compose_service_module, "retire_f1d_legacy_artifacts", tombstone)

    for _attempt in range(2):
        with pytest.raises(DeploymentContractError, match="legacy tombstone failed"):
            service.rebuild_pinned_runtime()

    assert tombstone.call_count == 2
    run_compose.assert_not_called()
    database_reset.assert_not_called()
    assert paired_builder.call_count == 2


def test_new_pinset_ignores_previous_journal_and_starts_a_fresh_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        "COMPOSE_PROJECT_NAME": "f1d-pinset-rotation",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "state"),
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "rebuild-admin-password",
    }
    previous_release = _release_with_map_revision("d" * 40)
    next_release = _release_with_map_revision("e" * 40)
    previous_sources = _sources_for(previous_release)
    previous_candidate = _candidate_generation(previous_sources)
    previous_journal = new_candidate_journal(
        candidate=previous_candidate,
        environment_bytes=b"frozen-env\n",
        compose_source_bytes=b"services: {}\n",
        resolved_compose_sha256="c" * 64,
    )
    previous_paths = pinned_runtime_state_paths(
        values,
        pinset_sha256=previous_release.pinset_sha256,
    )
    previous_paths.state_root.mkdir(parents=True, mode=0o700)
    write_rebuild_journal(previous_paths.journal, previous_journal)
    transaction = SimpleNamespace(
        environment=SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n"),
        compose_source_bytes=b"services: {}\n",
        resolved_document_hash="c" * 64,
        resolved={"services": {}},
    )
    service = ComposeService()
    compose_calls: list[tuple[str, ...]] = []
    next_sources = _sources_for(next_release)
    next_map_candidate = _map_application_300_candidate(next_sources)

    def run_compose(
        args: list[str],
        *,
        transaction: object,
    ) -> dict[str, object]:
        del transaction
        compose_calls.append(tuple(args))
        return {"success": True, "stdout": ""}

    def static_command(_image: str, command: tuple[str, ...], *, label: str) -> str:
        del label
        return {
            "ktm-dagster-storage": '{"head":"map-dagster-head","schema":"kor-travel-map.dagster-storage-head.v1"}\n',
            "pinvi-admin-bootstrap": '{"pinvi_head":"pinvi-head","schema":"pinvi.candidate-head.v1"}\n',
        }[command[0]]

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: __import__("contextlib").nullcontext(object()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: transaction.environment,
    )
    monkeypatch.setattr(compose_service_module, "_assert_transaction_matches_c6c_lock", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "current_pinned_runtime_release",
        lambda: next_release,
    )
    monkeypatch.setattr(service, "_capture_transaction_unlocked", lambda **_kwargs: (transaction, None))
    monkeypatch.setattr(service, "_validate_pinned_runtime_candidate_build_contract", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        lambda **_kwargs: next_sources,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_run_map_application_300_paired_builder",
        Mock(),
    )
    monkeypatch.setattr(
        service,
        "_load_application_300_paired_candidate",
        Mock(return_value=next_map_candidate),
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)
    monkeypatch.setattr(
        service,
        "_attest_pinned_runtime_candidate_images",
        lambda *, build, map_candidate: _candidate_image_ids(map_candidate),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_run_pinned_runtime_static_command",
        static_command,
    )
    monkeypatch.setattr(compose_service_module, "ensure_generation_references", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "retire_f1d_legacy_artifacts",
        Mock(side_effect=DeploymentContractError("stop after new journal")),
    )

    with pytest.raises(DeploymentContractError, match="stop after new journal"):
        service.rebuild_pinned_runtime()

    next_paths = pinned_runtime_state_paths(
        values,
        pinset_sha256=next_release.pinset_sha256,
    )
    assert read_rebuild_journal(previous_paths.journal) == previous_journal
    assert read_rebuild_journal(next_paths.journal).candidate.map_source_revision == "e" * 40
    assert compose_calls[0] == ("build", *COMPOSE_BUILT_RUNTIME_SERVICES)
    assert compose_calls[1][-2:] == ("pinvi-admin-bootstrap", "head")


def test_finalized_fixture_receipt_at_manifest_commit_never_allows_reset() -> None:
    journal = _journal_at_runtime_phase("manifest_committing")

    assert journal.phase == "manifest_committing"
    assert journal.cancel_probe.stage == "finalized"
    assert compose_service_module._pinned_runtime_reset_required(journal) is False
