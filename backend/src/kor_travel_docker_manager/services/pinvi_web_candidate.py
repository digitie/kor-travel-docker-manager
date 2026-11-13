"""Pinvi web 단독 후보. Map current와 기존 pair 기준선은 검증 후 읽기만 한다."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_candidate as candidate
from kor_travel_docker_manager.services.service_release_component_candidate import (
    _environment_values,
    _preserve_environment,
)


def _public_inputs(client: Any, project: str, runtime: str, image_id: str) -> None:
    records = [r for r in candidate._inventory(client) if candidate._selected(r, project, runtime)]
    if len(records) != 1:
        raise release.ServiceReleaseError("현재 Pinvi web은 정확히 하나여야 합니다")
    before = records[0]
    old = _environment_values(client.images.get(before["Image"]).attrs["Config"].get("Env"))
    new = _environment_values(client.images.get(image_id).attrs["Config"].get("Env"))
    actual = _environment_values(before["Config"].get("Env"))
    baked = {key: value for key, value in old.items() if key.startswith("NEXT_PUBLIC_")}
    proposed = {key: value for key, value in new.items() if key.startswith("NEXT_PUBLIC_")}
    required = ("NEXT_PUBLIC_PINVI_API_URL", "NEXT_PUBLIC_VWORLD_API_KEY")
    if (any(not baked.get(key) for key in required) or baked != proposed
            or any(actual.get(key) != value for key, value in baked.items())):
        raise release.ServiceReleaseError("Pinvi 기존 baked 공개 입력·effective 환경과 후보 입력이 다릅니다")


def apply_current(plan: dict[str, Any], manifests: dict[str, Any], image_id: str, source_sha: str,
                  root: Path, wait: int, execute: bool, client: Any,
                  baseline: tuple[Any, ...]) -> dict[str, Any]:
    """기존 G/frozen/health/publication 본문에서 web 하나만 선택한다."""
    topology = candidate.runtime_topology()
    runtime = topology.service("pinvi_web")
    if plan["runtime_services"] != [runtime]:
        raise release.ServiceReleaseError("Pinvi web 하나만 적용할 수 있습니다")
    legacy_conditions = []
    if "map" not in manifests:
        def legacy_map():
            candidate._legacy_map_guard(plan, root, client, baseline)
        legacy_map()
        legacy_conditions.append(("legacy_map_unchanged", legacy_map))
    inventory, bind_pair = candidate._paired_current_hooks(plan, manifests, client, baseline)

    _public_inputs(client, plan["project"], runtime, image_id)
    bind_env, unchanged_env, secrets = _preserve_environment(
        client, plan["project"], runtime, image_id,
        release_env="PINVI_SOURCE_REVISION", source_sha=source_sha)
    def bind(materialized):
        bind_pair(materialized)
        bind_env(materialized)

    return candidate._apply_current(
        plan, manifests, image_id, source_sha, root, wait, execute, client,
        canonical_url=candidate.CANONICAL_RUNTIME_SOURCE_URLS["pinvi"],
        extra_conditions=[
            *legacy_conditions,
            ("baseline_unchanged", lambda: candidate._unchanged_baseline(
                baseline, manifests["pinvi"]["groups"][plan["group"]])),
            ("selected_environment_unchanged", unchanged_env),
        ],
        inventory_check=inventory,
        bind_images=bind, extra_secrets=secrets,
    )
