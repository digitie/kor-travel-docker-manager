from __future__ import annotations

import os
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import (
    CompatibleImagePair,
    ComposePostMutationContractError,
    DeploymentContractError,
    initial_pair_manifest,
    new_image_pair,
)
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
)
from kor_travel_docker_manager.services.compose_service import ComposeService
from kor_travel_docker_manager.services.pinned_drift_bootstrap import (
    PinnedDriftBootstrapJournal,
    assert_pinned_drift_bootstrap_allows_pair_mutation,
    assert_pinned_drift_bootstrap_frozen_inputs,
    assert_pinned_drift_bootstrap_inputs,
    prepare_pinned_drift_bootstrap,
    read_pinned_drift_bootstrap,
    transition_pinned_drift_bootstrap,
    write_pinned_drift_bootstrap,
)


def _pair(seed: str) -> CompatibleImagePair:
    return new_image_pair(
        f"sha256:{seed * 64}",
        f"sha256:{seed * 64}",
        "gen7",
        map_ui_image_id=f"sha256:{seed * 64}",
        map_dagster_image_id=f"sha256:{seed * 64}",
        map_dagster_daemon_image_id=f"sha256:{seed * 64}",
        map_source_revision=seed * 40,
        pinvi_source_revision=seed * 40,
    )


def _journal() -> PinnedDriftBootstrapJournal:
    return prepare_pinned_drift_bootstrap(
        production_pin_version=1,
        environment_sha256="1" * 64,
        compose_sha256="2" * 64,
        resolved_compose_sha256="3" * 64,
        old_manifest_sha256="4" * 64,
        old_active=_pair("a"),
        old_rollback=_pair("b"),
        candidate=_pair("c"),
        database_heads={
            "map_application": "0078_cache_target_gc_observe",
            "map_dagster": "abc123",
            "pinvi": "20260802_0048",
        },
    )


def test_pinned_drift_journal_is_durable_and_blocks_other_pair_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path = tmp_path / "pinned-drift-bootstrap-v1.json"
    journal = _journal()
    write_pinned_drift_bootstrap(journal_path, journal)

    assert read_pinned_drift_bootstrap(journal_path) == journal
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.pinned_drift_bootstrap.pinned_drift_bootstrap_journal_path",
        lambda _values: journal_path,
    )
    with pytest.raises(DeploymentContractError, match="unfinished"):
        assert_pinned_drift_bootstrap_allows_pair_mutation({})

    activated = transition_pinned_drift_bootstrap(journal, "runtime_activated")
    manifest_committing = transition_pinned_drift_bootstrap(
        activated,
        "manifest_committing",
    )
    committed = transition_pinned_drift_bootstrap(manifest_committing, "committed")
    write_pinned_drift_bootstrap(journal_path, committed)

    assert_pinned_drift_bootstrap_allows_pair_mutation({})


def test_pinned_drift_journal_rejects_changed_frozen_input() -> None:
    journal = _journal()

    with pytest.raises(DeploymentContractError, match="inputs changed"):
        assert_pinned_drift_bootstrap_inputs(
            journal,
            production_pin_version=1,
            environment_sha256="f" * 64,
            compose_sha256="2" * 64,
            resolved_compose_sha256="3" * 64,
            old_manifest_sha256="4" * 64,
            database_heads={
                "map_application": "0078_cache_target_gc_observe",
                "map_dagster": "abc123",
                "pinvi": "20260802_0048",
            },
        )


def test_pinned_drift_terminal_revalidation_keeps_frozen_evidence() -> None:
    journal = transition_pinned_drift_bootstrap(
        transition_pinned_drift_bootstrap(
            transition_pinned_drift_bootstrap(_journal(), "runtime_activated"),
            "manifest_committing",
        ),
        "committed",
    )

    with pytest.raises(DeploymentContractError, match="inputs changed"):
        assert_pinned_drift_bootstrap_frozen_inputs(
            journal,
            production_pin_version=2,
            environment_sha256="1" * 64,
            compose_sha256="2" * 64,
            resolved_compose_sha256="3" * 64,
            database_heads={
                "map_application": "0078_cache_target_gc_observe",
                "map_dagster": "abc123",
                "pinvi": "20260802_0048",
            },
        )


