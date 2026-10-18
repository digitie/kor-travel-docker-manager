"""운영 모듈 초기화 없이 실제 임시 파일·builder 함수를 격리 실행한다."""

from __future__ import annotations

import ast
import errno
import os
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

_SERVICES = Path(__file__).resolve().parents[1] / "src/kor_travel_docker_manager/services"


@pytest.fixture
def functions(monkeypatch):
    monkeypatch.setattr(os, "MFD_CLOEXEC", 1, raising=False)
    # DB/SDK/Compose 초기화를 피하되 아래 함수 본문은 제품 AST 그대로 실행한다.
    namespace = {
        "contextmanager": contextmanager,
        "errno": errno,
        "os": os,
        "stat": stat,
        "tempfile": tempfile,
        "subprocess": subprocess,
        "DeploymentContractError": RuntimeError,
        "command_output_tail": lambda _label, value: str(value),
    }
    for filename, names in (
        ("pinned_runtime_generation.py", {
            "_validate_state_root", "ensure_pinned_runtime_state_directory",
        }),
        ("compose_service.py", {
            "_pinned_runtime_temporary_directory",
            "_create_frozen_compose_descriptor",
            "_build_map_application_300_images",
        }),
    ):
        source = ast.parse((_SERVICES / filename).read_text())
        module = ast.Module(body=[
            node for node in source.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ], type_ignores=[])
        future = ast.ImportFrom(module="__future__", names=[
            ast.alias(name="annotations"),
        ], level=0)
        module.body.insert(0, future)
        exec(compile(ast.fix_missing_locations(module), filename, "exec"), namespace)
    return SimpleNamespace(**namespace)


def no_memfd(*_args, **_kwargs):
    raise AttributeError("fixture: memfd unavailable")


def test_fallback_is_private_unlinked_and_survives_directory_cleanup(
    functions, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os, "memfd_create", no_memfd, raising=False)
    state = tmp_path / "state"
    original_mkstemp = tempfile.mkstemp
    observed = []

    def create(**kwargs):
        directory = Path(kwargs["dir"])
        assert directory.parent == state
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        descriptor, path = original_mkstemp(**kwargs)
        assert stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o600
        observed.append(Path(path))
        return descriptor, path

    monkeypatch.setattr(tempfile, "mkstemp", create)
    descriptor = functions._create_frozen_compose_descriptor("compose", state_root=state)
    try:
        os.write(descriptor, b"local-fixture")
        os.lseek(descriptor, 0, os.SEEK_SET)
        assert os.read(descriptor, 64) == b"local-fixture"
        assert stat.S_IMODE(state.stat().st_mode) == 0o700
        assert list(state.iterdir()) == []
        assert not observed[0].exists()
    finally:
        os.close(descriptor)


def test_memfd_does_not_require_disk_state(functions, tmp_path, monkeypatch):
    monkeypatch.setattr(os, "memfd_create", lambda *_a, **_k: 123, raising=False)
    assert functions._create_frozen_compose_descriptor(
        "compose", state_root=tmp_path / "not-created",
    ) == 123
    assert list(tmp_path.iterdir()) == []


