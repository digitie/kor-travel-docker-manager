"""공용 PostgreSQL 서비스의 실행 형태를 **고정된 그 이미지**에서 실제로 돌린다(ADR-52·ADR-53).

단위 테스트(`test_shared_postgres_runtime_contract.py`)는 compose 문서의 모양만 본다. 문서를
읽어서는 알 수 없는 것이 있다:

- digest로 고정한 이미지에서 D4 튜닝 `command:`가 정말 뜨는가 — preload한 `pg_prewarm`이
  있고, 값마다 서버가 받아들여 `command line`에서 온 값으로 보이는가. 하나라도 틀리면 창의
  재기동이 공용 instance를 띄우지 못하고 모든 테넌트가 멈춘다.
- autoprewarm leader가 도는가(창의 A7 게이트가 보는 프로세스 제목).
- probe의 절대경로가 이미지에 있는가. 없으면 공용 instance는 영원히 unhealthy가 되고
  `service_healthy`에 걸린 모든 테넌트의 `up`이 멈춘다.
- docker-init이 stop signal을 postmaster에 넘기는가. 아니면 모든 정지가 grace 뒤 SIGKILL =
  crash recovery다.
- compose가 `stop_grace_period`·`shm_size`를 `Config.StopTimeout`·`HostConfig.ShmSize`로
  넘기는가 — Manager의 컨테이너 stop/restart가 읽는 바로 그 값이다.

그래서 정본 서비스에서 실행 형태(image·init·command·healthcheck·stop_grace_period·shm_size)를
그대로 가져오고, 호스트에 닿는 것(PGDATA bind·secret·host network·포트)만 뺀 격리 프로젝트로
띄운다. 기대값도 전부 정본 compose에서 읽는다 — 이 파일에 배포값 리터럴은 없다. gate
(`KTDM_REQUIRE_DOCKER_INTEGRATION`)는 `test_compose_readiness_integration.py`와 같다.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_shared_postgres_runtime_contract import _bytes, _seconds, _service

_REQUIRED_GATE_ENV = "KTDM_REQUIRE_DOCKER_INTEGRATION"
#: 정본에서 그대로 가져오는 실행 형태. 빠진 키는 옮기지 않는다(변경 전 compose에서도 돈다).
_CARRIED_KEYS = ("image", "init", "command", "healthcheck", "stop_grace_period", "shm_size")
#: 격리 netns 안의 포트. command와 probe가 같은 변수에서 읽으므로 둘이 함께 바뀐다.
_ISOLATED_PORT = "15436"
#: 정지를 뺀 docker 명령 하나의 상한(n150 부하에서 `docker create` 한 번이 27.8초였다).
_TIMEOUT = 180
#: 정지 명령은 컨테이너 자신의 grace만큼 기다릴 수 있다 — 그 위에 두는 여유.
_STOP_TIMEOUT_MARGIN = 30


def _required_docker_gate() -> bool:
    value = os.environ.get(_REQUIRED_GATE_ENV, "0").strip()
    if value not in {"0", "1"}:
        pytest.fail(f"{_REQUIRED_GATE_ENV}는 0 또는 1이어야 함")
    return value == "1"


def _run(
    *arguments: str, env: dict[str, str] | None = None, timeout: float = _TIMEOUT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=env,
    )


def _fixture_service() -> dict[str, Any]:
    source = _service()
    environment = source["environment"]
    service = {key: source[key] for key in _CARRIED_KEYS if key in source}
    service.update(
        {
            "pull_policy": "never",
            # host network·PGDATA bind·secret·host 포트를 옮기지 않는다 — 운영 instance와
            # 아무것도 공유하지 않는다. 격리 netns에도 loopback은 있다.
            "network_mode": "none",
            "environment": {
                "POSTGRES_USER": environment["POSTGRES_USER"],
                "POSTGRES_DB": environment["POSTGRES_DB"],
                "POSTGRES_INITDB_ARGS": environment["POSTGRES_INITDB_ARGS"],
                "POSTGRES_PASSWORD": secrets.token_hex(16),
            },
        }
    )
    return service


#: (조회 명령, 제거 명령) — 컨테이너·네트워크·볼륨은 지우는 명령이 다르다.
_RESIDUE_KINDS = (
    (("ps", "--all"), ("rm", "--force")),
    (("network", "ls"), ("network", "rm")),
    (("volume", "ls"), ("volume", "rm", "--force")),
)


def _remove_project_residue(project: str) -> int:
    """compose 라벨로 남은 것을 지우고, 지우기 **전에** 본 개수를 돌려준다."""

    label = f"label=com.docker.compose.project={project}"
    seen = 0
    for listing, removal in _RESIDUE_KINDS:
        listed = _run("docker", *listing, "--quiet", "--filter", label)
        if listed.returncode != 0:
            pytest.fail("fixture residue를 조회할 수 없음")
        ids = listed.stdout.split()
        seen += len(ids)
        if ids:
            _run("docker", *removal, *ids)
    return seen


@pytest.fixture
def isolated_shared_postgres(tmp_path: Path) -> Iterator[str]:
    service = _fixture_service()
    image = service["image"]
    docker_ok = _run("docker", "compose", "version").returncode == 0
    image_ok = docker_ok and _run("docker", "image", "inspect", image).returncode == 0
    if not image_ok:
        reason = f"Docker Compose 또는 로컬 이미지 {image}를 쓸 수 없음(pull하지 않는다)"
        if _required_docker_gate():
            pytest.fail(reason)
        pytest.skip(f"{reason}; 필수 gate는 {_REQUIRED_GATE_ENV}=1로 실행")

    compose_path = tmp_path / "compose.yml"
    compose_path.write_text(
        yaml.safe_dump({"services": {"pg": service}}, sort_keys=False), encoding="utf-8"
    )
    project = f"ktdm-sharedpg-{os.getpid()}-{tmp_path.name[-8:]}".lower()
    env = {**os.environ, "KOR_TRAVEL_SHARED_DB_PORT": _ISOLATED_PORT}
    compose = ("docker", "compose", "--file", str(compose_path), "--project-name", project)
    stop_timeout = _seconds(service["stop_grace_period"]) + _STOP_TIMEOUT_MARGIN
    try:
        up = _run(*compose, "up", "--detach", "--pull", "never", env=env)
        assert up.returncode == 0, up.stderr
        ps = _run(*compose, "ps", "--all", "--quiet", "pg", env=env)
        assert ps.returncode == 0 and ps.stdout.strip(), ps.stderr
        yield ps.stdout.strip()
    finally:
        down = _run(
            *compose,
            "down",
            "--volumes",
            "--remove-orphans",
            "--timeout",
            "30",
            env=env,
            timeout=stop_timeout,
        )
        residue = _remove_project_residue(project)
        if down.returncode != 0 or residue or _remove_project_residue(project):
            pytest.fail(f"fixture cleanup 실패: down={down.returncode}, 잔여={residue}")


def _inspect(container: str) -> dict[str, Any]:
    result = _run("docker", "inspect", container)
    assert result.returncode == 0, result.stderr
    records = json.loads(result.stdout)
    assert isinstance(records, list) and len(records) == 1
    return records[0]


def _wait_healthy(container: str) -> dict[str, Any]:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        state = _inspect(container)["State"]
        status = (state.get("Health") or {}).get("Status")
        if status == "healthy":
            return state
        if status == "unhealthy" or state.get("Status") in {"exited", "dead"}:
            break
        time.sleep(2)
    logs = _run("docker", "logs", "--tail", "40", container)
    pytest.fail(
        f"공용 서비스 형태가 healthy가 되지 않음: {_inspect(container)['State']!r}\n"
        f"{logs.stdout[-2000:]}{logs.stderr[-2000:]}"
    )


def _container_env(details: dict[str, Any], name: str) -> str:
    values = [
        entry.partition("=")[2]
        for entry in details["Config"]["Env"]
        if entry.partition("=")[0] == name
    ]
    assert len(values) == 1, name
    return values[0]


def _command_settings(command: list[str]) -> dict[str, str]:
    """정본 `command:`의 `-c name=value` 전부 — 기대값의 유일한 원천이다."""

    settings: dict[str, str] = {}
    for flag, value in zip(command, command[1:], strict=False):
        if flag == "-c":
            name, _, setting = value.partition("=")
            settings[name] = setting
    return settings


def _psql(container: str, details: dict[str, Any], sql: str) -> str:
    result = _run(
        "docker",
        "exec",
        "--user",
        "postgres",
        container,
        "psql",
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
        "--field-separator",
        "|",
        "--port",
        _ISOLATED_PORT,
        "--username",
        _container_env(details, "POSTGRES_USER"),
        "--dbname",
        _container_env(details, "POSTGRES_DB"),
        "--command",
        sql,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _autoprewarm_leaders(container: str) -> int:
    # leader는 database에 붙지 않아 `pg_stat_activity`에 없다 — 프로세스 제목만이 즉시 보이는
    # 검출기다(창의 A7과 같은 검사).
    titles = _run(
        "docker",
        "exec",
        container,
        "sh",
        "-c",
        'for f in /proc/[0-9]*/cmdline; do tr "\\0" " " < "$f"; echo; done',
    )
    assert titles.returncode == 0, titles.stderr
    return sum("autoprewarm leader" in line for line in titles.stdout.splitlines())


def test_the_shared_postgres_runs_its_canonical_shape_in_the_pinned_image(
    isolated_shared_postgres: str,
) -> None:
    container = isolated_shared_postgres
    source = _service()

    state = _wait_healthy(container)
    # probe가 실제로 성공했다 — 절대경로가 이 이미지에 있고 인자가 맞다.
    last = state["Health"]["Log"][-1]
    assert last["ExitCode"] == 0 and "accepting connections" in last["Output"], last

    details = _inspect(container)
    probe = details["Config"]["Healthcheck"]["Test"]
    assert probe[0] == "CMD" and probe[1] == source["healthcheck"]["test"][1], probe
    assert details["HostConfig"]["Init"] is True
    # compose가 `stop_grace_period`를 `Config.StopTimeout`(초, 정수)으로, `shm_size`를
    # `HostConfig.ShmSize`(바이트)로 넘긴다. 기대값은 정본 compose를 같은 파서로 읽은 것이다.
    grace = _seconds(source["stop_grace_period"])
    assert details["Config"]["StopTimeout"] == grace
    assert details["HostConfig"]["ShmSize"] == _bytes(source["shm_size"])

    # D4 튜닝이 서버에 그대로 들어갔다 — 값마다 `command line`에서 온 것이다.
    expected = _command_settings(source["command"])
    assert expected, "전제: 정본 command에 -c 설정이 있다"
    names = ", ".join(f"'{name}'" for name in sorted(expected))
    rows = _psql(
        container,
        details,
        "SELECT name, current_setting(name), source FROM pg_settings "
        f"WHERE name IN ({names}) ORDER BY name",
    )
    observed = {
        name: (value, origin)
        for name, value, origin in (line.split("|") for line in rows.splitlines() if line)
    }
    assert observed == {name: (value, "command line") for name, value in expected.items()}

    deadline = time.monotonic() + 60
    while _autoprewarm_leaders(container) != 1 and time.monotonic() < deadline:
        time.sleep(2)
    assert _autoprewarm_leaders(container) == 1

    # PID 1은 docker-init이고, postmaster는 그 자식이다 — 고아는 docker-init이 거둔다.
    comm = _run("docker", "exec", container, "cat", "/proc/1/comm")
    assert comm.returncode == 0 and comm.stdout.strip() == "docker-init", comm
    postmaster = _run(
        "docker",
        "exec",
        container,
        "sh",
        "-c",
        'pid=$(head -1 "$PGDATA/postmaster.pid"); echo "$pid $(cut -d" " -f4 /proc/$pid/stat)"',
    )
    assert postmaster.returncode == 0, postmaster.stderr
    pid, parent = postmaster.stdout.split()
    assert pid != "1" and parent == "1", postmaster.stdout

    # docker-init이 stop signal을 postmaster에 넘긴다 — grace 안의 깨끗한 종료다. signal이
    # 닿지 않으면 docker가 grace를 다 기다린 뒤 SIGKILL한다(exit 137, 종료 로그 없음).
    started = time.monotonic()
    stopped = _run("docker", "stop", container, timeout=grace + _STOP_TIMEOUT_MARGIN)
    elapsed = time.monotonic() - started
    assert stopped.returncode == 0, stopped.stderr
    assert _inspect(container)["State"]["ExitCode"] == 0
    logs = _run("docker", "logs", container)
    assert "database system is shut down" in logs.stdout + logs.stderr
    assert elapsed < grace, f"정지가 {elapsed:.0f}초 걸렸다 — signal이 postmaster에 닿지 않았다"
