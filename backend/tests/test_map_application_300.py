from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from kor_travel_docker_manager.services.map_application_300 import (
    APPLICATION_DATABASE_OWNER,
    Application300Candidate,
    Application300Contract,
    ApplicationDatabaseIdentity,
    DagsterDatabaseIdentity,
    DagsterLoginRoleAttributes,
    DagsterStorageCandidate,
    FreshRootResult,
    JournalStamp,
    MapApplication300ContractError,
    application_database_identity_sha256,
    build_application_final_permit,
    build_dagster_metadata_permit,
    build_fresh_finalize_fence,
    build_fresh_migration_fence,
    canonical_json_bytes,
    expected_application_300_source_commit,
    json_artifact,
    parse_fresh_finalize_missing_receipt,
    parse_fresh_finalize_result,
    parse_fresh_root_missing_receipt,
    parse_fresh_root_result,
    publish_root_read_only_artifact,
    read_owner_only_artifact,
    read_root_read_only_artifact,
    replace_root_read_only_artifact,
    sha256_bytes,
    validate_application_final_permit,
    validate_dagster_metadata_permit,
    write_owner_only_artifact,
)


def _digest(seed: str) -> str:
    return seed * 64


def _image(seed: str) -> str:
    return f"sha256:{seed * 64}"


def _contract() -> Application300Contract:
    return Application300Contract(
        reference_manifest_sha256=_digest("1"),
        postgres_image_id=_image("2"),
        source_catalog_sha256=_digest("3"),
        destination_catalog_sha256=_digest("4"),
        seed_sha256=_digest("5"),
        privileged_residue_sha256=_digest("6"),
        source_alembic_version_sha256=_digest("7"),
        destination_alembic_version_sha256=_digest("8"),
        runtime_invariants_sql_sha256=_digest("9"),
    )


def _candidate() -> Application300Candidate:
    return Application300Candidate(
        map_source_commit=expected_application_300_source_commit(),
        api_image_id=_image("a"),
        dagster_image_id=_image("b"),
    )


def _application_database() -> ApplicationDatabaseIdentity:
    return ApplicationDatabaseIdentity(
        name="kor_travel_map",
        oid=127001,
        owner=APPLICATION_DATABASE_OWNER,
        system_identifier="7474747474747474747",
    )


def _journal(seed: str, generation: int) -> JournalStamp:
    return JournalStamp(
        transaction_id=str(uuid4()),
        operation_id=str(uuid4()),
        journal_sha256=_digest(seed),
        journal_generation=generation,
    )


def _expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def _root_result_raw(
    *,
    root_fence_sha256: str,
    root_transaction_id: str,
    root_operation_id: str,
    root_journal_sha256: str,
    root_generation: int,
) -> bytes:
    contract = _contract()
    candidate = _candidate()
    database = _application_database()
    return canonical_json_bytes(
        {
            "schema": "kor-travel-map.application-fresh-300-root.v2",
            "outcome": "root-committed",
            "authorization": "manager-fence",
            "operation_id": root_operation_id,
            "destination_head": "300",
            "map_candidate_commit": candidate.map_source_commit,
            "map_candidate_image_id": candidate.api_image_id,
            "postgres_image_id": contract.postgres_image_id,
            "reference_manifest_sha256": contract.reference_manifest_sha256,
            "writer_fence_receipt_sha256": root_fence_sha256,
            "writer_fence_transaction_id": root_transaction_id,
            "journal_sha256": root_journal_sha256,
            "journal_generation": root_generation,
            "database_identity": {
                "database_name": database.name,
                "database_oid": database.oid,
                "database_owner": database.owner,
                "postgres_system_identifier": database.system_identifier,
            },
            "post_source_catalog_sha256": contract.source_catalog_sha256,
            "post_seed_sha256": contract.seed_sha256,
            "expected_privileged_residue_sha256": contract.privileged_residue_sha256,
            "expected_destination_alembic_version_sha256": (
                contract.destination_alembic_version_sha256
            ),
            "post_destination_alembic_version_sha256": (
                contract.destination_alembic_version_sha256
            ),
        }
    ) + b"\n"


