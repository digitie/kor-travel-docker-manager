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

같은 서비스의 D4 튜닝(ADR-53 — Map DB가 이 instance로 오면서 Map의 값을 진다)도 여기서
결박한다: `command:`의 튜닝 목록, 이미지 digest 고정, grace ≥ 120초, `/dev/shm` ≥ 1 GiB.
duration·크기 파서와 서비스 로더는 이 파일 하나다 — 격리 실행 테스트
(`test_shared_postgres_runtime_integration.py`)도 이것을 가져다 쓴다.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_SERVICE = "kor-travel-shared-postgres"

#: docker 기본 10초는 부하 중 fast shutdown의 checkpoint를 못 기다린다(2026-09-25 08:03
#: `systemctl restart docker` → SIGKILL → 재기동이 73GB fsync crash recovery). D4(ADR-53)는
#: 1GB `shared_buffers`의 dirty buffer를 n150 디스크 대기 속에서 내릴 몫으로 120초를 요구한다.
_MIN_STOP_GRACE_SECONDS = 120
#: 64MB `work_mem`의 병렬 hash는 `work_mem × hash_mem_multiplier(2) × (workers+1)`까지
#: dynamic shared memory를 쓴다 — D4 오너 결정은 1 GiB다.
_MIN_SHM_BYTES = 1024**3

_SIZE_UNIT_EXPONENTS = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4, "p": 5}


def _service() -> dict[str, Any]:
    # 중복 키를 거부하는 로더다. `safe_load`는 같은 키의 뒤엣값으로 조용히 덮어써서, 리베이스가
    # `stop_grace_period`·`shm_size`를 두 벌 남겨도 이 파일의 검사는 한 벌만 보고 초록이
    # 된다 — 그 파일은 `docker compose config`가 거부한다.
    document = load_yaml_rejecting_duplicate_keys(_COMPOSE.read_text(encoding="utf-8"))
    service = document["services"][_SERVICE]
    assert isinstance(service, dict)
    return service


_DEFAULTED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")


def compose_default(value: object) -> str:
    """`${VAR:-default}`를 기본값으로 푼다 — root `.env`에 재정의가 없는 호스트(n150)가 실제로 쓰는 값."""
    return _DEFAULTED.sub(lambda match: match.group(2), str(value))


def _seconds(duration: object) -> float:
    """compose duration 문자열(`300s`, `5m`, `1m30s`)을 초로."""
    text = str(duration).strip()
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|us|h|m|s)", text)
    assert parts and "".join(value + unit for value, unit in parts) == text, (
        f"읽을 수 없는 duration: {duration!r}"
    )
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001, "us": 0.000001}
    return sum(float(value) * scale[unit] for value, unit in parts)


def _bytes(size: object) -> int:
    """compose 크기(`1gb`, `512m`, 바이트 정수)를 바이트로 — docker처럼 1024 배수다."""
    if isinstance(size, int) and not isinstance(size, bool):
        return size
    match = re.fullmatch(r"(\d+)([kmgtp]?)b?", str(size).strip().lower())
    assert match, f"읽을 수 없는 크기: {size!r}"
    number, unit = match.groups()
    return int(number) * 1024 ** _SIZE_UNIT_EXPONENTS[unit]


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
    size = _service().get("shm_size")
    assert size is not None, f"`{_SERVICE}`에 shm_size가 없다(기본 64MB)"
    assert _bytes(size) >= _MIN_SHM_BYTES, (
        f"`{_SERVICE}`의 shm_size {size!r}가 D4의 1 GiB보다 작다 — 64MB `work_mem`의 병렬 "
        "hash가 `could not resize shared memory segment`로 죽는 자리다."
    )


