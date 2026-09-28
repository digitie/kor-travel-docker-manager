"""`up --no-deps`가 설정이 어긋난(drift) PostgreSQL 의존성을 건드리지 않는지 진짜 Compose로 본다(§1.6 T-R3).

재구축은 모든 `up`/`run`에 `--no-deps`를 강제하고(R3 chokepoint의 전제), 공용 instance의 튜닝
release(MT)는 설치 직후 계획된 재생성으로 이 전제에 기댄다. 설치가 compose 정의를 바꾸면 떠 있는
컨테이너의 config hash가 어긋나고, 그 뒤 `--no-deps` 없는 `up`이 의존 서비스를 부르면 compose는
그 PostgreSQL을 **다시 만든다** — 모든 tenant가 끊긴다(T-R3c가 그 위험을 기록한다).

gate `KTDM_REQUIRE_DOCKER_INTEGRATION`: 0이면 Docker가 없을 때 skip, 1이면 실패.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

#: 공용 instance(`kor-travel-shared-postgres`)가 도는 바로 그 이미지다(2026-09-28 n150 실측).
_SHARED_IMAGE = (
    "postgis/postgis@sha256:8b33190b6486ab9905dea999171817c1ac461733a7078dd4c836091c6e6b5d40"
)
_REQUIRED_GATE_ENV = "KTDM_REQUIRE_DOCKER_INTEGRATION"
_PORT = 15433
_ADMIN = "it_admin"


def _required_docker_gate() -> bool:
    value = os.environ.get(_REQUIRED_GATE_ENV, "0").strip()
    if value not in {"0", "1"}:
        pytest.fail(f"{_REQUIRED_GATE_ENV}는 0 또는 1이어야 함")
    return value == "1"


def _unavailable(reason: str) -> None:
    if _required_docker_gate():
        pytest.fail(reason)
    pytest.skip(f"{reason}; 필수 gate는 {_REQUIRED_GATE_ENV}=1로 실행")


def _docker(*arguments: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments], text=True, capture_output=True, check=False, timeout=timeout
    )


def _require_shared_image() -> None:
    for command in (["compose", "version"], ["info"]):
        try:
            completed = _docker(*command)
        except (OSError, subprocess.TimeoutExpired):
            _unavailable("로컬 Docker Compose를 사용할 수 없음")
        if completed.returncode != 0:
            _unavailable("로컬 Docker Compose를 사용할 수 없음")
    if _docker("image", "inspect", _SHARED_IMAGE).returncode == 0:
        return
    if not _required_docker_gate():
        _unavailable(f"pull 없는 로컬 {_SHARED_IMAGE}를 사용할 수 없음")
    pull = _docker("pull", _SHARED_IMAGE, timeout=600)
    if pull.returncode != 0:
        pytest.fail(f"공용 PostgreSQL 이미지 pull 실패: {pull.stderr.strip()}")


class _Project:
    """임시 compose 프로젝트 하나. 문서를 바꿔 쓰는 것으로 drift를 만든다."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "compose.yml"
        self.name = f"ktdm-it-r3-{uuid.uuid4().hex[:12]}"
        self.environment = {**os.environ, "IT_PG_PASSWORD": secrets.token_urlsafe(32)}

    def write(self, *, pg_extra: list[str], app_marker: str) -> None:
        self.path.write_text(
            yaml.safe_dump(
                {
                    "services": {
                        "pg": {
                            "image": _SHARED_IMAGE,
                            "pull_policy": "never",
                            "network_mode": "none",
                            "environment": {
                                "POSTGRES_USER": _ADMIN,
                                "POSTGRES_PASSWORD": "${IT_PG_PASSWORD:?}",
                            },
                            "command": ["postgres", "-p", str(_PORT), *pg_extra],
                            "tmpfs": ["/var/lib/postgresql/data"],
                            "healthcheck": {
                                "test": [
                                    "CMD", "pg_isready", "--port", str(_PORT),
                                    "--username", _ADMIN,
                                ],
                                "interval": "1s",
                                "timeout": "5s",
                                "retries": 240,
                                "start_period": "2s",
                            },
                        },
                        "app": {
                            "image": _SHARED_IMAGE,
                            "pull_policy": "never",
                            "network_mode": "none",
                            "entrypoint": ["sleep", "infinity"],
                            "labels": {"ktdm.it.marker": app_marker},
                            "depends_on": ["pg"],
                        },
                    }
                },
                default_flow_style=False,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def compose(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "docker", "compose", "--file", str(self.path),
                "--project-name", self.name, *arguments,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=400,
            env=self.environment,
        )

    def container_id(self, service: str) -> str:
        completed = self.compose("ps", "--all", "--quiet", service)
        assert completed.returncode == 0, completed.stderr
        identifier = completed.stdout.strip()
        assert identifier and "\n" not in identifier, completed.stdout
        return identifier

    def postgres_identity(self) -> tuple[str, str, str, str]:
        """(컨테이너 ID, StartedAt, postmaster 시작 시각, checkpointer backend_start)."""

        identifier = self.container_id("pg")
        started = _docker("inspect", "--format", "{{.State.StartedAt}}", identifier)
        assert started.returncode == 0, started.stderr
        server = _docker(
            "exec", "--user", "postgres", identifier,
            "psql", "--no-psqlrc", "--tuples-only", "--no-align",
            "--username", _ADMIN, "--port", str(_PORT), "--dbname", "postgres",
            "--command",
            "SELECT pg_catalog.pg_postmaster_start_time(), (SELECT backend_start "
            "FROM pg_catalog.pg_stat_activity WHERE backend_type = 'checkpointer')",
        )
        assert server.returncode == 0, server.stderr
        postmaster, checkpointer = server.stdout.strip().split("|")
        assert postmaster and checkpointer, server.stdout
        return identifier, started.stdout.strip(), postmaster, checkpointer

    def config_drifted(self, service: str) -> bool:
        """compose가 다음 `up`에서 이 서비스를 다시 만들 조건(config hash 불일치)인가."""

        wanted = self.compose("config", "--hash", service)
        assert wanted.returncode == 0, wanted.stderr
        running = _docker(
            "inspect",
            "--format",
            '{{index .Config.Labels "com.docker.compose.config-hash"}}',
            self.container_id(service),
        )
        assert running.returncode == 0, running.stderr
        return wanted.stdout.split()[-1] != running.stdout.strip()

    def remove(self) -> None:
        down = self.compose("down", "--volumes", "--remove-orphans", "--timeout", "30")
        label = f"label=com.docker.compose.project={self.name}"
        leftovers = _docker("ps", "--all", "--quiet", "--filter", label).stdout.split()
        if leftovers:
            _docker("rm", "--force", "--volumes", *leftovers)
        remaining = (
            _docker("ps", "--all", "--quiet", "--filter", label).stdout.split()
            + _docker("network", "ls", "--quiet", "--filter", label).stdout.split()
            + _docker("volume", "ls", "--quiet", "--filter", label).stdout.split()
        )
        assert down.returncode == 0 and not leftovers and not remaining, (
            f"compose fixture cleanup: down={down.returncode}, "
            f"leftovers={len(leftovers)}, remaining={len(remaining)}"
        )


@pytest.fixture
def drifted_project(tmp_path: Path) -> Iterator[_Project]:
    """떠 있는 pg·app을 만든 뒤 두 서비스의 정의를 모두 바꾼다(pg는 설치된 새 release의 모양)."""

    _require_shared_image()
    project = _Project(tmp_path)
    project.write(pg_extra=[], app_marker="one")
    try:
        up = project.compose("up", "--detach", "--wait", "--wait-timeout", "300")
        assert up.returncode == 0, up.stderr
        project.write(pg_extra=["-c", "work_mem=8MB"], app_marker="two")
        assert project.config_drifted("pg")
        assert project.config_drifted("app")
        yield project
    finally:
        project.remove()


def test_no_deps_up_of_a_dependent_leaves_a_drifted_postgres_alone(
    drifted_project: _Project,
) -> None:
    """T-R3: `--no-deps`면 dependent만 다시 만들고, drift된 PostgreSQL은 한 번도 멈추지 않는다."""

    before = drifted_project.postgres_identity()
    app_before = drifted_project.container_id("app")

    up = drifted_project.compose("up", "--detach", "--no-deps", "app")

    assert up.returncode == 0, up.stderr
    # 호출이 실제로 일을 했다 — dependent는 새 정의로 다시 만들어졌다.
    assert drifted_project.container_id("app") != app_before
    assert drifted_project.postgres_identity() == before
    assert drifted_project.config_drifted("pg")


def test_up_without_no_deps_recreates_the_drifted_postgres(drifted_project: _Project) -> None:
    """T-R3c: 대조군 — `--no-deps`가 없으면 compose가 drift된 의존 PostgreSQL을 다시 만든다."""

    before = drifted_project.postgres_identity()

    up = drifted_project.compose("up", "--detach", "app")

    assert up.returncode == 0, up.stderr
    assert drifted_project.container_id("pg") != before[0]
    assert not drifted_project.config_drifted("pg")
