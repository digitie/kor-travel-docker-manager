from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import c6c_deployment as c6c
from kor_travel_docker_manager.services.c6c_deployment import (
    C6cCancelProbeFixture,
    C6cDeploymentConfig,
    DeploymentContractError,
    HttpProbeResponse,
    PinviCancelProbeState,
)


def _config() -> C6cDeploymentConfig:
    return cast(
        C6cDeploymentConfig,
        SimpleNamespace(
            smoke=SimpleNamespace(
                pinvi_api_base_url="http://pinvi.test",
                pinvi_admin_email="admin@example.test",
                pinvi_admin_password="test-password",
            ),
        ),
    )


def _outcome() -> dict[str, int | str]:
    return {
        "name": "pinvi_cancel_error",
        "status": 409,
        "code": "PIPELINE_CANCELLATION_UNSAFE",
    }


def _fixture(
    *,
    state: Literal["armed", "consumed", "finalized"],
    cancellation_id: str | None = None,
) -> C6cCancelProbeFixture:
    return C6cCancelProbeFixture(
        transaction_id="11111111-1111-1111-1111-111111111111",
        job_id="22222222-2222-2222-2222-222222222222",
        state=state,
        cancellation_id=cancellation_id,
        canonical_unsafe_outcome=None if state == "armed" else _outcome(),
        created_at="2026-08-06T00:00:00+00:00",
        consumed_at=("2026-08-06T00:01:00+00:00" if state != "armed" else None),
        finalized_at=("2026-08-06T00:02:00+00:00" if state == "finalized" else None),
    )


def _consumed_state(*, finalize_attempted: bool) -> PinviCancelProbeState:
    transaction_id = "11111111-1111-1111-1111-111111111111"
    fixture = _fixture(
        state="consumed",
        cancellation_id="33333333-3333-3333-3333-333333333333",
    )
    return PinviCancelProbeState(
        transaction_id=transaction_id,
        fixture=fixture,
        attempted=True,
        finalize_attempted=finalize_attempted,
        result=_outcome(),
    )


def test_map_dataset_identity_uses_exact_membership_triple() -> None:
    identity = {
        "provider_dataset_id": 41,
        "sync_scope": "external_system:pinvi",
        "operation_key": "kma_nowcast_refresh",
        "detail_url": (
            "/v1/ops/datasets/41?sync_scope=external_system%3Apinvi&"
            "operation_key=kma_nowcast_refresh"
        ),
    }

    assert c6c._validate_map_dataset_identity(identity)  # noqa: SLF001
    assert not c6c._validate_map_dataset_identity(  # noqa: SLF001
        {
            **identity,
            "detail_url": (
                "/v1/ops/datasets/detail?provider=kma&dataset_key=weather&"
                "sync_scope=external_system%3Apinvi"
            ),
        }
    )
    assert c6c._validate_map_dataset_identity(  # noqa: SLF001
        {
            "provider_dataset_id": 41,
            "sync_scope": "dataset_wide",
            "operation_key": None,
            "detail_url": "/v1/ops/datasets/41?sync_scope=dataset_wide",
        }
    )
    assert not c6c._validate_map_dataset_identity(  # noqa: SLF001
        {**identity, "operation_key": ""}
    )


def test_dataset_wide_execution_rejects_missing_membership_scope() -> None:
    execution_id = "11111111-1111-1111-1111-111111111111"
    member_id = "22222222-2222-2222-2222-222222222222"
    operation_key = "kma_nowcast_refresh"
    member = {
        "provider_dataset_id": 41,
        "provider": "kma",
        "dataset_key": "weather",
        "sync_scope": "dataset_wide",
        "operation_key": operation_key,
        "operation_member_id": member_id,
        "status": "queued",
    }
    execution = {
        "kind": "import_job",
        "id": execution_id,
        "status": "queued",
        "pair_status": "queued",
        "operation_member_id": member_id,
        "sync_scope": None,
        "operation_key": operation_key,
        "provider_datasets": [member],
        "providers": ["kma"],
        "dataset_keys": ["weather"],
        "created_at": "2026-08-11T00:00:00+00:00",
        "started_at": None,
        "finished_at": None,
        "dagster_run_id": None,
        "dagster_run_status": None,
        "trigger_kind": None,
        "operation_registry_version": None,
        "error_message": None,
        "detail_url": f"/v1/ops/pipeline/executions/import_job/{execution_id}",
        "projected_job": {
            "id": execution_id,
            "job_kind": "provider_sync",
            "status": "queued",
            "progress": 0,
            "current_stage": None,
            "error_message": None,
            "created_at": "2026-08-11T00:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "dagster_run_id": None,
            "dagster_run_status": None,
            "trigger_kind": None,
            "operation_registry_version": None,
            "depth": 0,
            "detail_url": f"/v1/ops/pipeline/executions/import_job/{execution_id}",
        },
        "cancellation": None,
    }

    assert not c6c._validate_dataset_execution(  # noqa: SLF001
        execution,
        provider="kma",
        dataset_key="weather",
        provider_dataset_id=41,
        sync_scope="dataset_wide",
        operation_key=operation_key,
        active=True,
    )
    # Catalog-only rows have no row operation key, but Map may still attach a
    # scope rollup for an execution that belongs to another operation in the
    # same dataset/scope.  Validate that rollup without collapsing the
    # execution's own operation identity.
    assert c6c._validate_dataset_execution(  # noqa: SLF001
        {**execution, "sync_scope": "dataset_wide"},
        provider="kma",
        dataset_key="weather",
        provider_dataset_id=41,
        sync_scope="dataset_wide",
        operation_key=None,
        active=True,
    )


