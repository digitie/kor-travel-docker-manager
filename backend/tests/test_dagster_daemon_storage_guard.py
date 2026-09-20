"""daemon이 **잘못된 instance storage 위에서 뜨지 않는다**는 것을 결박한다.

경위. PinVi의 webserver와 daemon은 같은 이미지를 argv만 달리해 돌리지만 서로
**다른 컨테이너**이고 `/opt/pinvi/.dagster`에는 볼륨이 없다. 그래서 그 이미지에
`apps/etl/dagster.yaml`이 없거나 `dagster-postgres`가 빠져 있으면 둘은 각자
자기 컨테이너 안에 SQLite를 만든다. 그 상태는 **실패로 보이지 않는다** —
webserver는 healthy이고 daemon도 healthy인데, daemon은 자기 빈 DB를 dequeue하고
webserver에서 띄운 run은 QUEUED인 채로 영원히 남는다. daemon이 아예 없던
이전보다 나쁘면서 겉보기는 더 멀쩡하다.

그래서 daemon의 command 앞에 기동 전제조건을 붙였다. 이 검사가 보는 것은
**그 전제조건의 효과**다 — compose에서 그 python 원문을 꺼내 실제로 실행하고,
storage가 Postgres가 아닌 instance에서 0이 아닌 코드로 죽는지, Postgres인
instance에서는 통과하는지를 양쪽으로 센다. 문자열 포함을 세면 전제조건이
아무것도 안 하도록 바뀌어도 초록이다.

`dagster`는 이 저장소의 의존이 아니므로, 검사는 **가짜 `dagster` 모듈**을
`PYTHONPATH`에 올려 실행한다. 가짜가 돌려주는 것은 storage 객체의 클래스 이름뿐이고
전제조건이 보는 것도 그것뿐이라, 이 대역은 실제 판정과 같은 것을 잰다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_COMPOSE: Final = _REPO_ROOT / "docker-compose.yml"

#: 전제조건을 실은 command의 모양. `sh -ec <script>`의 script 안에서 python이 먼저
#: 돌고, 통과해야 `exec dagster-daemon`으로 넘어간다.
_GUARDED_SHELL: Final = ("sh", "-ec")
_HANDOFF: Final = "exec dagster-daemon"
_PYTHON_INLINE_OPEN: Final = "python -c '"

#: **Map daemon은 이 형태를 쓸 수 없다.** Map의 이미지 entrypoint
#: (`docker/dagster-entrypoint.sh`)가 production profile에서 argv를 문자 단위로
#: 강제하고 허용 모양이 정확히 셋뿐이라, `sh -ec ...`는 그 자리에서 거절된다.
#: Map은 대신 permit으로 게이트된 one-shot storage 마이그레이션이 같은 일을 한다.
#: geo는 instance storage를 env DSN(`KTG_DAGSTER_PG_URL`)으로 이미 선언한다(T-290b부터,
#: PinVi처럼 나중에 추가된 게 아니다) — 그런데도 이 전제조건 메커니즘 자체는 아직 붙어
#: 있지 않다. PinVi를 다치게 한 것과 같은 이미지/compose 순서 리스크가 이론상 geo에도
#: 똑같이 적용될 수 있는 미해결 gap이다(T-307 code-server 분리 작업 중 발견, 별도 후속
#: 필요 — 이 PR의 범위 밖).
#:
#: 이 표는 **면제 목록이 아니라 이유의 목록**이다. 여기 이름을 더하려면 그 서비스가
#: 어떤 다른 기계로 같은 것을 보장하는지 적어야 한다.
_NOT_GUARDED_BY_COMMAND: Final = {
    "kor-travel-map-dagster-daemon": "이미지 entrypoint가 argv를 봉인한다(permit one-shot이 대신 본다)",
    "kor-travel-geo-dagster-daemon": (
        "instance storage를 env DSN(KTG_DAGSTER_PG_URL)으로 이미 선언하지만, 이 전제조건 "
        "메커니즘 자체는 아직 붙어 있지 않다 — T-307에서 발견한 미해결 gap, 후속 필요"
    ),
    "kor-travel-weather-dagster-daemon": (
        "instance storage를 env DSN(DAGSTER_POSTGRES_URL)으로 이미 선언하지만, 이 전제조건 "
        "메커니즘 자체는 아직 붙어 있지 않다 — geo와 같은 미해결 gap(ADR-47, weather "
        "internal-target 전환), 후속 필요"
    ),
}

#: 가짜 dagster 모듈. 전제조건이 만지는 표면만 갖는다.
_FAKE_DAGSTER: Final = '''
import os


class _Storage:
    pass


def _storage(name):
    return type(name, (_Storage,), {})()


class DagsterInstance:
    def __init__(self, flavour):
        self.run_storage = _storage(flavour + "RunStorage")
        self.event_log_storage = _storage(flavour + "EventLogStorage")
        self.schedule_storage = _storage(flavour + "ScheduleStorage")

    @classmethod
    def get(cls):
        return cls(os.environ["FAKE_DAGSTER_STORAGE_FLAVOUR"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
'''


def _compose() -> dict[str, Any]:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _daemon_services() -> dict[str, list[str]]:
    """`dagster-daemon`을 실행하는 서비스. **이름 목록에서 찾지 않는다.**"""

    found: dict[str, list[str]] = {}
    for name, service in _compose()["services"].items():
        command = service.get("command")
        if not isinstance(command, list):
            continue
        text = " ".join(str(part) for part in command)
        if "dagster-daemon" in text:
            found[name] = [str(part) for part in command]
    return found


def _guard_source(command: list[str]) -> str:
    """command에 실린 기동 전제조건의 python 원문을 꺼낸다."""

    script = command[2]
    start = script.index(_PYTHON_INLINE_OPEN) + len(_PYTHON_INLINE_OPEN)
    end = script.index("'", start)
    return script[start:end]


def _guarded_daemons() -> dict[str, str]:
    """전제조건을 실은 daemon과 그 전제조건 원문."""

    guarded: dict[str, str] = {}
    for name, command in _daemon_services().items():
        if tuple(command[:2]) != _GUARDED_SHELL or len(command) < 3:
            continue
        if _HANDOFF not in command[2] or _PYTHON_INLINE_OPEN not in command[2]:
            continue
        guarded[name] = _guard_source(command)
    return guarded


def _run_guard(source: str, flavour: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    (tmp_path / "dagster.py").write_text(_FAKE_DAGSTER, encoding="utf-8")
    return subprocess.run(  # noqa: S603 - 고정 인터프리터, compose에서 꺼낸 원문
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "PATH": "",
            "PYTHONPATH": str(tmp_path),
            "FAKE_DAGSTER_STORAGE_FLAVOUR": flavour,
        },
        timeout=60,
    )


def test_every_dagster_daemon_is_accounted_for() -> None:
    """가드가 있는 daemon과 **왜 없는지 적힌** daemon의 합이 전체여야 한다."""

    daemons = set(_daemon_services())
    assert daemons, "compose에서 dagster-daemon 서비스를 하나도 못 찾았다 — 추출이 낡았다."
    unexplained = daemons - set(_guarded_daemons()) - set(_NOT_GUARDED_BY_COMMAND)
    assert not unexplained, (
        "instance storage를 검사하지 않고 뜨는 dagster daemon이 있다. command에 "
        "기동 전제조건을 붙이거나, 다른 기계가 같은 것을 보장한다면 그 이유를 "
        f"`_NOT_GUARDED_BY_COMMAND`에 적을 것: {sorted(unexplained)}"
    )
    stale = set(_NOT_GUARDED_BY_COMMAND) - daemons
    assert not stale, f"면제 표에 이제 없는 서비스가 남아 있다: {sorted(stale)}"


def test_the_guard_extraction_is_not_vacuous() -> None:
    """가드를 실제로 하나 이상 꺼내야 한다 — 0개면 아래 두 검사가 항진명제다."""

    guarded = _guarded_daemons()
    assert "pinvi-dagster-daemon" in guarded, (
        "PinVi daemon에서 기동 전제조건이 사라졌다. 이 daemon은 webserver와 같은 "
        "이미지를 쓰면서 볼륨 없는 `DAGSTER_HOME`을 보므로, 전제조건이 없으면 낡은 "
        "이미지에서 **조용히** 두 번째 instance가 생긴다."
    )
    source = guarded["pinvi-dagster-daemon"]
    assert "DagsterInstance" in source and source.strip(), "전제조건 원문이 비었다."


@pytest.mark.parametrize("service", sorted(_guarded_daemons()))
def test_guard_refuses_a_non_postgres_instance(service: str, tmp_path: Path) -> None:
    """storage가 SQLite면 daemon으로 넘어가지 않는다(fail-close)."""

    result = _run_guard(_guarded_daemons()[service], "Sqlite", tmp_path)
    assert result.returncode != 0, (
        f"{service}의 전제조건이 SQLite instance를 통과시켰다 — "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    message = result.stderr
    for storage in ("SqliteRunStorage", "SqliteEventLogStorage", "SqliteScheduleStorage"):
        assert storage in message, (
            f"{service}의 거절 문구가 {storage}를 말하지 않는다 — 어느 storage가 "
            f"잘못됐는지 모르면 진단이 안 된다: {message!r}"
        )


@pytest.mark.parametrize("service", sorted(_guarded_daemons()))
def test_guard_admits_a_postgres_instance(service: str, tmp_path: Path) -> None:
    """정상 instance에서는 통과해야 한다 — 늘 거절하는 가드는 가드가 아니다."""

    result = _run_guard(_guarded_daemons()[service], "Postgres", tmp_path)
    assert result.returncode == 0, (
        f"{service}의 전제조건이 Postgres instance를 거절했다 — "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
