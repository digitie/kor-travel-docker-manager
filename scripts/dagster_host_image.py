#!/usr/bin/env python3
"""공용 Dagster host 이미지(`kor-travel-dagster-host:<내용 해시>`)를 개발 장비에서 빌드하고 운영 박스로 옮긴다(ADR-67).

운영 박스(n150·odroid)는 이 이미지를 빌드하지 않는다(소유자 결정 2026-11-24). Windows 개발 장비의 WSL에서
빌드해 tar로 내보내고, 운영 박스에는 `docker load`로 넣기만 한다. 레지스트리는 두지 않는다(ADR-58 결정 9 —
두 호스트가 같은 태그로 다른 arch 내용을 갖는다). `ktdctl dagster-plane converge`·pinned 재구축은 이미지가
없거나 아래 내용 label이 태그와 다르면 빌드하지 않고 거부한다(`services/dagster_host_image.py`).

    # 개발 장비(WSL)에서 — 운영 박스에서는 거부한다
    python3 scripts/dagster_host_image.py build --platform linux/amd64   # n150
    python3 scripts/dagster_host_image.py build --platform linux/arm64   # odroid(qemu binfmt 필요)

    # 개발 장비에서 운영 박스로 — ssh로 `docker load`하고 운반된 이미지를 대조한다
    python3 scripts/dagster_host_image.py load --ssh <host> dist/dagster-host/<tar>

`build`는 tar 옆에 receipt(`<tar>.json`: 태그·platform·image ID·layer digest·tar sha256)를 남긴다. `load`는 받은 쪽의
arch·내용 label·layer digest가 receipt와 같아야 성공이다 — image ID는 저장소 종류(containerd snapshotter인 n150, overlay2인
odroid)에 따라 config digest와 manifest digest로 갈리므로 대조에 쓰지 않고 기록만 한다. layer digest는 압축을 푼 layer의
diff_id 목록의 sha256이라 저장소와 무관하다.

표준 라이브러리만 쓴다(설치본 venv 없이 저장소 체크아웃에서 돈다).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import IO

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "docker" / "dagster-host"
COMPOSE = ROOT / "docker-compose.yml"
REPOSITORY = "kor-travel-dagster-host"
#: 이 스크립트가 빌드한 이미지에만 붙는 label. 값은 내용 해시(태그)다 — converge가 태그와 대조한다.
CONTENT_LABEL = "io.kor-travel-docker-manager.dagster-host-content"
#: 운영 박스 표식 — 설치된 Manager release root. 개발 장비(WSL)에는 없다.
OPERATING_HOST_MARKER = Path("/opt/kor-travel-docker-manager")
PLATFORMS = {"linux/amd64": "amd64", "linux/arm64": "arm64"}
#: Docker 데몬 이름 → 이미지 arch 이름(OCI). 운영 호스트는 x86_64(n150)·aarch64(odroid)뿐이다.
_ARCH_ALIASES = {"x86_64": "amd64", "aarch64": "arm64"}
_COMPOSE_IMAGE = re.compile(r"^\s*image:\s*" + re.escape(REPOSITORY) + r":(\S+)\s*$", re.MULTILINE)


class HostImageError(RuntimeError):
    """빌드·운반·대조를 멈추는 계약 위반. 메시지는 비밀을 싣지 않는다."""


def dockerfile_copy_sources(dockerfile: str) -> list[str]:
    """Dockerfile의 `COPY` 원본(build context 상대 경로). 옵션(`--chmod=` 등)은 건너뛴다.

    한 줄 `COPY [--opt ...] <src...> <dest>`만 읽는다 — JSON 배열·heredoc·`--from`은 내용 해시의 파일 집합을
    흐리므로 거부한다(`backend/tests/test_dagster_shared_config.py`의 대조와 같은 규칙).
    """

    sources: list[str] = []
    for line in dockerfile.splitlines():
        words = line.split()
        if not words or words[0].upper() != "COPY":
            continue
        if "[" in line or "<<" in line or any(word.startswith("--from") for word in words):
            raise HostImageError(f"unsupported COPY form in Dockerfile: {line.strip()}")
        paths = [word for word in words[1:] if not word.startswith("--")]
        if len(paths) < 2:
            raise HostImageError(f"COPY without a source in Dockerfile: {line.strip()}")
        sources.extend(paths[:-1])
    return sources


def content_tag(context: Path | None = None) -> str:
    """이미지에 들어가는 파일(Dockerfile + COPY 원본)의 내용 해시 앞 16자.

    compose 주석의 재현 한 줄(`sha256sum <파일...> | sha256sum | cut -c1-16`, 이름순)과 같은 값이다.
    """

    context = context or CONTEXT
    dockerfile = (context / "Dockerfile").read_text(encoding="utf-8")
    names = sorted({"Dockerfile", *dockerfile_copy_sources(dockerfile)})
    listing = "".join(
        f"{hashlib.sha256((context / name).read_bytes()).hexdigest()}  {name}\n" for name in names
    )
    return hashlib.sha256(listing.encode()).hexdigest()[:16]


def compose_tag(compose: Path | None = None) -> str:
    """compose가 공용 host 서비스에 적은 태그 — 셋(storage migrate·daemon·webserver)이 하나여야 한다."""

    tags = set(_COMPOSE_IMAGE.findall((compose or COMPOSE).read_text(encoding="utf-8")))
    if len(tags) != 1:
        raise HostImageError(f"docker-compose.yml names {len(tags)} {REPOSITORY} tags, expected one")
    return next(iter(tags))


def layer_digest(diff_ids: list[str]) -> str:
    """압축을 푼 layer diff_id 목록의 sha256 — 저장소 종류와 무관한 이미지 내용 식별자."""

    if not diff_ids or not all(re.fullmatch(r"sha256:[0-9a-f]{64}", d) for d in diff_ids):
        raise HostImageError("image rootfs diff_ids are missing or malformed")
    return "sha256:" + hashlib.sha256("\n".join(diff_ids).encode()).hexdigest()


def read_archive(path: Path) -> dict[str, object]:
    """`docker load`용 tar(docker-archive)에서 태그·config digest·arch·label·layer digest를 읽는다."""

    with tarfile.open(path) as archive:
        manifest = json.load(_member(archive, "manifest.json"))
        if not isinstance(manifest, list) or len(manifest) != 1:
            raise HostImageError("the archive must hold exactly one image")
        entry = manifest[0]
        config_name = str(entry["Config"])
        config_bytes = _member(archive, config_name).read()
    config = json.loads(config_bytes)
    labels = (config.get("config") or {}).get("Labels") or {}
    return {
        "repo_tags": list(entry.get("RepoTags") or []),
        "config_digest": "sha256:" + hashlib.sha256(config_bytes).hexdigest(),
        "architecture": config.get("architecture"),
        "os": config.get("os"),
        "content_label": labels.get(CONTENT_LABEL),
        "layer_digest": layer_digest(list((config.get("rootfs") or {}).get("diff_ids") or [])),
    }


def _member(archive: tarfile.TarFile, name: str) -> IO[bytes]:
    handle = archive.extractfile(name)
    if handle is None:
        raise HostImageError(f"the archive has no {name}")
    return handle


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build(platform: str, output_dir: Path, *, builder: str | None = None) -> dict[str, object]:
    """개발 장비에서 한 platform의 host 이미지를 빌드해 tar와 receipt를 쓴다. 운영 박스에서는 거부한다."""

    if OPERATING_HOST_MARKER.exists():
        raise HostImageError(
            f"{OPERATING_HOST_MARKER} exists — this looks like an operating host, which must not build "
            "images (ADR-67). Build on the WSL dev box and load the tar here."
        )
    tag, named = content_tag(), compose_tag()
    if named != tag:
        raise HostImageError(
            f"docker-compose.yml names {REPOSITORY}:{named} but the build context hashes to {tag}"
        )
    arch = PLATFORMS[platform]
    reference = f"{REPOSITORY}:{tag}"
    output_dir.mkdir(parents=True, exist_ok=True)
    tar = output_dir / f"{REPOSITORY}_{tag}_{arch}.tar"
    command = [
        "docker", "buildx", "build",
        *(["--builder", builder] if builder else []),
        "--platform", platform,
        "--provenance=false", "--sbom=false",
        "--label", f"{CONTENT_LABEL}={tag}",
        "--tag", reference,
        "--output", f"type=docker,dest={tar}",
        str(CONTEXT),
    ]
    print("+ " + shlex.join(command), file=sys.stderr)
    subprocess.run(command, check=True)
    observed = read_archive(tar)
    if observed["repo_tags"] != [reference]:
        raise HostImageError(f"the archive is tagged {observed['repo_tags']}, expected [{reference}]")
    if observed["architecture"] != arch or observed["os"] != "linux":
        raise HostImageError(f"the archive is {observed['os']}/{observed['architecture']}, expected {platform}")
    if observed["content_label"] != tag:
        raise HostImageError("the archive lacks the content label")
    receipt = {
        "image": reference,
        "platform": platform,
        "content_label": {CONTENT_LABEL: tag},
        "config_digest": observed["config_digest"],
        "layer_digest": observed["layer_digest"],
        "tar": tar.name,
        "tar_sha256": _sha256_file(tar),
        "tar_bytes": tar.stat().st_size,
    }
    Path(f"{tar}.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


#: 받은 쪽에서 볼 것 — 한 줄 JSON. image ID는 기록만 한다(저장소 종류에 따라 뜻이 다르다).
_REMOTE_INSPECT = (
    '{{json .Id}} {{json .Architecture}} {{json .Os}} '
    '{{json (index .Config.Labels "' + CONTENT_LABEL + '")}} {{json .RootFS.Layers}}'
)


def verify_loaded(receipt: dict[str, object], inspect_output: str) -> dict[str, object]:
    """`docker image inspect --format _REMOTE_INSPECT` 출력이 receipt와 같은 이미지인가."""

    try:
        decoder = json.JSONDecoder()
        values: list[object] = []
        text = inspect_output.strip()
        while text:
            value, end = decoder.raw_decode(text)
            values.append(value)
            text = text[end:].strip()
        image_id, architecture, os_name, label, layers = values
    except ValueError as exc:
        raise HostImageError("the loaded image could not be inspected") from exc
    platform = str(receipt["platform"])
    tag = str(receipt["image"]).rsplit(":", 1)[1]
    problems = []
    if f"{os_name}/{architecture}" != platform:
        problems.append(f"platform {os_name}/{architecture} != {platform}")
    if label != tag:
        problems.append(f"content label {label!r} != {tag}")
    if layer_digest(list(layers or [])) != receipt["layer_digest"]:  # type: ignore[call-overload]
        problems.append("layer digest differs from the build receipt")
    if problems:
        raise HostImageError("the loaded image is not the built one: " + "; ".join(problems))
    return {"image": receipt["image"], "image_id": image_id, "layer_digest": receipt["layer_digest"]}


def load(ssh_host: str, tar: Path) -> dict[str, object]:
    """tar를 ssh로 `docker load`하고 받은 쪽 이미지를 receipt와 대조한다. 컨테이너는 건드리지 않는다."""

    receipt = json.loads(Path(f"{tar}.json").read_text(encoding="utf-8"))
    if _sha256_file(tar) != receipt["tar_sha256"]:
        raise HostImageError("the tar differs from its build receipt")
    # 받는 쪽 arch를 load **전에** 본다 — 다른 arch tar를 넣으면 같은 태그가 그 이미지로 옮겨 가 맞던 이미지를 덮는다.
    host_arch = subprocess.run(
        ["ssh", ssh_host, "docker", "info", "--format", shlex.quote("{{.Architecture}}")],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    host_platform = "linux/" + _ARCH_ALIASES.get(host_arch, host_arch)
    if host_platform != receipt["platform"]:
        raise HostImageError(f"{ssh_host} is {host_platform} but the tar is {receipt['platform']}")
    with tar.open("rb") as stream:
        subprocess.run(["ssh", ssh_host, "docker", "load"], stdin=stream, check=True)
    inspected = subprocess.run(
        ["ssh", ssh_host, "docker", "image", "inspect", "--format", shlex.quote(_REMOTE_INSPECT),
         str(receipt["image"])],
        check=True, capture_output=True, text=True,
    ).stdout
    return verify_loaded(receipt, inspected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="개발 장비(WSL)에서 빌드해 tar·receipt를 쓴다")
    build_parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    build_parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "dagster-host")
    build_parser.add_argument("--builder", help="docker buildx builder(arm64 교차 빌드용, 생략하면 현재 builder)")
    load_parser = commands.add_parser("load", help="tar를 ssh 호스트에 docker load하고 대조한다")
    load_parser.add_argument("--ssh", required=True, dest="ssh_host")
    load_parser.add_argument("tar", type=Path)
    commands.add_parser("tag", help="build context의 내용 해시 태그를 출력한다")
    args = parser.parse_args(argv)
    try:
        if args.command == "tag":
            result: object = {"content_tag": content_tag(), "compose_tag": compose_tag()}
        elif args.command == "build":
            result = build(args.platform, args.output_dir, builder=args.builder)
        else:
            result = load(args.ssh_host, args.tar)
    except (HostImageError, subprocess.CalledProcessError, OSError, KeyError, ValueError) as exc:
        print(f"dagster host image: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    os.umask(0o022)
    raise SystemExit(main())
