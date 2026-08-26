from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from contextlib import contextmanager, nullcontext
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from dotenv import dotenv_values

from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinvi_database_role_credentials import (
    ROLE_CREDENTIALS_REBIND_SOURCE_SHA256_ENV,
    ensure_pinned_runtime_pinvi_role_credentials,
)

_ROLE_NAMES = (
    "PINVI_APP_DB_USER",
    "PINVI_APP_DB_PASSWORD",
    "PINVI_APP_SCHEMA_OWNER",
    "PINVI_MIGRATION_OWNER",
    "PINVI_MIGRATOR_DB_USER",
    "PINVI_MIGRATOR_DB_PASSWORD",
)


def _root_environment_bytes() -> bytes:
    return (
        b"PINVI_POSTGRES_USER=pinvi\n"
        b"PINVI_POSTGRES_PASSWORD='root-password-kept-private'\n"
    )


def _root_environment(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "PINVI_POSTGRES_USER=pinvi\n"
        "PINVI_POSTGRES_PASSWORD='root-password-kept-private'\n"
        f"{extra}",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _values(path: Path) -> dict[str, str]:
    parsed = dotenv_values(stream=StringIO(path.read_text(encoding="utf-8")))
    return {name: value or "" for name, value in parsed.items() if isinstance(name, str)}


def test_fresh_environment_generates_all_roles_atomically_and_privately(tmp_path: Path) -> None:
    path = _root_environment(tmp_path)

    credentials = ensure_pinned_runtime_pinvi_role_credentials(path, require_root=False)

    persisted = _values(path)
    assert set(credentials) == set(_ROLE_NAMES)
    assert {name: persisted[name] for name in _ROLE_NAMES} == dict(credentials)
    assert len(
        {
            persisted["PINVI_POSTGRES_USER"],
            persisted["PINVI_APP_DB_USER"],
            persisted["PINVI_APP_SCHEMA_OWNER"],
            persisted["PINVI_MIGRATION_OWNER"],
            persisted["PINVI_MIGRATOR_DB_USER"],
        }
    ) == 5
    assert persisted["PINVI_APP_DB_PASSWORD"] != persisted["PINVI_POSTGRES_PASSWORD"]
    assert persisted["PINVI_MIGRATOR_DB_PASSWORD"] != persisted["PINVI_POSTGRES_PASSWORD"]
    assert persisted["PINVI_APP_DB_PASSWORD"] != persisted["PINVI_MIGRATOR_DB_PASSWORD"]
    assert ROLE_CREDENTIALS_REBIND_SOURCE_SHA256_ENV not in persisted
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_partial_or_blank_role_values_fail_without_rewriting_root_environment(
    tmp_path: Path,
) -> None:
    path = _root_environment(tmp_path, "PINVI_APP_DB_USER=pinvi_runtime\n")
    original = path.read_bytes()

    with pytest.raises(DeploymentContractError, match="partially configured"):
        ensure_pinned_runtime_pinvi_role_credentials(path, require_root=False)

    assert path.read_bytes() == original


def test_all_blank_role_values_are_not_treated_as_a_fresh_environment(tmp_path: Path) -> None:
    path = _root_environment(tmp_path, "".join(f"{name}=\n" for name in _ROLE_NAMES))
    original = path.read_bytes()

    with pytest.raises(DeploymentContractError, match="partially configured"):
        ensure_pinned_runtime_pinvi_role_credentials(path, require_root=False)

    assert path.read_bytes() == original


def test_existing_complete_valid_credentials_are_never_rotated(tmp_path: Path) -> None:
    path = _root_environment(
        tmp_path,
        "PINVI_APP_DB_USER=pinvi_runtime\n"
        "PINVI_APP_DB_PASSWORD='runtime-password-kept-private'\n"
        "PINVI_APP_SCHEMA_OWNER=pinvi_schema_owner\n"
        "PINVI_MIGRATION_OWNER=pinvi_migration_owner\n"
        "PINVI_MIGRATOR_DB_USER=pinvi_migrator\n"
        "PINVI_MIGRATOR_DB_PASSWORD='migrator-password-kept-private'\n",
    )
    original = path.read_bytes()

    credentials = ensure_pinned_runtime_pinvi_role_credentials(path, require_root=False)

    assert path.read_bytes() == original
    assert dict(credentials) == {name: _values(path)[name] for name in _ROLE_NAMES}


def test_pinned_snapshot_keeps_literal_role_credentials_out_of_caller_ambient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PinVi role passwords의 `${...}`는 root authority 원문으로만 전달한다."""

    root = tmp_path / "trusted-root"
    root.mkdir(mode=0o700)
    path = _root_environment(
        root,
        "PINVI_APP_DB_USER=pinvi_runtime\n"
        "PINVI_APP_DB_PASSWORD='literal-${CALLER_VALUE}-application'\n"
        "PINVI_APP_SCHEMA_OWNER=pinvi_schema_owner\n"
        "PINVI_MIGRATION_OWNER=pinvi_migration_owner\n"
        "PINVI_MIGRATOR_DB_USER=pinvi_migrator\n"
        "PINVI_MIGRATOR_DB_PASSWORD='literal-${CALLER_VALUE}-migrator'\n"
        "KTDM_DEPLOYMENT_ENVIRONMENT=rehearsal\n"
        "KTDM_DEPLOYMENT_LIFECYCLE='${CALLER_LIFECYCLE}'\n"
        "PINVI_ENVIRONMENT=production\n"
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=true\n",
    )
    compose_path = root / "docker-compose.yml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        compose_service_module, "trusted_pinned_runtime_project_root", lambda: root
    )
    for name in (
        "KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT",
        "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE",
        "KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE",
        "KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CALLER_VALUE", "caller-controlled")
    monkeypatch.setenv("CALLER_LIFECYCLE", "rebuildable")

    credentials = ensure_pinned_runtime_pinvi_role_credentials(
        path, require_root=False
    )
    snapshot = compose_service_module._capture_pinned_runtime_rebuild_environment_snapshot(
        environment_override=credentials
    )
    literal_application = "literal-${CALLER_VALUE}-application"
    literal_migrator = "literal-${CALLER_VALUE}-migrator"
    assert snapshot.effective["PINVI_APP_DB_PASSWORD"] == literal_application
    assert snapshot.effective["PINVI_MIGRATOR_DB_PASSWORD"] == literal_migrator
    assert snapshot.effective["KTDM_DEPLOYMENT_LIFECYCLE"] == "${CALLER_LIFECYCLE}"
    with pytest.raises(DeploymentContractError, match="requires rehearsal/rebuildable"):
        compose_service_module.assert_pinned_runtime_rebuild_allowed(
            environment=snapshot.effective
        )

    candidate = {
        "services": {
            "pinvi-db-runtime-role": {
                "image": "busybox:1.36",
                "environment": {
                    "PINVI_APP_DB_PASSWORD": "${PINVI_APP_DB_PASSWORD:?required}",
                    "PINVI_MIGRATOR_DB_PASSWORD": (
                        "${PINVI_MIGRATOR_DB_PASSWORD:?required}"
                    ),
                },
            }
        }
    }
    resolved = {
        "services": {
            "pinvi-db-runtime-role": {
                "image": "busybox:1.36",
                "environment": {
                    "PINVI_APP_DB_PASSWORD": literal_application,
                    "PINVI_MIGRATOR_DB_PASSWORD": literal_migrator,
                },
            }
        }
    }
    compose_environment: dict[str, str] = {}

    monkeypatch.setattr(
        compose_service_module,
        "_revalidate_compose_external_input_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        compose_service_module,
        "_materialize_external_inputs_with_memfd",
        lambda document, _inputs: (document, ()),
    )
    monkeypatch.setattr(
        compose_service_module,
        "revalidate_candidate_system_bind_snapshots",
        lambda _snapshots: None,
    )

    def run(_command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        compose_environment.update(cast(dict[str, str], kwargs["env"]))
        return subprocess.CompletedProcess(
            _command, 0, stdout=json.dumps(resolved), stderr=""
        )

    monkeypatch.setattr(compose_service_module.subprocess, "run", run)
    service = compose_service_module.ComposeService()
    resolved_actual = service._resolve_compose_candidate_unlocked(
        candidate,
        environment=snapshot.effective,
        expected_system_bind_snapshots=(),
        environment_snapshot=snapshot,
        environment_override=None,
        external_input_snapshot=cast(Any, object()),
    )
    assert compose_environment["PINVI_APP_DB_PASSWORD"] == literal_application
    assert compose_environment["PINVI_MIGRATOR_DB_PASSWORD"] == literal_migrator
    assert resolved_actual == resolved

    validation = compose_service_module.ValidatedComposeCandidate(
        resolved=resolved_actual,
        system_bind_snapshots=(),
        environment_snapshot=snapshot,
        external_input_snapshot=compose_service_module.ComposeExternalInputSnapshot(
            references=(), files=()
        ),
    )
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        lambda **_kwargs: validation,
    )
    transaction, _ = service._capture_transaction_unlocked(
        environment_snapshot=snapshot
    )
    assert transaction.environment.effective["PINVI_APP_DB_PASSWORD"] == literal_application
    assert transaction.resolved == resolved


def test_duplicate_role_declaration_fails_without_rewriting_root_environment(tmp_path: Path) -> None:
    path = _root_environment(
        tmp_path,
        "PINVI_APP_DB_USER=pinvi_runtime\nPINVI_APP_DB_USER=other_runtime\n",
    )
    original = path.read_bytes()

    with pytest.raises(DeploymentContractError, match="duplicate role credentials"):
        ensure_pinned_runtime_pinvi_role_credentials(path, require_root=False)

    assert path.read_bytes() == original


def test_unsafe_root_environment_mode_is_rejected_before_any_write(tmp_path: Path) -> None:
    path = _root_environment(tmp_path)
    path.chmod(0o644)
    original = path.read_bytes()

    with pytest.raises(DeploymentContractError, match="unsafe ownership or mode"):
        ensure_pinned_runtime_pinvi_role_credentials(path, require_root=False)

    assert path.read_bytes() == original


def test_snapshot_drift_is_rejected_before_fresh_role_write(tmp_path: Path) -> None:
    path = _root_environment(tmp_path)
    original = path.read_bytes()

    with pytest.raises(DeploymentContractError, match="changed before role credential"):
        ensure_pinned_runtime_pinvi_role_credentials(
            path,
            require_root=False,
            expected_environment_bytes=b"stale-environment\n",
        )

    assert path.read_bytes() == original


def test_pinned_rebuild_rejects_ambient_manager_path_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "trusted-root"
    root.mkdir()
    monkeypatch.setenv(
        "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", str(tmp_path / "caller.env")
    )

    with pytest.raises(DeploymentContractError, match="execution path is not trusted"):
        compose_service_module._assert_pinned_runtime_rebuild_execution_paths(root)


def test_map_runtime_ready_journal_allows_only_the_marked_role_rebind(
    tmp_path: Path,
) -> None:
    path = _root_environment(tmp_path)
    original = path.read_bytes()
    journal = SimpleNamespace(
        environment_sha256=hashlib.sha256(original).hexdigest(),
        phase="map_runtime_ready",
        pinvi_role_credential_environment_rebind=None,
    )
    values_before = _values(path)

    rebind_source_sha256 = (
        compose_service_module.ComposeService._assert_pinvi_role_credential_rebind_admission(
            journal,
            environment_bytes=original,
            values=values_before,
        )
    )
    assert rebind_source_sha256 == journal.environment_sha256
    ensure_pinned_runtime_pinvi_role_credentials(
        path,
        require_root=False,
        rebind_source_sha256=rebind_source_sha256,
    )
    values_after = _values(path)

    assert (
        compose_service_module.ComposeService._assert_pinvi_role_credential_rebind_admission(
            journal,
            environment_bytes=path.read_bytes(),
            values=values_after,
        )
        is None
    )
    assert values_after[ROLE_CREDENTIALS_REBIND_SOURCE_SHA256_ENV] == (
        journal.environment_sha256
    )


def test_fresh_role_initialization_rejects_an_unadmitted_rebind_marker(
    tmp_path: Path,
) -> None:
    path = _root_environment(tmp_path)

    with pytest.raises(DeploymentContractError, match="rebind source is invalid"):
        ensure_pinned_runtime_pinvi_role_credentials(
            path,
            require_root=False,
            rebind_source_sha256="f" * 64,
        )

    assert ROLE_CREDENTIALS_REBIND_SOURCE_SHA256_ENV not in _values(path)


def test_rebuild_lock_runs_admission_before_role_write_and_refreshes_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _root_environment(tmp_path)
    initial = SimpleNamespace(
        env_path=str(path),
        env_file_bytes=b"before\n",
        effective={"mode": "before"},
    )
    current = SimpleNamespace(
        env_path=str(path),
        env_file_bytes=b"after\n",
        effective={"mode": "after"},
    )
    events: list[str] = []

    @contextmanager
    def host_lease() -> Any:
        events.append("host-enter")
        try:
            yield
        finally:
            events.append("host-exit")

    @contextmanager
    def c6c_lease(_path: str) -> Any:
        events.append("c6c-enter")
        try:
            yield
        finally:
            events.append("c6c-exit")

    snapshots = iter((initial, current))
    snapshot_overrides: list[dict[str, str] | None] = []
    monkeypatch.setattr(compose_service_module, "pinned_runtime_rebuild_lock", host_lease)
    monkeypatch.setattr(
        compose_service_module,
        "_capture_pinned_runtime_rebuild_environment_snapshot",
        lambda *, environment_override=None: (
            snapshot_overrides.append(environment_override) or next(snapshots)
        ),
    )
    monkeypatch.setattr(
        compose_service_module,
        "assert_pinned_runtime_rebuild_allowed",
        lambda *, environment: events.append(f"allowed-{environment['mode']}"),
    )
    monkeypatch.setattr(
        compose_service_module,
        "validate_c6c_operation_tokens",
        lambda environment, *, require_nonempty: events.append(
            f"tokens-{environment['mode']}"
        ),
    )

    role_credentials = {"PINVI_APP_DB_PASSWORD": "literal-${UNSET}"}

    def ensure(
        observed_path: Path,
        *,
        expected_environment_bytes: bytes,
        rebind_source_sha256: str | None,
    ) -> dict[str, str]:
        assert observed_path == path
        assert expected_environment_bytes == b"before\n"
        assert rebind_source_sha256 is None
        assert events == [
            "host-enter",
            "allowed-before",
            "tokens-before",
            "prewrite",
        ]
        events.append("credentials-initialized")
        return role_credentials

    monkeypatch.setattr(
        compose_service_module, "ensure_pinned_runtime_pinvi_role_credentials", ensure
    )
    monkeypatch.setattr(compose_service_module, "c6c_deployment_lock", c6c_lease)
    monkeypatch.setattr(
        compose_service_module,
        "_c6c_deployment_lock_snapshot_from_environment",
        lambda snapshot: SimpleNamespace(lock_path="c6c-lock"),
    )
    monkeypatch.setattr(
        compose_service_module,
        "_revalidate_c6c_deployment_lock_snapshot",
        lambda snapshot: events.append("c6c-revalidated"),
    )

    with compose_service_module._pinned_runtime_rebuild_environment_lock(
        prewrite_admission=lambda snapshot: events.append("prewrite") or None
    ) as actual:
        assert actual[0].lock_path == "c6c-lock"
        assert actual[1:] == (current, True)
        assert events == [
            "host-enter",
            "allowed-before",
            "tokens-before",
            "prewrite",
            "credentials-initialized",
            "allowed-after",
            "tokens-after",
            "c6c-enter",
            "c6c-revalidated",
        ]

    assert snapshot_overrides == [None, role_credentials]
    assert events == [
        "host-enter",
        "allowed-before",
        "tokens-before",
        "prewrite",
        "credentials-initialized",
        "allowed-after",
        "tokens-after",
        "c6c-enter",
        "c6c-revalidated",
        "c6c-exit",
        "host-exit",
    ]


def test_rebuild_lock_rejects_failed_admission_without_role_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _root_environment(tmp_path)
    initial = SimpleNamespace(
        env_path=str(path),
        env_file_bytes=path.read_bytes(),
        effective={"mode": "invalid"},
    )
    original = path.read_bytes()
    called = False

    monkeypatch.setattr(compose_service_module, "pinned_runtime_rebuild_lock", nullcontext)
    monkeypatch.setattr(
        compose_service_module,
        "_capture_pinned_runtime_rebuild_environment_snapshot",
        lambda *, environment_override=None: initial,
    )

    def reject(*, environment: dict[str, str]) -> None:
        raise DeploymentContractError("pinned runtime rebuild requires rehearsal/rebuildable")

    def ensure_not_called(*args: Any, **kwargs: Any) -> dict[str, str]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(
        compose_service_module, "assert_pinned_runtime_rebuild_allowed", reject
    )
    monkeypatch.setattr(
        compose_service_module,
        "ensure_pinned_runtime_pinvi_role_credentials",
        ensure_not_called,
    )

    with pytest.raises(DeploymentContractError, match="requires rehearsal"):
        with compose_service_module._pinned_runtime_rebuild_environment_lock(
            prewrite_admission=lambda snapshot: pytest.fail("admission reached")
        ):
            pass

    assert called is False
    assert path.read_bytes() == original
