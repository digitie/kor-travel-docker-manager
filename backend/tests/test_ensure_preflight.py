"""`ensure` 프리플라이트 효과 검사(ADR-58 결정 9·R5, T-MGR-05).

거부는 효과로 본다 — 예외와 함께 `up`에 이르지 않았음을 단언한다. n150 무변경: 장치 env 미설정이면 `findmnt`를 부르지
않고, 같은 arch면 통과한다.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from typing import Any

import pytest

from kor_travel_docker_manager.services.ensure_preflight import DATA_SOURCE_DEVICE_ENV, ensure_preflight
from kor_travel_docker_manager.services.errors import DeploymentContractError


class _Docker:
    """명령 → (returncode, stdout) 대역. 부른 명령을 기록한다."""

    def __init__(self, *, host: str = "x86_64", images: Mapping[str, str] | None = None,
                 mounts: Mapping[str, str] | None = None) -> None:
        self.host = host
        self.images = dict(images or {})
        self.mounts = dict(mounts or {})
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(command)
        if command[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(command, 0, stdout=self.host.encode())
        if command[:3] == ["docker", "image", "inspect"]:
            arch = self.images.get(command[-1])
            return subprocess.CompletedProcess(command, 0 if arch else 1, stdout=(arch or "").encode())
        if command[0] == "findmnt":
            path = command[3]
            source = next((value for prefix, value in self.mounts.items() if path.startswith(prefix)), "")
            return subprocess.CompletedProcess(command, 0 if source else 1, stdout=source.encode())
        raise AssertionError(command)


def _resolved(*, image: str = "img:1", volumes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"services": {"api": {"image": image, "volumes": volumes or []}}}


def test_an_image_of_another_architecture_is_refused():
    docker = _Docker(host="aarch64", images={"img:1": "amd64"})
    with pytest.raises(DeploymentContractError, match=r"architecture differs from this host \(arm64\).*api \(img:1: amd64\)"):
        ensure_preflight(_resolved(), ["api"], {}, run=docker)


@pytest.mark.parametrize(("host", "image"), [("x86_64", "amd64"), ("aarch64", "arm64"), ("amd64", "amd64")])
def test_the_same_architecture_passes(host, image):
    ensure_preflight(_resolved(), ["api"], {}, run=docker if (docker := _Docker(host=host, images={"img:1": image})) else None)


def test_an_image_not_present_locally_is_not_checked():
    # `up --build`가 이 호스트에서 빌드하거나 pull이 호스트 플랫폼을 고른다.
    ensure_preflight(_resolved(), ["api"], {}, run=_Docker(host="aarch64", images={}))


def test_services_not_being_started_are_not_checked():
    docker = _Docker(host="aarch64", images={"img:1": "amd64"})
    ensure_preflight(_resolved(), ["other"], {}, run=docker)
    assert docker.calls == []


_DATA = {"type": "bind", "source": "/home/digitie/kor-travel-shared-data/pgdata", "target": "/var/lib/postgresql/data"}
_CONFIG = {"type": "bind", "source": "/opt/x/config/a.yml", "target": "/a.yml", "read_only": True}


def test_device_env_unset_skips_the_mount_check_entirely():
    # n150 무변경: KTDM_DATA_SOURCE_DEVICE가 없으면 findmnt를 부르지 않는다.
    docker = _Docker(images={"img:1": "amd64"})
    ensure_preflight(_resolved(volumes=[_DATA, _CONFIG]), ["api"], {}, run=docker)
    assert [c for c in docker.calls if c[0] == "findmnt"] == []


def test_a_writable_bind_on_another_device_is_refused():
    docker = _Docker(images={"img:1": "amd64"}, mounts={"/home/digitie": "/dev/mmcblk0p2[/home]"})
    with pytest.raises(DeploymentContractError, match=r"KTDM_DATA_SOURCE_DEVICE=/dev/nvme0n1p1.*pgdata is on /dev/mmcblk0p2"):
        ensure_preflight(_resolved(volumes=[_DATA, _CONFIG]), ["api"], {DATA_SOURCE_DEVICE_ENV: "/dev/nvme0n1p1"}, run=docker)


def test_a_writable_bind_on_the_device_passes_and_read_only_binds_are_ignored():
    docker = _Docker(images={"img:1": "amd64"},
                     mounts={"/home/digitie": "/dev/nvme0n1p1[/kor-travel/shared-data]", "/opt": "/dev/mmcblk0p2"})
    ensure_preflight(_resolved(volumes=[_DATA, _CONFIG]), ["api"], {DATA_SOURCE_DEVICE_ENV: "/dev/nvme0n1p1"}, run=docker)
    assert [c[3] for c in docker.calls if c[0] == "findmnt"] != []
    assert not any(c[0] == "findmnt" and c[3].startswith("/opt") for c in docker.calls)


def test_an_unreadable_host_architecture_is_refused():
    def broken(command):
        return subprocess.CompletedProcess(command, 1, stdout=b"")

    with pytest.raises(DeploymentContractError, match="host architecture cannot be read"):
        ensure_preflight(_resolved(), ["api"], {}, run=broken)


# --- ensure 경로에 결박: 거부는 `up` 전, compose config 렌더 실패는 이미 ensure를 멈춘다 ----------------------


def test_ensure_runs_the_preflight_before_up(monkeypatch):
    """`ensure_target`이 프리플라이트를 `_ensure_target_unlocked`(= `up`) 전에 부른다 — 거부면 `up`에 이르지 않는다."""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from kor_travel_docker_manager.services import compose_service as module

    transaction = SimpleNamespace(
        resolved={"services": {}}, environment=SimpleNamespace(effective={}, compose_path="/nonexistent"),
        compose_source_bytes=b"", compose_source_mode=0,
    )
    monkeypatch.setattr(module, "local_mutation_target", lambda target: {
        "resolved_sequence": [target], "resolved_services": ["svc"], "resolved_init_steps": [
            {"name": "x", "command": ["run", "--rm", "one-shot"]}]})
    monkeypatch.setattr(module, "_capture_compose_environment_snapshot", lambda **_: SimpleNamespace(effective={}))
    monkeypatch.setattr(module, "assert_manager_mutation_allowed", lambda **_: "rehearsal")
    monkeypatch.setattr(module, "c6c_deployment_lock_from_environment", lambda: nullcontext(object()))
    monkeypatch.setattr(module.ComposeService, "capture_transaction_unlocked", lambda self: (transaction, SimpleNamespace()))
    monkeypatch.setattr(module, "_assert_transaction_matches_c6c_lock", lambda *_: None)
    monkeypatch.setattr(module.Path, "read_bytes", lambda self: b"")
    monkeypatch.setattr(module.Path, "stat", lambda self: SimpleNamespace(st_mode=0))
    seen = []

    def refuse(resolved, services, environment):
        seen.append(list(services))
        raise DeploymentContractError("ensure preflight: refused")

    monkeypatch.setattr(module, "ensure_preflight", refuse)
    monkeypatch.setattr(module.ComposeService, "_ensure_target_unlocked",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("up reached")))
    with pytest.raises(DeploymentContractError, match="ensure preflight: refused"):
        module.ComposeService().ensure_target("anything")
    assert seen == [["svc", "one-shot"]]


def test_a_failing_compose_config_render_stops_ensure_before_the_preflight(monkeypatch):
    """compose 플러그인 부재·렌더 실패 → `ensure`가 멈춘다. 그 렌더(`docker compose config --format json`)는 이미 ensure의
    transaction이 하는 것이라(실패하면 `compose candidate resolution failed` — `test_pinned_runtime_rebuild.py`·`test_api.py`가
    그 문구를 결박) T-MGR-05는 새로 만들지 않는다. 여기서는 그 실패가 프리플라이트·`up` **전에** ensure를 멈추는 순서만 고정한다."""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from kor_travel_docker_manager.services import compose_service as module
    from kor_travel_docker_manager.services.errors import ComposeCandidateContractError

    monkeypatch.setattr(module, "local_mutation_target", lambda target: {
        "resolved_sequence": [target], "resolved_services": ["svc"], "resolved_init_steps": []})
    monkeypatch.setattr(module, "_capture_compose_environment_snapshot", lambda **_: SimpleNamespace(effective={}))
    monkeypatch.setattr(module, "assert_manager_mutation_allowed", lambda **_: "rehearsal")
    monkeypatch.setattr(module, "c6c_deployment_lock_from_environment", lambda: nullcontext(object()))

    def no_render(self):
        raise ComposeCandidateContractError("compose candidate resolution failed")

    monkeypatch.setattr(module.ComposeService, "capture_transaction_unlocked", no_render)
    monkeypatch.setattr(module, "ensure_preflight", lambda *a: (_ for _ in ()).throw(AssertionError("preflight reached")))
    with pytest.raises(ComposeCandidateContractError, match="resolution failed"):
        module.ComposeService().ensure_target("anything")
