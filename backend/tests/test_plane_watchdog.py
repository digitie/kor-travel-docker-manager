"""공용 Dagster plane 수집 감시(plane watchdog) — 규칙별 단위 시험과 감사 당시 실제 사건 재생.

fixture(`fixtures/plane_watchdog/`)는 2026-11-24 두 호스트의 Dagster storage에서 읽기 전용으로 뽑은
실물이다: odroid 특보 이벤트 11-18 07:05Z부터 100% 실패, odroid weather location 11-19 17:15~20:20 정지,
n150 PinVi code-server fd 고갈(11-13 19:36~) 동안의 launch 실패와 sensor tick 실패. 공항 통계 사전집계
규칙은 odroid transport 공항 수집 run 40개(11-24 01:21~07:26Z)의 실물 모양·간격 위에 시나리오 태그를 덧씌운다
(transport #283 태그는 그 시점 아직 배포 전이었다).
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from kor_travel_docker_manager.services import plane_watchdog as wd
from kor_travel_docker_manager.services import plane_watchdog_diagnostics as diag
from kor_travel_docker_manager.services import plane_watchdog_probe as pr
from kor_travel_docker_manager.services.cron_period import cron_period

FIXTURES = Path(__file__).parent / "fixtures" / "plane_watchdog"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _epoch(text: str) -> float:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config() -> wd.WatchdogConfig:
    return wd.load_config(REPO_ROOT / "config" / "plane-watchdog.yml")


def _runs(name: str, now: float) -> list[pr.RunRecord]:
    """그 시각까지 종료된 run만(감시자는 미래를 못 본다)."""

    return [pr.run_from_row(row) for row in _fixture(name)["runs"] if row["updated"] <= now]


def _stall_instigators(now: float) -> list[pr.Instigator]:
    data = _fixture("weather_location_stall_20261119.json")
    result = []
    for row in data["instigators"]:
        last = _epoch(row["last_before"])
        if row["first_after"] and _epoch(row["first_after"]) <= now:
            last = _epoch(row["first_after"])
        result.append(pr.Instigator(
            selector=row["name"], type=row["type"], status=row["status"], name=row["name"],
            location=data["location"], cron=row.get("cron"), min_interval=row.get("min_interval"),
            last_tick=last if last <= now else None,
        ))
    return result


# ---------------------------------------------------------------------------
# 설정·cron
# ---------------------------------------------------------------------------


def test_repository_config_parses_and_keeps_external_delivery_off(config):
    assert config.interval_seconds >= 30
    assert set(config.rules) == set(wd._RULE_KEYS)
    # 발송 채널은 소유자 결정이다 — 기본은 꺼져 있어야 한다.
    assert config.notifiers["webhook"]["enabled"] is False
    # fd 경보는 프로세스별 50% 주의·80% 위험(부모 분석, 평소 13~24개).
    assert config.rule("dagster_fd_usage")["warning_ratio"] == 0.5
    assert config.rule("dagster_fd_usage")["critical_ratio"] == 0.8


def test_config_rejects_unknown_and_missing_keys(config):
    document = json.loads(json.dumps({
        "version": 1, "interval_seconds": 120, "storage": dict(config.storage),
        "rules": {name: dict(value) for name, value in config.rules.items()},
        "diagnostics": dict(config.diagnostics), "notifiers": {"webhook": dict(config.notifiers["webhook"])},
    }))
    wd.parse_config(document)
    document["rules"]["io_pressure"]["typo"] = 1
    with pytest.raises(wd.ConfigError, match="모름"):
        wd.parse_config(document)
    del document["rules"]["io_pressure"]["typo"]
    document["rules"]["io_pressure"]["for_evaluations"] = True
    with pytest.raises(wd.ConfigError, match="형식"):
        wd.parse_config(document)


def test_missing_config_is_visible_not_silent(tmp_path):
    with pytest.raises(wd.ConfigError):
        wd.load_config(tmp_path / "nope.yml")
    watchdog = wd.PlaneWatchdog(None, config_error="plane-watchdog.yml을 읽을 수 없습니다")
    payload = watchdog.status_payload()
    assert payload["enabled"] is False
    assert payload["summary"]["state"] == "error"
    assert "설정 오류" in payload["summary"]["text"]


@pytest.mark.parametrize(("expression", "minimum", "maximum"), [
    ("*/5 * * * *", 300, 300),
    ("15 2,5,8,11,14,17,20,23 * * *", 3 * 3600, 3 * 3600),
    ("30 6,18 * * *", 12 * 3600, 12 * 3600),
    ("15 4 4 * *", 28 * 86400, 31 * 86400),
    ("0 3 */3 * *", 86400, 3 * 86400),
    ("@hourly", 3600, 3600),
    ("0 9 * * MON-FRI", 86400, 3 * 86400),
    ('["0 * * * *", "30 3 * * *"]', 1800, 3600),
])
def test_cron_period_is_timezone_free_gap(expression, minimum, maximum):
    period = cron_period(expression)
    assert period is not None
    assert (period.min_gap_seconds, period.max_gap_seconds) == (minimum, maximum)


@pytest.mark.parametrize("expression", ["", "not cron", "61 * * * *", "* * *", "0 0 30 2 *"])
def test_cron_period_refuses_what_it_cannot_read(expression):
    assert cron_period(expression) is None


# ---------------------------------------------------------------------------
# location tick 정지 — odroid weather 11-19 17:15~20:20 재생
# ---------------------------------------------------------------------------


def test_weather_location_stall_fires_after_thirty_minutes_and_clears_on_resume(config):
    rule = config.rule("location_tick_stall")
    before, _ = wd.evaluate_location_stalls(_stall_instigators(_epoch("2026-11-19T17:44:00Z")), _epoch("2026-11-19T17:44:00Z"), rule)
    assert before == []  # 마지막 tick 17:15:32 → 28분 30초, 아직 임계 30분 안

    now = _epoch("2026-11-19T17:50:00Z")
    findings, states = wd.evaluate_location_stalls(_stall_instigators(now), now, rule)
    assert [item.subject for item in findings] == ["kortravelweather_dagster.definitions"]
    assert findings[0].severity == "critical"
    assert findings[0].evidence["last_tick_at"] == "2026-11-19T17:15:32Z"
    assert states[0].threshold_seconds == 1800  # 60초 sensor × 3 < 30분 → 30분

    after = _epoch("2026-11-19T20:22:00Z")
    assert wd.evaluate_location_stalls(_stall_instigators(after), after, rule)[0] == []


def test_stopped_instigator_outside_lookback_does_not_keep_a_location_alive():
    old = pr.Instigator("s", "SCHEDULE", "STOPPED", "old", "loc", "*/5 * * * *", None, last_tick=0.0)
    assert wd.active_instigators([old], now=10 * 86400, lookback_hours=24) == []
    # DB가 STOPPED라고 적어도 최근에 tick했으면 활성이다(odroid transport 실측).
    ticking = pr.Instigator("t", "SCHEDULE", "STOPPED", "live", "loc", "*/5 * * * *", None, last_tick=100.0)
    assert wd.active_instigators([ticking], now=200.0, lookback_hours=24) == [ticking]


# ---------------------------------------------------------------------------
# schedule 주기 내 성공 없음 · 연속 실패 — odroid 특보 이벤트 11-18 재생
# ---------------------------------------------------------------------------


def _alert_schedule() -> pr.Instigator:
    schedule = _fixture("weather_regional_alert_runs_20261118.json")["schedule"]
    return pr.Instigator("sel", "SCHEDULE", "RUNNING", schedule["name"], schedule["location"],
                         schedule["cron"], None, last_tick=_epoch("2026-11-18T09:25:00Z"))


def _no_success(config, now: float) -> list[wd.Finding]:
    feed = _runs("weather_regional_alert_runs_20261118.json", now)
    return wd.evaluate_schedule_no_success(
        [_alert_schedule()], feed, {}, now, coverage_start=_epoch("2026-11-18T05:00:00Z"),
        rule=config.rule("schedule_no_success"), lookback_hours=24)


def test_regional_alert_events_no_success_within_three_cycles(config):
    # 창 = 3 × 5분 + 30분 = 45분. 07:00 성공이 창 안이면 울리지 않는다.
    assert _no_success(config, _epoch("2026-11-18T07:44:00Z")) == []
    findings = _no_success(config, _epoch("2026-11-18T07:52:00Z"))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.subject == "kortravelweather_dagster.definitions/regional_alert_events_every_five_minutes"
    assert finding.evidence["jobs"] == ["kma_regional_alert_events_job"]
    assert finding.evidence["last_success_at"].startswith("2026-11-18T07:00")


def test_window_older_than_coverage_needs_backfill_before_judging(config):
    daily = pr.Instigator("d", "SCHEDULE", "RUNNING", "daily", "loc", "0 3 * * *", None, last_tick=1.0)
    now = 10 * 86400.0
    failure = pr.RunRecord("r1", "job", "loc", "daily", "FAILURE", now - 3600, now - 3500, False)
    # 창(3일+30분)이 feed 범위(12시간)보다 길다 — 앞부분을 모르면 울리지 않는다.
    assert wd.evaluate_schedule_no_success([daily], [failure], {}, now, now - 12 * 3600,
                                           config.rule("schedule_no_success"), 24) == []
    assert wd.schedules_needing_backfill([daily], now, now - 12 * 3600, config.rule("schedule_no_success"), 24) \
        == [("loc", "daily")]
    # backfill이 그 job의 수동 성공을 보여 주면 울리지 않는다(MCST 11-16 수동 성공 실측).
    manual = pr.RunRecord("r0", "job", "loc", None, "SUCCESS", now - 2 * 86400, now - 2 * 86400 + 60, False)
    assert wd.evaluate_schedule_no_success([daily], [failure], {("loc", "daily"): [manual, failure]}, now,
                                           now - 12 * 3600, config.rule("schedule_no_success"), 24) == []
    findings = wd.evaluate_schedule_no_success([daily], [failure], {("loc", "daily"): [failure]}, now,
                                               now - 12 * 3600, config.rule("schedule_no_success"), 24)
    assert [item.rule for item in findings] == ["schedule_no_success"]


def test_consecutive_failures_fire_on_fifth_failure(config):
    rule = config.rule("consecutive_failures")
    fourth = _epoch("2026-11-18T07:21:30Z")
    assert wd.evaluate_consecutive_failures(_runs("weather_regional_alert_runs_20261118.json", fourth), fourth, rule) == []
    now = _epoch("2026-11-18T07:35:00Z")
    findings = wd.evaluate_consecutive_failures(_runs("weather_regional_alert_runs_20261118.json", now), now, rule)
    assert [item.subject for item in findings] == ["kortravelweather_dagster.definitions/kma_regional_alert_events_job"]
    assert findings[0].evidence["streak"] >= 5
    # schedule 경보가 같은 job을 덮으면 두 번 울리지 않는다.
    assert wd.evaluate_consecutive_failures(
        _runs("weather_regional_alert_runs_20261118.json", now), now, rule,
        {("kortravelweather_dagster.definitions", "kma_regional_alert_events_job")}) == []


# ---------------------------------------------------------------------------
# launch 실패 — n150 PinVi code-server fd 고갈 11-13 19:36~ 재생
# ---------------------------------------------------------------------------


def test_pinvi_launch_failure_streak_fires_on_fifth_launch_failure(config):
    rule = config.rule("launch_failure_streak")
    early = _epoch("2026-11-13T19:53:30Z")  # launch 실패 4건째(19:52:56)까지
    assert wd.evaluate_launch_failure_streak(_runs("launch_failure_runs_20261113.json", early), early, rule) == []
    now = _epoch("2026-11-13T19:57:30Z")
    findings = wd.evaluate_launch_failure_streak(
        _runs("launch_failure_runs_20261113.json", now), now, rule,
        {"pinvi.etl.definitions": "pinvi-dagster-code-server-latest"})
    assert [item.subject for item in findings] == ["pinvi.etl.definitions"]
    assert findings[0].evidence["first_failure_at"].startswith("2026-11-13T19:40")
    # 재시작은 제안만 한다 — 대상 컨테이너가 문구에 있다.
    assert "docker restart pinvi-dagster-code-server-latest" in findings[0].detail
    assert "자동 재시작 없음" in findings[0].detail


def test_plane_wide_launch_rate_is_diluted_early_which_is_why_the_streak_rule_exists(config):
    # 사건 첫 시간(19:30~20:30)은 다른 location의 성공에 묽어져 13/67(19%)로 20% 임계 아래다. 그래서
    # location별 연속 실패 규칙(위 시험, 19:57에 발화)이 따로 있다.
    now = _epoch("2026-11-13T20:30:00Z")
    rule = config.rule("launch_failure_rate")
    assert wd.evaluate_launch_failures(_runs("launch_failure_runs_20261113.json", now), now, rule) == []


def test_launch_failure_rate_fires_on_the_audited_hour(config):
    # 감사가 센 n150 2026-11-14 12:00~13:00Z: 종료 46건 중 launch 실패 28건(concierge 13, PinVi 15).
    now = _epoch("2026-11-14T13:00:00Z")
    runs = []
    for index in range(46):
        if index < 13:
            location, status, never = "ktc.dagster.definitions", "FAILURE", True
        elif index < 28:
            location, status, never = "pinvi.etl.definitions", "FAILURE", True
        elif index == 28:
            location, status, never = "kortravelmap.dagster.definitions", "FAILURE", False
        else:
            location, status, never = "kortravelmap.dagster.definitions", "SUCCESS", False
        runs.append(pr.RunRecord(f"r{index}", "job", location, None, status, now - 3500 + index, now - 3400 + index, never))
    [finding] = wd.evaluate_launch_failures(runs, now, config.rule("launch_failure_rate"))
    assert finding.evidence["launch_failures"] == 28 and finding.evidence["terminal_runs"] == 46
    assert finding.evidence["by_location"] == {"ktc.dagster.definitions": 13, "pinvi.etl.definitions": 15}
    # 정상 시간대(19:00~19:36)는 launch 실패가 없다.
    quiet = _epoch("2026-11-13T19:36:30Z")
    assert wd.evaluate_launch_failures(_runs("launch_failure_runs_20261113.json", quiet), quiet,
                                       config.rule("launch_failure_rate")) == []


def test_pinvi_sensor_ticks_failing_with_unreachable_code_server(config):
    data = _fixture("pinvi_sensor_tick_failures_20261114.json")
    instigators = [pr.instigator_from_row({**row, "selector": row["name"]}) for row in data["instigators"]]
    now = _epoch("2026-11-14T05:13:00Z")
    findings = wd.evaluate_tick_failures(instigators, now, config.rule("tick_failures"), 24,
                                         {"pinvi.etl.definitions": "pinvi-dagster-code-server-latest"})
    assert [item.subject for item in findings] == ["pinvi.etl.definitions"]
    finding = findings[0]
    # 마지막 3 tick이 모두 FAILURE인 infra retry sensor 다섯. run failure sensor는 3번째가 SUCCESS라 빠진다.
    assert len(finding.evidence["instigators"]) == 5
    assert "pinvi_run_failure_sensor" not in finding.evidence["instigators"]
    assert finding.evidence["code_server_unreachable"] is True
    assert "docker restart pinvi-dagster-code-server-latest" in finding.detail


def test_tick_failure_recognises_emfile_text():
    assert wd.is_emfile("OSError: [Errno 24] Too many open files")
    assert wd.is_code_server_unreachable("dagster._core.errors.DagsterUserCodeUnreachableError: x")
    assert not wd.is_code_server_unreachable("kortravelcommon.deadline.DeadlineExceeded")


# ---------------------------------------------------------------------------
# 호스트: 디스크·PSI·fd
# ---------------------------------------------------------------------------


def test_disk_thresholds(config):
    rule = config.rule("disk_usage")
    gib = 1024**3
    observations = [
        pr.DiskObservation("/a", 100 * gib, 80 * gib, 20 * gib),
        pr.DiskObservation("/b", 100 * gib, 91 * gib, 9 * gib),
        pr.DiskObservation("/c", 100 * gib, 96 * gib, 4 * gib),
    ]
    findings = {item.subject: item.severity for item in wd.evaluate_disks(observations, rule)}
    assert findings == {"/b": "warning", "/c": "critical"}


def test_io_pressure_parse_and_sustain(config):
    sample = ("some avg10=15.67 avg60=7.36 avg300=2.16 total=1669483908436\n"
              "full avg10=14.74 avg60=6.90 avg300=31.01 total=1134767026336\n")
    pressure = pr.parse_pressure(sample)
    assert pressure["full"]["avg300"] == pytest.approx(31.01)
    findings = wd.evaluate_io_pressure(pressure, config.rule("io_pressure"))
    assert [item.rule for item in findings] == ["io_pressure"]
    book = wd.AlertBook()
    required = {"io_pressure": config.rule("io_pressure")["for_evaluations"]}
    events = [book.apply(findings, {"io_pressure"}, float(i), required) for i in range(3)]
    assert [len(item) for item in events] == [0, 0, 1]  # 세 번째 연속 관측에서 처음 낸다


def test_io_pressure_missing_on_kernel_without_psi(tmp_path):
    assert pr.read_io_pressure(str(tmp_path / "absent")) is None


def _fake_proc(root: Path, pid: int, fds: int, soft: int) -> None:
    (root / str(pid) / "fd").mkdir(parents=True)
    for index in range(fds):
        (root / str(pid) / "fd" / str(index)).write_text("")
    (root / str(pid) / "limits").write_text(
        "Limit                     Soft Limit           Hard Limit           Units\n"
        f"Max open files            {soft}                 524288               files\n")


def _docker_runner(tops: dict[str, str]):
    def runner(argv, **kwargs):
        if argv[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(argv, 0, "\n".join(tops) + "\n", "")
        if argv[:2] == ["docker", "top"]:
            return subprocess.CompletedProcess(argv, 0, "PID ARGS\n" + tops[argv[2]], "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    return runner


def test_fd_probe_judges_per_process_ratio_and_maps_location(tmp_path, config):
    _fake_proc(tmp_path, 11, 30, 1024)       # code-server start
    _fake_proc(tmp_path, 12, 700, 1024)      # dagster api grpc — 68%
    _fake_proc(tmp_path, 13, 900, 1048576)   # 더 많지만 한도가 커서 비율은 작다
    runner = _docker_runner({
        "pinvi-dagster-code-server-latest": (
            "11 /usr/local/bin/python /usr/local/bin/dagster code-server start -h 127.0.0.1 -p 12803 -m pinvi.etl.definitions\n"
            "12 /usr/local/bin/python -m dagster api grpc --lazy-load-user-code --location-name pinvi.etl.definitions\n"
            "13 /usr/local/bin/python -c helper\n"),
    })
    observations, unreadable = pr.read_dagster_fds(runner=runner, proc_root=tmp_path)
    assert unreadable == []
    [item] = observations
    assert (item.process_kind, item.pid, item.open_fds, item.nofile_soft_limit) == ("grpc", 12, 700, 1024)
    assert item.locations == ("pinvi.etl.definitions",)
    assert item.open_fds_total == 1630
    [finding] = wd.evaluate_fds(observations, config.rule("dagster_fd_usage"))
    assert finding.severity == "warning"
    critical = pr.ContainerFds("c", 900, 1024, 900, 1, "grpc", 12, ("pinvi.etl.definitions",))
    [finding] = wd.evaluate_fds([critical], config.rule("dagster_fd_usage"))
    assert finding.severity == "critical"
    assert "docker restart c" in finding.detail


def test_fd_probe_reports_unreadable_proc(tmp_path):
    runner = _docker_runner({"kor-travel-dagster-daemon": "99 /usr/local/bin/python kor-travel-dagster-daemon run\n"})
    observations, unreadable = pr.read_dagster_fds(runner=runner, proc_root=tmp_path)
    assert observations == [] and unreadable == ["kor-travel-dagster-daemon"]


# ---------------------------------------------------------------------------
# storage 질의
# ---------------------------------------------------------------------------


def test_storage_query_is_read_only_and_parses_json(monkeypatch):
    monkeypatch.setattr(pr.standalone_backup, "_discover_admin_role", lambda container: "shared_admin")
    monkeypatch.setattr(pr.standalone_backup, "_discover_port", lambda container: 11000)
    seen: dict[str, Any] = {}

    replies = iter([
        '[{"selector": "s", "type": "SENSOR", "status": "RUNNING", "name": "n", "location": "l", '
        '"min_interval": "60", "recent_ticks": [{"at": 10, "status": "FAILURE", "error": "e"}]}]\n',
        "[]\n",
        '[{"selector": "s"}]\n',
    ])

    def runner(argv, **kwargs):
        seen["argv"], seen["input"] = argv, kwargs["input"]
        return subprocess.CompletedProcess(argv, 0, next(replies), "")

    client = pr.StorageClient(statement_timeout_seconds=7, runner=runner)
    [item] = client.instigators()
    assert item.last_tick == 10 and item.recent_ticks == ((10.0, "FAILURE", "e"),)
    assert seen["input"].startswith("BEGIN READ ONLY;\nSET LOCAL statement_timeout = '7s';")
    assert "--user" in seen["argv"] and "postgres" in seen["argv"]
    assert "PGAPPNAME=ktdm-plane-watchdog" in seen["argv"]
    assert "dagster_shared" in seen["argv"]
    # 비밀번호·DSN은 argv에도 입력에도 없다.
    assert not any("password" in part.lower() for part in seen["argv"])

    assert client.runs_since(123.5) == []
    assert "since=123.500" in seen["argv"]
    # 모양이 틀린 결과는 KeyError로 루프를 깨지 않고 측정 실패가 된다.
    with pytest.raises(pr.ProbeError, match="모양"):
        client.runs_since(1.0)


def test_storage_query_failure_is_a_probe_error(monkeypatch):
    monkeypatch.setattr(pr.standalone_backup, "_discover_admin_role", lambda container: "shared_admin")
    monkeypatch.setattr(pr.standalone_backup, "_discover_port", lambda container: 11000)
    client = pr.StorageClient(statement_timeout_seconds=7, runner=lambda argv, **kw: subprocess.CompletedProcess(
        argv, 3, "", "ERROR:  canceling statement due to statement timeout\n"))
    with pytest.raises(pr.ProbeError, match="statement timeout"):
        client.instigators()


# ---------------------------------------------------------------------------
# 감시자 전체(가짜 측정)
# ---------------------------------------------------------------------------


class FakeProbes:
    def __init__(self) -> None:
        self.now = 0.0
        self.instigator_source = lambda now: []
        self.run_source = lambda since, now: []
        self.storage_error: str | None = None
        self.captures: list[str] = []
        self.fds: list[pr.ContainerFds] = []
        self.tagged_source = lambda now: []
        self.tagged_error: str | None = None
        self.tagged_calls: list[tuple[str, str, str, int]] = []

    def host(self) -> str:
        return "test-host"

    def plane_expected(self) -> bool:
        return True

    def instigators(self):
        if self.storage_error:
            raise pr.ProbeError(self.storage_error)
        return self.instigator_source(self.now)

    def runs_since(self, since):
        return self.run_source(since, self.now)

    def schedule_runs(self, schedules):
        return {key: [] for key in schedules}

    def tagged_job_runs(self, location, job, tag_key, limit):
        self.tagged_calls.append((location, job, tag_key, limit))
        if self.tagged_error:
            raise pr.ProbeError(self.tagged_error)
        return self.tagged_source(self.now)

    def disks(self, paths):
        return [pr.DiskObservation("/", 100, 10, 90)], []

    def io_pressure(self):
        return None

    def dagster_fds(self):
        return self.fds, []

    def capture(self, location, context):
        self.captures.append(location)
        return {"location": location, "directory": "/x", "captured_at": "t", "py_spy": "missing"}

    def capture_fds(self, container, context):
        self.captures.append(f"fd:{container}:{context['alert']['severity']}")
        return {"location": container, "directory": "/y", "captured_at": "t", "py_spy": "n/a"}


def _watchdog(config, probes: FakeProbes, sent: list[dict[str, Any]] | None = None) -> wd.PlaneWatchdog:
    watchdog = wd.PlaneWatchdog(config, probes=probes, clock=lambda: probes.now, run_diagnostics_inline=True)
    if sent is not None:
        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(204)
        settings = {**config.notifiers["webhook"], "enabled": True}
        watchdog._notifier = wd.WebhookNotifier(settings, host="test-host", transport=httpx.MockTransport(handler))
    return watchdog


def test_stall_fires_once_captures_diagnostics_and_resolves(config, monkeypatch):
    probes = FakeProbes()
    probes.instigator_source = _stall_instigators
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("KTDM_ALERT_WEBHOOK_URL", "https://hooks.invalid/secret-path")
    watchdog = _watchdog(config, probes, sent)

    for moment in ("2026-11-19T17:40:00Z", "2026-11-19T17:50:00Z", "2026-11-19T17:52:00Z", "2026-11-19T18:30:00Z"):
        probes.now = _epoch(moment)
        watchdog.evaluate_once()
    payload = watchdog.status_payload()
    assert [alert["rule"] for alert in payload["alerts"]] == ["location_tick_stall"]
    assert payload["alerts"][0]["since"] == "2026-11-19T17:50:00Z"
    assert probes.captures == ["kortravelweather_dagster.definitions"]  # 정지 경보 한 번에 한 번
    assert [event["event"] for batch in sent for event in batch["events"]] == ["firing"]

    probes.now = _epoch("2026-11-19T20:22:00Z")
    watchdog.evaluate_once()
    payload = watchdog.status_payload()
    assert payload["alerts"] == []
    assert payload["recent_resolved"][0]["rule"] == "location_tick_stall"
    assert [event["event"] for batch in sent for event in batch["events"]] == ["firing", "resolved"]
    assert payload["notifiers"][0]["enabled"] is True
    assert "secret-path" not in json.dumps(payload)


def test_webhook_is_off_by_default_and_hides_url_on_error(config, monkeypatch):
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(204))
    notifier = wd.WebhookNotifier(config.notifiers["webhook"], host="h", transport=transport)
    alert = wd.Alert(wd.Finding("io_pressure", "io", "warning", "t", "d"), since=1.0, last_seen=1.0)
    notifier.send([wd.Transition("firing", alert)])
    assert calls == [] and notifier.state()["enabled"] is False

    monkeypatch.setenv("KTDM_ALERT_WEBHOOK_URL", "https://hooks.invalid/secret-path")

    def broken(request):
        raise httpx.ConnectError("cannot reach https://hooks.invalid/secret-path")

    notifier = wd.WebhookNotifier({**config.notifiers["webhook"], "enabled": True}, host="h",
                                  transport=httpx.MockTransport(broken))
    notifier.send([wd.Transition("firing", alert)])
    assert notifier.last_error == "ConnectError"


def test_storage_outage_keeps_existing_alerts_and_fires_after_two_failures(config):
    probes = FakeProbes()
    probes.instigator_source = _stall_instigators
    watchdog = _watchdog(config, probes)
    probes.now = _epoch("2026-11-19T17:50:00Z")
    watchdog.evaluate_once()
    assert [alert["rule"] for alert in watchdog.status_payload()["alerts"]] == ["location_tick_stall"]

    probes.storage_error = "storage 질의 실패(exit 2): connection refused"
    probes.now += 120
    watchdog.evaluate_once()
    payload = watchdog.status_payload()
    # 측정하지 못한 규칙의 경보는 풀리지 않는다. 한 번의 실패로는 storage 경보를 내지 않는다.
    assert [alert["rule"] for alert in payload["alerts"]] == ["location_tick_stall"]
    checks = {check["rule"]: check["state"] for check in payload["checks"]}
    assert checks["location_tick_stall"] == "unavailable"
    probes.now += 120
    watchdog.evaluate_once()
    rules = sorted(alert["rule"] for alert in watchdog.status_payload()["alerts"])
    assert rules == ["location_tick_stall", "storage_unreachable"]


def test_payload_reports_a_dead_watcher(config):
    probes = FakeProbes()
    watchdog = _watchdog(config, probes)
    probes.now = 1000.0
    watchdog.evaluate_once()
    assert watchdog.status_payload()["summary"]["state"] == "ok"
    probes.now = 1000.0 + 4 * config.interval_seconds
    payload = watchdog.status_payload()
    assert payload["stale"] is True and payload["summary"]["state"] == "unknown"


def test_prometheus_metrics_expose_fds_and_tick_age(config):
    probes = FakeProbes()
    probes.instigator_source = _stall_instigators
    probes.fds = [pr.ContainerFds("pinvi-dagster-code-server-latest", 21, 1024, 60, 4, "grpc", 12,
                                  ("pinvi.etl.definitions",))]
    watchdog = _watchdog(config, probes)
    probes.now = _epoch("2026-11-19T17:50:00Z")
    watchdog.evaluate_once()
    text = watchdog.render_prometheus_metrics()
    assert 'ktdm_dagster_container_open_fds{container="pinvi-dagster-code-server-latest"} 21' in text
    assert 'ktdm_dagster_container_nofile_soft_limit{container="pinvi-dagster-code-server-latest"} 1024' in text
    assert 'ktdm_plane_alerts_firing{rule="location_tick_stall",severity="critical"} 1' in text
    assert "ktdm_dagster_location_tick_age_seconds" in text


# ---------------------------------------------------------------------------
# 진단 수집
# ---------------------------------------------------------------------------


def test_diagnostics_without_py_spy_record_threads_and_logs(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    task = proc / "21" / "task" / "21"
    task.mkdir(parents=True)
    (proc / "21" / "cmdline").write_bytes(b"python\0-m\0dagster\0api\0grpc")
    (task / "comm").write_text("python\n")
    (task / "stat").write_text("21 (python) S 1 2 3\n")
    (task / "wchan").write_text("futex_wait_queue\n")
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(argv, 0, "kor-travel-dagster-daemon\nweather-code-server\nother-dagster\n", "")
        if argv[:2] == ["docker", "top"]:
            body = {"kor-travel-dagster-daemon": "20 /usr/local/bin/python3.12 kor-travel-dagster-daemon run\n",
                    "weather-code-server": "21 /opt/venv/bin/python -m dagster api grpc --location-name kortravelweather_dagster.definitions\n",
                    "other-dagster": "22 /opt/venv/bin/python -m dagster api grpc --location-name other.location\n"}[argv[2]]
            return subprocess.CompletedProcess(argv, 0, "PID ARGS\n" + body, "")
        if argv[:2] == ["docker", "logs"]:
            return subprocess.CompletedProcess(argv, 0, "log line\n", "")
        raise AssertionError(f"unexpected command {argv}")

    monkeypatch.setattr(diag.shutil, "which", lambda name: None)
    result = diag.capture_location_stall(
        "kortravelweather_dagster.definitions", tmp_path / "out", runner=runner, proc_root=proc,
        now=dt.datetime(2026, 11, 19, 17, 50, tzinfo=dt.UTC))
    assert result.py_spy == "missing"
    assert result.containers == ["kor-travel-dagster-daemon", "weather-code-server"]
    target = Path(result.directory)
    assert target.name == "20261119T175000Z-kortravelweather_dagster.definitions"
    report = (target / "threads-weather-code-server-21.txt").read_text(encoding="utf-8")
    assert "state=S" in report and "futex_wait_queue" in report
    assert (target / "docker-logs-weather-code-server.txt").read_text() == "log line\n"
    assert json.loads((target / "summary.json").read_text(encoding="utf-8"))["py_spy"] == "missing"
    # 진단은 읽기만 한다 — kill·restart·exec signal 같은 명령은 없다.
    assert all(argv[1] in {"ps", "top", "logs"} for argv in calls)


def test_diagnostics_use_py_spy_dump_when_available(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(argv, 0, "kor-travel-dagster-daemon\n", "")
        if argv[:2] == ["docker", "top"]:
            return subprocess.CompletedProcess(argv, 0, "PID ARGS\n20 /usr/local/bin/python3.12 kor-travel-dagster-daemon run\n", "")
        if argv[1:3] == ["dump", "--pid"]:
            return subprocess.CompletedProcess(argv, 0, "Thread 0x1 (idle): \"SchedulerDaemon\"\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(diag.shutil, "which", lambda name: "/usr/bin/py-spy")
    result = diag.capture_location_stall("loc", tmp_path / "out", runner=runner, proc_root=tmp_path)
    assert result.py_spy == "used"
    assert ["/usr/bin/py-spy", "dump", "--pid", "20"] in calls
    assert "SchedulerDaemon" in (Path(result.directory) / "py-spy-kor-travel-dagster-daemon-20.txt").read_text()


def test_location_names_and_process_kind():
    assert pr.location_names("dagster code-server start -h 127.0.0.1 -p 1 -m a.b --location-name x-y") == ["a.b", "x-y"]
    assert pr.process_kind("python -m dagster api grpc --lazy-load-user-code") == "grpc"
    assert pr.process_kind("python /usr/local/bin/kor-travel-dagster-daemon run") == "daemon"



# ---------------------------------------------------------------------------
# fd 누수 진단(경보 때 grpc 프로세스의 fd 내역·스레드 수·패키지 버전)
# ---------------------------------------------------------------------------


def test_fd_alert_captures_inventory_once_per_severity(config):
    probes = FakeProbes()
    watchdog = _watchdog(config, probes)
    container = "pinvi-dagster-code-server-latest"
    for now, fds in ((1000.0, 600), (1120.0, 610), (1240.0, 900), (1360.0, 905)):
        probes.now = now
        probes.fds = [pr.ContainerFds(container, fds, 1024, fds + 40, 4, "grpc", 12, ("pinvi.etl.definitions",))]
        watchdog.evaluate_once()
    # 주의(50%)에서 한 번, 위험(80%)으로 올라갈 때 한 번 — 같은 경보 key라도 두 시점을 남긴다.
    assert probes.captures == [f"fd:{container}:warning", f"fd:{container}:critical"]
    assert [item["directory"] for item in watchdog.status_payload()["diagnostics"]] == ["/y", "/y"]


TCP_TABLE = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    "   0: 0100007F:2EE3 0100007F:2AF8 08 00000000:00000000 00:00000000 00000000 0 0 1001 1\n"
    "   1: 0100007F:2EE4 0100007F:2AF8 01 00000000:00000000 00:00000000 00000000 0 0 1002 1\n"
)
UNIX_TABLE = (
    "Num RefCount Protocol Flags Type St Inode Path\n"
    "0000: 00000002 00000000 00010000 0001 03 2001 /tmp/tmpabc\n"
)


def test_fd_inventory_groups_sockets_by_remote_and_state(tmp_path):
    proc = tmp_path / "proc"
    fd_dir = proc / "77" / "fd"
    fd_dir.mkdir(parents=True)
    targets = ["socket:[1001]", "socket:[1001]", "socket:[1002]", "socket:[2001]", "socket:[9999]",
               "pipe:[5]", "anon_inode:[eventpoll]", "/opt/dagster/state/compute_logs/x.out", "/dev/null"]
    for index, target in enumerate(targets):
        (fd_dir / str(index)).symlink_to(target)
    net = proc / "77" / "net"
    net.mkdir()
    (net / "tcp").write_text(TCP_TABLE)
    (net / "unix").write_text(UNIX_TABLE)
    (proc / "77" / "status").write_text("Name:\tpython\nThreads:\t23\n")
    (proc / "77" / "limits").write_text("Max open files            1024                 524288               files\n")
    inventory = diag.fd_inventory(77, proc)
    assert inventory["threads"] == "23"
    assert inventory["open_fds"] == 9
    assert inventory["kinds"]["socket"] == 5
    assert inventory["sockets"]["tcp CLOSE_WAIT 127.0.0.1:12003 -> 127.0.0.1:11000"] == 2
    assert inventory["sockets"]["unix CONNECTED /tmp/tmpabc -> -"] == 1
    assert inventory["unresolved_sockets"] == 1
    assert inventory["files"]["/opt/dagster/state/compute_logs/x.out"] == 1
    assert inventory["limits"].startswith("Max open files")


TOP_OUTPUT = (
    "PID ARGS\n"
    "11 /usr/local/bin/python /usr/local/bin/dagster code-server start -m pinvi.etl.definitions\n"
    "12 /usr/local/bin/python -m dagster api grpc --location-name pinvi.etl.definitions\n"
)


def test_fd_capture_targets_grpc_process_and_records_packages(tmp_path):
    proc = tmp_path / "proc"
    for pid in (11, 12):
        (proc / str(pid) / "fd").mkdir(parents=True)
        (proc / str(pid) / "fd" / "0").symlink_to("pipe:[1]")
        (proc / str(pid) / "status").write_text("Threads:\t5\n")
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "top"]:
            return subprocess.CompletedProcess(argv, 0, TOP_OUTPUT, "")
        if argv[:2] == ["docker", "exec"]:
            return subprocess.CompletedProcess(
                argv, 0, '{"python": "3.12.14", "grpcio": "1.84.0", "dagster": "1.13.24"}\n', "")
        raise AssertionError(argv)

    result = diag.capture_fd_usage("pinvi-code-server", tmp_path / "out", runner=runner, proc_root=proc,
                                   now=dt.datetime(2026, 11, 14, 0, 0, tzinfo=dt.UTC))
    target = Path(result.directory)
    assert target.name == "20261114T000000Z-fd-pinvi-code-server"
    assert [path.name for path in sorted(target.glob("fd-*.txt"))] == ["fd-pinvi-code-server-12.txt"]
    report = (target / "fd-pinvi-code-server-12.txt").read_text(encoding="utf-8")
    assert "threads 5, open fds 1" in report and "grpcio 1.84.0" in report
    # 대상 프로세스와 같은 인터프리터로 새 프로세스 하나를 띄워 버전만 읽는다 — signal·restart 없음.
    assert calls[1][:4] == ["docker", "exec", "pinvi-code-server", "/usr/local/bin/python"]
    assert all(argv[1] in {"top", "exec"} for argv in calls)


# ---------------------------------------------------------------------------
# 공항 통계 사전집계 갱신 — odroid transport 공항 수집 run(11-24) 위 시나리오 태그
# ---------------------------------------------------------------------------

_AIRPORT = "airport_analytics_runs_20261124.json"
#: 시나리오: run 순번(오래된 것부터) → (태그, status). 나머지는 실물 그대로(SUCCESS, 태그 없음 = 정상).
#: 20 한 번 튄 delayed → 21 정상(오탐 억제 확인), 24~25 delayed(주의 발화), 26 stale(아직 주의),
#: 27 태그 없는 FAILURE(건너뜀), 28 stale(위험으로 오름), 29 missing(위험 유지), 30 정상(해소).
_SCENARIO: dict[int, tuple[str | None, str]] = {
    20: ("delayed", "SUCCESS"), 24: ("delayed", "SUCCESS"), 25: ("delayed", "SUCCESS"),
    26: ("stale", "SUCCESS"), 27: (None, "FAILURE"), 28: ("stale", "SUCCESS"), 29: ("missing", "SUCCESS"),
}


def _airport_runs(now: float, scenario: dict[int, tuple[str | None, str]] | None = None) -> list[pr.TaggedRun]:
    """그 시각까지 끝난 run만, 최신 10개(운영 질의의 limit = 연속 2 + 여유 8과 같다)."""

    rows = _fixture(_AIRPORT)["runs"]
    result = []
    for index, row in enumerate(rows):
        if row["updated"] > now:
            continue
        tag, status = (scenario or {}).get(index, (None, row["status"]))
        result.append(pr.TaggedRun(record=pr.run_from_row({**row, "status": status}), tag=tag))
    return sorted(result, key=lambda item: item.record.created, reverse=True)[:10]


def _airport_moment(index: int) -> float:
    return _fixture(_AIRPORT)["runs"][index]["updated"] + 1


def test_airport_fixture_is_the_real_cadence():
    data = _fixture(_AIRPORT)
    runs = data["runs"]
    assert (data["location"], data["job"]) == ("kor-travel-transport", "airport_collection_job")
    assert len(runs) == 40 and all(run["location"] == "kor-travel-transport" for run in runs)
    gaps = [later["created"] - earlier["created"] for earlier, later in zip(runs, runs[1:], strict=False)]
    assert 290 <= min(gaps) and max(gaps) <= 960  # 5분 cron, coalescing으로 5~15분 간격


def test_repository_config_carries_the_analytics_refresh_rule(config):
    rule = config.rule("analytics_refresh_lag")
    assert rule["enabled"] is True
    assert (rule["location"], rule["job"], rule["tag_key"]) == (
        "kor-travel-transport", "airport_collection_job", "kortravel/analytics-refresh")
    # 소유자 결정: delayed=주의, stale·missing=위험, 연속 N회로 오탐 억제.
    assert rule["warning_values"] == ["delayed"] and sorted(rule["critical_values"]) == ["missing", "stale"]
    assert rule["consecutive_runs"] >= 2
    assert wd.RULE_LABELS["analytics_refresh_lag"] == "공항 통계 사전집계 갱신"


@pytest.mark.parametrize(("key", "value", "message"), [
    ("critical_values", ["stale", "delayed"], "겹칩니다"),
    ("warning_values", [], "문자열 목록"),
    ("job", " ", "비울 수 없습니다"),
    ("consecutive_runs", 0, "1 이상"),
    ("warning_values", "delayed", "형식"),
])
def test_analytics_refresh_config_is_validated(config, key, value, message):
    document = json.loads(json.dumps({
        "version": 1, "interval_seconds": 120, "storage": dict(config.storage),
        "rules": {name: dict(item) for name, item in config.rules.items()},
        "diagnostics": dict(config.diagnostics), "notifiers": {"webhook": dict(config.notifiers["webhook"])},
    }))
    document["rules"]["analytics_refresh_lag"][key] = value
    with pytest.raises(wd.ConfigError, match=message):
        wd.parse_config(document)


def test_analytics_refresh_needs_consecutive_runs_and_takes_the_lowest_severity(config):
    rule = config.rule("analytics_refresh_lag")
    assert rule["consecutive_runs"] == 2  # 아래 기대값은 기본 2회 기준

    def judge(index: int) -> list[wd.Finding]:
        now = _airport_moment(index)
        return wd.evaluate_analytics_refresh(_airport_runs(now, _SCENARIO), now, rule)

    assert judge(19) == []  # 태그 없음 = 정상
    assert judge(20) == []  # 한 번 튄 delayed는 내지 않는다
    assert judge(21) == []  # 태그 없는 SUCCESS가 연속을 끊는다
    assert judge(24) == []
    [warning] = judge(25)
    assert warning.severity == "warning" and warning.subject == "kor-travel-transport/airport_collection_job"
    assert warning.evidence["statuses"] == ["delayed", "delayed"]
    assert "갱신 지연" in warning.title and "SUCCESS일 수 있습니다" in warning.detail
    # stale 한 번으로는 위험이 아니다 — 최근 2회 중 낮은 쪽(delayed).
    [mixed] = judge(26)
    assert mixed.severity == "warning" and mixed.evidence["latest_status"] == "stale"
    # 태그 없는 FAILURE run은 판정 자료가 아니라 건너뛴다(연속을 끊지도, 늘리지도 않는다).
    [skipped] = judge(27)
    assert skipped.evidence["streak"] == 3 and skipped.severity == "warning"
    [critical] = judge(28)
    assert critical.severity == "critical" and critical.evidence["statuses"][:2] == ["stale", "stale"]
    [missing] = judge(29)
    assert missing.severity == "critical" and "사전집계 없음" in missing.title
    assert judge(30) == []


def test_analytics_refresh_ignores_old_runs_and_other_locations(config):
    rule = config.rule("analytics_refresh_lag")
    # n150: 공항 job은 10-14 odroid로 옮긴 뒤 없다 — 옛 run만 남았다(2026-11-24 실측 3,268건, 마지막 10-14).
    old = [pr.TaggedRun(pr.RunRecord("old", "airport_collection_job", "kor-travel-transport", None, "SUCCESS",
                                     _epoch("2026-10-14T21:10:00Z"), _epoch("2026-10-14T21:14:00Z"), False),
                        "stale")] * 3
    now = _epoch("2026-11-24T08:00:00Z")
    assert wd.relevant_analytics_runs(old, now, rule) == []
    assert wd.evaluate_analytics_refresh(old, now, rule) == []
    # 다른 location의 같은 이름 job은 보지 않는다.
    other = [pr.TaggedRun(pr.RunRecord(f"o{i}", "airport_collection_job", "elsewhere", None, "SUCCESS",
                                       now - 600 * (i + 1), now - 600 * i - 60, False), "stale") for i in range(3)]
    assert wd.evaluate_analytics_refresh(other, now, rule) == []


def test_watchdog_escalates_resolves_and_marks_n150_not_applicable(config):
    probes = FakeProbes()
    probes.tagged_source = lambda now: _airport_runs(now, _SCENARIO)
    watchdog = _watchdog(config, probes)

    def step(index: int) -> dict[str, Any]:
        probes.now = _airport_moment(index)
        watchdog.evaluate_once()
        return watchdog.status_payload()

    payload = step(21)
    checks = {check["rule"]: check for check in payload["checks"]}
    assert checks["analytics_refresh_lag"]["state"] == "ok"
    assert "정상(태그 없음)" in checks["analytics_refresh_lag"]["detail"]
    assert payload["alerts"] == []
    assert probes.tagged_calls[-1] == ("kor-travel-transport", "airport_collection_job",
                                       "kortravel/analytics-refresh", 2 + wd._ANALYTICS_RUN_MARGIN)

    payload = step(25)
    [alert] = payload["alerts"]
    assert alert["rule"] == "analytics_refresh_lag" and alert["severity"] == "warning"
    assert alert["rule_label"] == "공항 통계 사전집계 갱신"
    since = alert["since"]
    # 같은 경보 key가 위험으로 오른다(새 경보가 아니다 — since는 그대로).
    [alert] = step(28)["alerts"]
    assert alert["severity"] == "critical" and alert["since"] == since
    assert step(30)["alerts"] == []
    assert watchdog.status_payload()["recent_resolved"][0]["rule"] == "analytics_refresh_lag"

    # n150: 최근 run이 없다 → 해당 없음(경보 없음).
    probes.tagged_source = lambda now: []
    payload = step(39)
    checks = {check["rule"]: check for check in payload["checks"]}
    assert checks["analytics_refresh_lag"]["state"] == "not_applicable"
    assert "kor-travel-transport/airport_collection_job run이 없습니다" in checks["analytics_refresh_lag"]["detail"]
    assert payload["alerts"] == []


def test_analytics_query_failure_only_blinds_that_rule_and_keeps_its_alert(config):
    probes = FakeProbes()
    probes.tagged_source = lambda now: _airport_runs(now, _SCENARIO)
    watchdog = _watchdog(config, probes)
    probes.now = _airport_moment(28)
    watchdog.evaluate_once()
    assert [alert["rule"] for alert in watchdog.status_payload()["alerts"]] == ["analytics_refresh_lag"]

    probes.tagged_error = "storage 질의 실패(exit 3): canceling statement due to statement timeout"
    probes.now += 120
    watchdog.evaluate_once()
    payload = watchdog.status_payload()
    checks = {check["rule"]: check["state"] for check in payload["checks"]}
    assert checks["analytics_refresh_lag"] == "unavailable"
    assert checks["storage_unreachable"] == "ok" and checks["location_tick_stall"] == "ok"
    # 측정하지 못한 규칙의 경보는 풀리지 않는다.
    assert [alert["rule"] for alert in payload["alerts"]] == ["analytics_refresh_lag"]


def test_tagged_job_runs_query_is_read_only_and_filters_location(monkeypatch):
    monkeypatch.setattr(pr.standalone_backup, "_discover_admin_role", lambda container: "shared_admin")
    monkeypatch.setattr(pr.standalone_backup, "_discover_port", lambda container: 11000)
    seen: dict[str, Any] = {}
    reply = json.dumps([
        {"run_id": "a", "job": "airport_collection_job", "status": "SUCCESS", "created": 2.0, "updated": 3.0,
         "never_started": False, "location": "kor-travel-transport", "schedule": "s", "tag_value": "stale"},
        {"run_id": "b", "job": "airport_collection_job", "status": "SUCCESS", "created": 1.0, "updated": 1.5,
         "never_started": False, "location": "kor-travel-transport", "schedule": "s", "tag_value": None},
        {"run_id": "c", "job": "airport_collection_job", "status": "SUCCESS", "created": 0.5, "updated": 0.7,
         "never_started": False, "location": "elsewhere", "schedule": None, "tag_value": "stale"},
    ])

    def runner(argv, **kwargs):
        seen["argv"], seen["input"] = argv, kwargs["input"]
        return subprocess.CompletedProcess(argv, 0, reply + "\n", "")

    client = pr.StorageClient(statement_timeout_seconds=7, runner=runner)
    runs = client.tagged_job_runs("kor-travel-transport", "airport_collection_job",
                                  "kortravel/analytics-refresh", limit=10)
    assert [(item.record.run_id, item.tag) for item in runs] == [("a", "stale"), ("b", None)]
    assert seen["input"].startswith("BEGIN READ ONLY;\n")
    assert "t.key = :'tag_key'" in seen["input"] and "r.pipeline_name = :'job'" in seen["input"]
    for expected in ("location=kor-travel-transport", "job=airport_collection_job",
                     "tag_key=kortravel/analytics-refresh", "limit=10"):
        assert expected in seen["argv"]
    assert not any("password" in part.lower() for part in seen["argv"])
