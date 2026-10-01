"""`scripts/dagster-shared-cutover.sh`의 파생(derive)과 소비자 대조를 체크아웃 모델 위에서 돌린다(ADR-54).

창 스크립트는 n150에서만 실행되지만, 무엇을 무엇과 비교하는지는 compose·targets에서 파생한다. 그 파생이 계약과 어긋나면
(리뷰 H1: `internal/graphql` 종류의 경로를 버려 Map의 내부 GraphQL URL을 항상 틀렸다고 판정) 전환이 펜스 뒤에 실패한다.
그래서 스크립트의 derive 프로그램을 그대로 꺼내 렌더된 모델(기본값으로 보간)에 돌리고, 스크립트가 할 비교 — 소비자 env의
값이 derive가 낸 기대값과 같은가 — 를 여기서 한다.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "dagster-shared-cutover.sh"
_COMPOSE = _ROOT / "docker-compose.yml"
_TARGETS = _ROOT / "config" / "docker-targets.yml"
_DEFAULT = re.compile(r"\$\{[A-Za-z0-9_]+:-([^${}]*)\}")
_REQUIRED = re.compile(r"\$\{[A-Za-z0-9_]+(?::?\?[^${}]*)?\}")


def _derive_program() -> str:
    text = _SCRIPT.read_text(encoding="utf-8")
    start = text.index("derive() {")
    body = text[start:]
    begin = body.index("python3 -I -c '") + len("python3 -I -c '")
    end = body.index("' \"${1:-$TARGET}\"")
    return body[begin:end]


def _resolve(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _resolve(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item) for item in value]
    if isinstance(value, str):
        previous = None
        while previous != value:
            previous = value
            value = _DEFAULT.sub(r"\1", value)
        return _REQUIRED.sub("x", value)
    return value


def _rendered() -> dict[str, Any]:
    document = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    document = {key: value for key, value in document.items() if not str(key).startswith("x-")}
    return _resolve(document)


def _facts(target: str, document: dict[str, Any]) -> dict[str, str]:
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _derive_program(), target, str(_TARGETS)],
        input=json.dumps(document),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)


@pytest.mark.parametrize("target", ["map", "pinvi", "geo", "weather"])
def test_every_internal_consumer_matches_what_the_script_expects(target: str) -> None:
    document = _rendered()
    facts = _facts(target, document)
    assert facts["CONTROL_PLANE"] == "shared"
    entries = [entry for entry in facts["INTERNAL_CONSUMERS"].split(";") if entry]
    assert entries, facts
    for entry in entries:
        service, variable, want = entry.split(" ")
        got = document["services"][service]["environment"][variable]
        assert got == want, (service, variable, got, want)


def test_the_map_internal_graphql_url_keeps_its_path() -> None:
    """리뷰 H1: `internal/graphql` 종류는 공용 webserver loopback 뒤에 `/graphql`을 붙인 값을 기대한다."""

    facts = _facts("map", _rendered())
    port = facts["PLANE_WEBSERVER_PORT"]
    entries = set(facts["INTERNAL_CONSUMERS"].split(";"))
    assert (
        f"kor-travel-map-api KOR_TRAVEL_MAP_API_DAGSTER_INTERNAL_GRAPHQL_URL http://127.0.0.1:{port}/graphql"
        in entries
    ), entries
    assert f"kor-travel-map-api KOR_TRAVEL_MAP_API_DAGSTER_URL http://127.0.0.1:{port}" in entries


def test_the_script_reads_the_role_connection_limit_from_the_db_init() -> None:
    """G3-a: 창 스크립트가 live role에 걸 연결 상한은 db-init one-shot의 문장에서 온다(한 값이어야 한다)."""

    facts = _facts("map", _rendered())
    assert facts["PLANE_DB_INIT"] == "kor-travel-shared-db-init-dagster"
    assert facts["PLANE_ROLE_CONNECTION_LIMIT"] == "45"
