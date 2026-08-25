"""Pure contract artifacts for Map application fresh ``300``.

The orchestration layer owns Docker, volumes, credentials, and command
execution.  This module owns only the secret-free JSON contracts that make
those effects resumable: strict parsing, canonical bytes, SHA-256 binding, and
owner-only host artifact writes.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from uuid import UUID

MAP_APPLICATION_300_SOURCE_COMMIT: Final = (
    "3911677b76ff8d77a5186c2deabd0c5be7c9b8f7"
)
APPLICATION_HEAD: Final = "300"
APPLICATION_DATABASE_OWNER: Final = "ktm_feature_schema_owner"

BASELINE_CONTRACT_SCHEMA: Final = "kor-travel-map.application-baseline-contract.v1"
FRESH_MIGRATION_FENCE_SCHEMA: Final = (
    "kor-travel-docker-manager.map-fresh-300-migrate-fence.v2"
)
FRESH_MIGRATION_OPERATION: Final = "map-fresh-300"
FRESH_ROOT_RESULT_SCHEMA: Final = "kor-travel-map.application-fresh-300-root.v1"
FRESH_FINALIZE_FENCE_SCHEMA: Final = (
    "kor-travel-docker-manager.map-fresh-300-finalize-fence.v3"
)
FRESH_FINALIZE_OPERATION: Final = "map-fresh-300-finalize"
FRESH_FINALIZE_RESULT_SCHEMA: Final = (
    "kor-travel-map.application-fresh-300-finalize.v3"
)
APPLICATION_FINAL_PERMIT_SCHEMA: Final = (
    "kor-travel-docker-manager.map-application-final-permit.v4"
)
APPLICATION_FINAL_PERMIT_TRANSITION: Final = "map-fresh-300-finalize"
APPLICATION_FINAL_PERMIT_STATE: Final = "finalized"
APPLICATION_FINAL_PERMIT_DATABASE_IDENTITY_SCHEMA: Final = (
    "kor-travel-map.application-final-permit-database.v1"
)
APPLICATION_FINAL_PERMIT_FRESH_EVIDENCE_SCHEMA: Final = (
    "kor-travel-docker-manager.map-final-permit-fresh-finalize-evidence.v2"
)
DAGSTER_STORAGE_PERMIT_SCHEMA: Final = (
    "kor-travel-map.dagster-storage-database-permit.v1"
)
DAGSTER_STORAGE_PERMIT_AUTHORITY: Final = "docker-manager"

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
_DATABASE_NAME_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_ROLE_NAME_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_CONTRACT_FIELDS: Final = frozenset(
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
_FRESH_MIGRATION_FENCE_FIELDS: Final = frozenset(
    {
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
)
_FRESH_ROOT_RESULT_FIELDS: Final = frozenset(
    {
        "schema",
        "outcome",
        "authorization",
        "destination_head",
        "map_candidate_commit",
        "map_candidate_image_id",
        "reference_manifest_sha256",
        "writer_fence_receipt_sha256",
        "writer_fence_transaction_id",
        "journal_sha256",
        "journal_generation",
        "database_identity",
        "expected_destination_alembic_version_sha256",
        "post_destination_alembic_version_sha256",
    }
)
_FRESH_ROOT_DATABASE_IDENTITY_FIELDS: Final = frozenset(
    {
        "database_name",
        "database_oid",
        "database_owner",
        "postgres_system_identifier",
    }
)
_FRESH_FINALIZE_FENCE_FIELDS: Final = frozenset(
    {
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
)
_FRESH_FINALIZE_RESULT_FIELDS: Final = frozenset(
    {
        "schema",
        "outcome",
        "destination_head",
        "map_candidate_commit",
        "map_candidate_image_id",
        "reference_manifest_sha256",
        "writer_fence_receipt_sha256",
        "writer_fence_transaction_id",
        "journal_sha256",
        "journal_generation",
        "prior_fresh_migration_result_sha256",
        "prior_fresh_migration_fence_sha256",
        "prior_fresh_migration_transaction_id",
        "prior_fresh_migration_journal_sha256",
        "prior_fresh_migration_generation",
        "pre_source_catalog_sha256",
        "post_destination_catalog_sha256",
        "post_destination_alembic_version_sha256",
    }
)
_FINAL_PERMIT_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema",
        "transition_kind",
        "state",
        "transaction_id",
        "candidate",
        "database",
        "receipts",
        "operation_evidence",
    }
)
_FINAL_PERMIT_CANDIDATE_FIELDS: Final = frozenset(
    {
        "map_source_commit",
        "api_image_id",
        "dagster_image_id",
        "postgres_image_id",
        "application_head",
        "reference_manifest_sha256",
        "source_alembic_version_sha256",
        "destination_alembic_version_sha256",
        "runtime_invariants_sql_sha256",
    }
)
_FINAL_PERMIT_DATABASE_FIELDS: Final = frozenset(
    {"name", "oid", "owner", "system_identifier", "identity_sha256"}
)
_FINAL_PERMIT_RECEIPT_FIELDS: Final = frozenset(
    {
        "expected_catalog_sha256",
        "observed_catalog_sha256",
        "expected_seed_sha256",
        "observed_seed_sha256",
        "expected_privileged_residue_sha256",
        "pre_privileged_residue_sha256",
        "post_privileged_residue_sha256",
        "expected_destination_alembic_version_sha256",
        "observed_destination_alembic_version_sha256",
        "runtime_invariant_violation_count",
    }
)
_FINAL_PERMIT_FRESH_EVIDENCE_FIELDS: Final = frozenset(
    {
        "schema",
        "journal_sha256",
        "journal_generation",
        "finalize_result_sha256",
        "finalize_fence_receipt_sha256",
        "finalize_fence_transaction_id",
        "prior_fresh_migration_result_sha256",
        "prior_fresh_migration_fence_sha256",
        "prior_fresh_migration_transaction_id",
        "prior_fresh_migration_journal_sha256",
        "prior_fresh_migration_generation",
        "pre_source_catalog_sha256",
        "post_destination_catalog_sha256",
        "post_destination_alembic_version_sha256",
    }
)
_DAGSTER_STORAGE_PERMIT_FIELDS: Final = frozenset(
    {"schema", "authority", "candidate", "dagster_database", "application_database"}
)
_DAGSTER_STORAGE_CANDIDATE_FIELDS: Final = frozenset(
    {"dagster_image_id", "paired_candidate_build_receipt_sha256", "dagster_config_sha256"}
)
_DAGSTER_DATABASE_FIELDS: Final = frozenset(
    {
        "system_identifier",
        "name",
        "oid",
        "owner",
        "login_role",
        "login_role_attributes",
    }
)
_DAGSTER_LOGIN_ROLE_ATTRIBUTE_FIELDS: Final = frozenset(
    {
        "superuser",
        "create_database",
        "create_role",
        "replication",
        "bypass_rls",
        "granted_role_count",
        "member_role_count",
    }
)
_DAGSTER_APPLICATION_DATABASE_FIELDS: Final = frozenset(
    {"system_identifier", "name", "oid", "owner"}
)


class MapApplication300ContractError(ValueError):
    """Raised when a Map application ``300`` artifact is not exact."""


@dataclass(frozen=True)
class JsonArtifact:
    """Canonical JSON bytes plus the SHA-256 needed for journal binding."""

    payload: Mapping[str, Any]
    raw: bytes
    sha256: str


@dataclass(frozen=True)
class HostArtifactReceipt:
    """Result of an owner-only host artifact write."""

    path: Path
    sha256: str
    size: int


@dataclass(frozen=True)
class Application300Contract:
    """Installed Map static application contract."""

    reference_manifest_sha256: str
    postgres_image_id: str
    source_catalog_sha256: str
    destination_catalog_sha256: str
    seed_sha256: str
    privileged_residue_sha256: str
    source_alembic_version_sha256: str
    destination_alembic_version_sha256: str
    runtime_invariants_sql_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(self.reference_manifest_sha256, "reference_manifest_sha256")
        _require_image_id(self.postgres_image_id, "postgres_image_id")
        _require_sha256(self.source_catalog_sha256, "source_catalog_sha256")
        _require_sha256(self.destination_catalog_sha256, "destination_catalog_sha256")
        _require_sha256(self.seed_sha256, "seed_sha256")
        _require_sha256(self.privileged_residue_sha256, "privileged_residue_sha256")
        _require_sha256(
            self.source_alembic_version_sha256, "source_alembic_version_sha256"
        )
        _require_sha256(
            self.destination_alembic_version_sha256,
            "destination_alembic_version_sha256",
        )
        _require_sha256(
            self.runtime_invariants_sql_sha256, "runtime_invariants_sql_sha256"
        )

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> Application300Contract:
        payload = _require_exact_fields(value, _CONTRACT_FIELDS, "baseline contract")
        if (
            payload["schema"] != BASELINE_CONTRACT_SCHEMA
            or payload["application_head"] != APPLICATION_HEAD
        ):
            raise MapApplication300ContractError("baseline contract identity is invalid")
        return cls(
            reference_manifest_sha256=_require_sha256(
                payload["reference_manifest_sha256"], "reference_manifest_sha256"
            ),
            postgres_image_id=_require_image_id(
                payload["postgres_image_id"], "postgres_image_id"
            ),
            source_catalog_sha256=_require_sha256(
                payload["source_catalog_sha256"], "source_catalog_sha256"
            ),
            destination_catalog_sha256=_require_sha256(
                payload["destination_catalog_sha256"], "destination_catalog_sha256"
            ),
            seed_sha256=_require_sha256(payload["seed_sha256"], "seed_sha256"),
            privileged_residue_sha256=_require_sha256(
                payload["privileged_residue_sha256"], "privileged_residue_sha256"
            ),
            source_alembic_version_sha256=_require_sha256(
                payload["source_alembic_version_sha256"],
                "source_alembic_version_sha256",
            ),
            destination_alembic_version_sha256=_require_sha256(
                payload["destination_alembic_version_sha256"],
                "destination_alembic_version_sha256",
            ),
            runtime_invariants_sql_sha256=_require_sha256(
                payload["runtime_invariants_sql_sha256"],
                "runtime_invariants_sql_sha256",
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": BASELINE_CONTRACT_SCHEMA,
            "application_head": APPLICATION_HEAD,
            "reference_manifest_sha256": self.reference_manifest_sha256,
            "postgres_image_id": self.postgres_image_id,
            "source_catalog_sha256": self.source_catalog_sha256,
            "destination_catalog_sha256": self.destination_catalog_sha256,
            "seed_sha256": self.seed_sha256,
            "privileged_residue_sha256": self.privileged_residue_sha256,
            "source_alembic_version_sha256": self.source_alembic_version_sha256,
            "destination_alembic_version_sha256": (
                self.destination_alembic_version_sha256
            ),
            "runtime_invariants_sql_sha256": self.runtime_invariants_sql_sha256,
        }


@dataclass(frozen=True)
class Application300Candidate:
    """Map application ``300`` API/Dagster image identity."""

    map_source_commit: str
    api_image_id: str
    dagster_image_id: str

    def __post_init__(self) -> None:
        commit = _require_commit(self.map_source_commit, "map_source_commit")
        if commit != MAP_APPLICATION_300_SOURCE_COMMIT:
            raise MapApplication300ContractError(
                "Map application 300 source commit is not the fixed release candidate"
            )
        _require_image_id(self.api_image_id, "api_image_id")
        _require_image_id(self.dagster_image_id, "dagster_image_id")


@dataclass(frozen=True)
class JournalStamp:
    """Manager journal generation bound into one fence."""

    transaction_id: str
    journal_sha256: str
    journal_generation: int

    def __post_init__(self) -> None:
        _require_uuid(self.transaction_id, "transaction_id")
        _require_sha256(self.journal_sha256, "journal_sha256")
        _require_positive_int(self.journal_generation, "journal_generation")


@dataclass(frozen=True)
class ApplicationDatabaseIdentity:
    """Non-secret identity of the Map application database."""

    name: str
    oid: int
    owner: str
    system_identifier: str

    def __post_init__(self) -> None:
        _require_database_name(self.name, "database name")
        _require_positive_int(self.oid, "database oid")
        _require_role_name(self.owner, "database owner")
        if self.owner != APPLICATION_DATABASE_OWNER:
            raise MapApplication300ContractError("application database owner is invalid")
        _require_system_identifier(self.system_identifier, "postgres system identifier")

    @classmethod
    def from_fresh_result_payload(
        cls, value: Mapping[str, Any]
    ) -> ApplicationDatabaseIdentity:
        identity = _require_exact_fields(
            value, _FRESH_ROOT_DATABASE_IDENTITY_FIELDS, "fresh root database identity"
        )
        return cls(
            name=_require_database_name(identity["database_name"], "database_name"),
            oid=_require_positive_int(identity["database_oid"], "database_oid"),
            owner=_require_role_name(identity["database_owner"], "database_owner"),
            system_identifier=_require_system_identifier(
                identity["postgres_system_identifier"], "postgres_system_identifier"
            ),
        )

    def to_fence_payload(self) -> dict[str, Any]:
        return {
            "database_name": self.name,
            "database_oid": self.oid,
            "database_owner": self.owner,
            "postgres_system_identifier": self.system_identifier,
        }

    def to_final_permit_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "oid": self.oid,
            "owner": self.owner,
            "system_identifier": self.system_identifier,
            "identity_sha256": application_database_identity_sha256(self),
        }

    def to_dagster_permit_application_payload(self) -> dict[str, Any]:
        return {
            "system_identifier": self.system_identifier,
            "name": self.name,
            "oid": self.oid,
            "owner": self.owner,
        }


@dataclass(frozen=True)
class DagsterLoginRoleAttributes:
    """Privileges that must stay absent from the Dagster metadata login role."""

    superuser: bool = False
    create_database: bool = False
    create_role: bool = False
    replication: bool = False
    bypass_rls: bool = False
    granted_role_count: int = 0
    member_role_count: int = 0

    def __post_init__(self) -> None:
        for name in (
            "superuser",
            "create_database",
            "create_role",
            "replication",
            "bypass_rls",
        ):
            if getattr(self, name) is not False:
                raise MapApplication300ContractError(
                    "Dagster metadata login role has unsafe privileges"
                )
        for name in ("granted_role_count", "member_role_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value != 0:
                raise MapApplication300ContractError(
                    "Dagster metadata login role has role memberships"
                )

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> DagsterLoginRoleAttributes:
        payload = _require_exact_fields(
            value,
            _DAGSTER_LOGIN_ROLE_ATTRIBUTE_FIELDS,
            "Dagster login role attributes",
        )
        return cls(
            superuser=_require_bool(payload["superuser"], "superuser"),
            create_database=_require_bool(
                payload["create_database"], "create_database"
            ),
            create_role=_require_bool(payload["create_role"], "create_role"),
            replication=_require_bool(payload["replication"], "replication"),
            bypass_rls=_require_bool(payload["bypass_rls"], "bypass_rls"),
            granted_role_count=_require_non_negative_int(
                payload["granted_role_count"], "granted_role_count"
            ),
            member_role_count=_require_non_negative_int(
                payload["member_role_count"], "member_role_count"
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "superuser": self.superuser,
            "create_database": self.create_database,
            "create_role": self.create_role,
            "replication": self.replication,
            "bypass_rls": self.bypass_rls,
            "granted_role_count": self.granted_role_count,
            "member_role_count": self.member_role_count,
        }


@dataclass(frozen=True)
class DagsterDatabaseIdentity:
    """Non-secret identity of the Dagster metadata database."""

    system_identifier: str
    name: str
    oid: int
    owner: str
    login_role: str
    login_role_attributes: DagsterLoginRoleAttributes

    def __post_init__(self) -> None:
        _require_system_identifier(self.system_identifier, "postgres system identifier")
        _require_database_name(self.name, "Dagster database name")
        _require_positive_int(self.oid, "Dagster database oid")
        _require_role_name(self.owner, "Dagster database owner")
        _require_role_name(self.login_role, "Dagster login role")
        if self.owner != self.login_role:
            raise MapApplication300ContractError(
                "Dagster metadata owner must equal login role"
            )

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> DagsterDatabaseIdentity:
        payload = _require_exact_fields(
            value, _DAGSTER_DATABASE_FIELDS, "Dagster database identity"
        )
        return cls(
            system_identifier=_require_system_identifier(
                payload["system_identifier"], "system_identifier"
            ),
            name=_require_database_name(payload["name"], "name"),
            oid=_require_positive_int(payload["oid"], "oid"),
            owner=_require_role_name(payload["owner"], "owner"),
            login_role=_require_role_name(payload["login_role"], "login_role"),
            login_role_attributes=DagsterLoginRoleAttributes.from_payload(
                _require_mapping(
                    payload["login_role_attributes"], "login_role_attributes"
                )
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "system_identifier": self.system_identifier,
            "name": self.name,
            "oid": self.oid,
            "owner": self.owner,
            "login_role": self.login_role,
            "login_role_attributes": self.login_role_attributes.to_payload(),
        }


@dataclass(frozen=True)
class DagsterStorageCandidate:
    """Candidate inputs for the Dagster metadata permit."""

    dagster_image_id: str
    paired_candidate_build_receipt_sha256: str
    dagster_config_sha256: str

    def __post_init__(self) -> None:
        _require_image_id(self.dagster_image_id, "dagster_image_id")
        _require_sha256(
            self.paired_candidate_build_receipt_sha256,
            "paired_candidate_build_receipt_sha256",
        )
        _require_sha256(self.dagster_config_sha256, "dagster_config_sha256")

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> DagsterStorageCandidate:
        payload = _require_exact_fields(
            value, _DAGSTER_STORAGE_CANDIDATE_FIELDS, "Dagster storage candidate"
        )
        return cls(
            dagster_image_id=_require_image_id(
                payload["dagster_image_id"], "dagster_image_id"
            ),
            paired_candidate_build_receipt_sha256=_require_sha256(
                payload["paired_candidate_build_receipt_sha256"],
                "paired_candidate_build_receipt_sha256",
            ),
            dagster_config_sha256=_require_sha256(
                payload["dagster_config_sha256"], "dagster_config_sha256"
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "dagster_image_id": self.dagster_image_id,
            "paired_candidate_build_receipt_sha256": (
                self.paired_candidate_build_receipt_sha256
            ),
            "dagster_config_sha256": self.dagster_config_sha256,
        }


@dataclass(frozen=True)
class FreshRootResult:
    """Validated fresh root migration result."""

    payload_sha256: str
    writer_fence_receipt_sha256: str
    writer_fence_transaction_id: str
    journal_sha256: str
    journal_generation: int
    map_candidate_commit: str
    map_candidate_image_id: str
    reference_manifest_sha256: str
    database_identity: ApplicationDatabaseIdentity
    expected_destination_alembic_version_sha256: str
    post_destination_alembic_version_sha256: str


@dataclass(frozen=True)
class FreshFinalizeResult:
    """Validated fresh ACL finalize result."""

    payload_sha256: str
    writer_fence_receipt_sha256: str
    writer_fence_transaction_id: str
    journal_sha256: str
    journal_generation: int
    prior_fresh_migration_result_sha256: str
    prior_fresh_migration_fence_sha256: str
    prior_fresh_migration_transaction_id: str
    prior_fresh_migration_journal_sha256: str
    prior_fresh_migration_generation: int
    map_candidate_commit: str
    map_candidate_image_id: str
    reference_manifest_sha256: str
    pre_source_catalog_sha256: str
    post_destination_catalog_sha256: str
    post_destination_alembic_version_sha256: str


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return the canonical JSON form used for Manager-owned receipts."""

    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def json_artifact(payload: Mapping[str, Any]) -> JsonArtifact:
    raw = canonical_json_bytes(payload)
    return JsonArtifact(payload=payload, raw=raw, sha256=sha256_bytes(raw))


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def application_database_identity_sha256(identity: ApplicationDatabaseIdentity) -> str:
    value = (
        f"{APPLICATION_FINAL_PERMIT_DATABASE_IDENTITY_SCHEMA}\0"
        f"{identity.system_identifier}\0{identity.name}\0{identity.oid}\0{identity.owner}"
    )
    return sha256_bytes(value.encode("utf-8"))


