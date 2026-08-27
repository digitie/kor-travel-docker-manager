from __future__ import annotations

import fcntl
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import ANY, Mock, call

import pytest

from kor_travel_docker_manager.services import c6c_deployment
from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
    ComposeTransactionSnapshot,
)
from kor_travel_docker_manager.services.database_runtime import (
    Application300DatabaseIdentity as RuntimeApplication300DatabaseIdentity,
)
from kor_travel_docker_manager.services.database_runtime import (
    DagsterMetadataDatabaseIdentity as RuntimeDagsterMetadataDatabaseIdentity,
)
from kor_travel_docker_manager.services.database_runtime import (
    DagsterMetadataRoleAttributes,
    DatabaseRuntime,
    PinnedDatabaseIdentity,
)
from kor_travel_docker_manager.services.map_application_300 import (
    Application300Contract,
    FreshRootResult,
    MapApplication300ContractError,
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
    PinnedRuntimeDatabaseIdentity,
    PinnedRuntimeGeneration,
    PinnedRuntimeManifest,
    PinnedRuntimeRebuildJournal,
    PinviRoleCatalogResetReceipt,
    PinviRoleLifecycleBlock,
    RebuildPhase,
    RuntimeService,
    journal_from_payload,
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
    if not request.node.name.startswith("test_pinvi_sealed_role_topology_verifier"):
        monkeypatch.setattr(
            ComposeService,
            "_verify_pinned_runtime_pinvi_role_topology",
            lambda _self, *, transaction: None,
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
    # 각 orchestration 회귀는 그 이전/이후 phase만 격리한다. root `.env`를 실제로
    # 바꾸는 fresh role credential 초기화는 전용 unit suite가 소유한다. admission과
    # frozen snapshot 전달 순서는 production과 동일하게 유지한다.
    @contextmanager
    def isolated_rebuild_environment_lock(*, prewrite_admission: Any) -> Any:
        with compose_service_module.pinned_runtime_rebuild_lock():
            snapshot = compose_service_module._capture_compose_environment_snapshot(
                environment_override=None
            )
            # Broad orchestration fixtures intentionally model an already
            # configured role authority; fresh role creation itself belongs to
            # the dedicated credential-boundary suite.
            monkeypatch.setattr(
                compose_service_module,
                "pinvi_role_credentials_are_all_undeclared",
                lambda values: False,
            )
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
        root_fence_directory=tmp_path / "fresh-root-fence",
        finalize_fence_directory=tmp_path / "fresh-finalize-fence",
        application_permit_directory=tmp_path / "application-final-permit",
        metadata_permit_directory=tmp_path / "dagster-storage-permit",
        result_directory=tmp_path / "results",
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


def _application_create_database_identity() -> (
    MapApplication300ApplicationDatabaseIdentity
):
    return MapApplication300ApplicationDatabaseIdentity(
        database_name="kor_travel_map",
        database_oid=127001,
        database_owner="kor_travel_map",
        postgres_system_identifier="7474747474747474747",
    )


def _runtime_application_database_identity() -> RuntimeApplication300DatabaseIdentity:
    return RuntimeApplication300DatabaseIdentity(
        database_name="kor_travel_map",
        database_oid=127001,
        database_owner="ktm_feature_schema_owner",
        postgres_system_identifier="7474747474747474747",
    )


def _runtime_application_create_database_identity() -> (
    RuntimeApplication300DatabaseIdentity
):
    return RuntimeApplication300DatabaseIdentity(
        database_name="kor_travel_map",
        database_oid=127001,
        database_owner="kor_travel_map",
        postgres_system_identifier="7474747474747474747",
    )


def _pinvi_database_identity() -> PinnedRuntimeDatabaseIdentity:
    return PinnedRuntimeDatabaseIdentity(
        system_identifier="8585858585858585858",
        name="pinvi",
        oid=127003,
        owner="pinvi",
        login_role="pinvi",
    )


def _runtime_pinvi_database_identity() -> PinnedDatabaseIdentity:
    identity = _pinvi_database_identity()
    return PinnedDatabaseIdentity(
        system_identifier=identity.system_identifier,
        name=identity.name,
        oid=identity.oid,
        owner=identity.owner,
        login_role=identity.login_role,
    )


def _runtime_application_database() -> DatabaseRuntime:
    return DatabaseRuntime(
        role="map_application",
        container_name="kor-travel-map-postgres",
        port=12700,
        database_name="kor_travel_map",
        owner_name="kor_travel_map",
        admin_name="kor_travel_map",
    )


def _runtime_dagster_metadata_identity(
    *,
    can_login: bool = True,
    inherit: bool = False,
) -> RuntimeDagsterMetadataDatabaseIdentity:
    return RuntimeDagsterMetadataDatabaseIdentity(
        system_identifier="7474747474747474747",
        name="kor_travel_map_dagster",
        oid=127002,
        owner="map_dagster_metadata",
        login_role="map_dagster_metadata",
        login_role_attributes=DagsterMetadataRoleAttributes(
            superuser=False,
            create_database=False,
            create_role=False,
            replication=False,
            bypass_rls=False,
            granted_role_count=0,
            member_role_count=0,
            can_login=can_login,
            inherit=inherit,
        ),
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


def _dagster_storage_receipt(
    journal: PinnedRuntimeRebuildJournal,
    candidate: MapApplication300Candidate,
) -> dict[str, object]:
    identity = (
        journal.map_application_300_execution_evidence
        .dagster_metadata_database_identity
    )
    permit_sha256 = (
        journal.map_application_300_execution_evidence.metadata_permit_sha256
    )
    assert identity is not None
    assert permit_sha256 is not None
    candidate_binding = (
        f"{candidate.dagster_image_id}:{candidate.receipt_sha256}:"
        f"{candidate.dagster_yaml_sha256}"
    )
    return {
        "schema": "kor-travel-map.dagster-storage-migration.v3",
        "status": "migrated",
        "operation_id": journal.transaction_id,
        "permit_sha256": permit_sha256,
        "candidate_sha256": hashlib.sha256(candidate_binding.encode()).hexdigest(),
        "head": journal.candidate.map_dagster_head,
        "version_num": journal.candidate.map_dagster_head,
        "database_name": identity.name,
        "database_oid": str(identity.oid),
        "database_owner": identity.owner,
        "postgres_system_identifier": identity.system_identifier,
        "catalog_sha256": "a" * 64,
    }


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
    *,
    sources: PinnedRuntimeSourceMaterialization | None = None,
) -> PinnedRuntimeRebuildJournal:
    journal = new_candidate_journal(
        candidate=_candidate_generation(sources),
        environment_bytes=b"frozen-env\n",
        compose_source_bytes=b"services: {}\n",
        resolved_compose_sha256="c" * 64,
        created_at="2026-08-06T00:00:00+00:00",
    )
    journal = journal.transition("reset_intent_durable")
    if phase == "reset_intent_durable":
        return journal
    journal = journal.with_databases_recreated(
        pinvi_database_identity=_pinvi_database_identity()
    )
    if phase == "databases_recreated":
        return journal
    journal = journal.with_application_create_intent()
    if phase == "application_create_intent_durable":
        return journal
    journal = journal.with_application_created(
        application_create_database_identity=_application_create_database_identity()
    )
    if phase == "application_created":
        return journal
    journal = journal.with_application_bootstrap_intent()
    if phase == "application_bootstrap_intent_durable":
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


def _journal_at_runtime_phase(
    phase: RebuildPhase,
    *,
    sources: PinnedRuntimeSourceMaterialization | None = None,
) -> PinnedRuntimeRebuildJournal:
    journal = _journal_at_application_300_phase(
        "map_application_ready", sources=sources
    )
    for next_phase in REBUILD_PHASES[
        REBUILD_PHASES.index("map_application_ready") + 1 :
        REBUILD_PHASES.index(phase) + 1
    ]:
        if next_phase == "pinvi_schema_ready" and (
            journal.pinvi_role_catalog_reset
            == PinviRoleCatalogResetReceipt(state="intent")
        ):
            journal = journal.with_pinvi_role_catalog_reset_completed()
        if next_phase == "cancel_probe_finalized":
            for receipt in _cancel_probe_receipts():
                journal = journal.with_cancel_probe(receipt)
        journal = journal.transition(next_phase)
    return journal


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


def test_journal_resume_requires_exact_current_map_candidate_evidence() -> None:
    paired = _map_application_300_candidate()
    journal = new_candidate_journal(
        candidate=_candidate_generation(),
        environment_bytes=b"frozen-env\n",
        compose_source_bytes=b"services: {}\n",
        resolved_compose_sha256="c" * 64,
    )

    ComposeService._assert_pinned_runtime_journal_matches_map_candidate(
        journal,
        map_candidate=paired,
    )
    for changed in (
        replace(paired, receipt_sha256="f" * 64),
        replace(paired, api_image_id=f"sha256:{999:064x}"),
    ):
        with pytest.raises(
            DeploymentContractError,
            match="journal differs from current Map paired candidate",
        ):
            ComposeService._assert_pinned_runtime_journal_matches_map_candidate(
                journal,
                map_candidate=changed,
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


@pytest.mark.parametrize(
    ("api_receipt_exists", "paired_receipt_exists", "verify"),
    (
        (False, False, False),
        (True, False, False),
        (True, True, True),
    ),
)
def test_application_300_paired_builder_accepts_fresh_api_only_and_complete_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    api_receipt_exists: bool,
    paired_receipt_exists: bool,
    verify: bool,
) -> None:
    sources, paths = _paired_builder_inputs(tmp_path)
    if api_receipt_exists:
        paths.api_receipt.write_text("{}\n", encoding="utf-8")
        paths.api_receipt.chmod(0o600)
    if paired_receipt_exists:
        paths.paired_receipt.write_text("{}\n", encoding="utf-8")
        paths.paired_receipt.chmod(0o600)
    runner = Mock(return_value=subprocess.CompletedProcess(args=(), returncode=0))
    monkeypatch.setattr(compose_service_module.subprocess, "run", runner)

    compose_service_module._run_map_application_300_paired_builder(
        sources=sources,
        api_image="map-api:test",
        dagster_image="map-dagster:test",
        paths=paths,
        resume_journal=verify,
    )

    command = runner.call_args.args[0]
    assert ("--verify" in command) is verify
    assert command[command.index("--api-receipt") + 1] == str(paths.api_receipt)
    assert command[command.index("--receipt") + 1] == str(paths.paired_receipt)
    assert runner.call_args.kwargs["stdout"] is subprocess.DEVNULL
    assert runner.call_args.kwargs["stderr"] is subprocess.DEVNULL
    assert "capture_output" not in runner.call_args.kwargs


@pytest.mark.parametrize(
    ("api_receipt", "paired_receipt", "expected"),
    (
        (False, False, "api_receipt_missing"),
        (True, False, "paired_receipt_missing"),
        (False, True, "unclassified"),
        (True, True, "unclassified"),
    ),
)
def test_application_300_paired_builder_failure_code_uses_only_receipt_state(
    tmp_path: Path,
    *,
    api_receipt: bool,
    paired_receipt: bool,
    expected: str,
) -> None:
    _, paths = _paired_builder_inputs(tmp_path)
    for receipt_path, should_exist in (
        (paths.api_receipt, api_receipt),
        (paths.paired_receipt, paired_receipt),
    ):
        if should_exist:
            receipt_path.write_text("{}\n", encoding="utf-8")
            receipt_path.chmod(0o600)

    assert (
        compose_service_module._map_application_300_builder_failure_code(paths)
        == expected
    )


def test_application_300_paired_builder_failure_code_rejects_unsafe_receipt(
    tmp_path: Path,
) -> None:
    _, paths = _paired_builder_inputs(tmp_path)
    paths.api_receipt.write_text("{}\n", encoding="utf-8")
    paths.api_receipt.chmod(0o644)

    assert (
        compose_service_module._map_application_300_builder_failure_code(paths)
        == "unclassified"
    )


def test_application_300_paired_builder_failure_never_leaks_builder_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, paths = _paired_builder_inputs(tmp_path)
    raw_output = "build-application-300-candidate: password=not-a-real-secret"
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            args=(), returncode=1, stdout=raw_output, stderr=""
        )
    )
    monkeypatch.setattr(compose_service_module.subprocess, "run", runner)

    with pytest.raises(DeploymentContractError) as exc_info:
        compose_service_module._run_map_application_300_paired_builder(
            sources=sources,
            api_image="map-api:test",
            dagster_image="map-dagster:test",
            paths=paths,
            resume_journal=False,
        )

    assert str(exc_info.value) == (
        "application 300 paired builder failed: api_receipt_missing"
    )
    assert raw_output not in str(exc_info.value)


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


