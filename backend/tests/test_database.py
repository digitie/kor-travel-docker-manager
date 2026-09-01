"""GM-14: SQLite 엔진에 busy_timeout PRAGMA가 실제로 걸리는지 확인한다.

`test_metrics.py`가 모듈 레벨에서 `kor_travel_docker_manager.database.engine`을
테스트용 엔진으로 통째로 바꿔치기하므로(테스트 격리 목적), 그 시점 이후에는
이 모듈의 `engine` 이름이 더 이상 원본을 가리키지 않는다 — 어느 테스트 파일이
먼저 수집되는지에 의존하는 단언은 만들지 않는다. 대신 connect 리스너
함수(`_set_sqlite_busy_timeout`) 자체를 raw sqlite3 커넥션에 직접 호출해
검증한다 — SQLAlchemy가 실제로 이 함수를 어떤 엔진에 걸든 동작이 같다는 것만
증명하면 충분하다.

WAL은 개발 환경(drvfs/9p)에서 shm/mmap이 실패할 수 있어 별도 검증 없이는
켜지 않았다(docs/tasks.md 후속 항목) — 여기서는 무조건 안전한 busy_timeout만
검증한다."""

from __future__ import annotations

import sqlite3

from kor_travel_docker_manager.database import _set_sqlite_busy_timeout


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
