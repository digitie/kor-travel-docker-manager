from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType, ModuleType
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from kor_travel_docker_manager.services.pinned_runtime_release import (
    current_pinned_runtime_release,
)

PINNED_RUNTIME_RELEASE = current_pinned_runtime_release()


def _driver() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py"
    spec = importlib.util.spec_from_file_location("m05_isolated_e2e_driver", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pair_entry(*, revision: str, raw: bytes) -> dict[str, str]:
    canonical = json.dumps(
        json.loads(raw), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "openapi_sha256": hashlib.sha256(raw).hexdigest(),
        "runtime_operation_contract_sha256": "a" * 64,
        "source_canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        "source_operation_contract_sha256": "b" * 64,
        "source_revision": revision,
    }


def test_pair_reads_every_pinned_openapi_blob_before_accepting_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver()
    pinvi_root = tmp_path / "pinvi"
    map_root = tmp_path / "map"
    (pinvi_root / "contracts").mkdir(parents=True)
    map_root.mkdir()
    map_revision = PINNED_RUNTIME_RELEASE.source_for("map").revision
    revisions = {
        "admin": map_revision,
        "full": map_revision,
        "service": "c" * 40,
        "user": "d" * 40,
    }
    paths = {
        "admin": "packages/kor-travel-map-api/openapi.json",
        "full": "packages/kor-travel-map-api/openapi.json",
        "service": "packages/kor-travel-map-api/openapi.service.json",
        "user": "packages/kor-travel-map-api/openapi.user.json",
    }
    blobs = {
        f"{revisions[name]}:{paths[name]}": json.dumps({"name": name}).encode() for name in paths
    }
    pair = {
        "map": {
            name: _pair_entry(
                revision=revisions[name], raw=blobs[f"{revisions[name]}:{paths[name]}"]
            )
            for name in paths
        },
        "runtime_image_digests": {},
        "version": 1,
    }
    (pinvi_root / "contracts/kor-travel-map-m05-pair-provenance-v1.json").write_text(
        json.dumps(pair), encoding="utf-8"
    )
    fetches: list[tuple[str, ...]] = []

    def fake_command(*args: str, **_kwargs: object) -> str:
        fetches.append(args)
        return ""

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        target = args[-1]
        return subprocess.CompletedProcess(args, 0, stdout=blobs[target])

    monkeypatch.setattr(driver, "_command", fake_command)
    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    actual, service_openapi_sha256, service_source_revision = driver._pair(pinvi_root, map_root)

    assert actual.map_full_openapi_sha256 == pair["map"]["full"]["openapi_sha256"]
    assert service_openapi_sha256 == pair["map"]["service"]["openapi_sha256"]
    assert service_source_revision == revisions["service"]
    assert {args[-1] for args in fetches} == set(revisions.values())


def test_pair_rejects_a_historical_blob_digest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver()
    pinvi_root = tmp_path / "pinvi"
    map_root = tmp_path / "map"
    (pinvi_root / "contracts").mkdir(parents=True)
    map_root.mkdir()
    revision = PINNED_RUNTIME_RELEASE.source_for("map").revision
    raw = b'{"version":1}'
    entry = _pair_entry(revision=revision, raw=raw)
    pair = {
        "map": {name: dict(entry) for name in ("admin", "full", "service", "user")},
        "runtime_image_digests": {},
        "version": 1,
    }
    pair["map"]["service"]["openapi_sha256"] = "0" * 64
    (pinvi_root / "contracts/kor-travel-map-m05-pair-provenance-v1.json").write_text(
        json.dumps(pair), encoding="utf-8"
    )
    monkeypatch.setattr(driver, "_command", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        driver.subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, stdout=raw),
    )

    with pytest.raises(driver._PhaseError, match="pair_contract_invalid"):
        driver._pair(pinvi_root, map_root)


def test_pinvi_manager_admission_contract_requires_the_gate_and_verifier(tmp_path: Path) -> None:
    driver = _driver()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "docker-app.sh").write_text(
            "PINVI_M05_ISOLATED_MANAGER_ADMISSION_PATH\n"
            "PINVI_M05_PINSET_SHA256\n"
            "PINVI_M05_EXECUTION_IDENTITY_SHA256\n"
            "PINVI_DOCKER_COMPOSE_EXTRA_FILE\n"
        "m05_isolated_manager_admission.py\n",
        encoding="utf-8",
    )
    (scripts / "m05_isolated_manager_admission.py").write_text(
        "pinvi-m05-isolated-manager-admission-v1\n"
        '[[ "$EUID" -eq 0 ]]\n'
        "/usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/python3 -I\n",
        encoding="utf-8",
    )

    driver._assert_pinvi_manager_admission_contract(tmp_path)

    (scripts / "m05_isolated_manager_admission.py").unlink()
    with pytest.raises(driver._PhaseError, match="pinvi_manager_admission_contract_invalid"):
        driver._assert_pinvi_manager_admission_contract(tmp_path)


def test_generated_pbkdf2_hash_verifies_the_original_value() -> None:
    value = "isolated-password"
    encoded = _driver()._pbkdf2_password_hash(value)
    scheme, iterations, salt, digest = encoded.split("$")

    assert scheme == "pbkdf2_sha256"

    def restore(item: str) -> bytes:
        return base64.urlsafe_b64decode(item + "=" * (-len(item) % 4))

    assert hashlib.pbkdf2_hmac(
        "sha256", value.encode("utf-8"), restore(salt), int(iterations)
    ) == restore(digest)


def test_terminal_registry_reason_exposes_only_allowlisted_phase() -> None:
    """registry는 다음 candidate의 보정 범위만 말하고 예외 원문은 싣지 않는다."""

    driver = _driver()

    assert (
        driver._terminal_registry_reason("map_health_transport_failed")
        == "M05 isolated one-shot terminal: map_health_transport_failed"
    )
    assert (
        driver._terminal_registry_reason("untrusted detail must never be published")
        == "M05 isolated one-shot terminal: driver_contract_failed"
    )
    assert (
        driver._terminal_registry_reason("runtime_setup_credentials")
        == "M05 isolated one-shot terminal: runtime_setup_credentials"
    )
    assert (
        driver._terminal_registry_reason("runtime_loopback_publish_invalid")
        == "M05 isolated one-shot terminal: runtime_loopback_publish_invalid"
    )


def test_runtime_setup_uses_ordered_safe_subphases() -> None:
    """setup의 ordinary exception도 raw 없이 다음 source 보정 범위로만 수렴한다."""

    driver = _driver()
    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )
    phases = (
        "runtime_setup_ports",
        "runtime_setup_workspace",
        "runtime_setup_admission_build",
        "runtime_setup_admission_write",
        "runtime_setup_network",
        "runtime_setup_credentials",
        "runtime_setup_map_config",
        "runtime_setup_pinvi_config",
    )
    positions = [source.index(f'phase = "{phase}"') for phase in phases]

    assert positions == sorted(positions)
    assert all(phase in driver._PUBLIC_TERMINAL_PHASES for phase in phases)


def test_http_json_rejects_non_loopback_url_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _driver()

    monkeypatch.setattr(
        driver._LOOPBACK_OPENER,
        "open",
        lambda *_args, **_kwargs: pytest.fail("transport must not be called"),
    )

    with pytest.raises(driver._PhaseError, match="runtime_http_url_invalid"):
        driver._http_json("http://localhost:13701/health", headers={})
    with pytest.raises(driver._PhaseError, match="runtime_http_url_invalid"):
        driver._http_json("https://127.0.0.1:13701/health", headers={})


def test_http_json_default_transport_is_proxy_free_loopback_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _driver()
    seen: list[Request] = []

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return b'{"data":{}}'

    def fake_open(request: Request, *, timeout: int) -> _Response:
        assert timeout == 10
        seen.append(request)
        return _Response()

    monkeypatch.setattr(driver._LOOPBACK_OPENER, "open", fake_open)
    assert driver._http_json("http://127.0.0.1:13701/health", headers={}) == {"data": {}}
    assert len(seen) == 1