def build_fresh_migration_fence(
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
    database: ApplicationDatabaseIdentity,
    journal: JournalStamp,
    writer_fence_expires_at: datetime,
) -> JsonArtifact:
    payload = {
        "schema": FRESH_MIGRATION_FENCE_SCHEMA,
        "transaction_id": journal.transaction_id,
        "journal_sha256": journal.journal_sha256,
        "journal_generation": journal.journal_generation,
        "operation": FRESH_MIGRATION_OPERATION,
        "map_candidate_commit": candidate.map_source_commit,
        "map_candidate_image_id": candidate.api_image_id,
        "postgres_image_id": contract.postgres_image_id,
        "destination_head": APPLICATION_HEAD,
        "reference_manifest_sha256": contract.reference_manifest_sha256,
        "source_catalog_sha256": contract.source_catalog_sha256,
        "destination_catalog_sha256": contract.destination_catalog_sha256,
        "seed_sha256": contract.seed_sha256,
        "privileged_residue_sha256": contract.privileged_residue_sha256,
        "source_alembic_version_sha256": contract.source_alembic_version_sha256,
        "destination_alembic_version_sha256": (
            contract.destination_alembic_version_sha256
        ),
        "runtime_invariants_sql_sha256": contract.runtime_invariants_sql_sha256,
        **database.to_fence_payload(),
        "writer_fence_expires_at": _iso_datetime(writer_fence_expires_at),
    }
    _validate_fresh_migration_fence(payload, contract=contract, candidate=candidate)
    return json_artifact(payload)


