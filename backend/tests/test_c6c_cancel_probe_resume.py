from __future__ import annotations

import hashlib
import http.client
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
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


# --- Map fixture 요청의 cold-start 재시도 (2026-10-04 08:32Z 재구축 실패) -----------------------------
#
# 막 뜬 Map API의 첫 fixture PUT이 IO wait 아래에서 10초 timeout으로 끝나 재구축 전체가 죽었다. 몇 분 뒤
# 같은 요청은 성공했다. ensure PUT과 read GET은 transaction ID로 키가 잡힌 멱등 요청이라 연결 단계 실패를
# 제한적으로 재시도한다. finalize POST는 "불확실한 결과 뒤 반복 금지" 계약이 있으므로 재시도하지 않는다.

_TRANSACTION_ID = "11111111-1111-1111-1111-111111111111"


def _fixture_config() -> C6cDeploymentConfig:
    return cast(
        C6cDeploymentConfig,
        SimpleNamespace(base_url="http://127.0.0.1:12701", fixture_token="fixture-token"),
    )


def _armed_payload() -> bytes:
    return json.dumps(
        {
            "data": {
                "fixture": {
                    "transaction_id": _TRANSACTION_ID,
                    "job_id": "22222222-2222-2222-2222-222222222222",
                    "state": "armed",
                    "cancellation_id": None,
                    "created_at": "2026-10-04T08:32:00+00:00",
                    "consumed_at": None,
                    "finalized_at": None,
                    "canonical_unsafe_outcome": None,
                    "capability_generation": c6c.C6C_CANCEL_PROBE_CAPABILITY_GENERATION,
                }
            },
            "meta": {},
        }
    ).encode()


class _FakeResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, *_args: object) -> bytes:
        return self._body


