"""`verify-kor-travel-geo-source.sh`가 psql 실패를 정상으로 보고하지 않는지 본다.

종전 본문은 `count="$(table_count "$table" | tr -d '[:space:]')"`였다. 파이프로
넘기면 종료 상태가 `tr`의 것이 되므로 psql의 연결 실패·인증 실패가 **빈
문자열**로만 남고, `case`에 빈 값 분기가 없어 그 빈 값은 `*)`로 떨어졌다.
검증기는 DB에 닿지도 못한 채 `geo source verification complete`를 찍었다 —
"게이트가 있는데 아무것도 막지 않는" 전형이다.

이 파일은 스텁 `psql`/`pg_isready`를 PATH에 올려 스크립트를 **진짜로 실행**한다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verify-kor-travel-geo-source.sh"


def _environment(tmp_path: Path, psql_body: str) -> tuple[dict[str, str], Path]:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "pg_isready").write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    (binaries / "psql").write_text(psql_body, encoding="ascii")
    for name in ("pg_isready", "psql"):
        (binaries / name).chmod(0o755)

    source_dir = tmp_path / "juso"
    source_dir.mkdir()
    (source_dir / "sample.txt").write_text("x", encoding="ascii")

    environment = dict(os.environ)
    environment["PATH"] = f"{binaries}:{environment.get('PATH', '')}"
    environment["KOR_TRAVEL_GEO_SOURCE_DIR"] = str(source_dir)
    environment["POSTGRES_WAIT_RETRIES"] = "1"
    return environment, source_dir


def _run(tmp_path: Path, psql_body: str) -> subprocess.CompletedProcess[str]:
    environment, _ = _environment(tmp_path, psql_body)
    return subprocess.run(
        ["/bin/sh", str(_SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.parametrize(
    "psql_body",
    [
        # 연결 실패: 상태만 비정상이고 stdout은 비어 있다.
        "#!/bin/sh\nexit 2\n",
        # 인증 실패처럼 stderr만 있고 stdout이 빈 경우.
        '#!/bin/sh\necho "fatal: password authentication failed" >&2\nexit 1\n',
    ],
)
def test_a_failing_psql_is_never_reported_as_complete(
    tmp_path: Path, psql_body: str
) -> None:
    completed = _run(tmp_path, psql_body)
    assert completed.returncode != 0, completed.stdout
    assert "geo source verification complete" not in completed.stdout


def test_unreadable_output_is_never_reported_as_complete(tmp_path: Path) -> None:
    """상태는 0인데 숫자가 아닌 출력도 통과시키지 않는다."""
    completed = _run(tmp_path, '#!/bin/sh\necho "ERROR"\nexit 0\n')
    assert completed.returncode != 0, completed.stdout
    assert "geo source verification complete" not in completed.stdout


def test_a_loaded_database_passes(tmp_path: Path) -> None:
    completed = _run(tmp_path, "#!/bin/sh\necho 42\nexit 0\n")
    assert completed.returncode == 0, completed.stderr
    assert "geo source verification complete" in completed.stdout


def test_a_missing_table_still_fails(tmp_path: Path) -> None:
    """종전에도 잡던 경로가 그대로 잡히는지 확인한다(회귀 방지)."""
    completed = _run(tmp_path, "#!/bin/sh\necho -1\nexit 0\n")
    assert completed.returncode != 0
    assert "missing" in completed.stdout


def test_an_empty_table_still_fails(tmp_path: Path) -> None:
    completed = _run(tmp_path, "#!/bin/sh\necho 0\nexit 0\n")
    assert completed.returncode != 0
    assert "empty" in completed.stdout
