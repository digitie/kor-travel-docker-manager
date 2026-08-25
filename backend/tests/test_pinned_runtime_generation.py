from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path

import pytest
from kor_travel_docker_manager.services import c6c_deployment
from kor_travel_docker_manager.services.c6c_deployment import (
    _PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
    DeploymentContractError,
    assert_compose_mutation_allowed,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    REBUILD_PHASES,
    MapApplication300ApplicationDatabaseIdentity,
    MapApplication300CandidateEvidence,
    MapApplication300DagsterMetadataDatabaseIdentity,
    MapApplication300DagsterMetadataRoleAttributes,
    MapApplication300ExecutionEvidence,
    MapApplication300OperationPlan,
    PinnedRuntimeCancelProbeOutcome,
    PinnedRuntimeCancelProbeReceipt,
    PinnedRuntimeDatabaseIdentity,
    PinnedRuntimeGeneration,
    PinnedRuntimeManifest,
    PinnedRuntimeRebuildJournal,
    RebuildPhase,
    ensure_pinned_runtime_state_directory,
    f1d_legacy_artifact_paths,
    generation_from_payload,
    generation_logical_sha256,
    journal_from_payload,
    legacy_tombstone_receipt_path,
    load_deployment_mode,
    pinned_runtime_state_paths,
    read_manifest,
    read_rebuild_journal,
    rebuild_journal_sha256,
    require_rebuildable_mode,
    retire_f1d_legacy_artifacts,
    write_manifest,
    write_rebuild_journal,
)

_PINSET_SHA256 = "a" * 64


def _digest(seed: str) -> str:
    return seed * 64


def _revision(seed: str) -> str:
    return seed * 40


def _image_id(seed: str) -> str:
    return f"sha256:{_digest(seed)}"


