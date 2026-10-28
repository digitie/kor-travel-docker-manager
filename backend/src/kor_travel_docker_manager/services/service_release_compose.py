"""현재 release의 선택 runtime만 고정 Compose로 재생성한다.

호출자가 root와 기존 Manager 전역 잠금 G를 확보한 뒤 client·고정 plan·manifest를
전달해야 한다. 이 모듈은 잠금을 풀거나 pointer·manifest를 갱신하지 않는다.
"""
from __future__ import annotations

import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services import service_release
from kor_travel_docker_manager.services.compose_service import (
    _EXTERNAL_PROJECT_PASSTHROUGH_ENV,
    _escape_materialized_compose_environment_values,
)
from kor_travel_docker_manager.services.secret_scrub import (
    load_secret_environment,
    redact_secret_text,
)
from kor_travel_docker_manager.services.service_release import ServiceReleaseError


def _environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if key in _EXTERNAL_PROJECT_PASSTHROUGH_ENV or key.startswith("DOCKER_")}


def _secrets(manifests: dict[str, dict[str, Any]]) -> list[tuple[str, str | None]]:
    result: list[tuple[str, str | None]] = []
    for manifest in manifests.values():
        for group in manifest["groups"].values():
            path = group["env_file"] or str(Path(group["working_dir"]) / ".env")
            result.extend(load_secret_environment(path))
    return result


def _resolved_secrets(resolved: dict[str, Any]) -> list[tuple[str, str | None]]:
    result: list[tuple[str, str | None]] = []
    for definition in resolved["services"].values():
        environment = definition.get("environment", {}) if isinstance(definition, dict) else {}
        if isinstance(environment, dict):
            result.extend((str(key), str(value) if value is not None else None)
                          for key, value in environment.items())
        elif isinstance(environment, list):
            for entry in environment:
                if isinstance(entry, str):
                    key, separator, value = entry.partition("=")
                    result.append((key, value if separator else None))
    return result


def _detail(text: str | bytes | None, secrets: list[tuple[str, str | None]] | None) -> str:
    if secrets is None:
        return "실패 상세를 가릴 앱 환경파일을 읽지 못했습니다"
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return redact_secret_text(text or "", secrets)[:4000]


def _verify(manifests: dict[str, dict[str, Any]], client: Any) -> dict[str, Any]:
    return {name: service_release.verify_release_manifest(manifest, client=client)
            for name, manifest in manifests.items()}


