from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.map_application_300_candidate import (
    ImmutableImageObservation,
    MapApplication300CandidateError,
    load_map_application_300_candidate,
)

_COMMIT = "1" * 40
_TREE = "2" * 40
_API_IMAGE_ID = f"sha256:{'a' * 64}"
_DAGSTER_IMAGE_ID = f"sha256:{'b' * 64}"
_POSTGRES_IMAGE_ID = f"sha256:{'c' * 64}"
_BASE_ID = f"sha256:{'3' * 64}"
_BASE_REFERENCE = f"python@{_BASE_ID}"
_SHA = "d" * 64


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """보안 mode test는 chmod를 보존하지 않는 NTFS pytest temp를 사용하지 않는다."""

    path = Path(tempfile.mkdtemp(prefix="ktdm-map300-test.", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path)


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _application_contract() -> dict[str, object]:
    return {
        "schema": "kor-travel-map.application-baseline-contract.v1",
        "application_head": "300",
        "reference_manifest_sha256": _SHA,
        "postgres_image_id": _POSTGRES_IMAGE_ID,
        "source_catalog_sha256": _SHA,
        "destination_catalog_sha256": _SHA,
        "seed_sha256": _SHA,
        "privileged_residue_sha256": _SHA,
        "source_alembic_version_sha256": _SHA,
        "destination_alembic_version_sha256": _SHA,
        "runtime_invariants_sql_sha256": _SHA,
    }


def _api_receipt() -> dict[str, object]:
    return {
        "schema": "kor-travel-map.application-300-candidate-build.v2",
        "builder_script_sha256": _SHA,
        "candidate_image": "map-api:test",
        "candidate_image_id": _API_IMAGE_ID,
        "candidate_commit": _COMMIT,
        "candidate_git_tree": _TREE,
        "candidate_dockerfile_sha256": _SHA,
        "candidate_manifest_sha256": _SHA,
        "candidate_app_manifest_sha256": _SHA,
        "candidate_runtime_manifest_sha256": _SHA,
        "candidate_entrypoint_manifest_sha256": _SHA,
        "candidate_dependency_sbom_sha256": _SHA,
        "candidate_300_migration_sha256": _SHA,
        "candidate_base_image_reference": _BASE_REFERENCE,
        "candidate_base_image_id": _BASE_ID,
        "candidate_base_rootfs_layers_sha256": _SHA,
        "candidate_full_rootfs_layers_sha256": _SHA,
        "candidate_proof_tools_manifest_sha256": _SHA,
    }


def _launch_contract() -> dict[str, object]:
    return {
        "schema": "kor-travel-map.application-300-dagster-launch.v1",
        "requires_same_image_id": True,
        "application_final_permit_consumers": ["webserver", "daemon"],
        "webserver_image_id": _DAGSTER_IMAGE_ID,
        "daemon_image_id": _DAGSTER_IMAGE_ID,
        "storage_migration_image_id": _DAGSTER_IMAGE_ID,
        "webserver_argv_policy": {
            "fixed_prefix": [
                "/usr/local/bin/dagster-webserver",
                "-m",
                "kortravelmap.dagster.definitions",
                "-h",
                "0.0.0.0",
                "-p",
            ],
            "port_decimal_minimum": 1,
            "port_decimal_maximum": 65535,
        },
        "image_default_webserver_argv": [
            "/usr/local/bin/dagster-webserver",
            "-m",
            "kortravelmap.dagster.definitions",
            "-h",
            "0.0.0.0",
            "-p",
            "12702",
        ],
        "daemon_argv": [
            "/usr/local/bin/dagster-daemon",
            "run",
            "-m",
            "kortravelmap.dagster.definitions",
        ],
        "storage_migration": {
            "scope": "dagster-metadata-only-excluded-from-application-final-permit",
            "argv": ["/usr/local/bin/ktm-dagster-storage", "migrate"],
            "forbidden_application_environment": [
                "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN",
                "KOR_TRAVEL_MAP_PG_DSN",
                "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DAGSTER_IMAGE_ID",
                "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_API_IMAGE_ID",
            ],
            "forbids_application_final_permit_mount": True,
        },
        "metadata_database_identity_permit": {
            "schema": "kor-travel-map.dagster-storage-database-permit.v2",
            "path": "/run/kor-travel-map-dagster-storage-permit/permit.json",
            "production_authority": "docker-manager",
            "operation_id_binding": {
                "field": "operation_id",
                "format": "canonical-lowercase-uuid",
                "authority": "docker-manager-durable-journal",
            },
            "canonical_dagster_home": "/opt/dagster/dagster_home",
            "canonical_storage_env": "KOR_TRAVEL_MAP_DAGSTER_PG_URL",
            "candidate_binding_fields": [
                "dagster_image_id",
                "paired_candidate_build_receipt_sha256",
                "dagster_config_sha256",
            ],
            "dagster_config_receipt_field": "candidate_dagster_yaml_sha256",
            "database_identity_fields": [
                "system_identifier",
                "name",
                "oid",
                "owner",
                "login_role",
                "login_role_attributes",
            ],
            "required_login_role_attributes": {
                "can_login": True,
                "inherit": False,
                "superuser": False,
                "create_database": False,
                "create_role": False,
                "replication": False,
                "bypass_rls": False,
                "granted_role_count": 0,
                "member_role_count": 0,
            },
            "requires_owner_login_and_effective_role_equality": True,
            "forbidden_application_identity_fields": [
                "system_identifier",
                "name",
                "oid",
                "owner",
            ],
            "forbidden_application_raw_revision": "300",
        },
    }


def _paired_receipt(api_raw: bytes) -> dict[str, object]:
    api = _api_receipt()
    api_candidate = {key: value for key, value in api.items() if key not in {"schema", "builder_script_sha256"}}
    api_sha256 = hashlib.sha256(api_raw).hexdigest()
    api_candidate["candidate_build_receipt_sha256"] = api_sha256
    contract = _application_contract()
    contract_sha256 = hashlib.sha256(_canonical(contract)).hexdigest()
    return {
        "schema": "kor-travel-map.application-300-paired-candidate-build.v1",
        "candidate_commit": _COMMIT,
        "candidate_git_tree": _TREE,
        "paired_builder_script_sha256": _SHA,
        "api_candidate": api_candidate,
        "api_candidate_build_receipt_sha256": api_sha256,
        "dagster_candidate": {
            "candidate_image": "map-dagster:test",
            "candidate_image_id": _DAGSTER_IMAGE_ID,
            "candidate_commit": _COMMIT,
            "candidate_git_tree": _TREE,
            "candidate_dockerfile_sha256": _SHA,
            "candidate_base_image_reference": _BASE_REFERENCE,
            "candidate_base_image_id": _BASE_ID,
            "candidate_base_rootfs_layers_sha256": _SHA,
            "candidate_full_rootfs_layers_sha256": _SHA,
            "candidate_app_manifest_sha256": _SHA,
            "candidate_runtime_manifest_sha256": _SHA,
            "candidate_proof_manifest_sha256": _SHA,
            "candidate_dependency_sbom_sha256": _SHA,
            "candidate_config_sha256": _SHA,
            "candidate_dagster_yaml_sha256": _SHA,
            "application_contract": contract,
            "application_contract_sha256": contract_sha256,
        },
        "launch_contract": _launch_contract(),
    }


class ReceiptFixture:
    def __init__(self, root: Path) -> None:
        self.root = root / "private"
        self.root.mkdir(mode=0o700)
        os.chmod(self.root, 0o700)
        self.api = self.root / "api.json"
        self.paired = self.root / "paired.json"
        self.api_raw = _canonical(_api_receipt())
        self.payload = _paired_receipt(self.api_raw)
        self.write()

    def write(self) -> None:
        self.api.write_bytes(self.api_raw)
        self.paired.write_bytes(_canonical(self.payload))
        os.chmod(self.api, 0o600)
        os.chmod(self.paired, 0o600)

    def attestor(self) -> Callable[[str, str], ImmutableImageObservation]:
        expected = {
            "map_api": (_API_IMAGE_ID, _COMMIT),
            "map_dagster": (_DAGSTER_IMAGE_ID, _COMMIT),
            "map_postgres": (_POSTGRES_IMAGE_ID, None),
        }

        def attest(role: str, image_id: str) -> ImmutableImageObservation:
            expected_id, revision = expected[role]
            assert image_id == expected_id
            return ImmutableImageObservation(
                available=True,
                image_id=expected_id,
                oci_revision=revision,
            )

        return attest

    def load(self) -> object:
        return load_map_application_300_candidate(
            self.paired,
            self.api,
            expected_candidate_commit=_COMMIT,
            expected_candidate_tree=_TREE,
            attest_image=self.attestor(),
        )


def test_loads_strict_paired_candidate_without_dsn_payload(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)

    candidate = fixture.load()

    assert candidate.api_image_id == _API_IMAGE_ID
    assert candidate.dagster_image_id == _DAGSTER_IMAGE_ID
    assert candidate.postgres_image_id == _POSTGRES_IMAGE_ID
    assert candidate.candidate_commit == _COMMIT
    assert candidate.candidate_git_tree == _TREE
    assert candidate.api_receipt_sha256 == hashlib.sha256(fixture.api_raw).hexdigest()
    assert candidate.webserver_port_minimum == 1
    assert candidate.webserver_port_maximum == 65535
    assert "DSN" not in repr(candidate)
    assert "postgresql://" not in repr(candidate)


def test_missing_receipt_is_refused_without_path_reflection(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    missing = fixture.root / "credential-bearing-name.json"

    with pytest.raises(MapApplication300CandidateError) as caught:
        load_map_application_300_candidate(
            missing,
            fixture.api,
            expected_candidate_commit=_COMMIT,
            expected_candidate_tree=_TREE,
            attest_image=fixture.attestor(),
        )

    assert caught.value.code == "receipt_file_invalid"
    assert "credential-bearing-name" not in str(caught.value)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_top_level_exact_field_set_is_required(tmp_path: Path, mutation: str) -> None:
    fixture = ReceiptFixture(tmp_path)
    if mutation == "missing":
        del fixture.payload["launch_contract"]
    else:
        fixture.payload["unexpected"] = "postgresql://must-not-reflect"
    fixture.write()

    with pytest.raises(MapApplication300CandidateError) as caught:
        fixture.load()

    assert caught.value.code == "receipt_contract_invalid"
    assert "must-not-reflect" not in str(caught.value)


def test_nested_source_mismatch_is_refused(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    api_candidate = fixture.payload["api_candidate"]
    assert isinstance(api_candidate, dict)
    api_candidate["candidate_commit"] = "9" * 40
    fixture.write()

    with pytest.raises(MapApplication300CandidateError, match="receipt_contract_invalid"):
        fixture.load()


def test_noncanonical_traversal_path_is_refused(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    nested = fixture.root / "nested"
    nested.mkdir()
    traversal = nested / ".." / fixture.paired.name

    with pytest.raises(MapApplication300CandidateError, match="receipt_parent_invalid"):
        load_map_application_300_candidate(
            traversal,
            fixture.api,
            expected_candidate_commit=_COMMIT,
            expected_candidate_tree=_TREE,
            attest_image=fixture.attestor(),
        )


def test_symlink_receipt_is_refused(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    link = fixture.root / "paired-link.json"
    link.symlink_to(fixture.paired.name)

    with pytest.raises(MapApplication300CandidateError, match="receipt_file_invalid"):
        load_map_application_300_candidate(
            link,
            fixture.api,
            expected_candidate_commit=_COMMIT,
            expected_candidate_tree=_TREE,
            attest_image=fixture.attestor(),
        )


def test_receipt_mode_must_be_exact_0600(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    os.chmod(fixture.paired, 0o640)

    with pytest.raises(MapApplication300CandidateError, match="receipt_file_invalid"):
        fixture.load()


def test_receipt_must_have_one_link(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    os.link(fixture.paired, fixture.root / "paired-hardlink.json")

    with pytest.raises(MapApplication300CandidateError, match="receipt_file_invalid"):
        fixture.load()


def test_receipt_size_is_capped(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    fixture.paired.write_bytes(b"{" + b"x" * (64 * 1024) + b"}")
    os.chmod(fixture.paired, 0o600)

    with pytest.raises(MapApplication300CandidateError, match="receipt_file_invalid"):
        fixture.load()


def test_parent_must_be_owner_only(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    os.chmod(fixture.root, 0o750)

    with pytest.raises(MapApplication300CandidateError, match="receipt_parent_invalid"):
        fixture.load()


@pytest.mark.parametrize("field", ["environment", "mount"])
def test_storage_migration_is_strictly_excluded_from_application_permit(
    tmp_path: Path, field: str
) -> None:
    fixture = ReceiptFixture(tmp_path)
    launch = fixture.payload["launch_contract"]
    assert isinstance(launch, dict)
    storage = launch["storage_migration"]
    assert isinstance(storage, dict)
    if field == "environment":
        storage["forbidden_application_environment"] = []
    else:
        storage["forbids_application_final_permit_mount"] = False
    fixture.write()

    with pytest.raises(MapApplication300CandidateError, match="receipt_contract_invalid"):
        fixture.load()


def test_static_application_contract_digest_must_match(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    dagster = fixture.payload["dagster_candidate"]
    assert isinstance(dagster, dict)
    dagster["application_contract_sha256"] = "e" * 64
    fixture.write()

    with pytest.raises(MapApplication300CandidateError, match="receipt_contract_invalid"):
        fixture.load()


def test_api_receipt_raw_digest_must_match_nested_receipt(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    api_payload = copy.deepcopy(_api_receipt())
    api_payload["candidate_image"] = "map-api:changed"
    fixture.api.write_bytes(_canonical(api_payload))
    os.chmod(fixture.api, 0o600)

    with pytest.raises(MapApplication300CandidateError, match="receipt_contract_invalid"):
        fixture.load()


def test_image_attestation_requires_available_exact_id_and_revision(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)

    def divergent(role: str, image_id: str) -> ImmutableImageObservation:
        if role == "map_dagster":
            return ImmutableImageObservation(True, image_id, "9" * 40)
        revision = _COMMIT if role == "map_api" else None
        return ImmutableImageObservation(True, image_id, revision)

    with pytest.raises(MapApplication300CandidateError, match="image_attestation_failed"):
        load_map_application_300_candidate(
            fixture.paired,
            fixture.api,
            expected_candidate_commit=_COMMIT,
            expected_candidate_tree=_TREE,
            attest_image=divergent,
        )


def test_noncanonical_json_never_reflects_embedded_value(tmp_path: Path) -> None:
    fixture = ReceiptFixture(tmp_path)
    fixture.paired.write_text(
        '{"dsn":"postgresql://sensitive-value"}', encoding="utf-8"
    )
    os.chmod(fixture.paired, 0o600)

    with pytest.raises(MapApplication300CandidateError) as caught:
        fixture.load()

    assert caught.value.code == "receipt_json_invalid"
    assert "sensitive-value" not in str(caught.value)
