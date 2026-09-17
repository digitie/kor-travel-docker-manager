"""GM-11: docker-targets.yml 스키마 검증(`_validate_targets_config`) 회귀 테스트.

`_validate_targets_config`는 이미 파싱된 dict를 받는 순수 함수라, 대부분의 케이스는
그 함수를 직접 호출해 검증한다(모듈 레벨 `load_targets_config` lru_cache나 import 시점
`MANAGED_CONTAINERS` 등 전역 계산을 건드리지 않아 테스트 순서에 영향이 없다).
`load_targets_config()` 자체의 배선(중복 키 로더 + 검증 호출)은 별도로, 임시 파일과
`KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE` 환경변수 override + `cache_clear()`로만
격리해 확인한다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from kor_travel_docker_manager.services import registry as registry_module

_ROOT = Path(__file__).resolve().parents[2]
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
        # GM-17 A 이후 `compose_binds`는 필수다 — 절이 없으면 모든 operator bind가
        # baseline 밖이 되어 배포가 전부 거부되므로, 그 상태를 통과시키지 않는다.
        "compose_binds": {
            "geo-db": [
                {
                    "container_path": "/var/lib/postgresql/data",
                    "read_only": False,
                    "source": "./geo-pgdata",
                }
            ]
        },
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


def test_validate_targets_config_raises_the_targets_config_error_subclass() -> None:
    """적대적 리뷰 2건(item2-targets-validate 재검토)이 짚은 결함 대응:
    `cli.py`의 `main()`은 이제 bare `ValueError`가 아니라
    `TargetsConfigError`만 좁혀 잡는다(config 오타와 무관한 내부 불변식
    위반까지 "config 오류인 척"하는 exit 1로 둔갑시키지 않기 위해). 위
    테스트들이 쓰는 `pytest.raises(ValueError, ...)`는 `TargetsConfigError`가
    `ValueError`를 상속하기만 하면 계속 통과하므로, 실제로 이 서브클래스가
    나오는지는 별도로 타입 자체를 고정해야 한다."""

    config = _minimal_valid_config()
    config["dependency_order"] = ["does_not_exist"]
    with pytest.raises(registry_module.TargetsConfigError):
        _validate_targets_config(config, label="test.yml")


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
compose_binds:
  geo-db:
    - container_path: "/data"
      read_only: true
      source: "./x"
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
compose_binds:
  geo-db:
    - container_path: "/data"
      read_only: true
      source: "./x"
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
compose_binds:
  geo-db:
    - container_path: "/data"
      read_only: true
      source: "./x"
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


@pytest.mark.parametrize(
    ("label", "content"),
    [
        (
            "duplicate_key",
            "containers:\n"
            "  a: {name: x, compose_service: x}\n"
            "  a: {name: y, compose_service: y}\n"
            "targets: {}\ndependency_order: []\n",
        ),
        ("yaml_syntax", "containers:\n  a: {name: x\ntargets: {}\n"),
    ],
)
def test_targets_validate_reports_hand_edit_mistakes_without_a_traceback(
    tmp_path: Path, label: str, content: str
) -> None:
    """중복 키와 들여쓰기 오류는 손편집 시 가장 흔한 실수인데, 종전에는
    `TargetsConfigError`만 잡아 각각 52줄·61줄 raw traceback으로 샜다.

    실패 지점이 `main()` 이전의 import 체인
    (`cli -> docker_service -> metrics_collector` 모듈 레벨 싱글턴)이라
    fresh subprocess가 아니면 재현되지 않는다 — pytest는 collection 시점에
    이미 정상 config로 그 모듈을 import해 두기 때문이다(적대 리뷰 2인).
    """

    config = tmp_path / f"{label}.yml"
    config.write_text(content, encoding="utf-8")
    completed = _run_cli_subprocess(config)

    assert completed.returncode != 0
    assert "Traceback (most recent call last)" not in completed.stderr, completed.stderr
    assert completed.stderr.strip(), "원인을 알 수 없는 침묵 실패는 안 된다"


def test_targets_validate_reports_a_missing_config_in_one_line(tmp_path: Path) -> None:
    """파일 부재는 26줄 traceback이었다."""

    completed = _run_cli_subprocess(tmp_path / "absent.yml")

    assert completed.returncode != 0
    assert "Traceback (most recent call last)" not in completed.stderr, completed.stderr
    assert len(completed.stderr.strip().splitlines()) == 1, completed.stderr


def _run_cli_subprocess(config: Path) -> subprocess.CompletedProcess[str]:
    """`main()` 이전 import 체인까지 포함해 재현하려면 fresh 프로세스여야 한다."""

    root = Path(__file__).resolve().parents[1] / "src"
    environment = {
        **os.environ,
        "KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE": str(config),
        "PYTHONPATH": str(root),
    }
    return subprocess.run(
        [sys.executable, "-m", "kor_travel_docker_manager.cli", "targets", "validate"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=environment,
    )


def test_real_config_only_names_services_that_exist_in_docker_compose() -> None:
    """targets와 compose는 같은 커밋에서 같이 움직여야 한다.

    `_validate_targets_config`는 targets 파일 **내부** 참조만 본다 — compose를 열지
    않는다. 그래서 한쪽에서만 서비스를 지우면 `ktdctl targets validate`는 OK를 찍고,
    `status all`/`ensure all`이 런타임에 `no such service`로 죽을 때까지 아무것도
    잡지 못한다. 2026-09-05 weather 등록 해제에서 두 파일을 반드시 함께 움직여야 했던
    이유가 이것이다.
    """

    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    compose_services = set(compose["services"])

    registry_module.load_targets_config.cache_clear()
    try:
        config = registry_module.load_targets_config()
    finally:
        registry_module.load_targets_config.cache_clear()

    # 외부 프로젝트(형제 저장소)의 서비스는 이 검사의 전제 밖이다 — 정본 compose가
    # 다른 저장소에 있어 이 저장소의 커밋으로 묶을 수 없다. 면제하되, 면제가 구멍이
    # 되지 않도록 아래에서 **더 강한** 검사를 함께 한다.
    declared_projects = {
        spec["external_project"]["project"]
        for spec in config["targets"].values()
        if spec.get("external_project")
    }

    dangling: list[str] = []
    for container_id, spec in config["containers"].items():
        if spec.get("external_project"):
            continue
        service = spec["compose_service"]
        if service not in compose_services:
            dangling.append(f"containers.{container_id}.compose_service={service!r}")
    for target_id, spec in config["targets"].items():
        if spec.get("external_project"):
            continue
        for field in ("services", "runtime_services"):
            for service in spec.get(field) or []:
                if service not in compose_services:
                    dangling.append(f"targets.{target_id}.{field}: {service!r}")

    assert not dangling, (
        f"docker-targets.yml이 compose에 없는 서비스를 가리킨다: {dangling!r}"
    )

    # (1) 컨테이너가 가리키는 프로젝트는 실제로 선언된 것이어야 한다.
    #     이것이 없으면 `external_project: kor-travel-weater` 같은 오타가 면제만 받고
    #     조용히 통과한다.
    unknown_projects = sorted(
        f"containers.{container_id}.external_project={spec['external_project']!r}"
        for container_id, spec in config["containers"].items()
        if spec.get("external_project")
        and spec["external_project"] not in declared_projects
    )
    assert not unknown_projects, (
        f"어떤 target도 선언하지 않은 프로젝트를 가리킨다: {unknown_projects!r} "
        f"(선언된 것: {sorted(declared_projects)!r})"
    )

    # (2)(3) target과 컨테이너의 소속이 어긋나면 안 된다.
    mismatched: list[str] = []
    for target_id, spec in config["targets"].items():
        expected = (
            spec["external_project"]["project"] if spec.get("external_project") else None
        )
        for container_id in spec.get("containers") or []:
            actual = config["containers"][container_id].get("external_project")
            if actual != expected:
                mismatched.append(
                    f"targets.{target_id} (project={expected!r}) -> "
                    f"containers.{container_id} (project={actual!r})"
                )
    assert not mismatched, (
        "target과 컨테이너의 프로젝트 소속이 어긋난다 — Manager target의 컨테이너에는"
        f" external_project가 없어야 하고, 외부 target의 컨테이너는 전부 같은"
        f" 프로젝트여야 한다: {mismatched!r}"
    )


# ── GM-17 선행조건: targets 문서의 자리와 무결성 ─────────────────────────
#
# GM-17 본작업은 bind allowlist(`_CANDIDATE_ALLOWED_OPERATOR_BINDS`)를 이 문서로
# 옮기려 한다. 그 순간 이 파일은 **"어떤 host 경로가 production 컨테이너에
# 마운트돼도 되는가"를 결정하는 보안 경계**가 된다. 그런데 종전 로더는
# `KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE` 하나로 자리를 아무 데로나 돌릴 수 있었고
# 소유권·권한 검증이 **0건**이었다(감사 노트 (b): "그대로 옮기면 보안 회귀").
# 아래는 그 선행조건을 결박한다 — 이것이 초록이어야 allowlist를 옮길 수 있다.


def _trusted(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    """`running_from_trusted_install_root`를 그 자리에서 갈아 끼운다.

    실제 `/opt` 아래에서 도는 것을 테스트가 재현할 수는 없다. 대신 registry가
    **그 판정을 실제로 물어본다**는 사실에 결박한다 — 묻지 않게 되면 아래가 빨개진다.
    """

    import kor_travel_docker_manager.services.trusted_install as trusted_install

    monkeypatch.setattr(
        trusted_install, "running_from_trusted_install_root", lambda: value
    )


def test_trusted_install_pins_the_targets_path_and_refuses_redirection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """설치본에서는 env로 자리를 옮길 수 없다 — 조용히 무시하지 않고 **거절**한다.

    무시하면 "왜 내 설정이 안 먹지"가 되고 그 물음이 운영자를 다시 env로 데려간다.
    """

    _trusted(monkeypatch, True)
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", "/tmp/attacker.yml")

    with pytest.raises(registry_module.TargetsConfigError) as excinfo:
        registry_module.get_targets_config_path()
    assert "KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE" in str(excinfo.value)


def test_trusted_install_also_refuses_project_root_redirection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """자리를 한 단계 위에서 돌리는 것도 같은 구멍이다.

    `PROJECT_ROOT`만 막지 않으면 `TARGETS_FILE`을 막은 것이 장식이 된다 —
    기본 경로가 `get_project_root()`에서 나오기 때문이다.
    """

    _trusted(monkeypatch, True)
    monkeypatch.delenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", raising=False)
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", "/tmp/attacker-root")

    with pytest.raises(registry_module.TargetsConfigError) as excinfo:
        registry_module.get_targets_config_path()
    assert "KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT" in str(excinfo.value)


def test_trusted_install_allows_an_override_that_points_at_the_pinned_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """같은 자리를 가리키는 override는 무해하므로 막지 않는다.

    installer·launcher가 명시적으로 넘기는 경우가 있다. 그것까지 거절하면 정당한
    호출을 깨뜨리고, 그러면 다음 사람이 이 검사를 통째로 들어낸다.
    """

    from kor_travel_docker_manager.services.trusted_install import TRUSTED_INSTALL_ROOT

    _trusted(monkeypatch, True)
    pinned = TRUSTED_INSTALL_ROOT / "config" / "docker-targets.yml"
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", str(pinned))

    assert registry_module.get_targets_config_path() == str(pinned)


def test_development_checkout_keeps_the_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """개발 checkout에서는 종전 그대로다 — 거기서 이 override는 정당한 편의다.

    이 검사가 없으면 위 셋을 만족시키려고 override를 전역으로 막게 되고, 그러면
    이 로더를 부르는 모든 명령(CLI·metrics·compose)이 개발 환경에서 죽는다.
    """

    _trusted(monkeypatch, False)
    elsewhere = tmp_path / "docker-targets.yml"
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE", str(elsewhere))

    assert registry_module.get_targets_config_path() == str(elsewhere)


def test_trusted_install_refuses_a_non_root_owned_targets_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """설치본에서 읽는 문서는 root 소유여야 한다 — 설치본의 데이터만 신뢰한다.

    tmp 파일은 테스트 사용자 소유이므로, `_read_targets_bytes`가 소유권을 실제로
    보는 한 이것은 반드시 거절된다. 검사를 지우면 초록이 된다.
    """

    _trusted(monkeypatch, True)
    config = tmp_path / "docker-targets.yml"
    config.write_text("version: 1\n", encoding="utf-8")

    if os.geteuid() == 0:  # pragma: no cover - CI는 root로 돌지 않는다
        pytest.skip("root로 돌면 tmp 파일도 root 소유라 이 구분이 성립하지 않는다")

    with pytest.raises(registry_module.TargetsConfigError) as excinfo:
        registry_module._read_targets_bytes(str(config))
    assert "root 소유가 아니다" in str(excinfo.value)


def test_development_checkout_reads_a_user_owned_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """개발 checkout의 사용자 소유 파일은 그대로 읽힌다."""

    _trusted(monkeypatch, False)
    config = tmp_path / "docker-targets.yml"
    config.write_text("version: 1\n", encoding="utf-8")

    assert registry_module._read_targets_bytes(str(config)) == b"version: 1\n"


def test_targets_file_symlink_is_refused(tmp_path: Path) -> None:
    """심링크는 열지 않는다(`O_NOFOLLOW`).

    경로를 검사하고 나서 여는 사이에 심링크로 바꿔치기하는 것이 이 패턴이 막는
    바로 그 경로다 — 그래서 경로가 아니라 **열린 fd**를 `fstat`한다.
    """

    real = tmp_path / "real.yml"
    real.write_text("version: 1\n", encoding="utf-8")
    link = tmp_path / "link.yml"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows 권한
        pytest.skip("이 환경에서는 심링크를 만들 수 없다")

    with pytest.raises(registry_module.TargetsConfigError):
        registry_module._read_targets_bytes(str(link))


def test_oversized_targets_file_is_refused_before_parsing(tmp_path: Path) -> None:
    """상한을 넘으면 **파싱 전에** 멈춘다 — 크기로 YAML 파서를 밀지 않는다."""

    config = tmp_path / "docker-targets.yml"
    config.write_bytes(b"#" * (registry_module._MAX_TARGETS_BYTES + 1))

    with pytest.raises(registry_module.TargetsConfigError) as excinfo:
        registry_module._read_targets_bytes(str(config))
    assert "지원 크기" in str(excinfo.value)


# ── GM-17 본작업 A: bind allowlist가 설정에서 온다 ───────────────────────
#
# 종전에는 c6c_deployment.py의 125줄짜리 dict 리터럴이었다. 새 bind 하나 또는 여섯
# 번째 프로젝트의 pgdata에도 backend 수정 + trusted release 재설치가 필요했고, 그것이
# GM-17이 지목한 범용성의 실질 병목이다. 아래는 **자리를 옮기되 값이 바뀌지 않았다**는
# 성질과, 설정이 경계를 지킬 만큼 엄격하게 검증된다는 성질을 함께 결박한다.


def test_bind_allowlist_comes_from_config_not_code() -> None:
    """상수가 코드에 남아 있으면 정본이 둘이 된다 — 그러면 조용히 갈라진다."""

    source = (
        _ROOT / "backend/src/kor_travel_docker_manager/services/c6c_deployment.py"
    ).read_text(encoding="utf-8")
    assert "_CANDIDATE_ALLOWED_OPERATOR_BINDS" not in source, (
        "허용 bind 목록이 다시 코드 상수로 돌아왔다 — 정본은 "
        "config/docker-targets.yml의 compose_binds 절이다"
    )


def test_bind_allowlist_is_keyed_by_service_path_and_readonly() -> None:
    """소비부가 기대하는 키 모양 그대로여야 한다.

    `c6c_deployment`는 `(service, mount.target, mount.read_only)`로 조회한다.
    로더가 다른 모양을 내면 **모든 bind가 baseline에 없는 것이 되어** 배포가
    통째로 거부된다 — 조용한 실패가 아니라 시끄러운 실패지만, 그 시끄러움이
    배포 도중에 오면 늦다.
    """

    allowlist = registry_module.load_compose_bind_allowlist()
    assert allowlist, "compose_binds 절이 비었다"
    for key, source in allowlist.items():
        assert isinstance(key, tuple) and len(key) == 3, key
        service, container_path, read_only = key
        assert isinstance(service, str) and service
        assert isinstance(container_path, str) and container_path.startswith("/")
        assert isinstance(read_only, bool)
        assert isinstance(source, str) and source


def test_every_bind_service_exists_in_compose() -> None:
    """허용 목록이 compose에 없는 서비스를 가리키면 그 항목은 아무것도 지키지 않는다.

    `docker-targets.yml`과 `docker-compose.yml`을 함께 움직여야 하는 이유는 이
    파일의 다른 절에 이미 적혀 있다 — bind 절도 같은 규율을 받는다.
    """

    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    compose_services = set(compose["services"])
    dangling = sorted(
        {
            service
            for service, _, _ in registry_module.load_compose_bind_allowlist()
            if service not in compose_services
        }
    )
    assert not dangling, f"compose_binds가 compose에 없는 서비스를 가리킨다: {dangling}"


def test_compose_binds_rejects_a_string_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`read_only: "false"`는 참인 문자열이다 — 읽기 전용이어야 할 bind가 조용히
    쓰기 가능으로 등재되는 경로라 거절한다."""

    config = _minimal_valid_config()
    config["compose_binds"] = {
        "geo-db": [
            {"container_path": "/data", "read_only": "false", "source": "./x"},
        ]
    }
    with pytest.raises(registry_module.TargetsConfigError) as excinfo:
        registry_module._validate_compose_binds(config, label="t")
    assert "read_only" in str(excinfo.value)