def _fresh_root() -> tuple[bytes, object]:
    root_journal = _journal("c", 1)
    root_fence = build_fresh_migration_fence(
        contract=_contract(),
        candidate=_candidate(),
        database=_application_database(),
        journal=root_journal,
        writer_fence_expires_at=_expiry(),
    )
    raw = _root_result_raw(
        root_fence_sha256=root_fence.sha256,
        root_transaction_id=root_journal.transaction_id,
        root_operation_id=root_journal.operation_id,
        root_journal_sha256=root_journal.journal_sha256,
        root_generation=root_journal.journal_generation,
    )
    return raw, parse_fresh_root_result(
        raw, contract=_contract(), candidate=_candidate()
    )


def _root_missing_receipt_raw(
    *,
    root_fence_sha256: str,
    root_transaction_id: str,
    root_operation_id: str,
    root_journal_sha256: str,
    root_generation: int,
) -> bytes:
    contract = _contract()
    candidate = _candidate()
    database = _application_database()
    return canonical_json_bytes(
        {
            "schema": (
                "kor-travel-map.application-fresh-300-"
                "root-missing-receipt.v1"
            ),
            "outcome": "receipt-missing-exact-prestate",
            "operation_id": root_operation_id,
            "destination_head": "300",
            "map_candidate_commit": candidate.map_source_commit,
            "map_candidate_image_id": candidate.api_image_id,
            "postgres_image_id": contract.postgres_image_id,
            "reference_manifest_sha256": contract.reference_manifest_sha256,
            "writer_fence_receipt_sha256": root_fence_sha256,
            "writer_fence_transaction_id": root_transaction_id,
            "journal_sha256": root_journal_sha256,
            "journal_generation": root_generation,
            "database_identity": {
                "database_name": database.name,
                "database_oid": database.oid,
                "database_owner": database.owner,
                "postgres_system_identifier": database.system_identifier,
            },
            "pre_root_state_schema": (
                "kor-travel-map.application-fresh-300-pre-root.v1"
            ),
            "expected_post_source_catalog_sha256": contract.source_catalog_sha256,
            "expected_post_seed_sha256": contract.seed_sha256,
            "expected_post_destination_alembic_version_sha256": (
                contract.destination_alembic_version_sha256
            ),
        }
    ) + b"\n"


def _finalize_result_raw(
    *,
    finalize_fence_sha256: str,
    finalize_transaction_id: str,
    finalize_operation_id: str,
    finalize_journal_sha256: str,
    finalize_generation: int,
    prior_result_sha256: str,
    prior_fence_sha256: str,
    prior_transaction_id: str,
    prior_operation_id: str,
    prior_journal_sha256: str,
    prior_generation: int,
) -> bytes:
    contract = _contract()
    candidate = _candidate()
    return canonical_json_bytes(
        {
            "schema": "kor-travel-map.application-fresh-300-finalize.v4",
            "outcome": "finalized",
            "operation_id": finalize_operation_id,
            "destination_head": "300",
            "map_candidate_commit": candidate.map_source_commit,
            "map_candidate_image_id": candidate.api_image_id,
            "postgres_image_id": contract.postgres_image_id,
            "reference_manifest_sha256": contract.reference_manifest_sha256,
            "writer_fence_receipt_sha256": finalize_fence_sha256,
            "writer_fence_transaction_id": finalize_transaction_id,
            "journal_sha256": finalize_journal_sha256,
            "journal_generation": finalize_generation,
            "database_identity": {
                "database_name": _application_database().name,
                "database_oid": _application_database().oid,
                "database_owner": _application_database().owner,
                "postgres_system_identifier": (
                    _application_database().system_identifier
                ),
            },
            "prior_fresh_migration_result_sha256": prior_result_sha256,
            "prior_fresh_migration_fence_sha256": prior_fence_sha256,
            "prior_fresh_migration_transaction_id": prior_transaction_id,
            "prior_fresh_migration_operation_id": prior_operation_id,
            "prior_fresh_migration_journal_sha256": prior_journal_sha256,
            "prior_fresh_migration_generation": prior_generation,
            "pre_source_catalog_sha256": contract.source_catalog_sha256,
            "pre_seed_sha256": contract.seed_sha256,
            "post_destination_catalog_sha256": contract.destination_catalog_sha256,
            "post_seed_sha256": contract.seed_sha256,
            "expected_privileged_residue_sha256": contract.privileged_residue_sha256,
            "post_destination_alembic_version_sha256": (
                contract.destination_alembic_version_sha256
            ),
        }
    ) + b"\n"


