from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import asdict
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import cache_target_cutover
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_cutover import (
    CacheTargetFrozenEvidence,
    InitialCutoverReceipt,
    InitialCutoverResult,
    build_initial_cutover_receipt,
    commit_initial_cutover_receipt,
    initial_receipt_logical_sha256,
    initial_runner_compose_arguments,
    initial_runner_secret_path,
    parse_initial_cutover_output,
    prepare_enable_journal,
    render_cache_target_sync_env,
    scavenge_initial_runner_secret_bundle,
    transition_enable_journal,
    with_initial_runner_secret_bundle,
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
        receipt=_receipt(),
        old_env_sha256="a" * 64,
        new_env_sha256="b" * 64,
        enabled_resolved_compose_sha256="c" * 64,
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


def test_render_cache_target_sync_env_changes_only_one_exact_literal() -> None:
    raw = (
        b"PINVI_ENVIRONMENT=production\n"
        b"PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED=false\n"
        b"OTHER=value\n"
    )

    enabled = render_cache_target_sync_env(
        raw, expected="false", replacement="true"
    )
    assert enabled == raw.replace(b"SYNC_ENABLED=false", b"SYNC_ENABLED=true")
    assert (
        render_cache_target_sync_env(
            enabled, expected="true", replacement="false"
        )
        == raw
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED=False\n",
        b"PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED='false'\n",
        (
            b"PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED=false\n"
            b"PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED=false\n"
        ),
    ],
)
def test_render_cache_target_sync_env_rejects_ambiguous_or_inexact_line(
    raw: bytes,
) -> None:
    with pytest.raises(DeploymentContractError):
        render_cache_target_sync_env(raw, expected="false", replacement="true")


def test_enable_journal_rollback_is_ordered_and_terminal() -> None:
    journal = prepare_enable_journal(
        receipt=_receipt(),
        old_env_sha256="a" * 64,
        new_env_sha256="b" * 64,
        enabled_resolved_compose_sha256="c" * 64,
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
def test_initial_runner_secret_bundle_is_owner_only_and_removed_on_all_paths(
    tmp_path: Path,
    fail: bool,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    observed: list[Path] = []
    command_token = "c" * 32
    consumer_token = "u" * 32
    recovery_token = "r" * 32

    def runner(path: Path) -> InitialCutoverResult:
        observed.append(path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text(encoding="utf-8") == (
            f"{command_token}\n{consumer_token}\n{recovery_token}\n"
        )
        if fail:
            raise RuntimeError("runner failed")
        return _result()

    if fail:
        with pytest.raises(RuntimeError, match="runner failed"):
            with_initial_runner_secret_bundle(
                state_directory,
                _CUTOVER_ID,
                command_token,
                consumer_token,
                recovery_token,
                runner,
            )
    else:
        assert (
            with_initial_runner_secret_bundle(
                state_directory,
                _CUTOVER_ID,
                command_token,
                consumer_token,
                recovery_token,
                runner,
            )
            == _result()
        )
    assert observed and not observed[0].exists()
    assert not list(state_directory.glob("*.secret"))


def test_initial_runner_compose_arguments_never_contain_role_values_or_digests(
    tmp_path: Path,
) -> None:
    secret_path = (tmp_path / "initial.secret").resolve()
    tokens = ("c" * 32, "u" * 32, "r" * 32)

    arguments = initial_runner_compose_arguments(
        secret_path=secret_path,
        cutover_id=_CUTOVER_ID,
        expected_restore_epoch=3,
        reason="production initial cutover",
    )
    docker_create_config = {
        "Env": [argument.removeprefix("--env=") for argument in arguments if "TOKEN=" in argument],
        "Cmd": list(arguments),
    }
    serialized = json.dumps(docker_create_config)

    assert all(token not in serialized for token in tokens)
    assert all(
        hashlib.sha256(token.encode()).hexdigest() not in serialized
        for token in tokens
    )
    assert "RESTORE_FENCE" not in serialized
    assert str(secret_path) in serialized
    assert "production initial cutover" in arguments
    wrapper = arguments[arguments.index("-ec") + 1]
    for variable in (
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_COMMAND_TOKEN",
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_TOKEN",
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RECOVERY_TOKEN",
    ):
        assert f'export {variable}="$' in wrapper


def test_initial_runner_secret_bundle_preserves_shell_metacharacters(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    tokens = (
        "command-$*?[];&-token-000000000001",
        "consumer-$(x)`y`-token-000000000002",
        "recovery-${x}!#-token-0000000000003",
    )

    def runner(path: Path) -> InitialCutoverResult:
        assert path.read_text(encoding="utf-8").splitlines() == list(tokens)
        return _result()

    assert (
        with_initial_runner_secret_bundle(
            state_directory,
            _CUTOVER_ID,
            tokens[0],
            tokens[1],
            tokens[2],
            runner,
        )
        == _result()
    )


def test_initial_runner_secret_scavenger_zeroizes_crash_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    secret_path = initial_runner_secret_path(state_directory, _CUTOVER_ID)
    secret_path.write_bytes(b"sensitive-token-material\n")
    secret_path.chmod(0o600)
    writes: list[bytes] = []
    original_write = os.write

    def capture_write(descriptor: int, payload: bytes) -> int:
        writes.append(payload)
        return original_write(descriptor, payload)

    monkeypatch.setattr(cache_target_cutover.os, "write", capture_write)

    scavenge_initial_runner_secret_bundle(state_directory, _CUTOVER_ID)

    assert not secret_path.exists()
    assert writes and set(b"".join(writes)) == {0}


@pytest.mark.parametrize("artifact_kind", ["symlink", "hardlink"])
def test_initial_runner_secret_scavenger_rejects_link_artifacts(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    secret_path = initial_runner_secret_path(state_directory, _CUTOVER_ID)
    target = state_directory / "foreign"
    target.write_bytes(b"must-not-change")
    target.chmod(0o600)
    if artifact_kind == "symlink":
        secret_path.symlink_to(target)
    else:
        os.link(target, secret_path)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        scavenge_initial_runner_secret_bundle(state_directory, _CUTOVER_ID)

    assert target.read_bytes() == b"must-not-change"


def test_initial_runner_secret_scavenger_rejects_foreign_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    secret_path = initial_runner_secret_path(state_directory, _CUTOVER_ID)
    secret_path.write_bytes(b"must-not-change")
    secret_path.chmod(0o600)
    monkeypatch.setattr(cache_target_cutover.os, "geteuid", lambda: os.getuid() + 1)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        scavenge_initial_runner_secret_bundle(state_directory, _CUTOVER_ID)

    assert secret_path.read_bytes() == b"must-not-change"
