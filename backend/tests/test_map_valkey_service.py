"""Map Valkey(`kor-travel-map-valkey`, Map ADR-114 · ADR-59) — 렌더된 모델에서 파생해 대조한다.

이름을 외우지 않는다: 소비자는 `KOR_TRAVEL_MAP_VALKEY_URL`을 받는 활성 서비스로 찾고, 그 URL의 host·port·비밀번호를
Valkey 서비스의 `--bind`·`--port`·healthcheck·`VALKEYCLI_AUTH`와 대조한다. 비밀번호가 argv에 새지 않는다는 것과
기동 스크립트의 거부·stdin 설정은 **실제로 sh로 돌려서** 본다(가짜 `docker-entrypoint.sh`가 받은 argv와 stdin을 적는다).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from test_dagster_shared_workspace_is_derived import _resolve

from kor_travel_docker_manager.services.runtime_topology import (
    derive_pinned_infrastructure,
    runtime_topology,
)
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_ROOT = Path(__file__).resolve().parents[2]
_SERVICE = "kor-travel-map-valkey"
_URL_ENV = "KOR_TRAVEL_MAP_VALKEY_URL"
_PASSWORD_ENV = "KOR_TRAVEL_MAP_VALKEY_PASSWORD"
#: argv·로그에 새면 바로 보이도록 고른 값(URI unreserved 문자만).
_SENTINEL = "Sentinel-valkey.pw_9~zz"


def _render(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _resolve(value, env)
    if isinstance(value, list):
        return [_render(item, env) for item in value]
    if isinstance(value, dict):
        return {key: _render(item, env) for key, item in value.items()}
    return value


def _services(password: str = _SENTINEL) -> dict[str, dict[str, Any]]:
    document = load_yaml_rejecting_duplicate_keys((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return _render(document["services"], {_PASSWORD_ENV: password})


def _consumers(services: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        name: service
        for name, service in services.items()
        if _URL_ENV in (service.get("environment") or {}) and not service.get("profiles")
    }


def _flag(argv: list[str], name: str) -> str:
    index = argv.index(name)
    return argv[index + 1]


def _server_argv(service: dict[str, Any]) -> list[str]:
    """기동 스크립트의 `valkey-server` 줄(줄 이음 포함)을 argv로."""
    script = service["command"][-1]
    joined = script.replace("\\\n", " ")
    line = next(line for line in joined.splitlines() if "valkey-server" in line)
    words = line.split()
    words = words[words.index("valkey-server") :]
    return [word.strip('"') for word in words]


def test_the_derivation_finds_consumers() -> None:
    """항진명제 방지 — 소비자가 비면 아래 대조가 아무것도 재지 않는다."""
    assert _consumers(_services()), f"{_URL_ENV}를 받는 활성 서비스가 없다"


def test_every_consumer_url_points_at_the_valkey_listener_with_its_password() -> None:
    services = _services()
    valkey = services[_SERVICE]
    argv = _server_argv(valkey)
    probe = [str(word) for word in valkey["healthcheck"]["test"]]
    for name, service in _consumers(services).items():
        url = urlsplit(service["environment"][_URL_ENV])
        assert url.scheme == "redis", name
        assert url.hostname == _flag(argv, "--bind") == _flag(probe, "-h") == "127.0.0.1", name
        assert url.port == int(_flag(argv, "--port")) == int(_flag(probe, "-p")), name
        assert url.password == valkey["environment"]["VALKEYCLI_AUTH"] == _SENTINEL, name


def test_every_consumer_waits_for_a_healthy_valkey() -> None:
    for name, service in _consumers(_services()).items():
        assert service["depends_on"][_SERVICE] == {"condition": "service_healthy"}, name


def test_the_password_never_reaches_an_argv() -> None:
    valkey = _services()[_SERVICE]
    for field in ("entrypoint", "command"):
        assert "Sentinel" not in str(valkey[field])
    assert "Sentinel" not in str(valkey["healthcheck"]), "healthcheck argv는 호스트 ps에 보인다"


def test_the_server_keeps_state_without_eviction() -> None:
    valkey = _services()[_SERVICE]
    argv = _server_argv(valkey)
    assert _flag(argv, "--maxmemory") == "1gb"
    assert _flag(argv, "--maxmemory-policy") == "noeviction"
    assert _flag(argv, "--appendonly") == "yes"
    assert _flag(argv, "--appendfsync") == "everysec"
    assert _flag(argv, "--save")
    assert any(volume.endswith(":" + _flag(argv, "--dir")) for volume in valkey["volumes"])
    # swap으로 새지 않는다(상한을 넘기면 swap이 아니라 OOM).
    assert valkey["memswap_limit"] == valkey["mem_limit"]


def test_the_probe_is_exec_form_that_fails_on_error_replies() -> None:
    """`valkey-cli`는 `-e` 없이 NOAUTH·LOADING 오류 응답에도 0으로 끝난다(2026-10-23 n150 실측)."""
    valkey = _services()[_SERVICE]
    probe = valkey["healthcheck"]["test"]
    assert probe[0] == "CMD" and "-e" in probe
    assert valkey["init"] is True
    assert "@sha256:" in valkey["image"]
    assert not valkey.get("ports"), "외부 포트를 열지 않는다"


def _run_startup(tmp_path: Path, password: str) -> subprocess.CompletedProcess[str]:
    """렌더된 기동 스크립트를 sh로 돌린다 — `docker-entrypoint.sh`는 argv와 stdin을 적는 가짜다."""
    valkey = _services(password)[_SERVICE]
    assert valkey["entrypoint"][0] == "/bin/sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker-entrypoint.sh"
    fake.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$OUT_DIR/argv"\ncat > "$OUT_DIR/stdin"\n', encoding="utf-8"
    )
    fake.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "OUT_DIR": str(tmp_path),
        **valkey["environment"],
    }
    return subprocess.run(
        [shutil.which("sh") or "/bin/sh", *valkey["entrypoint"][1:], *valkey["command"]],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh가 필요하다")
def test_the_startup_script_sends_the_password_on_stdin_only(tmp_path: Path) -> None:
    completed = _run_startup(tmp_path, _SENTINEL)
    assert completed.returncode == 0, completed.stderr
    argv = (tmp_path / "argv").read_text(encoding="utf-8").splitlines()
    assert argv[:2] == ["valkey-server", "-"], "`-`가 stdin 설정을 읽게 한다"
    assert _SENTINEL not in "\n".join(argv)
    assert (tmp_path / "stdin").read_text(encoding="utf-8").splitlines() == [f"requirepass {_SENTINEL}"]


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh가 필요하다")
@pytest.mark.parametrize("password", ["", "has space", "at@sign", "colon:x", "slash/x", "quote'x", "hash#x"])
def test_the_startup_script_refuses_passwords_that_break_the_url(tmp_path: Path, password: str) -> None:
    completed = _run_startup(tmp_path, password)
    assert completed.returncode == 64, completed.stderr
    assert not (tmp_path / "argv").exists(), "거부했으면 서버를 띄우지 않는다"
    assert password == "" or password not in completed.stderr, "거부 메시지에 값을 싣지 않는다"


def _targets() -> dict[str, Any]:
    document = load_yaml_rejecting_duplicate_keys(
        (_ROOT / "config" / "docker-targets.yml").read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return document


def _dagster_and_slot_services() -> tuple[str, ...]:
    topology = runtime_topology()
    return (
        *topology.runtime_services,
        *(name for family in topology.families.values() for name in family.names),
    )


def test_the_pinned_rebuild_brings_up_what_the_slots_wait_for() -> None:
    """재구축은 `up --no-deps`라 `depends_on`이 Valkey를 끌어오지 않는다 — 대신 target이 소유한 비-slot 상시
    서비스를 targets에서 파생해 먼저 올린다. 파생된 것은 모두 어떤 소비자가 기다리는 서비스여야 하고, Valkey가
    거기 있어야 한다."""
    derived = derive_pinned_infrastructure(_targets(), excluded=_dagster_and_slot_services())
    assert _SERVICE in derived
    waited_for = {name for service in _services().values() for name in (service.get("depends_on") or {})}
    assert set(derived) <= waited_for


def test_a_valkey_missing_from_runtime_services_is_not_brought_up() -> None:
    """빨갛게 만들어 본다 — `runtime_services`에서 빼면 재구축은 그것을 올리지 않는다(그 선언이 스위치다)."""
    targets = _targets()
    targets["targets"]["map"]["runtime_services"].remove(_SERVICE)
    assert _SERVICE not in derive_pinned_infrastructure(targets, excluded=_dagster_and_slot_services())
