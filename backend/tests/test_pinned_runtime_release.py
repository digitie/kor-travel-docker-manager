from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.map_application_300 import (
    MAP_APPLICATION_300_SOURCE_COMMIT,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    CANONICAL_RUNTIME_SOURCE_URLS,
    MAP_PINNED_RUNTIME_SOURCE,
    PINNED_RUNTIME_RELEASE,
    PINNED_RUNTIME_RELEASE_VERSION,
    PINVI_PINNED_RUNTIME_SOURCE,
    PinnedRuntimeRelease,
    PinnedRuntimeSourceSpec,
    canonical_pinset_bytes,
    canonical_pinset_sha256,
    current_pinned_runtime_release,
    is_d9_legacy_pinvi_role_topology_retry,
)


def test_current_release_is_exact_map_and_pinvi_v5_authority() -> None:
    release = current_pinned_runtime_release()

    assert release is PINNED_RUNTIME_RELEASE
    assert release.version == PINNED_RUNTIME_RELEASE_VERSION == 5
    assert release.source_for("map") == MAP_PINNED_RUNTIME_SOURCE
    assert release.source_for("pinvi") == PINVI_PINNED_RUNTIME_SOURCE
    assert release.source_for("map").revision == "9c64e862c9da82016e12038e2e135526b300ca9d"
    assert release.source_for("map").revision == MAP_APPLICATION_300_SOURCE_COMMIT
    assert release.source_for("pinvi").revision == "f9df39bcbc0483c9c1067c2ce158c00eaf584a48"
    assert release.sources_by_role == {
        "map": MAP_PINNED_RUNTIME_SOURCE,
        "pinvi": PINVI_PINNED_RUNTIME_SOURCE,
    }


def test_pinset_digest_uses_stable_canonical_compact_json() -> None:
    release = PINNED_RUNTIME_RELEASE

    assert canonical_pinset_bytes(version=release.version, sources=release.sources) == (
        b'{"sources":[{"revision":"9c64e862c9da82016e12038e2e135526b300ca9d",'
        b'"role":"map","url":"https://github.com/digitie/kor-travel-map.git"},'
        b'{"revision":"f9df39bcbc0483c9c1067c2ce158c00eaf584a48",'
        b'"role":"pinvi","url":"https://github.com/digitie/pinvi.git"}],"version":5}'
    )
    assert canonical_pinset_sha256(version=release.version, sources=release.sources) == (
        "d4b34826192eaa435c97b8a531f0e0a2c750a50fedc01634d1a8e8070fbf9372"
    )
    assert release.pinset_sha256 == "d4b34826192eaa435c97b8a531f0e0a2c750a50fedc01634d1a8e8070fbf9372"


def test_d9_legacy_role_topology_retry_policy_is_exact() -> None:
    legacy = {
        "pinset_sha256": "d9aded44779114ed0595d3a4fb50908efb56b57c85148faf3083b0087a35e898",
        "map_source_revision": "14d18230e5a9ff21caf26d6abe37aed1e4944685",
        "pinvi_source_revision": "93296aee5d47676e6b9b79303bf417c598a273ac",
        "phase": "map_runtime_ready",
    }
    assert is_d9_legacy_pinvi_role_topology_retry(
        **legacy,
    )
    assert not is_d9_legacy_pinvi_role_topology_retry(
        **(legacy | {"phase": "candidate_attested"}),
    )
    assert not is_d9_legacy_pinvi_role_topology_retry(
        **(legacy | {"pinset_sha256": "a" * 64}),
    )
    assert not is_d9_legacy_pinvi_role_topology_retry(
        **(legacy | {"map_source_revision": "a" * 40}),
    )
    assert not is_d9_legacy_pinvi_role_topology_retry(
        **(legacy | {"pinvi_source_revision": "a" * 40}),
    )
    assert not is_d9_legacy_pinvi_role_topology_retry(
        pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
        map_source_revision=MAP_PINNED_RUNTIME_SOURCE.revision,
        pinvi_source_revision=PINVI_PINNED_RUNTIME_SOURCE.revision,
        phase="map_runtime_ready",
    )


@pytest.mark.parametrize(
    "role,canonical_url,revision,error",
    [
        (
            "map",
            "https://github.com/digitie/kor-travel-map.git/",
            "e420c89eb0f10776f7fb96e59ef3b409974d0d54",
            "URL",
        ),
        (
            "pinvi",
            CANONICAL_RUNTIME_SOURCE_URLS["pinvi"],
            "25505E05630FE167889E8595EE47F1ED0FDFF13F",
            "revision",
        ),
        (
            "unknown",
            "https://github.com/digitie/unknown.git",
            "a" * 40,
            "role",
        ),
    ],
)
def test_source_spec_rejects_noncanonical_or_malformed_values(
    role: str,
    canonical_url: str,
    revision: str,
    error: str,
) -> None:
    with pytest.raises(DeploymentContractError, match=error):
        PinnedRuntimeSourceSpec(
            role=role,  # type: ignore[arg-type]
            canonical_url=canonical_url,
            revision=revision,
        )


def test_release_requires_each_source_role_once_in_canonical_order() -> None:
    digest = canonical_pinset_sha256(
        version=5,
        sources=(MAP_PINNED_RUNTIME_SOURCE, MAP_PINNED_RUNTIME_SOURCE),
    )

    with pytest.raises(DeploymentContractError, match="exactly once"):
        PinnedRuntimeRelease(
            version=5,
            sources=(MAP_PINNED_RUNTIME_SOURCE, MAP_PINNED_RUNTIME_SOURCE),
            pinset_sha256=digest,
        )


def test_release_rejects_digest_for_different_source_order() -> None:
    with pytest.raises(DeploymentContractError, match="exactly once"):
        PinnedRuntimeRelease(
            version=5,
            sources=(PINVI_PINNED_RUNTIME_SOURCE, MAP_PINNED_RUNTIME_SOURCE),
            pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
        )


@pytest.mark.parametrize("pinset_sha256", ["z" * 64, "a" * 63])
def test_release_rejects_malformed_pinset_digest(pinset_sha256: str) -> None:
    with pytest.raises(DeploymentContractError, match="digest"):
        PinnedRuntimeRelease(
            version=5,
            sources=(MAP_PINNED_RUNTIME_SOURCE, PINVI_PINNED_RUNTIME_SOURCE),
            pinset_sha256=pinset_sha256,
        )


def test_source_specs_and_role_view_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        MAP_PINNED_RUNTIME_SOURCE.revision = "a" * 40  # type: ignore[misc]
    with pytest.raises(TypeError):
        PINNED_RUNTIME_RELEASE.sources_by_role["map"] = PINVI_PINNED_RUNTIME_SOURCE  # type: ignore[index]
