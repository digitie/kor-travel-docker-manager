"""공용 Dagster storage 보존 정리(ADR-66)를 **실제 PostgreSQL·Dagster 1.13.24 storage** 위에서 돌린다.

단위 시험(`test_dagster_storage_retention.py`)은 흐름과 SQL 모양만 본다. 여기서는 Dagster 자신의 API로 run·event·
asset materialization·observation·instigator·tick을 만들고 나이를 SQL로 되돌린 뒤 정리를 돌려

- 지울 것만 지우는가 — 보호마다 **그 보호 하나로만** 남는 run을 둔다(한 보호를 빼면 그 run이 지워져 시험이 실패한다):
  자산 최신 materialization·observation·partition별 최신 event, `asset_keys.last_run_id`, asset check 최신 실행,
  살아 있는 backfill, concurrency slot·대기 step, 계보 묶음(다른 보호로 남는 자식·최근 후손·전부 만료된 묶음),
- 고아가 남지 않는가(event_logs·run_tags·asset_event_tags·asset_check_executions의 run/event 참조),
- 정리 뒤 Dagster가 storage를 그대로 읽는가(run·로그·자산 최신 materialization·partition·run group·tick 조회),
- dry-run이 아무것도 바꾸지 않고 apply와 같은 수를 세는가, 두 번째 apply가 0인가

를 본다. 격리 plane은 `test_dagster_shared_storage_integration.py`의 fixture(정본 compose의 공용 PG·db-init·storage
migrate, 격리 tag 이미지)를 그대로 쓰고 gate도 같다(`KTDM_REQUIRE_DOCKER_INTEGRATION`). CI에는 fixture 이미지가 없어
skip되므로 이 모듈의 SQL을 바꾸면 `KTDM_REQUIRE_DOCKER_INTEGRATION=1`로 돌린 결과를 PR에 붙인다.
"""

from __future__ import annotations

import json
from typing import Any

from test_dagster_shared_storage_integration import (  # noqa: F401 - fixture 재사용
    _DB_INIT,
    _MIGRATE,
    _RUN_TIMEOUT,
    _one_shot,
    _Plane,
    _sql_in,
    isolated_plane,
)
from test_shared_postgres_runtime_integration import _ISOLATED_PORT, _inspect, _run, _wait_healthy

from kor_travel_docker_manager import dagster_storage_retention as retention