def test_application_300_paired_builder_rejects_paired_only_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, paths = _paired_builder_inputs(tmp_path)
    paths.paired_receipt.write_text("{}\n", encoding="utf-8")
    runner = Mock()
    monkeypatch.setattr(compose_service_module.subprocess, "run", runner)

    with pytest.raises(
        DeploymentContractError,
        match="journal resume requires a complete receipt set",
    ):
        compose_service_module._run_map_application_300_paired_builder(
            sources=sources,
            api_image="map-api:test",
            dagster_image="map-dagster:test",
            paths=paths,
            resume_journal=True,
        )

    runner.assert_not_called()


def test_application_300_journal_resume_requires_both_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, paths = _paired_builder_inputs(tmp_path)
    paths.api_receipt.write_text("{}\n", encoding="utf-8")
    paths.api_receipt.chmod(0o600)
    runner = Mock()
    monkeypatch.setattr(compose_service_module.subprocess, "run", runner)

    with pytest.raises(
        DeploymentContractError,
        match="journal resume requires a complete receipt set",
    ):
        compose_service_module._run_map_application_300_paired_builder(
            sources=sources,
            api_image="map-api:test",
            dagster_image="map-dagster:test",
            paths=paths,
            resume_journal=True,
        )

    runner.assert_not_called()


@pytest.mark.parametrize("unsafe_api_receipt", ("symlink", "foreign-owner"))
def test_application_300_unsafe_stale_receipt_is_not_discarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_api_receipt: str,
) -> None:
    sources, paths = _paired_builder_inputs(tmp_path)
    if unsafe_api_receipt == "symlink":
        target = tmp_path / "foreign-api.json"
        target.write_text("{}\n", encoding="utf-8")
        paths.api_receipt.symlink_to(target)
    else:
        paths.api_receipt.write_text("{}\n", encoding="utf-8")
        paths.api_receipt.chmod(0o600)
        original_lstat = Path.lstat

        def foreign_api_lstat(path: Path) -> os.stat_result:
            metadata = original_lstat(path)
            if path != paths.api_receipt:
                return metadata
            fields = list(metadata)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)

        monkeypatch.setattr(Path, "lstat", foreign_api_lstat)
    runner = Mock()
    monkeypatch.setattr(compose_service_module.subprocess, "run", runner)

    with pytest.raises(DeploymentContractError, match="stale candidate receipt is unsafe"):
        compose_service_module._run_map_application_300_paired_builder(
            sources=sources,
            api_image="map-api:test",
            dagster_image="map-dagster:test",
            paths=paths,
            resume_journal=False,
        )

    runner.assert_not_called()


def test_application_300_prejournal_receipts_are_discarded_before_fresh_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, paths = _paired_builder_inputs(tmp_path)
    for receipt_path in (paths.api_receipt, paths.paired_receipt):
        receipt_path.write_text("{}\n", encoding="utf-8")
        receipt_path.chmod(0o600)
    runner = Mock(return_value=subprocess.CompletedProcess(args=(), returncode=0))
    monkeypatch.setattr(compose_service_module.subprocess, "run", runner)

    compose_service_module._run_map_application_300_paired_builder(
        sources=sources,
        api_image="map-api:test",
        dagster_image="map-dagster:test",
        paths=paths,
        resume_journal=False,
    )

    assert not paths.api_receipt.exists()
    assert not paths.paired_receipt.exists()
    assert "--verify" not in runner.call_args.args[0]


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