def build_fresh_finalize_fence(
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
    database: ApplicationDatabaseIdentity,
    journal: JournalStamp,
    prior: FreshRootResult,
    writer_fence_expires_at: datetime,
) -> JsonArtifact:
    _require_prior_root_binding(prior, contract=contract, candidate=candidate, database=database)
    if journal.journal_generation <= prior.journal_generation:
        raise MapApplication300ContractError(
            "fresh finalize journal generation must advance root generation"
        )
    if journal.transaction_id == prior.writer_fence_transaction_id:
        raise MapApplication300ContractError(
            "fresh finalize transaction must differ from root transaction"
        )
    payload = {
        "schema": FRESH_FINALIZE_FENCE_SCHEMA,
        "transaction_id": journal.transaction_id,
        "journal_sha256": journal.journal_sha256,
        "journal_generation": journal.journal_generation,
        "operation": FRESH_FINALIZE_OPERATION,
        "prior_fresh_migration_result_sha256": prior.payload_sha256,
        "prior_fresh_migration_fence_sha256": prior.writer_fence_receipt_sha256,
        "prior_fresh_migration_transaction_id": prior.writer_fence_transaction_id,
        "prior_fresh_migration_journal_sha256": prior.journal_sha256,
        "prior_fresh_migration_generation": prior.journal_generation,
        "map_candidate_commit": candidate.map_source_commit,
        "map_candidate_image_id": candidate.api_image_id,
        "postgres_image_id": contract.postgres_image_id,
        "destination_head": APPLICATION_HEAD,
        "reference_manifest_sha256": contract.reference_manifest_sha256,
        "source_catalog_sha256": contract.source_catalog_sha256,
        "destination_catalog_sha256": contract.destination_catalog_sha256,
        "seed_sha256": contract.seed_sha256,
        "privileged_residue_sha256": contract.privileged_residue_sha256,
        "pre_privileged_residue_sha256": contract.privileged_residue_sha256,
        "destination_alembic_version_sha256": (
            contract.destination_alembic_version_sha256
        ),
        "runtime_invariants_sql_sha256": contract.runtime_invariants_sql_sha256,
        **database.to_fence_payload(),
        "writer_fence_expires_at": _iso_datetime(writer_fence_expires_at),
    }
    _validate_fresh_finalize_fence(payload, contract=contract, candidate=candidate)
    return json_artifact(payload)