def test_catalog_validator_accepts_unrefreshable_none_effect() -> None:
    catalog = {
        "feature_kind": "place",
        "provider_state_default_scope": "dataset_wide",
        "label": "Catalog only",
        "is_feature_load": True,
        "is_active": True,
        "is_refreshable": False,
        "scope_refresh": {
            "supported": False,
            "selector": "none",
            "effect": "none",
            "default_sync_scope": "dataset_wide",
            "allowed_sync_scopes": ["dataset_wide"],
            "reason": "이 dataset에는 실행 가능한 refresh runner가 없습니다.",
        },
        "preview": {
            "supported": False,
            "sources": [],
            "input_kind": "none",
            "default_max_items": 20,
            "max_items_limit": 100,
            "timeout_seconds": 5.0,
            "external_call_budget": 0,
        },
    }

    assert c6c._validate_dataset_catalog(catalog)  # noqa: SLF001


def test_catalog_validator_rejects_refreshable_none_effect() -> None:
    catalog = {
        "feature_kind": "place",
        "provider_state_default_scope": "dataset_wide",
        "label": "Catalog only",
        "is_feature_load": True,
        "is_active": True,
        "is_refreshable": True,
        "scope_refresh": {
            "supported": False,
            "selector": "none",
            "effect": "none",
            "default_sync_scope": "dataset_wide",
            "allowed_sync_scopes": ["dataset_wide"],
            "reason": "이 dataset에는 실행 가능한 refresh runner가 없습니다.",
        },
        "preview": {
            "supported": False,
            "sources": [],
            "input_kind": "none",
            "default_max_items": 20,
            "max_items_limit": 100,
            "timeout_seconds": 5.0,
            "external_call_budget": 0,
        },
    }

    assert not c6c._validate_dataset_catalog(catalog)  # noqa: SLF001


@pytest.mark.parametrize(
    ("is_active", "is_refreshable"),
    [(False, True), (True, False)],
)
def test_catalog_validator_rejects_refreshability_cross_field_mismatch(
    is_active: bool,
    is_refreshable: bool,
) -> None:
    catalog = {
        "feature_kind": "place",
        "provider_state_default_scope": "dataset_wide",
        "label": "Catalog only",
        "is_feature_load": True,
        "is_active": is_active,
        "is_refreshable": is_refreshable,
        "scope_refresh": {
            "supported": False,
            "selector": "none",
            "effect": "dataset_wide",
            "default_sync_scope": "dataset_wide",
            "allowed_sync_scopes": [],
            "reason": "이 dataset에는 실행 가능한 refresh runner가 없습니다.",
        },
        "preview": {
            "supported": False,
            "sources": [],
            "input_kind": "none",
            "default_max_items": 20,
            "max_items_limit": 100,
            "timeout_seconds": 5.0,
            "external_call_budget": 0,
        },
    }

    assert not c6c._validate_dataset_catalog(catalog)  # noqa: SLF001


