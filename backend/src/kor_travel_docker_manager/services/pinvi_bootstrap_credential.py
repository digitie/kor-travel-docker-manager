"""PinVi one-shot bootstrap의 transaction-scoped credential file 경계.

credential은 v5 rebuild journal의 exact transaction UUID 아래에 하나만 만들 수
있다. 일반 생성 경로는 다른 transaction의 artifact를 탐색하거나 정리하지 않으며,
runner가 종료됐음을 확인한 호출자만 같은 transaction을 명시적으로 폐기한다.
"""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeStatePaths,
    ensure_pinned_runtime_state_directory,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    pinned_runtime_state_paths as canonical_pinned_runtime_state_paths,
)

_BOOTSTRAP_DIRECTORY = "bootstrap"
_CREDENTIAL_FILENAME = "credential.json"
_MAX_CREDENTIAL_BYTES = 8 * 1024
_EMAIL_LOCAL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}$")
_EMAIL_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class PinviBootstrapCredentialFile:
    """runner에 전달할 path와 cleanup용 private inode identity만 보관한다.

    ``email``과 ``password``는 artifact에 저장하지 않아 repr·예외·journal을 통해
    원문이 다시 노출될 수 없다.
    """

    path: Path
    transaction_id: str
    _state_paths: PinnedRuntimeStatePaths = field(repr=False, compare=False)
    _device: int = field(repr=False, compare=False)
    _inode: int = field(repr=False, compare=False)


def create_pinvi_bootstrap_credential(
    *,
    state_paths: PinnedRuntimeStatePaths,
    values: Mapping[str, str],
    transaction_id: str,
    email: str,
    password: str,
) -> PinviBootstrapCredentialFile:
    """정확한 rebuild transaction에 PinVi credential file 하나를 만든다.

    ``state_paths``는 frozen canonical environment에서 다시 계산한 v5 path와
    정확히 일치해야 한다. 같은 transaction directory가 이미 있으면 runner가
    아직 credential을 소비 중이거나 이전 종료 뒤 cleanup이 누락됐다는 뜻이므로
    덮어쓰거나 scavenging하지 않고 fail-close한다.
    """

    _require_canonical_rebuildable_state_paths(state_paths=state_paths, values=values)
    canonical_transaction_id = _canonical_transaction_id(transaction_id)
    canonical_email = _canonical_email(email)
    _validate_password(password)
    ensure_pinned_runtime_state_directory(state_paths.state_root)

    bootstrap_fd = _open_bootstrap_directory(state_paths.state_root, create=True)
    transaction_fd: int | None = None
    transaction_stat: os.stat_result | None = None
    metadata: os.stat_result | None = None
    creation_complete = False
    try:
        transaction_fd, transaction_stat = _create_transaction_directory(
            bootstrap_fd,
            canonical_transaction_id,
        )
        descriptor = _create_private_file(transaction_fd, _CREDENTIAL_FILENAME)
        try:
            try:
                _write_credential_payload(
                    descriptor,
                    _credential_payload(canonical_email, password),
                )
                metadata = os.fstat(descriptor)
                _validate_private_file_stat(metadata)
            except BaseException:
                _cleanup_failed_creation(
                    transaction_fd,
                    _CREDENTIAL_FILENAME,
                    descriptor,
                )
                raise
        finally:
            os.close(descriptor)

        _fsync_directory_descriptor(transaction_fd)
        _fsync_directory_descriptor(bootstrap_fd)
        creation_complete = True
    except BaseException:
        if metadata is not None and transaction_fd is not None:
            _zeroize_and_unlink(
                transaction_fd,
                _CREDENTIAL_FILENAME,
                expected_device=metadata.st_dev,
                expected_inode=metadata.st_ino,
            )
        raise
    finally:
        if transaction_fd is not None:
            os.close(transaction_fd)
        if not creation_complete and transaction_stat is not None:
            _remove_empty_transaction_directory(
                bootstrap_fd,
                canonical_transaction_id,
                expected_device=transaction_stat.st_dev,
                expected_inode=transaction_stat.st_ino,
            )
        os.close(bootstrap_fd)

    if metadata is None:
        raise DeploymentContractError("PinVi bootstrap credential cannot be created")
    return PinviBootstrapCredentialFile(
        path=(
            state_paths.state_root
            / _BOOTSTRAP_DIRECTORY
            / canonical_transaction_id
            / _CREDENTIAL_FILENAME
        ),
        transaction_id=canonical_transaction_id,
        _state_paths=state_paths,
        _device=metadata.st_dev,
        _inode=metadata.st_ino,
    )


