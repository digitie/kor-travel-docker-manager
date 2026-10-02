"""공용 Dagster instance(`dagster_shared`, platform-topology.md §7 2단계)의 선언을 결박한다.

한 사실이 여러 자리에 적힌다 — metadata URL(compose 앵커), 그 URL을 읽는 instance 설정의 env
이름, DB·role을 만드는 db-init의 literal, 백업 role의 database, location별 run 상한과
code-server의 모듈. 사본을 없앨 수 없는 자리(YAML은 문자열 보간이 없다, 백업 표는 코드다)는
여기서 **서로를** 대조한다. 기대값은 정본 파일에서 읽는다 — 배포값 리터럴을 두지 않는다.

실제로 도는지(빈 DB에 schema가 생기고, 재실행이 멱등이고, Dagster가 이 설정을 받아들이는지)는
격리 실행 테스트 `test_dagster_shared_storage_integration.py`가 본다.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
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
#: location별 상한의 key. `.dagster/repository`는 run 본문 tags에 없어 queue daemon이 세지 못한다
#: (instance 설정 머리 주석, 적대 리뷰 H1). 효과는 격리 실행 테스트가 실제 dequeue로 본다.
_LOCATION_TAG = "dagster/code_location"
_UNCOUNTED_TAG = ".dagster/repository"

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
    """`dagster/code_location` 상한은 compose의 code-server(`dagster code-server start`·`api grpc`
    `-m <모듈>`)마다 하나다.

    location 이름은 code-server의 `-m` 모듈이다(오늘 네 instance의 실측 location 이름이고, 3단계
    공용 `workspace.yaml`의 `location_name`이다). 새 code-server가 compose에 들어오면(stage T의
    transport) 여기서 그 상한을 요구한다.
    """

    modules: set[str] = set()
    for service in _compose()["services"].values():
        command = [str(part) for part in service.get("command") or []]
        if any(
            command[index : index + 2] in (["api", "grpc"], ["code-server", "start"])
            for index in range(len(command))
        ) and "-m" in command:
            modules.add(command[command.index("-m") + 1])
    assert modules, "compose에서 code-server를 하나도 못 찾았다 — 추출이 낡았다"
    # 형제 프로젝트(transport)의 code-server는 그 저장소의 compose에 있다 — 선언한 location이 상한을 받는다.
    targets = load_yaml_rejecting_duplicate_keys(
        (_REPO_ROOT / "config" / "docker-targets.yml").read_text(encoding="utf-8")
    )
    externals = {
        str(spec["dagster"]["external"]["location_name"])
        for spec in targets["targets"].values()
        if spec.get("external_project") and "external" in (spec.get("dagster") or {})
    }
    assert externals, "형제 프로젝트의 Dagster 선언을 하나도 못 찾았다 — 추출이 낡았다"
    modules |= externals

    runs = _instance_config()["concurrency"]["runs"]
    limits = runs["tag_concurrency_limits"]
    caps = [entry for entry in limits if entry["key"] == _LOCATION_TAG]
    assert sorted(entry["value"] for entry in caps) == sorted(modules)
    # run 본문에 없는 tag의 상한은 아무것도 막지 않는다 — 있으면 막는다고 믿게 만든다.
    assert not [entry for entry in limits if entry["key"] == _UNCOUNTED_TAG]
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
    # 속성은 CREATE와 ALTER **둘 다**에 전부 — 재실행이 드리프트를 되돌린다.
    attributes = (
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 45"
    )
    for verb in ("CREATE", "ALTER"):
        statement = f"{verb} ROLE {role} WITH LOGIN PASSWORD :'role_password' {attributes}\""
        assert statement in script, verb
    # role 소속 0을 확인하고, 아니면 멈춘다.
    assert "FROM pg_auth_members WHERE member =" in script


def _run_db_init_with_stubs(tmp_path: Path, *, ready_after: int | None) -> tuple[int, str, str]:
    """db-init 스크립트를 stub `pg_isready`·`sleep`·`psql`·`cat`으로 돌린다.

    `ready_after`번째 `pg_isready`부터 성공한다(None이면 끝내 실패). 반환은 (종료 코드, stderr,
    호출 기록)이다.
    """

    script = _compose()["services"][_DB_INIT]["command"][-1].replace("$$", "$")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    counter = tmp_path / "ready-count"
    counter.write_text("0", encoding="utf-8")
    threshold = ready_after if ready_after is not None else 10**9
    stubs = {
        "pg_isready": (
            f'n=$(( $(cat "{counter}") + 1 )); echo "$n" > "{counter}"; '
            f'echo pg_isready >> "{calls}"; [ "$n" -ge {threshold} ]'
        ),
        "sleep": f'echo sleep >> "{calls}"',
        # 준비 대기를 지나면 첫 명령이 secret을 읽는다 — 거기서 멈춰 대기 뒤로 넘어갔음을 기록한다.
        "cat": f'case "$1" in /run/secrets/*) echo "cat $1" >> "{calls}"; exit 7;; esac; exec /bin/cat "$@"',
        "psql": f'echo psql >> "{calls}"; exit 1',
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        stub.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "PGHOST": "127.0.0.1",
        "PGPORT": "11000",
    }
    completed = subprocess.run(
        ["sh", "-ec", script],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
    return completed.returncode, completed.stderr, recorded


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh가 필요하다")
def test_db_init_waits_for_the_instance_and_gives_up_closed(tmp_path: Path) -> None:
    """`run --no-deps`는 service_healthy를 보지 않는다 — cold start에서 db-init이 스스로 기다린다.

    끝내 안 뜨면 유한 횟수 뒤 명확한 메시지로 실패하고 psql을 한 번도 치지 않는다.
    """

    code, stderr, calls = _run_db_init_with_stubs(tmp_path, ready_after=None)
    assert code != 0
    assert "not accepting connections" in stderr
    lines = calls.split()
    assert "psql" not in lines and not any(line.startswith("cat") for line in calls.splitlines())
    # 유한하고, 약 60~120초 대기(2초 간격)다.
    probes = lines.count("pg_isready")
    assert 30 <= probes <= 60, probes


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh가 필요하다")
def test_db_init_proceeds_once_the_instance_accepts(tmp_path: Path) -> None:
    code, _, calls = _run_db_init_with_stubs(tmp_path, ready_after=3)
    recorded = calls.splitlines()
    # 세 번째 probe에서 떠서 대기를 빠져나가고, 다음 명령(secret 읽기)에 닿는다.
    assert recorded[:5] == ["pg_isready", "sleep", "pg_isready", "sleep", "pg_isready"]
    assert recorded[5].startswith("cat /run/secrets/")
    assert code == 7


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


def test_the_dagster_target_gates_ensure_on_both_one_shots() -> None:
    """두 one-shot은 `up -d`가 아니라 init step(`run --rm --no-deps`)으로 **차례대로** 돈다.

    `up -d`는 one-shot의 종료 코드를 보지 않는다 — 거기 두면 실패한 migrate가 성공한 ensure로
    보인다(적대 리뷰 M3). 그리고 `up -d`와 init step 양쪽에 두면 migrate가 두 번 겹쳐 돈다.
    """

    target = yaml.safe_load(_TARGETS.read_text(encoding="utf-8"))["targets"]["dagster"]
    # one-shot은 `up -d`의 인자가 아니다 — 상시 서비스가 `service_completed_successfully`로 기대어
    # `up`이 그 종료 코드를 보게 하고, init step이 다시 한 번 종료 코드로 가른다.
    assert not {_DB_INIT, _MIGRATE} & set(target["services"])
    assert not {_DB_INIT, _MIGRATE} & set(target["runtime_services"])
    assert target["services"][0] == _SHARED_POSTGRES
    assert [step["command"] for step in target["init_steps"]] == [
        ["run", "--rm", "--no-deps", _DB_INIT],
        ["run", "--rm", "--no-deps", _MIGRATE],
    ]


def _host_services() -> dict[str, dict[str, Any]]:
    """호스트 이미지(migrate와 같은 image)를 쓰는 상시 서비스 — 이름이 아니라 image로 찾는다."""

    services = _compose()["services"]
    image = services[_MIGRATE]["image"]
    return {
        name: service
        for name, service in services.items()
        if service.get("image") == image and name != _MIGRATE
    }


def test_the_host_services_run_the_host_image_with_only_the_control_env() -> None:
    """daemon·webserver는 migrate와 같은 호스트 이미지이고, 공용 URL과 DAGSTER_HOME 말고 받는 것이 없다.

    run은 code-server의 자식으로 돈다 — 호스트 서비스에 앱 비밀을 넣을 이유가 없다(plan §1.4).
    """

    services = _compose()["services"]
    migrate = services[_MIGRATE]
    hosts = _host_services()
    assert len(hosts) == 2, sorted(hosts)
    target = yaml.safe_load(_TARGETS.read_text(encoding="utf-8"))["targets"]["dagster"]
    for name, service in hosts.items():
        assert service["build"] == migrate["build"], name
        assert name in target["runtime_services"], name
        assert "secrets" not in service and "env_file" not in service, name
        # 내용 digest 둘은 비밀이 아니다 — 붙인 파일이 바뀌면 재생성되게 하는 값이다(ADR-54, H1).
        allowed = {
            _url_env_name(), "DAGSTER_HOME", "DAGSTER_DAEMON_HEARTBEAT_TOLERANCE",
            "KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST", "KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST",
        }
        assert set(service["environment"]) <= allowed, (name, sorted(service["environment"]))
        assert service["environment"]["DAGSTER_HOME"] == migrate["environment"]["DAGSTER_HOME"]
        assert service["depends_on"] == {_MIGRATE: {"condition": "service_completed_successfully"}}
        assert service.get("init") is True and service.get("restart") == "unless-stopped"
        assert "user" not in service, "호스트 이미지의 비-root 사용자(USER dagster)를 덮지 않는다"
        assert "ports" not in service, "daemon은 포트가 없고 webserver는 loopback 뒤에 있다"
        # 둘 다 storage 가드를 지나 argv로 넘어간다(`sh -ec <가드> sh <argv>`).
        command = [str(part) for part in service["command"]]
        assert command[:2] == ["sh", "-ec"] and 'exec "$$@"' in command[2], name
        assert "DagsterInstance" in command[2] and command[3] == "sh", name


def test_the_shared_webserver_listens_only_on_loopback() -> None:
    """D2: webserver는 `127.0.0.1`에서만 듣는다 — 인증은 앞의 gateway가 한다."""

    for name, service in _host_services().items():
        command = [str(part) for part in service["command"]]
        if "dagster-webserver" not in command:
            continue
        assert command[command.index("-h") + 1] == "127.0.0.1", name
        return
    pytest.fail("공용 webserver를 못 찾았다")


def _dockerfile_copy_sources(dockerfile: str) -> set[str]:
    """build context에서 이미지로 들어가는 source(COPY·ADD, 대소문자 무관, `\\` 줄 이음 포함).

    못 읽는 형식(JSON 배열, heredoc, `--from` 다단계, escape 지시자)을 만나면 조용히 건너뛰지
    않고 멈춘다 — 건너뛰면 해시에서 빠진 입력이 tag를 그대로 둔다.
    """

    assert not re.search(r"^#\s*escape\s*=", dockerfile, re.MULTILINE | re.IGNORECASE), (
        "escape 지시자는 줄 이음 문자를 바꾼다 — 이 추출이 읽지 못한다"
    )
    logical = re.sub(r"\\[ \t]*\r?\n", " ", dockerfile)
    sources: set[str] = set()
    for line in logical.splitlines():
        tokens = line.split()
        if not tokens or tokens[0].upper() not in {"COPY", "ADD"}:
            continue
        unreadable = [
            token
            for token in tokens[1:]
            if token.startswith(("[", "<<")) or token.lower().startswith("--from")
        ]
        assert not unreadable, f"이 추출이 읽지 못하는 {tokens[0]} 형식이다: {line!r}"
        operands = [token for token in tokens[1:] if not token.startswith("--")]
        assert len(operands) >= 2, line
        sources.update(operands[:-1])
    return sources


def test_the_copy_source_extraction_reads_add_lowercase_and_continuations() -> None:
    dockerfile = (
        "FROM x@sha256:" + "0" * 64 + "\n"
        "copy a.txt /a\n"
        "ADD --chown=1:1 b.txt \\\n"
        "    c.txt /opt/\n"
        "RUN echo COPY not-a-source\n"
    )
    assert _dockerfile_copy_sources(dockerfile) == {"a.txt", "b.txt", "c.txt"}
    for unreadable in (
        'COPY ["a b.txt", "/a"]\n',
        "COPY <<EOF /a\nx\nEOF\n",
        "COPY --from=build /out /out\n",
        "# escape=`\nCOPY a /a\n",
    ):
        with pytest.raises(AssertionError):
            _dockerfile_copy_sources(unreadable)


def test_the_host_image_tag_is_the_content_hash_of_what_goes_into_it() -> None:
    """tag는 이미지에 들어가는 파일의 내용 해시다 — 움직이는 이름은 옛 이미지를 새 잠금본으로 속인다.

    해시에 드는 파일은 Dockerfile과 그것이 COPY하는 파일이다. COPY가 늘면 이 집합도 늘어야 한다 —
    그렇지 않으면 새 파일을 고쳐도 tag가 그대로다. 재현은 compose 주석의 coreutils 한 줄과 같다.
    """

    dockerfile = (_HOST_IMAGE_DIR / "Dockerfile").read_text(encoding="utf-8")
    hashed = sorted({"Dockerfile", *_dockerfile_copy_sources(dockerfile)})
    assert hashed == ["Dockerfile", "requirements.txt", "storage-migrate.py"]
    listing = "".join(
        f"{hashlib.sha256((_HOST_IMAGE_DIR / name).read_bytes()).hexdigest()}  {name}\n"
        for name in hashed
    )
    expected = f"kor-travel-dagster-host:{hashlib.sha256(listing.encode()).hexdigest()[:16]}"
    image = _compose()["services"][_MIGRATE]["image"]
    assert image == expected, f"호스트 이미지 내용이 바뀌었다 — compose의 tag를 {expected}로"


def test_contract_errors_name_the_dagster_one_shots() -> None:
    """후보 계약 오류가 두 one-shot을 sha8로 가리면 운영자가 어느 서비스가 거부됐는지 모른다."""

    from kor_travel_docker_manager.services.c6c_deployment import (
        _describe_candidate_service_key,
    )

    for name in (_DB_INIT, _MIGRATE):
        assert _describe_candidate_service_key(name) == name
