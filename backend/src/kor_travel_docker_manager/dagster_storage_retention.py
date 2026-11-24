"""공용 Dagster storage 보존 정리(ADR-66, `docs/dagster-storage-retention.md`).

Dagster의 `retention` 설정은 **RUNNING instigator의 tick**만 지운다. run·event_log에는 보존 설정이 없고, 멈췄거나
workspace에서 빠진 instigator의 tick은 아무도 지우지 않는다. 그래서 `dagster_shared`가 무기한 자란다(2026-11-23 수집
실패 감사 §1.4: n150 event_logs 2.0 GB·job_ticks 1.1 GB). 이 모듈은 자기 호스트 plane의 storage에서

1. 멈춘 instigator의 오래된 tick — instigator가 RUNNING이 아니고(STOPPED·DECLARED_IN_CODE·행 없음) 보존 창 안에
   tick이 하나도 없는 selector의, 창보다 오래된 tick,
2. 오래된 run — 종료 상태(SUCCESS는 `success_days`, FAILURE·CANCELED는 `failure_days`)이고 마지막 갱신이 그보다 오래된
   run과 그 run의 event_log·run_tags(FK cascade)·asset_event_tags·asset_check_executions

를 배치로 지운다. 한 배치가 한 트랜잭션이고 statement·lock timeout을 건다. 기본은 dry-run(읽기 전용 트랜잭션)이다.

**Dagster `delete_run`을 쓰지 않는 이유**(1.13.24 원문 검토): `DagsterInstance.delete_run`은 run 행 삭제와
`delete_events`(그 run의 event_log·asset_event_tags 삭제, concurrency slot 해제)를 각자 연결로 부른다. 그 의미를 그대로
SQL 한 트랜잭션으로 옮기되 세 가지를 더한다 — ① 배치·timeout(한 run씩 세 연결은 수만 건에 맞지 않고 timeout을 걸 수 없다),
② 자산 상태 보존(`delete_run`은 자산의 **최신** materialization·observation event까지 지워 자산 카탈로그·partition 상태가
바뀐다 — 그런 run은 남긴다), ③ `asset_check_executions` 삭제(`delete_run`은 남겨 고아가 된다). Dagster를 import하지 않으므로
schema는 Dagster 1.13.24의 alembic head(`DAGSTER_STORAGE_REVISION`)와 맞을 때만 돈다 — 버전이 바뀌면 멈추고 다시 검토한다.

storage 접근은 백업·수집 감시와 같은 길이다: `docker exec … psql`(컨테이너 안 local socket, `POSTGRES_USER`) — 비밀번호·DSN을
읽지 않는다. systemd timer(`deploy/systemd/ktdm-dagster-storage-retention.*`)가 주 1회 부른다.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services import standalone_backup
from kor_travel_docker_manager.services.registry import get_project_root
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

#: 이 SQL을 검토한 Dagster 버전과 그 storage alembic head. `docker/dagster-host/requirements.txt`의 dagster 핀과
#: `test_dagster_storage_retention.py`가 대조한다 — Dagster를 올리면 시험이 이 검토를 다시 요구한다.
DAGSTER_VERSION = "1.13.24"
DAGSTER_STORAGE_REVISION = "29b539ebc72a"

CONFIG_PATH = Path("config/dagster-storage-retention.yml")
#: tmpfiles.d가 부팅 때 root 0700으로 만드는 Manager lock 디렉터리 아래 — `/run/lock` 바로 아래(1777 sticky)는 다른 사용자가
#: 같은 이름을 먼저 만들어 root의 열기를 막거나(`fs.protected_regular`) flock을 쥘 수 있다(`deploy/tmpfiles.d`).
LOCK_PATH = Path("/run/lock/kor-travel-docker-manager/dagster-storage-retention.lock")
_APPLICATION_NAME = "ktdm-dagster-storage-retention"
_SELECTOR = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")

Runner = Callable[..., subprocess.CompletedProcess]
Emit = Callable[[dict[str, Any]], None]


class RetentionError(Exception):
    """정리 실패. 메시지는 journal로 가므로 비밀을 담지 않는다. `deleted`는 실패 전에 커밋한 양이다."""

    deleted: dict[str, int] | None = None


# --------------------------------------------------------------------------- 설정


@dataclass(frozen=True)
class Policy:
    success_days: int
    failure_days: int
    stopped_instigator_tick_days: int
    run_batch: int
    tick_batch: int
    pause_seconds: float
    statement_timeout_seconds: int
    plan_statement_timeout_seconds: int
    lock_timeout_seconds: int
    max_runtime_seconds: int

    @property
    def run_classes(self) -> tuple[tuple[str, int], ...]:
        """(Dagster run status, 보존 일수). 종료 상태만 — 진행 중·대기 run은 나이와 무관하게 남긴다."""

        return (
            ("SUCCESS", self.success_days),
            ("FAILURE", self.failure_days),
            ("CANCELED", self.failure_days),
        )


_SHAPE: dict[str, dict[str, type]] = {
    "runs": {"success_days": int, "failure_days": int},
    "ticks": {"stopped_instigator_days": int},
    "batch": {"runs": int, "ticks": int, "pause_seconds": float},
    "timeouts": {
        "statement_seconds": int, "plan_statement_seconds": int, "lock_seconds": int, "max_runtime_seconds": int,
    },
}


def parse_policy(document: Any) -> Policy:
    """모르는 키·빠진 키·0 이하·bool을 거부한다. 기본값을 조용히 채우지 않는다."""

    def fail(reason: str) -> RetentionError:
        return RetentionError(f"보존 정리 설정이 올바르지 않습니다: {reason}")

    if not isinstance(document, dict) or set(document) != {"version", *_SHAPE}:
        raise fail("최상위 키는 version·runs·ticks·batch·timeouts")
    if isinstance(document["version"], bool) or document["version"] != 1:
        raise fail("version은 1")
    values: dict[str, dict[str, Any]] = {}
    for section, fields in _SHAPE.items():
        block = document[section]
        if not isinstance(block, dict) or set(block) != set(fields):
            raise fail(f"{section}의 키는 {sorted(fields)}")
        for name, kind in fields.items():
            value = block[name]
            # bool은 int의 하위형이다 — 정수 자리에 true를 받지 않는다. 실수 자리만 float를 받는다.
            allowed = (int, float) if kind is float else (int,)
            if isinstance(value, bool) or not isinstance(value, allowed) or value <= 0:
                raise fail(f"{section}.{name}은 양수")
        values[section] = block
    runs, timeouts = values["runs"], values["timeouts"]
    if runs["failure_days"] < runs["success_days"]:
        raise fail("runs.failure_days는 success_days 이상")
    if timeouts["lock_seconds"] > timeouts["statement_seconds"]:
        raise fail("timeouts.lock_seconds는 statement_seconds 이하")
    return Policy(
        success_days=runs["success_days"],
        failure_days=runs["failure_days"],
        stopped_instigator_tick_days=values["ticks"]["stopped_instigator_days"],
        run_batch=values["batch"]["runs"],
        tick_batch=values["batch"]["ticks"],
        pause_seconds=float(values["batch"]["pause_seconds"]),
        statement_timeout_seconds=timeouts["statement_seconds"],
        plan_statement_timeout_seconds=timeouts["plan_statement_seconds"],
        lock_timeout_seconds=timeouts["lock_seconds"],
        max_runtime_seconds=timeouts["max_runtime_seconds"],
    )


def _install_root() -> Path:
    """설치본은 wheel이라 `get_project_root()`(`__file__` 기준)가 venv 안을 가리킨다 — trusted 설치 root를 쓴다."""

    from kor_travel_docker_manager.services.trusted_install import (
        TRUSTED_INSTALL_ROOT,
        running_from_trusted_install_root,
    )

    return TRUSTED_INSTALL_ROOT if running_from_trusted_install_root() else Path(get_project_root())


def load_policy(path: Path | None = None) -> Policy:
    target = path or _install_root() / CONFIG_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise RetentionError(f"보존 정리 설정을 읽을 수 없습니다: {target}") from exc
    return parse_policy(load_yaml_rejecting_duplicate_keys(text))


# --------------------------------------------------------------------------- SQL
#
# 시각: runs.update_timestamp·job_ticks.timestamp는 Dagster가 UTC naive로 쓴다(두 호스트 실측: 최신 값 = now() UTC).
# 그래서 경계는 `now() AT TIME ZONE 'UTC'`에서 뺀다. 숫자 변수(:days 등)는 Python이 int로 검증해 넘기고,
# 문자열 변수(:'status'·:'selector')는 psql이 literal로 quote한다.

_NOW = "(now() AT TIME ZONE 'UTC')"

#: 계보 묶음: Dagster는 재실행·자동 재시도 run에 `dagster/root_run_id`(모든 후손)와 `dagster/parent_run_id`(직계)를 단다
#: (`RunDomain.create_reexecuted_run`). 묶음 키는 `coalesce(root tag, run_id)`이고 묶음은 **통째로만** 지운다 — 한 run이라도
#: 남아야 하면(나이·상태·아래 보호) 묶음 전체를 남긴다. 남는 run의 조상이 지워지면 FROM_FAILURE 재실행이
#: `DagsterRunNotFoundError`로, `get_run_group`이 예외로 깨진다(2026-11-24 적대 리뷰 P1, Dagster `plan/state.py`·
#: `sql_run_storage.get_run_group`).
_ROOT_TAG = "'dagster/root_run_id'"


def _expired(m: str) -> str:
    """run `m`이 종료 상태이고 그 상태의 보존 일수를 넘겼다. 진행 중·대기 상태는 나이와 무관하게 거짓이다."""

    # update_timestamp가 NULL이면(이론상 — server default가 있다) 만료로 보지 않는다(coalesce false → 남긴다).
    return f"""coalesce((
        ({m}.status = 'SUCCESS' AND {m}.update_timestamp < {_NOW} - make_interval(days => :success_days))
        OR ({m}.status IN ('FAILURE', 'CANCELED')
            AND {m}.update_timestamp < {_NOW} - make_interval(days => :failure_days))), false)"""


def _member_must_stay(m: str) -> str:
    """run `m`이 남아야 하면 참. 나이·상태와 자산·check·backfill·slot 보호."""

    return f"""(
      NOT {_expired(m)}
      -- 자산 카탈로그가 가리키는 마지막 run(`asset_keys.last_run_id`)
      OR {m}.run_id IN (SELECT ak.last_run_id FROM asset_keys ak WHERE ak.last_run_id IS NOT NULL)
      -- 자산의 (asset_key, event 종류, partition)별 최신 event를 가진 run — 지우면 자산·partition 상태가 바뀐다
      OR EXISTS (
        SELECT 1 FROM event_logs e
        WHERE e.run_id = {m}.run_id AND e.asset_key IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM event_logs n
            WHERE n.asset_key = e.asset_key AND n.dagster_event_type = e.dagster_event_type AND n.id > e.id
              AND (n.partition = e.partition OR (n.partition IS NULL AND e.partition IS NULL))))
      -- asset check (asset_key, check_name, partition)별 최신 실행
      OR EXISTS (
        SELECT 1 FROM asset_check_executions c
        WHERE c.run_id = {m}.run_id
          AND NOT EXISTS (
            SELECT 1 FROM asset_check_executions n
            WHERE n.asset_key = c.asset_key AND n.check_name = c.check_name
              AND n.partition IS NOT DISTINCT FROM c.partition AND n.id > c.id))
      -- 아직 있는 backfill의 run
      OR ({m}.backfill_id IS NOT NULL AND EXISTS (SELECT 1 FROM bulk_actions b WHERE b.key = {m}.backfill_id))
      -- concurrency slot·대기 step을 쥔 run(종료 run이면 Dagster가 이미 풀었어야 한다 — 손대지 않는다)
      OR EXISTS (SELECT 1 FROM concurrency_slots s WHERE s.run_id = {m}.run_id)
      OR EXISTS (SELECT 1 FROM pending_steps p WHERE p.run_id = {m}.run_id))"""


def _members(group: str) -> str:
    """계보 묶음 `group`(root run_id)의 run 전부 — root 자신과 root tag가 그것인 run."""

    return f"""(
        SELECT m.* FROM runs m WHERE m.run_id = {group}
        UNION ALL
        SELECT m.* FROM run_tags t JOIN runs m ON m.run_id = t.run_id
        WHERE t.key = {_ROOT_TAG} AND t.value = {group} AND m.run_id <> {group})"""


#: 지울 수 있는 계보 묶음의 씨앗: 이 상태·나이의 run(`idx_run_range`)이 속한 묶음.
_SEED_GROUPS = f"""
  SELECT DISTINCT coalesce(rt.value, r.run_id) AS root
  FROM runs r
  LEFT JOIN run_tags rt ON rt.run_id = r.run_id AND rt.key = {_ROOT_TAG}
  WHERE r.status = :'status'
    AND r.update_timestamp < {_NOW} - make_interval(days => :days)
