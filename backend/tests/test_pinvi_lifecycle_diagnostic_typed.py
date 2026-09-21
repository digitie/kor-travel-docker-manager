"""GM-06 회귀: PinVi 진단이 메시지 재파싱이 아니라 타입 속성으로 전달된다.

이전에는 `_pinned_runtime_compose_failure_diagnostic`가 진단 코드를 예외 메시지에
접미사로 심고 나중에 `str(error)`를 다시 파싱해 추출했다. 메시지 조립 형식이 바뀌면
그 재파싱이 조용히 실패해 판정이 강등된다. `PinnedRuntimeComposeFailure`의 속성이
그 재파싱을 대체한다.

**이 파일의 대상이 한 번 바뀌었다.** 종전에는 M05 role one-shot의 코드 공간을
검사했는데, 그 모델을 폐기(geo 패턴 전환)하면서 role one-shot과 그 코드 공간이
사라졌다. 남은 PinVi 타입 오류 생산자는 `pinvi-admin-bootstrap` 하나이고, 구조적
전달이라는 원래 요지는 그대로 적용된다.
"""

from __future__ import annotations

from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
    _ComposeFailureDiagnostic,
)

_ADMIN_BOOTSTRAP = "pinvi-admin-bootstrap"


def _run_result(*, stdout: str = "", stderr: str = "", returncode: int = 1) -> dict[str, object]:
    return {"success": False, "returncode": returncode, "stdout": stdout, "stderr": stderr}


def test_diagnostic_carries_admin_bootstrap_code_structurally() -> None:
    """가장 중요한 코드: 이게 흘러야 운영자가 원인 문장을 본다."""

    result = _run_result(stderr='{"error_code": "migration_failed", "phase": "migration"}\n')
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", _ADMIN_BOOTSTRAP], result
    )
    assert isinstance(diagnostic, _ComposeFailureDiagnostic)
    assert diagnostic.pinvi_role_code == "migration_failed"
    assert diagnostic.message_suffix == "; pinvi:migration_failed"


def test_phase_must_match_the_declared_code() -> None:
    """코드와 phase가 어긋난 payload는 타입 있는 코드로 승격되지 않는다."""

    result = _run_result(stderr='{"error_code": "migration_failed", "phase": "startup"}\n')
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", _ADMIN_BOOTSTRAP], result
    )
    assert diagnostic.pinvi_role_code is None
    assert diagnostic.message_suffix == ""


def test_unmatched_output_has_no_structured_code() -> None:
    """M05 폐기 전에는 여기서 `unclassified`로 접혔다. 이제 코드 자체가 없다."""

    result = _run_result(stderr="some unexpected container output\n")
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", _ADMIN_BOOTSTRAP], result
    )
    assert diagnostic.pinvi_role_code is None
    assert diagnostic.message_suffix == ""


def test_non_pinvi_target_has_no_structured_code() -> None:
    result = _run_result(stderr="unrelated failure\n")
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", "some-other-service"], result
    )
    assert diagnostic.pinvi_role_code is None
    assert diagnostic.message_suffix == ""
