from __future__ import annotations

import json
import stat
import uuid
from dataclasses import asdict
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_cutover import (
    CacheTargetFrozenEvidence,
    InitialCutoverReceipt,
    InitialCutoverResult,
    build_initial_cutover_receipt,
    commit_initial_cutover_receipt,
    initial_receipt_logical_sha256,
    parse_initial_cutover_output,
    prepare_enable_journal,
    transition_enable_journal,
    with_recovery_secret_file,
    write_cutover_state,
)

_CUTOVER_ID = "11111111-1111-4111-8111-111111111111"
_REQUEST_ID = "22222222-2222-4222-8222-222222222222"


def _evidence() -> CacheTargetFrozenEvidence:
    return CacheTargetFrozenEvidence(
        env_sha256="1" * 64,
        raw_compose_sha256="2" * 64,
        resolved_compose_sha256="3" * 64,
        active_pair_sha256="4" * 64,
        rollback_pair_sha256="5" * 64,
        role_binding_sha256="6" * 64,
        expected_openapi_sha256="7" * 64,
        expected_source_revision="8" * 40,
        expected_contract_generation="7",
    )


def _result() -> InitialCutoverResult:
    return InitialCutoverResult(
        cutover_id=_CUTOVER_ID,
        request_id=_REQUEST_ID,
        count=12,
        merkle_root="9" * 64,
        published=12,
    )


def _receipt() -> InitialCutoverReceipt:
    return build_initial_cutover_receipt(
        cutover_id=_CUTOVER_ID,
        expected_restore_epoch=3,
        reason="production initial cutover",
        evidence=_evidence(),
        result=_result(),
    )


def test_parse_initial_cutover_output_accepts_only_single_exact_line() -> None:
    result = parse_initial_cutover_output(
        "initial cutover complete "
        f"cutover_id={_CUTOVER_ID} request_id={_REQUEST_ID} "
        f"count=12 merkle_root={'9' * 64} published=12\n"
    )

    assert result == _result()
    with pytest.raises(DeploymentContractError):
        parse_initial_cutover_output("debug\n" + _CUTOVER_ID)


def test_initial_receipt_binds_all_frozen_evidence_without_role_digests() -> None:
    receipt = _receipt()
    payload = json.dumps(asdict(receipt), sort_keys=True)

    assert receipt.reason_sha256 != "production initial cutover"
    assert len(initial_receipt_logical_sha256(receipt)) == 64
    for forbidden in (
        "registry_json",
        "token_sha256",
        "command_token",
        "consumer_token",
        "recovery_token",
    ):
        assert forbidden not in payload


def test_initial_receipt_rejects_foreign_result_and_stale_hash() -> None:
    with pytest.raises(DeploymentContractError, match="foreign cutover"):
        build_initial_cutover_receipt(
            cutover_id=str(uuid.uuid4()),
            expected_restore_epoch=3,
            reason="production initial cutover",
            evidence=_evidence(),
            result=_result(),
        )
    invalid = CacheTargetFrozenEvidence(**{**asdict(_evidence()), "env_sha256": "bad"})
    with pytest.raises(DeploymentContractError, match="env_sha256"):
        build_initial_cutover_receipt(
            cutover_id=_CUTOVER_ID,
            expected_restore_epoch=3,
            reason="production initial cutover",
            evidence=invalid,
            result=_result(),
        )


def test_enable_journal_requires_ordered_forward_and_causal_verification() -> None:
    journal = prepare_enable_journal(
        receipt=_receipt(), old_env_sha256="a" * 64, new_env_sha256="b" * 64
    )
    assert journal.phase == "enable_preparing"
    with pytest.raises(DeploymentContractError):
        transition_enable_journal(journal, "recreate_started")
    journal = transition_enable_journal(journal, "env_committed")
    journal = transition_enable_journal(journal, "recreate_started")
    with pytest.raises(DeploymentContractError, match="causal canary"):
        transition_enable_journal(journal, "verified")
    journal = transition_enable_journal(
        journal,
        "verified",
        verified_evidence={
            "command_id": str(uuid.uuid4()),
            "acked": True,
            "lag": 0,
            "dlq": 0,
            "count": 12,
            "merkle_root": "9" * 64,
        },
    )
    journal = transition_enable_journal(journal, "committed")
    assert journal.phase == "committed"
    assert journal.verified_evidence_sha256 is not None


def test_enable_journal_rollback_is_ordered_and_terminal() -> None:
    journal = prepare_enable_journal(
        receipt=_receipt(), old_env_sha256="a" * 64, new_env_sha256="b" * 64
    )
    journal = transition_enable_journal(journal, "env_committed")
    journal = transition_enable_journal(journal, "rollback_preparing")
    journal = transition_enable_journal(journal, "rollback_env_restored")
    journal = transition_enable_journal(journal, "rollback_recreate_started")
    journal = transition_enable_journal(journal, "rolled_back")
    assert journal.phase == "rolled_back"
    with pytest.raises(DeploymentContractError):
        transition_enable_journal(journal, "committed")


def test_write_cutover_state_is_owner_only_and_atomic(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    path = state_directory / "initial.json"

    payload_sha = write_cutover_state(path, _receipt())

    assert len(payload_sha) == 64
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(state_directory.glob("*.tmp"))


def test_initial_receipt_retry_converges_and_foreign_evidence_fails(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    path = state_directory / "initial.json"
    receipt = _receipt()

    first_sha = commit_initial_cutover_receipt(path, receipt)
    assert commit_initial_cutover_receipt(path, receipt) == first_sha
    foreign = build_initial_cutover_receipt(
        cutover_id=_CUTOVER_ID,
        expected_restore_epoch=4,
        reason="production initial cutover",
        evidence=_evidence(),
        result=_result(),
    )
    with pytest.raises(DeploymentContractError, match="foreign evidence"):
        commit_initial_cutover_receipt(path, foreign)


def test_initial_receipt_rejects_symlink_state(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    target = state_directory / "target"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    path = state_directory / "initial.json"
    path.symlink_to(target)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        commit_initial_cutover_receipt(path, _receipt())


@pytest.mark.parametrize("fail", [False, True])
def test_recovery_secret_file_is_owner_only_and_removed_on_all_paths(
    tmp_path: Path,
    fail: bool,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    observed: list[Path] = []

    def runner(path: Path) -> InitialCutoverResult:
        observed.append(path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text(encoding="utf-8") == "r" * 32 + "\n"
        if fail:
            raise RuntimeError("runner failed")
        return _result()

    if fail:
        with pytest.raises(RuntimeError, match="runner failed"):
            with_recovery_secret_file(state_directory, "r" * 32, runner)
    else:
        assert with_recovery_secret_file(state_directory, "r" * 32, runner) == _result()
    assert observed and not observed[0].exists()
    assert not list(state_directory.glob("*.secret"))
