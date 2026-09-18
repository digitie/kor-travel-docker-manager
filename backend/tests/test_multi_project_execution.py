"""멀티프로젝트 target — **실제로 실행되는 명령**의 계약.

이 파일이 따로 있는 이유가 있다. `test_multi_project_targets.py`의 검사들은
`build_command(external=…)`를 **직접** 부른다. 그 층에서는 전부 초록이었는데,
`run → _run_unlocked → build_command` 배관이 **끊겨 있었다**:

    TypeError: ComposeService.run() got an unexpected keyword argument 'external'

`logs`는 `external`이 `None`일 때도 무조건 넘기므로 **모든 이름**에서 죽었다 —
weather·airport뿐 아니라 map·db·all까지. 그런데 1770개 테스트가 전부 초록이었다.
적대 리뷰가 실측한 변이 `run이 external을 받되 버린다`도 **1770 전부 초록**이었다.

그래서 이 검사들은 한 층 아래에 결박한다: `subprocess.run`을 가로채 **실제 argv와
cwd**를 본다. 명령을 만드는 함수가 아니라 **실행되는 명령**이 대상이다.

    argv  — `-p <project>` · `--project-directory` · 파일마다 `-f`
    cwd   — **compose는 `-f`를 cwd 기준으로 푼다.** `--project-directory`가 아니다.
            Manager 루트에서 돌면 `-f docker-compose.yml`은 Manager 자신의 compose다.
    env   — 형제 프로젝트의 `.env`를 Manager의 셸 환경이 덮어쓰지 않는다.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

import pytest

from kor_travel_docker_manager.services import compose_service as compose_module
from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
    get_project_root,
)
from kor_travel_docker_manager.services.errors import DeploymentContractError


class _Capture:
    """`subprocess.run` 호출을 그대로 받아 적는다."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append({"command": list(command), **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    @property
    def only(self) -> dict[str, Any]:
        assert len(self.calls) == 1, f"호출이 하나가 아니다: {len(self.calls)}"
        return self.calls[0]


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> _Capture:
    capture = _Capture()
    monkeypatch.setattr(compose_module.subprocess, "run", capture)
    return capture


def _flag_values(command: list[str], flag: str) -> list[str]:
    return [command[i + 1] for i, item in enumerate(command) if item == flag]


# ── 실행되는 명령이 형제 프로젝트를 가리키는가 ───────────────────────────


def test_status_of_an_external_target_runs_in_that_projects_directory(
    captured: _Capture,
) -> None:
    """**cwd가 요점이다.**

    `-p`·`--project-directory`·`-f` 셋을 다 붙여도 cwd가 Manager 루트면 compose는
    `-f compose.yaml`을 **Manager 저장소 안에서** 찾는다. weather는 그 이름이 없어
    "no configuration file provided"로 죽고, airport은 이름이 있어 **Manager 자신의
    docker-compose.yml**을 남의 프로젝트 이름으로 연다 — 후자가 더 나쁘다.
    """

    ComposeService().status_target("weather")

    call = captured.only
    command = call["command"]
    assert call["cwd"] == "/home/digitie/kor-travel-weather", (
        f"cwd가 형제 프로젝트여야 한다: {call['cwd']!r}"
    )
    assert _flag_values(command, "-p") == ["kor-travel-weather"]
    assert _flag_values(command, "--project-directory") == [
        "/home/digitie/kor-travel-weather"
    ]
    assert _flag_values(command, "-f") == ["compose.yaml", "deploy/compose.n150.yaml"]


def test_container_logs_run_in_the_owning_projects_directory(captured: _Capture) -> None:
    """컨테이너 이름으로 부를 때도 같다 — 소유 프로젝트의 좌표를 쓴다."""

    ComposeService().logs("kor-travel-weather-api", tail=5)

    call = captured.only
    assert call["cwd"] == "/home/digitie/kor-travel-weather"
    assert _flag_values(call["command"], "-p") == ["kor-travel-weather"]
    assert "api" in call["command"], call["command"]


def test_logs_still_works_for_every_manager_name(captured: _Capture) -> None:
    """회귀 방지: `logs`는 한동안 **모든 이름**에서 TypeError로 죽어 있었다.

    `logs`가 `external=None`도 무조건 넘기는데 `run()`이 그 인자를 받지 않았다.
    weather를 더한 변경이 기존 `ktdctl logs map`까지 같이 죽였다.
    """

    ComposeService().logs("kor-travel-map-api", tail=5)

    call = captured.only
    assert call["cwd"] == get_project_root(), "Manager 명령은 저장소 루트에서 돈다"
    assert "-p" not in call["command"], "Manager 경로에 프로젝트 플래그가 붙으면 안 된다"