def test_compose_binds_rejects_unknown_fields() -> None:
    """오타 난 키를 조용히 무시하면 그 항목이 의도와 다른 bind가 된다."""

    config = _minimal_valid_config()
    config["compose_binds"] = {
        "geo-db": [
            {
                "container_path": "/data",
                "read_only": True,
                "source": "./x",
                "readonly": True,
            },
        ]
    }
    with pytest.raises(registry_module.TargetsConfigError) as excinfo:
        registry_module._validate_compose_binds(config, label="t")
    assert "readonly" in str(excinfo.value)


def test_compose_binds_rejects_a_relative_container_path() -> None:
    config = _minimal_valid_config()
    config["compose_binds"] = {
        "geo-db": [{"container_path": "data", "read_only": True, "source": "./x"}]
    }
    with pytest.raises(registry_module.TargetsConfigError) as excinfo:
        registry_module._validate_compose_binds(config, label="t")
    assert "absolute" in str(excinfo.value)


def test_compose_binds_rejects_duplicate_keys() -> None:
    """같은 키가 둘이면 뒤엣것이 조용히 이긴다 — 둘 중 어느 source가 쓰이는지
    읽는 사람이 알 수 없다."""

    config = _minimal_valid_config()
    config["compose_binds"] = {
        "geo-db": [
            {"container_path": "/data", "read_only": True, "source": "./a"},
            {"container_path": "/data", "read_only": True, "source": "./b"},
        ]
    }
    with pytest.raises(registry_module.TargetsConfigError) as excinfo:
        registry_module._validate_compose_binds(config, label="t")
    assert "duplicate" in str(excinfo.value)


def test_targets_validate_covers_the_bind_section() -> None:
    """`ktdctl targets validate`가 bind 절도 본다.

    이 검사가 없으면 형태 오류가 **배포 도중에** 처음 드러난다 — validate 명령의
    존재 이유가 그 앞에서 잡는 것이다.
    """

    config = _minimal_valid_config()
    config["compose_binds"] = {"geo-db": "not-a-list"}
    with pytest.raises(registry_module.TargetsConfigError):
        registry_module._validate_targets_config(config, label="t")
