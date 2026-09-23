from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from kor_travel_docker_manager.services.map_application_300 import (
    APPLICATION_DATABASE_OWNER,
    Application300Candidate,
    ApplicationDatabaseIdentity,
    DagsterDatabaseIdentity,
    DagsterLoginRoleAttributes,
    DagsterStorageCandidate,
    MapApplication300ContractError,
    build_dagster_metadata_permit,
    canonical_json_bytes,
    expected_application_300_source_commit,
    json_artifact,
    publish_root_read_only_artifact,
    sha256_bytes,
    validate_dagster_metadata_permit,
)


def _digest(seed: str) -> str:
    return seed * 64


def _image(seed: str) -> str:
    return f"sha256:{seed * 64}"


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


def test_dagster_metadata_permit_binds_candidate_and_isolates_databases() -> None:
    storage_candidate = DagsterStorageCandidate(
        dagster_image_id=_candidate().dagster_image_id,
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
    assert payload["candidate"]["dagster_config_sha256"] == _digest("e")
    assert payload["dagster_database"]["login_role_attributes"]["can_login"] is True
    assert payload["dagster_database"]["login_role_attributes"]["inherit"] is False
    assert b"application-final-permit" not in permit.raw


def test_dagster_metadata_permit_rejects_application_database_target() -> None:
    with pytest.raises(MapApplication300ContractError, match="must not target"):
        build_dagster_metadata_permit(
            candidate=DagsterStorageCandidate(
                dagster_image_id=_candidate().dagster_image_id,
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


def test_fixed_artifact_publisher_requires_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("os.geteuid", lambda: 1000)

    with pytest.raises(
        MapApplication300ContractError, match="fixed artifact publishing requires root"
    ):
        publish_root_read_only_artifact(tmp_path / "permit.json", b"{}")


# -- result/receipt의 확장 허용, fence의 exact 유지 (감사 I-9) ----------------

