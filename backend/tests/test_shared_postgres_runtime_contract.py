"""공용 PostgreSQL instance가 **자기 고아 때문에** crash recovery하지 않도록 결박한다(ADR-52).

경위. 2026-09-25~28 `kor-travel-shared-postgres`가 다섯 번 "server process (PID N)
exited with exit code 2" → 전 서버 프로세스 종료 → crash recovery를 겪었고, 매번 모든
테넌트(concierge·PinVi·weather·transport·geo)가 3~11분 끊겼다. 죽은 PID는 한 번도 로그를
남기지 않은 **backend가 아닌** 프로세스였다.

경로는 둘이 겹친 것이다.

1. postmaster가 컨테이너 PID 1이었다(`init` 없음). docker exec·healthcheck가 남긴 고아는
   PID 1로 입양되고, PG16 postmaster는 거둔 자식이 0·1 외의 코드로 끝나면 BackendList를
   보기도 전에 HandleChildCrash를 부른다.
2. healthcheck가 `CMD-SHELL pg_isready ...`였다. dash는 명령을 exec하지 않고 fork하므로
   호스트 stall로 probe가 timeout되면 docker가 `sh`만 죽이고 `pg_isready`는 고아가 되어
   exit 2("no response")로 끝났다.

둘 중 하나만 고쳐도 이번 경로는 막히지만 **둘 다** 요구한다 — exec 형식은 healthcheck만
막고, `docker exec sh -c psql`처럼 사람이·에이전트가 남기는 고아는 init만 막는다.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_SERVICE = "kor-travel-shared-postgres"

#: docker 기본 10초는 부하 중 fast shutdown의 checkpoint를 못 기다린다(2026-09-25 08:03
#: `systemctl restart docker` → SIGKILL → 재기동이 73GB fsync crash recovery).
_MIN_STOP_GRACE_SECONDS = 60


def _service() -> dict[str, Any]:
    document = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    service = document["services"][_SERVICE]
    assert isinstance(service, dict)
    return service


def _seconds(duration: object) -> float:
    """compose duration 문자열(`300s`, `5m`, `1m30s`)을 초로."""
    text = str(duration).strip()
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|us|h|m|s)", text)
    assert parts and "".join(value + unit for value, unit in parts) == text, (
        f"읽을 수 없는 duration: {duration!r}"
    )
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001, "us": 0.000001}
    return sum(float(value) * scale[unit] for value, unit in parts)


def _probe() -> list[str]:
    test = _service()["healthcheck"]["test"]
    assert isinstance(test, list), "healthcheck test가 목록이 아니다(문자열은 CMD-SHELL이다)"
    return [str(token) for token in test]


def test_the_shared_postgres_reaps_its_orphans_with_init() -> None:
    """PID 1이 init이어야 exec·healthcheck 고아의 종료 코드를 postmaster가 보지 않는다."""
    assert _service().get("init") is True, (
        f"`{_SERVICE}`에 `init: true`가 없다 — postmaster가 PID 1이면 고아의 exit 2가 "
        "클러스터 전체 crash recovery가 된다(2026-09-25~28 다섯 번)."
    )


def test_the_shared_postgres_probe_runs_the_binary_without_a_shell() -> None:
    """probe는 exec 형식으로 **실제 binary**를 부른다 — 셸도, Perl pg_wrapper도 없이."""
    probe = _probe()
    assert probe[0] == "CMD", f"healthcheck가 exec 형식(`CMD`)이 아니다: {probe[:2]}"
    program = PurePosixPath(probe[1])
    assert program.name == "pg_isready", f"probe 프로그램이 pg_isready가 아니다: {program}"
    assert program.is_absolute() and "/lib/postgresql/" in str(program), (
        f"`{program}`는 PATH의 `pg_isready`(Debian의 Perl pg_wrapper)일 수 있다 — "
        "wrapper도 자식을 fork하므로 binary의 절대 경로를 쓴다."
    )


def test_the_probe_gives_up_before_docker_kills_it() -> None:
    """`pg_isready -t`가 docker timeout보다 먼저 끝나야 probe가 스스로 끝난다."""
    probe = _probe()
    assert "-t" in probe, "pg_isready에 `-t`가 없다 — 기본 3초에 기대지 말고 명시한다"
    connect_timeout = float(probe[probe.index("-t") + 1])
    docker_timeout = _seconds(_service()["healthcheck"]["timeout"])
    assert connect_timeout < docker_timeout, (
        f"pg_isready -t {connect_timeout}s가 docker timeout {docker_timeout}s보다 짧지 않다."
    )


def test_the_shared_postgres_gets_time_to_shut_down_cleanly() -> None:
    grace = _service().get("stop_grace_period")
    assert grace is not None, f"`{_SERVICE}`에 stop_grace_period가 없다 — docker 기본 10초"
    assert _seconds(grace) >= _MIN_STOP_GRACE_SECONDS


def test_the_shared_postgres_has_room_for_parallel_query_shared_memory() -> None:
    """docker 기본 /dev/shm 64MB는 병렬 질의의 dynamic shared memory가 넘친다."""
    assert _service().get("shm_size"), f"`{_SERVICE}`에 shm_size가 없다(기본 64MB)"
