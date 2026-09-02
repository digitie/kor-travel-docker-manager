import os
from collections.abc import Callable, Iterator, Mapping
from functools import lru_cache
from typing import Any

from kor_travel_docker_manager.services.yaml_strict import (
    load_yaml_rejecting_duplicate_keys,
)

_REQUIRED_CONTAINER_FIELDS = (
    "compose_service",
    "name",
    "display_name",
    "role",
    "connection",
    "expected_ports",
)


class TargetsConfigError(ValueError):
    """`config/docker-targets.yml` 스키마·참조 무결성 검증 실패 전용 타입.

    적대적 리뷰 2건(item2-targets-validate 재검토) 반영: `cli.py`의 `main()`은
    이 타입만 잡아 "config가 깨졌으니 깔끔한 한 줄 메시지로 안내"하는 fail-open
    경로를 태운다. 예전에는 `main()`이 bare `ValueError`를 통째로 잡았는데,
    `compose_service.py`/`c6c_deployment.py`의 내부 불변식 위반(스테이지 값 오류,
    재시도 횟수 음수 등, 버그이지 config 오타가 아님)까지 같이 삼켜 "config
    오류처럼 보이는 exit 1"로 둔갑시킬 위험이 있었다 — 오늘은 그 두 사이트가 이미
    자기 자신의 좁은 `except`로 먼저 잡혀 실제로 새지는 않지만, 새 명령이 추가될
    때마다 반복 확인할 근거가 없었다. `ValueError`를 상속하므로 기존
    `except ValueError:` 호출부(레지스트리 자신의 `resolve_target_name`/
    `container_id_to_compose_service` 같은 "잘못된 사용자 입력" 오류를 잡던
    `_cmd_status`/`_cmd_ensure`/`_cmd_action` 등)는 전혀 바뀌지 않는다 — 이
    서브클래스는 오직 `main()`/`_cmd_targets_validate`/
    `MetricsCollector.__init__`이 정확히 무엇을 fail-open으로 삼키는지 좁히기
    위한 것이다.
    """


def get_project_root() -> str:
    configured = os.environ.get("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", "").strip()
    if configured:
        return os.path.abspath(configured)
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
        config = load_yaml_rejecting_duplicate_keys(f.read()) or {}

    if not isinstance(config.get("containers"), dict):
        raise TargetsConfigError("docker target config must define containers")
    if not isinstance(config.get("targets"), dict):
        raise TargetsConfigError("docker target config must define targets")
    if not isinstance(config.get("dependency_order"), list):
        raise TargetsConfigError("docker target config must define dependency_order")
    _validate_targets_config(config, label=os.path.basename(path))
    return config


def _require_list_field(
    spec: dict[str, Any], field: str, *, target_id: str, label: str
) -> list[Any]:
    """`field`가 리스트가 아니라 스칼라(예: 대괄호를 빼먹은 `depends_on: geo`)면
    글자 단위로 순회돼 `unknown target 'g'`처럼 원인을 짐작할 수 없는 메시지가
    나간다 — 리스트 여부를 먼저 검사해 그 자리에서 바로 지목한다."""

    value = spec.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise TargetsConfigError(
            f"{label} targets.{target_id}.{field}: must be a list, got "
            f"{type(value).__name__}"
        )
    return value


def _validate_targets_config(config: dict[str, Any], *, label: str) -> None:
    """GM-11: 오타 하나가 raw KeyError로 죽거나 조용히 무시되지 않게 fail-close한다.

    `containers`가 depends_on 폐포에서 기계적으로 유도 가능하다는 원래 개선안의
    전제는 틀렸다 — 모니터링 target(gra/cadv/prom)이 앱 target의 `depends_on`
    폐포에 들어가지만 실제 `containers` 목록에는 없다(기동 순서 선형화일 뿐
    논리적 의존이 아니기 때문). 그래서 여기서는 유도를 시도하지 않고, 이미 적힌
    `containers`/`depends_on`/`include`/`aliases`가 서로 참조 무결성을 지키는지만
    검증한다.
    """

    containers = config["containers"]
    targets = config["targets"]

    for container_id, spec in containers.items():
        if not isinstance(spec, dict):
            raise TargetsConfigError(f"{label} containers.{container_id}: must be a mapping")
        for field in _REQUIRED_CONTAINER_FIELDS:
            if field not in spec:
                raise TargetsConfigError(
                    f"{label} containers.{container_id}: missing required field '{field}'"
                )

    seen_aliases: dict[str, str] = {}
    for target_id, spec in targets.items():
        if not isinstance(spec, dict):
            raise TargetsConfigError(f"{label} targets.{target_id}: must be a mapping")

        for dep in _require_list_field(spec, "depends_on", target_id=target_id, label=label):
            if dep not in targets:
                raise TargetsConfigError(
                    f"{label} targets.{target_id}.depends_on: unknown target '{dep}'"
                )
        for included in _require_list_field(spec, "include", target_id=target_id, label=label):
            if included not in targets:
                raise TargetsConfigError(
                    f"{label} targets.{target_id}.include: unknown target '{included}'"
                )
        for container_id in _require_list_field(
            spec, "containers", target_id=target_id, label=label
        ):
            if container_id not in containers:
                raise TargetsConfigError(
                    f"{label} targets.{target_id}.containers: unknown container '{container_id}'"
                )

        aliases = _require_list_field(spec, "aliases", target_id=target_id, label=label)
        for alias in [target_id, *aliases]:
            normalized = str(alias).strip().lower()
            owner = seen_aliases.get(normalized)
            if owner is not None and owner != target_id:
                raise TargetsConfigError(
                    f"{label} targets.{target_id}.aliases: alias '{alias}' already used "
                    f"by target '{owner}'"
                )
            seen_aliases[normalized] = target_id

    for name in config["dependency_order"]:
        if name not in targets:
            raise TargetsConfigError(f"{label} dependency_order: unknown target '{name}'")


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


class _LazyMapping(Mapping[str, Any]):
    """`loader`를 최초 실제 접근(구독/순회/`in`/`.items()` 등) 시점에만 호출하는
    지연 dict-like 객체.

    module import 시점에는 loader를 실행하지 않으므로, 이 객체를 그저 import만
    하고 실제로 사용하지 않는 경로(예: `ktdctl --help`가 registry.py를 참조하는
    다른 모듈을 거쳐 import될 때)는 설정 파일이 깨져 있어도 import 자체는 깨지지
    않는다. `loader`가 내부적으로 참조하는 `load_targets_config()`가 이미
    `@lru_cache`이므로 최초 접근 이후 재계산 비용은 없다.
    """

    def __init__(self, loader: Callable[[], dict[str, Any]]) -> None:
        self._loader = loader

    def _resolve(self) -> dict[str, Any]:
        return self._loader()

    def __getitem__(self, key: str) -> Any:
        return self._resolve()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._resolve())

    def __len__(self) -> int:
        return len(self._resolve())

    def __repr__(self) -> str:
        return repr(self._resolve())


MANAGED_CONTAINERS: Mapping[str, dict[str, Any]] = _LazyMapping(
    lambda: load_targets_config()["containers"]
)
MANAGED_TARGETS: Mapping[str, dict[str, Any]] = _LazyMapping(_targets)
TARGET_ALIASES: Mapping[str, str] = _LazyMapping(_build_aliases)


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
