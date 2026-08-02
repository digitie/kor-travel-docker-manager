from __future__ import annotations

import json
import subprocess
import traceback
from collections.abc import Mapping, Sequence
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_writer_fence import (
    attest_cache_target_global_writer_fence,
    cache_target_writer_environments_from_resolved_compose,
    postgres_target_sha256,
    postgres_target_sha256s_from_environment,
)

_RUNNING_ID = "a" * 64
_SECOND_RUNNING_ID = "b" * 64
_WRITER_NAMES = (
    "kor-travel-map-api-latest",
    "kor-travel-map-dagster-daemon-latest",
    "kor-travel-map-dagster-latest",
    "pinvi-api-latest",
    "pinvi-dagster-latest",
)


def test_postgres_target_hash_canonicalizes_aliases_driver_query_and_credentials() -> None:
    expected = postgres_target_sha256(
        "postgresql://map:secret@POSTGRES.:5432/kor_travel_map"
    )

    assert (
        postgres_target_sha256(
            "postgresql+asyncpg://other:other@postgres/kor_travel_map"
            "?search_path=feature&sslmode=require"
        )
        == expected
    )
    assert (
        postgres_target_sha256(
            "postgresql+psycopg://map:p%40ss@%70ostgres/%6bor_travel_map"
            "?options=-csearch_path%3Dfeature"
        )
        == expected
    )
    assert (
        postgres_target_sha256(
            "host=postgres port=5432 dbname=kor_travel_map user=admin password=changed"
        )
        == expected
    )


def test_split_pg_environment_matches_uri_target() -> None:
    split = postgres_target_sha256s_from_environment(
        {
            "PGHOST": "postgres",
            "PGPORT": "5432",
            "PGDATABASE": "kor_travel_map",
            "PGUSER": "map",
            "PGPASSWORD": "not-returned",
        }
    )
    assert split == frozenset(
        {postgres_target_sha256("postgresql://map:any@postgres/kor_travel_map")}
    )


def test_postgres_target_hash_canonicalizes_loopback_aliases() -> None:
    expected = postgres_target_sha256("postgresql://app:any@localhost/app")

    assert postgres_target_sha256("postgresql://app:any@127.0.0.1/app") == expected
    assert postgres_target_sha256("postgresql://app:any@[::1]/app") == expected


def test_resolved_compose_writer_services_resolve_exact_container_environments() -> None:
    expected = _expected_writers()
    resolved = {
        "services": {
            service_name: {
                "container_name": container_name,
                "environment": environment,
            }
            for service_name, (container_name, environment) in zip(
                ("map-api", "map-daemon", "map-web", "pin-api", "pin-dagster"),
                sorted(expected.items()),
                strict=True,
            )
        }
    }

    assert cache_target_writer_environments_from_resolved_compose(
        resolved,
        ("map-api", "map-daemon", "map-web", "pin-api", "pin-dagster"),
    ) == expected


def test_resolved_compose_writer_services_reject_duplicate_runtime_name() -> None:
    environment = {"DATABASE_URL": "postgresql://app:any@postgres/app"}
    resolved = {
        "services": {
            name: {"container_name": "same-runtime", "environment": environment}
            for name in ("one", "two", "three", "four", "five")
        }
    }

    with pytest.raises(DeploymentContractError, match="runtime identity"):
        cache_target_writer_environments_from_resolved_compose(
            resolved,
            ("one", "two", "three", "four", "five"),
        )


@pytest.mark.parametrize(
    "environment",
    [
        {"DATABASE_URL": "mysql://db/app"},
        {"DATABASE_URL": "postgresql://db"},
        {"DATABASE_URL": "postgresql://db/app?host=other"},
        {"PGHOST": "postgres", "PGDATABASE": "app"},
        {"DATABASE_URL": "postgresql+unknown://postgres/app"},
    ],
)
def test_malformed_or_ambiguous_postgres_environment_fails_closed(
    environment: Mapping[str, str],
) -> None:
    with pytest.raises(DeploymentContractError, match="PostgreSQL|split"):
        postgres_target_sha256s_from_environment(environment)


def test_non_postgres_uri_policy_ignores_explicit_other_protocol_key() -> None:
    assert (
        postgres_target_sha256s_from_environment(
            {"REDIS_URL": "redis://cache/0", "lowercase": "allowed"}
        )
        == frozenset()
    )


def test_non_postgres_uri_in_database_contract_slot_fails_closed() -> None:
    with pytest.raises(DeploymentContractError, match="PostgreSQL connection"):
        postgres_target_sha256s_from_environment(
            {"APPLICATION_DATABASE_URL": "mysql://db/app"}
        )


