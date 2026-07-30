from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dotenv import dotenv_values

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


def test_map_ui_auth_rotate_stdin_does_not_echo_secrets(capsys, monkeypatch):
    current_password = "current-password-with-length"
    new_password = "new-password-with-length"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

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

    with patch(
        "sys.stdin.readlines",
        return_value=[f"{current_password}\n", f"{new_password}\n"],
    ), patch("kor_travel_docker_manager.cli.rotate_map_ui_auth", return_value=Result()) as rotate:
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
    assert rotate.call_args.kwargs["project_root"] == "/srv/manager"
    combined = captured.out + captured.err
    assert current_password not in combined
    assert new_password not in combined


def test_project_root_uses_explicit_canonical_checkout(tmp_path: Path, monkeypatch):
    (tmp_path / "docker-compose.yml").write_text("name: test\n", encoding="utf-8")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", str(tmp_path))

    assert rotation._project_root() == tmp_path.resolve()


def test_project_root_uses_validated_current_working_directory(tmp_path: Path, monkeypatch):
    (tmp_path / "docker-compose.yml").write_text("name: test\n", encoding="utf-8")
    monkeypatch.delenv("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert rotation._project_root() == tmp_path.resolve()


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
    assert calls[0][0] == (["docker", "inspect", "kor-travel-map-ui-latest"],)
    assert calls[0][1]["text"] is True
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["timeout"] == 30


def test_compose_file_args_include_canonical_override(tmp_path: Path):
    compose_path = tmp_path / "docker-compose.yml"
    override_path = tmp_path / "docker-compose.override.yml"
    compose_path.write_text("name: base\n", encoding="utf-8")
    override_path.write_text("services: {}\n", encoding="utf-8")
    paths = RotationPaths(
        project_root=tmp_path,
        compose_path=compose_path,
        env_path=tmp_path / ".env",
        state_dir=tmp_path / "state",
        manifest_path=tmp_path / "state" / "compatible-pair-v4.json",
        lock_path=tmp_path / "state" / "global-mutation.lock",
        rotation_dir=tmp_path / "state" / "map-ui-auth-rotation",
        journal_path=tmp_path / "state" / "map-ui-auth-rotation" / "journal.json",
        backup_path=tmp_path / "state" / "map-ui-auth-rotation" / "env.backup",
        audit_path=tmp_path / "state" / "map-ui-auth-rotation" / "audit.jsonl",
    )

    assert rotation._compose_file_args(paths) == [
        "-f",
        str(compose_path),
        "-f",
        str(override_path.resolve()),
    ]


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

    assert rotation._ui_stable_signature(before) == rotation._ui_stable_signature(after)
    drifted = _ui_inspect_payload(
        container_id="new",
        config_hash="hash-new",
        log_path="/var/lib/docker/containers/new/new-json.log",
        extra_bind="/tmp:/tmp:ro",
    )
    assert rotation._ui_stable_signature(before) != rotation._ui_stable_signature(drifted)


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


def test_journal_phase_update_preserves_env_sha(tmp_path: Path):
    journal_path = tmp_path / "journal.json"

    rotation._write_journal(
        journal_path,
        {"old_env_sha256": "old", "new_env_sha256": "new", "phase": "prepared"},
    )
    rotation._write_journal(journal_path, {"phase": "env_new"})

    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    assert payload == {
        "old_env_sha256": "old",
        "new_env_sha256": "new",
        "phase": "env_new",
    }


def test_pending_journal_unknown_env_sha_blocks_without_recovery(tmp_path: Path):
    env_path, _compose_path, paths = _rotation_fixture(
        tmp_path,
        "current-password-with-length",
    )
    paths.rotation_dir.mkdir(parents=True)
    paths.backup_path.write_bytes(env_path.read_bytes())
    paths.journal_path.write_text(
        json.dumps({"old_env_sha256": "old", "new_env_sha256": "new"}),
        encoding="utf-8",
    )

    try:
        rotation._recover_pending_journal(
            paths=paths,
            env_values={},
            runner=lambda *_: CommandResult(returncode=0),
        )
    except Exception as exc:
        assert "does not match the current .env" in str(exc)
    else:
        raise AssertionError("unknown .env sha must block recovery")


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
        return CommandResult(returncode=0)

    with _patched_rotation_runtime(paths):
        result = rotate_map_ui_auth(
            current_password=current_password,
            new_password=new_password,
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
    assert len(command_calls) == 2
    up_command = command_calls[1][0]
    assert up_command[-9:] == [
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        "--no-build",
        "--pull",
        "never",
        "--wait",
        "--wait-timeout",
    ] or up_command[-11:] == [
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
        if "up" in argv:
            call_count["up"] += 1
            if call_count["up"] == 1:
                return CommandResult(returncode=1, stderr="compose failed")
        return CommandResult(returncode=0)

    with _patched_rotation_runtime(paths):
        result = rotate_map_ui_auth(
            current_password=current_password,
            new_password=new_password,
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
        state_dir=tmp_path / "state",
        manifest_path=tmp_path / "state" / "compatible-pair-v4.json",
        lock_path=tmp_path / "state" / "global-mutation.lock",
        rotation_dir=tmp_path / "state" / "map-ui-auth-rotation",
        journal_path=tmp_path / "state" / "map-ui-auth-rotation" / "journal.json",
        backup_path=tmp_path / "state" / "map-ui-auth-rotation" / "env.backup",
        audit_path=tmp_path / "state" / "map-ui-auth-rotation" / "audit.jsonl",
    )
    return env_path, compose_path, paths


def _patched_rotation_runtime(paths: RotationPaths):
    image_id = "sha256:" + "a" * 64
    manifest = SimpleNamespace(active=SimpleNamespace(map_ui_image_id=image_id))

    @contextmanager
    def lock_noop(_path: Path):
        yield

    def relaxed_env_document(path: Path):
        raw = path.read_bytes()
        return rotation._env_document_from_bytes(raw, path.stat())

    return patch.multiple(
        rotation,
        _rotation_paths=lambda **_: paths,
        _hardened_lock=lock_noop,
        _read_strict_env_document=relaxed_env_document,
        load_pair_manifest=lambda _: manifest,
        _inspect_container=lambda _: {"Image": image_id, "Config": {}, "State": {}},
        _validate_map_ui_container=lambda *_, **__: None,
        _ui_stable_signature=lambda _: "stable-ui",
        _non_ui_snapshot=lambda: {"pinvi-api-latest": {"Id": "1"}},
        _assert_non_ui_unchanged=lambda _: None,
        _assert_plaintext_absent=lambda *_, **__: None,
        _verify_auth_lifecycle=lambda **_: "ktm_admin_session=old-cookie",
    )


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
