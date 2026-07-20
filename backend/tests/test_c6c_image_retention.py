from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any
from unittest.mock import Mock

import pytest
from kor_travel_docker_manager.services.c6c_deployment import (
    CompatibleImagePair,
    DeploymentContractError,
    new_image_pair,
)
from kor_travel_docker_manager.services.c6c_image_retention import (
    RETENTION_REPOSITORY_PREFIX,
    ensure_pair_references,
    reconcile_pair_references,
    require_empty_retention_namespace,
    validate_retention_namespace_is_reserved,
)


def _image(character: str) -> str:
    return f"sha256:{character * 64}"


def _pair(characters: str, revision: str) -> CompatibleImagePair:
    return new_image_pair(
        _image(characters[0]),
        _image(characters[4]),
        "c6c-ops-v1",
        map_ui_image_id=_image(characters[1]),
        map_dagster_image_id=_image(characters[2]),
        map_dagster_daemon_image_id=_image(characters[3]),
        map_source_revision=revision * 40,
        pinvi_source_revision=revision * 40,
    )


class FakeDocker:
    def __init__(self, *pairs: CompatibleImagePair) -> None:
        self.images = {
            image_id
            for pair in pairs
            for image_id in (
                pair.map_image_id,
                pair.map_ui_image_id,
                pair.map_dagster_image_id,
                pair.map_dagster_daemon_image_id,
                pair.pinvi_image_id,
            )
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


def test_reconcile_preserves_two_slots_then_keeps_only_new_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _pair("abcde", "6")
    rollback = _pair("f1234", "7")
    docker = FakeDocker(active, rollback)
    monkeypatch.setattr(subprocess, "run", docker.run)

    first = reconcile_pair_references((active, rollback), cwd="/tmp")
    repeated = reconcile_pair_references((active, rollback), cwd="/tmp")
    committed = reconcile_pair_references((active,), cwd="/tmp")

    assert first.ensured == 10
    assert first.removed == 0
    assert repeated.ensured == 0
    assert committed.removed == 5
    assert len(docker.references) == 5
    assert all(reference.startswith(RETENTION_REPOSITORY_PREFIX) for reference in docker.references)
    assert set(docker.references.values()) == {
        active.map_image_id,
        active.map_ui_image_id,
        active.map_dagster_image_id,
        active.map_dagster_daemon_image_id,
        active.pinvi_image_id,
    }
    assert len(docker.images) == 10


def test_active_equals_rollback_deduplicates_five_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = _pair("abcde", "6")
    docker = FakeDocker(pair)
    monkeypatch.setattr(subprocess, "run", docker.run)

    report = ensure_pair_references((pair, pair), cwd="/tmp")

    assert report.ensured == 5
    assert len(docker.references) == 5


def test_existing_content_reference_never_retargets_another_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = _pair("abcde", "6")
    docker = FakeDocker(pair)
    reference = (
        f"{RETENTION_REPOSITORY_PREFIX}kor-travel-map-api:"
        f"{pair.map_image_id.removeprefix('sha256:')}"
    )
    docker.references[reference] = pair.pinvi_image_id
    monkeypatch.setattr(subprocess, "run", docker.run)

    with pytest.raises(DeploymentContractError, match="another image"):
        ensure_pair_references((pair,), cwd="/tmp")

    assert docker.references[reference] == pair.pinvi_image_id
    assert not any(command[1:3] == ("image", "tag") for command in docker.commands)


def test_partial_tag_failure_does_not_remove_existing_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _pair("abcde", "6")
    rollback = _pair("f1234", "7")
    docker = FakeDocker(active, rollback)
    monkeypatch.setattr(subprocess, "run", docker.run)
    reconcile_pair_references((rollback,), cwd="/tmp")
    original = dict(docker.references)
    docker.fail_tag_number = docker._tag_count + 3

    with pytest.raises(DeploymentContractError, match="cannot be created"):
        ensure_pair_references((active,), cwd="/tmp")

    assert original.items() <= docker.references.items()
    assert not any(command[1:3] == ("image", "rm") for command in docker.commands)

    docker.fail_tag_number = None
    retry = reconcile_pair_references((active, rollback), cwd="/tmp")

    assert retry.removed == 0
    assert len(docker.references) == 10


def test_moving_service_tag_rollover_keeps_previous_content_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _pair("abcde", "6")
    candidate = _pair("f1234", "7")
    docker = FakeDocker(previous, candidate)
    moving_reference = "kor-travel-map-api:latest"
    docker.references[moving_reference] = previous.map_image_id
    monkeypatch.setattr(subprocess, "run", docker.run)
    reconcile_pair_references((previous,), cwd="/tmp")
    retained_previous = (
        f"{RETENTION_REPOSITORY_PREFIX}kor-travel-map-api:"
        f"{previous.map_image_id.removeprefix('sha256:')}"
    )

    docker.references[moving_reference] = candidate.map_image_id
    ensure_pair_references((candidate,), cwd="/tmp")

    assert docker.references[moving_reference] == candidate.map_image_id
    assert docker.references[retained_previous] == previous.map_image_id


def test_bootstrap_rejects_unresolved_retention_residue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = _pair("abcde", "6")
    docker = FakeDocker(pair)
    monkeypatch.setattr(subprocess, "run", docker.run)
    ensure_pair_references((pair,), cwd="/tmp")

    with pytest.raises(DeploymentContractError, match="unresolved"):
        require_empty_retention_namespace(cwd="/tmp")


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
    pair = _pair("abcde", "6")
    run = Mock(
        return_value=subprocess.CompletedProcess(
            ["docker"], 1, stdout="", stderr="permission denied\n"
        )
    )
    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(DeploymentContractError, match="cannot be inspected"):
        ensure_pair_references((pair,), cwd="/tmp")


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
    pair = _pair("abcde", "6")
    reference = (
        f"{RETENTION_REPOSITORY_PREFIX}kor-travel-map-api:"
        f"{pair.map_image_id.removeprefix('sha256:')}"
    )
    run = Mock(
        return_value=subprocess.CompletedProcess(
            ["docker"], 1, stdout=stdout, stderr=stderr.format(reference=reference)
        )
    )
    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(DeploymentContractError, match="cannot be inspected"):
        ensure_pair_references((pair,), cwd="/tmp")


def test_invalid_reference_in_owned_namespace_blocks_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = _pair("abcde", "6")
    docker = FakeDocker(pair)
    docker.references[f"{RETENTION_REPOSITORY_PREFIX}unknown:latest"] = (
        pair.map_image_id
    )
    monkeypatch.setattr(subprocess, "run", docker.run)

    with pytest.raises(DeploymentContractError, match="invalid reference"):
        reconcile_pair_references((pair,), cwd="/tmp")
