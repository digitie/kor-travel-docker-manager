"""허용된 Dagster 역할의 영속 state volume을 읽기 전용으로 검증한다."""

from __future__ import annotations

import os
import posixpath
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import docker

from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import transport_core_state as archive
from kor_travel_docker_manager.services.service_release import ServiceReleaseError

GENERATION_FILE = ".ktdm-state-generation"
LOCAL_DOCKER_HOST = "unix:///var/run/docker.sock"


@dataclass(frozen=True)
class VolumePolicy:
    role: str
    name: str
    destination: str
    state_owner_uid: int | None = None

    def filesystem_probe(self) -> str:
        return (
            "import json, os\n"
            f"s = os.stat({self.destination!r}, follow_symlinks=False)\n"
            "print(json.dumps([s.st_dev, s.st_ino]))"
        )


# 외부 Compose나 receipt는 이 allow-list를 확장할 수 없다.
_VOLUME_POLICIES = {
    ("transport", "dagster-code-server"): (
        "kor-travel-transport-dagster-state", archive.STATE_PATH
    ),
}


def policy_for(target: str, role: str) -> VolumePolicy:
    try:
        return VolumePolicy(role, *_VOLUME_POLICIES[target, role])
    except KeyError:
        raise ServiceReleaseError("이 Dagster target/role은 영속 state가 허용되지 않습니다") from None


def validate_volume_proof(policy: VolumePolicy, proof: dict) -> None:
    try:
        if (
            set(proof) != {"name", "driver", "scope", "mountpoint", "createdAt", "generation"}
            or proof["name"] != policy.name
            or proof["driver"] != "local"
            or proof["scope"] != "local"
            or not re.fullmatch(r"[0-9a-f]{32}", proof["generation"])
            or datetime.fromisoformat(proof["createdAt"]).utcoffset() is None
        ):
            raise ValueError
        path = Path(proof["mountpoint"])
        if not path.is_absolute() or str(path) != proof["mountpoint"] or path.name != "_data":
            raise ValueError
        if path.parent.name != policy.name or ".." in path.parts:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ServiceReleaseError("Dagster state volume 증거가 올바르지 않습니다") from exc


def _overlaps(left: str, right: str) -> bool:
    left, right = posixpath.normpath(left), posixpath.normpath(right)
    return (
        left == right
        or left.startswith(right.rstrip("/") + "/")
        or right.startswith(left.rstrip("/") + "/")
    )


def volume_declaration(policy: VolumePolicy, model: dict, project: str, mountpoint: str) -> dict:
    """등록 Compose의 전체 서비스에서 고정 volume의 유일한 소비자를 확인한다."""
    matches = []
    for role, definition in model["services"].items():
        for mount in definition.get("volumes", []):
            if not isinstance(mount, dict):
                raise ServiceReleaseError("Dagster Compose mount가 정규화되지 않았습니다")
            target = mount["target"]
            if mount["type"] == "volume":
                key = mount.get("source")
                spec = model.get("volumes", {}).get(key, {})
                if spec.get("name", f"{project}_{key}") == policy.name:
                    matches.append((role, mount, spec))
                    continue
            if role == policy.role and _overlaps(target, policy.destination):
                raise ServiceReleaseError("Dagster state 경로를 덮는 Compose mount가 있습니다")
            if (mount["type"] == "bind" and mount.get("read_only") is not True
                    and _overlaps(str(Path(mount["source"]).resolve()), mountpoint)):
                raise ServiceReleaseError("Dagster state volume의 Compose bind 별칭이 있습니다")
        if role == policy.role:
            for tmpfs in definition.get("tmpfs", []):
                if _overlaps(tmpfs.split(":", 1)[0], policy.destination):
                    raise ServiceReleaseError("Dagster state 경로를 덮는 tmpfs가 있습니다")
            for resource in ("configs", "secrets"):
                for ref in definition.get(resource, []):
                    if isinstance(ref, dict) and _overlaps(ref.get("target", ""), policy.destination):
                        raise ServiceReleaseError("Dagster state 경로를 덮는 Compose 리소스가 있습니다")
    if len(matches) != 1:
        raise ServiceReleaseError("Dagster state volume은 Compose에서 한 번만 사용해야 합니다")
    role, mount, spec = matches[0]
    if (
        role != policy.role
        or set(spec) != {"name", "external"}
        or spec["external"] is not True
        or set(mount) - {"type", "source", "target", "read_only", "volume"}
        or mount["target"] != policy.destination
        or mount.get("read_only", False) is not False
        or set(mount.get("volume") or {}) != {"nocopy"}
        or mount["volume"]["nocopy"] is not True
    ):
        raise ServiceReleaseError("Dagster state volume은 고정 external rw/nocopy mount여야 합니다")
    return mount