def cleanup_pinvi_bootstrap_credential(
    credential: PinviBootstrapCredentialFile,
    *,
    state_paths: PinnedRuntimeStatePaths,
    values: Mapping[str, str],
) -> None:
    """runner 종료를 확인한 뒤 그 transaction의 credential만 zeroize·폐기한다.

    다른 transaction directory는 열거나 열거하지 않는다. artifact의 inode가
    생성 때와 다르거나 보안 속성이 변하면 삭제하지 않고 실패한다. 이미 정상
    폐기된 artifact는 idempotent하게 처리한다.
    """

    _require_canonical_rebuildable_state_paths(state_paths=state_paths, values=values)
    transaction_id = _canonical_transaction_id(credential.transaction_id)
    expected_path = (
        state_paths.state_root
        / _BOOTSTRAP_DIRECTORY
        / transaction_id
        / _CREDENTIAL_FILENAME
    )
    if credential._state_paths != state_paths or credential.path != expected_path:
        raise DeploymentContractError("PinVi bootstrap credential artifact is invalid")

    bootstrap_fd = _open_bootstrap_directory(state_paths.state_root, create=False)
    transaction_fd: int | None = None
    transaction_stat: os.stat_result | None = None
    artifact_removed = False
    try:
        if (
            _optional_lstat_at(
                bootstrap_fd,
                transaction_id,
                "PinVi bootstrap transaction directory",
            )
            is None
        ):
            return
        transaction_fd, transaction_stat = _open_transaction_directory(
            bootstrap_fd,
            transaction_id,
        )
        _zeroize_and_unlink(
            transaction_fd,
            _CREDENTIAL_FILENAME,
            expected_device=credential._device,
            expected_inode=credential._inode,
        )
        artifact_removed = True
    finally:
        if transaction_fd is not None:
            os.close(transaction_fd)
        if artifact_removed and transaction_stat is not None:
            _remove_empty_transaction_directory(
                bootstrap_fd,
                transaction_id,
                expected_device=transaction_stat.st_dev,
                expected_inode=transaction_stat.st_ino,
            )
        os.close(bootstrap_fd)


@contextmanager
def pinvi_bootstrap_credential_file(
    *,
    state_paths: PinnedRuntimeStatePaths,
    values: Mapping[str, str],
    transaction_id: str,
    email: str,
    password: str,
) -> Iterator[PinviBootstrapCredentialFile]:
    """runner scope가 성공·실패로 끝난 뒤 exact transaction cleanup을 보장한다."""

    credential = create_pinvi_bootstrap_credential(
        state_paths=state_paths,
        values=values,
        transaction_id=transaction_id,
        email=email,
        password=password,
    )
    try:
        yield credential
    finally:
        cleanup_pinvi_bootstrap_credential(
            credential,
            state_paths=state_paths,
            values=values,
        )


def _require_canonical_rebuildable_state_paths(
    *,
    state_paths: PinnedRuntimeStatePaths,
    values: Mapping[str, str],
) -> None:
    """호출자가 임의 0700 directory로 credential을 유도하지 못하게 막는다."""

    expected = canonical_pinned_runtime_state_paths(values)
    if state_paths != expected:
        raise DeploymentContractError(
            "PinVi bootstrap state paths differ from canonical rebuildable state"
        )


def _canonical_transaction_id(value: str) -> str:
    if not isinstance(value, str):
        raise DeploymentContractError("PinVi bootstrap transaction ID is invalid")
    try:
        canonical = str(uuid.UUID(value))
    except ValueError as exc:
        raise DeploymentContractError("PinVi bootstrap transaction ID is invalid") from exc
    if canonical != value:
        raise DeploymentContractError("PinVi bootstrap transaction ID is invalid")
    return canonical