def parse_fresh_root_result(
    raw: bytes,
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
) -> FreshRootResult:
    payload = _load_exact_json(
        raw,
        _FRESH_ROOT_RESULT_FIELDS,
        "fresh root result",
        canonical_line=True,
    )
    if (
        payload["schema"] != FRESH_ROOT_RESULT_SCHEMA
        or payload["outcome"] != "root-committed"
        or payload["authorization"] != "manager-fence"
        or payload["destination_head"] != APPLICATION_HEAD
    ):
        raise MapApplication300ContractError("fresh root result identity is invalid")
    identity = ApplicationDatabaseIdentity.from_fresh_result_payload(
        _require_mapping(payload["database_identity"], "database_identity")
    )
    result = FreshRootResult(
        payload_sha256=sha256_bytes(raw),
        writer_fence_receipt_sha256=_require_sha256(
            payload["writer_fence_receipt_sha256"], "writer_fence_receipt_sha256"
        ),
        writer_fence_transaction_id=_require_uuid(
            payload["writer_fence_transaction_id"], "writer_fence_transaction_id"
        ),
        journal_sha256=_require_sha256(payload["journal_sha256"], "journal_sha256"),
        journal_generation=_require_positive_int(
            payload["journal_generation"], "journal_generation"
        ),
        map_candidate_commit=_require_commit(
            payload["map_candidate_commit"], "map_candidate_commit"
        ),
        map_candidate_image_id=_require_image_id(
            payload["map_candidate_image_id"], "map_candidate_image_id"
        ),
        reference_manifest_sha256=_require_sha256(
            payload["reference_manifest_sha256"], "reference_manifest_sha256"
        ),
        database_identity=identity,
        expected_destination_alembic_version_sha256=_require_sha256(
            payload["expected_destination_alembic_version_sha256"],
            "expected_destination_alembic_version_sha256",
        ),
        post_destination_alembic_version_sha256=_require_sha256(
            payload["post_destination_alembic_version_sha256"],
            "post_destination_alembic_version_sha256",
        ),
    )
    _require_prior_root_binding(
        result, contract=contract, candidate=candidate, database=identity
    )
    return result


