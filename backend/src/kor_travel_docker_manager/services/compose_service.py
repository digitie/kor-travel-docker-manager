import os
import subprocess
from collections.abc import Sequence
from typing import Any

from kor_travel_docker_manager.services.registry import (
    init_steps_for_target,
    is_known_target,
    runtime_services_for_target,
    services_for_target,
    target_sequence_for_target,
)


def get_project_root() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(current_dir, "../../../../"))


def get_compose_path() -> str:
    return os.environ.get(
        "KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE",
        os.path.join(get_project_root(), "docker-compose.yml"),
    )


def get_env_path() -> str:
    return os.environ.get(
        "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE",
        os.path.join(get_project_root(), ".env"),
    )


class ComposeService:
    def build_command(self, args: Sequence[str]) -> list[str]:
        command = ["docker", "compose"]
        env_path = get_env_path()
        if os.path.exists(env_path):
            command.extend(["--env-file", env_path])
        command.extend(["-f", get_compose_path()])
        command.extend(args)
        return command

    def run(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = True,
    ) -> dict[str, Any]:
        command = self.build_command(args)
        try:
            completed = subprocess.run(
                command,
                cwd=get_project_root(),
                text=True,
                capture_output=capture_output,
                check=False,
            )
        except FileNotFoundError as exc:
            return {
                "success": False,
                "returncode": 127,
                "command": command,
                "stdout": "",
                "stderr": str(exc),
            }

        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "command": command,
            "stdout": completed.stdout if capture_output else "",
            "stderr": completed.stderr if capture_output else "",
        }

    def ensure_target(
        self,
        target: str,
        *,
        build: bool = False,
        recreate: bool = False,
        capture_output: bool = True,
    ) -> dict[str, Any]:
        target_sequence = target_sequence_for_target(target)
        services = services_for_target(target)
        init_steps = init_steps_for_target(target)
        commands: list[list[str]] = []
        init_results: list[dict[str, Any]] = []

        result: dict[str, Any] = {
            "success": True,
            "returncode": 0,
            "target": target,
            "target_sequence": target_sequence,
            "services": services,
            "init_results": init_results,
            "command": [],
            "stdout": "",
            "stderr": "",
        }

        if services:
            args = ["up", "-d"]
            if build:
                args.append("--build")
            if recreate:
                args.append("--force-recreate")
            args.extend(services)
            up_result = self.run(args, capture_output=capture_output)
            commands.append(up_result["command"])
            result["stdout"] += up_result.get("stdout", "")
            result["stderr"] += up_result.get("stderr", "")
            result["returncode"] = up_result["returncode"]
            result["success"] = up_result["success"]
            if not up_result["success"]:
                result["command"] = commands
                return result

        for step in init_steps:
            step_command = step.get("command", [])
            step_result = self.run(step_command, capture_output=capture_output)
            step_result = {
                "target": step.get("target"),
                "name": step.get("name"),
                "description": step.get("description"),
                **step_result,
            }
            init_results.append(step_result)
            commands.append(step_result["command"])
            result["stdout"] += step_result.get("stdout", "")
            result["stderr"] += step_result.get("stderr", "")
            if not step_result["success"]:
                result["success"] = False
                result["returncode"] = step_result["returncode"]
                result["command"] = commands
                return result

        result["command"] = commands
        return result

    def status_target(self, target: str = "all", *, capture_output: bool = True) -> dict[str, Any]:
        services = services_for_target(target)
        result = self.run(["ps", *services], capture_output=capture_output)
        result["target"] = target
        result["target_sequence"] = target_sequence_for_target(target)
        result["services"] = services
        return result

    def logs(
        self,
        name: str,
        *,
        follow: bool = False,
        tail: int = 100,
        capture_output: bool = True,
    ) -> dict[str, Any]:
        if is_known_target(name):
            services = runtime_services_for_target(name)
        else:
            services = [name]

        args = ["logs", f"--tail={tail}"]
        if follow:
            args.append("-f")
        args.extend(services)
        result = self.run(args, capture_output=capture_output)
        result["target"] = name
        if is_known_target(name):
            result["target_sequence"] = target_sequence_for_target(name)
        result["services"] = services
        return result


compose_service = ComposeService()
