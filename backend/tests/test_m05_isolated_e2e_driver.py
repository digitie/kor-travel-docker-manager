from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from urllib.request import Request

import pytest

from kor_travel_docker_manager.services.pinned_runtime_release import (
    PINNED_RUNTIME_RELEASE,
)


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
    assert "raise SystemExit" in runner
    assert "base64.b64decode" in entrypoint


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