def _local_volume_identity(policy: VolumePolicy, client: Any) -> dict:
    if client.api.base_url != "http+docker://localhost":
        raise ServiceReleaseError("Dagster state volume 검증은 로컬 Unix Docker만 지원합니다")
    try:
        volume = client.api.inspect_volume(policy.name)
    except docker.errors.DockerException:
        raise ServiceReleaseError("사전 준비한 Dagster state volume을 inspect할 수 없습니다") from None
    identity = {
        key: volume.get(field)
        for key, field in (
            ("name", "Name"), ("driver", "Driver"), ("scope", "Scope"),
            ("mountpoint", "Mountpoint"), ("createdAt", "CreatedAt"),
        )
    }
    if volume.get("Options") not in ({}, None):
        raise ServiceReleaseError("Dagster state volume local driver 설정이 다릅니다")
    return identity


def _state_root(policy: VolumePolicy, root: Path) -> os.stat_result:
    release._trusted_ancestors(root.parent)
    info = root.lstat()
    owner_uid = release._OWNER_UID if policy.state_owner_uid is None else policy.state_owner_uid
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner_uid or info.st_mode & 0o022:
        raise ServiceReleaseError("Dagster state volume root 소유권·권한이 안전하지 않습니다")
    return info


def observe_volume_proof(policy: VolumePolicy, client: Any) -> dict:
    """Read a registered volume's generation for maintenance or receipt preparation."""
    proof = _local_volume_identity(policy, client)
    validate_volume_proof(policy, proof | {"generation": "0" * 32})
    mountpoint = Path(proof["mountpoint"])
    try:
        _state_root(policy, mountpoint)
        marker = mountpoint / GENERATION_FILE
        if marker.lstat().st_size != 33:
            raise ServiceReleaseError("Dagster state volume generation marker 크기가 다릅니다")
        generation = release._read_file(marker).decode("ascii")
        if not generation.endswith("\n"):
            raise ServiceReleaseError("Dagster state volume generation marker가 올바르지 않습니다")
    except (OSError, UnicodeDecodeError) as exc:
        raise ServiceReleaseError("Dagster state volume generation marker를 읽을 수 없습니다") from exc
    proof["generation"] = generation[:-1]
    validate_volume_proof(policy, proof)
    return proof