def test_manager_status_is_unchanged(captured: _Capture) -> None:
    """Manager target의 실행 형태는 한 글자도 바뀌지 않는다."""

    ComposeService().status_target("map")

    call = captured.only
    assert call["cwd"] == get_project_root()
    assert "-p" not in call["command"]
    assert "--project-directory" not in call["command"]


def test_a_two_project_target_runs_once_per_project(captured: _Capture) -> None:
    """`airport`은 `airport-db`에 의존한다 — 두 프로젝트, 두 호출, 각자의 cwd."""

    ComposeService().status_target("airport")

    assert len(captured.calls) == 2, captured.calls
    projects = [_flag_values(call["command"], "-p")[0] for call in captured.calls]
    assert projects == ["kor-travel-airport-db", "kor-travel-airport"]
    for call in captured.calls:
        assert call["cwd"] == "/home/digitie/apps/kor-travel-airport"
    assert _flag_values(captured.calls[0]["command"], "-f") == ["docker-compose.db.yml"]
    assert _flag_values(captured.calls[1]["command"], "-f") == ["docker-compose.yml"]


# ── 형제 프로젝트의 `.env`를 Manager 환경이 덮어쓰지 않는다 ──────────────


def test_external_calls_do_not_inherit_the_managers_environment(
    captured: _Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose에서 **셸 환경은 `.env`보다 우선한다.**

    `--env-file`을 빼는 것만으로는 부족하다. `env=None`이면 `os.environ` 상속이고,
    Manager 백엔드에 설정된 `PROMETHEUS_PORT`·`*_PGDATA` 같은 이름이 형제 프로젝트의
    `.env` 값을 조용히 이긴다. 주석이 막겠다고 적은 바로 그 일이었다.
    """

    monkeypatch.setenv("PROMETHEUS_PORT", "12401")
    monkeypatch.setenv("KOR_TRAVEL_MAP_PGDATA", "/manager/only")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

    ComposeService().status_target("weather")

    env = captured.only["env"]
    assert env is not None, "외부 호출은 환경을 명시해야 한다 — None은 전체 상속이다"
    assert "PROMETHEUS_PORT" not in env
    assert "KOR_TRAVEL_MAP_PGDATA" not in env
    assert env.get("DOCKER_HOST") == "unix:///var/run/docker.sock", (
        "docker 클라이언트 설정은 넘겨야 실제로 같은 데몬에 붙는다"
    )
    assert "PATH" in env


def test_manager_calls_still_inherit_the_process_environment(captured: _Capture) -> None:
    """좁히기가 Manager 경로까지 좁히면 안 된다 — 거기서는 종전대로 상속이다."""

    ComposeService().status_target("map")
    assert captured.only["env"] is None


# ── 변경 경로는 외부를 받지 않는다 ───────────────────────────────────────


def test_run_refuses_external_on_mutation_paths(captured: _Capture) -> None:
    """읽기만 허용한다. 변경 기계(C6c)는 Manager 후보를 전제한다.

    `ensure_target`이 이미 거부하지만 그쪽은 한 층 위다. 여기서 끊어야 새 호출부가
    생겨도 우회되지 않는다.
    """

    from kor_travel_docker_manager.services.registry import external_project_for_target

    service = ComposeService()
    with pytest.raises(DeploymentContractError, match="read-only"):
        service.run(
            ["up", "-d"],
            mutation_capability=object(),
            external=external_project_for_target("weather"),
        )
    assert captured.calls == [], "거부했으면 아무것도 실행되지 않아야 한다"


# ── 묶음 집계가 CLI의 종료 코드를 망가뜨리지 않는가 ──────────────────────


def test_group_aggregate_carries_an_exit_code(captured: _Capture) -> None:
    """`cli._emit_process_result`는 `int(result.get("returncode", 1))`을 읽는다.

    집계 dict에 `returncode`가 없으면 전부 running이어도 `ktdctl status weather`가
    **exit 1**이다. 그 자리를 덮는 검사는 `compose_service`를 Mock으로 세운 것뿐이라
    영영 초록이었다.
    """

    result = ComposeService().status_target("weather")
    assert result["returncode"] == 0
    assert result["success"] is True
    assert "command" in result and "stderr" in result


def test_group_aggregate_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 묶음이라도 실패하면 집계도 실패다 — 성공만 세면 조용히 초록이 된다."""

    def failing(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    monkeypatch.setattr(compose_module.subprocess, "run", failing)
    result = ComposeService().status_target("weather")
    assert result["success"] is False
    assert result["returncode"] == 1
    assert "boom" in result["stderr"]


# ── 라운드 2: 리뷰어 둘이 **독립적으로** 같은 곳을 짚었다 ────────────────
#
# **guard가 틀린 술어에 걸려 있었다.** `run()`의 external guard는 인자의 *존재*로
# 판정했는데, 변경 분기로 들어갈지를 실제로 정하는 것은
# `_compose_mutation_identifiers(args)`다. 그래서 `run(["up","-d"], external=X)`가
# guard를 통과했고, 그 아래 분기는 `external`을 조용히 버려서 형제 프로젝트를 지시한
# 명령이 **Manager 자신의 프로젝트에** 갔다.
#
# 그리고 guard의 네 조건 중 **셋을 개별로 제거해도 1797건이 전부 초록**이었다 —
# 검사가 `mutation_capability` 하나만 줬기 때문이다. 아래는 그 축을 전부 태운다.


def _weather() -> Any:
    from kor_travel_docker_manager.services.registry import external_project_for_target

    return external_project_for_target("weather")


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"mutation_capability": object()}, id="capability"),
        pytest.param({"expected_system_bind_snapshots": ()}, id="bind-snapshots"),
    ],
)
def test_each_mutation_input_alone_refuses_an_external_project(
    captured: _Capture, kwargs: dict[str, Any]
) -> None:
    """guard의 조건을 **하나씩** 태운다.

    첫 판의 검사는 `mutation_capability=object()` 하나만 줘서, 나머지 조건을 지우는
    변이가 전부 살아남았다(적대 리뷰 2026-09-18, 리뷰어 둘이 각각 실측).
    """

    service = ComposeService()
    with pytest.raises(DeploymentContractError, match="read-only"):
        service.run(["ps"], external=_weather(), **kwargs)
    assert captured.calls == []