def test_committed_resume_revalidates_all_database_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    journal = _journal_at_runtime_phase("committed")
    runtimes = (object(), object(), object())
    monkeypatch.setattr(
        compose_service_module,
        "read_application_300_database_identity",
        lambda _runtime: _runtime_application_database_identity(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_application_300_dagster_metadata_identity",
        lambda _runtime, *, metadata_user: _runtime_dagster_metadata_identity(),
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_pinned_database_identity",
        lambda _runtime: _runtime_pinvi_database_identity(),
    )

    service._assert_committed_application_database_identities(
        runtimes,
        journal=journal,
        metadata_user="map_dagster_metadata",
    )

    monkeypatch.setattr(
        compose_service_module,
        "read_application_300_database_identity",
        lambda _runtime: replace(
            _runtime_application_database_identity(),
            database_oid=127999,
        ),
    )
    with pytest.raises(DeploymentContractError, match="application database identity"):
        service._assert_committed_application_database_identities(
            runtimes,
            journal=journal,
            metadata_user="map_dagster_metadata",
        )


def test_committed_resume_revalidates_both_postgres_container_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    map_candidate = _map_application_300_candidate()
    pinvi_image = "sha256:" + "f" * 64
    transaction = SimpleNamespace(
        resolved={
            "services": {
                "pinvi-postgres": {
                    "image": "postgres:16@sha256:" + "1" * 64,
                }
            }
        }
    )
    records = (
        {"Service": "kor-travel-map-postgres", "Name": "map-postgres"},
        {"Service": "pinvi-postgres", "Name": "pinvi-postgres"},
    )
    monkeypatch.setattr(
        service,
        "_inspect_image_reference_id",
        lambda image_reference, *, label: pinvi_image,
    )
    observed = {
        "map-postgres": map_candidate.postgres_image_id,
        "pinvi-postgres": pinvi_image,
    }
    monkeypatch.setattr(
        service,
        "_inspect_container_image_id",
        lambda container_name, *, label: observed[container_name],
    )

    service._assert_committed_postgres_images(
        records,
        transaction=transaction,
        map_candidate=map_candidate,
    )

    observed["pinvi-postgres"] = "sha256:" + "e" * 64
    with pytest.raises(DeploymentContractError, match="pinvi-postgres runtime image"):
        service._assert_committed_postgres_images(
            records,
            transaction=transaction,
            map_candidate=map_candidate,
        )


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
    monkeypatch.setattr(service, "_require_services_ready", Mock(return_value=[]))
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


def test_sealed_role_topology_verifier_is_a_fresh_target_state_postcondition() -> None:
    """폐기할 기존 DB가 아니라 role bootstrap 뒤 fresh PinVi DB만 검증한다."""

    source = inspect.getsource(ComposeService.rebuild_pinned_runtime)

    assert source.index("reset_databases_for_application_300") < source.index(
        "_run_pinvi_schema_bootstrap_with_role_lifecycle"
    )
    assert source.index("_run_pinvi_schema_bootstrap_with_role_lifecycle") < source.index(
        "_verify_pinned_runtime_pinvi_role_topology_after_bootstrap"
    )
    assert source.index("_verify_pinned_runtime_pinvi_role_topology_after_bootstrap") < source.index(
        'journal, "pinvi_schema_ready"'
    )
    assert "_verify_pinned_runtime_pinvi_role_topology(\n                transaction=candidate_transaction" not in source


def test_fresh_role_catalog_reset_uses_only_manager_permit_and_current_identity(
    monkeypatch: pytest.MonkeyPatch,
    linux_tmp_path: Path,
) -> None:
    service = ComposeService()
    journal = replace(
        _journal_at_runtime_phase("map_runtime_ready"),
        pinvi_role_catalog_reset=PinviRoleCatalogResetReceipt(state="intent"),
    )
    writes = Mock()

    def write_artifact(path: Path, raw: bytes) -> None:
        path.write_bytes(raw)

    run_compose = Mock(return_value={"success": True, "stdout": ""})
    monkeypatch.setattr(compose_service_module, "read_pinned_database_identity", Mock(return_value=journal.pinvi_database_identity))
    writes.side_effect = write_artifact
    monkeypatch.setattr(compose_service_module, "write_owner_only_artifact", writes)
    monkeypatch.setattr(
        compose_service_module,
        "read_owner_only_artifact",
        Mock(
            return_value=(
                b'{"schema":"pinvi.role-catalog-reset-diagnostic.v1",'
                b'"status":"completed","class":"completed",'
                + f'"transaction":"{journal.transaction_id}",'.encode()
                + f'"pinset":"{journal.candidate.pinset_sha256}"}}'.encode()
            )
        ),
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    service._run_pinvi_fresh_role_catalog_reset(
        transaction=cast(ComposeTransactionSnapshot, SimpleNamespace()),
        state_paths=cast(Any, SimpleNamespace(state_root=linux_tmp_path)),
        journal=journal,
        runtime=cast(DatabaseRuntime, SimpleNamespace()),
    )

    permit_path, permit = writes.call_args_list[0].args
    assert permit_path.parent == linux_tmp_path
    assert permit == (
        "pinvi-role-catalog-reset-v1|"
        f"{journal.transaction_id}|{journal.candidate.pinset_sha256}|"
        f"{journal.pinvi_database_identity.system_identifier}|"
        f"{journal.pinvi_database_identity.oid}|pinvi|pinvi\n"
    ).encode()
    result_path, result = writes.call_args_list[1].args
    assert result_path.parent == linux_tmp_path
    assert result == b"{}"
    assert run_compose.call_args.args[0] == [
        "--profile",
        "bootstrap",
        "run",
        "--rm",
        "--no-deps",
            "-v",
            f"{permit_path}:/run/pinvi/role-catalog-reset.permit:ro",
            "-v",
            f"{result_path}:/run/pinvi/role-catalog-reset.result",
        "-e",
        "PINVI_ROLE_CATALOG_RESET_ONLY=1",
        "-e",
            "PINVI_ROLE_CATALOG_RESET_PERMIT_FILE=/run/pinvi/role-catalog-reset.permit",
            "-e",
            "PINVI_ROLE_CATALOG_RESET_RESULT_FILE=/run/pinvi/role-catalog-reset.result",
        "pinvi-db-runtime-role",
    ]
    assert run_compose.call_args.kwargs["capture_output"] is False
    assert run_compose.call_args.kwargs["allow_typed_error_diagnostic"] is False


def test_fresh_role_catalog_reset_identity_mismatch_is_terminal_before_compose(
    monkeypatch: pytest.MonkeyPatch,
    linux_tmp_path: Path,
) -> None:
    service = ComposeService()
    journal = replace(
        _journal_at_runtime_phase("map_runtime_ready"),
        pinvi_role_catalog_reset=PinviRoleCatalogResetReceipt(state="intent"),
    )
    writes = Mock()
    run_compose = Mock()
    monkeypatch.setattr(compose_service_module, "read_pinned_database_identity", Mock(return_value=None))
    monkeypatch.setattr(compose_service_module, "write_owner_only_artifact", writes)
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    with pytest.raises(compose_service_module._PinviRoleLifecycleError) as captured:
        service._run_pinvi_fresh_role_catalog_reset(
            transaction=cast(ComposeTransactionSnapshot, SimpleNamespace()),
            state_paths=cast(Any, SimpleNamespace(state_root=linux_tmp_path)),
            journal=journal,
            runtime=cast(DatabaseRuntime, SimpleNamespace()),
        )

    assert captured.value.role_topology_block == PinviRoleLifecycleBlock(
        stage="pinvi_role_catalog_reset",
        code="role_catalog_reset_failed",
    )
    writes.assert_not_called()
    run_compose.assert_not_called()


@pytest.mark.parametrize(
    ("status", "result_class", "expected"),
    [
        ("completed", "completed", "completed"),
        ("failed", "lifecycle_invalid", "lifecycle_invalid"),
        ("failed", "target_not_isolated", "target_not_isolated"),
        ("failed", "permit_invalid", "unclassified"),
        ("failed", "untrusted", "unclassified"),
    ],
)
def test_fresh_role_catalog_reset_receipt_parser_is_fixed_and_bound(
    status: str,
    result_class: str,
    expected: str,
) -> None:
    raw = (
        "{"
        '\"schema\":\"pinvi.role-catalog-reset-diagnostic.v1\",'
        f'\"status\":\"{status}\",'
        f'\"class\":\"{result_class}\",'
        '\"transaction\":\"transaction\",'
        '\"pinset\":\"pinset\"'
        "}"
    ).encode()

    assert compose_service_module._parse_pinvi_role_catalog_reset_result(
        raw, transaction_id="transaction", pinset_sha256="pinset"
    ) == expected
    assert compose_service_module._parse_pinvi_role_catalog_reset_result(
        raw, transaction_id="other-transaction", pinset_sha256="pinset"
    ) == "unclassified"
    assert compose_service_module._parse_pinvi_role_catalog_reset_result(
        raw + b'\n{\"unexpected\":true}',
        transaction_id="transaction",
        pinset_sha256="pinset",
    ) == "unclassified"


def test_fresh_role_catalog_reset_receipt_read_failure_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
    linux_tmp_path: Path,
) -> None:
    service = ComposeService()
    journal = replace(
        _journal_at_runtime_phase("map_runtime_ready"),
        pinvi_role_catalog_reset=PinviRoleCatalogResetReceipt(state="intent"),
    )

    def write_artifact(path: Path, raw: bytes) -> None:
        path.write_bytes(raw)

    monkeypatch.setattr(
        compose_service_module,
        "read_pinned_database_identity",
        Mock(return_value=journal.pinvi_database_identity),
    )
    monkeypatch.setattr(compose_service_module, "write_owner_only_artifact", write_artifact)
    monkeypatch.setattr(
        compose_service_module,
        "read_owner_only_artifact",
        Mock(side_effect=MapApplication300ContractError("unsafe artifact")),
    )
    monkeypatch.setattr(
        service,
        "_run_pinned_runtime_rebuild_compose",
        Mock(return_value={"success": True, "stdout": ""}),
    )

    with pytest.raises(compose_service_module._PinviRoleLifecycleError) as captured:
        service._run_pinvi_fresh_role_catalog_reset(
            transaction=cast(ComposeTransactionSnapshot, SimpleNamespace()),
            state_paths=cast(Any, SimpleNamespace(state_root=linux_tmp_path)),
            journal=journal,
            runtime=cast(DatabaseRuntime, SimpleNamespace()),
        )

    assert captured.value.role_topology_block == PinviRoleLifecycleBlock(
        stage="pinvi_role_catalog_reset",
        code="role_catalog_reset_failed",
        diagnostic="unclassified",
    )


def test_post_bootstrap_sealed_role_topology_failure_is_typed_and_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    raw_refusal = DeploymentContractError(
        "PinVi sealed role topology is noncanonical"
    )
    verifier = Mock(side_effect=raw_refusal)
    monkeypatch.setattr(service, "_verify_pinned_runtime_pinvi_role_topology", verifier)

    with pytest.raises(compose_service_module._PinviRoleLifecycleError) as captured:
        service._verify_pinned_runtime_pinvi_role_topology_after_bootstrap(
            transaction=cast(ComposeTransactionSnapshot, SimpleNamespace())
        )

    assert str(captured.value) == "PinVi sealed role topology verification failed"
    assert captured.value.role_topology_block == PinviRoleLifecycleBlock(
        stage="pinvi_role_verify",
        code="role_topology_noncanonical",
    )
    verifier.assert_called_once()


def test_post_bootstrap_topology_terminal_receipt_stops_runtime_and_blocks_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    journal = _journal_at_runtime_phase("map_runtime_ready")
    failure = compose_service_module._PinviRoleLifecycleError(
        "PinVi sealed role topology verification failed",
        role_topology_block=PinviRoleLifecycleBlock(
            stage="pinvi_role_verify",
            code="role_topology_noncanonical",
        ),
    )
    written: list[PinnedRuntimeRebuildJournal] = []
    run_compose = Mock(return_value={"success": True, "stdout": ""})
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_runtime_rebuild_journal",
        lambda _path, value: written.append(value),
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    with pytest.raises(compose_service_module._PinviRoleLifecycleError) as captured:
        service._terminate_pinned_runtime_after_pinvi_role_topology_failure(
            journal=journal,
            journal_path=tmp_path / "journal.json",
            transaction=cast(ComposeTransactionSnapshot, SimpleNamespace()),
            error=failure,
        )

    assert captured.value is failure
    assert written == [
        journal.with_pinvi_role_lifecycle_block(failure.role_topology_block)
    ]
    run_compose.assert_called_once_with(
        ["stop", *RUNTIME_SERVICES],
        transaction=ANY,
    )
    with pytest.raises(
        DeploymentContractError,
        match="blocked by durable PinVi role topology failure",
    ):
        ComposeService._assert_pinvi_role_lifecycle_block_admission(written[0])


def test_external_prerequisite_refusal_precedes_source_and_candidate_mutation(
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
        "COMPOSE_PROJECT_NAME": "f1d-prerequisite-refusal",
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
    materialize = Mock()
    paired_builder = Mock()
    journal_write = Mock()

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: nullcontext(object()),
    )
    monkeypatch.setattr(
        compose_service_module, "_require_pinned_runtime_rebuild_root", lambda: None
    )
    monkeypatch.setattr(
        compose_service_module,
        "_capture_compose_environment_snapshot",
        lambda *, environment_override: transaction.environment,
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        lambda **_kwargs: (transaction, None),
    )
    monkeypatch.setattr(
        compose_service_module, "_assert_transaction_matches_c6c_lock", Mock()
    )
    monkeypatch.setattr(
        service,
        "_require_services_ready",
        Mock(side_effect=DeploymentContractError("external prerequisite unavailable")),
    )
    monkeypatch.setattr(
        compose_service_module, "materialize_pinned_runtime_sources", materialize
    )
    monkeypatch.setattr(
        compose_service_module,
        "_run_map_application_300_paired_builder",
        paired_builder,
    )
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_runtime_rebuild_journal",
        journal_write,
    )

    with pytest.raises(
        DeploymentContractError, match="external prerequisite unavailable"
    ):
        service.rebuild_pinned_runtime()

    materialize.assert_not_called()
    paired_builder.assert_not_called()
    journal_write.assert_not_called()


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
    ("stderr", "expected"),
    (
        (
            "runtime/migrator/migration-owner role topology is not canonical",
            "role_topology_noncanonical",
        ),
        (
            "pinvi-db-runtime-role-1 | "
            "Postgres TCP endpoint did not become ready for DB role bootstrap",
            "role_endpoint_not_ready",
        ),
        (
            "pinvi-db-runtime-role | invalid PostgreSQL role name",
            "role_input_invalid",
        ),
        (
            "pinvi-db-runtime-role-1 | "
            "existing app objects are not owned by PINVI_APP_SCHEMA_OWNER; "
            "use the approved root-only legacy rebaseline profile",
            "role_existing_owner_noncanonical",
        ),
    ),
)
def test_rebuild_compose_error_exposes_only_allowlisted_pinvi_role_code(
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
    expected: str,
) -> None:
    service = ComposeService()
    secret = "test-pinvi-role-diagnostic-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": f"{secret}\n{stderr}",
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=rf"Compose run command failed \(exit 1; pinvi_role:{expected}\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "pinvi-db-runtime-role"],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


