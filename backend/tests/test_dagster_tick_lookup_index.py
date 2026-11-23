"""공용 Dagster tick 인덱스(`TICK_INDEXES`)의 재진입·실패·이름 충돌 계약."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("tick_storage_migrate", ROOT / "docker/dagster-host/storage-migrate.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DEFINITIONS = {
    name: f"CREATE INDEX {name} ON public.job_ticks USING btree ({columns})"
    for name, columns in MODULE.TICK_INDEXES
}
LATEST = "ktdm_job_ticks_latest_timestamp"
PURGE = "ktdm_job_ticks_selector_status_timestamp"


def valid(name):
    return (True, True, DEFINITIONS[name])


def setup_engine(monkeypatch, rows):
    """`rows`는 인덱스 이름별 조회 결과 목록이다(조회할 때마다 앞에서 하나씩 꺼낸다)."""
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    def execute(sql):
        result = MagicMock()
        if sql.startswith("SELECT indisvalid"):
            name = next(n for n in rows if f"'public.{n}'" in sql)
            result.first.return_value = rows[name].pop(0)
        return result
    connection.exec_driver_sql.side_effect = execute
    factory = MagicMock(return_value=engine)
    monkeypatch.setitem(sys.modules, "sqlalchemy", SimpleNamespace(create_engine=factory))
    return engine, connection, factory


def commands(connection):
    return [call.args[0] for call in connection.exec_driver_sql.call_args_list]


def test_the_purge_index_leads_with_selector_then_status_then_time():
    """Dagster 정리 DELETE의 등호 두 열(selector_id, status) 뒤에 범위 열(timestamp)이 와야 지울 행만 읽는다."""
    assert dict(MODULE.TICK_INDEXES)[PURGE] == 'selector_id, status, "timestamp"'
    assert dict(MODULE.TICK_INDEXES)[LATEST] == '"timestamp" DESC'


def test_new_indexes_are_concurrent_and_connection_is_bounded(monkeypatch):
    engine, connection, factory = setup_engine(
        monkeypatch, {LATEST: [None, valid(LATEST)], PURGE: [None, valid(PURGE)]}
    )
    MODULE.ensure_tick_indexes("postgresql://fixture")
    factory.assert_called_once_with(
        "postgresql://fixture", isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5}
    )
    sql = commands(connection)
    assert sql[:2] == ["SET lock_timeout = '60s'", "SET statement_timeout = '600s'"]
    creates = [s for s in sql if s.startswith("CREATE INDEX CONCURRENTLY")]
    assert creates == [
        f"CREATE INDEX CONCURRENTLY {name} ON public.job_ticks ({columns})"
        for name, columns in MODULE.TICK_INDEXES
    ]
    assert not any(s.startswith("DROP") for s in sql)
    engine.dispose.assert_called_once()


def test_an_existing_latest_index_only_adds_the_purge_index(monkeypatch):
    """운영 상태(최신 조회 인덱스만 있음)에서는 정리 인덱스 하나만 만든다."""
    _, connection, _ = setup_engine(monkeypatch, {LATEST: [valid(LATEST)], PURGE: [None, valid(PURGE)]})
    MODULE.ensure_tick_indexes("postgresql://fixture")
    creates = [s for s in commands(connection) if s.startswith("CREATE")]
    assert creates == [f'CREATE INDEX CONCURRENTLY {PURGE} ON public.job_ticks (selector_id, status, "timestamp")']


def test_valid_existing_indexes_are_idempotent(monkeypatch):
    engine, connection, _ = setup_engine(monkeypatch, {LATEST: [valid(LATEST)], PURGE: [valid(PURGE)]})
    MODULE.ensure_tick_indexes("postgresql://fixture")
    assert not any(s.startswith(("CREATE", "DROP")) for s in commands(connection))
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("name", [LATEST, PURGE])
def test_interrupted_owned_index_is_repaired(monkeypatch, name):
    rows = {n: [valid(n)] for n in DEFINITIONS}
    rows[name] = [(False, False, DEFINITIONS[name]), valid(name)]
    _, connection, _ = setup_engine(monkeypatch, rows)
    MODULE.ensure_tick_indexes("postgresql://fixture")
    sql = commands(connection)
    drop = sql.index(f"DROP INDEX CONCURRENTLY public.{name}")
    create = next(i for i, s in enumerate(sql) if s.startswith(f"CREATE INDEX CONCURRENTLY {name} "))
    assert drop < create
    assert len([s for s in sql if s.startswith(("CREATE", "DROP"))]) == 2


@pytest.mark.parametrize("name", [LATEST, PURGE])
@pytest.mark.parametrize("is_valid", [False, True])
def test_foreign_definition_is_never_replaced(monkeypatch, name, is_valid):
    rows = {n: [valid(n)] for n in DEFINITIONS}
    rows[name] = [(is_valid, is_valid, "CREATE INDEX other ON public.runs USING btree (id)")]
    engine, connection, _ = setup_engine(monkeypatch, rows)
    with pytest.raises(RuntimeError, match="이름 충돌"):
        MODULE.ensure_tick_indexes("postgresql://fixture")
    assert not any(s.startswith(("CREATE", "DROP")) for s in commands(connection))
    engine.dispose.assert_called_once()


def test_incomplete_creation_is_failure(monkeypatch):
    engine, _, _ = setup_engine(
        monkeypatch, {LATEST: [valid(LATEST)], PURGE: [None, (False, False, DEFINITIONS[PURGE])]}
    )
    with pytest.raises(RuntimeError, match="미완료"):
        MODULE.ensure_tick_indexes("postgresql://fixture")
    engine.dispose.assert_called_once()


def test_sql_error_does_not_leak_engine_or_silently_succeed(monkeypatch):
    engine, connection, _ = setup_engine(monkeypatch, {})
    connection.exec_driver_sql.side_effect = RuntimeError("fixture failure")
    with pytest.raises(RuntimeError):
        MODULE.ensure_tick_indexes("postgresql://fixture")
    engine.dispose.assert_called_once()


def test_url_comes_from_declared_storage_env(monkeypatch):
    monkeypatch.setenv("TEST_DAGSTER_STORAGE", "postgresql://fixture")
    assert MODULE._postgres_url({"postgres_url": {"env": "TEST_DAGSTER_STORAGE"}}) == "postgresql://fixture"
    with pytest.raises(MODULE.TickIndexError):
        MODULE._postgres_url({"postgres_url": {"env": "MISSING_TICK_STORAGE"}})


@pytest.mark.parametrize("name", [LATEST, PURGE])
def test_partial_index_with_same_prefix_is_not_accepted(monkeypatch, name):
    rows = {n: [valid(n)] for n in DEFINITIONS}
    rows[name] = [(True, True, DEFINITIONS[name] + " WHERE selector_id IS NOT NULL")]
    engine, connection, _ = setup_engine(monkeypatch, rows)
    with pytest.raises(MODULE.TickIndexError, match="이름 충돌"):
        MODULE.ensure_tick_indexes("postgresql://fixture")
    assert not any(s.startswith(("CREATE", "DROP")) for s in commands(connection))
    engine.dispose.assert_called_once()
