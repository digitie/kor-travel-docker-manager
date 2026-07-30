from __future__ import annotations

import json
from unittest.mock import patch

from kor_travel_docker_manager.cli import build_parser, main
from kor_travel_docker_manager.services.map_ui_auth_rotation import (
    MAP_PBKDF2_DIGEST_BYTES,
    MAP_PBKDF2_ITERATIONS,
    MAP_PBKDF2_SALT_BYTES,
    generate_map_pbkdf2_hash,
    verify_map_pbkdf2_hash,
)


def test_map_ui_auth_rotate_parser_exists():
    parser = build_parser()

    args = parser.parse_args(["map-ui-auth", "rotate", "--password-stdin", "--json"])

    assert args.command == "map-ui-auth"
    assert args.map_ui_auth_action == "rotate"
    assert args.password_stdin is True
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

    with patch(
        "sys.stdin.readlines",
        return_value=[f"{current_password}\n", f"{new_password}\n"],
    ):
        code = main(["map-ui-auth", "rotate", "--password-stdin", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 2
    assert payload["phase"] == "not_implemented"
    combined = captured.out + captured.err
    assert current_password not in combined
    assert new_password not in combined
