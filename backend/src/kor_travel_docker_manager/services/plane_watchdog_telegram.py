"""수집 경보의 Telegram 발송(소유자 결정 2026-11-24 — 채널은 Telegram 봇, 토큰은 PinVi 시스템 봇 재사용).

발송 규칙:

- **위험(critical)** 경보는 firing·escalated(주의→위험) 때 바로 보낸다. 같은 경보 key를 ``repeat_suppress_minutes`` 안에 다시
  보내지 않는다(울렸다 풀렸다 하는 경보, Manager 재시작 직후 다시 울리는 경보). 바로 보낸 경보가 풀리면 해소를 한 번 보낸다.
- **주의(warning)** 경보는 바로 보내지 않고 하루 한 번(KST ``digest_hour_kst`` 시) 요약으로 묶는다 — 그 사이 울린 것, 아직 울리는
  것, 풀린 것.
- 메시지에 호스트·규칙·대상(location)·제목(값)·대시보드 링크를 싣는다. 일반 텍스트(parse_mode 없음)라 escape가 필요 없다.

비밀: 토큰은 Manager root `.env`의 변수(기본 PinVi 시스템 봇 `PINVI_TELEGRAM_BOT_TOKEN_DEFAULT`)에서 **읽기만** 한다 — 파일·화면·
로그·상태 API에 내지 않는다. 토큰이 URL 경로에 들어가므로 httpx 예외 문구도 남기지 않고 종류만 남긴다. chat id가 비어 있으면
발송하지 않는다(소유자가 넣는다).

상태(바로 보낸 key와 시각, 요약에 쌓인 주의 경보, 마지막 요약 날짜)는 Manager 상태 root의 작은 JSON 파일에 둔다 — Manager가
재시작해도 같은 위험 경보를 다시 보내지 않고 쌓인 요약을 잃지 않는다. 파일이 없거나 깨졌으면 빈 상태로 시작한다.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BOT_PATH = re.compile(r"/bot[^/\s]+/")


class _RedactBotToken(logging.Filter):
    """httpx는 INFO로 요청 URL 전체를 남긴다(`HTTP Request: POST …/bot<토큰>/sendMessage`). 토큰을 가린다."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - 형식이 깨진 기록은 그대로 둔다
            return True
        if "/bot" in message:
            record.msg, record.args = _BOT_PATH.sub("/bot<redacted>/", message), ()
        return True


for _name in ("httpx", "httpcore"):
    if not any(isinstance(item, _RedactBotToken) for item in logging.getLogger(_name).filters):
        logging.getLogger(_name).addFilter(_RedactBotToken())

_KST = _dt.timezone(_dt.timedelta(hours=9))
_DETAIL_LIMIT = 600
_DIGEST_ITEM_LIMIT = 30
_MESSAGE_LIMIT = 4000  # Telegram sendMessage 상한 4096자
_ANNOUNCED_TTL = 7 * 86400
_SEVERITY_TEXT = {"critical": "위험", "warning": "주의"}


def _kst(epoch: float | None) -> str:
    if epoch is None:
        return "알 수 없음"
    return _dt.datetime.fromtimestamp(epoch, _KST).strftime("%m-%d %H:%M KST")