def test_rebuild_compose_error_keeps_unclassified_pinvi_role_output_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    secret = "test-pinvi-role-unclassified-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_frozen_recovery",
        Mock(
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": secret,
                "stderr": f"psql: error: {secret}",
            }
        ),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"Compose run command failed \(exit 1; pinvi_role:unclassified\)",
    ) as captured:
        service._run_pinned_runtime_rebuild_compose(
            ["run", "--rm", "--no-deps", "pinvi-db-runtime-role"],
            transaction=_opaque_transaction(),
        )

    assert secret not in str(captured.value)


def test_pinvi_sealed_role_topology_verifier_accepts_only_canonical_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    operations: list[tuple[str, ...]] = []

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operations.append(tuple(args))
        return {
            "success": True,
            "stdout": (
                '{"schema":"pinvi.role-topology-diagnostic.v1",'
                '"status":"canonical","mode":"sealed","reasons":[]}\n'
            ),
        }

    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    service._verify_pinned_runtime_pinvi_role_topology(
        transaction=cast(Any, SimpleNamespace()),
    )

    assert operations == [
        (
            "--profile",
            "bootstrap",
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "PINVI_ROLE_TOPOLOGY_VERIFY_ONLY=1",
            "-e",
            "PINVI_MIGRATOR_DISABLE_LOGIN=1",
            "-e",
            "PINVI_M05_LEGACY_REBASELINE=0",
            "pinvi-db-runtime-role",
        )
    ]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            '{"schema":"pinvi.role-topology-diagnostic.v1",'
            '"status":"noncanonical","mode":"sealed",'
            '"reasons":["principal_identity","runtime_role"]}\n',
            "PinVi sealed role topology is noncanonical",
        ),
        (
            '{"schema":"pinvi.role-topology-diagnostic.v1",'
            '"status":"unavailable","mode":"sealed",'
            '"reasons":["endpoint_unavailable"]}\n',
            "PinVi sealed role topology verifier is unavailable",
        ),
        (
            '{"schema":"pinvi.role-topology-diagnostic.v1",'
            '"status":"noncanonical","mode":"sealed",'
            '"reasons":["unknown_reason"]}\n',
            "PinVi sealed role topology verifier is unavailable",
        ),
    ],
)
def test_pinvi_sealed_role_topology_verifier_keeps_output_private(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    expected: str,
) -> None:
    service = ComposeService()
    secret = "pinvi-role-topology-verifier-output-must-not-leak"
    monkeypatch.setattr(
        service,
        "_run_pinned_runtime_rebuild_compose",
        Mock(return_value={"success": True, "stdout": output, "stderr": secret}),
    )

    with pytest.raises(DeploymentContractError, match=expected) as captured:
        service._verify_pinned_runtime_pinvi_role_topology(
            transaction=cast(Any, SimpleNamespace()),
        )

    assert secret not in str(captured.value)


def test_pinvi_role_lifecycle_reports_primary_and_seal_failures_without_raw_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    transaction = cast(Any, SimpleNamespace())
    state_paths = cast(Any, SimpleNamespace())
    values = {
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "test-admin-password",
    }
    operations: list[tuple[str, ...]] = []

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operation = tuple(args)
        operations.append(operation)
        if operation[-2:] == ("PINVI_MIGRATOR_DISABLE_LOGIN=0", "pinvi-db-runtime-role"):
            raise DeploymentContractError(
                "pinned runtime rebuild Compose run command failed "
                "(exit 1; pinvi_role:role_topology_noncanonical)"
            )
        if operation[-2:] == ("PINVI_MIGRATOR_DISABLE_LOGIN=1", "pinvi-db-runtime-role"):
            raise DeploymentContractError(
                "pinned runtime rebuild Compose run command failed "
                "(exit 1; pinvi_role:unclassified)"
            )
        return {"success": True, "stdout": ""}

    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    with pytest.raises(
        DeploymentContractError,
        match=(
            r"PinVi bootstrap failed at pinvi_role_open \(role_topology_noncanonical\); "
            r"migrator seal also failed at pinvi_role_seal \(unclassified\)"
        ),
    ) as captured:
        service._run_pinvi_schema_bootstrap_with_role_lifecycle(
            transaction=transaction,
            state_paths=state_paths,
            values=values,
            transaction_id="transaction-id",
        )

    assert "test-admin-password" not in str(captured.value)
    assert isinstance(captured.value, compose_service_module._PinviRoleLifecycleError)
    assert captured.value.role_topology_block == PinviRoleLifecycleBlock(
        stage="pinvi_role_open",
        code="role_topology_noncanonical",
    )
    assert [operation[-1] for operation in operations] == [
        "pinvi-db-runtime-role",
        "pinvi-db-runtime-role",
    ]


def test_pinvi_role_lifecycle_preserves_cancellation_after_successful_seal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    operations: list[tuple[str, ...]] = []

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operation = tuple(args)
        operations.append(operation)
        if operation[-2:] == ("PINVI_MIGRATOR_DISABLE_LOGIN=0", "pinvi-db-runtime-role"):
            raise KeyboardInterrupt()
        return {"success": True, "stdout": ""}

    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    with pytest.raises(KeyboardInterrupt):
        service._run_pinvi_schema_bootstrap_with_role_lifecycle(
            transaction=cast(Any, SimpleNamespace()),
            state_paths=cast(Any, SimpleNamespace()),
            values={
                "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
                "KTDM_C6C_PINVI_ADMIN_PASSWORD": "test-admin-password",
            },
            transaction_id="transaction-id",
        )

    assert [operation[-2:] for operation in operations] == [
        ("PINVI_MIGRATOR_DISABLE_LOGIN=0", "pinvi-db-runtime-role"),
        ("PINVI_MIGRATOR_DISABLE_LOGIN=1", "pinvi-db-runtime-role"),
    ]


def test_pinvi_role_lifecycle_separates_credential_preparation_from_admin_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    operations: list[tuple[str, ...]] = []
    secret = "credential-path-or-secret-must-not-leak"

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operations.append(tuple(args))
        return {"success": True, "stdout": ""}

    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)
    monkeypatch.setattr(
        compose_service_module,
        "pinvi_bootstrap_credential_file",
        Mock(side_effect=DeploymentContractError(secret)),
    )

    with pytest.raises(
        DeploymentContractError,
        match=r"PinVi bootstrap failed at pinvi_bootstrap_credential \(unclassified\)",
    ) as captured:
        service._run_pinvi_schema_bootstrap_with_role_lifecycle(
            transaction=cast(Any, SimpleNamespace()),
            state_paths=cast(Any, SimpleNamespace()),
            values={
                "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
                "KTDM_C6C_PINVI_ADMIN_PASSWORD": "test-admin-password",
            },
            transaction_id="transaction-id",
        )

    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert [operation[-1] for operation in operations] == [
        "pinvi-db-runtime-role",
        "pinvi-db-runtime-role",
    ]


