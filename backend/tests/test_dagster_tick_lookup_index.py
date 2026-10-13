"""공용 Dagster 최신 tick 인덱스의 재진입·실패·이름 충돌 계약."""

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
DEFINITION = 'CREATE INDEX ktdm_job_ticks_latest_timestamp ON public.job_ticks USING btree ("timestamp" DESC)'


def setup_engine(monkeypatch, rows):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    def execute(sql):
        result = MagicMock()
        if sql.startswith("SELECT indisvalid"):
            result.first.return_value = rows.pop(0)
        return result
    connection.exec_driver_sql.side_effect = execute
    factory = MagicMock(return_value=engine)
    monkeypatch.setitem(sys.modules, "sqlalchemy", SimpleNamespace(create_engine=factory))
    return engine, connection, factory


def test_new_index_is_concurrent_and_connection_is_bounded(monkeypatch):
    engine, connection, factory = setup_engine(monkeypatch, [None, (True, True, DEFINITION)])
    MODULE.ensure_tick_lookup_index("postgresql://fixture")
    factory.assert_called_once_with("postgresql://fixture", isolation_level="AUTOCOMMIT")
    commands = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert commands[:2] == ["SET lock_timeout = '5s'", "SET statement_timeout = '120s'"]
    assert len([sql for sql in commands if sql.startswith("CREATE INDEX CONCURRENTLY")]) == 1
    assert not any(sql.startswith("DROP") for sql in commands)
    engine.dispose.assert_called_once()


def test_valid_existing_index_is_idempotent(monkeypatch):
    engine, connection, _ = setup_engine(monkeypatch, [(True, True, DEFINITION)])
    MODULE.ensure_tick_lookup_index("postgresql://fixture")
    assert not any(call.args[0].startswith(("CREATE", "DROP")) for call in connection.exec_driver_sql.call_args_list)
    engine.dispose.assert_called_once()


def test_interrupted_owned_index_is_repaired(monkeypatch):
    _, connection, _ = setup_engine(monkeypatch, [(False, False, DEFINITION), (True, True, DEFINITION)])
    MODULE.ensure_tick_lookup_index("postgresql://fixture")
    commands = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    drop = commands.index("DROP INDEX CONCURRENTLY public.ktdm_job_ticks_latest_timestamp")
    create = next(i for i, sql in enumerate(commands) if sql.startswith("CREATE INDEX CONCURRENTLY"))
    assert drop < create


@pytest.mark.parametrize("valid", [False, True])
def test_foreign_definition_is_never_replaced(monkeypatch, valid):
    engine, connection, _ = setup_engine(monkeypatch, [(valid, valid, "CREATE INDEX other ON public.runs USING btree (id)")])
    with pytest.raises(RuntimeError, match="이름 충돌"):
        MODULE.ensure_tick_lookup_index("postgresql://fixture")
    assert not any(call.args[0].startswith(("CREATE", "DROP")) for call in connection.exec_driver_sql.call_args_list)
    engine.dispose.assert_called_once()


def test_incomplete_creation_is_failure(monkeypatch):
    engine, _, _ = setup_engine(monkeypatch, [None, (False, False, DEFINITION)])
    with pytest.raises(RuntimeError, match="미완료"):
        MODULE.ensure_tick_lookup_index("postgresql://fixture")
    engine.dispose.assert_called_once()


def test_sql_error_does_not_leak_engine_or_silently_succeed(monkeypatch):
    engine, connection, _ = setup_engine(monkeypatch, [])
    connection.exec_driver_sql.side_effect = RuntimeError("fixture failure")
    with pytest.raises(RuntimeError):
        MODULE.ensure_tick_lookup_index("postgresql://fixture")
    engine.dispose.assert_called_once()


def test_url_comes_from_declared_storage_env(monkeypatch):
    monkeypatch.setenv("TEST_DAGSTER_STORAGE", "postgresql://fixture")
    assert MODULE._postgres_url({"postgres_url": {"env": "TEST_DAGSTER_STORAGE"}}) == "postgresql://fixture"
    with pytest.raises(MODULE.TickIndexError):
        MODULE._postgres_url({"postgres_url": {"env": "MISSING_TICK_STORAGE"}})


def test_partial_index_with_same_prefix_is_not_accepted(monkeypatch):
    engine, connection, _ = setup_engine(monkeypatch, [(True, True, DEFINITION + " WHERE selector_id IS NOT NULL")])
    with pytest.raises(MODULE.TickIndexError, match="이름 충돌"):
        MODULE.ensure_tick_lookup_index("postgresql://fixture")
    assert not any(call.args[0].startswith(("CREATE", "DROP")) for call in connection.exec_driver_sql.call_args_list)
    engine.dispose.assert_called_once()
