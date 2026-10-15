"""ADR-58 R4(명령 host 규칙)·R7(교차 호스트 `depends_on` 간선 ∅) — T-MGR-01b.

모든 거부는 **효과**로 본다: 예외 타입·문구와 함께, 거부 뒤에 와야 할 변경 단계(compose 환경 스냅숏·lock·Docker
SDK·pg_dump)가 **불리지 않았음**을 단언한다. 호스트는 비운영 모드의 `KTDM_HOST`로 고른다(주소 검사 없음).
"""

from __future__ import annotations

import contextlib
import copy
import http.server
import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from kor_travel_docker_manager.services import registry
from kor_travel_docker_manager.services.errors import HostPlacementError
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

ROOT = Path(__file__).resolve().parents[2]


def _registry() -> dict[str, Any]:
    return load_yaml_rejecting_duplicate_keys((ROOT / "config/docker-targets.yml").read_text(encoding="utf-8"))


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    value = _registry()
    monkeypatch.setenv(registry.PROJECT_ROOT_ENV, str(tmp_path))
    monkeypatch.delenv(registry.HOST_ENV, raising=False)
    monkeypatch.setattr(registry, "load_targets_config", lambda: value)
    return value


def _default(config: dict[str, Any]) -> str:
    return registry.default_host(config)


def _other(config: dict[str, Any]) -> str:
    return next(name for name in config["hosts"] if name != _default(config))


def _remote_target(config: dict[str, Any]) -> str:
    """default 호스트가 아닌 곳에 있는 Manager 자신의 target 하나(registry에서 고른다 — 이름 리터럴 없음)."""
    return next(
        name
        for name, spec in config["targets"].items()
        if spec.get("host") not in (None, "all") and not spec.get("external_project") and not spec.get("include")
    )


# --- R7 -------------------------------------------------------------------------------------------


def test_registry_target_edges_stay_on_host(config):
    registry._validate_targets_config(config, label="test")


def test_a_cross_host_target_edge_is_rejected_at_load(config):
    remote = _remote_target(config)
    local = next(
        name
        for name, spec in config["targets"].items()
        if "host" not in spec and not spec.get("include") and not spec.get("external_project")
    )
    config["targets"][local]["depends_on"] = [*config["targets"][local].get("depends_on", []), remote]
    with pytest.raises(registry.TargetsConfigError, match=r"R7"):
        registry._validate_targets_config(config, label="test")


def test_an_edge_into_an_everywhere_target_is_allowed(config):
    # hosts(a) ⊆ hosts(b): `all` 호스트의 infra로 가는 간선은 어디서든 로컬이다.
    everywhere = next(name for name, spec in config["targets"].items() if spec.get("host") == "all" and not spec.get("include"))
    remote = _remote_target(config)
    config["targets"][remote]["depends_on"] = [everywhere]
    registry._validate_targets_config(config, label="test")