def verify_volume(policy: VolumePolicy, client: Any, proof: dict, records: list[dict], cs: dict | None) -> list[int]:
    """Docker identity, 유일한 참조, 고정 marker 및 비어 있지 않은 payload를 읽는다."""
    identity = _local_volume_identity(policy, client)
    if identity != {k: v for k, v in proof.items() if k != "generation"}:
        raise ServiceReleaseError("Dagster state volume identity가 다릅니다")
    mountpoint = identity["mountpoint"]
    expected_cid = cs["Id"] if cs is not None else None
    for record in records:
        observed = record.get("Mounts")
        if not isinstance(observed, list):
            raise ServiceReleaseError("Dagster state volume의 전체 Docker mount 관측이 불완전합니다")
        if record["Id"] == expected_cid:
            _verify_cs_mount(policy, record, mountpoint)
        for mount in observed:
            if record["Id"] == expected_cid and mount["Destination"] == policy.destination:
                continue
            if mount.get("Name") == policy.name or (
                mount.get("Type") in {"bind", "volume"}
                and mount.get("RW") is not False
                and _overlaps(str(Path(mount["Source"]).resolve()), mountpoint)
            ):
                raise ServiceReleaseError("Dagster state volume에 다른 컨테이너 또는 bind 별칭이 있습니다")
    if expected_cid is not None and not any(r["Id"] == expected_cid for r in records):
        raise ServiceReleaseError("Dagster CS mount를 관측할 수 없습니다")

    root = Path(mountpoint)
    try:
        before = _state_root(policy, root)
        marker = root / GENERATION_FILE
        marker_stat = marker.lstat()
        if (
            not stat.S_ISREG(marker_stat.st_mode)
            or stat.S_IMODE(marker_stat.st_mode) != 0o444
            or marker_stat.st_size != 33
            or release._read_file(marker) != (proof["generation"] + "\n").encode("ascii")
        ):
            raise ServiceReleaseError("Dagster state volume generation marker가 다릅니다")
        populated = False
        for name in ("artifacts", "compute_logs"):
            directory = root / name
            try:
                info = directory.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(info.st_mode):
                raise ServiceReleaseError("Dagster state volume 데이터 디렉터리가 안전하지 않습니다")
            with os.scandir(directory) as rows:
                populated |= next(rows, None) is not None
        if not populated:
            raise ServiceReleaseError("Dagster state volume에 artifact 또는 compute log가 없습니다")
        after = root.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ServiceReleaseError("검사 중 Dagster state volume 디렉터리가 변경되었습니다")
    except OSError as exc:
        raise ServiceReleaseError("Dagster state volume 또는 generation marker를 읽을 수 없습니다") from exc
    return [before.st_dev, before.st_ino]


def _verify_cs_mount(policy: VolumePolicy, record: dict, mountpoint: str) -> None:
    mounts = [m for m in record["Mounts"] if _overlaps(m["Destination"], policy.destination)]
    host = record.get("HostConfig") or {}
    configured = [m for m in host.get("Mounts") or [] if _overlaps(m["Target"], policy.destination)]
    if len(mounts) != 1 or len(configured) != 1:
        raise ServiceReleaseError("Dagster CS state mount가 없거나 경로가 겹칩니다")
    observed, intent = mounts[0], configured[0]
    options = intent.get("VolumeOptions") or {}
    if (
        observed["Destination"] != policy.destination
        or observed.get("Type") != "volume"
        or observed.get("Name") != policy.name
        or observed.get("Source") != mountpoint
        or observed.get("Driver") != "local"
        or observed.get("RW") is not True
        or intent["Target"] != policy.destination
        or intent.get("Type") != "volume"
        or intent.get("Source") != policy.name
        or intent.get("ReadOnly", False) is not False
        or set(options) - {"NoCopy", "DriverConfig"}
        or options.get("NoCopy") is not True
        or options.get("DriverConfig") not in ({}, None)
        or any(_overlaps(path, policy.destination) for path in (host.get("Tmpfs") or {}))
    ):
        raise ServiceReleaseError("Dagster CS의 실제 state mount가 승인한 rw/nocopy volume과 다릅니다")


def verify_seed(proof: dict, seed: dict) -> None:
    snapshot = archive.scan_state(Path(proof["mountpoint"]))
    if snapshot["entries"].pop(GENERATION_FILE, None) is None:
        raise ServiceReleaseError("전체 seed 비교 중 Dagster generation marker가 사라졌습니다")
    archive.validate_fingerprint(snapshot)
    if archive.semantic(snapshot) != archive.semantic(seed["fingerprint"]):
        raise ServiceReleaseError("Dagster state seed가 이전 CS의 전체 manifest와 다릅니다")
