"""crontab이 **직접 실행**하라고 적힌 스크립트는 git에 실행 비트가 있어야 한다.

2026-09-12부터 5일간 `geo_dagster`·`concierge`·`pinvi` standalone 백업이 전부 실패했다.
로그에 남은 것은 이 한 줄뿐이다:

    /bin/sh: 1: .../scripts/run-standalone-backup.sh: Permission denied

원인은 배포가 실행 비트를 "벗긴" 것이 아니었다 — **git이 처음부터 그 비트를 갖고 있지
않았다**(`100644`). 그래서 새 체크아웃마다 실행 불가 파일이 만들어지고, crontab은 그
경로를 **직접** 실행하므로 즉시 죽는다. 2026-09-16에 host에서 `chmod +x`로 고쳤지만
그것은 작업 트리 한 곳의 국소 수정이라 **다음 체크아웃이 조용히 되돌린다.** 같은 사고가
같은 방식으로 다시 난다.

그래서 이 검사는 탐지가 아니라 **예방**이다. 백업이 멈춘 것을 빨리 아는 것보다, 멈출 수
없게 만드는 것이 낫다.

**목록을 손으로 들고 있지 않는다.** 스크립트 자신이 헤더에 "이 줄을 crontab에 넣어라"고
적고 그 줄에 **자기 경로**를 쓰는 것을 근거로 삼는다. 주장을 하는 쪽과 검사받는 쪽이
같은 파일이므로, 새 wrapper가 같은 관행을 따라 들어오면 아무도 이 파일을 고치지 않아도
자동으로 대상이 된다. 반대로 헤더에서 crontab 줄을 지우면 대상에서 빠지는데, 그때는
아래 **하한**이 그것을 잡는다.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"

#: 탐지기가 공허해지는 것을 막는 하한. 이 둘은 crontab 직접 실행이 **문서화된 계약**이고
#: (`docs/docker-management.md`), 그중 하나는 실제로 5일치 사고를 냈다. 탐지기가 이 둘을
#: 못 찾으면 그것은 "대상이 없다"가 아니라 **탐지기가 고장 났다**는 뜻이다.
_MUST_BE_DETECTED: frozenset[str] = frozenset(
    {
        "scripts/run-standalone-backup.sh",
        "scripts/run-offbox-sync.sh",
    }
)

#: 해석기를 앞에 두고 부르면(`sh foo.sh`) 실행 비트가 필요 없다. 그런 줄은 제외한다.
_INTERPRETED = re.compile(r"(?:^|\s)(?:sh|bash|dash|zsh|python3?|env)\s+\S*$")


def _header_comment_lines(path: Path) -> list[str]:
    """첫 비주석 줄 전까지의 주석. shebang은 뺀다."""

    lines: list[str] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        stripped = line.strip()
        if index == 0 and stripped.startswith("#!"):
            continue
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        lines.append(stripped)
    return lines


def _declares_direct_cron_execution(path: Path) -> bool:
    """헤더가 "crontab에 넣어라"고 적고, 그 줄이 **자기 경로**를 직접 부르는가."""

    header = _header_comment_lines(path)
    if not any("crontab" in line for line in header):
        return False
    needle = f"/scripts/{path.name}"
    for line in header:
        position = line.find(needle)
        if position == -1:
            continue
        # 경로 바로 앞이 해석기면 실행 비트가 필요 없다.
        prefix = line[:position].rstrip()
        # 경로 토큰의 시작까지 되짚는다(공백 구분).
        token_start = prefix.rfind(" ")
        before = prefix[: token_start + 1] if token_start != -1 else ""
        if _INTERPRETED.search(before + " x"):
            continue
        return True
    return False


def _git_modes() -> dict[str, str]:
    completed = subprocess.run(
        ["git", "ls-files", "-s", "scripts/"],
        cwd=_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    modes: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        meta, _, name = line.partition("\t")
        modes[name] = meta.split()[0]
    return modes


def test_cron_executed_scripts_carry_the_exec_bit_in_git() -> None:
    """crontab 직접 실행이 문서화된 스크립트는 git 모드가 `100755`여야 한다.

    작업 트리의 권한이 아니라 **git이 기록한 모드**를 본다. 사고의 원인이 정확히
    그 차이였다 — host에서 `chmod +x`를 해도 git이 `100644`를 들고 있으면 다음
    체크아웃이 되돌린다.
    """

    modes = _git_modes()
    detected = {
        f"scripts/{path.name}"
        for path in sorted(_SCRIPTS.iterdir())
        if path.is_file() and _declares_direct_cron_execution(path)
    }

    missing_floor = _MUST_BE_DETECTED - detected
    assert not missing_floor, (
        "탐지기가 알려진 대상을 놓쳤다 — 헤더의 crontab 줄이 지워졌거나 탐지 규칙이"
        f" 깨졌다: {sorted(missing_floor)}"
    )

    not_executable = sorted(name for name in detected if modes.get(name) != "100755")
    assert not not_executable, (
        "crontab이 직접 실행하는데 git 모드가 실행 가능이 아니다 — 새 체크아웃마다"
        " `Permission denied`로 죽는다(2026-09-12~16, 백업 3종 5일 중단).\n"
        f"  대상: {not_executable}\n"
        "  고치는 법: git update-index --chmod=+x <경로>"
    )