def _finalize_missing_receipt_raw(
    *, operation_id: str, prior: FreshRootResult
) -> bytes:
    contract = _contract()
    candidate = _candidate()
    database = _application_database()
    return canonical_json_bytes(
        {
            "schema": (
                "kor-travel-map.application-fresh-300-finalize-missing-receipt.v1"
            ),
            "outcome": "receipt-missing-exact-prestate",
            "operation_id": operation_id,
            "prior_fresh_migration_operation_id": prior.operation_id,
            "prior_fresh_migration_result_sha256": prior.payload_sha256,
            "destination_head": "300",
            "map_candidate_commit": candidate.map_source_commit,
            "map_candidate_image_id": candidate.api_image_id,
            "postgres_image_id": contract.postgres_image_id,
            "reference_manifest_sha256": contract.reference_manifest_sha256,
            "database_identity": {
                "database_name": database.name,
                "database_oid": database.oid,
                "database_owner": database.owner,
                "postgres_system_identifier": database.system_identifier,
            },
            "pre_source_catalog_sha256": contract.source_catalog_sha256,
            "pre_seed_sha256": contract.seed_sha256,
            "pre_destination_alembic_version_sha256": (
                contract.destination_alembic_version_sha256
            ),
        }
    ) + b"\n"


def test_canonical_json_and_sha_are_stable() -> None:
    raw = canonical_json_bytes({"b": 2, "a": 1})

    assert raw == b'{"a":1,"b":2}'
    assert json_artifact({"b": 2, "a": 1}).sha256 == sha256_bytes(raw)


def test_candidate_is_fixed_to_reviewed_map_commit() -> None:
    with pytest.raises(MapApplication300ContractError, match="fixed release"):
        Application300Candidate(
            map_source_commit="0" * 40,
            api_image_id=_image("a"),
            dagster_image_id=_image("b"),
        )


def test_fresh_migration_fence_matches_map_executable_field_set() -> None:
    fence = build_fresh_migration_fence(
        contract=_contract(),
        candidate=_candidate(),
        database=_application_database(),
        journal=_journal("c", 1),
        writer_fence_expires_at=_expiry(),
    )
    payload = json.loads(fence.raw)

    assert set(payload) == {
        "schema",
        "transaction_id",
        "operation_id",
        "journal_sha256",
        "journal_generation",
        "operation",
        "map_candidate_commit",
        "map_candidate_image_id",
        "postgres_image_id",
        "destination_head",
        "reference_manifest_sha256",
        "source_catalog_sha256",
        "destination_catalog_sha256",
        "seed_sha256",
        "privileged_residue_sha256",
        "source_alembic_version_sha256",
        "destination_alembic_version_sha256",
        "runtime_invariants_sql_sha256",
        "database_name",
        "database_oid",
        "database_owner",
        "postgres_system_identifier",
        "writer_fence_expires_at",
    }
    assert payload["schema"] == "kor-travel-docker-manager.map-fresh-300-migrate-fence.v2"
    assert payload["operation"] == "map-fresh-300"
    assert payload["map_candidate_image_id"] == _candidate().api_image_id
    assert "dagster_image_id" not in payload


def test_root_result_parser_rejects_extra_fields_and_binds_database() -> None:
    root_raw, root = _fresh_root()
    payload = json.loads(root_raw)
    payload["extra"] = "not allowed"

    assert root.database_identity == _application_database()
    assert root.payload_sha256 == sha256_bytes(root_raw)
    with pytest.raises(MapApplication300ContractError, match="field set"):
        parse_fresh_root_result(
            canonical_json_bytes(payload) + b"\n",
            contract=_contract(),
            candidate=_candidate(),
        )


