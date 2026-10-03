from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from typing import Any
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.c6c_image_retention import (
    CANDIDATE_REPOSITORY_PREFIX,
    RETENTION_REPOSITORY_PREFIX,
    ensure_generation_references,
    reconcile_candidate_build_references,
    reconcile_generation_references,
    require_empty_generation_retention_namespace,
    validate_retention_namespace_is_reserved,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    MapApplication300CandidateEvidence,
    PinnedRuntimeGeneration,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    current_pinned_runtime_release,
)
from kor_travel_docker_manager.services.runtime_topology import (
    RuntimeSlot,
    RuntimeTopology,
    derive_dagster_families,
    runtime_topology,
)

#: pinned pair의 기제는 "Map·PinVi 모두 own" 기준선 위에서 본다 — 전환은 `flipped=(…)`/`_flip`으로 얹는다(conftest).
pytestmark = pytest.mark.usefixtures("own_pinned_pair")

PINNED_RUNTIME_RELEASE = current_pinned_runtime_release()


def _image(character: str) -> str:
    return f"sha256:{character * 64}"


def _candidate_evidence(seed: str) -> MapApplication300CandidateEvidence:
    return MapApplication300CandidateEvidence(
        candidate_git_tree=seed * 40,
        dagster_config_sha256=seed * 64,
    )


def _generation(characters: str, revision: str) -> PinnedRuntimeGeneration:
    return PinnedRuntimeGeneration(
        map_api_image_id=_image(characters[0]),
        map_ui_image_id=_image(characters[1]),
        map_dagster_image_id=_image(characters[2]),
        map_dagster_daemon_image_id=_image(characters[2]),
        pinvi_api_image_id=_image(characters[4]),
        pinvi_web_image_id=_image(characters[5]),
        pinvi_dagster_image_id=_image(characters[6]),
        map_source_revision=PINNED_RUNTIME_RELEASE.source_for("map").revision,
        pinvi_source_revision=PINNED_RUNTIME_RELEASE.source_for("pinvi").revision,
        map_application_head="300",
        pinvi_head="20260801_0050",
        pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
        map_application_300_candidate_evidence=_candidate_evidence(revision),
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
        self.lose_remove_response_for: set[str] = set()
        self.remove_stdout_for: dict[str, str] = {}
        self.duplicate_candidate_listing_for: str | None = None
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
            if docker_args[-1] == "--format={{.Repository}}:{{.Tag}}\t{{.ID}}":
                output = "".join(
                    f"{reference}\t{image_id}\n"
                    for reference, image_id in sorted(self.references.items())
                )
                duplicate = self.duplicate_candidate_listing_for
                if duplicate is not None:
                    output += f"{duplicate}\t{self.references[duplicate]}\n"
            else:
                output = "".join(
                    f"{reference}\n" for reference in sorted(self.references)
                )
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
        if docker_args[:2] == ("image", "rm"):
            reference = docker_args[2]
            self.references.pop(reference, None)
            if reference in self.lose_remove_response_for:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=self.remove_stdout_for.get(
                    reference,
                    f"Untagged: {reference}\n",
                ),
                stderr="",
            )
        raise AssertionError(command)


#: candidate tag slot과, 모두 `own`일 때 그 tag가 붙는 이름(ADR-54 파생 이전 literal 그대로).
_CANDIDATE_SERVICES: tuple[tuple[RuntimeSlot, str], ...] = (
    ("map_api", "kor-travel-map-api"),
    ("map_ui", "kor-travel-map-ui"),
    ("map_dagster", "kor-travel-map-dagster"),
    ("pinvi_api", "pinvi-api"),
    ("pinvi_web", "pinvi-web"),
    ("pinvi_dagster", "pinvi-dagster"),
)


def _candidate_references(
    generation: PinnedRuntimeGeneration,
    *,
    pinset_sha256: str | None = None,
    services: Mapping[RuntimeSlot, str] | None = None,
) -> dict[RuntimeSlot, str]:
    pinset = pinset_sha256 or generation.pinset_sha256
    names = dict(_CANDIDATE_SERVICES) if services is None else services
    return {
        slot: f"{CANDIDATE_REPOSITORY_PREFIX}{names[slot]}:{pinset}"
        for slot, _ in _CANDIDATE_SERVICES
    }


def _install_candidate_references(
    docker: FakeDocker,
    generation: PinnedRuntimeGeneration,
    *,
    pinset_sha256: str | None = None,
) -> dict[RuntimeSlot, str]:
    references = _candidate_references(generation, pinset_sha256=pinset_sha256)
    docker.references.update(
        {
            reference: generation.image_ids[service]
            for service, reference in references.items()
        }
    )
    return references


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


