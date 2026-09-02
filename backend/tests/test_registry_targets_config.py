"""GM-11: docker-targets.yml 스키마 검증(`_validate_targets_config`) 회귀 테스트.

`_validate_targets_config`는 이미 파싱된 dict를 받는 순수 함수라, 대부분의 케이스는
그 함수를 직접 호출해 검증한다(모듈 레벨 `load_targets_config` lru_cache나 import 시점
`MANAGED_CONTAINERS` 등 전역 계산을 건드리지 않아 테스트 순서에 영향이 없다).
`load_targets_config()` 자체의 배선(중복 키 로더 + 검증 호출)은 별도로, 임시 파일과
`KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE` 환경변수 override + `cache_clear()`로만
격리해 확인한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import registry as registry_module
from kor_travel_docker_manager.services.registry import _validate_targets_config


def _minimal_valid_config() -> dict[str, Any]:
    return {
        "containers": {
            "geo_db": {
                "compose_service": "geo-db",
                "name": "geo_db",
                "display_name": "Geo DB",
                "role": "database",
                "connection": {},
                "expected_ports": [],
            },
        },
        "targets": {
            "geo": {
                "containers": ["geo_db"],
                "services": ["geo-db"],
            },
        },
        "dependency_order": ["geo"],
    }


def test_minimal_valid_config_passes() -> None:
    _validate_targets_config(_minimal_valid_config(), label="test.yml")


def test_real_config_passes_validation() -> None:
    registry_module.load_targets_config.cache_clear()
    try:
        config = registry_module.load_targets_config()
    finally:
        registry_module.load_targets_config.cache_clear()
    assert "containers" in config
    assert "targets" in config


@pytest.mark.parametrize("missing_field", list(registry_module._REQUIRED_CONTAINER_FIELDS))
def test_container_missing_required_field_is_rejected(missing_field: str) -> None:
    config = _minimal_valid_config()
    del config["containers"]["geo_db"][missing_field]
    with pytest.raises(ValueError, match=f"missing required field '{missing_field}'"):
        _validate_targets_config(config, label="test.yml")


def test_container_spec_must_be_a_mapping() -> None:
    config = _minimal_valid_config()
    config["containers"]["geo_db"] = "not-a-mapping"
    with pytest.raises(ValueError, match="containers.geo_db: must be a mapping"):
        _validate_targets_config(config, label="test.yml")


def test_target_spec_must_be_a_mapping() -> None:
    config = _minimal_valid_config()
    config["targets"]["geo"] = ["not", "a", "mapping"]
    with pytest.raises(ValueError, match="targets.geo: must be a mapping"):
        _validate_targets_config(config, label="test.yml")


@pytest.mark.parametrize("field", ["depends_on", "include", "containers", "aliases"])
def test_scalar_instead_of_list_is_rejected_with_a_clear_message(field: str) -> None:
    config = _minimal_valid_config()
    config["targets"]["geo"][field] = "geo_db"
    with pytest.raises(
        ValueError, match=f"targets.geo.{field}: must be a list, got str"
    ):
        _validate_targets_config(config, label="test.yml")


def test_null_field_is_treated_as_empty_list() -> None:
    config = _minimal_valid_config()
    config["targets"]["geo"]["depends_on"] = None
    _validate_targets_config(config, label="test.yml")


def test_unknown_depends_on_target_is_rejected() -> None:
    config = _minimal_valid_config()
    config["targets"]["geo"]["depends_on"] = ["does_not_exist"]
    with pytest.raises(
        ValueError, match="targets.geo.depends_on: unknown target 'does_not_exist'"
    ):
        _validate_targets_config(config, label="test.yml")


def test_unknown_include_target_is_rejected() -> None:
    config = _minimal_valid_config()
    config["targets"]["geo"]["include"] = ["does_not_exist"]
    with pytest.raises(
        ValueError, match="targets.geo.include: unknown target 'does_not_exist'"
    ):
        _validate_targets_config(config, label="test.yml")


def test_unknown_container_reference_is_rejected() -> None:
    config = _minimal_valid_config()
    config["targets"]["geo"]["containers"] = ["does_not_exist"]
    with pytest.raises(
        ValueError, match="targets.geo.containers: unknown container 'does_not_exist'"
    ):
        _validate_targets_config(config, label="test.yml")


def test_colliding_alias_across_two_targets_is_rejected() -> None:
    config = _minimal_valid_config()
    config["targets"]["conc"] = {"containers": [], "services": [], "aliases": ["geo"]}
    with pytest.raises(
        ValueError,
        match="targets.conc.aliases: alias 'geo' already used by target 'geo'",
    ):
        _validate_targets_config(config, label="test.yml")


def test_target_self_alias_does_not_collide_with_itself() -> None:
    config = _minimal_valid_config()
    config["targets"]["geo"]["aliases"] = ["geo", "GEO"]
    _validate_targets_config(config, label="test.yml")


def test_alias_collision_is_case_insensitive() -> None:
    config = _minimal_valid_config()
    config["targets"]["conc"] = {"containers": [], "services": [], "aliases": ["GEO"]}
    with pytest.raises(ValueError, match="alias 'GEO' already used by target 'geo'"):
        _validate_targets_config(config, label="test.yml")


def test_unknown_dependency_order_entry_is_rejected() -> None:
    config = _minimal_valid_config()
    config["dependency_order"] = ["geo", "does_not_exist"]
    with pytest.raises(
        ValueError, match="dependency_order: unknown target 'does_not_exist'"
    ):
        _validate_targets_config(config, label="test.yml")


def test_error_message_includes_the_caller_supplied_label() -> None:
    config = _minimal_valid_config()
    config["dependency_order"] = ["does_not_exist"]
    with pytest.raises(ValueError, match=r"^custom-label\.yml "):
        _validate_targets_config(config, label="custom-label.yml")


_VALID_YAML = """
containers:
  geo_db:
    compose_service: geo-db
    name: geo_db
    display_name: Geo DB
    role: database
    connection: {}
    expected_ports: []