def test_global_fence_accepts_exact_stopped_writers_and_unrelated_running_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _expected_writers()
    stopped = _stopped_writer_payload(expected)
    unrelated = [
        _container(
            _RUNNING_ID,
            "unrelated-worker",
            running=True,
            environment=[
                "lowercase_key=is-valid",
                "DATABASE_URL=postgresql://other:secret@postgres/other_db",
            ],
        )
    ]
    runner = _install_inventory(
        monkeypatch,
        first_running=unrelated,
        first_stopped=stopped,
        second_running=unrelated,
        second_stopped=stopped,
    )

    evidence = attest_cache_target_global_writer_fence(
        expected_stopped_writers=expected,
        docker_bin="/usr/bin/docker",
        cwd="/srv/manager",
    )

    assert evidence.contract_version == "ktdm-cache-target-global-writer-fence/v1"
    assert evidence.protected_target_count == 3
    assert evidence.expected_stopped_writer_count == 5
    assert evidence.running_container_count == 1
    assert evidence.unrelated_running_container_count == 1
    assert len(evidence.inventory_sha256) == 64
    assert "secret" not in repr(evidence)
    assert runner.call_count == 6
    assert all("secret" not in repr(call.args) for call in runner.call_args_list)


def test_global_fence_rejects_foreign_running_container_with_alternate_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _expected_writers()
    stopped = _stopped_writer_payload(expected)
    foreign = [
        _container(
            _RUNNING_ID,
            "stale-oneoff",
            running=True,
            environment=[
                "DATABASE_URL=postgresql+asyncpg://foreign:different@postgres/kor_travel_map"
            ],
        )
    ]
    _install_inventory(
        monkeypatch,
        first_running=foreign,
        first_stopped=stopped,
        second_running=foreign,
        second_stopped=stopped,
    )

    with pytest.raises(DeploymentContractError, match="running foreign container") as exc:
        attest_cache_target_global_writer_fence(expected_stopped_writers=expected)

    assert "different" not in str(exc.value)
    assert "postgresql" not in str(exc.value)


def test_unrelated_stopped_container_is_not_a_writer() -> None:
    environment = {"DATABASE_URL": "postgresql://other:any@postgres/kor_travel_map"}

    assert postgres_target_sha256s_from_environment(environment)
    # 전역 fence는 `docker container ls`의 running 집합과 exact 5 stopped 이름만 inspect한다.
    # 따라서 이름이 다른 stopped container는 write-capable runtime으로 분류하지 않는다.


def test_global_fence_rejects_running_exact_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _expected_writers()
    stopped = _stopped_writer_payload(expected)
    stopped[0]["State"] = {"Running": True}
    _install_inventory(
        monkeypatch,
        first_running=[],
        first_stopped=stopped,
        second_running=[],
        second_stopped=stopped,
    )

    with pytest.raises(DeploymentContractError, match="state differs"):
        attest_cache_target_global_writer_fence(expected_stopped_writers=expected)


def test_global_fence_rejects_missing_exact_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _expected_writers()
    stopped = _stopped_writer_payload(expected)[:-1]
    _install_inventory(
        monkeypatch,
        first_running=[],
        first_stopped=stopped,
        second_running=[],
        second_stopped=stopped,
    )

    with pytest.raises(DeploymentContractError, match="identity drifted"):
        attest_cache_target_global_writer_fence(expected_stopped_writers=expected)


def test_global_fence_rejects_create_remove_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _expected_writers()
    stopped = _stopped_writer_payload(expected)
    first = [_container(_RUNNING_ID, "worker", running=True, environment=[])]
    second = [_container(_SECOND_RUNNING_ID, "worker-2", running=True, environment=[])]
    _install_inventory(
        monkeypatch,
        first_running=first,
        first_stopped=stopped,
        second_running=second,
        second_stopped=stopped,
    )

    with pytest.raises(DeploymentContractError, match="inventory changed"):
        attest_cache_target_global_writer_fence(expected_stopped_writers=expected)


def test_global_fence_rejects_container_rename_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _expected_writers()
    stopped = _stopped_writer_payload(expected)
    first = [_container(_RUNNING_ID, "worker", running=True, environment=[])]
    second = [_container(_RUNNING_ID, "renamed-worker", running=True, environment=[])]
    _install_inventory(
        monkeypatch,
        first_running=first,
        first_stopped=stopped,
        second_running=second,
        second_stopped=stopped,
    )

    with pytest.raises(DeploymentContractError, match="inventory changed"):
        attest_cache_target_global_writer_fence(expected_stopped_writers=expected)


