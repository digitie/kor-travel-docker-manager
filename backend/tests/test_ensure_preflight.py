"""`ensure` 프리플라이트 효과 검사(ADR-58 결정 9·R5, T-MGR-05).

거부는 효과로 본다 — 예외와 함께 `up`에 이르지 않았음을 단언한다. n150 무변경: 장치 env 미설정이면 `findmnt`를 부르지
않고, 같은 arch면 통과한다.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from typing import Any

import pytest

from kor_travel_docker_manager.services.ensure_preflight import (
    DATA_SOURCE_DEVICE_ENV,
    ensure_preflight,
)
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
    docker = _Docker(host=host, images={"img:1": image})
    ensure_preflight(_resolved(), ["api"], {}, run=docker)


def test_ensure_build_skips_the_arch_check_for_services_it_rebuilds():
    """리뷰 5번: `ensure --build`는 `build:` 서비스의 이미지를 이 호스트에서 다시 만든다 — 어긋난 arch를 고치는 그 명령을
    막지 않는다. `build:`가 없는 서비스는 `--build`여도 그대로 본다."""
    docker = _Docker(host="aarch64", images={"img:1": "amd64", "pulled:1": "amd64"})
    resolved = {"services": {
        "api": {"image": "img:1", "build": {"context": "."}},
        "pg": {"image": "pulled:1"},
    }}
    ensure_preflight(resolved, ["api"], {}, build=True, run=docker)
    with pytest.raises(DeploymentContractError, match="api"):
        ensure_preflight(resolved, ["api"], {}, build=False, run=docker)
    with pytest.raises(DeploymentContractError, match="pg"):
        ensure_preflight(resolved, ["api", "pg"], {}, build=True, run=docker)


def test_an_image_not_present_locally_is_not_checked():
    # `up --build`가 이 호스트에서 빌드하거나 pull이 호스트 플랫폼을 고른다.
    ensure_preflight(_resolved(), ["api"], {}, run=_Docker(host="aarch64", images={}))


def test_services_not_being_started_are_not_checked():
    docker = _Docker(host="aarch64", images={"img:1": "amd64"})
    ensure_preflight(_resolved(), ["other"], {}, run=docker)
    assert docker.calls == []


@pytest.fixture
def binds(tmp_path):
    """실재하는 디렉터리 아래의 쓰기 bind(leaf는 아직 없다 — 가장 가까운 조상으로 본다)와 읽기 전용 bind."""
    (tmp_path / "data").mkdir()
    (tmp_path / "cfg").mkdir()
    data = {"type": "bind", "source": str(tmp_path / "data" / "pgdata"), "target": "/var/lib/postgresql/data"}
    config = {"type": "bind", "source": str(tmp_path / "cfg" / "a.yml"), "target": "/a.yml", "read_only": True}
    return tmp_path, data, config


def test_device_env_unset_skips_the_mount_check_entirely(binds):
    # n150 무변경: KTDM_DATA_SOURCE_DEVICE가 없으면 findmnt를 부르지 않는다.
    _root, data, config = binds
    docker = _Docker(images={"img:1": "amd64"})
    ensure_preflight(_resolved(volumes=[data, config]), ["api"], {}, run=docker)
    assert [c for c in docker.calls if c[0] == "findmnt"] == []


def test_a_writable_bind_on_another_device_is_refused(binds):
    root, data, config = binds
    docker = _Docker(images={"img:1": "amd64"}, mounts={str(root): "/dev/mmcblk0p2[/home]"})
    with pytest.raises(DeploymentContractError, match=r"KTDM_DATA_SOURCE_DEVICE=/dev/nvme0n1p1.*pgdata is on /dev/mmcblk0p2"):
        ensure_preflight(_resolved(volumes=[data, config]), ["api"], {DATA_SOURCE_DEVICE_ENV: "/dev/nvme0n1p1"}, run=docker)


def test_a_writable_bind_on_the_device_passes_and_read_only_binds_are_ignored(binds):
    root, data, config = binds
    docker = _Docker(images={"img:1": "amd64"},
                     mounts={str(root / "data"): "/dev/nvme0n1p1[/kor-travel/shared-data]", str(root / "cfg"): "/dev/mmcblk0p2"})
    ensure_preflight(_resolved(volumes=[data, config]), ["api"], {DATA_SOURCE_DEVICE_ENV: "/dev/nvme0n1p1"}, run=docker)
    probed = [c[3] for c in docker.calls if c[0] == "findmnt"]
    assert probed == [str(root / "data")]  # 없는 leaf는 가장 가까운 조상, 읽기 전용 bind는 보지 않는다


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
    # argv 파서는 설치된 모델을 읽을 수 있다 — 여기서는 순서만 보므로 마지막 서비스 이름만 돌려주는 대역(파서·closure 자체는
    # `test_ensure_checks_the_rendered_depends_on_closure`가 본다).
    monkeypatch.setattr(module.ComposeService, "_compose_touched_services",
                        staticmethod(lambda argv, resolved: {argv[-1]} if argv[0] in {"up", "run"} else set()))
    seen = []

    def refuse(resolved, services, environment, *, build):
        seen.append(list(services))
        assert build is False
        raise DeploymentContractError("ensure preflight: refused")

    monkeypatch.setattr(module, "ensure_preflight", refuse)
    monkeypatch.setattr(module.ComposeService, "_ensure_target_unlocked",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("up reached")))
    with pytest.raises(DeploymentContractError, match="ensure preflight: refused"):
        module.ComposeService().ensure_target("anything")
    # 대상은 기존 compose argv 파서로 읽은 `up`·init step 서비스(+렌더된 depends_on closure)다.
    assert seen == [["one-shot", "svc"]]


def test_a_failing_compose_config_render_stops_ensure_before_the_preflight(monkeypatch):
    """compose 플러그인 부재·렌더 실패 → `ensure`가 멈춘다. 플러그인 부재 자체(`docker compose`가 없는 호스트)는 기존
    시험이 담당한다 — 해석 명령이 실패하면 `compose candidate resolution failed`다. 그 렌더(`docker compose config --format json`)는 이미 ensure의
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


