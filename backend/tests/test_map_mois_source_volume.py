"""Map MOIS 원천 SQLite는 project 범위 named volume 위에 있다 (Map ADR-108).

값을 박지 않고 **규칙**을 고정한다. compose에서 `KOR_TRAVEL_MAP_MOIS_SOURCE_DB_PATH`를 받는
활성 서비스를 유도하고, 그 경로와 같은 서비스의 named volume mount target을 **렌더링한 값**으로
비교한다 — 기본 환경과 디렉터리를 바꾼 환경 둘 다에서 경로의 디렉터리가 mount target과 같아야
한다. 원문 `${...}` 문자열을 비교하면 표현식이 같아도 렌더링이 갈라지는 경우(다른 기본값)를
놓친다. 렌더링은 Manager가 compose 경로에 쓰는 해석기(`_expand_env_path`)를 그대로 쓴다.
volume은 internal/default 정의여야 한다(`name`·`external`·`driver_opts` 없음) — 그래야 이름이
compose project에서 파생되어 격리 스택이 운영 원천을 공유하지 않는다(docs/docker-management.md).
"""

import posixpath
from pathlib import Path
from typing import Any

import pytest
import yaml

from kor_travel_docker_manager.services.c6c_deployment import _expand_env_path

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
_ENV_NAME = "KOR_TRAVEL_MAP_MOIS_SOURCE_DB_PATH"
_OVERRIDE_DIR = "/srv/kor-travel-map/mois-override"


def _compose() -> dict[str, Any]:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _active_services_with_source_path(compose: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: service
        for name, service in compose["services"].items()
        if _ENV_NAME in (service.get("environment") or {}) and not service.get("profiles")
    }


def _named_mount_targets(
    service: dict[str, Any], top_level: dict[str, Any], environment: dict[str, str]
) -> dict[str, str]:
    targets: dict[str, str] = {}
    for mount in service.get("volumes") or []:
        if not isinstance(mount, str):
            continue
        source, _, rest = mount.partition(":")
        if source not in top_level:
            continue
        target = rest.rsplit(":", 1)[0] if rest.endswith((":ro", ":rw")) else rest
        targets[source] = _expand_env_path(target, environment)
    return targets


def test_the_derivation_finds_the_map_code_server() -> None:
    """항진명제 방지 — 유도가 비면 아래 검사가 아무것도 재지 않는다."""
    assert _active_services_with_source_path(_compose()), (
        f"{_ENV_NAME}을 받는 활성 서비스가 없다 — 유도가 낡았다"
    )


def _directory_variables(raw_path: str) -> list[str]:
    """경로 표현식이 참조하는 변수 이름(재정의 환경을 만들 때 쓴다)."""
    names: list[str] = []
    index = raw_path.find("${")
    while index >= 0:
        closing = raw_path.find("}", index)
        expression = raw_path[index + 2 : closing]
        names.append(expression.split(":", 1)[0].split("-", 1)[0])
        index = raw_path.find("${", closing)
    return names


@pytest.mark.parametrize("override", [False, True], ids=["default-env", "overridden-dir"])
def test_rendered_source_directory_is_the_named_volume_target_of_the_same_service(
    override: bool,
) -> None:
    compose = _compose()
    top_level = compose.get("volumes") or {}
    for name, service in _active_services_with_source_path(compose).items():
        raw_path = str(service["environment"][_ENV_NAME])
        environment = (
            {variable: _OVERRIDE_DIR for variable in _directory_variables(raw_path)}
            if override
            else {}
        )
        rendered_dir = posixpath.dirname(_expand_env_path(raw_path, environment))
        if override:
            assert rendered_dir == _OVERRIDE_DIR, (
                f"{name}: 디렉터리 변수를 바꿔도 경로가 따라오지 않는다 ({rendered_dir})"
            )
        else:
            assert not rendered_dir.startswith("/tmp"), (
                f"{name}: 원천이 다시 컨테이너 overlay(/tmp)에 있다 ({rendered_dir})"
            )

        targets = _named_mount_targets(service, top_level, environment)
        assert rendered_dir in targets.values(), (
            f"{name}: 렌더링한 {_ENV_NAME} 디렉터리 {rendered_dir!r}가 named volume mount "
            f"target이 아니다 (named mounts: {targets})"
        )
        alias = next(source for source, target in targets.items() if target == rendered_dir)
        definition = top_level[alias] or {}
        forbidden = {"name", "external", "driver_opts"} & set(definition)
        assert not forbidden, f"{alias}: project 파생 이름을 깨는 키 {sorted(forbidden)}"
