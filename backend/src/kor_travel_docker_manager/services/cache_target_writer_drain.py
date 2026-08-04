"""Map 소유 cache-target writer drain의 좁은 private-command 계약.

Manager는 이 모듈을 통해서만 Map API image의 one-shot command 결과를 해석한다.
Map의 Dagster GraphQL/DB 내부 구현이나 외부 ops API를 여기로 끌어오지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Literal

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

WriterDrainOperation = Literal["begin", "attest", "restore"]
WriterDrainOwnerKind = Literal["diagnostic", "cutover"]
WriterDrainState = Literal["drained", "restored"]

WRITER_DRAIN_CONTRACT_VERSION = "ktm-cache-target-writer-drain/v1"
_BEGIN_REQUEST_FIELDS = frozenset(
    {"contract_version", "operation", "owner_kind", "owner_id"}
)
_FOLLOWUP_REQUEST_FIELDS = _BEGIN_REQUEST_FIELDS | frozenset(
    {"lease_id", "prior_receipt_sha256"}
)
_RECEIPT_FIELDS = _FOLLOWUP_REQUEST_FIELDS | frozenset(
    {
        "state",
        "snapshot_sha256",
        "run_count",
        "terminal_cancel_count",
        "receipt_sha256",
    }
)


@dataclass(frozen=True)
class WriterDrainReceipt:
    contract_version: Literal["ktm-cache-target-writer-drain/v1"]
    operation: WriterDrainOperation
    owner_kind: WriterDrainOwnerKind
    owner_id: str
    lease_id: str
    state: WriterDrainState
    prior_receipt_sha256: str | None
    snapshot_sha256: str
    run_count: Literal[0]
    terminal_cancel_count: int
    receipt_sha256: str


def build_writer_drain_request(
    *,
    operation: WriterDrainOperation,
    owner_kind: WriterDrainOwnerKind,
    owner_id: str,
    lease_id: str | None = None,
    prior_receipt_sha256: str | None = None,
) -> dict[str, str | None]:
    """입력의 exact shape를 고정한다. begin은 기존 lease/receipt를 받지 않는다."""

    _canonical_uuid(owner_id, "writer drain owner ID")
    if operation == "begin":
        if lease_id is not None or prior_receipt_sha256 is not None:
            raise DeploymentContractError(
                "writer drain begin must not bind a prior lease or receipt"
            )
    else:
        if lease_id is None or prior_receipt_sha256 is None:
            raise DeploymentContractError(
                "writer drain attest or restore requires the prior lease and receipt"
            )
        _canonical_uuid(lease_id, "writer drain lease ID")
        _validate_sha256(prior_receipt_sha256, "writer drain prior receipt")
    request: dict[str, str | None] = {
        "contract_version": WRITER_DRAIN_CONTRACT_VERSION,
        "operation": operation,
        "owner_kind": owner_kind,
        "owner_id": owner_id,
    }
    if operation != "begin":
        request["lease_id"] = lease_id
        request["prior_receipt_sha256"] = prior_receipt_sha256
    return request


def parse_writer_drain_receipt(
    *,
    stdout: str,
    stderr: str,
    request: dict[str, str | None],
) -> WriterDrainReceipt:
    """one-shot command의 단일 JSON line을 fail-closed로 검사한다."""

    operation = request.get("operation")
    expected_request_fields = (
        _BEGIN_REQUEST_FIELDS
        if operation == "begin"
        else _FOLLOWUP_REQUEST_FIELDS
    )
    if set(request) != expected_request_fields:
        raise DeploymentContractError("writer drain request schema is invalid")
    if stderr:
        raise DeploymentContractError("Map writer drain command wrote to stderr")
    lines = stdout.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise DeploymentContractError("Map writer drain command must return one JSON line")
    try:
        document = json.loads(lines[0])
        if not isinstance(document, dict) or set(document) != _RECEIPT_FIELDS:
            raise TypeError
        receipt = WriterDrainReceipt(**document)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeploymentContractError("Map writer drain receipt is invalid") from exc

    bound_fields = ("contract_version", "operation", "owner_kind", "owner_id")
    if any(getattr(receipt, field_name) != request[field_name] for field_name in bound_fields):
        raise DeploymentContractError("Map writer drain receipt binding is invalid")
    if operation != "begin" and (
        receipt.lease_id != request["lease_id"]
        or receipt.prior_receipt_sha256 != request["prior_receipt_sha256"]
    ):
        raise DeploymentContractError("Map writer drain receipt binding is invalid")
    _validate_receipt(receipt)
    return receipt


def writer_drain_receipt_sha256(receipt: WriterDrainReceipt) -> str:
    _validate_receipt(receipt)
    return receipt.receipt_sha256


def _validate_receipt(receipt: WriterDrainReceipt) -> None:
    if receipt.contract_version != WRITER_DRAIN_CONTRACT_VERSION:
        raise DeploymentContractError("writer drain contract version is invalid")
    if receipt.operation not in {"begin", "attest", "restore"}:
        raise DeploymentContractError("writer drain operation is invalid")
    if receipt.owner_kind not in {"diagnostic", "cutover"}:
        raise DeploymentContractError("writer drain owner kind is invalid")
    _canonical_uuid(receipt.owner_id, "writer drain owner ID")
    _canonical_uuid(receipt.lease_id, "writer drain lease ID")
    _validate_sha256(receipt.snapshot_sha256, "writer drain snapshot")
    _validate_sha256(receipt.receipt_sha256, "writer drain receipt")
    if receipt.prior_receipt_sha256 is not None:
        _validate_sha256(receipt.prior_receipt_sha256, "writer drain prior receipt")
    if receipt.operation == "begin":
        if receipt.prior_receipt_sha256 is not None:
            raise DeploymentContractError("writer drain begin receipt has a prior receipt")
    elif receipt.prior_receipt_sha256 is None:
        raise DeploymentContractError("writer drain receipt is missing its prior receipt")
    expected_state: WriterDrainState = (
        "restored" if receipt.operation == "restore" else "drained"
    )
    if receipt.state != expected_state:
        raise DeploymentContractError("writer drain receipt state is invalid")
    if type(receipt.run_count) is not int or receipt.run_count != 0:
        raise DeploymentContractError("writer drain receipt retained active runs")
    if (
        type(receipt.terminal_cancel_count) is not int
        or receipt.terminal_cancel_count < 0
    ):
        raise DeploymentContractError("writer drain terminal cancel count is invalid")
    document = asdict(receipt)
    actual_digest = document.pop("receipt_sha256")
    expected_digest = _logical_sha256(document)
    if actual_digest != expected_digest:
        raise DeploymentContractError("writer drain receipt digest is invalid")


def _canonical_uuid(value: str, label: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise DeploymentContractError(f"{label} is invalid") from exc
    if str(parsed) != value:
        raise DeploymentContractError(f"{label} is not canonical")


def _validate_sha256(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DeploymentContractError(f"{label} digest is invalid")


def _logical_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
