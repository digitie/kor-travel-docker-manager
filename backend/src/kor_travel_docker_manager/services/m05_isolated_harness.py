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
from typing import Any, Final, Literal

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_release import PinnedRuntimeRelease

M05IsolatedRuntimeRole = Literal["map", "pinvi"]
M05_ISOLATED_HARNESS_KIND: Final = "m05-isolated-bridge-v1"
M05_ISOLATED_HARNESS_VERSION: Final = 1
M05_ISOLATED_MANAGER_ADMISSION_KIND: Final = "pinvi-m05-isolated-manager-admission-v1"
_EXPOSED_RUNTIME_SERVICE_ROLES: Final[Mapping[str, M05IsolatedRuntimeRole]] = MappingProxyType(
    {"map-api": "map", "pinvi-api": "pinvi"}
)
_RUNTIME_IMAGE_ROLES: Final[Mapping[str, M05IsolatedRuntimeRole]] = MappingProxyType(
    {
        "map-admin": "map",
        "map-api": "map",
        "map-frontend": "map",
        "pinvi-api": "pinvi",
        "pinvi-dagster": "pinvi",
        "pinvi-web": "pinvi",
    }
)
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TRANSACTION = re.compile(r"^[0-9a-f]{32}$")
_NETWORK = re.compile(r"^m05i-(?:map|pinvi)-[0-9a-f]{32}_default$")
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


@dataclass(frozen=True)
class M05IsolatedNetworkExpectation:
    """claim 뒤 Docker가 만든 dedicated bridge network의 immutable inspect evidence."""

    role: M05IsolatedRuntimeRole
    name: str
    network_id: str

    def __post_init__(self) -> None:
        if self.role not in {"map", "pinvi"} or _NETWORK.fullmatch(self.name) is None:
            raise DeploymentContractError("M05 isolated network expectation is invalid")
        if _CONTAINER_ID.fullmatch(self.network_id) is None:
            raise DeploymentContractError("M05 isolated network ID is invalid")


@dataclass(frozen=True)
class M05IsolatedServiceExpectation:
    """receipt에 넣을 service 하나의 port·source·image immutable identity."""

    role: M05IsolatedRuntimeRole
    container_port: int
    host_port: int
    image_id: str

    def __post_init__(self) -> None:
        if self.role not in {"map", "pinvi"}:
            raise DeploymentContractError("M05 isolated service role is invalid")
        if not 1 <= self.container_port <= 65535 or not 1 <= self.host_port <= 65535:
            raise DeploymentContractError("M05 isolated service port is invalid")
        if not isinstance(self.image_id, str) or not self.image_id.startswith("sha256:"):
            raise DeploymentContractError("M05 isolated service image ID is invalid")
        if _SHA256.fullmatch(self.image_id[7:]) is None:
            raise DeploymentContractError("M05 isolated service image ID is invalid")


@dataclass(frozen=True)
class M05IsolatedPairEvidence:
    """Map/PinVi exact source와 vendored full/admin OpenAPI digest의 receipt input."""

    map_full_openapi_sha256: str
    map_source_revision: str
    pinvi_full_openapi_sha256: str
    pinvi_source_revision: str

    def __post_init__(self) -> None:
        for value, label, pattern in (
            (self.map_source_revision, "Map source revision", _REVISION),
            (self.pinvi_source_revision, "PinVi source revision", _REVISION),
            (self.map_full_openapi_sha256, "Map full OpenAPI hash", _SHA256),
            (self.pinvi_full_openapi_sha256, "PinVi full OpenAPI hash", _SHA256),
        ):
            if not isinstance(value, str) or pattern.fullmatch(value) is None:
                raise DeploymentContractError(f"M05 isolated {label} is invalid")
        if self.map_full_openapi_sha256 != self.pinvi_full_openapi_sha256:
            raise DeploymentContractError("M05 isolated full OpenAPI hashes differ")


