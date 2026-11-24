"""수집 경보의 Telegram 발송(소유자 결정 2026-11-24 — 채널은 Telegram 봇, 토큰은 PinVi 시스템 봇 재사용).

발송은 전이(event)가 아니라 **매 평가의 활성 경보와 "받는 사람이 아는 것"을 맞추는 방식**(reconcile)이다. 그래야 보내지 못한
경보가 사라지지 않고(다음 평가에 다시 보낸다), 억제 창이 끝났을 때 아직 울리는 경보를 놓치지 않는다.

- **위험(critical)**: 활성인데 받는 사람이 모르면 보낸다. 보낸 경보가 활성에서 빠지면(그 규칙이 이번 평가에서 관측됐을 때만 —
  측정 실패·Manager 재시작 직후에 거짓 해소를 보내지 않는다) 해소를 한 번 보낸다.
- **반복 억제**: 해소를 보낸 경보가 ``repeat_suppress_minutes`` 안에 다시 울리면 그 창이 끝날 때까지 기다린다. 창 안에 다시 풀리면
  아무것도 보내지 않고, 창이 끝났을 때 아직 울리면 보낸다.
- **주의(warning)**: 바로 보내지 않고 매일 KST ``digest_hour_kst`` 시 뒤 첫 평가에서 요약 한 통 — 지난 요약 뒤 울린 주의, 지금 울리는
  주의, 그사이 풀린 주의. 주의였다가 위험으로 오르면 위험 규칙으로 바로 보낸다("주의에서 위험으로 올랐습니다").
- **실패**: 보내지 못한 것은 남겨 두고 다음 평가에 다시 보낸다. 실패하면 간격을 늘려(60초부터 두 배, 최대 30분, 429면 Telegram의
  `retry_after`) 그동안 보내지 않는다. 한 평가에 ``_MAX_POSTS_PER_ROUND``통까지만 보낸다(평가 루프를 오래 붙잡지 않는다).
- 메시지: 호스트·규칙·대상(location)·제목(값)·대시보드 링크. 일반 텍스트(parse_mode 없음).

비밀: 토큰은 Manager root `.env`의 변수(기본 PinVi 시스템 봇 `PINVI_TELEGRAM_BOT_TOKEN_DEFAULT`)에서 **읽기만** 한다 — 파일·화면·
로그·상태 API에 내지 않는다. 토큰이 URL 경로에 들어가므로 예외 문구는 남기지 않고 종류만 남기며, httpx가 INFO로 남기는 요청
URL은 아래 filter가 가린다. chat id가 비어 있으면 보내지 않는다(소유자가 넣는다).

상태(받는 사람이 아는 위험 경보, 억제 창, 요약 대기열, 마지막 요약 날짜)는 Manager 상태 root의 JSON(0600)에 둔다.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BOT_PATH = re.compile(r"/bot[^/\s]+/")


class _RedactBotToken(logging.Filter):
    """httpx는 INFO로 요청 URL 전체를 남긴다(`HTTP Request: POST …/bot<토큰>/sendMessage`). 토큰을 가린다.

    logger filter는 하위 logger로 전파되지 않는다 — httpx는 `httpx` logger 하나로 남긴다(0.28.1 `_client.py`). httpcore의 trace는
    DEBUG이고 URL을 싣지 않는다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - 형식이 깨진 기록은 그대로 둔다
            return True
        if "/bot" in message:
            record.msg, record.args = _BOT_PATH.sub("/bot<redacted>/", message), ()
        return True


if not any(isinstance(item, _RedactBotToken) for item in logging.getLogger("httpx").filters):
    logging.getLogger("httpx").addFilter(_RedactBotToken())

_KST = _dt.timezone(_dt.timedelta(hours=9))
_DETAIL_LIMIT = 600
_DIGEST_ITEM_LIMIT = 20
_MESSAGE_LIMIT = 4000  # Telegram sendMessage 상한 4096자
_MAX_POSTS_PER_ROUND = 5
_BACKOFF_START = 60.0
_BACKOFF_MAX = 1800.0
_SEVERITY_TEXT = {"critical": "위험", "warning": "주의"}


def _kst(epoch: float | None) -> str:
    if epoch is None:
        return "알 수 없음"
    return _dt.datetime.fromtimestamp(epoch, _KST).strftime("%m-%d %H:%M KST")


def _parse_iso(text: Any) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(epoch, _dt.UTC).isoformat().replace("+00:00", "Z")


