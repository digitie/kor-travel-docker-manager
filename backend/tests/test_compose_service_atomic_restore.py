"""compose_service.py의 `_atomic_restore_compose_source` 디렉터리 fsync 계약을 고정한다."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import compose_service as compose_service_module


def test_directory_fsync_failure_after_successful_replace_still_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_atomic_restore_compose_source`는 `_recover_persisted_target_runtime`가
    `recovery_succeeded`를 판정하는 유일한 신호원이다 — os.replace 뒤 디렉터리
    fsync가 실패하면(그 rename이 crash에도 살아남는지 확인되지 않았다는 뜻) 이
    함수는 반드시 예외를 propagate해야 한다. GM-10 이관 당시 정본
    `atomic_write_bytes`(디렉터리 fsync 실패를 best-effort로 삼키는
    프리미티브)로 한 차례 바꿨다가 이 신호가 조용히 사라졌던 것을 적대적
    리뷰로 되돌린 회귀 테스트다 — 되돌리기 전에는 이 함수가 정상 반환해
    `_recover_persisted_target_runtime`이 rename의 durability를 확인하지
    못한 채로 `recovery_succeeded=True`를 보고했다.

    실제 디렉터리를 깨지 않고 재현하기 위해 ``os.fsync``를 감싸서, 대상 fd가
    디렉터리인지(``S_ISDIR``)로 구분한다: 파일 자신의 fd에 대한 fsync(성공해야
    함)가 관측된 "이후"의 디렉터리 fsync만 실패시킨다.
    """

    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_bytes(b"services: {}\n")

    real_fsync = os.fsync
    file_fsync_observed = {"value": False}

    def fake_fsync(fd: int) -> None:
        try:
            is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        except OSError:
            is_directory = False
        if not is_directory:
            file_fsync_observed["value"] = True
            real_fsync(fd)
            return
        if file_fsync_observed["value"]:
            raise OSError(
                5, "simulated directory fsync failure (no real directory touched)"
            )
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fake_fsync)

    with pytest.raises(OSError, match="simulated directory fsync failure"):
        compose_service_module._atomic_restore_compose_source(
            compose_path,
            b"services:\n  restored: {}\n",
            mode=0o640,
        )

    # rename 자체는 실제로 일어났다 — 디렉터리 fsync는 그 사실의 durability
    # 확인일 뿐, 이미 끝난 파일 교체 자체를 되돌리지는 않는다. 이 지점부터는
    # 호출자(`_recover_persisted_target_runtime`)가 예외를 보고
    # `recovery_succeeded=False`로 판정할 책임이다.
    assert compose_path.read_bytes() == b"services:\n  restored: {}\n"
