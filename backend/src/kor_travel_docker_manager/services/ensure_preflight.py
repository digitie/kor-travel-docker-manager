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
3. **PostgreSQL 서버 이미지**(T-MGR-03 리뷰): 올릴 서비스 중 registry가 PostgreSQL로 선언한 것의 렌더된 이미지가 digest
   핀이고 PGDATA major인지(`postgres_image.postgres_image_violations` — `targets validate`와 같은 규칙).

**범위**: 이 검사는 `ensure`(UI·`ktdctl ensure`)만 거친다. pinned 재구축·공용 plane 재생성(`dagster-plane converge`)·복구
경로는 거치지 않는다 — 그 경로들은 이미 같은 호스트에 설치된 이미지·bind를 다시 맞추는 일이고, odroid의 첫 배치는
`ensure`로 올라가므로 첫 배치는 이 검사를 받는다(받아들인 공백).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Final

from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.postgres_image import (
    declared_postgres_services,
    postgres_image_violations,
)

DATA_SOURCE_DEVICE_ENV: Final = "KTDM_DATA_SOURCE_DEVICE"

#: Docker 데몬 이름 → 이미지 `.Architecture` 이름(OCI). 운영 호스트는 x86_64(n150)·aarch64(odroid)뿐이다.
_ARCH_ALIASES: Final = {"x86_64": "amd64", "aarch64": "arm64"}

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
    build: bool = False,
    run: Runner = _run,
    postgres_services: Iterable[str] | None = None,
) -> None:
    """compose가 만들거나 시작할 `services`(호출자가 렌더된 `depends_on` closure로 넓힌 것)에 대해 위 두 효과를 본다.
    위반이면 `DeploymentContractError`(아무것도 바꾸기 전).

    `build`(`ensure --build`)면 `build:`가 있는 서비스는 arch를 보지 않는다 — 그 명령이 이 호스트에서 이미지를 다시
    만들어 어긋남을 고치는 길이다(막으면 고치는 명령 자체가 막힌다).
    """
    rendered = resolved.get("services") or {}
    names = [name for name in dict.fromkeys(services) if name in rendered]

    if postgres_services is None:
        from kor_travel_docker_manager.services.registry import load_targets_config

        postgres_services = declared_postgres_services(load_targets_config())
    image_violations = postgres_image_violations(rendered, set(postgres_services) & set(names))
    if image_violations:
        raise DeploymentContractError("ensure preflight: " + "; ".join(image_violations))

    images = {
        name: rendered[name].get("image")
        for name in names
        if rendered[name].get("image") and not (build and rendered[name].get("build"))
    }
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
