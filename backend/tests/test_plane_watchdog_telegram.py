"""수집 경보 Telegram 발송 — 위험 즉시·주의 하루 1회 요약·반복 억제·해소 1회·비밀 비노출."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from kor_travel_docker_manager.services import plane_watchdog as wd
from kor_travel_docker_manager.services import plane_watchdog_probe as pr
from kor_travel_docker_manager.services.plane_watchdog_telegram import (
    TelegramNotifier,
    dashboard_link,
    format_alert,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "123456:SECRET-TOKEN-VALUE"
ENV = {"PINVI_TELEGRAM_BOT_TOKEN_DEFAULT": TOKEN, "KTDM_ALERT_TELEGRAM_CHAT_ID": "-100200",
       "KTDM_ALERT_DASHBOARD_URL": "https://manager.example.org/"}


def _epoch(text: str) -> float:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


@pytest.fixture(scope="module")
def config() -> wd.WatchdogConfig:
    return wd.load_config(REPO_ROOT / "config" / "plane-watchdog.yml")


class Clock:
    def __init__(self, text: str) -> None:
        self.now = _epoch(text)

    def __call__(self) -> float:
        return self.now


def _notifier(config, clock, sent, tmp_path=None, env=ENV, status=200) -> TelegramNotifier:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), **json.loads(request.content)})
        return httpx.Response(status, json={"ok": status < 300})
    return TelegramNotifier(config.notifiers["telegram"], host="n150",
                            state_path=None if tmp_path is None else tmp_path / "telegram-state.json",
                            transport=httpx.MockTransport(handler), clock=clock, environ=env)


def _transition(event: str, severity: str, key: str = "launch_failure_streak:pinvi.etl.definitions",
                since: str = "2026-11-24T00:00:00Z") -> wd.Transition:
    rule, subject = key.split(":", 1)
    finding = wd.Finding(rule, subject, severity, f"{subject}: run launch 5회 연속 실패", "code-server 재시작 검토",
                         {"streak": 5})
    alert = wd.Alert(finding, since=_epoch(since), last_seen=_epoch(since),
                     resolved_at=_epoch(since) + 600 if event == "resolved" else None)
    return wd.Transition(event, alert)


def test_repository_config_points_at_the_pinvi_system_bot(config):
    telegram = config.notifiers["telegram"]
    assert telegram["token_env"] == "PINVI_TELEGRAM_BOT_TOKEN_DEFAULT"
    assert telegram["chat_id_env"] == "KTDM_ALERT_TELEGRAM_CHAT_ID"
    assert telegram["api_base"] == "https://api.telegram.org"
    assert telegram["enabled"] is True and 0 <= telegram["digest_hour_kst"] <= 23
    # 비밀 값은 설정 파일에 없다(변수 이름만).
    text = (REPO_ROOT / "config" / "plane-watchdog.yml").read_text(encoding="utf-8")
    assert "bot" not in telegram["api_base"] and ":" not in telegram["token_env"]
    assert "KTDM_ALERT_TELEGRAM_CHAT_ID" in (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "123456:" not in text


def test_without_chat_id_nothing_is_sent(config):
    sent: list[dict[str, Any]] = []
    notifier = _notifier(config, Clock("2026-11-24T01:00:00Z"), sent,
                         env={"PINVI_TELEGRAM_BOT_TOKEN_DEFAULT": TOKEN})
    notifier.send([_transition("firing", "critical")])
    assert sent == [] and notifier.state()["enabled"] is False and notifier.state()["configured"] is False


def test_critical_is_immediate_with_host_rule_subject_and_link(config, caplog):
    caplog.set_level("DEBUG")
    sent: list[dict[str, Any]] = []
    notifier = _notifier(config, Clock("2026-11-24T01:00:00Z"), sent)
    notifier.send([_transition("firing", "critical")])
    [message] = sent
    assert message["chat_id"] == "-100200" and message["disable_web_page_preview"] is True
    assert message["url"].endswith("/sendMessage")
    text = message["text"]
    assert text.startswith("[위험] 수집 경보 · n150")
    assert "규칙: location launch 연속 실패" in text and "대상: pinvi.etl.definitions" in text
    assert "run launch 5회 연속 실패" in text
    assert "대시보드: https://manager.example.org/#plane-alerts" in text
    assert TOKEN not in text
    # 상태 API에는 변수 이름만 — 토큰 값이 없다. httpx의 요청 로그(URL 경로에 토큰)도 가린다.
    assert TOKEN not in json.dumps(notifier.state())
    assert "HTTP Request" in caplog.text and TOKEN not in caplog.text and "/bot<redacted>/" in caplog.text


def test_repeat_is_suppressed_and_resolution_is_sent_once(config, tmp_path):
    sent: list[dict[str, Any]] = []
    clock = Clock("2026-11-24T01:00:00Z")
    notifier = _notifier(config, clock, sent, tmp_path)
    notifier.send([_transition("firing", "critical")])
    clock.now += 600
    notifier.send([_transition("resolved", "critical")])
    assert [item["text"].splitlines()[0] for item in sent] == ["[위험] 수집 경보 · n150", "[해소] 수집 경보 · n150"]
    # 억제 창(6시간) 안에 다시 울리면 보내지 않고, 그 해소도 다시 보내지 않는다.
    clock.now += 1200
    notifier.send([_transition("firing", "critical")])
    clock.now += 600
    notifier.send([_transition("resolved", "critical")])
    assert len(sent) == 2 and notifier.state()["suppressed"] == 1
    # 창이 지나면 다시 보낸다.
    clock.now += 7 * 3600
    notifier.send([_transition("firing", "critical")])
    assert len(sent) == 3


def test_state_survives_a_manager_restart(config, tmp_path):
    sent: list[dict[str, Any]] = []
    clock = Clock("2026-11-24T01:00:00Z")
    _notifier(config, clock, sent, tmp_path).send([_transition("firing", "critical")])
    state_file = tmp_path / "telegram-state.json"
    assert TOKEN not in state_file.read_text(encoding="utf-8")
    # 재시작 뒤 첫 평가가 같은 경보를 다시 울려도 보내지 않는다. 해소는 보낸다(바로 알린 경보였다).
    clock.now += 300
    restarted = _notifier(config, clock, sent, tmp_path)
    restarted.send([_transition("firing", "critical")])
    restarted.send([_transition("resolved", "critical")])
    assert [item["text"].splitlines()[0] for item in sent] == ["[위험] 수집 경보 · n150", "[해소] 수집 경보 · n150"]


def test_warnings_wait_for_one_daily_digest(config, tmp_path):
    sent: list[dict[str, Any]] = []
    clock = Clock("2026-11-23T20:00:00Z")  # 11-24 05:00 KST — 요약 시각(09시) 전
    notifier = _notifier(config, clock, sent, tmp_path)
    notifier.send([_transition("firing", "warning", key="analytics_refresh_lag:kor-travel-transport/airport_collection_job")])
    notifier.send([_transition("firing", "warning", key="io_pressure:io")])
    notifier.send([_transition("resolved", "warning", key="io_pressure:io")])
    assert sent == []
    assert notifier.maybe_digest([]) is False  # 아직 09시 전
    clock.now = _epoch("2026-11-24T00:30:00Z")  # 09:30 KST
    active = [_transition("firing", "warning",
                          key="analytics_refresh_lag:kor-travel-transport/airport_collection_job").alert.as_dict()]
    assert notifier.maybe_digest(active) is True
    [digest] = sent
    assert digest["text"].startswith("[주의 요약] 수집 경보 · n150")
    assert "울린 주의 2건 · 지금 울리는 주의 1건 · 풀린 주의 1건" in digest["text"]
    assert "지금 울리는 주의:\n- 공항 통계 사전집계 갱신 ·" in digest["text"]
    assert "그사이 울렸다 풀린 주의:\n- IO 압력(PSI) ·" in digest["text"]
    # 같은 날 두 번 보내지 않는다.
    clock.now += 3600
    assert notifier.maybe_digest(active) is False and len(sent) == 1
    # 다음 날, 쌓인 것이 없어도 울리는 주의가 있으면 요약한다.
    clock.now = _epoch("2026-11-25T00:10:00Z")
    assert notifier.maybe_digest(active) is True and len(sent) == 2
    # 아무것도 없으면 보내지 않는다.
    clock.now = _epoch("2026-11-26T00:10:00Z")
    assert notifier.maybe_digest([]) is False and len(sent) == 2


def test_escalation_to_critical_is_immediate(config):
    sent: list[dict[str, Any]] = []
    notifier = _notifier(config, Clock("2026-11-24T01:00:00Z"), sent)
    notifier.send([_transition("firing", "warning")])
    assert sent == []
    notifier.send([_transition("escalated", "critical")])
    [message] = sent
    assert "주의에서 위험으로 올랐습니다" in message["text"]


def test_send_failure_keeps_the_token_out_of_the_error(config):
    sent: list[dict[str, Any]] = []
    notifier = _notifier(config, Clock("2026-11-24T01:00:00Z"), sent, status=401)
    notifier.send([_transition("firing", "critical")])
    assert notifier.last_error == "HTTP 401"

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")
    notifier = TelegramNotifier(config.notifiers["telegram"], host="n150", state_path=None,
                                transport=httpx.MockTransport(broken), clock=Clock("2026-11-24T01:00:00Z"), environ=ENV)
    notifier.send([_transition("firing", "critical")])
    assert notifier.last_error == "ConnectError" and TOKEN not in json.dumps(notifier.state())


def test_dashboard_link_falls_back_to_the_frontend_origin(config):
    settings = config.notifiers["telegram"]
    assert dashboard_link(settings, {"KTDM_FRONTEND_ORIGINS": "http://127.0.0.1:12905,https://m.example.org"}) == \
        "https://m.example.org/#plane-alerts"
    assert dashboard_link(settings, {"KTDM_FRONTEND_ORIGINS": "http://127.0.0.1:12905"}) is None
    text = format_alert("firing", _transition("firing", "critical").alert.as_dict(), "odroid", None)
    assert "대시보드" not in text


@pytest.mark.parametrize(("key", "value", "message"), [
    ("digest_hour_kst", 24, "0~23"),
    ("api_base", "http://api.telegram.org", "https://"),
    ("chat_id_env", "", "비울 수 없습니다"),
])
def test_telegram_config_is_validated(config, key, value, message):
    document = json.loads(json.dumps({
        "version": 1, "interval_seconds": 120, "storage": dict(config.storage),
        "rules": {name: dict(item) for name, item in config.rules.items()},
        "diagnostics": dict(config.diagnostics),
        "notifiers": {name: dict(item) for name, item in config.notifiers.items()},
    }))
    document["notifiers"]["telegram"][key] = value
    with pytest.raises(wd.ConfigError, match=message):
        wd.parse_config(document)


def test_watchdog_routes_transitions_to_telegram_and_lists_it(config, monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    sent: list[dict[str, Any]] = []

    class Probes:
        now = _epoch("2026-11-24T01:00:00Z")
        streak = True

        def host(self): return "n150"
        def plane_expected(self): return True
        def instigators(self): return []
        def runs_since(self, since):
            if not self.streak:
                return []
            return [pr.RunRecord(f"r{i}", "job", "pinvi.etl.definitions", None, "FAILURE", self.now - 600 + i,
                                 self.now - 500 + i, True) for i in range(5)]
        def schedule_runs(self, schedules): return {key: [] for key in schedules}
        def tagged_job_runs(self, location, job, tag_key, since, limit): return []
        def disks(self, paths): return [pr.DiskObservation("/", 100, 10, 90)], []
        def io_pressure(self): return None
        def dagster_fds(self): return [], []
        def capture(self, location, context): return None
        def capture_fds(self, container, context): return None

    probes = Probes()
    watchdog = wd.PlaneWatchdog(config, probes=probes, clock=lambda: probes.now, run_diagnostics_inline=True)
    handler = httpx.MockTransport(lambda request: sent.append(json.loads(request.content)) or httpx.Response(200))
    watchdog._telegram._transport = handler
    watchdog.evaluate_once()
    assert any("launch 5회 연속 실패" in item["text"] for item in sent)
    names = [item["name"] for item in watchdog.status_payload()["notifiers"]]
    assert names == ["webhook", "telegram"]
    telegram = watchdog.status_payload()["notifiers"][1]
    assert telegram["enabled"] is True and "위험 즉시" in telegram["policy"]
    assert TOKEN not in json.dumps(watchdog.status_payload())
