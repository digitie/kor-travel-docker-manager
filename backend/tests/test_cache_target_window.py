from __future__ import annotations

import json
import stat
from dataclasses import asdict
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import c6c_deployment
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_window import (
    CacheTargetWindowJournal,
    DatabaseBackupReceipt,
    logical_sha256,
    map_helper_receipt_sha256,
    old_restore_is_authorized,
    parse_map_helper_receipt,
    prepare_cache_target_window,
    read_cache_target_window,
    transition_cache_target_window,
    write_cache_target_window,
)

_TRANSACTION_ID = "11111111-1111-4111-8111-111111111111"
_CUTOVER_ID = "22222222-2222-4222-8222-222222222222"
_MAP_REVISION = "a" * 40
_DATABASE_IDENTITY = "b" * 64


def _prepared() -> CacheTargetWindowJournal:
    return prepare_cache_target_window(
        transaction_id=_TRANSACTION_ID,
        cutover_id=_CUTOVER_ID,
        expected_restore_epoch=3,
        reason="production H35 and generation 7 cutover",
        environment_sha256="1" * 64,
        compose_sha256="2" * 64,
        resolved_compose_sha256="3" * 64,
        old_manifest_sha256="4" * 64,
    )


def _backup(seed: str, schema: str) -> DatabaseBackupReceipt:
    return DatabaseBackupReceipt(
        database_identity=seed * 64,
        schema_revision=schema,
        logical_backup_id=f"{seed * 8}-{seed * 4}-4{seed * 3}-8{seed * 3}-{seed * 12}",
        byte_size=1024,
        sha256=("f" if seed != "f" else "e") * 64,
    )


def _backups_committed() -> CacheTargetWindowJournal:
    return transition_cache_target_window(
        _prepared(),
        "backups_committed",
        rollback_bundle_sha256="5" * 64,
        map_application_backup=_backup("1", "0063_pipeline_root_id"),
        map_dagster_backup=_backup("2", "0063_pipeline_root_id"),
        pinvi_backup=_backup("3", "0007_cache_target_generation"),
    )


def test_window_journal_is_owner_only_and_exactly_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "state" / "cache-target-window-v1.json"
    path.parent.mkdir(mode=0o700)
    journal = _backups_committed()

    write_cache_target_window(path, journal)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_cache_target_window(path) == journal


def test_window_rejects_phase_skip_and_old_restore_after_external_event() -> None:
    with pytest.raises(DeploymentContractError, match="phase transition"):
        transition_cache_target_window(_prepared(), "candidate_built")

    journal = _backups_committed()
    journal = transition_cache_target_window(
        journal,
        "candidate_built",
        candidate_pair_sha256="6" * 64,
    )
    journal = transition_cache_target_window(
        journal,
        "databases_forwarded",
        last_map_receipt_sha256="7" * 64,
    )
    journal = transition_cache_target_window(
        journal,
        "csv_forwarded",
        last_map_receipt_sha256="8" * 64,
    )
    journal = transition_cache_target_window(journal, "generation_bootstrapped")
    journal = transition_cache_target_window(
        journal,
        "initial_committed",
        initial_receipt_sha256="9" * 64,
        external_event_count=12,
    )

    assert old_restore_is_authorized(journal) is False
    with pytest.raises(DeploymentContractError, match="old restore is forbidden"):
        transition_cache_target_window(journal, "rollback_preparing")


def test_window_allows_ordered_pre_event_coupled_rollback() -> None:
    journal = transition_cache_target_window(_prepared(), "rollback_preparing")
    journal = transition_cache_target_window(journal, "new_runtime_stopped")

    assert journal.phase == "new_runtime_stopped"
    assert old_restore_is_authorized(journal) is True


