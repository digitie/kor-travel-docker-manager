"""pinned runtime generation의 typed v6 manifest와 그 공개 사본.

ADR-51 뒤 배포 진행의 정본은 ``deploy_status.py``의 ``deploy-status.json``이다. 이
모듈은 커밋된 active generation 하나를 v6 manifest로 남기고, 비-root 관측자가 읽을
공개 사본을 발행한다. M05 하네스는 private manifest를, ``pin verify``와
``GET /pinned-runtime/generation``은 공개 사본을 읽는다. v8 rebuild journal·tombstone
모델은 ADR-51 B3에서 지웠다 — 호스트에 남은 옛 v8 파일은 읽지도 고치지도 않는다.
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
_MAX_STATE_BYTES = 64 * 1024
_MANIFEST_VERSION = 6
_STATE_ROOT_ENV = "KTDM_PINNED_RUNTIME_STATE_ROOT"
_PUBLIC_ROOT_ENV = "KTDM_PINNED_RUNTIME_PUBLIC_ROOT"
_PROJECT_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")
_DEFAULT_STATE_ROOT = Path.home() / ".local" / "state" / "kor-travel-docker-manager"
# GM-09: 경로 상수의 정본은 services/trusted_install.py다.
_DEFAULT_PUBLIC_ROOT = TRUSTED_PUBLIC_ROOT
_MANIFEST_FILENAME = "pinned-runtime-generation-v6.json"


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
    """v6 generation manifest와 pinset별 source·credential state의 owner-only 경로.

    ``state_root``는 ``deploy-status.json``과 pinset별 source bare·credential 파일이 함께
    사는 디렉터리다. ``pinset_sha256``은 그 pinset별 이름을 정한다.
    """

    state_root: Path
    pinset_sha256: str
    manifest: Path


@dataclass(frozen=True)
class PinnedRuntimePublicPaths:
    """비-root 관측자가 읽는 v6 manifest 공개 사본 경로.

    private manifest의 정확한 JSON을 복제할 뿐 envelope나 진단 원문을 파일에 섞지 않는다.
    """

    manifest: Path


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
    """rehearsal project의 state root와 v6 manifest 경로를 pinset과 함께 결정한다.

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


