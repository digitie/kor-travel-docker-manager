"""C6c 배포 계약 위반 예외 계층의 정본.

GM-20: 이전에는 이 클래스들이 c6c_deployment.py(7,800줄대) 안에 있어서,
`DeploymentContractError` 하나만 필요한 leaf 모듈 15개 이상이 그 거대 모듈
전체의 import 비용과 결합을 떠안았다. `c6c_deployment.py`는 하위 호환을 위해
이 모듈에서 재수출한다 — 기존 `from ...c6c_deployment import DeploymentContractError`
같은 import는 전부 그대로 동작한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class DeploymentContractError(ValueError):
    """C6c 배포가 컨테이너 변경 전에 중단되어야 하는 계약 위반."""


class ComposeCandidateContractError(DeploymentContractError):
    """compose candidate가 C6c 보호값 격리 계약을 위반했다."""

    code = "COMPOSE_CANDIDATE_PROTECTED_REFERENCE"


class ComposePostMutationContractError(DeploymentContractError):
    """mutation 성공 뒤 계약 drift가 발생해 복구 결과를 함께 보존한다."""

    code = "COMPOSE_POST_MUTATION_CONTRACT_FAILURE"

    def __init__(
        self,
        error: Exception,
        *,
        recovery_attempted: bool,
        recovery_succeeded: bool,
        recovery_error: str | None,
        restoration: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(str(error))
        self.original_error = error
        self.recovery_attempted = recovery_attempted
        self.recovery_succeeded = recovery_succeeded
        self.recovery_error = recovery_error
        self.restoration = restoration
