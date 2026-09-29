from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest

import kor_travel_docker_manager.services.standalone_backup as standalone_backup
from kor_travel_docker_manager.services.standalone_backup import (
    BACKUP_ROLES,
    StandaloneBackupError,
    StandaloneBackupInProgressError,
    create_standalone_backup,
    gc_standalone_backups,
    list_standalone_backups,
    list_standalone_backups_for_display,
    plan_standalone_restore,
    rehearse_standalone_restore,
)

_CMD_JSON = json.dumps(["postgres", "-p", "11000", "-c", "listen_addresses=127.0.0.1"]).encode(
    "utf-8"
)
_ENV_OUTPUT = b"POSTGRES_USER=shared_admin\nPOSTGRES_DB=postgres\n"
_TOC_OUTPUT = b";\n; Archive created ...\n;\n1; 2615 SCHEMA public\n2; 1259 TABLE t\n"
#: 공용 instance의 D4 이전 `max_wal_size`(1GB). 예약분이 하한(2 GiB) 그대로인 값이다.
_MAX_WAL_OUTPUT = b"1073741824\n"


@pytest.fixture(autouse=True)
def _ample_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """디스크 여유 가드는 아래 전용 테스트가 본다. 나머지 테스트가 이 머신의 실제 여유
    (CI runner의 `/tmp`는 우리가 정하지 않는다)에 기대지 않도록 넉넉한 값을 준다."""

    monkeypatch.setattr(shutil, "disk_usage", Mock(return_value=Mock(free=1 << 50)))


def _fake_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        standalone_backup,
        "time",
        Mock(time=Mock(return_value=1000.0), monotonic=Mock(side_effect=[500.0, 500.879])),
    )


def _happy_run_checked(max_wal_bytes: int = int(_MAX_WAL_OUTPUT)):
    def run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
        if arguments[:2] == ["docker", "inspect"] and "Cmd" in arguments[3]:
            return _CMD_JSON
        if arguments[:2] == ["docker", "inspect"] and "Env" in arguments[3]:
            return _ENV_OUTPUT
        if "pg_stat_activity" in " ".join(arguments):
            return b"0\n"
        if arguments[:3] == ["docker", "exec", "--user"] and "pg_dump" in arguments:
            return b""
        if arguments[:2] == ["docker", "exec"] and "pg_restore" in arguments:
            return _TOC_OUTPUT
        if arguments[:2] == ["docker", "cp"]:
            Path(arguments[-1]).write_bytes(b"fake dump contents")
            return b""
        if "pg_database_size" in " ".join(arguments):
            return b"12345\n"
        if "pg_table_size" in " ".join(arguments):
            return b"6789\n"
        # 예약분의 재료 — 추정 방식(`--expected-dump-bytes` 여부)과 상관없이 한 번 읽는다.
        if "max_wal_size" in " ".join(arguments):
            return f"{max_wal_bytes}\n".encode("ascii")
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
    _fake_time(monkeypatch)
    run_checked = Mock(side_effect=_happy_run_checked())
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
        "kor-travel-shared-postgres",
        "pg_dump",
        "--username",
        "shared_admin",
        "--port",
        "11000",
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
        "kor-travel-shared-postgres",
        "pg_restore",
        "--list",
        "/tmp/geo-1000.dump",
    ]
    cp_call = next(call for call in run_checked.call_args_list if call.args[0][:2] == ["docker", "cp"])
    assert cp_call.args[0] == [
        "docker",
        "cp",
        "kor-travel-shared-postgres:/tmp/geo-1000.dump",
        str(root / ".geo-1000.dump.copying"),
    ]

    assert manifest.role == "geo"
    assert manifest.created_at_unix == 1000
    assert manifest.duration_sec == pytest.approx(0.879)
    assert manifest.backup_filename == "geo-1000.dump"
    assert manifest.byte_size == len(b"fake dump contents")
    assert manifest.instance == "kor-travel-shared-postgres:127.0.0.1:11000/kor_travel_geo"
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
        if "pg_stat_activity" in " ".join(arguments):
            return b"0\n"
        if "max_wal_size" in " ".join(arguments):
            return _MAX_WAL_OUTPUT
        if "pg_database_size" in " ".join(arguments):
            return b"12345\n"
        if "pg_table_size" in " ".join(arguments):
            return b"6789\n"
        if "pg_dump" in arguments:
            return b""
        if "pg_restore" in arguments:
            return _TOC_OUTPUT
        if arguments[:2] == ["docker", "cp"]:
            Path(arguments[-1]).write_bytes(b"")
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
        if "pg_stat_activity" in " ".join(arguments):
            return b"0\n"
        if "max_wal_size" in " ".join(arguments):
            return _MAX_WAL_OUTPUT
        if "pg_database_size" in " ".join(arguments):
            return b"12345\n"
        if "pg_table_size" in " ".join(arguments):
            return b"6789\n"
        if "pg_dump" in arguments:
            return b""
        if "pg_restore" in arguments:
            return _TOC_OUTPUT
        if arguments[:2] == ["docker", "cp"]:
            Path(arguments[-1]).write_bytes(b"partial dump")
            raise StandaloneBackupError("copy-out failed")
        raise AssertionError(f"unexpected command: {arguments}")

    monkeypatch.setattr(standalone_backup, "_run_checked", Mock(side_effect=run_checked))
    _fake_time(monkeypatch)
    cleanup = Mock(return_value=Mock(returncode=0, stderr=b""))
    monkeypatch.setattr(standalone_backup.subprocess, "run", cleanup)

    with pytest.raises(StandaloneBackupError, match="copy-out failed"):
        create_standalone_backup("map_application", backup_root=root)
    cleanup.assert_called_once()
    assert not (root / ".map_application-1000.dump.copying").exists()
    assert not (root / "map_application-1000.dump").exists()


