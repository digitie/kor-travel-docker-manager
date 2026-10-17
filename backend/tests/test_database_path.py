"""DB/설정 모듈을 import하거나 engine을 만들지 않는 경로 선택 회귀."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

_SOURCE = Path(__file__).resolve().parents[1] / "src/kor_travel_docker_manager/database.py"


def load_path_code(origin: str):
    source = ast.parse(_SOURCE.read_text())
    names = {"current_dir", "DB_PATH", "DATABASE_URL"}
    nodes = [
        node for node in source.body
        if (
            isinstance(node, ast.FunctionDef) and node.name == "get_database_path"
        ) or (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)
        )
    ]
    namespace = {"os": os, "__file__": origin}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(_SOURCE), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("origin", [
    "/opt/kor-travel-docker-manager/backend/src/kor_travel_docker_manager/database.py",
    "/srv/releases/ktdm-release-fixture/backend/src/kor_travel_docker_manager/database.py",
    "/srv/releases/ktdm-release-fixture/backend/.venv/lib/python3.14/site-packages/kor_travel_docker_manager/database.py",
])
def test_unset_retains_exact_legacy_path(monkeypatch, origin):
    monkeypatch.delenv("KTDM_DATABASE_PATH", raising=False)
    namespace = load_path_code(origin)
    legacy = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(origin)),
                                         "../../../../", "pinvi_metrics.db"))
    assert namespace["DB_PATH"] == legacy
    assert namespace["DATABASE_URL"] == f"sqlite:///{legacy}"


def test_explicit_path_overrides_import_origin_without_creating_file(tmp_path, monkeypatch):
    database = tmp_path / "uncreated" / "상태 데이터.db"
    monkeypatch.setenv("KTDM_DATABASE_PATH", str(database))
    for origin in ("/opt/app/database.py", "/srv/release/.venv/site-packages/database.py"):
        namespace = load_path_code(origin)
        assert namespace["DB_PATH"] == str(database)
        assert namespace["DATABASE_URL"] == f"sqlite:///{database}"
    assert not database.exists()
    assert not database.parent.exists()


@pytest.mark.parametrize("configured", ["", " ", "pinvi_metrics.db", "../state.db",
                                      "sqlite:///state.db", "/", "/tmp/", "/tmp/.", "/tmp/..",
                                      "C:\\\\state.db"])
def test_invalid_explicit_value_fails_without_value_in_error(configured, monkeypatch):
    monkeypatch.setenv("KTDM_DATABASE_PATH", configured)
    with pytest.raises(ValueError) as error:
        load_path_code("/opt/app/database.py")
    assert str(error.value) == "KTDM_DATABASE_PATH must be a nonempty absolute database file path"


def test_nul_value_rejected_in_pure_environment_mapping(monkeypatch):
    # 실제 OS는 NUL env 자체를 거절한다. 함수 계약도 대역 mapping으로 확인한다.
    with monkeypatch.context() as context:
        context.setattr(os, "environ", {"KTDM_DATABASE_PATH": "/fixture/invalid" + chr(0) + ".db"})
        with pytest.raises(ValueError):
            load_path_code("/opt/app/database.py")


def test_dotenv_file_alone_is_not_an_early_import_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("KTDM_DATABASE_PATH", raising=False)
    (tmp_path / ".env").write_text("KTDM_DATABASE_PATH=/fixture/env-only.db" + chr(10))
    monkeypatch.chdir(tmp_path)
    namespace = load_path_code("/opt/kor-travel-docker-manager/backend/src/kor_travel_docker_manager/database.py")
    assert namespace["DB_PATH"] == "/opt/pinvi_metrics.db"


def test_actual_engine_setup_uses_selected_url(monkeypatch, tmp_path):
    database = str(tmp_path / "metrics.db")
    monkeypatch.setenv("KTDM_DATABASE_PATH", database)
    namespace = load_path_code("/opt/app/database.py")
    engine_assignment = next(
        node for node in ast.parse(_SOURCE.read_text()).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "engine" for target in node.targets)
    )
    calls = []

    def create_engine(url, **kwargs):
        calls.append((url, kwargs))
        return object()

    namespace["create_engine"] = create_engine
    exec(compile(ast.Module(body=[engine_assignment], type_ignores=[]), str(_SOURCE), "exec"),
         namespace)
    assert calls == [(f"sqlite:///{database}", {"connect_args": {"check_same_thread": False}})]
    assert not Path(database).exists()
