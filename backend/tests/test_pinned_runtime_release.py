from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
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
)


def test_current_release_is_exact_map_and_pinvi_v5_authority() -> None:
    release = current_pinned_runtime_release()

    assert release is PINNED_RUNTIME_RELEASE
    assert release.version == PINNED_RUNTIME_RELEASE_VERSION == 5
    assert release.source_for("map") == MAP_PINNED_RUNTIME_SOURCE
    assert release.source_for("pinvi") == PINVI_PINNED_RUNTIME_SOURCE
    assert release.source_for("map").revision == "443a7ff3ddacb4a43d816fad235833f78b5c6511"
    assert release.source_for("pinvi").revision == "25505e05630fe167889e8595ee47f1ed0fdff13f"
    assert release.sources_by_role == {
        "map": MAP_PINNED_RUNTIME_SOURCE,
        "pinvi": PINVI_PINNED_RUNTIME_SOURCE,
    }


def test_pinset_digest_uses_stable_canonical_compact_json() -> None:
    release = PINNED_RUNTIME_RELEASE

    assert canonical_pinset_bytes(version=release.version, sources=release.sources) == (
        b'{"sources":[{"revision":"443a7ff3ddacb4a43d816fad235833f78b5c6511",'
        b'"role":"map","url":"https://github.com/digitie/kor-travel-map.git"},'
        b'{"revision":"25505e05630fe167889e8595ee47f1ed0fdff13f",'
        b'"role":"pinvi","url":"https://github.com/digitie/pinvi.git"}],"version":5}'
    )
    assert canonical_pinset_sha256(version=release.version, sources=release.sources) == (
        "3db2950b94359697ffcf152c0317fc0f6b06266589ceddb923fe690dfbb3529b"
    )
    assert release.pinset_sha256 == "3db2950b94359697ffcf152c0317fc0f6b06266589ceddb923fe690dfbb3529b"


@pytest.mark.parametrize(
    "role,canonical_url,revision,error",
    [
        (
            "map",
            "https://github.com/digitie/kor-travel-map.git/",
            "443a7ff3ddacb4a43d816fad235833f78b5c6511",
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