def test_create_standalone_backup_refuses_when_a_pg_dump_is_already_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GM-13: role lock은 프로세스 재기동에서 살아남지 못한다 — 재기동 직후 같은
    role을 다시 시작했을 때 컨테이너 안에 이전 pg_dump가 여전히 돌고 있으면
    (pg_stat_activity로 확인) 새 pg_dump를 시작하지 않고 즉시 거부해야 한다."""

    root = tmp_path / "geo"

    def run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
        if arguments[:2] == ["docker", "inspect"] and "Cmd" in arguments[3]:
            return _CMD_JSON
        if arguments[:2] == ["docker", "inspect"] and "Env" in arguments[3]:
            return _ENV_OUTPUT
        if "pg_stat_activity" in " ".join(arguments):
            return b"1\n"
        raise AssertionError(f"unexpected command after in-progress guard: {arguments}")

    monkeypatch.setattr(standalone_backup, "_run_checked", Mock(side_effect=run_checked))
    _fake_time(monkeypatch)
    subprocess_run = Mock(side_effect=AssertionError("pg_dump must not start"))
    monkeypatch.setattr(standalone_backup.subprocess, "run", subprocess_run)

    with pytest.raises(StandaloneBackupInProgressError, match="already running"):
        create_standalone_backup("geo", backup_root=root)
    subprocess_run.assert_not_called()
    assert not (root / "geo-1000.dump").exists()
    assert not (root / "geo-1000.manifest").exists()


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


def test_list_standalone_backups_for_display_degrades_a_single_corrupt_manifest(
    tmp_path: Path,
) -> None:
    """GM-13: manifest 하나가 손상돼도(여기서는 role 불일치) 나머지 정상 manifest는
    여전히 보이고, 손상된 것은 예외 대신 {"state": "unreadable", ...} 행이 된다."""

    root = tmp_path / "geo"
    root.mkdir()
    (root / "geo-1000.manifest").write_text(
        json.dumps(_manifest_payload("geo", 1000, "geo-1000.dump")), encoding="utf-8"
    )
    # role 불일치 — map 세트가 geo 디렉터리에 잘못 복사된 것과 같은 실제 사고를 재현.
    (root / "geo-999.manifest").write_text(
        json.dumps(_manifest_payload("map_application", 999, "geo-999.dump")),
        encoding="utf-8",
    )

    rows = list_standalone_backups_for_display("geo", backup_root=root)

    assert len(rows) == 2
    assert rows[0]["backup_filename"] == "geo-1000.dump"
    assert rows[1]["state"] == "unreadable"
    assert rows[1]["filename"] == "geo-999.manifest"
    assert "role does not match" in rows[1]["reason"]


def test_list_standalone_backups_for_display_empty_when_root_missing(tmp_path: Path) -> None:
    assert (
        list_standalone_backups_for_display("geo", backup_root=tmp_path / "does-not-exist")
        == []
    )


def test_list_standalone_backups_for_display_all_corrupt_returns_no_readable_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    (root / "geo-1.manifest").write_text("not json", encoding="utf-8")

    rows = list_standalone_backups_for_display("geo", backup_root=root)

    assert rows == [
        {
            "state": "unreadable",
            "filename": "geo-1.manifest",
            "reason": "manifest is unreadable: geo-1.manifest",
        }
    ]


def test_list_standalone_backups_for_display_raises_when_the_directory_itself_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """디렉터리 자체를 못 읽는 것(권한 문제 등)은 개별 manifest 손상과 성격이 다르다
    — 이건 여전히 fail-close다(라우트가 503로 옮긴다). drvfs 마운트에서는 실제
    chmod 권한 거부가 재현되지 않을 수 있어(conftest.py 참고) glob 자체를
    OSError로 monkeypatch해 결정적으로 재현한다."""

    root = tmp_path / "geo"
    root.mkdir()

    def raising_glob(self: Path, pattern: str):
        if self == root:
            raise OSError("Permission denied")
        return []

    monkeypatch.setattr(standalone_backup.Path, "glob", raising_glob)

    with pytest.raises(StandaloneBackupError, match="unreadable"):
        list_standalone_backups_for_display("geo", backup_root=root)


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

    outcome = gc_standalone_backups("geo", keep=1, backup_root=root)

    assert outcome.deleted == ("geo-1000.dump", "geo-2000.dump")
    assert outcome.orphans_removed == ()
    remaining = {p.name for p in root.iterdir()}
    assert remaining == {
        "geo-3000.dump",
        "geo-3000.dump.sha256",
        "geo-3000.manifest",
        # gc가 create와 같은 role lock을 잡으므로 lock 파일이 남는다.
        ".backup.lock",
    }


def test_gc_standalone_backups_noop_when_within_keep(tmp_path: Path) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    name = "geo-1000.dump"
    (root / name).write_bytes(b"x")
    (root / name.replace(".dump", ".manifest")).write_text(
        json.dumps(_manifest_payload("geo", 1000, name)), encoding="utf-8"
    )

    assert gc_standalone_backups("geo", keep=5, backup_root=root).total == 0


def test_gc_standalone_backups_keeps_all_when_keep_equals_count(tmp_path: Path) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    for created_at in (1000, 2000, 3000):
        name = f"geo-{created_at}.dump"
        (root / name).write_bytes(b"x")
        (root / name.replace(".dump", ".manifest")).write_text(
            json.dumps(_manifest_payload("geo", created_at, name)), encoding="utf-8"
        )

    assert gc_standalone_backups("geo", keep=3, backup_root=root).total == 0
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
        ("geo", "KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", "geo-override"),
        ("geo_dagster", "KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", "geo-dagster-override"),
        ("concierge", "KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", "concierge-override"),
        ("map_application", "KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", "map-override"),
        ("map_dagster", "KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", "map-dagster-override"),
        ("pinvi", "KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", "pinvi-override"),
        ("transport", "KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", "transport-override"),
        (
            "transport_dagster",
            "KOR_TRAVEL_SHARED_POSTGRES_CONTAINER",
            "transport-dagster-override",
        ),
        ("dagster_shared", "KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", "dagster-shared-override"),
    ],
)
def test_role_config_respects_container_name_override(
    monkeypatch: pytest.MonkeyPatch, role: str, env_var: str, expected: str
) -> None:
    monkeypatch.setenv(env_var, expected)
    container_name, _ = standalone_backup._role_config(role)
    assert container_name == expected


def test_backup_roles_cover_four_instances() -> None:
    assert set(BACKUP_ROLES) == {
        "geo",
        "geo_dagster",
        "concierge",
        "map_application",
        "map_dagster",
        "pinvi",
        "transport",
        "transport_dagster",
        "dagster_shared",
    }


def test_map_roles_resolve_to_the_shared_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-53(D7): Map 두 DB는 공용 instance에 산다 — 백업도 그 컨테이너를 뜬다.

    옛 전용 instance의 override(`KOR_TRAVEL_MAP_POSTGRES_CONTAINER`)는 더 읽지 않는다 — n150 `.env`에
    남아 있어도 퇴역한 컨테이너를 겨냥하지 않는다(이동 창이 그 줄을 지운다).
    """

    monkeypatch.delenv("KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", raising=False)
    monkeypatch.setenv("KOR_TRAVEL_MAP_POSTGRES_CONTAINER", "retired-map-postgres")

    for role, database_name in (
        ("map_application", "kor_travel_map"),
        ("map_dagster", "kor_travel_map_dagster"),
    ):
        assert standalone_backup._role_config(role) == (
            "kor-travel-shared-postgres",
            database_name,
        )


@pytest.mark.parametrize(
    ("role", "database_name"),
    [
        ("transport", "kor_travel_transport"),
        ("transport_dagster", "kor_travel_transport_dagster"),
        # ADR-53: Map 둘도 같은 자리를 **실제로** 뜬다.
        ("map_application", "kor_travel_map"),
        ("map_dagster", "kor_travel_map_dagster"),
        # 공용 Dagster instance의 metadata DB(platform-topology.md §7).
        ("dagster_shared", "dagster_shared"),
    ],
)
def test_transport_roles_dump_their_database_on_the_shared_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, role: str, database_name: str
) -> None:
    """2026-09-28 오너 결정: transport 스택의 백업을 Manager의 standalone role로 접는다.

    두 DB는 공용 instance(`kor-travel-shared-postgres`)에 있다 — compose의
    `kor-travel-shared-db-init-transport`가 그 이름을 literal로 만든다. role이 그 자리를
    **실제로** 뜨는지(pg_dump 인자와 manifest의 instance) 본다. 설정 표만 보면 오타 난
    database 이름도 초록이다.
    """

    monkeypatch.delenv("KOR_TRAVEL_SHARED_POSTGRES_CONTAINER", raising=False)
    root = tmp_path / role
    _fake_time(monkeypatch)
    run_checked = Mock(side_effect=_happy_run_checked())
    monkeypatch.setattr(standalone_backup, "_run_checked", run_checked)
    monkeypatch.setattr(standalone_backup.subprocess, "run", _happy_subprocess_run())

    manifest = create_standalone_backup(role, backup_root=root)  # type: ignore[arg-type]

    commands = [call.args[0] for call in run_checked.call_args_list]
    pg_dump_index = next(index for index, command in enumerate(commands) if "pg_dump" in command)
    pg_dump_command = commands[pg_dump_index]
    assert pg_dump_command[4] == "kor-travel-shared-postgres"
    assert pg_dump_command[pg_dump_command.index("--dbname") + 1] == database_name
    assert manifest.instance == f"kor-travel-shared-postgres:127.0.0.1:11000/{database_name}"
    assert (root / f"{role}-1000.dump").is_file()

    # 디스크 확인이 **이 role의 database**를 잰다. 가짜는 어떤 이름에도 숫자를 돌려주므로,
    # 크기 질의가 엉뚱한 database(예: `postgres`)를 재도 위 단언은 전부 초록이다.
    before_dump = commands[:pg_dump_index]
    size_queries = [command for command in before_dump if "pg_database_size" in " ".join(command)]
    assert len(size_queries) == 1
    assert size_queries[0][-1] == f"SELECT pg_database_size('{database_name}')"
    # 첫 실행이라 테이블 크기도 묻는다 — pg_class는 database마다 따로라 그 database에 붙는다.
    table_queries = [command for command in before_dump if "pg_table_size" in " ".join(command)]
    assert len(table_queries) == 1
    assert table_queries[0][table_queries[0].index("--dbname") + 1] == database_name
    assert table_queries[0][4] == "kor-travel-shared-postgres"


# --- 디스크 여유(시작 전, role마다) ---------------------------------------------
#
# 요점: 필요량은 전역 상수가 아니라 **그 role의 database**에서 나온다. 같은 파일시스템의
# PostgreSQL을 지키려면 크게 뜨는 role에는 크게, 작게 뜨는 role에는 작게 요구해야 한다.

_GIB = 1024**3


