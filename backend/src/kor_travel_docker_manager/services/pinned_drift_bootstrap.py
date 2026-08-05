"""F1D pinned compatible-pair drift bootstrap의 owner-only durable journal."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from kor_travel_docker_manager.services.c6c_deployment import (
    CompatibleImagePair,
    DeploymentContractError,
    c6c_state_paths,
    compatible_pair_manifest_logical_hash,
    ensure_c6c_state_directory,
    initial_pair_manifest,
)

_VERSION = 1
_FILENAME = "pinned-drift-bootstrap-v1.json"
_MAX_BYTES = 16 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_REVISION = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_PHASES = frozenset(
    {"prepared", "runtime_activated", "manifest_committing", "committed"}
)
_DATABASE_ROLES = frozenset({"map_application", "map_dagster", "pinvi"})


@dataclass(frozen=True)
class PinnedDriftBootstrapJournal:
    version: int
    phase: Literal[
        "prepared", "runtime_activated", "manifest_committing", "committed"
    ]
    transaction_id: str
    production_pin_version: int
    environment_sha256: str
    compose_sha256: str
    resolved_compose_sha256: str
    old_manifest_sha256: str
    old_active: CompatibleImagePair
    old_rollback: CompatibleImagePair
    candidate: CompatibleImagePair
    database_heads: Mapping[str, str]
    prepared_at: str
    runtime_activated_at: str | None
    manifest_committing_at: str | None
    committed_at: str | None


def pinned_drift_bootstrap_journal_path(values: Mapping[str, str]) -> Path:
    manifest_path, _ = c6c_state_paths(values)
    return Path(manifest_path).with_name(_FILENAME)


def prepare_pinned_drift_bootstrap(
    *,
    production_pin_version: int,
    environment_sha256: str,
    compose_sha256: str,
    resolved_compose_sha256: str,
    old_manifest_sha256: str,
    old_active: CompatibleImagePair,
    old_rollback: CompatibleImagePair,
    candidate: CompatibleImagePair,
    database_heads: Mapping[str, str],
) -> PinnedDriftBootstrapJournal:
    journal = PinnedDriftBootstrapJournal(
        version=_VERSION,
        phase="prepared",
        transaction_id=str(uuid.uuid4()),
        production_pin_version=production_pin_version,
        environment_sha256=environment_sha256,
        compose_sha256=compose_sha256,
        resolved_compose_sha256=resolved_compose_sha256,
        old_manifest_sha256=old_manifest_sha256,
        old_active=old_active,
        old_rollback=old_rollback,
        candidate=candidate,
        database_heads=dict(database_heads),
        prepared_at=_now(),
        runtime_activated_at=None,
        manifest_committing_at=None,
        committed_at=None,
    )
    _validate_journal(journal)
    return journal


def transition_pinned_drift_bootstrap(
    journal: PinnedDriftBootstrapJournal,
    phase: Literal["runtime_activated", "manifest_committing", "committed"],
) -> PinnedDriftBootstrapJournal:
    _validate_journal(journal)
    if journal.phase == "prepared" and phase == "runtime_activated":
        updated = replace(journal, phase=phase, runtime_activated_at=_now())
    elif journal.phase == "runtime_activated" and phase == "manifest_committing":
        updated = replace(journal, phase=phase, manifest_committing_at=_now())
    elif journal.phase == "manifest_committing" and phase == "committed":
        updated = replace(journal, phase=phase, committed_at=_now())
    else:
        raise DeploymentContractError("pinned drift bootstrap journal transition is invalid")
    _validate_journal(updated)
    return updated


def read_pinned_drift_bootstrap(
    path: Path,
    *,
    allow_missing: bool = False,
) -> PinnedDriftBootstrapJournal | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise DeploymentContractError("pinned drift bootstrap journal is missing") from None
    except OSError as exc:
        raise DeploymentContractError("pinned drift bootstrap journal cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > _MAX_BYTES
        ):
            raise DeploymentContractError("pinned drift bootstrap journal is unsafe")
        raw = os.read(descriptor, _MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise DeploymentContractError("pinned drift bootstrap journal is too large")
    except OSError as exc:
        raise DeploymentContractError("pinned drift bootstrap journal cannot be read") from exc
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentContractError("pinned drift bootstrap journal is invalid") from exc
    journal = _journal_from_payload(payload)
    _validate_journal(journal)
    return journal


def write_pinned_drift_bootstrap(
    path: Path,
    journal: PinnedDriftBootstrapJournal,
) -> None:
    _validate_journal(journal)
    ensure_c6c_state_directory(path.parent)
    payload = (json.dumps(asdict(journal), ensure_ascii=False, sort_keys=True) + "\n").encode()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise DeploymentContractError("pinned drift bootstrap journal write failed") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def assert_pinned_drift_bootstrap_allows_pair_mutation(
    values: Mapping[str, str],
) -> None:
    journal = read_pinned_drift_bootstrap(
        pinned_drift_bootstrap_journal_path(values), allow_missing=True
    )
    if journal is not None and journal.phase != "committed":
        raise DeploymentContractError("unfinished pinned drift bootstrap blocks pair mutation")


def assert_pinned_drift_bootstrap_inputs(
    journal: PinnedDriftBootstrapJournal,
    *,
    production_pin_version: int,
    environment_sha256: str,
    compose_sha256: str,
    resolved_compose_sha256: str,
    old_manifest_sha256: str,
    database_heads: Mapping[str, str],
) -> None:
    _validate_journal(journal)
    assert_pinned_drift_bootstrap_frozen_inputs(
        journal,
        production_pin_version=production_pin_version,
        environment_sha256=environment_sha256,
        compose_sha256=compose_sha256,
        resolved_compose_sha256=resolved_compose_sha256,
        database_heads=database_heads,
    )
    if journal.old_manifest_sha256 != old_manifest_sha256:
        raise DeploymentContractError("pinned drift bootstrap journal inputs changed")


def assert_pinned_drift_bootstrap_frozen_inputs(
    journal: PinnedDriftBootstrapJournal,
    *,
    production_pin_version: int,
    environment_sha256: str,
    compose_sha256: str,
    resolved_compose_sha256: str,
    database_heads: Mapping[str, str],
) -> None:
    """manifest 전환 뒤에도 변하지 않아야 하는 transaction evidence를 비교한다."""

    _validate_journal(journal)
    expected = {
        "production_pin_version": production_pin_version,
        "environment_sha256": environment_sha256,
        "compose_sha256": compose_sha256,
        "resolved_compose_sha256": resolved_compose_sha256,
        "database_heads": dict(database_heads),
    }
    actual = {
        "production_pin_version": journal.production_pin_version,
        "environment_sha256": journal.environment_sha256,
        "compose_sha256": journal.compose_sha256,
        "resolved_compose_sha256": journal.resolved_compose_sha256,
        "database_heads": dict(journal.database_heads),
    }
    if actual != expected:
        raise DeploymentContractError("pinned drift bootstrap journal inputs changed")


def _journal_from_payload(payload: object) -> PinnedDriftBootstrapJournal:
    keys = {
        "version",
        "phase",
        "transaction_id",
        "production_pin_version",
        "environment_sha256",
        "compose_sha256",
        "resolved_compose_sha256",
        "old_manifest_sha256",
        "old_active",
        "old_rollback",
        "candidate",
        "database_heads",
        "prepared_at",
        "runtime_activated_at",
        "manifest_committing_at",
        "committed_at",
    }
    if not isinstance(payload, Mapping) or set(payload) != keys:
        raise DeploymentContractError("pinned drift bootstrap journal shape is invalid")
    return PinnedDriftBootstrapJournal(
        version=payload["version"],
        phase=payload["phase"],
        transaction_id=payload["transaction_id"],
        production_pin_version=payload["production_pin_version"],
        environment_sha256=payload["environment_sha256"],
        compose_sha256=payload["compose_sha256"],
        resolved_compose_sha256=payload["resolved_compose_sha256"],
        old_manifest_sha256=payload["old_manifest_sha256"],
        old_active=_pair_from_payload(payload["old_active"]),
        old_rollback=_pair_from_payload(payload["old_rollback"]),
        candidate=_pair_from_payload(payload["candidate"]),
        database_heads=payload["database_heads"],
        prepared_at=payload["prepared_at"],
        runtime_activated_at=payload["runtime_activated_at"],
        manifest_committing_at=payload["manifest_committing_at"],
        committed_at=payload["committed_at"],
    )


def _pair_from_payload(payload: object) -> CompatibleImagePair:
    if not isinstance(payload, Mapping):
        raise DeploymentContractError("pinned drift bootstrap journal pair is invalid")
    try:
        return CompatibleImagePair(**dict(payload))
    except TypeError as exc:
        raise DeploymentContractError("pinned drift bootstrap journal pair is invalid") from exc


def _validate_journal(journal: PinnedDriftBootstrapJournal) -> None:
    if (
        type(journal.version) is not int
        or journal.version != _VERSION
        or journal.phase not in _PHASES
        or not _canonical_uuid(journal.transaction_id)
        or type(journal.production_pin_version) is not int
        or journal.production_pin_version <= 0
        or any(
            not isinstance(value, str) or _SHA256.fullmatch(value) is None
            for value in (
                journal.environment_sha256,
                journal.compose_sha256,
                journal.resolved_compose_sha256,
                journal.old_manifest_sha256,
            )
        )
        or not _timestamp(journal.prepared_at)
        or not _valid_database_heads(journal.database_heads)
    ):
        raise DeploymentContractError("pinned drift bootstrap journal contract is invalid")
    for pair in (journal.old_active, journal.old_rollback, journal.candidate):
        try:
            compatible_pair_manifest_logical_hash(initial_pair_manifest(pair))
        except (TypeError, ValueError) as exc:
            raise DeploymentContractError("pinned drift bootstrap journal pair is invalid") from exc
    if journal.phase == "prepared":
        if (
            journal.runtime_activated_at is not None
            or journal.manifest_committing_at is not None
            or journal.committed_at is not None
        ):
            raise DeploymentContractError("prepared pinned drift bootstrap journal is invalid")
    elif journal.phase == "runtime_activated":
        if (
            not _timestamp(journal.runtime_activated_at)
            or journal.manifest_committing_at is not None
            or journal.committed_at is not None
        ):
            raise DeploymentContractError("activated pinned drift bootstrap journal is invalid")
    elif journal.phase == "manifest_committing":
        if (
            not _timestamp(journal.runtime_activated_at)
            or not _timestamp(journal.manifest_committing_at)
            or journal.committed_at is not None
        ):
            raise DeploymentContractError("manifest-committing pinned drift bootstrap journal is invalid")
    elif (
        not _timestamp(journal.runtime_activated_at)
        or not _timestamp(journal.manifest_committing_at)
        or not _timestamp(journal.committed_at)
    ):
        raise DeploymentContractError("committed pinned drift bootstrap journal is invalid")


def _valid_database_heads(heads: Mapping[str, str]) -> bool:
    return (
        isinstance(heads, Mapping)
        and set(heads) == _DATABASE_ROLES
        and all(
            isinstance(head, str) and _SCHEMA_REVISION.fullmatch(head) is not None
            for head in heads.values()
        )
    )


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
