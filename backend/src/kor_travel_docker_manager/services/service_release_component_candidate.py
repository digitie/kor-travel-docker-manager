"""기존 component current의 등록된 UI 하나를 교체한다. paired pin·그룹 layout은 바꾸지 않는다."""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import docker

from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_candidate as candidate
from kor_travel_docker_manager.services.c6c_deployment import manager_mutation_lock
from kor_travel_docker_manager.services.registry import (
    TargetsConfigError,
    component_ui_candidate_for_target,
    load_targets_config,
)
from kor_travel_docker_manager.services.service_release import ServiceReleaseError


def _selection(service: str, config: dict[str, Any]) -> tuple[str, str, str]:
    if service in candidate.CANONICAL_RUNTIME_SOURCE_URLS:
        raise ServiceReleaseError("paired 서비스는 기존 전용 후보 경로를 사용해야 합니다")
    declaration = entry._declaration(service, config)
    try:
        runtime, source_url = component_ui_candidate_for_target(service, config)
    except TargetsConfigError as exc:
        raise ServiceReleaseError(str(exc)) from exc
    groups = [name for name, group in declaration["groups"].items()
              if runtime in group["runtime_services"] and runtime not in group["oneshot_services"]]
    if len(groups) != 1:
        raise ServiceReleaseError("UI 역할은 기존 current의 단일 runtime 그룹에 속해야 합니다")
    return groups[0], runtime, source_url



def _environment_values(entries: Any) -> dict[str, str]:
    if not isinstance(entries, list):
        raise ServiceReleaseError("선택 UI 환경을 완전하게 읽을 수 없습니다")
    values: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, str) or "\x00" in item:
            raise ServiceReleaseError("선택 UI 환경 항목이 올바르지 않습니다")
        key, separator, value = item.partition("=")
        if not separator or not key or key in values:
            raise ServiceReleaseError("선택 UI 환경 항목이 누락되거나 중복되었습니다")
        values[key] = value
    return values


def _preserve_environment(client: Any, project: str, runtime: str, image_id: str):
    """G 안에서 현재 UI 환경을 승인하고 같은 값과 후보 이미지의 새 기본값만 사용한다."""
    def selected():
        matches = [item for item in candidate._inventory(client)
                   if candidate._selected(item, project, runtime)]
        if len(matches) != 1:
            raise ServiceReleaseError("선택 UI 환경의 컨테이너는 정확히 하나여야 합니다")
        return matches[0]
    initial = selected()
    before = _environment_values((initial.get("Config") or {}).get("Env"))
    image_config = client.images.get(image_id).attrs.get("Config") or {}
    image_environment = image_config.get("Env")
    defaults = _environment_values([] if image_environment is None else image_environment)
    expected = {**defaults, **before}
    def bind(materialized):
        definition = materialized["services"][runtime]
        definition["environment"] = dict(expected)
        # config로 해석한 effective 값을 고정했으므로 env_file을 다시 읽지 않는다.
        definition.pop("env_file", None)
    def unchanged():
        current = selected()
        actual = _environment_values((current.get("Config") or {}).get("Env"))
        if current.get("Image") == image_id:
            approved = expected
        elif current.get("Image") == initial.get("Image"):
            approved = before
        else:
            raise ServiceReleaseError("선택 UI 환경의 이미지가 승인 범위와 다릅니다")
        if actual != approved:
            raise ServiceReleaseError("선택 UI 환경이 승인한 effective 값과 다릅니다")
    return bind, unchanged, list(expected.items())


def apply_component_ui_candidate(service: str, *, image_id: str, source_sha: str,
                                 source_clone: Path, root: Path,
                                 config: dict[str, Any] | None = None,
                                 wait_timeout: int = 120, execute: bool = False,
                                 client_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    """등록 UI만 교체한다. UI 역할/source는 target 정본, group은 기존 current에서 유도한다."""
    release._image_id(image_id)
    if (not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha)
            or type(wait_timeout) is not int or not 1 <= wait_timeout <= 600
            or type(execute) is not bool):
        raise ServiceReleaseError("승인 source SHA 또는 대기 시간이 올바르지 않습니다")
    if os.geteuid() != 0:
        raise ServiceReleaseError("UI 후보 적용은 root로 실행해야 합니다")
    config = load_targets_config() if config is None else config
    group, runtime, source_url = _selection(service, config)
    candidate._merged_source(source_clone, source_sha, service, canonical_url=source_url)
    with manager_mutation_lock():
        # G를 기다리는 동안 바뀔 수 있는 승인 source/current를 잠금 안에서 다시 읽는다.
        candidate._merged_source(source_clone, source_sha, service, canonical_url=source_url)
        plan, manifests = entry._compose_plan(service, group, root=root, config=config)
        if runtime not in plan["runtime_services"]:
            raise ServiceReleaseError("등록 UI와 current Compose runtime이 다릅니다")
        # 전체 group의 overlay를 유지하고 mutation 선택만 UI 하나로 좁힌다.
        plan = {**plan, "runtime_services": [runtime]}
        factory = client_factory or (lambda: docker.from_env(timeout=10))
        client = factory()
        try:
            bind, unchanged, secrets = _preserve_environment(client, plan["project"], runtime, image_id)
            return candidate._apply_current(
                plan, manifests, image_id, source_sha, root, wait_timeout, execute, client,
                canonical_url=source_url,
                extra_conditions=[("selected_environment_unchanged", unchanged)],
                bind_images=bind, extra_secrets=secrets,
            )
        finally:
            client.close()