def _seed_sized_manifest(
    root: Path,
    role: str,
    created_at: int,
    *,
    byte_size: int,
    db_size_bytes: int,
    instance: str | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = _manifest_payload(role, created_at, f"{role}-{created_at}.dump", instance=instance)
    payload["byte_size"] = byte_size
    payload["db_size_bytes"] = db_size_bytes
    (root / f"{role}-{created_at}.manifest").write_text(json.dumps(payload), encoding="utf-8")


def _estimate(
    root: Path,
    role: str,
    db_size_bytes: int,
    table_bytes: int | None = None,
) -> int:
    """`table_bytes`가 None이면 테이블 크기를 묻는 순간 실패한다 — 이미 뜬 dump가 있는
    자리에서는 그 질의가 필요 없어야 한다."""

    def ask_tables() -> int:
        if table_bytes is None:
            raise AssertionError("table sizes must not be queried when a dump exists")
        return table_bytes

    estimate, _basis = standalone_backup._expected_dump_bytes(
        root,
        role,  # type: ignore[arg-type]
        standalone_backup._role_config(role),  # type: ignore[arg-type]
        db_size_bytes=db_size_bytes,
        table_bytes=ask_tables,
    )
    return estimate


def _reserve() -> int:
    """가짜 instance(`_MAX_WAL_OUTPUT`)에서 유도되는 예약분 — 테스트가 숫자를 따로 적지 않는다."""

    return standalone_backup._disk_reserve_bytes(int(_MAX_WAL_OUTPUT))


def _required(estimate: int) -> int:
    required, _reason = standalone_backup._required_free_bytes(
        estimate, "basis", max_wal_bytes=int(_MAX_WAL_OUTPUT)
    )
    return required


def test_required_space_is_twice_the_dump_plus_the_reserve() -> None:
    """컨테이너 `/tmp`와 host 사본이 복사가 끝날 때까지 함께 있다 — 두 벌 + 예약분."""

    assert _required(1_011_308_463) == 2 * 1_011_308_463 + _reserve()


#: 살아있는 `max_wal_size` → 기대 예약분(리터럴 — 기대값을 검사 대상 코드로 계산하지 않는다).
#: 예약분을 쓰는 세 자리(create의 추정 경로·`--expected-dump-bytes` 경로·rehearse-restore)가 모두
#: 이 표로 돈다. 1GB(D4 이전)에서는 예약분이 하한 2 GiB와 같아 유도와 고정 상수를 가르지 못한다 —
#: 2GB(D4 뒤, 예약분 3 GiB)가 그 둘을 가르는 경우다.
_RESERVE_CASES = pytest.mark.parametrize(
    ("max_wal_bytes", "reserve"),
    [
        # D4 튜닝 뒤 공용 instance(2GB): 고정 2 GiB는 WAL 상한보다 작다.
        pytest.param(2 * 1024**3, 3 * 1024**3, id="max_wal_size-2GB"),
        # D4 이전 공용 instance(1GB): 하한과 같다.
        pytest.param(1024**3, 2 * 1024**3, id="max_wal_size-1GB"),
        # 작은 WAL 상한에서도 하한 밑으로 내려가지 않는다.
        pytest.param(512 * 1024**2, 2 * 1024**3, id="max_wal_size-512MB"),
    ],
)


@_RESERVE_CASES
def test_disk_reserve_covers_live_max_wal_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, max_wal_bytes: int, reserve: int
) -> None:
    """예약분 = max(2 GiB, **살아있는** `max_wal_size` + 1 GiB).

    백업 root·Docker 쓰기 층·공용 PGDATA가 한 디스크다. 고정 2 GiB는 `max_wal_size`가
    1GB일 때의 근거였고 D4가 2GB로 올리면 WAL 자리를 백업이 먹는다. 값은 dump를 뜨는
    **그 instance**에 묻는다 — compose나 `.env`를 추측하지 않는다.
    """

    root = tmp_path / "transport"
    _fake_time(monkeypatch)
    recorder = Mock(side_effect=_happy_run_checked(max_wal_bytes))
    monkeypatch.setattr(standalone_backup, "_run_checked", recorder)
    subprocess_run = _happy_subprocess_run()
    monkeypatch.setattr(standalone_backup.subprocess, "run", subprocess_run)
    # fake database 12345 B, 테이블 6789 B → 첫 실행 추정 = ceil(1.25 x 6789) = 8487 B.
    required = 2 * 8487 + reserve
    disk_usage = Mock(return_value=Mock(free=required - 1))
    monkeypatch.setattr(shutil, "disk_usage", disk_usage)

    with pytest.raises(standalone_backup.StandaloneBackupInsufficientSpaceError) as excinfo:
        create_standalone_backup("transport", backup_root=root)

    assert f"{required} B" in str(excinfo.value)
    assert f"max_wal_size {max_wal_bytes} B" in str(excinfo.value)
    # dump를 뜨는 그 instance(컨테이너·포트·admin)에 한 번 묻는다.
    wal_queries = [
        call.args[0]
        for call in recorder.call_args_list
        if "max_wal_size" in " ".join(call.args[0])
    ]
    assert wal_queries == [
        [
            "docker",
            "exec",
            "--user",
            "postgres",
            "kor-travel-shared-postgres",
            "psql",
            "--username",
            "shared_admin",
            "--port",
            "11000",
            "--dbname",
            "postgres",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--command",
            "SELECT pg_size_bytes(current_setting('max_wal_size'))",
        ]
    ]
    assert not any("pg_dump" in call.args[0] for call in recorder.call_args_list)
    subprocess_run.assert_not_called()

    # 정확히 필요한 만큼 있으면 진행한다.
    disk_usage.return_value = Mock(free=required)
    manifest = create_standalone_backup("transport", backup_root=root)
    assert manifest.backup_filename == "transport-1000.dump"


@pytest.mark.parametrize("expected_dump_bytes", [None, 6_000_000_000])
def test_an_unreadable_max_wal_answer_refuses_before_pg_dump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, expected_dump_bytes: int | None
) -> None:
    """`max_wal_size`를 못 읽으면 예약분을 모르는 것이다 — 추측하지 않고 시작하지 않는다.

    운영자 값(`--expected-dump-bytes`)도 이 질의를 건너뛰지 않는다.
    """

    happy = _happy_run_checked()

    def run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
        if "max_wal_size" in " ".join(arguments):
            return b"1GB\n"
        return happy(arguments, label=label, timeout=timeout)

    recorder = Mock(side_effect=run_checked)
    monkeypatch.setattr(standalone_backup, "_run_checked", recorder)
    _fake_time(monkeypatch)

    with pytest.raises(StandaloneBackupError, match="max_wal_size query returned an unexpected"):
        create_standalone_backup(
            "transport",
            backup_root=tmp_path / "transport",
            expected_dump_bytes=expected_dump_bytes,
        )
    assert not any("pg_dump" in call.args[0] for call in recorder.call_args_list)


def test_first_backup_is_bounded_by_table_data_not_by_indexes(tmp_path: Path) -> None:
    """이 database를 뜬 적이 없으면 dump에 들어가는 것(테이블)만으로 상한을 잡는다.

    인덱스는 dump에 정의 한 줄로만 들어간다. database 크기로 잡으면 인덱스가 큰 database는
    필요량이 부풀어 멀쩡한 백업을 거부한다. 아래 숫자는 크기 비율의 예시다(측정값 아님).
    """

    factor = standalone_backup._FIRST_DUMP_TABLE_FACTOR
    # 인덱스가 database의 60%인 경우: 35 GiB 중 테이블 14 GiB → 1.25 x 14 GiB.
    assert _estimate(tmp_path / "geo", "geo", 35 * _GIB, 14 * _GIB) == int(factor * 14 * _GIB)
    # 인덱스가 거의 없어 1.25 x 테이블이 database보다 크면 database 크기가 상한이다.
    assert _estimate(tmp_path / "transport", "transport", 13 * _GIB, 12 * _GIB) == 13 * _GIB


