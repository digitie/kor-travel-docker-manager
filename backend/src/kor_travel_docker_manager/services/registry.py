import os
from functools import lru_cache
from typing import Any

import yaml


def get_project_root() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(current_dir, "../../../../"))


def get_targets_config_path() -> str:
    return os.environ.get(
        "KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE",
        os.path.join(get_project_root(), "config", "docker-targets.yml"),
    )


@lru_cache(maxsize=1)
def load_targets_config() -> dict[str, Any]:
    path = get_targets_config_path()
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    if not isinstance(config.get("containers"), dict):
        raise ValueError("docker target config must define containers")
    if not isinstance(config.get("targets"), dict):
        raise ValueError("docker target config must define targets")
    if not isinstance(config.get("dependency_order"), list):
        raise ValueError("docker target config must define dependency_order")
    return config


def _targets() -> dict[str, dict[str, Any]]:
    return load_targets_config()["targets"]


def _dependency_order() -> list[str]:
    return list(load_targets_config()["dependency_order"])


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _build_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for target, spec in _targets().items():
        for alias in [target, *spec.get("aliases", [])]:
            aliases[str(alias).strip().lower()] = target
    return aliases


MANAGED_CONTAINERS: dict[str, dict[str, Any]] = load_targets_config()["containers"]
MANAGED_TARGETS: dict[str, dict[str, Any]] = _targets()
TARGET_ALIASES: dict[str, str] = _build_aliases()


def resolve_target_name(target: str | None) -> str:
    normalized = (target or "all").strip().lower()
    if normalized in TARGET_ALIASES:
        return TARGET_ALIASES[normalized]
    raise ValueError(f"unknown target: {target}")


def _dependency_closure(target_name: str) -> set[str]:
    """target의 transitive `depends_on` 폐포(자기 자신 포함)를 반환한다."""
    closure: set[str] = set()
    stack = [target_name]
    while stack:
        name = stack.pop()
        if name in closure:
            continue
        closure.add(name)
        for dep in _targets().get(name, {}).get("depends_on", []):
            stack.append(str(dep))
    return closure


def target_sequence_for_target(target: str | None) -> list[str]:
    target_name = resolve_target_name(target)
    target_spec = _targets()[target_name]
    if target_spec.get("include"):
        included: list[str] = []
        for included_target in target_spec["include"]:
            included.extend(target_sequence_for_target(included_target))
        return _dedupe(included)

    # 선형 슬라이스 대신 `depends_on` DAG의 위상정렬을 사용한다.
    # dependency_order는 DAG의 유효한 linearization이므로, 폐포를 그 순서로 정렬하면
    # 의존성(부모)이 항상 먼저 오는 결정적 순서가 된다. depends_on이 없으면 단일 target.
    order = _dependency_order()
    closure = _dependency_closure(target_name)
    if not _targets()[target_name].get("depends_on") and target_name not in order:
        return [target_name]
    return sorted(closure, key=lambda t: order.index(t) if t in order else len(order))


def get_target(target: str | None) -> dict[str, Any]:
    target_name = resolve_target_name(target)
    spec = _targets()[target_name]
    return {
        "id": target_name,
        **spec,
        "resolved_sequence": target_sequence_for_target(target_name),
        "resolved_services": services_for_target(target_name),
        "resolved_runtime_services": runtime_services_for_target(target_name),
        "resolved_init_steps": init_steps_for_target(target_name),
    }


def list_targets() -> list[dict[str, Any]]:
    ordered_ids = _dedupe([*_dependency_order(), *list(_targets().keys())])
    return [get_target(target) for target in ordered_ids if target in _targets()]


def services_for_target(target: str | None) -> list[str]:
    services: list[str] = []
    for target_name in target_sequence_for_target(target):
        services.extend(_targets()[target_name].get("services", []))
    return _dedupe(services)


def runtime_services_for_target(target: str | None) -> list[str]:
    services: list[str] = []
    for target_name in target_sequence_for_target(target):
        services.extend(_targets()[target_name].get("runtime_services", []))
    return _dedupe(services)


def init_steps_for_target(target: str | None) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for target_name in target_sequence_for_target(target):
        for step in _targets()[target_name].get("init_steps", []):
            steps.append({"target": target_name, **step})
    return steps


def container_id_to_compose_service(container_id: str) -> str:
    if container_id not in MANAGED_CONTAINERS:
        raise ValueError(f"unknown container: {container_id}")
    return str(MANAGED_CONTAINERS[container_id]["compose_service"])


def is_known_target(name: str) -> bool:
    return name.strip().lower() in TARGET_ALIASES