"""

#: 묶음의 run이 하나도 남을 필요가 없을 때만 지운다(묶음 별칭 `g`).
_GROUP_IS_DELETABLE = f"""
  NOT EXISTS (SELECT 1 FROM {_members("g.root")} mm WHERE {_member_must_stay("mm")})
"""

_GROUP_RUNS = """
  SELECT m.run_id FROM runs m WHERE m.run_id IN (SELECT root FROM groups)
  UNION
  SELECT t.run_id FROM run_tags t WHERE t.key = 'dagster/root_run_id' AND t.value IN (SELECT root FROM groups)
"""

#: dry-run 집계는 상태를 가르지 않고 한 번에 센다 — 계보 묶음은 상태가 섞일 수 있어(실패 부모·성공 재실행) 상태별로
#: 세면 같은 묶음을 두 번 센다.
PLAN_RUNS_SQL = f"""
WITH aged AS (SELECT r.run_id, r.status FROM runs r WHERE {_expired("r")}),
seeds AS (
  SELECT DISTINCT coalesce(rt.value, a.run_id) AS root
  FROM aged a LEFT JOIN run_tags rt ON rt.run_id = a.run_id AND rt.key = {_ROOT_TAG}
),
groups AS (SELECT g.root FROM seeds g WHERE {_GROUP_IS_DELETABLE}),
eligible AS ({_GROUP_RUNS})
SELECT json_build_object(
  'aged_by_status', coalesce((SELECT json_object_agg(status, n) FROM (
      SELECT status, count(*) AS n FROM aged GROUP BY status) x), '{{}}'::json),
  'groups', (SELECT count(*) FROM groups),
  'eligible', (SELECT count(*) FROM eligible),
  'event_logs', (SELECT count(*) FROM event_logs WHERE run_id IN (SELECT run_id FROM eligible)),
  'run_tags', (SELECT count(*) FROM run_tags WHERE run_id IN (SELECT run_id FROM eligible)),
  'asset_event_tags', (SELECT count(*) FROM asset_event_tags WHERE event_id IN (
      SELECT id FROM event_logs WHERE run_id IN (SELECT run_id FROM eligible) AND asset_key IS NOT NULL)),
  'asset_check_executions', (SELECT count(*) FROM asset_check_executions
      WHERE run_id IN (SELECT run_id FROM eligible)));