def dashboard_link(settings: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> str | None:
    """대시보드 수집 경보 패널 주소. 설정한 env가 비면 `KTDM_FRONTEND_ORIGINS`의 첫 https origin."""

    env = os.environ if environ is None else environ
    base = env.get(str(settings["dashboard_url_env"]), "").strip()
    if not base:
        origins = [item.strip() for item in env.get("KTDM_FRONTEND_ORIGINS", "").split(",")]
        base = next((item for item in origins if item.startswith("https://")), "")
    if not base:
        return None
    return base.split("#", 1)[0].rstrip("/") + "/#plane-alerts"


def format_alert(event: str, alert: Mapping[str, Any], host: str, link: str | None,
                 resolved_at: float | None = None) -> str:
    severity = _SEVERITY_TEXT.get(str(alert.get("severity")), str(alert.get("severity")))
    head = {"firing": f"[{severity}] 수집 경보", "escalated": f"[{severity}] 수집 경보 — 주의에서 위험으로 올랐습니다",
            "resolved": "[해소] 수집 경보"}.get(event, f"[{severity}] 수집 경보")
    detail = str(alert.get("detail") or "")
    if len(detail) > _DETAIL_LIMIT:
        detail = detail[:_DETAIL_LIMIT] + "…"
    lines = [
        f"{head} · {host or '?'}",
        f"규칙: {alert.get('rule_label') or alert.get('rule')}",
        f"대상: {alert.get('subject')}",
        str(alert.get("title") or ""),
    ]
    if event == "resolved":
        lines.append(f"{_kst(_parse_iso(alert.get('since')))}부터 · {_kst(resolved_at)} 해소 확인")
    else:
        lines.append(detail)
        lines.append(f"시작: {_kst(_parse_iso(alert.get('since')))}")
    if link:
        lines.append(f"대시보드: {link}")
    return "\n".join(line for line in lines if line)[:_MESSAGE_LIMIT]


def _bounded(lines: list[str], tail: list[str]) -> str:
    """본문을 상한 안으로 줄이되 꼬리(대시보드 링크)는 늘 남긴다."""

    footer = "\n".join(tail)
    budget = _MESSAGE_LIMIT - len(footer) - 40
    kept: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        if used + len(line) + 1 > budget:
            kept.append(f"… 외 {len(lines) - index}줄")
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join([*kept, *tail]) if tail else "\n".join(kept)


def format_digest(host: str, fired: Sequence[Mapping[str, Any]], active: Sequence[Mapping[str, Any]],
                  resolved: Sequence[Mapping[str, Any]], link: str | None) -> str:
    """주의 요약. ``fired``·``resolved``는 지난 요약 뒤 울린·풀린 주의, ``active``는 지금 울리는 주의."""

    lines = [f"[주의 요약] 수집 경보 · {host or '?'}",
             f"지난 요약 뒤 울린 주의 {len(fired)}건 · 지금 울리는 주의 {len(active)}건 · 풀린 주의 {len(resolved)}건"]

    def section(title: str, items: Sequence[Mapping[str, Any]], with_since: bool) -> None:
        if not items:
            return
        lines.extend(["", title])
        for item in items[:_DIGEST_ITEM_LIMIT]:
            since = f" ({_kst(_parse_iso(item.get('since')))}부터)" if with_since else ""
            lines.append(f"- {item.get('rule_label') or item.get('rule')} · {item.get('title')}{since}")
        if len(items) > _DIGEST_ITEM_LIMIT:
            lines.append(f"- … 외 {len(items) - _DIGEST_ITEM_LIMIT}건")

    section("지금 울리는 주의:", active, True)
    section("그사이 울렸다 풀린 주의:", resolved, False)
    return _bounded(lines, ["", f"대시보드: {link}"] if link else [])


class TelegramNotifier:
    """Telegram 봇 발송. 위험 즉시(재시도 포함)·주의 하루 1회 요약·반복 억제·해소 1회."""

    name = "telegram"

    def __init__(
        self,
        settings: Mapping[str, Any],
        *,
        host: str,
        state_path: Path | None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._settings = dict(settings)
        self._host = host
        self._state_path = state_path
        self._transport = transport
        self._clock = clock
        self._environ = environ
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self.last_error_at: float | None = None
        self.last_sent_at: float | None = None
        self.suppressed = 0
        #: 받는 사람이 "울리는 중"으로 아는 위험 경보: key → 마지막으로 보낸 경보 dict(해소 문구에 쓴다)
        self._known: dict[str, dict[str, Any]] = {}
        #: 해소를 보낸 key → 억제 창이 끝나는 시각
        self._quiet_until: dict[str, float] = {}
        self._digest_fired: dict[str, dict[str, Any]] = {}
        self._digest_resolved: dict[str, dict[str, Any]] = {}
        self._digest_seen: set[str] = set()  # 주의로 본 적 있는 활성 경보(요약 대기열에 이미 넣었다)
        self._holding: set[str] = set()  # 억제 창 때문에 기다리는 활성 위험
        self._last_digest_date: str | None = None
        self._retry_after = 0.0
        self._backoff = 0.0
        self._load()

    # -- 설정 -------------------------------------------------------------

    def _env(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    def _token(self) -> str:
        return self._env().get(str(self._settings["token_env"]), "").strip()

    def _chat_id(self) -> str:
        return self._env().get(str(self._settings["chat_id_env"]), "").strip()

    @property
    def configured(self) -> bool:
        return bool(self._token() and self._chat_id())

    @property
    def active(self) -> bool:
        return bool(self._settings["enabled"]) and self.configured

    def disabled_reason(self) -> str | None:
        if not self._settings["enabled"]:
            return "설정(notifiers.telegram.enabled)에서 꺼져 있습니다"
        missing = [str(self._settings[key]) for key, value in (("token_env", self._token()),
                                                               ("chat_id_env", self._chat_id())) if not value]
        if missing:
            return f"{' · '.join(missing)}이(가) 비어 있습니다"
        return None

    def set_host(self, host: str) -> None:
        self._host = host

    def state(self) -> dict[str, Any]:
        hour = int(self._settings["digest_hour_kst"])
        return {
            "name": self.name, "enabled": self.active, "configured": self.configured,
            # 비밀이 아닌 변수 **이름**만 싣는다.
            "url_env": f"{self._settings['token_env']} · {self._settings['chat_id_env']}",
            "disabled_reason": self.disabled_reason(),
            "last_error": self.last_error, "last_error_at": _iso(self.last_error_at),
            "last_sent_at": _iso(self.last_sent_at),
            "policy": f"위험 즉시 · 주의 매일 {hour:02d}시(KST) 요약 · 같은 경보 "
                      f"{int(self._settings['repeat_suppress_minutes'])}분 안 반복 억제",
            "suppressed": self.suppressed,
            "known_critical": len(self._known),
        }

    # -- 상태 파일 --------------------------------------------------------

    def _load(self) -> None:
        if self._state_path is None:
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for name in ("known", "digest_fired", "digest_resolved"):
            value = data.get(name)
            if isinstance(value, dict):
                setattr(self, f"_{name}", {str(k): dict(v) for k, v in value.items() if isinstance(v, dict)})
        quiet = data.get("quiet_until")
        if isinstance(quiet, dict):
            self._quiet_until = {str(k): float(v) for k, v in quiet.items()
                                 if isinstance(v, (int, float)) and not isinstance(v, bool)}
        seen = data.get("digest_seen")
        if isinstance(seen, list):
            self._digest_seen = {str(item) for item in seen}
        date = data.get("last_digest_date")
        self._last_digest_date = date if isinstance(date, str) else None

    def _save(self) -> None:
        if self._state_path is None:
            return
        payload = {"known": self._known, "quiet_until": self._quiet_until, "digest_fired": self._digest_fired,
                   "digest_resolved": self._digest_resolved, "digest_seen": sorted(self._digest_seen),
                   "last_digest_date": self._last_digest_date}
        try:
            self._state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self._state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._state_path)
        except OSError as exc:
            logger.warning("plane watchdog telegram state not saved: %s", exc.strerror or type(exc).__name__)

    # -- 발송 -------------------------------------------------------------

    def _fail(self, error: str, now: float, retry_after: float | None = None) -> None:
        self.last_error, self.last_error_at = error, now
        self._backoff = min(_BACKOFF_MAX, max(_BACKOFF_START, self._backoff * 2))
        self._retry_after = now + max(self._backoff, retry_after or 0.0)

    def _post(self, text: str) -> bool:
        now = self._clock()
        if now < self._retry_after:
            return False
        token, chat_id = self._token(), self._chat_id()
        base = str(self._settings["api_base"]).rstrip("/")
        try:
            with httpx.Client(timeout=float(self._settings["timeout_seconds"]), trust_env=False,
                              follow_redirects=False, transport=self._transport) as client:
                response = client.post(f"{base}/bot{token}/sendMessage",
                                       json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
        except Exception as exc:  # noqa: BLE001 - httpx.InvalidURL 등 HTTPError 밖 예외도 묶음을 깨지 않는다
            # 예외 문자열에 토큰이 든 URL이 섞일 수 있다 — 종류만 남긴다.
            self._fail(type(exc).__name__, now)
            return False
        if response.status_code >= 300:
            retry_after = None
            if response.status_code == 429:
                try:
                    retry_after = float(response.json().get("parameters", {}).get("retry_after"))
                except (ValueError, TypeError, AttributeError):
                    retry_after = None
            self._fail(f"HTTP {response.status_code}", now, retry_after)
            return False
        self.last_error = None
        self._backoff = 0.0
        self.last_sent_at = now
        return True

    def send(self, transitions: Sequence[Any]) -> None:
        """전이 인터페이스(webhook과 같은 자리). Telegram은 `reconcile`이 활성 경보로 맞추므로 여기서는 하지 않는다."""

    def reconcile(self, active_alerts: Sequence[Mapping[str, Any]], observed_rules: Iterable[str]) -> None:
        """이번 평가의 활성 경보에 맞춰 보낼 것을 보낸다. ``observed_rules``는 이번 평가에서 측정된 규칙."""

        if not self.active:
            return
        now = self._clock()
        observed = set(observed_rules)
        link = dashboard_link(self._settings, self._environ)
        suppress = float(self._settings["repeat_suppress_minutes"]) * 60
        budget = _MAX_POSTS_PER_ROUND
        with self._lock:
            active = {str(alert["key"]): dict(alert) for alert in active_alerts}
            # 1) 해소: 알린 위험이 활성에서 빠졌고 그 규칙이 이번에 관측됐다.
            for key, alert in list(self._known.items()):
                if key in active or str(alert.get("rule")) not in observed:
                    continue
                if budget <= 0:
                    break
                budget -= 1
                if self._post(format_alert("resolved", alert, self._host, link, resolved_at=now)):
                    del self._known[key]
                    self._quiet_until[key] = now + suppress
            # 2) 위험: 활성인데 받는 사람이 모른다.
            for key, alert in active.items():
                if alert.get("severity") != "critical":
                    continue
                if key in self._known:
                    self._known[key] = alert  # 최신 값(해소 문구용)
                    continue
                quiet = self._quiet_until.get(key)
                if quiet is not None and now < quiet:
                    if key not in self._holding:
                        self._holding.add(key)
                        self.suppressed += 1
                    continue
                if budget <= 0:
                    break
                budget -= 1
                event = "escalated" if key in self._digest_seen else "firing"
                if self._post(format_alert(event, alert, self._host, link)):
                    self._known[key] = alert
                    self._quiet_until.pop(key, None)
                    self._holding.discard(key)
            # 3) 주의: 요약 대기열.
            for key, alert in active.items():
                if alert.get("severity") == "warning" and key not in self._digest_seen:
                    self._digest_seen.add(key)
                    self._digest_fired[key] = alert
            for key in list(self._digest_seen):
                if key not in active:
                    self._digest_seen.discard(key)
                    if key in self._digest_fired and key not in self._known:
                        self._digest_resolved[key] = self._digest_fired[key]
            self._holding &= set(active)
            self._quiet_until = {key: until for key, until in self._quiet_until.items() if until > now}
            # 4) 하루 1회 요약.
            if budget > 0:
                self._maybe_digest(now, [alert for alert in active.values() if alert.get("severity") == "warning"], link)
            self._save()

    def _maybe_digest(self, now: float, warnings: list[dict[str, Any]], link: str | None) -> None:
        local = _dt.datetime.fromtimestamp(now, _KST)
        today = local.date().isoformat()
        if local.hour < int(self._settings["digest_hour_kst"]) or self._last_digest_date == today:
            return
        fired = list(self._digest_fired.values())
        resolved = list(self._digest_resolved.values())
        if not (warnings or fired or resolved):
            self._last_digest_date = today
            return
        if self._post(format_digest(self._host, fired, warnings, resolved, link)):
            self._digest_fired.clear()
            self._digest_resolved.clear()
            self._last_digest_date = today

    def notify_action(self, text: str) -> bool:
        """자동 조치 결과. 횟수는 조치 쪽 한도가 묶는다 — 억제하지 않는다."""

        if not self.active:
            return False
        with self._lock:
            return self._post(text[:_MESSAGE_LIMIT])


__all__ = ["TelegramNotifier", "dashboard_link", "format_alert", "format_digest"]
