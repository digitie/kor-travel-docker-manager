"""code-server 자동 재시작 — 계기·실행 중 run 0·컨테이너당 6시간 1회·정식 경로·전후 진단·발송."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from kor_travel_docker_manager.services import plane_watchdog as wd
from kor_travel_docker_manager.services import plane_watchdog_probe as pr
from kor_travel_docker_manager.services.plane_watchdog_restart import (
    RestartLedger,
    restart_candidates,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTAINER = "pinvi-dagster-code-server-latest"
LOCATION = "pinvi.etl.definitions"


@pytest.fixture(scope="module")
def config() -> wd.WatchdogConfig:
    return wd.load_config(REPO_ROOT / "config" / "plane-watchdog.yml")


def _fd(open_fds: int, kind: str = "grpc") -> pr.ContainerFds:
    return pr.ContainerFds(CONTAINER, open_fds, 1024, open_fds + 40, 4, kind, 4242, (LOCATION,))


class Probes:
    def __init__(self, host: str = "n150") -> None:
        self.now = 1_795_500_000.0
        self._host = host
        self.fds = [_fd(980)]
        self.running = 0
        self.running_error: str | None = None
        self.launch_failures = False
        self.restarts: list[str] = []
        self.restart_result: dict[str, Any] = {"success": True, "message": "ok"}
        self.captures: list[tuple[str, str]] = []

    def host(self): return self._host
    def plane_expected(self): return True
    def instigators(self): return []

    def runs_since(self, since):
        if not self.launch_failures:
            return []
        return [pr.RunRecord(f"r{i}", "job", LOCATION, None, "FAILURE", self.now - 900 + i, self.now - 800 + i, True)
                for i in range(5)]

    def schedule_runs(self, schedules): return {key: [] for key in schedules}
    def tagged_job_runs(self, location, job, tag_key, since, limit): return []
    def disks(self, paths): return [pr.DiskObservation("/", 100, 10, 90)], []
    def io_pressure(self): return None
    def dagster_fds(self): return self.fds, []
    def capture(self, location, context): return None

    def capture_fds(self, container, context):
        phase = context.get("auto_restart", {}).get("phase", "alert")
        self.captures.append((container, phase))
        return {"location": container, "directory": f"/diag/{phase}", "captured_at": "t"}

    def running_runs(self, location):
        if self.running_error:
            raise pr.ProbeError(self.running_error)
        return self.running

    def restart_container(self, container):
        self.restarts.append(container)
        return self.restart_result


def _watchdog(config, probes: Probes, tmp_path: Path, sent: list[str] | None = None,
              monkeypatch: pytest.MonkeyPatch | None = None) -> wd.PlaneWatchdog:
    watchdog = wd.PlaneWatchdog(config, probes=probes, clock=lambda: probes.now, run_diagnostics_inline=True,
                                restart_ledger_path=tmp_path / "auto-restart-state.json", sleep=lambda seconds: None)
    if sent is not None and monkeypatch is not None:
        monkeypatch.setenv("PINVI_TELEGRAM_BOT_TOKEN_DEFAULT", "123:TOKEN")
        monkeypatch.setenv("KTDM_ALERT_TELEGRAM_CHAT_ID", "42")
        watchdog._telegram._transport = httpx.MockTransport(
            lambda request: sent.append(json.loads(request.content)["text"]) or httpx.Response(200))
    return watchdog


def test_repository_config_limits_auto_restart_to_n150(config):
    settings = config.auto_restart
    assert settings["enabled"] is True and settings["hosts"] == ["n150"]
    assert settings["fd_ratio"] == 0.9 and settings["min_interval_hours"] == 6


@pytest.mark.parametrize(("key", "value", "message"), [
    ("fd_ratio", 0.3, "0.5~1"), ("min_interval_hours", 0.5, "1 이상"), ("hosts", [""], "호스트 이름"),
])
def test_auto_restart_config_is_validated(config, key, value, message):
    document = json.loads(json.dumps({
        "version": 1, "interval_seconds": 120, "storage": dict(config.storage),
        "rules": {name: dict(item) for name, item in config.rules.items()},
        "diagnostics": dict(config.diagnostics),
        "notifiers": {name: dict(item) for name, item in config.notifiers.items()},
        "auto_restart": dict(config.auto_restart),
    }))
    document["auto_restart"][key] = value
    with pytest.raises(wd.ConfigError, match=message):
        wd.parse_config(document)


def test_candidates_need_grpc_fd_ninety_percent_or_a_launch_streak(config):
    settings = config.auto_restart
    containers = {LOCATION: CONTAINER}
    critical = wd.evaluate_fds([_fd(930)], config.rule("dagster_fd_usage"))
    [candidate], _ = restart_candidates(critical, containers, settings)
    assert (candidate.container, candidate.location, candidate.rule) == (CONTAINER, LOCATION, "dagster_fd_usage")
    assert "930/1024" in candidate.reason
    # 80%대 위험 fd 경보는 재시작하지 않는다(소유자 기준 90%). code-server 부모 프로세스 경보도 아니다.
    assert restart_candidates(wd.evaluate_fds([_fd(900)], config.rule("dagster_fd_usage")), containers, settings)[0] == []
    assert restart_candidates(wd.evaluate_fds([_fd(990, "code-server")], config.rule("dagster_fd_usage")),
                              containers, settings)[0] == []
    streak = wd.Finding("launch_failure_streak", LOCATION, "critical", "t", "d",
                        {"streak": 5, "first_failure_at": "2026-11-13T19:40:00Z"})
    [candidate], _ = restart_candidates([streak], containers, settings)
    assert candidate.rule == "launch_failure_streak" and "5회 연속" in candidate.reason
    assert restart_candidates([streak], {}, settings) == ([], [
        f"{LOCATION}: launch 연속 실패지만 그 location의 code-server 컨테이너를 모릅니다"])


def test_restart_runs_once_with_diagnostics_and_notification(config, tmp_path, monkeypatch):
    probes = Probes()
    sent: list[str] = []
    watchdog = _watchdog(config, probes, tmp_path, sent, monkeypatch)
    watchdog.evaluate_once()
    assert probes.restarts == [CONTAINER]
    assert (CONTAINER, "before") in probes.captures and (CONTAINER, "after") in probes.captures
    state = watchdog.status_payload()["auto_restart"]
    assert state["active_on_host"] is True and "실행 중 run 0" in state["policy"]
    [record] = state["recent"]
    assert record["success"] is True and record["diagnostics"] == {"before": "/diag/before", "after": "/diag/after"}
    assert any(text.startswith("[자동 조치] code-server 재시작 완료 · n150") and CONTAINER in text for text in sent)
    assert "123:TOKEN" not in json.dumps(watchdog.status_payload())

    # 같은 컨테이너는 6시간에 한 번 — 경보가 이어져도 다시 하지 않는다.
    probes.now += 2 * 3600
    watchdog.evaluate_once()
    assert probes.restarts == [CONTAINER]
    assert any("6시간에 한 번" in note for note in watchdog.status_payload()["auto_restart"]["skipped"])
    # Manager가 재시작해도 기록이 남는다.
    restarted = _watchdog(config, probes, tmp_path)
    restarted.evaluate_once()
    assert probes.restarts == [CONTAINER]
    probes.now += 5 * 3600
    restarted.evaluate_once()
    assert probes.restarts == [CONTAINER, CONTAINER]


def test_running_runs_block_the_restart(config, tmp_path):
    probes = Probes()
    probes.running = 2
    watchdog = _watchdog(config, probes, tmp_path)
    watchdog.evaluate_once()
    assert probes.restarts == []
    assert any("실행 중 run 2개" in note for note in watchdog.status_payload()["auto_restart"]["skipped"])
    # 확인하지 못하면 하지 않는다.
    probes.running, probes.running_error = 0, "storage 질의 실패"
    watchdog.evaluate_once()
    assert probes.restarts == []
    # 시도 기록도 남기지 않았다 — 조건이 풀리면 바로 한다.
    probes.running_error = None
    watchdog.evaluate_once()
    assert probes.restarts == [CONTAINER]


def test_other_hosts_never_restart(config, tmp_path):
    probes = Probes(host="odroid")
    watchdog = _watchdog(config, probes, tmp_path)
    watchdog.evaluate_once()
    assert probes.restarts == [] and watchdog.status_payload()["auto_restart"]["active_on_host"] is False


def test_launch_streak_restarts_the_location_code_server(config, tmp_path):
    probes = Probes()
    probes.fds = [_fd(40)]  # fd는 정상, launch만 연속 실패
    probes.launch_failures = True
    watchdog = _watchdog(config, probes, tmp_path)
    watchdog.evaluate_once()
    assert probes.restarts == [CONTAINER]
    assert watchdog.status_payload()["auto_restart"]["recent"][0]["rule"] == "launch_failure_streak"


def test_refused_restart_is_reported_and_still_counts(config, tmp_path, monkeypatch):
    probes = Probes()
    probes.restart_result = {"success": False,
                             "error": "ManagerMutationActiveError: another Manager mutation is already active"}
    sent: list[str] = []
    watchdog = _watchdog(config, probes, tmp_path, sent, monkeypatch)
    watchdog.evaluate_once()
    [record] = watchdog.status_payload()["auto_restart"]["recent"]
    assert record["success"] is False and "already active" in record["error"]
    assert (CONTAINER, "after") not in probes.captures
    assert any(text.startswith("[자동 조치] code-server 재시작 실패") for text in sent)
    probes.now += 600
    watchdog.evaluate_once()
    assert probes.restarts == [CONTAINER]  # 실패도 6시간 한도에 센다(재시도 폭주 없음)


def test_ledger_write_failure_blocks_the_restart(config, tmp_path, monkeypatch):
    probes = Probes()
    watchdog = _watchdog(config, probes, tmp_path)
    monkeypatch.setattr(RestartLedger, "record", lambda self, container, at: False)
    watchdog.evaluate_once()
    assert probes.restarts == []
    assert "기록을 남기지 못해" in watchdog.status_payload()["auto_restart"]["skipped"][0]


def test_restart_uses_the_managed_lifecycle_path_only(monkeypatch):
    from kor_travel_docker_manager.services import docker_service as ds
    from kor_travel_docker_manager.services import registry

    containers = {
        "pinvi-dagster-code-server": {"name": CONTAINER, "role": "pinvi-dagster-code-server"},
        "pinvi-api": {"name": "pinvi-api-latest", "role": "pinvi-api"},
    }
    monkeypatch.setattr(registry, "MANAGED_CONTAINERS", containers)
    calls: list[tuple[str, str]] = []

    def control(container_id, action):
        calls.append((container_id, action))
        return {"success": True, "message": "Successfully performed 'restart'"}

    monkeypatch.setattr(ds.docker_service, "control_container", control)
    assert wd.restart_managed_code_server(CONTAINER)["success"] is True
    assert calls == [("pinvi-dagster-code-server", "restart")]
    assert wd.restart_managed_code_server("pinvi-api-latest") == {
        "success": False, "error": "code-server 컨테이너가 아닙니다"}
    assert wd.restart_managed_code_server("stranger") == {
        "success": False, "error": "registry가 관리하는 컨테이너가 아닙니다"}

    def refuse(container_id, action):
        raise ds.DeploymentContractError("cannot acquire C6c deployment lock")

    monkeypatch.setattr(ds.docker_service, "control_container", refuse)
    result = wd.restart_managed_code_server(CONTAINER)
    assert result["success"] is False and "C6c deployment lock" in result["error"]


def test_running_runs_query_is_read_only(monkeypatch):
    monkeypatch.setattr(pr.standalone_backup, "_discover_admin_role", lambda container: "shared_admin")
    monkeypatch.setattr(pr.standalone_backup, "_discover_port", lambda container: 11000)
    seen: dict[str, Any] = {}

    def runner(argv, **kwargs):
        seen["argv"], seen["input"] = argv, kwargs["input"]
        return subprocess.CompletedProcess(argv, 0, '{"running": 3}\n', "")

    client = pr.StorageClient(statement_timeout_seconds=7, runner=runner)
    assert client.running_runs(LOCATION) == 3
    assert seen["input"].startswith("BEGIN READ ONLY;\n")
    assert "('STARTING', 'STARTED', 'CANCELING')" in seen["input"] and f"location={LOCATION}" in seen["argv"]
    client = pr.StorageClient(statement_timeout_seconds=7, runner=lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, '{"running": "x"}\n', ""))
    with pytest.raises(pr.ProbeError):
        client.running_runs(LOCATION)