targets:
  geo:
    containers: [geo_db]
    services: [geo-db]
dependency_order: [geo]
"""

_DUPLICATE_KEY_YAML = """
containers:
  geo_db:
    compose_service: geo-db
    name: geo_db
    display_name: Geo DB
    role: database
    connection: {}
    expected_ports: []
  geo_db:
    compose_service: geo-db-again
    name: geo_db
    display_name: Geo DB
    role: database
    connection: {}
    expected_ports: []
targets:
  geo:
    containers: [geo_db]
    services: [geo-db]
dependency_order: [geo]
"""

_BROKEN_REFERENCE_YAML = """
containers:
  geo_db:
    compose_service: geo-db
    name: geo_db
    display_name: Geo DB
    role: database
    connection: {}
    expected_ports: []
targets:
  geo:
    containers: [geo_db]
    services: [geo-db]
    depends_on: [typo_target]
dependency_order: [geo]
"""


@pytest.fixture
def _isolated_targets_config_cache(monkeypatch: pytest.MonkeyPatch):
    registry_module.load_targets_config.cache_clear()
    yield
    monkeypatch.delenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", raising=False)
    registry_module.load_targets_config.cache_clear()


def test_load_targets_config_accepts_a_valid_fixture_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _isolated_targets_config_cache: None,
) -> None:
    config_path = tmp_path / "docker-targets.yml"
    config_path.write_text(_VALID_YAML, encoding="utf-8")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", str(config_path))

    config = registry_module.load_targets_config()

    assert "geo_db" in config["containers"]


def test_load_targets_config_rejects_duplicate_yaml_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _isolated_targets_config_cache: None,
) -> None:
    config_path = tmp_path / "docker-targets.yml"
    config_path.write_text(_DUPLICATE_KEY_YAML, encoding="utf-8")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", str(config_path))

    with pytest.raises(Exception, match="duplicate key"):
        registry_module.load_targets_config()


def test_load_targets_config_rejects_broken_reference_via_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _isolated_targets_config_cache: None,
) -> None:
    config_path = tmp_path / "docker-targets.yml"
    config_path.write_text(_BROKEN_REFERENCE_YAML, encoding="utf-8")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", str(config_path))

    with pytest.raises(
        ValueError, match="depends_on: unknown target 'typo_target'"
    ):
        registry_module.load_targets_config()


def test_lazy_mapping_defers_loader_until_first_real_access() -> None:
    """docker-targets.yml 스키마 검증 잔여(GM-11 후속): `MANAGED_CONTAINERS`/
    `MANAGED_TARGETS`/`TARGET_ALIASES`를 모듈 import 시점에 즉시 계산되는 plain
    dict 대신 `_LazyMapping`으로 바꾼 핵심 계약 — 생성 자체는 `loader`를 절대
    호출하지 않고, 구독/순회/`in`/`.items()` 같은 실제 접근에서만 호출해야
    `ktdctl`뿐 아니라 이 모듈을 그저 import만 하는 다른 프로세스(FastAPI 등)도
    깨진 config에서 import 시점에 죽지 않는다."""

    calls: list[int] = []

    def loader() -> dict[str, Any]:
        calls.append(1)
        return {"a": 1}

    mapping = registry_module._LazyMapping(loader)

    assert calls == []  # 생성만으로는 loader가 호출되지 않아야 한다.

    assert mapping["a"] == 1
    assert calls == [1]
    assert "a" in mapping
    assert list(mapping) == ["a"]
    assert dict(mapping.items()) == {"a": 1}
    assert mapping.get("missing", "default") == "default"


def test_managed_containers_raises_only_on_first_real_access_with_broken_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _isolated_targets_config_cache: None,
) -> None:
    """실제 모듈 레벨 `MANAGED_CONTAINERS` 객체 자신이 깨진 config에서 구독/`in`
    시점에 `_validate_targets_config`의 명확한 메시지를 그대로 낸다는 것을
    확인한다(단순 참조는 여기서 안전을 재확인하지 않는다 — 모듈이 이미
    import돼 있어 재import로는 계약을 재현할 수 없기 때문에, 실제 접근에서
    예외가 나는지만 본다)."""

    config_path = tmp_path / "docker-targets.yml"
    config_path.write_text(_BROKEN_REFERENCE_YAML, encoding="utf-8")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", str(config_path))

    with pytest.raises(ValueError, match="depends_on: unknown target 'typo_target'"):
        assert "kor-travel-geo-postgresql" in registry_module.MANAGED_CONTAINERS
