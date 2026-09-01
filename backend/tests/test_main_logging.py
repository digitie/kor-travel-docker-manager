"""GM-16: 로그 이중 기록 회귀 테스트 + MonthlyRotatingFileHandler 동시성 안전성.

핵심 함정: 실제 root logger를 직접 검사하는 테스트는 못 믿는다 — pytest
자신의 logging 플러그인이 세션 내내 root logger에 자기 캡처용 핸들러
(`_pytest.logging.LogCaptureHandler` 등)를 붙이고 관리하므로, 테스트 실행
중 `logging.getLogger().handlers`를 들여다보면 우리가 등록한 콘솔·파일
핸들러가 아니라 pytest의 것만 보이는 경우가 실제로 재현된다(직접 확인함).
그래서 `main._configure_logging()`을 root/package 로거 객체를 파라미터로
받게 리팩터링해 뒀고, 여기서는 완전히 독립된 `logging.Logger(...)` 인스턴스
(전역 매니저에 등록되지 않아 pytest가 손댈 수 없다)를 만들어 그 함수 자체의
동작만 검증한다."""

from __future__ import annotations

import logging

import kor_travel_docker_manager.main as main_module


def _fresh_loggers() -> tuple[logging.Logger, logging.Logger]:
    """`logging.getLogger(name)`가 아니라 `logging.Logger(name)`을 직접
    만든다 — 전역 매니저 계층에 등록되지 않으므로 pytest나 다른 테스트가
    같은 이름으로 얻어 간 로거와 절대 충돌하지 않는다."""

    root = logging.Logger("gm16-test-root")
    package = logging.Logger("gm16-test-package")
    return root, package


def test_configure_logging_gives_the_package_logger_no_handlers_of_its_own(
    tmp_path,
) -> None:
    """이중 기록의 근본 원인 재발 방지: 패키지 로거가 자기 핸들러를 다시
    갖게 되면(레벨만이 아니라) 그 순간 이 불변식이 깨진다. 이게 참인 한,
    Python logging의 결정적 동작상(핸들러는 record가 실제로 도달한 로거의
    handlers 리스트에서만 호출된다) 이중 emit은 구조적으로 불가능하다."""

    root, package = _fresh_loggers()

    main_module._configure_logging(root, package, str(tmp_path / "app.log"))

    assert package.handlers == []
    assert package.propagate is True


def test_configure_logging_attaches_exactly_one_console_and_one_file_handler(
    tmp_path,
) -> None:
    root, package = _fresh_loggers()

    main_module._configure_logging(root, package, str(tmp_path / "app.log"))

    handler_types = [type(h) for h in root.handlers]
    assert handler_types.count(logging.StreamHandler) == 1
    assert handler_types.count(main_module.MonthlyRotatingFileHandler) == 1


def test_configure_logging_is_idempotent_against_reimport(tmp_path) -> None:
    """`--reload`로 이 모듈이 재-import돼도 핸들러가 중복 부착되면 안 된다
    (기존의 "기존 핸들러 초기화 방지" 가드가 여전히 살아있는지)."""

    root, package = _fresh_loggers()
    log_path = str(tmp_path / "app.log")

    main_module._configure_logging(root, package, log_path)
    main_module._configure_logging(root, package, log_path)

    assert len(root.handlers) == 2


def test_configure_logging_attaches_a_request_id_filter_to_every_handler(
    tmp_path,
) -> None:
    """RequestIdLogFilter가 실제로 두 핸들러 모두에 걸려 있는지 — 필터가
    없으면 포맷 문자열의 %(request_id)s가 어느 로거에서 왔든 KeyError로
    로깅 자체를 깨뜨린다. `handler.handle()`을 직접 불러 실제로 이 경로가
    죽지 않고 request_id 속성이 채워짐을 함께 확인한다."""

    root, package = _fresh_loggers()
    main_module._configure_logging(root, package, str(tmp_path / "app.log"))

    assert root.handlers
    for handler in root.handlers:
        assert any(isinstance(f, main_module.RequestIdLogFilter) for f in handler.filters)

    record = logging.LogRecord(
        name="gm16-test.deep.child",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="gm16 request-id marker",
        args=(),
        exc_info=None,
    )
    for handler in root.handlers:
        handler.handle(record)

    # 필터는 emit 전에 record를 직접 변형한다 — handle() 자체가 내부에서
    # 예외를 삼키므로(logging의 기본 handleError), 포맷 실패 여부가 아니라
    # 필터가 실제로 이 속성을 채웠는지를 직접 확인한다.
    assert record.request_id == "-"  # 이 테스트는 HTTP 요청 컨텍스트 밖이다


def test_do_rollover_appends_instead_of_destroying_a_concurrent_archive(
    tmp_path,
) -> None:
    """GM-16: --reload 다중 워커가 같은 로그 파일을 공유하면 두 프로세스가
    거의 동시에 rollover를 시도할 수 있다. 무조건 os.remove는 먼저
    rollover한 프로세스가 이번 달 아카이브에 이미 써 둔 로그를 파괴한다 —
    이제는 이어 붙인다."""

    log_path = tmp_path / "app.log"
    log_path.write_text("line from this process\n", encoding="utf-8")

    handler = main_module.MonthlyRotatingFileHandler(str(log_path), encoding="utf-8")
    try:
        # 다른(동시) 프로세스가 이미 이번 달 아카이브를 만들어 둔 상황을 흉내낸다.
        archive_path = log_path.with_name(f"{log_path.name}.{handler.current_month}")
        archive_path.write_text("line from the other process\n", encoding="utf-8")

        handler.doRollover()

        archived = archive_path.read_text(encoding="utf-8")
        assert "line from the other process" in archived
        assert "line from this process" in archived
        # 활성 로그 파일은 비워져 다음 기록을 받을 준비가 된다.
        assert not log_path.exists() or log_path.read_text(encoding="utf-8") == ""
    finally:
        handler.close()


def test_do_rollover_renames_when_no_concurrent_archive_exists(tmp_path) -> None:
    """충돌이 없는 흔한 경우는 예전처럼 단순 rename이어야 한다(불필요한
    read+append+remove로 바뀌지 않는다)."""

    log_path = tmp_path / "app.log"
    log_path.write_text("only this process\n", encoding="utf-8")

    handler = main_module.MonthlyRotatingFileHandler(str(log_path), encoding="utf-8")
    try:
        archive_path = log_path.with_name(f"{log_path.name}.{handler.current_month}")
        assert not archive_path.exists()

        handler.doRollover()

        assert archive_path.read_text(encoding="utf-8") == "only this process\n"
    finally:
        handler.close()
