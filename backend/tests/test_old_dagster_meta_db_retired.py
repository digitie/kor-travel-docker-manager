"""옛 프로젝트별 Dagster metadata DB에 대한 의존이 되살아나지 못하게 한다(platform-topology.md §7 4단계).

다섯 테넌트(weather·pinvi·geo·map·transport)는 공용 plane(`dagster_shared`)에서 돈다. 4단계는 옛
metadata DB(`kor_travel_map_dagster`·`pinvi_dagster`·`kor_travel_geo_dagster`·`kor_travel_weather_dagster`·
`kor_travel_transport_dagster`)를 `ALLOW_CONNECTIONS false`로 막고 30일 뒤 DROP한다.

2026-10-03 22:51Z 사고: 막은 뒤의 같은 pair 재구축이 Map·PinVi를 멈춘 다음
`kor-travel-map-dagster-storage-migrate`(옛 Map metadata DB를 migrate한다)에서
`dagster_storage_database_unavailable`로 죽었다 — 운영 Map·PinVi가 몇 분 내려갔다.

그래서 **효과**에 결박한다. 이름 목록이 아니라 실제로 도는 자리를 본다.

- 기본 profile의 모든 서비스(`ensure`·`up`이 띄운다)와 pinned 재구축이 `run`하는 one-shot, `ensure`의
  init step 서비스가 옛 metadata DB 이름을 싣거나(env·command), 옛 metadata URL env를 받거나, 그것을
  `depends_on`하지 않는다.
- compose 어디에서도(꺼진 profile 포함 — compose는 꺼진 profile의 서비스도 보간한다, 2026-10-04 n150
  실측) 옛 metadata env를 `:?`로 **요구**하지 않는다. 예외 하나는 Map의 role bootstrap이다(아래).
- 재구축이 다루는 DB role과 백업 role에 `dagster_shared` 밖의 `*_dagster`가 없다.

**남은 예외 — Map 저장소의 결합.** `kor-travel-map-db-role-bootstrap`(fresh bootstrap·`--restart`에서만
돈다)은 Map의 `docker/postgres-role-bootstrap.sh`가 `validate_map_database_credentials`로 metadata
env 네 개를 **문자열로** 요구하므로 그 넷을 받는다. 그 스크립트는 그 DSN으로 접속하지 않는다(모양과
비밀번호 서로 다름만 본다). Map이 그 요구를 지우면 이 예외도 지운다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services import compose_service, database_runtime, standalone_backup
from kor_travel_docker_manager.services.deploy_status import _DATABASE_ROLES, _SCHEMA_ROLES
from kor_travel_docker_manager.services.pinned_runtime_generation import PinnedRuntimeGeneration
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_TARGETS = _REPO_ROOT / "config" / "docker-targets.yml"

_SHARED_DATABASE = "dagster_shared"
#: `<이름>_dagster` 꼴의 DB 이름. 파이썬 모듈 경로(`kortravelgeo_dagster.definitions`)는 DB가 아니다 —
#: 바로 뒤에 `.`이 오는 것은 뺀다.
_DAGSTER_DATABASE = re.compile(r"(?<![\w.])([a-z][a-z0-9_]*_dagster)(?![\w.])")
#: 옛 metadata DB의 URL·이름·login env. 공용 plane의 `KOR_TRAVEL_DAGSTER_SHARED_*`는 아니다.
_OLD_METADATA_ENV = re.compile(
    r"^(?!KOR_TRAVEL_DAGSTER_SHARED_)[A-Z0-9_]*DAGSTER_"
    r"(?:PG_URL|POSTGRES_URL|DB|POSTGRES_DB|SHARED_DB|METADATA_[A-Z_]+)$"
)
#: 값 안의 보간 참조(`${KOR_TRAVEL_GEO_DAGSTER_PG_URL:?…}` 등).
_OLD_METADATA_REFERENCE = re.compile(
    r"\$\{(?!KOR_TRAVEL_DAGSTER_SHARED_)([A-Z0-9_]*DAGSTER_"
    r"(?:PG_URL|POSTGRES_URL|DB|POSTGRES_DB|SHARED_DB|METADATA_[A-Z_]+))(:?[?\-}])"
)
#: Map 저장소의 결합(모듈 docstring). 이 서비스의 이 env만 허용한다.
_MAP_BOOTSTRAP = "kor-travel-map-db-role-bootstrap"
_MAP_BOOTSTRAP_METADATA_ENV = frozenset(
    {
        "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB",
        "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER",
        "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD",
        "KOR_TRAVEL_MAP_DAGSTER_PG_URL",
    }
)


def _compose_services() -> Mapping[str, Mapping[str, Any]]:
    document = load_yaml_rejecting_duplicate_keys(_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    services = document["services"]
    assert isinstance(services, dict)
    return services


def _init_step_services(services: Mapping[str, Any]) -> set[str]:
    """`ensure`의 init step이 compose 명령으로 부르는 서비스(명령 인자 중 compose 서비스 이름)."""

    targets = load_yaml_rejecting_duplicate_keys(_TARGETS.read_text(encoding="utf-8"))
    names: set[str] = set()
    for target in targets["targets"].values():
        for step in target.get("init_steps") or ():
            names.update(str(word) for word in step.get("command") or () if word in services)
    assert names, "init step 서비스를 하나도 찾지 못했다 — 추출이 낡았다"
    return names


def _exercised_services() -> dict[str, Mapping[str, Any]]:
    """기본 profile 서비스 + pinned 재구축이 `run`하는 one-shot + `ensure` init step 서비스."""

    services = _compose_services()
    names = {name for name, service in services.items() if not service.get("profiles")}
    names |= set(compose_service._PINNED_RUNTIME_ONESHOT_WRITERS)
    names |= _init_step_services(services)
    # 재구축·init step이 부르는 이름이 compose에 없으면 그 자체가 결함이다.
    assert names <= set(services), sorted(names - set(services))
    return {name: services[name] for name in sorted(names)}


def _environment(service: Mapping[str, Any]) -> dict[str, str]:
    environment = service.get("environment") or {}
    if isinstance(environment, list):
        return dict(str(item).partition("=")[::2] for item in environment)
    return {str(key): "" if value is None else str(value) for key, value in environment.items()}


def _words(value: object) -> Iterator[str]:
    if isinstance(value, list):
        yield from (str(item) for item in value)
    elif value is not None:
        yield str(value)


def _old_databases(text: str) -> set[str]:
    return {name for name in _DAGSTER_DATABASE.findall(text) if name != _SHARED_DATABASE}


def test_exercised_services_never_name_an_old_metadata_database() -> None:
    offenders: dict[str, set[str]] = {}
    for name, service in _exercised_services().items():
        texts = [
            *(f"{key}={value}" for key, value in _environment(service).items()),
            *_words(service.get("command")),
            *_words(service.get("entrypoint")),
        ]
        found = set().union(*(_old_databases(text) for text in texts))
        if found:
            offenders[name] = found
    assert offenders == {}


def test_exercised_services_never_receive_old_metadata_env() -> None:
    offenders: dict[str, list[str]] = {}
    for name, service in _exercised_services().items():
        allowed = _MAP_BOOTSTRAP_METADATA_ENV if name == _MAP_BOOTSTRAP else frozenset()
        environment = _environment(service)
        bad = sorted(
            key
            for key, value in environment.items()
            if key not in allowed
            and (
                _OLD_METADATA_ENV.match(key)
                or any(ref not in allowed for ref, _ in _OLD_METADATA_REFERENCE.findall(value))
            )
        )
        if bad:
            offenders[name] = bad
    assert offenders == {}


def test_exercised_services_never_wait_for_an_old_metadata_migration() -> None:
    services = _compose_services()
    offenders: dict[str, list[str]] = {}
    for name, service in _exercised_services().items():
        depends_on = service.get("depends_on") or {}
        bad = sorted(
            dependency
            for dependency in depends_on
            if dependency.endswith("dagster-storage-migrate")
            and dependency != "kor-travel-dagster-storage-migrate"
        )
        if bad:
            offenders[name] = bad
    assert offenders == {}
    # 옛 Map metadata migrate one-shot은 compose에 없다(어느 profile로도 되살리지 않는다).
    assert sorted(
        name
        for name in services
        if name.endswith("dagster-storage-migrate") and name != "kor-travel-dagster-storage-migrate"
    ) == []


def test_no_service_in_any_profile_requires_old_metadata_env() -> None:
    """꺼진 profile도 보간된다 — `:?` 하나가 `.env`에 옛 env를 남기게 한다."""

    offenders: dict[str, list[str]] = {}
    for name, service in _compose_services().items():
        allowed = _MAP_BOOTSTRAP_METADATA_ENV if name == _MAP_BOOTSTRAP else frozenset()
        required = sorted(
            {
                ref
                for value in _environment(service).values()
                for ref, operator in _OLD_METADATA_REFERENCE.findall(value)
                if operator.endswith("?") and ref not in allowed
            }
        )
        if required:
            offenders[name] = required
    assert offenders == {}


def test_db_init_one_shots_never_create_or_grant_an_old_metadata_database() -> None:
    """db-init이 옛 DB를 만들거나 CONNECT를 주면 DROP 뒤 빈 DB를 되살린다."""

    offenders: dict[str, set[str]] = {}
    for name, service in _compose_services().items():
        if not name.startswith("kor-travel-shared-db-init-"):
            continue
        texts = [
            *(f"{key}={value}" for key, value in _environment(service).items()),
            *_words(service.get("command")),
        ]
        found = set().union(*(_old_databases(text) for text in texts))
        if found:
            offenders[name] = found
    assert offenders == {}


def test_pinned_rebuild_database_roles_have_no_old_metadata_database() -> None:
    assert not [role for role in database_runtime._ROLE_CONFIG if "dagster" in role]
    assert not [
        default
        for _env, default, _owner in database_runtime._ROLE_CONFIG.values()
        if _old_databases(default)
    ]
    assert not [role for role in _DATABASE_ROLES if "dagster" in role]
    assert not [role for role in _SCHEMA_ROLES if "dagster" in role]
    fields = PinnedRuntimeGeneration.__dataclass_fields__
    assert "map_dagster_head" not in fields


def test_backup_roles_only_dump_the_shared_metadata_database() -> None:
    dagster_roles = sorted(role for role in standalone_backup.BACKUP_ROLES if "dagster" in role)
    assert dagster_roles == [_SHARED_DATABASE]
    databases = {config[2] for config in standalone_backup._ROLE_CONFIG.values()}
    assert {name for name in databases if _old_databases(name)} == set()