def test_global_fence_rejects_duplicate_container_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _expected_writers()
    duplicate = _container(_RUNNING_ID, "worker", running=True, environment=[])
    runner = Mock(
        side_effect=[
            _completed(f"{_RUNNING_ID}\n"),
            _completed(json.dumps([duplicate, duplicate])),
        ]
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.cache_target_writer_fence.subprocess.run",
        runner,
    )

    with pytest.raises(DeploymentContractError, match="duplicate identities"):
        attest_cache_target_global_writer_fence(expected_stopped_writers=expected)


def test_global_fence_rejects_malformed_unknown_running_environment_without_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _expected_writers()
    malformed = [
        _container(
            _RUNNING_ID,
            "unknown-worker",
            running=True,
            environment=["DATABASE_URL=not-a-parseable-secret"],
        )
    ]
    runner = Mock(
        side_effect=[
            _completed(f"{_RUNNING_ID}\n"),
            _completed(json.dumps(malformed)),
        ]
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.cache_target_writer_fence.subprocess.run",
        runner,
    )

    with pytest.raises(DeploymentContractError) as exc:
        attest_cache_target_global_writer_fence(expected_stopped_writers=expected)

    assert "not-a-parseable-secret" not in str(exc.value)
    assert "not-a-parseable-secret" not in "".join(
        traceback.format_exception(exc.type, exc.value, exc.tb)
    )


def test_global_fence_rejects_duplicate_running_ids_before_inspect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(return_value=_completed(f"{_RUNNING_ID}\n{_RUNNING_ID}\n"))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.cache_target_writer_fence.subprocess.run",
        runner,
    )

    with pytest.raises(DeploymentContractError, match="invalid running container"):
        attest_cache_target_global_writer_fence(
            expected_stopped_writers=_expected_writers()
        )

    assert runner.call_count == 1


def test_global_fence_rejects_docker_failure_without_stderr_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Mock(return_value=_completed("", returncode=1, stderr="password=secret"))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.cache_target_writer_fence.subprocess.run",
        runner,
    )

    with pytest.raises(DeploymentContractError, match="inspection failed") as exc:
        attest_cache_target_global_writer_fence(
            expected_stopped_writers=_expected_writers()
        )

    assert "secret" not in str(exc.value)


def _expected_writers() -> dict[str, dict[str, str]]:
    return {
        "kor-travel-map-api-latest": {
            "KOR_TRAVEL_MAP_PG_DSN": (
                "postgresql+psycopg://map:map-secret@postgres/kor_travel_map"
            )
        },
        "kor-travel-map-dagster-daemon-latest": {
            "KOR_TRAVEL_MAP_DAGSTER_PG_URL": (
                "postgresql://dagster:dagster-secret@postgres/kor_travel_map_dagster"
            )
        },
        "kor-travel-map-dagster-latest": {
            "KOR_TRAVEL_MAP_DAGSTER_PG_URL": (
                "postgresql://dagster:dagster-secret@postgres/kor_travel_map_dagster"
            )
        },
        "pinvi-api-latest": {
            "PINVI_DATABASE_URL": "postgresql+asyncpg://pin:pin-secret@postgres/pinvi"
        },
        "pinvi-dagster-latest": {
            "PINVI_DATABASE_URL": "postgresql://pin:pin-secret@postgres/pinvi"
        },
    }


def _stopped_writer_payload(
    expected: Mapping[str, Mapping[str, str]],
) -> list[dict[str, object]]:
    return [
        _container(
            f"{index + 1:x}" * 64,
            name,
            running=False,
            environment=[f"{key}={value}" for key, value in environment.items()],
        )
        for index, (name, environment) in enumerate(sorted(expected.items()))
    ]


def _container(
    container_id: str,
    name: str,
    *,
    running: bool,
    environment: Sequence[str],
) -> dict[str, object]:
    return {
        "Id": container_id,
        "Name": f"/{name}",
        "Config": {"Env": list(environment)},
        "State": {"Running": running},
    }


def _install_inventory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    first_running: list[dict[str, object]],
    first_stopped: list[dict[str, object]],
    second_running: list[dict[str, object]],
    second_stopped: list[dict[str, object]],
) -> Mock:
    responses: list[subprocess.CompletedProcess[str]] = []
    for running, stopped in (
        (first_running, first_stopped),
        (second_running, second_stopped),
    ):
        responses.append(_completed("".join(f"{item['Id']}\n" for item in running)))
        if running:
            responses.append(_completed(json.dumps(running)))
        responses.append(_completed(json.dumps(stopped)))
    runner = Mock(side_effect=responses)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.cache_target_writer_fence.subprocess.run",
        runner,
    )
    return runner


def _completed(
    stdout: str, *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["docker"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
