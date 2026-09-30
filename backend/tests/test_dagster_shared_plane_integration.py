"""공용 Dagster plane(daemon·webserver·gateway)을 **정본의 실행 형태 그대로** 격리 실행한다(ADR-54).

선언 대조(`test_dagster_shared_workspace_is_derived.py`)로는 알 수 없는 것:

- 파생 workspace가 비어 있을 때(모든 target이 `own`) Dagster 1.13.24의 daemon·webserver가 **뜨고
  healthy한가** — `load_from: []`를 받아들이는가, daemon이 heartbeat를 `dagster_shared`에 쓰는가,
  instigator가 하나도 생기지 않는가(n150 3단계 1번의 검증 항목).
- 공용 webserver probe가 실제 webserver 앞에서 **기대 location이 없으면 빨갛다**.
- gateway가 비-root nginx로 뜨고 Basic Auth·same-origin POST 검사·`/health` 무인증·frame-ancestors가
  실제로 무는가, 비밀번호가 비었거나 origin 값이 URL 모양이 아니면 **기동을 거부하는가**.

정본 compose의 여섯 서비스(공용 instance·db-init·migrate·daemon·webserver·gateway)를 가져오고 호스트에
닿는 것(PGDATA bind·host network·컨테이너 이름·host 포트)만 뺀다. 모두 공용 instance의 netns를 빌려
`127.0.0.1`로 서로 닿는다. 호스트 이미지는 격리 태그로 만들고 끝에 지운다(운영 태그를 만들지 않는다).
gateway 이미지는 정본의 digest 그대로 쓰고 pull하지 않는다 — 없으면 gate대로 skip/fail이다.
gate는 `test_compose_readiness_integration.py`의 것을 그대로 쓴다.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from test_compose_readiness_integration import _unavailable_docker_fixture
from test_dagster_shared_storage_integration import _canonical, _sql_in
from test_dagster_shared_workspace_is_derived import _resolve
from test_shared_postgres_runtime_integration import (
    _ISOLATED_PORT,
    _fixture_service,
    _remove_project_residue,
    _run,
    _wait_healthy,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHARED_POSTGRES = "kor-travel-shared-postgres"
_DB_INIT = "kor-travel-shared-db-init-dagster"
_MIGRATE = "kor-travel-dagster-storage-migrate"
_DAEMON = "kor-travel-dagster-daemon"
_WEBSERVER = "kor-travel-dagster-webserver"
_GATEWAY = "kor-travel-dagster-gateway"
_PROJECT_PREFIX = "ktdm-dagsterplane-"
_BUILD_TIMEOUT = 1800
_RUN_TIMEOUT = 600

#: 정본에서 옮기는 실행 형태. 컨테이너 이름·network·build·ports는 옮기지 않는다.
_CARRIED_KEYS = (
    "image", "command", "entrypoint", "environment", "secrets", "depends_on", "restart",
    "volumes", "healthcheck", "init", "user",
)

_PUBLIC_ORIGIN = "https://dagster.example.test"
_FRAME_ANCESTOR = "https://geo.example.test"
_UI_USER = "admin"

#: gateway를 webserver 컨테이너 안에서(같은 netns) 부른다 — 상태 코드와 CSP 헤더만 돌려준다.
_GATEWAY_PROBE = """
import base64, json, sys, urllib.error, urllib.request

port, user, password, public = sys.argv[1:5]

def call(path, method="GET", secret=password, origin=None, body=None):
    request = urllib.request.Request(
        "http://127.0.0.1:%s%s" % (port, path), data=body, method=method)
    if secret is not None:
        token = base64.b64encode(("%s:%s" % (user, secret)).encode()).decode()
        request.add_header("Authorization", "Basic " + token)
    if origin:
        request.add_header("Origin", origin)
    if body:
        request.add_header("Content-Type", "application/json")
    try:
        response = urllib.request.urlopen(request, timeout=15)
        return response.status, response.headers.get_all("Content-Security-Policy") or []
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get_all("Content-Security-Policy") or []

