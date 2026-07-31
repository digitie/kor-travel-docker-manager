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


def _require_local_docker_fixture() -> None:
    for command in (
        ["docker", "compose", "version"],
        ["docker", "info"],
        ["docker", "image", "inspect", "alpine:3.20"],
    ):
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("로컬 Docker Compose fixture를 사용할 수 없음")
        if completed.returncode != 0:
            pytest.skip("pull 없는 로컬 Docker Compose fixture를 사용할 수 없음")


def test_canonical_compose_readiness_matches_real_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _require_local_docker_fixture()
    compose_path = tmp_path / "compose.yml"
    project_name = f"ktdm-readiness-{os.getpid()}-{tmp_path.name[-8:]}".lower()
    compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "healthy": {
                        "image": "alpine:3.20",
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
                        "image": "alpine:3.20",
                        "pull_policy": "never",
                        "command": ["sleep", "120"],
                    },
                    "unhealthy": {
                        "image": "alpine:3.20",
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
        ps_payload = ""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ps = _docker_compose(
                compose_path,
                project_name,
                "ps",
                "--format",
                "json",
                "healthy",
                "running-only",
                "unhealthy",
            )
            assert ps.returncode == 0, ps.stderr
            ps_payload = ps.stdout
            records = service._compose_ps_records(ps_payload, allow_empty=True)
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

        monkeypatch.setattr(
            service,
            "run",
            lambda *_args, **_kwargs: {
                "success": True,
                "stdout": ps_payload,
            },
        )
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
    finally:
        _docker_compose(
            compose_path,
            project_name,
            "down",
            "--volumes",
            "--remove-orphans",
        )