#: Dagster 자신의 API로 storage를 채운다. 나이는 SQL로 나중에 되돌린다(Dagster가 시각을 지금으로 쓴다).
_SEED = r"""
import json, time
from dagster import AssetKey, AssetMaterialization, AssetObservation, DagsterInstance
from dagster._core.events import (
    AssetObservationData, DagsterEvent, DagsterEventType, StepMaterializationData)
from dagster._core.events.log import EventLogEntry
from dagster._core.remote_origin import (
    GrpcServerCodeLocationOrigin, RemoteInstigatorOrigin, RemoteRepositoryOrigin)
from dagster._core.scheduler.instigation import (
    InstigatorState, InstigatorStatus, InstigatorType, ScheduleInstigatorData, TickData, TickStatus)
from dagster._core.storage.dagster_run import DagsterRunStatus
from dagster._core.test_utils import create_run_for_test

ids = {}
with DagsterInstance.get() as instance:
    def run(name, status, parent=None, root=None):
        kw = {}
        if parent is not None:
            # 재실행·자동 재시도(`create_reexecuted_run`)는 계보를 필드와 tag 둘 다에 둔다 — 같은 모양으로 만든다.
            kw = dict(parent_run_id=parent.run_id, root_run_id=root.run_id,
                      tags={"dagster/parent_run_id": parent.run_id, "dagster/root_run_id": root.run_id})
        r = create_run_for_test(instance, job_name="retention_probe", status=status, **kw)
        instance.report_engine_event(f"{name} step", r)
        instance.report_engine_event(f"{name} done", r)
        ids[name] = r.run_id
        return r

    def store(r, kind, data):
        instance.store_event(EventLogEntry(
            error_info=None, level="debug", user_message="", run_id=r.run_id, timestamp=time.time(),
            step_key="s", job_name=r.job_name,
            dagster_event=DagsterEvent(event_type_value=kind.value, job_name=r.job_name, event_specific_data=data)))

    def materialize(r, key, version=None, partition=None):
        tags = {"dagster/data_version": version} if version else None
        store(r, DagsterEventType.ASSET_MATERIALIZATION, StepMaterializationData(
            AssetMaterialization(asset_key=AssetKey(key), partition=partition, tags=tags)))

    def observe(r, key):
        store(r, DagsterEventType.ASSET_OBSERVATION, AssetObservationData(AssetObservation(asset_key=AssetKey(key))))

    S, F, C, ST = (DagsterRunStatus.SUCCESS, DagsterRunStatus.FAILURE, DagsterRunStatus.CANCELED,
                   DagsterRunStatus.STARTED)
    # 자산 x: 옛 materialization(지움, data_version tag 1행) → 최신(남김, last_run_id·최신 event)
    materialize(run("old_materialization", S), "x", "v1")
    materialize(run("latest_materialization", S), "x", "v2")
    # 자산 y: observation만 — observation은 last_run_id를 바꾸지 않으므로 최신 event 규칙 하나로만 남는다
    observe(run("old_observation", S), "y")
    observe(run("latest_observation_only", S), "y")
    # 자산 p: partition별 최신 — 2026-01의 최신은 last_run_id가 아니다(2026-02가 더 늦다)
    materialize(run("latest_of_partition_01_only", S), "p", partition="2026-01")
    materialize(run("latest_of_partition_02", S), "p", partition="2026-02")
    # last_run_id 하나로만 남는 run(아래 SQL이 자산 w의 last_run_id를 이 run으로 돌린다)
    run("last_run_id_only", S)
    # asset check 최신 실행 하나로만 남는 run과 옛 실행만 가진 run(실행 행은 SQL로 넣는다)
    run("old_check_only", S)
    run("latest_check_only", S)
    # backfill·slot·대기 step 하나로만 남는 run(행은 SQL로 넣는다)
    run("live_backfill_run", S)
    run("slot_holder", S)
    run("pending_step_holder", S)
    # 계보 ①: 만료된 부모 + 다른 보호(자산 z 최신)로 남는 만료된 자식 → 묶음 전체를 남긴다
    l1 = run("lineage1_parent", F)
    materialize(run("lineage1_child_latest_z", S, parent=l1, root=l1), "z")
    # 계보 ②: R→B→C 만료 + 최근 G(root R, parent C) → 묶음 전체를 남긴다
    r2 = run("lineage2_root", F)
    b2 = run("lineage2_b", F, parent=r2, root=r2)
    c2 = run("lineage2_c", F, parent=b2, root=r2)
    run("lineage2_recent_g", S, parent=c2, root=r2)
    # 계보 ③: 전부 만료 — 실패 root(180일 넘음) + 성공 재실행(90일 넘음) → 상태가 달라도 묶음째 지운다
    r3 = run("lineage3_root", F)
    run("lineage3_rerun", S, parent=r3, root=r3)
    # 나이·상태
    run("old_plain", S)
    run("failure_100_days", F)
    run("failure_200_days", F)
    run("canceled_200_days", C)
    w = run("recent_success", S)
    materialize(w, "w")
    run("started_200_days", ST)

    location = GrpcServerCodeLocationOrigin(host="127.0.0.1", port=4000, location_name="probe.location")
    repository = RemoteRepositoryOrigin(code_location_origin=location, repository_name="__repository__")
    ticks = {}
    for name, kind, status in (
        ("stopped_sensor", InstigatorType.SENSOR, InstigatorStatus.STOPPED),
        ("stopped_sensor_recent", InstigatorType.SENSOR, InstigatorStatus.STOPPED),
        ("running_monthly_schedule", InstigatorType.SCHEDULE, InstigatorStatus.RUNNING),
        ("declared_in_code_stale", InstigatorType.SENSOR, InstigatorStatus.DECLARED_IN_CODE),
        ("declared_in_code_live", InstigatorType.SENSOR, InstigatorStatus.DECLARED_IN_CODE),
        ("absent_sensor", InstigatorType.SENSOR, None),
    ):
        origin = RemoteInstigatorOrigin(repository_origin=repository, instigator_name=name)
        data = ScheduleInstigatorData("0 0 2 * *") if kind == InstigatorType.SCHEDULE else None
        state = InstigatorState(origin, kind, status or InstigatorStatus.STOPPED, data)
        if status is not None:
            instance.add_instigator_state(state)
        tick_ids = []
        for _ in range(3):
            tick = instance.create_tick(TickData(
                instigator_origin_id=origin.get_id(), instigator_name=name, instigator_type=kind,
                status=TickStatus.SKIPPED, timestamp=time.time(), selector_id=state.selector_id))
            tick_ids.append(tick.tick_id)
        ticks[name] = {"origin": origin.get_id(), "selector": state.selector_id, "ids": tick_ids}
print(json.dumps({"runs": ids, "ticks": ticks}))
"""

