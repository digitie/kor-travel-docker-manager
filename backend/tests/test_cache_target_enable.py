from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_cutover import (
    CacheTargetFrozenEvidence,
    InitialCutoverReceipt,
    InitialCutoverResult,
    build_initial_cutover_receipt,
    prepare_enable_journal,
    render_cache_target_sync_env,
    transition_enable_journal,
    write_cutover_state,
)
from kor_travel_docker_manager.services.cache_target_enable import (
    CacheTargetEnableRolledBackError,
    execute_cache_target_enable,
    read_canonical_env_file,
    read_enable_cutover_journal,
    replace_canonical_env_file,
)

_CUTOVER_ID = "11111111-1111-4111-8111-111111111111"
_ENV_FALSE = b"PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED=false\n"
_ENV_TRUE = b"PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED=true\n"
_ENABLED_RESOLVED_COMPOSE_SHA256 = "a" * 64


def _receipt() -> InitialCutoverReceipt:
    evidence = CacheTargetFrozenEvidence(
        env_sha256=hashlib.sha256(_ENV_FALSE).hexdigest(),
        raw_compose_sha256="2" * 64,
        resolved_compose_sha256="3" * 64,
        active_pair_sha256="4" * 64,
        rollback_pair_sha256="5" * 64,
        role_binding_sha256="6" * 64,
        expected_openapi_sha256="7" * 64,
        expected_source_revision="8" * 40,
        expected_contract_generation="7",
    )
    return build_initial_cutover_receipt(
        cutover_id=_CUTOVER_ID,
        expected_restore_epoch=3,
        reason="production initial cutover",
        evidence=evidence,
        result=InitialCutoverResult(
            cutover_id=_CUTOVER_ID,
            request_id="22222222-2222-4222-8222-222222222222",
            count=12,
            merkle_root="9" * 64,
            published=12,
        ),
    )


class _Environment:
    def __init__(self, value: bytes = _ENV_FALSE) -> None:
        self.value = value
        self.replacements: list[tuple[str, bytes]] = []

    def read(self) -> bytes:
        return self.value

    def replace(self, expected_sha256: str, replacement: bytes) -> None:
        assert hashlib.sha256(self.value).hexdigest() == expected_sha256
        self.replacements.append((expected_sha256, replacement))
        self.value = replacement


def test_execute_enable_orders_env_recreate_attest_canary_and_commit(
    tmp_path: Path,
) -> None:
    environment = _Environment()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    calls: list[tuple[str, bool | None]] = []

    journal = execute_cache_target_enable(
        receipt=_receipt(),
        journal_path=state / "enable.json",
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
        read_env=environment.read,
        replace_env=environment.replace,
        attest=lambda enabled: calls.append(("attest", enabled)),
        recreate_pinvi_api=lambda enabled: calls.append(("recreate", enabled)),
        causal_canary=lambda run_id: {
            "run_id": run_id,
            "cutover_id": _CUTOVER_ID,
            "active_pair_sha256": "4" * 64,
            "contract_generation": "7",
            "local_count": 12,
            "remote_count": 12,
            "local_merkle_root": "9" * 64,
            "remote_merkle_root": "9" * 64,
            "command_id": "33333333-3333-4333-8333-333333333333",
            "acked": True,
            "lag": 0,
            "dlq": 0,
            "count": 12,
            "merkle_root": "9" * 64,
        },
    )

    assert journal.phase == "committed"
    assert environment.value == _ENV_TRUE
    assert calls == [
        ("attest", False),
        ("recreate", True),
        ("attest", True),
        ("attest", True),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("local_count", 11),
        ("remote_count", 11),
        ("local_merkle_root", "8" * 64),
        ("remote_merkle_root", "8" * 64),
    ],
)
def test_execute_enable_rejects_canary_snapshot_drift_from_initial(
    tmp_path: Path,
    field: str,
    value: int | str,
) -> None:
    environment = _Environment()
    evidence: dict[str, object] = {
        "cutover_id": _CUTOVER_ID,
        "active_pair_sha256": "4" * 64,
        "contract_generation": "7",
        "local_count": 12,
        "remote_count": 12,
        "local_merkle_root": "9" * 64,
        "remote_merkle_root": "9" * 64,
    }
    evidence[field] = value

    with pytest.raises(CacheTargetEnableRolledBackError):
        execute_cache_target_enable(
            receipt=_receipt(),
            journal_path=tmp_path / "enable.json",
            enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
            read_env=environment.read,
            replace_env=environment.replace,
            attest=lambda _enabled: None,
            recreate_pinvi_api=lambda _enabled: None,
            causal_canary=lambda run_id: {"run_id": run_id, **evidence},
        )

    assert environment.value == _ENV_FALSE