def test_later_backups_scale_the_last_dump_by_database_growth(tmp_path: Path) -> None:
    """한 번 뜨고 나면 실제 dump 크기에서 출발해 database가 커진 만큼만 키운다.
    테이블 크기는 다시 묻지 않는다(`_estimate`가 table_bytes=None으로 그것을 강제한다)."""

    root = tmp_path / "transport"
    # n150 수동 dump(2026-09-27): 1,011,308,463 B. 그때 database 약 13 GB.
    _seed_sized_manifest(root, "transport", 1000, byte_size=1_011_308_463, db_size_bytes=13 * _GIB)
    # database가 그대로면 dump도 그대로.
    assert _estimate(root, "transport", 13 * _GIB) == 1_011_308_463
    # database가 14/13배가 되면 dump 추정도 14/13배.
    grown = -(-1_011_308_463 * 14 // 13)
    assert abs(_estimate(root, "transport", 14 * _GIB) - grown) <= 1
    # database가 줄었다고 추정을 줄이지 않는다(마지막 dump가 하한).
    assert _estimate(root, "transport", 6 * _GIB) == 1_011_308_463


def test_disk_estimate_uses_the_newest_dump_of_the_current_database_only(tmp_path: Path) -> None:
    root = tmp_path / "transport"
    _seed_sized_manifest(root, "transport", 1000, byte_size=900 * 1024**2, db_size_bytes=13 * _GIB)
    _seed_sized_manifest(root, "transport", 2000, byte_size=1000 * 1024**2, db_size_bytes=13 * _GIB)
    # 다른 자리에서 뜬 dump는 다른 데이터다 — 더 새것이어도 추정에 쓰지 않는다.
    _seed_sized_manifest(
        root,
        "transport",
        3000,
        byte_size=40 * _GIB,
        db_size_bytes=13 * _GIB,
        instance="kor-travel-airport-db-postgres-1:127.0.0.1:5432/kor_travel_transport",
    )
    # 읽지 못하는 manifest 하나가 새 백업을 막지 않는다 — 건너뛴다. JSON이 아닌 것과
    # UTF-8이 아닌 것(UnicodeDecodeError는 ValueError다) 둘 다.
    (root / "transport-4000.manifest").write_text("{not json", encoding="utf-8")
    (root / "transport-5000.manifest").write_bytes(b'{"role": "transport\xff\xfe"}')
    assert _estimate(root, "transport", 13 * _GIB) == 1000 * 1024**2


def test_a_non_utf8_manifest_is_a_typed_error_not_a_traceback(tmp_path: Path) -> None:
    """CLI는 StandaloneBackupError만 잡는다 — 그 밖의 예외는 traceback으로 create를 죽인다."""

    root = tmp_path / "transport"
    root.mkdir()
    (root / "transport-5000.manifest").write_bytes(b"\xff\xfe\x00garbage")

    with pytest.raises(StandaloneBackupError, match="manifest is unreadable"):
        standalone_backup._read_manifest(root / "transport-5000.manifest")
    rows = list_standalone_backups_for_display("transport", backup_root=root)
    assert rows == [
        {
            "state": "unreadable",
            "filename": "transport-5000.manifest",
            "reason": "manifest is unreadable: transport-5000.manifest",
        }
    ]


def test_small_roles_do_not_inherit_a_large_roles_requirement(tmp_path: Path) -> None:
    """작은 role은 자기 dump만큼만 요구받는다: pinvi dump 384 KB → 예약분 + 768 KB."""

    _seed_sized_manifest(
        tmp_path / "pinvi", "pinvi", 1000, byte_size=384_332, db_size_bytes=12_000_000
    )
    estimate = _estimate(tmp_path / "pinvi", "pinvi", 12_000_000)
    assert _required(estimate) == 2 * 384_332 + _reserve()


def test_create_refuses_before_pg_dump_when_the_disk_is_too_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """모자라면 **시작하지 않는다** — pg_dump도, 컨테이너 안 임시 파일도, 산출물도 없다."""

    root = tmp_path / "transport"
    _fake_time(monkeypatch)
    run_checked = Mock(side_effect=_happy_run_checked())
    monkeypatch.setattr(standalone_backup, "_run_checked", run_checked)
    subprocess_run = _happy_subprocess_run()
    monkeypatch.setattr(standalone_backup.subprocess, "run", subprocess_run)
    # fake database 12345 B, 테이블 6789 B → 첫 실행 추정 = ceil(1.25 x 6789) = 8487 B
    # (database 크기보다 작다). 필요량 = 2 x 8487 + 예약분. 1 B 모자라게 준다.
    required = 2 * 8487 + _reserve()
    disk_usage = Mock(return_value=Mock(free=required - 1))
    monkeypatch.setattr(shutil, "disk_usage", disk_usage)

    with pytest.raises(standalone_backup.StandaloneBackupInsufficientSpaceError) as excinfo:
        create_standalone_backup("transport", backup_root=root)

    assert disk_usage.call_args.args[0] == root
    assert f"{required} B" in str(excinfo.value)
    assert f"{required - 1} B" in str(excinfo.value)
    assert not any("pg_dump" in call.args[0] for call in run_checked.call_args_list)
    subprocess_run.assert_not_called()
    assert list(root.glob("*.dump")) == []
    assert list(root.glob("*.manifest")) == []

    # 정확히 필요한 만큼 있으면 진행한다(경계는 "모자람"만 막는다).
    disk_usage.return_value = Mock(free=required)
    manifest = create_standalone_backup("transport", backup_root=root)
    assert manifest.backup_filename == "transport-1000.dump"


@_RESERVE_CASES
def test_an_operator_expected_dump_size_replaces_the_estimate_but_keeps_the_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    max_wal_bytes: int,
    reserve: int,
) -> None:
    """`--expected-dump-bytes`는 추정만 바꾼다. 필요량은 여전히 2배 + 예약분이고 남는다.

    geo 첫 백업의 비상 경로라 디스크가 가장 빠듯한 때다 — 예약분도 추정 경로와 같이 살아있는
    `max_wal_size`에서 나와야 한다. 그것을 가르는 것은 2GB 경우다(`_RESERVE_CASES`).
    """

    root = tmp_path / "geo"
    _fake_time(monkeypatch)
    run_checked = Mock(side_effect=_happy_run_checked(max_wal_bytes))
    monkeypatch.setattr(standalone_backup, "_run_checked", run_checked)
    subprocess_run = _happy_subprocess_run()
    monkeypatch.setattr(standalone_backup.subprocess, "run", subprocess_run)
    override = 6_000_000_000
    required = 2 * override + reserve
    disk_usage = Mock(return_value=Mock(free=required - 1))
    monkeypatch.setattr(shutil, "disk_usage", disk_usage)

    with caplog.at_level("INFO", logger=standalone_backup.__name__):
        with pytest.raises(standalone_backup.StandaloneBackupInsufficientSpaceError) as excinfo:
            create_standalone_backup("geo", backup_root=root, expected_dump_bytes=override)

    assert f"{required} B" in str(excinfo.value)
    assert "--expected-dump-bytes 6000000000" in str(excinfo.value)
    assert f"max_wal_size {max_wal_bytes} B" in str(excinfo.value)
    assert "6000000000" in caplog.text
    # 운영자 값이 있으면 추정용 질의를 하지 않는다 — 예약분의 `max_wal_size`만 한 번 묻는다.
    commands = [" ".join(call.args[0]) for call in run_checked.call_args_list]
    assert not any("pg_database_size" in command or "pg_table_size" in command for command in commands)
    assert sum("max_wal_size" in command for command in commands) == 1
    subprocess_run.assert_not_called()

    disk_usage.return_value = Mock(free=required)
    manifest = create_standalone_backup("geo", backup_root=root, expected_dump_bytes=override)
    assert manifest.backup_filename == "geo-1000.dump"


def test_an_expected_dump_size_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(StandaloneBackupError, match="positive"):
        create_standalone_backup("geo", backup_root=tmp_path / "geo", expected_dump_bytes=0)


def test_query_table_bytes_connects_to_the_database_it_measures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_checked = Mock(return_value=b"4242\n")
    monkeypatch.setattr(standalone_backup, "_run_checked", run_checked)

    assert (
        standalone_backup._query_table_bytes("kor-travel-shared-postgres", 11000, "admin", "kor_travel_geo")
        == 4242
    )
    command = run_checked.call_args.args[0]
    assert command[command.index("--dbname") + 1] == "kor_travel_geo"
    sql = command[-1]
    assert "pg_table_size" in sql
    # 인덱스(`i`)와 TOAST(`t`, 부모의 pg_table_size에 이미 들어 있다)는 세지 않는다.
    assert "relkind IN ('r', 'm')" in sql
    with pytest.raises(StandaloneBackupError, match="invalid"):
        standalone_backup._query_table_bytes("c", 1, "admin", "Bad-Name")


# --- 복원 계획(KUM-M13, 읽기 전용) --------------------------------------------
#
# 이 블록의 요점: 목록에 백업이 보이는 것과 그 백업으로 복원할 수 있는 것은 다르다.
# dump가 잘려 있어도, digest가 어긋나도, live schema가 백업 시점과 달라도 목록은
# 똑같이 초록색이다. 계획은 그 거짓 안전감을 걷어내야 하고, **아무것도 바꾸지 않아야**
# 한다.


def _seed_backup(root: Path, role: str, created_at: int, body: bytes) -> str:
    import hashlib

    name = f"{role}-{created_at}.dump"
    (root / name).write_bytes(body)
    payload = _manifest_payload(role, created_at, name)
    payload["byte_size"] = len(body)
    payload["sha256"] = hashlib.sha256(body).hexdigest()
    (root / f"{role}-{created_at}.manifest").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return name


def _plan_probes(monkeypatch: pytest.MonkeyPatch, *, live_head: str | None) -> None:
    monkeypatch.setattr(standalone_backup, "_discover_port", lambda name: 12500)
    monkeypatch.setattr(standalone_backup, "_discover_admin_role", lambda name: "addr")
    monkeypatch.setattr(
        standalone_backup,
        "_discover_alembic_head",
        lambda *args, **kwargs: live_head,
    )


def test_restore_plan_confirms_a_healthy_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _plan_probes(monkeypatch, live_head="0001_head")
    before = {path.name: path.read_bytes() for path in root.iterdir()}

    plan = plan_standalone_restore("geo", backup_root=root)

    assert plan.restorable is True
    assert plan.backup_filename == "geo-1000.dump"
    assert plan.live_alembic_head == "0001_head"
    assert plan.containers == ("kor-travel-shared-postgres",)
    # 계획은 아무것도 바꾸지 않는다.
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