def test_a_mutating_command_refuses_an_external_project(captured: _Capture) -> None:
    """**인자가 아니라 명령이 변경 여부를 정한다.**

    넷을 다 비우고 `up -d`만 줘도 거부돼야 한다. 첫 판은 여기서 guard를 통과한 뒤
    C6c 변경 기계로 들어갔고, 그 분기의 `_run_unlocked` 호출은 `external`을 넘기지
    않으므로 **Manager 자신의 compose**에 `up`이 갔다 — 운영자는 형제 프로젝트를
    만졌다고 믿는다. C-2·C-3와 정확히 같은 계열의 조용한 오답이다.
    """

    service = ComposeService()
    with pytest.raises(DeploymentContractError, match="read-only"):
        service.run(["up", "-d"], external=_weather())
    assert captured.calls == []


def test_the_unlocked_layer_is_the_last_net(captured: _Capture) -> None:
    """`run()`을 우회해도 변경 입력과 `external`은 함께 올 수 없다.

    변경 분기의 `_run_unlocked` 호출 두 곳에 `assert`를 박는 대신 **피호출자**가
    거부한다 — 자리가 둘이면 한쪽을 지워도 아무 검사가 빨개지지 않는다.
    """

    service = ComposeService()
    with pytest.raises(DeploymentContractError, match="mutation machinery"):
        service._run_unlocked(
            ["up", "-d"],
            capture_output=True,
            environment=None,
            redact_config=None,
            expected_system_bind_snapshots=(),
            expected_compose_source_bytes=None,
            environment_snapshot=None,
            external_input_snapshot=None,
            materialized_compose=None,
            external=_weather(),
        )
    assert captured.calls == []