def recreate_current(plan: dict[str, Any], manifests: dict[str, dict[str, Any]], *,
                     client: Any, wait_timeout: int = 120, execute: bool = False) -> dict[str, Any]:
    """root/G를 보유한 호출자 아래 config를 고정하고 선택 runtime만 다룬다.

    execute=False도 현재 release 건강·resolved 이미지 ID를 검증한다. Docker 변경은
    execute=True일 때만 수행한다. mutation subprocess의 host timeout은 두지 않는다.
    CLI만 죽이고 daemon의 작업이 계속되는 동안 호출자가 G를 놓지 않도록 Compose의
    --wait-timeout만 사용한다. 실패 시 자동 rollback이나 새 release 발행은 하지 않는다.
    """
    if not isinstance(wait_timeout, int) or isinstance(wait_timeout, bool) or wait_timeout <= 0:
        raise ServiceReleaseError("wait_timeout은 양의 정수여야 합니다")
    if not manifests or plan.get("service") not in manifests:
        raise ServiceReleaseError("고정한 현재 release manifest가 필요합니다")
    try:
        selected = manifests[plan["service"]]["groups"][plan["group"]]
        runtime = selected["runtime_services"]
        project, working_dir = selected["project"], selected["working_dir"]
        if (not runtime or plan["project"] != project
                or plan["runtime_services"] != runtime):
            raise ServiceReleaseError("선택한 runtime과 현재 release가 다릅니다")
        argv = plan["argv"]
        allowed = {"--project-name", "--project-directory", "--env-file", "-f"}
        if (not isinstance(argv, list) or argv[:2] != ["docker", "compose"]
                or len(argv) % 2 or any(not isinstance(value, str) for value in argv)
                or any(argv[index] not in allowed for index in range(2, len(argv), 2))):
            raise ServiceReleaseError("공식 Compose 인자 형식이 올바르지 않습니다")
        expected: dict[str, str] = {}
        for manifest in manifests.values():
            if any(group["project"] != project or group["working_dir"] != working_dir
                   for group in manifest["groups"].values()):
                raise ServiceReleaseError("같은 Compose project의 manifest만 사용할 수 있습니다")
            for name, image in manifest["images"].items():
                if name in expected:
                    raise ServiceReleaseError("소유 이미지 역할이 중복되었습니다")
                expected[name] = image["id"]
    except (KeyError, TypeError) as exc:
        raise ServiceReleaseError("고정한 Compose 계획 또는 manifest가 올바르지 않습니다") from exc
    before = _verify(manifests, client)
    if any(value.get("result") != "healthy" for value in before.values()):
        raise ServiceReleaseError("현재 release가 healthy여야 재생성할 수 있습니다")
    try:
        secrets = _secrets(manifests)
    except (OSError, UnicodeError):
        secrets = None
    environment = _environment()
    result: dict[str, Any] = {"service": plan["service"], "group": plan["group"],
                              "project": project, "success": False, "result": "failed"}
    try:
        configured = subprocess.run([*argv, "config", "--format", "json"],
                                    cwd=working_dir, env=environment, text=True,
                                    capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.update(error_type=type(exc).__name__,
                      stderr=_detail(getattr(exc, "stderr", None) or str(exc), secrets))
        return result
    if configured.returncode:
        result.update(returncode=configured.returncode, stderr=_detail(configured.stderr, secrets))
        return result
    try:
        resolved = json.loads(configured.stdout)
        if not isinstance(resolved, dict) or not isinstance(resolved.get("services"), dict):
            raise ValueError
        if secrets is not None:
            secrets.extend(_resolved_secrets(resolved))
        for name, expected_id in expected.items():
            reference = resolved["services"][name]["image"]
            if not isinstance(reference, str) or client.images.get(reference).attrs["Id"] != expected_id:
                raise ServiceReleaseError("resolved 소유 이미지가 현재 release와 다릅니다: " + name)
    except ServiceReleaseError:
        raise
    except Exception as exc:
        raise ServiceReleaseError("resolved Compose의 소유 이미지 ID를 확인할 수 없습니다") from exc
    result["images"] = expected
    if not execute:
        result.update(success=True, result="resolved")
        return result
    # 검증된 tag도 ID로 고정해 resolve 뒤 tag 이동이 재생성 이미지를 바꾸지 못하게 한다.
    materialized = deepcopy(resolved)
    for name, image_id in expected.items():
        materialized["services"][name]["image"] = image_id
    command = ["docker", "compose", "--project-name", project,
               "--project-directory", working_dir, "--env-file", "/dev/null", "-f", "-",
               "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate",
               "--wait", "--wait-timeout", str(wait_timeout), *runtime]
    try:
        completed = subprocess.run(
            command, cwd=working_dir, env=environment, text=True, capture_output=True, check=False,
            input=json.dumps(_escape_materialized_compose_environment_values(materialized),
                             ensure_ascii=False, separators=(",", ":")),
        )
        result["returncode"] = completed.returncode
        if completed.returncode:
            result["stderr"] = _detail(completed.stderr, secrets)
    except OSError as exc:
        result.update(error_type=type(exc).__name__, stderr=_detail(str(exc), secrets))
    after = _verify(manifests, client)
    result["post_verification"] = after
    if result.get("returncode") == 0:
        healthy = all(value.get("result") == "healthy" for value in after.values())
        result.update(success=healthy, result="healthy" if healthy else "drift")
    return result
