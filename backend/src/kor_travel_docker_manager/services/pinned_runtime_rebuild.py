"""F1D v5 rebuild의 candidate build·static schema attestation contract.

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
from types import MappingProxyType

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    RUNTIME_SERVICES,
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
_CANDIDATE_REPOSITORY_PREFIX = "kor-travel-docker-manager/pinned-runtime-candidate-v5/"

_IMAGE_ENVIRONMENT: Mapping[RuntimeService, str] = MappingProxyType(
    {
        "kor-travel-map-api": "KOR_TRAVEL_MAP_API_IMAGE",
        "kor-travel-map-ui": "KOR_TRAVEL_MAP_UI_IMAGE",
        "kor-travel-map-dagster": "KOR_TRAVEL_MAP_DAGSTER_IMAGE",
        "kor-travel-map-dagster-daemon": "KOR_TRAVEL_MAP_DAGSTER_DAEMON_IMAGE",
        "pinvi-api": "PINVI_API_IMAGE",
        "pinvi-web": "PINVI_WEB_IMAGE",
        "pinvi-dagster": "PINVI_DAGSTER_IMAGE",
    }
)


@dataclass(frozen=True)
class CandidateRuntimeBuild:
    """하나의 release pinset에서 deterministic하게 계산한 Compose build input."""

    sources: PinnedRuntimeSourceMaterialization

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.sources.pinset_sha256) is None:
            raise DeploymentContractError("pinned runtime candidate pinset is invalid")

    @property
    def image_names(self) -> Mapping[RuntimeService, str]:
        return MappingProxyType(
            {
                service: (
                    f"{_CANDIDATE_REPOSITORY_PREFIX}{service}:"
                    f"{self.sources.pinset_sha256}"
                )
                for service in RUNTIME_SERVICES
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
            {
                _IMAGE_ENVIRONMENT[service]: image
                for service, image in self.image_names.items()
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
    image_ids: Mapping[RuntimeService, str],
    map_application_head: str,
    map_dagster_head: str,
    pinvi_head: str,
    recorded_at: str | None = None,
) -> PinnedRuntimeGeneration:
    """검증 완료된 seven-image candidate를 typed durable generation으로 만든다."""

    if set(image_ids) != set(RUNTIME_SERVICES) or any(
        _IMAGE_ID.fullmatch(image_id) is None for image_id in image_ids.values()
    ):
        raise DeploymentContractError("pinned runtime candidate image IDs are invalid")
    for head in (map_application_head, map_dagster_head, pinvi_head):
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
        map_application_head=map_application_head,
        map_dagster_head=map_dagster_head,
        pinvi_head=pinvi_head,
        pinset_sha256=sources.pinset_sha256,
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
        version=5,
        transaction_id=str(uuid.uuid4()),
        phase="candidate_attested",
        candidate=candidate,
        environment_sha256=hashlib.sha256(environment_bytes).hexdigest(),
        compose_sha256=hashlib.sha256(compose_source_bytes).hexdigest(),
        resolved_compose_sha256=resolved_compose_sha256,
        created_at=timestamp,
    )
