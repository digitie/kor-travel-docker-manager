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
    begin = text.index("DERIVE_PY='") + len("DERIVE_PY='")
    return text[begin : text.index("\n'\n", begin)]


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


def _run_derive(
    target: str, document: dict[str, Any], target_document: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """스크립트의 derive 프로그램 — stdin은 Manager 렌더, 셋째 인자는 형제 프로젝트의 렌더(없으면 `-`)."""

    return subprocess.run(
        [sys.executable, "-I", "-c", _derive_program(), target, str(_TARGETS), str(target_document or "-")],
        input=json.dumps(document),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def _facts(target: str, document: dict[str, Any], target_document: Path | None = None) -> dict[str, str]:
    completed = _run_derive(target, document, target_document)
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


# ── 형제 프로젝트(transport): 그 프로젝트의 렌더에서 파생하고 targets의 선언과 대조한다 ──────────────


def _shared_code_servers() -> list[dict[str, Any]]:
    """Manager compose의 공용 plane code-server(`code-server start`) — 형제 프로젝트가 따를 모양의 정본."""

    return [
        service
        for service in _rendered()["services"].values()
        if " code-server start " in " " + " ".join(str(w) for w in service.get("command") or []) + " "
    ]


def _shared_probe() -> str:
    probes = {str(service["healthcheck"]["test"][4]) for service in _shared_code_servers()}
    assert len(probes) == 1, len(probes)
    return next(iter(probes))


def _shared_heartbeat() -> str:
    values = {str(service["environment"]["DAGSTER_GRPC_PROXY_HEARTBEAT_TTL_SECONDS"]) for service in _shared_code_servers()}
    assert len(values) == 1, values
    return next(iter(values))


def _sibling_render(**changes: Any) -> dict[str, Any]:
    """transport `docker-compose.shared.yml`의 공용 plane 모양(렌더된 JSON) — 그 저장소의 계약 테스트가 정본을 본다."""

    code_command = [
        "dagster", "code-server", "start", "-h", "127.0.0.1", "-p", "14005",
        "-m", "app.dagster.definitions", "--location-name", "kor-travel-transport",
        "--inject-env-vars-from-instance",
    ]
    services: dict[str, Any] = {
        "backend": {"image": "kor-travel-transport-backend:rel-x", "command": ["uvicorn", "app.main:app"]},
        "frontend": {"image": "kor-travel-transport-frontend", "command": ["next", "start"]},
        "migrate": {"image": "kor-travel-transport-backend:rel-x", "command": ["alembic", "upgrade", "head"]},
        "dagster-migrate": {
            "image": "kor-travel-transport-backend:rel-x",
            "profiles": ["legacy-dagster"],
            "command": ["dagster", "instance", "migrate"],
        },
        "dagster-code-server": {
            "image": "kor-travel-transport-backend:rel-x",
            "command": code_command,
            "depends_on": {"migrate": {"condition": "service_completed_successfully"}},
            "environment": {
                "KOR_TRAVEL_DAGSTER_SHARED_PG_URL": "postgresql+psycopg2://u:p@127.0.0.1:11000/dagster_shared",
                "DAGSTER_GRPC_PROXY_HEARTBEAT_TTL_SECONDS": _shared_heartbeat(),
            },
            "init": True,
            "healthcheck": {"test": ["CMD", "python", "-I", "-c", _shared_probe(), "14005"]},
        },
        "dagster-webserver": {
            "image": "kor-travel-transport-backend:rel-x",
            "profiles": ["legacy-dagster"],
            "command": ["dagster-webserver", "-h", "127.0.0.1", "-p", "14004", "-w", "/app/dagster_home/workspace.yaml"],
            "depends_on": {"dagster-code-server": {"condition": "service_healthy"}, "dagster-migrate": {}},
        },
        "dagster-daemon": {
            "image": "kor-travel-transport-backend:rel-x",
            "profiles": ["legacy-dagster"],
            "command": ["dagster-daemon", "run", "-w", "/app/dagster_home/workspace.yaml"],
            "depends_on": {"dagster-code-server": {"condition": "service_healthy"}},
        },
        "dagster-gateway": {
            "image": "kor-travel-transport-dagster-gateway",
            "profiles": ["legacy-dagster"],
            "depends_on": {"dagster-webserver": {"condition": "service_healthy"}},
        },
    }
    for path, value in changes.items():
        service, key = path.split(".", 1)
        services[service][key] = value
    return {"services": services}


def _write(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "sibling.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_sibling_projects_family_comes_from_its_own_render(tmp_path: Path) -> None:
    facts = _facts("transport", _rendered(), _write(tmp_path, _sibling_render()))
    assert facts["CODE"] == "dagster-code-server"
    assert facts["OLD_WEBSERVER"] == "dagster-webserver"
    assert facts["OLD_DAEMON"] == "dagster-daemon"
    assert facts["OLD_GATEWAYS"] == "dagster-gateway"
    assert facts["LOCATION"] == "kor-travel-transport"
    assert facts["OLD_WEBSERVER_PORT"] == "14004"
    assert facts["CODE_HAS_SHARED_URL"] == "yes"
    assert facts["CODE_IMAGE"] == "kor-travel-transport-backend:rel-x"
    assert facts["CONSUMERS"] == ""
    # 공용 plane은 Manager 렌더에서 찾는다.
    assert facts["PLANE_WEBSERVER"] == "kor-travel-dagster-webserver"


def test_the_own_sibling_render_is_recognised_for_a_rollback(tmp_path: Path) -> None:
    """되돌리기 release의 렌더(옛 `api grpc`, 공용 URL 없음)도 같은 family로 읽는다."""

    own = _sibling_render(**{
        "dagster-code-server.command": [
            "dagster", "api", "grpc", "-h", "127.0.0.1", "-p", "14005", "-m", "app.dagster.definitions",
            "--location-name", "kor-travel-transport",
        ],
        "dagster-code-server.environment": {"DAGSTER_POSTGRES_URL": "postgresql://x"},
        "dagster-code-server.healthcheck": {"test": ["CMD", "python", "-I", "-c", "old probe", "14005"]},
        "dagster-code-server.init": None,
    })
    facts = _facts("transport", _rendered(), _write(tmp_path, own))
    assert facts["CODE"] == "dagster-code-server" and facts["CODE_HAS_SHARED_URL"] == "no"


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"dagster-code-server.command": [
            "dagster", "code-server", "start", "-h", "127.0.0.1", "-p", "14005", "-m", "app.dagster.definitions",
        ]}, "differs from targets.transport.dagster.external in ['location_name']"),
        ({"dagster-code-server.command": [
            "dagster", "code-server", "start", "-h", "127.0.0.1", "-p", "14015", "-m", "app.dagster.definitions",
            "--location-name", "kor-travel-transport",
        ]}, "differs from targets.transport.dagster.external in ['port']"),
        ({"dagster-code-server.command": [
            "dagster", "code-server", "start", "-h", "0.0.0.0", "-p", "14005", "-m", "app.dagster.definitions",
            "--location-name", "kor-travel-transport",
        ]}, "does not listen on 127.0.0.1 only"),
        ({"dagster-daemon.container_name": "transport-daemon"}, "set container_name"),
        ({"dagster-gateway.depends_on": {}}, "differs from targets.transport.dagster.external in ['gateways']"),
    ],
)
def test_a_sibling_render_that_drifts_from_the_declaration_stops_the_cutover(
    tmp_path: Path, changes: dict[str, Any], said: str
) -> None:
    """빨간 대조군: 그 프로젝트의 compose가 Manager의 선언과 어긋나면 펜스 전에 멈춘다."""

    completed = _run_derive("transport", _rendered(), _write(tmp_path, _sibling_render(**changes)))
    assert completed.returncode != 0
    assert said in completed.stderr, completed.stderr