@dataclass(frozen=True)
class M05IsolatedRuntimeExpectation:
    """root driver가 network creation·image build 뒤 freeze하는 inspect allowlist."""

    plan: M05IsolatedHarnessPlan
    networks: tuple[M05IsolatedNetworkExpectation, ...]
    pair: M05IsolatedPairEvidence
    services: Mapping[str, M05IsolatedServiceExpectation]

    def __post_init__(self) -> None:
        networks = tuple(self.networks)
        services = MappingProxyType(dict(self.services))
        object.__setattr__(self, "networks", networks)
        object.__setattr__(self, "services", services)
        if len(networks) != 2 or {item.role for item in networks} != {"map", "pinvi"}:
            raise DeploymentContractError("M05 isolated network roles are incomplete")
        expected_names = {"map": self.plan.map_network, "pinvi": self.plan.pinvi_network}
        if any(item.name != expected_names[item.role] for item in networks):
            raise DeploymentContractError("M05 isolated network name differs from the plan")
        if len({item.network_id for item in networks}) != len(networks):
            raise DeploymentContractError("M05 isolated network IDs must differ")
        if set(services) != set(_EXPOSED_RUNTIME_SERVICE_ROLES) or any(
            service.role != _EXPOSED_RUNTIME_SERVICE_ROLES[name]
            for name, service in services.items()
        ):
            raise DeploymentContractError("M05 isolated service set is invalid")
        release_revisions = {
            "map": self.plan.release.source_for("map").revision,
            "pinvi": self.plan.release.source_for("pinvi").revision,
        }
        pair_revisions = {
            "map": self.pair.map_source_revision,
            "pinvi": self.pair.pinvi_source_revision,
        }
        if release_revisions != pair_revisions:
            raise DeploymentContractError("M05 isolated pair source differs from the release")

    def network_for(self, role: M05IsolatedRuntimeRole) -> M05IsolatedNetworkExpectation:
        return next(item for item in self.networks if item.role == role)


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


def build_m05_isolated_manager_admission(
    *, plan: M05IsolatedHarnessPlan, pair: M05IsolatedPairEvidence
) -> Mapping[str, object]:
    """PinVi direct Compose를 열 수 있는 Manager-only one-shot admission을 만든다.

    이 문서는 root driver가 private ``0700`` runtime directory에 ``0600``으로만 쓴다.
    PinVi는 caller environment marker가 아니라 이 exact transaction·pinset·source pair를
    no-follow로 읽어 isolated mutation을 허용한다.
    """

    if (
        pair.map_source_revision != plan.release.source_for("map").revision
        or pair.pinvi_source_revision != plan.release.source_for("pinvi").revision
    ):
        raise DeploymentContractError("M05 isolated admission pair differs from the release")
    return MappingProxyType(
        {
            "kind": M05_ISOLATED_MANAGER_ADMISSION_KIND,
            "manager_source_revision": plan.manager_source_revision,
            "map_source_revision": pair.map_source_revision,
            "pinset_sha256": plan.release.pinset_sha256,
            "pinvi_source_revision": pair.pinvi_source_revision,
            "transaction_id": plan.transaction_id,
            "version": 1,
        }
    )


def assert_m05_isolated_runtime(
    *,
    expectation: M05IsolatedRuntimeExpectation,
    containers: Mapping[str, Mapping[str, Any]],
    image_inspects: Mapping[str, Mapping[str, Any]],
    network_inspects: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, str]:
    """launch 뒤 inspect JSON이 bridge·label·loopback port를 모두 만족하는지 확인한다.

    driver가 image build·network creation 직후 만든 typed expectation만 받는다. 허용되지 않은
    published port, host network, extra network, source/image/label drift는 completion 전에 거절한다.
    """

    plan = expectation.plan
    expected_images = {service.image_id for service in expectation.services.values()}
    if (
        set(containers) != set(expectation.services)
        or set(image_inspects) != expected_images
        or set(network_inspects) != {
        item.name for item in expectation.networks
        }
    ):
        raise DeploymentContractError("M05 isolated runtime service set is invalid")
    identities: dict[str, str] = {}
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
        service_expectation = expectation.services[service]
        network_expectation = expectation.network_for(service_expectation.role)
        if host_config.get("NetworkMode") != network_expectation.name:
            raise DeploymentContractError("M05 isolated runtime must use bridge network")
        labels = config.get("Labels")
        if not isinstance(labels, Mapping) or any(labels.get(key) != value for key, value in plan.labels.items()):
            raise DeploymentContractError("M05 isolated runtime labels differ")
        attached_networks = network_settings.get("Networks")
        if (
            not isinstance(attached_networks, Mapping)
            or set(attached_networks) != {network_expectation.name}
        ):
            raise DeploymentContractError("M05 isolated runtime network differs")
        attached_network = attached_networks[network_expectation.name]
        if (
            not isinstance(attached_network, Mapping)
            or attached_network.get("NetworkID") != network_expectation.network_id
        ):
            raise DeploymentContractError("M05 isolated runtime network ID differs")
        port_key = f"{service_expectation.container_port}/tcp"
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
            or binding.get("HostPort") != str(service_expectation.host_port)
        ):
            raise DeploymentContractError("M05 isolated runtime is not loopback bound")
        raw_id = item.get("Id")
        if not isinstance(raw_id, str) or _CONTAINER_ID.fullmatch(raw_id) is None:
            raise DeploymentContractError("M05 isolated runtime container ID is invalid")
        image = item.get("Image")
        if image != service_expectation.image_id:
            raise DeploymentContractError("M05 isolated runtime image ID differs")
        image_labels = config.get("Labels")
        image_inspect = image_inspects[service_expectation.image_id]
        image_config = image_inspect.get("Config") if isinstance(image_inspect, Mapping) else None
        image_inspect_id = image_inspect.get("Id") if isinstance(image_inspect, Mapping) else None
        image_inspect_labels = image_config.get("Labels") if isinstance(image_config, Mapping) else None
        source_revision = (
            expectation.pair.map_source_revision
            if service_expectation.role == "map"
            else expectation.pair.pinvi_source_revision
        )
        if (
            image_inspect_id != service_expectation.image_id
            or not isinstance(image_labels, Mapping)
            or not isinstance(image_inspect_labels, Mapping)
            or image_labels.get("org.opencontainers.image.revision") != source_revision
            or image_inspect_labels.get("org.opencontainers.image.revision") != source_revision
        ):
            raise DeploymentContractError("M05 isolated runtime source revision differs")
        identities[service] = image
    for network_expectation in expectation.networks:
        network = network_inspects[network_expectation.name]
        if not isinstance(network, Mapping):
            raise DeploymentContractError("M05 isolated Docker network inspect is invalid")
        labels = network.get("Labels")
        if (
            network.get("Id") != network_expectation.network_id
            or network.get("Name") != network_expectation.name
            or network.get("Driver") != "bridge"
            or network.get("Internal") is not False
            or not isinstance(labels, Mapping)
            or any(labels.get(key) != value for key, value in plan.labels.items())
        ):
            raise DeploymentContractError("M05 isolated Docker network differs")
    return MappingProxyType(identities)


