"""`ktdctl dagster-plane converge` — 공용 Dagster plane만 설치된 release의 render로 맞춘다.

재구축 안의 plane 수렴 한 조각을 같은 lock·환경 계약으로 따로 돈다. 아래는 그 경계를 빨갛게 만들 수 있는 것만 본다:
plane 밖 서비스를 건드리는가, lock 밖에서 바꾸는가, 재구축이 허락하지 않는 환경에서 도는가, 무연산·dry-run이
정말 아무것도 바꾸지 않는가, 기다림이 상한 안에서 서비스별 상태를 돌려주는가.
"""

from __future__ import annotations

import json
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
_ONESHOTS = ("kor-travel-dagster-storage-migrate",)


def _migrate_runs(resolved: dict[str, Any], names: tuple[str, ...]) -> list[tuple[str, ...]]:
    """`up` 앞에 도는 one-shot `run`들 — render의 `service_completed_successfully` 의존에서 파생(이름 목록 없음)."""

    oneshots = compose_service_module._completed_oneshot_dependencies(resolved["services"], names)
    assert oneshots, "the plane must wait for at least one one-shot (storage migrate)"
    return [("run", "--rm", "--no-deps", name) for name in oneshots]


def _installed_render() -> dict[str, Any]:
    """체크아웃 compose의 서비스 전부를 기본값으로 보간한 render(`compose config`처럼) — plane 밖 서비스도 있다."""

    from test_dagster_shared_workspace_is_derived import _documents

    compose, _targets = _documents()
    return yaml.safe_load(
        # `${X:-d}`와 `${X-d}` 둘 다(미설정이면 둘 다 d — `compose config`처럼).
        re.sub(r"\$\{[A-Z0-9_]+:?-([^${}]*)\}", r"\1", yaml.safe_dump(compose))
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
        # 서비스별 떠 있는 컨테이너의 로그 설정이 ADR-61 전(상한 없음)인가 — `up`이 지운다.
        log_stale={name: False for name in plane.services},
        health={name: "healthy" for name in plane.services},
        compose_calls=[],
        inspected=[],
        rotation_pending=False,
        env_overrides={},
        captures=[],
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
        env.update(state.env_overrides)
        # Docker가 render의 `logging`으로 만든 컨테이너의 `HostConfig.LogConfig` — 옛 컨테이너는 daemon 기본값(상한 없음).
        logging = render.get("logging") or {}
        log_config = (
            ("json-file", {})
            if state.log_stale[name]
            else (
                str(logging.get("driver") or "json-file"),
                {str(k): str(v) for k, v in (logging.get("options") or {}).items()},
            )
        )
        return {
            "image_id": _IMAGE_ID,
            "env": env,
            "cmd": [compose_service_module._unescape_compose(str(w)) for w in render["command"]],
            "entrypoint": None,
            "log_config": log_config,
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
                    state.log_stale[name] = False
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
        "capture_transaction_unlocked": lambda **kwargs: (state.captures.append(kwargs), (transaction, None))[1],
        "_read_shared_workspace_locations": lambda _source, **_kwargs: (),
        "_inspect_plane_container": plane_container,
        "_inspect_image_reference_id": lambda _reference, *, label: _IMAGE_ID,
        # 받아 둔 host 이미지(ADR-67): 기본은 내용 label·arch가 맞다. 시험이 바꿔 거부를 본다.
        "_prebuilt_host_image_id": lambda _image: _IMAGE_ID,
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
    # 바꾸는 compose 호출은 render가 plane 앞에 두는 one-shot `run`과 `up -d --no-deps <plane>` 하나다.
    migrate = _migrate_runs(harness.resolved, harness.plane.services)
    assert calls == [
        *((args, True) for args in migrate),
        (("up", "-d", "--no-deps", "--no-build", *harness.plane.services), True),
    ]
    # plane 밖 서비스(Map·PinVi slot, gateway, code-server)는 render에 있어도 `up`에 들지 않는다.
    others = set(harness.resolved["services"]) - set(harness.plane.services)
    assert others and not others & set(calls[-1][0])
    assert all(held for _name, held in harness.state.inspected)


@pytest.mark.parametrize("dry_run", [True, False])
def test_converge_captures_the_rebuilds_pinned_scope(harness: SimpleNamespace, dry_run: bool) -> None:
    """plane converge는 재구축과 같은 서비스 폐포로 render를 잡는다 — plane이 닿지 않는 서비스(Concierge UI의
    prebuilt 시작 명령 등)의 계약이 이 명령을 막지 않는다(2026-11-23 n150: 전체 문서 검사가 늘 거부했다)."""

    harness.service.converge_shared_dagster_plane(dry_run=dry_run)

    assert harness.state.captures and all(
        capture.get("pinned_runtime_scope") is True for capture in harness.state.captures
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_plane_image_that_was_not_loaded_is_refused_and_never_built(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    """운영 박스는 빌드하지 않는다(ADR-67) — 받은 이미지가 없거나 맞지 않으면 바꾸기 전에 거부한다.

    예전에는 없는 이미지에 `--build`를 붙여 그 자리(n150)에서 빌드했다. 이제 `run`·`up` 어느 것도 부르지 않고, dry-run은
    거부 사유를 `images`에 싣는다(무엇을 옮겨야 하는지 `--confirm` 전에 보인다).
    """

    checked: list[str] = []

    def not_loaded(image: str) -> str:
        checked.append(image)
        raise DeploymentContractError(f"{image} is not on this host; operating hosts do not build it")

    monkeypatch.setattr(harness.service, "_prebuilt_host_image_id", not_loaded)

    if dry_run:
        result = harness.service.converge_shared_dagster_plane(dry_run=True)
        image = harness.resolved["services"][harness.plane.daemon]["image"]
        names = (*_ONESHOTS, *harness.plane.services)
        assert result["outcome"] == "would_recreate"
        assert result["images"] == {
            name: f"{image} is not on this host; operating hosts do not build it" for name in names
        }
    else:
        with pytest.raises(DeploymentContractError, match="do not build"):
            harness.service.converge_shared_dagster_plane(dry_run=False)
    assert harness.state.compose_calls == []
    assert checked and len(set(checked)) == 1


def test_every_plane_image_is_checked_before_the_first_change(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """one-shot(storage migrate)과 plane이 다른 이미지를 쓰면 둘 다 본 뒤에야 `run`한다 — 반쯤 바꾸고 멈추지 않는다."""

    services = harness.resolved["services"]
    services["kor-travel-dagster-storage-migrate"]["image"] = "kor-travel-dagster-host:" + "0" * 16
    seen: list[str] = []

    def only_the_migrate_image_is_loaded(image: str) -> str:
        seen.append(image)
        if image.endswith("0" * 16):
            return _IMAGE_ID
        raise DeploymentContractError(f"{image} is not on this host")

    monkeypatch.setattr(harness.service, "_prebuilt_host_image_id", only_the_migrate_image_is_loaded)

    with pytest.raises(DeploymentContractError, match="not on this host"):
        harness.service.converge_shared_dagster_plane(dry_run=False)
    assert harness.state.compose_calls == []
    assert seen[0].endswith("0" * 16) and len(set(seen)) == 2


def test_a_ready_plane_image_is_reported_by_dry_run(harness: SimpleNamespace) -> None:
    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    assert result["images"] == {name: "ready" for name in (*_ONESHOTS, *harness.plane.services)}
    harness.state.stale = {name: False for name in harness.plane.services}
    assert harness.service.converge_shared_dagster_plane(dry_run=True)["images"] == {}


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_common_env_value_the_wrapper_rejects_is_refused_before_any_change(
    harness: SimpleNamespace, dry_run: bool
) -> None:
    """빈·범위 밖 pool env로 다시 만들면 daemon이 종료 코드 2로 재시작만 반복한다 — 다시 만들기 전에 거부한다(적대 리뷰 B-2)."""

    daemon = harness.plane.daemon
    harness.resolved["services"][daemon]["environment"]["KOR_TRAVEL_DAGSTER_DAEMON_POOL_SIZE"] = "11"

    if dry_run:
        images = harness.service.converge_shared_dagster_plane(dry_run=True)["images"]
        assert images[daemon].startswith(f"{daemon}: KOR_TRAVEL_DAGSTER_DAEMON_POOL_SIZE must be")
        assert images[harness.plane.webserver] == "ready"
    else:
        with pytest.raises(DeploymentContractError, match="POOL_SIZE must be an integer 1..10"):
            harness.service.converge_shared_dagster_plane(dry_run=False)
    assert harness.state.compose_calls == []


def test_the_prebuilt_hook_delegates_to_the_host_image_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """하네스는 `_prebuilt_host_image_id`를 바꿔 끼운다 — 그 hook이 실제 검사로 이어지는지는 따로 결박한다(적대 리뷰 A-6)."""

    seen: list[str] = []
    monkeypatch.setattr(
        compose_service_module, "require_prebuilt_host_image",
        lambda image: (seen.append(image), _IMAGE_ID)[1],
    )
    assert ComposeService._prebuilt_host_image_id("kor-travel-dagster-host:" + "a" * 16) == _IMAGE_ID
    assert seen == ["kor-travel-dagster-host:" + "a" * 16]


def test_a_failed_storage_one_shot_leaves_the_plane_untouched(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """one-shot이 실패하면 `up`하지 않는다 — compose가 의존으로 돌릴 때와 같은 판정(fail-closed)."""

    calls = harness.state.compose_calls

    def failing(args: Any, *, transaction: Any, **kwargs: Any) -> dict[str, Any]:
        del transaction, kwargs
        calls.append((tuple(args), harness.state.lock_held))
        failed = tuple(args[:1]) == ("run",)
        return {"success": not failed, "returncode": 2 if failed else 0, "stdout": "", "stderr": "x"}

    monkeypatch.setattr(harness.service, "_run_frozen_recovery", failing)

    with pytest.raises(compose_service_module.PinnedRuntimeComposeFailure):
        harness.service.converge_shared_dagster_plane(dry_run=False)

    assert [args[0] for args, _held in calls] == ["run"]


def test_the_real_plane_runs_exactly_the_storage_migrate_one_shot_first(harness: SimpleNamespace) -> None:
    """항진명제 방지 — 위 시험들의 기대값은 함수 자신으로 파생하므로, 실제 render에서 무엇이 나오는지 한 번 literal로 결박한다.

    공용 instance(PostgreSQL 서버)·db-init은 들지 않는다 — 그것들은 `ensure dagster`의 몫이다.
    """

    services = harness.resolved["services"]
    oneshots = compose_service_module._completed_oneshot_dependencies(services, harness.plane.services)
    assert oneshots == ("kor-travel-dagster-storage-migrate",)
    assert services[oneshots[0]]["command"] == ["/usr/local/bin/kor-travel-dagster-storage-migrate"]
    assert not set(oneshots) & compose_service_module.postgres_server_services(harness.resolved)


def test_only_completed_successfully_dependencies_are_run_first() -> None:
    services = {
        "web": {"depends_on": {
            "migrate": {"condition": "service_completed_successfully", "required": True},
            "db": {"condition": "service_healthy"},
            "init": {"condition": "service_completed_successfully"},
        }},
        "daemon": {"depends_on": {"migrate": {"condition": "service_completed_successfully"}}},
        "migrate": {}, "db": {}, "init": {},
    }
    assert compose_service_module._completed_oneshot_dependencies(
        services, ("web", "daemon")
    ) == ("migrate", "init")
    assert compose_service_module._completed_oneshot_dependencies(services, ("migrate",)) == ()


@pytest.mark.parametrize("depends_on", [["migrate"], {"migrate": "service_completed_successfully"},
                                        {"absent": {"condition": "service_completed_successfully"}}])
def test_an_unrendered_dependency_shape_is_refused(depends_on: Any) -> None:
    services = {"web": {"depends_on": depends_on}, "migrate": {}}
    with pytest.raises(DeploymentContractError):
        compose_service_module._completed_oneshot_dependencies(services, ("web",))


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
    assert result["returncode"] == 0 and result["success"] is True
    # 어느 결과든 health를 싣는다(떠 있는 옛 컨테이너는 unhealthy) — JSON 모양은 늘 같다.
    assert result["health"] == {name: "unhealthy" for name in harness.plane.services}
    assert result["remaining_drift"] == {}
    assert harness.state.compose_calls == []


def test_a_current_plane_is_a_no_op(harness: SimpleNamespace) -> None:
    harness.state.stale = {name: False for name in harness.plane.services}

    result = harness.service.converge_shared_dagster_plane(dry_run=False)

    assert result["outcome"] == "current" and result["returncode"] == 0
    assert result["drift"] == {name: [] for name in harness.plane.services}
    assert result["remaining_drift"] == {}
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
    monkeypatch.setattr(harness.service, "_shared_plane_health_wait_seconds", lambda _p, _s: 0.0)

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
    monkeypatch.setattr(harness.service, "_read_shared_workspace_locations", lambda _s, **_kwargs: ("loc",))
    monkeypatch.setattr(
        harness.service, "_inspect_container_running", lambda _container, *, label: True
    )

    with pytest.raises(DeploymentContractError, match="double fire"):
        harness.service.converge_shared_dagster_plane(dry_run=False)

    assert harness.state.compose_calls == []


def test_a_current_but_unhealthy_plane_is_not_ok(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """무연산도 health를 본다 — 거짓 실패 뒤 다시 돌린 명령이 unhealthy plane을 OK라고 하지 않는다."""

    harness.state.stale = {name: False for name in harness.plane.services}
    harness.state.health[harness.plane.daemon] = "unhealthy"
    monkeypatch.setattr(harness.service, "_shared_plane_health_wait_seconds", lambda _p, _s: 0.0)

    result = harness.service.converge_shared_dagster_plane(dry_run=False)

    assert result["outcome"] == "current_not_healthy"
    assert result["success"] is False and result["returncode"] == 2
    assert result["health"][harness.plane.daemon] == "unhealthy"
    assert harness.state.compose_calls == []


def test_a_current_plane_dry_run_reports_health_without_waiting(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.state.stale = {name: False for name in harness.plane.services}
    harness.state.health[harness.plane.daemon] = "starting"
    monkeypatch.setattr(
        compose_service_module.time, "sleep", lambda _s: pytest.fail("dry-run waited")
    )

    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    # 보기만 한다 — unhealthy여도 종료 코드는 0이고 판정은 운영자가 한다.
    assert result["outcome"] == "current_not_healthy" and result["returncode"] == 0
    assert result["health"][harness.plane.daemon] == "starting"
    assert harness.state.compose_calls == []


def test_the_wait_bound_comes_from_the_rendered_healthchecks(harness: SimpleNamespace) -> None:
    """고정 300초가 아니다 — daemon은 start_period 90초 뒤 앞 probe가 끝나고 360초를 쉬며, 디스크 대기 중엔 probe 하나가
    timeout(60초)까지 걸린다. 그래서 probe 한 주기는 interval + timeout이다."""

    services = harness.resolved["services"]
    expected = 0.0
    for name in harness.plane.services:
        check = services[name]["healthcheck"]
        expected = max(
            expected,
            compose_service_module._duration_seconds(check["start_period"], default=0.0)
            + (
                compose_service_module._duration_seconds(check["interval"], default=30.0)
                + compose_service_module._duration_seconds(check["timeout"], default=30.0)
            )
            * check["retries"]
            + compose_service_module._SHARED_PLANE_HEALTH_WAIT_SLACK_SECONDS,
        )

    bound = ComposeService._shared_plane_health_wait_seconds(harness.plane, services)

    assert bound == expected
    # 현재 daemon:90 + (360 + 60) × 2 + 15 = 945. 원 render 계산과 일치해야 한다.
    assert bound == 945.0


def test_the_wait_bound_reads_compose_config_durations_and_is_clamped(
    harness: SimpleNamespace,
) -> None:
    plane = harness.plane

    def services(**check: object) -> dict[str, Any]:
        return {name: {"healthcheck": dict(check)} for name in plane.services}

    # `docker compose config --format json`은 기간을 Go 문자열로 낸다.
    rendered = services(start_period="1m30s", interval="2m0s", timeout="1m0s", retries=2)
    assert ComposeService._shared_plane_health_wait_seconds(plane, rendered) == 465.0
    tiny = services(start_period="1s", interval="1s", timeout="1s", retries=1)
    assert (
        ComposeService._shared_plane_health_wait_seconds(plane, tiny)
        == compose_service_module._SHARED_PLANE_HEALTH_WAIT_FLOOR_SECONDS
    )
    huge = services(start_period="10h", interval="1h", timeout="1h", retries=5)
    assert ComposeService._shared_plane_health_wait_seconds(plane, huge) == float(
        compose_service_module._MAX_C6C_WAIT_TIMEOUT_SECONDS
    )


def test_a_container_without_a_healthcheck_fails_fast(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.state.health[harness.plane.daemon] = None
    monkeypatch.setattr(
        harness.service, "_shared_plane_health_wait_seconds", lambda _p, _s: 1e9
    )
    monkeypatch.setattr(
        compose_service_module.time, "sleep", lambda _s: pytest.fail("waited for a healthcheck")
    )

    result = harness.service.converge_shared_dagster_plane(dry_run=False)

    assert result["returncode"] == 2
    assert result["health"][harness.plane.daemon] == "no healthcheck"


def test_only_the_plane_file_digests_carry_values(harness: SimpleNamespace) -> None:
    """값을 싣는 env는 healthcheck가 이름으로 대조하는 파일 digest뿐이다 — `_DIGEST` 이름·16진 값만으로는 아니다."""

    for name in harness.plane.services:
        harness.resolved["services"][name]["environment"]["OTHER_DIGEST"] = "0123456789abcdef"
    harness.state.env_overrides = {
        "OTHER_DIGEST": "fedcba9876543210",
        "KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST": "not-a-digest-secret",
    }

    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    for name in harness.plane.services:
        reasons = result["drift"][name]
        assert "env OTHER_DIGEST differs" in reasons, reasons
        assert not any("OTHER_DIGEST:" in reason for reason in reasons)
        assert "not-a-digest-secret" not in repr(result)
        # 파일 digest라도 떠 있는 값이 digest 모양이 아니면 그 값은 싣지 않는다.
        assert "env KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST: running other != rendered" in " ".join(
            reasons
        )


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


# --- 2026-10-17: bind mount drift — 내용 digest가 같은 두 파일 사이의 bind 변경도 재생성 사유다 -------------------

_WORKSPACE_IN_CONTAINER = "/opt/dagster/dagster_home/workspace.yaml"


def _with_long_syntax_workspace_bind(harness: SimpleNamespace, rendered_source: str, mounted_source: str) -> None:
    """render를 `compose config --format json` 모양(long syntax bind)으로, 떠 있는 컨테이너는 `mounted_source`를 붙인 것으로."""
    for name in harness.plane.services:
        harness.resolved["services"][name]["volumes"] = [
            {"type": "bind", "source": rendered_source, "target": _WORKSPACE_IN_CONTAINER, "read_only": True}
        ]
    real = harness.service._inspect_plane_container

    def with_mounts(container_name: str, *, label: str) -> Any:
        observed = dict(real(container_name, label=label))
        observed["mounts"] = {_WORKSPACE_IN_CONTAINER: (mounted_source, True)}
        return observed

    harness.service._inspect_plane_container = with_mounts
    harness.state.stale = {name: False for name in harness.plane.services}  # env·digest는 같다(같은 내용의 두 파일)


def test_a_plane_mounting_another_file_with_the_same_content_is_stale(harness: SimpleNamespace) -> None:
    root = "/opt/kor-travel-docker-manager/config/dagster-shared"
    _with_long_syntax_workspace_bind(harness, f"{root}/workspace.n150.yaml", f"{root}/workspace.yaml")

    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    assert result["outcome"] == "would_recreate"
    for name in harness.plane.services:
        assert result["drift"][name] == [
            f"bind {_WORKSPACE_IN_CONTAINER}: running {root}/workspace.yaml != rendered {root}/workspace.n150.yaml"
        ]


def test_a_plane_mounting_the_rendered_file_is_current(harness: SimpleNamespace) -> None:
    root = "/opt/kor-travel-docker-manager/config/dagster-shared"
    _with_long_syntax_workspace_bind(harness, f"{root}/workspace.n150.yaml", f"{root}/workspace.n150.yaml")

    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    assert result["outcome"] == "current"
    assert result["drift"] == {name: [] for name in harness.plane.services}


def test_inspect_reads_bind_mounts_with_their_read_only_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    document = {
        "Image": "sha256:" + "a" * 64,
        "Config": {"Env": [], "Cmd": [], "Entrypoint": None},
        "State": {"Running": True, "Restarting": False},
        "Mounts": [
            {"Type": "bind", "Source": "/opt/x/workspace.yaml", "Destination": _WORKSPACE_IN_CONTAINER, "RW": False},
            {"Type": "bind", "Source": "/opt/x/dagster.yaml", "Destination": "/opt/dagster/dagster_home/dagster.yaml", "RW": True},
            {"Type": "volume", "Name": "v", "Source": "/var/lib/docker/volumes/v", "Destination": "/data", "RW": True},
        ],
    }
    monkeypatch.setattr(
        compose_service_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(document), stderr=""),
    )
    observed = ComposeService._inspect_plane_container("c", label="c")
    assert observed["mounts"] == {
        _WORKSPACE_IN_CONTAINER: ("/opt/x/workspace.yaml", True),
        "/opt/dagster/dagster_home/dagster.yaml": ("/opt/x/dagster.yaml", False),
    }


def test_a_bind_still_mounted_but_no_longer_rendered_is_stale(harness: SimpleNamespace) -> None:
    root = "/opt/kor-travel-docker-manager/config/dagster-shared"
    _with_long_syntax_workspace_bind(harness, f"{root}/workspace.n150.yaml", f"{root}/workspace.n150.yaml")
    real = harness.service._inspect_plane_container

    def with_extra(container_name: str, *, label: str) -> Any:
        observed = dict(real(container_name, label=label))
        observed["mounts"] = {**observed["mounts"], "/opt/dagster/old.yaml": (f"{root}/old.yaml", True)}
        return observed

    harness.service._inspect_plane_container = with_extra
    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    assert result["outcome"] == "would_recreate"
    for name in harness.plane.services:
        assert result["drift"][name] == ["bind /opt/dagster/old.yaml is mounted but not rendered"]

def test_shared_daemon_six_minute_interval_does_not_clip_health_start_window(
    harness: SimpleNamespace,
) -> None:
    services = {
        name: {"healthcheck": {
            "test": ["CMD", "unchanged"], "start_period": "90s",
            "interval": "360s", "timeout": "60s", "retries": 2,
        }}
        for name in harness.plane.services
    }
    bound = ComposeService._shared_plane_health_wait_seconds(harness.plane, services)
    assert bound == 945.0
    assert compose_service_module._compose_wait_timeout_seconds(services) >= bound


@pytest.mark.parametrize("daemon_state", ["stopped", "running", "missing", "restarting"])
@pytest.mark.parametrize("stale_webserver", [False, True, "logs"])
def test_pinned_rebuild_preserves_an_existing_stopped_daemon(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    daemon_state: str, stale_webserver: bool | str,
) -> None:
    service, plane, state = harness.service, harness.plane, harness.state
    state.stale = {name: False for name in plane.services}
    if stale_webserver == "logs":
        # ADR-61 뒤 첫 재구축: 둘 다 로그 설정만 옛것이다 — 멈춘 daemon은 그대로 두고 webserver만 다시 만든다.
        state.log_stale = {name: True for name in plane.services}
    else:
        state.stale[plane.webserver] = stale_webserver
    original_inspect = service._inspect_plane_container
    running = {"daemon": daemon_state == "running"}
    rebuilt = {"done": False}
    calls = []

    def inspect(container_name: str, *, label: str):
        observed = original_inspect(container_name, label=label)
        if label == plane.daemon:
            if daemon_state == "missing" and not rebuilt["done"]:
                return None
            observed["running"] = running["daemon"]
            observed["restarting"] = daemon_state == "restarting" and not rebuilt["done"]
        return observed

    def compose(args, **kwargs):
        del kwargs
        calls.append(tuple(args))
        for name in args:
            if name in state.stale:
                state.stale[name] = False
                state.log_stale[name] = False
            if name == plane.daemon:
                running["daemon"] = True
                rebuilt["done"] = True
        return {"success": True}

    monkeypatch.setattr(service, "_inspect_plane_container", inspect)
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", compose)
    monkeypatch.setattr(service, "_frozen_shared_plane", lambda _: (
        plane, harness.resolved["services"], ("fixture.map",)))
    monkeypatch.setattr(compose_service_module, "installed_location_owners", lambda: {
        "fixture.map": SimpleNamespace(target="map")})
    monkeypatch.setattr(service, "_require_plane_location_owners_fenced", lambda *_: None)
    monkeypatch.setattr(service, "_query_shared_plane_locations", lambda *_: {
        "fixture.map": "RepositoryLocation"})
    topology = SimpleNamespace(shared_dagster_slots=("map",), families={
        "map": SimpleNamespace(target="map", shared=True)})
    service._converge_shared_dagster_plane(
        runtime_transaction=SimpleNamespace(), topology=topology)
    assert running["daemon"] is (daemon_state != "stopped")
    if stale_webserver or daemon_state in {"missing", "restarting"}:
        expected = (plane.webserver,) if daemon_state == "stopped" else plane.services
        assert calls == [
            *_migrate_runs(harness.resolved, expected), ("up", "-d", "--no-deps", "--no-build", *expected)
        ]
    else:
        assert calls == []


@pytest.mark.parametrize("daemon_running,location_state", [
    (True, "RepositoryLocation"),
    (False, "PythonError"),
])
def test_paused_rebuild_still_rejects_a_started_daemon_or_unready_location(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    daemon_running: bool, location_state: str,
) -> None:
    service = harness.service
    original = service._inspect_plane_container

    def inspect(container_name: str, *, label: str):
        observed = original(container_name, label=label)
        observed["running"] = daemon_running
        return observed

    monkeypatch.setattr(service, "_inspect_plane_container", inspect)
    monkeypatch.setattr(service, "_query_shared_plane_locations", lambda *_: {
        "fixture.map": location_state})
    monkeypatch.setattr(service, "_shared_plane_health_wait_seconds", lambda *_: 0)
    with pytest.raises(DeploymentContractError, match="did not load"):
        service._wait_shared_plane_locations(
            harness.plane, harness.resolved["services"], ("fixture.map",),
            keep_daemon_stopped=True)
    assert harness.state.compose_calls == []


# --- 2026-11-03: 로그 설정 drift(ADR-61) — render의 `logging`이 떠 있는 컨테이너의 LogConfig와 다르면 재생성 사유다 ----------


def _only_logs_stale(harness: SimpleNamespace) -> None:
    """env·이미지·bind는 render 그대로이고 로그 설정만 ADR-61 전(daemon 기본값, 상한 없음)인 plane."""

    harness.state.stale = {name: False for name in harness.plane.services}
    harness.state.log_stale = {name: True for name in harness.plane.services}


def test_the_render_declares_log_limits_for_the_plane(harness: SimpleNamespace) -> None:
    """항진명제 방지 — 아래 대조는 render가 plane에 `logging`을 실을 때만 무엇을 잰다(이름이 아니라 render에서)."""

    for name in harness.plane.services:
        assert harness.resolved["services"][name].get("logging"), name


def test_a_plane_without_the_rendered_log_limits_is_stale(harness: SimpleNamespace) -> None:
    _only_logs_stale(harness)

    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    assert result["outcome"] == "would_recreate"
    for name in harness.plane.services:
        options = sorted(harness.resolved["services"][name]["logging"]["options"])
        assert result["drift"][name] == ["logging options differ: " + ", ".join(options)]
    assert harness.state.compose_calls == []


def test_converge_recreates_a_plane_without_log_limits_once(harness: SimpleNamespace) -> None:
    _only_logs_stale(harness)

    result = harness.service.converge_shared_dagster_plane(dry_run=False)

    assert result["outcome"] == "recreated" and result["success"] is True
    assert harness.state.compose_calls == [
        *((args, True) for args in _migrate_runs(harness.resolved, harness.plane.services)),
        (("up", "-d", "--no-deps", "--no-build", *harness.plane.services), True),
    ]
    # 재생성 뒤에는 남은 차이가 없다 — 다음 converge는 무연산이다.
    assert result["remaining_drift"] == {name: [] for name in harness.plane.services}
    again = harness.service.converge_shared_dagster_plane(dry_run=False)
    assert again["outcome"] == "current" and harness.state.compose_calls[-1][0][0] == "up"
    assert len(harness.state.compose_calls) == 1 + len(
        _migrate_runs(harness.resolved, harness.plane.services)
    )


def test_a_plane_with_the_rendered_log_limits_is_current(harness: SimpleNamespace) -> None:
    harness.state.stale = {name: False for name in harness.plane.services}

    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    assert result["outcome"] == "current"
    assert result["drift"] == {name: [] for name in harness.plane.services}


def test_a_different_logging_driver_is_stale(harness: SimpleNamespace) -> None:
    harness.state.stale = {name: False for name in harness.plane.services}
    real = harness.service._inspect_plane_container

    def local_driver(container_name: str, *, label: str) -> Any:
        observed = dict(real(container_name, label=label))
        observed["log_config"] = ("local", observed["log_config"][1])
        return observed

    harness.service._inspect_plane_container = local_driver
    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    for name in harness.plane.services:
        assert result["drift"][name] == ["logging driver: running local != rendered json-file"]


def _with_running_log_config(harness: SimpleNamespace, change: Any) -> None:
    real = harness.service._inspect_plane_container

    def changed(container_name: str, *, label: str) -> Any:
        observed = dict(real(container_name, label=label))
        observed["log_config"] = change(observed["log_config"])
        return observed

    harness.service._inspect_plane_container = changed


def test_options_docker_merged_from_daemon_defaults_are_not_drift(harness: SimpleNamespace) -> None:
    """Docker는 `daemon.json` `log-opts`·log cache 기본값을 컨테이너에 합쳐 싣는다 — 그것을 세면 재구축마다 plane을 다시
    만든다(적대 리뷰 MED). render가 말한 option만 본다."""

    harness.state.stale = {name: False for name in harness.plane.services}
    _with_running_log_config(
        harness, lambda log: (log[0], {**log[1], "compress": "true", "cache-disabled": "false"})
    )

    result = harness.service.converge_shared_dagster_plane(dry_run=False)

    assert result["outcome"] == "current" and harness.state.compose_calls == []


@pytest.mark.parametrize("unreadable", [None, ["json-file", {}], ("json-file", None)])
def test_an_unreadable_log_config_is_refused_not_recreated(
    harness: SimpleNamespace, unreadable: Any
) -> None:
    harness.state.stale = {name: False for name in harness.plane.services}
    _with_running_log_config(harness, lambda _log: unreadable)

    with pytest.raises(DeploymentContractError, match="log config"):
        harness.service.converge_shared_dagster_plane(dry_run=False)
    assert harness.state.compose_calls == []


def test_a_render_without_logging_does_not_compare_it(harness: SimpleNamespace) -> None:
    """render가 `logging`을 말하지 않으면 daemon 기본값이 정한다 — 그것은 drift가 아니다(일반 규칙, 이름 없음)."""

    _only_logs_stale(harness)
    for name in harness.plane.services:
        harness.resolved["services"][name].pop("logging")

    result = harness.service.converge_shared_dagster_plane(dry_run=True)

    assert result["outcome"] == "current"


def test_rebuild_plane_convergence_recreates_a_plane_without_log_limits(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """재구축 안의 plane 수렴도 같은 판정이다 — 로그 설정만 다른 plane을 한 번 다시 만든다."""

    service, plane = harness.service, harness.plane
    _only_logs_stale(harness)
    calls = []

    def compose(args, **kwargs):
        del kwargs
        calls.append(tuple(args))
        for name in args:
            if name in harness.state.log_stale:
                harness.state.log_stale[name] = False
        return {"success": True}

    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", compose)
    monkeypatch.setattr(service, "_frozen_shared_plane", lambda _: (
        plane, harness.resolved["services"], ("fixture.map",)))
    monkeypatch.setattr(compose_service_module, "installed_location_owners", lambda: {
        "fixture.map": SimpleNamespace(target="map")})
    monkeypatch.setattr(service, "_require_plane_location_owners_fenced", lambda *_: None)
    monkeypatch.setattr(service, "_query_shared_plane_locations", lambda *_: {
        "fixture.map": "RepositoryLocation"})
    topology = SimpleNamespace(shared_dagster_slots=("map",), families={
        "map": SimpleNamespace(target="map", shared=True)})

    service._converge_shared_dagster_plane(runtime_transaction=SimpleNamespace(), topology=topology)

    assert calls == [
        *_migrate_runs(harness.resolved, plane.services),
        ("up", "-d", "--no-deps", "--no-build", *plane.services),
    ]
    assert not any(service._shared_plane_drift(plane, harness.resolved["services"]).values())


def test_inspect_reads_the_log_config(monkeypatch: pytest.MonkeyPatch) -> None:
    document = {
        "Image": "sha256:" + "a" * 64,
        "Config": {"Env": [], "Cmd": [], "Entrypoint": None},
        "HostConfig": {"LogConfig": {"Type": "json-file", "Config": {"max-size": "50m", "max-file": "5"}}},
        "State": {"Running": True, "Restarting": False},
    }
    monkeypatch.setattr(
        compose_service_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(document), stderr=""),
    )
    observed = ComposeService._inspect_plane_container("c", label="c")
    assert observed["log_config"] == ("json-file", {"max-size": "50m", "max-file": "5"})
    del document["HostConfig"]
    assert ComposeService._inspect_plane_container("c", label="c")["log_config"] is None
