from __future__ import annotations

import json
import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from kor_travel_docker_manager.services.map_application_300 import (
    APPLICATION_DATABASE_OWNER,
    MAP_APPLICATION_300_SOURCE_COMMIT,
    Application300Candidate,
    Application300Contract,
    ApplicationDatabaseIdentity,
    DagsterDatabaseIdentity,
    DagsterLoginRoleAttributes,
    DagsterStorageCandidate,
    JournalStamp,
    MapApplication300ContractError,
    application_database_identity_sha256,
    build_application_final_permit,
    build_dagster_metadata_permit,
    build_fresh_finalize_fence,
    build_fresh_migration_fence,
    canonical_json_bytes,
    json_artifact,
    parse_fresh_finalize_result,
    parse_fresh_root_result,
    publish_root_read_only_artifact,
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
        map_source_commit=MAP_APPLICATION_300_SOURCE_COMMIT,
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
        journal_sha256=_digest(seed),
        journal_generation=generation,
    )


def _expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def _root_result_raw(
    *,
    root_fence_sha256: str,
    root_transaction_id: str,
    root_journal_sha256: str,
    root_generation: int,
) -> bytes:
    contract = _contract()
    candidate = _candidate()
    database = _application_database()
    return canonical_json_bytes(
        {
            "schema": "kor-travel-map.application-fresh-300-root.v1",
            "outcome": "root-committed",
            "authorization": "manager-fence",
            "destination_head": "300",
            "map_candidate_commit": candidate.map_source_commit,
            "map_candidate_image_id": candidate.api_image_id,
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
        root_journal_sha256=root_journal.journal_sha256,
        root_generation=root_journal.journal_generation,
    )
    return raw, parse_fresh_root_result(
        raw, contract=_contract(), candidate=_candidate()
    )


def _finalize_result_raw(
    *,
    finalize_fence_sha256: str,
    finalize_transaction_id: str,
    finalize_journal_sha256: str,
    finalize_generation: int,
    prior_result_sha256: str,
    prior_fence_sha256: str,
    prior_transaction_id: str,
    prior_journal_sha256: str,
    prior_generation: int,
) -> bytes:
    contract = _contract()
    candidate = _candidate()
    return canonical_json_bytes(
        {
            "schema": "kor-travel-map.application-fresh-300-finalize.v3",
            "outcome": "finalized",
            "destination_head": "300",
            "map_candidate_commit": candidate.map_source_commit,
            "map_candidate_image_id": candidate.api_image_id,
            "reference_manifest_sha256": contract.reference_manifest_sha256,
            "writer_fence_receipt_sha256": finalize_fence_sha256,
            "writer_fence_transaction_id": finalize_transaction_id,
            "journal_sha256": finalize_journal_sha256,
            "journal_generation": finalize_generation,
            "prior_fresh_migration_result_sha256": prior_result_sha256,
            "prior_fresh_migration_fence_sha256": prior_fence_sha256,
            "prior_fresh_migration_transaction_id": prior_transaction_id,
            "prior_fresh_migration_journal_sha256": prior_journal_sha256,
            "prior_fresh_migration_generation": prior_generation,
            "pre_source_catalog_sha256": contract.source_catalog_sha256,
            "post_destination_catalog_sha256": contract.destination_catalog_sha256,
            "post_destination_alembic_version_sha256": (
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
        "journal_sha256",
        "journal_generation",
        "operation",
        "prior_fresh_migration_result_sha256",
        "prior_fresh_migration_fence_sha256",
        "prior_fresh_migration_transaction_id",
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
        finalize_journal_sha256=finalize_journal.journal_sha256,
        finalize_generation=finalize_journal.journal_generation,
        prior_result_sha256=root.payload_sha256,
        prior_fence_sha256=root.writer_fence_receipt_sha256,
        prior_transaction_id=root.writer_fence_transaction_id,
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
            finalize_journal_sha256=finalize_journal.journal_sha256,
            finalize_generation=finalize_journal.journal_generation,
            prior_result_sha256=root.payload_sha256,
            prior_fence_sha256=root.writer_fence_receipt_sha256,
            prior_transaction_id=root.writer_fence_transaction_id,
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

    permit = build_dagster_metadata_permit(
        candidate=storage_candidate,
        dagster_database=dagster_database,
        application_database=_application_database(),
    )
    payload = validate_dagster_metadata_permit(
        permit.raw,
        expected_candidate=storage_candidate,
        application_database=_application_database(),
    )

    assert set(payload) == {
        "schema",
        "authority",
        "candidate",
        "dagster_database",
        "application_database",
    }
    assert payload["authority"] == "docker-manager"
    assert payload["candidate"]["paired_candidate_build_receipt_sha256"] == _digest("d")
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
        )


def test_dagster_metadata_role_must_have_no_privilege_or_membership() -> None:
    with pytest.raises(MapApplication300ContractError, match="unsafe privileges"):
        DagsterLoginRoleAttributes(superuser=True)
    with pytest.raises(MapApplication300ContractError, match="role memberships"):
        DagsterLoginRoleAttributes(granted_role_count=1)


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