def _candidate_evidence(seed: str = "a") -> MapApplication300CandidateEvidence:
    return MapApplication300CandidateEvidence(
        paired_receipt_sha256=_digest(seed),
        api_receipt_sha256=_digest(seed),
        candidate_git_tree=_revision(seed),
        postgres_image_id=_image_id(seed),
        dagster_config_sha256=_digest(seed),
        dagster_yaml_sha256=_digest(seed),
        application_contract_sha256=_digest(seed),
        launch_contract_sha256=_digest(seed),
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


def _pinvi_database_identity() -> PinnedRuntimeDatabaseIdentity:
    return PinnedRuntimeDatabaseIdentity(
        system_identifier="8585858585858585858",
        name="pinvi",
        oid=127003,
        owner="pinvi",
        login_role="pinvi",
    )


def _dagster_metadata_database_identity() -> MapApplication300DagsterMetadataDatabaseIdentity:
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


def _generation(seed: str = "a") -> PinnedRuntimeGeneration:
    return PinnedRuntimeGeneration(
        map_api_image_id=_image_id(seed),
        map_ui_image_id=_image_id(seed),
        map_dagster_image_id=_image_id(seed),
        map_dagster_daemon_image_id=_image_id(seed),
        pinvi_api_image_id=_image_id(seed),
        pinvi_web_image_id=_image_id(seed),
        pinvi_dagster_image_id=_image_id(seed),
        map_source_revision=_revision(seed),
        pinvi_source_revision=_revision(seed),
        map_application_head="0084_c6c_cancel_probe_fixtures",
        map_dagster_head="dagster-1",
        pinvi_head="20260801_0050",
        pinset_sha256=_digest(seed),
        map_application_300_candidate_evidence=_candidate_evidence(seed),
        recorded_at="2026-08-06T00:00:00+00:00",
    )


def _journal(seed: str = "a") -> PinnedRuntimeRebuildJournal:
    generation = _generation(seed)
    return PinnedRuntimeRebuildJournal(
        version=8,
        transaction_id=str(uuid.uuid4()),
        phase="candidate_attested",
        candidate=generation,
        map_application_300_candidate_evidence=(
            generation.map_application_300_candidate_evidence
        ),
        environment_sha256=_digest("b"),
        compose_sha256=_digest("c"),
        resolved_compose_sha256=_digest("d"),
        created_at="2026-08-06T00:00:00+00:00",
    )


def _operation_plan(
    journal: PinnedRuntimeRebuildJournal,
    *,
    seed: str,
    result_sha256: str | None = None,
) -> MapApplication300OperationPlan:
    return MapApplication300OperationPlan(
        transaction_id=journal.transaction_id,
        operation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{journal.transaction_id}:{seed}")),
        basis_journal_sha256=rebuild_journal_sha256(journal),
        basis_journal_generation=journal.journal_generation,
        writer_fence_expires_at="2026-08-06T00:05:00+00:00",
        fence_sha256=_digest(seed),
        result_sha256=result_sha256,
    )


def _copy_journal(
    journal: PinnedRuntimeRebuildJournal,
    *,
    phase: RebuildPhase | None = None,
    journal_generation: int | None = None,
    map_application_300_candidate_evidence: (
        MapApplication300CandidateEvidence | None
    ) = None,
    map_application_300_execution_evidence: (
        MapApplication300ExecutionEvidence | None
    ) = None,
) -> PinnedRuntimeRebuildJournal:
    return PinnedRuntimeRebuildJournal(
        version=journal.version,
        transaction_id=journal.transaction_id,
        phase=journal.phase if phase is None else phase,
        candidate=journal.candidate,
        map_application_300_candidate_evidence=(
            journal.map_application_300_candidate_evidence
            if map_application_300_candidate_evidence is None
            else map_application_300_candidate_evidence
        ),
        environment_sha256=journal.environment_sha256,
        compose_sha256=journal.compose_sha256,
        resolved_compose_sha256=journal.resolved_compose_sha256,
        created_at=journal.created_at,
        pinvi_database_identity=journal.pinvi_database_identity,
        journal_generation=(
            journal.journal_generation
            if journal_generation is None
            else journal_generation
        ),
        map_application_300_execution_evidence=(
            journal.map_application_300_execution_evidence
            if map_application_300_execution_evidence is None
            else map_application_300_execution_evidence
        ),
        cancel_probe=journal.cancel_probe,
    )


def _journal_with_application_roles_ready() -> PinnedRuntimeRebuildJournal:
    return (
        _journal()
        .transition("reset_intent_durable")
        .with_databases_recreated(
            pinvi_database_identity=_pinvi_database_identity()
        )
        .with_application_create_intent()
        .with_application_created(
            application_create_database_identity=(
                _application_create_database_identity()
            )
        )
        .with_application_bootstrap_intent()
        .with_application_roles_ready(
            application_database_identity=_application_database_identity()
        )
    )


def _journal_with_map_application_ready() -> PinnedRuntimeRebuildJournal:
    journal = _journal_with_application_roles_ready()
    root_plan = _operation_plan(journal, seed="2")
    journal = journal.with_fresh_root_plan_ready(fresh_root_operation_plan=root_plan)
    journal = journal.with_fresh_root_fence_ready(fresh_root_operation_plan=root_plan)
    journal = journal.with_fresh_root_execution_intent(fresh_root_operation_plan=root_plan)
    root_result_plan = root_plan.with_result(_digest("4"))
    journal = journal.with_fresh_root_ready(fresh_root_operation_plan=root_result_plan)

    finalize_plan = _operation_plan(journal, seed="5")
    journal = journal.with_fresh_finalize_plan_ready(
        fresh_finalize_operation_plan=finalize_plan
    )
    journal = journal.with_fresh_finalize_fence_ready(
        fresh_finalize_operation_plan=finalize_plan
    )
    journal = journal.with_fresh_finalize_execution_intent(
        fresh_finalize_operation_plan=finalize_plan
    )
    finalize_result_plan = finalize_plan.with_result(_digest("7"))
    journal = journal.with_fresh_finalize_ready(
        fresh_finalize_operation_plan=finalize_result_plan
    )
    journal = journal.with_application_permit_ready(app_final_permit_sha256=_digest("8"))
    journal = journal.with_metadata_permit_ready(
        dagster_metadata_database_identity=_dagster_metadata_database_identity(),
        metadata_permit_sha256=_digest("9"),
    )
    return journal.with_map_application_ready()


@pytest.mark.parametrize(
    ("environment", "lifecycle", "pinvi_environment", "required"),
    [
        ("local", "development", "development", "false"),
        ("rehearsal", "rebuildable", "production", "true"),
        ("production", "operational", "production", "true"),
    ],
)
def test_load_deployment_mode_accepts_only_typed_pairs(
    environment: str,
    lifecycle: str,
    pinvi_environment: str,
    required: str,
) -> None:
    mode = load_deployment_mode(
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": environment,
            "KTDM_DEPLOYMENT_LIFECYCLE": lifecycle,
            "PINVI_ENVIRONMENT": pinvi_environment,
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": required,
        }
    )

    assert mode.environment == environment
    assert mode.lifecycle == lifecycle
    assert mode.rebuildable is (lifecycle == "rebuildable")


def test_rebuildable_rejects_production_environment_even_with_lifecycle_flag() -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
    }

    with pytest.raises(DeploymentContractError, match="environment/lifecycle"):
        require_rebuildable_mode(values)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS", '[{"id":"configured"}]'),
        ("PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED", "true"),
        ("PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_COMMAND_TOKEN", "configured"),
        ("PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_ID", "other-consumer"),
    ],
)
def test_rebuildable_rejects_configured_cache_target_runtime(
    name: str, value: str
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        name: value,
    }

    with pytest.raises(DeploymentContractError, match="inert cache-target"):
        require_rebuildable_mode(values)