"""

#: run 한 배치 = 계보 묶음 :batch개(대개 run 하나짜리 묶음). 모든 CTE가 같은 snapshot을 본다. run_tags는 FK
#: `ON DELETE CASCADE`로 같이 지워진다. 행 lock은 DELETE가 잡고 `lock_timeout`이 대기를 끊는다.
DELETE_RUNS_SQL = f"""
WITH groups AS (
  SELECT g.root FROM ({_SEED_GROUPS}) g
  WHERE {_GROUP_IS_DELETABLE}
  LIMIT :batch
), batch AS ({_GROUP_RUNS}
), run_tag_rows AS (
  SELECT count(*) AS n FROM run_tags WHERE run_id IN (SELECT run_id FROM batch)
), deleted_asset_event_tags AS (
  DELETE FROM asset_event_tags WHERE event_id IN (
    SELECT e.id FROM event_logs e WHERE e.run_id IN (SELECT run_id FROM batch) AND e.asset_key IS NOT NULL)
  RETURNING 1
), deleted_checks AS (
  DELETE FROM asset_check_executions WHERE run_id IN (SELECT run_id FROM batch) RETURNING 1
), deleted_events AS (
  DELETE FROM event_logs WHERE run_id IN (SELECT run_id FROM batch) RETURNING 1
), deleted_runs AS (
  DELETE FROM runs WHERE run_id IN (SELECT run_id FROM batch) RETURNING 1
)
SELECT json_build_object(
  'groups', (SELECT count(*) FROM groups),
  'runs', (SELECT count(*) FROM deleted_runs),
  'event_logs', (SELECT count(*) FROM deleted_events),
  'run_tags', (SELECT n FROM run_tag_rows),
  'asset_event_tags', (SELECT count(*) FROM deleted_asset_event_tags),
  'asset_check_executions', (SELECT count(*) FROM deleted_checks));
