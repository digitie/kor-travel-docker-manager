from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_INITIAL_OUTPUT_PATTERN = re.compile(
    r"^initial cutover complete "
    r"cutover_id=(?P<cutover_id>[0-9a-f-]{36}) "
    r"request_id=(?P<request_id>[0-9a-f-]{36}) "
    r"count=(?P<count>[0-9]+) "
    r"merkle_root=(?P<merkle_root>[0-9a-f]{64}) "
    r"published=(?P<published>[0-9]+)$"
)
EnablePhase = Literal[
    "enable_preparing",
    "env_committed",
    "recreate_started",
    "verified",
    "committed",
    "rollback_preparing",
    "rollback_env_restored",
    "rollback_recreate_started",
    "rolled_back",
]
_ENABLE_FORWARD_PHASES: tuple[EnablePhase, ...] = (
    "enable_preparing",
    "env_committed",
    "recreate_started",
    "verified",
    "committed",
)
_ROLLBACK_PHASES: tuple[EnablePhase, ...] = (
    "rollback_preparing",
    "rollback_env_restored",
    "rollback_recreate_started",
    "rolled_back",
)


@dataclass(frozen=True)
class CacheTargetFrozenEvidence:
    env_sha256: str
    raw_compose_sha256: str
    resolved_compose_sha256: str
    active_pair_sha256: str
    rollback_pair_sha256: str
    role_binding_sha256: str
    expected_openapi_sha256: str
    expected_source_revision: str
    expected_contract_generation: str


@dataclass(frozen=True)
class InitialCutoverResult:
    cutover_id: str
    request_id: str
    count: int
    merkle_root: str
    published: int


@dataclass(frozen=True)
class InitialCutoverReceipt:
    version: Literal[1]
    cutover_id: str
    expected_restore_epoch: int
    reason_sha256: str
    request_id: str
    count: int
    merkle_root: str
    published: int
    evidence: CacheTargetFrozenEvidence


@dataclass(frozen=True)
class EnableCutoverJournal:
    version: Literal[1]
    transaction_id: str
    cutover_id: str
    phase: EnablePhase
    initial_receipt_sha256: str
    old_env_sha256: str
    new_env_sha256: str
    active_pair_sha256: str
    rollback_pair_sha256: str
    verified_evidence_sha256: str | None = None


def parse_initial_cutover_output(output: str) -> InitialCutoverResult:
    lines = [line for line in output.splitlines() if line]
    if len(lines) != 1 or (matched := _INITIAL_OUTPUT_PATTERN.fullmatch(lines[0])) is None:
        raise DeploymentContractError("initial cutover runner output is invalid")
    values = matched.groupdict()
    try:
        cutover_id = str(uuid.UUID(values["cutover_id"]))
        request_id = str(uuid.UUID(values["request_id"]))
    except ValueError as exc:
        raise DeploymentContractError("initial cutover runner UUID is invalid") from exc
    return InitialCutoverResult(
        cutover_id=cutover_id,
        request_id=request_id,
        count=int(values["count"]),
        merkle_root=values["merkle_root"],
        published=int(values["published"]),
    )


def build_initial_cutover_receipt(
    *,
    cutover_id: str,
    expected_restore_epoch: int,
    reason: str,
    evidence: CacheTargetFrozenEvidence,
    result: InitialCutoverResult,
) -> InitialCutoverReceipt:
    canonical_cutover_id = _canonical_uuid(cutover_id, "cutover ID")
    if result.cutover_id != canonical_cutover_id:
        raise DeploymentContractError("initial cutover result uses a foreign cutover ID")
    if expected_restore_epoch <= 0:
        raise DeploymentContractError("expected restore epoch must be positive")
    if not reason or reason != reason.strip() or "\n" in reason or "\r" in reason:
        raise DeploymentContractError("initial cutover reason is invalid")
    _validate_frozen_evidence(evidence)
    return InitialCutoverReceipt(
        version=1,
        cutover_id=canonical_cutover_id,
        expected_restore_epoch=expected_restore_epoch,
        reason_sha256=hashlib.sha256(reason.encode()).hexdigest(),
        request_id=result.request_id,
        count=result.count,
        merkle_root=result.merkle_root,
        published=result.published,
        evidence=evidence,
    )


def initial_receipt_logical_sha256(receipt: InitialCutoverReceipt) -> str:
    return _logical_sha256(asdict(receipt))


def commit_initial_cutover_receipt(
    path: Path, receipt: InitialCutoverReceipt
) -> str:
    if path.exists():
        existing = read_initial_cutover_receipt(path)
        if existing != receipt:
            raise DeploymentContractError(
                "existing initial cutover receipt belongs to foreign evidence"
            )
        return initial_receipt_logical_sha256(existing)
    return write_cutover_state(path, receipt)


