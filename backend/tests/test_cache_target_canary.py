from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from kor_travel_docker_manager.services import (
    cache_target_canary as cache_target_canary_module,
)
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_canary import (
    execute_cache_target_causal_canary,
    parse_cache_target_causal_canary_receipt,
)

_RUN_ID = "11111111-1111-4111-8111-111111111111"


def _receipt(**overrides: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "status": "succeeded",
        "run_id": _RUN_ID,
        "target_poi_id": "15f98050-27d7-5f85-be21-dc53eded5d7d",
        "put_command_id": "33333333-3333-4333-8333-333333333333",
        "delete_command_id": "44444444-4444-4444-8444-444444444444",
        "put_event_id": "55555555-5555-4555-8555-555555555555",
        "delete_event_id": "66666666-6666-4666-8666-666666666666",
        "put_generation": 7,
        "delete_generation": 8,
        "put_relay_order": 11,
        "delete_relay_order": 12,
        "baseline_cache_generation": 20,
        "put_cache_generation": 21,
        "final_cache_generation": 22,
        "pending_commands": 0,
        "leased_commands": 0,
        "dead_letter_commands": 0,
        "local_applied_cursor": "cursor-v1:12",
        "remote_acked_cursor": "cursor-v1:12",
        "local_count": 4,
        "remote_count": 4,
        "local_merkle_root": "a" * 64,
        "remote_merkle_root": "a" * 64,
    }
    receipt.update(overrides)
    return receipt


def _raw(**overrides: object) -> str:
    return json.dumps(
        _receipt(**overrides),
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def test_causal_canary_parser_accepts_exact_converged_receipt() -> None:
    assert parse_cache_target_causal_canary_receipt(_raw()) == _receipt()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"unexpected": "raw-payload"}, "schema"),
        ({"status": "failed"}, "succeed"),
        ({"run_id": "not-a-uuid"}, "UUID"),
        ({"put_command_id": _RUN_ID}, "identity"),
        ({"delete_generation": 9}, "convergence"),
        ({"pending_commands": True}, "integer"),
        ({"pending_commands": 1}, "convergence"),
        ({"remote_acked_cursor": "foreign"}, "convergence"),
        ({"remote_count": 5}, "convergence"),
        ({"remote_merkle_root": "b" * 64}, "convergence"),
        ({"local_applied_cursor": "https://secret.invalid"}, "cursor"),
    ],
)
def test_causal_canary_parser_rejects_extra_malformed_or_foreign_evidence(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(DeploymentContractError, match=message):
        parse_cache_target_causal_canary_receipt(_raw(**overrides))


def test_causal_canary_parser_rejects_multiple_stdout_lines() -> None:
    with pytest.raises(DeploymentContractError, match="stdout"):
        parse_cache_target_causal_canary_receipt(_raw() + _raw())


def test_causal_canary_docker_exec_is_injectable_and_returns_only_parsed_receipt() -> None:
    commands: list[Sequence[str]] = []

    def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=_raw(), stderr="")

    result = execute_cache_target_causal_canary(
        container_name="kor-travel-test-pinvi-api-1",
        run_id=_RUN_ID,
        docker_exec=run,
    )

    assert result == _receipt()
    assert commands == [
        (
            "docker",
            "exec",
            "kor-travel-test-pinvi-api-1",
            "pinvi-cache-target-causal-canary",
            "--run-id",
            _RUN_ID,
            "--timeout-seconds",
            "180",
        )
    ]


def test_causal_canary_docker_exec_does_not_echo_raw_failure() -> None:
    raw_secret = "raw-secret-that-must-not-escape"

    def fail(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=raw_secret,
            stderr=raw_secret,
        )

    with pytest.raises(DeploymentContractError) as raised:
        execute_cache_target_causal_canary(
            container_name="pinvi-api",
            run_id=_RUN_ID,
            docker_exec=fail,
        )

    assert raw_secret not in str(raised.value)


def test_causal_canary_real_docker_exec_has_bounded_redacted_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeout: list[int] = []

    def timeout(
        command: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        observed_timeout.append(int(kwargs["timeout"]))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="raw-secret")

    monkeypatch.setattr(cache_target_canary_module.subprocess, "run", timeout)

    with pytest.raises(DeploymentContractError) as raised:
        execute_cache_target_causal_canary(
            container_name="pinvi-api",
            run_id=_RUN_ID,
            timeout_seconds=120,
        )

    assert observed_timeout == [150]
    assert "raw-secret" not in str(raised.value)
    assert raised.value.__cause__ is None
