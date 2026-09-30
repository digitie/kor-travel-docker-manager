"""application-300 rebuild의 candidate build·static schema attestation contract.

Compose/Docker orchestration은 이 module이 만든 exact environment와 immutable image
ID만 소비한다. old compatible pair, backup, rollback slot은 이 경계에 없다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.map_application_candidate import (
    MapApplicationCandidate,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    MapApplication300CandidateEvidence,
    PinnedRuntimeGeneration,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    PinnedRuntimeSourceMaterialization,
)
from kor_travel_docker_manager.services.runtime_topology import (
    COMPOSE_BUILT_RUNTIME_SLOTS,
    MAP_PAIRED_BUILD_SLOTS,
    RUNTIME_SLOTS,
    RuntimeSlot,
    RuntimeTopology,
    runtime_topology,
)

_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SCHEMA_HEAD = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_REPOSITORY_PREFIX = "kor-travel-docker-manager/pinned-runtime-candidate-v6/"

#: slot 이미지를 compose에 넘기는 env. Map daemon slot은 Map Dagster 이미지와 같아야 하므로 자기 env가 없다.
_IMAGE_ENVIRONMENT: Mapping[RuntimeSlot, str] = MappingProxyType(
    {
        "map_api": "KOR_TRAVEL_MAP_API_IMAGE",
        "map_ui": "KOR_TRAVEL_MAP_UI_IMAGE",
        "map_dagster": "KOR_TRAVEL_MAP_DAGSTER_IMAGE",
        "pinvi_api": "PINVI_API_IMAGE",
        "pinvi_web": "PINVI_WEB_IMAGE",
        "pinvi_dagster": "PINVI_DAGSTER_IMAGE",
    }
)


def _validate_map_application_candidate(
    *,
    sources: PinnedRuntimeSourceMaterialization,
    candidate: MapApplicationCandidate,
) -> None:
    if not isinstance(candidate, MapApplicationCandidate):
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
    if _SHA256.fullmatch(candidate.dagster_config_sha256) is None:
        raise DeploymentContractError(
            "Map application 300 candidate evidence digest is invalid"
        )
    if any(
        not isinstance(image_id, str) or _IMAGE_ID.fullmatch(image_id) is None
        for image_id in (
            candidate.api_image_id,
            candidate.dagster_image_id,
        )
    ):
        raise DeploymentContractError("Map application 300 candidate image ID is invalid")


def _candidate_evidence(
    candidate: MapApplicationCandidate,
) -> MapApplication300CandidateEvidence:
    return MapApplication300CandidateEvidence(
        candidate_git_tree=candidate.candidate_git_tree,
        dagster_config_sha256=candidate.dagster_config_sha256,
    )


def _runtime_image_environment(
    image_ids: Mapping[RuntimeSlot, str],
    *,
    require_immutable: bool,
) -> Mapping[str, str]:
    if set(image_ids) != set(RUNTIME_SLOTS):
        raise DeploymentContractError("pinned runtime candidate image IDs are invalid")
    if require_immutable and any(
        not isinstance(image_id, str) or _IMAGE_ID.fullmatch(image_id) is None
        for image_id in image_ids.values()
    ):
        raise DeploymentContractError("pinned runtime candidate image IDs are invalid")
    if image_ids["map_dagster"] != image_ids["map_dagster_daemon"]:
        raise DeploymentContractError(
            "Map Dagster web and daemon candidate image IDs differ"
        )
    return MappingProxyType(
        {
            environment_name: image_ids[slot]
            for slot, environment_name in _IMAGE_ENVIRONMENT.items()
        }
    )


def generation_companion_services(
    resolved: Mapping[str, Any],
    image_ids: Mapping[RuntimeSlot, str],
    *,
    excluded_services: Collection[str],
    topology: RuntimeTopology,
) -> Mapping[str, RuntimeSlot]:
    """generation slot의 이미지를 그대로 쓰는 비-slot 장기 실행 서비스와 그 owner slot.

    ADR-069 code-server처럼 slot 이미지를 공유하는 서비스는 durable slot 없이 owner의
    이미지에 결박된다. `up --no-deps`는 이름이 없는 서비스로의 depends_on 간선을 지우므로
    이 목록이 없으면 그 서비스는 기동도 정지도 되지 않는다. 손으로 두지 않고 frozen
    resolved Compose에서 파생해, compose에 추가하는 것만으로 관리 대상이 되게 한다.

    공용 plane에 합류한 target의 옛 webserver·daemon(ADR-54 `legacy-dagster`)은 frozen
    render(`--profile bootstrap`만)에 없으므로 여기 오지 않는다. 그래도 문서에 있으면(그
    profile을 켠 render) 거부한다 — companion이 되면 `up`이 옛 daemon을 되살린다.
    """

    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("pinned runtime resolved Compose services are invalid")
    retired = sorted(set(topology.retired_services).intersection(map(str, services)))
    if retired:
        raise DeploymentContractError(
            "pinned runtime resolved Compose renders retired Dagster services: "
            + ", ".join(retired)
        )
    slot_services = set(topology.runtime_services)
    owners: dict[str, RuntimeSlot] = {}
    for slot in RUNTIME_SLOTS:
        owners.setdefault(image_ids[slot], slot)
    companions: dict[str, RuntimeSlot] = {}
    for name, service in services.items():
        if name in slot_services or name in excluded_services:
            continue
        if not isinstance(name, str) or not isinstance(service, Mapping):
            raise DeploymentContractError(
                "pinned runtime resolved Compose services are invalid"
            )
        image = service.get("image")
        if isinstance(image, str) and image in owners:
            companions[name] = owners[image]
    return MappingProxyType(dict(sorted(companions.items())))


def _candidate_image_name(
    sources: PinnedRuntimeSourceMaterialization,
    slot: RuntimeSlot,
    topology: RuntimeTopology,
) -> str:
    """candidate tag 이름은 그 slot을 운반하는 서비스다 — `own`이면 종전 이름 그대로."""

    return (
        f"{_CANDIDATE_REPOSITORY_PREFIX}{topology.require_service(slot)}:"
        f"{sources.pinset_sha256}"
    )


def map_application_300_paired_build_image_names(
    sources: PinnedRuntimeSourceMaterialization,
    topology: RuntimeTopology | None = None,
) -> Mapping[RuntimeSlot, str]:
    """strict paired candidate가 생기기 전 Map builder에 줄 두 output tag."""

    if _SHA256.fullmatch(sources.pinset_sha256) is None:
        raise DeploymentContractError("pinned runtime candidate pinset is invalid")
    topology = runtime_topology() if topology is None else topology
    return MappingProxyType(
        {slot: _candidate_image_name(sources, slot, topology) for slot in MAP_PAIRED_BUILD_SLOTS}
    )


@dataclass(frozen=True)
class CandidateRuntimeBuild:
    """하나의 release pinset에서 deterministic하게 계산한 Compose build input."""

    sources: PinnedRuntimeSourceMaterialization
    map_application_candidate: MapApplicationCandidate
    topology: RuntimeTopology = field(default_factory=runtime_topology)

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.sources.pinset_sha256) is None:
            raise DeploymentContractError("pinned runtime candidate pinset is invalid")
        _validate_map_application_candidate(
            sources=self.sources,
            candidate=self.map_application_candidate,
        )

    @property
    def image_names(self) -> Mapping[RuntimeSlot, str]:
        """Manager가 실제 build하는 네 slot의 deterministic tag."""

        return MappingProxyType(
            {
                slot: _candidate_image_name(self.sources, slot, self.topology)
                for slot in COMPOSE_BUILT_RUNTIME_SLOTS
            }
        )

    @property
    def build_services(self) -> tuple[str, ...]:
        """compose `build`에 넘길 서비스 — 네 slot을 지금 운반하는 서비스(slot 순서)."""

        return tuple(
            self.topology.require_service(slot) for slot in COMPOSE_BUILT_RUNTIME_SLOTS
        )

    @property
    def runtime_image_references(self) -> Mapping[RuntimeSlot, str]:
        """네 build tag와 paired Map exact image 세 개를 합친 runtime 입력."""

        candidate = self.map_application_candidate
        return MappingProxyType(
            {
                "map_api": candidate.api_image_id,
                "map_ui": self.image_names["map_ui"],
                "map_dagster": candidate.dagster_image_id,
                "map_dagster_daemon": candidate.dagster_image_id,
                "pinvi_api": self.image_names["pinvi_api"],
                "pinvi_web": self.image_names["pinvi_web"],
                "pinvi_dagster": self.image_names["pinvi_dagster"],
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
        return MappingProxyType(values)


def generation_compose_environment(
    generation: PinnedRuntimeGeneration,
) -> Mapping[str, str]:
    """attested image만 주는 runtime override.

    ADR-51 D-3에서 Dagster storage permit 디렉터리와 `..._STORAGE_CONFIG_SHA256`을
    뺐다 — M1 이후 Map storage one-shot은 둘 다 읽지 않는다.
    """

    return _runtime_image_environment(
        generation.image_ids,
        require_immutable=True,
    )


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
    map_application_candidate: MapApplicationCandidate,
    image_ids: Mapping[RuntimeSlot, str],
    map_dagster_head: str,
    pinvi_head: str,
    recorded_at: str | None = None,
) -> PinnedRuntimeGeneration:
    """검증 완료된 seven-image candidate를 typed durable generation으로 만든다."""

    _validate_map_application_candidate(
        sources=sources,
        candidate=map_application_candidate,
    )
    _runtime_image_environment(image_ids, require_immutable=True)
    if image_ids["map_api"] != map_application_candidate.api_image_id:
        raise DeploymentContractError(
            "Map API candidate image differs from the paired candidate"
        )
    if image_ids["map_dagster"] != map_application_candidate.dagster_image_id:
        raise DeploymentContractError(
            "Map Dagster candidate image differs from the paired candidate"
        )
    declared_head = map_application_candidate.application_head
    for head in (declared_head, map_dagster_head, pinvi_head):
        if _SCHEMA_HEAD.fullmatch(head) is None:
            raise DeploymentContractError("pinned runtime candidate schema head is invalid")
    timestamp = recorded_at or datetime.now(UTC).isoformat()
    return PinnedRuntimeGeneration(
        map_api_image_id=image_ids["map_api"],
        map_ui_image_id=image_ids["map_ui"],
        map_dagster_image_id=image_ids["map_dagster"],
        map_dagster_daemon_image_id=image_ids["map_dagster_daemon"],
        pinvi_api_image_id=image_ids["pinvi_api"],
        pinvi_web_image_id=image_ids["pinvi_web"],
        pinvi_dagster_image_id=image_ids["pinvi_dagster"],
        map_source_revision=sources.release.source_for("map").revision,
        pinvi_source_revision=sources.release.source_for("pinvi").revision,
        map_application_head=declared_head,
        map_dagster_head=map_dagster_head,
        pinvi_head=pinvi_head,
        pinset_sha256=sources.pinset_sha256,
        map_application_300_candidate_evidence=_candidate_evidence(
            map_application_candidate
        ),
        recorded_at=timestamp,
    )
