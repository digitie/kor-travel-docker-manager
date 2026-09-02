from __future__ import annotations

import json
import logging
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
from kor_travel_docker_manager.services.pinned_runtime_release import (
    current_pinned_runtime_release,
)

PINNED_RUNTIME_RELEASE = current_pinned_runtime_release()
from kor_travel_docker_manager.services.pinvi_bootstrap_credential import (
    PinviBootstrapCredentialFile,
    cleanup_pinvi_bootstrap_credential,
    create_pinvi_bootstrap_credential,
    pinvi_bootstrap_credential_file,
    reconcile_orphaned_pinvi_bootstrap_credentials,
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
    return (
        pinned_runtime_state_paths(
            values,
            pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
        ),
        values,
    )


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


def test_directory_fsync_failure_after_successful_write_does_not_destroy_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """디렉터리 fsync는 durability 보강일 뿐이다 — 이미 쓰기+fsync+stat 검증까지
    성공한 credential 파일을 그 실패가 파괴해서는 안 된다
    (`secure_state_file.py`의 `fsync_directory`와 같은 best-effort 계약).

    실제 디렉터리를 깨지 않고 재현하기 위해 ``os.fsync``를 감싸서, 대상 fd가
    디렉터리인지(``S_ISDIR``)로 구분한다: credential 파일 자신의 fd에 대한
    fsync(성공해야 함)가 관측된 "이후"의 디렉터리 fsync만 실패시킨다. 그
    이전(디렉터리 최초 생성 시점)의 디렉터리 fsync는 그대로 성공시켜 준비
    단계 자체는 건드리지 않는다.
    """

    state_paths, values = _state_paths(tmp_path)
    transaction_id = str(uuid.uuid4())
    real_fsync = os.fsync
    file_fsync_observed = {"value": False}

    def fake_fsync(fd: int) -> None:
        try:
            is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        except OSError:
            is_directory = False
        if not is_directory:
            file_fsync_observed["value"] = True
            real_fsync(fd)
            return
        if file_fsync_observed["value"]:
            raise OSError(5, "simulated directory fsync failure (no real directory touched)")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fake_fsync)
    with caplog.at_level(logging.WARNING):
        credential = create_pinvi_bootstrap_credential(
            state_paths=state_paths,
            values=values,
            transaction_id=transaction_id,
            email=_EMAIL,
            password=_PASSWORD,
        )
    monkeypatch.setattr(os, "fsync", real_fsync)

    assert credential.path.is_file()
    assert json.loads(credential.path.read_text(encoding="utf-8")) == {
        "email": _EMAIL,
        "password": _PASSWORD,
    }
    assert any(
        "directory fsync failed" in record.getMessage() for record in caplog.records
    )

    cleanup_pinvi_bootstrap_credential(
        credential,
        state_paths=state_paths,
        values=values,
    )


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


def test_global_reconcile_retires_old_transactions_before_new_pin_resumes(
    tmp_path: Path,
) -> None:
    first, state_paths, values = _credential(tmp_path)
    second = create_pinvi_bootstrap_credential(
        state_paths=state_paths,
        values=values,
        transaction_id=str(uuid.uuid4()),
        email=_EMAIL,
        password=_PASSWORD,
    )

    retired = reconcile_orphaned_pinvi_bootstrap_credentials(
        state_paths=state_paths,
        values=values,
        global_mutation_lock_held=True,
        all_one_shot_containers_absent=True,
    )

    assert retired == tuple(sorted((first.transaction_id, second.transaction_id)))
    assert not first.path.exists()
    assert not second.path.exists()

    resumed = create_pinvi_bootstrap_credential(
        state_paths=state_paths,
        values=values,
        transaction_id=str(uuid.uuid4()),
        email=_EMAIL,
        password=_PASSWORD,
    )
    assert resumed.path.is_file()
    cleanup_pinvi_bootstrap_credential(
        resumed,
        state_paths=state_paths,
        values=values,
    )


def test_global_reconcile_accepts_absent_state_root_after_required_proofs(
    tmp_path: Path,
) -> None:
    state_paths, values = _state_paths(tmp_path)

    assert reconcile_orphaned_pinvi_bootstrap_credentials(
        state_paths=state_paths,
        values=values,
        global_mutation_lock_held=True,
        all_one_shot_containers_absent=True,
    ) == ()
    assert not state_paths.state_root.exists()


@pytest.mark.parametrize(
    ("global_mutation_lock_held", "all_one_shot_containers_absent", "message"),
    [
        (False, True, "global mutation lock"),
        (True, False, "all one-shot containers absent"),
    ],
)
def test_global_reconcile_requires_lock_and_inactive_one_shot_proofs_without_mutation(
    tmp_path: Path,
    global_mutation_lock_held: bool,
    all_one_shot_containers_absent: bool,
    message: str,
) -> None:
    credential, state_paths, values = _credential(tmp_path)

    with pytest.raises(DeploymentContractError, match=message):
        reconcile_orphaned_pinvi_bootstrap_credentials(
            state_paths=state_paths,
            values=values,
            global_mutation_lock_held=global_mutation_lock_held,
            all_one_shot_containers_absent=all_one_shot_containers_absent,
        )

    assert credential.path.is_file()
    cleanup_pinvi_bootstrap_credential(
        credential,
        state_paths=state_paths,
        values=values,
    )


