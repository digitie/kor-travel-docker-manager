"""`ensure` 프리플라이트 효과 검사(ADR-58 결정 9·R5, T-MGR-05).

`ensure`가 `up`을 부르기 **전에** 렌더된 compose 문서(이미 `docker compose config`로 해석한 것 — 그 렌더가 실패하면
`ensure`는 여기까지 오지 않는다)에서 파생한 두 가지를 본다. 둘 다 값이 아니라 효과다:

1. **이미지 arch == 호스트 arch.** 올릴 서비스의 이미지가 **로컬에 있으면** 그 `.Architecture`가 Docker 데몬의
   `Architecture`와 같아야 한다. 두 호스트가 같은 태그로 다른 arch 내용을 갖는 것이 설계라(결정 9 — 레지스트리 없음)
   amd64 이미지를 arm64 호스트에 올리면 `exec format error`로 재시작만 반복한다. 로컬에 없는 이미지는 보지 않는다 —
   `up --build`가 이 호스트에서 빌드하거나 pull이 호스트 플랫폼을 고른다.
2. **쓰기 bind의 장치.** root `.env`의 `KTDM_DATA_SOURCE_DEVICE`가 **설정됐을 때만**, 올릴 서비스의 쓰기 가능
   bind 소스마다 `findmnt -T <경로> -o SOURCE`의 장치가 그 값이어야 한다(odroid: PGDATA 등 데이터가 NVMe가 아니라
   eMMC에 조용히 initdb되는 것을 막는다 — R5). 읽기 전용 bind(설정 파일)는 보지 않는다. 미설정이면 아무것도 하지
   않는다(n150 무변경).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Final

from kor_travel_docker_manager.services.errors import DeploymentContractError

DATA_SOURCE_DEVICE_ENV: Final = "KTDM_DATA_SOURCE_DEVICE"

#: `uname -m`/Docker 데몬 이름 → 이미지 `.Architecture` 이름(OCI).
_ARCH_ALIASES: Final = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm", "i686": "386"}

Runner = Callable[[list[str]], "subprocess.CompletedProcess[bytes]"]


def _run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, cwd="/", capture_output=True, check=False, timeout=30)


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def _arch(value: str) -> str:
    value = value.strip()
    return _ARCH_ALIASES.get(value, value)


def _host_arch(run: Runner) -> str:
    try:
        completed = run(["docker", "info", "--format", "{{.Architecture}}"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError("ensure preflight: Docker host architecture cannot be read") from exc
    arch = _text(completed.stdout).strip()
    if completed.returncode != 0 or not arch:
        raise DeploymentContractError("ensure preflight: Docker host architecture cannot be read")
    return _arch(arch)


def _image_arch(image: str, run: Runner) -> str | None:
    """로컬 이미지의 arch. 로컬에 없으면 None(검사 대상이 아니다)."""
    try:
        completed = run(["docker", "image", "inspect", "--format", "{{.Architecture}}", image])
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(f"ensure preflight: image {image} cannot be inspected") from exc
    if completed.returncode != 0:
        return None
    return _arch(_text(completed.stdout))


def _source_device(path: str, run: Runner) -> str:
    """`findmnt -T`의 SOURCE에서 장치만(`/dev/nvme0n1p1[/sub]` → `/dev/nvme0n1p1`). 없는 경로는 가장 가까운 조상."""
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        completed = run(["findmnt", "-n", "-T", str(probe), "-o", "SOURCE"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(f"ensure preflight: mount source of {path} cannot be read") from exc
    source = _text(completed.stdout).strip()
    if completed.returncode != 0 or not source:
        raise DeploymentContractError(f"ensure preflight: mount source of {path} cannot be read")
    return source.split("[", 1)[0]


def ensure_preflight(
    resolved: Mapping[str, Any],
    services: Iterable[str],
    environment: Mapping[str, str],
    *,
    run: Runner = _run,
) -> None:
    """올릴 `services`에 대해 위 두 효과를 본다. 위반이면 `DeploymentContractError`(아무것도 바꾸기 전)."""
    rendered = resolved.get("services") or {}
    names = [name for name in dict.fromkeys(services) if name in rendered]

    images = {name: rendered[name].get("image") for name in names if rendered[name].get("image")}
    if images:
        host = _host_arch(run)
        wrong = []
        for name, image in images.items():
            arch = _image_arch(str(image), run)
            if arch is not None and arch != host:
                wrong.append(f"{name} ({image}: {arch})")
        if wrong:
            raise DeploymentContractError(
                f"ensure preflight: image architecture differs from this host ({host}): " + ", ".join(wrong)
            )

    device = (environment.get(DATA_SOURCE_DEVICE_ENV) or "").strip()
    if not device:
        return
    misplaced = []
    for name in names:
        for volume in rendered[name].get("volumes") or []:
            if not isinstance(volume, Mapping) or volume.get("type") != "bind" or volume.get("read_only"):
                continue
            source = str(volume.get("source") or "")
            actual = _source_device(source, run)
            if actual != device:
                misplaced.append(f"{name}: {source} is on {actual}")
    if misplaced:
        raise DeploymentContractError(
            f"ensure preflight: writable binds must be on {DATA_SOURCE_DEVICE_ENV}={device}: " + "; ".join(misplaced)
        )
