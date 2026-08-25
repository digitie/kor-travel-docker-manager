"""Map application ``300`` paired candidate receipt의 fail-close reader.

이 모듈은 Docker를 직접 호출하지 않는다. 이미지의 로컬 실재와 OCI revision 관측은
호출자가 제공하는 read-only callback으로 격리하고, 여기서는 owner-only receipt bytes와
그 callback 결과를 하나의 typed candidate로 결박한다. 오류에는 입력 경로, JSON 값,
환경변수 값 또는 하위 예외 문자열을 반사하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from kor_travel_docker_manager.services.map_application_300 import (
    Application300Contract,
)

_PAIRED_SCHEMA: Final = "kor-travel-map.application-300-paired-candidate-build.v1"
_API_RECEIPT_SCHEMA: Final = "kor-travel-map.application-300-candidate-build.v2"
_APPLICATION_CONTRACT_SCHEMA: Final = "kor-travel-map.application-baseline-contract.v1"
_LAUNCH_SCHEMA: Final = "kor-travel-map.application-300-dagster-launch.v1"
_METADATA_PERMIT_SCHEMA: Final = "kor-travel-map.dagster-storage-database-permit.v1"
_APPLICATION_HEAD: Final = "300"
_MAX_RECEIPT_BYTES: Final = 64 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")

_PAIRED_KEYS: Final = frozenset(
    {
        "schema",
        "candidate_commit",
        "candidate_git_tree",
        "paired_builder_script_sha256",
        "api_candidate",
        "api_candidate_build_receipt_sha256",
        "dagster_candidate",
        "launch_contract",
    }
)
_API_CANDIDATE_KEYS: Final = frozenset(
    {
        "candidate_image",
        "candidate_image_id",
        "candidate_commit",
        "candidate_git_tree",
        "candidate_dockerfile_sha256",
        "candidate_manifest_sha256",
        "candidate_app_manifest_sha256",
        "candidate_runtime_manifest_sha256",
        "candidate_entrypoint_manifest_sha256",
        "candidate_dependency_sbom_sha256",
        "candidate_300_migration_sha256",
        "candidate_base_image_reference",
        "candidate_base_image_id",
        "candidate_base_rootfs_layers_sha256",
        "candidate_full_rootfs_layers_sha256",
        "candidate_proof_tools_manifest_sha256",
        "candidate_build_receipt_sha256",
    }
)
_API_RECEIPT_KEYS: Final = frozenset(
    (_API_CANDIDATE_KEYS - {"candidate_build_receipt_sha256"})
    | {"schema", "builder_script_sha256"}
)
_DAGSTER_CANDIDATE_KEYS: Final = frozenset(
    {
        "candidate_image",
        "candidate_image_id",
        "candidate_commit",
        "candidate_git_tree",
        "candidate_dockerfile_sha256",
        "candidate_base_image_reference",
        "candidate_base_image_id",
        "candidate_base_rootfs_layers_sha256",
        "candidate_full_rootfs_layers_sha256",
        "candidate_app_manifest_sha256",
        "candidate_runtime_manifest_sha256",
        "candidate_proof_manifest_sha256",
        "candidate_dependency_sbom_sha256",
        "candidate_config_sha256",
        "candidate_dagster_yaml_sha256",
        "application_contract",
        "application_contract_sha256",
    }
)
_APPLICATION_CONTRACT_KEYS: Final = frozenset(
    {
        "schema",
        "application_head",
        "reference_manifest_sha256",
        "postgres_image_id",
        "source_catalog_sha256",
        "destination_catalog_sha256",
        "seed_sha256",
        "privileged_residue_sha256",
        "source_alembic_version_sha256",
        "destination_alembic_version_sha256",
        "runtime_invariants_sql_sha256",
    }
)
_LAUNCH_KEYS: Final = frozenset(
    {
        "schema",
        "requires_same_image_id",
        "application_final_permit_consumers",
        "webserver_image_id",
        "daemon_image_id",
        "storage_migration_image_id",
        "webserver_argv_policy",
        "image_default_webserver_argv",
        "daemon_argv",
        "storage_migration",
        "metadata_database_identity_permit",
    }
)

_WEBSERVER_PREFIX: Final = (
    "/usr/local/bin/dagster-webserver",
    "-m",
    "kortravelmap.dagster.definitions",
    "-h",
    "0.0.0.0",
    "-p",
)
_DEFAULT_WEBSERVER_ARGV: Final = (*_WEBSERVER_PREFIX, "12702")
_DAEMON_ARGV: Final = (
    "/usr/local/bin/dagster-daemon",
    "run",
    "-m",
    "kortravelmap.dagster.definitions",
)
_STORAGE_ARGV: Final = ("/usr/local/bin/ktm-dagster-storage", "migrate")
_STORAGE_FORBIDDEN_ENVIRONMENT: Final = (
    "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN",
    "KOR_TRAVEL_MAP_PG_DSN",
    "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DAGSTER_IMAGE_ID",
    "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_API_IMAGE_ID",
)
_METADATA_PERMIT_PATH: Final = "/run/kor-travel-map-dagster-storage-permit/permit.json"
_METADATA_CANDIDATE_BINDINGS: Final = (
    "dagster_image_id",
    "paired_candidate_build_receipt_sha256",
    "dagster_config_sha256",
)
_DATABASE_IDENTITY_FIELDS: Final = (
    "system_identifier",
    "name",
    "oid",
    "owner",
    "login_role",
    "login_role_attributes",
)
_FORBIDDEN_APPLICATION_IDENTITY_FIELDS: Final = (
    "system_identifier",
    "name",
    "oid",
    "owner",
)


class MapApplication300CandidateError(RuntimeError):
    """입력값을 반사하지 않는 paired candidate 계약 오류."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ImmutableImageObservation:
    """호출자 orchestration 경계가 read-only inspect로 관측한 immutable image."""

    available: bool
    image_id: str
    oci_revision: str | None


