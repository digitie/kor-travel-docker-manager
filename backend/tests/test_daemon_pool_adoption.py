"""공식 Common wheel·원 runtime 잠금·daemon 한 곳의 소비 경계를 검증한다."""

from __future__ import annotations

import base64
import configparser
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_IMAGE = _ROOT / "docker/dagster-host"
_VENDOR = _IMAGE / "vendor"
_WHEEL = _VENDOR / "kor_travel_common-0.1.0.dev0-py3-none-any.whl"
# PR511 d021의71개 runtime 버전·전체 해시 집합. 주석/행 순서는 설치 의미가 아니다.
_PRIOR_RUNTIME_MAP_SHA = "1bce71dd6639cca0be56d6d6f22a0503d5c12e01201afd946bab10c26d855ce8"


def test_vendor_whole_record_source_and_official_metadata() -> None:
    proof = json.loads((_VENDOR / "common-source.json").read_text())
    assert hashlib.sha256(_WHEEL.read_bytes()).hexdigest() == proof["wheel_sha256"]
    assert _WHEEL.stat().st_size == proof["wheel_bytes"]
    assert proof["common"] == "b4f7f3dcb5f95c682b027e3886a603d5d02625d3"
    assert proof["product"] == proof["common"]
    with zipfile.ZipFile(_WHEEL) as wheel:
        names = wheel.namelist()
        assert len(names) == len(set(names))
        assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in names)
        package = {name for name in names if name.startswith("kortravelcommon/")}
        assert package == set(proof["package_files"])
        for name, digest in proof["package_files"].items():
            assert hashlib.sha256(wheel.read(name)).hexdigest() == digest
        info = "kor_travel_common-0.1.0.dev0.dist-info/"
        rows = list(csv.reader(io.StringIO(wheel.read(info + "RECORD").decode())))
        checked = 0
        for name, digest, size in rows:
            data = wheel.read(name)
            if digest:
                algorithm, value = digest.split("=", 1)
                assert algorithm == "sha256"
                actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
                assert actual.decode() == value and len(data) == int(size)
                checked += 1
            else:
                assert name == info + "RECORD" and size == ""
        assert checked == proof["record_rows_checked"] == 24
        parser = configparser.ConfigParser()
        parser.read_string(wheel.read(info + "entry_points.txt").decode())
        assert parser["console_scripts"]["kor-travel-dagster-daemon"] == (
            "kortravelcommon.daemon_pool:main"
        )
        assert parser["console_scripts"]["kor-travel-dagster-webserver"] == (
            "kortravelcommon.dagster_webserver:main"
        )
        assert parser["sqlalchemy.plugins"]["kortravel_daemon_pool"] == (
            "kortravelcommon._daemon_pool_plugin:DaemonPoolPlugin"
        )
        assert info + "licenses/LICENSE" in names and info + "licenses/NOTICE" in names


