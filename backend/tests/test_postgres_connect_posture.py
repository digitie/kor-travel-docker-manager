"""PUBLIC CONNECT 자세 검사 — 판정은 순수 함수라 형상으로 직접 태운다.

SQL이 실제로 무엇을 고르는지(특히 `datacl`이 NULL인 기본 ACL DB를 잡는지)는 격리 실행 테스트
`test_dagster_shared_storage_integration.py`가 진짜 PostgreSQL 위에서 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from kor_travel_docker_manager.services import deployment_readiness
from kor_travel_docker_manager.services import postgres_connect_posture as posture
from kor_travel_docker_manager.services.postgres_connect_posture import (
    PUBLIC_CONNECT_SQL,
    ConnectProbe,
    decide,
)


def _probe(**overrides: Any) -> ConnectProbe:
    base: dict[str, Any] = {
        "container_id": "kor-travel-test-postgresql",
        "container_name": "kor-travel-test-postgres",
        "external_project": None,
        "declared_postgres": True,
        "live_postgres": True,
    }
    return ConnectProbe(**{**base, **overrides})


def test_no_daemon_is_unknown_not_ok() -> None:
    assert decide(None).state == "unknown"


def test_public_connect_is_a_warning_that_names_the_databases() -> None:
    verdict = decide([_probe(public_connect_databases=("pinvi", "legacy"))])

    assert verdict.state == "warn"
    assert "kor-travel-test-postgresql: pinvi, legacy" in verdict.detail
    assert verdict.evidence["public_connect"] == {
        "kor-travel-test-postgresql": ["pinvi", "legacy"]
    }


def test_a_clean_instance_is_ok() -> None:
    verdict = decide([_probe()])

    assert verdict.state == "ok"
    assert verdict.evidence["checked"] == ["kor-travel-test-postgresql"]


def test_an_unobserved_instance_is_unknown_even_when_another_is_exposed() -> None:
    verdict = decide(
        [
            _probe(public_connect_databases=("pinvi",)),
            _probe(
                container_id="other-postgresql",
                live_postgres=None,
                unknown_reason="컨테이너를 조회할 수 없다",
            ),
        ]
    )

    assert verdict.state == "unknown"
    assert "other-postgresql" in verdict.detail
    assert "pinvi" in verdict.detail


def test_non_postgres_containers_are_out_of_scope_and_zero_scope_is_unknown() -> None:
    verdict = decide(
        [_probe(declared_postgres=False, live_postgres=False, public_connect_databases=("x",))]
    )

    assert verdict.state == "unknown"
    assert verdict.evidence["checked"] == []


def test_the_query_counts_default_acls_and_skips_templates() -> None:
    """`datacl` NULL(기본 ACL = PUBLIC CONNECT)을 펼치지 않으면 가장 흔한 노출이 사라진다."""

    assert "coalesce(d.datacl, acldefault('d', d.datdba))" in PUBLIC_CONNECT_SQL
    assert "NOT d.datistemplate" in PUBLIC_CONNECT_SQL
    assert "a.grantee = 0" in PUBLIC_CONNECT_SQL


def test_the_probe_runs_the_query_inside_the_live_server(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        posture,
        "_inspect",
        lambda name: {
            "Cmd": ["postgres", "-p", "11000"],
            "Entrypoint": ["docker-entrypoint.sh"],
            "Env": ["POSTGRES_USER=shared_admin"],
        },
    )

    def fake_psql(name: str, role: str, port: str, sql: str) -> list[list[str]]:
        seen.append((name, role, port, sql))
        return [["pinvi"]]

    monkeypatch.setattr(posture, "_psql", fake_psql)

    probe = posture._probe_one(
        "kor-travel-shared-postgresql",
        {"name": "kor-travel-shared-postgres", "role": "shared-postgresql"},
    )

    assert probe.public_connect_databases == ("pinvi",)
    assert seen == [("kor-travel-shared-postgres", "shared_admin", "11000", PUBLIC_CONNECT_SQL)]


def test_the_readiness_row_warns_and_never_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """발견은 `warn`이다 — `missing`은 재구축 blocker가 되므로 남의 DB로 재구축을 막는다."""

    monkeypatch.setattr(
        posture, "probe_instances", lambda: (_probe(public_connect_databases=("pinvi",)),)
    )

    check = deployment_readiness._check_postgres_public_connect()

    assert check.state == "warn"
    assert check.id in deployment_readiness._CHECK_ORDER
    assert "pinvi" in check.detail
