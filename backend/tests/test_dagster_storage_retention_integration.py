"""공용 Dagster storage 보존 정리(ADR-66)를 **실제 PostgreSQL·Dagster 1.13.24 storage** 위에서 돌린다.

단위 시험(`test_dagster_storage_retention.py`)은 흐름과 SQL 모양만 본다. 여기서는 Dagster 자신의 API로 run·event·
asset materialization·instigator·tick을 만들고 나이를 SQL로 되돌린 뒤 정리를 돌려

- 지울 것만 지우는가(종료 상태·나이·보호 규칙),
- 고아가 남지 않는가(event_logs·run_tags·asset_event_tags·asset_check_executions의 run/event 참조),
- 정리 뒤 Dagster가 storage를 그대로 읽는가(run·로그·자산 최신 materialization·tick 조회),
- dry-run이 아무것도 바꾸지 않고 apply와 같은 수를 세는가, 두 번째 apply가 0인가

를 본다. 격리 plane은 `test_dagster_shared_storage_integration.py`의 fixture(정본 compose의 공용 PG·db-init·storage
migrate, 격리 tag 이미지)를 그대로 쓰고 gate도 같다(`KTDM_REQUIRE_DOCKER_INTEGRATION`).
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
from dagster import AssetKey, AssetMaterialization, DagsterInstance
from dagster._core.events import DagsterEvent, DagsterEventType, StepMaterializationData
from dagster._core.events.log import EventLogEntry
from dagster._core.remote_origin import (
    GrpcServerCodeLocationOrigin, RemoteInstigatorOrigin, RemoteRepositoryOrigin)
from dagster._core.scheduler.instigation import (
    InstigatorState, InstigatorStatus, InstigatorType, ScheduleInstigatorData, TickData, TickStatus)
from dagster._core.storage.dagster_run import DagsterRunStatus
from dagster._core.test_utils import create_run_for_test

ids = {}
with DagsterInstance.get() as instance:
    def run(name, status, **kw):
        r = create_run_for_test(instance, job_name="retention_probe", status=status, **kw)
        instance.report_engine_event(f"{name} step", r)
        instance.report_engine_event(f"{name} done", r)
        ids[name] = r.run_id
        return r

    def materialize(r, version):
        instance.store_event(EventLogEntry(
            error_info=None, level="debug", user_message="", run_id=r.run_id, timestamp=time.time(),
            step_key="s", job_name=r.job_name,
            dagster_event=DagsterEvent(
                event_type_value=DagsterEventType.ASSET_MATERIALIZATION.value, job_name=r.job_name,
                event_specific_data=StepMaterializationData(AssetMaterialization(
                    asset_key=AssetKey("x"), tags={"dagster/data_version": version})))))

    old_mat = run("old_success_with_old_materialization", DagsterRunStatus.SUCCESS)
    materialize(old_mat, "v1")
    latest_mat = run("old_success_with_latest_materialization", DagsterRunStatus.SUCCESS)
    materialize(latest_mat, "v2")
    run("old_success_plain", DagsterRunStatus.SUCCESS)
    parent = run("old_success_parent_of_recent", DagsterRunStatus.SUCCESS)
    # 재실행·자동 재시도(`create_reexecuted_run`)는 계보를 필드와 tag 둘 다에 둔다 — 같은 모양으로 만든다.
    run("recent_child", DagsterRunStatus.SUCCESS, parent_run_id=parent.run_id, root_run_id=parent.run_id,
        tags={"dagster/parent_run_id": parent.run_id, "dagster/root_run_id": parent.run_id})
    run("failure_100_days", DagsterRunStatus.FAILURE)
    run("failure_200_days", DagsterRunStatus.FAILURE)
    run("canceled_200_days", DagsterRunStatus.CANCELED)
    run("recent_success", DagsterRunStatus.SUCCESS)
    run("started_200_days", DagsterRunStatus.STARTED)

    location = GrpcServerCodeLocationOrigin(host="127.0.0.1", port=4000, location_name="probe.location")
    repository = RemoteRepositoryOrigin(code_location_origin=location, repository_name="__repository__")
    ticks = {}
    for name, kind, status in (
        ("stopped_sensor", InstigatorType.SENSOR, InstigatorStatus.STOPPED),
        ("stopped_sensor_recent", InstigatorType.SENSOR, InstigatorStatus.STOPPED),
        ("running_monthly_schedule", InstigatorType.SCHEDULE, InstigatorStatus.RUNNING),
        ("declared_in_code_sensor", InstigatorType.SENSOR, InstigatorStatus.DECLARED_IN_CODE),
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
    present = {name: instance.get_run_by_id(run_id) is not None for name, run_id in request["runs"].items()}
    logs = {name: len(instance.all_logs(run_id)) for name, run_id in request["runs"].items() if present[name]}
    latest = instance.get_latest_materialization_event(AssetKey("x"))
    records = instance.get_asset_records([AssetKey("x")])
    ticks = {name: len(instance.get_ticks(t["origin"], t["selector"])) for name, t in request["ticks"].items()}
    print(json.dumps({
        "present": present, "logs": logs, "latest_run": latest.run_id if latest else None,
        "asset_records": len(records), "ticks": ticks, "all_runs": len(instance.get_run_records()),
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
      SELECT 1 FROM runs r WHERE r.run_id = c.run_id)))
"""

