"""호스트 정본에서 공용 instance bytes·경로·digest를 함께 유도한다.

조회는 쓰지 않는다. 비기본 구성의 준비만 기존 Manager G 아래에서 수행하며,
기본과 같은 호스트는 원 source 파일을 그대로 써 불필요한 재생성을 피한다.
"""
from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.registry import (
    current_host,
    get_project_root,
    load_targets_config,
)
from kor_travel_docker_manager.services.secure_state_file import atomic_write_bytes, fsync_directory
from kor_travel_docker_manager.services.trusted_install import TRUSTED_STATE_ROOT
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

CONFIG_ENV = "KTDM_DAGSTER_INSTANCE_CONFIG"
DIGEST_ENV = "KTDM_DAGSTER_INSTANCE_DIGEST"
_POLICY = "dagster_max_concurrent_runs"
_BASE = Path("config/dagster-shared/dagster.yaml")
_MAX_BYTES = 1 << 20
_OWNER_UID = 0


@dataclass(frozen=True)
class SharedDagsterInstance:
    host: str
    max_concurrent_runs: int
    path: Path
    sha256: str
    uses_base: bool

    def as_dict(self) -> dict[str, object]:
        """외부 공식 배포 caller가 읽는 비밀 없는 고정 네 필드다."""
        return {"host": self.host, "max_concurrent_runs": self.max_concurrent_runs,
                "path": str(self.path), "sha256": self.sha256}


def _configuration(raw: bytes, maximum: int) -> bytes:
    """YAML 정수 한 자리만 바꾼다. 기본이면 원 bytes를 그대로 돌려준다."""
    try:
        text = raw.decode("utf-8")
        document = load_yaml_rejecting_duplicate_keys(text)
        original = document["concurrency"]["runs"]["max_concurrent_runs"]
        if type(original) is not int or original <= 0 or type(maximum) is not int or maximum <= 0:
            raise ValueError("invalid maximum")
        if original == maximum:
            return raw
        node = yaml.compose(text)
        for key in ("concurrency", "runs", "max_concurrent_runs"):
            if not isinstance(node, yaml.MappingNode):
                raise ValueError("invalid instance mapping")
            node = next(value for name, value in node.value if name.value == key)
        if not isinstance(node, yaml.ScalarNode) or node.tag != "tag:yaml.org,2002:int":
            raise ValueError("invalid maximum node")
        result = (text[:node.start_mark.index] + str(maximum) + text[node.end_mark.index:]).encode("utf-8")
        document["concurrency"]["runs"]["max_concurrent_runs"] = maximum
        if load_yaml_rejecting_duplicate_keys(result.decode("utf-8")) != document:
            raise ValueError("instance configuration differs")
        return result
    except (KeyError, TypeError, ValueError, StopIteration, UnicodeError, yaml.YAMLError) as exc:
        raise DeploymentContractError("shared Dagster instance configuration is invalid") from exc


def _base_bytes(root: Path) -> bytes:
    """기본 파일도 안전한 실제 파일을 bounded read한다. 설치 root alias는 보존한다."""
    descriptor = None
    try:
        path = root / _BASE
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        operating = os.path.lexists(root / ".ktdm-source-revision")
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > _MAX_BYTES or stat.S_IMODE(before.st_mode) & 0o022
                or (operating and before.st_uid != _OWNER_UID)):
            raise ValueError("unsafe base file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(_MAX_BYTES + 1)
            after = os.fstat(handle.fileno())
        current = path.lstat()
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
                or len(raw) > _MAX_BYTES):
            raise ValueError("base file changed")
        return raw
    except (OSError, ValueError) as exc:
        raise DeploymentContractError("shared Dagster base configuration is unsafe") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _desired() -> tuple[SharedDagsterInstance, bytes]:
    root = Path(os.path.abspath(get_project_root()))
    targets = load_targets_config()
    host = current_host(targets)
    base = root / _BASE
    try:
        raw = _base_bytes(root)
        document = load_yaml_rejecting_duplicate_keys(raw.decode("utf-8"))
        default = document["concurrency"]["runs"]["max_concurrent_runs"]
        spec = targets.get("hosts", {}).get(host, {})
        maximum = spec.get(_POLICY, default)
        # 옛 단일 호스트 개발 fixture는 유지한다. 운영 release는 명시 정책이 필수다.
        if os.path.lexists(root / ".ktdm-source-revision") and _POLICY not in spec:
            raise ValueError("missing host policy")
        effective = _configuration(raw, maximum)
    except (OSError, KeyError, TypeError, ValueError, UnicodeError, yaml.YAMLError) as exc:
        raise DeploymentContractError("shared Dagster host configuration cannot be read") from exc
    digest = hashlib.sha256(effective).hexdigest()
    path = base if effective == raw else TRUSTED_STATE_ROOT / "dagster-instance" / (digest + ".yaml")
    return SharedDagsterInstance(host, maximum, path, digest, effective == raw), effective


def _safe_directory(path: Path, mode: int) -> None:
    try:
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != _OWNER_UID
                or stat.S_IMODE(info.st_mode) != mode or path.resolve() != path):
            raise ValueError("unsafe directory")
    except (OSError, ValueError) as exc:
        raise DeploymentContractError("shared Dagster instance directory is unsafe") from exc