ImmutableImageAttestor = Callable[[str, str], ImmutableImageObservation]


@dataclass(frozen=True)
class MapApplication300Candidate:
    """검증 후 orchestration에 전달할 수 있는 비민감 paired candidate identity."""

    receipt_sha256: str
    api_receipt_sha256: str
    candidate_commit: str
    candidate_git_tree: str
    api_image_id: str
    dagster_image_id: str
    postgres_image_id: str
    dagster_config_sha256: str
    dagster_yaml_sha256: str
    application_contract: Application300Contract
    application_contract_sha256: str
    launch_contract_sha256: str
    webserver_argv_prefix: tuple[str, ...]
    webserver_port_minimum: int
    webserver_port_maximum: int
    daemon_argv: tuple[str, ...]
    storage_migration_argv: tuple[str, ...]


def load_map_application_300_candidate(
    paired_receipt_path: Path,
    api_receipt_path: Path,
    *,
    expected_candidate_commit: str,
    expected_candidate_tree: str,
    attest_image: ImmutableImageAttestor,
) -> MapApplication300Candidate:
    """두 private receipt와 세 immutable image 관측을 strict exact 계약으로 검증한다."""

    if _COMMIT.fullmatch(expected_candidate_commit) is None or _COMMIT.fullmatch(
        expected_candidate_tree
    ) is None:
        raise MapApplication300CandidateError("invalid_expected_source")

    paired_raw = _read_private_receipt(paired_receipt_path)
    api_raw = _read_private_receipt(api_receipt_path)
    paired_sha256 = hashlib.sha256(paired_raw).hexdigest()
    api_sha256 = hashlib.sha256(api_raw).hexdigest()
    paired = _parse_canonical_object(paired_raw)
    api_receipt = _parse_canonical_object(api_raw)

    _require_keys(paired, _PAIRED_KEYS)
    _require_keys(api_receipt, _API_RECEIPT_KEYS)
    _require_exact_string(paired, "schema", _PAIRED_SCHEMA)
    _require_exact_string(api_receipt, "schema", _API_RECEIPT_SCHEMA)
    _require_exact_string(paired, "candidate_commit", expected_candidate_commit)
    _require_exact_string(paired, "candidate_git_tree", expected_candidate_tree)
    _require_sha256(paired, "paired_builder_script_sha256")
    _require_exact_string(paired, "api_candidate_build_receipt_sha256", api_sha256)
    _require_sha256(api_receipt, "builder_script_sha256")

    api_candidate = _require_object(paired, "api_candidate", _API_CANDIDATE_KEYS)
    dagster_candidate = _require_object(
        paired, "dagster_candidate", _DAGSTER_CANDIDATE_KEYS
    )
    contract = _require_object(
        dagster_candidate, "application_contract", _APPLICATION_CONTRACT_KEYS
    )
    launch = _require_object(paired, "launch_contract", _LAUNCH_KEYS)

    _validate_api_candidate(
        api_candidate,
        api_receipt=api_receipt,
        api_receipt_sha256=api_sha256,
        expected_commit=expected_candidate_commit,
        expected_tree=expected_candidate_tree,
    )
    _validate_dagster_candidate(
        dagster_candidate,
        expected_commit=expected_candidate_commit,
        expected_tree=expected_candidate_tree,
    )
    application_contract, application_contract_sha256 = _validate_application_contract(
        contract,
        recorded_digest=_require_sha256(dagster_candidate, "application_contract_sha256"),
        api_manifest_sha256=_require_sha256(api_candidate, "candidate_manifest_sha256"),
    )

    api_image_id = _require_image_id(api_candidate, "candidate_image_id")
    dagster_image_id = _require_image_id(dagster_candidate, "candidate_image_id")
    _validate_launch_contract(launch, dagster_image_id=dagster_image_id)
    _validate_base_images(api_candidate, dagster_candidate)
    _attest_images(
        attest_image,
        expected_commit=expected_candidate_commit,
        api_image_id=api_image_id,
        dagster_image_id=dagster_image_id,
        postgres_image_id=application_contract.postgres_image_id,
    )

    launch_sha256 = _canonical_digest(launch)
    return MapApplication300Candidate(
        receipt_sha256=paired_sha256,
        api_receipt_sha256=api_sha256,
        candidate_commit=expected_candidate_commit,
        candidate_git_tree=expected_candidate_tree,
        api_image_id=api_image_id,
        dagster_image_id=dagster_image_id,
        postgres_image_id=application_contract.postgres_image_id,
        dagster_config_sha256=_require_sha256(dagster_candidate, "candidate_config_sha256"),
        dagster_yaml_sha256=_require_sha256(
            dagster_candidate, "candidate_dagster_yaml_sha256"
        ),
        application_contract=application_contract,
        application_contract_sha256=application_contract_sha256,
        launch_contract_sha256=launch_sha256,
        webserver_argv_prefix=_WEBSERVER_PREFIX,
        webserver_port_minimum=1,
        webserver_port_maximum=65535,
        daemon_argv=_DAEMON_ARGV,
        storage_migration_argv=_STORAGE_ARGV,
    )