def test_rebuild_capability_allows_compose_mutation_only_in_rebuildable_mode() -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
    }

    assert_compose_mutation_allowed(
        ("kor-travel-map-api", "pinvi-api"),
        environment=values,
        capability=_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
    )

    with pytest.raises(DeploymentContractError, match="rehearsal/rebuildable"):
        assert_compose_mutation_allowed(
            ("kor-travel-map-api",),
            environment={**values, "KTDM_DEPLOYMENT_LIFECYCLE": "operational"},
            capability=_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
        )


def test_pinned_runtime_state_paths_are_rebuildable_project_scoped(
    tmp_path: Path,
) -> None:
    paths = pinned_runtime_state_paths(
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
            "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
            "PINVI_ENVIRONMENT": "production",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
            "COMPOSE_PROJECT_NAME": "f1d-isolated",
            "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path),
        },
        pinset_sha256=_PINSET_SHA256,
    )

    ensure_pinned_runtime_state_directory(paths.state_root)

    assert paths.state_root == tmp_path / "f1d-isolated"
    assert paths.manifest == paths.state_root / "pinned-runtime-generation-v6.json"
    assert paths.journal == (
        paths.state_root / f"pinned-runtime-rebuild-v8-{_PINSET_SHA256}.json"
    )
    assert paths.tombstone_receipt == legacy_tombstone_receipt_path(
        paths.state_root,
        pinset_sha256=_PINSET_SHA256,
    )
    assert paths.tombstone_receipt == (
        paths.state_root / f"legacy-tombstone-v8-{_PINSET_SHA256}.json"
    )
    assert stat.S_IMODE(paths.state_root.stat().st_mode) == 0o700


def test_pinned_runtime_state_paths_reject_nonrebuildable_or_invalid_project(
    tmp_path: Path,
) -> None:
    common = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path),
    }

    with pytest.raises(DeploymentContractError, match="COMPOSE_PROJECT_NAME"):
        pinned_runtime_state_paths(common, pinset_sha256=_PINSET_SHA256)
    with pytest.raises(DeploymentContractError, match="environment/lifecycle"):
        pinned_runtime_state_paths(
            {
                **common,
                "COMPOSE_PROJECT_NAME": "f1d-isolated",
                "KTDM_DEPLOYMENT_LIFECYCLE": "operational",
            },
            pinset_sha256=_PINSET_SHA256,
        )


def test_pinned_runtime_journal_and_tombstone_paths_are_pinset_scoped(
    tmp_path: Path,
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "COMPOSE_PROJECT_NAME": "f1d-isolated",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path),
    }
    previous = pinned_runtime_state_paths(values, pinset_sha256="a" * 64)
    next_release = pinned_runtime_state_paths(values, pinset_sha256="b" * 64)

    assert previous.state_root == next_release.state_root
    assert previous.manifest == next_release.manifest
    assert previous.journal != next_release.journal
    assert previous.tombstone_receipt != next_release.tombstone_receipt


