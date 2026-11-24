"""공용 Dagster storage 보존 정리(ADR-66) — 설정·흐름·SQL 모양의 단위 시험.

실제 Dagster storage 위의 삭제 결과(고아 없음·자산 상태 보존)는
`test_dagster_storage_retention_integration.py`가 실제 이미지로 본다.
"""

from __future__ import annotations

import configparser
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from kor_travel_docker_manager import dagster_storage_retention as retention

ROOT = Path(__file__).resolve().parents[2]


def _document() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "config/dagster-storage-retention.yml").read_text(encoding="utf-8"))


def _policy(**overrides: Any) -> retention.Policy:
    policy = retention.parse_policy(_document())
    return retention.Policy(**{**policy.__dict__, **overrides})


# --------------------------------------------------------------------------- 설정


def test_committed_policy_is_the_owner_decision() -> None:
    policy = retention.load_policy(ROOT / "config/dagster-storage-retention.yml")
    assert (policy.success_days, policy.failure_days, policy.stopped_instigator_tick_days) == (90, 180, 14)
    assert policy.run_classes == (("SUCCESS", 90), ("FAILURE", 180), ("CANCELED", 180))
    # 한 배치는 statement timeout 안에, 한 번 실행은 systemd 상한 안에 끝나야 한다.
    service = (ROOT / "deploy/systemd/ktdm-dagster-storage-retention.service").read_text(encoding="utf-8")
    limit = re.search(r"^TimeoutStartSec=(\d+)min$", service, re.M)
    assert limit and int(limit.group(1)) * 60 > policy.max_runtime_seconds + policy.statement_timeout_seconds + 30


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("ticks"),
    lambda d: d.update(extra=1),
    lambda d: d.update(version=2),
    lambda d: d.update(version=True),
    lambda d: d["runs"].update(success_days=0),
    lambda d: d["runs"].update(success_days=True),
    lambda d: d["runs"].update(success_days="90"),
    lambda d: d["runs"].update(success_days=90.0),
    lambda d: d["runs"].update(failure_days=30),
    lambda d: d["runs"].pop("failure_days"),
    lambda d: d["ticks"].update(stopped_instigator_days=-1),
    lambda d: d["batch"].update(pause_seconds=0),
    lambda d: d["batch"].update(unknown=1),
    lambda d: d["timeouts"].update(lock_seconds=120),
    lambda d: d["timeouts"].pop("plan_statement_seconds"),
])
def test_invalid_policy_is_refused(mutate: Any) -> None:
    document = _document()
    mutate(document)
    with pytest.raises(retention.RetentionError):
        retention.parse_policy(document)


def test_pinned_dagster_version_matches_the_host_image() -> None:
    """SQL은 Dagster 1.13.24 schema를 보고 썼다. host 이미지의 Dagster를 올리면 이 시험이 재검토를 요구한다."""

    requirements = (ROOT / "docker/dagster-host/requirements.txt").read_text(encoding="utf-8")
    assert re.search(rf"^dagster=={re.escape(retention.DAGSTER_VERSION)} ", requirements, re.M)


def test_timer_is_weekly_at_dawn_kst() -> None:
    timer = configparser.ConfigParser()
    timer.read(ROOT / "deploy/systemd/ktdm-dagster-storage-retention.timer", encoding="utf-8")
    assert timer["Timer"]["OnCalendar"] == "Sun *-*-* 06:10:00 Asia/Seoul"
    assert timer["Timer"]["Persistent"] == "true"
    service = configparser.ConfigParser()
    service.read(ROOT / "deploy/systemd/ktdm-dagster-storage-retention.service", encoding="utf-8")
    command = service["Service"]["ExecStart"].split()
    assert command[1:] == ["-I", "-m", "kor_travel_docker_manager.dagster_storage_retention", "--apply"]


# --------------------------------------------------------------------------- storage 호출


