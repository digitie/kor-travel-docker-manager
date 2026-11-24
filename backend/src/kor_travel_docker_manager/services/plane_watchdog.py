"""공용 Dagster plane 수집 감시(plane watchdog) — Dagster 밖에서 도는 경보(감사 §4 #1·#3·#19).

2026-11-23 수집 실패 감사는 두 plane 모두 경보가 없어 PinVi(11-13~16)·concierge(11-14~18) run 전부
실패, odroid 특보 이벤트 11-18부터 100% 실패를 아무도 몰랐다고 적었다. 이 모듈은 Manager backend
(systemd)의 백그라운드 루프에서 돌며 다음을 판정한다.

| 규칙 | 근거 자료 |
|---|---|
| location별 N분간 tick 없음 | instigator별 마지막 tick |
| schedule별 k주기 동안 SUCCESS 없음 | schedule tag가 붙은 종료 run + cron 주기 |
| job별 연속 실패 N회 | 종료 run |
| run launch 실패율 | 시작 없이 FAILURE가 된 run의 비율 |
| 공항 통계 사전집계 갱신 지연 | transport 공항 수집 run의 `kortravel/analytics-refresh` 태그 |
| 디스크 사용률·IO 압력(PSI) | statvfs·`/proc/pressure/io` |
| Dagster 컨테이너 열린 fd / nofile 한도 | `/proc/<pid>/fd`·`limits` |
| storage에 닿지 못함 | 위 질의의 연속 실패 |

설계 원칙:

1. **측정과 판정을 나눈다.** 판정 함수(`evaluate_*`)는 시계·파일·네트워크를 만지지 않는 순수
   함수다 — 감사 당시 실제 사건을 fixture로 재생해 결박한다.
2. **Dagster를 믿지 않는다.** Dagster sensor로 만들지 않는다(그 plane이 멈추면 경보도 멈춘다).
   storage는 공용 PostgreSQL을 읽기 전용 트랜잭션으로 직접 읽는다.
3. **죽음이 보이게 한다.** 상태 API는 마지막 평가 시각과 나이를 싣는다. 감시자가 멈추면 화면이
   "마지막 평가 N분 전"으로 그것을 보여 준다. 측정이 실패한 규칙은 "확인 불가"로 보이고, 그 규칙의
   기존 경보는 측정이 돌아올 때까지 그대로 둔다(측정 실패로 경보가 조용히 풀리지 않는다).
4. **발송은 플러그형, 기본 꺼짐.** 기본 경로는 화면 경보 패널·상태 API·Manager 로그다.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from kor_travel_docker_manager.services import plane_watchdog_probe as probe_module
from kor_travel_docker_manager.services.cron_period import cron_period
from kor_travel_docker_manager.services.plane_watchdog_probe import (
    ContainerFds,
    DiskObservation,
    Instigator,
    ProbeError,
    RunRecord,
    TaggedRun,
)
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

logger = logging.getLogger(__name__)

PLANE_ALERTS_SCHEMA = "kor-travel-docker-manager.plane-alerts.v1"
CONFIG_RELATIVE_PATH = Path("config/plane-watchdog.yml")

#: 규칙 id → 화면 이름. 순서가 화면의 점검 목록 순서다.
RULE_LABELS: dict[str, str] = {
    "storage_unreachable": "Dagster storage 접근",
    "location_tick_stall": "location tick 정지",
    "schedule_no_success": "schedule 주기 내 성공 없음",
    "tick_failures": "schedule·sensor tick 연속 실패",
    "consecutive_failures": "job 연속 실패",
    "launch_failure_streak": "location launch 연속 실패",
    "launch_failure_rate": "run launch 실패율",
    "analytics_refresh_lag": "공항 통계 사전집계 갱신",
    "disk_usage": "디스크 사용률",
    "io_pressure": "IO 압력(PSI)",
    "dagster_fd_usage": "Dagster 열린 fd",
}
_STORAGE_RULES = ("location_tick_stall", "schedule_no_success", "tick_failures", "consecutive_failures",
                  "launch_failure_streak", "launch_failure_rate", "analytics_refresh_lag")
_SEVERITIES = ("warning", "critical")
_SENSOR_DEFAULT_INTERVAL_SECONDS = 30.0  # Dagster sensor의 기본 minimum_interval_seconds
_RESOLVED_HISTORY = 50
_RETAIN_FEED_HOURS = 48
_FEED_OVERLAP_SECONDS = 300
_SCHEDULE_BACKFILL_LIMIT = 10
#: 긴 창 schedule의 최근 run을 다시 읽는 간격. feed가 이보다 길게(_RETAIN_FEED_HOURS) 남으므로
#: 두 자료의 합집합은 끊기지 않는다.
_SCHEDULE_BACKFILL_REFRESH_SECONDS = 6 * 3600
#: 사전집계 규칙이 연속 횟수 외에 더 읽는 run 수(태그 없는 실패 run을 건너뛰고도 연속을 셀 여유).
_ANALYTICS_RUN_MARGIN = 8


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class WatchdogConfig:
    interval_seconds: int
    storage: Mapping[str, Any]
    rules: Mapping[str, Mapping[str, Any]]
    diagnostics: Mapping[str, Any]
    notifiers: Mapping[str, Mapping[str, Any]]

    def rule(self, name: str) -> Mapping[str, Any]:
        return self.rules[name]

    def enabled(self, name: str) -> bool:
        return bool(self.rules.get(name, {}).get("enabled", False))


_RULE_KEYS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "location_tick_stall": {"enabled": bool, "severity": str, "min_silence_minutes": (int, float),
                            "cadence_multiplier": (int, float), "activity_lookback_hours": (int, float)},
    "schedule_no_success": {"enabled": bool, "severity": str, "cycles": int, "grace_minutes": (int, float)},
    "tick_failures": {"enabled": bool, "severity": str, "count": int},
    "consecutive_failures": {"enabled": bool, "severity": str, "count": int, "active_hours": (int, float)},
    "launch_failure_streak": {"enabled": bool, "severity": str, "count": int, "active_hours": (int, float)},
    "launch_failure_rate": {"enabled": bool, "severity": str, "window_minutes": (int, float),
                            "min_failures": int, "min_ratio": (int, float)},
    "analytics_refresh_lag": {"enabled": bool, "location": str, "job": str, "tag_key": str,
                              "warning_values": list, "critical_values": list, "consecutive_runs": int,
                              "active_hours": (int, float)},
    "disk_usage": {"enabled": bool, "warning_used_percent": (int, float),
                   "critical_used_percent": (int, float), "paths": list},
    "io_pressure": {"enabled": bool, "severity": str, "full_avg300_percent": (int, float),
                    "for_evaluations": int},
    "dagster_fd_usage": {"enabled": bool, "warning_ratio": (int, float), "critical_ratio": (int, float)},
}
_SECTION_KEYS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "storage": {"statement_timeout_seconds": int, "bootstrap_hours": (int, float),
                "unreachable_after_failures": int},
    "diagnostics": {"enabled": bool, "directory": str, "py_spy": str, "min_interval_minutes": (int, float),
                    "max_captures": int, "log_tail_minutes": int},
}
_WEBHOOK_KEYS: dict[str, type | tuple[type, ...]] = {"enabled": bool, "url_env": str,
                                                      "timeout_seconds": (int, float)}


def _check_mapping(value: Any, keys: Mapping[str, type | tuple[type, ...]], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: mapping이어야 합니다")
    if set(value) != set(keys):
        missing = sorted(set(keys) - set(value))
        extra = sorted(set(value) - set(keys))
        raise ConfigError(f"{where}: 키가 다릅니다(빠짐 {missing}, 모름 {extra})")
    for key, expected in keys.items():
        item = value[key]
        # bool은 int의 하위형이다 — 숫자 자리에 true를 쓰면 거부한다.
        if isinstance(item, bool) and expected is not bool:
            raise ConfigError(f"{where}.{key}: 형식이 틀렸습니다")
        if not isinstance(item, expected):
            raise ConfigError(f"{where}.{key}: 형식이 틀렸습니다")
        if isinstance(item, (int, float)) and not isinstance(item, bool) and item < 0:
            raise ConfigError(f"{where}.{key}: 음수일 수 없습니다")
    if "severity" in value and value["severity"] not in _SEVERITIES:
        raise ConfigError(f"{where}.severity: {_SEVERITIES} 중 하나여야 합니다")
    return dict(value)


def _check_analytics_refresh(rule: Mapping[str, Any]) -> None:
    where = "rules.analytics_refresh_lag"
    for key in ("location", "job", "tag_key"):
        if not str(rule[key]).strip():
            raise ConfigError(f"{where}.{key}: 비울 수 없습니다")
    for key in ("warning_values", "critical_values"):
        values = rule[key]
        if not values or not all(isinstance(value, str) and value.strip() for value in values):
            raise ConfigError(f"{where}.{key}: 비어 있지 않은 문자열 목록이어야 합니다")
    if set(rule["warning_values"]) & set(rule["critical_values"]):
        raise ConfigError(f"{where}: warning_values와 critical_values가 겹칩니다")
    if int(rule["consecutive_runs"]) < 1:
        raise ConfigError(f"{where}.consecutive_runs: 1 이상이어야 합니다")


def parse_config(document: Any) -> WatchdogConfig:
    if not isinstance(document, dict):
        raise ConfigError("최상위는 mapping이어야 합니다")
    expected = {"version", "interval_seconds", "storage", "rules", "diagnostics", "notifiers"}
    if set(document) != expected:
        raise ConfigError(f"최상위 키가 다릅니다: {sorted(set(document) ^ expected)}")
    if document["version"] != 1:
        raise ConfigError("version은 1이어야 합니다")
    interval = document["interval_seconds"]
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 30:
        raise ConfigError("interval_seconds는 30 이상의 정수여야 합니다")
    rules_raw = document["rules"]
    if not isinstance(rules_raw, dict) or set(rules_raw) != set(_RULE_KEYS):
        raise ConfigError(f"rules는 {sorted(_RULE_KEYS)}를 정확히 가져야 합니다")
    rules = {name: _check_mapping(rules_raw[name], keys, f"rules.{name}") for name, keys in _RULE_KEYS.items()}
    if not all(isinstance(path, str) and path.startswith("/") for path in rules["disk_usage"]["paths"]):
        raise ConfigError("rules.disk_usage.paths는 절대 경로 목록이어야 합니다")
    if rules["disk_usage"]["warning_used_percent"] > rules["disk_usage"]["critical_used_percent"]:
        raise ConfigError("rules.disk_usage: warning이 critical보다 클 수 없습니다")
    if rules["dagster_fd_usage"]["warning_ratio"] > rules["dagster_fd_usage"]["critical_ratio"]:
        raise ConfigError("rules.dagster_fd_usage: warning이 critical보다 클 수 없습니다")
    _check_analytics_refresh(rules["analytics_refresh_lag"])
    storage = _check_mapping(document["storage"], _SECTION_KEYS["storage"], "storage")
    diagnostics = _check_mapping(document["diagnostics"], _SECTION_KEYS["diagnostics"], "diagnostics")
    notifiers_raw = document["notifiers"]
    if not isinstance(notifiers_raw, dict) or set(notifiers_raw) != {"webhook"}:
        raise ConfigError("notifiers는 webhook 하나를 가져야 합니다")
    notifiers = {"webhook": _check_mapping(notifiers_raw["webhook"], _WEBHOOK_KEYS, "notifiers.webhook")}
    return WatchdogConfig(interval_seconds=interval, storage=storage, rules=rules,
                          diagnostics=diagnostics, notifiers=notifiers)


def load_config(path: Path | None = None) -> WatchdogConfig:
    if path is None:
        from kor_travel_docker_manager.services.registry import get_project_root

        path = Path(get_project_root()) / CONFIG_RELATIVE_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path.name}을 읽을 수 없습니다: {exc.strerror or exc}") from exc
    try:
        document = load_yaml_rejecting_duplicate_keys(text)
    except Exception as exc:  # noqa: BLE001 - YAML 오류 종류와 무관하게 설정 오류다
        raise ConfigError(f"{path.name}을 해석할 수 없습니다: {exc}") from exc
    return parse_config(document)


# ---------------------------------------------------------------------------
# 판정(순수 함수)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    rule: str
    subject: str
    severity: str
    title: str
    detail: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.rule}:{self.subject}"


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(epoch, _dt.UTC).isoformat().replace("+00:00", "Z")


def _human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}분"
    if seconds < 2 * 86400:
        hours, rest = divmod(seconds, 3600)
        return f"{hours}시간 {rest // 60}분" if rest >= 60 else f"{hours}시간"
    return f"{seconds / 86400:.1f}일"


def expected_interval(instigator: Instigator) -> float | None:
    """instigator가 기대하는 tick 간격(초). 모르면 None."""

    if instigator.type == "SENSOR":
        return instigator.min_interval or _SENSOR_DEFAULT_INTERVAL_SECONDS
    period = cron_period(instigator.cron)
    return period.min_gap_seconds if period else None


def active_instigators(instigators: Iterable[Instigator], now: float, lookback_hours: float) -> list[Instigator]:
    """한 번이라도 tick했고, RUNNING이거나 lookback 안에 tick한 instigator.

    DB의 status만 믿지 않는다 — odroid transport는 STOPPED로 기록된 채 5분마다 tick한다(2026-11-24
    실측). 거꾸로 사람이 끈 instigator는 lookback이 지나면 활성에서 빠진다.
    """

    horizon = now - lookback_hours * 3600
    return [
        item for item in instigators
        if item.last_tick is not None and (item.status == "RUNNING" or item.last_tick >= horizon)
    ]


#: code-server가 응답하지 못하는 흔적. EMFILE(열린 fd 한도)는 문구에 그대로 실리기도 하지만, n150 PinVi
#: 사건(11-14)의 sensor tick 오류는 `DagsterUserCodeUnreachableError` → `DagsterCodeLocationLoadError`로만
#: 남았다(2026-11-24 tick_body 실측, "Too many open files" 문구 없음). 둘 다 code-server 재시작 제안 대상이다.
_EMFILE_MARKERS = ("Too many open files", "Errno 24", "EMFILE")
_UNREACHABLE_MARKERS = ("DagsterUserCodeUnreachableError", "DagsterCodeLocationLoadError")


def is_emfile(text: str) -> bool:
    return any(marker in text for marker in _EMFILE_MARKERS)


def is_code_server_unreachable(text: str) -> bool:
    return is_emfile(text) or any(marker in text for marker in _UNREACHABLE_MARKERS)


def restart_hint(location: str, containers_by_location: Mapping[str, str]) -> str:
    """code-server 재시작 **제안** 문구. Manager는 스스로 재시작하지 않는다(운영 runtime 변경은 사람 몫)."""

    container = containers_by_location.get(location) or "<그 location의 code-server 컨테이너>"
    return ("code-server가 run을 띄우거나 sensor를 평가하지 못하고 있습니다(fd 고갈 등). 컨테이너 "
            "healthcheck는 이때도 healthy로 남습니다(2026-11-13~18 실측). Dagster 열린 fd 점검을 함께 보고 "
            f"재시작을 검토하세요(자동 재시작 없음): sudo -n docker restart {container}")


@dataclass(frozen=True)
class LocationTickState:
    location: str
    last_tick: float
    silence_seconds: float
    threshold_seconds: float
    active_instigators: int


def evaluate_location_stalls(
    instigators: Sequence[Instigator], now: float, rule: Mapping[str, Any]
) -> tuple[list[Finding], list[LocationTickState]]:
    by_location: dict[str, list[Instigator]] = defaultdict(list)
    for item in active_instigators(instigators, now, float(rule["activity_lookback_hours"])):
        if item.location:
            by_location[item.location].append(item)
    findings: list[Finding] = []
    states: list[LocationTickState] = []
    for location, items in sorted(by_location.items()):
        last_tick = max(item.last_tick for item in items if item.last_tick is not None)
        intervals = [value for value in (expected_interval(item) for item in items) if value]
        if not intervals:
            continue  # 기대 간격을 모르면(해석 못 한 cron뿐) 정지를 판정하지 않는다.
        threshold = max(float(rule["min_silence_minutes"]) * 60,
                        float(rule["cadence_multiplier"]) * min(intervals))
        silence = now - last_tick
        states.append(LocationTickState(location, last_tick, silence, threshold, len(items)))
        if silence > threshold:
            findings.append(Finding(
                rule="location_tick_stall", subject=location, severity=str(rule["severity"]),
                title=f"{location}: {_human_duration(silence)}째 schedule·sensor tick이 없습니다",
                detail=(f"활성 schedule·sensor {len(items)}개 중 어느 것도 tick을 남기지 않았습니다. "
                        f"마지막 tick {_iso(last_tick)}, 임계 {_human_duration(threshold)}. "
                        "daemon·code-server 스레드 상태를 진단 파일로 남깁니다."),
                evidence={"last_tick_at": _iso(last_tick), "silence_seconds": round(silence),
                          "threshold_seconds": round(threshold), "active_instigators": len(items)},
            ))
    return findings, states


def _schedule_window(instigator: Instigator, rule: Mapping[str, Any]) -> float | None:
    period = cron_period(instigator.cron)
    if period is None:
        return None
    return int(rule["cycles"]) * period.max_gap_seconds + float(rule["grace_minutes"]) * 60


def schedules_needing_backfill(
    instigators: Sequence[Instigator], now: float, coverage_start: float,
    rule: Mapping[str, Any], lookback_hours: float,
) -> list[tuple[str, str]]:
    """증분 feed가 덮지 못하는 긴 창의 schedule(location, 이름)."""

    wanted = []
    for item in active_instigators(instigators, now, lookback_hours):
        if item.type != "SCHEDULE" or not item.location:
            continue
        window = _schedule_window(item, rule)
        if window is not None and now - window < coverage_start:
            wanted.append((item.location, item.name))
    return sorted(set(wanted))


def evaluate_schedule_no_success(
    instigators: Sequence[Instigator],
    feed: Sequence[RunRecord],
    backfill: Mapping[tuple[str, str], Sequence[RunRecord]],
    now: float,
    coverage_start: float,
    rule: Mapping[str, Any],
    lookback_hours: float,
) -> list[Finding]:
    """창 = cycles × cron 최대 간격 + grace. 창 안 종료 run이 있고 성공이 0이면 낸다.

    run은 그 schedule이 띄운 run의 job(같은 location)으로 모은다 — 재실행 run은 schedule tag가
    없을 수 있으나 같은 job이므로 성공으로 센다.
    """

    jobs_by_schedule: dict[tuple[str, str], set[str]] = defaultdict(set)
    feed_by_job: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for record in feed:
        feed_by_job[(record.location, record.job)].append(record)
        if record.schedule:
            jobs_by_schedule[(record.location, record.schedule)].add(record.job)
    for key, records in backfill.items():
        for record in records:
            jobs_by_schedule[key].add(record.job)

    findings: list[Finding] = []
    for item in active_instigators(instigators, now, lookback_hours):
        if item.type != "SCHEDULE" or not item.location:
            continue
        window = _schedule_window(item, rule)
        if window is None:
            continue
        key = (item.location, item.name)
        start = now - window
        if start < coverage_start and key not in backfill:
            continue  # 창 앞부분을 모른다 — 모르는 것으로 경보를 내지 않는다.
        jobs = jobs_by_schedule.get(key, set())
        if not jobs:
            continue
        runs: dict[str, RunRecord] = {}
        for job in jobs:
            for record in feed_by_job.get((item.location, job), []):
                runs[record.run_id] = record
        for record in backfill.get(key, []):
            runs[record.run_id] = record
        in_window = [record for record in runs.values() if record.created >= start]
        successes = [record for record in in_window if record.status == "SUCCESS"]
        failures = [record for record in in_window if record.status in ("FAILURE", "CANCELED")]
        if successes or not failures:
            continue
        last_success = max((record.created for record in runs.values() if record.status == "SUCCESS"),
                           default=None)
        job_text = ", ".join(sorted(jobs))
        findings.append(Finding(
            rule="schedule_no_success", subject=f"{item.location}/{item.name}",
            severity=str(rule["severity"]),
            title=f"{job_text}: 최근 {int(rule['cycles'])}주기({_human_duration(window)}) 동안 성공이 없습니다",
            detail=(f"schedule {item.name}({item.location}, cron {item.cron})의 창 안 종료 run "
                    f"{len(failures)}건이 모두 실패·취소입니다. 마지막 성공: "
                    f"{_iso(last_success) or '관측 범위 밖'}."),
            evidence={"jobs": sorted(jobs), "window_seconds": round(window), "failures": len(failures),
                      "last_success_at": _iso(last_success),
                      "latest_failure_at": _iso(max(record.updated for record in failures))},
        ))
    return findings


def evaluate_tick_failures(
    instigators: Sequence[Instigator], now: float, rule: Mapping[str, Any], lookback_hours: float,
    containers_by_location: Mapping[str, str] | None = None,
) -> list[Finding]:
    """location별로, 최근 tick ``count``개가 모두 FAILURE인 schedule·sensor를 모은다.

    n150 fd 고갈 때 PinVi·concierge의 sensor tick이 같은 OSError로 55·100시간 실패했다.
    """

    containers = containers_by_location or {}
    count = int(rule["count"])
    failing: dict[str, list[Instigator]] = defaultdict(list)
    for item in active_instigators(instigators, now, lookback_hours):
        ticks = item.recent_ticks[:count]
        if item.location and len(ticks) >= count and all(status == "FAILURE" for _, status, _ in ticks):
            failing[item.location].append(item)
    findings: list[Finding] = []
    for location, items in sorted(failing.items()):
        # 오류 문구에는 instigator 이름이 섞이므로 예외 종류(첫 줄의 ':' 앞)로 묶는다.
        kinds: dict[str, int] = defaultdict(int)
        for item in items:
            first_line = (item.recent_ticks[0][2] or "(오류 문구 없음)").splitlines()[0]
            kinds[first_line.split(":", 1)[0][:160]] += 1
        common, common_count = max(kinds.items(), key=lambda pair: pair[1])
        latest_errors = [item.recent_ticks[0][2] for item in items]
        emfile = any(is_emfile(text) for text in latest_errors)
        unreachable = any(is_code_server_unreachable(text) for text in latest_errors)
        names = sorted(item.name for item in items)
        detail = (f"{', '.join(names[:8])}{' 외' if len(names) > 8 else ''}. "
                  f"가장 흔한 오류({common_count}개): {common}.")
        if unreachable:
            detail += " " + restart_hint(location, containers)
        findings.append(Finding(
            rule="tick_failures", subject=location, severity=str(rule["severity"]),
            title=f"{location}: schedule·sensor {len(items)}개가 최근 tick {count}회 연속 실패",
            detail=detail,
            evidence={"instigators": names, "common_error": common, "emfile": emfile,
                      "code_server_unreachable": unreachable,
                      "latest_failure_at": _iso(max(item.recent_ticks[0][0] for item in items))},
        ))
    return findings


def evaluate_launch_failure_streak(
    feed: Sequence[RunRecord], now: float, rule: Mapping[str, Any],
    containers_by_location: Mapping[str, str] | None = None,
) -> list[Finding]:
    """location별로 최근 종료 run이 ``count``회 연속 "시작하지 못한 FAILURE"인가.

    시작한 run(성공이든 실패든)이 끼면 끊는다. 큐에서 취소된 run은 건너뛴다.
    """

    containers = containers_by_location or {}
    by_location: dict[str, list[RunRecord]] = defaultdict(list)
    for record in feed:
        by_location[record.location].append(record)
    threshold = int(rule["count"])
    horizon = now - float(rule["active_hours"]) * 3600
    findings: list[Finding] = []
    for location, records in sorted(by_location.items()):
        if not location:
            continue
        streak: list[RunRecord] = []
        for record in sorted(records, key=lambda r: (r.updated, r.created), reverse=True):
            if record.status == "CANCELED" and record.never_started:
                continue
            if record.status == "FAILURE" and record.never_started:
                streak.append(record)
                continue
            break
        if len(streak) < threshold or streak[0].updated < horizon:
            continue
        jobs = sorted({record.job for record in streak})
        findings.append(Finding(
            rule="launch_failure_streak", subject=location, severity=str(rule["severity"]),
            title=f"{location}: run launch {len(streak)}회 연속 실패",
            detail=(f"{_iso(streak[-1].updated)}부터 이 location의 run이 하나도 시작하지 못했습니다"
                    f"(job {len(jobs)}개). " + restart_hint(location, containers)),
            evidence={"streak": len(streak), "jobs": jobs, "first_failure_at": _iso(streak[-1].updated),
                      "latest_failure_at": _iso(streak[0].updated)},
        ))
    return findings


def evaluate_consecutive_failures(
    feed: Sequence[RunRecord], now: float, rule: Mapping[str, Any],
    suppressed_jobs: Iterable[tuple[str, str]] = (),
) -> list[Finding]:
    suppressed = set(suppressed_jobs)
    by_job: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for record in feed:
        by_job[(record.location, record.job)].append(record)
    threshold = int(rule["count"])
    horizon = now - float(rule["active_hours"]) * 3600
    findings: list[Finding] = []
    for (location, job), records in sorted(by_job.items()):
        if (location, job) in suppressed:
            continue
        streak: list[RunRecord] = []
        for record in sorted(records, key=lambda r: (r.created, r.updated), reverse=True):
            if record.status == "SUCCESS":
                break
            if record.status == "FAILURE":
                streak.append(record)
        if len(streak) < threshold or streak[0].updated < horizon:
            continue
        findings.append(Finding(
            rule="consecutive_failures", subject=f"{location}/{job}", severity=str(rule["severity"]),
            title=f"{job}: 최근 {len(streak)}회 연속 실패",
            detail=(f"{location}의 {job}이 {_iso(streak[-1].created)}부터 {len(streak)}회 연속 FAILURE로 "
                    f"끝났습니다(관측 범위 안). 마지막 실패 {_iso(streak[0].updated)}."),
            evidence={"streak": len(streak), "latest_failure_at": _iso(streak[0].updated),
                      "first_failure_at": _iso(streak[-1].created)},
        ))
    return findings


def evaluate_launch_failures(feed: Sequence[RunRecord], now: float, rule: Mapping[str, Any]) -> list[Finding]:
    start = now - float(rule["window_minutes"]) * 60
    recent = [record for record in feed if record.updated >= start]
    launch = [record for record in recent if record.status == "FAILURE" and record.never_started]
    if not recent or len(launch) < int(rule["min_failures"]):
        return []
    ratio = len(launch) / len(recent)
    if ratio < float(rule["min_ratio"]):
        return []
    by_location: dict[str, int] = defaultdict(int)
    for record in launch:
        by_location[record.location or "?"] += 1
    breakdown = ", ".join(f"{location} {count}" for location, count in sorted(by_location.items()))
    return [Finding(
        rule="launch_failure_rate", subject="plane", severity=str(rule["severity"]),
        title=f"run launch 실패 {len(launch)}/{len(recent)}건({ratio:.0%}, 최근 {int(rule['window_minutes'])}분)",
        detail=(f"시작하지 못하고 FAILURE가 된 run: {breakdown}. code-server fd 고갈·gRPC 단절·code-server "
                "재시작을 먼저 의심합니다(Dagster 열린 fd 점검 참고)."),
        evidence={"launch_failures": len(launch), "terminal_runs": len(recent), "ratio": round(ratio, 3),
                  "by_location": dict(by_location)},
    )]


#: transport가 공항 수집 run 태그에 싣는 사전집계 상태(transport #283 `AnalyticsRefreshHealth`). 기준값은
#: transport `app/services/analytics_cache.py`가 정본이다 — 여기 문구는 화면 설명일 뿐 판정에 쓰지 않는다.
_ANALYTICS_STATUS_TEXT: dict[str, tuple[str, str]] = {
    "delayed": ("갱신 지연",
                "사전집계에 반영되지 않은 가장 오래된 적재가 15분을 넘었습니다. 대시보드는 아직 직전 사전집계를 냅니다"),
    "stale": ("갱신 멈춤",
              "대시보드가 원본 계산(ICN 25~30초, 504 위험)으로 떨어졌습니다 — 미반영 적재 40분 이상 또는 사전집계 2시간 초과"),
    "missing": ("사전집계 없음", "관측은 15분 넘게 쌓였는데 기본 사전집계가 없습니다"),
}


def relevant_analytics_runs(runs: Sequence[TaggedRun], now: float, rule: Mapping[str, Any]) -> list[TaggedRun]:
    """설정한 (location, job)의, ``active_hours`` 안에 끝난 종료 run(최신 먼저)."""

    horizon = now - float(rule["active_hours"]) * 3600
    picked = [item for item in runs
              if item.record.location == rule["location"] and item.record.job == rule["job"]
              and horizon <= item.record.updated <= now]
    return sorted(picked, key=lambda item: (item.record.created, item.record.updated), reverse=True)


def evaluate_analytics_refresh(runs: Sequence[TaggedRun], now: float, rule: Mapping[str, Any]) -> list[Finding]:
    """transport 공항 수집 run의 사전집계 상태 태그가 최근 ``consecutive_runs``회 연속 경보인가.

    transport는 판정이 경보(delayed·stale·missing)일 때만 자기 run에 태그를 달고, run은 SUCCESS로 둔다
    (transport #283 — 부분 성공을 FAILURE로 올리는 소음을 피했다). 그래서:

    - 태그 없이 SUCCESS로 끝난 run은 그 시점 판정이 정상(또는 판정 자체 실패)이므로 연속을 끊는다.
    - 태그 없는 FAILURE·CANCELED run은 판정 자료가 아니라 건너뛴다 — 그 실패는 `consecutive_failures`·
      `schedule_no_success`가 본다.
    - 심각도는 연속 구간 최근 N회 중 **가장 낮은 것**: 위험은 최근 N회가 모두 위험 값일 때만. 한 번 튄 stale로
      위험을 내지 않는다(오탐 억제).
    - 설정에 없는 값은 주의로 센다(경보 태그를 모른다고 정상으로 읽지 않는다).
    """

    needed = int(rule["consecutive_runs"])
    critical_values = set(rule["critical_values"])
    streak: list[TaggedRun] = []
    for item in relevant_analytics_runs(runs, now, rule):
        if item.tag is not None:
            streak.append(item)
            continue
        if item.record.status == "SUCCESS":
            break
    if len(streak) < needed:
        return []
    head = streak[:needed]
    severity = "critical" if all(item.tag in critical_values for item in head) else "warning"
    latest = streak[0]
    label, meaning = _ANALYTICS_STATUS_TEXT.get(str(latest.tag), (str(latest.tag), "transport가 모르는 상태를 남겼습니다"))
    location, job = str(rule["location"]), str(rule["job"])
    return [Finding(
        rule="analytics_refresh_lag", subject=f"{location}/{job}", severity=severity,
        title=f"공항 통계 사전집계 {label}: {job} 최근 {len(streak)}회 연속({latest.tag})",
        detail=(f"{meaning}. 공항 수집 run 자체는 SUCCESS일 수 있습니다 — 수집은 되는데 대시보드 묶음 갱신이 밀린 것입니다. "
                f"transport 관리 화면 홈의 운영 상태(`/v1/transport/providers`의 `airport_analytics_cache`)와 run 로그의 "
                f"`analytics_refresh_alert`(cache_refresh_failed·cache_refresh_skipped)를 확인하세요. "
                f"run 태그 `{rule['tag_key']}`, 최근 run {latest.record.run_id}."),
        evidence={"latest_status": latest.tag, "streak": len(streak),
                  "statuses": [item.tag for item in streak[:10]],
                  "latest_run_id": latest.record.run_id, "latest_at": _iso(latest.record.updated),
                  "first_alert_at": _iso(streak[-1].record.updated)},
    )]


def evaluate_disks(observations: Sequence[DiskObservation], rule: Mapping[str, Any]) -> list[Finding]:
    findings = []
    for item in observations:
        used = item.used_percent
        if used >= float(rule["critical_used_percent"]):
            severity = "critical"
        elif used >= float(rule["warning_used_percent"]):
            severity = "warning"
        else:
            continue
        free_gb = item.available_bytes / 1024**3
        findings.append(Finding(
            rule="disk_usage", subject=item.path, severity=severity,
            title=f"디스크 {item.path} 사용률 {used:.1f}%(남은 {free_gb:.1f} GB)",
            detail="디스크가 가득 차면 PostgreSQL이 WAL을 쓰지 못해 멈춥니다(2026-11 두 호스트 모두 실측).",
            evidence={"used_percent": round(used, 1), "available_bytes": item.available_bytes},
        ))
    return findings


def evaluate_io_pressure(pressure: Mapping[str, Mapping[str, float]], rule: Mapping[str, Any]) -> list[Finding]:
    value = pressure.get("full", {}).get("avg300")
    if value is None or value < float(rule["full_avg300_percent"]):
        return []
    return [Finding(
        rule="io_pressure", subject="io", severity=str(rule["severity"]),
        title=f"IO 정체: 최근 5분 중 {value:.0f}% 동안 모든 작업이 IO를 기다렸습니다",
        detail="PSI io full avg300. 테스트 게이트·백업·대량 적재가 겹치는지 확인합니다.",
        evidence={"full_avg300": value, "some_avg300": pressure.get("some", {}).get("avg300")},
    )]


def evaluate_fds(observations: Sequence[ContainerFds], rule: Mapping[str, Any]) -> list[Finding]:
    findings = []
    for item in observations:
        ratio = item.ratio
        if ratio is None:
            continue
        if ratio >= float(rule["critical_ratio"]):
            severity = "critical"
        elif ratio >= float(rule["warning_ratio"]):
            severity = "warning"
        else:
            continue
        detail = ("nofile soft 한도에 닿으면 run launch와 sensor tick이 `[Errno 24] Too many open files`로 "
                  "실패합니다. 평소 fd는 13~24개입니다(감사 실측).")
        if severity == "critical" and item.process_kind in {"grpc", "code-server"}:
            location = item.locations[0] if item.locations else ""
            detail += " " + restart_hint(location, {location: item.container})
        findings.append(Finding(
            rule="dagster_fd_usage", subject=item.container, severity=severity,
            title=(f"{item.container}: {item.process_kind} 프로세스(pid {item.pid}) 열린 fd "
                   f"{item.open_fds}/{item.nofile_soft_limit}({ratio:.0%})"),
            detail=detail,
            evidence={"open_fds": item.open_fds, "nofile_soft_limit": item.nofile_soft_limit,
                      "ratio": round(ratio, 3), "open_fds_total": item.open_fds_total,
                      "process_kind": item.process_kind, "pid": item.pid},
        ))
    return findings


# ---------------------------------------------------------------------------
# 경보 상태
# ---------------------------------------------------------------------------


@dataclass
class Alert:
    finding: Finding
    since: float
    last_seen: float
    resolved_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        item = self.finding
        return {
            "key": item.key, "rule": item.rule, "rule_label": RULE_LABELS.get(item.rule, item.rule),
            "subject": item.subject, "severity": item.severity, "title": item.title,
            "detail": item.detail, "evidence": dict(item.evidence),
            "since": _iso(self.since), "last_seen": _iso(self.last_seen),
            "resolved_at": _iso(self.resolved_at),
        }


@dataclass(frozen=True)
class Transition:
    event: str  # firing | resolved
    alert: Alert


class AlertBook:
    """firing·resolved 상태 기계. 같은 상태가 계속되면 다시 통지하지 않는다."""

    def __init__(self) -> None:
        self.active: dict[str, Alert] = {}
        self.resolved: list[Alert] = []
        self._pending: dict[str, int] = {}

    def apply(
        self, findings: Sequence[Finding], observed_rules: Iterable[str], now: float,
        for_evaluations: Mapping[str, int] | None = None,
    ) -> list[Transition]:
        observed = set(observed_rules)
        required = for_evaluations or {}
        transitions: list[Transition] = []
        current = {item.key: item for item in findings}
        for key, item in current.items():
            if key in self.active:
                alert = self.active[key]
                alert.finding = item
                alert.last_seen = now
                continue
            count = self._pending.get(key, 0) + 1
            if count < max(1, int(required.get(item.rule, 1))):
                self._pending[key] = count
                continue
            self._pending.pop(key, None)
            alert = Alert(finding=item, since=now, last_seen=now)
            self.active[key] = alert
            transitions.append(Transition("firing", alert))
        for key in [key for key in self._pending if key not in current]:
            if key.split(":", 1)[0] in observed:
                self._pending.pop(key)
        for key, alert in list(self.active.items()):
            if key in current or alert.finding.rule not in observed:
                continue  # 측정하지 못한 규칙의 경보는 그대로 둔다.
            alert.resolved_at = now
            del self.active[key]
            self.resolved.insert(0, alert)
            transitions.append(Transition("resolved", alert))
        del self.resolved[_RESOLVED_HISTORY:]
        return transitions


# ---------------------------------------------------------------------------
# 발송
# ---------------------------------------------------------------------------


class WebhookNotifier:
    """일반 webhook(JSON POST). 기본 비활성. URL은 env(비밀)에서만 읽고 화면·로그에 내지 않는다."""

    name = "webhook"

    def __init__(self, settings: Mapping[str, Any], *, host: str,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.enabled = bool(settings["enabled"])
        self._url_env = str(settings["url_env"])
        self._timeout = float(settings["timeout_seconds"])
        self._host = host
        self._transport = transport
        self.last_error: str | None = None
        self.last_sent_at: float | None = None

    def set_host(self, host: str) -> None:
        self._host = host

    def state(self) -> dict[str, Any]:
        configured = bool(os.environ.get(self._url_env, "").strip())
        return {"name": self.name, "enabled": self.enabled, "configured": configured,
                "url_env": self._url_env, "last_error": self.last_error,
                "last_sent_at": _iso(self.last_sent_at)}

    def send(self, transitions: Sequence[Transition]) -> None:
        if not self.enabled or not transitions:
            return
        url = os.environ.get(self._url_env, "").strip()
        if not url:
            self.last_error = f"{self._url_env}가 비어 있습니다"
            return
        payload = {
            "source": "kor-travel-docker-manager/plane-watchdog",
            "host": self._host,
            "events": [{"event": item.event, **item.alert.as_dict()} for item in transitions],
        }
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False, follow_redirects=False,
                              transport=self._transport) as client:
                response = client.post(url, json=payload)
            if response.status_code >= 300:
                self.last_error = f"HTTP {response.status_code}"
            else:
                self.last_error = None
                self.last_sent_at = time.time()
        except httpx.HTTPError as exc:
            # 예외 문자열에 URL(비밀)이 섞일 수 있어 종류만 남긴다.
            self.last_error = type(exc).__name__


# ---------------------------------------------------------------------------
# 측정 묶음(테스트가 바꿔 끼운다)
# ---------------------------------------------------------------------------


class Probes(Protocol):
    def plane_expected(self) -> bool: ...
    def instigators(self) -> list[Instigator]: ...
    def runs_since(self, since: float) -> list[RunRecord]: ...
    def schedule_runs(self, schedules: Sequence[tuple[str, str]]) -> dict[tuple[str, str], list[RunRecord]]: ...
    def tagged_job_runs(self, location: str, job: str, tag_key: str, limit: int) -> list[TaggedRun]: ...
    def disks(self, paths: Sequence[str]) -> tuple[list[DiskObservation], list[str]]: ...
    def io_pressure(self) -> dict[str, dict[str, float]] | None: ...
    def dagster_fds(self) -> tuple[list[ContainerFds], list[str]]: ...
    def capture(self, location: str, context: dict[str, Any]) -> dict[str, Any] | None: ...
    def capture_fds(self, container: str, context: dict[str, Any]) -> dict[str, Any] | None: ...
    def host(self) -> str: ...


class HostProbes:
    """운영 측정. storage는 읽기 전용 질의, 호스트는 /proc·statvfs·docker CLI 읽기뿐이다."""

    def __init__(self, config: WatchdogConfig) -> None:
        self._config = config
        self._storage = probe_module.StorageClient(
            statement_timeout_seconds=int(config.storage["statement_timeout_seconds"]))

    def host(self) -> str:
        try:
            from kor_travel_docker_manager.services.registry import current_host

            return current_host()
        except Exception:  # noqa: BLE001 - 설치 전 개발 체크아웃
            return os.uname().nodename if hasattr(os, "uname") else "local"

    def plane_expected(self) -> bool:
        """registry가 이 호스트에 공용 Dagster plane(`dagster` target)을 두는가. 모르면 True(보이게)."""

        try:
            from kor_travel_docker_manager.services.registry import current_host, target_hosts

            return current_host() in target_hosts("dagster")
        except Exception:  # noqa: BLE001
            return True

    def instigators(self) -> list[Instigator]:
        return self._storage.instigators()

    def runs_since(self, since: float) -> list[RunRecord]:
        return self._storage.runs_since(since)

    def schedule_runs(self, schedules: Sequence[tuple[str, str]]) -> dict[tuple[str, str], list[RunRecord]]:
        return self._storage.schedule_runs(schedules, limit=_SCHEDULE_BACKFILL_LIMIT)

    def tagged_job_runs(self, location: str, job: str, tag_key: str, limit: int) -> list[TaggedRun]:
        return self._storage.tagged_job_runs(location, job, tag_key, limit=limit)

    def disks(self, paths: Sequence[str]) -> tuple[list[DiskObservation], list[str]]:
        return probe_module.read_disks(list(paths) or probe_module.default_disk_paths())

    def io_pressure(self) -> dict[str, dict[str, float]] | None:
        return probe_module.read_io_pressure()

    def dagster_fds(self) -> tuple[list[ContainerFds], list[str]]:
        return probe_module.read_dagster_fds()

    def capture(self, location: str, context: dict[str, Any]) -> dict[str, Any] | None:
        from kor_travel_docker_manager.services.plane_watchdog_diagnostics import (
            capture_location_stall,
        )

        settings = self._config.diagnostics
        result = capture_location_stall(
            location, diagnostics_directory(settings), py_spy=str(settings["py_spy"]),
            log_tail_minutes=int(settings["log_tail_minutes"]), context=context,
        )
        return result.as_dict()

    def capture_fds(self, container: str, context: dict[str, Any]) -> dict[str, Any] | None:
        from kor_travel_docker_manager.services.plane_watchdog_diagnostics import capture_fd_usage

        return capture_fd_usage(container, diagnostics_directory(self._config.diagnostics), context=context).as_dict()


def diagnostics_directory(settings: Mapping[str, Any]) -> Path:
    configured = str(settings.get("directory") or "").strip()
    if configured:
        return Path(configured)
    from kor_travel_docker_manager.services.trusted_install import TRUSTED_STATE_ROOT

    return TRUSTED_STATE_ROOT / "plane-watchdog" / "diagnostics"


# ---------------------------------------------------------------------------
# 감시자
# ---------------------------------------------------------------------------


@dataclass
class CheckState:
    rule: str
    state: str  # ok | firing | unavailable | disabled | not_applicable
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "label": RULE_LABELS.get(self.rule, self.rule),
                "state": self.state, "detail": self.detail}


class PlaneWatchdog:
    def __init__(
        self,
        config: WatchdogConfig | None = None,
        *,
        config_error: str | None = None,
        probes: Probes | None = None,
        clock: Callable[[], float] = time.time,
        run_diagnostics_inline: bool = False,
    ) -> None:
        self.config = config
        self.config_error = config_error
        self._probes = probes if probes is not None else (HostProbes(config) if config else None)
        self._clock = clock
        self._inline_diagnostics = run_diagnostics_inline
        self._lock = threading.Lock()
        self._book = AlertBook()
        self._feed: dict[str, RunRecord] = {}
        self._cursor: float | None = None
        self._coverage_start: float | None = None
        self._backfill: dict[tuple[str, str], list[RunRecord]] = {}
        self._backfill_at: dict[tuple[str, str], float] = {}
        self._storage_failures = 0
        self._checks: dict[str, CheckState] = {}
        self._locations: list[LocationTickState] = []
        self._fds: list[ContainerFds] = []
        self._evaluated_at: float | None = None
        self._evaluation_error: str | None = None
        self._last_capture: dict[str, float] = {}
        self._captures: list[dict[str, Any]] = []
        self._host = ""
        self._plane_expected = True
        self._notifier = (WebhookNotifier(config.notifiers["webhook"], host="") if config else None)
        self._task: asyncio.Task | None = None

    # -- 루프 -------------------------------------------------------------

    def start(self) -> None:
        if self.config is None:
            logger.error("plane watchdog 설정 오류로 평가하지 않습니다: %s", self.config_error)
            return
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("plane watchdog started (interval %ss)", self.config.interval_seconds)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        assert self.config is not None
        while True:
            try:
                await asyncio.to_thread(self.evaluate_once)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 감시 루프는 죽지 않는다
                logger.exception("plane watchdog evaluation crashed")
            await asyncio.sleep(self.config.interval_seconds)

    # -- 평가 -------------------------------------------------------------

    def _retain_seconds(self) -> float:
        assert self.config is not None
        return max(_RETAIN_FEED_HOURS, float(self.config.storage["bootstrap_hours"])) * 3600

    def _feed_coverage_start(self, now: float) -> float:
        """feed가 빠짐없이 가진 구간의 시작 — 시작 시 bootstrap 범위와 보존 범위 중 늦은 쪽."""

        return max(self._coverage_start or now, now - self._retain_seconds())

    def _ingest(self, records: Sequence[RunRecord], now: float) -> None:
        for record in records:
            self._feed[record.run_id] = record
        if records:
            self._cursor = max(self._cursor or 0.0, max(record.updated for record in records))
        horizon = now - self._retain_seconds()
        for run_id in [run_id for run_id, record in self._feed.items() if record.updated < horizon]:
            del self._feed[run_id]

    def _storage_round(self, now: float, findings: list[Finding], checks: dict[str, CheckState]) -> set[str]:
        assert self.config is not None and self._probes is not None
        observed: set[str] = set()
        enabled = [rule for rule in _STORAGE_RULES if self.config.enabled(rule)]
        lookback = float(self.config.rule("location_tick_stall")["activity_lookback_hours"])
        try:
            instigators = self._probes.instigators()
            if self._coverage_start is None:
                self._coverage_start = now - float(self.config.storage["bootstrap_hours"]) * 3600
                since = self._coverage_start
            else:
                since = (self._cursor or now) - _FEED_OVERLAP_SECONDS
            self._ingest(self._probes.runs_since(since), now)
            if self.config.enabled("schedule_no_success"):
                wanted = [key for key in schedules_needing_backfill(
                    instigators, now, self._feed_coverage_start(now),
                    self.config.rule("schedule_no_success"), lookback,
                ) if now - self._backfill_at.get(key, float("-inf")) >= _SCHEDULE_BACKFILL_REFRESH_SECONDS]
                if wanted:
                    self._backfill.update(self._probes.schedule_runs(wanted))
                    self._backfill_at.update(dict.fromkeys(wanted, now))
        except ProbeError as exc:
            self._storage_failures += 1
            reason = str(exc)
            for rule in enabled:
                checks[rule] = CheckState(rule, "unavailable", reason)
            limit = int(self.config.storage["unreachable_after_failures"])
            observed.add("storage_unreachable")
            if self._storage_failures >= limit:
                findings.append(Finding(
                    rule="storage_unreachable", subject="dagster_shared", severity="critical",
                    title=f"Dagster storage에 {self._storage_failures}회 연속 닿지 못했습니다",
                    detail=f"{reason} 공용 PostgreSQL이 멈췄거나 과부하일 수 있습니다. 다른 수집 점검은 확인 불가입니다.",
                    evidence={"consecutive_failures": self._storage_failures},
                ))
                checks["storage_unreachable"] = CheckState("storage_unreachable", "firing", reason)
            else:
                checks["storage_unreachable"] = CheckState(
                    "storage_unreachable", "unavailable", f"일시 실패 {self._storage_failures}회: {reason}")
            return observed

        self._storage_failures = 0
        observed.add("storage_unreachable")
        checks["storage_unreachable"] = CheckState("storage_unreachable", "ok", "읽기 전용 질의 정상")
        feed = list(self._feed.values())

        stall_findings, self._locations = evaluate_location_stalls(
            instigators, now, self.config.rule("location_tick_stall"))
        schedule_findings = evaluate_schedule_no_success(
            instigators, feed, self._backfill, now, self._feed_coverage_start(now),
            self.config.rule("schedule_no_success"), lookback)
        # 같은 job을 두 번 울리지 않는다 — schedule 경보가 이미 그 job을 덮는다.
        covered_jobs = {(item.subject.split("/", 1)[0], job)
                        for item in schedule_findings for job in item.evidence.get("jobs", [])}
        containers = self._containers_by_location()
        produced = {
            "location_tick_stall": stall_findings,
            "schedule_no_success": schedule_findings,
            "tick_failures": evaluate_tick_failures(
                instigators, now, self.config.rule("tick_failures"), lookback, containers),
            "consecutive_failures": evaluate_consecutive_failures(
                feed, now, self.config.rule("consecutive_failures"), covered_jobs),
            "launch_failure_streak": evaluate_launch_failure_streak(
                feed, now, self.config.rule("launch_failure_streak"), containers),
            "launch_failure_rate": evaluate_launch_failures(feed, now, self.config.rule("launch_failure_rate")),
        }
        if self.config.enabled("analytics_refresh_lag"):
            produced["analytics_refresh_lag"] = self._analytics_round(now, checks)
        for rule in _STORAGE_RULES:
            if not self.config.enabled(rule):
                checks[rule] = CheckState(rule, "disabled")
                continue
            own = checks.get(rule)  # 규칙이 자기 점검 상태를 이미 적었다(사전집계 규칙).
            if own is not None and own.state == "unavailable":
                continue  # 측정 실패면 관측하지 않은 것으로 둔다 — 기존 경보는 그대로.
            observed.add(rule)  # "해당 없음"도 관측이다 — 이 plane에 그 job이 없으면 남은 경보는 풀린다.
            findings.extend(produced[rule])
            if own is None:
                checks[rule] = CheckState(rule, "firing" if produced[rule] else "ok",
                                          f"{len(produced[rule])}건" if produced[rule] else "")
        return observed

    def _analytics_round(self, now: float, checks: dict[str, CheckState]) -> list[Finding]:
        """transport 공항 수집 run의 사전집계 태그. 이 질의만 실패하면 이 규칙만 "확인 불가"다.

        판정할 run이 없으면(이 plane에 그 location이 없다 — n150) "해당 없음"이다. run은 있는데 `active_hours` 안에
        끝난 것이 없을 때도 같다 — job이 멈춘 것은 `location_tick_stall`·`schedule_no_success`가 본다.
        """

        assert self.config is not None and self._probes is not None
        rule = self.config.rule("analytics_refresh_lag")
        name = "analytics_refresh_lag"
        try:
            runs = self._probes.tagged_job_runs(
                str(rule["location"]), str(rule["job"]), str(rule["tag_key"]),
                int(rule["consecutive_runs"]) + _ANALYTICS_RUN_MARGIN)
        except ProbeError as exc:
            checks[name] = CheckState(name, "unavailable", str(exc))
            return []
        relevant = relevant_analytics_runs(runs, now, rule)
        if not relevant:
            checks[name] = CheckState(
                name, "not_applicable",
                f"최근 {_human_duration(float(rule['active_hours']) * 3600)} 안에 끝난 "
                f"{rule['location']}/{rule['job']} run이 없습니다")
            return []
        findings = evaluate_analytics_refresh(runs, now, rule)
        latest = relevant[0]
        state_text = f"최근 run {latest.tag or '정상(태그 없음)'} · {_iso(latest.record.updated)}"
        checks[name] = CheckState(name, "firing" if findings else "ok", state_text)
        return findings

    def _containers_by_location(self) -> dict[str, str]:
        return {location: item.container for item in self._fds for location in item.locations}

    def _host_round(self, findings: list[Finding], checks: dict[str, CheckState]) -> set[str]:
        assert self.config is not None and self._probes is not None
        observed: set[str] = set()
        if self.config.enabled("disk_usage"):
            observations, errors = self._probes.disks(self.config.rule("disk_usage")["paths"])
            if observations:
                produced = evaluate_disks(observations, self.config.rule("disk_usage"))
                findings.extend(produced)
                observed.add("disk_usage")
                summary = ", ".join(f"{item.path} {item.used_percent:.0f}%" for item in observations)
                checks["disk_usage"] = CheckState("disk_usage", "firing" if produced else "ok", summary)
            else:
                checks["disk_usage"] = CheckState("disk_usage", "unavailable", "; ".join(errors) or "경로 없음")
        else:
            checks["disk_usage"] = CheckState("disk_usage", "disabled")
        if self.config.enabled("io_pressure"):
            pressure = self._probes.io_pressure()
            if pressure is None:
                checks["io_pressure"] = CheckState(
                    "io_pressure", "unavailable", "/proc/pressure/io가 없습니다(커널 PSI 비활성)")
            else:
                produced = evaluate_io_pressure(pressure, self.config.rule("io_pressure"))
                findings.extend(produced)
                observed.add("io_pressure")
                value = pressure.get("full", {}).get("avg300")
                checks["io_pressure"] = CheckState(
                    "io_pressure", "firing" if produced else "ok",
                    f"full avg300 {value:.1f}%" if value is not None else "")
        else:
            checks["io_pressure"] = CheckState("io_pressure", "disabled")
        if self.config.enabled("dagster_fd_usage"):
            try:
                observations_fd, unreadable = self._probes.dagster_fds()
            except ProbeError as exc:
                observations_fd, unreadable = [], [str(exc)]
            self._fds = observations_fd
            if observations_fd:
                produced = evaluate_fds(observations_fd, self.config.rule("dagster_fd_usage"))
                findings.extend(produced)
                observed.add("dagster_fd_usage")
                worst = max(observations_fd, key=lambda item: item.ratio or 0)
                detail = f"최고 {worst.container} {worst.open_fds}/{worst.nofile_soft_limit}"
                if unreadable:
                    detail += f" · 읽지 못함 {len(unreadable)}"
                checks["dagster_fd_usage"] = CheckState("dagster_fd_usage", "firing" if produced else "ok", detail)
            else:
                checks["dagster_fd_usage"] = CheckState(
                    "dagster_fd_usage", "unavailable",
                    "Dagster 컨테이너 프로세스의 /proc을 읽지 못했습니다" + (f": {unreadable[0]}" if unreadable else ""))
        else:
            checks["dagster_fd_usage"] = CheckState("dagster_fd_usage", "disabled")
        return observed

    def evaluate_once(self) -> list[Transition]:
        if self.config is None or self._probes is None:
            return []
        now = self._clock()
        findings: list[Finding] = []
        checks: dict[str, CheckState] = {}
        observed: set[str] = set()
        error: str | None = None
        try:
            self._host = self._probes.host()
            if self._notifier is not None:
                self._notifier.set_host(self._host)
            self._plane_expected = self._probes.plane_expected()
            # 호스트 측정이 먼저다 — fd 관측의 location→컨테이너 대응을 storage 경보의 재시작 제안이 쓴다.
            observed |= self._host_round(findings, checks)
            if self._plane_expected:
                observed |= self._storage_round(now, findings, checks)
            else:
                for rule in ("storage_unreachable", *_STORAGE_RULES):
                    checks[rule] = CheckState(rule, "not_applicable", "이 호스트에는 공용 Dagster plane이 없습니다")
                    observed.add(rule)
        except Exception as exc:  # noqa: BLE001 - 한 번의 평가 실패가 루프를 죽이지 않는다
            logger.exception("plane watchdog evaluation failed")
            error = f"{type(exc).__name__}: {exc}"
        required = {"io_pressure": int(self.config.rule("io_pressure")["for_evaluations"])}
        with self._lock:
            transitions = self._book.apply(findings, observed, now, required)
            for rule, state in checks.items():
                if state.state == "firing" and not any(
                        alert.finding.rule == rule for alert in self._book.active.values()):
                    state.state = "ok"
                    state.detail = (state.detail + " · 연속 관측 대기").strip(" ·")
            self._checks = checks
            self._evaluated_at = now
            self._evaluation_error = error
        for item in transitions:
            alert = item.alert.finding
            if item.event == "firing":
                logger.warning("PLANE ALERT firing [%s] %s — %s", alert.severity, alert.title, alert.detail)
            else:
                logger.info("PLANE ALERT resolved [%s] %s", alert.severity, alert.title)
        if self._notifier is not None:
            try:
                self._notifier.send(transitions)
            except Exception:  # noqa: BLE001
                logger.exception("plane watchdog notifier failed")
        self._maybe_capture(transitions, now)
        return transitions

    def _maybe_capture(self, transitions: Sequence[Transition], now: float) -> None:
        """진단 수집 시점: location 정지가 처음 울릴 때, fd 경보가 (컨테이너, 심각도)마다 처음 울릴 때.

        fd 경보는 주의(50%)에서 위험(80%)으로 올라가도 같은 경보 key라 firing 전이가 다시 없다 — 그래서 활성
        경보를 보고 (컨테이너, 심각도)로 한 번씩 뜬다. 두 시점의 fd 내역을 비교해야 누수 경로가 보인다.
        """

        assert self.config is not None
        settings = self.config.diagnostics
        if not settings["enabled"]:
            return
        jobs: list[tuple[str, str, dict[str, Any]]] = []
        for item in transitions:
            finding = item.alert.finding
            if item.event == "firing" and finding.rule == "location_tick_stall":
                jobs.append(("stall", finding.subject, item.alert.as_dict()))
        with self._lock:
            fd_alerts = [alert for alert in self._book.active.values() if alert.finding.rule == "dagster_fd_usage"]
        for alert in fd_alerts:
            jobs.append((f"fd-{alert.finding.severity}", alert.finding.subject, alert.as_dict()))
        for kind, subject, alert_dict in jobs:
            key = f"{kind}:{subject}"
            previous = self._last_capture.get(key)
            if previous is not None and now - previous < float(settings["min_interval_minutes"]) * 60:
                continue
            if self._capture_directory_full(settings):
                logger.warning("plane watchdog diagnostics skipped for %s: capture limit reached", subject)
                continue
            self._last_capture[key] = now
            context = {"alert": alert_dict, "host": self._host}
            if self._inline_diagnostics:
                self._run_capture(kind, subject, context)
            else:
                threading.Thread(target=self._run_capture, args=(kind, subject, context),
                                 name=f"plane-watchdog-capture-{subject}", daemon=True).start()

    def _capture_directory_full(self, settings: Mapping[str, Any]) -> bool:
        if not isinstance(self._probes, HostProbes):
            return False
        from kor_travel_docker_manager.services.plane_watchdog_diagnostics import count_captures

        return count_captures(diagnostics_directory(settings)) >= int(settings["max_captures"])

    def _run_capture(self, kind: str, subject: str, context: dict[str, Any]) -> None:
        assert self._probes is not None
        try:
            if kind == "stall":
                result = self._probes.capture(subject, context)
            else:
                result = self._probes.capture_fds(subject, context)
        except Exception as exc:  # noqa: BLE001
            logger.exception("plane watchdog diagnostics failed for %s", subject)
            result = {"location": subject, "captured_at": _iso(self._clock()), "error": str(exc)}
        if result is not None:
            logger.warning("plane watchdog diagnostics captured for %s: %s", subject, result.get("directory"))
            with self._lock:
                self._captures.insert(0, result)
                del self._captures[20:]

    # -- 노출 -------------------------------------------------------------

    def status_payload(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            active = sorted(self._book.active.values(),
                            key=lambda alert: (alert.finding.severity != "critical", alert.since))
            alerts = [alert.as_dict() for alert in active]
            resolved = [alert.as_dict() for alert in self._book.resolved[:20]]
            checks = [self._checks[rule].as_dict() for rule in RULE_LABELS if rule in self._checks]
            evaluated_at = self._evaluated_at
            error = self._evaluation_error
            locations = [
                {"location": item.location, "last_tick_at": _iso(item.last_tick),
                 "silence_seconds": round(item.silence_seconds), "threshold_seconds": round(item.threshold_seconds),
                 "active_instigators": item.active_instigators}
                for item in self._locations
            ]
            fds = [
                {"container": item.container, "process_kind": item.process_kind, "pid": item.pid,
                 "locations": list(item.locations), "open_fds": item.open_fds,
                 "nofile_soft_limit": item.nofile_soft_limit,
                 "ratio": None if item.ratio is None else round(item.ratio, 3),
                 "open_fds_total": item.open_fds_total}
                for item in self._fds
            ]
            captures = list(self._captures)
        interval = self.config.interval_seconds if self.config else None
        age = None if evaluated_at is None else max(0.0, now - evaluated_at)
        stale = bool(interval and age is not None and age > 3 * interval)
        critical = sum(1 for alert in alerts if alert["severity"] == "critical")
        warning = len(alerts) - critical
        if self.config is None:
            state, text = "error", f"감시 설정 오류: {self.config_error}"
        elif evaluated_at is None:
            state, text = "unknown", "아직 첫 평가 전입니다."
        elif stale:
            state, text = "unknown", f"감시가 {_human_duration(age or 0)} 동안 평가하지 않았습니다."
        elif alerts:
            state, text = "firing", f"경보 {len(alerts)}건(심각 {critical}, 주의 {warning})"
        else:
            state, text = "ok", "경보가 없습니다."
        return {
            "schema": PLANE_ALERTS_SCHEMA,
            "host": self._host or None,
            "plane_expected": self._plane_expected,
            "enabled": self.config is not None,
            "config_error": self.config_error,
            "interval_seconds": interval,
            "evaluated_at": _iso(evaluated_at),
            "evaluation_age_seconds": None if age is None else round(age),
            "stale": stale,
            "evaluation_error": error,
            "summary": {"state": state, "text": text, "critical": critical, "warning": warning},
            "alerts": alerts,
            "recent_resolved": resolved,
            "checks": checks,
            "locations": locations,
            "dagster_fds": fds,
            "notifiers": [self._notifier.state()] if self._notifier else [],
            "diagnostics": captures,
        }

    def render_prometheus_metrics(self) -> str:
        from kor_travel_docker_manager.services.metrics_collector import _metric_block

        with self._lock:
            fds = list(self._fds)
            locations = list(self._locations)
            active = list(self._book.active.values())
            evaluated_at = self._evaluated_at
        lines: list[str] = []
        lines.extend(_metric_block(
            "ktdm_plane_watchdog_last_evaluation_timestamp_seconds",
            "Unix timestamp of the last plane watchdog evaluation.", "gauge",
            [({}, evaluated_at or 0)]))
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for alert in active:
            counts[(alert.finding.rule, alert.finding.severity)] += 1
        lines.extend(_metric_block(
            "ktdm_plane_alerts_firing", "Number of firing plane watchdog alerts.", "gauge",
            [({"rule": rule, "severity": severity}, value) for (rule, severity), value in sorted(counts.items())]
            or [({"rule": "none", "severity": "none"}, 0)]))
        lines.extend(_metric_block(
            "ktdm_dagster_container_open_fds", "Open file descriptors of the busiest process in a Dagster container.",
            "gauge", [({"container": item.container}, item.open_fds) for item in fds]))
        lines.extend(_metric_block(
            "ktdm_dagster_container_open_fds_total", "Open file descriptors summed over a Dagster container.",
            "gauge", [({"container": item.container}, item.open_fds_total) for item in fds]))
        lines.extend(_metric_block(
            "ktdm_dagster_container_nofile_soft_limit", "nofile soft limit of the busiest process in a Dagster container.",
            "gauge", [({"container": item.container}, item.nofile_soft_limit or 0) for item in fds]))
        lines.extend(_metric_block(
            "ktdm_dagster_location_tick_age_seconds", "Seconds since the newest schedule/sensor tick of a location.",
            "gauge", [({"location": item.location}, round(item.silence_seconds)) for item in locations]))
        return "\n".join(lines) + "\n"


def _build_default() -> PlaneWatchdog:
    try:
        return PlaneWatchdog(load_config())
    except ConfigError as exc:
        return PlaneWatchdog(None, config_error=str(exc))


plane_watchdog = _build_default()


__all__ = [
    "AlertBook",
    "ConfigError",
    "Finding",
    "PlaneWatchdog",
    "RULE_LABELS",
    "WatchdogConfig",
    "WebhookNotifier",
    "evaluate_analytics_refresh",
    "evaluate_consecutive_failures",
    "evaluate_disks",
    "evaluate_fds",
    "evaluate_io_pressure",
    "evaluate_launch_failure_streak",
    "evaluate_launch_failures",
    "evaluate_tick_failures",
    "evaluate_location_stalls",
    "evaluate_schedule_no_success",
    "load_config",
    "parse_config",
    "plane_watchdog",
    "schedules_needing_backfill",
]
