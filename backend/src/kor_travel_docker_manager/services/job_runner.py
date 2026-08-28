"""UI가 시작한 장기 mutation의 프로세스-로컬 job 레지스트리.

현재 유일한 사용처는 standalone backup create다 — 기본 timeout이 14,400초(4시간)라
HTTP 요청 수명에 묶을 수 없다. 4시간짜리 요청은 어떤 reverse proxy도 끊고, 브라우저
탭 하나가 백업의 수명을 쥐게 만든다.

**단일 프로세스 전제**: 레지스트리는 메모리다. uvicorn을 `--workers 2` 이상으로 띄우면
폴링 요청이 다른 worker에 닿아 404가 난다. 운영 기동은 worker 1개이므로 성립하지만,
worker를 늘리려면 이 모듈부터 durable store로 바꿔야 한다.

**여기 남는 기록은 권위가 아니다.** 프로세스가 죽으면 job 기록은 사라진다. 무엇이
실제로 남았는지의 권위는 언제나 디스크의 manifest이고 `GET /api/v1/backups`가 그것을
읽는다. 그래서 job 기록의 소실은 데이터 손실이 아니라 진행 표시의 손실이다.

**재기동 위험**: `_role_lock`은 이 프로세스가 쥔 파일 디스크립터의 `flock`이다. 프로세스가
종료되면 락은 풀리지만 컨테이너 안의 `pg_dump`는 계속 돈다. 따라서 UI가 시작한 백업이
도는 동안 backend를 재기동하면 같은 DB에 두 번째 `pg_dump`가 붙을 수 있다 —
`create_standalone_backup`의 docstring이 경고하는 바로 그 이중 부하다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

JobState = Literal["running", "succeeded", "failed"]

MAX_FINISHED_JOBS = 32
FINISHED_JOB_TTL_SECONDS = 24 * 60 * 60
MAX_RUNNING_JOBS = 2
SHUTDOWN_DRAIN_SECONDS = 5.0
_MAX_ERROR_LENGTH = 2000


class JobConflictError(RuntimeError):
    """같은 (kind, key)가 이미 실행 중이거나 동시 실행 상한을 넘었다."""


class JobNotFoundError(LookupError):
    """job id가 없거나 다른 (kind, key)에 속한다."""


@dataclass
class JobRecord:
    job_id: str
    kind: str
    key: str
    state: JobState
    started_at_unix: int
    finished_at_unix: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "key": self.key,
            "state": self.state,
            "started_at_unix": self.started_at_unix,
            "finished_at_unix": self.finished_at_unix,
            "result": self.result,
            "error": self.error,
        }


class JobRunner:
    def __init__(
        self,
        *,
        max_finished: int = MAX_FINISHED_JOBS,
        finished_ttl_seconds: int = FINISHED_JOB_TTL_SECONDS,
        max_running: int = MAX_RUNNING_JOBS,
    ) -> None:
        # asyncio.Lock이 아니라 threading.Lock이다 — 레코드는 event loop와 worker
        # thread 양쪽에서 만져지고, asyncio.Lock은 최초 await에서 루프에 결박돼
        # TestClient가 루프를 갈아끼우면 재사용할 수 없다.
        self._guard = threading.Lock()
        self._jobs: dict[str, JobRecord] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False
        self._max_finished = max_finished
        self._finished_ttl_seconds = finished_ttl_seconds
        self._max_running = max_running

    async def submit(
        self, *, kind: str, key: str, run: Callable[[], dict[str, Any]]
    ) -> JobRecord:
        """job을 시작하고 **복사본**을 돌려준다.

        복사본인 이유는 호출자가 직렬화하는 사이에 worker thread가 같은 레코드를
        완료 상태로 바꿀 수 있기 때문이다.
        """

        with self._guard:
            if self._closed:
                raise JobConflictError("the job runner is shutting down")
            running = [record for record in self._jobs.values() if record.state == "running"]
            if any(record.kind == kind and record.key == key for record in running):
                raise JobConflictError(f"a {kind} job is already running for {key}")
            # asyncio.to_thread는 loop의 **기본 executor**를 쓴다. 상태 브로드캐스트와
            # 메트릭 수집이 같은 풀을 공유하므로, 4시간짜리 dump가 스레드를 4시간 붙든다.
            if len(running) >= self._max_running:
                raise JobConflictError(f"too many jobs are running ({self._max_running})")
            record = JobRecord(
                job_id=str(uuid.uuid4()),
                kind=kind,
                key=key,
                state="running",
                started_at_unix=int(time.time()),
            )
            self._jobs[record.job_id] = record
            snapshot = dataclasses.replace(record)
        try:
            task = asyncio.create_task(self._supervise(record.job_id, kind, key, run))
        except BaseException:
            # task를 못 만들면 running 기록만 남는다. 그 기록은 어떤 축출 규칙으로도
            # 사라지지 않으므로(실행 중은 보존이 원칙), 그 role은 프로세스가 죽을
            # 때까지 409만 돌려준다. 만들지 못했으면 기록도 되돌린다.
            with self._guard:
                self._jobs.pop(record.job_id, None)
            raise
        with self._guard:
            self._tasks[record.job_id] = task
        return snapshot

    async def _supervise(
        self, job_id: str, kind: str, key: str, run: Callable[[], dict[str, Any]]
    ) -> None:
        try:
            result = await asyncio.to_thread(run)
        except asyncio.CancelledError:
            # 취소를 기록하지 않으면 영원히 running으로 남아 single-flight를 막는다.
            self._finish(job_id, state="failed", error="cancelled")
            raise
        except BaseException as exc:  # noqa: BLE001 - 어떤 실패도 job 상태로 남긴다
            self._finish(
                job_id,
                state="failed",
                error=f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_LENGTH],
            )
            logger.exception("job %s (%s/%s) failed", job_id, kind, key)
        else:
            self._finish(job_id, state="succeeded", result=result)
        finally:
            with self._guard:
                self._tasks.pop(job_id, None)

    def _finish(
        self,
        job_id: str,
        *,
        state: JobState,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._guard:
            record = self._jobs.get(job_id)
            if record is None:
                return
            record.state = state
            record.finished_at_unix = int(time.time())
            record.result = result
            record.error = error
            self._evict_locked()

    def _evict_locked(self) -> None:
        """실행 중인 job은 **절대** 버리지 않는다 — 진행 중에 사라지면 폴링이 404가 된다."""

        now = int(time.time())
        # 타임스탬프가 초 단위이므로 경계는 `<=`다 — TTL이 N초면 "N초 전에 끝난 것"이
        # 만료다. `<`로 두면 TTL 0이 아무것도 버리지 않는 무의미한 값이 된다.
        expired = [
            job_id
            for job_id, record in self._jobs.items()
            if record.state != "running"
            and record.finished_at_unix is not None
            and record.finished_at_unix <= now - self._finished_ttl_seconds
        ]
        for job_id in expired:
            del self._jobs[job_id]
        while True:
            finished = [
                (job_id, record)
                for job_id, record in self._jobs.items()
                if record.state != "running"
            ]
            if len(finished) <= self._max_finished:
                return
            oldest = min(finished, key=lambda item: item[1].finished_at_unix or 0)
            del self._jobs[oldest[0]]

    def get(self, job_id: str, *, kind: str, key: str) -> JobRecord:
        """(kind, key)가 어긋나면 404다 — role 간 정보 유출 경로를 만들지 않는다."""

        with self._guard:
            record = self._jobs.get(job_id)
            if record is None or record.kind != kind or record.key != key:
                raise JobNotFoundError(job_id)
            return dataclasses.replace(record)

    def latest(self, *, kind: str, key: str) -> JobRecord | None:
        """페이지를 새로 고치면 job id를 잃는다 — 다시 붙을 수 있게 해 준다."""

        with self._guard:
            matches = [
                record
                for record in self._jobs.values()
                if record.kind == kind and record.key == key
            ]
            if not matches:
                return None
            return dataclasses.replace(matches[-1])

    async def shutdown(self) -> None:
        """진행 중 job을 **취소하지 않는다.**

        `asyncio.to_thread`는 실행 중인 `subprocess.run`을 중단시키지 못한다. 취소는
        대기 중인 task만 떼어 내고 기록을 없앨 뿐, `pg_dump`는 계속 돈다. 짧게 배수한 뒤
        남은 것은 경고로 남긴다.
        """

        with self._guard:
            self._closed = True
            pending = list(self._tasks.values())
            running = [
                record for record in self._jobs.values() if record.state == "running"
            ]
        if pending:
            await asyncio.wait(pending, timeout=SHUTDOWN_DRAIN_SECONDS)
        for record in running:
            with self._guard:
                current = self._jobs.get(record.job_id)
            if current is None or current.state != "running":
                continue
            logger.warning(
                "job %s (%s/%s) is still running at shutdown; the underlying work may "
                "continue and its role lock is released when this process exits",
                record.job_id,
                record.kind,
                record.key,
            )

    def reset(self) -> None:
        """테스트 전용. 실행 중 job이 있으면 거부한다 — 지우면 상태가 새어 나간다."""

        with self._guard:
            if any(record.state == "running" for record in self._jobs.values()):
                raise RuntimeError("cannot reset the job runner while a job is running")
            self._jobs.clear()
            self._tasks.clear()
            self._closed = False


job_runner = JobRunner()


__all__ = [
    "FINISHED_JOB_TTL_SECONDS",
    "MAX_FINISHED_JOBS",
    "MAX_RUNNING_JOBS",
    "JobConflictError",
    "JobNotFoundError",
    "JobRecord",
    "JobRunner",
    "JobState",
    "job_runner",
]