def test_http_json_emits_only_the_caller_fixed_transport_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 원문을 저장하지 않고 다음 immutable candidate의 보정 범위만 남긴다."""

    driver = _driver()

    def fail_open(*_args: object, **_kwargs: object) -> object:
        raise URLError("transport detail must not escape")

    monkeypatch.setattr(driver._LOOPBACK_OPENER, "open", fail_open)

    with pytest.raises(driver._PhaseError, match="map_health_http_failed"):
        driver._http_json(
            "http://127.0.0.1:13701/health",
            headers={},
            failure_phase="map_health_http_failed",
        )


def test_map_health_keeps_http_status_and_loopback_transport_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """다음 one-shot source가 원문 없이 startup 보정 범위를 구별하게 한다."""

    driver = _driver()

    def fail_status(request: Request, **_kwargs: object) -> object:
        raise HTTPError(request.full_url, 503, "discarded", None, None)

    monkeypatch.setattr(driver._LOOPBACK_OPENER, "open", fail_status)
    with pytest.raises(driver._PhaseError, match="map_health_status_failed"):
        driver._http_json(
            "http://127.0.0.1:13701/health",
            headers={},
            failure_phase="map_health_transport_failed",
            http_error_phase="map_health_status_failed",
        )

    def fail_transport(*_args: object, **_kwargs: object) -> object:
        raise URLError("discarded")

    monkeypatch.setattr(driver._LOOPBACK_OPENER, "open", fail_transport)
    with pytest.raises(driver._PhaseError, match="map_health_transport_failed"):
        driver._http_json(
            "http://127.0.0.1:13701/health",
            headers={},
            failure_phase="map_health_transport_failed",
            http_error_phase="map_health_status_failed",
        )


def test_map_health_retries_only_a_transient_loopback_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _driver()
    calls = 0
    waits: list[int] = []

    def transient_health(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise driver._PhaseError("map_health_transport_failed")
        return {"data": {}}

    monkeypatch.setattr(driver, "_http_json", transient_health)
    monkeypatch.setattr(driver.time, "sleep", waits.append)

    assert driver._wait_for_map_health(url="http://127.0.0.1:13701/health") == {"data": {}}
    assert calls == 2
    assert waits == [driver.LOOPBACK_HTTP_READINESS_RETRY_SECONDS]


def test_map_health_uses_the_general_loopback_readiness_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M05 consumer는 Manager의 bounded host-loopback startup 정책을 따른다."""

    driver = _driver()
    calls = 0
    waits: list[int] = []

    def transient_until_final_attempt(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls < driver.LOOPBACK_HTTP_READINESS_ATTEMPTS:
            raise driver._PhaseError("map_health_transport_failed")
        return {"data": {}}

    monkeypatch.setattr(driver, "_http_json", transient_until_final_attempt)
    monkeypatch.setattr(driver.time, "sleep", waits.append)

    assert driver._wait_for_map_health(url="http://127.0.0.1:13701/health") == {"data": {}}
    assert calls == driver.LOOPBACK_HTTP_READINESS_ATTEMPTS
    assert waits == [driver.LOOPBACK_HTTP_READINESS_RETRY_SECONDS] * (
        driver.LOOPBACK_HTTP_READINESS_ATTEMPTS - 1
    )


def test_loopback_publish_is_verified_before_http_readiness() -> None:
    driver = _driver()
    valid = {
        "NetworkSettings": {
            "Ports": {"13701/tcp": [{"HostIp": "127.0.0.1", "HostPort": "13701"}]}
        }
    }

    driver._assert_loopback_tcp_publish(valid, container_port=13701, host_port=13701)

    invalid = {
        "NetworkSettings": {
            "Ports": {"13701/tcp": [{"HostIp": "0.0.0.0", "HostPort": "13701"}]}
        }
    }
    with pytest.raises(driver._PhaseError, match="runtime_loopback_publish_invalid"):
        driver._assert_loopback_tcp_publish(invalid, container_port=13701, host_port=13701)


def test_rendered_loopback_publish_is_checked_before_a_claim() -> None:
    driver = _driver()
    rendered = json.dumps(
        {
            "services": {
                "api": {
                    "ports": [
                        {
                            "host_ip": "127.0.0.1",
                            "protocol": "tcp",
                            "published": "31337",
                            "target": 13701,
                        }
                    ]
                }
            }
        }
    )

    driver._assert_rendered_loopback_tcp_publish(
        rendered, service="api", container_port=13701, host_port=31337
    )
    with pytest.raises(driver._PhaseError, match="runtime_loopback_publish_config_invalid"):
        driver._assert_rendered_loopback_tcp_publish(
            rendered, service="api", container_port=13701, host_port=31338
        )


def test_rendered_loopback_publish_keeps_only_safe_port_evidence(tmp_path: Path) -> None:
    driver = _driver()
    evidence = tmp_path / "rendered-loopback-publish.json"
    rendered = json.dumps(
        {
            "services": {
                "api": {
                    "ports": [
                        {
                            "host_ip": "127.0.0.1",
                            "protocol": "tcp",
                            "published": "31337",
                            "target": 13701,
                        }
                    ]
                }
            }
        }
    )

    driver._assert_rendered_loopback_tcp_publish(
        rendered,
        service="api",
        container_port=13701,
        host_port=31337,
        evidence_path=evidence,
    )

    assert json.loads(evidence.read_text(encoding="utf-8")) == {
        "container_port": 13701,
        "host_port": 31337,
        "port_count": 1,
        "ports": [
            {
                "host_ip": "127.0.0.1",
                "protocol": "tcp",
                "published": "31337",
                "target": 13701,
            }
        ],
        "service": "api",
        "version": 1,
    }


def test_rendered_loopback_publish_parse_failure_keeps_only_opt_in_bounded_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver()
    safe = tmp_path / "parse.json"
    with pytest.raises(driver._PhaseError, match="runtime_loopback_publish_config_invalid"):
        driver._assert_rendered_loopback_tcp_publish(
            "not-json",
            service="api",
            container_port=13701,
            host_port=31337,
            parse_failure_evidence_path=safe,
        )
    assert json.loads(safe.read_text(encoding="utf-8")) == {
        "kind": "compose_config_output",
        "truncated": False,
        "version": 1,
    }
    assert not safe.with_suffix(".stdout").exists()

    monkeypatch.setenv(driver._FORENSIC_CAPTURE_ENV, "1")
    forensic = tmp_path / "forensic.json"
    oversized = "x" * (driver._FORENSIC_CAPTURE_LIMIT + 1)
    with pytest.raises(driver._PhaseError, match="runtime_loopback_publish_config_invalid"):
        driver._assert_rendered_loopback_tcp_publish(
            oversized,
            service="api",
            container_port=13701,
            host_port=31337,
            parse_failure_evidence_path=forensic,
        )
    assert forensic.with_suffix(".stdout").read_bytes() == (
        b"x" * driver._FORENSIC_CAPTURE_LIMIT
    )


def test_rendered_loopback_publish_evidence_drops_unknown_or_invalid_values(
    tmp_path: Path,
) -> None:
    driver = _driver()
    evidence = tmp_path / "rendered-loopback-publish.json"
    rendered = json.dumps(
        {
            "services": {
                "api": {
                    "ports": [
                        {
                            "host_ip": "127.0.0.1",
                            "name": "untrusted-env-interpolation-value",
                            "protocol": "tcp",
                            "published": "not-a-port",
                            "target": 13701,
                            "x-unexpected": {"arbitrary": "rendered-compose-data"},
                        }
                    ]
                }
            }
        }
    )

    with pytest.raises(driver._PhaseError, match="runtime_loopback_publish_config_invalid"):
        driver._assert_rendered_loopback_tcp_publish(
            rendered,
            service="api",
            container_port=13701,
            host_port=31337,
            evidence_path=evidence,
        )

    assert json.loads(evidence.read_text(encoding="utf-8")) == {
        "container_port": 13701,
        "host_port": 31337,
        "port_count": 1,
        "ports": [
            {
                "host_ip": "127.0.0.1",
                "protocol": "tcp",
                "published": None,
                "target": 13701,
            }
        ],
        "service": "api",
        "version": 1,
    }


def test_map_health_does_not_retry_a_received_http_status_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _driver()
    waits: list[int] = []

    def status_failure(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise driver._PhaseError("map_health_status_failed")

    monkeypatch.setattr(driver, "_http_json", status_failure)
    monkeypatch.setattr(driver.time, "sleep", waits.append)

    with pytest.raises(driver._PhaseError, match="map_health_status_failed"):
        driver._wait_for_map_health(url="http://127.0.0.1:13701/health")
    assert waits == []


def test_pinvi_receipt_transport_phase_is_not_collapsed_into_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """receipt polling은 transport failure를 fixed caller phase로 보존한다."""

    driver = _driver()

    def fail_http(*_args: object, **_kwargs: object) -> object:
        raise driver._PhaseError("m05_pinvi_receipt_http_failed")

    monkeypatch.setattr(driver, "_http_json", fail_http)

    with pytest.raises(driver._PhaseError, match="m05_pinvi_receipt_http_failed"):
        driver._wait_for_pinvi_receipt(
            api_url="http://127.0.0.1:13701",
            opener=object(),
            event_id="00000000-0000-0000-0000-000000000000",
        )


@pytest.mark.parametrize(
    ("status", "phase"),
    [
        ("blocked", "m05_pinvi_receipt_blocked"),
        ("unexpected", "m05_pinvi_receipt_invalid"),
    ],
)
def test_pinvi_receipt_non_applied_status_is_terminal(
    monkeypatch: pytest.MonkeyPatch, status: str, phase: str
) -> None:
    """PinVi detail 계약에 없는 pending retry가 terminal 상태를 timeout으로 감추지 않는다."""

    driver = _driver()
    monkeypatch.setattr(
        driver,
        "_http_json",
        lambda *_args, **_kwargs: {"data": {"status": status}},
    )

    with pytest.raises(driver._PhaseError, match=phase):
        driver._wait_for_pinvi_receipt(
            api_url="http://127.0.0.1:13701",
            opener=object(),
            event_id="00000000-0000-0000-0000-000000000000",
        )


def test_execution_registry_gate_precedes_the_m05_ledger_claim() -> None:
    """현재 exact execution은 ledger claim 전에 terminal로 거절한다."""

    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )

    gate = source.index("_assert_current_m05_execution_is_runnable(expected_revision)")
    ledger_directory = source.index("_LEDGER.mkdir(mode=0o700, parents=True, exist_ok=True)")
    ledger_claim = source.index("claim_m05_isolated_harness_ledger(ledger_root=_LEDGER, plan=plan)")

    assert gate < ledger_directory
    assert gate < ledger_claim


def test_execution_registry_gate_refuses_the_current_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """다른 Manager revision도 unconditional block을 실행권으로 바꾸지 못한다."""

    driver = _driver()

    class _SourceRegistry:
        pinset_sha256 = driver.PINNED_RUNTIME_RELEASE.pinset_sha256
        map_revision = driver.PINNED_RUNTIME_RELEASE.source_for("map").revision
        pinvi_revision = driver.PINNED_RUNTIME_RELEASE.source_for("pinvi").revision

    class _TerminalExecutionRegistry:
        def current_matches(self, **_kwargs: object) -> bool:
            return True

        def is_unconditionally_blocked_current(self) -> bool:
            return True

    monkeypatch.setattr(driver, "load_runtime_pin_registry", lambda: _SourceRegistry())
    monkeypatch.setattr(
        driver, "load_runtime_execution_registry", lambda: _TerminalExecutionRegistry()
    )

    with pytest.raises(driver._PhaseError, match="terminal_execution_blocked"):
        driver._assert_current_m05_execution_is_runnable("a" * 40)


def test_infra_terminal_leaves_a_phase_scoped_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """인프라 phase 실패는 scoped 기록이다 — execution을 소각하지 않는다.

    종전에는 phase를 넘기지 않아 모든 terminal이 무조건 차단이 됐고, 인프라 실패가
    acceptance 실패와 같은 형벌(3-repo 회전)을 받았다. terminal 27개 중 본문 도달
    0건이 그 결과다.
    """
    driver = _driver()
    seen: dict[str, object] = {}

    class _SourceRegistry:
        pinset_sha256 = driver.PINNED_RUNTIME_RELEASE.pinset_sha256
        map_revision = driver.PINNED_RUNTIME_RELEASE.source_for("map").revision
        pinvi_revision = driver.PINNED_RUNTIME_RELEASE.source_for("pinvi").revision

    class _ExecutionRegistry:
        def current_matches(self, **_kwargs: object) -> bool:
            return True

    class _UpdatedRegistry:
        def has_block_for_current(self, *, phase: str | None = None) -> bool:
            return True

    def block(**kwargs: object) -> _UpdatedRegistry:
        seen.update(kwargs)
        return _UpdatedRegistry()

    monkeypatch.setattr(driver, "load_runtime_pin_registry", lambda: _SourceRegistry())
    monkeypatch.setattr(driver, "load_runtime_execution_registry", lambda: _ExecutionRegistry())
    monkeypatch.setattr(driver, "block_current_execution", block)
    monkeypatch.setattr(driver, "write_runtime_execution_registry", lambda _registry: None)

    assert driver._block_terminal_m05_execution(
        "map_health_transport_failed", expected_manager_revision="a" * 40
    ) is True
    assert seen["phase"] == "map_health_transport_failed"
    assert seen["reason"] == "M05 isolated one-shot terminal: map_health_transport_failed"


@pytest.mark.parametrize(
    "phase",
    ["ledger_claim", "m04_m05_e2e", "m05_case_invalid", "m04_fixture_http_failed"],
)
def test_acceptance_terminal_stays_unconditional(
    phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """acceptance 본문·ledger claim 실패는 여전히 무조건 소각이다(phase=None).

    "acceptance 본문은 정확히 한 번"이라는 one-shot 성질은 phase-scoped 완화의
    대상이 아니다 — 완화되는 것은 인프라 phase뿐이다.
    """
    driver = _driver()
    seen: dict[str, object] = {}

    class _SourceRegistry:
        pinset_sha256 = driver.PINNED_RUNTIME_RELEASE.pinset_sha256
        map_revision = driver.PINNED_RUNTIME_RELEASE.source_for("map").revision
        pinvi_revision = driver.PINNED_RUNTIME_RELEASE.source_for("pinvi").revision

    class _ExecutionRegistry:
        def current_matches(self, **_kwargs: object) -> bool:
            return True

    class _UpdatedRegistry:
        def has_block_for_current(self, *, phase: str | None = None) -> bool:
            return True

        def is_unconditionally_blocked_current(self) -> bool:
            return True

    def block(**kwargs: object) -> _UpdatedRegistry:
        seen.update(kwargs)
        return _UpdatedRegistry()

    monkeypatch.setattr(driver, "load_runtime_pin_registry", lambda: _SourceRegistry())
    monkeypatch.setattr(driver, "load_runtime_execution_registry", lambda: _ExecutionRegistry())
    monkeypatch.setattr(driver, "block_current_execution", block)
    monkeypatch.setattr(driver, "write_runtime_execution_registry", lambda _registry: None)

    assert driver._block_terminal_m05_execution(
        phase, expected_manager_revision="a" * 40
    ) is True
    assert seen["phase"] is None


@pytest.mark.parametrize(
    "phase",
    ["runtime_container_identity_invalid", "runtime_http_contract_failed"],
)
def test_body_entered_failure_is_forced_unconditional(
    phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """본문 진입 이후에는 인프라형 phase 이름의 실패도 무조건 소각한다(R1-S4/R2-S4).

    본문 내부 helper(_container_id 등)는 인프라형 phase로 _PhaseError를 던진다 —
    force_unconditional 없이는 그 실패가 scoped 기록으로 강등돼 mutating 본문이
    재실행될 수 있다(one-shot 위반).
    """
    driver = _driver()
    seen: dict[str, object] = {}

    class _SourceRegistry:
        pinset_sha256 = driver.PINNED_RUNTIME_RELEASE.pinset_sha256
        map_revision = driver.PINNED_RUNTIME_RELEASE.source_for("map").revision
        pinvi_revision = driver.PINNED_RUNTIME_RELEASE.source_for("pinvi").revision

    class _ExecutionRegistry:
        def current_matches(self, **_kwargs: object) -> bool:
            return True

    class _UpdatedRegistry:
        def has_block_for_current(self, *, phase: str | None = None) -> bool:
            return True

        def is_unconditionally_blocked_current(self) -> bool:
            return True

    def block(**kwargs: object) -> _UpdatedRegistry:
        seen.update(kwargs)
        return _UpdatedRegistry()

    monkeypatch.setattr(driver, "load_runtime_pin_registry", lambda: _SourceRegistry())
    monkeypatch.setattr(driver, "load_runtime_execution_registry", lambda: _ExecutionRegistry())
    monkeypatch.setattr(driver, "block_current_execution", block)
    monkeypatch.setattr(driver, "write_runtime_execution_registry", lambda _registry: None)

    assert driver._block_terminal_m05_execution(
        phase, expected_manager_revision="a" * 40, force_unconditional=True
    ) is True
    assert seen["phase"] is None


def test_cleanup_failure_does_not_downgrade_an_unconditional_phase() -> None:
    """cleanup 실패가 본문/ledger 실패 표면을 강등하지 못한다(R1-S4).

    guard 바로 다음 실행문이 cleanup overwrite인지 소스에서 확인한다 —
    본문 진입(body_entered) 또는 무조건-급 phase에서는 overwrite가 없어야 한다.
    """
    source = (
        Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py"
    ).read_text(encoding="utf-8")
    guard = (
        "if not body_entered and _terminal_block_phase(phase) is not None:"
    )
    assert guard in source
    tail = source[source.index(guard) + len(guard):]
    statements = [
        line.strip()
        for line in tail.splitlines()[1:8]
        if line.strip() and not line.strip().startswith("#")
    ]
    assert statements[0] == 'phase = "runtime_cleanup_failed"'


def test_preclaim_exception_writes_a_nonterminal_fixed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """unknown exception은 raw 없이 현재 admission 경계로 수렴한다."""

    driver = _driver()
    monkeypatch.setattr(
        driver,
        "_validate_trusted_release",
        lambda _expected: (_ for _ in ()).throw(RuntimeError("discarded")),
    )
    monkeypatch.setattr(
        driver,
        "_block_terminal_m05_execution",
        lambda *_args, **_kwargs: pytest.fail("preclaim failure must not block execution"),
    )

    assert driver.main("a" * 40, tmp_path) == 1
    receipt = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

    assert receipt["status"] == "preflight_rejected"
    assert receipt["phase"] == "admission"
    assert receipt["driver_phase"] == "admission"
    assert "discarded" not in json.dumps(receipt, sort_keys=True)


def test_cleanup_boundary_marks_ordinary_exceptions_for_fixed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cleanup의 OSError도 driver raw-output 부재로 전파하지 않는다."""

    driver = _driver()
    cleanup = (tmp_path, "m05i-test", tmp_path / "runtime.env", (tmp_path / "x.yml",), ())
    monkeypatch.setattr(
        driver,
        "_cleanup_project",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("discarded")),
    )

    assert driver._cleanup_temporary_resources(
        map_cleanup=cleanup,
        pinvi_cleanup=None,
        private_files=(),
    ) == (False, True)


def test_unexpected_cleanup_keeps_the_fixed_cleanup_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cleanup boundary의 ordinary exception은 generic phase로 덮어쓰지 않는다."""

    driver = _driver()
    monkeypatch.setattr(
        driver,
        "_validate_trusted_release",
        lambda _expected: (_ for _ in ()).throw(driver._PhaseError("admission")),
    )
    monkeypatch.setattr(
        driver,
        "_cleanup_temporary_resources",
        lambda **_kwargs: (False, True),
    )
    monkeypatch.setattr(driver, "_block_terminal_m05_execution", lambda *_args, **_kwargs: True)

    assert driver.main("a" * 40, tmp_path) == 1
    receipt = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

    assert receipt["phase"] == "runtime_cleanup_failed"
    assert receipt["driver_phase"] == "runtime_cleanup_failed"


def test_preclaim_phase_error_does_not_attempt_a_terminal_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ledger 이전의 contract failure는 block helper 자체를 호출하지 않는다."""

    driver = _driver()
    monkeypatch.setattr(
        driver,
        "_validate_trusted_release",
        lambda _expected: (_ for _ in ()).throw(driver._PhaseError("admission_failed")),
    )
    monkeypatch.setattr(
        driver,
        "_block_terminal_m05_execution",
        lambda *_args, **_kwargs: pytest.fail("preclaim failure must not block execution"),
    )

    assert driver.main("a" * 40, tmp_path) == 1
    receipt = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

    assert receipt["status"] == "preflight_rejected"
    assert receipt["phase"] == "admission_failed"
    assert receipt["driver_phase"] == "admission_failed"


def test_root_launcher_checks_registry_before_creating_an_output_leaf() -> None:
    """terminal direct launch은 새 leaf·driver·ledger를 만들기 전에 끝난다."""

    launcher = (Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once").read_text(
        encoding="utf-8"
    )

    assert launcher.index('"$ktdctl" pin verify --json >/dev/null 2>&1') < launcher.index(
        'install -d -o root -g root -m 0700 "$output_dir"'
    )


def test_root_launcher_defaults_to_forensic_capture_with_explicit_opt_out() -> None:
    """원문 보존이 기본값이다 — 관측 결핍이 후보 예산을 소비했다(감사 I-2).

    4개 candidate를 태운 `ports: !reset`은 stderr 한 번이면 즉시 보였을 값이었다.
    보존 대상은 bounded stderr뿐이고 root 0600 leaf를 벗어나지 않으며, 끄는 것은
    caller environment가 아니라 명시 launcher argument로만 가능하다.
    """
    launcher = (Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once").read_text(
        encoding="utf-8"
    )

    # 열 0의 대입만 기본값이다 — 들여쓰기된 호환 분기(`  forensic_capture=1`)와
    # 구분하지 않으면 기본값을 0으로 되돌려도 이 단언이 통과한다.
    assert "\nforensic_capture=1\n" in launcher, "기본값이 보존이어야 한다"
    assert "\nforensic_capture=0\n" not in launcher
    assert '"$1" == "--no-forensic-capture"' in launcher, "명시 opt-out이 있어야 한다"
    assert "export KTDM_M05_FORENSIC_CAPTURE=1" in launcher
    assert "unset KTDM_M05_FORENSIC_CAPTURE" in launcher
    assert '"${launcher_arguments[@]}"' in launcher


def test_root_launcher_forensic_default_is_behavioral_not_textual() -> None:
    """launcher를 실제 실행(bash -x)해 기본값 대입을 트레이스로 확인한다(R2-S9).

    문구 단언은 주석/데드 브랜치로 우회된다 — 여기서는 non-root 실행의 실제
    트레이스에서 `forensic_capture=1`(기본) / `=0`(--no-forensic-capture)을 본다.
    non-root라서 launcher는 root 검사에서 exit 2로 멈춘다(driver 실행 없음).
    """
    if os.name != "posix" or os.geteuid() == 0:
        pytest.skip("non-root POSIX에서만 안전하게 실행할 수 있다")
    launcher_path = Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once"

    default_run = subprocess.run(
        ["bash", "-x", str(launcher_path), "a" * 40, "/nonexistent-m05-out"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert default_run.returncode == 2
    assert "must run as root" in default_run.stderr
    assert "+ forensic_capture=1" in default_run.stderr
    assert "+ forensic_capture=0" not in default_run.stderr

    opt_out_run = subprocess.run(
        [
            "bash",
            "-x",
            str(launcher_path),
            "--no-forensic-capture",
            "a" * 40,
            "/nonexistent-m05-out",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert opt_out_run.returncode == 2
    assert "+ forensic_capture=0" in opt_out_run.stderr


def test_root_launcher_blocks_and_writes_a_fixed_envelope_when_driver_result_is_unavailable() -> None:
    """driver raw output 부재도 terminal evidence 없이 재시도할 수 없게 고정한다."""

    launcher = (Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once").read_text(
        encoding="utf-8"
    )

    assert "launcher_safe_result_unavailable" in launcher
    assert '"$ktdctl" pin block-execution' in launcher
    assert "launcher-result.json" in launcher
    assert ">/dev/null 2>&1" in launcher[launcher.index("m05_isolated_e2e.py") :]
    assert "stderr.log" not in launcher[launcher.index("driver_status=") :]
    block_start = launcher.index("has_unconditional_terminal_execution_block() {")
    block_end = launcher.index('install -d -o root -g root -m 0700 "$output_dir"')
    block_check = launcher[block_start:block_end]
    assert "/usr/bin/python3 -I -S -c" in block_check
    assert "<<'PY'" not in block_check


def test_root_launcher_accepts_only_the_launch_snapshot_and_fixed_schema() -> None:
    """rotation race와 임의 driver envelope은 fresh candidate 근거가 될 수 없다."""

    launcher = (Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once").read_text(
        encoding="utf-8"
    )

    assert "initial_snapshot" in launcher
    assert "stable_snapshot" in launcher
    assert "post_snapshot" in launcher
    assert '"$post_snapshot" == "$initial_snapshot"' in launcher
    assert 'value.get("pinset_sha256") != expected_pinset' in launcher
    assert 'value.get("execution_identity_sha256") != expected_execution' in launcher
    assert 'value.get("status") not in {"passed", "blocked", "preflight_rejected"}' in launcher
    assert 'if value["status"] == "preflight_rejected"' in launcher
    assert 'receipt_validation_status" == 4' in launcher
    assert "PREFLIGHT_REJECTED_PHASES" in launcher
    assert 'value["phase"] not in PREFLIGHT_REJECTED_PHASES' in launcher
    assert "if set(value) != expected_keys:" in launcher
    assert "if [[ ! -e \"$launcher_result_path\"" in launcher


def test_root_launcher_accepts_every_runtime_setup_subphase() -> None:
    """driver의 모든 public terminal phase는 launcher도 exact하게 수용한다."""

    launcher = (Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once").read_text(
        encoding="utf-8"
    )
    driver_source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )

    def frozenset_literal(source: str, name: str) -> set[str]:
        tree = ast.parse(source)
        for statement in tree.body:
            if not isinstance(statement, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == name for target in statement.targets
            ):
                continue
            assert isinstance(statement.value, ast.Call)
            assert isinstance(statement.value.func, ast.Name)
            assert statement.value.func.id == "frozenset"
            assert len(statement.value.args) == 1
            value = ast.literal_eval(statement.value.args[0])
            assert isinstance(value, set)
            assert all(isinstance(item, str) for item in value)
            return value
        raise AssertionError(f"{name} literal was not found")

    driver_phases = frozenset_literal(driver_source, "_PUBLIC_TERMINAL_PHASES")
    launcher_start = launcher.index("PHASES = frozenset(")
    launcher_end = launcher.index("FRESH_INIT_REASONS =", launcher_start)
    launcher_phases = frozenset_literal(launcher[launcher_start:launcher_end], "PHASES")

    assert launcher_phases == driver_phases | {"completed"}
    # blocked receipt는 scoped 기록으로도 durable하다(R1-S1) — 무조건 기록만
    # 요구하면 launcher가 모든 인프라 실패를 fallback에서 무조건 차단으로 승격한다.
    accepted_block = 'if [[ "$receipt_validation_status" == 3 ]] && has_any_terminal_execution_block; then'
    fallback = 'if ! has_unconditional_terminal_execution_block; then'
    assert accepted_block in launcher
    assert fallback in launcher
    assert launcher.index(accepted_block) < launcher.index(fallback)
    # scoped predicate는 phase 필터가 없어야 한다(identity/pinset/revision만 결박).
    any_start = launcher.index("has_any_terminal_execution_block() {")
    any_end = launcher.index("has_unconditional_terminal_execution_block() {")
    any_block = launcher[any_start:any_end]
    assert 'entry.get("phase")' not in any_block
    assert 'entry.get("execution_identity_sha256") == execution' in any_block


def test_free_ports_uses_the_standard_ss_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _driver()
    commands: list[tuple[str, ...]] = []

    def fake_command(*args: str, **_kwargs: object) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(driver, "_command", fake_command)

    ports = driver._free_ports("a" * 32)

    # 전 포트가 비-ephemeral 대역(20000-29999)이어야 한다 — ephemeral 대역은
    # listening 검사(ss -ltn)를 통과해도 outbound 선점으로 bind가 깨진다.
    assert all(20000 <= port < 30000 for port in ports.values())
    assert set(ports) == {
        "map_api",
        "map_dagster",
        "map_postgres",
        "map_rustfs",
        "map_rustfs_console",
        "pinvi_api",
        "pinvi_web",
        "pinvi_rustfs",
        "pinvi_rustfs_console",
        "pinvi_dagster",
        "pinvi_cadvisor",
        "pinvi_prometheus",
        "pinvi_grafana",
    }
    assert len(set(ports.values())) == len(ports)
    assert commands
    assert {command[0] for command in commands} == {"/usr/bin/ss"}


def test_cleanup_includes_map_fresh_init_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver()
    compose_arguments: list[tuple[str, ...]] = []
    commands: list[tuple[str, ...]] = []

    def fake_compose(*_args: object, **kwargs: object) -> str:
        arguments = kwargs["arguments"]
        assert isinstance(arguments, tuple)
        compose_arguments.append(arguments)
        return ""

    def fake_command(*args: str, **_kwargs: object) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(driver, "_compose", fake_compose)
    monkeypatch.setattr(driver, "_command", fake_command)

    driver._cleanup_project(
        root=tmp_path,
        project="m05i-map-a" * 4,
        env_file=tmp_path / "map.env",
        files=(tmp_path / "docker-compose.yml",),
        profiles=("fresh-init",),
    )

    assert compose_arguments == [
        ("--profile", "fresh-init", "down", "--volumes", "--remove-orphans")
    ]
    assert len(commands) == 3


def test_compose_records_the_supplied_fixed_failure_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver()

    def fail_command(*_args: str, **_kwargs: object) -> str:
        raise driver._PhaseError("runtime_command_failed")

    monkeypatch.setattr(driver, "_command", fail_command)

    with pytest.raises(driver._PhaseError, match="map_postgres_start_failed") as error:
        driver._compose(
            root=tmp_path,
            project="m05i-map",
            env_file=tmp_path / "map.env",
            files=(tmp_path / "docker-compose.yml",),
            arguments=("up", "postgres"),
            failure_phase="map_postgres_start_failed",
        )

    assert error.value.diagnostic is None


def test_compose_preserves_only_the_fixed_exit_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver()

    def fail_command(*_args: str, **_kwargs: object) -> str:
        raise driver._PhaseError(
            "runtime_command_failed", diagnostic="pre_root_state_invalid"
        )

    monkeypatch.setattr(driver, "_command", fail_command)

    with pytest.raises(driver._PhaseError, match="map_fresh_init_failed") as error:
        driver._compose(
            root=tmp_path,
            project="m05i-map",
            env_file=tmp_path / "map.env",
            files=(tmp_path / "docker-compose.yml",),
            arguments=("run", "db-application-schema-fresh-300"),
            failure_phase="map_fresh_init_failed",
            failure_exit_diagnostics={45: "pre_root_state_invalid"},
        )

    assert error.value.diagnostic == "pre_root_state_invalid"


def test_command_accepts_only_a_declared_failure_exit_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _driver()

    monkeypatch.setattr(
        driver.subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 45),
    )

    with pytest.raises(driver._PhaseError, match="runtime_command_failed") as error:
        driver._command(
            "/usr/bin/false", failure_exit_diagnostics={45: "pre_root_state_invalid"}
        )

    assert error.value.diagnostic == "pre_root_state_invalid"


def test_compose_config_failure_evidence_is_safe_by_default_and_forensic_on_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver()

    monkeypatch.setattr(
        driver.subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 2, stdout="", stderr="exact compose parser failure\n"
        ),
    )
    safe = tmp_path / "safe.json"
    with pytest.raises(driver._PhaseError, match="runtime_loopback_publish_config_invalid"):
        driver._compose(
            root=tmp_path,
            project="m05i-map",
            env_file=tmp_path / "map.env",
            files=(tmp_path / "docker-compose.yml",),
            arguments=("config", "--format", "json"),
            failure_phase="runtime_loopback_publish_config_invalid",
            failure_evidence_path=safe,
        )
    assert json.loads(safe.read_text(encoding="utf-8")) == {
        "kind": "compose_config",
        "returncode": 2,
        "version": 1,
    }
    assert not safe.with_suffix(".stderr").exists()

    monkeypatch.setenv(driver._FORENSIC_CAPTURE_ENV, "1")
    oversized_stderr = b"x" * (driver._FORENSIC_CAPTURE_LIMIT + 1)

    class FailedCompose:
        stdout = io.BytesIO(b"")
        stderr = io.BytesIO(oversized_stderr)

        def wait(self) -> int:
            return 2

    monkeypatch.setattr(driver.subprocess, "Popen", lambda *_args, **_kwargs: FailedCompose())
    forensic = tmp_path / "forensic.json"
    with pytest.raises(driver._PhaseError, match="runtime_loopback_publish_config_invalid"):
        driver._compose(
            root=tmp_path,
            project="m05i-map",
            env_file=tmp_path / "map.env",
            files=(tmp_path / "docker-compose.yml",),
            arguments=("config", "--format", "json"),
            failure_phase="runtime_loopback_publish_config_invalid",
            failure_evidence_path=forensic,
        )
    assert forensic.with_suffix(".stderr").read_bytes() == b"x" * driver._FORENSIC_CAPTURE_LIMIT


def test_compose_config_output_is_stream_bounded_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver()
    oversized_stdout = b"x" * (driver._COMPOSE_CONFIG_OUTPUT_LIMIT + 1)

    class OversizedCompose:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(oversized_stdout)

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(
        driver.subprocess, "Popen", lambda *_args, **_kwargs: OversizedCompose()
    )
    safe = tmp_path / "oversized.json"
    with pytest.raises(driver._PhaseError, match="runtime_loopback_publish_config_invalid"):
        driver._compose(
            root=tmp_path,
            project="m05i-map",
            env_file=tmp_path / "map.env",
            files=(tmp_path / "docker-compose.yml",),
            arguments=("config", "--format", "json"),
            capture=True,
            failure_phase="runtime_loopback_publish_config_invalid",
            output_evidence_path=safe,
        )
    assert json.loads(safe.read_text(encoding="utf-8")) == {
        "kind": "compose_config_output",
        "truncated": True,
        "version": 1,
    }
    assert not safe.with_suffix(".stdout").exists()

    monkeypatch.setenv(driver._FORENSIC_CAPTURE_ENV, "1")
    forensic = tmp_path / "oversized-forensic.json"
    with pytest.raises(driver._PhaseError, match="runtime_loopback_publish_config_invalid"):
        driver._compose(
            root=tmp_path,
            project="m05i-map",
            env_file=tmp_path / "map.env",
            files=(tmp_path / "docker-compose.yml",),
            arguments=("config", "--format", "json"),
            capture=True,
            failure_phase="runtime_loopback_publish_config_invalid",
            output_evidence_path=forensic,
        )
    assert forensic.with_suffix(".stdout").read_bytes() == (
        b"x" * driver._FORENSIC_CAPTURE_LIMIT
    )


def test_nonzero_compose_config_keeps_exit_evidence_when_stdout_is_oversized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver()

    class FailedOversizedCompose:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"x" * (driver._COMPOSE_CONFIG_OUTPUT_LIMIT + 1))

        def wait(self) -> int:
            return 2

    monkeypatch.setattr(
        driver.subprocess, "Popen", lambda *_args, **_kwargs: FailedOversizedCompose()
    )
    command_evidence = tmp_path / "command.json"
    output_evidence = tmp_path / "output.json"
    with pytest.raises(driver._PhaseError, match="runtime_loopback_publish_config_invalid"):
        driver._compose(
            root=tmp_path,
            project="m05i-map",
            env_file=tmp_path / "map.env",
            files=(tmp_path / "docker-compose.yml",),
            arguments=("config", "--format", "json"),
            capture=True,
            failure_phase="runtime_loopback_publish_config_invalid",
            failure_evidence_path=command_evidence,
            output_evidence_path=output_evidence,
        )
    assert json.loads(command_evidence.read_text(encoding="utf-8")) == {
        "kind": "compose_config",
        "returncode": 2,
        "version": 1,
    }
    assert not output_evidence.exists()


def test_generic_command_failure_evidence_is_safe_by_default_and_bounded_on_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _driver()
    safe = tmp_path / "command.json"
    driver._write_command_failure_evidence(
        safe, returncode=17, stderr=b"private command error"
    )
    assert json.loads(safe.read_text(encoding="utf-8")) == {
        "kind": "runtime_command",
        "returncode": 17,
        "version": 1,
    }
    assert not safe.with_suffix(".stderr").exists()

    monkeypatch.setenv(driver._FORENSIC_CAPTURE_ENV, "1")
    forensic = tmp_path / "command-forensic.json"
    driver._write_command_failure_evidence(
        forensic,
        returncode=17,
        stderr=b"x" * (driver._FORENSIC_CAPTURE_LIMIT + 1),
    )
    assert forensic.with_suffix(".stderr").read_bytes() == (
        b"x" * driver._FORENSIC_CAPTURE_LIMIT
    )


def test_forensic_capture_scrubs_raw_secret_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """opt-in 캡처 leaf에도 raw 비밀값은 남지 않는다(R1-S9 content-scrub).

    자식 프로세스가 비밀값을 stderr/stdout에 에코해도 _RAW_ENV_NAMES의 현재
    값은 마커로 치환된다. 크기 제한은 총량 방어일 뿐 내용 방어가 아니다.
    """
    driver = _driver()
    secret = "raw-secret-value-0123456789abcdef"
    monkeypatch.setenv("M05_PINVI_PASSWORD", secret)
    monkeypatch.setenv(driver._FORENSIC_CAPTURE_ENV, "1")

    stderr_leaf = tmp_path / "command.json"
    driver._write_command_failure_evidence(
        stderr_leaf,
        returncode=17,
        stderr=b"login failed for " + secret.encode() + b" retrying",
    )
    captured = stderr_leaf.with_suffix(".stderr").read_bytes()
    assert secret.encode() not in captured
    assert b"[scrubbed:M05_PINVI_PASSWORD]" in captured

    stdout_leaf = tmp_path / "compose-output.json"
    driver._write_compose_output_evidence(
        stdout_leaf, output="services: {password: " + secret + "}"
    )
    captured_out = stdout_leaf.with_suffix(".stdout").read_bytes()
    assert secret.encode() not in captured_out
    assert b"[scrubbed:M05_PINVI_PASSWORD]" in captured_out

    # 8바이트 미만 값은 우연 일치 훼손을 피하기 위해 치환하지 않는다.
    monkeypatch.setenv("M05_PINVI_EMAIL", "a@b.c")
    tiny = tmp_path / "tiny.json"
    driver._write_command_failure_evidence(tiny, returncode=3, stderr=b"a@b.c seen")
    assert tiny.with_suffix(".stderr").read_bytes() == b"a@b.c seen"


def test_map_fresh_diagnostic_runner_uses_exit_codes_without_output() -> None:
    driver = _driver()

    runner = driver._map_fresh_init_diagnostic_runner()
    entrypoint = driver._map_fresh_init_diagnostic_entrypoint()

    assert "print(" not in runner
    assert "sys.stderr" not in runner
    assert "FreshMigrationError" in runner
    assert "RuntimePrivilegeReconciliationError" in runner
    assert "SQLAlchemyError" in runner
    assert "CommandError" in runner
    assert "baseline_reference_invalid" not in runner
    assert "fresh 300 destination reference manifest is invalid" in runner
    assert "raise SystemExit" in runner
    assert "base64.b64decode" in entrypoint


def _map_fresh_runner_exit_code(
    driver: ModuleType, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> int:
    class FreshMigrationError(RuntimeError):
        pass

    async def migrate() -> None:
        raise error

    fake_runpy = ModuleType("runpy")
    fake_runpy.run_path = lambda *_args, **_kwargs: {
        "FreshMigrationError": FreshMigrationError,
        "_migrate": migrate,
        "_parse_args": lambda _arguments: ("migrate", None),
    }
    monkeypatch.setitem(sys.modules, "runpy", fake_runpy)

    with pytest.raises(SystemExit) as stopped:
        exec(compile(driver._map_fresh_init_diagnostic_runner(), "<runner>", "exec"))

    assert isinstance(stopped.value.code, int)
    return stopped.value.code


def test_map_fresh_diagnostic_runner_maps_exact_prefix_and_unknown_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _driver()

    assert _map_fresh_runner_exit_code(
        driver,
        monkeypatch,
        RuntimeError("fresh 300 destination reference manifest is invalid"),
    ) == 51
    assert _map_fresh_runner_exit_code(
        driver, monkeypatch, RuntimeError("unlisted Map runtime failure")
    ) == 48
    assert _map_fresh_runner_exit_code(driver, monkeypatch, ValueError("ignored")) == 127


def test_fixture_uses_only_dagster_runtime_dsn_and_provider_contract() -> None:
    fixture = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_fixture.py").read_text(
        encoding="utf-8"
    )
    driver_source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )
    fixture_env_start = driver_source.index("_write_private_text(\n            fixture_env,")
    fixture_env_end = driver_source.index("        # API에는", fixture_env_start)
    fixture_env = driver_source[fixture_env_start:fixture_env_end]

    assert "KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN" not in fixture
    assert "KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN" not in fixture_env
    assert "KOR_TRAVEL_MAP_PG_DSN" in fixture_env
    assert "assert_runtime_db_privilege_boundary" in fixture
    assert "AsyncKorTravelMapClient" in fixture
    assert "SET LOCAL ROLE" not in fixture
    assert "INSERT INTO" not in fixture


def test_pinvi_runtime_command_uses_bounded_generic_failure_evidence() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )

    assert 'for action in ("build", "up"):' in source
    assert 'runtime / f"pinvi-runtime-{action}-error.json"' in source
    assert "_write_command_failure_evidence(" in source


def test_manager_does_not_require_pinvi_crypto_dependency() -> None:
    pyproject = (Path(__file__).resolve().parents[2] / "backend/pyproject.toml").read_text(
        encoding="utf-8"
    )

    assert "cryptography" not in pyproject


def test_pair_preflight_runs_before_the_one_shot_ledger_claim() -> None:
    """invalid source pair는 ledger를 소비하지 않아 corrected pair를 막지 않는다."""

    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )
    pair_preflight = source.index("pair, service_openapi_sha256, service_source_revision = _pair(")
    admission_contract = source.index("_assert_pinvi_manager_admission_contract(pinvi_root)")
    ledger_claim = source.index("claim_m05_isolated_harness_ledger(ledger_root=_LEDGER, plan=plan)")

    assert pair_preflight < admission_contract < ledger_claim


def test_isolated_map_override_replaces_the_api_publish_instead_of_resetting_it() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )
    override_start = source.index('            "  api:",')
    override_end = source.index('            "  frontend:",', override_start)
    api_override = source[override_start:override_end]

    assert '"    ports: !override",' in api_override
    assert '"    ports: !reset",' not in api_override


def test_isolated_map_network_allowlists_the_bridge_gateway_for_host_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _driver()
    monkeypatch.setattr(driver, "_command", lambda *_args, **_kwargs: "")

    subnet, gateway, api, frontend = driver._map_network_addresses("a" * 32)

    # /28 확장 근거는 driver의 _map_network_addresses 주석 참조(app-api join +
    # provider fixture까지 담아야 IPAM 고갈이 없다 — 2026-09-01 적대 리뷰).
    assert subnet == "172.29.170.0/28"
    # 규칙 자체를 고정한다(값 리터럴이 아니라): gateway는 첫 host, 정적
    # api/frontend는 상단 두 host — 하단은 동적 할당(postgres/rustfs 등) 몫.
    # subnet 크기가 바뀌어도 .2/.3 회귀(동적 선점 충돌)를 되박을 수 없다.
    subnet_hosts = list(ipaddress.ip_network(subnet).hosts())
    assert (gateway, api, frontend) == (
        str(subnet_hosts[0]),
        str(subnet_hosts[-1]),
        str(subnet_hosts[-2]),
    )
    assert api not in {str(host) for host in subnet_hosts[:3]}
    assert frontend not in {str(host) for host in subnet_hosts[:3]}
    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )
    assert '"{map_gateway_ip}/32"' in source


def test_isolated_pinvi_api_uses_the_private_map_network_not_host_loopback() -> None:
    """PinVi worker의 Map service request는 loopback-only publish를 우회하지 않는다."""

    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )
    pinvi_env_start = source.index("_write_private_text(\n            pinvi_env,")
    pinvi_env_end = source.index("        pinvi_override_lines =", pinvi_env_start)
    pinvi_env = source[pinvi_env_start:pinvi_env_end]
    override_start = source.index("        pinvi_override_lines =", pinvi_env_end)
    override_end = source.index("        _write_private_text(pinvi_override", override_start)
    override = source[override_start:override_end]

    assert 'PINVI_KOR_TRAVEL_MAP_API_BASE_URL=http://{map_api_ip}:13701' in pinvi_env
    assert 'PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL=http://{map_api_ip}:13701' in pinvi_env
    assert "host.docker.internal:{ports['map_api']}" not in pinvi_env
    assert '"      default: {}"' in override
    assert '"      m05-map: {}"' in override
    assert '"    external: true"' in override
    assert 'f"    name: {plan.map_network}"' in override


def test_root_launcher_checks_the_m05_pair_before_creating_an_output_leaf() -> None:
    """wrong Map/PinVi provenance은 execution terminal·ledger를 소비하지 않는다."""

    launcher = (Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once").read_text(
        encoding="utf-8"
    )

    pair_preflight = launcher.index("m05_isolated_e2e.py \\")
    output_leaf = launcher.index('install -d -o root -g root -m 0700 "$output_dir"')

    assert pair_preflight < output_leaf


def test_preflight_rejects_a_pair_without_blocking_the_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """launcher preflight은 diagnostic-free failure만 반환하고 mutation을 하지 않는다."""

    driver = _driver()
    calls: list[str] = []
    monkeypatch.setattr(driver, "_validate_trusted_release", lambda _expected: calls.append("release"))
    monkeypatch.setattr(
        driver, "_assert_current_m05_execution_is_runnable", lambda _expected: calls.append("execution")
    )
    monkeypatch.setattr(
        driver,
        "_source_pair_preflight",
        lambda: (_ for _ in ()).throw(driver._PhaseError("pair_contract_invalid")),
    )

    assert driver.preflight("a" * 40) == 1
    assert calls == ["release", "execution"]


def test_driver_pair_failure_before_ledger_never_blocks_the_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launcher preflight 뒤 source cache가 달라져도 terminal 실행권을 소비하지 않는다."""

    driver = _driver()
    calls: list[str] = []

    class _Current:
        execution_identity_sha256 = "b" * 64

    class _Execution:
        current = _Current()

    class _Plan:
        execution_identity_sha256 = "b" * 64
        labels: dict[str, str] = {}
        map_network = "test-map-network"
        map_project = "test-map-project"

    class _Pair:
        map_source_revision = "c" * 40
        pinvi_source_revision = "d" * 40

    monkeypatch.setattr(driver, "_validate_trusted_release", lambda _expected: None)
    monkeypatch.setattr(
        driver, "_assert_current_m05_execution_is_runnable", lambda _expected: _Execution()
    )
    monkeypatch.setattr(driver, "_root_directory", lambda _path: None)
    monkeypatch.setattr(driver, "_root_file", lambda _path, **_kwargs: None)
    monkeypatch.setattr(driver, "_LEDGER", tmp_path / "ledger")
    monkeypatch.setattr(driver, "M05IsolatedHarnessPlan", lambda *_args: _Plan())
    monkeypatch.setattr(
        driver,
        "_source_pair_preflight",
        lambda: (_ for _ in ()).throw(driver._PhaseError("pair_contract_invalid")),
    )
    monkeypatch.setattr(
        driver,
        "_block_terminal_m05_execution",
        lambda *_args, **_kwargs: calls.append("blocked") or True,
    )

    assert driver.main("a" * 40, tmp_path) == 1
    receipt = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

    assert calls == []
    assert receipt["status"] == "preflight_rejected"
    assert receipt["phase"] == "pair_contract_invalid"


def _write_map_application_graph(root: Path, *, head: str = "300") -> None:
    """test double의 `map_root`에 Map application migration graph를 놓는다.

    driver는 `source_materialization` phase에서 이 파일을 읽어
    `KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD`를 유도한다. 종전에는 리터럴 `300`이라
    fake `map_root`가 비어 있어도 통과했지만, 그 리터럴이 곧 "Map이 migration을 하나
    더하면 API 컨테이너가 기동을 거부한다"는 뜻이었다.

    실제 materialize된 source에는 이 파일이 **항상** 있다. double을 그에 맞춘다.
    """
    package = root / "src" / "kortravelmap"
    package.mkdir(parents=True, exist_ok=True)
    (package / "_application_migration_graph.json").write_text(
        json.dumps(
            {
                "schema": "kor-travel-map.application-migration-graph.v1",
                "revisions": [{"revision": head, "down_revision": []}],
            }
        ),
        encoding="utf-8",
    )


def test_ledger_claim_attempt_failure_blocks_the_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O_EXCL create 뒤 fsync가 실패해 ledger가 남을 수 있는 경계는 fail-close한다."""

    driver = _driver()
    calls: list[str] = []

    class _Current:
        execution_identity_sha256 = "b" * 64

    class _Execution:
        current = _Current()

    class _Plan:
        execution_identity_sha256 = "b" * 64
        labels: dict[str, str] = {}
        map_network = "test-map-network"
        map_project = "test-map-project"

    class _Pair:
        map_source_revision = "c" * 40
        pinvi_source_revision = "d" * 40

    monkeypatch.setattr(driver, "_validate_trusted_release", lambda _expected: None)
    monkeypatch.setattr(
        driver, "_assert_current_m05_execution_is_runnable", lambda _expected: _Execution()
    )
    monkeypatch.setattr(driver, "_root_directory", lambda _path: None)
    monkeypatch.setattr(driver, "_root_file", lambda _path, **_kwargs: None)
    monkeypatch.setattr(driver, "_LEDGER", tmp_path / "ledger")
    monkeypatch.setattr(driver, "M05IsolatedHarnessPlan", lambda *_args: _Plan())
    _write_map_application_graph(tmp_path)
    monkeypatch.setattr(
        driver,
        "_source_pair_preflight",
        lambda: (tmp_path, tmp_path, _Pair(), "a" * 64, "b" * 40),
    )
    monkeypatch.setattr(
        driver, "build_m05_isolated_manager_admission", lambda **_kwargs: {}
    )
    compose_arguments: dict[str, object] = {}

    def compose(**kwargs: object) -> str:
        compose_arguments.update(kwargs)
        return "{}"

    monkeypatch.setattr(driver, "_compose", compose)
    monkeypatch.setattr(
        driver, "_assert_rendered_loopback_tcp_publish", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        driver, "_cleanup_temporary_resources", lambda **_kwargs: (False, False)
    )
    monkeypatch.setattr(
        driver,
        "claim_m05_isolated_harness_ledger",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("discarded")),
    )
    monkeypatch.setattr(
        driver,
        "_block_terminal_m05_execution",
        lambda *_args, **_kwargs: calls.append("blocked") or True,
    )

    assert driver.main("a" * 40, tmp_path) == 1
    receipt = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

    assert calls == ["blocked"]
    assert receipt["status"] == "blocked"
    assert receipt["phase"] == "ledger_claim"
    assert isinstance(compose_arguments["failure_evidence_path"], Path)
    assert compose_arguments["failure_evidence_path"].name == (
        "rendered-loopback-publish-error.json"
    )


def test_manager_writes_and_passes_the_private_pinvi_admission_not_an_environment_marker() -> None:
    driver = _driver()
    admission = Path("/private/runtime/pinvi-isolated-manager-admission.json")

    environment = driver._pinvi_manager_admission_environment(
        env_file=Path("/private/runtime/pinvi.env"),
        bootstrap_credential_file=Path("/private/runtime/pinvi-admin.json"),
        project="m05i-pinvi-" + "e" * 32,
        pinvi_source_revision="d" * 40,
        execution_identity_sha256="c" * 64,
        admission_path=admission,
        compose_extra_file=Path("/private/runtime/pinvi.override.yml"),
    )

    assert environment == {
        "PINVI_ENV_FILE": "/private/runtime/pinvi.env",
        # app-api 첫 기동부터 Map network join이 걸리도록 override를 docker-app.sh
        # compose에 겹친다 — reconciliation preflight가 startup에서 Map lease를
        # 소비하므로 override 없는 첫 up은 결정적으로 실패한다(2026-09-01 실측).
        "PINVI_DOCKER_COMPOSE_EXTRA_FILE": "/private/runtime/pinvi.override.yml",
        "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE": "/private/runtime/pinvi-admin.json",
        "PINVI_DOCKER_PROJECT": "m05i-pinvi-" + "e" * 32,
        "PINVI_SOURCE_REVISION": "d" * 40,
        "PINVI_M05_ISOLATED_MANAGER_ADMISSION_PATH": str(admission),
        "PINVI_M05_PINSET_SHA256": PINNED_RUNTIME_RELEASE.pinset_sha256,
        "PINVI_M05_EXECUTION_IDENTITY_SHA256": "c" * 64,
    }
    assert "PINVI_M05_ISOLATED_MANAGER_HARNESS" not in environment
    assert environment["PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE"] == (
        "/private/runtime/pinvi-admin.json"
    )

    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )
    admission_write = source.index("build_m05_isolated_manager_admission(plan=plan, pair=pair)")
    pinvi_up = source.index('str(pinvi_root / "scripts/docker-app.sh"),')

    assert admission_write < pinvi_up
    assert "_pinvi_manager_admission_environment(" in source
    assert '"--isolated-execution-identity-sha256"' in source
    assert "plan.execution_identity_sha256" in source
    assert "PINVI_M05_ISOLATED_MANAGER_HARNESS" not in source


def test_private_json_writer_serializes_immutable_manager_admission(tmp_path: Path) -> None:
    driver = _driver()
    admission = MappingProxyType(
        {
            "kind": "pinvi-m05-isolated-manager-admission-v1",
            "transaction_id": "a" * 32,
            "version": 1,
        }
    )
    path = tmp_path / "pinvi-isolated-manager-admission.json"

    digest = driver._write_private_json(path, admission)

    raw = path.read_bytes()
    assert json.loads(raw) == dict(admission)
    assert digest == hashlib.sha256(raw).hexdigest()