def parse_fresh_finalize_result(
    raw: bytes,
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
    prior: FreshRootResult,
) -> FreshFinalizeResult:
    payload = _load_exact_json(
        raw,
        _FRESH_FINALIZE_RESULT_FIELDS,
        "fresh finalize result",
        canonical_line=True,
    )
    if (
        payload["schema"] != FRESH_FINALIZE_RESULT_SCHEMA
        or payload["outcome"] != "finalized"
        or payload["destination_head"] != APPLICATION_HEAD
    ):
        raise MapApplication300ContractError("fresh finalize result identity is invalid")
    result = FreshFinalizeResult(
        payload_sha256=sha256_bytes(raw),
        writer_fence_receipt_sha256=_require_sha256(
            payload["writer_fence_receipt_sha256"], "writer_fence_receipt_sha256"
        ),
        writer_fence_transaction_id=_require_uuid(
            payload["writer_fence_transaction_id"], "writer_fence_transaction_id"
        ),
        journal_sha256=_require_sha256(payload["journal_sha256"], "journal_sha256"),
        journal_generation=_require_positive_int(
            payload["journal_generation"], "journal_generation"
        ),
        prior_fresh_migration_result_sha256=_require_sha256(
            payload["prior_fresh_migration_result_sha256"],
            "prior_fresh_migration_result_sha256",
        ),
        prior_fresh_migration_fence_sha256=_require_sha256(
            payload["prior_fresh_migration_fence_sha256"],
            "prior_fresh_migration_fence_sha256",
        ),
        prior_fresh_migration_transaction_id=_require_uuid(
            payload["prior_fresh_migration_transaction_id"],
            "prior_fresh_migration_transaction_id",
        ),
        prior_fresh_migration_journal_sha256=_require_sha256(
            payload["prior_fresh_migration_journal_sha256"],
            "prior_fresh_migration_journal_sha256",
        ),
        prior_fresh_migration_generation=_require_positive_int(
            payload["prior_fresh_migration_generation"],
            "prior_fresh_migration_generation",
        ),
        map_candidate_commit=_require_commit(
            payload["map_candidate_commit"], "map_candidate_commit"
        ),
        map_candidate_image_id=_require_image_id(
            payload["map_candidate_image_id"], "map_candidate_image_id"
        ),
        reference_manifest_sha256=_require_sha256(
            payload["reference_manifest_sha256"], "reference_manifest_sha256"
        ),
        pre_source_catalog_sha256=_require_sha256(
            payload["pre_source_catalog_sha256"], "pre_source_catalog_sha256"
        ),
        post_destination_catalog_sha256=_require_sha256(
            payload["post_destination_catalog_sha256"],
            "post_destination_catalog_sha256",
        ),
        post_destination_alembic_version_sha256=_require_sha256(
            payload["post_destination_alembic_version_sha256"],
            "post_destination_alembic_version_sha256",
        ),
    )
    _require_finalize_binding(
        result, contract=contract, candidate=candidate, prior=prior
    )
    return result


def build_application_final_permit(
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
    database: ApplicationDatabaseIdentity,
    finalize_result: FreshFinalizeResult,
) -> JsonArtifact:
    _require_finalize_binding(
        finalize_result,
        contract=contract,
        candidate=candidate,
        prior=None,
    )
    payload = {
        "schema": APPLICATION_FINAL_PERMIT_SCHEMA,
        "transition_kind": APPLICATION_FINAL_PERMIT_TRANSITION,
        "state": APPLICATION_FINAL_PERMIT_STATE,
        "transaction_id": finalize_result.writer_fence_transaction_id,
        "candidate": {
            "map_source_commit": candidate.map_source_commit,
            "api_image_id": candidate.api_image_id,
            "dagster_image_id": candidate.dagster_image_id,
            "postgres_image_id": contract.postgres_image_id,
            "application_head": APPLICATION_HEAD,
            "reference_manifest_sha256": contract.reference_manifest_sha256,
            "source_alembic_version_sha256": (
                contract.source_alembic_version_sha256
            ),
            "destination_alembic_version_sha256": (
                contract.destination_alembic_version_sha256
            ),
            "runtime_invariants_sql_sha256": contract.runtime_invariants_sql_sha256,
        },
        "database": database.to_final_permit_payload(),
        "receipts": {
            "expected_catalog_sha256": contract.destination_catalog_sha256,
            "observed_catalog_sha256": contract.destination_catalog_sha256,
            "expected_seed_sha256": contract.seed_sha256,
            "observed_seed_sha256": contract.seed_sha256,
            "expected_privileged_residue_sha256": contract.privileged_residue_sha256,
            "pre_privileged_residue_sha256": contract.privileged_residue_sha256,
            "post_privileged_residue_sha256": contract.privileged_residue_sha256,
            "expected_destination_alembic_version_sha256": (
                contract.destination_alembic_version_sha256
            ),
            "observed_destination_alembic_version_sha256": (
                contract.destination_alembic_version_sha256
            ),
            "runtime_invariant_violation_count": 0,
        },
        "operation_evidence": {
            "schema": APPLICATION_FINAL_PERMIT_FRESH_EVIDENCE_SCHEMA,
            "journal_sha256": finalize_result.journal_sha256,
            "journal_generation": finalize_result.journal_generation,
            "finalize_result_sha256": finalize_result.payload_sha256,
            "finalize_fence_receipt_sha256": (
                finalize_result.writer_fence_receipt_sha256
            ),
            "finalize_fence_transaction_id": (
                finalize_result.writer_fence_transaction_id
            ),
            "prior_fresh_migration_result_sha256": (
                finalize_result.prior_fresh_migration_result_sha256
            ),
            "prior_fresh_migration_fence_sha256": (
                finalize_result.prior_fresh_migration_fence_sha256
            ),
            "prior_fresh_migration_transaction_id": (
                finalize_result.prior_fresh_migration_transaction_id
            ),
            "prior_fresh_migration_journal_sha256": (
                finalize_result.prior_fresh_migration_journal_sha256
            ),
            "prior_fresh_migration_generation": (
                finalize_result.prior_fresh_migration_generation
            ),
            "pre_source_catalog_sha256": finalize_result.pre_source_catalog_sha256,
            "post_destination_catalog_sha256": (
                finalize_result.post_destination_catalog_sha256
            ),
            "post_destination_alembic_version_sha256": (
                finalize_result.post_destination_alembic_version_sha256
            ),
        },
    }
    validate_application_final_permit(
        json_artifact(payload).raw, contract=contract, candidate=candidate
    )
    return json_artifact(payload)