def test_the_shared_postgres_command_is_the_d4_tuning() -> None:
    """공용 instance가 Map의 튜닝을 진다(ADR-53, D4) — 순서까지 정확히, **기본값으로 푼 값**으로.

    값마다 출처 규칙이 있다: `listen_addresses`·`-p`는 공용 그대로, preload·autoprewarm·
    `work_mem`·`effective_cache_size`·`max_wal_size`는 Map, `shared_buffers`는 오너 결정
    (1GB), 둘 다 두던 `maintenance_work_mem`은 큰 쪽(Map 256MB), `pg_stat_statements.*`는
    공용. `ALTER SYSTEM`은 쓰지 않는다.

    2026-10-16(ADR-58 R6·결정 8 ⅰ, T-MGR-03): 튜닝 수치는 호스트별 root `.env`(`KTDM_PG_*`)로 바꿀 수 있고 기본값이
    n150 값이다 — 그래서 여기서는 기본값으로 푼 명령을 대조한다. 끝의 여섯 키는 PG 16 서버 기본값이라 n150 효과가 0이다
    (`test_postgres_host_tuning.py`). preload·`pg_stat_statements.*`·`max_locks_per_transaction`(#471, 두 호스트 같은
    리터럴 — 결정 8 ⅰ)은 env로 바꿀 수 없다.
    """
    raw = _service()["command"]
    command = [compose_default(token) if token.startswith(("shared_buffers=", "work_mem=", "maintenance_work_mem=",
               "effective_cache_size=", "random_page_cost=", "max_wal_size=", "max_parallel_workers",
               "effective_io_concurrency=", "checkpoint_timeout=", "wal_compression=", "autovacuum_max_workers="))
               else token for token in raw]
    assert command == [
        "postgres",
        "-c",
        "listen_addresses=127.0.0.1",
        "-p",
        "${KOR_TRAVEL_SHARED_DB_PORT:-11000}",
        "-c",
        "shared_preload_libraries=pg_prewarm,pg_stat_statements",
        "-c",
        "pg_prewarm.autoprewarm=on",
        "-c",
        "shared_buffers=1GB",
        "-c",
        "work_mem=64MB",
        "-c",
        "maintenance_work_mem=256MB",
        "-c",
        "effective_cache_size=1536MB",
        "-c",
        "random_page_cost=1.1",
        "-c",
        "max_wal_size=2GB",
        "-c",
        "max_parallel_workers=8",
        "-c",
        "max_parallel_workers_per_gather=2",
        "-c",
        "effective_io_concurrency=1",
        "-c",
        "checkpoint_timeout=5min",
        "-c",
        "wal_compression=off",
        "-c",
        "autovacuum_max_workers=3",
        "-c",
        "pg_stat_statements.track=all",
        "-c",
        "pg_stat_statements.max=10000",
        "-c",
        "max_locks_per_transaction=256",
    ]
    settings = [raw[index + 1] for index, token in enumerate(raw) if token == "-c"]
    fixed = [s for s in settings if s.startswith(("shared_preload_libraries=", "pg_prewarm.", "pg_stat_statements.",
                                                   "max_locks_per_transaction=", "listen_addresses="))]
    assert fixed and not any("${" in setting for setting in fixed), fixed
    # 바꿀 수 있는 것은 `${KTDM_PG_<KEY>:-default}` 꼴 하나뿐이다(다른 변수·기본값 없는 꼴 금지).
    tunable = [s for s in settings if "${" in s]
    for setting in tunable:
        key, _, value = setting.partition("=")
        assert re.fullmatch(rf"\$\{{KTDM_PG_{key.upper()}:-[^}}]+\}}", value), setting


def test_the_shared_postgres_image_is_digest_pinned() -> None:
    """태그만 두면 recreate나 `docker pull` 한 번이 모든 테넌트의 PostgreSQL을 바꾼다."""
    image = _service()["image"]
    assert isinstance(image, str)
    # 호스트별 이미지(`KTDM_POSTGRES_IMAGE`, ADR-58 결정 9)는 바꿀 수 있지만 기본값(n150)은 digest 핀이다.
    assert re.fullmatch(r"\$\{KTDM_POSTGRES_IMAGE:-[^}]+@sha256:[0-9a-f]{64}\}", image), image


def test_the_compose_duration_and_size_parsers_read_what_compose_writes() -> None:
    """위 하한 검사들의 재료가 틀리면 그 검사가 항진명제가 된다."""
    assert _seconds("300s") == 300
    assert _seconds("5m") == 300
    assert _seconds("1m30s") == 90
    # `docker compose config`(n150 v5.2.0, 운영 env)는 `300s`를 이 모양으로 다시 쓴다.
    assert _seconds("5m0s") == 300
    assert _seconds("119s") < _MIN_STOP_GRACE_SECONDS
    assert _bytes("1gb") == _MIN_SHM_BYTES
    assert _bytes("1g") == _MIN_SHM_BYTES
    assert _bytes("1024MB") == _MIN_SHM_BYTES
    # `docker compose config`는 크기를 바이트 수로 다시 쓴다.
    assert _bytes("1073741824") == _bytes(1073741824) == _MIN_SHM_BYTES
    assert _bytes("512mb") < _MIN_SHM_BYTES
    for malformed in ("", "gb", "1xb", "-1g", None, True):
        with pytest.raises(AssertionError, match="읽을 수 없는 크기"):
            _bytes(malformed)
    for malformed in ("", "300", "5 m", None):
        with pytest.raises(AssertionError, match="읽을 수 없는 duration"):
            _seconds(malformed)
