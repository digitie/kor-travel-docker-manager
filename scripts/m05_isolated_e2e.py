#!/usr/bin/env python3
"""M05 disposable bridge E2E의 root-only one-shot driver.

Docker/Playwright 원문 출력이나 secret은 result에 쓰지 않는다. 이 파일은 trusted
Manager release에서만 ``run-m05-isolated-e2e-once``를 통해 실행한다.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import secrets
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    ProxyHandler,
    Request,
    build_opener,
)

from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
    effective_environment,
)
from kor_travel_docker_manager.services.loopback_readiness import (
    LOOPBACK_HTTP_READINESS_ATTEMPTS,
    LOOPBACK_HTTP_READINESS_RETRY_SECONDS,
)
from kor_travel_docker_manager.services.m05_isolated_harness import (
    M05IsolatedHarnessPlan,
    M05IsolatedNetworkExpectation,
    M05IsolatedPairEvidence,
    M05IsolatedRuntimeExpectation,
    M05IsolatedServiceExpectation,
    assert_m05_isolated_runtime,
    build_m05_isolated_manager_admission,
    build_m05_isolated_runtime_provenance,
    claim_m05_isolated_harness_ledger,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    pinned_runtime_state_paths,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    current_pinned_runtime_release,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    materialize_pinned_runtime_sources,
)
from kor_travel_docker_manager.services.runtime_execution_registry import (
    RuntimeExecutionRegistry,
    RuntimeExecutionRegistryError,
    block_current_execution,
    load_runtime_execution_registry,
    write_runtime_execution_registry,
)
from kor_travel_docker_manager.services.runtime_pin_registry import (
    RuntimePinRegistryError,
    load_runtime_pin_registry,
)

# pinned revision은 코드 상수가 아니라 root 소유 registry가 소유한다(ADR-40).
# 이 드라이버는 한 번의 격리 실행 전체가 같은 pinset에 결박돼야 하므로 모듈 로드
# 시점에 한 번만 해석한다 — 실행 도중 회전이 끼어들면 전후가 다른 pinset이 된다.
PINNED_RUNTIME_RELEASE = current_pinned_runtime_release()
_CleanupProject = tuple[Path, str, Path, tuple[Path, ...], tuple[str, ...]]

_ROOT = Path("/opt/kor-travel-docker-manager")
_LEDGER = Path("/var/lib/kor-travel-docker-manager/m05-isolated-once")
_REVISION_LENGTH = 40
_RENDERED_PORT_EVIDENCE_LIMIT = 16
_SAFE_PORT_PROTOCOLS = frozenset({"tcp", "udp", "sctp"})
_FORENSIC_CAPTURE_ENV = "KTDM_M05_FORENSIC_CAPTURE"
_FORENSIC_CAPTURE_LIMIT = 256_000
# Compose config은 trusted input이라도 외부 CLI 출력이다. JSON parser에 넘기는
# 원문은 이 상한만 보관하고, 초과분도 끝까지 drain해 child pipe를 막지 않는다.
_COMPOSE_CONFIG_OUTPUT_LIMIT = 256_000
_RAW_ENV_NAMES = (
    "M05_MAP_ADMIN_PROXY_SECRET",
    "M05_PINVI_EMAIL",
    "M05_PINVI_PASSWORD",
    "PINVI_M04_LIVE_EMAIL",
    "PINVI_M04_LIVE_PASSWORD",
)
_PINVI_MANAGER_ADMISSION_FILES = (
    "scripts/docker-app.sh",
    "scripts/m05_isolated_manager_admission.py",
)
_PINVI_MANAGER_ADMISSION_TOKENS = frozenset(
    {
        "PINVI_M05_ISOLATED_MANAGER_ADMISSION_PATH",
        "PINVI_M05_PINSET_SHA256",
        "PINVI_M05_EXECUTION_IDENTITY_SHA256",
        "m05_isolated_manager_admission.py",
        "pinvi-m05-isolated-manager-admission-v1",
        '[[ "$EUID" -eq 0 ]]',
        "/usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/python3 -I",
    }
)
_SAFE_SUBPROCESS_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
# Root driver가 host loopback에만 연결할 때에도 ambient HTTP(S)_PROXY를 신뢰하지
# 않는다. PinVi cookie opener도 아래와 같은 proxy-free opener를 명시적으로 만든다.
_LOOPBACK_OPENER = build_opener(ProxyHandler({}))
_MAP_FRESH_INIT_EXIT_DIAGNOSTICS = {
    41: "migrator_dsn_missing",
    42: "image_alembic_root_invalid",
    43: "migrator_session_unverifiable",
    44: "migrator_identity_invalid",
    45: "pre_root_state_invalid",
    46: "alembic_root_result_invalid",
    47: "alembic_command_failed",
    48: "alembic_runtime_contract_failed",
    49: "database_statement_failed",
    50: "runtime_privilege_reconciliation_failed",
    51: "fresh_destination_contract_invalid",
    52: "alembic_runtime_configuration_invalid",
    53: "baseline_reference_invalid",
    54: "schema_lineage_invalid",
    55: "metadata_contract_invalid",
    127: "unclassified",
}
# terminal pinset registry는 비-root도 읽는 감사 표면이다. driver의 예외 원문을
# reason에 흘리지 않고, 다음 immutable candidate의 보정 범위만 나타내는 고정 phase만
# 허용한다. 이 집합 밖의 값은 가장 좁은 안전 진단으로 수렴한다.
_PUBLIC_TERMINAL_PHASES = frozenset(
    {
        "admission",
        "driver_contract_failed",
        "ledger_claim",
        "m04_fixture_http_failed",
        "m04_fixture_invalid",
        "m04_m05_e2e",
        "m04_map_approval_http_failed",
        "m04_map_approval_invalid",
        "m05_case_decision_http_failed",
        "m05_case_invalid",
        "m05_case_lookup_http_failed",
        "m05_fixture_invalid",
        "m05_pinvi_receipt_blocked",
        "m05_pinvi_receipt_http_failed",
        "m05_pinvi_receipt_invalid",
        "map_application_start_failed",
        "map_fresh_init_failed",
        "map_health_status_failed",
        "map_health_transport_failed",
        "map_postgres_start_failed",
        "map_runtime",
        "map_subscription",
        "map_subscription_http_failed",
        "network_inspect_invalid",
        "network_subnet_unavailable",
        "pair_contract_invalid",
        "pinvi_auth_invalid",
        "pinvi_login_http_failed",
        "pinvi_manager_admission_contract_invalid",
        "pinvi_runtime",
        "ports_unavailable",
        "result_write_failed",
        "runtime_cleanup_failed",
        "runtime_command_failed",
        "runtime_container_identity_invalid",
        "runtime_directory_invalid",
        "runtime_http_contract_failed",
        "runtime_http_failed",
        "runtime_http_url_invalid",
        "runtime_image_identity_invalid",
        "runtime_inspect_invalid",
        "runtime_loopback_publish_invalid",
        "runtime_loopback_publish_config_invalid",
        "runtime_execution_block_failed",
        "runtime_execution_registry_changed",
        "runtime_execution_registry_invalid",
        "runtime_pin_registry_changed",
        "runtime_pin_registry_invalid",
        "runtime_setup",
        "runtime_setup_admission",
        "runtime_setup_admission_build",
        "runtime_setup_admission_write",
        "runtime_setup_credentials",
        "runtime_setup_map_config",
        "runtime_setup_network",
        "runtime_setup_pinvi_config",
        "runtime_setup_ports",
        "runtime_setup_workspace",
        "secret_cleanup_identity_invalid",
        "source_materialization",
        "terminal_execution_blocked",
        "trusted_release_invalid",
        "trusted_release_revision_mismatch",
    }
)


class _PhaseError(RuntimeError):
    def __init__(
        self,
        phase: str,
        *,
        diagnostic: str | None = None,
        returncode: int | None = None,
        stderr: bytes | None = None,
        stdout: bytes | None = None,
        stdout_truncated: bool = False,
    ) -> None:
        super().__init__(phase)
        self.phase = phase
        self.diagnostic = diagnostic
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        self.stdout_truncated = stdout_truncated


def _fail(
    phase: str,
    *,
    diagnostic: str | None = None,
    returncode: int | None = None,
    stderr: bytes | None = None,
    stdout: bytes | None = None,
    stdout_truncated: bool = False,
) -> NoReturn:
    raise _PhaseError(
        phase,
        diagnostic=diagnostic,
        returncode=returncode,
        stderr=stderr,
        stdout=stdout,
        stdout_truncated=stdout_truncated,
    )


def _assert_current_m05_execution_is_runnable(
    expected_manager_revision: str,
) -> RuntimeExecutionRegistry:
    """현재 source pair와 trusted Manager 실행 결박을 mutation 전에 확인한다."""

    try:
        from kor_travel_docker_manager.services.runtime_pair_rotation import (
            require_no_pending_runtime_pair_rotation,
        )

        require_no_pending_runtime_pair_rotation()
        registry = load_runtime_pin_registry()
    except (RuntimePinRegistryError, DeploymentContractError):
        _fail("runtime_pin_registry_invalid")
    if (
        registry.pinset_sha256 != PINNED_RUNTIME_RELEASE.pinset_sha256
        or registry.map_revision != PINNED_RUNTIME_RELEASE.source_for("map").revision
        or registry.pinvi_revision != PINNED_RUNTIME_RELEASE.source_for("pinvi").revision
    ):
        _fail("runtime_pin_registry_changed")
    try:
        execution = load_runtime_execution_registry()
    except RuntimeExecutionRegistryError:
        _fail("runtime_execution_registry_invalid")
    if not execution.current_matches(
        pins=registry, manager_source_revision=expected_manager_revision
    ):
        _fail("runtime_execution_registry_changed")
    if execution.is_unconditionally_blocked_current():
        _fail("terminal_execution_blocked")
    return execution


def _terminal_registry_reason(phase: str) -> str:
    """root registry에는 고정 phase만 남겨 원문 유출을 막는다."""

    return f"M05 isolated one-shot terminal: {_public_terminal_phase(phase)}"


def _public_terminal_phase(phase: str) -> str:
    """원문 없이 이미 추적 중인 실행 경계만 public receipt에 남긴다."""

    return phase if phase in _PUBLIC_TERMINAL_PHASES else "driver_contract_failed"


def _block_terminal_m05_execution(phase: str, *, expected_manager_revision: str) -> bool:
    """terminal result를 현재 v6 execution의 unconditional block과 결박한다."""

    try:
        pins = load_runtime_pin_registry()
        registry = load_runtime_execution_registry()
        if not registry.current_matches(
            pins=pins, manager_source_revision=expected_manager_revision
        ):
            return False
        updated = block_current_execution(
            registry=registry, reason=_terminal_registry_reason(phase)
        )
        write_runtime_execution_registry(updated)
    except (RuntimePinRegistryError, RuntimeExecutionRegistryError):
        return False
    return updated.is_unconditionally_blocked_current()


def _root_file(path: Path, *, mode: int = 0o600) -> os.stat_result:
    data = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(data.st_mode)
        or data.st_uid != 0
        or stat.S_IMODE(data.st_mode) != mode
        or data.st_nlink != 1
    ):
        _fail("trusted_release_invalid")
    return data


def _secure_read_root_file(path: Path, *, mode: int, encoding: str, limit: int) -> str:
    """Read a root-owned immutable marker without a check/read substitution window."""

    before = _root_file(path, mode=mode)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("trusted_release_invalid")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (
            (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != mode
            or opened.st_nlink != 1
        ):
            _fail("trusted_release_invalid")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                _fail("trusted_release_invalid")
        after = os.fstat(fd)
        named = path.lstat()
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino) or (
            named.st_dev,
            named.st_ino,
        ) != (before.st_dev, before.st_ino):
            _fail("trusted_release_invalid")
        return b"".join(chunks).decode(encoding)
    except UnicodeDecodeError:
        _fail("trusted_release_invalid")
    finally:
        os.close(fd)


def _validate_trusted_release(expected: str) -> None:
    if len(expected) != _REVISION_LENGTH or any(
        char not in "0123456789abcdef" for char in expected
    ):
        _fail("arguments_invalid")
    root = _ROOT.lstat()
    if (
        _ROOT.is_symlink()
        or not stat.S_ISDIR(root.st_mode)
        or root.st_uid != 0
        or stat.S_IMODE(root.st_mode) & 0o022
    ):
        _fail("trusted_release_invalid")
    revision_file = _ROOT / ".ktdm-source-revision"
    manifest_file = _ROOT / ".ktdm-release-manifest.json"
    revision = _secure_read_root_file(
        revision_file, mode=0o644, encoding="ascii", limit=128
    ).strip()
    try:
        manifest = json.loads(
            _secure_read_root_file(
                manifest_file, mode=0o644, encoding="utf-8", limit=1_000_000
            )
        )
    except json.JSONDecodeError:
        _fail("trusted_release_invalid")
    if (
        revision != expected
        or not isinstance(manifest, dict)
        or manifest.get("manager_source_revision") != expected
    ):
        _fail("trusted_release_revision_mismatch")


def _write_private_json(path: Path, value: Mapping[str, object]) -> str:
    raw = (
        json.dumps(dict(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    _write_private_bytes(path, raw)
    return hashlib.sha256(raw).hexdigest()


def _write_private_bytes(path: Path, raw: bytes) -> None:
    if not raw:
        _fail("result_write_failed")
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                _fail("result_write_failed")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_private_text(path: Path, value: str) -> None:
    _write_private_bytes(path, value.encode("utf-8"))


def _command(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = False,
    failure_exit_diagnostics: dict[int, str] | None = None,
    capture_failure_stderr: bool = False,
    capture_output_limit: int | None = None,
) -> str:
    child_env = dict(_SAFE_SUBPROCESS_ENV)
    if env is not None:
        child_env.update(env)
    if capture_failure_stderr or capture_output_limit is not None:
        stdout, returncode, stderr, stdout_bytes, stdout_truncated = _run_with_bounded_output(
            args,
            cwd=cwd,
            env=child_env,
            capture=capture,
            capture_stderr=capture_failure_stderr,
            stdout_limit=capture_output_limit,
        )
    else:
        completed = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd is not None else "/",
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
        stdout = completed.stdout if capture else ""
        returncode = completed.returncode
        stderr = None
        stdout_bytes = None
        stdout_truncated = False
    if returncode != 0:
        diagnostic = (
            failure_exit_diagnostics.get(returncode)
            if failure_exit_diagnostics is not None
            else None
        )
        _fail(
            "runtime_command_failed",
            diagnostic=diagnostic,
            returncode=returncode,
            stderr=stderr,
        )
    if stdout_truncated:
        _fail(
            "runtime_command_output_too_large",
            stdout=stdout_bytes,
            stdout_truncated=True,
        )
    return stdout


def _run_with_bounded_output(
    args: tuple[str, ...],
    *,
    cwd: Path | None,
    env: dict[str, str],
    capture: bool,
    capture_stderr: bool,
    stdout_limit: int | None,
) -> tuple[str, int, bytes | None, bytes | None, bool]:
    """Bound captured child streams while draining every byte needed to avoid pipe stalls."""

    process = subprocess.Popen(
        list(args),
        cwd=str(cwd) if cwd is not None else "/",
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture_stderr else subprocess.DEVNULL,
    )
    captured_stderr = bytearray()

    def drain_stderr() -> None:
        assert process.stderr is not None
        while chunk := process.stderr.read(65_536):
            remaining = _FORENSIC_CAPTURE_LIMIT - len(captured_stderr)
            if remaining > 0:
                captured_stderr.extend(chunk[:remaining])

    reader = (
        threading.Thread(target=drain_stderr, daemon=True) if capture_stderr else None
    )
    if reader is not None:
        reader.start()
    captured_stdout = bytearray()
    stdout_truncated = False
    if capture:
        assert process.stdout is not None
        while chunk := process.stdout.read(65_536):
            if stdout_limit is None:
                captured_stdout.extend(chunk)
                continue
            remaining = stdout_limit - len(captured_stdout)
            if remaining > 0:
                captured_stdout.extend(chunk[:remaining])
            if len(chunk) > remaining:
                stdout_truncated = True
        stdout_bytes: bytes | None = bytes(captured_stdout)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
    else:
        stdout = ""
        stdout_bytes = None
    returncode = process.wait()
    if reader is not None:
        reader.join()
    return stdout, returncode, bytes(captured_stderr) if capture_stderr else None, stdout_bytes, stdout_truncated


def _compose(
    *,
    root: Path,
    project: str,
    env_file: Path,
    files: tuple[Path, ...],
    arguments: tuple[str, ...],
    capture: bool = False,
    environment: dict[str, str] | None = None,
    failure_phase: str | None = None,
    failure_exit_diagnostics: dict[int, str] | None = None,
    failure_evidence_path: Path | None = None,
    output_evidence_path: Path | None = None,
) -> str:
    command = [
        "/usr/bin/docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(env_file),
    ]
    for item in files:
        command.extend(("--file", str(item)))
    command.extend(arguments)
    try:
        return _command(
            *command,
            cwd=root,
            env=environment,
            capture=capture,
            failure_exit_diagnostics=failure_exit_diagnostics,
            capture_failure_stderr=(
                failure_evidence_path is not None
                and os.environ.get(_FORENSIC_CAPTURE_ENV) == "1"
            ),
            capture_output_limit=(
                _COMPOSE_CONFIG_OUTPUT_LIMIT if output_evidence_path is not None else None
            ),
        )
    except _PhaseError as error:
        if failure_evidence_path is not None and error.phase == "runtime_command_failed":
            _write_compose_failure_evidence(
                failure_evidence_path,
                returncode=error.returncode,
                stderr=error.stderr,
            )
        if (
            output_evidence_path is not None
            and error.phase == "runtime_command_output_too_large"
        ):
            _write_compose_output_evidence(
                output_evidence_path,
                output=error.stdout or b"",
                truncated=error.stdout_truncated,
            )
        if failure_phase is not None and error.phase in {
            "runtime_command_failed",
            "runtime_command_output_too_large",
        }:
            _fail(failure_phase, diagnostic=error.diagnostic)
        raise


def _write_compose_failure_evidence(
    path: Path, *, returncode: int | None, stderr: bytes | None
) -> None:
    """Persist fixed failure metadata; raw stderr requires an explicit root forensic opt-in."""

    if not isinstance(returncode, int) or returncode < 1 or returncode > 255:
        safe_returncode: int | None = None
    else:
        safe_returncode = returncode
    _write_private_json(
        path,
        {"kind": "compose_config", "returncode": safe_returncode, "version": 1},
    )
    if os.environ.get(_FORENSIC_CAPTURE_ENV) != "1" or stderr is None:
        return
    _write_private_bytes(
        path.with_suffix(".stderr"), stderr[:_FORENSIC_CAPTURE_LIMIT] or b"\n"
    )


def _write_command_failure_evidence(
    path: Path, *, returncode: int | None, stderr: bytes | None
) -> None:
    """Persist a bounded generic external-command receipt without command or env disclosure."""

    if not isinstance(returncode, int) or returncode < 1 or returncode > 255:
        safe_returncode: int | None = None
    else:
        safe_returncode = returncode
    _write_private_json(
        path,
        {"kind": "runtime_command", "returncode": safe_returncode, "version": 1},
    )
    if os.environ.get(_FORENSIC_CAPTURE_ENV) != "1" or stderr is None:
        return
    _write_private_bytes(
        path.with_suffix(".stderr"), stderr[:_FORENSIC_CAPTURE_LIMIT] or b"\n"
    )


def _write_compose_output_evidence(
    path: Path, *, output: str | bytes, truncated: bool = False
) -> None:
    """Keep a fixed parse-failure marker; raw successful-command output remains opt-in only."""

    _write_private_json(
        path,
        {
            "kind": "compose_config_output",
            "truncated": truncated,
            "version": 1,
        },
    )
    if os.environ.get(_FORENSIC_CAPTURE_ENV) != "1":
        return
    raw = output if isinstance(output, bytes) else output.encode("utf-8", errors="replace")
    raw = raw[:_FORENSIC_CAPTURE_LIMIT]
    _write_private_bytes(path.with_suffix(".stdout"), raw or b"\n")


def _unlink_private(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
        _fail("secret_cleanup_identity_invalid")
    path.unlink()
    directory_fd = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _root_directory(path: Path, *, mode: int = 0o700) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        _fail("runtime_directory_invalid")


def _cleanup_project(
    *,
    root: Path,
    project: str,
    env_file: Path,
    files: tuple[Path, ...],
    profiles: tuple[str, ...] = (),
) -> None:
    profile_arguments = tuple(
        item for profile in profiles for item in ("--profile", profile)
    )
    try:
        _compose(
            root=root,
            project=project,
            env_file=env_file,
            files=files,
            arguments=(*profile_arguments, "down", "--volumes", "--remove-orphans"),
        )
    except _PhaseError:
        _fail("runtime_cleanup_failed")
    remaining = _command(
        "/usr/bin/docker",
        "ps",
        "--all",
        "--quiet",
        "--filter",
        f"label=com.docker.compose.project={project}",
        capture=True,
    ).strip()
    networks = _command(
        "/usr/bin/docker",
        "network",
        "ls",
        "--quiet",
        "--filter",
        f"label=com.docker.compose.project={project}",
        capture=True,
    ).strip()
    volumes = _command(
        "/usr/bin/docker",
        "volume",
        "ls",
        "--quiet",
        "--filter",
        f"label=com.docker.compose.project={project}",
        capture=True,
    ).strip()
    if remaining or networks or volumes:
        _fail("runtime_cleanup_failed")


def _cleanup_temporary_resources(
    *,
    map_cleanup: _CleanupProject | None,
    pinvi_cleanup: _CleanupProject | None,
    private_files: tuple[Path, ...],
) -> tuple[bool, bool]:
    """정상 cleanup failure와 receipt로 수렴해야 할 unexpected failure를 분리한다."""

    cleanup_failed = False
    unexpected_failure = False
    for cleanup in (pinvi_cleanup, map_cleanup):
        if cleanup is None:
            continue
        try:
            _cleanup_project(
                root=cleanup[0],
                project=cleanup[1],
                env_file=cleanup[2],
                files=cleanup[3],
                profiles=cleanup[4],
            )
        except _PhaseError:
            cleanup_failed = True
        except Exception:  # noqa: BLE001 - fixed terminal receipt boundary
            unexpected_failure = True
    for path in private_files:
        try:
            _unlink_private(path)
        except _PhaseError:
            cleanup_failed = True
        except Exception:  # noqa: BLE001 - fixed terminal receipt boundary
            unexpected_failure = True
    return cleanup_failed, unexpected_failure


def _random_secret() -> str:
    return secrets.token_urlsafe(36)


def _pbkdf2_password_hash(value: str) -> str:
    """Map frontend가 요구하는 portable PBKDF2 형식으로 isolated admin 비밀번호를 봉인한다."""

    import base64

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, 310_000)
    encode = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"pbkdf2_sha256$310000${encode(salt)}${encode(digest)}"


def _map_fresh_init_diagnostic_runner() -> str:
    """Map source 오류를 원문 없이 고정 종료 코드로만 분류하는 one-shot runner."""

    error_codes = {
        "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN is required": 41,
        "installed application Alembic root is unavailable": 42,
        "installed active Alembic graph head is not exactly 300": 42,
        "fresh 300 migration cannot verify migrator session": 43,
        "fresh 300 migration must connect as restricted migrator": 44,
        "fresh 300 migration requires no existing public.alembic_version table": 45,
        "fresh 300 pre-root state cannot be attested": 45,
        "fresh 300 pre-root state is not exact": 45,
        "fresh 300 migration did not produce exact raw revision 300": 46,
        "fresh 300 migration destination facet does not match baseline": 46,
    }
    runtime_error_codes = {
        "fresh 300 destination reference manifest is invalid": 51,
        "fresh 300 destination artifact map is invalid": 51,
        "fresh 300 destination facet SQL is invalid": 51,
        "fresh 300 destination facet does not match immutable reference": 51,
        "KOR_TRAVEL_MAP_ALEMBIC_USE_SCHEMA_OWNER_ROLE must be exactly true or false": 52,
        "Alembic external connection must be a SQLAlchemy Connection": 52,
        "300_schema_baseline is forward-only — older Alembic lineages are unsupported": 54,
    }
    return "\n".join(
        (
            "import asyncio",
            "import runpy",
            "module = runpy.run_path(",
            "    '/usr/local/bin/ktm-application-schema-fresh-300',",
            "    run_name='m05_map_fresh_init_diagnostic',",
            ")",
            "try:",
            "    if module['_parse_args'](['migrate']) != ('migrate', None):",
            "        raise SystemExit(127)",
            "    asyncio.run(module['_migrate']())",
            "except module['FreshMigrationError'] as error:",
            f"    raise SystemExit({error_codes!r}.get(str(error), 127))",
            "except BaseException as error:",
            "    identity = (type(error).__module__, type(error).__name__)",
            "    codes = {",
            "        ('kortravelmap.infra.runtime_privileges',",
            "         'RuntimePrivilegeReconciliationError'): 50,",
            "        ('alembic.util.exc', 'CommandError'): 47,",
            "        ('sqlalchemy.exc', 'OperationalError'): 49,",
            "        ('sqlalchemy.exc', 'ProgrammingError'): 49,",
            "        ('sqlalchemy.exc', 'SQLAlchemyError'): 49,",
            "    }",
            "    if identity == ('builtins', 'RuntimeError'):",
            "        message = str(error)",
            f"        runtime_codes = {runtime_error_codes!r}",
            "        if message in runtime_codes:",
            "            raise SystemExit(runtime_codes[message])",
            "        if message.startswith('300 baseline reference') or message.startswith(",
            "            '300 baseline application-',",
            "        ):",
            "            raise SystemExit(53)",
            "        if message.startswith('0236-to-300 ') or message.startswith(",
            "            '0236 application schema',",
            "        ) or message.startswith('generic Alembic stamp'):",
            "            raise SystemExit(54)",
            "        if message.startswith('application metadata maps') or message.startswith(",
            "            'alembic unmapped-table exclusions',",
            "        ):",
            "            raise SystemExit(55)",
            "        raise SystemExit(48)",
            "    raise SystemExit(codes.get(identity, 127))",
        )
    )


def _map_fresh_init_diagnostic_entrypoint() -> str:
    encoded = base64.b64encode(
        _map_fresh_init_diagnostic_runner().encode("utf-8")
    ).decode("ascii")
    return (
        "import base64; exec(compile(base64.b64decode("
        f"{encoded!r}), '<m05-map-fresh-init>', 'exec'))"
    )


def _free_ports(transaction: str) -> dict[str, int]:
    base = 30000 + (int(transaction[:8], 16) % 9000)
    names = (
        "map_api",
        "map_dagster",
        "map_postgres",
        "map_rustfs",
        "map_rustfs_console",
        "pinvi_api",
        "pinvi_web",
        "pinvi_rustfs",
        "pinvi_rustfs_console",
        "pinvi_dagster",
        "pinvi_cadvisor",
        "pinvi_prometheus",
        "pinvi_grafana",
    )
    for offset in range(1000):
        ports = {
            name: base + offset * len(names) + index for index, name in enumerate(names)
        }
        if max(ports.values()) >= 65535:
            break
        if all(
            not _command(
                "/usr/bin/ss", "-H", "-ltn", f"sport = :{port}", capture=True
            ).strip()
            for port in ports.values()
        ):
            return ports
    _fail("ports_unavailable")


def _map_network_addresses(transaction: str) -> tuple[str, str, str, str]:
    """기존 Docker subnet과 겹치지 않는 bridge gateway·Map API/BFF 주소를 고른다."""

    raw = _command(
        "/usr/bin/docker",
        "network",
        "ls",
        "--quiet",
        capture=True,
    )
    network_ids = [line for line in raw.splitlines() if len(line) == 64]
    existing: list[ipaddress.IPv4Network] = []
    if network_ids:
        inspected = _command(
            "/usr/bin/docker", "network", "inspect", *network_ids, capture=True
        )
        try:
            values = json.loads(inspected)
        except json.JSONDecodeError:
            _fail("network_inspect_invalid")
        if not isinstance(values, list):
            _fail("network_inspect_invalid")
        for value in values:
            if not isinstance(value, dict):
                _fail("network_inspect_invalid")
            ipam = value.get("IPAM")
            if not isinstance(ipam, dict) or not isinstance(ipam.get("Config"), list):
                continue
            for config in ipam["Config"]:
                if not isinstance(config, dict) or not isinstance(
                    config.get("Subnet"), str
                ):
                    continue
                try:
                    subnet = ipaddress.ip_network(config["Subnet"], strict=False)
                except ValueError:
                    continue
                if isinstance(subnet, ipaddress.IPv4Network):
                    existing.append(subnet)
    seed = int(transaction[:8], 16)
    for offset in range(224):
        candidate = ipaddress.ip_network(f"172.29.{(seed + offset) % 224}.0/29")
        if not any(candidate.overlaps(item) for item in existing):
            hosts = list(candidate.hosts())
            return str(candidate), str(hosts[0]), str(hosts[1]), str(hosts[2])
    _fail("network_subnet_unavailable")


def _http_json(
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, object] | None = None,
    opener: Any | None = None,
    failure_phase: str = "runtime_http_failed",
    http_error_phase: str | None = None,
) -> dict[str, object]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        _fail("runtime_http_url_invalid")
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        _fail("runtime_http_url_invalid")
    encoded = (
        json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if body is not None
        else None
    )
    request = Request(
        url,
        data=encoded,
        headers={
            **headers,
            **({"Content-Type": "application/json"} if encoded else {}),
        },
        method="POST" if encoded else "GET",
    )
    try:
        request_opener = opener.open if opener is not None else _LOOPBACK_OPENER.open
        with request_opener(request, timeout=10) as response:
            raw = response.read(2_000_000)
    except HTTPError:
        # HTTP status와 loopback transport 오류를 같은 원문 없는 enum으로 합치면
        # 다음 one-shot 후보가 어느 startup 경계를 보정해야 하는지 알 수 없다.
        _fail(http_error_phase or failure_phase)
    except (OSError, URLError):
        # 원문 HTTP status/body/socket error는 receipt에 기록하지 않는다. 대신 caller가
        # 고정 enum을 주면 다음 immutable candidate의 보정 범위만 식별할 수 있다.
        _fail(failure_phase)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("runtime_http_contract_failed")
    if not isinstance(value, dict):
        _fail("runtime_http_contract_failed")
    return value


def _wait_for_map_health(*, url: str) -> dict[str, object]:
    """Container health와 host loopback publish 사이의 bounded 경합만 one-shot 안에서 흡수한다.

    HTTP status와 응답 계약 오류는 즉시 terminal로 보존한다. 재시도 대상은 API container가
    healthy가 된 직후 host publish socket이 아직 수신하지 않는 transport 오류뿐이며, 원문
    socket detail은 저장하지 않는다.
    """

    for attempt in range(LOOPBACK_HTTP_READINESS_ATTEMPTS):
        try:
            return _http_json(
                url,
                headers={},
                failure_phase="map_health_transport_failed",
                http_error_phase="map_health_status_failed",
            )
        except _PhaseError as error:
            if (
                error.phase != "map_health_transport_failed"
                or attempt + 1 == LOOPBACK_HTTP_READINESS_ATTEMPTS
            ):
                raise
            time.sleep(LOOPBACK_HTTP_READINESS_RETRY_SECONDS)
    raise AssertionError("map health retry loop must return or raise")


def _data(value: dict[str, object]) -> dict[str, object]:
    data = value.get("data")
    if not isinstance(data, dict):
        _fail("runtime_http_contract_failed")
    return data


def _map_headers(secret: str) -> dict[str, str]:
    return {
        "X-Kor-Travel-Map-Admin-Proxy-Secret": secret,
        "X-Kor-Travel-Map-Actor": "m05-isolated-harness",
    }


def _pinvi_admin_opener(api_url: str, *, email: str, password: str) -> Any:
    if not email or not password:
        _fail("pinvi_auth_invalid")
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    login = _data(
        _http_json(
            f"{api_url.rstrip('/')}/auth/login",
            headers={},
            body={"email": email, "password": password},
            opener=opener,
            failure_phase="pinvi_login_http_failed",
        )
    )
    roles = login.get("roles")
    if not isinstance(roles, list) or "admin" not in roles:
        _fail("pinvi_auth_invalid")
    return opener


def _pinvi_submit_m04_fixture(*, api_url: str, opener: Any, transaction: str) -> str:
    value = _data(
        _http_json(
            f"{api_url.rstrip('/')}/features/requests",
            headers={},
            body={
                "type": "new_place",
                "kind": "place",
                "title": f"M05 isolated manual {transaction[:12]}",
                "coord": {"lon": 127.111111, "lat": 37.511111},
                "categories": ["M05 isolated"],
                "note": "M05 isolated signed E2E fixture",
                "source": "user",
                "coord_source": "map_pick",
            },
            opener=opener,
            failure_phase="m04_fixture_http_failed",
        )
    )
    request_id = value.get("request_id")
    try:
        return str(uuid.UUID(str(request_id)))
    except (TypeError, ValueError):
        _fail("m04_fixture_invalid")


def _approve_map_request(
    *, admin_url: str, request_id: str, proxy_secret: str, manual_create_token: str
) -> str:
    value = _data(
        _http_json(
            f"{admin_url.rstrip('/')}/v1/admin/feature-requests/{request_id}/approve",
            headers={
                **_map_headers(proxy_secret),
                "Idempotency-Key": str(uuid.uuid4()),
                "X-Kor-Travel-Map-Admin-Feature-Create-Token": manual_create_token,
            },
            body={
                "category": "01070300",
                "marker_color": "P-01",
                "marker_icon": "marker",
            },
            failure_phase="m04_map_approval_http_failed",
        )
    )
    if value.get("request_id") != request_id or value.get("status") != "approved":
        _fail("m04_map_approval_invalid")
    feature_id = value.get("feature_id")
    if not isinstance(feature_id, str) or not feature_id:
        _fail("m04_map_approval_invalid")
    return feature_id


def _seed_m05_provider_fixture(
    *, map_network: str, map_env: Path, image: str, manual_feature_id: str
) -> dict[str, str]:
    raw = _command(
        "/usr/bin/docker",
        "run",
        "--rm",
        "--read-only",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,mode=1777",
        "--network",
        map_network,
        "--env-file",
        str(map_env),
        "--mount",
        f"type=bind,src={_ROOT / 'scripts/m05_isolated_fixture.py'},dst=/opt/m05_isolated_fixture.py,readonly",
        "--entrypoint",
        "/usr/local/bin/python",
        image,
        "-I",
        "-B",
        "/opt/m05_isolated_fixture.py",
        manual_feature_id,
        capture=True,
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        _fail("m05_fixture_invalid")
    if not isinstance(value, dict) or set(value) != {
        "case_id",
        "manual_feature_id",
        "provider_feature_id",
    }:
        _fail("m05_fixture_invalid")
    try:
        uuid.UUID(str(value["case_id"]))
    except (TypeError, ValueError):
        _fail("m05_fixture_invalid")
    if value.get("manual_feature_id") != manual_feature_id:
        _fail("m05_fixture_invalid")
    provider_id = value.get("provider_feature_id")
    if not isinstance(provider_id, str) or not provider_id:
        _fail("m05_fixture_invalid")
    return {
        "case_id": str(value["case_id"]),
        "manual_feature_id": manual_feature_id,
        "provider_feature_id": provider_id,
    }


def _resolve_m05_case(
    *, admin_url: str, proxy_secret: str, case_id: str, provider_feature_id: str
) -> str:
    before = _data(
        _http_json(
            f"{admin_url.rstrip('/')}/v1/admin/manual-provider-dedup-cases/{case_id}",
            headers=_map_headers(proxy_secret),
            failure_phase="m05_case_lookup_http_failed",
        )
    )
    manual = before.get("manual_feature")
    provider = before.get("provider_feature")
    if (
        before.get("status") != "pending"
        or not isinstance(manual, dict)
        or not isinstance(provider, dict)
        or not isinstance(before.get("evidence_fingerprint"), str)
        or type(manual.get("row_revision")) is not int
        or type(provider.get("row_revision")) is not int
        or provider.get("feature_id") != provider_feature_id
    ):
        _fail("m05_case_invalid")
    decision = _data(
        _http_json(
            f"{admin_url.rstrip('/')}/v1/admin/manual-provider-dedup-cases/{case_id}/decisions",
            headers={
                **_map_headers(proxy_secret),
                "Idempotency-Key": str(uuid.uuid4()),
            },
            body={
                "decision": "merged",
                "expected_case_fingerprint": before["evidence_fingerprint"],
                "expected_manual_row_revision": manual["row_revision"],
                "expected_provider_row_revision": provider["row_revision"],
                "survivor_feature_id": provider_feature_id,
                "reason": "M05 isolated signed E2E rebind",
            },
            failure_phase="m05_case_decision_http_failed",
        )
    )
    if decision.get("outcome") != "merged":
        _fail("m05_case_invalid")
    event_id = decision.get("event_id")
    try:
        return str(uuid.UUID(str(event_id)))
    except (TypeError, ValueError):
        _fail("m05_case_invalid")


def _wait_for_pinvi_receipt(*, api_url: str, opener: Any, event_id: str) -> int:
    """PinVi detail 계약의 `applied`만 성공으로 수용하고 나머지는 즉시 종료한다."""

    data = _data(
        _http_json(
            f"{api_url.rstrip('/')}/admin/feature-reference-reconciliations/{event_id}",
            headers={},
            opener=opener,
            failure_phase="m05_pinvi_receipt_http_failed",
        )
    )
    status = data.get("status")
    if status == "blocked":
        _fail("m05_pinvi_receipt_blocked")
    if status != "applied":
        _fail("m05_pinvi_receipt_invalid")
    receipt = data.get("receipt")
    if not isinstance(receipt, dict):
        _fail("m05_pinvi_receipt_invalid")
    impact_count = receipt.get("impact_count")
    if type(impact_count) is not int or impact_count < 0:
        _fail("m05_pinvi_receipt_invalid")
    return impact_count


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _sha256_text(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        _fail("pair_contract_invalid")
    return value


def _pair(pinvi_root: Path, map_root: Path) -> tuple[M05IsolatedPairEvidence, str, str]:
    """PinVi가 vendoring한 M05 pair를 Map pinned Git blob까지 직접 대조한다."""

    path = pinvi_root / "contracts/kor-travel-map-m05-pair-provenance-v1.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        mapping = value["map"]
        full = mapping["full"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        _fail("pair_contract_invalid")
    expected_entry_keys = {
        "openapi_sha256",
        "runtime_operation_contract_sha256",
        "source_canonical_sha256",
        "source_operation_contract_sha256",
        "source_revision",
    }
    if (
        not isinstance(value, dict)
        or set(value) != {"map", "runtime_image_digests", "version"}
        or value.get("version") != 1
        or not isinstance(mapping, dict)
        or set(mapping) != {"admin", "full", "service", "user"}
        or not isinstance(full, dict)
        or set(full) != expected_entry_keys
    ):
        _fail("pair_contract_invalid")
    revisions: set[str] = set()
    for name in ("admin", "full", "service", "user"):
        entry = mapping.get(name)
        if not isinstance(entry, dict) or set(entry) != expected_entry_keys:
            _fail("pair_contract_invalid")
        _sha256_text(entry.get("openapi_sha256"))
        _sha256_text(entry.get("runtime_operation_contract_sha256"))
        _sha256_text(entry.get("source_canonical_sha256"))
        _sha256_text(entry.get("source_operation_contract_sha256"))
        entry_revision = entry.get("source_revision")
        if (
            not isinstance(entry_revision, str)
            or len(entry_revision) != _REVISION_LENGTH
            or any(char not in "0123456789abcdef" for char in entry_revision)
        ):
            _fail("pair_contract_invalid")
        revisions.add(entry_revision)
    map_hash = _sha256_text(full.get("openapi_sha256"))
    if full.get("source_revision") != PINNED_RUNTIME_RELEASE.source_for("map").revision:
        _fail("pair_contract_invalid")
    # M05 source attestation은 pair가 지정한 admin/full/service/user Git blob 모두를
    # exact revision으로 다시 읽는다. materializer가 현재 head만 fetch하므로 worktree는
    # 바꾸지 않고 canonical bare source에 이 네 object만 보충한다.
    map_source = PINNED_RUNTIME_RELEASE.source_for("map")
    for pair_revision in sorted(revisions):
        _command(
            "/usr/bin/git",
            "-C",
            str(map_root),
            "fetch",
            "--no-tags",
            map_source.canonical_url,
            pair_revision,
        )
    paths = {
        "admin": "packages/kor-travel-map-api/openapi.json",
        "full": "packages/kor-travel-map-api/openapi.json",
        "service": "packages/kor-travel-map-api/openapi.service.json",
        "user": "packages/kor-travel-map-api/openapi.user.json",
    }
    for name, relative_path in paths.items():
        entry = mapping[name]
        if not isinstance(entry, dict):
            _fail("pair_contract_invalid")
        revision = entry.get("source_revision")
        if not isinstance(revision, str):
            _fail("pair_contract_invalid")
        try:
            raw = subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(map_root),
                    "show",
                    f"{revision}:{relative_path}",
                ],
                cwd="/",
                env=_SAFE_SUBPROCESS_ENV,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            _fail("pair_contract_invalid")
        if raw.returncode != 0 or hashlib.sha256(
            raw.stdout
        ).hexdigest() != _sha256_text(entry["openapi_sha256"]):
            _fail("pair_contract_invalid")
        try:
            source_value = json.loads(raw.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail("pair_contract_invalid")
        if hashlib.sha256(_canonical_json(source_value)).hexdigest() != _sha256_text(
            entry["source_canonical_sha256"]
        ):
            _fail("pair_contract_invalid")
    service = mapping["service"]
    if not isinstance(service, dict):
        _fail("pair_contract_invalid")
    service_openapi_sha256 = _sha256_text(service.get("openapi_sha256"))
    service_source_revision = service.get("source_revision")
    if not isinstance(service_source_revision, str):
        _fail("pair_contract_invalid")
    return (
        M05IsolatedPairEvidence(
            map_full_openapi_sha256=map_hash,
            map_source_revision=PINNED_RUNTIME_RELEASE.source_for("map").revision,
            pinvi_full_openapi_sha256=map_hash,
            pinvi_source_revision=PINNED_RUNTIME_RELEASE.source_for("pinvi").revision,
        ),
        service_openapi_sha256,
        service_source_revision,
    )


def _assert_pinvi_manager_admission_contract(pinvi_root: Path) -> None:
    """Pinned PinVi source가 Manager-only isolated admission을 실제로 강제하는지 확인한다."""

    values: dict[str, str] = {}
    for relative in _PINVI_MANAGER_ADMISSION_FILES:
        path = pinvi_root / relative
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 128_000:
                raise OSError
            values[relative] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            _fail("pinvi_manager_admission_contract_invalid")
    if not all(
        token in values["scripts/docker-app.sh"]
        or token in values["scripts/m05_isolated_manager_admission.py"]
        for token in _PINVI_MANAGER_ADMISSION_TOKENS
    ):
        _fail("pinvi_manager_admission_contract_invalid")


def _source_pair_preflight() -> tuple[Path, Path, M05IsolatedPairEvidence, str, str]:
    """실행권을 소비하기 전에 pinned source pair의 integration 계약만 검사한다."""

    ambient = dict(os.environ)
    try:
        os.environ.clear()
        values = effective_environment(str(_ROOT / ".env"))
    finally:
        os.environ.clear()
        os.environ.update(ambient)
    state_paths = pinned_runtime_state_paths(
        values, pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256
    )
    sources = materialize_pinned_runtime_sources(
        release=PINNED_RUNTIME_RELEASE, state_paths=state_paths, values=values
    )
    map_root, pinvi_root = (
        sources.source_for("map").root,
        sources.source_for("pinvi").root,
    )
    pair, service_openapi_sha256, service_source_revision = _pair(pinvi_root, map_root)
    _assert_pinvi_manager_admission_contract(pinvi_root)
    return map_root, pinvi_root, pair, service_openapi_sha256, service_source_revision


def preflight(expected_revision: str) -> int:
    """launcher용 비소비 source-materialization preflight; terminal/ledger를 쓰지 않는다."""

    try:
        _validate_trusted_release(expected_revision)
        _assert_current_m05_execution_is_runnable(expected_revision)
        _source_pair_preflight()
    except (_PhaseError, OSError, RuntimeError, ValueError):
        return 1
    return 0


def _pinvi_manager_admission_environment(
    *,
    env_file: Path,
    bootstrap_credential_file: Path,
    project: str,
    pinvi_source_revision: str,
    execution_identity_sha256: str,
    admission_path: Path,
) -> dict[str, str]:
    """Manager가 검증한 admission tuple과 one-shot credential 경로만 전달한다."""

    return {
        "PINVI_ENV_FILE": str(env_file),
        # ``docker-app.sh``는 Compose에는 ``PINVI_ENV_FILE``를 넘기지만, migration
        # 전 host-side bootstrap validator는 현재 process 환경에서 이 path를 읽는다.
        # credential 내용은 env에 넣지 않고, owner-only absolute host file path만
        # direct command boundary로 전달한다.
        "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE": str(bootstrap_credential_file),
        "PINVI_DOCKER_PROJECT": project,
        "PINVI_SOURCE_REVISION": pinvi_source_revision,
        "PINVI_M05_ISOLATED_MANAGER_ADMISSION_PATH": str(admission_path),
        "PINVI_M05_PINSET_SHA256": PINNED_RUNTIME_RELEASE.pinset_sha256,
        "PINVI_M05_EXECUTION_IDENTITY_SHA256": execution_identity_sha256,
    }


def _container_id(
    project: str, service: str, *, root: Path, env_file: Path, files: tuple[Path, ...]
) -> str:
    value = _compose(
        root=root,
        project=project,
        env_file=env_file,
        files=files,
        arguments=("ps", "-q", service),
        capture=True,
    ).strip()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        _fail("runtime_container_identity_invalid")
    return value


def _container_inspect(container_id: str) -> dict[str, Any]:
    """Read one Docker inspect object without allowing its raw payload into a receipt."""

    try:
        value = json.loads(
            _command("/usr/bin/docker", "container", "inspect", container_id, capture=True)
        )[0]
    except (IndexError, TypeError, json.JSONDecodeError):
        _fail("runtime_inspect_invalid")
    if not isinstance(value, dict):
        _fail("runtime_inspect_invalid")
    return value


def _assert_loopback_tcp_publish(
    container: Mapping[str, Any], *, container_port: int, host_port: int
) -> None:
    """Verify the generic host-loopback publish prerequisite before making HTTP readiness calls."""

    network_settings = container.get("NetworkSettings")
    ports = network_settings.get("Ports") if isinstance(network_settings, Mapping) else None
    bindings = ports.get(f"{container_port}/tcp") if isinstance(ports, Mapping) else None
    if (
        not isinstance(bindings, list)
        or len(bindings) != 1
        or not isinstance(bindings[0], Mapping)
        or bindings[0].get("HostIp") != "127.0.0.1"
        or bindings[0].get("HostPort") != str(host_port)
    ):
        _fail("runtime_loopback_publish_invalid")


def _safe_rendered_port_value(value: object, *, kind: str) -> str | int | None:
    """Project one rendered Compose port scalar into a fixed, non-raw evidence type."""

    if kind == "host_ip":
        if not isinstance(value, str) or len(value) > 45:
            return None
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            return None
    if kind == "protocol":
        if not isinstance(value, str):
            return None
        normalized = value.lower()
        return normalized if normalized in _SAFE_PORT_PROTOCOLS else None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 5:
        number = int(value)
    else:
        return None
    if not 1 <= number <= 65535:
        return None
    return number if kind == "target" else str(number)


def _safe_rendered_port_evidence(ports: list[Mapping[str, Any]]) -> tuple[dict[str, object], ...]:
    """Return a bounded whitelist projection; never persist a raw Compose mapping."""

    return tuple(
        {
            "host_ip": _safe_rendered_port_value(port.get("host_ip"), kind="host_ip"),
            "protocol": _safe_rendered_port_value(port.get("protocol", "tcp"), kind="protocol"),
            "published": _safe_rendered_port_value(port.get("published"), kind="published"),
            "target": _safe_rendered_port_value(port.get("target"), kind="target"),
        }
        for port in ports[:_RENDERED_PORT_EVIDENCE_LIMIT]
    )


def _assert_rendered_loopback_tcp_publish(
    rendered: str,
    *,
    service: str,
    container_port: int,
    host_port: int,
    evidence_path: Path | None = None,
    parse_failure_evidence_path: Path | None = None,
) -> None:
    """Fail before ledger claim when Compose cannot render the required loopback publish."""

    try:
        value = json.loads(rendered)
        services = value["services"]
        item = services[service]
        ports = item["ports"]
    except (KeyError, TypeError, json.JSONDecodeError):
        if parse_failure_evidence_path is not None:
            _write_compose_output_evidence(parse_failure_evidence_path, output=rendered)
        _fail("runtime_loopback_publish_config_invalid")
    if not isinstance(ports, list) or not all(isinstance(port, Mapping) for port in ports):
        _fail("runtime_loopback_publish_config_invalid")
    safe_ports = _safe_rendered_port_evidence(ports)
    if evidence_path is not None:
        # 검증한 고정 allowlist만 root-only로 남긴다. env·service 전체·raw Compose
        # mapping이나 extension field는 보존하지 않아 다음 preflight 보정에 필요한
        # topology만 남긴다.
        _write_private_json(
            evidence_path,
            {
                "container_port": container_port,
                "host_port": host_port,
                "port_count": len(ports),
                "ports": safe_ports,
                "service": service,
                "version": 1,
            },
        )
    if len(ports) > _RENDERED_PORT_EVIDENCE_LIMIT:
        _fail("runtime_loopback_publish_config_invalid")
    matches = [
        port
        for port in safe_ports
        if port["target"] == container_port
        and port["published"] == str(host_port)
        and port["host_ip"] == "127.0.0.1"
        and port["protocol"] == "tcp"
    ]
    if len(matches) != 1:
        _fail("runtime_loopback_publish_config_invalid")


def _image_id(reference: str) -> str:
    value = _command(
        "/usr/bin/docker",
        "image",
        "inspect",
        "--format",
        "{{.Id}}",
        reference,
        capture=True,
    ).strip()
    if not value.startswith("sha256:") or len(value) != 71:
        _fail("runtime_image_identity_invalid")
    return value


def _build_runtime_provenance(
    *,
    plan: M05IsolatedHarnessPlan,
    pair: M05IsolatedPairEvidence,
    map_network: str,
    pinvi_network: str,
    map_api_id: str,
    pinvi_api_id: str,
    map_api_port: int,
    pinvi_api_port: int,
    map_api_container: str,
    pinvi_api_container: str,
    image_references: dict[str, str],
    path: Path,
) -> str:
    def inspect_network(item: str) -> dict[str, Any]:
        try:
            value = json.loads(
                _command("/usr/bin/docker", "network", "inspect", item, capture=True)
            )[0]
        except (IndexError, TypeError, json.JSONDecodeError):
            _fail("runtime_inspect_invalid")
        return value

    def inspect_image(reference: str) -> dict[str, Any]:
        try:
            value = json.loads(
                _command("/usr/bin/docker", "image", "inspect", reference, capture=True)
            )[0]
        except (IndexError, TypeError, json.JSONDecodeError):
            _fail("runtime_inspect_invalid")
        return value

    map_network_value, pinvi_network_value = (
        inspect_network(map_network),
        inspect_network(pinvi_network),
    )
    expectation = M05IsolatedRuntimeExpectation(
        plan=plan,
        networks=(
            M05IsolatedNetworkExpectation(
                "map", map_network, str(map_network_value.get("Id", ""))
            ),
            M05IsolatedNetworkExpectation(
                "pinvi", pinvi_network, str(pinvi_network_value.get("Id", ""))
            ),
        ),
        pair=pair,
        services={
            "map-api": M05IsolatedServiceExpectation(
                "map", 13701, map_api_port, map_api_id
            ),
            "pinvi-api": M05IsolatedServiceExpectation(
                "pinvi", 8000, pinvi_api_port, pinvi_api_id
            ),
        },
    )
    containers = {
        "map-api": _container_inspect(map_api_container),
        "pinvi-api": _container_inspect(pinvi_api_container),
    }
    topology_images = {
        map_api_id: inspect_image(image_references["map-api"]),
        pinvi_api_id: inspect_image(image_references["pinvi-api"]),
    }
    assert_m05_isolated_runtime(
        expectation=expectation,
        containers=containers,
        image_inspects=topology_images,
        network_inspects={
            map_network: map_network_value,
            pinvi_network: pinvi_network_value,
        },
    )
    all_images = {
        name: inspect_image(reference) for name, reference in image_references.items()
    }
    provenance = build_m05_isolated_runtime_provenance(
        expectation=expectation, image_inspects=all_images
    )
    return _write_private_json(path, provenance)


def main(expected_revision: str, output: Path) -> int:
    phase = "admission"
    completed = False
    transaction = secrets.token_hex(16)
    plan: M05IsolatedHarnessPlan | None = None
    claim_attempted = False
    failure_diagnostic: str | None = None
    map_cleanup: _CleanupProject | None = None
    pinvi_cleanup: _CleanupProject | None = None
    private_files: tuple[Path, ...] = ()
    result_hashes: dict[str, str] = {}
    try:
        os.umask(0o077)
        _validate_trusted_release(expected_revision)
        execution = _assert_current_m05_execution_is_runnable(expected_revision)
        _root_directory(output)
        _LEDGER.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(_LEDGER, 0o700)
        _root_directory(_LEDGER)
        plan = M05IsolatedHarnessPlan(
            PINNED_RUNTIME_RELEASE,
            expected_revision,
            execution.current.execution_identity_sha256,
            transaction,
        )
        phase = "source_materialization"
        (
            map_root,
            pinvi_root,
            pair,
            service_openapi_sha256,
            service_source_revision,
        ) = _source_pair_preflight()
        # setup 전체를 하나의 `runtime_setup` receipt로 뭉개면 새 immutable source가
        # 어느 안전 경계를 보정해야 하는지 알 수 없다. 아래 단계명은 raw exception,
        # 경로, secret을 싣지 않는 allowlist receipt일 뿐 동일 pinset 재시도 권한은 아니다.
        phase = "runtime_setup_ports"
        ports = _free_ports(transaction)
        phase = "runtime_setup_workspace"
        runtime = output / "runtime"
        runtime.mkdir(mode=0o700)
        _root_directory(runtime)
        map_env, pinvi_env = runtime / "map.env", runtime / "pinvi.env"
        pinvi_admission = runtime / "pinvi-isolated-manager-admission.json"
        map_override, pinvi_override = (
            runtime / "map.override.yml",
            runtime / "pinvi.override.yml",
        )
        fixture_env = runtime / "map-fixture.env"
        private_key, bootstrap = (
            runtime / "m05-private-key.pem",
            runtime / "pinvi-admin.json",
        )
        # 각 private path를 생성 전부터 cleanup 대상에 넣는다. Map 시작 중간의 실패도
        # credential file을 남기면 안 된다. 없는 파일은 _unlink_private가 무시한다.
        private_files = (
            map_env,
            pinvi_env,
            fixture_env,
            map_override,
            pinvi_override,
            pinvi_admission,
            bootstrap,
            private_key,
        )
        m04_evidence, m05_evidence = runtime / "m04", runtime / "m05"
        m04_evidence.mkdir(mode=0o700)
        m05_evidence.mkdir(mode=0o700)
        _root_directory(m04_evidence)
        _root_directory(m05_evidence)
        phase = "runtime_setup_admission_build"
        admission_payload = build_m05_isolated_manager_admission(plan=plan, pair=pair)
        phase = "runtime_setup_admission_write"
        _write_private_json(
            pinvi_admission,
            admission_payload,
        )
        phase = "runtime_setup_network"
        subnet, map_gateway_ip, map_api_ip, map_frontend_ip = _map_network_addresses(
            transaction
        )
        phase = "runtime_setup_credentials"
        map_secret, feature_request_token, read_token, ack_token = (
            _random_secret(),
            _random_secret(),
            _random_secret(),
            _random_secret(),
        )
        manual_feature_token = _random_secret()
        admin_password = _random_secret()
        bootstrap_email = f"m05-{transaction[:12]}@example.com"
        _write_private_json(
            bootstrap, {"email": bootstrap_email, "password": admin_password}
        )
        _command(
            "/usr/bin/openssl",
            "genpkey",
            "-algorithm",
            "Ed25519",
            "-out",
            str(private_key),
        )
        _root_file(private_key, mode=0o600)
        phase = "runtime_setup_map_config"
        password = _random_secret()
        token_sha = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
        migrator_password, api_password, dagster_password, metadata_password = (
            _random_secret(),
            _random_secret(),
            _random_secret(),
            _random_secret(),
        )
        map_bootstrap_dsn = (
            f"postgresql://kor_travel_map:{password}@postgres:5432/kor_travel_map"
        )
        ui_hash = _pbkdf2_password_hash(_random_secret()).replace("$", "$$")
        _write_private_text(
            map_env,
            "\n".join(
                (
                    f"KOR_TRAVEL_MAP_GIT_COMMIT={pair.map_source_revision}",
                    "KOR_TRAVEL_MAP_POSTGRES_DB=kor_travel_map",
                    "KOR_TRAVEL_MAP_POSTGRES_USER=kor_travel_map",
                    f"KOR_TRAVEL_MAP_POSTGRES_PASSWORD={password}",
                    "KOR_TRAVEL_MAP_DB_ROLE_BOOTSTRAP_CONFIRM_DATABASE=kor_travel_map",
                    f"KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN={map_bootstrap_dsn}",
                    f"KOR_TRAVEL_MAP_MIGRATOR_PASSWORD={migrator_password}",
                    f"KOR_TRAVEL_MAP_API_RUNTIME_PASSWORD={api_password}",
                    f"KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PASSWORD={dagster_password}",
                    "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER=kor_travel_map_dagster",
                    f"KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD={metadata_password}",
                    f"KOR_TRAVEL_MAP_MIGRATOR_PG_DSN=postgresql+asyncpg://ktm_feature_migrator:{migrator_password}@postgres:5432/kor_travel_map",
                    f"KOR_TRAVEL_MAP_API_RUNTIME_PG_DSN=postgresql+asyncpg://ktm_feature_api_runtime:{api_password}@postgres:5432/kor_travel_map",
                    f"KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN=postgresql+asyncpg://ktm_feature_dagster_runtime:{dagster_password}@postgres:5432/kor_travel_map",
                    f"KOR_TRAVEL_MAP_PG_DSN=postgresql+asyncpg://ktm_feature_dagster_runtime:{dagster_password}@postgres:5432/kor_travel_map",
                    f"KOR_TRAVEL_MAP_DOCKER_DAGSTER_PG_URL=postgresql://kor_travel_map_dagster:{metadata_password}@postgres:5432/kor_travel_map_dagster",
                    "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD=300",
                    "KOR_TRAVEL_MAP_API_PROFILE=local-dev",
                    "KOR_TRAVEL_MAP_DOCKER_BIND_HOST=127.0.0.1",
                    "KOR_TRAVEL_MAP_API_PORT=13701",
                    f"KOR_TRAVEL_MAP_DAGSTER_PORT={ports['map_dagster']}",
                    f"KOR_TRAVEL_MAP_ADMIN_WEB_PORT={ports['map_api']}",
                    f"KOR_TRAVEL_MAP_POSTGRES_HOST_PORT={ports['map_postgres']}",
                    f"KOR_TRAVEL_MAP_RUSTFS_API_PORT={ports['map_rustfs']}",
                    f"KOR_TRAVEL_MAP_RUSTFS_CONSOLE_PORT={ports['map_rustfs_console']}",
                    f"KOR_TRAVEL_MAP_MOIS_SOURCE_DB_VOLUME={plan.map_project}-mois",
                    f"KOR_TRAVEL_MAP_RUSTFS_VOLUME={plan.map_project}-rustfs",
                    f"KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_VOLUME={plan.map_project}-application-final-permit",
                    f"KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_VOLUME={plan.map_project}-dagster-storage-permit",
                    f"KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET={map_secret}",
                    f"KOR_TRAVEL_MAP_API_SERVICE_TOKEN={_random_secret()}",
                    f"KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET={_random_secret()}",
                    f"KOR_TRAVEL_MAP_API_METRICS_TOKEN={_random_secret()}",
                    f"KOR_TRAVEL_MAP_API_OPS_READ_TOKEN={_random_secret()}",
                    f"KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN={_random_secret()}",
                    f"KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN={_random_secret()}",
                    "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=true",
                    f"KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN={manual_feature_token}",
                    f"KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256={token_sha(manual_feature_token)}",
                    "KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED=true",
                    "KOR_TRAVEL_MAP_API_DESTRUCTIVE_ENABLED=true",
                    "KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED=false",
                    f"KOR_TRAVEL_MAP_UI_SESSION_SECRET={_random_secret()}",
                    f"KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH={ui_hash}",
                    f"KOR_TRAVEL_MAP_OBJECT_STORE_SECRET_ACCESS_KEY={_random_secret()}",
                    "KOR_TRAVEL_MAP_OBJECT_STORE_ACCESS_KEY_ID=m05-isolated-access",
                    f"NEXT_PUBLIC_KOR_TRAVEL_MAP_API=http://127.0.0.1:{ports['map_api']}",
                    "KOR_TRAVEL_MAP_DOCKER_API_INTERNAL_URL=http://api:13701",
                )
            )
            + "\n",
        )
        # generic Map API image에서 실행하는 fixture에는 ordinary Dagster runtime
        # credential만 넣는다. bootstrap/migrator owner DSN은 전달하지 않는다.
        _write_private_text(
            fixture_env,
            "KOR_TRAVEL_MAP_PG_DSN="
            f"postgresql+asyncpg://ktm_feature_dagster_runtime:{dagster_password}"
            "@postgres:5432/kor_travel_map\n",
        )
        # API에는 digest capability만, frontend에는 raw manual-create credential만 전달한다.
        map_override_lines = [
            "services:",
            "  db-application-schema-fresh-300:",
            "    entrypoint:",
            "      - /usr/local/bin/python",
            "      - -I",
            "      - -c",
            "      - >-",
            f"        {_map_fresh_init_diagnostic_entrypoint()}",
            "  api:",
            "    env_file: !reset []",
            "    labels:",
            *[f"      {key}: {value}" for key, value in plan.labels.items()],
            "      io.pinvi.build.environment: isolated",
            "    environment:",
            f"      KOR_TRAVEL_MAP_API_FEATURE_REQUEST_TOKEN_SHA256: {token_sha(feature_request_token)}",
            f"      KOR_TRAVEL_MAP_API_FEATURE_REFERENCE_RECONCILIATION_READ_TOKEN_SHA256: {token_sha(read_token)}",
            f"      KOR_TRAVEL_MAP_API_FEATURE_REFERENCE_RECONCILIATION_ACK_TOKEN_SHA256: {token_sha(ack_token)}",
            # frontend BFF와 root one-shot만 admin endpoint에 닿는다. published
            # loopback 요청은 bridge gateway에서 API로 전달되므로 이를 explicit
            # allowlist에 포함한다. host 밖에서는 이 harness principal을 흉내낼 수 없다.
            f'      KOR_TRAVEL_MAP_API_ADMIN_TRUSTED_PROXY_CIDRS: \'["{map_frontend_ip}/32","{map_gateway_ip}/32","127.0.0.1/32"]\'',
            # !reset은 list를 기본값(빈 값)으로 되돌린다. 기존 publish를 정확한
            # isolated loopback publish 하나로 교체하려면 Compose의 !override여야 한다.
            "    ports: !override",
            f"      - 127.0.0.1:{ports['map_api']}:13701",
            "    networks: !reset",
            "      default:",
            f"        ipv4_address: {map_api_ip}",
            "  frontend:",
            "    labels:",
            *[f"      {key}: {value}" for key, value in plan.labels.items()],
            "      io.pinvi.build.environment: isolated",
            "    ports: !reset []",
            "    networks: !reset",
            "      default:",
            f"        ipv4_address: {map_frontend_ip}",
            "networks:",
            "  default:",
            f"    name: {plan.map_network}",
            "    ipam:",
            "      config:",
            f"        - subnet: {subnet}",
            "    labels:",
            *[f"      {key}: {value}" for key, value in plan.labels.items()],
        ]
        _write_private_text(map_override, "\n".join(map_override_lines) + "\n")
        map_files = (
            map_root / "docker-compose.yml",
            map_root / "docker-compose.local-dev.yml",
            map_override,
        )
        # Compose topology는 Docker mutation 전에 정적으로 판정할 수 있다. 이 단계가
        # 실패하면 private setup만 cleanup하고 execution ledger를 소비하지 않는다.
        phase = "runtime_loopback_publish_config_invalid"
        _assert_rendered_loopback_tcp_publish(
            _compose(
                root=map_root,
                project=plan.map_project,
                env_file=map_env,
                files=map_files,
                arguments=("config", "--format", "json"),
                capture=True,
                failure_phase="runtime_loopback_publish_config_invalid",
                failure_evidence_path=runtime / "rendered-loopback-publish-error.json",
                output_evidence_path=runtime / "rendered-loopback-publish-output.json",
            ),
            service="api",
            container_port=13701,
            host_port=ports["map_api"],
            evidence_path=runtime / "rendered-loopback-publish.json",
            parse_failure_evidence_path=runtime / "rendered-loopback-publish-output.json",
        )
        # source pair와 rendered runtime topology가 정합할 때만 one-shot ledger를
        # 소비한다. O_EXCL create 뒤 write/fsync 실패도 execution을 소비한 것으로 본다.
        phase = "ledger_claim"
        claim_attempted = True
        claim_m05_isolated_harness_ledger(ledger_root=_LEDGER, plan=plan)
        phase = "runtime_setup_pinvi_config"
        _write_private_text(
            pinvi_env,
            "\n".join(
                (
                    "PINVI_ENVIRONMENT=isolated",
                    f"PINVI_SOURCE_REVISION={pair.pinvi_source_revision}",
                    f"PINVI_M05_ISOLATED_MANAGER_ADMISSION_PATH={pinvi_admission}",
                    f"PINVI_M05_PINSET_SHA256={PINNED_RUNTIME_RELEASE.pinset_sha256}",
                    f"PINVI_M05_EXECUTION_IDENTITY_SHA256={plan.execution_identity_sha256}",
                    f"PINVI_API_BUILD_CONTEXT={pinvi_root}",
                    f"PINVI_APP_BUILD_CONTEXT={pinvi_root}",
                    f"PINVI_DOCKER_PROJECT={plan.pinvi_project}",
                    f"PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE={bootstrap}",
                    f"PINVI_POSTGRES_PASSWORD={_random_secret()}",
                    f"PINVI_APP_DB_PASSWORD={_random_secret()}",
                    f"PINVI_MIGRATOR_DB_PASSWORD={_random_secret()}",
                    f"PINVI_JWT_SECRET_KEY={_random_secret()}",
                    f"PINVI_MCP_JWT_SECRET={_random_secret()}",
                    f"PINVI_API_PORT={ports['pinvi_api']}",
                    f"PINVI_WEB_PORT={ports['pinvi_web']}",
                    f"PINVI_RUSTFS_PORT={ports['pinvi_rustfs']}",
                    f"PINVI_RUSTFS_CONSOLE_PORT={ports['pinvi_rustfs_console']}",
                    f"PINVI_DAGSTER_DEV_PORT={ports['pinvi_dagster']}",
                    f"PINVI_CADVISOR_PORT={ports['pinvi_cadvisor']}",
                    f"PINVI_PROMETHEUS_PORT={ports['pinvi_prometheus']}",
                    f"PINVI_GRAFANA_PORT={ports['pinvi_grafana']}",
                    f"PINVI_WEB_BASE_URL=http://127.0.0.1:{ports['pinvi_web']}",
                    f"NEXT_PUBLIC_PINVI_API_URL=http://127.0.0.1:{ports['pinvi_api']}",
                    f'PINVI_CORS_ALLOWED_ORIGINS=["http://127.0.0.1:{ports["pinvi_web"]}"]',
                    "PINVI_RATE_LIMIT_ENABLED=false",
                    f"PINVI_KOR_TRAVEL_MAP_API_BASE_URL=http://host.docker.internal:{ports['map_api']}",
                    f"PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL=http://host.docker.internal:{ports['map_api']}",
                    f"KOR_TRAVEL_MAP_FEATURE_REQUEST_TOKEN={feature_request_token}",
                    "PINVI_KOR_TRAVEL_MAP_FEATURE_REFERENCE_RECONCILIATION_ENABLED=true",
                    f"PINVI_KOR_TRAVEL_MAP_FEATURE_REFERENCE_RECONCILIATION_READ_TOKEN={read_token}",
                    f"PINVI_KOR_TRAVEL_MAP_FEATURE_REFERENCE_RECONCILIATION_ACK_TOKEN={ack_token}",
                    "PINVI_KOR_TRAVEL_MAP_FEATURE_REFERENCE_RECONCILIATION_POLL_SECONDS=1",
                    f"PINVI_KOR_TRAVEL_MAP_FEATURE_REFERENCE_RECONCILIATION_EXPECTED_OPENAPI_SHA256={service_openapi_sha256}",
                    f"PINVI_KOR_TRAVEL_MAP_FEATURE_REFERENCE_RECONCILIATION_EXPECTED_SOURCE_REVISION={service_source_revision}",
                )
            )
            + "\n",
        )
        pinvi_override_lines = ["services:"]
        for service in ("app-api", "app-web", "app-dagster"):
            pinvi_override_lines.extend((f"  {service}:", "    labels:"))
            pinvi_override_lines.extend(
                f"      {key}: {value}" for key, value in plan.labels.items()
            )
            pinvi_override_lines.append("      io.pinvi.build.environment: isolated")
        pinvi_override_lines.extend(
            (
                "networks:",
                "  default:",
                f"    name: {plan.pinvi_network}",
                "    labels:",
            )
        )
        pinvi_override_lines.extend(
            f"      {key}: {value}" for key, value in plan.labels.items()
        )
        _write_private_text(pinvi_override, "\n".join(pinvi_override_lines) + "\n")
        phase = "map_runtime"
        map_cleanup = (map_root, plan.map_project, map_env, map_files, ("fresh-init",))
        _compose(
            root=map_root,
            project=plan.map_project,
            env_file=map_env,
            files=map_files,
            arguments=("up", "--detach", "--build", "--wait", "postgres"),
            failure_phase="map_postgres_start_failed",
        )
        _compose(
            root=map_root,
            project=plan.map_project,
            env_file=map_env,
            files=map_files,
            arguments=(
                "--profile",
                "fresh-init",
                "run",
                "--rm",
                "db-application-schema-fresh-300",
            ),
            failure_phase="map_fresh_init_failed",
            failure_exit_diagnostics=_MAP_FRESH_INIT_EXIT_DIAGNOSTICS,
        )
        _compose(
            root=map_root,
            project=plan.map_project,
            env_file=map_env,
            files=map_files,
            arguments=(
                "up",
                "--detach",
                "--build",
                "--wait",
                "rustfs",
                "rustfs-init",
                "api",
                "frontend",
            ),
            failure_phase="map_application_start_failed",
        )
        # ``docker compose up --wait``가 container health를 돌려도 host publish
        # binding은 별도 runtime 경계다. HTTP retry보다 먼저 generic binding을
        # 검사해 잘못된 Compose topology를 transport timeout으로 오분류하지 않는다.
        map_api = _container_id(
            plan.map_project, "api", root=map_root, env_file=map_env, files=map_files
        )
        _assert_loopback_tcp_publish(
            _container_inspect(map_api),
            container_port=13701,
            host_port=ports["map_api"],
        )
        admin_url = f"http://127.0.0.1:{ports['map_api']}"
        _wait_for_map_health(url=f"{admin_url}/health")
        phase = "map_subscription"
        _data(
            _http_json(
                f"{admin_url}/v1/admin/feature-reference-reconciliation-subscriptions",
                headers={
                    **_map_headers(map_secret),
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                body={"initial_event_sequence": 0},
                failure_phase="map_subscription_http_failed",
            )
        )
        phase = "pinvi_runtime"
        pinvi_files = (pinvi_root / "infra/docker-compose.app.yml", pinvi_override)
        pinvi_cleanup = (pinvi_root, plan.pinvi_project, pinvi_env, pinvi_files, ())
        environment = _pinvi_manager_admission_environment(
            env_file=pinvi_env,
            bootstrap_credential_file=bootstrap,
            project=plan.pinvi_project,
            pinvi_source_revision=pair.pinvi_source_revision,
            execution_identity_sha256=plan.execution_identity_sha256,
            admission_path=pinvi_admission,
        )
        for action in ("build", "up"):
            try:
                _command(
                    str(pinvi_root / "scripts/docker-app.sh"),
                    action,
                    cwd=pinvi_root,
                    env=environment,
                    capture_failure_stderr=os.environ.get(_FORENSIC_CAPTURE_ENV) == "1",
                )
            except _PhaseError as error:
                if error.phase == "runtime_command_failed":
                    _write_command_failure_evidence(
                        runtime / f"pinvi-runtime-{action}-error.json",
                        returncode=error.returncode,
                        stderr=error.stderr,
                    )
                raise
        _compose(
            root=pinvi_root,
            project=plan.pinvi_project,
            env_file=pinvi_env,
            files=pinvi_files,
            arguments=(
                "up",
                "--detach",
                "--no-build",
                "--force-recreate",
                "--wait",
                "app-api",
                "app-web",
            ),
        )
        _compose(
            root=pinvi_root,
            project=plan.pinvi_project,
            env_file=pinvi_env,
            files=pinvi_files,
            arguments=(
                "--profile",
                "etl",
                "up",
                "--detach",
                "--build",
                "--wait",
                "app-dagster",
            ),
        )
        pinvi_api = _container_id(
            plan.pinvi_project,
            "app-api",
            root=pinvi_root,
            env_file=pinvi_env,
            files=pinvi_files,
        )
        image_references = {
            "map-admin": f"{plan.map_project}-api",
            "map-api": f"{plan.map_project}-api",
            "map-frontend": f"{plan.map_project}-frontend",
            "pinvi-api": f"{plan.pinvi_project}-app-api",
            "pinvi-dagster": f"{plan.pinvi_project}-app-dagster",
            "pinvi-web": f"{plan.pinvi_project}-app-web",
        }
        _build_runtime_provenance(
            plan=plan,
            pair=pair,
            map_network=plan.map_network,
            pinvi_network=plan.pinvi_network,
            map_api_id=_image_id(image_references["map-api"]),
            pinvi_api_id=_image_id(image_references["pinvi-api"]),
            map_api_port=ports["map_api"],
            pinvi_api_port=ports["pinvi_api"],
            map_api_container=map_api,
            pinvi_api_container=pinvi_api,
            image_references=image_references,
            path=runtime / "isolated-runtime-provenance.json",
        )
        phase = "m04_m05_e2e"
        pinvi_web = _container_id(
            plan.pinvi_project,
            "app-web",
            root=pinvi_root,
            env_file=pinvi_env,
            files=pinvi_files,
        )
        pinvi_dagster = _container_id(
            plan.pinvi_project,
            "app-dagster",
            root=pinvi_root,
            env_file=pinvi_env,
            files=pinvi_files,
        )
        map_frontend = _container_id(
            plan.map_project,
            "frontend",
            root=map_root,
            env_file=map_env,
            files=map_files,
        )
        pinvi_api_url = f"http://127.0.0.1:{ports['pinvi_api']}"
        pinvi_web_url = f"http://127.0.0.1:{ports['pinvi_web']}"
        admin_opener = _pinvi_admin_opener(
            pinvi_api_url, email=bootstrap_email, password=admin_password
        )
        feature_request_id = _pinvi_submit_m04_fixture(
            api_url=pinvi_api_url, opener=admin_opener, transaction=transaction
        )
        m04_environment = {
            "PINVI_M04_LIVE_EMAIL": bootstrap_email,
            "PINVI_M04_LIVE_PASSWORD": admin_password,
        }
        _command(
            sys.executable,
            "-I",
            str(pinvi_root / "scripts/m05_activation_attestation.py"),
            "m04",
            "--evidence-dir",
            str(m04_evidence),
            "--private-key",
            str(private_key),
            "--pinvi-api-url",
            pinvi_api_url,
            "--pinvi-api-container",
            pinvi_api,
            "--pinvi-web-url",
            pinvi_web_url,
            "--pinvi-web-container",
            pinvi_web,
            "--feature-request-id",
            feature_request_id,
            "--pinvi-source-revision",
            pair.pinvi_source_revision,
            "--scope",
            "isolated",
            "--playwright-runner-image",
            "mcr.microsoft.com/playwright@sha256:9bd26ad900bb5e0f4dee75839e957a89ae89c2b7ab1e76050e559790e946b948",
            "--require-root-owned",
            "--",
            str(pinvi_root / "scripts/n150-playwright-runner.sh"),
            "--",
            "npm",
            "-w",
            "@pinvi/web",
            "run",
            "test:e2e:live-mutating",
            "--",
            "apps/web/e2e/admin-feature-request-queue-live-mutating.live.ts",
            "--workers=1",
            cwd=pinvi_root,
            env=m04_environment,
        )
        manual_feature_id = _approve_map_request(
            admin_url=admin_url,
            request_id=feature_request_id,
            proxy_secret=map_secret,
            manual_create_token=manual_feature_token,
        )
        fixture = _seed_m05_provider_fixture(
            map_network=plan.map_network,
            map_env=fixture_env,
            image=image_references["map-api"],
            manual_feature_id=manual_feature_id,
        )
        event_id = _resolve_m05_case(
            admin_url=admin_url,
            proxy_secret=map_secret,
            case_id=fixture["case_id"],
            provider_feature_id=fixture["provider_feature_id"],
        )
        impact_count = _wait_for_pinvi_receipt(
            api_url=pinvi_api_url,
            opener=_pinvi_admin_opener(
                pinvi_api_url, email=bootstrap_email, password=admin_password
            ),
            event_id=event_id,
        )
        m05_environment = {
            "M05_MAP_ADMIN_PROXY_SECRET": map_secret,
            "M05_PINVI_EMAIL": bootstrap_email,
            "M05_PINVI_PASSWORD": admin_password,
            "PINVI_M05_LIVE_OLD_FEATURE_ID": manual_feature_id,
            "PINVI_M05_LIVE_REPLACEMENT_FEATURE_ID": fixture["provider_feature_id"],
            "PINVI_M05_LIVE_IMPACT_COUNT": str(impact_count),
        }
        _command(
            sys.executable,
            "-I",
            str(pinvi_root / "scripts/m05_activation_attestation.py"),
            "live",
            "--evidence-dir",
            str(m05_evidence),
            "--private-key",
            str(private_key),
            "--map-admin-url",
            admin_url,
            "--map-case-id",
            fixture["case_id"],
            "--map-docker-project",
            plan.map_project,
            "--map-admin-container",
            map_api,
            "--map-admin-service",
            "api",
            "--map-api-container",
            map_api,
            "--map-api-service",
            "api",
            "--map-frontend-container",
            map_frontend,
            "--map-frontend-service",
            "frontend",
            "--map-source-root",
            str(map_root),
            "--m04-evidence-dir",
            str(m04_evidence),
            "--pinvi-api-url",
            pinvi_api_url,
            "--pinvi-docker-project",
            plan.pinvi_project,
            "--pinvi-api-container",
            pinvi_api,
            "--pinvi-web-url",
            pinvi_web_url,
            "--pinvi-web-container",
            pinvi_web,
            "--pinvi-dagster-container",
            pinvi_dagster,
            "--event-id",
            event_id,
            "--pinvi-source-revision",
            pair.pinvi_source_revision,
            "--scope",
            "isolated",
            "--isolated-runtime-provenance",
            str(runtime / "isolated-runtime-provenance.json"),
            "--isolated-manager-source-revision",
            expected_revision,
            "--isolated-pinset-sha256",
            PINNED_RUNTIME_RELEASE.pinset_sha256,
            "--isolated-execution-identity-sha256",
            plan.execution_identity_sha256,
            "--playwright-runner-image",
            "mcr.microsoft.com/playwright@sha256:9bd26ad900bb5e0f4dee75839e957a89ae89c2b7ab1e76050e559790e946b948",
            "--require-root-owned",
            "--",
            str(pinvi_root / "scripts/n150-playwright-runner.sh"),
            "--",
            "npm",
            "-w",
            "@pinvi/web",
            "run",
            "test:e2e:live-mutating",
            "--",
            "apps/web/e2e/admin-feature-reference-reconciliations-live-mutating.live.ts",
            "--workers=1",
            cwd=pinvi_root,
            env=m05_environment,
        )
        result_hashes = {
            "m04_attestation_sha256": hashlib.sha256(
                _secure_read_root_file(
                    m04_evidence / "m04-attestation.json",
                    mode=0o600,
                    encoding="utf-8",
                    limit=2_000_000,
                ).encode("utf-8")
            ).hexdigest(),
            "m05_attestation_sha256": hashlib.sha256(
                _secure_read_root_file(
                    m05_evidence / "attestation.json",
                    mode=0o600,
                    encoding="utf-8",
                    limit=2_000_000,
                ).encode("utf-8")
            ).hexdigest(),
            "runtime_provenance_sha256": hashlib.sha256(
                _secure_read_root_file(
                    runtime / "isolated-runtime-provenance.json",
                    mode=0o600,
                    encoding="utf-8",
                    limit=2_000_000,
                ).encode("utf-8")
            ).hexdigest(),
        }
        completed = True
    except _PhaseError as error:
        phase = error.phase
        failure_diagnostic = error.diagnostic
    # 이 boundary 밖으로 예외가 새면 launcher는 raw driver output 없이 결과 부재만
    # 관측한다. 예상하지 못한 ordinary exception도 현재 allowlist 실행 경계로만
    # 수렴하므로, raw detail 없이 다음 immutable candidate의 보정 범위를 좁힐 수 있다.
    # BaseException은 잡지 않아 root 운영자가 중단 신호를 보낼 수 있게 둔다.
    except Exception:  # noqa: BLE001 - fixed terminal receipt boundary
        phase = _public_terminal_phase(phase)
    finally:
        cleanup_failed, unexpected_finalization_failure = _cleanup_temporary_resources(
            map_cleanup=map_cleanup,
            pinvi_cleanup=pinvi_cleanup,
            private_files=private_files,
        )
        if unexpected_finalization_failure or cleanup_failed:
            completed = False
            phase = "runtime_cleanup_failed"
        if not completed and claim_attempted:
            try:
                pinset_blocked = _block_terminal_m05_execution(
                    phase, expected_manager_revision=expected_revision
                )
            except Exception:  # noqa: BLE001 - fixed terminal receipt boundary
                pinset_blocked = False
            if not pinset_blocked:
                phase = "runtime_execution_block_failed"
        driver_phase = phase
        for name in _RAW_ENV_NAMES:
            os.environ.pop(name, None)
        result: dict[str, object] = {
            "harness": "m05-isolated-bridge-v1",
            "manager_source_revision": expected_revision,
            "phase": "completed" if completed else phase,
            "driver_phase": driver_phase,
            "cleanup_failed": cleanup_failed,
            "pinset_sha256": PINNED_RUNTIME_RELEASE.pinset_sha256,
            "execution_identity_sha256": (
                plan.execution_identity_sha256 if plan is not None else None
            ),
            "status": (
                "passed"
                if completed
                else "blocked"
                if claim_attempted
                else "preflight_rejected"
            ),
            "transaction_id": transaction,
            **result_hashes,
        }
        if failure_diagnostic is not None:
            result["map_fresh_init_reason"] = failure_diagnostic
        try:
            _write_private_json(output / "result.json", result)
        except (OSError, _PhaseError):
            return 1
    return 0 if completed else 1


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit(2)
    if len(sys.argv) == 3 and sys.argv[1] == "--preflight":
        raise SystemExit(preflight(sys.argv[2]))
    if len(sys.argv) != 3:
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], Path(sys.argv[2])))