#: 정리 뒤 Dagster가 storage를 그대로 읽는가.
_VERIFY = r"""
import json, sys
from dagster import AssetKey, DagsterInstance
request = json.loads(sys.argv[1])
with DagsterInstance.get() as instance:
    runs = request["runs"]
    present = {name: instance.get_run_by_id(run_id) is not None for name, run_id in runs.items()}
    logs = {name: len(instance.all_logs(run_id)) for name, run_id in runs.items() if present[name]}
    latest = instance.get_latest_materialization_event(AssetKey("x"))
    partitions = sorted(instance.get_materialized_partitions(AssetKey("p")))
    group = instance.get_run_group(runs["lineage2_recent_g"])
    ticks = {name: len(instance.get_ticks(t["origin"], t["selector"])) for name, t in request["ticks"].items()}
    print(json.dumps({
        "present": present, "logs": logs, "latest_run": latest.run_id if latest else None,
        "partitions": partitions, "lineage2_group": len(group[1]) if group else 0,
        "asset_records": len(instance.get_asset_records()), "ticks": ticks,
        "all_runs": len(instance.get_run_records()),
    }))
"""

_ORPHANS = """
SELECT json_build_object(
  'event_logs', (SELECT count(*) FROM event_logs e WHERE e.run_id <> '' AND NOT EXISTS (
      SELECT 1 FROM runs r WHERE r.run_id = e.run_id)),
  'run_tags', (SELECT count(*) FROM run_tags t WHERE NOT EXISTS (SELECT 1 FROM runs r WHERE r.run_id = t.run_id)),
  'asset_event_tags', (SELECT count(*) FROM asset_event_tags a WHERE NOT EXISTS (
      SELECT 1 FROM event_logs e WHERE e.id = a.event_id)),
  'asset_check_executions', (SELECT count(*) FROM asset_check_executions c WHERE c.run_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM runs r WHERE r.run_id = c.run_id)),
  'lineage', (SELECT count(*) FROM run_tags t
      WHERE t.key IN ('dagster/parent_run_id', 'dagster/root_run_id')
        AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.run_id = t.value)))
"""

_COUNTS = """
SELECT json_build_object(
  'runs', (SELECT count(*) FROM runs), 'event_logs', (SELECT count(*) FROM event_logs),
  'run_tags', (SELECT count(*) FROM run_tags), 'asset_event_tags', (SELECT count(*) FROM asset_event_tags),
  'asset_check_executions', (SELECT count(*) FROM asset_check_executions), 'job_ticks', (SELECT count(*) FROM job_ticks))
"""

#: run 이름 → 나이(일). 나머지 run은 최근(만든 그대로)이다.
_AGES = {
    "old_materialization": 120, "latest_materialization": 100, "old_observation": 110, "latest_observation_only": 100,
    "latest_of_partition_01_only": 100, "latest_of_partition_02": 100, "last_run_id_only": 100,
    "old_check_only": 110, "latest_check_only": 100, "live_backfill_run": 100, "slot_holder": 100,
    "pending_step_holder": 100, "lineage1_parent": 200, "lineage1_child_latest_z": 100,
    "lineage2_root": 200, "lineage2_b": 200, "lineage2_c": 200, "lineage3_root": 200, "lineage3_rerun": 100,
    "old_plain": 91, "failure_100_days": 100, "failure_200_days": 200, "canceled_200_days": 200,
    "started_200_days": 200,
}
_DELETED = {
    "old_materialization", "old_observation", "old_check_only", "lineage3_root", "lineage3_rerun",
    "old_plain", "failure_200_days", "canceled_200_days",
}