def test_root_missing_receipt_parser_is_exactly_bound_to_prestate() -> None:
    journal = _journal("c", 1)
    fence = build_fresh_migration_fence(
        contract=_contract(),
        candidate=_candidate(),
        database=_application_database(),
        journal=journal,
        writer_fence_expires_at=_expiry(),
    )
    proof = parse_fresh_root_missing_receipt(
        _root_missing_receipt_raw(
            root_fence_sha256=fence.sha256,
            root_transaction_id=journal.transaction_id,
            root_operation_id=journal.operation_id,
            root_journal_sha256=journal.journal_sha256,
            root_generation=journal.journal_generation,
        ),
        contract=_contract(),
        candidate=_candidate(),
    )
    assert proof.operation_id == journal.operation_id
    assert proof.database_identity == _application_database()

    payload = json.loads(
        _root_missing_receipt_raw(
            root_fence_sha256=fence.sha256,
            root_transaction_id=journal.transaction_id,
            root_operation_id=journal.operation_id,
            root_journal_sha256=journal.journal_sha256,
            root_generation=journal.journal_generation,
        )
    )
    payload["expected_post_seed_sha256"] = _digest("f")
    with pytest.raises(MapApplication300ContractError, match="candidate prestate"):
        parse_fresh_root_missing_receipt(
            canonical_json_bytes(payload) + b"\n",
            contract=_contract(),
            candidate=_candidate(),
        )


def test_fresh_finalize_fence_binds_prior_root_lineage() -> None:
    _root_raw, root = _fresh_root()
    journal = _journal("d", 2)

    fence = build_fresh_finalize_fence(
        contract=_contract(),
        candidate=_candidate(),
        database=_application_database(),
        journal=journal,
        prior=root,
        writer_fence_expires_at=_expiry(),
    )
    payload = json.loads(fence.raw)

    assert set(payload) == {
        "schema",
        "transaction_id",
        "operation_id",
        "journal_sha256",
        "journal_generation",
        "operation",
        "prior_fresh_migration_result_sha256",
        "prior_fresh_migration_fence_sha256",
        "prior_fresh_migration_transaction_id",
        "prior_fresh_migration_operation_id",
        "prior_fresh_migration_journal_sha256",
        "prior_fresh_migration_generation",
        "map_candidate_commit",
        "map_candidate_image_id",
        "postgres_image_id",
        "destination_head",
        "reference_manifest_sha256",
        "source_catalog_sha256",
        "destination_catalog_sha256",
        "seed_sha256",
        "privileged_residue_sha256",
        "pre_privileged_residue_sha256",
        "destination_alembic_version_sha256",
        "runtime_invariants_sql_sha256",
        "database_name",
        "database_oid",
        "database_owner",
        "postgres_system_identifier",
        "writer_fence_expires_at",
    }
    assert payload["prior_fresh_migration_result_sha256"] == root.payload_sha256
    assert payload["prior_fresh_migration_operation_id"] == root.operation_id
    assert payload["prior_fresh_migration_generation"] == 1

    with pytest.raises(MapApplication300ContractError, match="advance"):
        build_fresh_finalize_fence(
            contract=_contract(),
            candidate=_candidate(),
            database=_application_database(),
            journal=_journal("d", 1),
            prior=root,
            writer_fence_expires_at=_expiry(),
        )


def test_finalize_result_and_final_permit_are_exact_and_resumable() -> None:
    _root_raw, root = _fresh_root()
    finalize_journal = _journal("d", 2)
    finalize_fence = build_fresh_finalize_fence(
        contract=_contract(),
        candidate=_candidate(),
        database=_application_database(),
        journal=finalize_journal,
        prior=root,
        writer_fence_expires_at=_expiry(),
    )
    finalize_raw = _finalize_result_raw(
        finalize_fence_sha256=finalize_fence.sha256,
        finalize_transaction_id=finalize_journal.transaction_id,
        finalize_operation_id=finalize_journal.operation_id,
        finalize_journal_sha256=finalize_journal.journal_sha256,
        finalize_generation=finalize_journal.journal_generation,
        prior_result_sha256=root.payload_sha256,
        prior_fence_sha256=root.writer_fence_receipt_sha256,
        prior_transaction_id=root.writer_fence_transaction_id,
        prior_operation_id=root.operation_id,
        prior_journal_sha256=root.journal_sha256,
        prior_generation=root.journal_generation,
    )
    finalize = parse_fresh_finalize_result(
        finalize_raw,
        contract=_contract(),
        candidate=_candidate(),
        prior=root,
    )

    permit = build_application_final_permit(
        contract=_contract(),
        candidate=_candidate(),
        database=_application_database(),
        finalize_result=finalize,
    )
    payload = validate_application_final_permit(
        permit.raw, contract=_contract(), candidate=_candidate()
    )

    assert permit.sha256 == sha256_bytes(permit.raw)
    assert set(payload) == {
        "schema",
        "transition_kind",
        "state",
        "transaction_id",
        "candidate",
        "database",
        "receipts",
        "operation_evidence",
    }
    assert payload["transition_kind"] == "map-fresh-300-finalize"
    assert payload["database"]["identity_sha256"] == application_database_identity_sha256(
        _application_database()
    )
    assert payload["operation_evidence"]["finalize_result_sha256"] == sha256_bytes(
        finalize_raw
    )
    assert b"postgresql://" not in permit.raw
    assert b"password" not in permit.raw.lower()