def test_pinned_drift_journal_rejects_non_owner_only_mode(tmp_path: Path) -> None:
    journal_path = tmp_path / "pinned-drift-bootstrap-v1.json"
    write_pinned_drift_bootstrap(journal_path, _journal())
    os.chmod(journal_path, 0o644)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_pinned_drift_bootstrap(journal_path)


def test_manifest_commit_crash_resumes_from_candidate_only_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    old = _pair("a")
    candidate = _pair("c")
    manifest = initial_pair_manifest(candidate)
    manifest_path = tmp_path / "compatible-pair-v4.json"
    manifest_path.write_text("candidate-only\n", encoding="utf-8")
    transaction = SimpleNamespace(
        environment=SimpleNamespace(
            effective={"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
            env_path=str(tmp_path / ".env"),
            env_file_bytes=b"environment",
            compose_path=str(tmp_path / "docker-compose.yml"),
            env_file_identity=SimpleNamespace(uid=1000, gid=1000),
        ),
        compose_source_bytes=b"compose",
        resolved_document_hash="3" * 64,
        manifest_path=str(manifest_path),
    )
    journal = prepare_pinned_drift_bootstrap(
        production_pin_version=CACHE_TARGET_PRODUCTION_PINS.version,
        environment_sha256=sha256(b"environment").hexdigest(),
        compose_sha256=sha256(b"compose").hexdigest(),
        resolved_compose_sha256="3" * 64,
        old_manifest_sha256="4" * 64,
        old_active=old,
        old_rollback=old,
        candidate=candidate,
        database_heads={
            "map_application": "0078_cache_target_gc_observe",
            "map_dagster": "abc123",
            "pinvi": "20260802_0048",
        },
    )
    journal = transition_pinned_drift_bootstrap(journal, "runtime_activated")
    journal = transition_pinned_drift_bootstrap(journal, "manifest_committing")
    database_heads = dict(journal.database_heads)
    writes: list[PinnedDriftBootstrapJournal] = []
    write_manifest = Mock()

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: nullcontext(SimpleNamespace()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_assert_transaction_matches_c6c_lock",
        Mock(),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, None)),
    )
    monkeypatch.setattr(compose_service_module, "assert_manager_mutation_allowed", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "load_c6c_deployment_config_from_environment",
        Mock(return_value=SimpleNamespace(production=True)),
    )
    monkeypatch.setattr(
        compose_service_module,
        "require_committed_pinned_source_installation",
        Mock(),
    )
    monkeypatch.setattr(compose_service_module, "_require_cache_target_release", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "pinned_drift_bootstrap_journal_path",
        lambda _values: tmp_path / "pinned-drift-bootstrap-v1.json",
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_pinned_drift_bootstrap",
        Mock(return_value=journal),
    )
    monkeypatch.setattr(
        compose_service_module,
        "load_pair_manifest",
        Mock(return_value=manifest),
    )
    monkeypatch.setattr(service, "_pinned_drift_database_heads", Mock(return_value=database_heads))
    monkeypatch.setattr(service, "_require_pair_image_provenance", Mock())
    monkeypatch.setattr(service, "_assert_pinned_drift_candidate_database_heads", Mock())
    monkeypatch.setattr(compose_service_module, "ensure_pair_references", Mock())
    monkeypatch.setattr(
        service,
        "_verify_pinned_drift_candidate_or_halt",
        Mock(return_value={"verified": True}),
    )
    monkeypatch.setattr(
        compose_service_module,
        "write_pinned_drift_bootstrap",
        lambda _path, persisted: writes.append(persisted),
    )
    monkeypatch.setattr(compose_service_module, "write_pair_manifest", write_manifest)
    monkeypatch.setattr(
        compose_service_module,
        "reconcile_pair_references",
        Mock(return_value=SimpleNamespace(removed=[])),
    )
    monkeypatch.setattr(service, "_pair_provenance_payload", Mock(return_value={}))

    result = service.bootstrap_pinned_drift()

    assert result["state"] == "committed"
    assert result["resumed"] is True
    write_manifest.assert_not_called()
    assert writes[-1].phase == "committed"