def validate_application_final_permit(
    raw: bytes,
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
) -> Mapping[str, Any]:
    payload = _load_exact_json(raw, _FINAL_PERMIT_TOP_LEVEL_FIELDS, "final permit")
    if (
        payload["schema"] != APPLICATION_FINAL_PERMIT_SCHEMA
        or payload["transition_kind"] != APPLICATION_FINAL_PERMIT_TRANSITION
        or payload["state"] != APPLICATION_FINAL_PERMIT_STATE
    ):
        raise MapApplication300ContractError("application final permit identity is invalid")
    transaction_id = _require_uuid(payload["transaction_id"], "transaction_id")
    permit_candidate = _require_exact_fields(
        payload["candidate"], _FINAL_PERMIT_CANDIDATE_FIELDS, "final permit candidate"
    )
    permit_database = _require_exact_fields(
        payload["database"], _FINAL_PERMIT_DATABASE_FIELDS, "final permit database"
    )
    receipts = _require_exact_fields(
        payload["receipts"], _FINAL_PERMIT_RECEIPT_FIELDS, "final permit receipts"
    )
    evidence = _require_exact_fields(
        payload["operation_evidence"],
        _FINAL_PERMIT_FRESH_EVIDENCE_FIELDS,
        "final permit fresh evidence",
    )
    _validate_final_permit_candidate(
        permit_candidate, contract=contract, candidate=candidate
    )
    _validate_final_permit_database(permit_database)
    _validate_final_permit_receipts(receipts, contract=contract)
    _validate_final_permit_evidence(
        evidence, transaction_id=transaction_id, contract=contract
    )
    return payload


def build_dagster_metadata_permit(
    *,
    candidate: DagsterStorageCandidate,
    dagster_database: DagsterDatabaseIdentity,
    application_database: ApplicationDatabaseIdentity,
) -> JsonArtifact:
    _require_metadata_database_isolation(
        dagster_database=dagster_database, application_database=application_database
    )
    payload = {
        "schema": DAGSTER_STORAGE_PERMIT_SCHEMA,
        "authority": DAGSTER_STORAGE_PERMIT_AUTHORITY,
        "candidate": candidate.to_payload(),
        "dagster_database": dagster_database.to_payload(),
        "application_database": application_database.to_dagster_permit_application_payload(),
    }
    validate_dagster_metadata_permit(
        json_artifact(payload).raw,
        expected_candidate=candidate,
        application_database=application_database,
    )
    return json_artifact(payload)


def validate_dagster_metadata_permit(
    raw: bytes,
    *,
    expected_candidate: DagsterStorageCandidate,
    application_database: ApplicationDatabaseIdentity,
) -> Mapping[str, Any]:
    payload = _load_exact_json(
        raw, _DAGSTER_STORAGE_PERMIT_FIELDS, "Dagster metadata permit"
    )
    if (
        payload["schema"] != DAGSTER_STORAGE_PERMIT_SCHEMA
        or payload["authority"] != DAGSTER_STORAGE_PERMIT_AUTHORITY
    ):
        raise MapApplication300ContractError("Dagster metadata permit identity is invalid")
    candidate = DagsterStorageCandidate.from_payload(
        _require_mapping(payload["candidate"], "candidate")
    )
    if candidate != expected_candidate:
        raise MapApplication300ContractError("Dagster metadata candidate is invalid")
    dagster_database = DagsterDatabaseIdentity.from_payload(
        _require_mapping(payload["dagster_database"], "dagster_database")
    )
    app_identity = _require_exact_fields(
        payload["application_database"],
        _DAGSTER_APPLICATION_DATABASE_FIELDS,
        "Dagster permit application database",
    )
    observed_application = ApplicationDatabaseIdentity(
        system_identifier=_require_system_identifier(
            app_identity["system_identifier"], "system_identifier"
        ),
        name=_require_database_name(app_identity["name"], "name"),
        oid=_require_positive_int(app_identity["oid"], "oid"),
        owner=_require_role_name(app_identity["owner"], "owner"),
    )
    if observed_application != application_database:
        raise MapApplication300ContractError(
            "Dagster metadata permit application identity is invalid"
        )
    _require_metadata_database_isolation(
        dagster_database=dagster_database, application_database=observed_application
    )
    return payload


def write_owner_only_artifact(path: Path, raw: bytes) -> HostArtifactReceipt:
    """Write ``raw`` to ``path`` as a mode ``0600`` artifact.

    The function is idempotent for the same bytes and refuses to overwrite a
    different existing artifact.  The target is never followed as a symlink, and
    the directory is fsynced after the link/rename step.
    """

    _require_artifact_path(path)
    parent = path.parent
    _require_artifact_directory(parent)
    if path.exists() or path.is_symlink():
        return _verify_existing_artifact(path, raw)

    digest = sha256_bytes(raw)
    descriptor, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp_path, path, follow_symlinks=False)
        except FileExistsError:
            return _verify_existing_artifact(path, raw)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                return _verify_existing_artifact(path, raw)
            raise
        finally:
            _safe_unlink(tmp_path)
        _fsync_directory(parent)
        _verify_existing_artifact(path, raw)
        return HostArtifactReceipt(path=path, sha256=digest, size=len(raw))
    except Exception:
        _safe_unlink(tmp_path)
        raise


def _validate_fresh_migration_fence(
    payload: Mapping[str, Any],
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
) -> None:
    fence = _require_exact_fields(
        payload, _FRESH_MIGRATION_FENCE_FIELDS, "fresh migration fence"
    )
    if fence["schema"] != FRESH_MIGRATION_FENCE_SCHEMA:
        raise MapApplication300ContractError("fresh migration fence schema is invalid")
    if fence["operation"] != FRESH_MIGRATION_OPERATION:
        raise MapApplication300ContractError("fresh migration fence operation is invalid")
    _validate_common_fence_identity(fence, contract=contract, candidate=candidate)
    _require_sha256(fence["source_alembic_version_sha256"], "source_alembic_version")
    if (
        fence["source_alembic_version_sha256"]
        != contract.source_alembic_version_sha256
    ):
        raise MapApplication300ContractError(
            "fresh migration fence source Alembic binding is invalid"
        )


def _validate_fresh_finalize_fence(
    payload: Mapping[str, Any],
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
) -> None:
    fence = _require_exact_fields(
        payload, _FRESH_FINALIZE_FENCE_FIELDS, "fresh finalize fence"
    )
    if fence["schema"] != FRESH_FINALIZE_FENCE_SCHEMA:
        raise MapApplication300ContractError("fresh finalize fence schema is invalid")
    if fence["operation"] != FRESH_FINALIZE_OPERATION:
        raise MapApplication300ContractError("fresh finalize fence operation is invalid")
    _validate_common_fence_identity(fence, contract=contract, candidate=candidate)
    if (
        fence["pre_privileged_residue_sha256"]
        != contract.privileged_residue_sha256
    ):
        raise MapApplication300ContractError(
            "fresh finalize fence privileged residue binding is invalid"
        )
    for key in (
        "prior_fresh_migration_result_sha256",
        "prior_fresh_migration_fence_sha256",
        "prior_fresh_migration_journal_sha256",
    ):
        _require_sha256(fence[key], key)
    _require_uuid(
        fence["prior_fresh_migration_transaction_id"],
        "prior_fresh_migration_transaction_id",
    )
    prior_generation = _require_positive_int(
        fence["prior_fresh_migration_generation"],
        "prior_fresh_migration_generation",
    )
    current_generation = _require_positive_int(
        fence["journal_generation"], "journal_generation"
    )
    if current_generation <= prior_generation:
        raise MapApplication300ContractError(
            "fresh finalize fence generation must advance root generation"
        )


