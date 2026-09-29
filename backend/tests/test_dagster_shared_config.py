"""공용 Dagster instance(`dagster_shared`, platform-topology.md §7 2단계)의 선언을 결박한다.

한 사실이 여러 자리에 적힌다 — metadata URL(compose 앵커), 그 URL을 읽는 instance 설정의 env
이름, DB·role을 만드는 db-init의 literal, 백업 role의 database, location별 run 상한과
code-server의 모듈. 사본을 없앨 수 없는 자리(YAML은 문자열 보간이 없다, 백업 표는 코드다)는
여기서 **서로를** 대조한다. 기대값은 정본 파일에서 읽는다 — 배포값 리터럴을 두지 않는다.

실제로 도는지(빈 DB에 schema가 생기고, 재실행이 멱등이고, Dagster가 이 설정을 받아들이는지)는
격리 실행 테스트 `test_dagster_shared_storage_integration.py`가 본다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from kor_travel_docker_manager.services import standalone_backup
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_TARGETS = _REPO_ROOT / "config" / "docker-targets.yml"
_INSTANCE_CONFIG = _REPO_ROOT / "config" / "dagster-shared" / "dagster.yaml"
_HOST_IMAGE_DIR = _REPO_ROOT / "docker" / "dagster-host"

_ANCHOR = "x-dagster-shared-control-env"
_DB_INIT = "kor-travel-shared-db-init-dagster"
_MIGRATE = "kor-travel-dagster-storage-migrate"
_SHARED_POSTGRES = "kor-travel-shared-postgres"
_ADMIN_SECRET = "kor-travel-shared-postgres-password"
_BACKUP_ROLE = "dagster_shared"
_REPOSITORY_TAG = ".dagster/repository"

_URL = re.compile(
    r"^postgresql\+psycopg2://(?P<user>[a-z_][a-z0-9_]*)"
    r":\$\{(?P<password_env>[A-Z0-9_]+):\?[^}]*\}"
    r"@(?P<host>[0-9.]+):(?P<port>\$\{[^}]+\})/(?P<database>[a-z_][a-z0-9_]*)$"
)


def _compose() -> dict[str, Any]:
    document = load_yaml_rejecting_duplicate_keys(_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _instance_config() -> dict[str, Any]:
    document = load_yaml_rejecting_duplicate_keys(_INSTANCE_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _url_env_name() -> str:
    storage = _instance_config()["storage"]
    assert set(storage) == {"postgres"}, storage
    postgres_url = storage["postgres"]["postgres_url"]
    assert set(postgres_url) == {"env"}, "URL은 env로만 받는다 — 파일에 자격증명을 두지 않는다"
    return str(postgres_url["env"])


def _url() -> re.Match[str]:
    anchor = _compose()[_ANCHOR]
    name = _url_env_name()
    assert set(anchor) == {name}, (
        f"앵커는 instance 설정이 읽는 env 하나만 정의한다 — 앵커 {sorted(anchor)}, 설정 {name}"
    )
    match = _URL.match(str(anchor[name]))
    assert match, f"metadata URL 모양이 계약과 다르다: {anchor[name]!r}"
    return match


def test_the_instance_reads_its_storage_from_the_anchor_url_with_psycopg2() -> None:
    config = _instance_config()
    # 스키마는 migrate one-shot만 만든다 — runtime이 빠진 table을 몰래 만들면 불완전한
    # migrate가 가려진다.
    assert config["storage"]["postgres"]["should_autocreate_tables"] is False
    # 맨 `postgresql://`이면 SQLAlchemy 2.1 code-server가 psycopg v3를 고른다.
    assert _url().string.startswith("postgresql+psycopg2://")


def test_the_url_names_the_db_init_identity_and_the_backup_role() -> None:
    url = _url()
    services = _compose()["services"]
    db_init = services[_DB_INIT]
    environment = db_init["environment"]

    assert url["user"] == environment["KOR_TRAVEL_DAGSTER_SHARED_APP_USER"]
    assert url["database"] == environment["KOR_TRAVEL_DAGSTER_SHARED_DB"]
    assert url["host"] == environment["PGHOST"]
    assert url["port"] == environment["PGPORT"]

    # URL의 비밀번호 env는 db-init이 role에 거는 app secret의 provider와 같다.
    secret_sources = {entry["source"] for entry in db_init["secrets"]}
    app_secrets = secret_sources - {_ADMIN_SECRET}
    assert len(app_secrets) == 1, secret_sources
    top_level = _compose()["secrets"][app_secrets.pop()]
    assert top_level == {"environment": url["password_env"]}

    # 백업 role이 같은 database를 뜬다.
    _, database = standalone_backup._role_config(_BACKUP_ROLE)
    assert database == url["database"]


def test_every_service_with_the_url_mounts_the_one_instance_config() -> None:
    """URL을 받은 서비스는 같은 instance 설정 파일을 `$DAGSTER_HOME`에 읽기 전용으로 붙인다."""

    name = _url_env_name()
    carriers = {
        service_name: service
        for service_name, service in _compose()["services"].items()
        if name in (service.get("environment") or {})
    }
    assert _MIGRATE in carriers, sorted(carriers)
    source = f"./{_INSTANCE_CONFIG.relative_to(_REPO_ROOT).as_posix()}"
    for service_name, service in carriers.items():
        home = service["environment"]["DAGSTER_HOME"]
        assert f"{source}:{home}/dagster.yaml:ro" in service.get("volumes", []), service_name


def test_location_caps_cover_exactly_the_compose_code_servers() -> None:
    """`.dagster/repository` 상한은 compose의 code-server(`dagster api grpc -m <모듈>`)마다 하나다.

    location 이름은 code-server의 `-m` 모듈이다. 새 code-server가 compose에 들어오면(stage T의
    transport) 여기서 그 상한을 요구한다.
    """

    modules: set[str] = set()
    for service in _compose()["services"].values():
        command = [str(part) for part in service.get("command") or []]
        if any(
            command[index : index + 2] == ["api", "grpc"] for index in range(len(command))
        ) and "-m" in command:
            modules.add(command[command.index("-m") + 1])
    assert modules, "compose에서 code-server를 하나도 못 찾았다 — 추출이 낡았다"

    runs = _instance_config()["concurrency"]["runs"]
    caps = [
        entry for entry in runs["tag_concurrency_limits"] if entry["key"] == _REPOSITORY_TAG
    ]
    assert sorted(entry["value"] for entry in caps) == sorted(
        f"__repository__@{module}" for module in modules
    )
    # D3: 전역 상한은 호스트 보호 상한이다 — 테넌트 상한의 합보다 작아야 의미가 있다.
    maximum = runs["max_concurrent_runs"]
    assert isinstance(maximum, int) and 0 < maximum < sum(entry["limit"] for entry in caps)
    assert all(0 < entry["limit"] <= maximum for entry in caps)


def test_the_coordinator_is_the_queued_default_and_telemetry_is_off() -> None:
    config = _instance_config()
    # 상한은 `concurrency.runs`에 있다 — `pools`와 함께면 `run_coordinator`의 상한은 Dagster가
    # 거부하고, `run_queue`는 `run_coordinator`와 함께 쓸 수 없다. 기본 coordinator가
    # QueuedRunCoordinator다(실제 인스턴스의 확인은 격리 실행 테스트).
    assert "run_coordinator" not in config
    assert config["telemetry"] == {"enabled": False}
    assert config["run_queue"]["max_user_code_failure_retries"] > 0
    for daemon in ("schedules", "sensors"):
        assert config[daemon]["use_threads"] is True
    for section in ("local_artifact_storage", "compute_logs"):
        assert config[section]["config"]["base_dir"].startswith("/opt/dagster/state/")


def test_db_init_follows_the_shared_postgres_pattern() -> None:
    service = _compose()["services"][_DB_INIT]
    assert service["restart"] == "no"
    assert service["depends_on"] == {_SHARED_POSTGRES: {"condition": "service_healthy"}}
    script = service["command"][-1]
    environment = service["environment"]
    database = '\\"$$KOR_TRAVEL_DAGSTER_SHARED_DB\\"'
    role = '\\"$$KOR_TRAVEL_DAGSTER_SHARED_APP_USER\\"'
    assert set(environment) >= {"KOR_TRAVEL_DAGSTER_SHARED_DB", "KOR_TRAVEL_DAGSTER_SHARED_APP_USER"}
    # onboarding §5.3(C2) — PUBLIC CONNECT를 걷고 소유 role에게만 되돌린다.
    assert 'REVOKE CONNECT ON DATABASE \\"$$PGDATABASE\\" FROM PUBLIC' in script
    assert f"REVOKE CONNECT ON DATABASE {database} FROM PUBLIC" in script
    assert f"GRANT CONNECT ON DATABASE {database} TO {role}" in script
    # 이미 있던 DB의 owner가 다르면 멈춘다(소유권은 createdb 때만 걸린다).
    assert "SELECT pg_get_userbyid(datdba) FROM pg_database" in script
    # 비밀번호는 psql 변수로만 — 명령줄 SQL 문자열에 넣지 않는다.
    assert "PASSWORD :'role_password'" in script
    assert "PASSWORD '$$" not in script
    # URL에 들어가는 비밀번호라 빈 값·예약 문자를 거부한다.
    assert "*[!A-Za-z0-9._~-]*" in script
    # C3 — 오류를 삼키지 않는다.
    assert "|| true" not in script and "2>/dev/null" not in script
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in script


def test_migrate_waits_for_the_db_init_and_runs_the_host_image() -> None:
    services = _compose()["services"]
    migrate = services[_MIGRATE]
    assert migrate["restart"] == "no"
    assert migrate["depends_on"] == {
        _SHARED_POSTGRES: {"condition": "service_healthy"},
        _DB_INIT: {"condition": "service_completed_successfully"},
    }
    assert Path(_REPO_ROOT, migrate["build"]["context"]).resolve() == _HOST_IMAGE_DIR.resolve()
    # 호스트 서비스에는 URL과 DAGSTER_HOME 말고 아무것도 넣지 않는다.
    assert set(migrate["environment"]) == {_url_env_name(), "DAGSTER_HOME"}
    assert "secrets" not in migrate


def test_the_host_image_installs_only_the_hash_locked_set() -> None:
    """재빌드가 Dagster 버전을 움직이지 못한다 — 호스트 버전은 code-server의 상한이다."""

    dockerfile = (_HOST_IMAGE_DIR / "Dockerfile").read_text(encoding="utf-8")
    base = re.search(r"^FROM (\S+)", dockerfile, re.MULTILINE)
    assert base and re.search(r"@sha256:[0-9a-f]{64}$", base.group(1)), "베이스는 digest로 고정"
    install = re.search(r"pip install([^&]+)", dockerfile)
    assert install
    for flag in ("--require-hashes", "--no-deps", "-r "):
        assert flag in install.group(1), flag

    locked: dict[str, str] = {}
    for line in (_HOST_IMAGE_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith((" ", "#")):
            continue
        name, separator, rest = line.partition("==")
        assert separator, f"잠금본의 요구가 정확 핀이 아니다: {line!r}"
        locked[name.strip().lower().replace("_", "-")] = rest.split()[0]

    wanted: dict[str, str] = {}
    for line in (_HOST_IMAGE_DIR / "requirements.in").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, separator, version = line.partition("==")
        assert separator, f"최상위 핀은 하한이 아니라 정확 핀이다: {line!r}"
        wanted[name.strip().lower().replace("_", "-")] = version.strip()
    assert {name: locked.get(name) for name in wanted} == wanted, "잠금본이 최상위 핀과 어긋났다"

    # Dagster 본체 가족은 한 버전이다(dagster-postgres는 자기 번호 체계).
    family = {
        name: version
        for name, version in locked.items()
        if name == "dagster" or (name.startswith("dagster-") and name != "dagster-postgres")
    }
    assert len(set(family.values())) == 1, family


def test_the_dagster_target_provisions_without_runtime_services() -> None:
    target = yaml.safe_load(_TARGETS.read_text(encoding="utf-8"))["targets"]["dagster"]
    assert {_SHARED_POSTGRES, _DB_INIT, _MIGRATE} <= set(target["services"])
    # 두 one-shot의 정상 상태는 exited(0)다 — runtime에 두면 status가 늘 실패로 읽힌다.
    assert not {_DB_INIT, _MIGRATE} & set(target["runtime_services"])