def _canonical_email(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DeploymentContractError("PinVi bootstrap email is invalid")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise DeploymentContractError("PinVi bootstrap email is invalid")
    if value.count("@") != 1 or len(value) > 254:
        raise DeploymentContractError("PinVi bootstrap email is invalid")
    local, domain = value.rsplit("@", 1)
    if not _EMAIL_LOCAL.fullmatch(local):
        raise DeploymentContractError("PinVi bootstrap email is invalid")
    labels = domain.split(".")
    if not labels or any(_EMAIL_LABEL.fullmatch(label) is None for label in labels):
        raise DeploymentContractError("PinVi bootstrap email is invalid")
    return f"{local}@{domain.lower()}"


def _validate_password(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) < 12
        or len(value) > 512
        or not value.strip()
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise DeploymentContractError("PinVi bootstrap password is invalid")


def _credential_payload(email: str, password: str) -> bytes:
    payload = json.dumps(
        {"email": email, "password": password},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > _MAX_CREDENTIAL_BYTES:
        raise DeploymentContractError("PinVi bootstrap credential payload is invalid")
    return payload


def _required_no_follow_flag() -> int:
    flag = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(flag, int) or flag == 0:
        raise DeploymentContractError("PinVi bootstrap credential requires O_NOFOLLOW")
    return flag


def _open_bootstrap_directory(state_root: Path, *, create: bool) -> int:
    no_follow = _required_no_follow_flag()
    root_before = _lstat(state_root, "PinVi bootstrap state root")
    _validate_private_directory(root_before, "PinVi bootstrap state root")
    try:
        root_fd = os.open(state_root, os.O_RDONLY | os.O_DIRECTORY | no_follow)
    except OSError as exc:
        raise DeploymentContractError("PinVi bootstrap state root is unsafe") from exc
    try:
        root_after = os.fstat(root_fd)
        _validate_private_directory(root_after, "PinVi bootstrap state root")
        if (root_before.st_dev, root_before.st_ino) != (root_after.st_dev, root_after.st_ino):
            raise DeploymentContractError("PinVi bootstrap state root changed during open")
        bootstrap_fd, _metadata = _open_private_subdirectory(
            root_fd,
            _BOOTSTRAP_DIRECTORY,
            label="PinVi bootstrap credential directory",
            create=create,
        )
        return bootstrap_fd
    finally:
        os.close(root_fd)


def _open_private_subdirectory(
    parent_fd: int,
    name: str,
    *,
    label: str,
    create: bool,
) -> tuple[int, os.stat_result]:
    no_follow = _required_no_follow_flag()
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise DeploymentContractError(f"{label} is missing") from None
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise DeploymentContractError(f"{label} cannot be created") from exc
    except OSError as exc:
        raise DeploymentContractError(f"{label} is unavailable") from exc
    _validate_private_directory(before, label)
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | no_follow,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise DeploymentContractError(f"{label} is unsafe") from exc
    try:
        opened = os.fstat(descriptor)
        _validate_private_directory(opened, label)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise DeploymentContractError(f"{label} changed during open")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, opened


def _create_transaction_directory(
    bootstrap_fd: int,
    transaction_id: str,
) -> tuple[int, os.stat_result]:
    existing = _optional_lstat_at(
        bootstrap_fd,
        transaction_id,
        "PinVi bootstrap transaction directory",
    )
    if existing is not None:
        raise DeploymentContractError("PinVi bootstrap credential transaction already exists")
    try:
        os.mkdir(transaction_id, mode=0o700, dir_fd=bootstrap_fd)
        os.fsync(bootstrap_fd)
    except OSError as exc:
        raise DeploymentContractError("PinVi bootstrap transaction cannot be created") from exc
    return _open_private_subdirectory(
        bootstrap_fd,
        transaction_id,
        label="PinVi bootstrap transaction directory",
        create=False,
    )


def _open_transaction_directory(
    bootstrap_fd: int,
    transaction_id: str,
) -> tuple[int, os.stat_result]:
    return _open_private_subdirectory(
        bootstrap_fd,
        transaction_id,
        label="PinVi bootstrap transaction directory",
        create=False,
    )


def _remove_empty_transaction_directory(
    bootstrap_fd: int,
    transaction_id: str,
    *,
    expected_device: int,
    expected_inode: int,
) -> None:
    before = _lstat_at(
        bootstrap_fd,
        transaction_id,
        "PinVi bootstrap transaction directory",
    )
    _validate_private_directory(before, "PinVi bootstrap transaction directory")
    if (before.st_dev, before.st_ino) != (expected_device, expected_inode):
        raise DeploymentContractError("PinVi bootstrap transaction changed before cleanup")
    try:
        os.rmdir(transaction_id, dir_fd=bootstrap_fd)
        _fsync_directory_descriptor(bootstrap_fd)
    except OSError as exc:
        raise DeploymentContractError("PinVi bootstrap transaction cannot be cleaned") from exc


def _create_private_file(directory_fd: int, name: str) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _required_no_follow_flag()
    try:
        return os.open(name, flags, 0o600, dir_fd=directory_fd)
    except OSError as exc:
        raise DeploymentContractError("PinVi bootstrap credential cannot be created") from exc


def _write_credential_payload(descriptor: int, payload: bytes) -> None:
    try:
        os.fchmod(descriptor, 0o600)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("credential write made no progress")
            written += count
        os.fsync(descriptor)
    except OSError as exc:
        raise DeploymentContractError("PinVi bootstrap credential cannot be written") from exc


def _cleanup_failed_creation(directory_fd: int, name: str, descriptor: int) -> None:
    """실패한 생성이 남긴 own credential inode를 zeroize·unlink한다."""

    try:
        metadata = os.fstat(descriptor)
        _validate_private_file_stat(metadata)
        _zeroize_and_unlink(
            directory_fd,
            name,
            expected_device=metadata.st_dev,
            expected_inode=metadata.st_ino,
        )
    except (DeploymentContractError, OSError) as exc:
        raise DeploymentContractError(
            "PinVi bootstrap credential failed creation cannot be cleaned"
        ) from exc


def _zeroize_and_unlink(
    directory_fd: int,
    name: str,
    *,
    expected_device: int,
    expected_inode: int,
) -> None:
    before = _optional_lstat_at(directory_fd, name, "PinVi bootstrap credential artifact")
    if before is None:
        return
    _validate_private_file_stat(before)
    if (before.st_dev, before.st_ino) != (expected_device, expected_inode):
        raise DeploymentContractError("PinVi bootstrap credential artifact changed before cleanup")
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | _required_no_follow_flag(),
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise DeploymentContractError("PinVi bootstrap credential artifact cannot be cleaned") from exc
    try:
        opened = os.fstat(descriptor)
        _validate_private_file_stat(opened)
        if (opened.st_dev, opened.st_ino) != (expected_device, expected_inode):
            raise DeploymentContractError("PinVi bootstrap credential artifact changed during cleanup")
        remaining = opened.st_size
        os.lseek(descriptor, 0, os.SEEK_SET)
        zeroes = b"\0" * min(remaining, 65_536)
        while remaining:
            count = os.write(descriptor, zeroes[: min(remaining, len(zeroes))])
            if count <= 0:
                raise OSError("credential cleanup made no progress")
            remaining -= count
        os.fsync(descriptor)
    except DeploymentContractError:
        raise
    except OSError as exc:
        raise DeploymentContractError("PinVi bootstrap credential artifact cannot be cleaned") from exc
    finally:
        os.close(descriptor)
    after = _lstat_at(directory_fd, name, "PinVi bootstrap credential artifact")
    _validate_private_file_stat(after)
    if (after.st_dev, after.st_ino) != (expected_device, expected_inode):
        raise DeploymentContractError("PinVi bootstrap credential artifact changed before removal")
    try:
        os.unlink(name, dir_fd=directory_fd)
        _fsync_directory_descriptor(directory_fd)
    except OSError as exc:
        raise DeploymentContractError("PinVi bootstrap credential artifact cannot be cleaned") from exc


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise DeploymentContractError(f"{label} is unavailable") from exc


def _lstat_at(directory_fd: int, name: str, label: str) -> os.stat_result:
    metadata = _optional_lstat_at(directory_fd, name, label)
    if metadata is None:
        raise DeploymentContractError("PinVi bootstrap credential artifact is missing")
    return metadata


def _optional_lstat_at(
    directory_fd: int,
    name: str,
    label: str,
) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DeploymentContractError(f"{label} is unavailable") from exc


def _validate_private_directory(metadata: os.stat_result, label: str) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise DeploymentContractError(f"{label} is unsafe")


def _validate_private_file_stat(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size > _MAX_CREDENTIAL_BYTES
    ):
        raise DeploymentContractError("PinVi bootstrap credential artifact is unsafe")


def _fsync_directory_descriptor(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise DeploymentContractError("PinVi bootstrap credential directory fsync failed") from exc