def _python(plane: _Plane, script: str, *args: str) -> dict[str, Any]:
    probe = _run(
        *plane.compose, "run", "--rm", "--no-deps", "--entrypoint", "python", _MIGRATE, "-I", "-c", script, *args,
        env=plane.env, timeout=_RUN_TIMEOUT,
    )
    assert probe.returncode == 0, probe.stdout[-2000:] + probe.stderr[-3000:]
    result: dict[str, Any] = json.loads(probe.stdout.strip().splitlines()[-1])
    return result


def _json(plane: _Plane, sql: str) -> dict[str, Any]:
    rows = _sql_in(plane, plane.database, sql)
    value: dict[str, Any] = json.loads(rows[-1])
    return value


def _storage(plane: _Plane, policy: retention.Policy) -> retention.Storage:
    admin = next(
        entry.partition("=")[2] for entry in _inspect(plane.postgres)["Config"]["Env"]
        if entry.startswith("POSTGRES_USER=")
    )
    return retention.Storage(container=plane.postgres, database=plane.database, admin=admin,
                             port=int(_ISOLATED_PORT), policy=policy)


def _age_and_attach(plane: _Plane, seeded: dict[str, Any]) -> None:
    runs, ticks = seeded["runs"], seeded["ticks"]
    statements = [
        f"UPDATE runs SET update_timestamp = (now() AT TIME ZONE 'UTC') - make_interval(days => {days}) "
        f"WHERE run_id = '{runs[name]}'"
        for name, days in _AGES.items()
    ]
    statements += [
        # asset check x/c: 옛 실행(지움)은 지울 run에, 최신은 남는 run에. x/c2: 옛 실행만 가진 run과 최신 실행 하나로만 남는 run.
        "INSERT INTO asset_check_executions (asset_key, check_name, run_id, execution_status) VALUES "
        f"('[\"x\"]', 'c', '{runs['old_materialization']}', 'SUCCEEDED'), "
        f"('[\"x\"]', 'c', '{runs['latest_materialization']}', 'SUCCEEDED'), "
        f"('[\"x\"]', 'c2', '{runs['old_check_only']}', 'SUCCEEDED'), "
        f"('[\"x\"]', 'c2', '{runs['latest_check_only']}', 'SUCCEEDED')",
        # 자산 w의 last_run_id를 event 없는 run으로 돌린다 — last_run_id 보호 하나로만 남는다.
        f"UPDATE asset_keys SET last_run_id = '{runs['last_run_id_only']}' WHERE asset_key = '[\"w\"]'",
        "INSERT INTO bulk_actions (key, status, timestamp, body, action_type) "
        "VALUES ('bf000001', 'REQUESTED', now(), NULL, 'PARTITION_BACKFILL')",
        f"UPDATE runs SET backfill_id = 'bf000001' WHERE run_id = '{runs['live_backfill_run']}'",
        "INSERT INTO concurrency_slots (concurrency_key, run_id, step_key, deleted) "
        f"VALUES ('probe', '{runs['slot_holder']}', 's', false)",
        "INSERT INTO pending_steps (concurrency_key, run_id, step_key, priority) "
        f"VALUES ('probe', '{runs['pending_step_holder']}', 's', 0)",
        # DECLARED_IN_CODE 둘 중 하나만 오래 갱신되지 않았다(workspace에서 빠진 것) — 살아 있는 것은 남긴다.
        "UPDATE instigators SET update_timestamp = (now() AT TIME ZONE 'UTC') - make_interval(days => 40) "
        f"WHERE selector_id = '{ticks['declared_in_code_stale']['selector']}'",
    ]
    tick_ages = {"stopped_sensor": [20, 21, 22], "stopped_sensor_recent": [1, 20, 21],
                 "running_monthly_schedule": [20, 21, 22], "declared_in_code_stale": [15, 16, 30],
                 "declared_in_code_live": [15, 16, 30], "absent_sensor": [40, 41, 42]}
    for name, days in tick_ages.items():
        for tick_id, age in zip(ticks[name]["ids"], days, strict=True):
            statements.append(
                f"UPDATE job_ticks SET timestamp = (now() AT TIME ZONE 'UTC') - make_interval(days => {age}) "
                f"WHERE id = {int(tick_id)}"
            )
    _sql_in(plane, plane.database, "; ".join(statements))


