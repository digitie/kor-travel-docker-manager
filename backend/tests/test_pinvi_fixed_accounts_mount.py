"""PinVi 고정 관리자·테스트 사용자 파일이 bootstrap one-shot에 닿는지 센다.

`KTDM_PINVI_FIXED_ACCOUNTS_FILE`(호스트 절대 경로)이 있으면 `pinvi-admin-bootstrap` 실행에
read-only mount와 `PINVI_BOOTSTRAP_FIXED_ACCOUNTS_FILE`이 더해지고, 없으면 argv가 기존과 같다.
Manager는 파일 내용을 읽지 않는다 — 계정 값 검사는 PinVi CLI 몫이다.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.compose_service import (
    PINVI_FIXED_ACCOUNTS_FILE_ENV,
    ComposeService,
)

_BASE_VALUES = {
    "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
    "KTDM_C6C_PINVI_ADMIN_PASSWORD": "test-admin-password",
}


def _bootstrap_argv(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> list[str]:
    service = ComposeService()
    calls: list[list[str]] = []

    @contextmanager
    def credential_file(**kwargs: object):  # type: ignore[no-untyped-def]
        del kwargs
        yield SimpleNamespace(path=Path("/run/manager/bootstrap-admin.json"))

    def run_compose(args: list[str], *, transaction: object) -> dict[str, object]:
        del transaction
        calls.append(list(args))
        return {"success": True, "stdout": ""}

    monkeypatch.setattr(compose_service_module, "pinvi_bootstrap_credential_file", credential_file)
    monkeypatch.setattr(service, "_run_pinned_runtime_rebuild_compose", run_compose)
    service._run_pinvi_admin_bootstrap(
        transaction=cast(Any, SimpleNamespace()),
        state_paths=cast(Any, SimpleNamespace()),
        values=values,
        transaction_id="transaction-id",
    )
    assert len(calls) == 1
    return calls[0]


_LEGACY_ARGV = [
    "--profile",
    "bootstrap",
    "run",
    "--rm",
    "--no-deps",
    "-v",
    "/run/manager/bootstrap-admin.json:/run/pinvi/bootstrap-admin.json:ro",
    "-e",
    "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE=/run/pinvi/bootstrap-admin.json",
    "pinvi-admin-bootstrap",
]


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_unset_fixed_accounts_file_keeps_existing_argv(
    monkeypatch: pytest.MonkeyPatch, configured: str | None
) -> None:
    values = dict(_BASE_VALUES)
    if configured is not None:
        values[PINVI_FIXED_ACCOUNTS_FILE_ENV] = configured
    assert _bootstrap_argv(monkeypatch, values) == _LEGACY_ARGV


def test_configured_fixed_accounts_file_is_mounted_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_file = tmp_path / "pinvi-fixed-accounts.json"
    host_file.write_text("{}", encoding="utf-8")
    host_file.chmod(0o600)
    argv = _bootstrap_argv(
        monkeypatch, {**_BASE_VALUES, PINVI_FIXED_ACCOUNTS_FILE_ENV: str(host_file)}
    )

    assert argv[-1] == "pinvi-admin-bootstrap"
    assert argv[:-1] == [
        *_LEGACY_ARGV[:-1],
        "-v",
        f"{host_file}:/run/pinvi/fixed-accounts.json:ro",
        "-e",
        "PINVI_BOOTSTRAP_FIXED_ACCOUNTS_FILE=/run/pinvi/fixed-accounts.json",
    ]


def test_fixed_accounts_file_rejects_relative_missing_and_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    for configured in (
        "relative/fixed.json",
        str(tmp_path / "missing.json"),
        str(link),
        str(tmp_path),
        f"{real}:/etc/passwd",
    ):
        with pytest.raises(DeploymentContractError, match=PINVI_FIXED_ACCOUNTS_FILE_ENV):
            _bootstrap_argv(monkeypatch, {**_BASE_VALUES, PINVI_FIXED_ACCOUNTS_FILE_ENV: configured})
