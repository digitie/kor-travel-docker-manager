"""`ktdctl dagster-plane converge` — 공용 Dagster plane만 설치된 release의 render로 맞춘다.

재구축 안의 plane 수렴 한 조각을 같은 lock·환경 계약으로 따로 돈다. 아래는 그 경계를 빨갛게 만들 수 있는 것만 본다:
plane 밖 서비스를 건드리는가, lock 밖에서 바꾸는가, 재구축이 허락하지 않는 환경에서 도는가, 무연산·dry-run이
정말 아무것도 바꾸지 않는가, 기다림이 상한 안에서 서비스별 상태를 돌려주는가.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from kor_travel_docker_manager.cli import main
from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services import runtime_pair_rotation
from kor_travel_docker_manager.services import runtime_topology as runtime_topology_module
from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
    ManagerMutationActiveError,
)
from kor_travel_docker_manager.services.compose_service import ComposeService
from kor_travel_docker_manager.services.runtime_pair_rotation import RuntimePairRotationError

_REBUILD_ENVIRONMENT = {
    "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
    "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
    "PINVI_ENVIRONMENT": "production",
    "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
}
_IMAGE_ID = "sha256:" + "e" * 64


def _installed_render() -> dict[str, Any]:
    """체크아웃 compose의 서비스 전부를 기본값으로 보간한 render(`compose config`처럼) — plane 밖 서비스도 있다."""

    from test_dagster_shared_workspace_is_derived import _documents

    compose, _targets = _documents()
    return yaml.safe_load(
        re.sub(r"\$\{[A-Z0-9_]+:-([^${}]*)\}", r"\1", yaml.safe_dump(compose))
    )


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    resolved = _installed_render()
    plane = runtime_topology_module.derive_shared_dagster_plane(resolved)
    state = SimpleNamespace(
        environment=dict(_REBUILD_ENVIRONMENT),
        lock_held=False,
        lock_error=None,
        # 서비스별 떠 있는 컨테이너: render와 같으면 current. `digest`를 바꾸면 workspace digest만 어긋난다.
        stale={name: True for name in plane.services},
        health={name: "healthy" for name in plane.services},
        compose_calls=[],
        inspected=[],
        rotation_pending=False,
    )

    @contextmanager
    def lock() -> Iterator[None]:
        if state.lock_error is not None:
            raise state.lock_error
        state.lock_held = True
        try:
            yield
        finally:
            state.lock_held = False

    def require_no_rotation() -> None:
        if state.rotation_pending:
            raise RuntimePairRotationError("runtime pair rotation is incomplete")

    by_container = {
        runtime_topology_module.installed_container_name(name): name for name in plane.services
    }

    def plane_container(container_name: str, *, label: str) -> dict[str, Any]:
        del label
        name = by_container[container_name]
        state.inspected.append((name, state.lock_held))
        render = resolved["services"][name]
        env = {
            str(key): compose_service_module._unescape_compose(str(value))
            for key, value in (render.get("environment") or {}).items()
            if value is not None
        }
        if state.stale[name]:
            env["KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST"] = "0123456789abcdef"
        return {
            "image_id": _IMAGE_ID,
            "env": env,
            "cmd": [compose_service_module._unescape_compose(str(w)) for w in render["command"]],
            "entrypoint": None,
            "running": True,
            "restarting": False,
            "health": state.health[name] if not state.stale[name] else "unhealthy",
        }

    def frozen_recovery(args: Any, *, transaction: Any, **kwargs: Any) -> dict[str, Any]:
        del transaction, kwargs
        state.compose_calls.append((tuple(args), state.lock_held))
        if tuple(args[:1]) == ("up",):
            for name in args:
                if name in state.stale:
                    state.stale[name] = False
        return {"success": True, "returncode": 0, "stdout": "", "stderr": ""}

    for name, replacement in {
        "_require_pinned_runtime_rebuild_root": lambda: None,
        "manager_mutation_lock": lock,
        "_capture_pinned_runtime_rebuild_environment_snapshot": (
            lambda: SimpleNamespace(effective=dict(state.environment))
        ),
        "validate_c6c_operation_tokens": lambda *_args, **_kwargs: None,
        "installed_shared_dagster_plane": lambda: plane,
        "installed_location_owners": lambda: {},
    }.items():
        monkeypatch.setattr(compose_service_module, name, replacement)
    monkeypatch.setattr(
        runtime_pair_rotation, "require_no_pending_runtime_pair_rotation", require_no_rotation
    )
    monkeypatch.setattr(compose_service_module, "_SHARED_PLANE_PROBE_INTERVAL_SECONDS", 0.0)
    service = ComposeService()
    transaction = SimpleNamespace(resolved=resolved)
    for name, replacement in {
        "capture_transaction_unlocked": lambda **_kwargs: (transaction, None),
        "_read_shared_workspace_locations": lambda _source: (),
        "_inspect_plane_container": plane_container,
        "_inspect_image_reference_id": lambda _reference, *, label: _IMAGE_ID,
        "_run_frozen_recovery": frozen_recovery,
    }.items():
        monkeypatch.setattr(service, name, replacement)
    yield SimpleNamespace(service=service, plane=plane, state=state, resolved=resolved)


def test_a_stale_plane_recreates_only_the_plane_services_under_the_lock(
    harness: SimpleNamespace,
) -> None:
    result = harness.service.converge_shared_dagster_plane(dry_run=False)

    assert result["outcome"] == "recreated" and result["success"] is True
    assert result["returncode"] == 0
    assert result["health"] == {name: "healthy" for name in harness.plane.services}
    calls = harness.state.compose_calls
    # 바꾸는 compose 호출은 하나, 재구축과 같은 모양(`up -d --no-deps <plane>`)이고 G 안이다.
    assert calls == [(("up", "-d", "--no-deps", *harness.plane.services), True)]
    # plane 밖 서비스(Map·PinVi slot, gateway, code-server)는 render에 있어도 호출에 들지 않는다.
    others = set(harness.resolved["services"]) - set(harness.plane.services)
    assert others and not others & set(calls[0][0])
    assert all(held for _name, held in harness.state.inspected)


def test_the_stale_reason_names_the_digest_without_leaking_secret_env(
    harness: SimpleNamespace,
) -> None:
    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    for name in harness.plane.services:
        reasons = result["drift"][name]
        assert any(
            reason.startswith("env KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST: running 0123456789abcdef")
            for reason in reasons
        ), reasons
    # 공용 metadata URL(비밀)의 값은 어디에도 실리지 않는다.
    assert "postgresql" not in repr(result)


def test_dry_run_reports_and_mutates_nothing(harness: SimpleNamespace) -> None:
    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    assert result["outcome"] == "would_recreate" and result["dry_run"] is True
    assert harness.state.compose_calls == []


def test_a_current_plane_is_a_no_op(harness: SimpleNamespace) -> None:
    harness.state.stale = {name: False for name in harness.plane.services}

    result = harness.service.converge_shared_dagster_plane(dry_run=False)

    assert result["outcome"] == "current"
    assert result["drift"] == {name: [] for name in harness.plane.services}
    assert harness.state.compose_calls == []


@pytest.mark.parametrize(
    "override",
    [
        {"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
        {"KTDM_DEPLOYMENT_ENVIRONMENT": "local"},
        {"KTDM_DEPLOYMENT_LIFECYCLE": "operational"},
        {"PINVI_ENVIRONMENT": "development"},
    ],
)
def test_an_environment_the_rebuild_refuses_is_refused_before_any_docker_call(
    harness: SimpleNamespace, override: dict[str, str]
) -> None:
    harness.state.environment.update(override)

    with pytest.raises(DeploymentContractError, match="rehearsal/rebuildable"):
        harness.service.converge_shared_dagster_plane(dry_run=False)

    assert harness.state.compose_calls == [] and harness.state.inspected == []


def test_a_held_lock_refuses_without_looking(harness: SimpleNamespace) -> None:
    harness.state.lock_error = ManagerMutationActiveError("another Manager mutation is active")

    with pytest.raises(ManagerMutationActiveError):
        harness.service.converge_shared_dagster_plane(dry_run=False)

    assert harness.state.compose_calls == [] and harness.state.inspected == []


def test_a_pending_pair_rotation_refuses(harness: SimpleNamespace) -> None:
    harness.state.rotation_pending = True

    with pytest.raises(RuntimePairRotationError):
        harness.service.converge_shared_dagster_plane(dry_run=False)

    assert harness.state.compose_calls == []


def test_a_plane_that_does_not_become_healthy_reports_each_service(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = harness.plane.daemon
    harness.state.health[daemon] = "unhealthy"
    monkeypatch.setattr(compose_service_module, "_SHARED_PLANE_PROBE_TIMEOUT_SECONDS", 0.0)

    result = harness.service.converge_shared_dagster_plane(dry_run=False)

    assert result["success"] is False and result["returncode"] == 2
    assert result["outcome"] == "recreated_not_healthy"
    assert result["health"] == {harness.plane.webserver: "healthy", daemon: "unhealthy"}


def test_a_running_legacy_daemon_for_a_loaded_location_refuses(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """plane이 실을 location의 옛 Dagster 서비스가 돌면 재구축과 같이 거부한다(이중 발화)."""

    family = SimpleNamespace(legacy=("old-daemon",), container_name=lambda service: service)
    monkeypatch.setattr(
        compose_service_module, "installed_location_owners", lambda: {"loc": family}
    )
    monkeypatch.setattr(harness.service, "_read_shared_workspace_locations", lambda _s: ("loc",))
    monkeypatch.setattr(
        harness.service, "_inspect_container_running", lambda _container, *, label: True
    )

    with pytest.raises(DeploymentContractError, match="double fire"):
        harness.service.converge_shared_dagster_plane(dry_run=False)

    assert harness.state.compose_calls == []


# --- CLI ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("flags", [[], ["--dry-run", "--confirm"]])
@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_requires_exactly_one_of_dry_run_or_confirm(
    mock_compose_service: Any, flags: list[str]
) -> None:
    assert main(["dagster-plane", "converge", *flags]) == 2
    mock_compose_service.converge_shared_dagster_plane.assert_not_called()


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_refuses_an_inherited_lock(
    mock_compose_service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kor_travel_docker_manager import cli

    monkeypatch.setenv(cli._INHERITED_GLOBAL_MUTATION_LOCK_FD_ENV, "9")

    assert main(["dagster-plane", "converge", "--confirm"]) == 2
    mock_compose_service.converge_shared_dagster_plane.assert_not_called()


@pytest.mark.parametrize(("flag", "dry_run"), [("--dry-run", True), ("--confirm", False)])
@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_passes_the_mode_and_returns_the_verdict(
    mock_compose_service: Any,
    flag: str,
    dry_run: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_compose_service.converge_shared_dagster_plane.return_value = {
        "success": False,
        "returncode": 2,
        "outcome": "recreated_not_healthy",
        "services": ["web", "daemon"],
        "drift": {"web": ["image differs"], "daemon": []},
        "health": {"web": "healthy", "daemon": "unhealthy"},
    }

    assert main(["dagster-plane", "converge", flag]) == 2
    mock_compose_service.converge_shared_dagster_plane.assert_called_once_with(dry_run=dry_run)
    out = capsys.readouterr().out
    assert "recreated_not_healthy" in out and "daemon: current [health: unhealthy]" in out


@patch("kor_travel_docker_manager.cli.compose_service")
def test_cli_failure_is_a_json_verdict(
    mock_compose_service: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_compose_service.converge_shared_dagster_plane.side_effect = DeploymentContractError(
        "pinned runtime rebuild requires rehearsal/rebuildable environment"
    )

    assert main(["dagster-plane", "converge", "--confirm", "--json"]) == 2
    assert '"status": "failed"' in capsys.readouterr().out
