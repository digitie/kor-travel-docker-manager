"""F1D pinned compatible-pair drift bootstrap의 owner-only durable journal."""

from __future__ import annotations

import hashlib
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
from typing import Literal, cast

from kor_travel_docker_manager.services.c6c_deployment import (
    CompatibleImagePair,
    DeploymentContractError,
    c6c_state_paths,
    compatible_pair_manifest_logical_hash,
    ensure_c6c_state_directory,
    initial_pair_manifest,
)
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
)

_VERSION = 2
_LEGACY_VERSION = 1
_FILENAME_PREFIX = "pinned-drift-bootstrap-v2-"
_LEGACY_FILENAME = "pinned-drift-bootstrap-v1.json"
_HISTORY_DIRECTORY = "pinned-drift-bootstrap-history-v2"
_MAX_BYTES = 16 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_REVISION = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_PHASES = frozenset(
    {"prepared", "runtime_activated", "manifest_committing", "committed"}
)
_DATABASE_ROLES = frozenset({"map_application", "map_dagster", "pinvi"})
_MAX_FAILURE_COUNT = 1_000_000
PINNED_DRIFT_BOOTSTRAP_CHECKPOINTS = frozenset(
    {
        "prepared.stop_pair",
        "prepared.map_api_up",
        "prepared.map_api_provenance",
        "prepared.map_smoke",
        "prepared.map_runtime_dependents_up",
        "prepared.map_runtime_provenance",
        "prepared.pinvi_api_up",
        "prepared.pinvi_api_provenance",
        "prepared.contract.services_ready",
        "prepared.contract.resolved_compose",
        "prepared.contract.image_provenance",
        "prepared.contract.map_smoke",
        "prepared.contract.pinvi_smoke",
        "prepared.contract.ui_auth",
        "prepared.contract.runtime_isolation",
        "prepared.contract.map_env_migration",
        "prepared.database_heads",
        "prepared.runtime_activated_journal",
        "runtime_activated.contract.services_ready",
        "runtime_activated.contract.resolved_compose",
        "runtime_activated.contract.image_provenance",
        "runtime_activated.contract.map_smoke",
        "runtime_activated.contract.pinvi_smoke",
        "runtime_activated.contract.ui_auth",
        "runtime_activated.contract.runtime_isolation",
        "runtime_activated.contract.map_env_migration",
        "runtime_activated.database_heads",
        "runtime_activated.manifest_committing_journal",
        "manifest_committing.contract.services_ready",
        "manifest_committing.contract.resolved_compose",
        "manifest_committing.contract.image_provenance",
        "manifest_committing.contract.map_smoke",
        "manifest_committing.contract.pinvi_smoke",
        "manifest_committing.contract.ui_auth",
        "manifest_committing.contract.runtime_isolation",
        "manifest_committing.contract.map_env_migration",
        "manifest_committing.database_heads",
        "manifest_committing.manifest_write",
        "manifest_committing.committed_journal",
    }
)


@dataclass(frozen=True)
class PinnedDriftBootstrapJournal:
    version: int
    phase: Literal[
        "prepared", "runtime_activated", "manifest_committing", "committed"
    ]
    transaction_id: str
    production_pin_version: int
    pinset_sha256: str
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
    attempt_checkpoint: str | None = None
    last_failure_checkpoint: str | None = None
    last_failed_at: str | None = None
    failure_count: int = 0


def pinned_drift_bootstrap_journal_path(
    values: Mapping[str, str], *, pinset_sha256: str | None = None
) -> Path:
    """현재 exact pinset에만 결합되는 F1D v2 journal 경로를 계산한다."""

    manifest_path, _ = c6c_state_paths(values)
    pinset = CACHE_TARGET_PRODUCTION_PINS.pinset_sha256 if pinset_sha256 is None else pinset_sha256
    if _SHA256.fullmatch(pinset) is None:
        raise DeploymentContractError("pinned drift bootstrap pinset digest is invalid")
    return Path(manifest_path).with_name(f"{_FILENAME_PREFIX}{pinset}.json")


