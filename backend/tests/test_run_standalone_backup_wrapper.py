"""cron이 부르는 `scripts/run-standalone-backup.sh`의 주기 백업 허용 목록을 **실행해서** 본다.

wrapper의 `case "$ROLE" in ...)`는 role 정본(`BACKUP_ROLES`)과 따로 적힌 허용 목록이다.
role을 추가하고 이 줄을 빠뜨리면 CLI·UI에는 role이 보이는데 cron은 매번 `exit 2`로
끝난다 — 로그 한 줄 말고는 아무도 모른다(`docs/shared-postgres-onboarding.md` §6.4).
그래서 줄을 grep하지 않고, 가짜 `ktdctl`을 끼워 wrapper를 실제로 돌려 **무엇을 불렀는지**를
본다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from kor_travel_docker_manager.services.standalone_backup import BACKUP_ROLES

_WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "run-standalone-backup.sh"

#: cron 주기 백업 대상. **이 집합이 정책이다.** geo application은 kor-travel-geo 앱 레벨
#: 백업이, Map은 kor-travel-map #148이 소유한다. transport는 2026-09-28 오너 결정으로
#: transport 저장소의 자체 cron을 대신해 합류했다. `dagster_shared`는 공용 Dagster instance의
#: metadata DB다(platform-topology.md §7 — stage 4가 이 백업의 7일 연속 초록을 전제한다). 옛
#: 프로젝트별 Dagster metadata DB(`geo_dagster`·`transport_dagster`)는 4단계로 막히고 DROP되므로 뺐다.
_PERIODIC = frozenset({"concierge", "pinvi", "transport", "dagster_shared"})


def _run_wrapper(role: str, tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    calls = tmp_path / "ktdctl-calls"
    fake_ktdctl = tmp_path / "ktdctl"
    fake_ktdctl.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\n', encoding="utf-8")
    fake_ktdctl.chmod(0o755)
    completed = subprocess.run(
        ["sh", str(_WRAPPER), role, "3"],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "KTDCTL": str(fake_ktdctl),
            "KTDM_BACKUP_ROOT": str(tmp_path / "backups"),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return completed, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


@pytest.mark.parametrize("role", sorted(set(BACKUP_ROLES) | _PERIODIC))
def test_wrapper_backs_up_exactly_the_periodic_roles(role: str, tmp_path: Path) -> None:
    completed, calls = _run_wrapper(role, tmp_path)

    if role in _PERIODIC:
        assert completed.returncode == 0, completed.stderr
        assert calls == [f"db-backup create {role}", f"db-backup gc {role} --keep 3"]
    else:
        assert completed.returncode == 2
        assert "not enabled" in completed.stderr
        assert calls == []


def test_every_periodic_role_is_a_backup_role() -> None:
    """허용 목록에만 있고 정본에 없는 role은 CLI가 `invalid choice`로 거부한다."""

    assert _PERIODIC <= set(BACKUP_ROLES)
