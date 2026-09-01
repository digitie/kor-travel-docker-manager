"""GM-14: SQLite 엔진에 busy_timeout PRAGMA가 실제로 걸리는지 확인한다.

`test_metrics.py`가 모듈 레벨에서 `kor_travel_docker_manager.database.engine`을
테스트용 엔진으로 통째로 바꿔치기하므로(테스트 격리 목적), 그 시점 이후에는
이 모듈의 `engine` 이름이 더 이상 원본을 가리키지 않는다 — 어느 테스트 파일이
먼저 수집되는지에 의존하는 단언은 만들지 않는다. 대신 connect 리스너
함수(`_set_sqlite_busy_timeout`) 자체를 raw sqlite3 커넥션에 직접 호출해
검증한다 — SQLAlchemy가 실제로 이 함수를 어떤 엔진에 걸든 동작이 같다는 것만
증명하면 충분하다.

WAL은 개발 환경(drvfs/9p)에서 shm/mmap이 실패할 수 있어, 매 연결마다 시도한 뒤
"PRAGMA journal_mode" 재조회로 실제 적용 여부를 확인하고 실패 시 안전하게
rollback-journal로 남는 `_set_sqlite_wal_mode` 리스너를 추가로 검증한다.
SQLite는 WAL 전환 실패 시 항상 예외를 던지는 게 아니라 조용히 이전 모드를
유지하기도 하므로(대표적으로 ":memory:" DB) 예외 캐치만으로는 불충분하다는
점이 이 테스트들의 핵심이다.

다만 리스너 함수를 raw 커넥션에 직접 호출하는 위 두 테스트는 함수 자체의
로직만 증명할 뿐, "`database.py`가 실제로 이 함수를 올바른 이벤트 이름으로
올바른 엔진에 등록했는가"라는 배선(integration point)은 검증하지 못한다
(리뷰 발견 — `@event.listens_for(engine, "connect")`의 `"connect"`를
`"checkout"`으로 mutation해도 이 두 테스트는 여전히 통과한다). 세 번째
테스트는 `conftest.py`가 미리 잡아 둔 원본 엔진 참조로 SQLAlchemy 자신의
이벤트 레지스트리(`event.contains`)를 직접 조회해 그 배선 자체를 검증한다."""

from __future__ import annotations

import sqlite3

from sqlalchemy import event

import kor_travel_docker_manager.database as database_module
from kor_travel_docker_manager.database import _set_sqlite_busy_timeout, _set_sqlite_wal_mode


def test_set_sqlite_busy_timeout_sets_the_pragma_on_a_connection() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        _set_sqlite_busy_timeout(connection, None)
        (value,) = connection.execute("PRAGMA busy_timeout").fetchone()
    finally:
        connection.close()
    assert value == 3000


def test_set_sqlite_busy_timeout_applies_independently_to_each_connection() -> None:
    """PRAGMA는 연결 단위다 — SQLAlchemy가 이 리스너를 매 connect 이벤트마다
    다시 호출하지 않으면 두 번째 이후 연결은 기본값(0)으로 돌아간다. 리스너
    자체가 매 호출마다 값을 제대로 세팅하는지를 두 개의 독립된 연결로 확인한다."""

    first = sqlite3.connect(":memory:")
    second = sqlite3.connect(":memory:")
    try:
        _set_sqlite_busy_timeout(first, None)
        _set_sqlite_busy_timeout(second, None)
        (first_value,) = first.execute("PRAGMA busy_timeout").fetchone()
        (second_value,) = second.execute("PRAGMA busy_timeout").fetchone()
    finally:
        first.close()
        second.close()
    assert first_value == 3000
    assert second_value == 3000


def test_the_real_engine_has_the_busy_timeout_listener_registered_on_connect(
    original_metrics_db_engine,
) -> None:
    """리뷰 반영: 위 두 테스트는 리스너 함수 자체만 증명한다 — `database.py`가
    그 함수를 실제로 (올바른 이벤트에, 올바른 엔진에) 등록했는지는 SQLAlchemy의
    이벤트 레지스트리를 직접 조회해야 확인할 수 있다. `original_metrics_db_engine`은
    `conftest.py`가 pytest 수집 순서와 무관하게 미리 캡처해 둔 원본 엔진이다
    (test_api.py/test_metrics.py가 나중에 `database.engine`을 테스트용으로
    바꿔치기해도 이 참조 자체는 영향받지 않는다)."""

    assert event.contains(original_metrics_db_engine, "connect", _set_sqlite_busy_timeout)


def test_set_sqlite_wal_mode_enables_wal_on_a_real_file(tmp_path, monkeypatch) -> None:
    """일반 파일시스템 위의 파일 기반 연결에서는 WAL 전환이 그대로 성공해야
    한다 — 이 경로가 fallback 로직 때문에 깨지지 않았는지 확인한다."""

    monkeypatch.setattr(database_module, "_wal_fallback_warning_emitted", False)
    db_path = tmp_path / "wal-success.db"
    connection = sqlite3.connect(str(db_path))
    try:
        _set_sqlite_wal_mode(connection, None)
        (mode,) = connection.execute("PRAGMA journal_mode").fetchone()
    finally:
        connection.close()
    assert str(mode).lower() == "wal"
    assert database_module._wal_fallback_warning_emitted is False


