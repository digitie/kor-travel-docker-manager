"""GM-06 회귀: PinVi one-shot의 typed error가 원문 없이 진단 문구로만 붙는다.

이전에는 `_pinned_runtime_compose_failure_diagnostic`가 진단 코드를 예외 메시지에
접미사로 심고 나중에 `str(error)`를 다시 파싱해 추출했다. 그 재파싱은 한때 구조화된
속성으로 대체됐다.

**이 파일의 대상이 두 번 바뀌었다.** 종전에는 M05 role one-shot의 코드 공간을
검사했는데, 그 모델을 폐기(geo 패턴 전환)하면서 role one-shot과 그 코드 공간이
사라졌다. 남은 PinVi 타입 오류 생산자는 `pinvi-admin-bootstrap` 하나다. 그 뒤
구조화 속성을 읽던 v8 journal 차단 기록이 ADR-51 B3에서 사라져 속성도 지웠다 —
이제 남은 계약은 허용된 payload만 고정 형식의 문구가 된다는 것이다.
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
    assert diagnostic.message_suffix == "; pinvi:migration_failed"


def test_phase_must_match_the_declared_code() -> None:
    """코드와 phase가 어긋난 payload는 타입 있는 코드로 승격되지 않는다."""

    result = _run_result(stderr='{"error_code": "migration_failed", "phase": "startup"}\n')
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", _ADMIN_BOOTSTRAP], result
    )
    assert diagnostic.message_suffix == ""


def test_unmatched_output_has_no_structured_code() -> None:
    """M05 폐기 전에는 여기서 `unclassified`로 접혔다. 이제 코드 자체가 없다."""

    result = _run_result(stderr="some unexpected container output\n")
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", _ADMIN_BOOTSTRAP], result
    )
    assert diagnostic.message_suffix == ""


def test_non_pinvi_target_has_no_structured_code() -> None:
    result = _run_result(stderr="unrelated failure\n")
    diagnostic = ComposeService._pinned_runtime_compose_failure_diagnostic(
        ["run", "--rm", "some-other-service"], result
    )
    assert diagnostic.message_suffix == ""