def _read_prepared(config: SharedDagsterInstance, expected: bytes) -> None:
    _safe_directory(TRUSTED_STATE_ROOT, 0o700)
    _safe_directory(config.path.parent, 0o755)
    descriptor = None
    try:
        descriptor = os.open(config.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != _OWNER_UID or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o444 or info.st_size > _MAX_BYTES):
            raise ValueError("unsafe instance file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            actual = handle.read(_MAX_BYTES + 1)
            after = os.fstat(handle.fileno())
        pathname = config.path.lstat()
        if ((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (info.st_dev, info.st_ino) != (pathname.st_dev, pathname.st_ino)
                or info.st_dev != config.path.parent.stat().st_dev or actual != expected):
            raise ValueError("instance changed")
    except (OSError, ValueError) as exc:
        raise DeploymentContractError("shared Dagster instance is not safely prepared") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def shared_dagster_instance() -> SharedDagsterInstance:
    """호스트 정책과 실제 prepared bytes를 확인한다. 파일·서비스를 변경하지 않는다."""
    config, raw = _desired()
    if not config.uses_base:
        _read_prepared(config, raw)
    return config


def prepare_shared_dagster_instance() -> SharedDagsterInstance:
    """정상 installer가 명시 호출한다. G는 상속한 경우도 기존 검증으로 재사용한다."""
    from kor_travel_docker_manager.services.c6c_deployment import manager_mutation_lock

    with manager_mutation_lock():
        config, raw = _desired()
        if config.uses_base:
            return config
        if os.geteuid() != _OWNER_UID:
            raise DeploymentContractError("shared Dagster instance preparation requires root")
        _safe_directory(TRUSTED_STATE_ROOT, 0o700)
        directory = config.path.parent
        try:
            directory.mkdir(mode=0o755)
        except FileExistsError:
            pass  # 기존 디렉터리의 mode는 고치지 않고 아래 정본 검사로 거부한다.
        except OSError as exc:
            raise DeploymentContractError("shared Dagster instance directory cannot be prepared") from exc
        else:
            # caller umask만으로 새 디렉터리가 0700이 될 수 있다. 만든 inode만 FD로 맞춘다.
            created = directory.lstat()
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                opened = os.fstat(descriptor)
                current = directory.lstat()
                if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != _OWNER_UID
                        or (opened.st_dev, opened.st_ino) != (created.st_dev, created.st_ino)
                        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
                    raise DeploymentContractError("shared Dagster instance directory is unsafe")
                os.fchmod(descriptor, 0o755)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        _safe_directory(directory, 0o755)
        if os.path.lexists(config.path):
            _read_prepared(config, raw)
            return config
        temporary = directory / (".instance-" + uuid4().hex + ".tmp")
        try:
            atomic_write_bytes(temporary, raw, mode=0o444)
            # replace가 아니다. G 밖의 예상치 못한 publisher도 같은 이름을 덮어쓰지 못한다.
            try:
                os.link(temporary, config.path, follow_symlinks=False)
            except FileExistsError:
                _read_prepared(config, raw)
        finally:
            temporary.unlink(missing_ok=True)
            fsync_directory(directory)
        _read_prepared(config, raw)
        return config


def instance_environment(values: Mapping[str, str]) -> dict[str, str]:
    """frozen render 입력에 한 정본을 넣고 독립 path/digest 재정의를 거부한다."""
    config = shared_dagster_instance()
    selected = values.get("KTDM_HOST", "").strip()
    if selected and selected != config.host:
        raise DeploymentContractError("shared Dagster instance host differs from render host")
    result = dict(values)
    expected = {CONFIG_ENV: str(config.path), DIGEST_ENV: config.sha256[:16]}
    for name, value in expected.items():
        if name in values and values[name] != value:
            raise DeploymentContractError("shared Dagster instance override differs from host policy")
    # 기본 render는 기존 ${VAR:-default} 결과를 유지한다.
    if not config.uses_base:
        result.update(expected)
    return result


def validate_instance_consumers(resolved: Mapping[str, Any]) -> None:
    """공용 instance digest를 받은 모든 consumer가 같은 파일을 readonly bind한다."""
    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("shared Dagster instance consumers are invalid")
    carriers = [service for service in services.values() if isinstance(service, Mapping)
                and isinstance(service.get("environment"), Mapping)
                and "KOR_TRAVEL_DAGSTER_SHARED_PG_URL" in service["environment"]]
    if not carriers:
        return
    config = shared_dagster_instance()
    from kor_travel_docker_manager.services.runtime_topology import _volume_source
    from kor_travel_docker_manager.services.workspace_digest import interpolate

    values = {CONFIG_ENV: str(config.path), DIGEST_ENV: config.sha256[:16]}
    for service in carriers:
        environment = service.get("environment") or {}
        if ("KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST" in environment
                and interpolate(str(environment["KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST"]), values.get)
                != config.sha256[:16]):
            raise DeploymentContractError("shared Dagster instance consumer digest differs")
        target = str(environment.get("DAGSTER_HOME")) + "/dagster.yaml"
        mounts = []
        for volume in service.get("volumes") or []:
            if isinstance(volume, Mapping):
                if volume.get("target") == target:
                    mounts.append(volume)
            elif isinstance(volume, str):
                source = _volume_source(volume)
                destination, _, access = volume[len(source) + 1:].rpartition(":")
                if destination == target:
                    mounts.append(dict(type="bind", source=interpolate(source, values.get),
                                       target=destination, read_only=access == "ro"))
        if (len(mounts) != 1 or mounts[0].get("type") != "bind" or not mounts[0].get("read_only")
                or Path(str(mounts[0].get("source"))).resolve() != config.path.resolve()):
            raise DeploymentContractError("shared Dagster instance consumer mount differs")