def test_restore_plan_picks_the_newest_backup_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"old")
    _seed_backup(root, "geo", 3000, b"new")
    _plan_probes(monkeypatch, live_head="0001_head")

    assert plan_standalone_restore("geo", backup_root=root).backup_filename == (
        "geo-3000.dump"
    )
    assert plan_standalone_restore(
        "geo", backup_filename="geo-1000.dump", backup_root=root
    ).backup_filename == "geo-1000.dump"


def test_restore_plan_recomputes_the_digest_rather_than_trusting_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """manifest에 적힌 값을 그대로 믿으면 이 점검은 아무것도 검증하지 않는다."""

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    # dump만 조용히 바뀐 상태 — 크기는 같고 내용이 다르다.
    (root / "geo-1000.dump").write_bytes(b"dump-BYTES")
    _plan_probes(monkeypatch, live_head="0001_head")

    plan = plan_standalone_restore("geo", backup_root=root)

    assert plan.restorable is False
    assert [f.code for f in plan.findings if f.blocking] == ["SHA256_MISMATCH"]


def test_restore_plan_blocks_a_truncated_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    (root / "geo-1000.dump").write_bytes(b"dump")
    _plan_probes(monkeypatch, live_head="0001_head")

    plan = plan_standalone_restore("geo", backup_root=root)

    assert plan.restorable is False
    assert "SIZE_MISMATCH" in [f.code for f in plan.findings]


def test_restore_plan_blocks_when_the_dump_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    (root / "geo-1000.dump").unlink()
    _plan_probes(monkeypatch, live_head="0001_head")

    plan = plan_standalone_restore("geo", backup_root=root)

    assert plan.restorable is False
    assert [f.code for f in plan.findings if f.blocking] == ["DUMP_MISSING"]


def test_a_schema_revision_drift_is_reported_but_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """복원 자체는 가능하다 — 다만 코드가 기대하는 schema보다 과거로 간다는 사실을
    모르고 실행하면 안 된다. 판단은 사람이 한다."""

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _plan_probes(monkeypatch, live_head="0007_much_later")

    plan = plan_standalone_restore("geo", backup_root=root)

    assert plan.restorable is True
    drift = [f for f in plan.findings if f.code == "HEAD_MISMATCH"]
    assert drift and drift[0].blocking is False
    assert "0007_much_later" in drift[0].text


def test_an_unreadable_live_head_is_reported_not_assumed_equal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _plan_probes(monkeypatch, live_head=None)

    plan = plan_standalone_restore("geo", backup_root=root)

    assert "LIVE_HEAD_UNKNOWN" in [f.code for f in plan.findings]


def test_restore_plan_blocks_when_the_instance_cannot_be_inspected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")

    def explode(name: str) -> int:
        raise StandaloneBackupError("container is not running")

    monkeypatch.setattr(standalone_backup, "_discover_port", explode)

    plan = plan_standalone_restore("geo", backup_root=root)

    assert plan.restorable is False
    assert [f.code for f in plan.findings if f.blocking] == ["INSTANCE_UNREACHABLE"]
    assert plan.containers == ()


def test_restore_plan_refuses_an_unknown_backup_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _plan_probes(monkeypatch, live_head="0001_head")

    with pytest.raises(StandaloneBackupError, match="no backup named"):
        plan_standalone_restore("geo", backup_filename="geo-9999.dump", backup_root=root)
    with pytest.raises(StandaloneBackupError, match="invalid"):
        plan_standalone_restore("geo", backup_filename="../etc/passwd", backup_root=root)


def test_restore_plan_refuses_when_there_is_nothing_to_restore(tmp_path: Path) -> None:
    root = tmp_path / "geo"
    root.mkdir()

    with pytest.raises(StandaloneBackupError, match="no backup"):
        plan_standalone_restore("geo", backup_root=root)


def _manifest_payload(
    role: str, created_at: int, backup_filename: str, *, instance: str | None = None
) -> dict[str, object]:
    if instance is None:
        # 기본은 그 role이 **지금** 뜨는 자리다 — 모델에서 읽는다.
        container_name, database_name = standalone_backup._role_config(role)
        instance = f"{container_name}:127.0.0.1:12345/{database_name}"
    return {
        "role": role,
        "created_at_unix": created_at,
        "duration_sec": 1.0,
        "byte_size": 10,
        "sha256": "a" * 64,
        "backup_filename": backup_filename,
        "instance": instance,
        "db_size_bytes": 100,
        "toc_entry_count": 2,
        "alembic_head": "0001_head",
    }


def test_gc_rotates_only_dumps_of_the_instance_the_role_dumps_now(tmp_path: Path) -> None:
    """role이 instance를 옮긴 뒤 옛 instance의 dump는 회전에 끼지 않는다.

    옛 dump를 새 dump와 한 줄로 세우면 새 dump가 keep개 쌓이는 순간 옛 데이터의
    유일한 사본이 지워진다(2026-09-28 PinVi 옛 전용 instance의 dump 7개).
    """

    root = tmp_path / "pinvi"
    root.mkdir()
    old_instance = "retired-postgres:127.0.0.1:12800/pinvi"
    for created_at in (100, 200):
        name = f"pinvi-{created_at}.dump"
        (root / name).write_bytes(b"old")
        (root / name.replace(".dump", ".manifest")).write_text(
            json.dumps(_manifest_payload("pinvi", created_at, name, instance=old_instance)),
            encoding="utf-8",
        )
    for created_at in (1000, 2000, 3000):
        name = f"pinvi-{created_at}.dump"
        (root / name).write_bytes(b"new")
        (root / name.replace(".dump", ".manifest")).write_text(
            json.dumps(_manifest_payload("pinvi", created_at, name)), encoding="utf-8"
        )

    outcome = gc_standalone_backups("pinvi", keep=2, backup_root=root)

    assert outcome.deleted == ("pinvi-1000.dump",)
    assert outcome.other_instance_kept == ("pinvi-100.dump", "pinvi-200.dump")
    assert outcome.orphans_removed == ()
    assert {p.name for p in root.glob("*.dump")} == {
        "pinvi-100.dump",
        "pinvi-200.dump",
        "pinvi-2000.dump",
        "pinvi-3000.dump",
    }


def test_restore_plan_blocks_a_dump_taken_from_another_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """무결성이 멀쩡해도 다른 instance의 dump는 다른 데이터다 — 복원하면 바꿔치기다."""

    root = tmp_path / "pinvi"
    root.mkdir()
    name = _seed_backup(root, "pinvi", 1000, b"dump-bytes")
    manifest_path = root / "pinvi-1000.manifest"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["instance"] = "retired-postgres:127.0.0.1:12800/pinvi"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    _plan_probes(monkeypatch, live_head="0001_head")

    plan = plan_standalone_restore("pinvi", backup_root=root)

    assert plan.backup_filename == name
    assert plan.restorable is False
    assert [f.code for f in plan.findings if f.blocking] == ["INSTANCE_MISMATCH"]


def test_gc_binds_manifest_content_to_its_own_filename(tmp_path: Path) -> None:
    """손상된 manifest 하나가 살아 있는 다른 백업을 지우게 하면 안 된다.

    이전에는 gc가 삭제 대상을 manifest **내용**의 `backup_filename`에서 가져왔고
    그 값이 자기 파일 이름과 같은지 확인하지 않았다. 그래서 `geo-1000.manifest`의
    내용을 `geo-3000.dump`로 바꿔 두면 최신 백업이 지워졌다.
    """

    root = tmp_path / "geo"
    root.mkdir()
    for created_at in (1000, 3000):
        name = f"geo-{created_at}.dump"
        (root / name).write_bytes(b"x")
        (root / name.replace(".dump", ".manifest")).write_text(
            json.dumps(_manifest_payload("geo", created_at, name)), encoding="utf-8"
        )
    # 내용만 최신 백업을 가리키게 바꾼다.
    (root / "geo-1000.manifest").write_text(
        json.dumps(_manifest_payload("geo", 1000, "geo-3000.dump")), encoding="utf-8"
    )

    with pytest.raises(StandaloneBackupError, match="does not match its own file"):
        gc_standalone_backups("geo", keep=1, backup_root=root)

    assert (root / "geo-3000.dump").exists()


def test_gc_rejects_a_manifest_belonging_to_another_role(tmp_path: Path) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    name = "geo-1000.dump"
    (root / name).write_bytes(b"x")
    (root / "geo-1000.manifest").write_text(
        json.dumps(_manifest_payload("pinvi", 1000, name)), encoding="utf-8"
    )

    with pytest.raises(StandaloneBackupError, match="does not match the requested role"):
        list_standalone_backups("geo", backup_root=root)


