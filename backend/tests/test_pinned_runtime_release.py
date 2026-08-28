"""pinned release 계약 테스트.

v3 전환 이후 이 파일은 **특정 SHA 값을 고정하지 않는다.** pin은 registry 파일에
있으므로 여기서 값을 박아 두면 회전할 때마다 테스트가 함께 churn한다(그 churn을
없애는 것이 전환의 목적이다). 대신 값이 무엇이든 성립해야 하는 성질 —
canonical URL 강제, 40-hex 형식, role 순서, digest 재계산 대조, 파생 상수의 단일화 —
을 검증한다. 값 자체의 무결성은 런타임의 digest 재계산과 ``ktdctl pin verify``가
담당하고, 그 경로는 ``test_runtime_pin_registry.py``가 검증한다.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.map_application_300 import (
    expected_application_300_source_commit,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    CANONICAL_RUNTIME_SOURCE_URLS,
    PINNED_RUNTIME_RELEASE_VERSION,
    RUNTIME_SOURCE_ROLES,
    PinnedRuntimeRelease,
    PinnedRuntimeSourceSpec,
    canonical_pinset_bytes,
    canonical_pinset_sha256,
    current_map_source_revision,
    current_pinned_runtime_release,
    source_specs_for,
)

_REVISION_A = "a" * 40
_REVISION_B = "b" * 40


def _release(map_revision: str = _REVISION_A, pinvi_revision: str = _REVISION_B):
    sources = source_specs_for(map_revision=map_revision, pinvi_revision=pinvi_revision)
    return PinnedRuntimeRelease(
        version=PINNED_RUNTIME_RELEASE_VERSION,
        sources=sources,
        pinset_sha256=canonical_pinset_sha256(
            version=PINNED_RUNTIME_RELEASE_VERSION,
            sources=sources,
        ),
    )


def test_current_release_reads_the_registry_and_keeps_the_v5_shape() -> None:
    release = current_pinned_runtime_release()

    assert release.version == PINNED_RUNTIME_RELEASE_VERSION == 5
    assert tuple(source.role for source in release.sources) == RUNTIME_SOURCE_ROLES
    for role in RUNTIME_SOURCE_ROLES:
        source = release.source_for(role)
        assert source.canonical_url == CANONICAL_RUNTIME_SOURCE_URLS[role]
        assert len(source.revision) == 40
    # digest는 파일에 적힌 값이 아니라 재계산 결과여야 한다.
    assert release.pinset_sha256 == canonical_pinset_sha256(
        version=release.version,
        sources=release.sources,
    )


def test_map_application_300_expects_the_same_commit_as_the_pin() -> None:
    """이원 관리 hazard가 소멸했는지 — 두 경로가 같은 registry 값을 읽는다."""

    release = current_pinned_runtime_release()

    assert expected_application_300_source_commit() == release.source_for("map").revision
    assert current_map_source_revision() == release.source_for("map").revision


def test_pinset_digest_uses_stable_canonical_compact_json() -> None:
    """digest 계산 규칙은 map 저장소 attestation과 공유하는 계약이라 고정한다."""

    sources = source_specs_for(map_revision=_REVISION_A, pinvi_revision=_REVISION_B)

    assert canonical_pinset_bytes(version=5, sources=sources) == (
        b'{"sources":[{"revision":"' + _REVISION_A.encode() + b'",'
        b'"role":"map","url":"https://github.com/digitie/kor-travel-map.git"},'
        b'{"revision":"' + _REVISION_B.encode() + b'",'
        b'"role":"pinvi","url":"https://github.com/digitie/pinvi.git"}],"version":5}'
    )


def test_source_specs_for_supplies_canonical_urls_from_code() -> None:
    sources = source_specs_for(map_revision=_REVISION_A, pinvi_revision=_REVISION_B)

    assert [source.role for source in sources] == ["map", "pinvi"]
    assert sources[0].canonical_url == CANONICAL_RUNTIME_SOURCE_URLS["map"]
    assert sources[1].canonical_url == CANONICAL_RUNTIME_SOURCE_URLS["pinvi"]


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
    map_source = source_specs_for(map_revision=_REVISION_A, pinvi_revision=_REVISION_B)[0]
    digest = canonical_pinset_sha256(version=5, sources=(map_source, map_source))

    with pytest.raises(DeploymentContractError, match="exactly once"):
        PinnedRuntimeRelease(version=5, sources=(map_source, map_source), pinset_sha256=digest)


def test_release_rejects_digest_for_different_source_order() -> None:
    release = _release()
    reversed_sources = tuple(reversed(release.sources))

    with pytest.raises(DeploymentContractError, match="exactly once"):
        PinnedRuntimeRelease(
            version=5,
            sources=reversed_sources,
            pinset_sha256=release.pinset_sha256,
        )


@pytest.mark.parametrize("pinset_sha256", ["z" * 64, "a" * 63])
def test_release_rejects_malformed_pinset_digest(pinset_sha256: str) -> None:
    sources = source_specs_for(map_revision=_REVISION_A, pinvi_revision=_REVISION_B)

    with pytest.raises(DeploymentContractError, match="digest"):
        PinnedRuntimeRelease(version=5, sources=sources, pinset_sha256=pinset_sha256)


def test_release_rejects_a_digest_that_does_not_match_its_sources() -> None:
    other = _release(map_revision="c" * 40)

    with pytest.raises(DeploymentContractError, match="digest differs"):
        PinnedRuntimeRelease(
            version=5,
            sources=source_specs_for(map_revision=_REVISION_A, pinvi_revision=_REVISION_B),
            pinset_sha256=other.pinset_sha256,
        )


def test_source_specs_and_role_view_are_immutable() -> None:
    release = _release()

    with pytest.raises(FrozenInstanceError):
        release.source_for("map").revision = "a" * 40  # type: ignore[misc]
    with pytest.raises(TypeError):
        release.sources_by_role["map"] = release.source_for("pinvi")  # type: ignore[index]
