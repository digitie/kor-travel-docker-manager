from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest

import kor_travel_docker_manager.services.standalone_backup as standalone_backup
from kor_travel_docker_manager.services.standalone_backup import (
    BACKUP_ROLES,
    StandaloneBackupError,
    create_standalone_backup,
    gc_standalone_backups,
    list_standalone_backups,
)

_CMD_JSON = json.dumps(["postgres", "-p", "12500", "-c", "listen_addresses=127.0.0.1"]).encode(
    "utf-8"
)
_ENV_OUTPUT = b"POSTGRES_USER=addr\nPOSTGRES_DB=kor_travel_geo\n"
_TOC_OUTPUT = b";\n; Archive created ...\n;\n1; 2615 SCHEMA public\n2; 1259 TABLE t\n"


def _fake_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        standalone_backup,
        "time",
        Mock(time=Mock(return_value=1000.0), monotonic=Mock(side_effect=[500.0, 500.879])),
    )


def _happy_run_checked(dest_path: Path):
    def run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
        if arguments[:2] == ["docker", "inspect"] and "Cmd" in arguments[3]:
            return _CMD_JSON
        if arguments[:2] == ["docker", "inspect"] and "Env" in arguments[3]:
            return _ENV_OUTPUT
        if arguments[:3] == ["docker", "exec", "--user"] and "pg_dump" in arguments:
            return b""
        if arguments[:2] == ["docker", "exec"] and "pg_restore" in arguments:
            return _TOC_OUTPUT
        if arguments[:2] == ["docker", "cp"]:
            dest_path.write_bytes(b"fake dump contents")
            return b""
        if "pg_database_size" in " ".join(arguments):
            return b"12345\n"
        raise AssertionError(f"unexpected _run_checked command: {arguments}")

    return run_checked


def _happy_subprocess_run():
    def run(arguments: list[str], **kwargs: object) -> Mock:
        if "alembic_version" in " ".join(arguments):
            return Mock(returncode=0, stderr=b"", stdout=b"0099_abcdef\n")
        if arguments[:2] == ["docker", "exec"] and "rm" in arguments:
            return Mock(returncode=0, stderr=b"", stdout=b"")
        raise AssertionError(f"unexpected subprocess.run command: {arguments}")

    return Mock(side_effect=run)


