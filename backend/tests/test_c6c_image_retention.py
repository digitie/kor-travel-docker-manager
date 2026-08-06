from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.c6c_image_retention import (
    RETENTION_REPOSITORY_PREFIX,
    ensure_generation_references,
    reconcile_generation_references,
    require_empty_generation_retention_namespace,
    validate_retention_namespace_is_reserved,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeGeneration,
)


def _image(character: str) -> str:
    return f"sha256:{character * 64}"


def _generation(characters: str, revision: str) -> PinnedRuntimeGeneration:
    return PinnedRuntimeGeneration(
        map_api_image_id=_image(characters[0]),
        map_ui_image_id=_image(characters[1]),
        map_dagster_image_id=_image(characters[2]),
        map_dagster_daemon_image_id=_image(characters[3]),
        pinvi_api_image_id=_image(characters[4]),
        pinvi_web_image_id=_image(characters[5]),
        pinvi_dagster_image_id=_image(characters[6]),
        map_source_revision=revision * 40,
        pinvi_source_revision=revision * 40,
        map_application_head="0084_c6c_cancel_probe_fixtures",
        map_dagster_head="29b539ebc72a",
        pinvi_head="20260801_0050",
        pinset_sha256=revision * 64,
        recorded_at="2026-08-06T00:00:00+00:00",
    )


class FakeDocker:
    def __init__(self, *generations: PinnedRuntimeGeneration) -> None:
        self.images = {
            image_id
            for generation in generations
            for image_id in generation.image_ids.values()
        }
        self.references: dict[str, str] = {}
        self.commands: list[tuple[str, ...]] = []
        self.fail_tag_number: int | None = None
        self._tag_count = 0

    def run(self, arguments: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = tuple(arguments)
        self.commands.append(command)
        docker_args = command[1:]
        if docker_args[:3] == ("image", "inspect", "--format={{.Id}}"):
            reference = docker_args[3]
            image_id = self.references.get(reference)
            if image_id is None and reference in self.images:
                image_id = reference
            if image_id is None:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="[]\n",
                    stderr=f"Error response from daemon: No such image: {reference}\n",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{image_id}\n",
                stderr="",
            )
        if docker_args[:2] == ("image", "tag"):
            self._tag_count += 1
            source, reference = docker_args[2:]
            if self.fail_tag_number == self._tag_count:
                return subprocess.CompletedProcess(
                    command, 1, stdout="", stderr="tag failed\n"
                )
            self.references[reference] = source
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if docker_args[:2] == ("image", "ls"):
            output = "".join(f"{reference}\n" for reference in sorted(self.references))
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
        if docker_args[:2] == ("image", "rm"):
            reference = docker_args[2]
            self.references.pop(reference, None)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"Untagged: {reference}\n",
                stderr="",
            )
        raise AssertionError(command)


def test_reconcile_keeps_single_active_generation_with_all_seven_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _generation("abcdef1", "6")
    stale = _generation("2345678", "7")
    docker = FakeDocker(active, stale)
    monkeypatch.setattr(subprocess, "run", docker.run)

    first = reconcile_generation_references((active, stale), cwd="/tmp")
    repeated = reconcile_generation_references((active, stale), cwd="/tmp")
    committed = reconcile_generation_references((active,), cwd="/tmp")

    assert first.ensured == 14
    assert first.removed == 0
    assert repeated.ensured == 0
    assert committed.removed == 7
    assert len(docker.references) == 7
    assert all(reference.startswith(RETENTION_REPOSITORY_PREFIX) for reference in docker.references)
    assert set(docker.references.values()) == set(active.image_ids.values())


def test_same_generation_deduplicates_all_seven_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation = _generation("abcdef1", "6")
    docker = FakeDocker(generation)
    monkeypatch.setattr(subprocess, "run", docker.run)

    report = ensure_generation_references((generation, generation), cwd="/tmp")

    assert report.ensured == 7
    assert len(docker.references) == 7


def test_existing_content_reference_never_retargets_another_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation = _generation("abcdef1", "6")
    docker = FakeDocker(generation)
    reference = (
        f"{RETENTION_REPOSITORY_PREFIX}kor-travel-map-api:"
        f"{generation.map_api_image_id.removeprefix('sha256:')}"
    )
    docker.references[reference] = generation.pinvi_api_image_id
    monkeypatch.setattr(subprocess, "run", docker.run)

    with pytest.raises(DeploymentContractError, match="another image"):
        ensure_generation_references((generation,), cwd="/tmp")

    assert docker.references[reference] == generation.pinvi_api_image_id
    assert not any(command[1:3] == ("image", "tag") for command in docker.commands)