@pytest.mark.parametrize(
    ("state", "created_at", "consumed_at", "finalized_at"),
    [
        (
            "consumed",
            "2026-08-06T00:01:00+00:00",
            "2026-08-06T00:00:00+00:00",
            None,
        ),
        (
            "finalized",
            "2026-08-06T00:00:00+00:00",
            "2026-08-06T00:02:00+00:00",
            "2026-08-06T00:01:00+00:00",
        ),
    ],
)
def test_cancel_fixture_parser_rejects_reversed_lifecycle_timestamps(
    state: Literal["consumed", "finalized"],
    created_at: str,
    consumed_at: str,
    finalized_at: str | None,
) -> None:
    transaction_id = "11111111-1111-1111-1111-111111111111"
    cancellation_id = "33333333-3333-3333-3333-333333333333"
    payload = {
        "data": {
            "fixture": {
                "transaction_id": transaction_id,
                "job_id": "22222222-2222-2222-2222-222222222222",
                "state": state,
                "cancellation_id": cancellation_id,
                "created_at": created_at,
                "consumed_at": consumed_at,
                "finalized_at": finalized_at,
                "canonical_unsafe_outcome": {
                    "http_status": 409,
                    "code": "PIPELINE_CANCELLATION_UNSAFE",
                    "root_job_id": "22222222-2222-2222-2222-222222222222",
                    "cancellation_id": cancellation_id,
                },
                "capability_generation": c6c.C6C_CANCEL_PROBE_CAPABILITY_GENERATION,
            }
        },
        "meta": {},
    }

    with pytest.raises(DeploymentContractError, match="timestamp order"):
        c6c._parse_c6c_cancel_probe_fixture(  # noqa: SLF001
            payload,
            expected_transaction_id=transaction_id,
        )


def _rehearsal_environment() -> dict[str, str]:
    return {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": "admin",
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": "pbkdf2_sha256$100000$salt$digest",
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET": "u" * 32,
        "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET": "p" * 32,
        "KOR_TRAVEL_MAP_API_SERVICE_TOKEN": "s" * 32,
        "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET": "g" * 32,
        "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN": (
            "manual-feature-create-rehearsal-token-0000"
        ),
        "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256": hashlib.sha256(
            b"manual-feature-create-rehearsal-token-0000"
        ).hexdigest(),
        "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY": "h" * 32,
        "PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN": "n" * 32,
        "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN": "m" * 32,
        "KTDM_C6C_MAP_UI_ADMIN_PASSWORD": "map-ui-password-1",
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "pinvi-password-1",
        "KTDM_C6C_CONTRACT_GENERATION": "c6c-v1",
    }


def test_rehearsal_loader_requires_production_like_fixture_capabilities() -> None:
    values = _rehearsal_environment()

    config = c6c.load_c6c_deployment_config_from_environment(values)

    assert config.deployment_environment == "rehearsal"
    assert config.pinvi_environment == "production"
    assert config.fixture_token == "f" * 32
    assert config.curation_snapshot_token == "n" * 32
    assert config.curation_cutover_mapping_token == "m" * 32


def test_rehearsal_loader_rejects_invalid_manual_feature_create_flag() -> None:
    values = _rehearsal_environment()
    values["KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED"] = "maybe"

    with pytest.raises(
        DeploymentContractError,
        match=(
            "KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED must be exactly "
            "true or false"
        ),
    ):
        c6c.load_c6c_deployment_config_from_environment(values)


@pytest.mark.parametrize(
    "geo_api_key",
    ["x", "x" * 31, "x" * 33, f"{'x' * 31}-", f"{'x' * 31}é"],
)
def test_rehearsal_loader_rejects_non_issued_geo_key_shape(
    geo_api_key: str,
) -> None:
    values = _rehearsal_environment()
    values["KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY"] = geo_api_key

    with pytest.raises(
        DeploymentContractError,
        match="KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY is invalid",
    ):
        c6c.load_c6c_deployment_config_from_environment(values)


def test_uncertain_cancel_post_is_never_reissued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_id = "11111111-1111-1111-1111-111111111111"
    state = PinviCancelProbeState(
        transaction_id=transaction_id,
        fixture=C6cCancelProbeFixture(
            transaction_id=transaction_id,
            job_id="22222222-2222-2222-2222-222222222222",
            state="armed",
            cancellation_id=None,
            canonical_unsafe_outcome=None,
            created_at="2026-08-06T00:00:00+00:00",
        ),
        attempted=True,
    )
    session_request = Mock()
    monkeypatch.setattr(c6c, "_ensure_c6c_cancel_probe_fixture", lambda *_args: state.fixture)
    monkeypatch.setattr(c6c, "_session_request", session_request)

    with pytest.raises(DeploymentContractError, match="cannot be repeated"):
        c6c.run_pinvi_canonical_smoke(
            _config(),
            cancel_probe_state=state,
        )

    session_request.assert_not_called()


