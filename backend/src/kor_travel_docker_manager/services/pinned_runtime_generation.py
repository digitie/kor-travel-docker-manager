"""F1D fresh-300 pinned runtime generation의 typed state와 durable manifest.

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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.trusted_install import (
    TRUSTED_PUBLIC_ROOT,
    running_from_trusted_install_root,
)

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
PinviRoleCatalogResetDiagnostic = Literal[
    "lifecycle_invalid",
    "permit_invalid",
    "target_not_isolated",
    "target_identity_invalid",
    "protected_namespace_present",
    "extra_namespace_present",
    "extension_present",
    "relation_present",
    "routine_present",
    "foreign_membership",
    "foreign_database_owner",
    "foreign_role_setting",
    "foreign_shared_dependency",
    "foreign_namespace_object",
    "unclassified",
]
PINVI_ROLE_CATALOG_RESET_DIAGNOSTICS = frozenset(
    {
        "lifecycle_invalid",
        "permit_invalid",
        "target_not_isolated",
        "target_identity_invalid",
        "protected_namespace_present",
        "extra_namespace_present",
        "extension_present",
        "relation_present",
        "routine_present",
        "foreign_membership",
        "foreign_database_owner",
        "foreign_role_setting",
        "foreign_shared_dependency",
        "foreign_namespace_object",
        "unclassified",
    }
)
RebuildPhase = Literal[
    "candidate_attested",
    "reset_intent_durable",
    "databases_recreated",
    "application_create_intent_durable",
    "application_created",
    "application_bootstrap_intent_durable",
    "application_roles_ready",
    "fresh_root_plan_ready",
    "fresh_root_fence_ready",
    "fresh_root_execution_intent",
    "fresh_root_ready",
    "fresh_finalize_plan_ready",
    "fresh_finalize_fence_ready",
    "fresh_finalize_execution_intent",
    "fresh_finalize_ready",
    "application_permit_ready",
    "metadata_permit_ready",
    "map_application_ready",
    "map_dagster_storage_intent_durable",
    "map_dagster_ready",
    "map_runtime_ready",
    "pinvi_schema_ready",
    "pinvi_api_ready",
    "cancel_probe_finalized",
    "pinvi_runtime_ready",
    "contract_verified",
    "manifest_committing",
    "committed",
]
CancelProbeStage = Literal[
    "uninitialized",
    "armed",
    "cancel_post_attempted",
    "consumed",
    "finalize_post_attempted",
    "finalized",
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
    "application_create_intent_durable",
    "application_created",
    "application_bootstrap_intent_durable",
    "application_roles_ready",
    "fresh_root_plan_ready",
    "fresh_root_fence_ready",
    "fresh_root_execution_intent",
    "fresh_root_ready",
    "fresh_finalize_plan_ready",
    "fresh_finalize_fence_ready",
    "fresh_finalize_execution_intent",
    "fresh_finalize_ready",
    "application_permit_ready",
    "metadata_permit_ready",
    "map_application_ready",
    "map_dagster_storage_intent_durable",
    "map_dagster_ready",
    "map_runtime_ready",
    "pinvi_schema_ready",
    "pinvi_api_ready",
    "cancel_probe_finalized",
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
_REBUILDABLE_CACHE_TARGET_DEFAULTS: dict[str, str] = {
    "KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS": "[]",
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED": "false",
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_COMMAND_TOKEN": "",
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_TOKEN": "",
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_ID": "pinvi-cache-target-consumer",
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_OPENAPI_SHA256": "",
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_SOURCE_REVISION": "",
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_CONTRACT_GENERATION": "",
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RESTORE_FENCE_TOKEN": "",
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RECOVERY_TOKEN": "",
}
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_DATABASE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_HEAD = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_POSTGRES_SYSTEM_IDENTIFIER = re.compile(r"^[0-9]{1,32}$")
_MAX_STATE_BYTES = 64 * 1024
_MANIFEST_VERSION = 6
_REBUILD_JOURNAL_VERSION = 8
_TOMBSTONE_VERSION = 8
_F1D_LEGACY_ARTIFACTS: tuple[str, ...] = (
    "compatible-pair-v2.json",
    "compatible-pair-v3.json",
    "compatible-pair-v4.json",
    "map-production-env-migration-v1.json",
    "cache-target-window-v1.json",
    "cache-target-diagnostic-v1.json",
    "cache-target-diagnostic-attempts-v1.json",
    "pinned-runtime-generation-v5.json",
    "pinned-runtime-rebuild-v5.json",
    "pinned-runtime-rebuild-v6.json",
    "pinned-runtime-v6/legacy-tombstone-v6.json",
    "pinned-runtime-rebuild-v7.json",
    "pinned-runtime-v7/legacy-tombstone-v7.json",
)
_F1D_PINSET_LEGACY_ARTIFACT = re.compile(
    r"^(pinned-runtime-rebuild-v7|legacy-tombstone-v7)-[0-9a-f]{64}\.json$"
)
_STATE_ROOT_ENV = "KTDM_PINNED_RUNTIME_STATE_ROOT"
_PUBLIC_ROOT_ENV = "KTDM_PINNED_RUNTIME_PUBLIC_ROOT"
_PROJECT_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")
_DEFAULT_STATE_ROOT = Path.home() / ".local" / "state" / "kor-travel-docker-manager"
# GM-09: 경로 상수의 정본은 services/trusted_install.py다.
_DEFAULT_PUBLIC_ROOT = TRUSTED_PUBLIC_ROOT
_MANIFEST_FILENAME = "pinned-runtime-generation-v6.json"
_JOURNAL_FILENAME_PREFIX = "pinned-runtime-rebuild-v8-"
_PUBLIC_JOURNAL_FILENAME = "pinned-runtime-rebuild-v8.json"
_TOMBSTONE_FILENAME_PREFIX = "legacy-tombstone-v8-"
_CANCEL_PROBE_STAGES: tuple[CancelProbeStage, ...] = (
    "uninitialized",
    "armed",
    "cancel_post_attempted",
    "consumed",
    "finalize_post_attempted",
    "finalized",
)
_APPLICATION_300_CONTROLLED_PHASES: frozenset[RebuildPhase] = frozenset(
    {
        "databases_recreated",
        "application_create_intent_durable",
        "application_created",
        "application_bootstrap_intent_durable",
        "application_roles_ready",
        "metadata_permit_ready",
        "fresh_root_plan_ready",
        "fresh_root_fence_ready",
        "fresh_root_execution_intent",
        "fresh_root_ready",
        "fresh_finalize_plan_ready",
        "fresh_finalize_fence_ready",
        "fresh_finalize_execution_intent",
        "fresh_finalize_ready",
        "application_permit_ready",
        "map_application_ready",
    }
)
_APPLICATION_300_EVIDENCE_FIELDS: tuple[str, ...] = (
    "application_create_database_identity",
    "application_create_database_identity_sha256",
    "application_database_identity",
    "application_database_identity_sha256",
    "fresh_root_operation_plan",
    "fresh_finalize_operation_plan",
    "app_final_permit_sha256",
    "dagster_metadata_database_identity",
    "dagster_metadata_database_identity_sha256",
    "metadata_permit_sha256",
)


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


@dataclass(frozen=True)
class PinnedRuntimeStatePaths:
    """v6 generation과 pinset별 v8 rebuild journal이 소유하는 owner-only state 경로.

    하나의 pinset은 하나의 journal/tombstone filename을 독점한다. 따라서 새 Map·PinVi
    release는 old same-pinset crash receipt만 재개하고, 다른 pinset의 immutable
    history가 새 destructive generation을 막지 않는다.
    """

    state_root: Path
    pinset_sha256: str
    manifest: Path
    journal: Path
    tombstone_receipt: Path


@dataclass(frozen=True)
class PinnedRuntimePublicPaths:
    """비-root 관측자가 읽는 v6/v8 공개 사본 경로.

    private state의 정확한 JSON을 복제할 뿐 envelope나 진단 원문을 파일에 섞지 않는다.
    Map의 exact-dict attestation은 private·public 원본 모두 같은 schema를 본다.
    """

    manifest: Path
    journal: Path


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
    for name, default in _REBUILDABLE_CACHE_TARGET_DEFAULTS.items():
        if values.get(name, default).strip() != default:
            raise DeploymentContractError(
                "pinned runtime rebuild requires an inert cache-target configuration"
            )
    return mode


def pinned_runtime_state_root(values: Mapping[str, str]) -> Path:
    """frozen environment만으로 project별 pinned runtime state root를 계산한다.

    ``pinned_runtime_state_paths``와 같은 규칙을 쓰되 mode 게이트
    (``require_rebuildable_mode``)와 pinset 인자를 요구하지 않는다. v4 legacy artifact의
    tombstone 경로나 현재 v6/v8 receipt를 읽기 전용으로 검사하는 호출자가 같은 정본을
    참조하기 위한 진입점이다. 디렉터리를 만들지 않고 존재도 요구하지 않는다.
    """

    project_name = values.get("COMPOSE_PROJECT_NAME", "").strip().lower()
    if _PROJECT_NAME.fullmatch(project_name) is None:
        raise DeploymentContractError(
            "COMPOSE_PROJECT_NAME must be explicit and canonical for pinned runtime state"
        )
    configured_root = values.get(_STATE_ROOT_ENV, "").strip()
    root = Path(configured_root) if configured_root else _DEFAULT_STATE_ROOT
    if not root.is_absolute() or root != root.resolve(strict=False):
        raise DeploymentContractError(
            "KTDM_PINNED_RUNTIME_STATE_ROOT must be a canonical absolute path"
        )
    state_root = root / project_name
    if state_root != state_root.resolve(strict=False):
        raise DeploymentContractError("pinned runtime state directory is invalid")
    return state_root


def pinned_runtime_manifest_path(values: Mapping[str, str]) -> Path:
    """v6 pinned generation manifest의 경로. 존재 여부는 확인하지 않는다."""

    return pinned_runtime_state_root(values) / _MANIFEST_FILENAME


def pinned_runtime_state_paths(
    values: Mapping[str, str],
    *,
    pinset_sha256: str,
) -> PinnedRuntimeStatePaths:
    """rehearsal project의 v6 manifest와 pinset별 v8 state namespace를 결정한다.

    파기형 transaction은 ``rehearsal/rebuildable``에서만 가능한 만큼 production
    fixed-root 예외나 v4 override를 갖지 않는다. 다만 disposable test/rehearsal은
    명시한 canonical absolute root로 격리할 수 있다.
    """

    require_rebuildable_mode(values)
    if _SHA256.fullmatch(pinset_sha256) is None:
        raise DeploymentContractError("pinned runtime state pinset digest is invalid")
    state_root = pinned_runtime_state_root(values)
    return PinnedRuntimeStatePaths(
        state_root=state_root,
        pinset_sha256=pinset_sha256,
        manifest=state_root / _MANIFEST_FILENAME,
        journal=state_root / f"{_JOURNAL_FILENAME_PREFIX}{pinset_sha256}.json",
        tombstone_receipt=legacy_tombstone_receipt_path(
            state_root,
            pinset_sha256=pinset_sha256,
        ),
    )


def ensure_pinned_runtime_state_directory(state_root: Path) -> None:
    """fresh-300 state root를 current Manager owner의 ``0700``으로 준비한다."""

    try:
        state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise DeploymentContractError("pinned runtime state directory is unavailable") from exc
    _validate_state_root(state_root)


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
    map_application_300_candidate_evidence: MapApplication300CandidateEvidence
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
        if not isinstance(
            self.map_application_300_candidate_evidence,
            MapApplication300CandidateEvidence,
        ):
            raise DeploymentContractError(
                "Map application 300 generation candidate evidence is invalid"
            )
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

    def to_payload(self) -> dict[str, object]:
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
            "map_application_300_candidate_evidence": (
                self.map_application_300_candidate_evidence.to_payload()
            ),
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


def _canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PinnedRuntimeManifest:
    """v6는 DB preimage가 없는 rollback slot을 보관하지 않는다."""

    version: Literal[6]
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
class PinnedRuntimeCancelProbeOutcome:
    """Map fixture가 확정한 canonical unsafe cancellation의 secret-free receipt."""

    name: Literal["pinvi_cancel_error"]
    status: Literal[409]
    code: Literal["PIPELINE_CANCELLATION_UNSAFE"]

    def __post_init__(self) -> None:
        if (
            self.name != "pinvi_cancel_error"
            or self.status != 409
            or self.code != "PIPELINE_CANCELLATION_UNSAFE"
        ):
            raise DeploymentContractError("pinned runtime cancel probe outcome is invalid")

    def to_payload(self) -> dict[str, int | str]:
        return {"name": self.name, "status": self.status, "code": self.code}


@dataclass(frozen=True)
class PinnedRuntimeCancelProbeReceipt:
    """cancel/finalize 재발행을 막는 v7 transaction-local high-watermark.

    Map fixture가 응답한 lifecycle UTC evidence도 함께 보존한다. 이 값은 Map
    lifecycle 상태를 다시 읽어 수렴할 때 같은 transaction의 immutable evidence인지
    검증하는 기준이며, retry 시 새 시각으로 덮어쓰지 않는다.
    """

    stage: CancelProbeStage = "uninitialized"
    job_id: str | None = None
    cancellation_id: str | None = None
    outcome: PinnedRuntimeCancelProbeOutcome | None = None
    fixture_created_at: str | None = None
    fixture_consumed_at: str | None = None
    fixture_finalized_at: str | None = None

    def __post_init__(self) -> None:
        if self.stage not in _CANCEL_PROBE_STAGES:
            raise DeploymentContractError("pinned runtime cancel probe stage is invalid")
        if self.stage == "uninitialized":
            if any(
                value is not None
                for value in (
                    self.job_id,
                    self.cancellation_id,
                    self.outcome,
                    self.fixture_created_at,
                    self.fixture_consumed_at,
                    self.fixture_finalized_at,
                )
            ):
                raise DeploymentContractError("uninitialized cancel probe receipt has evidence")
            return
        _validate_canonical_uuid(self.job_id, "pinned runtime cancel probe job ID")
        if self.fixture_created_at is None:
            raise DeploymentContractError("pinned runtime cancel probe has no creation timestamp")
        created_at = _parse_utc_timestamp(
            self.fixture_created_at,
            "pinned runtime cancel probe creation timestamp",
        )
        if self.stage in {"armed", "cancel_post_attempted"}:
            if (
                self.cancellation_id is not None
                or self.outcome is not None
                or self.fixture_consumed_at is not None
                or self.fixture_finalized_at is not None
            ):
                raise DeploymentContractError("armed cancel probe receipt has cancellation evidence")
            return
        _validate_canonical_uuid(
            self.cancellation_id,
            "pinned runtime cancel probe cancellation ID",
        )
        if self.outcome is None:
            raise DeploymentContractError("consumed cancel probe receipt has no outcome")
        if self.fixture_consumed_at is None:
            raise DeploymentContractError("pinned runtime cancel probe has no consumption timestamp")
        consumed_at = _parse_utc_timestamp(
            self.fixture_consumed_at,
            "pinned runtime cancel probe consumption timestamp",
        )
        if consumed_at < created_at:
            raise DeploymentContractError("pinned runtime cancel probe timestamp order is invalid")
        if self.stage in {"consumed", "finalize_post_attempted"}:
            if self.fixture_finalized_at is not None:
                raise DeploymentContractError("consumed cancel probe receipt has finalization evidence")
            return
        if self.fixture_finalized_at is None:
            raise DeploymentContractError("pinned runtime cancel probe has no finalization timestamp")
        finalized_at = _parse_utc_timestamp(
            self.fixture_finalized_at,
            "pinned runtime cancel probe finalization timestamp",
        )
        if finalized_at < consumed_at:
            raise DeploymentContractError("pinned runtime cancel probe timestamp order is invalid")

    def transition(
        self,
        stage: CancelProbeStage,
        *,
        job_id: str | None = None,
        cancellation_id: str | None = None,
        outcome: PinnedRuntimeCancelProbeOutcome | None = None,
        fixture_created_at: str | None = None,
        fixture_consumed_at: str | None = None,
        fixture_finalized_at: str | None = None,
    ) -> PinnedRuntimeCancelProbeReceipt:
        current_index = _CANCEL_PROBE_STAGES.index(self.stage)
        next_index = _CANCEL_PROBE_STAGES.index(stage)
        if next_index != current_index + 1:
            raise DeploymentContractError("pinned runtime cancel probe transition is invalid")
        if self.stage == "uninitialized":
            return PinnedRuntimeCancelProbeReceipt(
                stage=stage,
                job_id=job_id,
                fixture_created_at=fixture_created_at,
            )
        if self.stage in {"armed", "cancel_post_attempted"}:
            return PinnedRuntimeCancelProbeReceipt(
                stage=stage,
                job_id=self.job_id,
                cancellation_id=cancellation_id,
                outcome=outcome,
                fixture_created_at=self.fixture_created_at,
                fixture_consumed_at=fixture_consumed_at,
            )
        return PinnedRuntimeCancelProbeReceipt(
            stage=stage,
            job_id=self.job_id,
            cancellation_id=self.cancellation_id,
            outcome=self.outcome,
            fixture_created_at=self.fixture_created_at,
            fixture_consumed_at=self.fixture_consumed_at,
            fixture_finalized_at=fixture_finalized_at,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "job_id": self.job_id,
            "cancellation_id": self.cancellation_id,
            "outcome": None if self.outcome is None else self.outcome.to_payload(),
            "fixture_created_at": self.fixture_created_at,
            "fixture_consumed_at": self.fixture_consumed_at,
            "fixture_finalized_at": self.fixture_finalized_at,
        }


@dataclass(frozen=True)
class MapApplication300CandidateEvidence:
    """fresh application 300 candidate build/runtime contract evidence."""

    paired_receipt_sha256: str
    api_receipt_sha256: str
    candidate_git_tree: str
    postgres_image_id: str
    dagster_config_sha256: str
    dagster_yaml_sha256: str
    application_contract_sha256: str
    launch_contract_sha256: str

    def __post_init__(self) -> None:
        for digest in (
            self.paired_receipt_sha256,
            self.api_receipt_sha256,
            self.dagster_config_sha256,
            self.dagster_yaml_sha256,
            self.application_contract_sha256,
            self.launch_contract_sha256,
        ):
            if _SHA256.fullmatch(digest) is None:
                raise DeploymentContractError(
                    "Map application 300 candidate evidence digest is invalid"
                )
        if _REVISION.fullmatch(self.candidate_git_tree) is None:
            raise DeploymentContractError(
                "Map application 300 candidate git tree is invalid"
            )
        if _IMAGE_ID.fullmatch(self.postgres_image_id) is None:
            raise DeploymentContractError(
                "Map application 300 candidate PostgreSQL image ID is invalid"
            )

    def to_payload(self) -> dict[str, str]:
        return {
            "paired_receipt_sha256": self.paired_receipt_sha256,
            "api_receipt_sha256": self.api_receipt_sha256,
            "candidate_git_tree": self.candidate_git_tree,
            "postgres_image_id": self.postgres_image_id,
            "dagster_config_sha256": self.dagster_config_sha256,
            "dagster_yaml_sha256": self.dagster_yaml_sha256,
            "application_contract_sha256": self.application_contract_sha256,
            "launch_contract_sha256": self.launch_contract_sha256,
        }


@dataclass(frozen=True)
class MapApplication300ApplicationDatabaseIdentity:
    """application 300 fence/permit에 재검증 가능한 non-secret DB identity."""

    database_name: str
    database_oid: int
    database_owner: str
    postgres_system_identifier: str

    def __post_init__(self) -> None:
        if _DATABASE_IDENTIFIER.fullmatch(self.database_name) is None:
            raise DeploymentContractError(
                "Map application 300 application database name is invalid"
            )
        if type(self.database_oid) is not int or self.database_oid <= 0:
            raise DeploymentContractError(
                "Map application 300 application database OID is invalid"
            )
        if _DATABASE_IDENTIFIER.fullmatch(self.database_owner) is None:
            raise DeploymentContractError(
                "Map application 300 application database owner is invalid"
            )
        if _POSTGRES_SYSTEM_IDENTIFIER.fullmatch(self.postgres_system_identifier) is None:
            raise DeploymentContractError(
                "Map application 300 application database system identifier is invalid"
            )

    def to_payload(self) -> dict[str, object]:
        return {
            "database_name": self.database_name,
            "database_oid": self.database_oid,
            "database_owner": self.database_owner,
            "postgres_system_identifier": self.postgres_system_identifier,
        }

    def sha256(self) -> str:
        return _canonical_payload_sha256(self.to_payload())


@dataclass(frozen=True)
class PinnedRuntimeDatabaseIdentity:
    """PinVi DB를 committed generation에 재검증 가능한 identity로 고정한다."""

    system_identifier: str
    name: str
    oid: int
    owner: str
    login_role: str

    def __post_init__(self) -> None:
        if _POSTGRES_SYSTEM_IDENTIFIER.fullmatch(self.system_identifier) is None:
            raise DeploymentContractError("pinned runtime database system identifier is invalid")
        for identifier in (self.name, self.owner, self.login_role):
            if _DATABASE_IDENTIFIER.fullmatch(identifier) is None:
                raise DeploymentContractError("pinned runtime database identity is invalid")
        if type(self.oid) is not int or self.oid <= 0 or self.owner != self.login_role:
            raise DeploymentContractError("pinned runtime database identity is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "system_identifier": self.system_identifier,
            "name": self.name,
            "oid": self.oid,
            "owner": self.owner,
            "login_role": self.login_role,
        }


@dataclass(frozen=True)
class MapApplication300DagsterMetadataRoleAttributes:
    """Dagster metadata login role의 fail-closed privilege snapshot."""

    superuser: bool
    create_database: bool
    create_role: bool
    replication: bool
    bypass_rls: bool
    granted_role_count: int
    member_role_count: int
    can_login: bool = True
    inherit: bool = False
    connection_limit: int = -1
    valid_until_is_null: bool = True
    role_config_count: int = 0
    database_role_setting_count: int = 0

    def __post_init__(self) -> None:
        for flag in (
            self.can_login,
            self.inherit,
            self.superuser,
            self.create_database,
            self.create_role,
            self.replication,
            self.bypass_rls,
        ):
            if type(flag) is not bool:
                raise DeploymentContractError(
                    "Map application 300 Dagster metadata role attribute is invalid"
                )
        for count in (self.granted_role_count, self.member_role_count):
            if type(count) is not int or count < 0:
                raise DeploymentContractError(
                    "Map application 300 Dagster metadata role membership is invalid"
                )
        if (
            type(self.connection_limit) is not int
            or self.connection_limit < -1
            or type(self.valid_until_is_null) is not bool
        ):
            raise DeploymentContractError(
                "Map application 300 Dagster metadata role persistence is invalid"
            )
        for count in (self.role_config_count, self.database_role_setting_count):
            if type(count) is not int or count < 0:
                raise DeploymentContractError(
                    "Map application 300 Dagster metadata role setting is invalid"
                )
        if (
            not self.can_login
            or self.inherit
            or self.superuser
            or self.create_database
            or self.create_role
            or self.replication
            or self.bypass_rls
            or self.connection_limit != -1
            or not self.valid_until_is_null
            or self.role_config_count != 0
            or self.database_role_setting_count != 0
            or self.granted_role_count != 0
            or self.member_role_count != 0
        ):
            raise DeploymentContractError(
                "Map application 300 Dagster metadata role is privileged"
            )

    def to_payload(self) -> dict[str, object]:
        return {
            "can_login": self.can_login,
            "inherit": self.inherit,
            "superuser": self.superuser,
            "create_database": self.create_database,
            "create_role": self.create_role,
            "replication": self.replication,
            "bypass_rls": self.bypass_rls,
            "connection_limit": self.connection_limit,
            "valid_until_is_null": self.valid_until_is_null,
            "role_config_count": self.role_config_count,
            "database_role_setting_count": self.database_role_setting_count,
            "granted_role_count": self.granted_role_count,
            "member_role_count": self.member_role_count,
        }


@dataclass(frozen=True)
class MapApplication300DagsterMetadataDatabaseIdentity:
    """Dagster metadata permit에 재검증 가능한 non-secret DB/role identity."""

    system_identifier: str
    name: str
    oid: int
    owner: str
    login_role: str
    login_role_attributes: MapApplication300DagsterMetadataRoleAttributes

    def __post_init__(self) -> None:
        if _POSTGRES_SYSTEM_IDENTIFIER.fullmatch(self.system_identifier) is None:
            raise DeploymentContractError(
                "Map application 300 Dagster metadata system identifier is invalid"
            )
        for identifier in (self.name, self.owner, self.login_role):
            if _DATABASE_IDENTIFIER.fullmatch(identifier) is None:
                raise DeploymentContractError(
                    "Map application 300 Dagster metadata identity is invalid"
                )
        if type(self.oid) is not int or self.oid <= 0:
            raise DeploymentContractError(
                "Map application 300 Dagster metadata database OID is invalid"
            )
        if self.owner != self.login_role:
            raise DeploymentContractError(
                "Map application 300 Dagster metadata owner differs from login role"
            )
        if not isinstance(
            self.login_role_attributes,
            MapApplication300DagsterMetadataRoleAttributes,
        ):
            raise DeploymentContractError(
                "Map application 300 Dagster metadata role attributes are invalid"
            )

    def to_payload(self) -> dict[str, object]:
        return {
            "system_identifier": self.system_identifier,
            "name": self.name,
            "oid": self.oid,
            "owner": self.owner,
            "login_role": self.login_role,
            "login_role_attributes": self.login_role_attributes.to_payload(),
        }

    def sha256(self) -> str:
        return _canonical_payload_sha256(self.to_payload())


@dataclass(frozen=True)
class MapApplication300OperationPlan:
    """root/finalize write를 재개할 수 있게 보존하는 durable operation plan."""

    transaction_id: str
    operation_id: str
    basis_journal_sha256: str
    basis_journal_generation: int
    writer_fence_expires_at: str
    fence_sha256: str
    result_sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_canonical_uuid(
            self.transaction_id,
            "Map application 300 operation transaction ID",
        )
        _validate_canonical_uuid(
            self.operation_id,
            "Map application 300 operation ID",
        )
        for digest in (
            self.basis_journal_sha256,
            self.fence_sha256,
            self.result_sha256,
        ):
            if digest is not None and _SHA256.fullmatch(digest) is None:
                raise DeploymentContractError(
                    "Map application 300 operation plan digest is invalid"
                )
        if type(self.basis_journal_generation) is not int or (
            self.basis_journal_generation < 0
        ):
            raise DeploymentContractError(
                "Map application 300 operation plan basis generation is invalid"
            )
        _validate_utc_timestamp(
            self.writer_fence_expires_at,
            "Map application 300 operation writer fence expiry",
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "operation_id": self.operation_id,
            "basis_journal_sha256": self.basis_journal_sha256,
            "basis_journal_generation": self.basis_journal_generation,
            "writer_fence_expires_at": self.writer_fence_expires_at,
            "fence_sha256": self.fence_sha256,
            "result_sha256": self.result_sha256,
        }

    def pending(self) -> MapApplication300OperationPlan:
        return replace(self, result_sha256=None)

    def with_result(self, result_sha256: str) -> MapApplication300OperationPlan:
        if self.result_sha256 is not None and self.result_sha256 != result_sha256:
            raise DeploymentContractError(
                "Map application 300 operation plan result cannot be rebound"
            )
        return replace(self, result_sha256=result_sha256)


@dataclass(frozen=True)
class MapApplication300ExecutionEvidence:
    """fresh application 300 execution receipts accumulated by phase."""

    application_create_database_identity: (
        MapApplication300ApplicationDatabaseIdentity | None
    ) = None
    application_create_database_identity_sha256: str | None = None
    application_database_identity: MapApplication300ApplicationDatabaseIdentity | None = None
    application_database_identity_sha256: str | None = None
    fresh_root_operation_plan: MapApplication300OperationPlan | None = None
    fresh_finalize_operation_plan: MapApplication300OperationPlan | None = None
    app_final_permit_sha256: str | None = None
    dagster_metadata_database_identity: (
        MapApplication300DagsterMetadataDatabaseIdentity | None
    ) = None
    dagster_metadata_database_identity_sha256: str | None = None
    metadata_permit_sha256: str | None = None

    def __post_init__(self) -> None:
        for digest in (
            self.application_create_database_identity_sha256,
            self.application_database_identity_sha256,
            self.app_final_permit_sha256,
            self.dagster_metadata_database_identity_sha256,
            self.metadata_permit_sha256,
        ):
            if digest is not None and _SHA256.fullmatch(digest) is None:
                raise DeploymentContractError(
                    "Map application 300 execution evidence digest is invalid"
                )
        if self.application_create_database_identity is not None:
            if not isinstance(
                self.application_create_database_identity,
                MapApplication300ApplicationDatabaseIdentity,
            ):
                raise DeploymentContractError(
                    "Map application 300 create database identity is invalid"
                )
            if (
                self.application_create_database_identity_sha256
                != self.application_create_database_identity.sha256()
            ):
                raise DeploymentContractError(
                    "Map application 300 create database identity SHA differs"
                )
        elif self.application_create_database_identity_sha256 is not None:
            raise DeploymentContractError(
                "Map application 300 create database identity is missing"
            )
        if self.application_database_identity is not None:
            if not isinstance(
                self.application_database_identity,
                MapApplication300ApplicationDatabaseIdentity,
            ):
                raise DeploymentContractError(
                    "Map application 300 application database identity is invalid"
                )
            if (
                self.application_database_identity_sha256
                != self.application_database_identity.sha256()
            ):
                raise DeploymentContractError(
                    "Map application 300 application database identity SHA differs"
                )
        elif self.application_database_identity_sha256 is not None:
            raise DeploymentContractError(
                "Map application 300 application database identity is missing"
            )
        if self.fresh_root_operation_plan is not None and not isinstance(
            self.fresh_root_operation_plan,
            MapApplication300OperationPlan,
        ):
            raise DeploymentContractError(
                "Map application 300 root operation plan is invalid"
            )
        if self.fresh_finalize_operation_plan is not None and not isinstance(
            self.fresh_finalize_operation_plan,
            MapApplication300OperationPlan,
        ):
            raise DeploymentContractError(
                "Map application 300 finalize operation plan is invalid"
            )
        if self.fresh_finalize_operation_plan is not None and (
            self.fresh_root_operation_plan is None
            or self.fresh_root_operation_plan.result_sha256 is None
        ):
            raise DeploymentContractError(
                "Map application 300 finalize operation lacks root result"
            )
        if self.app_final_permit_sha256 is not None and (
            self.fresh_finalize_operation_plan is None
            or self.fresh_finalize_operation_plan.result_sha256 is None
        ):
            raise DeploymentContractError(
                "Map application 300 final permit lacks finalize result"
            )
        if self.dagster_metadata_database_identity is not None:
            if not isinstance(
                self.dagster_metadata_database_identity,
                MapApplication300DagsterMetadataDatabaseIdentity,
            ):
                raise DeploymentContractError(
                    "Map application 300 Dagster metadata identity is invalid"
                )
            if (
                self.dagster_metadata_database_identity_sha256
                != self.dagster_metadata_database_identity.sha256()
            ):
                raise DeploymentContractError(
                    "Map application 300 Dagster metadata identity SHA differs"
                )
        elif self.dagster_metadata_database_identity_sha256 is not None:
            raise DeploymentContractError(
                "Map application 300 Dagster metadata identity is missing"
            )

    def with_application_create_database_identity(
        self,
        identity: MapApplication300ApplicationDatabaseIdentity,
    ) -> MapApplication300ExecutionEvidence:
        if not isinstance(identity, MapApplication300ApplicationDatabaseIdentity):
            raise DeploymentContractError(
                "Map application 300 create database identity is invalid"
            )
        if (
            self.application_create_database_identity is not None
            and self.application_create_database_identity != identity
        ):
            raise DeploymentContractError(
                "Map application 300 execution evidence cannot be rebound"
            )
        return self.with_digest(
            application_create_database_identity=identity,
            application_create_database_identity_sha256=identity.sha256(),
        )

    def with_application_database_identity(
        self,
        identity: MapApplication300ApplicationDatabaseIdentity,
    ) -> MapApplication300ExecutionEvidence:
        if not isinstance(identity, MapApplication300ApplicationDatabaseIdentity):
            raise DeploymentContractError(
                "Map application 300 application database identity is invalid"
            )
        if (
            self.application_database_identity is not None
            and self.application_database_identity != identity
        ):
            raise DeploymentContractError(
                "Map application 300 execution evidence cannot be rebound"
            )
        return self.with_digest(
            application_database_identity=identity,
            application_database_identity_sha256=identity.sha256(),
        )

    def with_dagster_metadata_database_identity(
        self,
        identity: MapApplication300DagsterMetadataDatabaseIdentity,
    ) -> MapApplication300ExecutionEvidence:
        if not isinstance(identity, MapApplication300DagsterMetadataDatabaseIdentity):
            raise DeploymentContractError(
                "Map application 300 Dagster metadata identity is invalid"
            )
        if (
            self.dagster_metadata_database_identity is not None
            and self.dagster_metadata_database_identity != identity
        ):
            raise DeploymentContractError(
                "Map application 300 execution evidence cannot be rebound"
            )
        return self.with_digest(
            dagster_metadata_database_identity=identity,
            dagster_metadata_database_identity_sha256=identity.sha256(),
        )

    def with_fresh_root_operation_plan(
        self,
        plan: MapApplication300OperationPlan,
    ) -> MapApplication300ExecutionEvidence:
        _validate_operation_plan_object(plan, "root")
        _validate_operation_plan_result_state(plan, result_required=False)
        if (
            self.fresh_root_operation_plan is not None
            and self.fresh_root_operation_plan != plan
        ):
            raise DeploymentContractError(
                "Map application 300 root operation plan cannot be rebound"
            )
        return self.with_digest(fresh_root_operation_plan=plan)

    def with_fresh_root_result(
        self,
        plan: MapApplication300OperationPlan,
    ) -> MapApplication300ExecutionEvidence:
        _validate_operation_plan_object(plan, "root")
        _validate_operation_plan_result_state(plan, result_required=True)
        if self.fresh_root_operation_plan is None:
            raise DeploymentContractError("Map application 300 root operation plan is missing")
        if self.fresh_root_operation_plan.pending() != plan.pending():
            raise DeploymentContractError(
                "Map application 300 root operation plan changed"
            )
        if (
            self.fresh_root_operation_plan.result_sha256 is not None
            and self.fresh_root_operation_plan != plan
        ):
            raise DeploymentContractError(
                "Map application 300 root operation result cannot be rebound"
            )
        return replace(self, fresh_root_operation_plan=plan)

    def with_renewed_fresh_root_operation_plan(
        self,
        plan: MapApplication300OperationPlan,
    ) -> MapApplication300ExecutionEvidence:
        _validate_operation_plan_object(plan, "root")
        _validate_operation_plan_result_state(plan, result_required=False)
        if (
            self.fresh_root_operation_plan is None
            or self.fresh_root_operation_plan.result_sha256 is not None
            or self.fresh_root_operation_plan.operation_id != plan.operation_id
        ):
            raise DeploymentContractError(
                "Map application 300 root operation plan cannot be renewed"
            )
        return replace(self, fresh_root_operation_plan=plan)

    def with_fresh_finalize_operation_plan(
        self,
        plan: MapApplication300OperationPlan,
    ) -> MapApplication300ExecutionEvidence:
        _validate_operation_plan_object(plan, "finalize")
        _validate_operation_plan_result_state(plan, result_required=False)
        if (
            self.fresh_finalize_operation_plan is not None
            and self.fresh_finalize_operation_plan != plan
        ):
            raise DeploymentContractError(
                "Map application 300 finalize operation plan cannot be rebound"
            )
        return self.with_digest(fresh_finalize_operation_plan=plan)

    def with_fresh_finalize_result(
        self,
        plan: MapApplication300OperationPlan,
    ) -> MapApplication300ExecutionEvidence:
        _validate_operation_plan_object(plan, "finalize")
        _validate_operation_plan_result_state(plan, result_required=True)
        if self.fresh_finalize_operation_plan is None:
            raise DeploymentContractError(
                "Map application 300 finalize operation plan is missing"
            )
        if self.fresh_finalize_operation_plan.pending() != plan.pending():
            raise DeploymentContractError(
                "Map application 300 finalize operation plan changed"
            )
        if (
            self.fresh_finalize_operation_plan.result_sha256 is not None
            and self.fresh_finalize_operation_plan != plan
        ):
            raise DeploymentContractError(
                "Map application 300 finalize operation result cannot be rebound"
            )
        return replace(self, fresh_finalize_operation_plan=plan)

    def with_renewed_fresh_finalize_operation_plan(
        self,
        plan: MapApplication300OperationPlan,
    ) -> MapApplication300ExecutionEvidence:
        _validate_operation_plan_object(plan, "finalize")
        _validate_operation_plan_result_state(plan, result_required=False)
        if (
            self.fresh_finalize_operation_plan is None
            or self.fresh_finalize_operation_plan.result_sha256 is not None
            or self.fresh_finalize_operation_plan.operation_id != plan.operation_id
        ):
            raise DeploymentContractError(
                "Map application 300 finalize operation plan cannot be renewed"
            )
        return replace(self, fresh_finalize_operation_plan=plan)

    def with_digest(
        self,
        **changes: str
        | MapApplication300ApplicationDatabaseIdentity
        | MapApplication300DagsterMetadataDatabaseIdentity
        | MapApplication300OperationPlan,
    ) -> MapApplication300ExecutionEvidence:
        for key, value in changes.items():
            if key not in _APPLICATION_300_EVIDENCE_FIELDS:
                raise DeploymentContractError(
                    "Map application 300 execution evidence field is invalid"
                )
            if key in {
                "application_create_database_identity",
                "application_database_identity",
                "dagster_metadata_database_identity",
                "fresh_root_operation_plan",
                "fresh_finalize_operation_plan",
            }:
                if key in {
                    "application_create_database_identity",
                    "application_database_identity",
                } and not isinstance(
                    value,
                    MapApplication300ApplicationDatabaseIdentity,
                ):
                    raise DeploymentContractError(
                        "Map application 300 application database identity is invalid"
                    )
                if key == "dagster_metadata_database_identity" and not isinstance(
                    value,
                    MapApplication300DagsterMetadataDatabaseIdentity,
                ):
                    raise DeploymentContractError(
                        "Map application 300 Dagster metadata identity is invalid"
                    )
                if key in {
                    "fresh_root_operation_plan",
                    "fresh_finalize_operation_plan",
                } and not isinstance(value, MapApplication300OperationPlan):
                    raise DeploymentContractError(
                        "Map application 300 operation plan is invalid"
                    )
                existing = getattr(self, key)
                if existing is not None and existing != value:
                    raise DeploymentContractError(
                        "Map application 300 execution evidence cannot be rebound"
                    )
                continue
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise DeploymentContractError(
                    "Map application 300 execution evidence digest is invalid"
                )
            existing = getattr(self, key)
            if existing is not None and existing != value:
                raise DeploymentContractError(
                    "Map application 300 execution evidence cannot be rebound"
                )
        return replace(self, **cast(dict[str, Any], changes))

    def to_payload(self) -> dict[str, object]:
        return {
            "application_create_database_identity": (
                None
                if self.application_create_database_identity is None
                else self.application_create_database_identity.to_payload()
            ),
            "application_create_database_identity_sha256": (
                self.application_create_database_identity_sha256
            ),
            "application_database_identity": (
                None
                if self.application_database_identity is None
                else self.application_database_identity.to_payload()
            ),
            "application_database_identity_sha256": (
                self.application_database_identity_sha256
            ),
            "fresh_root_operation_plan": (
                None
                if self.fresh_root_operation_plan is None
                else self.fresh_root_operation_plan.to_payload()
            ),
            "fresh_finalize_operation_plan": (
                None
                if self.fresh_finalize_operation_plan is None
                else self.fresh_finalize_operation_plan.to_payload()
            ),
            "app_final_permit_sha256": self.app_final_permit_sha256,
            "dagster_metadata_database_identity": (
                None
                if self.dagster_metadata_database_identity is None
                else self.dagster_metadata_database_identity.to_payload()
            ),
            "dagster_metadata_database_identity_sha256": (
                self.dagster_metadata_database_identity_sha256
            ),
            "metadata_permit_sha256": self.metadata_permit_sha256,
        }


def _validate_operation_plan_object(
    plan: MapApplication300OperationPlan,
    label: str,
) -> None:
    if not isinstance(plan, MapApplication300OperationPlan):
        raise DeploymentContractError(
            f"Map application 300 {label} operation plan is invalid"
        )


def _validate_operation_plan_result_state(
    plan: MapApplication300OperationPlan,
    *,
    result_required: bool,
) -> None:
    if result_required and plan.result_sha256 is None:
        raise DeploymentContractError("Map application 300 operation result is missing")
    if not result_required and plan.result_sha256 is not None:
        raise DeploymentContractError("Map application 300 phase has future evidence")


def _validate_operation_plan_basis(
    plan: MapApplication300OperationPlan,
    journal: PinnedRuntimeRebuildJournal,
    *,
    label: str,
) -> None:
    if plan.basis_journal_generation != journal.journal_generation:
        raise DeploymentContractError(
            f"Map application 300 {label} operation plan basis generation differs"
        )
    if plan.basis_journal_sha256 != rebuild_journal_sha256(journal):
        raise DeploymentContractError(
            f"Map application 300 {label} operation plan basis journal differs"
        )


def _validate_same_pending_operation_plan(
    expected: MapApplication300OperationPlan | None,
    actual: MapApplication300OperationPlan,
    *,
    label: str,
) -> None:
    if expected is None:
        raise DeploymentContractError(
            f"Map application 300 {label} operation plan is missing"
        )
    if expected.pending() != actual.pending():
        raise DeploymentContractError(
            f"Map application 300 {label} operation plan changed"
        )


@dataclass(frozen=True)
class PinviRoleCredentialEnvironmentRebind:
    """resume 중 fresh PinVi DB role source를 결박하는 비밀 비포함 receipt."""

    previous_environment_sha256: str
    previous_resolved_compose_sha256: str
    current_environment_sha256: str
    current_resolved_compose_sha256: str

    def __post_init__(self) -> None:
        if not all(
            _SHA256.fullmatch(value) is not None
            for value in (
                self.previous_environment_sha256,
                self.previous_resolved_compose_sha256,
                self.current_environment_sha256,
                self.current_resolved_compose_sha256,
            )
        ) or self.previous_environment_sha256 == self.current_environment_sha256:
            raise DeploymentContractError(
                "PinVi role credential environment rebind receipt is invalid"
            )

    def to_payload(self) -> dict[str, str]:
        return {
            "previous_environment_sha256": self.previous_environment_sha256,
            "previous_resolved_compose_sha256": self.previous_resolved_compose_sha256,
            "current_environment_sha256": self.current_environment_sha256,
            "current_resolved_compose_sha256": self.current_resolved_compose_sha256,
        }


@dataclass(frozen=True)
class PinviRoleLifecycleBlock:
    """동일 pinset의 role topology 재실행을 막는 비밀 비포함 terminal receipt."""

    stage: Literal[
        "pinvi_role_catalog_reset",
        "pinvi_role_open",
        "pinvi_role_seal",
        "pinvi_role_verify",
    ]
    code: Literal[
        "role_catalog_reset_failed",
        "role_topology_noncanonical",
        "role_topology_unavailable",
    ]
    diagnostic: PinviRoleCatalogResetDiagnostic = "unclassified"

    def __post_init__(self) -> None:
        if self.stage not in {
            "pinvi_role_catalog_reset",
            "pinvi_role_open",
            "pinvi_role_seal",
            "pinvi_role_verify",
        } or self.code not in {
            "role_catalog_reset_failed",
            "role_topology_noncanonical",
            "role_topology_unavailable",
        }:
            raise DeploymentContractError("PinVi role lifecycle block receipt is invalid")
        if self.stage == "pinvi_role_catalog_reset":
            if (
                self.code != "role_catalog_reset_failed"
                or self.diagnostic not in PINVI_ROLE_CATALOG_RESET_DIAGNOSTICS
            ):
                raise DeploymentContractError("PinVi role lifecycle block receipt is invalid")
        elif self.stage != "pinvi_role_verify" and self.code != "role_topology_noncanonical":
            raise DeploymentContractError("PinVi role lifecycle block receipt is invalid")
        elif self.stage != "pinvi_role_catalog_reset" and self.diagnostic != "unclassified":
            raise DeploymentContractError("PinVi role lifecycle block receipt is invalid")

    def to_payload(self) -> dict[str, str]:
        return {"stage": self.stage, "code": self.code, "diagnostic": self.diagnostic}


@dataclass(frozen=True)
class PinviRoleCatalogResetReceipt:
    """fresh-only cluster role catalog reset의 durable intent/result."""

    state: Literal["intent", "completed"]

    def __post_init__(self) -> None:
        if self.state not in {"intent", "completed"}:
            raise DeploymentContractError("PinVi role catalog reset receipt is invalid")

    def to_payload(self) -> dict[str, str]:
        return {"state": self.state}


@dataclass(frozen=True)
class PinnedRuntimeRebuildJournal:
    """candidate image 보존부터 v6 manifest commit까지의 v8 same-pinset resume receipt."""

    version: Literal[8]
    transaction_id: str
    phase: RebuildPhase
    candidate: PinnedRuntimeGeneration
    map_application_300_candidate_evidence: MapApplication300CandidateEvidence
    environment_sha256: str
    compose_sha256: str
    resolved_compose_sha256: str
    created_at: str
    pinvi_database_identity: PinnedRuntimeDatabaseIdentity | None = None
    journal_generation: int = 0
    map_application_300_execution_evidence: MapApplication300ExecutionEvidence = field(
        default_factory=MapApplication300ExecutionEvidence
    )
    cancel_probe: PinnedRuntimeCancelProbeReceipt = PinnedRuntimeCancelProbeReceipt()
    pinvi_role_credential_environment_rebind: (
        PinviRoleCredentialEnvironmentRebind | None
    ) = None
    pinvi_role_catalog_reset: PinviRoleCatalogResetReceipt | None = None
    pinvi_role_lifecycle_block: PinviRoleLifecycleBlock | None = None

    def __post_init__(self) -> None:
        if self.version != _REBUILD_JOURNAL_VERSION:
            raise DeploymentContractError("pinned runtime rebuild journal version is invalid")
        _validate_canonical_uuid(self.transaction_id, "pinned runtime rebuild transaction ID")
        if self.phase not in REBUILD_PHASES:
            raise DeploymentContractError("pinned runtime rebuild phase is invalid")
        if (
            type(self.journal_generation) is not int
            or self.journal_generation < REBUILD_PHASES.index(self.phase)
        ):
            raise DeploymentContractError(
                "pinned runtime rebuild journal generation is invalid"
            )
        for digest in (
            self.environment_sha256,
            self.compose_sha256,
            self.resolved_compose_sha256,
        ):
            if _SHA256.fullmatch(digest) is None:
                raise DeploymentContractError(
                    "pinned runtime rebuild input digest is invalid"
                )
        rebind = self.pinvi_role_credential_environment_rebind
        if rebind is not None:
            if (
                not isinstance(rebind, PinviRoleCredentialEnvironmentRebind)
                or REBUILD_PHASES.index(self.phase)
                < REBUILD_PHASES.index("map_runtime_ready")
                or self.environment_sha256 != rebind.current_environment_sha256
                or self.resolved_compose_sha256
                != rebind.current_resolved_compose_sha256
            ):
                raise DeploymentContractError(
                    "pinned runtime rebuild has invalid PinVi role credential environment rebind"
                )
        role_lifecycle_block = self.pinvi_role_lifecycle_block
        if role_lifecycle_block is not None and (
            not isinstance(role_lifecycle_block, PinviRoleLifecycleBlock)
            or self.phase != "map_runtime_ready"
        ):
            raise DeploymentContractError(
                "pinned runtime rebuild has invalid PinVi role lifecycle block"
            )
        catalog_reset = self.pinvi_role_catalog_reset
        if catalog_reset is not None and (
            not isinstance(catalog_reset, PinviRoleCatalogResetReceipt)
            or self.pinvi_database_identity is None
            or REBUILD_PHASES.index(self.phase)
            < REBUILD_PHASES.index("databases_recreated")
            or (
                catalog_reset.state == "intent"
                and REBUILD_PHASES.index(self.phase)
                > REBUILD_PHASES.index("map_runtime_ready")
            )
            or (
                catalog_reset.state == "completed"
                and REBUILD_PHASES.index(self.phase)
                < REBUILD_PHASES.index("map_runtime_ready")
            )
        ):
            raise DeploymentContractError(
                "pinned runtime rebuild has invalid PinVi role catalog reset receipt"
            )
        _validate_utc_timestamp(self.created_at, "pinned runtime rebuild timestamp")
        if not isinstance(
            self.map_application_300_candidate_evidence,
            MapApplication300CandidateEvidence,
        ):
            raise DeploymentContractError(
                "Map application 300 candidate evidence is invalid"
            )
        if self.map_application_300_candidate_evidence != (
            self.candidate.map_application_300_candidate_evidence
        ):
            raise DeploymentContractError(
                "Map application 300 candidate evidence differs from generation"
            )
        if not isinstance(
            self.map_application_300_execution_evidence,
            MapApplication300ExecutionEvidence,
        ):
            raise DeploymentContractError(
                "Map application 300 execution evidence is invalid"
            )
        _validate_application_300_phase_evidence(
            self.phase,
            self.map_application_300_execution_evidence,
        )
        pinvi_identity_required = REBUILD_PHASES.index(self.phase) >= REBUILD_PHASES.index(
            "databases_recreated"
        )
        if pinvi_identity_required != isinstance(
            self.pinvi_database_identity,
            PinnedRuntimeDatabaseIdentity,
        ):
            raise DeploymentContractError(
                "pinned runtime phase has invalid PinVi database identity evidence"
            )
        if not isinstance(self.cancel_probe, PinnedRuntimeCancelProbeReceipt):
            raise DeploymentContractError("pinned runtime cancel probe receipt is invalid")
        if (
            REBUILD_PHASES.index(self.phase)
            >= REBUILD_PHASES.index("cancel_probe_finalized")
            and self.cancel_probe.stage != "finalized"
        ):
            raise DeploymentContractError("pinned runtime phase lacks finalized cancel probe")

    def transition(self, phase: RebuildPhase) -> PinnedRuntimeRebuildJournal:
        current_index = REBUILD_PHASES.index(self.phase)
        if current_index == len(REBUILD_PHASES) - 1 or REBUILD_PHASES[current_index + 1] != phase:
            raise DeploymentContractError("pinned runtime rebuild phase transition is invalid")
        if phase in _APPLICATION_300_CONTROLLED_PHASES:
            raise DeploymentContractError(
                "Map application 300 phase transition requires evidence-specific method"
            )
        return replace(self, phase=phase, journal_generation=self.journal_generation + 1)

    def with_pinvi_role_credential_environment_rebind(
        self,
        *,
        previous_environment_sha256: str,
        compose_sha256: str,
        current_environment_sha256: str,
        current_resolved_compose_sha256: str,
    ) -> PinnedRuntimeRebuildJournal:
        """Map runtime ready resume에만 fresh role config input을 한 번 재결박한다."""

        if (
            self.phase != "map_runtime_ready"
            or self.pinvi_role_credential_environment_rebind is not None
            or self.environment_sha256 != previous_environment_sha256
            or self.compose_sha256 != compose_sha256
        ):
            raise DeploymentContractError(
                "PinVi role credential environment rebind is not permitted"
            )
        receipt = PinviRoleCredentialEnvironmentRebind(
            previous_environment_sha256=previous_environment_sha256,
            previous_resolved_compose_sha256=self.resolved_compose_sha256,
            current_environment_sha256=current_environment_sha256,
            current_resolved_compose_sha256=current_resolved_compose_sha256,
        )
        return replace(
            self,
            environment_sha256=current_environment_sha256,
            resolved_compose_sha256=current_resolved_compose_sha256,
            journal_generation=self.journal_generation + 1,
            pinvi_role_credential_environment_rebind=receipt,
        )

    def with_pinvi_role_lifecycle_block(
        self,
        receipt: PinviRoleLifecycleBlock,
    ) -> PinnedRuntimeRebuildJournal:
        """role topology failure 뒤 같은 candidate의 lifecycle 재실행을 봉인한다."""

        if (
            self.phase != "map_runtime_ready"
            or self.pinvi_role_lifecycle_block is not None
            or not isinstance(receipt, PinviRoleLifecycleBlock)
        ):
            raise DeploymentContractError("PinVi role lifecycle block is not permitted")
        return replace(
            self,
            journal_generation=self.journal_generation + 1,
            pinvi_role_lifecycle_block=receipt,
        )

    def with_pinvi_role_catalog_reset_completed(self) -> PinnedRuntimeRebuildJournal:
        """fresh catalog reset의 성공만 다음 role-open 단계로 넘긴다."""

        if (
            self.phase != "map_runtime_ready"
            or self.pinvi_role_catalog_reset
            != PinviRoleCatalogResetReceipt(state="intent")
            or self.pinvi_role_lifecycle_block is not None
        ):
            raise DeploymentContractError("PinVi role catalog reset completion is not permitted")
        return replace(
            self,
            journal_generation=self.journal_generation + 1,
            pinvi_role_catalog_reset=PinviRoleCatalogResetReceipt(state="completed"),
        )

    def with_application_roles_ready(
        self,
        *,
        application_database_identity: MapApplication300ApplicationDatabaseIdentity,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "application_bootstrap_intent_durable":
            raise DeploymentContractError(
                "Map application 300 application role evidence is out of order"
            )
        create_identity = (
            self.map_application_300_execution_evidence
            .application_create_database_identity
        )
        if create_identity is None or (
            create_identity.database_name != application_database_identity.database_name
            or create_identity.database_oid != application_database_identity.database_oid
            or create_identity.postgres_system_identifier
            != application_database_identity.postgres_system_identifier
        ):
            raise DeploymentContractError(
                "Map application 300 database identity changed during role bootstrap"
            )
        evidence = (
            self.map_application_300_execution_evidence.with_application_database_identity(
                application_database_identity
            )
        )
        return replace(
            self,
            phase="application_roles_ready",
            journal_generation=self.journal_generation + 1,
            map_application_300_execution_evidence=evidence,
        )

    def with_databases_recreated(
        self,
        *,
        pinvi_database_identity: PinnedRuntimeDatabaseIdentity,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "reset_intent_durable":
            raise DeploymentContractError(
                "pinned runtime PinVi database identity is out of order"
            )
        return replace(
            self,
            phase="databases_recreated",
            journal_generation=self.journal_generation + 1,
            pinvi_database_identity=pinvi_database_identity,
            pinvi_role_catalog_reset=PinviRoleCatalogResetReceipt(state="intent"),
        )

    def with_application_create_intent(self) -> PinnedRuntimeRebuildJournal:
        if self.phase != "databases_recreated":
            raise DeploymentContractError(
                "Map application 300 create intent is out of order"
            )
        return replace(
            self,
            phase="application_create_intent_durable",
            journal_generation=self.journal_generation + 1,
        )

    def with_application_created(
        self,
        *,
        application_create_database_identity: (
            MapApplication300ApplicationDatabaseIdentity
        ),
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "application_create_intent_durable":
            raise DeploymentContractError(
                "Map application 300 create result is out of order"
            )
        evidence = (
            self.map_application_300_execution_evidence
            .with_application_create_database_identity(
                application_create_database_identity
            )
        )
        return replace(
            self,
            phase="application_created",
            journal_generation=self.journal_generation + 1,
            map_application_300_execution_evidence=evidence,
        )

    def with_application_bootstrap_intent(self) -> PinnedRuntimeRebuildJournal:
        if self.phase != "application_created":
            raise DeploymentContractError(
                "Map application 300 role bootstrap intent is out of order"
            )
        return replace(
            self,
            phase="application_bootstrap_intent_durable",
            journal_generation=self.journal_generation + 1,
        )

    def with_metadata_permit_ready(
        self,
        *,
        dagster_metadata_database_identity: MapApplication300DagsterMetadataDatabaseIdentity,
        metadata_permit_sha256: str,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "application_permit_ready":
            raise DeploymentContractError("Map application 300 metadata permit is out of order")
        evidence = (
            self.map_application_300_execution_evidence.with_dagster_metadata_database_identity(
                dagster_metadata_database_identity
            ).with_digest(metadata_permit_sha256=metadata_permit_sha256)
        )
        return replace(
            self,
            phase="metadata_permit_ready",
            journal_generation=self.journal_generation + 1,
            map_application_300_execution_evidence=evidence,
        )

    def with_fresh_root_plan_ready(
        self,
        *,
        fresh_root_operation_plan: MapApplication300OperationPlan,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "application_roles_ready":
            raise DeploymentContractError("Map application 300 root plan is out of order")
        _validate_operation_plan_basis(
            fresh_root_operation_plan,
            self,
            label="root",
        )
        evidence = self.map_application_300_execution_evidence.with_fresh_root_operation_plan(
            fresh_root_operation_plan
        )
        return replace(
            self,
            phase="fresh_root_plan_ready",
            journal_generation=self.journal_generation + 1,
            map_application_300_execution_evidence=evidence,
        )

    def with_fresh_root_fence_ready(
        self,
        *,
        fresh_root_operation_plan: MapApplication300OperationPlan,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "fresh_root_plan_ready":
            raise DeploymentContractError("Map application 300 root fence is out of order")
        _validate_operation_plan_object(fresh_root_operation_plan, "root")
        _validate_operation_plan_result_state(
            fresh_root_operation_plan,
            result_required=False,
        )
        _validate_same_pending_operation_plan(
            self.map_application_300_execution_evidence.fresh_root_operation_plan,
            fresh_root_operation_plan,
            label="root",
        )
        return replace(
            self,
            phase="fresh_root_fence_ready",
            journal_generation=self.journal_generation + 1,
        )

    def with_fresh_root_execution_intent(
        self,
        *,
        fresh_root_operation_plan: MapApplication300OperationPlan,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "fresh_root_fence_ready":
            raise DeploymentContractError("Map application 300 root execution intent is out of order")
        _validate_operation_plan_object(fresh_root_operation_plan, "root")
        _validate_operation_plan_result_state(
            fresh_root_operation_plan,
            result_required=False,
        )
        _validate_same_pending_operation_plan(
            self.map_application_300_execution_evidence.fresh_root_operation_plan,
            fresh_root_operation_plan,
            label="root",
        )
        return replace(
            self,
            phase="fresh_root_execution_intent",
            journal_generation=self.journal_generation + 1,
        )

    def with_renewed_fresh_root_execution_intent(
        self,
        *,
        fresh_root_operation_plan: MapApplication300OperationPlan,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "fresh_root_execution_intent":
            raise DeploymentContractError(
                "Map application 300 root renewal is out of order"
            )
        _validate_operation_plan_basis(
            fresh_root_operation_plan,
            self,
            label="root renewal",
        )
        evidence = (
            self.map_application_300_execution_evidence
            .with_renewed_fresh_root_operation_plan(fresh_root_operation_plan)
        )
        return replace(
            self,
            journal_generation=self.journal_generation + 1,
            map_application_300_execution_evidence=evidence,
        )

    def with_fresh_root_ready(
        self,
        *,
        fresh_root_operation_plan: MapApplication300OperationPlan,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "fresh_root_execution_intent":
            raise DeploymentContractError("Map application 300 root result is out of order")
        _validate_same_pending_operation_plan(
            self.map_application_300_execution_evidence.fresh_root_operation_plan,
            fresh_root_operation_plan,
            label="root",
        )
        evidence = self.map_application_300_execution_evidence.with_fresh_root_result(
            fresh_root_operation_plan
        )
        return replace(
            self,
            phase="fresh_root_ready",
            journal_generation=self.journal_generation + 1,
            map_application_300_execution_evidence=evidence,
        )

    def with_fresh_finalize_plan_ready(
        self,
        *,
        fresh_finalize_operation_plan: MapApplication300OperationPlan,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "fresh_root_ready":
            raise DeploymentContractError("Map application 300 finalize plan is out of order")
        _validate_operation_plan_basis(
            fresh_finalize_operation_plan,
            self,
            label="finalize",
        )
        evidence = (
            self.map_application_300_execution_evidence.with_fresh_finalize_operation_plan(
                fresh_finalize_operation_plan
            )
        )
        return replace(
            self,
            phase="fresh_finalize_plan_ready",
            journal_generation=self.journal_generation + 1,
            map_application_300_execution_evidence=evidence,
        )

    def with_fresh_finalize_fence_ready(
        self,
        *,
        fresh_finalize_operation_plan: MapApplication300OperationPlan,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "fresh_finalize_plan_ready":
            raise DeploymentContractError("Map application 300 finalize fence is out of order")
        _validate_operation_plan_object(fresh_finalize_operation_plan, "finalize")
        _validate_operation_plan_result_state(
            fresh_finalize_operation_plan,
            result_required=False,
        )
        _validate_same_pending_operation_plan(
            self.map_application_300_execution_evidence.fresh_finalize_operation_plan,
            fresh_finalize_operation_plan,
            label="finalize",
        )
        return replace(
            self,
            phase="fresh_finalize_fence_ready",
            journal_generation=self.journal_generation + 1,
        )

    def with_fresh_finalize_execution_intent(
        self,
        *,
        fresh_finalize_operation_plan: MapApplication300OperationPlan,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "fresh_finalize_fence_ready":
            raise DeploymentContractError(
                "Map application 300 finalize execution intent is out of order"
            )
        _validate_operation_plan_object(fresh_finalize_operation_plan, "finalize")
        _validate_operation_plan_result_state(
            fresh_finalize_operation_plan,
            result_required=False,
        )
        _validate_same_pending_operation_plan(
            self.map_application_300_execution_evidence.fresh_finalize_operation_plan,
            fresh_finalize_operation_plan,
            label="finalize",
        )
        return replace(
            self,
            phase="fresh_finalize_execution_intent",
            journal_generation=self.journal_generation + 1,
        )

    def with_renewed_fresh_finalize_execution_intent(
        self,
        *,
        fresh_finalize_operation_plan: MapApplication300OperationPlan,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "fresh_finalize_execution_intent":
            raise DeploymentContractError(
                "Map application 300 finalize renewal is out of order"
            )
        _validate_operation_plan_basis(
            fresh_finalize_operation_plan,
            self,
            label="finalize renewal",
        )
        evidence = (
            self.map_application_300_execution_evidence
            .with_renewed_fresh_finalize_operation_plan(
                fresh_finalize_operation_plan
            )
        )
        return replace(
            self,
            journal_generation=self.journal_generation + 1,
            map_application_300_execution_evidence=evidence,
        )

    def with_fresh_finalize_ready(
        self,
        *,
        fresh_finalize_operation_plan: MapApplication300OperationPlan,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "fresh_finalize_execution_intent":
            raise DeploymentContractError("Map application 300 finalize result is out of order")
        _validate_same_pending_operation_plan(
            self.map_application_300_execution_evidence.fresh_finalize_operation_plan,
            fresh_finalize_operation_plan,
            label="finalize",
        )
        evidence = self.map_application_300_execution_evidence.with_fresh_finalize_result(
            fresh_finalize_operation_plan
        )
        return replace(
            self,
            phase="fresh_finalize_ready",
            journal_generation=self.journal_generation + 1,
            map_application_300_execution_evidence=evidence,
        )

    def with_application_permit_ready(
        self,
        *,
        app_final_permit_sha256: str,
    ) -> PinnedRuntimeRebuildJournal:
        if self.phase != "fresh_finalize_ready":
            raise DeploymentContractError(
                "Map application 300 application permit is out of order"
            )
        evidence = self.map_application_300_execution_evidence.with_digest(
            app_final_permit_sha256=app_final_permit_sha256
        )
        return replace(
            self,
            phase="application_permit_ready",
            journal_generation=self.journal_generation + 1,
            map_application_300_execution_evidence=evidence,
        )

    def with_map_application_ready(self) -> PinnedRuntimeRebuildJournal:
        if self.phase != "metadata_permit_ready":
            raise DeploymentContractError("Map application 300 readiness is out of order")
        return replace(
            self,
            phase="map_application_ready",
            journal_generation=self.journal_generation + 1,
        )

    def with_cancel_probe(
        self,
        receipt: PinnedRuntimeCancelProbeReceipt,
    ) -> PinnedRuntimeRebuildJournal:
        if REBUILD_PHASES.index(self.phase) < REBUILD_PHASES.index("pinvi_api_ready"):
            raise DeploymentContractError("pinned runtime cancel probe ran before PinVi API readiness")
        current_index = _CANCEL_PROBE_STAGES.index(self.cancel_probe.stage)
        next_index = _CANCEL_PROBE_STAGES.index(receipt.stage)
        if next_index < current_index or next_index > current_index + 1:
            raise DeploymentContractError("pinned runtime cancel probe receipt regressed")
        if self.cancel_probe.stage != "uninitialized":
            if (
                receipt.job_id != self.cancel_probe.job_id
                or receipt.fixture_created_at != self.cancel_probe.fixture_created_at
            ):
                raise DeploymentContractError("pinned runtime cancel probe receipt identity drifted")
        if self.cancel_probe.stage in {
            "consumed",
            "finalize_post_attempted",
            "finalized",
        } and (
            receipt.cancellation_id != self.cancel_probe.cancellation_id
            or receipt.outcome != self.cancel_probe.outcome
            or receipt.fixture_consumed_at != self.cancel_probe.fixture_consumed_at
        ):
            raise DeploymentContractError("pinned runtime cancel probe receipt outcome drifted")
        if next_index == current_index and receipt != self.cancel_probe:
            raise DeploymentContractError("pinned runtime cancel probe receipt drifted")
        if receipt == self.cancel_probe:
            return self
        return replace(
            self,
            journal_generation=self.journal_generation + 1,
            cancel_probe=receipt,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "transaction_id": self.transaction_id,
            "phase": self.phase,
            "candidate": self.candidate.to_payload(),
            "map_application_300_candidate_evidence": (
                self.map_application_300_candidate_evidence.to_payload()
            ),
            "environment_sha256": self.environment_sha256,
            "compose_sha256": self.compose_sha256,
            "resolved_compose_sha256": self.resolved_compose_sha256,
            "created_at": self.created_at,
            "pinvi_database_identity": (
                None
                if self.pinvi_database_identity is None
                else self.pinvi_database_identity.to_payload()
            ),
            "journal_generation": self.journal_generation,
            "map_application_300_execution_evidence": (
                self.map_application_300_execution_evidence.to_payload()
            ),
            "cancel_probe": self.cancel_probe.to_payload(),
            "pinvi_role_credential_environment_rebind": (
                None
                if self.pinvi_role_credential_environment_rebind is None
                else self.pinvi_role_credential_environment_rebind.to_payload()
            ),
            "pinvi_role_catalog_reset": (
                None
                if self.pinvi_role_catalog_reset is None
                else self.pinvi_role_catalog_reset.to_payload()
            ),
            "pinvi_role_lifecycle_block": (
                None
                if self.pinvi_role_lifecycle_block is None
                else self.pinvi_role_lifecycle_block.to_payload()
            ),
        }


@dataclass(frozen=True)
class LegacyTombstoneEntry:
    """삭제 전 fsync한 legacy artifact의 path·content evidence."""

    relative_path: str
    sha256: str

    def __post_init__(self) -> None:
        if not _is_f1d_legacy_artifact_path(self.relative_path):
            raise DeploymentContractError("legacy tombstone path is invalid")
        if _SHA256.fullmatch(self.sha256) is None:
            raise DeploymentContractError("legacy tombstone digest is invalid")

    def to_payload(self) -> dict[str, str]:
        return {"relative_path": self.relative_path, "sha256": self.sha256}


@dataclass(frozen=True)
class LegacyTombstoneReceipt:
    """candidate-attested 뒤에만 쓰는 v8 legacy state 퇴역 receipt."""

    version: Literal[8]
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
            or any(not _is_f1d_legacy_artifact_path(path) for path in self.requested_paths)
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
        "map_application_300_candidate_evidence",
        "recorded_at",
    }
    if set(payload) != expected:
        raise DeploymentContractError("pinned runtime generation payload is invalid")
    string_fields = expected - {"map_application_300_candidate_evidence"}
    if any(not isinstance(payload.get(field), str) for field in string_fields):
        raise DeploymentContractError("pinned runtime generation payload is invalid")
    values = cast(Mapping[str, object], payload)
    return PinnedRuntimeGeneration(
        map_api_image_id=cast(str, values["map_api_image_id"]),
        map_ui_image_id=cast(str, values["map_ui_image_id"]),
        map_dagster_image_id=cast(str, values["map_dagster_image_id"]),
        map_dagster_daemon_image_id=cast(str, values["map_dagster_daemon_image_id"]),
        pinvi_api_image_id=cast(str, values["pinvi_api_image_id"]),
        pinvi_web_image_id=cast(str, values["pinvi_web_image_id"]),
        pinvi_dagster_image_id=cast(str, values["pinvi_dagster_image_id"]),
        map_source_revision=cast(str, values["map_source_revision"]),
        pinvi_source_revision=cast(str, values["pinvi_source_revision"]),
        map_application_head=cast(str, values["map_application_head"]),
        map_dagster_head=cast(str, values["map_dagster_head"]),
        pinvi_head=cast(str, values["pinvi_head"]),
        pinset_sha256=cast(str, values["pinset_sha256"]),
        map_application_300_candidate_evidence=(
            map_application_300_candidate_evidence_from_payload(
                values["map_application_300_candidate_evidence"]
            )
        ),
        recorded_at=cast(str, values["recorded_at"]),
    )


def manifest_from_payload(payload: object) -> PinnedRuntimeManifest:
    if not isinstance(payload, Mapping) or set(payload) != {"version", "active_generation"}:
        raise DeploymentContractError("pinned runtime manifest payload is invalid")
    version = payload.get("version")
    if type(version) is not int or version != _MANIFEST_VERSION:
        raise DeploymentContractError("pinned runtime manifest payload is invalid")
    return PinnedRuntimeManifest(
        version=6,
        active_generation=generation_from_payload(payload.get("active_generation")),
    )


def journal_from_payload(payload: object) -> PinnedRuntimeRebuildJournal:
    expected = {
        "version",
        "transaction_id",
        "phase",
        "candidate",
        "map_application_300_candidate_evidence",
        "environment_sha256",
        "compose_sha256",
        "resolved_compose_sha256",
        "created_at",
        "pinvi_database_identity",
        "journal_generation",
        "map_application_300_execution_evidence",
        "cancel_probe",
    }
    optional_keys = {
        "pinvi_role_credential_environment_rebind",
        "pinvi_role_catalog_reset",
        "pinvi_role_lifecycle_block",
    }
    if (
        not isinstance(payload, Mapping)
        or not expected.issubset(payload)
        or not set(payload).issubset(expected | optional_keys)
    ):
        raise DeploymentContractError("pinned runtime rebuild journal payload is invalid")
    version = payload.get("version")
    transaction_id = payload.get("transaction_id")
    phase = payload.get("phase")
    environment_sha256 = payload.get("environment_sha256")
    compose_sha256 = payload.get("compose_sha256")
    resolved_compose_sha256 = payload.get("resolved_compose_sha256")
    created_at = payload.get("created_at")
    journal_generation = payload.get("journal_generation")
    cancel_probe = payload.get("cancel_probe")
    if (
        type(version) is not int
        or version != _REBUILD_JOURNAL_VERSION
        or type(journal_generation) is not int
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
        version=8,
        transaction_id=cast(str, transaction_id),
        phase=cast(RebuildPhase, phase),
        candidate=generation_from_payload(payload.get("candidate")),
        map_application_300_candidate_evidence=(
            map_application_300_candidate_evidence_from_payload(
                payload.get("map_application_300_candidate_evidence")
            )
        ),
        environment_sha256=cast(str, environment_sha256),
        compose_sha256=cast(str, compose_sha256),
        resolved_compose_sha256=cast(str, resolved_compose_sha256),
        created_at=cast(str, created_at),
        pinvi_database_identity=(
            None
            if payload.get("pinvi_database_identity") is None
            else pinned_runtime_database_identity_from_payload(
                payload.get("pinvi_database_identity")
            )
        ),
        journal_generation=journal_generation,
        map_application_300_execution_evidence=(
            map_application_300_execution_evidence_from_payload(
                payload.get("map_application_300_execution_evidence")
            )
        ),
        cancel_probe=_cancel_probe_receipt_from_payload(cancel_probe),
        pinvi_role_credential_environment_rebind=(
            None
            if payload.get("pinvi_role_credential_environment_rebind") is None
            else pinvi_role_credential_environment_rebind_from_payload(
                payload.get("pinvi_role_credential_environment_rebind")
            )
        ),
        pinvi_role_catalog_reset=(
            None
            if payload.get("pinvi_role_catalog_reset") is None
            else pinvi_role_catalog_reset_from_payload(
                payload.get("pinvi_role_catalog_reset")
            )
        ),
        pinvi_role_lifecycle_block=(
            None
            if payload.get("pinvi_role_lifecycle_block") is None
            else pinvi_role_lifecycle_block_from_payload(
                payload.get("pinvi_role_lifecycle_block")
            )
        ),
    )


def pinvi_role_credential_environment_rebind_from_payload(
    payload: object,
) -> PinviRoleCredentialEnvironmentRebind:
    expected = {
        "previous_environment_sha256",
        "previous_resolved_compose_sha256",
        "current_environment_sha256",
        "current_resolved_compose_sha256",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != expected
        or not all(isinstance(value, str) for value in payload.values())
    ):
        raise DeploymentContractError(
            "PinVi role credential environment rebind receipt payload is invalid"
        )
    return PinviRoleCredentialEnvironmentRebind(
        previous_environment_sha256=cast(
            str, payload["previous_environment_sha256"]
        ),
        previous_resolved_compose_sha256=cast(
            str, payload["previous_resolved_compose_sha256"]
        ),
        current_environment_sha256=cast(
            str, payload["current_environment_sha256"]
        ),
        current_resolved_compose_sha256=cast(
            str, payload["current_resolved_compose_sha256"]
        ),
    )


def pinvi_role_lifecycle_block_from_payload(
    payload: object,
) -> PinviRoleLifecycleBlock:
    if (
        not isinstance(payload, Mapping)
        or set(payload) not in ({"stage", "code"}, {"stage", "code", "diagnostic"})
        or payload.get("stage")
        not in {
            "pinvi_role_catalog_reset",
            "pinvi_role_open",
            "pinvi_role_seal",
            "pinvi_role_verify",
        }
        or payload.get("code")
        not in {
            "role_catalog_reset_failed",
            "role_topology_noncanonical",
            "role_topology_unavailable",
        }
    ):
        raise DeploymentContractError("PinVi role lifecycle block payload is invalid")
    stage = cast(
        Literal[
            "pinvi_role_catalog_reset",
            "pinvi_role_open",
            "pinvi_role_seal",
            "pinvi_role_verify",
        ],
        payload["stage"],
    )
    code = cast(
        Literal[
            "role_catalog_reset_failed",
            "role_topology_noncanonical",
            "role_topology_unavailable",
        ],
        payload["code"],
    )
    diagnostic = cast(PinviRoleCatalogResetDiagnostic, payload.get("diagnostic", "unclassified"))
    if diagnostic not in PINVI_ROLE_CATALOG_RESET_DIAGNOSTICS:
        raise DeploymentContractError("PinVi role lifecycle block payload is invalid")
    if stage == "pinvi_role_catalog_reset" and code != "role_catalog_reset_failed":
        raise DeploymentContractError("PinVi role lifecycle block receipt payload is invalid")
    if stage not in {"pinvi_role_catalog_reset", "pinvi_role_verify"} and code != "role_topology_noncanonical":
        raise DeploymentContractError("PinVi role lifecycle block payload is invalid")
    if stage != "pinvi_role_catalog_reset" and diagnostic != "unclassified":
        raise DeploymentContractError("PinVi role lifecycle block payload is invalid")
    return PinviRoleLifecycleBlock(
        stage=stage,
        code=code,
        diagnostic=diagnostic,
    )


def pinvi_role_catalog_reset_from_payload(
    payload: object,
) -> PinviRoleCatalogResetReceipt:
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"state"}
        or payload.get("state") not in {"intent", "completed"}
    ):
        raise DeploymentContractError("PinVi role catalog reset receipt payload is invalid")
    return PinviRoleCatalogResetReceipt(
        state=cast(Literal["intent", "completed"], payload["state"])
    )


def map_application_300_candidate_evidence_from_payload(
    payload: object,
) -> MapApplication300CandidateEvidence:
    expected = {
        "paired_receipt_sha256",
        "api_receipt_sha256",
        "candidate_git_tree",
        "postgres_image_id",
        "dagster_config_sha256",
        "dagster_yaml_sha256",
        "application_contract_sha256",
        "launch_contract_sha256",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != expected
        or not all(isinstance(value, str) for value in payload.values())
    ):
        raise DeploymentContractError(
            "Map application 300 candidate evidence payload is invalid"
        )
    values = cast(Mapping[str, str], payload)
    return MapApplication300CandidateEvidence(**dict(values))


def map_application_300_execution_evidence_from_payload(
    payload: object,
) -> MapApplication300ExecutionEvidence:
    expected = {
        "application_create_database_identity",
        "application_create_database_identity_sha256",
        "application_database_identity",
        "application_database_identity_sha256",
        "fresh_root_operation_plan",
        "fresh_finalize_operation_plan",
        "app_final_permit_sha256",
        "dagster_metadata_database_identity",
        "dagster_metadata_database_identity_sha256",
        "metadata_permit_sha256",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DeploymentContractError(
            "Map application 300 execution evidence payload is invalid"
        )
    application_create_identity = payload.get(
        "application_create_database_identity"
    )
    application_identity = payload.get("application_database_identity")
    fresh_root_operation_plan = payload.get("fresh_root_operation_plan")
    fresh_finalize_operation_plan = payload.get("fresh_finalize_operation_plan")
    dagster_metadata_identity = payload.get("dagster_metadata_database_identity")
    digest_fields = expected - {
        "application_create_database_identity",
        "application_database_identity",
        "fresh_root_operation_plan",
        "fresh_finalize_operation_plan",
        "dagster_metadata_database_identity",
    }
    if any(
        value is not None and not isinstance(value, str)
        for key, value in payload.items()
        if key in digest_fields
    ):
        raise DeploymentContractError(
            "Map application 300 execution evidence payload is invalid"
        )
    values = cast(Mapping[str, str | None], payload)
    return MapApplication300ExecutionEvidence(
        application_create_database_identity=(
            None
            if application_create_identity is None
            else map_application_300_application_database_identity_from_payload(
                application_create_identity
            )
        ),
        application_create_database_identity_sha256=values[
            "application_create_database_identity_sha256"
        ],
        application_database_identity=(
            None
            if application_identity is None
            else map_application_300_application_database_identity_from_payload(
                application_identity
            )
        ),
        application_database_identity_sha256=values[
            "application_database_identity_sha256"
        ],
        fresh_root_operation_plan=(
            None
            if fresh_root_operation_plan is None
            else map_application_300_operation_plan_from_payload(
                fresh_root_operation_plan
            )
        ),
        fresh_finalize_operation_plan=(
            None
            if fresh_finalize_operation_plan is None
            else map_application_300_operation_plan_from_payload(
                fresh_finalize_operation_plan
            )
        ),
        app_final_permit_sha256=values["app_final_permit_sha256"],
        dagster_metadata_database_identity=(
            None
            if dagster_metadata_identity is None
            else map_application_300_dagster_metadata_database_identity_from_payload(
                dagster_metadata_identity
            )
        ),
        dagster_metadata_database_identity_sha256=values[
            "dagster_metadata_database_identity_sha256"
        ],
        metadata_permit_sha256=values["metadata_permit_sha256"],
    )


def map_application_300_operation_plan_from_payload(
    payload: object,
) -> MapApplication300OperationPlan:
    expected = {
        "transaction_id",
        "operation_id",
        "basis_journal_sha256",
        "basis_journal_generation",
        "writer_fence_expires_at",
        "fence_sha256",
        "result_sha256",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DeploymentContractError(
            "Map application 300 operation plan payload is invalid"
        )
    transaction_id = payload.get("transaction_id")
    operation_id = payload.get("operation_id")
    basis_journal_sha256 = payload.get("basis_journal_sha256")
    basis_journal_generation = payload.get("basis_journal_generation")
    writer_fence_expires_at = payload.get("writer_fence_expires_at")
    fence_sha256 = payload.get("fence_sha256")
    result_sha256 = payload.get("result_sha256")
    if (
        not isinstance(transaction_id, str)
        or not isinstance(operation_id, str)
        or not isinstance(basis_journal_sha256, str)
        or type(basis_journal_generation) is not int
        or not isinstance(writer_fence_expires_at, str)
        or not isinstance(fence_sha256, str)
        or (result_sha256 is not None and not isinstance(result_sha256, str))
    ):
        raise DeploymentContractError(
            "Map application 300 operation plan payload is invalid"
        )
    return MapApplication300OperationPlan(
        transaction_id=transaction_id,
        operation_id=operation_id,
        basis_journal_sha256=basis_journal_sha256,
        basis_journal_generation=basis_journal_generation,
        writer_fence_expires_at=writer_fence_expires_at,
        fence_sha256=fence_sha256,
        result_sha256=result_sha256,
    )


def map_application_300_application_database_identity_from_payload(
    payload: object,
) -> MapApplication300ApplicationDatabaseIdentity:
    expected = {
        "database_name",
        "database_oid",
        "database_owner",
        "postgres_system_identifier",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DeploymentContractError(
            "Map application 300 application database identity payload is invalid"
        )
    database_name = payload.get("database_name")
    database_oid = payload.get("database_oid")
    database_owner = payload.get("database_owner")
    postgres_system_identifier = payload.get("postgres_system_identifier")
    if (
        not isinstance(database_name, str)
        or type(database_oid) is not int
        or not isinstance(database_owner, str)
        or not isinstance(postgres_system_identifier, str)
    ):
        raise DeploymentContractError(
            "Map application 300 application database identity payload is invalid"
        )
    return MapApplication300ApplicationDatabaseIdentity(
        database_name=database_name,
        database_oid=database_oid,
        database_owner=database_owner,
        postgres_system_identifier=postgres_system_identifier,
    )


def pinned_runtime_database_identity_from_payload(
    payload: object,
) -> PinnedRuntimeDatabaseIdentity:
    expected = {"system_identifier", "name", "oid", "owner", "login_role"}
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DeploymentContractError(
            "pinned runtime database identity payload is invalid"
        )
    system_identifier = payload.get("system_identifier")
    name = payload.get("name")
    oid = payload.get("oid")
    owner = payload.get("owner")
    login_role = payload.get("login_role")
    if (
        not isinstance(system_identifier, str)
        or not isinstance(name, str)
        or type(oid) is not int
        or not isinstance(owner, str)
        or not isinstance(login_role, str)
    ):
        raise DeploymentContractError(
            "pinned runtime database identity payload is invalid"
        )
    return PinnedRuntimeDatabaseIdentity(
        system_identifier=system_identifier,
        name=name,
        oid=oid,
        owner=owner,
        login_role=login_role,
    )


def map_application_300_dagster_metadata_database_identity_from_payload(
    payload: object,
) -> MapApplication300DagsterMetadataDatabaseIdentity:
    expected = {
        "system_identifier",
        "name",
        "oid",
        "owner",
        "login_role",
        "login_role_attributes",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DeploymentContractError(
            "Map application 300 Dagster metadata identity payload is invalid"
        )
    system_identifier = payload.get("system_identifier")
    name = payload.get("name")
    oid = payload.get("oid")
    owner = payload.get("owner")
    login_role = payload.get("login_role")
    if (
        not isinstance(system_identifier, str)
        or not isinstance(name, str)
        or type(oid) is not int
        or not isinstance(owner, str)
        or not isinstance(login_role, str)
    ):
        raise DeploymentContractError(
            "Map application 300 Dagster metadata identity payload is invalid"
        )
    return MapApplication300DagsterMetadataDatabaseIdentity(
        system_identifier=system_identifier,
        name=name,
        oid=oid,
        owner=owner,
        login_role=login_role,
        login_role_attributes=(
            map_application_300_dagster_metadata_role_attributes_from_payload(
                payload.get("login_role_attributes")
            )
        ),
    )


def map_application_300_dagster_metadata_role_attributes_from_payload(
    payload: object,
) -> MapApplication300DagsterMetadataRoleAttributes:
    expected = {
        "can_login",
        "inherit",
        "superuser",
        "create_database",
        "create_role",
        "replication",
        "bypass_rls",
        "connection_limit",
        "valid_until_is_null",
        "role_config_count",
        "database_role_setting_count",
        "granted_role_count",
        "member_role_count",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DeploymentContractError(
            "Map application 300 Dagster metadata role attributes payload is invalid"
        )
    booleans = (
        payload.get("can_login"),
        payload.get("inherit"),
        payload.get("superuser"),
        payload.get("create_database"),
        payload.get("create_role"),
        payload.get("replication"),
        payload.get("bypass_rls"),
        payload.get("valid_until_is_null"),
    )
    counts = (
        payload.get("connection_limit"),
        payload.get("role_config_count"),
        payload.get("database_role_setting_count"),
        payload.get("granted_role_count"),
        payload.get("member_role_count"),
    )
    if any(type(value) is not bool for value in booleans) or any(
        type(value) is not int for value in counts
    ):
        raise DeploymentContractError(
            "Map application 300 Dagster metadata role attributes payload is invalid"
        )
    return MapApplication300DagsterMetadataRoleAttributes(
        can_login=cast(bool, payload["can_login"]),
        inherit=cast(bool, payload["inherit"]),
        superuser=cast(bool, payload["superuser"]),
        create_database=cast(bool, payload["create_database"]),
        create_role=cast(bool, payload["create_role"]),
        replication=cast(bool, payload["replication"]),
        bypass_rls=cast(bool, payload["bypass_rls"]),
        connection_limit=cast(int, payload["connection_limit"]),
        valid_until_is_null=cast(bool, payload["valid_until_is_null"]),
        role_config_count=cast(int, payload["role_config_count"]),
        database_role_setting_count=cast(
            int, payload["database_role_setting_count"]
        ),
        granted_role_count=cast(int, payload["granted_role_count"]),
        member_role_count=cast(int, payload["member_role_count"]),
    )


def _validate_application_300_phase_evidence(
    phase: RebuildPhase,
    evidence: MapApplication300ExecutionEvidence,
) -> None:
    required = set(_application_300_required_evidence_fields(phase))
    for field_name in _APPLICATION_300_EVIDENCE_FIELDS:
        value = getattr(evidence, field_name)
        if field_name in required:
            if value is None:
                raise DeploymentContractError(
                    "Map application 300 phase lacks required evidence"
                )
        elif value is not None:
            raise DeploymentContractError(
                "Map application 300 phase has future evidence"
            )
    phase_index = REBUILD_PHASES.index(phase)
    if evidence.fresh_root_operation_plan is not None:
        if phase in {
            "fresh_root_plan_ready",
            "fresh_root_fence_ready",
            "fresh_root_execution_intent",
        }:
            _validate_operation_plan_result_state(
                evidence.fresh_root_operation_plan,
                result_required=False,
            )
        elif phase_index >= REBUILD_PHASES.index("fresh_root_ready"):
            _validate_operation_plan_result_state(
                evidence.fresh_root_operation_plan,
                result_required=True,
            )
    if evidence.fresh_finalize_operation_plan is not None:
        if phase in {
            "fresh_finalize_plan_ready",
            "fresh_finalize_fence_ready",
            "fresh_finalize_execution_intent",
        }:
            _validate_operation_plan_result_state(
                evidence.fresh_finalize_operation_plan,
                result_required=False,
            )
        elif phase_index >= REBUILD_PHASES.index("fresh_finalize_ready"):
            _validate_operation_plan_result_state(
                evidence.fresh_finalize_operation_plan,
                result_required=True,
            )


def _application_300_required_evidence_fields(phase: RebuildPhase) -> tuple[str, ...]:
    phase_index = REBUILD_PHASES.index(phase)
    if phase_index < REBUILD_PHASES.index("application_created"):
        return ()
    fields: list[str] = [
        "application_create_database_identity",
        "application_create_database_identity_sha256",
    ]
    if phase_index < REBUILD_PHASES.index("application_roles_ready"):
        return tuple(fields)
    fields.extend(
        [
        "application_database_identity",
        "application_database_identity_sha256",
        ]
    )
    if phase_index >= REBUILD_PHASES.index("fresh_root_plan_ready"):
        fields.append("fresh_root_operation_plan")
    if phase_index >= REBUILD_PHASES.index("fresh_finalize_plan_ready"):
        fields.append("fresh_finalize_operation_plan")
    if phase_index >= REBUILD_PHASES.index("application_permit_ready"):
        fields.append("app_final_permit_sha256")
    if phase_index >= REBUILD_PHASES.index("metadata_permit_ready"):
        fields.extend(
            (
                "dagster_metadata_database_identity",
                "dagster_metadata_database_identity_sha256",
                "metadata_permit_sha256",
            )
        )
    return tuple(fields)


def _cancel_probe_receipt_from_payload(
    payload: object,
) -> PinnedRuntimeCancelProbeReceipt:
    if not isinstance(payload, Mapping) or set(payload) != {
        "stage",
        "job_id",
        "cancellation_id",
        "outcome",
        "fixture_created_at",
        "fixture_consumed_at",
        "fixture_finalized_at",
    }:
        raise DeploymentContractError("pinned runtime cancel probe receipt is invalid")
    stage = payload.get("stage")
    job_id = payload.get("job_id")
    cancellation_id = payload.get("cancellation_id")
    outcome_payload = payload.get("outcome")
    fixture_created_at = payload.get("fixture_created_at")
    fixture_consumed_at = payload.get("fixture_consumed_at")
    fixture_finalized_at = payload.get("fixture_finalized_at")
    if (
        not isinstance(stage, str)
        or (job_id is not None and not isinstance(job_id, str))
        or (cancellation_id is not None and not isinstance(cancellation_id, str))
        or (fixture_created_at is not None and not isinstance(fixture_created_at, str))
        or (fixture_consumed_at is not None and not isinstance(fixture_consumed_at, str))
        or (fixture_finalized_at is not None and not isinstance(fixture_finalized_at, str))
    ):
        raise DeploymentContractError("pinned runtime cancel probe receipt is invalid")
    outcome: PinnedRuntimeCancelProbeOutcome | None
    if outcome_payload is None:
        outcome = None
    elif (
        isinstance(outcome_payload, Mapping)
        and set(outcome_payload) == {"name", "status", "code"}
        and isinstance(outcome_payload.get("name"), str)
        and type(outcome_payload.get("status")) is int
        and isinstance(outcome_payload.get("code"), str)
    ):
        outcome = PinnedRuntimeCancelProbeOutcome(
            name=cast(Literal["pinvi_cancel_error"], outcome_payload["name"]),
            status=cast(Literal[409], outcome_payload["status"]),
            code=cast(
                Literal["PIPELINE_CANCELLATION_UNSAFE"],
                outcome_payload["code"],
            ),
        )
    else:
        raise DeploymentContractError("pinned runtime cancel probe receipt is invalid")
    return PinnedRuntimeCancelProbeReceipt(
        stage=cast(CancelProbeStage, stage),
        job_id=job_id,
        cancellation_id=cancellation_id,
        outcome=outcome,
        fixture_created_at=fixture_created_at,
        fixture_consumed_at=fixture_consumed_at,
        fixture_finalized_at=fixture_finalized_at,
    )


def read_manifest(path: Path) -> PinnedRuntimeManifest:
    return manifest_from_payload(_read_private_json(path, "pinned runtime manifest"))


def write_manifest(path: Path, manifest: PinnedRuntimeManifest) -> None:
    _write_private_json(path, manifest.to_payload(), "pinned runtime manifest")
    try:
        publish_pinned_runtime_generation(manifest=manifest, private_path=path)
    except OSError as exc:
        raise DeploymentContractError(
            "pinned runtime manifest was written but its public copy could not be updated"
        ) from exc


def read_rebuild_journal(path: Path) -> PinnedRuntimeRebuildJournal:
    return journal_from_payload(_read_private_json(path, "pinned runtime rebuild journal"))


def write_rebuild_journal(path: Path, journal: PinnedRuntimeRebuildJournal) -> None:
    _write_private_json(path, journal.to_payload(), "pinned runtime rebuild journal")
    try:
        publish_pinned_runtime_generation(journal=journal, private_path=path)
    except OSError as exc:
        raise DeploymentContractError(
            "pinned runtime rebuild journal was written but its public copy could not be updated"
        ) from exc


def pinned_runtime_public_paths(*, private_path: Path | None = None) -> PinnedRuntimePublicPaths:
    """generation 관측 API가 읽을 공개 사본 경로를 반환한다.

    설치본은 release 교체에 살아남는 `/var/lib` 경로를 쓴다. 개발 테스트처럼 trusted
    install 밖에서 private path를 직접 넘긴 경우에는 그 path 옆의 임시 공개 디렉터리를
    사용해, 테스트가 호스트 전역 state를 만들지 않게 한다. custom state root를 쓰는
    실제 배포는 반드시 `KTDM_PINNED_RUNTIME_PUBLIC_ROOT`를 함께 지정한다.
    """

    configured = os.environ.get(_PUBLIC_ROOT_ENV, "").strip()
    if configured:
        root = Path(configured)
        if not root.is_absolute() or root != root.resolve(strict=False):
            raise DeploymentContractError(
                "KTDM_PINNED_RUNTIME_PUBLIC_ROOT must be a canonical absolute path"
            )
    elif private_path is not None and not _running_from_trusted_install_root():
        root = private_path.parent / ".ktdm-pinned-runtime-public"
    else:
        root = _DEFAULT_PUBLIC_ROOT
    return PinnedRuntimePublicPaths(
        manifest=root / _MANIFEST_FILENAME,
        journal=root / _PUBLIC_JOURNAL_FILENAME,
    )


def publish_pinned_runtime_generation(
    *,
    manifest: PinnedRuntimeManifest | None = None,
    journal: PinnedRuntimeRebuildJournal | None = None,
    private_path: Path | None = None,
) -> PinnedRuntimePublicPaths:
    """검증된 private v6/v8 원본을 backend 가독 사본으로 원자 복제한다.

    이 함수는 private 파일을 다시 읽지 않는다. caller가 typed model로 이미 검증한
    payload만 받아서 쓰므로 symlink·mode가 다른 private artifact를 API에 중계할 여지가
    없다. 한 번에 둘 다 주면 같은 public root로 각각 atomic replace한다. 파일 두 개의
    교체 사이를 API가 읽으면 summary가 `재구축 진행 중` 또는 `정합성 확인 필요`로
    fail-close하며, raw JSON schema 자체는 절대 바꾸지 않는다.
    """

    if manifest is None and journal is None:
        raise DeploymentContractError("pinned runtime public publication needs an artifact")
    paths = pinned_runtime_public_paths(private_path=private_path)
    if manifest is not None:
        _write_public_json(paths.manifest, manifest.to_payload())
    if journal is not None:
        _write_public_json(paths.journal, journal.to_payload())
    return paths


def read_published_pinned_runtime_generation() -> dict[str, object]:
    """backend 전용 public-copy reader.

    root-owned private state의 경로·권한을 우회하지 않는다. 사본이 없거나 schema가 틀리면
    원문/경로를 노출하지 않고 `unknown`으로 끝낸다.
    """

    paths = pinned_runtime_public_paths()
    manifest: PinnedRuntimeManifest | None = None
    journal: PinnedRuntimeRebuildJournal | None = None
    for label, path, parser in (
        ("manifest", paths.manifest, manifest_from_payload),
        ("journal", paths.journal, journal_from_payload),
    ):
        try:
            raw = _read_public_json(path, f"pinned runtime {label} public copy")
            parsed = parser(raw)
        except DeploymentContractError:
            continue
        if label == "manifest":
            manifest = cast(PinnedRuntimeManifest, parsed)
        else:
            journal = cast(PinnedRuntimeRebuildJournal, parsed)

    # manifest와 journal은 같은 committed generation을 증명하는 한 쌍이다. 하나만
    # 공개됐거나 한쪽이 깨졌다면 raw 한 조각을 API로 돌려 주지 않는다. 부분 사본을
    # complete generation으로 오인하면 terminal pinset 재시도 gate가 무력화된다.
    if manifest is None or journal is None:
        return {
            "status": "unknown",
            "source": "published_copy",
            "detail": "pinned runtime generation public copy is incomplete or invalid",
            "manifest": None,
            "journal": None,
            "pinset_binding": _published_generation_pinset_binding(
                manifest=None, journal=None
            ),
            "terminal": None,
            "summary": _published_generation_summary(
                manifest=None,
                journal=None,
                pinset_binding="unknown",
            ),
        }

    if manifest.active_generation != journal.candidate:
        return {
            "status": "unverified",
            "source": "published_copy",
            "detail": "published manifest and rebuild journal generation differ",
            "manifest": manifest.to_payload(),
            "journal": journal.to_payload(),
            "pinset_binding": _published_generation_pinset_binding(
                manifest=manifest, journal=journal
            ),
            "terminal": None,
            "summary": _published_generation_summary(
                manifest=manifest,
                journal=journal,
                pinset_binding="unknown",
            ),
        }

    pinset_binding = _published_generation_pinset_binding(
        manifest=manifest, journal=journal
    )
    terminal: dict[str, str] | None = None
    if journal is not None and journal.pinvi_role_lifecycle_block is not None:
        terminal = {
            "class": "pinvi_role_lifecycle_block",
            "subclass": journal.pinvi_role_lifecycle_block.diagnostic,
            "pinset_sha256": journal.candidate.pinset_sha256,
        }
    return {
        "status": "ok",
        "source": "published_copy",
        "manifest": None if manifest is None else manifest.to_payload(),
        "journal": None if journal is None else journal.to_payload(),
        "pinset_binding": pinset_binding,
        "terminal": terminal,
        "summary": _published_generation_summary(
            manifest=manifest,
            journal=journal,
            pinset_binding=cast(str, pinset_binding["status"]),
        ),
    }


def _published_generation_pinset_binding(
    *,
    manifest: PinnedRuntimeManifest | None,
    journal: PinnedRuntimeRebuildJournal | None,
) -> dict[str, str | None]:
    """public registry와 generation 후보의 Map·PinVi pair 결박을 비교한다.

    `runtime-pins`가 unknown/stale/degraded이면 generation API가 값을 추측해 match라고
    말하지 않는다. 아직 journal이 없는 신규 rotation은 `pending_rebuild`, current candidate
    journal이 다른 pair면 `drift`로 나눠 사람이 one-shot을 잘못 시작하지 않게 한다.
    """

    if (
        manifest is None
        or journal is None
        or manifest.active_generation != journal.candidate
    ):
        return {
            "status": "unknown",
            "registry_pinset_sha256": None,
            "generation_pinset_sha256": None,
        }
    from kor_travel_docker_manager.services.runtime_pin_registry import (
        read_published_runtime_pins,
    )

    payload = read_published_runtime_pins()
    if payload.get("status") != "ok":
        return {
            "status": "unknown",
            "registry_pinset_sha256": None,
            "generation_pinset_sha256": _generation_for_public_binding(
                manifest=manifest, journal=journal
            ).pinset_sha256,
        }
    sources = payload.get("sources")
    if not isinstance(sources, list):
        return {
            "status": "unknown",
            "registry_pinset_sha256": None,
            "generation_pinset_sha256": _generation_for_public_binding(
                manifest=manifest, journal=journal
            ).pinset_sha256,
        }
    revisions = {
        entry.get("role"): entry.get("revision")
        for entry in sources
        if isinstance(entry, Mapping)
    }
    generation = _generation_for_public_binding(manifest=manifest, journal=journal)
    registry_pinset = payload.get("pinset_sha256")
    if (
        not isinstance(registry_pinset, str)
        or revisions.get("map") != generation.map_source_revision
        or revisions.get("pinvi") != generation.pinvi_source_revision
        or registry_pinset != generation.pinset_sha256
    ):
        return {
            # 이전 candidate의 terminal은 typed role receipt만으로 표현되지 않는다.
            # launcher/HTTP/preflight 계열은 root registry의 exact unconditional block이
            # terminal 정본이다. 새 atomic pair로 회전한 뒤 그런 old generation을
            # `drift`로 남기면 `pin verify`가 1로 끝나 정상적인 새 one-shot까지 막힌다.
            # strict public copy인 old committed 또는 exact unconditional terminal
            # generation만 pending으로 보존하고, partial·mismatched·phase-scoped block·
            # 현재 candidate의 비terminal 중단은 drift다.
            "status": (
                "pending_rebuild"
                if journal is not None
                and (
                    journal.phase == "committed"
                    or journal.pinvi_role_lifecycle_block is not None
                    or _is_unconditionally_blocked_public_generation(
                        payload=payload,
                        generation=generation,
                    )
                )
                else "drift"
            ),
            "registry_pinset_sha256": registry_pinset if isinstance(registry_pinset, str) else None,
            "generation_pinset_sha256": generation.pinset_sha256,
        }
    return {
        "status": "match",
        "registry_pinset_sha256": registry_pinset,
        "generation_pinset_sha256": generation.pinset_sha256,
    }


def _is_unconditionally_blocked_public_generation(
    *,
    payload: Mapping[str, object],
    generation: PinnedRuntimeGeneration,
) -> bool:
    """공개 registry가 generation의 모든 재실행을 차단했는지 exact로 판정한다.

    이 helper는 registry reader가 strict parse한 공개 payload만 받는다. phase가 있는
    block은 특정 재개만 막으므로 새 pair 회전 뒤에도 `pending_rebuild` 근거가 아니다.
    """

    blocked_pinsets = payload.get("blocked_pinsets")
    if not isinstance(blocked_pinsets, list):
        return False
    return any(
        isinstance(entry, Mapping)
        and entry.get("phase") is None
        and entry.get("pinset_sha256") == generation.pinset_sha256
        and entry.get("map_revision") == generation.map_source_revision
        and entry.get("pinvi_revision") == generation.pinvi_source_revision
        for entry in blocked_pinsets
    )


def _generation_for_public_binding(
    *,
    manifest: PinnedRuntimeManifest | None,
    journal: PinnedRuntimeRebuildJournal | None,
) -> PinnedRuntimeGeneration:
    if journal is not None:
        return journal.candidate
    if manifest is not None:
        return manifest.active_generation
    raise DeploymentContractError("published generation binding has no generation")


def _published_generation_summary(
    *,
    manifest: PinnedRuntimeManifest | None,
    journal: PinnedRuntimeRebuildJournal | None,
    pinset_binding: str,
) -> dict[str, object]:
    """raw v6/v8 원본을 바꾸지 않는 API envelope의 인간용 요약."""

    if journal is None and manifest is None:
        return {
            "state": "unknown",
            "text": "공개된 pinned runtime 세대 기록이 없습니다.",
            "next_action": (
                "sudo -n backend/.venv/bin/ktdctl pin publish-generation "
                "--manifest <absolute-v6-path> --journal <absolute-v8-path> --confirm"
            ),
            "manifest_version": None,
            "journal_version": None,
        }
    if manifest is not None and journal is not None and (
        manifest.active_generation != journal.candidate
    ):
        return {
            "state": "unverified",
            "text": "공개된 manifest와 rebuild journal의 세대가 일치하지 않습니다.",
            "next_action": (
                "sudo -n backend/.venv/bin/ktdctl pin publish-generation "
                "--manifest <absolute-v6-path> --journal <absolute-v8-path> --confirm"
            ),
            "manifest_version": manifest.version,
            "journal_version": journal.version,
        }
    if pinset_binding in {"pending_rebuild", "drift", "unknown"}:
        return {
            "state": "pending_rebuild" if pinset_binding == "pending_rebuild" else "unverified",
            "text": (
                "현재 pinset은 새 재구축을 기다리고 있습니다."
                if pinset_binding == "pending_rebuild"
                else "현재 registry와 공개 generation의 Map·PinVi pair 결박을 확인할 수 없습니다."
            ),
            "next_action": "" if pinset_binding == "pending_rebuild" else "sudo -n backend/.venv/bin/ktdctl pin verify",
            "manifest_version": None if manifest is None else manifest.version,
            "journal_version": None if journal is None else journal.version,
        }
    if journal is not None and journal.pinvi_role_lifecycle_block is not None:
        return {
            "state": "action_required",
            "text": "PinVi role lifecycle 차단 기록이 있어 이 pinset은 재시도할 수 없습니다.",
            "next_action": "ktdctl pin rotate-pair --map-revision <40-hex> --pinvi-revision <40-hex> --reason <reason> --confirm",
            "manifest_version": None if manifest is None else manifest.version,
            "journal_version": journal.version,
        }
    if journal is not None and journal.phase != "committed":
        position = REBUILD_PHASES.index(journal.phase) + 1
        return {
            "state": "rebuilding",
            "text": f"재구축 진행 중 ({position}/{len(REBUILD_PHASES)} 단계)",
            "next_action": "",
            "manifest_version": None if manifest is None else manifest.version,
            "journal_version": journal.version,
        }
    return {
        "state": "committed",
        "text": "고정된 runtime 세대가 커밋되어 있습니다.",
        "next_action": "",
        "manifest_version": None if manifest is None else manifest.version,
        "journal_version": None if journal is None else journal.version,
    }


def rebuild_journal_sha256(journal: PinnedRuntimeRebuildJournal) -> str:
    """Canonical v8 journal bytes digest used by application 300 writer fences."""

    raw = (
        json.dumps(
            journal.to_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def f1d_legacy_artifact_paths(
    *, pinset_sha256: str | None = None
) -> tuple[str, ...]:
    """C2 preflight가 퇴역할 수 있는 유일한 historical state file 이름."""

    if pinset_sha256 is None:
        return tuple(sorted(_F1D_LEGACY_ARTIFACTS))
    return _default_f1d_legacy_artifact_paths(pinset_sha256)


def legacy_tombstone_receipt_path(
    state_root: Path,
    *,
    pinset_sha256: str,
) -> Path:
    """pinset별 v8 tombstone receipt path를 반환한다."""

    if _SHA256.fullmatch(pinset_sha256) is None:
        raise DeploymentContractError("legacy tombstone pinset digest is invalid")
    return state_root / f"{_TOMBSTONE_FILENAME_PREFIX}{pinset_sha256}.json"


def retire_f1d_legacy_artifacts(
    *,
    state_root: Path,
    transaction_id: str,
    candidate: PinnedRuntimeGeneration,
    requested_paths: tuple[str, ...] | None = None,
    recorded_at: str,
) -> LegacyTombstoneReceipt:
    """candidate attest 뒤 legacy state를 receipt-first·fail-close로 퇴역한다.

    호출자는 candidate journal을 이미 fsync한 뒤에만 이 함수를 실행해야 한다. 이 함수는
    Docker/Compose/DB를 건드리지 않으며, receipt가 durable해진 뒤에만 allowlist file을 unlink한다.
    """

    _validate_state_root(state_root)
    normalized_paths = _normalize_legacy_paths(
        _default_f1d_legacy_artifact_paths(candidate.pinset_sha256)
        if requested_paths is None
        else requested_paths
    )
    receipt_path = legacy_tombstone_receipt_path(
        state_root,
        pinset_sha256=candidate.pinset_sha256,
    )
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
        version=8,
        transaction_id=transaction_id,
        candidate_generation_sha256=expected_generation_sha256,
        requested_paths=normalized_paths,
        retired=entries,
        recorded_at=recorded_at,
    )
    _write_private_json(receipt_path, receipt.to_payload(), "legacy tombstone receipt")
    _unlink_receipted_legacy_artifacts(state_root=state_root, receipt=receipt)
    return receipt


def _parse_utc_timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DeploymentContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise DeploymentContractError(f"{label} is invalid")
    return parsed


def _validate_utc_timestamp(value: str, label: str) -> None:
    _parse_utc_timestamp(value, label)


def _validate_canonical_uuid(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise DeploymentContractError(f"{label} is invalid")
    try:
        canonical = str(uuid.UUID(value))
    except ValueError as exc:
        raise DeploymentContractError(f"{label} is invalid") from exc
    if canonical != value:
        raise DeploymentContractError(f"{label} is not canonical")


def _normalize_legacy_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    if not paths:
        raise DeploymentContractError("legacy tombstone requested paths are invalid")
    normalized = tuple(sorted(paths))
    if (
        normalized != paths
        or len(set(paths)) != len(paths)
        or any(not _is_f1d_legacy_artifact_path(path) for path in paths)
    ):
        raise DeploymentContractError("legacy tombstone requested paths are invalid")
    return normalized


def _default_f1d_legacy_artifact_paths(pinset_sha256: str) -> tuple[str, ...]:
    if _SHA256.fullmatch(pinset_sha256) is None:
        raise DeploymentContractError("legacy tombstone pinset digest is invalid")
    return tuple(
        sorted(
            (
                *_F1D_LEGACY_ARTIFACTS,
                f"pinned-runtime-rebuild-v7-{pinset_sha256}.json",
                f"legacy-tombstone-v7-{pinset_sha256}.json",
            )
        )
    )


def _is_f1d_legacy_artifact_path(path: str) -> bool:
    return path in _F1D_LEGACY_ARTIFACTS or _F1D_PINSET_LEGACY_ARTIFACT.fullmatch(path) is not None


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
        version=8,
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
    except OSError as exc:
        raise DeploymentContractError(f"{label} cannot be written") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    # GM-10 후속(적대적 리뷰 발견): 디렉터리 fsync는 os.replace가 이미 성공한
    # 뒤의 추가 durability 보장일 뿐이다 — 예전에는 이 호출이 위 try 안에 있어서
    # fsync만 실패해도 이미 끝난 쓰기를 "쓸 수 없음"으로 잘못 보고했다(runtime_pair_rotation.py에서
    # 고친 것과 같은 버그 계열). 여기서는 실패를 조용히 삼킨다.
    try:
        _fsync_directory(path.parent)
    except OSError:
        pass


def _write_public_json(path: Path, payload: Mapping[str, object]) -> None:
    """world-readable state 사본을 atomic replace한다.

    공개본은 endpoint가 root state에 닿지 않고 읽는 유일한 경로다. payload는 이미
    typed constructor를 통과했으므로 여기서는 schema를 확장하거나 metadata를 삽입하지
    않는다. 디렉터리는 traverse 가능한 0755, 파일은 0644로 고정한다.
    """

    raw = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")
    if len(raw) > _MAX_STATE_BYTES:
        raise DeploymentContractError("pinned runtime public copy is too large")
    if path.name not in {_MANIFEST_FILENAME, _PUBLIC_JOURNAL_FILENAME}:
        raise DeploymentContractError("pinned runtime public copy filename is invalid")
    directory = _open_public_state_directory(path.parent, create=True)
    temporary_name: str | None = None
    try:
        try:
            _validate_existing_public_file(directory, path.name)
            temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
                dir_fd=directory,
            )
            try:
                os.fchmod(descriptor, 0o644)
                _write_all(descriptor, raw)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
            temporary_name = None
        except OSError as exc:
            raise DeploymentContractError(
                "pinned runtime public copy cannot be written"
            ) from exc
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        # GM-10 후속(적대적 리뷰 발견): os.replace가 이미 성공한 뒤의 디렉터리
        # fsync 실패를 "쓸 수 없음"으로 잘못 보고하지 않는다 — 위 블록에서 예외 없이
        # 여기 도달했다는 것 자체가 replace 성공을 뜻한다.
        try:
            os.fsync(directory)
        except OSError:
            pass
    finally:
        os.close(directory)


def _read_public_json(path: Path, label: str) -> object:
    """public copy를 symlink·권한·크기 검증 뒤 읽는다."""

    try:
        directory = _open_public_state_directory(path.parent, create=False)
    except DeploymentContractError:
        raise
    try:
        try:
            before = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            raise DeploymentContractError(f"{label} is missing") from None
        except OSError as exc:
            raise DeploymentContractError(f"{label} cannot be inspected") from exc
        _validate_public_file_stat(before, label)
        descriptor: int | None = None
        try:
            try:
                descriptor = os.open(
                    path.name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory,
                )
            except OSError as exc:
                raise DeploymentContractError(f"{label} cannot be opened safely") from exc
            after = os.fstat(descriptor)
            _validate_public_file_stat(after, label)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise DeploymentContractError(f"{label} changed during read")
            raw = _read_bounded(descriptor, label)
        finally:
            if descriptor is not None:
                os.close(descriptor)
    finally:
        os.close(directory)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentContractError(f"{label} is invalid") from exc


def _open_public_state_directory(path: Path, *, create: bool) -> int:
    """root-owned public state root를 no-follow directory FD로 연다.

    공개 파일은 world-readable여도 되지만, 그 부모를 group/other가 쓸 수 있으면 root
    publisher의 ``replace`` 대상이 바뀔 수 있다. 디렉터리 검사와 파일 생성·교체를 같은
    FD에 묶어 symlink 및 경로 TOCTOU를 fail-close한다.
    """

    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise DeploymentContractError("pinned runtime public copy parent is unavailable") from exc
    _validate_public_directory_stat(parent, "pinned runtime public copy parent", exact_mode=False)
    created = False
    try:
        before = path.lstat()
    except FileNotFoundError:
        if not create:
            raise DeploymentContractError("pinned runtime public copy directory is missing") from None
        try:
            path.mkdir(mode=0o755)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise DeploymentContractError("pinned runtime public copy directory cannot be created") from exc
        try:
            before = path.lstat()
        except OSError as exc:
            raise DeploymentContractError("pinned runtime public copy directory is unavailable") from exc
    _validate_public_directory_stat(
        before,
        "pinned runtime public copy directory",
        exact_mode=not created,
    )
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DeploymentContractError("pinned runtime public copy directory cannot be opened safely") from exc
    after = os.fstat(descriptor)
    try:
        if created:
            os.fchmod(descriptor, 0o755)
            after = os.fstat(descriptor)
        _validate_public_directory_stat(after, "pinned runtime public copy directory", exact_mode=True)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise DeploymentContractError("pinned runtime public copy directory changed during open")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _validate_public_directory_stat(
    file_stat: os.stat_result,
    label: str,
    *,
    exact_mode: bool,
) -> None:
    mode = stat.S_IMODE(file_stat.st_mode)
    if (
        not stat.S_ISDIR(file_stat.st_mode)
        or file_stat.st_uid not in {0, os.geteuid()}
        or (mode != 0o755 if exact_mode else mode & 0o022)
    ):
        raise DeploymentContractError(f"{label} is unsafe")


def _validate_public_file_stat(file_stat: os.stat_result, label: str) -> None:
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(file_stat.st_mode) != 0o644
        or file_stat.st_nlink != 1
        or file_stat.st_size > _MAX_STATE_BYTES
    ):
        raise DeploymentContractError(f"{label} is unsafe")


def _validate_existing_public_file(directory: int, name: str) -> None:
    try:
        observed = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DeploymentContractError("pinned runtime public copy cannot be inspected") from exc
    _validate_public_file_stat(observed, "pinned runtime public copy")


def _write_all(descriptor: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        written = os.write(descriptor, raw[offset:])
        if written <= 0:
            raise OSError("pinned runtime public copy write failed")
        offset += written


def _running_from_trusted_install_root() -> bool:
    """설치 tree에서는 public state를 release 밖 `/var/lib`에 고정한다.

    GM-09: 이 모듈만 `__file__` 상대경로 확인 하나였다 — services/trusted_install.py의
    running_from_trusted_install_root가 그 확인을 포함해 sys.prefix·
    get_project_root() 비교까지 셋을 모두 확인하는 정본이다(OR 결합이라 이 모듈이
    이미 잡던 경우를 그대로 포함하고 잃지 않는다).
    """

    return running_from_trusted_install_root()


def _validate_state_parent(path: Path, label: str) -> None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        path.mkdir(mode=0o700, parents=True)
        _fsync_directory(path.parent)
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
