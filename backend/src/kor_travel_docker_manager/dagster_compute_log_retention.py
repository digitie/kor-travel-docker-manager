"""Run a registered code server's compute-log maintenance under deployment locks."""

from __future__ import annotations

import argparse
import json
import re
from contextlib import closing
from pathlib import Path, PurePosixPath

import docker
from docker.errors import DockerException

from kor_travel_docker_manager.services import persistent_dagster_state as persistent
from kor_travel_docker_manager.services import service_release_candidate as candidate_service
from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
    ManagerMutationActiveError,
    manager_mutation_lock,
)
from kor_travel_docker_manager.services.registry import TargetsConfigError, load_targets_config
from kor_travel_docker_manager.services.service_release import ServiceReleaseError
from kor_travel_docker_manager.services.transport_core_state import project_lease

WORKER_TIMEOUT_SECONDS = 600


class RetentionWorkerTimeout(ServiceReleaseError):
    pass


def _target(config: dict, target: str) -> tuple[str, str, str, str, Path]:
    registrations = config.get("dagster_compute_log_retention", {})
    settings = registrations.get(target) if isinstance(registrations, dict) else None
    if not isinstance(settings, dict) or set(settings) != {"service", "python", "module"}:
        raise ServiceReleaseError("등록된 Dagster 로그 유지보수 대상이 아닙니다.")
    candidate = config["targets"][target]["core_candidate"]
    group = config["service_releases"][target]["groups"][candidate["group"]]
    project = (
        config["targets"][target]["external_project"]["project"]
        if group.get("external_project") is True else group["project"]
    )
    service, python, module = (settings[key] for key in ("service", "python", "module"))
    lease = Path(candidate["project_lease"])
    if (
        service not in group["runtime_services"]
        or not isinstance(python, str)
        or not python.startswith("/")
        or re.fullmatch(r"(?:/[a-zA-Z0-9_.-]+)+", python) is None
        or ".." in PurePosixPath(python).parts
        or not isinstance(module, str)
        or re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)+", module, re.ASCII) is None
        or not lease.is_absolute()
    ):
        raise ServiceReleaseError("Dagster 로그 유지보수 설정이 올바르지 않습니다.")
    return project, service, python, module, lease


def _running_code_server(records: list[dict], project: str, service: str) -> dict:
    candidates = [
        record for record in records
        if candidate_service._selected(record, project, service)
        and record["State"]["Status"] == "running"
    ]
    if len(candidates) != 1:
        raise ServiceReleaseError("실행 중인 대상 코드 서버를 한 개로 확인할 수 없습니다.")
    live = candidates[0]
    if (
        live["Config"]["Labels"].get("com.docker.compose.oneoff", "false").lower() != "false"
        or live["State"].get("Health", {}).get("Status") != "healthy"
    ):
        raise ServiceReleaseError("대상 코드 서버가 정상 실행 중이어야 합니다.")
    return live


def run(config: dict, target: str, *, apply: bool) -> dict:
    project, service, python, module, lease = _target(config, target)
    policy = persistent.policy_for(target, service)
    mode = "apply" if apply else "dry-run"
    with manager_mutation_lock(), project_lease(lease):
        with closing(docker.DockerClient(base_url=persistent.LOCAL_DOCKER_HOST, timeout=WORKER_TIMEOUT_SECONDS + 60)) as docker_client:
            client = docker_client.api
            records = candidate_service._inventory(docker_client)
            live = _running_code_server(records, project, service)
            proof = persistent.observe_volume_proof(policy, docker_client)
            persistent.verify_volume(policy, docker_client, proof, records, live)
            # Finish the in-container process before the Docker client can release the host lease.
            command = [
                "/usr/bin/timeout", "--kill-after=5s", f"{WORKER_TIMEOUT_SECONDS}s",
                python, "-I", "-m", module, f"--{mode}",
            ]
            process = client.exec_create(live["Id"], command)
            output, _stderr = client.exec_start(process["Id"], demux=True)
            status = client.exec_inspect(process["Id"])["ExitCode"]

    if status == 124:
        raise RetentionWorkerTimeout("Dagster 로그 유지보수 실행 시간이 초과되었습니다.")
    report = json.loads(output or b"null")
    if (
        not isinstance(report, dict)
        or report.get("schema_version") != 1
        or report.get("mode") != mode
        or status != (0 if report.get("ok") is True else 1)
        or type(report.get("ok")) is not bool
    ):
        raise ServiceReleaseError("코드 서버의 로그 유지보수 결과가 올바르지 않습니다.")
    return {"target": target, **report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--apply", action="store_true", help="만료된 실행 로그 삭제")
    options = parser.parse_args()
    try:
        result = run(load_targets_config(), options.target, apply=options.apply)
    except (ManagerMutationActiveError, BlockingIOError):
        result = {"target": options.target, "ok": True, "state": "deployment_busy"}
    except (
        DeploymentContractError, DockerException, KeyError, OSError, ServiceReleaseError,
        TargetsConfigError, TypeError, ValueError,
    ) as exc:
        # Docker errors may include daemon connection details. Report the category only.
        result = {"target": options.target, "ok": False, "error": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