_COUNTS = """
SELECT json_build_object(
  'runs', (SELECT count(*) FROM runs), 'event_logs', (SELECT count(*) FROM event_logs),
  'run_tags', (SELECT count(*) FROM run_tags), 'asset_event_tags', (SELECT count(*) FROM asset_event_tags),
  'asset_check_executions', (SELECT count(*) FROM asset_check_executions), 'job_ticks', (SELECT count(*) FROM job_ticks))
"""


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


def test_retention_deletes_only_expired_runs_and_stale_ticks_without_orphans(
    isolated_plane: _Plane,  # noqa: F811 - 가져온 fixture
) -> None:
    plane = isolated_plane
    _wait_healthy(plane.postgres)
    for service in (_DB_INIT, _MIGRATE):
        prepared = _one_shot(plane, service)
        assert prepared.returncode == 0, service + prepared.stdout[-2000:] + prepared.stderr[-2000:]

    seeded = _python(plane, _SEED)
    runs, ticks = seeded["runs"], seeded["ticks"]
    ages = {
        "old_success_with_old_materialization": 120, "old_success_with_latest_materialization": 100,
        "old_success_plain": 91, "old_success_parent_of_recent": 95, "recent_child": 3,
        "failure_100_days": 100, "failure_200_days": 200, "canceled_200_days": 200,
        "recent_success": 1, "started_200_days": 200,
    }
    statements = [
        f"UPDATE runs SET update_timestamp = (now() AT TIME ZONE 'UTC') - make_interval(days => {days}) "
        f"WHERE run_id = '{runs[name]}'"
        for name, days in ages.items()
    ]
    # asset check: 오래된 run의 옛 실행(지운다)과 보호된 run의 최신 실행(남긴다).
    statements += [
        "INSERT INTO asset_check_executions (asset_key, check_name, run_id, execution_status) VALUES "
        f"('[\"x\"]', 'c', '{runs['old_success_with_old_materialization']}', 'SUCCEEDED'), "
        f"('[\"x\"]', 'c', '{runs['old_success_with_latest_materialization']}', 'SUCCEEDED')",
    ]
    tick_ages = {"stopped_sensor": [20, 21, 22], "stopped_sensor_recent": [1, 20, 21],
                 "running_monthly_schedule": [20, 21, 22], "declared_in_code_sensor": [15, 16, 30],
                 "absent_sensor": [40, 41, 42]}
    for name, days in tick_ages.items():
        for tick_id, age in zip(ticks[name]["ids"], days, strict=True):
            statements.append(
                f"UPDATE job_ticks SET timestamp = (now() AT TIME ZONE 'UTC') - make_interval(days => {age}) "
                f"WHERE id = {int(tick_id)}"
            )
    _sql_in(plane, plane.database, "; ".join(statements))

    policy = retention.load_policy()
    storage = _storage(plane, policy)
    before = _json(plane, _COUNTS)

    dry = retention.run(storage, policy, apply=False, emit=lambda _: None)
    assert _json(plane, _COUNTS) == before, "dry-run이 storage를 바꿨다"
    plan = dry["plan"]
    assert plan["runs"]["SUCCESS"]["aged"] == 4 and plan["runs"]["SUCCESS"]["eligible"] == 2
    assert plan["runs"]["FAILURE"]["eligible"] == 1 and plan["runs"]["CANCELED"]["eligible"] == 1
    assert plan["ticks"]["selectors"] == 3 and plan["ticks"]["ticks"] == 9
    assert plan["ticks"]["by_instigator_state"] == {
        "STOPPED": {"selectors": 1, "ticks": 3}, "DECLARED_IN_CODE": {"selectors": 1, "ticks": 3},
        "ABSENT": {"selectors": 1, "ticks": 3},
    }

    events: list[dict[str, Any]] = []
    applied = retention.run(storage, policy, apply=True, emit=events.append)
    deleted = applied["deleted"]
    assert deleted["runs"] == 4 and deleted["ticks"] == 9
    assert deleted["asset_event_tags"] == 1 and deleted["asset_check_executions"] == 1
    planned = {key: sum(plan["runs"][s][key] for s in ("SUCCESS", "FAILURE", "CANCELED"))
               for key in ("eligible", "event_logs", "run_tags", "asset_event_tags", "asset_check_executions")}
    assert (deleted["runs"], deleted["event_logs"], deleted["run_tags"]) == (
        planned["eligible"], planned["event_logs"], planned["run_tags"])
    assert all(event["event"] == "batch" for event in events)

    assert _json(plane, _ORPHANS) == {
        "event_logs": 0, "run_tags": 0, "asset_event_tags": 0, "asset_check_executions": 0}

    after = _python(plane, _VERIFY, json.dumps(seeded))
    gone = {"old_success_with_old_materialization", "old_success_plain", "failure_200_days", "canceled_200_days"}
    assert {name for name, present in after["present"].items() if not present} == gone
    # 자산의 최신 materialization run은 남고, Dagster가 그것을 최신으로 읽는다.
    assert after["latest_run"] == runs["old_success_with_latest_materialization"]
    assert after["asset_records"] == 1
    assert all(count > 0 for count in after["logs"].values())
    assert after["ticks"] == {"stopped_sensor": 0, "stopped_sensor_recent": 3, "running_monthly_schedule": 3,
                              "declared_in_code_sensor": 0, "absent_sensor": 0}
    assert after["all_runs"] == len(ages) - len(gone)

    again = retention.run(storage, policy, apply=True, emit=lambda _: None)
    assert again["deleted"] == {key: 0 for key in again["deleted"]}
