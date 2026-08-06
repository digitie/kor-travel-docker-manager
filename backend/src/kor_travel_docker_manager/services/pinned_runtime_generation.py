"""F1D v5 pinned runtime generation의 typed state와 durable manifest.

이 모듈은 legacy compatible-pair/rollback model을 읽지 않는다. candidate image와
schema contract를 database reset 전에 고정하고, 한 active generation만 기록한다.
"""

from __future__ import annotations

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


def _validate_utc_timestamp(value: str, label: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DeploymentContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise DeploymentContractError(f"{label} is invalid")


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