def read_initial_cutover_receipt(path: Path) -> InitialCutoverReceipt:
    payload = _read_owner_only_state(path)
    try:
        document = json.loads(payload)
        if not isinstance(document, dict):
            raise TypeError
        evidence_value = document.pop("evidence")
        if not isinstance(evidence_value, dict):
            raise TypeError
        receipt = InitialCutoverReceipt(
            evidence=CacheTargetFrozenEvidence(**evidence_value),
            **document,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise DeploymentContractError("initial cutover receipt is invalid") from exc
    if receipt.version != 1:
        raise DeploymentContractError("initial cutover receipt version is invalid")
    _validate_frozen_evidence(receipt.evidence)
    return receipt


def prepare_enable_journal(
    *,
    receipt: InitialCutoverReceipt,
    old_env_sha256: str,
    new_env_sha256: str,
) -> EnableCutoverJournal:
    _validate_sha256(old_env_sha256, "old env")
    _validate_sha256(new_env_sha256, "new env")
    if old_env_sha256 == new_env_sha256:
        raise DeploymentContractError("enable env transition must change canonical bytes")
    return EnableCutoverJournal(
        version=1,
        transaction_id=str(uuid.uuid4()),
        cutover_id=receipt.cutover_id,
        phase="enable_preparing",
        initial_receipt_sha256=initial_receipt_logical_sha256(receipt),
        old_env_sha256=old_env_sha256,
        new_env_sha256=new_env_sha256,
        active_pair_sha256=receipt.evidence.active_pair_sha256,
        rollback_pair_sha256=receipt.evidence.rollback_pair_sha256,
    )


def transition_enable_journal(
    journal: EnableCutoverJournal,
    phase: EnablePhase,
    *,
    verified_evidence: Mapping[str, Any] | None = None,
) -> EnableCutoverJournal:
    if phase == journal.phase:
        return journal
    allowed_next = _allowed_next_phases(journal.phase)
    if phase not in allowed_next:
        raise DeploymentContractError(
            f"cache-target enable phase cannot transition {journal.phase} -> {phase}"
        )
    verified_sha = journal.verified_evidence_sha256
    if phase == "verified":
        if verified_evidence is None:
            raise DeploymentContractError("verified phase requires causal canary evidence")
        verified_sha = _logical_sha256(verified_evidence)
    elif verified_evidence is not None:
        raise DeploymentContractError("causal canary evidence is accepted only at verified")
    if phase == "committed" and verified_sha is None:
        raise DeploymentContractError("enable commit requires verified causal evidence")
    return EnableCutoverJournal(
        **{
            **asdict(journal),
            "phase": phase,
            "verified_evidence_sha256": verified_sha,
        }
    )


def write_cutover_state(path: Path, state: InitialCutoverReceipt | EnableCutoverJournal) -> str:
    payload = json.dumps(
        asdict(state), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    _validate_state_payload(payload)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_stat = path.parent.lstat()
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.geteuid()
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
    ):
        raise DeploymentContractError("cache-target state directory is unsafe")
    if path.exists():
        _read_owner_only_state(path)
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        _validate_owner_only_file(path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return hashlib.sha256(payload).hexdigest()


def with_recovery_secret_file(
    state_directory: Path,
    recovery_token: str,
    runner: Callable[[Path], InitialCutoverResult],
) -> InitialCutoverResult:
    if len(recovery_token) < 32 or any(character.isspace() for character in recovery_token):
        raise DeploymentContractError("recovery token must be whitespace-free and 32+ chars")
    state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_stat = state_directory.lstat()
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.geteuid()
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
    ):
        raise DeploymentContractError("cache-target secret directory is unsafe")
    descriptor, name = tempfile.mkstemp(
        dir=state_directory, prefix=".cache-target-recovery.", suffix=".secret"
    )
    secret_path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(recovery_token.encode())
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        return runner(secret_path)
    finally:
        secret_path.unlink(missing_ok=True)


def _allowed_next_phases(phase: EnablePhase) -> frozenset[EnablePhase]:
    if phase in _ENABLE_FORWARD_PHASES:
        index = _ENABLE_FORWARD_PHASES.index(phase)
        forward: frozenset[EnablePhase] = (
            frozenset({_ENABLE_FORWARD_PHASES[index + 1]})
            if index + 1 < len(_ENABLE_FORWARD_PHASES)
            else frozenset()
        )
        rollback: frozenset[EnablePhase] = (
            frozenset({"rollback_preparing"})
            if phase not in {"committed", "enable_preparing"}
            else frozenset()
        )
        return frozenset((*forward, *rollback))
    index = _ROLLBACK_PHASES.index(phase)
    return (
        frozenset({_ROLLBACK_PHASES[index + 1]})
        if index + 1 < len(_ROLLBACK_PHASES)
        else frozenset()
    )


def _validate_frozen_evidence(evidence: CacheTargetFrozenEvidence) -> None:
    for label, value in asdict(evidence).items():
        if label == "expected_source_revision":
            if not re.fullmatch(r"[0-9a-f]{40}", value):
                raise DeploymentContractError("expected source revision is invalid")
        elif label == "expected_contract_generation":
            if not re.fullmatch(r"[1-9][0-9]*", value):
                raise DeploymentContractError("expected contract generation is invalid")
        else:
            _validate_sha256(value, label)


def _validate_state_payload(payload: bytes) -> None:
    text = payload.decode()
    forbidden = (
        "registry_json",
        "token_sha256",
        "command_token",
        "consumer_token",
        "restore_fence_token",
        "recovery_token",
    )
    if any(name in text for name in forbidden):
        raise DeploymentContractError("cache-target state payload contains protected data")


def _read_owner_only_state(path: Path) -> bytes:
    _validate_owner_only_file(path)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DeploymentContractError("cache-target state cannot be read") from exc
    if not payload or len(payload) > 65_536:
        raise DeploymentContractError("cache-target state size is invalid")
    _validate_owner_only_file(path)
    return payload


def _validate_owner_only_file(path: Path) -> None:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError("cache-target state is unavailable") from exc
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != os.geteuid()
        or file_stat.st_nlink != 1
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise DeploymentContractError("cache-target state file is unsafe")


def _logical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _validate_sha256(value: str, label: str) -> None:
    if not _SHA256_PATTERN.fullmatch(value):
        raise DeploymentContractError(f"{label} SHA-256 is invalid")


def _canonical_uuid(value: str, label: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise DeploymentContractError(f"{label} is invalid") from exc
    canonical = str(parsed)
    if value != canonical:
        raise DeploymentContractError(f"{label} must be canonical lowercase UUID")
    return canonical
