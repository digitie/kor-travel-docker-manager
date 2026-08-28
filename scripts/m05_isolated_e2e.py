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
import time
import uuid
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
from kor_travel_docker_manager.services.m05_isolated_harness import (
    M05IsolatedHarnessPlan,
    M05IsolatedNetworkExpectation,
    M05IsolatedPairEvidence,
    M05IsolatedRuntimeExpectation,
    M05IsolatedServiceExpectation,
    assert_m05_isolated_runtime,
    build_m05_isolated_runtime_provenance,
    claim_m05_isolated_harness_ledger,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    pinned_runtime_state_paths,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    PINNED_RUNTIME_RELEASE,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    materialize_pinned_runtime_sources,
)

_ROOT = Path("/opt/kor-travel-docker-manager")
_LEDGER = Path("/var/lib/kor-travel-docker-manager/m05-isolated-once")
_REVISION_LENGTH = 40
_RAW_ENV_NAMES = (
    "M05_MAP_ADMIN_PROXY_SECRET",
    "M05_PINVI_EMAIL",
    "M05_PINVI_PASSWORD",
    "PINVI_M04_LIVE_EMAIL",
    "PINVI_M04_LIVE_PASSWORD",
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


class _PhaseError(RuntimeError):
    def __init__(self, phase: str, *, diagnostic: str | None = None) -> None:
        super().__init__(phase)
        self.phase = phase
        self.diagnostic = diagnostic


def _fail(phase: str, *, diagnostic: str | None = None) -> NoReturn:
    raise _PhaseError(phase, diagnostic=diagnostic)


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


def _write_private_json(path: Path, value: dict[str, object]) -> str:
    raw = (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
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
) -> str:
    child_env = dict(_SAFE_SUBPROCESS_ENV)
    if env is not None:
        child_env.update(env)
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
    if completed.returncode != 0:
        diagnostic = (
            failure_exit_diagnostics.get(completed.returncode)
            if failure_exit_diagnostics is not None
            else None
        )
        _fail("runtime_command_failed", diagnostic=diagnostic)
    return completed.stdout if capture else ""


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
        )
    except _PhaseError as error:
        if failure_phase is not None and error.phase == "runtime_command_failed":
            _fail(failure_phase, diagnostic=error.diagnostic)
        raise


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
        "pinvi_api",
        "pinvi_web",
        "pinvi_rustfs",
        "pinvi_dagster",
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


def _map_network_addresses(transaction: str) -> tuple[str, str, str]:
    """기존 Docker subnet과 겹치지 않는 Map 단일 bridge /29와 API/BFF 주소를 고른다."""

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
            return str(candidate), str(hosts[1]), str(hosts[2])
    _fail("network_subnet_unavailable")


