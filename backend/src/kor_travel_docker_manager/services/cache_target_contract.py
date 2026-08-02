from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

MAP_REGISTRY_ENV = "KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS"
PINVI_SYNC_ENV = "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED"
PINVI_COMMAND_TOKEN_ENV = "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_COMMAND_TOKEN"
PINVI_CONSUMER_TOKEN_ENV = "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_TOKEN"
PINVI_CONSUMER_ID_ENV = "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_ID"
PINVI_OPENAPI_SHA_ENV = "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_OPENAPI_SHA256"
PINVI_SOURCE_REVISION_ENV = (
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_SOURCE_REVISION"
)
PINVI_CONTRACT_GENERATION_ENV = (
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_CONTRACT_GENERATION"
)
PINVI_RESTORE_FENCE_TOKEN_ENV = (
    "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RESTORE_FENCE_TOKEN"
)
PINVI_RECOVERY_TOKEN_ENV = "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RECOVERY_TOKEN"

PINVI_ORDINARY_ENV_NAMES = frozenset(
    {
        PINVI_SYNC_ENV,
        PINVI_COMMAND_TOKEN_ENV,
        PINVI_CONSUMER_TOKEN_ENV,
        PINVI_CONSUMER_ID_ENV,
        PINVI_OPENAPI_SHA_ENV,
        PINVI_SOURCE_REVISION_ENV,
        PINVI_CONTRACT_GENERATION_ENV,
    }
)
MANAGER_ONLY_TOKEN_ENV_NAMES = frozenset(
    {PINVI_RESTORE_FENCE_TOKEN_ENV, PINVI_RECOVERY_TOKEN_ENV}
)
PROTECTED_ENV_NAMES = frozenset(
    {MAP_REGISTRY_ENV, *PINVI_ORDINARY_ENV_NAMES, *MANAGER_ONLY_TOKEN_ENV_NAMES}
)

