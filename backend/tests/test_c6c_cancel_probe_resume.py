from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import c6c_deployment as c6c
from kor_travel_docker_manager.services.c6c_deployment import (
    C6cCancelProbeFixture,
    DeploymentContractError,
    HttpProbeResponse,
    PinviCancelProbeState,
)


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        smoke=SimpleNamespace(
            pinvi_api_base_url="http://pinvi.test",
            pinvi_admin_email="admin@example.test",
            pinvi_admin_password="test-password",
        ),
    )


def _outcome() -> dict[str, int | str]:
    return {
        "name": "pinvi_cancel_error",
        "status": 409,
        "code": "PIPELINE_CANCELLATION_UNSAFE",
    }


def _consumed_state(*, finalize_attempted: bool) -> PinviCancelProbeState:
    transaction_id = "11111111-1111-1111-1111-111111111111"
    fixture = C6cCancelProbeFixture(
        transaction_id=transaction_id,
        job_id="22222222-2222-2222-2222-222222222222",
        state="consumed",
        cancellation_id="33333333-3333-3333-3333-333333333333",
        canonical_unsafe_outcome=_outcome(),
    )
    return PinviCancelProbeState(
        transaction_id=transaction_id,
        fixture=fixture,
        attempted=True,
        finalize_attempted=finalize_attempted,
        result=_outcome(),
    )


def test_rehearsal_loader_requires_production_like_fixture_capabilities() -> None:
    values = {
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
        "KTDM_C6C_MAP_UI_ADMIN_PASSWORD": "map-ui-password-1",
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": "pinvi-password-1",
        "KTDM_C6C_CONTRACT_GENERATION": "c6c-v1",
    }

    config = c6c.load_c6c_deployment_config_from_environment(values)

    assert config.deployment_environment == "rehearsal"
    assert config.pinvi_environment == "production"
    assert config.fixture_token == "f" * 32


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