def test_finalize_missing_receipt_proof_is_exactly_bound_to_root_prestate() -> None:
    _root_raw, root = _fresh_root()
    operation_id = str(uuid4())

    proof = parse_fresh_finalize_missing_receipt(
        _finalize_missing_receipt_raw(operation_id=operation_id, prior=root),
        contract=_contract(),
        candidate=_candidate(),
        prior=root,
    )

    assert proof.operation_id == operation_id
    assert proof.prior_fresh_migration_operation_id == root.operation_id
    assert proof.prior_fresh_migration_result_sha256 == root.payload_sha256

    payload = json.loads(
        _finalize_missing_receipt_raw(operation_id=operation_id, prior=root)
    )
    payload["pre_source_catalog_sha256"] = _digest("f")
    with pytest.raises(MapApplication300ContractError, match="candidate prestate"):
        parse_fresh_finalize_missing_receipt(
            canonical_json_bytes(payload) + b"\n",
            contract=_contract(),
            candidate=_candidate(),
            prior=root,
        )


def test_final_permit_rejects_receipt_drift() -> None:
    _root_raw, root = _fresh_root()
    finalize_journal = _journal("d", 2)
    finalize_fence = build_fresh_finalize_fence(
        contract=_contract(),
        candidate=_candidate(),
        database=_application_database(),
        journal=finalize_journal,
        prior=root,
        writer_fence_expires_at=_expiry(),
    )
    finalize = parse_fresh_finalize_result(
        _finalize_result_raw(
            finalize_fence_sha256=finalize_fence.sha256,
            finalize_transaction_id=finalize_journal.transaction_id,
            finalize_operation_id=finalize_journal.operation_id,
            finalize_journal_sha256=finalize_journal.journal_sha256,
            finalize_generation=finalize_journal.journal_generation,
            prior_result_sha256=root.payload_sha256,
            prior_fence_sha256=root.writer_fence_receipt_sha256,
            prior_transaction_id=root.writer_fence_transaction_id,
            prior_operation_id=root.operation_id,
            prior_journal_sha256=root.journal_sha256,
            prior_generation=root.journal_generation,
        ),
        contract=_contract(),
        candidate=_candidate(),
        prior=root,
    )
    permit = build_application_final_permit(
        contract=_contract(),
        candidate=_candidate(),
        database=_application_database(),
        finalize_result=finalize,
    )
    payload = json.loads(permit.raw)
    payload["receipts"]["observed_catalog_sha256"] = _digest("f")

    with pytest.raises(MapApplication300ContractError, match="receipts"):
        validate_application_final_permit(
            canonical_json_bytes(payload) + b"\n",
            contract=_contract(),
            candidate=_candidate(),
        )


def test_dagster_metadata_permit_binds_candidate_and_isolates_databases() -> None:
    storage_candidate = DagsterStorageCandidate(
        dagster_image_id=_candidate().dagster_image_id,
        paired_candidate_build_receipt_sha256=_digest("d"),
        dagster_config_sha256=_digest("e"),
    )
    dagster_database = DagsterDatabaseIdentity(
        system_identifier=_application_database().system_identifier,
        name="kor_travel_map_dagster",
        oid=127002,
        owner="ktm_dagster_metadata",
        login_role="ktm_dagster_metadata",
        login_role_attributes=DagsterLoginRoleAttributes(),
    )
    operation_id = str(uuid4())

    permit = build_dagster_metadata_permit(
        candidate=storage_candidate,
        dagster_database=dagster_database,
        application_database=_application_database(),
        operation_id=operation_id,
    )
    payload = validate_dagster_metadata_permit(
        permit.raw,
        expected_candidate=storage_candidate,
        application_database=_application_database(),
        expected_operation_id=operation_id,
    )

    assert set(payload) == {
        "schema",
        "authority",
        "operation_id",
        "candidate",
        "dagster_database",
        "application_database",
    }
    assert payload["authority"] == "docker-manager"
    assert payload["operation_id"] == operation_id
    assert payload["candidate"]["paired_candidate_build_receipt_sha256"] == _digest("d")
    assert payload["dagster_database"]["login_role_attributes"]["can_login"] is True
    assert payload["dagster_database"]["login_role_attributes"]["inherit"] is False
    assert b"application-final-permit" not in permit.raw


