"""application-300 rebuild의 candidate build·static schema attestation contract.

Compose/Docker orchestration은 이 module이 만든 exact environment와 immutable image
ID만 소비한다. old compatible pair, backup, rollback slot은 이 경계에 없다.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.map_application_300 import (
    APPLICATION_HEAD,
    Application300Contract,
)
from kor_travel_docker_manager.services.map_application_300_candidate import (
    MapApplication300Candidate,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    RUNTIME_SERVICES,
    MapApplication300CandidateEvidence,
    PinnedRuntimeGeneration,
    PinnedRuntimeRebuildJournal,
    RuntimeService,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    PinnedRuntimeSourceMaterialization,
)

_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SCHEMA_HEAD = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_REPOSITORY_PREFIX = "kor-travel-docker-manager/pinned-runtime-candidate-v6/"

COMPOSE_BUILT_RUNTIME_SERVICES: tuple[RuntimeService, ...] = (
    "kor-travel-map-ui",
    "pinvi-api",
    "pinvi-web",
    "pinvi-dagster",
)

_IMAGE_ENVIRONMENT: Mapping[RuntimeService, str] = MappingProxyType(
    {
        "kor-travel-map-api": "KOR_TRAVEL_MAP_API_IMAGE",
        "kor-travel-map-ui": "KOR_TRAVEL_MAP_UI_IMAGE",
        "kor-travel-map-dagster": "KOR_TRAVEL_MAP_DAGSTER_IMAGE",
        "pinvi-api": "PINVI_API_IMAGE",
        "pinvi-web": "PINVI_WEB_IMAGE",
        "pinvi-dagster": "PINVI_DAGSTER_IMAGE",
    }
)


def _validate_map_application_300_candidate(
    *,
    sources: PinnedRuntimeSourceMaterialization,
    candidate: MapApplication300Candidate,
) -> None:
    if not isinstance(candidate, MapApplication300Candidate):
        raise DeploymentContractError("Map application 300 candidate is invalid")
    map_source = sources.source_for("map")
    if (
        candidate.candidate_commit != map_source.revision
        or candidate.candidate_commit != sources.release.source_for("map").revision
        or candidate.candidate_git_tree != map_source.tree
    ):
        raise DeploymentContractError(
            "Map application 300 candidate source differs from the release pin"
        )
    if any(
        not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
        for digest in (
            candidate.receipt_sha256,
            candidate.api_receipt_sha256,
            candidate.dagster_config_sha256,
            candidate.dagster_yaml_sha256,
            candidate.application_contract_sha256,
            candidate.launch_contract_sha256,
        )
    ):
        raise DeploymentContractError(
            "Map application 300 candidate evidence digest is invalid"
        )
    if any(
        not isinstance(image_id, str) or _IMAGE_ID.fullmatch(image_id) is None
        for image_id in (
            candidate.api_image_id,
            candidate.dagster_image_id,
            candidate.postgres_image_id,
        )
    ):
        raise DeploymentContractError("Map application 300 candidate image ID is invalid")
    if (
        not isinstance(candidate.application_contract, Application300Contract)
        or candidate.application_contract.postgres_image_id
        != candidate.postgres_image_id
    ):
        raise DeploymentContractError(
            "Map application 300 candidate PostgreSQL image differs from its contract"
        )


def _candidate_evidence(
    candidate: MapApplication300Candidate,
) -> MapApplication300CandidateEvidence:
    return MapApplication300CandidateEvidence(
        paired_receipt_sha256=candidate.receipt_sha256,
        api_receipt_sha256=candidate.api_receipt_sha256,
        candidate_git_tree=candidate.candidate_git_tree,
        postgres_image_id=candidate.postgres_image_id,
        dagster_config_sha256=candidate.dagster_config_sha256,
        dagster_yaml_sha256=candidate.dagster_yaml_sha256,
        application_contract_sha256=candidate.application_contract_sha256,
        launch_contract_sha256=candidate.launch_contract_sha256,
    )


def _runtime_image_environment(
    image_ids: Mapping[RuntimeService, str],
    *,
    require_immutable: bool,
) -> Mapping[str, str]:
    if set(image_ids) != set(RUNTIME_SERVICES):
        raise DeploymentContractError("pinned runtime candidate image IDs are invalid")
    if require_immutable and any(
        not isinstance(image_id, str) or _IMAGE_ID.fullmatch(image_id) is None
        for image_id in image_ids.values()
    ):
        raise DeploymentContractError("pinned runtime candidate image IDs are invalid")
    if (
        image_ids["kor-travel-map-dagster"]
        != image_ids["kor-travel-map-dagster-daemon"]
    ):
        raise DeploymentContractError(
            "Map Dagster web and daemon candidate image IDs differ"
        )
    return MappingProxyType(
        {
            environment_name: image_ids[service]
            for service, environment_name in _IMAGE_ENVIRONMENT.items()
        }
    )


def _candidate_image_name(
    sources: PinnedRuntimeSourceMaterialization,
    service: RuntimeService,
) -> str:
    return (
        f"{_CANDIDATE_REPOSITORY_PREFIX}{service}:"
        f"{sources.pinset_sha256}"
    )


def map_application_300_paired_build_image_names(
    sources: PinnedRuntimeSourceMaterialization,
) -> Mapping[RuntimeService, str]:
    """strict paired candidate가 생기기 전 Map builder에 줄 두 output tag."""

    if _SHA256.fullmatch(sources.pinset_sha256) is None:
        raise DeploymentContractError("pinned runtime candidate pinset is invalid")
    return MappingProxyType(
        {
            service: _candidate_image_name(sources, service)
            for service in ("kor-travel-map-api", "kor-travel-map-dagster")
        }
    )


@dataclass(frozen=True)
class CandidateRuntimeBuild:
    """하나의 release pinset에서 deterministic하게 계산한 Compose build input."""

    sources: PinnedRuntimeSourceMaterialization
    map_application_300_candidate: MapApplication300Candidate

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.sources.pinset_sha256) is None:
            raise DeploymentContractError("pinned runtime candidate pinset is invalid")
        _validate_map_application_300_candidate(
            sources=self.sources,
            candidate=self.map_application_300_candidate,
        )

    @property
    def image_names(self) -> Mapping[RuntimeService, str]:
        """Manager가 실제 build하는 네 service의 deterministic tag."""

        return MappingProxyType(
            {
                service: _candidate_image_name(self.sources, service)
                for service in COMPOSE_BUILT_RUNTIME_SERVICES
            }
        )

    @property
    def runtime_image_references(self) -> Mapping[RuntimeService, str]:
        """네 build tag와 paired Map exact image 세 개를 합친 runtime 입력."""

        candidate = self.map_application_300_candidate
        return MappingProxyType(
            {
                "kor-travel-map-api": candidate.api_image_id,
                "kor-travel-map-ui": self.image_names["kor-travel-map-ui"],
                "kor-travel-map-dagster": candidate.dagster_image_id,
                "kor-travel-map-dagster-daemon": candidate.dagster_image_id,
                "pinvi-api": self.image_names["pinvi-api"],
                "pinvi-web": self.image_names["pinvi-web"],
                "pinvi-dagster": self.image_names["pinvi-dagster"],
            }
        )

    def compose_environment(self) -> Mapping[str, str]:
        """candidate build와 후속 one-shot/runtime이 공유하는 frozen override."""

        release = self.sources.release
        values = {
            "KOR_TRAVEL_MAP_REPO_DIR": str(self.sources.source_for("map").root),
            "PINVI_REPO_DIR": str(self.sources.source_for("pinvi").root),
            "KOR_TRAVEL_MAP_GIT_COMMIT": release.source_for("map").revision,
            "PINVI_SOURCE_REVISION": release.source_for("pinvi").revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        }
        values.update(
            _runtime_image_environment(
                self.runtime_image_references,
                require_immutable=False,
            )
        )
        candidate = self.map_application_300_candidate
        values.update(
            {
                "KOR_TRAVEL_MAP_POSTGRES_IMAGE_ID": candidate.postgres_image_id,
                "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PAIRED_RECEIPT_SHA256": (
                    candidate.receipt_sha256
                ),
                "KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256": (
                    candidate.dagster_yaml_sha256
                ),
            }
        )
        return MappingProxyType(values)


@dataclass(frozen=True)
class MapApplication300ArtifactDirectories:
    """application 300 fence/permit의 canonical fixed-mount host 디렉터리."""

    fresh_migrate_fence: Path
    fresh_finalize_fence: Path
    application_final_permit: Path
    dagster_storage_permit: Path

    def __post_init__(self) -> None:
        paths = self.paths
        if any(not isinstance(path, Path) for path in paths):
            raise DeploymentContractError(
                "Map application 300 artifact directory is invalid"
            )
        if len(set(paths)) != len(paths):
            raise DeploymentContractError(
                "Map application 300 artifact directories must be distinct"
            )
        for path in paths:
            if (
                not path.is_absolute()
                or path != path.resolve(strict=False)
            ):
                raise DeploymentContractError(
                    "Map application 300 artifact directory is invalid"
                )

    @property
    def paths(self) -> tuple[Path, Path, Path, Path]:
        return (
            self.fresh_migrate_fence,
            self.fresh_finalize_fence,
            self.application_final_permit,
            self.dagster_storage_permit,
        )

    def compose_environment(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                "KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR": str(
                    self.fresh_migrate_fence
                ),
                "KOR_TRAVEL_MAP_APPLICATION_FRESH_FINALIZE_FENCE_DIR": str(
                    self.fresh_finalize_fence
                ),
                "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DIR": str(
                    self.application_final_permit
                ),
                "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR": str(
                    self.dagster_storage_permit
                ),
            }
        )


def generation_compose_environment(
    generation: PinnedRuntimeGeneration,
    *,
    artifact_directories: MapApplication300ArtifactDirectories,
) -> Mapping[str, str]:
    """attested image·paired receipt·fixed artifact만 주는 runtime override."""

    values = dict(
        _runtime_image_environment(
            generation.image_ids,
            require_immutable=True,
        )
    )
    evidence = generation.map_application_300_candidate_evidence
    values.update(
        {
            "KOR_TRAVEL_MAP_POSTGRES_IMAGE_ID": evidence.postgres_image_id,
            "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PAIRED_RECEIPT_SHA256": (
                evidence.paired_receipt_sha256
            ),
            # Dagster launch receipt의 config digest와 실제 dagster.yaml digest는
            # 서로 다른 증거다. runtime storage contract에는 후자만 전달한다.
            "KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256": (
                evidence.dagster_yaml_sha256
            ),
            **artifact_directories.compose_environment(),
        }
    )
    return MappingProxyType(values)


def parse_candidate_static_head(
    output: str,
    *,
    schema: str,
    field: str,
) -> str:
    """network-less candidate head command의 한 줄 JSON만 수용한다."""

    if not isinstance(output, str):
        raise DeploymentContractError("candidate schema head output is invalid")
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0] or len(lines[0]) > 1024:
        raise DeploymentContractError("candidate schema head output is invalid")
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise DeploymentContractError("candidate schema head output is invalid") from exc
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"schema", field}
        or payload.get("schema") != schema
        or not isinstance(payload.get(field), str)
    ):
        raise DeploymentContractError("candidate schema head output is invalid")
    head = str(payload[field])
    if _SCHEMA_HEAD.fullmatch(head) is None:
        raise DeploymentContractError("candidate schema head output is invalid")
    return head


def build_candidate_generation(
    *,
    sources: PinnedRuntimeSourceMaterialization,
    map_application_300_candidate: MapApplication300Candidate,
    image_ids: Mapping[RuntimeService, str],
    map_dagster_head: str,
    pinvi_head: str,
    recorded_at: str | None = None,
) -> PinnedRuntimeGeneration:
    """검증 완료된 seven-image candidate를 typed durable generation으로 만든다."""

    _validate_map_application_300_candidate(
        sources=sources,
        candidate=map_application_300_candidate,
    )
    _runtime_image_environment(image_ids, require_immutable=True)
    if image_ids["kor-travel-map-api"] != map_application_300_candidate.api_image_id:
        raise DeploymentContractError(
            "Map API candidate image differs from the paired candidate"
        )
    if (
        image_ids["kor-travel-map-dagster"]
        != map_application_300_candidate.dagster_image_id
    ):
        raise DeploymentContractError(
            "Map Dagster candidate image differs from the paired candidate"
        )
    for head in (map_dagster_head, pinvi_head):
        if _SCHEMA_HEAD.fullmatch(head) is None:
            raise DeploymentContractError("pinned runtime candidate schema head is invalid")
    timestamp = recorded_at or datetime.now(UTC).isoformat()
    return PinnedRuntimeGeneration(
        map_api_image_id=image_ids["kor-travel-map-api"],
        map_ui_image_id=image_ids["kor-travel-map-ui"],
        map_dagster_image_id=image_ids["kor-travel-map-dagster"],
        map_dagster_daemon_image_id=image_ids["kor-travel-map-dagster-daemon"],
        pinvi_api_image_id=image_ids["pinvi-api"],
        pinvi_web_image_id=image_ids["pinvi-web"],
        pinvi_dagster_image_id=image_ids["pinvi-dagster"],
        map_source_revision=sources.release.source_for("map").revision,
        pinvi_source_revision=sources.release.source_for("pinvi").revision,
        map_application_head=APPLICATION_HEAD,
        map_dagster_head=map_dagster_head,
        pinvi_head=pinvi_head,
        pinset_sha256=sources.pinset_sha256,
        map_application_300_candidate_evidence=_candidate_evidence(
            map_application_300_candidate
        ),
        recorded_at=timestamp,
    )


def new_candidate_journal(
    *,
    candidate: PinnedRuntimeGeneration,
    environment_bytes: bytes,
    compose_source_bytes: bytes,
    resolved_compose_sha256: str,
    created_at: str | None = None,
) -> PinnedRuntimeRebuildJournal:
    """DB mutation 전에 fsync할 candidate_attested receipt를 생성한다."""

    if _SHA256.fullmatch(resolved_compose_sha256) is None:
        raise DeploymentContractError("pinned runtime resolved Compose digest is invalid")
    if not environment_bytes or not compose_source_bytes:
        raise DeploymentContractError("pinned runtime frozen input bytes are invalid")
    timestamp = created_at or datetime.now(UTC).isoformat()
    return PinnedRuntimeRebuildJournal(
        version=8,
        transaction_id=str(uuid.uuid4()),
        phase="candidate_attested",
        candidate=candidate,
        map_application_300_candidate_evidence=(
            candidate.map_application_300_candidate_evidence
        ),
        environment_sha256=hashlib.sha256(environment_bytes).hexdigest(),
        compose_sha256=hashlib.sha256(compose_source_bytes).hexdigest(),
        resolved_compose_sha256=resolved_compose_sha256,
        created_at=timestamp,
        journal_generation=0,
    )