class FakeRunner:
    """psql 호출을 기록하고 SQL 종류별로 정해진 결과를 돌려준다."""

    def __init__(self, responses: dict[str, list[Any]]) -> None:
        self.responses = {key: list(values) for key, values in responses.items()}
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        script = kwargs["input"]
        self.calls.append((argv, script))
        kind = next(
            name for name, sql in (
                ("preflight", retention.PREFLIGHT_SQL), ("plan_runs", retention.PLAN_RUNS_SQL),
                ("delete_runs", retention.DELETE_RUNS_SQL), ("selectors", retention.STALE_SELECTORS_SQL),
                ("delete_ticks", retention.DELETE_TICKS_SQL),
            ) if sql.strip() in script
        )
        queue = self.responses[kind]
        value = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(value, Exception):
            return subprocess.CompletedProcess(argv, 1, "", f"ERROR: {value}\n")
        return subprocess.CompletedProcess(argv, 0, json.dumps(value) + "\n", "")

    def kinds(self) -> list[str]:
        out = []
        for _, script in self.calls:
            for name, sql in (("preflight", retention.PREFLIGHT_SQL), ("plan_runs", retention.PLAN_RUNS_SQL),
                              ("delete_runs", retention.DELETE_RUNS_SQL),
                              ("selectors", retention.STALE_SELECTORS_SQL),
                              ("delete_ticks", retention.DELETE_TICKS_SQL)):
                if sql.strip() in script:
                    out.append(name)
        return out


_PREFLIGHT_OK = {"revision": retention.DAGSTER_STORAGE_REVISION, "run_ahead_seconds": -5.0, "tick_ahead_seconds": -1.0}
_SELECTORS = {
    "selectors": [
        {"selector": "a" * 40, "ticks": 7, "instigator": "DECLARED_IN_CODE"},
        {"selector": "b" * 40, "ticks": 2, "instigator": "ABSENT"},
    ],
    "legacy_null_selector_ticks": 0,
}
_NO_RUNS = {"runs": 0, "event_logs": 0, "run_tags": 0, "asset_event_tags": 0, "asset_check_executions": 0}


def _storage(runner: FakeRunner, policy: retention.Policy) -> retention.Storage:
    return retention.Storage(container="kor-travel-shared-postgres", database="dagster_shared",
                             admin="admin", port=11000, policy=policy, runner=runner)


def test_dry_run_only_reads_and_reports_the_plan() -> None:
    plan = {"aged": 3, "eligible": 2, "event_logs": 40, "run_tags": 20, "asset_event_tags": 1,
            "asset_check_executions": 0}
    runner = FakeRunner({"preflight": [_PREFLIGHT_OK], "plan_runs": [plan], "selectors": [_SELECTORS]})
    result = retention.run(_storage(runner, _policy()), _policy(), apply=False, emit=lambda _: None)

    assert all(script.startswith("BEGIN READ ONLY;\n") for _, script in runner.calls)
    # 전체 집계만 긴 상한(plan_statement_seconds)을 쓴다. 나머지 읽기는 배치 상한 그대로다.
    for _argv, script in runner.calls:
        expected = "600s" if retention.PLAN_RUNS_SQL.strip() in script else "60s"
        assert f"SET LOCAL statement_timeout = '{expected}';" in script
    assert "delete_runs" not in runner.kinds() and "delete_ticks" not in runner.kinds()
    assert result["mode"] == "dry-run" and result["ok"] is True
    assert result["plan"]["runs"]["FAILURE"] == {"days": 180, **plan}
    assert result["plan"]["ticks"] == {
        "days": 14, "selectors": 2, "ticks": 9,
        "by_instigator_state": {"DECLARED_IN_CODE": {"selectors": 1, "ticks": 7}, "ABSENT": {"selectors": 1, "ticks": 2}},
        "legacy_null_selector_ticks_untouched": 0,
    }


