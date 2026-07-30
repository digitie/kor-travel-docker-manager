from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from dotenv import dotenv_values

from kor_travel_docker_manager import cli as cli_module
from kor_travel_docker_manager.cli import build_parser, main
from kor_travel_docker_manager.services import map_ui_auth_rotation as rotation
from kor_travel_docker_manager.services.map_ui_auth_rotation import (
    MAP_PBKDF2_DIGEST_BYTES,
    MAP_PBKDF2_ITERATIONS,
    MAP_PBKDF2_SALT_BYTES,
    CommandResult,
    RotationPaths,
    generate_map_pbkdf2_hash,
    rotate_map_ui_auth,
    verify_map_pbkdf2_hash,
)

_STABLE_UI_SHA = "a" * 64
_NON_UI_SHA = "b" * 64


def _journal_payload(
    *,
    operation_id: str,
    phase: str,
    old_env_sha256: str,
    new_env_sha256: str,
    recovery_env_sha256: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "operation_id": operation_id,
        "version": rotation._ROTATION_JOURNAL_VERSION,
        "old_env_sha256": old_env_sha256,
        "new_env_sha256": new_env_sha256,
        "phase": phase,
        "before_ui_sha256": _STABLE_UI_SHA,
        "before_non_ui": {"pinvi-api": _NON_UI_SHA},
    }
    if recovery_env_sha256 is not None:
        payload["recovery_env_sha256"] = recovery_env_sha256
    return payload


def test_map_ui_auth_rotate_parser_exists():
    parser = build_parser()

    args = parser.parse_args(
        [
            "map-ui-auth",
            "rotate",
            "--password-stdin",
            "--project-root",
            "/srv/manager",
            "--json",
        ]
    )

    assert args.command == "map-ui-auth"
    assert args.map_ui_auth_action == "rotate"
    assert args.password_stdin is True
    assert args.project_root == "/srv/manager"
    assert args.json is True


def test_map_pbkdf2_hash_is_exact_map_format():
    encoded = generate_map_pbkdf2_hash(
        "new-password-with-length",
        salt=b"s" * MAP_PBKDF2_SALT_BYTES,
    )

    algorithm, iterations, salt, digest = encoded.split("$")
    assert algorithm == "pbkdf2_sha256"
    assert iterations == str(MAP_PBKDF2_ITERATIONS)
    assert salt == "c3Nzc3Nzc3Nzc3Nzc3Nzcw"
    assert "=" not in salt
    assert "=" not in digest
    assert len(digest) == 43
    assert MAP_PBKDF2_DIGEST_BYTES == 32
    assert verify_map_pbkdf2_hash("new-password-with-length", encoded)
    assert not verify_map_pbkdf2_hash("wrong-password-with-length", encoded)


@pytest.mark.parametrize("password", ["current\x00password", "current\rpassword", "current\npassword"])
def test_current_password_rejects_unsafe_control_characters(password: str):
    with pytest.raises(Exception, match="forbidden character"):
        rotation._validate_current_password(password)


@pytest.mark.parametrize(
    "password",
    [
        "new-password-with-\x00-nul",
        "new-password-with-\r-cr",
        "new-password-with-\n-lf",
        "new-password-with-'quote",
        "new-password-with-\t-tab",
    ],
)
def test_new_password_rejects_env_unsafe_characters(password: str):
    with pytest.raises(Exception, match="forbidden character|whitespace"):
        rotation._validate_new_password(password)


def test_map_ui_auth_rotate_stdin_does_not_echo_secrets(capsys, monkeypatch):
    current_password = "current-password-with-length"
    new_password = "new-password-with-length"
    monkeypatch.setattr(
        "sys.stdin",
        SimpleNamespace(
            buffer=io.BytesIO(f"{current_password}\n{new_password}\n".encode()),
            isatty=lambda: False,
        ),
    )

    class Result:
        def as_process_result(self):
            return {
                "success": True,
                "returncode": 0,
                "phase": "committed",
                "checks": ["secret_safe"],
                "stdout": "",
                "stderr": "",
            }

    rotate_calls: list[dict[str, str | None]] = []

    def fake_rotate(**kwargs):
        rotate_calls.append(kwargs)
        return Result()

    with patch("kor_travel_docker_manager.cli._load_map_ui_auth_rotator", return_value=fake_rotate):
        code = main(
            [
                "map-ui-auth",
                "rotate",
                "--password-stdin",
                "--project-root",
                "/srv/manager",
                "--json",
            ]
        )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["phase"] == "committed"
    assert rotate_calls[0]["project_root"] == "/srv/manager"
    combined = captured.out + captured.err
    assert current_password not in combined
    assert new_password not in combined


def test_map_ui_auth_rotate_stdin_rejects_extra_line(monkeypatch):
    monkeypatch.setattr(
        "sys.stdin",
        SimpleNamespace(
            buffer=io.BytesIO(b"current-password-with-length\nnew-password-with-length\nextra\n"),
            isatty=lambda: False,
        ),
    )

    assert main(["map-ui-auth", "rotate", "--password-stdin"]) == 2


def test_root_map_ui_auth_rotate_requires_trusted_launcher_before_reading_password(
    monkeypatch,
):
    monkeypatch.setattr(cli_module.os, "geteuid", lambda: 0)
    monkeypatch.delenv("KTDM_TRUSTED_ROOT_LAUNCHER", raising=False)
    monkeypatch.setattr(
        "sys.stdin",
        SimpleNamespace(
            buffer=io.BytesIO(b"current-password-with-length\nnew-password-with-length\n"),
            isatty=lambda: False,
        ),
    )

    assert main(["map-ui-auth", "rotate", "--password-stdin"]) == 2


def test_project_root_uses_explicit_canonical_checkout(tmp_path: Path, monkeypatch):
    (tmp_path / "docker-compose.yml").write_text("name: test\n", encoding="utf-8")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", str(tmp_path))

    assert rotation._project_root() == tmp_path.resolve()


