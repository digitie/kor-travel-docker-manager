from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
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


def test_runtime_setup_uses_ordered_safe_subphases() -> None:
    """setup의 ordinary exception도 raw 없이 다음 source 보정 범위로만 수렴한다."""

    driver = _driver()
    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )
    phases = (
        "runtime_setup_ports",
        "runtime_setup_workspace",
        "runtime_setup_admission",
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


def test_terminal_registry_gate_precedes_the_m05_ledger_claim() -> None:
    """다른 Manager revision도 terminal pinset을 재실행할 수 없어야 한다."""

    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )

    gate = source.index("_assert_current_m05_pinset_is_runnable()")
    ledger_directory = source.index("_LEDGER.mkdir(mode=0o700, parents=True, exist_ok=True)")
    ledger_claim = source.index("claim_m05_isolated_harness_ledger(ledger_root=_LEDGER, plan=plan)")

    assert gate < ledger_directory
    assert gate < ledger_claim


def test_terminal_registry_gate_refuses_the_current_pinset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """다른 Manager revision도 unconditional block을 실행권으로 바꾸지 못한다."""

    driver = _driver()

    class _TerminalRegistry:
        pinset_sha256 = driver.PINNED_RUNTIME_RELEASE.pinset_sha256
        map_revision = driver.PINNED_RUNTIME_RELEASE.source_for("map").revision
        pinvi_revision = driver.PINNED_RUNTIME_RELEASE.source_for("pinvi").revision

        def is_unconditionally_blocked_pinset(self, _pinset_sha256: str) -> bool:
            return True

    monkeypatch.setattr(driver, "load_runtime_pin_registry", lambda: _TerminalRegistry())

    with pytest.raises(driver._PhaseError, match="terminal_pinset_blocked"):
        driver._assert_current_m05_pinset_is_runnable()


def test_terminal_result_blocks_the_exact_current_pinset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """terminal output은 phase-scoped entry가 아닌 unconditional registry block을 남긴다."""

    driver = _driver()
    seen: dict[str, object] = {}

    class _BlockedRegistry:
        def is_unconditionally_blocked_pinset(self, pinset_sha256: str) -> bool:
            return pinset_sha256 == driver.PINNED_RUNTIME_RELEASE.pinset_sha256

    def block(**kwargs: object) -> _BlockedRegistry:
        seen.update(kwargs)
        return _BlockedRegistry()

    monkeypatch.setattr(driver, "block_runtime_pinset", block)

    assert driver._block_terminal_m05_pinset("map_health_transport_failed") is True
    assert seen["pinset_sha256"] == driver.PINNED_RUNTIME_RELEASE.pinset_sha256
    assert seen["map_revision"] == driver.PINNED_RUNTIME_RELEASE.source_for("map").revision
    assert seen["pinvi_revision"] == driver.PINNED_RUNTIME_RELEASE.source_for("pinvi").revision
    assert "phase" not in seen
    assert seen["reason"] == "M05 isolated one-shot terminal: map_health_transport_failed"


def test_unexpected_driver_exception_still_writes_fixed_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """unknown exception은 raw 없이 현재 admission 경계로 수렴한다."""

    driver = _driver()
    monkeypatch.setattr(
        driver,
        "_validate_trusted_release",
        lambda _expected: (_ for _ in ()).throw(RuntimeError("discarded")),
    )
    monkeypatch.setattr(driver, "_block_terminal_m05_pinset", lambda _phase: True)

    assert driver.main("a" * 40, tmp_path) == 1
    receipt = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

    assert receipt["status"] == "blocked"
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
    monkeypatch.setattr(driver, "_block_terminal_m05_pinset", lambda _phase: True)

    assert driver.main("a" * 40, tmp_path) == 1
    receipt = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

    assert receipt["phase"] == "runtime_cleanup_failed"
    assert receipt["driver_phase"] == "runtime_cleanup_failed"


def test_terminal_block_exception_still_writes_fixed_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """registry block 내부 오류도 원문 없이 fixed driver receipt로 수렴한다."""

    driver = _driver()
    monkeypatch.setattr(
        driver,
        "_validate_trusted_release",
        lambda _expected: (_ for _ in ()).throw(driver._PhaseError("admission_failed")),
    )
    monkeypatch.setattr(
        driver,
        "_block_terminal_m05_pinset",
        lambda _phase: (_ for _ in ()).throw(OSError("discarded")),
    )

    assert driver.main("a" * 40, tmp_path) == 1
    receipt = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

    assert receipt["status"] == "blocked"
    assert receipt["phase"] == "runtime_pin_block_failed"
    assert receipt["driver_phase"] == "runtime_pin_block_failed"
    assert "discarded" not in json.dumps(receipt, sort_keys=True)