"""

_TICK_CUTOFF = f"{_NOW} - make_interval(days => :tick_days)"

#: 멈춘 instigator의 selector — RUNNING이 아니고 창 안에 tick이 없다. RUNNING instigator는 Dagster가 자기 tick 때
#: `retention`대로 지운다(월 1회 schedule처럼 창 안에 tick이 없어도 다음 tick에 지운다) — 여기서 손대지 않는다.
STALE_SELECTORS_SQL = f"""
SELECT json_build_object(
  'selectors', coalesce((
    SELECT json_agg(json_build_object(
        'selector', s.selector_id, 'ticks', s.n, 'instigator', coalesce(i.status, 'ABSENT'),
        -- 사람이 dry-run에서 무엇인지 알아보게(표시용, 판정에 쓰지 않는다)
        'name', coalesce(
          substring(i.instigator_body from '"origin":.*?"(?:instigator_name|job_name)": "([^"]+)"'),
          (SELECT substring(x.tick_body from '"(?:instigator_name|job_name)": "([^"]+)"')
             FROM job_ticks x WHERE x.selector_id = s.selector_id ORDER BY x.timestamp DESC LIMIT 1)))
      ORDER BY s.n DESC, s.selector_id)
    FROM (
      SELECT t.selector_id, count(*) AS n FROM job_ticks t
      WHERE t.timestamp < {_TICK_CUTOFF} AND t.selector_id IS NOT NULL
      GROUP BY t.selector_id
    ) s
    LEFT JOIN instigators i ON i.selector_id = s.selector_id
    WHERE i.status IS DISTINCT FROM 'RUNNING'
      -- DECLARED_IN_CODE 행은 workspace에 있고 code 기본값으로 도는 instigator다 — Dagster scheduler는 돌지 않는 것을
      -- 지우고 남은 것을 갱신한다(`_scheduler/scheduler.py`). 창 안에 갱신됐으면 살아 있는 것으로 보고 손대지 않는다.
      -- instigator 행이 없으면(i.status NULL) 이 조건이 NULL이 되어 행을 버리지 않게 coalesce한다.
      AND NOT (coalesce(i.status, '') = 'DECLARED_IN_CODE' AND i.update_timestamp >= {_TICK_CUTOFF})
      AND NOT EXISTS (
        SELECT 1 FROM job_ticks recent
        WHERE recent.selector_id = s.selector_id AND recent.timestamp >= {_TICK_CUTOFF})
  ), '[]'::json),
  'legacy_null_selector_ticks', (
    SELECT count(*) FROM job_ticks WHERE selector_id IS NULL AND timestamp < {_TICK_CUTOFF}));