def test_apply_deletes_ticks_then_runs_in_bounded_batches() -> None:
    policy = _policy(tick_batch=5, run_batch=2)
    runner = FakeRunner({
        "preflight": [_PREFLIGHT_OK],
        "selectors": [_SELECTORS],
        # 첫 selector: 5(가득) → 2(덜 참, 끝). 둘째: 2.
        "delete_ticks": [{"ticks": 5}, {"ticks": 2}, {"ticks": 2}],
        # SUCCESS: 2(가득) → 1. FAILURE: 0. CANCELED: 0.
        "delete_runs": [{**_NO_RUNS, "runs": 2, "event_logs": 30, "run_tags": 12},
                        {**_NO_RUNS, "runs": 1, "event_logs": 9, "run_tags": 6},
                        _NO_RUNS, _NO_RUNS],
    })
    events: list[dict[str, Any]] = []
    sleeps: list[float] = []
    result = retention.run(_storage(runner, policy), policy, apply=True, emit=events.append, sleep=sleeps.append)

    assert runner.kinds() == ["preflight", "selectors", "delete_ticks", "delete_ticks", "delete_ticks",
                              "delete_runs", "delete_runs", "delete_runs", "delete_runs"]
    assert result["deleted"] == {"ticks": 9, "runs": 3, "event_logs": 39, "run_tags": 18,
                                 "asset_event_tags": 0, "asset_check_executions": 0}
    assert result["budget_exhausted"] is False
    # 가득 찬 배치 뒤에만 쉰다.
    assert sleeps == [policy.pause_seconds, policy.pause_seconds]
    assert [e["phase"] for e in events if e["event"] == "batch"] == ["ticks"] * 3 + ["runs"] * 4
    # 쓰기 배치는 쓰기 트랜잭션이고 timeout을 건다.
    for _argv, script in runner.calls:
        if "DELETE FROM" in script:
            assert script.startswith("BEGIN;\nSET LOCAL statement_timeout = '60s';\nSET LOCAL lock_timeout = '5s';\n")
            assert script.rstrip().endswith("COMMIT;")
    # 변수는 argv의 --set으로만 간다. selector는 psql이 quote한다(:'selector').
    tick_call = next(argv for argv, script in runner.calls if retention.DELETE_TICKS_SQL.strip() in script)
    assert f"selector={'a' * 40}" in tick_call and "batch=5" in tick_call and "tick_days=14" in tick_call
    run_call = next(argv for argv, script in runner.calls if retention.DELETE_RUNS_SQL.strip() in script)
    assert {"status=SUCCESS", "days=90", "success_days=90", "failure_days=180", "batch=2"} <= set(run_call)


def test_runtime_budget_stops_and_reports_not_a_failure() -> None:
    policy = _policy(tick_batch=1, max_runtime_seconds=10)
    runner = FakeRunner({"preflight": [_PREFLIGHT_OK], "selectors": [_SELECTORS], "delete_ticks": [{"ticks": 1}],
                         "delete_runs": [_NO_RUNS]})
    now = iter(range(0, 1000, 4))
    result = retention.run(_storage(runner, policy), policy, apply=True, emit=lambda _: None,
                           clock=lambda: float(next(now)), sleep=lambda _: None)
    assert result["ok"] is True and result["budget_exhausted"] is True
    assert "delete_runs" not in runner.kinds()


@pytest.mark.parametrize("header,needle", [
    ({**_PREFLIGHT_OK, "revision": "f495c27d5019"}, "schema"),
    ({**_PREFLIGHT_OK, "revision": None}, "schema"),
    ({**_PREFLIGHT_OK, "run_ahead_seconds": 9 * 3600.0}, "UTC"),
    ({**_PREFLIGHT_OK, "tick_ahead_seconds": 9 * 3600.0}, "UTC"),
])
def test_preflight_refuses_unreviewed_schema_or_non_utc_clock(header: dict[str, Any], needle: str) -> None:
    runner = FakeRunner({"preflight": [header], "selectors": [_SELECTORS], "delete_ticks": [{"ticks": 0}],
                         "delete_runs": [_NO_RUNS]})
    with pytest.raises(retention.RetentionError, match=needle):
        retention.run(_storage(runner, _policy()), _policy(), apply=True, emit=lambda _: None)
    assert runner.kinds() == ["preflight"]


def test_a_failed_batch_stops_the_run() -> None:
    runner = FakeRunner({"preflight": [_PREFLIGHT_OK], "selectors": [_SELECTORS],
                         "delete_ticks": [RuntimeError("canceling statement due to lock timeout")]})
    with pytest.raises(retention.RetentionError, match="lock timeout"):
        retention.run(_storage(runner, _policy()), _policy(), apply=True, emit=lambda _: None)
    assert runner.kinds() == ["preflight", "selectors", "delete_ticks"]