def test_fallback_unlink_failure_closes_descriptor(functions, tmp_path, monkeypatch):
    monkeypatch.setattr(os, "memfd_create", no_memfd, raising=False)
    original = os.unlink
    original_mkstemp = tempfile.mkstemp
    opened = []

    def create(**kwargs):
        result = original_mkstemp(**kwargs)
        opened.append(result[0])
        return result

    def unlink(path, **kwargs):
        if str(path).split("/")[-1].startswith("compose-"):
            # 실패를 한 번만 발생시켜 TemporaryDirectory cleanup은 실제로 수행한다.
            monkeypatch.setattr(os, "unlink", original)
            raise OSError(errno.EACCES, "fixture")
        return original(path, **kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", create)
    monkeypatch.setattr(os, "unlink", unlink)
    with pytest.raises(OSError):
        functions._create_frozen_compose_descriptor("compose", state_root=tmp_path / "state")
    with pytest.raises(OSError):
        os.fstat(opened[0])
    assert list((tmp_path / "state").iterdir()) == []


@pytest.mark.parametrize("unsafe", ["file", "symlink", "permissions"])
def test_invalid_state_fails_before_build(functions, tmp_path, monkeypatch, unsafe):
    state = tmp_path / "state"
    if unsafe == "file":
        state.write_text("fixture")
    elif unsafe == "symlink":
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        state.symlink_to(target)
    else:
        state.mkdir(mode=0o755)
        state.chmod(0o755)  # 실행자 umask와 무관하게 잘못된 권한을 실제로 만든다.
    runner = Mock()
    monkeypatch.setattr(subprocess, "run", runner)
    with pytest.raises(RuntimeError):
        functions._build_map_application_300_images(
            sources=SimpleNamespace(source_for=lambda _: SimpleNamespace(
                root=tmp_path, revision="a" * 40,
            )),
            api_image="fixture-api", dagster_image="fixture-cs", state_root=state,
        )
    runner.assert_not_called()


@pytest.mark.parametrize("failure", [None, "exit", "timeout"])
def test_build_tmpdir_env_and_cleanup(functions, tmp_path, monkeypatch, failure):
    state = tmp_path / "state"
    for key in ("DOCKER_CONFIG", "DOCKER_HOST", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(key, "fixture-" + key)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "must-not-use"))
    observed = []

    def run(args, **kwargs):
        environment = kwargs["env"]
        directory = Path(environment["TMPDIR"])
        assert directory.parent == state
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert set(environment) == {
            "DOCKER_CONFIG", "DOCKER_HOST", "XDG_RUNTIME_DIR", "PATH", "TMPDIR",
        }
        for key in ("DOCKER_CONFIG", "DOCKER_HOST", "XDG_RUNTIME_DIR"):
            assert environment[key] == "fixture-" + key
        assert args[:4] == ["docker", "buildx", "build", "--load"]
        assert kwargs["cwd"] == "/"
        assert kwargs["timeout"] == 3600
        (directory / "builder-file").write_text("fixture")
        observed.append(directory)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 3600)
        return subprocess.CompletedProcess(args, 1 if failure == "exit" else 0, b"", b"fixture")

    monkeypatch.setattr(subprocess, "run", run)
    def call():
        functions._build_map_application_300_images(
            sources=SimpleNamespace(source_for=lambda _: SimpleNamespace(
                root=tmp_path, revision="a" * 40,
            )),
            api_image="fixture-api", dagster_image="fixture-cs", state_root=state,
        )
    if failure:
        with pytest.raises(RuntimeError):
            call()
        assert len(observed) == 1
    else:
        call()
        assert len(observed) == 2
        assert observed[0] == observed[1]
    assert all(not directory.exists() for directory in observed)
    assert list(state.iterdir()) == []


def test_rebuild_passes_frozen_state_root():
    source = ast.parse((_SERVICES / "compose_service.py").read_text())
    calls = [
        node for node in ast.walk(source)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_build_map_application_300_images"
    ]
    assert len(calls) == 1
    value = next(item.value for item in calls[0].keywords if item.arg == "state_root")
    assert ast.unparse(value) == "state_paths.state_root"


@pytest.mark.parametrize("error_number", [errno.ENOSYS, errno.ENOMEM, errno.EPERM])
def test_memfd_os_failure_only_unsupported_has_disk_fallback(
    functions, tmp_path, monkeypatch, error_number,
):
    def unavailable(*_args, **_kwargs):
        raise OSError(error_number, "fixture")
    monkeypatch.setattr(os, "memfd_create", unavailable, raising=False)
    state = tmp_path / "state"
    if error_number == errno.ENOSYS:
        descriptor = functions._create_frozen_compose_descriptor("compose", state_root=state)
        os.close(descriptor)
        assert list(state.iterdir()) == []
    else:
        with pytest.raises(OSError):
            functions._create_frozen_compose_descriptor("compose", state_root=state)
        assert not state.exists()


def test_existing_builder_failure_test_body_without_global_integration_fixtures(
    functions, tmp_path, monkeypatch,
):
    # 기존 파일의 autouse integration fixture 없이 동일한 test 본문을 실행한다.
    from typing import Any, cast

    path = Path(__file__).with_name("test_pinned_runtime_rebuild.py")
    source = ast.parse(path.read_text())
    name = "test_application_image_build_failure_carries_the_buildx_tail"
    function = next(node for node in source.body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        function,
    ], type_ignores=[])
    namespace = {
        "Mock": Mock, "subprocess": subprocess, "cast": cast, "Any": Any,
        "SimpleNamespace": SimpleNamespace, "pytest": pytest,
        "compose_service_module": functions, "DeploymentContractError": RuntimeError,
    }
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    namespace[name](tmp_path, monkeypatch)


@pytest.mark.parametrize("label_kind", ["absolute", "relative"])
def test_fallback_label_cannot_escape_state_directory(
    functions, tmp_path, monkeypatch, label_kind,
):
    monkeypatch.setattr(os, "memfd_create", no_memfd, raising=False)
    state = tmp_path / "state"
    label = str(tmp_path / "outside-state") if label_kind == "absolute" else "../../outside-state"
    original_mkstemp = tempfile.mkstemp
    created = []

    def create(**kwargs):
        descriptor, path = original_mkstemp(**kwargs)
        created.append(Path(path).resolve())
        return descriptor, path

    monkeypatch.setattr(tempfile, "mkstemp", create)
    descriptor = functions._create_frozen_compose_descriptor(label, state_root=state)
    os.close(descriptor)
    assert len(created) == 1
    assert created[0].is_relative_to(state)
    assert created[0].parent.parent == state
    assert not created[0].exists()
    assert list(state.iterdir()) == []
