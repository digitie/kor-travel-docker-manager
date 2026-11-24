"""공용 Dagster host 이미지는 운영 박스에서 빌드하지 않는다 — 받은 이미지만 쓴다(ADR-67).

`kor-travel-dagster-host:<내용 해시>`는 Windows 개발 장비의 WSL에서 `scripts/dagster_host_image.py build`로 빌드해 tar로
옮기고 운영 박스에서는 `docker load`만 한다(소유자 결정 2026-11-24: n150·odroid 빌드 금지). 그래서 plane 수렴(pinned
재구축 안·`ktdctl dagster-plane converge`)은 이미지가 없을 때 `--build`를 붙이지 않고 **바꾸기 전에** 거부한다.

받은 이미지가 그 태그의 내용인지는 세 가지로 본다:

- 이 호스트에 있다(`docker image inspect`).
- 내용 label(`CONTENT_LABEL`)이 태그와 같다 — 빌드 스크립트만 붙이는 label이라, 손으로 다른 이미지에 같은 태그를
  붙였거나 옛 경로(운영 박스의 compose build)로 만든 이미지는 걸린다. 위조 방지가 아니라 경로 확인이다.
- arch가 Docker 데몬의 arch다 — 두 호스트가 같은 태그로 다른 arch 내용을 갖는 것이 설계라(ADR-58 결정 9) amd64 tar를
  odroid에 넣으면 `exec format error`로 재시작만 반복한다.

image ID는 대조하지 않는다 — 저장소 종류(n150 containerd snapshotter, odroid overlay2)마다 manifest digest와 config
digest로 뜻이 갈린다. 운반된 내용의 대조(layer digest)는 `load` 단계가 receipt와 한다.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from typing import Final

from kor_travel_docker_manager.services.errors import DeploymentContractError

HOST_IMAGE_REPOSITORY: Final = "kor-travel-dagster-host"
#: `scripts/dagster_host_image.py`가 빌드할 때만 붙이는 label. 값은 내용 해시(태그).
CONTENT_LABEL: Final = "io.kor-travel-docker-manager.dagster-host-content"
_TAGGED: Final = re.compile(re.escape(HOST_IMAGE_REPOSITORY) + r":([0-9a-f]{16})")
_ARCH_ALIASES: Final = {"x86_64": "amd64", "aarch64": "arm64"}
_INSPECT_FORMAT: Final = (
    '{{.Id}}|{{.Architecture}}|{{index .Config.Labels "' + CONTENT_LABEL + '"}}'
)
_HOW_TO_LOAD: Final = (
    "operating hosts do not build it (ADR-67) — build it on the WSL dev box with "
    "`scripts/dagster_host_image.py build` and load it with `... load --ssh <host>` "
    "(docs/docker-management.md)"
)

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd="/", capture_output=True, text=True, check=False, timeout=60)


def _arch(value: str) -> str:
    value = value.strip()
    return _ARCH_ALIASES.get(value, value)


def require_prebuilt_host_image(image: str, *, run: Runner = _run) -> str:
    """`image`가 이 호스트에 받아 둔 host 이미지이고 내용 label·arch가 맞으면 그 image ID. 아니면 거부한다."""

    match = _TAGGED.fullmatch(image)
    if match is None:
        raise DeploymentContractError(
            f"{image} is not a content-tagged {HOST_IMAGE_REPOSITORY} image"
        )
    tag = match.group(1)
    try:
        inspected = run(["docker", "image", "inspect", "--format", _INSPECT_FORMAT, image])
        host = run(["docker", "info", "--format", "{{.Architecture}}"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(f"cannot inspect {image}") from exc
    if inspected.returncode != 0:
        raise DeploymentContractError(f"{image} is not on this host; {_HOW_TO_LOAD}")
    parts = inspected.stdout.strip().split("|")
    host_arch = _arch(host.stdout) if host.returncode == 0 else ""
    if len(parts) != 3 or re.fullmatch(r"sha256:[0-9a-f]{64}", parts[0]) is None or not host_arch:
        raise DeploymentContractError(f"cannot inspect {image}")
    image_id, arch, label = parts
    if label != tag:
        raise DeploymentContractError(
            f"{image} on this host was not built by scripts/dagster_host_image.py "
            f"(label {CONTENT_LABEL} is not {tag}); {_HOW_TO_LOAD}"
        )
    if _arch(arch) != host_arch:
        raise DeploymentContractError(
            f"{image} on this host is {_arch(arch)}, but this host is {host_arch}; {_HOW_TO_LOAD}"
        )
    return image_id
