"""Pinned runtime generation의 Docker image retention reference 수명주기."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    RUNTIME_SERVICES,
    PinnedRuntimeGeneration,
    RuntimeService,
)

RETENTION_REPOSITORY_PREFIX = "kor-travel-docker-manager/pinned-runtime-v5/"
CANDIDATE_REPOSITORY_PREFIX = (
    "kor-travel-docker-manager/pinned-runtime-candidate-v6/"
)
_CANDIDATE_TAG_SERVICES: tuple[RuntimeService, ...] = (
    "kor-travel-map-api",
    "kor-travel-map-ui",
    "kor-travel-map-dagster",
    "pinvi-api",
    "pinvi-web",
    "pinvi-dagster",
)
_IMAGE_ID = re.compile(r"^sha256:([0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DELETED_IMAGE = re.compile(r"^Deleted: sha256:[0-9a-f]{64}$")
_REFERENCE = re.compile(
    rf"^{re.escape(RETENTION_REPOSITORY_PREFIX)}"
    rf"({'|'.join(re.escape(service) for service in RUNTIME_SERVICES)}):([0-9a-f]{{64}})$"
)
_CANDIDATE_REFERENCE = re.compile(
    rf"^{re.escape(CANDIDATE_REPOSITORY_PREFIX)}"
    rf"({'|'.join(re.escape(service) for service in _CANDIDATE_TAG_SERVICES)})"
    rf":([0-9a-f]{{64}})$"
)


@dataclass(frozen=True)
class RetentionReport:
    """한 번의 ensure/reconcile에서 변경된 manager-owned reference 수."""

    ensured: int
    removed: int


def _generation_images(
    generation: PinnedRuntimeGeneration,
) -> tuple[tuple[RuntimeService, str], ...]:
    return tuple(generation.image_ids.items())


def _reference(service: RuntimeService, image_id: str) -> str:
    match = _IMAGE_ID.fullmatch(image_id)
    if service not in RUNTIME_SERVICES or match is None:
        raise DeploymentContractError("pinned runtime retention identity is invalid")
    return f"{RETENTION_REPOSITORY_PREFIX}{service}:{match.group(1)}"


def _desired_references(
    generations: Sequence[PinnedRuntimeGeneration],
) -> dict[str, str]:
    desired: dict[str, str] = {}
    for generation in generations:
        for service, image_id in _generation_images(generation):
            reference = _reference(service, image_id)
            previous = desired.setdefault(reference, image_id)
            if previous != image_id:
                raise DeploymentContractError(
                    "pinned runtime retention reference collision"
                )
    return desired


def _run_docker(arguments: Sequence[str], *, cwd: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["docker", *arguments],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentContractError(
            "pinned runtime retention Docker command failed"
        ) from exc


def _inspect_reference(reference: str, *, cwd: str) -> str | None:
    completed = _run_docker(
        ["image", "inspect", "--format={{.Id}}", reference],
        cwd=cwd,
    )
    if completed.returncode == 0:
        image_id = completed.stdout.strip()
        if completed.stderr or _IMAGE_ID.fullmatch(image_id) is None:
            raise DeploymentContractError(
                "pinned runtime retention reference inspection is invalid"
            )
        return image_id
    missing = f"Error response from daemon: No such image: {reference}"
    if (
        completed.returncode == 1
        and completed.stdout.strip() in {"", "[]"}
        and completed.stderr.strip() == missing
    ):
        return None
    raise DeploymentContractError("pinned runtime retention reference cannot be inspected")


def ensure_generation_references(
    generations: Sequence[PinnedRuntimeGeneration],
    *,
    cwd: str,
) -> RetentionReport:
    """generation reference를 additive 생성하고 content collision을 거부한다."""

    desired = _desired_references(generations)
    created = 0
    for reference, image_id in sorted(desired.items()):
        observed = _inspect_reference(reference, cwd=cwd)
        if observed is not None:
            if observed != image_id:
                raise DeploymentContractError(
                    "pinned runtime retention reference points to another image"
                )
            continue
        source = _inspect_reference(image_id, cwd=cwd)
        if source != image_id:
            raise DeploymentContractError(
                "pinned runtime retention source image is unavailable"
            )
        tagged = _run_docker(["image", "tag", image_id, reference], cwd=cwd)
        if tagged.returncode != 0 or tagged.stdout or tagged.stderr:
            raise DeploymentContractError(
                "pinned runtime retention reference cannot be created"
            )
        if _inspect_reference(reference, cwd=cwd) != image_id:
            raise DeploymentContractError(
                "pinned runtime retention reference verification failed"
            )
        created += 1
    return RetentionReport(ensured=created, removed=0)


def _owned_references(*, cwd: str) -> set[str]:
    completed = _run_docker(
        ["image", "ls", "--no-trunc", "--format={{.Repository}}:{{.Tag}}"],
        cwd=cwd,
    )
    if completed.returncode != 0 or completed.stderr:
        raise DeploymentContractError("pinned runtime retention references cannot be listed")
    owned: set[str] = set()
    for line in completed.stdout.splitlines():
        reference = line.strip()
        if not reference.startswith(RETENTION_REPOSITORY_PREFIX):
            continue
        if _REFERENCE.fullmatch(reference) is None:
            raise DeploymentContractError(
                "pinned runtime retention namespace contains an invalid reference"
            )
        owned.add(reference)
    return owned


def _owned_candidate_references(*, cwd: str) -> dict[str, str]:
    completed = _run_docker(
        [
            "image",
            "ls",
            "--no-trunc",
            "--format={{.Repository}}:{{.Tag}}\t{{.ID}}",
        ],
        cwd=cwd,
    )
    if (
        completed.returncode != 0
        or not isinstance(completed.stdout, str)
        or not isinstance(completed.stderr, str)
        or completed.stderr
    ):
        raise DeploymentContractError(
            "pinned runtime candidate references cannot be listed"
        )
    owned: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if not line or line.strip() != line:
            raise DeploymentContractError(
                "pinned runtime candidate reference listing is invalid"
            )
        fields = line.split("\t")
        if len(fields) != 2:
            raise DeploymentContractError(
                "pinned runtime candidate reference listing is invalid"
            )
        reference, image_id = fields
        if not reference.startswith(CANDIDATE_REPOSITORY_PREFIX):
            continue
        if (
            _CANDIDATE_REFERENCE.fullmatch(reference) is None
            or _IMAGE_ID.fullmatch(image_id) is None
        ):
            raise DeploymentContractError(
                "pinned runtime candidate namespace contains an invalid reference"
            )
        if reference in owned:
            raise DeploymentContractError(
                "pinned runtime candidate namespace contains an ambiguous reference"
            )
        owned[reference] = image_id
    return owned


def _desired_candidate_references(
    references: Mapping[RuntimeService, str],
    generation: PinnedRuntimeGeneration,
) -> dict[str, str]:
    if set(references) != set(_CANDIDATE_TAG_SERVICES):
        raise DeploymentContractError(
            "pinned runtime active candidate references are incomplete"
        )
    if _SHA256.fullmatch(generation.pinset_sha256) is None:
        raise DeploymentContractError("pinned runtime candidate pinset is invalid")
    desired: dict[str, str] = {}
    for service in _CANDIDATE_TAG_SERVICES:
        reference = references.get(service)
        if not isinstance(reference, str):
            raise DeploymentContractError(
                "pinned runtime active candidate reference is invalid"
            )
        match = _CANDIDATE_REFERENCE.fullmatch(reference)
        if (
            match is None
            or match.group(1) != service
            or match.group(2) != generation.pinset_sha256
        ):
            raise DeploymentContractError(
                "pinned runtime active candidate reference is invalid"
            )
        image_id = generation.image_ids[service]
        if _IMAGE_ID.fullmatch(image_id) is None or reference in desired:
            raise DeploymentContractError(
                "pinned runtime active candidate identity is invalid"
            )
        desired[reference] = image_id
    if len(desired) != len(_CANDIDATE_TAG_SERVICES):
        raise DeploymentContractError(
            "pinned runtime active candidate references are not unique"
        )
    return desired


def _remove_candidate_reference(reference: str, *, cwd: str) -> None:
    completed = _run_docker(["image", "rm", reference], cwd=cwd)
    if (
        completed.returncode != 0
        or not isinstance(completed.stdout, str)
        or not isinstance(completed.stderr, str)
        or completed.stderr
    ):
        raise DeploymentContractError(
            "pinned runtime stale candidate reference cannot be removed"
        )
    lines = completed.stdout.splitlines()
    if lines and (
        lines[0] != f"Untagged: {reference}"
        or any(_DELETED_IMAGE.fullmatch(line) is None for line in lines[1:])
        or len(lines[1:]) != len(set(lines[1:]))
    ):
        raise DeploymentContractError(
            "pinned runtime stale candidate removal response is invalid"
        )
    if _inspect_reference(reference, cwd=cwd) is not None:
        raise DeploymentContractError(
            "pinned runtime stale candidate reference remains after removal"
        )


def reconcile_candidate_build_references(
    references: Mapping[RuntimeService, str],
    generation: PinnedRuntimeGeneration,
    *,
    cwd: str,
) -> RetentionReport:
    """content-address 보존 뒤 active pinset의 candidate tag 여섯 개만 남긴다.

    호출자는 pinned rebuild 전역 lock을 보유해야 한다. 이 함수는 active image의 v5
    content-address reference가 이미 확보됐음을 먼저 검증하므로 candidate tag 정리가
    현재 runtime image의 유일한 도달 경로를 없애지 않는다.
    """

    desired = _desired_candidate_references(references, generation)
    for service in _CANDIDATE_TAG_SERVICES:
        image_id = generation.image_ids[service]
        if _inspect_reference(_reference(service, image_id), cwd=cwd) != image_id:
            raise DeploymentContractError(
                "pinned runtime candidate cleanup requires retained content references"
            )

    owned = _owned_candidate_references(cwd=cwd)
    for reference, image_id in desired.items():
        if owned.get(reference) != image_id:
            raise DeploymentContractError(
                "pinned runtime active candidate reference changed"
            )
        if _inspect_reference(reference, cwd=cwd) != image_id:
            raise DeploymentContractError(
                "pinned runtime active candidate reference cannot be verified"
            )

    stale = sorted(set(owned) - set(desired))
    for reference in stale:
        if _inspect_reference(reference, cwd=cwd) != owned[reference]:
            raise DeploymentContractError(
                "pinned runtime stale candidate reference changed"
            )
        _remove_candidate_reference(reference, cwd=cwd)

    if _owned_candidate_references(cwd=cwd) != desired:
        raise DeploymentContractError(
            "pinned runtime candidate reference reconciliation failed"
        )
    for reference, image_id in desired.items():
        if _inspect_reference(reference, cwd=cwd) != image_id:
            raise DeploymentContractError(
                "pinned runtime active candidate reference changed during cleanup"
            )
    return RetentionReport(ensured=0, removed=len(stale))


def reconcile_generation_references(
    generations: Sequence[PinnedRuntimeGeneration],
    *,
    cwd: str,
) -> RetentionReport:
    """desired reference를 먼저 보존한 뒤 owned stale tag만 제거한다."""

    ensured = ensure_generation_references(generations, cwd=cwd).ensured
    desired = set(_desired_references(generations))
    stale = sorted(_owned_references(cwd=cwd) - desired)
    for reference in stale:
        removed = _run_docker(["image", "rm", reference], cwd=cwd)
        if removed.returncode != 0 or removed.stderr:
            raise DeploymentContractError(
                "pinned runtime stale retention reference cannot be removed"
            )
    if _owned_references(cwd=cwd) != desired:
        raise DeploymentContractError(
            "pinned runtime retention reference reconciliation failed"
        )
    for reference, image_id in _desired_references(generations).items():
        if _inspect_reference(reference, cwd=cwd) != image_id:
            raise DeploymentContractError(
                "pinned runtime retained image changed during reconciliation"
            )
    return RetentionReport(ensured=ensured, removed=len(stale))


def require_empty_generation_retention_namespace(*, cwd: str) -> None:
    """manifest 없는 bootstrap은 불확정 v5 retention residue를 덮지 않는다."""

    if _owned_references(cwd=cwd):
        raise DeploymentContractError(
            "pinned runtime bootstrap has unresolved retention references"
        )


def validate_retention_namespace_is_reserved(
    resolved: Mapping[str, Any],
) -> None:
    """Compose service image가 manager-owned reference와 충돌하지 않게 한다."""

    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("resolved Compose services are invalid")
    for service in services.values():
        if not isinstance(service, Mapping):
            raise DeploymentContractError("resolved Compose service is invalid")
        image = service.get("image")
        if isinstance(image, str) and image.startswith(
            (RETENTION_REPOSITORY_PREFIX, CANDIDATE_REPOSITORY_PREFIX)
        ):
            raise DeploymentContractError(
                "Compose image cannot use the pinned runtime retention namespace"
            )