def test_runtime_reverification_failure_halts_without_old_pair_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    result = {"success": True, "returncode": 0, "stderr": "", "stages": []}
    verification = Mock(side_effect=DeploymentContractError("verification failed"))
    halt = Mock(return_value={"success": True, "state": "halted_requires_operator"})
    old_recovery = Mock()

    monkeypatch.setattr(service, "_verify_active_contract", verification)
    monkeypatch.setattr(service, "_halt_c6c_pair", halt)
    monkeypatch.setattr(service, "_recover_previous_pair", old_recovery)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service._verify_pinned_drift_candidate_or_halt(
            result=result,
            config=SimpleNamespace(),
            candidate=_pair("c"),
            services=["kor-travel-map-api", "pinvi-api"],
            transaction=SimpleNamespace(),
            expected_database_heads={
                "map_application": "0078_cache_target_gc_observe",
                "map_dagster": "abc123",
                "pinvi": "20260802_0048",
            },
        )

    assert caught.value.restoration == {
        "success": True,
        "state": "halted_requires_operator",
    }
    halt.assert_called_once()
    old_recovery.assert_not_called()


@pytest.mark.parametrize(
    ("runtime_tuple_matches", "expected_error", "build_expected"),
    (
        (False, "candidate Map API", True),
        (True, "requires a runtime tuple", False),
    ),
    ids=("candidate_head_mismatch", "already_active_tuple"),
)
def test_pinned_drift_preflight_blocks_before_runtime_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_tuple_matches: bool,
    expected_error: str,
    build_expected: bool,
) -> None:
    service = ComposeService()
    old = _pair("a")
    candidate = _pair("c")
    manifest = initial_pair_manifest(old)
    manifest_path = tmp_path / "compatible-pair-v4.json"
    manifest_path.write_text("old-pair\n", encoding="utf-8")
    transaction = SimpleNamespace(
        environment=SimpleNamespace(
            effective={"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
            env_path=str(tmp_path / ".env"),
            env_file_bytes=b"environment",
            compose_path=str(tmp_path / "docker-compose.yml"),
            env_file_identity=SimpleNamespace(uid=1000, gid=1000),
        ),
        compose_source_bytes=b"compose",
        resolved_document_hash="3" * 64,
        manifest_path=str(manifest_path),
    )
    candidate_build = Mock(
        return_value=SimpleNamespace(
            map_source_revision=candidate.map_source_revision,
            pinvi_source_revision=candidate.pinvi_source_revision,
        )
    )
    prepare_candidate = Mock(return_value=(candidate, None))
    activate_candidate = Mock()
    retention = Mock()

    monkeypatch.setattr(
        compose_service_module,
        "c6c_deployment_lock_from_environment",
        lambda: nullcontext(SimpleNamespace()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_assert_transaction_matches_c6c_lock",
        Mock(),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, None)),
    )
    monkeypatch.setattr(compose_service_module, "assert_manager_mutation_allowed", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "load_c6c_deployment_config_from_environment",
        Mock(return_value=SimpleNamespace(production=True)),
    )
    monkeypatch.setattr(
        compose_service_module,
        "require_committed_pinned_source_installation",
        Mock(),
    )
    monkeypatch.setattr(compose_service_module, "_require_cache_target_release", Mock())
    monkeypatch.setattr(
        compose_service_module,
        "pinned_drift_bootstrap_journal_path",
        lambda _values: tmp_path / "pinned-drift-bootstrap-v1.json",
    )
    monkeypatch.setattr(
        compose_service_module,
        "read_pinned_drift_bootstrap",
        Mock(return_value=None),
    )
    monkeypatch.setattr(
        compose_service_module,
        "load_pair_manifest",
        Mock(return_value=manifest),
    )
    monkeypatch.setattr(
        service,
        "_pinned_drift_database_heads",
        Mock(
            return_value={
                "map_application": "0078_cache_target_gc_observe",
                "map_dagster": "abc123",
                "pinvi": "20260802_0048",
            }
        ),
    )
    monkeypatch.setattr(service, "_require_pair_image_provenance", Mock())
    monkeypatch.setattr(
        service,
        "_validate_resolved_compose_contract",
        Mock(),
    )
    monkeypatch.setattr(service, "_require_services_ready", Mock())
    monkeypatch.setattr(
        service,
        "_current_runtime_image_tuple_matches_pair",
        Mock(return_value=runtime_tuple_matches),
    )
    monkeypatch.setattr(service, "_inspect_c6c_runtime_configs", Mock(return_value={}))
    monkeypatch.setattr(
        compose_service_module,
        "validate_runtime_secret_isolation",
        Mock(),
    )
    monkeypatch.setattr(compose_service_module, "run_map_ui_auth_preflight", Mock(return_value=[]))
    monkeypatch.setattr(
        service,
        "_prepare_c6c_candidate_pair",
        prepare_candidate,
    )
    monkeypatch.setattr(
        service,
        "_assert_pinned_drift_candidate_database_heads",
        Mock(side_effect=DeploymentContractError("candidate Map API head differs")),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_derive_c6c_build_provenance",
        candidate_build,
    )
    monkeypatch.setattr(compose_service_module, "ensure_pair_references", retention)
    monkeypatch.setattr(service, "_activate_pair_sequentially", activate_candidate)

    with pytest.raises(DeploymentContractError, match=expected_error):
        service.bootstrap_pinned_drift()

    if build_expected:
        candidate_build.assert_called_once()
        prepare_candidate.assert_called_once()
    else:
        candidate_build.assert_not_called()
        prepare_candidate.assert_not_called()
    retention.assert_not_called()
    activate_candidate.assert_not_called()


