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


class ManagerMutationActiveError(DeploymentContractError):
    """다른 Manager mutation이 host 변경 lock을 쥐고 있어 기다리지 않고 거절했다.

    ADR-51 C: 모든 획득 경로가 ``LOCK_EX|LOCK_NB``로 한 lock을 잡는다. 이 예외는 그
    경합 **하나만** 뜻한다 — lock이 안전하지 않거나 열 수 없는 경우는 여전히 일반
    ``DeploymentContractError``다. 아무것도 바꾸기 전에 던지므로 재시도해도 안전하다.
    API는 ``code``를 실어 409로 내보낸다(``main._contract_error_detail``).
    """

    code = "MANAGER_MUTATION_ACTIVE"


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