query = json.dumps({"query": "{workspaceOrError{__typename}}"}).encode()
ui_status, csp = call("/")
print(json.dumps({
    "health": call("/health", secret=None)[0],
    "anonymous": call("/", secret=None)[0],
    "wrong_password": call("/", secret=password + "x")[0],
    "ui": ui_status,
    "csp": csp,
    "post_without_origin": call("/graphql", "POST", body=query)[0],
    "post_foreign_origin": call("/graphql", "POST", origin="https://evil.example.test", body=query)[0],
    "post_public_origin": call("/graphql", "POST", origin=public, body=query)[0],
    "post_local_origin": call("/graphql", "POST", origin="http://127.0.0.1:%s" % port, body=query)[0],
    "post_public_origin_anonymous": call(
        "/graphql", "POST", secret=None, origin=public, body=query)[0],
}))
"""


@dataclass(frozen=True)
class _Plane:
    compose: tuple[str, ...]
    env: dict[str, str]
    project: str
    ui_password: str
    ui_password_env: str


def _carry(service: dict[str, Any], postgres: str) -> dict[str, Any]:
    carried = {key: service[key] for key in _CARRIED_KEYS if key in service}
    carried["network_mode"] = f"service:{postgres}"
    volumes = [
        str(_REPO_ROOT / str(volume)[2:]) if str(volume).startswith("./") else volume
        for volume in carried.get("volumes", [])
    ]
    if volumes:
        carried["volumes"] = volumes
    return carried


def _container(plane: _Plane, service: str) -> str:
    ps = _run(*plane.compose, "ps", "--all", "--quiet", service, env=plane.env)
    assert ps.returncode == 0 and ps.stdout.strip(), ps.stderr
    return ps.stdout.strip().splitlines()[0]


@pytest.fixture
def isolated_plane(tmp_path: Path) -> Iterator[_Plane]:
    yield from _isolated(tmp_path, build_host_image=True)


@pytest.fixture
def isolated_gateway(tmp_path: Path) -> Iterator[_Plane]:
    """gateway 기동 거부만 본다 — 호스트 이미지를 만들지 않는다. netns를 빌려 줄 공용 instance만 띄운다."""

    for plane in _isolated(tmp_path, build_host_image=False):
        up = _run(
            *plane.compose, "up", "--detach", "--pull", "never", "--no-deps", _SHARED_POSTGRES,
            env=plane.env, timeout=_RUN_TIMEOUT,
        )
        assert up.returncode == 0, up.stderr
        yield plane


def _isolated(tmp_path: Path, *, build_host_image: bool) -> Iterator[_Plane]:
    canonical = _canonical()
    services = canonical["services"]
    postgres = _fixture_service()
    postgres["environment"]["PGCTLTIMEOUT"] = "300"
    gateway_image = services[_GATEWAY]["image"]
    try:
        available = (
            _run("docker", "compose", "version").returncode == 0
            and _run("docker", "image", "inspect", postgres["image"]).returncode == 0
            and _run("docker", "image", "inspect", services[_DB_INIT]["image"]).returncode == 0
            and _run("docker", "image", "inspect", gateway_image).returncode == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        available = False
    if not available:
        _unavailable_docker_fixture("Docker Compose 또는 공용 instance·gateway 이미지를 쓸 수 없음")

    # 이미지 참조와 compose 프로젝트 이름에 쓸 수 있는 글자만 — tmp 이름은 `_`로 시작할 수 있다.
    project = f"{_PROJECT_PREFIX}{os.getpid()}-{secrets.token_hex(4)}"
    image = f"{project}:it"
    context = _REPO_ROOT / services[_MIGRATE]["build"]["context"]
    host_image = services[_MIGRATE]["image"]

    names = (_DB_INIT, _MIGRATE, _DAEMON, _WEBSERVER, _GATEWAY)
    used_secrets = {entry["source"] for name in names for entry in services[name].get("secrets", [])}
    admin_password = postgres["environment"]["POSTGRES_PASSWORD"]
    env = {
        **os.environ,
        "KOR_TRAVEL_SHARED_DB_PORT": _ISOLATED_PORT,
        "KTDM_PROD_URL_DAGSTER": _PUBLIC_ORIGIN,
        "KOR_TRAVEL_DAGSTER_FRAME_ANCESTORS": _FRAME_ANCESTOR,
        "KOR_TRAVEL_DAGSTER_UI_USER": _UI_USER,
    }
    providers: dict[str, str] = {}
    for secret in used_secrets:
        provider = canonical["secrets"][secret]["environment"]
        providers[secret] = provider
        env[provider] = (
            admin_password if secret == "kor-travel-shared-postgres-password" else secrets.token_hex(16)
        )
    ui_secret = services[_GATEWAY]["secrets"][0]["source"]

    document: dict[str, Any] = {"services": {_SHARED_POSTGRES: postgres}, "secrets": {}}
    for name in names:
        carried = _carry(services[name], _SHARED_POSTGRES)
        if carried.get("image") == host_image:
            # 정본의 tag는 내용 해시(운영 이름)다 — 격리 tag로 바꿔 운영 이미지를 만들지 않는다.
            carried["image"] = image
        document["services"][name] = carried
    document["secrets"] = {secret: canonical["secrets"][secret] for secret in used_secrets}
    compose_path = tmp_path / "compose.yml"
    compose_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    compose = ("docker", "compose", "--file", str(compose_path), "--project-name", project)
    built = False
    try:
        if build_host_image:
            build = _run("docker", "build", "--tag", image, str(context), timeout=_BUILD_TIMEOUT)
            assert build.returncode == 0, build.stdout[-3000:] + build.stderr[-3000:]
            built = True
        yield _Plane(
            compose=compose,
            env=env,
            project=project,
            ui_password=env[providers[ui_secret]],
            ui_password_env=providers[ui_secret],
        )
    finally:
        down = _run(
            *compose, "down", "--volumes", "--remove-orphans", "--timeout", "30",
            env=env, timeout=400,
        )
        residue = _remove_project_residue(project)
        removed = _run("docker", "image", "rm", "--force", image) if built else None
        if down.returncode != 0 or residue or _remove_project_residue(project):
            pytest.fail(f"fixture cleanup 실패: down={down.returncode}, 잔여={residue}")
        if removed is not None and removed.returncode != 0:
            pytest.fail(f"격리 이미지 {image}를 지우지 못함: {removed.stderr}")


def _probe_in_webserver(plane: _Plane, workspace_text: str) -> subprocess.CompletedProcess[str]:
    """정본 webserver probe 원문을 실제 webserver 컨테이너 안에서 다른 workspace 파일로 돌린다."""

    container = _container(plane, _WEBSERVER)
    written = subprocess.run(
        ["docker", "exec", "-i", container, "sh", "-c", "cat > /tmp/probe-workspace.yaml"],
        input=workspace_text, text=True, capture_output=True, check=False, timeout=60,
    )
    assert written.returncode == 0, written.stderr
    test = [str(part) for part in _canonical()["services"][_WEBSERVER]["healthcheck"]["test"]]
    return _run(
        "docker", "exec", container, "python", "-I", "-c", test[4],
        _resolve(test[5], {}), "/tmp/probe-workspace.yaml",
        timeout=120,
    )


def test_the_empty_plane_is_healthy_and_the_gateway_guards_the_ui(isolated_plane: _Plane) -> None:
    plane = isolated_plane
    up = _run(
        *plane.compose, "up", "--detach", "--pull", "never", _DAEMON, _WEBSERVER, _GATEWAY,
        env=plane.env, timeout=_RUN_TIMEOUT,
    )
    assert up.returncode == 0, up.stdout[-3000:] + up.stderr[-3000:]

    # 정본의 healthcheck 그대로 — 빈 workspace에서 셋 다 healthy다.
    for service in (_WEBSERVER, _DAEMON, _GATEWAY):
        _wait_healthy(_container(plane, service))

    # daemon이 heartbeat를 공용 DB에 쓴다. 아무 instigator도 없다(모든 target이 own).
    canonical = _canonical()["services"]
    database = canonical[_DB_INIT]["environment"]["KOR_TRAVEL_DAGSTER_SHARED_DB"]
    instance = SimpleNamespace(postgres=_container(plane, _SHARED_POSTGRES))
    assert int(_sql_in(instance, database, "SELECT count(*) FROM daemon_heartbeats")[0]) > 0  # type: ignore[arg-type]
    assert _sql_in(instance, database, "SELECT count(*) FROM instigators") == ["0"]  # type: ignore[arg-type]
    assert _sql_in(instance, database, "SELECT count(*) FROM jobs") == ["0"]  # type: ignore[arg-type]

    # 비-root로 돈다.
    for service, uid in ((_WEBSERVER, "10001"), (_DAEMON, "10001"), (_GATEWAY, "101")):
        whoami = _run("docker", "exec", _container(plane, service), "id", "-u")
        assert whoami.stdout.strip() == uid, (service, whoami.stdout, whoami.stderr)

    # 공용 probe: 붙은(빈) workspace로는 초록이고, 합류하지 않은 location을 기대하면 빨갛다.
    missing = _probe_in_webserver(
        plane,
        "load_from:\n  - grpc_server: {host: 127.0.0.1, port: 4999, location_name: not.joined}\n",
    )
    assert missing.returncode != 0
    assert "code locations not loaded: ['not.joined']" in missing.stderr
    empty = _probe_in_webserver(plane, "load_from: []\n")
    assert empty.returncode == 0, empty.stderr

    # gateway — webserver 컨테이너 안에서(같은 netns) 부른다.
    port = _resolve(str(canonical[_GATEWAY]["environment"]["DAGSTER_GATEWAY_PORT"]), {})
    answered = _run(
        "docker", "exec", _container(plane, _WEBSERVER), "python", "-I", "-c", _GATEWAY_PROBE,
        port, _UI_USER, plane.ui_password, _PUBLIC_ORIGIN,
        timeout=120,
    )
    assert answered.returncode == 0, answered.stderr
    seen = json.loads(answered.stdout.strip().splitlines()[-1])
    # Dagster webserver는 자기 CSP(script-src 등)를 보낸다. gateway의 frame-ancestors는 **두 번째** 정책으로
    # 붙는다 — 브라우저는 정책 둘을 모두 적용하므로 Dagster의 것을 지우지 않고 더한다.
    policies = seen.pop("csp")
    assert f"frame-ancestors 'self' {_FRAME_ANCESTOR}" in policies, policies
    assert not any("frame-ancestors" in p for p in policies if not p.startswith("frame-ancestors")), (
        policies
    )
    assert seen == {
        "health": 204,
        "anonymous": 401,
        "wrong_password": 401,
        "ui": 200,
        "post_without_origin": 403,
        "post_foreign_origin": 403,
        "post_public_origin": 200,
        "post_local_origin": 200,
        "post_public_origin_anonymous": 401,
    }


@pytest.mark.parametrize(
    ("override", "said"),
    [
        ({"__password__": ""}, "KOR_TRAVEL_DAGSTER_UI_PASSWORD is empty"),
        ({"KTDM_PROD_URL_DAGSTER": 'https://x.test/" 1; default 1; "'}, "not an origin"),
        ({"KOR_TRAVEL_DAGSTER_FRAME_ANCESTORS": "'unsafe-inline'"}, "not an origin"),
        ({"KOR_TRAVEL_DAGSTER_UI_USER": "a:b"}, "KOR_TRAVEL_DAGSTER_UI_USER must be"),
    ],
)
def test_the_gateway_refuses_to_start_unguarded(
    isolated_gateway: _Plane, override: dict[str, str], said: str
) -> None:
    """비밀번호가 비었거나 설정 값이 nginx 설정을 깰 모양이면 nginx를 띄우지 않고 죽는다."""

    plane = isolated_gateway
    env = dict(plane.env)
    for key, value in override.items():
        env[plane.ui_password_env if key == "__password__" else key] = value
    refused = _run(
        *plane.compose, "run", "--rm", "--no-deps", _GATEWAY,
        env=env, timeout=_RUN_TIMEOUT,
    )
    assert refused.returncode != 0
    assert said in refused.stdout + refused.stderr, refused.stdout[-2000:] + refused.stderr[-2000:]
