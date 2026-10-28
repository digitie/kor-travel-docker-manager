"""실행 중인 이미지만 관측해 서비스 release 표지를 등록하고 검증한다.

Docker 변경 명령은 호출하지 않는다. OCI revision은 관측된 라벨일 뿐 소스 전체의
동일성을 보증하지 않으며, 서로 다르거나 알 수 없으면 공통 revision은 비운다.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from kor_travel_docker_manager.services.c6c_deployment import manager_mutation_lock
from kor_travel_docker_manager.services.secure_state_file import (
    atomic_write_bytes,
    atomic_write_json,
    fsync_directory,
)
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_OWNER_UID = 0
_MAX_BYTES = 1024 * 1024
_SCHEMA = "ktdm.service-release.v1"
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_GROUP_KEYS = {"project", "working_dir", "config_files", "env_file", "runtime_services", "oneshot_images"}


class ServiceReleaseError(ValueError):
    """안전하게 읽거나 등록할 수 없는 release다."""


def _name(value: Any) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value) or value in {".", ".."}:
        raise ServiceReleaseError("서비스 또는 그룹 이름이 올바르지 않습니다")
    return value


def _image_id(value: Any) -> str:
    if not isinstance(value, str) or not _IMAGE.fullmatch(value):
        raise ServiceReleaseError("이미지는 완전한 sha256 ID여야 합니다")
    return value


def _absolute(value: Any, base: Path | None = None) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ServiceReleaseError("Compose 경로가 올바르지 않습니다")
    path = Path(value)
    if not path.is_absolute():
        if base is None:
            raise ServiceReleaseError("절대 경로가 필요합니다")
        path = base / path
    # Compose의 lexical 프로젝트 root는 bind mount 기준이므로 symlink를 따라가지 않는다.
    canonical = posixpath.normpath(str(path))
    if base is None and canonical != value:
        raise ServiceReleaseError("정규화된 절대 경로가 필요합니다")
    return canonical


def _profile(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict) or set(profile) != {"service", "groups"}:
        raise ServiceReleaseError("서비스 프로필 형태가 올바르지 않습니다")
    service = _name(profile["service"])
    source = profile["groups"]
    if not isinstance(source, dict) or not source:
        raise ServiceReleaseError("Compose 그룹이 필요합니다")
    groups: dict[str, Any] = {}
    seen: set[str] = set()
    for key in source:
        _name(key)
    for key, group in sorted(source.items()):
        _name(key)
        if not isinstance(group, dict) or set(group) != _GROUP_KEYS:
            raise ServiceReleaseError("Compose 그룹 형태가 올바르지 않습니다")
        project = _name(group["project"])
        working_dir = _absolute(group["working_dir"])
        files = group["config_files"]
        runtime = group["runtime_services"]
        oneshot = group["oneshot_images"]
        if (not isinstance(files, list) or not files or not isinstance(runtime, list)
                or not isinstance(oneshot, dict) or not (runtime or oneshot)):
            raise ServiceReleaseError("Compose 파일과 서비스 역할이 필요합니다")
        files = [_absolute(value, Path(working_dir)) for value in files]
        if len(files) != len(set(files)):
            raise ServiceReleaseError("Compose 파일이 중복되었습니다")
        names = [_name(value) for value in runtime] + [_name(value) for value in oneshot]
        if len(set(names)) != len(names) or seen.intersection(names):
            raise ServiceReleaseError("Compose 서비스 역할은 그룹 사이에서도 중복할 수 없습니다")
        seen.update(names)
        env = group["env_file"]
        groups[key] = {
            "project": project, "working_dir": working_dir, "config_files": files,
            "env_file": _absolute(env, Path(working_dir)) if env is not None else None,
            "runtime_services": sorted(runtime),
            "oneshot_images": {name: _image_id(oneshot[name]) for name in sorted(oneshot)},
        }
    return {"service": service, "groups": groups}


def _directory(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != _OWNER_UID
            or info.st_mode & 0o022 or path.resolve() != path):
        raise ServiceReleaseError("release 디렉터리 소유권·권한·경로가 안전하지 않습니다")


def _trusted_ancestors(root: Path) -> None:
    for path in (root, *root.parents):
        _directory(path)


def _root(root: Path) -> Path:
    root = Path(root)
    if not root.is_absolute():
        raise ServiceReleaseError("release root는 절대 경로여야 합니다")
    _trusted_ancestors(root)
    return root


def _read_file(path: Path) -> bytes:
    descriptor = None
    try:
        # FIFO도 writer를 기다리지 않고 열어 regular-file 검사에서 즉시 거부한다.
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != _OWNER_UID
                or before.st_nlink != 1 or before.st_mode & 0o022 or before.st_size > _MAX_BYTES):
            raise ServiceReleaseError("release 파일 소유권·권한·크기가 안전하지 않습니다")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            content = handle.read(_MAX_BYTES + 1)
            after = os.fstat(handle.fileno())
        current = path.lstat()
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
                or len(content) > _MAX_BYTES):
            raise ServiceReleaseError("읽는 도중 release 파일이 변경되었습니다")
        return content
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _timestamp(value: Any) -> None:
    if not isinstance(value, str):
        raise ServiceReleaseError("UTC 시각이 필요합니다")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ServiceReleaseError("UTC 시각이 필요합니다")


def _common_sha(images: dict[str, Any]) -> str | None:
    revisions = {image["source_sha"] for image in images.values()}
    return next(iter(revisions)) if len(revisions) == 1 and None not in revisions else None


def _manifest(value: Any, service: str, root: Path) -> dict[str, Any]:
    expected = {"schema", "service", "groups", "images", "source_sha", "adopted_at", "previous", "health"}
    if (not isinstance(value, dict) or set(value) != expected or value["schema"] != _SCHEMA
            or value["service"] != service):
        raise ServiceReleaseError("release manifest 스키마가 올바르지 않습니다")
    normalized = _profile({"service": service, "groups": value["groups"]})
    if normalized["groups"] != value["groups"]:
        raise ServiceReleaseError("manifest Compose 좌표가 정규화되지 않았습니다")
    roles = {name for group in value["groups"].values()
             for name in group["runtime_services"] + list(group["oneshot_images"])}
    images = value["images"]
    if not isinstance(images, dict) or set(images) != roles:
        raise ServiceReleaseError("manifest 이미지와 서비스 역할이 일치하지 않습니다")
    for image in images.values():
        if not isinstance(image, dict) or set(image) != {"id", "source_sha", "image_created_at"}:
            raise ServiceReleaseError("manifest 이미지 형태가 올바르지 않습니다")
        _image_id(image["id"])
        if image["source_sha"] is not None and (
                not isinstance(image["source_sha"], str) or not _SHA.fullmatch(image["source_sha"])):
            raise ServiceReleaseError("manifest revision이 올바르지 않습니다")
        if image["image_created_at"] is not None:
            _timestamp(image["image_created_at"])
    for group in value["groups"].values():
        if any(images[name]["id"] != image for name, image in group["oneshot_images"].items()):
            raise ServiceReleaseError("one-shot 이미지와 manifest가 일치하지 않습니다")
    if value["source_sha"] != _common_sha(images):
        raise ServiceReleaseError("공통 revision과 이미지 revision이 일치하지 않습니다")
    _timestamp(value["adopted_at"])
    previous = value["previous"]
    if previous is not None:
        if _absolute(previous) != previous or Path(previous).parent != root or Path(previous).name == "current":
            raise ServiceReleaseError("이전 release 경로가 root의 직접 하위가 아닙니다")
    health = value["health"]
    if not isinstance(health, dict) or set(health) != {"checked_at", "result"} or health["result"] != "healthy":
        raise ServiceReleaseError("manifest health가 올바르지 않습니다")
    _timestamp(health["checked_at"])
    return value


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ServiceReleaseError("manifest JSON 키가 중복되었습니다")
        result[key] = value
    return result


def _read_current(service: str, root: Path) -> tuple[Path, dict[str, Any]]:
    _directory(root / "current")
    pointer = root / "current" / service
    info = pointer.lstat()
    if not stat.S_ISLNK(info.st_mode) or info.st_uid != _OWNER_UID:
        raise ServiceReleaseError("현재 release 표지는 root 소유 symlink여야 합니다")
    # 표지는 한 번만 읽는다. 뒤의 두 파일은 고정한 실제 leaf에서 읽는다.
    target = Path(os.readlink(pointer))
    if not target.is_absolute() or target.parent != root or target.name == "current":
        raise ServiceReleaseError("현재 release 표지가 root의 직접 하위를 가리키지 않습니다")
    _directory(target)
    manifest = _manifest(json.loads(_read_file(target / "manifest.json"), object_pairs_hook=_json_object), service, root)
    overlay = load_yaml_rejecting_duplicate_keys(_read_file(target / "images.yml").decode("utf-8"))
    expected = {"services": {name: {"image": image["id"]} for name, image in manifest["images"].items()}}
    if overlay != expected:
        raise ServiceReleaseError("images.yml은 manifest 전체 서비스의 image만 포함해야 합니다")
    return target, manifest


def read_current_release(service: str, *, root: Path) -> dict[str, Any]:
    """고정한 현재 leaf의 공개 메타데이터 두 파일을 함께 검증한다."""
    try:
        leaf, manifest = _read_current(_name(service), _root(root))
        return {**manifest, "release_directory": str(leaf)}
    except (OSError, ValueError, TypeError, UnicodeError, yaml.YAMLError) as exc:
        if isinstance(exc, ServiceReleaseError):
            raise
        raise ServiceReleaseError("현재 release를 안전하게 읽을 수 없습니다") from exc


def _metadata(client: Any, image_id: str) -> dict[str, Any]:
    image = client.images.get(image_id)
    attrs = image.attrs
    if _image_id(attrs.get("Id")) != image_id:
        raise ServiceReleaseError("로컬 이미지 ID가 요청한 ID와 일치하지 않습니다")
    revision = (attrs.get("Config") or {}).get("Labels") or {}
    revision = revision.get("org.opencontainers.image.revision")
    revision = revision if isinstance(revision, str) and _SHA.fullmatch(revision) else None
    created = attrs.get("Created")
    if created is not None:
        _timestamp(created)
    return {"id": image_id, "source_sha": revision, "image_created_at": created}


def _observe(profile: dict[str, Any], client: Any) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    images: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}
    issues: list[str] = []
    for group in profile["groups"].values():
        for name in group["runtime_services"]:
            try:
                containers = client.containers.list(all=True, filters={"label": [
                    "com.docker.compose.project=" + group["project"],
                    "com.docker.compose.service=" + name,
                ]})
                if len(containers) != 1:
                    raise ServiceReleaseError("컨테이너가 정확히 하나여야 합니다")
                attrs = containers[0].attrs
                image_id = _image_id(attrs.get("Image"))
                state = attrs["State"]
                check = (attrs.get("Config") or {}).get("Healthcheck") or {}
                test = check.get("Test")
                enabled = bool(check) and test != ["NONE"]
                health = (state.get("Health") or {}).get("Status") if enabled else None
                identity, started = attrs.get("Id"), state.get("StartedAt")
                snapshot[name] = (identity, image_id, started, state.get("Running"), health)
                if not isinstance(identity, str) or not identity or not isinstance(started, str) or not started:
                    raise ServiceReleaseError("컨테이너 식별자 또는 시작 시각이 없습니다")
                images[name] = {"id": image_id, "source_sha": None, "image_created_at": None}
                images[name] = _metadata(client, image_id)
                if state.get("Running") is not True or (enabled and health != "healthy"):
                    raise ServiceReleaseError("컨테이너가 running/healthy 조건을 만족하지 않습니다")
            except ServiceReleaseError as exc:
                issues.append(f"{name}: {exc}")
            except Exception as exc:
                issues.append(f"{name}: runtime 조회 실패 ({type(exc).__name__})")
        for name, image_id in group["oneshot_images"].items():
            try:
                images[name] = {"id": image_id, "source_sha": None, "image_created_at": None}
                images[name] = _metadata(client, image_id)
                snapshot[name] = (image_id,)
            except ServiceReleaseError as exc:
                issues.append(f"{name}: one-shot {exc}")
            except Exception as exc:
                issues.append(f"{name}: one-shot 이미지 조회 실패 ({type(exc).__name__})")
    return images, snapshot, issues


def verify_release(service: str, *, root: Path, client: Any) -> dict[str, Any]:
    """표지와 실행 이미지의 차이를 보고하며 Docker를 변경하지 않는다."""
    manifest = read_current_release(service, root=root)
    actual, _, issues = _observe(manifest, client)
    images = {name: {"expected": image["id"], "actual": actual.get(name, {}).get("id")}
              for name, image in manifest["images"].items()}
    for name, ids in images.items():
        if ids["expected"] != ids["actual"]:
            issues.append(f"{name}: 실행 이미지 ID가 현재 release와 다릅니다")
    return {"service": service, "release_directory": manifest["release_directory"],
            "result": "drift" if issues else "healthy",
            "checked_at": datetime.now(UTC).isoformat(), "images": images, "issues": issues}


def _same_layout(current: dict[str, Any], profile: dict[str, Any]) -> bool:
    def layout(groups: dict[str, Any]) -> dict[str, Any]:
        return {key: {**group, "oneshot_images": sorted(group["oneshot_images"])}
                for key, group in groups.items()}
    return layout(current["groups"]) == layout(profile["groups"])


def adopt_release(profile: dict[str, Any], *, root: Path, client: Any,
                  replace_current: bool = False) -> dict[str, Any]:
    """G 아래 현재 실행 이미지만 등록하고, 완성 뒤 표지를 원자 교체한다."""
    profile = _profile(profile)
    with manager_mutation_lock():
        try:
            root = _root(root)
            current_dir = root / "current"
            if not os.path.lexists(current_dir):
                current_dir.mkdir(mode=0o755)
                os.chmod(current_dir, 0o755)
            _directory(current_dir)
            pointer = current_dir / profile["service"]
            previous, current = _read_current(profile["service"], root) if os.path.lexists(pointer) else (None, None)
            if current is not None and not _same_layout(current, profile):
                raise ServiceReleaseError("기존 release와 Compose 좌표 또는 서비스 역할이 다릅니다")
            images, snapshot, issues = _observe(profile, client)
            if issues:
                raise ServiceReleaseError("; ".join(issues))
            if current is not None and current["images"] == images and current["groups"] == profile["groups"]:
                second_images, second, issues = _observe(profile, client)
                if issues or second != snapshot or second_images != images:
                    raise ServiceReleaseError("관측 도중 runtime이 변경되어 등록하지 않았습니다")
                return {**current, "release_directory": str(previous)}
            if current is not None and not replace_current:
                raise ServiceReleaseError("현재 release와 실행 이미지가 다릅니다. 명시적 replace_current가 필요합니다")
            now = datetime.now(UTC).isoformat()
            manifest = {"schema": _SCHEMA, **profile, "images": images, "source_sha": _common_sha(images),
                        "adopted_at": now, "previous": str(previous) if previous else None,
                        "health": {"checked_at": now, "result": "healthy"}}
            _manifest(manifest, profile["service"], root)
            leaf = root / f"{profile['service']}-adopt-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex}"
            leaf.mkdir(mode=0o711)
            os.chmod(leaf, 0o711)
            overlay = {"services": {name: {"image": image["id"]} for name, image in images.items()}}
            atomic_write_bytes(leaf / "images.yml", yaml.safe_dump(overlay, sort_keys=True).encode(), mode=0o644)
            atomic_write_json(leaf / "manifest.json", manifest, mode=0o644)
            second_images, second, issues = _observe(profile, client)
            if issues or second != snapshot or second_images != images:
                raise ServiceReleaseError("관측 도중 runtime이 변경되어 등록하지 않았습니다")
            temporary = current_dir / f".{profile['service']}-{uuid4().hex}.tmp"
            try:
                temporary.symlink_to(leaf)
                os.replace(temporary, pointer)
                fsync_directory(current_dir)
            finally:
                temporary.unlink(missing_ok=True)
            return {**manifest, "release_directory": str(leaf)}
        except (OSError, ValueError, TypeError, UnicodeError, yaml.YAMLError) as exc:
            if isinstance(exc, ServiceReleaseError):
                raise
            raise ServiceReleaseError("release를 안전하게 등록할 수 없습니다") from exc
