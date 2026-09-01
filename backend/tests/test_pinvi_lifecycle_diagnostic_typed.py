"""GM-06 회귀: PinVi lifecycle 진단이 메시지 재파싱이 아니라 타입 속성으로 전달된다.

이전에는 `_pinned_runtime_compose_failure_diagnostic`가 진단 코드를 예외 메시지에
`"; pinvi_role:{code})"` 형태로 심고, `_pinvi_lifecycle_diagnostic`가 나중에 `str(error)`
를 다시 파싱해 그 접미사를 추출했다. 메시지 조립 형식(괄호 위치 등)이 바뀌면 이
재파싱이 실패해 role_topology_noncanonical 같은 terminal 판정이 조용히
"unclassified"로 강등된다. `PinnedRuntimeComposeFailure.pinvi_role_diagnostic` 속성이
이제 그 재파싱을 대체하고, 문자열 매칭은 그 타입이 아닌 예외를 위한 폴백으로만
남는다.
"""

from __future__ import annotations

from kor_travel_docker_manager.services.c6c_deployment import (
    _PINVI_DB_RUNTIME_ROLE_SERVICE,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
    PinnedRuntimeComposeFailure,
    _ComposeFailureDiagnostic,
)


def _run_result(*, stdout: str = "", stderr: str = "", returncode: int = 1) -> dict[str, object]:
    return {"success": False, "returncode": returncode, "stdout": stdout, "stderr": stderr}


def test_diagnostic_carries_role_topology_code_structurally() -> None:
    """가장 중요한 코드: 이게 흘러야 M05 terminal 판정이 선다."""

    result = _run_result(
        stderr="runtime/migrator/migration-owner role topology is not canonical\n"
    )
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", _PINVI_DB_RUNTIME_ROLE_SERVICE], result
    )
    assert isinstance(diagnostic, _ComposeFailureDiagnostic)
    assert diagnostic.pinvi_role_code == "role_topology_noncanonical"
    assert diagnostic.message_suffix == "; pinvi_role:role_topology_noncanonical"


def test_unmatched_pinvi_role_output_is_unclassified_but_structural() -> None:
    result = _run_result(stderr="some unexpected container output\n")
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", _PINVI_DB_RUNTIME_ROLE_SERVICE], result
    )
    assert diagnostic.pinvi_role_code == "unclassified"


def test_non_pinvi_target_has_no_structured_code() -> None:
    result = _run_result(stderr="unrelated failure\n")
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", "some-other-service"], result
    )
    assert diagnostic.pinvi_role_code is None
    assert diagnostic.message_suffix == ""


def test_lifecycle_diagnostic_reads_the_attribute_not_the_message() -> None:
    """메시지 문구가 조립 형식(괄호 없음 등)을 어겨도 속성 경로는 흔들리지 않는다.

    이게 이번 리팩터의 핵심 방어다 — 예전 구현은 이 케이스에서 반드시 실패했다.
    """

    error = PinnedRuntimeComposeFailure(
        "an unrelated free-form message with no trailing paren at all",
        pinvi_role_diagnostic="role_topology_noncanonical",
    )
    assert (
        ComposeService._pinvi_lifecycle_diagnostic(error) == "role_topology_noncanonical"
    )


def test_lifecycle_diagnostic_falls_back_to_message_parsing_for_other_exception_types() -> (
    None
):
    """하위호환: 이 타입이 아닌 예외는 기존 문자열 매칭으로 계속 분류된다."""

    error = DeploymentContractError(
        "pinned runtime rebuild Compose run command failed "
        "(exit 1; pinvi_role:role_topology_noncanonical)"
    )
    assert (
        ComposeService._pinvi_lifecycle_diagnostic(error) == "role_topology_noncanonical"
    )


def test_lifecycle_diagnostic_falls_back_when_attribute_is_none() -> None:
    """PinnedRuntimeComposeFailure이어도 진단이 없으면(allow_typed_error_diagnostic=False)
    메시지 재파싱으로 폴백한다 — 그 경로도 메시지에 접미사가 없으면 unclassified다."""

    error = PinnedRuntimeComposeFailure(
        "pinned runtime rebuild Compose run command failed (exit 1)",
        pinvi_role_diagnostic=None,
    )
    assert ComposeService._pinvi_lifecycle_diagnostic(error) == "unclassified"


def test_admin_bootstrap_code_also_flows_structurally() -> None:
    import json

    payload = json.dumps({"error_code": "credential_missing", "phase": "provision"})
    # _PINVI_ADMIN_BOOTSTRAP_ERROR_PHASE_BY_CODE의 실제 매핑을 그대로 쓴다.
    from kor_travel_docker_manager.services.compose_service import (
        _PINVI_ADMIN_BOOTSTRAP_ERROR_PHASE_BY_CODE,
    )

    code, phase = next(iter(_PINVI_ADMIN_BOOTSTRAP_ERROR_PHASE_BY_CODE.items()))
    payload = json.dumps({"error_code": code, "phase": phase})
    result = _run_result(stdout=payload + "\n")
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", "pinvi-admin-bootstrap"], result
    )
    assert diagnostic.pinvi_role_code == code
    assert diagnostic.message_suffix == f"; pinvi:{code}"