def test_pinvi_role_lifecycle_separates_credential_cleanup_from_admin_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    operations: list[tuple[str, ...]] = []
    secret = "credential-cleanup-path-or-secret-must-not-leak"

    @contextmanager
    def credential_file(**_kwargs: object):
        yield SimpleNamespace(path=Path("/run/manager/credential.json"))
        raise DeploymentContractError(secret)

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operations.append(tuple(args))
        return {"success": True, "stdout": ""}

    monkeypatch.setattr(
        compose_service_module, "pinvi_bootstrap_credential_file", credential_file
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    with pytest.raises(
        DeploymentContractError,
        match=(
            r"PinVi bootstrap failed at pinvi_bootstrap_credential_cleanup "
            r"\(unclassified\)"
        ),
    ) as captured:
        service._run_pinvi_schema_bootstrap_with_role_lifecycle(
            transaction=cast(Any, SimpleNamespace()),
            state_paths=cast(Any, SimpleNamespace()),
            values={
                "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
                "KTDM_C6C_PINVI_ADMIN_PASSWORD": "test-admin-password",
            },
            transaction_id="transaction-id",
        )

    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert [operation[-1] for operation in operations] == [
        "pinvi-db-runtime-role",
        "pinvi-admin-bootstrap",
        "pinvi-db-runtime-role",
    ]


def test_pinvi_role_lifecycle_keeps_admin_and_seal_codes_without_raw_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    operations: list[tuple[str, ...]] = []
    secret = "admin-or-seal-raw-output-must-not-leak"

    @contextmanager
    def credential_file(**_kwargs: object):
        yield SimpleNamespace(path=Path("/run/manager/credential.json"))

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operation = tuple(args)
        operations.append(operation)
        if operation[-1] == "pinvi-admin-bootstrap":
            raise DeploymentContractError(
                "pinned runtime rebuild Compose run command failed "
                "(exit 1; pinvi:migration_failed)"
            )
        if operation[-2:] == ("PINVI_MIGRATOR_DISABLE_LOGIN=1", "pinvi-db-runtime-role"):
            raise DeploymentContractError(
                "pinned runtime rebuild Compose run command failed "
                "(exit 1; pinvi_role:role_topology_noncanonical); "
                f"raw={secret}"
            )
        return {"success": True, "stdout": ""}

    monkeypatch.setattr(
        compose_service_module, "pinvi_bootstrap_credential_file", credential_file
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    with pytest.raises(
        DeploymentContractError,
        match=(
            r"PinVi bootstrap failed at pinvi_admin_bootstrap \(migration_failed\); "
            r"migrator seal also failed at pinvi_role_seal \(role_topology_noncanonical\)"
        ),
    ) as captured:
        service._run_pinvi_schema_bootstrap_with_role_lifecycle(
            transaction=cast(Any, SimpleNamespace()),
            state_paths=cast(Any, SimpleNamespace()),
            values={
                "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
                "KTDM_C6C_PINVI_ADMIN_PASSWORD": "test-admin-password",
            },
            transaction_id="transaction-id",
        )

    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert isinstance(captured.value, compose_service_module._PinviRoleLifecycleError)
    assert captured.value.role_topology_block == PinviRoleLifecycleBlock(
        stage="pinvi_role_seal",
        code="role_topology_noncanonical",
    )
    assert [operation[-1] for operation in operations] == [
        "pinvi-db-runtime-role",
        "pinvi-admin-bootstrap",
        "pinvi-db-runtime-role",
    ]


def test_role_topology_lifecycle_failure_is_durably_recorded_before_rethrow(
    linux_tmp_path: Path,
) -> None:
    journal = _journal_at_runtime_phase("map_runtime_ready")
    journal_path = linux_tmp_path / "pinned-runtime-rebuild-v8.json"
    failure = compose_service_module._PinviRoleLifecycleError(
        "PinVi bootstrap failed at pinvi_role_open (role_topology_noncanonical)",
        role_topology_block=PinviRoleLifecycleBlock(
            stage="pinvi_role_open",
            code="role_topology_noncanonical",
        ),
    )

    updated = ComposeService._record_pinvi_role_lifecycle_block(
        journal,
        journal_path=journal_path,
        error=failure,
    )

    assert updated.journal_generation == journal.journal_generation + 1
    assert updated.pinvi_role_lifecycle_block == failure.role_topology_block
    assert read_rebuild_journal(journal_path) == updated


def test_terminal_role_topology_block_precedes_source_or_database_mutation(
    monkeypatch: pytest.MonkeyPatch,
    linux_tmp_path: Path,
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "COMPOSE_PROJECT_NAME": "f1d-terminal-role-block",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(linux_tmp_path / "state"),
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
    }
    environment = SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n")
    state_paths = pinned_runtime_state_paths(
        values,
        pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
    )
    state_paths.state_root.mkdir(parents=True, mode=0o700)
    journal = _journal_at_runtime_phase("map_runtime_ready").with_pinvi_role_lifecycle_block(
        PinviRoleLifecycleBlock(
            stage="pinvi_role_open",
            code="role_topology_noncanonical",
        )
    )
    write_rebuild_journal(state_paths.journal, journal)
    materialize = Mock()
    reset_databases = Mock()

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
    monkeypatch.setattr(
        compose_service_module,
        "reset_databases_for_application_300",
        reset_databases,
    )

    with pytest.raises(
        DeploymentContractError,
        match="blocked by durable PinVi role topology failure",
    ):
        ComposeService().rebuild_pinned_runtime()

    materialize.assert_not_called()
    reset_databases.assert_not_called()


def test_legacy_d9_role_topology_failure_precedes_credential_or_source_mutation(
    monkeypatch: pytest.MonkeyPatch,
    linux_tmp_path: Path,
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "COMPOSE_PROJECT_NAME": "f1d-legacy-role-block",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(linux_tmp_path / "state"),
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
    }
    environment = SimpleNamespace(effective=values, env_file_bytes=b"frozen-env\n")
    legacy_sources = (
        PinnedRuntimeSourceSpec(
            role="map",
            canonical_url=CANONICAL_RUNTIME_SOURCE_URLS["map"],
            revision="14d18230e5a9ff21caf26d6abe37aed1e4944685",
        ),
        PinnedRuntimeSourceSpec(
            role="pinvi",
            canonical_url=CANONICAL_RUNTIME_SOURCE_URLS["pinvi"],
            revision="93296aee5d47676e6b9b79303bf417c598a273ac",
        ),
    )
    legacy_release = PinnedRuntimeRelease(
        version=5,
        sources=legacy_sources,
        pinset_sha256=canonical_pinset_sha256(version=5, sources=legacy_sources),
    )
    state_paths = pinned_runtime_state_paths(
        values,
        pinset_sha256=legacy_release.pinset_sha256,
    )
    state_paths.state_root.mkdir(parents=True, mode=0o700)
    payload = _journal_at_runtime_phase(
        "map_runtime_ready", sources=_sources_for(legacy_release)
    ).to_payload()
    del payload["pinvi_role_lifecycle_block"]
    legacy_journal = journal_from_payload(payload)
    assert legacy_journal.pinvi_role_lifecycle_block is None
    write_rebuild_journal(state_paths.journal, legacy_journal)
    credential_write = Mock()
    materialize = Mock()
    paired_builder = Mock()
    external_ready = Mock()
    reset_databases = Mock()

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
        "current_pinned_runtime_release",
        lambda: legacy_release,
    )
    monkeypatch.setattr(
        compose_service_module,
        "ensure_pinned_runtime_pinvi_role_credentials",
        credential_write,
    )
    monkeypatch.setattr(
        compose_service_module,
        "materialize_pinned_runtime_sources",
        materialize,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_run_map_application_300_paired_builder",
        paired_builder,
    )
    monkeypatch.setattr(
        ComposeService,
        "_require_services_ready",
        external_ready,
    )
    monkeypatch.setattr(
        compose_service_module,
        "reset_databases_for_application_300",
        reset_databases,
    )

    with pytest.raises(
        DeploymentContractError,
        match="blocked by durable PinVi role topology failure",
    ):
        ComposeService().rebuild_pinned_runtime()

    credential_write.assert_not_called()
    materialize.assert_not_called()
    paired_builder.assert_not_called()
    external_ready.assert_not_called()
    reset_databases.assert_not_called()


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
    static_entrypoints: list[str | None] = []
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
    external_readiness = Mock(return_value=[])
    monkeypatch.setattr(service, "_require_services_ready", external_readiness)
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
    def static_command(
        _image: str,
        command: tuple[str, ...],
        *,
        label: str,
        entrypoint: str | None = None,
    ) -> str:
        del label
        static_commands.append(command)
        static_entrypoints.append(entrypoint)
        return {
            "head": '{"head":"map-dagster-head","schema":"kor-travel-map.dagster-storage-head.v1"}\n',
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
    artifact_root = tmp_path / "application-300"
    assert captured[0] == {
        "KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR": str(
            artifact_root / "fresh-root-fence"
        ),
        "KOR_TRAVEL_MAP_APPLICATION_FRESH_FINALIZE_FENCE_DIR": str(
            artifact_root / "fresh-finalize-fence"
        ),
        "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DIR": str(
            artifact_root / "application-final-permit"
        ),
        "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR": str(
            artifact_root / "dagster-storage-permit"
        ),
    }
    assert captured[1] is not None
    assert captured[1]["KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD"] == "300"
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
        ("head",),
        ("pinvi-admin-bootstrap", "head"),
    ]
    assert static_entrypoints == [
        "/usr/local/bin/ktm-dagster-storage",
        None,
    ]
    paired_builder.assert_called_once()
    assert paired_builder.call_args.kwargs["resume_journal"] is False
    candidate_contract.assert_called_once()
    assert external_readiness.call_args_list == [
        call(
            ("rustfs", "kor-travel-geo-api", "kor-travel-concierge-api"),
            transaction=transaction,
            frozen_recovery=True,
        ),
        call(
            ("rustfs", "kor-travel-geo-api", "kor-travel-concierge-api"),
            transaction=transaction,
            frozen_recovery=True,
        ),
    ]
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
    databases_recreated = reset_intent.with_databases_recreated(
        pinvi_database_identity=_pinvi_database_identity()
    )
    assert (
        compose_service_module._pinned_runtime_reset_required(databases_recreated)
        is False
    )
    journal = _journal_at_runtime_phase("pinvi_api_ready")
    assert compose_service_module._pinned_runtime_reset_required(journal) is False


def test_dagster_live_identity_preserves_login_and_inherit_attestation() -> None:
    contract_identity, journal_identity = (
        compose_service_module._application_300_dagster_identities(
            _runtime_dagster_metadata_identity()
        )
    )

    assert contract_identity.login_role_attributes.can_login is True
    assert contract_identity.login_role_attributes.inherit is False
    assert journal_identity.login_role_attributes.can_login is True
    assert journal_identity.login_role_attributes.inherit is False


def test_dagster_storage_v3_receipt_is_exactly_bound_to_journal() -> None:
    journal = _journal_at_runtime_phase("map_dagster_storage_intent_durable")
    candidate = _map_application_300_candidate()
    receipt = _dagster_storage_receipt(journal, candidate)

    compose_service_module._validate_map_dagster_storage_receipt(
        receipt,
        journal=journal,
        candidate=candidate,
    )

    mutations = (
        ("schema", "kor-travel-map.dagster-storage-migration.v2"),
        ("permit_sha256", "0" * 64),
        ("candidate_sha256", "0" * 64),
        ("database_oid", 127002),
        ("catalog_sha256", "short"),
    )
    for field, value in mutations:
        changed = {**receipt, field: value}
        with pytest.raises(DeploymentContractError, match="receipt differs"):
            compose_service_module._validate_map_dagster_storage_receipt(
                changed,
                journal=journal,
                candidate=candidate,
            )
    missing = dict(receipt)
    missing.pop("catalog_sha256")
    with pytest.raises(DeploymentContractError, match="receipt differs"):
        compose_service_module._validate_map_dagster_storage_receipt(
            missing,
            journal=journal,
            candidate=candidate,
        )
    with pytest.raises(DeploymentContractError, match="receipt differs"):
        compose_service_module._validate_map_dagster_storage_receipt(
            {**receipt, "unexpected": True},
            journal=journal,
            candidate=candidate,
        )


@pytest.mark.parametrize(
    ("can_login", "inherit"),
    ((False, False), (True, True)),
)
def test_dagster_live_identity_rejects_login_attribute_drift(
    can_login: bool,
    inherit: bool,
) -> None:
    with pytest.raises(DeploymentContractError, match="identity is invalid"):
        compose_service_module._application_300_dagster_identities(
            _runtime_dagster_metadata_identity(
                can_login=can_login,
                inherit=inherit,
            )
        )


def test_createdb_response_loss_converges_from_exact_virgin_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    journal = _journal_at_application_300_phase(
        "application_create_intent_durable"
    )
    state_reader = Mock(
        side_effect=("virgin", "virgin", "virgin", "exact_complete")
    )
    identity_reader = Mock(
        side_effect=(
            _runtime_application_create_database_identity(),
            _runtime_application_create_database_identity(),
            _runtime_application_database_identity(),
        )
    )
    create = Mock()
    compose = Mock(return_value={"success": True})
    journal_writer = Mock()
    monkeypatch.setattr(
        compose_service_module,
        "inspect_application_300_bootstrap_state",
        state_reader,
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_application_300_database_identity",
        identity_reader,
    )
    monkeypatch.setattr(
        compose_service_module,
        "create_fresh_application_300_database",
        create,
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", compose)
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_runtime_rebuild_journal",
        journal_writer,
    )

    result, application_database = (
        service._converge_application_300_database_bootstrap(
            journal=journal,
            runtime=_runtime_application_database(),
            transaction=_opaque_transaction(),
            journal_path=Path("/state/journal.json"),
        )
    )

    assert result.phase == "application_roles_ready"
    assert application_database.oid == 127001
    create.assert_not_called()
    compose.assert_called_once_with(
        [
            "--profile",
            "bootstrap",
            "run",
            "--rm",
            "--no-deps",
            "--env",
            "KOR_TRAVEL_MAP_POSTGRES_PASSWORD",
            "kor-travel-map-db-role-bootstrap",
        ],
        transaction=ANY,
    )
    assert journal_writer.call_count == 3


def test_bootstrap_response_loss_uses_exact_result_without_reexecution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    journal = _journal_at_application_300_phase(
        "application_bootstrap_intent_durable"
    )
    compose = Mock()
    monkeypatch.setattr(
        compose_service_module,
        "inspect_application_300_bootstrap_state",
        Mock(return_value="exact_complete"),
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_application_300_database_identity",
        Mock(return_value=_runtime_application_database_identity()),
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", compose)
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_runtime_rebuild_journal",
        Mock(),
    )

    result, _ = service._converge_application_300_database_bootstrap(
        journal=journal,
        runtime=_runtime_application_database(),
        transaction=_opaque_transaction(),
        journal_path=Path("/state/journal.json"),
    )

    assert result.phase == "application_roles_ready"
    compose.assert_not_called()


@pytest.mark.parametrize("state", ("partial", "foreign", "absent"))
def test_bootstrap_intent_fails_closed_on_nonconvergent_state(
    state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    journal = _journal_at_application_300_phase(
        "application_bootstrap_intent_durable"
    )
    compose = Mock()
    monkeypatch.setattr(
        compose_service_module,
        "inspect_application_300_bootstrap_state",
        Mock(return_value=state),
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", compose)

    with pytest.raises(DeploymentContractError, match="bootstrap result is not exact"):
        service._converge_application_300_database_bootstrap(
            journal=journal,
            runtime=_runtime_application_database(),
            transaction=_opaque_transaction(),
            journal_path=Path("/state/journal.json"),
        )

    compose.assert_not_called()


def _application_paths(tmp_path: Path) -> Any:
    return compose_service_module._MapApplication300Paths(
        api_receipt=tmp_path / "api-candidate-build.json",
        paired_receipt=tmp_path / "paired-candidate-build.json",
        root_fence_directory=tmp_path / "fresh-root-fence",
        finalize_fence_directory=tmp_path / "fresh-finalize-fence",
        application_permit_directory=tmp_path / "application-final-permit",
        metadata_permit_directory=tmp_path / "dagster-storage-permit",
        result_directory=tmp_path / "results",
    )


def _fresh_root_result_for_finalize_renewal(
    *,
    journal: PinnedRuntimeRebuildJournal,
    map_candidate: MapApplication300Candidate,
    database: Any,
) -> FreshRootResult:
    root_plan = (
        journal.map_application_300_execution_evidence.fresh_root_operation_plan
    )
    if root_plan is None:
        raise AssertionError("root plan is missing")
    return FreshRootResult(
        payload_sha256=root_plan.result_sha256 or "4" * 64,
        operation_id=root_plan.operation_id,
        writer_fence_receipt_sha256=root_plan.fence_sha256,
        writer_fence_transaction_id=root_plan.transaction_id,
        journal_sha256=root_plan.basis_journal_sha256,
        journal_generation=root_plan.basis_journal_generation,
        map_candidate_commit=map_candidate.candidate_commit,
        map_candidate_image_id=map_candidate.api_image_id,
        postgres_image_id=map_candidate.postgres_image_id,
        reference_manifest_sha256=(
            map_candidate.application_contract.reference_manifest_sha256
        ),
        database_identity=database,
        post_source_catalog_sha256=(
            map_candidate.application_contract.source_catalog_sha256
        ),
        post_seed_sha256=map_candidate.application_contract.seed_sha256,
        expected_privileged_residue_sha256=(
            map_candidate.application_contract.privileged_residue_sha256
        ),
        expected_destination_alembic_version_sha256=(
            map_candidate.application_contract.destination_alembic_version_sha256
        ),
        post_destination_alembic_version_sha256=(
            map_candidate.application_contract.destination_alembic_version_sha256
        ),
    )


def test_expired_root_fence_renewal_preserves_operation_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    journal = _journal_at_application_300_phase("fresh_root_execution_intent")
    plan = (
        journal.map_application_300_execution_evidence.fresh_root_operation_plan
    )
    if plan is None:
        raise AssertionError("root plan is missing")
    map_candidate = _map_application_300_candidate()
    application_database, _ = compose_service_module._application_300_database_identities(
        _runtime_application_database_identity()
    )
    replace_artifact = Mock()
    write_journal = Mock()
    monkeypatch.setattr(
        compose_service_module,
        "replace_root_read_only_artifact",
        replace_artifact,
    )
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_runtime_rebuild_journal",
        write_journal,
    )

    updated, renewed_plan = service._renew_fresh_root_operation_plan(
        journal=journal,
        plan=plan,
        map_candidate=map_candidate,
        execution_candidate=compose_service_module._application_300_execution_candidate(
            map_candidate
        ),
        application_database=application_database,
        application_paths=_application_paths(tmp_path),
        journal_path=tmp_path / "journal.json",
    )

    raw = replace_artifact.call_args.kwargs["raw"]
    fence = json.loads(raw)
    assert renewed_plan.operation_id == plan.operation_id
    assert renewed_plan.transaction_id != plan.transaction_id
    assert renewed_plan.fence_sha256 != plan.fence_sha256
    assert fence["operation_id"] == plan.operation_id
    assert fence["transaction_id"] == renewed_plan.transaction_id
    assert fence["writer_fence_expires_at"] == renewed_plan.writer_fence_expires_at
    assert replace_artifact.call_args.kwargs["expected_old_sha256"] == plan.fence_sha256
    assert updated.phase == "fresh_root_execution_intent"
    assert updated.journal_generation == journal.journal_generation + 1
    assert (
        updated.map_application_300_execution_evidence.fresh_root_operation_plan
        == renewed_plan
    )
    write_journal.assert_called_once_with(tmp_path / "journal.json", updated)


def test_expired_root_fence_reconciliation_converges_file_first_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    journal = _journal_at_application_300_phase("fresh_root_execution_intent")
    plan = journal.map_application_300_execution_evidence.fresh_root_operation_plan
    if plan is None:
        raise AssertionError("root plan is missing")
    map_candidate = _map_application_300_candidate()
    application_database, _ = compose_service_module._application_300_database_identities(
        _runtime_application_database_identity()
    )
    expected_plan, renewed_raw = service._build_fresh_root_renewal(
        journal=journal,
        plan=plan,
        map_candidate=map_candidate,
        execution_candidate=compose_service_module._application_300_execution_candidate(
            map_candidate
        ),
        application_database=application_database,
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_root_read_only_artifact",
        Mock(return_value=renewed_raw),
    )
    write_journal = Mock()
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_runtime_rebuild_journal",
        write_journal,
    )

    updated, reconciled_plan = service._reconcile_expired_fresh_root_fence(
        journal=journal,
        plan=plan,
        map_candidate=map_candidate,
        execution_candidate=compose_service_module._application_300_execution_candidate(
            map_candidate
        ),
        application_database=application_database,
        application_paths=_application_paths(tmp_path),
        journal_path=tmp_path / "journal.json",
    )

    assert reconciled_plan == expected_plan
    assert reconciled_plan.operation_id == plan.operation_id
    assert reconciled_plan.transaction_id != plan.transaction_id
    assert updated.phase == "fresh_root_execution_intent"
    assert write_journal.call_args.args[0] == tmp_path / "journal.json"
    assert write_journal.call_args.args[1] == updated


def test_expired_finalize_fence_renewal_preserves_operation_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    journal = _journal_at_application_300_phase("fresh_finalize_execution_intent")
    plan = (
        journal.map_application_300_execution_evidence
        .fresh_finalize_operation_plan
    )
    if plan is None:
        raise AssertionError("finalize plan is missing")
    map_candidate = _map_application_300_candidate()
    application_database, _ = compose_service_module._application_300_database_identities(
        _runtime_application_database_identity()
    )
    replace_artifact = Mock()
    write_journal = Mock()
    monkeypatch.setattr(
        compose_service_module,
        "replace_root_read_only_artifact",
        replace_artifact,
    )
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_runtime_rebuild_journal",
        write_journal,
    )

    updated, renewed_plan = service._renew_fresh_finalize_operation_plan(
        journal=journal,
        plan=plan,
        map_candidate=map_candidate,
        execution_candidate=compose_service_module._application_300_execution_candidate(
            map_candidate
        ),
        application_database=application_database,
        application_paths=_application_paths(tmp_path),
        journal_path=tmp_path / "journal.json",
        root_result=_fresh_root_result_for_finalize_renewal(
            journal=journal,
            map_candidate=map_candidate,
            database=application_database,
        ),
    )

    raw = replace_artifact.call_args.kwargs["raw"]
    fence = json.loads(raw)
    assert renewed_plan.operation_id == plan.operation_id
    assert renewed_plan.transaction_id != plan.transaction_id
    assert renewed_plan.fence_sha256 != plan.fence_sha256
    assert fence["operation_id"] == plan.operation_id
    assert fence["transaction_id"] == renewed_plan.transaction_id
    assert fence["writer_fence_expires_at"] == renewed_plan.writer_fence_expires_at
    assert (
        fence["prior_fresh_migration_operation_id"]
        == journal.map_application_300_execution_evidence
        .fresh_root_operation_plan
        .operation_id
    )
    assert replace_artifact.call_args.kwargs["expected_old_sha256"] == plan.fence_sha256
    assert updated.phase == "fresh_finalize_execution_intent"
    assert updated.journal_generation == journal.journal_generation + 1
    assert (
        updated.map_application_300_execution_evidence
        .fresh_finalize_operation_plan
        == renewed_plan
    )
    write_journal.assert_called_once_with(tmp_path / "journal.json", updated)