def test_project_root_uses_validated_current_working_directory(tmp_path: Path, monkeypatch):
    (tmp_path / "docker-compose.yml").write_text("name: test\n", encoding="utf-8")
    monkeypatch.delenv("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert rotation._project_root() == tmp_path.resolve()


def test_env_capture_requires_canonical_owner(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("KEY=value\n", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(Exception, match="private regular file"):
        rotation._capture_strict_child_file(
            tmp_path,
            ".env",
            kind="env",
            expected_uid=os.geteuid() + 1,
        )


def test_source_boundary_rejects_unexpected_owner(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    with pytest.raises(Exception, match="owner-locked"):
        rotation._validate_manager_source_boundary(tmp_path, source_owner_uid=os.geteuid() + 1)


def test_source_revision_evidence_uses_root_owned_file_without_git_execution(
    tmp_path: Path,
    monkeypatch,
):
    revision = "2" * 40
    (tmp_path / ".git").mkdir()
    evidence = tmp_path / rotation._MANAGER_SOURCE_REVISION_FILE
    evidence.write_text(f"{revision}\n", encoding="utf-8")
    monkeypatch.setattr(
        rotation.subprocess,
        "run",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("git must not execute")),
    )

    assert (
        rotation._validate_manager_source_evidence(
            tmp_path,
            {rotation._MANAGER_SOURCE_REVISION_ENV: revision},
            source_owner_uid=os.geteuid(),
        )
        == revision
    )


def test_source_revision_evidence_file_is_required(tmp_path: Path):
    with pytest.raises(Exception, match="evidence file is required"):
        rotation._validate_manager_source_evidence(
            tmp_path,
            {rotation._MANAGER_SOURCE_REVISION_ENV: "2" * 40},
            source_owner_uid=os.geteuid(),
        )


def test_inspect_container_invokes_docker_with_single_argv(monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stdout = json.dumps([{"Id": "container-id"}])

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return Completed()

    monkeypatch.setattr(rotation.subprocess, "run", fake_run)

    assert rotation._inspect_container("kor-travel-map-ui-latest")["Id"] == "container-id"
    assert calls[0][0] == (
        [rotation._DOCKER_BIN, "inspect", "kor-travel-map-ui-latest"],
    )
    assert calls[0][1]["env"]["DOCKER_HOST"] == rotation._DOCKER_HOST
    assert calls[0][1]["text"] is True
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["timeout"] == 30


def test_compose_override_file_is_rejected(tmp_path: Path):
    compose_path = tmp_path / "docker-compose.yml"
    override_path = tmp_path / "docker-compose.override.yml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    override_path.write_text("services: {}\n", encoding="utf-8")

    with pytest.raises(Exception, match="single compose file"):
        rotation._validate_single_file_compose_boundary(
            compose_path,
            source_owner_uid=os.geteuid(),
        )


def test_ui_stable_signature_ignores_recreate_volatile_paths_and_config_hash():
    before = _ui_inspect_payload(
        container_id="old",
        config_hash="hash-old",
        log_path="/var/lib/docker/containers/old/old-json.log",
    )
    after = _ui_inspect_payload(
        container_id="new",
        config_hash="hash-new",
        log_path="/var/lib/docker/containers/new/new-json.log",
    )

    before_signature = rotation._ui_stable_signature(before)
    assert before_signature == rotation._ui_stable_signature(after)
    assert len(before_signature) == 64
    assert "hash" not in before_signature
    assert "session" not in before_signature
    drifted = _ui_inspect_payload(
        container_id="new",
        config_hash="hash-new",
        log_path="/var/lib/docker/containers/new/new-json.log",
        extra_bind="/tmp:/tmp:ro",
    )
    assert rotation._ui_stable_signature(before) != rotation._ui_stable_signature(drifted)


def test_ui_stable_signature_normalizes_frozen_compose_provenance_labels():
    before = _ui_inspect_payload(
        container_id="old",
        config_hash="hash-old",
        log_path="/var/lib/docker/containers/old/old-json.log",
    )
    after = _ui_inspect_payload(
        container_id="new",
        config_hash="hash-new",
        log_path="/var/lib/docker/containers/new/new-json.log",
    )
    before["Config"]["Labels"]["com.docker.compose.project.config_files"] = (
        "/opt/kor-travel-docker-manager/docker-compose.yml"
    )
    before["Config"]["Labels"]["com.docker.compose.project.environment_file"] = (
        "/opt/kor-travel-docker-manager/.env"
    )
    after["Config"]["Labels"]["com.docker.compose.project.config_files"] = (
        "/var/lib/kor-travel-docker-manager/map-ui-auth-rotation/prod/frozen-compose.yml"
    )

    assert rotation._ui_stable_signature(before) == rotation._ui_stable_signature(after)


def test_auth_lifecycle_requires_exact_login_json_and_logout_clear_cookie():
    responses = iter(
        [
            {
                "status": 200,
                "set_cookie": "ktm_admin_session=value; HttpOnly; Secure; SameSite=Strict; Path=/",
                "payload": {"ok": True, "next": "/wrong"},
            }
        ]
    )

    with patch.object(rotation, "_http_request", side_effect=lambda *_, **__: next(responses)):
        try:
            rotation._verify_auth_lifecycle(
                origin="https://map.example.test",
                username="admin",
                password="password-with-length",
                expect_cookie_reject=None,
            )
        except Exception as exc:
            assert "login verification failed" in str(exc)
        else:
            raise AssertionError("invalid login JSON must fail")


def test_auth_lifecycle_preserves_active_cookie_and_checks_revoked_cookie():
    responses = iter(
        [
            {
                "status": 200,
                "set_cookie": "ktm_admin_session=active; HttpOnly; Secure; SameSite=Strict; Path=/",
                "payload": {"ok": True, "next": "/ops/datasets"},
            },
            {"status": 200, "set_cookie": "", "payload": None},
            {
                "status": 200,
                "set_cookie": "ktm_admin_session=logout-target; HttpOnly; Secure; SameSite=Strict; Path=/",
                "payload": {"ok": True, "next": "/ops/datasets"},
            },
            {"status": 200, "set_cookie": "", "payload": None},
            {
                "status": 200,
                "set_cookie": (
                    "ktm_admin_session=; Max-Age=0; HttpOnly; Secure; "
                    "SameSite=Strict; Path=/"
                ),
                "payload": {"ok": True},
            },
            {"status": 302, "location": "/login", "set_cookie": "", "payload": None},
            {"status": 302, "location": "/login", "set_cookie": "", "payload": None},
        ]
    )
    seen_cookies: list[str] = []

    def fake_request(_opener, _url, **kwargs):
        headers = kwargs.get("headers", {})
        if "Cookie" in headers:
            seen_cookies.append(headers["Cookie"])
        return next(responses)

    with patch.object(rotation, "_http_request", side_effect=fake_request):
        cookie = rotation._verify_auth_lifecycle(
            origin="https://map.example.test",
            username="admin",
            password="password-with-length",
            expect_cookie_reject=None,
            preserve_active_session=True,
        )

    assert cookie == "ktm_admin_session=active"
    assert seen_cookies == ["ktm_admin_session=logout-target"]


def test_cookie_parser_rejects_duplicate_or_future_logout_cookie():
    assert not rotation._valid_login_cookie(
        "ktm_admin_session=value; HttpOnly; Secure; SameSite=Strict; Path=/; Path=/x"
    )
    assert not rotation._valid_login_cookie(
        "ktm_admin_session=value; HttpOnly; Secure; SameSite=Strict; Path=/, other=x"
    )
    assert not rotation._valid_logout_cookie(
        "ktm_admin_session=; Expires=Wed, 01 Jan 3000 00:00:00 GMT; Path=/"
    )


@pytest.mark.parametrize(
    ("location", "accepted"),
    [
        ("/login", True),
        ("https://map.example.test/login", True),
        ("/login?next=/ops", False),
        ("/login#fragment", False),
        ("https://map.example.test/login?next=/ops", False),
        ("https://other.example.test/login", False),
        ("//other.example.test/login", False),
        ("https://user@map.example.test/login", False),
    ],
)
def test_login_redirect_requires_exact_same_origin_login(location: str, accepted: bool):
    assert (
        rotation._is_login_redirect(
            {"status": 302, "location": location},
            origin="https://map.example.test",
        )
        is accepted
    )


def test_journal_phase_update_preserves_env_sha(tmp_path: Path):
    journal_path = tmp_path / "journal.json"
    old_sha = "0" * 64
    new_sha = "1" * 64
    operation_id = "op-1"

    rotation._write_journal(
        journal_path,
        _journal_payload(
            operation_id=operation_id,
            phase="prepared",
            old_env_sha256=old_sha,
            new_env_sha256=new_sha,
        ),
    )
    rotation._write_journal(journal_path, {"operation_id": operation_id, "phase": "env_new"})

    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    assert payload["old_env_sha256"] == old_sha
    assert payload["new_env_sha256"] == new_sha
    assert payload["phase"] == "env_new"
    assert payload["version"] == rotation._ROTATION_JOURNAL_VERSION


def test_pending_journal_unknown_env_sha_blocks_without_recovery(tmp_path: Path):
    env_path, _compose_path, paths = _rotation_fixture(
        tmp_path,
        "current-password-with-length",
    )
    rotation._prepare_private_state_dir(paths.rotation_dir)
    rotation._write_secret_backup(paths.backup_path, env_path.read_bytes())
    rotation._write_journal(
        paths.journal_path,
        _journal_payload(
            operation_id="op-1",
            phase="env_new",
            old_env_sha256="0" * 64,
            new_env_sha256="1" * 64,
        ),
    )

    try:
        rotation._recover_pending_journal(
            paths=paths,
            env_values={},
            runner=lambda *_: CommandResult(returncode=0),
            current_password="current-password-with-length",
            new_password="new-password-with-length",
        )
    except Exception as exc:
        assert "does not match the current .env" in str(exc)
    else:
        raise AssertionError("unknown .env sha must block recovery")


def test_committed_journal_residue_cleans_without_rollback(tmp_path: Path):
    env_path, _compose_path, paths = _rotation_fixture(
        tmp_path,
        "current-password-with-length",
    )
    rotation._prepare_private_state_dir(paths.rotation_dir)
    current_sha = rotation._sha256(env_path.read_bytes())
    rotation._write_secret_backup(paths.backup_path, env_path.read_bytes())
    rotation._write_journal(
        paths.journal_path,
        _journal_payload(
            operation_id="op-committed",
            phase="committed",
            old_env_sha256="0" * 64,
            new_env_sha256=current_sha,
        ),
    )

    with _patched_rotation_runtime(paths):
        result = rotation._recover_pending_journal(
            paths=paths,
            env_values={},
            runner=lambda *_: (_ for _ in ()).throw(AssertionError("rollback not expected")),
            current_password="old-password-with-length",
            new_password="current-password-with-length",
        )

    assert result is not None
    assert result.phase == "committed"
    assert not paths.journal_path.exists()


def test_old_env_with_forward_phase_fails_closed(tmp_path: Path):
    env_path, _compose_path, paths = _rotation_fixture(
        tmp_path,
        "current-password-with-length",
    )
    rotation._prepare_private_state_dir(paths.rotation_dir)
    current_sha = rotation._sha256(env_path.read_bytes())
    rotation._write_secret_backup(paths.backup_path, env_path.read_bytes())
    rotation._write_journal(
        paths.journal_path,
        _journal_payload(
            operation_id="op-bad-phase",
            phase="env_new",
            old_env_sha256=current_sha,
            new_env_sha256="1" * 64,
        ),
    )

    with pytest.raises(Exception, match="phase conflicts"):
        rotation._recover_pending_journal(
            paths=paths,
            env_values={},
            runner=lambda *_: CommandResult(returncode=0),
            current_password="current-password-with-length",
            new_password="new-password-with-length",
        )


def test_pending_forward_phase_recovery_records_rollback_sha_and_cleans_frozen(
    tmp_path: Path,
):
    current_password = "current-password-with-length"
    new_password = "new-password-with-length"
    env_path, _compose_path, paths = _rotation_fixture(tmp_path, current_password)
    rotation._prepare_private_state_dir(paths.rotation_dir)
    env_document = rotation._read_strict_env_document(env_path, expected_uid=paths.env_owner_uid)
    new_env = env_document.rewritten(
        {
            rotation.MAP_UI_PASSWORD_ENV: new_password,
            rotation.MAP_UI_PASSWORD_HASH_ENV: generate_map_pbkdf2_hash(new_password),
            rotation.MAP_UI_SESSION_SECRET_ENV: "new-session-secret-value-xxxxxxxxxxxx",
        }
    )
    rotation._write_secret_backup(paths.backup_path, env_document.original_bytes)
    rotation._write_private_file(
        paths.frozen_compose_path,
        b"services:\n  kor-travel-map-ui: {}\n",
        create_exclusive=True,
    )
    env_path.write_bytes(new_env)
    env_path.chmod(0o600)
    rotation._write_journal(
        paths.journal_path,
        _journal_payload(
            operation_id="op-recover-forward",
            phase="env_new",
            old_env_sha256=rotation._sha256(env_document.original_bytes),
            new_env_sha256=rotation._sha256(new_env),
        ),
    )

    def runner(*_args, **_kwargs) -> CommandResult:
        argv = _args[0]
        if argv[-1] == "config":
            return CommandResult(returncode=0, stdout="services:\n  kor-travel-map-ui: {}\n")
        return CommandResult(returncode=0)

    with _patched_rotation_runtime(paths):
        result = rotation._recover_pending_journal(
            paths=paths,
            env_values={},
            runner=runner,
            current_password=current_password,
            new_password=new_password,
        )

    assert result is not None
    assert result.phase == "recovered_pending_journal"
    assert not paths.journal_path.exists()
    assert not paths.backup_path.exists()
    assert not paths.frozen_compose_path.exists()
    assert not paths.recovery_path.exists()


def test_same_stdin_replay_after_env_new_crash_reaches_recovery_before_hash_check(
    tmp_path: Path,
):
    current_password = "current-password-with-length"
    new_password = "new-password-with-length"
    env_path, compose_path, paths = _rotation_fixture(tmp_path, current_password)
    rotation._prepare_private_state_dir(paths.rotation_dir)
    env_document = rotation._read_strict_env_document(env_path, expected_uid=paths.env_owner_uid)
    new_env = env_document.rewritten(
        {
            rotation.MAP_UI_PASSWORD_ENV: new_password,
            rotation.MAP_UI_PASSWORD_HASH_ENV: generate_map_pbkdf2_hash(new_password),
            rotation.MAP_UI_SESSION_SECRET_ENV: "new-session-secret-value-xxxxxxxxxxxx",
        }
    )
    rotation._write_secret_backup(paths.backup_path, env_document.original_bytes)
    env_path.write_bytes(new_env)
    env_path.chmod(0o600)
    rotation._write_journal(
        paths.journal_path,
        _journal_payload(
            operation_id="op-replay-env-new",
            phase="env_new",
            old_env_sha256=rotation._sha256(env_document.original_bytes),
            new_env_sha256=rotation._sha256(new_env),
        ),
    )

    def runner(*_args, **_kwargs) -> CommandResult:
        argv = _args[0]
        if argv[-1] == "config":
            return CommandResult(returncode=0, stdout="services:\n  kor-travel-map-ui: {}\n")
        return CommandResult(returncode=0)

    with _patched_rotation_runtime(paths):
        result = rotate_map_ui_auth(
            current_password=current_password,
            new_password=new_password,
            project_root=str(tmp_path),
            compose_path=str(compose_path),
            env_path=str(env_path),
            command_runner=runner,
            require_root=False,
        )

    assert result.phase == "recovered_pending_journal"
    assert not paths.journal_path.exists()


def test_same_stdin_replay_after_committed_crash_completes_terminal_audit(
    tmp_path: Path,
):
    current_password = "current-password-with-length"
    new_password = "new-password-with-length"
    env_path, compose_path, paths = _rotation_fixture(tmp_path, current_password)
    rotation._prepare_private_state_dir(paths.rotation_dir)
    env_document = rotation._read_strict_env_document(env_path, expected_uid=paths.env_owner_uid)
    new_env = env_document.rewritten(
        {
            rotation.MAP_UI_PASSWORD_ENV: new_password,
            rotation.MAP_UI_PASSWORD_HASH_ENV: generate_map_pbkdf2_hash(new_password),
            rotation.MAP_UI_SESSION_SECRET_ENV: "new-session-secret-value-xxxxxxxxxxxx",
        }
    )
    env_path.write_bytes(new_env)
    env_path.chmod(0o600)
    rotation._write_journal(
        paths.journal_path,
        _journal_payload(
            operation_id="op-replay-committed",
            phase="committed",
            old_env_sha256=rotation._sha256(env_document.original_bytes),
            new_env_sha256=rotation._sha256(new_env),
        ),
    )

    with _patched_rotation_runtime(paths):
        result = rotate_map_ui_auth(
            current_password=current_password,
            new_password=new_password,
            project_root=str(tmp_path),
            compose_path=str(compose_path),
            env_path=str(env_path),
            command_runner=lambda *_: (_ for _ in ()).throw(AssertionError("runner not expected")),
            require_root=False,
        )

    assert result.phase == "committed"
    assert not paths.journal_path.exists()


def test_rollback_prepared_resume_from_old_env_uses_recorded_recovery_bytes(
    tmp_path: Path,
):
    current_password = "current-password-with-length"
    new_password = "new-password-with-length"
    env_path, _compose_path, paths = _rotation_fixture(tmp_path, current_password)
    rotation._prepare_private_state_dir(paths.rotation_dir)
    env_document = rotation._read_strict_env_document(env_path, expected_uid=paths.env_owner_uid)
    new_env = env_document.rewritten(
        {
            rotation.MAP_UI_PASSWORD_ENV: new_password,
            rotation.MAP_UI_PASSWORD_HASH_ENV: generate_map_pbkdf2_hash(new_password),
            rotation.MAP_UI_SESSION_SECRET_ENV: "new-session-secret-value-xxxxxxxxxxxx",
        }
    )
    recovery_env = env_document.rewritten(
        {rotation.MAP_UI_SESSION_SECRET_ENV: "r" * 64}
    )
    rotation._write_secret_backup(paths.backup_path, env_document.original_bytes)
    rotation._write_private_file(paths.recovery_path, recovery_env, create_exclusive=True)
    rotation._write_journal(
        paths.journal_path,
        _journal_payload(
            operation_id="op-recover-old",
            phase="rollback_prepared",
            old_env_sha256=rotation._sha256(env_document.original_bytes),
            new_env_sha256=rotation._sha256(new_env),
            recovery_env_sha256=rotation._sha256(recovery_env),
        ),
    )

    def runner(*_args, **_kwargs) -> CommandResult:
        argv = _args[0]
        if argv[-1] == "config":
            return CommandResult(returncode=0, stdout="services:\n  kor-travel-map-ui: {}\n")
        return CommandResult(returncode=0)

    with _patched_rotation_runtime(paths):
        result = rotation._recover_pending_journal(
            paths=paths,
            env_values={},
            runner=runner,
            current_password=current_password,
            new_password=new_password,
        )

    assert result is not None
    assert result.phase == "recovered_pending_journal"
    assert rotation._sha256(env_path.read_bytes()) == rotation._sha256(recovery_env)
    assert not paths.recovery_path.exists()


def test_rolled_back_terminal_journal_finishes_cleanup_after_artifacts_unlinked(
    tmp_path: Path,
):
    current_password = "current-password-with-length"
    new_password = "new-password-with-length"
    env_path, _compose_path, paths = _rotation_fixture(tmp_path, current_password)
    rotation._prepare_private_state_dir(paths.rotation_dir)
    env_document = rotation._read_strict_env_document(env_path, expected_uid=paths.env_owner_uid)
    new_env = env_document.rewritten(
        {
            rotation.MAP_UI_PASSWORD_ENV: new_password,
            rotation.MAP_UI_PASSWORD_HASH_ENV: generate_map_pbkdf2_hash(new_password),
            rotation.MAP_UI_SESSION_SECRET_ENV: "new-session-secret-value-xxxxxxxxxxxx",
        }
    )
    recovery_env = env_document.rewritten(
        {rotation.MAP_UI_SESSION_SECRET_ENV: "r" * 64}
    )
    env_path.write_bytes(recovery_env)
    env_path.chmod(0o600)
    rotation._write_journal(
        paths.journal_path,
        _journal_payload(
            operation_id="op-rolled-back-terminal",
            phase="rolled_back",
            old_env_sha256=rotation._sha256(env_document.original_bytes),
            new_env_sha256=rotation._sha256(new_env),
            recovery_env_sha256=rotation._sha256(recovery_env),
        ),
    )

    with _patched_rotation_runtime(paths):
        result = rotation._recover_pending_journal(
            paths=paths,
            env_values={},
            runner=lambda *_: (_ for _ in ()).throw(AssertionError("runner not expected")),
            current_password=current_password,
            new_password=new_password,
        )

    assert result is not None
    assert result.phase == "rolled_back"
    assert not paths.journal_path.exists()


def test_orphan_backup_matching_current_env_is_cleared(tmp_path: Path):
    env_path, _compose_path, paths = _rotation_fixture(
        tmp_path,
        "current-password-with-length",
    )
    rotation._prepare_private_state_dir(paths.rotation_dir)
    rotation._write_secret_backup(paths.backup_path, env_path.read_bytes())

    result = rotation._recover_orphan_rotation_artifacts(paths)

    assert result is not None
    assert result.phase == "cleared_orphan_backup_on_current_env"
    assert not paths.backup_path.exists()


def test_orphan_backup_drift_fails_closed(tmp_path: Path):
    env_path, _compose_path, paths = _rotation_fixture(
        tmp_path,
        "current-password-with-length",
    )
    rotation._prepare_private_state_dir(paths.rotation_dir)
    rotation._write_secret_backup(paths.backup_path, env_path.read_bytes() + b"# drift\n")

    with pytest.raises(Exception, match="ambiguous orphan backup"):
        rotation._recover_orphan_rotation_artifacts(paths)


def test_orphan_recovery_env_without_journal_fails_closed(tmp_path: Path):
    env_path, _compose_path, paths = _rotation_fixture(
        tmp_path,
        "current-password-with-length",
    )
    rotation._prepare_private_state_dir(paths.rotation_dir)
    rotation._write_private_file(paths.recovery_path, env_path.read_bytes(), create_exclusive=True)

    with pytest.raises(Exception, match="stale recovery env"):
        rotation._recover_orphan_rotation_artifacts(paths)


def test_backup_env_document_rejects_duplicate_rotated_key(tmp_path: Path):
    env_path, _compose_path, paths = _rotation_fixture(
        tmp_path,
        "current-password-with-length",
    )
    evidence = rotation._capture_strict_child_file(
        env_path.parent,
        env_path.name,
        kind="env",
        expected_uid=paths.env_owner_uid,
    )
    duplicate = evidence.raw + b"KOR_TRAVEL_MAP_UI_SESSION_SECRET='duplicate'\n"

    with pytest.raises(Exception, match="duplicate Map UI auth keys"):
        rotation._env_document_from_bytes(
            duplicate,
            evidence.parent_stat,
            evidence.stat_result,
        )


def test_write_all_fd_retries_short_writes(monkeypatch):
    writes: list[bytes] = []

    def short_write(_fd: int, payload) -> int:
        chunk = bytes(payload[:3])
        writes.append(chunk)
        return len(chunk)

    monkeypatch.setattr(rotation.os, "write", short_write)

    rotation._write_all_fd(123, b"abcdefghi")

    assert writes == [b"abc", b"def", b"ghi"]


def test_write_all_fd_rejects_zero_progress(monkeypatch):
    monkeypatch.setattr(rotation.os, "write", lambda *_: 0)

    with pytest.raises(Exception, match="made no progress"):
        rotation._write_all_fd(123, b"abc")


def test_trusted_launcher_verifies_package_with_system_python_before_venv_exec():
    script = (Path(__file__).parents[2] / "scripts" / "ktdctl-map-ui-auth-rotate").read_text(
        encoding="utf-8"
    )

    verifier = '/usr/bin/python3 -I -S - "${PACKAGE_DIR}"'
    venv_exec = 'exec "${PYTHON_BIN}" -I -m kor_travel_docker_manager.cli'
    assert verifier in script
    assert script.index(verifier) < script.index(venv_exec)
    assert '"${PYTHON_BIN}" -I - "${PACKAGE_DIR}"' not in script
    assert "require_trusted_python" in script
    assert "trusted package path is writable or not root-owned" in script
    assert "site_packages, package_dir, dist_info, record" in script
    assert "wheel RECORD digest does not match release manifest" in script


def test_trusted_release_installer_uses_staged_git_archive_and_preserves_env():
    script = (Path(__file__).parents[2] / "scripts" / "install-ktdm-trusted-release").read_text(
        encoding="utf-8"
    )

    assert "/usr/bin/sudo -H -u \"#${source_uid}\" -- /usr/bin/git" in script
    assert "run_source_git archive --format=tar HEAD > \"${ARCHIVE}\"" in script
    assert "/usr/bin/install -o \"${env_uid}\" -g \"${env_gid}\" -m 0600" in script
    assert "-m pip wheel" in script
    assert "--wheel-dir \"${STAGING}/.wheelhouse\"" in script
    assert "KTDM_SOURCE_OWNER_UID=\"${source_uid}\" \\" in script
    assert "/usr/bin/python3 -I -S - \"${STAGING}\" \"${revision}\"" in script
    assert "--no-index" in script
    assert "--find-links \"${WHEELHOUSE}\"" in script
    assert "KTDM_BUILT_BACKEND_WHEEL" in script
    assert "backend_wheel_sha256" in script
    assert "wheel_record_sha256" in script
    assert "wheelhouse_sha256" in script
    assert "mv -T \"${APP_ROOT}\" \"${ROLLBACK}\"" in script
    assert "LAUNCHER_ROLLBACK" in script
    assert "rm -rf \"${APP_ROOT}\"" in script
    assert script.rindex("install-ktdctl-map-ui-auth-rotate") < script.rindex(
        "rm -rf \"${ROLLBACK}\""
    )
    assert "/usr/local/sbin/ktdctl-map-ui-auth-rotate --help >/dev/null" in script


def test_rotate_map_ui_auth_rewrites_three_env_keys_and_uses_sanitized_child_env(
    tmp_path: Path,
):
    current_password = "current-password-with-length"
    new_password = "new-password-with-length"
    env_path, compose_path, paths = _rotation_fixture(tmp_path, current_password)
    command_calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        stdin: str | None,
        timeout: int,
    ) -> CommandResult:
        assert stdin is None
        assert cwd == tmp_path
        assert timeout in {120, 180}
        command_calls.append((argv, dict(env)))
        if argv[-1] == "config":
            return CommandResult(returncode=0, stdout="services:\n  kor-travel-map-ui: {}\n")
        return CommandResult(returncode=0)

    with _patched_rotation_runtime(paths):
        result = rotate_map_ui_auth(
            current_password=current_password,
            new_password=new_password,
            project_root=str(tmp_path),
            compose_path=str(compose_path),
            env_path=str(env_path),
            command_runner=runner,
            require_root=False,
        )

    values = dotenv_values(env_path)
    assert result.success is True
    assert result.phase == "committed"
    assert values["KTDM_C6C_MAP_UI_ADMIN_PASSWORD"] == new_password
    assert verify_map_pbkdf2_hash(
        new_password,
        str(values["KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH"]),
    )
    assert values["KOR_TRAVEL_MAP_UI_SESSION_SECRET"] != "old-session-secret-value-xxxxxxxxxxxx"
    assert values["KOR_TRAVEL_MAP_UI_ADMIN_USERNAME"] == "map-ui-admin"
    assert len(command_calls) == 3
    assert command_calls[1][0] == [
        rotation._DOCKER_BIN,
        "compose",
        "-f",
        str(paths.frozen_compose_path),
        "config",
        "--quiet",
    ]
    up_command = command_calls[2][0]
    assert up_command[-11:] == [
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        "--no-build",
        "--pull",
        "never",
        "--wait",
        "--wait-timeout",
        "120",
        "kor-travel-map-ui",
    ]
    serialized_envs = json.dumps([env for _, env in command_calls], sort_keys=True)
    assert current_password not in serialized_envs
    assert new_password not in serialized_envs
    assert "KOR_TRAVEL_MAP_UI_SESSION_SECRET" not in serialized_envs
    assert "KOR_TRAVEL_MAP_UI_IMAGE" in serialized_envs


def test_rotate_map_ui_auth_rolls_back_with_fresh_session_after_recreate_failure(
    tmp_path: Path,
):
    current_password = "current-password-with-length"
    new_password = "new-password-with-length"
    env_path, compose_path, paths = _rotation_fixture(tmp_path, current_password)
    call_count = {"up": 0}

    def runner(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        stdin: str | None,
        timeout: int,
    ) -> CommandResult:
        del cwd, env, stdin, timeout
        if argv[-1] == "config":
            return CommandResult(returncode=0, stdout="services:\n  kor-travel-map-ui: {}\n")
        if "up" in argv:
            call_count["up"] += 1
            if call_count["up"] == 1:
                return CommandResult(returncode=1, stderr="compose failed")
        return CommandResult(returncode=0)

    with _patched_rotation_runtime(paths):
        result = rotate_map_ui_auth(
            current_password=current_password,
            new_password=new_password,
            project_root=str(tmp_path),
            compose_path=str(compose_path),
            env_path=str(env_path),
            command_runner=runner,
            require_root=False,
        )

    values = dotenv_values(env_path)
    assert result.success is False
    assert result.phase == "rolled_back"
    assert result.rollback_state == "rolled_back_password_state_with_irreversible_session_invalidation"
    assert values["KTDM_C6C_MAP_UI_ADMIN_PASSWORD"] == current_password
    assert verify_map_pbkdf2_hash(
        current_password,
        str(values["KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH"]),
    )
    assert values["KOR_TRAVEL_MAP_UI_SESSION_SECRET"] != "old-session-secret-value-xxxxxxxxxxxx"
    assert call_count["up"] == 2


def test_rollback_auth_uses_operator_current_password_not_stale_env_plaintext(
    tmp_path: Path,
):
    current_password = "current-password-with-length"
    stale_plaintext = "stale-password-with-length"
    new_password = "new-password-with-length"
    env_path, compose_path, paths = _rotation_fixture(tmp_path, current_password)
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(current_password, stale_plaintext),
        encoding="utf-8",
    )
    env_path.chmod(0o600)
    auth_passwords: list[str] = []
    call_count = {"up": 0}

    def runner(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        stdin: str | None,
        timeout: int,
    ) -> CommandResult:
        del cwd, env, stdin, timeout
        if argv[-1] == "config":
            return CommandResult(returncode=0, stdout="services:\n  kor-travel-map-ui: {}\n")
        if "up" in argv:
            call_count["up"] += 1
            if call_count["up"] == 1:
                return CommandResult(returncode=1, stderr="compose failed")
        return CommandResult(returncode=0)

    with _patched_rotation_runtime(paths, auth_passwords=auth_passwords):
        result = rotate_map_ui_auth(
            current_password=current_password,
            new_password=new_password,
            project_root=str(tmp_path),
            compose_path=str(compose_path),
            env_path=str(env_path),
            command_runner=runner,
            require_root=False,
        )

    values = dotenv_values(env_path)
    assert result.phase == "rolled_back"
    assert values["KTDM_C6C_MAP_UI_ADMIN_PASSWORD"] == stale_plaintext
    assert auth_passwords[-1] == current_password
    assert stale_plaintext not in auth_passwords


def _rotation_fixture(tmp_path: Path, current_password: str):
    env_path = tmp_path / ".env"
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("name: kor-travel-docker-manager\nservices: {}\n", encoding="utf-8")
    current_hash = generate_map_pbkdf2_hash(
        current_password,
        salt=b"o" * MAP_PBKDF2_SALT_BYTES,
    )
    env_path.write_text(
        "\n".join(
            [
                "KTDM_DEPLOYMENT_ENVIRONMENT=production",
                "PINVI_ENVIRONMENT=production",
                "KTDM_DOCKER_NETWORK_MODE=host",
                "COMPOSE_PROJECT_NAME=kor-travel-docker-manager",
                "KOR_TRAVEL_MAP_API_CONTAINER_PORT=12701",
                "KOR_TRAVEL_MAP_UI_PORT=12705",
                "PINVI_API_PORT=12801",
                "PINVI_WEB_PORT=12805",
                "export NON_TARGET=value",
                "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL=http://127.0.0.1:12701",
                "KTDM_PROD_URL_MAP=https://map.example.test",
                "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=true",
                f"KOR_TRAVEL_MAP_API_OPS_READ_TOKEN={'r' * 40}",
                f"KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN={'c' * 40}",
                "KTDM_C6C_CONTRACT_GENERATION=c6c-ops-v1",
                f"{rotation._MANAGER_SOURCE_REVISION_ENV}={'2' * 40}",
                "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME=map-ui-admin",
                f"KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH='{current_hash}'",
                "KOR_TRAVEL_MAP_UI_SESSION_SECRET='old-session-secret-value-xxxxxxxxxxxx'",
                f"KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET={'a' * 40}",
                f"KOR_TRAVEL_MAP_API_SERVICE_TOKEN={'s' * 40}",
                f"KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET={'u' * 40}",
                f"KTDM_C6C_MAP_UI_ADMIN_PASSWORD='{current_password}'",
                "KTDM_C6C_PINVI_ADMIN_EMAIL=pinvi-admin@example.test",
                f"KTDM_C6C_PINVI_ADMIN_PASSWORD={'p' * 40}",
                "KTDM_C6C_CANCEL_PROBE_JOB_ID=77777777-7777-4777-8777-777777777777",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)
    paths = RotationPaths(
        project_root=tmp_path,
        compose_path=compose_path,
        env_path=env_path,
        env_owner_uid=os.geteuid(),
        source_owner_uid=os.geteuid(),
        state_dir=tmp_path / "state",
        manifest_path=tmp_path / "state" / "compatible-pair-v4.json",
        lock_path=tmp_path / "state" / "global-mutation.lock",
        rotation_dir=tmp_path / "state" / "map-ui-auth-rotation",
        journal_path=tmp_path / "state" / "map-ui-auth-rotation" / "journal.json",
        backup_path=tmp_path / "state" / "map-ui-auth-rotation" / "env.backup",
        audit_path=tmp_path / "state" / "map-ui-auth-rotation" / "audit.jsonl",
        frozen_compose_path=tmp_path / "state" / "map-ui-auth-rotation" / "frozen-compose.yml",
        recovery_path=tmp_path / "state" / "map-ui-auth-rotation" / "env.recovery",
    )
    return env_path, compose_path, paths


def _patched_rotation_runtime(paths: RotationPaths, auth_passwords: list[str] | None = None):
    image_id = "sha256:" + "a" * 64
    manifest = SimpleNamespace(
        active=SimpleNamespace(
            map_image_id="sha256:" + "b" * 64,
            map_ui_image_id=image_id,
            map_dagster_image_id="sha256:" + "c" * 64,
            map_dagster_daemon_image_id="sha256:" + "d" * 64,
            pinvi_image_id="sha256:" + "e" * 64,
            map_source_revision="2" * 40,
            pinvi_source_revision="3" * 40,
            contract_generation="c6c-ops-v1",
        )
    )

    def relaxed_env_document(path: Path, *, expected_uid: int):
        evidence = rotation._capture_strict_child_file(
            path.parent,
            path.name,
            kind="env",
            expected_uid=expected_uid,
        )
        return rotation._env_document_from_bytes(
            evidence.raw,
            evidence.parent_stat,
            evidence.stat_result,
        )

    def verify_auth(**kwargs):
        if auth_passwords is not None:
            auth_passwords.append(kwargs["password"])
        return "ktm_admin_session=old-cookie"

    return patch.multiple(
        rotation,
        _rotation_paths=lambda **_: paths,
        _read_strict_env_document=relaxed_env_document,
        _validate_manager_source_evidence=lambda *_, **__: "2" * 40,
        validate_compose_candidate_protected_values=lambda *_, **__: (),
        validate_resolved_compose_candidate_protected_values=lambda *_, **__: (),
        validate_resolved_compose_secret_isolation=lambda *_: None,
        validate_resolved_compose_image_pair=lambda *_, **__: None,
        _validate_active_pair_runtime=lambda *_: None,
        load_pair_manifest=lambda _: manifest,
        _inspect_container=lambda _: {"Image": image_id, "Config": {}, "State": {}},
        _validate_map_ui_container=lambda *_, **__: None,
        _ui_stable_signature=lambda _: _STABLE_UI_SHA,
        _non_ui_snapshot=lambda _project: {"pinvi-api": _NON_UI_SHA},
        _assert_non_ui_unchanged=lambda *_: None,
        _assert_plaintext_absent=lambda *_, **__: None,
        _verify_auth_lifecycle=verify_auth,
    )


def test_write_frozen_compose_reuses_c6c_raw_and_resolved_validators(
    tmp_path: Path,
    monkeypatch,
):
    current_password = "current-password-with-length"
    env_path, _compose_path, paths = _rotation_fixture(tmp_path, current_password)
    active_image = "sha256:" + "f" * 64
    active_pair = SimpleNamespace(
        map_image_id="sha256:" + "1" * 64,
        map_ui_image_id=active_image,
        map_dagster_image_id="sha256:" + "2" * 64,
        map_dagster_daemon_image_id="sha256:" + "3" * 64,
        pinvi_image_id="sha256:" + "4" * 64,
        map_source_revision="2" * 40,
        pinvi_source_revision="3" * 40,
        contract_generation="c6c-ops-v1",
    )
    calls: dict[str, object] = {}

    def raw_validator(*args, **kwargs):
        candidate = args[0]
        calls["raw_candidate"] = candidate
        calls["raw_kwargs"] = kwargs
        return ("bind-snapshot",)

    def resolved_validator(*args, **kwargs):
        resolved = args[0]
        calls["resolved_candidate"] = resolved
        calls["resolved_kwargs"] = kwargs
        return ("bind-snapshot",)

    def isolation_validator(resolved, config):
        calls["isolation"] = (resolved, config.map_ui_password_hash)

    def pair_validator(resolved, config, pair):
        calls["pair"] = (resolved, config.contract_generation, pair.map_ui_image_id)

    def runner(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        stdin: str | None,
        timeout: int,
    ) -> CommandResult:
        del cwd, env, stdin, timeout
        if argv[-1] == "config":
            return CommandResult(returncode=0, stdout="services:\n  kor-travel-map-ui: {}\n")
        return CommandResult(returncode=0)

    monkeypatch.setattr(rotation, "validate_compose_candidate_protected_values", raw_validator)
    monkeypatch.setattr(
        rotation,
        "validate_resolved_compose_candidate_protected_values",
        resolved_validator,
    )
    monkeypatch.setattr(rotation, "validate_resolved_compose_secret_isolation", isolation_validator)
    monkeypatch.setattr(rotation, "validate_resolved_compose_image_pair", pair_validator)
    rotation._prepare_private_state_dir(paths.rotation_dir)

    rotation._write_frozen_compose(paths, active_pair, runner)

    raw_kwargs = calls["raw_kwargs"]
    resolved_kwargs = calls["resolved_kwargs"]
    assert isinstance(raw_kwargs, dict)
    assert isinstance(resolved_kwargs, dict)
    assert raw_kwargs["compose_path"] == str(paths.compose_path)
    assert raw_kwargs["root_env_path"] == str(env_path)
    assert raw_kwargs["environment"][rotation.MAP_UI_IMAGE_ENV] == active_image
    assert raw_kwargs["environment"][rotation.MAP_UI_PASSWORD_ENV] == current_password
    assert resolved_kwargs["environment"] == raw_kwargs["environment"]
    assert calls["resolved_candidate"] == {"services": {"kor-travel-map-ui": {}}}
    assert calls["isolation"][0] == calls["resolved_candidate"]
    assert calls["pair"] == (
        calls["resolved_candidate"],
        "c6c-ops-v1",
        active_image,
    )
    assert paths.frozen_compose_path.exists()


def test_active_pair_runtime_requires_canonical_container_name_and_generation(monkeypatch):
    image_id = "sha256:" + "a" * 64
    active_pair = SimpleNamespace(
        map_image_id=image_id,
        map_ui_image_id=image_id,
        map_dagster_image_id=image_id,
        map_dagster_daemon_image_id=image_id,
        pinvi_image_id=image_id,
        contract_generation="c6c-ops-v1",
    )
    env_values = {
        rotation.COMPOSE_PROJECT_ENV: "kor-travel-docker-manager",
        "KTDM_C6C_CONTRACT_GENERATION": "c6c-ops-v1",
    }

    def payload(container_name: str, service_name: str):
        return {
            "Image": image_id,
            "Name": f"/{container_name}",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "kor-travel-docker-manager",
                    "com.docker.compose.service": service_name,
                }
            },
            "State": {"Running": True, "Health": {"Status": "healthy"}},
        }

    monkeypatch.setattr(
        rotation,
        "_compose_project_snapshot",
        lambda *_args, **_kwargs: {
            service: {"_payload": payload(container, service)}
            for container, (service, _image_attr) in rotation._ACTIVE_PAIR_RUNTIME.items()
        },
    )

    rotation._validate_active_pair_runtime(active_pair, env_values)

    drifted_pair = SimpleNamespace(**{**active_pair.__dict__, "contract_generation": "c6c-ops-v2"})
    with pytest.raises(Exception, match="generation drifted"):
        rotation._validate_active_pair_runtime(drifted_pair, env_values)

    def drifted_snapshot(*_args, **_kwargs):
        snapshot = {
            service: {"_payload": payload(container, service)}
            for container, (service, _image_attr) in rotation._ACTIVE_PAIR_RUNTIME.items()
        }
        snapshot["kor-travel-map-ui"]["_payload"]["Name"] = "/wrong-name"
        return snapshot

    monkeypatch.setattr(rotation, "_compose_project_snapshot", drifted_snapshot)
    with pytest.raises(Exception, match="container name drifted"):
        rotation._validate_active_pair_runtime(active_pair, env_values)


def _ui_inspect_payload(
    *,
    container_id: str,
    config_hash: str,
    log_path: str,
    extra_bind: str | None = None,
):
    binds = ["/data:/data:ro"]
    if extra_bind is not None:
        binds.append(extra_bind)
    return {
        "Id": container_id,
        "Created": "volatile",
        "State": {"StartedAt": "volatile"},
        "NetworkSettings": {"Networks": {}},
        "GraphDriver": {"Data": {}},
        "MountLabel": "",
        "ProcessLabel": "",
        "ResolvConfPath": f"/var/lib/docker/containers/{container_id}/resolv.conf",
        "HostnamePath": f"/var/lib/docker/containers/{container_id}/hostname",
        "HostsPath": f"/var/lib/docker/containers/{container_id}/hosts",
        "LogPath": log_path,
        "Image": "sha256:" + "a" * 64,
        "Path": "node",
        "Args": ["server.js"],
        "HostConfig": {"Binds": binds},
        "Mounts": [{"Destination": "/data", "Source": "/data", "Mode": "ro"}],
        "Config": {
            "Hostname": container_id,
            "Image": "sha256:" + "a" * 64,
            "Env": [
                "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH=hash",
                "KOR_TRAVEL_MAP_UI_SESSION_SECRET=session",
                "OTHER=value",
            ],
            "Labels": {"com.docker.compose.config-hash": config_hash, "stable": "yes"},
        },
    }