def test_root_launcher_checks_registry_before_creating_an_output_leaf() -> None:
    """terminal direct launch은 새 leaf·driver·ledger를 만들기 전에 끝난다."""

    launcher = (Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once").read_text(
        encoding="utf-8"
    )

    assert launcher.index('"$ktdctl" pin verify --json >/dev/null 2>&1') < launcher.index(
        'install -d -o root -g root -m 0700 "$output_dir"'
    )


def test_root_launcher_blocks_and_writes_a_fixed_envelope_when_driver_result_is_unavailable() -> None:
    """driver raw output 부재도 terminal evidence 없이 재시도할 수 없게 고정한다."""

    launcher = (Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once").read_text(
        encoding="utf-8"
    )

    assert "launcher_safe_result_unavailable" in launcher
    assert '"$ktdctl" pin block "$initial_pinset"' in launcher
    assert "--map-revision \"$initial_map_revision\"" in launcher
    assert "--pinvi-revision \"$initial_pinvi_revision\"" in launcher
    assert "launcher-result.json" in launcher
    assert ">/dev/null 2>&1" in launcher[launcher.index("m05_isolated_e2e.py") :]
    assert "stderr.log" not in launcher[launcher.index("driver_status=") :]
    block_check = launcher[launcher.rindex('"$ktdctl" pin show --json') :]
    assert "/usr/bin/python3 -I -S -c" in block_check
    assert "<<'PY'" not in block_check[: block_check.index("fallback_path=")]


def test_root_launcher_accepts_only_the_launch_snapshot_and_fixed_schema() -> None:
    """rotation race와 임의 driver envelope은 fresh candidate 성공 근거가 될 수 없다."""

    launcher = (Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once").read_text(
        encoding="utf-8"
    )

    assert "initial_snapshot" in launcher
    assert "stable_snapshot" in launcher
    assert "post_snapshot" in launcher
    assert '"$post_snapshot" == "$initial_snapshot"' in launcher
    assert 'value.get("pinset_sha256") != expected_pinset' in launcher
    assert 'value.get("status") != "passed"' in launcher
    assert "if set(value) != expected_keys:" in launcher
    assert "if [[ ! -e \"$launcher_result_path\"" in launcher


def test_root_launcher_accepts_every_runtime_setup_subphase() -> None:
    """driver가 쓴 안전 phase를 launcher가 fallback으로 다시 뭉개면 안 된다."""

    launcher = (Path(__file__).resolve().parents[2] / "scripts/run-m05-isolated-e2e-once").read_text(
        encoding="utf-8"
    )
    phases = (
        "runtime_setup_ports",
        "runtime_setup_workspace",
        "runtime_setup_admission",
        "runtime_setup_network",
        "runtime_setup_credentials",
        "runtime_setup_map_config",
        "runtime_setup_pinvi_config",
    )

    assert all(f'"{phase}"' in launcher for phase in phases)


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

    assert set(ports) == {
        "map_api",
        "map_dagster",
        "map_postgres",
        "map_rustfs",
        "pinvi_api",
        "pinvi_web",
        "pinvi_rustfs",
        "pinvi_dagster",
    }
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


def test_manager_writes_and_passes_the_private_pinvi_admission_not_an_environment_marker() -> None:
    driver = _driver()
    admission = Path("/private/runtime/pinvi-isolated-manager-admission.json")

    environment = driver._pinvi_manager_admission_environment(
        env_file=Path("/private/runtime/pinvi.env"),
        project="m05i-pinvi-" + "e" * 32,
        pinvi_source_revision="d" * 40,
        admission_path=admission,
    )

    assert environment == {
        "PINVI_ENV_FILE": "/private/runtime/pinvi.env",
        "PINVI_DOCKER_PROJECT": "m05i-pinvi-" + "e" * 32,
        "PINVI_SOURCE_REVISION": "d" * 40,
        "PINVI_M05_ISOLATED_MANAGER_ADMISSION_PATH": str(admission),
        "PINVI_M05_PINSET_SHA256": PINNED_RUNTIME_RELEASE.pinset_sha256,
    }
    assert "PINVI_M05_ISOLATED_MANAGER_HARNESS" not in environment

    source = (Path(__file__).resolve().parents[2] / "scripts/m05_isolated_e2e.py").read_text(
        encoding="utf-8"
    )
    admission_write = source.index("build_m05_isolated_manager_admission(plan=plan, pair=pair)")
    pinvi_up = source.index('str(pinvi_root / "scripts/docker-app.sh"),')

    assert admission_write < pinvi_up
    assert "_pinvi_manager_admission_environment(" in source
    assert "PINVI_M05_ISOLATED_MANAGER_HARNESS" not in source
