"""Map MOIS 원천 SQLite는 project 범위 named volume 위에 있다 (Map ADR-108).

값을 박지 않고 **규칙**을 고정한다. compose에서 `KOR_TRAVEL_MAP_MOIS_SOURCE_DB_PATH`를 받는
활성 서비스를 유도하고, 그 경로의 디렉터리가 같은 서비스의 named volume mount target과 **같은
표현식**인지 본다 — 둘이 다른 리터럴이면 한쪽만 바뀌어 원천이 다시 컨테이너 overlay에 쓰인다.
volume은 internal/default 정의여야 한다(`name`·`external`·`driver_opts` 없음) — 그래야 이름이
compose project에서 파생되어 격리 스택이 운영 원천을 공유하지 않는다(docs/docker-management.md).
"""

from pathlib import Path
from typing import Any

import yaml

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
_ENV_NAME = "KOR_TRAVEL_MAP_MOIS_SOURCE_DB_PATH"


def _compose() -> dict[str, Any]:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _active_services_with_source_path(compose: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: service
        for name, service in compose["services"].items()
        if _ENV_NAME in (service.get("environment") or {}) and not service.get("profiles")
    }


def test_the_derivation_finds_the_map_code_server() -> None:
    """항진명제 방지 — 유도가 비면 아래 검사가 아무것도 재지 않는다."""
    assert _active_services_with_source_path(_compose()), (
        f"{_ENV_NAME}을 받는 활성 서비스가 없다 — 유도가 낡았다"
    )


def test_source_db_directory_is_the_named_volume_target_of_the_same_service() -> None:
    compose = _compose()
    top_level = compose.get("volumes") or {}
    for name, service in _active_services_with_source_path(compose).items():
        db_path = str(service["environment"][_ENV_NAME])
        directory = db_path.rsplit("/", 1)[0]
        assert not directory.startswith("/tmp"), f"{name}: 원천이 다시 overlay(/tmp)에 있다"

        named_targets = {
            source: target
            for source, _, target in (
                str(mount).partition(":")
                for mount in service.get("volumes") or []
                if isinstance(mount, str)
            )
            if source in top_level
        }
        assert directory in named_targets.values(), (
            f"{name}: {_ENV_NAME}의 디렉터리 {directory!r}가 named volume mount target이 아니다 "
            f"(named mounts: {named_targets})"
        )
        alias = next(source for source, target in named_targets.items() if target == directory)
        definition = top_level[alias] or {}
        forbidden = {"name", "external", "driver_opts"} & set(definition)
        assert not forbidden, f"{alias}: project 파생 이름을 깨는 키 {sorted(forbidden)}"
