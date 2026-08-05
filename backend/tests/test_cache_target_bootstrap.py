from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values

import kor_travel_docker_manager.services.compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_bootstrap import (
    DEFAULT_OFF_BOOTSTRAP_ENV_NAMES,
    PINVI_CACHE_API_BASE_URL_ENV,
    prepare_default_off_cache_target_bootstrap,
)
from kor_travel_docker_manager.services.cache_target_contract import (
    PINVI_CONTRACT_GENERATION_ENV,
    PINVI_OPENAPI_SHA_ENV,
    PINVI_SOURCE_REVISION_ENV,
    PINVI_SYNC_ENV,
    load_cache_target_runtime_contract,
)
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
)
from kor_travel_docker_manager.services.compose_service import ComposeService

_BASE_URL = "http://127.0.0.1:12701"
_OPENAPI_SHA = "a" * 64
_SOURCE_REVISION = "b" * 40


def _tokens() -> Iterator[str]:
    yield from (
        "command-token-000000000000000000000000000001",
        "consumer-token-000000000000000000000000000002",
        "restore-fence-token-000000000000000000000003",
        "recovery-token-000000000000000000000000000004",
    )


def _raw_env(*extra: str) -> bytes:
    return (
        "KTDM_DEPLOYMENT_ENVIRONMENT=production\n"
        f"PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL={_BASE_URL}\n"
        + "".join(f"{line}\n" for line in extra)
    ).encode()


def test_bootstrap_creates_exact_default_off_four_role_contract() -> None:
    tokens = _tokens()
    bootstrap = prepare_default_off_cache_target_bootstrap(
        _raw_env(),
        base_url=_BASE_URL,
        expected_openapi_sha256=_OPENAPI_SHA,
        expected_source_revision=_SOURCE_REVISION,
        expected_contract_generation="7",
        token_factory=lambda: next(tokens),
    )

    values = {
        key: value
        for key, value in dotenv_values(
            stream=StringIO(bootstrap.replacement.decode())
        ).items()
        if isinstance(key, str) and isinstance(value, str)
    }
    contract = load_cache_target_runtime_contract(values, require_nonempty=True)

    assert contract is not None
    assert contract == bootstrap.contract
    assert contract.sync_enabled == "false"
    assert values[PINVI_CACHE_API_BASE_URL_ENV] == _BASE_URL
    assert values[PINVI_OPENAPI_SHA_ENV] == _OPENAPI_SHA
    assert values[PINVI_SOURCE_REVISION_ENV] == _SOURCE_REVISION
    assert values[PINVI_CONTRACT_GENERATION_ENV] == "7"
    assert len(contract.role_binding_sha256) == 64


@pytest.mark.parametrize(
    "partial_declaration",
    [
        f"{PINVI_SYNC_ENV}=false",
        f"export {PINVI_SYNC_ENV}=false",
        f"export {PINVI_CACHE_API_BASE_URL_ENV}",
    ],
)
def test_bootstrap_rejects_partial_state_without_generating_tokens(
    partial_declaration: str,
) -> None:
    generated = False

    def token_factory() -> str:
        nonlocal generated
        generated = True
        return "x" * 32

    with pytest.raises(DeploymentContractError, match="wholly unconfigured"):
        prepare_default_off_cache_target_bootstrap(
            _raw_env(partial_declaration),
            base_url=_BASE_URL,
            expected_openapi_sha256=_OPENAPI_SHA,
            expected_source_revision=_SOURCE_REVISION,
            expected_contract_generation="7",
            token_factory=token_factory,
        )

    assert generated is False


def test_bootstrap_rejects_foreign_admin_base_url() -> None:
    with pytest.raises(DeploymentContractError, match="admin base URL differs"):
        prepare_default_off_cache_target_bootstrap(
            _raw_env().replace(_BASE_URL.encode(), b"http://127.0.0.1:1"),
            base_url=_BASE_URL,
            expected_openapi_sha256=_OPENAPI_SHA,
            expected_source_revision=_SOURCE_REVISION,
            expected_contract_generation="7",
            token_factory=lambda: "x" * 32,
        )


def test_compose_service_bootstrap_only_replaces_canonical_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = _tokens()
    bootstrap = prepare_default_off_cache_target_bootstrap(
        _raw_env(),
        base_url=_BASE_URL,
        expected_openapi_sha256=CACHE_TARGET_PRODUCTION_PINS.service_openapi_sha256,
        expected_source_revision=(
            CACHE_TARGET_PRODUCTION_PINS.map_functional_owner_revision
        ),
        expected_contract_generation=CACHE_TARGET_PRODUCTION_PINS.contract_generation,
        token_factory=lambda: next(tokens),
    )
    transaction = SimpleNamespace(
        environment=SimpleNamespace(
            effective={"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
            env_file_bytes=_raw_env(),
            env_path="/canonical/.env",
            env_file_identity=SimpleNamespace(uid=1000, gid=1000),
        )
    )
    replacements: list[tuple[str, str, bytes, int, int]] = []

    @contextmanager
    def lock():
        yield SimpleNamespace(lock_path="/lock")

    for name in DEFAULT_OFF_BOOTSTRAP_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        compose_service_module, "c6c_deployment_lock_from_environment", lock
    )
    monkeypatch.setattr(
        ComposeService,
        "_capture_transaction_unlocked",
        lambda _self: (transaction, None),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_assert_transaction_matches_c6c_lock",
        lambda _transaction, _lock: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "assert_manager_mutation_allowed",
        lambda *, environment: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "load_c6c_deployment_config_from_environment",
        lambda _environment: SimpleNamespace(
            production=True, cache_target=None, base_url=_BASE_URL
        ),
    )
    monkeypatch.setattr(
        compose_service_module,
        "prepare_default_off_cache_target_bootstrap",
        lambda *_args, **_kwargs: bootstrap,
    )
    monkeypatch.setattr(
        compose_service_module,
        "replace_canonical_env_file",
        lambda path, *, expected_sha256, replacement, expected_owner_uid, expected_owner_gid: replacements.append(
            (
                str(path),
                expected_sha256,
                replacement,
                expected_owner_uid,
                expected_owner_gid,
            )
        ),
    )

    result = ComposeService().bootstrap_cache_target_default_off()

    assert replacements == [
        (
            "/canonical/.env",
            hashlib.sha256(_raw_env()).hexdigest(),
            bootstrap.replacement,
            1000,
            1000,
        )
    ]
    assert result["sync_enabled"] == "false"
    rendered_result = json.dumps(result, sort_keys=True)
    assert all(value not in rendered_result for value in bootstrap.contract.protected_values)
