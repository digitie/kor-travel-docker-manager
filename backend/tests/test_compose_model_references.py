"""Manager 모델의 모든 서비스 참조가 정본 compose가 **정의하는** 서비스로 풀린다.

2026-09-28에 geo·concierge·PinVi 전용 PostgreSQL(:12500/:12600/:12800)과 그 one-shot을
compose에서 뺐다. 그런 퇴역은 compose에서 서비스를 지우는 것만으로 끝나지 않는다 —
그 이름을 **다른 자리**가 들고 있으면 조용히 매달린다:

- `depends_on` 간선(`kor-travel-geo-api`가 "롤백 안전망"으로 옛 geo instance에 걸려
  있었다) — compose 자체가 기동을 거부한다.
- target의 `services`/`runtime_services`/`init_steps`와 컨테이너 선언, `compose_binds` —
  `ensure`/`status`가 없는 서비스를 compose에 묻는다.
- standalone 백업 role — 없는 컨테이너를 `docker exec`로 뜨다가 실패하거나, 떠 있는
  동결 사본을 떠서 **낡은 데이터**를 백업이라고 남긴다.
- C6c 계약이 이름으로 아는 서비스, 재구축이 지우는 one-shot, 배포 DB의 서비스 —
  재구축이 `no such service`로 죽는다.

그래서 각 자리를 **모델에서** 읽어(설정 문서·모듈이 이미 가진 값) compose의 서비스
집합과 대조한다. 목록을 여기 다시 적지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import c6c_deployment as c6c_deployment_module
from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services import database_runtime as database_runtime_module
from kor_travel_docker_manager.services import registry as registry_module
from kor_travel_docker_manager.services import standalone_backup as standalone_backup_module
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_ROOT = Path(__file__).resolve().parents[2]
#: `${NAME:-default}` / `${NAME-default}` 한 겹. 컨테이너 이름은 이 모양이거나 리터럴이다.
_DEFAULTED_VARIABLE = re.compile(r"^\$\{(?P<name>[A-Z_][A-Z0-9_]*):?-(?P<default>[^}]*)\}$")


def _compose() -> dict[str, Any]:
    document = load_yaml_rejecting_duplicate_keys(
        (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return document


def _compose_services() -> Mapping[str, Any]:
    services = _compose()["services"]
    assert isinstance(services, dict) and services
    return services


def _targets_config() -> dict[str, Any]:
    registry_module.load_targets_config.cache_clear()
    try:
        return dict(registry_module.load_targets_config())
    finally:
        registry_module.load_targets_config.cache_clear()


def _dangling(names: Iterator[tuple[str, str]], defined: Mapping[str, Any]) -> list[str]:
    return sorted(f"{where} -> {name}" for where, name in names if name not in defined)


def test_every_depends_on_edge_names_a_defined_service() -> None:
    services = _compose_services()

    def edges() -> Iterator[tuple[str, str]]:
        for name, service in services.items():
            depends_on = service.get("depends_on") or {}
            for dependency in depends_on:  # 짧은 문법(list)과 긴 문법(mapping) 둘 다
                yield f"services.{name}.depends_on", dependency

    assert list(edges()), "간선이 하나도 없다 — 검사가 아무것도 보지 않는다"
    assert _dangling(edges(), services) == []


def _init_step_service(command: list[str]) -> str:
    """`exec -T <svc> ...` / `run --rm <svc>`에서 서비스 자리를 읽는다."""

    operands = [token for token in command[1:] if not token.startswith("-")]
    assert operands, command
    return operands[0]


def test_every_manager_target_and_container_names_a_defined_service() -> None:
    services = _compose_services()
    config = _targets_config()

    def references() -> Iterator[tuple[str, str]]:
        for target_id, spec in config["targets"].items():
            # 외부 target의 서비스는 **그 저장소의** compose에 있다 — 여기서 대조할 수 없다.
            if spec.get("external_project"):
                continue
            for field in ("services", "runtime_services"):
                for name in spec.get(field) or ():
                    yield f"targets.{target_id}.{field}", name
            for step in spec.get("init_steps") or ():
                yield (
                    f"targets.{target_id}.init_steps.{step['name']}",
                    _init_step_service(list(step["command"])),
                )
        for container_id, spec in config["containers"].items():
            if spec.get("external_project"):
                continue
            yield f"containers.{container_id}.compose_service", spec["compose_service"]
        for name in config.get("compose_binds") or {}:
            yield "compose_binds", name

    assert list(references()), "참조가 하나도 없다 — 검사가 아무것도 보지 않는다"
    assert _dangling(references(), services) == []


def _container_names(services: Mapping[str, Any]) -> dict[str, str]:
    """compose가 정한 컨테이너 이름(env 기본값으로 푼 것) → 그 이름을 정하는 env 변수."""

    names: dict[str, str] = {}
    for service in services.values():
        container_name = service.get("container_name")
        if not isinstance(container_name, str):
            continue
        match = _DEFAULTED_VARIABLE.fullmatch(container_name)
        if match is None:
            names[container_name] = ""
        else:
            names[match["default"]] = match["name"]
    return names


@pytest.mark.parametrize("role", standalone_backup_module.BACKUP_ROLES)
def test_every_backup_role_dumps_a_container_the_compose_defines(role: str) -> None:
    """백업 role이 뜨는 컨테이너가 compose에 있고, 이름 override도 같은 변수를 따른다."""

    spec = standalone_backup_module._ROLE_CONFIG[role]  # type: ignore[index]
    container_env, container_default, _database = spec
    names = _container_names(_compose_services())
    assert container_default in names, (
        f"backup role {role} dumps {container_default}, which the compose does not define"
    )
    # compose가 이름을 env로 바꿀 수 있게 했으면 백업도 같은 env를 존중해야 한다 —
    # 안 그러면 override된 스택에서 엉뚱한(또는 없는) 컨테이너를 뜬다.
    assert (container_env or "") == names[container_default], (role, container_env)


def test_services_the_code_names_are_defined() -> None:
    """C6c 계약·재구축·배포 DB가 **이름으로** 아는 서비스는 compose에 있어야 한다."""

    services = _compose_services()

    def references() -> Iterator[tuple[str, str]]:
        for name in c6c_deployment_module._CANDIDATE_KNOWN_SERVICE_NAMES:
            yield "c6c._CANDIDATE_KNOWN_SERVICE_NAMES", name
        for field in (
            "_PINNED_RUNTIME_ONESHOT_WRITERS",
            "_PINNED_RUNTIME_EXTERNAL_PREREQUISITES",
            "RUNTIME_SERVICES",
        ):
            for name in getattr(compose_service_module, field):
                yield f"compose_service.{field}", name
        # ADR-53: 세 DB의 PostgreSQL 서비스는 이름이 아니라 DSN 포트에서 유도한다 — 코드에 이름이 없다.
        for role, spec in database_runtime_module._ROLE_CONFIG.items():
            assert not any(
                isinstance(item, str) and item in services for item in spec
            ), (role, spec)

    assert _dangling(references(), services) == []