def test_expired_finalize_fence_reconciliation_converges_file_first_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    journal = _journal_at_application_300_phase("fresh_finalize_execution_intent")
    plan = journal.map_application_300_execution_evidence.fresh_finalize_operation_plan
    if plan is None:
        raise AssertionError("finalize plan is missing")
    map_candidate = _map_application_300_candidate()
    application_database, _ = compose_service_module._application_300_database_identities(
        _runtime_application_database_identity()
    )
    root_result = _fresh_root_result_for_finalize_renewal(
        journal=journal,
        map_candidate=map_candidate,
        database=application_database,
    )
    expected_plan, renewed_raw = service._build_fresh_finalize_renewal(
        journal=journal,
        plan=plan,
        map_candidate=map_candidate,
        execution_candidate=compose_service_module._application_300_execution_candidate(
            map_candidate
        ),
        application_database=application_database,
        root_result=root_result,
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_root_read_only_artifact",
        Mock(return_value=renewed_raw),
    )
    write_journal = Mock()
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_runtime_rebuild_journal",
        write_journal,
    )

    updated, reconciled_plan = service._reconcile_expired_fresh_finalize_fence(
        journal=journal,
        plan=plan,
        map_candidate=map_candidate,
        execution_candidate=compose_service_module._application_300_execution_candidate(
            map_candidate
        ),
        application_database=application_database,
        application_paths=_application_paths(tmp_path),
        journal_path=tmp_path / "journal.json",
        root_result=root_result,
    )

    assert reconciled_plan == expected_plan
    assert reconciled_plan.operation_id == plan.operation_id
    assert reconciled_plan.transaction_id != plan.transaction_id
    assert updated.phase == "fresh_finalize_execution_intent"
    assert write_journal.call_args.args[0] == tmp_path / "journal.json"
    assert write_journal.call_args.args[1] == updated


