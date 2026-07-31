from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
    ComposeTransactionSnapshot,
)

_FIXTURE_IMAGE = (
    "alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"
)
_REQUIRED_GATE_ENV = "KTDM_REQUIRE_DOCKER_INTEGRATION"


def _docker_compose(
    compose_path: Path,
    project_name: str,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "--file",
            str(compose_path),
            "--project-name",
            project_name,
            *arguments,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _docker(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _required_docker_gate() -> bool:
    value = os.environ.get(_REQUIRED_GATE_ENV, "0").strip()
    if value not in {"0", "1"}:
        pytest.fail(f"{_REQUIRED_GATE_ENV}는 0 또는 1이어야 함")
    return value == "1"


def _unavailable_docker_fixture(reason: str) -> None:
    if _required_docker_gate():
        pytest.fail(reason)
    pytest.skip(f"{reason}; 필수 gate는 {_REQUIRED_GATE_ENV}=1로 실행")


def _fixture_image_id() -> str:
    for command in (["compose", "version"], ["info"]):
        try:
            completed = _docker(*command)
        except (OSError, subprocess.TimeoutExpired):
            _unavailable_docker_fixture("로컬 Docker Compose fixture를 사용할 수 없음")
        if completed.returncode != 0:
            _unavailable_docker_fixture("로컬 Docker Compose fixture를 사용할 수 없음")
    inspect = _docker("image", "inspect", "--format", "{{.Id}}", _FIXTURE_IMAGE)
    if inspect.returncode != 0:
        if not _required_docker_gate():
            _unavailable_docker_fixture(
                f"pull 없는 로컬 {_FIXTURE_IMAGE} fixture를 사용할 수 없음"
            )
        pull = _docker("pull", _FIXTURE_IMAGE)
        if pull.returncode != 0:
            pytest.fail(f"필수 Docker fixture pull 실패: {pull.stderr.strip()}")
        inspect = _docker("image", "inspect", "--format", "{{.Id}}", _FIXTURE_IMAGE)
    image_id = inspect.stdout.strip()
    if inspect.returncode != 0 or not image_id.startswith("sha256:"):
        pytest.fail("Docker fixture의 immutable image ID를 확인할 수 없음")
    return image_id


def _project_resource_ids(project_name: str) -> tuple[list[str], list[str], list[str]]:
    filters = ["--filter", f"label=com.docker.compose.project={project_name}"]
    containers = _docker("ps", "--all", "--quiet", *filters)
    networks = _docker("network", "ls", "--quiet", *filters)
    volumes = _docker("volume", "ls", "--quiet", *filters)
    for result in (containers, networks, volumes):
        if result.returncode != 0:
            pytest.fail("Docker Compose fixture residue를 조회할 수 없음")
    return (
        containers.stdout.split(),
        networks.stdout.split(),
        volumes.stdout.split(),
    )


def _cleanup_project(compose_path: Path, project_name: str) -> None:
    down = _docker_compose(
        compose_path,
        project_name,
        "down",
        "--volumes",
        "--remove-orphans",
    )
    residue_after_down = _project_resource_ids(project_name)
    if residue_after_down[0]:
        _docker("rm", "--force", *residue_after_down[0])
    if residue_after_down[1]:
        _docker("network", "rm", *residue_after_down[1])
    if residue_after_down[2]:
        _docker("volume", "rm", "--force", *residue_after_down[2])
    remaining = _project_resource_ids(project_name)
    if down.returncode != 0 or any(residue_after_down) or any(remaining):
        pytest.fail(
            "Docker Compose fixture cleanup 실패: "
            f"down={down.returncode}, "
            f"residue={tuple(map(len, residue_after_down))}, "
            f"remaining={tuple(map(len, remaining))}"
        )


def test_canonical_compose_readiness_matches_real_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture_image_id = _fixture_image_id()
    compose_path = tmp_path / "compose.yml"
    project_name = f"ktdm-readiness-{os.getpid()}-{tmp_path.name[-8:]}".lower()
    compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "healthy": {
                        "image": fixture_image_id,
                        "pull_policy": "never",
                        "command": ["sh", "-c", "touch /tmp/ready && sleep 120"],
                        "healthcheck": {
                            "test": ["CMD", "test", "-f", "/tmp/ready"],
                            "interval": "100ms",
                            "timeout": "100ms",
                            "retries": 3,
                            "start_period": "100ms",
                        },
                    },
                    "running-only": {
                        "image": fixture_image_id,
                        "pull_policy": "never",
                        "command": ["sleep", "120"],
                    },
                    "unhealthy": {
                        "image": fixture_image_id,
                        "pull_policy": "never",
                        "command": ["sleep", "120"],
                        "healthcheck": {
                            "test": ["CMD", "test", "-f", "/tmp/never"],
                            "interval": "100ms",
                            "timeout": "100ms",
                            "retries": 1,
                            "start_period": "100ms",
                        },
                    },
                }
            },
            default_flow_style=False,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    try:
        up = _docker_compose(
            compose_path,
            project_name,
            "up",
            "--detach",
            "--pull",
            "never",
        )
        assert up.returncode == 0, up.stderr
        config = _docker_compose(
            compose_path,
            project_name,
            "config",
            "--format",
            "json",
        )
        assert config.returncode == 0, config.stderr
        resolved = json.loads(config.stdout)
        service = ComposeService()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ps = _docker_compose(
                compose_path,
                project_name,
                "ps",
                "--all",
                "--format",
                "json",
                "healthy",
                "running-only",
                "unhealthy",
            )
            assert ps.returncode == 0, ps.stderr
            records = service._compose_ps_records(ps.stdout, allow_empty=True)
            health_by_service = {
                str(record["Service"]): str(record.get("Health", "")).lower()
                for record in records
            }
            if (
                health_by_service.get("healthy") == "healthy"
                and health_by_service.get("unhealthy") == "unhealthy"
            ):
                break
            time.sleep(0.1)
        else:
            pytest.fail("실제 Compose health 상태가 제한 시간 안에 확정되지 않음")

        def run(arguments, **_kwargs):  # type: ignore[no-untyped-def]
            result = _docker_compose(compose_path, project_name, *arguments)
            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        monkeypatch.setattr(service, "run", run)
        transaction = Mock(spec=ComposeTransactionSnapshot)
        transaction.resolved = resolved

        ready = service._require_services_ready(
            ["healthy", "running-only"],
            transaction=transaction,
        )
        assert [record["Service"] for record in ready] == [
            "healthy",
            "running-only",
        ]
        with pytest.raises(
            DeploymentContractError,
            match="canonical readiness",
        ):
            service._require_services_ready(
                ["unhealthy"],
                transaction=transaction,
            )

        scale = _docker_compose(
            compose_path,
            project_name,
            "up",
            "--detach",
            "--pull",
            "never",
            "--scale",
            "running-only=2",
            "running-only",
        )
        assert scale.returncode == 0, scale.stderr
        scaled = _docker_compose(
            compose_path,
            project_name,
            "ps",
            "--all",
            "--format",
            "json",
            "running-only",
        )
        assert scaled.returncode == 0, scaled.stderr
        scaled_records = service._compose_ps_records(scaled.stdout)
        assert len(scaled_records) == 2
        stopped = _docker("stop", str(scaled_records[0]["Name"]))
        assert stopped.returncode == 0, stopped.stderr

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            all_ps = _docker_compose(
                compose_path,
                project_name,
                "ps",
                "--all",
                "--format",
                "json",
                "running-only",
            )
            assert all_ps.returncode == 0, all_ps.stderr
            all_records = service._compose_ps_records(all_ps.stdout)
            states = sorted(str(record["State"]).lower() for record in all_records)
            if len(states) == 2 and states == ["exited", "running"]:
                break
            time.sleep(0.1)
        else:
            pytest.fail("scale fixture의 stopped/running 상태가 확정되지 않음")

        default_ps = _docker_compose(
            compose_path,
            project_name,
            "ps",
            "--format",
            "json",
            "running-only",
        )
        assert default_ps.returncode == 0, default_ps.stderr
        default_records = service._compose_ps_records(default_ps.stdout)
        assert [record["State"].lower() for record in default_records] == ["running"]
        with pytest.raises(
            DeploymentContractError,
            match="duplicate singleton",
        ):
            service._require_services_ready(
                ["running-only"],
                transaction=transaction,
            )
    finally:
        _cleanup_project(compose_path, project_name)