def test_uncertain_finalize_post_is_never_reissued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _consumed_state(finalize_attempted=True)
    finalizer = Mock()
    responses = iter(
        (
            HttpProbeResponse(status=200, payload={}, set_cookie=True),
            HttpProbeResponse(status=200, payload={}),
            HttpProbeResponse(status=200, payload={}),
        )
    )
    monkeypatch.setattr(c6c, "_ensure_c6c_cancel_probe_fixture", lambda *_args: state.fixture)
    monkeypatch.setattr(c6c, "_finalize_c6c_cancel_probe_fixture", finalizer)
    monkeypatch.setattr(c6c, "_cookie_opener", lambda **_kwargs: object())
    monkeypatch.setattr(c6c, "_session_request", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(c6c, "_pinvi_envelope_ok", lambda _payload: True)
    monkeypatch.setattr(c6c, "_validate_pinvi_etl_summary", lambda _payload: True)
    monkeypatch.setattr(c6c, "_validate_pinvi_provider_sync", lambda _payload: True)

    with pytest.raises(DeploymentContractError, match="finalization cannot be repeated"):
        c6c.run_pinvi_canonical_smoke(
            _config(),
            cancel_probe_state=state,
        )

    finalizer.assert_not_called()


def test_consumed_fixture_resume_reads_map_and_finalizes_without_second_cancel_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_id = "11111111-1111-1111-1111-111111111111"
    state = PinviCancelProbeState(
        transaction_id=transaction_id,
        fixture=_fixture(state="armed"),
        attempted=True,
    )
    consumed = _fixture(
        state="consumed",
        cancellation_id="33333333-3333-3333-3333-333333333333",
    )
    finalized = _fixture(
        state="finalized",
        cancellation_id="33333333-3333-3333-3333-333333333333",
    )
    requests: list[tuple[str, str]] = []
    finalizer = Mock()
    responses = iter(
        (
            HttpProbeResponse(status=200, payload={}, set_cookie=True),
            HttpProbeResponse(status=200, payload={}),
            HttpProbeResponse(status=200, payload={}),
            HttpProbeResponse(status=204, payload={}, set_cookie=True),
            HttpProbeResponse(status=401, payload={}),
        )
    )

    monkeypatch.setattr(c6c, "_read_c6c_cancel_probe_fixture", lambda *_args: consumed)
    monkeypatch.setattr(c6c, "_cookie_opener", lambda **_kwargs: object())

    def session_request(_opener: object, url: str, *, method: str, **_kwargs: object) -> HttpProbeResponse:
        requests.append((method, url))
        return next(responses)

    def finalize(_config: object, resume_state: PinviCancelProbeState) -> C6cCancelProbeFixture:
        finalizer()
        assert resume_state.fixture == consumed
        resume_state.fixture = finalized
        return finalized

    monkeypatch.setattr(c6c, "_session_request", session_request)
    monkeypatch.setattr(c6c, "_finalize_c6c_cancel_probe_fixture", finalize)
    monkeypatch.setattr(c6c, "_pinvi_envelope_ok", lambda _payload: True)
    monkeypatch.setattr(c6c, "_validate_pinvi_etl_summary", lambda _payload: True)
    monkeypatch.setattr(c6c, "_validate_pinvi_provider_sync", lambda _payload: True)

    c6c.run_pinvi_canonical_smoke(_config(), cancel_probe_state=state)

    finalizer.assert_called_once_with()
    assert state.fixture == finalized
    assert not any("/cancel" in url for _method, url in requests)


def test_finalized_fixture_resume_reads_map_without_second_finalize_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalized = _fixture(
        state="finalized",
        cancellation_id="33333333-3333-3333-3333-333333333333",
    )
    state = PinviCancelProbeState(
        transaction_id=finalized.transaction_id,
        fixture=_fixture(
            state="consumed",
            cancellation_id="33333333-3333-3333-3333-333333333333",
        ),
        attempted=True,
        finalize_attempted=True,
        result=_outcome(),
    )
    finalizer = Mock()
    responses = iter(
        (
            HttpProbeResponse(status=200, payload={}, set_cookie=True),
            HttpProbeResponse(status=200, payload={}),
            HttpProbeResponse(status=200, payload={}),
            HttpProbeResponse(status=204, payload={}, set_cookie=True),
            HttpProbeResponse(status=401, payload={}),
        )
    )

    monkeypatch.setattr(c6c, "_read_c6c_cancel_probe_fixture", lambda *_args: finalized)
    monkeypatch.setattr(c6c, "_finalize_c6c_cancel_probe_fixture", finalizer)
    monkeypatch.setattr(c6c, "_cookie_opener", lambda **_kwargs: object())
    monkeypatch.setattr(c6c, "_session_request", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(c6c, "_pinvi_envelope_ok", lambda _payload: True)
    monkeypatch.setattr(c6c, "_validate_pinvi_etl_summary", lambda _payload: True)
    monkeypatch.setattr(c6c, "_validate_pinvi_provider_sync", lambda _payload: True)

    c6c.run_pinvi_canonical_smoke(_config(), cancel_probe_state=state)

    finalizer.assert_not_called()
    assert state.fixture == finalized
