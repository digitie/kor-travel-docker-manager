"""통합 시험이 남긴 격리 compose 프로젝트 잔재 회수.

docker 통합 시험(`ktdm-<kind>-<pid>-<suffix>` 프로젝트)은 fixture `finally`에서 `compose down --volumes`로 치운다. 그러나
pytest가 SIGKILL·외부 timeout으로 죽으면 `finally`가 돌지 않아 컨테이너(공용 PG 대역은 PGDATA 익명 볼륨까지)가 남는다 —
n150에 12일째 Up인 `ktdm-dagsterplane-4186004-*`가 그렇게 남았다(2026-10-16).

그래서 각 fixture가 **자기 프로젝트를 만들기 전에** 같은 접두사의 **죽은 실행**이 남긴 것을 회수한다. 판정은 이름의
pid가 이 호스트에서 살아 있지 않은가(`/proc/<pid>`) 하나다 — 다른 clone·세션의 **진행 중** 실행은 pid가 살아 있어 건드리지
않는다. 대상은 compose 프로젝트 label이 그 접두사로 시작하는 컨테이너·네트워크·볼륨과 그 실행이 만든 `<project>:it`
이미지뿐이다(운영 컨테이너는 이 label을 갖지 않는다). `/proc`이 없는 호스트에서는 아무것도 하지 않는다.
"""

from __future__ import annotations

import os
import subprocess
import warnings
from collections.abc import Callable
from pathlib import Path

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

_LABEL = "com.docker.compose.project"
_KINDS = (
    (("ps", "--all", "--quiet"), ("rm", "--force", "--volumes")),
    (("network", "ls", "--quiet"), ("network", "rm")),
    (("volume", "ls", "--quiet"), ("volume", "rm", "--force")),
)


def _docker(run: Runner, *arguments: str) -> subprocess.CompletedProcess[str]:
    """시간 초과는 경고만 남기고 빈 결과로 넘어간다 — 정리 실패가 fixture를 에러로 만들지 않는다."""
    try:
        return run(["docker", *arguments], capture_output=True, text=True, check=False, timeout=120)
    except subprocess.TimeoutExpired:
        warnings.warn(f"docker {' '.join(arguments[:2])} timed out while sweeping test residue", stacklevel=2)
        return subprocess.CompletedProcess(["docker", *arguments], 124, stdout="", stderr="")


def _local_daemon() -> bool:
    """pid를 이 호스트의 `/proc`로 판정하므로 데몬도 이 호스트의 것이어야 한다 — `DOCKER_HOST`가 원격(로컬 unix 소켓이
    아닌 것)이거나 기본이 아닌 docker context면 건너뛴다(다른 호스트·컨테이너의 실행을 지우지 않게)."""
    host = os.environ.get("DOCKER_HOST", "").strip()
    if host and not host.startswith("unix://"):
        return False
    context = os.environ.get("DOCKER_CONTEXT", "").strip()
    return context in {"", "default"}


def _projects(run: Runner) -> set[str]:
    found: set[str] = set()
    for listing in (("ps", "--all"), ("network", "ls"), ("volume", "ls")):
        listed = _docker(run, *listing, "--format", "{{.Label \"" + _LABEL + "\"}}")
        if listed.returncode == 0:
            found.update(line.strip() for line in listed.stdout.splitlines() if line.strip())
    return found


def sweep_dead_runs(prefix: str, *, run: Runner = subprocess.run, proc: Path = Path("/proc")) -> list[str]:
    """`prefix<pid>-…` 프로젝트 중 pid가 죽은 것을 지우고 그 이름들을 돌려준다(시험 시작 전 정리용)."""
    if not (proc / "self").exists() or not _local_daemon():
        return []
    removed: list[str] = []
    for project in sorted(_projects(run)):
        if not project.startswith(prefix):
            continue
        pid = project[len(prefix):].split("-", 1)[0]
        if not (pid.isascii() and pid.isdigit()) or (proc / pid).exists():
            continue
        label = f"label={_LABEL}={project}"
        for listing, removal in _KINDS:
            ids = _docker(run, *listing, "--filter", label).stdout.split()
            if ids:
                _docker(run, *removal, *ids)
        _docker(run, "image", "rm", "--force", f"{project}:it")
        removed.append(project)
    return removed
