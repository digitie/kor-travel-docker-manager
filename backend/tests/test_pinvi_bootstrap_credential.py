from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeStatePaths,
    pinned_runtime_state_paths,
)
from kor_travel_docker_manager.services.pinvi_bootstrap_credential import (
    PinviBootstrapCredentialFile,
    cleanup_pinvi_bootstrap_credential,
    create_pinvi_bootstrap_credential,
    pinvi_bootstrap_credential_file,
    retire_stale_pinvi_bootstrap_credential,
)

_EMAIL = "admin@example.test"
_PASSWORD = "bootstrap-password-keep-private"
_PROJECT_NAME = "f1d-credential-test"


def _values(tmp_path: Path) -> dict[str, str]:
    return {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "COMPOSE_PROJECT_NAME": _PROJECT_NAME,
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path / "pinned-runtime-state"),
    }


def _state_paths(tmp_path: Path) -> tuple[PinnedRuntimeStatePaths, dict[str, str]]:
    values = _values(tmp_path)
    return pinned_runtime_state_paths(values), values


def _credential(
    tmp_path: Path,
    *,
    transaction_id: str | None = None,
) -> tuple[PinviBootstrapCredentialFile, PinnedRuntimeStatePaths, dict[str, str]]:
    state_paths, values = _state_paths(tmp_path)
    credential = create_pinvi_bootstrap_credential(
        state_paths=state_paths,
        values=values,
        transaction_id=transaction_id or str(uuid.uuid4()),
        email=_EMAIL,
        password=_PASSWORD,
    )
    return credential, state_paths, values


def test_credential_file_has_exact_pinvi_schema_private_mode_and_opaque_repr(
    tmp_path: Path,
) -> None:
    credential, state_paths, values = _credential(tmp_path)

    assert credential.path.parent.name == credential.transaction_id
    assert credential.path.parent.parent.name == "bootstrap"
    assert credential.path.name == "credential.json"
    assert stat.S_IMODE(credential.path.lstat().st_mode) == 0o600
    assert json.loads(credential.path.read_text(encoding="utf-8")) == {
        "email": _EMAIL,
        "password": _PASSWORD,
    }
    assert _PASSWORD not in repr(credential)
    assert _EMAIL not in repr(credential)

    cleanup_pinvi_bootstrap_credential(
        credential,
        state_paths=state_paths,
        values=values,
    )


def test_explicit_cleanup_removes_only_its_transaction_and_is_idempotent(
    tmp_path: Path,
) -> None:
    credential, state_paths, values = _credential(tmp_path)

    cleanup_pinvi_bootstrap_credential(
        credential,
        state_paths=state_paths,
        values=values,
    )
    assert not credential.path.exists()
    cleanup_pinvi_bootstrap_credential(
        credential,
        state_paths=state_paths,
        values=values,
    )


def test_context_manager_cleans_credential_after_runner_scope(tmp_path: Path) -> None:
    state_paths, values = _state_paths(tmp_path)
    with pinvi_bootstrap_credential_file(
        state_paths=state_paths,
        values=values,
        transaction_id=str(uuid.uuid4()),
        email=_EMAIL,
        password=_PASSWORD,
    ) as credential:
        assert credential.path.is_file()

    assert not credential.path.exists()


def test_second_active_transaction_is_never_scavenged_or_deleted(tmp_path: Path) -> None:
    state_paths, values = _state_paths(tmp_path)
    first = create_pinvi_bootstrap_credential(
        state_paths=state_paths,
        values=values,
        transaction_id=str(uuid.uuid4()),
        email=_EMAIL,
        password=_PASSWORD,
    )
    second = create_pinvi_bootstrap_credential(
        state_paths=state_paths,
        values=values,
        transaction_id=str(uuid.uuid4()),
        email=_EMAIL,
        password=_PASSWORD,
    )

    assert first.path.exists()
    assert second.path.exists()
    cleanup_pinvi_bootstrap_credential(
        first,
        state_paths=state_paths,
        values=values,
    )
    assert not first.path.exists()
    assert second.path.exists()

    cleanup_pinvi_bootstrap_credential(
        second,
        state_paths=state_paths,
        values=values,
    )


def test_second_credential_for_same_transaction_fails_without_overwriting_active_file(
    tmp_path: Path,
) -> None:
    transaction_id = str(uuid.uuid4())
    first, state_paths, values = _credential(tmp_path, transaction_id=transaction_id)

    with pytest.raises(DeploymentContractError, match="transaction already exists"):
        create_pinvi_bootstrap_credential(
            state_paths=state_paths,
            values=values,
            transaction_id=transaction_id,
            email=_EMAIL,
            password=_PASSWORD,
        )

    assert json.loads(first.path.read_text(encoding="utf-8"))["password"] == _PASSWORD
    cleanup_pinvi_bootstrap_credential(
        first,
        state_paths=state_paths,
        values=values,
    )


