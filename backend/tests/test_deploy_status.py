from __future__ import annotations

import json
import stat
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.deploy_status import (
    DeployedDatabase,
    DeployRestart,
    DeployStatus,
    begin_deploy,
    commit_deploy,
    deploy_status_path,
    read_deploy_status,
    write_deploy_status,
)

_RUN_ID = str(uuid.UUID(int=1))
_DATABASES = {
    "map_application": DeployedDatabase("kor_travel_map", 16401, "7300000000000000001"),
    "map_dagster": DeployedDatabase("kor_travel_map_dagster", 16402, "7300000000000000001"),
    "pinvi": DeployedDatabase("pinvi", 20001, "7300000000000000002"),
}
_IMAGES = {
    "kor-travel-map-api": "sha256:" + "1" * 64,
    "kor-travel-map-dagster-code-server": "sha256:" + "2" * 64,
}
_HEADS = {"map_application": "400", "map_dagster": "7e2f3204cf8e", "pinvi": "20260917_0102"}


def _begin(previous: DeployStatus | None = None, **overrides: object) -> DeployStatus:
    arguments: dict[str, object] = {
        "run_id": _RUN_ID,
        "started_at": "2026-09-26T00:00:00+00:00",
        "manager_revision": "a" * 40,
        "map_revision": "b" * 40,
        "pinvi_revision": "c" * 40,
        "pinset_sha256": "d" * 64,
    }
    arguments.update(overrides)
    return begin_deploy(previous, **arguments)  # type: ignore[arg-type]


def _committed() -> DeployStatus:
    return commit_deploy(
        _begin(),
        committed_at="2026-09-26T01:00:00+00:00",
        images=_IMAGES,
        schema_heads=_HEADS,
        databases=_DATABASES,
    )


def test_missing_status_file_reads_as_none(tmp_path: Path) -> None:
    assert read_deploy_status(deploy_status_path(tmp_path)) is None


def test_committed_status_round_trips_through_a_private_file(tmp_path: Path) -> None:
    path = deploy_status_path(tmp_path)
    status = _committed()

    write_deploy_status(path, status)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_deploy_status(path) == status
    assert not list(tmp_path.glob(".deploy-status.json.*"))


def test_begin_inherits_the_committed_identity_baseline() -> None:
    assert _begin(_committed()).databases == _DATABASES


def test_begin_after_an_interrupted_run_keeps_its_baseline() -> None:
    interrupted = _begin(_committed())

    assert _begin(interrupted).databases == _DATABASES


def test_restart_keeps_the_baseline_until_the_reset_actually_happens() -> None:
    """리셋 전에 죽으면 DB는 그대로다 — 다음 일반 실행이 여전히 옛 기준으로 확인해야 한다."""

    restart = DeployRestart(reason="rebuild from empty DBs", at="2026-09-26T02:00:00+00:00")

    status = _begin(_committed(), restart=restart)

    assert status.databases == _DATABASES
    assert status.restart == restart


_RESTORED = {
    **_DATABASES,
    "pinvi": DeployedDatabase("pinvi", 29999, "7300000000000000002"),
}


def test_adopting_takes_the_live_databases_as_the_baseline() -> None:
    """채택이 중간에 죽어도 다음 일반 실행이 **받아들인 그 DB**로 확인하도록 기준선을 남긴다."""

    adopted = DeployRestart(reason="restored from backup", at="2026-09-26T02:00:00+00:00")

    status = _begin(_committed(), adopted=adopted, adopted_databases=_RESTORED)

    assert status.databases == _RESTORED
    assert status.adopted == adopted


def test_a_plain_rerun_of_an_interrupted_adoption_keeps_its_record_and_baseline() -> None:
    adopted = DeployRestart(reason="restored from backup", at="2026-09-26T02:00:00+00:00")
    interrupted = _begin(_committed(), adopted=adopted, adopted_databases=_RESTORED)

    rerun = _begin(interrupted)

    assert rerun.adopted == adopted
    assert rerun.databases == _RESTORED


def test_a_plain_rerun_keeps_the_restart_record_only_after_the_reset_happened() -> None:
    restart = DeployRestart(reason="rebuild from empty DBs", at="2026-09-26T02:00:00+00:00")
    before_reset = _begin(_committed(), restart=restart)
    after_reset = replace(before_reset, databases=None)  # 호출자가 리셋 뒤 비운 상태

    assert _begin(before_reset).restart is None
    assert _begin(after_reset).restart == restart


def test_a_new_deploy_after_a_commit_carries_no_explicit_record() -> None:
    adopted = DeployRestart(reason="restored from backup", at="2026-09-26T02:00:00+00:00")
    committed = commit_deploy(
        _begin(_committed(), adopted=adopted, adopted_databases=_RESTORED),
        committed_at="2026-09-26T03:00:00+00:00",
        images=_IMAGES,
        schema_heads=_HEADS,
        databases=_RESTORED,
    )

    assert _begin(committed).adopted is None


