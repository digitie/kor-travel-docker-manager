"""T-VN-41 private writer-drain runner의 격리 Compose rehearsal.

실제 production Compose는 고정 port와 host mount를 공유하므로 사용하지 않는다. 이 fixture는
전용 project/network와 local immutable Python image, 임시 bind mount만 사용해 Manager의
frozen one-shot 경계가 Map command의 strict stdin/argv 계약을 지키는지만 검증한다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kor_travel_docker_manager.services.cache_target_writer_drain import (
    writer_drain_receipt_sha256,
)
from kor_travel_docker_manager.services.compose_service import ComposeService

_REQUIRED_GATE_ENV = "KTDM_REQUIRE_DOCKER_INTEGRATION"
_PYTHON_IMAGE = "python:3.13-slim"
_OWNER_ID = "11111111-1111-4111-8111-111111111111"


def _require_docker_fixture() -> str:
    required = os.environ.get(_REQUIRED_GATE_ENV, "0").strip()
    if required not in {"0", "1"}:
        pytest.fail(f"{_REQUIRED_GATE_ENV}는 0 또는 1이어야 함")
    try:
        inspected = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", _PYTHON_IMAGE],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if required == "1":
            pytest.fail(f"격리 Docker fixture를 사용할 수 없음: {exc}")
        pytest.skip(f"격리 Docker fixture를 사용할 수 없음; 필수 gate는 {_REQUIRED_GATE_ENV}=1")
    image_id = inspected.stdout.strip()
    if inspected.returncode != 0 or not image_id.startswith("sha256:"):
        if required == "1":
            pytest.fail("필수 격리 Docker fixture image를 확인할 수 없음")
        pytest.skip(
            f"local {_PYTHON_IMAGE} fixture가 없음; 필수 gate는 {_REQUIRED_GATE_ENV}=1"
        )
    return image_id


def _write_private_command_fixture(root: Path) -> None:
    module = root / "kortravelmap" / "api" / "writer_drain_command.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        """
import hashlib
import json
import sys

CONTRACT = \"ktm-cache-target-writer-drain/v1\"
LEASE = \"22222222-2222-4222-8222-222222222222\"
SNAPSHOT = \"a\" * 64

if sys.argv[1:]:
    raise SystemExit(2)
request = json.loads(sys.stdin.buffer.read())
operation = request.get(\"operation\")
required = {\"contract_version\", \"operation\", \"owner_kind\", \"owner_id\"}
if operation != \"begin\":
    required |= {\"lease_id\", \"prior_receipt_sha256\"}
if request.get(\"contract_version\") != CONTRACT or set(request) != required:
    raise SystemExit(2)
if operation not in {\"begin\", \"attest\", \"restore\"}:
    raise SystemExit(2)
receipt = {
    \"contract_version\": CONTRACT,
    \"operation\": operation,
    \"owner_kind\": request[\"owner_kind\"],
    \"owner_id\": request[\"owner_id\"],
    \"lease_id\": LEASE,
    \"state\": \"restored\" if operation == \"restore\" else \"drained\",
    \"prior_receipt_sha256\": request.get(\"prior_receipt_sha256\"),
    \"snapshot_sha256\": SNAPSHOT,
    \"run_count\": 0,
    \"terminal_cancel_count\": 0,
}
receipt[\"receipt_sha256\"] = hashlib.sha256(
    json.dumps(receipt, ensure_ascii=True, separators=(\",\", \":\"), sort_keys=True).encode()
).hexdigest()
sys.stdout.write(json.dumps(receipt, separators=(\",\", \":\")) + \"\\n\")
""".lstrip(),
        encoding="utf-8",
    )


def _compose_down(compose_path: Path) -> None:
    completed = subprocess.run(
        ["docker", "compose", "--file", str(compose_path), "down", "--volumes", "--remove-orphans"],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_frozen_runner_rehearses_exact_map_private_command_contract(tmp_path: Path) -> None:
    """실제 one-shot container가 argv 없음·strict begin shape를 직접 거부/수락한다."""

    image_id = _require_docker_fixture()
    fixture_root = tmp_path / "fixture"
    _write_private_command_fixture(fixture_root)
    compose_path = tmp_path / "compose.yaml"
    resolved = {
        "services": {
            "kor-travel-map-api": {
                "image": image_id,
                "environment": {"PYTHONPATH": "/fixture"},
                "volumes": [f"{fixture_root}:/fixture:ro"],
            }
        }
    }
    compose_path.write_text(yaml.safe_dump(resolved, sort_keys=False), encoding="utf-8")
    transaction = SimpleNamespace(
        resolved=resolved,
        environment=SimpleNamespace(compose_path=str(compose_path), effective={}),
    )
    service = ComposeService()

    try:
        begin = service._run_map_writer_drain(
            operation="begin",
            owner_kind="diagnostic",
            owner_id=_OWNER_ID,
            transaction=transaction,
        )
        attest = service._run_map_writer_drain(
            operation="attest",
            owner_kind="diagnostic",
            owner_id=_OWNER_ID,
            transaction=transaction,
            lease_id=begin.lease_id,
            prior_receipt_sha256=writer_drain_receipt_sha256(begin),
        )
        restored = service._run_map_writer_drain(
            operation="restore",
            owner_kind="diagnostic",
            owner_id=_OWNER_ID,
            transaction=transaction,
            lease_id=attest.lease_id,
            prior_receipt_sha256=writer_drain_receipt_sha256(attest),
        )
    finally:
        _compose_down(compose_path)

    assert begin.state == "drained"
    assert attest.prior_receipt_sha256 == writer_drain_receipt_sha256(begin)
    assert restored.state == "restored"
    assert restored.prior_receipt_sha256 == writer_drain_receipt_sha256(attest)