def test_retire_stale_credential_removes_only_exact_transaction_after_runner_exit(
    tmp_path: Path,
) -> None:
    stale, state_paths, values = _credential(tmp_path)
    other = create_pinvi_bootstrap_credential(
        state_paths=state_paths,
        values=values,
        transaction_id=str(uuid.uuid4()),
        email=_EMAIL,
        password=_PASSWORD,
    )

    retire_stale_pinvi_bootstrap_credential(
        state_paths=state_paths,
        values=values,
        transaction_id=stale.transaction_id,
    )

    assert not stale.path.exists()
    assert other.path.exists()
    cleanup_pinvi_bootstrap_credential(
        other,
        state_paths=state_paths,
        values=values,
    )


def test_create_rejects_forged_owner_private_state_root(tmp_path: Path) -> None:
    state_paths, values = _state_paths(tmp_path)
    forged_root = tmp_path / "forged-private-root"
    forged_root.mkdir(mode=0o700)
    os.chmod(forged_root, 0o700)
    forged = replace(
        state_paths,
        state_root=forged_root,
        manifest=forged_root / "pinned-runtime-generation-v5.json",
        journal=forged_root / "pinned-runtime-rebuild-v5.json",
        tombstone_receipt=forged_root / "pinned-runtime-v5" / "legacy-tombstone-v5.json",
    )

    with pytest.raises(DeploymentContractError, match="canonical rebuildable state"):
        create_pinvi_bootstrap_credential(
            state_paths=forged,
            values=values,
            transaction_id=str(uuid.uuid4()),
            email=_EMAIL,
            password=_PASSWORD,
        )

    assert not (forged_root / "bootstrap").exists()


def test_create_rejects_unsafe_canonical_state_root(tmp_path: Path) -> None:
    state_paths, values = _state_paths(tmp_path)
    state_paths.state_root.mkdir(parents=True, mode=0o700)
    os.chmod(state_paths.state_root, 0o755)

    with pytest.raises(DeploymentContractError, match="state root is unsafe"):
        create_pinvi_bootstrap_credential(
            state_paths=state_paths,
            values=values,
            transaction_id=str(uuid.uuid4()),
            email=_EMAIL,
            password=_PASSWORD,
        )


def test_cleanup_rejects_hardlinked_credential_without_removal(tmp_path: Path) -> None:
    credential, state_paths, values = _credential(tmp_path)
    hardlink = credential.path.with_name("hardlink-copy")
    os.link(credential.path, hardlink)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        cleanup_pinvi_bootstrap_credential(
            credential,
            state_paths=state_paths,
            values=values,
        )

    assert credential.path.exists()
    assert hardlink.exists()


def test_cleanup_rejects_credential_state_path_or_values_drift(tmp_path: Path) -> None:
    credential, state_paths, values = _credential(tmp_path)
    drifted_values = {**values, "COMPOSE_PROJECT_NAME": "different-project"}

    with pytest.raises(DeploymentContractError, match="canonical rebuildable state"):
        cleanup_pinvi_bootstrap_credential(
            credential,
            state_paths=state_paths,
            values=drifted_values,
        )

    assert credential.path.exists()
    cleanup_pinvi_bootstrap_credential(
        credential,
        state_paths=state_paths,
        values=values,
    )


def test_create_fails_closed_when_o_nofollow_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_paths, values = _state_paths(tmp_path)
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

    with pytest.raises(DeploymentContractError, match="requires O_NOFOLLOW"):
        create_pinvi_bootstrap_credential(
            state_paths=state_paths,
            values=values,
            transaction_id=str(uuid.uuid4()),
            email=_EMAIL,
            password=_PASSWORD,
        )


@pytest.mark.parametrize(
    ("email", "password"),
    [
        (" ", _PASSWORD),
        ("admin@example.test\n", _PASSWORD),
        (_EMAIL, "short"),
        (_EMAIL, "private\npassword-value"),
    ],
)
def test_invalid_input_never_reflects_password_in_error(
    tmp_path: Path,
    email: str,
    password: str,
) -> None:
    state_paths, values = _state_paths(tmp_path)
    with pytest.raises(DeploymentContractError) as caught:
        create_pinvi_bootstrap_credential(
            state_paths=state_paths,
            values=values,
            transaction_id=str(uuid.uuid4()),
            email=email,
            password=password,
        )

    assert password not in str(caught.value)
    assert password not in repr(caught.value)