def _validate_common_fence_identity(
    fence: Mapping[str, Any],
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
) -> None:
    _require_uuid(fence["transaction_id"], "transaction_id")
    _require_sha256(fence["journal_sha256"], "journal_sha256")
    _require_positive_int(fence["journal_generation"], "journal_generation")
    if (
        fence["map_candidate_commit"] != candidate.map_source_commit
        or fence["map_candidate_image_id"] != candidate.api_image_id
        or fence["postgres_image_id"] != contract.postgres_image_id
        or fence["destination_head"] != APPLICATION_HEAD
        or fence["reference_manifest_sha256"] != contract.reference_manifest_sha256
        or fence["source_catalog_sha256"] != contract.source_catalog_sha256
        or fence["destination_catalog_sha256"] != contract.destination_catalog_sha256
        or fence["seed_sha256"] != contract.seed_sha256
        or fence["privileged_residue_sha256"] != contract.privileged_residue_sha256
        or fence["destination_alembic_version_sha256"]
        != contract.destination_alembic_version_sha256
        or fence["runtime_invariants_sql_sha256"]
        != contract.runtime_invariants_sql_sha256
    ):
        raise MapApplication300ContractError("fresh fence candidate binding is invalid")
    ApplicationDatabaseIdentity(
        name=_require_database_name(fence["database_name"], "database_name"),
        oid=_require_positive_int(fence["database_oid"], "database_oid"),
        owner=_require_role_name(fence["database_owner"], "database_owner"),
        system_identifier=_require_system_identifier(
            fence["postgres_system_identifier"], "postgres_system_identifier"
        ),
    )
    _require_future_datetime(fence["writer_fence_expires_at"], "writer_fence_expires_at")


def _require_prior_root_binding(
    prior: FreshRootResult,
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
    database: ApplicationDatabaseIdentity,
) -> None:
    if (
        prior.map_candidate_commit != candidate.map_source_commit
        or prior.map_candidate_image_id != candidate.api_image_id
        or prior.reference_manifest_sha256 != contract.reference_manifest_sha256
        or prior.database_identity != database
        or prior.expected_destination_alembic_version_sha256
        != contract.destination_alembic_version_sha256
        or prior.post_destination_alembic_version_sha256
        != contract.destination_alembic_version_sha256
    ):
        raise MapApplication300ContractError(
            "fresh root result does not bind to the selected candidate"
        )


def _require_finalize_binding(
    finalize: FreshFinalizeResult,
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
    prior: FreshRootResult | None,
) -> None:
    if (
        finalize.map_candidate_commit != candidate.map_source_commit
        or finalize.map_candidate_image_id != candidate.api_image_id
        or finalize.reference_manifest_sha256 != contract.reference_manifest_sha256
        or finalize.pre_source_catalog_sha256 != contract.source_catalog_sha256
        or finalize.post_destination_catalog_sha256
        != contract.destination_catalog_sha256
        or finalize.post_destination_alembic_version_sha256
        != contract.destination_alembic_version_sha256
    ):
        raise MapApplication300ContractError(
            "fresh finalize result does not bind to the selected candidate"
        )
    if finalize.journal_generation <= finalize.prior_fresh_migration_generation:
        raise MapApplication300ContractError(
            "fresh finalize result generation is invalid"
        )
    if prior is not None and (
        finalize.prior_fresh_migration_result_sha256 != prior.payload_sha256
        or finalize.prior_fresh_migration_fence_sha256
        != prior.writer_fence_receipt_sha256
        or finalize.prior_fresh_migration_transaction_id
        != prior.writer_fence_transaction_id
        or finalize.prior_fresh_migration_journal_sha256 != prior.journal_sha256
        or finalize.prior_fresh_migration_generation != prior.journal_generation
    ):
        raise MapApplication300ContractError(
            "fresh finalize result prior lineage is invalid"
        )


def _validate_final_permit_candidate(
    value: Mapping[str, Any],
    *,
    contract: Application300Contract,
    candidate: Application300Candidate,
) -> None:
    if (
        value["map_source_commit"] != candidate.map_source_commit
        or value["api_image_id"] != candidate.api_image_id
        or value["dagster_image_id"] != candidate.dagster_image_id
        or value["postgres_image_id"] != contract.postgres_image_id
        or value["application_head"] != APPLICATION_HEAD
        or value["reference_manifest_sha256"] != contract.reference_manifest_sha256
        or value["source_alembic_version_sha256"]
        != contract.source_alembic_version_sha256
        or value["destination_alembic_version_sha256"]
        != contract.destination_alembic_version_sha256
        or value["runtime_invariants_sql_sha256"]
        != contract.runtime_invariants_sql_sha256
    ):
        raise MapApplication300ContractError("final permit candidate binding is invalid")


def _validate_final_permit_database(value: Mapping[str, Any]) -> None:
    identity = ApplicationDatabaseIdentity(
        name=_require_database_name(value["name"], "name"),
        oid=_require_positive_int(value["oid"], "oid"),
        owner=_require_role_name(value["owner"], "owner"),
        system_identifier=_require_system_identifier(
            value["system_identifier"], "system_identifier"
        ),
    )
    if value["identity_sha256"] != application_database_identity_sha256(identity):
        raise MapApplication300ContractError("final permit database identity is invalid")


def _validate_final_permit_receipts(
    receipts: Mapping[str, Any], *, contract: Application300Contract
) -> None:
    for key in _FINAL_PERMIT_RECEIPT_FIELDS - {"runtime_invariant_violation_count"}:
        _require_sha256(receipts[key], key)
    if receipts["runtime_invariant_violation_count"] != 0:
        raise MapApplication300ContractError(
            "final permit runtime invariant receipt is invalid"
        )
    if (
        receipts["expected_catalog_sha256"] != contract.destination_catalog_sha256
        or receipts["observed_catalog_sha256"] != contract.destination_catalog_sha256
        or receipts["expected_seed_sha256"] != contract.seed_sha256
        or receipts["observed_seed_sha256"] != contract.seed_sha256
        or receipts["expected_privileged_residue_sha256"]
        != contract.privileged_residue_sha256
        or receipts["pre_privileged_residue_sha256"]
        != contract.privileged_residue_sha256
        or receipts["post_privileged_residue_sha256"]
        != contract.privileged_residue_sha256
        or receipts["expected_destination_alembic_version_sha256"]
        != contract.destination_alembic_version_sha256
        or receipts["observed_destination_alembic_version_sha256"]
        != contract.destination_alembic_version_sha256
    ):
        raise MapApplication300ContractError("final permit receipts are invalid")


