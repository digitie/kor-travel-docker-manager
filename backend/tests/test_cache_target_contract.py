from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

import pytest

from kor_travel_docker_manager.services.cache_target_contract import (
    MAP_REGISTRY_ENV,
    PINVI_COMMAND_TOKEN_ENV,
    PINVI_CONSUMER_ID_ENV,
    PINVI_CONSUMER_TOKEN_ENV,
    PINVI_CONTRACT_GENERATION_ENV,
    PINVI_OPENAPI_SHA_ENV,
    PINVI_RECOVERY_TOKEN_ENV,
    PINVI_RESTORE_FENCE_TOKEN_ENV,
    PINVI_SOURCE_REVISION_ENV,
    PINVI_SYNC_ENV,
    load_cache_target_runtime_contract,
)

_TOKENS = {
    "command": "command-role-token-00000000000000000001",
    "consumer": "consumer-role-token-0000000000000000002",
    "restore-fence": "restore-fence-token-000000000000000003",
    "recovery": "recovery-role-token-0000000000000000004",
}
_SCOPES = {
    "command": ["cache-target:command"],
    "consumer": [
        "cache-target:read",
        "cache-target:claim",
        "cache-target:ack",
        "cache-target:nack",
        "cache-target:snapshot",
    ],
    "restore-fence": ["cache-target:restore-fence"],
    "recovery": ["cache-target:recovery"],
}


def _environment() -> dict[str, str]:
    consumer_id = "pinvi-cache-target-consumer"
    registry = [
        {
            "principal_id": f"pinvi-cache-target-{role}",
            "consumer_id": consumer_id,
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "scopes": scopes,
            "external_systems": ["pinvi"],
        }
        for role, token in _TOKENS.items()
        for scopes in (_SCOPES[role],)
    ]
    return {
        MAP_REGISTRY_ENV: json.dumps(registry, separators=(",", ":")),
        PINVI_SYNC_ENV: "false",
        PINVI_COMMAND_TOKEN_ENV: _TOKENS["command"],
        PINVI_CONSUMER_TOKEN_ENV: _TOKENS["consumer"],
        PINVI_RESTORE_FENCE_TOKEN_ENV: _TOKENS["restore-fence"],
        PINVI_RECOVERY_TOKEN_ENV: _TOKENS["recovery"],
        PINVI_CONSUMER_ID_ENV: consumer_id,
        PINVI_OPENAPI_SHA_ENV: "a" * 64,
        PINVI_SOURCE_REVISION_ENV: "b" * 40,
        PINVI_CONTRACT_GENERATION_ENV: "7",
    }


def test_load_cache_target_runtime_contract_accepts_exact_four_role_binding() -> None:
    contract = load_cache_target_runtime_contract(
        _environment(), require_nonempty=True, legacy_tokens=("legacy-token-" + "x" * 32,)
    )

    assert contract is not None
    assert contract.consumer_id == "pinvi-cache-target-consumer"
    assert len(contract.role_binding_sha256) == 64
    assert all(token not in contract.role_binding_sha256 for token in _TOKENS.values())
    assert all(
        hashlib.sha256(token.encode()).hexdigest() in contract.protected_values
        for token in _TOKENS.values()
    )
    assert set(contract.ordinary_environment) == {
        PINVI_SYNC_ENV,
        PINVI_COMMAND_TOKEN_ENV,
        PINVI_CONSUMER_TOKEN_ENV,
        PINVI_CONSUMER_ID_ENV,
        PINVI_OPENAPI_SHA_ENV,
        PINVI_SOURCE_REVISION_ENV,
        PINVI_CONTRACT_GENERATION_ENV,
    }


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda env: env.update({PINVI_CONSUMER_ID_ENV: "pinvi"}), "must be exactly"),
        (
            lambda env: env.update({PINVI_RECOVERY_TOKEN_ENV: _TOKENS["command"]}),
            "must be distinct",
        ),
        (
            lambda env: env.update({PINVI_COMMAND_TOKEN_ENV: "short"}),
            r"32\+ chars",
        ),
    ],
)
def test_load_cache_target_runtime_contract_rejects_invalid_role_values(
    mutation: Callable[[dict[str, str]], None], match: str
) -> None:
    environment = _environment()
    mutation(environment)

    with pytest.raises(ValueError, match=match):
        load_cache_target_runtime_contract(environment, require_nonempty=True)


@pytest.mark.parametrize("fault", ["extra", "scope", "system", "digest"])
def test_load_cache_target_runtime_contract_rejects_non_exact_registry(
    fault: str,
) -> None:
    environment = _environment()
    registry = json.loads(environment[MAP_REGISTRY_ENV])
    if fault == "extra":
        registry.append(dict(registry[0], principal_id="extra"))
    elif fault == "scope":
        registry[0]["scopes"].append("cache-target:read")
    elif fault == "system":
        registry[0]["external_systems"].append("other")
    else:
        registry[0]["token_sha256"] = "0" * 64
    environment[MAP_REGISTRY_ENV] = json.dumps(registry)

    with pytest.raises(ValueError):
        load_cache_target_runtime_contract(environment, require_nonempty=True)


def test_local_unconfigured_cache_target_contract_is_absent() -> None:
    assert (
        load_cache_target_runtime_contract(
            {
                MAP_REGISTRY_ENV: "[]",
                PINVI_SYNC_ENV: "false",
                PINVI_CONSUMER_ID_ENV: "pinvi-cache-target-consumer",
            },
            require_nonempty=False,
        )
        is None
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (PINVI_SYNC_ENV, "true"),
        (PINVI_SYNC_ENV, "invalid"),
        (PINVI_SYNC_ENV, " TRUE "),
        (PINVI_SYNC_ENV, "False"),
        (PINVI_CONSUMER_ID_ENV, "foreign-consumer"),
    ],
)
def test_local_partial_enable_or_foreign_consumer_fails_closed(
    name: str, value: str
) -> None:
    environment = {
        MAP_REGISTRY_ENV: "[]",
        PINVI_SYNC_ENV: "false",
        PINVI_CONSUMER_ID_ENV: "pinvi-cache-target-consumer",
    }
    environment[name] = value

    with pytest.raises(ValueError):
        load_cache_target_runtime_contract(environment, require_nonempty=False)
