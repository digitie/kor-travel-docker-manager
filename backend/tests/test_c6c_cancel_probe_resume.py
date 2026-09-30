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
    [
        "x",
        "x" * 31,
        "x" * 33,
        f"{'x' * 31}-",
        f"{'x' * 31}é",
        "00000000-0000-0000-0000-000000000000",
    ],
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


# --- 인증된 smoke의 안전한 GET 재시도(재구축 직후의 upstream 미준비 창) ---------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _response(status: int, payload: object = None) -> c6c.HttpProbeResponse:
    return c6c.HttpProbeResponse(
        status=status,
        payload=payload,
        retry_after=None,
        retry_after_present=False,
        set_cookie=False,
        location=None,
        body_text=None,
        content_type=None,
    )


_UNAVAILABLE = {"error": {"code": "FEATURE_SERVICE_UNAVAILABLE", "message": "x"}}
_UNAVAILABLE_MESSAGE = "C6c authenticated smoke endpoint is unavailable"


def _timeout() -> c6c.DeploymentContractError:
    error = c6c.DeploymentContractError(_UNAVAILABLE_MESSAGE)
    error.__cause__ = TimeoutError("timed out")
    return error


def _run_retry(monkeypatch: pytest.MonkeyPatch, outcomes: list[object]) -> tuple[object, _Clock, int]:
    clock = _Clock()
    monkeypatch.setattr(c6c.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(c6c.time, "sleep", clock.sleep)
    calls = 0

    def operation() -> c6c.HttpProbeResponse:
        nonlocal calls
        outcome = outcomes[min(calls, len(outcomes) - 1)]
        calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return cast(c6c.HttpProbeResponse, outcome)

    try:
        result: object = c6c._retry_safe_smoke_get(operation, unavailable_message=_UNAVAILABLE_MESSAGE)
    except c6c.DeploymentContractError as exc:
        result = exc
    return result, clock, calls


def test_a_safe_get_retries_timeouts_and_upstream_unavailable_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ok = _response(200, {"data": {}})
    result, clock, calls = _run_retry(
        monkeypatch, [_timeout(), _response(503, _UNAVAILABLE), _response(502, _UNAVAILABLE), ok]
    )
    assert result is ok
    assert calls == 4
    assert clock.sleeps == [5.0, 10.0, 20.0]


def test_a_safe_get_stays_fail_closed_after_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    unavailable = _response(503, _UNAVAILABLE)
    result, clock, calls = _run_retry(monkeypatch, [unavailable])
    # 다 쓰면 마지막 응답을 그대로 돌려준다 — 호출자의 envelope 판정이 실패로 끝낸다.
    assert result is unavailable
    assert calls == 6
    assert sum(clock.sleeps) <= 180.0
    assert clock.sleeps == [5.0, 10.0, 20.0, 30.0, 30.0]
    # 예외로 끝나면 예외를 다시 던진다.
    result, _, calls = _run_retry(monkeypatch, [_timeout()])
    assert isinstance(result, c6c.DeploymentContractError) and calls == 6


@pytest.mark.parametrize(
    "outcome",
    [
        _response(500, _UNAVAILABLE),
        _response(503, {"error": {"code": "PIPELINE_CANCELLATION_UNSAFE"}}),
        _response(503, None),
        _response(401, None),
        _response(200, {"data": {}}),
    ],
)
def test_a_safe_get_does_not_retry_other_answers(
    monkeypatch: pytest.MonkeyPatch, outcome: c6c.HttpProbeResponse
) -> None:
    result, clock, calls = _run_retry(monkeypatch, [outcome, _response(200, {"data": {}})])
    assert result is outcome and calls == 1 and clock.sleeps == []


def test_a_safe_get_does_not_retry_foreign_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    refused_other = c6c.DeploymentContractError("something else")
    refused_other.__cause__ = TimeoutError()
    result, _, calls = _run_retry(monkeypatch, [refused_other])
    assert result is refused_other and calls == 1
    no_cause = c6c.DeploymentContractError(_UNAVAILABLE_MESSAGE)
    no_cause.__cause__ = OSError("reset")
    result, _, calls = _run_retry(monkeypatch, [no_cause])
    assert result is no_cause and calls == 1


def test_a_safe_get_retry_never_waits_past_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(c6c, "_SAFE_GET_READINESS_BUDGET_SECONDS", 12.0)
    unavailable = _response(503, _UNAVAILABLE)
    result, clock, calls = _run_retry(monkeypatch, [unavailable])
    # 5초 대기 뒤 10초를 더 기다리면 12초를 넘는다 — 기다리지 않고 한 번 더 부르고 끝낸다.
    assert clock.sleeps == [5.0] and calls == 3 and result is unavailable


def test_only_bodyless_gets_may_retry() -> None:
    """cancel POST·login처럼 상태를 바꾸는 호출은 이 재시도에 들어오지 못한다."""

    opener = Mock()
    with pytest.raises(ValueError, match="bodyless GET"):
        c6c._session_request(
            opener, "http://127.0.0.1:1/x", method="POST", headers={}, body=b"{}",
            read_error_body=True, retry_safe_get_readiness=True,
        )
    opener.open.assert_not_called()
