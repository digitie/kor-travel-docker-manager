from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_cutover import (
    EnableCutoverJournal,
    EnablePhase,
    InitialCutoverReceipt,
    initial_receipt_logical_sha256,
    prepare_enable_journal,
    read_owner_only_state,
    render_cache_target_sync_env,
    transition_enable_journal,
    write_cutover_state,
)

_FORWARD_PHASES = {
    "enable_preparing",
    "env_committed",
    "recreate_started",
    "verified",
    "committed",
}
_ROLLBACK_PHASES = {
    "rollback_preparing",
    "rollback_env_restored",
    "rollback_recreate_started",
    "rolled_back",
}
_JOURNAL_FIELDS = frozenset(EnableCutoverJournal.__dataclass_fields__)


class CacheTargetEnableRolledBackError(DeploymentContractError):
    """enable 실패 뒤 sync=false env/runtime 복구가 완료되었다."""

    def __init__(self, cause: Exception) -> None:
        super().__init__("cache-target enable failed and rolled back to sync=false")
        self.cause = cause


def read_canonical_env_file(
    path: Path,
    *,
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
) -> bytes:
    """frozen owner identity의 canonical env를 no-follow로 읽는다."""

    before = _validate_canonical_env_file(
        path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DeploymentContractError("canonical env cannot be read") from exc
    if not payload or len(payload) > 1_048_576:
        raise DeploymentContractError("canonical env size is invalid")
    after = _validate_canonical_env_file(
        path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    if after != before:
        raise DeploymentContractError("canonical env identity changed during read")
    return payload


def replace_canonical_env_file(
    path: Path,
    *,
    expected_sha256: str,
    replacement: bytes,
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
) -> None:
    """기대 SHA와 frozen owner/mode/identity가 맞는 env만 원자 교체한다."""

    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise DeploymentContractError("canonical env expected digest is invalid")
    if not replacement or len(replacement) > 1_048_576:
        raise DeploymentContractError("canonical env replacement size is invalid")
    before = _validate_canonical_env_file(
        path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    current = read_canonical_env_file(
        path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    if hashlib.sha256(current).hexdigest() != expected_sha256:
        raise DeploymentContractError("canonical env changed before atomic replace")
    try:
        parent_stat = path.parent.lstat()
    except OSError as exc:
        raise DeploymentContractError("canonical env parent is unavailable") from exc
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.geteuid()
        or stat.S_IMODE(parent_stat.st_mode) & 0o022
    ):
        raise DeploymentContractError("canonical env parent is unsafe")

    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(before.st_mode))
            os.fchown(stream.fileno(), before.st_uid, before.st_gid)
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if _validate_canonical_env_file(
            path,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        ) != before:
            raise DeploymentContractError(
                "canonical env identity changed before atomic replace"
            )
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise DeploymentContractError("canonical env changed before atomic replace")
        os.replace(temporary, path)
        temporary = None
        _validate_canonical_env_file(
            path,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise DeploymentContractError("canonical env atomic replace failed") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_canonical_env_file(
    path: Path,
    *,
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
) -> os.stat_result:
    """호출자 소유 또는 frozen transaction의 명시 owner만 허용한다."""

    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError("canonical env is unavailable") from exc
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid
        != (os.geteuid() if expected_owner_uid is None else expected_owner_uid)
        or (
            expected_owner_gid is not None
            and file_stat.st_gid != expected_owner_gid
        )
        or file_stat.st_nlink != 1
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise DeploymentContractError("canonical env file is unsafe")
    return file_stat


def execute_cache_target_enable(
    *,
    receipt: InitialCutoverReceipt,
    journal_path: Path,
    enabled_resolved_compose_sha256: str,
    read_env: Callable[[], bytes],
    replace_env: Callable[[str, bytes], None],
    attest: Callable[[bool], None],
    recreate_pinvi_api: Callable[[bool], None],
    causal_canary: Callable[[str], Mapping[str, Any]],
    window_transaction_id: str | None = None,
) -> EnableCutoverJournal:
    """caller가 잡은 하나의 global lock 안에서 enable 또는 crash resume를 완료한다."""

    env_bytes = read_env()
    journal = _load_or_prepare_journal(
        receipt=receipt,
        journal_path=journal_path,
        env_bytes=env_bytes,
        enabled_resolved_compose_sha256=enabled_resolved_compose_sha256,
        attest=attest,
        window_transaction_id=window_transaction_id,
    )
    old_env, new_env = _bound_env_versions(receipt, journal, env_bytes)
    if journal.phase == "committed":
        attest(True)
        return journal
    if journal.phase == "rolled_back":
        attest(False)
        return journal
    if journal.phase in _ROLLBACK_PHASES:
        return _resume_rollback(
            journal_path=journal_path,
            journal=journal,
            current_env=env_bytes,
            old_env=old_env,
            replace_env=replace_env,
            recreate_pinvi_api=recreate_pinvi_api,
            attest=attest,
        )

    try:
        current_sha = hashlib.sha256(env_bytes).hexdigest()
        if journal.phase == "enable_preparing":
            if current_sha == journal.old_env_sha256:
                replace_env(journal.old_env_sha256, new_env)
            elif current_sha != journal.new_env_sha256:
                raise DeploymentContractError(
                    "canonical env is foreign at enable_preparing"
                )
            journal = _persist_transition(
                journal_path, journal, "env_committed"
            )
        if journal.phase == "env_committed":
            journal = _persist_transition(
                journal_path, journal, "recreate_started"
            )
        if journal.phase == "recreate_started":
            recreate_pinvi_api(True)
            attest(True)
            canary_evidence = causal_canary(journal.transaction_id)
            if not canary_evidence:
                raise DeploymentContractError("causal canary returned no evidence")
            _validate_causal_evidence_binding(
                canary_evidence,
                journal=journal,
                receipt=receipt,
            )
            journal = transition_enable_journal(
                journal,
                "verified",
                verified_evidence=canary_evidence,
            )
            write_cutover_state(journal_path, journal)
        if journal.phase == "verified":
            attest(True)
            journal = _persist_transition(journal_path, journal, "committed")
        return journal
    except Exception as cause:
        current_env = read_env()
        rolled_back = _resume_rollback(
            journal_path=journal_path,
            journal=journal,
            current_env=current_env,
            old_env=old_env,
            replace_env=replace_env,
            recreate_pinvi_api=recreate_pinvi_api,
            attest=attest,
        )
        if rolled_back.phase != "rolled_back":
            raise DeploymentContractError(
                "cache-target rollback did not reach terminal state"
            ) from cause
        raise CacheTargetEnableRolledBackError(cause) from cause


def read_enable_cutover_journal(path: Path) -> EnableCutoverJournal:
    try:
        payload = read_owner_only_state(path)
        document = json.loads(payload)
        if not isinstance(document, dict) or set(document) != _JOURNAL_FIELDS:
            raise TypeError
        required_strings = _JOURNAL_FIELDS - {
            "version",
            "window_transaction_id",
            "attempt",
            "supersedes_transaction_id",
            "verified_evidence_sha256",
        }
        if (
            type(document["version"]) is not int
            or type(document["attempt"]) is not int
            or any(not isinstance(document[name], str) for name in required_strings)
            or (
                document["window_transaction_id"] is not None
                and not isinstance(document["window_transaction_id"], str)
            )
            or (
                document["supersedes_transaction_id"] is not None
                and not isinstance(document["supersedes_transaction_id"], str)
            )
            or (
                document["verified_evidence_sha256"] is not None
                and not isinstance(document["verified_evidence_sha256"], str)
            )
        ):
            raise TypeError
        journal = EnableCutoverJournal(**document)
    except DeploymentContractError:
        raise
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError("cache-target enable journal is invalid") from exc
    if journal.version != 5 or journal.phase not in _FORWARD_PHASES | _ROLLBACK_PHASES:
        raise DeploymentContractError("cache-target enable journal contract is invalid")
    try:
        if str(uuid.UUID(journal.transaction_id)) != journal.transaction_id:
            raise ValueError
        if str(uuid.UUID(journal.cutover_id)) != journal.cutover_id:
            raise ValueError
        if journal.window_transaction_id is not None and (
            str(uuid.UUID(journal.window_transaction_id))
            != journal.window_transaction_id
        ):
            raise ValueError
        if journal.supersedes_transaction_id is not None and (
            str(uuid.UUID(journal.supersedes_transaction_id))
            != journal.supersedes_transaction_id
        ):
            raise ValueError
    except ValueError as exc:
        raise DeploymentContractError("cache-target enable journal UUID is invalid") from exc
    for digest in (
        journal.initial_receipt_sha256,
        journal.old_env_sha256,
        journal.new_env_sha256,
        journal.enabled_resolved_compose_sha256,
        journal.runtime_generation_sha256,
    ):
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise DeploymentContractError("cache-target enable journal digest is invalid")
    if (
        journal.verified_evidence_sha256 is not None
        and re.fullmatch(r"[0-9a-f]{64}", journal.verified_evidence_sha256)
        is None
    ):
        raise DeploymentContractError("cache-target verified evidence digest is invalid")
    if journal.old_env_sha256 == journal.new_env_sha256:
        raise DeploymentContractError("cache-target enable journal env transition is invalid")
    if (
        journal.attempt <= 0
        or (journal.attempt == 1) != (journal.supersedes_transaction_id is None)
    ):
        raise DeploymentContractError("cache-target enable attempt chain is invalid")
    if journal.phase in {"verified", "committed"} and journal.verified_evidence_sha256 is None:
        raise DeploymentContractError("cache-target verified journal evidence is missing")
    if (
        journal.phase in {"enable_preparing", "env_committed", "recreate_started"}
        and journal.verified_evidence_sha256 is not None
    ):
        raise DeploymentContractError("cache-target unverified journal has evidence")
    return journal


def _load_or_prepare_journal(
    *,
    receipt: InitialCutoverReceipt,
    journal_path: Path,
    env_bytes: bytes,
    enabled_resolved_compose_sha256: str,
    attest: Callable[[bool], None],
    window_transaction_id: str | None,
) -> EnableCutoverJournal:
    try:
        journal_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise DeploymentContractError(
            "cache-target enable journal path is unavailable"
        ) from exc
    else:
        journal = read_enable_cutover_journal(journal_path)
        _validate_journal_binding(journal, receipt)
        if (
            journal.enabled_resolved_compose_sha256
            != enabled_resolved_compose_sha256
        ):
            raise DeploymentContractError(
                "cache-target enabled resolved compose evidence drifted"
            )
        if journal.window_transaction_id != window_transaction_id:
            raise DeploymentContractError(
                "cache-target enable window transaction drifted"
            )
        if journal.phase == "rolled_back" and window_transaction_id is not None:
            attest(False)
            return _supersede_rolled_back_journal(
                journal_path=journal_path,
                journal=journal,
                receipt=receipt,
                env_bytes=env_bytes,
                enabled_resolved_compose_sha256=(
                    enabled_resolved_compose_sha256
                ),
            )
        return journal
    env_sha = hashlib.sha256(env_bytes).hexdigest()
    if env_sha != receipt.evidence.env_sha256:
        raise DeploymentContractError("canonical env differs from initial receipt")
    new_env = render_cache_target_sync_env(
        env_bytes,
        expected="false",
        replacement="true",
    )
    attest(False)
    journal = prepare_enable_journal(
        receipt=receipt,
        old_env_sha256=env_sha,
        new_env_sha256=hashlib.sha256(new_env).hexdigest(),
        enabled_resolved_compose_sha256=enabled_resolved_compose_sha256,
        window_transaction_id=window_transaction_id,
    )
    write_cutover_state(journal_path, journal)
    return journal


def _supersede_rolled_back_journal(
    *,
    journal_path: Path,
    journal: EnableCutoverJournal,
    receipt: InitialCutoverReceipt,
    env_bytes: bytes,
    enabled_resolved_compose_sha256: str,
) -> EnableCutoverJournal:
    if hashlib.sha256(env_bytes).hexdigest() != journal.old_env_sha256:
        raise DeploymentContractError(
            "canonical env is not disabled before enable retry"
        )
    window_transaction_id = journal.window_transaction_id
    if window_transaction_id is None:
        raise DeploymentContractError(
            "cache-target enable retry has no window transaction"
        )
    history_directory = journal_path.parent / "cache-target-enable-history-v2"
    history_directory.mkdir(mode=0o700, parents=False, exist_ok=True)
    history_path = history_directory / (
        f"{window_transaction_id}-attempt-{journal.attempt}-"
        f"{journal.transaction_id}.json"
    )
    try:
        history_path.lstat()
    except FileNotFoundError:
        write_cutover_state(history_path, journal)
    except OSError as exc:
        raise DeploymentContractError(
            "cache-target enable retry history is unavailable"
        ) from exc
    else:
        archived = read_enable_cutover_journal(history_path)
        if archived != journal:
            raise DeploymentContractError(
                "cache-target enable retry history is foreign"
            )
    enabled_env = render_cache_target_sync_env(
        env_bytes,
        expected="false",
        replacement="true",
    )
    successor = prepare_enable_journal(
        receipt=receipt,
        old_env_sha256=journal.old_env_sha256,
        new_env_sha256=hashlib.sha256(enabled_env).hexdigest(),
        enabled_resolved_compose_sha256=enabled_resolved_compose_sha256,
        window_transaction_id=window_transaction_id,
        attempt=journal.attempt + 1,
        supersedes_transaction_id=journal.transaction_id,
    )
    write_cutover_state(journal_path, successor)
    return successor


def _bound_env_versions(
    receipt: InitialCutoverReceipt,
    journal: EnableCutoverJournal,
    current: bytes,
) -> tuple[bytes, bytes]:
    current_sha = hashlib.sha256(current).hexdigest()
    if current_sha == journal.old_env_sha256:
        old_env = current
        new_env = render_cache_target_sync_env(
            current, expected="false", replacement="true"
        )
    elif current_sha == journal.new_env_sha256:
        new_env = current
        old_env = render_cache_target_sync_env(
            current, expected="true", replacement="false"
        )
    else:
        raise DeploymentContractError("canonical env is foreign to enable journal")
    if (
        hashlib.sha256(old_env).hexdigest() != receipt.evidence.env_sha256
        or hashlib.sha256(new_env).hexdigest() != journal.new_env_sha256
    ):
        raise DeploymentContractError("enable journal env binding is invalid")
    return old_env, new_env


def _resume_rollback(
    *,
    journal_path: Path,
    journal: EnableCutoverJournal,
    current_env: bytes,
    old_env: bytes,
    replace_env: Callable[[str, bytes], None],
    recreate_pinvi_api: Callable[[bool], None],
    attest: Callable[[bool], None],
) -> EnableCutoverJournal:
    current_sha = hashlib.sha256(current_env).hexdigest()
    if journal.phase in _FORWARD_PHASES:
        journal = _persist_transition(
            journal_path, journal, "rollback_preparing"
        )
    if journal.phase == "rollback_preparing":
        if current_sha == journal.new_env_sha256:
            replace_env(journal.new_env_sha256, old_env)
        elif current_sha != journal.old_env_sha256:
            raise DeploymentContractError("canonical env is foreign during rollback")
        journal = _persist_transition(
            journal_path, journal, "rollback_env_restored"
        )
    if journal.phase == "rollback_env_restored":
        journal = _persist_transition(
            journal_path, journal, "rollback_recreate_started"
        )
    if journal.phase == "rollback_recreate_started":
        recreate_pinvi_api(False)
        attest(False)
        journal = _persist_transition(journal_path, journal, "rolled_back")
    return journal


def _persist_transition(
    path: Path,
    journal: EnableCutoverJournal,
    phase: EnablePhase,
) -> EnableCutoverJournal:
    current = read_enable_cutover_journal(path)
    if current != journal:
        raise DeploymentContractError("cache-target enable journal changed concurrently")
    updated = transition_enable_journal(journal, phase)
    write_cutover_state(path, updated)
    return updated


def _validate_journal_binding(
    journal: EnableCutoverJournal,
    receipt: InitialCutoverReceipt,
) -> None:
    if (
        journal.cutover_id != receipt.cutover_id
        or journal.initial_receipt_sha256
        != initial_receipt_logical_sha256(receipt)
        or journal.old_env_sha256 != receipt.evidence.env_sha256
        or journal.runtime_generation_sha256
        != receipt.evidence.runtime_generation_sha256
    ):
        raise DeploymentContractError("cache-target enable journal is foreign")
    safe_payload = json.dumps(asdict(journal), sort_keys=True)
    if any(
        forbidden in safe_payload
        for forbidden in ("registry_json", "token_sha256", "recovery_token")
    ):
        raise DeploymentContractError("cache-target enable journal leaks protected data")


def _validate_causal_evidence_binding(
    evidence: Mapping[str, Any],
    *,
    journal: EnableCutoverJournal,
    receipt: InitialCutoverReceipt,
) -> None:
    expected = {
        "run_id": journal.transaction_id,
        "cutover_id": receipt.cutover_id,
        "runtime_generation_sha256": receipt.evidence.runtime_generation_sha256,
        "contract_generation": receipt.evidence.expected_contract_generation,
        "local_count": receipt.count,
        "remote_count": receipt.count,
        "local_merkle_root": receipt.merkle_root,
        "remote_merkle_root": receipt.merkle_root,
    }
    if any(evidence.get(name) != value for name, value in expected.items()):
        raise DeploymentContractError("causal canary evidence binding is invalid")