def test_gc_collects_orphan_dumps_left_by_an_interrupted_backup(tmp_path: Path) -> None:
    """manifest 없는 dump는 목록에도 안 잡히고 복원할 수도 없어 영원히 쌓였다."""

    root = tmp_path / "geo"
    root.mkdir()
    name = "geo-3000.dump"
    (root / name).write_bytes(b"x")
    (root / name.replace(".dump", ".manifest")).write_text(
        json.dumps(_manifest_payload("geo", 3000, name)), encoding="utf-8"
    )
    # 중단된 create가 남긴 잔해: dump와 sha256만 있고 manifest가 없다.
    (root / "geo-1000.dump").write_bytes(b"orphan")
    (root / "geo-1000.dump.sha256").write_text("deadbeef  geo-1000.dump", encoding="ascii")

    outcome = gc_standalone_backups("geo", keep=5, backup_root=root)

    assert outcome.deleted == ()
    assert outcome.orphans_removed == ("geo-1000.dump",)
    assert not (root / "geo-1000.dump").exists()
    assert not (root / "geo-1000.dump.sha256").exists()
    # 정상 백업은 keep 안에 있으므로 그대로다.
    assert (root / "geo-3000.dump").exists()


def test_gc_refuses_while_a_backup_holds_the_role_lock(tmp_path: Path) -> None:
    """gc가 락을 잡지 않으면 진행 중인 백업(geo는 20분 이상)의 산출물을 지운다."""

    import fcntl as _fcntl

    root = tmp_path / "geo"
    root.mkdir()
    lock_path = root / ".backup.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        with pytest.raises(StandaloneBackupError, match="already running"):
            gc_standalone_backups("geo", keep=1, backup_root=root)
    finally:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        os.close(fd)


# --- 공유 그룹(setgid) 모드 — 적대 리뷰 2건이 각각 다른 각도로 찾은 결함 -------


def _shared_group_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`2770` setgid 루트를 만들고 정책을 그 그룹으로 고정한다."""

    root = tmp_path / "backups"
    root.mkdir()
    gid = os.stat(root).st_gid
    monkeypatch.setenv(standalone_backup.BACKUP_SHARED_GROUP_ENV, str(gid))
    os.chmod(root, 0o2770)
    return root


def test_a_new_role_directory_gets_the_shared_mode_not_the_umask_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """setgid 부모 아래 mkdir은 그룹과 setgid만 상속하고 permission 비트는 umask가 정한다.

    그 사실을 놓치면 `2770` 부모 아래 자식이 `2755`가 되어 그 role의 **첫 백업**이
    항상 거부된다 — 하필 cron이 건드리지 않아 UI로만 만드는 role들이다.
    """

    root = _shared_group_root(tmp_path, monkeypatch)
    policy = standalone_backup._artifact_mode_policy()
    assert policy.shared_gid is not None

    role_root = root / "geo"
    standalone_backup._prepare_backup_root(role_root, policy)

    mode = stat.S_IMODE(role_root.lstat().st_mode)
    assert mode & 0o070 == 0o070, f"group bits missing: {mode:04o}"
    assert role_root.lstat().st_mode & stat.S_ISGID


def test_the_role_lock_follows_the_shared_mode_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lock을 `0600`으로 고정하면 처음 만든 쪽만 열 수 있어 공유가 조용히 끝난다."""

    root = _shared_group_root(tmp_path, monkeypatch)
    role_root = root / "geo"
    standalone_backup._prepare_backup_root(
        role_root, standalone_backup._artifact_mode_policy()
    )

    with standalone_backup._role_lock(role_root):
        pass

    mode = stat.S_IMODE((role_root / ".backup.lock").lstat().st_mode)
    assert mode & 0o060 == 0o060, f"lock is not group-accessible: {mode:04o}"


def test_an_unopenable_role_lock_is_a_typed_error_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI는 StandaloneBackupError만 잡는다 — raw OSError는 traceback으로 죽는다."""

    root = tmp_path / "geo"
    root.mkdir()

    def refuse(*args: object, **kwargs: object) -> int:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(standalone_backup.os, "open", refuse)

    with pytest.raises(StandaloneBackupError, match="backup lock cannot be opened"):
        with standalone_backup._role_lock(root):
            pass


def test_the_shared_group_recovery_message_has_no_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """붙여넣어서 그대로 도는 명령이어야 한다 — `<group>`은 실행되지 않는다."""

    root = _shared_group_root(tmp_path, monkeypatch)
    role_root = root / "geo"
    role_root.mkdir(mode=0o755)
    os.chmod(role_root, 0o755)

    with pytest.raises(StandaloneBackupError) as caught:
        standalone_backup._prepare_backup_root(
            role_root, standalone_backup._artifact_mode_policy()
        )

    message = str(caught.value)
    assert "<group>" not in message
    # 파일까지 2770으로 만들라고 하지 않는다 — 0640 정책과 어긋난다.
    assert "chmod -R 2770" not in message


def test_restore_plan_turns_a_vanishing_dump_into_a_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gc가 lock을 잡고 지우는 사이일 수 있다 — traceback 대신 판정을 내야 한다."""

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _plan_probes(monkeypatch, live_head="0001_head")

    def vanish(path: Path) -> str:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(standalone_backup, "_sha256_file", vanish)

    plan = plan_standalone_restore("geo", backup_root=root)

    assert plan.restorable is False
    assert "DUMP_UNREADABLE" in [f.code for f in plan.findings]


# --- 복원 리허설(GM-07, scratch DB만 건드림) -----------------------------------
#
# 이 블록의 요점: 운영 DB로 덮어쓰는 파괴적 복원은 여전히 없다(오너가 로드맵 뒤로
# 미룸). 여기서 증명하는 것은 "이 백업이 scratch DB에 실제로 복원된다"는 사실뿐이고,
# scratch DB는 성공/실패와 무관하게 항상 지워야 한다.


#: 마지막 리허설이 실제로 낸 명령 순서. 순서 게이트가 읽는다 — 소유권 이양이
#: copy-in 뒤·pg_restore 앞에 있어야 하고, 그 위치는 값이 아니라 **순서**다.
REHEARSAL_COMMANDS: list[list[str]] = []


def _rehearsal_probes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pg_restore_returncode: int = 0,
    pg_restore_stderr: bytes = b"",
    restored_head: str | None = "0001_head",
    restored_size: int = 12345,
    catalog_digests: tuple[str | None, str | None] = ("cat-same", "cat-same"),
    max_wal_bytes: int = 1024**3,
) -> list[list[str]]:
    """createdb/pg_restore/cleanup을 가짜로 응답하고, cleanup 호출을 기록한다."""

    monkeypatch.setattr(standalone_backup, "_discover_port", lambda name: 12500)
    monkeypatch.setattr(standalone_backup, "_discover_admin_role", lambda name: "addr")
    monkeypatch.setattr(standalone_backup, "_query_db_size", lambda *a, **k: restored_size)
    monkeypatch.setattr(
        standalone_backup, "_discover_alembic_head", lambda *a, **k: restored_head
    )
    # (복원본, 운영본) 순으로 답한다 — 리허설이 그 순서로 두 번 부른다.
    digest_calls = iter(catalog_digests)
    monkeypatch.setattr(
        standalone_backup, "catalog_digest", lambda *a, **k: next(digest_calls, None)
    )
    # 오래된 scratch DB 스윕은 별도 테스트에서 다룬다 — 여기서는 항상 없다고 답한다.
    monkeypatch.setattr(
        standalone_backup, "_drop_stale_rehearsal_databases", lambda *a, **k: ()
    )

    REHEARSAL_COMMANDS.clear()

    def run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
        REHEARSAL_COMMANDS.append(list(arguments))
        if "max_wal_size" in " ".join(arguments):
            return f"{max_wal_bytes}\n".encode("ascii")
        if arguments[:2] == ["docker", "cp"]:
            return b""
        if "chown" in arguments:
            return b""
        if "createdb" in arguments:
            return b""
        raise AssertionError(f"unexpected _run_checked command in rehearsal: {arguments}")

    monkeypatch.setattr(standalone_backup, "_run_checked", run_checked)

    def run_pg_restore(arguments: list[str], *, label: str, timeout: int) -> tuple[int, bytes]:
        assert "pg_restore" in arguments
        REHEARSAL_COMMANDS.append(list(arguments))
        return pg_restore_returncode, pg_restore_stderr

    monkeypatch.setattr(standalone_backup, "_run_pg_restore", run_pg_restore)

    cleanup_calls: list[list[str]] = []

    def fake_subprocess_run(arguments: list[str], **kwargs: object) -> Mock:
        cleanup_calls.append(arguments)
        return Mock(returncode=0, stderr=b"", stdout=b"")

    monkeypatch.setattr(standalone_backup.subprocess, "run", fake_subprocess_run)
    return cleanup_calls