def test_global_reconcile_fails_closed_on_malformed_transaction_without_partial_cleanup(
    tmp_path: Path,
) -> None:
    credential, state_paths, values = _credential(tmp_path)
    malformed = credential.path.parent.parent / "not-a-canonical-uuid"
    malformed.mkdir(mode=0o700)

    with pytest.raises(DeploymentContractError, match="transaction ID is invalid"):
        reconcile_orphaned_pinvi_bootstrap_credentials(
            state_paths=state_paths,
            values=values,
            global_mutation_lock_held=True,
            all_one_shot_containers_absent=True,
        )

    assert credential.path.is_file()
    assert malformed.is_dir()


def test_global_reconcile_fails_closed_on_symlink_transaction_without_deleting_it(
    tmp_path: Path,
) -> None:
    credential, state_paths, values = _credential(tmp_path)
    foreign = tmp_path / "foreign-transaction"
    foreign.mkdir(mode=0o700)
    symlink = credential.path.parent.parent / str(uuid.uuid4())
    symlink.symlink_to(foreign, target_is_directory=True)

    with pytest.raises(DeploymentContractError, match="transaction directory is unsafe"):
        reconcile_orphaned_pinvi_bootstrap_credentials(
            state_paths=state_paths,
            values=values,
            global_mutation_lock_held=True,
            all_one_shot_containers_absent=True,
        )

    assert credential.path.is_file()
    assert symlink.is_symlink()


def test_global_reconcile_fails_closed_on_unexpected_transaction_file(
    tmp_path: Path,
) -> None:
    credential, state_paths, values = _credential(tmp_path)
    unexpected = credential.path.parent / "unexpected"
    unexpected.write_text("not manager credential data", encoding="utf-8")

    with pytest.raises(DeploymentContractError, match="unexpected artifacts"):
        reconcile_orphaned_pinvi_bootstrap_credentials(
            state_paths=state_paths,
            values=values,
            global_mutation_lock_held=True,
            all_one_shot_containers_absent=True,
        )

    assert credential.path.is_file()
    assert unexpected.is_file()


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


def test_transaction_directory_fsync_failure_does_not_leak_an_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """디렉터리 **생성** 직후의 fsync 실패가 orphan을 남기면 안 된다.

    `os.mkdir`는 이미 성공했으므로 여기서 raise하면 되돌릴 것이 없는 채로
    중단되어 빈 `bootstrap/<uuid>/`가 남는다. 그러면
    `_validate_exact_transaction_contents`가 `entries != [credential.json]`로
    이후 **모든** orphan 정리를 영구 fail-close한다 — 운영자가 손으로 rmdir
    하기 전까지 PinVi rebuild가 막힌다(적대 리뷰 2인이 독립 재현).

    기존 fsync 테스트는 credential 파일 fsync가 관측된 **이후**의 디렉터리
    fsync만 실패시켜 이 시점을 덮지 못했다.
    """

    state_paths, values = _state_paths(tmp_path)
    transaction_id = str(uuid.uuid4())
    real_fsync = os.fsync

    def fail_every_directory_fsync(fd: int) -> None:
        try:
            is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        except OSError:
            is_directory = False
        if is_directory:
            raise OSError(5, "simulated directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_every_directory_fsync)
    with caplog.at_level(logging.WARNING):
        credential = create_pinvi_bootstrap_credential(
            state_paths=state_paths,
            values=values,
            transaction_id=transaction_id,
            email=_EMAIL,
            password=_PASSWORD,
        )
    monkeypatch.setattr(os, "fsync", real_fsync)

    # 생성은 성공했고 credential은 쓸 수 있다.
    assert credential.path.is_file()

    # 그리고 정리가 orphan 없이 끝난다 — 이것이 잠김 사슬의 마지막 고리다.
    cleanup_pinvi_bootstrap_credential(
        credential,
        state_paths=state_paths,
        values=values,
    )
    bootstrap_root = credential.path.parent.parent
    assert not (bootstrap_root / transaction_id).exists(), (
        "빈 transaction 디렉터리가 남으면 이후 orphan 정리가 영구 fail-close한다"
    )


def test_cleanup_directory_fsync_failure_still_removes_the_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`unlink`/`rmdir` **직후**의 fsync 실패도 정리를 중단시키면 안 된다.

    unlink는 이미 성공했다(시크릿은 zeroize 후 사라졌다). 여기서 raise하면
    호출자의 `artifact_removed = True`에 도달하지 못해 `finally`의
    `_remove_empty_transaction_directory`가 건너뛰어지고, 같은 orphan 잠김
    사슬이 재현된다.
    """

    state_paths, values = _state_paths(tmp_path)
    transaction_id = str(uuid.uuid4())
    credential = create_pinvi_bootstrap_credential(
        state_paths=state_paths,
        values=values,
        transaction_id=transaction_id,
        email=_EMAIL,
        password=_PASSWORD,
    )
    bootstrap_root = credential.path.parent.parent
    assert (bootstrap_root / transaction_id).is_dir()

    real_fsync = os.fsync

    def fail_every_directory_fsync(fd: int) -> None:
        try:
            is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        except OSError:
            is_directory = False
        if is_directory:
            raise OSError(5, "simulated directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_every_directory_fsync)
    with caplog.at_level(logging.WARNING):
        cleanup_pinvi_bootstrap_credential(
            credential,
            state_paths=state_paths,
            values=values,
        )
    monkeypatch.setattr(os, "fsync", real_fsync)

    assert not credential.path.exists()
    assert not (bootstrap_root / transaction_id).exists(), (
        "정리가 중단되면 빈 transaction 디렉터리가 남아 rebuild가 막힌다"
    )
