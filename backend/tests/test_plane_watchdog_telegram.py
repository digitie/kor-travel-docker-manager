"""수집 경보 Telegram 발송 — 위험 즉시(재시도)·주의 하루 1회 요약·반복 억제·해소 1회·비밀 비노출."""

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
    format_digest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "123456:SECRET-TOKEN-VALUE"
ENV = {"PINVI_TELEGRAM_BOT_TOKEN_DEFAULT": TOKEN, "KTDM_ALERT_TELEGRAM_CHAT_ID": "-100200",
       "KTDM_ALERT_DASHBOARD_URL": "https://manager.example.org/"}
STALL = "location_tick_stall:pinvi.etl.definitions"
RULES = set(wd.RULE_LABELS)


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


class Telegram:
    """가짜 Telegram API. ``status``를 바꾸면 그 응답을 돌려준다."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.status = 200
        self.body: dict[str, Any] = {"ok": True}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.status >= 300:
            return httpx.Response(self.status, json=self.body)
        self.sent.append({"url": str(request.url), **json.loads(request.content)})
        return httpx.Response(200, json={"ok": True})

    def heads(self) -> list[str]:
        return [item["text"].splitlines()[0] for item in self.sent]


def _notifier(config, clock, api: Telegram, tmp_path=None, env=ENV) -> TelegramNotifier:
    return TelegramNotifier(config.notifiers["telegram"], host="n150",
                            state_path=None if tmp_path is None else tmp_path / "telegram-state.json",
                            transport=httpx.MockTransport(api), clock=clock, environ=env)


def _alert(severity: str, key: str = STALL, since: str = "2026-11-24T00:00:00Z") -> dict[str, Any]:
    rule, subject = key.split(":", 1)
    finding = wd.Finding(rule, subject, severity, f"{subject}: 40분째 schedule·sensor tick이 없습니다",
                         "daemon·code-server 스레드 상태를 진단 파일로 남깁니다.", {"silence_seconds": 2400})
    return wd.Alert(finding, since=_epoch(since), last_seen=_epoch(since)).as_dict()


def test_repository_config_points_at_the_pinvi_system_bot(config):
    telegram = config.notifiers["telegram"]
    assert telegram["token_env"] == "PINVI_TELEGRAM_BOT_TOKEN_DEFAULT"
    assert telegram["chat_id_env"] == "KTDM_ALERT_TELEGRAM_CHAT_ID"
    assert telegram["api_base"] == "https://api.telegram.org"
    assert telegram["enabled"] is True and 0 <= telegram["digest_hour_kst"] <= 23
    assert "KTDM_ALERT_TELEGRAM_CHAT_ID" in (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


def test_missing_token_or_chat_disables_delivery_and_says_why(config):
    api = Telegram()
    notifier = _notifier(config, Clock("2026-11-24T01:00:00Z"), api, env={"PINVI_TELEGRAM_BOT_TOKEN_DEFAULT": TOKEN})
    notifier.reconcile([_alert("critical")], RULES)
    assert api.sent == [] and notifier.state()["enabled"] is False
    assert notifier.state()["disabled_reason"] == "KTDM_ALERT_TELEGRAM_CHAT_ID이(가) 비어 있습니다"
    only_chat = _notifier(config, Clock("2026-11-24T01:00:00Z"), api, env={"KTDM_ALERT_TELEGRAM_CHAT_ID": "1"})
    assert only_chat.state()["disabled_reason"] == "PINVI_TELEGRAM_BOT_TOKEN_DEFAULT이(가) 비어 있습니다"


def test_critical_is_immediate_with_host_rule_subject_and_link(config, caplog):
    caplog.set_level("DEBUG")
    api = Telegram()
    notifier = _notifier(config, Clock("2026-11-24T01:00:00Z"), api)
    notifier.reconcile([_alert("critical")], RULES)
    [message] = api.sent
    assert message["chat_id"] == "-100200" and message["disable_web_page_preview"] is True
    assert message["url"].endswith("/sendMessage")
    text = message["text"]
    assert text.startswith("[위험] 수집 경보 · n150")
    assert "규칙: location tick 정지" in text and "대상: pinvi.etl.definitions" in text
    assert "40분째 schedule·sensor tick이 없습니다" in text
    assert "대시보드: https://manager.example.org/#plane-alerts" in text
    assert TOKEN not in text and TOKEN not in json.dumps(notifier.state())
    # httpx의 요청 로그(URL 경로에 토큰)를 가린다.
    assert "HTTP Request" in caplog.text and TOKEN not in caplog.text and "/bot<redacted>/" in caplog.text
    # 계속 울리는 동안 다시 보내지 않는다.
    notifier.reconcile([_alert("critical")], RULES)
    assert len(api.sent) == 1


def test_failed_send_is_retried_with_backoff_and_resolution_still_sent(config):
    api = Telegram()
    clock = Clock("2026-11-24T01:00:00Z")
    notifier = _notifier(config, clock, api)
    api.status, api.body = 429, {"ok": False, "parameters": {"retry_after": 300}}
    notifier.reconcile([_alert("critical")], RULES)
    assert api.sent == [] and notifier.last_error == "HTTP 429"
    api.status = 200
    clock.now += 120  # retry_after(300초) 안 — 보내지 않는다
    notifier.reconcile([_alert("critical")], RULES)
    assert api.sent == []
    clock.now += 240
    notifier.reconcile([_alert("critical")], RULES)
    assert api.heads() == ["[위험] 수집 경보 · n150"] and notifier.last_error is None
    clock.now += 600
    notifier.reconcile([], RULES)
    assert api.heads() == ["[위험] 수집 경보 · n150", "[해소] 수집 경보 · n150"]


def test_resolution_waits_until_the_rule_is_observed(config):
    # 측정 실패(그 규칙 미관측)나 Manager 재시작 직후의 빈 경보 목록으로 거짓 해소를 보내지 않는다.
    api = Telegram()
    clock = Clock("2026-11-24T01:00:00Z")
    notifier = _notifier(config, clock, api)
    notifier.reconcile([_alert("critical")], RULES)
    notifier.reconcile([], RULES - {"location_tick_stall"})
    assert len(api.sent) == 1
    notifier.reconcile([], RULES)
    assert api.heads()[-1] == "[해소] 수집 경보 · n150"


def test_refiring_inside_the_window_is_held_then_sent_if_still_firing(config):
    api = Telegram()
    clock = Clock("2026-11-24T00:00:00Z")
    notifier = _notifier(config, clock, api)
    notifier.reconcile([_alert("critical")], RULES)
    clock.now += 5 * 3600 + 50 * 60
    notifier.reconcile([], RULES)  # 05:50 해소
    clock.now += 5 * 60
    notifier.reconcile([_alert("critical")], RULES)  # 05:55 다시 울림 — 억제 창(6시간) 안
    assert len(api.sent) == 2 and notifier.state()["suppressed"] == 1
    clock.now += 6 * 3600  # 창(해소 뒤 6시간)이 끝났는데 아직 울린다 → 보낸다
    notifier.reconcile([_alert("critical")], RULES)
    assert api.heads() == ["[위험] 수집 경보 · n150", "[해소] 수집 경보 · n150", "[위험] 수집 경보 · n150"]
    # 억제 창 안에 다시 울렸다 풀리면 아무것도 보내지 않는다.
    clock.now += 600
    notifier.reconcile([], RULES)
    clock.now += 60
    notifier.reconcile([_alert("critical")], RULES)
    clock.now += 60
    notifier.reconcile([], RULES)
    assert api.heads()[-1] == "[해소] 수집 경보 · n150" and len(api.sent) == 4


def test_state_survives_a_manager_restart(config, tmp_path):
    api = Telegram()
    clock = Clock("2026-11-24T01:00:00Z")
    _notifier(config, clock, api, tmp_path).reconcile([_alert("critical")], RULES)
    assert TOKEN not in (tmp_path / "telegram-state.json").read_text(encoding="utf-8")
    clock.now += 300
    restarted = _notifier(config, clock, api, tmp_path)
    restarted.reconcile([_alert("critical")], RULES)  # 재시작 뒤 같은 경보 — 다시 보내지 않는다
    assert len(api.sent) == 1
    restarted.reconcile([], RULES)  # Manager가 꺼진 사이 풀렸어도 관측되면 해소를 보낸다
    assert api.heads()[-1] == "[해소] 수집 경보 · n150"


def test_warnings_wait_for_one_daily_digest_and_escalation_is_immediate(config, tmp_path):
    api = Telegram()
    clock = Clock("2026-11-23T20:00:00Z")  # 11-24 05:00 KST — 요약 시각(09시) 전
    notifier = _notifier(config, clock, api, tmp_path)
    analytics = "analytics_refresh_lag:kor-travel-transport/airport_collection_job"
    notifier.reconcile([_alert("warning", analytics), _alert("warning", "io_pressure:io")], RULES)
    clock.now += 600
    notifier.reconcile([_alert("warning", analytics)], RULES)  # io 풀림
    assert api.sent == []
    clock.now = _epoch("2026-11-24T00:30:00Z")  # 09:30 KST
    notifier.reconcile([_alert("warning", analytics)], RULES)
    [digest] = api.sent
    assert digest["text"].startswith("[주의 요약] 수집 경보 · n150")
    assert "울린 주의 2건 · 지금 울리는 주의 1건 · 풀린 주의 1건" in digest["text"]
    assert "지금 울리는 주의:\n- 공항 통계 사전집계 갱신 ·" in digest["text"]
    assert "그사이 울렸다 풀린 주의:\n- IO 압력(PSI) ·" in digest["text"]
    assert digest["text"].endswith("대시보드: https://manager.example.org/#plane-alerts")
    clock.now += 3600
    notifier.reconcile([_alert("warning", analytics)], RULES)
    assert len(api.sent) == 1  # 같은 날 두 번 보내지 않는다
    notifier.reconcile([_alert("critical", analytics)], RULES)  # 주의 → 위험
    assert "주의에서 위험으로 올랐습니다" in api.sent[-1]["text"]
    clock.now = _epoch("2026-11-26T00:10:00Z")
    notifier.reconcile([], RULES)
    assert api.heads()[-1] == "[해소] 수집 경보 · n150"  # 요약에는 "풀린 주의"로 다시 나오지 않는다
    assert not any(head.startswith("[주의 요약]") for head in api.heads()[1:])


def test_long_digest_keeps_the_link(config):
    items = [_alert("warning", f"consecutive_failures:loc/job_{index}") for index in range(80)]
    text = format_digest("n150", items, items, items, "https://m.example.org/#plane-alerts")
    assert len(text) <= 4000 and text.endswith("대시보드: https://m.example.org/#plane-alerts")
    assert "외 60건" in text


def test_unexpected_client_error_keeps_the_round_and_hides_the_token(config):
    clock = Clock("2026-11-24T01:00:00Z")

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL(f"bad {request.url}")
    notifier = TelegramNotifier(config.notifiers["telegram"], host="n150", state_path=None,
                                transport=httpx.MockTransport(broken), clock=clock, environ=ENV)
    notifier.reconcile([_alert("critical"), _alert("critical", "tick_failures:x")], RULES)
    assert notifier.last_error == "InvalidURL" and TOKEN not in json.dumps(notifier.state())


def test_dashboard_link_falls_back_to_the_frontend_origin(config):
    settings = config.notifiers["telegram"]
    assert dashboard_link(settings, {"KTDM_FRONTEND_ORIGINS": "http://127.0.0.1:12905,https://m.example.org"}) == \
        "https://m.example.org/#plane-alerts"
    assert dashboard_link(settings, {"KTDM_FRONTEND_ORIGINS": "http://127.0.0.1:12905"}) is None
    assert "대시보드" not in format_alert("firing", _alert("critical"), "odroid", None)


@pytest.mark.parametrize(("key", "value", "message"), [
    ("digest_hour_kst", 24, "0~23"),
    ("api_base", "http://api.telegram.org", "https://"),
    ("chat_id_env", "", "비울 수 없습니다"),
    ("timeout_seconds", 120, "1~30"),
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


def test_watchdog_routes_alerts_to_telegram_and_lists_it(config, monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    api = Telegram()

    class Probes:
        now = _epoch("2026-11-24T01:00:00Z")

        def host(self): return "n150"
        def plane_expected(self): return True
        def instigators(self): return []
        def runs_since(self, since):
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
    watchdog._telegram._transport = httpx.MockTransport(api)
    watchdog.evaluate_once()
    assert any("launch 5회 연속 실패" in item["text"] for item in api.sent)
    payload = watchdog.status_payload()
    assert [item["name"] for item in payload["notifiers"]] == ["webhook", "telegram"]
    assert payload["notifiers"][1]["enabled"] is True and "위험 즉시" in payload["notifiers"][1]["policy"]
    assert TOKEN not in json.dumps(payload)


def test_tests_do_not_inherit_a_real_bot(config):
    # conftest가 두 변수를 지운다 — 운영 체크아웃 옆에서 pytest를 돌려도 실제 chat으로 나가지 않는다.
    watchdog = wd.PlaneWatchdog(config, probes=object(), clock=lambda: 0.0)  # type: ignore[arg-type]
    assert watchdog._telegram is not None and watchdog._telegram.active is False