def test_ensure_checks_the_rendered_depends_on_closure(monkeypatch):
    """리뷰 6번: `up`은 이름 붙은 서비스의 `depends_on`도 만든다 — 그 서비스들도 검사 대상이다."""
    from kor_travel_docker_manager.services import compose_service as module

    resolved = {"services": {"api": {"depends_on": {"db": {}}}, "db": {}, "other": {}}}
    touched = module.ComposeService._compose_touched_services(["up", "-d", "api"], resolved)
    assert touched == {"api", "db"}
    assert module.ComposeService._compose_touched_services(["run", "--rm", "--no-deps", "api"], resolved) == {"api"}
    assert module.ComposeService._compose_touched_services(["kill", "--signal=SIGHUP", "api"], resolved) == set()


@pytest.mark.skipif(shutil.which("findmnt") is None, reason="findmnt 없음(Linux 전용)")
def test_the_real_findmnt_names_the_device_of_a_tmp_path(tmp_path):
    """대역 없이 실제 `findmnt`로 — 이 경로의 장치를 기대값으로 두면 통과, 다른 값이면 거부."""
    import subprocess as sp

    device = sp.run(["findmnt", "-n", "-T", str(tmp_path), "-o", "SOURCE"], capture_output=True, text=True,
                    check=True).stdout.strip().split("[", 1)[0]
    volume = {"type": "bind", "source": str(tmp_path / "not-yet" / "data"), "target": "/data"}
    resolved = {"services": {"api": {"volumes": [volume]}}}
    ensure_preflight(resolved, ["api"], {DATA_SOURCE_DEVICE_ENV: device})
    with pytest.raises(DeploymentContractError, match="writable binds must be on"):
        ensure_preflight(resolved, ["api"], {DATA_SOURCE_DEVICE_ENV: "/dev/not-this-one"})