def test_execute_enable_canary_failure_rolls_back_false_runtime(
    tmp_path: Path,
) -> None:
    environment = _Environment()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    calls: list[tuple[str, bool]] = []

    with pytest.raises(CacheTargetEnableRolledBackError):
        execute_cache_target_enable(
            receipt=_receipt(),
            journal_path=state / "enable.json",
            enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
            read_env=environment.read,
            replace_env=environment.replace,
            attest=lambda enabled: calls.append(("attest", enabled)),
            recreate_pinvi_api=lambda enabled: calls.append(("recreate", enabled)),
            causal_canary=lambda _run_id: {},
        )

    assert environment.value == _ENV_FALSE
    assert read_enable_cutover_journal(state / "enable.json").phase == "rolled_back"
    assert calls[-2:] == [("recreate", False), ("attest", False)]


def test_execute_enable_supersedes_rolled_back_attempt_for_same_window(
    tmp_path: Path,
) -> None:
    environment = _Environment()
    journal_path = tmp_path / "enable.json"
    window_transaction_id = "33333333-3333-4333-8333-333333333333"

    with pytest.raises(CacheTargetEnableRolledBackError):
        execute_cache_target_enable(
            receipt=_receipt(),
            journal_path=journal_path,
            enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
            read_env=environment.read,
            replace_env=environment.replace,
            attest=lambda _enabled: None,
            recreate_pinvi_api=lambda _enabled: None,
            causal_canary=lambda _run_id: {},
            window_transaction_id=window_transaction_id,
        )
    rolled_back = read_enable_cutover_journal(journal_path)
    assert rolled_back.phase == "rolled_back"
    assert rolled_back.attempt == 1
    assert rolled_back.window_transaction_id == window_transaction_id

    committed = execute_cache_target_enable(
        receipt=_receipt(),
        journal_path=journal_path,
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
        read_env=environment.read,
        replace_env=environment.replace,
        attest=lambda _enabled: None,
        recreate_pinvi_api=lambda _enabled: None,
        causal_canary=lambda run_id: {
            "run_id": run_id,
            "cutover_id": _CUTOVER_ID,
            "active_pair_sha256": "4" * 64,
            "contract_generation": "7",
            "local_count": 12,
            "remote_count": 12,
            "local_merkle_root": "9" * 64,
            "remote_merkle_root": "9" * 64,
        },
        window_transaction_id=window_transaction_id,
    )

    assert committed.phase == "committed"
    assert committed.attempt == 2
    assert committed.supersedes_transaction_id == rolled_back.transaction_id
    assert committed.window_transaction_id == window_transaction_id
    history = (
        tmp_path
        / "cache-target-enable-history-v2"
        / (
            f"{window_transaction_id}-attempt-1-"
            f"{rolled_back.transaction_id}.json"
        )
    )
    assert read_enable_cutover_journal(history) == rolled_back


def test_execute_enable_rejects_foreign_window_for_rolled_back_attempt(
    tmp_path: Path,
) -> None:
    environment = _Environment()
    journal_path = tmp_path / "enable.json"
    window_transaction_id = "33333333-3333-4333-8333-333333333333"
    with pytest.raises(CacheTargetEnableRolledBackError):
        execute_cache_target_enable(
            receipt=_receipt(),
            journal_path=journal_path,
            enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
            read_env=environment.read,
            replace_env=environment.replace,
            attest=lambda _enabled: None,
            recreate_pinvi_api=lambda _enabled: None,
            causal_canary=lambda _run_id: {},
            window_transaction_id=window_transaction_id,
        )

    with pytest.raises(DeploymentContractError, match="window transaction"):
        execute_cache_target_enable(
            receipt=_receipt(),
            journal_path=journal_path,
            enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
            read_env=environment.read,
            replace_env=environment.replace,
            attest=lambda _enabled: None,
            recreate_pinvi_api=lambda _enabled: None,
            causal_canary=lambda _run_id: {},
            window_transaction_id=(
                "44444444-4444-4444-8444-444444444444"
            ),
        )


def test_execute_enable_resumes_crash_after_env_commit(
    tmp_path: Path,
) -> None:
    receipt = _receipt()
    environment = _Environment(_ENV_TRUE)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = prepare_enable_journal(
        receipt=receipt,
        old_env_sha256=hashlib.sha256(_ENV_FALSE).hexdigest(),
        new_env_sha256=hashlib.sha256(_ENV_TRUE).hexdigest(),
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
    )
    journal = transition_enable_journal(journal, "env_committed")
    write_cutover_state(state / "enable.json", journal)
    recreates: list[bool] = []

    result = execute_cache_target_enable(
        receipt=receipt,
        journal_path=state / "enable.json",
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
        read_env=environment.read,
        replace_env=environment.replace,
        attest=lambda _enabled: None,
        recreate_pinvi_api=recreates.append,
        causal_canary=lambda run_id: {
            "run_id": run_id,
            "cutover_id": _CUTOVER_ID,
            "active_pair_sha256": "4" * 64,
            "contract_generation": "7",
            "local_count": 12,
            "remote_count": 12,
            "local_merkle_root": "9" * 64,
            "remote_merkle_root": "9" * 64,
            "acked": True,
            "lag": 0,
            "dlq": 0,
        },
    )

    assert result.phase == "committed"
    assert recreates == [True]
    assert environment.replacements == []