def build_m05_isolated_runtime_provenance(
    *,
    expectation: M05IsolatedRuntimeExpectation,
    image_inspects: Mapping[str, Mapping[str, Any]],
) -> dict[str, object]:
    """PinVi M05 attestation이 소비할 root-only isolated image/source receipt를 만든다.

    caller는 raw Docker output을 그대로 저장하지 않고 이 function이 반환한 fixed schema만 `0600`
    receipt로 내보낸다. Map/PinVi의 모든 M05 runtime image는 image inspect ID와 OCI source label을
    exact source pin으로 대조한다. `assert_m05_isolated_runtime`은 이 중 public loopback endpoint
    두 개의 container/network topology도 별도로 검증한다.
    """

    if set(image_inspects) != set(_RUNTIME_IMAGE_ROLES):
        raise DeploymentContractError("M05 isolated runtime image set is invalid")
    image_ids: dict[str, str] = {}
    for name, role in _RUNTIME_IMAGE_ROLES.items():
        image = image_inspects[name]
        if not isinstance(image, Mapping):
            raise DeploymentContractError("M05 isolated runtime image inspect is invalid")
        image_id = image.get("Id")
        config = image.get("Config")
        labels = config.get("Labels") if isinstance(config, Mapping) else None
        source_revision = (
            expectation.pair.map_source_revision
            if role == "map"
            else expectation.pair.pinvi_source_revision
        )
        if (
            not isinstance(image_id, str)
            or _DIGEST_RE.fullmatch(image_id) is None
            or not isinstance(labels, Mapping)
            or labels.get("org.opencontainers.image.revision") != source_revision
        ):
            raise DeploymentContractError("M05 isolated runtime image provenance differs")
        image_ids[name] = image_id
    if (
        image_ids["map-api"] != expectation.services["map-api"].image_id
        or image_ids["pinvi-api"] != expectation.services["pinvi-api"].image_id
    ):
        raise DeploymentContractError("M05 isolated runtime API image differs from topology")
    return {
        "kind": "m05-isolated-runtime-provenance-v1",
        "manager_source_revision": expectation.plan.manager_source_revision,
        "map": {
            "admin_image_id": image_ids["map-admin"],
            "api_image_id": image_ids["map-api"],
            "frontend_image_id": image_ids["map-frontend"],
            "full_openapi_sha256": expectation.pair.map_full_openapi_sha256,
            "source_revision": expectation.pair.map_source_revision,
        },
        "pinset_sha256": expectation.plan.release.pinset_sha256,
        "pinvi": {
            "api_image_id": image_ids["pinvi-api"],
            "dagster_image_id": image_ids["pinvi-dagster"],
            "source_revision": expectation.pair.pinvi_source_revision,
            "web_image_id": image_ids["pinvi-web"],
        },
        "transaction_id": expectation.plan.transaction_id,
        "version": 1,
    }
