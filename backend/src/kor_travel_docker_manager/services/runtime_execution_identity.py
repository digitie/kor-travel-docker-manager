"""M05 one-shot의 Manager-aware 실행 식별자(v6).

v5 ``pinset_sha256``은 Map·PinVi source materialization의 식별자다. 이를 Manager
revision까지 억지로 확장하면 이미 생성된 registry·generation·terminal audit의 뜻이
바뀐다. 실행 권한은 별도 v6 identity로 표현한다. 이 값의 Manager revision은 장차
trusted installer provenance에서만 공급하며, CLI/환경 입력으로 받지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

EXECUTION_IDENTITY_VERSION: Final = 6
SOURCE_PINSET_VERSION: Final = 5
CANONICAL_MANAGER_SOURCE_URL: Final = (
    "https://github.com/digitie/kor-travel-docker-manager.git"
)

_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_execution_identity_bytes(
    *, source_pinset_sha256: str, manager_source_revision: str
) -> bytes:
    """Map·PinVi v5 source pin과 trusted Manager revision의 canonical preimage."""

    if _SHA256.fullmatch(source_pinset_sha256) is None:
        raise DeploymentContractError("execution identity source pinset digest is invalid")
    if _REVISION.fullmatch(manager_source_revision) is None:
        raise DeploymentContractError("execution identity Manager revision is invalid")
    payload = {
        "version": EXECUTION_IDENTITY_VERSION,
        "source_pinset": {
            "version": SOURCE_PINSET_VERSION,
            "sha256": source_pinset_sha256,
        },
        "manager": {
            "url": CANONICAL_MANAGER_SOURCE_URL,
            "source_revision": manager_source_revision,
        },
    }
    return json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def canonical_execution_identity_sha256(
    *, source_pinset_sha256: str, manager_source_revision: str
) -> str:
    """v6 실행 후보 digest. Manager 변경만으로 새 실행 후보가 된다."""

    return hashlib.sha256(
        canonical_execution_identity_bytes(
            source_pinset_sha256=source_pinset_sha256,
            manager_source_revision=manager_source_revision,
        )
    ).hexdigest()


@dataclass(frozen=True)
class ExecutionIdentityV6:
    """검증된 source pinset + trusted Manager revision의 immutable execution key."""

    source_pinset_sha256: str
    manager_source_revision: str
    execution_identity_sha256: str

    def __post_init__(self) -> None:
        expected = canonical_execution_identity_sha256(
            source_pinset_sha256=self.source_pinset_sha256,
            manager_source_revision=self.manager_source_revision,
        )
        if _SHA256.fullmatch(self.execution_identity_sha256) is None:
            raise DeploymentContractError("execution identity digest is invalid")
        if self.execution_identity_sha256 != expected:
            raise DeploymentContractError("execution identity digest differs")

    @classmethod
    def build(
        cls, *, source_pinset_sha256: str, manager_source_revision: str
    ) -> ExecutionIdentityV6:
        return cls(
            source_pinset_sha256=source_pinset_sha256,
            manager_source_revision=manager_source_revision,
            execution_identity_sha256=canonical_execution_identity_sha256(
                source_pinset_sha256=source_pinset_sha256,
                manager_source_revision=manager_source_revision,
            ),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "version": EXECUTION_IDENTITY_VERSION,
            "source_pinset": {
                "version": SOURCE_PINSET_VERSION,
                "sha256": self.source_pinset_sha256,
            },
            "manager": {
                "url": CANONICAL_MANAGER_SOURCE_URL,
                "source_revision": self.manager_source_revision,
            },
            "execution_identity_sha256": self.execution_identity_sha256,
        }