def test_execute_enable_resumes_rollback_after_env_restored(
    tmp_path: Path,
) -> None:
    receipt = _receipt()
    environment = _Environment(_ENV_FALSE)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = prepare_enable_journal(
        receipt=receipt,
        old_env_sha256=hashlib.sha256(_ENV_FALSE).hexdigest(),
        new_env_sha256=hashlib.sha256(_ENV_TRUE).hexdigest(),
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
    )
    journal = transition_enable_journal(journal, "env_committed")
    journal = transition_enable_journal(journal, "rollback_preparing")
    journal = transition_enable_journal(journal, "rollback_env_restored")
    write_cutover_state(state / "enable.json", journal)
    recreates: list[bool] = []

    result = execute_cache_target_enable(
        receipt=receipt,
        journal_path=state / "enable.json",
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
        read_env=environment.read,
        replace_env=environment.replace,
        attest=lambda _enabled: None,
        recreate_pinvi_api=recreates.append,
        causal_canary=lambda _run_id: pytest.fail(
            "canary must not run during rollback"
        ),
    )

    assert result.phase == "rolled_back"
    assert recreates == [False]


def test_execute_enable_rejects_foreign_journal_binding(tmp_path: Path) -> None:
    receipt = _receipt()
    environment = _Environment()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = prepare_enable_journal(
        receipt=receipt,
        old_env_sha256=hashlib.sha256(_ENV_FALSE).hexdigest(),
        new_env_sha256=hashlib.sha256(_ENV_TRUE).hexdigest(),
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
    )
    foreign = type(journal)(
        **{**asdict(journal), "active_pair_sha256": "f" * 64}
    )
    write_cutover_state(state / "enable.json", foreign)

    with pytest.raises(DeploymentContractError, match="foreign"):
        execute_cache_target_enable(
            receipt=receipt,
            journal_path=state / "enable.json",
            enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
            read_env=environment.read,
            replace_env=environment.replace,
            attest=lambda _enabled: None,
            recreate_pinvi_api=lambda _enabled: None,
            causal_canary=lambda _run_id: {},
        )


def test_env_renderer_round_trip_matches_enable_journal_hashes() -> None:
    enabled = render_cache_target_sync_env(
        _ENV_FALSE, expected="false", replacement="true"
    )
    assert enabled == _ENV_TRUE
    assert (
        render_cache_target_sync_env(
            enabled, expected="true", replacement="false"
        )
        == _ENV_FALSE
    )


def test_execute_enable_reuses_transaction_id_for_causal_canary_retry(
    tmp_path: Path,
) -> None:
    receipt = _receipt()
    environment = _Environment(_ENV_TRUE)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = prepare_enable_journal(
        receipt=receipt,
        old_env_sha256=hashlib.sha256(_ENV_FALSE).hexdigest(),
        new_env_sha256=hashlib.sha256(_ENV_TRUE).hexdigest(),
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
    )
    journal = transition_enable_journal(journal, "env_committed")
    journal = transition_enable_journal(journal, "recreate_started")
    write_cutover_state(state / "enable.json", journal)
    run_ids: list[str] = []

    result = execute_cache_target_enable(
        receipt=receipt,
        journal_path=state / "enable.json",
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
        read_env=environment.read,
        replace_env=environment.replace,
        attest=lambda _enabled: None,
        recreate_pinvi_api=lambda _enabled: None,
        causal_canary=lambda run_id: run_ids.append(run_id)
        or {
            "run_id": run_id,
            "cutover_id": _CUTOVER_ID,
            "active_pair_sha256": "4" * 64,
            "contract_generation": "7",
            "local_count": 12,
            "remote_count": 12,
            "local_merkle_root": "9" * 64,
            "remote_merkle_root": "9" * 64,
        },
    )

    assert result.phase == "committed"
    assert run_ids == [journal.transaction_id]