def test_pinned_drift_start_tuple_check_allows_mixed_legacy_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    pair = new_image_pair(
        "sha256:" + "a" * 64,
        "sha256:" + "e" * 64,
        "gen7",
        map_ui_image_id="sha256:" + "b" * 64,
        map_dagster_image_id="sha256:" + "c" * 64,
        map_dagster_daemon_image_id="sha256:" + "d" * 64,
        map_source_revision="f" * 40,
        pinvi_source_revision="0" * 40,
    )
    service_images = dict(
        zip(
            compose_service_module._MAP_RUNTIME_SERVICES,
            (
                pair.map_image_id,
                pair.map_ui_image_id,
                pair.map_dagster_image_id,
                pair.map_dagster_daemon_image_id,
            ),
            strict=True,
        )
    )
    container_images = {
        container_name: service_images[service_name]
        for service_name, container_name in compose_service_module._MAP_RUNTIME_CONTAINERS.items()
    }
    container_images["pinvi-api"] = pair.pinvi_image_id
    monkeypatch.setattr(
        service,
        "_inspect_container_image_id",
        Mock(side_effect=lambda container: container_images[container]),
    )
    source_revision = Mock(side_effect=AssertionError("legacy revisions are not read"))
    monkeypatch.setattr(service, "_inspect_image_source_revision", source_revision)

    assert service._current_runtime_image_tuple_matches_pair(
        SimpleNamespace(pinvi_container="pinvi-api"), pair
    )
    for container_name in (
        compose_service_module._MAP_RUNTIME_CONTAINERS[
            compose_service_module._MAP_UI_SERVICE
        ],
        compose_service_module._MAP_RUNTIME_CONTAINERS[
            compose_service_module._MAP_DAGSTER_SERVICE
        ],
        "pinvi-api",
    ):
        original_image = container_images[container_name]
        container_images[container_name] = "sha256:" + "9" * 64
        assert not service._current_runtime_image_tuple_matches_pair(
            SimpleNamespace(pinvi_container="pinvi-api"), pair
        )
        container_images[container_name] = original_image
    source_revision.assert_not_called()
