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
