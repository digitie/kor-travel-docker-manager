"""M05 격리 bridge harness의 immutable admission·runtime inspect 계약.

이 module은 canonical pinned runtime을 재사용하거나 변경하지 않는다. root launcher가
새 source snapshot·전용 Docker project를 만들기 **전** 아래 claim을 남기고, 종료 뒤에는
이 module의 strict inspect 결과만 구조화 receipt에 넣는다. Docker/compose의 원문 출력은
receipt 입력이 아니다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_release import PinnedRuntimeRelease

M05_ISOLATED_HARNESS_KIND: Final = "m05-isolated-bridge-v1"
M05_ISOLATED_HARNESS_VERSION: Final = 1
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TRANSACTION = re.compile(r"^[0-9a-f]{32}$")
_PROJECT = re.compile(r"^m05i-[a-z0-9-]{8,63}$")
_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class M05IsolatedHarnessPlan:
    """한 isolated run이 소유하는 caller-independent resource 이름과 claim."""

    release: PinnedRuntimeRelease
    manager_source_revision: str
    transaction_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.release, PinnedRuntimeRelease):
            raise DeploymentContractError("M05 isolated harness release is invalid")
        if _REVISION.fullmatch(self.manager_source_revision) is None:
            raise DeploymentContractError("M05 isolated harness Manager revision is invalid")
        if _TRANSACTION.fullmatch(self.transaction_id) is None:
            raise DeploymentContractError("M05 isolated harness transaction is invalid")

    @property
    def map_project(self) -> str:
        return f"m05i-map-{self.transaction_id}"

    @property
    def pinvi_project(self) -> str:
        return f"m05i-pinvi-{self.transaction_id}"

    @property
    def map_network(self) -> str:
        return f"{self.map_project}_default"

    @property
    def pinvi_network(self) -> str:
        return f"{self.pinvi_project}_default"

    @property
    def labels(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                "io.kortravelmap.m05.harness": M05_ISOLATED_HARNESS_KIND,
                "io.kortravelmap.m05.manager-revision": self.manager_source_revision,
                "io.kortravelmap.m05.pinset": self.release.pinset_sha256,
                "io.kortravelmap.m05.transaction": self.transaction_id,
            }
        )

    @property
    def ledger_filename(self) -> str:
        """같은 release/Manager/harness 구현은 한 번만 admission한다."""

        return hashlib.sha256(self.claim_bytes).hexdigest()

    @property
    def claim_bytes(self) -> bytes:
        payload = {
            "harness": M05_ISOLATED_HARNESS_KIND,
            "manager_source_revision": self.manager_source_revision,
            "pinset_sha256": self.release.pinset_sha256,
            "version": M05_ISOLATED_HARNESS_VERSION,
        }
        return json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii") + b"\n"


def claim_m05_isolated_harness_ledger(*, ledger_root: Path, plan: M05IsolatedHarnessPlan) -> Path:
    """새 run의 immutable root claim을 O_NOFOLLOW|O_EXCL+fsync로 남긴다.

    transaction ID나 output path를 바꿔도 release+Manager+harness 조합을 재실행할 수 없다.
    claim은 child Docker mutation 이전에 만들며 실패해도 제거하지 않는다.
    """

    if not hasattr(os, "O_NOFOLLOW"):
        raise DeploymentContractError("M05 isolated harness ledger requires O_NOFOLLOW")
    metadata = ledger_root.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise DeploymentContractError("M05 isolated harness ledger root is unsafe")
    directory_fd = os.open(
        ledger_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        opened = os.fstat(directory_fd)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise DeploymentContractError("M05 isolated harness ledger root changed")
        filename = plan.ledger_filename
        try:
            descriptor = os.open(
                filename,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_CLOEXEC
                | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError as exc:
            raise DeploymentContractError("M05 isolated harness was already claimed") from exc
        try:
            written = os.write(descriptor, plan.claim_bytes)
            if written != len(plan.claim_bytes):
                raise DeploymentContractError("M05 isolated harness ledger write is incomplete")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(directory_fd)
        return ledger_root / filename
    finally:
        os.close(directory_fd)


def assert_m05_isolated_runtime(
    *,
    plan: M05IsolatedHarnessPlan,
    containers: Mapping[str, Mapping[str, Any]],
    expected_ports: Mapping[str, tuple[int, int]],
    expected_networks: Mapping[str, str],
) -> Mapping[str, str]:
    """launch 뒤 inspect JSON이 bridge·label·loopback port를 모두 만족하는지 확인한다.

    ``expected_ports``는 ``service -> (container_port, host_port)`` 정본이다. 허용되지 않은
    published port, host network, extra network 및 label drift는 모두 completion 전에 거절한다.
    """

    if (
        not expected_ports
        or set(containers) != set(expected_ports)
        or set(containers) != set(expected_networks)
    ):
        raise DeploymentContractError("M05 isolated runtime service set is invalid")
    identities: dict[str, str] = {}
    required_networks = frozenset({plan.map_network, plan.pinvi_network})
    for service, item in containers.items():
        if not isinstance(item, Mapping):
            raise DeploymentContractError("M05 isolated runtime inspect is invalid")
        config = item.get("Config")
        host_config = item.get("HostConfig")
        network_settings = item.get("NetworkSettings")
        state = item.get("State")
        if not all(isinstance(value, Mapping) for value in (config, host_config, network_settings, state)):
            raise DeploymentContractError("M05 isolated runtime inspect is invalid")
        if state.get("Running") is not True:
            raise DeploymentContractError("M05 isolated runtime container is not running")
        if host_config.get("NetworkMode") != "bridge":
            raise DeploymentContractError("M05 isolated runtime must use bridge network")
        labels = config.get("Labels")
        if not isinstance(labels, Mapping) or any(labels.get(key) != value for key, value in plan.labels.items()):
            raise DeploymentContractError("M05 isolated runtime labels differ")
        networks = network_settings.get("Networks")
        expected_network = expected_networks[service]
        if expected_network not in required_networks:
            raise DeploymentContractError("M05 isolated runtime expected network is invalid")
        if not isinstance(networks, Mapping) or set(networks) != {expected_network}:
            raise DeploymentContractError("M05 isolated runtime network differs")
        container_port, host_port = expected_ports[service]
        port_key = f"{container_port}/tcp"
        ports = network_settings.get("Ports")
        if not isinstance(ports, Mapping) or set(ports) != {port_key}:
            raise DeploymentContractError("M05 isolated runtime published ports differ")
        bindings = ports.get(port_key)
        if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes)) or len(bindings) != 1:
            raise DeploymentContractError("M05 isolated runtime port binding is invalid")
        binding = bindings[0]
        if (
            not isinstance(binding, Mapping)
            or binding.get("HostIp") != "127.0.0.1"
            or binding.get("HostPort") != str(host_port)
        ):
            raise DeploymentContractError("M05 isolated runtime is not loopback bound")
        raw_id = item.get("Id")
        if not isinstance(raw_id, str) or _CONTAINER_ID.fullmatch(raw_id) is None:
            raise DeploymentContractError("M05 isolated runtime container ID is invalid")
        image = item.get("Image")
        if not isinstance(image, str) or not image.startswith("sha256:") or _SHA256.fullmatch(image[7:]) is None:
            raise DeploymentContractError("M05 isolated runtime image ID is invalid")
        identities[service] = image
    return MappingProxyType(identities)