def test_set_sqlite_wal_mode_falls_back_when_wal_cannot_actually_apply(
    monkeypatch, caplog
) -> None:
    """":memory:" DB는 "PRAGMA journal_mode=WAL"을 예외 없이 받아들이지만
    실제로는 항상 "memory" 모드를 유지한다(실측 확인됨) — 예외를 던지지 않고
    조용히 이전 모드를 지키는, 이 리스너가 반드시 읽어온 값으로 판별해야 하는
    바로 그 케이스를 결정적으로 재현하는 fallback 경로 테스트다."""

    monkeypatch.setattr(database_module, "_wal_fallback_warning_emitted", False)
    connection = sqlite3.connect(":memory:")
    try:
        with caplog.at_level("WARNING", logger=database_module.logger.name):
            _set_sqlite_wal_mode(connection, None)
        (mode,) = connection.execute("PRAGMA journal_mode").fetchone()
    finally:
        connection.close()
    assert str(mode).lower() != "wal"
    assert len(caplog.records) == 1
    assert "WAL" in caplog.records[0].message


def test_set_sqlite_wal_mode_falls_back_when_the_pragma_raises(monkeypatch, caplog) -> None:
    """PRAGMA 실행 자체가 OSError/sqlite3.OperationalError를 던지는 경우도
    (예: 손상되었거나 잠긴 파일) 예외를 삼키고 같은 fallback 경로로 처리해야
    한다. `sqlite3.Connection`은 내장 메서드가 read-only 속성이라 인스턴스에
    직접 monkeypatch할 수 없으므로, `.cursor()`만 흉내 내는 얇은 dbapi
    래퍼로 WAL PRAGMA에서만 실패를 재현한다."""

    monkeypatch.setattr(database_module, "_wal_fallback_warning_emitted", False)
    real_connection = sqlite3.connect(":memory:")

    class _RaisingWalCursor:
        def __init__(self, conn):
            self._conn = conn
            self._last_cursor = None

        def execute(self, sql, *args, **kwargs):
            if "journal_mode=WAL" in sql:
                raise sqlite3.OperationalError("simulated WAL failure")
            self._last_cursor = self._conn.execute(sql, *args, **kwargs)
            return self

        def fetchone(self):
            return self._last_cursor.fetchone()

        def close(self):
            pass

    class _FakeDbapiConnection:
        def __init__(self, conn):
            self._conn = conn

        def cursor(self):
            return _RaisingWalCursor(self._conn)

    fake_connection = _FakeDbapiConnection(real_connection)
    try:
        with caplog.at_level("WARNING", logger=database_module.logger.name):
            _set_sqlite_wal_mode(fake_connection, None)
        # 리스너가 예외를 삼켰는지: 여기까지 도달했다는 것 자체가 증거다.
        _set_sqlite_busy_timeout(fake_connection, None)
        (busy_timeout,) = real_connection.execute("PRAGMA busy_timeout").fetchone()
    finally:
        real_connection.close()
    assert busy_timeout == 3000
    assert len(caplog.records) == 1
    assert "WAL" in caplog.records[0].message


def test_set_sqlite_wal_mode_warns_only_once_across_connections(monkeypatch, caplog) -> None:
    """매 연결마다 fallback이어도 로그는 한 번만 남아야 한다(스팸 방지)."""

    monkeypatch.setattr(database_module, "_wal_fallback_warning_emitted", False)
    first = sqlite3.connect(":memory:")
    second = sqlite3.connect(":memory:")
    try:
        with caplog.at_level("WARNING", logger=database_module.logger.name):
            _set_sqlite_wal_mode(first, None)
            _set_sqlite_wal_mode(second, None)
    finally:
        first.close()
        second.close()
    assert len(caplog.records) == 1


def test_set_sqlite_busy_timeout_is_unaffected_by_wal_success_or_fallback(tmp_path, monkeypatch) -> None:
    """busy_timeout PRAGMA는 WAL 성공/실패와 무관하게 항상 걸려야 한다 —
    두 리스너를 실제 엔진과 같은 순서(등록 순서)로 같은 연결에 걸어 확인한다."""

    monkeypatch.setattr(database_module, "_wal_fallback_warning_emitted", False)

    # 성공 케이스: 일반 파일 기반 연결.
    success_path = tmp_path / "wal-and-busy-timeout.db"
    success_connection = sqlite3.connect(str(success_path))
    try:
        _set_sqlite_busy_timeout(success_connection, None)
        _set_sqlite_wal_mode(success_connection, None)
        (success_mode,) = success_connection.execute("PRAGMA journal_mode").fetchone()
        (success_busy_timeout,) = success_connection.execute("PRAGMA busy_timeout").fetchone()
    finally:
        success_connection.close()
    assert str(success_mode).lower() == "wal"
    assert success_busy_timeout == 3000

    # fallback 케이스: :memory: 연결.
    fallback_connection = sqlite3.connect(":memory:")
    try:
        _set_sqlite_busy_timeout(fallback_connection, None)
        _set_sqlite_wal_mode(fallback_connection, None)
        (fallback_mode,) = fallback_connection.execute("PRAGMA journal_mode").fetchone()
        (fallback_busy_timeout,) = fallback_connection.execute("PRAGMA busy_timeout").fetchone()
    finally:
        fallback_connection.close()
    assert str(fallback_mode).lower() != "wal"
    assert fallback_busy_timeout == 3000


def test_the_real_engine_has_the_wal_mode_listener_registered_on_connect(
    original_metrics_db_engine,
) -> None:
    """`_set_sqlite_busy_timeout`용 배선 테스트와 동일한 근거로, WAL 리스너도
    실제 엔진에 올바른 이벤트 이름으로 등록됐는지를 SQLAlchemy의 이벤트
    레지스트리로 직접 확인한다."""

    assert event.contains(original_metrics_db_engine, "connect", _set_sqlite_wal_mode)