def test_a_deploy_cannot_both_restart_and_adopt() -> None:
    record = DeployRestart(reason="x", at="2026-09-26T02:00:00+00:00")

    with pytest.raises(DeploymentContractError, match="either restarts or adopts"):
        _begin(_committed(), restart=record, adopted=record)


def test_an_adoption_record_round_trips(tmp_path: Path) -> None:
    adopted = DeployRestart(reason="restored from backup", at="2026-09-26T02:00:00+00:00")
    status = commit_deploy(
        _begin(_committed(), adopted=adopted),
        committed_at="2026-09-26T03:00:00+00:00",
        images=_IMAGES,
        schema_heads=_HEADS,
        databases=_DATABASES,
    )
    path = deploy_status_path(tmp_path)

    write_deploy_status(path, status)

    assert read_deploy_status(path) == status


def test_only_an_in_progress_deploy_can_be_committed() -> None:
    with pytest.raises(DeploymentContractError, match="only an in-progress"):
        commit_deploy(
            _committed(),
            committed_at="2026-09-26T03:00:00+00:00",
            images=_IMAGES,
            schema_heads=_HEADS,
            databases=_DATABASES,
        )


@pytest.mark.parametrize(
    "missing",
    ("images", "schema_heads", "databases"),
)
def test_committed_status_must_carry_what_it_observed(missing: str) -> None:
    arguments: dict[str, object] = {
        "committed_at": "2026-09-26T01:00:00+00:00",
        "images": _IMAGES,
        "schema_heads": _HEADS,
        "databases": _DATABASES,
    }
    arguments[missing] = {} if missing != "databases" else None

    with pytest.raises(DeploymentContractError):
        DeployStatus(
            state="committed",
            run_id=_RUN_ID,
            started_at="2026-09-26T00:00:00+00:00",
            manager_revision="a" * 40,
            map_revision="b" * 40,
            pinvi_revision="c" * 40,
            pinset_sha256="d" * 64,
            **arguments,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "reason",
    ("", "   ", "two\nlines", "x" * 201),
)
def test_restart_reason_is_one_short_line(reason: str) -> None:
    with pytest.raises(DeploymentContractError, match="restart record"):
        DeployRestart(reason=reason, at="2026-09-26T02:00:00+00:00")


@pytest.mark.parametrize(
    "payload",
    (
        "not json",
        json.dumps([]),
        json.dumps({"version": 1}),
    ),
)
def test_malformed_status_is_refused_not_guessed(tmp_path: Path, payload: str) -> None:
    path = deploy_status_path(tmp_path)
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(DeploymentContractError):
        read_deploy_status(path)


def test_unknown_version_is_refused(tmp_path: Path) -> None:
    path = deploy_status_path(tmp_path)
    payload = _committed().to_payload()
    payload["version"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DeploymentContractError, match="version"):
        read_deploy_status(path)


def test_status_mappings_are_detached_from_the_caller() -> None:
    images = dict(_IMAGES)
    status = commit_deploy(
        _begin(),
        committed_at="2026-09-26T01:00:00+00:00",
        images=images,
        schema_heads=_HEADS,
        databases=_DATABASES,
    )

    images["kor-travel-map-ui"] = "sha256:" + "3" * 64

    assert "kor-travel-map-ui" not in status.images


@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: payload.__setitem__("images", "ab"),
        lambda payload: payload.__setitem__("schema_heads", "abc"),
        lambda payload: payload.__setitem__("images", [["kor-travel-map-api", "x"]]),
        lambda payload: payload.__setitem__("version", True),
        lambda payload: payload.__setitem__("version", 1.0),
        lambda payload: payload["databases"]["pinvi"].__setitem__("extra", 1),
        lambda payload: payload.__setitem__("restart", {"reason": "r", "at": "t", "x": 1}),
        lambda payload: payload.__setitem__("started_at", 5),
        lambda payload: payload.__setitem__("step", ["x"]),
        lambda payload: payload.__setitem__("run_id", "ABCDEF00-0000-4000-8000-000000000001"),
        lambda payload: payload.__setitem__("run_id", "{" + _RUN_ID + "}"),
    ),
)
def test_every_malformed_field_is_a_contract_error_not_a_crash(tmp_path: Path, mutate) -> None:  # noqa: ANN001
    payload = json.loads(json.dumps(_committed().to_payload()))
    mutate(payload)
    path = deploy_status_path(tmp_path)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DeploymentContractError):
        read_deploy_status(path)


def test_a_status_the_reader_would_refuse_is_never_written(tmp_path: Path) -> None:
    images = {f"service-{index:04d}": "sha256:" + "1" * 64 for index in range(1000)}
    status = commit_deploy(
        _begin(),
        committed_at="2026-09-26T01:00:00+00:00",
        images=images,
        schema_heads=_HEADS,
        databases=_DATABASES,
    )

    with pytest.raises(DeploymentContractError, match="too large"):
        write_deploy_status(deploy_status_path(tmp_path), status)

    assert not deploy_status_path(tmp_path).exists()
