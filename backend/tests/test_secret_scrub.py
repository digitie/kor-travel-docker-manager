"""실패 출력 스크러버 하나(ADR-51 잃는 보장 G) — 규칙과, 심은 비밀이 실패 출력 어디에도 없는지.

CLI는 `main()`을 그대로 부른다. 명령 처리기가 여러 종류의 예외를 던지게 하고 stdout·stderr 전부에서
심은 값을 찾는다 — 규칙 목록을 흉내 내지 않고 출력이라는 효과를 본다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from kor_travel_docker_manager import cli
from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.secret_scrub import (
    REDACTED,
    redact_secret_text,
    scrub_failure_text,
)

_ENV_TOKEN = "planted-token-9f3c71a2"
_ENV_SERVICE_KEY = "planted-svc-key-5d18e0b4"
_PROCESS_SECRET = "planted-process-secret-77aa"
_SQL_SECRET = "pa'ss-planted-42"


@pytest.fixture()
def planted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"KOR_TRAVEL_MAP_API_SERVICE_TOKEN={_ENV_TOKEN}\n"
        f"KOR_TRAVEL_MAP_DATA_GO_KR_SERVICE_KEY={_ENV_SERVICE_KEY}\n"
        f'KTDM_C6C_PINVI_ADMIN_PASSWORD="{_SQL_SECRET}"\n'
        "KOR_TRAVEL_GEO_PUBLIC_API_KEY_CACHE_TTL_S=30\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", str(env_file))
    monkeypatch.setenv("KTDM_LAUNCHER_SECRET", _PROCESS_SECRET)
    return env_file


_SECRETS = (_ENV_TOKEN, _ENV_SERVICE_KEY, _PROCESS_SECRET, _SQL_SECRET, _SQL_SECRET.replace("'", "''"))


def _assert_scrubbed(output: str) -> None:
    for secret in _SECRETS:
        assert secret not in output, secret


# --------------------------------------------------------------------------- 규칙


def test_the_rules(planted: Path) -> None:
    text = (
        f"token {_ENV_TOKEN} key {_ENV_SERVICE_KEY} env {_PROCESS_SECRET} "
        f"sql PASSWORD '{_SQL_SECRET.replace(chr(39), chr(39) * 2)}' "
        "dsn postgresql://ktm:dsn-pass-1234@127.0.0.1:12700/db ttl=30"
    )

    scrubbed = scrub_failure_text(text, planted)

    _assert_scrubbed(scrubbed)
    assert "dsn-pass-1234" not in scrubbed
    assert "postgresql://ktm:<redacted>@127.0.0.1:12700/db" in scrubbed
    # 4자 미만 값은 가리지 않는다 — `ttl=30`이 `ttl=<redacted>`가 되면 원인 문구가 망가진다.
    assert "ttl=30" in scrubbed


def test_longer_secrets_are_replaced_first() -> None:
    environment = {"A_TOKEN": "abcd", "B_TOKEN": "abcdefgh"}

    assert redact_secret_text("x abcdefgh y", environment) == f"x {REDACTED} y"


def test_extra_values_are_scrubbed_too() -> None:
    assert redact_secret_text("run secret-xyz-123", {}, ("secret-xyz-123",)) == f"run {REDACTED}"


def test_an_unreadable_env_withholds_the_text(tmp_path: Path) -> None:
    unreadable = tmp_path / ".env"
    unreadable.write_bytes(b"KOR_TRAVEL_MAP_API_SERVICE_TOKEN=\xff\xfe\n")

    scrubbed = scrub_failure_text(f"cause {_ENV_TOKEN}", unreadable)

    assert _ENV_TOKEN not in scrubbed
    assert "withheld" in scrubbed


# --------------------------------------------------------------------------- CLI


def _pinvi_pair_failures() -> list[BaseException]:
    chained = DeploymentContractError("outer contract failure")
    chained.__cause__ = RuntimeError(f"inner cause names {_ENV_TOKEN}")
    return [
        DeploymentContractError(f"contract refused; saw {_ENV_SERVICE_KEY}"),
        ValueError(f"bad value {_PROCESS_SECRET}"),
        OSError(f"cannot open {_ENV_TOKEN}"),
        subprocess.TimeoutExpired(cmd=["psql", f"PASSWORD '{_SQL_SECRET}'"], timeout=5),
        yaml.MarkedYAMLError(problem=f"bad yaml near {_ENV_SERVICE_KEY}"),
        chained,
    ]


@pytest.mark.parametrize("failure", _pinvi_pair_failures(), ids=lambda exc: type(exc).__name__)
@pytest.mark.parametrize("json_output", [False, True])
def test_no_rebuild_failure_output_carries_a_planted_secret(
    planted: Path,
    capsys: pytest.CaptureFixture[str],
    failure: BaseException,
    json_output: bool,
) -> None:
    """재구축이 어떤 예외로 실패하든 원인은 보이고 심은 비밀은 stdout·stderr 어디에도 없다."""

    argv = ["pinvi-pair", "rebuild-pinned", "--confirm", *(["--json"] if json_output else [])]
    with patch.object(cli.compose_service, "rebuild_pinned_runtime", side_effect=failure):
        status = cli.main(argv)

    captured = capsys.readouterr()
    assert status == 2
    _assert_scrubbed(captured.out + captured.err)
    assert type(failure).__name__ in captured.err
    if json_output:
        assert '"status": "failed"' in captured.out


def test_an_unhandled_command_failure_is_scrubbed(
    planted: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """명령 처리기가 잡지 않은 예외도 가린 원문으로 끝난다 — 기본 traceback이 비밀을 싣고 가지 않는다."""

    def broken(_args: object) -> int:
        raise RuntimeError(f"invariant broke near {_ENV_TOKEN}")

    monkeypatch.setattr(cli, "_cmd_targets_list", broken)

    status = cli.main(["targets", "list"])

    captured = capsys.readouterr()
    assert status == 1
    assert "RuntimeError: invariant broke near" in captured.err
    _assert_scrubbed(captured.out + captured.err)


def test_a_targets_config_failure_is_scrubbed(
    planted: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config 읽기 실패(OSError·YAML)는 한 줄로 끝나되 그 한 줄도 가린다."""

    def broken(_args: object) -> int:
        raise OSError(f"cannot read targets near {_ENV_SERVICE_KEY}")

    monkeypatch.setattr(cli, "_cmd_targets_list", broken)

    with pytest.raises(SystemExit) as exited:
        cli.main(["targets", "list"])

    captured = capsys.readouterr()
    assert exited.value.code == 1
    assert "cannot read targets" in captured.err
    _assert_scrubbed(captured.out + captured.err)


def test_compose_process_output_is_scrubbed(planted: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli._emit_process_result(
        {
            "returncode": 1,
            "stdout": f"started with {_ENV_TOKEN}",
            "stderr": f"error: required variable {_ENV_SERVICE_KEY} is missing",
        }
    )

    captured = capsys.readouterr()
    assert "required variable" in captured.err
    _assert_scrubbed(captured.out + captured.err)
