from __future__ import annotations

import json
import re
import subprocess
import uuid
from collections.abc import Callable, Sequence

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

_UUID_FIELDS = frozenset(
    {
        "run_id",
        "target_poi_id",
        "put_command_id",
        "delete_command_id",
        "put_event_id",
        "delete_event_id",
    }
)
_INTEGER_FIELDS = frozenset(
    {
        "put_generation",
        "delete_generation",
        "put_relay_order",
        "delete_relay_order",
        "baseline_cache_generation",
        "put_cache_generation",
        "final_cache_generation",
        "pending_commands",
        "leased_commands",
        "dead_letter_commands",
        "local_count",
        "remote_count",
    }
)
_CURSOR_FIELDS = frozenset({"local_applied_cursor", "remote_acked_cursor"})
_ROOT_FIELDS = frozenset({"local_merkle_root", "remote_merkle_root"})
_RECEIPT_FIELDS = frozenset({"status"}) | _UUID_FIELDS | _INTEGER_FIELDS | _CURSOR_FIELDS | _ROOT_FIELDS
_CONTAINER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_STABLE_TARGET_POI_ID = "15f98050-27d7-5f85-be21-dc53eded5d7d"


def parse_cache_target_causal_canary_receipt(raw: str) -> dict[str, int | str]:
    """PinVi stdout의 secret-free 단일 JSON receipt만 exact schema로 수용한다."""

    encoded = raw.encode("utf-8")
    if not encoded or len(encoded) > 65_536 or raw.count("\n") != 1 or not raw.endswith("\n"):
        raise DeploymentContractError("cache-target causal canary stdout is invalid")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeploymentContractError(
            "cache-target causal canary receipt is invalid"
        ) from exc
    if not isinstance(document, dict) or set(document) != _RECEIPT_FIELDS:
        raise DeploymentContractError("cache-target causal canary receipt schema is invalid")
    if document["status"] != "succeeded":
        raise DeploymentContractError("cache-target causal canary did not succeed")
    for field in _UUID_FIELDS:
        value = document[field]
        if not isinstance(value, str):
            raise DeploymentContractError("cache-target causal canary UUID is invalid")
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError
        except ValueError as exc:
            raise DeploymentContractError(
                "cache-target causal canary UUID is invalid"
            ) from exc
    if (
        document["target_poi_id"] != _STABLE_TARGET_POI_ID
        or len({str(document[field]) for field in _UUID_FIELDS})
        != len(_UUID_FIELDS)
    ):
        raise DeploymentContractError(
            "cache-target causal canary UUID identity is invalid"
        )
    if any(type(document[field]) is not int for field in _INTEGER_FIELDS):
        raise DeploymentContractError("cache-target causal canary integer is invalid")
    if any(int(document[field]) < 0 for field in _INTEGER_FIELDS):
        raise DeploymentContractError("cache-target causal canary integer is invalid")
    for field in _CURSOR_FIELDS:
        value = document[field]
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or any(character.isspace() for character in value)
            or "://" in value
        ):
            raise DeploymentContractError("cache-target causal canary cursor is invalid")
    if any(
        not isinstance(document[field], str)
        or _SHA256_PATTERN.fullmatch(str(document[field])) is None
        for field in _ROOT_FIELDS
    ):
        raise DeploymentContractError("cache-target causal canary Merkle root is invalid")
    if (
        int(document["put_generation"]) <= 0
        or int(document["delete_generation"])
        != int(document["put_generation"]) + 1
        or int(document["put_relay_order"]) <= 0
        or int(document["put_relay_order"]) >= int(document["delete_relay_order"])
        or int(document["baseline_cache_generation"])
        >= int(document["put_cache_generation"])
        or int(document["put_cache_generation"])
        >= int(document["final_cache_generation"])
        or any(
            int(document[field]) != 0
            for field in (
                "pending_commands",
                "leased_commands",
                "dead_letter_commands",
            )
        )
        or document["local_applied_cursor"] != document["remote_acked_cursor"]
        or document["local_count"] != document["remote_count"]
        or document["local_merkle_root"] != document["remote_merkle_root"]
    ):
        raise DeploymentContractError(
            "cache-target causal canary convergence evidence is invalid"
        )
    return {str(key): value for key, value in document.items()}


def execute_cache_target_causal_canary(
    *,
    container_name: str,
    run_id: str,
    timeout_seconds: int = 180,
    docker_exec: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, int | str]:
    """running ordinary PinVi API container의 canary를 raw 비보존 경계로 실행한다."""

    try:
        canonical_run_id = str(uuid.UUID(run_id))
    except ValueError as exc:
        raise DeploymentContractError("cache-target causal canary run ID is invalid") from exc
    if canonical_run_id != run_id:
        raise DeploymentContractError("cache-target causal canary run ID is invalid")
    if _CONTAINER_PATTERN.fullmatch(container_name) is None:
        raise DeploymentContractError("cache-target causal canary container is invalid")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise DeploymentContractError("cache-target causal canary timeout is invalid")
    command = (
        "docker",
        "exec",
        container_name,
        "pinvi-cache-target-causal-canary",
        "--run-id",
        run_id,
        "--timeout-seconds",
        str(timeout_seconds),
    )
    try:
        completed = (
            docker_exec(command)
            if docker_exec is not None
            else subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds + 30,
            )
        )
    except subprocess.TimeoutExpired:
        raise DeploymentContractError(
            "cache-target causal canary docker exec timed out"
        ) from None
    except OSError as exc:
        raise DeploymentContractError(
            "cache-target causal canary docker exec failed"
        ) from exc
    if completed.returncode != 0 or completed.stderr:
        raise DeploymentContractError("cache-target causal canary docker exec failed")
    return parse_cache_target_causal_canary_receipt(completed.stdout)
