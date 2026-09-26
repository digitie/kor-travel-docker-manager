"""pinned runtime generation의 typed 모델과 rehearsal 배포 mode·state 경로.

ADR-51 뒤 배포 진행의 정본은 ``deploy_status.py``의 ``deploy-status.json``이다. 이
모듈은 후보·active generation의 in-memory 모델과 그 identity digest, 배포 mode 검증,
pinset별 state 경로만 갖는다. 디스크에 쓰던 v6 manifest와 그 공개 사본은 ADR-51
D-2에서 지웠다 — 호스트에 남은 옛 v6 manifest·v8 journal 파일은 읽지도 고치지도 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
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

RUNTIME_SERVICES: tuple[RuntimeService, ...] = (
    "kor-travel-map-api",
    "kor-travel-map-ui",
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
    "pinvi-api",
    "pinvi-web",
    "pinvi-dagster",
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
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_HEAD = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_STATE_ROOT_ENV = "KTDM_PINNED_RUNTIME_STATE_ROOT"
_PROJECT_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")
_DEFAULT_STATE_ROOT = Path.home() / ".local" / "state" / "kor-travel-docker-manager"


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
    """pinset별 source·credential state의 owner-only 경로.

    ``state_root``는 ``deploy-status.json``과 pinset별 source bare·credential 파일이 함께
    사는 디렉터리다. ``pinset_sha256``은 그 pinset별 이름을 정한다.
    """

    state_root: Path
    pinset_sha256: str


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
    tombstone 경로나 현재 state를 읽기 전용으로 검사하는 호출자가 같은 정본을
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


def pinned_runtime_state_paths(
    values: Mapping[str, str],
    *,
    pinset_sha256: str,
) -> PinnedRuntimeStatePaths:
    """rehearsal project의 state root를 pinset과 함께 결정한다.

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


@dataclass(frozen=True)
class MapApplication300CandidateEvidence:
    """fresh application 300 candidate build/runtime contract evidence."""

    candidate_git_tree: str
    postgres_image_id: str
    dagster_config_sha256: str

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.dagster_config_sha256) is None:
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
            "candidate_git_tree": self.candidate_git_tree,
            "postgres_image_id": self.postgres_image_id,
            "dagster_config_sha256": self.dagster_config_sha256,
        }


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


def _validate_state_root(state_root: Path) -> None:
    try:
        state = state_root.lstat()
    except FileNotFoundError as exc:
        raise DeploymentContractError("pinned runtime state root is missing") from exc
    if (
        not stat.S_ISDIR(state.st_mode)
        or state.st_uid != os.geteuid()
        or stat.S_IMODE(state.st_mode) != 0o700
    ):
        raise DeploymentContractError("pinned runtime state root is unsafe")