_TOKEN_ENV_BY_ROLE = {
    "command": PINVI_COMMAND_TOKEN_ENV,
    "consumer": PINVI_CONSUMER_TOKEN_ENV,
    "restore-fence": PINVI_RESTORE_FENCE_TOKEN_ENV,
    "recovery": PINVI_RECOVERY_TOKEN_ENV,
}
_SCOPES_BY_ROLE = {
    "command": frozenset({"cache-target:command"}),
    "consumer": frozenset(
        {
            "cache-target:read",
            "cache-target:claim",
            "cache-target:ack",
            "cache-target:nack",
            "cache-target:snapshot",
        }
    ),
    "restore-fence": frozenset({"cache-target:restore-fence"}),
    "recovery": frozenset(
        {"cache-target:recovery", "cache-target:recovery-replay"}
    ),
}
_PRINCIPAL_KEYS = frozenset(
    {"principal_id", "consumer_id", "token_sha256", "scopes", "external_systems"}
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_GENERATION_PATTERN = re.compile(r"^[1-9][0-9]*$")


@dataclass(frozen=True)
class CacheTargetRuntimeContract:
    registry_json: str = field(repr=False)
    sync_enabled: str
    command_token: str = field(repr=False)
    consumer_token: str = field(repr=False)
    restore_fence_token: str = field(repr=False)
    recovery_token: str = field(repr=False)
    consumer_id: str
    expected_openapi_sha256: str
    expected_source_revision: str
    expected_contract_generation: str
    role_binding_sha256: str

    @property
    def ordinary_environment(self) -> dict[str, str]:
        return {
            PINVI_SYNC_ENV: self.sync_enabled,
            PINVI_COMMAND_TOKEN_ENV: self.command_token,
            PINVI_CONSUMER_TOKEN_ENV: self.consumer_token,
            PINVI_CONSUMER_ID_ENV: self.consumer_id,
            PINVI_OPENAPI_SHA_ENV: self.expected_openapi_sha256,
            PINVI_SOURCE_REVISION_ENV: self.expected_source_revision,
            PINVI_CONTRACT_GENERATION_ENV: self.expected_contract_generation,
        }

    @property
    def protected_values(self) -> tuple[str, ...]:
        return (
            self.registry_json,
            self.command_token,
            self.consumer_token,
            self.restore_fence_token,
            self.recovery_token,
            *(hashlib.sha256(token.encode()).hexdigest() for token in self.role_tokens),
        )

    @property
    def role_tokens(self) -> tuple[str, str, str, str]:
        return (
            self.command_token,
            self.consumer_token,
            self.restore_fence_token,
            self.recovery_token,
        )


def load_cache_target_runtime_contract(
    environment: Mapping[str, str],
    *,
    require_nonempty: bool,
    legacy_tokens: Iterable[str] = (),
    error_type: type[ValueError] = ValueError,
) -> CacheTargetRuntimeContract | None:
    values = {name: environment.get(name, "") for name in PROTECTED_ENV_NAMES}
    configured_values = (
        values[MAP_REGISTRY_ENV] not in {"", "[]"}
        or values[PINVI_SYNC_ENV] not in {"", "false"}
        or values[PINVI_CONSUMER_ID_ENV]
        not in {"", "pinvi-cache-target-consumer"}
        or any(values[name] for name in _TOKEN_ENV_BY_ROLE.values())
        or any(
            values[name]
            for name in (
                PINVI_OPENAPI_SHA_ENV,
                PINVI_SOURCE_REVISION_ENV,
                PINVI_CONTRACT_GENERATION_ENV,
            )
        )
    )
    if not require_nonempty and not configured_values:
        return None

    sync_enabled = values[PINVI_SYNC_ENV]
    if sync_enabled not in {"false", "true"}:
        raise error_type(f"{PINVI_SYNC_ENV} must be literal true or false")
    consumer_id = values[PINVI_CONSUMER_ID_ENV]
    if consumer_id != "pinvi-cache-target-consumer":
        raise error_type(
            f"{PINVI_CONSUMER_ID_ENV} must be exactly pinvi-cache-target-consumer"
        )

    tokens = {
        role: values[env_name]
        for role, env_name in _TOKEN_ENV_BY_ROLE.items()
    }
    for role, token in tokens.items():
        if len(token) < 32 or token != token.strip() or any(
            char.isspace() for char in token
        ):
            raise error_type(f"cache-target {role} token must be whitespace-free and 32+ chars")
    all_tokens = [*tokens.values(), *(token for token in legacy_tokens if token)]
    if len(set(all_tokens)) != len(all_tokens):
        raise error_type("cache-target role tokens and legacy credentials must be distinct")

    openapi_sha = values[PINVI_OPENAPI_SHA_ENV]
    source_revision = values[PINVI_SOURCE_REVISION_ENV]
    generation = values[PINVI_CONTRACT_GENERATION_ENV]
    if not _SHA256_PATTERN.fullmatch(openapi_sha):
        raise error_type(f"{PINVI_OPENAPI_SHA_ENV} must be lowercase SHA-256")
    if not _SOURCE_REVISION_PATTERN.fullmatch(source_revision):
        raise error_type(f"{PINVI_SOURCE_REVISION_ENV} must be lowercase git SHA-1")
    if not _GENERATION_PATTERN.fullmatch(generation):
        raise error_type(f"{PINVI_CONTRACT_GENERATION_ENV} must be a positive integer")

    registry_json = values[MAP_REGISTRY_ENV]
    bindings = _validate_registry(
        registry_json,
        tokens=tokens,
        consumer_id=consumer_id,
        error_type=error_type,
    )
    canonical = json.dumps(bindings, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return CacheTargetRuntimeContract(
        registry_json=registry_json,
        sync_enabled=sync_enabled,
        command_token=tokens["command"],
        consumer_token=tokens["consumer"],
        restore_fence_token=tokens["restore-fence"],
        recovery_token=tokens["recovery"],
        consumer_id=consumer_id,
        expected_openapi_sha256=openapi_sha,
        expected_source_revision=source_revision,
        expected_contract_generation=generation,
        role_binding_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def _validate_registry(
    registry_json: str,
    *,
    tokens: Mapping[str, str],
    consumer_id: str,
    error_type: type[ValueError],
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(registry_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise error_type(f"{MAP_REGISTRY_ENV} must be valid JSON") from exc
    if not isinstance(payload, list) or len(payload) != len(_SCOPES_BY_ROLE):
        raise error_type("cache-target registry must contain exactly four principals")

    role_bindings: dict[str, dict[str, Any]] = {}
    principal_ids: set[str] = set()
    for entry in payload:
        if not isinstance(entry, dict) or frozenset(entry) != _PRINCIPAL_KEYS:
            raise error_type("cache-target registry principal shape is invalid")
        scopes_value = entry["scopes"]
        systems_value = entry["external_systems"]
        if not isinstance(scopes_value, list) or len(scopes_value) != len(set(scopes_value)):
            raise error_type("cache-target registry scopes must be a unique list")
        scopes = frozenset(scopes_value)
        roles = [role for role, expected in _SCOPES_BY_ROLE.items() if scopes == expected]
        if len(roles) != 1:
            raise error_type("cache-target registry scopes are not an exact role binding")
        role = roles[0]
        if role in role_bindings:
            raise error_type("cache-target registry contains a duplicate role")
        principal_id = entry["principal_id"]
        if (
            not isinstance(principal_id, str)
            or not principal_id
            or principal_id != principal_id.strip()
            or principal_id in principal_ids
        ):
            raise error_type("cache-target principal IDs must be unique stable strings")
        principal_ids.add(principal_id)
        if entry["consumer_id"] != consumer_id:
            raise error_type("cache-target registry consumer ID binding is invalid")
        if systems_value != ["pinvi"]:
            raise error_type("cache-target registry external systems must be exactly pinvi")
        token_sha = entry["token_sha256"]
        expected_sha = hashlib.sha256(tokens[role].encode()).hexdigest()
        if (
            not isinstance(token_sha, str)
            or not _SHA256_PATTERN.fullmatch(token_sha)
            or not hmac.compare_digest(token_sha, expected_sha)
        ):
            raise error_type("cache-target registry token digest binding is invalid")
        role_bindings[role] = {
            "consumer_id": consumer_id,
            "external_systems": ["pinvi"],
            "principal_id": principal_id,
            "scopes": sorted(scopes),
            "token_sha256": token_sha,
        }
    if frozenset(role_bindings) != frozenset(_SCOPES_BY_ROLE):
        raise error_type("cache-target registry role set is incomplete")
    return [{"role": role, **role_bindings[role]} for role in sorted(role_bindings)]