def test_retention_deletes_only_expired_runs_and_stale_ticks_without_orphans(
    isolated_plane: _Plane,  # noqa: F811 - 가져온 fixture
) -> None:
    plane = isolated_plane
    _wait_healthy(plane.postgres)
    for service in (_DB_INIT, _MIGRATE):
        prepared = _one_shot(plane, service)
        assert prepared.returncode == 0, service + prepared.stdout[-2000:] + prepared.stderr[-2000:]

    seeded = _python(plane, _SEED)
    runs = seeded["runs"]
    _age_and_attach(plane, seeded)

    policy = retention.load_policy()
    storage = _storage(plane, policy)
    before = _json(plane, _COUNTS)

    dry = retention.run(storage, policy, apply=False, emit=lambda _: None)
    assert _json(plane, _COUNTS) == before, "dry-run이 storage를 바꿨다"
    plan = dry["plan"]
    assert plan["runs"]["eligible"] == len(_DELETED)
    assert plan["runs"]["groups"] == len(_DELETED) - 1  # 계보 ③은 두 run이 한 묶음
    assert plan["ticks"]["selectors"] == 3 and plan["ticks"]["ticks"] == 9, plan["ticks"]
    assert plan["ticks"]["by_instigator_state"] == {
        "STOPPED": {"selectors": 1, "ticks": 3}, "DECLARED_IN_CODE": {"selectors": 1, "ticks": 3},
        "ABSENT": {"selectors": 1, "ticks": 3},
    }
    assert sorted(row["name"] for row in plan["ticks"]["detail"]) == [
        "absent_sensor", "declared_in_code_stale", "stopped_sensor"]

    events: list[dict[str, Any]] = []
    applied = retention.run(storage, policy, apply=True, emit=events.append)
    deleted = applied["deleted"]
    assert deleted["runs"] == len(_DELETED) and deleted["ticks"] == 9
    assert deleted["asset_event_tags"] == 1 and deleted["asset_check_executions"] == 2
    assert (deleted["runs"], deleted["event_logs"], deleted["run_tags"]) == (
        plan["runs"]["eligible"], plan["runs"]["event_logs"], plan["runs"]["run_tags"])
    assert all(event["event"] == "batch" for event in events)

    assert _json(plane, _ORPHANS) == {
        "event_logs": 0, "run_tags": 0, "asset_event_tags": 0, "asset_check_executions": 0, "lineage": 0}

    after = _python(plane, _VERIFY, json.dumps(seeded))
    assert {name for name, present in after["present"].items() if not present} == _DELETED
    # 자산의 최신 materialization run은 남고, Dagster가 그것을 최신으로 읽는다. partition 둘 다 그대로 보인다.
    assert after["latest_run"] == runs["latest_materialization"]
    assert after["partitions"] == ["2026-01", "2026-02"]
    # 계보 ②는 묶음째 남아 run group 조회가 깨지지 않는다(root가 없으면 Dagster가 예외를 낸다).
    assert after["lineage2_group"] == 4
    assert all(count > 0 for count in after["logs"].values())
    assert after["ticks"] == {"stopped_sensor": 0, "stopped_sensor_recent": 3, "running_monthly_schedule": 3,
                              "declared_in_code_stale": 0, "declared_in_code_live": 3, "absent_sensor": 0}
    assert after["all_runs"] == len(runs) - len(_DELETED)

    again = retention.run(storage, policy, apply=True, emit=lambda _: None)
    assert again["deleted"] == {key: 0 for key in again["deleted"]}