def test_manifest_is_single_active_generation_without_rollback(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    path = state / "pinned-runtime-generation-v6.json"
    manifest = PinnedRuntimeManifest(version=6, active_generation=_generation())

    write_manifest(path, manifest)

    assert read_manifest(path) == manifest
    assert '"rollback"' not in path.read_text(encoding="utf-8")


def test_manifest_rejects_unsafe_file_mode(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    path = state / "pinned-runtime-generation-v6.json"
    write_manifest(path, PinnedRuntimeManifest(version=6, active_generation=_generation()))
    os.chmod(path, 0o644)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_manifest(path)


def test_rebuild_journal_requires_candidate_first_and_exact_phase_order(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    journal = _journal()
    path = state / "pinned-runtime-rebuild-v8.json"

    write_rebuild_journal(path, journal)
    restored = read_rebuild_journal(path)

    assert restored == journal
    assert restored.transition("reset_intent_durable").phase == "reset_intent_durable"
    with pytest.raises(DeploymentContractError, match="phase transition"):
        restored.transition("databases_recreated")


def test_rebuild_journal_application_300_phases_require_evidence_methods() -> None:
    reset_intent = _journal().transition("reset_intent_durable")
    with pytest.raises(DeploymentContractError, match="evidence-specific"):
        reset_intent.transition("databases_recreated")
    journal = reset_intent.with_databases_recreated(
        pinvi_database_identity=_pinvi_database_identity()
    )

    assert journal.journal_generation == REBUILD_PHASES.index("databases_recreated")
    with pytest.raises(DeploymentContractError, match="evidence-specific"):
        journal.transition("application_create_intent_durable")

    journal = journal.with_application_create_intent()
    create_identity = _application_create_database_identity()
    journal = journal.with_application_created(
        application_create_database_identity=create_identity
    )
    journal = journal.with_application_bootstrap_intent()
    application_identity = _application_database_identity()
    journal = journal.with_application_roles_ready(
        application_database_identity=application_identity
    )
    assert journal.phase == "application_roles_ready"
    assert journal.journal_generation == REBUILD_PHASES.index("application_roles_ready")
    assert (
        journal.map_application_300_execution_evidence.application_database_identity
        == application_identity
    )
    assert (
        journal.map_application_300_execution_evidence.application_database_identity_sha256
        == application_identity.sha256()
    )

    with pytest.raises(DeploymentContractError, match="phase transition"):
        journal.transition("metadata_permit_ready")

    with pytest.raises(DeploymentContractError, match="metadata permit is out of order"):
        journal.with_metadata_permit_ready(
            dagster_metadata_database_identity=_dagster_metadata_database_identity(),
            metadata_permit_sha256=_digest("9"),
        )
    with pytest.raises(DeploymentContractError, match="evidence-specific"):
        journal.transition("fresh_root_plan_ready")

    root_plan = _operation_plan(journal, seed="2")
    journal = journal.with_fresh_root_plan_ready(fresh_root_operation_plan=root_plan)
    assert journal.phase == "fresh_root_plan_ready"
    assert journal.map_application_300_execution_evidence.fresh_root_operation_plan == (
        root_plan
    )
    journal = journal.with_fresh_root_fence_ready(fresh_root_operation_plan=root_plan)
    root_intent = journal.with_fresh_root_execution_intent(
        fresh_root_operation_plan=root_plan
    )

    assert root_intent.phase == "fresh_root_execution_intent"
    root_intent_plan = (
        root_intent.map_application_300_execution_evidence.fresh_root_operation_plan
    )
    assert root_intent_plan is not None
    assert root_intent_plan.result_sha256 is None
    with pytest.raises(DeploymentContractError, match="evidence-specific"):
        root_intent.transition("fresh_root_ready")

    root_result_plan = root_plan.with_result(_digest("4"))
    journal = root_intent.with_fresh_root_ready(
        fresh_root_operation_plan=root_result_plan
    )
    finalize_plan = _operation_plan(journal, seed="5")
    journal = journal.with_fresh_finalize_plan_ready(
        fresh_finalize_operation_plan=finalize_plan
    )
    journal = journal.with_fresh_finalize_fence_ready(
        fresh_finalize_operation_plan=finalize_plan
    )
    finalize_intent = journal.with_fresh_finalize_execution_intent(
        fresh_finalize_operation_plan=finalize_plan
    )

    assert finalize_intent.phase == "fresh_finalize_execution_intent"
    finalize_intent_plan = (
        finalize_intent
        .map_application_300_execution_evidence
        .fresh_finalize_operation_plan
    )
    assert finalize_intent_plan is not None
    assert finalize_intent_plan.result_sha256 is None
    with pytest.raises(DeploymentContractError, match="evidence-specific"):
        finalize_intent.transition("fresh_finalize_ready")

    finalize_result_plan = finalize_plan.with_result(_digest("7"))
    journal = finalize_intent.with_fresh_finalize_ready(
        fresh_finalize_operation_plan=finalize_result_plan
    )
    journal = journal.with_application_permit_ready(app_final_permit_sha256=_digest("8"))
    metadata_identity = _dagster_metadata_database_identity()
    journal = journal.with_metadata_permit_ready(
        dagster_metadata_database_identity=metadata_identity,
        metadata_permit_sha256=_digest("9"),
    )
    assert (
        journal.map_application_300_execution_evidence.dagster_metadata_database_identity
        == metadata_identity
    )
    journal = journal.with_map_application_ready()

    assert journal.phase == "map_application_ready"
    assert journal.journal_generation == REBUILD_PHASES.index("map_application_ready")
    journal = journal.transition("map_dagster_storage_intent_durable")
    assert journal.phase == "map_dagster_storage_intent_durable"
    assert journal.transition("map_dagster_ready").phase == "map_dagster_ready"


def test_application_create_and_bootstrap_receipts_bind_one_database_identity() -> None:
    journal = (
        _journal()
        .transition("reset_intent_durable")
        .with_databases_recreated(
            pinvi_database_identity=_pinvi_database_identity()
        )
        .with_application_create_intent()
    )
    created = journal.with_application_created(
        application_create_database_identity=_application_create_database_identity()
    )

    restored = journal_from_payload(created.to_payload())
    assert restored == created
    assert restored.phase == "application_created"
    assert (
        restored.map_application_300_execution_evidence
        .application_create_database_identity_sha256
        == _application_create_database_identity().sha256()
    )

    bootstrap_intent = restored.with_application_bootstrap_intent()
    changed_identity = MapApplication300ApplicationDatabaseIdentity(
        database_name="kor_travel_map",
        database_oid=127099,
        database_owner="ktm_feature_schema_owner",
        postgres_system_identifier="7474747474747474747",
    )
    with pytest.raises(DeploymentContractError, match="changed during role bootstrap"):
        bootstrap_intent.with_application_roles_ready(
            application_database_identity=changed_identity
        )


def test_rebuild_journal_rejects_skipped_phase_and_operation_plan_basis_drift() -> None:
    journal = _journal_with_application_roles_ready()
    root_plan = _operation_plan(journal, seed="2")

    with pytest.raises(DeploymentContractError, match="root fence is out of order"):
        journal.with_fresh_root_fence_ready(fresh_root_operation_plan=root_plan)

    wrong_generation_plan = MapApplication300OperationPlan(
        transaction_id=root_plan.transaction_id,
        operation_id=root_plan.operation_id,
        basis_journal_sha256=root_plan.basis_journal_sha256,
        basis_journal_generation=root_plan.basis_journal_generation + 1,
        writer_fence_expires_at=root_plan.writer_fence_expires_at,
        fence_sha256=root_plan.fence_sha256,
    )
    with pytest.raises(DeploymentContractError, match="basis generation differs"):
        journal.with_fresh_root_plan_ready(
            fresh_root_operation_plan=wrong_generation_plan
        )

    wrong_sha_plan = MapApplication300OperationPlan(
        transaction_id=root_plan.transaction_id,
        operation_id=root_plan.operation_id,
        basis_journal_sha256=_digest("e"),
        basis_journal_generation=root_plan.basis_journal_generation,
        writer_fence_expires_at=root_plan.writer_fence_expires_at,
        fence_sha256=root_plan.fence_sha256,
    )
    with pytest.raises(DeploymentContractError, match="basis journal differs"):
        journal.with_fresh_root_plan_ready(fresh_root_operation_plan=wrong_sha_plan)

    root_plan_ready = journal.with_fresh_root_plan_ready(
        fresh_root_operation_plan=root_plan
    )
    changed_plan = _operation_plan(journal, seed="3")
    with pytest.raises(DeploymentContractError, match="root operation plan changed"):
        root_plan_ready.with_fresh_root_fence_ready(
            fresh_root_operation_plan=changed_plan
        )
    with pytest.raises(DeploymentContractError, match="finalize plan is out of order"):
        root_plan_ready.with_fresh_finalize_plan_ready(
            fresh_finalize_operation_plan=_operation_plan(root_plan_ready, seed="5")
        )


def test_rebuild_journal_application_300_journal_generation_is_monotonic() -> None:
    journal = _journal()
    observed: list[int] = [journal.journal_generation]

    journal = journal.transition("reset_intent_durable")
    observed.append(journal.journal_generation)
    journal = journal.with_databases_recreated(
        pinvi_database_identity=_pinvi_database_identity()
    )
    observed.append(journal.journal_generation)
    journal = journal.with_application_create_intent()
    observed.append(journal.journal_generation)
    journal = journal.with_application_created(
        application_create_database_identity=_application_create_database_identity()
    )
    observed.append(journal.journal_generation)
    journal = journal.with_application_bootstrap_intent()
    observed.append(journal.journal_generation)
    journal = journal.with_application_roles_ready(
        application_database_identity=_application_database_identity()
    )
    observed.append(journal.journal_generation)
    root_plan = _operation_plan(journal, seed="2")
    journal = journal.with_fresh_root_plan_ready(fresh_root_operation_plan=root_plan)
    observed.append(journal.journal_generation)
    journal = journal.with_fresh_root_fence_ready(fresh_root_operation_plan=root_plan)
    observed.append(journal.journal_generation)
    journal = journal.with_fresh_root_execution_intent(
        fresh_root_operation_plan=root_plan
    )
    observed.append(journal.journal_generation)
    journal = journal.with_fresh_root_ready(
        fresh_root_operation_plan=root_plan.with_result(_digest("4"))
    )
    observed.append(journal.journal_generation)
    finalize_plan = _operation_plan(journal, seed="5")
    journal = journal.with_fresh_finalize_plan_ready(
        fresh_finalize_operation_plan=finalize_plan
    )
    observed.append(journal.journal_generation)
    journal = journal.with_fresh_finalize_fence_ready(
        fresh_finalize_operation_plan=finalize_plan
    )
    observed.append(journal.journal_generation)
    journal = journal.with_fresh_finalize_execution_intent(
        fresh_finalize_operation_plan=finalize_plan
    )
    observed.append(journal.journal_generation)
    journal = journal.with_fresh_finalize_ready(
        fresh_finalize_operation_plan=finalize_plan.with_result(_digest("7"))
    )
    observed.append(journal.journal_generation)
    journal = journal.with_application_permit_ready(app_final_permit_sha256=_digest("8"))
    observed.append(journal.journal_generation)
    journal = journal.with_metadata_permit_ready(
        dagster_metadata_database_identity=_dagster_metadata_database_identity(),
        metadata_permit_sha256=_digest("9"),
    )
    observed.append(journal.journal_generation)
    journal = journal.with_map_application_ready()
    observed.append(journal.journal_generation)

    assert observed == list(range(REBUILD_PHASES.index("map_application_ready") + 1))


def test_rebuild_journal_application_300_rejects_missing_or_future_evidence() -> None:
    base = _journal().transition("reset_intent_durable").with_databases_recreated(
        pinvi_database_identity=_pinvi_database_identity()
    )

    with pytest.raises(DeploymentContractError, match="lacks required evidence"):
        _copy_journal(
            base,
            phase="application_roles_ready",
            journal_generation=REBUILD_PHASES.index("application_roles_ready"),
            map_application_300_execution_evidence=MapApplication300ExecutionEvidence(),
        )

    root_intent = _journal_with_application_roles_ready()
    root_plan = _operation_plan(root_intent, seed="2")
    root_intent = root_intent.with_fresh_root_plan_ready(
        fresh_root_operation_plan=root_plan
    )
    root_intent = root_intent.with_fresh_root_fence_ready(
        fresh_root_operation_plan=root_plan
    )
    root_intent = root_intent.with_fresh_root_execution_intent(
        fresh_root_operation_plan=root_plan
    )
    with pytest.raises(DeploymentContractError, match="future evidence"):
        _copy_journal(
            root_intent,
            map_application_300_execution_evidence=(
                root_intent.map_application_300_execution_evidence.with_fresh_root_result(
                    root_plan.with_result(_digest("4"))
                )
            ),
        )

    with pytest.raises(DeploymentContractError, match="result is missing"):
        _copy_journal(
            root_intent,
            phase="fresh_root_ready",
            journal_generation=REBUILD_PHASES.index("fresh_root_ready"),
        )


def test_map_application_300_identity_sha_and_role_privileges_fail_closed() -> None:
    application_identity = _application_database_identity()
    with pytest.raises(DeploymentContractError, match="identity SHA differs"):
        MapApplication300ExecutionEvidence(
            application_database_identity=application_identity,
            application_database_identity_sha256=_digest("e"),
        )

    with pytest.raises(DeploymentContractError, match="role is privileged"):
        MapApplication300DagsterMetadataRoleAttributes(
            superuser=True,
            create_database=False,
            create_role=False,
            replication=False,
            bypass_rls=False,
            granted_role_count=0,
            member_role_count=0,
        )


def test_rebuild_journal_binds_candidate_evidence_and_strict_payload() -> None:
    journal = _journal_with_map_application_ready()
    payload = journal.to_payload()

    assert journal_from_payload(payload) == journal
    assert journal.candidate.map_application_300_candidate_evidence.to_payload() == (
        payload["map_application_300_candidate_evidence"]
    )
    execution_payload = payload["map_application_300_execution_evidence"]
    assert isinstance(execution_payload, dict)
    root_plan_payload = execution_payload["fresh_root_operation_plan"]
    assert isinstance(root_plan_payload, dict)
    assert root_plan_payload["basis_journal_generation"] == REBUILD_PHASES.index(
        "application_roles_ready"
    )
    assert root_plan_payload["result_sha256"] == _digest("4")

    with pytest.raises(DeploymentContractError, match="candidate evidence differs"):
        _copy_journal(
            journal,
            map_application_300_candidate_evidence=_candidate_evidence("b"),
        )

    with pytest.raises(DeploymentContractError, match="payload is invalid"):
        journal_from_payload({**payload, "extra": "nope"})
    nested_extra = json.loads(json.dumps(payload))
    nested_extra["map_application_300_execution_evidence"]["fresh_root_operation_plan"][
        "extra"
    ] = "nope"
    with pytest.raises(DeploymentContractError, match="operation plan payload"):
        journal_from_payload(nested_extra)


def test_rebuild_journal_sha256_is_canonical_and_evidence_sensitive() -> None:
    journal = _journal_with_map_application_ready()
    expected = hashlib.sha256(
        (
            json.dumps(
                journal.to_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()

    assert rebuild_journal_sha256(journal) == expected
    assert rebuild_journal_sha256(journal) == rebuild_journal_sha256(
        journal_from_payload(journal.to_payload())
    )

    root_plan = journal.map_application_300_execution_evidence.fresh_root_operation_plan
    assert root_plan is not None
    with pytest.raises(DeploymentContractError, match="result cannot be rebound"):
        root_plan.with_result(_digest("0"))


def test_rebuild_journal_requires_durable_cancel_post_and_finalize_receipts() -> None:
    receipt = PinnedRuntimeCancelProbeReceipt()
    armed = receipt.transition(
        "armed",
        job_id=str(uuid.uuid4()),
        fixture_created_at="2026-08-06T00:00:00+00:00",
    )
    attempted = armed.transition("cancel_post_attempted")
    consumed = attempted.transition(
        "consumed",
        cancellation_id=str(uuid.uuid4()),
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

    assert finalized.stage == "finalized"
    with pytest.raises(DeploymentContractError, match="transition"):
        armed.transition("consumed")


@pytest.mark.parametrize(
    ("consumed_at", "finalized_at"),
    [
        ("2026-08-06T00:00:00+00:00", "2026-08-06T00:02:00+00:00"),
        ("2026-08-06T00:02:00+00:00", "2026-08-06T00:01:00+00:00"),
    ],
)
def test_pinned_runtime_receipt_rejects_reversed_fixture_timestamps(
    consumed_at: str,
    finalized_at: str,
) -> None:
    with pytest.raises(DeploymentContractError, match="timestamp order"):
        PinnedRuntimeCancelProbeReceipt(
            stage="finalized",
            job_id=str(uuid.uuid4()),
            cancellation_id=str(uuid.uuid4()),
            outcome=PinnedRuntimeCancelProbeOutcome(
                name="pinvi_cancel_error",
                status=409,
                code="PIPELINE_CANCELLATION_UNSAFE",
            ),
            fixture_created_at="2026-08-06T00:01:00+00:00",
            fixture_consumed_at=consumed_at,
            fixture_finalized_at=finalized_at,
        )


def test_rebuild_journal_rejects_fixture_timestamp_drift() -> None:
    journal = _journal_with_map_application_ready()
    journal = journal.transition("map_dagster_storage_intent_durable")
    journal = journal.transition("map_dagster_ready")
    journal = journal.transition("map_runtime_ready")
    journal = journal.transition("pinvi_schema_ready")
    journal = journal.transition("pinvi_api_ready")
    armed = PinnedRuntimeCancelProbeReceipt().transition(
        "armed",
        job_id=str(uuid.uuid4()),
        fixture_created_at="2026-08-06T00:00:00+00:00",
    )
    journal = journal.with_cancel_probe(armed)

    with pytest.raises(DeploymentContractError, match="identity drifted"):
        journal.with_cancel_probe(
            PinnedRuntimeCancelProbeReceipt(
                stage="cancel_post_attempted",
                job_id=armed.job_id,
                fixture_created_at="2026-08-06T00:00:01+00:00",
            )
        )


def test_generation_logical_sha256_excludes_recording_timestamp() -> None:
    initial = _generation()
    later = generation_from_payload(
        {**initial.to_payload(), "recorded_at": "2026-08-06T01:00:00+00:00"}
    )

    assert generation_logical_sha256(initial) == generation_logical_sha256(later)


def test_legacy_tombstone_receipt_is_fsynced_before_allowlisted_unlink(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o700)
    legacy = state_root / "compatible-pair-v4.json"
    legacy.write_text('{"version":4}\n', encoding="utf-8")
    os.chmod(legacy, 0o600)
    transaction_id = str(uuid.uuid4())

    receipt = retire_f1d_legacy_artifacts(
        state_root=state_root,
        transaction_id=transaction_id,
        candidate=_generation(),
        recorded_at="2026-08-06T00:00:00+00:00",
    )

    assert receipt.transaction_id == transaction_id
    assert receipt.requested_paths == f1d_legacy_artifact_paths(
        pinset_sha256=_generation().pinset_sha256
    )
    assert receipt.version == 8
    assert tuple(entry.relative_path for entry in receipt.retired) == (
        "compatible-pair-v4.json",
    )
    assert not legacy.exists()
    stored = legacy_tombstone_receipt_path(
        state_root,
        pinset_sha256=_generation().pinset_sha256,
    )
    assert stored.exists()
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert retire_f1d_legacy_artifacts(
        state_root=state_root,
        transaction_id=transaction_id,
        candidate=_generation(),
        recorded_at="2026-08-06T00:00:00+00:00",
    ) == receipt


def test_v4_manifest_api_is_absent_and_only_tombstoned() -> None:
    for name in (
        "CompatibleImagePair",
        "CompatiblePairManifest",
        "parse_pair_manifest",
        "initial_pair_manifest",
        "write_pair_manifest",
        "restore_pair_manifest_snapshot",
    ):
        assert not hasattr(c6c_deployment, name)


def test_legacy_tombstone_rejects_artifact_that_appears_after_receipt(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o700)
    transaction_id = str(uuid.uuid4())
    retire_f1d_legacy_artifacts(
        state_root=state_root,
        transaction_id=transaction_id,
        candidate=_generation(),
        recorded_at="2026-08-06T00:00:00+00:00",
    )
    foreign = state_root / "cache-target-window-v1.json"
    foreign.write_text("{}\n", encoding="utf-8")
    os.chmod(foreign, 0o600)

    with pytest.raises(DeploymentContractError, match="conflicts"):
        retire_f1d_legacy_artifacts(
            state_root=state_root,
            transaction_id=transaction_id,
            candidate=_generation(),
            recorded_at="2026-08-06T00:00:00+00:00",
        )


def test_v8_tombstone_retires_v5_journal_without_reusing_v5_receipt(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o700)
    old_journal = state_root / "pinned-runtime-rebuild-v5.json"
    old_journal.write_text('{"version":5}\n', encoding="utf-8")
    os.chmod(old_journal, 0o600)
    old_receipt_directory = state_root / "pinned-runtime-v5"
    old_receipt_directory.mkdir(mode=0o700)
    os.chmod(old_receipt_directory, 0o700)
    (old_receipt_directory / "legacy-tombstone-v5.json").write_text(
        '{"historical":true}\n',
        encoding="utf-8",
    )
    os.chmod(old_receipt_directory / "legacy-tombstone-v5.json", 0o600)

    receipt = retire_f1d_legacy_artifacts(
        state_root=state_root,
        transaction_id=str(uuid.uuid4()),
        candidate=_generation(),
        recorded_at="2026-08-06T00:00:00+00:00",
    )

    assert not old_journal.exists()
    assert receipt.version == 8
    assert "pinned-runtime-rebuild-v5.json" in receipt.requested_paths
    assert legacy_tombstone_receipt_path(
        state_root,
        pinset_sha256=_generation().pinset_sha256,
    ).exists()


def test_v8_tombstone_retires_static_v6_v7_journals_and_receipts(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o700)
    old_journal = state_root / "pinned-runtime-rebuild-v6.json"
    old_journal.write_text('{"version":6}\n', encoding="utf-8")
    os.chmod(old_journal, 0o600)
    old_receipt_directory = state_root / "pinned-runtime-v6"
    old_receipt_directory.mkdir(mode=0o700)
    os.chmod(old_receipt_directory, 0o700)
    old_receipt = old_receipt_directory / "legacy-tombstone-v6.json"
    old_receipt.write_text('{"version":6}\n', encoding="utf-8")
    os.chmod(old_receipt, 0o600)
    old_v7_journal = state_root / "pinned-runtime-rebuild-v7.json"
    old_v7_journal.write_text('{"version":7}\n', encoding="utf-8")
    os.chmod(old_v7_journal, 0o600)
    old_v7_receipt_directory = state_root / "pinned-runtime-v7"
    old_v7_receipt_directory.mkdir(mode=0o700)
    os.chmod(old_v7_receipt_directory, 0o700)
    old_v7_receipt = old_v7_receipt_directory / "legacy-tombstone-v7.json"
    old_v7_receipt.write_text('{"version":7}\n', encoding="utf-8")
    os.chmod(old_v7_receipt, 0o600)
    old_v7_pinset_journal = (
        state_root / f"pinned-runtime-rebuild-v7-{_generation().pinset_sha256}.json"
    )
    old_v7_pinset_journal.write_text('{"version":7,"pinset":true}\n', encoding="utf-8")
    os.chmod(old_v7_pinset_journal, 0o600)
    old_v7_pinset_receipt = (
        state_root / f"legacy-tombstone-v7-{_generation().pinset_sha256}.json"
    )
    old_v7_pinset_receipt.write_text('{"version":7,"pinset":true}\n', encoding="utf-8")
    os.chmod(old_v7_pinset_receipt, 0o600)

    receipt = retire_f1d_legacy_artifacts(
        state_root=state_root,
        transaction_id=str(uuid.uuid4()),
        candidate=_generation(),
        recorded_at="2026-08-06T00:00:00+00:00",
    )

    assert not old_journal.exists()
    assert not old_receipt.exists()
    assert not old_v7_journal.exists()
    assert not old_v7_receipt.exists()
    assert not old_v7_pinset_journal.exists()
    assert not old_v7_pinset_receipt.exists()
    assert receipt.version == 8
    assert {
        "pinned-runtime-rebuild-v6.json",
        "pinned-runtime-v6/legacy-tombstone-v6.json",
        "pinned-runtime-rebuild-v7.json",
        "pinned-runtime-v7/legacy-tombstone-v7.json",
        f"pinned-runtime-rebuild-v7-{_generation().pinset_sha256}.json",
        f"legacy-tombstone-v7-{_generation().pinset_sha256}.json",
    } <= set(entry.relative_path for entry in receipt.retired)
    assert legacy_tombstone_receipt_path(
        state_root,
        pinset_sha256=_generation().pinset_sha256,
    ).exists()


def test_legacy_tombstone_rejects_unsafe_artifact_before_receipt(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    os.chmod(state_root, 0o700)
    legacy = state_root / "compatible-pair-v4.json"
    legacy.write_text("{}\n", encoding="utf-8")
    os.chmod(legacy, 0o644)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        retire_f1d_legacy_artifacts(
            state_root=state_root,
            transaction_id=str(uuid.uuid4()),
            candidate=_generation(),
            recorded_at="2026-08-06T00:00:00+00:00",
        )

    assert not legacy_tombstone_receipt_path(
        state_root,
        pinset_sha256=_generation().pinset_sha256,
    ).exists()