def test_dagster_metadata_permit_rejects_application_database_target() -> None:
    with pytest.raises(MapApplication300ContractError, match="must not target"):
        build_dagster_metadata_permit(
            candidate=DagsterStorageCandidate(
                dagster_image_id=_candidate().dagster_image_id,
                paired_candidate_build_receipt_sha256=_digest("d"),
                dagster_config_sha256=_digest("e"),
            ),
            dagster_database=DagsterDatabaseIdentity(
                system_identifier=_application_database().system_identifier,
                name=_application_database().name,
                oid=_application_database().oid,
                owner="ktm_dagster_metadata",
                login_role="ktm_dagster_metadata",
                login_role_attributes=DagsterLoginRoleAttributes(),
            ),
            application_database=_application_database(),
            operation_id=str(uuid4()),
        )


def test_dagster_metadata_role_must_have_no_privilege_or_membership() -> None:
    with pytest.raises(MapApplication300ContractError, match="unsafe privileges"):
        DagsterLoginRoleAttributes(superuser=True)
    with pytest.raises(MapApplication300ContractError, match="role memberships"):
        DagsterLoginRoleAttributes(granted_role_count=1)
    with pytest.raises(MapApplication300ContractError, match="login attributes"):
        DagsterLoginRoleAttributes(can_login=False)
    with pytest.raises(MapApplication300ContractError, match="login attributes"):
        DagsterLoginRoleAttributes(inherit=True)
    with pytest.raises(MapApplication300ContractError, match="connection limits"):
        DagsterLoginRoleAttributes(connection_limit=0)
    with pytest.raises(MapApplication300ContractError, match="connection limits"):
        DagsterLoginRoleAttributes(valid_until_is_null=False)
    with pytest.raises(MapApplication300ContractError, match="persistent settings"):
        DagsterLoginRoleAttributes(role_config_count=1)
    with pytest.raises(MapApplication300ContractError, match="persistent settings"):
        DagsterLoginRoleAttributes(database_role_setting_count=1)


@pytest.mark.parametrize(("field", "value"), (("can_login", False), ("inherit", True)))
def test_dagster_metadata_permit_rejects_login_attribute_drift(
    field: str,
    value: bool,
) -> None:
    storage_candidate = DagsterStorageCandidate(
        dagster_image_id=_candidate().dagster_image_id,
        paired_candidate_build_receipt_sha256=_digest("d"),
        dagster_config_sha256=_digest("e"),
    )
    dagster_database = DagsterDatabaseIdentity(
        system_identifier=_application_database().system_identifier,
        name="kor_travel_map_dagster",
        oid=127002,
        owner="ktm_dagster_metadata",
        login_role="ktm_dagster_metadata",
        login_role_attributes=DagsterLoginRoleAttributes(),
    )
    operation_id = str(uuid4())
    permit = build_dagster_metadata_permit(
        candidate=storage_candidate,
        dagster_database=dagster_database,
        application_database=_application_database(),
        operation_id=operation_id,
    )
    payload = json.loads(permit.raw)
    payload["dagster_database"]["login_role_attributes"][field] = value

    with pytest.raises(MapApplication300ContractError, match="login attributes"):
        validate_dagster_metadata_permit(
            canonical_json_bytes(payload),
            expected_candidate=storage_candidate,
            application_database=_application_database(),
            expected_operation_id=operation_id,
        )