def _read_private_receipt(path: Path) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute() or path.name in {"", ".", ".."}:
        raise MapApplication300CandidateError("receipt_path_invalid")
    parent = path.parent
    try:
        if parent.absolute() != parent.resolve(strict=True):
            raise MapApplication300CandidateError("receipt_parent_invalid")
        parent_before = parent.lstat()
    except MapApplication300CandidateError:
        raise
    except OSError:
        raise MapApplication300CandidateError("receipt_parent_invalid") from None
    expected_uid = os.geteuid()
    _validate_parent_metadata(parent_before, expected_uid=expected_uid)

    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise MapApplication300CandidateError("secure_open_unavailable")
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor = os.open(
            parent,
            os.O_RDONLY | os.O_CLOEXEC | nofollow | directory,
        )
        parent_after = os.fstat(parent_descriptor)
        if (parent_after.st_dev, parent_after.st_ino) != (
            parent_before.st_dev,
            parent_before.st_ino,
        ):
            raise MapApplication300CandidateError("receipt_parent_invalid")
        _validate_parent_metadata(parent_after, expected_uid=expected_uid)
        before = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_CLOEXEC | nofollow,
            dir_fd=parent_descriptor,
        )
        observed = os.fstat(descriptor)
        if (observed.st_dev, observed.st_ino) != (before.st_dev, before.st_ino):
            raise MapApplication300CandidateError("receipt_file_invalid")
        _validate_receipt_metadata(observed, expected_uid=expected_uid)
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, min(64 * 1024, _MAX_RECEIPT_BYTES + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_RECEIPT_BYTES:
                break
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        _validate_receipt_metadata(after, expected_uid=expected_uid)
        if len(raw) > _MAX_RECEIPT_BYTES or len(raw) != observed.st_size:
            raise MapApplication300CandidateError("receipt_file_invalid")
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != (
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
            observed.st_mtime_ns,
            observed.st_ctime_ns,
        ):
            raise MapApplication300CandidateError("receipt_file_invalid")
        return raw
    except MapApplication300CandidateError:
        raise
    except OSError:
        raise MapApplication300CandidateError("receipt_file_invalid") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _validate_parent_metadata(metadata: os.stat_result, *, expected_uid: int) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or mode & 0o077
        or mode & 0o500 != 0o500
    ):
        raise MapApplication300CandidateError("receipt_parent_invalid")