def _http_json(
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, object] | None = None,
    opener: Any | None = None,
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
    except (HTTPError, OSError, URLError):
        _fail("runtime_http_failed")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("runtime_http_contract_failed")
    if not isinstance(value, dict):
        _fail("runtime_http_contract_failed")
    return value


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
    for _ in range(90):
        try:
            data = _data(
                _http_json(
                    f"{api_url.rstrip('/')}/admin/feature-reference-reconciliations/{event_id}",
                    headers={},
                    opener=opener,
                )
            )
        except _PhaseError:
            time.sleep(2)
            continue
        receipt = data.get("receipt")
        if data.get("status") == "applied" and isinstance(receipt, dict):
            impact_count = receipt.get("impact_count")
            if type(impact_count) is int and impact_count >= 0:
                return impact_count
            _fail("m05_pinvi_receipt_invalid")
        time.sleep(2)
    _fail("m05_pinvi_receipt_timeout")


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
    def inspect_container(item: str) -> dict[str, Any]:
        try:
            value = json.loads(
                _command("/usr/bin/docker", "container", "inspect", item, capture=True)
            )[0]
        except (IndexError, TypeError, json.JSONDecodeError):
            _fail("runtime_inspect_invalid")
        return value

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
        "map-api": inspect_container(map_api_container),
        "pinvi-api": inspect_container(pinvi_api_container),
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
    failure_diagnostic: str | None = None
    map_cleanup: tuple[Path, str, Path, tuple[Path, ...], tuple[str, ...]] | None = None
    pinvi_cleanup: tuple[Path, str, Path, tuple[Path, ...], tuple[str, ...]] | None = (
        None
    )
    private_files: tuple[Path, ...] = ()
    result_hashes: dict[str, str] = {}
    try:
        os.umask(0o077)
        _validate_trusted_release(expected_revision)
        _root_directory(output)
        _LEDGER.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(_LEDGER, 0o700)
        _root_directory(_LEDGER)
        plan = M05IsolatedHarnessPlan(
            PINNED_RUNTIME_RELEASE, expected_revision, transaction
        )
        claim_m05_isolated_harness_ledger(ledger_root=_LEDGER, plan=plan)
        phase = "source_materialization"
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
        pair, service_openapi_sha256, service_source_revision = _pair(
            pinvi_root, map_root
        )
        ports = _free_ports(transaction)
        runtime = output / "runtime"
        runtime.mkdir(mode=0o700)
        _root_directory(runtime)
        map_env, pinvi_env = runtime / "map.env", runtime / "pinvi.env"
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
            bootstrap,
            private_key,
        )
        m04_evidence, m05_evidence = runtime / "m04", runtime / "m05"
        m04_evidence.mkdir(mode=0o700)
        m05_evidence.mkdir(mode=0o700)
        _root_directory(m04_evidence)
        _root_directory(m05_evidence)
        subnet, map_api_ip, map_frontend_ip = _map_network_addresses(transaction)
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
                    f"KOR_TRAVEL_MAP_RUSTFS_CONSOLE_PORT={ports['map_rustfs'] + 1}",
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
            # frontend BFF와 root one-shot만 admin endpoint에 닿는다. API port는
            # loopback publish이므로 host 밖에서 이 별도 harness principal을 흉내낼 수 없다.
            f'      KOR_TRAVEL_MAP_API_ADMIN_TRUSTED_PROXY_CIDRS: \'["{map_frontend_ip}/32","127.0.0.1/32"]\'',
            "    ports: !reset",
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
        _write_private_text(
            pinvi_env,
            "\n".join(
                (
                    "PINVI_ENVIRONMENT=isolated",
                    "PINVI_M05_ISOLATED_MANAGER_HARNESS=1",
                    f"PINVI_SOURCE_REVISION={pair.pinvi_source_revision}",
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
                    f"PINVI_RUSTFS_CONSOLE_PORT={ports['pinvi_rustfs'] + 1}",
                    f"PINVI_DAGSTER_DEV_PORT={ports['pinvi_dagster']}",
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
        map_files = (
            map_root / "docker-compose.yml",
            map_root / "docker-compose.local-dev.yml",
            map_override,
        )
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
        admin_url = f"http://127.0.0.1:{ports['map_api']}"
        _http_json(f"{admin_url}/health", headers={})
        phase = "map_subscription"
        _data(
            _http_json(
                f"{admin_url}/v1/admin/feature-reference-reconciliation-subscriptions",
                headers={
                    **_map_headers(map_secret),
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                body={"initial_event_sequence": 0},
            )
        )
        phase = "pinvi_runtime"
        pinvi_files = (pinvi_root / "infra/docker-compose.app.yml", pinvi_override)
        pinvi_cleanup = (pinvi_root, plan.pinvi_project, pinvi_env, pinvi_files, ())
        environment = {
            "PINVI_ENV_FILE": str(pinvi_env),
            "PINVI_DOCKER_PROJECT": plan.pinvi_project,
            "PINVI_M05_ISOLATED_MANAGER_HARNESS": "1",
        }
        _command(
            str(pinvi_root / "scripts/docker-app.sh"),
            "up",
            cwd=pinvi_root,
            env=environment,
        )
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
        map_api = _container_id(
            plan.map_project, "api", root=map_root, env_file=map_env, files=map_files
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
    except (DeploymentContractError, OSError, ValueError):
        phase = "driver_contract_failed"
    finally:
        cleanup_failed = False
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
        for path in private_files:
            try:
                _unlink_private(path)
            except _PhaseError:
                cleanup_failed = True
        driver_phase = phase
        if cleanup_failed:
            completed = False
            phase = "runtime_cleanup_failed"
        for name in _RAW_ENV_NAMES:
            os.environ.pop(name, None)
        result: dict[str, object] = {
            "harness": "m05-isolated-bridge-v1",
            "manager_source_revision": expected_revision,
            "phase": "completed" if completed else phase,
            "driver_phase": driver_phase,
            "cleanup_failed": cleanup_failed,
            "pinset_sha256": PINNED_RUNTIME_RELEASE.pinset_sha256,
            "status": "passed" if completed else "blocked",
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
    if len(sys.argv) != 3 or os.geteuid() != 0:
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], Path(sys.argv[2])))