def _parse_iso(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


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


def format_alert(event: str, alert: Mapping[str, Any], host: str, link: str | None) -> str:
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
        lines.append(f"{_kst(_parse_iso(alert.get('since')))}부터 · {_kst(_parse_iso(alert.get('resolved_at')))} 해소")
    else:
        lines.append(detail)
        lines.append(f"시작: {_kst(_parse_iso(alert.get('since')))}")
    if link:
        lines.append(f"대시보드: {link}")
    return "\n".join(line for line in lines if line)[:_MESSAGE_LIMIT]


def format_digest(host: str, fired: Sequence[Mapping[str, Any]], active: Sequence[Mapping[str, Any]],
                  resolved: Sequence[Mapping[str, Any]], link: str | None) -> str:
    lines = [f"[주의 요약] 수집 경보 · {host or '?'}",
             f"지난 요약 뒤 울린 주의 {len(fired)}건 · 지금 울리는 주의 {len(active)}건 · 풀린 주의 {len(resolved)}건"]
    if active:
        lines.append("")
        lines.append("지금 울리는 주의:")
        lines.extend(f"- {item.get('rule_label') or item.get('rule')} · {item.get('title')} "
                     f"({_kst(_parse_iso(item.get('since')))}부터)" for item in active[:_DIGEST_ITEM_LIMIT])
    gone = [item for item in fired if item.get("key") not in {other.get("key") for other in active}]
    if gone or resolved:
        lines.append("")
        lines.append("그사이 울렸다 풀린 주의:")
        seen: set[str] = set()
        for item in [*gone, *resolved][:_DIGEST_ITEM_LIMIT]:
            if item.get("key") in seen:
                continue
            seen.add(str(item.get("key")))
            lines.append(f"- {item.get('rule_label') or item.get('rule')} · {item.get('title')}")
    if link:
        lines.append("")
        lines.append(f"대시보드: {link}")
    return "\n".join(lines)[:_MESSAGE_LIMIT]


class TelegramNotifier:
    """Telegram 봇 발송. 위험 즉시·주의 하루 1회 요약·반복 억제·해소 1회."""

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
        self.last_sent_at: float | None = None
        self.suppressed = 0
        self._announced: dict[str, float] = {}  # 바로 보냈고 아직 울리는 위험 경보 key → 보낸 시각
        self._closed: dict[str, float] = {}  # 해소까지 알린 key → 처음 보낸 시각(억제 창이 지나면 지운다)
        self._digest_fired: dict[str, dict[str, Any]] = {}  # 지난 요약 뒤 울린 주의
        self._digest_resolved: dict[str, dict[str, Any]] = {}  # 지난 요약 뒤 풀린 주의
        self._last_digest_date: str | None = None
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

    def set_host(self, host: str) -> None:
        self._host = host

    def state(self) -> dict[str, Any]:
        hour = int(self._settings["digest_hour_kst"])
        return {
            "name": self.name, "enabled": self.active, "configured": self.configured,
            # 비밀이 아닌 변수 **이름**만 싣는다.
            "url_env": f"{self._settings['token_env']} · {self._settings['chat_id_env']}",
            "last_error": self.last_error, "last_sent_at": _iso(self.last_sent_at),
            "policy": f"위험 즉시 · 주의 매일 {hour:02d}시(KST) 요약 · 같은 경보 "
                      f"{int(self._settings['repeat_suppress_minutes'])}분 안 반복 억제",
            "suppressed": self.suppressed,
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
        for name in ("announced", "closed"):
            value = data.get(name)
            if isinstance(value, dict):
                setattr(self, f"_{name}", {str(k): float(v) for k, v in value.items()
                                           if isinstance(v, (int, float)) and not isinstance(v, bool)})
        for name in ("digest_fired", "digest_resolved"):
            value = data.get(name)
            if isinstance(value, dict):
                setattr(self, f"_{name}", {str(k): dict(v) for k, v in value.items() if isinstance(v, dict)})
        date = data.get("last_digest_date")
        self._last_digest_date = date if isinstance(date, str) else None

    def _save(self) -> None:
        if self._state_path is None:
            return
        payload = {"announced": self._announced, "closed": self._closed, "digest_fired": self._digest_fired,
                   "digest_resolved": self._digest_resolved, "last_digest_date": self._last_digest_date}
        try:
            self._state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self._state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._state_path)
        except OSError as exc:
            logger.warning("plane watchdog telegram state not saved: %s", exc.strerror or type(exc).__name__)

    # -- 발송 -------------------------------------------------------------

    def _post(self, text: str) -> bool:
        token, chat_id = self._token(), self._chat_id()
        base = str(self._settings["api_base"]).rstrip("/")
        try:
            with httpx.Client(timeout=float(self._settings["timeout_seconds"]), trust_env=False,
                              follow_redirects=False, transport=self._transport) as client:
                response = client.post(f"{base}/bot{token}/sendMessage",
                                       json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
        except httpx.HTTPError as exc:
            # 예외 문자열에 토큰이 든 URL이 섞인다 — 종류만 남긴다.
            self.last_error = type(exc).__name__
            return False
        if response.status_code >= 300:
            self.last_error = f"HTTP {response.status_code}"
            return False
        self.last_error = None
        self.last_sent_at = self._clock()
        return True

    def send(self, transitions: Sequence[Any]) -> None:
        """전이를 받는다(AlertBook `Transition`). 위험은 바로, 주의는 요약 대기열로."""

        if not self.active or not transitions:
            return
        now = self._clock()
        suppress = float(self._settings["repeat_suppress_minutes"]) * 60
        link = dashboard_link(self._settings, self._environ)
        with self._lock:
            for item in transitions:
                alert = item.alert.as_dict()
                key = str(alert["key"])
                if item.event in ("firing", "escalated"):
                    if alert["severity"] != "critical":
                        self._digest_fired.setdefault(key, alert)
                        continue
                    previous = self._announced.get(key, self._closed.get(key))
                    if previous is not None and now - previous < suppress:
                        # 같은 경보를 억제 창 안에 다시 보내지 않는다(울렸다 풀렸다, Manager 재시작 뒤 재발화).
                        # 억제한 재발화의 해소도 보내지 않는다 — 받는 사람이 마지막으로 안 것이 이미 "해소"다.
                        self.suppressed += 1
                        continue
                    if self._post(format_alert(item.event, alert, self._host, link)):
                        self._closed.pop(key, None)
                        self._announced[key] = now
                elif item.event == "resolved":
                    if key in self._announced:
                        # 위험을 바로 알린 경보는 해소도 한 번 바로 알린다.
                        self._post(format_alert("resolved", alert, self._host, link))
                        self._closed[key] = self._announced.pop(key)
                    elif key in self._digest_fired or alert["severity"] == "warning":
                        self._digest_resolved[key] = alert
            self._closed = {key: value for key, value in self._closed.items() if now - value < suppress}
            # Manager가 꺼진 사이 풀린 경보는 해소 전이가 없다 — 오래된 key는 버린다.
            self._announced = {key: value for key, value in self._announced.items() if now - value < _ANNOUNCED_TTL}
            self._save()

    def maybe_digest(self, active_alerts: Sequence[Mapping[str, Any]]) -> bool:
        """KST ``digest_hour_kst`` 시가 지났고 오늘 요약을 아직 안 보냈으면 주의 요약을 보낸다."""

        if not self.active:
            return False
        now = self._clock()
        local = _dt.datetime.fromtimestamp(now, _KST)
        today = local.date().isoformat()
        if local.hour < int(self._settings["digest_hour_kst"]) or self._last_digest_date == today:
            return False
        with self._lock:
            warnings = [alert for alert in active_alerts if alert.get("severity") == "warning"]
            fired = list(self._digest_fired.values())
            resolved = list(self._digest_resolved.values())
            if not (warnings or fired or resolved):
                self._last_digest_date = today
                self._save()
                return False
            sent = self._post(format_digest(self._host, fired, warnings, resolved,
                                            dashboard_link(self._settings, self._environ)))
            if sent:
                self._digest_fired.clear()
                self._digest_resolved.clear()
                self._last_digest_date = today
            self._save()
            return sent


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(epoch, _dt.UTC).isoformat().replace("+00:00", "Z")


__all__ = ["TelegramNotifier", "dashboard_link", "format_alert", "format_digest"]