def _validate_receipt_metadata(metadata: os.stat_result, *, expected_uid: int) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size < 2
        or metadata.st_size > _MAX_RECEIPT_BYTES
    ):
        raise MapApplication300CandidateError("receipt_file_invalid")


def _parse_canonical_object(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MapApplication300CandidateError("receipt_json_invalid") from None
    if type(value) is not dict:
        raise MapApplication300CandidateError("receipt_json_invalid")
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    if raw != canonical.encode("utf-8"):
        raise MapApplication300CandidateError("receipt_json_invalid")
    return value


def _require_keys(value: Mapping[str, object], expected: frozenset[str]) -> None:
    if set(value) != expected:
        raise MapApplication300CandidateError("receipt_contract_invalid")


def _require_object(
    value: Mapping[str, object],
    key: str,
    expected_keys: frozenset[str],
) -> dict[str, object]:
    observed = value.get(key)
    if type(observed) is not dict:
        raise MapApplication300CandidateError("receipt_contract_invalid")
    _require_keys(observed, expected_keys)
    return observed


def _require_string(value: Mapping[str, object], key: str) -> str:
    observed = value.get(key)
    if type(observed) is not str:
        raise MapApplication300CandidateError("receipt_contract_invalid")
    return observed


def _require_exact_string(value: Mapping[str, object], key: str, expected: str) -> str:
    observed = _require_string(value, key)
    if observed != expected:
        raise MapApplication300CandidateError("receipt_contract_invalid")
    return observed


def _require_sha256(value: Mapping[str, object], key: str) -> str:
    observed = _require_string(value, key)
    if _SHA256.fullmatch(observed) is None:
        raise MapApplication300CandidateError("receipt_contract_invalid")
    return observed


def _require_image_id(value: Mapping[str, object], key: str) -> str:
    observed = _require_string(value, key)
    if _IMAGE_ID.fullmatch(observed) is None:
        raise MapApplication300CandidateError("receipt_contract_invalid")
    return observed


def _require_string_tuple(value: Mapping[str, object], key: str) -> tuple[str, ...]:
    observed = value.get(key)
    if type(observed) is not list or any(type(item) is not str for item in observed):
        raise MapApplication300CandidateError("receipt_contract_invalid")
    return tuple(observed)


def _require_exact_tuple(
    value: Mapping[str, object], key: str, expected: tuple[str, ...]
) -> None:
    if _require_string_tuple(value, key) != expected:
        raise MapApplication300CandidateError("receipt_contract_invalid")


def _require_bool(value: Mapping[str, object], key: str, expected: bool) -> None:
    observed = value.get(key)
    if type(observed) is not bool or observed is not expected:
        raise MapApplication300CandidateError("receipt_contract_invalid")


def _require_int(value: Mapping[str, object], key: str, expected: int) -> None:
    observed = value.get(key)
    if type(observed) is not int or observed != expected:
        raise MapApplication300CandidateError("receipt_contract_invalid")


def _validate_api_candidate(
    candidate: Mapping[str, object],
    *,
    api_receipt: Mapping[str, object],
    api_receipt_sha256: str,
    expected_commit: str,
    expected_tree: str,
) -> None:
    _require_exact_string(candidate, "candidate_commit", expected_commit)
    _require_exact_string(candidate, "candidate_git_tree", expected_tree)
    _require_exact_string(candidate, "candidate_build_receipt_sha256", api_receipt_sha256)
    _validate_image_candidate_fields(candidate, include_manifest=True)
    for key in _API_CANDIDATE_KEYS - {"candidate_build_receipt_sha256"}:
        if key in {"candidate_image", "candidate_base_image_reference"}:
            if _require_string(candidate, key) != _require_string(api_receipt, key):
                raise MapApplication300CandidateError("receipt_contract_invalid")
        elif _require_sha256_or_image(candidate, key) != _require_sha256_or_image(
            api_receipt, key
        ):
            raise MapApplication300CandidateError("receipt_contract_invalid")


def _validate_dagster_candidate(
    candidate: Mapping[str, object], *, expected_commit: str, expected_tree: str
) -> None:
    _require_exact_string(candidate, "candidate_commit", expected_commit)
    _require_exact_string(candidate, "candidate_git_tree", expected_tree)
    _validate_image_candidate_fields(candidate, include_manifest=False)
    for key in (
        "candidate_app_manifest_sha256",
        "candidate_runtime_manifest_sha256",
        "candidate_proof_manifest_sha256",
        "candidate_dependency_sbom_sha256",
        "candidate_config_sha256",
        "candidate_dagster_yaml_sha256",
    ):
        _require_sha256(candidate, key)


def _validate_image_candidate_fields(
    candidate: Mapping[str, object], *, include_manifest: bool
) -> None:
    image_name = _require_string(candidate, "candidate_image")
    if not image_name or len(image_name) > 255 or any(character.isspace() for character in image_name):
        raise MapApplication300CandidateError("receipt_contract_invalid")
    _require_image_id(candidate, "candidate_image_id")
    reference = _require_string(candidate, "candidate_base_image_reference")
    if _IMAGE_REFERENCE.fullmatch(reference) is None:
        raise MapApplication300CandidateError("receipt_contract_invalid")
    _require_image_id(candidate, "candidate_base_image_id")
    for key in (
        "candidate_dockerfile_sha256",
        "candidate_base_rootfs_layers_sha256",
        "candidate_full_rootfs_layers_sha256",
    ):
        _require_sha256(candidate, key)
    if include_manifest:
        for key in (
            "candidate_manifest_sha256",
            "candidate_app_manifest_sha256",
            "candidate_runtime_manifest_sha256",
            "candidate_entrypoint_manifest_sha256",
            "candidate_dependency_sbom_sha256",
            "candidate_300_migration_sha256",
            "candidate_proof_tools_manifest_sha256",
        ):
            _require_sha256(candidate, key)


def _require_sha256_or_image(value: Mapping[str, object], key: str) -> str:
    if key in {"candidate_image_id", "candidate_base_image_id"}:
        return _require_image_id(value, key)
    if key in {"candidate_commit", "candidate_git_tree"}:
        observed = _require_string(value, key)
        if _COMMIT.fullmatch(observed) is None:
            raise MapApplication300CandidateError("receipt_contract_invalid")
        return observed
    return _require_sha256(value, key)


def _validate_application_contract(
    contract: Mapping[str, object],
    *,
    recorded_digest: str,
    api_manifest_sha256: str,
) -> tuple[Application300Contract, str]:
    _require_exact_string(contract, "schema", _APPLICATION_CONTRACT_SCHEMA)
    _require_exact_string(contract, "application_head", _APPLICATION_HEAD)
    reference_sha256 = _require_sha256(contract, "reference_manifest_sha256")
    if reference_sha256 != api_manifest_sha256:
        raise MapApplication300CandidateError("receipt_contract_invalid")
    for key in _APPLICATION_CONTRACT_KEYS - {
        "schema",
        "application_head",
        "postgres_image_id",
    }:
        _require_sha256(contract, key)
    observed_digest = _canonical_digest(contract)
    if observed_digest != recorded_digest:
        raise MapApplication300CandidateError("receipt_contract_invalid")
    return Application300Contract.from_payload(contract), observed_digest


def _validate_base_images(
    api_candidate: Mapping[str, object], dagster_candidate: Mapping[str, object]
) -> None:
    api_reference = _require_string(api_candidate, "candidate_base_image_reference")
    dagster_reference = _require_string(dagster_candidate, "candidate_base_image_reference")
    api_id = _require_image_id(api_candidate, "candidate_base_image_id")
    dagster_id = _require_image_id(dagster_candidate, "candidate_base_image_id")
    if api_reference != dagster_reference or api_id != dagster_id:
        raise MapApplication300CandidateError("receipt_contract_invalid")
    if api_reference.rsplit("@", 1)[1] != api_id:
        raise MapApplication300CandidateError("receipt_contract_invalid")


def _validate_launch_contract(
    launch: Mapping[str, object], *, dagster_image_id: str
) -> None:
    _require_exact_string(launch, "schema", _LAUNCH_SCHEMA)
    _require_bool(launch, "requires_same_image_id", True)
    _require_exact_tuple(launch, "application_final_permit_consumers", ("webserver", "daemon"))
    for key in ("webserver_image_id", "daemon_image_id", "storage_migration_image_id"):
        _require_exact_string(launch, key, dagster_image_id)
    policy = _require_object(
        launch,
        "webserver_argv_policy",
        frozenset({"fixed_prefix", "port_decimal_minimum", "port_decimal_maximum"}),
    )
    _require_exact_tuple(policy, "fixed_prefix", _WEBSERVER_PREFIX)
    _require_int(policy, "port_decimal_minimum", 1)
    _require_int(policy, "port_decimal_maximum", 65535)
    _require_exact_tuple(launch, "image_default_webserver_argv", _DEFAULT_WEBSERVER_ARGV)
    _require_exact_tuple(launch, "daemon_argv", _DAEMON_ARGV)

    storage = _require_object(
        launch,
        "storage_migration",
        frozenset(
            {
                "scope",
                "argv",
                "forbidden_application_environment",
                "forbids_application_final_permit_mount",
            }
        ),
    )
    _require_exact_string(
        storage, "scope", "dagster-metadata-only-excluded-from-application-final-permit"
    )
    _require_exact_tuple(storage, "argv", _STORAGE_ARGV)
    _require_exact_tuple(
        storage, "forbidden_application_environment", _STORAGE_FORBIDDEN_ENVIRONMENT
    )
    _require_bool(storage, "forbids_application_final_permit_mount", True)
    _validate_metadata_permit(launch)


def _validate_metadata_permit(launch: Mapping[str, object]) -> None:
    permit = _require_object(
        launch,
        "metadata_database_identity_permit",
        frozenset(
            {
                "schema",
                "path",
                "production_authority",
                "canonical_dagster_home",
                "canonical_storage_env",
                "candidate_binding_fields",
                "dagster_config_receipt_field",
                "database_identity_fields",
                "required_login_role_attributes",
                "requires_owner_login_and_effective_role_equality",
                "forbidden_application_identity_fields",
                "forbidden_application_raw_revision",
            }
        ),
    )
    _require_exact_string(permit, "schema", _METADATA_PERMIT_SCHEMA)
    _require_exact_string(permit, "path", _METADATA_PERMIT_PATH)
    _require_exact_string(permit, "production_authority", "docker-manager")
    _require_exact_string(permit, "canonical_dagster_home", "/opt/dagster/dagster_home")
    _require_exact_string(permit, "canonical_storage_env", "KOR_TRAVEL_MAP_DAGSTER_PG_URL")
    _require_exact_tuple(permit, "candidate_binding_fields", _METADATA_CANDIDATE_BINDINGS)
    _require_exact_string(
        permit, "dagster_config_receipt_field", "candidate_dagster_yaml_sha256"
    )
    _require_exact_tuple(permit, "database_identity_fields", _DATABASE_IDENTITY_FIELDS)
    attributes = _require_object(
        permit,
        "required_login_role_attributes",
        frozenset(
            {
                "superuser",
                "create_database",
                "create_role",
                "replication",
                "bypass_rls",
                "granted_role_count",
                "member_role_count",
            }
        ),
    )
    for key in ("superuser", "create_database", "create_role", "replication", "bypass_rls"):
        _require_bool(attributes, key, False)
    _require_int(attributes, "granted_role_count", 0)
    _require_int(attributes, "member_role_count", 0)
    _require_bool(permit, "requires_owner_login_and_effective_role_equality", True)
    _require_exact_tuple(
        permit,
        "forbidden_application_identity_fields",
        _FORBIDDEN_APPLICATION_IDENTITY_FIELDS,
    )
    _require_exact_string(permit, "forbidden_application_raw_revision", _APPLICATION_HEAD)


def _attest_images(
    attestor: ImmutableImageAttestor,
    *,
    expected_commit: str,
    api_image_id: str,
    dagster_image_id: str,
    postgres_image_id: str,
) -> None:
    for role, image_id, expected_revision in (
        ("map_api", api_image_id, expected_commit),
        ("map_dagster", dagster_image_id, expected_commit),
        ("map_postgres", postgres_image_id, None),
    ):
        try:
            observed = attestor(role, image_id)
        except Exception:
            raise MapApplication300CandidateError("image_attestation_failed") from None
        if (
            type(observed) is not ImmutableImageObservation
            or observed.available is not True
            or observed.image_id != image_id
            or _IMAGE_ID.fullmatch(observed.image_id) is None
            or observed.oci_revision != expected_revision
        ):
            raise MapApplication300CandidateError("image_attestation_failed")


def _canonical_digest(value: Mapping[str, object]) -> str:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