def test_execute_enable_replace_failure_rolls_back_from_preparing(
    tmp_path: Path,
) -> None:
    environment = _Environment()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    recreates: list[bool] = []

    def fail_replace(_expected_sha256: str, _replacement: bytes) -> None:
        raise RuntimeError("atomic replace failed")

    with pytest.raises(CacheTargetEnableRolledBackError) as raised:
        execute_cache_target_enable(
            receipt=_receipt(),
            journal_path=state / "enable.json",
            enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
            read_env=environment.read,
            replace_env=fail_replace,
            attest=lambda _enabled: None,
            recreate_pinvi_api=recreates.append,
            causal_canary=lambda _run_id: pytest.fail("canary must not run"),
        )

    assert isinstance(raised.value.cause, RuntimeError)
    assert read_enable_cutover_journal(state / "enable.json").phase == "rolled_back"
    assert recreates == [False]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("transaction_id", "not-a-uuid", "UUID"),
        ("active_pair_sha256", "A" * 64, "digest"),
        ("phase", "unknown", "contract"),
        ("version", True, "invalid"),
        ("new_env_sha256", hashlib.sha256(_ENV_FALSE).hexdigest(), "transition"),
        ("verified_evidence_sha256", None, "evidence"),
        ("verified_evidence_sha256", "a" * 64, "unverified"),
    ],
)
def test_read_enable_journal_rejects_invalid_contract(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = prepare_enable_journal(
        receipt=_receipt(),
        old_env_sha256=hashlib.sha256(_ENV_FALSE).hexdigest(),
        new_env_sha256=hashlib.sha256(_ENV_TRUE).hexdigest(),
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
    )
    document = {**asdict(journal), field: value}
    if field == "verified_evidence_sha256" and value is None:
        document["phase"] = "verified"
    path = state / "enable.json"
    path.write_text(json.dumps(document))
    path.chmod(0o600)

    with pytest.raises(DeploymentContractError, match=message):
        read_enable_cutover_journal(path)


def test_read_enable_journal_rejects_extra_field(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = prepare_enable_journal(
        receipt=_receipt(),
        old_env_sha256=hashlib.sha256(_ENV_FALSE).hexdigest(),
        new_env_sha256=hashlib.sha256(_ENV_TRUE).hexdigest(),
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
    )
    path = state / "enable.json"
    path.write_text(json.dumps({**asdict(journal), "unexpected": True}))
    path.chmod(0o600)

    with pytest.raises(DeploymentContractError, match="invalid"):
        read_enable_cutover_journal(path)


def test_read_enable_journal_rejects_symlink_and_insecure_mode(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = prepare_enable_journal(
        receipt=_receipt(),
        old_env_sha256=hashlib.sha256(_ENV_FALSE).hexdigest(),
        new_env_sha256=hashlib.sha256(_ENV_TRUE).hexdigest(),
        enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
    )
    target = state / "target.json"
    write_cutover_state(target, journal)
    hardlink = state / "hardlink.json"
    os.link(target, hardlink)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_enable_cutover_journal(target)

    hardlink.unlink()
    link = state / "link.json"
    link.symlink_to(target)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_enable_cutover_journal(link)

    target.chmod(0o644)
    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_enable_cutover_journal(target)


def test_execute_enable_rejects_dangling_journal_symlink_without_attestation(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal_path = state / "enable.json"
    journal_path.symlink_to(state / "missing.json")
    attestations: list[bool] = []

    with pytest.raises(DeploymentContractError, match="unsafe"):
        execute_cache_target_enable(
            receipt=_receipt(),
            journal_path=journal_path,
            enabled_resolved_compose_sha256=_ENABLED_RESOLVED_COMPOSE_SHA256,
            read_env=_Environment().read,
            replace_env=lambda _expected, _replacement: None,
            attest=attestations.append,
            recreate_pinvi_api=lambda _enabled: None,
            causal_canary=lambda _run_id: {},
        )

    assert attestations == []


def test_canonical_env_atomic_replace_preserves_owner_only_mode(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_bytes(_ENV_FALSE)
    env_path.chmod(0o600)

    replace_canonical_env_file(
        env_path,
        expected_sha256=hashlib.sha256(_ENV_FALSE).hexdigest(),
        replacement=_ENV_TRUE,
    )

    assert read_canonical_env_file(env_path) == _ENV_TRUE
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_canonical_env_atomic_replace_rejects_drift_and_unsafe_links(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_bytes(_ENV_FALSE)
    env_path.chmod(0o600)

    with pytest.raises(DeploymentContractError, match="changed"):
        replace_canonical_env_file(
            env_path,
            expected_sha256="f" * 64,
            replacement=_ENV_TRUE,
        )

    hardlink = tmp_path / "hardlink"
    os.link(env_path, hardlink)
    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_canonical_env_file(env_path)
    hardlink.unlink()

    link = tmp_path / "link"
    link.symlink_to(env_path)
    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_canonical_env_file(link)
