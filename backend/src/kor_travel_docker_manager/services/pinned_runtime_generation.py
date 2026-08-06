"""F1D v5 pinned runtime generation의 typed state와 durable manifest.

이 모듈은 legacy compatible-pair/rollback model을 읽지 않는다. candidate image와
schema contract를 database reset 전에 고정하고, 한 active generation만 기록한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

DeploymentEnvironment = Literal["local", "rehearsal", "production"]
DeploymentLifecycle = Literal["development", "rebuildable", "operational"]
RuntimeService = Literal[
    "kor-travel-map-api",
    "kor-travel-map-ui",
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
    "pinvi-api",
    "pinvi-web",
    "pinvi-dagster",
]
SchemaRole = Literal["map_application", "map_dagster", "pinvi"]
RebuildPhase = Literal[
    "candidate_attested",
    "reset_intent_durable",
    "databases_recreated",
    "map_application_ready",
    "map_dagster_ready",
    "map_runtime_ready",
    "pinvi_schema_ready",
    "pinvi_api_ready",
    "pinvi_runtime_ready",
    "contract_verified",
    "manifest_committing",
    "committed",
]

RUNTIME_SERVICES: tuple[RuntimeService, ...] = (
    "kor-travel-map-api",
    "kor-travel-map-ui",
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
    "pinvi-api",
    "pinvi-web",
    "pinvi-dagster",
)
SCHEMA_ROLES: tuple[SchemaRole, ...] = (
    "map_application",
    "map_dagster",
    "pinvi",
)
REBUILD_PHASES: tuple[RebuildPhase, ...] = (
    "candidate_attested",
    "reset_intent_durable",
    "databases_recreated",
    "map_application_ready",
    "map_dagster_ready",
    "map_runtime_ready",
    "pinvi_schema_ready",
    "pinvi_api_ready",
    "pinvi_runtime_ready",
    "contract_verified",
    "manifest_committing",
    "committed",
)

_LIFECYCLE_PAIRS: dict[tuple[str, str], tuple[str, str]] = {
    ("local", "development"): ("development", "false"),
    ("rehearsal", "rebuildable"): ("production", "true"),
    ("production", "operational"): ("production", "true"),
}
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_HEAD = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_MAX_STATE_BYTES = 64 * 1024
_MANIFEST_VERSION = 5
_TOMBSTONE_VERSION = 5
_F1D_LEGACY_ARTIFACTS: tuple[str, ...] = (
    "compatible-pair-v2.json",
    "compatible-pair-v3.json",
    "compatible-pair-v4.json",
    "map-production-env-migration-v1.json",
    "cache-target-window-v1.json",
    "cache-target-diagnostic-v1.json",
    "cache-target-diagnostic-attempts-v1.json",
)
_TOMBSTONE_DIRECTORY = "pinned-runtime-v5"
_TOMBSTONE_FILENAME = "legacy-tombstone-v5.json"


@dataclass(frozen=True)
class DeploymentMode:
    """frozen canonical environment이 허용하는 유일한 lifecycle pair."""

    environment: DeploymentEnvironment
    lifecycle: DeploymentLifecycle
    pinvi_environment: str
    map_ops_principal_required: bool

    @property
    def rebuildable(self) -> bool:
        return self.lifecycle == "rebuildable"


def load_deployment_mode(values: Mapping[str, str]) -> DeploymentMode:
    """환경·lifecycle·PinVi/Map security scalar를 함께 fail-close 검증한다."""

    environment = values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
    lifecycle = values.get("KTDM_DEPLOYMENT_LIFECYCLE", "").strip().lower()
    expected = _LIFECYCLE_PAIRS.get((environment, lifecycle))
    if expected is None:
        raise DeploymentContractError("deployment environment/lifecycle pair is invalid")
    expected_pinvi, expected_map_required = expected
    pinvi_environment = values.get("PINVI_ENVIRONMENT", "").strip().lower()
    if pinvi_environment != expected_pinvi:
        raise DeploymentContractError("deployment lifecycle and PINVI_ENVIRONMENT differ")
    required = values.get("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "").strip().lower()
    if required != expected_map_required:
        raise DeploymentContractError(
            "deployment lifecycle and Map ops-principal requirement differ"
        )
    return DeploymentMode(
        environment=cast(DeploymentEnvironment, environment),
        lifecycle=cast(DeploymentLifecycle, lifecycle),
        pinvi_environment=pinvi_environment,
        map_ops_principal_required=required == "true",
    )


def require_rebuildable_mode(values: Mapping[str, str]) -> DeploymentMode:
    """파기형 rebuild entrypoint에서만 호출하는 explicit authorization."""

    mode = load_deployment_mode(values)
    if not mode.rebuildable:
        raise DeploymentContractError("pinned runtime rebuild requires rehearsal/rebuildable")
    return mode


@dataclass(frozen=True)
class PinnedRuntimeGeneration:
    """Map 4개와 PinVi 3개 runtime을 같이 고정하는 immutable candidate/active 세대."""

    map_api_image_id: str
    map_ui_image_id: str
    map_dagster_image_id: str
    map_dagster_daemon_image_id: str
    pinvi_api_image_id: str
    pinvi_web_image_id: str
    pinvi_dagster_image_id: str
    map_source_revision: str
    pinvi_source_revision: str
    map_application_head: str
    map_dagster_head: str
    pinvi_head: str
    pinset_sha256: str
    recorded_at: str

    def __post_init__(self) -> None:
        for image_id in self.image_ids.values():
            if _IMAGE_ID.fullmatch(image_id) is None:
                raise DeploymentContractError("pinned runtime generation image ID is invalid")
        if _REVISION.fullmatch(self.map_source_revision) is None:
            raise DeploymentContractError("pinned runtime generation Map revision is invalid")
        if _REVISION.fullmatch(self.pinvi_source_revision) is None:
            raise DeploymentContractError("pinned runtime generation PinVi revision is invalid")
        for schema_head in self.schema_heads.values():
            if _SCHEMA_HEAD.fullmatch(schema_head) is None:
                raise DeploymentContractError("pinned runtime generation schema head is invalid")
        if _SHA256.fullmatch(self.pinset_sha256) is None:
            raise DeploymentContractError("pinned runtime generation pinset digest is invalid")
        _validate_utc_timestamp(self.recorded_at, "pinned runtime generation timestamp")

    @property
    def image_ids(self) -> Mapping[RuntimeService, str]:
        return {
            "kor-travel-map-api": self.map_api_image_id,
            "kor-travel-map-ui": self.map_ui_image_id,
            "kor-travel-map-dagster": self.map_dagster_image_id,
            "kor-travel-map-dagster-daemon": self.map_dagster_daemon_image_id,
            "pinvi-api": self.pinvi_api_image_id,
            "pinvi-web": self.pinvi_web_image_id,
            "pinvi-dagster": self.pinvi_dagster_image_id,
        }

    @property
    def schema_heads(self) -> Mapping[SchemaRole, str]:
        return {
            "map_application": self.map_application_head,
            "map_dagster": self.map_dagster_head,
            "pinvi": self.pinvi_head,
        }

    def to_payload(self) -> dict[str, str]:
        return {
            "map_api_image_id": self.map_api_image_id,
            "map_ui_image_id": self.map_ui_image_id,
            "map_dagster_image_id": self.map_dagster_image_id,
            "map_dagster_daemon_image_id": self.map_dagster_daemon_image_id,
            "pinvi_api_image_id": self.pinvi_api_image_id,
            "pinvi_web_image_id": self.pinvi_web_image_id,
            "pinvi_dagster_image_id": self.pinvi_dagster_image_id,
            "map_source_revision": self.map_source_revision,
            "pinvi_source_revision": self.pinvi_source_revision,
            "map_application_head": self.map_application_head,
            "map_dagster_head": self.map_dagster_head,
            "pinvi_head": self.pinvi_head,
            "pinset_sha256": self.pinset_sha256,
            "recorded_at": self.recorded_at,
        }


def generation_logical_sha256(generation: PinnedRuntimeGeneration) -> str:
    """시간 기록을 제외한 immutable generation identity를 계산한다."""

    payload = generation.to_payload()
    payload.pop("recorded_at")
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PinnedRuntimeManifest:
    """v5는 DB preimage가 없는 rollback slot을 보관하지 않는다."""

    version: Literal[5]
    active_generation: PinnedRuntimeGeneration

    def __post_init__(self) -> None:
        if self.version != _MANIFEST_VERSION:
            raise DeploymentContractError("pinned runtime manifest version is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "active_generation": self.active_generation.to_payload(),
        }


@dataclass(frozen=True)
class PinnedRuntimeRebuildJournal:
    """candidate image 보존부터 v5 manifest commit까지의 same-pinset resume receipt."""

    version: Literal[5]
    transaction_id: str
    phase: RebuildPhase
    candidate: PinnedRuntimeGeneration
    environment_sha256: str
    compose_sha256: str
    resolved_compose_sha256: str
    created_at: str

    def __post_init__(self) -> None:
        if self.version != _MANIFEST_VERSION:
            raise DeploymentContractError("pinned runtime rebuild journal version is invalid")
        try:
            canonical = str(uuid.UUID(self.transaction_id))
        except ValueError as exc:
            raise DeploymentContractError("pinned runtime rebuild transaction ID is invalid") from exc
        if canonical != self.transaction_id:
            raise DeploymentContractError("pinned runtime rebuild transaction ID is not canonical")
        if self.phase not in REBUILD_PHASES:
            raise DeploymentContractError("pinned runtime rebuild phase is invalid")
        for digest in (
            self.environment_sha256,
            self.compose_sha256,
            self.resolved_compose_sha256,
        ):
            if _SHA256.fullmatch(digest) is None:
                raise DeploymentContractError("pinned runtime rebuild input digest is invalid")
        _validate_utc_timestamp(self.created_at, "pinned runtime rebuild timestamp")

    def transition(self, phase: RebuildPhase) -> PinnedRuntimeRebuildJournal:
        current_index = REBUILD_PHASES.index(self.phase)
        if current_index == len(REBUILD_PHASES) - 1 or REBUILD_PHASES[current_index + 1] != phase:
            raise DeploymentContractError("pinned runtime rebuild phase transition is invalid")
        return PinnedRuntimeRebuildJournal(
            version=5,
            transaction_id=self.transaction_id,
            phase=phase,
            candidate=self.candidate,
            environment_sha256=self.environment_sha256,
            compose_sha256=self.compose_sha256,
            resolved_compose_sha256=self.resolved_compose_sha256,
            created_at=self.created_at,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "transaction_id": self.transaction_id,
            "phase": self.phase,
            "candidate": self.candidate.to_payload(),
            "environment_sha256": self.environment_sha256,
            "compose_sha256": self.compose_sha256,
            "resolved_compose_sha256": self.resolved_compose_sha256,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class LegacyTombstoneEntry:
    """삭제 전 fsync한 legacy artifact의 path·content evidence."""

    relative_path: str
    sha256: str

    def __post_init__(self) -> None:
        if self.relative_path not in _F1D_LEGACY_ARTIFACTS:
            raise DeploymentContractError("legacy tombstone path is invalid")
        if _SHA256.fullmatch(self.sha256) is None:
            raise DeploymentContractError("legacy tombstone digest is invalid")

    def to_payload(self) -> dict[str, str]:
        return {"relative_path": self.relative_path, "sha256": self.sha256}


@dataclass(frozen=True)
class LegacyTombstoneReceipt:
    """candidate-attested 뒤에만 쓰는 v5 legacy state 퇴역 receipt."""

    version: Literal[5]
    transaction_id: str
    candidate_generation_sha256: str
    requested_paths: tuple[str, ...]
    retired: tuple[LegacyTombstoneEntry, ...]
    recorded_at: str

    def __post_init__(self) -> None:
        if self.version != _TOMBSTONE_VERSION:
            raise DeploymentContractError("legacy tombstone version is invalid")
        try:
            canonical = str(uuid.UUID(self.transaction_id))
        except ValueError as exc:
            raise DeploymentContractError("legacy tombstone transaction ID is invalid") from exc
        if canonical != self.transaction_id:
            raise DeploymentContractError("legacy tombstone transaction ID is not canonical")
        if _SHA256.fullmatch(self.candidate_generation_sha256) is None:
            raise DeploymentContractError("legacy tombstone generation digest is invalid")
        if (
            not self.requested_paths
            or tuple(sorted(self.requested_paths)) != self.requested_paths
            or len(set(self.requested_paths)) != len(self.requested_paths)
            or any(path not in _F1D_LEGACY_ARTIFACTS for path in self.requested_paths)
        ):
            raise DeploymentContractError("legacy tombstone requested paths are invalid")
        if (
            tuple(sorted(entry.relative_path for entry in self.retired))
            != tuple(entry.relative_path for entry in self.retired)
            or len({entry.relative_path for entry in self.retired}) != len(self.retired)
            or any(entry.relative_path not in self.requested_paths for entry in self.retired)
        ):
            raise DeploymentContractError("legacy tombstone retired paths are invalid")
        _validate_utc_timestamp(self.recorded_at, "legacy tombstone timestamp")

    def to_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "transaction_id": self.transaction_id,
            "candidate_generation_sha256": self.candidate_generation_sha256,
            "requested_paths": list(self.requested_paths),
            "retired": [entry.to_payload() for entry in self.retired],
            "recorded_at": self.recorded_at,
        }


def generation_from_payload(payload: object) -> PinnedRuntimeGeneration:
    if not isinstance(payload, Mapping):
        raise DeploymentContractError("pinned runtime generation payload is invalid")
    expected = {
        "map_api_image_id",
        "map_ui_image_id",
        "map_dagster_image_id",
        "map_dagster_daemon_image_id",
        "pinvi_api_image_id",
        "pinvi_web_image_id",
        "pinvi_dagster_image_id",
        "map_source_revision",
        "pinvi_source_revision",
        "map_application_head",
        "map_dagster_head",
        "pinvi_head",
        "pinset_sha256",
        "recorded_at",
    }
    if set(payload) != expected or any(not isinstance(value, str) for value in payload.values()):
        raise DeploymentContractError("pinned runtime generation payload is invalid")
    values = cast(Mapping[str, str], payload)
    return PinnedRuntimeGeneration(**dict(values))


def manifest_from_payload(payload: object) -> PinnedRuntimeManifest:
    if not isinstance(payload, Mapping) or set(payload) != {"version", "active_generation"}:
        raise DeploymentContractError("pinned runtime manifest payload is invalid")
    version = payload.get("version")
    if type(version) is not int or version != _MANIFEST_VERSION:
        raise DeploymentContractError("pinned runtime manifest payload is invalid")
    return PinnedRuntimeManifest(
        version=5,
        active_generation=generation_from_payload(payload.get("active_generation")),
    )


def journal_from_payload(payload: object) -> PinnedRuntimeRebuildJournal:
    expected = {
        "version",
        "transaction_id",
        "phase",
        "candidate",
        "environment_sha256",
        "compose_sha256",
        "resolved_compose_sha256",
        "created_at",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DeploymentContractError("pinned runtime rebuild journal payload is invalid")
    version = payload.get("version")
    transaction_id = payload.get("transaction_id")
    phase = payload.get("phase")
    environment_sha256 = payload.get("environment_sha256")
    compose_sha256 = payload.get("compose_sha256")
    resolved_compose_sha256 = payload.get("resolved_compose_sha256")
    created_at = payload.get("created_at")
    if (
        type(version) is not int
        or not all(
            isinstance(value, str)
            for value in (
                transaction_id,
                phase,
                environment_sha256,
                compose_sha256,
                resolved_compose_sha256,
                created_at,
            )
        )
        or phase not in REBUILD_PHASES
    ):
        raise DeploymentContractError("pinned runtime rebuild journal payload is invalid")
    return PinnedRuntimeRebuildJournal(
        version=5,
        transaction_id=cast(str, transaction_id),
        phase=cast(RebuildPhase, phase),
        candidate=generation_from_payload(payload.get("candidate")),
        environment_sha256=cast(str, environment_sha256),
        compose_sha256=cast(str, compose_sha256),
        resolved_compose_sha256=cast(str, resolved_compose_sha256),
        created_at=cast(str, created_at),
    )


def read_manifest(path: Path) -> PinnedRuntimeManifest:
    return manifest_from_payload(_read_private_json(path, "pinned runtime manifest"))


def write_manifest(path: Path, manifest: PinnedRuntimeManifest) -> None:
    _write_private_json(path, manifest.to_payload(), "pinned runtime manifest")


def read_rebuild_journal(path: Path) -> PinnedRuntimeRebuildJournal:
    return journal_from_payload(_read_private_json(path, "pinned runtime rebuild journal"))


def write_rebuild_journal(path: Path, journal: PinnedRuntimeRebuildJournal) -> None:
    _write_private_json(path, journal.to_payload(), "pinned runtime rebuild journal")


def f1d_legacy_artifact_paths() -> tuple[str, ...]:
    """C2 preflight가 퇴역할 수 있는 유일한 historical state file 이름."""

    return tuple(sorted(_F1D_LEGACY_ARTIFACTS))


def legacy_tombstone_receipt_path(state_root: Path) -> Path:
    """v5 receipt는 legacy file과 분리된 manager-owned directory에만 기록한다."""

    return state_root / _TOMBSTONE_DIRECTORY / _TOMBSTONE_FILENAME


def retire_f1d_legacy_artifacts(
    *,
    state_root: Path,
    transaction_id: str,
    candidate: PinnedRuntimeGeneration,
    requested_paths: tuple[str, ...] = tuple(sorted(_F1D_LEGACY_ARTIFACTS)),
    recorded_at: str,
) -> LegacyTombstoneReceipt:
    """candidate attest 뒤 legacy state를 receipt-first·fail-close로 퇴역한다.

    호출자는 candidate journal을 이미 fsync한 뒤에만 이 함수를 실행해야 한다. 이 함수는
    Docker/Compose/DB를 건드리지 않으며, receipt가 durable해진 뒤에만 allowlist file을 unlink한다.
    """

    _validate_state_root(state_root)
    normalized_paths = _normalize_legacy_paths(requested_paths)
    receipt_path = legacy_tombstone_receipt_path(state_root)
    expected_generation_sha256 = generation_logical_sha256(candidate)
    try:
        receipt_path.lstat()
        receipt_exists = True
    except FileNotFoundError:
        receipt_exists = False
    if receipt_exists:
        receipt = _legacy_tombstone_receipt_from_payload(
            _read_private_json(receipt_path, "legacy tombstone receipt")
        )
        if (
            receipt.transaction_id != transaction_id
            or receipt.candidate_generation_sha256 != expected_generation_sha256
            or receipt.requested_paths != normalized_paths
        ):
            raise DeploymentContractError("legacy tombstone receipt differs from candidate")
        _unlink_receipted_legacy_artifacts(state_root=state_root, receipt=receipt)
        return receipt

    entries = tuple(
        entry
        for entry in (
            _read_legacy_tombstone_entry(state_root, relative_path)
            for relative_path in normalized_paths
        )
        if entry is not None
    )
    receipt = LegacyTombstoneReceipt(
        version=5,
        transaction_id=transaction_id,
        candidate_generation_sha256=expected_generation_sha256,
        requested_paths=normalized_paths,
        retired=entries,
        recorded_at=recorded_at,
    )
    _write_private_json(receipt_path, receipt.to_payload(), "legacy tombstone receipt")
    _unlink_receipted_legacy_artifacts(state_root=state_root, receipt=receipt)
    return receipt


def _validate_utc_timestamp(value: str, label: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DeploymentContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise DeploymentContractError(f"{label} is invalid")


def _normalize_legacy_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    if not paths:
        raise DeploymentContractError("legacy tombstone requested paths are invalid")
    normalized = tuple(sorted(paths))
    if (
        normalized != paths
        or len(set(paths)) != len(paths)
        or any(path not in _F1D_LEGACY_ARTIFACTS for path in paths)
    ):
        raise DeploymentContractError("legacy tombstone requested paths are invalid")
    return normalized


def _legacy_tombstone_receipt_from_payload(payload: object) -> LegacyTombstoneReceipt:
    expected = {
        "version",
        "transaction_id",
        "candidate_generation_sha256",
        "requested_paths",
        "retired",
        "recorded_at",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DeploymentContractError("legacy tombstone receipt is invalid")
    version = payload.get("version")
    transaction_id = payload.get("transaction_id")
    candidate_generation_sha256 = payload.get("candidate_generation_sha256")
    requested_paths = payload.get("requested_paths")
    retired = payload.get("retired")
    recorded_at = payload.get("recorded_at")
    if (
        type(version) is not int
        or not isinstance(transaction_id, str)
        or not isinstance(candidate_generation_sha256, str)
        or not isinstance(requested_paths, list)
        or not all(isinstance(path, str) for path in requested_paths)
        or not isinstance(retired, list)
        or not isinstance(recorded_at, str)
    ):
        raise DeploymentContractError("legacy tombstone receipt is invalid")
    entries: list[LegacyTombstoneEntry] = []
    for entry in retired:
        if (
            not isinstance(entry, Mapping)
            or set(entry) != {"relative_path", "sha256"}
            or not isinstance(entry.get("relative_path"), str)
            or not isinstance(entry.get("sha256"), str)
        ):
            raise DeploymentContractError("legacy tombstone receipt is invalid")
        entries.append(
            LegacyTombstoneEntry(
                relative_path=cast(str, entry["relative_path"]),
                sha256=cast(str, entry["sha256"]),
            )
        )
    return LegacyTombstoneReceipt(
        version=5,
        transaction_id=transaction_id,
        candidate_generation_sha256=candidate_generation_sha256,
        requested_paths=tuple(requested_paths),
        retired=tuple(entries),
        recorded_at=recorded_at,
    )


def _validate_state_root(state_root: Path) -> None:
    try:
        state = state_root.lstat()
    except FileNotFoundError as exc:
        raise DeploymentContractError("legacy tombstone state root is missing") from exc
    if (
        not stat.S_ISDIR(state.st_mode)
        or state.st_uid != os.geteuid()
        or stat.S_IMODE(state.st_mode) != 0o700
    ):
        raise DeploymentContractError("legacy tombstone state root is unsafe")


def _read_legacy_tombstone_entry(
    state_root: Path, relative_path: str
) -> LegacyTombstoneEntry | None:
    artifact = state_root / relative_path
    try:
        before = artifact.lstat()
    except FileNotFoundError:
        return None
    _validate_private_file_stat(before, "legacy tombstone artifact")
    parent_descriptor = _open_legacy_parent(state_root, artifact.parent)
    try:
        descriptor = _open_relative_no_follow(
            parent_descriptor, artifact.name, "legacy tombstone artifact"
        )
        try:
            after = os.fstat(descriptor)
            _validate_private_file_stat(after, "legacy tombstone artifact")
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise DeploymentContractError("legacy tombstone artifact changed during read")
            raw = _read_bounded(descriptor, "legacy tombstone artifact")
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)
    return LegacyTombstoneEntry(
        relative_path=relative_path,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _unlink_receipted_legacy_artifacts(
    *, state_root: Path, receipt: LegacyTombstoneReceipt
) -> None:
    expected = {entry.relative_path: entry.sha256 for entry in receipt.retired}
    for relative_path in receipt.requested_paths:
        artifact = state_root / relative_path
        if relative_path not in expected:
            try:
                artifact.lstat()
                artifact_exists = True
            except FileNotFoundError:
                artifact_exists = False
            if artifact_exists:
                raise DeploymentContractError("legacy tombstone receipt conflicts with artifact")
            continue
        try:
            before = artifact.lstat()
        except FileNotFoundError:
            continue
        _validate_private_file_stat(before, "legacy tombstone artifact")
        parent_descriptor = _open_legacy_parent(state_root, artifact.parent)
        try:
            descriptor = _open_relative_no_follow(
                parent_descriptor, artifact.name, "legacy tombstone artifact"
            )
            try:
                after = os.fstat(descriptor)
                _validate_private_file_stat(after, "legacy tombstone artifact")
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise DeploymentContractError(
                        "legacy tombstone artifact changed during unlink"
                    )
                raw = _read_bounded(descriptor, "legacy tombstone artifact")
                if hashlib.sha256(raw).hexdigest() != expected[relative_path]:
                    raise DeploymentContractError("legacy tombstone artifact content differs")
            finally:
                os.close(descriptor)
            os.unlink(artifact.name, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
        except OSError as exc:
            raise DeploymentContractError("legacy tombstone artifact cannot be removed") from exc
        finally:
            os.close(parent_descriptor)


def _open_legacy_parent(state_root: Path, parent: Path) -> int:
    try:
        relative_parent = parent.relative_to(state_root)
    except ValueError as exc:
        raise DeploymentContractError("legacy tombstone path escapes state root") from exc
    cursor = state_root
    for part in relative_parent.parts:
        cursor = cursor / part
        try:
            cursor_stat = cursor.lstat()
        except FileNotFoundError as exc:
            raise DeploymentContractError("legacy tombstone parent is missing") from exc
        if (
            not stat.S_ISDIR(cursor_stat.st_mode)
            or cursor_stat.st_uid != os.geteuid()
            or stat.S_IMODE(cursor_stat.st_mode) != 0o700
        ):
            raise DeploymentContractError("legacy tombstone parent is unsafe")
    try:
        return os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DeploymentContractError("legacy tombstone parent cannot be opened safely") from exc


def _open_relative_no_follow(parent_descriptor: int, name: str, label: str) -> int:
    try:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_descriptor)
    except OSError as exc:
        raise DeploymentContractError(f"{label} cannot be opened safely") from exc


def _read_private_json(path: Path, label: str) -> object:
    _validate_state_parent(path.parent, label)
    try:
        before = path.lstat()
    except FileNotFoundError:
        raise DeploymentContractError(f"{label} is missing") from None
    _validate_private_file_stat(before, label)
    descriptor = _open_no_follow(path, label)
    try:
        after = os.fstat(descriptor)
        _validate_private_file_stat(after, label)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise DeploymentContractError(f"{label} changed during read")
        raw = _read_bounded(descriptor, label)
    finally:
        os.close(descriptor)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentContractError(f"{label} is invalid") from exc


def _write_private_json(path: Path, payload: Mapping[str, object], label: str) -> None:
    _validate_state_parent(path.parent, label)
    raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    if len(raw) > _MAX_STATE_BYTES:
        raise DeploymentContractError(f"{label} is too large")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    except OSError as exc:
        raise DeploymentContractError(f"{label} cannot be written") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_state_parent(path: Path, label: str) -> None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        path.mkdir(mode=0o700, parents=True)
        file_stat = path.lstat()
    if (
        not stat.S_ISDIR(file_stat.st_mode)
        or file_stat.st_uid != os.geteuid()
        or stat.S_IMODE(file_stat.st_mode) != 0o700
    ):
        raise DeploymentContractError(f"{label} state directory is unsafe")


def _validate_private_file_stat(file_stat: os.stat_result, label: str) -> None:
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != os.geteuid()
        or stat.S_IMODE(file_stat.st_mode) != 0o600
        or file_stat.st_nlink != 1
        or file_stat.st_size > _MAX_STATE_BYTES
    ):
        raise DeploymentContractError(f"{label} is unsafe")


def _open_no_follow(path: Path, label: str) -> int:
    try:
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DeploymentContractError(f"{label} cannot be opened safely") from exc


def _read_bounded(descriptor: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 8192)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_STATE_BYTES:
            raise DeploymentContractError(f"{label} is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