def _validate_final_permit_evidence(
    evidence: Mapping[str, Any],
    *,
    transaction_id: str,
    contract: Application300Contract,
) -> None:
    if evidence["schema"] != APPLICATION_FINAL_PERMIT_FRESH_EVIDENCE_SCHEMA:
        raise MapApplication300ContractError("final permit evidence schema is invalid")
    _require_sha256(evidence["journal_sha256"], "journal_sha256")
    generation = _require_positive_int(evidence["journal_generation"], "journal_generation")
    prior_generation = _require_positive_int(
        evidence["prior_fresh_migration_generation"],
        "prior_fresh_migration_generation",
    )
    if generation <= prior_generation:
        raise MapApplication300ContractError("final permit evidence generation is invalid")
    if (
        _require_uuid(
            evidence["finalize_fence_transaction_id"],
            "finalize_fence_transaction_id",
        )
        != transaction_id
    ):
        raise MapApplication300ContractError(
            "final permit evidence transaction binding is invalid"
        )
    _require_uuid(
        evidence["prior_fresh_migration_transaction_id"],
        "prior_fresh_migration_transaction_id",
    )
    for key in (
        "finalize_result_sha256",
        "finalize_fence_receipt_sha256",
        "prior_fresh_migration_result_sha256",
        "prior_fresh_migration_fence_sha256",
        "prior_fresh_migration_journal_sha256",
    ):
        _require_sha256(evidence[key], key)
    if (
        evidence["pre_source_catalog_sha256"] != contract.source_catalog_sha256
        or evidence["post_destination_catalog_sha256"]
        != contract.destination_catalog_sha256
        or evidence["post_destination_alembic_version_sha256"]
        != contract.destination_alembic_version_sha256
    ):
        raise MapApplication300ContractError("final permit evidence facets are invalid")


def _require_metadata_database_isolation(
    *,
    dagster_database: DagsterDatabaseIdentity,
    application_database: ApplicationDatabaseIdentity,
) -> None:
    if dagster_database.system_identifier != application_database.system_identifier:
        raise MapApplication300ContractError(
            "Dagster metadata database must share the selected PostgreSQL system"
        )
    if dagster_database.owner == application_database.owner or (
        dagster_database.name,
        dagster_database.oid,
    ) == (application_database.name, application_database.oid):
        raise MapApplication300ContractError(
            "Dagster metadata database must not target the application database"
        )


def _require_exact_fields(
    value: object, expected: frozenset[str], label: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise MapApplication300ContractError(f"{label} field set is invalid")
    return value


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MapApplication300ContractError(f"{label} must be an object")
    return value


def _load_exact_json(
    raw: bytes,
    expected: frozenset[str],
    label: str,
    *,
    canonical_line: bool = False,
) -> Mapping[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > 64 * 1024:
        raise MapApplication300ContractError(f"{label} JSON is invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MapApplication300ContractError(f"{label} JSON is invalid") from exc
    payload = _require_exact_fields(value, expected, label)
    if canonical_line and raw != canonical_json_bytes(payload) + b"\n":
        raise MapApplication300ContractError(f"{label} JSON is not canonical")
    return payload


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise MapApplication300ContractError(f"{label} digest is invalid")
    return value


def _require_image_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _IMAGE_ID_PATTERN.fullmatch(value) is None:
        raise MapApplication300ContractError(f"{label} image id is invalid")
    return value


def _require_commit(value: object, label: str) -> str:
    if not isinstance(value, str) or _COMMIT_PATTERN.fullmatch(value) is None:
        raise MapApplication300ContractError(f"{label} commit is invalid")
    return value


def _require_uuid(value: object, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise MapApplication300ContractError(f"{label} UUID is invalid") from exc


def _require_positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise MapApplication300ContractError(f"{label} must be a positive integer")
    return value


def _require_non_negative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MapApplication300ContractError(f"{label} must be a non-negative integer")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise MapApplication300ContractError(f"{label} must be boolean")
    return value


def _require_database_name(value: object, label: str) -> str:
    if not isinstance(value, str) or _DATABASE_NAME_PATTERN.fullmatch(value) is None:
        raise MapApplication300ContractError(f"{label} database name is invalid")
    return value


def _require_role_name(value: object, label: str) -> str:
    if not isinstance(value, str) or _ROLE_NAME_PATTERN.fullmatch(value) is None:
        raise MapApplication300ContractError(f"{label} role name is invalid")
    return value


def _require_system_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.isdigit():
        raise MapApplication300ContractError(f"{label} is invalid")
    return value


def _require_future_datetime(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise MapApplication300ContractError(f"{label} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MapApplication300ContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.astimezone(UTC) <= datetime.now(UTC):
        raise MapApplication300ContractError(f"{label} must be in the future")
    return value


def _iso_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise MapApplication300ContractError("writer fence expiry must include timezone")
    if value.astimezone(UTC) <= datetime.now(UTC):
        raise MapApplication300ContractError("writer fence expiry must be in the future")
    return value.isoformat()


def _require_artifact_path(path: Path) -> None:
    if not path.is_absolute():
        raise MapApplication300ContractError("artifact path must be absolute")
    if path.name in {"", ".", ".."}:
        raise MapApplication300ContractError("artifact path is invalid")
    if path.is_symlink():
        raise MapApplication300ContractError("artifact path is unsafe")
    if path != path.resolve(strict=False):
        raise MapApplication300ContractError("artifact path must be canonical")


def _require_artifact_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise MapApplication300ContractError("artifact directory is unavailable") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or mode & 0o077
        or mode & 0o700 != 0o700
    ):
        raise MapApplication300ContractError("artifact directory is unsafe")


def _verify_existing_artifact(path: Path, expected: bytes) -> HostArtifactReceipt:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise MapApplication300ContractError("artifact is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise MapApplication300ContractError("artifact metadata is unsafe")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            ):
                raise MapApplication300ContractError("artifact changed while opening")
            observed = os.read(descriptor, len(expected) + 1)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise MapApplication300ContractError("artifact cannot be read safely") from exc
    if observed != expected:
        raise MapApplication300ContractError("artifact already exists with different bytes")
    return HostArtifactReceipt(
        path=path, sha256=sha256_bytes(expected), size=len(expected)
    )


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
