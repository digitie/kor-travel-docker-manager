"""실제 stdlib 대신 작은 격리 소스에서 빌드 helper의 산출물·실패 경계를 확인한다."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HELPER = _ROOT / "docker/dagster-host/build-bytecode.py"
_COMMON_SOURCE_SHA = "b07c7f94565147f619bffba7f1f9871106c0ee07d3683974393d596ffda5d266"


@pytest.fixture
def bytecode_helper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    spec = importlib.util.spec_from_file_location("manager_build_bytecode", _HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sysconfig, "get_path", lambda name: str(tmp_path / "stdlib"))
    monkeypatch.setattr(module.sys, "pycache_prefix", None)
    root = tmp_path / "stdlib"
    root.mkdir()
    return module, root


def test_common_helper_is_an_exact_spdx_preserving_snapshot() -> None:
    assert hashlib.sha256(_HELPER.read_bytes()).hexdigest() == _COMMON_SOURCE_SHA
    assert _HELPER.read_text().startswith("# SPDX-License-Identifier: GPL-3.0-or-later")


def test_build_creates_only_checked_hash_o0_cache_and_preserves_sources(bytecode_helper) -> None:
    module, root = bytecode_helper
    sources = {root / "small.py": b"ANSWER = 42\n", root / "pkg" / "other.py": b"VALUE = 1\n"}
    for path, data in sources.items():
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    native = root / "native.so"
    native.write_bytes(b"unchanged native fixture")
    for name in ("site-packages", "dist-packages", "test", "tests", "idle_test"):
        excluded = root / name
        excluded.mkdir()
        (excluded / "invalid.py").write_text("invalid python !")
    assert module.main(["--stdlib"]) == 0
    caches = list(root.rglob("*.pyc"))
    assert len(caches) == len(sources)
    for path, data in sources.items():
        cache = Path(importlib.util.cache_from_source(str(path), optimization=""))
        raw = cache.read_bytes()
        assert raw[:4] == importlib.util.MAGIC_NUMBER
        assert int.from_bytes(raw[4:8], "little") == 3
        assert raw[8:16] == importlib.util.source_hash(data)
        assert path.read_bytes() == data
        assert ".opt-" not in cache.name
    assert native.read_bytes() == b"unchanged native fixture"


def test_external_source_link_is_skipped(bytecode_helper, tmp_path: Path) -> None:
    module, root = bytecode_helper
    (root / "good.py").write_text("VALUE = 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 2\n")
    (root / "linked.py").symlink_to(outside)
    assert module.main(["--stdlib"]) == 0
    assert not (tmp_path / "__pycache__").exists()
    assert len(list(root.rglob("*.pyc"))) == 1


def test_external_cache_destination_is_rejected_before_write(bytecode_helper, tmp_path: Path) -> None:
    module, root = bytecode_helper
    (root / "good.py").write_text("VALUE = 1\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "__pycache__").symlink_to(outside, target_is_directory=True)
    assert module.main(["--stdlib"]) == 1
    assert list(outside.iterdir()) == []


def test_compile_failure_is_nonzero_and_quiet(bytecode_helper, capsys: pytest.CaptureFixture[str]) -> None:
    module, root = bytecode_helper
    (root / "broken.py").write_text("invalid syntax !\n")
    assert module.main(["--stdlib"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "표준 라이브러리 bytecode 준비에 실패했습니다.\n"


def test_docker_build_prepares_cache_after_locked_install_only() -> None:
    dockerfile = (_ROOT / "docker/dagster-host/Dockerfile").read_text()
    assert "--no-deps --require-hashes --only-binary=:all: --compile" in dockerfile
    assert dockerfile.index("pip check") < dockerfile.index(
        "python -I -B /tmp/dagster-host-build-bytecode.py --stdlib"
    )
    assert "USER dagster" in dockerfile


def test_docker_instructions_and_run_shell_syntax_are_valid() -> None:
    """줄 연결이 끊겨 shell 연산자가 Docker instruction이 되는 실수를 거절한다."""
    dockerfile = (_ROOT / "docker/dagster-host/Dockerfile").read_text()
    logical = dockerfile.replace("\\\n", " ").splitlines()
    valid = {"FROM", "ENV", "COPY", "RUN", "USER", "WORKDIR"}
    for line in logical:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        instruction, _, body = line.partition(" ")
        assert instruction in valid
        if instruction == "RUN":
            subprocess.run(["sh", "-n", "-c", body], check=True, capture_output=True)