def map_application_300_candidate_evidence_from_payload(
    payload: object,
) -> MapApplication300CandidateEvidence:
    expected = {
        "candidate_git_tree",
        "postgres_image_id",
        "dagster_config_sha256",
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
    return PinnedRuntimePublicPaths(manifest=root / _MANIFEST_FILENAME)


def publish_pinned_runtime_generation(
    *,
    manifest: PinnedRuntimeManifest,
    private_path: Path | None = None,
) -> PinnedRuntimePublicPaths:
    """검증된 private v6 manifest를 backend 가독 사본으로 원자 복제한다.

    이 함수는 private 파일을 다시 읽지 않는다. caller가 typed model로 이미 검증한
    payload만 받아서 쓰므로 symlink·mode가 다른 private artifact를 API에 중계할 여지가
    없고, raw JSON schema 자체는 절대 바꾸지 않는다.
    """

    paths = pinned_runtime_public_paths(private_path=private_path)
    _write_public_json(paths.manifest, manifest.to_payload())
    return paths


def read_published_pinned_runtime_generation() -> dict[str, object]:
    """backend 전용 public-copy reader.

    root-owned private state의 경로·권한을 우회하지 않는다. 사본이 없거나 schema가 틀리면
    원문/경로를 노출하지 않고 `unknown`으로 끝낸다.

    v6 manifest 공개 사본 하나만 읽는다. 배포는 manifest를 **커밋 때만** 쓰므로 그것이
    곧 마지막으로 커밋된 세대다(ADR-51). 공개 root에 남은 옛
    `pinned-runtime-rebuild-v8.json`(ADR-51 이전 journal 사본)은 열지 않는다 — 있어도
    결과가 달라지지 않는다(ADR-51 B3).
    """

    paths = pinned_runtime_public_paths()
    try:
        manifest = manifest_from_payload(
            _read_public_json(paths.manifest, "pinned runtime manifest public copy")
        )
    except DeploymentContractError:
        return {
            "status": "unknown",
            "source": "published_copy",
            "detail": "pinned runtime generation public copy is incomplete or invalid",
            "manifest": None,
            "pinset_binding": _published_generation_pinset_binding(None),
            "summary": _published_generation_summary(
                manifest=None,
                pinset_binding="unknown",
            ),
        }
    pinset_binding = _published_generation_pinset_binding(manifest)
    return {
        "status": "ok",
        "source": "published_copy",
        "manifest": manifest.to_payload(),
        "pinset_binding": pinset_binding,
        "summary": _published_generation_summary(
            manifest=manifest,
            pinset_binding=cast(str, pinset_binding["status"]),
        ),
    }


def _published_generation_pinset_binding(
    manifest: PinnedRuntimeManifest | None,
) -> dict[str, str | None]:
    """public registry와 커밋된 generation의 Map·PinVi pair 결박을 비교한다.

    `runtime-pins`가 unknown/stale/degraded이거나 모양이 틀리면 generation API가 값을
    추측해 match라고 말하지 않는다(`unknown`). manifest는 커밋 때만 쓰이므로 registry
    pair와 다르면 회전한 새 pair가 아직 배포되지 않은 것이다 — `pending_rebuild`. 진행
    중인 journal이 없어졌으므로 옛 `drift` 상태도 없다(ADR-51 B3). 그래서 pair를 회전한
    직후에도 `pin verify`가 이 결박 때문에 1로 끝나지 않는다.
    """

    if manifest is None:
        return {
            "status": "unknown",
            "registry_pinset_sha256": None,
            "generation_pinset_sha256": None,
        }
    from kor_travel_docker_manager.services.runtime_pin_registry import (
        read_published_runtime_pins,
    )

    generation = manifest.active_generation
    payload = read_published_runtime_pins()
    sources = payload.get("sources")
    registry_pinset = payload.get("pinset_sha256")
    if (
        payload.get("status") != "ok"
        or not isinstance(sources, list)
        or not isinstance(registry_pinset, str)
    ):
        return {
            "status": "unknown",
            "registry_pinset_sha256": None,
            "generation_pinset_sha256": generation.pinset_sha256,
        }
    revisions = {
        entry.get("role"): entry.get("revision")
        for entry in sources
        if isinstance(entry, Mapping)
    }
    if (
        revisions.get("map") != generation.map_source_revision
        or revisions.get("pinvi") != generation.pinvi_source_revision
        or registry_pinset != generation.pinset_sha256
    ):
        return {
            "status": "pending_rebuild",
            "registry_pinset_sha256": registry_pinset,
            "generation_pinset_sha256": generation.pinset_sha256,
        }
    return {
        "status": "match",
        "registry_pinset_sha256": registry_pinset,
        "generation_pinset_sha256": generation.pinset_sha256,
    }


def _published_generation_summary(
    *,
    manifest: PinnedRuntimeManifest | None,
    pinset_binding: str,
) -> dict[str, object]:
    """raw v6 원본을 바꾸지 않는 API envelope의 인간용 요약."""

    if manifest is None:
        return {
            "state": "unknown",
            "text": "공개된 pinned runtime 세대 기록이 없습니다.",
            "next_action": (
                "sudo -n backend/.venv/bin/ktdctl pin publish-generation "
                "--manifest <absolute-v6-path> --confirm"
            ),
            "manifest_version": None,
        }
    if pinset_binding == "pending_rebuild":
        return {
            "state": "pending_rebuild",
            "text": "현재 pinset은 새 재구축을 기다리고 있습니다.",
            "next_action": "",
            "manifest_version": manifest.version,
        }
    if pinset_binding != "match":
        return {
            "state": "unverified",
            "text": "현재 registry와 공개 generation의 Map·PinVi pair 결박을 확인할 수 없습니다.",
            "next_action": "sudo -n backend/.venv/bin/ktdctl pin verify",
            "manifest_version": manifest.version,
        }
    return {
        "state": "committed",
        "text": "고정된 runtime 세대가 커밋되어 있습니다.",
        "next_action": "",
        "manifest_version": manifest.version,
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
    if path.name != _MANIFEST_FILENAME:
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