@pytest.mark.parametrize(
    "prefix",
    (RETENTION_REPOSITORY_PREFIX, CANDIDATE_REPOSITORY_PREFIX),
)
def test_compose_image_cannot_use_retention_namespace(prefix: str) -> None:
    validate_retention_namespace_is_reserved(
        {"services": {"api": {"image": "example/api:latest"}}}
    )

    with pytest.raises(DeploymentContractError, match="retention namespace"):
        validate_retention_namespace_is_reserved(
            {
                "services": {
                    "api": {
                        "image": f"{prefix}api:latest",
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


def test_candidate_reconcile_removes_stale_pinset_and_preserves_active_six(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _generation("abcdef1", "6")
    stale = _generation("2345678", "7")
    docker = FakeDocker(active, stale)
    monkeypatch.setattr(subprocess, "run", docker.run)
    ensure_generation_references((active,), cwd="/tmp")
    active_references = _install_candidate_references(docker, active)
    stale_references = _install_candidate_references(
        docker,
        stale,
        pinset_sha256="8" * 64,
    )

    report = reconcile_candidate_build_references(
        active_references,
        active,
        cwd="/tmp",
    )

    assert report.ensured == 0
    assert report.removed == 6
    assert {
        reference
        for reference in docker.references
        if reference.startswith(CANDIDATE_REPOSITORY_PREFIX)
    } == set(active_references.values())
    assert set(stale_references.values()).isdisjoint(docker.references)
    assert all(
        docker.references[reference] == active.image_ids[service]
        for service, reference in active_references.items()
    )


def test_candidate_reconcile_requires_content_retention_before_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _generation("abcdef1", "6")
    stale = _generation("2345678", "7")
    docker = FakeDocker(active, stale)
    active_references = _install_candidate_references(docker, active)
    _install_candidate_references(docker, stale, pinset_sha256="8" * 64)
    monkeypatch.setattr(subprocess, "run", docker.run)

    with pytest.raises(DeploymentContractError, match="retained content"):
        reconcile_candidate_build_references(active_references, active, cwd="/tmp")

    assert not any(command[1:3] == ("image", "rm") for command in docker.commands)


def test_candidate_reconcile_recovers_from_remove_response_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _generation("abcdef1", "6")
    stale = _generation("2345678", "7")
    docker = FakeDocker(active, stale)
    monkeypatch.setattr(subprocess, "run", docker.run)
    ensure_generation_references((active,), cwd="/tmp")
    active_references = _install_candidate_references(docker, active)
    stale_references = _install_candidate_references(
        docker,
        stale,
        pinset_sha256="8" * 64,
    )
    lost_response_reference = next(iter(stale_references.values()))
    docker.lose_remove_response_for.add(lost_response_reference)

    report = reconcile_candidate_build_references(
        active_references,
        active,
        cwd="/tmp",
    )

    assert report.removed == 6
    assert lost_response_reference not in docker.references
    assert set(active_references.values()) <= docker.references.keys()


def test_candidate_reconcile_accepts_strict_docker_deleted_image_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _generation("abcdef1", "6")
    stale = _generation("2345678", "7")
    docker = FakeDocker(active, stale)
    monkeypatch.setattr(subprocess, "run", docker.run)
    ensure_generation_references((active,), cwd="/tmp")
    active_references = _install_candidate_references(docker, active)
    stale_references = _install_candidate_references(
        docker,
        stale,
        pinset_sha256="8" * 64,
    )
    removed_reference = next(iter(stale_references.values()))
    docker.remove_stdout_for[removed_reference] = (
        f"Untagged: {removed_reference}\nDeleted: {_image('9')}\n"
    )

    report = reconcile_candidate_build_references(
        active_references,
        active,
        cwd="/tmp",
    )

    assert report.removed == 6
    assert set(active_references.values()) <= docker.references.keys()


@pytest.mark.parametrize("ambiguous", [False, True])
def test_candidate_reconcile_rejects_foreign_or_ambiguous_owned_reference(
    monkeypatch: pytest.MonkeyPatch,
    ambiguous: bool,
) -> None:
    active = _generation("abcdef1", "6")
    docker = FakeDocker(active)
    monkeypatch.setattr(subprocess, "run", docker.run)
    ensure_generation_references((active,), cwd="/tmp")
    active_references = _install_candidate_references(docker, active)
    if ambiguous:
        docker.duplicate_candidate_listing_for = next(iter(active_references.values()))
        expected = "ambiguous reference"
    else:
        foreign = f"{CANDIDATE_REPOSITORY_PREFIX}foreign-service:{'8' * 64}"
        docker.references[foreign] = active.map_api_image_id
        expected = "invalid reference"

    with pytest.raises(DeploymentContractError, match=expected):
        reconcile_candidate_build_references(active_references, active, cwd="/tmp")

    assert not any(command[1:3] == ("image", "rm") for command in docker.commands)


def test_candidate_reconcile_rejects_active_reference_content_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _generation("abcdef1", "6")
    other = _generation("2345678", "7")
    docker = FakeDocker(active, other)
    monkeypatch.setattr(subprocess, "run", docker.run)
    ensure_generation_references((active,), cwd="/tmp")
    active_references = _install_candidate_references(docker, active)
    docker.references[active_references["map_api"]] = other.map_api_image_id

    with pytest.raises(DeploymentContractError, match="active candidate reference changed"):
        reconcile_candidate_build_references(active_references, active, cwd="/tmp")

    assert not any(command[1:3] == ("image", "rm") for command in docker.commands)


# --- ADR-54: 공용 Dagster plane에 합류한 target ------------------------------------------------


def _flipped_topology(*targets: str) -> RuntimeTopology:
    from test_dagster_shared_workspace_is_derived import _flip, _own_pair_documents

    compose_document, targets_document = _own_pair_documents()
    for target_id in targets:
        _flip(compose_document, targets_document, target_id)
    return runtime_topology(derive_dagster_families(compose_document, targets_document))


def test_every_own_target_retains_the_pre_adr54_reference_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation = _generation("abcdef1", "6")
    docker = FakeDocker(generation)
    monkeypatch.setattr(subprocess, "run", docker.run)

    ensure_generation_references((generation,), cwd="/tmp")

    names = {reference.split("/")[-1].split(":")[0] for reference in docker.references}
    assert names == {
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "pinvi-api",
        "pinvi-web",
        "pinvi-dagster",
    }


def test_a_flipped_map_retains_its_code_server_not_its_old_dagster_and_prunes_their_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """전환 전 tag가 남은 채 Map이 합류한다: 옛 webserver·daemon tag는 모르는 reference로 멈추지 않고 stale로
    지워지며, 새로 보존하는 것은 code-server다. daemon slot은 서비스가 없어 보존하지 않는다."""

    generation = _generation("abcdef1", "6")
    docker = FakeDocker(generation)
    monkeypatch.setattr(subprocess, "run", docker.run)
    reconcile_generation_references((generation,), cwd="/tmp")
    own_candidates = _install_candidate_references(docker, generation)
    assert len(docker.references) == 13
    topology = _flipped_topology("map")

    report = reconcile_generation_references((generation,), cwd="/tmp", topology=topology)

    retained = {
        reference.removeprefix(RETENTION_REPOSITORY_PREFIX).split(":")[0]
        for reference in docker.references
        if reference.startswith(RETENTION_REPOSITORY_PREFIX)
    }
    assert retained == {
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster-code-server",
        "pinvi-api",
        "pinvi-web",
        "pinvi-dagster",
    }
    assert report.removed == 2

    flipped_candidates = _candidate_references(
        generation,
        services={slot: topology.require_service(slot) for slot, _ in _CANDIDATE_SERVICES},
    )
    docker.references.update(
        {reference: generation.image_ids[slot] for slot, reference in flipped_candidates.items()}
    )
    removed = reconcile_candidate_build_references(
        flipped_candidates, generation, cwd="/tmp", topology=topology
    )
    assert removed.removed == 1
    assert own_candidates["map_dagster"] not in docker.references
    assert flipped_candidates["map_dagster"] in docker.references


def test_a_rollback_to_own_prunes_the_tags_the_shared_shape_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`shared`→`own` 되돌리기: 합류 동안 code-server 이름으로 남긴 보존·candidate tag를 `own` namespace도
    알아보고 stale로 지운다 — 모르는 reference로 보존 정리 전체가 멈추지 않는다(적대 리뷰 2026-09-30 MED-3)."""

    generation = _generation("abcdef1", "6")
    docker = FakeDocker(generation)
    monkeypatch.setattr(subprocess, "run", docker.run)
    shared = _flipped_topology("map", "pinvi")
    reconcile_generation_references((generation,), cwd="/tmp", topology=shared)
    shared_candidates = _candidate_references(
        generation,
        services={slot: shared.require_service(slot) for slot, _ in _CANDIDATE_SERVICES},
    )
    docker.references.update(
        {reference: generation.image_ids[slot] for slot, reference in shared_candidates.items()}
    )
    assert any("dagster-code-server:" in reference for reference in docker.references)

    report = reconcile_generation_references((generation,), cwd="/tmp")
    own_candidates = _install_candidate_references(docker, generation)
    candidate_report = reconcile_candidate_build_references(own_candidates, generation, cwd="/tmp")

    assert report.removed == 2  # Map·PinVi code-server 보존 tag
    assert candidate_report.removed == 2  # Map·PinVi code-server candidate tag
    assert not any("dagster-code-server:" in reference for reference in docker.references)
    retained = {
        reference.removeprefix(RETENTION_REPOSITORY_PREFIX).split(":")[0]
        for reference in docker.references
        if reference.startswith(RETENTION_REPOSITORY_PREFIX)
    }
    assert "kor-travel-map-dagster-daemon" in retained and len(retained) == 7


def test_a_name_outside_every_family_still_stops_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """알아보는 이름을 family 전부로 넓혔어도 그 밖의 이름은 여전히 namespace를 멈춘다(fail-closed)."""

    generation = _generation("abcdef1", "6")
    docker = FakeDocker(generation)
    docker.references[f"{RETENTION_REPOSITORY_PREFIX}grafana:{'a' * 64}"] = generation.map_api_image_id
    monkeypatch.setattr(subprocess, "run", docker.run)

    with pytest.raises(DeploymentContractError, match="invalid reference"):
        require_empty_generation_retention_namespace(cwd="/tmp")