def test_the_narrowed_environment_is_exactly_the_allowlist(
    captured: _Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """통과 집합의 **내용**을 결박한다.

    첫 판 검사는 `PROMETHEUS_PORT`·`KOR_TRAVEL_MAP_PGDATA` 두 이름만 핀으로 박아서,
    allowlist에 임의의 이름을 **더하는** 변이가 살아남았다(적대 리뷰 2026-09-18
    B-M08c). `main.py`가 Manager `.env`를 `os.environ`에 통째로 싣기 때문에, 한 이름이
    새는 것이 곧 Manager 비밀이 형제 프로세스 env로 가는 것이다.

    그래서 카나리아를 잔뜩 심고 **결과 키 집합 자체**를 단언한다.
    """

    for index in range(12):
        monkeypatch.setenv(f"KTDM_CANARY_{index}", "leak")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

    ComposeService().status_target("weather")

    env = captured.only["env"]
    assert env is not None
    expected = {
        name
        for name in os.environ
        if name in compose_module._EXTERNAL_PROJECT_PASSTHROUGH_ENV
        or name.startswith("DOCKER_")
    }
    assert set(env) == expected, "좁힌 집합이 allowlist와 정확히 같아야 한다"
    assert not any(name.startswith("KTDM_CANARY_") for name in env)


@pytest.mark.parametrize(
    "name", sorted({"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "XDG_RUNTIME_DIR"})
)
def test_every_passthrough_name_is_declared(name: str) -> None:
    """집합의 각 이름이 **의도된 것**임을 하나씩 센다.

    `HOME`을 지우는 변이가 살아남았다 — docker CLI가 `~/.docker/config.json`과
    `~/.docker/contexts`(= **어느 데몬에 붙는가**)를 읽는 유일한 통로라 값어치가
    `DOCKER_HOST`와 같은 급이다.
    """

    assert name in compose_module._EXTERNAL_PROJECT_PASSTHROUGH_ENV


def test_the_environment_argument_does_not_reopen_full_inheritance(
    captured: _Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """명시 `environment`가 좁히기를 **건너뛰게** 하지 않는다.

    첫 판은 좁히기가 `if process_environment is None:` 안에 있어서, `environment`
    인자가 주어지면 그 앞의 `{**os.environ, **environment}`가 그대로 나갔다 —
    `docs/ports.md`에 무조건문으로 적은 문장이 조건부였다.
    """

    monkeypatch.setenv("KTDM_CANARY_ENVARG", "leak")
    service = ComposeService()
    service._run_unlocked(
        ["ps"],
        capture_output=True,
        environment={"COMPOSE_PROFILES": "x"},
        redact_config=None,
        expected_system_bind_snapshots=None,
        expected_compose_source_bytes=None,
        environment_snapshot=None,
        external_input_snapshot=None,
        materialized_compose=None,
        external=_weather(),
    )
    env = captured.only["env"]
    assert env is not None
    assert "KTDM_CANARY_ENVARG" not in env
    assert env["COMPOSE_PROFILES"] == "x", "명시 인자는 좁힌 것 위에 덮인다"


def test_the_group_label_names_the_project(captured: _Capture) -> None:
    """묶음 라벨이 **실제로 쓰인다**.

    `ServiceGroup.project_label` 속성은 검사됐지만 그 **사용처**는 아니어서, 라벨을
    리터럴로 바꿔도 전부 초록이었다. 그러면 `ktdctl status airport --json`의 두 묶음과
    `# project=` 헤더가 전부 오표기돼도 아무도 모른다 — 그 dict의 docstring은
    "소비자가 `groups`를 읽게 한다"고 말한다.
    """

    result = ComposeService().status_target("airport")
    assert [group["project"] for group in result["groups"]] == [
        "kor-travel-airport-db",
        "kor-travel-airport",
    ]
    assert "# project=kor-travel-airport-db" in result["stdout"]


def test_a_missing_working_directory_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """가장 흔할 오설정이 "docker 바이너리 없음"과 구별돼야 한다.

    첫 판은 `except OSError:`가 예외 이름조차 받지 않아 둘이 같은 문구였다.
    `working_dir`이 호스트마다 다른 값이 된 지금은 그 구별이 진단의 전부다.
    """

    def missing(command: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(compose_module.subprocess, "run", missing)
    result = ComposeService().status_target("weather")
    assert result["returncode"] == 1
    assert "/home/digitie/kor-travel-weather" in result["stderr"]


def test_container_scoped_logs_translate_manager_ids_too(captured: _Capture) -> None:
    """**컨테이너 id는 compose service 이름이 아니다.**

    첫 판은 외부 컨테이너만 번역하고 Manager 컨테이너는 id를 그대로 넘겼다 —
    `kor-travel-map-postgresql`(서비스는 `kor-travel-map-postgres`)처럼 둘이 다른
    이름 넷에서 `no such service`다. 선재 결함이지만 같은 함수의 한쪽 분기만 고쳐
    비대칭이 남아 있었다.
    """

    ComposeService().logs("kor-travel-map-postgresql", tail=3)
    command = captured.only["command"]
    assert command[-1] == "kor-travel-map-postgres"
