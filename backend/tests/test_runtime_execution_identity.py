"""Manager-aware M05 execution identity v6의 순수 계약 회귀."""

from __future__ import annotations

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.runtime_execution_identity import (
    CANONICAL_MANAGER_SOURCE_URL,
    EXECUTION_IDENTITY_VERSION,
    ExecutionIdentityV6,
    canonical_execution_identity_bytes,
    canonical_execution_identity_sha256,
)

_PINSET = "a" * 64
_MANAGER_A = "b" * 40
_MANAGER_B = "c" * 40


def test_identity_uses_stable_canonical_compact_json() -> None:
    assert canonical_execution_identity_bytes(
        source_pinset_sha256=_PINSET, manager_source_revision=_MANAGER_A
    ) == (
        b'{"manager":{"source_revision":"' + _MANAGER_A.encode() + b'","url":"'
        + CANONICAL_MANAGER_SOURCE_URL.encode()
        + b'"},"source_pinset":{"sha256":"'
        + _PINSET.encode()
        + b'","version":5},"version":6}'
    )


def test_manager_change_creates_new_execution_identity_without_mutating_source_pinset() -> None:
    first = ExecutionIdentityV6.build(
        source_pinset_sha256=_PINSET, manager_source_revision=_MANAGER_A
    )
    second = ExecutionIdentityV6.build(
        source_pinset_sha256=_PINSET, manager_source_revision=_MANAGER_B
    )

    assert first.source_pinset_sha256 == second.source_pinset_sha256 == _PINSET
    assert first.execution_identity_sha256 != second.execution_identity_sha256
    assert first.to_payload()["version"] == EXECUTION_IDENTITY_VERSION


@pytest.mark.parametrize(
    ("source_pinset_sha256", "manager_source_revision"),
    [("x" * 64, _MANAGER_A), (_PINSET, "x" * 40)],
)
def test_identity_rejects_untrusted_wire_values(
    source_pinset_sha256: str, manager_source_revision: str
) -> None:
    with pytest.raises(DeploymentContractError):
        canonical_execution_identity_sha256(
            source_pinset_sha256=source_pinset_sha256,
            manager_source_revision=manager_source_revision,
        )


def test_identity_rejects_digest_that_does_not_bind_both_inputs() -> None:
    with pytest.raises(DeploymentContractError, match="differs"):
        ExecutionIdentityV6(
            source_pinset_sha256=_PINSET,
            manager_source_revision=_MANAGER_A,
            execution_identity_sha256=canonical_execution_identity_sha256(
                source_pinset_sha256=_PINSET, manager_source_revision=_MANAGER_B
            ),
        )
