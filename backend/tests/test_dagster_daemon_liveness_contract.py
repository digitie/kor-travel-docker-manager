"""Dagster daemon의 생존을 무엇이 보는가를 결박한다.

이 계약이 생긴 경위. 형제 프로젝트 `kor-travel-weather`가 같은 호스트에서 수집
정지를 두 번 겪었고, 두 번째는 시간별 수집이 18시간 멈췄다. 원인의 한 겹은
`run_monitoring`(프로세스가 사라진 run을 회수)이 없었던 것이지만, **더 깊은 겹은
그 회수 기제를 담은 프로세스의 생존을 아무도 보지 않았다는 것**이다.

`run_monitoring`과 queued run dequeue는 전부 `dagster-daemon` 프로세스 안의 스레드고,
controller의 `check_daemon_threads`는 스레드 하나가 죽으면 프로세스를 스스로 끝낸다.
그런데 이 compose의 두 daemon 서비스에는 healthcheck가 없었다 — 같은 스택의
webserver와 api는 갖고 있었다. 그래서 두 실패가 조용했다.

1. 프로세스가 나가면 `restart: unless-stopped`가 되살리지만, **그 사이 회수 기제가
   없었다는 사실은 어디에도 남지 않는다.**
2. 스레드가 살아 있으면서 끼이면 heartbeat만 낡는다. controller는 그것에 warning만
   남기고 프로세스를 끝내지 않으므로 아무 일도 일어나지 않는다.

검사는 **이름 목록이 아니라 command에서 유도한다.** daemon 서비스가 새로 생기면
그것도 같은 요구를 받는다 — 목록에 적어야만 재는 검사는 적어 둔 것만 잰다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"

#: dagster가 제공하는 정식 생존 판정. `all_daemons_live`가 required daemon 전부의
#: heartbeat 신선도를 보고(`ignore_errors=True`) 하나라도 낡으면 exit 1이다.
_LIVENESS_PROBE = "liveness-check"

#: 유예를 명시하는 env. 기본값은 dagster의 1800초이고 판정식이
#: `now <= heartbeat + interval + tolerance`이므로, 그대로 두면 끼인 스레드를
#: 31분 뒤에 알게 된다 — interval·retries를 줄여도 그 지연은 줄지 않는다.
_TOLERANCE_ENV = "DAGSTER_DAEMON_HEARTBEAT_TOLERANCE"

#: dagster 자신이 같은 controller에서 "이만큼 낡으면 계속할 수 없다"로 쓰는 값
#: (`DEFAULT_WORKSPACE_FRESHNESS_TOLERANCE`). 임의값을 쓰지 않기 위한 상한이다.
_MAX_TOLERATED_STALENESS_SECONDS = 300


def _compose() -> dict[str, Any]:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _command_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return ""


def _daemon_services() -> dict[str, dict[str, Any]]:
    """`dagster-daemon`을 실행하는 서비스. **이름으로 찾지 않는다.**"""
    services = _compose()["services"]
    return {
        name: service
        for name, service in services.items()
        if "dagster-daemon" in _command_text(service.get("command"))
        or "dagster-daemon" in _command_text(service.get("entrypoint"))
    }


def test_the_compose_declares_at_least_one_dagster_daemon() -> None:
    """유도의 전제. 하나도 못 찾으면 아래 검사들이 조용히 항진명제가 된다."""
    found = _daemon_services()
    assert found, (
        "`dagster-daemon`을 실행하는 서비스를 command에서 찾지 못했다 — "
        "command 모양이 바뀌었거나 이 계약의 파서가 낡았다. 어느 쪽이든 아래 "
        "검사들이 아무것도 재지 않는 상태다."
    )


@pytest.mark.parametrize("service_name", sorted(_daemon_services()))
def test_every_dagster_daemon_has_a_liveness_healthcheck(service_name: str) -> None:
    """회수 기제를 담은 프로세스의 생존을 무엇이 본다."""
    service = _daemon_services()[service_name]
    healthcheck = service.get("healthcheck")
    assert healthcheck, (
        f"`{service_name}`이 run_monitoring과 queue dequeue를 담고 있는데 "
        "healthcheck가 없다. 프로세스가 나가거나 스레드가 끼이면 회수 기제가 조용히 "
        "사라지고, 그 사실이 어디에도 남지 않는다."
    )
    probe = _command_text(healthcheck.get("test"))
    assert _LIVENESS_PROBE in probe, (
        f"`{service_name}`의 healthcheck가 dagster의 정식 판정을 쓰지 않는다: {probe}. "
        "직접 만든 판정(예: pgrep)은 프로세스 존재만 보고 heartbeat 신선도를 보지 "
        "않으므로, 끼인 스레드를 영원히 healthy로 보고한다."
    )
    # 기동 창이 없으면 code location 로딩 중에 unhealthy로 떨어진다.
    assert healthcheck.get("start_period"), (service_name, healthcheck)


@pytest.mark.parametrize("service_name", sorted(_daemon_services()))
def test_every_dagster_daemon_declares_its_staleness_tolerance(
    service_name: str,
) -> None:
    """유예를 기본값(1800초)에 맡기지 않는다.

    판정식은 `now <= heartbeat + interval + tolerance`다. 기본 1800이면 끼인
    스레드를 31분 뒤에 알게 되고, healthcheck의 `interval`·`retries`를 어떻게
    줄여도 그 지연은 줄지 않는다 — 그 둘은 **프로브를 얼마나 자주 부르는가**이고
    지연을 정하는 것은 tolerance다. 그래서 값을 명시하고 상한을 건다.

    상한 300은 임의값이 아니다. dagster 자신이 같은 controller에서
    `DEFAULT_WORKSPACE_FRESHNESS_TOLERANCE = 300`을 "이만큼 낡으면 계속할 수 없다"로
    쓴다. daemon loop의 정상 heartbeat 간격은 15~30초이므로 10~20배 여유다.
    """
    environment = _daemon_services()[service_name].get("environment") or {}
    raw = environment.get(_TOLERANCE_ENV)
    assert raw is not None, (
        f"`{service_name}`이 {_TOLERANCE_ENV}를 선언하지 않는다 — dagster 기본값 "
        "1800초가 이기고, 끼인 스레드를 31분 뒤에 알게 된다."
    )
    # `${VAR:-300}` 형태의 기본값을 읽는다. 그 기본값이 계약이다 — env가 비어 있는
    # 배포에서 실제로 적용되는 값이기 때문이다.
    text = str(raw)
    default = text.split(":-", maxsplit=1)[1].rstrip("}") if ":-" in text else text
    assert default.isdecimal(), (service_name, raw)
    assert 1 <= int(default) <= _MAX_TOLERATED_STALENESS_SECONDS, (
        f"`{service_name}`의 {_TOLERANCE_ENV} 기본값이 {default}초다. dagster가 "
        f"'이만큼 낡으면 계속할 수 없다'로 쓰는 {_MAX_TOLERATED_STALENESS_SECONDS}초를 "
        "넘으면 그 창 동안 회수 기제가 멈춘 채로 healthy로 보고된다."
    )


@pytest.mark.parametrize("service_name", sorted(_daemon_services()))
def test_every_dagster_daemon_comes_back_on_its_own(service_name: str) -> None:
    """healthcheck는 보이게만 한다 — 되돌리는 것은 restart 정책이다.

    docker는 unhealthy로 재시작하지 않고(그건 Swarm이다) Exited 컨테이너를 되살리는
    watchdog도 이 배포에 없다. `always`는 쓰지 않는다 — 명시적 stop도 되돌려 파괴적
    rebuild(`docker compose stop`)와 운영자의 stop 버튼(`container.stop()`)과 싸운다.
    """
    restart = _daemon_services()[service_name].get("restart")
    assert restart == "unless-stopped", (
        f"`{service_name}`의 restart 정책이 `unless-stopped`가 아니다: {restart!r}. "
        "healthcheck는 상태를 보이게 하지만 되돌리지 않는다."
    )


# ── webserver 쪽 ────────────────────────────────────────────────────────
#
# daemon과 같은 질문을 webserver에도 한다. 다만 판정 대상이 다르다 — daemon은
# **자기 스레드**의 생존이고, webserver는 **code location이 실제로 로드됐는가**다.
#
# 2026-09-19 실측: 세 Dagster webserver(map 12702 / pinvi 12802 / geo 12502)의
# healthcheck가 전부 `urlopen('http://127.0.0.1:<port>/')`였다. 그 경로는 webserver가
# **정적으로** 주는 페이지라, code location이 로드에 실패해도 200이 돌아온다.
# 그러면 컨테이너는 끝까지 healthy로 보고되고 job은 조용히 멈춘다.
#
# 이 사고는 이미 집 안에 기록돼 있었다 — `pinvi/apps/etl/Dockerfile`의 HEALTHCHECK
# 주석이 정확히 그 probe를 규탄하며 GraphQL 대안을 적어 뒀는데, Manager compose의
# healthcheck가 그것을 덮고 있었다. 이미지가 옳은 것을 알고 있어도 orchestrator가
# 덮으면 소용이 없다.

#: code location이 로드됐을 때만 돌아오는 GraphQL 타입.
#:
#: `repositoriesOrError`는 성공 시 `RepositoryConnection`, 실패 시 `PythonError`를
#: 준다. 둘 다 HTTP 200이므로 **본문을 봐야** 갈린다.
_CODE_LOCATION_PROBE_FRAGMENT = "repositoriesOrError"
_CODE_LOCATION_PROBE_EXPECTED = "RepositoryConnection"


def _webserver_services() -> dict[str, dict[str, Any]]:
    """`dagster-webserver`를 실행하는 서비스. **이름으로 찾지 않는다.**"""
    services = _compose()["services"]
    return {
        name: service
        for name, service in services.items()
        if "dagster-webserver" in _command_text(service.get("command"))
        or "dagster-webserver" in _command_text(service.get("entrypoint"))
    }


def test_the_compose_declares_at_least_one_dagster_webserver() -> None:
    """유도의 전제. 못 찾으면 아래 검사가 조용히 항진명제가 된다."""
    found = _webserver_services()
    assert found, (
        "`dagster-webserver`를 실행하는 서비스를 command에서 찾지 못했다 — "
        "command 모양이 바뀌었거나 이 계약의 파서가 낡았다."
    )


@pytest.mark.parametrize("service_name", sorted(_webserver_services()))
def test_every_dagster_webserver_probe_asks_whether_code_loaded(
    service_name: str,
) -> None:
    """webserver probe는 **code location이 로드됐는지**를 물어야 한다.

    정적 페이지를 받아 오는 probe는 "프로세스가 떠 있다"만 재고, 정작 그 프로세스가
    존재하는 이유(job을 실행할 수 있는가)는 재지 않는다.
    """

    service = _webserver_services()[service_name]
    healthcheck = service.get("healthcheck")
    assert healthcheck, (
        f"`{service_name}`에 healthcheck가 없다 — code location 로드 실패가 조용해진다."
    )
    probe = _command_text(healthcheck.get("test"))
    assert _CODE_LOCATION_PROBE_FRAGMENT in probe, (
        f"`{service_name}`의 healthcheck가 code location을 묻지 않는다: {probe}. "
        "`/`나 `/server_info`는 webserver가 정적으로 주는 문서라 code location이 "
        "죽어도 200이다 — 컨테이너는 끝까지 healthy로 보고되고 job은 조용히 멈춘다."
    )
    assert _CODE_LOCATION_PROBE_EXPECTED in probe, (
        f"`{service_name}`의 probe가 응답 **본문을 판정하지 않는다**: {probe}. "
        "`repositoriesOrError`는 실패 시에도 HTTP 200으로 `PythonError`를 주므로, "
        "요청이 성공한 것만 보면 아무것도 관측하지 못한다."
    )
    # 기동 창이 없으면 code location 로딩 중에 unhealthy로 떨어진다.
    assert healthcheck.get("start_period"), (service_name, healthcheck)


# ── code-server 쪽 (PinVi ADR-069, 2026-09-19) ──────────────────────────
#
# code-server는 daemon·webserver와 또 다른 질문을 받는다 — **유일하게 유저
# 코드를 실제로 import·실행하는 프로세스**이므로, 그 프로세스가 죽으면
# webserver/daemon이 아무리 healthy해도 job은 하나도 못 돈다. daemon처럼
# "이름 목록이 아니라 command에서 유도한다" — code-server가 새로 생기면(다른
# 프로젝트가 §7 1단계를 밟으면) 같은 요구를 자동으로 받는다.

_GRPC_HEALTH_PROBE = "grpc-health-check"


def _code_server_services() -> dict[str, dict[str, Any]]:
    """`dagster api grpc`를 실행하는 서비스. **이름으로 찾지 않는다.**"""
    services = _compose()["services"]
    return {
        name: service
        for name, service in services.items()
        if "dagster api grpc" in _command_text(service.get("command"))
        or "dagster api grpc" in _command_text(service.get("entrypoint"))
    }


def test_the_compose_declares_at_least_one_dagster_code_server() -> None:
    """유도의 전제. 못 찾으면 아래 검사가 조용히 항진명제가 된다."""
    found = _code_server_services()
    assert found, (
        "`dagster api grpc`를 실행하는 서비스를 command에서 찾지 못했다 — "
        "command 모양이 바뀌었거나 이 계약의 파서가 낡았다."
    )


@pytest.mark.parametrize("service_name", sorted(_code_server_services()))
def test_every_dagster_code_server_has_a_grpc_health_healthcheck(
    service_name: str,
) -> None:
    """유저 코드를 실제로 실행하는 유일한 프로세스의 생존을 무엇이 본다.

    프로세스 존재만 보는 probe(예: pgrep)는 gRPC 서버가 실제로 응답하는지를
    보지 못한다 — `dagster api grpc-health-check`는 dagster 자신의 gRPC health
    protocol을 실제로 호출한다.
    """
    service = _code_server_services()[service_name]
    healthcheck = service.get("healthcheck")
    assert healthcheck, (
        f"`{service_name}`이 유저 코드를 실행하는 유일한 프로세스인데 "
        "healthcheck가 없다 — 죽거나 응답 없어져도 조용하다."
    )
    probe = _command_text(healthcheck.get("test"))
    assert _GRPC_HEALTH_PROBE in probe, (
        f"`{service_name}`의 healthcheck가 dagster의 gRPC health protocol을 쓰지 않는다: {probe}."
    )
    assert healthcheck.get("start_period"), (service_name, healthcheck)


@pytest.mark.parametrize("service_name", sorted(_code_server_services()))
def test_every_dagster_code_server_comes_back_on_its_own(service_name: str) -> None:
    """daemon/webserver와 같은 요구 — healthcheck는 보이게만 하고, 되돌리는
    것은 restart 정책이다."""
    restart = _code_server_services()[service_name].get("restart")
    assert restart == "unless-stopped", (
        f"`{service_name}`의 restart 정책이 `unless-stopped`가 아니다: {restart!r}."
    )
