from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_writer_drain import (
    WRITER_DRAIN_CONTRACT_VERSION,
    WriterDrainReceipt,
    build_writer_drain_request,
    parse_writer_drain_receipt,
    writer_drain_receipt_sha256,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeService,
    _create_frozen_compose_descriptor,
)

_OWNER_ID = "11111111-1111-4111-8111-111111111111"
_LEASE_ID = "22222222-2222-4222-8222-222222222222"
_SNAPSHOT = "a" * 64


def _receipt(*, operation: str, prior: str | None = None) -> WriterDrainReceipt:
    payload = {
        "contract_version": WRITER_DRAIN_CONTRACT_VERSION,
        "operation": operation,
        "owner_kind": "diagnostic",
        "owner_id": _OWNER_ID,
        "lease_id": _LEASE_ID,
        "state": "restored" if operation == "restore" else "drained",
        "prior_receipt_sha256": prior,
        "snapshot_sha256": _SNAPSHOT,
        "run_count": 0,
        "terminal_cancel_count": 0,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return WriterDrainReceipt(**payload, receipt_sha256=digest)


def test_begin_receipt_requires_exact_bound_single_line_json() -> None:
    request = build_writer_drain_request(
        operation="begin", owner_kind="diagnostic", owner_id=_OWNER_ID
    )
    receipt = _receipt(operation="begin")

    parsed = parse_writer_drain_receipt(
        stdout=json.dumps(asdict(receipt), separators=(",", ":")) + "\n",
        stderr="",
        request=request,
    )

    assert parsed == receipt
    assert writer_drain_receipt_sha256(parsed) == receipt.receipt_sha256


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: {**value, "run_count": 1}, "retained active runs"),
        (lambda value: {**value, "unknown": True}, "receipt is invalid"),
        (lambda value: {**value, "receipt_sha256": "0" * 64}, "digest is invalid"),
    ],
)
def test_receipt_rejects_unbound_or_nonzero_evidence(mutate, message: str) -> None:
    request = build_writer_drain_request(
        operation="begin", owner_kind="diagnostic", owner_id=_OWNER_ID
    )
    document = mutate(asdict(_receipt(operation="begin")))

    with pytest.raises(DeploymentContractError, match=message):
        parse_writer_drain_receipt(
            stdout=json.dumps(document) + "\n", stderr="", request=request
        )


def test_receipt_rejects_boolean_zero_run_count_even_with_a_valid_digest() -> None:
    request = build_writer_drain_request(
        operation="begin", owner_kind="diagnostic", owner_id=_OWNER_ID
    )
    document = asdict(_receipt(operation="begin"))
    document["run_count"] = False
    digest_payload = {
        key: value for key, value in document.items() if key != "receipt_sha256"
    }
    document["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()

    with pytest.raises(DeploymentContractError, match="retained active runs"):
        parse_writer_drain_receipt(
            stdout=json.dumps(document) + "\n", stderr="", request=request
        )


def test_attest_and_restore_bind_previous_receipt() -> None:
    begin = _receipt(operation="begin")
    attest = _receipt(operation="attest", prior=begin.receipt_sha256)
    request = build_writer_drain_request(
        operation="attest",
        owner_kind="diagnostic",
        owner_id=_OWNER_ID,
        lease_id=_LEASE_ID,
        prior_receipt_sha256=begin.receipt_sha256,
    )
    assert (
        parse_writer_drain_receipt(
            stdout=json.dumps(asdict(attest)) + "\n", stderr="", request=request
        )
        == attest
    )


def test_manager_runs_only_frozen_map_api_private_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    service = ComposeService()
    transaction = SimpleNamespace(
        resolved={"services": {"map-api": {"image": "sha256:unused"}}},
        environment=SimpleNamespace(
            compose_path=str(tmp_path / "compose.yaml"),
            effective={"SAFE": "value"},
        ),
    )
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(command: list[str], **kwargs: object) -> Completed:
        captured["command"] = command
        captured["input"] = kwargs["input"]
        request = json.loads(str(kwargs["input"]))
        document = {
            **request,
            "lease_id": _LEASE_ID,
            "prior_receipt_sha256": None,
            "state": "drained",
            "snapshot_sha256": _SNAPSHOT,
            "run_count": 0,
            "terminal_cancel_count": 0,
        }
        document["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                document, ensure_ascii=True, separators=(",", ":"), sort_keys=True
            ).encode()
        ).hexdigest()
        return Completed(json.dumps(document, separators=(",", ":")) + "\n")

    monkeypatch.setattr(service, "_map_api_image_id", Mock(return_value="sha256:unused"))
    monkeypatch.setattr(service, "_cleanup_map_h35_runner", Mock())
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run", fake_run
    )

    receipt = service._run_map_writer_drain(
        operation="begin",
        owner_kind="diagnostic",
        owner_id=_OWNER_ID,
        transaction=transaction,
    )

    assert receipt.lease_id == _LEASE_ID
    command = captured["command"]
    assert command[:6] == [
        "docker",
        "compose",
        "--progress",
        "quiet",
        "--env-file",
        "/dev/null",
    ]
    assert "--no-deps" in command
    assert command[-3:] == [
        "kor-travel-map-api",
        "-m",
        "kortravelmap.api.writer_drain_command",
    ]
    assert "docker exec" not in " ".join(command)
    assert "DSN" not in str(captured["input"])
    assert set(json.loads(str(captured["input"]))) == {
        "contract_version",
        "operation",
        "owner_kind",
        "owner_id",
    }


def test_frozen_compose_descriptor_falls_back_to_unlinked_passed_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WSL처럼 memfd_create가 없어도 filename 없이 frozen descriptor를 전달한다."""

    monkeypatch.delattr(
        "kor_travel_docker_manager.services.compose_service.os.memfd_create",
        raising=False,
    )
    descriptor = _create_frozen_compose_descriptor("ktdm-writer-drain-test")
    try:
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        assert target.endswith(" (deleted)")
        assert os.fstat(descriptor).st_nlink == 0
    finally:
        os.close(descriptor)