def test_a_malformed_selector_is_skipped_not_interpolated() -> None:
    selectors = {"selectors": [{"selector": "x'; DROP TABLE runs; --", "ticks": 1, "instigator": "ABSENT"}],
                 "legacy_null_selector_ticks": 0}
    runner = FakeRunner({"preflight": [_PREFLIGHT_OK], "selectors": [selectors], "delete_runs": [_NO_RUNS]})
    events: list[dict[str, Any]] = []
    retention.run(_storage(runner, _policy()), _policy(), apply=True, emit=events.append)
    assert "delete_ticks" not in runner.kinds()
    assert events[0]["event"] == "skip"


def test_query_variables_are_validated() -> None:
    runner = FakeRunner({"preflight": [_PREFLIGHT_OK]})
    storage = _storage(runner, _policy())
    with pytest.raises(retention.RetentionError):
        storage.query("SELECT 1", {"bad-name": 1}, write=False)
    with pytest.raises(retention.RetentionError):
        storage.query("SELECT 1", {"flag": True}, write=False)


# --------------------------------------------------------------------------- SQL 모양


def test_run_deletion_covers_every_run_keyed_table_of_the_reviewed_schema() -> None:
    """Dagster 1.13.24에서 run_id로 run을 가리키는 표: run_tags(FK cascade)·event_logs·asset_event_tags(event_id)·
    asset_check_executions·concurrency_slots·pending_steps. 앞의 넷은 지우고 뒤의 둘은 쥔 run을 지우지 않는다."""

    sql = retention.DELETE_RUNS_SQL
    for table in ("asset_event_tags", "asset_check_executions", "event_logs", "runs"):
        assert re.search(rf"DELETE FROM {table}\b", sql), table
    for table in ("concurrency_slots", "pending_steps"):
        assert f"FROM {table} " in sql and f"DELETE FROM {table}" not in sql
    assert "FOR UPDATE OF r SKIP LOCKED" in sql and "LIMIT :batch" in sql
    # 종료 상태만: status는 변수 하나와 같아야 하고, 변수 값은 Policy.run_classes에서만 온다.
    assert "r.status = :'status'" in sql
    assert {status for status, _ in _policy().run_classes} == {"SUCCESS", "FAILURE", "CANCELED"}


def test_dry_run_and_apply_share_the_same_protections() -> None:
    protections = retention._RUN_PROTECTIONS.strip()
    assert protections in retention.PLAN_RUNS_SQL and protections in retention.DELETE_RUNS_SQL
    for needle in ("asset_keys", "dagster/parent_run_id", "dagster/root_run_id", "bulk_actions",
                   "asset_check_executions", "n.partition = e.partition"):
        assert needle in protections


def test_tick_deletion_rechecks_running_and_recent_at_delete_time() -> None:
    sql = retention.DELETE_TICKS_SQL
    assert "i.status = 'RUNNING'" in sql
    assert "recent.timestamp >=" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    listing = retention.STALE_SELECTORS_SQL
    assert "IS DISTINCT FROM 'RUNNING'" in listing and "recent.timestamp >=" in listing


def test_main_reports_already_running(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                      tmp_path: Path) -> None:
    from contextlib import contextmanager

    @contextmanager
    def busy():
        yield False

    monkeypatch.setattr(retention, "single_instance", busy)
    assert retention.main(["--config", str(ROOT / "config/dagster-storage-retention.yml")]) == 0
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line == {"event": "summary", "ok": True, "state": "already_running"}


def test_main_reports_failure_without_secrets(monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    from contextlib import contextmanager

    @contextmanager
    def free():
        yield True

    def unreachable(_policy: retention.Policy) -> retention.Storage:
        raise retention.RetentionError("공용 PostgreSQL 컨테이너를 확인할 수 없습니다")

    monkeypatch.setattr(retention, "single_instance", free)
    monkeypatch.setattr(retention, "discover_storage", unreachable)
    assert retention.main(["--apply", "--config", str(ROOT / "config/dagster-storage-retention.yml")]) == 1
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["ok"] is False and line["mode"] == "apply" and line["error"] == "RetentionError"


def test_single_instance_lock_excludes_a_second_holder(tmp_path: Path) -> None:
    path = tmp_path / "retention.lock"
    with retention.single_instance(path) as first:
        assert first is True
        with retention.single_instance(path) as second:
            assert second is False
    with retention.single_instance(path) as again:
        assert again is True