def test_owner_only_artifact_writer_is_idempotent_and_rejects_symlink(
    tmp_path: Path,
) -> None:
    del tmp_path
    root = Path("/tmp") / f"ktdm-map-application-300-{uuid4()}"
    root.mkdir(mode=0o700)
    try:
        target = root / "permit.json"
        raw = b'{"schema":"example"}'

        receipt = write_owner_only_artifact(target, raw)
        same = write_owner_only_artifact(target, raw)

        assert receipt == same
        assert target.read_bytes() == raw
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert read_owner_only_artifact(
            target,
            expected_sha256=sha256_bytes(raw),
        ) == raw

        with pytest.raises(MapApplication300ContractError, match="digest"):
            read_owner_only_artifact(target, expected_sha256=_digest("f"))

        with pytest.raises(MapApplication300ContractError, match="different bytes"):
            write_owner_only_artifact(target, b'{"schema":"other"}')

        link = root / "link.json"
        link.symlink_to(target)
        with pytest.raises(MapApplication300ContractError, match="unsafe"):
            write_owner_only_artifact(link, raw)
    finally:
        shutil.rmtree(root)


def test_owner_only_artifact_writer_rejects_noncanonical_or_shared_parent(
    tmp_path: Path,
) -> None:
    del tmp_path

    with pytest.raises(MapApplication300ContractError, match="absolute"):
        write_owner_only_artifact(Path("relative.json"), b"{}")


def test_fixed_artifact_publisher_requires_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("os.geteuid", lambda: 1000)

    with pytest.raises(MapApplication300ContractError, match="requires root"):
        publish_root_read_only_artifact(tmp_path / "permit.json", b"{}")


def test_fixed_artifact_replacement_requires_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """읽기 경로에서 root 조건을 뺀 근거는 쓰기 두 진입점이 각각 막는다는 것이다.

    publish 쪽만 테스트되어 있었다. replace가 조용히 열리면 그 근거가 무너진다.
    """

    monkeypatch.setattr("os.geteuid", lambda: 1000)

    with pytest.raises(MapApplication300ContractError, match="requires root"):
        replace_root_read_only_artifact(
            tmp_path / "permit.json", expected_old_sha256=_digest("a"), raw=b"{}"
        )


def _root_owned_stat(metadata: os.stat_result) -> os.stat_result:
    mode = stat.S_IFMT(metadata.st_mode) | (
        0o755 if stat.S_ISDIR(metadata.st_mode) else 0o444
    )
    return os.stat_result(
        (
            mode,
            metadata.st_ino,
            metadata.st_dev,
            metadata.st_nlink,
            0,
            metadata.st_gid,
            metadata.st_size,
            metadata.st_atime,
            metadata.st_mtime,
            metadata.st_ctime,
        )
    )


@pytest.mark.parametrize("euid", [0, 1000])
def test_fixed_artifact_reader_accepts_root_owned_mode_0444(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, euid: int
) -> None:
    """reconciliation은 owner-only 0600 결과가 아닌 root-owned 0444 fence를 읽는다.

    ``euid``를 함께 돌리는 이유: 읽기 경로는 호출자의 euid를 보지 않는다는 것이 계약인데,
    이 테스트가 `0`으로만 돌던 동안에는 읽기 경로에 root 게이트를 되돌려 넣어도 전체
    스위트가 green이었다. 바이트는 world-readable(`0444`)이 계약이므로 비-root 읽기는
    아무것도 넓히지 않는다 — 쓰기는 진입점 두 곳이 각자 root를 요구한다.
    """

    target_directory = tmp_path / "fixed"
    target_directory.mkdir(mode=0o755)
    target_directory.chmod(0o755)
    target = target_directory / "fence.json"
    raw = b'{"schema":"fixed"}\n'
    target.write_bytes(raw)
    target.chmod(0o444)

    module = __import__(
        "kor_travel_docker_manager.services.map_application_300",
        fromlist=["map_application_300"],
    )
    original_lstat = Path.lstat
    original_fstat = module.os.fstat

    monkeypatch.setattr(module.os, "geteuid", lambda: euid)
    monkeypatch.setattr(
        Path, "lstat", lambda path: _root_owned_stat(original_lstat(path))
    )
    monkeypatch.setattr(
        module.os,
        "fstat",
        lambda descriptor: _root_owned_stat(original_fstat(descriptor)),
    )

    assert read_root_read_only_artifact(target) == raw
