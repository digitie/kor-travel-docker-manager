from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from io import StringIO

from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_contract import (
    MAP_REGISTRY_ENV,
    PROTECTED_ENV_NAMES,
    CacheTargetRuntimeContract,
    create_default_off_cache_target_runtime_contract,
)

PINVI_ADMIN_BASE_URL_ENV = "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL"
PINVI_CACHE_API_BASE_URL_ENV = "PINVI_KOR_TRAVEL_MAP_API_BASE_URL"
DEFAULT_OFF_BOOTSTRAP_ENV_NAMES = frozenset(
    {PINVI_CACHE_API_BASE_URL_ENV, *PROTECTED_ENV_NAMES}
)


@dataclass(frozen=True, repr=False)
class DefaultOffCacheTargetBootstrap:
    replacement: bytes = field(repr=False)
    contract: CacheTargetRuntimeContract = field(repr=False)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def prepare_default_off_cache_target_bootstrap(
    raw: bytes,
    *,
    base_url: str,
    expected_openapi_sha256: str,
    expected_source_revision: str,
    expected_contract_generation: str,
    token_factory: Callable[[], str] = _new_token,
) -> DefaultOffCacheTargetBootstrap:
    """완전 미구성 canonical env에만 default-off contract를 append한다.

    partial 구성을 자동 보정하지 않는다. 이를 통해 재시도·외부 편집과 기존
    credential을 새 token으로 덮어쓰는 사고를 모두 mutation 전에 차단한다.
    """

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentContractError("canonical env must be UTF-8") from exc
    if not text:
        raise DeploymentContractError("canonical env is empty")
    lines = text.splitlines(keepends=True)
    values = {
        key: value
        for key, value in dotenv_values(
            stream=StringIO(text), interpolate=False
        ).items()
        if isinstance(key, str)
    }
    admin_base_url = _read_single_env_value(
        lines, values, PINVI_ADMIN_BASE_URL_ENV
    )
    if admin_base_url != base_url:
        raise DeploymentContractError(
            "canonical env PinVi Map admin base URL differs from the frozen contract"
        )
    if DEFAULT_OFF_BOOTSTRAP_ENV_NAMES.intersection(values):
        raise DeploymentContractError(
            "cache-target default-off bootstrap requires a wholly unconfigured env"
        )

    contract = create_default_off_cache_target_runtime_contract(
        expected_openapi_sha256=expected_openapi_sha256,
        expected_source_revision=expected_source_revision,
        expected_contract_generation=expected_contract_generation,
        token_factory=token_factory,
    )
    additions = {
        PINVI_CACHE_API_BASE_URL_ENV: base_url,
        MAP_REGISTRY_ENV: contract.registry_json,
        **contract.ordinary_environment,
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RESTORE_FENCE_TOKEN": (
            contract.restore_fence_token
        ),
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RECOVERY_TOKEN": contract.recovery_token,
    }
    rendered = text if text.endswith(("\n", "\r")) else f"{text}\n"
    replacement = (
        rendered + "".join(f"{key}={value}\n" for key, value in additions.items())
    ).encode()
    if hashlib.sha256(replacement).digest() == hashlib.sha256(raw).digest():
        raise DeploymentContractError("cache-target default-off bootstrap made no env change")
    return DefaultOffCacheTargetBootstrap(replacement=replacement, contract=contract)


def _read_single_env_value(
    lines: list[str],
    values: dict[str, str | None],
    env_name: str,
) -> str:
    matches = _find_env_lines(lines, env_name)
    if len(matches) != 1:
        raise DeploymentContractError(f"canonical env must contain exactly one {env_name}")
    value = values.get(env_name)
    if not isinstance(value, str) or not value:
        raise DeploymentContractError(f"canonical env {env_name} is empty")
    return value


def _find_env_lines(lines: list[str], env_name: str) -> list[tuple[int, str]]:
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(env_name)}\s*(?:=|$)")
    return [
        (index, line)
        for index, line in enumerate(lines)
        if pattern.match(line)
    ]