def archive_terminal_pinned_drift_bootstrap(
    values: Mapping[str, str], *, pinset_sha256: str
) -> Path | None:
    """terminal F1D v2 receipt를 pinset별 immutable history로 receipt-first archive한다."""

    journal_path = pinned_drift_bootstrap_journal_path(
        values, pinset_sha256=pinset_sha256
    )
    journal = read_pinned_drift_bootstrap(journal_path, allow_missing=True)
    if journal is None:
        return _read_archived_terminal_pinned_drift_bootstrap(
            journal_path=journal_path,
            pinset_sha256=pinset_sha256,
        )
    if journal.pinset_sha256 != pinset_sha256 or journal.phase != "committed":
        raise DeploymentContractError(
            "unfinished or foreign pinned drift bootstrap blocks input rotation"
        )
    raw = _read_journal_bytes(journal_path)
    return _archive_journal_bytes(
        journal_path=journal_path,
        raw=raw,
        pinset_sha256=pinset_sha256,
    )


def _read_archived_terminal_pinned_drift_bootstrap(
    *, journal_path: Path, pinset_sha256: str
) -> Path | None:
    history_directory = journal_path.parent / _HISTORY_DIRECTORY / pinset_sha256
    try:
        metadata = history_directory.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DeploymentContractError("pinned drift bootstrap history is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise DeploymentContractError("pinned drift bootstrap history is unsafe")
    try:
        entries = tuple(history_directory.iterdir())
    except OSError as exc:
        raise DeploymentContractError("pinned drift bootstrap history is unavailable") from exc
    if any(path.suffix != ".json" for path in entries):
        raise DeploymentContractError("pinned drift bootstrap history is unsafe")
    if len(entries) > 1:
        raise DeploymentContractError("pinned drift bootstrap history is ambiguous")
    if not entries:
        return None
    archived = entries[0]
    journal = read_pinned_drift_bootstrap(archived)
    if (
        journal is None
        or journal.pinset_sha256 != pinset_sha256
        or journal.phase != "committed"
    ):
        raise DeploymentContractError("pinned drift bootstrap history receipt is invalid")
    raw = _read_journal_bytes(archived)
    if archived.stem != hashlib.sha256(raw).hexdigest():
        raise DeploymentContractError("pinned drift bootstrap history digest is invalid")
    return archived


def archive_terminal_legacy_pinned_drift_bootstrap(
    values: Mapping[str, str],
) -> Path | None:
    """v1 F1D receipt만 copy+verify+unlink 순서로 history로 옮긴다.

    nonterminal v1 residue는 writer-drain 복구 여부를 증명할 수 없으므로 v2 input
    rotation을 fail-close한다. 파일이 없는 경우는 F1D journal을 만들기 전
    preflight에서 중단된 predecessor이므로 허용한다.
    """

    manifest_path, _ = c6c_state_paths(values)
    legacy_path = Path(manifest_path).with_name(_LEGACY_FILENAME)
    try:
        raw = _read_journal_bytes(legacy_path)
    except FileNotFoundError:
        return None
    payload = _parse_legacy_v1_payload(raw)
    if payload["phase"] != "committed":
        raise DeploymentContractError(
            "unfinished legacy pinned drift bootstrap blocks v2 input rotation"
        )
    return _archive_journal_bytes(
        journal_path=legacy_path,
        raw=raw,
        pinset_sha256="legacy-v1",
    )


def _archive_journal_bytes(
    *, journal_path: Path, raw: bytes, pinset_sha256: str
) -> Path:
    digest = hashlib.sha256(raw).hexdigest()
    history_directory = journal_path.parent / _HISTORY_DIRECTORY / pinset_sha256
    ensure_c6c_state_directory(history_directory)
    archive_path = history_directory / f"{digest}.json"
    archive_exists = False
    try:
        archive_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise DeploymentContractError("legacy pinned drift archive cannot be inspected") from exc
    else:
        if _read_journal_bytes(archive_path) != raw:
            raise DeploymentContractError("legacy pinned drift archive already exists")
        archive_exists = True
    temporary: Path | None = None
    try:
        if not archive_exists:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=history_directory,
                prefix=f".{archive_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, archive_path)
            temporary = None
            if _read_journal_bytes(archive_path) != raw:
                raise DeploymentContractError("legacy pinned drift archive verification failed")
        if _read_journal_bytes(journal_path) != raw:
            raise DeploymentContractError("legacy pinned drift journal changed before archive")
        journal_path.unlink()
        _fsync_directory(history_directory)
        _fsync_directory(journal_path.parent)
    except DeploymentContractError:
        raise
    except OSError as exc:
        raise DeploymentContractError("legacy pinned drift archive failed") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return archive_path


def prepare_pinned_drift_bootstrap(
    *,
    production_pin_version: int,
    pinset_sha256: str | None = None,
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
        pinset_sha256=(
            CACHE_TARGET_PRODUCTION_PINS.pinset_sha256
            if pinset_sha256 is None
            else pinset_sha256
        ),
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


def record_pinned_drift_bootstrap_attempt(
    journal: PinnedDriftBootstrapJournal,
    checkpoint: str,
) -> PinnedDriftBootstrapJournal:
    """side effect 전 closed checkpoint를 durable journal에 기록할 값을 만든다."""

    _validate_journal(journal)
    _validate_checkpoint(checkpoint)
    updated = replace(journal, attempt_checkpoint=checkpoint)
    _validate_journal(updated)
    return updated


def record_pinned_drift_bootstrap_failure(
    journal: PinnedDriftBootstrapJournal,
    checkpoint: str,
) -> PinnedDriftBootstrapJournal:
    """비밀값 없는 failure evidence를 현재 checkpoint에 결박한다."""

    _validate_journal(journal)
    _validate_checkpoint(checkpoint)
    if journal.failure_count >= _MAX_FAILURE_COUNT:
        raise DeploymentContractError("pinned drift bootstrap failure count is exhausted")
    updated = replace(
        journal,
        attempt_checkpoint=checkpoint,
        last_failure_checkpoint=checkpoint,
        last_failed_at=_now(),
        failure_count=journal.failure_count + 1,
    )
    _validate_journal(updated)
    return updated


def read_pinned_drift_bootstrap(
    path: Path,
    *,
    allow_missing: bool = False,
) -> PinnedDriftBootstrapJournal | None:
    try:
        raw = _read_journal_bytes(path)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise DeploymentContractError("pinned drift bootstrap journal is missing") from None
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
    pinset_sha256: str | None = None,
    environment_sha256: str,
    compose_sha256: str,
    resolved_compose_sha256: str,
    database_heads: Mapping[str, str],
) -> None:
    """manifest 전환 뒤에도 변하지 않아야 하는 transaction evidence를 비교한다."""

    _validate_journal(journal)
    expected = {
        "production_pin_version": production_pin_version,
        "pinset_sha256": (
            CACHE_TARGET_PRODUCTION_PINS.pinset_sha256
            if pinset_sha256 is None
            else pinset_sha256
        ),
        "environment_sha256": environment_sha256,
        "compose_sha256": compose_sha256,
        "resolved_compose_sha256": resolved_compose_sha256,
        "database_heads": dict(database_heads),
    }
    actual = {
        "production_pin_version": journal.production_pin_version,
        "pinset_sha256": journal.pinset_sha256,
        "environment_sha256": journal.environment_sha256,
        "compose_sha256": journal.compose_sha256,
        "resolved_compose_sha256": journal.resolved_compose_sha256,
        "database_heads": dict(journal.database_heads),
    }
    if actual != expected:
        raise DeploymentContractError("pinned drift bootstrap journal inputs changed")


def _read_journal_bytes(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise
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
        return raw
    except OSError as exc:
        raise DeploymentContractError("pinned drift bootstrap journal cannot be read") from exc
    finally:
        os.close(descriptor)


def _parse_legacy_v1_payload(raw: bytes) -> Mapping[str, object]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentContractError("legacy pinned drift bootstrap journal is invalid") from exc
    fields = {
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
    if (
        not isinstance(payload, Mapping)
        or set(payload) != fields
        or payload.get("version") != _LEGACY_VERSION
        or payload.get("phase") not in _PHASES
        or not _canonical_uuid(payload.get("transaction_id"))
        or not isinstance(payload.get("production_pin_version"), int)
        or payload["production_pin_version"] <= 0
        or not all(
            isinstance(payload.get(key), str) and _SHA256.fullmatch(payload[key]) is not None
            for key in (
                "environment_sha256",
                "compose_sha256",
                "resolved_compose_sha256",
                "old_manifest_sha256",
            )
        )
        or not _timestamp(payload.get("prepared_at"))
        or not _timestamp(payload.get("committed_at"))
    ):
        raise DeploymentContractError("legacy pinned drift bootstrap journal is invalid")
    try:
        _validate_journal(
            PinnedDriftBootstrapJournal(
                version=_VERSION,
                phase=payload["phase"],
                transaction_id=payload["transaction_id"],
                production_pin_version=payload["production_pin_version"],
                pinset_sha256="0" * 64,
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
        )
    except (TypeError, ValueError, DeploymentContractError) as exc:
        raise DeploymentContractError("legacy pinned drift bootstrap journal is invalid") from exc
    return payload


def _journal_from_payload(payload: object) -> PinnedDriftBootstrapJournal:
    base_keys = {
        "version",
        "phase",
        "transaction_id",
        "production_pin_version",
        "pinset_sha256",
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
    diagnostic_keys = {
        "attempt_checkpoint",
        "last_failure_checkpoint",
        "last_failed_at",
        "failure_count",
    }
    if not isinstance(payload, Mapping):
        raise DeploymentContractError("pinned drift bootstrap journal shape is invalid")
    keys = set(payload)
    if keys == base_keys:
        diagnostics: Mapping[str, object] = {
            "attempt_checkpoint": None,
            "last_failure_checkpoint": None,
            "last_failed_at": None,
            "failure_count": 0,
        }
    elif keys == base_keys | diagnostic_keys:
        diagnostics = payload
    else:
        raise DeploymentContractError("pinned drift bootstrap journal shape is invalid")
    return PinnedDriftBootstrapJournal(
        version=payload["version"],
        phase=payload["phase"],
        transaction_id=payload["transaction_id"],
        production_pin_version=payload["production_pin_version"],
        pinset_sha256=payload["pinset_sha256"],
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
        attempt_checkpoint=cast(str | None, diagnostics["attempt_checkpoint"]),
        last_failure_checkpoint=cast(
            str | None, diagnostics["last_failure_checkpoint"]
        ),
        last_failed_at=cast(str | None, diagnostics["last_failed_at"]),
        failure_count=cast(int, diagnostics["failure_count"]),
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
        or _SHA256.fullmatch(journal.pinset_sha256) is None
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
    if journal.attempt_checkpoint is not None:
        _validate_checkpoint(journal.attempt_checkpoint)
    failure_is_empty = (
        journal.last_failure_checkpoint is None
        and journal.last_failed_at is None
        and type(journal.failure_count) is int
        and journal.failure_count == 0
    )
    failure_is_complete = (
        isinstance(journal.last_failure_checkpoint, str)
        and journal.last_failure_checkpoint in PINNED_DRIFT_BOOTSTRAP_CHECKPOINTS
        and _timestamp(journal.last_failed_at)
        and type(journal.failure_count) is int
        and 0 < journal.failure_count <= _MAX_FAILURE_COUNT
    )
    if not (failure_is_empty or failure_is_complete):
        raise DeploymentContractError("pinned drift bootstrap failure evidence is invalid")
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


def _validate_checkpoint(checkpoint: object) -> None:
    if (
        not isinstance(checkpoint, str)
        or checkpoint not in PINNED_DRIFT_BOOTSTRAP_CHECKPOINTS
    ):
        raise DeploymentContractError("pinned drift bootstrap checkpoint is invalid")


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