def test_a_sibling_target_without_its_render_is_refused() -> None:
    completed = _run_derive("transport", _rendered())
    assert completed.returncode != 0
    assert "no compose rendering for the sibling project" in completed.stderr


def _code_with(**changes: Any) -> dict[str, Any]:
    """공용 plane 모양의 code-server 하나를 바꾼 형제 렌더 — 위 `_sibling_render`의 `서비스.키` 대신 깊은 값."""

    document = _sibling_render()
    code = document["services"]["dagster-code-server"]
    for key, value in changes.items():
        if key == "command":
            code["command"] = value
        elif key == "probe":
            code["healthcheck"]["test"][4] = value
        elif key == "probe_port":
            code["healthcheck"]["test"][5] = value
        elif key == "shell":
            code["healthcheck"]["test"] = ["CMD-SHELL", "python -I -c 'x' 14005"]
        elif key == "ttl":
            code["environment"].pop("DAGSTER_GRPC_PROXY_HEARTBEAT_TTL_SECONDS")
        elif key == "init":
            code.pop("init")
    return document


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"command": ["dagster", "api", "grpc", "-h", "127.0.0.1", "-p", "14005", "-m", "app.dagster.definitions",
                      "--location-name", "kor-travel-transport"]}, "does not run `dagster code-server start`"),
        ({"probe": "import sys; sys.exit(0)"}, "is not the shared probe"),
        ({"probe_port": "14015"}, "is not the shared probe"),
        ({"shell": True}, "is not the shared probe"),
        ({"ttl": True}, "DAGSTER_GRPC_PROXY_HEARTBEAT_TTL_SECONDS differs"),
        ({"init": True}, "does not run under init"),
    ],
)
def test_a_sibling_code_server_on_the_shared_plane_follows_the_shared_code_server_rules(
    tmp_path: Path, changes: dict[str, Any], said: str
) -> None:
    """빨간 대조군: 공용 plane의 형제 code-server도 `api grpc`가 아니고 Manager의 공용 probe·heartbeat·init을 쓴다.

    Manager의 규칙 테스트(`test_every_shared_plane_code_server_reloads_its_definitions`, liveness 계약)는 이 저장소의
    compose만 본다 — 형제 프로젝트는 창 스크립트가 그 렌더에서 효과로 막는다.
    """

    completed = _run_derive("transport", _rendered(), _write(tmp_path, _code_with(**changes)))
    assert completed.returncode != 0
    assert said in completed.stderr, completed.stderr