@pytest.mark.parametrize(
    ("phase", "_one_shot_service", "expected_error"),
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
                "PinVi fresh role catalog reset outcome is ambiguous",
            ),
        (
            "pinvi_schema_ready",
            "pinvi-admin-bootstrap",
            "stop after PinVi API startup",
        ),
    ),
)
def test_application_300_one_shots_never_reexecute_after_durable_intent(
    phase: RebuildPhase,
    _one_shot_service: str,
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
            "pinvi_schema_ready",
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
    root_plan = (
        journal.map_application_300_execution_evidence.fresh_root_operation_plan
    )
    finalize_plan = (
        journal.map_application_300_execution_evidence.fresh_finalize_operation_plan
    )

    def run_compose(
        arguments: list[str],
        *,
        transaction: object,
    ) -> dict[str, object]:
        del transaction
        operation = tuple(arguments)
        operations.append(operation)
        if (
            phase == "fresh_root_execution_intent"
            and root_plan is not None
            and operation
            == (
                "--profile",
                "bootstrap",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "/usr/local/bin/python",
                "kor-travel-map-application-fresh-300",
                "-I",
                "/usr/local/bin/ktm-application-schema-fresh-300",
                "recover",
                "--operation-id",
                root_plan.operation_id,
            )
        ):
            raise DeploymentContractError("root receipt missing")
        if (
            phase == "fresh_root_execution_intent"
            and root_plan is not None
            and operation
            == (
                "--profile",
                "bootstrap",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "/usr/local/bin/python",
                "kor-travel-map-application-fresh-300",
                "-I",
                "/usr/local/bin/ktm-application-schema-fresh-300",
                "probe-missing",
                "--operation-id",
                root_plan.operation_id,
            )
        ):
            runtime_identity = _runtime_application_database_identity()
            contract = map_candidate.application_contract
            return {
                "success": True,
                "stdout": json.dumps(
                    {
                        "schema": (
                            "kor-travel-map.application-fresh-300-"
                            "root-missing-receipt.v1"
                        ),
                        "outcome": "receipt-missing-exact-prestate",
                        "operation_id": root_plan.operation_id,
                        "destination_head": "300",
                        "map_candidate_commit": map_candidate.candidate_commit,
                        "map_candidate_image_id": map_candidate.api_image_id,
                        "postgres_image_id": map_candidate.postgres_image_id,
                        "reference_manifest_sha256": contract.reference_manifest_sha256,
                        "writer_fence_receipt_sha256": root_plan.fence_sha256,
                        "writer_fence_transaction_id": root_plan.transaction_id,
                        "journal_sha256": root_plan.basis_journal_sha256,
                        "journal_generation": root_plan.basis_journal_generation,
                        "database_identity": {
                            "database_name": runtime_identity.database_name,
                            "database_oid": runtime_identity.database_oid,
                            "database_owner": runtime_identity.database_owner,
                            "postgres_system_identifier": (
                                runtime_identity.postgres_system_identifier
                            ),
                        },
                        "pre_root_state_schema": (
                            "kor-travel-map.application-fresh-300-pre-root.v1"
                        ),
                        "expected_post_source_catalog_sha256": (
                            contract.source_catalog_sha256
                        ),
                        "expected_post_seed_sha256": contract.seed_sha256,
                        "expected_post_destination_alembic_version_sha256": (
                            contract.destination_alembic_version_sha256
                        ),
                    },
                    sort_keys=True,
                ),
            }
        if (
            phase == "fresh_finalize_execution_intent"
            and finalize_plan is not None
            and operation
            == (
                "--profile",
                "bootstrap",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "/usr/local/bin/python",
                "kor-travel-map-application-fresh-finalize",
                "-I",
                "/usr/local/bin/ktm-application-schema-fresh-finalize",
                "recover",
                "--operation-id",
                finalize_plan.operation_id,
            )
        ):
            raise DeploymentContractError("finalize receipt missing")
        if (
            finalize_plan is not None
            and operation
            == (
                "--profile",
                "bootstrap",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "/usr/local/bin/python",
                "kor-travel-map-application-fresh-finalize",
                "-I",
                "/usr/local/bin/ktm-application-schema-fresh-finalize",
                "recover",
                "--operation-id",
                finalize_plan.operation_id,
            )
        ):
            return {"success": True, "stdout": "root-result\n"}
        if operation == (
            "run",
            "--rm",
            "--no-deps",
            "kor-travel-map-dagster-storage-migrate",
        ):
            return {
                "success": True,
                "stdout": json.dumps(
                    _dagster_storage_receipt(journal, map_candidate),
                    sort_keys=True,
                ),
            }
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
    monkeypatch.setattr(service, "_require_services_ready", Mock(return_value=[]))
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
        "read_pinned_database_identity",
        Mock(return_value=_runtime_pinvi_database_identity()),
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
    if phase == "fresh_root_execution_intent":
        monkeypatch.setattr(
            compose_service_module,
            "inspect_application_300_bootstrap_state",
            Mock(return_value="partial"),
        )
    if phase in {
        "fresh_finalize_execution_intent",
        "map_dagster_storage_intent_durable",
        "map_application_ready",
        "pinvi_schema_ready",
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
    if phase == "fresh_finalize_execution_intent":
        monkeypatch.setattr(
            compose_service_module,
            "read_database_schema_revision",
            Mock(return_value="unexpected-map-head"),
        )
    if phase in {
        "map_dagster_storage_intent_durable",
        "map_application_ready",
        "pinvi_schema_ready",
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
        revision_values = [
            "300",
            (
                "unexpected-dagster-head"
                if phase == "map_dagster_storage_intent_durable"
                else journal.candidate.map_dagster_head
            ),
        ]
        if phase == "pinvi_schema_ready":
            revision_values.append(journal.candidate.pinvi_head)
        revision_heads = iter(revision_values)
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
    elif phase == "pinvi_schema_ready":
        credential_file = Mock()
        monkeypatch.setattr(
            compose_service_module,
            "pinvi_bootstrap_credential_file",
            credential_file,
        )
        monkeypatch.setattr(
            compose_service_module,
            "load_c6c_deployment_config_from_environment",
            Mock(
                side_effect=DeploymentContractError(
                    "stop after PinVi API startup"
                )
            ),
        )

    with pytest.raises(DeploymentContractError, match=expected_error) as captured:
        service.rebuild_pinned_runtime()

    if phase == "map_application_ready":
        assert "stop after map runtime startup" not in str(captured.value)

    root_execution_command = (
        "--profile",
        "bootstrap",
        "run",
        "--rm",
        "--no-deps",
        "kor-travel-map-application-fresh-300",
    )
    finalize_execution_command = (
        "--profile",
        "bootstrap",
        "run",
        "--rm",
        "--no-deps",
        "kor-travel-map-application-fresh-finalize",
    )
    root_recovery_prefix = (
        "--profile",
        "bootstrap",
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "/usr/local/bin/python",
        "kor-travel-map-application-fresh-300",
        "-I",
        "/usr/local/bin/ktm-application-schema-fresh-300",
    )
    finalize_recovery_prefix = (
        "--profile",
        "bootstrap",
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "/usr/local/bin/python",
        "kor-travel-map-application-fresh-finalize",
        "-I",
        "/usr/local/bin/ktm-application-schema-fresh-finalize",
    )
    storage_command = (
        "run",
        "--rm",
        "--no-deps",
        "kor-travel-map-dagster-storage-migrate",
    )
    if phase == "fresh_root_execution_intent":
        assert root_plan is not None
        assert (
            *root_recovery_prefix,
            "recover",
            "--operation-id",
            root_plan.operation_id,
        ) in operations
        assert (
            *root_recovery_prefix,
            "probe-missing",
            "--operation-id",
            root_plan.operation_id,
        ) in operations
        assert root_execution_command not in operations
    elif phase == "fresh_finalize_execution_intent":
        assert finalize_plan is not None
        assert (
            *finalize_recovery_prefix,
            "recover",
            "--operation-id",
            finalize_plan.operation_id,
        ) in operations
        assert (
            *finalize_recovery_prefix,
            "probe-missing",
            "--operation-id",
            finalize_plan.operation_id,
        ) in operations
        assert finalize_execution_command not in operations
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
        assert operations.count(storage_command) == 1
    elif phase == "pinvi_schema_ready":
        assert operations.count(storage_command) == 0
        credential_file.assert_not_called()
        assert (
            "up",
            "-d",
            "--no-deps",
            "--wait",
            "--wait-timeout",
            "300",
            "pinvi-api",
        ) in operations
    assert all(
        "--no-deps" in operation
        for operation in operations
        if "up" in operation or "run" in operation
    )
    database_reset.assert_not_called()
    create_database.assert_not_called()


@pytest.mark.parametrize("failure_stage", ("none", "open", "admin", "seal"))
def test_pinvi_role_lifecycle_seals_the_migrator_after_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    service = ComposeService()
    transaction = cast(Any, SimpleNamespace())
    state_paths = cast(Any, SimpleNamespace())
    values = {
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "test-admin-password",
    }
    operations: list[tuple[str, ...]] = []
    credential_request = Mock()

    @contextmanager
    def credential_file(**kwargs: object):
        credential_request(**kwargs)
        yield SimpleNamespace(path=Path("/run/manager/bootstrap-admin.json"))

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        operation = tuple(args)
        operations.append(operation)
        if (
            operation[-1] == "pinvi-db-runtime-role"
            and operation[-2] == "PINVI_MIGRATOR_DISABLE_LOGIN=0"
            and failure_stage == "open"
        ):
            raise DeploymentContractError("role open failed")
        if operation[-1] == "pinvi-admin-bootstrap" and failure_stage == "admin":
            raise DeploymentContractError("admin bootstrap failed")
        if (
            operation[-1] == "pinvi-db-runtime-role"
            and operation[-2] == "PINVI_MIGRATOR_DISABLE_LOGIN=1"
            and failure_stage == "seal"
        ):
            raise DeploymentContractError("role seal failed")
        return {"success": True, "stdout": ""}

    monkeypatch.setattr(
        compose_service_module,
        "pinvi_bootstrap_credential_file",
        credential_file,
    )
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)

    if failure_stage in {"open", "admin"}:
        stage = "pinvi_role_open" if failure_stage == "open" else "pinvi_admin_bootstrap"
        with pytest.raises(
            DeploymentContractError,
            match=rf"PinVi bootstrap failed at {stage} \(unclassified\)",
        ) as captured:
            service._run_pinvi_schema_bootstrap_with_role_lifecycle(
                transaction=transaction,
                state_paths=state_paths,
                values=values,
                transaction_id="transaction-id",
            )
    elif failure_stage == "seal":
        with pytest.raises(
            DeploymentContractError,
            match=r"PinVi migrator seal failed at pinvi_role_seal \(unclassified\)",
        ) as captured:
            service._run_pinvi_schema_bootstrap_with_role_lifecycle(
                transaction=transaction,
                state_paths=state_paths,
                values=values,
                transaction_id="transaction-id",
            )
    else:
        service._run_pinvi_schema_bootstrap_with_role_lifecycle(
            transaction=transaction,
            state_paths=state_paths,
            values=values,
            transaction_id="transaction-id",
        )

    if failure_stage != "none":
        assert "test-admin-password" not in str(captured.value)

    role_prefix = (
        "--profile",
        "bootstrap",
        "run",
        "--rm",
        "--no-deps",
        "-e",
    )
    assert operations[0] == (
        *role_prefix,
        "PINVI_MIGRATOR_DISABLE_LOGIN=0",
        "pinvi-db-runtime-role",
    )
    assert operations[-1] == (
        *role_prefix,
        "PINVI_MIGRATOR_DISABLE_LOGIN=1",
        "pinvi-db-runtime-role",
    )
    assert (
        operations.count(
            (
                *role_prefix,
                "PINVI_MIGRATOR_DISABLE_LOGIN=1",
                "pinvi-db-runtime-role",
            )
        )
        == 1
    )
    if failure_stage == "open":
        credential_request.assert_not_called()
    else:
        credential_request.assert_called_once_with(
            state_paths=state_paths,
            values=values,
            transaction_id="transaction-id",
            email="admin@example.test",
            password="test-admin-password",
        )


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
        "pinvi-db-runtime-role",
        "kor-travel-map-dagster-db-init",
        "kor-travel-map-db-role-bootstrap",
        "kor-travel-map-application-fresh-300",
        "kor-travel-map-application-fresh-finalize",
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
    monkeypatch.setattr(service, "_require_services_ready", Mock(return_value=[]))
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
    previous_release = _release_with_pinvi_revision("d" * 40)
    next_release = _release_with_pinvi_revision("e" * 40)
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

    def static_command(
        _image: str,
        command: tuple[str, ...],
        *,
        label: str,
        entrypoint: str | None = None,
    ) -> str:
        del label
        return {
            "head": '{"head":"map-dagster-head","schema":"kor-travel-map.dagster-storage-head.v1"}\n',
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
    monkeypatch.setattr(service, "_require_services_ready", Mock(return_value=[]))
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
    assert read_rebuild_journal(next_paths.journal).candidate.pinvi_source_revision == "e" * 40
    assert compose_calls[0] == ("build", *COMPOSE_BUILT_RUNTIME_SERVICES)
    assert compose_calls[1][-2:] == ("pinvi-admin-bootstrap", "head")


def test_finalized_fixture_receipt_at_manifest_commit_never_allows_reset() -> None:
    journal = _journal_at_runtime_phase("manifest_committing")

    assert journal.phase == "manifest_committing"
    assert journal.cancel_probe.stage == "finalized"
    assert compose_service_module._pinned_runtime_reset_required(journal) is False


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
    holder = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(c6c_deployment.DeploymentContractError) as excinfo:
            with c6c_deployment.pinned_runtime_rebuild_lock():
                pass  # pragma: no cover - contended lock must not enter.
    finally:
        os.close(holder)

    assert str(excinfo.value) == "another C6c compatible-pair operation is already active"


def test_pinned_runtime_rebuild_lease_rejects_nonroot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(c6c_deployment.os, "geteuid", lambda: 1000)

    with pytest.raises(c6c_deployment.DeploymentContractError, match="requires root"):
        with c6c_deployment.pinned_runtime_rebuild_lock():
            pass  # pragma: no cover - root gate must reject before entering.