def test_rehearse_standalone_restore_confirms_a_healthy_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    cleanup_calls = _rehearsal_probes(monkeypatch, restored_head="0001_head")

    outcome = rehearse_standalone_restore("geo", backup_root=root)

    assert outcome.attempted is True
    assert outcome.restore_succeeded is True
    assert outcome.verified is True
    assert outcome.restored_alembic_head == "0001_head"
    assert outcome.restored_db_size_bytes == 12345
    assert outcome.scratch_database is not None
    # scratch DB는 항상 지운다 — dropdb가 실제로 호출됐는지 확인한다.
    assert any("dropdb" in call for call in cleanup_calls)
    assert any("rm" in call for call in cleanup_calls)


@_RESERVE_CASES
def test_rehearse_restore_refuses_before_copy_in_when_the_disk_is_too_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_wal_bytes: int, reserve: int
) -> None:
    """scratch DB는 대상과 같은 PGDATA에 원본만큼 자란다 — 공용 instance의 디스크를 채우면
    다섯 프로젝트의 DB가 멈춘다. 모자라면 dump를 넣지도, DB를 만들지도 않는다.

    예약분은 create와 같은 식으로 살아있는 `max_wal_size`에서 나온다 — 고정 하한과 그것을
    가르는 것은 2GB 경우다(`_RESERVE_CASES`)."""

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch, max_wal_bytes=max_wal_bytes)
    # manifest db_size_bytes 100 + dump 10 B + max_wal_size(가짜) + 예약분(같은 max_wal_size에서
    # 유도 — WAL을 두 번 세는 것은 일부러다, `_require_rehearsal_space`).
    required = 100 + len(b"dump-bytes") + max_wal_bytes + reserve
    disk_usage = Mock(return_value=Mock(free=required - 1))
    monkeypatch.setattr(shutil, "disk_usage", disk_usage)

    with pytest.raises(standalone_backup.StandaloneBackupInsufficientSpaceError) as excinfo:
        rehearse_standalone_restore("geo", backup_root=root)

    assert disk_usage.call_args.args[0] == root
    assert f"{required} B" in str(excinfo.value)
    assert "max_wal_size" in str(excinfo.value)
    # 크기 질의 말고는 아무것도 하지 않았다 — copy-in·chown·createdb·pg_restore 없음.
    assert len(REHEARSAL_COMMANDS) == 1
    assert "max_wal_size" in " ".join(REHEARSAL_COMMANDS[0])

    disk_usage.return_value = Mock(free=required)
    assert rehearse_standalone_restore("geo", backup_root=root).verified is True


def test_rehearse_restore_hands_the_dump_over_before_restoring_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """복원 전에 dump의 소유권을 복원 유저에게 넘긴다.

    `docker cp`는 host 파일의 소유권·권한을 **그대로 보존한다.** 백업은 root:root
    0600이고 pg_restore는 컨테이너 안 postgres(uid 999)로 도므로, 넘겨주지 않으면
    자기가 복원할 파일을 읽지 못한다. 2026-09-07 n150 실측:

        pg_restore: error: could not open input file
        "/tmp/rehearsal-....dump": Permission denied

    모든 백업이 root 0600이라 이 명령은 그전까지 한 번도 성공한 적이 없다 — 그래서
    `T-VN-H49-{GEO-DAGSTER,CONCIERGE,PINVI}`의 마지막 조건이 닫히지 못하고 있었다.

    결박하는 것은 **순서**다. chown이 copy-in 뒤·pg_restore 앞에 있지 않으면 아무
    의미가 없다.
    """
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch, restored_head="0001_head")

    rehearse_standalone_restore("geo", backup_root=root)

    kinds = [
        "cp"
        if command[:2] == ["docker", "cp"]
        else next((token for token in ("chown", "createdb", "pg_restore") if token in command), "?")
        for command in REHEARSAL_COMMANDS
    ]
    assert "cp" in kinds and "chown" in kinds and "pg_restore" in kinds, kinds
    assert kinds.index("cp") < kinds.index("chown") < kinds.index("pg_restore"), kinds

    chown = REHEARSAL_COMMANDS[kinds.index("chown")]
    restore = REHEARSAL_COMMANDS[kinds.index("pg_restore")]
    # chown 대상 경로 == pg_restore가 읽는 경로.
    assert chown[-1] == restore[-1]
    # 소유자는 복원을 실행하는 그 유저다 — 둘이 갈라지면 결박이 없는 것과 같다.
    exec_user = standalone_backup._REHEARSAL_EXEC_USER
    assert chown[-2] == f"{exec_user}:{exec_user}"
    assert restore[restore.index("--user") + 1] == exec_user
    # chown 자체는 root로 돈다 — 컨테이너 기본 유저로는 소유권을 넘길 수 없다.
    assert chown[chown.index("--user") + 1] == "root"


def test_rehearse_standalone_restore_skips_the_attempt_when_the_plan_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """무결성이 깨진 dump를 scratch DB에라도 복원 시도하는 것은 낭비다."""

    root = tmp_path / "geo"
    root.mkdir()
    # dump 파일 자체가 없다 — plan이 DUMP_MISSING으로 차단한다. 이 probe는
    # plan_standalone_restore 자신의 정당한 live-schema 조회만 허용한다.
    payload = _manifest_payload("geo", 1000, "geo-1000.dump")
    (root / "geo-1000.manifest").write_text(json.dumps(payload), encoding="utf-8")
    _plan_probes(monkeypatch, live_head="0001_head")

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("복원 계획이 차단됐으면 pg_restore를 시도하면 안 된다")

    monkeypatch.setattr(standalone_backup, "_run_pg_restore", fail_if_called)

    outcome = rehearse_standalone_restore("geo", backup_root=root)

    assert outcome.attempted is False
    assert outcome.verified is False
    assert outcome.plan.restorable is False


def test_rehearse_standalone_restore_reports_a_failed_pg_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    cleanup_calls = _rehearsal_probes(
        monkeypatch, pg_restore_returncode=1, pg_restore_stderr=b"boom"
    )

    outcome = rehearse_standalone_restore("geo", backup_root=root)

    assert outcome.attempted is True
    assert outcome.restore_succeeded is False
    assert outcome.verified is False
    assert "REHEARSAL_RESTORE_FAILED" in [f.code for f in outcome.findings]
    assert any(f.blocking for f in outcome.findings)
    # 실패해도 scratch DB 정리는 여전히 시도한다.
    assert any("dropdb" in call for call in cleanup_calls)


def test_rehearse_standalone_restore_flags_a_schema_mismatch_after_a_successful_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pg_restore가 exit 0으로 끝나도 복원된 내용이 manifest와 다르면 검증 실패다."""

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch, restored_head="9999_other_head")

    outcome = rehearse_standalone_restore("geo", backup_root=root)

    assert outcome.restore_succeeded is True
    assert outcome.verified is False
    assert "REHEARSAL_HEAD_MISMATCH" in [f.code for f in outcome.findings]


def test_rehearse_standalone_restore_always_drops_the_scratch_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cleanup은 try 블록의 예외 여부와 무관하게 실행돼야 한다(finally)."""

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    monkeypatch.setattr(standalone_backup, "_discover_port", lambda name: 12500)
    monkeypatch.setattr(standalone_backup, "_discover_admin_role", lambda name: "addr")

    def run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
        if "max_wal_size" in " ".join(arguments):
            return b"1073741824\n"
        if arguments[:2] == ["docker", "cp"]:
            return b""
        raise StandaloneBackupError("createdb exploded")

    monkeypatch.setattr(standalone_backup, "_run_checked", run_checked)

    cleanup_calls: list[list[str]] = []

    def fake_subprocess_run(arguments: list[str], **kwargs: object) -> Mock:
        cleanup_calls.append(arguments)
        return Mock(returncode=0, stderr=b"", stdout=b"")

    monkeypatch.setattr(standalone_backup.subprocess, "run", fake_subprocess_run)

    with pytest.raises(StandaloneBackupError):
        rehearse_standalone_restore("geo", backup_root=root)

    assert any("dropdb" in call for call in cleanup_calls)


