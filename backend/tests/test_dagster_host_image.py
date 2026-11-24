"""공용 Dagster host 이미지 — 개발 장비에서 빌드·운반하고 운영 박스는 받은 이미지만 쓴다(ADR-67).

두 쪽을 본다:

- `scripts/dagster_host_image.py`(개발 장비): 내용 해시 태그가 compose와 같고, 빌드는 내용 label을 붙이며 운영 박스 표식이
  있으면 거부하고, 운반한 쪽의 이미지가 receipt(arch·label·layer digest)와 같아야 성공이다.
- `services/dagster_host_image.py`(운영 박스 converge): 이미지가 없거나 label·arch가 맞지 않으면 거부한다.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from kor_travel_docker_manager.services import dagster_host_image
from kor_travel_docker_manager.services.errors import DeploymentContractError

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "dagster_host_image.py"
_TAG = "1234567890abcdef"  # 임의의 16자 — 합성 fixture 전용(compose 태그와 무관)
_ID = "sha256:" + "a" * 64
_DIFF_IDS = ["sha256:" + "1" * 64, "sha256:" + "2" * 64]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("manager_dagster_host_image", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- 운영 박스: converge가 쓰기 전에 보는 것 ---------------------------------------------------------------------------


def _runner(inspect: tuple[int, str], host_arch: str = "x86_64"):
    calls: list[list[str]] = []

    def run(command: list[str]) -> Any:
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=inspect[0], stdout=inspect[1])
        assert command[:2] == ["docker", "info"]
        return SimpleNamespace(returncode=0, stdout=host_arch + "\n")

    return run, calls


@pytest.mark.parametrize("host,arch", [("x86_64", "amd64"), ("aarch64", "arm64")])
def test_a_loaded_image_with_the_content_label_and_host_arch_is_accepted(host: str, arch: str) -> None:
    run, calls = _runner((0, f"{_ID}|{arch}|{_TAG}\n"), host)

    image = f"kor-travel-dagster-host:{_TAG}"
    assert dagster_host_image.require_prebuilt_host_image(image, run=run) == _ID
    assert calls[0][-1] == image and dagster_host_image.CONTENT_LABEL in calls[0][-2]


def test_an_image_that_is_not_on_the_host_is_refused_with_the_way_to_load_it() -> None:
    run, _calls = _runner((1, ""))

    with pytest.raises(DeploymentContractError, match="not on this host.*do not build.*load"):
        dagster_host_image.require_prebuilt_host_image(f"kor-travel-dagster-host:{_TAG}", run=run)


@pytest.mark.parametrize("label", ["<no value>", "", "0a02ea5b1d52d1f9"])
def test_an_image_built_some_other_way_is_refused(label: str) -> None:
    """운영 박스의 옛 compose build로 만든 이미지(label 없음)나 다른 내용에 붙인 같은 태그를 쓰지 않는다."""

    run, _calls = _runner((0, f"{_ID}|amd64|{label}"))

    with pytest.raises(DeploymentContractError, match="was not built by scripts/dagster_host_image.py"):
        dagster_host_image.require_prebuilt_host_image(f"kor-travel-dagster-host:{_TAG}", run=run)


def test_an_image_of_the_other_arch_is_refused() -> None:
    run, _calls = _runner((0, f"{_ID}|amd64|{_TAG}"), host_arch="aarch64")

    with pytest.raises(DeploymentContractError, match="is amd64, but this host is arm64"):
        dagster_host_image.require_prebuilt_host_image(f"kor-travel-dagster-host:{_TAG}", run=run)


@pytest.mark.parametrize("image", [
    "kor-travel-dagster-host:latest", "kor-travel-dagster-host", f"other:{_TAG}",
    f"kor-travel-dagster-host:{_TAG}@sha256:" + "b" * 64,
])
def test_only_content_tagged_host_images_are_accepted(image: str) -> None:
    run, calls = _runner((0, f"{_ID}|amd64|{_TAG}"))

    with pytest.raises(DeploymentContractError, match="not a content-tagged"):
        dagster_host_image.require_prebuilt_host_image(image, run=run)
    assert calls == []


@pytest.mark.parametrize("stdout", ["", "sha256:short|amd64|x", f"{_ID}|amd64"])
def test_an_unreadable_inspect_is_refused(stdout: str) -> None:
    run, _calls = _runner((0, stdout))

    with pytest.raises(DeploymentContractError, match="cannot inspect"):
        dagster_host_image.require_prebuilt_host_image(f"kor-travel-dagster-host:{_TAG}", run=run)


def test_the_converge_label_is_the_one_the_build_script_sets() -> None:
    script = _script()
    assert script.CONTENT_LABEL == dagster_host_image.CONTENT_LABEL
    assert script.REPOSITORY == dagster_host_image.HOST_IMAGE_REPOSITORY


# --- 개발 장비: 빌드 스크립트 -----------------------------------------------------------------------------------------------


def test_the_script_hashes_the_build_context_to_the_compose_tag() -> None:
    """compose 주석의 coreutils 한 줄·test_dagster_shared_config의 독립 대조와 같은 값이다."""

    script = _script()
    assert script.content_tag() == script.compose_tag()


def test_two_compose_tags_are_refused(tmp_path: Path) -> None:
    script = _script()
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  a:\n    image: kor-travel-dagster-host:aaaa\n  b:\n    image: kor-travel-dagster-host:bbbb\n",
        encoding="utf-8",
    )
    with pytest.raises(script.HostImageError, match="2 kor-travel-dagster-host tags"):
        script.compose_tag(compose)


@pytest.mark.parametrize("line", [
    'COPY ["a b", "/a"]', "COPY <<EOF /a", "COPY --from=build /out /out", "COPY /only",
])
def test_unhashable_copy_forms_are_refused(line: str) -> None:
    script = _script()
    with pytest.raises(script.HostImageError):
        script.dockerfile_copy_sources(line + "\n")


def _archive(path: Path, *, tag: str = _TAG, arch: str = "amd64", label: str | None = _TAG) -> Path:
    config = {
        "architecture": arch, "os": "linux",
        "config": {"Labels": {} if label is None else {dagster_host_image.CONTENT_LABEL: label}},
        "rootfs": {"type": "layers", "diff_ids": _DIFF_IDS},
    }
    config_bytes = json.dumps(config).encode()
    name = "blobs/sha256/" + hashlib.sha256(config_bytes).hexdigest()
    manifest = json.dumps([{"Config": name, "RepoTags": [f"kor-travel-dagster-host:{tag}"], "Layers": []}])
    with tarfile.open(path, "w") as archive:
        for member, data in ((name, config_bytes), ("manifest.json", manifest.encode())):
            info = tarfile.TarInfo(member)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def test_the_archive_reader_returns_the_config_digest_label_and_layer_digest(tmp_path: Path) -> None:
    script = _script()
    observed = script.read_archive(_archive(tmp_path / "image.tar"))

    assert observed["repo_tags"] == [f"kor-travel-dagster-host:{_TAG}"]
    assert observed["architecture"] == "amd64" and observed["content_label"] == _TAG
    assert observed["layer_digest"] == script.layer_digest(_DIFF_IDS)
    assert str(observed["config_digest"]).startswith("sha256:")


def _fake_build(script: ModuleType, monkeypatch: pytest.MonkeyPatch, **archive: Any) -> list[list[str]]:
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> Any:
        commands.append(command)
        dest = next(word for word in command if word.startswith("type=docker,dest="))
        _archive(Path(dest.split("dest=", 1)[1]), **archive)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(script.subprocess, "run", run)
    monkeypatch.setattr(script, "OPERATING_HOST_MARKER", Path("/nonexistent/ktdm-marker"))
    return commands


def test_build_labels_the_image_and_writes_a_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _script()
    tag = script.content_tag()
    commands = _fake_build(script, monkeypatch, tag=tag, label=tag)

    receipt = script.build("linux/amd64", tmp_path)

    (command,) = commands
    assert command[:3] == ["docker", "buildx", "build"]
    assert command[command.index("--platform") + 1] == "linux/amd64"
    assert command[command.index("--label") + 1] == f"{script.CONTENT_LABEL}={tag}"
    assert command[command.index("--tag") + 1] == f"kor-travel-dagster-host:{tag}"
    assert "--provenance=false" in command and command[-1] == str(script.CONTEXT)
    tar = tmp_path / f"kor-travel-dagster-host_{tag}_amd64.tar"
    assert json.loads(Path(f"{tar}.json").read_text(encoding="utf-8")) == receipt
    assert receipt["tar_sha256"] == hashlib.sha256(tar.read_bytes()).hexdigest()
    assert receipt["layer_digest"] == script.layer_digest(_DIFF_IDS)


@pytest.mark.parametrize("archive,match", [
    ({"arch": "arm64"}, "expected linux/amd64"),
    ({"label": None}, "lacks the content label"),
    ({"tag": "0" * 16}, "expected"),
])
def test_build_refuses_an_archive_that_is_not_what_it_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archive: dict[str, Any], match: str
) -> None:
    script = _script()
    tag = script.content_tag()
    _fake_build(script, monkeypatch, **{"tag": tag, "label": tag, **archive})

    with pytest.raises(script.HostImageError, match=match):
        script.build("linux/amd64", tmp_path)


def test_build_refuses_on_an_operating_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """운영 박스(설치된 release root가 있는 호스트)에서는 docker를 부르기 전에 멈춘다."""

    script = _script()
    commands = _fake_build(script, monkeypatch)
    monkeypatch.setattr(script, "OPERATING_HOST_MARKER", tmp_path)

    with pytest.raises(script.HostImageError, match="operating host"):
        script.build("linux/amd64", tmp_path / "out")
    assert commands == []


def test_build_refuses_when_compose_names_another_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _script()
    commands = _fake_build(script, monkeypatch)
    compose = tmp_path / "compose.yml"
    compose.write_text("    image: kor-travel-dagster-host:0000000000000000\n", encoding="utf-8")
    monkeypatch.setattr(script, "COMPOSE", compose)

    with pytest.raises(script.HostImageError, match="hashes to"):
        script.build("linux/amd64", tmp_path / "out")
    assert commands == []


def _receipt(script: ModuleType) -> dict[str, Any]:
    return {
        "image": f"kor-travel-dagster-host:{_TAG}", "platform": "linux/amd64",
        "layer_digest": script.layer_digest(_DIFF_IDS),
    }


def _inspect_line(arch: str = "amd64", label: str | None = _TAG, layers: list[str] = _DIFF_IDS) -> str:
    return " ".join(json.dumps(v) for v in (_ID, arch, "linux", label, layers)) + "\n"


def test_a_loaded_image_matching_the_receipt_is_accepted() -> None:
    script = _script()
    assert script.verify_loaded(_receipt(script), _inspect_line()) == {
        "image": f"kor-travel-dagster-host:{_TAG}", "image_id": _ID,
        "layer_digest": script.layer_digest(_DIFF_IDS),
    }


@pytest.mark.parametrize("line,match", [
    (_inspect_line(arch="arm64"), "platform linux/arm64"),
    (_inspect_line(label=None), "content label None"),
    (_inspect_line(layers=list(reversed(_DIFF_IDS))), "layer digest"),
    ("not json", "could not be inspected"),
])
def test_a_loaded_image_that_differs_from_the_receipt_is_refused(line: str, match: str) -> None:
    script = _script()
    with pytest.raises(script.HostImageError, match=match):
        script.verify_loaded(_receipt(script), line)


def test_load_streams_the_tar_over_ssh_and_checks_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    tar = _archive(tmp_path / "image.tar")
    receipt = {**_receipt(script), "tar_sha256": hashlib.sha256(tar.read_bytes()).hexdigest()}
    Path(f"{tar}.json").write_text(json.dumps(receipt), encoding="utf-8")
    calls: list[tuple[list[str], bool]] = []

    def run(command: list[str], **kwargs: Any) -> Any:
        calls.append((command, "stdin" in kwargs))
        return SimpleNamespace(returncode=0, stdout=_inspect_line())

    monkeypatch.setattr(script.subprocess, "run", run)

    assert script.load("ops-host", tar)["image_id"] == _ID
    assert calls[0] == (["ssh", "ops-host", "docker", "load"], True)
    assert calls[1][0][:4] == ["ssh", "ops-host", "docker", "image"]
    assert len(calls) == 2  # 컨테이너·compose는 건드리지 않는다


def test_load_refuses_a_tar_that_differs_from_its_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _script()
    tar = _archive(tmp_path / "image.tar")
    Path(f"{tar}.json").write_text(json.dumps({**_receipt(script), "tar_sha256": "0" * 64}), encoding="utf-8")
    monkeypatch.setattr(script.subprocess, "run", lambda *a, **k: pytest.fail("must not ssh"))

    with pytest.raises(script.HostImageError, match="differs from its build receipt"):
        script.load("ops-host", tar)


def test_the_script_runs_without_the_manager_package() -> None:
    """설치본 venv 없이 저장소 체크아웃의 시스템 python3로 돈다 — 표준 라이브러리만 import한다."""

    result = subprocess.run(
        [sys.executable, "-I", str(_SCRIPT), "tag"], check=True, capture_output=True, text=True,
    )
    tags = json.loads(result.stdout)
    assert tags["content_tag"] == tags["compose_tag"]