"""

#: tick 한 배치. 지우는 순간에도 조건을 다시 본다 — 그 사이 누가 instigator를 켰으면 지우지 않는다.
DELETE_TICKS_SQL = f"""
WITH deleted AS (
  DELETE FROM job_ticks WHERE id IN (
      SELECT t.id FROM job_ticks t
      WHERE t.selector_id = :'selector' AND t.timestamp < {_TICK_CUTOFF}
      ORDER BY t.timestamp
      LIMIT :batch
      FOR UPDATE SKIP LOCKED)
    AND NOT EXISTS (
      SELECT 1 FROM instigators i WHERE i.selector_id = :'selector'
        AND (i.status = 'RUNNING' OR (i.status = 'DECLARED_IN_CODE' AND i.update_timestamp >= {_TICK_CUTOFF})))
    AND NOT EXISTS (
      SELECT 1 FROM job_ticks recent
      WHERE recent.selector_id = :'selector' AND recent.timestamp >= {_TICK_CUTOFF})
  RETURNING 1
)
SELECT json_build_object('ticks', (SELECT count(*) FROM deleted));
"""

#: schema 판과 시각 기준의 사전 확인. 최신 run·tick 시각이 UTC 지금보다 한 시간 넘게 미래면 시각이 UTC가 아니다
#: (예: KST로 쓰였다) — 그때 경계를 계산하면 9시간 어긋나므로 지우지 않는다.
PREFLIGHT_SQL = f"""
SELECT json_build_object(
  'revision', (SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version),
  'run_ahead_seconds', extract(epoch FROM (SELECT update_timestamp FROM runs ORDER BY id DESC LIMIT 1) - {_NOW}),
  'tick_ahead_seconds', extract(epoch FROM (SELECT timestamp FROM job_ticks ORDER BY id DESC LIMIT 1) - {_NOW}));