def test_rehearse_standalone_restore_generates_a_unique_scratch_database_name_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 초에 두 번 불러도 이름이 겹치면 안 된다 — geo/geo_dagster처럼 컨테이너를
    공유하는 role 쌍이 동시에 리허설하면 한쪽 dropdb가 다른 쪽의 진행 중인 scratch
    DB를 지울 수 있었다."""

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch)
    monkeypatch.setattr(standalone_backup.time, "time", lambda: 1000.0)

    first = rehearse_standalone_restore("geo", backup_root=root)
    second = rehearse_standalone_restore("geo", backup_root=root)

    assert first.scratch_database != second.scratch_database
    assert first.scratch_database.startswith("ktdm_rehearsal_1000_")


def test_rehearse_standalone_restore_flags_a_size_shortfall_short_of_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """복원된 크기가 0은 아니어도 백업 시점 크기의 절반에 못 미치면 부분 복원을 의심한다.

    갓 만든 빈 DB도 카탈로그만으로 몇 MB라 순수 0바이트 판정은 현실에서 거의 걸리지
    않는다 — manifest 크기 대비 비율로 봐야 실제로 잡는다.
    """

    root = tmp_path / "geo"
    root.mkdir()
    # _manifest_payload의 db_size_bytes == 100.
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch, restored_size=10)

    outcome = rehearse_standalone_restore("geo", backup_root=root)

    assert outcome.restore_succeeded is True
    assert outcome.verified is False
    assert "REHEARSAL_SIZE_SHORTFALL" in [f.code for f in outcome.findings]
    assert "REHEARSAL_EMPTY_DATABASE" not in [f.code for f in outcome.findings]


def test_rehearse_standalone_restore_surfaces_a_cleanup_failure_without_hiding_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dropdb 정리가 실패해도 예외로 삼키지 않고 findings에 남긴다 — 그래야 잔해가
    생겼다는 사실이 조용히 사라지지 않는다. 복원 자체는 성공했으므로 verified는 유지한다.
    """

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    monkeypatch.setattr(standalone_backup, "_discover_port", lambda name: 12500)
    monkeypatch.setattr(standalone_backup, "_discover_admin_role", lambda name: "addr")
    monkeypatch.setattr(standalone_backup, "_query_db_size", lambda *a, **k: 12345)
    monkeypatch.setattr(standalone_backup, "_discover_alembic_head", lambda *a, **k: "0001_head")
    monkeypatch.setattr(
        standalone_backup, "_drop_stale_rehearsal_databases", lambda *a, **k: ()
    )

    def run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
        if "max_wal_size" in " ".join(arguments):
            return b"1073741824\n"
        return b""

    monkeypatch.setattr(standalone_backup, "_run_checked", run_checked)
    monkeypatch.setattr(
        standalone_backup, "_run_pg_restore", lambda *a, **k: (0, b"")
    )

    def fake_subprocess_run(arguments: list[str], **kwargs: object) -> Mock:
        if "dropdb" in arguments:
            return Mock(returncode=1, stderr=b"still has active connections", stdout=b"")
        return Mock(returncode=0, stderr=b"", stdout=b"")

    monkeypatch.setattr(standalone_backup.subprocess, "run", fake_subprocess_run)

    outcome = rehearse_standalone_restore("geo", backup_root=root)

    assert outcome.restore_succeeded is True
    assert outcome.verified is True
    assert "REHEARSAL_CLEANUP_INCOMPLETE" in [f.code for f in outcome.findings]


def test_drop_stale_rehearsal_databases_removes_only_databases_older_than_the_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000_000.0
    stale_epoch = int(now) - standalone_backup._REHEARSAL_STALE_AFTER_SECONDS - 1
    fresh_epoch = int(now) - 10
    listing = (
        f"ktdm_rehearsal_{stale_epoch}_aaaa\n"
        f"ktdm_rehearsal_{fresh_epoch}_bbbb\n"
        "\n"
    ).encode()

    monkeypatch.setattr(standalone_backup.time, "time", lambda: now)

    def run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
        assert "pg_database" in " ".join(arguments)
        return listing

    monkeypatch.setattr(standalone_backup, "_run_checked", run_checked)

    dropped_names: list[str] = []

    def fake_subprocess_run(arguments: list[str], **kwargs: object) -> Mock:
        if "dropdb" in arguments:
            dropped_names.append(arguments[-1])
        return Mock(returncode=0, stderr=b"", stdout=b"")

    monkeypatch.setattr(standalone_backup.subprocess, "run", fake_subprocess_run)

    dropped = standalone_backup._drop_stale_rehearsal_databases(
        "kor-travel-geo-postgres", 12500, "addr"
    )

    assert dropped == (f"ktdm_rehearsal_{stale_epoch}_aaaa",)
    assert dropped_names == [f"ktdm_rehearsal_{stale_epoch}_aaaa"]


def test_rehearse_standalone_restore_reports_swept_stale_databases_as_a_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch)
    monkeypatch.setattr(
        standalone_backup,
        "_drop_stale_rehearsal_databases",
        lambda *a, **k: ("ktdm_rehearsal_1_aaaa",),
    )

    outcome = rehearse_standalone_restore("geo", backup_root=root)

    assert "STALE_REHEARSAL_DATABASES_CLEANED" in [f.code for f in outcome.findings]
    assert outcome.verified is True


def test_rehearse_restore_does_not_strip_ownership_from_the_scratch_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--no-owner --no-privileges`가 붙으면 소유권 보존을 **물을 수 없다.**

    그 둘이 붙어 있는 한 복원본의 소유자·ACL은 항상 실행자의 것이 되므로, 리허설이
    증명하는 것이 "행이 들어갔다"에 그친다. n150 실측에서 정확히 그것 때문에 카탈로그
    대조가 항상 어긋났다(2026-09-08).
    """

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch)

    rehearse_standalone_restore("geo", backup_root=root)

    restore_command = next(
        command for command in REHEARSAL_COMMANDS if "pg_restore" in command
    )
    assert "--no-owner" not in restore_command
    assert "--no-privileges" not in restore_command
    # 반쯤 복원된 DB를 조용히 통과시키지 않는다.
    assert "--exit-on-error" in restore_command


def test_rehearse_restore_flags_catalog_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """소유권·ACL이 원본과 다르면 finding을 남긴다."""

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch, catalog_digests=("cat-restored", "cat-live"))

    outcome = rehearse_standalone_restore("geo", backup_root=root)

    assert "REHEARSAL_CATALOG_DRIFT" in [f.code for f in outcome.findings]
    assert outcome.restored_catalog_digest == "cat-restored"
    assert outcome.live_catalog_digest == "cat-live"


def test_rehearse_restore_does_not_read_an_unreadable_catalog_as_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """지문을 못 읽으면 **통과가 아니다.**

    둘 다 `None`이면 소박한 동등 비교는 '같다'가 되는데, 그것이 정확히 이 도구가
    막으려는 침묵이다.
    """

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch, catalog_digests=(None, None))

    outcome = rehearse_standalone_restore("geo", backup_root=root)

    assert "REHEARSAL_CATALOG_UNKNOWN" in [f.code for f in outcome.findings]
    assert "REHEARSAL_CATALOG_DRIFT" not in [f.code for f in outcome.findings]


def test_rehearse_restore_reports_a_matching_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같으면 drift도 unknown도 남기지 않는다 — n150 실측이 이 상태였다."""

    root = tmp_path / "geo"
    root.mkdir()
    _seed_backup(root, "geo", 1000, b"dump-bytes")
    _rehearsal_probes(monkeypatch)

    outcome = rehearse_standalone_restore("geo", backup_root=root)

    codes = [f.code for f in outcome.findings]
    assert "REHEARSAL_CATALOG_DRIFT" not in codes
    assert "REHEARSAL_CATALOG_UNKNOWN" not in codes
    assert outcome.restored_catalog_digest == outcome.live_catalog_digest


def test_catalog_digest_sql_pins_the_two_properties_execution_proved() -> None:
    """SQL이 `search_path`를 고정하고 ACL을 `acldefault()`로 정규화하는지 본다.

    **이 게이트의 한계를 먼저 적는다.** 위 리허설 테스트들은 `catalog_digest`를 통째로
    monkeypatch하므로 SQL이 한 줄도 돌지 않는다 — 그래서 두 속성을 지우는 변이가 그
    테스트들에서는 초록이다(실제로 확인했다). 여기서는 **텍스트로** 잡는다.

    두 속성의 진짜 증명은 n150 실측이다(2026-09-08):

    - `search_path`를 고정하지 않으면 `pg_get_function_identity_arguments()`가 세션에
      따라 `geometry`와 `x_extension.geometry`를 오가, 소유권이 완전히 같은데도
      PostGIS 함수 **495건**이 어긋났다.
    - ACL을 정규화하지 않으면 `relacl`이 NULL인 복원본과 소유자 기본 권한이 명시된
      운영본이 **의미가 같은데도** 어긋났다(마지막까지 남은 1건).

    거짓 양성은 진짜 drift를 덮으므로 없는 것보다 나쁘다. 그래서 지우면 안 된다.
    """

    sql = standalone_backup._CATALOG_DIGEST_SQL
    assert "SET LOCAL search_path = pg_catalog;" in sql
    # 세 종류 ACL이 전부 정규화돼야 한다 — 하나만 빠져도 그 종류에서 거짓 양성이 난다.
    assert "c.relacl," in sql
    assert "acldefault(" in sql
    assert "coalesce(p.proacl, acldefault(" in sql
    assert "coalesce(n.nspacl, acldefault(" in sql
    # `acldefault`의 첫 인자는 `"char"`다 — 캐스트가 없으면 함수를 못 찾아 지문이
    # 통째로 `None`이 되고, 그것이 '읽지 못함'으로 조용히 새어 나간다.
    assert sql.count('::"char"') >= 3