def _compose() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _cross_host_compose_edges(compose: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """registry로 계산한 compose `depends_on` 간선 중 호스트를 넘는 것(B-9).

    출발 service를 어느 target도 실행하지 않으면(옛 profile·수동 bootstrap one-shot) 이 검사 밖이다 — Manager의
    `ensure`가 그 service를 올리지 않는다. 출발이 실행 service인데 도착이 무소유면 위반이다(어디서 뜰지 모른다).
    """
    violations = []
    for name, service in compose["services"].items():
        source = registry.service_hosts(name, config)
        if not source:
            continue
        for dependency in service.get("depends_on") or []:
            reached = registry.service_hosts(dependency, config)
            if not reached or not source <= reached:
                violations.append(f"{name} {sorted(source)} -> {dependency} {sorted(reached)}")
    return violations


def test_compose_has_no_cross_host_depends_on_edge(config):
    compose = _compose()
    checked = sum(
        1 for name, service in compose["services"].items()
        if registry.service_hosts(name, config) and service.get("depends_on")
    )
    assert checked >= 10  # 하한은 본 것에 건다 — 간선이 있는 실행 service를 실제로 봤다
    assert _cross_host_compose_edges(compose, config) == []


def test_a_left_over_cross_host_compose_edge_is_red(config):
    compose = _compose()
    remote = _remote_target(config)
    local = next(
        name for name, spec in config["targets"].items()
        if "host" not in spec and not spec.get("include") and not spec.get("external_project")
    )
    remote_service = next(s for s in config["targets"][remote]["services"] if registry.service_hosts(s, config) == {config["targets"][remote]["host"]})
    local_service = next(s for s in config["targets"][local]["services"] if registry.service_hosts(s, config) == {_default(config)})
    compose["services"][local_service].setdefault("depends_on", {})[remote_service] = {"condition": "service_healthy"}
    assert _cross_host_compose_edges(compose, config) == [
        f"{local_service} {[_default(config)]} -> {remote_service} {[config['targets'][remote]['host']]}"
    ]


def test_ensure_closure_of_default_host_targets_stays_on_the_default_host(config, monkeypatch):
    # 2026-10-15 위험의 효과: default 호스트 target의 ensure 폐포에 다른 호스트 target이 없다(`ensure map`이 geo를 안 올림).
    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    for name, spec in config["targets"].items():
        if spec.get("include") or "host" in spec:
            continue
        for member in registry.target_sequence_for_target(name):
            assert _default(config) in registry.target_hosts(member, config), (name, member)


# --- R4: ensure ------------------------------------------------------------------------------------


def _forbid(*_: Any, **__: Any) -> Any:
    raise AssertionError("거부 뒤의 변경 단계가 불렸다")


class _Reached(Exception):
    pass


def _reached(*_: Any, **__: Any) -> Any:
    raise _Reached


def test_ensure_refuses_a_target_on_another_host_before_any_compose_step(config, monkeypatch):
    from kor_travel_docker_manager.services import compose_service as module

    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    monkeypatch.setattr(module, "_capture_compose_environment_snapshot", _forbid)
    remote = _remote_target(config)
    with pytest.raises(HostPlacementError, match=rf"target {remote} is on host {config['targets'][remote]['host']}"):
        module.compose_service.ensure_target(remote)


def test_ensure_passes_the_host_rule_on_the_owning_host(config, monkeypatch):
    from kor_travel_docker_manager.services import compose_service as module

    remote = _remote_target(config)
    monkeypatch.setenv(registry.HOST_ENV, config["targets"][remote]["host"])
    monkeypatch.setattr(module, "_capture_compose_environment_snapshot", _reached)
    with pytest.raises(_Reached):
        module.compose_service.ensure_target(remote)


def test_ensure_refuses_when_the_closure_reaches_another_host(config, monkeypatch):
    # 로더(R7)를 우회한 폐포도 ensure가 다시 본다 — `up`이 다른 호스트 target을 이 호스트에 되살리지 않는다.
    from kor_travel_docker_manager.services import compose_service as module

    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    monkeypatch.setattr(module, "_capture_compose_environment_snapshot", _forbid)
    remote = _remote_target(config)
    local = next(
        name for name, spec in config["targets"].items()
        if "host" not in spec and not spec.get("include") and not spec.get("external_project")
    )
    config["targets"][local]["depends_on"] = [remote]
    with pytest.raises(HostPlacementError, match=rf"target {remote} is on host"):
        module.compose_service.ensure_target(local)


# --- R4: start/restart vs stop ----------------------------------------------------------------------


@pytest.fixture
def remote_container(config) -> str:
    remote = _remote_target(config)
    return next(
        name for name in config["targets"][remote]["containers"]
        if registry.container_hosts(name, config) == {config["targets"][remote]["host"]}
    )


@pytest.mark.parametrize("action", ["start", "restart"])
def test_start_and_restart_refuse_a_container_of_another_host(config, monkeypatch, remote_container, action):
    from kor_travel_docker_manager.services import docker_service as module

    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    monkeypatch.setattr(module, "c6c_deployment_lock_from_environment", _forbid)
    monkeypatch.setattr(module.DockerService, "_control_container_unlocked", _forbid)
    with pytest.raises(HostPlacementError, match=rf"container {remote_container} is on host"):
        module.DockerService().control_container(remote_container, action)


def test_stop_does_not_ask_the_host_rule(config, monkeypatch, remote_container):
    # 옛 스택 해체: 이 소켓에 그 이름이 있으면 멈춘다. 판정은 SDK 조회(아래 unlocked 층)다.
    from kor_travel_docker_manager.services import docker_service as module

    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    calls = []
    monkeypatch.setattr(module, "c6c_deployment_lock_from_environment", lambda: contextlib.nullcontext(object()))
    from types import SimpleNamespace

    monkeypatch.setattr(module, "_capture_compose_environment_snapshot", lambda **_: SimpleNamespace(effective={}))
    monkeypatch.setattr(module, "assert_environment_snapshot_matches_c6c_lock", lambda *_: None)
    monkeypatch.setattr(module, "assert_manager_mutation_allowed", lambda **_: "rehearsal")
    monkeypatch.setattr(
        module.DockerService, "_control_container_unlocked",
        lambda self, container_id, action, **_: calls.append((container_id, action)) or {"success": True},
    )
    assert module.DockerService().control_container(remote_container, "stop") == {"success": True}
    assert calls == [(remote_container, "stop")]


# --- R4: pinned rebuild · pin · converge · db-backup ----------------------------------------------


def _move_runtime_pair_away(config) -> None:
    from kor_travel_docker_manager.services.pinned_runtime_release import RUNTIME_SOURCE_ROLES

    for role in RUNTIME_SOURCE_ROLES:
        config["targets"][role]["host"] = _other(config)


def test_pinned_rebuild_refuses_before_its_root_check(config, monkeypatch):
    from kor_travel_docker_manager.services import compose_service as module

    _move_runtime_pair_away(config)
    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    monkeypatch.setattr(module, "_require_pinned_runtime_rebuild_root", _forbid)
    with pytest.raises(HostPlacementError, match="pinned runtime rebuild"):
        module.compose_service.rebuild_pinned_runtime()


def test_runtime_pin_mutation_refuses_before_the_lock(config, monkeypatch):
    from kor_travel_docker_manager import cli

    _move_runtime_pair_away(config)
    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    monkeypatch.setattr(cli, "manager_mutation_lock", _forbid)
    with pytest.raises(HostPlacementError, match="runtime pin mutation"), cli._runtime_pin_mutation_lock():
        pass


def test_pin_cli_reports_the_refusal_as_a_failed_json_verdict(config, monkeypatch, capsys):
    import json

    from kor_travel_docker_manager import cli

    _move_runtime_pair_away(config)
    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    monkeypatch.setattr(cli, "manager_mutation_lock", _forbid)
    code = cli.main(["pin", "rotate", "--role", "map", "--revision", "a" * 40, "--reason", "r", "--confirm", "--json"])
    assert code == 2
    out = capsys.readouterr().out
    assert json.loads(out)["status"] == "failed"


def test_shared_plane_services_are_on_the_default_host_only(config, monkeypatch):
    services = config["targets"]["dagster"]["services"]
    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    registry.require_services_on_this_host(services, action="dagster-plane converge")
    monkeypatch.setenv(registry.HOST_ENV, _other(config))
    with pytest.raises(HostPlacementError, match="dagster-plane converge"):
        registry.require_services_on_this_host(services, action="dagster-plane converge")


def test_backup_roles_name_registry_targets(config):
    from kor_travel_docker_manager.services.standalone_backup import (
        BACKUP_ROLE_TARGETS,
        BACKUP_ROLES,
    )

    assert set(BACKUP_ROLE_TARGETS) == set(BACKUP_ROLES)
    assert set(BACKUP_ROLE_TARGETS.values()) <= set(config["targets"])


def test_db_backup_create_refuses_a_role_of_another_host_before_touching_postgres(config, monkeypatch):
    from kor_travel_docker_manager.services import standalone_backup as module

    role = next(r for r, t in module.BACKUP_ROLE_TARGETS.items() if config["targets"][t].get("host") not in (None, "all"))
    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    monkeypatch.setattr(module, "_discover_port", _forbid)
    with pytest.raises(module.StandaloneBackupError, match=rf"db-backup create {role}"):
        module.create_standalone_backup(role)
    monkeypatch.setenv(registry.HOST_ENV, config["targets"][module.BACKUP_ROLE_TARGETS[role]]["host"])
    monkeypatch.setattr(module, "_discover_port", _reached)
    with pytest.raises(_Reached):
        module.create_standalone_backup(role)


# --- R4: 원격 status (HTTP probe) ------------------------------------------------------------------


@contextlib.contextmanager
def _http_server(status: int) -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(status)
            if 300 <= status < 400:
                self.send_header("Location", "http://127.0.0.1:9/elsewhere")
            self.end_headers()

        def log_message(self, *_: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(("status", "state"), [(200, "up"), (302, "up"), (404, "down"), (503, "down")])
def test_probe_classifies_http_responses(status, state):
    from kor_travel_docker_manager.services.remote_probe import probe_url

    with _http_server(status) as url:
        assert probe_url(url, timeout=3) == state


def test_a_refused_connection_is_unreachable_not_down():
    # 거짓 빨강 입력: HAProxy 전용 포트처럼 이 호스트에서 닫힌 포트는 `down`이 아니다.
    from kor_travel_docker_manager.services.remote_probe import probe_url

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert probe_url(f"http://127.0.0.1:{port}/", timeout=3) == "unreachable"


def test_a_peer_that_accepts_and_hangs_up_is_down():
    from kor_travel_docker_manager.services.remote_probe import probe_url

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve() -> None:
        connection, _ = listener.accept()
        connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        assert probe_url(f"http://127.0.0.1:{listener.getsockname()[1]}/", timeout=3) == "down"
    finally:
        thread.join(timeout=3)
        listener.close()


def test_status_of_a_remote_target_probes_every_http_connection(config, monkeypatch):
    from kor_travel_docker_manager.services import compose_service as module
    from kor_travel_docker_manager.services import remote_probe

    remote = _remote_target(config)
    monkeypatch.setenv(registry.HOST_ENV, _default(config))
    monkeypatch.setattr(module.ComposeService, "_status_target_local", lambda self, target, **_: {"success": True, "returncode": 0, "stdout": ""})
    seen: list[str] = []
    monkeypatch.setattr(remote_probe, "probe_url", lambda url, **_: seen.append(url) or "unreachable")
    result = module.compose_service.status_target(remote)
    containers = result["remote"]["containers"]
    rendered = {name: str(registry.MANAGED_CONTAINERS[name]["connection"]) for name in containers}
    expected_urls = sorted(url for text in rendered.values() for url in remote_probe._URL.findall(text))
    assert expected_urls and sorted(seen) == expected_urls
    address = config["hosts"][config["targets"][remote]["host"]]["addr"]
    assert all(f"://{address}:" in url for url in seen)
    for name, text in rendered.items():
        assert containers[name]["state"] == ("unreachable" if "://" in text else remote_probe.NOT_PROBED)
    assert result["returncode"] == 0
    # 이 호스트의 target은 probe하지 않는다.
    local = next(n for n, s in config["targets"].items() if "host" not in s and not s.get("include"))
    assert "remote" not in module.compose_service.status_target(local)


def test_three_hosts_keep_the_rules_generic(config, monkeypatch):
    """N-host: 세 번째 호스트로 target을 옮기면 R4는 그 호스트에서만 통과하고 R7은 끊긴 간선을 요구한다."""
    config = copy.deepcopy(config)
    monkeypatch.setattr(registry, "load_targets_config", lambda: config)
    config["hosts"]["mars"] = {"addr": "192.0.2.7"}
    targets = config["targets"]
    # 다른 target(map)이 기대는 default 호스트 target. 공용 PG service도 실행하므로 mars에서도 PG가 로컬이다 — PG를
    # 실행하지 않는 target만 옮기면 mars에서 PG(n150·odroid 두 곳)의 R1 주소가 하나로 정해지지 않아 로더가 거부한다
    # (#483의 다중 소유 규칙; 열린 질문으로 보고).
    moved = "conc"
    assert any(moved in (other.get("depends_on") or []) for other in targets.values())
    targets[moved]["host"] = "mars"
    with pytest.raises(registry.TargetsConfigError, match="R7"):
        registry._validate_targets_config(config, label="test")
    # 플립 PR이 하는 일: 옮긴 target에 닿는 교차 호스트 간선을 끊는다(모든 호스트에 있는 infra로 가는 간선은 남는다).
    for name, spec in targets.items():
        spec["depends_on"] = [
            d for d in spec.get("depends_on") or []
            if registry.target_hosts(name, config) <= registry.target_hosts(d, config)
        ]
    registry._validate_targets_config(config, label="test")
    for host in config["hosts"]:
        monkeypatch.setenv(registry.HOST_ENV, host)
        if host == "mars":
            registry.require_targets_on_this_host([moved], action="ensure")
        else:
            with pytest.raises(HostPlacementError, match="on host mars"):
                registry.require_targets_on_this_host([moved], action="ensure")