def test_wheel_core_and_cli_import_are_lazy_without_sdk_or_database() -> None:
    # zipimport로 실제 wheel 본문과 배포판 metadata를 읽는다. SDK entry point는 호출하지 않는다.
    script = """
import sys, json, importlib.metadata
sys.path.insert(0, sys.argv[1])
import kortravelcommon
import kortravelcommon.daemon_pool
import kortravelcommon.dagster_webserver
distribution = importlib.metadata.distribution("kor-travel-common")
assert distribution.version == "0.1.0.dev0"
assert len([e for e in distribution.entry_points if e.name == "kor-travel-dagster-daemon"]) == 1
assert len([e for e in distribution.entry_points if e.name == "kor-travel-dagster-webserver"]) == 1
assert not any(n == "dagster" or n.startswith("dagster.") for n in sys.modules)
assert "sqlalchemy" not in sys.modules and "psycopg2" not in sys.modules
assert ".whl/" in kortravelcommon.__file__
print(json.dumps({"lazy": True}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(_WHEEL)],
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        check=True, capture_output=True, text=True,
    )
    assert json.loads(result.stdout) == {"lazy": True}


def _requirements(text: str) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    current = ""
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==(\S+)", line)
        if match:
            current = match[1].lower().replace("_", "-")
            result[current] = {"version": match[2], "hashes": []}
        elif "--hash=sha256:" in line:
            hashes = result[current]["hashes"]
            assert isinstance(hashes, list)
            hashes.extend(re.findall(r"--hash=sha256:([0-9a-f]{64})", line))
    for row in result.values():
        hashes = row["hashes"]
        assert isinstance(hashes, list)
        row["hashes"] = sorted(hashes)
    return result


def test_only_common_is_added_to_the_prior_runtime_lock() -> None:
    requirements = _requirements((_IMAGE / "requirements.txt").read_text())
    common = requirements.pop("kor-travel-common")
    proof = json.loads((_VENDOR / "common-source.json").read_text())
    assert common == {"version": "0.1.0.dev0", "hashes": [proof["wheel_sha256"]]}
    assert len(requirements) == 71
    encoded = json.dumps(requirements, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == _PRIOR_RUNTIME_MAP_SHA
    dockerfile = (_IMAGE / "Dockerfile").read_text()
    assert "COPY vendor/" + _WHEEL.name + " /tmp/common-wheels/" + _WHEEL.name in dockerfile
    assert "--find-links /tmp/common-wheels" in dockerfile
    assert "--no-deps --require-hashes --only-binary=:all: --compile" in dockerfile


def test_only_daemon_uses_same_dsn_wrapper_after_original_guard() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    daemon = services["kor-travel-dagster-daemon"]
    environment = daemon["environment"]
    dedicated = "KOR_TRAVEL_DAGSTER_DAEMON_POSTGRES_URL"
    shared = "KOR_TRAVEL_DAGSTER_SHARED_PG_URL"
    assert environment[dedicated] == environment[shared]
    assert [name for name, service in services.items()
            if dedicated in (service.get("environment") or {})] == ["kor-travel-dagster-daemon"]
    command = daemon["command"]
    assert command[:2] == ["sh", "-ec"] and 'exec "$$@"' in command[2]
    assert "DagsterInstance" in command[2]
    assert command[3:] == [
        "sh", "kor-travel-dagster-daemon", "run", "-w",
        "/opt/dagster/dagster_home/workspace.yaml",
    ]
    probe = daemon["healthcheck"]
    assert (probe["interval"], probe["start_interval"], probe["timeout"],
            probe["retries"], probe["start_period"]) == ("360s", "45s", "60s", 2, "90s")
    assert "os.execvp('dagster-daemon', ['dagster-daemon', 'liveness-check'])" in (
        probe["test"][probe["test"].index("-c") + 1]
    )
    assert all("kor-travel-dagster-daemon" not in services[name]["command"]
               for name in ("kor-travel-dagster-webserver", "kor-travel-dagster-storage-migrate"))


def test_webserver_uses_official_alias_after_the_unchanged_storage_guard() -> None:
    services = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())["services"]
    web = services["kor-travel-dagster-webserver"]
    daemon = services["kor-travel-dagster-daemon"]
    assert web["command"][:4] == daemon["command"][:4]
    assert web["command"][3:] == [
        "sh", "kor-travel-dagster-webserver", "-h", "127.0.0.1",
        "-p", "$"+"{KOR_TRAVEL_DAGSTER_WEBSERVER_PORT:-11002}",
        "-w", "/opt/dagster/dagster_home/workspace.yaml",
    ]
    assert "KOR_TRAVEL_DAGSTER_DAEMON_POSTGRES_URL" not in web["environment"]
    assert services["kor-travel-dagster-storage-migrate"]["command"] == [
        "/usr/local/bin/kor-travel-dagster-storage-migrate"
    ]
    assert {services[name]["image"] for name in (
        "kor-travel-dagster-webserver", "kor-travel-dagster-daemon",
        "kor-travel-dagster-storage-migrate",
    )} == {web["image"]}


def test_workspace_refresh_option_is_daemon_only_and_default_is_unoptimized() -> None:
    services = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())["services"]
    key = "KOR_TRAVEL_DAGSTER_WORKSPACE_RELOAD_SECONDS"
    assert [name for name, service in services.items()
            if key in (service.get("environment") or {})] == ["kor-travel-dagster-daemon"]
    # `-`는 미설정만 기본60으로 처리한다. 명시 빈 문자열을 `:-`로 숨기지 않는다.
    assert services["kor-travel-dagster-daemon"]["environment"][key] == "${"+key+"-60}"


@pytest.mark.parametrize("raw,expected", [(None, [60, 300]), ("60", [60, 300]),
                                          ("180", [180, 420]), ("", None), ("240", None)])
@pytest.mark.parametrize("exit_during_profile", [False, True])
def test_vendored_workspace_profile_preserves_grace_and_restores_on_exit(
    raw, expected, exit_during_profile,
) -> None:
    # CI는 Dagster를 설치하지 않는다. SDK 두 상수만 대역으로 주고 실제 wheel 원문을 실행한다.
    # 실제 SDK/CLI/신호 통합은 Common44 검증과 소비자 격리 gate에서 별도로 확인한다.
    script = """
import json, os, sys, types
sys.path.insert(0, sys.argv[1])
from kortravelcommon.daemon_pool import _workspace_refresh_profile, DaemonPoolConfigurationError
raw = json.loads(sys.argv[2])
key = 'KOR_TRAVEL_DAGSTER_WORKSPACE_RELOAD_SECONDS'
if raw is None:
    os.environ.pop(key, None)
else:
    os.environ[key] = raw
controller = types.SimpleNamespace(RELOAD_WORKSPACE_INTERVAL=60,
                                  DEFAULT_WORKSPACE_FRESHNESS_TOLERANCE=300)
package = types.ModuleType('dagster._daemon')
package.controller = controller
sys.modules['dagster'] = types.ModuleType('dagster')
sys.modules['dagster._daemon'] = package
seen = None
try:
    with _workspace_refresh_profile():
        seen = [controller.RELOAD_WORKSPACE_INTERVAL,
                controller.DEFAULT_WORKSPACE_FRESHNESS_TOLERANCE]
        if sys.argv[3] == 'True':
            raise SystemExit(7)
except DaemonPoolConfigurationError:
    assert seen is None
except SystemExit as exc:
    assert exc.code == 7
assert controller.RELOAD_WORKSPACE_INTERVAL == 60
assert controller.DEFAULT_WORKSPACE_FRESHNESS_TOLERANCE == 300
print(json.dumps(seen))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(_WHEEL), json.dumps(raw),
         str(exit_during_profile)],
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        check=True, capture_output=True, text=True, timeout=15,
    )
    assert json.loads(result.stdout) == expected
    if expected:
        assert expected[1] - expected[0] == 240
