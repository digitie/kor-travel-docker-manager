"""GM-05 회귀: 프록시 뒤 로그인 rate-limit 공유 버킷 감지.

check_login_rate_limit이 client IP 단일 키라, 신뢰되지 않은 엣지 프록시 뒤에서는
모든 WAN 클라이언트가 프록시 소켓 IP 하나로 수렴해 외부인이 관리자 로그인을 잠글 수
있다. 코드 측 방어는 (1) readiness가 이 misconfiguration을 노출하고, (2) 429 감사에
shared-IP bucket 사실을 남기는 것이다. 근본 해법은 prod 프록시 설정(문서).
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from kor_travel_docker_manager.services.auth_service import (
    login_bucket_is_shared_fallback,
    trusted_proxy_posture,
)


def _request(*, client_ip: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": headers or [],
            "client": (client_ip, 55555),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_shared_bucket_detected_when_xff_present_but_proxy_untrusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 기본 신뢰 CIDR은 loopback 전용. 엣지 프록시가 WAN IP에서 접속하며 XFF를 붙였다.
    monkeypatch.delenv("KTDM_TRUSTED_PROXY_CIDRS", raising=False)
    monkeypatch.delenv("KTDM_TRUSTED_PROXY_SECRET", raising=False)
    req = _request(
        client_ip="203.0.113.9",  # 엣지 프록시(신뢰 대상 아님)
        headers=[(b"x-forwarded-for", b"198.51.100.5")],
    )
    assert login_bucket_is_shared_fallback(req) is True


def test_not_shared_bucket_when_no_forwarded_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KTDM_TRUSTED_PROXY_CIDRS", raising=False)
    req = _request(client_ip="203.0.113.9")
    assert login_bucket_is_shared_fallback(req) is False


def test_not_shared_bucket_when_proxy_is_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    # 프록시 IP를 exact /32로 신뢰하면 XFF가 반영돼 per-client 버킷이 된다.
    monkeypatch.setenv("KTDM_TRUSTED_PROXY_CIDRS", "203.0.113.9/32")
    monkeypatch.delenv("KTDM_TRUSTED_PROXY_SECRET", raising=False)
    req = _request(
        client_ip="203.0.113.9",
        headers=[(b"x-forwarded-for", b"198.51.100.5")],
    )
    assert login_bucket_is_shared_fallback(req) is False


def test_posture_flags_loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KTDM_TRUSTED_PROXY_CIDRS", raising=False)
    monkeypatch.delenv("KTDM_TRUSTED_PROXY_SECRET", raising=False)
    posture = trusted_proxy_posture()
    assert posture["loopback_only"] is True
    assert posture["secret_set"] is False


def test_posture_flags_wide_cidr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KTDM_TRUSTED_PROXY_CIDRS", "10.0.0.0/24")
    posture = trusted_proxy_posture()
    assert posture["has_wide_cidr"] is True
    assert posture["loopback_only"] is False


def test_posture_exact_proxy_with_secret_is_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KTDM_TRUSTED_PROXY_CIDRS", "203.0.113.9/32")
    monkeypatch.setenv("KTDM_TRUSTED_PROXY_SECRET", "s3cret")
    posture = trusted_proxy_posture()
    assert posture["loopback_only"] is False
    assert posture["has_wide_cidr"] is False
    assert posture["secret_set"] is True


def test_readiness_warns_on_production_loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from kor_travel_docker_manager.services.deployment_readiness import (
        _check_login_rate_limit_proxy,
    )

    monkeypatch.delenv("KTDM_TRUSTED_PROXY_CIDRS", raising=False)
    monkeypatch.delenv("KTDM_TRUSTED_PROXY_SECRET", raising=False)
    check = _check_login_rate_limit_proxy({"KTDM_DEPLOYMENT_ENVIRONMENT": "production"})
    assert check.state == "warn"
    assert "rate limit" in check.detail


def test_readiness_ok_in_non_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from kor_travel_docker_manager.services.deployment_readiness import (
        _check_login_rate_limit_proxy,
    )

    monkeypatch.delenv("KTDM_TRUSTED_PROXY_CIDRS", raising=False)
    check = _check_login_rate_limit_proxy({"KTDM_DEPLOYMENT_ENVIRONMENT": "local"})
    assert check.state == "ok"


def test_readiness_ok_with_exact_proxy_and_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from kor_travel_docker_manager.services.deployment_readiness import (
        _check_login_rate_limit_proxy,
    )

    monkeypatch.setenv("KTDM_TRUSTED_PROXY_CIDRS", "203.0.113.9/32")
    monkeypatch.setenv("KTDM_TRUSTED_PROXY_SECRET", "s3cret")
    check = _check_login_rate_limit_proxy({"KTDM_DEPLOYMENT_ENVIRONMENT": "production"})
    assert check.state == "ok"