"""
# 최신 행은 기본 키 역순 1행으로 읽는다 — `max(update_timestamp)`는 그 열만의 인덱스가 없어 표 전체를 읽는다
# (odroid 2026-11-24 실측 16초).
_MAX_AHEAD_SECONDS = 3600

#: 쓰기 트랜잭션의 첫 문장. schema 판이 다르면 예외로 트랜잭션 전체를 되돌린다(ON_ERROR_STOP).
_SCHEMA_GUARD = (
    "DO $guard$ BEGIN IF (SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version) "
    f"IS DISTINCT FROM '{DAGSTER_STORAGE_REVISION}' THEN "
    "RAISE EXCEPTION 'ktdm retention: Dagster storage schema changed'; END IF; END $guard$;\n"
)


# --------------------------------------------------------------------------- storage


class Storage:
    """질의 하나 = `docker exec -i … psql` 한 번 = 트랜잭션 하나. 결과는 jsonb/json 한 값(한 줄)이다."""

    def __init__(
        self, *, container: str, database: str, admin: str, port: int, policy: Policy,
        runner: Runner = subprocess.run,
    ) -> None:
        self._container, self._database, self._admin, self._port = container, database, admin, port
        self._policy = policy
        self._runner = runner

    def query(self, sql: str, variables: Mapping[str, str | int], *, write: bool, plan: bool = False) -> Any:
        """`plan`은 dry-run의 전체 집계(읽기 전용)다 — 지울 run 전부를 한 문장으로 세므로 상한이 따로다
        (odroid 2026-11-24 실측: 30일 가정 4,376 run 집계 53초)."""

        policy = self._policy
        if plan and write:
            raise RetentionError("plan 질의는 읽기 전용이어야 합니다")
        statement = policy.plan_statement_timeout_seconds if plan else policy.statement_timeout_seconds
        script = (
            ("BEGIN;\n" if write else "BEGIN READ ONLY;\n")
            + f"SET LOCAL statement_timeout = '{int(statement)}s';\n"
            + f"SET LOCAL lock_timeout = '{int(policy.lock_timeout_seconds)}s';\n"
            + f"SET LOCAL idle_in_transaction_session_timeout = '{int(statement)}s';\n"
            # 쓰기 배치마다 같은 트랜잭션 안에서 schema 판을 다시 본다 — 45분 실행 중 storage migrate가 Dagster를 올리면
            # 그 뒤 배치는 검토하지 않은 schema에서 지우게 된다(2026-11-24 적대 리뷰 P2).
            + (_SCHEMA_GUARD if write else "")
            + sql.strip() + "\nCOMMIT;\n"
        )
        argv = [
            "docker", "exec", "-i", "--user", "postgres", "--env", f"PGAPPNAME={_APPLICATION_NAME}",
            self._container,
            "psql", "--username", self._admin, "--port", str(self._port), "--dbname", self._database,
            "--no-psqlrc", "--quiet", "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1",
        ]
        for name, value in variables.items():
            if not re.fullmatch(r"[a-z_]+", name):
                raise RetentionError("invalid query variable name")
            if isinstance(value, bool) or not isinstance(value, int | str):
                raise RetentionError("invalid query variable value")
            argv.extend(["--set", f"{name}={value}"])
        try:
            completed = self._runner(
                argv, input=script, capture_output=True, text=True, check=False,
                timeout=statement + 30, cwd="/",
            )
        except subprocess.TimeoutExpired as exc:
            raise RetentionError("storage 질의가 시간 안에 끝나지 않았습니다.") from exc
        except OSError as exc:
            raise RetentionError(f"docker를 실행할 수 없습니다: {exc.strerror or exc}") from exc
        if completed.returncode != 0:
            # psql은 ERROR 뒤에 CONTEXT·caret 줄을 더 찍는다 — 원인 줄을 고른다.
            lines = (completed.stderr or "").strip().splitlines()
            cause = next((line for line in lines if "ERROR:" in line), lines[-1] if lines else "(stderr 없음)")
            raise RetentionError(f"storage 질의 실패(exit {completed.returncode}): {cause[:300]}")
        text = (completed.stdout or "").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RetentionError("storage 질의 결과를 해석할 수 없습니다.") from exc


def discover_storage(policy: Policy) -> Storage:
    """백업 role 표(`dagster_shared`)가 컨테이너·DB 이름의 정본이다. admin role·port는 살아 있는 컨테이너에서 읽는다."""

    env_name, default_container, database = standalone_backup._ROLE_CONFIG["dagster_shared"]
    container = (os.environ.get(env_name, "").strip() if env_name else "") or default_container
    try:
        admin = standalone_backup._discover_admin_role(container)
        port = standalone_backup._discover_port(container)
    except standalone_backup.StandaloneBackupError as exc:
        raise RetentionError(f"공용 PostgreSQL 컨테이너를 확인할 수 없습니다: {exc}") from exc
    return Storage(container=container, database=database, admin=admin, port=port, policy=policy)


# --------------------------------------------------------------------------- 실행


@contextmanager
def single_instance(path: Path = LOCK_PATH) -> Iterator[bool]:
    """같은 호스트에서 정리가 겹치지 않게 한다. 배포 lock(G)은 잡지 않는다 — 한 시간 가까이 배포를 막지 않으려고.
    배포·storage migrate와의 경합은 배치마다의 lock_timeout이 막는다."""

    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(descriptor)


def _plan(storage: Storage, policy: Policy) -> dict[str, Any]:
    runs = storage.query(PLAN_RUNS_SQL, {
        "success_days": policy.success_days, "failure_days": policy.failure_days,
    }, write=False, plan=True)
    runs["days"] = {status: days for status, days in policy.run_classes}
    ticks = storage.query(STALE_SELECTORS_SQL, {"tick_days": policy.stopped_instigator_tick_days}, write=False)
    by_state: dict[str, dict[str, int]] = {}
    for row in ticks["selectors"]:
        bucket = by_state.setdefault(str(row["instigator"]), {"selectors": 0, "ticks": 0})
        bucket["selectors"] += 1
        bucket["ticks"] += int(row["ticks"])
    return {
        "runs": runs,
        "ticks": {
            "days": policy.stopped_instigator_tick_days,
            "selectors": len(ticks["selectors"]),
            "ticks": sum(int(row["ticks"]) for row in ticks["selectors"]),
            "by_instigator_state": by_state,
            "legacy_null_selector_ticks_untouched": ticks["legacy_null_selector_ticks"],
            # 첫 apply 전에 사람이 무엇을 지우는지 알아보게(이름은 표시용).
            "detail": [{key: row.get(key) for key in ("name", "instigator", "ticks")}
                       for row in ticks["selectors"][:200]],
        },
    }


def _run_variables(policy: Policy, status: str, days: int) -> dict[str, str | int]:
    return {
        "status": status, "days": int(days),
        "success_days": policy.success_days, "failure_days": policy.failure_days,
    }


def run(
    storage: Storage, policy: Policy, *, apply: bool, emit: Emit,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """dry-run은 지울 양만 센다(읽기 전용). apply는 tick → run 순서로 배치 삭제하고 배치마다 진행을 낸다."""

    started = clock()
    header = storage.query(PREFLIGHT_SQL, {}, write=False)
    if header.get("revision") != DAGSTER_STORAGE_REVISION:
        raise RetentionError(
            f"Dagster storage schema가 검토한 판(Dagster {DAGSTER_VERSION}, {DAGSTER_STORAGE_REVISION})과 다릅니다: "
            f"{header.get('revision')!r} — SQL을 다시 검토하기 전에는 지우지 않습니다."
        )
    for key in ("run_ahead_seconds", "tick_ahead_seconds"):
        ahead = header.get(key)
        if ahead is not None and float(ahead) > _MAX_AHEAD_SECONDS:
            raise RetentionError(f"storage 시각이 UTC가 아닙니다({key}={float(ahead):.0f}) — 경계를 믿을 수 없어 지우지 않습니다.")
    if not apply:
        return {"mode": "dry-run", "ok": True, "plan": _plan(storage, policy)}

    totals: dict[str, int] = {
        "ticks": 0, "runs": 0, "event_logs": 0, "run_tags": 0, "asset_event_tags": 0, "asset_check_executions": 0,
    }

    def out_of_time() -> bool:
        return clock() - started >= policy.max_runtime_seconds

    try:
        budget_exhausted = _apply(storage, policy, totals, emit, out_of_time, clock, sleep, started)
    except RetentionError as exc:
        # 이미 커밋한 배치는 그대로다 — summary에 지운 양을 함께 남긴다.
        exc.deleted = dict(totals)
        raise
    return {
        "mode": "apply", "ok": True, "deleted": totals,
        # 시간 상한에 걸리면 남은 것은 다음 주에 이어 지운다 — 실패가 아니다.
        "budget_exhausted": budget_exhausted,
        "elapsed_seconds": round(clock() - started, 1),
    }


def _apply(
    storage: Storage, policy: Policy, totals: dict[str, int], emit: Emit, out_of_time: Callable[[], bool],
    clock: Callable[[], float], sleep: Callable[[float], None], started: float,
) -> bool:
    budget_exhausted = False
    selectors = storage.query(STALE_SELECTORS_SQL, {"tick_days": policy.stopped_instigator_tick_days}, write=False)
    for row in selectors["selectors"]:
        selector = str(row["selector"])
        if not _SELECTOR.fullmatch(selector):
            emit({"event": "skip", "phase": "ticks", "reason": "selector 형식", "selector": selector[:80]})
            continue
        while True:
            if out_of_time():
                budget_exhausted = True
                break
            result = storage.query(DELETE_TICKS_SQL, {
                "selector": selector, "batch": policy.tick_batch,
                "tick_days": policy.stopped_instigator_tick_days,
            }, write=True)
            deleted = int(result["ticks"])
            totals["ticks"] += deleted
            emit({"event": "batch", "phase": "ticks", "selector": selector, "instigator": row["instigator"],
                  "deleted": deleted, "elapsed_seconds": round(clock() - started, 1)})
            if deleted < policy.tick_batch:
                break
            sleep(policy.pause_seconds)
        if budget_exhausted:
            break

    for status, days in policy.run_classes:
        while not budget_exhausted:
            if out_of_time():
                budget_exhausted = True
                break
            result = storage.query(DELETE_RUNS_SQL, {
                **_run_variables(policy, status, days), "batch": policy.run_batch,
            }, write=True)
            for key in ("runs", "event_logs", "run_tags", "asset_event_tags", "asset_check_executions"):
                totals[key] += int(result[key])
            emit({"event": "batch", "phase": "runs", "status": status, **result,
                  "elapsed_seconds": round(clock() - started, 1)})
            if int(result["groups"]) < policy.run_batch:
                break
            sleep(policy.pause_seconds)

    return budget_exhausted


def _emit_line(record: dict[str, Any]) -> None:
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="공용 Dagster storage 보존 정리(기본 dry-run)")
    parser.add_argument("--apply", action="store_true", help="실제로 지운다(없으면 읽기 전용으로 지울 양만 센다)")
    parser.add_argument("--config", type=Path, help="설정 파일(기본: 설치 root의 config/dagster-storage-retention.yml)")
    options = parser.parse_args(argv)
    result: dict[str, Any]
    try:
        policy = load_policy(options.config)
        if os.geteuid() != 0:
            raise RetentionError("root로 실행해야 합니다(공용 PG 컨테이너 docker exec·Manager lock 디렉터리).")
        with single_instance() as acquired:
            if not acquired:
                # 다른 정리가 돌고 있다 — 이번 실행은 아무것도 하지 않았으므로 성공으로 보고하지 않는다.
                result = {"ok": False, "state": "already_running"}
            else:
                storage = discover_storage(policy)
                result = run(storage, policy, apply=options.apply, emit=_emit_line)
    except (RetentionError, OSError, KeyError, TypeError, ValueError) as exc:
        result = {"ok": False, "mode": "apply" if options.apply else "dry-run",
                  "error": type(exc).__name__, "message": str(exc)[:500]}
        if getattr(exc, "deleted", None) is not None:
            result["deleted"] = exc.deleted
    _emit_line({"event": "summary", **result})
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
