"""상시 서비스의 Docker 로그 상한 — 렌더된 compose 모델에서 파생해 센다(2026-11-03).

n150·odroid의 운영 컨테이너가 모두 `json-file` driver를 상한 없이 썼다(daemon 기본값도 없다). 로그는
컨테이너가 지워질 때까지 자라고, n150 공용 Dagster daemon 하나가 887 MB였다. 상한은 `daemon.json`(Docker
재시작이 필요하다)이 아니라 이 compose에 둔다. `x-logging` 한 자리를 상시 서비스가 alias로 받고, 각 서비스는
다음 재생성 때 상한을 얻는다.

이름을 외우지 않는다. 상시 서비스는 restart 정책으로 찾는다 — `restart: "no"`(또는 생략)이고
`deploy.restart_policy`도 없는 서비스만 one-shot이고, 나머지(`always`·`unless-stopped`·`on-failure[:n]`·
`deploy.restart_policy`)는 전부 상시다. 로그가 자라는 것은 컨테이너가 오래 사는 것이지 다시 뜨는 것이
아니므로 `on-failure`도 센다(적대 리뷰 2026-11-03 LOW-4). 꺼진 profile의 서비스도 profile을 켜면 상시로
돌므로 센다. 원문 파싱(중복 키 거부, alias 전개)과 실제 `docker compose config`
렌더 두 층에서 같은 판정을 한다. 렌더 층은 compose가 alias·profile을 우리 파서와 같게 읽는다는 것을 본다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker-compose.yml"

#: one-shot의 restart 정책 — 이것(또는 생략)만 범위 밖이다.
_ONESHOT_RESTART = "no"
#: `${NAME:?...}`·`${NAME?...}` — 값이 없으면 config 자체가 실패하는 필수 변수.
_REQUIRED_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?\?")
#: 상한이 있다는 뜻의 값 모양. 단위 없는 바이트 수나 0은 상한으로 치지 않는다.
_BOUNDED_SIZE = re.compile(r"[1-9][0-9]*[kmg]")
_BOUNDED_COUNT = re.compile(r"[1-9][0-9]*")


def _source() -> dict[str, Any]:
    document = load_yaml_rejecting_duplicate_keys(_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _is_long_running(service: Mapping[str, Any]) -> bool:
    restart = service.get("restart")
    if restart is not None and str(restart).strip().lower() != _ONESHOT_RESTART:
        return True
    deploy = service.get("deploy")
    policy = deploy.get("restart_policy") if isinstance(deploy, Mapping) else None
    return isinstance(policy, Mapping) and policy.get("condition") != "none"


def _long_running(services: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        name: service
        for name, service in services.items()
        if isinstance(service, Mapping) and _is_long_running(service)
    }


def _limit_problems(logging: object) -> list[str]:
    """상한이 없는 이유(비면 상한이 있다)."""

    if not isinstance(logging, Mapping):
        return ["no logging section"]
    problems = []
    if logging.get("driver") != "json-file":
        problems.append(f"driver {logging.get('driver')!r} is not json-file")
    options = logging.get("options")
    if not isinstance(options, Mapping):
        return [*problems, "no logging options"]
    size, count = options.get("max-size"), options.get("max-file")
    if not isinstance(size, str) or not _BOUNDED_SIZE.fullmatch(size):
        problems.append(f"max-size {size!r} is not a bounded size")
    if not isinstance(count, str) or not _BOUNDED_COUNT.fullmatch(count):
        problems.append(f"max-file {count!r} is not a positive count")
    return problems


def _unbounded(services: Mapping[str, Any]) -> dict[str, list[str]]:
    return {
        name: problems
        for name, service in _long_running(services).items()
        if (problems := _limit_problems(service.get("logging")))
    }


def _rendered_services(tmp_path: Path) -> dict[str, Any]:
    """실제 compose가 렌더한 서비스 — 원문 파일 그대로, 모든 profile을 켜고.

    project 디렉터리는 빈 `tmp_path`다. 그래서 `required: false` env_file(앱 저장소의 `.env` 등)이 이
    호스트에 있어도 읽히지 않는다. root `.env`도 읽지 않는다(`--env-file /dev/null`). 필수 변수는
    자리값으로 채운다.
    """

    if shutil.which("docker") is None:
        pytest.skip("Docker Compose가 없어 compose를 렌더할 수 없음")
    text = _COMPOSE.read_text(encoding="utf-8")
    services = _source()["services"]
    profiles = sorted(
        {profile for service in services.values() for profile in service.get("profiles") or []}
    )
    command = ["docker", "compose", "--env-file", "/dev/null", "--project-directory", str(tmp_path)]
    for profile in profiles:
        command += ["--profile", profile]
    completed = subprocess.run(
        [*command, "-f", str(_COMPOSE), "config", "--format", "json"],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            **{name: "1" for name in _REQUIRED_VAR.findall(text)},
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    rendered = json.loads(completed.stdout)["services"]
    assert isinstance(rendered, dict)
    # 모든 profile을 켰으니 원문의 서비스가 전부 렌더돼야 한다 — 빠진 서비스는 아래 대조가 재지 못한다.
    assert set(rendered) == set(services)
    return rendered


def test_the_derivation_finds_long_running_services() -> None:
    """항진명제 방지 — 상시 서비스가 비면 아래 대조가 아무것도 재지 않는다. one-shot도 있어야 판정이 가른다."""

    services = _source()["services"]
    long_running = _long_running(services)
    assert long_running, "restart 정책으로 상시 서비스를 하나도 찾지 못했다"
    assert set(services) - set(long_running), "one-shot을 하나도 가르지 못했다"


@pytest.mark.parametrize(
    ("service", "long_running"),
    [
        pytest.param({"restart": "unless-stopped"}, True, id="unless-stopped"),
        pytest.param({"restart": "always"}, True, id="always"),
        pytest.param({"restart": "on-failure"}, True, id="on-failure"),
        pytest.param({"restart": "on-failure:5"}, True, id="on-failure-count"),
        pytest.param({"deploy": {"restart_policy": {"condition": "any"}}}, True, id="deploy-policy"),
        pytest.param({"restart": "no"}, False, id="no"),
        pytest.param({"restart": False}, False, id="unquoted-no"),
        pytest.param({}, False, id="absent"),
        pytest.param({"deploy": {"restart_policy": {"condition": "none"}}}, False, id="deploy-none"),
    ],
)
def test_the_long_running_rule_counts_lifetime_not_restarts(
    service: dict[str, Any], long_running: bool
) -> None:
    """빨간 대조군 — 상한 없는 `on-failure` daemon이나 `deploy.restart_policy` 서비스가 조용히 빠지지 않는다."""

    assert _is_long_running(service) is long_running
    assert bool(_unbounded({"svc": service})) is long_running


def test_every_long_running_service_caps_its_json_file_log() -> None:
    assert _unbounded(_source()["services"]) == {}


def test_long_running_services_share_one_log_policy() -> None:
    """상한은 한 자리(`x-logging`)에서 온다 — 서비스마다 값이 갈라지면 어느 쪽이 정본인지 알 수 없다."""

    policies = {
        json.dumps(service.get("logging"), sort_keys=True)
        for service in _long_running(_source()["services"]).values()
    }
    assert len(policies) == 1, sorted(policies)


def test_the_rendered_compose_keeps_the_log_limits(tmp_path: Path) -> None:
    """compose가 alias·profile을 우리 파서와 같게 읽는다 — 렌더된 모델에서도 상시 서비스마다 상한이 있다."""

    rendered = _rendered_services(tmp_path)
    assert _long_running(rendered).keys() == _long_running(_source()["services"]).keys()
    assert _unbounded(rendered) == {}
