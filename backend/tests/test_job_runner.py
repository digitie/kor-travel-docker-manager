"""job runner 계약 테스트 (KUM-M9).

이 레지스트리는 **권위가 아니다** — 무엇이 실제로 남았는지는 디스크의 manifest가
말한다. 그래서 여기서 지키려는 것은 "진행 표시가 거짓말하지 않는가" 하나다:
실행 중인 job이 사라지지 않고, 실패가 조용히 성공으로 보이지 않으며, 한 role의
job id로 다른 role을 들여다볼 수 없어야 한다.

레포에 `pytest-asyncio`가 없으므로 시나리오를 `asyncio.run`으로 직접 돌린다.
모듈 싱글턴 대신 테스트마다 새 `JobRunner`를 만들어, 한 테스트의 running 기록이
다른 테스트로 새지 않게 한다.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from kor_travel_docker_manager.services.job_runner import (
    JobConflictError,
    JobNotFoundError,
    JobRunner,
)


def _ok() -> dict[str, Any]:
    return {"role": "geo"}


def test_a_submitted_job_runs_and_records_its_result() -> None:
    async def scenario() -> None:
        runner = JobRunner()
        record = await runner.submit(kind="db_backup_create", key="geo", run=_ok)

        assert record.state == "running"
        assert record.finished_at_unix is None

        await asyncio.sleep(0.05)
        finished = runner.get(record.job_id, kind="db_backup_create", key="geo")
        assert finished.state == "succeeded"
        assert finished.result == {"role": "geo"}
        assert finished.error is None

    asyncio.run(scenario())


def test_a_failure_is_recorded_as_failed_not_lost() -> None:
    async def scenario() -> None:
        runner = JobRunner()

        def boom() -> dict[str, Any]:
            raise RuntimeError("pg_dump exited 1")

        record = await runner.submit(kind="db_backup_create", key="geo", run=boom)
        await asyncio.sleep(0.05)

        finished = runner.get(record.job_id, kind="db_backup_create", key="geo")
        assert finished.state == "failed"
        assert finished.result is None
        assert "pg_dump exited 1" in (finished.error or "")

    asyncio.run(scenario())


def test_the_returned_record_is_a_snapshot_not_the_live_object() -> None:
    """호출자가 직렬화하는 사이에 worker가 같은 레코드를 완료로 바꿀 수 있다."""

    async def scenario() -> None:
        runner = JobRunner()
        record = await runner.submit(kind="db_backup_create", key="geo", run=_ok)
        await asyncio.sleep(0.05)

        assert record.state == "running"
        assert runner.get(record.job_id, kind="db_backup_create", key="geo").state == (
            "succeeded"
        )

    asyncio.run(scenario())


def test_the_same_key_cannot_run_twice_at_once() -> None:
    async def scenario() -> None:
        runner = JobRunner()
        release = threading.Event()

        def blocked() -> dict[str, Any]:
            release.wait(timeout=5)
            return {}

        first = await runner.submit(kind="db_backup_create", key="geo", run=blocked)
        try:
            with pytest.raises(JobConflictError, match="already running for geo"):
                await runner.submit(kind="db_backup_create", key="geo", run=_ok)
            # 다른 key는 막지 않는다 — 단일 비행은 role별이다.
            await runner.submit(kind="db_backup_create", key="pinvi", run=_ok)
        finally:
            release.set()
            await asyncio.sleep(0.05)
        assert runner.get(first.job_id, kind="db_backup_create", key="geo").state != (
            "running"
        )

    asyncio.run(scenario())


def test_the_running_ceiling_protects_the_shared_thread_pool() -> None:
    """4시간짜리 dump 하나가 기본 executor 스레드를 4시간 붙든다."""

    async def scenario() -> None:
        runner = JobRunner(max_running=1)
        release = threading.Event()

        def blocked() -> dict[str, Any]:
            release.wait(timeout=5)
            return {}

        await runner.submit(kind="db_backup_create", key="geo", run=blocked)
        try:
            with pytest.raises(JobConflictError, match="too many jobs"):
                await runner.submit(kind="db_backup_create", key="pinvi", run=_ok)
        finally:
            release.set()
            await asyncio.sleep(0.05)

    asyncio.run(scenario())


def test_a_job_id_from_another_key_is_not_found() -> None:
    """role 간 정보 유출 경로를 만들지 않는다."""

    async def scenario() -> None:
        runner = JobRunner()
        record = await runner.submit(kind="db_backup_create", key="geo", run=_ok)
        await asyncio.sleep(0.05)

        with pytest.raises(JobNotFoundError):
            runner.get(record.job_id, kind="db_backup_create", key="pinvi")
        with pytest.raises(JobNotFoundError):
            runner.get(record.job_id, kind="something_else", key="geo")

    asyncio.run(scenario())


def test_latest_lets_a_reloaded_page_reattach() -> None:
    async def scenario() -> None:
        runner = JobRunner()
        assert runner.latest(kind="db_backup_create", key="geo") is None

        record = await runner.submit(kind="db_backup_create", key="geo", run=_ok)
        await asyncio.sleep(0.05)

        latest = runner.latest(kind="db_backup_create", key="geo")
        assert latest is not None and latest.job_id == record.job_id
        assert runner.latest(kind="db_backup_create", key="pinvi") is None

    asyncio.run(scenario())


def test_eviction_never_drops_a_running_job() -> None:
    """진행 중에 기록이 사라지면 폴링이 404가 되고 운영자는 결과를 볼 수 없다."""

    async def scenario() -> None:
        runner = JobRunner(max_finished=1)
        release = threading.Event()

        def blocked() -> dict[str, Any]:
            release.wait(timeout=5)
            return {}

        running = await runner.submit(kind="db_backup_create", key="geo", run=blocked)
        try:
            for index in range(4):
                await runner.submit(
                    kind="db_backup_create", key=f"filler{index}", run=_ok
                )
                await asyncio.sleep(0.03)
            assert (
                runner.get(running.job_id, kind="db_backup_create", key="geo").state
                == "running"
            )
        finally:
            release.set()
            await asyncio.sleep(0.05)

    asyncio.run(scenario())


def test_finished_records_expire_by_ttl() -> None:
    async def scenario() -> None:
        runner = JobRunner(finished_ttl_seconds=0)
        first = await runner.submit(kind="db_backup_create", key="geo", run=_ok)
        await asyncio.sleep(0.05)
        # 다음 완료가 만료 청소를 돌린다.
        await runner.submit(kind="db_backup_create", key="pinvi", run=_ok)
        await asyncio.sleep(0.05)

        with pytest.raises(JobNotFoundError):
            runner.get(first.job_id, kind="db_backup_create", key="geo")

    asyncio.run(scenario())


def test_shutdown_refuses_new_jobs_and_does_not_cancel_running_work() -> None:
    """취소는 pg_dump를 멈추지 못하고 기록만 잃는다."""

    async def scenario() -> None:
        runner = JobRunner()
        release = threading.Event()
        finished: list[float] = []

        def blocked() -> dict[str, Any]:
            release.wait(timeout=5)
            finished.append(time.monotonic())
            return {}

        record = await runner.submit(kind="db_backup_create", key="geo", run=blocked)
        drain = asyncio.create_task(runner.shutdown())
        await asyncio.sleep(0.05)

        with pytest.raises(JobConflictError, match="shutting down"):
            await runner.submit(kind="db_backup_create", key="pinvi", run=_ok)

        release.set()
        await drain
        await asyncio.sleep(0.05)
        assert finished, "shutdown이 실행 중 작업을 중단시키면 안 된다"
        assert runner.get(record.job_id, kind="db_backup_create", key="geo").state == (
            "succeeded"
        )

    asyncio.run(scenario())


def test_reset_refuses_while_a_job_is_running() -> None:
    async def scenario() -> None:
        runner = JobRunner()
        release = threading.Event()

        def blocked() -> dict[str, Any]:
            release.wait(timeout=5)
            return {}

        await runner.submit(kind="db_backup_create", key="geo", run=blocked)
        try:
            with pytest.raises(RuntimeError, match="while a job is running"):
                runner.reset()
        finally:
            release.set()
            await asyncio.sleep(0.05)
        runner.reset()
        assert runner.latest(kind="db_backup_create", key="geo") is None

    asyncio.run(scenario())