def _fake_urlopen(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[object]
) -> tuple[list[tuple[str, float]], _Clock]:
    """outcomes를 차례로 낸다(마지막 것은 반복). 예외면 던지고, bytes면 200 응답 본문이다."""

    clock = _Clock()
    monkeypatch.setattr(c6c.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(c6c.time, "sleep", clock.sleep)
    calls: list[tuple[str, float]] = []

    def urlopen(request: urllib.request.Request, *, timeout: float) -> _FakeResponse:
        outcome = outcomes[min(len(calls), len(outcomes) - 1)]
        calls.append((request.get_method(), timeout))
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(cast(bytes, outcome))

    monkeypatch.setattr(c6c.urllib.request, "urlopen", urlopen)
    return calls, clock


def test_fixture_ensure_put_survives_a_cold_start_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, clock = _fake_urlopen(monkeypatch, [TimeoutError("timed out"), _armed_payload()])
    state = PinviCancelProbeState(transaction_id=_TRANSACTION_ID)

    fixture = c6c._ensure_c6c_cancel_probe_fixture(_fixture_config(), state)

    assert fixture.state == "armed" and state.fixture == fixture
    assert [method for method, _ in calls] == ["PUT", "PUT"]
    assert clock.sleeps == [5.0]


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
        urllib.error.URLError(TimeoutError("timed out")),
        ConnectionResetError(104, "Connection reset by peer"),
    ],
)
def test_fixture_read_get_survives_cold_start_connection_errors(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    calls, _ = _fake_urlopen(monkeypatch, [error, error, _armed_payload()])

    fixture = c6c._read_c6c_cancel_probe_fixture(_fixture_config(), _TRANSACTION_ID)

    assert fixture.transaction_id == _TRANSACTION_ID
    assert [method for method, _ in calls] == ["GET", "GET", "GET"]


def test_fixture_cold_start_retry_is_bounded_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, clock = _fake_urlopen(monkeypatch, [TimeoutError("timed out")])
    state = PinviCancelProbeState(transaction_id=_TRANSACTION_ID)

    with pytest.raises(DeploymentContractError, match="Map smoke endpoint is unavailable"):
        c6c._ensure_c6c_cancel_probe_fixture(_fixture_config(), state)

    # 대기 합 + 시도마다 timeout을 더한 최악이 약 2분이다.
    worst_case = sum(clock.sleeps) + sum(timeout for _, timeout in calls)
    assert len(calls) == 5 and worst_case <= 120.0
    assert state.fixture is None


def test_fixture_cold_start_retry_ignores_http_answers_and_other_os_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, clock = _fake_urlopen(
        monkeypatch, [OSError("unrelated"), _armed_payload()]
    )
    with pytest.raises(DeploymentContractError, match="Map smoke endpoint is unavailable"):
        c6c._read_c6c_cancel_probe_fixture(_fixture_config(), _TRANSACTION_ID)
    assert len(calls) == 1 and clock.sleeps == []

    http_error = urllib.error.HTTPError(
        "http://127.0.0.1:12701/x", 503, "unavailable", Message(), io.BytesIO(b"{}")
    )
    calls, clock = _fake_urlopen(monkeypatch, [http_error, _armed_payload()])
    with pytest.raises(DeploymentContractError, match="lifecycle read failed"):
        c6c._read_c6c_cancel_probe_fixture(_fixture_config(), _TRANSACTION_ID)
    assert len(calls) == 1 and clock.sleeps == []


def test_fixture_finalize_post_is_not_retried_after_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, clock = _fake_urlopen(monkeypatch, [TimeoutError("timed out"), _armed_payload()])
    state = _consumed_state(finalize_attempted=True)
    config = _fixture_config()

    with pytest.raises(DeploymentContractError, match="Map smoke endpoint is unavailable"):
        c6c._finalize_c6c_cancel_probe_fixture(config, state)

    assert [method for method, _ in calls] == ["POST"] and clock.sleeps == []


class _TruncatedResponse(_FakeResponse):
    """헤더는 왔지만 본문이 Content-Length보다 짧게 끊긴 응답 — `read()`가 `IncompleteRead`를 던진다."""

    def __init__(self) -> None:
        super().__init__(b"")

    def read(self, *_args: object) -> bytes:
        raise http.client.IncompleteRead(b'{"da', 120)


def test_fixture_read_get_survives_a_truncated_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """`IncompleteRead`는 `OSError`가 아니다 — 연결 단계 실패처럼 재시도된다."""

    clock = _Clock()
    monkeypatch.setattr(c6c.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(c6c.time, "sleep", clock.sleep)
    responses = iter((_TruncatedResponse(), _FakeResponse(_armed_payload())))
    monkeypatch.setattr(
        c6c.urllib.request, "urlopen", lambda _request, *, timeout: next(responses)
    )

    fixture = c6c._read_c6c_cancel_probe_fixture(_fixture_config(), _TRANSACTION_ID)

    assert fixture.transaction_id == _TRANSACTION_ID and clock.sleeps == [5.0]


@pytest.mark.parametrize(
    "error",
    [
        http.client.IncompleteRead(b"", 10),
        http.client.RemoteDisconnected("Remote end closed connection without response"),
    ],
)
def test_fixture_put_retries_incomplete_reads_and_remote_disconnects(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    calls, _ = _fake_urlopen(monkeypatch, [error, _armed_payload()])
    state = PinviCancelProbeState(transaction_id=_TRANSACTION_ID)

    fixture = c6c._ensure_c6c_cancel_probe_fixture(_fixture_config(), state)

    assert fixture.state == "armed"
    assert [method for method, _ in calls] == ["PUT", "PUT"]


def test_an_incomplete_read_that_is_not_retried_is_a_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재시도하지 않는 finalize POST와 재시도를 다 쓴 GET 모두 날것의 `IncompleteRead`로 새지 않는다."""

    calls, clock = _fake_urlopen(monkeypatch, [http.client.IncompleteRead(b"", 10)])
    with pytest.raises(DeploymentContractError, match="Map smoke endpoint is unavailable") as caught:
        c6c._finalize_c6c_cancel_probe_fixture(
            _fixture_config(), _consumed_state(finalize_attempted=True)
        )
    assert isinstance(caught.value.__cause__, http.client.IncompleteRead)
    assert [method for method, _ in calls] == ["POST"] and clock.sleeps == []

    calls, _ = _fake_urlopen(monkeypatch, [http.client.IncompleteRead(b"", 10)])
    with pytest.raises(DeploymentContractError, match="Map smoke endpoint is unavailable"):
        c6c._read_c6c_cancel_probe_fixture(_fixture_config(), _TRANSACTION_ID)
    assert len(calls) == 5


def _stalling_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stalls: list[float],
    outcomes: list[object],
    timeouts: list[float] | None = None,
) -> tuple[list[float], _Clock]:
    """시도마다 `stalls`만큼 벽시계를 쓴다(마지막 값 반복) — socket timeout은 연산마다라 시도의 timeout보다 길 수 있다."""

    clock = _Clock()
    monkeypatch.setattr(c6c.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(c6c.time, "sleep", clock.sleep)
    starts: list[float] = []

    def urlopen(_request: urllib.request.Request, *, timeout: float) -> _FakeResponse:
        index = len(starts)
        outcome = outcomes[min(index, len(outcomes) - 1)]
        starts.append(clock.now)
        if timeouts is not None:
            timeouts.append(timeout)
        clock.now += stalls[min(index, len(stalls) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(cast(bytes, outcome))

    monkeypatch.setattr(c6c.urllib.request, "urlopen", urlopen)
    return starts, clock


def test_cold_start_retry_has_a_wall_clock_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """시도마다 50초씩 늘어지면 시도 횟수가 아니라 벽시계 예산이 멈춘다 — 같은 오류로 닫힌다."""

    starts, clock = _stalling_urlopen(
        monkeypatch, stalls=[50.0], outcomes=[TimeoutError("timed out")]
    )

    with pytest.raises(DeploymentContractError, match="Map smoke endpoint is unavailable"):
        c6c._read_c6c_cancel_probe_fixture(_fixture_config(), _TRANSACTION_ID)

    budget = c6c._MAP_FIXTURE_BUDGET_SECONDS
    assert 150.0 <= budget <= 210.0
    # 예산 안에서만 시도를 시작하고, 대기는 예산을 넘기지 않는다. 넘는 것은 진행 중이던 시도 하나뿐이다.
    assert all(start < budget for start in starts) and len(starts) < 5
    assert clock.now <= budget + 50.0


def test_the_smoke_shares_one_map_request_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """smoke 하나의 Map fixture 요청(ensure·cancel 뒤 read)이 예산 하나를 나눠 쓴다.

    첫 read가 예산을 거의 다 쓰면 둘째 read는 남은 몇 초 안에서만 돈다 — 시도 하나가 실패하면 대기 없이 닫힌다.
    """

    budget = c6c._MAP_FIXTURE_BUDGET_SECONDS
    starts, _ = _stalling_urlopen(
        monkeypatch,
        stalls=[budget - 5.0, 10.0],
        outcomes=[_armed_payload(), TimeoutError("timed out")],
    )

    def smoke_body(config: C6cDeploymentConfig, **_kwargs: object) -> list[dict[str, int | str]]:
        c6c._read_c6c_cancel_probe_fixture(config, _TRANSACTION_ID)
        c6c._read_c6c_cancel_probe_fixture(config, _TRANSACTION_ID)
        return []

    monkeypatch.setattr(c6c, "_run_pinvi_canonical_smoke", smoke_body)
    with pytest.raises(DeploymentContractError, match="Map smoke endpoint is unavailable"):
        c6c.run_pinvi_canonical_smoke(_fixture_config())
    assert len(starts) == 2

    # 예산은 smoke마다 새로 잡힌다 — 다음 smoke의 첫 요청은 다시 시작한다.
    starts.clear()
    with pytest.raises(DeploymentContractError, match="Map smoke endpoint is unavailable"):
        c6c.run_pinvi_canonical_smoke(_fixture_config())
    assert len(starts) == 2


def _smoke_with_spent_budget(
    monkeypatch: pytest.MonkeyPatch, spent: float, body: Callable[[], object]
) -> None:
    """smoke 예산을 `spent`초 쓴 상태에서 `body()`를 부른다(cancel 뒤 read의 자리)."""

    def smoke_body(config: C6cDeploymentConfig, **_kwargs: object) -> list[dict[str, int | str]]:
        budget = c6c._MAP_FIXTURE_BUDGET.get()
        assert budget is not None
        budget.remaining -= spent
        body()
        return []

    monkeypatch.setattr(c6c, "_run_pinvi_canonical_smoke", smoke_body)
    c6c.run_pinvi_canonical_smoke(_fixture_config())


def test_an_exhausted_budget_still_gives_the_read_one_full_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """파괴적인 PinVi cancel 뒤의 read는 예산이 바닥나도 timeout 10초 그대로 한 번은 간다."""

    timeouts: list[float] = []
    starts, clock = _stalling_urlopen(
        monkeypatch, stalls=[1.0], outcomes=[_armed_payload()], timeouts=timeouts
    )
    def read() -> object:
        return c6c._read_c6c_cancel_probe_fixture(_fixture_config(), _TRANSACTION_ID)

    _smoke_with_spent_budget(monkeypatch, c6c._MAP_FIXTURE_BUDGET_SECONDS + 60.0, read)
    assert timeouts == [c6c._MAP_FIXTURE_REQUEST_TIMEOUT_SECONDS]

    # 그 한 번이 실패하면 재시도·대기 없이 같은 오류로 닫힌다.
    timeouts.clear()
    starts, clock = _stalling_urlopen(
        monkeypatch, stalls=[10.0], outcomes=[TimeoutError("timed out")], timeouts=timeouts
    )
    with pytest.raises(DeploymentContractError, match="Map smoke endpoint is unavailable"):
        _smoke_with_spent_budget(monkeypatch, c6c._MAP_FIXTURE_BUDGET_SECONDS, read)
    assert timeouts == [c6c._MAP_FIXTURE_REQUEST_TIMEOUT_SECONDS] and clock.sleeps == []
    assert len(starts) == 1


def test_only_retries_are_clipped_to_the_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """첫 시도는 10초, 재시도는 남은 예산(여기서는 7초)으로 줄어든 timeout을 받는다."""

    budget = c6c._MAP_FIXTURE_BUDGET_SECONDS
    timeouts: list[float] = []
    # 첫 시도가 budget-12초를 쓰고 실패 → 5초 대기 → 남은 예산 7초.
    _stalling_urlopen(
        monkeypatch,
        stalls=[budget - 12.0, 1.0],
        outcomes=[TimeoutError("timed out"), _armed_payload()],
        timeouts=timeouts,
    )

    fixture = c6c._read_c6c_cancel_probe_fixture(_fixture_config(), _TRANSACTION_ID)

    assert fixture.transaction_id == _TRANSACTION_ID
    assert timeouts == [c6c._MAP_FIXTURE_REQUEST_TIMEOUT_SECONDS, 7.0]


class _BodyTruncatedHttpError(urllib.error.HTTPError):
    """오류 응답의 본문을 읽다가 `IncompleteRead`가 난다."""

    def __init__(self) -> None:
        super().__init__("http://127.0.0.1:1/x", 503, "unavailable", Message(), io.BytesIO(b""))

    def read(self, *_args: object) -> bytes:
        raise http.client.IncompleteRead(b'{"err', 64)


def test_a_truncated_map_error_body_is_a_contract_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(_request: urllib.request.Request, *, timeout: float) -> object:
        raise _BodyTruncatedHttpError()

    monkeypatch.setattr(c6c.urllib.request, "urlopen", urlopen)
    with pytest.raises(DeploymentContractError, match="Map smoke endpoint is unavailable") as caught:
        c6c._request_json_once(
            "http://127.0.0.1:1/x", method="GET", headers={}, body=None, read_error_body=True
        )
    assert isinstance(caught.value.__cause__, http.client.IncompleteRead)


@pytest.mark.parametrize("where", ["error_body", "response_body"])
def test_pinvi_session_requests_never_leak_a_raw_incomplete_read(where: str) -> None:
    """PinVi `_session_request`도 `IncompleteRead`(오류 본문·정상 본문)를 계약 오류로 감싼다."""

    opener = Mock()
    if where == "error_body":
        opener.open.side_effect = _BodyTruncatedHttpError()
    else:
        opener.open.return_value = _TruncatedResponse()
    with pytest.raises(
        DeploymentContractError, match="authenticated smoke endpoint is unavailable"
    ) as caught:
        c6c._session_request(
            opener, "http://127.0.0.1:1/x", method="GET", headers={}, read_error_body=True
        )
    assert isinstance(caught.value.__cause__, http.client.IncompleteRead)


def test_cold_start_retry_refuses_requests_with_a_body_or_post() -> None:
    for method, body in (("POST", None), ("PUT", b"{}"), ("DELETE", None)):
        with pytest.raises(ValueError, match="idempotent bodyless"):
            c6c._request_json(
                "http://127.0.0.1:1/x", method=method, headers={}, body=body,
                retry_cold_start=True,
            )