def test_partial_tag_failure_does_not_remove_existing_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _generation("abcdef1", "6")
    candidate = _generation("2345678", "7")
    docker = FakeDocker(active, candidate)
    monkeypatch.setattr(subprocess, "run", docker.run)
    reconcile_generation_references((candidate,), cwd="/tmp")
    original = dict(docker.references)
    docker.fail_tag_number = docker._tag_count + 3

    with pytest.raises(DeploymentContractError, match="cannot be created"):
        ensure_generation_references((active,), cwd="/tmp")

    assert original.items() <= docker.references.items()
    assert not any(command[1:3] == ("image", "rm") for command in docker.commands)

    docker.fail_tag_number = None
    retry = reconcile_generation_references((active, candidate), cwd="/tmp")

    assert retry.removed == 0
    assert len(docker.references) == 14


def test_moving_service_tag_rollover_keeps_previous_content_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _generation("abcdef1", "6")
    candidate = _generation("2345678", "7")
    docker = FakeDocker(previous, candidate)
    moving_reference = "kor-travel-map-api:latest"
    docker.references[moving_reference] = previous.map_api_image_id
    monkeypatch.setattr(subprocess, "run", docker.run)
    reconcile_generation_references((previous,), cwd="/tmp")
    retained_previous = (
        f"{RETENTION_REPOSITORY_PREFIX}kor-travel-map-api:"
        f"{previous.map_api_image_id.removeprefix('sha256:')}"
    )

    docker.references[moving_reference] = candidate.map_api_image_id
    ensure_generation_references((candidate,), cwd="/tmp")

    assert docker.references[moving_reference] == candidate.map_api_image_id
    assert docker.references[retained_previous] == previous.map_api_image_id


def test_bootstrap_rejects_unresolved_retention_residue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation = _generation("abcdef1", "6")
    docker = FakeDocker(generation)
    monkeypatch.setattr(subprocess, "run", docker.run)
    ensure_generation_references((generation,), cwd="/tmp")

    with pytest.raises(DeploymentContractError, match="unresolved"):
        require_empty_generation_retention_namespace(cwd="/tmp")


def test_compose_image_cannot_use_retention_namespace() -> None:
    validate_retention_namespace_is_reserved(
        {"services": {"api": {"image": "example/api:latest"}}}
    )

    with pytest.raises(DeploymentContractError, match="retention namespace"):
        validate_retention_namespace_is_reserved(
            {
                "services": {
                    "api": {
                        "image": f"{RETENTION_REPOSITORY_PREFIX}api:latest",
                    }
                }
            }
        )


def test_unexpected_docker_error_is_not_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation = _generation("abcdef1", "6")
    run = Mock(
        return_value=subprocess.CompletedProcess(
            ["docker"], 1, stdout="", stderr="permission denied\n"
        )
    )
    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(DeploymentContractError, match="cannot be inspected"):
        ensure_generation_references((generation,), cwd="/tmp")


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        ("{}\n", "Error response from daemon: No such image: {reference}\n"),
        ("[]\n", "Error response from daemon: No such image: {reference} extra\n"),
        ("[]\n", "permission denied\n"),
    ],
)
def test_near_miss_missing_output_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    stderr: str,
) -> None:
    generation = _generation("abcdef1", "6")
    reference = (
        f"{RETENTION_REPOSITORY_PREFIX}kor-travel-map-api:"
        f"{generation.map_api_image_id.removeprefix('sha256:')}"
    )
    run = Mock(
        return_value=subprocess.CompletedProcess(
            ["docker"], 1, stdout=stdout, stderr=stderr.format(reference=reference)
        )
    )
    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(DeploymentContractError, match="cannot be inspected"):
        ensure_generation_references((generation,), cwd="/tmp")


def test_invalid_reference_in_owned_namespace_blocks_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation = _generation("abcdef1", "6")
    docker = FakeDocker(generation)
    docker.references[f"{RETENTION_REPOSITORY_PREFIX}unknown:latest"] = (
        generation.map_api_image_id
    )
    monkeypatch.setattr(subprocess, "run", docker.run)

    with pytest.raises(DeploymentContractError, match="invalid reference"):
        reconcile_generation_references((generation,), cwd="/tmp")