def test_create_standalone_backup_happy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "geo"
    dest_path = root / "geo-1000.dump"
    _fake_time(monkeypatch)
    run_checked = Mock(side_effect=_happy_run_checked(dest_path))
    monkeypatch.setattr(standalone_backup, "_run_checked", run_checked)
    subprocess_run = _happy_subprocess_run()
    monkeypatch.setattr(standalone_backup.subprocess, "run", subprocess_run)

    manifest = create_standalone_backup("geo", backup_root=root)

    # exact argument lists, not just prefix/substring matches — a flag-order or
    # value-swap bug (wrong port/db/container) must fail this test.
    pg_dump_call = next(
        call for call in run_checked.call_args_list if "pg_dump" in call.args[0]
    )
    assert pg_dump_call.args[0] == [
        "docker",
        "exec",
        "--user",
        "postgres",
        "kor-travel-geo-postgres",
        "pg_dump",
        "--username",
        "addr",
        "--port",
        "12500",
        "--dbname",
        "kor_travel_geo",
        "--format=custom",
        "--compress=6",
        "--file",
        "/tmp/geo-1000.dump",
    ]
    toc_call = next(call for call in run_checked.call_args_list if "pg_restore" in call.args[0])
    assert toc_call.args[0] == [
        "docker",
        "exec",
        "kor-travel-geo-postgres",
        "pg_restore",
        "--list",
        "/tmp/geo-1000.dump",
    ]
    cp_call = next(call for call in run_checked.call_args_list if call.args[0][:2] == ["docker", "cp"])
    assert cp_call.args[0] == [
        "docker",
        "cp",
        "kor-travel-geo-postgres:/tmp/geo-1000.dump",
        str(dest_path),
    ]

    assert manifest.role == "geo"
    assert manifest.created_at_unix == 1000
    assert manifest.duration_sec == pytest.approx(0.879)
    assert manifest.backup_filename == "geo-1000.dump"
    assert manifest.byte_size == len(b"fake dump contents")
    assert manifest.instance == "kor-travel-geo-postgres:127.0.0.1:12500/kor_travel_geo"
    assert manifest.db_size_bytes == 12345
    assert manifest.toc_entry_count == 2
    assert manifest.alembic_head == "0099_abcdef"

    dump_path = root / manifest.backup_filename
    sha256_path = root / f"{manifest.backup_filename}.sha256"
    manifest_path = root / "geo-1000.manifest"
    assert dump_path.is_file()
    assert sha256_path.is_file()
    assert manifest_path.is_file()
    assert stat.S_IMODE(dump_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(sha256_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700

    assert sha256_path.read_text(encoding="ascii") == f"{manifest.sha256}  geo-1000.dump\n"
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved == manifest.to_json()

    cleanup_calls = [call for call in subprocess_run.call_args_list if "rm" in call.args[0]]
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0].args[0][:2] == ["docker", "exec"]


def test_create_standalone_backup_rejects_unknown_role(tmp_path: Path) -> None:
    with pytest.raises(StandaloneBackupError, match="unknown backup role"):
        create_standalone_backup("unknown", backup_root=tmp_path)  # type: ignore[arg-type]


def test_create_standalone_backup_rejects_empty_dump_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "pinvi"

    def run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
        if arguments[:2] == ["docker", "inspect"] and "Cmd" in arguments[3]:
            return json.dumps(["postgres", "-p", "12800"]).encode("utf-8")
        if arguments[:2] == ["docker", "inspect"] and "Env" in arguments[3]:
            return b"POSTGRES_USER=pinvi\n"
        if "pg_dump" in arguments:
            return b""
        if "pg_restore" in arguments:
            return _TOC_OUTPUT
        if arguments[:2] == ["docker", "cp"]:
            (root / "pinvi-1000.dump").write_bytes(b"")
            return b""
        raise AssertionError(f"unexpected command: {arguments}")

    monkeypatch.setattr(standalone_backup, "_run_checked", Mock(side_effect=run_checked))
    _fake_time(monkeypatch)
    monkeypatch.setattr(
        standalone_backup.subprocess, "run", Mock(return_value=Mock(returncode=0, stderr=b""))
    )

    with pytest.raises(StandaloneBackupError, match="empty file"):
        create_standalone_backup("pinvi", backup_root=root)
    assert not (root / "pinvi-1000.dump").exists()


def test_create_standalone_backup_attempts_container_cleanup_even_on_copy_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "map_application"

    def run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
        if arguments[:2] == ["docker", "inspect"] and "Cmd" in arguments[3]:
            return json.dumps(["postgres", "-p", "12700"]).encode("utf-8")
        if arguments[:2] == ["docker", "inspect"] and "Env" in arguments[3]:
            return b"POSTGRES_USER=kor_travel_map\n"
        if "pg_dump" in arguments:
            return b""
        if "pg_restore" in arguments:
            return _TOC_OUTPUT
        if arguments[:2] == ["docker", "cp"]:
            raise StandaloneBackupError("copy-out failed")
        raise AssertionError(f"unexpected command: {arguments}")

    monkeypatch.setattr(standalone_backup, "_run_checked", Mock(side_effect=run_checked))
    _fake_time(monkeypatch)
    cleanup = Mock(return_value=Mock(returncode=0, stderr=b""))
    monkeypatch.setattr(standalone_backup.subprocess, "run", cleanup)

    with pytest.raises(StandaloneBackupError, match="copy-out failed"):
        create_standalone_backup("map_application", backup_root=root)
    cleanup.assert_called_once()


def test_discover_port_parses_dash_p_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        standalone_backup,
        "_run_checked",
        Mock(return_value=json.dumps(["postgres", "-p", "12600", "-c", "x=1"]).encode()),
    )
    assert standalone_backup._discover_port("kor-travel-concierge-postgres") == 12600


def test_discover_port_rejects_missing_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        standalone_backup, "_run_checked", Mock(return_value=json.dumps(["postgres"]).encode())
    )
    with pytest.raises(StandaloneBackupError, match="does not declare an explicit -p port"):
        standalone_backup._discover_port("kor-travel-concierge-postgres")