def test_the_shared_probe_the_sibling_must_carry_is_the_manager_anchor() -> None:
    """형제가 맞출 기준은 Manager compose의 `x-dagster-code-server-probe` 원문이다(본 것에 하한)."""

    anchor = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["x-dagster-code-server-probe"]
    assert len(_shared_code_servers()) >= 4
    assert _shared_probe() == anchor


# ── 펜스 전 가드(2026-10-03 적대 리뷰 H1·M1) ──────────────────────────────────────


def _bash_function(name: str) -> str:
    """스크립트의 bash 함수 하나 — 여러 줄(닫는 `}` 줄)이든 python 래퍼(`'; }`로 끝남)이든 먼저 끝나는 쪽."""

    text = _SCRIPT.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{") + 1
    ends = [end + 3 for end in (text.find("\n}\n", start),) if end >= 0]
    ends += [end + 5 for end in (text.find("'; }\n", start),) if end >= 0]
    return text[start : min(ends)]


def _instigators(*rows: tuple[str, str, str, str]) -> str:
    """(kind, name, status, default) → 옛 webserver GraphQL 응답."""

    schedules = [{"name": n, "defaultStatus": d, "scheduleState": {"status": s}} for k, n, s, d in rows if k == "schedule"]
    sensors = [{"name": n, "defaultStatus": d, "sensorState": {"status": s}} for k, n, s, d in rows if k == "sensor"]
    return json.dumps({"data": {"repositoryOrError": {"__typename": "Repository", "schedules": schedules, "sensors": sensors}}})


@pytest.mark.parametrize(
    ("rows", "drift"),
    [
        ([("schedule", "a", "RUNNING", "RUNNING"), ("sensor", "s", "STOPPED", "STOPPED")], ""),
        ([("schedule", "a", "STOPPED", "RUNNING"), ("schedule", "b", "RUNNING", "RUNNING")], "schedule:a STOPPED!=RUNNING"),
        ([("sensor", "s", "RUNNING", "STOPPED")], "sensor:s RUNNING!=STOPPED"),
    ],
)
def test_instigators_off_their_code_default_are_named_before_the_fence(rows: list[tuple[str, str, str, str]], drift: str) -> None:
    """M1: 옛 instance에서 손으로 멈춘 schedule은 공용 instance(새로 시작)에서 코드 기본값으로 쏜다 — 펜스 전에 멈춘다."""

    result = subprocess.run(
        ["bash", "-c", _bash_function("instigators_off_their_code_default") + "instigators_off_their_code_default"],
        input=_instigators(*rows), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == drift


def test_an_instigator_without_a_default_status_fails_closed() -> None:
    payload = json.dumps({"data": {"repositoryOrError": {"__typename": "Repository", "schedules": [
        {"name": "a", "scheduleState": {"status": "RUNNING"}}], "sensors": []}}})
    result = subprocess.run(
        ["bash", "-c", _bash_function("instigators_off_their_code_default") + "instigators_off_their_code_default"],
        input=payload, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0 and "no defaultStatus" in result.stderr


def test_the_instigator_and_version_guards_run_before_the_fence() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    fence = text.index('PHASE="c-fence"')
    assert "defaultStatus" in text[text.index("instigators_q() {") : text.index("instigators_q() {") + 400]
    assert text.index('drift="$(instigators_off_their_code_default') < fence
    assert text.index('[[ -z "$drift" ]] || fail') < fence
    # 버전 대조는 derive 직후(forward·rollback·resume 갈래 전)에 — 형제 프로젝트 이미지마다.
    version = text.index('dagster_versions_match "$CODE_IMAGE" || fail')
    assert version < text.index('PHASE="f-precheck"') < fence


@pytest.mark.parametrize(("image", "host", "ok"), [("1.13.24", "1.13.24", True), ("1.13.25", "1.13.24", False), ("", "1.13.24", False)])
def test_a_sibling_image_with_another_dagster_than_the_plane_is_refused(image: str, host: str, ok: bool) -> None:
    """H1: 형제 이미지의 dagster가 공용 plane과 다르면 펜스 전에 멈춘다(높으면 공용 webserver가 버전 상한으로 내려간다)."""

    fakes = (
        f'docker() {{ printf "%s" "{image}"; }}\n'
        f"graphql() {{ printf '%s' '{{\"data\": {{\"version\": \"{host}\"}}}}'; }}\n"
        "say() { echo \"$*\"; }\n"
    )
    result = subprocess.run(
        ["bash", "-c", fakes + _bash_function("dagster_versions_match") + "dagster_versions_match img"],
        capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