def test_unfinished_window_blocks_foreign_manager_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setattr(
        c6c_deployment,
        "_C6C_PRODUCTION_STATE_ROOT",
        state_root,
    )
    environment = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
        "PINVI_ENVIRONMENT": "production",
        "COMPOSE_PROJECT_NAME": "pinvi-prod",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
    }
    path = c6c_deployment.cache_target_window_journal_path(environment)
    path.parent.mkdir(mode=0o700, parents=True)
    write_cache_target_window(path, _prepared())

    with pytest.raises(DeploymentContractError, match="blocks every other"):
        c6c_deployment.assert_manager_mutation_allowed(environment=environment)

    with c6c_deployment.cache_target_window_mutation_scope(
        _TRANSACTION_ID,
        capability=c6c_deployment._CACHE_TARGET_WINDOW_MUTATION_CAPABILITY,
    ):
        assert (
            c6c_deployment.assert_manager_mutation_allowed(environment=environment)
            == "production"
        )

    foreign = "33333333-3333-4333-8333-333333333333"
    with c6c_deployment.cache_target_window_mutation_scope(
        foreign,
        capability=c6c_deployment._CACHE_TARGET_WINDOW_MUTATION_CAPABILITY,
    ):
        with pytest.raises(DeploymentContractError, match="blocks every other"):
            c6c_deployment.assert_manager_mutation_allowed(environment=environment)


def test_map_helper_receipt_requires_exact_secret_free_binding() -> None:
    request = {
        "contract_version": "h35-map/v1",
        "operation": "preflight",
        "transaction_id": _TRANSACTION_ID,
        "source_revision": _MAP_REVISION,
        "database_identity": _DATABASE_IDENTITY,
        "prior_receipt_digest": None,
    }
    document = {
        **request,
        "status": "accepted",
        "request_digest": logical_sha256(request),
        "schema_before": "0063_pipeline_root_id",
        "schema_after": "0063_pipeline_root_id",
        "forward_boundary": "not_crossed",
        "row_counts": {"public_item_count": 3265},
        "checks": [
            {
                "name": "identity_violations",
                "expected": 0,
                "observed": 0,
                "passed": True,
            }
        ],
        "runtime_mutation_count": 0,
        "external_event_count": 0,
    }
    receipt = parse_map_helper_receipt(
        stdout=json.dumps(document, separators=(",", ":")) + "\n",
        stderr="",
        operation="preflight",
        transaction_id=_TRANSACTION_ID,
        source_revision=_MAP_REVISION,
        database_identity=_DATABASE_IDENTITY,
        request=request,
        prior_receipt_digest=None,
    )

    assert receipt.row_counts == {"public_item_count": 3265}
    assert map_helper_receipt_sha256(receipt) == logical_sha256(asdict(receipt))

    with pytest.raises(DeploymentContractError, match="one JSON line"):
        parse_map_helper_receipt(
            stdout=json.dumps(document) + "\n{}\n",
            stderr="",
            operation="preflight",
            transaction_id=_TRANSACTION_ID,
            source_revision=_MAP_REVISION,
            database_identity=_DATABASE_IDENTITY,
            request=request,
            prior_receipt_digest=None,
        )


def test_map_helper_receipt_rejects_runtime_mutation_and_extra_key() -> None:
    request = {
        "contract_version": "h35-map/v1",
        "operation": "preflight",
        "transaction_id": _TRANSACTION_ID,
        "source_revision": _MAP_REVISION,
        "database_identity": _DATABASE_IDENTITY,
        "prior_receipt_digest": None,
    }
    base = {
        **request,
        "status": "accepted",
        "request_digest": logical_sha256(request),
        "schema_before": "0063_pipeline_root_id",
        "schema_after": "0063_pipeline_root_id",
        "forward_boundary": "not_crossed",
        "row_counts": {"public_item_count": 3265},
        "checks": [
            {"name": "identity", "expected": 0, "observed": 0, "passed": True}
        ],
        "runtime_mutation_count": 1,
        "external_event_count": 0,
    }

    with pytest.raises(DeploymentContractError, match="binding"):
        parse_map_helper_receipt(
            stdout=json.dumps(base) + "\n",
            stderr="",
            operation="preflight",
            transaction_id=_TRANSACTION_ID,
            source_revision=_MAP_REVISION,
            database_identity=_DATABASE_IDENTITY,
            request=request,
            prior_receipt_digest=None,
        )

    with pytest.raises(DeploymentContractError, match="receipt is invalid"):
        parse_map_helper_receipt(
            stdout=json.dumps(
                {**base, "runtime_mutation_count": 0, "extra": True}
            )
            + "\n",
            stderr="",
            operation="preflight",
            transaction_id=_TRANSACTION_ID,
            source_revision=_MAP_REVISION,
            database_identity=_DATABASE_IDENTITY,
            request=request,
            prior_receipt_digest=None,
        )
