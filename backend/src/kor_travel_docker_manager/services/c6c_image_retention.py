"""C6c compatible-pair image의 Docker local reference 수명주기."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import (
    _MAP_API_SERVICE,
    _MAP_DAGSTER_DAEMON_SERVICE,
    _MAP_DAGSTER_SERVICE,
    _MAP_UI_SERVICE,
    _PINVI_API_SERVICE,
    CompatibleImagePair,
    DeploymentContractError,
)

RETENTION_REPOSITORY_PREFIX = "kor-travel-docker-manager/c6c-retention/"
_IMAGE_ID = re.compile(r"^sha256:([0-9a-f]{64})$")
_SERVICES = (
    _MAP_API_SERVICE,
    _MAP_UI_SERVICE,
    _MAP_DAGSTER_SERVICE,
    _MAP_DAGSTER_DAEMON_SERVICE,
    _PINVI_API_SERVICE,
)
_REFERENCE = re.compile(
    rf"^{re.escape(RETENTION_REPOSITORY_PREFIX)}"
    rf"({'|'.join(re.escape(service) for service in _SERVICES)}):([0-9a-f]{{64}})$"
)


@dataclass(frozen=True)
class RetentionReport:
    """한 번의 ensure/reconcile에서 변경된 manager-owned reference 수."""

    ensured: int
    removed: int


def _pair_images(pair: CompatibleImagePair) -> tuple[tuple[str, str], ...]:
    return (
        (_MAP_API_SERVICE, pair.map_image_id),
        (_MAP_UI_SERVICE, pair.map_ui_image_id),
        (_MAP_DAGSTER_SERVICE, pair.map_dagster_image_id),
        (_MAP_DAGSTER_DAEMON_SERVICE, pair.map_dagster_daemon_image_id),
        (_PINVI_API_SERVICE, pair.pinvi_image_id),
    )


def _reference(service: str, image_id: str) -> str:
    match = _IMAGE_ID.fullmatch(image_id)
    if service not in _SERVICES or match is None:
        raise DeploymentContractError("compatible pair retention identity is invalid")
    return f"{RETENTION_REPOSITORY_PREFIX}{service}:{match.group(1)}"


def _desired_references(
    pairs: Sequence[CompatibleImagePair],
) -> dict[str, str]:
    desired: dict[str, str] = {}
    for pair in pairs:
        for service, image_id in _pair_images(pair):
            reference = _reference(service, image_id)
            previous = desired.setdefault(reference, image_id)
            if previous != image_id:
                raise DeploymentContractError(
                    "compatible pair retention reference collision"
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
            "compatible pair retention Docker command failed"
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
                "compatible pair retention reference inspection is invalid"
            )
        return image_id
    missing = f"Error response from daemon: No such image: {reference}"
    if (
        completed.returncode == 1
        and completed.stdout.strip() in {"", "[]"}
        and completed.stderr.strip() == missing
    ):
        return None
    raise DeploymentContractError(
        "compatible pair retention reference cannot be inspected"
    )


def ensure_pair_references(
    pairs: Sequence[CompatibleImagePair],
    *,
    cwd: str,
) -> RetentionReport:
    """pair reference를 additive 생성하고 content collision을 거부한다."""

    desired = _desired_references(pairs)
    created = 0
    for reference, image_id in sorted(desired.items()):
        observed = _inspect_reference(reference, cwd=cwd)
        if observed is not None:
            if observed != image_id:
                raise DeploymentContractError(
                    "compatible pair retention reference points to another image"
                )
            continue
        source = _inspect_reference(image_id, cwd=cwd)
        if source != image_id:
            raise DeploymentContractError(
                "compatible pair retention source image is unavailable"
            )
        tagged = _run_docker(["image", "tag", image_id, reference], cwd=cwd)
        if tagged.returncode != 0 or tagged.stdout or tagged.stderr:
            raise DeploymentContractError(
                "compatible pair retention reference cannot be created"
            )
        if _inspect_reference(reference, cwd=cwd) != image_id:
            raise DeploymentContractError(
                "compatible pair retention reference verification failed"
            )
        created += 1
    return RetentionReport(ensured=created, removed=0)


def _owned_references(*, cwd: str) -> set[str]:
    completed = _run_docker(
        ["image", "ls", "--no-trunc", "--format={{.Repository}}:{{.Tag}}"],
        cwd=cwd,
    )
    if completed.returncode != 0 or completed.stderr:
        raise DeploymentContractError(
            "compatible pair retention references cannot be listed"
        )
    owned: set[str] = set()
    for line in completed.stdout.splitlines():
        reference = line.strip()
        if not reference.startswith(RETENTION_REPOSITORY_PREFIX):
            continue
        if _REFERENCE.fullmatch(reference) is None:
            raise DeploymentContractError(
                "compatible pair retention namespace contains an invalid reference"
            )
        owned.add(reference)
    return owned


def reconcile_pair_references(
    pairs: Sequence[CompatibleImagePair],
    *,
    cwd: str,
) -> RetentionReport:
    """desired reference를 먼저 보존한 뒤 owned stale tag만 제거한다."""

    ensured = ensure_pair_references(pairs, cwd=cwd).ensured
    desired = set(_desired_references(pairs))
    stale = sorted(_owned_references(cwd=cwd) - desired)
    for reference in stale:
        removed = _run_docker(["image", "rm", reference], cwd=cwd)
        if removed.returncode != 0 or removed.stderr:
            raise DeploymentContractError(
                "compatible pair stale retention reference cannot be removed"
            )
    if _owned_references(cwd=cwd) != desired:
        raise DeploymentContractError(
            "compatible pair retention reference reconciliation failed"
        )
    for reference, image_id in _desired_references(pairs).items():
        if _inspect_reference(reference, cwd=cwd) != image_id:
            raise DeploymentContractError(
                "compatible pair retained image changed during reconciliation"
            )
    return RetentionReport(ensured=ensured, removed=len(stale))


def require_empty_retention_namespace(*, cwd: str) -> None:
    """manifest 없는 bootstrap은 불확정 retention residue를 덮지 않는다."""

    if _owned_references(cwd=cwd):
        raise DeploymentContractError(
            "compatible pair bootstrap has unresolved retention references"
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
        if isinstance(image, str) and image.startswith(RETENTION_REPOSITORY_PREFIX):
            raise DeploymentContractError(
                "Compose image cannot use the compatible pair retention namespace"
            )