def test_discover_admin_role_reads_postgres_user_only(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Mock(return_value=b"POSTGRES_PASSWORD_FILE=/run/secrets/x\nPOSTGRES_USER=addr\n")
    monkeypatch.setattr(standalone_backup, "_run_checked", runner)

    assert standalone_backup._discover_admin_role("kor-travel-geo-postgres") == "addr"
    command = runner.call_args.args[0]
    assert "POSTGRES_PASSWORD" not in " ".join(command)


@pytest.mark.parametrize(
    "output", [b"", b"POSTGRES_USER=addr\nPOSTGRES_USER=other\n", b"POSTGRES_USER=bad-name\n"]
)
def test_discover_admin_role_rejects_missing_or_ambiguous_user(
    monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    monkeypatch.setattr(standalone_backup, "_run_checked", Mock(return_value=output))
    with pytest.raises(StandaloneBackupError, match="POSTGRES_USER"):
        standalone_backup._discover_admin_role("kor-travel-geo-postgres")


def test_discover_alembic_head_falls_back_to_second_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(arguments: list[str], **kwargs: object) -> Mock:
        if '"public"."alembic_version"' in " ".join(arguments):
            return Mock(returncode=1, stderr=b"relation does not exist", stdout=b"")
        if '"app"."alembic_version"' in " ".join(arguments):
            return Mock(returncode=0, stderr=b"", stdout=b"0007_pinvi_head\n")
        raise AssertionError(arguments)

    monkeypatch.setattr(standalone_backup.subprocess, "run", Mock(side_effect=run))

    head = standalone_backup._discover_alembic_head("pinvi-postgres", 12800, "pinvi", "pinvi")

    assert head == "0007_pinvi_head"


def test_discover_alembic_head_returns_none_when_no_schema_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        standalone_backup.subprocess,
        "run",
        Mock(return_value=Mock(returncode=1, stderr=b"nope", stdout=b"")),
    )

    head = standalone_backup._discover_alembic_head(
        "kor-travel-concierge-postgres", 12600, "addr", "kor_travel_concierge"
    )

    assert head is None


def test_list_standalone_backups_sorted_by_created_at(tmp_path: Path) -> None:
    root = tmp_path / "pinvi"
    root.mkdir()
    for created_at, name in [(2000, "pinvi-2000.dump"), (1000, "pinvi-1000.dump")]:
        (root / name.replace(".dump", ".manifest")).write_text(
            json.dumps(_manifest_payload("pinvi", created_at, name)),
            encoding="utf-8",
        )

    manifests = list_standalone_backups("pinvi", backup_root=root)

    assert [m.created_at_unix for m in manifests] == [1000, 2000]


def test_list_standalone_backups_empty_when_root_missing(tmp_path: Path) -> None:
    assert list_standalone_backups("geo", backup_root=tmp_path / "does-not-exist") == []


def test_list_standalone_backups_rejects_malformed_manifest(tmp_path: Path) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    (root / "geo-1.manifest").write_text("{}", encoding="utf-8")
    with pytest.raises(StandaloneBackupError, match="malformed"):
        list_standalone_backups("geo", backup_root=root)


def test_gc_standalone_backups_keeps_newest_and_deletes_rest(tmp_path: Path) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    for created_at in (1000, 2000, 3000):
        name = f"geo-{created_at}.dump"
        (root / name).write_bytes(b"x")
        (root / f"{name}.sha256").write_text("deadbeef  " + name, encoding="ascii")
        (root / name.replace(".dump", ".manifest")).write_text(
            json.dumps(_manifest_payload("geo", created_at, name)), encoding="utf-8"
        )

    deleted = gc_standalone_backups("geo", keep=1, backup_root=root)

    assert deleted == ["geo-1000.dump", "geo-2000.dump"]
    remaining = {p.name for p in root.iterdir()}
    assert remaining == {
        "geo-3000.dump",
        "geo-3000.dump.sha256",
        "geo-3000.manifest",
    }


def test_gc_standalone_backups_noop_when_within_keep(tmp_path: Path) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    name = "geo-1000.dump"
    (root / name).write_bytes(b"x")
    (root / name.replace(".dump", ".manifest")).write_text(
        json.dumps(_manifest_payload("geo", 1000, name)), encoding="utf-8"
    )

    assert gc_standalone_backups("geo", keep=5, backup_root=root) == []


def test_gc_standalone_backups_keeps_all_when_keep_equals_count(tmp_path: Path) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    for created_at in (1000, 2000, 3000):
        name = f"geo-{created_at}.dump"
        (root / name).write_bytes(b"x")
        (root / name.replace(".dump", ".manifest")).write_text(
            json.dumps(_manifest_payload("geo", created_at, name)), encoding="utf-8"
        )

    assert gc_standalone_backups("geo", keep=3, backup_root=root) == []
    assert {p.stem for p in root.glob("*.manifest")} == {"geo-1000", "geo-2000", "geo-3000"}


def test_gc_standalone_backups_rejects_keep_below_one(tmp_path: Path) -> None:
    with pytest.raises(StandaloneBackupError, match="keep must be at least 1"):
        gc_standalone_backups("geo", keep=0, backup_root=tmp_path)


def test_discover_port_rejects_invalid_container_name(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Mock()
    monkeypatch.setattr(standalone_backup, "_run_checked", runner)
    with pytest.raises(StandaloneBackupError, match="container name is invalid"):
        standalone_backup._discover_port("../etc/passwd")
    runner.assert_not_called()


def test_discover_admin_role_rejects_invalid_container_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock()
    monkeypatch.setattr(standalone_backup, "_run_checked", runner)
    with pytest.raises(StandaloneBackupError, match="container name is invalid"):
        standalone_backup._discover_admin_role("$(rm -rf /)")
    runner.assert_not_called()


def test_query_db_size_rejects_invalid_database_name() -> None:
    with pytest.raises(StandaloneBackupError, match="database name is invalid"):
        standalone_backup._query_db_size("kor-travel-geo-postgres", 12500, "addr", "'; DROP")


def test_query_db_size_parses_digit_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(standalone_backup, "_run_checked", Mock(return_value=b"98765\n"))
    assert (
        standalone_backup._query_db_size("kor-travel-geo-postgres", 12500, "addr", "kor_travel_geo")
        == 98765
    )


def test_role_lock_rejects_concurrent_acquisition(tmp_path: Path) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    with standalone_backup._role_lock(root):
        with pytest.raises(StandaloneBackupError, match="already running"):
            with standalone_backup._role_lock(root):
                pass  # pragma: no cover - must not be reached


def test_role_lock_releases_after_context_exits(tmp_path: Path) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    with standalone_backup._role_lock(root):
        pass
    with standalone_backup._role_lock(root):
        pass  # second acquisition succeeds once the first has released


@pytest.mark.parametrize(
    ("role", "env_var", "expected"),
    [
        ("concierge", "KOR_TRAVEL_CONCIERGE_POSTGRES_CONTAINER", "concierge-override"),
        ("map_application", "KOR_TRAVEL_MAP_POSTGRES_CONTAINER", "map-override"),
        ("pinvi", "PINVI_POSTGRES_CONTAINER", "pinvi-override"),
    ],
)
def test_role_config_respects_container_name_override(
    monkeypatch: pytest.MonkeyPatch, role: str, env_var: str, expected: str
) -> None:
    monkeypatch.setenv(env_var, expected)
    container_name, _ = standalone_backup._role_config(role)
    assert container_name == expected


def test_role_config_geo_ignores_env_since_compose_hardcodes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KOR_TRAVEL_GEO_POSTGRES_CONTAINER", "should-be-ignored")
    container_name, _ = standalone_backup._role_config("geo")
    assert container_name == "kor-travel-geo-postgres"


def test_backup_roles_cover_four_instances() -> None:
    assert set(BACKUP_ROLES) == {
        "geo",
        "geo_dagster",
        "concierge",
        "map_application",
        "map_dagster",
        "pinvi",
    }


def _manifest_payload(role: str, created_at: int, backup_filename: str) -> dict[str, object]:
    return {
        "role": role,
        "created_at_unix": created_at,
        "duration_sec": 1.0,
        "byte_size": 10,
        "sha256": "a" * 64,
        "backup_filename": backup_filename,
        "instance": "container:127.0.0.1:12345/db",
        "db_size_bytes": 100,
        "toc_entry_count": 2,
        "alembic_head": "0001_head",
    }
